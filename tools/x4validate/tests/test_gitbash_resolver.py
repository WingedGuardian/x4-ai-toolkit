r"""`scripts/gitbash.py` must never hand back the WSL/Store stub.

`scripts/fuzz-guard.py` carried `shutil.which("bash.exe") or shutil.which("bash")` with
a comment stating it avoided the WSL stub. MEASURED 2026-09-01 from PowerShell, BOTH
calls return `C:\Windows\system32\bash.exe` -- the stub is itself named bash.exe. The
guard fuzzer would have run every mutant under WSL, where the `C:/...` paths its seeds
are built from do not exist, so every verdict would have been meaningless AND green.

A defence that names the right threat and does not stop it is worse than none: it stops
the next person looking.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "gitbash.py"


def _mod():
    spec = importlib.util.spec_from_file_location("gitbash_undertest", _SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_module_ships():
    assert _SRC.is_file(), "scripts/gitbash.py is missing: %s" % _SRC


def test_generic_only_install_uses_its_neutral_bash_resolver(tmp_path):
    import shutil
    script = tmp_path / "scripts/gitbash.py"
    script.parent.mkdir()
    shutil.copy2(_SRC, script)
    runtime = tmp_path / "tools/x4_harness"
    runtime.mkdir(parents=True)
    (runtime / "x4guard.py").write_text(
        "def resolve_bash(): return ('fixture-bash', None)\n"
        "def is_stub_bash(path): return False\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("generic_gitbash_control", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.find_bash() == "fixture-bash"


@pytest.mark.skipif(sys.platform != "win32", reason="the stub only exists on Windows")
def test_it_returns_None_rather_than_the_stub(monkeypatch):
    """The branch that matters: nothing but the stub is reachable."""
    m = _mod()
    monkeypatch.setattr(m._resolver(), "git_bash_candidates", lambda: [])   # B1: one resolver
    monkeypatch.delenv("X4_BASH", raising=False)
    monkeypatch.setenv("PATH", r"C:\Windows\system32")
    assert m.find_bash() is None, "handed back the WSL stub"


@pytest.mark.skipif(sys.platform != "win32", reason="windows path shapes")
def test_it_finds_a_real_bash_on_PATH_past_the_stub(monkeypatch, tmp_path):
    """A stub earlier on PATH must not mask a real bash later -- `shutil.which` returns
    only the FIRST hit, which is exactly how the stub won."""
    m = _mod()
    real = tmp_path / "bin"
    real.mkdir()
    (real / "bash.exe").write_bytes(b"MZ")
    monkeypatch.setattr(m._resolver(), "git_bash_candidates", lambda: [])   # B1: one resolver
    monkeypatch.delenv("X4_BASH", raising=False)
    monkeypatch.setenv("PATH", r"C:\Windows\system32;" + str(real))
    assert m.find_bash() == str(real / "bash.exe")


def test_an_explicit_override_wins(monkeypatch, tmp_path):
    m = _mod()
    p = tmp_path / "mybash"
    p.write_bytes(b"#!/bin/sh\n")
    monkeypatch.setenv("X4_BASH", str(p))
    assert m.find_bash() == str(p)


@pytest.mark.skipif(sys.platform != "win32", reason="windows stub dirs")
def test_every_stub_directory_is_rejected(monkeypatch):
    m = _mod()
    for d in (r"C:\Windows\System32\bash.exe",
              r"C:\Windows\SysWOW64\bash.exe",
              r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\bash.exe"):
        assert m._is_stub(d), d
    assert not m._is_stub(r"C:\Program Files\Git\bin\bash.exe")
