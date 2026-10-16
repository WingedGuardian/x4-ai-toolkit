"""Replay every historical Bash command through protect-bash.sh, on a STABLE hook.

WHY. Every guard rule that was ever exercised turned out to be wrong the first time
it ran for real (F79: none had ever executed; then six false positives in a day).
Replaying 10,000+ commands that all RAN FINE at the time is the cheap way to find
the rest -- any deny or advise is the guard's own verdict on work that needed doing.

CONTROLS FIRST. An earlier attempt scored a perfect 300 of 300 allow -- vacuous,
because the replay environment lacked X4_TOOLKIT and no path rule could fire. Then
`subprocess.run(["bash", ...])` resolved to WSL's bash, whose empty stdout read as
"allow" for every command. A harness that cannot produce a deny cannot measure a
false-positive rate; this one refuses to report unless a known-bad command comes
back `deny` AND a harmless one comes back `allow`.

STABLE INSTRUMENT (2026-08-30). The first F82 baseline was measured while the live
hook was REDEPLOYED mid-run (launched 22:28, hook deployed 00:08:29, finished
00:15:36). Bash re-reads the script on every spawn, so the tail of that run used a
different rule set from the head, and its per-rule table was not reproducible. This
version hashes the hook files (and the optional x4-paths.env files they source) before and
after, re-resolves the paths at the end, and VOIDS the run if anything moved.

COUNTS, NOT EXAMPLES. The first artifact stored 25 examples per rule and no
counts, so "1,450" and "25" were indistinguishable in the file. And it overwrote
its own baseline on every run, so nothing could ever be diffed.

NO ALLOW FROM A FAILURE (code review 2026-08-30). A per-command hook failure --
MEASURED: `JQ=no_such_binary` gives rc 0, EMPTY stdout and stderr noise, because
protect-bash.sh exits 0 on an empty $COMMAND -- used to be read as "allow". Now a
non-zero rc, or an empty verdict beside stderr noise, is a refusal.

Run:  uv run python gates/hook_false_positives.py [--record] [--limit=N]
                                                  [--workers=N] [--prove-parallel=N]
                                                  [--out=PATH]
      --out writes the artifact there and skips the compare.
      --record with --limit is refused unless --out is given: a partial baseline
      would make every later compare a clean "no drift" over N commands.
      The transcript directory comes from toolkit_usage.transcript_dir
      (honours --transcripts DIR / X4_TRANSCRIPTS like every other gate).
Exit: 0 no drift (or recorded) / 1 drift on shared commands / 2 refused
      (unconfigured, controls failed, hook changed under the run, empty corpus,
      no or partial baseline) / 3 unexpected exception (never 1: 1 means DRIFT).

Compare mode diffs per RULE over the INTERSECTION of commands present in both
runs, so a growing corpus never reads as a rule change (CLAUDE.md, concurrent
sessions rule 5: design a measurement to survive drift). A rule SWAP (two commands
trading rules, delta 0 each) is reported as moved pairs, not hidden by the sums.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / ".hook-false-positive-baseline.json"
# Git Bash, found by name on PATH. NOT "explicit": on this machine WSL's
# C:\Windows\System32\bash.exe is also on PATH (second), and `bash` alone
# resolved to it via CreateProcess. The start-of-run controls are what guard
# this -- WSL's bash cannot run a Windows-path script and fails the known-bad test.
BASH = shutil.which("bash.exe") or "bash"
# The only variables that may ever be written into the artifact. An allow-list,
# because the same resolver environment carries X4_NEXUS_KEY. (Corpus TEXT is not
# covered by this list -- examples hold the first 120 chars of historical
# commands, which is why the artifact is gitignored.)
PATH_VARS = ("X4_GAME", "X4_PROFILE", "X4_REFERENCE", "X4_MODS", "X4_TOOLKIT",
             "X4_DOCUMENTS", "X4_SAVES")
# Every file whose bytes decide a verdict. Hashed before and after the run.
# _x4-env.sh ALSO sources $X4_TOOLKIT/x4-paths.env, else the 3.x
# $X4_TOOLKIT/.claude/x4-paths.env (Plan 3 lane I). BOTH are hashed, each with an
# "absent" sentinel, so either appearing, vanishing or changing voids the run.
# The shell delegates filesystem facts to hook_facts.py, including nested
# PowerShell commands translated by ps_translate.ps1. A change in either can
# change a Bash verdict without changing either shell file.
HOOK_FILES = ("protect-bash.sh", "_x4-env.sh", "hook_facts.py", "ps_translate.ps1", "save_policy.py", "x4guard.py")
CORE_FILES = ("__init__.py", "approval.py", "contracts.py", "dispatch.py", "durable.py", "locking.py",
              "preservation.py", "profiles.py", "telemetry.py", "workspace.py")
EXAMPLE_CAP = 25


#: Seconds a single hook invocation may take before it is a NON-ANSWER. The guard
#: decides an ordinary command in about 0.25s (MEASURED 2026-09-06), so this is
#: ~120x headroom and can only fire on something genuinely wedged.
HOOK_TIMEOUT = 30


class HookOutputError(RuntimeError):
    """The hook did not produce a verdict. That is a broken instrument, never
    permission -- the F79 shape is 'nothing read == fine'."""


class UnstableInstrument(RuntimeError):
    """The hook files or resolved paths changed while the run was in progress."""


class NoSuchRule(RuntimeError):
    """--rule matched no recorded rule. A typo would otherwise replay only the
    controls and report a clean zero -- a narrowing step that reports success."""


class PartialBaseline(RuntimeError):
    """The stored baseline cannot support a compare (old format, or --limit)."""


# ---- pure functions (tested in tests/test_hook_false_positives_gate.py) --------

def parse_verdict(stdout: str) -> tuple[str, str]:
    """(verdict, first line of the reason) from the hook's stdout."""
    if not stdout.strip():
        return "allow", ""
    try:
        h = json.loads(stdout)["hookSpecificOutput"]
        if not isinstance(h, dict):
            raise TypeError("hookSpecificOutput is not an object")
    except (ValueError, KeyError, TypeError) as e:
        raise HookOutputError(stdout[:200]) from e
    v = h.get("permissionDecision") or ("advise" if h.get("additionalContext") else "allow")
    reason = h.get("permissionDecisionReason") or h.get("additionalContext") or ""
    return v, reason.split("\n")[0]


def interpret(returncode: int, stdout: str, stderr: str) -> tuple[str, str, bool]:
    """(verdict, reason, stderr_noise). A non-zero exit, or an EMPTY verdict beside
    stderr output, is a hook that did not evaluate -- refused, never 'allow'. A
    verdict beside stderr output (a benign NUL-byte warning, say) counts, flagged."""
    if returncode != 0:
        raise HookOutputError(f"rc={returncode}: {stderr.strip()[:200]}")
    if not stdout.strip() and stderr.strip():
        raise HookOutputError(f"empty verdict with stderr: {stderr.strip()[:200]}")
    v, reason = parse_verdict(stdout)
    return v, reason, bool(stderr.strip())


_CMD_TAIL = re.compile(r"(confirm:).*$", re.IGNORECASE)
_TIMEOUT_VALUE = re.compile(r"you passed \d+ms")


def rule_key(first_line: str) -> str:
    """One key per RULE. Several rules embed `$COMMAND` after 'confirm:', which
    made every hit its own key in the first artifact; the timeout-cap rule embeds
    the value it saw, which split it into a key per value the first time the field
    reached the hook. Never truncated."""
    return _TIMEOUT_VALUE.sub("you passed Nms", _CMD_TAIL.sub(r"\1", first_line)).rstrip()


def resolved_paths(text: str) -> dict[str, str]:
    """`NAME=value` lines -> dict, ALLOW-LISTED. Anything else is dropped."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        k, sep, v = line.partition("=")
        if sep and k in PATH_VARS:
            out[k] = v.strip()
    return out


def assert_stable(before: dict[str, str], after: dict[str, str]) -> None:
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if changed:
        raise UnstableInstrument("changed during the run: " + ", ".join(changed))


_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD", re.IGNORECASE)


def secret_scan(blob: str, env: dict) -> list[str]:
    """Names of secret-shaped env vars whose VALUE appears in the blob. The
    allow-list covers env values written on purpose; the examples carry the first
    120 chars of historical COMMANDS, which no allow-list can vet. Values shorter
    than 8 chars are ignored -- they would match by accident and the scan would
    train you to ignore it."""
    return sorted(k for k, v in env.items()
                  if _SECRET_NAME.search(k) and v and len(v) >= 8 and v in blob)


def hook_same(base_sha: dict, now_sha: dict) -> tuple[bool, list[str]]:
    """Compare hook hashes on the keys BOTH sides have; return the keys only one
    side has so they can be named. MEASURED 2026-08-30: a 2-key baseline against
    a 3-key current dict read as 'hook DIFFERENT' with both real files identical."""
    common = set(base_sha) & set(now_sha)
    same = all(base_sha[k] == now_sha[k] for k in common)
    return same, sorted((set(base_sha) | set(now_sha)) - common)


def check_baseline(base: dict) -> None:
    """A baseline must be the NEW format and COMPLETE, or compare is meaningless."""
    if "verdicts" not in base or "replayed" not in base or "distinct_total" not in base:
        raise PartialBaseline("old-format baseline (no per-command verdicts); re-record")
    if base["replayed"] < base["distinct_total"]:
        raise PartialBaseline(f"baseline covers {base['replayed']} of {base['distinct_total']} "
                              "commands (recorded with --limit); re-record without it")


def select_subset(baseline: dict, prefix: str, n_control: int, seed: int
                  ) -> tuple[list[str], list[str]]:
    """(items the named rule caught, allow items to use as a control).

    Only a command a rule actually caught can change when that rule's predicate
    changes -- so a per-rule fix does not need the full corpus. The control exists
    because a fix that turns an ALLOW into anything is a regression, not a delta.
    """
    import random
    v = baseline["verdicts"]
    targets = [h for h, (verdict, rule) in v.items()
               if verdict != "allow" and rule.startswith(prefix)]
    if not targets:
        raise NoSuchRule(f"no recorded rule starts with {prefix!r}")
    allows = sorted(h for h, (verdict, _) in v.items() if verdict == "allow")
    rng = random.Random(seed)
    controls = sorted(rng.sample(allows, min(n_control, len(allows)))) if n_control else []
    return targets, controls


def summarize(verdicts: dict[str, list], example_cap: int = EXAMPLE_CAP,
              text: dict[str, str] | None = None) -> dict:
    """Uncapped counts per verdict and per rule, plus capped examples."""
    counts: Counter = Counter()
    by_rule: Counter = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    for h, (v, rule) in verdicts.items():
        counts[v] += 1
        if v != "allow":
            by_rule[rule] += 1
            if len(examples[rule]) < example_cap:
                examples[rule].append((text or {}).get(h, h))
    return {"counts": dict(counts), "counts_by_rule": dict(by_rule),
            "examples_by_rule": dict(examples)}


def compare(baseline: dict, current: dict) -> dict:
    """Per-rule drift over the commands present in BOTH runs, plus every
    (old rule -> new rule) pair so a swap cannot hide behind equal counts."""
    bv, cv = baseline["verdicts"], current["verdicts"]
    shared = set(bv) & set(cv)
    by_rule: dict[str, dict[str, int]] = defaultdict(lambda: {"baseline": 0, "current": 0})
    moved: Counter = Counter()
    changed = 0
    for h in shared:
        if list(bv[h]) == list(cv[h]):
            continue
        changed += 1
        old = bv[h][1] if bv[h][0] != "allow" else ""
        new = cv[h][1] if cv[h][0] != "allow" else ""
        if old:
            by_rule[old]["baseline"] += 1
        if new:
            by_rule[new]["current"] += 1
        moved[(old, new)] += 1
    out = {k: {**d, "delta": d["current"] - d["baseline"]} for k, d in by_rule.items()}
    return {"shared": len(shared), "only_in_baseline": len(set(bv) - shared),
            "only_in_current": len(set(cv) - shared), "changed": changed,
            "by_rule": out, "moved": dict(moved), "drift": changed > 0}


def extract_commands(records) -> list[dict]:
    """Every Bash tool_use as {command, run_in_background, timeout}, distinct on all
    three, first-seen order, values passed through RAW.

    ALL THREE, because the hook reads all three: the LONG JOB rule consults
    run_in_background and the timeout-cap rule consults timeout. The first version
    sent only the command, so every historically-backgrounded long job replayed as
    FOREGROUND and the timeout rule could never fire -- a wrong-population
    measurement (CLAUDE.md gotcha #20), found while classifying its own output.

    RAW, because the replay's job is to send what the hook saw: the hook compares
    the LITERAL string "true" and rejects a non-integer timeout, so coercing here
    would replay a population the hook never received.
    """
    seen: dict[str, dict] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        body = (rec.get("message") or {}).get("content")
        if not isinstance(body, list):
            continue
        for b in body:
            if not (isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash"):
                continue
            inp = b.get("input") or {}
            c = inp.get("command") or ""
            if not c:
                continue
            item = {"command": c, "run_in_background": inp.get("run_in_background", False),
                    "timeout": inp.get("timeout", 0)}
            seen.setdefault(item_hash(item), item)
    return list(seen.values())


def item_hash(item: dict) -> str:
    return hashlib.sha1(json.dumps(item, sort_keys=True).encode("utf-8")).hexdigest()


def payload(item: dict) -> str:
    """The JSON the harness would send for this tool call."""
    return json.dumps({"tool_name": "Bash", "tool_input": {
        "command": item["command"], "run_in_background": item["run_in_background"],
        "timeout": item["timeout"]}})


# ---- the replay -----------------------------------------------------------------

def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def config_files_to_hash(toolkit: Path | None, environ) -> dict[str, Path]:
    """`label -> path` of every config the hooks could read, for `hash_hooks`.

    THE LABEL NAMES THE LOCATION (v4.0.0 review R5-13). Up to v3.3.1 the label `x4-paths.env`
    hashed `<tk>/.claude/x4-paths.env`; 4.0 pointed that same label at the root file, so an
    old baseline and a new run compared two DIFFERENT files under one key. These labels cannot
    collide with that one, so `hook_same`'s common-key rule names the old key instead of
    comparing it. X4_CONFIG's file is hashed too when it is set (R5-9): the hooks read it."""
    out: dict[str, Path] = {}
    if toolkit is not None:
        out["x4-paths.env (4.x root)"] = Path(toolkit) / "x4-paths.env"
        out[".claude/x4-paths.env (3.x)"] = Path(toolkit) / ".claude" / "x4-paths.env"
    if environ.get("X4_CONFIG"):
        out["X4_CONFIG"] = Path(_env._paths.native(environ["X4_CONFIG"]))
    return out


def _file_fingerprint(path: Path) -> str:
    try:
        return _sha(path.read_bytes())
    except FileNotFoundError:
        return "absent"
    except OSError as exc:
        return "unreadable:" + type(exc).__name__


def hash_hooks(hook_dir: Path, paths_envs: dict[str, Path] | None = None,
               toolkit: Path | None = None) -> dict[str, str]:
    """The actual hook, resolved save runtime and configs, including absent/unreadable states."""
    hook_dir = Path(hook_dir).resolve()
    toolkit = Path(toolkit).resolve() if toolkit is not None else hook_dir.parent.parent
    out = {f: _file_fingerprint(hook_dir / f) for f in HOOK_FILES}
    out["runtime:hook-directory"] = str(hook_dir)
    out["runtime:toolkit"] = str(toolkit)
    core = toolkit / "tools/modding_harness"
    names = set(CORE_FILES)
    if core.is_dir():
        def failed(error):
            raise UnstableInstrument("could not enumerate the shared runtime: " + type(error).__name__)
        for directory, _, filenames in os.walk(core, onerror=failed):
            names.update((Path(directory) / name).relative_to(core).as_posix()
                         for name in filenames if name.endswith(".py"))
    for name in sorted(names):
        out["runtime:modding_harness/" + name] = _file_fingerprint(core / name)
    out["runtime:modding-harness.lock.json"] = _file_fingerprint(toolkit / "tools/modding-harness.lock.json")
    for label, p in (paths_envs or {}).items():
        out[label] = _file_fingerprint(p)
    return out


def assert_complete_runtime(fingerprints: dict[str, str]) -> None:
    required = {*HOOK_FILES, "runtime:modding-harness.lock.json",
                *("runtime:modding_harness/" + name for name in CORE_FILES),
                *(key for key in fingerprints if key.startswith("runtime:modding_harness/"))}
    bad = [key for key in required if not re.fullmatch(r"[0-9a-f]{64}", fingerprints.get(key, ""))]
    if bad:
        raise UnstableInstrument("missing or unreadable verdict dependencies: " + ", ".join(bad))


#: Lines the transcript scan could not parse, and the files they came from.
#: Counted rather than merely skipped: "the denominator reported is DISTINCT
#: COMMANDS, never lines read" is true, but it makes a corpus that quietly
#: shrank indistinguishable from one that was always this size -- and
#: `errors="replace"` means a single corrupt byte can turn a VALID record into
#: an unparseable one. A drop is work not done; it gets a number.
dropped_lines: dict[str, int] = {}


def _records():
    from toolkit_usage import transcript_dir  # noqa: E402  (needs the project env)
    for f in sorted(transcript_dir().glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                yield json.loads(line)
            except ValueError:
                # Not silent any more. A malformed line carries no command, so it
                # cannot enter the corpus -- but "we dropped N" and "there were
                # none" are different statements and this used to make them the
                # same one.
                dropped_lines[f.name] = dropped_lines.get(f.name, 0) + 1
                continue


class RuleDelta(NamedTuple):
    """What a targeted replay actually measured.

    Every field is over the REPLAYED set, never the recorded one. The two differ by
    whatever left the corpus since the baseline, and comparing across that boundary
    reports an aged-out command as a fix -- in the mode used to verify a guard fix,
    which is the direction that matters.
    """
    replayed: int          # baseline hits still in the corpus
    still: int             # of those, how many still fire
    gone: int              # baseline hits no longer in the corpus
    replayed_controls: int
    moved_controls: list   # replayed allows that stopped allowing
    gone_controls: int


def rule_delta(targets, controls, now: dict) -> RuleDelta:
    """The targeted-replay arithmetic, as a pure function of three inputs."""
    tset, cset = set(targets), set(controls)
    replayed_t = tset & set(now)
    replayed_c = cset & set(now)
    return RuleDelta(
        replayed=len(replayed_t),
        still=sum(1 for h in replayed_t if now[h][0] != "allow"),
        gone=len(tset) - len(replayed_t),
        replayed_controls=len(replayed_c),
        moved_controls=[h for h in replayed_c if now[h][0] != "allow"],
        gone_controls=len(cset) - len(replayed_c),
    )


def collect_corpus() -> list[dict]:
    dropped_lines.clear()   # per call, or two runs in one process accumulate
    return extract_commands(_records())


def dropped_note() -> str:
    """One line naming what the scan could not read, or the empty string.

    Empty rather than "0 dropped" on purpose: a clean run should not print noise,
    and every CALLER prints its own denominator beside this."""
    if not dropped_lines:
        return ""
    n = sum(dropped_lines.values())
    where = ", ".join(f"{k}:{v}" for k, v in sorted(dropped_lines.items())[:3])
    more = "" if len(dropped_lines) <= 3 else f" (+{len(dropped_lines) - 3} more)"
    return f"{n} unparseable transcript line(s) skipped [{where}{more}]"


def _opt(argv: list[str], name: str, default: str) -> str:
    return next((a.split("=", 1)[1] for a in argv if a.startswith(name + "=")), default)


def _safe(fn, item):
    try:
        return fn(item)
    except HookOutputError as e:
        return e


def run(argv: list[str]) -> int:
    record = "--record" in argv
    limit = int(_opt(argv, "--limit", "0"))
    workers = int(_opt(argv, "--workers", str(min(14, (os.cpu_count() or 4)))))
    prove = int(_opt(argv, "--prove-parallel", "0"))
    out_opt = _opt(argv, "--out", "")
    out_path = Path(out_opt) if out_opt else None
    rule = _opt(argv, "--rule", "")
    n_control = int(_opt(argv, "--control", "400"))
    if rule and (record or out_path):
        print("REFUSING: --rule is a TARGETED check, not a run of the corpus. It must never "
              "write an artifact -- a partial one would read as a clean baseline.",
              file=sys.stderr)
        return 2
    if record and limit and not out_path:
        print("REFUSING: --record with --limit would store a PARTIAL baseline, and every "
              "later compare would report a clean 'no drift' over that subset. Add --out.",
              file=sys.stderr)
        return 2

    try:
        game = _env.extensions().parent
    except (SystemExit, AttributeError):
        game = None
    hook_dir = (game / ".claude" / "hooks") if game else None
    if not hook_dir or not all((hook_dir / f).is_file() for f in HOOK_FILES):
        print("REFUSING: game root not configured, or no protect-bash.sh/_x4-env.sh under it.",
              file=sys.stderr)
        return 2
    hook = str(hook_dir / "protect-bash.sh").replace("\\", "/")

    # env-ok: inherited for a CHILD process, not configuration being read -- the
    # hook must see what a human running it would.
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(game)
    noise = Counter()

    def decide(item: dict | str) -> tuple[str, str]:
        if isinstance(item, str):
            item = {"command": item, "run_in_background": False, "timeout": 0}
        # TIMEOUT, because a hook that never returns is not a verdict either.
        # MEASURED 2026-09-06: a full-corpus replay stalled at 15,000 of 17,202
        # with no diagnostic and had to be killed -- 14 workers, one wedged call,
        # and nothing able to say WHICH command. An unbounded wait is the same
        # absent-refusal shape this gate exists to find elsewhere: the instrument
        # had no failure path on the one axis that failed. A timeout converts it
        # into a HookOutputError, which already forces a REFUSAL naming the count.
        #
        # THE CAUSE IS NOW KNOWN, and this stays anyway. It was hook_facts.resolve()
        # expanding a self-referential assignment: bounded at 5 passes, unbounded in
        # SIZE, growing x9 per pass. One 949-character command produced 23,128,230
        # characters of carried command and an 18.3 GB working set. Fixed at source
        # by _MAX_RESOLVED, so this timeout should now be unreachable -- which is
        # exactly why it must remain: it is the only thing that turned an invisible
        # wedge into a named command, and the next unbounded axis will not be this one.
        try:
            p = subprocess.run([BASH, hook], input=payload(item), capture_output=True,
                               text=True, env=env, encoding="utf-8",
                               errors="replace", timeout=HOOK_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise HookOutputError(
                "no verdict in %ds: %r" % (HOOK_TIMEOUT, item["command"][:120]))
        v, reason, noisy = interpret(p.returncode, p.stdout, p.stderr)
        if noisy:
            noise["stderr_noise"] += 1
        return v, rule_key(reason)

    # What the resolver sees -- recorded so two runs on two machines are comparable,
    # allow-listed so a secret can never land in the artifact. Re-run at the end:
    # x4-paths.env or an env var moving mid-run is as void as the hook moving.
    def resolve() -> dict[str, str]:
        script = ('. "$0"; for v in ' + " ".join(PATH_VARS)
                  + '; do printf "%s=%s\\n" "$v" "${!v}"; done')
        res = subprocess.run([BASH, "-c", script, str(hook_dir / "_x4-env.sh").replace("\\", "/")],
                             capture_output=True, text=True, env=env, encoding="utf-8",
                             errors="replace")
        return resolved_paths(res.stdout)

    resolved = resolve()
    print("resolved:", ", ".join(f"{k}={'set' if v else 'EMPTY'}" for k, v in resolved.items()))
    _tk = Path(_env._paths.native(resolved["X4_TOOLKIT"])) if resolved.get("X4_TOOLKIT") else None
    paths_envs = config_files_to_hash(_tk, env)
    try:
        if _tk is None:
            raise UnstableInstrument("the hook did not resolve its shared runtime toolkit")
        sha_before = hash_hooks(hook_dir, paths_envs, toolkit=_tk)
        assert_complete_runtime(sha_before)
    except UnstableInstrument as exc:
        print("REFUSING:", exc, file=sys.stderr)
        return 2

    try:
        pos, _ = decide('rm -rf "' + str(game) + '"')
        neg, _ = decide("ls -la")
    except HookOutputError as e:
        # MEASURED (review probe): JQ=no_such_binary -> rc 0, empty stdout, stderr
        # noise. That is the instrument failing, not the corpus -- rc 2, and say so.
        print("REFUSING: the hook did not produce a verdict on a CONTROL command:", e,
              file=sys.stderr)
        return 2
    print("control: known-bad ->", pos, " harmless ->", neg)
    if pos != "deny" or neg != "allow":
        print("REFUSING: the harness cannot produce a deny AND an allow, so any rate it "
              "reported would be meaningless.", file=sys.stderr)
        return 2

    if rule:
        if not BASELINE.is_file():
            print(f"REFUSING: no baseline at {BASELINE.name} to take the subset from.",
                  file=sys.stderr)
            return 2
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
        try:
            check_baseline(base)
            targets, controls = select_subset(base, rule, n_control, seed=17)
        except (PartialBaseline, NoSuchRule) as e:
            print("REFUSING:", e, file=sys.stderr)
            return 2
        by_hash = {item_hash(i): i for i in collect_corpus()}
        missing = [h for h in targets + controls if h not in by_hash]
        pairs = [(h, by_hash[h]) for h in targets + controls if h in by_hash]
        print(f"targeted: rule {rule!r} -> {len(targets)} baseline hits, "
              f"{len(controls)} allow controls, {len(missing)} no longer in the corpus")
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            got = list(ex.map(lambda kv: _safe(decide, kv[1]), pairs))
        bad = [p[0] for p, g in zip(pairs, got) if isinstance(g, HookOutputError)]
        if bad:
            print(f"REFUSING: the hook produced no verdict on {len(bad)} command(s).",
                  file=sys.stderr)
            return 2
        now = {h: g for (h, _), g in zip(pairs, got)}
        tset, cset = set(targets), set(controls)
        d = rule_delta(targets, controls, now)
        moved_control = d.moved_controls
        replayed_c = d.replayed_controls
        print(f"  {rule!r}: of {d.replayed} replayed, {d.still} still fire "
              f"({d.still - d.replayed:+d})   [{time.perf_counter() - t0:.0f}s]")
        if d.gone or d.gone_controls:
            print(f"     NOT REPLAYED: {d.gone} of {len(tset)} baseline hit(s) and "
                  f"{d.gone_controls} of {len(cset)} control(s) are no longer in the "
                  "corpus -- absent from BOTH numbers above, never counted as fixed.")
        note = dropped_note()
        if note:
            print("     " + note)
        for v, n in sorted(Counter(now[h][0] for h in tset if h in now).items()):
            print(f"     {v:8} {n:5}")
        for r, n in Counter(now[h][1] for h in tset if h in now and now[h][0] != "allow").most_common():
            print(f"     [{n:4}] {r[:80]}")
        # Over the REPLAYED controls, for the same reason: a control that left the
        # corpus is not in `moved_control`, so the old form counted it as passing
        # and could print 400/400 having replayed none of them. This is the check
        # that catches a LOOSENING regression -- it must not pass vacuously.
        print(f"  control: {replayed_c - len(moved_control)}/{replayed_c} replayed allows still allow")
        if not replayed_c and cset:
            print("REFUSING: none of the allow controls is still in the corpus, so "
                  "nothing guarded against a loosening.", file=sys.stderr)
            return 2
        if moved_control:
            # rc 1, not 2. This is a FINDING -- the gate's own words call it a
            # regression -- and run-gates.sh reads 2 as "could not run", so a real
            # loosening/tightening regression was being filed as an instrument
            # problem (AUDIT-2026-09-24 GT-4).
            print("FAIL: a fix that turns an ALLOW into anything is a regression, not a "
                  "delta. Moved:", file=sys.stderr)
            for h in moved_control[:5]:
                print(f"    {now[h][0]}  {now[h][1][:70]}", file=sys.stderr)
            return 1
        return 0

    uniq = collect_corpus()
    total = len(uniq)
    if limit:
        uniq = uniq[:limit]
    print(f"corpus: {total} distinct commands" + (f", replaying first {limit}" if limit else ""))
    if not uniq:
        print("REFUSING: the corpus is EMPTY -- nothing was replayed, so there is no rate.",
              file=sys.stderr)
        return 2

    if prove:
        slice_ = uniq[:prove]
        t0 = time.perf_counter()
        serial = [decide(c) for c in slice_]
        t1 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            par = list(ex.map(decide, slice_))
        t2 = time.perf_counter()
        bad = [i for i, (a, b) in enumerate(zip(serial, par)) if a != b]
        print(f"prove-parallel over {len(slice_)}: serial {t1 - t0:.1f}s, "
              f"{workers} workers {t2 - t1:.1f}s, mismatches {len(bad)}")
        if bad:
            print("REFUSING: parallel replay disagrees with serial on", bad[:10], file=sys.stderr)
            return 2
        noise.clear()  # the proof replayed the slice twice; count noise once, below

    t0 = time.perf_counter()
    verdicts: dict[str, list] = {}
    text: dict[str, str] = {}
    errors: list[str] = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for item, result in zip(uniq, ex.map(lambda c: _safe(decide, c), uniq)):
            h = item_hash(item)
            first = item["command"].splitlines()[0][:120]
            if isinstance(result, HookOutputError):
                errors.append(f"{first}  <-  {result}")
                continue
            verdicts[h] = list(result)
            text[h] = first
            done += 1
            if done % 1000 == 0:
                c = Counter(v for v, _ in verdicts.values())
                print(f"  {done}/{len(uniq)}  deny={c['deny']} ask={c['ask']} "
                      f"advise={c['advise']}  {time.perf_counter() - t0:.0f}s", flush=True)
    elapsed = time.perf_counter() - t0

    if errors:
        print(f"REFUSING: the hook did not produce a verdict on {len(errors)} command(s), e.g.",
              file=sys.stderr)
        for e in errors[:5]:
            print("   ", e, file=sys.stderr)
        return 2
    try:
        assert_stable(sha_before, hash_hooks(hook_dir, paths_envs, toolkit=_tk))
        assert_stable(resolved, resolve())
    except UnstableInstrument as e:
        print("REFUSING:", e, "-- the run mixed two rule sets and is void.", file=sys.stderr)
        return 2

    s = summarize(verdicts, text=text)
    n = len(verdicts)
    print(f"\nreplayed {n} commands in {elapsed:.0f}s with {workers} workers; "
          f"stderr noise on {noise['stderr_noise']}")
    print("VERDICTS over distinct historical commands (all of which RAN FINE):")
    for k in ("allow", "advise", "ask", "deny"):
        print(f"  {k:8} {s['counts'].get(k, 0):6}  ({100.0 * s['counts'].get(k, 0) / n:5.2f}%)")
    print("\nBY RULE (uncapped counts, every non-allow verdict):")
    for rule, cnt in sorted(s["counts_by_rule"].items(), key=lambda kv: -kv[1]):
        print(f"  [{cnt:5}]  {rule[:100]}")

    artifact = {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "distinct_total": total, "replayed": n, "limit": limit, "workers": workers,
        "prove_parallel": prove, "stderr_noise": noise["stderr_noise"],
        "hook_sha256": sha_before, "resolved": resolved,
        **s, "verdicts": verdicts,
    }
    target = out_path or (BASELINE if record else None)
    if target:
        blob = json.dumps(artifact, indent=1)
        leaked = secret_scan(blob, env)
        if leaked:
            print("REFUSING to write the artifact: the VALUE of", ", ".join(leaked),
                  "appears in it (a historical command carried it). Nothing written.",
                  file=sys.stderr)
            return 2
        target.write_text(blob, encoding="utf-8")
        print(f"\nrecorded -> {target}")
        return 0
    if not BASELINE.is_file():
        print(f"\nno baseline at {BASELINE.name} -- run with --record first "
              "(then diff future runs against it)", file=sys.stderr)
        return 2
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    try:
        check_baseline(base)
    except PartialBaseline as e:
        print("REFUSING:", e, file=sys.stderr)
        return 2
    rep = compare(base, artifact)
    same_hook, unshared = hook_same(base.get("hook_sha256") or {}, sha_before)
    print(f"\nCOMPARE vs baseline ({base.get('recorded_at', '?')}, hook "
          f"{'IDENTICAL' if same_hook else 'DIFFERENT'}"
          + (f", not hashed on both sides: {', '.join(unshared)}" if unshared else "")
          + f"): shared {rep['shared']}, "
          f"only-in-baseline {rep['only_in_baseline']}, only-in-current {rep['only_in_current']}, "
          f"changed {rep['changed']}")
    for rule, d in sorted(rep["by_rule"].items(), key=lambda kv: kv[1]["delta"]):
        print(f"  {d['baseline']:5} -> {d['current']:5}  ({d['delta']:+d})  {rule[:90]}")
    if rep["moved"]:
        print("  moved (old rule -> new rule; '' = allow):")
        for (old, new), k in sorted(rep["moved"].items(), key=lambda kv: -kv[1]):
            print(f"    {k:5}  {old[:40] or '(allow)'!r} -> {new[:40] or '(allow)'!r}")
    # A verdict over an EMPTY intersection is a non-answer. MEASURED by the review: a
    # baseline and a current run sharing ZERO commands returns changed 0, drift False, and
    # main() printed "no drift on shared commands" and returned 0 -- with every rule in
    # protect-bash.sh free to have changed. Transcripts are per-machine, gitignored and
    # rotate, so the intersection really does decay toward zero. The --rule branch already
    # refuses its own version of this ("nothing guarded against a loosening"); this one did
    # not. rc 2, because run-gates.sh buckets 0 as ok and discards stdout.
    if not rep["shared"]:
        print("REFUSING: the baseline and this run share NO commands, so "
              "'no drift on shared commands' would be a claim about an empty set. "
              "Re-record against the current transcripts (--record) or point --transcripts "
              "at the corpus the baseline was drawn from.", file=sys.stderr)
        return 2
    if rep["drift"]:
        if same_hook:
            print("\nDRIFT with an IDENTICAL hook: the verdicts are not deterministic, or the "
                  "environment differs. That is an instrument finding, not a rule change.")
        else:
            print("\nDRIFT on shared commands with a CHANGED hook. Check the deltas match the "
                  "prediction made before the change, then re-record with --record.")
        return 1
    print("\nno drift on shared commands")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        return run(argv)
    except Exception:  # noqa: BLE001 -- rc 3 so an unexpected failure can never read as DRIFT (1)
        traceback.print_exc()
        print("REFUSING: unexpected exception (rc 3, distinct from drift).", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
