"""4.3.1 task 6b: the X4 release profile, the harness `update-check`/`update-fetch` commands, and the session-start note.

The shared core (`tools/modding_harness/release.py`) does the checking and the verified download; this toolkit
supplies the repository, the zip's name, how to read the installed version, and the session-start line. No test here
reaches GitHub: every request goes through an injected callable (a fake GitHub serving releases/latest), a patched core
function that fails the test when it is called, or an off switch that must make no call at all.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from _layout import claude_hooks_source, require_repo

REPO = Path(__file__).resolve().parents[3]
HARNESS = REPO / "tools" / "x4_harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


x4u = _load("x4_update_under_test", HARNESS / "x4_update.py")
sess = _load("x4update_session_under_test", REPO / "scripts" / "x4update_session.py")
release = x4u.release

BASH = os.environ.get("X4_BASH") or shutil.which("bash")
if BASH and "system32" in BASH.lower():
    BASH = None
GW = r"C:\Program Files\Git\bin\bash.exe"
if not BASH and os.path.isfile(GW):
    BASH = GW
needs_bash = pytest.mark.skipif(not BASH, reason="no Git Bash -- the shell hook is NOT checked here")

NOW = 1_800_000_000.0
DAY = 24 * 3600
BAD_SWITCHES = [{"X4_UPDATE_CHECK": "off"}, {"X4_HOUSEKEEP_SWEEP": "off"}, {"MODDING_HARNESS_UPDATE_CHECK": "off"},
                {"X4_UPDATE_CHECK": "OFF"}]


def _install(tmp_path, version="4.3.0", name="toolkit"):
    root = tmp_path / name
    root.mkdir()
    (root / "tools").mkdir()
    (root / "tools" / "agent-harness.py").write_text("# entry\n", encoding="utf-8")
    if version:
        (root / "BUILD-INFO").write_text(f"version={version}\ncommit={'ab' * 20}\n", encoding="utf-8", newline="\n")
    return root


def _release_json(version="4.4.0", asset=True):
    name = x4u.PROFILE.asset.format(version=version)
    assets = [{"name": name, "digest": "sha256:" + "c" * 64, "size": 10,
               "browser_download_url": f"https://github.com/WingedGuardian/x4-ai-toolkit/releases/download/v{version}/{name}"}]
    return {"tag_name": "v" + version, "draft": False, "prerelease": False, "assets": assets if asset else []}


class FakeGitHub:
    """An injected http callable: serves releases/latest only, counts every request, never touches the network."""

    def __init__(self, version="4.4.0", fail=None, asset=True):
        self.calls, self.version, self.fail, self.asset = [], version, fail, asset

    def __call__(self, url, limit=0, accept="", stream_to=None):
        self.calls.append(url)
        if self.fail:
            raise self.fail
        assert url == "https://api.github.com/repos/WingedGuardian/x4-ai-toolkit/releases/latest", url
        return json.dumps(_release_json(self.version, self.asset)).encode("utf-8")


def _boom(*args, **kwargs):
    pytest.fail("a request was made: " + repr(args[:1]))


# ----------------------------------------------------------------------------------------- installed_version --

def test_installed_version_reads_build_info(tmp_path):
    assert x4u.installed_version(_install(tmp_path, "4.3.1")) == "4.3.1"


def test_installed_version_falls_back_to_the_installed_changelogs_release_heading(tmp_path):
    """A pre-4.3.1 release install has no BUILD-INFO; its CHANGELOG's first `## ` heading is `vX.Y.Z`."""
    root = _install(tmp_path, None)
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## v4.3.0 \u2014 2026-11-20\n\n- x\n\n## v4.2.0 \u2014 2026-11-18\n",
                                       encoding="utf-8")
    assert x4u.installed_version(root) == "4.3.0"
    # an unusable BUILD-INFO version does not mask the heading
    (root / "BUILD-INFO").write_text("version=\ncommit=\n", encoding="utf-8")
    assert x4u.installed_version(root) == "4.3.0"
    # a dry-run build's BUILD-INFO (scripts/build-release.sh: `version=` EMPTY, `build=dry-run`) is the same case
    (root / "BUILD-INFO").write_text("version=\ncommit=" + "ab" * 20 + "\nbuild=dry-run\n", encoding="utf-8")
    assert x4u.installed_version(root) == "4.3.0"
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n", encoding="utf-8")
    assert x4u.installed_version(root) is None, "a dry-run BUILD-INFO and an Unreleased heading name no release"


def test_installed_version_is_none_for_unreleased_a_git_checkout_and_nothing_at_all(tmp_path):
    root = _install(tmp_path, None)
    assert x4u.installed_version(root) is None                      # no BUILD-INFO, no CHANGELOG
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n- x\n\n## v4.3.0 \u2014 2026-11-20\n", encoding="utf-8")
    assert x4u.installed_version(root) is None                      # the FIRST heading decides, not the first version
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## v4.3.0 \u2014 2026-11-20\n", encoding="utf-8")
    assert x4u.installed_version(root) == "4.3.0"                   # control: the same file with the heading first
    (root / ".git").mkdir()
    assert x4u.installed_version(root) is None                      # a checkout: nothing to compare, whatever it reads


def test_a_git_checkout_is_never_a_release_even_with_a_stray_build_info(tmp_path):
    """M1 (6b review): BUILD-INFO is gitignored, so a copy can leave one in a development tree. It must not make the tree
    look like a release (daily checks, update offers, a guarded update aimed at a working tree). The twin: the same files
    without `.git` ARE that release. `.git` as a FILE (a worktree) counts the same."""
    root = _install(tmp_path, "4.3.2")
    assert x4u.installed_version(root) == "4.3.2"                   # twin: a release install reads its BUILD-INFO
    (root / ".git").mkdir()
    assert x4u.installed_version(root) is None
    (root / ".git").rmdir()
    (root / ".git").write_text("gitdir: ../elsewhere\n", encoding="utf-8")
    assert x4u.installed_version(root) is None
    # and none of the update machinery treats it as one
    assert sess.background_check(root, environ=_clean_env(), fetch=_boom, now=NOW) == "no-release"
    _cache_newer_unchecked(root)
    assert sess.note(root, environ={}) == ""


# --------------------------------------------------------------------------------------------- the profile ----

def _build_release_asset_template():
    text = (REPO / "scripts" / "build-release.sh").read_text(encoding="utf-8")
    prefix = re.search(r'^NAME_PREFIX="([^"]+)"$', text, re.M)
    out = re.search(r'^OUT="dist/\$\{NAME_PREFIX\}-\$\{TAG\}\.zip"$', text, re.M)
    assert prefix and out, "build-release.sh no longer names its zip the way this test parses it"
    return prefix.group(1) + "-v{version}.zip"                         # TAG is v<version>


def test_the_profile_asset_name_is_the_one_build_release_sh_makes():
    require_repo("scripts/build-release.sh", why="parses the build script's NAME_PREFIX")
    assert x4u.PROFILE.asset == _build_release_asset_template()
    assert x4u.PROFILE.repo == "WingedGuardian/x4-ai-toolkit"
    assert x4u.PROFILE.product == "The X4 toolkit"
    assert x4u.PROFILE.installed_version is x4u.installed_version


def test_the_profile_finds_the_published_asset_and_a_different_name_is_not_found():
    found = release.latest(x4u.PROFILE, FakeGitHub("4.4.0"))
    assert found["version"] == "4.4.0" and found["asset"]["name"] == "X4.Foundations.AI.Assistant.Toolkit-v4.4.0.zip"
    assert found["asset"]["sha256"] == "c" * 64
    assert release.latest(x4u.PROFILE, FakeGitHub("4.4.0", asset=False))["asset"] is None


# --------------------------------------------------------------------------------- update-check and the cache ----

def test_update_check_reports_a_newer_release_and_asks_github_once_a_day(tmp_path):
    root = _install(tmp_path, "4.3.0")
    fake = FakeGitHub("4.4.0")
    state = release.check(root, x4u.PROFILE, fetch=fake, now=NOW, environ={})
    assert state["status"] == "checked" and state["latest"] == "4.4.0"
    assert release.newer(state["latest"], x4u.installed_version(root))
    assert len(fake.calls) == 1
    release.check(root, x4u.PROFILE, fetch=fake, now=NOW + 3600, environ={})
    assert len(fake.calls) == 1, "a second check inside the day asked GitHub again"
    release.check(root, x4u.PROFILE, fetch=fake, now=NOW + DAY + 1, environ={})
    assert len(fake.calls) == 2, "the control: a day later it must ask again"


def _run_harness(args, root, monkeypatch, env=None):
    """`agent-harness.py <args>` in-process, with the core's `check` and `fetch` replaceable by the caller."""
    harness = _load("x4_agent_harness_under_test", REPO / "tools" / "agent-harness.py")
    for key in ("X4_UPDATE_CHECK", "X4_HOUSEKEEP_SWEEP", "MODDING_HARNESS_UPDATE_CHECK"):
        monkeypatch.delenv(key, raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    out = io.BytesIO()
    monkeypatch.setattr(harness.sys, "stdout", type("O", (), {"buffer": out})())
    code = harness.main(args)
    return code, json.loads(out.getvalue().decode("ascii"))


def test_the_harness_update_check_command_reports_and_exits_zero(tmp_path, monkeypatch):
    root = _install(tmp_path, "4.3.0")
    fake = FakeGitHub("4.4.0")
    original = release.check
    monkeypatch.setattr(release, "check", lambda r, p, **kw: original(r, p, **dict(kw, fetch=fake, now=NOW)))
    code, result = _run_harness(["update-check", "--installed-root", str(root)], root, monkeypatch)
    assert code == 0 and result["installed"] == "4.3.0" and result["latest"] == "4.4.0"
    assert result["update_available"] is True and result["status"] == "checked" and fake.calls


def test_offline_update_check_is_quiet_exit_zero_and_leaves_no_note(tmp_path, monkeypatch):
    root = _install(tmp_path, "4.3.0")
    fake = FakeGitHub(fail=urllib.error.URLError("no network"))
    original = release.check
    monkeypatch.setattr(release, "check", lambda r, p, **kw: original(r, p, **dict(kw, fetch=fake, now=NOW)))
    code, result = _run_harness(["update-check", "--installed-root", str(root)], root, monkeypatch)
    assert code == 0 and result["status"] == "unreachable" and result["update_available"] is False
    assert fake.calls, "the control: the offline fake was reached"
    assert sess.note(root, environ={}) == ""


def test_a_failed_update_fetch_says_why_and_exits_two_changing_nothing(tmp_path, monkeypatch):
    root = _install(tmp_path, "4.3.0")

    def refused(*args, **kwargs):
        raise ValueError("no newer release: 4.3.0 is installed and the latest is 4.3.0")
    monkeypatch.setattr(release, "fetch", refused)
    code, result = _run_harness(["update-fetch", "--installed-root", str(root)], root, monkeypatch)
    assert code == 2 and result["ok"] is False and "no newer release" in result["error"]
    assert result["installed_changed"] is False


@pytest.mark.parametrize("env", BAD_SWITCHES)
def test_every_off_switch_makes_update_check_ask_nobody(tmp_path, monkeypatch, env):
    root = _install(tmp_path, "4.3.0")
    monkeypatch.setattr(release, "latest", _boom)
    code, result = _run_harness(["update-check", "--installed-root", str(root)], root, monkeypatch, env)
    assert code == 0 and result["status"] == "off" and result["update_available"] is False
    assert result["installed"] == "4.3.0" and result["latest"] is None


def test_the_settings_file_off_switch_makes_update_check_ask_nobody(tmp_path, monkeypatch):
    root = _install(tmp_path, "4.3.0")
    (root / ".modding-agent-harness").mkdir()
    (root / ".modding-agent-harness" / "updates.json").write_text('{"v": 1, "check": false}', encoding="utf-8")
    monkeypatch.setattr(release, "latest", _boom)
    code, result = _run_harness(["update-check", "--installed-root", str(root), "--now"], root, monkeypatch)
    assert code == 0 and result["status"] == "off"


def test_the_off_switch_control_update_check_does_ask_when_nothing_is_off(tmp_path, monkeypatch):
    """The twin of the five tests above: with no switch the same call reaches the (patched) request."""
    root = _install(tmp_path, "4.3.0")
    seen = []
    monkeypatch.setattr(release, "latest", lambda profile, fetch=None: seen.append(profile.repo) or
                        {"tag": "v4.3.0", "version": "4.3.0", "asset": None})
    code, result = _run_harness(["update-check", "--installed-root", str(root)], root, monkeypatch)
    assert code == 0 and seen == ["WingedGuardian/x4-ai-toolkit"] and result["status"] == "checked"


# ------------------------------------------------------------------------------------- the session-start note ---

def _cache_newer_unchecked(root, version="9.9.9"):
    """Write a cache that says `version` is out, without going through the profile (a checkout has no installed release)."""
    release._write_state(root, {"v": 1, "status": "checked", "latest": version, "tag": "v" + version, "asset": None,
                                "checked_at": NOW})


def _cache_newer(root, version="4.4.0", now=NOW):
    state = release.check(root, x4u.PROFILE, fetch=FakeGitHub(version), now=now, environ={})
    assert state["latest"] == version


def test_the_note_reads_the_cache_only_and_shows_once_per_version(tmp_path, monkeypatch):
    root = _install(tmp_path, "4.3.0")
    _cache_newer(root, "4.4.0")
    monkeypatch.setattr(release, "latest", _boom)
    monkeypatch.setattr(release, "http_get", _boom)
    line = sess.note(root, environ={})
    assert "4.4.0" in line and "4.3.0" in line and "The X4 toolkit" in line and "update the toolkit" in line
    assert sess.note(root, environ={}) == "", "shown twice for one version"
    monkeypatch.undo()                                                # the cache is read-only above; now let it be refilled
    _cache_newer(root, "4.5.0", now=NOW + 2 * DAY)                    # a newer release is announced again
    assert "4.5.0" in sess.note(root, environ={})


def test_the_note_is_silent_without_a_cache_and_for_a_git_checkout(tmp_path):
    assert sess.note(_install(tmp_path, "4.3.0"), environ={}) == ""    # nothing cached yet
    root = _install(tmp_path, None, name="checkout")
    (root / ".git").mkdir()
    _cache_newer(root, "9.9.9")
    assert sess.note(root, environ={}) == "", "a checkout has no installed release to compare with"


@pytest.mark.parametrize("env", BAD_SWITCHES)
def test_every_off_switch_silences_the_update_line(tmp_path, env):
    root = _install(tmp_path, "4.3.0")
    _cache_newer(root)
    assert sess.note(root, environ=env) == ""
    assert "4.4.0" in sess.note(root, environ={}), "the control: the same cache speaks without the switch"


def test_the_settings_file_silences_the_update_line(tmp_path):
    root = _install(tmp_path, "4.3.0")
    _cache_newer(root)
    (root / ".modding-agent-harness" / "updates.json").write_text('{"v": 1, "check": false}', encoding="utf-8")
    assert sess.note(root, environ={}) == ""


MARKERS = (".modding-agent-harness/update-failed.json", ".modding-agent-harness/update-in-flight.json")


@pytest.mark.parametrize("marker", MARKERS)
@pytest.mark.parametrize("env", [{}, *BAD_SWITCHES])
def test_a_failed_or_unfinished_update_is_always_reported_even_with_checks_off(tmp_path, marker, env):
    root = _install(tmp_path, "4.3.0")
    assert "TOOLKIT UPDATE" not in sess.note(root, environ=env), "no marker, yet a marker line"       # control
    path = root / marker
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    assert "TOOLKIT UPDATE NEEDS THE USER" in sess.note(root, environ=env)


def test_the_marker_shows_every_session_not_once_and_even_for_a_checkout(tmp_path):
    root = _install(tmp_path, None)
    (root / ".git").mkdir()
    (root / MARKERS[0]).parent.mkdir(parents=True)
    (root / MARKERS[0]).write_text("{}", encoding="utf-8")
    assert "TOOLKIT UPDATE NEEDS THE USER" in sess.note(root, environ={})
    assert "TOOLKIT UPDATE NEEDS THE USER" in sess.note(root, environ={})


def test_the_note_never_raises(tmp_path, monkeypatch):
    root = _install(tmp_path, "4.3.0")

    def explode(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(release, "note", explode)
    assert sess.note(root, environ={}) == ""
    monkeypatch.setattr(sess, "_guarded_update", explode)
    assert isinstance(sess.note(root, environ={}), str)
    assert sess.main(["note"]) == 0 and sess.main(["check"]) == 0
    assert sess.main(["bogus"]) == 2                                 # a typo is the one thing it reports


# --------------------------------------------------------------------------- the background check, gated hard ---

def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTEST_CURRENT_TEST", "HARNESS_FIXTURE_ROOT", "X4_UPDATE_CHECK", "X4_HOUSEKEEP_SWEEP",
                        "MODDING_HARNESS_UPDATE_CHECK")}
    env.update(extra)
    return env


def test_the_background_check_asks_github_and_caches_when_nothing_forbids_it(tmp_path):
    root = _install(tmp_path, "4.3.0")
    fake = FakeGitHub("4.4.0")
    assert sess.background_check(root, environ=_clean_env(), fetch=fake, now=NOW) == "checked"
    assert len(fake.calls) == 1
    assert sess.background_check(root, environ=_clean_env(), fetch=fake, now=NOW + 60) == "checked"
    assert len(fake.calls) == 1, "twice in a minute"
    assert "4.4.0" in sess.note(root, environ={})


def test_no_background_check_runs_under_pytest_or_a_test_fixture(tmp_path):
    root = _install(tmp_path, "4.3.0")
    assert sess.background_check(root, environ=_clean_env(PYTEST_CURRENT_TEST="t::x"), fetch=_boom, now=NOW) == "test"
    assert sess.background_check(root, environ=_clean_env(HARNESS_FIXTURE_ROOT=str(tmp_path)), fetch=_boom) == "test"
    # the real pytest process this runs in carries the marker, so the default environment refuses too
    assert os.environ.get("PYTEST_CURRENT_TEST")
    assert sess.background_check(root, fetch=_boom) == "test"


@pytest.mark.parametrize("env", BAD_SWITCHES)
def test_every_off_switch_stops_the_background_check(tmp_path, env):
    root = _install(tmp_path, "4.3.0")
    assert sess.background_check(root, environ=_clean_env(**env), fetch=_boom, now=NOW) == "off"


def test_the_settings_file_stops_the_background_check(tmp_path):
    root = _install(tmp_path, "4.3.0")
    (root / ".modding-agent-harness").mkdir()
    (root / ".modding-agent-harness" / "updates.json").write_text('{"v": 1, "check": false}', encoding="utf-8")
    assert sess.background_check(root, environ=_clean_env(), fetch=_boom, now=NOW) == "off"


def test_no_background_check_without_an_installed_release_or_the_toolkits_own_entry(tmp_path):
    checkout = _install(tmp_path, None, name="checkout")
    (checkout / ".git").mkdir()
    assert sess.background_check(checkout, environ=_clean_env(), fetch=_boom, now=NOW) == "no-release"
    stranger = _install(tmp_path, "4.3.0", name="stranger")
    (stranger / "tools" / "agent-harness.py").unlink()
    assert sess.background_check(stranger, environ=_clean_env(), fetch=_boom, now=NOW) == "not-own-entry"


def test_an_unreachable_github_is_a_quiet_background_check(tmp_path):
    root = _install(tmp_path, "4.3.0")
    fake = FakeGitHub(fail=urllib.error.URLError("offline"))
    assert sess.background_check(root, environ=_clean_env(), fetch=fake, now=NOW) == "unreachable"
    assert sess.note(root, environ={}) == ""


# ------------------------------------------------------------------------------------- the generated layout ----

def test_x4_update_is_generated_into_the_harness_and_into_no_hook_folder():
    require_repo("agent/guards/adapters/x4_update.py", why="compares the adapter source with its generated copy")
    source = (REPO / "agent" / "guards" / "adapters" / "x4_update.py").read_bytes()
    assert (HARNESS / "x4_update.py").read_bytes() == source
    for hooks in (".claude/hooks", ".codex/hooks", ".opencode/hooks"):
        assert not (REPO / hooks / "x4_update.py").exists(), hooks


# ----------------------------------------------------------------------------- session-canary.sh, the hook ----

STUB = (
    "import sys\n"
    "from pathlib import Path\n"
    "here = Path(__file__).resolve().parent\n"
    "mode = sys.argv[1] if len(sys.argv) > 1 else ''\n"
    "(here / ('ran-' + mode)).write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n"
    "if (here / 'crash').exists():\n"
    "    raise SystemExit('Traceback: the helper crashed')\n"
    "if mode == 'note':\n"
    "    print('X4-UPDATE-NOTE-LINE')\n"
)


def _hook_tree(tmp_path, stub=STUB, cache=True):
    require_repo("agent/guards/claude-hooks", why="copies the hook source")
    hooks = tmp_path / "proj" / ".claude" / "hooks"
    shutil.copytree(claude_hooks_source(REPO, "the session hooks under test"), hooks)
    toolkit = tmp_path / "tk"
    (toolkit / "scripts").mkdir(parents=True)
    if stub is not None:
        (toolkit / "scripts" / "x4update_session.py").write_text(stub, encoding="utf-8")
    if cache:                                       # a check has run before: the helper's `note` has something to read
        (toolkit / ".modding-agent-harness").mkdir()
        (toolkit / ".modding-agent-harness" / "update-check.json").write_text('{"v": 1}', encoding="utf-8")
    env = dict(os.environ, X4_TOOLKIT=str(toolkit), CLAUDE_PROJECT_DIR=str(tmp_path / "proj"), X4_SWEEP_IN_TESTS="1")
    for key in ("X4_HOUSEKEEP_SWEEP", "X4_FRESH_AUTO", "X4_UPDATE_CHECK", "X4_DEPLOY_DRIFT"):
        env.pop(key, None)
    env["X4_DEPLOY_DRIFT"] = "off"
    return hooks, toolkit, env


def _canary(hooks, env):
    return subprocess.run([BASH, str(hooks / "session-canary.sh")], input=b"{}", capture_output=True, env=env, timeout=120)


def _ran(toolkit, mode, wait=10):
    import time
    flag = toolkit / "scripts" / ("ran-" + mode)
    for _ in range(int(wait / 0.2)):
        if flag.exists():
            return True
        time.sleep(0.2)
    return flag.exists()


@needs_bash
def test_session_canary_prints_the_update_note_and_starts_the_background_check(tmp_path):
    hooks, toolkit, env = _hook_tree(tmp_path)
    done = _canary(hooks, env)
    assert done.returncode == 0
    assert "X4-UPDATE-NOTE-LINE" in done.stdout.decode("utf-8", "replace")
    assert _ran(toolkit, "check"), "the background check was not started"


@needs_bash
def test_session_canary_exits_zero_and_keeps_its_report_when_the_helper_crashes(tmp_path):
    hooks, toolkit, env = _hook_tree(tmp_path)
    (toolkit / "scripts" / "crash").write_text("", encoding="utf-8")
    (hooks.parent / "housekeep-session.txt").write_text("HOUSEKEEP-NOTE-LINE\n", encoding="utf-8")
    done = _canary(hooks, env)
    out = done.stdout.decode("utf-8", "replace")
    assert done.returncode == 0, done.stderr
    assert "HOUSEKEEP-NOTE-LINE" in out, "the crash cost the session the rest of its banner"
    assert "Traceback" not in out and "crashed" not in out
    assert _ran(toolkit, "note")                                     # control: it did run, and did crash


@needs_bash
def test_session_canary_without_the_helper_says_nothing_and_exits_zero(tmp_path):
    hooks, toolkit, env = _hook_tree(tmp_path, stub=None)
    done = _canary(hooks, env)
    assert done.returncode == 0
    assert "X4-UPDATE" not in done.stdout.decode("utf-8", "replace")


@needs_bash
@pytest.mark.parametrize("off", [("X4_UPDATE_CHECK", "off"), ("X4_HOUSEKEEP_SWEEP", "off"),
                                 ("MODDING_HARNESS_UPDATE_CHECK", "off"), ("X4_SWEEP_IN_TESTS", None)])
def test_each_off_switch_stops_the_hooks_background_launch_but_never_the_note(tmp_path, off):
    """The note runs every session (the helper itself decides what to print: a failed-update marker must show with
    checks off); only the network-capable `check` is gated in the hook, and again inside the helper."""
    hooks, toolkit, env = _hook_tree(tmp_path)
    key, value = off
    if value is None:
        env.pop(key, None)
    else:
        env[key] = value
    done = _canary(hooks, env)
    assert done.returncode == 0 and "X4-UPDATE-NOTE-LINE" in done.stdout.decode("utf-8", "replace")
    assert not _ran(toolkit, "check", wait=3), f"{key} did not stop the background check"


@needs_bash
def test_session_canary_only_runs_the_toolkits_own_helper(tmp_path):
    hooks, toolkit, env = _hook_tree(tmp_path, stub=None)
    (tmp_path / "proj" / "scripts").mkdir()
    (tmp_path / "proj" / "scripts" / "x4update_session.py").write_text(STUB, encoding="utf-8")
    done = _canary(hooks, env)
    assert done.returncode == 0 and "X4-UPDATE-NOTE-LINE" not in done.stdout.decode("utf-8", "replace")


def test_the_hook_launches_the_background_check_the_way_the_x4fresh_launch_does_never_detached():
    hook = (claude_hooks_source(REPO, "the session hook") / "session-canary.sh").read_text(encoding="utf-8")
    block = hook[hook.index("x4update_session.py"):]
    block = block[:block.index("fi\n)\"")]
    assert re.search(r'\( "\$_x4_hkpy" "\$\{X4_PYI\[@\]\}" -B "\$UP" check </dev/null >/dev/null 2>&1 & \)', block), block
    for word in ("DETACHED", "nohup", "setsid", "start /b", "Start-Process"):
        assert word not in block, word


MARKER_LINE = "TOOLKIT UPDATE NEEDS THE USER"


def _marker(toolkit, name="update-failed.json"):
    path = toolkit / ".modding-agent-harness" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("{}", encoding="utf-8")


@needs_bash
def test_the_helper_is_not_started_for_a_note_when_nothing_has_been_cached_and_no_marker_exists(tmp_path):
    """L1 (6b review): the extra Python start (about 0.1 s) is only paid when there is something to read."""
    hooks, toolkit, env = _hook_tree(tmp_path, cache=False)
    done = _canary(hooks, env)
    assert done.returncode == 0 and "X4-UPDATE-NOTE-LINE" not in done.stdout.decode("utf-8", "replace")
    assert not _ran(toolkit, "note", wait=3), "a Python start was paid for nothing"
    assert _ran(toolkit, "check"), "control: the background check still starts (it writes the first cache)"
    hooks2, toolkit2, env2 = _hook_tree(tmp_path / "second", cache=False)      # the twin: a marker alone is reason enough
    _marker(toolkit2)
    assert "X4-UPDATE-NOTE-LINE" in _canary(hooks2, env2).stdout.decode("utf-8", "replace")


@needs_bash
def test_a_failed_update_line_comes_before_everything_but_the_guards_banner_and_survives_the_output_cap(tmp_path):
    """M2 (6b review): it used to come last, after the preferences (up to 4 KiB), and the harness keeps only the HEAD of
    the output, so a maximal preferences block could cut the one line saying the guards may be broken."""
    hooks, toolkit, env = _hook_tree(tmp_path)
    _marker(toolkit)
    shutil.copytree(REPO / "tools" / "modding_harness", tmp_path / "proj" / "tools" / "modding_harness",
                    ignore=shutil.ignore_patterns("__pycache__"))       # the preferences renderer lives in the shared core
    (tmp_path / "proj" / "KNOWLEDGEBASE.local.md").write_text("## My preferences\n" + "Keep the local evidence.\n" * 150,
                                                          encoding="utf-8")
    env.update(X4_PYTHON=sys.executable, X4_CONFIG=str(tmp_path / "absent.env"), HOME=str(tmp_path / "home"))
    out = _canary(hooks, env).stdout.decode("utf-8", "replace")
    assert MARKER_LINE in out and "Keep the local evidence." in out, out[:600]
    assert out.index(MARKER_LINE) < out.index("Keep the local evidence."), "the marker comes after the preferences"
    assert out.index(MARKER_LINE) < 600, "the marker is not at the head"
    # the cap bites hard (preferences alone are 3.7 KB): the head, with the marker, is what survives
    env["X4_HOOK_MAX_CHARS"] = "1200"
    capped = _canary(hooks, env).stdout.decode("utf-8", "replace")
    assert "TRUNCATED" in capped, "control: the cap did not bite, so this proves nothing"
    assert MARKER_LINE in capped
    # with the guards off, the GUARDS OFF banner stays first and the marker second
    env["X4_GUARD"] = "off"
    env.pop("X4_HOOK_MAX_CHARS")
    both = _canary(hooks, env).stdout.decode("utf-8", "replace").lstrip()
    assert both.startswith("[x4 guards] *** GUARDS OFF") and both.index(MARKER_LINE) < both.index("Keep the local evidence.")


@needs_bash
@pytest.mark.parametrize("name", ["update-failed.json", "update-in-flight.json"])
def test_the_failed_update_line_needs_no_python(tmp_path, name):
    """L3 (6b review): with no usable Python the helper cannot run, and the marker must still be said (plain file tests)."""
    hooks, toolkit, env = _hook_tree(tmp_path)
    env["X4_PYTHON"] = "no_such_python_for_the_session_hook"
    clean = _canary(hooks, env)
    assert clean.returncode == 0 and MARKER_LINE not in clean.stdout.decode("utf-8", "replace")      # twin: no marker
    _marker(toolkit, name)
    done = _canary(hooks, env)
    out = done.stdout.decode("utf-8", "replace")
    assert done.returncode == 0 and MARKER_LINE in out and name in out, out[:800]
    assert not _ran(toolkit, "note", wait=3), "control: Python really was unavailable"


@needs_bash
def test_a_hung_background_check_never_holds_the_hook(tmp_path):
    """L2 (6b review): `check` hangs for 25 s; the hook still returns promptly and exits 0 (a plain regex on the `&` would
    pass for a launch that waits)."""
    import time
    hung = STUB + "if mode == 'check':\n    import time\n    time.sleep(25)\n"
    hooks, toolkit, env = _hook_tree(tmp_path, stub=hung)
    started = time.monotonic()
    done = _canary(hooks, env)
    elapsed = time.monotonic() - started
    assert done.returncode == 0 and "X4-UPDATE-NOTE-LINE" in done.stdout.decode("utf-8", "replace")
    assert _ran(toolkit, "check", wait=5), "control: the hanging check was started"
    assert elapsed < 20, f"the hook waited {elapsed:.1f} s for a hung background check"


def test_a_hung_note_gives_up_by_itself_and_says_so(tmp_path):
    """L2: the helper times `note` out (env X4_UPDATE_NOTE_TIMEOUT_S lowers the limit for the test) and exits 0 with one line."""
    import time
    toolkit = tmp_path / "tk"
    (toolkit / "scripts").mkdir(parents=True)
    (toolkit / "tools" / "x4_harness").mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "x4update_session.py", toolkit / "scripts" / "x4update_session.py")
    (toolkit / "tools" / "x4_harness" / "x4_update.py").write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
    env = dict(os.environ, X4_UPDATE_NOTE_TIMEOUT_S="1")
    started = time.monotonic()
    done = subprocess.run([sys.executable, "-B", str(toolkit / "scripts" / "x4update_session.py"), "note"],
                          capture_output=True, env=env, timeout=60)
    elapsed = time.monotonic() - started
    assert done.returncode == 0 and elapsed < 30, (done.returncode, elapsed, done.stderr)
    assert b"timed out" in done.stdout


def test_a_failed_download_is_exit_two_nothing_installed_changed(tmp_path, monkeypatch):
    """L5 (6b review): update-fetch only reads, downloads aside and unpacks aside, so every failure of it, an OSError mid
    download included, is the core's exit 2 ("refused/failed, nothing installed changed"); the skill says the same."""
    root = _install(tmp_path, "4.3.0")

    def died(*args, **kwargs):
        raise OSError("the connection was reset half way through the download")
    monkeypatch.setattr(release, "fetch", died)
    code, result = _run_harness(["update-fetch", "--installed-root", str(root)], root, monkeypatch)
    assert code == 2 and result["ok"] is False and result["installed_changed"] is False
    skill = (REPO / "agent" / "skills" / "x4-toolkit-update" / "SKILL.md").read_text(encoding="utf-8")
    assert "`2` when it failed for any reason" in skill and "nothing installed has changed" in skill
