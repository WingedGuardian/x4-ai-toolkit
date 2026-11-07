"""Read an X4 savegame: what is baked into it, what it references that is gone, its
MD variables, and a census of its components (`census`, in _savecensus.py).

A save is the only artifact the ENGINE wrote. It answers two questions no manifest
can, and both were measured in-game on 2026-08-26 (the measurements are summarised
below; the maintainers' knowledge-base entry they came from does not ship).

WHY THIS IS NOT EXPENSIVE. The scary number is the decompressed size -- the largest
save here is 140 MB compressed and 1.28 GB expanded. That is a wall for a DOM and for
nothing else: streaming the whole thing takes 2.5s, and a scoped attribute sweep over
it 4.2s. Everything here streams; nothing builds a tree.

WHAT <patches> IS, AND IS NOT. It is NOT a record of what loaded. MEASURED: an
extension appears iff its content.xml `save` attribute is ABSENT or `="1"`. Of 129
installed extensions, 11 qualify and 10 were recorded (the gap is ego_dlc_ventures,
an online DLC not loaded as a content patch); of the 118 declaring "0"/"false", zero
appeared. For MODS that is 3 of 121 (2.5%). It is the SAVE-BAKED set -- the ones
unsafe to remove -- and it is *less* complete than the profile content.xml, which is
itself only a decision log. `info` states that rule on every run, so the list cannot
be misread as coverage (the counts above are this machine's; `info` prints none).

WHY `check` IS WORTH HAVING. Removing a mod does not leave dangling references: the
engine SILENTLY DELETES the orphaned content. MEASURED by disabling one mod and
reloading -- its 37 macros / 46 references went to 0, the engine logged ONE error line
(naming a galaxy connection, not the station, ship, production modules or 36 other
macros that vanished), there was no dialog, and the net error count went DOWN by 3
because the mod's own 4 errors left with it. Judged by debug.txt the removal looked
like an improvement. This tool is worth having precisely BECAUSE the engine is quiet:
it names 37 items where the log names one.
  Scope: n=1 mod, one save. `check` reports what a save references that no longer
  resolves; it does NOT assert the general rule.

WHAT `vars` READS. Mod options live in MD cue variables. MEASURED layout of a 9.0 save:
<md> > <script name> > nested <cue name>/<library name> > <vars> > <value name type value>.
The same <value> element also holds AI-script variables (<aidirector> > <entity id> > <script
name> > <vars>, ~90% of a save's named values) and entity blackboards; every row says which it
is, so an AI-script variable never reads as an MD cue path. The engine OMITS value= for a zero
integer (MEASURED), printed "0 (absent)" -- read as empty it would invert an on/off flag; for
other numeric types the same rule is INFERRED and printed "0 (absent, inferred)". A value with no
type is null. Tables, lists and groups are stored by reference id and printed type#<id>, not
expanded, so their contents are not searched. Rows are streamed, never accumulated. Pass a save
NAME: a path into the save folder makes the save-protection guard treat the command as an opaque
program (an ask).

THREE THINGS THAT MADE THIS WRONG BEFORE IT WAS RIGHT, all enforced below:
  1. `macro=` is NOT always a macro reference. On <connection> it mirrors the
     connection NAME (<connection connection="con_cockpit" macro="con_cockpit">), and
     counting those produced 2,463 false dangling. Only _REF_ELEMENTS carry real ones.
  2. The corpus mixes case and the save lowercases. A case-SENSITIVE compare produced
     1,068 false positives on a stock vanilla save. Folding costs something and the
     cost is REPORTED, never swallowed: 13,308 defined names fold to 12,755, so 553
     (4.2%) collide and a genuine miss could hide behind one.
  3. EntityDefs.__contains__ is a per-NAME oracle whose cost model assumes a mod (7
     misses). A save has 5,022 references; it blew a 600s cap. Use
     EntityDefs.all_names() -- built once (19.4s then; 31-44s MEASURED 2026-10-18 over
     19,763 effective documents). BLIND-SPOTS F65.
"""
from __future__ import annotations

import gzip
import re
import sys
import zlib
from xml.parsers import expat
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import _check, _closedpipe, _effective, _merge, _paths, _registry, _scan
from . import __version__

#: Elements whose ``macro=`` is a genuine macro reference. <connection> is excluded on
#: purpose -- see the module docstring, defect (1).
_REF_ELEMENTS = ("component", "item", "unit", "launched", "entry", "field")
_REF_RE = re.compile(
    rb"<(" + b"|".join(e.encode() for e in _REF_ELEMENTS) + rb')\b[^>]*?\bmacro="([^"]+)"')

_CHUNK = 1 << 22
#: Carried between chunks so a tag split across a read boundary is not missed.
#: MUST be > 0: `buf[-0:]` is `buf[:]`, i.e. the WHOLE buffer, so a value of 0
#: silently makes the tail infinite instead of removing it -- found while
#: mutation-testing this file, where it made a mutant pass.
_TAIL = 4096
assert _TAIL > 0


class SaveUnreadable(Exception):
    """The save could not be opened or decoded. rc 2 -- a NON-ANSWER, never rc 0."""


def read_header(path: Path, limit: int = 4_000_000) -> str:
    """The <info> block as text, by streaming and stopping at </info>."""
    raw = b""
    try:
        with gzip.open(path, "rb") as fh:
            while b"</info>" not in raw and len(raw) < limit:
                chunk = fh.read(65536)
                if not chunk:
                    break
                raw += chunk
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error) as exc:
        raise SaveUnreadable(f"{path}: not readable as gzip ({exc})") from exc
    text = raw.decode("utf-8", "replace")
    end = text.find("</info>")
    if end < 0:
        raise SaveUnreadable(
            f"{path}: no </info> within {len(raw):,} bytes -- not an X4 savegame?")
    return text[:end]


def extract_refs(path: Path) -> Counter:
    """Bounded streaming XML scan of the six semantic reference elements.

    Expat retains parser state rather than a document tree. Comments cannot add
    references, XML quoting is handled by the parser, and truncation is a non-answer.
    DTD/entity declarations are unsupported and refused explicitly.
    """
    found: Counter = Counter()
    parser = expat.ParserCreate()

    def start(name, attrs):
        if name in _REF_ELEMENTS and attrs.get("macro"):
            found[attrs["macro"]] += 1

    def doctype(*args):
        raise SaveUnreadable(f"{path}: DTD/entity declarations are not supported in save XML")

    parser.StartElementHandler = start
    parser.StartDoctypeDeclHandler = doctype
    try:
        with gzip.open(path, "rb") as fh:
            while True:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    break
                parser.Parse(chunk, False)
            parser.Parse(b"", True)
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error, expat.ExpatError) as exc:
        raise SaveUnreadable(f"{path}: decompression/XML parse failed ({exc})") from exc
    return found



@dataclass(frozen=True)
class VarRow:
    """One named variable: where it lives, its name, declared type, display value."""
    where: str
    name: str
    type: str
    value: str


@dataclass
class VarScan:
    rows: list[VarRow]          # empty when an on_row callback consumed them
    scanned_bytes: int          # decompressed bytes read: the denominator for a zero
    count: int = 0              # matches, whether kept or streamed


#: MD containers whose name= forms the variable's location (MEASURED layout of a 9.0 save:
#: <md> > <script name> > nested <cue name>/<library name> > <vars> > <value>).
_VAR_SCOPES = ("script", "cue", "library")
#: Types stored by REFERENCE to a container serialised elsewhere in the save (MEASURED on a
#: 9.0 save: table, list and group all carry value="<id>"). Shown as type#id, never expanded.
_REF_TYPES = ("table", "list", "group")
#: The engine omits value= for a zero. MEASURED for integer (mod on/off flags read back as
#: their defaults); INFERRED for these other numeric types, so they are labelled as such.
_NUMERIC_INFERRED = ("float", "largeint", "largefloat", "time", "length", "money",
                     "hitpoints", "angle")


def _var_display(attrs: dict) -> tuple[str, str]:
    typ = attrs.get("type", "")
    if not typ:
        return "", "null"                       # no type attribute: the value is null
    if typ in _REF_TYPES:
        return typ, f"{typ}#{attrs.get('value', '?')}"
    if "value" not in attrs:
        if typ == "integer":
            return typ, "0 (absent)"            # read as empty it would invert a flag
        if typ in _NUMERIC_INFERRED:
            return typ, "0 (absent, inferred)"
        return typ, "(absent)"
    return typ, attrs["value"]


def _var_where(stack: list[tuple[str, dict]]) -> str:
    tags = [tag for tag, _ in stack]
    if "md" in tags:
        inner = stack[tags.index("md") + 1:]
        scopes = [a.get("name", "") for tag, a in inner if tag in _VAR_SCOPES and a.get("name")]
        return "/".join(scopes) if scopes else "(md, no named cue)"
    if "aidirector" in tags:
        script = next((a.get("name", "?") for tag, a in reversed(stack) if tag == "script"), "?")
        entity = next((a.get("id", "?") for tag, a in reversed(stack) if tag == "entity"), "?")
        return f"(aiscript {script} on entity {entity})"
    container = next((tag for tag, _ in reversed(stack) if tag != "vars"), "?")
    return f"(outside MD cues: {container})"


def extract_vars(path: Path, pattern: str, on_row=None) -> VarScan:
    """Every NAMED <value> whose name matches *pattern* (regex, case-insensitive, tried both
    with and without the stored leading '$'), with where it lives: an MD script/cue path, an
    AI-director script and its entity, or the container of anything else (never dropped).
    Anonymous <value> elements are not variables. With *on_row*, rows are streamed to it and
    not kept. Streams like extract_refs; a truncated or malformed save raises SaveUnreadable."""
    rx = re.compile(pattern, re.IGNORECASE)
    rows: list[VarRow] = []
    stack: list[tuple[str, dict]] = []          # (element name, {name, id}) per open element
    parser = expat.ParserCreate()
    scanned = 0
    count = 0
    sink_failure = None     # an on_row write that failed: the destination's fault, never the save's

    def start(name, attrs):
        nonlocal count, sink_failure
        vname = attrs.get("name") if name == "value" else None
        if vname and (rx.search(vname) or rx.search(vname.lstrip("$"))):
            typ, val = _var_display(attrs)
            row = VarRow(_var_where(stack), vname, typ, val)
            count += 1
            if on_row is None:
                rows.append(row)
            else:
                try:
                    on_row(row)
                except OSError as exc:
                    sink_failure = exc
                    raise
        keep = {k: attrs[k] for k in ("name", "id") if k in attrs}
        stack.append((name, keep))

    def end(name):
        stack.pop()

    def doctype(*args):
        raise SaveUnreadable(f"{path}: DTD/entity declarations are not supported in save XML")

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = doctype
    try:
        with gzip.open(path, "rb") as fh:
            while True:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    break
                scanned += len(chunk)
                parser.Parse(chunk, False)
            parser.Parse(b"", True)
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error, expat.ExpatError) as exc:
        if exc is sink_failure:
            raise
        raise SaveUnreadable(f"{path}: decompression/XML parse failed ({exc})") from exc
    return VarScan(rows, scanned, count)


def cmd_vars(path: Path, pattern: str, out=None) -> int:
    out = sys.stdout if out is None else out
    printed = 0

    def emit(r: VarRow) -> None:
        nonlocal printed
        # One row, one line: a stored newline must not split a row a reader may be cutting or grepping. The backslash is
        # escaped too, so a stored backslash-n reads differently from a stored newline and the display can be undone.
        row = f"  {r.where}  {r.name}  {r.type or '-'}  {r.value}"
        print(row.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n"), file=out)
        printed += 1

    print(f"save: {path}", file=out)
    try:
        scan = extract_vars(path, pattern, on_row=emit)
    except SaveUnreadable:
        if printed:
            print(f"INCOMPLETE: {printed} row(s) were printed above before the save failed to read; "
                  "the list is partial, not an answer", file=sys.stderr)
        raise
    if scan.count:
        print(f"{scan.count} match(es) over {scan.scanned_bytes:,} decompressed bytes. Tables, lists "
              "and groups are shown as references (type#id), not expanded.", file=out)
        return 0
    print(f"NO MATCH over {scan.scanned_bytes:,} decompressed bytes for /{pattern}/: no named "
          "variable matched (table, list and group contents are not searched)", file=out)
    return 1


def _attr(block: str, tag: str, name: str) -> str:
    m = re.search(rf'<{tag}\b[^>]*?\b{name}="([^"]*)"', block)
    return m.group(1) if m else ""


def patch_lists(block: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(current, history) as (extension, version) pairs."""
    cur_src, _, hist_src = block.partition("<history>")
    pat = re.compile(r'<patch extension="([^"]+)" version="([^"]+)"')
    return pat.findall(cur_src), pat.findall(hist_src)


def resolve_save(arg: str | None) -> Path:
    """A path, a save NAME, or the newest save in the profile."""
    if arg:
        p = Path(arg)
        if p.exists():
            return p
        d = _paths.savegames()
        if d:
            for cand in (d / arg, d / f"{arg}.xml.gz"):
                if cand.exists():
                    return cand
        raise SaveUnreadable(f"no save found at {arg!r}")
    d = _paths.savegames()
    if not d or not d.is_dir():
        raise SaveUnreadable(
            "no savegame directory -- set $X4_SAVES or $X4_PROFILE "
            "(see `x4validate --paths`)")
    saves = sorted(d.glob("*.xml.gz"), key=lambda q: q.stat().st_mtime, reverse=True)
    if not saves:
        raise SaveUnreadable(f"{d}: contains no *.xml.gz saves")
    return saves[0]


def cmd_info(path: Path, out=None) -> int:
    out = sys.stdout if out is None else out
    block = read_header(path)
    cur, hist = patch_lists(block)

    print(f"save: {path}", file=out)
    print(f"  name       {_attr(block, 'save', 'name')!r}", file=out)
    print(f"  version    {_attr(block, 'game', 'version')}  "
          f"build {_attr(block, 'game', 'build')}", file=out)
    print(f"  playtime   {float(_attr(block, 'game', 'time') or 0) / 3600:.1f} h", file=out)
    print(f"  gamestart  {_attr(block, 'game', 'start')}", file=out)
    print(f"  player     {_attr(block, 'player', 'name')!r}  "
          f"money {int(_attr(block, 'player', 'money') or 0):,}", file=out)

    print(f"\n  SAVE-BAKED extensions ({len(cur)}) -- recorded because their content.xml",
          file=out)
    print('  declares save="1" or omits the attribute. Removing one of these is the',
          file=out)
    print("  dangerous case (CLAUDE.md #4).", file=out)
    for ext, v in sorted(cur):
        print(f"    {ext:24s} v{v}", file=out)

    # Compared by extension NAME. As (ext, version) tuples an extension that was merely
    # UPDATED (v100 in <history>, v200 now) landed in this list (RT-2).
    cur_names = {ext for ext, _ in cur}
    gone = sorted({(e, v) for e, v in hist if e not in cur_names})
    if gone:
        # Worded as what the SAVE records, never as a load state: <patches> is the
        # save-baked set, not what loaded (module docstring), so a history-only entry
        # does not establish that the extension is absent from the game now. The real
        # trigger was ego_dlc_ventures -- installed, enabled, and history-only (RT-2).
        print(f"\n  IN <history> ONLY ({len(gone)}) -- baked into this save at some point,",
              file=out)
        print("  absent from its current <patches>. That says nothing about whether the",
              file=out)
        print("  extension is installed or loads today:", file=out)
        for ext, v in gone:
            print(f"    {ext:24s} v{v}", file=out)

    # No counts here: the figures this used to print (3 of 121 mods, 118 of 129
    # extensions) were ONE machine's measurement, printed to every user as if about
    # theirs (RT-9). The rule they illustrate is general; the numbers are not.
    print("\n  ! This is NOT a list of what loaded. An extension appears only if its",
          file=out)
    print('    content.xml declares save="1" or omits the attribute; one declaring',
          file=out)
    print('    save="0"/"false" never appears, however much it changes the game.',
          file=out)
    return 0


def cmd_check(path: Path, limit: int = 40, out=None) -> int:
    out = sys.stdout if out is None else out
    report = _check.Report()

    refs = extract_refs(path)
    if not refs:
        report.skip("save reference scan",
                    f"{path}: no macro references found on any of {_REF_ELEMENTS} -- "
                    "that is a NON-ANSWER, not a clean save", degraded=True)

    mods = _registry.mods("active")
    # A macro only a mod the engine leaves out defines reads UNRESOLVED below -- right
    # for the engine, and NAMED here so it is not mistaken for a missing mod (F139).
    _check.note_not_loaded(mods, "definition set: a mod the engine does not load", report)
    cfg = _merge.Config(overlays=[p for _, p in _effective.ordered_overlays(mods)])
    _effective.require_base_documents(cfg, "check save references")
    defs = _check.EntityDefs(cfg, report=report)
    print(f"building the definition set over base + {len(cfg.dlc_dirs())} DLC + "
          f"{len(cfg.overlays)} active mods (~30-45 s on a ~130-mod install)...", file=sys.stderr)
    names = defs.all_names()
    folded = {n.lower() for n in names}
    collisions = len(names) - len(folded)

    missing = {n: c for n, c in refs.items() if n.lower() not in folded}
    shown = sorted(missing, key=lambda n: -missing[n])[:limit]

    print(f"save: {path}", file=out)
    print(f"  examined   {len(refs):,} distinct macro references "
          f"({sum(refs.values()):,} total)", file=out)
    print(f"  against    {len(names):,} names defined by base + "
          f"{len(cfg.dlc_dirs())} DLC + {len(cfg.overlays)} active mods", file=out)
    print(f"  UNRESOLVED {len(missing):,}", file=out)

    if shown:
        print("", file=out)
        for n in shown:
            print(f"    {missing[n]:8,d}x  {n}", file=out)
        print("  " + _scan.count_line(len(shown), len(missing), "unresolved names"),
              file=out)

    print("\n  SCOPE -- checked: macro references on <"
          + ">, <".join(_REF_ELEMENTS) + ">.", file=out)
    print("    NOT examined: ware ids, component refs, faction ids, script names, or",
          file=out)
    print("    anything outside a macro= attribute. A zero is not \"this save is clean\".",
          file=out)
    print(f"    Comparison is case-insensitive (the corpus mixes case, the save "
          f"lowercases);", file=out)
    print(f"    {collisions} of {len(names):,} defined names "
          f"({collisions / max(len(names), 1):.1%}) collide when folded, so a genuine",
          file=out)
    print("    miss could hide behind one.", file=out)

    if missing:
        print("\n  These resolve against nothing in the live tree. MEASURED on one "
              "removal, the", file=out)
        print("  engine deletes such content SILENTLY on load -- 37 macros went to 0 "
              "with one", file=out)
        print("  error line and no dialog. Treat this as content that would vanish, "
              "not as an", file=out)
        print("  error the game will report.", file=out)

    for s in report.skipped:
        print(f"  {'!!' if s.degraded else '-'} {s.what}: {s.why}", file=out)
    if report.degraded:
        print("  ** a check you asked for did not run -- this result is NOT a pass **",
              file=out)
        return 3
    return 1 if missing else 0


@_paths.refuses_unconfigured
@_closedpipe.quiet_exit
def main(argv: list[str] | None = None) -> int:
    import argparse

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass  # silent-ok: console encoding shim. Changes how output LOOKS, never what
        # was examined.

    p = argparse.ArgumentParser(
        prog="x4save",
        description="Read an X4 savegame: what is baked into it, what it "
                    "references that the live tree no longer defines, its MD variables, and a count "
                    "of its components (ships, turrets, stations) by macro, owner or parent.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("info",
                        help="header: build, playtime, and the SAVE-BAKED extensions")
    pi.add_argument("save", nargs="?", help="path or save name (default: newest)")

    pc = sub.add_parser("check",
                        help="macro references the live tree no longer defines")
    pc.add_argument("save", nargs="?", help="path or save name (default: newest)")
    pc.add_argument("--limit", type=int, default=40, help="names to print (default 40)")

    pv = sub.add_parser("vars",
                        help="named variables whose name matches a pattern: MD cue variables, AI-script "
                             "and blackboard values, each labelled with where it lives (pass a save "
                             "NAME, not a path into the save folder; only gzipped *.xml.gz saves are "
                             "read, a plain .xml save is not)")
    pv.add_argument("pattern", help="regular expression matched case-insensitively against the "
                                    "variable name, with and without its stored leading $, e.g. "
                                    "allFactions or ^mymod_")
    pv.add_argument("save", nargs="?", help="save name, e.g. save_020, or a path "
                                            "(default: newest)")

    from . import _savecensus
    pn = sub.add_parser(
        "census",
        help="count <component> elements (ships, turrets, stations, modules...) grouped by macro, "
             "owner, class or parent, with the scanned denominator (pass a save NAME)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Count every <component> in a save, filtered by class/owner/macro globs and grouped by\n"
            "the --by keys. Read-only and streamed. Pass a save NAME (save_016), not a path into\n"
            "the save folder: the save guard refuses a path given to a program it cannot see into.\n"
            "An owner is the component's owner=, else its nearest ancestor component's. 'Parent' is\n"
            "the nearest ancestor of --parent-class, searched up to the component's own station or\n"
            "ship_* (so a turret on a docked ship belongs to the ship) unless --parent-anywhere."),
        epilog=(
            "examples:\n"
            "  # Xenon ships by macro\n"
            "  x4save census save_016 --class 'ship_*' --owner xenon --by macro\n"
            "  # Xenon turrets by macro, split station vs ship (parent defaults to station,ship_*)\n"
            "  x4save census save_016 --class turret --owner xenon --by parent-class,macro\n"
            "  # Xenon L turrets by station module macro, and the station they are on\n"
            "  x4save census save_016 --class turret --owner xenon --macro 'turret_xen_l_*' \\\n"
            "      --parent-class @module --by macro,parent-macro,station-macro\n"
            "\n"
            "exit: 0 matches found, 1 no component matched (the scanned count is printed),\n"
            "2 the save could not be read or an argument was refused."))
    pn.add_argument("save", nargs="?", help="save name, e.g. save_016, or a path (default: newest)")
    pn.add_argument("--class", dest="classes", action="append", metavar="GLOB",
                    help="component class glob(s), comma-separated or repeated, case-insensitive "
                         "(e.g. 'ship_*', turret, station)")
    pn.add_argument("--owner", dest="owners", action="append", metavar="GLOB",
                    help="owner faction glob(s) (e.g. xenon); matched against the inherited owner")
    pn.add_argument("--macro", dest="macros", action="append", metavar="GLOB",
                    help="macro glob(s) (e.g. 'turret_xen_l_*')")
    pn.add_argument("--by", default="macro", metavar="KEYS",
                    help="comma-separated group keys from: " + ", ".join(_savecensus.KEYS) + " (default: macro)")
    pn.add_argument("--parent-class", dest="parent_classes", action="append", metavar="GLOB",
                    help="which ancestor counts as the parent for parent-class/parent-macro (default "
                         "station,ship_*); @module = the station-module classes ("
                         + ",".join(_savecensus.MODULE_CLASSES) + ")")
    pn.add_argument("--parent-anywhere", action="store_true",
                    help="let the parent and station-macro search continue past the component's own "
                         "station or ship_* (e.g. a docked ship's turret then finds the dock module)")
    pn.add_argument("--limit", type=int, default=0, help="groups to print, largest first (default 0 = all)")
    pn.add_argument("--json", action="store_true", help="machine-readable output (schema x4save.census/1)")

    args = p.parse_args(argv)
    if args.cmd == "census":
        try:
            by = _savecensus.parse_by(args.by)
            parents = _savecensus.split_list(args.parent_classes)
            _savecensus.expand_parent(parents or list(_savecensus.DEFAULT_PARENT))
            if args.limit < 0:
                raise _savecensus.CensusArgumentError("--limit must be 0 (all) or more")
        except _savecensus.CensusArgumentError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if args.cmd == "vars":
        try:
            re.compile(args.pattern)
        except re.error as exc:
            print(f"error: invalid pattern {args.pattern!r}: {exc}", file=sys.stderr)
            return 2
    try:
        path = resolve_save(args.save)
        if args.cmd == "info":
            return cmd_info(path)
        if args.cmd == "vars":
            return cmd_vars(path, args.pattern)
        if args.cmd == "census":
            return _savecensus.cmd_census(
                path, classes=_savecensus.split_list(args.classes), owners=_savecensus.split_list(args.owners),
                macros=_savecensus.split_list(args.macros), parent_classes=parents,
                parent_anywhere=args.parent_anywhere, by=by, limit=args.limit, as_json=args.json)
        return cmd_check(path, limit=args.limit)
    except (SaveUnreadable, _effective.EmptyReference) as exc:
        # rc 2, never 0: a save that could not be read is a NON-ANSWER.
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
