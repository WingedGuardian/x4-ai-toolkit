"""The two non-xref consumers of `_xref._iter_mod_files`, after it began yielding
extension-nested patches (`extensions/<owner>/md|aiscripts/...`).

* `_exprlint.scan_mod` SHOULD lint them: a nested `<diff>` carries script expressions
  exactly like a direct one, and the engine evaluates them once applied.
* `_check._script_name_map` resolves an engine error that names a SCRIPT back to this
  mod's file, and that error then GATES the mod. It stays DIRECT-ONLY:
  - a nested file must never displace a direct script of the same name (the loose walk
    sorts `aiscripts/` < `extensions/` < `md/`, so last-wins let a nested aiscript
    override, and a direct md override a nested one, depending on the alphabet);
  - a FULL `<mdscript>`/`<aiscript>` at a nested path is excluded altogether. What the
    engine does with one is UNMEASURED for scripts: the KB records full non-diff files
    overriding cross-mod for MACROS (engine-confirmed), and gotcha #21 records that a
    duplicate full mdscript at a vanilla path never takes effect. Mapping its name would
    gate THIS mod on errors in a script the owner also defines, on an inferred rule.
    MEASURED on a real install: 72 of 72 nested script files are `<diff>` roots, so the
    excluded class is empty there today.
"""

from __future__ import annotations

from pathlib import Path

from x4validate import _check, _exprlint


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _mod(tmp_path: Path, files: dict[str, str]) -> Path:
    mod = tmp_path / "moda"
    _w(mod / "content.xml", '<content id="moda" version="1"/>')
    for rel, text in files.items():
        _w(mod / rel, text)
    return mod


def test_exprlint_lints_a_nested_patch(tmp_path):
    mod = _mod(tmp_path, {"extensions/ownerb/md/p.xml":
                          '<diff><add sel="//cues"><a exact="$s.{random(1,$s.count)}"/></add></diff>'})
    hits = [(f.vpath, f.rule_id) for f in _exprlint.scan_mod(mod)]
    assert hits == [("extensions/ownerb/md/p.xml", "random_call")]


def test_twin_exprlint_still_lints_a_direct_script(tmp_path):
    mod = _mod(tmp_path, {"md/p.xml": '<mdscript name="P"><a exact="$s.{random(1,$s.count)}"/></mdscript>'})
    assert [(f.vpath, f.rule_id) for f in _exprlint.scan_mod(mod)] == [("md/p.xml", "random_call")]


def test_name_map_direct_aiscript_wins_over_a_nested_one(tmp_path):
    mod = _mod(tmp_path, {
        "aiscripts/s.xml": '<aiscript name="shared.name"/>',
        "extensions/ownerb/aiscripts/s.xml": '<aiscript name="shared.name"/>',
    })
    assert _check._script_name_map(mod) == {"shared.name": "aiscripts/s.xml"}


def test_name_map_direct_mdscript_wins_over_a_nested_one(tmp_path):
    mod = _mod(tmp_path, {
        "md/s.xml": '<mdscript name="Shared"/>',
        "extensions/ownerb/md/s.xml": '<mdscript name="Shared"/>',
    })
    assert _check._script_name_map(mod) == {"Shared": "md/s.xml"}


def test_name_map_excludes_a_nested_full_script(tmp_path):
    mod = _mod(tmp_path, {"extensions/ownerb/md/only.xml": '<mdscript name="OnlyNested"/>'})
    assert _check._script_name_map(mod) == {}


def test_twin_name_map_keeps_direct_scripts(tmp_path):
    mod = _mod(tmp_path, {"md/a.xml": '<mdscript name="A"/>',
                          "aiscripts/b.xml": '<aiscript name="b"/>',
                          "md/d.xml": '<diff/>'})
    assert _check._script_name_map(mod) == {"A": "md/a.xml", "b": "aiscripts/b.xml"}
