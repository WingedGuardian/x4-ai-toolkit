"""build-corpus.sh / build-effective.sh refuse a missing or empty reference BEFORE java (#10).

MEASURED/READ on bf2e9dc: build-corpus.sh ran `DROP DB x4raw` (and removed
coverage-x4raw.json) before its `ADD TO /base $REFERENCE`, so a missing reference was
refused only after the old database was gone; an EMPTY reference beside installed mods
would index mods with no base game. build-effective.sh `rm -rf`'d the previous tree
before build-effective.py could refuse. Both now run `reference_check.py` -- the shared
`_effective.require_base_documents` -- before preflight, java or any deletion.

HERMETIC: the scripts are COPIED into tmp_path, so `$HERE/basex` (the BaseX home, its
databases and coverage files) is a scratch directory. Every X4_* variable is dropped;
X4_CONFIG names an empty file. `java` is a stub that records each call, placed first on
PATH in both spellings bash and Windows Python resolve (`java`, `java.cmd`), so "java
was never invoked" is observed, not assumed. The real x4raw/x4eff are never reachable.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
X4VALIDATE = HERE.parent / "x4validate"
_gb = HERE.parents[1] / "scripts" / "gitbash.py"
_spec = importlib.util.spec_from_file_location("gitbash", _gb)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
BASH = _mod.find_bash()

COPIED = ("build-corpus.sh", "build-effective.sh", "_x4v-tree.sh", "preflight.py",
          "reference_check.py")

pytestmark = pytest.mark.skipif(
    BASH is None or shutil.which("uv") is None,
    reason="needs Git Bash and uv on PATH")


@pytest.fixture
def world(tmp_path):
    basex = tmp_path / "tools" / "basex"
    (basex / "basex").mkdir(parents=True)
    for n in COPIED:
        src = HERE / n
        if src.is_file():          # reference_check.py is absent before the fix (RED)
            shutil.copy2(src, basex / n)
    # Sentinels: the previous x4raw licence and the previous x4eff tree + manifest.
    (basex / "basex" / "coverage-x4raw.json").write_text('{"sentinel": 1}', encoding="utf-8")
    (basex / "basex" / "BaseX.jar").write_bytes(b"not a jar")
    tree = basex / "_eff" / "tree"
    (tree / "libraries").mkdir(parents=True)
    (tree / "libraries" / "wares.xml").write_text("<wares id='kept'/>", encoding="utf-8")
    (basex / "_eff" / "effective-manifest.json").write_text('{"sentinel": 1}', encoding="utf-8")

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "java-calls.log"
    (stubs / "java").write_text(
        f'#!/usr/bin/env bash\necho "java $*" >> "{log.as_posix()}"\nexit 97\n',
        encoding="utf-8", newline="\n")
    os.chmod(stubs / "java", 0o755)
    (stubs / "java.cmd").write_text(
        f'@echo off\r\necho java %* >> "{log}"\r\nexit /b 97\r\n', encoding="utf-8",
        newline="")
    ext = tmp_path / "extensions"
    (ext / "somemod" / "libraries").mkdir(parents=True)     # mods present: the case
    (ext / "somemod" / "content.xml").write_text(            # an x4raw over them alone
        '<content id="somemod" version="1"/>', encoding="utf-8")  # would have passed
    (ext / "somemod" / "libraries" / "wares.xml").write_text("<diff/>", encoding="utf-8")
    cfg = tmp_path / "x4-paths.env"
    cfg.write_text("", encoding="utf-8")
    return tmp_path, basex, stubs, log, ext, cfg


def _snapshot(basex: Path) -> dict:
    return {p.relative_to(basex).as_posix(): p.read_bytes()
            for p in basex.rglob("*") if p.is_file() and p.suffix not in (".py", ".sh")}


def _run(world, script: str, ref: Path):
    tmp, basex, stubs, log, ext, cfg = world
    env = {k: v for k, v in os.environ.items() if not k.startswith("X4_")}
    env.update({
        "X4_CONFIG": str(cfg), "X4_REFERENCE": str(ref), "X4_EXTENSIONS": str(ext),
        "X4_GAME": str(tmp / "nogame"), "X4_MODS": str(tmp / "mods"),
        "X4VALIDATE_DIR": X4VALIDATE.as_posix(),
        "PATH": str(stubs) + os.pathsep + env.get("PATH", ""),
    })
    env.pop("VIRTUAL_ENV", None)
    return subprocess.run([BASH, (basex / script).as_posix()], capture_output=True,
                          text=True, env=env, timeout=300)


def _refs(tmp: Path):
    missing = tmp / "missing-ref"
    empty = tmp / "empty-ref"
    (empty / "libraries").mkdir(parents=True)
    good = tmp / "good-ref"
    (good / "libraries").mkdir(parents=True)
    (good / "libraries" / "wares.xml").write_text("<wares><ware id='ore'/></wares>",
                                                   encoding="utf-8")
    return {"missing": missing, "empty": empty, "good": good}


@pytest.mark.parametrize("script", ["build-corpus.sh", "build-effective.sh"])
@pytest.mark.parametrize("shape", ["missing", "empty"])
def test_script_refuses_before_java_and_before_any_deletion(world, script, shape):
    tmp, basex, _stubs, log, _ext, _cfg = world
    ref = _refs(tmp)[shape]
    before = _snapshot(basex)
    p = _run(world, script, ref)
    out = p.stdout + p.stderr
    assert p.returncode == 2, out
    assert not log.exists(), f"java was invoked: {log.read_text()}\n{out}"
    assert _snapshot(basex) == before, "a refused build deleted or changed a previous artifact"
    assert str(ref) in out or ref.as_posix() in out, out


@pytest.mark.parametrize("script", ["build-corpus.sh", "build-effective.sh"])
def test_TWIN_valid_reference_passes_the_check_and_reaches_java(world, script):
    """The control: with a real (minimal) reference the check passes and the script goes
    on to preflight, where the stub java stops it. Without this, a check that refused
    EVERYTHING would pass the test above."""
    tmp, basex, _stubs, log, _ext, _cfg = world
    ref = _refs(tmp)["good"]
    before = _snapshot(basex)
    p = _run(world, script, ref)
    out = p.stdout + p.stderr
    assert p.returncode != 0, "the stub java must stop the script"
    assert "base+DLC XML document(s)" in out, out
    assert log.exists(), f"the script never reached java (preflight)\n{out}"
    assert _snapshot(basex) == before, "preflight must still precede any deletion"
