"""`scripts/deploy-mod.py` -- the guarded deploy of a mod this toolkit SHIPS.

WHY. The obvious deploy is `rm -rf "$EXT/$m" && cp -r "$SRC/$m" "$EXT/"`: one empty shell
variable away from deleting the wrong tree inside the game installation, and it destroys
the destination BEFORE the copy. The workspace had a guarded script for this, but it lived
in a private tree with personal paths and a personal mod list, so the public repo -- which
ships the helper mod -- had no safe way to install it at all.

One test per guard, each built so that ONLY that guard can refuse it.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "deploy-mod.py"


def _mod():
    spec = importlib.util.spec_from_file_location("deploy_mod", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _manifest(mod_id: str) -> str:
    return f'<?xml version="1.0" encoding="utf-8"?>\n<content id="{mod_id}" name="x" version="1"/>\n'


def _quiet(_s):
    """An `out` that discards: these tests assert what the guards DO, not what they print."""


def _shipped(tmp_path, name="helper", mod_id="helper_id", files=None):
    """A repo `mods/<name>` and a game root that looks like one (it carries 01.cat)."""
    src = tmp_path / "mods" / name
    (src / "ui").mkdir(parents=True)
    (src / "content.xml").write_text(_manifest(mod_id), encoding="utf-8")
    for rel, body in (files or {"ui/a.lua": "print(1)\n"}).items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(body.encode("utf-8"))
    game = tmp_path / "game"
    (game / "extensions").mkdir(parents=True)
    (game / "01.cat").write_bytes(b"")
    return tmp_path / "mods", game / "extensions"


def _symlink_or_skip(link, target, is_dir=False):
    """A link the guard must see through. A DIRECTORY falls back to a Windows junction,
    which needs no privilege, so the escape guard is exercised on an ordinary Windows
    account instead of skipping there."""
    try:
        os.symlink(target, link, target_is_directory=is_dir)
        return
    except (OSError, NotImplementedError) as exc:
        reason = exc
    if is_dir and os.name == "nt":
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            return
        except OSError as exc:
            reason = exc
    pytest.skip(f"this machine cannot create this link ({reason}) -- guard NOT checked here")


def test_a_DRY_RUN_writes_nothing(tmp_path):
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    assert d.deploy("helper", apply=False, src_root=src_root, ext_root=ext, out=_quiet)
    assert not (ext / "helper").exists()


def test_APPLY_copies_every_file_and_re_reads_them_byte_identical(tmp_path):
    d = _mod()
    src_root, ext = _shipped(tmp_path, files={"ui/a.lua": "print(1)\n", "ui.xml": "<x/>\n"})
    assert d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet)
    for rel in ("content.xml", "ui/a.lua", "ui.xml"):
        assert (ext / "helper" / rel).read_bytes() == (src_root / "helper" / rel).read_bytes()


def test_a_copy_that_does_not_READ_BACK_identical_is_a_failure_not_a_success(tmp_path, monkeypatch):
    """'N copied' is the writer's intention; the re-read is the file's state."""
    d = _mod()
    src_root, ext = _shipped(tmp_path, files={"ui/a.lua": "print(1)\n" * 50})

    def short_copy(a, b):
        pathlib.Path(b).write_bytes(pathlib.Path(a).read_bytes()[:10])

    monkeypatch.setattr(d.shutil, "copy2", short_copy)
    assert d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet) is False


def test_REFUSES_a_mod_this_repo_does_not_ship(tmp_path):
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    with pytest.raises(d.Refused, match="does not ship"):
        d.deploy("someone_elses_mod", apply=True, src_root=src_root, ext_root=ext, out=_quiet)


def test_REFUSES_an_extensions_root_that_does_NOT_EXIST(tmp_path):
    """A typo in X4_EXTENSIONS must not create a folder the game never reads and then
    report the deploy verified against it."""
    d = _mod()
    src_root, _ = _shipped(tmp_path)
    missing = tmp_path / "game" / "extensionz"
    with pytest.raises(d.Refused, match="does not exist"):
        d.deploy("helper", apply=True, src_root=src_root, ext_root=missing, out=_quiet)
    assert not missing.exists()


def test_REFUSES_an_extensions_root_whose_parent_is_NOT_a_game_root(tmp_path):
    d = _mod()
    src_root, _ = _shipped(tmp_path)
    elsewhere = tmp_path / "not_a_game" / "extensions"
    elsewhere.mkdir(parents=True)
    with pytest.raises(d.Refused, match="01.cat"):
        d.deploy("helper", apply=True, src_root=src_root, ext_root=elsewhere, out=_quiet)


def test_REFUSES_an_extensions_root_inside_the_CONFIGURED_profile(tmp_path, monkeypatch):
    """Dependencies resolve only within ONE extensions root, so a profile deploy makes
    every dependency read as MISSING. The profile folder is given an 01.cat so that only
    the profile guard can be what refuses."""
    d = _mod()
    src_root, _ = _shipped(tmp_path)
    profile = tmp_path / "somewhere" / "myprofile"
    prof_ext = profile / "extensions"
    prof_ext.mkdir(parents=True)
    (profile / "01.cat").write_bytes(b"")
    monkeypatch.setattr(d._paths, "profile", lambda: profile)
    with pytest.raises(d.Refused, match="profile"):
        d.deploy("helper", apply=True, src_root=src_root, ext_root=prof_ext, out=_quiet)


def test_a_PROFILE_SHAPED_path_is_refused_even_when_no_profile_is_configured(tmp_path, monkeypatch):
    d = _mod()
    src_root, _ = _shipped(tmp_path)
    prof_ext = tmp_path / "Egosoft" / "X4" / "12345678" / "extensions"
    prof_ext.mkdir(parents=True)
    (prof_ext.parent / "01.cat").write_bytes(b"")
    monkeypatch.setattr(d._paths, "profile", lambda: None)
    with pytest.raises(d.Refused, match="profile"):
        d.deploy("helper", apply=True, src_root=src_root, ext_root=prof_ext, out=_quiet)


def test_an_UNCONFIGURED_profile_is_ANNOUNCED_not_silently_skipped(tmp_path, monkeypatch):
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    monkeypatch.setattr(d._paths, "profile", lambda: None)
    lines = []
    d.deploy("helper", apply=False, src_root=src_root, ext_root=ext, out=lines.append)
    assert any("profile" in s.lower() and "not configured" in s.lower() for s in lines), lines


def test_REFUSES_a_destination_carrying_a_DIFFERENT_manifest_id(tmp_path):
    """Folder name is not identity: the manifest id is the real 'same mod' test."""
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    (ext / "helper").mkdir()
    (ext / "helper" / "content.xml").write_text(_manifest("another_mod"), encoding="utf-8")
    with pytest.raises(d.Refused, match="manifest id"):
        d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet)
    assert (ext / "helper" / "content.xml").read_text(encoding="utf-8") == _manifest("another_mod")


def test_REFUSES_a_destination_folder_with_NO_manifest(tmp_path):
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    (ext / "helper").mkdir()
    (ext / "helper" / "keep.txt").write_text("not ours", encoding="utf-8")
    with pytest.raises(d.Refused, match="no content.xml"):
        d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet)
    assert (ext / "helper" / "keep.txt").is_file()


def test_REFUSES_a_source_manifest_WITHOUT_an_id(tmp_path):
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    (src_root / "helper" / "content.xml").write_text('<content name="x"/>', encoding="utf-8")
    with pytest.raises(d.Refused, match="no readable content.xml id"):
        d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet)


def test_a_MALFORMED_manifest_is_a_refusal_not_a_traceback(tmp_path):
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    (src_root / "helper" / "content.xml").write_text("<content id=", encoding="utf-8")
    with pytest.raises(d.Refused, match="well-formed"):
        d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet)


def test_an_ORPHAN_is_deleted_one_named_FILE_at_a_time(tmp_path):
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    assert d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet)
    (ext / "helper" / "ui" / "stale.lua").write_bytes(b"old\n")
    assert d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet)
    assert not (ext / "helper" / "ui" / "stale.lua").exists()
    assert (ext / "helper" / "ui" / "a.lua").is_file()
    bin_dir = pathlib.Path(os.environ["X4_RECYCLE_TEST_BIN"])
    assert any(p.name.endswith("-stale.lua") and p.read_bytes() == b"old\n"
               for p in bin_dir.iterdir())


def test_orphan_recycle_refusal_retains_it_and_fails_deploy(tmp_path, monkeypatch):
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    assert d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet)
    orphan = ext / "helper/ui/stale.lua"
    orphan.write_bytes(b"retain")
    def refuse(paths):
        raise OSError("declined")
    monkeypatch.setattr(d, "recycle_or_raise", refuse)
    with pytest.raises(OSError, match="declined"):
        d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet)
    assert orphan.read_bytes() == b"retain"


def test_REFUSES_to_delete_an_orphan_that_is_a_SYMLINK(tmp_path):
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    assert d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet)
    outside = tmp_path / "precious.txt"
    outside.write_text("keep me\n", encoding="utf-8")
    _symlink_or_skip(ext / "helper" / "ui" / "link.lua", outside)
    with pytest.raises(d.Refused, match="not a plain file"):
        d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet)
    assert outside.read_text(encoding="utf-8") == "keep me\n"


def test_REFUSES_an_orphan_SYMLINK_even_when_its_target_is_INSIDE_the_destination(tmp_path):
    """Isolates the is_symlink clause: the target is inside dst, so only that clause
    can be what refuses."""
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    assert d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet)
    _symlink_or_skip(ext / "helper" / "ui" / "alias.lua", ext / "helper" / "ui" / "a.lua")
    with pytest.raises(d.Refused, match="not a plain file"):
        d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet)


def test_REFUSES_a_DESTINATION_folder_that_is_itself_a_LINK(tmp_path):
    """A junction from extensions/<mod> to another tree -- say another worktree's
    mods/<mod>, whose manifest id matches -- would take the copies AND the orphan
    deletions. MEASURED risk class on this machine: several worktrees of one repo."""
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    other = tmp_path / "another_tree" / "helper"
    other.mkdir(parents=True)
    (other / "content.xml").write_text(_manifest("helper_id"), encoding="utf-8")
    (other / "theirs.lua").write_text("keep\n", encoding="utf-8")
    _symlink_or_skip(ext / "helper", other, is_dir=True)
    with pytest.raises(d.Refused, match="link"):
        d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet)
    assert (other / "theirs.lua").read_text(encoding="utf-8") == "keep\n"
    assert not (other / "ui").exists()


def test_REFUSES_to_write_THROUGH_a_changed_file_that_is_a_SYMLINK(tmp_path):
    d = _mod()
    src_root, ext = _shipped(tmp_path, files={"ui/a.lua": "new\n"})
    (ext / "helper" / "ui").mkdir(parents=True)
    (ext / "helper" / "content.xml").write_text(_manifest("helper_id"), encoding="utf-8")
    outside = tmp_path / "precious.lua"
    outside.write_text("keep me\n", encoding="utf-8")
    _symlink_or_skip(ext / "helper" / "ui" / "a.lua", outside)
    with pytest.raises(d.Refused, match="link"):
        d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet)
    assert outside.read_text(encoding="utf-8") == "keep me\n"


def test_REFUSES_a_copy_that_would_ESCAPE_the_destination_through_a_link(tmp_path):
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    (ext / "helper").mkdir()
    (ext / "helper" / "content.xml").write_text(_manifest("helper_id"), encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink_or_skip(ext / "helper" / "ui", outside, is_dir=True)
    with pytest.raises(d.Refused, match="outside"):
        d.deploy("helper", apply=True, src_root=src_root, ext_root=ext, out=_quiet)
    assert not (outside / "a.lua").exists()


def test_the_output_NAMES_the_source_tree_it_deployed_from(tmp_path):
    """Two checkouts once forked with nothing saying which one a tool was standing in."""
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    lines = []
    d.deploy("helper", apply=False, src_root=src_root, ext_root=ext, out=lines.append)
    assert any(str(src_root / "helper") in s for s in lines), lines


def test_with_SEVERAL_mods_every_guard_runs_before_ANY_is_applied(tmp_path, monkeypatch):
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    monkeypatch.setattr(d, "MODS", src_root)
    monkeypatch.setattr(d._paths, "game_extensions", lambda: ext)
    assert d.main(["helper", "not_shipped", "--apply"]) == 2
    assert not (ext / "helper").exists(), "the first mod was applied before the second refused"


def test_an_UNKNOWN_flag_is_refused_rather_than_silently_ignored(tmp_path, monkeypatch, capsys):
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    monkeypatch.setattr(d, "MODS", src_root)
    monkeypatch.setattr(d._paths, "game_extensions", lambda: ext)
    assert d.main(["helper", "--aply"]) == 2
    assert "--aply" in capsys.readouterr().err
    assert not (ext / "helper").exists()


def test_HELP_prints_the_usage_and_exits_0(capsys):
    d = _mod()
    assert d.main(["--help"]) == 0
    assert "--apply" in capsys.readouterr().out


def test_an_OS_ERROR_during_the_write_is_a_failure_not_a_traceback(tmp_path, monkeypatch, capsys):
    d = _mod()
    src_root, ext = _shipped(tmp_path)
    monkeypatch.setattr(d, "MODS", src_root)
    monkeypatch.setattr(d._paths, "game_extensions", lambda: ext)

    def denied(a, b):
        raise PermissionError("access denied")

    monkeypatch.setattr(d.shutil, "copy2", denied)
    assert d.main(["helper", "--apply"]) == 1
    assert "access denied" in capsys.readouterr().err


def test_main_is_rc_2_when_no_game_extensions_folder_is_configured(monkeypatch, capsys):
    d = _mod()
    monkeypatch.setattr(d._paths, "game_extensions", lambda: None)
    assert d.main(["x4_toolkit_helper"]) == 2
    assert "REFUSING" in capsys.readouterr().err


def test_main_lists_what_it_can_deploy_when_given_nothing(capsys):
    d = _mod()
    assert d.main([]) == 2
    assert "x4_toolkit_helper" in capsys.readouterr().out
