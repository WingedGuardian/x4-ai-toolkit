"""`x4effective ... | head` on Windows: a closed pipe arrives as OSError EINVAL, not BrokenPipeError."""
import errno
import io

import pytest

from x4validate import _effectivecli as cli


class _Stdout(io.StringIO):
    def __init__(self, flush_raises):
        super().__init__()
        self.flush_raises = flush_raises

    def flush(self):
        if self.flush_raises:
            raise OSError(errno.EINVAL, 'Invalid argument')


def test_broken_pipe_is_a_closed_reader():
    assert cli._reader_gone(BrokenPipeError(errno.EPIPE, 'x'))


def test_einval_with_stdout_unwritable_is_a_closed_reader(monkeypatch):
    monkeypatch.setattr(cli.sys, 'stdout', _Stdout(True))
    assert cli._reader_gone(OSError(errno.EINVAL, 'x'))


def test_TWIN_einval_with_stdout_fine_is_a_real_error(monkeypatch):
    monkeypatch.setattr(cli.sys, 'stdout', _Stdout(False))
    assert not cli._reader_gone(OSError(errno.EINVAL, 'x'))


def test_TWIN_another_write_error_is_a_real_error(monkeypatch):
    monkeypatch.setattr(cli.sys, 'stdout', _Stdout(True))
    assert not cli._reader_gone(OSError(errno.ENOSPC, 'x'))
