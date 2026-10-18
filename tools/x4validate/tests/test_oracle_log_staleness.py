"""`oracle.py` and `oracle_reverse.py` refuse a log that does not describe the modlist.

Both gates compare a captured engine log with the merged tree built from what is
installed NOW. Until FX-H10 (2026-10-12) neither asked whether the log was written by a
launch of the current modlist, so against a stale log (2026-09-06) both returned rc 1 --
a FAIL -- while `load_order_oracle` on the same log correctly said CANNOT JUDGE. A
disagreement with a log of a different modlist may be the drift, not the code, so it
can neither convict nor clear: rc 2, naming the reasons.

The decision is ONE helper, `_env.modlist_staleness`, shared with `load_order_oracle`.
`oracle.py` scopes the manifest and unlogged clauses to the mods it judges plus the
mods loading before each of them (its Tier B patch-time overlays) -- a newer manifest
anywhere else cannot change any tree it replays. `oracle_reverse.py` answers from the
whole merged tree, so it uses the whole active modlist.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from lxml import etree

GATES = Path(__file__).resolve().parent.parent / "gates"
sys.path.insert(0, str(GATES))
import _env  # noqa: E402

from x4validate import _check, _debuglog, _merge, _paths, _registry  # noqa: E402

LOG_REV = (
    "[General] GetText(pageid=1001, textid=42) TextID not found!\n"
    "[=ERROR=] Cannot find referenced part template XML file from index 'part_x'\n"
)
T_WITH_PAGE = '<language id="44"><page id="1001"><t id="9">x</t></page></language>'
T_RESOLVES = '<language id="44"><page id="1001"><t id="42">x</t></page></language>'
IDX = '<index><entry name="something_else" value="v"/></index>'


def _modlist(tmp_path, monkeypatch, folders, *, log_text, stale=(), log_age=+1000,
             inactive=(), files=None, root_rank=None, dlc=(), profile_age=None):
    """Installed mods *folders*; *stale* ones get a manifest NEWER than the log, the rest
    older. *inactive*: installed but not in the active set. *files*: {folder: [rel]}.
    *root_rank*: {folder: rank} (default 0, the game root). *dlc*: the pinned DLC set.
    *profile_age*: profile content.xml mtime relative to the log (None = no profile).
    Registry, profile and DLC set pinned -- never the real install."""
    ext = tmp_path / "ext"
    files, root_rank = files or {}, root_rank or {}
    mods = []
    for f in folders:
        d = ext / f
        d.mkdir(parents=True)
        (d / "content.xml").write_text(f'<content id="{f}" version="1"/>', encoding="utf-8")
        for rel in files.get(f, []):
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text("<x/>", encoding="utf-8")
        mods.append({"folder": f, "path": str(d), "root_rank": root_rank.get(f, 0), "id": f})
    log = tmp_path / "debug.txt"
    log.write_text(log_text, encoding="utf-8")
    t_log = os.stat(log).st_mtime
    for m in mods:
        t = t_log + (1000 if m["folder"] in stale else -log_age)
        os.utime(Path(m["path"]) / "content.xml", (t, t))
    prof = None
    if profile_age is not None:
        prof = tmp_path / "profile_content.xml"
        prof.write_text("<content/>", encoding="utf-8")
        os.utime(prof, (t_log + profile_age, t_log + profile_age))

    def fake_mods(scope, *a, **k):
        if scope == "active":
            return _registry.ModList([m for m in mods if m["folder"] not in inactive])
        return _registry.ModList(list(mods))
    monkeypatch.setattr(_registry, "mods", fake_mods)
    monkeypatch.setattr(_paths, "profile_content", lambda: prof)
    monkeypatch.setattr(_env, "dlc_folders", lambda: frozenset(dlc), raising=False)
    return ext, log


_B = chr(92)


def _sig(folder: str, rel: str = "md/x.xml") -> str:
    """One engine signature-check line naming `.\\extensions\\<folder>\\<rel>`."""
    return ("[FileIO ] 0.00 File I/O: Failed to verify the file signature for file '."
            + _B + "extensions" + _B + folder + _B + rel.replace("/", _B) + "' (error: 14)\n")


# --------------------------------------------------------------------- oracle_reverse
def _reverse(monkeypatch, log, t_tree):
    import oracle_reverse as orev
    trees = {"t/0001-l044.xml": t_tree, "index/components.xml": IDX}
    monkeypatch.setattr(orev, "log_path", lambda: log)
    monkeypatch.setattr(orev, "effective",
                        lambda vpath: (etree.fromstring(trees[vpath].encode("utf-8"))
                                       if trees.get(vpath) else None))
    return orev.main()


def test_reverse_a_STALE_log_with_a_disagreement_refuses_not_fails(tmp_path, monkeypatch,
                                                                    capsys):
    _, log = _modlist(tmp_path, monkeypatch, ["a"], log_text=LOG_REV, stale=["a"])
    assert _reverse(monkeypatch, log, T_RESOLVES) == 2
    assert "a manifest changed after the log was written (a)" in capsys.readouterr().err


def test_reverse_a_STALE_log_with_agreement_refuses_not_passes(tmp_path, monkeypatch,
                                                               capsys):
    _, log = _modlist(tmp_path, monkeypatch, ["a"], log_text=LOG_REV, stale=["a"])
    assert _reverse(monkeypatch, log, T_WITH_PAGE) == 2
    assert "does not describe the current modlist" in capsys.readouterr().err


def test_reverse_a_FRESH_log_keeps_its_verdicts(tmp_path, monkeypatch):
    """Twins: the same comparisons on a log newer than every manifest -- rc 1 and rc 0."""
    _, log = _modlist(tmp_path, monkeypatch, ["a"], log_text=LOG_REV)
    assert _reverse(monkeypatch, log, T_RESOLVES) == 1
    assert _reverse(monkeypatch, log, T_WITH_PAGE) == 0


# --------------------------------------------------------------------------- oracle
def _oracle(monkeypatch, ext, log, *, judged="a", overlays=(), agree=True, game_ext=None,
            extra=()):
    """Run gates/oracle.py over one engine-rejected op of mod *judged*.

    agree=True: our side reports the op 'no-base' (not OK) -> agreement, rc 0.
    agree=False: our iterator never produces the op -> 'unseen', rc 1.
    *game_ext*: what `_env.extensions()` returns (default *ext*). *extra*: further logged
    folders that also have a rejected op."""
    entry = SimpleNamespace(folder=judged, vpath="libraries/wares.xml",
                            sel="//ware[@id='x']", cardinality="none")
    more = [SimpleNamespace(folder=f, vpath="libraries/wares.xml",
                            sel="//ware[@id='x']", cardinality="none") for f in extra]
    monkeypatch.setattr(_env, "extensions", lambda: game_ext or ext)
    monkeypatch.setattr(_env, "oracle_log", lambda: log)
    monkeypatch.setattr(_debuglog, "parse_debug", lambda p: [entry, *more])
    monkeypatch.setattr(_check, "tier_b_overlays",
                        lambda mod_dir, *a: (tuple(ext / o for o in overlays), []))
    diff = etree.fromstring(f'<diff><replace sel="{entry.sel}">1</replace></diff>')
    monkeypatch.setattr(_check, "iter_diff_files",
                        lambda mod_dir: [("libraries/wares.xml", diff)] if agree else [])
    monkeypatch.setattr(_merge, "Config", lambda **k: None)
    monkeypatch.setattr(_merge, "build_effective",
                        lambda *a, **k: SimpleNamespace(base_found=False, tree=None))
    try:
        runpy.run_path(str(GATES / "oracle.py"), run_name="__main__")
    except SystemExit as exc:
        return exc.code
    return 0


def test_oracle_a_STALE_judged_mod_refuses_not_fails(tmp_path, monkeypatch, capsys):
    ext, log = _modlist(tmp_path, monkeypatch, ["a"], log_text="", stale=["a"])
    assert _oracle(monkeypatch, ext, log, agree=False) == 2
    assert "a manifest changed after the log was written (a)" in capsys.readouterr().err


def test_oracle_a_STALE_judged_mod_refuses_not_passes(tmp_path, monkeypatch, capsys):
    ext, log = _modlist(tmp_path, monkeypatch, ["a"], log_text="", stale=["a"])
    assert _oracle(monkeypatch, ext, log, agree=True) == 2


def test_oracle_a_FRESH_log_keeps_its_verdicts(tmp_path, monkeypatch):
    ext, log = _modlist(tmp_path, monkeypatch, ["a"], log_text="")
    assert _oracle(monkeypatch, ext, log, agree=False) == 1
    assert _oracle(monkeypatch, ext, log, agree=True) == 0


def test_oracle_a_stale_mod_it_does_NOT_judge_does_not_block_it(tmp_path, monkeypatch):
    """Scope: z is active, newer than the log, but neither judged nor loading before a."""
    ext, log = _modlist(tmp_path, monkeypatch, ["a", "z"], log_text="", stale=["z"])
    assert _oracle(monkeypatch, ext, log, agree=True) == 0


def test_oracle_a_stale_mod_in_a_judged_mods_overlays_DOES_block_it(tmp_path, monkeypatch,
                                                                    capsys):
    """Twin: the same z, now in a's patch-time tree -- it changes what a was replayed on."""
    ext, log = _modlist(tmp_path, monkeypatch, ["a", "z"], log_text="", stale=["z"])
    assert _oracle(monkeypatch, ext, log, overlays=["z"], agree=True) == 2
    assert "(z)" in capsys.readouterr().err


# ------------------------------------------------- release review P, finding 1 (MEDIUM)
# A mod the log names that is still INSTALLED but no longer ACTIVE (its own manifest
# disables it, or a dependency changed) was never age-checked: `newest_manifest` ran
# over the active set only. MEASURED by the reviewer: reasons() == [], oracle_reverse
# rc 0, oracle rc 0 -- a verdict on a log of a modlist that has since changed.
_E_LOG = _sig("a") + _sig("e")


def test_P1_an_INACTIVE_logged_mod_edited_after_the_log_is_a_reason(tmp_path, monkeypatch):
    _modlist(tmp_path, monkeypatch, ["a", "e"], log_text=_E_LOG, stale=["e"], inactive=["e"])
    assert _env.modlist_staleness(tmp_path / "debug.txt").reasons() == [
        "a manifest changed after the log was written (e)"]


def test_P1_twin_the_same_inactive_mod_edited_BEFORE_the_log_is_not(tmp_path, monkeypatch):
    _modlist(tmp_path, monkeypatch, ["a", "e"], log_text=_E_LOG, inactive=["e"])
    assert _env.modlist_staleness(tmp_path / "debug.txt").reasons() == []


def test_P1_twin_an_inactive_mod_the_log_never_names_does_not_count(tmp_path, monkeypatch):
    """Not logged, not active: the launch never had it and neither does today."""
    _modlist(tmp_path, monkeypatch, ["a", "e"], log_text=_sig("a"), stale=["e"],
             inactive=["e"])
    assert _env.modlist_staleness(tmp_path / "debug.txt").reasons() == []


def test_P1_reverse_refuses_on_an_inactive_logged_mod_edited_after(tmp_path, monkeypatch):
    _, log = _modlist(tmp_path, monkeypatch, ["a", "e"], log_text=_E_LOG + LOG_REV,
                      stale=["e"], inactive=["e"])
    assert _reverse(monkeypatch, log, T_WITH_PAGE) == 2


def test_P1_reverse_twin_edited_before_keeps_its_verdict(tmp_path, monkeypatch):
    _, log = _modlist(tmp_path, monkeypatch, ["a", "e"], log_text=_E_LOG + LOG_REV,
                      inactive=["e"])
    assert _reverse(monkeypatch, log, T_WITH_PAGE) == 0


def test_P1_oracle_refuses_when_the_JUDGED_mod_is_inactive_and_edited_after(
        tmp_path, monkeypatch):
    ext, log = _modlist(tmp_path, monkeypatch, ["a", "e"], log_text=_E_LOG, stale=["e"],
                        inactive=["e"])
    assert _oracle(monkeypatch, ext, log, judged="e", agree=True) == 2


def test_P1_oracle_twin_edited_before_keeps_its_verdict(tmp_path, monkeypatch):
    ext, log = _modlist(tmp_path, monkeypatch, ["a", "e"], log_text=_E_LOG, inactive=["e"])
    assert _oracle(monkeypatch, ext, log, judged="e", agree=True) == 0


# ------------------------------------------------- release review P, finding 2
# Each reason THROUGH `modlist_staleness`, each with a twin.
def test_P2_a_logged_folder_that_is_not_installed_is_gone(tmp_path, monkeypatch):
    _modlist(tmp_path, monkeypatch, ["a"], log_text=_sig("a") + _sig("ghost"))
    assert _env.modlist_staleness(tmp_path / "debug.txt").reasons() == [
        "1 logged folder(s) are not installed now: ghost"]


def test_P2_twin_a_logged_INSTALLED_folder_is_not_gone(tmp_path, monkeypatch):
    _modlist(tmp_path, monkeypatch, ["a", "b"], log_text=_sig("a") + _sig("b"))
    assert _env.modlist_staleness(tmp_path / "debug.txt").reasons() == []


def test_P2_a_DLC_signature_line_is_not_gone(tmp_path, monkeypatch):
    """Fails if `drop_dlc(seqs, dlc_folders())` is removed from modlist_staleness."""
    _modlist(tmp_path, monkeypatch, ["a"], log_text=_sig("a") + _sig("ego_dlc_terran"),
             dlc=["ego_dlc_terran"])
    assert _env.modlist_staleness(tmp_path / "debug.txt").reasons() == []


def test_P2_twin_the_same_line_with_no_DLC_configured_is_gone(tmp_path, monkeypatch):
    _modlist(tmp_path, monkeypatch, ["a"], log_text=_sig("a") + _sig("ego_dlc_terran"))
    assert _env.modlist_staleness(tmp_path / "debug.txt").reasons() == [
        "1 logged folder(s) are not installed now: ego_dlc_terran"]


def test_P2_an_active_mod_shipping_a_logged_class_never_logged_for_it(tmp_path, monkeypatch):
    _modlist(tmp_path, monkeypatch, ["a", "b"], log_text=_sig("a", "md/x.xml"),
             files={"b": ["md/x.xml"]})
    assert _env.modlist_staleness(tmp_path / "debug.txt").reasons() == [
        "1 active mod(s) ship a file the engine checked for other mods but never for "
        "them: b (md/x.xml)"]


def test_P2_twin_an_active_mod_shipping_only_unlogged_classes(tmp_path, monkeypatch):
    _modlist(tmp_path, monkeypatch, ["a", "b"], log_text=_sig("a", "md/x.xml"),
             files={"b": ["md/y.xml"]})
    assert _env.modlist_staleness(tmp_path / "debug.txt").reasons() == []


def test_P2_root_rank_an_unlogged_mod_outside_the_game_root_is_not_evidence(
        tmp_path, monkeypatch):
    """The signature line names game-root folders only, so a profile-root mod's absence
    from it is no evidence (twin of the case above, root_rank 1)."""
    _modlist(tmp_path, monkeypatch, ["a", "b"], log_text=_sig("a", "md/x.xml"),
             files={"b": ["md/x.xml"]}, root_rank={"b": 1})
    assert _env.modlist_staleness(tmp_path / "debug.txt").reasons() == []


def test_P2_a_profile_content_xml_newer_than_the_log(tmp_path, monkeypatch):
    _modlist(tmp_path, monkeypatch, ["a"], log_text=_sig("a"), profile_age=+1000)
    assert _env.modlist_staleness(tmp_path / "debug.txt").reasons() == [
        "the profile content.xml (the enable/disable decisions) changed after the log "
        "was written"]


def test_P2_twin_a_profile_content_xml_older_than_the_log(tmp_path, monkeypatch):
    _modlist(tmp_path, monkeypatch, ["a"], log_text=_sig("a"), profile_age=-1000)
    assert _env.modlist_staleness(tmp_path / "debug.txt").reasons() == []


# ------------------------------------------------- release review P, finding 3
def _cfg_with_dlc(monkeypatch, names):
    monkeypatch.setattr(_merge, "Config", lambda **k: SimpleNamespace(
        dlc_dirs=lambda: [Path("x") / n for n in names]))


def test_P3_an_EMPTY_resolved_DLC_set_is_announced(monkeypatch, capsys):
    """A reference that is missing or holds no ego_dlc_* resolves to nothing; every DLC
    signature line then reads as gone, so the empty set must say so."""
    _cfg_with_dlc(monkeypatch, [])
    assert _env.dlc_folders() == frozenset()
    assert "no DLC set to exempt" in capsys.readouterr().err


def test_P3_twin_a_non_empty_DLC_set_is_silent(monkeypatch, capsys):
    _cfg_with_dlc(monkeypatch, ["ego_dlc_terran"])
    assert _env.dlc_folders() == frozenset({"ego_dlc_terran"})
    assert "no DLC set to exempt" not in capsys.readouterr().err


# ----------------------------------------------------------------------- F285 (edited)
# A diff edited AFTER the log, content.xml untouched, tripped none of the manifest, profile,
# removed-folder or unlogged-mod signals: the gates replayed the NEW ops against the
# engine's verdicts on the OLD ones. `Staleness.edited` is filled only when the caller
# passes `content_scope` (oracle.py and oracle_reverse do; load_order_oracle does not).
_WARES = "libraries/wares.xml"
_EDITED = "an engine-loadable file changed after the log was written ({})"


def _stamp(ext, folder, rel, log, delta):
    """Set a shipped file's mtime to the log's mtime + *delta* seconds."""
    t = os.stat(log).st_mtime + delta
    os.utime(ext / folder / rel, (t, t))


def test_F285_a_diff_edited_after_the_log_is_a_reason(tmp_path, monkeypatch):
    ext, log = _modlist(tmp_path, monkeypatch, ["a"], log_text=_sig("a"),
                        files={"a": [_WARES]})
    _stamp(ext, "a", _WARES, log, +1000)
    assert _env.modlist_staleness(log).reasons() == [_EDITED.format("a/libraries/wares.xml")]


def test_F285_twin_the_same_file_older_than_the_log_is_not(tmp_path, monkeypatch):
    ext, log = _modlist(tmp_path, monkeypatch, ["a"], log_text=_sig("a"),
                        files={"a": [_WARES]})
    _stamp(ext, "a", _WARES, log, -1000)
    assert _env.modlist_staleness(log).reasons() == []


def test_F285_twin_a_newer_NON_engine_file_is_not(tmp_path, monkeypatch):
    """A README edited after the log changes no tree (ENGINE_FILE_SUFFIXES)."""
    ext, log = _modlist(tmp_path, monkeypatch, ["a"], log_text=_sig("a"),
                        files={"a": ["README.txt", "notes.md"]})
    _stamp(ext, "a", "README.txt", log, +1000)
    _stamp(ext, "a", "notes.md", log, +1000)
    assert _env.modlist_staleness(log).reasons() == []


def test_F285_the_suffix_set_is_DERIVED_not_retyped(tmp_path, monkeypatch):
    """Narrow the shared set and a .lua edit stops counting: _env reads _freshness live."""
    from x4validate import _freshness
    ext, log = _modlist(tmp_path, monkeypatch, ["a"], log_text=_sig("a"),
                        files={"a": ["ui/x.lua"]})
    _stamp(ext, "a", "ui/x.lua", log, +1000)
    assert len(_env.modlist_staleness(log).edited) == 1
    monkeypatch.setattr(_freshness, "ENGINE_FILE_SUFFIXES", frozenset({".xml"}))
    assert _env.modlist_staleness(log).edited == []


def test_F285_a_scoped_caller_ignores_an_edit_in_a_mod_it_does_not_judge(tmp_path,
                                                                      monkeypatch):
    ext, log = _modlist(tmp_path, monkeypatch, ["a", "z"], log_text=_sig("a") + _sig("z"),
                        files={"z": [_WARES]})
    _stamp(ext, "z", _WARES, log, +1000)
    assert _env.modlist_staleness(log, {"a"}).reasons() == []
    assert _env.modlist_staleness(log, {"z"}).reasons() == [
        _EDITED.format("z/libraries/wares.xml")]


def test_F285_log_staleness_without_content_scope_never_asks(tmp_path, monkeypatch):
    """load_order_oracle's call shape: the same edited file, no content_scope -> no reason."""
    ext, log = _modlist(tmp_path, monkeypatch, ["a"], log_text=_sig("a"),
                        files={"a": [_WARES]})
    _stamp(ext, "a", _WARES, log, +1000)
    mods = list(_registry.mods("active"))
    kw = dict(scope=mods, known={"a"}, t_profile=None)
    t_log = os.stat(log).st_mtime
    assert _env.log_staleness(t_log, {"s": ["a"]}, **kw).reasons() == []
    assert _env.log_staleness(t_log, {"s": ["a"]}, content_scope=mods, **kw).reasons() == [
        _EDITED.format("a/libraries/wares.xml")]


def test_F285_load_order_oracle_does_not_pass_content_scope():
    """Structural: the load order comes from manifests, so a content edit cannot change it."""
    import ast
    src = (GATES / "load_order_oracle.py").read_text(encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "log_staleness"]
    assert len(calls) == 1
    assert "content_scope" not in {k.arg for k in calls[0].keywords}


def test_F285_an_unreadable_file_FAILS_CLOSED(tmp_path, monkeypatch):
    ext, log = _modlist(tmp_path, monkeypatch, ["a"], log_text=_sig("a"),
                        files={"a": [_WARES]})
    real = os.stat

    def flaky(path, *a, **k):
        if str(path).endswith("wares.xml"):
            raise PermissionError("denied")
        return real(path, *a, **k)
    monkeypatch.setattr(os, "stat", flaky)
    assert _env.modlist_staleness(log).reasons() == [
        _EDITED.format("a/libraries/wares.xml (unreadable)")]


def test_F285_an_unlistable_folder_FAILS_CLOSED(tmp_path, monkeypatch):
    ext, log = _modlist(tmp_path, monkeypatch, ["a"], log_text=_sig("a"),
                        files={"a": [_WARES]})
    real = os.scandir

    def deny(path=".", *a, **k):
        if str(path).endswith("libraries"):
            raise PermissionError("denied")
        return real(path, *a, **k)
    monkeypatch.setattr(os, "scandir", deny)
    assert _env.modlist_staleness(log).reasons() == [
        _EDITED.format("a (a folder could not be listed)")]


def test_F285_oracle_refuses_on_an_edited_diff_with_content_xml_untouched(
        tmp_path, monkeypatch, capsys):
    """Manifest older than the log, diff newer: the case no other signal sees."""
    ext, log = _modlist(tmp_path, monkeypatch, ["a"], log_text="", files={"a": [_WARES]})
    _stamp(ext, "a", _WARES, log, +1000)
    assert _oracle(monkeypatch, ext, log, agree=True) == 2
    assert "a/libraries/wares.xml" in capsys.readouterr().err


def test_F285_oracle_twin_an_older_diff_keeps_its_verdict(tmp_path, monkeypatch):
    ext, log = _modlist(tmp_path, monkeypatch, ["a"], log_text="", files={"a": [_WARES]})
    _stamp(ext, "a", _WARES, log, -1000)
    assert _oracle(monkeypatch, ext, log, agree=True) == 0


def test_F285_oracle_ignores_an_edit_outside_its_scope_but_not_inside_an_overlay(
        tmp_path, monkeypatch, capsys):
    ext, log = _modlist(tmp_path, monkeypatch, ["a", "z"], log_text="",
                        files={"z": [_WARES]})
    _stamp(ext, "z", _WARES, log, +1000)
    assert _oracle(monkeypatch, ext, log, agree=True) == 0
    assert _oracle(monkeypatch, ext, log, overlays=["z"], agree=True) == 2
    assert "z/libraries/wares.xml" in capsys.readouterr().err


def test_F285_reverse_refuses_on_an_edited_diff(tmp_path, monkeypatch, capsys):
    ext, log = _modlist(tmp_path, monkeypatch, ["a"], log_text=LOG_REV,
                        files={"a": [_WARES]})
    _stamp(ext, "a", _WARES, log, +1000)
    assert _reverse(monkeypatch, log, T_WITH_PAGE) == 2
    assert "a/libraries/wares.xml" in capsys.readouterr().err


def test_F285_reverse_twin_an_older_diff_keeps_its_verdict(tmp_path, monkeypatch):
    ext, log = _modlist(tmp_path, monkeypatch, ["a"], log_text=LOG_REV,
                        files={"a": [_WARES]})
    _stamp(ext, "a", _WARES, log, -1000)
    assert _reverse(monkeypatch, log, T_WITH_PAGE) == 0


# --------------------------------------------------------------- F285 (LOW: install roots)
def test_F285_LOW_a_PROFILE_root_mod_is_judged_not_NOT_INSTALLED(tmp_path, monkeypatch,
                                                                capsys):
    """The judged mod lives in the profile root; the game-root extensions folder is empty."""
    ext, log = _modlist(tmp_path, monkeypatch, ["a"], log_text="", root_rank={"a": 1})
    empty = tmp_path / "game_ext"
    empty.mkdir()
    assert _oracle(monkeypatch, ext, log, agree=True, game_ext=empty) == 0
    assert "NOT INSTALLED" not in capsys.readouterr().out


def test_F285_LOW_a_logged_folder_in_NO_root_refuses(tmp_path, monkeypatch, capsys):
    """Twin: 'ghost' is in no root. Before the fix the judged mod 'a' carried a rc 0 and
    ghost's lines were merely footnoted as outside the denominator."""
    ext, log = _modlist(tmp_path, monkeypatch, ["a"], log_text="")
    assert _oracle(monkeypatch, ext, log, agree=True, extra=["ghost"]) == 2
    assert "installed in no root now: ghost" in capsys.readouterr().err
