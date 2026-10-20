r"""A deliberate experiment that makes the engine SHOW its extension load order.

    uv run python scripts/load-order-probe.py build   <dir> [--only <prefix>]  # probe mods + PREDICTION.json
    uv run python scripts/load-order-probe.py deploy  <dir> [--apply] # through deploy-mod.py's guards
    uv run python scripts/load-order-probe.py score   <dir> [debug.txt]
    uv run python scripts/load-order-probe.py remove  [--apply]       # only folders THIS tool wrote
    uv run python scripts/load-order-probe.py profile-entries         # the 2 profile lines RG-6 needs

Run from `tools/x4validate`. Launch X4 to the MAIN MENU between `deploy` and `score`, then
quit -- no save is loaded, so nothing is baked into a savegame.

WHY. The order of the engine's "Failed to verify the file signature" checks settled the
common case (AUDIT-2026-09-24 LO-1: 372 file classes, 5,134 pairs, 0 inverted by the
multi-pass rule). It cannot show shapes the installed modlist never exercises, and it is
SIGNATURE order, not direct proof of the order patches APPLY in. Every probe here ships a
`libraries/wares.xml` diff, so:

  * ORDER  -- each loaded probe is signature-checked in load order (one class, one sequence);
  * LOADED -- a probe that never appears was not loaded at all;
  * APPLY  -- the collision probes write/read attributes on the `energycells` ware. A
    `<replace>` of an attribute that an EARLIER mod added succeeds silently; one whose adder
    loads LATER logs `No matching node ... in patch file 'extensions\<probe>\...'`.

The PREDICTION is written by `build`, BEFORE the launch, from the rule measured on the log
(held-out test: the rule was fitted on the same log it was first scored on). Cases the log
could not reach are predicted UNKNOWN with the alternatives named; `score` records what the
engine did. Nothing here assumes the answer it is trying to find.

Exit: 0 every non-UNKNOWN prediction held · 1 a prediction failed · 2 cannot run.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
from x4recycle import recycle_or_raise  # noqa: E402
sys.path.insert(0, str(HERE.parent))
from x4validate import _paths  # noqa: E402

MARKER = ".x4-load-order-probe"          # every folder this tool writes carries it
ID_PREFIX = "lo_probe"                   # every manifest id this tool writes starts with it
WARE = "energycells"                     # base ware the probes annotate (no save is loaded)
DLC_ONLY_WARE = "bofu"                   # defined only by ego_dlc_boron (0 hits in base wares.xml)


def _add(attr: str) -> str:
    return f'<add sel="//ware[@id=\'{WARE}\']" type="@{attr}">1</add>'


def _use(attr: str) -> str:
    return f'<replace sel="//ware[@id=\'{WARE}\']/@{attr}">2</replace>'


PROOF_ATTR = "loproof_"


def _proof(tag: str) -> str:
    """A replace that can NEVER match (no mod adds @loproof_*): the engine logs
    No-matching-node for it exactly when this file is APPLIED, so the line proves
    application even where no signature line is ever written (the profile root)."""
    return f'<replace sel="//ware[@id=\'{WARE}\']/@{PROOF_ATTR}{tag}">1</replace>'


# folder: (manifest id, dependencies [(id, optional)], enabled, wares.xml ops, what it tests)
PROBES: dict[str, tuple[str, list[tuple[str, bool]], bool, str, str]] = {
    # --- APPLY order: collisions on one attribute --------------------------------------
    "lo_probe_ca_add": ("lo_probe_ca_add", [], True, _add("lopa"), "adds @lopa"),
    "lo_probe_cb_use": ("lo_probe_cb_use", [], True, _use("lopa"),
                        "replaces @lopa, added by an EARLIER-sorting mod"),
    "lo_probe_cc_use": ("lo_probe_cc_use", [], True, _use("lopd"),
                        "replaces @lopd, added by a LATER-sorting mod, no dependency"),
    "lo_probe_cd_add": ("lo_probe_cd_add", [], True, _add("lopd"), "adds @lopd"),
    "lo_probe_ce_dep": ("lo_probe_ce_dep", [("lo_probe_cf_add", False)], True, _use("lopf"),
                        "replaces @lopf, added by a LATER-sorting mod it DEPENDS on"),
    "lo_probe_cf_add": ("lo_probe_cf_add", [], True, _add("lopf"), "adds @lopf"),
    # --- sort key ------------------------------------------------------------------------
    "lo_probe_k_Zc": ("lo_probe_k_zc", [], True, _add("lokzc"), "mixed case"),
    "lo_probe_k_ya": ("lo_probe_k_ya", [], True, _add("lokya"), "mixed-case control"),
    "lo_probe_k_q_x": ("lo_probe_k_q_x", [], True, _add("lokqx"), "underscore vs letter"),
    "lo_probe_k_qa": ("lo_probe_k_qa", [], True, _add("lokqa"), "underscore control"),
    "lo_probe_k_ßa": ("lo_probe_k_ssa", [], True, _add("lokss"),
                           "non-ASCII: sharp s (Python upper -> 'SS', ordinal upper keeps it)"),
    "lo_probe_k_sz": ("lo_probe_k_sz", [], True, _add("loksz"), "non-ASCII control"),
    # --- dependency shapes the installed modlist never exercises -----------------------
    "lo_probe_d_reqmiss": ("lo_probe_d_reqmiss", [("lo_probe_nothere", False)], True,
                           _add("lodrm"), "REQUIRED dependency that is not installed"),
    "lo_probe_d_optmiss": ("lo_probe_d_optmiss", [("lo_probe_nothere", True)], True,
                           _add("lodom"), "OPTIONAL dependency that is not installed"),
    "lo_probe_d_cyc1": ("lo_probe_d_cyc1", [("lo_probe_d_cyc2", False)], True, _add("lodc1"),
                        "dependency cycle (1 of 2)"),
    "lo_probe_d_cyc2": ("lo_probe_d_cyc2", [("lo_probe_d_cyc1", False)], True, _add("lodc2"),
                        "dependency cycle (2 of 2)"),
    "lo_probe_d_dup1": ("lo_probe_d_dupid", [], True, _add("lodd1"), "duplicate id (1 of 2)"),
    "lo_probe_d_dup2": ("lo_probe_d_dupid", [], True, _add("lodd2"), "duplicate id (2 of 2)"),
    "lo_probe_d_disabled": ("lo_probe_d_disabled", [], False, _add("lodds"),
                            "manifest enabled=0, no profile entry"),
    "lo_probe_d_disdep": ("lo_probe_d_disdep", [("lo_probe_d_disabled", False)], True,
                          _add("loddd"), "depends on a DISABLED mod"),
    # --- who decides "enabled" (AUDIT RG-6). These two need PROFILE entries, which this
    # tool never writes: `profile-entries` prints them for a human-confirmed edit. --------
    "lo_probe_d_profon": ("lo_probe_d_profon", [], False, _add("lodpo"),
                          "manifest enabled=0 but PROFILE enabled=true"),
    "lo_probe_d_profnoattr": ("lo_probe_d_profnoattr", [], True, _add("lodpn"),
                              "profile entry with NO enabled attribute"),
    # --- DLC position: sorts before every ego_dlc_* -------------------------------------
    "a_lo_probe_dlc": ("lo_probe_a_dlc", [], True,
                       f'<add sel="//ware[@id=\'{DLC_ONLY_WARE}\']" type="@lopdlc">1</add>',
                       "annotates a ware only ego_dlc_boron defines"),
    # --- ROUND 2 (2026-09-26): an OPTIONAL dependency on a DISABLED mod ------------------
    # Round 1 measured a REQUIRED dependency on a disabled mod (dependent NOT loaded). The
    # optional case decides whether "declare optional deps to load after X" is safe when X
    # is later switched off. Both ways a mod is disabled: its manifest, and the profile
    # (what the in-game Extensions menu writes). The required twin is the control.
    "lo_probe_d_optdis": ("lo_probe_d_optdis", [("lo_probe_d_disabled", True)], True,
                          _add("lodod"), "OPTIONAL dependency on a manifest-DISABLED mod"),
    "lo_probe_d_profoff": ("lo_probe_d_profoff", [], True, _add("lodpf"),
                           "manifest enabled=1 but PROFILE enabled=false"),
    "lo_probe_d_optprofoff": ("lo_probe_d_optprofoff", [("lo_probe_d_profoff", True)], True,
                              _add("lodop"), "OPTIONAL dependency on a PROFILE-disabled mod"),
    "lo_probe_d_reqprofoff": ("lo_probe_d_reqprofoff", [("lo_probe_d_profoff", False)], True,
                              _add("lodrp"), "REQUIRED dependency on a PROFILE-disabled mod"),
    # --- ROUND 2: a SECOND extensions root (the user profile's extensions/) -------------
    # An apply chain alternating game root (a, c) and profile root (b, d). Each probe
    # replaces the attribute the previous one added, so the No-matching-node pattern alone
    # tells three layouts apart (see classify_roots) -- even if profile files never log a
    # signature line. e/f test a REQUIRED dependency across the roots, both directions.
    # ROUND 3: each also carries a PROOF op (`_proof`) that can never match, so its file
    # logs No-matching-node iff it was APPLIED -- and those lines come out in apply order.
    "lo_probe_x_a": ("lo_probe_x_a", [], True, _add("loxa") + _proof("a"),
                     "game root, chain 1 of 4"),
    "lo_probe_x_b": ("lo_probe_x_b", [], True, _use("loxa") + _add("loxb") + _proof("b"),
                     "PROFILE root, chain 2 of 4"),
    "lo_probe_x_c": ("lo_probe_x_c", [], True, _use("loxb") + _add("loxc") + _proof("c"),
                     "game root, chain 3 of 4"),
    "lo_probe_x_d": ("lo_probe_x_d", [], True, _use("loxc") + _proof("d"),
                     "PROFILE root, chain 4 of 4"),
    "lo_probe_x_e": ("lo_probe_x_e", [("lo_probe_x_a", False)], True, _add("loxe"),
                     "PROFILE root, REQUIRED dependency on a GAME-root mod"),
    "lo_probe_x_f": ("lo_probe_x_f", [("lo_probe_x_d", False)], True, _add("loxf"),
                     "game root, REQUIRED dependency on a PROFILE-root mod"),
}
PROFILE_ROOT = {"lo_probe_x_b", "lo_probe_x_d", "lo_probe_x_e"}


def root_of(folder: str) -> str:
    return "profile" if folder in PROFILE_ROOT else "game"


class ProbeRefused(Exception):
    pass


# ------------------------------------------------------------------------ prediction
def _rule_key(name: str) -> str:
    """The MEASURED sort key: case-insensitive via per-character upper (not str.upper(),
    which maps sharp s to 'SS' and so changes the LENGTH of the key)."""
    return "".join(c.upper() if len(c.upper()) == 1 else c for c in name)


def predict(only: str | None = None) -> dict:
    """The prediction, derived ONLY from the rule measured on the real log. Shapes that log
    could not reach are UNKNOWN, with the alternatives written down before the launch."""
    known = [f for f in PROBES if f not in {
        "lo_probe_d_reqmiss", "lo_probe_d_cyc1", "lo_probe_d_cyc2", "lo_probe_d_dup1",
        "lo_probe_d_dup2", "lo_probe_d_disabled", "lo_probe_d_disdep",
        "lo_probe_d_profon", "lo_probe_d_profnoattr",
        "lo_probe_k_ßa", "lo_probe_k_sz",
        # round 2: never measured, and anything in or depending on the profile root
        "lo_probe_d_optdis", "lo_probe_d_profoff", "lo_probe_d_optprofoff",
        "lo_probe_d_reqprofoff", "lo_probe_x_f", *PROFILE_ROOT}
        and (only is None or f.startswith(only))]
    ids = {PROBES[f][0]: f for f in known}
    order, done = [], set()
    names = sorted(known, key=_rule_key)
    while len(order) < len(names):
        before = len(order)
        for n in names:
            if n in done:
                continue
            need = {ids[d] for d, _opt in PROBES[n][1] if d in ids}
            if need <= done:
                order.append(n)
                done.add(n)
        if len(order) == before:
            break
    return {
        "relative_order": order,
        "applies": {p: v for p, v in {
            "lo_probe_cb_use": "OK", "lo_probe_cc_use": "NO_MATCH", "lo_probe_ce_dep": "OK",
        }.items() if only is None or p.startswith(only)},
        "unknown": {
            "sharp_s": "lo_probe_k_ßa before lo_probe_k_sz (Python str.upper -> 'SS') "
                       "OR after (per-character / ordinal upper, NTFS-like)",
            "lo_probe_d_reqmiss": "loaded OR not loaded",
            "lo_probe_d_optmiss": "predicted LOADED (optional)",
            "cycle": "both load (fallback) OR neither loads",
            "duplicate_id": "both load OR only the first-sorting one",
            "lo_probe_d_disabled": "not loaded (manifest default) OR loaded",
            "lo_probe_d_disdep": "loaded OR not loaded",
            "lo_probe_d_profon": "loaded (profile overrides manifest) OR not loaded",
            "lo_probe_d_profnoattr": "loaded (missing attr = enabled) OR not loaded",
            "a_lo_probe_dlc": "OK (all DLC load before mods) OR NO_MATCH (DLC in the walk)",
            "lo_probe_d_optdis": "loaded (optional never blocks) OR not loaded (like required)",
            "lo_probe_d_profoff": "predicted NOT loaded (profile false; round 1 showed the "
                                  "profile overrides the manifest)",
            "lo_probe_d_optprofoff": "loaded OR not loaded -- the case the overlays rely on",
            "lo_probe_d_reqprofoff": "predicted NOT loaded (control: round 1 required-on-"
                                     "disabled)",
            "cross_root": "INTERLEAVED (one walk by name) OR GAME_ROOT_FIRST OR PROFILE_FIRST",
            "lo_probe_x_e": "loaded OR not loaded (does a dependency resolve across roots?)",
            "lo_probe_x_f": "loaded OR not loaded (the other direction)",
        },
        "listdir_note": "record os.listdir order of the sharp-s pair at deploy time",
    }


# ------------------------------------------------------------------------ build / deploy
def _manifest(folder: str) -> str:
    mid, deps, enabled, _ops, what = PROBES[folder]
    dep_xml = "".join(
        f'\n  <dependency id="{d}"{" optional=\"true\"" if opt else ""} name="{d}"/>'
        for d, opt in deps)
    return (f'<?xml version="1.0" encoding="utf-8"?>\n'
            f'<content id="{mid}" name="{folder}" version="100" '
            f'enabled="{1 if enabled else 0}" save="0" '
            f'description="Load-order probe: {what}. Remove after the test.">'
            f'{dep_xml}\n</content>\n')


def build(out: Path, only: str | None = None) -> None:
    """Write the probes (all, or those whose folder starts with *only*) + PREDICTION.json.
    A subset build predicts only its own probes, and deploy/score work from what was
    BUILT (`built_probes`), so a focused re-run never drags the full set along."""
    out.mkdir(parents=True, exist_ok=True)
    chosen = {f: v for f, v in PROBES.items() if only is None or f.startswith(only)}
    if not chosen:
        raise ProbeRefused(f"--only {only!r} selects no probe")
    for folder, (_mid, _deps, _en, ops, _what) in chosen.items():
        d = out / folder
        (d / "libraries").mkdir(parents=True, exist_ok=True)
        (d / "content.xml").write_bytes(_manifest(folder).encode("utf-8"))
        (d / "libraries" / "wares.xml").write_bytes(
            f'<?xml version="1.0" encoding="utf-8"?>\n<diff>\n  {ops}\n</diff>\n'.encode("utf-8"))
        (d / MARKER).write_bytes(b"written by scripts/load-order-probe.py\n")
    (out / "PREDICTION.json").write_bytes(
        json.dumps(predict(only), indent=2, ensure_ascii=False).encode("utf-8"))
    print(f"built {len(chosen)} probe mods + PREDICTION.json in {out}")


def built_probes(src: Path) -> list[str]:
    """The probe folders a `build` actually wrote into *src* (marker present), sorted."""
    return sorted(p for p in PROBES if (src / p / MARKER).is_file())


def _deploy_module():
    spec = importlib.util.spec_from_file_location("deploy_mod", HERE / "deploy-mod.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ext_root() -> Path | None:
    return _paths.game_extensions()


def _profile_root() -> Path | None:
    return _paths.profile_extensions()


def write_profile_probes(src: Path, dst_root: Path, apply: bool) -> None:
    """Write the PROFILE-root probes into *dst_root*. Deliberately NOT deploy-mod.py: that
    script refuses a profile root by design (real mods belong in the game root), and this
    experiment exists to measure exactly what happens there.

    Narrow by construction: only the probes named in PROFILE_ROOT, only into folders that do
    NOT exist yet (nothing is ever overwritten), every guard before any write, and every
    written file re-read and compared byte for byte."""
    names = sorted(p for p in PROFILE_ROOT if (src / p / MARKER).is_file())
    if not names:
        raise ProbeRefused(f"{src} holds no profile-root probe; run `build` first")
    if not dst_root.is_dir():
        if apply:
            raise ProbeRefused(f"the profile extensions folder {dst_root} does not exist")
        # A PREVIEW still runs every guard and names every write: skipping them here made
        # the dry run understate what --apply does (release review 2026-09-26).
        print(f"  would create {dst_root} (it does not exist yet; X4 creates it only once a "
              "mod lives there)")
    for n in names:
        if not (src / n / MARKER).is_file():
            raise ProbeRefused(f"{src / n} is not a build of this tool; run `build` first")
        if (dst_root / n).exists():
            raise ProbeRefused(f"{dst_root / n} already exists -- nothing is overwritten; "
                               "run `remove --apply` first if it is a stale probe")
    for n in names:
        files = sorted(f for f in (src / n).rglob("*") if f.is_file())
        print(f"  {'write' if apply else 'would write'} {dst_root / n} ({len(files)} files)")
        if not apply:
            continue
        for f in files:
            t = dst_root / n / f.relative_to(src / n)
            t.parent.mkdir(parents=True, exist_ok=True)
            data = f.read_bytes()
            t.write_bytes(data)
            if t.read_bytes() != data:
                raise ProbeRefused(f"re-read of {t} differs from what was written")


def deploy(src: Path, apply: bool) -> int:
    dm = _deploy_module()
    ext, prof = _ext_root(), _profile_root()
    built = built_probes(src)
    if not built:
        print(f"refused: {src} is not a build of this tool (no probe folder in it); "
              "run `build` first", file=sys.stderr)
        return 2
    names = [p for p in built if root_of(p) == "game"]
    needs_profile = any(root_of(p) == "profile" for p in built)
    if prof is None and needs_profile:
        print("refused: no profile extensions folder is configured (X4_PROFILE), and the "
              "round-2 probes need one", file=sys.stderr)
        return 2
    if ext is not None:
        clash = [n for n in names if (ext / n).exists() and not (ext / n / MARKER).is_file()]
        if clash:
            print(f"refused: {clash} already exist in {ext} and were not written by this "
                  "tool -- nothing is overwritten", file=sys.stderr)
            return 2
    try:
        for n in names:                      # every guard before any write
            dm.deploy(n, False, src_root=src, ext_root=ext, out=lambda *_: None)
        if needs_profile:
            # its guards, no write -- and in a dry run ALSO when the folder does not exist
            # yet, so the preview names every write --apply would make.
            write_profile_probes(src, prof, apply=False)
    except (dm.Refused, ProbeRefused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    ok = True
    for n in names:
        ok &= dm.deploy(n, apply, src_root=src, ext_root=ext)
    print(f"profile root: {prof}")
    if apply and needs_profile:
        if not ok:
            # A game-root probe that did not verify makes the round's evidence unreadable;
            # writing the profile half anyway only adds folders to clean up.
            print("refused: a game-root probe did not VERIFY, so the profile-root probes were "
                  "NOT written; fix the game-root deploy (or `remove --apply`) and re-run",
                  file=sys.stderr)
            return 1
        prof.mkdir(exist_ok=True)            # X4 creates it only once a mod lives there
        write_profile_probes(src, prof, apply=True)
    if apply and ext is not None:
        pair = [n for n in os.listdir(ext) if n in ("lo_probe_k_ßa", "lo_probe_k_sz")]
        print(f"os.listdir order of the sharp-s pair: {pair}")
    return 0 if ok else 1


def remove(apply: bool) -> int:
    """Remove ONLY folders carrying this tool's marker AND an lo_probe manifest id, from the
    game root AND the profile root. Whole marked folders go to the Recycle Bin/Trash."""
    ext = _ext_root()
    if ext is None or not ext.is_dir():
        print("cannot run: no game extensions folder configured", file=sys.stderr)
        return 2
    prof = _profile_root()
    roots = [ext] + ([prof] if prof is not None and prof.is_dir() else [])
    # DEDUPED BY RESOLVED PATH. Misconfigured so both resolve to one folder, every probe
    # was listed twice and the second unlink raised FileNotFoundError part way through.
    seen: set[Path] = set()
    roots = [r for r in roots if not (r.resolve() in seen or seen.add(r.resolve()))]
    victims = []
    for d in sorted(x for r in roots for x in r.iterdir()):
        if not (d.is_dir() and not d.is_symlink() and (d / MARKER).is_file()):
            continue
        cx = (d / "content.xml").read_text(encoding="utf-8", errors="replace") \
            if (d / "content.xml").is_file() else ""
        m = re.search(r'<content\s+id="([^"]+)"', cx)
        if not (m and m.group(1).startswith(ID_PREFIX)):
            print(f"  SKIP {d.name}: marker present but manifest id is not {ID_PREFIX}*")
            continue
        victims.append(d)
    print(f"{len(victims)} probe folder(s) in {' and '.join(map(str, roots))}:")
    for d in victims:
        files = sorted(p for p in d.rglob("*") if p.is_file() and not p.is_symlink())
        print(f"  {d.name}: {len(files)} file(s)")
        if not apply:
            continue
        try:
            recycle_or_raise([d])
        except (OSError, RuntimeError) as exc:
            print(f"NOT removed: {d}: {exc}", file=sys.stderr)
            return 1
    if not apply:
        print("(dry run -- nothing removed; add --apply)")
    left = [d.name for d in victims if d.exists()] if apply else []
    if left:
        print(f"NOT removed: {left}", file=sys.stderr)
        return 1
    return 0


# ------------------------------------------------------------------------ score
BS = "\\"
# Root-agnostic (round 2): a game-root file logs as '.\extensions\<folder>\...'; how a
# PROFILE-root file logs is exactly what is unmeasured, so any path ending in
# extensions\<folder>\libraries\wares.xml is accepted, and `score` also prints every probe
# line neither pattern recognised, so an unexpected shape is read, never silently dropped.
SIG = re.compile(r"Failed to verify the file signature for file '[^']*?extensions\\([^\\']+)"
                 r"\\libraries\\wares\.xml'", re.I)
# The engine quotes the selector with ' and selectors themselves contain ' (MEASURED on
# real lines: `'//sound[@id='wpn_HEPT_shoot']/sample'`), so the path is matched lazily up
# to the patch-file clause. A `[^']*` here matched NOTHING and scored every NO_MATCH as OK
# (caught by tests/test_load_order_probe.py before any launch).
NOMATCH = re.compile(r"No matching node for path '.*?' in patch file "
                     r"'[^']*?extensions\\([^\\']+)\\libraries\\wares", re.I)
CHAIN = ("lo_probe_x_a", "lo_probe_x_b", "lo_probe_x_c", "lo_probe_x_d")


def classify_roots(loaded: set[str], errors: dict[str, int]) -> str:
    """Which layout explains the cross-root apply chain? Each chain probe (b, c, d) replaces
    the attribute its predecessor added, so it logs No-matching-node exactly when it loaded
    BEFORE that predecessor. Predicted NO_MATCH sets per layout:

      INTERLEAVED      one walk by name over both roots: a b c d       -> {}
      GAME_ROOT_FIRST  a c | b d                                        -> {c}
      PROFILE_FIRST    b d | a c                                        -> {b, d}

    A chain probe that never loaded writes NO error line, which would read as "OK" -- so an
    incomplete chain is CANNOT_TELL, never a layout."""
    if not set(CHAIN) <= loaded:
        return "CANNOT_TELL"
    seen = frozenset(p for p in CHAIN if errors.get(p))
    return {frozenset(): "INTERLEAVED",
            frozenset({"lo_probe_x_c"}): "GAME_ROOT_FIRST",
            frozenset({"lo_probe_x_b", "lo_probe_x_d"}): "PROFILE_FIRST"}.get(seen, "INCONSISTENT")


def read_log(lines) -> tuple[list[str], dict[str, int]]:
    """(probe folders in wares.xml signature order, {probe folder: No-matching-node count})."""
    probes = {p.lower() for p in PROBES}
    order, errors = [], {}
    for line in lines:
        m = SIG.search(line)
        if m and m.group(1).lower() in probes and m.group(1).lower() not in order:
            order.append(m.group(1).lower())
        m = NOMATCH.search(line)
        if m and m.group(1).lower() in probes and "@" + PROOF_ATTR not in line:
            errors[m.group(1).lower()] = errors.get(m.group(1).lower(), 0) + 1
    return order, errors


def read_proofs(lines) -> list[str]:
    """Probe folders whose PROOF op logged No-matching-node, in log (= apply) order, each
    once. A folder here was APPLIED; a proof line is never a chain miss (read_log)."""
    probes = {p.lower() for p in PROBES}
    seen: list[str] = []
    for line in lines:
        if "@" + PROOF_ATTR not in line:
            continue
        m = NOMATCH.search(line)
        if m and m.group(1).lower() in probes and m.group(1).lower() not in seen:
            seen.append(m.group(1).lower())
    return seen


def score(src: Path, log: Path) -> int:
    pred = json.loads((src / "PREDICTION.json").read_text(encoding="utf-8"))
    with log.open(encoding="utf-8", errors="replace") as fh:
        order, errors = read_log(fh)
    with log.open(encoding="utf-8", errors="replace") as fh:
        proofs = read_proofs(fh)
    built = [p.lower() for p in built_probes(src)] or [p.lower() for p in PROBES]
    if not order and not proofs:
        print(f"cannot score: no probe appears in {log} -- were they deployed and the game "
              "launched after deploying?", file=sys.stderr)
        return 2
    # LOADED = signature-checked OR proven applied. A profile-root file never gets a
    # signature line (round 2), so without the proof op it could only ever read "absent".
    loaded = set(order) | set(proofs)
    fails = 0
    want = [p.lower() for p in pred["relative_order"]]
    got = [p for p in order if p in want]
    missing = [p for p in want if p not in loaded]
    print(f"probes loaded: {len(loaded & set(built))} of {len(built)} built   log: {log}")
    print(f"engine order : {order}")
    print(f"apply order (proof lines): {', '.join(proofs) if proofs else '(none)'}")
    if missing:
        fails += 1
        print(f"FAIL predicted-loaded probes absent: {missing}")
    if got != [p for p in want if p in loaded]:
        fails += 1
        print(f"FAIL relative order\n  predicted: {want}\n  engine   : {got}")
    else:
        print(f"ok   relative order of {len(got)} predicted probes")
    for probe, expect in pred["applies"].items():
        actual = "NO_MATCH" if errors.get(probe.lower()) else (
            "OK" if probe.lower() in loaded else "NOT LOADED")
        mark = "ok  " if actual == expect else "FAIL"
        fails += actual != expect
        print(f"{mark} apply {probe}: predicted {expect}, engine {actual}")
    print("\nRECORDED (were UNKNOWN before the launch):")

    def pos(p):
        return order.index(p.lower()) if p.lower() in order else None

    def state(p):
        if p.lower() in order:
            return f"LOADED at {order.index(p.lower())}"
        if p.lower() in proofs:
            return f"APPLIED (proof #{proofs.index(p.lower())}, no signature line)"
        return "NOT loaded"
    ss, sz = pos("lo_probe_k_ßa"), pos("lo_probe_k_sz")
    print(f"  sharp s  : {'before' if ss is not None and sz is not None and ss < sz else 'after' if ss is not None and sz is not None else 'n/a'} "
          f"lo_probe_k_sz  (ßa pos {ss}, sz pos {sz})")
    for p in ("lo_probe_d_reqmiss", "lo_probe_d_optmiss", "lo_probe_d_cyc1", "lo_probe_d_cyc2",
              "lo_probe_d_dup1", "lo_probe_d_dup2", "lo_probe_d_disabled", "lo_probe_d_disdep",
              "lo_probe_d_profon", "lo_probe_d_profnoattr",
              "lo_probe_d_optdis", "lo_probe_d_profoff", "lo_probe_d_optprofoff",
              "lo_probe_d_reqprofoff", *CHAIN, "lo_probe_x_e", "lo_probe_x_f"):
        if p.lower() not in built:
            continue
        where = f" [{root_of(p)} root]" if p.startswith("lo_probe_x_") else ""
        print(f"  {p:22s}: {state(p)}{where}")
    dlc = "NO_MATCH" if errors.get("a_lo_probe_dlc") else (
        "OK" if "a_lo_probe_dlc" in loaded else "NOT LOADED")
    print(f"  a_lo_probe_dlc (DLC before mods?): {dlc}")
    print(f"  cross-root layout: {classify_roots(loaded, errors)}   "
          f"(chain NO_MATCH: {[p for p in CHAIN if errors.get(p)]})")
    with log.open(encoding="utf-8", errors="replace") as fh:
        odd = [ln.rstrip() for ln in fh if "lo_probe" in ln.lower()
               and not SIG.search(ln) and not NOMATCH.search(ln)]
    print(f"  probe lines neither pattern recognised: {len(odd)}")
    for ln in odd[:15]:
        print(f"    {ln[:220]}")
    print("\nPASS" if not fails else f"\n{fails} prediction(s) FAILED")
    return 0 if not fails else 1


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    apply = "--apply" in rest
    rest = [a for a in rest if a != "--apply"]
    only = None
    if "--only" in rest:
        k = rest.index("--only")
        if k + 1 >= len(rest):
            print("--only needs a folder prefix, e.g. --only lo_probe_x_", file=sys.stderr)
            return 2
        only = rest[k + 1]
        rest = rest[:k] + rest[k + 2:]
    if cmd == "build" and rest:
        try:
            build(Path(rest[0]), only)
        except ProbeRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        return 0
    if cmd == "deploy" and rest:
        return deploy(Path(rest[0]), apply)
    if cmd == "remove":
        return remove(apply)
    if cmd == "profile-entries":
        # Printed, never written: the profile content.xml is the user's, and its edit is a
        # confirmed, backed-up, hand-reverted step (CLAUDE.md "Requires user confirmation").
        print('  <extension id="lo_probe_d_profon" enabled="true"/>')
        print('  <extension id="lo_probe_d_profnoattr"/>')
        print('  <extension id="lo_probe_d_profoff" enabled="false"/>')
        return 0
    if cmd == "score" and rest:
        log = Path(rest[1]) if len(rest) > 1 else _paths.debug_log()
        if log is None or not log.is_file():
            print(f"cannot run: no debug log ({log})", file=sys.stderr)
            return 2
        return score(Path(rest[0]), log)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
