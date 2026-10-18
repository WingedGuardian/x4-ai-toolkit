r"""Mod registry: installed-folder ingest, ruamel round-trip I/O (preserve human:), dashboard.

The registry is the canonical store; `auto:` fields are owned by the tool and
refreshed, `human:` fields are owned by the user and NEVER overwritten on merge.

SOURCE OF TRUTH: the physically INSTALLED extension folders (game-root `extensions\`,
profile `extensions\` if present, Steam Workshop `content\392160\` if present) are
PRIMARY — that's what the game actually loads. The profile `content.xml` enabled-list
is a SECONDARY cross-check only ("did I forget to re-acquire something from my old
modlist?") — it is NOT used to determine what's active. A mod tracked from the old
content.xml list but not found on disk is marked `installed: false` and surfaced in
a separate dashboard section rather than counted as active.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

from lxml import etree
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from x4validate import _paths

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 4096  # don't line-wrap long notes

# Resolved through _paths, which accepts BOTH the installer's env names and the
# legacy ones this module used to read directly, and falls back to the installer's
# .claude/x4-paths.env. These stay module-level constants because tests (and the
# CLI's --dirs) monkeypatch them.
#
# UNRESOLVED IS None, NEVER A GUESS. These used to fall back to CWD-relative
# paths (`Path("content.xml")`, `Path("extensions")`, ...), which meant an
# unconfigured install silently ingested whatever content.xml the CWD happened
# to hold and wrote its registry into the CWD — "0 installed mods" as a
# statement about your modlist instead of about the missing setting. Callers
# pass a value through `require()` (exit 2, names the setting) before using it.
DEFAULT_REGISTRY = _paths.registry()
PROFILE_CONTENT = _paths.profile_content()
GAME_EXTENSIONS = _paths.game_extensions()
PROFILE_EXTENSIONS = _paths.profile_extensions()
WORKSHOP_CONTENT = _paths.workshop_content()


# --- Identity provenance -----------------------------------------------------
#
# A guess and a measurement must never occupy the same slot in the same grammar.
# Before this existed, `auto.nexus_id` held both, so a fuzzy name match sat next to
# `settled: stable` and read as fact — one real row pointed at an unrelated author's
# mod for weeks. Every stored id now carries HOW it was obtained, and anything
# DERIVED from a guessed id is capped so it cannot be promoted into a confident lane.

#: How the id in `auto.nexus_id` was arrived at. Order is roughly most→least trusted.
ID_STATES = (
    "pinned",      # a human set it — authoritative, never re-searched
    "exact",       # unambiguous: a ws_ id, or one clearly-dominant name match
    "guess",       # matched on weak evidence; `candidates` records the alternatives
    "ambiguous",   # several plausible hits, no winner -> NO id is stored
    "unmatched",   # searched, nothing plausible came back
    "off-nexus",   # a human says there is no Nexus page for this at all
    "unsearched",  # never attempted
)
#: States whose id may be trusted by anything downstream.
TRUSTED_ID_STATES = frozenset({"pinned", "exact"})
#: States that mean "do not spend an API call on this again".
TERMINAL_ID_STATES = frozenset({"pinned", "off-nexus"})
#: The lane a row is capped at while its identity is not trusted. A guess must
#: never reach `ready`/`settled: stable` — that is the whole point of the split.
UNCONFIRMED_LANE = "needs-confirmation"
#: Lanes that assert something CONFIDENT about the upstream mod, and therefore
#: may only be occupied by a row whose identity is trusted.
_CONFIDENT_LANES = frozenset({"ready", "churning", "predates-9.0", "drop", "custom-local"})


def identity(m) -> tuple[int | None, int | None, str]:
    """(nexus_id, nexus_file_id, id_state) for one entry, human pins winning.

    The single place that answers "who is this mod upstream?". `human:` is checked
    first and unconditionally: a human verdict is permanent, including the verdict
    "there is no page" (`human.nexus_id: none` / a `human.source`), which stops the
    resolver from re-guessing something it already got wrong.
    """
    a, h = m.get("auto") or {}, m.get("human") or {}
    hid, hfile = h.get("nexus_id"), h.get("nexus_file_id")
    if isinstance(hid, str) and hid.strip().lower() in ("none", "no", "off-nexus"):
        return None, None, "off-nexus"
    if hid not in (None, ""):
        try:
            return int(hid), (int(hfile) if hfile not in (None, "") else None), "pinned"
        except (TypeError, ValueError):
            # silent-ok: a malformed human pin (e.g. `nexus_id: "1330 maybe?"`) falls
            # through to the auto side, which yields a NON-trusted id_state — so the
            # row surfaces in `needs_review`/`verify` as unconfirmed rather than
            # vanishing. The miss is reported there, not swallowed here.
            pass
    if str(h.get("source") or "").strip():
        return None, None, "off-nexus"
    state = a.get("id_state") or "unsearched"
    nid = a.get("nexus_id")
    if state not in ID_STATES:
        state = "guess" if nid else "unsearched"   # unknown label is never promoted
    return (int(nid) if nid not in (None, "") else None,
            int(a["nexus_file_id"]) if a.get("nexus_file_id") not in (None, "") else None,
            state)


def cap_classification(state: str, classification: str, settled: str) -> tuple[str, str]:
    """Stop an unverified identity from producing a confident verdict.

    `classification`/`settled` are computed from upstream data that was fetched
    using the id — so they are only ever as good as the id. A row whose identity is
    a guess is held at `needs-confirmation` no matter how healthy the mod it fetched
    looks, because the reassuring part ("updated recently, stable") may describe
    somebody else's mod entirely.
    """
    if state in TRUSTED_ID_STATES:
        return classification, settled
    return UNCONFIRMED_LANE, f"unconfirmed identity ({state})"


#: Legacy `auto.resolve` values -> the id_state they honestly correspond to.
#: NOTHING is promoted here: every historical auto-match becomes `guess`, because
#: that is what it always was — the old label just did not say so.
_LEGACY_RESOLVE = {
    "manual": "pinned",
    "auto (spot-check)": "guess",
    "auto (skipped implausible top hit; spot-check)": "guess",
    "unmatched (needs review)": "unmatched",
}


def migrate_entry(m) -> bool:
    """Bring one entry up to the identity-provenance schema. True if it changed.

    Idempotent, and deliberately pessimistic: an id whose provenance cannot be
    reconstructed becomes `guess`, never `exact`. Downgrading a real match costs
    one spot-check; upgrading a bad one re-creates the defect this schema exists
    to remove.
    """
    changed = False
    a = m.setdefault("auto", CommentedMap())
    h = m.setdefault("human", CommentedMap())
    for key, default in (("nexus_id", None), ("nexus_file_id", None),
                         ("source", ""), ("verified_on", None)):
        if key not in h:
            h[key] = default
            changed = True
    if "nexus_file_id" not in a:
        a["nexus_file_id"] = None
        changed = True
    if "upstream_from" not in a:
        a["upstream_from"] = None
        changed = True
    if "id_state" not in a:
        legacy = str(a.get("resolve") or "")
        if legacy in _LEGACY_RESOLVE:
            a["id_state"] = _LEGACY_RESOLVE[legacy]
        elif legacy.startswith("auto"):
            a["id_state"] = "guess"
        else:
            a["id_state"] = "guess" if a.get("nexus_id") else "unsearched"
        changed = True
        # A lane computed BEFORE the cap existed is a confident verdict resting on
        # an identity nobody confirmed — the exact falsehood this schema removes,
        # so it does not get grandfathered. Measured on the first real migration:
        # 62 of 101 active rows were sitting in `ready`/`churning`/`predates-9.0`
        # on the strength of a fuzzy name match. The upstream data is kept; only
        # the verdict drawn from it is withdrawn, and one refresh restores any
        # lane whose identity is confirmed.
        _, _, state = identity(m)
        if state not in TRUSTED_ID_STATES and a.get("classification") in _CONFIDENT_LANES:
            a["classification"], a["settled"] = cap_classification(
                state, a.get("classification"), a.get("settled") or "")
    return changed


def migrate(reg: CommentedMap) -> int:
    """Migrate every entry; returns how many changed. Called on load."""
    return sum(1 for m in reg.get("mods") or [] if migrate_entry(m))


def require(value: Path | None, what: str, fix: str) -> Path:
    """Named-loss gate for an unresolved location: exit 2 rather than guess.

    A guessed CWD-relative path does not error — it looks in the wrong place and
    reports finding nothing, which reads as a fact about the user's mods."""
    if value is None:
        print(f"error: {what} is not configured — {fix}", file=sys.stderr)
        print("       (run `x4validate --paths` to see what resolved; "
              "config file: <toolkit>/x4-paths.env, 3.x: <toolkit>/.claude/x4-paths.env)",
              file=sys.stderr)
        raise SystemExit(2)
    return Path(value)


def default_installed_dirs() -> list[Path]:
    """The configured roots a mod can be installed into.

    A root that is configured but does not exist is NOT dropped here —
    `scan_installed` reports what it actually found, and silently shrinking this
    list is how "0 installed mods" starts reading as "you have no mods" instead
    of "I looked in the wrong place". A root that is NOT CONFIGURED is a
    different thing: there is nowhere to look, and the CLI names that loss
    (empty list) instead of scanning a guessed CWD-relative folder.
    """
    return [d for d in (GAME_EXTENSIONS, PROFILE_EXTENSIONS, WORKSHOP_CONTENT)
            if d is not None]

# Seed: content-id -> Nexus mod_id already resolved from the batch triage.
SEED_NEXUS_IDS = {
    "kuerteeNPCReactions": 497, "kuerteeEmergentMissions": 780,
    "kuerteeMoreGenericMissions": 622, "kuerteeSurfaceElementTargeting": 710,
    "DeadAirJobs": 1084, "station_combat_rebalance_vro": 1331,
    "lc4hunter_xenon_overhaul": 1132, "tuning_overhaul": 1316,
    "Synthetium_Music": 601, "jupiter_x4_own_radio_stations": 544,
    "ws_2042901274": None,  # SirNukes — Steam-only id; Nexus id TBD
    "ws_1696862840": 305,   # VRO
    "warpscrambler": 1042,
    "kuerteeAlternativesToDeath": 551,  # ATD — currently HIDDEN on Nexus (KB); search can't find it
}


def ingest_content_xml(path: Path | None = None) -> list[tuple[str, bool]]:
    """(id, enabled) for every <extension> in the profile content.xml.

    SECONDARY source (cross-check only) — see module docstring."""
    path = path or PROFILE_CONTENT
    if path is None:
        raise FileNotFoundError(
            "profile content.xml is not configured (set X4_PROFILE or "
            "X4_PROFILE_CONTENT, or pass --content)")
    root = etree.parse(str(path)).getroot()
    out = []
    for ext in root.xpath("//extension[@id]"):
        out.append((ext.get("id"), profile_flag(ext.get("enabled"))))
    return out


def profile_flag(value: str | None) -> bool:
    """One profile `<extension enabled=…>` value -> enabled?

    MEASURED 2026-09-26 (load-order probe, case b): an entry with NO `enabled`
    attribute LOADS -- it used to be read as disabled here and in `_freshness`.
    "false"/"0" (any case, trimmed) disable; absent or anything else enables, the
    same convention `scan_installed` applies to a manifest. The ONE parser for
    this value: `_freshness._profile_decisions` calls it too.
    """
    return value is None or str(value).strip().lower() not in ("false", "0")


#: The two questions people ask about "the mod list", which are NOT the same
#: question. Nothing in the code used to say which one a caller wanted, so it was
#: decided by whichever helper the author happened to import.
#:
#:   "active"    — what the ENGINE WILL LOAD: installed; enabled by the profile
#:                 content.xml, or -- when the profile has no entry for it -- by
#:                 its own manifest; and every REQUIRED dependency provided by a
#:                 mod or DLC that itself loads (see `_active_filter`).
#:   "installed" — what is ON DISK, enabled or not. The right answer when you are
#:                 inventorying, or evaluating a mod before switching it on.
#:
#: MEASURED 2026-08-22 across 13 call sites: 5 correct, 3 defensible but silent,
#: and **4 wrong**, all wrong the same way — modelling the running game from the
#: disk. Concretely, with exactly ONE mod installed-but-disabled (`escape_pod`,
#: 19 files): `x4eff` carried its 3 macros as live; `x4compat` listed it as a
#: participant in 4 collision rows; and Tier B — the mode whose entire job is
#: proving a cross-mod selector resolves — would resolve a selector against it
#: and report OK. That last one is a FALSE PASS in the tool built to catch
#: silent no-ops.
#:
#: The cost was 1 mod only because you happen to have one disabled. It scales
#: with the disabled set, and nothing warned.
MOD_SCOPES = ("active", "installed")


class ModList(list):
    """What :func:`mods` returns: the mod dicts, PLUS every mod the read left out.

    ``.dropped`` holds one record per exclusion, ``"<folder>: <why>"`` -- a manifest
    that will not parse, or (scope "active") a mod the ENGINE will not load because a
    REQUIRED dependency is missing, disabled or cyclic. It rides on the result so a
    caller cannot receive the narrowed set without its exclusions (BLIND-SPOTS F139:
    when they travelled only through an opt-in ``dropped=`` list, 16 of 20
    active-scope callers never asked). Render it with :func:`dropped_note`, or
    :func:`left_out` where a caller must classify per folder.

    ``.notes`` holds what is NOT an exclusion but still makes the modelled set or
    order an assumption: an UNMEASURED root (the Steam Workshop root, or an
    unconfigured folder beside another root), a dependency id matched only ignoring
    case, and -- appended by `_loadorder.compute_load_order` when it orders this list
    -- a duplicate id or folder, a dependency cycle, an unmeasured cross-root pass.
    Kept apart from ``.dropped`` because :func:`left_out` reads every ``.dropped``
    record as "this folder was left out"; :func:`model_note` renders them.

    A plain ``list`` subclass, so every existing consumer (iteration, ``len``,
    comprehension, JSON) is unchanged; a slice or comprehension is a plain list and
    drops the attribute, which is why callers read it off the value `mods()` returned.
    """

    def __init__(self, items=(), dropped=(), notes=()):
        super().__init__(items)
        self.dropped: list[str] = list(dropped)
        self.notes: list[str] = list(notes)


def left_out(mod_list) -> dict[str, str]:
    """``{folder: why}`` for every mod *mod_list* (a :func:`mods` result) left out.

    Works on any list: one without ``.dropped`` (a test stub, a filtered copy) has
    left nothing out that it can say, so the answer is ``{}``. A Windows folder name
    cannot contain ``:``, so the first ``": "`` always ends the folder.
    """
    out: dict[str, str] = {}
    for rec in getattr(mod_list, "dropped", ()) or ():
        folder, _, why = rec.partition(": ")
        out[folder] = why
    return out


def dropped_note(mod_list) -> str | None:
    """The ONE-LINE disclosure of what *mod_list* left out, or None if nothing.

    Exclusions ONLY. Every caller prefixes this line with its own "NOT in <tree>
    (the engine does not load it)", so a record that excludes nothing -- an
    unmeasured root, a case-only dependency match, a load-order record -- goes
    through :func:`model_note` instead, which would make none of those lines false.

    Pure -- this module is an engine source and carries no CLI output
    (`tests/test_engine_sources_carry_no_cli.py`); every caller routes the line to
    the sink it already has (stderr, a gate note, a Report skip).
    """
    recs = list(getattr(mod_list, "dropped", ()) or ())
    if not recs:
        return None
    return (f"{len(recs)} installed mod(s) left out of the mod set this run models "
            f"-- {'; '.join(recs)}")


def model_note(mod_list) -> str | None:
    """The ONE-LINE disclosure of *mod_list*'s ``.notes``, or None if there are none:
    every assumption the modelled set or its load order rests on that was NOT
    measured, and every record `_loadorder.compute_load_order` made while ordering
    it (duplicate id or folder, cycle, unmeasured cross-root pass).

    A caller that orders the list renders this AFTER ordering it, or the order's own
    records are not in it yet. Pure, as :func:`dropped_note`.
    """
    notes = list(getattr(mod_list, "notes", ()) or ())
    if not notes:
        return None
    return (f"{len(notes)} note(s) on the modelled mod set and its load order "
            f"-- {'; '.join(notes)}")


def mods(scope: str, dirs: list[Path] | None = None,
         dropped: list[str] | None = None, *, dlc_config=None) -> ModList:
    """The mod set, for an EXPLICITLY NAMED *scope* — see :data:`MOD_SCOPES`.

    *scope* is positional and required on purpose. A default would just recreate
    the bug: the whole defect was that callers never had to say which world they
    meant, so they silently got whichever one the helper happened to implement.

    Prefer this over calling :func:`scan_installed` directly; that is the raw
    disk reader and `tests/test_mod_scope_is_explicit.py` enforces the boundary.

    Returns a :class:`ModList`: the mods, plus ``.dropped`` -- every mod this read
    left out and why. A caller that renders anything must disclose it
    (:func:`dropped_note`); *dropped*, if passed, receives the same records.

    ⚠ NEITHER scope includes the DLC. `ego_dlc_*` is skipped as base-game
    content, not a mod to triage (see :func:`scan_installed`), and enumerating it
    here would DOUBLE-COUNT every DLC against the unpacked reference tree, which is the
    base+DLC (CLAUDE.md #20). MEASURED 2026-08-27: 123 installed, 0 of them a DLC.
    The caveat is documented on `scan_installed` and was missing here, on the
    function this docstring tells everyone to call instead -- so a dependency
    check asked `mods("installed")` whether the Terran DLC was present and was
    told NO. For that question use `_merge.Config().dlc_dirs()` (8 here) or
    `.packed_dlc_names()` (the two mini-DLC, which are never unpacked).

    For "active", the DLC that count as dependency PROVIDERS come from
    *dlc_config*`.dlc_dirs()` (default: `_merge.Config()`, i.e. the configured
    reference plus the packed-only DLC of the live install), UNIONED with any
    `ego_dlc_*` folder in the scanned *dirs*. They used to come from the scanned
    dirs alone, so `--ext-dir <a folder that is not the game's extensions>` made
    every mod requiring a DLC silently NOT LOADED (AUDIT-2026-09-24 final review).
    With no configured reference the scanned dirs are the only source (fail open to
    what was there before, never to "no DLC at all" being claimed).
    """
    if scope not in MOD_SCOPES:
        raise ValueError(
            f"mod scope must be one of {MOD_SCOPES}, got {scope!r}. "
            f"'active' = what the engine will load; 'installed' = what is on disk.")
    # EVERY EXCLUSION TRAVELS WITH THE RESULT (BLIND-SPOTS F139, closed 2026-09-26).
    # A mod whose content.xml will not parse, and -- scope "active" -- a mod the ENGINE
    # will not load (a REQUIRED dependency missing, disabled or cyclic; see
    # `_active_filter`), are left out. Leaving them out is right; saying nothing is not.
    # They used to be recorded only into an opt-in `dropped=` list, and MEASURED
    # 2026-09-26 16 of the 20 active-scope call sites never passed one. So they are
    # now ALWAYS collected and returned as `ModList.dropped`; `dropped=` still receives
    # the same records for the callers that pass it.
    #
    # Nothing is PRINTED here, deliberately: this module is in
    # _freshness.ENGINE_SOURCES and tests/test_engine_sources_carry_no_cli.py forbids
    # CLI output in it. `dropped_note` is the pure one-line formatter; each caller
    # routes it to its own sink, and tests/test_active_exclusions_disclosed.py bans an
    # active-scope call in any function that never reaches the channel.
    sink: list[str] = []
    dlc: list[dict] = []
    installed = scan_installed(dirs, dropped=sink, dlc=dlc)
    if scope == "installed":
        if dropped is not None:
            dropped.extend(sink)
        return ModList(installed, sink, _root_notes(installed))
    try:
        prof = dict(ingest_content_xml())
    except (OSError, etree.XMLSyntaxError):
        # silent-ok: FAIL OPEN, and deliberately. No readable profile means we
        # cannot know what is switched off; treating every mod as enabled matches
        # the registry's documented "absent = enabled" convention and keeps this
        # from quietly EMPTYING the world model on a machine with no profile.
        prof = {}
    seen = {d["id"].lower() for d in dlc}
    for sub in _reference_dlc_dirs(dlc_config):
        entry = _dlc_entry(sub)
        if entry["id"].lower() not in seen:
            seen.add(entry["id"].lower())
            dlc.append(entry)
    notes: list[str] = []
    active = _active_filter(installed, prof, dlc, sink, notes)
    if dropped is not None:
        dropped.extend(sink)
    return ModList(active, sink, _root_notes(active) + notes)


def _root_notes(mod_set: list[dict]) -> list[str]:
    """Disclose every root in *mod_set* whose behaviour the model does not measure.

    MEASURED (F141): the game root, then the profile root, and no dependency between
    them. NOT measured: anything about the Steam Workshop root, and -- for a folder
    that is not a configured root (a custom `dirs` list) -- which root it stands for.
    Both are modelled as before F141 (walked after the roots listed ahead of them,
    dependencies resolving across them); that is a guess and is said to be one."""
    out = []
    ws = sorted(m["folder"] for m in mod_set if m.get("root_kind") == "workshop")
    if ws:
        out.append(
            f"Steam Workshop root: {len(ws)} mod(s) ({', '.join(ws[:5])}"
            f"{', ...' if len(ws) > 5 else ''}); where the engine places them relative "
            "to game-root and profile-root mods, and whether a dependency resolves "
            "between them, are UNMEASURED -- modelled as loading after both, with "
            "dependencies resolving across the Workshop root")
    ranks = {m.get("root_rank", 0) for m in mod_set}
    custom = sorted({str(Path(m["path"]).parent)
                     for m in mod_set if m.get("root_kind") == "custom"})
    if custom and len(ranks) > 1:
        out.append(
            f"{len(custom)} scanned folder(s) are not a configured extensions root "
            f"({'; '.join(custom[:3])}), so which root each stands for is unknown; "
            "placement and dependencies between them and the other scanned roots are "
            "UNMEASURED -- modelled as walked in the order given, with dependencies "
            "resolving across them")
    return out


def _reference_dlc_dirs(config=None) -> list[Path]:
    """Every DLC root the configured game has -- unpacked in reference/ and packed
    in the install -- via `_merge.Config().dlc_dirs()`, the one DLC enumeration.

    An UNCONFIGURED reference yields [] rather than raising: `mods()` is called by
    inventory tools that must work without a reference, and the scanned dirs are
    still consulted for `ego_dlc_*` folders, so this narrows back to the old
    behaviour instead of emptying the provider set.
    """
    from x4validate import _merge
    try:
        cfg = config if config is not None else _merge.Config()
        return list(cfg.dlc_dirs())
    except (_paths.Unconfigured, OSError):
        # silent-ok: fail open to the scanned-dirs providers (see docstring); the
        # reference being unconfigured is reported by every tool that NEEDS it.
        return []


def _active_filter(installed: list[dict], prof: dict[str, bool],
                   dlc: list[dict], dropped: list[str] | None,
                   notes: list[str] | None = None) -> list[dict]:
    """The engine's load decision, MEASURED 2026-09-26 by the in-game load-order probe
    (`scripts/load-order-probe.py`, 23 probe mods; AUDIT-2026-09-24 LO-3/RG-6):

      1. ENABLED: the profile entry decides when there is one (an entry with no
         `enabled` attribute counts as enabled); otherwise the manifest's own
         `enabled` is the default. So manifest enabled="0" + profile "true" LOADS.
      2. REQUIRED dependencies (`optional` not true): every one must name an id
         provided by a mod or an installed DLC that ITSELF loads. Missing, disabled,
         or excluded-by-this-rule all exclude the mod -- applied to a fixpoint, so a
         dependency CYCLE loads neither side. Optional dependencies never exclude.
         A `<dependency>` naming only a version is a game requirement, ignored here.
         A dependency does NOT resolve between the GAME root and the PROFILE root:
         MEASURED 2026-09-26, probe rounds 2-3 -- refused in BOTH directions
         (BLIND-SPOTS F141). Nothing about the Steam Workshop root (or a scanned folder
         that is not a configured root) was measured, so a dependency to or from one
         RESOLVES, as it did before F141, and `_root_notes` says the model is a guess
         there. An installed DLC counts for every root (UNMEASURED for a non-game
         root: no probe put a DLC-dependent mod in the profile root).
         A dependency id that matches only IGNORING CASE is SATISFIED and recorded in
         *notes* (whether the engine compares ids case-sensitively is UNMEASURED, and
         excluding a mod over a spelling would assert a behaviour nobody observed).
      3. Two folders sharing a manifest id BOTH load (the clash is recorded by
         `_loadorder.compute_load_order`, not here).

    Every mod excluded by rule 2 is appended to *dropped* with its reason. The
    exclusion is the engine's behaviour, but a mod leaving the world model with
    nothing said is the silent-narrowing shape this module exists to prevent.
    """
    from x4validate._loadorder import (case_only_note, crosses_isolated_roots,
                                       mod_dependencies, root_kind)

    enabled = [m for m in installed if prof.get(m["id"], m["enabled"])]

    def _deps(m: dict) -> list[str]:
        path = m.get("path")
        if not path:
            return []
        _id, deps = mod_dependencies(Path(path))   # parse failures: scan_installed's
        return [d for d, optional in deps           # channel already reported them
                if not optional and d != m["id"]]

    required = {id(m): _deps(m) for m in enabled}
    if not any(required.values()):
        return enabled

    dlc_ids = {d["id"] for d in dlc if prof.get(d["id"], d["enabled"])}
    # Every provider that has LOADED so far: exact id -> the root kinds providing it.
    # A DLC provides for every root (kind None).
    provided: dict[str, set] = {d: {None} for d in dlc_ids}
    by_lower: dict[str, set[str]] = {}
    for d in dlc_ids:
        by_lower.setdefault(d.lower(), set()).add(d)

    def _reaches(ident: str, kind: str) -> bool:
        return any(k is None or not crosses_isolated_roots(k, kind)
                   for k in provided.get(ident, ()))

    def _match(dep: str, kind: str) -> str | None:
        """The loaded id that satisfies *dep* for a mod in a *kind* root, or None.
        The exact spelling first; a case-only match second (see rule 2)."""
        if _reaches(dep, kind):
            return dep
        for actual in sorted(by_lower.get(dep.lower(), ())):
            if actual != dep and _reaches(actual, kind):
                return actual
        return None

    loaded: list[dict] = []
    pending = list(enabled)
    progress = True
    while pending and progress:
        progress = False
        rest = []
        for m in pending:
            kind = root_kind(m)
            hits = [(d, _match(d, kind)) for d in required[id(m)]]
            if all(actual is not None for _d, actual in hits):
                loaded.append(m)
                provided.setdefault(m["id"], set()).add(kind)
                by_lower.setdefault(m["id"].lower(), set()).add(m["id"])
                for d, actual in hits:
                    msg = case_only_note(m["folder"], d, actual)
                    if actual != d and notes is not None and msg not in notes:
                        notes.append(msg)
                progress = True
            else:
                rest.append(m)
        pending = rest
    if not pending:
        return enabled          # nothing excluded; keep the caller's order

    if dropped is not None:
        # Compared IGNORING CASE throughout, to agree with `_match` above.
        installed_ids = ({m["id"].lower() for m in installed}
                         | {d["id"].lower() for d in dlc})
        stuck_ids = {m["id"].lower() for m in pending}
        stuck_by_id: dict[str, list[dict]] = {}
        for m in pending:
            stuck_by_id.setdefault(m["id"].lower(), []).append(m)

        def _in_cycle(start: dict) -> bool:
            seen: set[str] = set()
            todo = [d.lower() for d in required[id(start)] if d.lower() in stuck_ids]
            while todo:
                nxt = todo.pop()
                if nxt == start["id"].lower():
                    return True
                if nxt in seen:
                    continue
                seen.add(nxt)
                for other in stuck_by_id.get(nxt, []):
                    todo.extend(d.lower() for d in required[id(other)]
                                if d.lower() in stuck_ids)
            return False

        for m in pending:
            unmet = [d for d in required[id(m)] if _match(d, root_kind(m)) is None]
            why = []
            for d in unmet:
                if by_lower.get(d.lower()):
                    why.append(f"required dependency {d!r} is in a DIFFERENT extensions "
                               "root -- the engine does not resolve a dependency between "
                               "the game root and the profile root")
                elif d.lower() not in installed_ids:
                    why.append(f"required dependency {d!r} is not installed")
                elif d.lower() not in stuck_ids:
                    why.append(f"required dependency {d!r} is installed but disabled")
                else:
                    why.append(f"required dependency {d!r} does not load itself")
            cyc = (" -- part of a dependency cycle, so neither side loads"
                   if _in_cycle(m) else "")
            dropped.append(f"{m['folder']}: NOT LOADED by the engine -- "
                           f"{'; '.join(why)}{cyc}")
    keep = {id(m) for m in loaded}
    return [m for m in enabled if id(m) in keep]


def _dlc_entry(sub: Path) -> dict:
    """{id, folder, enabled} for one `ego_dlc_*` folder (see `scan_installed`)."""
    cxml = sub / "content.xml"
    try:
        root = etree.parse(str(cxml)).getroot()
    except (OSError, etree.XMLSyntaxError):
        # silent-ok: a DLC folder with an unreadable manifest is still named by its
        # folder, which is its id; counting it as a provider fails OPEN.
        return {"id": sub.name, "folder": sub.name, "enabled": True}
    return {"id": root.get("id") or sub.name, "folder": sub.name,
            "enabled": str(root.get("enabled", "")).strip().lower() not in ("false", "0")}


def scan_installed(dirs: list[Path] | None = None,
                   dropped: list[str] | None = None,
                   dlc: list[dict] | None = None) -> list[dict]:
    """Scan extension folders for a content.xml and return each mod's OWN
    manifest identity — this is the PRIMARY source of truth (what the game
    actually loads). Skips `ego_dlc_*` (base-game DLC, not a mod to triage).

    Folder name may differ from the manifest `id` (e.g. folder `X4CapturableXenonXL`
    has id `X4_Capturable_Xenon XL PERSONAL`) — always read the real `id` attribute.

    A folder whose `content.xml` will not parse is EXCLUDED and appended to
    *dropped*. Excluding it is almost certainly right — X4 needs that manifest to
    load the mod at all — but doing so **silently** is not: this list is the world
    model behind Tier B, x4compat, x4stats, x4similar and x4modlist, so a mod
    vanishing from it shrinks all five at once with nothing said. Report the
    exclusion; do not force the mod back in.

    *dlc*, when given, receives {id, folder, enabled} for every `ego_dlc_*` folder the
    same walk passes over: the PROVIDER set `mods("active")` needs to check a required
    dependency on a DLC. Collected here rather than by a second walk, so there is one
    enumeration of the install roots. A DLC's manifest id is its folder name (READ
    2026-09-26: all 8 installed `ego_dlc_*/content.xml`, the packed mini-DLC included --
    their manifests are loose), but it is read from the manifest anyway.
    """
    from x4validate._merge import is_dlc_folder  # lazy, as `_merge` is elsewhere here
    out = []
    # root_rank: the position of the root this mod was found in -- the WALK order. The
    # engine applies the game root's mods before the profile root's (MEASURED 2026-09-26,
    # probe round 3). root_kind: WHICH root it is -- decided by comparing the folder to
    # the configured roots, never by position (with the profile root unconfigured, the
    # Workshop root is second in the list). A dependency does not resolve between the
    # game and profile roots (F141); every other pairing is unmeasured (`_root_notes`).
    for rank, base in enumerate(dirs or default_installed_dirs()):
        if not base.is_dir():
            continue
        kind = _root_kind(base)
        for sub in sorted(base.iterdir()):
            if not sub.is_dir():
                continue
            if is_dlc_folder(sub.name):
                if dlc is not None:
                    dlc.append(_dlc_entry(sub))
                continue
            cxml = sub / "content.xml"
            if not cxml.is_file():
                continue
            try:
                root = etree.parse(str(cxml)).getroot()
            except etree.XMLSyntaxError as exc:
                if dropped is not None:
                    dropped.append(f"{sub.name}: content.xml will not parse ({exc})")
                continue
            mod_id = root.get("id") or sub.name
            # enabled="0"/"false" explicitly disables; absent/blank/anything else = enabled.
            enabled = str(root.get("enabled", "")).strip().lower() not in ("false", "0")
            out.append({
                "id": mod_id, "folder": sub.name, "path": str(sub),
                "name": root.get("name") or "", "version": root.get("version") or "",
                "date": root.get("date") or "", "author": root.get("author") or "",
                "enabled": enabled, "root_rank": rank, "root_kind": kind,
            })
    return out


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))
    except OSError:
        # silent-ok: an unresolvable path compares by its spelling; a miss makes the
        # root "custom", which is disclosed by `_root_notes`, never silently isolated.
        return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _root_kind(base: Path) -> str:
    """"game" / "profile" / "workshop" for a CONFIGURED root, else "custom".

    Read at call time from the module constants, which tests and the CLI's --dirs
    monkeypatch. A "custom" folder (an --ext-dir, a fixture) is not isolated from
    anything: which root it stands for is unknown, so the pre-F141 model applies and
    `_root_notes` discloses it whenever another root is present too."""
    for kind, root in (("game", GAME_EXTENSIONS), ("profile", PROFILE_EXTENSIONS),
                       ("workshop", WORKSHOP_CONTENT)):
        if root is not None and _same_dir(Path(root), base):
            return kind
    return "custom"


def _source_of(mod_id: str) -> str:
    return "workshop" if mod_id.startswith("ws_") else "nexus"


def _new_entry(mod_id: str, enabled: bool) -> CommentedMap:
    auto = CommentedMap()
    auto["enabled"] = enabled
    auto["installed"] = False  # set True by merge_installed() when actually found on disk
    auto["source"] = _source_of(mod_id)
    seeded = SEED_NEXUS_IDS.get(mod_id)
    auto["nexus_id"] = seeded
    auto["nexus_file_id"] = None
    # A seed is curated, not searched — but it is still not a human looking at this
    # install, so it is `exact`, never `pinned`.
    auto["id_state"] = "exact" if seeded else "unsearched"
    auto["upstream_from"] = None
    for k in ("name", "version", "updated", "status", "author", "settled", "checked_at",
              "installed_version", "installed_date", "installed_name", "folder", "path"):
        auto[k] = None
    auto["classification"] = "untriaged"
    human = CommentedMap()
    human["custom_edited"] = False
    human["ignored"] = False
    human["decision"] = ""
    human["done"] = False
    human["notes"] = ""
    # Human-owned identity. These are the durable answers: a pin survives every
    # refresh, and `source` (or nexus_id: none) records a mod that has no Nexus
    # page at all — your own overlays, Workshop-only mods, bundled add-ons —
    # so the resolver stops searching for something that was never there.
    human["nexus_id"] = None        # int | "none"
    human["nexus_file_id"] = None   # int — for an add-on shipped as a FILE on another page
    human["source"] = ""            # steam:<id> | local | bundled:<mod-id> | <url>
    human["verified_on"] = None
    e = CommentedMap()
    e["id"] = mod_id
    e["auto"] = auto
    e["human"] = human
    return e


def _new_registry() -> CommentedMap:
    m = CommentedMap()
    meta = CommentedMap()
    meta["game_build"] = None
    meta["generated"] = None
    m["meta"] = meta
    m["mods"] = CommentedSeq()
    return m


#: Filename used when the configured registry location names a DIRECTORY.
REGISTRY_FILENAME = "modlist.yaml"


def _registry_file(path: Path) -> Path:
    """Normalize a configured registry location to the FILE to read/write.

    `$X4_REGISTRY` is documented as a file, but its name invites a directory and
    the fallback (`$X4_MODS/_registry/modlist.yaml`) makes either reading
    plausible. Given a directory we used to hand it straight to `open(..., "w")`,
    which raises a bare PermissionError traceback on Windows — a confusing crash
    for a reasonable setting. Treat a directory as "put the registry in here".
    """
    return path / REGISTRY_FILENAME if path.is_dir() else path


def load_registry(path: Path | None = None) -> CommentedMap:
    path = _registry_file(path or require(
        DEFAULT_REGISTRY, "the registry location",
        "set X4_MODS (or X4_REGISTRY), or pass --registry"))
    if path.is_file():
        with open(path, encoding="utf-8") as f:
            data = _yaml.load(f)
        if data is not None:
            data.setdefault("mods", CommentedSeq())
            data.setdefault("meta", CommentedMap())
            migrate(data)  # add identity-provenance fields; promotes nothing
            return data
    return _new_registry()


class RegistryShrink(RuntimeError):
    """A save would have removed entries that only exist in the registry.

    Not a corruption check -- the write is perfectly well-formed. It is a check
    that the CONTENT is not a loss, which is the failure atomicity cannot catch:
    on 2026-09-03 a successful dump of a *fresh* registry replaced 258 triaged
    entries with 46 bytes, and every layer below (open, dump, close) worked
    exactly as designed.
    """


#: Marker for one entry in the dumped form. Counting bytes rather than parsing is
#: deliberate: MEASURED on the live 202,847-byte registry -- parse 464 ms vs count
#: 0.1 ms -- and `refresh` saves once per mod, so parsing would add minutes to a
#: routine run. Exact on our own output (266 == 266 against the parser) and
#: CRLF-safe, because the newline of a CRLF pair still precedes the marker.
_ENTRY_MARKER = b"\n- id: "

#: Below this the "did it shrink by size" backstop is meaningless -- a small
#: registry legitimately halves. The failure it exists for was 46 vs 196,363.
_SIZE_FLOOR = 4096


def _entry_count(data: bytes) -> int:
    return data.count(_ENTRY_MARKER) + (1 if data.startswith(b"- id: ") else 0)


def save_registry(reg: CommentedMap, path: Path | None = None,
                  allow_shrink: bool = False) -> None:
    """Write the registry so that neither a crash nor a wrong caller can lose it.

    Three properties, each earned by a real incident:

    1. SERIALISE FIRST. `open(path, "w")` truncates at open, so a dump that
       raised used to leave the file EMPTY while the traceback read like nothing
       had happened (MEASURED 2026-09-03: 213 -> 7 bytes). Nothing touches the
       destination until the whole document exists as text.
    2. REFUSE TO SHRINK. The registry is append-only in practice --
       `merge_installed` marks `installed: false`, it never removes, and the
       filtered lists in `_modlist` are report-only. Three recovered copies bear
       that out: 243 -> 258 -> 266, a strict superset each time. So FEWER entries
       than are on disk means a caller loaded a registry that was not the user's
       (or was already damaged) and is about to overwrite the real one. A caller
       that means it passes `allow_shrink=True`.
    3. REPLACE ATOMICALLY. A sibling temp plus `os.replace`, so an interrupted
       write leaves the previous version in place rather than a half-file.

    Raises `RegistryShrink` for (2); nothing catches it, deliberately -- the point
    is to stop, not to warn into a log nobody reads.
    """
    path = _registry_file(path or require(
        DEFAULT_REGISTRY, "the registry location",
        "set X4_MODS (or X4_REGISTRY), or pass --registry"))
    path.parent.mkdir(parents=True, exist_ok=True)

    buf = io.StringIO()
    _yaml.dump(reg, buf)                      # may raise; no file has been opened
    text = buf.getvalue()

    if not allow_shrink and path.is_file():
        old = path.read_bytes()
        new = text.encode("utf-8")
        old_n, new_n = _entry_count(old), _entry_count(new)
        # Two INDEPENDENT clauses. The count is exact on our own output but is a
        # heuristic on a file a human reformatted; size cannot be fooled that way
        # and catches the catastrophic case regardless of layout.
        lost = old_n - new_n
        shrank = len(old) > _SIZE_FLOOR and len(new) * 2 < len(old)
        if lost > 0 or shrank:
            raise RegistryShrink(
                f"REFUSING to write {path}: it holds {old_n} entries "
                f"({len(old)} bytes) and this save has {new_n} "
                f"({len(new)} bytes). The registry is append-only, so this is a "
                f"LOSS, not an update -- most likely the caller loaded a "
                f"different (or empty) registry than the one it is saving over. "
                f"Recover with: git -C <mods root> checkout -- "
                f"_registry/modlist.yaml . If the shrink is intended, pass "
                f"allow_shrink=True.")

    # `newline` is left at the default so the on-disk line endings are exactly
    # what the previous implementation produced; changing them would rewrite the
    # user's whole file in git for no reason (gotcha #57).
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            os.replace(tmp, path)
        except PermissionError as exc:
            # `scripts/x4lock.py` marks the registry read-only, so an ACCIDENTAL write
            # from inside any process fails here. That is the design -- but a bare
            # WinError 5 traceback would look like a bug in this tool rather than a
            # guard doing its job, so name the guard and the one command that lifts it.
            raise _paths.Locked(
                f"{path} is READ-ONLY, so it was not written. This is the protection "
                f"in scripts/x4lock.py, not a failure: the registry holds triage that "
                f"exists nowhere else. To write it deliberately:\n"
                f"       python scripts/x4lock.py unlock {path}\n"
                f"       <your command>\n"
                f"       python scripts/x4lock.py lock") from exc
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            # silent-ok: best-effort cleanup of OUR OWN temp file while an
            # exception is already in flight. The real failure is re-raised on
            # the next line, so nothing is hidden; a stray .tmp<pid> beside the
            # registry is inert, and masking the original error to report it
            # would lose the diagnosis the caller actually needs.
            pass
        raise


def merge(reg: CommentedMap, content_ids: list[tuple[str, bool]],
          enabled_only: bool = True) -> tuple[int, int]:
    """Add new ids as untriaged; update auto.enabled on existing; PRESERVE human:.

    Returns (added, existing)."""
    by_id = {m["id"]: m for m in reg["mods"]}
    added = existing = 0
    for mod_id, enabled in content_ids:
        if enabled_only and not enabled:
            continue
        if mod_id in by_id:
            by_id[mod_id]["auto"]["enabled"] = enabled  # auto field; human untouched
            existing += 1
        else:
            reg["mods"].append(_new_entry(mod_id, enabled))
            added += 1
    return added, existing


def merge_installed(reg: CommentedMap, installed: list[dict]) -> tuple[int, int, int]:
    """Merge the PRIMARY installed-folder scan into the registry.

    - A mod found on disk -> auto.installed=True + installed_version/date/name/
      folder/path set + auto.enabled from its own manifest. PRESERVES human: and
      any prior upstream research (nexus_id, classification, etc.) if the id was
      already tracked (e.g. from an old content.xml ingest).
    - A mod previously tracked but NOT found in this scan -> auto.installed=False
      (kept for the "old modlist, not currently installed" diff view; not deleted).

    Returns (new_to_registry, matched_existing, no_longer_installed)."""
    by_id = {m["id"]: m for m in reg["mods"]}
    installed_ids = {e["id"] for e in installed}
    new = matched = 0
    for e in installed:
        if e["id"] in by_id:
            m = by_id[e["id"]]
            matched += 1
        else:
            m = _new_entry(e["id"], True)
            reg["mods"].append(m)
            new += 1
        a = m["auto"]
        a["installed"] = True
        a["enabled"] = e["enabled"]
        a["installed_version"] = e["version"] or None
        a["installed_date"] = e["date"] or None
        a["installed_name"] = e["name"] or None
        a["folder"] = e["folder"]
        a["path"] = e["path"]
        if not a.get("author") and e["author"]:
            a["author"] = e["author"]  # fill in if upstream research hasn't set it yet

    not_installed = 0
    for m in reg["mods"]:
        if m["id"] not in installed_ids:
            m["auto"]["installed"] = False  # unconditional: backfills pre-existing entries too
            not_installed += 1
    return new, matched, not_installed


# --- Dashboard generation ---

# Classification reflects what the API can HONESTLY tell us. "9.0-ready" is NOT
# in the API, so we infer from update-date relative to the 9.0 release; old mods
# are "predates-9.0 / review", never auto-claimed compatible.
_LANES = [
    ("ready", "✅ LIKELY 9.0-READY (updated post-9.0, settled)"),
    (UNCONFIRMED_LANE, "🔎 NEEDS IDENTITY CONFIRMATION (upstream data fetched, but the id is a GUESS)"),
    ("churning", "⏸ CHURNING (updated <14d ago — defer / pin)"),
    ("predates-9.0", "⚠ PREDATES 9.0 — review (compat unconfirmed)"),
    ("custom-local", "🔧 CUSTOM-LOCAL FORK (upstream hidden/removed, but you maintain your own edits)"),
    ("off-nexus", "📎 OFF-NEXUS (no Nexus page — Workshop, bundled, or your own; confirmed, not pending)"),
    ("drop", "❌ DROP (removed from Nexus)"),
    ("untriaged", "❓ UNTRIAGED (identity unresolved)"),
    ("error", "⚠ ERROR (API fetch failed)"),
]


def _bar(done: int, total: int, width: int = 12) -> str:
    filled = 0 if total == 0 else round(width * done / total)
    return "▓" * filled + "░" * (width - filled)


def _active(m) -> bool:
    """The PRIMARY active set: physically installed, not ignored.
    (`enabled` is the mod's own manifest flag — installed-but-self-disabled mods
    are still 'active' for triage purposes; the game just won't load them yet.)"""
    return bool(m["auto"].get("installed")) and not m["human"].get("ignored")


def needs_review(reg: CommentedMap) -> list:
    """Active entries whose IDENTITY a human has not settled.

    Keyed on `id_state`, not on the old free-text `resolve` label — the point is
    that a guess stays visible until somebody confirms or corrects it. `off-nexus`
    is settled (a human said so) and drops out; `pinned`/`exact` never appear.
    """
    return [m for m in reg["mods"]
            if _active(m) and identity(m)[2] not in TRUSTED_ID_STATES | {"off-nexus"}]


def unverified_summary(reg: CommentedMap) -> dict[str, int]:
    """Counts by id_state over the active set — the denominator for 'how much of
    this registry is guesswork?', which is not answerable without it."""
    out: dict[str, int] = {}
    for m in reg["mods"]:
        if _active(m):
            out[identity(m)[2]] = out.get(identity(m)[2], 0) + 1
    return out


def not_installed(reg: CommentedMap) -> list:
    """Tracked (e.g. from the old profile content.xml) but NOT found in the
    installed-folder scan — the diff/backstop view ('did I forget to re-acquire
    this?'). Excludes ignored entries."""
    return [m for m in reg["mods"]
            if not m["auto"].get("installed") and not m["human"].get("ignored")]


def generate_dashboard(reg: CommentedMap) -> str:
    mods = [m for m in reg["mods"] if _active(m)]
    ignored = sum(1 for m in reg["mods"] if m["human"].get("ignored"))
    total = len(mods)
    done = sum(1 for m in mods if m["human"].get("done"))
    build = reg.get("meta", {}).get("game_build") or "?"
    lines = ["# Phase-A Worklist (generated)\n",
             f"**Game build {build}** · {total} installed mods "
             f"({ignored} ignored) · {done} done\n",
             f"```\nprogress  {_bar(done, total)}  {done}/{total}\n```\n"]

    counts = unverified_summary(reg)
    trusted = sum(n for s, n in counts.items() if s in TRUSTED_ID_STATES)
    lines.append(f"**Identity provenance:** {trusted}/{total} confirmed · " +
                 " · ".join(f"{n} {s}" for s, n in sorted(counts.items())
                            if s not in TRUSTED_ID_STATES) + "\n")

    review = needs_review(reg)
    if review:
        lines.append(f"## ⚠ UNCONFIRMED IDENTITY  ({len(review)})")
        lines.append("Every row below is a **guess or a blank**, not a fact — anything upstream "
                     "said about it may describe a different mod. Confirm/correct with "
                     "`x4modlist resolve <id> <nexus_id> [--file <file_id>]`; if it has no "
                     "Nexus page at all say so with `x4modlist source <id> <steam:…|local|"
                     "bundled:…|url>` (or `x4modlist resolve <id> none`) and it stops being "
                     "searched; junk → `x4modlist ignore <id>`.\n")
        lines.append("| Mod | id_state | current guess | candidates |")
        lines.append("|-----|----------|---------------|------------|")
        for m in sorted(review, key=lambda x: x["id"]):
            a = m["auto"]
            nid, fid, state = identity(m)
            cur = (f"{nid}" + (f" file {fid}" if fid else "") +
                   f":{a.get('name')}" if nid else "—")
            cands = " | ".join(a.get("candidates") or []) or "—"
            lines.append(f"| `{m['id']}` | **{state}** | {cur} | {cands} |")
        lines.append("")

    # UPDATES. "Available" = the upstream file (newest MAIN, or the pinned file) was
    # UPLOADED more than a grace window AFTER the installed manifest's date -- both
    # dates are printed, because version strings are not comparable across Nexus's
    # shapes (AUDIT-2026-09-24 RG-3). An upload within the grace window reads
    # `same-release?` instead (RG-3 refinement, user decision 2026-09-26): authors
    # often date the manifest before they finish uploading, so a gap that small is
    # very often the SAME release landing, not a new one -- it is counted in the
    # tally below but EXCLUDED from the UPDATE AVAILABLE table so it never reads as
    # a confirmed update. The window's length is `_modlist.UPDATE_GRACE_DAYS` --
    # imported lazily here (not at module scope) because `_modlist` imports THIS
    # module, and a top-level import the other way would cycle.
    # The tally covers every active row, so "0 available" can be told from "never checked".
    from x4validate._modlist import UPDATE_GRACE_DAYS
    tally: dict[str, int] = {}
    for m in mods:
        v = m["auto"].get("update") or "not checked"
        tally[v] = tally.get(v, 0) + 1
    lines.append(f"**Updates** (upstream upload date more than {UPDATE_GRACE_DAYS} days "
                 "after the installed manifest date; same-release? = uploaded within that "
                 "window, likely the same release, not counted as available; versions not "
                 "compared): " +
                 " · ".join(f"{n} {k}" for k, n in sorted(tally.items())) + "\n")
    avail = [m for m in mods if m["auto"].get("update") == "available"]
    if avail:
        lines.append(f"## ⬆ UPDATE AVAILABLE  ({len(avail)})")
        lines.append("| Mod | id | installed manifest date | upstream uploaded | upstream file |")
        lines.append("|-----|----|-------------------------|-------------------|---------------|")
        for m in sorted(avail, key=lambda x: x["id"]):
            a = m["auto"]
            lines.append(f"| {a.get('name') or a.get('installed_name') or '—'} | {m['id']} | "
                         f"{a.get('installed_date') or '—'} | "
                         f"{a.get('upstream_newest_uploaded') or '—'} | "
                         f"{a.get('upstream_newest') or '—'} |")
        lines.append("")

    by_class: dict[str, list] = {}
    for m in mods:
        by_class.setdefault(m["auto"].get("classification", "untriaged"), []).append(m)
    for key, title in _LANES:
        group = by_class.get(key, [])
        if not group:
            continue
        lines.append(f"## {title}  ({len(group)})")
        lines.append("| Mod | id | identity | source | installed | upstream | updated | status |")
        lines.append("|-----|----|----------|--------|-----------|----------|---------|--------|")
        for m in sorted(group, key=lambda x: str(x["auto"].get("name") or x["id"])):
            a = m["auto"]
            name = a.get("name") or a.get("installed_name") or "—"
            inst = a.get("installed_version") or "—"
            ver = a.get("version") or "—"
            upd = a.get("updated") or "—"
            _, fid, state = identity(m)
            # The upstream columns are only as good as the id that fetched them, so
            # the provenance travels in the same row rather than in a footnote.
            prov = state + (f" · file {fid}" if fid else "")
            lines.append(f"| {name} | {m['id']} | {prov} | {a.get('source')} | {inst} | "
                         f"{ver} | {upd} | {a.get('status') or '—'} |")
        lines.append("")

    old = not_installed(reg)
    if old:
        lines.append(f"## 📦 OLD MODLIST — NOT CURRENTLY INSTALLED  ({len(old)})")
        lines.append("Tracked previously (e.g. old content.xml) but no matching folder found on "
                     "disk right now — re-acquire if still wanted, or `ignore` if intentionally dropped.\n")
        lines.append("| id | last known name | last known status |")
        lines.append("|----|------------------|--------------------|")
        for m in sorted(old, key=lambda x: x["id"]):
            a = m["auto"]
            lines.append(f"| {m['id']} | {a.get('name') or '—'} | {a.get('classification') or 'untriaged'} |")
        lines.append("")
    return "\n".join(lines)


def write_dashboard(reg: CommentedMap, path: Path | None = None) -> Path:
    # Same normalization as load/save, or the dashboard lands somewhere else:
    # with a DIRECTORY configured, `.parent` of the raw path is the directory
    # ABOVE it, so WORKLIST.md ended up beside the registry folder instead of
    # inside it. Every step of a lookup chain has to learn the same rule.
    path = path or (_registry_file(require(
        DEFAULT_REGISTRY, "the registry location",
        "set X4_MODS (or X4_REGISTRY), or pass --registry")).parent / "WORKLIST.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate_dashboard(reg), encoding="utf-8")
    return path
