"""A mod `mods("active")` leaves out must be DISCLOSED by every caller that renders.

AUDIT-2026-09-24 final review item 2: `_active_filter` records each exclusion (a
REQUIRED dependency missing, disabled or cyclic) only when the caller passes
`dropped=`, and none of the 14 active-scope callers did -- so a mod leaving the
world model said nothing. The engine is right to skip it; the tool staying silent
about it is the defect. Each surfaced caller is driven here with one excluded mod
("needy", requiring an id nothing provides) and must NAME it with its reason.

F139 (2026-09-26) closed the other 16: `mods()` now ALWAYS collects its exclusions
and returns them on the list it hands back (`ModList.dropped`), `_registry.dropped_note`
is the one line every sink prints, and `test_every_active_scope_caller_discloses` bans
an active-scope call in any function that does not reach the channel.

Hermetic: tmp_path install, profile and reference only.
"""
from __future__ import annotations

import ast
import importlib.util
import io
import subprocess
import sys
import types
from pathlib import Path

import pytest

from x4validate import _check, _compat, _effective, _merge, _registry

ROOT = Path(__file__).resolve().parents[1]
BASEX = ROOT.parent / "basex"
GATES = ROOT / "gates"


def _mod(parent: Path, folder: str, deps: tuple[str, ...] = (),
         enabled: bool = True) -> Path:
    d = parent / folder
    d.mkdir(parents=True, exist_ok=True)
    body = "".join(f'<dependency id="{i}"/>' for i in deps)
    en = "" if enabled else ' enabled="0"'
    (d / "content.xml").write_text(
        f'<content id="{folder}" version="100" name="{folder}"{en}>{body}</content>',
        encoding="utf-8")
    return d


@pytest.fixture
def env(tmp_path, monkeypatch):
    ext = tmp_path / "game" / "extensions"
    ext.mkdir(parents=True)
    ref = tmp_path / "reference"
    (ref / "libraries").mkdir(parents=True)
    (ref / "libraries" / "wares.xml").write_bytes(b"<wares/>")
    prof = tmp_path / "profile_content.xml"
    prof.write_text("<content/>", encoding="utf-8")
    monkeypatch.setattr(_registry, "PROFILE_CONTENT", prof)
    monkeypatch.setattr(_registry, "default_installed_dirs", lambda: [ext])
    monkeypatch.setattr(_registry, "_reference_dlc_dirs", lambda config=None: [])
    _mod(ext, "AAA")
    _mod(ext, "needy", deps=("nothing_provides_this",))
    return ext, tmp_path, _merge.Config(reference=ref)


def _names_needy(text: str | None) -> bool:
    return bool(text) and "needy" in text and "nothing_provides_this" in text


def _skips(rep) -> str:
    return "\n".join(f"{s.what} {s.why}" for s in rep.skipped)


# --------------------------------------------------------------- the channel itself

def test_mods_ALWAYS_carries_its_exclusions_without_being_asked(env):
    got = _registry.mods("active")
    assert [m["folder"] for m in got] == ["AAA"]
    assert any(_names_needy(r) for r in got.dropped), got.dropped
    assert _names_needy(_registry.dropped_note(got))


def test_the_legacy_dropped_list_still_receives_the_same_records(env):
    legacy: list[str] = []
    got = _registry.mods("active", dropped=legacy)
    assert legacy == list(got.dropped) and legacy


def test_dropped_note_is_None_when_nothing_was_left_out(env):
    ext, _tmp, _cfg = env
    _mod(ext, "needy")                  # dependency removed: now it loads
    got = _registry.mods("active")
    assert got.dropped == [] and _registry.dropped_note(got) is None
    assert _registry.dropped_note([]) is None, "a plain list (a test stub) is no drop"


def test_left_out_maps_each_folder_to_its_reason(env):
    lo = _registry.left_out(_registry.mods("active"))
    assert set(lo) == {"needy"} and "nothing_provides_this" in lo["needy"]


# ------------------------------------------------------ the four fixed 2026-09-26

def test_tier_b_discloses_the_excluded_mod_as_NOT_CHECKED(env):
    ext, tmp, _cfg = env
    cand = _mod(tmp / "dev", "cand")
    rep = _check.Report()
    tb = _check.tier_b_trees(cand, rep)
    assert "needy" not in [p.name for p in tb.final]
    hits = [s for s in rep.skipped if _names_needy(f"{s.what} {s.why}")]
    assert hits, rep.skipped
    assert not any(s.degraded for s in hits), "the engine's own exclusion is not a degraded run"
    assert any(_names_needy(n) for n in tb.notes), tb.notes


def test_tier_b_twin_says_nothing_when_nothing_is_excluded(env):
    ext, tmp, _cfg = env
    (ext / "needy" / "content.xml").write_text(
        '<content id="needy" version="100" name="needy"/>', encoding="utf-8")
    rep = _check.Report()
    _check.tier_b_trees(_mod(tmp / "dev", "cand"), rep)
    assert not [s for s in rep.skipped if "NOT LOADED" in s.why + s.what], rep.skipped


def test_x4compat_discloses_the_excluded_mod(env):
    ext, _tmp, cfg = env
    rep = _compat.analyze(ext, config=cfg)
    assert "needy" not in rep.load_order
    assert any(_names_needy(f"{s.what} {s.why}") for s in rep.skipped), rep.skipped
    assert _names_needy(_compat.render(rep))


def test_x4effective_build_discloses_the_excluded_mod(env):
    ext, tmp, cfg = env
    said: list[str] = []
    _effective.build(cfg, tmp / "eff.sqlite", dirs=[ext], kinds=("ware",),
                     progress=said.append)
    assert any(_names_needy(s) for s in said), said


def test_basex_build_effective_discloses_the_excluded_mod(env, capsys):
    ext, _tmp, cfg = env
    spec = importlib.util.spec_from_file_location("basex_build_effective_excl",
                                                  BASEX / "build-effective.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    overlays = mod.installed_in_load_order(cfg)
    assert [p.name for p in overlays] == ["AAA"]
    assert _names_needy(capsys.readouterr().err)


# ------------------------------------------------------------ F139: the other 16

def test_F139_an_engine_excluded_target_is_NOT_called_disabled(env):
    """`_check._disabled_folders` was installed MINUS active, so a mod the engine
    leaves out for a missing dependency was reported as 'installed but disabled' --
    a claim about a switch the user never touched."""
    _ext, _tmp, cfg = env
    why = _check._inactive_target_reason("extensions/needy/libraries/wares.xml", cfg)
    assert why is not None, "an inactive target must still be excused"
    assert "nothing_provides_this" in why and "disabled" not in why.lower(), why
    sev, cat, msg = _check._no_base_finding("extensions/needy/libraries/nope.xml", cfg)
    assert (sev, cat) == ("info", "inactive")
    assert "nothing_provides_this" in msg and "DISABLED" not in msg, msg


def test_F139_twin_a_really_disabled_target_still_says_disabled(env):
    ext, _tmp, cfg = env
    _mod(ext, "off", enabled=False)
    why = _check._inactive_target_reason("extensions/off/libraries/wares.xml", cfg)
    assert why is not None and "disabled" in why, why
    _sev, _cat, msg = _check._no_base_finding("extensions/off/libraries/nope.xml", cfg)
    assert "DISABLED" in msg, msg


def test_F139_variant_check_discloses(env):
    _ext, tmp, cfg = env
    rel = "assets/units/size_l/macros"
    d = cfg.reference / rel
    d.mkdir(parents=True)
    for v in ("a", "b"):
        (d / f"ship_ter_x_{v}_macro.xml").write_text("<macros/>", encoding="utf-8")
    mod = tmp / "dev" / "vmod"
    (mod / rel).mkdir(parents=True)
    (mod / rel / "ship_ter_x_a_macro.xml").write_text("<diff/>", encoding="utf-8")
    rep = _check.Report()
    _check.check_variant_consistency(mod, cfg, rep)
    assert _names_needy(_skips(rep)), rep.skipped


def test_F139_xsd_nested_scope_discloses(env, monkeypatch):
    _ext, tmp, cfg = env
    monkeypatch.setattr(_check._xsd, "validate_mod", lambda mod_dir, config: ([], 0, 0))
    monkeypatch.setattr(_check._xsd, "validate_nested_scripts",
                        lambda mod_dir, config, active: types.SimpleNamespace(
                            introduced=[], fixed=[], checked=0, skips=[]))
    rep = _check.Report()
    _check.check_xsd(_mod(tmp / "dev", "cand"), cfg, rep)
    assert _names_needy(_skips(rep)), rep.skipped


def test_F139_a_mod_named_once_across_checks_not_once_per_check(env):
    """The same exclusion reaching three checks in one run is ONE cause."""
    _ext, tmp, cfg = env
    rep = _check.Report()
    for what in ("a", "b", "c"):
        _check.note_not_loaded(_registry.mods("active"), what, rep)
    assert len([s for s in rep.skipped if _names_needy(s.why)]) == 1


def test_F139_x4live_extensions_discloses(env, monkeypatch):
    from x4validate import _livecli
    rows = [{"id": i, "enabled": "true", "egosoftextension": "false"}
            for i in ("AAA", "needy")]
    monkeypatch.setattr(_livecli, "_load",
                        lambda path: types.SimpleNamespace(extensions=lambda: rows))
    out = io.StringIO()
    _livecli.cmd_extensions(None, "active", out=out)
    assert _names_needy(out.getvalue()), out.getvalue()


def test_F139_x4live_helper_deployment_discloses(env):
    from x4validate import _livepipe as lp
    deployed, note = lp.helper_deployment()
    assert deployed is False
    assert _names_needy(note), note
    text = lp._no_connection_reason("p", 1.0, True, None, deployed, None, not_loaded=note)
    assert _names_needy(text), text


def test_F139_x4save_check_discloses(env, monkeypatch, tmp_path):
    import gzip
    from x4validate import _savecli
    monkeypatch.setattr(_savecli._merge, "Config",
                        lambda **k: types.SimpleNamespace(overlays=[], dlc_dirs=lambda: []))
    monkeypatch.setattr(_savecli._effective, "require_base_documents", lambda *a: (1, 0))
    monkeypatch.setattr(_savecli._check, "EntityDefs",
                        lambda cfg, **k: types.SimpleNamespace(all_names=lambda: {"a_macro"}))
    save = tmp_path / "s.xml.gz"
    with gzip.open(save, "wb") as fh:
        fh.write(b'<savegame><universe><component macro="a_macro"/></universe></savegame>')
    out = io.StringIO()
    _savecli.cmd_check(save, out=out)
    assert _names_needy(out.getvalue()), out.getvalue()


def test_F139_x4effective_dump_discloses(env, monkeypatch, capsys):
    from lxml import etree
    from x4validate import _effectivecli as C
    _ext, _tmp, cfg = env
    monkeypatch.setattr(C._merge, "build_effective", lambda *a, **k: types.SimpleNamespace(
        tree=etree.fromstring("<wares/>"), sources=["base"], skipped=[]))
    args = types.SimpleNamespace(reference=str(cfg.reference), vpath="libraries/wares.xml",
                                 chain=False)
    C._cmd_dump(args)
    got = capsys.readouterr()
    assert _names_needy(got.out + got.err), got


def test_F139_x4similar_discloses(env, monkeypatch, capsys):
    from x4validate import _similarity
    ext, _tmp, cfg = env
    monkeypatch.setattr(_merge.Config, "dlc_dirs", lambda self: [])
    _similarity.main(["--reference", str(cfg.reference), "--ext-dir", str(ext)])
    got = capsys.readouterr()
    assert _names_needy(got.out + got.err), got


def test_F139_x4stats_wares_discloses(env, capsys):
    from x4validate import _stats
    ext, tmp, cfg = env
    cand = _mod(tmp / "dev", "cand")
    _stats.main(["wares", str(cand), "--ext-dir", str(ext),
                 "--reference", str(cfg.reference)])
    got = capsys.readouterr()
    assert _names_needy(got.out + got.err), got


# --- the gates: loaded from source under a private name, `_env` pointed at tmp ---

class _Stop(Exception):
    """Raised by a stub once a gate is past its disclosure point."""


def _stop(*_a, **_k):
    raise _Stop


@pytest.fixture
def gate_env(env, monkeypatch):
    ext, tmp, cfg = env
    if str(GATES) not in sys.path:
        monkeypatch.syspath_prepend(str(GATES))
    import _env
    log = tmp / "debug.txt"
    log.write_text("[=ERROR=] 0.00 Cannot find XML file component macro 'x_macro' in "
                   "index 'index\\macros'\n", encoding="utf-8")
    db = tmp / "eff.sqlite"
    db.write_bytes(b"")
    monkeypatch.setattr(_env, "extensions", lambda: ext)
    monkeypatch.setattr(_env, "reference", lambda: cfg.reference)
    monkeypatch.setattr(_env, "effective_db", lambda: db)
    monkeypatch.setattr(_env, "oracle_log", lambda: log)
    return ext, tmp, cfg, log


def _load_gate(name: str, monkeypatch):
    """A private copy, registered (a dataclass needs its module in sys.modules) under a
    name no other test uses, and unregistered afterwards."""
    key = f"f139_gate_{name}"
    spec = importlib.util.spec_from_file_location(key, GATES / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, key, mod)
    spec.loader.exec_module(mod)
    return mod


def _both(capsys) -> str:
    got = capsys.readouterr()
    return got.out + got.err


def test_F139_gate_load_order_oracle_discloses(gate_env, monkeypatch, capsys):
    _ext, _tmp, _cfg, log = gate_env
    g = _load_gate("load_order_oracle", monkeypatch)
    monkeypatch.setattr(g, "_log_path", lambda: log)
    g.main()
    assert _names_needy(_both(capsys))


def test_F139_gate_oracle_index_discloses(gate_env, monkeypatch, capsys):
    g = _load_gate("oracle_index", monkeypatch)
    monkeypatch.setattr(g._merge, "Config", _stop)
    with pytest.raises(_Stop):
        g.main()
    assert _names_needy(_both(capsys))


def test_F139_gate_similar_audit_discloses(gate_env, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=1, stdout="", stderr="stub\n"))
    with pytest.raises(SystemExit):
        _load_gate("similar_audit", monkeypatch)
    assert _names_needy(_both(capsys))


def test_F139_gate_consistency_audit_discloses(gate_env, monkeypatch, capsys):
    g = _load_gate("consistency_audit", monkeypatch)
    g._overlays_for("libraries/wares.xml")
    assert _names_needy(_both(capsys))


def test_F139_gate_oracle_reverse_discloses(gate_env, monkeypatch, capsys):
    g = _load_gate("oracle_reverse", monkeypatch)
    g.overlays_for("libraries/wares.xml")
    assert _names_needy(_both(capsys))


def test_F139_gate_obtainability_audit_discloses(gate_env, monkeypatch, capsys):
    from lxml import etree
    g = _load_gate("obtainability_audit", monkeypatch)
    monkeypatch.setattr(g._merge, "Config", lambda: types.SimpleNamespace())
    monkeypatch.setattr(g._merge, "build_effective", lambda *a, **k: types.SimpleNamespace(
        tree=etree.fromstring("<wares/>")))
    monkeypatch.setattr(g._refs, "deprecated_only_macros", lambda tree: {})
    monkeypatch.setattr(g._effective, "base_vpaths", lambda cfg, pat: {})
    g.audit()
    assert _names_needy(_both(capsys))


def test_F139_gate_tool_properties_discloses(gate_env, monkeypatch, capsys):
    import json
    import sqlite3
    _ext, tmp, _cfg, _log = gate_env
    g = _load_gate("tool_properties", monkeypatch)
    store = tmp / "store.sqlite"
    con = sqlite3.connect(store)
    con.execute("create table mods (folder)")
    con.execute("insert into mods values ('AAA')")
    con.commit()
    con.close()
    tk = tmp / "toolkit"
    man = tk / "tools" / "basex" / "_eff" / "effective-manifest.json"
    man.parent.mkdir(parents=True)
    man.write_text(json.dumps({"overlays_in_load_order": ["AAA"]}), encoding="utf-8")
    monkeypatch.setattr(_effective, "DB_PATH", store)
    # The gate reads the store through the one door (`_effective.effective_db()`), not the
    # import-time DB_PATH snapshot: without this the test passed only on a machine that has
    # a real store, and the cold-clone run caught it.
    monkeypatch.setattr(_effective, "effective_db", lambda *a, **k: store)
    # Point the gate at the stub ONLY: the tree running this test may hold a real,
    # built manifest (master does), which the gate would otherwise prefer.
    monkeypatch.setattr(g, "_manifest_candidates", lambda: [man])
    g.check_mod_scope_agreement()
    assert _names_needy(_both(capsys))


# ------------------------------------------------------------------ the AST ban

SCAN_ROOTS = [ROOT / "x4validate", ROOT / "gates", BASEX]

#: The disclosure API. A function holding an active-scope call must reach one of these:
#:   dropped_note   `_registry`: the one-line disclosure every text sink prints
#:   left_out       `_registry`: folder -> reason, for a caller that must CLASSIFY
#:   note_not_loaded `_check`:   the Report sink (a NOT CHECKED skip per mod, deduplicated)
DISCLOSERS = {"dropped_note", "left_out", "note_not_loaded"}

#: (file, function) pairs allowed to hold a silent active-scope call. EMPTY on purpose:
#: every one of the 20 sites could disclose, so none is excused. Adding one needs a
#: comment here saying why that caller genuinely has no sink.
ALLOWED: set[tuple[str, str]] = set()


def _is_active_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
    if name == "active_mods":
        return True
    if name == "mods" and node.args and isinstance(node.args[0], ast.Constant) \
            and node.args[0].value == "active":
        if isinstance(f, ast.Attribute):
            return isinstance(f.value, ast.Name) and f.value.id.endswith("_registry")
        return True
    return False


def _discloses(nodes: list[ast.AST]) -> bool:
    for top in nodes:
        for n in ast.walk(top):
            if isinstance(n, ast.Call):
                f = n.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                if name in DISCLOSERS:
                    return True
                if _is_active_call(n):
                    # passes the legacy list: dropped=..., or positionally
                    # (mods(scope, dirs, dropped) / active_mods(dirs, dropped))
                    if any(k.arg == "dropped" for k in n.keywords):
                        return True
                    if len(n.args) >= (3 if name == "mods" else 2):
                        return True
            if isinstance(n, ast.Attribute) and n.attr == "dropped":
                return True
    return False


def _silent_sites(path: Path) -> tuple[int, list[str]]:
    """(active calls seen, silent 'file:line func' sites) for one file. Parse errors
    RAISE -- a file this cannot read is a non-answer, never a clean one."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for c in ast.iter_child_nodes(node):
            parent[c] = node
    seen, silent = 0, []
    for node in ast.walk(tree):
        if not _is_active_call(node):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "mods" \
                and path.name == "_registry.py":
            continue
        seen += 1
        fn = node
        while fn in parent and not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = parent[fn]
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            unit, label = [fn], fn.name
        else:   # module level: every statement outside a def/class
            unit = [s for s in tree.body
                    if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef,
                                          ast.ClassDef))]
            label = "<module>"
        if (path.name, label) in ALLOWED or _discloses(unit):
            continue
        silent.append(f"{path.name}:{node.lineno} {label}")
    return seen, silent


def _scan_files() -> list[Path]:
    return [p for r in SCAN_ROOTS for p in sorted(r.glob("*.py"))]


def test_every_active_scope_caller_discloses():
    seen, silent = 0, []
    for p in _scan_files():
        n, s = _silent_sites(p)
        seen += n
        silent += s
    # Denominator first: 20 sites + the `_effective.active_mods` wrapper at F139.
    assert seen >= 21, f"only {seen} active-scope call(s) found -- the scan is blind"
    assert not silent, (
        "these call the ACTIVE mod set and never reach its exclusions, so a mod the "
        "engine leaves out vanishes from their output with nothing said:\n  "
        + "\n  ".join(silent)
        + "\n\nRoute `_registry.dropped_note(mods)` to the sink the caller already has "
          "(or `_check.note_not_loaded` for a Report).")


def test_the_ban_can_fail(tmp_path):
    """CLAUDE.md #26: prove each branch can go red."""
    bad = tmp_path / "bad.py"
    bad.write_text("from x4validate import _registry, _effective\n"
                   "def f():\n    return _registry.mods('active')\n"
                   "def g():\n    return _effective.active_mods()\n"
                   "X = _registry.mods('active')\n", encoding="utf-8")
    seen, silent = _silent_sites(bad)
    assert (seen, sorted(silent)) == (3, ["bad.py:3 f", "bad.py:5 g", "bad.py:6 <module>"])
    ok = tmp_path / "ok.py"
    ok.write_text("from x4validate import _registry, _effective\n"
                  "def f():\n    m = _registry.mods('active')\n"
                  "    print(_registry.dropped_note(m))\n"
                  "def g(d):\n    return _effective.active_mods(None, d)\n"
                  "def h():\n    return _registry.mods('installed')\n", encoding="utf-8")
    assert _silent_sites(ok) == (2, [])
