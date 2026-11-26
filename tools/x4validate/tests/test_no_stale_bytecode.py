"""A cached .pyc must not disagree with its source.

WHY THIS EXISTS -- MEASURED 2026-09-12, and it produced WRONG VERDICTS. CPython
invalidates a cached .pyc on (source mtime in SECONDS, source size). A mutation run
that writes a SAME-LENGTH mutant and restores it within the same second leaves the
MUTANT's bytecode in place: the source reads correct, `cmp` says byte-identical, and
the next import silently executes the mutant. Four mutation verdicts were reported
before this was caught, and the only tell was one test failing against code that
had just been proven identical to its backup.

`gates/mutation_probe.py:428` already clears __pycache__ for exactly this reason and
says so in capitals. The defect was reproduced anyway, by hand-rolling a harness
instead of using it. So this is the check that REFUSES -- the only thing that has
ever actually helped, per the register's own conclusion.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
#: Every tree holding importable .py under this package. NOTE the detector maps
#: source -> cache via `cache_from_source`, so pytest's assertion-rewritten
#: `*-pytest-*.pyc` under tests/ are invisible to it; those are covered by
#: PYTHONDONTWRITEBYTECODE=1 at the process boundaries (run-gates.sh,
#: verify-cold.sh, ci.yml), not here.
SCANNED = ("gates", "x4validate", "tests", "scripts")


def test_the_scan_has_a_denominator():
    """A glob that matched nothing would make every assertion below vacuously true."""
    files = [p for d in SCANNED for p in (ROOT / d).glob("*.py")]
    assert len(files) >= 30, [p.name for p in files]


def test_no_cached_bytecode_disagrees_with_its_source():
    """The real trees. This is the assertion that would have caught the incident."""
    bad = stale_pycs([ROOT / d for d in SCANNED])
    assert not bad, bad


def test_it_detects_a_planted_stale_pyc(tmp_path):
    """FALSIFICATION. Reproduces the incident exactly: compile, then change the
    source by the SAME number of bytes and put its mtime back, so (mtime_sec, size)
    still match and CPython would reuse the stale bytecode."""
    src = tmp_path / "m.py"
    src.write_bytes(b"VALUE = 2\n")
    _compile(src)
    _rewrite_same_length_same_mtime(src, b"VALUE = 0\n")
    assert stale_pycs([tmp_path]), "a planted stale .pyc was not detected"


def test_a_fresh_pyc_is_not_reported(tmp_path):
    """The other direction: a correctly-cached file must not be flagged, or the
    check floods and gets ignored."""
    src = tmp_path / "ok.py"
    src.write_bytes(b"VALUE = 1\n")
    _compile(src)
    assert stale_pycs([tmp_path]) == []


def test_a_source_with_no_pyc_is_not_reported(tmp_path):
    (tmp_path / "bare.py").write_bytes(b"VALUE = 1\n")
    assert stale_pycs([tmp_path]) == []


# ---------------------------------------------------------------------------
# the detector
# ---------------------------------------------------------------------------
#: A timestamp .pyc header is magic(4) + flags(4) + mtime(4) + size(4). A flags
#: low bit of 1 means HASH-based invalidation, where mtime/size are not consulted
#: and this whole hazard cannot arise -- those are skipped rather than guessed at.
_HEADER = 16


def _compile(src: pathlib.Path) -> pathlib.Path:
    """Compile to the normal cache location, timestamp invalidation."""
    import py_compile
    return pathlib.Path(py_compile.compile(
        str(src), doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP))


def _rewrite_same_length_same_mtime(src: pathlib.Path, new: bytes) -> None:
    """Reproduce the incident: same byte length, mtime second unchanged."""
    import os
    st = src.stat()
    old = src.read_bytes()
    assert len(new) == len(old), "the plant must be the same length to be realistic"
    src.write_bytes(new)
    os.utime(src, (st.st_atime, st.st_mtime))


def stale_pycs(roots) -> list[str]:
    """Cached bytecode that CPython would REUSE but that no longer matches its source.

    Returns human-readable rows, not booleans: a caller must be able to name the file.
    """
    import importlib.util
    import struct

    bad: list[str] = []
    for root in roots:
        for src in sorted(pathlib.Path(root).glob("*.py")):
            cached = pathlib.Path(importlib.util.cache_from_source(str(src)))
            if not cached.is_file():
                continue                      # no cache: nothing to disagree
            raw = cached.read_bytes()
            if len(raw) < _HEADER:
                bad.append(f"{cached.name}: truncated header ({len(raw)} bytes)")
                continue
            flags, mtime, size = struct.unpack("<III", raw[4:_HEADER])
            if flags & 0b1:
                continue                      # hash-based: immune by construction
            st = src.stat()
            if mtime != int(st.st_mtime) & 0xFFFFFFFF or size != st.st_size & 0xFFFFFFFF:
                continue                      # CPython will recompile: not a hazard
            # CPython WOULD reuse this. Does it actually match the source?
            # Compare CODE OBJECTS compiled under the CACHED co_filename, never raw
            # marshal bytes: marshal embeds co_filename, i.e. the importer's PATH
            # SPELLING. MEASURED (review, 2026-09-13): a correct cache written via a
            # forward-slash sys.path entry was reported stale against a backslash
            # recompile on an UNCHANGED file -- a flooding detector, which is the
            # shape that gets ignored.
            import marshal
            try:
                cached_code = marshal.loads(raw[_HEADER:])
            except (ValueError, EOFError, TypeError) as exc:
                bad.append(f"{cached.name}: unreadable bytecode ({exc})")
                continue
            fresh_code = compile(src.read_bytes(), cached_code.co_filename, "exec",
                                 dont_inherit=True)
            # Compare by VALUE (code-object equality), not by re-marshalled bytes either:
            # MEASURED 2026-11-25 on CPython 3.13.14, `marshal.dumps(marshal.loads(raw)) != raw`
            # for an UNCHANGED, correctly cached `_inputs.py` (marshal's back-reference flags
            # depend on object identity at dump time), so the byte comparison reported two
            # unchanged files stale in two checkouts and refused every push. Code equality still
            # separates a same-length mutant (see the planted-file test below).
            if fresh_code != cached_code:
                bad.append(
                    f"{src.name}: cached bytecode differs from its source while "
                    f"(mtime={mtime}, size={size}) still match, so CPython would "
                    f"REUSE it -- delete {cached.parent.name}/")
    return bad
