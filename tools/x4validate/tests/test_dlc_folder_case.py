"""F284: DLC folder names must be recognised case-insensitively.

`Config.dlc_dirs` (unpacked list and packed supplement), `_registry.scan_installed`,
`_compat._strip_nesting` and `_resolve._Roots.folder` each tested
`name.startswith("ego_dlc_")` on their own. MEASURED by release review P (2026-10-12):
a reference folder named `EGO_DLC_Terran` was not a DLC to `dlc_dirs`, so
load_order_oracle's DLC exemption missed it; `scan_installed` listed the same folder
as a MOD. One helper, `_merge.is_dlc_folder`, now answers the question everywhere.

Every positive here has a twin that must stay excluded, so a helper that matched
everything (or a substring) goes red.
"""

from __future__ import annotations

import ast
from pathlib import Path

from x4validate import _compat, _merge, _registry, _resolve


def _mkdlc(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "content.xml").write_text(f'<content id="{name.lower()}" version="1"/>',
                                   encoding="utf-8")
    return d


# --- the helper ---------------------------------------------------------------

def test_is_dlc_folder_is_case_insensitive_and_a_prefix_test():
    assert _merge.is_dlc_folder("ego_dlc_terran")
    assert _merge.is_dlc_folder("EGO_DLC_Terran")
    assert _merge.is_dlc_folder("Ego_Dlc_mini_01")
    # twins: a substring, a missing underscore, and the bare prefix's look-alikes
    assert not _merge.is_dlc_folder("my_ego_dlc_x")
    assert not _merge.is_dlc_folder("ego_dlcx")
    assert not _merge.is_dlc_folder("ego_dlc")
    assert not _merge.is_dlc_folder("")


# --- Config.dlc_dirs: unpacked list --------------------------------------------

def test_a_mixed_case_reference_DLC_is_a_DLC(tmp_path):
    ext = tmp_path / "reference" / "extensions"
    _mkdlc(ext, "EGO_DLC_Terran")
    _mkdlc(ext, "ego_dlc_split")
    _mkdlc(ext, "my_ego_dlc_x")   # twin: not a DLC
    _mkdlc(ext, "ego_dlcx")       # twin: not a DLC
    cfg = _merge.Config(reference=tmp_path / "reference", include_packed_dlc=False)
    names = [p.name for p in cfg.dlc_dirs()]
    assert sorted(names, key=str.lower) == ["ego_dlc_split", "EGO_DLC_Terran"], names


# --- Config.dlc_dirs: packed supplement ----------------------------------------

def _packed_world(tmp_path, monkeypatch, ref_names, game_names):
    ref = tmp_path / "reference"
    (ref / "extensions").mkdir(parents=True)
    for n in ref_names:
        _mkdlc(ref / "extensions", n)
    game = tmp_path / "game"
    (game / "extensions").mkdir(parents=True)
    for n in game_names:
        _mkdlc(game / "extensions", n)
    monkeypatch.setattr(_merge, "GAME_ROOT", game)
    monkeypatch.setattr(_merge, "is_configured_reference", lambda r: True)
    monkeypatch.setattr(_merge._cat, "is_packed", lambda p: True)
    return _merge.Config(reference=ref)


def test_a_mixed_case_PACKED_DLC_is_supplemented(tmp_path, monkeypatch):
    cfg = _packed_world(tmp_path, monkeypatch, ["ego_dlc_split"],
                        ["EGO_DLC_Mini_01", "my_ego_dlc_x"])
    got = [(p.parent.parent.name, p.name) for p in cfg.dlc_dirs()]
    assert ("game", "EGO_DLC_Mini_01") in got, got
    assert all(name != "my_ego_dlc_x" for _, name in got), got   # twin
    assert len(got) == 2, got


def test_a_packed_DLC_already_unpacked_is_not_double_counted(tmp_path, monkeypatch):
    """Twin of the supplement: an unpacked copy (in either casing) always wins."""
    cfg = _packed_world(tmp_path, monkeypatch, ["ego_dlc_mini_01", "EGO_DLC_Terran"],
                        ["EGO_DLC_Mini_01", "ego_dlc_terran"])
    got = [(p.parent.parent.name, p.name.lower()) for p in cfg.dlc_dirs()]
    assert sorted(got) == [("reference", "ego_dlc_mini_01"),
                           ("reference", "ego_dlc_terran")], got


# --- scan_installed -----------------------------------------------------------

def test_scan_installed_routes_a_mixed_case_DLC_to_dlc_not_mods(tmp_path):
    base = tmp_path / "ext"
    _mkdlc(base, "EGO_DLC_Terran")
    _mkdlc(base, "my_ego_dlc_x")   # twin: a MOD whose name merely contains the prefix
    dlc: list[dict] = []
    mods = _registry.scan_installed([base], dlc=dlc)
    assert [m["folder"] for m in mods] == ["my_ego_dlc_x"]
    assert [d["folder"] for d in dlc] == ["EGO_DLC_Terran"]


# --- the two other call sites ---------------------------------------------------

def test_strip_nesting_leaves_a_mixed_case_DLC_path_alone():
    assert _compat._strip_nesting("extensions/EGO_DLC_Terran/md/x.xml") == \
        "extensions/ego_dlc_terran/md/x.xml"
    # twin: a mod-nested path IS stripped
    assert _compat._strip_nesting("extensions/my_ego_dlc_x/md/x.xml") == "md/x.xml"


def test_resolver_finds_a_mixed_case_reference_DLC(tmp_path):
    ext = tmp_path / "reference" / "extensions"
    d = _mkdlc(ext, "EGO_DLC_Terran")
    cfg = _merge.Config(reference=tmp_path / "reference", include_packed_dlc=False)
    roots = _resolve._Roots(cfg, (), None, "index/macros.xml")
    assert roots.folder("ego_dlc_terran", tmp_path / "beside" / "m") == d
    # twin: an unknown DLC is still unresolved (never borrowed from a mod folder)
    assert roots.folder("ego_dlc_boron", tmp_path / "beside" / "m") is None


# --- the source guard ---------------------------------------------------------

def _tool_sources():
    here = Path(__file__).resolve().parent.parent
    for sub in ("x4validate", "gates", "audit"):
        yield from sorted((here / sub).glob("*.py"))
    yield from sorted((here.parent / "basex").glob("*.py"))


def _is_lowered(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("lower", "casefold"))


def test_only_is_dlc_folder_tests_the_DLC_prefix_on_an_unlowered_value():
    """A `.startswith("ego_dlc...")` whose receiver is not syntactically `.lower()`-ed
    is a case-sensitive DLC test -- the F284 shape. Five such sites existed at
    e144c5e (two in `_merge.dlc_dirs`, `_registry.scan_installed`,
    `_compat._strip_nesting`, `_resolve._Roots.folder`), two of them on a value
    lowered several lines earlier; the guard cannot see that, so it asks for the
    helper instead of a dataflow argument."""
    offenders = []
    for src in _tool_sources():
        tree = ast.parse(src.read_text(encoding="utf-8"))
        allowed = set()
        for fn in ast.walk(tree):
            if (isinstance(fn, ast.FunctionDef) and fn.name == "is_dlc_folder"
                    and src.name == "_merge.py"):
                allowed |= {id(n) for n in ast.walk(fn)}
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "startswith" and n.args
                    and isinstance(n.args[0], ast.Constant)
                    and isinstance(n.args[0].value, str)
                    and n.args[0].value.lower().startswith("ego_dlc")):
                continue
            if id(n) in allowed or _is_lowered(n.func.value):
                continue
            offenders.append(f"{src.parent.name}/{src.name}:{n.lineno}")
    assert not offenders, (
        "case-sensitive DLC-prefix tests (use _merge.is_dlc_folder):\n  "
        + "\n  ".join(offenders))
