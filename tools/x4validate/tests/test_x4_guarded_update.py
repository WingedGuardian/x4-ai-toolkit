"""4.3.1 task 6c: the guarded self-update (stage, review, apply, roll back, fail closed) wired for the X4 toolkit.

The shared core (`tools/modding_harness/guarded_update.py`) does the staging, the review, the apply with its setup and
canary, the rollback and the fail-closed markers. This toolkit supplies the `UpdatePolicy` (which paths are guard code,
DERIVED from the installed toolkit's own deployer and generator; how to run setup and the canary), the pre-check that
refuses to update an install that is already unhealthy, `x4doctor.py --update-canary`, and the harness commands.

No test reaches GitHub: every request goes to `FakeGitHub`, an injected callable that serves releases/latest, the tag's
commit, the previous release and the zips this file builds (an `X4AI/` folder plus `X4AI/BUILD-INFO`, the shape
`scripts/build-release.sh` makes). No test touches a real install: each builds a small installed-shape toolkit under
tmp_path whose setup.sh and scripts/x4doctor.py are stubs, so the real core runs them (no console window, no shell).
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from _layout import require_repo

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


x4u = _load("x4_update_guarded_under_test", HARNESS / "x4_update.py")
sess = _load("x4update_session_guarded_under_test", REPO / "scripts" / "x4update_session.py")
release = x4u.release
guarded = x4u.guarded
import x4_upgrade_render as render          # noqa: E402  (tools/x4_harness is on the path above)
import x4guard                               # noqa: E402

DEPLOYER = "tools/x4validate/scripts/deploy-claude-dir.py"
GENERATOR = "tools/x4validate/scripts/gen-agent-trees.py"
OLD, NEW = "4.3.1", "4.3.2"
COMMIT = "c0" * 20
OLD_COMMIT = "a1" * 20
API = "https://api.github.com/repos/WingedGuardian/x4-ai-toolkit/"
DOWNLOAD = "https://github.com/WingedGuardian/x4-ai-toolkit/releases/download/v{v}/{name}"

SETUP_STUB = (b"#!/bin/bash\n"
              b"[ -z \"${CLAUDE_PROJECT_DIR:-}\" ] || { echo \"CLAUDE_PROJECT_DIR leaked: $CLAUDE_PROJECT_DIR\"; exit 7; }\n"
              b"[ -f ./setup.sh ] || { echo 'not run in the install root'; exit 8; }\n"
              b"[ \"${UV_FROZEN:-}\" = 1 ] || { echo 'UV_FROZEN is not 1: uv sync could rewrite uv.lock'; exit 6; }\n"
              b"[ ! -f ./.fail-setup ] || { echo 'setup stub told to fail'; exit 1; }\n"
              b"echo setup-ok\n")

#: The installer's lists the way install.sh spells them (the update reads the RELEASE's install.sh).
FAKE_INSTALL_SH = (b'#!/bin/bash\n'
                   b'X4_COPY_ITEMS="tools scripts README.md CHANGELOG.md setup.sh install.sh x4-paths.env.example BUILD-INFO"\n'
                   b'X4_AGENT_ITEMS_claude=".claude CLAUDE.md"\n'
                   b'X4_AGENT_ITEMS_codex="AGENTS.md .codex .agents"\n'
                   b'X4_AGENT_ITEMS_generic="AGENTS.md .agents"\n'
                   b'X4_AGENT_ITEMS_opencode="AGENTS.md .opencode"\n'
                   b'X4_AGENT_NAMES="claude codex generic opencode"\n'
                   b'X4_COPY_PRUNE="tools/x4validate/.venv"\n'
                   b'X4_KEEP_LOCAL="x4-paths.env .claude/x4-paths.env .codex/hooks.json .opencode/opencode.jsonc"\n')


def _doctor_stub(version, fail=False):
    """x4doctor's stand-in: `detect_targets` (the update reads which agents an install holds from it) and a canary."""
    return ("import sys\nfrom pathlib import Path\n"
            "def detect_targets(root):\n"
            "    root = Path(root)\n"
            "    return {{'claude': (root / '.claude' / 'hooks').is_dir(), 'codex': (root / '.codex' / 'hooks').is_dir(),\n"
            "            'generic': (root / '.agents' / 'skills').is_dir(),\n"
            "            'opencode': (root / '.opencode' / 'plugins' / 'x4guard.js').is_file()}}\n"
            "if __name__ == '__main__':\n"
            "    if '--update-canary' not in sys.argv:\n        sys.exit(9)\n"
            "    if {fail!r} or Path('.fail-canary').exists():\n"
            "        print('x4doctor --update-canary: FAIL')\n"
            "        print('  FAIL    claude   guards.selftest.claude  stub {version} says the guards are inert')\n"
            "        sys.exit(1)\n"
            "    print('x4doctor --update-canary: PASS ({version})')\n").format(fail=fail, version=version).encode()


def _base_files(version=OLD):
    return {
        "README.md": ("readme " + version + "\n").encode(),
        "CHANGELOG.md": ("# Changelog\n\n## v" + version + " - 2026-11-27\n").encode(),
        ".claude/hooks/guard.sh": b"echo guard 4.3.1\n",
        "setup.sh": SETUP_STUB,
        "install.sh": FAKE_INSTALL_SH,
        "scripts/x4doctor.py": _doctor_stub("4.3.1"),
        DEPLOYER: (REPO / DEPLOYER).read_bytes(),
        GENERATOR: (REPO / GENERATOR).read_bytes(),
        "x4-paths.env.example": b"X4_GAME=\n",
    }


def _build_info(version, commit):
    return ("version={}\ncommit={}\n".format(version, commit)).encode()


def _zip(files, version, commit, *, top="X4AI/", extra=(), build_info=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(top + name, data)
        archive.writestr(top + "BUILD-INFO", _build_info(version, commit) if build_info is None else build_info)
        for info, data in extra:
            archive.writestr(info, data)
    return buf.getvalue()


def _asset(version, data, url=None, digest=None):
    name = x4u.PROFILE.asset.format(version=version)
    return {"name": name, "size": len(data),
            "digest": digest or "sha256:" + hashlib.sha256(data).hexdigest(),
            "browser_download_url": url or DOWNLOAD.format(v=version, name=name)}


class FakeGitHub:
    """An injected http callable: releases/latest, commits/<tag>, releases/tags/<previous> and the two zips."""

    def __init__(self, new_zip, *, version=NEW, commit=COMMIT, old_zip=None, url=None, digest=None):
        self.calls, self.version, self.commit = [], version, commit
        self.new_zip, self.old_zip, self.url, self.digest = new_zip, old_zip, url, digest

    def _downloads(self):
        out = {}
        new = _asset(self.version, self.new_zip, self.url, self.digest)
        out[new["browser_download_url"]] = self.new_zip
        if self.old_zip is not None:
            out[_asset(OLD, self.old_zip)["browser_download_url"]] = self.old_zip
        return out

    def __call__(self, url, limit=0, accept="", stream_to=None):
        self.calls.append(url)
        if url == API + "releases/latest":
            return json.dumps({"tag_name": "v" + self.version, "draft": False, "prerelease": False,
                               "assets": [_asset(self.version, self.new_zip, self.url, self.digest)]}).encode()
        if url == API + "commits/v" + self.version:
            return json.dumps({"sha": self.commit}).encode()
        if url == API + "releases/tags/v" + OLD:
            if self.old_zip is None:
                raise OSError("no previous release served")
            return json.dumps({"tag_name": "v" + OLD, "assets": [_asset(OLD, self.old_zip)]}).encode()
        downloads = self._downloads()
        if url in downloads and stream_to is not None:
            stream_to.write(downloads[url])
            return hashlib.sha256(downloads[url]).hexdigest(), len(downloads[url])
        raise AssertionError("FakeGitHub was asked for an unexpected URL: " + url)


def _write(root, files):
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _install(tmp_path, files=None, name="install"):
    """An installed 4.3.1 toolkit: the release files, its BUILD-INFO, and the user's own x4-paths.env and notes."""
    root = tmp_path / name
    root.mkdir()
    _write(root, files or _base_files())
    (root / "BUILD-INFO").write_bytes(_build_info(OLD, OLD_COMMIT))
    (root / "x4-paths.env").write_bytes(b"X4_GAME=C:/Games/X4\n")
    (root / "X4-NOTES.md").write_bytes(b"my private notes\n")
    return root


def _hashes(root, skip_state=True):
    out = {}
    for path in sorted(Path(root).rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and not (skip_state and rel.startswith(".modding-agent-harness/")):
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


@pytest.fixture
def world(tmp_path, monkeypatch):
    """(root, make_fake): an install, a private updates folder, and the core's fetch pointed at a FakeGitHub."""
    monkeypatch.setenv("MODDING_HARNESS_UPDATES_DIR", str(tmp_path / "updates"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    for key in ("X4_UPDATE_CHECK", "X4_HOUSEKEEP_SWEEP", "MODDING_HARNESS_UPDATE_CHECK"):
        monkeypatch.delenv(key, raising=False)
    root = _install(tmp_path)
    old_zip = _zip(_base_files(), OLD, OLD_COMMIT)
    original = release.fetch
    box = {}

    def serve(new_files=None, **kwargs):
        new = new_files if new_files is not None else _new_files()
        fake = FakeGitHub(kwargs.pop("new_zip", None) or _zip(new, kwargs.pop("zip_version", NEW),
                                                                kwargs.pop("zip_commit", COMMIT)),
                          old_zip=kwargs.pop("old_zip", old_zip), **kwargs)
        box["fake"] = fake
        return fake
    monkeypatch.setattr(release, "fetch", lambda r, p, http=None, environ=None: original(
        r, p, http=box["fake"], environ=environ))
    return root, serve


def _new_files(**changes):
    files = _base_files(NEW)
    files.update(changes)
    return files


def _run_harness(args, monkeypatch):
    """`tools/agent-harness.py <args>` in-process; returns (exit code, parsed JSON or None)."""
    harness = _load("x4_agent_harness_guarded_under_test", REPO / "tools" / "agent-harness.py")
    out = io.BytesIO()
    monkeypatch.setattr(harness.sys, "stdout", type("O", (), {"buffer": out})())
    try:
        code = harness.main(args)
    except SystemExit as exc:                     # argparse refusing a command line
        code = exc.code
    raw = out.getvalue().decode("ascii")
    return code, (json.loads(raw) if raw.strip() else None)


def _stage(root, monkeypatch):
    return _run_harness(["update-stage", "--installed-root", str(root)], monkeypatch)


def _apply(root, monkeypatch, staged, *extra, choices=None):
    args = ["update-apply", "--installed-root", str(root), "--bundle", staged["bundle"],
            "--review-sha256", staged["review_sha256"], *extra]
    if choices is not None:
        path = Path(staged["bundle"]).parent / "choices.json"
        path.write_text(json.dumps(choices), encoding="utf-8")
        args += ["--choices", str(path)]
    return _run_harness(args, monkeypatch)


# =================================================================================================== the policy ==

def _parse_tuple(path, name):
    """The literal value of `name` the way a reader of the file sees it: every top-level `=`/`+=` in order."""
    value = ()
    for node in ast.parse(Path(path).read_text(encoding="utf-8")).body:
        target = getattr(node, "target", None) or (node.targets[0] if isinstance(node, ast.Assign) else None)
        if isinstance(target, ast.Name) and target.id == name:
            literal = tuple(ast.literal_eval(node.value))
            value = value + literal if isinstance(node, ast.AugAssign) else literal
    return value


def _is_guard(pol, path):
    return bool(guarded.update_review.classify(
        [{"path": path, "action": "create", "installed_sha256": None, "candidate_sha256": "0" * 64}], pol)[0])


def test_guard_paths_cover_every_deployer_guard_source_and_every_guard_owned_entry():
    require_repo(DEPLOYER, GENERATOR, why="reads the deployer's and the generator's lists")
    pol = x4u.policy(REPO)
    sources = _parse_tuple(REPO / DEPLOYER, "GUARD_SOURCES")
    owned = [e for e in _parse_tuple(REPO / GENERATOR, "OWNED") if x4u.owned_is_guard(e)]
    assert sources and owned
    for entry in (*sources, *owned):
        bare = entry.rstrip("/")
        if entry.endswith("/") or (REPO / bare).is_dir():
            assert _is_guard(pol, bare + "/a.py") and _is_guard(pol, bare + "/deep/inside/a.py"), entry
        else:
            assert _is_guard(pol, bare), entry
    # the selection is not "everything": the instruction and skill trees are not guard code (twin)
    for entry in ("CLAUDE.md", "AGENTS.md", ".claude/skills/", ".agents/skills/", ".opencode/skills/"):
        assert entry in _parse_tuple(REPO / GENERATOR, "OWNED") and not x4u.owned_is_guard(entry), entry
        assert not _is_guard(pol, entry.rstrip("/") + ("/x4-balance/SKILL.md" if entry.endswith("/") else "")), entry


def test_the_owned_selection_alone_covers_the_hook_trees(tmp_path):
    """The deployer's GUARD_SOURCES names the hook trees too, so the union above cannot show the generator's half works.
    Here GUARD_SOURCES names one unrelated file, and every guard-shaped OWNED entry must still be guard."""
    root = _install(tmp_path)
    (root / DEPLOYER).write_text('GUARD_SOURCES = ("scripts/x4lock.py",)\n', encoding="utf-8")
    pol = x4u.policy(root)
    selected = [e for e in _parse_tuple(REPO / GENERATOR, "OWNED") if x4u.owned_is_guard(e)]
    for entry in (".claude/hooks/", ".codex/hooks/", ".codex/rules/", ".opencode/hooks/", ".claude/settings.json",
                  ".opencode/plugins/x4guard.js", "tools/x4_harness/", "tools/agent-harness.py"):
        assert entry in selected, entry
        assert _is_guard(pol, entry + "x.sh" if entry.endswith("/") else entry), entry


def test_the_extras_the_brief_names_are_guard_paths():
    pol = x4u.policy(REPO)
    for path in (".codex/hooks.json", ".opencode/opencode.jsonc", "agent/targets/codex/hooks.json.tmpl",
                 "agent/rules/codex-rules.yaml", "scripts/x4update_session.py", "scripts/x4refguard.py",
                 ".claude/settings.local.json.example"):
        assert _is_guard(pol, path), path
    assert not _is_guard(pol, "README.md") and not _is_guard(pol, "tools/x4validate/x4validate/_merge.py")


#: prepush-gate GUARD_PATHS entries whose shipped files are deliberately NOT guard code for an update: text the agent
#: reads (skills, subagents, instructions), never code that decides what an agent may do. A release changes these in
#: almost every version; listing each as a guard diff would bury the hook diffs the user must read. They are still in
#: the review, as "Other changes".
NOT_GUARD_FOR_UPDATE = (".claude/skills/", ".claude/agents/", ".opencode/skills/", ".opencode/X4-OPENCODE.md",
                        "agent/skills/", "agent/agents/", "agent/instructions/", "agent/README.md")


def _shipped(entries):
    """Tracked files under `entries` that `git archive` would put in a release (no export-ignore)."""
    listed = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "--", *entries], capture_output=True,
                            timeout=60, check=True).stdout.decode("utf-8").split("\0")
    listed = [name for name in listed if name]
    attrs = subprocess.run(["git", "-C", str(REPO), "check-attr", "-z", "export-ignore", "--stdin"],
                           input="\0".join(listed).encode() + b"\0", capture_output=True, timeout=60,
                           check=True).stdout.decode("utf-8").split("\0")
    ignored = {attrs[i] for i in range(0, len(attrs) - 2, 3) if attrs[i + 2] == "set"}
    return [name for name in listed if name not in ignored]


def test_guard_paths_cover_the_prepush_gates_guard_paths_where_they_ship():
    require_repo("scripts/prepush-gate.py", ".git", why="the pre-push gate is a checkout-only file")
    pol = x4u.policy(REPO)
    entries = _parse_tuple(REPO / "scripts" / "prepush-gate.py", "GUARD_PATHS")
    shipped = _shipped(entries)
    assert len(shipped) > 100, "control: the enumeration found the hook trees"
    assert "scripts/prepush-gate.py" not in shipped, "control: export-ignore was applied"
    exempt = [n for n in shipped if any(n == e or (e.endswith("/") and n.startswith(e)) for e in NOT_GUARD_FOR_UPDATE)]
    uncovered = [n for n in shipped if n not in exempt and not _is_guard(pol, n)]
    assert not uncovered, "shipped prepush guard paths the update policy does not treat as guard: %s" % uncovered
    assert exempt and not any(_is_guard(pol, n) for n in exempt if n.endswith("SKILL.md")), "twin: the exemption is real"


def test_every_generated_hook_file_is_classified_guard():
    require_repo(".claude/hooks", why="walks the generated hook trees")
    pol = x4u.policy(REPO)
    seen = 0
    for top in (".claude/hooks", ".codex/hooks", ".codex/rules", ".opencode/hooks", ".opencode/plugins",
                "tools/x4_harness", "agent/guards", "tools/modding_harness"):
        for path in (REPO / top).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                seen += 1
                assert _is_guard(pol, path.relative_to(REPO).as_posix()), path
    assert seen > 100
    for name in (".claude/settings.json", "tools/agent-harness.py", ".opencode/plugins/x4guard.js"):
        assert _is_guard(pol, name), name


def test_policy_files_name_setup_the_canary_and_the_derivation_sources(tmp_path):
    root = _install(tmp_path)
    pol = x4u.policy(root)
    for name in ("setup.sh", "scripts/x4doctor.py", DEPLOYER, GENERATOR):
        assert name in pol.policy_files, name
    # only what exists on this install: the fake install has no scripts/gitbash.py, so it is not named
    assert "scripts/gitbash.py" not in pol.policy_files
    full = x4u.policy(REPO)
    for name in ("setup.sh", "scripts/x4doctor.py", "scripts/gitbash.py", "tools/agent-harness.py",
                 "tools/x4_harness/x4_update.py", "tools/x4_harness/x4guard.py", DEPLOYER, GENERATOR):
        assert name in full.policy_files, name


def test_setup_and_canary_commands():
    pol = x4u.policy(REPO)
    bash, _ = x4guard.resolve_bash()
    assert pol.setup[0] == bash and pol.setup[1] == "-c" and '"$BASH" ./setup.sh' in pol.setup[2]
    assert pol.setup[3:] == ("x4-update-setup", sys.executable)
    assert pol.setup[2].splitlines()[0] == "unset CLAUDE_PROJECT_DIR; export UV_FROZEN=1 X4_SETUP_KEEP_LOCAL=1"
    assert pol.canary == (sys.executable, "-I", "scripts/x4doctor.py", "--update-canary")


OPENCODE_CONFIG_STUB = (b"import os, sys\nfrom pathlib import Path\n"
                        b"Path(sys.argv[sys.argv.index('--root') + 1], '.oc-rendered').write_text("
                        b"' '.join(sys.argv[1:]) + '|' + os.environ.get('X4_TOOLKIT', ''), encoding='utf-8')\n")


@pytest.mark.parametrize("has_opencode", [True, False])
def test_setup_re_renders_the_opencode_deny_rules_only_for_an_install_with_opencode(tmp_path, monkeypatch, has_opencode):
    """6c review F1: .opencode/opencode.jsonc is per-machine, never in a release, and nothing else on the update path
    renders it. The setup step renders it the way install.sh's write_opencode_config does."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    root = _install(tmp_path)
    _write(root, {".opencode/hooks/opencode_config.py": OPENCODE_CONFIG_STUB})
    if has_opencode:
        _write(root, {".opencode/plugins/x4guard.js": b"// plugin\n"})
    pol = x4u.policy(root)
    done = guarded.run(pol.setup, root, 120)
    assert done.returncode == 0, (done.stdout, done.stderr)
    rendered = root / ".oc-rendered"
    if not has_opencode:
        assert not rendered.exists()
        return
    args, toolkit = rendered.read_text(encoding="utf-8").split("|")
    assert args.split()[0] == "write" and Path(args.split()[2]).resolve() == root.resolve(), args
    assert Path(toolkit).resolve() == root.resolve()


def test_a_failing_opencode_render_fails_the_setup_step(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    root = _install(tmp_path)
    _write(root, {".opencode/plugins/x4guard.js": b"//\n", ".opencode/hooks/opencode_config.py": b"raise SystemExit(3)\n"})
    assert guarded.run(x4u.policy(root).setup, root, 120).returncode == 3


def test_no_git_bash_refuses_the_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(x4guard, "resolve_bash", lambda: (None, "no Git Bash found"))
    with pytest.raises(guarded.UpdateRefused, match="Git Bash"):
        x4u.policy(_install(tmp_path))


def test_the_derivation_works_on_an_installed_shape_tree_and_reads_the_installed_sources(tmp_path):
    """An install has no agent/ and no prepush gate; the deployer and the generator ship in tools/ (install.sh copies
    `tools` whole and neither is export-ignored). The policy reads THIS install's copies, never the running toolkit's."""
    root = _install(tmp_path)
    assert not (root / "agent").exists()
    text = (root / DEPLOYER).read_text(encoding="utf-8")
    (root / DEPLOYER).write_text(text.replace('GUARD_SOURCES = (', 'GUARD_SOURCES = ("scripts/zz-only-here.py", ', 1),
                                 encoding="utf-8")
    pol = x4u.policy(root)
    assert _is_guard(pol, "scripts/zz-only-here.py")
    assert not _is_guard(x4u.policy(REPO), "scripts/zz-only-here.py"), "twin: the running toolkit does not name it"
    for path in (".claude/hooks/x.sh", ".codex/hooks.json", "tools/x4_harness/x4guard.py", "agent/guards/x.py"):
        assert _is_guard(pol, path), path


def test_the_two_derivation_sources_ship_in_an_install():
    require_repo("install.sh", ".git", why="parses the installer's copy list and the archive attributes")
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    items = text.split('X4_COPY_ITEMS="', 1)[1].split('"', 1)[0].split()
    assert "tools" in items and "setup.sh" in items and "scripts" in items
    assert set(_shipped([DEPLOYER, GENERATOR, "setup.sh", "scripts/x4doctor.py"])) == {
        DEPLOYER, GENERATOR, "setup.sh", "scripts/x4doctor.py"}


@pytest.mark.parametrize("which,text", [
    (DEPLOYER, "GUARD_SOURCES = tuple(x for x in ('a',))\n"),
    (DEPLOYER, "GUARD_SOURCES = (\n"),                                        # a syntax error
    (DEPLOYER, "NOT_THE_LIST = ('a',)\n"),                                     # no assignment at all
    (DEPLOYER, "GUARD_SOURCES = ('a',)\nGUARD_SOURCES = ('b',)\n"),            # two answers
    (DEPLOYER, "GUARD_SOURCES = ()\n"),                                        # nothing
    (DEPLOYER, "GUARD_SOURCES = ('../outside',)\n"),                           # not a relative path
    (GENERATOR, "OWNED = ('.claude/hooks/',)\nOWNED += EXTRA\n"),
    (GENERATOR, "OWNED = ('.claude/hooks/',)\ndef f():\n    global OWNED\n    OWNED = ()\n"),   # changed elsewhere
    (GENERATOR, "OWNED = ('.claude/hooks/',)\nif True:\n    OWNED = ('CLAUDE.md',)\n"),             # in a branch
    (GENERATOR, "OWNED = ('CLAUDE.md',)\n"),                                   # parses, but names no guard entry
    (GENERATOR, None),                                                         # the file is missing
])
def test_an_unparseable_source_refuses_the_policy_and_stage(world, monkeypatch, which, text):
    root, serve = world
    if text is None:
        (root / which).unlink()
    else:
        (root / which).write_text(text, encoding="utf-8")
    with pytest.raises(guarded.UpdateRefused, match=Path(which).name):
        x4u.policy(root)
    fake = serve()
    before = _hashes(root)
    code, result = _stage(root, monkeypatch)
    assert code == 2 and Path(which).name in result["error"], result
    assert fake.calls == [] and _hashes(root) == before


def test_a_directory_is_everything_under_it_and_never_a_bare_glob(tmp_path):
    """6c review F5: fnmatch's `*` crosses `/`, so `x/*` is the whole tree; a bare `x` matches no file and only printed
    a "matched no path" warning in every review."""
    globs = x4u.policy(_install(tmp_path)).guard_paths
    assert ".claude/hooks/*" in globs and ".claude/hooks" not in globs
    assert ".claude/settings.json" in globs and ".claude/settings.json/*" not in globs
    for glob in x4u.policy(REPO).guard_paths:
        assert not (REPO / glob).is_dir(), glob


def test_the_render_configuration_follows_the_installed_files(tmp_path, monkeypatch):
    monkeypatch.setenv("MODDING_HARNESS_UPDATES_DIR", str(tmp_path / "updates"))
    root = _install(tmp_path)
    _write(root, {".agents/skills/a/SKILL.md": b"run $env:X4_TOOLKIT/tools/x\n",
                  ".opencode/skills/a/SKILL.md": b"run $X4_TOOLKIT/tools/x\n"})
    config = json.loads(Path(x4u.policy(root).render_config(root)).read_text(encoding="utf-8"))
    assert config == {"v": 1, "agent_shell": "powershell", "opencode_shell": "bash"}
    assert not Path(x4u.policy(root).render_config(root)).resolve().is_relative_to(root.resolve())
    _write(root, {".agents/skills/b/SKILL.md": b"run $X4_TOOLKIT/tools/x\n"})            # both spellings: ambiguous
    with pytest.raises(ValueError, match="agents"):
        x4u.policy(root).render_config(root)


# ============================================================================================ fetch and stage ==

def test_the_happy_path_stage_then_apply_keeps_the_users_files(world, monkeypatch):
    root, serve = world
    serve()
    paths_env, notes = (root / "x4-paths.env").read_bytes(), (root / "X4-NOTES.md").read_bytes()
    code, staged = _stage(root, monkeypatch)
    assert code == 0, staged
    assert staged["requires_guard_accept"] is False and staged["application_authorized"] is False
    assert staged["from_version"] == OLD and staged["to_version"] == NEW
    assert "No guard, hook, settings or policy change." in staged["review"]
    code, applied = _apply(root, monkeypatch, staged, "--quiescent")
    assert code == 0 and applied["ok"] is True, applied
    assert (root / "README.md").read_bytes() == b"readme 4.3.2\n"
    assert x4u.installed_version(root) == NEW
    assert (root / "x4-paths.env").read_bytes() == paths_env and (root / "X4-NOTES.md").read_bytes() == notes
    assert not (root / guarded.IN_FLIGHT).exists() and not (root / guarded.FAILED_MARKER).exists()


def test_stage_authorizes_nothing_and_changes_no_installed_byte(world, monkeypatch):
    root, serve = world
    serve(_new_files(**{".claude/hooks/guard.sh": b"echo guard 4.3.2\n"}))
    before = _hashes(root, skip_state=False)
    code, staged = _stage(root, monkeypatch)
    assert code == 0 and staged["application_authorized"] is False
    assert _hashes(root, skip_state=False) == before
    assert not Path(staged["bundle"]).resolve().is_relative_to(root.resolve())


def _unpacked(tmp_path):
    return list((tmp_path / "updates").rglob("unpacked"))


def test_a_bad_digest_is_refused_and_nothing_is_unpacked(world, monkeypatch, tmp_path):
    root, serve = world
    serve(digest="sha256:" + "0" * 64)
    before = _hashes(root)
    code, result = _stage(root, monkeypatch)
    assert code == 2 and "SHA-256 does not match" in result["error"], result
    assert _unpacked(tmp_path) == [] and _hashes(root) == before
    assert not list((tmp_path / "updates").rglob("*.zip")), "the refused download was kept"


def test_a_build_info_commit_mismatch_is_refused(world, monkeypatch, tmp_path):
    root, serve = world
    serve(zip_commit="ee" * 20)
    code, result = _stage(root, monkeypatch)
    assert code == 2 and "different commit" in result["error"], result
    assert _unpacked(tmp_path) == []


def test_a_build_info_version_mismatch_is_refused(world, monkeypatch, tmp_path):
    root, serve = world
    serve(zip_version="4.3.3")
    code, result = _stage(root, monkeypatch)
    assert code == 2 and "names version 4.3.3" in result["error"], result
    assert _unpacked(tmp_path) == []


def _link_member():
    info = zipfile.ZipInfo("X4AI/a-link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


@pytest.mark.parametrize("member,why", [
    ("X4AI/../escape.txt", "unsafe entry name"),
    ("/abs.txt", "unsafe entry name"),
    ("C:/drive.txt", "unsafe entry name"),
    (_link_member(), "contains a link"),
    ("Other/second-root.txt", "exactly one folder"),
])
def test_an_unsafe_zip_member_is_refused(world, monkeypatch, tmp_path, member, why):
    root, serve = world
    serve(new_zip=_zip(_new_files(), NEW, COMMIT, extra=[(member, b"x")]))
    before = _hashes(root)
    code, result = _stage(root, monkeypatch)
    assert code == 2 and why in result["error"], result
    assert _unpacked(tmp_path) == [] and _hashes(root) == before
    assert not (tmp_path / "escape.txt").exists() and not (tmp_path / "updates" / "escape.txt").exists()


@pytest.mark.parametrize("url", [
    "https://evil.example/WingedGuardian/x4-ai-toolkit/releases/download/v4.3.2/x.zip",
    "https://github.com/someone/else/releases/download/v4.3.2/X4.Foundations.AI.Assistant.Toolkit-v4.3.2.zip",
    "https://github.com/WingedGuardian/x4-ai-toolkit/releases/download/v4.3.2/../../../evil/x.zip",
])
def test_a_foreign_asset_url_is_refused_before_any_download(world, monkeypatch, url):
    root, serve = world
    fake = serve(url=url)
    code, result = _stage(root, monkeypatch)
    assert code == 2 and "not this repository's release" in result["error"], result
    assert url not in fake.calls and fake.calls, "control: GitHub was asked, the asset was not downloaded"


def test_a_guard_change_needs_the_separate_go(world, monkeypatch):
    root, serve = world
    serve(_new_files(**{".claude/hooks/guard.sh": b"echo guard 4.3.2\n"}))
    code, staged = _stage(root, monkeypatch)
    assert code == 0 and staged["requires_guard_accept"] is True
    assert "GUARD CHANGES (1)" in staged["review"] and ".claude/hooks/guard.sh" in staged["review"]
    before = _hashes(root)
    code, result = _apply(root, monkeypatch, staged, "--quiescent")
    assert code == 2 and "--accept-guard-changes" in result["error"], result
    assert _hashes(root) == before
    code, applied = _apply(root, monkeypatch, staged, "--quiescent", "--accept-guard-changes")
    assert code == 0 and applied["ok"] is True, applied                                   # twin: with the go
    assert (root / ".claude/hooks/guard.sh").read_bytes() == b"echo guard 4.3.2\n"


def test_refused_apply_never_runs_setup(world, monkeypatch):
    root, serve = world
    serve(_new_files(**{".claude/hooks/guard.sh": b"echo guard 4.3.2\n"}))
    setup = root / "setup.sh"
    setup.write_bytes(setup.read_bytes() + b"\nprintf setup-ran > setup-side-effect.txt\n")
    code, staged = _stage(root, monkeypatch)
    assert code == 0, staged
    before = _hashes(root)

    code, refused = _apply(root, monkeypatch, staged, "--quiescent")

    assert code == 2 and "--accept-guard-changes" in refused["error"], refused
    assert _hashes(root) == before


def test_stage_is_refused_when_the_installed_canary_fails(world, monkeypatch):
    root, serve = world
    fake = serve()
    (root / ".fail-canary").write_text("", encoding="utf-8")
    code, result = _stage(root, monkeypatch)
    assert code == 2 and "fix x4doctor first" in result["error"], result
    assert "guards.selftest.claude" in result["error"], "the FAIL rows are named"
    assert fake.calls == [], "nothing was fetched for an unhealthy install"
    (root / ".fail-canary").unlink()
    assert _stage(root, monkeypatch)[0] == 0, "twin: the healthy install stages"


def test_a_checkout_is_refused_before_any_canary_or_request(world, monkeypatch):
    root, serve = world
    fake = serve()
    (root / ".git").mkdir()
    (root / ".fail-canary").write_text("", encoding="utf-8")
    code, result = _stage(root, monkeypatch)
    assert code == 2 and "no installed release" in result["error"], result
    assert fake.calls == []


# ======================================================================================================= apply ==

def test_apply_without_quiescent_is_refused(world, monkeypatch):
    root, serve = world
    serve()
    code, staged = _stage(root, monkeypatch)
    assert code == 0
    before = _hashes(root)
    code, result = _apply(root, monkeypatch, staged)
    assert code == 2 and _hashes(root) == before
    with pytest.raises(guarded.UpdateRefused, match="quiescent"):
        guarded.apply_update(root, x4u.policy(root), staged["bundle"], staged["review_sha256"], {}, quiescent=False)
    assert _hashes(root) == before


def test_a_user_modified_file_with_no_choice_is_refused_and_kept(world, monkeypatch):
    root, serve = world
    serve()
    (root / "README.md").write_bytes(b"my own readme\n")
    code, staged = _stage(root, monkeypatch)
    assert code == 0 and "README.md  preserve_collision" in staged["review"], staged["review"]
    code, result = _apply(root, monkeypatch, staged, "--quiescent")
    assert code == 2 and "README.md" in result["error"], result
    assert (root / "README.md").read_bytes() == b"my own readme\n"
    code, applied = _apply(root, monkeypatch, staged, "--quiescent", choices={"README.md": "keep"})
    assert code == 0 and applied["ok"] is True, applied
    assert (root / "README.md").read_bytes() == b"my own readme\n"
    assert x4u.installed_version(root) == NEW, "control: the rest of the update was applied"


def test_a_release_shipping_the_users_path_config_cannot_replace_it(world, monkeypatch):
    """Two layers: install.sh's KEEP_LOCAL drops it from the update (test below); and should a release's installer
    ever list it as an item, the notes protection still refuses `candidate`."""
    root, serve = world
    sh = FAKE_INSTALL_SH.replace(b"x4-paths.env.example BUILD-INFO", b"x4-paths.env.example x4-paths.env BUILD-INFO")
    sh = sh.replace(b'X4_KEEP_LOCAL="x4-paths.env ', b'X4_KEEP_LOCAL="')
    serve(_new_files(**{"x4-paths.env": b"X4_GAME=\n", "install.sh": sh}))
    code, staged = _stage(root, monkeypatch)
    assert code == 0
    code, result = _apply(root, monkeypatch, staged, "--quiescent", choices={"x4-paths.env": "candidate"})
    assert code == 2 and "must be retained" in result["error"], result
    assert (root / "x4-paths.env").read_bytes() == b"X4_GAME=C:/Games/X4\n"


def test_apply_rolls_back_when_candidate_setup_fails(world, monkeypatch):
    root, serve = world
    serve(_new_files(**{"setup.sh": SETUP_STUB + b"exit 6\n"}))
    code, staged = _stage(root, monkeypatch)
    assert code == 0
    before = _hashes(root)
    code, result = _apply(root, monkeypatch, staged, "--quiescent", "--accept-guard-changes")
    assert code == 1 and result["rolled_back"] and "FAILED at setup" in result["report"], result
    assert _hashes(root) == before and not (root / guarded.FAILED_MARKER).exists()


def test_apply_refuses_when_the_installed_canary_fails_since_stage(world, monkeypatch):
    root, serve = world
    serve()
    code, staged = _stage(root, monkeypatch)
    (root / ".fail-canary").write_text("", encoding="utf-8")
    before = _hashes(root)
    code, result = _apply(root, monkeypatch, staged, "--quiescent")
    assert code == 2 and "fix x4doctor first" in result["error"], result
    assert _hashes(root) == before and not (root / guarded.FAILED_MARKER).exists()


def test_setup_never_inherits_the_sessions_project_folder(world, monkeypatch, tmp_path):
    """setup.sh roots itself at $CLAUDE_PROJECT_DIR when set; a session's project folder is not the install."""
    root, serve = world
    serve()
    code, staged = _stage(root, monkeypatch)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "some-game-folder"))
    code, applied = _apply(root, monkeypatch, staged, "--quiescent")
    assert code == 0, applied                       # the stub exits 7 when the variable leaks


# ============================================================================================ failure handling ==

def test_a_failing_new_canary_rolls_back_byte_exact_exit_one(world, monkeypatch):
    root, serve = world
    serve(_new_files(**{"scripts/x4doctor.py": _doctor_stub(NEW, fail=True),
                        ".claude/hooks/guard.sh": b"echo guard 4.3.2\n", "docs/new-file.md": b"new\n"}))
    before = _hashes(root)
    code, staged = _stage(root, monkeypatch)
    assert code == 0 and staged["requires_guard_accept"] is True
    code, applied = _apply(root, monkeypatch, staged, "--quiescent", "--accept-guard-changes")
    assert code == 1 and applied["ok"] is False and applied["rolled_back"] is True, applied
    assert "FAILED at canary" in applied["report"] and "rolled back" in applied["report"]
    assert not applied["report"].startswith("Codex users:")
    assert _hashes(root) == before, "the rollback is not byte-exact"
    assert not (root / guarded.FAILED_MARKER).exists() and not (root / guarded.IN_FLIGHT).exists()


def _staged_direct(root, monkeypatch):
    code, staged = _stage(root, monkeypatch)
    assert code == 0, staged
    return staged


def _review_policy(root, staged):
    source = Path(staged["bundle"]).parent / "unpacked"
    return x4u.review_policy(root, source, x4u.selection(root, source)[0])


def _apply_direct(root, staged, run, **kw):
    pol = _review_policy(root, staged)
    return guarded.apply_update(root, pol, staged["bundle"], staged["review_sha256"], kw.pop("choices", {}),
                                accept_guard_changes=True, quiescent=True, notes_files=x4u.NOTES_FILES, run=run,
                                new_policy=pol, candidate_validator=render.validate)


def _ok(argv, cwd, timeout):
    return subprocess.CompletedProcess(argv, 0, b"", b"")


def test_a_failed_rollback_writes_the_marker_and_stage_refuses_until_it_is_cleared(world, monkeypatch):
    root, serve = world
    serve()
    staged = _staged_direct(root, monkeypatch)
    calls = []

    def run(argv, cwd, timeout):
        calls.append(argv)
        if "--update-canary" in argv:
            (Path(cwd) / "README.md").write_bytes(b"changed behind the updater's back\n")     # rollback must refuse
            return subprocess.CompletedProcess(argv, 1, b"  FAIL x", b"")
        return _ok(argv, cwd, timeout)
    applied = _apply_direct(root, staged, run)
    assert applied.ok is False and applied.rolled_back is False, applied.report
    assert guarded.exit_code(applied) == 1
    marker = json.loads((root / guarded.FAILED_MARKER).read_text(encoding="utf-8"))
    assert marker["failed_step"] == "rollback" and "FAIL CLOSED" in applied.report
    assert "TOOLKIT UPDATE NEEDS THE USER" in sess.note(root, environ={})
    fake = serve(version="4.3.3", new_zip=_zip(_base_files("4.3.3"), "4.3.3", COMMIT))
    code, result = _stage(root, monkeypatch)
    assert code == 2 and "update-failed.json" in result["error"] and fake.calls == [], result
    code, cleared = _run_harness(["update-clear-failed", "--installed-root", str(root)], monkeypatch)
    assert code == 0 and cleared["cleared"] == [guarded.FAILED_MARKER], cleared
    assert list((root / ".modding-agent-harness").glob("update-failed.cleared-*.json")), "kept as evidence"
    code, staged = _stage(root, monkeypatch)
    assert code == 0 and staged["to_version"] == "4.3.3", staged


def test_an_interrupted_apply_leaves_the_in_flight_marker_and_the_note_says_so(world, monkeypatch):
    """Finding 3: the core's `handle` passes no `run=`, so this calls `apply_update` directly."""
    root, serve = world
    serve()
    staged = _staged_direct(root, monkeypatch)

    def run(argv, cwd, timeout):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        _apply_direct(root, staged, run)
    record = json.loads((root / guarded.IN_FLIGHT).read_text(encoding="utf-8"))
    assert record["interrupted"].startswith("KeyboardInterrupt") and record["operation"]
    note = sess.note(root, environ={})
    assert "TOOLKIT UPDATE NEEDS THE USER" in note and "update-in-flight.json" in note
    code, result = _stage(root, monkeypatch)
    assert code == 2 and "update-in-flight.json" in result["error"], result
    # the recorded process is this one, which is alive: clearing needs --force
    code, result = _run_harness(["update-clear-failed", "--installed-root", str(root)], monkeypatch)
    assert code == 2 and "still running" in result["error"], result
    code, cleared = _run_harness(["update-clear-failed", "--installed-root", str(root), "--force"], monkeypatch)
    assert code == 0 and cleared["cleared"] == [guarded.IN_FLIGHT]
    assert sess.note(root, environ={}) == "" or "NEEDS THE USER" not in sess.note(root, environ={})


def test_clear_failed_with_nothing_to_clear_is_exit_zero(world, monkeypatch):
    root, _ = world
    code, cleared = _run_harness(["update-clear-failed", "--installed-root", str(root)], monkeypatch)
    assert code == 0 and cleared == {"cleared": []}


def test_clear_failed_works_when_the_policy_cannot_be_built(world, monkeypatch):
    """The markers must be clearable even when the guard sources are broken (that may be what the user fixed)."""
    root, _ = world
    (root / ".modding-agent-harness").mkdir()
    (root / guarded.FAILED_MARKER).write_text("{}", encoding="utf-8")
    (root / DEPLOYER).write_text("broken(\n", encoding="utf-8")
    code, cleared = _run_harness(["update-clear-failed", "--installed-root", str(root)], monkeypatch)
    assert code == 0 and cleared["cleared"] == [guarded.FAILED_MARKER]


# ======================================================================================== the harness and canary ==

def test_the_skill_describes_the_staged_flow_as_present():
    require_repo("agent/skills/x4-toolkit-update/SKILL.md", why="reads the skill source")
    skill = (REPO / "agent" / "skills" / "x4-toolkit-update" / "SKILL.md").read_text(encoding="utf-8")
    assert "arrive with" not in skill and "arrives with" not in skill, "the stage/apply steps are still marked as future"
    for needle in ("update-stage", "update-apply", "update-clear-failed", "verbatim", "--accept-guard-changes",
                   "--choices", "--quiescent", "close their other agent sessions", "fix x4doctor first",
                   "update-failed.json", "update-in-flight.json", "`2` when it failed for any reason"):
        assert needle in skill, needle


def test_the_harness_lists_the_three_commands():
    done = subprocess.run([sys.executable, "-B", str(REPO / "tools" / "agent-harness.py"), "--help"],
                          capture_output=True, text=True, timeout=60)
    for name in ("update-stage", "update-apply", "update-clear-failed"):
        assert name in done.stdout, done.stdout


def test_an_unexpected_error_in_a_guarded_command_is_exit_one_with_the_reason(world, monkeypatch):
    root, serve = world
    serve()
    staged = _staged_direct(root, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("something nobody expected")
    monkeypatch.setattr(guarded, "apply_update", boom)
    code, result = _apply(root, monkeypatch, staged, "--quiescent")
    assert code == 1 and "something nobody expected" in result["error"], result


DOCTOR = REPO / "scripts" / "x4doctor.py"
doc = _load("x4doctor_canary_under_test", DOCTOR)


def _canary(monkeypatch, tmp_path, rows, env_budget=None):
    (tmp_path / ".claude").mkdir(exist_ok=True)
    (tmp_path / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    seen = {}
    if env_budget is None:
        monkeypatch.delenv("X4_GUARD_TIMEOUT_S", raising=False)
    else:
        monkeypatch.setenv("X4_GUARD_TIMEOUT_S", env_budget)

    def collect(ctx, only=None):
        seen["budget"] = ctx.env.get("X4_GUARD_TIMEOUT_S")
        seen["root"] = ctx.root
        return list(rows)
    monkeypatch.setattr(doc, "collect", collect)
    out = io.StringIO()
    monkeypatch.setattr(doc.sys, "stdout", out)
    monkeypatch.chdir(tmp_path)
    code = doc.main(["--update-canary"])
    return code, out.getvalue(), seen


def _row(cid, status, target="claude"):
    return doc.Check(cid, target, status, "detail of " + cid)


def test_the_canary_passes_on_todo_and_unknown_and_sets_the_guard_budget(monkeypatch, tmp_path):
    todo = _row("roots.reference", doc.TODO, "all")
    assert todo.id not in doc.UPDATE_CANARY_SKIPPED, "control: the TODO row is one the canary judges"
    code, out, seen = _canary(monkeypatch, tmp_path, [_row("toolchain.bash", doc.OK), _row("x4lock", doc.UNKNOWN), todo])
    assert code == 0, out
    assert seen["budget"] == "90" and Path(seen["root"]).resolve() == tmp_path.resolve()
    assert out.splitlines()[0].startswith("x4doctor --update-canary: PASS")


def test_the_canary_fails_on_a_fail_row_and_names_it(monkeypatch, tmp_path):
    code, out, _ = _canary(monkeypatch, tmp_path, [_row("toolchain.bash", doc.OK),
                                                   _row("guards.selftest.claude", doc.FAIL)])
    assert code == 1 and out.splitlines()[0].startswith("x4doctor --update-canary: FAIL")
    assert any(line.split()[:1] == ["FAIL"] and "guards.selftest.claude" in line for line in out.splitlines()), out


def test_the_canary_keeps_an_explicit_guard_budget(monkeypatch, tmp_path):
    _, _, seen = _canary(monkeypatch, tmp_path, [_row("toolchain.bash", doc.OK)], env_budget="40")
    assert seen["budget"] == "40"


def test_the_canary_ignores_the_rows_it_excludes_and_says_so(monkeypatch, tmp_path):
    for cid in doc.UPDATE_CANARY_SKIPPED:
        code, out, _ = _canary(monkeypatch, tmp_path, [_row("toolchain.bash", doc.OK), _row(cid, doc.FAIL)])
        assert code == 0 and cid in out and "not part of the update canary" in out, (cid, out)
    code, _, _ = _canary(monkeypatch, tmp_path, [_row("toolchain.bash", doc.OK), _row("codex.hooks", doc.FAIL, "codex")])
    assert code == 1, "twin: a kept codex row still fails"


def test_the_canary_with_nothing_answered_is_not_a_pass(monkeypatch, tmp_path):
    code, _, _ = _canary(monkeypatch, tmp_path, [_row("x4lock", doc.UNKNOWN)])
    assert code == 2
    shutil.rmtree(tmp_path / ".claude")
    out = io.StringIO()
    monkeypatch.setattr(doc.sys, "stdout", out)
    assert doc.main(["--update-canary"]) == 2, "no agent target in the folder it runs in"


def test_the_canary_keeps_the_checks_the_brief_names():
    kept = {g.__name__ for g in doc.GROUPS}
    assert {"check_toolchain", "check_roots", "check_guards", "check_parity", "check_claude", "check_codex",
            "check_common", "check_opencode"} <= kept
    assert set(doc.UPDATE_CANARY_SKIPPED) == {"layer2.reference", "codex.config", "codex.trusted", "codex.reviewed",
                                              "opencode.userconfig", "opencode.cli"}


# ======================================================== 6c review: stage only what this install holds (F1, F2, F4) ==

def test_the_install_selection_is_the_installers_own_lists():
    """Derived from install.sh, never listed here; test_installers_agree ties install.sh to install.ps1."""
    require_repo("install.sh", "install.ps1", why="parses both installers")
    agree = _load("test_installers_agree_for_6c", Path(__file__).with_name("test_installers_agree.py"))
    items, agents, skip = x4u.install_selection(REPO)
    assert sorted(items) == sorted(agree.sh_items()) == sorted(agree.ps1_items())
    assert {k: sorted(v) for k, v in agents.items()} == {k: sorted(v) for k, v in agree.sh_agent_items().items()}
    assert {k: sorted(v) for k, v in agents.items()} == {k: sorted(v) for k, v in agree.ps1_agent_items().items()}
    for name in ("x4-paths.env", ".codex/hooks.json", ".opencode/opencode.jsonc", "tools/x4validate/.venv"):
        assert name in skip, name
    for neutral in ("agent", "docs", ".github"):
        assert neutral not in items + [x for v in agents.values() for x in v], neutral


@pytest.mark.parametrize("text,why", [(b'X4_COPY_ITEMS="a"\n', "X4_AGENT_NAMES"),
                                      (FAKE_INSTALL_SH + b'X4_COPY_ITEMS="b"\n', "2 times"),
                                      (FAKE_INSTALL_SH.replace(b'"tools ', b'"../tools '), "unsafe"),
                                      (None, "no readable install.sh")])
def test_an_unreadable_installer_list_refuses_stage(world, monkeypatch, text, why):
    root, serve = world
    files = _new_files()
    if text is None:
        files.pop("install.sh")
    else:
        files["install.sh"] = text
    serve(files)
    before = _hashes(root)
    code, result = _stage(root, monkeypatch)
    assert code == 2 and why in result["error"], result
    assert _hashes(root) == before


EVERY_AGENT = {".codex/hooks/c.sh": b"echo c\n", ".agents/skills/s/SKILL.md": b"skill\n",
               ".opencode/plugins/x4guard.js": b"// plugin\n", "AGENTS.md": b"agents\n", "agent/guards/g.py": b"g\n",
               "docs/d.md": b"d\n", ".github/workflows/w.yml": b"w\n"}


def _rows_part(review):
    """The review below the X4 note (the note names no path)."""
    return review.split("\n\n", 1)[1]


def test_a_claude_only_install_receives_only_its_own_files(world, monkeypatch):
    root, serve = world
    serve(_new_files(**EVERY_AGENT))
    code, staged = _stage(root, monkeypatch)
    assert code == 0, staged
    assert staged["agents"] == ["claude"] and staged["not_installed"] == len(EVERY_AGENT)
    assert staged["requires_guard_accept"] is False, "files this install never receives asked for the guard go"
    for name in EVERY_AGENT:
        assert name not in _rows_part(staged["review"]), name
    code, applied = _apply(root, monkeypatch, staged, "--quiescent")
    assert code == 0 and applied["ok"] is True, applied
    for name in EVERY_AGENT:
        assert not (root / name).exists(), name
    assert (root / "README.md").read_bytes() == b"readme 4.3.2\n"


def test_an_install_with_opencode_receives_the_opencode_files(world, monkeypatch):
    """The twin: the same release to an install that already has the OpenCode plugin."""
    root, serve = world
    _write(root, {".opencode/plugins/x4guard.js": b"// plugin\n"})
    serve(_new_files(**dict(EVERY_AGENT, **{".opencode/plugins/x4guard.js": b"// plugin 4.3.2\n"})))
    code, staged = _stage(root, monkeypatch)
    assert code == 0 and staged["agents"] == ["claude", "opencode"], staged
    rows = _rows_part(staged["review"])
    assert staged["requires_guard_accept"] is True and ".opencode/plugins/x4guard.js" in rows
    assert "AGENTS.md" in rows and "agent/guards/g.py" not in rows and ".codex/hooks/c.sh" not in rows


def test_the_core_applies_an_x4_staged_bundle(world, monkeypatch):
    """apply_update reads only the bundle and meta.json; the X4 stage must write what it checks."""
    root, serve = world
    serve(_new_files(**EVERY_AGENT))
    staged = _staged_direct(root, monkeypatch)
    meta = json.loads((Path(staged["bundle"]).parent / "meta.json").read_text(encoding="utf-8"))
    assert meta == {"requires_guard_accept": False, "review_sha256": staged["review_sha256"],
                    "from_version": OLD, "to_version": NEW}
    applied = _apply_direct(root, staged, guarded.run)
    assert applied.ok is True, applied.report
    assert not (root / "docs").exists() and (root / "README.md").read_bytes() == b"readme 4.3.2\n"


@pytest.mark.parametrize("custom_matcher", [False, True])
def test_new_codex_definitions_replace_verified_prior_groups_and_keep_user_hooks(world, monkeypatch, custom_matcher):
    root, serve = world
    template = (REPO / render.TEMPLATE).read_text(encoding="utf-8")
    latest = json.loads(template)
    for groups in latest["hooks"].values():
        groups[0]["hooks"][0]["timeout"] += 1
    newer_template = json.dumps(latest)
    previous_files = {render.TEMPLATE: template.encode(), ".codex/hooks/codex-entry.sh": b"# fixture\n"}
    old = _zip(dict(_base_files(), **previous_files), OLD, OLD_COMMIT)
    installed = json.loads(render.render_codex(template, root))
    custom = {"hooks": [{"type": "command", "command": "echo local color policy"}]}
    installed["hooks"]["SessionStart"].append(custom)
    if custom_matcher:
        installed["hooks"]["PostToolUse"][0]["matcher"] = "^apply_patch$"
    _write(root, dict(previous_files, **{".codex/hooks.json": json.dumps(installed).encode()}))
    serve(_new_files(**dict(previous_files, **{render.TEMPLATE: newer_template.encode()})), old_zip=old)

    code, staged = _stage(root, monkeypatch)
    if custom_matcher:
        assert code == 2 and "duplicate PostToolUse commands" in staged["error"], staged
        assert json.loads((root / ".codex/hooks.json").read_bytes()) == installed
        return
    assert code == 0 and staged["requires_guard_accept"], staged
    code, result = _apply(root, monkeypatch, staged, "--quiescent", "--accept-guard-changes",
                          choices={".codex/hooks.json": "merged_settings"})
    assert code == 0 and result["ok"], result
    expected = json.loads(render.render_codex(newer_template, root))
    expected["hooks"]["SessionStart"].insert(0, custom)
    assert json.loads((root / ".codex/hooks.json").read_bytes()) == expected


def test_a_hook_folder_new_in_the_release_is_a_guard_change(world, monkeypatch):
    """6c review F4: the installed deployer does not know tools/newguard; the release's does. Its files must be shown as
    guard diffs, and the apply must judge them the same way (meta.json agrees)."""
    root, serve = world
    deployer = (REPO / DEPLOYER).read_text(encoding="utf-8").replace(
        'GUARD_SOURCES = (', 'GUARD_SOURCES = ("tools/newguard", ', 1).encode("utf-8")
    serve(_new_files(**{DEPLOYER: deployer, "tools/newguard/g.py": b"print('guard')\n"}))
    code, staged = _stage(root, monkeypatch)
    assert code == 0 and staged["requires_guard_accept"] is True, staged
    guard_part = staged["review"].split("GUARD CHANGES", 1)[1].split("Other changes", 1)[0]
    assert "tools/newguard/g.py" in guard_part
    assert not _is_guard(x4u.policy(root), "tools/newguard/g.py"), "control: the installed policy alone misses it"
    code, result = _apply(root, monkeypatch, staged, "--quiescent")
    assert code == 2 and "--accept-guard-changes" in result["error"], result
    code, applied = _apply(root, monkeypatch, staged, "--quiescent", "--accept-guard-changes")
    assert code == 0 and applied["ok"] is True, applied


def test_an_unreadable_release_deployer_widens_nothing_and_refuses_nothing(world, monkeypatch):
    root, serve = world
    serve(_new_files(**{DEPLOYER: b"GUARD_SOURCES = compute()\n"}))
    code, staged = _stage(root, monkeypatch)
    assert code == 0 and staged["requires_guard_accept"] is True, staged        # the deployer is a policy file
    assert DEPLOYER in staged["review"]


def test_the_apply_report_starts_with_the_codex_re_approval_line(world, monkeypatch):
    root, serve = world
    serve()
    code, staged = _stage(root, monkeypatch)
    code, applied = _apply(root, monkeypatch, staged, "--quiescent")
    assert code == 0 and applied["report"].splitlines()[0] == x4u.CODEX_LINE
    assert "/hooks" in x4u.CODEX_LINE and "skips" in x4u.CODEX_LINE


# ============================================== the REAL canary on a post-update tree built from a release-shaped zip ==

def _release_tree():
    """What the release zip holds: every shipped file (git ls-files minus export-ignore) with its working-tree bytes, so
    the code under test is what is installed. setup.sh is a stub: the real one runs `uv sync` (network, minutes)."""
    files = {name: (REPO / name).read_bytes() for name in _shipped(["."]) if (REPO / name).is_file()}
    files["setup.sh"] = SETUP_STUB
    return files


def _real_install(tmp_path, agents, files):
    """A toolkit installed the way install.sh does it: its lists, the token and Codex rendering, the deny rules."""
    items, agent_items, skip = x4u.install_selection(REPO)
    wanted = items + [item for agent in agents for item in agent_items[agent]]
    chosen = x4u.select_files(dict.fromkeys(files, "0" * 64), wanted, skip)
    root = tmp_path / ("install-" + "-".join(agents))
    _write(root, {name: files[name] for name in chosen})
    (root / "BUILD-INFO").write_bytes(_build_info(OLD, OLD_COMMIT))
    shell = "powershell" if os.name == "nt" else "bash"
    for name, data in render.prepare(root, root, list(chosen), {"v": 1, "agent_shell": shell,
                                                                 "opencode_shell": shell}).items():
        (root / name).write_bytes(data)
    (tmp_path / "game").mkdir(exist_ok=True)
    (tmp_path / "reference").mkdir(exist_ok=True)
    (root / "x4-paths.env").write_text("X4_GAME={}\nX4_REFERENCE={}\n".format(
        (tmp_path / "game").as_posix(), (tmp_path / "reference").as_posix()), encoding="utf-8")
    if "opencode" in agents:
        done = subprocess.run([sys.executable, "-B", str(root / ".opencode/hooks/opencode_config.py"), "write", "--root",
                               str(root)], env=dict(os.environ, X4_TOOLKIT=str(root)), capture_output=True, text=True,
                              timeout=120)
        assert done.returncode == 0, done.stderr
    return root


@pytest.fixture
def release_world(tmp_path, monkeypatch):
    monkeypatch.setenv("MODDING_HARNESS_UPDATES_DIR", str(tmp_path / "updates"))
    for key in ("CLAUDE_PROJECT_DIR", "X4_GAME", "X4_REFERENCE", "X4_PROFILE", "X4_MODS", "X4_CONFIG", "X4_GUARD",
                "X4_UPDATE_CHECK", "X4_HOUSEKEEP_SWEEP", "MODDING_HARNESS_UPDATE_CHECK"):
        monkeypatch.delenv(key, raising=False)
    files = _release_tree()
    new = dict(files, **{"README.md": files["README.md"] + b"\n4.3.2\n"})
    fake = FakeGitHub(_zip(new, NEW, COMMIT), old_zip=_zip(files, OLD, OLD_COMMIT))
    original = release.fetch
    monkeypatch.setattr(release, "fetch", lambda r, p, http=None, environ=None: original(r, p, http=fake,
                                                                                          environ=environ))
    return tmp_path, files


def _real_canary(root):
    done = guarded.run((sys.executable, "-I", "scripts/x4doctor.py", "--update-canary"), root, 600)
    return done.returncode, done.stdout.decode("utf-8", "replace")


def test_the_update_canary_writes_no_bytecode(tmp_path, monkeypatch):
    root = tmp_path / "install"
    root.mkdir()
    for rel in ("scripts/x4doctor.py", "scripts/gitbash.py", ".claude/settings.json",
                ".claude/hooks/_x4-env.sh", ".claude/hooks/x4guard.py"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((REPO / rel).read_bytes())
    monkeypatch.setenv("X4_TOOLKIT", str(root))
    before = _hashes(root)

    _, report = _real_canary(root)

    assert "checked:" in report
    assert _hashes(root) == before


@pytest.mark.parametrize("agents", [("claude",), ("claude", "codex", "generic", "opencode")])
def test_the_real_canary_passes_after_a_real_shaped_update(release_world, monkeypatch, agents):
    """6c review F1: the key test. A release-shaped zip (X4AI/ + BUILD-INFO) of the shipped files, installed the way
    install.sh installs it, staged and applied through the harness, then the REAL x4doctor --update-canary."""
    require_repo(".git", "install.sh", why="builds the release tree from git's shipped set")
    tmp_path, files = release_world
    root = _real_install(tmp_path, list(agents), files)
    monkeypatch.setenv("X4_TOOLKIT", str(root))
    code, out = _real_canary(root)
    assert code == 0, "control: the fresh install is healthy\n" + out
    import time
    started = time.monotonic()
    code, staged = _stage(root, monkeypatch)
    print("STAGE-SECONDS %s %.1f" % ("-".join(agents), time.monotonic() - started), file=sys.stderr)
    assert code == 0, staged
    accept = ["--accept-guard-changes"] if staged["requires_guard_accept"] else []
    review = json.loads((Path(staged["bundle"]) / "complete.json").read_text(encoding="utf-8"))
    rows = {row["path"] for row in review["plan"]["files"]}
    choices = {name: "merged_settings" for name in review.get("merged_settings_hashes", {}) if name in rows}
    assert ".claude/settings.json" in choices, "the core asks a settings choice of every install"
    code, applied = _apply(root, monkeypatch, staged, "--quiescent", *accept, choices=choices)
    assert code == 0 and applied["ok"] is True, applied.get("report", applied)
    assert x4u.installed_version(root) == NEW
    code, out = _real_canary(root)
    assert code == 0, out
    if agents == ("claude",):
        for name in (".opencode", ".codex", ".agents", "AGENTS.md", "agent", "docs", ".github"):
            assert not (root / name).exists(), name


#: The setup step 70a902ca shipped (with F3's UV_FROZEN): setup.sh only, no OpenCode re-render.
OLD_SETUP = 'unset CLAUDE_PROJECT_DIR; export UV_FROZEN=1; exec "$BASH" ./setup.sh'


def test_twin_the_unfiltered_core_stage_fails_the_real_canary_on_a_claude_only_install(release_world, monkeypatch):
    """The core's own stage_update writes the whole zip: the OpenCode plugin turns x4doctor's OpenCode target on with no
    deny rules, and the canary FAILs, so the update rolls back. This is F1 as 70a902ca had it."""
    require_repo(".git", "install.sh", why="builds the release tree from git's shipped set")
    import dataclasses
    tmp_path, files = release_world
    root = _real_install(tmp_path, ["claude"], files)
    monkeypatch.setenv("X4_TOOLKIT", str(root))
    pol = x4u.policy(root)
    pol = dataclasses.replace(pol, setup=(pol.setup[0], "-c", OLD_SETUP))
    staged = guarded.stage_update(root, x4u.PROFILE, pol, notes_files=x4u.NOTES_FILES,
                                  candidate_preparer=render.prepare, generated_paths=x4u.GENERATED_PATHS)
    review = json.loads((Path(staged.bundle) / "complete.json").read_text(encoding="utf-8"))
    rows = {row["path"] for row in review["plan"]["files"]}
    choices = {name: "merged_settings" for name in review.get("merged_settings_hashes", {}) if name in rows}
    before = _hashes(root)
    applied = guarded.apply_update(root, pol, staged.bundle, staged.review_sha256, choices, accept_guard_changes=True,
                                   quiescent=True, notes_files=x4u.NOTES_FILES, new_policy=pol,
                                   candidate_validator=render.validate)
    assert applied.ok is False and applied.rolled_back is True, applied.report
    assert "FAILED at canary" in applied.report and "opencode.config" in applied.report, applied.report
    assert _hashes(root) == before
    assert not any(k for k in before if "__pycache__/" in k), "control: the fresh install had no bytecode"
