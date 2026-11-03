"""`x4modlist downloads`: archives compared with what is installed, by manifest id."""

import json
import zipfile

import pytest

from x4validate import _downloads, _modlist, _registry


def _manifest(mod_id, version="100", date="2026-10-01", name="A Mod"):
    return (f'<?xml version="1.0"?><content id="{mod_id}" name="{name}" version="{version}" '
            f'date="{date}"/>').encode()


def _zip(folder, filename, members):
    path = folder / filename
    with zipfile.ZipFile(path, "w") as z:
        for member, data in members.items():
            z.writestr(member, data)
    return path


def _inst(mod_id, folder, version="100", date="2026-10-01", name="A Mod"):
    return {"id": mod_id, "folder": folder, "version": version, "date": date, "name": name}


@pytest.fixture
def world(monkeypatch):
    installed = [_inst("mod_a", "folder_a"), _inst("Mod_Case", "folder_case"),
                 _inst("ws_fork", "shared_folder"), _inst("mod_off", "folder_off"),
                 _inst("mod_custom", "folder_custom"), _inst("mod_named", "folder_named", name="Old Name")]
    active = [m for m in installed if m["id"] != "mod_off"]
    monkeypatch.setattr(_registry, "mods", lambda scope, *a, **k: installed if scope == "installed" else active)
    monkeypatch.setattr(_downloads, "_registry_flags", lambda _p: ({"mod_custom": True}, None))
    return installed


def _run(capsys, *argv):
    rc = _modlist.main(["downloads", *map(str, argv)])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def _report(capsys, folder):
    rc, out, _ = _run(capsys, folder, "--json")
    return rc, json.loads(out)


def _bucket(report, mod_id):
    return next(r for r in report["rows"] if r["id"] == mod_id)["bucket"]


@pytest.mark.parametrize("cand,inst,expected", [
    (("200", "2026-10-01"), ("100", "2026-10-01"), "NEWER"),
    (("100", "2026-10-02"), ("100", "2026-10-01"), "NEWER"),
    (("100", "2026-10-01"), ("100", "2026-10-01"), "SAME"),
    (("100", "2026-09-01"), ("100", "2026-10-01"), "OLDER"),
    (("100", "2026-12-01"), ("200", "2026-10-01"), "OLDER"),   # version outranks date
    (("x", "2026-10-02"), ("100", "2026-10-01"), "NEWER"),     # no usable version: date decides
    (("100", "2026-10-02"), ("x", "2026-10-01"), "NEWER"),     # installed has no usable version
    (("x", ""), ("100", ""), "UNDETERMINED"),
    (("100", ""), ("100", ""), "SAME"),
])
def test_compare(cand, inst, expected):
    assert _downloads.compare({"version": cand[0], "date": cand[1]},
                              {"version": inst[0], "date": inst[1]}) == expected


def test_buckets_by_manifest_id(world, tmp_path, capsys):
    _zip(tmp_path, "a.zip", {"folder_a/content.xml": _manifest("mod_a", "200")})
    _zip(tmp_path, "new.zip", {"brand_new/content.xml": _manifest("mod_new")})
    rc, report = _report(capsys, tmp_path)
    assert rc == 0
    assert _bucket(report, "mod_a") == "NEWER"
    assert _bucket(report, "mod_new") == "NOT-INSTALLED"


def test_a_misleading_filename_does_not_decide(world, tmp_path, capsys):
    """The manifest is the identity: a file named after another mod is still what it contains."""
    _zip(tmp_path, "mod_a 99 9.9 2026-10-01T00-00Z abc.zip", {"x/content.xml": _manifest("mod_new")})
    _, report = _report(capsys, tmp_path)
    assert _bucket(report, "mod_new") == "NOT-INSTALLED"


def test_id_matches_ignoring_case(world, tmp_path, capsys):
    _zip(tmp_path, "c.zip", {"x/content.xml": _manifest("MOD_CASE")})
    _, report = _report(capsys, tmp_path)
    assert _bucket(report, "MOD_CASE") == "SAME"


def test_a_folder_of_only_non_mod_archives_is_an_answer_not_a_refusal(world, tmp_path, capsys):
    """Every archive was read and none holds a manifest: that is a fact, unlike an unreadable one."""
    _zip(tmp_path, "skyrim.zip", {"meshes/armor.nif": b"x"})
    rc, out, _ = _run(capsys, tmp_path)
    assert rc == 0
    assert "1 archives = 0 with a manifest + 1 without + 0 unreadable" in out


def test_same_folder_different_id_is_not_called_not_installed(world, tmp_path, capsys):
    """Installing it would land in the folder of a DIFFERENT mod: its own bucket, not NOT-INSTALLED."""
    _zip(tmp_path, "c.zip", {"shared_folder/content.xml": _manifest("ws_original", "193")})
    _, report = _report(capsys, tmp_path)
    row = report["rows"][0]
    assert row["bucket"] == "DIFFERENT-ID-SAME-FOLDER"
    assert row["installed"]["id"] == "ws_fork"


def test_several_mods_in_one_archive_each_count(world, tmp_path, capsys):
    _zip(tmp_path, "pack.zip", {"folder_a/content.xml": _manifest("mod_a"), "other/content.xml": _manifest("mod_new")})
    _, report = _report(capsys, tmp_path)
    assert {r["id"] for r in report["rows"]} == {"mod_a", "mod_new"}


def test_only_the_newest_copy_is_listed_and_the_rest_are_counted(world, tmp_path, capsys):
    _zip(tmp_path, "old.zip", {"folder_a/content.xml": _manifest("mod_a", "150")})
    _zip(tmp_path, "new.zip", {"folder_a/content.xml": _manifest("mod_a", "200")})
    _, report = _report(capsys, tmp_path)
    assert [r["version"] for r in report["rows"]] == ["200"]
    assert [r["version"] for r in report["superseded"]] == ["150"]


def test_every_archive_ends_in_one_state_and_the_totals_reproduce(world, tmp_path, capsys):
    _zip(tmp_path, "mod.zip", {"folder_a/content.xml": _manifest("mod_a")})
    _zip(tmp_path, "skyrim.zip", {"meshes/armor.nif": b"x"})
    (tmp_path / "empty.zip").write_bytes(b"")
    (tmp_path / "broken.zip").write_bytes(b"this is not a zip")
    (tmp_path / "notes.txt").write_text("x")
    rc, out, _ = _run(capsys, tmp_path)
    assert rc == 0
    assert "4 archives = 1 with a manifest + 1 without + 2 unreadable. 1 other files not opened." in out


def test_a_zero_byte_download_is_named_as_such(world, tmp_path, capsys):
    (tmp_path / "empty.zip").write_bytes(b"")
    _zip(tmp_path, "mod.zip", {"folder_a/content.xml": _manifest("mod_a")})
    _, report = _report(capsys, tmp_path)
    assert report["unreadable"] == [{"file": "empty.zip", "reason": "0 bytes: the download did not complete"}]


def test_a_broken_zip_is_a_non_answer_not_not_a_mod(world, tmp_path, capsys):
    (tmp_path / "broken.zip").write_bytes(b"this is not a zip")
    rc, out, err = _run(capsys, tmp_path)
    assert rc == 3
    assert "broken.zip: not a readable zip" in out
    assert "Nothing was read" in err


def test_an_empty_folder_refuses_rather_than_reporting_clean(world, tmp_path, capsys):
    rc, _, err = _run(capsys, tmp_path)
    assert rc == 3
    assert "Nothing was examined" in err


def test_a_folder_of_other_files_also_refuses(world, tmp_path, capsys):
    (tmp_path / "notes.txt").write_text("x")
    rc, out, _ = _run(capsys, tmp_path)
    assert rc == 3
    assert "1 other files not opened" in out


def test_a_missing_folder_refuses(world, tmp_path, capsys):
    rc, _, err = _run(capsys, tmp_path / "nope")
    assert rc == 2
    assert "not a folder" in err


def test_no_installed_mods_refuses_rather_than_calling_everything_not_installed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(_registry, "mods", lambda scope, *a, **k: [])
    _zip(tmp_path, "a.zip", {"folder_a/content.xml": _manifest("mod_a")})
    rc, _, err = _run(capsys, tmp_path)
    assert rc == 2
    assert "no installed mods" in err


def test_flags(world, tmp_path, capsys):
    _zip(tmp_path, "a.zip", {"folder_custom/content.xml": _manifest("mod_custom", "200")})
    _zip(tmp_path, "b.zip", {"folder_off/content.xml": _manifest("mod_off", "200")})
    _zip(tmp_path, "c.zip", {"folder_named/content.xml": _manifest("mod_named", "200", name="New Name")})
    _, report = _report(capsys, tmp_path)
    flags = {r["id"]: r["flags"] for r in report["rows"]}
    assert flags == {"mod_custom": ["custom"], "mod_off": ["inactive"], "mod_named": ["renamed"]}


def test_an_unreadable_registry_costs_the_flag_and_says_so(monkeypatch, tmp_path, capsys):
    installed = [_inst("mod_a", "folder_a")]
    monkeypatch.setattr(_registry, "mods", lambda scope, *a, **k: installed)

    def boom(_path=None):
        raise ValueError("bad yaml")
    monkeypatch.setattr(_registry, "load_registry", boom)
    _zip(tmp_path, "a.zip", {"folder_a/content.xml": _manifest("mod_a", "200")})
    rc, out, _ = _run(capsys, tmp_path)
    assert rc == 0
    assert "[custom] flag is unavailable" in out


def test_an_unusable_manifest_is_listed_not_dropped(world, tmp_path, capsys):
    _zip(tmp_path, "good.zip", {"folder_a/content.xml": _manifest("mod_a")})
    _zip(tmp_path, "a.zip", {"bad/content.xml": b"<content id='x' name=broken"})
    _zip(tmp_path, "b.zip", {"noid/content.xml": b"<content name='no id'/>"})
    _zip(tmp_path, "c.zip", {"wrongroot/content.xml": b"<other id='x'/>"})
    _, report = _report(capsys, tmp_path)
    assert {b["file"] for b in report["bad_manifests"]} == {"a.zip", "b.zip", "c.zip"}


def test_a_deep_or_oversized_content_xml_is_not_taken_for_a_manifest(world, tmp_path, capsys):
    _zip(tmp_path, "deep.zip", {"a/b/c/content.xml": _manifest("mod_new")})
    _zip(tmp_path, "huge.zip", {"x/content.xml": b"<content id='h'/>" + b" " * (_downloads.MAX_MANIFEST_BYTES + 1)})
    _zip(tmp_path, "ok.zip", {"folder_a/content.xml": _manifest("mod_a")})
    _, report = _report(capsys, tmp_path)
    assert [r["id"] for r in report["rows"]] == ["mod_a"]
    assert [u["file"] for u in report["unreadable"]] == ["huge.zip"]
    assert report["no_manifest"] == 1


def test_top_level_only_unless_recursive(world, tmp_path, capsys):
    sub = tmp_path / "sub"
    sub.mkdir()
    _zip(sub, "a.zip", {"folder_a/content.xml": _manifest("mod_a")})
    _zip(tmp_path, "top.zip", {"folder_case/content.xml": _manifest("mod_case")})
    _, report = _report(capsys, tmp_path)
    assert [r["id"] for r in report["rows"]] == ["mod_case"]
    rc = _modlist.main(["downloads", str(tmp_path), "--recursive", "--json"])
    assert rc == 0
    assert len(json.loads(capsys.readouterr().out)["rows"]) == 2


def test_nothing_is_written(world, tmp_path, capsys):
    _zip(tmp_path, "a.zip", {"folder_a/content.xml": _manifest("mod_a", "200")})
    before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in tmp_path.rglob("*")}
    _run(capsys, tmp_path)
    assert {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in tmp_path.rglob("*")} == before


def test_without_7zip_a_7z_is_unreadable_and_a_zip_still_works(world, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(_downloads, "_find_7z", lambda: None)
    (tmp_path / "a.7z").write_bytes(b"7z-bytes")
    (tmp_path / "b.rar").write_bytes(b"rar-bytes")
    _zip(tmp_path, "c.zip", {"folder_a/content.xml": _manifest("mod_a")})
    rc, out, _ = _run(capsys, tmp_path)
    assert rc == 0
    assert "a.7z: needs 7-Zip" in out and "b.rar: needs 7-Zip" in out
    assert "7-Zip was not found" in out


def test_7zip_is_called_with_names_that_cannot_be_switches(monkeypatch, tmp_path):
    calls = []

    def fake(exe, args):
        calls.append(args)
        if args[0] == "l":
            return b"Path = folder_a/content.xml\nPath = folder_a/md/x.xml\n"
        return _manifest("mod_a", "200")
    monkeypatch.setattr(_downloads, "_run_7z", fake)
    path = tmp_path / "-rf.7z"
    path.write_bytes(b"x")
    got = _downloads.read_manifests(path, "7z")
    assert [m for m, _ in got] == ["folder_a/content.xml"]
    assert calls[0] == ["l", "-ba", "-slt", "--", str(path)]
    assert calls[1] == ["e", "-so", "-spd", "--", str(path), "folder_a/content.xml"]


def test_7zip_failure_is_a_reason_not_a_crash(monkeypatch, tmp_path):
    class R:
        returncode = 2
        stdout = b""
    monkeypatch.setattr(_downloads.subprocess, "run", lambda *a, **k: R())
    path = tmp_path / "bad.rar"
    path.write_bytes(b"x")
    with pytest.raises(_downloads.ArchiveUnreadable, match="7-Zip exit 2"):
        _downloads.read_manifests(path, "7z")


@pytest.mark.parametrize("name,expected", [
    ("sample_helper_1.32.zip 2089 1.32 2026-10-30T18-12Z kslDldtFQ.zip", (2089, "1.32", "2026-10-30")),
    ("Sample Overlay V1.00 2515 1 2026-10-25T19-24Z kslDldtfD(1).zip", (2515, "1", "2026-10-25")),
    ("Sample Modpack Example 7.04-1505-7-04-1729465116.rar", (1505, "7-04", "2024-10-20")),
    ("Follow Me-3093-1-0.7z", None),
    ("plain.zip", None),
])
def test_parse_nexus_name(name, expected):
    got = _downloads.parse_nexus_name(name)
    assert (None if got is None else (got["nexus_id"], got["version"], got["uploaded"])) == expected
