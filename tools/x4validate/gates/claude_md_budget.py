#!/usr/bin/env python
"""CLAUDE.md may not grow silently. A per-file RECORDED CEILING, lowered only by hand.

WHAT IT IS, AND WHAT IT IS NOT. Each file's size is recorded with `--record`, and a
run fails when a file exceeds ITS recorded size. It is NOT a ratchet: it never
tightens by itself. Shrink a file and it may regrow up to the recorded size unseen
until someone re-records -- so after a deliberate shrink, run `--record` to lock the
smaller size in. (USER DECISION 2026-09-25, AUDIT-2026-09-24 GT-6: it stays a
manual-`--record` ceiling; the docs that called it a self-tightening ratchet were
wrong and are corrected here.)

WHY RECORDED PER FILE, NOT ONE NUMBER. `CLAUDE.md` is loaded into every session, so making it smaller is a
standing objective rather than a one-time cleanup (user-set 2026-09-12). "Under
40,000 characters" is a threshold that **cannot go red in the way that matters**:
it reports PASS on a file full of content that has a better home, and being under
budget is not evidence that anything in the file earns its place.

THE WORKED EXAMPLE, MEASURED 2026-09-12. The two CLAUDE.md files on this machine
moved in OPPOSITE directions and nobody noticed for ten days:

    game root (this workspace's)   154,425 -> 38,056 chars   (-75%, deliberate)
    the SHIPPED one (repo root)     18,420 -> 70,118 chars   (+3.8x, in ONE commit)

That commit is `8289d5a` "promote the working principles and merge the knowledgebase
head": +624 lines, unannounced. **This gate would not have blocked it. It would have
made it name what it displaced.** That is the whole design.

⚠ COUNT CONTENT, NOT THE CHECKOUT'S LINE ENDINGS. Characters come from the bytes
(never `wc -c`: 332 multi-byte glyphs here), and every CRLF counts as ONE newline.
Both halves were paid for. Mixing conventions is lax by the LINE COUNT: a baseline
taken from a CRLF file and a measurement through `read_text()` differ by exactly the
file's CRLF pairs (38,056 vs 37,460 on the real file, its 596). The first fix counted
the raw CR instead, and MEASURED 2026-09-13 that flipped the verdict on a checkout: a
fast-forward rewrote the shipped CLAUDE.md as CRLF and this gate reported GREW +872,
on a file whose committed content was 52 characters SMALLER than its baseline --
924 lines, 924 CRs, one phantom finding (F120). How a checkout stores a newline is
not content.

⚠ PATHS COME FROM `__file__`, NOT FROM `$X4_TOOLKIT`. MEASURED 2026-09-12: that
variable had two different live values on one machine at the same moment -- a
running session had the pre-switch value while a new shell had the post-switch one.
A gate keyed to it would resolve differently for two people simultaneously.

THE BASELINE IS LOCAL AND GITIGNORED. A committed one would be wrong for every
other clone and would fail their first run -- `toolkit_usage.py`'s reasoning, and
`perf_guard`'s before it. No baseline is reported as "drift is NOT being checked",
never as a pass.

THE SHIPPED FILE ALSO HAS A COMMITTED FLOOR (issue #21, 4.3). The local baseline meant
nothing automated ever ran this gate: the shipped CLAUDE.md grew 29,230 -> 30,657 across
the 4.1.0 work unflagged. The repo-root CLAUDE.md is the same bytes in every clone, so ITS
floor is committed, in `claude-md-floor.json` beside this gate, counted the same way.
`--shipped` checks that one file against it and needs no game root: it runs in the
maintainers' pre-push gate, in CI's Fast checks and as a stage of the release gates.
Still a hand-lowered ceiling: a shrink never lowers it; `--shipped --record` does, and
the commit that does it shows the change.

Run:  uv run python gates/claude_md_budget.py [--record [LABEL]]
      uv run python gates/claude_md_budget.py --shipped [--record]
      `--record LABEL` re-records ONE file (a bare --record re-records every file, which
      accepts any unreviewed growth in the others).
Exit: 0 within baseline - 1 a file grew, named with its overage - 2 nothing could
      be measured (a budget that passes having measured nothing is the defect), or the
      floor/baseline could not be read, or --record named no measured file.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from x4validate import _paths  # noqa: E402

#: `<repo>/tools/x4validate/gates/this.py` -> `<repo>`. From __file__ deliberately.
REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_ROOT = Path(__file__).resolve().parents[1]

BASELINE = TOOL_ROOT / ".claude-md-budget-baseline.json"
#: COMMITTED: the shipped file's floor (issue #21). Lowered only by `--shipped --record`.
FLOOR = TOOL_ROOT / "claude-md-floor.json"
SHIPPED = REPO_ROOT / "CLAUDE.md"
SHIPPED_LABEL = "shipped (repo root)"
GAME_LABEL = "game root"

#: Written into every baseline and required on read. A baseline recorded before F120 counted
#: raw CRs, so on a CRLF checkout it sits above the content count by the file's line count
#: and would let that much growth through, silently (review, 2026-09-14).
COUNTING = "content: each CRLF counts as one newline (F120)"

#: A backstop only. The RECORDED per-file size is the operative check -- see the docstring.
#: chr() rather than escapes: this file's history includes a heredoc eating a backslash.
_CRLF, _LF = chr(13) + chr(10), chr(10)
HARD_CEILING = 40_000


class Unmeasurable(Exception):
    """A file that could not be read, so no verdict about it is admissible."""


@dataclass(frozen=True)
class Violation:
    name: str
    measured: int
    baseline: int

    @property
    def over(self) -> int:
        return self.measured - self.baseline


def char_count(path: Path) -> int:
    """Characters of CONTENT: each newline counts once, however the checkout stored it.
    See the module docstring for why both halves matter."""
    try:
        return len(Path(path).read_bytes().decode("utf-8").replace(_CRLF, _LF))
    except (OSError, UnicodeDecodeError) as exc:
        raise Unmeasurable(f"{path}: {type(exc).__name__}: {exc}") from exc


def budget_files() -> list[tuple[str, Path]]:
    """(label, path) for every CLAUDE.md that resolves. Absent ones are omitted,
    never guessed at -- and resolving none is a refusal in main()."""
    out: list[tuple[str, Path]] = []
    shipped = SHIPPED
    if shipped.is_file():
        out.append((SHIPPED_LABEL, shipped))
    root = _paths.game_root()
    if root is not None:
        gm = Path(root) / "CLAUDE.md"
        if gm.is_file() and gm.resolve() != shipped.resolve():
            out.append((GAME_LABEL, gm))
    return out


def violations(measured: dict[str, int], baseline: dict[str, int]) -> list[Violation]:
    """Files that exceed their recorded floor.

    A file absent from the baseline is NOT a violation: first sight is not growth,
    and treating it as one would make adding a file impossible.
    """
    return [Violation(n, c, baseline[n])
            for n, c in sorted(measured.items())
            if n in baseline and c > baseline[n]]


def _load(path: Path | None = None) -> dict[str, int] | None:
    path = BASELINE if path is None else path
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # Not swallowed: an unreadable baseline must not silently mean "nothing
        # recorded", which would read as a clean first run forever.
        raise Unmeasurable(f"baseline {path.name} is unreadable: {exc}") from exc
    method = data.pop("_counting", None) if isinstance(data, dict) else None
    if method != COUNTING:
        raise Unmeasurable(
            f"baseline {path.name} was recorded by a different counting method "
            f"({method or 'none recorded: raw CRs counted, before F120'}); "
            f"re-record with --record")
    try:
        return {str(k): int(v) for k, v in data.items()}
    except (TypeError, ValueError) as exc:
        raise Unmeasurable(f"baseline {path.name} holds a non-integer size: {exc}") from exc


def _write(path: Path, sizes: dict[str, int]) -> None:
    path.write_bytes((json.dumps({"_counting": COUNTING, **sizes}, indent=2, sort_keys=True)
                      + "\n").encode("utf-8"))


def _floor_change(name: str, was: int | None, now: int) -> None:
    if was is None:
        print(f"  {name}: new floor {now}")
    elif was != now:
        print(f"  {name}: floor {was} -> {now} ({now - was:+d})")
    else:
        print(f"  {name}: floor {now} unchanged")


def shipped_main(record: str | None) -> int:
    """`--shipped`: the repo-root CLAUDE.md against the COMMITTED floor. No game root, no local baseline."""
    print(f"CLAUDE.md BUDGET (shipped file, committed floor {FLOOR.name})")
    try:
        chars = char_count(SHIPPED)
    except Unmeasurable as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2
    print(f"  {SHIPPED_LABEL:22s} {chars:7d} chars  headroom to the {HARD_CEILING} backstop "
          f"{HARD_CEILING - chars:+7d}")
    if record is not None:
        if record not in ("", SHIPPED_LABEL):
            print(f"REFUSING: --shipped records only {SHIPPED_LABEL!r}, not {record!r}; nothing written",
                  file=sys.stderr)
            return 2
        try:
            previous = (_load(FLOOR) or {}).get(SHIPPED_LABEL)
        except Unmeasurable as exc:
            previous = None
            print(f"  the existing floor could not be read ({exc}); recording over it.")
        _floor_change(SHIPPED_LABEL, previous, chars)
        _write(FLOOR, {SHIPPED_LABEL: chars})
        print(f"  recorded to {FLOOR.name}: commit it, and say in the commit why the floor moved")
        return 0
    try:
        floor = _load(FLOOR)
    except Unmeasurable as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2
    if floor is None or SHIPPED_LABEL not in floor:
        print(f"REFUSING: no committed floor for {SHIPPED_LABEL!r} in {FLOOR.name}; growth is NOT being "
              "checked", file=sys.stderr)
        return 2
    limit = floor[SHIPPED_LABEL]
    if chars > limit:
        print(f"GREW: {SHIPPED_LABEL} {limit} -> {chars} (+{chars - limit} chars)", file=sys.stderr)
        print(f"      Name what this displaces, or remove {chars - limit} chars elsewhere. Raise the "
              "committed floor (--shipped --record) only when the growth is a decision.", file=sys.stderr)
        return 1
    under = f"; {limit - chars} under it (lower it by hand with --shipped --record)" if chars < limit else ""
    print(f"  within its committed floor {limit}{under}.")
    return 0


#: `--record` with no label: every measured file.
ALL = ""


def main(record: bool = False, argv: list[str] | None = None) -> int:
    if argv is None:
        # Called as a function (the tests) with no argv: the old contract, `--record` anywhere in sys.argv.
        label = ALL if (record or "--record" in sys.argv) else None
        shipped = False
    else:
        ap = argparse.ArgumentParser(prog="claude_md_budget", description="CLAUDE.md may not grow silently.")
        ap.add_argument("--record", nargs="?", const=ALL, default=None, metavar="LABEL",
                        help="record the current size as the floor: of every file, or of the one named LABEL "
                             f"(e.g. {SHIPPED_LABEL!r}, {GAME_LABEL!r})")
        ap.add_argument("--shipped", action="store_true",
                        help=f"check only the repo-root CLAUDE.md against the committed {FLOOR.name} "
                             "(needs no game root); with --record, lower or raise that committed floor")
        a = ap.parse_args(argv)
        label = ALL if (record and a.record is None) else a.record
        shipped = a.shipped
    if shipped:
        return shipped_main(label)
    record = label is not None
    files = budget_files()
    if not files:
        print("REFUSING: no CLAUDE.md resolved, so there is nothing to measure\n"
              "      expected one at the repo root and/or via the configured game "
              "root ($X4_GAME)", file=sys.stderr)
        return 2

    measured: dict[str, int] = {}
    print("CLAUDE.md BUDGET -- recorded per-file size, lowered only by --record "
          "(the 40,000 ceiling is a backstop, not the check)")
    try:
        for name, path in files:
            chars = char_count(path)
            measured[name] = chars
            size = path.stat().st_size
            print(f"  {name:22s} {chars:7d} chars  {size:7d} bytes  "
                  f"headroom {HARD_CEILING - chars:+7d}   {path}")
    except Unmeasurable as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2

    # BEFORE the load: --record is the remedy every baseline refusal names, so it must
    # work over a baseline that cannot be read or was counted another way (review of
    # the F120 follow-up, 2026-09-14: it refused with 're-record with --record').
    if record:
        # NAME what is being displaced. Every baseline refusal tells the operator to
        # "re-record with --record", and this wrote today's sizes over yesterday's with
        # no before/after -- so a machine carrying an old baseline could accept thousands
        # of characters of growth by doing exactly what the error message said, which is
        # the one outcome this budget exists to prevent (review, 2026-09-20). An
        # unreadable baseline still records: that is what keeps --record a usable remedy.
        if label != ALL and label not in measured:
            print(f"REFUSING: --record {label!r} names no measured file (measured: "
                  f"{', '.join(sorted(measured))}); nothing written", file=sys.stderr)
            return 2
        try:
            previous = _load()
        except Unmeasurable as exc:
            if label != ALL:
                # One label into a baseline whose other entries cannot be read would DROP them.
                print(f"REFUSING: the existing baseline could not be read ({exc}); --record {label!r} "
                      "would drop its other entries. A bare --record re-records every file.", file=sys.stderr)
                return 2
            previous = None
            print(f"  the existing baseline could not be read ({exc}); recording over it.")
        if label == ALL:
            new = dict(measured)
        else:
            new = {**(previous or {}), label: measured[label]}
        for _name in sorted(set(new) & set(measured)):
            if label == ALL or _name == label:
                _floor_change(_name, (previous or {}).get(_name), new[_name])
        _write(BASELINE, new)
        print(f"  recorded {len(measured) if label == ALL else 1} floor(s) to {BASELINE.name}")
        return 0

    try:
        baseline = _load()
    except Unmeasurable as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2

    if baseline is None:
        # rc 2, never 0: run-gates.sh buckets 0 as `ok` and discards stdout, so a
        # budget that has measured nothing would read as a passing one in the summary
        # on every fresh clone forever. perf_guard returns 2 in the same state.
        print(f"  no baseline at {BASELINE.name} -- run with --record to create one.")
        print("  drift is NOT being checked.", file=sys.stderr)
        return 2

    # NAME the population that is not being checked. violations() skips a file with no
    # baseline entry (first sight is not growth), which silently narrowed the check: with
    # X4_GAME unset, or a baseline recorded for different files, it passed having compared
    # nothing (review, 2026-09-13).
    for n in sorted(set(measured) - set(baseline)):
        print(f"  NOT CHECKED  {n}: no baseline entry (run --record to start checking it)")
    for n in sorted(set(baseline) - set(measured)):
        print(f"  NOT RESOLVED {n}: in the baseline but not found on this machine now")
    if not set(measured) & set(baseline):
        print("REFUSING: no measured file has a baseline entry, so nothing was compared.",
              file=sys.stderr)
        return 2
    bad = violations(measured, baseline)
    over_ceiling = [n for n, c in sorted(measured.items()) if c > HARD_CEILING]
    for n in over_ceiling:
        print(f"  NOTE  {n} is over the {HARD_CEILING} backstop "
              f"({measured[n]}). Pinned, not condoned.")
    if bad:
        print("", file=sys.stderr)
        for v in bad:
            print(f"GREW: {v.name} {v.baseline} -> {v.measured} (+{v.over} chars)",
                  file=sys.stderr)
            print(f"      Name what this displaces, or remove {v.over} chars "
                  f"elsewhere. Re-baseline with --record only when the growth is "
                  f"a decision.", file=sys.stderr)
        return 1
    print(f"  all {len(measured)} file(s) within their recorded floor.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(argv=sys.argv[1:]))
