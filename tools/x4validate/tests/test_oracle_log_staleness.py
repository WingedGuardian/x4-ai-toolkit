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


def _modlist(tmp_path, monkeypatch, folders, *, log_text, stale=(), log_age=+1000):
    """Installed game-root mods *folders*; *stale* ones get a manifest NEWER than the
    log, the rest older. Registry, profile and DLC set pinned -- never the real install."""
    ext = tmp_path / "ext"
    mods = []
    for f in folders:
        d = ext / f
        d.mkdir(parents=True)
        (d / "content.xml").write_text(f'<content id="{f}" version="1"/>', encoding="utf-8")
        mods.append({"folder": f, "path": str(d), "root_rank": 0, "id": f})
    log = tmp_path / "debug.txt"
    log.write_text(log_text, encoding="utf-8")
    t_log = os.stat(log).st_mtime
    for m in mods:
        t = t_log + (1000 if m["folder"] in stale else -log_age)
        os.utime(Path(m["path"]) / "content.xml", (t, t))
    monkeypatch.setattr(_registry, "mods",
                        lambda scope, *a, **k: _registry.ModList(list(mods)))
    monkeypatch.setattr(_paths, "profile_content", lambda: None)
    monkeypatch.setattr(_env, "dlc_folders", lambda: frozenset(), raising=False)
    return ext, log


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
def _oracle(monkeypatch, ext, log, *, judged="a", overlays=(), agree=True):
    """Run gates/oracle.py over one engine-rejected op of mod *judged*.

    agree=True: our side reports the op 'no-base' (not OK) -> agreement, rc 0.
    agree=False: our iterator never produces the op -> 'unseen', rc 1."""
    entry = SimpleNamespace(folder=judged, vpath="libraries/wares.xml",
                            sel="//ware[@id='x']", cardinality="none")
    monkeypatch.setattr(_env, "extensions", lambda: ext)
    monkeypatch.setattr(_env, "oracle_log", lambda: log)
    monkeypatch.setattr(_debuglog, "parse_debug", lambda p: [entry])
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
