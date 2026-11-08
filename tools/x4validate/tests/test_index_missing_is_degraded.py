"""BLIND-SPOTS F364: a missing base index must degrade the report, never register nothing in silence.

The quiet shape is the dangerous one: `_read_source` returns None for a file that is not there, and until
2026-11-08 `build_index` just `continue`d. A run against a wrong reference then reported every vanilla
macro as unregistered (or, for a check that skips unresolvable items, reported fewer findings) and looked
clean. Twin per clause: missing reference index -> degraded skip; missing DLC index -> plain skip; both
present -> no skip; report=None -> no crash."""

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


def test_none_report_does_not_crash(tmp_path):
    ref = tmp_path / "reference"
    ref.mkdir()
    assert _resolve.build_index(_merge.Config(reference=ref, include_packed_dlc=False), [], _resolve.MACRO_INDEX, None) == {}
