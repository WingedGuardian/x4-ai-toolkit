#!/usr/bin/env python3
"""Run audited module batches in isolated processes. Unknown modules stay exclusive.

--serial runs the original monolithic suite, with the same per-test reporting.
--compare checks node/phase outcomes and skip reasons, not just totals.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile

PACKAGE=Path(__file__).resolve().parents[1]
REPO=PACKAGE.parents[1]
sys.path.insert(0,str(REPO/"scripts"))
from gate_support import wait_dispatch, save, source_fingerprint, external_output, protected_roots, gate_environment


def classify(path):
    """Conservative source audit: process/OS/timing/shared-root users remain exclusive.

    This is a candidate classifier, not proof of hermeticity. Only a manifest row
    with reviewed=true and a matching SHA256 can enable parallel dispatch.
    """
    import re
    text=path.read_text(encoding="utf-8")
    hits=sorted(set(re.findall(r"subprocess|Popen|winreg|perf_counter|monotonic|sleep|Thread|Process|"
                 r"socket|sqlite|mutation_probe|os\.system|REPO|ROOT|require_repo|shutil|"
                 r"write_text|write_bytes|open\(|unlink|rmtree|rename|replace|mkdir",text)))
    return {"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
            "mode":"exclusive","reviewed":False,
            "reason":"Review required: "+", ".join(hits) if hits else "Review required: no static risk markers"}


def normalize_skip(reason):
    """Canonicalize only this runner's random directory nonce under system temp.

    Retain the parent, source location, error code, relative filename and message.
    Repr-escaped Windows exceptions can carry several backslashes per separator.
    """
    import re
    parent=r"[\\/]+".join(re.escape(p) for p in re.split(r"[\\/]",tempfile.gettempdir()))
    return re.sub("("+parent+r"[\\/]+x4-pytest-batch-)[a-z0-9_]{8}(?=[\\/])",
                  r"\1<SCRATCH>",reason)


def outcomes(data):
    return {node:{phase:{k:normalize_skip(v) if k=="skip_reason" else v for k,v in row.items() if k!="duration"}
                  for phase,row in phases.items()} for node,phases in data["tests"].items()}


def skip_ceiling_errors(tests, collection_skips, cap):
    if cap is None:return []
    try:limit=int(cap)
    except ValueError:return ["X4_MAX_SKIPS is not an integer"]
    count=len(collection_skips)+sum(row["outcome"]=="skipped" and not row.get("wasxfail")
        for phases in tests.values() for row in phases.values())
    if limit<0:return ["X4_MAX_SKIPS is negative"]
    return [f"{count} skips exceeds X4_MAX_SKIPS={limit}"] if count>limit else []


def compare(a,b):
    if a["fingerprint"] != b["fingerprint"]:
        raise ValueError("different source fingerprints")
    aa,bb=outcomes(a),outcomes(b)
    for data in (a,b):
        if not data["collected"] or len(data["collected"])!=len(set(data["collected"])) or set(data["collected"])!=set(data["tests"]):
            raise ValueError("incomplete or duplicate test inventory")
    # Pytest skip locations embed per-worker temp paths; retain the actual reason.
    changed=[n for n in sorted(aa.keys()|bb.keys()) if aa.get(n)!=bb.get(n)]
    ac,bc=({n:normalize_skip(reason) for n,reason in d.get("collection_skips",{}).items()} for d in (a,b))
    changed += ["collection:"+n for n in sorted(ac.keys()|bc.keys()) if ac.get(n)!=bc.get(n)]
    if a.get("exit")!=b.get("exit"):changed.append("session:exit")
    if a.get("session_errors",[])!=b.get("session_errors",[]):changed.append("session:errors")
    return changed


def run_batch(batch, out, number):
    wait_dispatch()
    report=out/f"batch-{number}.json"
    env=dict(gate_environment(),X4_BATCH_REPORT=str(report),
             PYTHONPATH=str(PACKAGE/"scripts")+os.pathsep+os.environ.get("PYTHONPATH",""))
    # Guard tests interpret path spellings. A report folder such as .claude/backups
    # is policy-exempt, so it must never become the test sandbox's parent.
    with tempfile.TemporaryDirectory(prefix="x4-pytest-batch-") as scratch:
        command=[sys.executable,"-B","-m","pytest","-q","-rfEs","-p","no:cacheprovider",
                 "-p","batch_report","--basetemp",str(Path(scratch)/"tests"),*batch]
        with (out/f"batch-{number}.log").open("wb") as log:
            p=subprocess.run(command,cwd=PACKAGE,env=env,stdout=log,stderr=subprocess.STDOUT)
    if not report.exists():
        raise RuntimeError(f"batch {number} produced no report (rc {p.returncode})")
    data=json.loads(report.read_text(encoding="utf-8"))
    if p.returncode not in (0,1) or data["collection_errors"]:
        raise RuntimeError(f"batch {number} incomplete: rc {p.returncode}; see its log")
    data["exit"]=p.returncode
    print(f"batch {number}: {len(data['collected'])} tests, rc={p.returncode}, {data['seconds']:.1f}s",flush=True)
    return data


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workers",type=int,choices=range(1,9),default=4)
    p.add_argument("--out",type=Path)
    p.add_argument("--serial",action="store_true")
    p.add_argument("--inventory",action="store_true")
    p.add_argument("--compare",nargs=2,type=Path)
    p.add_argument("modules",nargs="*")
    args=p.parse_args()
    if args.compare:
        changed=compare(*(json.loads(f.read_text(encoding="utf-8")) for f in args.compare))
        print(json.dumps({"changed":changed,"count":len(changed)},indent=2));return bool(changed)
    files=sorted((PACKAGE/"tests").glob("test_*.py"))
    if args.inventory:
        print(json.dumps({f.name:classify(f) for f in files},indent=2));return 0
    if not args.out:
        p.error("--out is required")
    out=external_output(args.out,REPO,protected_roots(PACKAGE))
    if out.exists():
        p.error("--out must be a new directory (preserve earlier evidence)")
    out.mkdir(parents=True)
    if args.workers>1 and not args.serial and not os.environ.get("RB_PRESSURE_FILE"):
        p.error("parallel batches require rb.py run")
    manifest_path=PACKAGE/"scripts"/"pytest-batches.json"
    manifest=json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    helpers=manifest.get("__helpers__",{})
    helpers_match=bool(helpers) and all((PACKAGE/"tests"/name).is_file() and
        hashlib.sha256((PACKAGE/"tests"/name).read_bytes()).hexdigest()==digest
        for name,digest in helpers.items())
    selected=args.modules or ["tests/"+f.name for f in files]
    parallel=[]; exclusive=[]; inventory={}
    for name in selected:
        file=PACKAGE/name
        row=classify(file)
        reviewed=manifest.get(file.name,{})
        if helpers_match and reviewed.get("reviewed") and reviewed.get("sha256")==row["sha256"]:
            row=reviewed
        inventory[name]=row
        if row["mode"]=="functions":
            import ast
            tree=ast.parse(file.read_text(encoding="utf-8"))
            if any(isinstance(n,ast.ClassDef) for n in tree.body):
                raise RuntimeError(f"{name}: function split review does not cover classes")
            functions=[n.name for n in tree.body if isinstance(n,ast.FunctionDef) and n.name.startswith("test_")]
            if not functions:raise RuntimeError(f"{name}: no test functions")
            chunks=min(args.workers*2,len(functions))
            parallel += [[name+"::"+fn for fn in functions[i::chunks]] for i in range(chunks)]
        elif row["mode"]=="parallel":parallel.append([name])
        else:exclusive.append(name)
    save(out/"inventory.json",inventory)
    fingerprint=source_fingerprint(REPO)
    if args.serial:
        results=[run_batch(selected,out,0)]
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results=list(pool.map(lambda pair:run_batch(pair[1],out,pair[0]),enumerate(parallel)))
        # All exclusive modules retain original relative order in ONE process.
        if exclusive:results.append(run_batch(exclusive,out,len(parallel)))
    tests={}; collected=[]; collection_skips={}
    for r in results:
        overlap=tests.keys() & r["tests"].keys()
        if overlap:raise RuntimeError(f"duplicate tests: {sorted(overlap)}")
        tests.update(r["tests"]);collected+=r["collected"]
        collection_skips.update(r["collection_skips"])
    if not collected or len(collected)!=len(set(collected)) or set(collected)!=set(tests):
        raise RuntimeError("missing or duplicate test executions")
    if source_fingerprint(REPO)!=fingerprint:raise RuntimeError("source changed during verification")
    errors=skip_ceiling_errors(tests,collection_skips,os.environ.get("X4_MAX_SKIPS"))
    for r in results:
        if r["exit"] and not any(row["outcome"]=="failed" for phases in r["tests"].values() for row in phases.values()):
            errors.append("a batch returned nonzero without a failed test; inspect its log")
    rc=max([int(bool(errors)),*[r["exit"] for r in results]])
    save(out/"results.json",{"fingerprint":fingerprint,"tests":tests,"collected":sorted(collected),
         "collection_skips":collection_skips,"session_errors":errors,
         "exit":rc,"workers":1 if args.serial else args.workers})
    return rc


if __name__=="__main__":raise SystemExit(main())
