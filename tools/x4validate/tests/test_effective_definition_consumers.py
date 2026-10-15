"""Source presence must not certify an effective definition after removal."""
import gzip
import io

import pytest
from lxml import etree

from x4validate import _check, _effective, _merge, _refs, _savecli
from test_reference_scope import _write_cat


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("mode", ["remove", "replace"])
def test_lazy_bulk_and_reference_consumers_use_merged_definition(tmp_path, packed, mode):
    ref, mod = tmp_path / "reference", tmp_path / "overlay"
    rel = "assets/props/macros/probe.xml"
    put(ref / rel, '<macros><macro name="removed_macro"/></macros>')
    operation = ('<remove sel="/macros/macro"/>' if mode == "remove" else
                 '<replace sel="/macros/macro"><macro name="replacement_macro"/></replace>')
    diff = "<diff>" + operation + "</diff>"
    if packed:
        _write_cat(mod, "ext_01.cat", [(rel, diff.encode())])
    else:
        put(mod / rel, diff)
    config = _merge.Config(reference=ref, overlays=(mod,), include_packed_dlc=False)
    defs = _check.EntityDefs(config)
    assert "removed_macro" not in defs
    assert "removed_macro" not in defs.all_names()
    if mode == "replace":
        assert "replacement_macro" in defs and "replacement_macro" in defs.all_names()
    introduced = etree.fromstring(b'<wares><ware id="probe"><component ref="removed_macro"/></ware></wares>')
    assert [d.ref for d in _refs.find_dangling(introduced, set(), set(), defs)] == ["removed_macro"]


def test_nested_removal_and_full_override_do_not_leave_source_names(tmp_path):
    ref, owner, patcher = (tmp_path / name for name in ("reference", "owner", "patcher"))
    ref.mkdir()
    rel = "assets/m.xml"
    put(owner / rel, '<macros><macro name="old_macro"/></macros>')
    put(patcher / "extensions/owner" / rel, '<diff><remove sel="/macros/macro[@name=\'old_macro\']"/></diff>')
    config = _merge.Config(reference=ref, overlays=(owner, patcher), include_packed_dlc=False)
    assert "old_macro" not in _check.EntityDefs(config).all_names()
    put(patcher / rel, '<macros><macro name="new_macro"/></macros>')
    assert _check.EntityDefs(config).all_names() == {"new_macro"}


def test_save_check_uses_effective_set_and_missing_reference_is_nonanswer(tmp_path, monkeypatch):
    ref, mod = tmp_path / "reference", tmp_path / "overlay"
    put(ref / "assets/a.xml", '<macros><macro name="old_macro"/></macros>')
    put(mod / "assets/a.xml", '<diff><remove sel="/macros/macro"/></diff>')
    save = tmp_path / "save.xml.gz"
    save.write_bytes(gzip.compress(b'<savegame><component macro="old_macro"/></savegame>'))
    config = _merge.Config(reference=ref, overlays=(mod,), include_packed_dlc=False)
    monkeypatch.setattr(_savecli._registry, "mods", lambda *a: [])
    monkeypatch.setattr(_savecli._merge, "Config", lambda **k: config)
    out = io.StringIO()
    assert _savecli.cmd_check(save, out=out) == 1
    assert "old_macro" in out.getvalue()
    config.reference = tmp_path / "missing"
    with pytest.raises(_effective.EmptyReference):
        _savecli.cmd_check(save, out=io.StringIO())
