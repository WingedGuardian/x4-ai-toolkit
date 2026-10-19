"""gates/tool_properties.py: shared full files under t/ and index/ (release review F7).

2b1a902 excused EVERY shared full file under t/ and index/ as union-merged, so the
completeness property could no longer go red for those directories -- a real duplicate
key between two mods shipping the same complete file was waved through. The gate now
excuses such a file only when the sharing mods' keys are disjoint: (page id, t id) for
t/, the entry key (tag + id-or-name, as x4compat keys union children) for index/. A
clash, or a file it cannot read, is NOT excused and must be reported by x4compat.
"""
from __future__ import annotations

import pytest


def _gate(gate_install):
    mod = gate_install.load("tool_properties")
    mod.failures.clear()
    mod.skips.clear()
    return mod


def _ship(ext, folder, vpath, text):
    p = ext / folder / vpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


T_A = '<language id="44"><page id="9001"><t id="1">a</t><t id="2">b</t></page></language>'
T_B_DISJOINT = '<language id="44"><page id="9001"><t id="3">c</t></page><page id="9002"><t id="1">d</t></page></language>'
T_B_CLASH = '<language id="44"><page id="9001"><t id="2">other</t></page></language>'
IDX_A = '<index><entry name="ship_a_macro" value="a"/></index>'
IDX_B_DISJOINT = '<index><entry name="ship_b_macro" value="b"/></index>'
IDX_B_CLASH = '<index><entry name="ship_a_macro" value="b2"/></index>'


@pytest.mark.parametrize("vpath,a,b", [("t/0001-l044.xml", T_A, T_B_DISJOINT),
                                        ("index/macros.xml", IDX_A, IDX_B_DISJOINT)])
def test_a_shared_union_file_with_DISJOINT_keys_is_excused(gate_install, tmp_path, vpath, a, b):
    tp = _gate(gate_install)
    ext = tmp_path / "ext"
    _ship(ext, "mod_a", vpath, a)
    _ship(ext, "mod_b", vpath, b)
    excused, clashes = tp._union_dir_excused({vpath: ["mod_a", "mod_b"]}, ext)
    assert excused == {vpath} and clashes == {}, clashes


@pytest.mark.parametrize("vpath,a,b,key", [
    ("t/0001-l044.xml", T_A, T_B_CLASH, "page 9001 t 2"),
    ("index/macros.xml", IDX_A, IDX_B_CLASH, "entry#ship_a_macro")])
def test_TWIN_a_shared_union_file_with_a_DUPLICATE_key_is_NOT_excused(
        gate_install, tmp_path, vpath, a, b, key):
    """The falsification twin: the clash must survive to the completeness check, where
    only an x4compat report of it can explain the shared file."""
    tp = _gate(gate_install)
    ext = tmp_path / "ext"
    _ship(ext, "mod_a", vpath, a)
    _ship(ext, "mod_b", vpath, b)
    excused, clashes = tp._union_dir_excused({vpath: ["mod_a", "mod_b"]}, ext)
    assert excused == set()
    assert any(key in c for c in clashes[vpath]), clashes


def test_TWIN_an_UNREADABLE_shared_union_file_is_NOT_excused(gate_install, tmp_path):
    tp = _gate(gate_install)
    ext = tmp_path / "ext"
    _ship(ext, "mod_a", "t/0001-l044.xml", T_A)
    _ship(ext, "mod_b", "t/0001-l044.xml", "<language><page id=")
    excused, clashes = tp._union_dir_excused({"t/0001-l044.xml": ["mod_a", "mod_b"]}, ext)
    assert excused == set() and "unreadable" in " ".join(clashes["t/0001-l044.xml"])


def test_libraries_files_are_not_this_helpers_business(gate_install, tmp_path):
    """libraries/ keeps its explicit union list; the helper never excuses one."""
    tp = _gate(gate_install)
    ext = tmp_path / "ext"
    lib = '<wares><ware id="x"/></wares>'
    _ship(ext, "mod_a", "libraries/wares.xml", lib)
    _ship(ext, "mod_b", "libraries/wares.xml", '<wares><ware id="y"/></wares>')
    excused, clashes = tp._union_dir_excused({"libraries/wares.xml": ["mod_a", "mod_b"]}, ext)
    assert excused == set() and clashes == {}
