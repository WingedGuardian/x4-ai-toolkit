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


def test_surviving_index_registration_cannot_resurrect_a_removed_definition(tmp_path):
    ref, mod = tmp_path / "reference", tmp_path / "overlay"
    put(ref / "index/macros.xml", '<index><entry name="old_macro" value="assets/a"/></index>')
    put(ref / "assets/a.xml", '<macros><macro name="old_macro"/></macros>')
    put(mod / "assets/a.xml", '<diff><remove sel="/macros/macro"/></diff>')
    config = _merge.Config(reference=ref, overlays=(mod,), include_packed_dlc=False)
    defs = _check.EntityDefs(config)
    assert "old_macro" not in defs
    assert "old_macro" not in defs.all_names()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_nonascii_and_entity_encoded_definition_candidates_are_not_dropped(tmp_path, encoding):
    ref = tmp_path / "reference"
    file = ref / "assets/a.xml"
    file.parent.mkdir(parents=True)
    file.write_bytes(('<?xml version="1.0" encoding="' + encoding + '"?>'
                     '<macros><macro name="雪_macro"/><macro name="snow_&#38634;"/></macros>').encode(encoding))
    defs = _check.EntityDefs(_merge.Config(reference=ref, include_packed_dlc=False))
    assert "雪_macro" in defs and "snow_雪" in defs
    assert {"雪_macro", "snow_雪"} <= defs.all_names()


def test_registered_packed_definition_uses_targeted_document_not_corpus(tmp_path):
    ref, mod = tmp_path / "reference", tmp_path / "owner"
    put(ref / "index/macros.xml", '<index/>')
    _write_cat(mod, "ext_01.cat", [
        ("index/macros.xml", b'<index><entry name="packed_macro" value="assets/m"/></index>'),
        ("assets/m.xml", b'<macros><macro name="packed_macro"/></macros>')])
    defs = _check.EntityDefs(_merge.Config(reference=ref, overlays=(mod,), include_packed_dlc=False))
    assert "packed_macro" in defs
    assert not defs.corpus_scanned


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


# --- V2 (release review): bulk definition set merges only PATCHED documents --------

def test_all_names_merges_only_documents_something_patches(tmp_path):
    """d6cc98a/ded0a21 built a merged tree for EVERY candidate document (MEASURED live:
    ~19,763 documents, ~7 min vs 19.4 s before, F65). A document only ONE source ships,
    as a full file, IS its effective tree: parsed straight from the bytes already read."""
    ref, mod = tmp_path / "reference", tmp_path / "overlay"
    for i in range(4):
        put(ref / f"assets/a{i}.xml", f'<macros><macro name="m{i}"/></macros>')
    put(mod / "assets/a0.xml", '<diff><replace sel="/macros/macro/@name">m0_new</replace></diff>')
    put(mod / "assets/own.xml", '<components><component name="own_c"/></components>')
    put(mod / "assets/a1.xml", '<macros><macro name="m1_full"/></macros>')   # full override
    defs = _check.EntityDefs(_merge.Config(reference=ref, overlays=(mod,),
                                           include_packed_dlc=False))
    assert defs.all_names() == {"m0_new", "m1_full", "m2", "m3", "own_c"}
    assert defs.merged_documents == 2, (defs.merged_documents, defs.direct_documents)
    assert defs.direct_documents == 3


def test_TWIN_a_lone_DIFF_document_is_still_merged_never_read_raw(tmp_path):
    ref, mod = tmp_path / "reference", tmp_path / "overlay"
    ref.mkdir()
    put(mod / "assets/x.xml", '<diff><add sel="/macros"><macro name="ghost"/></add></diff>')
    defs = _check.EntityDefs(_merge.Config(reference=ref, overlays=(mod,),
                                           include_packed_dlc=False))
    assert "ghost" not in defs.all_names()      # nothing to patch: no definition
    assert defs.direct_documents == 0


# --- V9 (release review): definition lookup is case-insensitive like the engine -----

def test_a_registration_whose_file_spells_the_name_in_another_case_is_found(tmp_path):
    """MEASURED live: 40 of 1,500 sampled index registrations (XU_EP2_universe_macro)
    are defined only under a different capitalisation; the old index lookup found them."""
    ref = tmp_path / "reference"
    put(ref / "index/macros.xml",
        '<index><entry name="XU_EP2_universe_macro" value="maps/xu_ep2_universe/macro"/></index>')
    put(ref / "maps/xu_ep2_universe/macro.xml",
        '<macros><macro name="xu_ep2_universe_macro"/></macros>')
    put(ref / "assets/u.xml", '<macros><macro name="Unregistered_Macro"/></macros>')
    defs = _check.EntityDefs(_merge.Config(reference=ref, include_packed_dlc=False))
    assert "XU_EP2_universe_macro" in defs
    assert "unregistered_macro" in defs          # corpus tier, other case


def test_TWIN_exact_case_is_still_found_and_an_absent_name_still_is_not(tmp_path):
    ref = tmp_path / "reference"
    put(ref / "index/macros.xml", '<index><entry name="a_macro" value="assets/a"/></index>')
    put(ref / "assets/a.xml", '<macros><macro name="a_macro"/></macros>')
    defs = _check.EntityDefs(_merge.Config(reference=ref, include_packed_dlc=False))
    assert "a_macro" in defs
    assert "b_macro" not in defs and "A_MACRO_X" not in defs


def test_TWIN_a_lone_mod_on_mod_FULL_file_is_merged_never_read_raw(tmp_path):
    """MEASURED live: full files at extensions/<owner>/<a path the owner lacks> merge to
    no tree; reading them raw would have invented 19 definitions."""
    ref, owner, patcher = (tmp_path / n for n in ("reference", "owner", "patcher"))
    ref.mkdir()
    put(owner / "assets/props/m.xml", '<macros><macro name="real_macro"/></macros>')
    put(patcher / "extensions/owner/assets/prop/m.xml", '<macros><macro name="stray_macro"/></macros>')
    config = _merge.Config(reference=ref, overlays=(owner, patcher), include_packed_dlc=False)
    defs = _check.EntityDefs(config)
    before = {k for k in defs.all_names()}
    merged_only = _check.EntityDefs(config)
    merged_only._direct_root = lambda data: None          # the pre-V2 behaviour
    assert before == merged_only.all_names()
