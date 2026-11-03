"""`x4effective ... | head` and `x4save ... | head`: the REAL CLI on a REAL closed pipe, with throwaway inputs.

A stub stdout has no exit flush for the interpreter to fail on, and a child snippet proves only what the snippet does:
review round 2 found `dump` outside the guarded block (traceback, rc 1) and `ls`/`find`/`coverage` failing at the exit
flush (rc 120) while a snippet-level test was green. Two shapes, each with a reader that STAYS as its twin:

    reader takes one line, then leaves   the write itself fails while the command is still printing
    reader gone before the child starts  no print fails; only the last flush can (the rc-120 shape)

The other half is that a REAL failure stays one: EINVAL from a file operation used to read as "the reader left".
"""
from __future__ import annotations

import errno
import gzip
import os
import subprocess
import sys

import pytest

from x4validate import _closedpipe, _effectivecli, _savecli


def _macro(i):
    return (f'<macros><macro name="ship_{i}_macro" class="ship_xl"><properties><identification name="{{1,1}}"/>'
            f'</properties></macro></macros>').encode()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    base = tmp_path_factory.mktemp("closed_pipe")
    ref = base / "reference"
    (ref / "t").mkdir(parents=True)
    (ref / "t" / "0001-l044.xml").write_bytes(b'<language id="44"><page id="1"><t id="1">Shipname</t></page></language>')
    macros = ref / "assets" / "units" / "size_xl" / "macros"
    macros.mkdir(parents=True)
    for i in range(2400):
        (macros / f"ship_{i}_macro.xml").write_bytes(_macro(i))
    (ref / "libraries").mkdir()
    (ref / "libraries" / "wares.xml").write_bytes(
        b"<wares>" + b"".join(b'<ware id="w%d" name="n"><x a="1"/></ware>\n' % i for i in range(6000)) + b"</wares>")
    (ref / "libraries" / "small.xml").write_bytes(b"<small/>")
    exts = base / "extensions"
    exts.mkdir()
    db = base / "effective.db"
    env = {k: v for k, v in os.environ.items() if not k.startswith("X4_")}
    env.update(X4_REFERENCE=str(ref), X4_EXTENSIONS=str(exts), X4_EFFECTIVE_DB=str(db), X4_MODS=str(base / "mods"),
               PYTHONDONTWRITEBYTECODE="1")
    built = subprocess.run([sys.executable, "-m", "x4validate._effectivecli", "--db", str(db), "build",
                            "--reference", str(ref)], capture_output=True, text=True, env=env, timeout=300)
    assert built.returncode == 0, built.stderr
    head = '<?xml version="1.0" encoding="UTF-8"?>\n<savegame>\n<info><save name="x" date="1"/>'
    head += '<game id="X4" version="900" build="1" time="3600" start="s"/><player name="p" money="1"/><patches>'
    head += "".join(f'<patch extension="ext{i}" version="1"/>' for i in range(6000)) + "</patches></info><universe>"
    # Names nothing defines, so `check` has a long list to print; one that is defined, so it has a denominator.
    head += "".join(f'<component macro="gone_{i}_macro"/>' for i in range(6000))
    head += '<component macro="ship_1_macro"/></universe></savegame>'
    big = base / "big.xml.gz"
    big.write_bytes(gzip.compress(head.encode()))
    small = base / "small.xml.gz"
    small.write_bytes(gzip.compress(b'<savegame><info><save name="s"/><game time="1"/><player money="1"/></info>'
                                    b'<universe><component macro="ship_1_macro"/></universe></savegame>'))
    return dict(env=env, db=str(db), big=str(big), small=str(small))


def _argv(world, tool, args):
    if tool == "x4effective":
        return [sys.executable, "-m", "x4validate._effectivecli", "--db", world["db"], *args]
    return [sys.executable, "-m", "x4validate._savecli",
            *[world[a[1:]] if a.startswith("@") else a for a in args]]


def _with_a_reader_that_leaves(world, tool, args, when):
    proc = subprocess.Popen(_argv(world, tool, args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=world["env"])
    got = b""
    if when == "after one line":
        got = proc.stdout.readline()
    proc.stdout.close()
    err = proc.stderr.read().decode("utf-8", "replace")
    proc.stderr.close()
    return proc.wait(timeout=300), err, got


def _with_a_reader_that_stays(world, tool, args):
    done = subprocess.run(_argv(world, tool, args), capture_output=True, env=world["env"], timeout=300)
    return done.returncode, done.stderr.decode("utf-8", "replace"), done.stdout


def _assert_quiet(err):
    for bad in ("Traceback", "Exception ignored", "OSError", "BrokenPipe", "decompression"):
        assert bad not in err, err


PIPE_HOLDS = 64 * 1024
MID_WRITE = [
    ("x4effective", ["dump", "libraries/wares.xml"]),
    ("x4effective", ["dump", "libraries/wares.xml", "--chain"]),
    ("x4effective", ["ls", "macro", "--limit", "0"]),
    ("x4save", ["info", "@big"]),
    ("x4save", ["check", "@big", "--limit", "6000"]),
]
EXIT_FLUSH = [
    ("x4effective", ["dump", "libraries/small.xml"]),
    ("x4effective", ["ls", "macro", "--limit", "5"]),
    ("x4effective", ["find", "shipname", "--limit", "5"]),
    ("x4effective", ["coverage"]),
    ("x4effective", ["--help"]),
    ("x4save", ["info", "@small"]),
    ("x4save", ["check", "@small"]),
]


@pytest.mark.parametrize("tool,args", MID_WRITE, ids=lambda v: v if isinstance(v, str) else " ".join(v))
def test_a_reader_that_leaves_while_it_prints_is_a_quiet_rc_0(world, tool, args):
    rc, err, first = _with_a_reader_that_leaves(world, tool, args, "after one line")
    assert first, "the control: the reader saw output before it left"
    _assert_quiet(err)
    assert rc == 0, err


@pytest.mark.parametrize("tool,args", MID_WRITE, ids=lambda v: v if isinstance(v, str) else " ".join(v))
def test_a_mid_write_case_prints_more_than_a_pipe_holds(world, tool, args):
    """A child that fits in the pipe can finish before the reader leaves, and then the case above tests only the exit.
    Linux holds 64 KiB in a pipe by default. The twin of that test's name, so the fixture cannot shrink silently."""
    _rc, _err, out = _with_a_reader_that_stays(world, tool, args)
    assert len(out) > PIPE_HOLDS * 2, f"{len(out)} bytes fit too easily"


@pytest.mark.parametrize("tool,args", EXIT_FLUSH, ids=lambda v: v if isinstance(v, str) else " ".join(v))
def test_a_reader_gone_before_the_last_flush_keeps_the_commands_own_answer(world, tool, args):
    """The command finished, so its verdict stands: a closed pipe must not turn a DEGRADED 3 into 0, or anything into 120."""
    stayed_rc, _, out = _with_a_reader_that_stays(world, tool, args)
    assert out, "the twin: with a reader there, the same command prints"
    rc, err, _ = _with_a_reader_that_leaves(world, tool, args, "at once")
    _assert_quiet(err)
    assert rc == stayed_rc, err


def test_a_reader_that_stays_gets_the_whole_output(world):
    """The twin of every test above: the watcher must not eat a line, least of all the last one."""
    rc, _, out = _with_a_reader_that_stays(world, "x4effective", ["ls", "macro", "--limit", "0"])
    assert rc == 0 and out.count(b"ship_") >= 400
    rc, _, out = _with_a_reader_that_stays(world, "x4save", ["info", "@big"])
    assert rc == 0 and b"ext2999" in out


# ------------------------------------------------------------- a real failure is not a closed pipe

class _Broken:
    """A stdout whose write fails from the `after`-th call on."""

    def __init__(self, exc, after=0):
        self.exc, self.after, self.calls = exc, after, 0

    def write(self, _s):
        self.calls += 1
        if self.calls > self.after:
            raise self.exc
        return 0

    def flush(self):
        pass

    def fileno(self):
        raise OSError("no fd")


def test_a_full_disk_on_stdout_is_not_a_closed_pipe(monkeypatch):
    monkeypatch.setattr("sys.stdout", _Broken(OSError(errno.ENOSPC, "No space left on device")))
    with pytest.raises(OSError, match="No space"):
        _closedpipe.quiet_exit(lambda: print("x"))()


@pytest.mark.parametrize("exc", [BrokenPipeError(errno.EPIPE, "x"), OSError(errno.EINVAL, "x")])
def test_both_spellings_of_a_closed_pipe_on_stdout_are_one(monkeypatch, exc):
    """POSIX raises the first, Windows the second."""
    monkeypatch.setattr("sys.stdout", _Broken(exc))
    assert _closedpipe.quiet_exit(lambda: print("x") or 7)() == 0


def test_einval_that_names_a_file_is_not_the_closed_pipe_shape(monkeypatch):
    """The clause's twin: a closed pipe carries no filename; a path that came through a stream's write would."""
    monkeypatch.setattr("sys.stdout", _Broken(OSError(errno.EINVAL, "x", "some:bad?name")))
    with pytest.raises(OSError) as raised:
        _closedpipe.quiet_exit(lambda: print("x"))()
    assert not isinstance(raised.value, _closedpipe.OutputClosed)


def test_a_broken_pipe_to_something_else_is_not_stdout_closing():
    """A child's stdin closing is a real failure of what talks to the child: only stdout's own write is quiet."""
    def talks_to_a_child():
        raise BrokenPipeError(errno.EPIPE, "the child left")
    with pytest.raises(BrokenPipeError):
        _closedpipe.quiet_exit(talks_to_a_child)()


def test_a_usage_error_still_exits_with_its_own_code(world):
    """argparse leaves by SystemExit; the wrapper flushes on the way out and must not turn a 2 into a return of 0."""
    done = subprocess.run(_argv(world, "x4effective", ["no-such-subcommand"]), capture_output=True, env=world["env"])
    assert done.returncode == 2


def test_stdout_is_put_back_whatever_happens(monkeypatch):
    stream = _Broken(OSError(errno.EINVAL, "x"))
    monkeypatch.setattr("sys.stdout", stream)
    _closedpipe.quiet_exit(lambda: print("x"))()
    assert sys.stdout is stream


def test_einval_from_a_file_operation_is_a_real_failure_in_every_x4effective_branch(world, tmp_path, monkeypatch):
    """`f.seek(-5)` raises OSError(EINVAL) with no filename, the same shape as Windows's closed pipe, and it read as
    "the reader left": rc 0 and nothing said."""
    src = tmp_path / "f.bin"
    src.write_bytes(b"x")

    def seeking(*_a, **_k):
        with open(src, "rb") as f:
            f.seek(-5)

    for k, v in world["env"].items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(_effectivecli, "_cmd_dump", seeking)
    with pytest.raises(OSError) as dumped:
        _effectivecli.main(["--db", world["db"], "dump", "x.xml"])
    assert dumped.value.errno == errno.EINVAL
    monkeypatch.setattr(_effectivecli, "_read_store", seeking)
    with pytest.raises(OSError) as read:
        _effectivecli.main(["--db", world["db"], "coverage"])
    assert read.value.errno == errno.EINVAL


def test_a_store_that_will_not_open_still_says_so(tmp_path, world, monkeypatch, capsys):
    """Twin: the store-failure label survives; only the stdout path became a quiet exit."""
    for k, v in world["env"].items():
        monkeypatch.setenv(k, v)
    bad = tmp_path / "not-a-store.db"
    bad.write_bytes(b"garbage, not sqlite" * 100)
    assert _effectivecli.main(["--db", str(bad), "coverage"]) == 2
    assert "effective store unreadable" in capsys.readouterr().err


def test_a_write_error_while_listing_vars_is_not_called_a_broken_save(tmp_path, monkeypatch, capsys):
    """_savecli.py:250 relabelled ANY OSError raised through the parser, so a full disk read "decompression/XML parse
    failed", rc 2: the save blamed for the destination."""
    save = tmp_path / "s.xml.gz"
    save.write_bytes(gzip.compress(b'<savegame><md><script name="s"><cue name="c"><vars>'
                                   b'<value name="$a" type="integer" value="1"/></vars></cue></script></md></savegame>'))
    monkeypatch.setattr("sys.stdout", _Broken(OSError(errno.ENOSPC, "No space left on device"), after=2))
    with pytest.raises(OSError, match="No space"):
        _savecli.main(["vars", "a", str(save)])
    assert "decompression" not in capsys.readouterr().err


def test_a_real_read_failure_is_still_an_unreadable_save(tmp_path, capsys):
    """Twin: the relabel existed for THIS, and it stays."""
    save = tmp_path / "s.xml.gz"
    save.write_bytes(b"not gzip at all")
    assert _savecli.main(["vars", "a", str(save)]) == 2
    assert "decompression/XML parse failed" in capsys.readouterr().err
