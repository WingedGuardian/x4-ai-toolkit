r"""x4refguard must refuse every target that is not the configured, unpacked reference root,
and must never report "protected" for a state it did not confirm.

THE REAL REFERENCE TREE IS NEVER TOUCHED. Every test points X4_REFERENCE at a `tmp_path`
tree, and the autouse `_sandbox` fixture sets `X4_REFGUARD_SANDBOX` to that `tmp_path`:
x4refguard then refuses (SandboxViolation, which no `except Exception` swallows) every
mutating call -- icacls, chattr, chflags, chmod -- aimed anywhere else. The sandbox is
itself asserted to sit under the system temp dir before any test body runs.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("x4refguard", REPO / "scripts" / "x4refguard.py")
x4refguard = importlib.util.module_from_spec(_spec)
sys.modules["x4refguard"] = x4refguard
_spec.loader.exec_module(x4refguard)
from x4validate import _paths  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refguard_owner  # noqa: E402
from refguard_owner import own_or_skip  # noqa: E402

SENTINEL = ".unpacked-and-locked"


@pytest.mark.parametrize("platform", ["windows", "linux"])
@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("fault", [None, "blocked", "."])
def test_unreadable_tree_cannot_report_complete_protection(tmp_path, monkeypatch, platform, full, fault):
    root = tmp_path / "enumeration-reference"
    for directory in ("visible", "blocked"):
        (root / directory).mkdir(parents=True)
        (root / directory / "file.xml").write_text("owned fixture", encoding="utf-8")
    (root / SENTINEL).write_text("owned", encoding="utf-8")
    sid = "S-1-5-21-OWNED-FIXTURE"
    monkeypatch.setattr(x4refguard, "_platform", lambda: platform)
    monkeypatch.setattr(x4refguard, "_user_sid", lambda: sid)
    monkeypatch.setattr(x4refguard, "_owner_is_user", lambda p: True)
    monkeypatch.setattr(x4refguard, "_posix_mark", lambda p: "readonly")
    monkeypatch.setattr(x4refguard, "_readonly", lambda p: True)
    def acl(objects):
        return sid, [{"path": str(p), "owner": sid, "rules": [{"sid": sid, "type": 1,
                     "rights": x4refguard.EXPECTED_MASK, "inherited": p != root,
                     "inh": 3, "prop": 0}]} for p in objects]
    monkeypatch.setattr(x4refguard, "_acl", acl)
    original = os.scandir
    def scandir(path):
        if fault is not None and Path(path) == root / fault:
            raise PermissionError(13, "owned enumeration denial", str(path))
        return original(path)
    monkeypatch.setattr(os, "scandir", scandir)
    result = (x4refguard._win_report if platform == "windows" else x4refguard._posix_report)(root, full)
    assert result["enumeration_complete"] is (fault is None), result
    assert result["sample_scope"] == ("FULL walk" if full else x4refguard.SAMPLE_SCOPE), result
    if fault is None:
        assert result["state"] == "protected", result
        assert result["sampled"] == (6 if full else 4), result
    else:
        assert result["state"] == "error", result
        assert "incomplete" in result["detail"] and "enumerat" in result["detail"], result
        assert result["sampled"] == 0 and result["sample_ok"] == 0, result


def test_count_failure_refuses_before_any_permission_change(tmp_path, monkeypatch, capsys):
    def count(root):
        raise PermissionError("owned enumeration failure")
    monkeypatch.setattr(x4refguard, "_count", count)
    assert x4refguard._confirm(tmp_path, "apply", True) == 2
    assert "Nothing was changed" in capsys.readouterr().err


def test_chmod_walk_failure_is_not_reported_as_completed(tmp_path, monkeypatch):
    def walk(root):
        yield root
        raise PermissionError("owned enumeration failure")
    calls = []
    monkeypatch.setattr(x4refguard, "_walk", walk)
    monkeypatch.setattr(x4refguard, "_mode", lambda p: 0o755)
    monkeypatch.setattr(x4refguard, "_mutate_chmod", lambda *args: calls.append(args))
    assert x4refguard._chmod_tree(tmp_path, writable=False) > 0
    assert len(calls) == 1  # Synthetic mutation seam only, no filesystem permissions changed.


@pytest.mark.parametrize("action", ["apply", "remove"])
def test_incomplete_chmod_cannot_report_success(tmp_path, monkeypatch, action):
    monkeypatch.setattr(x4refguard, "_platform", lambda: "linux")
    monkeypatch.setattr(x4refguard, "_geteuid", lambda: 1000)
    monkeypatch.setattr(x4refguard, "_owner_is_user", lambda p: True)
    monkeypatch.setattr(x4refguard, "_confirm", lambda *args: None)
    monkeypatch.setattr(x4refguard, "_posix_report", lambda *args: {"state": "absent", "detail": "mocked"})
    monkeypatch.setattr(x4refguard, "_posix_mark", lambda p: "readonly" if action == "remove" else None)
    monkeypatch.setattr(x4refguard, "_readonly", lambda p: False)
    monkeypatch.setattr(x4refguard, "_chmod_tree", lambda *args, **kwargs: 1)
    assert (x4refguard._posix_apply if action == "apply" else x4refguard._posix_remove)(tmp_path, yes=True) == 1


def _under_temp(p: Path) -> bool:
    tmp = os.path.normcase(str(Path(tempfile.gettempdir()).resolve()))
    q = os.path.normcase(str(Path(p).resolve()))
    return q != tmp and q.startswith(tmp + os.sep)


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    assert _under_temp(tmp_path), "pytest tmp_path is not under the system temp dir: %s" % tmp_path
    monkeypatch.setenv(x4refguard.SANDBOX_ENV, str(tmp_path))
    # FX-B2: NO real config. Without this the resolver read the CHECKOUT's own x4-paths.env --
    # on a developer machine a real one, whose X4_REFERENCE differs from the scratch tree the
    # tests export, and an apply/remove then (correctly) refuses on the disagreement. An
    # X4_CONFIG naming no file means "read none": the exported X4_REFERENCE is the config.
    # FX-B3: that idiom now REFUSES apply/remove (an inherited X4_CONFIG naming a missing file
    # or another toolkit's config disabled the env-vs-config check). The acting toolkit is
    # instead a scratch folder holding NO config, which reads none just the same.
    monkeypatch.delenv("X4_CONFIG", raising=False)
    monkeypatch.delenv("X4_TOOLKIT", raising=False)   # else it names a toolkit other than kit
    (tmp_path / "kit").mkdir()
    monkeypatch.setattr(_paths, "_SELF", tmp_path / "kit", raising=False)
    monkeypatch.setattr(_paths, "_EXPLICIT", None, raising=False)
    monkeypatch.setattr(_paths, "_NOTICED", set(), raising=False)
    _paths.reload()
    yield
    _paths.reload()


@pytest.fixture
def ref(tmp_path, monkeypatch):
    root = tmp_path / "reference"
    (root / "libraries").mkdir(parents=True)
    (root / "libraries" / "wares.xml").write_text("<wares/>", encoding="utf-8")
    (root / SENTINEL).write_text("Re-unpacked from X4 (steam buildid 1) on 2026-10-02.", encoding="utf-8")
    own_or_skip(root, x4refguard)          # an elevated runner creates it owned by Administrators
    monkeypatch.setenv("X4_REFERENCE", str(root))
    _paths.reload()
    yield root
    _paths.reload()


# ------------------------------------------------------------- Task 2: resolution

def test_resolve_returns_the_configured_root(ref):
    assert x4refguard.resolve_target(None, action="apply") == ref.resolve()


def test_a_path_that_is_not_the_configured_root_is_REFUSED(ref, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / SENTINEL).write_text("x")
    with pytest.raises(x4refguard.Refused, match="not the configured reference root"):
        x4refguard.resolve_target(other, action="apply")


def test_case_and_slash_variants_of_the_configured_root_are_ACCEPTED(ref):
    variant = Path(str(ref).replace("\\", "/"))
    if os.name == "nt":
        variant = Path(str(variant).upper())
    assert x4refguard.resolve_target(variant, action="apply") == ref.resolve()


def test_apply_REFUSES_a_root_without_the_sentinel(ref):
    (ref / SENTINEL).unlink()
    with pytest.raises(x4refguard.Refused, match="sentinel"):
        x4refguard.resolve_target(None, action="apply")


def test_remove_does_NOT_need_the_sentinel(ref):        # the escape hatch must always work
    (ref / SENTINEL).unlink()
    assert x4refguard.resolve_target(None, action="remove") == ref.resolve()


def test_apply_REFUSES_a_root_that_is_not_a_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("X4_REFERENCE", str(tmp_path / "nope"))
    _paths.reload()
    try:
        with pytest.raises(x4refguard.Refused, match="not an existing directory"):
            x4refguard.resolve_target(None, action="apply")
    finally:
        _paths.reload()


@pytest.mark.parametrize("bad", ["drive_root", "home", "home_parent", "git_root"])
def test_apply_REFUSES_dangerous_roots_even_when_configured(bad, tmp_path, monkeypatch):
    if bad == "drive_root":
        root = Path(Path.cwd().anchor)
    elif bad == "home":
        root = Path.home()
    elif bad == "home_parent":
        root = Path.home().parent
    else:
        root = tmp_path / "repo"
        (root / ".git").mkdir(parents=True)
        (root / SENTINEL).write_text("x")
    monkeypatch.setenv("X4_REFERENCE", str(root))
    _paths.reload()
    try:
        with pytest.raises(x4refguard.Refused):
            x4refguard.resolve_target(None, action="apply")
    finally:
        _paths.reload()


def test_the_git_root_refusal_is_the_git_clause_not_the_sentinel(tmp_path, monkeypatch):
    # Falsification twin of the git_root row: WITH the sentinel present, only the `.git`
    # clause can refuse it -- and without `.git` the same tree resolves.
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / SENTINEL).write_text("x")
    monkeypatch.setenv("X4_REFERENCE", str(root))
    _paths.reload()
    try:
        with pytest.raises(x4refguard.Refused, match="git"):
            x4refguard.resolve_target(None, action="apply")
        (root / ".git").rmdir()
        assert x4refguard.resolve_target(None, action="apply") == root.resolve()
    finally:
        _paths.reload()


def test_apply_REFUSES_the_game_root(tmp_path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    (game / SENTINEL).write_text("x")
    monkeypatch.setenv("X4_REFERENCE", str(game))
    monkeypatch.setenv("X4_GAME", str(game))
    _paths.reload()
    try:
        with pytest.raises(x4refguard.Refused, match="game"):
            x4refguard.resolve_target(None, action="apply")
    finally:
        _paths.reload()


def test_apply_REFUSES_an_ancestor_of_the_game_root(tmp_path, monkeypatch):
    game = tmp_path / "steam" / "game"
    game.mkdir(parents=True)
    (tmp_path / "steam" / SENTINEL).write_text("x")
    monkeypatch.setenv("X4_REFERENCE", str(tmp_path / "steam"))
    monkeypatch.setenv("X4_GAME", str(game))
    _paths.reload()
    try:
        with pytest.raises(x4refguard.Refused, match="game"):
            x4refguard.resolve_target(None, action="apply")
    finally:
        _paths.reload()


def test_apply_REFUSES_the_toolkit_root(monkeypatch):
    # The toolkit itself is a git checkout too, so this asserts the TOOLKIT clause by name.
    monkeypatch.setenv("X4_REFERENCE", str(REPO))
    _paths.reload()
    try:
        with pytest.raises(x4refguard.Refused, match="toolkit"):
            x4refguard.resolve_target(None, action="apply")
    finally:
        _paths.reload()


def test_UNCONFIGURED_is_exit_2_and_says_so(monkeypatch, capsys):
    monkeypatch.setattr(x4refguard, "_configured_root", lambda: None)
    rc = x4refguard.main(["status", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["state"] == "unconfigured"
    assert x4refguard.main(["apply", "--yes"]) == 2
    assert x4refguard.main(["remove", "--yes"]) == 2


def test_a_paths_IMPORT_failure_is_never_unconfigured_by_accident(monkeypatch):
    # x4lock's Unresolvable rule: "could not look" must not read as "nothing configured".
    monkeypatch.setattr(x4refguard, "_paths", None)
    with pytest.raises(x4refguard.Unresolvable):
        x4refguard._configured_root()


def test_an_unknown_platform_is_UNSUPPORTED_exit_3_never_protected(ref, monkeypatch, capsys):
    # Decision #15: exit 3 is ONLY for a platform with no mechanism at all. PIN, not skip.
    monkeypatch.setattr(x4refguard, "_platform", lambda: "sunos5")
    rc = x4refguard.main(["status", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 3 and out["state"] == "unsupported"
    assert x4refguard.main(["apply", "--yes"]) == 3
    assert x4refguard.main(["remove", "--yes"]) == 3


def test_status_json_is_ONE_object_even_when_refusing(monkeypatch, capsys):
    monkeypatch.setattr(x4refguard, "_configured_root", lambda: None)
    x4refguard.main(["status", "--json"])
    out = capsys.readouterr().out.strip()
    assert out.count("\n") == 0
    obj = json.loads(out)
    for key in ("state", "root", "mask", "mask_expected", "sentinel", "owner_is_user",
                "sampled", "sample_ok", "sample_scope", "detail", "mechanism",
                "stops", "does_not_stop"):
        assert key in obj, key


# ------------------------------------------------- Task 3: the Windows mechanism

import subprocess  # noqa: E402

win = pytest.mark.skipif(os.name != "nt", reason="NTFS ACLs: the Windows mechanism")
posix = pytest.mark.skipif(os.name == "nt", reason="real chmod semantics: POSIX only")


def scratch_icacls(tmp_path, target, *args):
    """A TEST's own icacls call (setup/teardown). Asserted under the temp dir AND routed
    through x4refguard's sandboxed choke point -- two independent checks."""
    assert _under_temp(target) and str(Path(target).resolve()).lower().startswith(
        str(tmp_path.resolve()).lower()), "test icacls aimed outside the scratch tree: %s" % target
    return x4refguard._mutate_run(["icacls", target, *args], target)


def scratch_unprotect(tmp_path, root):
    """Never leave an undeletable directory in the temp dir, whatever the test did."""
    if os.name == "nt":
        scratch_icacls(tmp_path, root, "/reset", "/T", "/C", "/Q")
    else:
        for p in [root, *Path(root).rglob("*")]:
            if not p.is_symlink():
                try:
                    x4refguard._mutate_chmod(p, (p.stat().st_mode & 0o7777) | 0o200)
                except OSError:
                    pass


@pytest.fixture
def tripwire(monkeypatch, tmp_path):
    """Fail the test if ANY icacls call names a path outside tmp_path. Independent of
    the in-module sandbox: it wraps subprocess.run itself."""
    real = subprocess.run
    seen = []

    def guarded(argv, *a, **k):
        if isinstance(argv, (list, tuple)) and argv and Path(str(argv[0])).stem.lower() == "icacls":
            target = Path(argv[1]).resolve()
            assert str(target).lower().startswith(str(tmp_path.resolve()).lower()), \
                "icacls aimed OUTSIDE the scratch tree: %s" % target
            seen.append(list(argv))
        return real(argv, *a, **k)
    monkeypatch.setattr(x4refguard.subprocess, "run", guarded)
    return seen


@pytest.fixture
def protected_cleanup(ref, tmp_path):
    yield ref
    scratch_unprotect(tmp_path, ref)


def test_SANDBOX_refuses_a_mutation_outside_it(tmp_path):         # falsification twin
    # The OUTSIDE target is a SCRATCH sibling, never Path.home() (v4.0.0 review R7-5): if the
    # sandbox check ever regressed, this twin would have chmod-ed the developer's home.
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir(exist_ok=True)
    with pytest.raises(x4refguard.SandboxViolation):
        x4refguard._mutate_run(["icacls", outside, "/?"], outside)
    with pytest.raises(x4refguard.SandboxViolation):
        x4refguard._mutate_chmod(outside, 0o755)
    with pytest.raises(x4refguard.SandboxViolation):       # the sandbox itself is not "under" it
        x4refguard._mutate_chmod(tmp_path, 0o755)


def test_SANDBOX_is_not_an_Exception_so_nothing_swallows_it():
    assert not issubclass(x4refguard.SandboxViolation, Exception)


@win
def test_TRIPWIRE_fires_on_an_outside_path(tripwire):              # falsification twin
    with pytest.raises(AssertionError, match="OUTSIDE"):
        x4refguard.subprocess.run(["icacls", str(Path.home()), "/?"], capture_output=True)


def test_EXPECTED_MASK_is_delete_plus_write_and_nothing_else():
    # DE 0x10000 + DC 0x40 + WD 0x2 + AD 0x4. Never SYNCHRONIZE (0x100000), WRITE_DAC
    # (0x40000) or WRITE_OWNER (0x80000): those denied reads / made the deny unremovable.
    assert x4refguard.EXPECTED_MASK == 65606
    assert x4refguard.EXPECTED_MASK & (0x100000 | 0x40000 | 0x80000) == 0
    assert x4refguard.ICACLS_SPEC == "(OI)(CI)(DE,DC,WD,AD)"


@win
def test_status_before_apply_is_ABSENT_exit_1(ref, tripwire):
    r = x4refguard.report()
    assert r["state"] == "absent" and r["owner_is_user"] is True and r["sentinel"] is True
    assert x4refguard.main(["status"]) == 1
    assert not tripwire, "status made an icacls call"


@win
def test_apply_then_status_is_PROTECTED_with_the_exact_mask(protected_cleanup, tripwire):
    assert x4refguard.main(["apply", "--yes"]) == 0
    r = x4refguard.report()
    assert r["state"] == "protected"
    assert r["mask"] == x4refguard.EXPECTED_MASK == 65606
    assert r["sample_ok"] == r["sampled"] >= 3              # root + sentinel + libraries/wares.xml
    assert r["stops"] and r["does_not_stop"]
    assert tripwire, "apply made no icacls call -- it cannot have applied anything"
    assert x4refguard.main(["status"]) == 0


@win
def test_apply_is_IDEMPOTENT_one_ace_no_second_write(protected_cleanup, tripwire):
    assert x4refguard.main(["apply", "--yes"]) == 0
    n = len(tripwire)
    assert x4refguard.main(["apply", "--yes"]) == 0
    assert len(tripwire) == n, "a second apply re-ran icacls"
    assert x4refguard._explicit_denies(protected_cleanup) == [x4refguard.EXPECTED_MASK]


@win
def test_remove_leaves_ZERO_deny_entries_anywhere(protected_cleanup, tripwire):
    assert x4refguard.main(["apply", "--yes"]) == 0
    assert x4refguard.main(["remove", "--yes"]) == 0
    assert x4refguard.report(full=True)["state"] == "absent"
    objs = [protected_cleanup, *protected_cleanup.rglob("*")]
    _, items = x4refguard._acl(objs)
    assert len(items) == len(objs) >= 4
    for p, it in zip(objs, items):
        assert x4refguard._deny_masks(p, it) == [], p          # explicit AND inherited


@win
def test_remove_when_nothing_is_applied_is_a_verified_noop(ref, tripwire):
    assert x4refguard.main(["remove", "--yes"]) == 0
    assert not tripwire


@win
def test_a_FOREIGN_deny_is_refused_and_left_alone(protected_cleanup, tripwire, tmp_path):
    sid = x4refguard._user_sid()
    scratch_icacls(tmp_path, protected_cleanup, "/deny", "*%s:(WEA)" % sid)
    assert x4refguard.main(["apply", "--yes"]) == 2
    assert x4refguard.main(["remove", "--yes"]) == 2
    assert x4refguard.report()["state"] == "foreign"
    assert 16 in x4refguard._explicit_denies(protected_cleanup)    # WriteExtendedAttributes untouched


@win
def test_apply_REFUSES_a_root_the_user_does_not_own(protected_cleanup, tripwire, monkeypatch):
    monkeypatch.setattr(x4refguard, "_owner_is_user", lambda p: False)
    assert x4refguard.main(["apply", "--yes"]) == 2                  # the lockout precondition (x4lock history)
    assert not tripwire


@win
def test_the_refusal_sees_a_REAL_foreign_owner():
    """The twin of the monkeypatched refusal above, on a real object: the Windows directory is
    owned by TrustedInstaller on every install, never by the user -- elevated or not."""
    sysroot = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    assert sysroot.is_dir()
    assert x4refguard._owner_is_user(sysroot) is False


class _Owner:
    """Fakes the owner read: `owned` flips to True only when /setowner names the user's SID."""

    def __init__(self, monkeypatch, settable):
        self.owned, self.calls, self.settable = False, [], settable
        monkeypatch.setattr(x4refguard, "_owner_is_user", lambda p: self.owned)
        monkeypatch.setattr(x4refguard, "_user_sid", lambda: "S-1-5-21-1-2-3-1001")

        def run(argv, target):
            self.calls.append([str(a) for a in argv])
            if self.settable and "/setowner" in self.calls[-1] and "*S-1-5-21-1-2-3-1001" in self.calls[-1]:
                self.owned = True
            return subprocess.CompletedProcess(argv, 0 if self.settable else 5, "", "denied")
        monkeypatch.setattr(x4refguard, "_mutate_run", run)


def test_OWNERSHIP_fixture_takes_the_root_when_it_can(tmp_path, monkeypatch):
    o = _Owner(monkeypatch, settable=True)
    own_or_skip(tmp_path, x4refguard, windows=True)
    assert o.owned and len(o.calls) == 3 and o.calls[0][:3] == ["icacls", str(tmp_path), "/setowner"]
    # FX-R2: the access the owner change can drop is granted back, so the tree stays removable
    assert o.calls[1][:4] == ["icacls", str(tmp_path), "/reset", "/T"]
    assert o.calls[2][:4] == ["icacls", str(tmp_path), "/grant", "*S-1-5-21-1-2-3-1001:(OI)(CI)F"]
    assert "/T" not in o.calls[2], "explicit child grants override inherited protection"


def test_OWNERSHIP_fixture_does_not_GRANT_when_the_owner_change_failed(tmp_path, monkeypatch):
    o = _Owner(monkeypatch, settable=False)
    with pytest.raises(pytest.skip.Exception):
        own_or_skip(tmp_path, x4refguard, windows=True)
    assert len(o.calls) == 1 and "/setowner" in o.calls[0]


def test_OWNERSHIP_fixture_SKIPS_when_it_cannot__never_passes(tmp_path, monkeypatch):
    _Owner(monkeypatch, settable=False)
    with pytest.raises(pytest.skip.Exception, match="NOT CHECKED"):
        own_or_skip(tmp_path, x4refguard, windows=True)


def test_OWNERSHIP_fixture_is_a_NO_OP_on_a_tree_already_owned(tmp_path, monkeypatch):
    o = _Owner(monkeypatch, settable=True)
    o.owned = True
    own_or_skip(tmp_path, x4refguard, windows=True)
    assert o.calls == []


@win
def test_OWNERSHIP_the_real_setowner_command_is_accepted(tmp_path):
    """The exact icacls step the fixture uses, run for real on a scratch dir this user owns.

    FX-R2 (2026-10-05): it also must leave a tree the user can still READ and DELETE. MEASURED
    under Python 3.13, whose mkdir(0o700) -- pytest's temp root -- grants access only through
    an OWNER RIGHTS ACE: `/setowner` (even to the same owner) left the folder unreadable and
    undeletable without a repair, and every run stranded one in pytest's garbage (20 found)."""
    d = tmp_path / "own"
    d.mkdir()
    r = refguard_owner.take_ownership(d, x4refguard)
    assert r.returncode == 0, r.stdout + r.stderr
    assert x4refguard._owner_is_user(d) is True
    (d / "f.txt").write_text("x", encoding="utf-8")      # still writable ...
    assert os.listdir(d) == ["f.txt"]                     # ... readable ...
    (d / "f.txt").unlink()
    d.rmdir()                                             # ... and removable, no admin


@win
def test_OWNERSHIP_repair_preserves_the_inherited_deny(protected_cleanup, tripwire):
    """Force the elevated-runner fixture branch even when already the owner.

    Root access must remain repairable without granting explicit child allows
    that take precedence over the reference guard's inherited deny.
    """
    objects = [protected_cleanup, *protected_cleanup.rglob("*")]
    states = []
    def snapshot(label):
        sid, items = x4refguard._acl(objects)
        states.append((label, [{"node": str(Path(it["path"]).relative_to(protected_cleanup)),
                               "rules": [{**{k: r.get(k) for k in ("type", "rights", "inherited", "inh", "prop")},
                                          "user": r.get("sid") == sid} for r in it["rules"]]}
                              for it in items]))
    snapshot("before")
    r = refguard_owner.take_ownership(protected_cleanup, x4refguard)
    assert r.returncode == 0, r.stdout + r.stderr
    snapshot("ownership")
    rc = x4refguard.main(["apply", "--yes"])
    snapshot("deny")
    if rc != 0:
        print(json.dumps(states))
    assert rc == 0, states
    report = x4refguard.report(full=True)
    assert report["state"] == "protected", report
    assert report["sample_ok"] == report["sampled"]


@win
def test_an_APPLY_verification_mismatch_is_exit_1_never_0(protected_cleanup, tripwire, monkeypatch):
    monkeypatch.setattr(x4refguard, "_explicit_denies", lambda *a, **k: [])   # icacls "succeeded", ACL disagrees
    assert x4refguard.main(["apply", "--yes"]) == 1
    assert tripwire


@win
def test_a_REMOVE_verification_mismatch_is_exit_1_never_0(protected_cleanup, tripwire, monkeypatch):
    assert x4refguard.main(["apply", "--yes"]) == 0
    monkeypatch.setattr(x4refguard, "_explicit_denies",
                        lambda *a, **k: [x4refguard.EXPECTED_MASK])        # the deny "survives"
    assert x4refguard.main(["remove", "--yes"]) == 1


@win
def test_remove_on_an_OLD_root_needs_our_exact_ace(protected_cleanup, tripwire, tmp_path, monkeypatch):
    assert x4refguard.main(["apply", "--yes"]) == 0
    new = tmp_path / "newref"
    new.mkdir()
    monkeypatch.setenv("X4_REFERENCE", str(new))
    _paths.reload()
    assert x4refguard.main(["remove", "--yes", "--path", str(protected_cleanup)]) == 0   # carries our ACE
    other = tmp_path / "other"
    other.mkdir()
    assert x4refguard.main(["remove", "--yes", "--path", str(other)]) == 2               # carries none


@win
def test_PARTIAL_is_named_when_a_child_has_inheritance_cut(protected_cleanup, tripwire, tmp_path):
    assert x4refguard.main(["apply", "--yes"]) == 0
    child = protected_cleanup / "libraries" / "wares.xml"
    scratch_icacls(tmp_path, child, "/inheritance:r", "/grant", "*%s:F" % x4refguard._user_sid())
    r = x4refguard.report(full=True)
    assert r["state"] == "partial" and r["sample_ok"] < r["sampled"]
    assert x4refguard.main(["status"]) == 1
    assert x4refguard.main(["apply", "--yes"]) == 1          # re-applying cannot fix a cut child: never 0


@win
def test_status_with_an_UNREADABLE_acl_is_error_never_absent(ref, monkeypatch):
    def broken(paths):
        raise x4refguard.AclError("simulated")
    monkeypatch.setattr(x4refguard, "_acl", broken)
    r = x4refguard.report()
    assert r["state"] == "error"
    assert x4refguard.main(["status"]) == 2
    assert x4refguard.main(["apply", "--yes"]) == 2


# --------------------------------------------- Task 3: the POSIX mechanism (#15)

@posix
def test_POSIX_chmod_apply_status_remove_round_trip(protected_cleanup, monkeypatch):
    monkeypatch.setattr(x4refguard, "_geteuid", lambda: 12345)       # force the chmod branch
    monkeypatch.setattr(x4refguard, "_owner_is_user", lambda p: True)
    assert x4refguard.report()["state"] == "absent"
    assert x4refguard.main(["apply", "--yes"]) == 0
    r = x4refguard.report(full=True)
    assert r["state"] == "protected" and r["mechanism"].startswith("chmod")
    assert r["sample_ok"] == r["sampled"] >= 4
    assert any("root" in g for g in r["does_not_stop"])
    assert x4refguard.main(["remove", "--yes"]) == 0
    assert x4refguard.report()["state"] == "absent"
    (protected_cleanup / "libraries" / "wares.xml").write_text("ok")   # writable again


@posix
def test_POSIX_chmod_actually_stops_delete_and_write(protected_cleanup, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("root bypasses permission bits (a disclosed gap, not a test failure)")
    monkeypatch.setattr(x4refguard, "_geteuid", lambda: 12345)
    monkeypatch.setattr(x4refguard, "_owner_is_user", lambda p: True)
    assert x4refguard.main(["apply", "--yes"]) == 0
    f = protected_cleanup / "libraries" / "wares.xml"

    def _try(fn):
        try:
            fn()
        except OSError:
            pass
    _try(lambda: os.remove(f))
    _try(lambda: open(f, "w").write("x"))
    _try(lambda: (protected_cleanup / "new.xml").write_text("x"))
    _try(lambda: os.rename(f, f.with_name("moved.xml")))
    # judged by the DISK, never by whether a call raised
    assert f.read_text() == "<wares/>" and not (protected_cleanup / "new.xml").exists()
    assert not f.with_name("moved.xml").exists()


@posix
def test_POSIX_CONTROL_the_same_primitives_work_unprotected(tmp_path):
    root = tmp_path / "plain"
    (root / "libraries").mkdir(parents=True)
    f = root / "libraries" / "wares.xml"
    f.write_text("<wares/>")
    open(f, "w").write("x")
    (root / "new.xml").write_text("x")
    os.rename(f, f.with_name("moved.xml"))
    os.remove(f.with_name("moved.xml"))
    assert (root / "new.xml").exists() and not f.exists()


class _FakeFS:
    """Fakes for the chattr/chflags/chmod branches, so they run on every OS."""

    def __init__(self, monkeypatch, plat, euid, tools):
        self.calls, self.immutable, self.ro = [], set(), set()
        self.honour = True
        monkeypatch.setattr(x4refguard, "_platform", lambda: plat)
        monkeypatch.setattr(x4refguard, "_geteuid", lambda: euid)
        monkeypatch.setattr(x4refguard, "_owner_is_user", lambda p: True)
        monkeypatch.setattr(x4refguard, "_which", lambda n: ("/usr/bin/" + n) if n in tools else None)
        monkeypatch.setattr(x4refguard, "_flags",
                            lambda paths: {str(p): str(p) in self.immutable for p in paths})
        monkeypatch.setattr(x4refguard, "_mode",
                            lambda p: 0o40555 if str(p) in self.ro else 0o40755)

        def run(argv, target):
            x4refguard._sandbox_check(target)
            self.calls.append([str(a) for a in argv])
            if self.honour:
                objs = [str(p) for p in x4refguard._walk(Path(target))]
                if "+i" in argv or "uchg" in argv:
                    self.immutable.update(objs)
                if "-i" in argv or "nouchg" in argv:
                    self.immutable.difference_update(objs)
            return subprocess.CompletedProcess(argv, 0, "", "")

        def chmod(p, mode):
            x4refguard._sandbox_check(p)
            self.calls.append(["chmod", oct(mode), str(p)])
            if self.honour:
                (self.ro.add if not mode & 0o222 else self.ro.discard)(str(p))
        monkeypatch.setattr(x4refguard, "_mutate_run", run)
        monkeypatch.setattr(x4refguard, "_mutate_chmod", chmod)


def test_FAKE_linux_as_root_uses_chattr_and_verifies(ref, monkeypatch):
    fs = _FakeFS(monkeypatch, "linux", 0, {"chattr", "lsattr"})
    assert x4refguard.main(["apply", "--yes"]) == 0
    assert fs.calls[0][1:] == ["-R", "+i", str(ref.resolve())]
    r = x4refguard.report()
    assert r["state"] == "protected" and r["mechanism"] == "chattr +i"
    assert x4refguard.main(["remove", "--yes"]) == 0
    assert fs.calls[-1][1:] == ["-R", "-i", str(ref.resolve())]


def test_FAKE_linux_unprivileged_falls_back_to_chmod_on_dirs_AND_files(ref, monkeypatch):
    fs = _FakeFS(monkeypatch, "linux", 1000, {"chattr", "lsattr"})
    assert x4refguard.main(["apply", "--yes"]) == 0
    chmodded = {c[2] for c in fs.calls if c[0] == "chmod"}
    assert str(ref.resolve()) in chmodded                                   # a directory
    assert str(ref.resolve() / "libraries" / "wares.xml") in chmodded       # a file
    assert not any("chattr" in c[0] for c in fs.calls)
    assert x4refguard.report()["mechanism"].startswith("chmod")


def test_FAKE_linux_immutable_tree_cannot_be_lifted_unprivileged(ref, monkeypatch):
    _FakeFS(monkeypatch, "linux", 0, {"chattr", "lsattr"})
    assert x4refguard.main(["apply", "--yes"]) == 0
    monkeypatch.setattr(x4refguard, "_geteuid", lambda: 1000)
    assert x4refguard.main(["remove", "--yes"]) == 2        # names `sudo chattr -R -i`, changes nothing
    assert x4refguard.report()["state"] == "protected"


def test_FAKE_macos_uses_chflags_uchg(ref, monkeypatch):
    fs = _FakeFS(monkeypatch, "darwin", 501, {"chflags"})
    assert x4refguard.main(["apply", "--yes"]) == 0
    assert fs.calls[0][1:] == ["-R", "uchg", str(ref.resolve())]
    assert x4refguard.report()["mechanism"] == "chflags uchg"
    assert x4refguard.main(["remove", "--yes"]) == 0
    assert fs.calls[-1][1:] == ["-R", "nouchg", str(ref.resolve())]


def test_FAKE_macos_without_chflags_falls_back_to_chmod(ref, monkeypatch):
    fs = _FakeFS(monkeypatch, "darwin", 501, set())
    assert x4refguard.main(["apply", "--yes"]) == 0
    assert fs.calls and all(c[0] == "chmod" for c in fs.calls)


@pytest.mark.parametrize("plat,euid,tools", [("linux", 0, {"chattr", "lsattr"}),
                                             ("linux", 1000, set()),
                                             ("darwin", 501, {"chflags"})])
def test_FAKE_a_mechanism_that_does_not_take_is_exit_1_never_0(ref, monkeypatch, plat, euid, tools):
    fs = _FakeFS(monkeypatch, plat, euid, tools)
    fs.honour = False                                   # the tool "ran", the filesystem ignored it
    assert x4refguard.main(["apply", "--yes"]) == 1
    assert fs.calls
    assert x4refguard.report()["state"] == "absent"


def test_FAKE_a_partly_applied_tree_is_PARTIAL(ref, monkeypatch):
    fs = _FakeFS(monkeypatch, "linux", 1000, set())
    assert x4refguard.main(["apply", "--yes"]) == 0
    fs.ro.discard(str(ref.resolve() / "libraries" / "wares.xml"))
    r = x4refguard.report()
    assert r["state"] == "partial" and r["sample_ok"] == r["sampled"] - 1
    assert x4refguard.main(["status"]) == 1


# --- Plan 3 lane J (J9, CI run 37091872873): a PowerShell 7 parent must not break Get-Acl ---- #
# MEASURED 2026-10-03: windows-latest CI and a local pwsh 7 parent both export a PSModulePath
# whose first entries are PowerShell 7's own module folders. The Windows PowerShell 5.1 child
# x4refguard starts then tries to load PS7's Microsoft.PowerShell.Security and fails:
# "Get-Acl ... the module could not be loaded" -> every status/apply refused (exit 2). Codex
# runs PowerShell 7 on Windows, so a Codex session hits it too.
_PWSH7_MODULE_PATH = ";".join([
    os.path.join(os.environ.get("USERPROFILE", "C:/Users/x"), "Documents", "PowerShell", "Modules"),
    r"C:\Program Files\PowerShell\Modules",
    r"C:\Program Files\PowerShell\7\Modules",
    r"C:\Program Files\WindowsPowerShell\Modules",
    r"C:\Windows\system32\WindowsPowerShell\v1.0\Modules"])


@win
def test_J9_a_PowerShell_7_PSModulePath_in_the_parent_does_not_break_the_ACL_read(ref, tripwire, monkeypatch):
    monkeypatch.setenv("PSModulePath", _PWSH7_MODULE_PATH)
    r = x4refguard.report()
    assert r["state"] == "absent", r
    assert x4refguard.main(["status"]) == 1


def test_J9_TWIN_the_child_env_drops_PSModulePath_and_the_parents_is_untouched(monkeypatch):
    """Runs on every OS (no skip): the child gets a scrubbed COPY; the caller keeps its own."""
    monkeypatch.setenv("PSModulePath", _PWSH7_MODULE_PATH)
    monkeypatch.setenv("X4_J9_KEEP", "kept")
    env = x4refguard._ps51_env()
    assert not [k for k in env if k.upper() == "PSMODULEPATH"], sorted(env)
    assert env["X4_J9_KEEP"] == "kept"                  # everything else is passed through
    assert os.environ["PSModulePath"] == _PWSH7_MODULE_PATH


# --------------------------------------------- B3 (install red-team 2026-10-04): ask first
#
# An apply ran >2 minutes on a 60 GB tree with no output and no question. Now: the target
# root and an object count come FIRST, then a confirmation; --yes answers it; with neither a
# terminal nor --yes the command refuses with exit 2 and changes nothing.

def _b3_platform(monkeypatch):
    """The POSIX chmod fallback, whatever the host OS: the confirmation sits in front of BOTH
    mechanisms, and this one runs everywhere without an ACL."""
    monkeypatch.setattr(x4refguard, "_platform", lambda: "linux")
    monkeypatch.setattr(x4refguard, "_geteuid", lambda: 1000)
    monkeypatch.setattr(x4refguard, "_owner_is_user", lambda p: True)
    monkeypatch.setattr(x4refguard, "_which", lambda n: None)


def test_B3_apply_without_yes_and_without_a_terminal_REFUSES_and_changes_nothing(
        ref, monkeypatch, capsys):
    _b3_platform(monkeypatch)
    monkeypatch.setattr(x4refguard, "_isatty", lambda: False)
    calls = []
    monkeypatch.setattr(x4refguard, "_mutate_chmod", lambda p, m: calls.append(p))
    assert x4refguard.main(["apply"]) == 2
    err = capsys.readouterr().err
    assert not calls, "it changed %d object(s) without confirmation" % len(calls)
    assert str(ref.resolve()) in err and "--yes" in err, err
    assert "4 object(s)" in err, "no object count before the question: " + err   # root, libraries, wares.xml, sentinel


def test_B3_the_count_comes_BEFORE_the_question(ref, monkeypatch, capsys):
    _b3_platform(monkeypatch)
    monkeypatch.setattr(x4refguard, "_isatty", lambda: True)
    seen = {}

    def ask(prompt):
        seen["err_so_far"] = capsys.readouterr().err
        return "n"
    monkeypatch.setattr(x4refguard, "_ask", ask)
    monkeypatch.setattr(x4refguard, "_mutate_chmod", lambda p, m: pytest.fail("changed after a NO"))
    assert x4refguard.main(["apply"]) == 2
    assert "target: %s" % ref.resolve() in seen["err_so_far"] and "4 object(s)" in seen["err_so_far"]


def test_B3_an_interactive_YES_proceeds(ref, monkeypatch):
    _b3_platform(monkeypatch)
    monkeypatch.setattr(x4refguard, "_isatty", lambda: True)
    monkeypatch.setattr(x4refguard, "_ask", lambda prompt: "y")
    calls = []
    monkeypatch.setattr(x4refguard, "_mutate_chmod", lambda p, m: calls.append(p))
    x4refguard.main(["apply"])          # verification then fails (nothing really chmodded)
    assert len(calls) == 4, calls


def test_B3_yes_flag_proceeds_without_asking(ref, monkeypatch):
    _b3_platform(monkeypatch)
    monkeypatch.setattr(x4refguard, "_isatty", lambda: True)
    monkeypatch.setattr(x4refguard, "_ask", lambda prompt: pytest.fail("asked despite --yes"))
    calls = []
    monkeypatch.setattr(x4refguard, "_mutate_chmod", lambda p, m: calls.append(p))
    x4refguard.main(["apply", "--yes"])
    assert len(calls) == 4, calls


def test_B3_remove_also_asks(ref, monkeypatch, capsys):
    _b3_platform(monkeypatch)
    monkeypatch.setattr(x4refguard, "_posix_mark", lambda root: "readonly")
    monkeypatch.setattr(x4refguard, "_isatty", lambda: False)
    monkeypatch.setattr(x4refguard, "_mutate_chmod", lambda p, m: pytest.fail("changed unasked"))
    assert x4refguard.main(["remove"]) == 2
    assert "--yes" in capsys.readouterr().err


def test_B3_progress_lines_while_counting(ref, monkeypatch, capsys):
    monkeypatch.setattr(x4refguard, "PROGRESS_EVERY", 2)
    assert x4refguard._count(ref) == 4
    assert capsys.readouterr().err.count("counting:") == 2


def test_B3_a_long_OS_call_has_a_heartbeat(monkeypatch, capsys):
    import time
    monkeypatch.setattr(x4refguard, "HEARTBEAT_S", 0.05)
    assert x4refguard._with_heartbeat("applying", lambda: time.sleep(0.3) or 7) == 7
    assert capsys.readouterr().err.count("still applying") >= 2


def test_C6_unconfigured_says_how_to_fix(monkeypatch, capsys):
    monkeypatch.setattr(x4refguard, "_configured_root", lambda: None)
    x4refguard.main(["status", "--json"])
    detail = json.loads(capsys.readouterr().out)["detail"]
    assert "unpack-reference.sh" in detail and "X4_REFERENCE" in detail, detail


def test_C6_a_missing_configured_root_says_how_to_fix(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(x4refguard, "_configured_root", lambda: tmp_path / "nope")
    x4refguard.main(["status", "--json"])
    detail = json.loads(capsys.readouterr().out)["detail"]
    assert "unpack-reference.sh" in detail and "X4_REFERENCE" in detail, detail


# ------------------------- R2 (second install red-team, 2026-10-04): messages a human acts on

def test_R2a_the_no_sentinel_refusal_names_it_what_it_means_and_both_fixes(ref, monkeypatch, capsys):
    (ref / SENTINEL).unlink()
    assert x4refguard.main(["apply", "--yes"]) == 2
    err = capsys.readouterr().err
    assert SENTINEL in err and str(ref.resolve()) in err, err
    assert "bin/unpack-reference.sh" in err, err                    # fix 1: unpack with the toolkit
    # its PowerShell form, PLATFORM-CORRECT (CI3: this pinned `bash.exe"`, which only Windows
    # prints; pwsh on Linux runs bash by its own path, `& "/usr/bin/bash"`, and that is right).
    want = '& "%s" bin/unpack-reference.sh' % x4refguard._bash_for_humans()
    assert want in err, err
    if os.name == "nt":
        assert 'bash.exe" bin/unpack-reference.sh' in err, err
    else:
        assert ".exe" not in want and want.startswith('& "/'), want
    # the PowerShell mark line uses THIS platform's separator (a backslash only on Windows)
    assert "%s%s%s" % (str(ref.resolve()), os.sep, SENTINEL) in err, err
    assert "printf" in err and "Set-Content" in err, err             # fix 2: mark a hand-made tree
    assert "To lift it" not in err, "the lift block answers a question nobody asked here: " + err


def test_R2a_the_printed_MARK_command_really_satisfies_apply(ref, monkeypatch, capsys):
    """README's pre-4.0 / hand-unpacked advice must WORK, not just be printed: run the printed
    Git Bash line, then the sentinel clause passes."""
    import re
    import subprocess
    sys.path.insert(0, str(REPO / "scripts"))
    import gitbash
    bash = gitbash.find_bash()
    if not bash:
        pytest.skip("no Git Bash to run the printed command with")
    (ref / SENTINEL).unlink()
    x4refguard.main(["apply", "--yes"])
    err = capsys.readouterr().err
    line = next(l.split(":", 1)[1].strip() for l in err.splitlines()
                if "printf" in l and l.strip().startswith("Git Bash"))
    r = subprocess.run([bash, "-c", line], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (ref / SENTINEL).is_file()
    assert x4refguard.resolve_target(None, action="apply") == ref.resolve()
    assert re.search(r"build id", (ref / SENTINEL).read_text(encoding="utf-8"))


def test_R2c_status_names_the_folder_it_checked(ref, monkeypatch, capsys):
    _b3_platform(monkeypatch)
    x4refguard.main(["status"])
    out = capsys.readouterr().out
    assert "folder: %s" % ref.resolve() in out, out


def test_R2_status_ABSENT_reads_not_applied_and_names_apply_not_the_lift_block(ref, monkeypatch,
                                                                               capsys):
    _b3_platform(monkeypatch)
    assert x4refguard.main(["status"]) == 1
    out = capsys.readouterr().out
    assert "not applied" in out and 'x4refguard.py" apply' in out, out
    assert "To lift it" not in out, out


def test_R2_TWIN_status_PARTIAL_still_prints_the_lift_block(ref, monkeypatch, capsys):
    _b3_platform(monkeypatch)
    monkeypatch.setattr(x4refguard, "report",
                        lambda full=False, path=None: x4refguard._blank("partial", ref, "cut"))
    x4refguard.main(["status"])
    assert "To lift it" in capsys.readouterr().out


@win
def test_R2d_apply_and_status_report_the_SAME_sample_count(protected_cleanup, tripwire, capsys):
    import re
    assert x4refguard.main(["apply", "--yes"]) == 0
    a = capsys.readouterr().out
    assert x4refguard.main(["status"]) == 0
    s = capsys.readouterr().out
    pa = re.findall(r"(\d+) of (\d+) sampled", a)
    ps = re.findall(r"(\d+) of (\d+) sampled", s)
    assert pa and ps and set(pa) == set(ps), (a, s)
