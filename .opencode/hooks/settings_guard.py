#!/usr/bin/env python3
"""May an agent write this Claude Code settings file? -- the X4_GUARD-in-settings rule.

USER DECISION (2026-10-05, relayed by the orchestrator): "Block X4_GUARD there". MEASURED on
Claude Code 2.1.290: a project `.claude/settings.json` `env` block reaches every hook process
(a SessionStart and a UserPromptSubmit hook both saw the value). So an agent that writes
`"env": {"X4_GUARD": "off"}` into a settings file switches every guard to advisory at the next
launch -- the switch the launch environment alone is meant to hold (R1-F1).

The rule: an agent write to `.claude/settings*.json` whose RESULTING content names X4_GUARD or
X4_GUARD_CHECK -- in an `env` block or ANYWHERE else, e.g. a `hooks` command prefix
`X4_GUARD=off bash ...` (FX-G4 / H4a) -- is DENIED; every other settings edit is allowed. When the
result cannot be known, it is denied with a reason the agent can act on -- never an ask (the
user's no-prompt rule).

One implementation, three callers:
  protect-files.sh  stdin = the Write/Edit/MultiEdit payload  -> a reason on stdout, or nothing
  x4guard / adapters a write whose text is an apply_patch or an OpenCode write/edit (`x4_written`)
  session-canary.sh --scan FILE...  -> one line per settings file that sets it now
Stdlib only.
"""
from __future__ import annotations

import json
from collections import Counter
import os
import re
import sys

#: `.claude/settings.json`, `.claude/settings.local.json`, and any other settings*.json there --
#: project or user level, any dialect (backslashes and case folded by the caller's norm).
SETTINGS = re.compile(r"(^|/)\.claude/settings[^/]*\.json$")
KEYS = ("X4_GUARD", "X4_GUARD_CHECK")
#: Either key named anywhere in a string (X4_GUARD_CHECK contains X4_GUARD).
_MENTION = re.compile(r"X4_GUARD(?:_CHECK)?", re.IGNORECASE)
#: The text could spell a key without containing it literally: a JSON \u escape.
_ESCAPE = re.compile(r"\\u[0-9a-fA-F]{4}")

REASON = ("BLOCKED: this write would put {key} into {path}, a Claude Code settings file whose "
          "`env` block and hook commands reach every hook -- it could switch the X4 guards off at "
          "the next launch. Agents may not do that (user decision, 2026-10-05). The user can set "
          "X4_GUARD=off themselves, in the environment they launch from. Make the settings edit "
          "without that key.")
UNKNOWN = ("BLOCKED: the guard cannot tell what this write leaves in {path}, a Claude Code "
           "settings file -- and its `env` block reaches every hook, so X4_GUARD there would "
           "switch the X4 guards off. {why} Write it with your file-edit tool (Write / Edit / "
           "apply_patch), whose content the guard checks; X4_GUARD itself is the user's to set, "
           "at launch.")


#: An apply_patch whose every operation is `*** Add File:` -- no Update, Delete or Move header
#: ANYWHERE in the text (an indented header counts: Codex obeys one), so it can only write the
#: added lines as a whole file.
_PATCH_OP = re.compile(r"\*\*\* (Add File|Update File|Delete File|Move to):")


def _add_only(text: str) -> bool:
    ops = {m.group(1) for m in _PATCH_OP.finditer(text)}
    return "*** Begin Patch" in text and ops == {"Add File"}


def norm(p: str) -> str:
    return (p or "").replace(chr(92), "/").lower()


def is_settings(path: str) -> bool:
    return bool(SETTINGS.search(norm(path)))


#: The NAME part of a shell word `NAME=value` that is not a plain literal: built only from name
#: characters plus quoting / expansion characters (`X4_GUA""RD`, `X4_$'GUARD'`, `X4_$V`,
#: `${P}GUARD`), and holding at least one of the latter. A `(` or `/` (a `$(date)=x`, a URL's
#: `?a=b`) is not a name and is left alone.
_DYN_NAME = re.compile(r"^(?=.*[\"'\\$`])[A-Za-z0-9_\"'\\${}`]+$")


def _shell_words(s: str):
    """`s` split at UNQUOTED blanks and command separators (quote-aware, stdlib only)."""
    out, buf, q, i = [], [], "", 0
    while i < len(s):
        c = s[i]
        if q:
            buf.append(c)
            if c == q:
                q = ""
            elif c == chr(92) and q == '"' and i + 1 < len(s):
                i += 1
                buf.append(s[i])
        elif c == chr(92) and i + 1 < len(s):
            buf += [c, s[i + 1]]
            i += 1
        elif c in "\"'":
            q = c
            buf.append(c)
        elif c.isspace() or c in ";&|()<>":
            if buf:
                out.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    if buf:
        out.append("".join(buf))
    return out


def dynamic_assignment(s: str) -> bool:
    """Does shell text `s` assign through a NAME that is not a plain literal? (v4.0 release
    review, lane F7, MEASURED in Git Bash: `env X4_GUA""RD=off bash ...`, `export X4_GUA""RD=off;
    bash ...`, `env X4_$'GUARD'=off bash ...` and `V=GUARD; env X4_$V=off bash ...` each SET
    X4_GUARD=off, and none names the key literally.) Fail closed: any such name is refused,
    whatever it spells -- what it spells is decided at run time, or by quote removal."""
    for w in _shell_words(s):
        eq = w.find("=")
        if eq > 0 and w[eq - 1] == "+":
            eq -= 1
        if eq > 0 and _DYN_NAME.match(w[:eq]):
            return True
    return False


def guard_keys(text: str):
    """(keys set in an `env` block, decidable?) for settings-file TEXT. Unparseable JSON is
    decidable only when the text cannot spell a key at all."""
    try:
        doc = loads(text) if text.strip() else {}
    except ValueError:
        if any(k in text.upper() for k in KEYS) or _ESCAPE.search(text) or dynamic_assignment(text):
            return [], False
        return [], True
    found = []
    stack = [doc]
    while stack:                                   # an `env` at ANY depth counts
        o = stack.pop()
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "env" and isinstance(v, dict):
                    found += [x for x in v if str(x).upper() in KEYS]
                elif _MENTION.search(str(k)):
                    found.append(_MENTION.search(str(k)).group(0))
                if k == "permissions" and o is doc:
                    continue    # rule STRINGS (`Bash(export X4_GUARD=off)`) set nothing (USER, 2026-10-06)
                stack.append(v)
        elif isinstance(o, list):
            stack.extend(o)
        elif isinstance(o, str) and _MENTION.search(o):
            # FX-G4 / reviewer H4a, MEASURED: `X4_GUARD=off bash .../protect-bash.sh` as a
            # `hooks` COMMAND string switches that hook off and was allowed -- only an `env`
            # block was read. The key ANYWHERE in the file is refused (USER DECISION: "block
            # X4_GUARD there"); a settings file has no legitimate use for it.
            found.append(_MENTION.search(o).group(0))
        elif isinstance(o, str) and dynamic_assignment(o):
            # ...and a NAME spelled so that no literal text shows it (see dynamic_assignment).
            found.append("X4_GUARD (an assignment whose variable NAME is quoted, escaped or "
                         "built from a variable -- write the name literally)")
    return found, True


def _read(path: str):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    except FileNotFoundError:
        return ""
    except OSError:
        return None


def _apply(text: str, edits: list):
    """The Edit/MultiEdit result, or None when an old_string does not apply (the tool fails)."""
    for e in edits:
        old, new = e.get("old_string"), e.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return None
        if old == "":
            text = new + text
        elif old not in text:
            return None
        else:
            text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text


def _could_form_key(new: str) -> bool:
    """Could `new`, spliced into text this guard has not seen at a place it does not know, make
    a key appear? (FX-G6 / reviewer K I5: an edit that does not apply to the file as read here
    is judged this way -- fail closed.) Yes when it names one or holds a JSON backslash-u
    escape; when it is a piece of `X4_GUARD`; when it STARTS with the key's tail or ENDS with
    its head (the surrounding text supplies the rest); or when its first characters could
    finish a backslash-u escape the text before it opened, or its last could open one."""
    u, k = new.upper(), KEYS[0]
    if _MENTION.search(new) or _ESCAPE.search(new) or (u and u in k):
        return True
    if any(u.startswith(k[i:]) or u.endswith(k[:i]) for i in range(1, len(k))):
        return True
    return bool(re.match(r"(?i)[u0-9a-f]", new) or _OPEN_ESCAPE.search(new))


#: Text ending in a backslash, or a backslash-u with fewer than four hex digits after it.
_OPEN_ESCAPE = re.compile("(?i)" + re.escape(chr(92)) + "(u[0-9a-f]{0,3})?$")


def _judge_edit(path: str, edit) -> str:
    """One string replacement -- {old, new, replace_all} -- applied to the file as it is NOW and
    the RESULT judged, as for a Claude Code Edit (FX-G6 / reviewer K I5, MEASURED: an OpenCode
    edit was judged on its new text alone, so `"X4_GUA": "x"` then `A": "x"` -> `ARD": "off"`
    built X4_GUARD=off from two innocent texts). Everything the text reading refused stays
    refused: a file that already names the key, a new text that names it. An edit that does not
    apply here (another spelling of the path, a fuzzy match the agent's tool makes) is refused
    when its text could form a key wherever it lands (_could_form_key), else allowed."""
    old = edit.get("old") if isinstance(edit, dict) else None
    new = edit.get("new") if isinstance(edit, dict) else None
    if not isinstance(old, str) or not isinstance(new, str):
        return UNKNOWN.format(path=path, why="The edit carries no old/new text.")
    cur = _read(path)
    if cur is None:
        return UNKNOWN.format(path=path, why="The file cannot be read.")
    cur_keys, ok = guard_keys(cur)
    if not ok or cur_keys:
        return UNKNOWN.format(path=path, why="The file already sets (or may set) the key, and an "
                              "edit's result is not proven here to remove it.")
    if _MENTION.search(new) or _ESCAPE.search(new):
        return REASON.format(key="X4_GUARD", path=path)
    if old == "":
        # The new text is the file (OpenCode) -- or, read as Claude Code reads an empty
        # old_string, goes before it: judged both ways.
        results = [new, new + cur]
    elif old in cur:
        results = [cur.replace(old, new) if edit.get("replace_all") else cur.replace(old, new, 1)]
    else:
        return REASON.format(key="X4_GUARD", path=path) if _could_form_key(new) else ""
    for result in results:
        keys, ok = guard_keys(result)
        if keys or not ok:
            return REASON.format(key=(keys or ["X4_GUARD"])[0], path=path)
    return ""


# --- what an agent may not change in a settings file: the CLASS rules (4.2.0) -------------------
# A settings file's `env` block reaches every hook process (and outranks x4-paths.env: _x4-env.sh,
# _paths._layers), and its `hooks` ARE the guards. Three review rounds of the 4.2.0 release found
# LISTS leaking -- one more variable (BASH_ENV with `exit 0` turned a deny into an allow;
# CLAUDE_CODE_SHELL_PREFIX, BASH_FUNC_*%%, PYTHONUSERBASE, ...), one more hook field (matcher,
# event, async, timeout, type), one more path spelling -- so each rule is a CLASS, judged as a
# NO-CHANGE rule on the RESULTING file. Refused: adding, removing or changing
#   * ANY key of any `env` object, at any depth outside the top-level `permissions` (the user edits
#     env by hand; an agent cannot know which variable a hook, bash or python reads);
#   * a dict key holding `=` (it reaches a child process as the name before it, MEASURED in Node)
#     or starting with a protected-root key's name, at any depth, with the path of keys above it,
#     so renaming its parent (`stash` -> `env`) is a change too;
#   * a GUARD HOOK as a whole: for every hook whose definition runs one of the toolkit's guard
#     scripts, each (event, its group's fields other than `hooks` -- the matcher and any other --,
#     the full hook object -- type, command, timeout, async, ...) the file has now must still be
#     there, as many times as now, so a matcher that matches nothing, a move to another event,
#     async, a tiny timeout, another type, or deleting one of two groups that share a command are
#     all refused. ADDING a hook (a guard script included) is allowed: the most restrictive
#     PreToolUse DECISION wins, so an added hook cannot turn a guard's deny into an allow -- but it
#     is not only a check: a PreToolUse hook returning `updatedInput` rewrites the tool call
#     (F221, OPEN, with every other command an agent adds);
#   * `disableAllHooks` set to a true value where it was not.
# A user's own hooks stay theirs: adding, changing or removing a hook that runs no guard script is
# allowed (an env prefix in one only affects that hook's own process). NOT judged here (F221, OPEN):
# what such an agent-added hook, or a statusLine / apiKeyHelper command, will RUN at the next launch.
# A write that leaves all of the above alone is allowed, so the keys `install.sh --method global`
# writes stay put and every other edit works as before. Tightening only: this runs after the
# X4_GUARD rule and can only turn an allow into a deny.
ROOT_KEYS = ("X4_STAGING_DIRS", "X4_GAME", "X4_REFERENCE", "X4_PROFILE", "X4_SAVES", "X4_MODS",
             "X4_CONFIG", "X4_TOOLKIT", "X4_EXTENSIONS", "X4_DOCUMENTS")
#: A hook definition that runs one of the toolkit's guard scripts (every rendered tree, and the
#: neutral harness), searched in the hook object's JSON text, so any field naming it counts.
_OWN_HOOK = re.compile(r"(?i)(?:^|[/\\\s\"'])(?:\.claude|\.codex|\.opencode)[/\\]+hooks[/\\]|x4_harness|x4guard")
#: Free text that may hold a protected setting: an env block, hooks, disableAllHooks, a root key.
_TEXT_NAME = re.compile(r"(?i)(?<![A-Za-z0-9_])(env|hooks|disableallhooks|(?:"
                        + "|".join(ROOT_KEYS) + r")[A-Za-z0-9_]*)(?![A-Za-z0-9_])")
ENV_REASON = ("BLOCKED: this write would add, remove or change a setting in the `env` block of {path}, a "
              "Claude Code settings file. That block reaches every hook process -- the folders the X4 "
              "guards protect, the shell and python they run under -- so an agent may not change any "
              "key in it. Ask the user to edit `env` in this file by hand. Make your own edit without "
              "touching `env`.")
KEY_REASON = ("BLOCKED: this write would add, remove or move {key} in {path}, a Claude Code settings "
              "file, as a key that can become a hook's environment variable. Ask the user to make this "
              "change by hand.")
HOOK_REASON = ("BLOCKED: this write would remove or change an X4 guard hook in {path} ({detail}). A "
               "guard hook is protected as a whole -- its event, matcher, type, command, timeout and "
               "every other field -- because any change can stop it judging. Your own hooks stay "
               "yours to edit; ask the user to change a guard hook.")
DISABLE_REASON = ("BLOCKED: this write would set disableAllHooks in {path}, which switches every hook, "
                  "the X4 guards included, off. Only the user may do that, by hand.")
ROOT_UNKNOWN = ("BLOCKED: the guard cannot tell whether this write changes `env`, an X4 guard hook or "
                "disableAllHooks in {path}, a Claude Code settings file. {why}")
JSONC = ("The file as it is now is not plain JSON (a comment or a trailing comma, say) and holds such "
         "settings, so no edit of it can be re-judged safely. Ask the user to make this edit by hand, "
         "or to convert the file to plain JSON first.")


def _key_protected(k: str) -> bool:
    name = k.strip().upper()
    return "=" in k or any(name.startswith(r) for r in ROOT_KEYS)


def _truthy(v) -> bool:
    return v is True or v == 1 or (isinstance(v, str) and v.strip().lower() in ("true", "1", "yes"))


def _j(v) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False)


def _no_constant(name):
    raise ValueError(f"{name} is not JSON")


def loads(text: str):
    """JSON as Claude Code's JSON.parse reads it: NaN, Infinity and -Infinity -- which Python's json
    accepts -- are refused (4.2.0 review 4, MEASURED: appended to a guard-hooked file they were
    allowed, while the host would stop loading the whole file, its guard hooks included)."""
    return json.loads(text, parse_constant=_no_constant)


def root_settings(text: str):
    """(sorted protected facts, disableAllHooks paths set true, decidable?) for settings TEXT.
    Text that is not JSON is decidable only when it cannot hold such a setting at all."""
    try:
        doc = loads(text) if text.strip() else {}
    except ValueError:
        if _TEXT_NAME.search(text) or _ESCAPE.search(text):
            return [], set(), False
        return [], set(), True
    facts, disabled = [], set()
    if isinstance(doc, dict) and isinstance(doc.get("hooks"), dict):
        for event, groups in doc["hooks"].items():
            # An event's value or a group's `hooks` that is not a LIST is a shape Claude Code does
            # not read as hooks (review 4, MEASURED: wrapped here, the hook read as "unchanged").
            # The shape is part of each guard hook's fact, so the list -> object edit is a change.
            shape = "" if isinstance(groups, list) else "event-not-a-list"
            for group in (groups if isinstance(groups, list) else [groups]):
                if not isinstance(group, dict):
                    continue
                rest = {k: v for k, v in group.items() if k != "hooks"}
                hooks = group.get("hooks")
                gshape = shape or ("" if isinstance(hooks, list) else "hooks-not-a-list")
                for h in (hooks if isinstance(hooks, list) else [hooks]):
                    if _OWN_HOOK.search(_j(h)):
                        facts.append(("hook", str(event), _j(rest), _j(h), gshape))
    stack = [((), doc)]
    while stack:
        where, o = stack.pop()
        if isinstance(o, dict):
            for k, v in o.items():
                if not where and k == "permissions":
                    continue    # rule STRINGS set nothing (USER, 2026-10-06)
                ks = str(k)
                if ks.lower() == "env":
                    facts.append(("env", where + (ks,), _j(v)))
                elif _key_protected(ks):
                    facts.append(("key", where + (ks,), _j(v)))
                if ks.lower() == "disableallhooks" and _truthy(v):
                    disabled.add(where + (ks,))
                stack.append((where + (ks,), v))
        elif isinstance(o, list):
            stack.extend((where + (str(i),), x) for i, x in enumerate(o))
    return sorted(facts), disabled, True


def _root_delta(before: str, after: str, path: str) -> str:
    """'' when AFTER leaves every protected setting exactly as BEFORE has it, else the refusal."""
    bf, bd, bok = root_settings(before)
    af, ad, aok = root_settings(after)
    if not bok:
        return ROOT_UNKNOWN.format(path=path, why=JSONC)
    if not aok:
        return ROOT_UNKNOWN.format(path=path, why="The result would not be plain JSON. Write plain "
                                   "JSON, leaving those settings as they are.")
    if ad - bd:
        return DISABLE_REASON.format(path=path)
    # Guard hooks: every one the file has now must still be there, whole. An ADDED hook is allowed:
    # the most restrictive PreToolUse decision wins, so it cannot lift a guard's deny (a REPLACED
    # guard hook is a removal, and refused). What an added hook RUNS -- one returning `updatedInput`
    # rewrites the tool call -- is not judged here (F221, OPEN).
    lost = Counter(f for f in bf if f[0] == "hook") - Counter(f for f in af if f[0] == "hook")
    if lost:
        hook = sorted(lost.elements())[0]
        return HOOK_REASON.format(path=path, detail=f"{hook[1]}: {hook[3][:160]}")
    af = [f for f in af if f[0] != "hook"]
    bf = [f for f in bf if f[0] != "hook"]
    if af != bf:
        ca, cb = Counter(af), Counter(bf)
        diff = sorted((ca - cb).elements()) + sorted((cb - ca).elements())
        kinds = {f[0] for f in diff}
        if "env" in kinds:
            return ENV_REASON.format(path=path)
        return KEY_REASON.format(key=", ".join(sorted({str(f[1][-1]) for f in diff})), path=path)
    return ""


INEXACT = ("This change cannot be applied here exactly as written (or it reaches the file through a "
           "path spelling the adapter did not resolve), and the file holds such settings, so its "
           "result cannot be judged. Read the file again and edit its exact text, or Write the whole "
           "file -- a Write is judged on the file it leaves.")


def _inexact(cur: str, text: str, path: str) -> str:
    """A change that cannot be applied to CUR exactly -- an edit's old text that is not there (Claude
    Code's Edit matches curly quotes to straight ones, OpenCode's edit has whitespace-fuzzy
    replacers), a patch not rebuilt -- may still land somewhere. Refused when the file holds a
    protected setting now: what survives cannot be proven (4.2.0 review I1 for patches; review 4,
    MEASURED, for edits: `"matcher": "NoSuchTool"` and `"async": true` name nothing protected).
    Else judged on its TEXT."""
    bf, bd, bok = root_settings(cur)
    if not bok:
        return ROOT_UNKNOWN.format(path=path, why=JSONC)
    if bf or bd:
        return ROOT_UNKNOWN.format(path=path, why=INEXACT)
    return _root_text(text, path)


def _root_text(text: str, path: str) -> str:
    """A fragment whose landing place is unknown (an edit that does not apply here): refused when
    it names such a setting or holds a JSON escape that could spell one."""
    if _TEXT_NAME.search(text) or _ESCAPE.search(text):
        return ROOT_UNKNOWN.format(path=path, why="This text does not apply to the file as it is now, "
                                   "and it names such a setting. Read the file again and edit what is there.")
    return ""


_PATCH_HEAD = (("*** Update File: ", "update"), ("*** Add File: ", "add"), ("*** Delete File: ", "delete"))


def _adapter_ops(written: str, grammar: str):
    """The adapter's own reading of a patch -- patch_paths.parse_patch (Codex) or
    parse_patch_opencode -- from the copy beside this file, or None when there is none or it
    refuses the text (then nothing is rebuilt)."""
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "patch_paths.py")
    if not os.path.isfile(src):
        src = os.path.join(here, "..", "adapters", "patch_paths.py")      # the agent/ source tree
        if not os.path.isfile(src):
            return None
    try:
        spec = importlib.util.spec_from_file_location("x4_settings_patch_paths", src)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        parse = mod.parse_patch_opencode if grammar == "opencode" else mod.parse_patch
        return parse(written)
    except Exception:
        return None


def _patch_result(cur: str, written: str, targets, grammar: str = "codex"):
    """(result, why): the file after the ONE patch header the ADAPTER resolved to it (its own
    resolver, codex._patch_calls, passes [[op, header]] as x4_patch_targets -- this never resolves a
    path itself), or (None, '') when it cannot be rebuilt EXACTLY, or (None, refusal) when the
    patch reaches the file through more than one header."""
    if not isinstance(targets, list) or not targets:
        return None, ""
    if len(targets) > 1:
        return None, "This patch reaches the file through more than one header; make one change per file."
    t = targets[0]
    if not (isinstance(t, (list, tuple)) and len(t) == 2 and all(isinstance(x, str) for x in t)):
        return None, ""
    op, header = t
    if op not in ("update", "add", "delete"):
        return None, ""                       # a move: not modelled
    blocks, block = [], None
    for ln in written.splitlines():
        # Codex reads a header on the TRIMMED line, but inside an Update hunk on the right-trimmed
        # line only: an indented `  *** Update File: x` there is a context line (patch_paths._Codex).
        probe = ln.rstrip() if block is not None and block["op"] == "update" else ln.strip()
        head = next(((o, probe[len(m):]) for m, o in _PATCH_HEAD if probe.startswith(m)), None)
        if head:
            block = {"op": head[0], "path": head[1], "body": [], "move": None}
            blocks.append(block)
        elif probe in ("*** Begin Patch", "*** End Patch"):
            block = None
        elif block is not None:
            if block["op"] == "update" and not block["body"] and probe.startswith("*** Move to: "):
                block["move"] = probe[len("*** Move to: "):]
            else:
                block["body"].append(ln)
    # Cross-check with the ADAPTER's own parser (the grammar it read the patch with): any
    # difference in which files this patch touches means this split is not the agent's.
    mine_ops = []
    for b in blocks:
        if b["op"] == "update" and b["move"] is not None:
            mine_ops += [("move_from", b["path"]), ("move_to", b["move"])]
        else:
            mine_ops.append((b["op"], b["path"]))
    theirs = _adapter_ops(written, grammar)
    if theirs is None or [tuple(x) for x in theirs] != mine_ops:
        return None, ""
    mine = [b for b in blocks if b["path"] == header]
    if len(mine) != 1 or mine[0]["op"] != op or mine[0]["move"] is not None:
        return None, ""
    body = mine[0]["body"]
    if op == "delete":
        return "", ""
    if op == "add":
        if any(not ln.startswith("+") for ln in body):
            return None, ""
        return "".join(ln[1:] + "\n" for ln in body), ""
    out, trailing_nl, chunks, ch = cur.splitlines(), cur.endswith("\n"), [], None
    for ln in body:
        if ln.startswith("@@"):
            if ln.strip() != "@@":
                return None, ""               # a context header steers Codex's search: not modelled
            ch = []
            chunks.append(ch)
        elif ln.strip() == "*** End of File":
            return None, ""                   # anchors the chunk at the end: not modelled
        elif ln[:1] in (" ", "-", "+") or ln == "":
            if ch is None:
                ch = []
                chunks.append(ch)
            ch.append(ln or " ")
        else:
            return None, ""
    pos = 0
    for ch in chunks:
        old = [x[1:] for x in ch if x[0] in " -"]
        new = [x[1:] for x in ch if x[0] in " +"]
        if not old:
            return None, ""
        at = next((i for i in range(pos, len(out) - len(old) + 1) if out[i:i + len(old)] == old), None)
        if at is None:
            return None, ""
        out[at:at + len(old)] = new
        pos = at + len(new)
    return "\n".join(out) + ("\n" if trailing_nl else ""), ""


def judge_roots(tool_input: dict) -> str:
    """'' to allow, else the refusal: the class rules above, for every channel judge() reads
    (content, Edit/MultiEdit, an OpenCode x4_edit, an apply_patch or opaque x4_written)."""
    path = tool_input.get("file_path") or tool_input.get("filePath") or ""
    if not is_settings(path):
        return ""
    cur = _read(path)
    if cur is None:
        return UNKNOWN.format(path=path, why="The file cannot be read.")
    if "x4_edit" in tool_input:
        edit = tool_input.get("x4_edit")
        old = edit.get("old") if isinstance(edit, dict) else None
        new = edit.get("new") if isinstance(edit, dict) else None
        if not isinstance(old, str) or not isinstance(new, str):
            return UNKNOWN.format(path=path, why="The edit carries no old/new text.")
        if old == "":
            results = [new, new + cur]
        elif old in cur:
            results = [cur.replace(old, new) if edit.get("replace_all") else cur.replace(old, new, 1)]
        else:
            return _inexact(cur, new, path)
        for result in results:
            why = _root_delta(cur, result, path)
            if why:
                return why
        return ""
    if "x4_written" in tool_input:
        written = tool_input.get("x4_written")
        if not isinstance(written, str):
            return UNKNOWN.format(path=path, why="The caller passed no written text.")
        if tool_input.get("x4_patch") is True:
            result, why = _patch_result(cur, written, tool_input.get("x4_patch_targets"),
                                        str(tool_input.get("x4_patch_grammar") or "codex"))
            if why:
                return ROOT_UNKNOWN.format(path=path, why=why)
            if result is not None:
                return _root_delta(cur, result, path)
        # Not rebuilt (or not a patch): refused when the file holds a protected setting now --
        # what survives cannot be proven (4.2.0 review I1) -- or the text names one.
        return _inexact(cur, written, path)
    if "content" in tool_input:
        result = tool_input.get("content")
        if not isinstance(result, str):
            return UNKNOWN.format(path=path, why="The content is not text.")
        return _root_delta(cur, result, path)
    edits = tool_input.get("edits")
    if not isinstance(edits, list):
        edits = [tool_input]
    result = _apply(cur, edits)
    if result is None:
        for e in edits:
            new = e.get("new_string") if isinstance(e, dict) else None
            why = _inexact(cur, new if isinstance(new, str) else "", path)
            if why:
                return why
        return ""
    return _root_delta(cur, result, path)


def judge(tool_input: dict) -> str:
    """'' to allow, else the refusal: the X4_GUARD rule, then the protected-root rule."""
    return _judge_guard(tool_input) or judge_roots(tool_input)


def _judge_guard(tool_input: dict) -> str:
    """'' to allow, else the refusal (the X4_GUARD rule)."""
    path = tool_input.get("file_path") or tool_input.get("filePath") or ""
    if not is_settings(path):
        return ""
    if "x4_edit" in tool_input:                    # one replacement: applied, the RESULT judged
        return _judge_edit(path, tool_input.get("x4_edit"))
    if "x4_written" in tool_input:                 # a patch / opaque write: its TEXT is all we see
        written = tool_input.get("x4_written")
        if not isinstance(written, str):
            return UNKNOWN.format(path=path, why="The caller passed no written text (x4guard check: "
                                  "--command <the text>).")
        # Patch semantics only when the CALLER says it is a patch (FX-G6 / reviewer K M1): a
        # text that merely CONTAINS `*** Begin Patch` / `*** Add File:` is not one.
        if tool_input.get("x4_patch") is True and _add_only(written):
            # FX-G5 item 10: an add-only patch REPLACES the file (or fails), so the old content
            # cannot survive it -- only its own text matters (checked below).
            cur = ""
        else:
            cur = _read(path)
        if cur is None:
            return UNKNOWN.format(path=path, why="The file cannot be read.")
        cur_keys, ok = guard_keys(cur)
        if not ok or cur_keys:
            return UNKNOWN.format(path=path, why="The file already sets (or may set) the key, and a "
                                  "patch's result cannot be proven to remove it.")
        if any(k in written.upper() for k in KEYS) or _ESCAPE.search(written):
            return REASON.format(key="X4_GUARD", path=path)
        return ""
    if "content" in tool_input:
        result = tool_input.get("content")
        if not isinstance(result, str):
            return UNKNOWN.format(path=path, why="The content is not text.")
    else:
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            edits = [tool_input]
        cur = _read(path)
        if cur is None:
            return UNKNOWN.format(path=path, why="The file cannot be read.")
        result = _apply(cur, edits)
        if result is None:
            # The edit does not apply to the file as READ here -- but the tool may see a
            # different file (it changed in between, another spelling of the path). FX-G4 /
            # reviewer H-M1: the text being written is then judged on its own.
            for e in edits:
                new = e.get("new_string") if isinstance(e, dict) else None
                if isinstance(new, str) and (_MENTION.search(new) or _ESCAPE.search(new)):
                    return REASON.format(key="X4_GUARD", path=path)
            return ""
    keys, ok = guard_keys(result)
    if not ok:
        return UNKNOWN.format(path=path, why="The result is not valid JSON and mentions the key.")
    if keys:
        return REASON.format(key=keys[0], path=path)
    return ""


def main(argv: list) -> int:
    if argv[1:2] == ["--scan"]:
        for p in argv[2:]:
            text = _read(p)
            if not text:
                continue
            keys, ok = guard_keys(text)
            if keys or not ok:
                print(p)
        return 0
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError:
        return 3
    ti = payload.get("tool_input") if isinstance(payload, dict) else None
    ti = dict(ti) if isinstance(ti, dict) else {}
    if argv[1:2] == ["--path"] and len(argv) > 2:  # the caller's CANONICAL path (Windows aliases)
        ti["file_path"] = argv[2]
    out = judge(ti)
    if out:
        sys.stdout.buffer.write(out.encode("utf-8", "replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
