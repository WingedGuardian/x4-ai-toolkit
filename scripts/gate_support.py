"""Small shared helpers for capped, resumable verification runners."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


#: The X4 roots a report directory must never land in, as `x4validate._paths` names them.
PROTECTED_ROOTS = ("game_root", "reference", "profile")


def protected_roots(pkg):
    """The protected X4 roots as the TOOLS resolve them: environment, then the path config.

    Reviewer M-2: only the exported variables were checked, so a root named only in
    x4-paths.env was not seen (the run-gates.sh log-dir check had the same hole, R4-7/R6-10).
    A resolver that cannot run REFUSES: an output directory that cannot be checked is not
    one that passed. An unconfigured root (None) protects nothing and is skipped.
    """
    pkg = str(pkg)
    if pkg not in sys.path:
        sys.path.insert(0, pkg)
    try:
        from x4validate import _paths
    except Exception as exc:
        raise RuntimeError(f"cannot import x4validate._paths ({exc}); refusing to choose an "
                           "output directory that cannot be checked") from exc
    roots = {}
    for name in PROTECTED_ROOTS:
        try:
            value = getattr(_paths, name)()
        except Exception as exc:
            raise RuntimeError(f"cannot resolve the {name} root ({exc}); refusing to choose an "
                               "output directory that cannot be checked") from exc
        if value is not None:
            roots[name] = Path(value)
    return roots


def external_output(path, root, protected):
    """Reports must not mutate the source being fingerprinted or protected data.

    *protected* is `protected_roots(...)`: required, so no caller can fall back to checking
    the environment alone. The exported variables are still checked as well."""
    path, root = Path(path).resolve(), Path(root).resolve()
    if path == root or root in path.parents:
        raise ValueError("output must be outside the toolkit source tree")
    # The reports are write artifacts; reject known game/reference/profile roots.
    named = [Path(v) for v in dict(protected).values()]
    named += [Path(os.environ[n]) for n in ("X4_GAME", "X4_REFERENCE", "X4_PROFILE") if os.environ.get(n)]
    for value in named:
        guarded = value.resolve()
        if path == guarded or guarded in path.parents:
            raise ValueError("output overlaps a protected X4 root")
    return path


def evidence_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def evidence_intact(out, row):
    return bool(row.get("evidence")) and all(
        (out / name).is_file() and evidence_digest(out / name) == digest
        for name, digest in row["evidence"].items())


def gate_environment():
    """Keep child `bash` lookups on Git Bash, never the Windows WSL stub."""
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1",PYTHONUTF8="1")
    if os.name=="nt":
        from gitbash import find_bash
        bash=find_bash()
        if not bash:raise RuntimeError("Git Bash is required for these gates")
        bindir=Path(bash).parent
        env["PATH"]=os.pathsep.join([str(bindir),str(bindir.parent/"usr"/"bin"),env.get("PATH","")])
    return env


def dispatch_ready():
    p = os.environ.get("RB_PRESSURE_FILE")
    if not p:
        return True
    state = json.loads(Path(p).read_text(encoding="utf-8"))
    if time.time()-state["updated"] > 30:
        raise RuntimeError("resource monitor heartbeat is stale")
    return not state["blocked"]


def wait_dispatch():
    while not dispatch_ready():
        time.sleep(1)


def save(path, data):
    path = Path(path)
    payload = (json.dumps(data, indent=2, sort_keys=True)+"\n").encode("utf-8")
    temp = path.with_suffix(path.suffix+".partial")
    temp.write_bytes(payload)
    temp.replace(path)


def source_fingerprint(root):
    """Hash actual tracked/untracked source bytes; never read ignored path configs."""
    p = subprocess.run(["git","-C",str(root),"ls-files","-z","--cached","--others","--exclude-standard"],
                       capture_output=True,check=True)
    h=hashlib.sha256()
    for name in sorted(set(p.stdout.decode("utf-8").split("\0")) - {""}):
        path=Path(root)/name
        h.update(name.encode("utf-8"))
        h.update(path.read_bytes() if path.is_file() else b"<absent>")
    return h.hexdigest()
