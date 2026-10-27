"""Installing OVER an existing install -- BOTH installers, the path nothing drove.

The installer had two static test files and neither executes it. That is why a
guaranteed runtime failure was invisible: `install.sh` cannot install over a
toolkit whose config the toolkit own lock has made read-only, and the README
(since aa1c67d) tells users to run that lock.

REPRODUCED 2026-09-07 against a real install, then reduced to the fixture below:

    cp: cannot create regular file '.../.claude/./x4-paths.env': Permission denied

and because the script runs under `set -euo pipefail` and copies SIXTEEN items in
a loop, it died on item 1 leaving `.claude` half-populated, an orphaned
`x4-paths.env.bak-<stamp>` beside it, and nothing printed about what had landed.
`tools` is item 2 -- on a developer machine that is a live tree.

Two properties are pinned here and they are different questions:
  * the install SUCCEEDS over a locked config (it must not need the lock lifted);
  * the config is never touched (byte-identical afterwards, no backup left).

The second is the one that matters. An install that succeeded by overwriting the
users paths would pass the first and be worse than the failure.
"""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import pathlib
import re
import stat
import shutil
import subprocess
import sys
import uuid

import pytest
from _layout import common_hook_dir, require_repo

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
INSTALL_SH = ROOT / "install.sh"
INSTALL_PS1 = ROOT / "install.ps1"
SENTINEL = "SENTINEL_DO_NOT_CLOBBER=1"


def _bash() -> str | None:
    """Git Bash via the repo own resolver, never a bare `bash`.

    On Windows with WSL enabled, `shutil.which("bash")` returns the System32 stub
    and the test fails with a WSL relay error -- a broken test, not a broken
    installer. `scripts/gitbash.py` exists for this and has its own suite.
    """
    src = ROOT / "scripts" / "gitbash.py"
    if not src.is_file():
        return None
    spec = importlib.util.spec_from_file_location("gitbash_for_install", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.find_bash()


#: Locations this suite must never be pointed at, however it is edited later.
#:
#: The installer WRITES, and it once wrote 1,642 files into a real install. A test
#: that names a real destination is one typo away from an upgrade nobody asked
#: for, and the damage would be indistinguishable from a deliberate install.
#:
#: Two checks rather than one, because they fail differently: CONTAINMENT is the
#: real guarantee (everything under pytest tmp_path), and the NAME blocklist
#: catches the case where containment is somehow satisfied by a path that is
#: still a live tree -- a symlinked temp dir, a reconfigured TMPDIR.
_FORBIDDEN_FRAGMENTS = ("x4 foundations", "desktop/modding", "egosoft",
                        "program files", "steamapps")


def _refuse_unless_sandboxed(tmp_path: pathlib.Path, *paths: pathlib.Path) -> None:
    """Every path the installer may WRITE must be inside the pytest sandbox.

    ⚠ CLAUDE_CONFIG_DIR and HOME are checked too, and they were the hole: the
    global arm writes to `<claude-dir>/skills`, `<claude-dir>/agents` and
    `settings.json`, which are NOT among the six path flags. Only one test drove
    that arm and only with --dry-run, so the first non-dry-run global case anyone
    added would have written into the developer's real ~/.claude. Guarding the
    flags and not the destination is the same shape as a precheck that covers one
    file of twenty-six.
    """
    root = tmp_path.resolve()
    # The paths PASSED here, not the ambient environment: the caller overrides
    # CLAUDE_CONFIG_DIR/HOME for the child, so validating os.environ would refuse
    # on the developer's real home while the child never sees it. Check what the
    # child will actually get.
    for p in paths:
        rp = pathlib.Path(p).resolve()
        if root not in rp.parents and rp != root:
            raise AssertionError(
                "REFUSING to run the installer: %s is not under the pytest "
                "sandbox %s. This suite writes, and it must never be aimed at a "
                "real tree." % (rp, root))
        s = rp.as_posix().lower()
        for bad in _FORBIDDEN_FRAGMENTS:
            if bad in s:
                raise AssertionError(
                    "REFUSING to run the installer: %s looks like a live "
                    "installation (matched %r), even though it is under the "
                    "sandbox root." % (rp, bad))

#: The ONLY registry root an installer run from this suite may be pointed at, via the
#: installers' test seam X4_INSTALL_ENV_REGKEY. Production writes HKCU\Environment, and
#: that is the developer's real environment: one forgotten flag would repoint their
#: X4_TOOLKIT. Every key handed out is recorded, and deleted at module teardown.
_TEST_REGROOT = "HKCU\\Software\\X4ToolkitTests\\"
_REGKEYS_HANDED_OUT: list[str] = []


def _new_regkey(tmp_path: pathlib.Path) -> str:
    key = "%s%s-%s" % (_TEST_REGROOT, tmp_path.name, uuid.uuid4().hex[:8])
    _REGKEYS_HANDED_OUT.append(key)
    return key


def _reg_exists(key: str) -> bool:
    return subprocess.run(["reg", "query", key], capture_output=True, text=True).returncode == 0


@pytest.fixture(scope="module", autouse=True)
def _regkey_cleanup():
    """Delete every test registry key this module handed out -- ONLY those, never the
    whole X4ToolkitTests root, which a concurrent run in another worktree may be using --
    and assert each delete worked, so a leak is a failure rather than residue."""
    yield
    if os.name != "nt":
        return
    leaked = []
    for key in _REGKEYS_HANDED_OUT:
        if not _reg_exists(key):
            continue
        subprocess.run(["reg", "delete", key, "/f"], capture_output=True, text=True)
        if _reg_exists(key):
            leaked.append(key)
    _REGKEYS_HANDED_OUT.clear()
    assert not leaked, "test registry keys survived teardown: %s" % leaked


def _fresh(tmp_path: pathlib.Path) -> pathlib.Path:
    """An empty destination plus the directories the installer is pointed at."""
    dest = tmp_path / "toolkit"
    dest.mkdir()
    for d in ("game", "profile", "mods"):
        (tmp_path / d).mkdir()
    return dest

def _install(installer: str, tmp_path: pathlib.Path, dest: pathlib.Path, *extra: str,
             method: str = "separate", from_dest: bool = False,
             source: pathlib.Path | None = None, over_existing: bool = True,
             scrub: tuple = (), env_write: bool = False, regkey: str | None = None,
             detect_path: pathlib.Path | None = None, shell: str = "/bin/bash",
             omit: tuple = (), inherit: dict | None = None, inproc: bool = False):
    """Run ONE of the two installers with identical intent.

    Parameterised rather than duplicated, because the point is that both reach the
    same VERDICT. `test_installers_agree.py` compares the two files as TEXT, so it
    would pass on two installers that agree about item lists and disagree about
    whether they clean up -- which is exactly what was true here: install.ps1 had
    `finally` where install.sh had none, and both still failed this path.

    THE USER ENVIRONMENT IS SANDBOXED HERE, for every run (Plan 3 lane H). The
    installers now set X4_TOOLKIT at OS level, so an unsandboxed run would write the
    developer's real HKCU\\Environment or ~/.bashrc. `--no-env` is passed unless the
    test asks for the write (`env_write=True`), and even then the write lands in a
    throwaway registry key (`regkey`, under `_TEST_REGROOT`) or a profile file under
    tmp_path (HOME, ZDOTDIR). Agent detection walks `detect_path` (an empty directory
    by default), never the developer's PATH.

    FX-B2: the developer's own X4_* PATH variables are removed too (X4_REFERENCE and
    X4_EXTENSIONS leaked: with `omit=("reference",)` the installer took the developer's
    exported tree). *inherit* sets the ones a test means the installer to INHERIT.
    `installer="ps51"` runs Windows PowerShell 5.1 (powershell.exe) -- what the README's
    `powershell -ExecutionPolicy Bypass -File install.ps1` runs; "ps1" runs pwsh 7 when
    present. *inproc* runs install.ps1 IN-PROCESS (`& 'install.ps1' ...; exit $LASTEXITCODE`),
    the CI step's shape, where a script that falls off its end leaks the last native exit.
    """
    if source is None and not from_dest:
        # R2-B1: the installers install FROM a repository or release tree; an installed toolkit
        # has no agent/ (MEASURED: "the source has no agent/targets/codex/hooks.json.tmpl").
        from _layout import require_repo
        require_repo("agent/targets/codex/hooks.json.tmpl",
                     why="the installers install FROM a repository or release tree")
    fake_home = tmp_path / "fake-claude-home"
    _refuse_unless_sandboxed(tmp_path, dest, tmp_path / "game",
                             tmp_path / "profile", tmp_path / "mods", fake_home)
    if regkey is None:
        regkey = _new_regkey(tmp_path)
    if not regkey.startswith(_TEST_REGROOT) or len(regkey) <= len(_TEST_REGROOT):
        raise AssertionError(
            "REFUSING to run the installer: registry key %r is not under %r. The "
            "production key is the developer's real environment." % (regkey, _TEST_REGROOT))
    if detect_path is None:
        detect_path = tmp_path / "no-agents"
        detect_path.mkdir(exist_ok=True)
    zdot = tmp_path / "zdot"
    zdot.mkdir(exist_ok=True)
    _refuse_unless_sandboxed(tmp_path, detect_path, zdot)
    common = {
        "method": method,
        "toolkit": dest.as_posix(),
        "game": (tmp_path / "game").as_posix(),
        "profile": (tmp_path / "profile").as_posix(),
        "mods": (tmp_path / "mods").as_posix(),
        "reference": (dest / "reference").as_posix(),
        "extensions": (tmp_path / "game" / "extensions").as_posix(),
    }
    for k in omit:                      # let the installer DERIVE these (e.g. reference)
        common.pop(k)
    # OVERRIDES REPLACE, they do not append. Appending a second `--game` is
    # tolerated by bash (last wins) and REJECTED by PowerShell with "parameter
    # 'Game' is specified more than once" -- so the harness would have reported a
    # missing refusal when the installer never ran. A parameterised test has to
    # express intent, not one dialect spelled twice.
    flags = []
    it = iter(extra)
    for a in it:
        if a == "--game":
            common["game"] = next(it)
        elif a == "--reference":
            common["reference"] = next(it)
        elif a == "--agent":
            common["agent"] = next(it)
        elif a == "--dry-run":
            flags.append("dry-run")
        elif a == "--unpack":
            flags.append("unpack")
        elif a == "--codex-doc-max-bytes":
            common["codex-doc-max-bytes"] = next(it)
        else:
            raise AssertionError("unmapped flag: %s" % a)
    if not env_write:
        flags.append("no-env")

    _ps_names = {"dry-run": "DryRun", "no-env": "NoEnv", "codex-doc-max-bytes": "CodexDocMaxBytes",
                 "unpack": "Unpack"}
    if installer == "sh":
        exe = _bash()
        if exe is None:
            pytest.skip("no Git Bash on this machine")
        script = (dest / "install.sh") if from_dest else (source / "install.sh" if source else INSTALL_SH)
        cmd = [exe, script.as_posix()]
        for k, v in common.items():
            cmd += ["--" + k, v]
        cmd += (["--over-existing"] if over_existing else []) + ["--yes"]
        cmd += ["--" + f for f in flags]
    else:
        if installer == "ps51":
            exe = shutil.which("powershell") if os.name == "nt" else None
            if exe is None:
                pytest.skip("no Windows PowerShell 5.1 (powershell.exe) on this machine")
        else:
            exe = shutil.which("pwsh") or shutil.which("powershell")
        if exe is None:
            pytest.skip("no PowerShell on this machine")
        script = (dest / "install.ps1") if from_dest else (source / "install.ps1" if source else INSTALL_PS1)
        args = []
        for k, v in common.items():
            args += ["-" + _ps_names.get(k, k[:1].upper() + k[1:]), v]
        args += (["-OverExisting"] if over_existing else []) + ["-Yes"]
        args += ["-" + _ps_names[f] for f in flags]
        if inproc:
            def _q(a):
                return a if a.startswith("-") else "'" + a.replace("'", "''") + "'"
            line = "& " + _q(script.as_posix()) + " " + " ".join(_q(a) for a in args)
            cmd = [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                   line + "; exit $LASTEXITCODE"]
        else:
            cmd = [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script.as_posix()] + args
    cwd = dest.as_posix() if from_dest else (source.as_posix() if source else ROOT.as_posix())
    # The global arm writes to <claude-dir>, which is NOT one of the six path
    # flags. Pin it into the sandbox for every run rather than hoping no test
    # ever drives that arm without --dry-run.
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = fake_home.as_posix()
    # Codex's own config lives in $CODEX_HOME (default ~/.codex). The installer must
    # never write there -- trusting a project or approving its hooks is the USER's act
    # (spec section 8) -- so it is pinned into the sandbox, where a test can see a write.
    env["CODEX_HOME"] = (tmp_path / "fake-codex-home").as_posix()
    env["HOME"] = tmp_path.as_posix()
    env["USERPROFILE"] = tmp_path.as_posix()
    # With a synthetic USERPROFILE, Windows PowerShell may resolve its default
    # module cache relative to the source cwd. Keep this automatic writer owned too.
    env["PSModuleAnalysisCachePath"] = (tmp_path / "powershell-module-analysis.cache").as_posix()
    # THE USER ENVIRONMENT, sandboxed (see the docstring). The developer's own
    # X4_TOOLKIT is removed too: an inherited value is a NAMED destination to both
    # installers, and a "different existing value" to the env writer.
    env.pop("X4_TOOLKIT", None)
    for _k in ("X4_GAME", "X4_PROFILE", "X4_MODS", "X4_REFERENCE", "X4_EXTENSIONS", "XRCATTOOL",
               "X4_CONFIG"):
        env.pop(_k, None)
    env.update({k: str(v) for k, v in (inherit or {}).items()})
    env["X4_INSTALL_ENV_REGKEY"] = regkey
    env["X4_INSTALL_DETECT_PATH"] = str(detect_path)
    env["ZDOTDIR"] = zdot.as_posix()
    env["SHELL"] = shell
    # `scrub` REMOVES variables, so a Windows box can reproduce a POSIX
    # environment. Every name passed there is Windows-only, i.e. exactly
    # $null under PowerShell on Linux and macOS.
    for _name in scrub:
        env.pop(_name, None)
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=env)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_harness_can_install_at_all(installer, tmp_path):
    """Denominator first. Without this every assertion below could be passing
    because the installer never ran, and a skip is not a pass."""
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest)
    assert r.returncode == 0, "unlocked install failed, so the harness proves nothing:\n%s\n%s" % (r.stdout[-2000:], r.stderr[-2000:])
    assert (dest / "scripts").is_dir(), "the installer reported success and copied no scripts/"


@pytest.mark.parametrize("installer", ["sh", "ps1", "ps51"])
def test_upgrade_retains_legacy_knowledge_and_existing_local_preferences(installer, tmp_path):
    dest = _fresh(tmp_path)
    legacy = b"# Existing knowledge\nA private learned fact.\n## My preferences\nOld unclassified text.\n"
    local = b"## My preferences\nKeep these actual preferences.\n"
    (dest / "KNOWLEDGEBASE.md").write_bytes(legacy)
    (dest / "KNOWLEDGEBASE.local.md").write_bytes(local)
    result = _install(installer, tmp_path, dest, "--agent", "generic")
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    snapshots = list(dest.glob("KNOWLEDGEBASE.pre-4.0-*.md"))
    assert len(snapshots) == 1 and snapshots[0].read_bytes() == legacy
    assert (dest / "KNOWLEDGEBASE.local.md").read_bytes() == local
    assert (dest / "KNOWLEDGEBASE.md").read_bytes() == (ROOT / "KNOWLEDGEBASE.md").read_bytes()
    again = _install(installer, tmp_path, dest, "--agent", "generic")
    assert again.returncode == 0, again.stderr
    assert list(dest.glob("KNOWLEDGEBASE.pre-4.0-*.md")) == snapshots
    assert (dest / "KNOWLEDGEBASE.local.md").read_bytes() == local


def test_the_harness_NEVER_lets_an_installer_reach_the_real_user_env(tmp_path, monkeypatch):
    """Every installer run gets a sandboxed registry key, HOME, ZDOTDIR, SHELL and detect
    PATH, and --no-env unless the test asks for the env write. A test that forgot cannot
    reach HKCU\\Environment, the developer's ~/.zshenv or ~/.bashrc, or their real PATH."""
    captured = {}
    real_run = subprocess.run

    def spy(cmd, **kw):
        captured["cmd"], captured["env"] = cmd, kw["env"]
        return real_run([sys.executable, "-c", "pass"], capture_output=True, text=True)

    monkeypatch.setattr(subprocess, "run", spy)
    monkeypatch.setattr(sys.modules[__name__], "_bash", lambda: "bash")   # never skip
    monkeypatch.setenv("ZDOTDIR", "/opt/developer-zdotdir")
    monkeypatch.setenv("X4_TOOLKIT", "/the/real/toolkit")
    monkeypatch.setenv("X4_INSTALL_ENV_REGKEY", "HKCU\\Environment")
    dest = _fresh(tmp_path)
    _install("sh", tmp_path, dest)
    env, cmd = captured["env"], captured["cmd"]
    assert env["X4_INSTALL_ENV_REGKEY"].startswith("HKCU\\Software\\X4ToolkitTests\\"), env.get(
        "X4_INSTALL_ENV_REGKEY")
    assert pathlib.Path(env["ZDOTDIR"]).resolve().is_relative_to(tmp_path.resolve())
    assert pathlib.Path(env["X4_INSTALL_DETECT_PATH"]).resolve().is_relative_to(tmp_path.resolve())
    assert env["SHELL"] == "/bin/bash"
    assert "X4_TOOLKIT" not in env, "the developer's own X4_TOOLKIT reached the installer"
    assert "--no-env" in cmd                                   # the default
    _install("sh", tmp_path, dest, env_write=True)
    assert "--no-env" not in captured["cmd"]                   # twin: the opt-in reaches the CLI
    _install("ps1", tmp_path, dest)
    assert "-NoEnv" in captured["cmd"]                         # the PowerShell spelling
    with pytest.raises(AssertionError, match="REFUSING"):
        _install("sh", tmp_path, dest, env_write=True, regkey="HKCU\\Environment")


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_UPGRADING_over_a_LOCKED_config_succeeds(installer, tmp_path):
    """THE DOCUMENTED PATH, and the one that was broken.

    Install, lock (which README tells the user to do), upgrade. The second run
    needs to change nothing about the config, so the lock must never bite --
    and before this fix it failed on item 1 of 16 with a bare
    `cp: Permission denied`.
    """
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    cfg = dest / "x4-paths.env"
    before = cfg.read_bytes()
    cfg.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)      # what x4lock does

    r = _install(installer, tmp_path, dest)
    assert r.returncode == 0, (
        "upgrade over a read-only x4-paths.env failed (rc=%s). The toolkit tells "
        "users to lock, so this IS the documented upgrade path:\n%s" % (r.returncode, r.stderr[-2000:]))
    assert cfg.read_bytes() == before, "the upgrade rewrote a config it did not need to change"
    leftovers = sorted(p.name for p in dest.glob("x4-paths.env.bak-*"))
    assert not leftovers, (
        "a backup was left behind: %s -- nothing should be backed up, because "
        "nothing should have been overwritten" % leftovers)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_locked_config_that_MUST_change_refuses_UP_FRONT(installer, tmp_path):
    """The other half, and refusing is the right answer.

    An installer that silently unlocked would defeat the mechanism the user asked
    for. So it must refuse -- but BEFORE writing anything, naming the unlock
    command, rather than dying part-way through a 16-item copy.
    """
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    cfg = dest / "x4-paths.env"
    cfg.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    (tmp_path / "game2").mkdir()

    r = _install(installer, tmp_path, dest, "--game", (tmp_path / "game2").as_posix())
    assert r.returncode != 0, "a config that cannot be written was reported as installed"
    out = (r.stdout + r.stderr).lower()
    assert "unlock" in out, (
        "the refusal does not name the remedy; the user sees a bare permission "
        "error and cannot tell it from a broken install:\n%s" % (r.stdout + r.stderr)[-2000:])
    # POSITIVE, not "absent OR present". `"permission denied" not in out or ...`
    # passes whenever the raw error simply is not there -- including when the run
    # failed for an unrelated reason and said nothing about either. The claim is
    # that the refusal came from the PRECHECK, so assert the precheck's own
    # vocabulary, and assert the raw error absent, separately.
    assert "x4lock" in out, (
        "the refusal does not come from the lock precheck -- it names neither "
        "x4lock nor its remedy:\n%s" % (r.stdout + r.stderr)[-1500:])
    assert "permission denied" not in out, (
        "the failure is still a raw cp error rather than a precondition check:\n%s"
        % (r.stdout + r.stderr)[-1500:])


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_DRY_RUN_predicts_a_refusal_it_would_hit(installer, tmp_path):
    """Finding 3. A dry run that cannot go red carries no information.

    MEASURED before the fix: rc 0 and a clean 16-item list, immediately followed
    by a real run that failed on item 1.
    """
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    (dest / "x4-paths.env").chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    (tmp_path / "game2").mkdir()

    r = _install(installer, tmp_path, dest, "--game", (tmp_path / "game2").as_posix(), "--dry-run")
    # TWO ASSERTIONS, NOT ONE `or`. As a single disjunction this was satisfied by
    # the word "unlock" appearing ANYWHERE in the output -- a help banner, an
    # unrelated hint -- so a dry run exiting 0, the exact pre-fix behaviour this
    # test names in its own docstring, passed. Prose satisfying an assertion is
    # CLAUDE.md #37b, and here the prose can come from the tool itself.
    assert r.returncode != 0, (
        "the dry run reported SUCCESS for a run that cannot succeed:\n%s"
        % (r.stdout + r.stderr)[-1500:])
    assert "unlock" in (r.stdout + r.stderr).lower(), (
        "the dry run refused without naming the remedy, so the user cannot tell "
        "it from a broken install:\n%s" % (r.stdout + r.stderr)[-1500:])


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_dry_run_still_passes_when_the_install_WOULD_work(installer, tmp_path):
    """The twin. A precondition that always refuses is not a check."""
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    (dest / "x4-paths.env").chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    r = _install(installer, tmp_path, dest, "--dry-run")
    assert r.returncode == 0, (
        "the dry run refused an upgrade that needs no config change:\n%s" % (r.stdout + r.stderr)[-1500:])
@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_DRY_RUN_writes_NOTHING_on_the_arm_that_SKIPS_the_copy(installer, tmp_path):
    """THE ARM MY OTHER DRY-RUN CASES COULD NOT REACH, and a CRITICAL lived in it.

    Under `--method separate` the COPY step refuses first and exits, so the config
    writer is never reached -- which is why ten passing tests said nothing about
    it. `--method global` never copies, so the writer's own gate is the only thing
    between a dry run and a real write.

    MEASURED before the fix, ps1, with `.claude` present: rc 0,
    `.claude/x4-paths.env` WRITTEN, "wrote <path>" printed, and then
    "=== dry run complete: nothing was changed ===". A brace error had nested the
    gate inside `if (Test-Path $f)`, so with the config ABSENT -- the state of
    every fresh clone, since it is gitignored -- the gate was skipped entirely and
    execution fell through to the write. bash on the same intent wrote 0 files.

    Asserting on the ARTIFACT rather than on the wording, because the wording said
    nothing was changed while it was untrue.
    """
    dest = _fresh(tmp_path)
    (dest / ".claude").mkdir()          # present, which is what makes the write reachable
    r = _install(installer, tmp_path, dest, "--dry-run", method="global")
    written = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    assert not written, (
        "--dry-run wrote %d file(s): %s (rc=%s) %s"
        % (len(written), written[:8], r.returncode, (r.stdout + r.stderr)[-600:]))
    # PROGRESS, not just absence (v4.0.0 review R7-P1): a run that crashed or refused on its
    # first line also writes nothing. It must have got as far as the dry-run gate and said so.
    assert r.returncode == 0, (r.stdout + r.stderr)[-1200:]
    assert "dry run complete" in (r.stdout + r.stderr).lower(), (r.stdout + r.stderr)[-1200:]
@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_LOCKED_file_anywhere_in_the_copy_set_refuses_UP_FRONT(installer, tmp_path):
    """precheck_config guarded ONE file; x4lock locks about twenty-six.

    Under `--method in-game` the game root IS the destination, and x4lock's
    manifest there covers CLAUDE.md, KNOWLEDGEBASE.md, .claude/settings.json,
    the hooks, the skills and the agents -- all of which are inside the 16-item
    copy set. Guarding only x4-paths.env moved the failure from one filename to
    another rather than removing it.

    Reproduced with `separate` because the DEFECT is not method-specific: it is
    "a locked file in the copy set", and a user may lock anything.

    Both halves matter and they fail differently:
      * bash dies mid-copy with a bare `cp: Permission denied` and a half-copied
        destination, no unlock hint and no INCOMPLETE banner;
      * PowerShell's `Copy-Item -Force` CLEARS the read-only attribute and
        overwrites, so it returns 0 and reports success while destroying the very
        files the lock was protecting.

    One of those is wrong and nothing decided which. Refusing up front is the only
    answer that is right for both.
    """
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    victim = dest / ".claude" / "hooks" / "protect-bash.sh"
    assert victim.is_file(), "fixture assumption broken: the installer did not place %s" % victim
    # DISTINGUISHABLE content first. Source and destination otherwise hold the
    # same bytes, so `read_bytes() == before` would hold whether or not the file
    # was overwritten -- an assertion that cannot fail for the case it exists to
    # catch. Marking it makes a clobber visible.
    victim.write_bytes(b"# SENTINEL - the installer must not replace a locked file")
    before = victim.read_bytes()
    victim.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    r = _install(installer, tmp_path, dest)
    out = (r.stdout + r.stderr).lower()

    assert victim.read_bytes() == before, (
        "a LOCKED file in the copy set was overwritten -- the lock exists to stop "
        "exactly this, and the installer cleared it")
    assert r.returncode != 0, (
        "the installer reported success over a locked file it could not legitimately "
        "replace (rc=%s)" % r.returncode)
    assert "unlock" in out, (
        "the refusal does not name the remedy, so the user sees a bare permission "
        "error: %s" % (r.stdout + r.stderr)[-900:])


def _probe_repo(tmp_path):
    """A throwaway repo carrying the SHIPPED .gitignore.

    The assertion is about the file the release ships, so it must be made
    against that file rather than against whatever repo the suite happens to be
    standing in -- which in a `git archive` cold extract is no repo at all.
    """
    repo = tmp_path / "probe"
    (repo / ".claude").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo.as_posix(), check=True,
                   capture_output=True)
    shutil.copy2(ROOT / ".gitignore", repo / ".gitignore")
    return repo


def _is_git_ignored(repo, path):
    """0 = ignored, 1 = not ignored, ANYTHING ELSE = git could not answer.

    Reading "non-zero" as "not ignored" is what made the old form pass in cold
    for the wrong reason: `git check-ignore` exits 128 outside a repository, and
    an error is not an allow.
    """
    r = subprocess.run(["git", "check-ignore", "-q", path],
                       cwd=str(repo), capture_output=True)
    if r.returncode not in (0, 1):
        raise AssertionError(
            "REFUSING a verdict: `git check-ignore` exited %d for %s, which is "
            "neither 'ignored' nor 'not ignored'. %s"
            % (r.returncode, path, r.stderr.decode("utf-8", "replace")[:200]))
    return r.returncode == 0


def test_a_non_repo_REFUSES_rather_than_reading_as_NOT_IGNORED(tmp_path):
    """The falsification twin for the refusal clause.

    Without it the six positive cases below pass only where a repo happens to
    exist, and the `.example` case -- the one that expects False -- passes
    ANYWHERE, including where nothing was checked at all.
    """
    bare = tmp_path / "not-a-repo"
    (bare / ".claude").mkdir(parents=True)
    with pytest.raises(AssertionError, match="REFUSING a verdict"):
        _is_git_ignored(bare, ".claude/x4-paths.env")


@pytest.mark.parametrize("path,ignored,why", [
    (".claude/x4-paths.env", True, "the live config: machine paths and X4_NEXUS_KEY"),
    (".claude/x4-paths.env.tmp12345", True,
     "the RENDER TARGET: it holds the key by construction, and survives a crash "
     "between render and move"),
    (".claude/settings.local.json.tmp999", True, "the same sibling on the other file"),
    (".claude/x4-paths.env.bak-20260907-120000", True,
     "a backup of it, which the carry-over deliberately preserves the key into"),
    (".claude/settings.local.json", True, "per-machine settings"),
    (".claude/settings.local.json.bak-20260907-120000", True, "and its backups"),
    (".claude/x4-paths.env.example", True,
     "no longer the template (Plan 3 lane I moved it to the root); a 3.x leftover"),
    ("x4-paths.env", True, "the 4.x live config at the toolkit root (Plan 3 lane I)"),
    ("x4-paths.env.bak-20260907-120000", True, "a backup beside the root config"),
    ("x4-paths.env.tmp12345", True, "the render target beside the root config"),
    ("x4-paths.env.example", False,
     "the TEMPLATE is tracked on purpose; a blanket x4-paths.env* would swallow it"),
])
def test_the_config_backups_cannot_be_committed(path, ignored, why, tmp_path):
    """Asserts the SHIPPED .gitignore, in a repo built for the purpose.

    WHY the rule exists: `.gitignore` pinned the two config filenames EXACTLY, so
    the `.bak-<stamp>` files beside them were not ignored at all -- and
    `write_paths_env` carries over every key it does not own (X4_NEXUS_KEY
    explicitly, per setup.sh), so each backup holds the key plus that machine's
    absolute paths, and they show up in `git status` where a `git add -A` would
    take them. PRE-ARC: v3.0.0 already created `.bak-$stamp`. Both directions are
    asserted, because the obvious fix is a blanket `x4-paths.env*` and that
    silently stops tracking the `.example` template the installer ships.

    WHY it is built here rather than run in place. It used to run `git check-ignore` in whatever repo the suite was standing in.
    Two things were wrong with that, and the second is worse:

    * `verify-cold.sh` extracts via `git archive HEAD` into a directory that is
      NOT a git repo, so check-ignore exited 128 and every case read as "not
      ignored" -- six failed and the release gate went red.
    * rc 128 is an ERROR, not a negative answer. Treating any non-zero as "not
      ignored" conflated "git could not look" with "git says no", so the
      `.example` case PASSED IN COLD FOR THE WRONG REASON: it cannot tell
      "correctly not ignored" from "there is no repo here". An error is not an
      allow.

    Building the repo here fixes both, and makes the test assert the file the
    release SHIPS rather than the ignore behaviour of the tree it happens to run
    in -- which is the thing the claim was always about.
    """
    is_ignored = _is_git_ignored(_probe_repo(tmp_path), path)
    assert is_ignored == ignored, (
        "%s should%s be git-ignored (%s)" % (path, "" if ignored else " NOT", why))


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_refusal_LIST_is_bounded_and_says_how_many_it_hid(installer, tmp_path):
    """A regression pin for a branch I wrote and never exercised.

    The refusal prints at most 8 paths plus a count of the rest. Written that way
    because an UNBOUNDED warning is the defect this same session fixed in the
    canary -- a report whose length grows with the severity it describes is filed
    by the harness exactly when it matters. But the >8 branch had no case, so
    "bounded" was a claim about code nobody had run.

    Locks 12 files so the cap actually bites, and asserts BOTH halves: the list is
    capped, and the hidden count is stated rather than silently dropped.
    """
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    hooks = dest / ".claude" / "hooks"
    victims = sorted(p for p in hooks.glob("*") if p.is_file())[:12]
    assert len(victims) >= 10, "fixture needs >8 lockable files, found %d" % len(victims)
    for v in victims:
        v.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    r = _install(installer, tmp_path, dest)
    out = r.stdout + r.stderr
    assert r.returncode != 0, "reported success over %d locked files" % len(victims)
    # ASSERTED, not merely computed. This was assigned and never used while the
    # docstring claimed both halves were pinned -- a variable that looks like a
    # check and is not one.
    # KEYED ON THE DESTINATION, not on a drive letter. This counted lines
    # starting "C:" or "/", so on a runner whose temp is on another drive --
    # GitHub's RUNNER_TEMP is D:\\a\\_temp, and any developer can set TMP
    # anywhere -- `listed` was 0 and `assert listed <= 8` passed WITHOUT
    # EXAMINING A SINGLE LINE. That is the half this test exists for: the
    # docstring calls the >8 branch "a claim about code nobody had run".
    #
    # It matters now rather than later because ubuntu is being promoted to a
    # GATING CI leg: an assertion that cannot go red there is worse than absent.
    _dest_posix = dest.as_posix()
    _dest_native = str(dest)
    listed = sum(1 for ln in out.splitlines()
                 if (_dest_posix in ln or _dest_native in ln) and "hooks" in ln)
    # A FLOOR, because "<= 8" is also satisfied by counting NOTHING. The fixture
    # locks more than 8 files on purpose, so the refusal must name some of them.
    assert listed > 0, (
        "no listed path matched the destination, so the cap assertion below "
        "would pass over an empty count:\n%s" % out[-1200:])
    assert listed <= 8, (
        "the refusal listed %d paths; the cap is 8 plus a count" % listed)
    assert "NOT LISTED" in out, (
        "more than 8 files were blocked and the refusal did not say how many it "
        "hid -- a narrowing step must announce itself:\n%s" % out[-1200:])
    assert "unlock" in out.lower()
@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_config_write_leaves_no_temp_behind(installer, tmp_path):
    """The config is rendered to a temp, verified, then moved.

    Both installers used to write the LIVE config and verify it AFTERWARDS, so a
    render that failed part way left a half-written file and the check then
    reported a failure about something it was too late to protect. Third instance
    of one shape today, after `_effective._write_db` and the round trip removed
    from copy_toolkit.

    ⚠ WHAT THIS DOES AND DOES NOT PROVE. It asserts the observable half: no temp
    survives a successful run, and the installed config is sourceable. It does NOT
    inject a mid-write failure -- the escaping exists to make an unsourceable
    render impossible, so there is no honest way to provoke one from outside. The
    partial-write window is closed BY CONSTRUCTION (the live path is never the
    target of the render), and that is a claim about the code, not about this
    test.
    """
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "install failed"
    cfg = dest / "x4-paths.env"
    assert cfg.is_file(), "no config was written"
    strays = sorted(p.name for p in dest.glob("x4-paths.env.tmp*"))
    assert not strays, "the config write left a temp behind: %s" % strays
    body = cfg.read_text(encoding="utf-8")
    assert "X4_TOOLKIT=" in body, "the installed config is not the rendered one"
@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_an_IN_PLACE_dry_run_runs_NOTHING(installer, tmp_path):
    """THE SHAPE NO FIXTURE HERE HAD, and the fourth critical of this arc lived in it.

    Every other case points --toolkit at a NEW directory, so `same_dir` is false,
    the copy step runs and ITS dry-run gate exits first. When a user re-runs the
    installer from inside an existing install to preview an upgrade, `same_dir` is
    TRUE: no copy, so `write_paths_env` is the only writer reached -- and its
    "nothing to change" fast path returned ABOVE the dry-run gate.

    MEASURED before the fix, all four arms: rc 0, no dry-run banner, "install
    complete" printed, and `setup.sh` RAN -- syncing dependencies with uv. With
    --unpack it would also have run bin/unpack-reference.sh, which unpacks the
    game archives. Proven a regression of this arc against the same fixture at
    c6fe0f0^, where the banner appeared and setup.sh did not run.

    ★ The structural half matters more than the ordering bug: `setup.sh` and
    `bin/unpack-reference.sh` are invoked at TOP LEVEL, outside all three writers,
    so a gate placed in the writers is incapable of covering them however
    carefully it is positioned. refuse_if_dry_run's own docstring claimed every
    write goes through one of the three. It does not.
    """
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    marker = dest / "tools" / "x4validate" / ".venv"
    had_venv = marker.exists()

    r = _install(installer, tmp_path, dest, "--dry-run", from_dest=True)
    out = (r.stdout + r.stderr)
    low = out.lower()
    assert r.returncode == 0, out[-1200:]          # R7-P1: a crash also "runs nothing"

    assert "dry run complete" in low, (
        "an in-place --dry-run printed no dry-run banner:\n%s" % out[-1200:])
    assert "install complete" not in low, (
        "an in-place --dry-run reported a completed INSTALL:\n%s" % out[-1200:])
    # DERIVED from setup.sh's own banner, never retyped: the v4.0 rename changed it, and a
    # retyped literal of the OLD banner would have made this assertion unable to go red.
    banner = re.search(r'^echo "(=== .+ setup ===)"', (ROOT / "setup.sh").read_text(
        encoding="utf-8"), re.M)
    assert banner, "setup.sh has no '=== ... setup ===' banner to look for -- not checked"
    assert banner.group(1).lower() not in low, (
        "an in-place --dry-run RAN setup.sh -- it syncs dependencies and writes to "
        "disk:\n%s" % out[-1500:])
    assert marker.exists() == had_venv, "a dry run created or removed the virtualenv"
@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_an_UNREADABLE_config_REFUSES_with_a_crafted_message(installer, tmp_path):
    """A read that cannot succeed must not be reported as "everything changed".

    `Get-OwnedEnvLinesFromFile` / `_owned_lines_old` read the live config to decide
    whether a write is needed. Unguarded, a config held open FileShare::None -- an
    editor, an AV scanner, a sync client -- gave a raw interpreter dump naming the
    line and the file, with no crafted message and no INCOMPLETE accounting. Worse,
    an empty result would compare unequal to the rendered lines and be read as
    "the paths changed", which is a different claim from "I could not look".

    A directory standing where the config belongs is the same failure and can
    actually be created, which is what makes this a test rather than an argument.
    The point is not the directory; it is that the guard FIRES and says something
    a user can act on.
    """
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    cfg = dest / "x4-paths.env"
    cfg.unlink()
    cfg.mkdir()                      # unreadable as a file, and creatable

    r = _install(installer, tmp_path, dest)
    out = (r.stdout + r.stderr)
    low = out.lower()
    assert r.returncode != 0, "an unreadable config was reported as a successful install"
    assert "refusing" in low or "cannot read" in low, (
        "the failure is a raw interpreter error rather than a crafted message:\n%s"
        % out[-1200:])
    assert "nothing has been changed" in low or "untouched" in low, (
        "the refusal does not tell the user whether anything was written:\n%s" % out[-1200:])
def _synthetic_source(tmp_path: pathlib.Path) -> pathlib.Path:
    """A toolkit source that has been installed FROM at least once.

    Which is every maintainer checkout: `.bak-<stamp>` is created on every
    config-changing run and accumulates, and `.tmp<pid>` survives a crash between
    render and move. A pristine clone has neither, which is why nothing noticed.
    """
    src = tmp_path / "src"
    (src / ".claude").mkdir(parents=True)
    for name in ("install.sh", "install.ps1", "setup.sh"):
        shutil.copy2(ROOT / name, src / name)
    secret = 'X4_NEXUS_KEY="SECRET_FROM_THE_SOURCE_MACHINE"\n'
    (src / ".claude" / "x4-paths.env").write_text(secret, encoding="utf-8")
    (src / ".claude" / "x4-paths.env.bak-20260101-000000").write_text(secret, encoding="utf-8")
    (src / ".claude" / "x4-paths.env.tmp4242").write_text(secret, encoding="utf-8")
    # ...and the 4.x siblings at the ROOT (Plan 3 lane I): none of them may travel either
    (src / "x4-paths.env").write_text(secret, encoding="utf-8")
    (src / "x4-paths.env.bak-20260101-000000").write_text(secret, encoding="utf-8")
    (src / "x4-paths.env.tmp4242").write_text(secret, encoding="utf-8")
    (src / ".claude" / "settings.local.json.bak-20260101-000000").write_text(secret, encoding="utf-8")
    # the two TEMPLATES ship and must still travel
    (src / "x4-paths.env.example").write_text("X4_TOOLKIT=\n", encoding="utf-8")   # at the ROOT since 4.0
    (src / ".claude" / "settings.local.json.example").write_text("{}\n", encoding="utf-8")
    return src


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_SOURCE_machines_config_siblings_do_not_travel(installer, tmp_path):
    """The keep-list matched two EXACT names; the siblings beside them travelled.

    MEASURED before the fix, both installers: a source carrying
    `x4-paths.env.bak-<stamp>` and `x4-paths.env.tmp<pid>` copied BOTH into the
    destination, rc 0, silent -- and the carry-over deliberately preserves
    X4_NEXUS_KEY into every rewrite, so each holds the source machine's key plus
    its absolute paths.

    ★ This is the previous defect with the roles REVERSED. In the same arc I
    widened `.gitignore` from two exact names to `x4-paths.env.*` and wrote a
    comment explaining why -- and did not widen the COPY rule in step. The ignore
    rule and the copy rule describe the same set and only one of them knew it.

    Both directions, because a blanket prefix skip would stop shipping the
    templates the installer is supposed to install.
    """
    src = _synthetic_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, source=src)
    # rc is NOT asserted: setup.sh cannot succeed against a synthetic source with
    # no tools/x4validate, and that failure belongs to the fixture rather than the
    # installer. What matters is what the COPY did.
    #
    # DENOMINATOR FIRST. Without it, "no secret travelled" is equally true of a
    # run that copied nothing at all.
    assert (dest / ".claude").is_dir(), "the copy did not run; this proves nothing"

    leaked = sorted(p.relative_to(dest).as_posix()
                    for p in dest.rglob("*") if p.is_file()
                    and "SECRET_FROM_THE_SOURCE_MACHINE" in p.read_text(encoding="utf-8", errors="ignore"))
    assert not leaked, (
        "the source machine's secret travelled to the destination in %d file(s): %s"
        % (len(leaked), leaked))

    for tpl in ("x4-paths.env.example", ".claude/settings.local.json.example"):
        assert (dest / tpl).is_file(), (
            "%s did not travel -- the skip is too broad and the installer no "
            "longer ships its own template" % tpl)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_SOURCE_machines_reference_buildid_marker_does_not_travel(installer, tmp_path):
    """FX-G5 / reviewer J2 item 7: `.claude/.reference-buildid` is written by THIS machine's
    finished unpack, and x4doctor reads it as evidence the reference tree was MOVED. It was in
    neither installer list, so installing from an unpacked (non-git) source tree carried it and
    a fresh install read "moved". Keep-local, not prune: the destination's OWN marker survives."""
    src = _synthetic_source(tmp_path)
    (src / ".claude" / ".reference-buildid").write_text("SRC-BUILD\n", encoding="utf-8")
    dest = _fresh(tmp_path)
    _install(installer, tmp_path, dest, source=src)
    assert (dest / ".claude").is_dir(), "the copy did not run; this proves nothing"
    assert (dest / ".claude" / "settings.local.json.example").is_file(), "the copy did not run"
    assert not (dest / ".claude" / ".reference-buildid").exists()
    # twin: the destination's own marker is left exactly as it was
    (dest / ".claude" / ".reference-buildid").write_text("DEST-BUILD\n", encoding="utf-8")
    _install(installer, tmp_path, dest, source=src)
    assert (dest / ".claude" / ".reference-buildid").read_text(encoding="utf-8") == "DEST-BUILD\n"


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_CRLF_config_is_still_recognised_as_UNCHANGED(installer, tmp_path):
    """One input, and the two installers reached opposite verdicts on it.

    bash reads with `read -r`, which KEEPS the trailing carriage return, while the
    renderer emits none -- so a config saved by any Windows editor compared
    unequal forever. PowerShell's Get-Content strips it and said "already
    matches".

    MEASURED before the fix, same fixture, values byte-identical apart from line
    endings: install.sh rc 1 "REFUSING: your path config must change, and it is
    READ-ONLY", install.ps1 rc 0 "already matches these paths; left untouched".
    The config header says "edit freely" and CLAUDE.md names Notepad++ as the
    editor, so opening the file once was enough -- and the refusal's remedy
    (unlock, re-run, re-lock) cannot help, because the "change" is invisible.
    Unlocked, bash instead rewrote the config on EVERY run, losing the
    hand-written comments the fast path exists to preserve.

    `test_UPGRADING_over_a_LOCKED_config_succeeds` could not see this: the
    installer it had just run wrote LF.
    """
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    cfg = dest / "x4-paths.env"
    body = cfg.read_bytes()
    assert b"\r\n" not in body, "fixture assumption broken: the installer wrote CRLF"
    cfg.write_bytes(body.replace(b"\n", b"\r\n"))          # the only change
    cfg.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)  # what x4lock does

    r = _install(installer, tmp_path, dest)
    out = (r.stdout + r.stderr).lower()
    assert r.returncode == 0, (
        "a CRLF config was treated as CHANGED, so the locked upgrade was refused "
        "over line endings alone:\n%s" % (r.stdout + r.stderr)[-1000:])
    assert "already matches" in out, (
        "the config was not recognised as unchanged:\n%s" % (r.stdout + r.stderr)[-1000:])


def _global_fixture(tmp_path, toolkit_name: str = "toolkit"):
    """A toolkit that ships one skill, beside a user who keeps notes in it.

    The destination directory `x4-balance` is one the toolkit OWNS, which is the
    whole point: the existing guard is keyed on that name, so it protects
    `my-own-thing/` and waves through a user file sitting inside `x4-balance/`.

    `toolkit_name` is a parameter because one twin needs a path carrying `[` and
    `]`, which is a metacharacter hazard measured on this file rather than an
    invented one.
    """
    dest = tmp_path / toolkit_name
    dest.mkdir()
    for d in ("game", "profile", "mods"):
        (tmp_path / d).mkdir(exist_ok=True)
    sk = dest / ".claude" / "skills" / "x4-balance"
    sk.mkdir(parents=True)
    # NESTED, because the loop recurses and a mapping bug shows up here first:
    # a top-level file survives some wrong prefixes that a nested one does not.
    (sk / "nested").mkdir()
    (sk / "nested" / "DEEP.md").write_text("deep $CLAUDE_PROJECT_DIR/x" + chr(10),
                                           encoding="utf-8")
    (sk / "SKILL.md").write_text("run $CLAUDE_PROJECT_DIR/tools/x4validate\n",
                                 encoding="utf-8")
    quiet = dest / ".claude" / "skills" / "x4-debug"
    quiet.mkdir(parents=True)
    (quiet / "SKILL.md").write_text("this one names no variable at all\n",
                                    encoding="utf-8")
    ag = dest / ".claude" / "agents"
    ag.mkdir(parents=True)
    (ag / "mod-research.md").write_text("see $CLAUDE_PROJECT_DIR/reference\n",
                                        encoding="utf-8")
    # setup.sh is invoked at the end of a real run; without it the installer
    # reports a failed step and the rewrite result would be read through a
    # non-zero rc that has nothing to do with this claim.
    (dest / "setup.sh").write_text("exit 0\n", encoding="utf-8")

    home = tmp_path / "fake-claude-home"
    mine = home / "skills" / "x4-balance"
    mine.mkdir(parents=True)
    (mine / "MY_NOTES.md").write_text("my dir is $CLAUDE_PROJECT_DIR/notes\n",
                                      encoding="utf-8")
    theirs = home / "skills" / "my-own-thing"
    theirs.mkdir(parents=True)
    (theirs / "THEIRS.md").write_text("my dir is $CLAUDE_PROJECT_DIR/mine\n",
                                      encoding="utf-8")
    return dest, home


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_global_rewrite_touches_only_files_the_TOOLKIT_SHIPS(installer, tmp_path):
    """`--method global --over-existing` rewrote a user's own file and deleted the backup.

    install.sh derives the DIRECTORY list from the toolkit -- its comment says
    "never from a destination glob (a pre-existing user skill named x4-* must not
    match)" -- and then runs `grep -rl CLAUDE_PROJECT_DIR "$tgt"` over the
    DESTINATION directory. With --over-existing that directory holds the toolkit's
    files MERGED with the user's, so every user file inside a shipped skill was
    rewritten by `sed -i.bak ... && rm -f "$f.bak"`: edited in place, only copy
    deleted, rc 0, nothing printed.

    Toolkit-derived for directories and destination-derived for files is the
    wrong-baseline shape: the list is not the thing the comment claims it is.

    install.ps1 already builds `$copied` from `$srcRoot`, so it is the immune case
    and it names the cause -- the operand was right there in the sibling.
    """
    dest, home = _global_fixture(tmp_path)
    r = _install(installer, tmp_path, dest, method="global")

    shipped = (home / "skills" / "x4-balance" / "SKILL.md").read_text(encoding="utf-8")
    # THE CONTROL, and it must come first: if the rewrite step never ran, every
    # "unchanged" assertion below passes for the wrong reason.
    assert "$X4_TOOLKIT" in shipped, (
        "the rewrite never ran, so this test proves nothing about what it spares "
        "(rc=%s) %s" % (r.returncode, (r.stdout + r.stderr)[-800:]))

    mine = (home / "skills" / "x4-balance" / "MY_NOTES.md").read_text(encoding="utf-8")
    assert mine == "my dir is $CLAUDE_PROJECT_DIR/notes\n", (
        "the user's own file inside a SHIPPED skill directory was rewritten: %r" % mine)

    theirs = (home / "skills" / "my-own-thing" / "THEIRS.md").read_text(encoding="utf-8")
    assert theirs == "my dir is $CLAUDE_PROJECT_DIR/mine\n", (
        "a user skill the toolkit does not ship was rewritten: %r" % theirs)

    left = sorted(p.name for p in home.rglob("*.bak*"))
    assert not left, "left backups behind: %s" % left


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_shipped_file_with_NO_token_is_left_alone_and_does_not_fail_the_run(
        installer, tmp_path):
    """The status twin. 2 of the 7 real skills contain no $CLAUDE_PROJECT_DIR.

    grep exits 1 on no match and `set -o pipefail` + `set -e` once killed the
    installer here, after copying and before writing any env -- so "needs no
    rewrite" must stay the normal case, not an error.
    """
    dest, home = _global_fixture(tmp_path)
    r = _install(installer, tmp_path, dest, method="global")
    assert r.returncode == 0, (
        "a skill needing no rewrite failed the run:\n%s" % (r.stdout + r.stderr)[-1200:])
    quiet = (home / "skills" / "x4-debug" / "SKILL.md").read_text(encoding="utf-8")
    assert quiet == "this one names no variable at all\n", "rewrote a file with no token"


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_source_to_destination_MAPPING_survives_a_BRACKETED_toolkit_path(
        installer, tmp_path):
    """The twin for the PATH-PARSING clause, which the other three cannot reach.

    They vary WHICH files the loop selects and hold the path shape constant. This
    one varies the path and holds the selection constant, because the failure is
    not a wrong selection: `${src#"$TOOLKIT/.claude/skills/"}` unquoted treats
    `[v3]` as a character CLASS matching one of v/3, so it does not match the
    literal four characters, the prefix is not stripped, `$rel` keeps the whole
    absolute path, and the destination mapping points outside the skill entirely.
    The file is then simply not rewritten -- or, with a different prefix, written
    somewhere in the user's ~/.claude that nobody asked for.

    It is a test rather than the one-off probe it started as because THIS COMMIT
    made that expression load-bearing. Before the fix the file list came from
    `grep -rl`, which yields absolute paths and never went through a prefix strip,
    so nothing in the suite had ever exercised it. A construct that moves onto the
    path needs coverage on the axis it introduced, not on the axis that was
    already covered.

    `[` and `]` in a toolkit path is a MEASURED hazard on this file, not a
    hypothetical: `toolkit [v3]` is what made the PowerShell arm's bare path
    cmdlets match nothing and report "installed" over an empty directory.
    test_installer_literal_paths.py pins that, but it never drives --method
    global, so it cannot cover this.
    """
    dest, home = _global_fixture(tmp_path, toolkit_name="toolkit [v3]")
    r = _install(installer, tmp_path, dest, method="global")

    for rel in ("SKILL.md", "nested/DEEP.md"):
        f = home / "skills" / "x4-balance" / rel
        assert f.is_file(), (
            "%s never arrived at its destination, so the source->destination "
            "mapping did not survive the bracketed path (rc=%s) %s"
            % (rel, r.returncode, (r.stdout + r.stderr)[-800:]))
        body = f.read_text(encoding="utf-8")
        assert "$X4_TOOLKIT" in body and "$CLAUDE_PROJECT_DIR" not in body, (
            "%s was copied but NOT rewritten -- the prefix strip did not strip, so "
            "the mapped path named no real file and the loop skipped it: %r"
            % (rel, body))

    mine = (home / "skills" / "x4-balance" / "MY_NOTES.md").read_text(encoding="utf-8")
    assert mine == "my dir is $CLAUDE_PROJECT_DIR/notes\n", (
        "a bracketed toolkit path must not widen what gets rewritten either: %r" % mine)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_upgrade_KEEPS_the_recovery_store_and_still_PRUNES_build_artifacts(
        installer, tmp_path):
    """`.claude/backups/` is the RECOVERY STORE, and the upgrade deleted it.

    X4_COPY_PRUNE is dual-purpose -- skipped on the way IN and `rm -rf`'d from the
    destination on the way OUT, twice. v3.0.0's list held three build artifacts.
    24da65e added `.claude/backups` to stop the SOURCE's backup files travelling,
    and the path silently inherited the destructive second meaning: an upgrade
    erased every known-good snapshot and the whole audit trail, rc 0, with the word
    "backup" appearing nowhere in the output.

    That store is not a build artifact. `backup-before-edit.sh` writes every
    pre-edit backup there, `generate-baseline.sh` writes the known-good snapshots
    CLAUDE.md mandates before any experiment, `restore-from-backup.sh` reads it --
    and `x4lock.py` deliberately leaves it UNLOCKED, so the lock guard cannot catch
    this either. MEASURED on the live install when this was found: 982 files,
    60 MB, 33 named snapshots.

    FOUR arms, because the obvious repair fails two of them:

      SUBJECT   the destination's own store survives          (the defect)
      TRAVEL    the SOURCE's store does NOT arrive            (what 24da65e fixed)
      CONTROL   a real build artifact is STILL pruned         (or this passes for
                                                               an installer that
                                                               prunes nothing)
      TWIN      an unrelated user directory under .claude/    (bounds it to the
                survives                                       list, not blanket)

    SUBJECT and TRAVEL are asserted as one exact set, so neither can be satisfied
    by the other going wrong.
    """
    dest = _fresh(tmp_path)

    store = dest / ".claude" / "backups" / "known-good-2026-09-01"
    store.mkdir(parents=True)
    (store / "snapshot.xml").write_text("MY-IRREPLACEABLE-SNAPSHOT", encoding="utf-8")
    audit = dest / ".claude" / "backups" / "AUDIT_LOG.txt"
    audit.write_text("MY-AUDIT-TRAIL", encoding="utf-8")

    artifact = dest / "scripts" / "__pycache__"
    artifact.mkdir(parents=True)
    (artifact / "stale.pyc").write_bytes(b"\x00stale")

    baseline = dest / "tools/x4validate/.perf-baseline.json"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_bytes(b'MY-MEASURED-BASELINE')

    mine = dest / ".claude" / "my-own-notes"
    mine.mkdir(parents=True)
    (mine / "README.md").write_text("MY-OWN-NOTES", encoding="utf-8")

    r = _install(installer, tmp_path, dest)
    assert r.returncode == 0, "the install failed: %s" % (r.stdout + r.stderr)[-1500:]

    # SUBJECT + TRAVEL as one exact set: exactly what I put there, nothing else.
    got = sorted(p.relative_to(dest / ".claude" / "backups").as_posix()
                 for p in (dest / ".claude" / "backups").rglob("*") if p.is_file())
    assert got == ["AUDIT_LOG.txt", "known-good-2026-09-01/snapshot.xml"], (
        "the recovery store is wrong after the upgrade. Either the destination's own "
        "store was destroyed, or the SOURCE machine's backups travelled into it. "
        "got=%r" % (got,))
    assert (store / "snapshot.xml").read_text(encoding="utf-8") == "MY-IRREPLACEABLE-SNAPSHOT"
    assert audit.read_text(encoding="utf-8") == "MY-AUDIT-TRAIL"

    # CONTROL: without this the test passes for an installer that prunes nothing.
    assert not (artifact / "stale.pyc").exists(), (
        "the build-artifact prune stopped working, so the SUBJECT assertion above "
        "proves nothing about the prune list")
    recycled = pathlib.Path(os.environ["X4_RECYCLE_TEST_BIN"])
    assert any((p / "stale.pyc").is_file() and (p / "stale.pyc").read_bytes() == b"\x00stale"
               for p in recycled.iterdir() if p.name.endswith("-__pycache__"))
    assert baseline.read_bytes() == b'MY-MEASURED-BASELINE'

    # TWIN: bounds the change to the list rather than to deletion in general.
    assert (mine / "README.md").read_text(encoding="utf-8") == "MY-OWN-NOTES", (
        "an unrelated user directory under .claude/ was removed")


def _global_only_fixture(tmp_path, mine: str | None = None, theirs_installed: bool = False):
    """A toolkit shipping one skill, and a home that may hold the user's own.

    `mine` is a skill directory the TOOLKIT DOES NOT SHIP whose name still starts
    `x4-`; `theirs_installed` puts the SHIPPED skill into the destination, which is
    the only thing an install would actually replace.
    """
    dest = tmp_path / "toolkit"
    dest.mkdir()
    for d in ("game", "profile", "mods"):
        (tmp_path / d).mkdir(exist_ok=True)
    sk = dest / ".claude" / "skills" / "x4-balance"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text("shipped\n", encoding="utf-8")
    (dest / "setup.sh").write_text("exit 0\n", encoding="utf-8")

    home = tmp_path / "fake-claude-home"
    (home / "skills").mkdir(parents=True)
    if mine:
        (home / "skills" / mine).mkdir()
        (home / "skills" / mine / "SKILL.md").write_text("mine\n", encoding="utf-8")
    if theirs_installed:
        (home / "skills" / "x4-balance").mkdir()
        (home / "skills" / "x4-balance" / "SKILL.md").write_text("old\n", encoding="utf-8")
    return dest, home


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_global_gate_names_only_skills_THIS_TOOLKIT_SHIPS(installer, tmp_path):
    """The gate listed every `~/.claude/skills/x4-*`, not only the ones it would write.

    So a user with their own `x4-mycustom/` was told the install "would REPLACE" it
    and refused until they passed --over-existing -- against a directory this
    installer never touches. The comment directly above the check already claims the
    correct property: *"Only files this install would actually WRITE are named: an
    unrelated agent of the user's own is not at risk and must not be listed as though
    it were."* The AGENTS leg beside it enumerates `$TOOLKIT/.claude/agents/*.md` and
    maps to the destination; the SKILLS leg globbed the destination.

    ⚠ FIX THE CODE, NOT THE COMMENT. Softening that sentence would launder the defect
    into documentation -- CLAUDE.md #37's prose-satisfies-the-assertion shape, which
    this release has already produced once.
    """
    dest, home = _global_only_fixture(tmp_path, mine="x4-mycustom")
    r = _install(installer, tmp_path, dest, method="global", over_existing=False)
    out = r.stdout + r.stderr
    assert "x4-mycustom" not in out, (
        "the gate named a skill this toolkit does not ship, so the user is asked to "
        "authorise replacing a directory the install never touches:\n%s" % out[-900:])
    assert r.returncode == 0, (
        "nothing in the destination would be replaced, so the run must not refuse "
        "(rc=%s):\n%s" % (r.returncode, out[-900:]))
    assert (home / "skills" / "x4-mycustom" / "SKILL.md").read_text(encoding="utf-8") \
        == "mine\n", "the user's own skill was modified"


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_global_gate_STILL_refuses_over_a_skill_it_DOES_ship(installer, tmp_path):
    """The twin, and without it the fix above is satisfied by a gate that refuses
    nothing at all -- which is the failure the gate exists to prevent."""
    dest, home = _global_only_fixture(tmp_path, mine="x4-mycustom",
                                      theirs_installed=True)
    r = _install(installer, tmp_path, dest, method="global", over_existing=False)
    out = r.stdout + r.stderr
    assert r.returncode != 0, (
        "a shipped skill already present in the destination WOULD be replaced, so "
        "the gate must still refuse:\n%s" % out[-900:])
    assert "x4-balance" in out, "the refusal must NAME the skill it would replace"
    assert "x4-mycustom" not in out, (
        "even when refusing, the gate must not name a skill it would not write")


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_READ_ONLY_file_INSIDE_a_shipped_skill_refuses_BEFORE_any_write(
        installer, tmp_path):
    """Two defects in one trace, and the second is in BOTH installers.

    (a) install.sh tested `[ ! -w "$_t" ]` where `$_t` is the skill DIRECTORY, so a
        read-only `SKILL.md` inside a writable directory was invisible. It then ran
        the copy, `cp` failed, and the run ended "1 item(s) could not be copied ... a
        partial install is not recoverable" -- the outcome the guard exists to
        prevent. install.ps1 recursed per file and refused correctly, so this is a
        genuine bash/PowerShell divergence, and the guard's own comment says it was
        added because "install.sh skipped it and ALSO returned 0".

    (b) BOTH ran the check INSIDE the global installer, which the dispatch calls
        AFTER `write_paths_env`. So both printed "wrote ... x4-paths.env" before
        refusing, and PowerShell then said "Nothing has been changed." -- false.
        install.ps1's own comment states the rule this breaks: "refusing after the
        path config has already been rewritten is a partial write, which is the shape
        of the bug rather than a fix."

    Asserted on the ARTIFACT, not the wording, because the wording was untrue.
    """
    dest, home = _global_only_fixture(tmp_path, theirs_installed=True)
    victim = home / "skills" / "x4-balance" / "SKILL.md"
    os.chmod(victim, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    try:
        r = _install(installer, tmp_path, dest, method="global")
        out = r.stdout + r.stderr

        assert r.returncode != 0, (
            "a read-only file inside a shipped skill was not refused:\n%s" % out[-900:])
        assert "SKILL.md" in out or "x4-balance" in out, (
            "the refusal must name what is locked:\n%s" % out[-900:])
        assert "could not be copied" not in out, (
            "the run reached the COPY and failed there, so the guard did not fire "
            "up front:\n%s" % out[-900:])
        cfg = dest / "x4-paths.env"
        assert not cfg.exists(), (
            "the path config was written BEFORE the refusal, so a refused run left a "
            "partial write -- which is the shape of the bug, not the fix")
    finally:
        os.chmod(victim, stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_an_INDENTED_owned_key_does_not_survive_and_WIN(installer, tmp_path):
    """An indented `X4_GAME=` was not recognised as owned, so it was CARRIED OVER --
    and emitted AFTER the authoritative line, which is what bash sources last.

    MEASURED before the fix: a config containing `  X4_GAME="/OLD/STALE/GAME"`,
    re-installed with `--game <new>`, produced

        X4_GAME="<new>"
        # --- carried over from your previous x4-paths.env ---
          X4_GAME="/OLD/STALE/GAME"        <- what bash actually sources

    rc 0, silent. Every hook, gate and tool then resolves the OLD game root. The
    config's own header tells the user to edit it freely, so an indented key is a
    supported edit, not abuse.

    Three sites shared the untrimmed extraction, and the range RECRUITED for it:
    `_owned_lines_old` -- the "would this change?" precondition added this arc --
    copied the same `${line%%=*}`, so the new precondition was blind to the indented
    key too and could not stop the run. install.ps1 trimmed in one of its two.

    Asserted as ONE assignment, because "the new value appears" is satisfied by a
    file that also carries the old one after it.
    """
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    cfg = dest / "x4-paths.env"

    body = cfg.read_text(encoding="utf-8")
    assert "X4_GAME=" in body, "fixture assumption broken: no X4_GAME in the config"
    stale = tmp_path / "OLD-STALE-GAME"
    stale.mkdir()
    edited = []
    for ln in body.splitlines():
        edited.append("  X4_GAME=\"%s\"" % stale.as_posix()
                      if ln.startswith("X4_GAME=") else ln)
    cfg.write_text("\n".join(edited) + "\n", encoding="utf-8")

    newgame = tmp_path / "NEW-GAME"
    newgame.mkdir()
    r = _install(installer, tmp_path, dest, "--game", newgame.as_posix())
    assert r.returncode == 0, "re-install failed: %s" % (r.stdout + r.stderr)[-900:]

    after = cfg.read_text(encoding="utf-8")
    assigns = [ln for ln in after.splitlines()
               if ln.strip().startswith("X4_GAME=")]
    assert len(assigns) == 1, (
        "X4_GAME is assigned %d times; bash sources the LAST one, so the carried "
        "copy wins and the tool resolves the stale root:\n%s" % (len(assigns), assigns))
    assert stale.as_posix() not in after, (
        "the stale game root survived the upgrade:\n%s" % after)
    assert newgame.as_posix() in assigns[0], (
        "the surviving assignment is not the one the user asked for: %r" % assigns[0])


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_rewrite_covers_EVERY_shipped_file_not_only_markdown(installer, tmp_path):
    """`11fa6d3` widened the bash rewrite to `find -type f` and left install.ps1
    filtering `*.md`, so the two installers stopped agreeing about which shipped
    files get `$CLAUDE_PROJECT_DIR` resolved.

    MEASURED cost at the time: ZERO -- no skill ships a non-`.md` file today. Latent
    thereafter: the first skill to ship a script, template or reference file leaves a
    Windows global install resolving `$CLAUDE_PROJECT_DIR` to whatever repo the user
    happens to have open, silently, rc 0.

    Fixed rather than deferred because the divergence was CREATED by this arc -- by a
    commit of mine -- and a latent divergence is exactly what the parity suite cannot
    see (BLIND-SPOTS F109).
    """
    dest, home = _global_only_fixture(tmp_path)
    sub = dest / ".claude" / "skills" / "x4-balance" / "sub"
    sub.mkdir()
    (sub / "helper.sh").write_text("cd $CLAUDE_PROJECT_DIR/tools\n", encoding="utf-8")

    r = _install(installer, tmp_path, dest, method="global")
    assert r.returncode == 0, (r.stdout + r.stderr)[-900:]

    landed = home / "skills" / "x4-balance" / "sub" / "helper.sh"
    assert landed.is_file(), "the non-markdown file was not copied at all"
    body = landed.read_text(encoding="utf-8")
    assert "$X4_TOOLKIT" in body and "$CLAUDE_PROJECT_DIR" not in body, (
        "a shipped NON-markdown file kept $CLAUDE_PROJECT_DIR, so a global install "
        "resolves it to whichever repo is open: %r" % body)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_refusal_the_user_SEES_first_is_the_same_on_both_installers(
        installer, tmp_path):
    """install.sh gates direction FIRST; install.ps1 gated it LAST.

    install.sh carries a nine-line comment at both call sites declaring the order
    load-bearing: both prechecks can exit telling the user to unlock and re-run --
    against a destination they never authorised replacing. The refusal they should
    see is the one about the destination, because acting on the other one (unlock,
    re-run) leads straight back to a refusal for the real reason.

    MEASURED before the fix, locked CLAUDE.md in an existing install, no
    --over-existing:

        sh   rc 2 -> "REFUSING: there is already an installation at the destination."
        ps1  rc 1 -> "REFUSING: ... are READ-ONLY."

    Neither writes, so this is a wrong MESSAGE rather than a wrong action -- and
    install.sh's own comment concedes exactly that while still calling the order
    load-bearing. `811a9a7` fixed bash and left PowerShell; seventh occurrence of the
    class on this file pair, running bash -> ps1 this time.

    Asserted as PARITY plus CONTENT: the two must agree, and they must agree on the
    destination refusal rather than both drifting to the lock one.
    """
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    victim = dest / "CLAUDE.md"
    assert victim.is_file(), "fixture assumption broken: the copy set did not land CLAUDE.md"
    victim.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    try:
        r = _install(installer, tmp_path, dest, over_existing=False)
        out = r.stdout + r.stderr
        assert r.returncode != 0, "a second install over an existing one was not refused"
        assert "already an installation at the destination" in out, (
            "the user is told to unlock and re-run, against a destination they have "
            "not authorised replacing -- so acting on this refusal leads back to a "
            "refusal for the real reason:\n%s" % out[-900:])
    finally:
        victim.chmod(stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
@pytest.mark.parametrize("state,body", [("empty", ""), ("malformed", "{ not json")])
def test_an_UNPARSEABLE_global_settings_file_refuses_in_WORDS_not_a_stack_trace(
        installer, state, body, tmp_path):
    """install.ps1 parsed `~/.claude/settings.json` with an UNGUARDED
    `Get-Content -Raw | ConvertFrom-Json`, and it was the only unguarded operation
    left in a function whose other four writes all gained try/catch this arc.

    MEASURED before the fix, --method global:

        empty      sh  crafted ERROR, rc 1   |  ps1  raw "Cannot index into a null
                                                     array" + CategoryInfo dump, rc 1
        malformed  sh  crafted ERROR, rc 1   |  ps1  raw ConvertFrom-Json
                                                     ArgumentException, rc 1

    A raw .NET dump is not a refusal a user can act on, and it arrives with the
    global settings file untouched but no statement that it was untouched -- which
    reads exactly like a half-finished write.

    Refusing on EMPTY as well as malformed is deliberate and matches install.sh: an
    empty settings.json is indistinguishable from a truncated one, and overwriting
    the user's GLOBAL config on that guess is the wrong call.
    """
    dest, home = _global_only_fixture(tmp_path)
    (home / "settings.json").write_text(body, encoding="utf-8")

    r = _install(installer, tmp_path, dest, method="global")
    out = r.stdout + r.stderr
    assert r.returncode != 0, "an unparseable global settings.json was not refused"
    for raw in ("Cannot index into a null array", "CategoryInfo",
                "System.ArgumentException", "Traceback"):
        assert raw not in out, (
            "the refusal is a raw interpreter dump rather than a message the user "
            "can act on (%s):\n%s" % (raw, out[-900:]))
    assert "settings.json" in out and "ERROR" in out.upper(), (
        "the refusal does not name the file or read as an error:\n%s" % out[-900:])
    assert (home / "settings.json").read_text(encoding="utf-8") == body, (
        "the unparseable settings.json was modified by a run that refused")


# --- the installers must survive a POSIX environment, ON ANY RUNNER -------------------
#
# MEASURED 2026-09-09: install.ps1 was 100% dead under PowerShell on Linux, for BOTH
# methods. Find-GitBash built its candidate array with
# (Join-Path $env:LOCALAPPDATA 'Programs') INSIDE the array literal. LOCALAPPDATA is
# Windows-only, so it threw "Cannot bind argument to parameter 'Path' because it is
# null" before the `if ($base)` guard on the very next line could skip it -- the guard
# protected the loop VARIABLE and not the EXPRESSION that produced it.
#
# 21 tests in this file already caught it, on the ubuntu leg only, where it is
# invisible twice over: that leg is continue-on-error, so the RUN still concludes
# success and even `gh run watch --exit-status` returns 0. It was then masked for a
# whole release behind an earlier failing step, and shipped in v3.1.0.
#
# So this asserts it WITHOUT a POSIX runner: scrub the Windows-only variables and the
# same code path is exercised on Windows. A defect only one leg can see, on a leg that
# cannot fail the build, is a defect nothing sees.
_WINDOWS_ONLY_ENV = ("LOCALAPPDATA", "APPDATA", "ProgramFiles", "ProgramFiles(x86)",
                     "ProgramW6432", "USERPROFILE")


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_installer_survives_an_environment_with_no_WINDOWS_variables(installer, tmp_path):
    """A POSIX box has none of these set. Neither installer may dereference one."""
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, scrub=_WINDOWS_ONLY_ENV)
    out = r.stdout + r.stderr
    assert "because it is null" not in out, (
        "the installer dereferenced a Windows-only environment variable, which is "
        "$null on every Linux and macOS runner:\n%s" % out[-1200:])
    assert r.returncode == 0, (
        "install failed with no Windows environment variables set (rc=%s). This is "
        "what every POSIX user gets:\n%s" % (r.returncode, out[-1200:]))
    assert (dest / "scripts").is_dir(), "reported success and copied no scripts/"


# --- install.ps1's path helpers, called directly, with no Windows environment ---------
#
# The test above drives the installer end to end, and the harness pins all six paths --
# so Detect-Profile returns at its first line and its Join-Path is never reached. That
# left the SECOND POSIX null of 2026-09-09 uncovered: Detect-Profile and
# Get-GlobalClaudeDir both built a path from $env:USERPROFILE, which is $null on Linux and
# macOS, exactly as Find-GitBash did with LOCALAPPDATA.
#
# So this parses install.ps1 with PowerShell's own parser, defines every function it
# declares, and CALLS the path-deriving ones with the Windows variables removed. It
# needs no POSIX runner and no installed game, and it covers helpers this file's
# end-to-end tests reach only by accident of which flags they happen to pass.
_PATH_HELPERS = ("Get-UserHome", "Get-GlobalClaudeDir", "Detect-Profile", "Find-GitBash")

_PROBE = r"""
$ErrorActionPreference = 'Stop'
foreach ($n in @('LOCALAPPDATA','APPDATA','ProgramFiles','ProgramFiles(x86)',
                 'ProgramW6432','USERPROFILE')) {
  Remove-Item -LiteralPath ('Env:' + $n) -ErrorAction SilentlyContinue
}
# install.ps1 declares $Profile / $Game / $Toolkit in its param() block, so inside
# the script they are $null when not passed. Here there is no param() block, and
# $Profile would otherwise resolve to PowerShell's AUTOMATIC $PROFILE -- which is
# always set, so Detect-Profile returned at its first line and this probe could not
# fail for it. MEASURED: three of four falsification twins went red, that one did not.
$Profile = $null; $Game = $null; $Toolkit = $null
$errs = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
         '__PS1__', [ref]$null, [ref]$errs)
if ($errs) { Write-Output ('PARSE:' + $errs[0].Message); exit 3 }
$fns = $ast.FindAll({ param($n) $n -is
        [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)
foreach ($f in $fns) { Invoke-Expression $f.Extent.Text }
foreach ($name in @(__NAMES__)) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
    Write-Output ('MISSING:' + $name); continue
  }
  try { $null = & $name; Write-Output ('OK:' + $name) }
  catch { Write-Output ('THREW:' + $name + ':' + $_.Exception.Message) }
}
"""


def test_install_ps1_path_helpers_do_not_dereference_a_null_windows_variable(tmp_path):
    exe = shutil.which("pwsh") or shutil.which("powershell")
    if exe is None:
        pytest.skip("no PowerShell on this machine")
    names = ",".join("'%s'" % n for n in _PATH_HELPERS)
    script = (_PROBE.replace("__PS1__", INSTALL_PS1.as_posix())
                    .replace("__NAMES__", names))
    sf = tmp_path / "probe-helpers.ps1"
    sf.write_text(script, encoding="utf-8")
    r = subprocess.run([exe, "-NoProfile", "-File", sf.as_posix()],
                       capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")

    threw = [ln for ln in out.splitlines() if ln.startswith("THREW:")]
    assert not threw, (
        "install.ps1 path helper(s) threw with no Windows environment variables set. "
        "That is every Linux and macOS user:\n%s" % ("\n".join(threw)))

    # DENOMINATOR. Without this the assertion above passes when nothing ran at all.
    ok = [ln for ln in out.splitlines() if ln.startswith("OK:")]
    missing = [ln for ln in out.splitlines() if ln.startswith("MISSING:")]
    assert not missing, (
        "a helper this test names no longer exists in install.ps1, so it was never "
        "called: %s -- rename it here or drop it" % (", ".join(missing)))
    assert len(ok) == len(_PATH_HELPERS), (
        "expected %d helpers to be exercised, got %d. Full output:\n%s"
        % (len(_PATH_HELPERS), len(ok), out[-1500:]))


# --- a git CHECKOUT as the source copies what git TRACKS, not what is on disk ---------
#
# Release review 2026-09-26: installing from a maintainer checkout copied 33 files
# `git ls-files` does not know -- tools/basex/basex/data/ alone was 2.3 GB of BaseX
# databases, plus _eff/, stage-manifest.json, the coverage manifests and a
# .pytest_cache. The prune list is a hand-kept second copy of .gitignore and had
# drifted from it; `.gitignore` itself is the one correct enumeration. So a source
# that IS a git work tree copies its tracked set, and anything else (a release zip,
# which holds only tracked files anyway) keeps the walk -- with the derived paths
# added to its lists as defence in depth.

#: Derived BaseX products a checkout accumulates. All gitignored upstream.
_DERIVED = ("tools/basex/basex/data/x4raw/tbl.basex",
            "tools/basex/_eff/effective-manifest.json",
            "tools/basex/stage-manifest.json",
            "tools/basex/basex/coverage-x4raw.json",
            "tools/basex/.pytest_cache/v/cache/nodeids")
#: Untracked and named in NO list: only the tracked-set rule can keep it out.
_UNTRACKED = "tools/scratch-output/result.json"
_TRACKED = "tools/tracked-dir/kept.txt"
#: Tracked, then deleted from the working tree: must not crash the copy.
_TRACKED_GONE = "tools/tracked-dir/gone.txt"


def _git(repo: pathlib.Path, *args: str) -> None:
    hooks = repo.parent / "no-hooks"
    hooks.mkdir(exist_ok=True)
    subprocess.run(["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + hooks.as_posix(),
                    "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _derived_source(tmp_path: pathlib.Path, git: str | None) -> pathlib.Path:
    """A toolkit source holding derived + untracked files beside tracked ones.

    git=None     -- a plain folder (the release-zip shape)
    git="top"    -- the source IS the top of a work tree, tracked set committed
    git="empty"  -- `git init` in the source, nothing tracked
    git="partial" -- the source is the top of a repo tracking the installers but
                    NOT tools/ (the shape of a game-root repo)   [coverage clause]
    git="outer"  -- the source sits UNTRACKED inside an unrelated repo (a zip
                    extracted inside a game root that is itself a repo)
    git="outer-tracked" -- inside another repo that tracks every copy item the
                    source holds, so ONLY the top-of-work-tree clause can say
                    "not the toolkit's own repo"                  [top clause]
    """
    if git is not None and shutil.which("git") is None:
        pytest.skip("no git on this machine")
    outer = tmp_path / "outer"
    src = (outer / "src") if git in ("outer", "outer-tracked") else (tmp_path / "src")
    src.mkdir(parents=True)
    for name in ("install.sh", "install.ps1", "setup.sh"):
        shutil.copy2(ROOT / name, src / name)
    for rel in (_TRACKED, _TRACKED_GONE):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text("TRACKED", encoding="utf-8")
    if git == "top":
        _git(src, "init", "-q")
        _git(src, "add", "--", "install.sh", "install.ps1", "setup.sh", _TRACKED, _TRACKED_GONE)
        _git(src, "commit", "-q", "-m", "fixture")
        (src / _TRACKED_GONE).unlink()
    elif git == "empty":
        _git(src, "init", "-q")
    elif git == "partial":
        _git(src, "init", "-q")
        _git(src, "add", "--", "install.sh", "install.ps1", "setup.sh")
        _git(src, "commit", "-q", "-m", "fixture")
    elif git == "outer-tracked":
        _git(outer, "init", "-q")
        _git(outer, "add", "--", *("src/" + n for n in ("install.sh", "install.ps1", "setup.sh",
                                                        _TRACKED, _TRACKED_GONE)))
        _git(outer, "commit", "-q", "-m", "fixture")
    elif git == "outer":
        (outer / "unrelated.txt").write_text("x", encoding="utf-8")
        _git(outer, "init", "-q")
        _git(outer, "add", "--", "unrelated.txt")
        _git(outer, "commit", "-q", "-m", "fixture")
    for rel in _DERIVED + (_UNTRACKED,):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text("DERIVED-OR-UNTRACKED", encoding="utf-8")
    return src


def _copied(dest: pathlib.Path) -> set[str]:
    return {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_GIT_CHECKOUT_source_copies_only_its_TRACKED_files(installer, tmp_path):
    src = _derived_source(tmp_path, "top")
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, source=src)
    out = r.stdout + r.stderr
    got = _copied(dest)
    # DENOMINATOR: the tracked file arrived, and so did an item copied AFTER tools/,
    # so the walk neither skipped the tree nor died on the deleted tracked file.
    assert _TRACKED in got, "the TRACKED file did not travel:\n%s" % out[-1500:]
    assert "install.ps1" in got, "the copy stopped part way:\n%s" % out[-1500:]
    leaked = sorted(set(_DERIVED + (_UNTRACKED,)) & got)
    assert not leaked, "untracked/derived files travelled from a git source: %s" % leaked
    assert _TRACKED_GONE not in got
    assert "tracked" in out.lower(), (
        "the narrowing to the tracked set was not ANNOUNCED:\n%s" % out[-1500:])


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_PLAIN_FOLDER_source_still_copies_what_is_on_disk_minus_the_derived_paths(
        installer, tmp_path):
    """The release-zip shape: behaviour unchanged, plus the derived-path lists."""
    src = _derived_source(tmp_path, None)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, source=src)
    got = _copied(dest)
    assert _TRACKED in got and _TRACKED_GONE in got and _UNTRACKED in got, (
        "a non-git source no longer copies what is on disk:\n%s"
        % (r.stdout + r.stderr)[-1500:])
    leaked = sorted(set(_DERIVED) & got)
    assert not leaked, "derived BaseX products travelled from a plain source: %s" % leaked


@pytest.mark.parametrize("installer", ["sh", "ps1"])
@pytest.mark.parametrize("shape", ["empty", "partial", "outer", "outer-tracked"])
def test_TWIN_a_source_that_is_not_the_TOP_of_a_populated_work_tree_keeps_the_walk(
        installer, shape, tmp_path):
    """One twin PER CLAUSE, each able to fail alone: `partial` (the source is the
    top of a repo that does not track tools/) is the coverage clause's; and
    `outer-tracked` (another repo tracks everything the source holds) is the
    top-of-work-tree clause's. `empty` and `outer` are the obvious shapes -- but
    each is caught by BOTH clauses, so neither can tell a missing one. MEASURED:
    with only those two, deleting either clause left this test green."""
    src = _derived_source(tmp_path, shape)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, source=src)
    got = _copied(dest)
    assert _TRACKED in got and _UNTRACKED in got, (
        "a %s source fell into tracked-set mode:\n%s" % (shape, (r.stdout + r.stderr)[-1500:]))
    assert not sorted(set(_DERIVED) & got)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_DESTINATIONS_own_BaseX_databases_survive_an_upgrade(installer, tmp_path):
    """The derived paths are SKIP-ON-THE-WAY-IN, never delete-from-the-destination.
    X4_COPY_PRUNE is also `rm -rf`'d from the destination, twice; filing data/ there
    would erase a user's 2+ GB of built databases on every upgrade."""
    src = _derived_source(tmp_path, "top")
    dest = _fresh(tmp_path)
    mine = dest / "tools" / "basex" / "basex" / "data" / "x4raw" / "tbl.basex"
    mine.parent.mkdir(parents=True)
    mine.write_text("MY-DATABASE", encoding="utf-8")
    manifest = dest / "tools" / "basex" / "stage-manifest.json"
    manifest.write_text("MY-MANIFEST", encoding="utf-8")
    r = _install(installer, tmp_path, dest, source=src)
    assert (dest / _TRACKED).is_file(), "the copy did not run:\n%s" % (r.stdout + r.stderr)[-1500:]
    assert mine.read_text(encoding="utf-8") == "MY-DATABASE"
    assert manifest.read_text(encoding="utf-8") == "MY-MANIFEST"


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_LOCKED_destination_file_the_copy_would_NOT_write_does_not_refuse(
        installer, tmp_path):
    """The locked-target precheck enumerates the SAME set the copy writes. A
    read-only file standing where an UNTRACKED source file sits is never written,
    so refusing over it would block an install for nothing."""
    src = _derived_source(tmp_path, "top")
    dest = _fresh(tmp_path)
    f = dest / _UNTRACKED
    f.parent.mkdir(parents=True)
    f.write_text("MINE", encoding="utf-8")
    os.chmod(f, stat.S_IREAD)
    try:
        r = _install(installer, tmp_path, dest, source=src)
    finally:
        os.chmod(f, stat.S_IREAD | stat.S_IWRITE)
    out = r.stdout + r.stderr
    assert "REFUSING" not in out, out[-1500:]
    assert (dest / _TRACKED).is_file(), out[-1500:]


# --- per-agent targets: --agent claude|codex|generic|all (Plan 2 lane C; audit F8) ---- #
#
# A SYNTHETIC source, so the Codex rows run today -- before the generated .codex/ and
# .agents/ trees land in the repo -- and with a stub setup.sh, so the exit code means
# something. The real-repo rows below cover the shipped tree and skip (counted) while
# a tree they need is absent.

BS = chr(92)

#: The shape of the frozen Codex hook template (lane B, Task 7): `{{ROOT}}` forward-slash
#: in `command`, `{{ROOT_WIN}}` backslash in `commandWindows`, JSON-escaped in the file.
_TEMPLATE = (
    '{\n  "hooks": {\n    "PreToolUse": [{"matcher": ".*", "hooks": [{"type": "command", '
    '"timeout": 60,\n      "command": "bash \\"{{ROOT}}/.codex/hooks/codex-entry.sh\\" pre_tool_use",\n'
    '      "commandWindows": "pwsh -NoProfile -File \\"{{ROOT_WIN}}' + BS * 2 + '.codex'
    + BS * 2 + 'hooks' + BS * 2 + 'codex-entry.ps1\\" pre_tool_use"}]}]\n  }\n}\n')

_SKILL = "Run:\n\n    cd {{TOOLKIT}}/tools/x4validate && uv run x4validate --paths\n"
_SHIPPED_AGENTS_MD = "# AGENTS.md -- shipped by the toolkit\n"


def _agent_source(tmp_path: pathlib.Path, *, codex: bool = True, template: bool = True,
                  opencode: bool = True) -> pathlib.Path:
    src = tmp_path / "src"
    src.mkdir()
    if opencode:
        # An owned source fixture must not depend on which native agent was
        # selected in this install. Common code comes from the neutral carrier;
        # five exact native files travel as public fixtures, checked against
        # generated production bytes by test_installer_runtime_fixture.py.
        hooks = src / ".opencode" / "hooks"
        shutil.copytree(ROOT / "tools" / "x4_harness", hooks,
                        ignore=shutil.ignore_patterns("__pycache__"))
        fixture = ROOT / "tools/x4validate/tests/fixtures/opencode-runtime"
        manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
        native = {name: ".opencode/hooks/" + name for name in
                  ("opencode_config.py", "opencode_adapter.py", "codex_adapter.py", "patch_paths.py")}
        native["x4guard.js"] = ".opencode/plugins/x4guard.js"
        assert manifest["v"] == 1 and set(manifest["files"]) == set(native)
        for name, row in manifest["files"].items():
            assert row["source"] == native[name], "fixture destination escaped its declared native path"
            data = (fixture / name).read_bytes()
            assert hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest() == row["sha256"], name
            target = src / row["source"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        # a config IN the source never travels (KEEP_LOCAL): the destination's is rendered
        (src / ".opencode" / "opencode.jsonc").write_bytes(b"// SOURCE COPY -- must never be installed\n")
    for name in ("install.sh", "install.ps1"):
        shutil.copy2(ROOT / name, src / name)
    # The REAL user-environment writer both installers call (lane H), so the env tests
    # drive the shipped script; its seam keeps every write under _TEST_REGROOT.
    (src / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts" / "x4-userenv.ps1", src / "scripts" / "x4-userenv.ps1")
    files = {
        "setup.sh": "#!/bin/bash\necho stub-setup\nexit 0\n",
        "CLAUDE.md": "# CLAUDE.md -- shipped\n",
        "AGENTS.md": _SHIPPED_AGENTS_MD,
        "KNOWLEDGEBASE.md": "kb\n",
        "README.md": "readme\n",
        ".claude/settings.json": "{}\n",
        ".claude/hooks/protect-bash.sh": "#\n",
        ".agents/skills/x4-demo/SKILL.md": _SKILL,
        ".agents/skills/x4-demo/reference/x4demo.md": "plain\n",
        "agent/README.md": "the neutral source -- never installed\n",
        "tools/x4validate/README.md": "t\n",
        "scripts/x4lock.py": "# stub\n",
    }
    if codex:
        files[".codex/hooks/codex-entry.sh"] = "#!/bin/bash\n"
        files[".codex/hooks/codex-entry.ps1"] = "#\n"
        files[".codex/rules/x4.rules"] = "# rules\n"
    if template:
        files["agent/targets/codex/hooks.json.tmpl"] = _TEMPLATE
    for rel, text in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return src


def _ok(r) -> str:
    return "rc=%s\n%s\n%s" % (r.returncode, r.stdout[-2500:], r.stderr[-2500:])


@pytest.mark.parametrize("installer", ["sh", "ps1"])
@pytest.mark.parametrize("agent,present,absent", [
    ("claude", ["CLAUDE.md", ".claude/settings.json"], ["AGENTS.md", ".codex", ".agents", ".opencode"]),
    ("codex", ["AGENTS.md", ".codex/hooks.json", ".codex/rules/x4.rules", ".agents/skills/x4-demo/SKILL.md"],
     ["CLAUDE.md", ".claude/settings.json", ".claude/hooks", ".opencode"]),
    ("generic", ["AGENTS.md", ".agents/skills/x4-demo/SKILL.md"], ["CLAUDE.md", ".codex", ".claude/hooks", ".opencode"]),
    ("opencode", ["AGENTS.md", ".opencode/plugins/x4guard.js", ".opencode/hooks/opencode_adapter.py",
                  ".opencode/opencode.jsonc"], ["CLAUDE.md", ".codex", ".claude/hooks", ".agents"]),
    ("all", ["CLAUDE.md", ".claude/settings.json", "AGENTS.md", ".codex/hooks.json",
             ".agents/skills/x4-demo/SKILL.md", ".opencode/plugins/x4guard.js", ".opencode/opencode.jsonc"], []),
])
def test_F8_each_agent_installs_its_own_files_and_nothing_else(installer, agent, present, absent, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", agent, source=src)
    assert r.returncode == 0, _ok(r)
    for rel in present:
        assert (dest / rel).exists(), "--agent %s did not install %s\n%s" % (agent, rel, _ok(r))
    for rel in absent + ["agent"]:
        assert not (dest / rel).exists(), "--agent %s installed %s, which it must not" % (agent, rel)
    # the common set still travels, whatever the agent
    assert (dest / "KNOWLEDGEBASE.md").is_file() and (dest / "tools").is_dir()


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_DEFAULT_agent_is_all(installer, tmp_path):
    """User decision #10 (2026-10-02)."""
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, source=src)
    assert r.returncode == 0, _ok(r)
    for rel in ("CLAUDE.md", ".claude/settings.json", "AGENTS.md", ".codex/hooks.json", ".agents"):
        assert (dest / rel).exists(), rel
    # decision L-Q1: `all` (the default) INCLUDES OpenCode
    assert (dest / ".opencode/plugins/x4guard.js").is_file() and (dest / ".opencode/opencode.jsonc").is_file()
    assert "claude, codex, generic, opencode" in r.stdout, "the summary does not say which agents landed\n" + _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
@pytest.mark.parametrize("agent,word", [("nonsense", "nonsense"), ("OpenCode", "OpenCode")])
def test_an_unknown_or_unsupported_agent_REFUSES_before_writing(installer, agent, word, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", agent, source=src)
    assert r.returncode == 2, _ok(r)
    assert word in r.stdout + r.stderr, _ok(r)
    assert not any(dest.iterdir()), "a refused install wrote something: %s" % sorted(
        p.name for p in dest.iterdir())


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_codex_hooks_json_is_RENDERED_for_the_destination(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "codex", source=src)
    assert r.returncode == 0, _ok(r)
    raw = (dest / ".codex" / "hooks.json").read_text(encoding="utf-8")
    assert "{{" not in raw, raw
    h = json.loads(raw)["hooks"]["PreToolUse"][0]["hooks"][0]
    root = dest.resolve()
    want_posix = root.as_posix() + "/.codex/hooks/codex-entry.sh"
    want_win = str(root).replace("/", BS) + BS + ".codex" + BS + "hooks" + BS + "codex-entry.ps1"
    norm = os.path.normcase
    assert norm(want_posix) in norm(h["command"]), (h["command"], want_posix)
    if os.name == "nt":
        assert norm(want_win) in norm(h["commandWindows"]), (h["commandWindows"], want_win)
    # never the SOURCE's root: the definition is per-install (lane B, C14)
    assert norm(src.resolve().as_posix()) not in norm(raw)
    # the review is the USER's: the installer says so and never touches Codex's config
    assert "/hooks" in r.stdout, "the install does not tell the user to review the hooks\n" + _ok(r)
    assert not (tmp_path / "fake-codex-home").exists() and not (tmp_path / ".codex").exists()


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_codex_source_with_NO_template_is_an_INCOMPLETE_install(installer, tmp_path):
    """Hooks that were never rendered are guards that never run: that is a failure to
    report, never a quiet success."""
    src = _agent_source(tmp_path, template=False)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "codex", source=src)
    assert r.returncode == 1 and "INCOMPLETE" in r.stdout, _ok(r)
    assert "hooks.json" in r.stdout + r.stderr


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_a_source_with_no_codex_tree_skips_codex_SAYING_so(installer, tmp_path):
    src = _agent_source(tmp_path, codex=False, template=False)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "all", source=src)
    assert r.returncode == 0, _ok(r)
    assert not (dest / ".codex").exists()
    assert ".codex" in r.stdout, "the missing Codex tree was not NAMED\n" + _ok(r)
    assert "claude, generic" in r.stdout and "codex" not in r.stdout.split("Agents:")[-1].splitlines()[0]


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_skill_token_is_rendered_for_THIS_os(installer, tmp_path):
    """User decision #2: the installer renders `{{TOOLKIT}}` per OS. Codex runs PowerShell
    on Windows, where `$X4_TOOLKIT` is an EMPTY variable (MEASURED, lane A)."""
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "codex", source=src)
    assert r.returncode == 0, _ok(r)
    got = (dest / ".agents/skills/x4-demo/SKILL.md").read_text(encoding="utf-8")
    want = "$env:X4_TOOLKIT" if os.name == "nt" else "$X4_TOOLKIT"
    assert "cd %s/tools/x4validate" % want in got, got
    assert "{{TOOLKIT}}" not in got
    # a file WITHOUT the token is left byte-identical
    assert (dest / ".agents/skills/x4-demo/reference/x4demo.md").read_text(encoding="utf-8") == "plain\n"
    # and the SOURCE is never rewritten
    assert "{{TOOLKIT}}" in (src / ".agents/skills/x4-demo/SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_global_layout_REFUSES_an_explicit_codex_target(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "codex", source=src, method="global")
    assert r.returncode == 2 and "Claude-only" in r.stdout + r.stderr, _ok(r)
    assert not (tmp_path / "fake-claude-home").exists()


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_global_layout_REFUSES_an_explicit_opencode_target(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "opencode", source=src, method="global")
    assert r.returncode == 2 and "Claude-only" in r.stdout + r.stderr, _ok(r)
    assert not (tmp_path / "fake-claude-home").exists()


# --- OpenCode, best effort (Plan 3 lane L) --------------------------------------------- #

@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_agent_opencode_installs_the_opencode_tree_and_renders_its_config(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "opencode", source=src)
    assert r.returncode == 0, _ok(r)
    for rel in ("AGENTS.md", ".opencode/plugins/x4guard.js", ".opencode/hooks/opencode_adapter.py"):
        assert (dest / rel).exists(), rel
    assert not (dest / "CLAUDE.md").exists() and not (dest / ".codex").exists()
    assert "BEST EFFORT" in r.stdout and "desktop app" in r.stdout.lower(), _ok(r)
    cfg = (dest / ".opencode/opencode.jsonc").read_text(encoding="utf-8")
    # rendered for THIS destination, never the source's copy (KEEP_LOCAL)
    assert cfg.startswith("// GENERATED by .opencode/hooks/opencode_config.py for "), cfg[:200]
    assert "SOURCE COPY" not in cfg
    tail = re.sub(r"^[A-Za-z]:", "", (dest / "reference").as_posix()).lstrip("/")
    assert '"*' + tail + '/*": "deny"' in cfg, cfg


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_an_existing_user_opencode_jsonc_is_never_overwritten(installer, tmp_path):
    """No banner = the user's own file: left as it is, and the install says the deny layer
    is NOT in place (INCOMPLETE), never silently."""
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    (dest / ".opencode").mkdir()
    (dest / ".opencode/opencode.jsonc").write_bytes(b'{"permission": {"edit": "ask"}}\n')
    r = _install(installer, tmp_path, dest, "--agent", "opencode", source=src)
    assert r.returncode == 1 and "INCOMPLETE" in r.stdout, _ok(r)
    assert ".opencode/opencode.jsonc" in r.stdout, _ok(r)
    assert (dest / ".opencode/opencode.jsonc").read_bytes() == b'{"permission": {"edit": "ask"}}\n'


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_no_python_for_the_opencode_config_is_an_INCOMPLETE_install(installer, tmp_path, monkeypatch):
    """The deny layer that was never rendered is a guard that never runs: named, never quiet."""
    monkeypatch.setenv("X4_PYTHON", (tmp_path / "no-python.exe").as_posix())
    monkeypatch.setenv("X4_NO_PYTHON_FALLBACK", "1")
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "opencode", source=src)
    assert r.returncode == 1 and "INCOMPLETE" in r.stdout, _ok(r)
    assert ".opencode/opencode.jsonc" in r.stdout and "Python" in r.stdout, _ok(r)
    assert not (dest / ".opencode/opencode.jsonc").exists()


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_a_source_with_no_opencode_tree_skips_opencode_SAYING_so(installer, tmp_path):
    src = _agent_source(tmp_path, opencode=False)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "all", source=src)
    assert r.returncode == 0, _ok(r)
    assert not (dest / ".opencode").exists()
    assert ".opencode" in r.stdout, "the missing OpenCode tree was not NAMED\n" + _ok(r)
    assert "opencode" not in r.stdout.split("Agents:")[-1].splitlines()[0]


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_an_opencode_dry_run_writes_no_config(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "opencode", "--dry-run", source=src)
    assert r.returncode == 0, _ok(r)
    assert not (dest / ".opencode").exists(), sorted(p.name for p in dest.iterdir())


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_the_global_layout_with_the_DEFAULT_agent_still_runs_and_SAYS_codex_is_skipped(
        installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--dry-run", source=src, method="global")
    assert r.returncode == 0, _ok(r)
    assert "Claude-only" in r.stdout, _ok(r)


# --- Task 4: an AGENTS.md the toolkit did not write is moved aside, never overwritten -- #
#
# 0 of 23 tagged releases before 4.0 shipped AGENTS.md (MEASURED 2026-10-02), so any
# AGENTS.md in a destination today was written by its user.

@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_USER_AGENTS_md_is_moved_aside_never_overwritten(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    (dest / "AGENTS.md").write_bytes(b"# my own codex notes\n")
    r = _install(installer, tmp_path, dest, "--agent", "codex", source=src)
    assert r.returncode == 0, _ok(r)
    assert (dest / "AGENTS.pre-4.0.md").read_bytes() == b"# my own codex notes\n"
    assert "AGENTS.pre-4.0.md" in r.stdout + r.stderr, "moved, but not SAID"
    assert (dest / "AGENTS.md").read_text(encoding="utf-8") == _SHIPPED_AGENTS_MD


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_an_AGENTS_md_identical_to_the_shipped_one_is_not_moved(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    # CRLF-only difference: the same file as far as anyone reading it is concerned
    (dest / "AGENTS.md").write_bytes(_SHIPPED_AGENTS_MD.replace("\n", "\r\n").encode("utf-8"))
    r = _install(installer, tmp_path, dest, "--agent", "codex", source=src)
    assert r.returncode == 0, _ok(r)
    assert not list(dest.glob("AGENTS.pre-4.0*.md")), "an identical AGENTS.md was moved aside"


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_a_claude_only_install_leaves_a_user_AGENTS_md_ALONE(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    (dest / "AGENTS.md").write_bytes(b"mine\n")
    r = _install(installer, tmp_path, dest, "--agent", "claude", source=src)
    assert r.returncode == 0, _ok(r)
    assert (dest / "AGENTS.md").read_bytes() == b"mine\n"
    assert not list(dest.glob("AGENTS.pre-4.0*.md"))


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_second_preserved_copy_never_overwrites_the_first(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    (dest / "AGENTS.pre-4.0.md").write_bytes(b"older\n")
    (dest / "AGENTS.md").write_bytes(b"newer\n")
    r = _install(installer, tmp_path, dest, "--agent", "codex", source=src)
    assert r.returncode == 0, _ok(r)
    assert (dest / "AGENTS.pre-4.0.md").read_bytes() == b"older\n"
    assert [p.read_bytes() for p in dest.glob("AGENTS.pre-4.0.*.md")] == [b"newer\n"]


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_DRY_RUN_moves_nothing_and_says_what_it_would_move(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    (dest / "AGENTS.md").write_bytes(b"mine\n")
    r = _install(installer, tmp_path, dest, "--agent", "codex", "--dry-run", source=src)
    assert r.returncode == 0, _ok(r)
    assert (dest / "AGENTS.md").read_bytes() == b"mine\n"
    assert not list(dest.glob("AGENTS.pre-4.0*.md"))
    assert "AGENTS.pre-4.0.md" in r.stdout + r.stderr, _ok(r)
    assert sorted(p.name for p in dest.iterdir()) == ["AGENTS.md"], "the dry run wrote something"


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_LOCKED_user_AGENTS_md_refuses_up_front_NAMING_it(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    mine = dest / "AGENTS.md"
    mine.write_bytes(b"mine\n")
    mine.chmod(mine.stat().st_mode & ~stat.S_IWRITE)
    try:
        r = _install(installer, tmp_path, dest, "--agent", "codex", source=src)
        assert r.returncode == 1, _ok(r)
        assert "AGENTS.md" in r.stdout + r.stderr and "x4lock" in r.stdout + r.stderr, _ok(r)
        assert mine.read_bytes() == b"mine\n" and not list(dest.glob("AGENTS.pre-4.0*.md"))
    finally:
        mine.chmod(mine.stat().st_mode | stat.S_IWRITE)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_LOCKED_hooks_json_that_must_CHANGE_refuses_up_front(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    hj = dest / ".codex" / "hooks.json"
    hj.parent.mkdir(parents=True)
    hj.write_bytes(b'{"hooks": {}}\n')
    hj.chmod(hj.stat().st_mode & ~stat.S_IWRITE)
    try:
        r = _install(installer, tmp_path, dest, "--agent", "codex", source=src)
        assert r.returncode == 1 and "hooks.json" in r.stdout + r.stderr, _ok(r)
        assert hj.read_bytes() == b'{"hooks": {}}\n'
        assert not (dest / "AGENTS.md").exists(), "the refusal came AFTER a write"
    finally:
        hj.chmod(hj.stat().st_mode | stat.S_IWRITE)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_a_LOCKED_hooks_json_that_is_UNCHANGED_does_not_refuse(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "codex", source=src)
    assert r.returncode == 0, _ok(r)
    hj = dest / ".codex" / "hooks.json"
    before = hj.read_bytes()
    hj.chmod(hj.stat().st_mode & ~stat.S_IWRITE)
    try:
        r = _install(installer, tmp_path, dest, "--agent", "codex", source=src)
        assert r.returncode == 0, _ok(r)
        assert hj.read_bytes() == before
    finally:
        hj.chmod(hj.stat().st_mode | stat.S_IWRITE)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_both_installers_render_the_SAME_hooks_json(installer, tmp_path):
    """Parsed-JSON equality across the two installers: Codex hashes the parsed
    definition, so two renderings of one install must never differ in a value."""
    src = _agent_source(tmp_path)
    dests = {}
    for which in ("sh", "ps1"):
        d = tmp_path / ("t-" + which)
        d.mkdir()
        for sub in ("game", "profile", "mods"):
            (tmp_path / sub).mkdir(exist_ok=True)
        r = _install(which, tmp_path, d, "--agent", "codex", source=src)
        assert r.returncode == 0, _ok(r)
        dests[which] = json.loads((d / ".codex/hooks.json").read_text(encoding="utf-8"))
    # `{{ROOT_WIN}}` is the root with every `/` turned into `\` -- on Linux too, where
    # str(path) has no backslash at all. Normalising with str(path) left the Linux root in
    # commandWindows un-replaced, so the two installers differed by their own dir names
    # (CI ubuntu, run 37091872873). Build the backslash form from as_posix() on every OS.
    def norm(d, w):
        root = (tmp_path / ("t-" + w)).resolve().as_posix()
        return (json.dumps(d).replace(root.replace("/", BS).replace(BS, BS * 2), "<ROOT_WIN>")
                .replace(root, "<ROOT>"))
    got = {w: norm(dests[w], w) for w in ("sh", "ps1")}
    assert "<ROOT_WIN>" in got["sh"] and "<ROOT>" in got["sh"], got["sh"]   # the norm DID apply
    assert got["sh"] == got["ps1"]


# --- 3.x -> 4.0: a personalised CLAUDE.md is KEPT as X4-NOTES.pre-4.0.md (lane H T2) -- #
#
# "Personalised" = its canonical hash (BOM dropped, CR deleted, trailing LF stripped) is
# neither the source's CLAUDE.md NOR any CLAUDE.md a release tag shipped
# (scripts/shipped-instruction-hashes.txt). The same list makes a KNOWN shipped AGENTS.md
# ours, so it is replaced rather than moved aside.

_V3_SHIPPED = "# CLAUDE.md -- as v3.3.1 shipped it\nrule one\n"


def _known(src, *texts, name="CLAUDE.md"):
    """Write the data file the way the generator does, hashing with PYTHON -- so every
    installer test below is also a three-implementation agreement test."""
    import hashlib

    def canon(b):
        b = b[3:] if b.startswith(b"\xef\xbb\xbf") else b
        return hashlib.sha256(b.replace(b"\r", b"").rstrip(b"\n")).hexdigest()
    (src / "scripts").mkdir(exist_ok=True)
    (src / "scripts" / "shipped-instruction-hashes.txt").write_text(
        "# comment line\n" + "".join("%s  %s  vtest\n" % (canon(t.encode("utf-8")), name) for t in texts),
        encoding="utf-8")


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_PERSONALISED_claude_md_is_kept_as_X4_NOTES_pre_4_0(installer, tmp_path):
    src = _agent_source(tmp_path)
    _known(src, _V3_SHIPPED)
    dest = _fresh(tmp_path)
    mine = (_V3_SHIPPED + "my own rule\n").encode("utf-8")
    (dest / "CLAUDE.md").write_bytes(mine)
    r = _install(installer, tmp_path, dest, "--agent", "claude", source=src)
    assert r.returncode == 0, _ok(r)
    assert (dest / "X4-NOTES.pre-4.0.md").read_bytes() == mine, "the user's file was not kept BYTE-identical"
    assert (dest / "CLAUDE.md").read_bytes() == b"# CLAUDE.md -- shipped\n"
    assert "X4-NOTES.pre-4.0.md" in r.stdout and "X4-NOTES.md" in r.stdout, _ok(r)
    # C3 (install red-team 2026-10-04): "KEPT" alone read as "still in effect". Say it is NOT
    # loaded any more, and how to bring the content back.
    low = r.stdout.lower()
    assert "no longer loaded" in low and "merge" in low, _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
@pytest.mark.parametrize("variant", ["lf", "crlf", "bom_crlf", "extra_trailing_newlines", "lone_cr"])
def test_TWIN_an_UNEDITED_shipped_claude_md_is_replaced_not_kept(installer, variant, tmp_path):
    """One variant per canonicalisation clause: deleting any clause turns a named one RED.

    `lone_cr` exists because Git Bash's sed reads in TEXT mode and already drops the CR of
    every CRLF (MEASURED: deleting install.sh's `tr -d '\\r'` left `crlf` GREEN on Windows).
    A CR not followed by LF survives that, so only this variant can see the clause there."""
    src = _agent_source(tmp_path)
    _known(src, _V3_SHIPPED)
    dest = _fresh(tmp_path)
    b = _V3_SHIPPED.encode("utf-8")
    b = {"lf": b, "crlf": b.replace(b"\n", b"\r\n"),
         "bom_crlf": b"\xef\xbb\xbf" + b.replace(b"\n", b"\r\n"),
         "extra_trailing_newlines": b + b"\n\n",
         "lone_cr": b.replace(b"rule one", b"rule\r one")}[variant]
    (dest / "CLAUDE.md").write_bytes(b)
    r = _install(installer, tmp_path, dest, "--agent", "claude", source=src)
    assert r.returncode == 0, _ok(r)
    assert not list(dest.glob("X4-NOTES.pre-4.0*")), "an unedited shipped file was kept as the user's\n" + _ok(r)
    assert (dest / "CLAUDE.md").read_bytes() == b"# CLAUDE.md -- shipped\n"


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_a_claude_md_equal_to_the_SOURCE_is_not_kept_even_with_no_list(installer, tmp_path):
    src = _agent_source(tmp_path)                   # no data file at all
    dest = _fresh(tmp_path)
    (dest / "CLAUDE.md").write_bytes(b"# CLAUDE.md -- shipped\r\n")
    r = _install(installer, tmp_path, dest, "--agent", "claude", source=src)
    assert r.returncode == 0, _ok(r)
    assert not list(dest.glob("X4-NOTES.pre-4.0*")), _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_with_NO_list_a_differing_claude_md_is_KEPT_and_the_run_SAYS_why(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    (dest / "CLAUDE.md").write_bytes(_V3_SHIPPED.encode())
    r = _install(installer, tmp_path, dest, "--agent", "claude", source=src)
    assert r.returncode == 0, _ok(r)
    assert (dest / "X4-NOTES.pre-4.0.md").is_file(), _ok(r)
    assert "shipped-instruction-hashes.txt" in r.stdout, "a narrowed decision must announce itself\n" + _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_a_codex_only_install_leaves_CLAUDE_md_ALONE(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    (dest / "CLAUDE.md").write_bytes(b"mine\n")
    r = _install(installer, tmp_path, dest, "--agent", "codex", source=src)
    assert r.returncode == 0, _ok(r)
    assert (dest / "CLAUDE.md").read_bytes() == b"mine\n" and not list(dest.glob("X4-NOTES*"))


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_second_kept_CLAUDE_md_never_overwrites_the_first(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    (dest / "X4-NOTES.pre-4.0.md").write_bytes(b"first\n")
    (dest / "CLAUDE.md").write_bytes(b"second\n")
    r = _install(installer, tmp_path, dest, "--agent", "claude", source=src)
    assert r.returncode == 0, _ok(r)
    assert (dest / "X4-NOTES.pre-4.0.md").read_bytes() == b"first\n"
    assert [p.read_bytes() for p in dest.glob("X4-NOTES.pre-4.0.*.md")] == [b"second\n"]


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_DRY_RUN_keeps_nothing_and_SAYS_what_it_would_keep(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    (dest / "CLAUDE.md").write_bytes(b"mine\n")
    r = _install(installer, tmp_path, dest, "--agent", "claude", "--dry-run", source=src)
    assert r.returncode == 0, _ok(r)
    assert (dest / "CLAUDE.md").read_bytes() == b"mine\n" and not list(dest.glob("X4-NOTES*"))
    assert "X4-NOTES.pre-4.0.md" in r.stdout, _ok(r)
    assert sorted(p.name for p in dest.iterdir()) == ["CLAUDE.md"], "the dry run wrote something"


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_KNOWN_shipped_AGENTS_md_is_replaced_not_moved_aside(installer, tmp_path):
    src = _agent_source(tmp_path)
    old = "# AGENTS.md as 4.0.0 shipped it\n"
    _known(src, old, name="AGENTS.md")
    dest = _fresh(tmp_path)
    (dest / "AGENTS.md").write_bytes(old.replace("\n", "\r\n").encode())
    r = _install(installer, tmp_path, dest, "--agent", "codex", source=src)
    assert r.returncode == 0, _ok(r)
    assert not list(dest.glob("AGENTS.pre-4.0*")), _ok(r)
    assert (dest / "AGENTS.md").read_text(encoding="utf-8") == _SHIPPED_AGENTS_MD


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_a_listed_hash_under_the_OTHER_name_does_not_count(installer, tmp_path):
    """The list is keyed on (hash, NAME): a CLAUDE.md row never blesses an AGENTS.md."""
    src = _agent_source(tmp_path)
    old = "# shipped once, as CLAUDE.md\n"
    _known(src, old, name="CLAUDE.md")
    dest = _fresh(tmp_path)
    (dest / "AGENTS.md").write_bytes(old.encode())
    r = _install(installer, tmp_path, dest, "--agent", "codex", source=src)
    assert r.returncode == 0, _ok(r)
    assert (dest / "AGENTS.pre-4.0.md").read_bytes() == old.encode(), _ok(r)


# --- --agent auto (lane H T3) ----------------------------------------------------------- #
#
# Detection walks ONE PATH (the harness pins it to `detect_path`, an empty directory by
# default) with ONE name/extension list per installer, plus destination markers. A bare
# `.claude/` is never a Claude signal: every install creates one (x4-paths.env lives there).

def _stub_agents(tmp_path, *names):
    d = tmp_path / "agents-on-path"
    d.mkdir(exist_ok=True)
    for n in names:
        p = d / n
        p.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        p.chmod(0o755)
        if os.name == "nt":
            (d / (n + ".cmd")).write_text("@exit /b 0\r\n", encoding="utf-8")
    return d


@pytest.mark.parametrize("installer", ["sh", "ps1"])
@pytest.mark.parametrize("on_path,present,absent", [
    (("codex",), ["AGENTS.md", ".codex/hooks.json"], ["CLAUDE.md", ".claude/settings.json"]),
    (("claude",), ["CLAUDE.md", ".claude/settings.json"], ["AGENTS.md", ".codex"]),
    (("claude", "codex"), ["CLAUDE.md", "AGENTS.md", ".codex/hooks.json"], []),
])
def test_auto_installs_exactly_the_agents_found_on_PATH(installer, on_path, present, absent, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "auto", source=src,
                 detect_path=_stub_agents(tmp_path, *on_path))
    assert r.returncode == 0, _ok(r)
    for rel in present:
        assert (dest / rel).exists(), rel + "\n" + _ok(r)
    for rel in absent:
        assert not (dest / rel).exists(), rel + "\n" + _ok(r)
    for n in on_path:
        assert "%s (on PATH" % n in r.stdout, _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_auto_with_NOTHING_found_installs_all_and_SAYS_so(installer, tmp_path):
    """User decision H-Q1: nothing detected installs `all` and says nothing was detected."""
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "auto", source=src)   # harness PATH is empty
    assert r.returncode == 0, _ok(r)
    assert (dest / "CLAUDE.md").exists() and (dest / ".codex/hooks.json").exists(), _ok(r)
    assert "no agent detected" in r.stdout, _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_auto_reads_a_CLAUDE_marker_in_the_destination(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    (dest / ".claude").mkdir()
    (dest / ".claude" / "settings.json").write_text("{}\n")
    r = _install(installer, tmp_path, dest, "--agent", "auto", source=src)
    assert r.returncode == 0, _ok(r)
    assert (dest / "CLAUDE.md").exists() and not (dest / ".codex").exists(), _ok(r)
    assert "destination has .claude/settings.json" in r.stdout, _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_a_bare_dot_claude_dir_is_NOT_a_claude_signal(installer, tmp_path):
    """A 3.x install made .claude/ for every agent (x4-paths.env lived there) -- F5. This
    fixture stays a 3.x shape ON PURPOSE: that is the destination an upgrade meets."""
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    (dest / ".claude").mkdir()
    (dest / ".claude" / "x4-paths.env").write_text("X4_TOOLKIT=\n")
    (dest / ".codex").mkdir()
    r = _install(installer, tmp_path, dest, "--agent", "auto", source=src)
    assert r.returncode == 0, _ok(r)
    assert not (dest / "CLAUDE.md").exists() and (dest / ".codex/hooks.json").exists(), _ok(r)
    assert "codex (destination has .codex" in r.stdout, _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_a_non_agent_file_on_PATH_is_not_detected(installer, tmp_path):
    d = _stub_agents(tmp_path, "claudette", "xcodex")          # near-miss names
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "auto", source=src, detect_path=d)
    assert r.returncode == 0, _ok(r)
    assert "no agent detected" in r.stdout, _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_auto_with_the_GLOBAL_layout_behaves_as_all_does_there(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "auto", "--dry-run", source=src, method="global")
    assert r.returncode == 0, _ok(r)
    assert "Claude-only" in r.stdout, _ok(r)


# --- X4_TOOLKIT at OS user level (lane H T4; user decision 1) ----------------------------- #
#
# Set when unset; left alone and REPORTED when it names a different toolkit; nothing at
# all under --no-env. Every Windows test writes a throwaway key under _TEST_REGROOT (the
# installers' X4_INSTALL_ENV_REGKEY seam), never HKCU\Environment; every POSIX test writes a
# profile file under tmp_path (HOME / ZDOTDIR).

USERENV = ROOT / "scripts" / "x4-userenv.ps1"


@pytest.fixture
def regkey(tmp_path):
    return _new_regkey(tmp_path)          # deleted, and the delete asserted, at module teardown


def _reg_get(key, name="X4_TOOLKIT"):
    r = subprocess.run(["reg", "query", key, "/v", name], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0] == name and parts[1].startswith("REG_"):
            return parts[2]
        if len(parts) == 2 and parts[0] == name:
            return ""
    return None


def _reg_set(key, value, name="X4_TOOLKIT"):
    subprocess.run(["reg", "add", key, "/v", name, "/t", "REG_SZ", "/d", value, "/f"],
                   check=True, capture_output=True)


@pytest.mark.skipif(os.name != "nt", reason="user environment lives in the registry on Windows only")
@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_an_UNSET_user_X4_TOOLKIT_is_set_to_this_toolkit_in_native_form(installer, tmp_path, regkey):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, source=src, env_write=True, regkey=regkey)
    assert r.returncode == 0, _ok(r)
    assert _reg_get(regkey) == str(dest.resolve()), _ok(r)          # backslashes, no trailing sep
    assert "X4_TOOLKIT set for your user" in r.stdout, _ok(r)


@pytest.mark.skipif(os.name != "nt", reason="user environment lives in the registry on Windows only")
@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_DIFFERENT_existing_value_is_REPORTED_and_LEFT(installer, tmp_path, regkey):
    _reg_set(regkey, r"D:\elsewhere")
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, source=src, env_write=True, regkey=regkey)
    assert r.returncode == 0, _ok(r)
    assert _reg_get(regkey) == r"D:\elsewhere"
    assert r"D:\elsewhere" in r.stdout and "Left unchanged" in r.stdout, _ok(r)


# --- R4-3 (v4.0.0 review): EVERY place an X4_TOOLKIT already lives is read --------------
#
# The check read ONE place: HKCU on Windows, the one startup file the shell would get on
# POSIX. A MACHINE-level value (HKLM) or a Git Bash ~/.bashrc export was invisible, and the
# installer then wrote a SECOND, conflicting value -- which one a given terminal sees depends
# on how it was started. Now every source found is reported and nothing is written beside a
# different one. Under the test seam the machine scope is `<seam>\Machine`.

@pytest.mark.skipif(os.name != "nt", reason="user environment lives in the registry on Windows only")
@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_different_MACHINE_value_is_REPORTED_and_no_user_value_is_written(installer, tmp_path, regkey):
    _reg_set(regkey + "\\Machine", r"D:\machine-wide")
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, source=src, env_write=True, regkey=regkey)
    assert r.returncode == 0, _ok(r)
    assert _reg_get(regkey) is None, "a SECOND, conflicting user value was written"
    assert r"D:\machine-wide" in r.stdout and "Left unchanged" in r.stdout, _ok(r)
    assert "machine" in r.stdout.lower(), _ok(r)


@pytest.mark.skipif(os.name != "nt", reason="user environment lives in the registry on Windows only")
@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_different_GIT_BASH_profile_export_is_REPORTED_and_nothing_written(installer, tmp_path, regkey):
    (tmp_path / ".bashrc").write_text("export X4_TOOLKIT='/d/gitbash-one'\n", encoding="utf-8")
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, source=src, env_write=True, regkey=regkey)
    assert r.returncode == 0, _ok(r)
    assert _reg_get(regkey) is None, "a SECOND, conflicting user value was written"
    assert "/d/gitbash-one" in r.stdout and ".bashrc" in r.stdout and "Left unchanged" in r.stdout, _ok(r)


@pytest.mark.skipif(os.name != "nt", reason="user environment lives in the registry on Windows only")
@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_a_MACHINE_value_naming_THIS_toolkit_is_already_set(installer, tmp_path, regkey):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    _reg_set(regkey + "\\Machine", str(dest.resolve()))
    r = _install(installer, tmp_path, dest, source=src, env_write=True, regkey=regkey)
    assert r.returncode == 0, _ok(r)
    assert "already set" in r.stdout and "WARNING" not in r.stdout, _ok(r)
    assert _reg_get(regkey) is None, "a redundant user value was written over a matching machine one"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell profile")
def test_POSIX_a_different_export_in_ANOTHER_startup_file_is_REPORTED(tmp_path):
    """The shell is bash (.bashrc would be written) and ~/.profile already exports another
    toolkit: a login shell would see one value and an interactive one the other."""
    (tmp_path / ".profile").write_text("export X4_TOOLKIT=/opt/from-profile\n", encoding="utf-8")
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install("sh", tmp_path, dest, source=src, env_write=True, shell="/bin/bash")
    assert r.returncode == 0, _ok(r)
    assert not (tmp_path / ".bashrc").exists(), "a SECOND, conflicting export was written"
    assert "/opt/from-profile" in r.stdout and ".profile" in r.stdout and "Left unchanged" in r.stdout, _ok(r)


@pytest.mark.skipif(os.name != "nt", reason="user environment lives in the registry on Windows only")
@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_the_SAME_value_spelled_differently_is_not_different(installer, tmp_path, regkey):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    spelled = dest.resolve().as_posix().upper() + "/"              # C:/.../TOOLKIT/
    _reg_set(regkey, spelled)
    r = _install(installer, tmp_path, dest, source=src, env_write=True, regkey=regkey)
    assert r.returncode == 0, _ok(r)
    assert _reg_get(regkey) == spelled and "already set" in r.stdout, _ok(r)
    assert "WARNING" not in r.stdout, _ok(r)


@pytest.mark.skipif(os.name != "nt", reason="user environment lives in the registry on Windows only")
@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_no_env_writes_NOTHING_and_prints_the_manual_command(installer, tmp_path, regkey):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, source=src, env_write=False, regkey=regkey)
    assert r.returncode == 0 and _reg_get(regkey) is None, _ok(r)
    assert "X4_TOOLKIT" in r.stdout and ("--no-env" in r.stdout or "-NoEnv" in r.stdout), _ok(r)
    assert not _reg_exists(regkey), "--no-env created the registry key"


@pytest.mark.skipif(os.name != "nt", reason="user environment lives in the registry on Windows only")
@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_DRY_RUN_says_what_it_would_do_to_X4_TOOLKIT_and_writes_nothing(installer, tmp_path, regkey):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--dry-run", source=src, env_write=True, regkey=regkey)
    assert r.returncode == 0, _ok(r)
    assert not _reg_exists(regkey), "a dry run wrote the registry"
    assert "X4_TOOLKIT would be set" in r.stdout, _ok(r)


def _userenv(*args, key):
    exe = shutil.which("pwsh") or shutil.which("powershell")
    if exe is None:
        pytest.skip("no PowerShell on this machine")
    env = dict(os.environ)
    env["X4_INSTALL_ENV_REGKEY"] = key
    return subprocess.run([exe, "-NoProfile", "-NonInteractive", "-File", str(USERENV), *args],
                          capture_output=True, text=True, env=env)


@pytest.mark.skipif(os.name != "nt", reason="user environment lives in the registry on Windows only")
def test_userenv_round_trips_and_never_REPLACES_an_existing_key(tmp_path, regkey):
    r = _userenv("get", key=regkey)
    assert r.returncode == 0 and r.stdout.strip() == "", (r.stdout, r.stderr)        # absent key: nothing
    _reg_set(regkey, "keep me", name="SIBLING")                               # the key now exists
    v = r"C:\Program Files\x4 & co\tool kit"
    r = _userenv("set", v, key=regkey)
    assert r.returncode == 0, r.stderr
    assert _userenv("get", key=regkey).stdout.rstrip("\r\n") == v
    assert _reg_get(regkey) == v
    assert _reg_get(regkey, "SIBLING") == "keep me", "set REPLACED the key (the New-Item -Force trap)"
    r = _userenv("unset", key=regkey)
    assert r.returncode == 0 and _reg_get(regkey) is None, r.stderr
    assert _reg_get(regkey, "SIBLING") == "keep me"


@pytest.mark.skipif(os.name != "nt", reason="user environment lives in the registry on Windows only")
@pytest.mark.parametrize("key", ["HKCU\\Environment", "HKCU\\Software\\X4ToolkitTests\\",
                                 "HKCU\\Software\\X4ToolkitTestsX\\a"])
def test_userenv_REFUSES_a_seam_outside_the_test_root(key):
    # PROBED WITH `get`, never `set` (v4.0.0 review R7-4): one of these keys IS the real
    # HKCU\Environment, so if the refusal ever regressed a `set` probe would rewrite the
    # developer's own X4_TOOLKIT. The seam is validated before the action is dispatched, so
    # `get` reaches the same refusal and can only ever read.
    r = _userenv("get", key=key)
    assert r.returncode == 2 and "REFUSING" in r.stderr, (r.returncode, r.stderr)


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell profile")
@pytest.mark.parametrize("shell,rel", [("/bin/zsh", "zdot/.zshenv"), ("/bin/bash", ".bashrc")])
def test_POSIX_writes_ONE_marked_block_to_the_shells_profile_and_is_idempotent(shell, rel, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    for _ in range(2):
        r = _install("sh", tmp_path, dest, source=src, env_write=True, shell=shell)
        assert r.returncode == 0, _ok(r)
    body = (tmp_path / rel).read_text(encoding="utf-8")
    assert body.count(">>> X4 toolkit") == 1, body
    assert "export X4_TOOLKIT='%s'" % os.path.realpath(dest) in body, body


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell profile")
def test_POSIX_an_existing_DIFFERENT_export_is_LEFT_and_reported(tmp_path):
    (tmp_path / ".bashrc").write_text("export X4_TOOLKIT=/opt/other\n", encoding="utf-8")
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install("sh", tmp_path, dest, source=src, env_write=True, shell="/bin/bash")
    assert r.returncode == 0, _ok(r)
    assert (tmp_path / ".bashrc").read_text(encoding="utf-8") == "export X4_TOOLKIT=/opt/other\n"
    assert "/opt/other" in r.stdout and "Left unchanged" in r.stdout, _ok(r)


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell profile")
def test_POSIX_an_UNKNOWN_shell_gets_NO_file_and_a_manual_line(tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install("sh", tmp_path, dest, source=src, env_write=True, shell="/usr/bin/fish")
    assert r.returncode == 0, _ok(r)
    assert not (tmp_path / ".config" / "fish").exists() and not (tmp_path / ".bashrc").exists()
    assert "export X4_TOOLKIT" in r.stdout or "set -Ux X4_TOOLKIT" in r.stdout, _ok(r)


# --- --codex-doc-max-bytes (lane H T5, opt-in) ------------------------------------------ #
#
# Codex reads the root AGENTS.md and every nested one into ONE 32,768-byte budget (MEASURED,
# lane A), so a user's own AGENTS.md lower in the tree can cut the toolkit's tail off. The
# flag raises the cap in the PROJECT .codex/config.toml; it never overwrites a value.

@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_codex_doc_max_bytes_writes_a_ROOT_key_and_keeps_existing_tables(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    (dest / ".codex").mkdir()
    (dest / ".codex" / "config.toml").write_bytes(b'[profiles.x]\nmodel = "m"\n')
    r = _install(installer, tmp_path, dest, "--agent", "codex", "--codex-doc-max-bytes", "65536", source=src)
    assert r.returncode == 0, _ok(r)
    raw = (dest / ".codex" / "config.toml").read_bytes()
    lines = raw.decode("utf-8").split("\n")
    assert lines[0].startswith("project_doc_max_bytes = 65536"), lines
    assert raw.endswith(b'\n[profiles.x]\nmodel = "m"\n'), raw          # the rest, byte for byte
    assert b"\r" not in raw
    assert "trust this folder" in r.stdout, _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_codex_doc_max_bytes_CREATES_the_config_and_a_rerun_changes_nothing(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    for _ in range(2):
        r = _install(installer, tmp_path, dest, "--agent", "codex", "--codex-doc-max-bytes", "65536", source=src)
        assert r.returncode == 0, _ok(r)
    body = (dest / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert body.count("project_doc_max_bytes") == 1 and body.startswith("project_doc_max_bytes = 65536"), body


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_without_the_flag_no_codex_config_is_written(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "codex", source=src)
    assert r.returncode == 0 and not (dest / ".codex" / "config.toml").exists(), _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_DIFFERENT_existing_cap_is_LEFT_and_reported(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    (dest / ".codex").mkdir()
    (dest / ".codex" / "config.toml").write_bytes(b"project_doc_max_bytes = 40000\n")
    r = _install(installer, tmp_path, dest, "--agent", "codex", "--codex-doc-max-bytes", "65536", source=src)
    assert r.returncode == 0, _ok(r)
    assert (dest / ".codex" / "config.toml").read_bytes() == b"project_doc_max_bytes = 40000\n"
    assert "40000" in r.stdout and "65536" in r.stdout, _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_a_cap_inside_a_TABLE_is_not_the_root_key(installer, tmp_path):
    """`[profiles.x] project_doc_max_bytes` is a different key: the root one is still added."""
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    (dest / ".codex").mkdir()
    (dest / ".codex" / "config.toml").write_bytes(b"[profiles.x]\nproject_doc_max_bytes = 40000\n")
    r = _install(installer, tmp_path, dest, "--agent", "codex", "--codex-doc-max-bytes", "65536", source=src)
    assert r.returncode == 0, _ok(r)
    assert (dest / ".codex" / "config.toml").read_text(encoding="utf-8").startswith(
        "project_doc_max_bytes = 65536"), _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
@pytest.mark.parametrize("agent,value", [("claude", "65536"), ("codex", "abc"), ("codex", "1000"),
                                         ("codex", "2000000")])
def test_an_unusable_codex_doc_max_bytes_REFUSES_before_writing(installer, agent, value, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", agent, "--codex-doc-max-bytes", value, source=src)
    assert r.returncode == 2, _ok(r)
    # REFUSED BY THE CHECK, not by an argument parser that does not know the flag: before
    # the flag existed, bash's "unknown option" also exited 2 having written nothing.
    out = r.stdout + r.stderr
    assert "REFUSING" in out and ("codex-doc-max-bytes" in out or "CodexDocMaxBytes" in out), _ok(r)
    assert not any(dest.iterdir()), _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_codex_doc_max_bytes_with_the_GLOBAL_layout_REFUSES(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--codex-doc-max-bytes", "65536", source=src, method="global")
    assert r.returncode == 2 and "Claude-only" in r.stdout + r.stderr, _ok(r)
    assert not (tmp_path / "fake-claude-home").exists()


# --- the REAL repository's tree (skips, counted, while a generated tree is absent) ---- #

@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_real_repo_installs_ONE_AGENTS_md_and_no_agent_source(installer, tmp_path):
    if not (ROOT / "AGENTS.md").is_file():
        pytest.skip("the repo has no AGENTS.md")
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "all")
    assert r.returncode == 0, _ok(r)
    found = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*")
                   if p.is_file() and p.name.lower() == "agents.md")
    assert found == ["AGENTS.md"], found
    assert not (dest / "agent").exists()
    assert (dest / "CLAUDE.md").is_file(), "the Claude target is installed, so CLAUDE.md must be"


@pytest.mark.parametrize("installer", ["sh", "ps1"])
@pytest.mark.parametrize("agent,skills_dir", [("codex", ".agents"), ("opencode", ".opencode")])
def test_J_the_REAL_codex_skills_get_the_token_rendered_for_THIS_os(installer, tmp_path, agent, skills_dir,
                                                                  monkeypatch):
    """Lane J (Plan 3), orchestrator finding 2026-10-02: the generator rendered the token
    to `$X4_TOOLKIT` itself, so the installers' per-OS rewrite never fired and Codex on
    Windows (PowerShell) saw an EMPTY variable. The synthetic-source row above could not
    see it -- it plants the token by hand. This row installs the COMMITTED generated tree."""
    # The OpenCode row (Plan 3 merge of lanes J + L): lane L's target copied Codex's entry
    # tokens but not the skill override, so .opencode/skills rendered `$X4_TOOLKIT` again.
    if not (ROOT / skills_dir / "skills").is_dir():
        pytest.skip("the repo has no generated %s/skills tree" % skills_dir)
    from _layout import require_repo
    require_repo("agent/skills", why="the token census reads the skill SOURCE")
    n_src = sum("{{TOOLKIT}}" in p.read_bytes().decode("utf-8")
                for p in (ROOT / "agent" / "skills").glob("*/SKILL.md"))
    assert n_src >= 7, n_src          # derived, never retyped: an empty population cannot pass
    monkeypatch.delenv("X4_OPENCODE_SHELL", raising=False)   # R4-6: it moves the OpenCode form
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", agent)
    assert r.returncode == 0, _ok(r)
    want = "$env:X4_TOOLKIT" if os.name == "nt" else "$X4_TOOLKIT"
    assert "rendered {{TOOLKIT}} in" in r.stdout and "%s/ as %s" % (skills_dir, want) in r.stdout, (
        "the installer rendered nothing -- the tree it copied carries no token\n" + _ok(r))
    got = {p.relative_to(dest).as_posix(): p.read_bytes().decode("utf-8")
           for p in (dest / skills_dir / "skills").rglob("*") if p.is_file()}
    assert not [k for k, t in got.items() if "{{TOOLKIT}}" in t]
    # Any toolkit path, not only tools/x4validate: x4-mod-release-review cites <toolkit>/docs/.
    assert sum("%s/" % want in t for k, t in got.items()
               if k.endswith("/SKILL.md")) == n_src, sorted(got)
    if os.name == "nt":   # the bash spelling expands to EMPTY in PowerShell
        assert not [k for k, t in got.items() if re.search(r"(?<!env:)\$X4_TOOLKIT\b", t)]


def test_the_installers_render_hooks_json_exactly_as_the_GENERATOR_does(tmp_path):
    """Lane B owns `render_codex_hooks_json`; both installers re-implement it natively,
    because a bare installer cannot import the generator (it needs ruamel.yaml). This
    pins the three renderings to one answer once the template exists."""
    from _layout import require_repo
    # The template landed in Plan 2: in a checkout its absence is a FAILURE, not a skip.
    require_repo("agent/targets/codex/hooks.json.tmpl", "tools/x4validate/scripts/gen-agent-trees.py",
                 why="the installers' hooks.json is compared with the generator's")
    spec = importlib.util.spec_from_file_location(
        "gen_agent_trees_for_installer", ROOT / "tools/x4validate/scripts/gen-agent-trees.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    if not hasattr(gen, "render_codex_hooks_json"):
        pytest.skip("the generator has no render_codex_hooks_json yet")
    for which in ("sh", "ps1"):
        d = tmp_path / ("t-" + which)
        d.mkdir()
        for sub in ("game", "profile", "mods"):
            (tmp_path / sub).mkdir(exist_ok=True)
        r = _install(which, tmp_path, d, "--agent", "codex")
        assert r.returncode == 0, _ok(r)
        got = json.loads((d / ".codex/hooks.json").read_text(encoding="utf-8"))
        assert got == json.loads(gen.render_codex_hooks_json(d.resolve())), which


# --- Plan 3 lane I: the path config lives at the toolkit ROOT -------------------------- #

def _as_3x(dest: pathlib.Path, extra: str = "") -> pathlib.Path:
    """Turn a fresh 4.x install into what a 3.x one left behind: the config in .claude/."""
    new, old = dest / "x4-paths.env", dest / ".claude" / "x4-paths.env"
    old.parent.mkdir(exist_ok=True)
    old.write_bytes(new.read_bytes() + extra.encode("utf-8"))
    new.unlink()
    return old


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_fresh_install_writes_the_ROOT_config_and_no_claude_one(installer, tmp_path):
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "codex")
    assert r.returncode == 0, (r.stdout + r.stderr)[-1500:]
    assert (dest / "x4-paths.env").is_file(), r.stdout[-1500:]
    assert not (dest / ".claude" / "x4-paths.env").exists()
    assert (dest / "x4-paths.env.example").is_file(), "the template did not ship to the root"


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_an_upgrade_MOVES_a_3x_config_and_says_so(installer, tmp_path):
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    old = _as_3x(dest, 'X4_NEXUS_KEY="carried-key"\n')
    r = _install(installer, tmp_path, dest)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-1500:]
    assert not old.exists(), "the 3.x config is still there:\n" + out[-1500:]
    assert 'X4_NEXUS_KEY="carried-key"' in (dest / "x4-paths.env").read_text(encoding="utf-8")
    assert "[migrated]" in out, out[-1500:]
    flat = out.replace("\\", "/")
    assert (dest / ".claude" / "x4-paths.env").as_posix() in flat, out[-1500:]
    assert (dest / "x4-paths.env").as_posix() in flat, out[-1500:]


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_an_upgrade_over_a_LOCKED_3x_config_with_unchanged_paths_succeeds(installer, tmp_path):
    """The M6 case end to end: a rename passes the read-only bit, and the lock travels."""
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    old = _as_3x(dest)
    before = old.read_bytes()
    old.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    r = _install(installer, tmp_path, dest)
    new = dest / "x4-paths.env"
    try:
        assert r.returncode == 0, (r.stdout + r.stderr)[-1500:]
        assert not old.exists() and new.read_bytes() == before
        assert not os.access(new, os.W_OK), "the lock did not travel with the file"
    finally:
        for f in (old, new):
            if f.exists():
                f.chmod(stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_two_DIFFERING_configs_refuse_before_anything_is_written(installer, tmp_path):
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    new, old = dest / "x4-paths.env", dest / ".claude" / "x4-paths.env"
    old.write_text('X4_GAME="/somewhere/else"\n', encoding="utf-8")
    snap = {f: f.read_bytes() for f in (new, old)}
    (dest / "README.md").unlink()                      # proves whether the copy ran
    r = _install(installer, tmp_path, dest)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out[-1500:]
    assert "REFUSING" in out and "X4_GAME" in out, out[-1500:]
    assert "/somewhere/else" not in out, "a VALUE was printed"
    assert {f: f.read_bytes() for f in snap} == snap
    assert not (dest / "README.md").exists(), "the copy ran before the refusal"
    # v4.0.0 review R4-11: the named command must look at THIS destination. Without --root,
    # `x4config.py status` reports X4_TOOLKIT's (or the cwd's) config -- another toolkit.
    m = re.search(r"x4config\.py\"? status --root \"?([^\"\r\n]+)", out)
    assert m, out[-1500:]
    assert pathlib.Path(m.group(1).strip()).resolve() == dest.resolve(), m.group(1)


@pytest.mark.parametrize("installer", ["sh", "ps1", "ps51"])
@pytest.mark.parametrize("readonly", [False, True])
def test_two_AGREEING_configs_retire_the_old_one_to_a_bak(installer, tmp_path, readonly):
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    new, old = dest / "x4-paths.env", dest / ".claude" / "x4-paths.env"
    old.write_bytes(new.read_bytes())
    before = old.read_bytes()
    if readonly:
        old.chmod(stat.S_IREAD)
    identity = old.stat().st_ino
    try:
        r = _install(installer, tmp_path, dest)
        assert r.returncode == 0, (r.stdout + r.stderr)[-1500:]
        assert not old.exists() and new.read_bytes() == before
        backups = list((dest / ".claude").glob("x4-paths.env.bak-*"))
        assert len(backups) == 1 and backups[0].read_bytes() == before
        # H3 (release review): RENAMED, as the message says -- the same file, not a copy
        # plus a delete -- so a locked (read-only) 3.x config stays locked.
        assert backups[0].stat().st_ino == identity, "the 3.x config was copied, not renamed"
        assert os.access(backups[0], os.W_OK) is (not readonly)
    finally:
        for p in [old, *(dest / ".claude").glob("x4-paths.env.bak-*")]:
            if p.exists():
                p.chmod(stat.S_IREAD | stat.S_IWRITE)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_dry_run_names_the_migration_and_changes_nothing(installer, tmp_path):
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    old = _as_3x(dest)
    before = old.read_bytes()
    r = _install(installer, tmp_path, dest, "--dry-run")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-1500:]
    assert "would MOVE" in out, out[-1500:]
    assert old.read_bytes() == before and not (dest / "x4-paths.env").exists()


# --- R4-2 (v4.0.0 review): x4doctor can say OK on a healthy FRESH install ---------------

def _doctor_env(tmp_path: pathlib.Path, **extra) -> dict:
    """The doctor run the way a user runs it after `--no-env`: no X4_* / CLAUDE_* from the
    developer's shell, Claude's and Codex's homes in the sandbox."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("X4_") or k.startswith("CLAUDE_") or k in ("CODEX_HOME", "XRCATTOOL"))}
    env.update(HOME=tmp_path.as_posix(), USERPROFILE=tmp_path.as_posix(),
               CLAUDE_CONFIG_DIR=(tmp_path / "fake-claude-home").as_posix(),
               CODEX_HOME=(tmp_path / "fake-codex-home").as_posix())
    env.update(extra)
    return env


@pytest.mark.parametrize("installer", ["sh", "ps1"])
@pytest.mark.parametrize("method", ["separate", "in-game"])
def test_x4doctor_on_a_healthy_fresh_install_leaves_ONLY_the_users_steps_open_exit_4(
        installer, method, tmp_path):
    """FX-B3 (reviewer F M3): this was named `..._EXITS_0_...` while it asserted exit 4 -- the
    name described a goal the FX-B2 change had (rightly) given up, not what is checked.

    Two states of the same install: BEFORE the unpack (no reference/ -- the user's steps are
    the unpack, rows roots.reference + layer2.reference, both TODO; was UNKNOWN, exit 3) and
    with a reference/ that is not a finished unpack (layer2 alone, and its hint is the unpack,
    because `apply` refuses a tree without the sentinel).

    MEASURED before the fix (scratch install, install.sh --method separate --agent claude):
    x4doctor exit 3 -- parity.claude UNKNOWN ('X4_TOOLKIT is unset'), x4lock UNKNOWN ('1
    unlocked'), and with X4_TOOLKIT set parity.claude read 'no agent/ source' UNKNOWN. A
    doctor that cannot exit 0 on a healthy install trains its reader to ignore it. 'Healthy'
    here: the Claude target, a game folder, an unpacked (here: present) reference/. Both
    installers, both layouts that copy the guards, with X4_TOOLKIT unset (--no-env)."""
    dest = _fresh(tmp_path)
    game = tmp_path / "game"
    for n in range(1, 10):
        (game / ("%02d.cat" % n)).write_bytes(b"")
    extra = ("--agent", "claude")
    if method == "in-game":
        dest = game
    r = _install(installer, tmp_path, dest, *extra, method=method)
    assert r.returncode == 0, (r.stdout[-1500:], r.stderr[-1500:])

    def doctor():
        d = subprocess.run([sys.executable, str(dest / "scripts" / "x4doctor.py"), "--root",
                            str(dest), "--json"], capture_output=True, text=True, timeout=300,
                           env=_doctor_env(tmp_path), cwd=str(tmp_path))
        return d, json.loads(d.stdout)

    assert not (dest / "reference").exists(), "fixture: the install unpacked nothing"
    d0, got0 = doctor()
    open0 = sorted((c["id"], c["status"]) for c in got0["checks"] if c["status"] not in ("OK", "N/A"))
    assert d0.returncode == 4 and open0 == [("layer2.reference", "TODO"),
                                            ("roots.reference", "TODO")], (d0.returncode, open0)
    assert all("unpack-reference.sh" in c["detail"] for c in got0["checks"]
               if c["status"] == "TODO"), got0["checks"]

    (dest / "reference" / "libraries").mkdir(parents=True, exist_ok=True)
    d, got = doctor()
    # FX-B2: an unprotected reference/ is the USER's pending step on every root (TODO, exit 4),
    # never OK -- the installers do not apply Layer 2. It is the ONLY item allowed to be open.
    bad = [(c["id"], c["status"], c["detail"][:200]) for c in got["checks"]
           if c["status"] not in ("OK", "N/A")
           and not (c["id"] == "layer2.reference" and c["status"] == "TODO")]
    todo = [c["id"] for c in got["checks"] if c["status"] == "TODO"]
    assert d.returncode == 4 and not bad and todo == ["layer2.reference"], (d.returncode, bad, todo)
    assert sum(c["status"] == "OK" for c in got["checks"]) >= 10, got["checks"]
    l2 = [c["detail"] for c in got["checks"] if c["id"] == "layer2.reference"][0]
    assert "not a finished unpack" in l2 and "unpack-reference.sh" in l2, l2


def test_TWIN_x4doctor_on_a_fresh_install_still_FAILS_a_missing_game(tmp_path):
    """The exit-0 above must be EARNED: the same install with its game folder gone is FAIL."""
    dest = _fresh(tmp_path)
    game = tmp_path / "game"
    for n in range(1, 10):
        (game / ("%02d.cat" % n)).write_bytes(b"")
    r = _install("sh", tmp_path, dest, "--agent", "claude")
    assert r.returncode == 0, (r.stdout[-1500:], r.stderr[-1500:])
    (dest / "reference" / "libraries").mkdir(parents=True, exist_ok=True)
    shutil.rmtree(game)
    d = subprocess.run([sys.executable, str(dest / "scripts" / "x4doctor.py"), "--root", str(dest)],
                       capture_output=True, text=True, timeout=300, env=_doctor_env(tmp_path),
                       cwd=str(tmp_path))
    assert d.returncode == 1 and "roots.game" in d.stdout, d.stdout[-2000:]


# --- R6-04 (v4.0.0 review): the reference/ OS protection step is PRINTED, never applied ---

_REFGUARD_STEP = "scripts/x4refguard.py apply"


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_an_UNPROTECTED_existing_reference_PRINTS_the_x4refguard_step(installer, tmp_path):
    """Layer 2 (x4refguard) was applied by nothing in install/upgrade: a user upgrading over
    an unpacked reference/ never learned it existed. RULING: the installers do NOT apply it
    (an ACL change is the user's act) -- they PRINT the step when reference/ exists and
    x4refguard status is not 'protected'. Both installers, the same line."""
    dest = _fresh(tmp_path)
    (dest / "reference" / "libraries").mkdir(parents=True)
    (dest / "reference" / "libraries" / "wares.xml").write_text("<wares/>", encoding="utf-8")
    # A FINISHED unpack (R2-a: apply refuses a tree without the sentinel; that case is
    # test_R2_a_reference_WITHOUT_the_sentinel_is_named_as_unfinished_not_sent_to_apply).
    (dest / "reference" / ".unpacked-and-locked").write_text("Re-unpacked on 2026-10-04." + chr(10),
                                                           encoding="utf-8")
    r = _install(installer, tmp_path, dest, "--agent", "claude")
    assert r.returncode == 0, (r.stdout[-1500:], r.stderr[-1500:])
    assert _REFGUARD_STEP in r.stdout, r.stdout[-2500:]
    # R2 cosmetic: the state is named in words ("absent" read like a missing file)
    assert "not applied" in r.stdout, "the step must name the state it saw: " + r.stdout[-1500:]
    # ...and NEITHER installer applied it -- measured on the tree, not read from the source
    # (FX-B2, reviewer C: test_installers_agree's "neither APPLIES it" was a substring negation).
    st = subprocess.run([sys.executable, str(dest / "scripts" / "x4refguard.py"), "status", "--json",
                         "--toolkit", str(dest)], capture_output=True, text=True,
                        env=_doctor_env(tmp_path), timeout=300)
    assert json.loads(st.stdout)["state"] == "absent", (st.stdout, st.stderr)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_no_reference_yet_prints_NO_refguard_step(installer, tmp_path):
    """No reference/ yet: setup.sh / unpack-reference.sh apply the protection on a fresh
    unpack, so there is nothing to tell the user here."""
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "claude")
    assert r.returncode == 0, (r.stdout[-1500:], r.stderr[-1500:])
    assert _REFGUARD_STEP not in r.stdout, r.stdout[-2500:]


# --- R4-4 (v4.0.0 review): the dry run names EVERY write the real run would make ---------

@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_COPY_dry_run_names_the_rendered_codex_opencode_and_doc_cap_writes(installer, tmp_path):
    """The preview listed the copied items and stopped: the Codex hooks.json render, the
    OpenCode deny-rule render and the .codex/config.toml prepend -- three writes the real run
    makes -- were never mentioned."""
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--dry-run", "--agent", "all",
                 "--codex-doc-max-bytes", "65536")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "dry run complete" in out.lower(), out[-1500:]
    assert ".codex/hooks.json would be" in out, out[-2500:]
    assert ".opencode/opencode.jsonc" in out and "would be rendered" in out, out[-2500:]
    assert "project_doc_max_bytes = 65536 would be" in out, out[-2500:]
    assert not [p for p in dest.rglob("*") if p.is_file()], "the dry run wrote"


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_an_IN_PLACE_dry_run_names_what_it_would_do_to_X4_TOOLKIT(installer, tmp_path):
    """The in-place and global arms reach no copy, so the copy plan -- the only place the
    X4_TOOLKIT preview was printed -- never ran there."""
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest).returncode == 0, "first install failed"
    r = _install(installer, tmp_path, dest, "--dry-run", from_dest=True)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-1500:]
    assert "X4_TOOLKIT would not be touched" in out, out[-2500:]       # --no-env in the harness


# --- R4-5 (v4.0.0 review): a READ-ONLY OpenCode deny-rule file refuses UP FRONT -----------

@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_a_READ_ONLY_stale_opencode_config_refuses_before_anything_is_written(installer, tmp_path):
    """x4lock locks .opencode/opencode.jsonc. The Codex hooks.json had a read-only precheck;
    the OpenCode file had none, so an upgrade copied the whole toolkit and THEN failed at the
    render -- an INCOMPLETE install over a half-upgraded tree."""
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest, "--agent", "opencode").returncode == 0, "first install failed"
    cfg = dest / ".opencode" / "opencode.jsonc"
    assert cfg.is_file()
    cfg.write_text(cfg.read_text(encoding="utf-8") + "// a stale line\n", encoding="utf-8")
    (dest / "README.md").unlink()                      # proves whether the copy ran
    cfg.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    try:
        r = _install(installer, tmp_path, dest, "--agent", "opencode")
        out = r.stdout + r.stderr
        assert r.returncode == 1 and "REFUSING" in out and "READ-ONLY" in out, out[-1500:]
        assert "opencode.jsonc" in out, out[-1500:]
        assert not (dest / "README.md").exists(), "the copy ran before the refusal"
    finally:
        cfg.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_a_READ_ONLY_but_FRESH_opencode_config_does_not_refuse(installer, tmp_path):
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest, "--agent", "opencode").returncode == 0, "first install failed"
    cfg = dest / ".opencode" / "opencode.jsonc"
    cfg.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    try:
        r = _install(installer, tmp_path, dest, "--agent", "opencode")
        assert r.returncode == 0, (r.stdout + r.stderr)[-1500:]
    finally:
        cfg.chmod(stat.S_IRUSR | stat.S_IWUSR)


# --- R4-6 (v4.0.0 review): the OpenCode skills' token follows OpenCode's SHELL ------------

@pytest.mark.skipif(os.name != "nt", reason="the PowerShell rendering exists on Windows only")
@pytest.mark.parametrize("installer", ["sh", "ps1"])
@pytest.mark.parametrize("oc_shell,want,not_want", [("bash", "$X4_TOOLKIT/", "$env:X4_TOOLKIT"),
                                                    (None, "$env:X4_TOOLKIT", "$X4_TOOLKIT/")])
def test_the_opencode_token_is_rendered_for_OPENCODES_shell(installer, oc_shell, want, not_want,
                                                            tmp_path, monkeypatch):
    """{{TOOLKIT}} was rendered `$env:X4_TOOLKIT` for every Windows target. OpenCode runs bash
    when told to (X4_OPENCODE_SHELL=bash, which its plugin also reads), and bash expands
    `$env:X4_TOOLKIT` to ':X4_TOOLKIT' -- every skill command broke. The Codex/generic copy
    keeps the PowerShell form (Codex runs PowerShell on Windows)."""
    if oc_shell:
        monkeypatch.setenv("X4_OPENCODE_SHELL", oc_shell)
    else:
        monkeypatch.delenv("X4_OPENCODE_SHELL", raising=False)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "all")
    assert r.returncode == 0, (r.stdout + r.stderr)[-1500:]
    oc = (dest / ".opencode" / "skills" / "x4-balance" / "SKILL.md").read_text(encoding="utf-8")
    ag = (dest / ".agents" / "skills" / "x4-balance" / "SKILL.md").read_text(encoding="utf-8")
    assert want in oc and not_want not in oc, oc[:400]
    assert "$env:X4_TOOLKIT" in ag and "{{TOOLKIT}}" not in ag, ag[:400]


# --- pre-arc minor (v4.0.0 review): DERIVED paths are spelled alike by both installers ----

def _cfg_values(f: pathlib.Path) -> dict:
    out = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def test_both_installers_spell_the_DERIVED_suffix_the_SAME_way(tmp_path):
    """install.sh derived `<toolkit>/reference`, `<profile>/debug.txt`, `<game>/extensions`
    with '/', install.ps1 with Join-Path's backslash -- two configs from one input. The
    derived suffix is '/' in both now (bash, PowerShell and Python all read it)."""
    got = {}
    for inst in ("sh", "ps1"):
        base = tmp_path / inst
        base.mkdir()
        dest = _fresh(base)
        r = _install(inst, base, dest, omit=("reference", "extensions"))
        assert r.returncode == 0, (r.stdout + r.stderr)[-1500:]
        v = _cfg_values(dest / "x4-paths.env")
        got[inst] = {k: v.get(k, "")[-len(suffix):] for k, suffix in
                     (("X4_REFERENCE", "/reference"), ("X4_DEBUGLOG", "/debug.txt"),
                      ("X4_EXTENSIONS", "/extensions"))}
    assert got["sh"] == got["ps1"] == {"X4_REFERENCE": "/reference", "X4_DEBUGLOG": "/debug.txt",
                                        "X4_EXTENSIONS": "/extensions"}, got


# --- C1 (install red-team 2026-10-04): --agent / -Agent takes a COMMA LIST ---------------- #

@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_C1_a_comma_list_installs_exactly_those_agents(installer, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "claude,codex", source=src)
    assert r.returncode == 0, _ok(r)
    for rel in ("CLAUDE.md", ".claude/settings.json", "AGENTS.md", ".codex/hooks.json"):
        assert (dest / rel).exists(), "--agent claude,codex did not install %s\n%s" % (rel, _ok(r))
    assert not (dest / ".opencode").exists(), "--agent claude,codex installed OpenCode"
    assert "claude, codex" in r.stdout and "opencode" not in r.stdout.split("Agents:")[-1].splitlines()[0], _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
@pytest.mark.parametrize("agent,word", [("claude,nonsense", "'nonsense'"), ("claude,all", "cannot be combined"),
                                        ("claude,", "empty")])
def test_C1_every_item_of_a_list_is_validated_before_writing(installer, agent, word, tmp_path):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", agent, source=src)
    assert r.returncode == 2, _ok(r)
    assert word in r.stdout + r.stderr, _ok(r)
    assert not any(dest.iterdir()), "a refused install wrote something"


# --- C4 / C5 (install red-team 2026-10-04): every summary line true and specific -------- #

@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_C4_the_summary_ALWAYS_states_the_reference_protection(installer, tmp_path):
    """No reference/ yet: the summary still says the protection is not applied by the
    installer, and how it gets applied -- never silence."""
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "claude")
    assert r.returncode == 0, _ok(r)
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("Reference:")]
    assert line, "the summary says nothing about reference/ protection\n" + _ok(r)
    body = r.stdout[r.stdout.index(line[0]):]
    assert "not applied by the installer" in body and "unpack-reference.sh" in body, _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_C4_an_unprotected_reference_names_the_exact_apply_command(installer, tmp_path):
    """R2-b (second install red-team): the command a HUMAN types is plain `apply`, which shows
    the folder and a count and ASKS (B3); `--yes` skipped that question. The reference here is
    a FINISHED unpack (it carries the sentinel), so apply would act on it."""
    dest = _fresh(tmp_path)
    (dest / "reference" / "libraries").mkdir(parents=True)
    (dest / "reference" / "libraries" / "wares.xml").write_text("<wares/>", encoding="utf-8")
    (dest / "reference" / ".unpacked-and-locked").write_text("Re-unpacked on 2026-10-04.\n",
                                                           encoding="utf-8")
    r = _install(installer, tmp_path, dest, "--agent", "claude")
    assert r.returncode == 0, _ok(r)
    assert "python scripts/x4refguard.py apply" in r.stdout, _ok(r)
    assert "apply --yes" not in r.stdout, _ok(r)
    assert "state: absent" not in r.stdout and "not applied" in r.stdout, _ok(r)
    nxt = [ln for ln in r.stdout.splitlines() if ln.startswith("Next:")]
    assert nxt and "unpack-reference.sh" not in nxt[0], "asked to unpack a finished tree\n" + _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_R2_a_reference_WITHOUT_the_sentinel_is_named_as_unfinished_not_sent_to_apply(installer,
                                                                                     tmp_path):
    """apply REFUSES a tree without .unpacked-and-locked, so telling the user to run it was a
    dead end (R2-a). The summary says why, and Next names the unpack."""
    dest = _fresh(tmp_path)
    (dest / "reference" / "libraries").mkdir(parents=True)
    (dest / "reference" / "libraries" / "wares.xml").write_text("<wares/>", encoding="utf-8")
    r = _install(installer, tmp_path, dest, "--agent", "claude")
    assert r.returncode == 0, _ok(r)
    ref = [ln for ln in r.stdout.splitlines() if ln.startswith("Reference:")]
    assert ref and ".unpacked-and-locked" in ref[0], _ok(r)
    nxt = [ln for ln in r.stdout.splitlines() if ln.startswith("Next:")]
    assert nxt and "unpack-reference.sh" in nxt[0] and ".unpacked-and-locked" in nxt[0], _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_C5_Next_does_not_ask_to_set_X4_GAME_when_it_is_set(installer, tmp_path):
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "claude")
    assert r.returncode == 0, _ok(r)
    assert "set X4_GAME if blank" not in r.stdout, _ok(r)
    nxt = [ln for ln in r.stdout.splitlines() if ln.startswith("Next:")]
    assert nxt and "unpack-reference.sh" in nxt[0] and "X4_GAME" not in nxt[0], _ok(r)


def test_C5_TWIN_both_installers_name_X4_GAME_only_on_the_BLANK_branch():
    """Static on purpose: an install run with no --game AUTO-DETECTS the game, which on a
    developer machine is the real install -- a test must never resolve that. So the blank
    branch is pinned by text: each installer tests the game value and names X4_GAME there."""
    sh = INSTALL_SH.read_text(encoding="utf-8")
    ps = INSTALL_PS1.read_text(encoding="utf-8")
    assert 'if [ -z "$GAME" ]; then' in sh and 'Next:      X4_GAME is blank' in sh
    assert 'if (-not $Game) {' in ps and 'Next:      X4_GAME is blank' in ps
    assert 'set X4_GAME if blank' not in sh + ps


def test_C5_setup_says_the_config_is_IN_PLACE_not_already_present(tmp_path):
    """The installers write x4-paths.env and then run setup.sh, which said 'already present'
    about the file the installer wrote seconds earlier."""
    b = _bash()
    if b is None:
        pytest.skip("no Git Bash")
    root = tmp_path / "tk"
    root.mkdir()
    shutil.copy2(ROOT / "setup.sh", root / "setup.sh")
    (root / "x4-paths.env").write_text('X4_GAME="/g"\n', encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("X4_")}
    env["CLAUDE_PROJECT_DIR"] = str(root)
    r = subprocess.run([b, str(root / "setup.sh"), "--config-only"], capture_output=True, text=True,
                       env=env, cwd=str(root))
    assert "already present" not in r.stdout and "in place" in r.stdout, r.stdout + r.stderr


# --- cosmetic (install red-team 2026-10-04): the 3.x .claude/x4-paths.env.example ---------- #
#
# An upgrade left the 3.x example beside the 4.0 one at the root. It is removed ONLY when it is
# an unedited shipped copy (its canonical hash is a `.claude/x4-paths.env.example` row of
# scripts/shipped-instruction-hashes.txt: 3 distinct over 19 of 23 tags, MEASURED); an edited
# one is KEPT and said so -- a user's file is never deleted.

def _v3_example() -> bytes:
    r = subprocess.run(["git", "-C", str(ROOT), "cat-file", "blob", "v3.3.1:.claude/x4-paths.env.example"],
                       capture_output=True)
    if r.returncode != 0:
        pytest.skip("no v3.3.1 tag in this clone (shallow?) -- NOT CHECKED")
    return r.stdout


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_an_UNEDITED_3x_example_is_removed_by_the_upgrade(installer, tmp_path):
    dest = _fresh(tmp_path)
    old = dest / ".claude" / "x4-paths.env.example"
    old.parent.mkdir(parents=True)
    old.write_bytes(_v3_example().replace(b"\n", b"\r\n"))       # CRLF: still the shipped file
    previous = old.read_bytes()
    r = _install(installer, tmp_path, dest, "--agent", "claude")
    assert r.returncode == 0, _ok(r)
    assert (dest / "x4-paths.env.example").is_file()
    assert not old.exists(), "the unedited 3.x example was left behind\n" + _ok(r)
    assert any(p.name.endswith("-x4-paths.env.example") and p.read_bytes() == previous
               for p in pathlib.Path(os.environ["X4_RECYCLE_TEST_BIN"]).iterdir())
    assert "removed" in r.stdout and ".claude/x4-paths.env.example" in r.stdout.replace("\\", "/"), _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_TWIN_an_EDITED_3x_example_is_KEPT_and_said_so(installer, tmp_path):
    dest = _fresh(tmp_path)
    old = dest / ".claude" / "x4-paths.env.example"
    old.parent.mkdir(parents=True)
    mine = _v3_example() + b"# my own note\n"
    old.write_bytes(mine)
    r = _install(installer, tmp_path, dest, "--agent", "claude")
    assert r.returncode == 0, _ok(r)
    assert old.read_bytes() == mine, "an EDITED example was changed or deleted"
    assert "kept" in r.stdout.lower() and ".claude/x4-paths.env.example" in r.stdout.replace("\\", "/"), _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_the_dry_run_names_the_3x_example_removal(installer, tmp_path):
    dest = _fresh(tmp_path)
    old = dest / ".claude" / "x4-paths.env.example"
    old.parent.mkdir(parents=True)
    old.write_bytes(_v3_example())
    r = _install(installer, tmp_path, dest, "--agent", "claude", "--dry-run")
    assert r.returncode == 0, _ok(r)
    assert old.is_file(), "the dry run deleted it"
    assert "would be removed" in r.stdout, _ok(r)



# --- R2 (second install red-team, 2026-10-04): every message names what to do next ---- #

@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_R2e_the_kept_CLAUDE_md_says_how_to_see_only_YOUR_edits(installer, tmp_path):
    """The kept X4-NOTES.pre-4.0.md is the WHOLE old CLAUDE.md. The previous install's
    CHANGELOG names its version, so point at THAT release's shipped CLAUDE.md and a diff."""
    src = _agent_source(tmp_path)
    _known(src, _V3_SHIPPED)
    dest = _fresh(tmp_path)
    (dest / "CLAUDE.md").write_bytes((_V3_SHIPPED + "my own rule\n").encode("utf-8"))
    (dest / "CHANGELOG.md").write_text("# Changelog\n\n## v3.3.1 \u2014 2026-09-29\n\nx\n\n"
                                       "## v3.3.0 \u2014 2026-09-27\n", encoding="utf-8")
    r = _install(installer, tmp_path, dest, "--agent", "claude", source=src)
    assert r.returncode == 0, _ok(r)
    assert "/v3.3.1/CLAUDE.md" in r.stdout and "v3.3.0" not in r.stdout, _ok(r)
    assert "git diff --no-index" in r.stdout, _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_R2e_TWIN_no_previous_CHANGELOG_points_at_the_releases_page(installer, tmp_path):
    src = _agent_source(tmp_path)
    _known(src, _V3_SHIPPED)
    dest = _fresh(tmp_path)
    (dest / "CLAUDE.md").write_bytes((_V3_SHIPPED + "my own rule\n").encode("utf-8"))
    r = _install(installer, tmp_path, dest, "--agent", "claude", source=src)
    assert r.returncode == 0, _ok(r)
    assert "/releases" in r.stdout and "git diff --no-index" in r.stdout, _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_R2f_the_over_existing_refusal_prints_the_FULL_rerun_command(installer, tmp_path):
    """It ended in `...`. Now: the user's own flags, plus the one that was missing."""
    import shlex
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest, "--agent", "claude", source=src).returncode == 0
    r = _install(installer, tmp_path, dest, "--agent", "claude", source=src, over_existing=False)
    assert r.returncode == 2, _ok(r)
    out = r.stdout + r.stderr
    flag = "--over-existing" if installer == "sh" else "-OverExisting"
    line = next((ln.strip() for ln in out.splitlines() if flag in ln and "install." in ln), None)
    assert line, _ok(r)
    assert not line.endswith("..."), line
    toks = shlex.split(line, posix=installer == "sh")
    norm = [t.strip('"').replace(chr(92), "/") for t in toks]
    want_dest = dest.as_posix()
    if installer == "sh":
        assert "--toolkit" in norm and norm[norm.index("--toolkit") + 1] == want_dest, line
        assert "--agent" in norm and norm[norm.index("--agent") + 1] == "claude", line
        assert norm[-1] == "--over-existing" and "--no-env" in norm and "--yes" in norm, line
    else:
        assert "-Toolkit" in norm and norm[norm.index("-Toolkit") + 1] == want_dest, line
        assert "-Agent" in norm and norm[norm.index("-Agent") + 1] == "claude", line
        assert norm[-1] == "-OverExisting" and "-NoEnv" in norm and "-Yes" in norm, line


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_R2_cosmetic_setup_does_not_ask_for_what_the_installer_just_did(installer, tmp_path):
    """setup.sh said 'x4-paths.env in place -- left as it is' about a file written seconds
    earlier, and 'Set X4_GAME in x4-paths.env' after --game had set it."""
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "claude")
    assert r.returncode == 0, _ok(r)
    assert "left as it is" not in r.stdout and "written by the installer" in r.stdout, _ok(r)
    assert "Set X4_GAME in x4-paths.env" not in r.stdout, _ok(r)


@pytest.mark.skipif(os.name != "nt", reason="the MSYS path spelling is Git Bash on Windows")
def test_R2_cosmetic_the_git_bash_installer_prints_a_WINDOWS_source_path(tmp_path):
    dest = _fresh(tmp_path)
    r = _install("sh", tmp_path, dest, "--agent", "claude", "--dry-run")
    first = next(ln for ln in r.stdout.splitlines() if "source:" in ln)
    src = first.split("source:", 1)[1].strip()
    assert not src.startswith("/"), first
    assert src.replace(chr(92), "/").lower() == ROOT.as_posix().lower(), first


@pytest.mark.skipif(os.name != "nt", reason="path separators are a Windows display question")
def test_R2_cosmetic_install_ps1_prints_ONE_separator_in_its_summary(tmp_path):
    dest = _fresh(tmp_path)
    r = _install("ps1", tmp_path, dest, "--agent", "claude")
    assert r.returncode == 0, _ok(r)
    summary = r.stdout[r.stdout.index("=== install complete"):]
    for ln in summary.splitlines():
        if ln.startswith(("Toolkit:", "Config:", "Verify:")):
            assert "/" not in ln.split(":", 1)[1], ln


@pytest.mark.parametrize("installer,agent", [
    ("sh", "all"), ("ps1", "all"),
    # SINGLE agents (release review T3): `--agent all` ships every carrier, so a module that
    # imported from one native carrier (.codex/hooks) at import time collected fine there and
    # died in a claude-only install. One installer per single agent keeps the cost bounded.
    ("sh", "claude"), ("sh", "codex"), ("sh", "opencode"), ("sh", "generic")])
def test_R2B1_an_INSTALLED_toolkits_suite_COLLECTS_and_counts_its_repo_only_skips(installer, agent,
                                                                                tmp_path):
    """SETUP_PROMPT.txt has the user run the installed toolkit's suite. Before R2-B1 it died in
    collection (3 errors: modules reading the repo-only agent/ source at import). A REAL install
    (both installers), then `pytest --collect-only` inside it, every X4_* path pinned to the
    sandbox: no collection error, and the repo-only modules are SKIPPED with the counted reason.

    ⚠ SCOPE: this proves COLLECTION only. `--collect-only` imports every module and evaluates
    skip markers, but RUNS NO TEST BODY -- a test that fails at runtime in an installed tree
    (a false red such as the export-ignore audit inside a game-root repo, delta review C) is
    NOT covered here. Running the installed suite for real is left to the release's cold
    install check. COST (MEASURED 2026-10-05, Windows): each parametrization makes a fresh
    install and `uv run` builds that install's own .venv, so the two cases build TWO venvs;
    18.3 s (sh) + 12.9 s (ps1) = ~31 s, ~0.1 GB written to the temp drive. The four
    single-agent cases add one install + venv each."""
    if shutil.which("uv") is None:
        pytest.skip("no uv to run the installed suite with")
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", agent)
    assert r.returncode == 0, _ok(r)
    assert not (dest / "agent").exists(), "an install copied the repo-only agent/ source"
    env = {k: v for k, v in os.environ.items() if not k.startswith("X4_")}
    env.update({"X4_TOOLKIT": str(dest), "X4_CONFIG": str(dest / "x4-paths.env"),
                "X4_GAME": str(tmp_path / "game"), "X4_PROFILE": str(tmp_path / "profile"),
                "X4_REFERENCE": str(dest / "reference"), "X4_MODS": str(tmp_path / "mods")})
    c = subprocess.run(["uv", "run", "python", "-m", "pytest", "--collect-only", "-q", "-rs",
                        "-p", "no:cacheprovider"], cwd=str(dest / "tools" / "x4validate"),
                       env=env, capture_output=True, text=True, timeout=600)
    out = c.stdout + c.stderr
    assert c.returncode == 0 and "ERROR collecting" not in out, out[-3000:]
    assert out.count("REPO-ONLY") >= 3, out[-3000:]


# ======================================================================================
# FX-B2 (v4.0.0 delta review + CI on da93d2d)
# ======================================================================================

@pytest.mark.parametrize("installer", ["ps1", "ps51"])
def test_FXB2_CI1_a_COMPLETE_install_run_IN_PROCESS_exits_0(installer, tmp_path):
    """CI on da93d2d: `& .\\install.ps1 ... -Yes` printed "=== install complete ===" and left
    $LASTEXITCODE = 2 -- the script fell off its end and the caller saw the LAST native exit
    (Write-RefguardStep's `x4refguard status`, 2 = "no reference/ tree yet"). MEASURED on 5.1
    and 7 in a sandbox. A complete install exits 0, explicitly."""
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "claude", inproc=True)
    assert "=== install complete" in r.stdout, _ok(r)
    assert r.returncode == 0, _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1", "ps51"])
def test_FXB2_2_the_READ_ONLY_opencode_refusal_is_the_crafted_one_on_EVERY_powershell(installer, tmp_path):
    """R4-5's refusal, on Windows PowerShell 5.1 too (the test ran under pwsh only). Under
    $ErrorActionPreference='Stop', 5.1 turns the renderer's stderr behind `*> $null` into a
    terminating NativeCommandError: a raw error and exit 1 instead of the READ-ONLY refusal."""
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest, "--agent", "opencode").returncode == 0, "first install failed"
    cfg = dest / ".opencode" / "opencode.jsonc"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "// a stale line\n", encoding="utf-8")
    (dest / "README.md").unlink()
    cfg.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    try:
        r = _install(installer, tmp_path, dest, "--agent", "opencode")
        out = r.stdout + r.stderr
        assert r.returncode == 1 and "REFUSING" in out and "READ-ONLY" in out, out[-1500:]
        assert "NativeCommandError" not in out, out[-1500:]
        assert not (dest / "README.md").exists(), "the copy ran before the refusal"
        # ...and the unlock it prints is the DESTINATION's x4lock, which really unlocks it
        # (FX-B2 #6: `scripts/x4lock.py` relative to the cwd was the SOURCE's, and looped).
        m = re.search(r'python "([^"]+x4lock\.py)" unlock "([^"]+)" --toolkit "([^"]+)"', out)
        assert m, out[-1500:]
        assert pathlib.Path(m.group(1)).resolve() == (dest / "scripts" / "x4lock.py").resolve()
        u = subprocess.run([sys.executable, m.group(1), "unlock", m.group(2), "--toolkit", m.group(3)],
                           capture_output=True, text=True, env=_doctor_env(tmp_path), timeout=300)
        assert u.returncode == 0 and os.access(cfg, os.W_OK), (u.returncode, u.stdout, u.stderr)
    finally:
        cfg.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.parametrize("installer", ["sh", "ps1", "ps51"])
def test_FXB2_2_the_summary_names_an_UNREADABLE_protection_state_not_no_tree(installer, tmp_path):
    """Write-RefguardStep swallowed x4refguard's stderr (5.1: a terminating error, caught) and
    said "no reference/ tree yet". A guard that gives NO answer is said as such. Driven by a
    destination x4refguard.py that prints nothing parseable."""
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest, "--agent", "claude").returncode == 0, "first install failed"
    (dest / "scripts" / "x4refguard.py").write_text(
        "import sys\nsys.stderr.write('boom\\n')\nprint('not json')\nsys.exit(1)\n", encoding="utf-8")
    r = _install(installer, tmp_path, dest, "--agent", "claude", from_dest=True)
    assert r.returncode == 0, _ok(r)
    assert "could NOT be read" in r.stdout, _ok(r)
    assert "no reference/ tree yet" not in r.stdout, _ok(r)


def _other_toolkit(tmp_path):
    """A second, installed-looking toolkit: what an inherited X4_TOOLKIT usually names."""
    other = tmp_path / "live-toolkit"
    (other / "tools" / "x4validate").mkdir(parents=True)
    (other / "CLAUDE.md").write_text("the user's live toolkit\n", encoding="utf-8")
    return other


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_FXB2_3_an_INHERITED_X4_TOOLKIT_is_not_a_named_destination(installer, tmp_path):
    """The installers adopted an inherited X4_TOOLKIT as the destination AND counted it as
    "named", so -Yes went straight to the over-existing refusal, whose re-run line added only
    --over-existing -- i.e. offered to overwrite the user's LIVE toolkit. Now: refused as an
    unnamed destination, said to come from the environment, no --over-existing offered."""
    _fresh(tmp_path)
    other = _other_toolkit(tmp_path)
    r = _install(installer, tmp_path, other, omit=("toolkit",), over_existing=False,
                 inherit={"X4_TOOLKIT": other})
    out = r.stdout + r.stderr
    assert r.returncode == 2, out[-2000:]
    assert "INHERITED" in out and "X4_TOOLKIT" in out, out[-2000:]
    assert "over-existing" not in out and "OverExisting" not in out, out[-2000:]
    assert not (other / "scripts").exists(), "it wrote into the inherited toolkit"
    assert (other / "CLAUDE.md").read_text(encoding="utf-8") == "the user's live toolkit\n"


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_FXB2_3_TWIN_an_explicit_toolkit_with_an_inherited_one_proceeds(installer, tmp_path):
    dest = _fresh(tmp_path)
    other = _other_toolkit(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "claude", inherit={"X4_TOOLKIT": other})
    assert r.returncode == 0, _ok(r)
    assert (dest / "scripts").is_dir() and not (other / "scripts").exists()


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_FXB2_3_TWIN_a_NAMED_destination_still_gets_the_over_existing_rerun_line(installer, tmp_path):
    """Clause twin: the rerun line is withheld only for an UNNAMED destination."""
    dest = _fresh(tmp_path)
    assert _install(installer, tmp_path, dest, "--agent", "claude").returncode == 0
    r = _install(installer, tmp_path, dest, "--agent", "claude", over_existing=False)
    out = r.stdout + r.stderr
    assert r.returncode == 2, out[-2000:]
    assert "--over-existing" in out or "-OverExisting" in out, out[-2000:]
    assert "no upgrade command is offered" not in out, out[-2000:]


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_FXB2_3_global_REFUSES_an_inherited_X4_TOOLKIT_naming_another_toolkit(installer, tmp_path):
    _fresh(tmp_path)
    other = _other_toolkit(tmp_path)
    (other / ".claude" / "skills" / "x4-foreign").mkdir(parents=True)
    r = _install(installer, tmp_path, tmp_path / "unused", method="global", omit=("toolkit",),
                 inherit={"X4_TOOLKIT": other})
    out = r.stdout + r.stderr
    assert r.returncode == 2 and "REFUSING" in out and "X4_TOOLKIT" in out, out[-2000:]
    assert not (tmp_path / "fake-claude-home" / "skills").exists(), "it installed skills anyway"


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_FXB2_1b_an_INHERITED_X4_REFERENCE_is_printed_before_it_is_written(installer, tmp_path):
    dest = _fresh(tmp_path)
    ref = tmp_path / "inherited-ref"
    r = _install(installer, tmp_path, dest, "--agent", "claude", omit=("reference",),
                 inherit={"X4_REFERENCE": ref.as_posix()})
    assert r.returncode == 0, _ok(r)
    line = [ln for ln in r.stdout.splitlines() if "X4_REFERENCE from your environment" in ln]
    assert line and "inherited-ref" in line[0], _ok(r)
    assert "inherited-ref" in (dest / "x4-paths.env").read_text(encoding="utf-8")


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_FXB2_1b_TWIN_a_reference_FLAG_is_not_called_inherited(installer, tmp_path):
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "claude",
                 inherit={"X4_REFERENCE": (tmp_path / "inherited-ref").as_posix()})
    assert r.returncode == 0, _ok(r)
    assert "X4_REFERENCE from your environment" not in r.stdout, _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_FXB2_4_unpack_is_given_the_installed_toolkit(installer, tmp_path):
    """Neither installer passed --toolkit to bin/unpack-reference.sh, although its comment said
    "the installers always pass it": with an inherited X4_TOOLKIT naming another toolkit and
    --unpack, the unpack REFUSED and the install ended INCOMPLETE. Driven with a fake xrcat and
    a stub x4refguard (the real unpack path, no game, no ACL)."""
    dest = _fresh(tmp_path)
    other = _other_toolkit(tmp_path)
    (tmp_path / "game" / "01.cat").write_text("", encoding="utf-8")
    fake = tmp_path / "fakexrcat"
    fake.write_text('#!/usr/bin/env bash\nwhile [ $# -gt 0 ]; do [ "$1" = -out ] && o="$2"; shift; done\n'
                    'mkdir -p "$o/libraries"; echo x > "$o/libraries/f.xml"\n',
                    encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    stub = tmp_path / "stubguard.py"
    stub.write_text("import sys\nprint('{\"state\": \"absent\"}')\nsys.exit(0)\n", encoding="utf-8")
    r = _install(installer, tmp_path, dest, "--agent", "claude", "--unpack",
                 inherit={"X4_TOOLKIT": other, "X4_XRCAT": fake.as_posix(), "X4_UNPACK_FLOOR": "1",
                          "X4_REFGUARD_SCRIPT": stub.as_posix()})
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-2500:]
    assert "INCOMPLETE" not in out and "REFUSED" not in out, out[-2500:]
    assert (dest / "reference" / "libraries" / "f.xml").is_file(), out[-2500:]


@pytest.mark.parametrize("installer", ["sh", "ps1", "ps51"])
@pytest.mark.parametrize("agent,ok", [("claude codex", True), ("claude, codex", True),
                                      ("claude,,codex", False)])
def test_FXB2_6_agent_list_SEPARATORS_agree_across_installers(installer, agent, ok, tmp_path):
    """`-Agent "claude codex"` was accepted and `--agent "claude codex"` refused. Both now split
    on `\\s*,\\s*|\\s+`; an empty item is refused by both."""
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", agent, "--dry-run")
    out = r.stdout + r.stderr
    if ok:
        assert r.returncode == 0, out[-1500:]
    else:
        assert r.returncode == 2 and "empty item" in out, out[-1500:]


@pytest.mark.skipif(os.name != "nt", reason="the registry seam is Windows only")
@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_FXB2_6_R4_3_a_DIFFERENT_settings_json_env_value_is_REPORTED_and_nothing_written(
        installer, tmp_path, regkey):
    home = tmp_path / "fake-claude-home"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"env": {"X4_TOOLKIT": r"D:\elsewhere"}}),
                                        encoding="utf-8")
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, source=src, env_write=True, regkey=regkey)
    assert r.returncode == 0, _ok(r)
    assert _reg_get(regkey) is None, "a SECOND, conflicting value was written"
    assert "settings.json env" in r.stdout and "Left unchanged" in r.stdout, _ok(r)


@pytest.mark.skipif(os.name != "nt", reason="the registry seam is Windows only")
@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_FXB2_6_R4_3_a_DIFFERENT_process_env_value_is_REPORTED_on_windows(installer, tmp_path, regkey):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    other = _other_toolkit(tmp_path)
    r = _install(installer, tmp_path, dest, source=src, env_write=True, regkey=regkey,
                 inherit={"X4_TOOLKIT": other})
    assert r.returncode == 0, _ok(r)
    assert _reg_get(regkey) is None, "a SECOND, conflicting value was written"
    assert "environment)" in r.stdout and "Left unchanged" in r.stdout, _ok(r)


@pytest.mark.skipif(os.name != "nt", reason="the registry seam is Windows only")
@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_FXB2_6_R4_3_TWIN_an_EQUAL_process_env_value_still_SETS_the_user_value(installer, tmp_path, regkey):
    src = _agent_source(tmp_path)
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, source=src, env_write=True, regkey=regkey,
                 inherit={"X4_TOOLKIT": dest})
    assert r.returncode == 0, _ok(r)
    assert _reg_get(regkey) == str(dest.resolve()), _ok(r)


@pytest.mark.parametrize("loader", [True, False], ids=["guard-loader", "no-guard-copy"])
@pytest.mark.parametrize("line,set_", [('export X4_GAME="/g"', True), ("X4_GAME='/g'", True),
                                       ("# X4_GAME=/g", False)])
def test_FXB2_6_setup_reads_the_config_with_the_SAME_grammar(tmp_path, loader, line, set_):
    """setup.sh's _i_cfg_val was a sed that ignored `export KEY=` lines, so a config written
    that way read as "Set X4_GAME" right after it was set. It now asks the guards' own loader
    (the parser every tool shares); the sed fallback, for a root with no guard copy, takes
    `export` too. A commented line is a twin: still unset."""
    b = _bash()
    if b is None:
        pytest.skip("no Git Bash")
    root = tmp_path / "tk"
    root.mkdir()
    shutil.copy2(ROOT / "setup.sh", root / "setup.sh")
    if loader:
        (root / ".claude" / "hooks").mkdir(parents=True)
        shutil.copy2(common_hook_dir(ROOT) / "_x4-env.sh", root / ".claude" / "hooks" / "_x4-env.sh")
    (root / "x4-paths.env").write_text(line + "\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("X4_")}
    env["CLAUDE_PROJECT_DIR"] = str(root)
    r = subprocess.run([b, str(root / "setup.sh")], capture_output=True, text=True, env=env,
                       cwd=str(root), timeout=300)
    if set_:
        assert "X4_GAME is set in x4-paths.env" in r.stdout, r.stdout[-2000:]
    else:
        assert "Set X4_GAME in x4-paths.env" in r.stdout, r.stdout[-2000:]


# ---------------- FX-B3: --unpack gets THIS run's reference / config / game; X4_GAME inherited

def _unpack_seams(tmp_path):
    """A fake xrcat and a stub x4refguard: the real unpack path, no game, no ACL."""
    (tmp_path / "game" / "01.cat").write_text("", encoding="utf-8")
    fake = tmp_path / "fakexrcat"
    fake.write_text('#!/usr/bin/env bash\nwhile [ $# -gt 0 ]; do [ "$1" = -out ] && o="$2"; shift; done\n'
                    'mkdir -p "$o/libraries"; echo x > "$o/libraries/f.xml"\n',
                    encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    stub = tmp_path / "stubguard.py"
    stub.write_text("import sys\nprint('{\"state\": \"absent\"}')\nsys.exit(0)\n", encoding="utf-8")
    return {"X4_XRCAT": fake.as_posix(), "X4_UNPACK_FLOOR": "1", "X4_REFGUARD_SCRIPT": stub.as_posix()}


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_FXB3_unpack_uses_the_reference_THIS_run_configured(installer, tmp_path):
    """Reviewer F M1: `--reference R2 --unpack` in a shell exporting a different X4_REFERENCE.
    The installers passed --toolkit but not --reference, so the unpack saw the inherited
    value disagree with the config R2 and REFUSED: install INCOMPLETE."""
    dest = _fresh(tmp_path)
    r2, other = tmp_path / "r2", tmp_path / "inherited-ref"
    r = _install(installer, tmp_path, dest, "--agent", "claude", "--reference", r2.as_posix(),
                 "--unpack", inherit={"X4_REFERENCE": other.as_posix(), **_unpack_seams(tmp_path)})
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-2500:]
    assert "INCOMPLETE" not in out and "REFUSED" not in out, out[-2500:]
    assert (r2 / "libraries" / "f.xml").is_file() and not other.exists(), out[-2500:]


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_FXB3_unpack_reads_THIS_run_s_game_not_an_inherited_X4_GAME(installer, tmp_path):
    """--game G with an inherited X4_GAME naming another folder: the unpack's loader lets the
    environment win, so it read the inherited game (here: absent -> 'game dir not found')."""
    dest = _fresh(tmp_path)
    r = _install(installer, tmp_path, dest, "--agent", "claude", "--unpack",
                 inherit={"X4_GAME": (tmp_path / "not-this-game").as_posix(), **_unpack_seams(tmp_path)})
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "INCOMPLETE" not in out, out[-2500:]
    assert (dest / "reference" / "libraries" / "f.xml").is_file(), out[-2500:]


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_FXB3_unpack_reads_THIS_run_s_config_not_an_inherited_X4_CONFIG(installer, tmp_path):
    dest = _fresh(tmp_path)
    elsewhere = tmp_path / "elsewhere.env"
    elsewhere.write_text('X4_REFERENCE="%s"\n' % (tmp_path / "theirs").as_posix(), encoding="utf-8")
    r = _install(installer, tmp_path, dest, "--agent", "claude", "--unpack",
                 inherit={"X4_CONFIG": elsewhere.as_posix(), **_unpack_seams(tmp_path)})
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "INCOMPLETE" not in out, out[-2500:]
    assert (dest / "reference" / "libraries" / "f.xml").is_file(), out[-2500:]
    assert not (tmp_path / "theirs").exists(), out[-2500:]


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_FXB3_an_INHERITED_X4_GAME_is_not_a_named_in_game_destination(installer, tmp_path):
    """Reviewer F M6: an inherited X4_GAME counted as a NAMED destination, so an in-game
    install under --yes wrote into whatever game root the shell carried (a --method global
    install puts X4_GAME in every agent session). Refused, naming the variable; nothing written."""
    _fresh(tmp_path)
    game = tmp_path / "game"
    r = _install(installer, tmp_path, game, "--dry-run", method="in-game", omit=("game",),
                 over_existing=False, inherit={"X4_GAME": game.as_posix()})
    out = r.stdout + r.stderr
    assert r.returncode == 2, out[-2000:]
    assert "INHERITED" in out and "X4_GAME" in out, out[-2000:]
    assert not (game / "scripts").exists() and not (game / ".claude").exists()


@pytest.mark.parametrize("installer", ["sh", "ps1"])
def test_FXB3_TWIN_an_explicit_game_with_an_inherited_X4_GAME_proceeds(installer, tmp_path):
    _fresh(tmp_path)
    game = tmp_path / "game"
    r = _install(installer, tmp_path, game, "--dry-run", method="in-game", over_existing=False,
                 inherit={"X4_GAME": (tmp_path / "another-game").as_posix()})
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "REFUSING" not in out, out[-2000:]


def test_FXB3_every_CI_powershell_install_leg_is_ISOLATED():
    """Reviewer F M4: ci.yml isolated only the two `separate` PowerShell legs (FX-B2: HKCU
    persists across a runner's steps, so one leg's X4_TOOLKIT write reached the next as a
    "different existing value"). The in-game and global legs ran on the default key with the
    inherited X4_TOOLKIT. Every step that runs install.ps1 must set its OWN test key under
    X4ToolkitTests and drop X4_TOOLKIT, before its first install.ps1 call."""
    require_repo(".github/workflows/full-qualification.yml", why="CI installer source is not installed")
    text = (ROOT / ".github" / "workflows" / "full-qualification.yml").read_text(encoding="utf-8")
    # one block per step (no YAML library in the venv): split at each `- name:` item
    blocks = re.split(r"(?m)^\s*- name: ", text)[1:]
    steps = [{"name": b.splitlines()[0], "run": b} for b in blocks
             if "install.ps1" in b and ("shell: pwsh" in b or "shell: powershell" in b)]
    assert len(steps) >= 4, [st["name"] for st in steps]
    keys = []
    for st in steps:
        run = st["run"]
        first = run.index("install.ps1 -Method")
        m = re.search(r"\$env:X4_INSTALL_ENV_REGKEY = '(HKCU\\Software\\X4ToolkitTests\\[^']+)'",
                      run[:first])
        assert m, "%s: no isolated test key before install.ps1" % st["name"]
        assert "Remove-Item -LiteralPath env:X4_TOOLKIT" in run[:first], st["name"]
        keys.append(m.group(1))
    assert len(set(keys)) == len(keys), keys


def test_FXB4_every_CI_BASH_install_leg_that_runs_on_Windows_is_ISOLATED_too():
    """Reviewer I: the bash `separate` and `in-game` legs have no `if:`, so they run on the
    Windows runner too -- where install.sh writes X4_TOOLKIT through scripts/x4-userenv.ps1,
    i.e. the runner's REAL HKCU\\Environment. That script honours X4_INSTALL_ENV_REGKEY (its
    test seam, refused outside HKCU\\Software\\X4ToolkitTests\\), inherited from the leg. Every
    bash step not gated to Linux must export its OWN key, distinct from every other leg's
    (PowerShell legs included), and drop X4_TOOLKIT, before its first install.sh call."""
    require_repo(".github/workflows/full-qualification.yml", why="CI installer source is not installed")
    text = (ROOT / ".github" / "workflows" / "full-qualification.yml").read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^\s*- name: ", text)[1:]
    bash_steps = [b for b in blocks if "bash install.sh --method" in b and "shell: bash" in b
                  and not re.search(r"(?m)^\s*if:.*runner\.os == 'Linux'", b)]
    assert len(bash_steps) >= 2, [b.splitlines()[0] for b in bash_steps]
    keys = []
    for b in bash_steps:
        first = b.index("bash install.sh --method")
        m = re.search(r"export X4_INSTALL_ENV_REGKEY='(HKCU\\Software\\X4ToolkitTests\\[^']+)'",
                      b[:first])
        assert m, "%s: no isolated test key before install.sh" % b.splitlines()[0]
        assert "unset X4_TOOLKIT" in b[:first], b.splitlines()[0]
        keys.append(m.group(1))
    ps_keys = re.findall(r"\$env:X4_INSTALL_ENV_REGKEY = '(HKCU\\Software\\X4ToolkitTests\\[^']+)'",
                         text)
    assert len(set(keys + ps_keys)) == len(keys + ps_keys), keys + ps_keys
