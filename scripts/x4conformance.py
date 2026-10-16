#!/usr/bin/env python3
"""x4conformance -- replay the toolkit's guard corpus through ANY agent's adapter and compare,
case by case, with what the guards themselves decide. Run it as `x4guard conformance`:

    python .claude/hooks/x4guard.py conformance --profile NAME|PATH [--cases FILE.jsonl]
        [--min-cases N] [--workers N] [--no-extras] [-- ADAPTER ARGV...]

The corpus is every `decide` probe in scripts/test-hooks.sh (dumped live with X4_DECIDE_DUMP,
in a kept sandbox under <toolkit>/.test-sandbox) plus scripts/conformance-extra-cases.json:
neutral cases the dump cannot express (PowerShell, a path with spaces, a Git-Bash drive path),
each carrying an `expect` that is checked against the guards first, as a CONTROL.

The reference verdict is the Claude hook itself, run NOW in the same sandbox and env; a deny or
ask it gives is re-run under X4_GUARD_CHECK=1, and an exit 2 there means "checked nothing" ->
"inert". The adapter verdict is what the profile's output rules decode from the adapter's
stdout. They are compared PER ITEM; a total is never the verdict.

Exit codes:
  0  every replayed case agrees, and the cases the guards CHECKED (reference verdict not
     inert) number >= --min-cases (default 80)
  1  at least one case disagrees, or the adapter's output was unreadable (each one is listed)
  2  cannot evaluate: bad or incomplete profile, no adapter command, toolkit / .claude/hooks /
     a required program missing, the dump harness failed, a reference verdict unreadable or
     ABSENT (the reference hook could not start, timed out, or exited non-zero), a
     reference CONTROL (an extra case's `expect`) failed, or the extra cases were wanted (no
     --no-extras) and could not be built
  3  nothing, or too little, examined: replayed == 0, or fewer than --min-cases replayed cases
     were CHECKED by the guards -- an inert guard agreeing with an inert adapter examined
     nothing. Never 0 on an empty population.

Profile format v1 (scripts/conformance-profiles/<name>.json; ADAPTING.md explains each field):
  v: 1;  agent: name;  command: [argv...] with {TOOLKIT} {HOOKS} {PYTHON} {BASH} {PWSH} {HOOK};
  requires: [programs];  run_cwd: "neutral" (an empty scratch dir; default) | "case" (the row's
  cwd);  timeout_s;  env: {VAR: value};
  cases: {KIND: {payload: FILE relative to the profile | "row", set: {json-pointer: template},
          env: {VAR: value}}} for KIND in shell-bash, shell-powershell, edit, write;
  unsupported: {KIND: why} -- a GAP printed on every run; cases + unsupported cover all four kinds;
  output: {format: "json"|"text", exit_codes: {"0": "decode", "2": "inert", ...}, empty: decision,
           rules: [...], strip_prefix, single_line, allowed_keys, inert_pattern}.
Template tokens: {{COMMAND}} {{PATH}} {{CONTENT}} {{CWD}} and {{NAME|lines:P}} (each line of the
value prefixed with P and ended with a newline). One pass over the template: a {{ inside a value
is never expanded. An unknown token is a profile error (exit 2).

Stdlib only; Python >= 3.10.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gitbash import find_bash  # noqa: E402  THE bash resolver (scripts/gitbash.py -> x4guard)

KINDS = ("shell-bash", "shell-powershell", "edit", "write")
RC_OK, RC_DISAGREE, RC_ERROR, RC_NOTHING = 0, 1, 2, 3
DECISIONS = ("allow", "advise", "ask", "deny", "inert")
TOKENS = ("COMMAND", "PATH", "CONTENT", "CWD")
NOT_CHECKED = re.compile(r"X4 GUARD INERT|NO rule (?:below )?was evaluated|NEVER checked against any rule"
                         r"|could not be translated|could not be analysed"
                         r"|X4 save dispatcher (?:failed:|did not return a complete decision;)")
_TOKEN = re.compile(r"\{\{([A-Za-z_]+)(?:\|([^{}]*))?\}\}")
_MISSING = object()


class ProfileError(ValueError):
    """The profile cannot be used: missing, malformed, incomplete, or an unknown template token."""


# ------------------------------------------------------------------ profile ---------------- #

def load_profile(spec: str, root: Path) -> dict:
    """NAME -> <root>/scripts/conformance-profiles/NAME.json; anything with a separator or a
    .json suffix is a path. Refuses a profile that leaves any of the four KINDS unaccounted for."""
    looks_like_path = any(c in spec for c in "/\\") or spec.endswith(".json")
    path = Path(spec) if looks_like_path else Path(root) / "scripts" / "conformance-profiles" / f"{spec}.json"
    if not path.is_file():
        raise ProfileError(f"profile not found: {path}")
    try:
        prof = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ProfileError(f"profile {path} is not JSON: {e}") from e
    if not isinstance(prof, dict) or prof.get("v") != 1:
        raise ProfileError(f"profile {path}: \"v\" must be 1")
    cases = prof.get("cases") or {}
    unsupported = prof.setdefault("unsupported", {})
    if not isinstance(cases, dict) or not isinstance(unsupported, dict):
        raise ProfileError(f"profile {path}: cases and unsupported must be objects")
    for k in list(cases) + list(unsupported):
        if k not in KINDS:
            raise ProfileError(f"profile {path}: unknown kind {k!r} (kinds: {', '.join(KINDS)})")
    both = sorted(set(cases) & set(unsupported))
    if both:
        raise ProfileError(f"profile {path}: kind(s) both mapped and unsupported: {', '.join(both)}")
    missing = [k for k in KINDS if k not in cases and k not in unsupported]
    if missing:
        raise ProfileError(f"profile {path}: kind(s) neither in cases nor in unsupported: {', '.join(missing)}"
                           " -- map each one, or declare it unsupported (it is then a GAP on every run)")
    for k, c in cases.items():
        if not isinstance(c, dict) or "payload" not in c:
            raise ProfileError(f"profile {path}: cases.{k} needs a \"payload\"")
    out = prof.get("output")
    if not isinstance(out, dict) or out.get("format") not in ("json", "text") or not isinstance(out.get("rules"), list):
        raise ProfileError(f"profile {path}: output needs format \"json\"|\"text\" and a rules list")
    if not isinstance(out.get("exit_codes", {"0": "decode"}), dict):
        raise ProfileError(f"profile {path}: output.exit_codes must be an object")
    prof["_dir"] = str(path.parent)
    prof["_path"] = str(path)
    return prof


def fill(template, values: dict):
    """Fill {{TOKEN}} / {{TOKEN|lines:P}} in a string (or recursively in a list/dict) in ONE pass."""
    if isinstance(template, dict):
        return {k: fill(v, values) for k, v in template.items()}
    if isinstance(template, list):
        return [fill(v, values) for v in template]
    if not isinstance(template, str):
        return template

    def sub(m):
        name, flt = m.group(1), m.group(2)
        if name not in TOKENS:
            raise ProfileError(f"unknown template token {{{{{name}}}}} (known: {', '.join(TOKENS)})")
        if name not in values or values[name] is None:
            raise ProfileError(f"template token {{{{{name}}}}} has no value for this case")
        v = str(values[name])
        if flt is None:
            return v
        if flt.startswith("lines:"):
            prefix = flt[len("lines:"):]
            return "".join(prefix + ln + "\n" for ln in (v.split("\n") if v else [""]))
        raise ProfileError(f"unknown template filter |{flt} on {{{{{name}}}}}")

    return _TOKEN.sub(sub, template)


def _pointer_parts(ptr: str) -> list:
    if ptr == "":
        return []
    if not ptr.startswith("/"):
        raise ProfileError(f"not a JSON pointer: {ptr!r}")
    return [p.replace("~1", "/").replace("~0", "~") for p in ptr[1:].split("/")]


def get_pointer(doc, ptr: str):
    cur = doc
    for part in _pointer_parts(ptr):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


def set_pointer(doc: dict, ptr: str, value) -> None:
    parts = _pointer_parts(ptr)
    if not parts:
        raise ProfileError("cannot set the whole payload through a pointer")
    cur = doc
    for part in parts[:-1]:
        nxt = cur.get(part) if isinstance(cur, dict) else None
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


# ------------------------------------------------------------------ decode ----------------- #

def _finish(decision: str, text, output: dict):
    if decision in ("deny", "ask") and text and re.search(output.get("inert_pattern", "X4 GUARD INERT"), text):
        return "inert", text
    return decision, text


def decode(output: dict, stdout: bytes, rc: int) -> tuple:
    """(decision, text) from what an adapter printed. A decision is one of allow|advise|ask|deny|inert,
    or "unreadable": an exit status the profile does not list, output no rule matches, a key the
    agent does not know (`allowed_keys`). "unreadable" is a disagreement, never an allow."""
    action = (output.get("exit_codes") or {"0": "decode"}).get(str(rc))
    if action is None:
        return "unreadable", f"exit status {rc} is not in the profile's exit_codes"
    body = stdout.decode("utf-8", "replace") if isinstance(stdout, (bytes, bytearray)) else str(stdout)
    body = body.strip()
    if action != "decode":
        if action not in DECISIONS:
            return "unreadable", f"exit_codes maps {rc} to unknown decision {action!r}"
        return action, body or None
    sp = output.get("strip_prefix")
    if sp:
        if not body.startswith(sp):
            return "unreadable", f"output does not start with {sp!r}: {body[:200]!r}"
        body = body[len(sp):].strip()
    if not body:
        empty = output.get("empty")
        return (empty, None) if empty in DECISIONS else ("unreadable", "empty output and no \"empty\" decision declared")
    if output.get("single_line") and "\n" in body:
        return "unreadable", f"more than one line: {body[:200]!r}"
    if output.get("format") == "text":
        for rule in output.get("rules", []):
            m = re.search(rule["regex"], body, re.M)
            if m:
                text = m.groupdict().get("text")
                return _finish(rule["decision"], text, output)
        return "unreadable", f"no output rule matched: {body[:200]!r}"
    try:
        doc = json.loads(body)
    except ValueError:
        return "unreadable", f"not JSON: {body[:200]!r}"
    for ptr, allowed in (output.get("allowed_keys") or {}).items():
        node = get_pointer(doc, ptr)
        if node is _MISSING:
            continue
        if not isinstance(node, dict):
            return "unreadable", f"{ptr or '/'} is not an object"
        extra = sorted(set(node) - set(allowed))
        if extra:
            return "unreadable", f"key(s) the agent does not know at {ptr or '/'}: {extra}"
    for rule in output.get("rules", []):
        when, ok = rule.get("when") or {}, True
        for ptr, want in when.items():
            got = get_pointer(doc, ptr)
            if isinstance(want, dict) and want.get("present"):
                ok = got is not _MISSING
            else:
                ok = got is not _MISSING and got == want
            if not ok:
                break
        if ok and any(get_pointer(doc, p) is not _MISSING for p in rule.get("absent", [])):
            ok = False
        if not ok:
            continue
        text = get_pointer(doc, rule["text"]) if rule.get("text") else None
        text = None if text is _MISSING else text
        decision = rule["decision"]
        for prefix, d in (rule.get("prefix_decisions") or {}).items():
            if isinstance(text, str) and text.startswith(prefix):
                decision = d
                break
        return _finish(decision, text if isinstance(text, str) or text is None else json.dumps(text), output)
    return "unreadable", f"no output rule matched: {body[:200]!r}"


# ------------------------------------------------------------------ classify / summarise --- #

#: The kind of an Edit/Write row whose path is spelled with BACKSLASHES, off Windows. On POSIX a
#: backslash is a filename character: `\home\u\tk\reference\x` is ONE relative name, so a write
#: lands in the cwd and never in reference/ -- the adapter's allow is the truth there, while the
#: hooks fold backslashes on every OS and deny. MEASURED, CI run 37172347642 (ubuntu): the only
#: disagreement of both the Codex and the toy adapter. Counted in its own bucket, never dropped.
WINDOWS_PATH_DIALECT = "windows_path_dialect"


def classify(row: dict, windows: bool | None = None) -> str:
    """The native kind a Claude-shaped dump row maps to, or "no_native_analogue", or (off Windows)
    WINDOWS_PATH_DIALECT. `windows` defaults to this OS; tests pass it to judge both."""
    windows = (os.name == "nt") if windows is None else windows
    p = row.get("payload") or {}
    tool = p.get("tool_name")
    ti = p.get("tool_input") if isinstance(p.get("tool_input"), dict) else None
    if ti is None:
        return "no_native_analogue"
    if tool in ("Bash", "PowerShell") and isinstance(ti.get("command"), str) and ti["command"]:
        return "shell-powershell" if tool == "PowerShell" else "shell-bash"
    if tool in ("Edit", "Write") and isinstance(ti.get("file_path"), str) and ti["file_path"]:
        if not windows and "\\" in ti["file_path"]:
            return WINDOWS_PATH_DIALECT
        return tool.lower()
    return "no_native_analogue"


def bucket_counts(rows: list, n_replayed: int, profile: dict) -> dict:
    """Every row in exactly one bucket: replayed, WINDOWS_PATH_DIALECT, or no_native_analogue
    (no shell command or file path, or a kind the profile declares unsupported)."""
    b = {"replayed": n_replayed}
    rest = [r for r in rows if (r.get("kind") or classify(r)) not in profile["cases"]]
    n_dialect = sum((r.get("kind") or classify(r)) == WINDOWS_PATH_DIALECT for r in rest)
    if n_dialect:
        b[WINDOWS_PATH_DIALECT] = n_dialect
    if len(rest) - n_dialect:
        b["no_native_analogue"] = len(rest) - n_dialect
    return b


def neutral_run_dir(root: Path) -> Path:
    """The directory an adapter runs in when the profile says `run_cwd: neutral`: one the guards
    have no opinion about. NOT the system temp: on POSIX that is /tmp, which protect-bash denies
    writes into (shared-/tmp rule), so every relative case was judged by THAT rule and the
    ignores_workdir mutant survived on ubuntu (MEASURED, CI run 37172347642). Under
    <root>/.test-sandbox, where the corpus dump already lives (gitignored)."""
    base = Path(root) / ".test-sandbox"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="x4conf-run-", dir=str(base)))


def summarise(results, n_total, buckets, gaps, min_cases) -> tuple:
    """(exit code, report). Buckets must account for every case; disagreements are listed PER ITEM."""
    assert sum(buckets.values()) == n_total, f"buckets {buckets} do not sum to the {n_total} cases"
    assert buckets.get("replayed", 0) == len(results), f"replayed bucket {buckets.get('replayed', 0)} != {len(results)} results"
    lines = [f"conformance: {len(results)} replayed of {n_total} cases; buckets: "
             + ", ".join(f"{k}={v}" for k, v in sorted(buckets.items()))]
    for kind, why in sorted(gaps.items()):
        lines.append(f"GAP: {kind} is not covered by this adapter ({why}); its cases were not replayed")
    # A reference that gave NO verdict (FX-G3): that case was not checked, so it can neither agree
    # nor disagree, and the run cannot pass -- whatever the adapter said about it.
    broken = [r for r in results if r["reference"] in ("error", "unreadable")]
    if broken:
        for r in broken:
            lines.append(f"REFERENCE ERROR {r['label']} [{r['kind']}]: the guards gave no verdict -- "
                         f"{r.get('reference_why') or r['reference']}"[:600])
        lines.append(f"CANNOT EVALUATE: {len(broken)} of {len(results)} replayed case(s) have no reference verdict")
        return RC_ERROR, "\n".join(lines)
    bad = [r for r in results if r["reference"] != r["adapter"]]
    if bad:
        for r in bad:
            extra = f" -- {r['text']}" if r.get("text") else ""
            lines.append(f"DISAGREE {r['label']} [{r['kind']}]: guards say {r['reference']}, "
                         f"adapter says {r['adapter']}{extra}"[:600])
        lines.append(f"FAIL: {len(bad)} of {len(results)} replayed cases disagree")
        return RC_DISAGREE, "\n".join(lines)
    if not results:
        lines.append("REFUSING: 0 replayed -- nothing was examined, so nothing is proven")
        return RC_NOTHING, "\n".join(lines)
    # INERT AGREEING WITH INERT EXAMINED NOTHING (R2-F3, v4.0.0 review): a broken X4_PYTHON made
    # every guard and the adapter answer "checked nothing", and 3 of 3 "agreed". Only a case the
    # guards actually CHECKED counts toward the floor.
    checked = sum(r["reference"] != "inert" for r in results)
    if checked < len(results):
        lines.append(f"{len(results) - checked} replayed case(s) were INERT in the guards (checked "
                     "nothing): they agree, and prove nothing")
    if checked == 0 or checked < min_cases:
        lines.append(f"REFUSING: {checked} of {len(results)} replayed case(s) were checked by the guards, "
                     f"below --min-cases {max(min_cases, 1)}: too little examined")
        return RC_NOTHING, "\n".join(lines)
    lines.append(f"OK: all {len(results)} replayed cases agree ({checked} checked by the guards)")
    return RC_OK, "\n".join(lines)


# ------------------------------------------------------------------ harness ---------------- #

def _native_path(p: str) -> str:
    """A Git Bash path (/c/Users/...) as the OS spells it; anything else unchanged. The dump
    records `$(pwd)`, which is the MSYS form on Windows."""
    if os.name == "nt":
        m = re.match(r"^/([A-Za-z])(/.*|)$", p or "")
        if m:
            return f"{m.group(1).upper()}:{m.group(2) or '/'}"
    return p


def _bash():
    """Git Bash, never the WSL stub (it cannot run a Windows-path script). ONE RESOLVER: this
    asks scripts/gitbash.py, which asks the guards' own x4guard.resolve_bash(). A second resolver
    lived here (X4_BASH > which(bash.exe) > which(bash), stub test by substring) and could pick a
    different bash than the guards it replays (FX-G3, v4.0.0 delta review)."""
    return find_bash()


def _pwsh():
    return shutil.which("pwsh") or shutil.which("powershell")


def _have(program: str) -> bool:
    if program == "bash":
        return bool(_bash())
    if program in ("pwsh", "powershell"):
        return bool(_pwsh())
    return bool(shutil.which(program))


def _env(row: dict) -> dict:
    """The process env minus every X4_* (secrets named *KEY* kept), plus the row's own env: each
    case runs under exactly the roots the harness judged it with."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("X4_") or "KEY" in k}
    env.update(row.get("env") or {})
    return env


def _row_cwd(row: dict, root: Path) -> str:
    cwd = _native_path(row.get("cwd") or "")
    return cwd if cwd and os.path.isdir(cwd) else str(root)


def dump_cases(root: Path, base: Path | None = None) -> tuple:
    """Run scripts/test-hooks.sh with X4_DECIDE_DUMP and return (rows, kept sandbox). The sandbox
    base is <root>/.test-sandbox/conf-<pid> (the harness refuses /tmp). Raises RuntimeError when
    the harness fails or dumps too few rows -- a corpus that could not be built is not a pass."""
    root = Path(root)
    bash = _bash()
    if not bash or not shutil.which("jq"):
        raise RuntimeError("the guard corpus needs Git Bash and jq")
    base = Path(base) if base else root / ".test-sandbox"
    sbx_base = base / f"conf-{os.getpid()}"
    sbx_base.mkdir(parents=True, exist_ok=True)
    dump = sbx_base / "cases.jsonl"
    marker = Path(str(dump) + ".sandbox")
    for f in (dump, marker):
        if f.exists():
            f.unlink()
    # FORWARD slashes: the harness interpolates sandbox paths unquoted into probe commands, and a
    # backslash base made 2 of its probes fail for that reason alone (MEASURED 2026-10-02).
    env = dict(os.environ, X4_DECIDE_DUMP=dump.as_posix(), X4_TEST_SANDBOX=sbx_base.as_posix())
    r = subprocess.run([bash, str(root / "scripts" / "test-hooks.sh")], cwd=str(root), env=env,
                       capture_output=True, timeout=1500)
    sbx = None
    if marker.is_file():
        sbx = Path(_native_path(marker.read_text(encoding="utf-8").strip()))
    try:
        if r.returncode != 0:
            raise RuntimeError(f"scripts/test-hooks.sh exited {r.returncode}; the corpus is not trustworthy:\n"
                               + r.stdout.decode("utf-8", "replace")[-3000:])
        if sbx is None or not sbx.is_dir():
            raise RuntimeError(f"the harness did not record its kept sandbox in {marker}")
        rows = [json.loads(ln) for ln in dump.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if len(rows) < 100:
            raise RuntimeError(f"only {len(rows)} cases dumped -- the harness or the dump hook changed")
    except BaseException:
        if sbx is not None and sbx.is_dir():
            remove_sandbox(sbx, base)
        raise
    finally:
        for f in (dump, marker):
            if f.exists():
                f.unlink()
    for n, row in enumerate(rows, 1):
        row["kind"] = classify(row)
        row["n"] = n            # labels repeat across the harness's layouts; the number does not
    return rows, sbx


def remove_sandbox(path: Path, base: Path) -> None:
    """Delete ONE kept sandbox, then its now-empty parents up to and including `base`.
    Refuses (ValueError, nothing deleted) unless `base` is a parent of `path`."""
    path, base = Path(path).resolve(), Path(base).resolve()
    if base not in path.parents:
        raise ValueError(f"refusing to remove {path}: it is not under {base}")
    shutil.rmtree(path)
    d = path.parent
    while d == base or base in d.parents:
        try:
            d.rmdir()
        except OSError:
            break
        d = d.parent


def _hook_decision(out: str) -> str:
    out = out.strip()
    if not out:
        return "allow"
    try:
        hso = json.loads(out)["hookSpecificOutput"]
    except (ValueError, KeyError, TypeError):
        return "unreadable"
    if not isinstance(hso, dict):
        return "unreadable"
    d = hso.get("permissionDecision")
    if d in ("deny", "ask"):
        return "inert" if NOT_CHECKED.search(hso.get("permissionDecisionReason") or "") else d
    return "advise" if hso.get("additionalContext") else "allow"


REFERENCE_TIMEOUT_S = 120


def _run_reference(bash, hook: Path, data: bytes, env: dict, cwd: str):
    """(CompletedProcess, None) or (None, why): a reference hook that cannot start, or does not
    answer in time, gave NO verdict -- it must never decode as the empty-stdout "allow"."""
    if not bash:
        return None, "no bash to run the reference hook with"
    try:
        return subprocess.run([bash, str(hook)], input=data, capture_output=True, env=env, cwd=cwd,
                              timeout=REFERENCE_TIMEOUT_S), None
    except subprocess.TimeoutExpired:
        return None, f"the reference hook did not answer within {REFERENCE_TIMEOUT_S}s"
    except OSError as e:
        return None, f"the reference hook could not start: {e}"


def reference_verdict_detail(row: dict, root: Path) -> tuple:
    """(verdict, why). What the guards decide: the row's Claude hook on its Claude-shaped payload,
    now, in the row's env and cwd. A deny/ask is re-run under X4_GUARD_CHECK=1 -- the guards' own
    "checked nothing" protocol, where such paths exit 2 -- so "inert" comes from the guards, not
    from a phrase list that can drift.

    "error" (FX-G3, v4.0.0 delta review): the hook could not start, timed out, or EXITED NON-ZERO.
    A Claude hook allows by exiting 0 with empty stdout; a missing hook (bash exit 127) or a crash
    (exit 1, no output) also has empty stdout, and was read as a CHECKED allow. Claude Code does
    not act on the stdout of a non-zero exit either, so no non-zero exit is a reference verdict."""
    bash = _bash()
    hook = Path(root) / ".claude" / "hooks" / row["hook"]
    data = json.dumps(row["payload"]).encode("utf-8")
    env, cwd = _env(row), _row_cwd(row, Path(root))
    r, why = _run_reference(bash, hook, data, env, cwd)
    if r is None:
        return "error", why
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip()[-300:]
        return "error", f"the reference hook exited {r.returncode} (no verdict)" + (f"; stderr: {err}" if err else "")
    d = _hook_decision(r.stdout.decode("utf-8", "replace"))
    if d in ("ask", "deny"):
        c, why = _run_reference(bash, hook, data, dict(env, X4_GUARD_CHECK="1"), cwd)
        if c is None:
            return "error", f"the X4_GUARD_CHECK re-run: {why}"
        if c.returncode == 2:
            return "inert", ""
        if c.returncode != 0:
            # FX-G4 / reviewer G-M4: any OTHER non-zero exit of the re-run is no verdict either
            # (as for the first run) -- it fell back to the first run's verdict and counted as
            # checked.
            err = c.stderr.decode("utf-8", "replace").strip()[-300:]
            return "error", (f"the X4_GUARD_CHECK re-run exited {c.returncode} (no verdict)"
                             + (f"; stderr: {err}" if err else ""))
    return d, ""


def reference_verdict(row: dict, root: Path) -> str:
    """The verdict alone; see reference_verdict_detail."""
    return reference_verdict_detail(row, root)[0]


def _placeholders(root: Path, row) -> dict:
    root = Path(root)
    return {"TOOLKIT": root.as_posix(), "HOOKS": (root / ".claude" / "hooks").as_posix(),
            "PYTHON": sys.executable, "BASH": _bash(), "PWSH": _pwsh(),
            "HOOK": (row or {}).get("hook")}


def _expand(s: str, ph: dict) -> str:
    def sub(m):
        v = ph.get(m.group(1))
        if v is None:
            raise ProfileError(f"placeholder {{{m.group(1)}}} has no value here (program missing, or not a hook row)")
        return v
    return re.sub(r"(?<!\{)\{(TOOLKIT|HOOKS|PYTHON|BASH|PWSH|HOOK)\}(?!\})", sub, s)


def adapter_command(profile: dict, argv, root: Path, row=None) -> list:
    cmd = list(argv) if argv else profile.get("command")
    if not cmd:
        raise ProfileError("no adapter command: the profile has no \"command\" and none was given after --")
    ph = _placeholders(root, row)
    return [_expand(str(a), ph) for a in cmd]


def _case_values(row: dict, cwd: str) -> dict:
    ti = row["payload"].get("tool_input") or {}
    return {"COMMAND": ti.get("command"), "PATH": ti.get("file_path"),
            "CONTENT": str(ti.get("content") or ""), "CWD": cwd}


def build_payload(row: dict, profile: dict, cwd: str):
    """The NATIVE payload for a row: the profile's template file with each `set` pointer filled
    after parsing (no escaping hazard), or the row's own payload for "row". A row carrying
    `native` (an agent-specific case built by its own test) is sent as it is."""
    if "native" in row:
        return copy.deepcopy(row["native"])
    case = profile["cases"][row.get("kind") or classify(row)]
    if case["payload"] == "row":
        payload = copy.deepcopy(row["payload"])
    else:
        f = Path(profile["_dir"]) / case["payload"]
        if not f.is_file():
            raise ProfileError(f"payload template not found: {f}")
        payload = json.loads(f.read_text(encoding="utf-8"))
    values = _case_values(row, cwd)
    for ptr, tmpl in (case.get("set") or {}).items():
        set_pointer(payload, ptr, fill(tmpl, values))
    return payload


def adapter_verdict(row, profile, argv, root, run_dir) -> tuple:
    """(decision, text) the adapter gives for one row, decoded by the profile's output rules."""
    root = Path(root)
    case = profile["cases"][row.get("kind") or classify(row)]
    cwd = _row_cwd(row, root)
    payload = build_payload(row, profile, cwd)
    ph = _placeholders(root, row)
    env = _env(row)
    for k, v in {**(profile.get("env") or {}), **(case.get("env") or {})}.items():
        env[k] = _expand(str(v), ph)
    cmd = adapter_command(profile, argv, root, row)
    run_cwd = cwd if profile.get("run_cwd", "neutral") == "case" else str(run_dir)
    try:
        r = subprocess.run(cmd, input=json.dumps(payload).encode("utf-8"), capture_output=True,
                           env=env, cwd=run_cwd, timeout=float(profile.get("timeout_s", 120)))
    except subprocess.TimeoutExpired:
        return "unreadable", f"the adapter did not answer within {profile.get('timeout_s', 120)}s"
    except OSError as e:
        return "unreadable", f"the adapter could not start: {e}"
    d, text = decode(profile["output"], r.stdout, r.returncode)
    if d == "unreadable" and r.stderr:
        text = f"{text}; stderr: {r.stderr.decode('utf-8', 'replace').strip()[-300:]}"
    return d, text


def replay(rows, profile, argv, root, workers=4) -> list:
    """Judge every row whose kind the profile maps: reference and adapter, side by side, per item."""
    root = Path(root)
    todo = [r for r in rows if (r.get("kind") or classify(r)) in profile["cases"]]
    run_dir = neutral_run_dir(root)

    def one(row):
        ref, ref_why = reference_verdict_detail(row, root)
        got, text = adapter_verdict(row, profile, argv, root, run_dir)
        label = row.get("label") or row.get("id")
        return {"label": f"#{row['n']} {label}" if row.get("n") else label, "kind": row.get("kind") or classify(row),
                "hook": row.get("hook"), "reference": ref, "reference_why": ref_why, "adapter": got, "text": text}
    try:
        with ThreadPoolExecutor(max(1, int(workers))) as ex:
            return list(ex.map(one, todo))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def _extra_fill(s: str, tk: Path, tmp: Path) -> str:
    tkp = tk.as_posix()
    msys = "/" + tkp[0].lower() + tkp[2:] if re.match(r"^[A-Za-z]:", tkp) else tkp
    return (s.replace("{TKMSYS}", msys).replace("{TKP}", tkp).replace("{TK}", str(tk))
             .replace("{TMP}", str(tmp)))


def _fill_obj(obj, tk: Path, tmp: Path):
    if isinstance(obj, dict):
        return {k: _fill_obj(v, tk, tmp) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill_obj(v, tk, tmp) for v in obj]
    return _extra_fill(obj, tk, tmp) if isinstance(obj, str) else obj


def extra_rows(root: Path, rows: list) -> list:
    """The neutral extra cases (scripts/conformance-extra-cases.json), filled against the dump's
    first sandbox toolkit: Claude-shaped rows like the dump's, each with its reference `expect`.
    Tokens: {TK} the sandbox toolkit (native separators), {TKP} its forward-slash form, {TKMSYS}
    its Git Bash form, {TMP} its parent. `files` are created first."""
    src = next((r for r in rows if (r.get("env") or {}).get("X4_TOOLKIT")), None)
    if src is None:
        return []
    tk = Path(_native_path(src["env"]["X4_TOOLKIT"]))
    tmp = tk.parent
    spec = json.loads((Path(root) / "scripts" / "conformance-extra-cases.json").read_text(encoding="utf-8"))
    out = []
    for case in spec["cases"]:
        for f in case.get("files", []):
            p = Path(_extra_fill(f, tk, tmp))
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text("x\n", encoding="utf-8")
        row = {"id": case["id"], "label": "extra:" + case["id"], "hook": case["hook"],
               "payload": _fill_obj(case["payload"], tk, tmp),
               "cwd": _extra_fill(case.get("cwd", "{TK}"), tk, tmp), "env": dict(src["env"]),
               "expect": case["expect"], "relative": bool(case.get("relative")), "extra": True}
        row["kind"] = classify(row)
        out.append(row)
    return out


# ------------------------------------------------------------------ CLI -------------------- #

def _read_cases(path: Path) -> list:
    rows = []
    for n, ln in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if ln.strip():
            try:
                rows.append(json.loads(ln))
            except ValueError as e:
                raise ProfileError(f"{path}:{n}: not a JSON case row: {e}") from e
    return rows


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    adapter_argv = None
    if "--" in argv:                      # everything after the FIRST -- is the adapter's argv
        i = argv.index("--")
        argv, adapter_argv = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(
        prog="x4guard conformance",
        description="Replay the toolkit's guard corpus through an agent adapter and compare every case "
                    "with the guards' own verdict. Exit 0 all agree; 1 a disagreement; 2 cannot evaluate; "
                    "3 too little examined. See ADAPTING.md.",
        epilog="Anything after -- is the adapter command, overriding the profile's \"command\".")
    ap.add_argument("--profile", required=True, help="a profile NAME (scripts/conformance-profiles/NAME.json) or a path")
    ap.add_argument("--cases", help="a JSONL case file (dumped rows) instead of dumping scripts/test-hooks.sh now")
    ap.add_argument("--min-cases", type=int, default=80,
                    help="fewer replayed cases than this is exit 3, never a pass (default 80)")
    ap.add_argument("--workers", type=int, default=4, help="parallel cases (default 4)")
    ap.add_argument("--no-extras", action="store_true",
                    help="skip scripts/conformance-extra-cases.json (PowerShell, spaces, drive paths)")
    a = ap.parse_args(argv)
    root = Path(__file__).resolve().parents[1]

    def say(msg: str) -> None:
        print(msg, flush=True)            # progress is visible while the run is still going

    def refuse(msg: str) -> int:
        sys.stdout.write(f"x4guard conformance: CANNOT EVALUATE: {msg}\n")
        return RC_ERROR

    try:
        prof = load_profile(a.profile, root)
    except ProfileError as e:
        return refuse(str(e))
    if not (adapter_argv or prof.get("command")):
        return refuse("no adapter command: the profile has no \"command\" and none was given after --")
    if not (root / ".claude" / "hooks").is_dir():
        return refuse(f"{root / '.claude' / 'hooks'} is missing: the reference guards cannot run")
    missing = [p for p in ["bash", "jq", *prof.get("requires", [])] if not _have(p)]
    if missing:
        return refuse(f"required program(s) not found: {', '.join(dict.fromkeys(missing))}")
    try:
        cmd = adapter_command(prof, adapter_argv, root, {"hook": "<hook>"})
    except ProfileError as e:
        return refuse(str(e))
    say("adapter: " + " ".join(shlex.quote(c) for c in cmd))
    for kind, case in prof["cases"].items():
        if case["payload"] != "row" and not (Path(prof["_dir"]) / case["payload"]).is_file():
            return refuse(f"cases.{kind}: payload template not found: {Path(prof['_dir']) / case['payload']}")

    sbx = None
    try:
        if a.cases:
            try:
                rows = _read_cases(Path(a.cases))
            except (OSError, ProfileError) as e:
                return refuse(str(e))
        else:
            say("dumping the guard corpus (scripts/test-hooks.sh; several minutes; a run that is KILLED "
                "leaves .test-sandbox/conf-<pid> behind -- delete it)")
            try:
                rows, sbx = dump_cases(root)
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
                return refuse(f"the guard corpus could not be dumped: {e}")
        for r in rows:
            r["kind"] = r.get("kind") or classify(r)
        extras = [] if a.no_extras else extra_rows(root, rows)
        if not a.no_extras and rows and not extras:
            # R2-F3 (v4.0.0 review): the neutral cases were WANTED and could not be built -- this
            # was a printed note and the run went on to pass without them.
            return refuse("the neutral extra cases (scripts/conformance-extra-cases.json) could not be "
                          "built: no case row names a sandbox toolkit (env X4_TOOLKIT). Replay a dumped "
                          "corpus, or pass --no-extras to run without them")
        if not a.no_extras:
            say(f"extras: {len(extras)} neutral case(s)")
        for r in extras:                  # the reference CONTROL: the guards must still say `expect`
            got, why = reference_verdict_detail(r, root)
            if got == "error":
                return refuse(f"reference control {r['id']}: the guards gave no verdict -- {why}")
            if got != r["expect"]:
                return refuse(f"reference control {r['id']}: the guards say {got}, the case expects "
                              f"{r['expect']} -- the guard policy changed or the case is wrong; "
                              "fix scripts/conformance-extra-cases.json, never the adapter")
        rows = rows + extras
        n_todo = sum((r.get("kind") or classify(r)) in prof["cases"] for r in rows)
        say(f"replaying {n_todo} case(s) through the adapter, {a.workers} at a time (each case runs the guards "
            "twice and the adapter once; this is the slow part)")
        try:
            results = replay(rows, prof, adapter_argv, root, workers=a.workers)
        except ProfileError as e:
            return refuse(str(e))
        unreadable_ref = [r["label"] for r in results if r["reference"] == "unreadable"]
        if unreadable_ref:
            return refuse(f"the guards' own verdict was unreadable for: {', '.join(map(str, unreadable_ref[:20]))}")
        no_ref = [r for r in results if r["reference"] == "error"]
        if no_ref:
            return refuse(f"the guards gave NO verdict for {len(no_ref)} case(s) (a reference hook that could "
                          "not run is not an allow): " + "; ".join(f"{r['label']}: {r['reference_why']}"
                                                                 for r in no_ref[:10]))
        buckets = bucket_counts(rows, len(results), prof)
        if buckets.get(WINDOWS_PATH_DIALECT):
            say(f"not replayed: {buckets[WINDOWS_PATH_DIALECT]} case(s) whose file path is spelled with "
                "backslashes -- off Windows that is one relative filename, not the path the guards judge")
        n_other = buckets.get("no_native_analogue", 0)
        if n_other:
            tools = sorted({str((r.get("payload") or {}).get("tool_name")) for r in rows
                            if (r.get("kind") or classify(r)) not in prof["cases"]
                            and (r.get("kind") or classify(r)) != WINDOWS_PATH_DIALECT})
            say(f"not replayed: {n_other} case(s) with no shell command or file path for an adapter to see "
                f"(tools: {', '.join(tools)}), or of a kind the profile declares unsupported")
        rc, text = summarise(results, len(rows), buckets, prof.get("unsupported") or {}, a.min_cases)
        sys.stdout.write(text + "\n")
        return rc
    finally:
        if sbx is not None:
            remove_sandbox(sbx, root / ".test-sandbox")


if __name__ == "__main__":
    sys.exit(main())
