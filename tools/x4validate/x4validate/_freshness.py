r"""When was this derived artifact last TRUE — and if it is not, WHAT moved?

Coverage answers *how much* was indexed. Nothing answered *as of when*, so an
artifact that no longer describes the world reported success indefinitely — a
third case beside absence and non-answer: **an answer about a world that has
moved on.**

**The case that produced this module.** BaseX's `x4eff` was built 2026-08-02. The
merge engine was then fixed twice — root-`<replace>` ops were being dropped while
reported applied (2026-08-08, 858 ops, VRO alone 848) and nested cross-mod
patches were invisible from one of two doors (2026-08-11). **Neither date changed
one input file**, so any check watching only inputs would have called the index
fresh. MEASURED after rebuilding: **140 of 194 (72%)** engine thrust rows moved,
and a design decision recorded on 08-02 had quoted VANILLA engine values as if
they were VRO's.

Hence two axes:

``content``  the installed world — every extension's identity, its manifest, and
             the mtime/size of every engine-relevant file it ships, plus a
             reference-tree marker.
``engine``   the CODE that produced the artifact, hashed from source BYTES. A git
             commit does not move for a dirty tree, and merge changes here are
             routinely used before they are committed.

Only artifacts *derived through the merge* are engine-dependent. A raw file index
is not, and flagging it anyway would train the reader to ignore the warning —
which is how a noisy check ends up worse than no check.

One implementation, imported by everyone: `_effective` (the sqlite store),
`_xref` (its TSV index) and `tools/basex/staleness.py` all use this. The
DLC-enumeration bug was written five times precisely because each caller rolled
its own copy.

--------------------------------------------------------------------------------
WHAT THE CONTENT AXIS COVERS, AND WHY IT WAS WIDENED (2026-08-25)
--------------------------------------------------------------------------------

It used to stat ONLY each folder's ``content.xml``. That made it blind to a mod
whose FILES changed while its manifest did not — which is the ordinary
overlay-deploy workflow: edit ``libraries/god.xml``, copy it in, manifest
untouched.

    MEASURED 2026-08-25: **72 of 121 non-DLC mods (59.5%)** had at least one file
    newer than their own ``content.xml``. Demonstrated directly against the old
    code: a file-only edit returned the IDENTICAL digest ``03122df005f47fbd``
    before and after.

Every artifact therefore reported **FRESH** while describing a different tree.
That is a false FRESH — it fails in the UNSAFE direction, unlike F53's false
STALE, which merely nags.

**Cost of closing it, MEASURED** (`os.walk`, three stable runs; an earlier figure
of 1.73 s was ~92% process-spawn overhead from a bash loop and is logged as
checker-bug #48):

===========================================  =======  =========
variant                                        files       time
===========================================  =======  =========
whole ``extensions\`` tree                    15,523     0.14 s
filtered to .xml/.cat/.dat/.lua                2,042     0.060 s
filtered + per-file triples + tree_sha         2,042     0.092 s
sha256 of all 129 ``content.xml``                129     0.008 s
===========================================  =======  =========

**The suffix filter is CORRECTNESS, not speed.** MEASURED: 8 mods would otherwise
move the fingerprint purely from ``README``/``CHANGES``/``LICENSE``/
``live_editor_log.json`` churn — manufactured staleness, which trains the reader
to ignore the banner, which is how a noisy check ends up worse than none.

**SCOPE LIMIT, stated because a tool that cannot distinguish a guess from a
measurement is a defect.** Per-file identity is ``(relpath, mtime, size)``, NOT a
content hash: hashing every file would mean reading VRO's multi-hundred-MB
``ext_01.cat`` on every CLI run. Consequences, both real:

* an edit that changes NEITHER size NOR mtime is invisible;
* an edit that keeps the size but moves the mtime is classified ``touched``
  rather than ``content``.

Manifests ARE hashed (``manifest_sha``, 8 ms for all 129) because they are small
and because separating *touched* from *actually changed* is exactly what defeated
us in the ``distances`` incident.

--------------------------------------------------------------------------------
WHY THE VECTOR IS PERSISTED (F56)
--------------------------------------------------------------------------------

A digest says THAT the world moved, never WHAT. MEASURED 2026-08-25: localising
one changed mod (``distances``, v160 -> v170) took **nine investigative steps and
three false leads**, and succeeded only because that mod happened to bump its
``version``. A content-identical rewrite with a new mtime would have been
undiagnosable.

So ``content_detail`` computes the per-folder vector and ``hash_content`` folds
it. The vector is persisted beside the digest (149 KB for 121 mods / 2,042
files), which is what lets ``x4modlist changed --files`` name exact files with no
dependency on a possibly-stale BaseX index or on ``_cat``.

``distances`` also rewrote its 26 files **back-dated** two days. A ``max(mtime)``
signal is precisely what back-dating defeats — ``find -newermt`` failed on this
for that reason — so the per-file triples are hashed as a SET, and an mtime
moving BACKWARD registers just as a forward one does.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sized
from dataclasses import dataclass, field
from pathlib import Path

#: Modules that decide what the EFFECTIVE tree contains. Editing any of them
#: changes the answer for identical inputs.
#:
#: `_effective.py` was ADDED 2026-08-22 after a near-miss. The first five decide
#: what a document MERGES TO; `_effective.py` decides which documents and which
#: entities exist at all (`base_vpaths`, `reference_vpaths`, `macro_vpaths`,
#: `component_vpaths`, `build_touch_map`). An enumeration change moves the store
#: for byte-identical inputs just as surely as a merge change does.
#:
#: The near-miss, stated plainly because it is the only reason this line is
#: right: F34's fix edited `reference_vpaths` itself, and the store's
#: `fingerprint_engine` did not move -- `gates/claims_audit.py` returned 21/21
#: green against a store built by the OLD enumeration. It was correct only
#: because set-equality had been proven by hand (4,002 and 7,551 vpaths, 0
#: added / 0 removed / 0 changed). Nothing in the toolkit would have caught the
#: other outcome, which is exactly the eleven-day `x4eff` failure that produced
#: this contract in the first place.
#: `_registry.py` was ADDED 2026-08-22 alongside `_effective.py`, for the same
#: reason one step further out: it decides WHICH MODS EXIST. `_registry.mods()`
#: is now the single definition of "active" (what the engine loads) vs
#: "installed" (what is on disk), and moving a mod between those two worlds
#: changes the effective tree for byte-identical inputs just as surely as a merge
#: change does. MEASURED the day it was written: one installed-but-disabled mod
#: moved x4eff by 10 documents and x4compat by 4 collision rows.
#:
#: NOTE `_freshness.py` is deliberately NOT in this list. It does not decide what
#: the tree contains, so widening the CONTENT axis here does not pretend the
#: merge code changed -- that distinction is the whole of F53's complaint.
ENGINE_SOURCES = ("_merge.py", "_cat.py", "_effective.py", "_registry.py",
                  "_loadorder.py", "_provenance.py", "_modfiles.py")
#: ⚠ RE-DERIVED 2026-09-24 BY TRACING A STORE BUILD (AUDIT-2026-09-24 FR-2), not by
#: reading imports -- `tests/test_audit0924_analysis.py::test_FR2_*` pin it both ways:
#:   * ADDED `_provenance.py` -- its `Recorder` decides the per-attribute chains the store
#:     keeps; and `_modfiles.py`, which now holds `mod_xml_paths` (lifted out of the CLI
#:     module `_compat`, where `_effective.build_touch_map` called it) and the x4eff
#:     enumeration lifted out of `tools/basex/build-effective.py`. The rest of that script
#:     is folded into x4eff's engine axis by `tools/basex/staleness.py::ENGINE_EXTRA`.
#:   * REMOVED `_diff.py` (the x4diff mod-version differ; nothing on the store path imports
#:     it), `_xpath.py` (imported only by `_compat`) and `_scan.py` (the store build reaches
#:     it only for `count_line`, a display helper). Each listed but unused module made an
#:     unrelated edit mark every artifact STALE -- the cry-wolf cost F69 measured.
#: ⚠ THE LOAD-ORDER GAP IS CLOSED (2026-09-06). `compute_load_order` decides which
#: mod wins every collision (CLAUDE.md gotcha #13: the same macro reads 0
#: alphabetically and 200 in true load order), so editing it changes the merged
#: answer for byte-identical inputs -- precisely what this axis is for. It could not
#: be watched while it lived in `_compat.py`, which also carries the `x4compat` CLI:
#: F69 (`tests/test_engine_sources_carry_no_cli.py`) measured that cost twice, a
#: CLI-text change and a DOCSTRING change each invalidating the effective store and
#: BaseX `x4eff` for rebuilds that could not change one row. That is what trains you
#: to ignore a banner.
#:
#: F69's remedy was 'make the POPULATION right, not the hash clever' -- so the two
#: functions now live in `_loadorder.py`, which imports only `pathlib` and
#: `lxml.etree` and is named above. `_compat` re-exports them, so every caller and
#: the `mutation_probe` source-string mutant are unaffected. `_compat.py` itself is
#: still deliberately absent, and still correctly so: it is a CLI module.

#: Files the ENGINE can load. Everything else -- READMEs, changelogs, licences,
#: tool logs -- is excluded, because 8 real mods would otherwise mark every
#: artifact stale for documentation churn. See the module docstring.
ENGINE_FILE_SUFFIXES = frozenset({".xml", ".cat", ".dat", ".lua"})

_PKG = Path(__file__).resolve().parent

#: Distinguishes "read the configured profile" from "there is no profile".
#: Passing `profile=None` explicitly means the latter, and every mod then counts
#: as enabled -- which is also what an unreadable profile must mean (see
#: `_profile_decisions`).
_UNSET = object()


class NoBaseline(Exception):
    """Asked to diff against a baseline that carries no vector.

    Not an error in the world -- an error in the QUESTION. Artifacts built before
    the vector existed cannot answer "what changed", and reporting "nothing
    changed" for them would be a wrong answer where a non-answer is the honest
    one. Same contract as `tools/basex/ask.py` refusing a zero-result without a
    coverage denominator.
    """


def hash_engine(engine_dir: Path | None = None) -> str:
    """Hash the merge-relevant sources, in fixed order.

    A missing file is hashed as ``<ABSENT>`` rather than skipped: silently
    hashing fewer inputs than intended is how a fingerprint stops noticing.
    """
    engine_dir = engine_dir or _PKG
    h = hashlib.sha256()
    for name in ENGINE_SOURCES:
        p = engine_dir / name
        h.update(name.encode())
        h.update(p.read_bytes() if p.is_file() else b"<ABSENT>")
    return h.hexdigest()[:16]


# --- the content vector -------------------------------------------------------

def _as_dirs(dirs) -> list[Path]:
    """Accept one root or many.

    `hash_content` walked a SINGLE directory, so the profile and Workshop roots
    were invisible to it -- the same narrowing shape the blind-spot register
    exists to police. A bare Path is still accepted because four production call
    sites and fifteen tests pass one.
    """
    if dirs is None:
        return []
    if isinstance(dirs, (str, os.PathLike)):
        return [Path(dirs)]
    # A None ENTRY is an unconfigured root (no X4 on this machine), not a directory:
    # Path(None) raised TypeError from every CLI that stamps freshness on a fresh clone.
    return [Path(d) for d in dirs if d is not None]


def _profile_decisions(profile) -> dict[str, bool]:
    """manifest id -> enabled, from the profile content.xml.

    Keyed by MANIFEST ID, never folder name (gotcha #30b: `amphitrite` is the
    folder, `ws_3616342050` the id; MEASURED 60 of 123 match by id, only 9 by
    folder). An unreadable or absent profile yields {} and therefore "everything
    enabled" -- failing OPEN, because failing closed would empty the world model
    on any machine without a profile and report that emptiness as fact.
    """
    if profile is _UNSET:
        from x4validate import _registry
        profile = _registry.PROFILE_CONTENT
    if profile is None:
        return {}
    try:
        from lxml import etree
        root = etree.parse(str(profile)).getroot()
    except Exception:
        # silent-ok: an absent/unreadable profile must FAIL OPEN -- {} means
        # "no decisions recorded", and every mod then counts as enabled, which
        # is what X4 itself does with a folder it has not seen (gotcha #30a).
        # Failing CLOSED would mark all 121 mods disabled and silently shrink
        # the fingerprint's world model to nothing on any machine without a
        # profile. The freshness verdict is unaffected either way: the profile
        # contributes one bit per mod, and a machine with no profile simply has
        # a constant for it.
        return {}
    # ONE parser for the value, shared with `_registry.ingest_content_xml`: an entry
    # with no `enabled` attribute LOADS (MEASURED 2026-09-26, load-order probe case b)
    # and used to be read as disabled here.
    from x4validate._registry import profile_flag
    out: dict[str, bool] = {}
    for ext in root.xpath("//extension[@id]"):
        out[ext.get("id")] = profile_flag(ext.get("enabled"))
    return out


def _read_manifest(manifest: Path) -> dict:
    """id/version/sha/mtime/size for one content.xml, tolerant of malformed XML.

    A mod with a broken manifest must stay in the denominator rather than crash
    the fingerprint or vanish from it -- `id`/`version` simply read as None.
    """
    try:
        body = manifest.read_bytes()
        st = manifest.stat()
    except OSError:
        return {"manifest_id": None, "manifest_version": None,
                "manifest_sha": None, "manifest_mtime": None,
                "manifest_size": None, "no_manifest": True}
    mid = ver = None
    enabled = True
    try:
        from lxml import etree
        root = etree.fromstring(
            body, parser=etree.XMLParser(recover=True, resolve_entities=False))
        if root is not None:
            mid, ver = root.get("id"), root.get("version")
            enabled = str(root.get("enabled", "")).strip().lower() not in ("false", "0")
    except Exception:
        # silent-ok: id/version are CONVENIENCES for localisation, not inputs to
        # the digest. A malformed manifest still contributes its bytes via
        # `manifest_sha` and its files via `tree_sha`, so the mod stays in the
        # denominator and any change to it is still detected -- it just cannot
        # be labelled "version 1.6 -> 1.7". MEASURED 2026-08-23: 12 of 4,383
        # mod documents are malformed, so this is a real case, not a defensive
        # hypothetical. Raising here would let one broken manifest take the
        # whole fingerprint down.
        pass
    return {"manifest_id": mid, "manifest_version": ver, "manifest_enabled": enabled,
            "manifest_sha": hashlib.sha256(body).hexdigest()[:12],
            "manifest_mtime": int(st.st_mtime), "manifest_size": st.st_size,
            "no_manifest": False}


def _walk_mod(folder: Path) -> tuple[list[list], int]:
    """Sorted (relpath, mtime, size) for every engine-relevant file, + unreadable.

    A file we could not stat is COUNTED rather than dropped, so the record can
    say it narrowed instead of quietly shrinking -- the one shape every tool
    defect in this workspace has had.
    """
    entries: list[list] = []
    unreadable = 0
    for dirpath, _dirnames, filenames in os.walk(folder):
        for name in filenames:
            if os.path.splitext(name)[1].lower() not in ENGINE_FILE_SUFFIXES:
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                unreadable += 1
                continue
            rel = os.path.relpath(full, folder).replace(os.sep, "/")
            entries.append([rel, int(st.st_mtime), st.st_size])
    entries.sort()
    return entries, unreadable


def content_detail(reference: Path, dirs, profile=_UNSET) -> list[dict]:
    """The per-folder vector the digest is folded from. See the module docstring.

    Sorted by (folder, root) so the fold is deterministic. Note the sort key is a
    PAIR: the same folder name can legally exist in two install roots, and every
    consumer must keep them apart (see `diff_detail`).

    *reference* is accepted but not read here -- the reference marker belongs to
    the FOLD, not to the per-mod vector. It stays in the signature so this and
    `hash_content` take the same arguments, which is what lets a caller swap one
    for the other without re-deriving anything.
    """
    decisions = _profile_decisions(profile)
    out: list[dict] = []
    for root in _as_dirs(dirs):
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if not d.is_dir():
                continue
            rec: dict = {"folder": d.name, "root": str(root)}
            rec.update(_read_manifest(d / "content.xml"))
            entries, unreadable = _walk_mod(d)
            payload = "\n".join(f"{p}|{m}|{s}" for p, m, s in entries)
            rec["files"] = len(entries)
            rec["unreadable"] = unreadable
            rec["tree_sha"] = hashlib.sha256(payload.encode()).hexdigest()[:12]
            # The EFFECTIVE decision despite the key's name (kept: persisted baselines
            # carry it). The profile decides when it has an entry; otherwise the
            # manifest's own `enabled` is the default (MEASURED 2026-09-26, load-order
            # probe case a). Defaulting an absent entry to True hashed "no entry" and
            # "enabled=true" identically for a manifest-disabled mod -- which the engine
            # does NOT load in the first case and DOES in the second.
            rec["enabled_in_profile"] = decisions.get(rec["manifest_id"],
                                                      rec.get("manifest_enabled", True))
            rec["entries"] = entries
            out.append(rec)
    out.sort(key=lambda r: (r["folder"].lower(), r["root"]))
    return out


def _fold(detail: list[dict], reference: Path) -> str:
    """The ONE place a vector becomes a digest.

    Written once on purpose. Two independent code paths answering the same
    question is the single most repeated defect shape in this toolkit -- the
    nested-patch door gave Tier B and `x4effective` contradictory answers, each
    internally consistent. `hash_content` and `fingerprint` both fold through
    here so they cannot drift apart.
    """
    h = hashlib.sha256()
    if not detail:
        h.update(b"<NO-EXTENSIONS-DIR>")
    for rec in detail:
        h.update(rec["folder"].lower().encode())
        # The ROOT as well as the folder (AUDIT-2026-09-24 FR-1). Moving a mod from the
        # game-root extensions folder to the profile's is exactly the mistake CLAUDE.md
        # warns about -- dependencies then resolve as MISSING -- and it left the digest
        # unchanged, so every artifact read FRESH while `diff_detail` over the same two
        # vectors reported removed+added. Normalised (absolute, case-folded, one
        # separator) so two callers spelling the same root differently do not cry wolf.
        root = rec.get("root", "")
        h.update(b"@" + os.path.normcase(os.path.abspath(root)).replace("\\", "/")
                 .encode() if root else b"@")
        if rec["no_manifest"]:
            h.update(b"<NO-MANIFEST>")
        else:
            h.update(f"{rec['manifest_mtime']}:{rec['manifest_size']}"
                     f":{rec['manifest_sha']}".encode())
        h.update(f":{rec['tree_sha']}:{int(rec['enabled_in_profile'])}".encode())
    h.update(_reference_survey(Path(reference)).encode())
    return h.hexdigest()[:16]


def reference_digest(reference: Path) -> str:
    """The REFERENCE side of the content axis on its own (AUDIT-2026-09-24 FR-4).

    `_fold` mixes the per-mod records with the reference survey into one digest, so a
    game update and a mod edit produced the same banner -- "a mod was added, removed,
    updated..." -- and `x4modlist changed` then said "no change" for the move the banner
    had sent you to find. Stamped alongside `content` so `compare` can say WHICH side
    moved. Absent from stamps written before 2026-09-24; `compare` then falls back to the
    undivided message rather than guessing.
    """
    return hashlib.sha256(_reference_survey(Path(reference)).encode()).hexdigest()[:16]


#: How many directory levels of `reference/` the survey folds in. At each level the
#: survey folds the CHILD COUNT and the child NAMES, so folding depth D detects an
#: add or a delete at depth D+1.
#:
#: ⚠ This said "a directory's mtime moves when its DIRECT children change" until
#: 2026-09-08. That mechanism was REMOVED by the commit that fixed the CI flake --
#: see the '★ NO DIRECTORY MTIME' note in `_reference_survey`, where the directory
#: mtime was found to be the only value in the fold that could move without the
#: tree moving (1 unstable run in 400 over an unchanged tree). The prose survived
#: the mechanism it described, which is the same shape as the timing figures below
#: being measured on a proxy: a comment is an artifact and it rots.
#:
#: MEASURED 2026-09-05 by TIMING THIS FUNCTION on the real 510,711-file tree -- not a
#: proxy walk. My first estimate came from a bare scandir traversal and said depth 3
#: cost 0.019 s; the real function, which also stats every entry and lists every child,
#: costs 0.441 s. Measuring the neighbouring thing is how a design gets chosen wrong:
#:     depth 1   0.002 s   <- what this was; it detected adds/deletes only at depth 2
#:     depth 2   0.018 s   <- chosen: 9x, still trivial against a per-query check
#:     depth 3   0.441 s   <- 220x. "A check too slow to run protects nothing."
#:
#: STILL BLIND, stated rather than implied: an add or delete at depth 4 or deeper
#: (e.g. `assets/units/deep/ship.xml`), and an in-place edit to any file except the
#: marker below. Both are covered in practice by the recorded build id -- a game patch
#: plus re-unpack rewrites the tree -- and `reference/` is policy-locked read-only, so
#: an in-place edit is a violation rather than an event.
_SURVEY_DEPTH = 2


def _reference_survey(reference: Path) -> str:
    """A cheap shape-print of the reference tree.

    THE PROBLEM. This used to be `(mtime, size)` of ONE file --
    `libraries/wares.xml` -- standing in for 510,711 files and 27 GB. Anything that
    changed a DLC macro, a script, or an index while leaving that one file alone moved
    nothing, so the store could be reported fresh by BOTH axes and still describe a
    world that had changed.

    WHY NOT A FULL WALK. MEASURED on this tree: a stat of every file is 17.6 s, a
    name-only walk 0.80 s, and this survey 0.001 s. The check runs on every query, and
    a check too slow to run protects nothing -- which is why the single marker existed
    in the first place. This is 1,760x cheaper than the full walk and covers all 20
    top-level entries instead of one file.

    WHAT IT SEES: every top-level entry; each directory's CHILD COUNT and the
    sorted names of its direct children; each top-level file's (mtime, size).
    Directory mtimes are deliberately NOT folded -- see the note at the fold. A re-unpack, a
    game patch, an added or removed DLC, or a rename all move it.

    WHAT IT DOES NOT SEE, and this is a LIMIT OF THE APPROACH, not of the effort: an
    in-place edit to an existing file (except the marker below). MEASURED -- changing a
    file's contents moves NO ancestor directory's mtime at any depth, so the only way to
    see it is to stat every file, which is the 17.6 s this exists to avoid.

    ⚠ AND, until 2026-09-05, an ADD or DELETE below depth 2 -- which is not an in-place
    edit and was not covered by that sentence. The survey folded only the top level, so
    `libraries/sub/new.xml` appearing moved nothing, and essentially all reference
    content sits deeper than depth 2. Folding to _SURVEY_DEPTH now covers adds and
    deletes to depth 3; depth 4 and below remain blind, and that is now stated at the
    constant instead of being implied away.

    That is acceptable HERE and nowhere else, for one reason: `reference/` is
    policy-locked read-only behind `.unpacked-and-locked`, so an in-place edit is a
    violation rather than an event. The change that actually happens is a game patch
    followed by a re-unpack, which rewrites the tree -- new names, new mtimes -- and
    changes the recorded build id below. That case is covered exactly.
    """
    h = hashlib.sha256()
    try:
        entries = sorted(os.scandir(reference), key=lambda e: e.name.lower())
    except OSError:
        return "ref:<ABSENT>"
    def _fold(items, depth: int) -> None:
        """Fold one directory level into the hash, recursing to _SURVEY_DEPTH.

        A directory's mtime moves when its DIRECT children change, so folding the
        mtimes of every directory down to depth D detects an add or a delete at any
        depth up to D+1. At depth 1 -- what this did until 2026-09-05 -- that is only
        depth 2, and essentially all reference content sits deeper.
        """
        for e in sorted(items, key=lambda x: x.name.lower()):
            try:
                st = e.stat()
            except OSError:
                h.update(("?" + e.name.lower()).encode())
                continue
            h.update(e.name.lower().encode())
            if e.is_dir(follow_symlinks=False):
                try:
                    kids = sorted(os.scandir(e.path), key=lambda x: x.name.lower())
                except OSError:
                    kids = []
                # ★ NO DIRECTORY MTIME. It was the ONLY value here that could
                # move without the tree moving, and it carries nothing the child
                # names do not already carry.
                #
                # MEASURED 2026-09-07, 400 freshly-built trees, two surveys taken
                # back to back with NO filesystem change between them:
                #
                #                     unstable   add@3   del@3   edit@1
                #   with dir mtime           1     400     400     400
                #   without                  0     400     400     400
                #
                # Identical detection, zero instability. A directory mtime moves
                # when a child is added, removed or renamed -- all three of which
                # also move `len(kids)` or the name list -- and does NOT move for
                # a child's in-place edit, which this docstring already states.
                # So it was redundant for detection and load-bearing for the flake.
                #
                # WHY IT MATTERS BEYOND A FLAKY TEST: this survey feeds the CONTENT
                # axis. A value that changes without the world changing means the
                # axis can report a spurious STALE -- an artifact declared out of
                # date over a tree nobody touched. It surfaced as
                # `test_the_depth_limit_is_STATED_not_implied` failing 1 of 2 COLD
                # runs on the leg that GATES the release, which would have reddened
                # a release for a reason unrelated to it.
                #
                # (On Windows `DirEntry.stat()` returns metadata cached by the
                # directory scan, and a freshly created directory's entry can be
                # read before the OS has settled it. That is the INFERRED
                # mechanism; the measurement above stands without it.)
                h.update(f"d:{len(kids)}:".encode())
                h.update(hashlib.sha256(
                    chr(10).join(k.name for k in kids).encode()).digest())
                if depth < _SURVEY_DEPTH:
                    _fold(kids, depth + 1)
            else:
                h.update(f"f:{int(st.st_mtime)}:{st.st_size}".encode())

    _fold(entries, 1)
    # The marker file the SINGLE-FILE survey used to watch. Restored 2026-09-05: the
    # move to a shape-print was presented as a strict widening and was in fact a TRADE
    # -- an in-place edit to this one file moved the OLD axis and moved nothing in the
    # new one. One stat buys that case back.
    try:
        mst = (reference / "libraries" / "wares.xml").stat()
        h.update(f"marker:{int(mst.st_mtime)}:{mst.st_size}".encode())
    except OSError:
        h.update(b"marker:<absent>")
    # The build the tree was unpacked FROM. A game update under an old reference is the
    # staleness that matters most, and it is the one an mtime survey can miss entirely
    # if the unpack preserved timestamps.
    for cand in (reference.parent / ".claude" / ".reference-buildid",
                 reference / ".reference-buildid"):
        try:
            h.update(b"build:" + cand.read_bytes().strip())
            break
        except OSError:
            # silent-ok: this loop IS the search -- the first candidate not existing is
            # the ordinary case, not a failure, and a machine with no recorded build id
            # simply contributes no build term. The fingerprint stays well-defined
            # either way, and `test_the_recorded_build_id_is_part_of_it` pins that a
            # present id really does reach it.
            continue
    return h.hexdigest()[:16]


def hash_content(reference: Path, dirs, profile=_UNSET) -> str:
    """Fold :func:`content_detail` into one digest.

    Keyed on identity + manifest + every engine-relevant file's (mtime, size),
    rather than a full byte hash: reading 60 GB on every query would make the
    check too expensive to run, and a check nobody runs protects nothing.
    """
    return _fold(content_detail(reference, dirs, profile=profile), reference)


def _entry_map(rec: dict) -> dict[str, tuple[int, int]]:
    return {p: (m, s) for p, m, s in rec.get("entries", ())}


def diff_detail(before, after) -> list[dict]:
    """Localise a moved fingerprint: which mod, and how.

    Kinds, in precedence order — ``added``, ``removed``, ``toggled`` (profile
    enable-list), ``version`` (manifest version moved), ``content`` (a file's
    SIZE moved, or the manifest bytes did), ``touched`` (mtime moved, bytes did
    not). The content/touched split is why `manifest_sha` is stored beside the
    mtime; without it the `distances` incident could not have been called either
    way.

    Raises :class:`NoBaseline` when *before* is None, rather than returning [] —
    an artifact predating the vector cannot answer this, and "nothing changed"
    would be a wrong answer where a non-answer is the honest one.
    """
    if before is None:
        raise NoBaseline(
            "the baseline carries no content vector (built before the vector "
            "existed), so WHAT changed cannot be derived from it -- rebuild the "
            "artifact, or take a snapshot, to establish one")
    # Keyed by (root, folder), NEVER by folder alone. The same folder name can
    # exist in two install roots (game-root vs profile vs Workshop), and a
    # folder-keyed dict silently collapses them last-write-wins. That is exactly
    # checker-bug #47: a change-detector keyed by FOLDER met a mod shipping two
    # manifests and invented a phantom identity change -- the most alarming shape
    # it could have produced. Same family, so it does not get to happen twice.
    def _key(r):
        return (r.get("root", ""), r["folder"])

    old = {_key(r): r for r in before}
    new = {_key(r): r for r in after}
    changes: list[dict] = []

    for key in sorted(set(old) | set(new), key=lambda k: (k[1].lower(), k[0])):
        folder = key[1]
        a, b = old.get(key), new.get(key)
        # `root` travels with every change: with the same folder name legal in
        # two install roots, naming only the folder would make the answer
        # ambiguous exactly when it matters most.
        root = (b or a).get("root", "")
        if b is None:
            changes.append({"folder": folder, "root": root, "kind": "removed",
                            "detail": f"{a['files']} file(s) gone"})
            continue
        if a is None:
            changes.append({"folder": folder, "root": root, "kind": "added",
                            "detail": f"{b['files']} file(s), "
                                      f"version {b.get('manifest_version')}"})
            continue

        am, bm = _entry_map(a), _entry_map(b)
        changed = sorted({p for p in set(am) | set(bm)
                          if am.get(p, (None, None))[1] != bm.get(p, (None, None))[1]})
        touched = sorted({p for p in set(am) & set(bm)
                          if p not in changed and am[p][0] != bm[p][0]})

        if a["enabled_in_profile"] != b["enabled_in_profile"]:
            kind = "toggled"
            detail = ("enabled in the profile" if b["enabled_in_profile"]
                      else "DISABLED in the profile")
        elif a.get("manifest_version") != b.get("manifest_version"):
            kind = "version"
            detail = f"{a.get('manifest_version')} -> {b.get('manifest_version')}"
        elif changed or a["manifest_sha"] != b["manifest_sha"]:
            kind = "content"
            detail = f"{len(changed)} file(s) changed"
        elif touched or a["manifest_mtime"] != b["manifest_mtime"]:
            kind = "touched"
            detail = (f"{len(touched)} file(s) rewritten with identical size "
                      "(mtime only)")
        else:
            continue

        changes.append({"folder": folder, "root": root, "kind": kind,
                        "detail": detail, "files_changed": changed,
                        "files_touched": touched})
    return changes


def fingerprint(config, extensions=_UNSET, engine_dir: Path | None = None,
                profile=_UNSET) -> dict:
    """Both axes for *config*'s world, plus the vector the content axis folds.

    *extensions* defaults to its overlay root. It used to fall back to
    ``Path("")`` -- which is ``Path(".")``, so a bare call hashed whatever
    directory you happened to be standing in and reported it as the installed
    world. That is F46's mechanism, and it produced a wrong number in an audit
    the same day it was registered elsewhere. It now REFUSES: a tool that cannot
    tell a guess from a measurement is a defect, not a limitation.
    """
    ext = extensions
    # AN EMPTY SEQUENCE IS THE SAME ANSWER AS None, AND IT USED TO BE DIGESTED.
    # `_registry.default_installed_dirs()` returns `[]` when NOTHING is configured
    # -- "there is nowhere to look" -- and `[]` is neither None nor _UNSET, so it
    # fell through to `content_detail` and produced a real, stable digest over ZERO
    # enumerated mods. An artifact stamped with it compares equal to itself forever
    # and reports FRESH having looked at nothing, which is the precise failure this
    # whole module exists to refuse.
    #
    # MEASURED 2026-09-08 at 208019e:
    #     extensions=None            -> content=None              mods=0   honest
    #     extensions=[]              -> content=cc9efd07e163e204  mods=0   "fresh"
    #     extensions=[missing dir]   -> content=cc9efd07e163e204  mods=0   same digest
    # The last row is the sharper half: a MISCONFIGURED root and NO root were
    # indistinguishable, so a typo in a path read as a clean world.
    # `Sized` rather than try/len/except: an unmarked `except TypeError: pass` IS a
    # silent swallow, and this repo's own tests/test_no_silent_swallow.py says so --
    # it caught this exact handler. Asking whether the object HAS a length cannot
    # raise, so there is nothing to swallow and nothing to justify.
    if (isinstance(ext, Sized) and not isinstance(ext, (str, Path))
            and len(ext) == 0):
        ext = None
    if ext is None:
        # EXPLICIT None: the caller looked and there is no installed world -- a
        # machine with no game, which is every fresh clone and every CI runner.
        # That is an ANSWER, not a mistake, and it is not the same as OMITTING
        # the argument. Recording it as UNKNOWN keeps the F46 guard intact for
        # the caller who simply forgot, while letting the honest caller through.
        # MEASURED 2026-08-26: conflating the two made 5 tests fail on any
        # machine without X4 installed and turned public CI red for two runs,
        # because `_registry.GAME_EXTENSIONS` is None when nothing is configured
        # and `_effective._ext_root`'s `except AttributeError` never fires -- the
        # attribute exists, its VALUE is None.
        return {"content": None, "engine": hash_engine(engine_dir), "detail": []}
    if ext is _UNSET:
        overlays = list(getattr(config, "overlays", ()) or ())
        if not overlays:
            raise ValueError(
                "fingerprint() cannot infer the extensions root: `config` has no "
                "overlays and no `extensions` was passed. Pass one explicitly "
                "(e.g. _registry.GAME_EXTENSIONS or "
                "_registry.default_installed_dirs()). Refusing to fall back to "
                "the current directory -- that is F46, and it silently hashes "
                "whatever you are standing in.")
        ext = overlays[0].parent
    detail = content_detail(config.reference, ext, profile=profile)
    return {"content": _fold(detail, config.reference),
            "engine": hash_engine(engine_dir), "detail": detail,
            "reference": reference_digest(config.reference)}


@dataclass
class Verdict:
    fresh: bool
    reasons: list[str] = field(default_factory=list)

    def banner(self, what: str) -> str:
        """Text a CLI prints on EVERY run until the artifact is rebuilt."""
        if self.fresh:
            return ""
        return ("\n" + "!" * 78
                + f"\n!! STALE — {what} no longer describes the current world.\n!!   "
                + "\n!!   ".join(self.reasons)
                + "\n!! Its answers describe the world as of the build, not now, and it\n"
                  "!! cannot back a NEGATIVE claim until rebuilt.\n"
                + "!" * 78 + "\n")


def _only_reference_moved(stored: dict, current: dict) -> bool:
    """True only when both per-mod vectors are present and identical, so the content
    move is FULLY explained by the reference side. Unknown is not "only the reference"."""
    a, b = stored.get("detail"), current.get("detail")
    if not a or not b:
        return False
    try:
        return not diff_detail(a, b)
    except NoBaseline:
        # silent-ok: an unlocalisable vector is "cannot prove only the reference moved",
        # and False makes `compare` name the MODS as well -- the conservative verdict.
        return False


def compare(stored: dict | None, current: dict, engine_dependent: bool) -> Verdict:
    """Absent is UNKNOWN, never fresh — see the module docstring."""
    if not stored:
        return Verdict(False, ["no fingerprint recorded — built before freshness "
                               "tracking existed, so it cannot be established"])
    reasons = []
    # An UNKNOWN content axis is not a value to compare, it is the absence of
    # one -- and `None == None` would otherwise make two unknowns MATCH and
    # report FRESH. An artifact that cannot say which world it describes must
    # never claim it still describes it.
    if stored.get("content") is None or current.get("content") is None:
        return Verdict(False, [
            "content axis UNKNOWN: no extensions root was available when this "
            "was stamped or when it was checked, so freshness cannot be "
            "established (it is not fresh, and it is not known to be stale)"])
    # Both reasons quote STORED -> NOW. A consumer's first question on hitting a
    # stale artifact is "moved how far, and by whose change?", and without the two
    # hashes answering it takes a hand-written script against `fingerprint()`.
    # They are also what lets someone match the move against a commit. Raised by a
    # downstream session that hit this for real (F59).
    if stored.get("content") != current.get("content"):
        ref_then, ref_now = stored.get("reference"), current.get("reference")
        split = ref_then is not None and ref_now is not None
        if split and ref_then != ref_now:
            reasons.append("reference changed: the unpacked base game (reference\\) moved "
                           "-- a game update or a re-unpack "
                           f"[{ref_then} -> {ref_now}]")
        # Mods are named unless the split PROVES the whole move was the reference: with
        # the reference unchanged, or with no reference digest to compare, the content
        # move is attributed to the mods exactly as before.
        if not (split and ref_then != ref_now and _only_reference_moved(stored, current)):
            reasons.append("content changed: a mod was added, removed, updated, "
                           "toggled, or one of its files was edited "
                           "(run `x4modlist changed` to see which) "
                           f"[{stored.get('content')} -> {current.get('content')}]")
    if engine_dependent and stored.get("engine") != current.get("engine"):
        reasons.append("engine changed: the merge code that produced this has been "
                       "edited, so the SAME inputs would now merge differently "
                       f"[{stored.get('engine')} -> {current.get('engine')}]")
    return Verdict(not reasons, reasons)


# --- persistence -------------------------------------------------------------

_KEYS = ("fingerprint_content", "fingerprint_engine")
#: Stored separately and read OPTIONALLY. An artifact written before the vector
#: existed must stay readable and keep giving a correct FRESH/STALE verdict --
#: requiring this key would flip every artifact on disk to UNKNOWN at once, which
#: is a far worse failure than not being able to localise.
_DETAIL_KEY = "fingerprint_detail"
#: Optional like `_DETAIL_KEY`, for the same reason: a stamp without it stays readable and
#: keeps its FRESH/STALE verdict; only the reference-vs-mods split is unavailable.
_REFERENCE_KEY = "fingerprint_reference"


def stamp_sqlite(con, fp: dict) -> None:
    """Write into the existing `meta` table — additive, no sidecar to lose."""
    con.execute("create table if not exists meta (key text primary key, value text)")
    for key, val in zip(_KEYS, (fp["content"], fp["engine"])):
        con.execute("insert or replace into meta (key, value) values (?, ?)", (key, val))
    if fp.get("detail") is not None:
        con.execute("insert or replace into meta (key, value) values (?, ?)",
                    (_DETAIL_KEY, json.dumps(fp["detail"], separators=(",", ":"))))
    if fp.get("reference") is not None:
        con.execute("insert or replace into meta (key, value) values (?, ?)",
                    (_REFERENCE_KEY, fp["reference"]))


def read_sqlite(con) -> dict | None:
    try:
        rows = dict(con.execute("select key, value from meta"))
    except Exception:
        return None  # silent-ok: a store with no meta table predates this and is
        # reported as UNKNOWN by `compare`, which is the correct verdict.
    if not all(k in rows for k in _KEYS):
        return None
    out = {"content": rows["fingerprint_content"],
           "engine": rows["fingerprint_engine"], "detail": None,
           "reference": rows.get(_REFERENCE_KEY)}
    if _DETAIL_KEY in rows:
        try:
            out["detail"] = json.loads(rows[_DETAIL_KEY])
        except (TypeError, ValueError):
            out["detail"] = None  # unreadable vector -> NoBaseline, not a wrong answer
    return out


def _sidecar(target: Path) -> Path:
    return Path(str(target) + ".freshness.json")


def stamp_sidecar(target: Path, fp: dict) -> None:
    """For artifacts that are not databases (the x4xref TSV). Payload untouched.

    Replaced, not rewritten in place: a stamp half-written by a killed build would read as UNKNOWN
    and, worse, could be paired with the previous artifact."""
    p = _sidecar(target)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(json.dumps(fp, separators=(",", ":")).encode("utf-8"))
        os.replace(tmp, p)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass  # silent-ok: best effort; the original exception is what the caller needs
        raise


def read_sidecar(target: Path) -> dict | None:
    p = _sidecar(target)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # silent-ok: an unreadable sidecar is exactly the UNKNOWN case, and `compare`
        # renders None as "no fingerprint — freshness cannot be established", which
        # is the conservative verdict. Nothing is swallowed; the caller still warns.
        return None
    if not all(k in data for k in ("content", "engine")):
        return None
    data.setdefault("detail", None)
    return data
