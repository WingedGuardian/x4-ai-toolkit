"""A faster runner must preserve individual outcomes and isolate mutation bytes."""
import importlib.util
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from _layout import require_repo
require_repo("scripts/verify-hook-tests.py", module_level=True, why="parallel mutation runner source")

ROOT=Path(__file__).resolve().parents[3]


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod


batch=load("pytest_batches",ROOT/"tools/x4validate/scripts/run-pytest-batches.py")
vht=load("vht_parallel",ROOT/"scripts/verify-hook-tests.py")


def report(outcome="passed"):
    return {"fingerprint":"same","collected":["a"],"tests":{"a":{"call":{
            "outcome":outcome,"duration":1,"skip_reason":"","wasxfail":""}}}}


def test_comparison_is_per_item_and_ignores_only_duration():
    a=report();b=report();b["tests"]["a"]["call"]["duration"]=100
    assert batch.compare(a,b)==[]
    assert batch.compare(a,report("failed"))==["a"]
    b["tests"]["a"]["call"]["skip_reason"]="new exclusion"
    assert batch.compare(a,b)==["a"]
    import tempfile
    a=report("skipped");b=report("skipped")
    root=Path(tempfile.gettempdir())
    reason="WinError 1314: "+str(root/"x4-pytest-batch-abcdefgh"/"tests"/"precious.lua")
    # Actual skip messages repr-escape the path inside the OS error.
    reason=reason.replace("\\","\\\\")
    a["tests"]["a"]["call"]["skip_reason"]=reason
    b["tests"]["a"]["call"]["skip_reason"]=reason.replace("abcdefgh","1234_wxy")
    assert batch.compare(a,b)==[]
    for changed in (reason.replace("1314","5"),reason.replace("precious.lua","other.lua")):
        b["tests"]["a"]["call"]["skip_reason"]=changed
        assert batch.compare(a,b)==["a"]
    a["tests"]["a"]["call"]["skip_reason"]="/outside/x4-pytest-batch-abcdefgh/target"
    b["tests"]["a"]["call"]["skip_reason"]="/outside/x4-pytest-batch-1234_wxy/target"
    assert batch.compare(a,b)==["a"]


def test_comparison_refuses_changed_source_missing_or_duplicate_tests():
    for key,value in (("fingerprint","changed"),("collected",[]),("collected",["a","a"])):
        b=report();b[key]=value
        with pytest.raises(ValueError):batch.compare(report(),b)


def test_unreviewed_module_stays_exclusive_even_without_markers(tmp_path):
    p=tmp_path/"test_x.py";p.write_text("def test_x(): assert True\n",encoding="utf-8")
    r=batch.classify(p)
    assert r["mode"]=="exclusive" and not r["reviewed"]


def test_each_mutation_gets_its_own_directory_and_bytes(tmp_path,monkeypatch):
    (tmp_path/"hook_facts.py").write_text("pristine",encoding="utf-8")
    monkeypatch.setattr(vht,"HOOKS",tmp_path);monkeypatch.setattr(vht,"FILES",["hook_facts.py"])
    monkeypatch.setattr(vht,"wait_for_dispatch",lambda:None)
    seen=[]
    def run(work):
        seen.append((str(work),(work/"hook_facts.py").read_text(encoding="utf-8")))
        return 1,{"Target.test_x"},42
    monkeypatch.setattr(vht,"run",run)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(vht.evaluate_source,["A","B","C","D"]))
    assert len({p for p,_ in seen})==4
    assert {s for _,s in seen}=={"A","B","C","D"}
    assert (tmp_path/"hook_facts.py").read_text(encoding="utf-8")=="pristine"
    assert all(r["failed"]==["Target.test_x"] and r["ran"]==42 for r in rows)


def test_dispatch_refuses_stale_monitor(tmp_path,monkeypatch):
    p=tmp_path/"pressure.json";p.write_text(json.dumps({"updated":0,"blocked":False}),encoding="utf-8")
    monkeypatch.setenv("RB_PRESSURE_FILE",str(p))
    with pytest.raises(RuntimeError,match="stale"):vht.wait_for_dispatch()


def test_comparison_refuses_empty_and_tracks_collection_skips():
    empty={"fingerprint":"same","collected":[],"tests":{}}
    with pytest.raises(ValueError):batch.compare(empty,empty)
    a=report();b=report();b["collection_skips"]={"module":"dependency absent"}
    assert batch.compare(a,b)==["collection:module"]


def test_checkpoint_reuse_requires_every_evidence_file(tmp_path):
    from gate_support import evidence_digest, evidence_intact
    log=tmp_path/"log";log.write_text("passed")
    data=tmp_path/"report";data.write_text("details")
    row={"evidence":{"log":evidence_digest(log),"report":evidence_digest(data)}}
    assert evidence_intact(tmp_path,row)
    data.write_text("changed")
    assert not evidence_intact(tmp_path,row)
    assert not evidence_intact(tmp_path,{})


def test_reports_cannot_change_the_fingerprinted_tree(tmp_path):
    from gate_support import external_output
    with pytest.raises(ValueError):external_output(tmp_path/"reports",tmp_path)


def test_skip_ceiling_is_global_not_per_batch():
    tests={name:{"call":{"outcome":"skipped","wasxfail":""}} for name in ("a","b")}
    assert batch.skip_ceiling_errors(tests,{},"1")
    assert not batch.skip_ceiling_errors(tests,{},"2")
    assert batch.skip_ceiling_errors(tests,{"module":"missing"},"2")
    assert batch.skip_ceiling_errors(tests,{},"")


# --- FX-H2 / reviewer M-1: a partial or pending release run is not a pass ------------------
rrg=load("run_release_gates_h2",ROOT/"scripts/run-release-gates.py")


def _fake_release_run(tmp_path,monkeypatch,stage_scripts):
    """Run the REAL main() over fake stages: (name, python source) pairs. A source receives the
    stage's output directory as argv[1]."""
    import sys
    out=tmp_path/"gates-out"
    def stages(o,workers):
        rows=[]
        for name,src in stage_scripts:
            f=tmp_path/(name+".py");f.write_text(src,encoding="utf-8")
            rows.append((name,tmp_path,[sys.executable,str(f),str(o/(name+"-dir"))]))
        return rows
    monkeypatch.setattr(rrg,"stages",stages)
    monkeypatch.setattr(rrg,"identity",lambda workers:{"fixed":workers})
    monkeypatch.setattr(rrg,"wait_dispatch",lambda:None)
    monkeypatch.setenv("RB_PRESSURE_FILE",str(tmp_path/"pressure.json"))
    def run(*extra):
        monkeypatch.setattr("sys.argv",["run-release-gates.py","--out",str(out),*extra])
        return rrg.main()
    return run,out


_PASS="import sys; sys.exit(0)\n"
_FAIL="import sys; sys.exit(1)\n"
_PENDING=("import json, pathlib, sys\n"
          "d=pathlib.Path(sys.argv[1]); d.mkdir(parents=True, exist_ok=True)\n"
          "t={'tests/test_deploy_parity.py::test_the_real_trees':{'call':{'outcome':'failed'}}}\n"
          "(d/'results.json').write_text(json.dumps({'tests':t}), encoding='utf-8')\n"
          "sys.exit(1)\n")


def test_every_stage_passed_is_the_only_exit_0(tmp_path,monkeypatch):
    run,_=_fake_release_run(tmp_path,monkeypatch,[("gen",_PASS),("vht",_PASS),("pytest",_PASS)])
    assert run()==0


def test_a_PENDING_stage_exits_3_and_resume_cannot_launder_it(tmp_path,monkeypatch):
    run,out=_fake_release_run(tmp_path,monkeypatch,[("gen",_PASS),("vht",_PASS),("pytest",_PENDING)])
    assert run()==3
    state=json.loads((out/"checkpoint.json").read_text(encoding="utf-8"))
    assert state["stages"]["pytest"]["status"]=="pending deployment parity"
    assert run("--resume")==3          # every stage reused; the verdict still reads the state


def test_stopping_early_with_through_exits_3_until_the_rest_runs(tmp_path,monkeypatch):
    run,_=_fake_release_run(tmp_path,monkeypatch,[("gen",_PASS),("vht",_PASS),("pytest",_PASS)])
    assert run("--through","vht")==3
    assert run("--resume","--through","vht")==3
    assert run("--resume")==0          # twin: once pytest really passes, the run is whole


def test_TWIN_a_failed_stage_is_still_1_not_3(tmp_path,monkeypatch):
    run,_=_fake_release_run(tmp_path,monkeypatch,[("gen",_FAIL),("vht",_PASS),("pytest",_PASS)])
    assert run()==1


def test_verdict_reads_only_recorded_state():
    names=["a","b"]
    ok={"accepted":True,"status":"passed"}
    assert rrg.verdict({"stages":{"a":ok,"b":ok}},names)==0
    assert rrg.verdict({"stages":{"a":ok}},names)==3                                  # not run
    assert rrg.verdict({"stages":{"a":ok,"b":{"accepted":True,"status":"pending x"}}},names)==3
    assert rrg.verdict({"stages":{"a":{"accepted":False,"status":"failed"},"b":ok}},names)==1
