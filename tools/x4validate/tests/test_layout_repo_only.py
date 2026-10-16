"""`_layout.require_repo` -- the one door for a test that needs repo-only content (R2-B1).

Each clause of "skip iff installed layout AND content absent" has its own twin, so neither
clause can be dropped without a test going red (CLAUDE.md #26)."""
from __future__ import annotations

import pytest

import _layout


@pytest.mark.parametrize("marker,names", [(".claude/hooks", ["CLAUDE.md"]),
    (".codex/hooks", ["AGENTS.md"]), (".agents/skills", ["AGENTS.md"]),
    (".opencode/hooks", ["AGENTS.md"]), (".claude/hooks", ["CLAUDE.md", "AGENTS.md"])])
def test_runtime_instruction_population_matches_selected_and_present_targets(tmp_path, marker, names):
    (tmp_path / marker).mkdir(parents=True)
    for name in names:
        (tmp_path / name).write_text("selected instruction", encoding="utf-8")
    assert {p.name for p in _layout.runtime_instructions(tmp_path)} == set(names)


@pytest.mark.parametrize("marker", [".claude/hooks", ".codex/hooks", ".agents", ".opencode"])
def test_missing_selected_runtime_instruction_cannot_skip(tmp_path, marker):
    (tmp_path / marker).mkdir(parents=True)
    with pytest.raises(AssertionError, match="instructions are missing"):
        _layout.runtime_instructions(tmp_path)


@pytest.mark.parametrize("relative", [".claude/hooks", ".codex/hooks", ".opencode/hooks", "tools/x4_harness"])
def test_each_common_runtime_carrier_is_examined_and_partial_payload_refuses(tmp_path, relative):
    carrier = tmp_path / relative
    carrier.mkdir(parents=True)
    for name in ("backup-before-edit.sh", "x4validate-on-edit.sh"):
        (carrier / name).write_text("owned", encoding="utf-8")
    assert _layout.common_hook_dir(tmp_path) == carrier
    (carrier / "backup-before-edit.sh").unlink()
    with pytest.raises(AssertionError, match="incomplete common runtime"):
        _layout.common_hook_dir(tmp_path)


def test_missing_all_runtime_carriers_or_instructions_cannot_skip(tmp_path):
    with pytest.raises(AssertionError, match="no common hook runtime"):
        _layout.common_hook_dir(tmp_path)
    with pytest.raises(AssertionError, match="no selected runtime instruction"):
        _layout.runtime_instructions(tmp_path)


@pytest.mark.parametrize("checkout,present,adapter", [(False,False,False), (False,True,False),
    (False,True,True), (True,False,False), (True,True,False), (True,True,True)])
def test_only_an_unselected_installed_codex_transport_can_skip(tmp_path, monkeypatch, checkout, present, adapter):
    monkeypatch.setattr(_layout, "installed_layout", lambda root: not checkout)
    if present:
        (tmp_path / ".codex/hooks").mkdir(parents=True)
    if adapter:
        (tmp_path / ".codex/hooks/codex_adapter.py").write_text("owned", encoding="utf-8")
    if not checkout and not present:
        with pytest.raises(pytest.skip.Exception, match="not selected"):
            _layout.require_native_codex(tmp_path)
    elif not adapter:
        with pytest.raises(AssertionError, match="selected Codex adapter is missing"):
            _layout.require_native_codex(tmp_path)
    else:
        _layout.require_native_codex(tmp_path)


def _installed(tmp_path):
    (tmp_path / "tools").mkdir(parents=True)     # an install: no agent/
    return tmp_path


def _checkout(tmp_path):
    """The toolkit's own checkout: a git repo whose HEAD tracks agent/ (FX-H2: agent/ alone is
    also what an in-place install of the release zip has)."""
    return _repo(tmp_path, track_agent=True)


def test_installed_layout_and_ABSENT_content_SKIPS_with_a_counted_named_reason(tmp_path):
    root = _installed(tmp_path)
    with pytest.raises(pytest.skip.Exception) as e:
        _layout.require_repo("agent/rules/codex-rules.yaml", why="the rule rows", root=root)
    msg = str(e.value)
    assert _layout.REASON in msg and "agent/rules/codex-rules.yaml" in msg and "rule rows" in msg


def test_TWIN_a_CHECKOUT_missing_the_same_file_does_NOT_skip(tmp_path):
    """A checkout that lost a source file is broken, not installed: the test must run (and fail)."""
    _layout.require_repo("agent/rules/codex-rules.yaml", root=_checkout(tmp_path))


def test_TWIN_an_installed_layout_WITH_the_content_does_NOT_skip(tmp_path):
    root = _installed(tmp_path)
    (root / "docs").mkdir()
    _layout.require_repo("docs", root=root)


def test_installed_layout_is_keyed_on_the_toolkit_s_own_git_checkout(tmp_path):
    assert _layout.installed_layout(_installed(tmp_path / "i")) is True
    assert _layout.installed_layout(_checkout(tmp_path / "c")) is False


def test_a_RELEASE_ZIP_installed_IN_PLACE_is_INSTALLED_although_it_has_agent(tmp_path):
    """FX-H2 / I-1 (F269), MEASURED: the README's default install extracts the release zip
    (`git archive`: agent/ present, no .git) and installs IN PLACE. Keyed on agent/, that read
    as a checkout, so every "configured but not present yet" skip (gates, the needs-a-reference
    markers, the freshness root) turned into a failure or an error for that user."""
    root = tmp_path / "zip"
    (root / "agent").mkdir(parents=True)
    (root / "tools").mkdir()
    assert _layout.installed_layout(root) is True


# --- a checkout that LOST agent/ is broken, not installed (v4.0.0 delta review) ----------

def _git(*argv, cwd):
    import subprocess
    r = subprocess.run(["git", *argv], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (argv, r.stdout, r.stderr)


def _repo(root, *, track_agent):
    """A git repo at `root` whose HEAD does (the toolkit) or does not (a game root) hold agent/."""
    root.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=root)
    rel = "agent/x.md" if track_agent else "readme.txt"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text("x", encoding="utf-8")
    _git("add", rel, cwd=root)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
         "commit", "-q", "-m", "init", cwd=root)
    return root


def _drop_agent(root):
    import shutil
    shutil.rmtree(root / "agent")


@pytest.mark.parametrize("checkout", [False, True])
@pytest.mark.parametrize("payload", ["absent", "partial", "namespace-file", "renderer"])
def test_only_an_unselected_installed_opencode_renderer_can_skip(tmp_path, checkout, payload):
    root = _checkout(tmp_path / "source") if checkout else _installed(tmp_path / "installed")
    if payload == "namespace-file":
        (root / ".opencode").write_text("incomplete native namespace", encoding="utf-8")
    elif payload != "absent":
        (root / ".opencode/hooks").mkdir(parents=True)
        if payload == "renderer":
            (root / ".opencode/hooks/opencode_config.py").write_text("owned", encoding="utf-8")
    assert _layout.installed_layout(root) is not checkout
    if not checkout and payload == "absent":
        with pytest.raises(pytest.skip.Exception, match="OpenCode native runtime was not selected"):
            _layout.require_native_opencode(root)
    elif payload != "renderer":
        with pytest.raises(AssertionError, match="selected OpenCode renderer is missing"):
            _layout.require_native_opencode(root)
    else:
        _layout.require_native_opencode(root)


def test_broken_opencode_namespace_cannot_skip_as_unselected(tmp_path):
    import os
    import subprocess
    root = _installed(tmp_path / "installed")
    namespace = root / ".opencode"
    missing = tmp_path / "missing-target"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(namespace), str(missing)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    else:
        namespace.symlink_to(missing, target_is_directory=True)
    assert os.path.lexists(namespace) and not namespace.exists()
    try:
        with pytest.raises(AssertionError, match="selected OpenCode renderer is missing"):
            _layout.require_native_opencode(root)
    except pytest.skip.Exception as exc:
        pytest.fail("a broken selected namespace was skipped as unselected: " + str(exc))


def test_a_CHECKOUT_that_lost_agent_is_NOT_installed_and_does_NOT_skip(tmp_path):
    root = _repo(tmp_path / "tk", track_agent=True)
    _drop_agent(root)
    assert _layout.installed_layout(root) is False
    _layout.require_repo("agent/rules/codex-rules.yaml", root=root)      # no skip -> runs, fails


def test_a_WORKTREE_that_lost_agent_is_NOT_installed(tmp_path):
    """A worktree's `.git` is a FILE, not a directory -- the predicate must take both."""
    main = _repo(tmp_path / "main", track_agent=True)
    wt = tmp_path / "wt"
    _git("worktree", "add", "-q", str(wt), cwd=main)
    assert (wt / ".git").is_file()
    _drop_agent(wt)
    assert _layout.installed_layout(wt) is False


def test_TWIN_an_install_inside_ANOTHER_git_repo_is_still_installed(tmp_path):
    """The `in-game` method installs INTO the game root, which can be a git repo of its own
    (this machine's is). That repo never tracks agent/, so the install is still an install."""
    root = _repo(tmp_path / "game", track_agent=False)
    (root / "tools").mkdir()
    assert _layout.installed_layout(root) is True
    with pytest.raises(pytest.skip.Exception):
        _layout.require_repo("agent/rules/codex-rules.yaml", root=root)


def test_TWIN_a_dot_git_that_git_cannot_answer_for_is_treated_as_installed(tmp_path, monkeypatch):
    """No git on PATH: nothing proves the repo is the toolkit's, so it is not called a checkout."""
    import subprocess
    root = _installed(tmp_path)
    (root / ".git").mkdir()
    _layout._head_tracks_agent.cache_clear()

    def no_git(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(subprocess, "run", no_git)
    try:
        assert _layout.installed_layout(root) is True
    finally:
        _layout._head_tracks_agent.cache_clear()


# --- configured is not present (R2-B1) ---------------------------------------------------

def _paths_pointing_at(monkeypatch, reg, ext, ref):
    from x4validate import _paths
    monkeypatch.setattr(_paths, "registry", lambda: reg)
    monkeypatch.setattr(_paths, "game_extensions", lambda: ext)
    monkeypatch.setattr(_paths, "reference", lambda: ref)
    # the reference is NAMED (X4_REFERENCE), not the derived <X4_TOOLKIT>/reference default
    real_value = _paths.value
    monkeypatch.setattr(_paths, "value", lambda *n: (str(ref) if ref else None)
                        if n == ("X4_REFERENCE",) else real_value(*n))


def _three_cases(tmp_path, monkeypatch):
    """Each of the three roots configured but MISSING, the other two present."""
    (tmp_path / "ext").mkdir()
    (tmp_path / "ref").mkdir()
    (tmp_path / "reg.json").write_text("{}", encoding="utf-8")
    yield lambda: _paths_pointing_at(monkeypatch, tmp_path / "reg.json", tmp_path / "ext",
                                     tmp_path / "nope")
    yield lambda: _paths_pointing_at(monkeypatch, tmp_path / "nope.json", tmp_path / "ext",
                                     tmp_path / "ref")
    yield lambda: _paths_pointing_at(monkeypatch, tmp_path / "reg.json", tmp_path / "nope",
                                     tmp_path / "ref")


def test_a_CONFIGURED_root_that_does_not_exist_is_unresolvable_in_an_INSTALL(tmp_path, monkeypatch):
    """A fresh install configures every root before the user unpacks or runs anything; a gate
    then exits 2 on a missing folder, which import_gate re-raised as an ERROR (MEASURED: 12 in
    an installed toolkit). It is the no-X4-yet case, so it SKIPS like the unconfigured one."""
    import conftest
    inst = _installed(tmp_path / "inst")
    n = 0
    for point in _three_cases(tmp_path, monkeypatch):
        point()
        why = conftest._environment_unresolvable_reason(root=inst)
        assert why and "configured but not present" in why, why
        n += 1
    assert n == 3


def test_TWIN_a_CONFIGURED_root_that_does_not_exist_is_RESOLVABLE_in_a_CHECKOUT(tmp_path, monkeypatch):
    """v4.0.0 delta review: in a checkout the same state is a broken dev configuration, so
    import_gate must RE-RAISE (an error), not skip. Each of the three roots on its own."""
    import conftest
    co = _checkout(tmp_path / "co")
    n = 0
    for point in _three_cases(tmp_path, monkeypatch):
        point()
        assert conftest._environment_unresolvable_reason(root=co) is None
        n += 1
    assert n == 3


def test_an_UNCONFIGURED_root_is_unresolvable_in_EITHER_layout(tmp_path, monkeypatch):
    import conftest
    (tmp_path / "ext").mkdir()
    (tmp_path / "ref").mkdir()
    for root in (_installed(tmp_path / "inst"), _checkout(tmp_path / "co")):
        _paths_pointing_at(monkeypatch, None, tmp_path / "ext", tmp_path / "ref")
        why = conftest._environment_unresolvable_reason(root=root)
        assert why and why.startswith("not configured: registry"), why


def test_import_gate_RE_RAISES_in_a_checkout_with_a_configured_missing_root(tmp_path, monkeypatch):
    """End to end through import_gate: a gate whose import raises TypeError on a configured dev
    checkout is an ERROR, never a skip. (This suite runs from a checkout; in an installed
    layout the same state is the counted skip pinned above.)"""
    import _layout as L
    import conftest
    if L.installed_layout():
        pytest.skip(L.REASON + ": this twin needs the suite to run from a checkout")
    gates = tmp_path / "gates"
    gates.mkdir()
    (gates / "zz_t2_bad_gate.py").write_text("raise TypeError('bad gate')\n", encoding="utf-8")
    monkeypatch.setattr(conftest, "GATES", gates)
    monkeypatch.syspath_prepend(str(gates))
    for point in _three_cases(tmp_path, monkeypatch):
        point()
        with pytest.raises(TypeError, match="bad gate"):
            conftest.import_gate("zz_t2_bad_gate", module_level=False)


def test_TWIN_every_configured_root_present_is_resolvable(tmp_path, monkeypatch):
    import conftest
    (tmp_path / "ext").mkdir()
    (tmp_path / "ref").mkdir()
    (tmp_path / "reg.json").write_text("{}", encoding="utf-8")
    _paths_pointing_at(monkeypatch, tmp_path / "reg.json", tmp_path / "ext", tmp_path / "ref")
    assert conftest._environment_is_unresolvable() is False


def test_reference_unpacked_needs_the_wares_library_not_just_a_folder(tmp_path, monkeypatch):
    from x4validate import _paths
    monkeypatch.setattr(_paths, "reference", lambda: tmp_path)
    assert _layout.reference_unpacked() is False                 # configured, empty
    (tmp_path / "libraries").mkdir()
    (tmp_path / "libraries" / "wares.xml").write_text("<wares/>", encoding="utf-8")
    assert _layout.reference_unpacked() is True                  # twin
    monkeypatch.setattr(_paths, "reference", lambda: None)
    assert _layout.reference_unpacked() is False                 # unconfigured


# --- needs-a-reference: only an INSTALL (or nothing configured) skips -------------------

def _ref(monkeypatch, path, *, explicit=True):
    """Point `_paths.reference()` at `path`; `explicit` = X4_REFERENCE names it (False: only
    the derived <X4_TOOLKIT>/reference default answered)."""
    from x4validate import _paths
    real_value = _paths.value
    monkeypatch.setattr(_paths, "reference", lambda: path)
    monkeypatch.setattr(_paths, "value", lambda *n: (str(path) if explicit and path else None)
                        if n == ("X4_REFERENCE",) else real_value(*n))


def test_a_configured_but_NOT_UNPACKED_reference_skips_only_in_an_INSTALL(tmp_path, monkeypatch):
    ref = tmp_path / "ref"
    ref.mkdir()
    _ref(monkeypatch, ref)
    why = _layout.reference_skip_reason(root=_installed(tmp_path / "inst"))
    assert why and "not unpacked yet" in why, why


def test_TWIN_a_configured_but_NOT_UNPACKED_reference_RUNS_in_a_CHECKOUT(tmp_path, monkeypatch):
    """v4.0.0 delta review: a checkout whose configured reference lost libraries/wares.xml
    must fail its needs-a-reference tests, not skip them."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _ref(monkeypatch, ref)
    assert _layout.reference_skip_reason(root=_checkout(tmp_path / "co")) is None


def test_an_UNCONFIGURED_reference_skips_in_EITHER_layout(tmp_path, monkeypatch):
    _ref(monkeypatch, None)
    for root in (_installed(tmp_path / "inst"), _checkout(tmp_path / "co")):
        why = _layout.reference_skip_reason(root=root)
        assert why and "no reference tree is configured" in why, why


def test_an_UNPACKED_reference_runs_in_EITHER_layout(tmp_path, monkeypatch):
    ref = tmp_path / "ref"
    (ref / "libraries").mkdir(parents=True)
    (ref / "libraries" / "wares.xml").write_text("<wares/>", encoding="utf-8")
    _ref(monkeypatch, ref)
    for root in (_installed(tmp_path / "inst"), _checkout(tmp_path / "co")):
        assert _layout.reference_skip_reason(root=root) is None


def test_the_DERIVED_reference_default_with_nothing_unpacked_skips_even_in_a_CHECKOUT(
        tmp_path, monkeypatch):
    """Clause twin: X4_TOOLKIT alone derives <toolkit>/reference. A checkout whose user never
    named a reference has none -- that is 'not configured', not a broken configuration."""
    ref = tmp_path / "ref"
    ref.mkdir()
    _ref(monkeypatch, ref, explicit=False)
    why = _layout.reference_skip_reason(root=_checkout(tmp_path / "co"))
    assert why and "default" in why, why


def test_TWIN_an_extract_INSIDE_a_toolkit_checkout_is_still_installed(tmp_path):
    """Clause: the `.git` must be AT the root. Asked of git alone, an extract nested in a
    checkout (the cold archive leg's .test-sandbox) would borrow the PARENT's HEAD."""
    parent = _repo(tmp_path / "tk", track_agent=True)
    root = parent / ".test-sandbox" / "zip"
    (root / "agent").mkdir(parents=True)
    assert _layout.installed_layout(root) is True


# --- the SHIPPED git rules, asked without this tree's metadata (FX-H2 / I-1) -------------

def _ask_ignore(repo, rel):
    import subprocess
    return subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", "--no-index", rel]).returncode


def test_shipped_rules_repo_answers_from_the_copied_rules_only(tmp_path):
    root = tmp_path / "zip"
    (root / "sub").mkdir(parents=True)
    (root / ".gitignore").write_text("/rendered.json\n", encoding="utf-8")
    (root / "sub" / ".gitignore").write_text("local.txt\n", encoding="utf-8")
    repo = _layout.shipped_rules_repo(tmp_path / "s", root)
    assert _ask_ignore(repo, "rendered.json") == 0
    assert _ask_ignore(repo, "sub/local.txt") == 0           # a nested rule file travels too
    assert _ask_ignore(repo, "other.json") == 1              # twin: an unruled path is NOT ignored


def test_TWIN_shipped_rules_repo_refuses_a_tree_with_no_rules(tmp_path):
    (tmp_path / "zip").mkdir()
    with pytest.raises(AssertionError, match="no root .gitignore"):
        _layout.shipped_rules_repo(tmp_path / "s", tmp_path / "zip")


def test_own_checkout_is_the_work_tree_TOP_not_any_repo_above(tmp_path):
    tk = _repo(tmp_path / "tk", track_agent=True)
    assert _layout.own_checkout(tk) is True
    nested = tk / "nested"
    nested.mkdir()
    assert _layout.own_checkout(nested) is False             # inside a repo, not its top
    assert _layout.own_checkout(tmp_path / "nowhere") is False


# --- the git question is asked of the repo AT the root, whatever the caller's env (M-a) --
#
# v4.0.0 delta review (reviewer N, MEASURED): an exported GIT_DIR made `git -C <root>` answer
# for SOME OTHER repo, so a real checkout read as an install (x4lock would lock its generated
# trees; the suite would skip its repo-only tests). Dubious ownership did the same. One twin
# per environment the question must survive, and the tri-state keeps "git failed" apart from
# "git answered: not a checkout".

def _status(root):
    _layout._TL._git_answer.cache_clear()
    try:
        return _layout._TL.checkout_status(root)
    finally:
        _layout._TL._git_answer.cache_clear()


def test_an_exported_GIT_DIR_does_not_turn_a_checkout_into_an_install(tmp_path, monkeypatch):
    tk = _repo(tmp_path / "tk", track_agent=True)
    other = _repo(tmp_path / "other", track_agent=False)
    for k, v in (("GIT_DIR", other / ".git"), ("GIT_WORK_TREE", other),
                 ("GIT_INDEX_FILE", other / ".git" / "index"),
                 ("GIT_COMMON_DIR", other / ".git")):
        monkeypatch.setenv(k, str(v))
    assert _status(tk)[0] == "checkout"


def test_TWIN_an_exported_GIT_DIR_does_not_turn_an_install_into_a_checkout(tmp_path, monkeypatch):
    game = _repo(tmp_path / "game", track_agent=False)
    tk = _repo(tmp_path / "tk", track_agent=True)
    monkeypatch.setenv("GIT_DIR", str(tk / ".git"))           # points at a repo that HAS agent/
    assert _status(game)[0] == "install"


def test_a_worktree_with_an_exported_GIT_DIR_is_still_a_checkout(tmp_path, monkeypatch):
    main = _repo(tmp_path / "main", track_agent=True)
    wt = tmp_path / "wt"
    _git("worktree", "add", "-q", str(wt), cwd=main)
    assert (wt / ".git").is_file()
    monkeypatch.setenv("GIT_DIR", str(_repo(tmp_path / "other", track_agent=False) / ".git"))
    assert _status(wt)[0] == "checkout"


def _skip_unless_the_dubious_knob_bites(probe):
    """The control for the test below. GIT_TEST_ASSUME_DIFFERENT_OWNER is a git TEST knob, and
    a runner's git need not honour it: on CI ubuntu-latest git answered rc 0 with it set. Where
    the knob cannot produce dubious ownership, the subject assertion would prove nothing, so it
    is a COUNTED skip naming what was observed -- never a fail, never a bare return."""
    if probe.returncode == 0 or "dubious" not in probe.stderr:
        pytest.skip(
            "dubious-ownership control NOT produced on this platform: with "
            f"GIT_TEST_ASSUME_DIFFERENT_OWNER=1, `git cat-file -e HEAD:agent` returned "
            f"rc={probe.returncode} (wanted non-zero) and stderr {probe.stderr.strip()[:200]!r} "
            "(wanted 'dubious'); the checkout-vs-install assertion was NOT run")


def test_DUBIOUS_OWNERSHIP_does_not_turn_a_checkout_into_an_install(tmp_path, monkeypatch):
    """git's own knob simulates a repo owned by someone else (it refuses with rc 128 unless the
    root is a safe.directory) -- the shape of a checkout copied from another account."""
    tk = _repo(tmp_path / "tk", track_agent=True)
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    import subprocess
    probe = subprocess.run(["git", "-C", str(tk), "cat-file", "-e", "HEAD:agent"],
                           capture_output=True, text=True)
    _skip_unless_the_dubious_knob_bites(probe)                      # control: the knob bites
    assert _status(tk)[0] == "checkout"


class _Probe:
    def __init__(self, rc, stderr):
        self.returncode, self.stderr = rc, stderr


def test_TWIN_where_the_dubious_knob_BITES_the_control_lets_the_test_proceed():
    _skip_unless_the_dubious_knob_bites(_Probe(128, "fatal: detected dubious ownership in repository"))


@pytest.mark.parametrize("rc,stderr", [
    (0, ""),                                      # clause 1: git did not refuse (CI ubuntu)
    (128, "fatal: not a git repository"),         # clause 2: refused, but not for ownership
])
def test_TWIN_where_the_dubious_knob_does_NOT_bite_it_is_a_counted_skip_naming_the_observation(rc, stderr):
    with pytest.raises(pytest.skip.Exception) as e:
        _skip_unless_the_dubious_knob_bites(_Probe(rc, stderr))
    msg = str(e.value)
    assert f"rc={rc}" in msg and repr(stderr) in msg and "NOT run" in msg, msg


def test_NO_GIT_on_PATH_is_UNKNOWN_with_a_reason_and_never_a_checkout(tmp_path, monkeypatch):
    import subprocess
    tk = _repo(tmp_path / "tk", track_agent=True)

    def no_git(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(subprocess, "run", no_git)
    state, why = _status(tk)
    assert state == "unknown" and "git" in why
    _layout._TL._git_answer.cache_clear()
    try:
        assert _layout._TL.is_source_checkout(tk) is False      # the documented policy
    finally:
        _layout._TL._git_answer.cache_clear()


def test_a_repo_git_CANNOT_read_is_UNKNOWN_not_install(tmp_path):
    """No commit yet: git fails, which proves nothing either way -- unlike a HEAD without agent/."""
    root = tmp_path / "empty"
    root.mkdir()
    _git("init", "-q", cwd=root)
    state, why = _status(root)
    assert state == "unknown" and why


def test_TWIN_no_dot_git_at_the_root_is_INSTALL_without_asking_git(tmp_path, monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise AssertionError("git must not be asked")
    monkeypatch.setattr(subprocess, "run", boom)
    root = tmp_path / "zip"
    (root / "agent").mkdir(parents=True)
    assert _status(root)[0] == "install"


def test_a_skip_resting_on_UNKNOWN_says_so_in_its_reason(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    _git("init", "-q", cwd=root)                     # a .git git cannot answer for: no commit
    with pytest.raises(pytest.skip.Exception) as e:
        _layout.require_repo("agent/rules/codex-rules.yaml", root=root)
    assert "layout UNKNOWN" in str(e.value)


def test_TWIN_a_skip_on_a_SETTLED_install_carries_no_caveat(tmp_path):
    root = _repo(tmp_path / "game", track_agent=False)
    with pytest.raises(pytest.skip.Exception) as e:
        _layout.require_repo("agent/rules/codex-rules.yaml", root=root)
    assert "UNKNOWN" not in str(e.value)
