"""x4diff CLI: argument parsing and rendering for :mod:`x4validate._diff`.

Split out of `_diff.py` for F69. `_diff.py` is an `ENGINE_SOURCE`, so the
freshness fingerprint hashes its BYTES -- which meant editing a help string or a
summary line invalidated the effective store and BaseX `x4eff` exactly as a
merge-semantics change would, while the stale banner asserted "the SAME inputs
would now merge differently". Nothing that only formats output belongs on that
side of the line.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path, PurePosixPath

from x4validate import __version__, _diff, _input, _paths, _scan, _threeway
from x4validate._diff import MANIFEST, FileDiff, ManifestDiff, ModDiff, diff_mods

#: A manifest value longer than this is cut in the summary; `--file` shows it whole.
_MANIFEST_VALUE_WIDTH = 40


def _clip(value: str) -> str:
    return value if len(value) <= _MANIFEST_VALUE_WIDTH else value[:_MANIFEST_VALUE_WIDTH] + "..."


def _manifest_text(m: ManifestDiff) -> str:
    """The manifest answer in one phrase. Every state reads differently."""
    if m.state == "identical":
        text = "identical"
    elif m.state == "differs":
        parts = [f"{a} {_clip(ov)}->{_clip(nv)}" for a, ov, nv in m.root_changes]
        others = m.detail.weight - len(m.root_changes)
        if others:
            parts.append(f"{others} other change(s); --file {MANIFEST} lists them")
        text = f"differs ({', '.join(parts)})"
    elif m.state == "absent-old":
        text = f"absent from {m.old_label}, present in {m.new_label}"
    elif m.state == "absent-new":
        text = f"present in {m.old_label}, absent from {m.new_label}"
    elif m.state == "absent-both":
        text = "absent from both"
    else:
        text = f"NOT COMPARED, and NOT counted as identical - {m.reason or 'no comparison ran'}"
    return f"{text} [{m.note}]" if m.note else text


def _norm_vpath(vpath: str) -> str:
    """A typed path as a vpath: forward slashes, no `./`, no doubled separators."""
    return PurePosixPath(vpath.replace("\\", "/")).as_posix()


def _names_manifest(vpath: str) -> bool:
    """True for the mod-root manifest however it is spelled, and for nothing nested."""
    return _norm_vpath(vpath).lower() == MANIFEST


def _vpath_under(typed: str, roots: list[Path]) -> str:
    """An absolute path under one of the compared folders as its vpath. Anything else is returned as typed."""
    path = Path(typed)
    if not path.is_absolute():
        return typed
    resolved = path.resolve()
    # The innermost folder wins: a compared folder can sit inside another.
    for root in sorted((r.resolve() for r in roots), key=lambda r: len(r.parts), reverse=True):
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:  # silent-ok: not under this root; a path under none stays as typed and is reported
            continue
    return typed


def _fmt_summary(md: ModDiff) -> str:
    ch, ad, rm = md.changed(), md.added(), md.removed()
    lines = [f"# diff  {Path(md.old).name}  ->  {Path(md.new).name}",
             f"  changed files: {len(ch)}   added: {len(ad)}   removed: {len(rm)}",
             # Attribute (and `text()`) changes ONLY. This line used to sum
             # `FileDiff.weight`, which also counts node adds/removes, so one
             # inserted node read as an attribute change (AUDIT-2026-09-24 DF-4).
             # gates/diff_truth.py and gates/cross_tool.py parse this line and
             # plant attribute edits only, so their expected counts are unchanged.
             f"  total attr changes: {sum(len(f.attr_changes) for f in ch)}"
             f"   nodes added: {sum(len(f.nodes_added) for f in ch)}"
             f"   nodes removed: {sum(len(f.nodes_removed) for f in ch)}",
             f"  manifest {MANIFEST}: {_manifest_text(md.manifest)}"]
    if md.unreadable:
        lines.append(f"  NOT COMPARED: {len(md.unreadable)} file(s) — see the list below; "
                     "they are absent from every section, not 'unchanged'")
    return "\n".join(lines)


@_paths.refuses_unconfigured
def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass  # silent-ok: console encoding shim. Failure means the default codec
        # stays; it affects how output LOOKS, never what was examined.
    p = argparse.ArgumentParser(prog="x4diff",
                                description="Semantic XML diff between two mod versions.")
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__}")
    p.add_argument("old", help="pristine/older mod dir (baseline)")
    p.add_argument("new", help="edited/newer mod dir")
    p.add_argument("--overlay", action="append", default=[],
                   help="extra baseline dir merged onto OLD (repeatable; e.g. a submod "
                        "that patches the mod being compared). For a file EVERY layer "
                        "ships as a <diff>, the baseline is the layers' ops concatenated "
                        "in order: it models NEW as ONE merged core+submod patch. If NEW "
                        "is the core alone, the submod's ops show as REMOVED nodes")
    p.add_argument("--detail", action="store_true",
                   help="list every attr change (element text shows as @text())")
    p.add_argument("--file", help="detail one vpath only, or an absolute path under a compared folder; "
                                  "content.xml shows the manifest comparison")
    p.add_argument("--top", type=int, default=30, help="show N heaviest changed files (default: %(default)s)")
    p.add_argument("--base", action="append", default=[],
                   help="COMMON ANCESTOR of OLD and NEW: makes this a THREE-WAY "
                        "diff separating the author's edits from upstream drift "
                        "(repeatable, merged in order)")
    args = p.parse_args(argv)

    # Without this, a mistyped NEW path reports every file as "removed" — i.e.
    # "the new version deleted your whole mod" — with exit 0.
    _input.require_mod_dir(Path(args.old), "OLD (baseline) mod folder")
    _input.require_mod_dir(Path(args.new), "NEW mod folder")
    for o in args.overlay:
        _input.require_mod_dir(Path(o), "--overlay dir")

    if args.file:
        args.file = _vpath_under(args.file, [Path(d) for d in (args.old, args.new, *args.overlay, *args.base)])

    if args.base:
        for b in args.base:
            _input.require_mod_dir(Path(b), "--base (common ancestor) dir")
        return _three_way(args)

    baseline = [Path(args.old)] + [Path(o) for o in args.overlay]
    md = diff_mods(baseline, Path(args.new))
    print(_fmt_summary(md))

    if args.file and _names_manifest(args.file):
        return _print_manifest_file(md.manifest)

    if args.file:
        want = _norm_vpath(args.file).lower()
        fd = next((f for f in md.files if f.vpath.lower() == want), None)
        if fd is not None:
            _print_file(fd)
            return 0
        # `next(...)` returning None meant THREE different things and printed one
        # answer for all of them. MEASURED 2026-09-04: a vpath that exists on NEITHER
        # side printed "no differences" rc 0, and so did a file sitting in the
        # NOT COMPARED list two lines above -- the tool answering "no differences"
        # about the very file it had just said it could not read. The correct
        # handling already exists in this function's three-way sibling below: "that
        # is an ABSENCE only if the file is in the comparison".
        hit = [u for u in md.unreadable
               if u.split(":", 1)[0].strip().lower().endswith(want)]
        if hit:
            print(f"  {args.file}: NOT COMPARED - {hit[0]}")
            return 1
        #
        # "No differences" is earned only by a document `diff_mods` actually compared,
        # so this asks its population. Asking "does any side parse?" answered yes for
        # a type it skips: a changed .xsd read as "no differences".
        suffix = PurePosixPath(want).suffix
        if suffix and (suffix != ".xml" or want.endswith(_diff.IGNORED_SUFFIXES)):
            print(f"  {args.file}: NOT IN THE COMPARISON - x4diff does not compare "
                  f"{suffix} files, so this says nothing about whether it differs.")
            return 1
        compared = _diff.merged_vpaths(baseline).keys() | _diff.mod_xml_vpaths(Path(args.new)).keys()
        if want not in compared:
            print(f"  {args.file}: NOT IN THE COMPARISON - neither side has that "
                  f"vpath. That is an absence of the FILE, not an absence of "
                  f"differences.")
            return 1
        print(f"  {args.file}: no differences")
        return 0

    # Every one of these lists is truncated by --top. A bare list reads as
    # complete, and "removed files" reading as complete is the worst of the three
    # — it says the new version DELETED content it may simply not have listed.
    def _section(title: str, items: list, mark: str, detail: bool = False) -> None:
        shown = items[:args.top] if args.top else items
        print(f"\n## {title}: {_scan.count_line(len(shown), len(items), 'file(s)', '--top')}")
        for f in shown:
            suffix = f"  ({f.weight} changes)" if detail else ""
            print(f"  {mark} {f.vpath}{suffix}")
            if detail and args.detail:
                _print_file(f, indent="      ")

    _section("added files (present only in NEW)",
             sorted(md.added(), key=lambda x: x.vpath), "+")
    _section("removed files (present only in OLD)",
             sorted(md.removed(), key=lambda x: x.vpath), "-")
    _section("changed files (heaviest first)",
             sorted(md.changed(), key=lambda x: -x.weight), "~", detail=True)
    if args.detail and md.manifest.detail is not None:
        print(f"\n## manifest {MANIFEST}:")
        _print_file(md.manifest.detail)
    if md.unreadable:
        print(f"\n## NOT COMPARED ({len(md.unreadable)}):")
        for u in md.unreadable:
            print(f"  ? {u}")
    return 0


def _print_manifest_file(m: ManifestDiff) -> int:
    """`--file content.xml`, answered from the comparison the summary line reports."""
    if m.not_read:
        print(f"  {MANIFEST}: NOT COMPARED - {m.reason or 'no comparison ran'}")
        return 1
    if m.state == "absent-both":
        print(f"  {MANIFEST}: NOT IN THE COMPARISON - neither side has that vpath. That "
              f"is an absence of the FILE, not an absence of differences.")
        return 1
    if m.detail is None:
        print(f"  {MANIFEST}: no differences")
    else:
        _print_file(m.detail)
    return 0


def _print_file(fd: FileDiff, indent: str = "  ") -> None:
    # A whole-file add/remove carries no attr/node rows, so `--file` on one used
    # to print NOTHING about it (AUDIT-2026-09-24 DF-4). Say what it is.
    if fd.status == "added":
        print(f"{indent}{fd.vpath}: ADDED (present only in NEW)")
    elif fd.status == "removed":
        print(f"{indent}{fd.vpath}: REMOVED (present only in OLD)")
    for path, attr, ov, nv in fd.attr_changes:
        print(f"{indent}{path} @{attr}: {ov} -> {nv}")
    for path in fd.nodes_added:
        print(f"{indent}+ {path}")
    for path in fd.nodes_removed:
        print(f"{indent}- {path}")


if __name__ == "__main__":
    raise SystemExit(main())


def _three_way(args) -> int:
    """Render a three-way classification. OLD = archived, NEW = current."""
    base = [Path(b) for b in args.base]
    r = _threeway.three_way(base if len(base) > 1 else base[0],
                            Path(args.old), Path(args.new))

    print(f"# three-way  base={r.base}")
    print(f"#   archived={Path(r.archived).name}   current={Path(r.current).name}")
    # Denominator FIRST. The headline of the case this was built for was
    # "124 of 135 documents verbatim", and a bare change list hides exactly that.
    print(f"  documents shared with the baseline : {r.documents_compared}")
    print(f"    the author edited                : {len(r.author_edited_docs)}")
    print(f"    VERBATIM (author touched nothing): {r.verbatim}")
    print(f"  attributes classified              : {r.attributes_classified}")
    print(f"    author edits ....... {len(r.author_edits)}")
    print(f"    upstream drift ..... {len(r.upstream_drift)}")
    print(f"    converged .......... {len(r.converged)}   (both sides, same change)")
    print(f"    BOTH-MOVED ......... {len(r.both_moved)}   <-- the decisions")
    print(f"  manifest {MANIFEST} base -> archived: {_manifest_text(r.manifest_archived)}")
    print(f"  manifest {MANIFEST} base -> current: {_manifest_text(r.manifest_current)}")
    print("    (the manifest is reported here and never classified above)")

    # A rewritten vpath is a transforming step and says so. Reported BEFORE the
    # exclusions, because it is what moves a document out of them.
    if r.unwrapped:
        print()
        print(f"  UNWRAPPED ({len(r.unwrapped)}): nested `extensions/<baseline>/` prefixes "
              f"removed so the overlay joins the mod it patches:")
        for u in r.unwrapped[:args.top]:
            print(f"    {u}")
        if len(r.unwrapped) > args.top:
            print(f"    ... {len(r.unwrapped) - args.top} more (raise --top)")
    if r.key_collisions:
        print()
        print(f"  KEY COLLISIONS ({len(r.key_collisions)}): two paths collapsed onto one "
              f"join key; the first was kept and the other NOT compared:")
        for c in r.key_collisions[:args.top]:
            print(f"    {c}")

    # Everything excluded from the verdict is NAMED. A silent exclusion is the
    # narrowing step this toolkit refuses.
    if r.no_base:
        print()
        print(f"  NO BASELINE ({len(r.no_base)}): in the archive, absent from the base "
              f"- direction UNKNOWABLE, excluded from every bucket above")
        for v in r.no_base[:args.top]:
            print(f"    {v}")
        if len(r.no_base) > args.top:
            print(f"    ... {len(r.no_base) - args.top} more (raise --top)")
    if r.dropped_by_author:
        print()
        print(f"  NOT IN THE ARCHIVE ({len(r.dropped_by_author)}): in the base, absent "
              f"from the archive - excluded, and NOT reported as upstream drift")
        for v in r.dropped_by_author[:args.top]:
            print(f"    {v}")
        if len(r.dropped_by_author) > args.top:
            print(f"    ... {len(r.dropped_by_author) - args.top} more (raise --top)")
    if r.unreadable:
        print()
        print(f"  NOT COMPARED ({len(r.unreadable)}) - absent from every section above, "
              f"and NOT counted as unchanged:")
        for v in r.unreadable[:args.top]:
            print(f"    {v}")
    if r.node_level:
        print()
        print(f"  node-level changes ({len(r.node_level)}) - reported, not classified:")
        for v in r.node_level[:args.top]:
            print(f"    {v}")

    want = _norm_vpath(args.file).lower() if args.file else ""
    wants_manifest = bool(args.file) and _names_manifest(args.file)

    def _keep(rows):
        return [c for c in rows if not want or c.vpath.lower() == want
                or c.vpath.lower().endswith("/" + want.lstrip("/"))]

    if want:
        print()
        print(f"  --file {args.file}: the counts above are the WHOLE comparison; "
              f"only the listings below are filtered")

    if wants_manifest:
        for side, m in (("archived", r.manifest_archived), ("current", r.manifest_current)):
            if m.detail is not None and m.detail.weight:
                print()
                print(f"  {MANIFEST} base -> {side}:")
                _print_file(m.detail, indent="    ")

    _bm = _keep(r.both_moved)
    if _bm:
        print()
        print("  BOTH-MOVED - both sides changed these, to different values:")
        for c in _bm:
            print(f"    {c.vpath}")
            print(f"      {c.node}@{c.attr}:  base {c.base}  ->  archived {c.archived}"
                  f"  |  upstream {c.current}")

    if args.detail:
        for title, allrows in (("author edits", r.author_edits),
                               ("upstream drift", r.upstream_drift),
                               ("converged", r.converged)):
            rows = _keep(allrows)
            if not rows:
                continue
            print()
            print(f"  {title} ({len(rows)}):")
            for c in rows[:args.top]:
                # The KIND matters most exactly where the sentinel appears: an
                # addition and a removal are not value edits, and a consumer that
                # applied one as a value edit would write the sentinel into the
                # attribute instead of deleting it.
                tag = "" if c.kind in ("author-edit", "upstream-drift") else f"  [{c.kind}]"
                print(f"    {c.vpath}  {c.node}@{c.attr}: {c.base} -> {c.value}{tag}")
            if len(rows) > args.top:
                print(f"    ... {len(rows) - args.top} more (raise --top)")

    if want and not wants_manifest and not any(_keep(rows) for rows in
                        (r.both_moved, r.author_edits, r.upstream_drift, r.converged)):
        print()
        print(f"  --file {args.file}: no classified attribute in that document. "
              f"That is an ABSENCE only if the file is in the comparison - check the "
              f"exclusion lists above.")

    # Exit 1 when there is a decision to make, 0 when the port is mechanical --
    # and "mechanical" cannot be claimed over documents nobody managed to read.
    # `unreadable`, `no_base`, `dropped_by_author` and `key_collisions` were absent
    # from this expression entirely, so a three-way whose ARCHIVE was wholly
    # unparseable exited 0, and so did one against an unrelated baseline (0 shared
    # documents, 0 attributes classified). MEASURED 2026-09-04.
    #
    # Counted unfiltered on purpose: `_keep` filters by --file and reads `c.vpath`,
    # while these four are plain strings -- and the note printed above already says
    # the counts are the WHOLE comparison while only the listings are filtered.
    excluded = (len(r.unreadable) + len(r.no_base) + len(r.dropped_by_author)
                + len(r.key_collisions)
                + r.manifest_archived.not_read + r.manifest_current.not_read)
    if excluded:
        print()
        print(f"  {excluded} document(s) were NOT compared (see the exclusion lists "
              f"above). Exit is non-zero: a port cannot be called mechanical over "
              f"documents nobody read.")
    return 1 if (_keep(r.both_moved) or excluded) else 0
