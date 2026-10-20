"""Isolated X4 tool audit. Production inputs are read-only; all outputs stay in LANE.

Run with the existing toolkit's Python 3.13 environment. This bootstrap installs
an identical copy of itself in the audit worktree. No tool implementation is edited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import tempfile
import tomllib

LANE = Path(__file__).resolve().parents[3]
if not (LANE / ".git").is_file():
    raise RuntimeError("This audit harness requires a dedicated Git worktree")
CONFIG = LANE / "audit-output/local-paths.json"
if not CONFIG.is_file():
    raise RuntimeError("Create audit-output/local-paths.json with source, game, modding, profile paths in the dedicated audit worktree first; see audit/README.md")
LOCAL = json.loads(CONFIG.read_text(encoding="utf-8"))
SOURCE, GAME, MODDING, PROFILE = (Path(LOCAL[k]) for k in ("source", "game", "modding", "profile"))
OUT = LANE / "audit-output"
PKG = LANE / "tools/x4validate"
BASEX = LANE / "tools/basex"
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
PYTHON = PKG / ".venv/Scripts/python.exe"
HERE = LANE / "tools/x4validate/audit"
ISSUES = []
DRIVER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def inside(path: Path, root: Path = LANE) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise RuntimeError(f"REFUSE output outside audit worktree: {resolved}")
    return resolved


def write(path: Path, text: str):
    inside(path).parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def environment(real=True):
    # Explicit overrides outrank personal configs. Never copy API secrets into a log.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("X4_") and k not in ("CLAUDE_PROJECT_DIR", "PYTHONPATH")}
    ref = MODDING / "reference" if real else OUT / "fixture/reference"
    game = GAME if real else OUT / "fixture/game"
    env.update({
        "X4_TOOLKIT": str(LANE), "CLAUDE_PROJECT_DIR": str(LANE),
        "X4_GAME": str(game), "X4_EXTENSIONS": str(game / "extensions"),
        "X4_REFERENCE": str(ref), "X4_PROFILE": str(OUT / "profile"),
        "X4_PROFILE_CONTENT": str(OUT / "profile/content.xml"),
        "X4_PROFILE_EXTENSIONS": str(PROFILE / "extensions") if real else str(OUT / "fixture/profile-ext"),
        "X4_WORKSHOP_CONTENT": str(GAME.parent.parent / "workshop/content/392160") if real else str(OUT / "fixture/workshop"),
        "X4_MODS": str(OUT / "mods"), "X4_REGISTRY": str(OUT / "registry/modlist.yaml"),
        "X4_EFFECTIVE_DB": str(OUT / "effective.sqlite"),
        "X4_DEBUGLOG": str(PROFILE / "debug.txt") if real else str(OUT / "fixture/debug.txt"),
        "PYTHONPATH": str(PKG), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8", "UV_NO_SYNC": "1", "UV_OFFLINE": "1",
        "UV_CACHE_DIR": str(OUT / "uv-cache"), "X4VALIDATE_DIR": str(PKG),
        "PATH": str(PYTHON.parent) + os.pathsep + os.environ.get("PATH", ""),
        "MSYS_NO_PATHCONV": "1", "MSYS2_ARG_CONV_EXCL": "*",
    })
    # Do not change TMP/TEMP: F140 records native Windows child-process hangs.
    return env


def run(label, argv, expect=(0,), *, env=None, cwd=None, timeout=900):
    cwd = PKG if cwd is None else cwd
    OUT.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-zA-Z0-9_.-]", "_", label)
    log = OUT / "logs" / (slug + ".json")
    inside(log).parent.mkdir(exist_ok=True)
    run_id = str(time.time_ns())
    # Keep prior evidence when a corrected harness repeats a named exercise.
    for previous in (log, log.with_suffix(".stdout.txt"), log.with_suffix(".stderr.txt")):
        if previous.exists():
            history = OUT / "logs/history" / run_id / previous.name
            inside(history).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(previous, history)
    t0 = time.monotonic()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=LANE, text=True).strip()
    stdout_path, stderr_path = log.with_suffix(".stdout.txt"), log.with_suffix(".stderr.txt")
    termination_error = None
    try:
        with stdout_path.open("w", encoding="utf-8") as out_stream, stderr_path.open("w", encoding="utf-8") as err_stream:
            proc = subprocess.Popen([str(x) for x in argv], cwd=cwd,
                                    env=env or environment(), stdout=out_stream,
                                    stderr=err_stream)
            proc.wait(timeout=timeout)
            rc = proc.returncode
            timed_out = False
    except subprocess.TimeoutExpired as exc:
        # Kill ONLY this audit's process tree before the parent disappears.
        try:
            killed = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                    capture_output=True, timeout=30, check=False)
            if killed.returncode:
                termination_error = "taskkill returned " + str(killed.returncode)
            proc.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            termination_error = str(error)
        rc, timed_out = None, True
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    row = dict(label=label, run_id=run_id, revision=revision, harness_sha256=DRIVER_SHA256, argv=[str(x) for x in argv], cwd=str(cwd), rc=rc,
               expected=list(expect), seconds=round(time.monotonic()-t0, 3),
               stdout=stdout, stderr=stderr, timeout=timed_out, termination_error=termination_error,
               okay=rc in expect and "Traceback (most recent call last)" not in stdout+stderr)
    write(log, json.dumps(row, ensure_ascii=False, indent=2))
    with (OUT / "ledger.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({k: v for k, v in row.items() if k not in ("stdout", "stderr")}) + "\n")
    print(f"{'OK' if row['okay'] else 'CHECK'} {label}: rc={rc}, {row['seconds']}s, {len(stdout)+len(stderr)} chars", flush=True)
    if not row["okay"]:
        ISSUES.append(label)
        print((stdout+stderr)[-1200:], flush=True)
    if termination_error:
        raise RuntimeError("Audit stopped after termination failure; raw logs retained: " + termination_error)
    return row


def cli(tool, args, label=None, expect=(0,), **kwargs):
    roster = tomllib.loads((PKG / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    module, function = roster[tool].split(":")
    # Exercise the declared entry point in a separate real process.
    code = ("import importlib,sys; m=importlib.import_module(sys.argv[1]); "
            "sys.exit(getattr(m,sys.argv[2])(sys.argv[3:]))")
    return run(label or tool+"-"+"-".join(map(str,args)),
               [PYTHON, "-c", code, module, function, *args], expect, **kwargs)


def snapshot(name):
    # Metadata for XML/XSD/catalog inputs and saves; content hashes for manifests,
    # registry, profile decisions, freshness reports, and other small state files.
    roots = [GAME / "extensions", MODDING / "reference", PROFILE / "save"]
    rows = {}
    for root in roots:
        if not root.is_dir():
            continue
        for folder, _, names in os.walk(root):
            for filename in names:
                p = Path(folder) / filename
                if p.suffix.lower() not in (".xml", ".xsd", ".cat", ".dat", ".gz"):
                    continue
                s = p.stat()
                rows[str(p)] = [s.st_size, s.st_mtime_ns]
    state = [MODDING / "dev/_registry", PROFILE,
             SOURCE / "tools/basex/basex"]
    hashes = {}
    for root in state:
        if root.is_dir():
            for p in root.iterdir():
                if p.is_file():
                    s = p.stat()
                    rows[str(p)] = [s.st_size, s.st_mtime_ns]
                    if s.st_size <= 2_000_000:
                        hashes[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    write(OUT / f"snapshot-{name}.json", json.dumps(dict(metadata=rows, hashes=hashes), indent=2))
    print(f"Snapshot {name}: {len(rows)} files, {len(hashes)} content hashes", flush=True)


def setup():
    assert (LANE / ".git").is_file(), "Expected the dedicated audit worktree"
    OUT.mkdir(exist_ok=True)
    for rel in ("registry", "profile", "mods", "fixture/reference", "fixture/game/extensions", "fixture/profile-ext", "fixture/workshop", "logs"):
        inside(OUT / rel).mkdir(parents=True, exist_ok=True)
    for src, dest in [(MODDING / "dev/_registry/modlist.yaml", OUT / "registry/modlist.yaml"),
                      (PROFILE / "content.xml", OUT / "profile/content.xml")]:
        if not dest.exists():
            shutil.copyfile(src, inside(dest))
            dest.chmod(stat.S_IWRITE | stat.S_IREAD)
    if not PYTHON.exists():
        shutil.copytree(SOURCE / "tools/x4validate/.venv", inside(PKG / ".venv"))
        # Editable-install paths must select this worktree, never the original checkout.
        for p in (PKG / ".venv/Lib/site-packages").glob("*.pth"):
            old = p.read_text(encoding="utf-8")
            new = old.replace(str(SOURCE), str(LANE)).replace(SOURCE.as_posix(), LANE.as_posix())
            if new != old:
                write(p, new)
    bx = BASEX / "basex"
    for rel in ("data", "repo", "webapp"):
        inside(bx / rel).mkdir(exist_ok=True)
    # Do not copy the personal BaseX config: its absolute DBPATH points at shared state.
    write(bx / ".basex", f"DBPATH = {(bx / 'data').as_posix()}\nREPOPATH = {(bx / 'repo').as_posix()}\nWEBPATH = {(bx / 'webapp').as_posix()}\n")
    write(OUT / "fixture/debug.txt", "")
    dest = HERE / "driver.py"
    inside(dest).parent.mkdir(parents=True, exist_ok=True)
    if Path(__file__).resolve() != dest.resolve():
        shutil.copyfile(__file__, inside(dest))
    write(OUT / "metadata.json", json.dumps(dict(source=str(SOURCE), lane=str(LANE),
          baseline=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=LANE, text=True).strip(), scope="audit first; x4live fully offline"), indent=2))
    origin = ("import x4validate; from pathlib import Path; print(x4validate.__file__); "
              f"assert Path(x4validate.__file__).resolve().is_relative_to(Path({str(PKG)!r}).resolve())")
    run("import-origin", [PYTHON, "-c", origin])
    snapshot("before")


def tests():
    # All test-created files stay in this worktree or pytest's disposable temp dirs.
    env = environment()
    # The newest x4guard tests intentionally discover bash through PATH. Windows'
    # WSL launcher is not Git Bash and cannot execute Windows-path hook scripts.
    env['PATH'] = str(BASH.parent) + os.pathsep + env['PATH']
    for key in list(env):
        if key.startswith("X4_") or key in ("CLAUDE_PROJECT_DIR", "MSYS_NO_PATHCONV", "MSYS2_ARG_CONV_EXCL"):
            del env[key]
    temp = Path(tempfile.mkdtemp(prefix="x4-tool-audit-pytest-"))
    try:
        run("pytest-tools-reviewed", [PYTHON, "-m", "pytest", "tests", "-q",
            "--ignore=tests/test_install_over_existing.py", "--ignore=tests/test_installer_recovery_command_works.py", "--ignore=tests/test_installer_literal_paths.py", "--ignore=tests/test_installers_agree.py", "--ignore=tests/test_gate_install_is_hermetic.py",
            "--tb=short", "-o", "faulthandler_timeout=60", "-o", f"cache_dir={OUT / 'pytest-cache'}", "--basetemp", temp], env=env, timeout=1200)
    finally:
        shutil.rmtree(temp, ignore_errors=True)   # a whole suite's tmp_path dirs; the results are already in OUT


def interface():
    roster = tomllib.loads((PKG / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    for tool in roster:
        cli(tool, ["--help"], f"help-{tool}")
        cli(tool, ["--version"], f"version-{tool}")
        cli(tool, ["--definitely-invalid-audit-option"], f"invalid-option-{tool}", (2,))
    cli("x4validate", ["--paths"], "resolved-paths")
    # Subcommand help is discovered from each actual argparse parser, not a prose roster.
    sys.path.insert(0, str(PKG))
    import importlib.util
    spec = importlib.util.spec_from_file_location("audit_cli_help", PKG / "scripts/gen-cli-reference.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    commands = {tool: [name for name, _ in generator.listed_subcommands(
        generator.capture_help(target, ["--help"]))] for tool, target in roster.items()}
    write(OUT / "surface.json", json.dumps(commands, indent=2))
    for tool, subs in commands.items():
        for sub in subs:
            cli(tool, [sub, "--help"], f"help-{tool}-{sub}")


def basex_build():
    env = environment()
    env.pop("MSYS_NO_PATHCONV", None)
    env.pop("MSYS2_ARG_CONV_EXCL", None)
    for name in ("build-corpus", "build-effective"):
        run(name, [BASH, BASEX / f"{name}.sh"], (0,3,4), env=env, cwd=BASEX, timeout=1800)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["setup", "tests", "interface", "basex-build", "snapshot"])
    a = p.parse_args()
    {"setup": setup, "tests": tests, "interface": interface,
     "basex-build": basex_build, "snapshot": lambda: snapshot("after")}[a.mode]()
    return bool(ISSUES)


if __name__ == "__main__":
    raise SystemExit(main())
