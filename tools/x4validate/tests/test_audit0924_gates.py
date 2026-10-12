"""AUDIT-2026-09-24 Phase 1 -- BaseX (BX-*) and gates (GT-*).

Each test was written as `xfail(strict=True)`, failing on the audited code; every finding has
since been fixed and its marker removed with the fix, so these are now plain REGRESSION tests
that must pass.

Only DEPENDENCIES are stubbed: BaseX (`run_xq` / `basex_query`), subprocess launches of the
CLIs, the effective store (a synthetic sqlite of the real two-table schema), the registry
loader, and `_env` path resolution. The unit under test -- the gate's or the tool's own
decision logic -- always runs for real. No test here starts a JVM, runs a CLI, reads the real
corpus, or writes outside `tmp_path`.
"""
from __future__ import annotations

import importlib
import importlib.util
import io
import json
import re
import runpy
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from conftest import import_gate

PKG = Path(__file__).resolve().parent.parent            # tools/x4validate
BASEX = PKG.parent / "basex"                           # tools/basex
GATES = PKG / "gates"


def _basex(name: str):
    """Import a tools/basex module the way tools/basex/test_*.py do (bare import, dir on path)."""
    if not (BASEX / f"{name}.py").is_file():
        pytest.skip(f"no BaseX tooling at {BASEX}")
    if str(BASEX) not in sys.path:
        sys.path.insert(0, str(BASEX))
    return importlib.import_module(name)


# ============================================================================ BX-1 / BX-5 (ask.py)

@pytest.fixture
def ask(monkeypatch):
    """ask.py with BaseX, the preflight and freshness stubbed; coverage reads COMPLETE 100/100."""
    mod = _basex("ask")
    staleness = _basex("staleness")
    monkeypatch.setattr(mod.preflight, "check", lambda *a, **k: [])
    for k in ("MSYSTEM", "MSYS_NO_PATHCONV", "MSYS2_ARG_CONV_EXCL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(mod, "load_coverage", lambda db: {
        "db": db, "status": "complete", "supports_negative_claim": True,
        "indexed": {"total": 100}, "expected": {"total": 100}, "unparseable": []})
    monkeypatch.setattr(mod, "staleness_verdict", lambda db: staleness.Verdict(True, [], db))

    def fake_basex(items):
        def run_xq(q):
            if mod._SEP in q:
                return f"{len(items)}\n{mod._SEP}\n" + "\n".join(items)
            return "\n".join(items)
        monkeypatch.setattr(mod, "run_xq", run_xq)
    mod._fake = fake_basex
    return mod


def test_bx1_a_zero_from_ONE_addressed_document_is_not_a_negative_over_the_database(ask, capsys):
    ask._fake([])
    rc = ask.main(["xq", "db:get('x4eff','no/such/typo.xml')//ware", "--db", "x4eff"])
    out = capsys.readouterr().out
    assert "NEGATIVE CONFIRMED" not in out and rc != 0, (
        f"rc={rc}: 100 of 100 documents describes the database, not the one path the query "
        f"addressed (which does not exist)\n{out}")


@pytest.mark.parametrize("query", [
    "db:get('x4eff','libraries/wares.xml')//nosuch",
    "let $d := 'x4eff' return collection($d)//nosuch",
])
def test_bx1_a_query_reaching_x4eff_is_not_scored_against_x4raw(ask, capsys, query):
    ask._fake([])
    rc = ask.main(["xq", query])                          # --db defaults to x4raw
    out = capsys.readouterr().out
    assert "NEGATIVE CONFIRMED" not in out, (
        f"rc={rc}: a query that searched (or may have searched) x4eff was certified against "
        f"x4raw's coverage and freshness\n{out}")


def test_bx5_a_false_result_is_not_a_hit(ask, capsys):
    ask._fake(["false"])
    rc = ask.main(["xq", "exists(collection('x4raw')//nosuch)"])
    assert rc != 0, f"'false' read as one hit, rc {rc}:\n{capsys.readouterr().out}"


def test_bx5_an_empty_string_result_is_not_a_hit(ask, capsys):
    ask._fake([""])
    rc = ask.main(["xq", "string(collection('x4raw')//nosuch)"])
    assert rc != 0, f"'' read as one hit, rc {rc}:\n{capsys.readouterr().out}"


def test_bx5_the_scope_refusal_only_advises_a_db_argparse_accepts(ask, capsys):
    ask._fake([])
    rc = ask.main(["xq", "collection('x4eff/libraries')//ware", "--db", "x4eff"])
    err = capsys.readouterr().err
    advised = re.findall(r"--db (\S+?)\.?$", err, re.M)
    assert rc == 2 and advised, err
    assert all(a in ("x4raw", "x4eff") for a in advised), (
        f"advice names {advised}; following it is an argparse error\n{err}")


# --- BX-1 / BX-5 falsification twins: every refusal above has a case it must NOT fire on,
# --- and the refusal is pinned for each address shape, not only the two the audit named.

@pytest.mark.parametrize("query,db", [
    ("collection('x4raw')//nosuch", "x4raw"),
    ("db:get('x4eff')//nosuch", "x4eff"),
    ('fn:collection( "x4eff" )//nosuch', "x4eff"),
    ("(: doc('x4eff/a.xml') :) collection('x4eff')//nosuch", "x4eff"),   # a comment is no address
    # `collection('x4raw')//*[@id = db:node-id(.)]` was a twin here until the v3.3.0 review:
    # db:node-id is now a scope token (a zero is not certified wherever one appears), so it
    # moved to the rc-4 list below. Its replacement keeps a content predicate in the twin.
    ("collection('x4raw')//*[@id = 'x']", "x4raw"),
])
def test_bx1_TWIN_a_whole_database_zero_is_still_confirmed(ask, capsys, query, db):
    ask._fake([])
    rc = ask.main(["xq", query, "--db", db])
    out = capsys.readouterr().out
    assert rc == 0 and "NEGATIVE CONFIRMED over 100 of 100" in out, out


@pytest.mark.parametrize("query", [
    "doc('x4eff/libraries/wares.xml')//nosuch",
    "db:open('x4eff', 'libraries')//nosuch",
    "collection('x4eff/libraries')//nosuch",
    "collection('x4' || 'eff')//nosuch",
    "db:get-id('x4eff', 5)",
    "collection()//nosuch",
])
def test_bx1_every_partial_or_unreadable_address_is_refused(ask, capsys, query):
    ask._fake([])
    rc = ask.main(["xq", query, "--db", "x4eff"])
    cap = capsys.readouterr()
    assert rc == 2 and "NEGATIVE CONFIRMED" not in cap.out, (rc, cap)


# A scope narrowed OUTSIDE a reach call's arguments RUNS, and only its zero is withheld (rc 4),
# since the v3.3.0 review replaced the list of narrowing shapes with a token rule.
# `collection('x4raw')//*[db:path(.) = 'x']` sat in the rc-2 list above, run with --db x4eff:
# it was refused as a FOREIGN database, so it never tested the narrowing it was there for.
# It is here now with the matching --db, together with the old db:node-id twin.
@pytest.mark.parametrize("query", [
    "collection('x4raw')//*[db:path(.) = 'x']",
    "collection('x4raw')//*[@id = db:node-id(.)]",
])
def test_bx1_a_zero_from_a_query_that_can_narrow_its_documents_is_not_a_negative(ask, capsys, query):
    ask._fake([])
    rc = ask.main(["xq", query, "--db", "x4raw"])
    out = capsys.readouterr().out
    assert rc == 4 and "NEGATIVE CONFIRMED" not in out and "can narrow" in out, (rc, out)


def test_bx1_a_zero_from_a_query_naming_NO_database_is_not_a_negative(ask, capsys):
    """`()` searches nothing, and was certified over the whole --db denominator."""
    ask._fake([])
    rc = ask.main(["xq", "()"])
    out = capsys.readouterr().out
    assert rc == 4 and "NEGATIVE CONFIRMED" not in out and "names no database" in out, out


def test_bx1_TWIN_a_POSITIVE_from_a_query_naming_no_database_still_answers(ask, capsys):
    ask._fake(["2"])
    assert ask.main(["xq", "1+1"]) == 0, capsys.readouterr()


def test_bx5_a_database_argparse_rejects_is_never_advised(ask, capsys):
    ask._fake([])
    rc = ask.main(["xq", "collection('mydb')//x"])
    err = capsys.readouterr().err
    assert rc == 2 and "--db mydb" not in err and "x4raw and x4eff" in err, err


@pytest.mark.parametrize("items", [["true"], ["a", "false"], ["0.5"]])
def test_bx5_TWIN_other_single_values_and_mixed_results_are_still_hits(ask, capsys, items):
    ask._fake(items)
    assert ask.main(["xq", "collection('x4raw')//x"]) == 0, capsys.readouterr()


# ============================================================================ BX-2 (stage.py)

def test_bx2_an_empty_packed_dlc_answer_is_honoured(monkeypatch):
    stage = _basex("stage")
    from x4validate import _merge
    monkeypatch.setattr(_merge.Config, "packed_dlc_names", lambda self: set())
    got = stage.packed_dlc_names()
    assert got == (), (f"Config said no DLC is packed-only (all unpacked into reference\\), yet "
                       f"{got} would be staged into /base/extensions ON TOP of the reference copies")


def test_bx2_TWIN_a_nonempty_answer_is_staged_as_given(monkeypatch):
    stage = _basex("stage")
    from x4validate import _merge
    monkeypatch.setattr(_merge.Config, "packed_dlc_names",
                        lambda self: {"ego_dlc_mini_02", "ego_dlc_mini_01"})
    assert stage.packed_dlc_names() == ("ego_dlc_mini_01", "ego_dlc_mini_02")


@pytest.mark.parametrize("exc", [ValueError("bad catalog"), KeyError("x"), RuntimeError("boom"),
                                 UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")])
def test_bx2_ANY_config_failure_is_a_refusal_not_a_traceback(monkeypatch, tmp_path, capsys, exc):
    """Re-review: only four exception types were caught, so any other failure of the
    packed-DLC query escaped at IMPORT as a raw traceback, rc 1 -- which in this toolkit
    means "findings". It must be the same rc-2 refusal as the listed four."""
    stage = _basex("stage")
    from x4validate import _merge

    def boom(self):
        raise exc
    monkeypatch.setattr(_merge.Config, "packed_dlc_names", boom)
    with pytest.raises(stage.PackedDlcUnknown):
        stage.packed_dlc_names()


def test_bx2_a_config_FAILURE_refuses_instead_of_guessing(monkeypatch, tmp_path, capsys):
    """The old fallback staged the historical pair on ANY failure; a guess is wrong one way
    or the other (double-index an unpacked DLC, or omit a packed one), so main refuses."""
    stage = _basex("stage")
    from x4validate import _merge

    def boom(self):
        raise OSError("simulated: reference unreadable")
    monkeypatch.setattr(_merge.Config, "packed_dlc_names", boom)
    with pytest.raises(stage.PackedDlcUnknown):
        stage.packed_dlc_names()
    monkeypatch.setattr(stage, "MINI_DLC", None)
    monkeypatch.setattr(stage, "MINI_DLC_ERROR", "OSError: simulated")
    ext, out, man = tmp_path / "ext", tmp_path / "stage", tmp_path / "m.json"
    ext.mkdir()
    rc = stage.main(["--out", str(out), "--extensions", str(ext), "--manifest", str(man)])
    err = capsys.readouterr().err
    assert rc == 2 and not out.exists() and not man.exists(), err
    assert "Refusing to guess" in err, err


# ============================================================================ BX-3 (build scripts)

@pytest.mark.parametrize("script", ["build-corpus.sh", "build-effective.sh"])
def test_bx3_the_old_coverage_licence_is_revoked_before_the_db_is_dropped(script):
    text = (BASEX / script).read_text(encoding="utf-8")
    # The first DROP DB on a line that is not a COMMENT: issue #10 added a comment naming
    # "the DROP DB below" above the revoke, and a bare text.find() anchored there.
    drop, pos = -1, 0
    for line in text.splitlines(keepends=True):
        if "DROP DB" in line and not line.lstrip().startswith("#"):
            drop = pos + line.index("DROP DB")
            break
        pos += len(line)
    assert drop > 0, f"{script}: no DROP DB found -- the fixture premise moved"
    revoke = re.search(r"rm -f[^\n]*coverage-", text[:drop])
    assert revoke, (f"{script}: nothing removes coverage-$DB.json before DROP DB; an "
                    f"interrupted or failed rebuild (exit at the 'not found' grep) leaves the "
                    f"previous build's supports_negative_claim=true beside a partial database")


# ============================================================================ BX-4 (coverage.py)

def test_bx4_offsetting_per_root_errors_do_not_license_a_negative(tmp_path, monkeypatch, capsys):
    # By FILE, never by the bare name: `coverage` is also the PyPI package, and a bare import
    # can hand back that one (test_coverage_reporting.py loads it the same way).
    if not (BASEX / "coverage.py").is_file():
        pytest.skip(f"no BaseX tooling at {BASEX}")
    spec = importlib.util.spec_from_file_location("basex_coverage_audit0924", BASEX / "coverage.py")
    cov = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cov)
    ref, ext, stage = tmp_path / "ref", tmp_path / "ext", tmp_path / "stage"
    for d in (ref, ext, stage):
        d.mkdir()
    counts = {str(ref): 50, str(ext): 50}
    monkeypatch.setattr(cov, "count_disk_xml", lambda root: counts.get(str(root), 0))
    monkeypatch.setattr(cov, "basex_query", lambda db, xq: "total=100\nbase=55\nmods=45\n")
    out = tmp_path / "coverage-x4raw.json"
    rc = cov.main(["--db", "x4raw", "--stage", str(stage), "--manifest", str(tmp_path / "none.json"),
                   "--reference", str(ref), "--extensions", str(ext), "--out", str(out)])
    data = json.loads(out.read_text(encoding="utf-8"))
    assert not data["supports_negative_claim"], (
        f"rc={rc} status={data['status']}: base is +5 EXTRA and mods -5 MISSING, and the "
        f"verdict came from their sum\n{capsys.readouterr().out}")


@pytest.mark.parametrize("where,short,expect", [
    ("ref", "base", "accounted"),        # the malformed file sits under the root that is short
    ("ext", "mods", "accounted"),
    ("packed-mods", "mods", "accounted"),
    ("ext", "base", "unexplained"),      # right COUNT, wrong ROOT: the aggregate would pass it
    ("packed-mods", "base", "unexplained"),
])
def test_bx4_TWIN_a_deficit_is_accounted_only_by_its_OWN_roots_malformed_files(
        tmp_path, monkeypatch, capsys, where, short, expect):
    if not (BASEX / "coverage.py").is_file():
        pytest.skip(f"no BaseX tooling at {BASEX}")
    spec = importlib.util.spec_from_file_location("basex_coverage_audit0924_twin", BASEX / "coverage.py")
    cov = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cov)
    ref, ext, stage = tmp_path / "ref", tmp_path / "ext", tmp_path / "stage"
    for d in (ref, ext, stage):
        d.mkdir()
    manifest = {"sources": [], "unparseable": [], "totals": {}}
    if where in ("ref", "ext"):
        (tmp_path / where / "broken.xml").write_text("<a><b></a>", encoding="utf-8")
    else:
        manifest["sources"] = [{"name": "m", "root": "/mods", "unparseable": ["x.xml: bad"]}]
        manifest["unparseable"] = ["m/x.xml: bad"]
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps(manifest), encoding="utf-8")
    counts = {str(ref): 50, str(ext): 50}
    monkeypatch.setattr(cov, "count_disk_xml", lambda root: counts.get(str(root), 0))
    idx = {"base": 50, "mods": 50}
    idx[short] -= 1
    monkeypatch.setattr(cov, "basex_query", lambda db, xq: (
        f"total={idx['base'] + idx['mods']}\nbase={idx['base']}\nmods={idx['mods']}\n"))
    out = tmp_path / "coverage-x4raw.json"
    cov.main(["--db", "x4raw", "--stage", str(stage), "--manifest", str(man),
              "--reference", str(ref), "--extensions", str(ext), "--out", str(out)])
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["status"] == expect, capsys.readouterr().out
    assert data["supports_negative_claim"] is (expect == "accounted")


# ============================================================================ GT-1 noop_audit

def test_gt1_an_unreadable_catalog_is_not_a_clean_noop_audit(gate_install, tmp_path, monkeypatch, capsys):
    na = gate_install.load("noop_audit")
    ext, ref = tmp_path / "ext", tmp_path / "ref"
    (ext / "somemod").mkdir(parents=True)
    ref.mkdir()
    monkeypatch.setattr(na, "EXT", ext)
    monkeypatch.setattr(na, "REF", ref)
    monkeypatch.setattr(na, "UNREADABLE", [])

    def boom(*a, **k):
        raise OSError("simulated unreadable catalog")
    monkeypatch.setattr(na._cat, "mod_vfs", boom)
    rc = na.main()
    assert rc != 0, f"somemod was never audited, yet rc 0:\n{capsys.readouterr().out}"


def test_gt1_unreadable_is_named_and_rc_2_and_a_false_ok_outranks_it(gate_install, tmp_path, monkeypatch, capsys):
    """The UNREADABLE list reaches the verdict: printed by name, rc 2 when nothing else was
    found -- and a real FALSE OK beside it is still rc 1 (a finding outranks a refusal)."""
    na = gate_install.load("noop_audit")
    ext, ref = tmp_path / "ext", tmp_path / "ref"
    (ext / "somemod").mkdir(parents=True)
    ref.mkdir()
    monkeypatch.setattr(na, "EXT", ext)
    monkeypatch.setattr(na, "REF", ref)
    monkeypatch.setattr(na, "UNREADABLE", [])

    def boom(*a, **k):
        raise OSError("simulated unreadable catalog")
    monkeypatch.setattr(na._cat, "mod_vfs", boom)
    assert na.main() == 2
    assert "somemod: catalog unreadable" in capsys.readouterr().out

    # A false OK in a readable mod beside the unreadable one: rc 1, not 2.
    (ref / "libraries").mkdir()
    (ref / "libraries" / "x.xml").write_bytes(b"<root><a/></root>")
    (ext / "goodmod" / "libraries").mkdir(parents=True)
    (ext / "goodmod" / "libraries" / "x.xml").write_bytes(
        b'<diff><add sel="/root/a"><b/></add></diff>')
    monkeypatch.setattr(na, "UNREADABLE", [])

    def mod_vfs(mod, packed_only=True):
        if mod.name == "somemod":
            raise OSError("simulated unreadable catalog")
        return {}
    monkeypatch.setattr(na._cat, "mod_vfs", mod_vfs)
    real_apply = na._merge.apply_diff

    def lying_apply(tree, diff):                     # reports applied, changes nothing
        import copy
        return real_apply(copy.deepcopy(tree), diff)
    monkeypatch.setattr(na._merge, "apply_diff", lying_apply)
    assert na.main() == 1, capsys.readouterr().out


# ============================================================================ GT-2 determinism_audit

def test_gt2_a_command_that_fails_twice_is_not_deterministic_output(gate_install, monkeypatch, capsys):
    da = gate_install.load("determinism_audit")
    monkeypatch.setattr(da, "WITH_BUILD", False)
    monkeypatch.setattr(da.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=2, stdout="error: cannot resolve the game's extensions directory\n", stderr=""))
    rc = da.main()
    assert rc != 0, f"every CASE exited 2 and was scored stable:\n{capsys.readouterr().out}"


def _fake_runs(seq):
    """subprocess.run stand-in returning (rc, stdout) from `seq(argv, call_no)`."""
    calls = {"n": 0}

    def fake(argv, *a, **k):
        calls["n"] += 1
        rc, out = seq(argv, calls["n"])
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr="")
    return fake


def test_gt2_rc_is_compared_per_run_and_collisions_rc_1_is_an_answer(gate_install, monkeypatch, capsys):
    da = gate_install.load("determinism_audit")
    monkeypatch.setattr(da, "WITH_BUILD", False)
    # Same text, every case answers with its own allowed rc: 0 -- x4compat's rc 1 counts.
    monkeypatch.setattr(da.subprocess, "run", _fake_runs(
        lambda argv, n: (1 if "x4compat" in argv else 0, "same\n")))
    assert da.main() == 0, capsys.readouterr().out
    # Same text, exit code differs between the two runs of one case: VARY, rc 1.
    monkeypatch.setattr(da.subprocess, "run", _fake_runs(
        lambda argv, n: ((n % 2) if "x4similar" in argv else 0, "same\n")))
    assert da.main() == 1, capsys.readouterr().out


def test_gt2_with_build_a_failed_build_is_a_failure(gate_install, monkeypatch, capsys):
    da = gate_install.load("determinism_audit")
    monkeypatch.setattr(da, "WITH_BUILD", True)
    monkeypatch.setattr(da, "store_fingerprint", lambda: "abc")   # the OLD store, unchanged
    monkeypatch.setattr(da.subprocess, "run", _fake_runs(
        lambda argv, n: (2 if "build" in argv else (1 if "x4compat" in argv else 0), "same\n")))
    rc = da.main()
    assert rc == 1, f"both builds failed and the old store was scored idempotent, rc {rc}"
    assert "FAIL  x4effective build" in capsys.readouterr().out


# ============================================================================ GT-3 floors

def test_gt3_similar_audit_over_zero_pairs_is_not_a_pass(tmp_path, monkeypatch, capsys):
    _env = import_gate("_env", module_level=False)
    (tmp_path / "ref").mkdir()
    (tmp_path / "ext").mkdir()
    monkeypatch.setattr(_env, "reference", lambda: tmp_path / "ref")
    monkeypatch.setattr(_env, "extensions", lambda: tmp_path / "ext")
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=1, stdout="", stderr="Traceback (most recent call last): ...\n"))
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(GATES / "similar_audit.py"), run_name="__main__")
    assert exc.value.code not in (0, None), (
        f"x4similar produced nothing and the audit passed:\n{capsys.readouterr().out}")


def _store(path: Path, rows: list[tuple] = ()) -> Path:
    """A synthetic effective store: (entity name, vpath, prop, value, origin) per row."""
    con = sqlite3.connect(path)
    con.execute("create table entities (id integer primary key, kind, name, klass, vpath, origin)")
    con.execute("create table attrs (entity_id, prop, value, origin)")
    for i, (name, vpath, prop, value, origin) in enumerate(rows, 1):
        con.execute("insert into entities values (?,?,?,?,?,?)", (i, "macro", name, "k", vpath, "base"))
        con.execute("insert into attrs values (?,?,?,?)", (i, prop, value, origin))
    con.commit()
    con.close()
    return path


def test_gt3_provenance_audit_over_an_empty_store_is_not_a_pass(gate_install, tmp_path, monkeypatch, capsys):
    pa = gate_install.load("provenance_audit")
    monkeypatch.setattr(pa, "DB", _store(tmp_path / "e.sqlite"))
    monkeypatch.setattr(pa, "REF", tmp_path)
    # The synthetic store has no fingerprint, so the stale-store refusal (rc 2) fired
    # FIRST and this test never reached the floor it names: a mutant that disabled the
    # floor stayed green (v3.3.1 release review). Treat the store as fresh.
    monkeypatch.setattr(pa._env, "stale_store_refusal", lambda *a, **k: None)
    rc = pa.main()
    err = capsys.readouterr().err
    assert rc == 2 and "0 items were examined" in err, (rc, err)


def test_gt3_registry_provenance_over_zero_rows_is_not_a_pass(tmp_path, monkeypatch, capsys):
    rp = import_gate("registry_provenance", module_level=False)
    live = tmp_path / "modlist.yaml"
    live.write_text("mods: []\n", encoding="utf-8")
    monkeypatch.setattr(rp._env, "registry_file", lambda: live)
    monkeypatch.setattr(rp._registry, "load_registry", lambda p: {"mods": []})
    monkeypatch.setattr(rp._registry, "save_registry",
                        lambda reg, p: Path(p).write_text("mods: []\n", encoding="utf-8"))
    rc = rp.main()
    assert rc != 0, f"0 active rows audited, rc 0:\n{capsys.readouterr().out}"


def test_gt3_xsd_parity_over_files_with_no_schema_is_not_parity(gate_install, tmp_path, monkeypatch, capsys):
    xfp = gate_install.load("xsd_fast_parity")
    ext, ref = tmp_path / "ext", tmp_path / "ref"
    (ext / "m" / "md").mkdir(parents=True)
    (ext / "m" / "md" / "probe.xml").write_text('<mdscript name="p"><cues/></mdscript>',
                                                encoding="utf-8")
    ref.mkdir()                                            # no libraries/ -> no schema anywhere
    monkeypatch.setattr(xfp, "EXT", ext)
    monkeypatch.setattr(xfp, "LIMIT", 0)
    monkeypatch.setattr(xfp._merge, "Config", lambda *a, **k: types.SimpleNamespace(reference=ref))
    rc = xfp.main()
    out = capsys.readouterr().out
    # The premise is that the ONE file reached the gate and had no schema. Before the fix
    # it was counted as "compared : 1"; a file the full path never validated is not a
    # comparison, so it is now reported as skipped and the denominator is 0.
    assert "full validation skipped: 1" in out, f"fixture premise moved:\n{out}"
    assert rc == 2, f"the only file had no schema to compare against, and it read as parity:\n{out}"


# ============================================================================ GT-4 rc handling

def test_gt4_edge_sweep_does_not_pass_hostile_inputs_that_all_exit_0(monkeypatch, capsys):
    es = import_gate("edge_sweep", module_level=False)
    monkeypatch.setattr(es, "run", lambda argv, env=None, timeout=900, cwd=None:
                        (0, "OK: no issues found\n"))
    rc = es.main()
    assert rc != 0, ("`x4validate <missing dir>` exiting 0 with 'OK' is the confident wrong "
                     "answer the docstring promises to catch:\n" + capsys.readouterr().out[-800:])


def test_gt4_stress_sweep_names_a_corpus_it_could_not_read(tmp_path, monkeypatch, capsys):
    ss = import_gate("stress_sweep", module_level=False)
    missing = tmp_path / "no_such_corpus"
    monkeypatch.setattr(ss, "CORPUS", missing)
    monkeypatch.setattr(ss, "run", lambda argv, timeout=900, cwd=None: (0, "ok\n"))
    rc = ss.main()
    out = capsys.readouterr()
    assert rc == 2 or missing.name in (out.out + out.err), (
        f"rc={rc}: axis 1 was asked for and skipped without a word")


def test_gt4_control_bytes_hits_outrank_an_unreadable_path(tmp_path, monkeypatch):
    cb = import_gate("control_bytes", module_level=False)
    monkeypatch.setattr(cb, "tracked_text_files", lambda: [])
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"collapsed" + bytes([8]) + b"escape\n")
    rc = cb.main([str(bad), str(tmp_path / "gone.md")])
    assert rc == 1, f"a live 0x08 was found and run-gates.sh would bucket rc {rc} as CANNOT"


def test_gt4_oracle_index_without_ground_truth_is_cannot_not_fail(gate_install, tmp_path, monkeypatch):
    _env = import_gate("_env", module_level=False)
    log = tmp_path / "debug.txt"
    log.write_text("", encoding="utf-8")
    monkeypatch.setattr(_env, "oracle_log", lambda: log)
    oi = gate_install.load("oracle_index")
    monkeypatch.setattr(oi._debuglog, "parse_debug", lambda p: [])
    with pytest.raises(SystemExit) as exc:
        oi.main()
    code = exc.value.code
    rc = code if isinstance(code, int) else 1                # what the interpreter would exit with
    assert rc == 2, f"no ground truth read as a FAILED gate (rc {rc}): {code!r}"


def test_gt4_toolkit_usage_refusal_is_rc_2(tmp_path, monkeypatch, capsys):
    tu = import_gate("toolkit_usage", module_level=False)
    cap = {"cli": "x4validate", "sub": "", "invoked": 3, "named": 3,
           "first": "2026-09-01", "last": "2026-09-20", "last_named": "2026-09-20"}
    monkeypatch.setattr(tu, "audit", lambda: {"caps": {"x4validate": cap}, "unenumerable": [],
                                              "subs_by_cli": {"x4validate": []}})
    monkeypatch.setattr(tu, "qa_sweep_coverage",
                        lambda subs: (None, "could not import qa_sweep (SystemExit: 2)"))
    monkeypatch.setattr(tu, "RECORD", False)
    monkeypatch.setattr(tu, "BASELINE", tmp_path / "no-baseline.json")
    rc = tu.main()
    assert rc == 2, f"'could not evaluate' was reported as a finding, rc {rc}"
    # ...and a real finding beside the refusal still outranks it.
    monkeypatch.setattr(tu, "audit", lambda: {"caps": {"x4validate": cap},
                                              "unenumerable": ["x4live: --help timed out"],
                                              "subs_by_cli": {"x4validate": []}})
    assert tu.main() == 1


def test_gt4_stress_sweep_judges_each_cell_against_its_expected_rc(tmp_path, monkeypatch):
    ss = import_gate("stress_sweep", module_level=False)
    # The table names every pathological mod the sweep builds -- a missing key would
    # be a KeyError mid-run, an extra one a cell that never runs.
    assert {n for n, _ in ss.build_pathological(tmp_path)} == set(ss.PATHOLOGICAL_EXPECT)
    monkeypatch.setattr(ss, "run", lambda argv, timeout=900, cwd=None: (0, "OK\n"))
    assert ss.judge("billion laughs", ["x4validate"], ss.PATHOLOGICAL_EXPECT[
        "path_billion_laughs"])[0] == "FAIL", "an entity bomb read as a clean 0"
    monkeypatch.setattr(ss, "run", lambda argv, timeout=900, cwd=None: (3, "degraded\n"))
    assert ss.judge("billion laughs", ["x4validate"], ss.PATHOLOGICAL_EXPECT[
        "path_billion_laughs"])[0] == "ok"
    # A whole run where every tool exits 0 is not a pass: the hostile cells expect 1/3.
    monkeypatch.setattr(ss, "CORPUS", None)
    monkeypatch.setattr(ss, "run", lambda argv, timeout=900, cwd=None: (0, "OK\n"))
    assert ss.main() == 1


# ============================================================================ GT-5 aggregate / file-wide

def test_gt5_cross_tool_hard_winner_is_checked_on_the_collided_attr(tmp_path, monkeypatch, capsys):
    ct = import_gate("cross_tool", module_level=False)
    from x4validate import _compat
    vp = "assets/units/size_s/macros/ship_x_macro.xml"
    db = _store(tmp_path / "e.sqlite", [("ship_a_macro", vp, "hull.max", "900", "modB"),
                                        ("ship_b_macro", vp, "hull.max", "100", "modA")])
    c = _compat.Collision(vpath=vp, kind="HARD",
                          target="/macros/macro[@name='ship_a_macro']/properties/hull/@max",
                          mods=["modB", "modA"], winner="modA")
    monkeypatch.setattr(ct, "failures", [])
    monkeypatch.setattr(ct._env, "effective_db", lambda: db)
    monkeypatch.setattr(ct._env, "extensions", lambda: tmp_path)
    monkeypatch.setattr(ct._merge, "Config", lambda *a, **k: None)
    monkeypatch.setattr(ct._compat, "analyze", lambda *a, **k: types.SimpleNamespace(collisions=[c]))
    # The synthetic store carries no fingerprint; since GT-6 that is a stale-store
    # refusal, which would make this test pass or fail for a reason it is not about.
    monkeypatch.setattr(ct._env, "stale_store_refusal", lambda db, who: None)
    # Every target is resolved in the effective base tree (review of fe8065b), so the
    # setup supplies one; the assertion is unchanged.
    from lxml import etree
    tree = etree.fromstring(b'<macros><macro name="ship_a_macro"><properties><hull max="1"/>'
                            b'</properties></macro><macro name="ship_b_macro"><properties>'
                            b'<hull max="1"/></properties></macro></macros>')
    monkeypatch.setattr(ct._merge, "build_effective",
                        lambda vp, cfg: types.SimpleNamespace(tree=tree))
    ct.check_cross_tool_agreement()
    assert ct.failures, ("modA is the live winner on ship_a_macro hull.max, the store says modB "
                         "owns it, and the check agreed because modA owns SOMETHING in the file:\n"
                         + capsys.readouterr().out)


def _store_k(path: Path, rows: list[tuple]) -> Path:
    """Like `_store`, with the entity KIND: (kind, name, vpath, prop, value, origin).
    Rows sharing (kind, name, vpath) are ONE entity, as in the real store."""
    con = sqlite3.connect(path)
    con.execute("create table entities (id integer primary key, kind, name, klass, vpath, origin)")
    con.execute("create table attrs (entity_id, prop, value, origin)")
    ids: dict[tuple, int] = {}
    for kind, name, vpath, prop, value, origin in rows:
        k = (kind, name, vpath)
        if k not in ids:
            ids[k] = len(ids) + 1
            con.execute("insert into entities values (?,?,?,?,?,?)",
                        (ids[k], kind, name, "k", vpath, "base"))
        con.execute("insert into attrs values (?,?,?,?)", (ids[k], prop, value, origin))
    con.commit()
    con.close()
    return path


def _cross_tool_with(monkeypatch, tmp_path, db, collisions, tree=None):
    ct = import_gate("cross_tool", module_level=False)
    monkeypatch.setattr(ct, "failures", [])
    monkeypatch.setattr(ct, "cannot", [])
    monkeypatch.setattr(ct._env, "effective_db", lambda: db)
    monkeypatch.setattr(ct._env, "extensions", lambda: tmp_path)
    monkeypatch.setattr(ct._env, "stale_store_refusal", lambda db, who: None)
    monkeypatch.setattr(ct._merge, "Config", lambda *a, **k: None)
    monkeypatch.setattr(ct._merge, "build_effective",
                        lambda vp, cfg: types.SimpleNamespace(tree=tree))
    monkeypatch.setattr(ct._compat, "analyze",
                        lambda *a, **k: types.SimpleNamespace(collisions=collisions))
    ct.check_cross_tool_agreement()
    return ct


def test_gt5_cross_tool_positional_hard_target_resolves_to_its_entity(tmp_path, monkeypatch):
    """x4compat emits lxml getpath targets (`/wares/ware[2]`), not name predicates. The
    position is resolved in the effective base tree to the ware it names, and only THAT
    ware's attrs are compared -- both directions, so the check can go red and green."""
    from lxml import etree
    from x4validate import _compat
    vp = "libraries/wares.xml"
    tree = etree.fromstring(b'<wares><ware id="ore"><price max="1"/></ware>'
                            b'<ware id="silicon"><price max="2"/></ware></wares>')
    db = _store_k(tmp_path / "e.sqlite", [("ware", "ore", vp, "price.max", "1", "modA"),
                                          ("ware", "silicon", vp, "price.max", "9", "modB")])
    hard = lambda w: _compat.Collision(vpath=vp, kind="HARD", target="/wares/ware[2]",  # noqa: E731
                                       mods=["modA", "modB"], winner=w)
    ct = _cross_tool_with(monkeypatch, tmp_path, db, [hard("modB")], tree)
    assert not ct.failures and not ct.cannot, (ct.failures, ct.cannot)
    ct = _cross_tool_with(monkeypatch, tmp_path, db, [hard("modA")], tree)
    assert ct.failures, "modA owns ore, not silicon (ware[2]); file-wide it would have agreed"


def test_gt5_cross_tool_a_kind_with_every_row_absent_is_not_0_of_0(tmp_path, monkeypatch):
    from x4validate import _compat
    db = _store(tmp_path / "e.sqlite", [("ore", "libraries/wares.xml", "price.max", "1", "modA")])
    c = _compat.Collision(vpath="md/some_script.xml", kind="HARD", target="/mdscript/cues/cue",
                          mods=["modA", "modB"], winner="modB")
    ct = _cross_tool_with(monkeypatch, tmp_path, db, [c])
    assert not ct.failures
    assert any(x.startswith("HARD:") for x in ct.cannot), (
        f"1 HARD row, 0 checkable, and it passed as 0/0: cannot={ct.cannot}")


def test_gt5_update_corpus_credits_a_detection_only_to_its_own_file(tmp_path, monkeypatch):
    uc = import_gate("update_corpus", module_level=False)
    from x4validate import _check
    F = _check.Finding
    one_file = [
        F("error", "xsd", "attribute 'space' is required but missing", vpath="md/probe_lua.xml"),
        F("warn", "migration", "Lua_Loader .keys.list kuertee_hud", vpath="md/probe_lua.xml"),
        F("warn", "exprlint", "random '.' [...] .keys.count", vpath="md/probe_list.xml"),
    ]
    monkeypatch.setattr(uc._check, "validate", lambda *a, **k: types.SimpleNamespace(findings=one_file))
    failures: list[str] = []
    uc.check("LOOSE  mod", tmp_path, failures)
    assert failures, ("3 findings in 2 files were credited as detecting all 8 planted breaks "
                      "across 8 files")


def test_gt5_update_corpus_credits_every_case_found_in_its_own_file(tmp_path, monkeypatch):
    """The other direction: one finding per planted case, each in the case's own file
    (the shape MEASURED on the real run, loose and packed), is a full detection."""
    uc = import_gate("update_corpus", module_level=False)
    from x4validate import _check
    per_case = [_check.Finding("warn", c.category, f"...{c.wants}...", vpath=c.vpath)
                for c in uc.CASES]
    monkeypatch.setattr(uc._check, "validate",
                        lambda *a, **k: types.SimpleNamespace(findings=per_case))
    failures: list[str] = []
    uc.check("LOOSE  mod", tmp_path, failures)
    assert failures == []
    # ...and moving ONE case's finding to a sibling file loses exactly that case.
    moved = [*per_case[1:], _check.Finding("warn", uc.CASES[0].category,
                                           f"...{uc.CASES[0].wants}...",
                                           vpath=uc.CASES[1].vpath)]
    monkeypatch.setattr(uc._check, "validate",
                        lambda *a, **k: types.SimpleNamespace(findings=moved))
    uc.check("LOOSE  mod", tmp_path, failures)
    assert len(failures) == 1 and uc.CASES[0].case_id in failures[0], failures


def test_gt5_the_word_selftest_in_prose_is_not_a_check(tmp_path):
    rr = import_gate("register_rederivation", module_level=False)
    body = " FIXED 2026-09-01\n\nThere is no selftest for this, and no test either.\n"
    assert rr.names_a_check(body, tmp_path) is None


def test_gt5_a_dead_path_citation_is_never_silent(tmp_path, monkeypatch, capsys):
    """RULE REVISED by the review of 311a231 (orchestrator, 2026-09-25): the original
    assertion here -- ANY dead cited path voids the entry -- flagged six real entries
    (F60/F61/F73/F76/F78/F87) that cite a LIVE check and name a retired one as history,
    and the register's wording does not reliably mark which is which. The GT-5 defect
    was that a dead citation was IGNORED; it is now named on every run instead. An
    entry whose ONLY cited checks are dead is still NOT covered."""
    rr = import_gate("register_rederivation", module_level=False)
    (tmp_path / "gates").mkdir()
    (tmp_path / "gates" / "unrelated.py").write_text("", encoding="utf-8")
    body = (" FIXED\n\nRe-derived by tests/test_deleted_long_ago.py; see also "
            "gates/unrelated.py for context.\n")
    assert rr.dead_citations(body, tmp_path) == ["tests/test_deleted_long_ago.py"]
    assert rr.names_a_check(body, tmp_path) == "gates/unrelated.py"
    only_dead = " FIXED\n\nRe-derived by tests/test_deleted_long_ago.py.\n"
    assert rr.names_a_check(only_dead, tmp_path) is None
    # ...and main() prints the dead one as a NOTE.
    reg = tmp_path / "BLIND-SPOTS.md"
    reg.write_bytes(("## F1 -- x FIXED" + body).encode("utf-8"))
    monkeypatch.setattr(rr, "REGISTER", reg)
    monkeypatch.setattr(rr, "ROOT", tmp_path)
    monkeypatch.setattr(rr, "BASELINE", tmp_path / "b.json")
    monkeypatch.setattr(rr, "RECORD", False)
    assert rr.main() == 0
    assert "NOTE F1: cites 1 path(s) that no longer exist" in capsys.readouterr().out


def test_gt5_a_quoted_exemption_marker_is_not_an_exemption(tmp_path):
    rr = import_gate("register_rederivation", module_level=False)
    text = ('## F999 -- something FIXED\n\nWe chose not to write "NO RE-DERIVATION" here; '
            "the check is still owed.\n")
    ok, missing = rr.audit(text, tmp_path)
    assert "F999" in missing, f"ok={ok} missing={missing}"


def _schema_sweep(monkeypatch, tmp_path, per_mod: dict[str, tuple[int, int]]):
    """schema_sweep over synthetic mods: name -> (gating, advisory). The totals and
    KNOWN_REAL are pinned to whatever these rows sum to, so ONLY the per-mod
    comparison can tell two runs apart."""
    sw = import_gate("schema_sweep", module_level=False)
    from x4validate import _check
    ext = tmp_path / "ext"
    for name in per_mod:
        (ext / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sw._env, "extensions", lambda: ext)
    monkeypatch.setattr(sw._merge, "Config", lambda *a, **k: None)

    def fake(mod, cfg, report):
        e, i = per_mod[mod.name]
        report.findings += [_check.Finding("error", "xsd", "e")] * e
        report.findings += [_check.Finding("info", "xsd", "i")] * i
        report.notes.append("effective-schema: 1 pair(s)")
    monkeypatch.setattr(sw._check, "check_effective_schema", fake)
    monkeypatch.setattr(sw, "EXPECT_PAIRS", len(per_mod))
    monkeypatch.setattr(sw, "EXPECT_ERR", sum(e for e, _ in per_mod.values()))
    monkeypatch.setattr(sw, "EXPECT_INFO", sum(i for _, i in per_mod.values()))
    monkeypatch.setattr(sw, "EXPECT_SUPPRESSED", 0)
    monkeypatch.setattr(sw, "EXPECT_SKIPPED", 0)
    monkeypatch.setattr(sw, "EXPECT_MODS_FLAGGED", sum(1 for r in per_mod.values() if any(r)))
    monkeypatch.setattr(sw, "KNOWN_REAL", {})
    monkeypatch.setattr(sw, "BASELINE", tmp_path / "schema-baseline.json")
    return sw


def test_gt5_schema_sweep_findings_moving_between_mods_do_not_net_to_zero(tmp_path, monkeypatch):
    sw = _schema_sweep(monkeypatch, tmp_path, {"moda": (3, 1), "modb": (1, 1)})
    monkeypatch.setattr(sw, "RECORD", True)
    assert sw.main() == 0
    monkeypatch.setattr(sw, "RECORD", False)
    assert sw.main() == 0, "an unchanged per-mod table must pass"
    # Same totals (4 gating, 2 advisory, 2 flagged), two findings moved from moda to modb.
    sw = _schema_sweep(monkeypatch, tmp_path, {"moda": (1, 1), "modb": (3, 1)})
    monkeypatch.setattr(sw, "RECORD", False)
    assert sw.main() == 1, "every total held while two gating findings changed mods"


@pytest.mark.parametrize("content", [None, b"{ not json", b'{"mods": {}}',
                                     b'{"_format": 99, "mods": {}}'])
def test_gt5_schema_sweep_without_a_readable_per_mod_baseline_is_not_a_pass(
        tmp_path, monkeypatch, capsys, content):
    sw = _schema_sweep(monkeypatch, tmp_path, {"moda": (3, 1)})
    monkeypatch.setattr(sw, "RECORD", False)
    if content is not None:
        sw.BASELINE.write_bytes(content)
    assert sw.main() == 2
    assert "--record" in capsys.readouterr().err


# ============================================================================ GT-6

def test_gt6_provenance_audit_refuses_a_stale_store(gate_install, tmp_path, monkeypatch, capsys):
    pa = gate_install.load("provenance_audit")
    from x4validate import _effective
    vp = "assets/x_macro.xml"
    (tmp_path / "assets").mkdir()
    (tmp_path / vp).write_text('<macros><macro name="m"><properties><hull max="100"/>'
                               '</properties></macro></macros>', encoding="utf-8")
    monkeypatch.setattr(pa, "DB", _store(tmp_path / "e.sqlite", [("m", vp, "hull.max", "200", "modA")]))
    monkeypatch.setattr(pa, "REF", tmp_path)
    monkeypatch.setattr(_effective, "store_freshness", lambda con, config=None: types.SimpleNamespace(
        fresh=False, determinable=True, reasons=["content axis moved"],
        banner=lambda *a, **k: "STALE"))
    rc = pa.main()
    assert rc == 2, f"a STALE store was audited as current, rc {rc}:\n{capsys.readouterr().out}"


def test_gt6_cross_tool_refuses_a_stale_store(tmp_path, monkeypatch, capsys):
    ct = import_gate("cross_tool", module_level=False)
    from x4validate import _compat, _effective
    vp = "assets/x_macro.xml"
    db = _store(tmp_path / "e.sqlite", [("m", vp, "hull.max", "200", "modA")])
    c = _compat.Collision(vpath=vp, kind="HARD", target="/macros/macro[@name='m']/properties/hull/@max",
                          mods=["modB", "modA"], winner="modA")
    monkeypatch.setattr(ct, "failures", [])
    monkeypatch.setattr(ct._env, "effective_db", lambda: db)
    monkeypatch.setattr(ct._env, "extensions", lambda: tmp_path)
    monkeypatch.setattr(ct._merge, "Config", lambda *a, **k: None)
    monkeypatch.setattr(ct._compat, "analyze", lambda *a, **k: types.SimpleNamespace(collisions=[c]))
    monkeypatch.setattr(_effective, "store_freshness", lambda con, config=None: types.SimpleNamespace(
        fresh=False, determinable=True, reasons=["content axis moved"],
        banner=lambda *a, **k: "STALE"))
    ct.check_cross_tool_agreement()
    out = capsys.readouterr().out
    assert ct.failures or "stale" in out.lower(), f"scored against a STALE store as if current:\n{out}"


@pytest.mark.parametrize("tol", ["inf", "-1"])
def test_gt6_claims_audit_rejects_a_meaningless_tolerance(tmp_path, monkeypatch, tol):
    ca = import_gate("claims_audit", module_level=False)
    tsv = tmp_path / "CLAIMS.tsv"
    tsv.write_text("\t".join(["macro", "m", "hull.max", "100", tol, "doc.md:1", "effective"]) + "\n",
                   encoding="utf-8")
    monkeypatch.setattr(ca, "_claims", lambda: tsv)
    [row] = list(ca.rows())
    assert row[-1] is not None, f"tolerance {tol!r} parsed as {row[5]!r} with no error"


# GT-6 claude_md_budget: `test_gt6_claude_md_budget_ratchet_tightens` is RETIRED by user
# decision (2026-09-25): the budget stays a manual-`--record` ceiling, so an automatic
# floor-lowering is not the intended behaviour. The docs that called it a ratchet were
# corrected instead (gates/claude_md_budget.py, gates/README.md, CHANGELOG.md).


# GT-6 diff_truth -- was a module-level script (untestable without a real install);
# these pin the two verdicts that used to be missing.

def _diff_headline(changed: int, attrs: int) -> str:
    return (f"changed files: {changed}   added: 0   removed: 0\n"
            f"total attr changes: {attrs}\n")


def test_gt6_diff_truth_changed_files_mismatch_fails_not_notes():
    dt = import_gate("diff_truth", module_level=False)
    planted = {("a.xml", "max", "1", "7332.5")}
    detail = "a.xml  max  1 -> 7332.5\n"
    assert dt.judge(_diff_headline(1, 1) + detail, planted, 1) is True     # control
    assert dt.judge(_diff_headline(2, 1) + detail, planted, 1) is False, (
        "x4diff reported 2 changed files for 1 mutated file -- that was only a NOTE")


def test_gt6_diff_truth_nothing_planted_is_not_exact(tmp_path, monkeypatch, capsys):
    dt = import_gate("diff_truth", module_level=False)
    mod = tmp_path / "ext" / "textonly"
    mod.mkdir(parents=True)
    for i in range(5):                                   # 5 XML files, no numeric attribute
        (mod / f"f{i}.xml").write_bytes(b'<root><a kind="text"/></root>')
    monkeypatch.setattr(dt._env, "extensions", lambda: tmp_path / "ext")
    monkeypatch.setattr(dt, "run_x4diff", lambda a, b: _diff_headline(0, 0))
    assert dt.main() == 2, f"0 planted, 0 found read as exact:\n{capsys.readouterr().out}"
    (tmp_path / "ext" / "textonly").rename(tmp_path / "ext" / "_gone")
    for f in (tmp_path / "ext" / "_gone").iterdir():
        f.unlink()
    assert dt.main() == 2, "no candidate mod at all"


def test_gt6_obtainability_audit_names_an_unreadable_mod_file(tmp_path, monkeypatch, capsys):
    """The per-mod scan passed an inline `[]` as iter_mod_xml's unreadable list and threw
    it away: a mod file that would not parse contributed 0 references, unmentioned."""
    oa = import_gate("obtainability_audit", module_level=False)
    mod = tmp_path / "somemod" / "assets"
    mod.mkdir(parents=True)
    (mod / "good_macro.xml").write_bytes(b'<macros><macro name="m"/></macros>')
    (mod / "broken_macro.xml").write_bytes(b"<macros><macro name=")
    per_mod, unreadable = oa.scan_mods([{"folder": "somemod", "path": str(tmp_path / "somemod")}],
                                       dead={})
    assert per_mod == {}
    assert len(unreadable) == 1 and unreadable[0].startswith("somemod/assets/broken_macro.xml")
    # ...and main() prints it by name.
    now = {"base_macro_files_scanned": 1, "base_macro_files_unreadable": 0,
           "mod_files_unreadable": 1, "unreadable_files": unreadable,
           "deprecated_only_macros_vanilla": 0, "deprecated_only_macros_effective": 0,
           "live_macros_with_deprecated_ammo": 0, "of_those_sold_by_a_live_ware": 0,
           "mods_referencing_deprecated": {}}
    monkeypatch.setattr(oa, "audit", lambda: dict(now))
    monkeypatch.setattr(oa, "RECORD", False)
    monkeypatch.setattr(oa, "BASELINE", tmp_path / "b.json")
    (tmp_path / "b.json").write_text(json.dumps(now), encoding="utf-8")
    assert oa.main() == 0
    assert "unreadable: somemod/assets/broken_macro.xml" in capsys.readouterr().out


# --- review of fe8065b: the POSITION of every step matters, not only the entity ----------

_WARE_TREE = (b'<wares><ware id="ore"><production method="default" time="10"/>'
              b'<production method="alt" time="20"/></ware></wares>')


def _ware_store(tmp_path):
    vp = "libraries/wares.xml"
    return vp, _store_k(tmp_path / "e.sqlite", [
        ("ware", "ore", vp, "@id", "ore", "base"),
        ("ware", "ore", vp, "production[default].time", "10", "modA"),
        ("ware", "ore", vp, "production[alt].time", "20", "modB")])


@pytest.mark.parametrize("target", ["/wares/ware[1]/production[2]",
                                    "/wares/ware[1]/production[2]/@time"])
def test_gt5_cross_tool_a_sibling_position_is_not_dropped(tmp_path, monkeypatch, target):
    """REPRODUCED in review: production[2] became the prefix `production`, matched BOTH
    siblings, and the wrong winner (modA owns production[1]) agreed; the @time form
    became `production.time`, matched nothing, and was silently counted absent."""
    from lxml import etree
    from x4validate import _compat
    vp, db = _ware_store(tmp_path)
    tree = etree.fromstring(_WARE_TREE)
    hard = lambda w: _compat.Collision(vpath=vp, kind="HARD", target=target,  # noqa: E731
                                       mods=["modA", "modB"], winner=w)
    ct = _cross_tool_with(monkeypatch, tmp_path, db, [hard("modB")], tree)
    assert not ct.failures and not ct.cannot, (ct.failures, ct.cannot)     # control
    ct = _cross_tool_with(monkeypatch, tmp_path, db, [hard("modA")], tree)
    assert ct.failures, "modA owns production[default], not production[2] (alt)"


def test_gt5_cross_tool_refuses_when_the_checker_cannot_place_its_population(
        tmp_path, monkeypatch, capsys):
    """Coverage floor: rows in a store-tracked file that the CHECKER cannot map (not the
    explained kinds: untracked file, document-level target, removed entity) may be at
    most MAX_UNEXPLAINED_SHARE of the mappable rows -- over it the section is CANNOT, even
    though every row it did map agreed."""
    from lxml import etree
    from x4validate import _compat
    vp, db = _ware_store(tmp_path)
    tree = etree.fromstring(_WARE_TREE)
    good = _compat.Collision(vpath=vp, kind="HARD", target="/wares/ware[1]/production[2]",
                             mods=["modA", "modB"], winner="modB")
    lost = [_compat.Collision(vpath=vp, kind="HARD", target=f"/wares/ware[{9 + i}]",
                              mods=["modA", "modB"], winner="modB") for i in range(2)]
    ct = _cross_tool_with(monkeypatch, tmp_path, db, [good, *lost], tree)
    assert not ct.failures
    assert any(x.startswith("HARD:") for x in ct.cannot), ct.cannot
    out = capsys.readouterr().out
    assert "checked 1 of 3 mappable" in out and "2 unmapped by the checker" in out


def test_gt5_schema_sweep_record_refuses_when_totals_do_not_match(tmp_path, monkeypatch):
    """Review of a202814: --record wrote the per-mod baseline even when the totals or
    KNOWN_REAL had just failed, so a regression could be recorded as the new normal."""
    sw = _schema_sweep(monkeypatch, tmp_path, {"moda": (3, 1), "modb": (1, 1)})
    monkeypatch.setattr(sw, "EXPECT_ERR", 99)                # the totals check fails
    monkeypatch.setattr(sw, "RECORD", True)
    assert sw.main() == 1
    assert not sw.BASELINE.exists(), "a per-mod baseline was recorded over a failing run"


def test_gt8_toolkit_usage_with_no_baseline_is_not_a_pass(tmp_path, monkeypatch, capsys):
    """AUDIT-2026-09-24 GT-8: no baseline means drift was NOT checked; its siblings return 2
    in this state, and so must it (it returned 0)."""
    tu = import_gate("toolkit_usage", module_level=False)
    cap = {"cli": "x4validate", "sub": "", "invoked": 3, "named": 3,
           "first": "2026-09-01", "last": "2026-09-20", "last_named": "2026-09-20"}
    monkeypatch.setattr(tu, "audit", lambda: {"caps": {"x4validate": cap}, "unenumerable": [],
                                              "subs_by_cli": {"x4validate": []}})
    # Coverage fully evaluable and complete, so the ONLY unevaluated part is drift.
    cov = types.SimpleNamespace(undecidable=[], covers=lambda cli, sub: True)
    monkeypatch.setattr(tu, "qa_sweep_coverage", lambda subs: (cov, None))
    monkeypatch.setattr(tu, "RECORD", False)
    monkeypatch.setattr(tu, "BASELINE", tmp_path / "no-baseline.json")
    assert tu.main() == 2
    assert "no baseline" in capsys.readouterr().out


# --- release review 2026-09-26 (pre-arc): the planted set is a MULTISET -----------------

def test_diff_truth_counts_identical_planted_changes_as_a_multiset(tmp_path, monkeypatch):
    """Two sibling elements with the same attr and value, both mutated: the planted SET
    collapsed them to one row, so the count check went red against a tool that correctly
    reported 2 attr changes -- a failure with nothing wrong."""
    import random
    dt = import_gate("diff_truth", module_level=False)
    f = tmp_path / "a.xml"
    f.write_bytes(b'<r><a max="1"/><a max="1"/></r>')
    monkeypatch.setattr(dt, "N_MUTATIONS", 2)
    planted, by_tree = dt.plant(tmp_path, random.Random(1))
    assert len(planted) == 2, planted
    row = "a.xml  max  1 -> 7332.5\n"
    assert dt.judge(_diff_headline(1, 2) + row + row, planted, 1) is True


def test_TWIN_diff_truth_still_fails_when_one_of_two_identical_changes_is_missing(tmp_path,
                                                                                    monkeypatch):
    import random
    dt = import_gate("diff_truth", module_level=False)
    f = tmp_path / "a.xml"
    f.write_bytes(b'<r><a max="1"/><a max="1"/></r>')
    monkeypatch.setattr(dt, "N_MUTATIONS", 2)
    planted, _ = dt.plant(tmp_path, random.Random(1))
    row = "a.xml  max  1 -> 7332.5\n"
    assert dt.judge(_diff_headline(1, 2) + row, planted, 1) is False, (
        "the detail shows ONE of the two planted changes")


# --- release review 2026-09-26 (pre-arc): stress_sweep states what it skipped, and the
# XXE cell proves the entity was NOT expanded rather than accepting any rc 1 -----------

def _fast_sweep(ss, monkeypatch, rc_for):
    """Every tool call answers instantly; *rc_for(argv)* picks the exit code."""
    monkeypatch.setattr(ss, "run", lambda argv, timeout=900, cwd=None: (rc_for(argv), "x\n"))


def _pathological_rc(argv):
    return 3 if any("path_" in a and not a.endswith(("path_many_ops", "path_unicode",
                                                      "path_cycle_a", "path_cycle_b",
                                                      "path_large", "path_nested_diff"))
                    for a in argv) else 0


def test_stress_sweep_without_a_corpus_says_the_unseen_axis_was_NOT_CHECKED(
        monkeypatch, capsys):
    ss = import_gate("stress_sweep", module_level=False)
    monkeypatch.setattr(ss, "CORPUS", None)
    _fast_sweep(ss, monkeypatch, _pathological_rc)
    ss.main()
    out = capsys.readouterr().out
    assert "NOT CHECKED" in out and "--corpus" in out, out[-1500:]


def test_TWIN_stress_sweep_with_a_corpus_does_not_say_NOT_CHECKED(tmp_path, monkeypatch,
                                                                   capsys):
    ss = import_gate("stress_sweep", module_level=False)
    corpus = tmp_path / "corpus"
    (corpus / "a_mod").mkdir(parents=True)
    (corpus / "a_mod" / "content.xml").write_text('<content id="a"/>', encoding="utf-8")
    monkeypatch.setattr(ss, "CORPUS", corpus)
    _fast_sweep(ss, monkeypatch, _pathological_rc)
    ss.main()
    assert "NOT CHECKED" not in capsys.readouterr().out


def test_stress_sweep_XXE_cell_goes_red_when_the_marker_reaches_the_output(monkeypatch):
    """rc 1 (or 3) was accepted with nothing checking that the external entity stayed
    UNexpanded. The entity now points at a sentinel file; its marker in any output is an
    expansion, whatever the exit code."""
    ss = import_gate("stress_sweep", module_level=False)
    monkeypatch.setattr(ss, "run", lambda argv, timeout=900, cwd=None:
                        (1, f"ware x name={ss.XXE_MARKER}\n"))
    st, detail = ss.judge("xxe", ["x4validate"], ss.PATHOLOGICAL_EXPECT["path_xxe"],
                          forbid=ss.XXE_MARKER)
    assert st == "FAIL" and "EXPANDED" in detail, detail
    monkeypatch.setattr(ss, "run", lambda argv, timeout=900, cwd=None: (1, "refused\n"))
    assert ss.judge("xxe", ["x4validate"], ss.PATHOLOGICAL_EXPECT["path_xxe"],
                    forbid=ss.XXE_MARKER)[0] == "ok"


def test_stress_sweep_XXE_in_process_check_catches_a_parser_that_expands(tmp_path,
                                                                        monkeypatch):
    """The toolkit's parsers are asked directly: a parser that resolves external entities
    puts the sentinel's marker into the tree, and the check names it."""
    from lxml import etree
    ss = import_gate("stress_sweep", module_level=False)
    made = dict(ss.build_pathological(tmp_path))
    files = sorted((made["path_xxe"] / "libraries").glob("*.xml"))
    assert ss.xxe_expanded(files) == [], "control: the real parsers must not expand"
    resolving = etree.XMLParser(resolve_entities=True, load_dtd=True, no_network=True)
    monkeypatch.setattr(ss._merge, "parse_file",
                        lambda p: etree.parse(str(p), resolving).getroot())
    assert ss.xxe_expanded(files), "a parser that EXPANDS the entity went unnoticed"
