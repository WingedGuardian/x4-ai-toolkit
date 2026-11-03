"""A reader that leaves (`x4effective ls ware | head`) is not a failure of the tool, and nothing else is that.

A closed pipe arrives as BrokenPipeError on POSIX and as a bare OSError(EINVAL) on Windows. Judged by its shape at the
top of a command, that EINVAL is the same as `f.seek(-5)` on a real file, so "the reader left" swallowed real failures.
So it is judged where it happens: `quiet_exit` watches writes to stdout, and only a failure THERE becomes OutputClosed.

It also makes the last flush part of the command. The interpreter's own exit flush fails with "Exception ignored" and
rc 120 when the reader left while the final lines sat in the buffer, which no print can see.

Exit code: a command that finished keeps the verdict it returned (a DEGRADED 3 stays 3). A command cut off while
printing exits 0: what was printed was printed, and the reader that could have used more chose to stop.
"""
from __future__ import annotations

import errno
import functools
import os
import sys


class OutputClosed(BrokenPipeError):
    """The reader of THIS process's stdout went away."""


class _Watched:
    """stdout, with a failed write told apart from every other OSError. Everything else passes through."""

    def __init__(self, stream):
        self._stream = stream

    def _attempt(self, call, *args):
        try:
            return call(*args)
        except OSError as exc:
            if exc.errno in (errno.EPIPE, errno.EINVAL) and exc.filename is None:
                raise OutputClosed(errno.EPIPE, "output closed") from exc
            raise                     # a full disk behind a redirect is a real failure

    def write(self, text):
        return self._attempt(self._stream.write, text)

    def flush(self):
        return self._attempt(self._stream.flush)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _silence(stream) -> None:
    """Python's own recipe: point fd 1 at the null device, so the interpreter's exit flush has nothing to fail on."""
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), stream.fileno())
    except (OSError, ValueError, AttributeError):
        pass  # silent-ok: no real fd 1 to redirect (a captured or replaced stdout); nothing will flush to a closed pipe


def quiet_exit(fn):
    """Wrap a CLI `main`. Goes inside `_paths.refuses_unconfigured`, so the whole command, argparse included, is covered."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        real = sys.stdout
        if real is None:
            return fn(*args, **kwargs)
        sys.stdout = watched = _Watched(real)
        rc = 0
        ended = None
        try:
            try:
                rc = fn(*args, **kwargs)
            except SystemExit as exc:  # argparse: --help or a usage error
                ended = exc
            watched.flush()
        except OutputClosed:
            _silence(real)
        finally:
            sys.stdout = real
        if ended is not None:
            raise ended
        return rc
    return wrapper
