r"""The ONE way this toolkit runs an XQuery through the vendored BaseX jar.

Why this exists (issue #7, MEASURED 2026-10-10 on a cp1252 Windows host, BaseX 12.4,
Java 24, raw bytes per channel):

  * stdout  BaseX serializes its RESULT as UTF-8 bytes. `text=True` decoded them as
            cp1252: U+706B (bytes E7 81 AB) hit the undefined byte 0x81, the reader
            thread died, `stdout` came back None and the caller raised AttributeError.
            A result whose bytes all happen to be valid cp1252 was silently MANGLED.
  * stderr  BaseX's error text goes through System.err in the PLATFORM charset unless
            `-Dstderr.encoding=UTF-8` is set: `e` + U+00E9 arrived as one byte 0xE9.
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

import os
import subprocess
import tempfile
from pathlib import Path

#: JVM properties pinning BaseX's own console streams to UTF-8 (Java 18+ honours both).
JVM_PROPS = ("-Dstdout.encoding=UTF-8", "-Dstderr.encoding=UTF-8")


def _text(raw: bytes, errors: str) -> str:
    # Text mode used to normalise newlines; bytes do not, and BaseX on Windows ends
    # every line with CRLF. Line splitting and the zero-count guard depend on `\n`.
    return raw.decode("utf-8", errors=errors).replace("\r\n", "\n")


def run_query(xquery: str, cwd: Path) -> tuple[int, str, str]:
    """Run *xquery* with BaseX in *cwd*; return (returncode, stdout, stderr) as text.

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
        out = subprocess.run(["java", *JVM_PROPS, "-cp", "BaseX.jar", "org.basex.BaseX",
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
