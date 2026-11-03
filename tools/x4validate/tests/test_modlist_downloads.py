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
    assert rc == 3
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
    assert rc == 3
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
    ("options_helper_1.32.zip 2089 1.32 2026-10-30T18-12Z kslDldtFQ.zip", (2089, "1.32", "2026-10-30")),
    ("Faction Hud V1.00 2515 1 2026-10-25T19-24Z kslDldtfD(1).zip", (2515, "1", "2026-10-25")),
    ("CPSDO Modpack VRO 7.04-1505-7-04-1729465116.rar", (1505, "7-04", "2024-10-20")),
    ("Follow Me-3093-1-0.7z", None),
    ("plain.zip", None),
])
def test_parse_nexus_name(name, expected):
    got = _downloads.parse_nexus_name(name)
    assert (None if got is None else (got["nexus_id"], got["version"], got["uploaded"])) == expected


# --------------------------------------------------------------------------- release review 4.1.0: T6, T8, T9

def _badname_zip(folder, filename="badname.zip"):
    """A zip whose central directory says UTF-8 for a name that is not: decoding it raises UnicodeDecodeError."""
    path = _zip(folder, filename, {"m3/content.xml": _manifest("mod_a")})
    raw = bytearray(path.read_bytes().replace(b"m3/content.xml", b"\xff3/content.xml"))
    at = raw.find(b"PK\x01\x02")
    raw[at + 9] |= 0x08   # general purpose flag bit 11, high byte: the name is UTF-8
    path.write_bytes(bytes(raw))
    return path


def _corrupt_deflate_zip(folder, filename="corrupt.zip"):
    path = folder / filename
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("m2/content.xml", _manifest("mod_a") * 20)
    raw = bytearray(path.read_bytes())
    start = 30 + len("m2/content.xml")
    for i in range(start + 2, start + 30):
        raw[i] ^= 0xA5
    path.write_bytes(bytes(raw))
    return path


def test_a_partial_read_exits_3_and_says_so(world, tmp_path, capsys):
    """One good archive + one broken one used to exit 0: "everything compared" for a list missing a file."""
    _zip(tmp_path, "good.zip", {"mod_a/content.xml": _manifest("mod_a", version="200")})
    (tmp_path / "broken.zip").write_bytes(b"not a zip")
    rc, _out, err = _run(capsys, tmp_path)
    assert rc == 3
    assert "PARTIAL" in err and "1 unreadable" in err


def test_an_unusable_manifest_also_exits_3(world, tmp_path, capsys):
    """Twin of the unreadable clause: each alone must go to 3."""
    _zip(tmp_path, "good.zip", {"mod_a/content.xml": _manifest("mod_a", version="200")})
    _zip(tmp_path, "bad.zip", {"m/content.xml": b"<notcontent/>"})
    rc, _out, err = _run(capsys, tmp_path)
    assert rc == 3
    assert "PARTIAL" in err and "1 unusable manifest" in err


def test_a_clean_read_with_a_non_mod_archive_is_still_rc_0(world, tmp_path, capsys):
    """Twin: an archive with no manifest was READ and has an answer, so it is not partial."""
    _zip(tmp_path, "good.zip", {"mod_a/content.xml": _manifest("mod_a", version="200")})
    _zip(tmp_path, "tool.zip", {"readme.txt": b"x"})
    rc, _out, err = _run(capsys, tmp_path)
    assert rc == 0 and "PARTIAL" not in err


def test_no_manifest_archives_are_named_under_all_and_counted_without_it(world, tmp_path, capsys):
    _zip(tmp_path, "tool.zip", {"readme.txt": b"x"})
    _zip(tmp_path, "good.zip", {"mod_a/content.xml": _manifest("mod_a", version="200")})
    _, plain, _ = _run(capsys, tmp_path)
    assert "tool.zip" not in plain and "1 without" in plain
    _, listed, _ = _run(capsys, tmp_path, "--all")
    assert "NO MANIFEST: 1" in listed and "tool.zip" in listed
    _, report = _report(capsys, tmp_path)
    assert report["no_manifest"] == 1 and report["no_manifest_files"] == ["tool.zip"]


def test_a_malformed_zip_name_does_not_abort_the_scan(world, tmp_path, capsys):
    _badname_zip(tmp_path)
    _zip(tmp_path, "good.zip", {"mod_a/content.xml": _manifest("mod_a", version="200")})
    rc, report = _report(capsys, tmp_path)
    assert rc == 3
    assert [u["file"] for u in report["unreadable"]] == ["badname.zip"]
    assert _bucket(report, "mod_a") == "NEWER", "the good archive must still be read"


def test_a_corrupt_deflate_stream_is_a_reason_not_a_crash(world, tmp_path, capsys):
    _corrupt_deflate_zip(tmp_path)
    rc, report = _report(capsys, tmp_path)
    assert rc == 3 and [u["file"] for u in report["unreadable"]] == ["corrupt.zip"]


def _dup(installed, tmp_path, candidate_version, monkeypatch):
    monkeypatch.setattr(_registry, "mods", lambda scope, *a, **k: installed)
    monkeypatch.setattr(_downloads, "_registry_flags", lambda _p: ({}, None))
    _zip(tmp_path, "mod.zip", {"dup/content.xml": _manifest("dup", version=candidate_version)})


def test_duplicate_installed_ids_that_disagree_are_undetermined_and_named(tmp_path, capsys, monkeypatch):
    """Last-wins used to compare against whichever folder came last: 200 read OLDER next to a v100 copy."""
    _dup([_inst("dup", "a_nexus", version="300"), _inst("dup", "ws_123", version="100")], tmp_path, "200", monkeypatch)
    rc, out, _err = _run(capsys, tmp_path)
    assert rc == 0
    assert "UNDETERMINED: 1" in out
    assert "a_nexus" in out and "ws_123" in out and "OLDER" in out.split("UNDETERMINED: 1")[1] and "installed 2 times" in out
    _, report = _report(capsys, tmp_path)
    row = report["rows"][0]
    assert {c["folder"]: c["bucket"] for c in row["installed_copies"]} == {"a_nexus": "OLDER", "ws_123": "NEWER"}


def test_duplicate_installed_ids_that_agree_keep_the_verdict_and_still_say_it(tmp_path, capsys, monkeypatch):
    """Twin: both copies are older, so NEWER is true of each."""
    _dup([_inst("dup", "a_nexus", version="100"), _inst("dup", "ws_123", version="150")], tmp_path, "200", monkeypatch)
    _, report = _report(capsys, tmp_path)
    row = report["rows"][0]
    assert row["bucket"] == "NEWER" and "installed 2 times" in row["flags"]
    assert sorted(c["folder"] for c in row["installed_copies"]) == ["a_nexus", "ws_123"]


def test_a_single_installed_copy_carries_no_copies_noise(world, tmp_path, capsys):
    _zip(tmp_path, "mod.zip", {"a/content.xml": _manifest("mod_a", version="200")})
    _, report = _report(capsys, tmp_path)
    assert report["rows"][0]["flags"] == [] and report["rows"][0]["installed_copies"] == []


def test_an_installed_row_with_no_id_does_not_abort(tmp_path, capsys, monkeypatch):
    rows = [{"id": None, "folder": "noid", "version": "1", "date": "", "name": "x"}, _inst("mod_a", "folder_a")]
    monkeypatch.setattr(_registry, "mods", lambda scope, *a, **k: rows)
    monkeypatch.setattr(_downloads, "_registry_flags", lambda _p: ({}, None))
    _zip(tmp_path, "mod.zip", {"a/content.xml": _manifest("mod_a", version="200")})
    rc, report = _report(capsys, tmp_path)
    assert rc == 0 and _bucket(report, "mod_a") == "NEWER"
