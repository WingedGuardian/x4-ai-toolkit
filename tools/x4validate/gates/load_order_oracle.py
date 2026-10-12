r"""LOAD ORDER ORACLE — does `compute_load_order` agree with the order the ENGINE used?

WHY THIS EXISTS. `_loadorder.compute_load_order` decides the winner of every collision
between mods (x4compat, x4effective, x4stats, Tier B). Until 2026-09-24 nothing ever
compared it with the engine: every test and the #13 mutant compared the code with
itself. MEASURED that day on a 125-mod install, the order the code produced agreed with
the engine at 0 of 23 first-pass positions (AUDIT-2026-09-24 LO-1).

THE EVIDENCE IT READS. At startup and while loading, the engine checks the signature of
every extension file it opens, and an unsigned mod file logs

    [FileIO ] 0.00 File I/O: Failed to verify the file signature for file
              '.\extensions\<folder>\<relative path>' (error: 14)

For every relative path (a "file class" -- `libraries/wares.xml`, `t/0001-l044.xml`, ...)
the ORDER in which different mods' copies are checked is the order the engine walked the
extensions for that file. Each class shipped by two or more mods yields ordered pairs;
this gate counts the pairs the computed order INVERTS. It does not assume which rule the
engine uses -- it scores whatever `compute_load_order` returns.

SCOPE, stated so a pass is not read as more than it is:
  * Only mods that ship at least one unsigned file appear (in practice: every mod).
  * Only the relative order of mods that SHARE a file class is tested; two mods that
    never ship the same path are never compared. The denominator (pairs, classes,
    mods covered) is printed on every run.
  * The log describes the modlist AT THAT LAUNCH. Whether it still describes the current
    one is decided BEFORE any verdict, so a stale log can neither convict nor clear the
    code: the gate refuses (rc 2, naming the cause) when a mod manifest or the profile
    content.xml changed after the log was written, when a logged folder is no longer
    installed (e.g. a removed probe mod; a configured DLC folder is base game, never a
    mod, and is left out of the sequences first), or when an active game-root mod ships a file
    class the engine logged for other mods but never for it (it was not in that launch).
  * A mod that IS installed but that our active rule EXCLUDES, yet the engine logged, is
    a FINDING (rc 1): our model of what loads disagrees with the engine. It is judged
    only on a log no older than the manifests and profile and naming no removed folder
    (a dependency removed since the launch would explain the exclusion); otherwise it is
    listed in the refusal.

INPUT: `$X4_ORACLE_LOG` (a pinned capture) if set, else the live profile debug.txt.

Exit: 0 no inversions over >= 1 comparable class, on a log of the current modlist ·
1 inversions, or a logged mod our active rule excludes · 2 cannot judge (no log, nothing
comparable, or a log that does not describe the current modlist).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import _env  # also puts the package on sys.path

from x4validate import _loadorder, _paths, _registry

# The log parsing and the does-this-log-describe-the-modlist decision live in
# `_env` (FX-H10), shared with `oracle.py` and `oracle_reverse.py`. The names below
# are kept so tests and callers can pin them on this module.
MARKER = _env.SIG_MARKER
PREFIX = _env.SIG_PREFIX
parse_sequences = _env.parse_signature_sequences


@dataclass
class Score:
    classes: int = 0                  # classes with >= 2 comparable mods
    pairs: int = 0                    # ordered pairs compared
    inversions: int = 0
    mods_covered: set = field(default_factory=set)
    unknown_folders: set = field(default_factory=set)   # in the log, not in the order
    bad: list = field(default_factory=list)             # (class, n, inversions, seq)


def score(order: list[str], seqs: dict[str, list[str]]) -> Score:
    pos = {f.lower(): i for i, f in enumerate(order)}
    s = Score()
    for rel, seq in seqs.items():
        s.unknown_folders.update(f for f in seq if f not in pos)
        known = [f for f in seq if f in pos]
        if len(known) < 2:
            continue
        s.classes += 1
        s.mods_covered.update(known)
        inv = 0
        for i in range(len(known)):
            for j in range(i + 1, len(known)):
                s.pairs += 1
                if pos[known[i]] > pos[known[j]]:
                    inv += 1
        if inv:
            s.inversions += inv
            s.bad.append((rel, len(known), inv, known))
    s.bad.sort(key=lambda b: -b[2])
    return s


def _dlc_folders() -> frozenset[str]:
    """The configured DLC folders -- see `_env.dlc_folders`."""
    return _env.dlc_folders()


_drop_dlc = _env.drop_dlc


def _log_path() -> Path | None:
    raw = _paths._resolve(lambda layer: _paths._pick(layer, "X4_ORACLE_LOG"))
    if raw:
        return Path(raw)
    return _paths.debug_log()


def _profile_content() -> Path | None:
    """The profile content.xml -- the enable/disable decisions -- or None."""
    return _paths.profile_content()


def main() -> int:
    log = _log_path()
    if log is None or not log.is_file():
        print(f"SKIP: no debug log to judge against (resolved to {log or 'nothing'})\n"
              "      launch X4 once with the modlist active, or set $X4_ORACLE_LOG",
              file=sys.stderr)
        return 2
    mods = _registry.mods("active")
    left_out = _registry.dropped_note(mods)
    if left_out:
        # A mod the engine leaves out is in no computed order; if the log shows it
        # anyway, that is a finding about OUR exclusion rule -- judged below (F139).
        print(f"NOT in the computed order (our model: the engine does not load it): "
              f"{left_out}")
    if not mods:
        print("SKIP: the active mod set is empty -- nothing to order", file=sys.stderr)
        return 2
    order = _loadorder.compute_load_order(mods)
    with log.open(encoding="utf-8", errors="replace") as fh:
        seqs = parse_sequences(fh)
    n_dlc = _drop_dlc(seqs, _dlc_folders())
    s = score(order, seqs)

    print(f"log: {log}")
    if n_dlc:
        print(f"DLC entries left out of the sequences (base game, never a mod): {n_dlc}")
    print(f"active mods: {len(order)}   in the log: {len(s.mods_covered)} compared "
          f"(+{len(s.unknown_folders)} logged folder(s) not in the active set)")
    print(f"file classes compared: {s.classes}   ordered pairs: {s.pairs}   "
          f"inverted by compute_load_order: {s.inversions}")
    for rel, n, inv, seq in s.bad[:10]:
        print(f"  {rel}  mods={n}  inverted pairs={inv}  engine order: {', '.join(seq[:8])}"
              + (" ..." if len(seq) > 8 else ""))

    # DOES THE LOG DESCRIBE THE CURRENT MODLIST? Decided BEFORE any verdict: a log of a
    # different modlist can neither convict the code nor clear it (release review
    # 2026-09-26 -- this used to be asked only after an inversion, so a mismatched log
    # with 0 inversions PASSED). Four signals, because each alone misses a case: a
    # manifest mtime is preserved when a mod is copied in; the profile's enable/disable
    # decisions live in its own content.xml; a removed folder shows only as a logged
    # name; an added one only as a class it ships that the engine never checked for it.
    active = {f.lower() for f in order}
    installed = {m["folder"].lower(): m for m in _registry.mods("installed")
                 if m.get("root_rank", 0) == 0}
    st = _env.log_staleness(log.stat().st_mtime, seqs, scope=mods,
                            known=active | set(installed),
                            t_profile=_env.mtime_or_none(_profile_content()))
    logged, aged, gone = st.logged, st.aged, st.gone
    excluded = sorted(f for f in logged - active if f in installed)
    mismatch = st.reasons()

    # A mod WE exclude that the engine LOGGED is our active rule disagreeing with the
    # engine -- a FINDING (rc 1), distinct from a folder that is simply gone. It needs a
    # log that is not older than the decisions (manifests, profile) and names no removed
    # folder, since a dependency removed since the launch would explain the exclusion.
    if excluded:
        why = _registry.left_out(mods)
        detail = "; ".join(f"{f} ({why.get(installed[f]['folder'], 'disabled by the '
                                            'profile or its manifest')})" for f in excluded)
        if not aged and not gone:
            print(f"FAIL: the engine signature-checked {len(excluded)} installed mod(s) our "
                  f"active rule EXCLUDES -- our model of what loads disagrees with the "
                  f"engine: {detail}")
            return 1
        mismatch.append(f"{len(excluded)} logged mod(s) our active rule excludes: {detail} "
                        "(a finding only against a log of the current modlist)")
    if mismatch:
        print("CANNOT JUDGE: the log does not describe the current modlist -- "
              + "; ".join(mismatch)
              + ". Launch the game once with the current modlist (or point $X4_ORACLE_LOG "
              "at a log that does) and re-run.", file=sys.stderr)
        return 2
    if s.classes == 0:
        print("SKIP: no file class is shipped by two or more active mods in this log, so "
              "nothing about the order can be judged", file=sys.stderr)
        return 2
    silent = sorted(active - logged)
    if silent:
        print(f"note: {len(silent)} active mod(s) never appear in the log and ship no file "
              "class it logged (nothing unsigned was checked for them); they are simply "
              "not compared")
    if s.inversions == 0:
        print("PASS: the computed order agrees with every engine-checked pair above")
        return 0
    print(f"FAIL: {s.inversions} of {s.pairs} engine-ordered pairs are inverted by "
          "compute_load_order -- every collision winner between those mods is suspect")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
