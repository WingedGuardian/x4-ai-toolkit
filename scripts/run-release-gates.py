#!/usr/bin/env python3
"""Step-1 release checks only. Resume completed stages on identical declared inputs.

Exit 0 only when every stage is recorded passed; 1 a stage failed; 3 a stage is pending
(deployment parity) or was not run (`--through vht`), however many times it is resumed.

Run using the toolkit Python through rb.py. Never deploys, pushes or publishes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from gate_support import save, source_fingerprint, wait_dispatch, external_output, evidence_digest, evidence_intact, gate_environment

ROOT=Path(__file__).resolve().parents[1]
PKG=ROOT/"tools"/"x4validate"
sys.path.insert(0,str(PKG))


def identity(workers):
    from x4validate import _freshness, _merge, _registry, _paths
    fp=_freshness.fingerprint(_merge.Config(),extensions=_registry.default_installed_dirs())
    if fp.get("content") is None or any(row.get("unreadable",0) for row in fp["detail"]):
        raise RuntimeError("installed inputs could not be fingerprinted")
    private=hashlib.sha256()
    # Hash private config and relevant environment without persisting their values.
    for name in sorted(k for k in os.environ if k.startswith("X4_") or k in ("PATH","RB_HOME")):
        private.update(name.encode()+b"="+os.environ[name].encode())
    cfg=_paths._find_env_file()
    if cfg: private.update(cfg.read_bytes())
    policy=Path.home()/".claude"/"tools"/"resource-budget"
    h=hashlib.sha256()
    for p in sorted([policy/"rb.py",*policy.glob("rblib/*.py"),*policy.glob("config.json")]):
        h.update(p.name.encode()+p.read_bytes())
    deployed=hashlib.sha256()
    game=_paths.game_root()
    if game:
        for p in sorted((game/".claude").glob("hooks/*")):
            if p.is_file():deployed.update(p.name.encode()+p.read_bytes())
    return {"source":source_fingerprint(ROOT),"python":sys.version,"workers":workers,
            "installed":fp,"private_digest":private.hexdigest(),"policy":h.hexdigest(),
            "deployed_hooks":deployed.hexdigest(),
            "sha":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()}


def stages(out,workers):
    py=[sys.executable,"-B","-u"]
    bash="C:/Program Files/Git/bin/bash.exe" if os.name=="nt" else "bash"
    rows=[(n,PKG,py+["scripts/"+f,"--check"]) for n,f in
          (("gen","gen-agent-trees.py"),("clir","gen-cli-reference.py"),("hashes","gen-shipped-hashes.py"))]
    rows += [("scan",ROOT,py+["scripts/scan-identifiers.py"]),
             ("pb",ROOT,[bash,".claude/hooks/test-protect-bash.sh"]),
             ("hookfacts",ROOT,py+[".claude/hooks/test_hook_facts.py"]),
             ("audit0924",ROOT,py+[".claude/hooks/test_audit0924_hooks.py"]),
             ("fuzz",ROOT,py+["scripts/fuzz-guard.py"]),
             ("testhooks",ROOT,[bash,"scripts/test-hooks.sh"]),
             ("rereg",PKG,py+["gates/register_rederivation.py"]),
             ("vht",ROOT,py+["scripts/verify-hook-tests.py","--workers",str(workers),"--report",str(out/"vht.json")]),
             ("pytest",PKG,py+["scripts/run-pytest-batches.py","--workers",str(workers),"--out",str(out/("pytest-"+str(time.time_ns())))])]
    return rows


def pytest_pending_only(path):
    data=json.loads(path.read_text(encoding="utf-8"))
    failed={n for n,phases in data["tests"].items() if any(p["outcome"]=="failed" for p in phases.values())}
    return not data.get("session_errors") and failed=={"tests/test_deploy_parity.py::test_the_real_trees"}


def verdict(state,names):
    """The run's exit code, read ONLY from the recorded checkpoint state (reviewer M-1).

    0 every stage is recorded `passed`; 1 a stage is recorded as failed; 3 anything else -- a
    stage still pending (deployment parity) or never run (`--through` stopped early). Reading
    the STATE rather than this invocation's own path is what stops `--resume` laundering a
    pending stage into a pass: a reused row is judged exactly like a fresh one."""
    rows=[state["stages"].get(n) for n in names]
    if any(r is not None and not r["accepted"] for r in rows):return 1
    if all(r is not None and r["status"]=="passed" for r in rows):return 0
    return 3


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--workers",type=int,choices=range(1,9),default=4)
    p.add_argument("--resume",action="store_true")
    p.add_argument("--through",choices=("vht","pytest"),default="pytest",
                   help="stop at a stage boundary; vht leaves pytest explicitly pending")
    args=p.parse_args()
    if not os.environ.get("RB_PRESSURE_FILE"):p.error("run through rb.py")
    out=external_output(args.out,ROOT);out.mkdir(parents=True,exist_ok=True)
    checkpoint=out/"checkpoint.json"
    ident=identity(args.workers)
    state={"identity":ident,"stages":{}}
    if args.resume and checkpoint.exists():
        state=json.loads(checkpoint.read_text(encoding="utf-8"))
        if state["identity"]!=ident:p.error("checkpoint inputs changed; use a new output directory")
    elif checkpoint.exists():p.error("checkpoint already exists; use --resume or a new output directory")
    rows=stages(out,args.workers)
    for name,cwd,cmd in rows:
        if name=="pytest" and args.through=="vht":
            print("PARTIAL step 1: hook verification complete; pytest not run. Resume without --through vht.")
            break
        prior=state["stages"].get(name)
        if prior and prior["accepted"]:
            if evidence_intact(out,prior):
                print(f"{name}: reused completed {prior['status']}",flush=True);continue
            p.error(f"{name}: prior evidence missing or changed")
        wait_dispatch()
        print(f"{name}: starting",flush=True)
        started=time.monotonic()
        log=out/(name+"-"+str(time.time_ns())+".log")
        with log.open("wb") as stream:
            rc=subprocess.run(cmd,cwd=cwd,env=gate_environment(),
                              stdout=stream,stderr=subprocess.STDOUT).returncode
        accepted=rc==0; status="passed" if accepted else "failed"
        if name=="pytest" and rc==1:
            report=Path(cmd[-1])/"results.json"
            if report.exists() and pytest_pending_only(report):accepted=True;status="pending deployment parity"
        if identity(args.workers)!=ident:raise RuntimeError("inputs changed during stage; result not accepted")
        evidence=[log]
        if name=="vht":evidence.append(out/"vht.json")
        if name=="pytest":
            evidence += list(Path(cmd[-1]).glob("*.json")) + list(Path(cmd[-1]).glob("*.log"))
        state["stages"][name]={"rc":rc,"accepted":accepted,"status":status,
                "seconds":time.monotonic()-started,"log":log.name,
                "evidence":{str(f.relative_to(out)):evidence_digest(f) for f in evidence if f.is_file()}}
        save(checkpoint,state)
        print(f"{name}: {status}, rc={rc}, {state['stages'][name]['seconds']:.1f}s",flush=True)
        if not accepted:return 1
    rc=verdict(state,[n for n,_,_ in rows])
    if rc==0:print("STEP 1 complete: every stage passed. No later release steps ran.")
    else:print("STEP 1 NOT complete (exit %d): pending or not run: %s. No later release steps ran."
               %(rc,", ".join(n for n,_,_ in rows if (state["stages"].get(n) or {}).get("status")!="passed")))
    return rc


if __name__=="__main__":raise SystemExit(main())
