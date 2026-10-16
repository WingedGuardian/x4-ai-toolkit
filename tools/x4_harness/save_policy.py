"""X4 save profile bridge: one batch, synchronous rules then preservation.

The Bash backend retains game policy. The callback is told when every affected
profile file is a resolved save, so only its obsolete save confirmation can be
replaced. Read-only checks use preview=True and never preserve bytes.
"""
from __future__ import annotations

import glob
import json
import hashlib
import importlib
import importlib.util
import os
import re
import sys
import subprocess
import time
from dataclasses import replace
from pathlib import Path

sys.dont_write_bytecode = True


def core(root, name):
    package = Path(root).resolve() / "tools/modding_harness"
    if not package.is_dir():
        raise ValueError("pinned offline harness is missing")
    prefix = "_x4_save_harness_" + hashlib.sha256(os.path.normcase(str(package)).encode()).hexdigest()[:20]
    if prefix not in sys.modules:
        spec = importlib.util.spec_from_file_location(prefix, package / "__init__.py",
                                                     submodule_search_locations=[str(package)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[prefix] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(prefix, None)
            raise
    return importlib.import_module(prefix + "." + name)


def native(path):
    if os.name == "nt":
        path = re.sub(r"^/([A-Za-z])(?=/|$)", lambda match: match[1].upper() + ":", path)
        # Other MSYS mounts must be translated by the calling Bash transport.
        # Treating /tmp as C:\tmp would invent a different physical source.
        if path.startswith("/") and not path.startswith("//"):
            import x4guard
            bash, why = x4guard.resolve_bash()
            if not bash:
                raise ValueError("MSYS path could not be resolved: " + (why or "no Bash"))
            converted = subprocess.run([bash, "-c", 'cygpath -m -- "$1"', "--", path],
                                       capture_output=True, timeout=3)
            if converted.returncode:
                raise ValueError("MSYS path conversion failed")
            path = converted.stdout.decode("utf-8").strip()
            if not re.match(r"^[A-Za-z]:/|^//", path):
                raise ValueError("MSYS path conversion returned no native absolute path")
    if not os.path.isabs(path):
        raise ValueError("save mutation has no absolute physical path")
    return Path(path).absolute()


def folders(roots):
    result = []
    if roots.get("saves"):
        result.append(native(roots["saves"]))
    if roots.get("profile"):
        profile = native(roots["profile"])
        result.append(profile / "save")
        # The fallback protection root can hold several account directories.
        if profile.name.lower() == "x4" and profile.is_dir():
            result.extend(path / "save" for path in profile.iterdir() if path.is_dir())
    return tuple(dict.fromkeys(result))


def resolve(root, roots, evidence, deadline=None):
    dispatch = core(root, "dispatch")
    profiles = core(root, "profiles")
    preservation = core(root, "preservation")
    workspace = core(root, "workspace")
    save_roots = folders(roots)
    # XML is also accepted by X4 tools when save compression is disabled.
    profile = profiles.GameProfile("x4", save_roots, (".xml.gz", ".xml"))
    store = workspace.save_store(root, profile)
    mutations, affected, problems = [], [], []
    wildcard = False
    profile_targets, save_targets = 0, 0
    expanded = []
    directory_copy = False
    for row in evidence:
        raw = row.get("path", "")
        if (raw and not row.get("unresolved") and row.get("scope") == "copy-destination"
                and native(raw).is_dir() and any(preservation.contains(folder, native(raw)) for folder in save_roots)):
            directory_copy = True
            sources = row.get("copy_sources")
            if not isinstance(sources, list) or not sources:
                problems.append("copy into a save folder must identify its resulting filenames")
                continue
            for source in sources:
                if (source.get("unresolved") or not source.get("path") or glob.has_magic(source["path"])
                        or not native(source["path"]).is_file() or native(source["path"]).is_symlink()):
                    problems.append("copy source filename or file kind is not qualified")
                    continue
                expanded.append({**row, "path": str(native(raw) / native(source["path"]).name), "scope": "file"})
        else:
            expanded.append(row)
    for row in expanded:
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("save scope resolution deadline expired")
        raw = row.get("path", "") or row.get("raw", "")
        if row.get("unresolved"):
            # Keep an unresolved protected operand as a refusal, never an empty set.
            if any(value and str(value).casefold() in raw.casefold() for value in
                   (*save_roots, roots.get("profile", ""), store.root)):
                problems.append("unresolved protected operand")
            continue
        path = native(raw)
        op = row["operation"]
        affected.append((str(path), op))
        if preservation.protects(path, [Path(root) / ".modding-agent-harness"], ancestor=op in ("move", "delete")):
            problems.append("neutral harness state is protected")
        if preservation.protects(path, [store.root], ancestor=op in ("move", "delete")):
            problems.append("permanent save store is protected")
        if op in ("move", "delete") and any(preservation.contains(path, folder) for folder in save_roots):
            problems.append("whole save-folder move/delete is refused")
        pattern = row.get("literal") is not True and glob.has_magic(str(path))
        candidates = []
        for candidate in glob.iglob(str(path)) if pattern else [str(path)]:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("save scope resolution deadline expired")
            candidates.append(candidate)
        candidates.sort()
        if pattern and ("**" in str(path) or row.get("scope") in ("tree", "filtered-tree")):
            problems.append("recursive or filtered helper membership is not qualified")
        if row.get("scope") in ("tree", "filtered-tree") and any(
                preservation.contains(folder, path) or preservation.contains(path, folder) for folder in save_roots):
            problems.append("save helper does not identify its exact affected files")
        for candidate in candidates:
            candidate = native(candidate)
            affected.append((str(candidate), op))
            if preservation.protects(candidate, [store.root], ancestor=op in ("move", "delete")):
                problems.append("permanent save store is protected")
            if op in ("move", "delete") and any(preservation.contains(candidate, folder) for folder in save_roots):
                problems.append("whole save-folder move/delete is refused")
            in_profile = (bool(roots.get("profile")) and preservation.contains(native(roots["profile"]), candidate)
                          or any(preservation.contains(folder, candidate) for folder in save_roots))
            is_save = any(preservation.contains(folder, candidate) for folder in save_roots) and any(
                candidate.name.lower().endswith(ext) for ext in profile.extensions)
            if in_profile:
                profile_targets += 1
            if is_save:
                if candidate.is_dir():
                    problems.append("save member is a directory")
                mutations.append(dispatch.Mutation(str(candidate), op))
                save_targets += int(in_profile)
                wildcard = wildcard or pattern
            elif any(candidate == folder for folder in save_roots) and op == "write":
                problems.append("copy into a save folder must identify its resulting filenames")
        if pattern and not candidates and any(preservation.contains(folder, path) for folder in save_roots):
            # An empty wildcard cannot become authority for later appearing saves.
            wildcard = True
    mutations = tuple(sorted(set(mutations), key=lambda row: (row.path, row.operation)))
    # Resolve again after preservation; any changed wildcard set invalidates authority.
    recheck = (lambda: resolve(root, roots, evidence, deadline)[1].mutations) if wildcard or directory_copy else None
    plan = dispatch.SavePlan(mutations, wildcard, helper=directory_copy,
                             unresolved="; ".join(dict.fromkeys(problems)), recheck=recheck)
    return profile, plan, tuple(dict.fromkeys(affected)), profile_targets > 0 and profile_targets == save_targets


def enforce(root, roots, evidence, action, backend, *, transcript="", preview=False, deadline=None,
            local_restrictions=(), helper=False):
    """backend(save_only) returns a v1 Result after all Bash/local rules ran."""
    contracts = core(root, "contracts")
    dispatch = core(root, "dispatch")
    try:
        profile, plan, affected, save_only = resolve(root, roots, evidence, deadline)
        if helper:
            plan = replace(plan, helper=True, recheck=plan.recheck or
                           (lambda: resolve(root, roots, evidence, deadline)[1].mutations))
        action = replace(action, paths=tuple(path for path, _op in affected),
                         operations=tuple(op for _path, op in affected))
        def evaluate(request):
            result = backend(save_only and not plan.unresolved)
            if plan.unresolved and result.decision != "deny":
                result = replace(result, decision="deny", evaluation="partial", preservation="failed",
                                 reason="save scope refused: " + plan.unresolved)
            return dispatch.Evaluation(result, plan)
        dispatcher = dispatch.Dispatcher(root, profile, evaluate, local_restrictions)
        outcome = dispatcher.pre(action, transcript, deadline, preserve=not preview)
        if not preview:
            core(root, "telemetry").Telemetry(root).record(action, "pre", outcome.result,
                                                          source="X4 profile bridge")
        return outcome
    except (OSError, ValueError) as exc:
        return dispatch.Outcome(contracts.Result("deny", "failed", "failed", reason=str(exc)))


def shell_evidence(payload, roots):
    import hook_facts
    evidence = []
    facts = hook_facts.facts(payload, roots, evidence=evidence)
    if facts.get("powershell_error") or facts.get("carrier_untranslated"):
        raise ValueError("shell scope could not be completely evaluated")
    return evidence


def read_roots(deadline):
    import x4guard
    bash, why = x4guard.resolve_bash()
    if not bash:
        raise ValueError(why)
    hooks = Path(__file__).resolve().parent
    result = subprocess.run([bash, "-c", 'HOOK_DIR="$1"; . "$HOOK_DIR/_x4-env.sh"; x4_emit_guard_roots',
                             "--", str(hooks)], capture_output=True,
                            timeout=max(.01, deadline - time.monotonic()))
    if result.returncode:
        raise ValueError("protection roots could not be resolved by the Bash backend")
    return packet(result.stdout.decode("utf-8") + "{}")[0]


def batch(payload, calls, judge, deadline, host, preview=False, helper=False):
    """The named adapters call this once, before ANY mutation is permitted."""
    if not calls:
        import x4guard
        # Every generated host folder and tools/x4_harness has the same depth.
        # Unknown tools need neither game roots nor another install's package.
        root = native(str(Path(__file__).resolve().parents[2]) if x4guard._deployed() else
                      os.environ.get("X4_TOOLKIT") or str(Path(__file__).resolve().parents[1]))
    else:
        roots = read_roots(deadline)
        root = native(roots["toolkit"])
    contracts = core(root, "contracts")
    evidence = []
    for kind, shell, command, path, _label, *_extra in calls:
        if kind == "shell":
            evidence += shell_evidence({"tool_name": "PowerShell" if shell == "powershell" else "Bash",
                                        "tool_input": {"command": command}, "cwd": os.getcwd()}, roots)
        else:
            evidence.append({"path": str(path), "operation": kind, "literal": True})
    ti = payload.get("tool_input", payload.get("args", {}))
    action = contracts.Action(host, str(payload.get("tool_name", payload.get("tool", ""))),
                              "native" if calls else "unknown", cwd=os.getcwd(),
                              host_version=str(payload.get("host_version", "unknown")),
                              session_id=str(payload.get("session_id", "")),
                              tool_call_id=str(payload.get("tool_use_id", payload.get("call_id", ""))),
                              command=str(ti.get("command", ti.get("patchText", ti.get("input", "")))))
    if not calls:
        if action.tool == "write_stdin":
            # A poll or an unfinished line submits no command. This says nothing
            # about the host's documented lack of a fresh stdin interception.
            result = contracts.Result(evaluation="partial", reason="no complete stdin line submitted")
            if not preview:
                core(root, "telemetry").Telemetry(root).record(action, "pre", result, source=host + " adapter")
            return {"decision": "allow", "inert": False, "reason": None, "context": None,
                    "result": result.as_dict(), "transactions": []}
        result = contracts.unknown_tool(action)
        telemetry = core(root, "telemetry")
        count = telemetry.Telemetry(root).record(action, "pre", result, source=host + " adapter") if not preview else None
        return {"decision": "advise", "inert": False, "reason": None,
                "context": result.warnings[0] + " (" + action.tool + ")" if telemetry.announce(count) else None,
                "result": result.as_dict(), "transactions": []}
    base = []
    def backend(save_only):
        verdict = judge(save_only)
        base.append(verdict)
        return contracts.Result("allow" if verdict["decision"] == "advise" else verdict["decision"],
                                "failed" if verdict.get("inert") else "evaluated",
                                reason=verdict.get("reason") or "",
                                warnings=(verdict["context"],) if verdict.get("context") else ())
    outcome = enforce(root, roots, evidence, action, backend, deadline=deadline,
                      transcript=str(payload.get("transcript_path", "")), preview=preview, helper=helper)
    result = outcome.result
    if result.decision in ("deny", "ask"):
        return {"decision": result.decision, "inert": result.evaluation == "failed",
                "reason": result.reason, "context": None, "result": result.as_dict(), "transactions": []}
    context = "\n".join(result.warnings)
    if result.preservation == "verified":
        context += "\nSave backup verified in the permanent neutral store before this call."
    return {"decision": "advise" if context.strip() else "allow", "inert": False,
            "reason": None, "context": context.strip() or None, "result": result.as_dict(),
            "transactions": list(outcome.transactions)}


def packet(raw):
    head, body = raw.split("--X4-ROOTS-END--", 1)
    roots = dict(line.split("\t", 1) for line in head.splitlines() if "\t" in line)
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("hook payload is not an object")
    return roots, payload


def _hook_json(result):
    hso = {"hookEventName": "PreToolUse"}
    if result.decision in ("deny", "ask"):
        hso.update(permissionDecision=result.decision, permissionDecisionReason=result.reason)
    else:
        text = "\n".join(result.warnings)
        if result.preservation == "verified":
            text += "\nSave backup verified in the permanent neutral store before this call."
        elif result.preservation == "required":
            text += "\nPREVIEW ONLY: executing this save change still requires verified preservation."
        if text.strip():
            hso["additionalContext"] = text.strip()
        else:
            return ""
    return json.dumps({"hookSpecificOutput": hso}, ensure_ascii=True)


def main(argv=None):
    import x4guard
    args = list(sys.argv[1:] if argv is None else argv)
    failed = False
    try:
        kind, script = args
        roots, payload = packet(sys.stdin.buffer.read().decode("utf-8"))
        root = native(roots["toolkit"])
        contracts = core(root, "contracts")
        action = contracts.Action("claude", str(payload.get("tool_name", "")), kind,
                                  cwd=str(payload.get("cwd", "")),
                                  shell="powershell" if payload.get("tool_name") == "PowerShell" else "bash",
                                  command=str((payload.get("tool_input") or {}).get("command", "")),
                                  session_id=str(payload.get("session_id", "")),
                                  tool_call_id=str(payload.get("tool_use_id", "")))
        deadline = time.monotonic() + 20
        if kind == "shell":
            evidence = shell_evidence(payload, roots)
        else:
            ti = payload.get("tool_input") or {}
            path = ti.get("file_path") or ti.get("notebook_path")
            if not isinstance(path, str) or not path:
                raise ValueError("file request has no path")
            # Git Bash payloads retain /c/... even though native Python 3.13
            # no longer treats a single leading slash as a Windows absolute
            # path. Resolve that mount before deciding that a cwd is needed.
            if not (os.path.isabs(path) or (os.name == "nt" and path.startswith("/"))):
                if not action.cwd:
                    raise ValueError("relative file request has no absolute cwd")
                path = os.path.join(str(native(action.cwd)), path)
            path = str(native(path))
            evidence = [{"path": path, "operation": "write", "literal": True}]

        def backend(save_only):
            bash, why = x4guard.resolve_bash()
            if not bash:
                raise ValueError(why)
            args = [bash, script, "--harness-policy-backend"] + (["--save-qualified"] if save_only else [])
            completed = subprocess.run(args, input=json.dumps(payload).encode("utf-8"),
                                       capture_output=True, timeout=max(.01, deadline - time.monotonic()))
            if completed.returncode:
                raise ValueError("Bash rule backend did not complete")
            decision, reason, context = x4guard.parse_hook_output(completed.stdout.decode("utf-8"))
            return contracts.Result("allow" if decision == "advise" else decision, "evaluated",
                                    reason=reason or "", warnings=(context,) if context else ())

        outcome = enforce(root, roots, evidence, action, backend,
                          transcript=str(payload.get("transcript_path", "")),
                          preview=os.environ.get("X4_GUARD_CHECK") == "1", deadline=deadline)
        failed = outcome.result.evaluation == "failed"
        text = _hook_json(outcome.result)
    except BaseException as exc:
        failed = True
        # No raw shell/payload/exception text (which may include private data).
        text = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                          "permissionDecisionReason": "X4 save dispatcher failed: " + type(exc).__name__ +
                          "; this call was not allowed."}})
    sys.stdout.buffer.write(("X4SAVE " + text + "\n").encode("ascii"))
    # Native hooks still return an explicit refusal with exit zero. The private
    # read-only check protocol must retain its separate "checked nothing" code.
    return 2 if failed and os.environ.get("X4_GUARD_CHECK") == "1" else 0


if __name__ == "__main__":
    raise SystemExit(main())
