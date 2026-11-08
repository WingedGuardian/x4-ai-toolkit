"""BLIND-SPOTS F364: a missing base index must degrade the report, never register nothing in silence.

The quiet shape is the dangerous one: `_read_source` returns None for a file that is not there, and until
2026-11-08 `build_index` just `continue`d. A run against a wrong reference then reported every vanilla
macro as unregistered (or, for a check that skips unresolvable items, reported fewer findings) and looked
clean. Twin per clause: reference with no index folder (or no folder at all) -> degraded skip; reference
with index/ but one file missing -> plain skip (a minimal tree, as test fixtures build); missing DLC index ->
plain skip; present -> no skip; report=None -> no crash."""

from pathlib import Path

from x4validate import _check, _merge, _resolve


def _w(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_missing_reference_index_is_a_degraded_skip(tmp_path):
    ref = tmp_path / "reference"
    ref.mkdir()  # exists, but ships no index/macros.xml
    rep = _check.Report()
    idx = _resolve.build_index(_merge.Config(reference=ref, include_packed_dlc=False), [], _resolve.MACRO_INDEX, rep)
    assert idx == {}
    assert any(s.degraded and "base index" in s.why and "macros.xml" in s.why for s in rep.skipped)


def test_nonexistent_reference_is_a_degraded_skip(tmp_path):
    """The MEASURED F364 case: a worktree without x4-paths.env resolved `reference` to a missing folder."""
    rep = _check.Report()
    _resolve.build_index(_merge.Config(reference=tmp_path / "nowhere", include_packed_dlc=False), [], _resolve.MACRO_INDEX, rep)
    assert any(s.degraded and "no index folder" in s.why for s in rep.skipped)


def test_partial_reference_index_is_a_plain_skip(tmp_path):
    """Twin: index/ exists with macros.xml only; building the COMPONENT index reports, but does not degrade."""
    ref = tmp_path / "reference"
    _w(ref / "index/macros.xml", "<index/>")
    rep = _check.Report()
    _resolve.build_index(_merge.Config(reference=ref, include_packed_dlc=False), [], _resolve.COMPONENT_INDEX, rep)
    hits = [s for s in rep.skipped if "components.xml" in s.why]
    assert len(hits) == 1 and not hits[0].degraded


def test_present_reference_index_records_no_skip(tmp_path):
    ref = tmp_path / "reference"
    _w(ref / "index/macros.xml", '<index><entry name="m" value="assets/m"/></index>')
    rep = _check.Report()
    idx = _resolve.build_index(_merge.Config(reference=ref, include_packed_dlc=False), [], _resolve.MACRO_INDEX, rep)
    assert "m" in idx
    assert [s for s in rep.skipped if "resolution" in s.what] == []


def test_missing_dlc_index_is_a_plain_skip(tmp_path, monkeypatch):
    ref = tmp_path / "reference"
    _w(ref / "index/macros.xml", "<index/>")
    dlc = ref / "extensions" / "ego_dlc_fake"
    dlc.mkdir(parents=True)
    cfg = _merge.Config(reference=ref, include_packed_dlc=False)
    monkeypatch.setattr(type(cfg), "dlc_dirs", lambda self: [dlc])
    rep = _check.Report()
    _resolve.build_index(cfg, [], _resolve.MACRO_INDEX, rep)
    hits = [s for s in rep.skipped if "ego_dlc_fake" in s.why]
    assert len(hits) == 1 and not hits[0].degraded


def _resolution_skips(rep):
    return [s for s in rep.skipped if s.what.endswith("resolution")]


def test_three_checks_record_one_skip_per_missing_index(tmp_path):
    """One cause, one line: check_file_existence, check_connections and check_fitting_compatibility each
    build both indexes, so an undeduplicated record gave 6 degraded skips for 2 missing files."""
    rep = _check.Report()
    cfg = _merge.Config(reference=tmp_path / "nowhere", include_packed_dlc=False)
    mod = tmp_path / "mod"
    mod.mkdir()
    for check in (_check.check_file_existence, _check.check_connections, _check.check_fitting_compatibility):
        check(mod, cfg, rep)
    skips = _resolution_skips(rep)
    assert len(skips) == 2 and all(s.degraded for s in skips), skips
    assert {("macros.xml" in s.why, "components.xml" in s.why) for s in skips} == {(True, False), (False, True)}


def test_partial_reference_skip_is_deduplicated_too(tmp_path):
    """Twin for the plain (index folder exists) branch: two builds, one record."""
    ref = tmp_path / "reference"
    _w(ref / "index/macros.xml", "<index/>")
    cfg = _merge.Config(reference=ref, include_packed_dlc=False)
    rep = _check.Report()
    for _ in range(2):
        _resolve.build_index(cfg, [], _resolve.COMPONENT_INDEX, rep)
    hits = _resolution_skips(rep)
    assert len(hits) == 1 and not hits[0].degraded


def test_missing_dlc_skip_is_deduplicated_too(tmp_path, monkeypatch):
    """Twin for the DLC branch: two builds, one record."""
    ref = tmp_path / "reference"
    _w(ref / "index/macros.xml", "<index/>")
    dlc = ref / "extensions" / "ego_dlc_fake"
    dlc.mkdir(parents=True)
    cfg = _merge.Config(reference=ref, include_packed_dlc=False)
    monkeypatch.setattr(type(cfg), "dlc_dirs", lambda self: [dlc])
    rep = _check.Report()
    for _ in range(2):
        _resolve.build_index(cfg, [], _resolve.MACRO_INDEX, rep)
    assert len([s for s in rep.skipped if "ego_dlc_fake" in s.why]) == 1


def test_distinct_causes_are_not_merged(tmp_path):
    """Twin against over-deduplication: the macro and component index are two causes, two lines."""
    rep = _check.Report()
    cfg = _merge.Config(reference=tmp_path / "nowhere", include_packed_dlc=False)
    _resolve.build_index(cfg, [], _resolve.MACRO_INDEX, rep)
    _resolve.build_index(cfg, [], _resolve.COMPONENT_INDEX, rep)
    assert len(_resolution_skips(rep)) == 2


def test_none_report_does_not_crash(tmp_path):
    ref = tmp_path / "reference"
    ref.mkdir()
    assert _resolve.build_index(_merge.Config(reference=ref, include_packed_dlc=False), [], _resolve.MACRO_INDEX, None) == {}
