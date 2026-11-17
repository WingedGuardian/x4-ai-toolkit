r"""The ONE way this toolkit runs an XQuery through the vendored BaseX jar.

Why this exists (issue #7, MEASURED 2026-10-10 on a cp1252 Windows host, BaseX 12.4,
Java 24, raw bytes per channel; the console props re-MEASURED 2026-10-12 on Temurin
17.0.20.1 AND Java 24.0.1 -- see JVM_PROPS):

  * stdout  BaseX serializes its RESULT as UTF-8 bytes. `text=True` decoded them as
            cp1252: U+706B (bytes E7 81 AB) hit the undefined byte 0x81, the reader
            thread died, `stdout` came back None and the caller raised AttributeError.
            A result whose bytes all happen to be valid cp1252 was silently MANGLED.
  * stderr  BaseX's error text goes through System.err in the PLATFORM charset unless
            the console props in JVM_PROPS are set: `e` + U+00E9 arrived as one byte
            0xE9, U+706B as `?`.
  * input   a `-q` ARGUMENT is decoded by the JVM in the platform charset: U+706B
            arrived as `?` (string-to-codepoints gave 63, not 28779). So a query for a
            non-cp1252 literal ran as a query for `?`, matched a literal `?`, and a zero
            described a query nobody wrote.

The query therefore travels as a UTF-8 FILE, never as an argument. BaseX takes a file
as its positional input; MEASURED: the literal round-trips (28779) with and without a
BOM, under `-Dfile.encoding=` UTF-8, windows-1252 and COMPAT alike. No parsing or
escaping of the query text is involved (the rejected alternative was rewriting
non-cp1252 characters inside string literals as `&#N;` -- which needs a literal-boundary
parser and cannot express a non-cp1252 NAME at all).

The file is written into *cwd* (the BaseX directory, which BaseX already writes its
`.basex` configuration into) so that relative URIs resolve exactly as they did against
the old `-q` static base URI, the working directory: MEASURED `resolve-uri('x.xml')`
identical both ways. `static-base-uri()` itself now names the file rather than the
directory. The file is this tool's own temporary and is deleted after the run.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

#: JVM properties pinning BaseX's own console streams (System.out/System.err) to UTF-8.
#: MEASURED 2026-10-12, BaseX 12.4, cp1252 host, raw bytes, on Temurin 17.0.20.1 and 24.0.1:
#:   * `stdout/stderr.encoding` are the JEP 400 props (Java 19+). Java 17 IGNORES them: an
#:     error naming U+706B still arrived as `?` (CI run 38287706366, windows-latest).
#:   * `sun.stdout/sun.stderr.encoding` are what Java 17 honours; Java 24 honours them too.
#:     Without the stdout one, Java 17 also mangled BaseX's own System.out text (`-V` info).
#:   * NOT needed on either version: the query FILE already decodes as UTF-8 and the RESULT
#:     is written as raw UTF-8 bytes with no prop at all. `-Dfile.encoding=UTF-8` was
#:     rejected: it fixed stderr on 17 but NOT on 24, and it changes the JVM-wide default
#:     charset rather than just the two console streams.
#: All four stay: the `sun.*` pair is undocumented JDK-internal, so the documented pair is
#: kept for when a later JDK drops it. A structural test holds the argv to this set.
JVM_PROPS = ("-Dstdout.encoding=UTF-8", "-Dstderr.encoding=UTF-8",
             "-Dsun.stdout.encoding=UTF-8", "-Dsun.stderr.encoding=UTF-8")


# --------------------------------------------------------------------------- the build heap
# ONE place for how much Java heap a BaseX BUILD may use, and what that costs the job wrapper.
#
# WHY (MEASURED 2026-11-17, Java 24, 32 GB host): a `java` started with no -Xmx may grow its heap to a quarter of
# physical RAM (8.5 GB here, `-XX:+PrintFlagsFinal` MaxHeapSize 8547991552) and does not collect before it has to. The
# builds sat at about 3.3 GB for weeks and then one reached 6219 MB, which is over the 6 GB cap the job wrapper
# (X4_JOB_WRAPPER, rb) gave x4fresh's builders, so the wrapper killed x4eff (2026-11-17) and x4raw (2026-11-16). With
# `-Xmx4g` x4eff built in the same 277 s, peak job memory 3985 MB. x4raw at -Xmx4g peaked at 4409 MB in 371 s (MEASURED 2026-11-17, whole job: staging
# python + JVM). The cap now comes from the heap, not from history.
#
# Every build-time java launch carries `-Xmx<heap>`: the two build scripts (through `heap-flag` below), dbswap's
# BaseXRunner and coverage.py (both through `heap=True`; coverage.py runs only inside the two build scripts). A query
# run by ask.py is NOT a build and keeps the JVM's default heap.
#
# PRECEDENCE (MEASURED 2026-11-17, `java -Xmx2g -XX:+PrintFlagsFinal -version`): an `-Xmx` on the command line beats
# JAVA_TOOL_OPTIONS (set to -Xmx1g: MaxHeapSize 2147483648, {command line}), so a JAVA_TOOL_OPTIONS heap cannot lift
# the cap. `_JAVA_OPTIONS` is the opposite: it beats the command line (set to -Xmx1g: MaxHeapSize 1073741824). A user
# who sets `_JAVA_OPTIONS=-Xmx...` is therefore choosing the heap themselves; x4fresh's cap would then be wrong, so do
# not set it for BaseX builds. Change the heap with X4_BASEX_HEAP, which the cap follows.
HEAP_ENV = "X4_BASEX_HEAP"
DEFAULT_HEAP = "4g"
#: What a build's JVM can hold beyond its heap, in MB: metaspace, code cache (240 MB reserved by default), GC
#: bookkeeping (G1 remembered sets and mark bitmaps, INFERRED at 10-20% of the heap, so up to about 800 MB at 4g),
#: thread stacks, direct buffers and BaseX's own native use, plus the bash/python/tee processes of the same job (about
#: 100 MB). 1.5 GiB covers that sum with room. MEASURED anchor, not a derivation: at -Xmx4g the whole job peaked at
#: 3985 MB, UNDER the 4096 MB heap limit, so the real overhead at this load is well below this figure. It is a
#: margin on purpose: the cap's job is to stop a runaway, never to sit near a normal run.
JVM_NON_HEAP_MB = 1536
_HEAP_RE = re.compile(r"^([1-9][0-9]*)([mMgG])$")


#: Smallest heap a BaseX build is allowed: below it BaseX cannot hold its own working set and the build dies late with an
#: OutOfMemoryError instead of being refused at the start.
MIN_HEAP_MB = 256
#: Upper bound when the machine's physical RAM cannot be read: a heap above it is refused as a typo.
FALLBACK_MAX_HEAP_MB = 64 * 1024


def _physical_ram_mb() -> int | None:
    """Physical RAM in MB, or None when it cannot be read. Windows: GlobalMemoryStatusEx. POSIX: sysconf pages x page
    size. No third-party module, so a plain python can ask."""
    try:
        if os.name == "nt":
            import ctypes

            class _Mem(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total_phys", ctypes.c_ulonglong),
                            ("avail_phys", ctypes.c_ulonglong), ("total_page", ctypes.c_ulonglong),
                            ("avail_page", ctypes.c_ulonglong), ("total_virt", ctypes.c_ulonglong),
                            ("avail_virt", ctypes.c_ulonglong), ("avail_ext", ctypes.c_ulonglong)]
            mem = _Mem()
            mem.length = ctypes.sizeof(_Mem)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
                return None
            return int(mem.total_phys // (1024 * 1024))
        return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // (1024 * 1024))
    except (OSError, ValueError, AttributeError):
        return None   # silent-ok: an unreadable RAM size falls back to the fixed upper bound, which heap_setting names


def heap_setting() -> str:
    """The heap size to give a build's JVM, e.g. `4g`: X4_BASEX_HEAP, else the default.

    Refused (ValueError) when it is not `<number>m` or `<number>g`, is under MIN_HEAP_MB, or is above the machine's
    physical RAM (FALLBACK_MAX_HEAP_MB when that cannot be read): a typo must stop a build before its long work, not
    fall back to a default the user did not choose, nor reach java as a flag it reads differently."""
    # env-ok: a per-run build tuning switch (JVM heap), not a configured location
    raw = os.environ.get(HEAP_ENV, "").strip()
    value = raw or DEFAULT_HEAP
    m = _HEAP_RE.match(value)
    if not m:
        raise ValueError(f"{HEAP_ENV}={value!r} is not a heap size: write a whole number of megabytes or gigabytes "
                         f"such as 4g or 6144m (default {DEFAULT_HEAP})")
    mb = int(m.group(1)) * (1024 if m.group(2).lower() == "g" else 1)
    if mb < MIN_HEAP_MB:
        raise ValueError(f"{HEAP_ENV}={value!r} is under {MIN_HEAP_MB}m: BaseX cannot build in so little heap")
    ram = _physical_ram_mb()
    limit, what = (ram, f"this machine's {ram}m of physical RAM") if ram else (
        FALLBACK_MAX_HEAP_MB, f"{FALLBACK_MAX_HEAP_MB}m (physical RAM could not be read)")
    if mb > limit:
        raise ValueError(f"{HEAP_ENV}={value!r} is above {what}")
    return value.lower()


def heap_mb() -> int:
    number, unit = _HEAP_RE.match(heap_setting()).groups()
    return int(number) * (1024 if unit.lower() == "g" else 1)


def heap_flag() -> str:
    """The one JVM flag that caps a build's heap: `-Xmx4g` by default."""
    return f"-Xmx{heap_setting()}"


def job_memory_gb() -> str:
    """What a job wrapper should reserve and cap for a BaseX build: the heap plus JVM_NON_HEAP_MB, in GB (GiB), as the
    shortest decimal (`5.5`), rounded UP to 0.01 so the figure never undercuts the heap it covers. Independent of any
    earlier run: the heap bounds it."""
    gb = math.ceil((heap_mb() + JVM_NON_HEAP_MB) * 100 / 1024) / 100
    return f"{gb:.2f}".rstrip("0").rstrip(".")


def _jvm_args(heap: bool) -> list[str]:
    return [heap_flag()] if heap else []


def _text(raw: bytes, errors: str) -> str:
    # Text mode used to normalise newlines; bytes do not, and BaseX on Windows ends
    # every line with CRLF. Line splitting and the zero-count guard depend on `\n`.
    return raw.decode("utf-8", errors=errors).replace("\r\n", "\n")


def run_query(xquery: str, cwd: Path, heap: bool = False) -> tuple[int, str, str]:
    """Run *xquery* with BaseX in *cwd*; return (returncode, stdout, stderr) as text.

    `heap=True` (a query run as part of a build) adds the build heap flag; an interactive query keeps the default.

    stdout is decoded STRICTLY: a result that is not valid UTF-8 raises
    UnicodeDecodeError rather than reaching a caller as altered text. stderr is
    diagnostic only, so it is decoded with replacement and never raises.
    """
    try:
        fd, name = tempfile.mkstemp(prefix="ask-", suffix=".xq", dir=cwd)
    except OSError as exc:
        # M-d (v4.0.0 delta review): a BaseX folder this user cannot write (an install under
        # Program Files, a read-only share) made mkstemp raise PermissionError through every
        # caller as a traceback. RuntimeError is what ask.py and coverage.py already report
        # as a refusal (rc 2, with a reason).
        raise RuntimeError(
            f"cannot write the temporary query file into the BaseX folder {cwd} "
            f"({type(exc).__name__}: {exc}). The query travels as a UTF-8 file in that "
            f"folder (issue #7), so it must be writable by this user; no query was run"
        ) from exc
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(xquery.encode("utf-8"))
        # M-c (v4.0.0 delta review): the file by its NAME, never its absolute path. cwd IS
        # its folder, and the JVM decodes argv in the platform charset (the channel issue #7
        # moved the QUERY off) -- an absolute path would carry a non-ASCII install folder
        # through it. mkstemp's own name is ASCII (prefix, suffix, and [a-z0-9_]).
        out = subprocess.run(["java", *_jvm_args(heap), *JVM_PROPS, "-cp", "BaseX.jar", "org.basex.BaseX",
                              path.name],
                             cwd=cwd, capture_output=True, check=False)
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    err = _text(out.stderr, "replace")
    try:
        text = _text(out.stdout, "strict")
    except UnicodeDecodeError as exc:
        # RuntimeError is what every caller already reports as "BaseX query failed",
        # so an undecodable result is refused with a reason instead of a traceback.
        raise RuntimeError(f"BaseX output is not valid UTF-8 ({exc}); no result was "
                           f"accepted") from exc
    return out.returncode, text, err


def run_command(command: str, cwd: Path, heap: bool = False) -> tuple[int, str]:
    """Run one BaseX COMMAND (`ALTER DB a b`, `DROP DB a`, `INFO`) in *cwd*; return (returncode, output text).

    `heap=True` (a command run as part of a build or its swap) adds the build heap flag.

    A command travels as a `-c` ARGUMENT, the channel issue #7 moved queries off: the JVM decodes argv in the
    platform charset. So only ASCII is accepted here -- dbswap's commands name the toolkit's own databases and
    nothing else -- and anything else is refused rather than sent through a channel that may alter it.
    """
    if not command.isascii():
        raise RuntimeError(f"refusing a non-ASCII BaseX command (argv is decoded in the platform charset): "
                           f"{command!r}")
    out = subprocess.run(["java", *_jvm_args(heap), *JVM_PROPS, "-cp", "BaseX.jar", "org.basex.BaseX", "-c", command],
                         cwd=cwd, capture_output=True, check=False, timeout=600)
    return out.returncode, _text(out.stdout, "replace") + _text(out.stderr, "replace")


def main(argv: list[str] | None = None) -> int:
    """`heap-flag` prints the JVM flag for a build script (`-Xmx4g`); `job-gb` prints the memory a job wrapper should
    reserve and cap. Exit 2 with the reason on stderr when X4_BASEX_HEAP is not a heap size."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in ("heap-flag", "job-gb"):
        print("usage: basex_run.py heap-flag | job-gb", file=sys.stderr)
        return 2
    try:
        print(heap_flag() if args[0] == "heap-flag" else job_memory_gb())
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
