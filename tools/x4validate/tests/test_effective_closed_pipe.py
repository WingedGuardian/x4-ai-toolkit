"""`x4effective ... | head` on Windows: a closed pipe arrives as OSError EINVAL, not BrokenPipeError."""
import errno
import subprocess
import sys

from x4validate import _effectivecli as cli


def test_broken_pipe_is_a_closed_reader():
    assert cli._reader_gone(BrokenPipeError(errno.EPIPE, 'x'))


def test_einval_with_no_filename_is_a_closed_reader():
    assert cli._reader_gone(OSError(errno.EINVAL, 'x'))


def test_TWIN_einval_from_a_file_operation_is_a_real_error():
    assert not cli._reader_gone(OSError(errno.EINVAL, 'x', 'some:bad?name'))


def test_TWIN_another_write_error_is_a_real_error():
    assert not cli._reader_gone(OSError(errno.ENOSPC, 'x'))


CHILD = '''
import sys
from x4validate import _effectivecli as cli
try:
    while True:
        print('x' * 200)
except OSError as exc:
    sys.stderr.write('READER-LEFT' if cli._reader_gone(exc) else 'REAL-ERROR')
    cli._quiet_stdout()
'''


def test_a_real_closed_pipe_is_recognised_and_the_exit_is_quiet():
    """A stub cannot stand in here: what a real closed pipe raises, and what flush does next, is the question."""
    p = subprocess.Popen([sys.executable, '-c', CHILD], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p.stdout.readline()
    p.stdout.close()
    err = p.stderr.read().decode('utf-8', 'replace')
    p.stderr.close()
    assert p.wait(timeout=120) == 0, err
    assert err.endswith('READER-LEFT'), err  # an import may print a toolkit banner first
