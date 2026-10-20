#!/usr/bin/env python3
"""x4doctor -- are this root's guards LIVE, per agent target? Read-only.

WHY THIS EXISTS. Every way the guards have failed here looked, from outside, exactly like a
guarded install: a hook that read zero bytes of stdin and exited 0; `bash` resolving to the
WSL stub, which cannot run a Windows-path script; a Store `python` stub that resolves and
then cannot run; Codex skipping a hook it has not reviewed, SILENTLY (MEASURED 2026-09-30).
None of those says anything. This command asks, per installed agent target:

  * toolchain  -- the bash, python and jq the GUARDS resolve, asked of the guards' own
                  resolver (never re-implemented: a parallel resolver answers an adjacent
                  question), and EXECUTED, because resolving is not running;
  * roots      -- the reference/game roots the guards see vs the ones the tools see;
  * parity     -- the deployed tree against the toolkit source (gates/deploy_parity.py);
  * self-test  -- `x4guard.py check` controls that MUST deny and controls that MUST allow,
                  so a guard that denies everything cannot read as healthy;
  * liveness   -- Claude's hook wiring; Codex's project trust and per-hook review state;
                  X4_GUARD; the OS-level reference protection; the x4lock state.

THE CONTRACT -- ABSENCE vs NON-ANSWER, structurally:
  every row is OK, FAIL, UNKNOWN, TODO or N/A. N/A means only "this target is not installed
  here" (or "this check cannot apply on this OS"). TODO means "correctly installed; YOUR
  step is pending" -- trusting the folder in Codex, approving its hooks, applying the OS
  protection of reference/: things the installer must never do for you (C2, install
  red-team 2026-10-04: a correct fresh install exited 1 on those alone). A check that
  RAISES becomes UNKNOWN with the exception named, never a dropped row.

    exit 0  every applicable check is OK, and at least one answered
    exit 1  any FAIL                             (a TODO never hides one)
    exit 3  no FAIL, but at least one UNKNOWN   (x4validate's degraded exit)
    exit 4  no FAIL, no UNKNOWN: only YOUR steps (TODO rows) are pending
    exit 2  could not run: no agent target at --root, nothing answered, or a usage error

The verdict line is printed FIRST (CLAUDE.md #38: a truncated report keeps its head).

It executes nothing it judges: the guard self-test goes through `x4guard.py check`, which
is side-effect-free, against a probe path that does not exist. It writes no file and never
touches Codex's config. Stdlib only, Python >= 3.10, so a broken uv or venv cannot take
down the tool that diagnoses it.

USAGE
    python scripts/x4doctor.py [--root DIR] [--agent claude|codex|generic|opencode] [--json] [--toolkit DIR]

OPENCODE (Plan 3 lane L) is BEST EFFORT, from OpenCode's docs and source, not measured: its
rows say whether the toolkit's two layers are IN PLACE (plugin + adapter, rendered deny
rules), never that OpenCode loaded them -- only the `X4 GUARDS LIVE` line in an OpenCode
session shows that. The OpenCode desktop app is not supported (anomalyco/opencode#38604).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

OK, FAIL, UNKNOWN, NA, TODO = "OK", "FAIL", "UNKNOWN", "N/A", "TODO"
STATUSES = (OK, FAIL, UNKNOWN, TODO, NA)
TARGETS = ("claude", "codex", "generic", "opencode")
HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Check:
    id: str
    target: str
    status: str
    detail: str


#: Ctx(toolkit=...) default: the toolkit this doctor LIVES IN (B2). Pass None for "no toolkit".
AUTO = object()


def own_toolkit() -> Path | None:
    """The toolkit this doctor ships in, or None outside the toolkit layout."""
    tk = HERE.parent
    return tk if (tk / "tools" / "x4validate" / "x4validate" / "_paths.py").is_file() else None


def _same_dir(a, b) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except OSError:
        return os.path.normcase(str(a)) == os.path.normcase(str(b))


@dataclass
class Ctx:
    """`toolkit` is the toolkit this doctor acts for: --toolkit, else the one it LIVES IN (B2,
    install red-team 2026-10-04: an inherited X4_TOOLKIT naming another copy made a second
    toolkit's parity row compare against the FIRST toolkit's source). $X4_TOOLKIT decides only
    when the doctor lives outside the toolkit layout. The guard probes still run with the real
    environment: what the guards at the root read is the thing being diagnosed."""
    root: Path
    toolkit: object = AUTO
    env: dict = field(default_factory=lambda: dict(os.environ))
    targets: dict = field(default_factory=dict)
    toolkit_note: str = ""

    def __post_init__(self):
        self.root = Path(self.root)
        if self.toolkit is AUTO:
            self.toolkit = own_toolkit()
            if self.toolkit is None and self.env.get("X4_TOOLKIT"):
                self.toolkit = Path(self.env["X4_TOOLKIT"])
        elif self.toolkit is not None:
            self.toolkit = Path(self.toolkit)
        env_tk = self.env.get("X4_TOOLKIT")
        if env_tk and self.toolkit is not None and not _same_dir(env_tk, self.toolkit):
            self.toolkit_note = ("acting for toolkit %s; $X4_TOOLKIT names a different toolkit, %s, "
                                 "which this doctor does NOT use (the guards at the root still read "
                                 "whatever their own environment names)" % (self.toolkit, env_tk))
        if not self.targets:
            self.targets = detect_targets(self.root)


def exit_code(rows) -> int:
    """0 all OK, 1 any FAIL, 3 UNKNOWN without FAIL, 4 only user steps (TODO) pending,
    2 nothing answered. Precedence FAIL > UNKNOWN > TODO: a pending user step never masks a
    defect or a non-answer."""
    rows = list(rows)
    answered = [r for r in rows if r.status in (OK, FAIL, TODO)]
    if not answered:
        return 2
    if any(r.status == FAIL for r in rows):
        return 1
    if any(r.status == UNKNOWN for r in rows):
        return 3
    return 4 if any(r.status == TODO for r in rows) else 0


def run_check(cid: str, target: str, fn, ctx) -> Check:
    """One check, one row. Whatever happens inside fn, a row comes out -- and a row whose
    status is not one of the four is UNKNOWN, never trusted."""
    try:
        status, detail = fn(ctx)
    except Exception as exc:  # noqa: BLE001 -- the whole point: no check may vanish
        tb = traceback.extract_tb(exc.__traceback__)
        where = ("%s:%d" % (Path(tb[-1].filename).name, tb[-1].lineno)) if tb else "?"
        return Check(cid, target, UNKNOWN, "the check itself failed: %s: %s (at %s)"
                     % (type(exc).__name__, exc, where))
    if status not in STATUSES:
        return Check(cid, target, UNKNOWN, "the check returned an unknown status %r: %s"
                     % (status, detail))
    return Check(cid, target, status, str(detail))


def detect_targets(root: Path) -> dict[str, bool]:
    """Which agent targets are installed at `root`. Keyed on each target's own PAYLOAD,
    never a bare folder or a shared file: a 3.x install wrote `.claude/x4-paths.env` for
    every agent (a Codex-only one included, and 4.x still reads it there), Codex reads a project `.codex/config.toml` of its own, and a
    hand-written AGENTS.md is not the toolkit's generic target (MEASURED on the author's
    game root). The instruction files are reported by the instructions rows instead."""
    root = Path(root)
    return {
        "claude": (root / ".claude" / "settings.json").is_file(),
        "codex": (root / ".codex" / "hooks").is_dir() or (root / ".codex" / "hooks.json").is_file(),
        "generic": (root / ".agents" / "skills").is_dir(),
        # OUR plugin, never a bare .opencode/: OpenCode writes that folder itself (its
        # .gitignore and package.json, READ config.ts) and a user may keep plugins there.
        "opencode": (root / ".opencode" / "plugins" / "x4guard.js").is_file(),
    }


def default_root(start: Path) -> Path:
    """The nearest ancestor of `start` that holds an agent target, else `start`."""
    start = Path(start).resolve()
    for d in (start, *start.parents):
        if any(detect_targets(d).values()):
            return d
    return start


#: Check groups, in report order. Each takes a Ctx and returns rows.
GROUPS: list = []


def group(fn):
    GROUPS.append(fn)
    return fn


def collect(ctx: Ctx, only: str | None = None) -> list[Check]:
    rows: list[Check] = []
    for g in GROUPS:
        try:
            got = list(g(ctx))
        except Exception as exc:  # noqa: BLE001
            got = [Check(g.__name__, "all", UNKNOWN, "the check group itself failed: %s: %s"
                         % (type(exc).__name__, exc))]
        rows.extend(got)
    if only:
        rows = [r for r in rows if r.target in (only, "all")]
    return rows


# ----------------------------------------------------------------- shared helpers

def _on_windows() -> bool:
    return os.name == "nt"


def _norm_path(p) -> str:
    """One spelling per directory, for COMPARING what two resolvers said: MSYS `/c/x` and
    `C:\\x` and `C:/x/` are one folder. Never used to open anything."""
    if p is None:
        return ""
    s = str(p).strip().replace("\\", "/")
    if _on_windows():
        if len(s) >= 3 and s[0] == "/" and s[2] == "/" and s[1].isalpha():
            s = s[1].upper() + ":" + s[2:]
        s = s.lower()
    return s.rstrip("/") if len(s) > 1 else s


def _config_spelling(cfg: str) -> Path:
    """The file an explicit X4_CONFIG names, spelled as BOTH loaders spell it (`_x4-env.sh`,
    `_paths.config_spelling`; FX-G5 / reviewer J2 item 4): MSYS `/c/` translated and a
    `::$DATA` suffix dropped on Windows, then `.` / `..` resolved lexically."""
    s = cfg
    if _on_windows():
        if len(s) >= 3 and s[0] == "/" and s[2] == "/" and s[1].isalpha():
            s = s[1].upper() + ":" + s[2:]
        if s.lower().endswith("::$data"):
            s = s[:-len("::$data")]
    return Path(os.path.normpath(s)) if s else Path(s)


def _config_stream(cfg: str) -> bool:
    """A `::` stream suffix other than exactly one trailing `::$DATA` names NO config file, as in
    `_x4-env.sh` and `_paths.config_names_stream` (FX-G6 / reviewer K M2). Windows only."""
    if not _on_windows():
        return False
    s = cfg.replace(chr(92), "/")
    if s.lower().endswith("::$data"):
        s = s[:-len("::$data")]
    return "::" in s


def _is_stub(path: str | None) -> bool:
    """The Windows bash stubs: WSL's System32/SysWOW64 launcher and the Store alias. They
    RESOLVE, and cannot run a Windows-path script -- a guard started through one fails
    open (MEASURED 2026-09-04, x4lock's docstring)."""
    if not path:
        return False
    s = path.replace("/", "\\").lower()
    return any(k in s for k in ("\\system32\\", "\\syswow64\\", "\\windowsapps\\"))


def same_bash(a: str | None, b: str | None) -> bool:
    """One bash, possibly spelled two ways: Git for Windows ships `<Git>/bin/bash.exe` as a
    launcher for `<Git>/usr/bin/bash.exe`. Anything else is compared as a path."""
    if not a or not b:
        return False
    na, nb = _norm_path(a), _norm_path(b)
    if na == nb:
        return True

    def git_root(p: str) -> str | None:
        for tail in ("/usr/bin/bash.exe", "/bin/bash.exe", "/usr/bin/bash", "/bin/bash"):
            if p.endswith(tail):
                return p[: -len(tail)]
        return None
    ra, rb = git_root(na), git_root(nb)
    return _on_windows() and ra is not None and ra == rb


def _load(name: str, path: Path):
    """Import one file as a module under a doctor-private name. Registered in sys.modules
    while it executes, because @dataclass looks its own module up there."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


def _run(cmd, *, env=None, cwd=None, timeout=60, stdin=None):
    import subprocess
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd,
                          timeout=timeout, input=stdin, encoding="utf-8", errors="replace")


def guard_dirs(ctx: Ctx) -> list[tuple[str, Path]]:
    """The installed guard copies, (target, hooks dir), Claude first."""
    out = []
    if ctx.targets.get("claude") and (ctx.root / ".claude" / "hooks" / "_x4-env.sh").is_file():
        out.append(("claude", ctx.root / ".claude" / "hooks"))
    if ctx.targets.get("codex") and (ctx.root / ".codex" / "hooks" / "_x4-env.sh").is_file():
        out.append(("codex", ctx.root / ".codex" / "hooks"))
    if ctx.targets.get("opencode") and (ctx.root / ".opencode" / "hooks" / "_x4-env.sh").is_file():
        out.append(("opencode", ctx.root / ".opencode" / "hooks"))
    return out


def _hook_env(ctx: Ctx, target: str, hooks: Path) -> dict:
    """The environment a hook of `target` would see. Claude Code sets CLAUDE_PROJECT_DIR to
    the project it runs in; Codex sets nothing of the kind."""
    env = dict(ctx.env)
    env["HOOK_DIR"] = hooks.as_posix()
    if target == "claude":
        env["CLAUDE_PROJECT_DIR"] = str(ctx.root)
    return env


def guard_bash(ctx: Ctx) -> tuple[str | None, str]:
    """The bash the deployed x4guard.py would use -- asked of x4guard itself."""
    dirs = guard_dirs(ctx)
    if not dirs:
        return None, "no guard copy installed here"
    xg = dirs[0][1] / "x4guard.py"
    if not xg.is_file():
        return None, "%s is missing" % xg
    saved = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(ctx.env)
        bash, why = _load("x4doctor_x4guard", xg).resolve_bash()
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return bash, (why or "")


def _starts_bare_bash(command) -> bool:
    """Does this hook command line start with an UNQUALIFIED bash (resolved from PATH)?"""
    if not isinstance(command, str):
        return False
    tok = command.strip().split(None, 1)[0].strip('"\'').lower() if command.strip() else ""
    return tok in ("bash", "bash.exe")


def windows_path_bash_sites(ctx: Ctx) -> list[str]:
    """The installed hooks that would start bare `bash` from the WINDOWS PATH (R2-B2).

    READ from the hook definitions, never assumed: Claude Code runs a hook command in its Git
    Bash unless the hook names another `shell`, so only such a hook resolves `bash` from the
    Windows PATH; Codex runs `commandWindows` (else `command`) through cmd.exe on Windows."""
    out = []
    if ctx.targets.get("claude"):
        for name in ("settings.json", "settings.local.json"):
            f = ctx.root / ".claude" / name
            try:
                d = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
            except (OSError, ValueError):
                continue
            for ev, groups in (d.get("hooks") or {}).items():
                for g in groups or []:
                    for h in (g or {}).get("hooks") or []:
                        sh = str(h.get("shell") or "bash").lower()
                        if "bash" not in sh and _starts_bare_bash(h.get("command")):
                            out.append("the Claude %s hook in .claude/%s (shell %s)" % (ev, name, sh))
    if ctx.targets.get("codex"):
        f = ctx.root / ".codex" / "hooks.json"
        try:
            d = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
        except (OSError, ValueError):
            d = {}
        for ev, groups in (d.get("hooks") or {}).items():
            for g in groups or []:
                for h in (g or {}).get("hooks") or []:
                    if _starts_bare_bash(h.get("commandWindows") or h.get("command")):
                        out.append("the Codex %s hook in .codex/hooks.json (%s)"
                                   % (ev, "commandWindows" if h.get("commandWindows")
                                      else "no commandWindows, so `command` runs under cmd.exe"))
    return out


#: Sourced through the guards' bash: the values the guards see, one KEY=VALUE per line.
_PROBE = r'''
. "$HOOK_DIR/_x4-env.sh" >/dev/null 2>&1
printf 'TOOLKIT=%s\nGAME=%s\nREFERENCE=%s\nPROFILE=%s\nMODS=%s\nCFG=%s\n' \
  "${X4_TOOLKIT:-}" "${X4_GAME:-}" "${X4_REFERENCE:-}" "${X4_PROFILE:-}" "${X4_MODS:-}" "${_x4_cfg:-}"
printf 'CFGSRC=%s\nCFGTK=%s\nREFDEF=%s\n' "${_x4_cfg_src:-}" "${_x4_cfg_tk:-}" "${_x4_ref_defaulted:-}"
printf 'CFGIGN=%s\n' "${_x4_cfg_ignored:-}"
x4_resolve_python
printf 'PY=%s\n' "$X4_PY"
if [ -n "$X4_PY" ]; then
  _v="$("$X4_PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"
  printf 'PYRC=%s\nPYVER=%s\n' "$?" "$_v"
fi
if printf '%s' '{}' | "${JQ:-jq}" -e . >/dev/null 2>&1; then echo JQ=ok; else echo JQ=missing; fi
'''


def guard_probe(ctx: Ctx) -> tuple[dict | None, str]:
    """Run _PROBE through the guards' own bash with the first guard copy's environment.
    Cached per Ctx: four groups read it."""
    cached = getattr(ctx, "_probe", None)
    if cached is not None:
        return cached
    bash, why = guard_bash(ctx)
    if not bash:
        res = (None, "no bash the guards would run: " + why)
    else:
        target, hooks = guard_dirs(ctx)[0]
        r = _run([bash, "-c", _PROBE], env=_hook_env(ctx, target, hooks), cwd=str(ctx.root))
        vals = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
        res = (vals, "") if "PY" in vals else (None, "the probe printed nothing usable (rc %s): %s"
                                               % (r.returncode, (r.stderr or r.stdout)[-300:]))
    ctx._probe = res
    return res


# ----------------------------------------------------------------- Task 6: toolchain

@group
def check_toolchain(ctx: Ctx) -> list[Check]:
    dirs = guard_dirs(ctx)
    if not dirs:
        why = "no guard copy is installed here (.claude/hooks, .codex/hooks or .opencode/hooks)"
        return [Check(i, "all", NA, why) for i in ("bash.guards", "bash.path", "bash.agree",
                                                    "python.guards", "jq")]
    rows = []
    bash, why = guard_bash(ctx)

    def _bash_guards(_):
        if not bash:
            return FAIL, "x4guard resolves NO usable bash: " + why
        if _is_stub(bash):
            return FAIL, "x4guard would run the guards with a STUB bash (%s): it cannot run them" % bash
        r = _run([bash, "-c", "echo ok"])
        if r.returncode != 0 or r.stdout.strip() != "ok":
            return FAIL, "%s resolves but does not run (rc %s)" % (bash, r.returncode)
        return OK, bash
    rows.append(run_check("bash.guards", "all", _bash_guards, ctx))

    def _bash_path(_):
        # R2-B2 (second install red-team, 2026-10-04): this row FAILED on every stock Windows
        # PATH, where System32's WSL stub is the first bash, although no installed hook ever
        # starts bash from that PATH. It now judges what the HOOKS would run: FAIL only when a
        # hook WOULD start bare `bash` from the Windows PATH while that resolves to a stub, or
        # when no Git Bash can be resolved at all. A stub that nothing reaches is named, as
        # information.
        if not _on_windows():
            return NA, "the bash-stub trap is Windows-only"
        import shutil
        first = shutil.which("bash", path=ctx.env.get("PATH"))
        stub_first = bool(first) and _is_stub(first)
        if not bash or _is_stub(bash):
            nob = why or "x4guard found none"
            if "setx X4_BASH" not in nob:
                nob += ('. Fix (NEW shells): setx X4_BASH "C:%sProgram Files%sGit%sbin%sbash.exe" '
                         '(adjust the path if Git is installed elsewhere)' % ((chr(92),) * 4))
            if stub_first:
                return FAIL, ("the first bash on PATH is the WSL/Store STUB (%s) and the guards "
                              "resolve no Git Bash: %s" % (first, nob))
            return FAIL, "the guards resolve no Git Bash: %s" % nob
        sites = windows_path_bash_sites(ctx)
        if stub_first and sites:
            return FAIL, ("the first bash on PATH is a STUB (%s), and %s start(s) bare `bash` from "
                          "that PATH, so the hook fails OPEN. Fix: re-run the installer for this "
                          "root (its hook definitions never start bash from PATH), or edit those "
                          "hooks to call Git Bash by path (\"%s\")" % (first, "; ".join(sites), bash))
        if stub_first:
            return OK, ("no installed hook starts bash from the Windows PATH (Claude Code runs its "
                        "hooks in Git Bash, Codex uses its PowerShell commandWindows, the guards "
                        "resolve %s themselves); informational: the first bash on this PATH is the "
                        "stub %s, so do not type `bash ...` in PowerShell or cmd" % (bash, first))
        if not first:
            return OK, ("no bash on the Windows PATH, and no installed hook needs one (the guards "
                        "resolve %s themselves)" % bash)
        return OK, first
    rows.append(run_check("bash.path", "all", _bash_path, ctx))

    def _bash_agree(_):
        gb = None
        for base in (ctx.root, ctx.toolkit):
            if base and (Path(base) / "scripts" / "gitbash.py").is_file():
                gb = Path(base) / "scripts" / "gitbash.py"
                break
        if gb is None:
            return UNKNOWN, "scripts/gitbash.py not found, so the toolkit's own resolver cannot be asked"
        theirs = _load("x4doctor_gitbash", gb).find_bash()
        if not bash or not theirs:
            return UNKNOWN, "x4guard: %s; scripts/gitbash.py: %s" % (bash or "none", theirs or "none")
        if _norm_path(bash) == _norm_path(theirs):
            return OK, "x4guard and scripts/gitbash.py both resolve %s" % bash
        if same_bash(bash, theirs):
            return OK, ("one Git install, two spellings -- x4guard: %s ; scripts/gitbash.py: %s"
                        % (bash, theirs))
        return UNKNOWN, ("the resolvers DISAGREE -- x4guard: %s ; scripts/gitbash.py: %s. Both run, "
                         "but a fix applied to one is not applied to the other" % (bash, theirs))
    rows.append(run_check("bash.agree", "all", _bash_agree, ctx))

    def _python(_):
        vals, why2 = guard_probe(ctx)
        if vals is None:
            return UNKNOWN, why2
        py = vals.get("PY", "")
        if not py:
            if ctx.env.get("X4_PYTHON"):
                return FAIL, ("X4_PYTHON=%s does not resolve, and the guards REFUSE to fall back "
                              "to another interpreter" % ctx.env["X4_PYTHON"])
            return FAIL, "the guards find no python/python3/py: protect-bash cannot analyse commands"
        if vals.get("PYRC") != "0" or not vals.get("PYVER"):
            return FAIL, ("%s resolves but does not run (rc %s) -- a Store stub, or a broken "
                          "install" % (py, vals.get("PYRC")))
        major, minor = (int(x) for x in vals["PYVER"].split("."))
        if (major, minor) < (3, 10):
            return FAIL, "%s is Python %s; the guards need >= 3.10" % (py, vals["PYVER"])
        return OK, "%s -> Python %s" % (py, vals["PYVER"])
    rows.append(run_check("python.guards", "all", _python, ctx))

    def _jq(_):
        vals, why2 = guard_probe(ctx)
        if vals is None:
            return UNKNOWN, why2
        if vals.get("JQ") == "ok":
            return OK, "jq runs"
        if vals.get("PYRC") == "0" and vals.get("PYVER"):
            return OK, "jq absent; the guards fall back to python (degraded, as setup.sh warns)"
        return FAIL, "neither jq nor a working python: the guards cannot render a verdict"
    rows.append(run_check("jq", "all", _jq, ctx))
    return rows


# ----------------------------------------------------------------- Task 6: roots

#: Asked of the TOOLS' resolver, run from the root the way an agent would run a tool.
_PY_ROOTS = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from x4validate import _paths
if len(sys.argv) > 2 and sys.argv[2] and hasattr(_paths, "use_toolkit"):
    _paths.use_toolkit(sys.argv[2])                  # the doctor's toolkit, explicitly (B2)
out = {}
for k in ("game_root", "reference", "profile", "_find_env_file"):
    try:
        v = getattr(_paths, k)()
        out[k] = None if v is None else str(v)
    except Exception as e:
        out[k] = None
        out[k + "_error"] = "%s: %s" % (type(e).__name__, e)
try:
    out["config_state"] = _paths.config_state()      # absent before 4.0
except Exception:
    out["config_state"] = None
print(json.dumps(out))
'''


def tools_roots(ctx: Ctx) -> tuple[dict | None, str]:
    """The tools' answer. The resolver CODE comes from the doctor's toolkit (`ctx.toolkit`:
    --toolkit, else the toolkit this doctor lives in), else the root, else the toolkit this
    doctor ships in. The CONFIG it reads is that toolkit's (passed as `use_toolkit`, B2),
    unless the environment names one with $X4_CONFIG; the environment's X4_* variables still
    outrank it, as for any tool an agent runs from the root (FX-B2: this said "env and cwd
    alone", true only before B2)."""
    for base in (ctx.toolkit, ctx.root, HERE.parent):
        if base and (Path(base) / "tools" / "x4validate" / "x4validate" / "_paths.py").is_file():
            pkg = Path(base) / "tools" / "x4validate"
            r = _run([sys.executable, "-c", _PY_ROOTS, str(pkg), str(ctx.toolkit or "")],
                     env=dict(ctx.env), cwd=str(ctx.root))
            try:
                return json.loads(r.stdout.strip().splitlines()[-1]), str(pkg)
            except (ValueError, IndexError):
                return None, "the tools' resolver failed (rc %s): %s" % (r.returncode, r.stderr[-300:])
    return None, "no tools/x4validate under X4_TOOLKIT or the root, so the tools' view cannot be asked"


def _cfg_differing_keys(a: Path, b: Path) -> list[str]:
    """KEY names whose assignments differ between two path configs -- the comparison of
    `_paths._assignments` (CR and indent stripped, blank and comment lines dropped), restated
    because the doctor is stdlib-only and may examine an install whose tools predate it.
    Never a value: the file carries X4_NEXUS_KEY."""
    def lines(p):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return set()
        out = set()
        for raw in text.split("\n"):
            ln = raw.replace("\r", "").lstrip()
            if ln and not ln.startswith("#") and "=" in ln:
                out.add(ln)
        return out

    def key(ln):
        k = ln.split("=", 1)[0].strip()
        return k[len("export "):].strip() if k.startswith("export ") else k
    return sorted({key(ln) for ln in lines(a) ^ lines(b)})


@group
def check_roots(ctx: Ctx) -> list[Check]:
    if not guard_dirs(ctx):
        why = "no guard target is installed here"
        return [Check(i, "all", NA, why) for i in ("roots.config", "roots.reference", "roots.game",
                                                    "roots.agree")]
    vals, why = guard_probe(ctx)

    def _ref(_):
        if vals is None:
            return UNKNOWN, why
        ref = vals.get("REFERENCE", "")
        if not ref:
            return FAIL, "the guards resolve NO reference root: nothing under it is protected"
        if not Path(ref).is_dir():
            # FX-B3: a fresh install before its unpack step is a KNOWN, normal state -- the
            # user's step (TODO, exit 4), not a can't-tell (UNKNOWN, exit 3). FX-B4: unless
            # something says the tree was MOVED (_missing_tree_row).
            return _missing_tree_row(ctx, ref, (
                "YOUR STEP: the guards protect %s, which does not exist yet (not "
                "unpacked) -- run: %s. Nothing is there to damage meanwhile. If your "
                "unpacked tree is elsewhere, set X4_REFERENCE in x4-paths.env instead."
                % (ref, _unpack_cmd(ctx, ref))))
        return OK, ref

    def _game(_):
        if vals is None:
            return UNKNOWN, why
        game = vals.get("GAME", "")
        if not game:
            return FAIL, "X4_GAME is unset for the guards: the game install is NOT protected"
        if not Path(game).is_dir():
            return FAIL, "the guards' X4_GAME names a folder that does not exist: %s" % game
        return OK, game

    def _agree(_):
        if vals is None:
            return UNKNOWN, why
        tools, where = tools_roots(ctx)
        if tools is None:
            return UNKNOWN, where
        cfg_g = vals.get("CFG") or "(none)"
        cfg_t = tools.get("_find_env_file") or "(none found)"
        diffs = []
        for label, gkey, tkey in (("reference", "REFERENCE", "reference"), ("game", "GAME", "game_root")):
            gv, tv = vals.get(gkey, ""), tools.get(tkey)
            if _norm_path(gv) != _norm_path(tv):
                diffs.append("%s: guards %s vs tools %s" % (label, gv or "(unset)", tv or "(unset)"))
        detail = "guards read %s; tools read %s" % (cfg_g, cfg_t)
        if diffs:
            return FAIL, ("the guards and the tools see DIFFERENT trees -- they protect one and "
                          "read another. %s. (%s)" % ("; ".join(diffs), detail))
        return OK, "the guards and the tools agree on reference and game; " + detail

    def _config(_):
        if vals is None:
            return UNKNOWN, why
        cfg, src = vals.get("CFG", ""), vals.get("CFGSRC", "")
        # FX-B2 (reviewer C I1): a line the grammar REFUSES is dropped by every loader, and what
        # it would set falls back to a default -- silently, until this row. `guard` has its own
        # FAIL (guard.escape); `key` (a non-X4 variable) configures nothing either way.
        ign = [t for t in (vals.get("CFGIGN") or "").split() if t.rpartition(":")[2] in
               ("shape", "subst", "operator")]
        if ign:
            return FAIL, ("the path config %s has lines every loader IGNORES (line:reason): %s -- "
                          "what they would set falls back to a default. shape = not KEY=value; "
                          "subst = $( ) or a backtick (never run): write the literal path; "
                          "operator = an unquoted ; & | < >: quote the value, e.g. "
                          "X4_REFERENCE=\"D:/x4/ref&x\". Fix it by hand (agents may not write it)"
                          % (cfg or "?", ", ".join(ign)))
        if not src:
            return _config_pre40(cfg)
        tk = vals.get("CFGTK") or vals.get("TOOLKIT") or "<toolkit>"
        new, old = "%s/x4-paths.env" % tk, "%s/.claude/x4-paths.env" % tk
        if src in ("new", "explicit"):
            return OK, "the guards read %s" % cfg
        if src == "legacy":
            return OK, ("the guards read %s -- the 3.x location, DEPRECATED (still read for all of "
                        "4.x); 4.0 reads %s. Move it: x4config.py migrate --apply, i.e. "
                        "python \"%s/scripts/x4config.py\" migrate --apply" % (cfg, new, tk))
        if src == "both":
            tools, _where = tools_roots(ctx)
            state = (tools or {}).get("config_state")
            keys = []
            if Path(new).is_file() and Path(old).is_file():
                keys = _cfg_differing_keys(Path(new), Path(old))
            if keys or state == "both-differ":
                return FAIL, ("TWO path configs that DIFFER (on %s): the guards read %s and IGNORE the "
                              "3.x %s, but an older guard copy elsewhere may still read it and protect "
                              "a different tree. Keep the values you want in the first, then delete or "
                              "rename the second; check with python \"%s/scripts/x4config.py\" status "
                              "--root \"%s\"" % (", ".join(keys) or "unknown keys", new, old, tk, tk))
            return OK, ("the guards read %s; the 3.x %s agrees and is ignored. Retire it: "
                        "python \"%s/scripts/x4config.py\" migrate --apply" % (new, old, tk))
        # none / explicit-missing
        if vals.get("REFDEF") == "1":
            if src == "explicit-missing":
                where = ("X4_CONFIG names %s, which does not exist, so no config file is read"
                         % ctx.env.get("X4_CONFIG", "?"))
            else:
                where = "neither %s nor the 3.x %s exists" % (new, old)
            return FAIL, ("the guards read NO path config: %s, and X4_REFERENCE is not exported. They "
                          "ASSUME reference/ is %s (hard-blocked there) and know the game and profile "
                          "only by folder NAME. Fix: re-run the installer, or copy "
                          "%s/x4-paths.env.example to %s; or set X4_TOOLKIT in your user environment "
                          "to the toolkit holding x4-paths.env" % (where, vals.get("REFERENCE", "?"), tk, new))
        return OK, ("no config file is read; the roots come from the exported environment "
                    "(X4_REFERENCE is set)")

    def _config_pre40(cfg):
        """A 3.x guard copy: `_x4_cfg` is always the path it LOOKED for, and there is no state."""
        if not cfg:
            return UNKNOWN, "the guards did not report which config they read (_x4_cfg is empty)"
        if not Path(cfg).is_file():
            return FAIL, ("the guards read NO path config: %s does not exist, so every root they "
                          "protect is a default guess (the reference falls back to <toolkit>/reference). "
                          "Set X4_TOOLKIT in your user environment to the toolkit holding x4-paths.env"
                          % cfg)
        return OK, "the guards read %s (a pre-4.0 guard copy)" % cfg

    return [run_check("roots.config", "all", _config, ctx),
            run_check("roots.reference", "all", _ref, ctx),
            run_check("roots.game", "all", _game, ctx),
            run_check("roots.agree", "all", _agree, ctx)]


# ----------------------------------------------------------------- Task 8: self-test

#: The probe path. It must NOT exist: nothing to damage even if a future x4guard regressed
#: into executing what it judges.
PROBE_NAME = "__x4doctor_probe__.xml"


def _controls(ref: str | None) -> list[tuple[str, str, list[str]]]:
    """(id, must, x4guard check args). MEASURED 2026-10-02 against the deployed and the repo
    copies, X4_TOOLKIT set: every deny control denied, both allow controls allowed."""
    out = []
    if ref:
        r = ref.replace("\\", "/").rstrip("/")
        p = r + "/" + PROBE_NAME
        out += [
            ("deny.write.ref", "deny", ["--kind", "write", "--path", p]),
            ("deny.delete.ref", "deny", ["--kind", "delete", "--path", p]),
            ("deny.pwsh.ref", "deny", ["--kind", "shell", "--shell", "powershell", "--command",
                                       "Set-Content -Path '%s' -Value x" % p]),
            ("deny.bash.rmref", "deny", ["--kind", "shell", "--shell", "bash", "--command",
                                         "rm -rf '%s'" % r]),
        ]
    out += [
        ("allow.bash.echo", "allow", ["--kind", "shell", "--shell", "bash", "--command", "echo x4doctor"]),
        ("allow.pwsh.echo", "allow", ["--kind", "shell", "--shell", "powershell", "--command",
                                      "Write-Output x4doctor"]),
    ]
    return out


def _ask_guard(xg: Path, args: list[str], env: dict, cwd: str) -> dict:
    """One `x4guard.py check`. Run with THIS interpreter (x4guard is stdlib, >= 3.10); the
    guard scripts it starts resolve their own python exactly as in a session."""
    r = _run([sys.executable, str(xg), "check", *args], env=env, cwd=cwd, timeout=120)
    try:
        v = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"decision": "?", "inert": True,
                "reason": "x4guard printed no verdict (rc %s): %s" % (r.returncode, r.stderr[-200:])}
    return v


@group
def check_guards(ctx: Ctx) -> list[Check]:
    dirs = guard_dirs(ctx)
    rows = []
    for target in ("claude", "codex", "opencode"):
        if any(t == target for t, _ in dirs):
            continue
        if ctx.targets.get(target):
            hooks = ctx.root / ("." + target) / "hooks"
            rows.append(Check("guards.selftest." + target, target, FAIL,
                              "the %s target is installed but its guard copy is not (%s has no "
                              "_x4-env.sh): its hooks point at scripts that are absent, and a hook "
                              "that cannot run lets the tool run. Re-run the installer" % (target, hooks)))
        else:
            rows.append(Check("guards.selftest." + target, target, NA,
                              "the %s target is not installed here" % target))
    for target, hooks in dirs:
        def _selftest(_, target=target, hooks=hooks):
            from concurrent.futures import ThreadPoolExecutor
            xg = hooks / "x4guard.py"
            if not xg.is_file():
                return FAIL, "%s is missing: nothing can ask these guards for a verdict" % xg
            vals, why = guard_probe(ctx)
            ref = (vals or {}).get("REFERENCE") or None
            controls = _controls(ref)
            env = _hook_env(ctx, target, hooks)
            t0 = time.monotonic()
            # 3 workers: 6 sequential checks took 29-36 s on a loaded machine (MEASURED).
            with ThreadPoolExecutor(max_workers=3) as pool:
                got = list(pool.map(lambda c: (c, _ask_guard(xg, c[2], env, str(ctx.root))), controls))
            dt = time.monotonic() - t0
            inert = [(c[0], v.get("reason", "")) for c, v in got if v.get("inert")]
            leaked = [c[0] for c, v in got if not v.get("inert") and c[1] == "deny" and v.get("decision") != "deny"]
            blocked = [c[0] for c, v in got if not v.get("inert") and c[1] == "allow"
                       and v.get("decision") not in ("allow", "advise")]
            where = "%s (%d control(s), %.1fs)" % (xg, len(controls), dt)
            if inert:
                return FAIL, ("guards INERT -- they cannot evaluate: %s. %s"
                              % ("; ".join("%s: %s" % (i, r[:160]) for i, r in inert[:3]), where))
            if leaked:
                return FAIL, ("the guard LET THROUGH what it must deny (%s): a write or delete into "
                              "reference/ was not refused. %s" % (", ".join(leaked), where))
            if blocked:
                return FAIL, ("the guards deny what they must allow (%s): a guard that denies "
                              "everything is not a working guard. %s" % (", ".join(blocked), where))
            if not ref:
                return UNKNOWN, ("the allow controls passed, but no reference root resolved, so the "
                                 "deny controls could not run (%s). %s" % (why or "empty X4_REFERENCE", where))
            return OK, "every control held: %s" % where
        rows.append(run_check("guards.selftest." + target, target, _selftest, ctx))
    return rows


# ----------------------------------------------------------------- Codex trust model
#
# Codex runs a project hook only if `[hooks.state.'<key>']` in its config.toml holds a
# `trusted_hash` equal to the definition's CURRENT hash (and `enabled` is not false).
# Anything else is skipped SILENTLY (MEASURED, spike 2026-09-30). The scheme below is lane
# B's reproduction of Codex 0.160.0's `hook_hash` (READ: codex-rs hooks/src/engine/
# discovery.rs + config/src/fingerprint.rs), which matched 4 of 4 hashes Codex itself
# stored in the spike (MEASURED by lane B; re-measured by lane C, see the commit). Lane B
# ships the same model as `.codex/hooks/codex_trust.py`; a test pins the two together once
# it lands. A future Codex may change the scheme: when NO entry matches, the verdict is
# UNKNOWN ("scheme unconfirmed on this Codex"), never "modified".

def _snake(event: str) -> str:
    out = []
    for i, ch in enumerate(event):
        if ch.isupper() and i:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def codex_hook_hash(event_key: str, group: dict, handler: dict, windows: bool | None = None) -> str:
    """sha256 over canonical JSON {"event_name", "matcher"?, "hooks": [handler]}: keys
    sorted, compact separators, None omitted; timeout defaults to 600, async to false,
    additionalContextLimit omitted when unset or 2500; on Windows commandWindows wins."""
    import hashlib
    if windows is None:
        windows = _on_windows()
    cmd = handler.get("commandWindows") if windows and handler.get("commandWindows") is not None \
        else handler.get("command")
    h = {"type": handler.get("type", "command"), "command": cmd,
         "timeout": 600 if handler.get("timeout") is None else handler["timeout"],
         "async": bool(handler.get("async", False))}
    if handler.get("statusMessage") is not None:
        h["statusMessage"] = handler["statusMessage"]
    acl = handler.get("additionalContextLimit")
    if acl is not None and acl != 2500:
        h["additionalContextLimit"] = acl
    ident = {"event_name": event_key, "hooks": [h]}
    if group.get("matcher") is not None:
        ident["matcher"] = group["matcher"]
    s = json.dumps(ident, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def expected_hooks(hooks_json: Path, windows: bool | None = None) -> dict[str, str]:
    """{state key: expected hash} for every handler in a hooks.json, keyed the way Codex
    keys them: `<absolute hooks.json path>:<snake_case event>:<group>:<handler>`."""
    data = json.loads(hooks_json.read_text(encoding="utf-8"))
    base = str(hooks_json.resolve())
    out = {}
    for event, groups in (data.get("hooks") or {}).items():
        ek = _snake(event)
        for i, g in enumerate(groups or []):
            for j, h in enumerate(g.get("hooks") or []):
                out["%s:%s:%d:%d" % (base, ek, i, j)] = codex_hook_hash(ek, g, h, windows)
    return out


class TomlUnreadable(ValueError):
    """A line inside a table x4doctor reads could not be parsed: the answer is UNKNOWN."""


def _toml_tables(text: str) -> dict[str, dict]:
    """The `[projects.'...']` and `[hooks.state.'...']` tables of a Codex config.toml.

    tomllib where it exists (Python >= 3.11). On 3.10 a deliberately NARROW reader: it
    understands only those two table shapes and the three keys read from them
    (trust_level, trusted_hash, enabled); any other line INSIDE one of them raises
    TomlUnreadable rather than being guessed at. Every other table is skipped whole.
    """
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        tomllib = None
    if tomllib is not None:
        try:
            doc_ = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise TomlUnreadable(str(e)) from e
        out = {}
        for k, v in (doc_.get("projects") or {}).items():
            out["projects:" + k] = v
        for k, v in ((doc_.get("hooks") or {}).get("state") or {}).items():
            out["hooks.state:" + k] = v
        return out
    import re
    head = re.compile(r"""^\[(projects|hooks\.state)\.(?:'([^']*)'|"((?:[^"\\]|\\.)*)")\]\s*(?:#.*)?$""")
    kv = re.compile(r"""^(trust_level|trusted_hash|enabled)\s*=\s*(?:"([^"\\]*)"|'([^']*)'|(true|false))\s*(?:#.*)?$""")
    out, cur = {}, None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            m = head.match(line)
            if m:
                key = m.group(2) if m.group(2) is not None else bytes(m.group(3), "utf-8").decode("unicode_escape")
                cur = out.setdefault("%s:%s" % (m.group(1), key), {})
            else:
                cur = None
            continue
        if cur is None:
            continue
        m = kv.match(line)
        if not m:
            raise TomlUnreadable("config.toml line %d is not understood by the 3.10 reader: %r" % (n, raw[:80]))
        val = m.group(2) if m.group(2) is not None else m.group(3) if m.group(3) is not None else (m.group(4) == "true")
        cur[m.group(1)] = val
    return out


def codex_home(ctx: Ctx) -> Path:
    """$CODEX_HOME, else ~/.codex (Codex's documented default)."""
    return Path(ctx.env["CODEX_HOME"]) if ctx.env.get("CODEX_HOME") else Path.home() / ".codex"


def trust_report(hooks_json: Path, config: Path, root: Path, windows: bool | None = None) -> dict:
    """{"project": trusted|ancestor|untrusted, "hooks": [{key, status}], "scheme": ...}.
    Raises TomlUnreadable / OSError / ValueError when it cannot tell -- the caller turns
    that into UNKNOWN, never into a verdict."""
    tables = _toml_tables(config.read_text(encoding="utf-8"))
    rkey = str(Path(root).resolve()).lower()
    project = "untrusted"
    for k, v in tables.items():
        if not k.startswith("projects:") or not isinstance(v, dict) or v.get("trust_level") != "trusted":
            continue
        p = k[len("projects:"):].lower().rstrip("\\/")
        if p == rkey.rstrip("\\/"):
            project = "trusted"
            break
        if rkey.startswith(p + os.sep) or rkey.startswith(p + "/"):
            project = "ancestor"
    state = {k[len("hooks.state:"):]: v for k, v in tables.items()
             if k.startswith("hooks.state:") and isinstance(v, dict)}
    folded = {k.lower(): v for k, v in state.items()}
    hooks = []
    for key, want in sorted(expected_hooks(hooks_json, windows).items()):
        ent = state.get(key) or folded.get(key.lower())
        if ent is None:
            st = "untrusted"
        elif ent.get("enabled") is False:
            st = "disabled"
        elif ent.get("trusted_hash") == want:
            st = "trusted"
        else:
            st = "mismatch"
        hooks.append({"key": key, "status": st})
    matched = any(h["status"] in ("trusted", "disabled") for h in hooks)
    return {"project": project, "hooks": hooks,
            "scheme": "confirmed" if matched else "unconfirmed"}


# ----------------------------------------------------------------- Task 9: liveness

def _codex_cmd() -> list[str] | None:
    import shutil
    exe = shutil.which("codex")
    return [exe] if exe else None


@group
def check_codex(ctx: Ctx) -> list[Check]:
    ids = ("codex.hooks", "codex.config", "codex.trusted", "codex.reviewed", "codex.rules")
    if not ctx.targets.get("codex"):
        return [Check(i, "codex", NA, "the codex target is not installed here") for i in ids]
    hj = ctx.root / ".codex" / "hooks.json"
    cfg = codex_home(ctx) / "config.toml"
    box: dict = {}

    def report():
        if "r" not in box:
            box["r"] = trust_report(hj, cfg, ctx.root)
        return box["r"]

    def _hooks(_):
        if not hj.is_file():
            return FAIL, ("no .codex/hooks.json: Codex runs NO X4 hook here. Re-run the installer, "
                          "which renders it for this folder")
        n = len(expected_hooks(hj))
        return (OK, "%d hook definition(s) in %s" % (n, hj)) if n else \
            (FAIL, "%s defines no hooks" % hj)

    def _config(_):
        if not cfg.is_file():
            return UNKNOWN, ("no Codex config at %s: Codex has never run here, or CODEX_HOME points "
                             "elsewhere -- trust and review cannot be read" % cfg)
        _toml_tables(cfg.read_text(encoding="utf-8"))          # raises -> UNKNOWN
        return OK, "read %s" % cfg

    def _trusted(_):
        if not cfg.is_file() or not hj.is_file():
            return UNKNOWN, "needs both .codex/hooks.json and %s" % cfg
        p = report()["project"]
        if p == "trusted":
            return OK, "Codex trusts %s" % ctx.root
        if p == "ancestor":
            return UNKNOWN, ("only an ANCESTOR folder is trusted; whether Codex extends that trust to "
                             "%s is unverified" % ctx.root)
        return TODO, ("YOUR STEP: the project is not trusted yet, so Codex will not load .codex/ "
                      "here. Run `codex` in %s and trust the folder" % ctx.root)

    def _reviewed(_):
        if not cfg.is_file() or not hj.is_file():
            return UNKNOWN, "needs both .codex/hooks.json and %s" % cfg
        rep = report()
        hooks = rep["hooks"]
        if not hooks:
            return FAIL, "no hook definitions to review"
        bad = {s: [h["key"].rsplit(":", 3)[1] for h in hooks if h["status"] == s]
               for s in ("untrusted", "disabled", "mismatch")}
        n_ok = sum(h["status"] == "trusted" for h in hooks)
        if bad["disabled"]:
            return FAIL, "%d hook(s) disabled in Codex's config (%s): they never run" % (
                len(bad["disabled"]), ", ".join(bad["disabled"]))
        if bad["mismatch"]:
            if rep["scheme"] == "unconfirmed":
                return UNKNOWN, ("no reviewed hook matches x4doctor's hash: the trust-hash scheme is "
                                 "unconfirmed on this Codex version (reproduced on 0.159.2 spike data), "
                                 "so changed-vs-reviewed cannot be told")
            return FAIL, ("%d hook definition(s) changed since they were reviewed (%s): Codex treats "
                          "them as untrusted. Re-approve them in /hooks" % (len(bad["mismatch"]),
                                                                          ", ".join(bad["mismatch"])))
        if bad["untrusted"]:
            return TODO, ("YOUR STEP: %d hook(s) NOT REVIEWED yet (%s): Codex skips them SILENTLY "
                          "until you do. Open /hooks in Codex here and approve them"
                          % (len(bad["untrusted"]), ", ".join(bad["untrusted"])))
        return OK, "%d of %d hook(s) reviewed, hashes current" % (n_ok, len(hooks))

    def _rules(_):
        files = sorted((ctx.root / ".codex" / "rules").glob("*.rules"))
        if not files:
            return FAIL, "no .codex/rules/*.rules: the execpolicy layer is absent"
        cmd = _codex_cmd()
        if cmd is None:
            return UNKNOWN, ("%d rules file(s) present; not parse-checked (codex is not on PATH)" % len(files))
        bad = []
        for f in files:
            r = _run(cmd + ["execpolicy", "check", "--rules", str(f), "git", "status"], timeout=60)
            if r.returncode != 0:
                bad.append("%s: %s" % (f.name, (r.stderr or r.stdout).strip()[:160]))
        if bad:
            return FAIL, ("Codex REJECTS the rules (an unparseable policy is no policy): %s" % "; ".join(bad))
        return OK, "%d rules file(s) parse under `codex execpolicy check`" % len(files)

    return [run_check("codex.hooks", "codex", _hooks, ctx),
            run_check("codex.config", "codex", _config, ctx),
            run_check("codex.trusted", "codex", _trusted, ctx),
            run_check("codex.reviewed", "codex", _reviewed, ctx),
            run_check("codex.rules", "codex", _rules, ctx)]


#: PreToolUse matchers the shipped settings.json registers (READ 2026-10-02). Each must be
#: present, and each wired script must exist.
CLAUDE_MATCHERS = ("Bash", "PowerShell", "Edit|Write|NotebookEdit")


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


@group
def check_claude(ctx: Ctx) -> list[Check]:
    if not ctx.targets.get("claude"):
        return [Check(i, "claude", NA, "the claude target is not installed here")
                for i in ("claude.wiring", "claude.enabled")]

    def _wiring(_):
        import re
        s = _read_json(ctx.root / ".claude" / "settings.json") or {}
        pre = (s.get("hooks") or {}).get("PreToolUse") or []
        have = {g.get("matcher"): g for g in pre}
        missing = [m for m in CLAUDE_MATCHERS if m not in have]
        absent = []
        n = 0
        for g in pre:
            for h in g.get("hooks") or []:
                cmd = (h.get("command") or "").replace("$CLAUDE_PROJECT_DIR", str(ctx.root))
                m = re.search(r'"([^"]+\.(?:sh|py))"', cmd)
                if m:
                    n += 1
                    if not Path(m.group(1)).is_file():
                        absent.append(Path(m.group(1)).name)
        if missing:
            return FAIL, "PreToolUse has no matcher for %s: those tools run UNGUARDED" % ", ".join(missing)
        if absent:
            return FAIL, "wired hook script(s) missing: %s -- the hook fails, and Claude runs the tool" % ", ".join(absent)
        return OK, "%d PreToolUse matcher(s), %d wired script(s) present" % (len(pre), n)

    def _enabled(_):
        """`disableAllHooks` (READ: code.claude.com hooks-guide) -- precedence local >
        project > user; managed settings are not read here, and say so."""
        home = Path(ctx.env["CLAUDE_CONFIG_DIR"]) if ctx.env.get("CLAUDE_CONFIG_DIR") else Path.home() / ".claude"
        layers = [("project local", ctx.root / ".claude" / "settings.local.json"),
                  ("project", ctx.root / ".claude" / "settings.json"),
                  ("user", home / "settings.json")]
        for name, p in layers:
            d = _read_json(p)
            if isinstance(d, dict) and "disableAllHooks" in d:
                if d["disableAllHooks"] is True:
                    return FAIL, "disableAllHooks is true in the %s settings (%s): EVERY hook is off" % (name, p)
                return OK, "disableAllHooks is %r in the %s settings (managed settings not read)" % (
                    d["disableAllHooks"], name)
        return OK, "no disableAllHooks in local/project/user settings (managed settings not read)"

    return [run_check("claude.wiring", "claude", _wiring, ctx),
            run_check("claude.enabled", "claude", _enabled, ctx)]


def _claude_delete_hook_wired(root: Path) -> bool:
    """Is a Claude delete guard actually WIRED at *root*: .claude/settings.json names
    protect-bash.sh and the hook file exists? A present .claude/ alone is not a guard."""
    root = Path(root)
    if not (root / ".claude" / "hooks" / "protect-bash.sh").is_file():
        return False
    try:
        return "protect-bash.sh" in (root / ".claude" / "settings.json").read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return False


#: The file bin/unpack-reference.sh writes LAST, after counting a complete unpack; x4refguard
#: apply refuses a tree without it (x4refguard.SENTINEL).
_REF_SENTINEL = ".unpacked-and-locked"


def _unpack_cmd(ctx: Ctx, ref=None) -> str:
    """The ABSOLUTE unpack command for this root's toolkit, with --toolkit (FX-B3: a hint the
    user can paste from any cwd, and one a foreign $X4_TOOLKIT does not refuse).

    FX-B4 (reviewer I): and `--reference` naming the tree the doctor JUDGED -- without it, an
    exported X4_REFERENCE differing from the config makes the command refuse (FX-B2). An
    inherited X4_CONFIG outside the toolkit is refused whatever the flags (FX-B4 #3), so the
    hint then says to clear it FIRST."""
    for base in (ctx.root, ctx.toolkit, HERE.parent):
        p = Path(base) / "bin" / "unpack-reference.sh" if base else None
        if p and p.is_file():
            tk = p.resolve().parent.parent
            cmd = 'bash "%s" --toolkit "%s"' % (p.resolve().as_posix(), tk.as_posix())
            if ref:
                cmd += ' --reference "%s"' % Path(ref).as_posix()
            return _foreign_config_first(ctx, tk) + cmd
    return "bash bin/unpack-reference.sh   (from the toolkit folder)"


def _foreign_config_first(ctx: Ctx, tk: Path) -> str:
    """'' unless the environment's X4_CONFIG names a missing file or one outside *tk* -- the
    unpack's own test (bin/unpack-reference.sh), which no flag lifts."""
    cfg = ctx.env.get("X4_CONFIG") or ""
    if not cfg:
        return ""
    f = _config_spelling(cfg)
    found = f.is_file() and not _config_stream(cfg)      # FX-G6 / K M2: a stream names none
    try:
        inside = found and os.path.normcase(str(f.resolve())).startswith(
            os.path.normcase(str(Path(tk).resolve())) + os.sep)
    except OSError:
        inside = False
    if inside:
        return ""
    why = "names a config outside this toolkit" if found else "names a file that does not exist"
    return ("first clear the inherited X4_CONFIG (%s; the unpack refuses it whatever the flags): "
            "`unset X4_CONFIG` in bash, `Remove-Item env:X4_CONFIG` in PowerShell -- then " % why)


def _moved_tree_evidence(ctx: Ctx, ref) -> str:
    """Why a MISSING reference tree looks MOVED rather than never unpacked, or ''. x4refguard
    reports both as "does not exist (not unpacked yet, or renamed or moved)": it cannot tell.
    Two local facts can (FX-B4, reviewer I): the unpack writes .claude/.reference-buildid only
    after a counted, finished unpack; and a finished unpack (its sentinel) at the toolkit's
    default <toolkit>/reference while the config names somewhere else."""
    seen = []
    for base in dict.fromkeys(Path(b) for b in (ctx.root, ctx.toolkit) if b):
        marker = base / ".claude" / ".reference-buildid"
        if marker.is_file():
            seen.append("%s exists (the unpack writes it only after a finished unpack)" % marker)
        default = base / "reference"
        if (default / _REF_SENTINEL).is_file():     # never *ref* itself: that one is missing
            seen.append("a finished unpack is at %s (it carries %s)" % (default, _REF_SENTINEL))
    return "; ".join(seen)


def _missing_tree_row(ctx: Ctx, ref, todo_text: str):
    """TODO with *todo_text* for a fresh install; UNKNOWN (exit 3) in x4refguard's words when
    something says the tree was MOVED -- re-unpacking ~27 GB into the old place is the wrong
    step for a tree that only moved."""
    moved = _moved_tree_evidence(ctx, ref)
    if not moved:
        return TODO, todo_text
    return UNKNOWN, ("%s does not exist -- not unpacked yet, or renamed or moved (x4refguard cannot "
                     "tell), and %s. If you moved or renamed it, set X4_REFERENCE in x4-paths.env "
                     "to where it is now; only if it is really gone, run: %s"
                     % (ref, moved, _unpack_cmd(ctx, ref)))


def _refguard_cmd(ctx: Ctx, m, action: str, ref) -> str:
    """The ABSOLUTE `x4refguard <action>` command, with --toolkit and --reference -- the same
    approach as `_unpack_cmd` (FX-Z, the FX-B4 lead). A bare `apply` REFUSES in the very shell
    the doctor judged whenever an exported X4_REFERENCE differs from the config (FX-B2) or an
    inherited X4_CONFIG names a config outside the toolkit (FX-B3); --reference naming the tree
    the doctor JUDGED lifts both for x4refguard, and --toolkit a foreign $X4_TOOLKIT."""
    f = getattr(m, "__file__", None)
    p = Path(f) if f else None
    if p is None or not p.is_file():
        p = next((Path(b) / "scripts" / "x4refguard.py" for b in (ctx.root, ctx.toolkit, HERE.parent)
                  if b and (Path(b) / "scripts" / "x4refguard.py").is_file()), None)
    if p is None:
        return "python scripts/x4refguard.py %s   (from the toolkit folder)" % action
    p = p.resolve()
    cmd = 'python "%s" %s --toolkit "%s"' % (p, action, p.parent.parent.as_posix())
    if ref:
        cmd += ' --reference "%s"' % Path(ref).as_posix()
    return cmd


def _x4refguard_module(ctx: Ctx):
    """Layer 2 (the OS-level deny-delete on reference/) is scripts/x4refguard.py -- NOT x4lock,
    which an earlier version of this check asked, so the row could never answer."""
    for base in (ctx.root, ctx.toolkit, HERE.parent):
        p = Path(base) / "scripts" / "x4refguard.py" if base else None
        if p and p.is_file():
            return _load("x4doctor_x4refguard", p)
    return None


import re as _re

#: An ASSIGNMENT of X4_GUARD in a config, in any shape the loaders accept (`KEY=`, `export
#: KEY=`, indented, spaced). A comment that merely names it is not one.
_GUARD_LINE = _re.compile(r"^\s*(?:export\s+)?X4_GUARD(?:_CHECK)?\s*=")


def config_files(ctx: Ctx) -> list[Path]:
    """Every path config a guard or tool of this root could read: X4_CONFIG, the one the
    guards report reading, and the 4.x and 3.x files of the root and of X4_TOOLKIT.
    Existing files only, deduplicated."""
    cands = []
    if ctx.env.get("X4_CONFIG"):
        if not _config_stream(ctx.env["X4_CONFIG"]):
            cands.append(_config_spelling(ctx.env["X4_CONFIG"]))
    vals = (guard_probe(ctx)[0] if guard_dirs(ctx) else None) or {}
    if vals.get("CFG"):
        cands.append(Path(vals["CFG"]))
    for base in (ctx.root, ctx.toolkit, vals.get("CFGTK") or None):
        if base:
            cands += [Path(base) / "x4-paths.env", Path(base) / ".claude" / "x4-paths.env"]
    out, seen = [], set()
    for c in cands:
        try:
            key = os.path.normcase(str(c.resolve()))
        except OSError:
            continue
        if key not in seen and c.is_file():
            seen.add(key)
            out.append(c)
    return out


def config_guard_lines_in(files) -> list[Path]:
    """Of *files*, those that ASSIGN X4_GUARD or X4_GUARD_CHECK (see _GUARD_LINE) -- exactly the
    lines the loaders' parser ignores with reason `guard` (`_paths.parse_env_report`; pinned
    line by line in test_x4doctor). A leading BOM is dropped, as the parser drops it."""
    hits = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.replace("\r", "").split("\n")
        if lines and lines[0].startswith("﻿"):
            lines[0] = lines[0][1:]
        if any(_GUARD_LINE.match(ln) for ln in lines):
            hits.append(f)
    return hits


def config_guard_lines(ctx: Ctx) -> list[Path]:
    """The config files of this root that ASSIGN X4_GUARD (see config_guard_lines_in)."""
    return config_guard_lines_in(config_files(ctx))


#: The x4lock state, asked of the REAL scripts/x4lock.py in a child process run the way an
#: agent runs it from the root: the root's environment and cwd. In-process it inherited the
#: DOCTOR's cwd and os.environ, resolved no game root, and reported one config file as the
#: whole manifest (MEASURED, v4.0.0 review R7-8 probe: '1 protected').
_PY_LOCK = r'''
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("x4lock_probe", sys.argv[1])
m = importlib.util.module_from_spec(spec)
sys.modules["x4lock_probe"] = m
spec.loader.exec_module(m)
if len(sys.argv) > 2 and sys.argv[2] and hasattr(getattr(m, "_paths", None), "use_toolkit"):
    m._paths.use_toolkit(sys.argv[2])                # the doctor's toolkit, explicitly (B2)
try:
    items, gone = m.manifest(), m.missing()
except m.Unresolvable as e:
    print(json.dumps({"error": "unresolvable: %s" % e}))
    raise SystemExit(0)
states = {}
for p in items:
    s = m.state(p)
    states[s] = states.get(s, 0) + 1
print(json.dumps({"states": states, "missing": len(gone)}))
'''


def _x4lock_answer(ctx: Ctx) -> tuple[dict | None, str]:
    for base in (ctx.root, ctx.toolkit, HERE.parent):
        p = Path(base) / "scripts" / "x4lock.py" if base else None
        if p and p.is_file():
            r = _run([sys.executable, "-c", _PY_LOCK, str(p), str(ctx.toolkit or "")],
                     env=dict(ctx.env), cwd=str(ctx.root), timeout=120)
            try:
                got = json.loads(r.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                return None, "x4lock printed no answer (rc %s): %s" % (r.returncode, r.stderr[-300:])
            if "error" in got:
                return None, "x4lock cannot build its manifest: %s" % got["error"]
            return got, str(p)
    return None, "no scripts/x4lock.py to ask"


@group
def check_common(ctx: Ctx) -> list[Check]:
    def _escape(_):
        # R1-F1 (v4.0.0 review, FX-S ruling 4): X4_GUARD is honoured ONLY from the launch
        # environment. The config is a file an agent can write, so a line there is ignored
        # by the guards -- and reported here as a FAIL, because someone tried. KEY names and
        # paths only, never a value (the same file carries X4_NEXUS_KEY).
        tried = config_guard_lines(ctx)
        if tried:
            return FAIL, ("an X4_GUARD line in the path config (%s) is ignored: the guards take "
                          "X4_GUARD only from the environment a session is launched from, never "
                          "from a file an agent can write. Someone tried to switch the guards "
                          "from that file; remove the line" % ", ".join(str(f) for f in tried))
        v = (ctx.env.get("X4_GUARD") or "").strip().lower()
        readers = 0
        for _t, hooks in guard_dirs(ctx):
            for f in hooks.glob("*.sh"):
                try:
                    readers += "X4_GUARD:" in f.read_text(encoding="utf-8", errors="replace") or \
                        '"$X4_GUARD"' in f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
        note = "" if readers else " (note: no guard here reads X4_GUARD yet)"
        if v == "off":
            return FAIL, "X4_GUARD=off: GUARDS OFF for every session launched from this environment" + note
        return OK, "X4_GUARD is %s%s" % (repr(v) if v else "unset", note)

    def _layer2(_):
        m = _x4refguard_module(ctx)
        fn = getattr(m, "report", None) if m is not None else None
        if fn is None:
            return UNKNOWN, ("the OS-level delete protection on reference/ cannot be queried here "
                             "(no scripts/x4refguard.py)")
        vals, why = guard_probe(ctx) if guard_dirs(ctx) else (None, "no guard copy")
        ref = (vals or {}).get("REFERENCE")
        if not ref:
            return UNKNOWN, "no reference root resolved: " + why
        try:
            r = fn(path=Path(ref))
        except Exception as exc:  # noqa: BLE001 -- a query that raises is a non-answer, never OK
            return UNKNOWN, "x4refguard could not report: %s" % exc
        st, detail = r.get("state"), r.get("detail") or ""
        # R6-15: every target that is not Claude -- OpenCode was left out, so an OpenCode
        # root read "the Claude hooks cover deletes" with no Claude target installed.
        hookless = [t for t in ("codex", "opencode", "generic") if ctx.targets.get(t)]
        if st == "protected":
            return OK, "reference/ carries the OS-level delete protection"
        apply_cmd = _refguard_cmd(ctx, m, "apply", ref)
        # FX-B3 (reviewer F M2/M3): the hint must be a command that WORKS in this case.
        if st == "unconfigured" and not Path(ref).is_dir():
            # A fresh install before its unpack step: a normal, KNOWN state -- the user's step
            # (TODO, exit 4), not a can't-tell (UNKNOWN, exit 3). x4refguard reports it as
            # "unconfigured" ("does not exist (not unpacked yet, or renamed or moved)").
            return _missing_tree_row(ctx, ref, (
                "YOUR STEP: reference/ is not unpacked yet (%s does not exist) -- run: "
                "%s (it unpacks, then applies the OS-level delete protection). If your "
                "unpacked tree is elsewhere, set X4_REFERENCE in x4-paths.env instead."
                % (ref, _unpack_cmd(ctx, ref))))
        if st in ("absent", "partial") and not (Path(ref) / _REF_SENTINEL).is_file():
            # `apply` REFUSES a tree without the sentinel (x4refguard resolve_target), so
            # "run: apply" was a hint that could only fail. The unpack writes the sentinel and
            # then applies -- but it refuses a tree that carries ANY protection, so a PARTIAL
            # one is lifted first.
            if st == "absent":
                return TODO, (
                    "YOUR STEP: reference/ has NO OS-level delete protection, and it is not a "
                    "finished unpack (no %s), so `apply` would refuse it -- run: %s (it writes "
                    "that file, then applies the protection). Only if it IS a complete unpack you "
                    "made yourself: %s prints how to mark it."
                    % (_REF_SENTINEL, _unpack_cmd(ctx, ref), apply_cmd))
            return FAIL, (
                "reference/ has only PARTIAL OS-level delete protection and is not a finished "
                "unpack (no %s): neither `apply` nor the unpack will act on it as it is -- run: "
                "%s, then %s" % (_REF_SENTINEL, _refguard_cmd(ctx, m, "remove", ref),
                                 _unpack_cmd(ctx, ref)))
        if st in ("absent", "partial") and not hookless:
            # FX-B2 (delta review): a Claude-only root read OK here whatever the state, saying
            # "the Claude hooks cover deletes" -- true only if they are WIRED. absent is the
            # user's pending step (TODO); partial is broken (FAIL). Never OK.
            wired = _claude_delete_hook_wired(ctx.root)
            cover = (" Meanwhile the Claude hooks (wired in .claude/settings.json) block deletes "
                     "made THROUGH Claude only." if wired else
                     " No Claude delete hook is wired here (.claude/settings.json), so nothing "
                     "else covers deletes.")
            return (TODO if st == "absent" else FAIL), (
                "%sreference/ has %s OS-level delete protection (x4refguard: %s) -- run: %s "
                "(it shows the folder and a count, then asks).%s"
                % ("YOUR STEP: " if st == "absent" else "",
                   "NO" if st == "absent" else "only PARTIAL", st, apply_cmd, cover))
        if st in ("absent", "partial"):
            if hookless:
                names = {"codex": "Codex", "opencode": "OpenCode", "generic": "generic"}
                # C2: ABSENT on a fresh install is the user's pending step (the installers never
                # apply it); PARTIAL is a broken state and stays a FAIL.
                return (TODO if st == "absent" else FAIL), (
                    "%sreference/ has %s OS-level delete protection, and a %s agent here has "
                    "no Claude hook-level delete guard to fall back on -- run: "
                    "%s (it shows the folder and a count, then "
                    "asks; --yes answers for you, e.g. when an agent runs it after you agreed)"
                    % ("YOUR STEP: " if st == "absent" else "",
                       "NO" if st == "absent" else "only PARTIAL",
                       " / ".join(names[t] for t in hookless), apply_cmd))
            return OK, ("no OS-level delete protection on reference/ (%s); the Claude hooks cover "
                        "deletes" % st)
        return UNKNOWN, "the OS-level protection state of reference/ is %r: %s" % (st, detail)

    def _lock(_):
        got, why = _x4lock_answer(ctx)
        if got is None:
            return UNKNOWN, why
        counts, gone = got.get("states") or {}, int(got.get("missing") or 0)
        detail = "%d protected: %s; %d missing" % (sum(counts.values()), ", ".join(
            "%d %s" % (v, k) for k, v in sorted(counts.items())) or "none", gone)
        # ABSOLUTE (FX-B2): `scripts/x4lock.py` is relative to a cwd the reader may not share.
        lk = 'python "%s"' % Path(why).resolve()
        if gone:
            return UNKNOWN, detail + (" -- a protected file the manifest expects is ABSENT "
                                      "(deleted, renamed, or never installed): %s status" % lk)
        if counts.get("unlocked"):
            # R4-2: locking is the user's CHOICE, so this is an answer, never UNKNOWN.
            return OK, detail + " (informational: locking is your choice -- %s lock)" % lk
        return OK, detail

    return [run_check("guard.escape", "all", _escape, ctx),
            run_check("layer2.reference", "all", _layer2, ctx),
            run_check("x4lock", "all", _lock, ctx)]


# ----------------------------------------------------------------- Task 7: parity

#: The first line of every generated instruction file carries this (gen-agent-trees.py's
#: BANNER_CLAUDE_MD / BANNER_MD both begin with it).
GENERATED_MARK = "<!-- GENERATED"
INSTRUCTION_FILE = {"claude": "CLAUDE.md", "codex": "AGENTS.md", "generic": "AGENTS.md",
                    "opencode": "AGENTS.md"}
#: OpenCode reads AGENTS.md plus its own addendum, named under `instructions` in the rendered
#: .opencode/opencode.jsonc (READ opencode.ai/docs/rules).
OPENCODE_ADDENDUM = Path(".opencode") / "X4-OPENCODE.md"


def _parity_module(ctx: Ctx):
    """deploy_parity.py -- from the source toolkit if its comparer knows TargetSpecs, else
    the one this doctor ships with. One comparer for the gate and the doctor, so they
    cannot disagree; the SOURCE tree compared is X4_TOOLKIT's either way."""
    for i, base in enumerate((ctx.toolkit, HERE.parent)):
        p = Path(base) / "tools" / "x4validate" / "gates" / "deploy_parity.py" if base else None
        if p and p.is_file():
            try:
                m = _load("x4doctor_deploy_parity_%d" % i, p)
            except Exception:  # noqa: BLE001 -- an unloadable gate is tried past, not trusted
                continue
            if _parity_targets(m):
                return m
    return None


def _parity_targets(mod) -> dict:
    return dict(getattr(mod, "TARGETS", {}) or {})


def _is_installed_toolkit(root: Path) -> bool:
    """`root` holds the toolkit RUNTIME the installers copy (scripts/ + tools/x4validate)."""
    return ((Path(root) / "scripts" / "x4doctor.py").is_file()
            and (Path(root) / "tools" / "x4validate" / "x4validate" / "_paths.py").is_file())


def _venv_python(tk: Path) -> Path | None:
    for rel in (("Scripts", "python.exe"), ("bin", "python")):
        p = tk / "tools" / "x4validate" / ".venv" / Path(*rel)
        if p.is_file():
            return p
    return None


def _has_banner(p: Path) -> bool:
    try:
        with open(p, "rb") as fh:
            return GENERATED_MARK.encode() in fh.read(2048)
    except OSError:
        return False


@group
def check_parity(ctx: Ctx) -> list[Check]:
    rows: list[Check] = []
    mod_box: dict = {}

    def mod():
        if "m" not in mod_box:
            mod_box["m"] = _parity_module(ctx)
        return mod_box["m"]

    for target in ("claude", "codex"):
        cid = "parity." + target
        if not ctx.targets.get(target):
            rows.append(Check(cid, target, NA, "the %s target is not installed here" % target))
            continue

        def _one(_, target=target):
            # R4-2 (v4.0.0 review). Parity is a DEPLOY-workflow check: deploy-claude-dir.py
            # copies a source checkout's .claude/ into a root. It is N/A where no deploy
            # relationship exists (an INSTALLED toolkit: runtime-only, decision #9), UNKNOWN
            # only where one exists and cannot be checked. Both used to be UNKNOWN, so
            # x4doctor could never exit 0 on any installed root.
            if ctx.toolkit is None and not _is_installed_toolkit(ctx.root):
                return UNKNOWN, ("X4_TOOLKIT is unset, so there is no SOURCE to compare against "
                                 "(never this doctor's own folder: an installed copy would compare "
                                 "to itself)")
            src = Path(ctx.toolkit) if ctx.toolkit is not None else ctx.root
            if src.resolve() == ctx.root.resolve():
                gen = src / "tools" / "x4validate" / "scripts" / "gen-agent-trees.py"
                py = _venv_python(src)
                layout = _load("x4doctor_toolkit_layout",
                               Path(__file__).resolve().parent / "toolkit_layout.py")
                # FX-H2 / F269: a source checkout is a root .git whose HEAD tracks agent/; the
                # release zip installed in place HAS agent/ and the generator, and is an install.
                # M-a (v4.0.0 delta review): a .git git could not answer for is UNKNOWN, never
                # N/A -- calling it an install would hide a checkout parity never compared.
                state, why = layout.checkout_status(src)
                if state == "unknown" and gen.is_file():
                    return UNKNOWN, ("cannot tell whether this root is a source checkout or an "
                                     "install: %s. Parity runs the generator only in a checkout"
                                     % why)
                if state == "unknown":
                    # F7 (v4.0.0 delta review): git could not answer AND the generator is
                    # absent. Parity cannot run either way (N/A), but nothing measured that
                    # this is an install -- so do not say it is.
                    return NA, ("whether this root is a source checkout or an installed toolkit "
                                "could not be determined: %s. Parity cannot run here either way: "
                                "it needs the generator (tools/x4validate/scripts/"
                                "gen-agent-trees.py), which is absent. The installed guards are "
                                "checked by guards.selftest and %s.*" % (why, target))
                if state != "checkout" or not gen.is_file():
                    return NA, ("this root IS an installed toolkit (not a source checkout, i.e. no .git tracking agent/: runtime-only), "
                                "so no deploy relationship exists here -- parity checks a tree "
                                "deploy-claude-dir.py copied from a source checkout. The installed "
                                "guards are checked by guards.selftest and %s.*" % target)
                if py is None:
                    return UNKNOWN, ("this root is the toolkit; its generator needs the venv "
                                     "(tools/x4validate/.venv), which is absent -- run setup.sh")
                r = _run([str(py), str(gen), "--check"], cwd=str(gen.parent.parent), timeout=300)
                if r.returncode == 0:
                    return OK, "the generated trees match agent/ (gen-agent-trees.py --check)"
                return FAIL, ("gen-agent-trees.py --check rc %s: %s"
                              % (r.returncode, (r.stdout + r.stderr).strip()[-400:]))
            m = mod()
            if m is None:
                return UNKNOWN, "no gates/deploy_parity.py to compare with"
            spec = _parity_targets(m).get(target)
            if spec is None:
                return NA, ("no deploy workflow ships .%s/: deploy_parity defines only the %s "
                            "deployment(s) (deploy-claude-dir.py --target deploys only those), and .%s/ reaches a "
                            "root only through the installer -- whose output %s.* checks"
                            % (target, ", ".join(sorted(_parity_targets(m))) or "no", target, target))
            a, b = src / spec.root_rel, ctx.root / spec.root_rel
            rws = m.compare_trees(a, b, spec)
            if not rws:
                return UNKNOWN, "nothing to compare: both %s populations are empty" % spec.root_rel
            off = [r for r in rws if not r.at_parity]
            if off:
                shown = "; ".join(m.describe(r) for r in off[:10])
                more = " ... and %d more NOT LISTED" % (len(off) - 10) if len(off) > 10 else ""
                return FAIL, "%d of %d file(s) differ from %s: %s%s" % (len(off), len(rws), src, shown, more)
            rew = sum(1 for r in rws if r.state != m.IDENTICAL)
            return OK, "%d file(s) at parity with %s (%d via the installer rewrite)" % (len(rws), src, rew)
        rows.append(run_check(cid, target, _one, ctx))

    # The instruction files are in no deploy population (READ): reported here instead.
    for target in ("claude", "codex", "generic", "opencode"):
        cid = "instructions." + target
        if not ctx.targets.get(target):
            rows.append(Check(cid, target, NA, "the %s target is not installed here" % target))
            continue

        def _instr(_, target=target):
            name = INSTRUCTION_FILE[target]
            p = ctx.root / name
            if not p.is_file():
                return FAIL, ("no %s here: the %s target runs without the toolkit's instructions"
                              % (name, target))
            if target == "opencode" and not _has_banner(ctx.root / OPENCODE_ADDENDUM):
                return FAIL, ("no generated %s: OpenCode runs without the toolkit's OpenCode rules "
                              "(how its guards work, what to do when the plugin did not load)"
                              % OPENCODE_ADDENDUM.as_posix())
            if _has_banner(p):
                return OK, "%s is the toolkit's generated file" % name
            if target == "claude":
                return OK, "%s has no GENERATED banner (hand-written or pre-4.0)" % name
            return FAIL, ("%s has no GENERATED banner: %s reads THIS file, and it is not the "
                          "toolkit's -- re-run the installer, which keeps yours as AGENTS.pre-4.0.md"
                          % (name, {"codex": "Codex", "opencode": "OpenCode"}.get(target, "a generic agent")))
        rows.append(run_check(cid, target, _instr, ctx))

    agents = ctx.root / "AGENTS.md"
    if agents.is_file() and not (ctx.targets.get("codex") or ctx.targets.get("generic")
                                 or ctx.targets.get("opencode")):
        kind = "the toolkit's generated file" if _has_banner(agents) else "a hand-written file"
        rows.append(Check("instructions.agents_md", "all", OK,
                          "NOTE: %s is %s; no Codex/generic target is installed, but any Codex "
                          "session started here reads it" % (agents.name, kind)))
    return rows


# ----------------------------------------------------------------- OpenCode (lane L)
#
# BEST EFFORT, from OpenCode v1.18.34's docs and source (READ), never run here. What these
# rows can answer: are the two layers IN PLACE -- the plugin with the adapter and guard copy
# it calls, and the deny rules rendered for the roots the guards see now. What they cannot:
# that OpenCode loaded either one (a plugin that fails to load is skipped with only a log
# line, READ plugin/index.ts). That is the `X4 GUARDS LIVE` line inside a session.

OPENCODE_NEEDS = ("plugins/x4guard.js", "hooks/opencode_adapter.py", "hooks/codex_adapter.py",
                  "hooks/patch_paths.py", "hooks/x4guard.py", "hooks/_x4-env.sh",
                  "hooks/opencode_config.py")


def _jsonc_loads(text: str):
    """JSON with // and /* */ comments and trailing commas (OpenCode accepts JSONC, READ)."""
    import re
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            k = text.find("*/", i + 2)
            i = n if k < 0 else k + 2
        else:
            out.append(c)
            i += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))


def opencode_user_configs(ctx: Ctx) -> list[Path]:
    """The OpenCode config files merged BEFORE ours (READ config.ts): global
    (<xdg config>/opencode/{config.json,opencode.json,opencode.jsonc}, xdg = XDG_CONFIG_HOME
    else ~/.config), OPENCODE_CONFIG, the project's opencode.json(c), .opencode/opencode.json."""
    xdg = Path(ctx.env["XDG_CONFIG_HOME"]) if ctx.env.get("XDG_CONFIG_HOME") else Path.home() / ".config"
    cands = [xdg / "opencode" / n for n in ("config.json", "opencode.json", "opencode.jsonc")]
    if ctx.env.get("OPENCODE_CONFIG"):
        cands.append(Path(ctx.env["OPENCODE_CONFIG"]))
    cands += [ctx.root / "opencode.json", ctx.root / "opencode.jsonc", ctx.root / ".opencode" / "opencode.json"]
    return [p for p in cands if p.is_file()]


@group
def check_opencode(ctx: Ctx) -> list[Check]:
    ids = ("opencode.plugin", "opencode.config", "opencode.userconfig", "opencode.cli")
    if not ctx.targets.get("opencode"):
        return [Check(i, "opencode", NA, "the opencode target is not installed here") for i in ids]
    oc = ctx.root / ".opencode"

    def _plugin(_):
        gone = [n for n in OPENCODE_NEEDS if not (oc / n).is_file()]
        if gone:
            return FAIL, ("%s missing: the plugin would THROW 'X4 GUARD INERT' on every write (or not "
                          "load at all). Re-run the installer" % ", ".join(".opencode/" + g for g in gone))
        return OK, ("plugin, adapter and guard copy in place (.opencode/plugins/x4guard.js). Whether "
                    "OpenCode LOADED it shows only in a session: the 'X4 GUARDS LIVE' line")

    def _config(_):
        cfg = oc / "opencode.jsonc"
        fix = "python .opencode/hooks/opencode_config.py write --root \"%s\"" % ctx.root
        if not cfg.is_file():
            return FAIL, "no .opencode/opencode.jsonc: the deny-rule layer is ABSENT. Run: " + fix
        r = _run([sys.executable, str(oc / "hooks" / "opencode_config.py"), "check", "--root", str(ctx.root)],
                 env=ctx.env, cwd=str(ctx.root), timeout=120)
        if r.returncode == 0:
            return OK, "the deny rules match the roots the guards resolve now"
        if r.returncode == 1:
            # C5 (install red-team 2026-10-04): "stale" alone explained nothing. Carry the
            # renderer's own reason, and name the commonest cause when it applies here: the
            # guards resolve their roots through $X4_TOOLKIT's config, so an X4_TOOLKIT naming
            # another toolkit makes a fresh file look stale (and protects the wrong tree).
            why = (r.stderr or r.stdout).strip().splitlines()
            why = why[-1][:400] if why else ""
            tk = ctx.env.get("X4_TOOLKIT")
            hint = ""
            if tk and not _same_dir(tk, ctx.root):
                hint = (" NOTE: the guards here read the config of X4_TOOLKIT=%s, not this root's "
                        "-- that alone makes the rules differ; point X4_TOOLKIT here (new shells) "
                        "if this is the toolkit you use." % tk)
            return FAIL, ("the deny rules on disk differ from what the guards' current roots render: "
                          "%s.%s Run: %s" % (why or "stale", hint, fix))
        return UNKNOWN, "the deny rules cannot be rendered here: " + (r.stderr or r.stdout).strip()[-300:]

    def _userconfig(_):
        hits, bad = [], []
        for p in opencode_user_configs(ctx):
            try:
                d = _jsonc_loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError) as e:
                bad.append("%s (%s)" % (p, type(e).__name__))
                continue
            perm = d.get("permission") if isinstance(d, dict) else None
            if isinstance(perm, str):
                hits.append('%s: "permission": "%s"' % (p, perm))
            elif isinstance(perm, dict):
                hits += ['%s: "permission.%s": "%s"' % (p, k, perm[k]) for k in ("edit", "bash")
                         if isinstance(perm.get(k), str)]
        if hits:
            return UNKNOWN, ("NOT a guard failure: your own setting is REPLACED by the toolkit's deny "
                             "object when OpenCode merges configs (READ: a string is replaced by an "
                             "object), so it silently stops applying -- %s. Write it as "
                             '{"*": "<value>"} instead' % "; ".join(hits))
        if bad:
            return UNKNOWN, "could not read: " + "; ".join(bad)
        return OK, "no OpenCode config of yours sets permission/edit/bash as a plain string"

    def _cli(_):
        import shutil
        exe = shutil.which("opencode", path=ctx.env.get("PATH"))
        ver = ""
        if exe:
            try:
                r = _run([exe, "--version"], env=ctx.env, timeout=30)
                ver = (r.stdout or r.stderr).strip().splitlines()[0][:80] if (r.stdout or r.stderr).strip() else ""
            except Exception:  # noqa: BLE001 -- informational row
                ver = "(--version did not answer)"
        where = ("opencode on PATH: %s %s" % (exe, ver)).strip() if exe else "opencode is not on PATH"
        return OK, ("NOTE: %s. OpenCode support is BEST EFFORT (from docs, not measured), CLI only: "
                    "the OpenCode desktop app is NOT supported (its plugin hooks never fire, "
                    "anomalyco/opencode#38604)" % where)

    return [run_check("opencode.plugin", "opencode", _plugin, ctx),
            run_check("opencode.config", "opencode", _config, ctx),
            run_check("opencode.userconfig", "opencode", _userconfig, ctx),
            run_check("opencode.cli", "opencode", _cli, ctx)]


def _verdict(code: int) -> str:
    return {0: "OK -- every applicable check passed",
            1: "FAIL -- at least one check failed: the guards here are not fully live or not current (see FAIL rows)",
            3: "UNKNOWN -- nothing failed, but some checks could not answer (see UNKNOWN rows)",
            4: "YOUR STEPS PENDING -- installed correctly; finish the TODO rows (things only you may do)",
            2: "COULD NOT RUN -- nothing was checked; this is not a pass"}[code]


def render_text(ctx: Ctx, rows: list[Check], code: int, elapsed: float) -> str:
    present = [t for t in TARGETS if ctx.targets.get(t)]
    shown, suffix = present, ""
    if ctx.targets.get("codex") and ctx.targets.get("generic"):
        # A Codex install ships .agents/skills, which IS the generic payload: listing "generic"
        # beside it read as a third install (cosmetic, install red-team 2026-10-04).
        shown = [t for t in present if t != "generic"]
        suffix = "   (a generic agent reads codex's AGENTS.md + .agents/skills too)"
    out = ["x4doctor: %s" % _verdict(code),
           "  root:    %s" % ctx.root,
           "  toolkit: %s" % (ctx.toolkit or "(none)"),
           "  targets: %s" % ((", ".join(shown) + suffix) or "NONE (no .claude/settings.json, .codex/hooks, .agents/skills or .opencode/plugins/x4guard.js here)"),
           "  checked: %d row(s) in %.1fs -- %s" % (
               len(rows), elapsed,
               ", ".join("%d %s" % (sum(r.status == s for r in rows), s) for s in STATUSES))]
    if ctx.toolkit_note:
        out.append("  note:    " + ctx.toolkit_note)
    if ctx.targets.get("generic"):
        out.append("  note:    a GENERIC agent runs no hooks here (by design, spec D13): nothing enforces "
                   "the guards for it; only x4lock and the OS-level reference protection apply")
    out.append("")
    w = max([len(r.id) for r in rows] + [10])
    for r in rows:
        out.append("  %-7s %-8s %-*s %s" % (r.status, r.target, w, r.id, r.detail))
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    if sys.version_info < (3, 10):
        print("x4doctor: COULD NOT RUN -- needs Python >= 3.10 (this is %d.%d)" % sys.version_info[:2])
        return 2
    ap = argparse.ArgumentParser(
        prog="x4doctor", description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="rows: OK, FAIL, UNKNOWN, TODO (your step: trust the folder in Codex, approve its\n"
               "hooks, apply the reference/ OS protection), N/A (target not installed here)\n"
               "exit: 0 all OK | 1 any FAIL | 3 no FAIL, some UNKNOWN | 4 only TODO rows pending\n"
               "      | 2 could not run (FAIL > UNKNOWN > TODO: a TODO never hides a FAIL)")
    ap.add_argument("--root", help="the folder an agent runs in (default: nearest ancestor holding a target)")
    ap.add_argument("--agent", help="report only this target: " + ", ".join(TARGETS))
    ap.add_argument("--json", action="store_true", help="one JSON document on stdout")
    ap.add_argument("--toolkit", metavar="DIR",
                    help="the toolkit to compare and ask (default: the one this doctor lives in)")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    if a.agent and a.agent not in TARGETS:
        print("x4doctor: COULD NOT RUN -- unknown --agent %r (one of: %s)" % (a.agent, ", ".join(TARGETS)))
        return 2
    root = Path(a.root).resolve() if a.root else default_root(Path.cwd())
    ctx = Ctx(root=root, toolkit=Path(a.toolkit).resolve() if a.toolkit else AUTO)
    t0 = time.monotonic()
    rows = collect(ctx, a.agent) if any(ctx.targets.values()) else []
    elapsed = time.monotonic() - t0
    code = exit_code(rows)
    if a.json:
        print(json.dumps({"v": 1, "root": str(ctx.root),
                          "targets": {t: ("present" if ctx.targets.get(t) else "absent") for t in TARGETS},
                          "checks": [asdict(r) for r in rows],
                          "summary": _verdict(code), "exit": code,
                          "toolkit": str(ctx.toolkit) if ctx.toolkit else None,
                          "toolkit_note": ctx.toolkit_note,
                          "elapsed_s": round(elapsed, 2)}, indent=1))
    else:
        sys.stdout.write(render_text(ctx, rows, code, elapsed))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
