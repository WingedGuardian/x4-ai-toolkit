r"""Inputs the gates need, resolved rather than hardcoded.

Until 2026-07-29 three of the four gates opened with a developer's own absolute
paths — the game install and a personal mod-workspace directory, hostname and
username included. That is how a personal path survives into a public repo, and
this docstring deliberately does not quote them, so the pre-push grep for such
paths stays clean rather than learning to ignore a known hit. The note in
`gates/README.md` claiming they had already been parameterised was simply wrong,
which is worse, because a false "this is clean" is what stops the next check.

Everything here resolves through `x4validate._paths` (the same layered
env → `x4-paths.env` → fallback chain the CLI uses), with one addition:

**`$X4_ORACLE_LOG`** — a *captured* `debug.txt` snapshot to measure against. It has
to be pinned rather than live, or the denominator moves between game sessions and
the 234/234 result stops meaning anything. It is deliberately NOT committed and
never will be: a real `debug.txt` names the mods you run, your filesystem layout
and your play session. Anyone reproducing these numbers supplies their own.

A missing input is a **SKIP (exit 2)**, never an empty run that prints like a pass.
That is the same contract `Report.skipped` enforces one level down.
"""

from __future__ import annotations


import sys
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from x4validate import _paths  # noqa: E402


def sandbox_copy(src: Path, dest: Path) -> Path:
    """Copy a protected file into a throwaway location AND make it writable.

    `shutil.copy2` preserves permission bits, so copying a file that
    `scripts/x4lock.py` has marked read-only produces a read-only SANDBOX -- and the
    gate then fails on its own scratch copy, which protects nothing and looks exactly
    like the tool being broken. MEASURED 2026-09-04: `registry_provenance` went red
    that way within minutes of the registry being locked for the first time.

    A sandbox exists to be written. The protection belongs to the original, and the
    original is not what is being handed to the caller here.
    """
    import shutil
    import stat as _stat

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    try:
        dest.chmod(dest.stat().st_mode | _stat.S_IWRITE)
    except OSError as exc:
        skip("could not make the sandbox copy of %s writable (%s)" % (src.name, exc),
             "check the destination filesystem; a read-only sandbox would fail the "
             "gate for a reason that has nothing to do with what it tests")
    return dest


def skip(what: str, how: str) -> NoReturn:
    print(f"SKIP: {what}\n      {how}", file=sys.stderr)
    raise SystemExit(2)


def extensions() -> Path:
    """The installed extension set — the population every gate measures over."""
    p = _paths.game_extensions()
    if p is None or not p.is_dir():
        skip(f"no installed extension set (resolved to {p or 'nothing'})",
             "set $X4_GAME or $X4_EXTENSIONS (see x4-paths.env), "
             "then check with `x4validate --paths`")
    return p


def mods_dir() -> Path:
    """Where the mod source folders live, one per mod."""
    p = _paths.mods()
    if p is None or not p.is_dir():
        skip(f"no mod source directory (resolved to {p or 'nothing'})",
             "set $X4_MODS (see x4-paths.env)")
    return p


def reference() -> Path:
    """The unpacked base+DLC tree the audits compare mod patches against."""
    p = _paths.reference()
    if p is None or not p.is_dir():
        skip(f"no reference tree (resolved to {p or 'nothing'})",
             "set $X4_REFERENCE (see x4-paths.env), then check with "
             "`x4validate --paths`")
    return p


def effective_db() -> Path:
    """The x4effective store. Built, not shipped — a missing one is a SKIP."""
    from x4validate import _effective
    # ONE door. This used to open-code the resolution while `_effective` read
    # os.environ at import, so a gate and the CLI could disagree about which
    # store was configured. `effective_db()` is now the only answer to this.
    path = _effective.effective_db()
    if path is None or not Path(path).is_file():
        skip(f"no effective store (resolved to {path or 'nothing'})",
             "run `x4effective build` first, or set $X4_EFFECTIVE_DB")
    return Path(path)


def stale_store_refusal(db: Path, who: str) -> int | None:
    """rc 2 (CANNOT) when the effective store no longer describes the installed world,
    else None. ONE implementation, because four gates audited whatever older engine or
    modlist built the store and reported the result as current (AUDIT-2026-09-24 GT-6):
    a regression in the merge stayed invisible until someone happened to rebuild, and a
    stale store's disagreements read as tool defects. `claims_audit` already refused; it
    returned 5, which run-gates.sh counts as FAIL rather than could-not-run (GT-7).

    UNKNOWN freshness (no fingerprint) refuses too -- absent is never fresh.
    """
    import sqlite3

    from x4validate import _effective
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        print(f"REFUSING ({who}): could not open the effective store {db}: {exc}",
              file=sys.stderr)
        return 2
    try:
        verdict = _effective.store_freshness(con)
    finally:
        con.close()
    if verdict.fresh:
        return None
    print(verdict.banner("the effective store"), file=sys.stderr)
    print(f"REFUSING ({who}): the store describes a world that has moved on, so any "
          "result here would describe that world, not this one. Rebuild it "
          "(`x4effective build`) and re-run.", file=sys.stderr)
    return 2


def nothing_checked(who: str, what: str) -> int:
    """rc 2 for a run whose denominator is ZERO. Several gates printed a table of zeroes
    and exited 0 -- 0 examined and 0 wrong is not agreement, it is a non-answer
    (AUDIT-2026-09-24 GT-3). Returned, not raised, so callers keep their own flow."""
    print(f"REFUSING ({who}): {what} -- 0 items were examined, so there is nothing to "
          "pass. A NON-ANSWER, not a clean run.", file=sys.stderr)
    return 2


def oracle_log() -> Path:
    """A pinned debug.txt capture. Never committed — see the module docstring.

    Resolved through `_paths`' own layers, not `os.environ` alone: the config file
    is where a user would naturally put this, and a gate that only honoured a real
    exported variable would silently ignore it and report SKIP with the setting
    sitting right there in `x4-paths.env`.
    """
    raw = _paths._resolve(lambda layer: _paths._pick(layer, "X4_ORACLE_LOG"))
    if not raw:
        skip("no captured engine log ($X4_ORACLE_LOG is unset)",
             "point it at a debug.txt saved AFTER a full game load with your modlist "
             "active, e.g. X4_ORACLE_LOG=/path/to/debug-YYYY-MM-DD.txt. Use a copy, not "
             "the live file — the log must not change between runs or the denominator "
             "moves. It is never committed: a real debug.txt names your mods and paths.")
    p = Path(raw)
    if not p.is_file():
        skip(f"$X4_ORACLE_LOG points at nothing readable: {p}",
             "check the path; a captured log, not the live debug.txt")
    return p


def registry_file() -> Path:
    """The mod registry (`modlist.yaml`), resolved the way the package does.

    Exists so a gate that needs the registry copies the REAL one into a sandbox
    rather than either hardcoding a path or writing to the live file. `qa_sweep`
    used to run `x4modlist dashboard` against the live registry, regenerating
    the user's WORKLIST.md on every sweep.
    """
    from x4validate import _registry
    p = _registry.DEFAULT_REGISTRY
    if p is None:
        skip("no registry configured", "set $X4_MODS or $X4_REGISTRY")
    p = _registry._registry_file(Path(p))
    if not p.is_file():
        skip(f"no registry file at {p}", "run `x4modlist ingest` first")
    return p


# ---------------------------------------------------------------------------------------
# DOES A CAPTURED LOG DESCRIBE THE CURRENT MODLIST?  (FX-H10, 2026-10-12)
#
# One decision, shared by every gate that compares an engine log with what is installed
# NOW (`load_order_oracle`, `oracle`, `oracle_reverse`). Until then only
# `load_order_oracle` asked it; against a 2026-09-06 log the other two returned rc 1 --
# a FAIL -- on the same log that one refused. A log of a different modlist can neither
# convict the code nor clear it, so each gate refuses (rc 2) with the reasons below.
#
# Lives HERE, not in a new gates module, because every one of those gates already
# imports `_env`, and `_env.py` is the one file under gates/ that `run-gates.sh` and
# `test_every_gate_can_fail` already name as a helper, not a gate. A new module would
# have to be exempted by name in both, and missing either makes it a "gate" that can
# only ever refuse.
#
# Four signals, because each alone misses a case: a manifest mtime is preserved when a
# mod is copied in; the profile's enable/disable decisions live in its own content.xml;
# a removed folder shows only as a logged name; an added one only as a file class it
# ships that the engine never checked for it.
# ---------------------------------------------------------------------------------------
import collections as _collections  # noqa: E402
from dataclasses import dataclass as _dataclass, field as _field  # noqa: E402

_BS = "\\"
SIG_MARKER = "Failed to verify the file signature for file"
SIG_PREFIX = "." + _BS + "extensions" + _BS


def parse_signature_sequences(lines) -> dict[str, list[str]]:
    r"""{relative path (lowercase, '/') : [folder (lowercase), in first-checked order]}.

    Read from the engine's signature-check lines,
    `Failed to verify the file signature for file '.\extensions\<folder>\<rel>'`.
    Nested paths (`extensions\a\extensions\b\...`) are a mod patching another mod's
    file; the folder that OWNS the checked copy is `a`, and the class key keeps the
    nested remainder, so they never mix with the plain class of the same name.
    """
    seqs: dict[str, list[str]] = _collections.defaultdict(list)
    for line in lines:
        if SIG_MARKER not in line:
            continue
        a = line.find(SIG_PREFIX)
        if a < 0:
            continue
        rest = line[a + len(SIG_PREFIX):]
        q = rest.find("'")
        if q >= 0:
            rest = rest[:q]
        folder, sep, rel = rest.partition(_BS)
        if not sep or not folder or not rel:
            continue
        folder = folder.lower()
        rel = rel.lower().replace(_BS, "/")
        if folder not in seqs[rel]:
            seqs[rel].append(folder)
    return dict(seqs)


def dlc_folders() -> frozenset[str]:
    r"""Lowercased folder names of the configured DLC (`_merge.Config().dlc_dirs()`:
    unpacked in the reference tree, plus packed-only in the live install).

    A DLC is base-game content, never a mod: it is in no `_registry.mods(...)` scope and
    in no computed order. The engine still signature-checks the odd file under
    `.\extensions\ego_dlc_*\` (MEASURED 2026-10-12: 1 such line of 28,039, a Terran
    texture, error 13), which without this read as a REMOVED folder and refused a
    current log. Unconfigured reference -> empty set: a DLC line then counts as not
    installed and the gate REFUSES (fails closed), and it says why."""
    from x4validate import _merge
    try:
        names = frozenset(p.name.lower() for p in _merge.Config().dlc_dirs())
    except _paths.Unconfigured as exc:
        print(f"note: no DLC set to exempt ({exc})", file=sys.stderr)
        return frozenset()
    if not names:
        # Same consequence as unconfigured, so the same disclosure (release review P,
        # finding 3): a reference that is missing or holds no ego_dlc_* resolves to
        # nothing, and every DLC signature line then reads as a removed folder.
        print("note: no DLC set to exempt (the configured reference and the install "
              "resolve to no DLC folder)", file=sys.stderr)
    return names


def drop_dlc(seqs: dict[str, list[str]], dlc: frozenset[str]) -> int:
    """Remove DLC folders from every sequence IN PLACE; return the entries removed.

    Dropped, not kept as fixed-position entries: a load-order score compares only
    folders in the computed order, which never holds a DLC, so a DLC entry contributes
    no pair either way -- its only effects were the staleness check and the
    unknown-folder count."""
    n = 0
    for rel in list(seqs):
        kept = [f for f in seqs[rel] if f not in dlc]
        n += len(seqs[rel]) - len(kept)
        if kept:
            seqs[rel] = kept
        else:
            del seqs[rel]
    return n


def newest_manifest(mods: list[dict]) -> tuple[float, str]:
    newest, who = 0.0, ""
    for m in mods:
        cx = Path(m["path"]) / "content.xml"
        try:
            t = cx.stat().st_mtime
        except OSError:
            # FAIL CLOSED: a manifest whose age cannot be read cannot be shown to predate
            # the log, so treat it as newer -- the gate then refuses to judge.
            return float("inf"), f"{m['folder']} (manifest unreadable)"
        if t > newest:
            newest, who = t, m["folder"]
    return newest, who


def mtime_or_none(p: Path | None) -> float | None:
    try:
        return p.stat().st_mtime if p is not None else None
    except OSError:
        # silent-ok: no profile content.xml (none configured, or absent) = no profile
        # signal; the other three modlist signals still decide, and the header names the log.
        return None


def evidence_unlogged(mods: list[dict], logged: set[str],
                      classes: set[str]) -> dict[str, str]:
    """{folder: one file class it ships} for every GAME-ROOT mod in *mods* the log never
    names although it ships a file the log shows the engine checking for other mods.

    That is EVIDENCE the logged launch did not have this mod (the engine checks every
    copy of a file class it reads), as opposed to plain absence: a mod shipping no
    logged class is never checked, so its absence says nothing. Game root only: the
    signature line names the folder under SIG_PREFIX, the game root; a mod in another
    extensions root is not read out of it here, so it is not held against the log.
    """
    out: dict[str, str] = {}
    for m in mods:
        folder = m["folder"].lower()
        if folder in logged or m.get("root_rank", 0) != 0 or not m.get("path"):
            continue
        root = Path(m["path"])
        # reference-scope-ok: a MOD folder, not the reference tree, and loose-only on purpose:
        # this collects EVIDENCE against the log, so a class the mod ships only packed is
        # simply not counted -- a packed-only mod is never held against the log (conservative).
        for p in root.rglob("*"):
            if p.is_file():
                rel = p.relative_to(root).as_posix().lower()
                if rel in classes:
                    out[folder] = rel
                    break
    return out


@_dataclass
class Staleness:
    """Why a log does not describe the current modlist; empty `reasons()` = it does."""
    aged: list = _field(default_factory=list)       # manifest / profile newer than the log
    gone: list = _field(default_factory=list)       # logged, neither known nor a DLC
    unlogged: dict = _field(default_factory=dict)   # {folder: a logged class it ships}
    logged: set = _field(default_factory=set)       # every folder the log names (no DLC)

    def reasons(self) -> list[str]:
        out = list(self.aged)
        if self.gone:
            out.append(f"{len(self.gone)} logged folder(s) are not installed now: "
                       f"{', '.join(self.gone)}")
        if self.unlogged:
            out.append(f"{len(self.unlogged)} active mod(s) ship a file the engine checked "
                       "for other mods but never for them: "
                       + ", ".join(f"{f} ({rel})" for f, rel in sorted(self.unlogged.items())))
        return out


STALE_ADVICE = ("Launch the game once with the current modlist (or point $X4_ORACLE_LOG "
                "at a log that does) and re-run.")


def log_staleness(t_log: float, seqs: dict[str, list[str]], *, scope: list[dict],
                  known: set[str], t_profile: float | None,
                  also_aged: list[dict] = ()) -> Staleness:
    """The core decision, from inputs the caller has already gathered.

    *seqs*: signature sequences with DLC already dropped. *scope*: the mods whose
    manifest age and unlogged evidence count (a gate judging a subset passes that
    subset). *known*: every folder a logged name may legitimately be (active or
    installed, lowercased) -- anything else is 'gone'. *t_profile*: the profile
    content.xml mtime, or None. *also_aged*: further mods whose manifest age counts but
    which are not in *scope* -- an installed mod the log names that is no longer active
    (its manifest disabled it, a dependency changed): it was in that launch, so its
    manifest changing since is the modlist changing (release review P, finding 1)."""
    st = Staleness(logged={f for seq in seqs.values() for f in seq})
    newest, who = newest_manifest([*scope, *also_aged])
    if newest > t_log:
        st.aged.append(f"a manifest changed after the log was written ({who})")
    if t_profile is not None and t_profile > t_log:
        st.aged.append("the profile content.xml (the enable/disable decisions) changed "
                       "after the log was written")
    st.gone = sorted(f for f in st.logged if f not in known)
    st.unlogged = evidence_unlogged(scope, st.logged, set(seqs))
    return st


def modlist_staleness(log: Path, scope: set[str] | None = None) -> Staleness:
    """Does *log* describe the current modlist? Gathers the inputs and decides.

    *scope*: lowercased folders whose manifests and unlogged evidence count; None = the
    whole active modlist. The profile and logged-folder checks are always global --
    neither can be attributed to one mod, and a removed folder's old load position is
    unknown."""
    from x4validate import _registry
    with log.open(encoding="utf-8", errors="replace") as fh:
        seqs = parse_signature_sequences(fh)
    drop_dlc(seqs, dlc_folders())
    active = _registry.mods("active")
    left_out = _registry.dropped_note(active)
    if left_out:
        # Disclosed (F139): an excluded mod is in no scope here; it can still never be
        # 'gone', because the installed set below names it.
        print(f"note: the modlist check covers the active set; NOT in it: {left_out}",
              file=sys.stderr)
    active = list(active)
    installed = {m["folder"].lower(): m for m in _registry.mods("installed")
                 if m.get("root_rank", 0) == 0}
    act = {m["folder"].lower() for m in active}
    known = act | set(installed)
    in_scope = active if scope is None else [m for m in active
                                             if m["folder"].lower() in scope]
    # Installed-but-inactive mods whose manifest age still counts: every one the log
    # names (it was in that launch), and -- for a scoped caller -- every one it judges.
    logged = {f for seq in seqs.values() for f in seq}
    also = [m for f, m in sorted(installed.items()) if f not in act
            and (f in logged or (scope is not None and f in scope))
            and (scope is None or f in scope)]
    return log_staleness(log.stat().st_mtime, seqs, scope=in_scope, known=known,
                         t_profile=mtime_or_none(_paths.profile_content()),
                         also_aged=also)
