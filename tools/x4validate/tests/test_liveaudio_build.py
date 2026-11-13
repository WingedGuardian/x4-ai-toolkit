"""tools/native_audio/build.py cleans up its own outputs after a failed compile (review r1-7).

It refuses to run when its outputs already exist, so a failed compile that left
process_loopback.obj behind blocked every later attempt. The compiler is faked: the
test is about the script's bookkeeping, never about MSVC.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
BUILD = ROOT / "tools" / "native_audio" / "build.py"


def _module():
    spec = importlib.util.spec_from_file_location("native_audio_build_under_test", BUILD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _toolchain(tmp: Path):
    vc = tmp / "vc"
    (vc / "bin").mkdir(parents=True)
    compiler = vc / "bin" / "cl.exe"
    for name in ("cl.exe", "link.exe", "c1xx.dll", "c2.dll"):
        (vc / "bin" / name).write_bytes(b"fake")
    (vc / "include").mkdir()
    (vc / "lib").mkdir()
    sdk = tmp / "sdk"
    for sub in ("ucrt", "shared", "um", "winrt"):
        (sdk / "Include" / "10.0.1" / sub).mkdir(parents=True)
    for name in ("audioclient.h", "audioclientactivationparams.h", "mmdeviceapi.h"):
        (sdk / "Include" / "10.0.1" / "um" / name).write_bytes(b"fake")
    for sub in ("ucrt", "um"):
        (sdk / "Lib" / "10.0.1" / sub / "x64").mkdir(parents=True)
    out = tmp / "out"
    out.mkdir()
    argv = ["build.py", "--compiler", str(compiler), "--vc-include", str(vc / "include"),
            "--vc-lib", str(vc / "lib"), "--sdk-root", str(sdk), "--sdk-version", "10.0.1",
            "--output", str(out)]
    return out, argv


def _fake_run(rc):
    def run(argv, cwd=None, **kwargs):
        out = Path(cwd)
        (out / "process_loopback.obj").write_bytes(b"partial")
        if rc == 0:
            (out / "x4-process-audio.exe").write_bytes(b"built")
        return SimpleNamespace(returncode=rc, stdout=b"fake compiler output")
    return run


def test_failed_compile_removes_its_own_partial_outputs(tmp_path, monkeypatch):
    module = _module()
    out, argv = _toolchain(tmp_path)
    (out / "unrelated.txt").write_bytes(b"keep")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(module.subprocess, "run", _fake_run(2))
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 2
    assert sorted(p.name for p in out.iterdir()) == ["unrelated.txt"]
    # A later attempt is no longer refused for the leftovers.
    monkeypatch.setattr(module.subprocess, "run", _fake_run(0))
    module.main()
    assert (out / "build-receipt.json").exists()


def test_existing_outputs_still_refuse_and_are_not_deleted(tmp_path, monkeypatch):
    # Twin: outputs that were there BEFORE the run are someone's; they are refused, never removed.
    module = _module()
    out, argv = _toolchain(tmp_path)
    (out / "process_loopback.obj").write_bytes(b"earlier")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(module.subprocess, "run", _fake_run(2))
    with pytest.raises(ValueError, match="already exist"):
        module.main()
    assert (out / "process_loopback.obj").read_bytes() == b"earlier"
