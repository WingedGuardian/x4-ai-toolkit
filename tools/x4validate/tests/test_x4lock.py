r"""`x4lock` must actually stop a write, and must never claim a lock it did not verify.

WHY. Four irreplaceable files were destroyed here in twelve days by writes from inside
other processes -- where a command-string hook cannot see them. The read-only attribute
is the only layer that can. This pins what it does and, just as importantly, what it
does NOT do, so the docstring's table cannot quietly drift from the behaviour.

THE CONTROL IS THE POINT. Every "blocked" row is paired with the same primitive run
against an UNLOCKED file. Without that pairing a broken probe reads as a working lock:
measured 2026-09-04, the `bash` on Windows PATH is the WSL stub, it cannot open a
`C:/...` path, and its failure was recorded as `rm -f` being blocked. It is not.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("x4lock", REPO / "scripts" / "x4lock.py")
x4lock = importlib.util.module_from_spec(_spec)
sys.modules["x4lock"] = x4lock
_spec.loader.exec_module(x4lock)


def _fresh(p: Path) -> Path:
    p.write_text("IRREPLACEABLE\n", encoding="utf-8")
    return p


def _intact(p: Path) -> bool:
    return p.is_file() and p.read_text(encoding="utf-8") == "IRREPLACEABLE\n"


#: Primitives the attribute is expected to stop. Deliberately the ones that have
#: actually destroyed a file in this workspace: python text writes and a copy.
BLOCKED = {
    "open_w": lambda p, tmp: open(p, "w", encoding="utf-8").write("x"),
    "write_text": lambda p, tmp: p.write_text("x", encoding="utf-8"),
    "write_bytes": lambda p, tmp: p.write_bytes(b"x"),
    "truncate": lambda p, tmp: os.truncate(p, 0),
    "os_replace": lambda p, tmp: os.replace(tmp, p),
    "copy2": lambda p, tmp: shutil.copy2(tmp, p),
    "os_remove": lambda p, tmp: os.remove(p),
}


#: Primitives the attribute stops ONLY on Windows. On POSIX, delete and rename-over
#: are authorised by write permission on the DIRECTORY, so the read-only bit is not
#: consulted at all. MEASURED on CI (ubuntu, run 33994180317): both DID NOT RAISE.
#: The docstring table in x4lock.py carries the same two rows marked WINDOWS ONLY.
WINDOWS_ONLY = {"os_remove", "os_replace"}


@pytest.mark.parametrize("name", sorted(BLOCKED))
def test_a_locked_file_survives(name, tmp_path):
    p = _fresh(tmp_path / (name + ".md"))
    src = tmp_path / (name + ".src")
    src.write_text("x", encoding="utf-8")
    ok, msg = x4lock._apply(p, True)
    assert ok, "the lock step itself failed: " + msg
    if os.name != "nt" and name in WINDOWS_ONLY:
        # PIN the weaker guarantee rather than skip it: if POSIX ever starts refusing
        # these, the table's WINDOWS ONLY marks are wrong and must be revisited. A skip
        # here would let the docstring drift from the behaviour in silence. (And no
        # bare `return`: the suite's own guard reads that as an un-named pass.)
        BLOCKED[name](p, str(src))            # must NOT raise on POSIX
        assert not _intact(p), (
            name + " was blocked on POSIX -- x4lock's table says it is Windows-only; "
            "re-measure and update the table rather than leaving it weaker than reality")
    else:
        with pytest.raises(OSError):
            BLOCKED[name](p, str(src))
        assert _intact(p), "a locked file was modified by " + name


@pytest.mark.parametrize("name", sorted(BLOCKED))
def test_the_control_proves_the_primitive_works_unlocked(name, tmp_path):
    """Without this, a probe that is simply broken reads as a working lock."""
    p = _fresh(tmp_path / (name + ".md"))
    src = tmp_path / (name + ".src")
    src.write_text("x", encoding="utf-8")
    BLOCKED[name](p, str(src))
    assert not _intact(p), name + " had no effect even UNLOCKED -- the probe is broken"


def test_unlock_restores_writability(tmp_path):
    """A lock that cannot be lifted is its own kind of damage."""
    p = _fresh(tmp_path / "a.md")
    assert x4lock._apply(p, True)[0]
    assert x4lock.is_locked(p)
    with pytest.raises(OSError):
        p.write_text("x", encoding="utf-8")
    assert x4lock._apply(p, False)[0]
    assert not x4lock.is_locked(p)
    p.write_text("y", encoding="utf-8")          # must not raise
    assert p.read_text(encoding="utf-8") == "y"


def test_state_reports_missing_rather_than_guessing(tmp_path):
    assert x4lock.state(tmp_path / "nope.md") == "missing"


def test_apply_verifies_and_refuses_to_claim_an_unconfirmed_lock(tmp_path, monkeypatch):
    """`chmod` can succeed on a filesystem that ignores the attribute. Reporting a
    lock that is not there is the worst outcome, so _apply re-reads the state."""
    p = _fresh(tmp_path / "a.md")
    monkeypatch.setattr(x4lock.Path, "chmod", lambda self, mode: None)  # a no-op chmod
    ok, msg = x4lock._apply(p, True)
    assert not ok and "verification" in msg, (
        "a chmod that did nothing was reported as a successful lock")


def test_cfg_refuses_a_name_that_does_not_exist():
    """The first draft used getattr(..., lambda: None) and silently built a two-file
    manifest from two typo'd names, reporting it as complete."""
    with pytest.raises(AttributeError, match="does not exist"):
        x4lock._cfg("definitely_not_a_paths_function")


def test_the_manifest_never_contains_a_directory_or_a_missing_path(tmp_path):
    for p in x4lock.manifest():
        assert p.is_file(), "manifest contains a non-file: " + str(p)


def test_extra_protected_paths_are_added_from_the_environment(tmp_path, monkeypatch):
    extra = _fresh(tmp_path / "site-specific.md")
    monkeypatch.setenv("X4_PROTECTED", str(extra))
    got = {str(p.resolve()).lower() for p in x4lock.manifest()}
    assert str(extra.resolve()).lower() in got


@pytest.mark.skipif(os.name != "nt", reason="the -Force gap is Windows-specific")
def test_the_KNOWN_GAP_is_still_the_gap(tmp_path):
    """Pins the documented limit. If PowerShell ever stops clearing the attribute this
    goes red, and the docstring's table -- and the layered defence built on it -- must
    be revisited rather than silently becoming stronger than claimed."""
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh not available; the gap cannot be measured here")
    p = _fresh(tmp_path / "gap.md")
    assert x4lock._apply(p, True)[0]
    r = subprocess.run(["pwsh", "-NoProfile", "-Command",
                        "Remove-Item -Force '%s'" % p], capture_output=True)
    assert r.returncode == 0 and not p.exists(), (
        "pwsh -Force no longer defeats the read-only attribute -- update x4lock's "
        "documented table and the guard layering that assumes this gap")


def test_a_sandbox_copy_of_a_LOCKED_file_is_writable(tmp_path):
    r"""A gate copies the user's registry into a throwaway sandbox and writes to it.

    `shutil.copy2` preserves permission bits, so once the real registry was locked for
    the first time the SANDBOX copy was read-only too and `registry_provenance` failed
    on its own scratch file -- protecting nothing and looking exactly like the tool
    being broken. MEASURED 2026-09-04, within minutes of the first lock.

    `_env.sandbox_copy` is the fix, and this pins it: a sandbox exists to be written,
    and the protection belongs to the original.
    """
    import shutil
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gates"))
    import _env

    src = _fresh(tmp_path / "protected.yaml")
    assert x4lock._apply(src, True)[0]

    naive = tmp_path / "naive.yaml"
    shutil.copy2(src, naive)
    assert x4lock.is_locked(naive), (
        "precondition: copy2 is supposed to carry the read-only bit; if it no longer "
        "does, this whole failure mode is gone and the helper may be unnecessary")

    good = _env.sandbox_copy(src, tmp_path / "sandbox" / "copy.yaml")
    assert not x4lock.is_locked(good), "sandbox_copy produced a read-only sandbox"
    good.write_text("written", encoding="utf-8")          # must not raise
    assert good.read_text(encoding="utf-8") == "written"
    assert x4lock.is_locked(src), "the ORIGINAL must still be protected"


def test_a_DELETED_protected_file_is_NAMED_not_dropped(tmp_path, monkeypatch):
    """`manifest()` filtered out anything that is not a file, so deleting a locked
    file simply made the manifest smaller and `status` printed a clean total.

    MEASURED 2026-09-04: 3 protected -> delete one -> "2 protected file(s): 2
    locked", exit 0. That matters more than it looks, because this module's own
    table names `rm -f` and `Remove-Item -Force` as the primitives the read-only
    bit does NOT stop -- deletion is the documented residual risk, and it was
    rendering as a clean sweep.
    """
    a = _fresh(tmp_path / "a.md")
    b = _fresh(tmp_path / "b.md")
    monkeypatch.setenv("X4_PROTECTED", os.pathsep.join([str(a), str(b)]))
    assert not [p for p in x4lock.missing() if p.name in ("a.md", "b.md")]
    b.unlink()
    gone = [p.name for p in x4lock.missing()]
    assert "b.md" in gone, "a deleted protected file must be NAMED, not dropped"
    assert "b.md" not in [p.name for p in x4lock.manifest()], (
        "manifest() still returns only lockable files -- absences belong to missing()")


def test_an_unimportable_paths_module_REFUSES(monkeypatch):
    """The ImportError fallback left `_paths = None`, and the manifest then held only
    this checkout's own x4-paths.env -- 1 of 26 on this machine -- reported as a
    complete, healthy manifest with exit 0. A partial manifest presented as whole is
    the narrowing-step-that-reports-success shape this tool exists to stop."""
    monkeypatch.setattr(x4lock, "_paths", None)
    with pytest.raises(x4lock.Unresolvable):
        x4lock.manifest()
    assert x4lock.main(["status"]) == 2


# --- F119: a LINKED git worktree has no checkout-local config, by design ------------- #
#
# `.claude/x4-paths.env` is gitignored per-machine config written by the installer, so a
# linked worktree never carries one -- and CLAUDE.md MANDATES a worktree per concurrent
# session. Demanding it there made every compliant session see a red `x4lock status`.
# The main checkout must still report a deleted one, a SUBMODULE (whose `.git` is also a
# file) is not a worktree, and the waiver must never cost the configured toolkit's copy.

def _checkout(tmp_path, kind: str) -> Path:
    """A checkout root shaped the way git lays one out.

    main       `.git` is a DIRECTORY
    worktree   `.git` is a FILE whose gitdir is `<main>/.git/worktrees/<name>`, an admin
               directory holding the `commondir` file git writes there (`../..`, MEASURED
               in this repository's own worktrees)
    submodule  `.git` is a FILE whose gitdir is `<super>/.git/modules/<name>`, which has
               NO `commondir`
    """
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    if kind == "main":
        (root / ".git").mkdir()
    elif kind == "worktree":
        admin = tmp_path / "main" / ".git" / "worktrees" / "checkout"
        admin.mkdir(parents=True)
        (admin / "commondir").write_text("../..\n", encoding="utf-8")
        (root / ".git").write_text(f"gitdir: {admin.as_posix()}\n", encoding="utf-8")
    elif kind == "submodule":
        admin = tmp_path / "super" / ".git" / "modules" / "checkout"
        admin.mkdir(parents=True)
        (root / ".git").write_text(f"gitdir: {admin.as_posix()}\n", encoding="utf-8")
    else:
        raise AssertionError(kind)
    return root


def _local_env(root: Path) -> Path:
    """The config a checkout is demanded to carry: the 4.x root file (Plan 3 lane I)."""
    return root / "x4-paths.env"


def _named(paths, target: Path) -> bool:
    want = str(target.resolve()).lower()
    return any(str(p.resolve()).lower() == want for p in paths)


def test_a_linked_WORKTREE_does_not_demand_its_own_x4_paths_env(tmp_path, monkeypatch, capsys):
    root = _checkout(tmp_path, "worktree")
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    monkeypatch.delenv("X4_TOOLKIT", raising=False)
    assert not _named(x4lock.missing(), _local_env(root)), (
        "a linked worktree never has its own x4-paths.env; demanding it is F119")
    monkeypatch.setenv("X4_PROTECTED", str(_fresh(tmp_path / "protected.md")))
    x4lock.main(["status"])
    out = capsys.readouterr()
    assert "linked git worktree" in out.out, "the waiver must be ANNOUNCED, never silent"
    assert str(_local_env(root)) not in out.err


def test_the_MAIN_checkout_still_reports_a_deleted_x4_paths_env(tmp_path, monkeypatch, capsys):
    """The twin: without it, dropping the candidate for EVERY checkout would pass the
    test above and lose the only report of a deleted config in the main checkout."""
    root = _checkout(tmp_path, "main")
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    assert _named(x4lock.missing(), _local_env(root))
    monkeypatch.setenv("X4_PROTECTED", str(_fresh(tmp_path / "protected.md")))
    x4lock.main(["status"])
    assert "linked git worktree" not in capsys.readouterr().out


def test_a_SUBMODULE_is_not_a_worktree_and_still_demands_its_env(tmp_path, monkeypatch):
    """A submodule's `.git` is a FILE too. Treating every `.git` file as a worktree
    would waive the config of a toolkit vendored as a submodule."""
    root = _checkout(tmp_path, "submodule")
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    assert _named(x4lock.missing(), _local_env(root))


def test_a_worktree_env_file_that_DOES_exist_is_still_protected(tmp_path, monkeypatch):
    """The waiver covers ABSENCE only. A worktree that does carry a config keeps it in
    the manifest, where it gets locked like any other."""
    root = _checkout(tmp_path, "worktree")
    env = _local_env(root)
    env.parent.mkdir(parents=True, exist_ok=True)
    _fresh(env)
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    assert _named(x4lock.manifest(), env)


def test_a_worktree_still_demands_the_CONFIGURED_toolkits_env(tmp_path, monkeypatch):
    """When the worktree waiver applies, the config that matters is the one $X4_TOOLKIT
    names. `_paths._find_env_file` returns only a file that EXISTS, so a deleted one is
    simply not found, and without an explicit candidate that deletion would be reported
    by nobody."""
    root = _checkout(tmp_path, "worktree")
    toolkit = tmp_path / "toolkit"
    (toolkit / ".claude").mkdir(parents=True)
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    monkeypatch.setenv("X4_TOOLKIT", str(toolkit))
    assert _named(x4lock.missing(), toolkit / "x4-paths.env")
    assert not _named(x4lock.missing(), _local_env(root))


def test_an_UNRECOGNISED_git_file_fails_closed(tmp_path, monkeypatch):
    """Anything that cannot be proven a linked worktree is treated as a checkout that
    should have its config -- a false MISSING is visible, a false waiver is not."""
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    (root / ".git").write_text("not a gitdir line\n", encoding="utf-8")
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    assert _named(x4lock.missing(), _local_env(root))


def test_an_UNREADABLE_git_file_fails_closed(tmp_path, monkeypatch):
    """The twin for the OSError branch: a `.git` file that raises on read."""
    root = _checkout(tmp_path, "worktree")
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    real = x4lock.Path.read_text

    def boom(self, *a, **k):
        if self.name == ".git":
            raise OSError("simulated unreadable .git")
        return real(self, *a, **k)

    monkeypatch.setattr(x4lock.Path, "read_text", boom)
    assert _named(x4lock.missing(), _local_env(root))


def test_a_worktree_demands_the_MAIN_checkouts_env_even_without_X4_TOOLKIT(tmp_path, monkeypatch):
    """Review finding: the waiver was free only when X4_TOOLKIT was set. git's own
    metadata names the main checkout (`<admin>/commondir` -> `<main>/.git`), so that
    checkout's config is demanded in every case."""
    root = _checkout(tmp_path, "worktree")
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    monkeypatch.delenv("X4_TOOLKIT", raising=False)
    assert _named(x4lock.missing(), tmp_path / "main" / "x4-paths.env")
    assert not _named(x4lock.missing(), _local_env(root))


def test_a_SUBMODULE_under_a_folder_named_worktrees_is_not_waived(tmp_path, monkeypatch):
    """`git submodule add <url> vendor/worktrees/toolkit` gives a gitdir ending
    `.../modules/vendor/worktrees/toolkit`, whose second-to-last part IS `worktrees`.
    Only a `commondir` file marks a linked worktree's admin directory."""
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    admin = tmp_path / "super" / ".git" / "modules" / "vendor" / "worktrees" / "toolkit"
    admin.mkdir(parents=True)
    (root / ".git").write_text(f"gitdir: {admin.as_posix()}\n", encoding="utf-8")
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    assert _named(x4lock.missing(), _local_env(root))


def test_a_RELATIVE_gitdir_is_resolved_against_the_checkout(tmp_path, monkeypatch):
    root = _checkout(tmp_path, "worktree")
    (root / ".git").write_text("gitdir: ../main/.git/worktrees/checkout\n", encoding="utf-8")
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    monkeypatch.delenv("X4_TOOLKIT", raising=False)
    assert not _named(x4lock.missing(), _local_env(root))
    assert _named(x4lock.missing(), tmp_path / "main" / "x4-paths.env")


def test_the_note_NAMES_the_copy_checked_instead(tmp_path, monkeypatch, capsys):
    root = _checkout(tmp_path, "worktree")
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    monkeypatch.delenv("X4_TOOLKIT", raising=False)
    monkeypatch.setenv("X4_PROTECTED", str(_fresh(tmp_path / "protected.md")))
    x4lock.main(["status"])
    out = capsys.readouterr().out
    want = str((tmp_path / "main" / "x4-paths.env").resolve())
    assert want in out, out


def test_X4_TOOLKIT_pointing_at_the_worktree_itself_still_reports_its_config(tmp_path, monkeypatch, capsys):
    """Hooks would find no config at all here, so rc 1 is right -- and the note must not
    claim a different copy is being checked."""
    root = _checkout(tmp_path, "worktree")
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    monkeypatch.setenv("X4_TOOLKIT", str(root))
    assert _named(x4lock.missing(), _local_env(root))
    monkeypatch.setenv("X4_PROTECTED", str(_fresh(tmp_path / "protected.md")))
    x4lock.main(["status"])
    assert "points at this worktree" in capsys.readouterr().out


def test_the_note_names_each_copy_ONCE(tmp_path, monkeypatch, capsys):
    """When $X4_TOOLKIT IS the main checkout, both routes reach one file: name it once,
    and do not claim X4_TOOLKIT points at this worktree -- it does not."""
    root = _checkout(tmp_path, "worktree")
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    monkeypatch.setenv("X4_TOOLKIT", str(tmp_path / "main"))
    monkeypatch.setenv("X4_PROTECTED", str(_fresh(tmp_path / "protected.md")))
    x4lock.main(["status"])
    out = capsys.readouterr().out
    want = str((tmp_path / "main" / "x4-paths.env").resolve())
    assert out.count(want) == 1, out
    assert "points at this worktree" not in out, out


def _worktree_with_commondir(tmp_path, commondir: bytes, common: Path | None = None) -> Path:
    """A linked worktree whose admin directory holds exactly the given `commondir` bytes."""
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    admin = (common or (tmp_path / "main" / ".git")) / "worktrees" / "checkout"
    admin.mkdir(parents=True)
    (admin / "commondir").write_bytes(commondir)
    (root / ".git").write_text(f"gitdir: {admin.as_posix()}\n", encoding="utf-8")
    return root


def test_no_waiver_when_NOTHING_can_stand_in_for_the_config(tmp_path, monkeypatch, capsys):
    """Re-review finding: a linked worktree of a SUBMODULE (or a --separate-git-dir repo)
    has a common dir not named `.git`, so no main checkout is derived. With X4_TOOLKIT
    unset the waiver then replaced the config with NOTHING and still exited 0. It must fail
    closed: no replacement, no waiver."""
    common = tmp_path / "super" / ".git" / "modules" / "sub"
    root = _worktree_with_commondir(tmp_path, b"../..\n", common=common)
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    monkeypatch.delenv("X4_TOOLKIT", raising=False)
    assert _named(x4lock.missing(), _local_env(root))
    monkeypatch.setenv("X4_PROTECTED", str(_fresh(tmp_path / "protected.md")))
    x4lock.main(["status"])
    assert "linked git worktree" not in capsys.readouterr().out


def test_a_CRLF_commondir_is_read(tmp_path, monkeypatch):
    """Git for Windows or a hand edit can leave a CR on the line; git itself trims it."""
    root = _worktree_with_commondir(tmp_path, b"../..\r\n")
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    monkeypatch.delenv("X4_TOOLKIT", raising=False)
    assert not _named(x4lock.missing(), _local_env(root))
    assert _named(x4lock.missing(), tmp_path / "main" / "x4-paths.env")


def test_an_ABSOLUTE_commondir_is_used_as_is(tmp_path, monkeypatch):
    main_git = tmp_path / "main" / ".git"
    root = _worktree_with_commondir(tmp_path, (main_git.as_posix() + "\n").encode("utf-8"))
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    monkeypatch.delenv("X4_TOOLKIT", raising=False)
    assert _named(x4lock.missing(), tmp_path / "main" / "x4-paths.env")


def test_a_GARBLED_commondir_fails_closed_without_raising(tmp_path, monkeypatch):
    """Non-UTF-8 bytes raise UnicodeDecodeError, a ValueError -- not an OSError. Uncaught,
    `status` crashed with a traceback instead of reporting."""
    root = _worktree_with_commondir(tmp_path, b"\xff\xfe\x00\x81")
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    assert _named(x4lock.missing(), _local_env(root))


# --- F9: AGENTS.md and the Codex / .agents trees -------------------------------------- #
#
# `_GAME_RELATIVE` entries are DEMANDED unconditionally, so adding AGENTS.md there would
# report every Claude-only root (and every v3.x install) as damaged. 0 of 23 tagged
# releases ever shipped AGENTS.md (MEASURED 2026-10-02). Each agent's files are demanded
# only when that agent's own directory marks it as installed.

def _game(tmp_path, monkeypatch, claude=True):
    game = tmp_path / "game"
    game.mkdir(parents=True, exist_ok=True)
    if claude:
        (game / ".claude" / "hooks").mkdir(parents=True)
        (game / "CLAUDE.md").write_text("c\n", encoding="utf-8")
    real = x4lock._cfg
    monkeypatch.setattr(x4lock, "_cfg", lambda n: game if n == "game_root" else real(n))
    return game


def _in(paths, game: Path, rel: str) -> bool:
    want = str((game / rel).resolve()).lower()
    return any(str(p.resolve()).lower() == want for p in paths)


def test_F9_a_present_AGENTS_md_is_in_the_manifest(tmp_path, monkeypatch):
    game = _game(tmp_path, monkeypatch)
    (game / "AGENTS.md").write_text("a\n", encoding="utf-8")
    assert _in(x4lock.manifest(), game, "AGENTS.md")


def test_generic_carrier_demands_agents_and_locks_the_neutral_runtime(tmp_path, monkeypatch):
    game = _game(tmp_path, monkeypatch, claude=False)
    (game / ".agents/skills").mkdir(parents=True)
    runtime = game / "tools/x4_harness"
    runtime.mkdir(parents=True)
    for name in ("x4guard.py", "_x4-env.sh"):
        (runtime / name).write_text("fixture")
    core = game / "tools/modding_harness"
    core.mkdir()
    (core / "dispatch.py").write_text("fixture")
    (core / "opencode_plugin.mjs").write_text("fixture")
    (game / "tools/modding-harness.lock.json").write_text("{}")
    missing = x4lock.missing()
    assert _in(missing, game, "AGENTS.md") and not _in(missing, game, "CLAUDE.md")
    held = x4lock.manifest()
    assert all(_in(held, game, rel) for rel in (
        "tools/x4_harness/x4guard.py", "tools/x4_harness/_x4-env.sh",
        "tools/modding_harness/dispatch.py", "tools/modding_harness/opencode_plugin.mjs",
        "tools/modding-harness.lock.json"))


def test_neutral_runtime_does_not_replace_a_named_native_target(tmp_path, monkeypatch):
    game = _game(tmp_path, monkeypatch, claude=False)
    (game / ".agents").mkdir()
    (game / "tools/x4_harness").mkdir(parents=True)
    (game / ".codex/hooks").mkdir(parents=True)
    assert x4lock._markers_in(game) == [".codex/hooks"]
    (game / ".codex/hooks").rmdir()
    (game / ".codex/hooks.json").write_text("{}")
    assert x4lock._markers_in(game) == [".codex/hooks"]


@pytest.mark.parametrize("toolkit_hook", [False, True])
def test_neutral_carrier_keeps_remaining_claude_toolkit_definition(tmp_path, monkeypatch, toolkit_hook):
    import json
    game = _game(tmp_path, monkeypatch, claude=False)
    (game / ".agents").mkdir()
    (game / "tools/x4_harness").mkdir(parents=True)
    (game / ".claude").mkdir()
    content = {"hooks": {"PreToolUse": [{"hooks": [{"command": "bash .claude/hooks/protect-bash.sh"}]}]}} if toolkit_hook else {"env": {"NOTES": "protect-bash.sh"}}
    (game / ".claude/settings.json").write_text(json.dumps(content))
    markers = x4lock._markers_in(game)
    assert markers == ([".claude/hooks"] if toolkit_hook else ["tools/x4_harness"])
    assert _in(x4lock.missing(), game, "CLAUDE.md") == toolkit_hook


def test_F9_TWIN_a_claude_only_root_does_NOT_report_AGENTS_md_missing(tmp_path, monkeypatch):
    """The trap: _GAME_RELATIVE entries are demanded unconditionally. Every v3.x and
    Claude-only install would read as damaged."""
    game = _game(tmp_path, monkeypatch)
    gone = x4lock.missing()
    assert not _in(gone, game, "AGENTS.md")
    assert not _in(gone, game, ".codex/hooks.json")


def test_F9_a_codex_root_DEMANDS_AGENTS_md_and_hooks_json(tmp_path, monkeypatch):
    game = _game(tmp_path, monkeypatch)
    (game / ".codex" / "hooks").mkdir(parents=True)
    gone = x4lock.missing()
    assert _in(gone, game, "AGENTS.md")
    assert _in(gone, game, ".codex/hooks.json")


def test_F9_TWIN_a_users_OWN_codex_config_dir_demands_nothing(tmp_path, monkeypatch):
    """Codex itself reads a project `.codex/config.toml`, so a bare `.codex/` is not
    proof the toolkit's Codex target is installed; its guard copy is."""
    game = _game(tmp_path, monkeypatch)
    (game / ".codex").mkdir()
    (game / ".codex" / "config.toml").write_text("#\n", encoding="utf-8")
    assert not _in(x4lock.missing(), game, "AGENTS.md")


def test_F9_codex_rules_hooks_and_guards_are_locked_when_present(tmp_path, monkeypatch):
    game = _game(tmp_path, monkeypatch)
    (game / ".codex" / "rules").mkdir(parents=True)
    (game / ".codex" / "hooks").mkdir(parents=True)
    for rel in (".codex/hooks.json", ".codex/rules/x4.rules", ".codex/hooks/codex-entry.ps1",
                ".codex/hooks/codex-entry.sh", ".codex/hooks/x4guard.py"):
        (game / rel).write_text("#\n", encoding="utf-8")
    got = x4lock.manifest()
    for rel in (".codex/hooks.json", ".codex/rules/x4.rules", ".codex/hooks/codex-entry.ps1",
                ".codex/hooks/codex-entry.sh", ".codex/hooks/x4guard.py"):
        assert _in(got, game, rel), rel


def test_F9_agents_skills_are_locked_like_claude_skills(tmp_path, monkeypatch):
    """User decision #11 (2026-10-02): the rule must not differ per agent."""
    game = _game(tmp_path, monkeypatch)
    sk = game / ".agents" / "skills" / "x4-cli-reference"
    (sk / "reference").mkdir(parents=True)
    (sk / "SKILL.md").write_text("s\n", encoding="utf-8")
    (sk / "reference" / "x4save.md").write_text("r\n", encoding="utf-8")
    got = x4lock.manifest()
    assert _in(got, game, ".agents/skills/x4-cli-reference/SKILL.md")
    assert _in(got, game, ".agents/skills/x4-cli-reference/reference/x4save.md")


def test_F9_a_CODEX_ONLY_root_does_not_report_CLAUDE_md_missing(tmp_path, monkeypatch):
    """`install --agent codex` installs no CLAUDE.md and no .claude/. Demanding them there
    is the same false-MISSING as demanding AGENTS.md on a Claude-only root."""
    game = _game(tmp_path, monkeypatch, claude=False)
    (game / ".codex" / "hooks").mkdir(parents=True)
    # a 3.x install wrote its path config to .claude/x4-paths.env, so a Codex-only
    # root HAS a .claude/ directory -- that alone must not mean "Claude installed"
    (game / ".claude").mkdir()
    (game / ".claude" / "x4-paths.env").write_text("X4_TOOLKIT=x\n", encoding="utf-8")
    gone = x4lock.missing()
    assert not _in(gone, game, "CLAUDE.md")
    assert not _in(gone, game, ".claude/settings.json")


def test_F9_TWIN_a_claude_root_still_demands_CLAUDE_md(tmp_path, monkeypatch):
    game = _game(tmp_path, monkeypatch)
    (game / "CLAUDE.md").unlink()
    gone = x4lock.missing()
    assert _in(gone, game, "CLAUDE.md")
    assert _in(gone, game, ".claude/settings.json")


def test_F9_TWIN_a_root_with_NO_agent_marker_still_demands_CLAUDE_md(tmp_path, monkeypatch):
    """Deleting .claude/ wholesale must not make CLAUDE.md's absence silent: with no
    target marker at all, the pre-4.0 (Claude) demand stands."""
    # R2-B1: the fallback applies when NO agent root is marked. Run from an INSTALLED separate
    # toolkit, this script's own folder is a marked agent root, so the game root is rightly
    # lock-if-present (MEASURED: this failed only in an installed toolkit). Pin the script to a
    # checkout-shaped folder so the case means the same wherever the suite runs.
    (tmp_path / "checkout" / "scripts").mkdir(parents=True)
    monkeypatch.setattr(x4lock, "_HERE", tmp_path / "checkout" / "scripts")
    game = _game(tmp_path, monkeypatch, claude=False)
    gone = x4lock.missing()
    assert _in(gone, game, "CLAUDE.md")


def test_F9_a_root_with_BOTH_targets_demands_both(tmp_path, monkeypatch):
    game = _game(tmp_path, monkeypatch)
    (game / ".codex" / "hooks").mkdir(parents=True)
    (game / "CLAUDE.md").unlink()
    gone = x4lock.missing()
    assert _in(gone, game, "CLAUDE.md") and _in(gone, game, "AGENTS.md")
# ------------------------------------- a SEPARATE install: the guards live in the toolkit
#
# v4.0.0 review (FX-I, pre-arc minor): `install --method separate` puts the guards, CLAUDE.md
# and settings.json in the TOOLKIT folder -- the folder the agent runs in -- and nothing in the
# game folder. x4lock walked only the game root, so it locked none of that install's guards,
# and demanded CLAUDE.md / settings.json in a game folder that was never meant to hold them
# (MEASURED on a scratch separate install: 1 protected, 4 missing). DECISION: an INSTALLED
# toolkit (runtime only, no agent/ source) is an agent root like the game root. A source
# CHECKOUT is not: its .claude/ etc. are generated, and a read-only bit there would break
# gen-agent-trees.py and git checkout.

def _installed_tk(tmp_path, monkeypatch, *, source=False):
    tk = tmp_path / "tk"
    for rel, text in (("tools/x4validate/x4validate/_paths.py", "#\n"), (".claude/hooks/a.sh", "#\n"),
                      (".claude/settings.json", "{}\n"), ("CLAUDE.md", "c\n"),
                      ("KNOWLEDGEBASE.md", "k\n"), ("x4-paths.env", "X4_TOOLKIT=x\n")):
        (tk / rel).parent.mkdir(parents=True, exist_ok=True)
        (tk / rel).write_text(text, encoding="utf-8")
    if source:
        (tk / "agent").mkdir()
    (tk / "scripts").mkdir()
    monkeypatch.setattr(x4lock, "_HERE", tk / "scripts")
    return tk


def test_a_SEPARATE_install_locks_the_TOOLKITs_guards(tmp_path, monkeypatch):
    tk = _installed_tk(tmp_path, monkeypatch)
    game = _game(tmp_path, monkeypatch, claude=False)
    got, gone = x4lock.manifest(), x4lock.missing()
    for rel in (".claude/hooks/a.sh", ".claude/settings.json", "CLAUDE.md", "KNOWLEDGEBASE.md"):
        assert _in(got, tk, rel), rel
    for rel in ("CLAUDE.md", ".claude/settings.json", "KNOWLEDGEBASE.md"):
        assert not _in(gone, game, rel), "a separate install's GAME folder was demanded %s" % rel


def test_TWIN_a_separate_install_that_LOST_its_CLAUDE_md_reports_it(tmp_path, monkeypatch):
    tk = _installed_tk(tmp_path, monkeypatch)
    _game(tmp_path, monkeypatch, claude=False)
    (tk / "CLAUDE.md").unlink()
    assert _in(x4lock.missing(), tk, "CLAUDE.md")


def _git_commit_all(root):
    """Make `root` a git repo whose HEAD tracks every file in it (agent/ included if present)."""
    import subprocess
    for argv in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                  "commit", "-q", "-m", "fixture"]):
        r = subprocess.run(["git", "-C", str(root), *argv], capture_output=True, text=True)
        assert r.returncode == 0, (argv, r.stderr)
    return root


def test_an_IN_PLACE_install_of_the_release_zip_IS_locked(tmp_path, monkeypatch):
    """FX-H2 / F269, user decision 2026-10-10: the README's default install extracts the release
    zip (agent/ present, NO .git) and installs in place. Keyed on agent/, x4lock took it for a
    source checkout and locked none of its guards. agent/ alone is not a checkout."""
    tk = _installed_tk(tmp_path, monkeypatch, source=True)
    _game(tmp_path, monkeypatch, claude=False)
    got = x4lock.manifest()
    for rel in (".claude/hooks/a.sh", ".claude/settings.json", "CLAUDE.md"):
        assert _in(got, tk, rel), rel


def test_TWIN_an_install_inside_ANOTHER_repo_is_still_locked(tmp_path, monkeypatch):
    """Clause: the HEAD must track agent/. A repo that does not (a game root under git) does
    not make the toolkit inside it a checkout."""
    tk = _installed_tk(tmp_path, monkeypatch)
    _git_commit_all(tk)                       # a repo at the root, HEAD without agent/
    _game(tmp_path, monkeypatch, claude=False)
    assert _in(x4lock.manifest(), tk, ".claude/hooks/a.sh")


def test_TWIN_a_SOURCE_checkout_is_never_locked_as_an_agent_root(tmp_path, monkeypatch):
    tk = _installed_tk(tmp_path, monkeypatch, source=True)
    (tk / "agent" / "core.md").write_text("#\n", encoding="utf-8")
    _git_commit_all(tk)                       # the toolkit's own checkout: .git, HEAD tracks agent/
    game = _game(tmp_path, monkeypatch, claude=False)
    got = x4lock.manifest()
    assert not _in(got, tk, ".claude/hooks/a.sh")
    assert _in(x4lock.missing(), game, "CLAUDE.md"), "the pre-4.0 fallback demand must stand"


def test_Ma_an_exported_GIT_DIR_does_not_lock_a_SOURCE_checkout(tmp_path, monkeypatch):
    """M-a (v4.0.0 delta review, MEASURED): `git -C <root>` with GIT_DIR exported answered for
    the OTHER repo, so the checkout read as an install and its generated trees were locked."""
    tk = _installed_tk(tmp_path, monkeypatch, source=True)
    (tk / "agent" / "core.md").write_text("#\n", encoding="utf-8")
    _git_commit_all(tk)
    other = tmp_path / "other"
    other.mkdir()
    (other / "r.txt").write_text("x", encoding="utf-8")
    _git_commit_all(other)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    _game(tmp_path, monkeypatch, claude=False)
    assert not _in(x4lock.manifest(), tk, ".claude/hooks/a.sh")


def test_Ma_a_dot_git_git_cannot_answer_for_is_LOCKED_and_SAYS_why(tmp_path, monkeypatch, capsys):
    import subprocess
    tk = _installed_tk(tmp_path, monkeypatch, source=True)
    r = subprocess.run(["git", "init", "-q", str(tk)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr                 # a .git with no commit: git cannot answer
    _game(tmp_path, monkeypatch, claude=False)
    assert _in(x4lock.manifest(), tk, ".claude/hooks/a.sh")
    err = capsys.readouterr().err
    assert "cannot tell whether" in err and "INSTALL" in err, err


def test_Ma_TWIN_a_SETTLED_install_prints_no_layout_note(tmp_path, monkeypatch, capsys):
    tk = _installed_tk(tmp_path, monkeypatch)
    _git_commit_all(tk)                                # git CAN answer: HEAD without agent/
    _game(tmp_path, monkeypatch, claude=False)
    assert _in(x4lock.manifest(), tk, ".claude/hooks/a.sh")
    assert "cannot tell whether" not in capsys.readouterr().err


def test_a_FRESH_install_with_no_registry_yet_does_not_report_it_missing(tmp_path, monkeypatch):
    """The registry is created by the first x4modlist run. Before that its folder does not
    exist either, and its absence is not a loss (MEASURED: every fresh install read MISSING)."""
    game = _game(tmp_path, monkeypatch)
    reg = tmp_path / "mods" / "_registry" / "modlist.yaml"
    monkeypatch.setattr(x4lock, "_cfg", lambda n: game if n == "game_root" else reg if n == "registry" else None)
    assert not any(str(p).endswith("modlist.yaml") for p in x4lock.missing())


def test_TWIN_a_DELETED_registry_whose_folder_remains_is_MISSING(tmp_path, monkeypatch):
    game = _game(tmp_path, monkeypatch)
    reg = tmp_path / "mods" / "_registry" / "modlist.yaml"
    reg.parent.mkdir(parents=True)
    monkeypatch.setattr(x4lock, "_cfg", lambda n: game if n == "game_root" else reg if n == "registry" else None)
    assert any(str(p).endswith("modlist.yaml") for p in x4lock.missing())


# --------------------------------------------- Layer 2 (x4refguard), informational only

@pytest.fixture(autouse=True)
def _no_real_layer2(monkeypatch, request):
    """`status` would otherwise read the CONFIGURED reference tree's ACL (one PowerShell
    call) in every test above. Read-only, but slow and machine-dependent -- so stubbed,
    except where a test asks for the real wiring."""
    if "REAL_layer2" not in request.node.name:
        monkeypatch.setattr(x4lock, "_layer2",
                            lambda: {"state": "stubbed", "detail": "test stub"}, raising=False)


def test_status_REPORTS_layer2_and_does_not_change_its_exit_code(tmp_path, monkeypatch, capsys):
    p = _fresh(tmp_path / "a.md")
    monkeypatch.setenv("X4_PROTECTED", str(p))
    x4lock._apply(p, True)
    try:
        monkeypatch.setattr(x4lock, "_layer2", lambda: {"state": "protected", "detail": "ok"})
        rc_on = x4lock.main(["status"])
        monkeypatch.setattr(x4lock, "_layer2", lambda: {"state": "absent", "detail": "LAYER 2 OFF"})
        rc_off = x4lock.main(["status"])
        out = capsys.readouterr()
        assert "reference deny-delete: absent" in (out.out + out.err)
        assert "reference deny-delete: protected" in (out.out + out.err)
        # informational: x4doctor is the verdict surface, x4lock's own contract is unchanged
        assert rc_on == rc_off
    finally:
        x4lock._apply(p, False)


def test_status_says_UNKNOWN_never_absent_when_layer2_cannot_be_read(tmp_path, monkeypatch, capsys):
    p = _fresh(tmp_path / "a.md")
    monkeypatch.setenv("X4_PROTECTED", str(p))

    def boom():
        raise RuntimeError("powershell missing")
    monkeypatch.setattr(x4lock, "_layer2", boom)
    x4lock.main(["status"])
    out = capsys.readouterr()
    text = out.out + out.err
    assert "reference deny-delete: UNKNOWN (RuntimeError: powershell missing)" in text
    assert "reference deny-delete: absent" not in text


def test_REAL_layer2_DELEGATES_to_x4refguard_report(monkeypatch):
    called = {}

    def fake_report(full=False, path=None):
        called["full"] = full
        return {"state": "partial", "detail": "x"}
    x4lock._layer2_module = None
    real = x4lock._load_refguard()
    monkeypatch.setattr(real, "report", fake_report)
    assert x4lock._layer2() == {"state": "partial", "detail": "x"}
    assert called == {"full": False}


# --- Plan 3 lane L: the OpenCode target is locked like the Codex one ------------------- #

_OC_LOCKED = (".opencode/hooks/x4guard.py", ".opencode/hooks/protect-bash.sh",
              ".opencode/hooks/ps_translate.ps1", ".opencode/plugins/x4guard.js",
              ".opencode/opencode.jsonc", ".opencode/X4-OPENCODE.md",
              ".opencode/skills/x4-cli-reference/SKILL.md",
              ".opencode/skills/x4-cli-reference/reference/x4save.md")


def test_opencode_guards_plugin_config_and_skills_are_locked_when_present(tmp_path, monkeypatch):
    game = _game(tmp_path, monkeypatch)
    for rel in _OC_LOCKED:
        (game / rel).parent.mkdir(parents=True, exist_ok=True)
        (game / rel).write_text("#\n", encoding="utf-8")
    got = x4lock.manifest()
    for rel in _OC_LOCKED:
        assert _in(got, game, rel), rel


def test_TWIN_a_users_own_opencode_plugin_is_NOT_locked(tmp_path, monkeypatch):
    """A user's plugin beside ours, and what OpenCode itself writes into .opencode/, are theirs."""
    game = _game(tmp_path, monkeypatch)
    for rel in (".opencode/plugins/mine.js", ".opencode/package.json", ".opencode/opencode.json"):
        (game / rel).parent.mkdir(parents=True, exist_ok=True)
        (game / rel).write_text("#\n", encoding="utf-8")
    got = x4lock.manifest()
    for rel in (".opencode/plugins/mine.js", ".opencode/package.json", ".opencode/opencode.json"):
        assert not _in(got, game, rel), rel


def test_an_opencode_root_DEMANDS_AGENTS_md_and_its_plugin(tmp_path, monkeypatch):
    game = _game(tmp_path, monkeypatch)
    (game / ".opencode" / "hooks").mkdir(parents=True)
    gone = x4lock.missing()
    assert _in(gone, game, "AGENTS.md")
    assert _in(gone, game, ".opencode/plugins/x4guard.js")


def test_TWIN_a_users_own_opencode_dir_demands_nothing(tmp_path, monkeypatch):
    game = _game(tmp_path, monkeypatch)
    (game / ".opencode").mkdir()
    (game / ".opencode" / "opencode.json").write_text("{}", encoding="utf-8")
    gone = x4lock.missing()
    assert not _in(gone, game, "AGENTS.md") and not _in(gone, game, ".opencode/plugins/x4guard.js")


# --- Plan 3 lane I: the manifest follows the config to the toolkit root ---------------- #

def _main_checkout(tmp_path, monkeypatch) -> Path:
    root = _checkout(tmp_path, "main")
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    monkeypatch.delenv("X4_TOOLKIT", raising=False)
    monkeypatch.delenv("X4_CONFIG", raising=False)
    return root


def _legacy(root: Path) -> Path:
    p = root / ".claude" / "x4-paths.env"
    p.parent.mkdir(parents=True, exist_ok=True)
    return _fresh(p)


def test_a_NEW_location_config_is_protected(tmp_path, monkeypatch):
    root = _main_checkout(tmp_path, monkeypatch)
    _fresh(root / "x4-paths.env")
    assert _named(x4lock.manifest(), root / "x4-paths.env")
    assert not _named(x4lock.missing(), root / "x4-paths.env")


def test_a_LEGACY_only_checkout_still_protects_its_config(tmp_path, monkeypatch):
    """A 3.x checkout not yet migrated: its config is still read, so it is still locked --
    and the 4.x path it does not have yet is NOT reported missing."""
    root = _main_checkout(tmp_path, monkeypatch)
    old = _legacy(root)
    assert _named(x4lock.manifest(), old)
    missing = x4lock.missing()
    assert not _named(missing, old) and not _named(missing, root / "x4-paths.env"), missing


def test_neither_reports_the_NEW_path_missing(tmp_path, monkeypatch):
    root = _main_checkout(tmp_path, monkeypatch)
    missing = x4lock.missing()
    assert _named(missing, root / "x4-paths.env"), missing
    assert not _named(missing, root / ".claude" / "x4-paths.env"), missing


def test_both_present_protects_BOTH(tmp_path, monkeypatch):
    """A legacy copy beside a new one still carries keys (X4_NEXUS_KEY): lock it too."""
    root = _main_checkout(tmp_path, monkeypatch)
    _fresh(root / "x4-paths.env")
    old = _legacy(root)
    man = x4lock.manifest()
    assert _named(man, root / "x4-paths.env") and _named(man, old), man


def test_a_worktree_whose_MAIN_checkout_is_still_legacy_demands_the_legacy_file(tmp_path, monkeypatch):
    """F119 waiver x lane I: the main checkout has not migrated yet. The waiver must demand
    the file that EXISTS there, not report a phantom 4.x file missing."""
    root = _checkout(tmp_path, "worktree")
    monkeypatch.setattr(x4lock, "_HERE", root / "scripts")
    monkeypatch.delenv("X4_TOOLKIT", raising=False)
    main_old = _legacy(tmp_path / "main")
    assert _named(x4lock.manifest(), main_old)
    assert not _named(x4lock.missing(), tmp_path / "main" / "x4-paths.env")
