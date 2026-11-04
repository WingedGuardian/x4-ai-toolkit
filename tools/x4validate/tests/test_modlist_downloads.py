"""`x4modlist downloads`: archives compared with what is installed, by manifest id."""

import json
import zipfile

import pytest

from x4validate import _downloads, _modlist, _registry


_REAL_ENTRIES = _downloads.profile_entries  # the fixture stubs only the machine's own profile


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
    monkeypatch.setattr(_downloads, "profile_entries", lambda p=None: {} if p is None else _REAL_ENTRIES(p))
    monkeypatch.setattr(_downloads, "icon_world", lambda active, config=None: {"base": {"b"}, "layers": [], "unread": []})
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
    ("sample_helper_1.32.zip 2089 1.32 2026-10-30T18-12Z kslDldtFQ.zip", (2089, "1.32", "2026-10-30")),
    ("Sample Overlay V1.00 2515 1 2026-10-25T19-24Z kslDldtfD(1).zip", (2515, "1", "2026-10-25")),
    ("Sample Modpack Example 7.04-1505-7-04-1729465116.rar", (1505, "7-04", "2024-10-20")),
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


# --- warnings: what would go wrong after installing ----------------------------------------

def _warn(report, mod_id):
    return [w["kind"] for w in next(r for r in report["rows"] if r["id"] == mod_id)["warnings"]]


def _texts(report, mod_id):
    return " | ".join(w["text"] for w in next(r for r in report["rows"] if r["id"] == mod_id)["warnings"])


def test_an_id_the_profile_turns_off_warns_and_one_it_turns_on_does_not(world, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(_downloads, "profile_entries", lambda p=None: {"new_off": False, "new_on": True})
    _zip(tmp_path, "a.zip", {"x/content.xml": _manifest("New_Off", name="Off Mod")})
    _zip(tmp_path, "b.zip", {"y/content.xml": _manifest("new_on", name="On Mod")})
    _zip(tmp_path, "c.zip", {"z/content.xml": _manifest("new_absent", name="Absent Mod")})
    _, report = _report(capsys, tmp_path)
    assert _warn(report, "New_Off") == ["profile-disabled"]
    assert _warn(report, "new_on") == [] and _warn(report, "new_absent") == []


def test_a_profile_that_cannot_be_read_says_the_check_did_not_run(world, tmp_path, capsys, monkeypatch):
    def boom(p=None):
        raise FileNotFoundError("no profile")
    monkeypatch.setattr(_downloads, "profile_entries", boom)
    _zip(tmp_path, "a.zip", {"x/content.xml": _manifest("new_mod", name="Some Mod")})
    rc, out, _ = _run(capsys, tmp_path)
    assert rc == 0 and "profile-disabled not checked" in out


def test_the_same_name_under_another_id_warns_and_names_it(world, tmp_path, capsys):
    _zip(tmp_path, "a.zip", {"x/content.xml": _manifest("other_id", name="old name ")})
    _, report = _report(capsys, tmp_path)
    assert _warn(report, "other_id") == ["same-name"]
    assert "folder_named [mod_named]" in _texts(report, "other_id")


def test_the_same_name_and_the_same_id_is_not_a_twin(world, tmp_path, capsys):
    _zip(tmp_path, "a.zip", {"folder_named/content.xml": _manifest("MOD_NAMED", "200", name="Old Name")})
    _, report = _report(capsys, tmp_path)
    assert _warn(report, "MOD_NAMED") == []


def test_what_you_would_not_install_gets_no_warning(world, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(_downloads, "profile_entries", lambda p=None: {"mod_a": False})
    _zip(tmp_path, "a.zip", {"folder_a/content.xml": _manifest("mod_a")})  # SAME
    _, report = _report(capsys, tmp_path)
    assert _bucket(report, "mod_a") == "SAME" and _warn(report, "mod_a") == []


def _profile(folder, *entries):
    folder.mkdir()
    body = "".join(f'<extension id="{i}" enabled="{on}"/>' for i, on in entries)
    (folder / "content.xml").write_text(f"<content>{body}</content>", encoding="utf-8")
    return folder


def test_a_test_profile_with_no_entry_leaks_and_one_with_any_entry_does_not(world, tmp_path, capsys):
    downloads = tmp_path / "dl"
    downloads.mkdir()
    _zip(downloads, "a.zip", {"x/content.xml": _manifest("leaky", name="Leaky")})
    _zip(downloads, "b.zip", {"y/content.xml": _manifest("listed", name="Listed")})
    _zip(downloads, "c.zip", {"z/content.xml":
                              b'<content id="off_itself" name="Off Itself" version="1" enabled="0"/>'})
    prof = _profile(tmp_path / "900", ("listed", "false"))
    rc, out, _ = _run(capsys, downloads, "--test-profile", prof, "--json")
    report = json.loads(out)
    assert rc == 0 and report["test_profiles"] == ["900"]
    assert _warn(report, "leaky") == ["test-profile"] and "profile 900" in _texts(report, "leaky")
    assert _warn(report, "listed") == []
    assert _warn(report, "off_itself") == []   # its manifest defaults it off, so nothing leaks


@pytest.mark.parametrize("make", ["missing", "broken"])
def test_a_named_test_profile_that_cannot_be_read_refuses(world, tmp_path, capsys, make):
    downloads = tmp_path / "dl"
    downloads.mkdir()
    _zip(downloads, "a.zip", {"x/content.xml": _manifest("leaky", name="Leaky")})
    target = tmp_path / "content.xml"
    if make == "broken":
        target.write_text("<content", encoding="utf-8")
    rc, out, err = _run(capsys, downloads, "--test-profile", target)
    assert rc == 2 and "--test-profile" in err and "NEW" not in out


def test_icon_ops_reads_a_whole_file_a_diff_and_refuses_anything_else():
    assert _downloads.icon_ops(b'<icons><icon name="a"/><icon name="a"/><icon name="b"/></icons>') == ({"a", "b"}, set())
    diff = (b'<diff><add sel="/icons"><icon name="c"/></add>'
            b"""<remove sel="//icon[@name='d']"/><remove sel='/icons/icon[@name="e"]'/>"""
            b'<replace sel="//icon[@name=\'f\']/@width">1</replace></diff>')
    assert _downloads.icon_ops(diff) == ({"c"}, {"d", "e"})
    assert _downloads.icon_ops(b"<wares/>") is None
    assert _downloads.icon_ops(b"<icons") is None


def _icon_xml(*names):
    return ("<icons>" + "".join(f'<icon name="{n}"/>' for n in names) + "</icons>").encode()


def _icon_world(base_size, *layers):
    return {"base": {f"b{i}" for i in range(base_size)}, "layers": list(layers), "unread": []}


def test_icon_total_follows_load_order_and_swaps_a_mod_for_its_archive():
    world = _icon_world(10, ("m1", {"x", "y"}, set()), ("m2", set(), {"x", "b0"}))
    assert _downloads.icon_total(world) == 10 + 2 - 2
    assert _downloads.icon_total(world, "new", ({"z"}, set())) == 11              # appended
    assert _downloads.icon_total(world, "m1", ({"y"}, set())) == 10               # m1 replaced, x never added
    assert _downloads.icon_total(world, "m2", (set(), set())) == 12               # the removals go with m2


def _with_world(monkeypatch, world):
    monkeypatch.setattr(_downloads, "icon_world", lambda active, config=None: world)


@pytest.mark.parametrize("base,expected", [
    (_downloads.ICONS_LOADED - 1, []),                     # exactly the total that loaded: no warning
    (_downloads.ICONS_LOADED, ["icons"]),                  # one past it
    (_downloads.ICONS_FAILED - 1, ["icons"]),              # reaches the total that failed
])
def test_the_icon_warning_edges(world, tmp_path, capsys, monkeypatch, base, expected):
    _with_world(monkeypatch, _icon_world(base))
    _zip(tmp_path, "a.zip", {"x/content.xml": _manifest("new_mod", name="Icons"),
                             "x/libraries/icons.xml": _icon_xml("z")})
    _, report = _report(capsys, tmp_path)
    assert _warn(report, "new_mod") == expected
    assert next(r for r in report["rows"] if r["id"] == "new_mod")["icons"] == {"names": 1, "total_after": base + 1}


def test_the_two_icon_warnings_say_different_things():
    assert "unmeasured" in _downloads._icon_warning(_downloads.ICONS_LOADED + 1)["text"]
    assert "failing to load" in _downloads._icon_warning(_downloads.ICONS_FAILED)["text"]
    assert _downloads._icon_warning(_downloads.ICONS_LOADED) is None


def test_a_mod_with_no_icons_is_not_blamed_for_a_total_already_over(world, tmp_path, capsys, monkeypatch):
    _with_world(monkeypatch, _icon_world(_downloads.ICONS_FAILED + 5))
    _zip(tmp_path, "a.zip", {"x/content.xml": _manifest("new_mod", name="No Icons")})
    _, report = _report(capsys, tmp_path)
    assert _warn(report, "new_mod") == []


@pytest.mark.parametrize("members,why", [
    ({"x/ext_01.cat": b"", "x/content.xml": _manifest("new_mod", name="P")}, "packed in a catalog"),
    ({"x/libraries/icons.xml": b"<icons", "x/content.xml": _manifest("new_mod", name="P")}, "will not parse"),
])
def test_an_icon_count_that_cannot_be_read_is_unknown_not_zero(world, tmp_path, capsys, monkeypatch, members, why):
    _with_world(monkeypatch, _icon_world(5))
    _zip(tmp_path, "a.zip", members)
    _, report = _report(capsys, tmp_path)
    row = next(r for r in report["rows"] if r["id"] == "new_mod")
    assert row["icons"] is None and why in _texts(report, "new_mod")


def test_each_mod_in_an_archive_is_counted_from_its_own_folder(world, tmp_path, capsys, monkeypatch):
    _with_world(monkeypatch, _icon_world(5))
    _zip(tmp_path, "a.zip", {"one/content.xml": _manifest("m_one", name="One"),
                             "one/libraries/icons.xml": _icon_xml("p", "q"),
                             "two/content.xml": _manifest("m_two", name="Two"),
                             "two/libraries/icons.xml": _icon_xml("r")})
    _, report = _report(capsys, tmp_path)
    got = {r["id"]: r["icons"]["names"] for r in report["rows"]}
    assert got == {"m_one": 2, "m_two": 1}


def test_a_failed_icon_count_says_so_and_prints_no_total(world, tmp_path, capsys, monkeypatch):
    _with_world(monkeypatch, {"note": "icon budget not checked: no base-game icon list was found"})
    _zip(tmp_path, "a.zip", {"x/content.xml": _manifest("new_mod", name="M")})
    rc, out, _ = _run(capsys, tmp_path)
    assert rc == 0 and "icon budget not checked" in out and "Merged icon names now" not in out


def test_icon_world_refuses_an_empty_base(tmp_path):
    class Cfg:
        reference = tmp_path

        @staticmethod
        def dlc_dirs():
            return []
    assert "not checked" in _downloads.icon_world([], Cfg())["note"]


def test_icon_world_reads_base_and_mods_and_names_an_unreadable_list(tmp_path):
    base = tmp_path / "ref" / "libraries"
    base.mkdir(parents=True)
    (base / "icons.xml").write_bytes(_icon_xml("a", "b"))
    good = tmp_path / "good" / "libraries"
    good.mkdir(parents=True)
    (good / "icons.xml").write_bytes(b'<diff><add sel="/icons"><icon name="c"/></add></diff>')
    bad = tmp_path / "bad" / "libraries"
    bad.mkdir(parents=True)
    (bad / "icons.xml").write_bytes(b"<icons")

    class Cfg:
        reference = tmp_path / "ref"

        @staticmethod
        def dlc_dirs():
            return []
    active = [{"id": "good", "path": str(tmp_path / "good"), "folder": "good"},
              {"id": "bad", "path": str(tmp_path / "bad"), "folder": "bad"}]
    world = _downloads.icon_world(active, Cfg())
    assert _downloads.icon_total(world) == 3 and world["unread"] == ["bad"]


def test_icons_in_a_7z_are_read_with_names_that_cannot_be_switches(monkeypatch, tmp_path):
    seen = []

    def run7(exe, args):
        seen.append(args)
        if args[0] == "l":
            return b"Path = m/libraries/icons.xml\nPath = n/ext_01.cat\nPath = m/other.xml\n"
        return _icon_xml("a")
    monkeypatch.setattr(_downloads, "_run_7z", run7)
    path = tmp_path / "-x.7z"
    path.write_bytes(b"not read")
    lists, packed = _downloads.read_icon_lists(path, "7z")
    assert lists == {"m": _icon_xml("a")} and packed == {"n"}
    assert all("--" in a for a in seen)


# --- release review of the warnings: missing base, profile labels, catalog names, op semantics, the combined total

def _cfg(ref, *dlcs):
    class Cfg:
        reference = ref

        @staticmethod
        def dlc_dirs():
            return list(dlcs)
    return Cfg()


def _icon_root(root, data):
    (root / "libraries").mkdir(parents=True)
    (root / "libraries" / "icons.xml").write_bytes(data)
    return root


@pytest.mark.parametrize("base_list", [None, b"<icons", b"<wares/>", b"<icons/>"])
def test_a_base_list_missing_or_unreadable_refuses_even_with_a_dlc_present(tmp_path, base_list):
    """An un-unpacked reference plus packed DLC used to count the DLC alone: 1 name and a huge false margin."""
    ref = tmp_path / "ref"
    ref.mkdir()
    if base_list is not None:
        _icon_root(ref, base_list)
    dlc = _icon_root(tmp_path / "ego_dlc_x", _icon_xml("d"))
    world = _downloads.icon_world([], _cfg(ref, dlc))
    assert "not checked" in world["note"] and "base" not in world


def test_a_small_readable_base_is_counted_because_no_size_is_a_threshold(tmp_path):
    """Twin: the refusal is about the base's own file, never about how many names it has."""
    ref = _icon_root(tmp_path / "ref", _icon_xml("a"))
    dlc = _icon_root(tmp_path / "ego_dlc_x", _icon_xml("d"))
    world = _downloads.icon_world([], _cfg(ref, dlc))
    assert _downloads.icon_total(world) == 2 and world["unread"] == []


def test_a_dlc_with_no_icon_list_does_not_refuse(tmp_path):
    """Twin: a DLC with no icon list is ordinary, so only the base's own file can refuse."""
    ref = _icon_root(tmp_path / "ref", _icon_xml("a", "b"))
    dlc = tmp_path / "ego_dlc_x"
    dlc.mkdir()
    world = _downloads.icon_world([], _cfg(ref, dlc))
    assert _downloads.icon_total(world) == 2


def test_a_dlc_removal_counts_in_the_base(tmp_path):
    ref = _icon_root(tmp_path / "ref", _icon_xml("a", "b"))
    dlc = _icon_root(tmp_path / "ego_dlc_x", b"""<diff><remove sel="//icon[@name='a']"/></diff>""")
    assert _downloads.icon_total(_downloads.icon_world([], _cfg(ref, dlc))) == 1


@pytest.mark.parametrize("leak_first", [True, False])
def test_two_test_profiles_with_one_folder_name_stay_apart(world, tmp_path, capsys, leak_first):
    """Keyed by folder name these collapsed, last wins, so in one order a real leak gave no warning."""
    downloads = tmp_path / "dl"
    downloads.mkdir()
    _zip(downloads, "a.zip", {"x/content.xml": _manifest("leaky", name="Leaky")})
    (tmp_path / "aa").mkdir()
    (tmp_path / "bb").mkdir()
    leaks = _profile(tmp_path / "aa" / "900")
    covers = _profile(tmp_path / "bb" / "900", ("leaky", "true"))
    first, second = (leaks, covers) if leak_first else (covers, leaks)
    _, out, _ = _run(capsys, downloads, "--test-profile", first, "--test-profile", second, "--json")
    report = json.loads(out)
    assert report["test_profiles"] == ["aa/900", "bb/900"]
    assert _warn(report, "leaky") == ["test-profile"]
    assert "profile aa/900 " in _texts(report, "leaky")
    assert tmp_path.name not in " ".join(report["test_profiles"]) + _texts(report, "leaky")


def test_differently_named_test_profiles_print_only_the_folder_name(world, tmp_path, capsys):
    """Twin: nothing more of a private path than it takes to tell them apart."""
    downloads = tmp_path / "dl"
    downloads.mkdir()
    _zip(downloads, "a.zip", {"x/content.xml": _manifest("leaky", name="Leaky")})
    one, two = _profile(tmp_path / "900"), _profile(tmp_path / "901", ("leaky", "true"))
    _, out, _ = _run(capsys, downloads, "--test-profile", one, "--test-profile", two, "--json")
    report = json.loads(out)
    assert report["test_profiles"] == ["900", "901"] and "profile 900 " in _texts(report, "leaky")


def test_one_test_profile_given_as_folder_and_as_file_is_one_profile(world, tmp_path, capsys):
    downloads = tmp_path / "dl"
    downloads.mkdir()
    _zip(downloads, "a.zip", {"x/content.xml": _manifest("leaky", name="Leaky")})
    prof = _profile(tmp_path / "900")
    _, out, _ = _run(capsys, downloads, "--test-profile", prof, "--test-profile", prof / "content.xml", "--json")
    report = json.loads(out)
    assert report["test_profiles"] == ["900"] and _warn(report, "leaky") == ["test-profile"]


@pytest.mark.parametrize("cat", ["ext_v900.cat", "ext_01_diff_v900.cat", "subst_v900.cat", "EXT_01.CAT"])
def test_a_version_or_diff_catalog_makes_the_count_unknown_not_zero(world, tmp_path, capsys, monkeypatch, cat):
    """One matching the game version is live, and nothing here opens it."""
    _with_world(monkeypatch, _icon_world(5))
    _zip(tmp_path, "a.zip", {f"x/{cat}": b"", "x/content.xml": _manifest("new_mod", name="P")})
    _, report = _report(capsys, tmp_path)
    row = next(r for r in report["rows"] if r["id"] == "new_mod")
    assert row["icons"] is None and "packed in a catalog" in _texts(report, "new_mod")


@pytest.mark.parametrize("cat", ["ext_01_sig.cat", "ext_v900_sig.cat", "notes.cat"])
def test_a_signature_or_unrelated_catalog_name_leaves_the_count_at_zero(world, tmp_path, capsys, monkeypatch, cat):
    """Twin: only a catalog the game could load makes the count unknown."""
    _with_world(monkeypatch, _icon_world(5))
    _zip(tmp_path, "a.zip", {f"x/{cat}": b"", "x/content.xml": _manifest("new_mod", name="P")})
    _, report = _report(capsys, tmp_path)
    assert next(r for r in report["rows"] if r["id"] == "new_mod")["icons"] == {"names": 0, "total_after": 5}


def _version_cat(root, name, *members):
    root.mkdir(parents=True)
    lines = "".join(f"{m} 1 0 {'0' * 32}\n" for m in members)
    (root / name).write_text(lines, encoding="utf-8")
    return root


def test_an_installed_version_catalog_holding_an_icon_list_is_named_as_not_read(tmp_path):
    ref = _icon_root(tmp_path / "ref", _icon_xml("a"))
    mod = _version_cat(tmp_path / "mod", "ext_v900.cat", "libraries/icons.xml")
    world = _downloads.icon_world([{"id": "m", "path": str(mod), "folder": "mod"}], _cfg(ref))
    assert world["unread"] == ["mod"]


def test_an_installed_version_catalog_without_an_icon_list_is_not_named(tmp_path):
    """Twin: most version catalogs have nothing to do with icons."""
    ref = _icon_root(tmp_path / "ref", _icon_xml("a"))
    mod = _version_cat(tmp_path / "mod", "ext_v900.cat", "libraries/wares.xml")
    world = _downloads.icon_world([{"id": "m", "path": str(mod), "folder": "mod"}], _cfg(ref))
    assert world["unread"] == []


@pytest.mark.parametrize("ops,expected", [
    # Document order decides, so a name removed and added back is present.
    ("""<remove sel="//icon[@name='a']"/><add sel="/icons"><icon name="a"/></add>""", ({"a"}, set())),
    ("""<add sel="/icons"><icon name="a"/></add><remove sel="//icon[@name='a']"/>""", (set(), {"a"})),
    # Only a selector ending on the icon removes the icon.
    ("""<remove sel="//icon[@name='a']/@width"/>""", (set(), set())),
    ("""<remove sel="//icon[@name='a']/child"/>""", (set(), set())),
    ("""<remove sel="//other[@name='a']"/>""", (set(), set())),
    ("""<remove sel="/icons/icon[@name='a']"/>""", (set(), {"a"})),
    # Replace: the whole icon, its name, or an attribute that changes no name.
    ("""<replace sel="//icon[@name='a']"><icon name="b"/></replace>""", ({"b"}, {"a"})),
    ("""<replace sel="//icon[@name='a']"><icon name="a"/></replace>""", ({"a"}, set())),
    ("""<replace sel="//icon[@name='a']/@name">b</replace>""", ({"b"}, {"a"})),
    ("""<replace sel="//icon[@name='a']/@width">1</replace>""", (set(), set())),
    ("""<replace sel="/icons/group"><group><icon name="c"/></group></replace>""", ({"c"}, set())),
    # A condition is not evaluated: its removal is not assumed, so the count errs high.
    ("""<remove sel="//icon[@name='a']" if="x"/>""", (set(), set())),
    ("""<replace sel="//icon[@name='a']" if="x"><icon name="b"/></replace>""", ({"b"}, set())),
    ("""<replace sel="//icon[@name='a']/@name" if="x">b</replace>""", ({"b"}, set())),
])
def test_icon_ops_follows_what_each_op_does_to_names(ops, expected):
    assert _downloads.icon_ops(f"<diff>{ops}</diff>".encode()) == expected


def test_a_name_removed_and_added_back_stays_in_the_total():
    readded = _downloads.icon_ops(b"""<diff><remove sel="//icon[@name='b0']"/>"""
                                  b"""<add sel="/icons"><icon name="b0"/></add></diff>""")
    assert _downloads.icon_total(_icon_world(3, ("m", *readded))) == 3
    assert _downloads.icon_total(_icon_world(3), "new", readded) == 3


def test_the_output_says_the_count_can_err_low(world, tmp_path, capsys):
    _zip(tmp_path, "a.zip", {"x/content.xml": _manifest("new_mod", name="M")})
    _, out, _ = _run(capsys, tmp_path)
    assert "can err low" in out


def _two_icon_mods(folder):
    _zip(folder, "a.zip", {"x/content.xml": _manifest("m_one", name="One"), "x/libraries/icons.xml": _icon_xml("p")})
    _zip(folder, "b.zip", {"y/content.xml": _manifest("m_two", name="Two"), "y/libraries/icons.xml": _icon_xml("q")})


def test_two_mods_each_under_but_together_over_warn_on_the_combined_total(world, tmp_path, capsys, monkeypatch):
    base = _downloads.ICONS_LOADED - 1
    _with_world(monkeypatch, _icon_world(base))
    _two_icon_mods(tmp_path)
    _, report = _report(capsys, tmp_path)
    assert _warn(report, "m_one") == [] and _warn(report, "m_two") == []
    combined = report["icons_combined"]
    assert combined["mods"] == 2 and combined["total_after"] == base + 2 and combined["unknown"] == 0
    assert combined["warning"]["kind"] == "icons" and "unmeasured" in combined["warning"]["text"]
    _, out, _ = _run(capsys, tmp_path)
    assert "together" in out.split("WARNINGS:")[1].split("\n\n")[0]


def test_two_mods_together_under_do_not_warn_and_still_print_the_total(world, tmp_path, capsys, monkeypatch):
    """Twin: one short of the combined edge."""
    base = _downloads.ICONS_LOADED - 2
    _with_world(monkeypatch, _icon_world(base))
    _two_icon_mods(tmp_path)
    _, report = _report(capsys, tmp_path)
    assert report["icons_combined"]["total_after"] == base + 2 and report["icons_combined"]["warning"] is None
    _, out, _ = _run(capsys, tmp_path)
    assert "WARNINGS" not in out and f"{base + 2} merged icon names" in out


def test_the_combined_total_counts_only_the_newest_copy_and_only_what_would_install(world, tmp_path, capsys, monkeypatch):
    _with_world(monkeypatch, _icon_world(5))
    _zip(tmp_path, "old.zip", {"x/content.xml": _manifest("m_one", "100", name="One"),
                               "x/libraries/icons.xml": _icon_xml("p", "old_only")})
    _zip(tmp_path, "new.zip", {"x/content.xml": _manifest("m_one", "200", name="One"),
                               "x/libraries/icons.xml": _icon_xml("p")})
    _zip(tmp_path, "two.zip", {"y/content.xml": _manifest("m_two", name="Two"),
                               "y/libraries/icons.xml": _icon_xml("q")})
    _zip(tmp_path, "same.zip", {"folder_a/content.xml": _manifest("mod_a"),
                                "folder_a/libraries/icons.xml": _icon_xml("same_only")})
    _, report = _report(capsys, tmp_path)
    assert report["icons_combined"]["mods"] == 2 and report["icons_combined"]["total_after"] == 5 + 2


def test_one_mod_with_icons_has_no_combined_total(world, tmp_path, capsys, monkeypatch):
    """Twin: its own row already says it."""
    _with_world(monkeypatch, _icon_world(5))
    _zip(tmp_path, "a.zip", {"x/content.xml": _manifest("m_one", name="One"), "x/libraries/icons.xml": _icon_xml("p")})
    _, report = _report(capsys, tmp_path)
    assert report["icons_combined"] is None


def test_the_combined_total_says_what_it_left_out(world, tmp_path, capsys, monkeypatch):
    _with_world(monkeypatch, _icon_world(5))
    _two_icon_mods(tmp_path)
    _zip(tmp_path, "c.zip", {"z/ext_01.cat": b"", "z/content.xml": _manifest("m_three", name="Three")})
    _, report = _report(capsys, tmp_path)
    assert report["icons_combined"]["unknown"] == 1
    _, out, _ = _run(capsys, tmp_path)
    assert "1 whose count is unknown" in out
