r"""x4xref: a cross-index of MD / aiscript actions, events, and cue edges.

Behavioral mod interactions (two mods reacting to the same event, one disabling an
engine feature another relies on) are invisible to a file/node collision check and
painful to trace by grep: the decisive tokens often share no keywords with the
concept (ATD suppresses ejection via ``set_emergency_eject_active`` +
``set_object_min_hull`` — neither contains "eject" or "death"). This index answers
the three questions that actually matter for interaction analysis in one lookup:

- ``who-calls <action>``  — every place an MD/aiscript action element appears,
  cue-edge actions (``signal_cue``, ``cancel_cue``, ...) included
- ``who-listens <event>`` — every cue whose condition fires on an ``event_*``
- ``cue <name>``          — where a cue is defined, signalled, and cancelled

built over base + DLC + every installed mod (packed or loose). Structural containers,
control flow (``do_if``/``do_else``/``do_elseif``/``do_all``/``do_while``/``do_for_each``
and the generic ``check_all``/``check_any``/``check_value``/``check_age``), and
variable/debug plumbing are excluded so the index stays about behavior, not
bookkeeping -- the exact list is ``_SKIP_TAGS``. A SPECIFIC condition such as
``check_object`` names what it tests, so it IS indexed, as an action.
"""

from __future__ import annotations

import csv
import hashlib
import io
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from x4validate import _paths, _cat, _effective, _merge, _mutation, _registry, _scan, _freshness
from x4validate import __version__

# Excluded from the `action` index: structural containers, control flow, and
# variable/debug plumbing whose element TAG carries no behavioral meaning (the
# interesting part, if any, is an attribute we index separately or not at all).
_SKIP_TAGS = frozenset({
    "actions", "conditions", "cues", "library", "params", "param",
    "do_if", "do_else", "do_elseif", "do_all", "do_while", "do_for_each",
    "check_all", "check_any", "check_value", "check_age",
    "set_value", "remove_value", "append_to_list", "insert_in_list", "remove_from_list",
    "debug_text", "run_actions", "include_actions", "patch", "delay", "aiscript", "mdscript",
})
# Cue-edge actions: their `cue=` attribute names the target cue.
_CUE_EDGE_TAGS = frozenset({
    "signal_cue", "signal_cue_instantly", "cancel_cue", "reset_cue",
    "enable_cue", "disable_cue", "complete_cue",
})
_SCRIPT_DIRS = ("md/", "aiscripts/")


@dataclass(frozen=True)
class XrefRow:
    kind: str     # event | signal | cuedef | action
    name: str     # event tag / action tag / cue name
    source: str   # base | dlc:<name> | <mod folder>
    file: str     # virtual path
    cue: str      # enclosing cue (MD) or aiscript name
    line: int
    target: str = ""  # signal -> cue=; event -> object=/by= if present


def _walk(root: etree._Element, source: str, vpath: str, out: list[XrefRow]) -> None:
    """DFS the tree, emitting rows, carrying the nearest enclosing cue/script name."""
    script_name = root.get("name", "") if root.tag in ("mdscript", "aiscript") else ""

    def rec(el: etree._Element, cue: str) -> None:
        tag = el.tag
        if not isinstance(tag, str):
            return
        here_cue = cue
        if tag == "cue" and el.get("name"):
            here_cue = el.get("name")
            out.append(XrefRow("cuedef", here_cue, source, vpath, cue, el.sourceline or 0))
        elif tag.startswith("event_"):
            target = el.get("object") or el.get("by") or el.get("cue") or ""
            out.append(XrefRow("event", tag, source, vpath, cue, el.sourceline or 0, target))
        elif tag in _CUE_EDGE_TAGS:
            out.append(XrefRow("signal", tag, source, vpath, cue, el.sourceline or 0,
                               el.get("cue", "")))
        elif tag not in _SKIP_TAGS:
            out.append(XrefRow("action", tag, source, vpath, cue, el.sourceline or 0))
        for child in el:
            rec(child, here_cue)

    rec(root, script_name)


def _iter_source_files(base: Path, source: str, unreadable: list | None = None):
    """Yield (vpath, parse_root) for md/ + aiscripts/ under a loose directory.

    `under()` narrows the walk to those two subtrees, so pointing this at the
    reference root does not rglob the whole ~13k-file base game.
    """
    yield from _scan.iter_mod_xml(base, _scan.under(*_SCRIPT_DIRS), unreadable)


def _iter_mod_files(mod_dir: Path, unreadable: list | None = None):
    """Yield (vpath, root) for a mod's md/aiscripts — packed (via _cat) and loose."""
    yield from _scan.iter_mod_xml(mod_dir, _scan.under(*_SCRIPT_DIRS), unreadable)


def index_roots(ext_dir: Path) -> list[Path]:
    """The install roots an index built from *ext_dir* covers: the named root first, then
    every other configured root (profile extensions, Steam workshop).

    ONE definition, used by `build_index` AND by the freshness stamp and check
    (AUDIT-2026-09-24 FR-3). The index walked all of these while the stamp covered only
    the named one, so a mod added or edited in the profile or workshop root left the
    index FRESH -- and x4xref exists to make NEGATIVES admissible.
    """
    # An UNCONFIGURED root (None on a machine with no X4) is not a root: including it made
    # every freshness stamp and check call Path(None) and crash (the cold-clone run caught it).
    roots = [ext_dir] if ext_dir is not None else []
    try:
        for extra in _registry.default_installed_dirs():
            if extra not in roots:
                roots.append(extra)
    except Exception:  # silent-ok: a root set that cannot be derived must not
        # empty the index. The NAMED root is still walked, and the caller's own
        # `unreadable` channel still reports whatever that walk could not read -- so
        # the failure mode is a NARROWER population, never a silent zero. Recording it
        # here would fire on every machine with no profile extensions dir, which is
        # the normal state.
        pass
    return roots


def build_index(reference: Path, ext_dir: Path,
                unreadable: list | None = None,
                dlc_dirs: list[Path] | None = None) -> list[XrefRow]:
    """Index base + DLC + every installed mod's MD/aiscripts.

    *unreadable* collects files that would not parse. An index is the
    denominator behind every "nobody references X" answer this tool gives, so a
    file silently missing from it turns a negative into a guess.
    """
    rows: list[XrefRow] = []

    # Base game (reference root, excluding the DLC extensions subtree).
    for vpath, root in _iter_source_files(reference, "base", unreadable):
        _walk(root, "base", vpath, rows)
    # DLC. Ask Config, do NOT re-derive from `reference / "extensions"`: the two
    # mini-DLC are never unpacked into reference\ (their content lives in
    # ext_*.cat inside the game install), so a directory walk there misses them
    # entirely -- 13 md/aiscript files that indexed 0 rows, which silently breaks
    # every NEGATIVE this index exists to make admissible. `_scan.iter_mod_xml`
    # was already packed-aware; only the directory list was wrong.
    if dlc_dirs is None:
        dlc_dirs = _merge.Config(reference=reference).dlc_dirs()
    for dlc in sorted(dlc_dirs, key=lambda p: p.name):
        for vpath, root in _iter_source_files(dlc, f"dlc:{dlc.name}", unreadable):
            _walk(root, f"dlc:{dlc.name}", vpath, rows)
    # Installed mods.
    #
    # A MISSING extensions dir is a SETUP failure, not an empty modlist, and
    # the two used to be the same wordless `if`. `_registry.default_installed_dirs`
    # already draws that line in its own docstring — "0 installed mods"
    # must not read as "you have no mods" when the truth is "I looked in the
    # wrong place" — and this walk sat on the wrong side of it. Recorded
    # into `unreadable`, which is the channel every caller already renders, so
    # the state becomes visible instead of being skipped in silence.
    if not ext_dir.is_dir() and unreadable is not None:
        unreadable.append(str(ext_dir) + " (the extensions directory does not "
                          "exist, so NO installed mod was indexed at all)")
    # EVERY CONFIGURED ROOT, not just the one named. `_registry.default_installed_dirs`
    # returns up to THREE -- game-root extensions, the profile's extensions, and the
    # Steam workshop content dir -- and this walked only the one it was handed. x4xref
    # exists to make NEGATIVES admissible ("nobody calls X"), so a population missing a
    # whole configured root makes every negative a claim about part of the install
    # while reading as a claim about the install.
    #
    # MEASURED on this machine when the gap was found: the two populations are
    # IDENTICAL (the profile extensions dir is empty and no workshop dir exists), so
    # the index digest does not move here. That is the state in which such a gap
    # survives -- a recorded cost of zero is where a wrong denominator hides.
    roots = index_roots(ext_dir)
    scanned = [r for r in roots if r.is_dir()]
    # NO failure channel entry for an ABSENT candidate root. `default_installed_dirs`
    # returns CANDIDATES -- the profile's extensions dir and the Steam workshop dir
    # are routinely absent (X4 downloads subscribed mods straight into game-root
    # extensions, and this machine has no workshop dir at all), so their absence is a
    # normal state and not a read failure. Recording it filled `unreadable` on every
    # clean build, which a test correctly refused. The widening below IS the fix.
    seen_folders = set()
    for root_dir in scanned:
        for m in _registry.mods("installed", [root_dir]):
            # A mod present in two roots is ONE mod to the engine; index it once and
            # keep the first, which is the order default_installed_dirs declares.
            if m["folder"] in seen_folders:
                continue
            seen_folders.add(m["folder"])
            for vpath, root in _iter_mod_files(Path(m["path"]), unreadable):
                _walk(root, m["folder"], vpath, rows)
    return rows


# --- TSV persistence + queries ------------------------------------------------

_HEADER = ["kind", "name", "source", "file", "cue", "line", "target"]


def write_tsv(rows: list[XrefRow], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(_HEADER)
        for r in rows:
            w.writerow([r.kind, r.name, r.source, r.file, r.cue, r.line, r.target])
    return path


class IndexUnreadable(ValueError):
    """A damaged index is a non-answer, not a smaller denominator."""


def _parse_tsv(body: bytes, path: Path) -> list[XrefRow]:
    rows: list[XrefRow] = []
    line = 1
    try:
        r = csv.reader(io.StringIO(body.decode('utf-8-sig'), newline=''),
                       delimiter='\t', strict=True)
        if next(r, None) != _HEADER:
            raise ValueError('missing or incorrect header')
        for row in r:
            line = r.line_num
            if len(row) != 7:
                raise ValueError(f'expected 7 fields, got {len(row)}')
            if row[0] not in ('event', 'signal', 'cuedef', 'action'):
                raise ValueError(f'unsupported kind {row[0]!r}')
            if not all(row[i].strip() for i in (1, 2, 3)):
                raise ValueError('name, source and file must be nonempty')
            if not row[5].isascii() or not row[5].isdigit():
                raise ValueError('line must be a nonnegative integer')
            rows.append(XrefRow(*row[:5], int(row[5]), row[6]))
    except (ValueError, csv.Error) as exc:
        raise IndexUnreadable(f'{path}: row {line}: {exc}; rebuild the xref index') from exc
    return rows


def read_tsv(path: Path) -> list[XrefRow]:
    return _parse_tsv(path.read_bytes(), path)


def query(rows: list[XrefRow], kind: str, name: str) -> list[XrefRow]:
    name_l = name.lower()
    return [r for r in rows if r.kind == kind and r.name.lower() == name_l]


def cue_edges(rows: list[XrefRow], cue_name: str) -> dict[str, list[XrefRow]]:
    """All rows referencing a cue by short name (defs, signals, cancels).

    A ``cue="md.Script.Cue"`` reference is matched on its final ``.Cue`` segment,
    since signals within a script use the short name and cross-script refs qualify it.
    """
    short = cue_name.rsplit(".", 1)[-1].lower()
    out: dict[str, list[XrefRow]] = defaultdict(list)
    for r in rows:
        if r.kind == "cuedef" and r.name.lower() == short:
            out["defined"].append(r)
        elif r.kind == "signal" and r.target.rsplit(".", 1)[-1].lower() == short:
            out[r.name].append(r)
    return dict(out)


# --- CLI ----------------------------------------------------------------------

def _default_tsv() -> Path:
    return _registry.require(
        _registry.DEFAULT_REGISTRY, "the registry location",
        "set X4_MODS (or X4_REGISTRY), or pass --out/--index").parent / "md_xref.tsv"


def _fmt(r: XrefRow) -> str:
    loc = f"{r.source}:{r.file}:{r.line}"
    ctx = f" (in cue {r.cue})" if r.cue else ""
    tgt = f" -> {r.target}" if r.target else ""
    return f"  {loc}{ctx}{tgt}"


#: Which command finds a row of each kind BY ITS NAME. A `signal` row's name is the
#: cue-edge ACTION tag (`signal_cue`); its target cue is found by `cue <target>`, so a
#: signal row is reached by name through `who-calls`, not `cue` -- mapping it to `cue`
#: made `cue signal_cue` suggest `cue signal_cue` (AUDIT-2026-09-24 AN-8).
_KIND_CMD = {"action": "who-calls", "event": "who-listens", "cuedef": "cue", "signal": "who-calls"}

#: The row kinds each query command searches by name. `who-calls` covers the cue-edge
#: actions too: they ARE action elements, indexed as `signal` only so their `cue=`
#: target is kept, and no command answered "where is signal_cue used" (AN-8).
_CMD_KINDS = {"who-calls": ("action", "signal"), "who-listens": ("event",)}


def _sidecar(tsv: Path) -> Path:
    """Where an index records the files it could NOT read."""
    return tsv.with_suffix(tsv.suffix + ".notindexed")


def _exclusions(tsv: Path | None) -> str:
    """Render the index's own exclusions, so a negative carries its denominator.

    "0 hits" is a lead; "0 hits over N rows with M named exclusions" is a
    finding. Same contract as `tools\\basex\\ask.py`, which refuses to render a
    zero-result without coverage. A negative that hides its blind spots is the
    most confidently wrong answer this tool can give.
    """
    if tsv is None or not _sidecar(tsv).is_file():
        return ""
    lines = [ln for ln in _sidecar(tsv).read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return ""
    return (f" — EXCEPT {len(lines)} file(s) that would not parse and are not in the "
            f"index at all (see {_sidecar(tsv).name})")


def _expected_dlc() -> int | None:
    """How many DLC roots the configured game has (`Config.dlc_dirs()`), or None when no game
    root is configured -- a SETUP state, not a coverage claim."""
    from x4validate import _merge
    try:
        return len(_merge.Config().dlc_dirs())
    except Exception:  # silent-ok: no configured game root; coverage_note says nothing then
        return None


def coverage_incomplete(rows: list[XrefRow]) -> bool:
    """True when the index holds fewer DLC than are installed (see coverage_note)."""
    expected = _expected_dlc()
    dlc = {r.source for r in rows if r.source.startswith("dlc:")}
    return bool(expected) and len(dlc) < expected


def coverage_note(rows: list[XrefRow]) -> str:
    """Describe the SCANNED SOURCE SET behind an answer, and flag a shortfall.

    `_exclusions` below reports files that were tried and would not parse. That is
    a different thing from a source never enumerated at all -- and the second is
    what a blind spot actually looks like. F10 hid in exactly that gap: the packed
    mini-DLC were not unreadable, they were unvisited, so nothing anywhere said a
    word while `who-calls` answered over 6 of the 8 installed DLC.

    Comparing the DLC actually present in the index against `Config.dlc_dirs()`
    turns that silence into a visible INCOMPLETE.
    """
    srcs = {r.source for r in rows}
    dlc = {s for s in srcs if s.startswith("dlc:")}
    mods = srcs - dlc - {"base"}
    note = (f" from base + {len(dlc)} DLC + {len(mods)} mod(s)")
    expected = _expected_dlc()
    if expected is None:
        # No configured game root is a SETUP state, not a coverage claim. Report what was
        # scanned; assert nothing about what is missing.
        return note
    if expected and len(dlc) < expected:
        note += (f" — ⚠ INCOMPLETE: {len(dlc)} of {expected} installed DLC are in this "
                 f"index, so any negative here is a lead, not a finding")
    return note


def _hint_other_kinds(rows: list[XrefRow], name: str, asked_kind: "str | tuple[str, ...]",
                      tsv: Path | None = None, ran: str = "", *, certified: bool = False) -> None:
    """When a name isn't found in the asked-for kind, say whether it exists at all.

    `who-calls event_player_ejected` used to print exactly the same line as
    `who-calls definitely_not_a_real_thing` — yet the first name is in the index
    5 times as an EVENT. Two completely different states, one message, exit 0.
    (That was also the example CLAUDE.md advertises for this tool.) A name that
    exists under another kind is a wrong-command mistake; a name that exists
    nowhere is a real negative. They must never read the same.
    """
    from collections import Counter
    # CASE-INSENSITIVE, exactly as `query` matches. It compared `r.name == name`,
    # while `query` folds both sides -- so a differently-cased argument fell into
    # the branch below and printed 'does not appear under ANY kind - a real
    # negative', with a denominator attached to lend it weight, for a name the
    # index holds. REPRODUCED through the CLI: `who-calls Event_Player_Ejected`
    # asserted the negative while `who-calls event_player_ejected` correctly
    # offered `who-listens`, the only difference being the case of the argument.
    #
    # That inverts this function's purpose as stated three lines up: the two
    # states 'wrong command' and 'real negative' must never read the same, and
    # they read the same in the direction that manufactures a false fact.
    # CLAUDE.md #9: default to case-insensitive for X4 identifiers, because the
    # corpus genuinely mixes case.
    name_l = name.lower()
    asked = (asked_kind,) if isinstance(asked_kind, str) else tuple(asked_kind)
    elsewhere = Counter(r.kind for r in rows
                        if r.name.lower() == name_l and r.kind not in asked)
    if not elsewhere:
        print(f"  and '{name}' does not appear under ANY kind — "
              + (f"a real negative over {len(rows)} indexed rows" if certified else
                 f"an unverified absence over {len(rows)} indexed rows")
              +
              f"{coverage_note(rows)}{_exclusions(tsv)}.")
        return
    print(f"  BUT '{name}' IS in the index under other kind(s):")
    for kind, n in elsewhere.most_common():
        cmd = _KIND_CMD.get(kind)
        # Never suggest the command that was just run: that hint is a loop.
        suffix = f"   -> try:  x4xref {cmd} {name}" if cmd and cmd != ran else ""
        print(f"    {kind:8} {n:>5} occurrence(s){suffix}")


@_paths.refuses_unconfigured
def main(argv: list[str] | None = None) -> int:
    import argparse

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass  # silent-ok: console encoding shim. Failure means the default codec
        # stays; it affects how output LOOKS, never what was examined.

    p = argparse.ArgumentParser(
        prog="x4xref",
        description="Cross-index of MD/aiscript actions, events, and cue edges.")
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("build", help="(re)build the index over base+DLC+installed mods")
    pb.add_argument("--reference", help="unpacked base+DLC tree ($X4_REFERENCE)")
    pb.add_argument("--ext-dir", help="extensions dir (default: game-root from _registry)")
    pb.add_argument("--out", help="TSV output path (default: dev\\_registry\\md_xref.tsv)")

    for cmd, kind, help_ in [
        ("who-calls", "action", "list every place an action element appears, cue-edge "
                                "actions (signal_cue, cancel_cue, ...) included"),
        ("who-listens", "event", "list every cue reacting to an event_* condition"),
    ]:
        q = sub.add_parser(cmd, help=help_)
        q.add_argument("name", help="the action/event name (e.g. set_object_min_hull)")
        q.add_argument("--tsv", help="index path (default: dev\\_registry\\md_xref.tsv)")
        q.add_argument("--limit", type=int, default=20,
                       help="max occurrences shown per source, 0 = no cap (default: %(default)s)")
        q.set_defaults(_kind=kind)

    qc = sub.add_parser("cue", help="where a cue is defined, signalled, cancelled")
    qc.add_argument("name", help="cue name (short or md.Script.Cue)")
    qc.add_argument("--tsv", help="index path (default: dev\\_registry\\md_xref.tsv)")

    args = p.parse_args(argv)

    if args.cmd == "build":
        ref = Path(args.reference) if args.reference else _merge.Config().reference
        ext = Path(args.ext_dir) if args.ext_dir else _registry.require(
        _registry.GAME_EXTENSIONS, "the game extensions dir",
        "set X4_GAME (or X4_EXTENSIONS), or pass --ext-dir")
        out = Path(args.out) if args.out else _default_tsv()
        unreadable: list = []
        if not ext.is_dir():
            # An index with no mods in it cannot support the one thing x4xref
            # exists for: an admissible NEGATIVE ("nobody calls X"). Building
            # one anyway and returning 0 hands every later query a denominator
            # that excludes every mod on the machine without saying so.
            print("REFUSING: the extensions directory does not exist: "
                  + str(ext), file=sys.stderr)
            print("  An xref index built without it covers base + DLC only, "
                  "and every 'nobody references X' answer from it would "
                  "silently exclude every installed mod.", file=sys.stderr)
            print("  Set X4_GAME (or X4_EXTENSIONS), or pass --ext-dir.",
                  file=sys.stderr)
            return 2
        # Issue #10 shape: the REFERENCE is the other half of the population, and a
        # missing or empty one indexed 0 rows, overwrote the index and returned 0.
        # Refused BEFORE anything is written, so an existing index survives.
        try:
            _effective.require_base_documents(_merge.Config(reference=ref),
                                              "build the md/aiscript xref index")
        except _effective.EmptyReference as exc:
            print(f"REFUSING: {exc}", file=sys.stderr)
            return 2
        rows = build_index(ref, ext, unreadable)
        write_tsv(rows, out)
        # THE SIDECAR THE QUERY PATH READS, WHICH NOTHING HAS EVER WRITTEN.
        # `_exclusions()` renders "EXCEPT N file(s) that would not parse" from
        # this file on every answer — and `_sidecar()` had exactly one
        # caller, a reader. The unreadable files were printed once at build
        # time, to stderr, and then discarded, so every later negative was
        # rendered with no exclusion list at all: precisely what `_exclusions`'
        # own docstring calls "the most confidently wrong answer this tool can
        # give".
        #
        # Written UNCONDITIONALLY, empty file included. A sidecar left over from
        # an earlier build would otherwise attach stale exclusions to a clean
        # index — a wrong denominator is not safer than none.
        _sidecar(out).write_text(
            "".join(str(u) + "\n" for u in unreadable), encoding="utf-8")
        # WHEN this index was true. Its whole purpose is to make "nobody calls X"
        # admissible, and a negative from a superseded world is not admissible.
        _mutation.refuse_if_mutating("build the md/aiscript xref index")
        _fp = _freshness.fingerprint(_merge.Config(reference=ref), index_roots(ext))
        # The roots are RECORDED so the check compares against the world this index was
        # built over -- `build --ext-dir X` used to read STALE forever, because the query
        # side always fingerprinted the game-root extensions instead (FR-3).
        _fp["roots"] = [str(r) for r in index_roots(ext)]
        # ...and the REFERENCE, for the same reason: `build --reference X` was checked
        # against the CONFIGURED reference and read stale forever when they differ.
        # (`reference` is taken -- it is the fingerprint's digest -- hence the name.)
        _fp["reference_path"] = str(ref)
        _fp['artifact_sha256'] = hashlib.sha256(out.read_bytes()).hexdigest()
        _fp['exclusions_sha256'] = hashlib.sha256(_sidecar(out).read_bytes()).hexdigest()
        _freshness.stamp_sidecar(out, _fp)
        from collections import Counter
        by = Counter(r.kind for r in rows)
        print(f"indexed {len(rows)} rows -> {out}")
        print("  " + "  ".join(f"{k}={v}" for k, v in sorted(by.items())))
        if unreadable:
            # The index is the denominator behind every negative answer below.
            # A file missing from it must be named, not quietly absent.
            print(f"\n  NOT INDEXED — {len(unreadable)} file(s) would not parse; "
                  "any 'no references' answer excludes them:", file=sys.stderr)
            for u in unreadable:
                print(f"   - {u}", file=sys.stderr)
        return 0

    # An EMPTY or blank name is not a question. It used to reach the search, match
    # nothing, and be certified "a real negative over N indexed rows" at rc 0
    # (AUDIT-2026-09-24 AN-11, pinned by gates/edge_sweep.py) -- the confident wrong
    # answer this tool exists to refuse.
    if not (getattr(args, "name", "x") or "").strip():
        print("refusing: an empty name matches nothing by definition, so it is not a "
              "negative about anything. Pass the action/event/cue name to look up.",
              file=sys.stderr)
        return 2
    tsv = Path(args.tsv) if getattr(args, "tsv", None) else _default_tsv()
    if not tsv.is_file():
        print(f"index not found: {tsv}\nrun `x4xref build` first.", file=sys.stderr)
        return 2
    # Printed on EVERY query until rebuilt. x4xref exists to support NEGATIVES
    # ("nobody listens to this event"), which is precisely the claim a stale
    # index gets wrong -- a mod added since the build is simply not in it.
    try:
        body = tsv.read_bytes()
        rows = _parse_tsv(body, tsv)
        _stored = _freshness.read_sidecar(tsv)
        signed = bool(_stored and _stored.get('artifact_sha256'))
        if signed and _stored['artifact_sha256'] != hashlib.sha256(body).hexdigest():
            raise IndexUnreadable(f'{tsv}: artifact integrity mismatch; rebuild the xref index')
        exclusions_signed = bool(_stored and _stored.get('exclusions_sha256'))
        if exclusions_signed and _stored['exclusions_sha256'] != hashlib.sha256(_sidecar(tsv).read_bytes()).hexdigest():
            raise IndexUnreadable(f'{tsv}: exclusions integrity mismatch; rebuild the xref index')
        if _stored and ('roots' in _stored and (not isinstance(_stored['roots'], list)
                or not all(isinstance(r, str) for r in _stored['roots']))):
            raise IndexUnreadable(f'{tsv}: invalid roots in freshness sidecar; rebuild the xref index')
        if _stored:
            for field in ('reference_path', 'content', 'engine', 'reference',
                          'artifact_sha256', 'exclusions_sha256'):
                value = _stored.get(field)
                if value is not None and not isinstance(value, str):
                    raise IndexUnreadable(f'{tsv}: invalid {field} in freshness sidecar; rebuild the xref index')
            detail = _stored.get('detail')
            if detail is not None and (not isinstance(detail, list)
                    or not all(isinstance(r, dict) and isinstance(r.get('folder'), str)
                               and isinstance(r.get('root', ''), str) for r in detail)):
                raise IndexUnreadable(f'{tsv}: invalid detail in freshness sidecar; rebuild the xref index')
        _roots = ([Path(r) for r in _stored["roots"]] if _stored and _stored.get("roots")
                  else index_roots(_registry.GAME_EXTENSIONS))
        _ref_then = (Path(_stored["reference_path"])
                     if _stored and _stored.get("reference_path") else None)
        _stale = _freshness.compare(
            _stored, _freshness.fingerprint(_merge.Config(reference=_ref_then), _roots),
            engine_dependent=False)
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        print(f'index unreadable: {tsv}: {exc}; rebuild with uv run x4xref build', file=sys.stderr)
        return 2
    if not signed or not exclusions_signed:
        print(f'!! Index integrity unavailable for {tsv}; positive lookups remain usable, '
              'but absence cannot be certified. Rebuild: uv run x4xref build', file=sys.stderr)
    if not _stale.fresh:
        print(_stale.banner("the x4xref index"), file=sys.stderr)
        print("!! Rebuild:  uv run x4xref build", file=sys.stderr)

    intact = signed and exclusions_signed and _stale.fresh
    # v4.0.0 review R5-11: an index that misses installed DLC said "INCOMPLETE ... a lead,
    # not a finding" in its note and then certified the absence with exit 0 anyway.
    incomplete = intact and coverage_incomplete(rows)
    certified = intact and not incomplete

    def absence():
        if certified:
            return 0
        if incomplete:
            print('DEGRADED: the index is intact and fresh but holds fewer DLC than are '
                  'installed, so this negative is a lead, not a finding (exit 3). Rebuild: '
                  'uv run x4xref build', file=sys.stderr)
            return 3
        print('NOT A NEGATIVE FINDING: index integrity or source freshness is unavailable; '
              'rebuild with uv run x4xref build.', file=sys.stderr)
        return 2

    if args.cmd == "cue":
        edges = cue_edges(rows, args.name)
        if not edges:
            print(f"no references to cue '{args.name}'")
            _hint_other_kinds(rows, args.name, "cue", tsv, ran="cue", certified=certified)
            return absence()
        for group in ("defined", *sorted(k for k in edges if k != "defined")):
            if group in edges:
                print(f"{group} ({len(edges[group])}):")
                for r in edges[group]:
                    print(_fmt(r))
        return 0

    kinds = _CMD_KINDS[args.cmd]
    hits = [h for k in kinds for h in query(rows, k, args.name)]
    if not hits:
        print(f"no {args._kind} '{args.name}' found in the index.")
        _hint_other_kinds(rows, args.name, kinds, tsv, ran=args.cmd, certified=certified)
        return absence()
    by_source: dict[str, list[XrefRow]] = defaultdict(list)
    for h in hits:
        by_source[h.source].append(h)
    print(f"{args._kind} '{args.name}': {len(hits)} occurrence(s) across "
          f"{len(by_source)} source(s)")
    cap = args.limit if args.limit and args.limit > 0 else None
    for src in sorted(by_source):
        group = by_source[src]
        print(f"[{src}]  ({len(group)})")
        for r in group[:cap] if cap else group:
            print(_fmt(r))
        if cap and len(group) > cap:
            print(f"  ... +{len(group) - cap} more (use --limit 0 to show all)")
    return 0
