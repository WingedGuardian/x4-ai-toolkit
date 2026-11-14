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
        doc = json.loads(text) if text.strip() else {}
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


# --- the protected-ROOT and STARTUP keys, and the hooks block (4.2) ---------------------------
# The guards take their roots from the X4_* keys below, and the ENVIRONMENT outranks x4-paths.env
# (_x4-env.sh, _paths._layers); every hook runs under bash and python, which read their own startup
# variables. A settings `env` block reaches every hook process, so an agent that writes one could
# move reference/ or the game root, name a game folder in X4_STAGING_DIRS, or make bash source a
# file that ends the hook before it speaks (BASH_ENV with `exit 0` turned a deny into an allow,
# MEASURED by the 4.2.0 review) -- at the next launch.
#
# A DELTA rule, unlike X4_GUARD's: the global install mode itself writes X4_TOOLKIT, X4_GAME,
# X4_REFERENCE, X4_PROFILE, X4_MODS and X4_EXTENSIONS into settings.json, so a write that leaves
# every such setting exactly where and as it is stays allowed. Refused: adding, removing, moving or
# changing
#   * a dict key outside the top-level `permissions` that holds `=` or starts with a root key's
#     name, at ANY depth (a key `X4_REFERENCE=C:/x` reaches a child process as X4_REFERENCE,
#     MEASURED in Node by the 4.2.0 review; a parent renamed `stash` -> `env` later makes a nested
#     one live, so the path of keys above it is part of the setting), or a startup variable's name
#     directly in an `env` block;
#   * a string (outside `permissions`) that SETS one in a spelling recognised below: `KEY=`, `KEY+=`,
#     `export|declare|typeset|local|readonly [-opts] KEY`, PowerShell `$env:KEY =`,
#     `Set-Item env:KEY`, `[Environment]::SetEnvironmentVariable('KEY'`, fish `set -x KEY`,
#     `printf -v KEY`, `read ... KEY`. NOT recognised (an accepted residual): a wrapper script that
#     sets it, `eval`/encoded text, `source` of another file, any other program's own way;
#   * `disableAllHooks` set to a true value where it was not;
#   * a hook `command` running one of the toolkit's guard scripts (`.claude/hooks/...`) that was
#     there before and is not after, byte for byte (removing or emptying `hooks`, or changing such a
#     command: a suffix like `> /dev/null` silences its verdict). A user's own hooks stay editable.
# Reading a key (`$X4_TOOLKIT/...`, `$env:X4_TOOLKIT`) sets nothing. Tightening only: it runs after
# the X4_GUARD rule and can only turn an allow into a deny.
ROOT_KEYS = ("X4_STAGING_DIRS", "X4_GAME", "X4_REFERENCE", "X4_PROFILE", "X4_SAVES", "X4_MODS",
             "X4_CONFIG", "X4_TOOLKIT", "X4_EXTENSIONS", "X4_DOCUMENTS")
#: Startup variables of the shells and the python every hook runs under.
STARTUP_KEYS = ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "PATH", "PYTHONSTARTUP", "PYTHONPATH",
                "PYTHONHOME")
_ROOTS_RX = "(?i:" + "|".join(ROOT_KEYS) + ")[A-Za-z0-9_]*"
_STARTUP_RX = "|".join(sorted(STARTUP_KEYS, key=len, reverse=True))
#: A protected NAME in free text: a root key (any case, any suffix -- Windows names are
#: case-insensitive), a startup variable in its own upper case (`path` and `env` are ordinary
#: words, and `env` is the settings block itself), or disableAllHooks.
_TEXT_NAME = re.compile("(?<![A-Za-z0-9_])(" + _ROOTS_RX + "|(?:" + _STARTUP_RX
                        + ")(?![A-Za-z0-9_])|(?i:disableallhooks))")
#: The name a SETTER sets. Shell spellings: roots any case, startup names upper case. PowerShell
#: and .NET spellings: every name in any case (`$env:Path = ...` is how PowerShell sets PATH).
_N_SH = "(" + _ROOTS_RX + "|(?:" + _STARTUP_RX + "))"
_N_PS = "(" + _ROOTS_RX + "|(?i:" + _STARTUP_RX + "))"
_END = "(?![A-Za-z0-9_])"
_SETTERS = [re.compile(p) for p in (
    "(?<![A-Za-z0-9_$:-])" + _N_SH + r"\+?=",
    r"\b(?:export|declare|typeset|local|readonly)\s+(?:-[A-Za-z]+\s+)*" + _N_SH + _END,
    r"(?i:\$env:)" + _N_PS + r"\s*\+?=",
    r"(?i:\b(?:Set-Item|New-Item|si|ni)\s+(?:-Path\s+)?['" + '"' + r"]?env:)" + _N_PS,
    r"(?i:SetEnvironmentVariable\s*\(\s*['" + '"' + r"])" + _N_PS,
    r"\bset\s+(?:-[A-Za-z]+\s+)+" + _N_SH + _END,
    r"\bprintf\s+-v\s+" + _N_SH + _END,
    r"\bread\b[^;&|\n]*?(?<![A-Za-z0-9_$:])" + _N_SH + _END,
)]
ROOT_REASON = ("BLOCKED: this write would change {key} in {path}, a Claude Code settings file whose "
               "`env` block, hooks and hook commands reach every hook -- they decide which folders the "
               "X4 guards protect, which shell and python run them, and whether they run at all, so an "
               "agent may not add, remove or change them there. The user sets these with the installer, "
               "in x4-paths.env or by hand. Make the settings edit without touching them.")
ROOT_UNKNOWN = ("BLOCKED: the guard cannot tell whether this write changes a protected setting "
                "(X4_GAME, X4_REFERENCE, X4_STAGING_DIRS, PATH, BASH_ENV, a hook, ...) in {path}, a "
                "Claude Code settings file whose `env` and hooks reach every hook. {why} Write the whole "
                "file with your file-edit tool (Write), or leave those settings as they are.")


#: A hook command that runs one of the toolkit's own guard scripts (every rendered tree, and the
#: neutral harness). Only these are protected: a user's own hooks stay theirs to edit.
_OWN_HOOK = re.compile(r"(?i)(?:^|[/\\\s\"'])(?:\.claude|\.codex|\.opencode)[/\\]hooks[/\\]|x4_harness|x4guard")


def _key_protected(k: str, where: tuple) -> bool:
    """A dict key that sets a protected variable: `=` in it or a root-key name (anywhere -- a
    parent renamed to `env` later makes it live), or a startup name directly in an `env` block."""
    name = k.strip().upper()
    if "=" in k or any(name.startswith(r) for r in ROOT_KEYS):
        return True
    return name in STARTUP_KEYS and bool(where) and where[-1].lower() == "env"


def _setter(s: str):
    for rx in _SETTERS:
        m = rx.search(s)
        if m:
            return next(g for g in m.groups() if g).upper()
    return None


def _truthy(v) -> bool:
    return v is True or v == 1 or (isinstance(v, str) and v.strip().lower() in ("true", "1", "yes"))


def root_settings(text: str):
    """(facts, hook commands, disableAllHooks paths set true, decidable?) for settings-file TEXT.
    Unparseable text is decidable only when it cannot name a protected setting or a hook at all."""
    try:
        doc = json.loads(text) if text.strip() else {}
    except ValueError:
        if _TEXT_NAME.search(text) or _ESCAPE.search(text) or re.search(r"(?i)\bhooks\b", text):
            return [], set(), set(), False
        return [], set(), set(), True
    facts, commands, disabled = [], set(), set()
    stack = [((), doc)]
    while stack:
        where, o = stack.pop()
        if isinstance(o, dict):
            for k, v in o.items():
                if not where and k == "permissions":
                    continue    # rule STRINGS set nothing (USER, 2026-10-06)
                ks = str(k)
                if _key_protected(ks, where):
                    facts.append(("key", where + (ks,), ks.split("=")[0].strip().upper() or ks,
                                  json.dumps(v, sort_keys=True)))
                if ks.lower() == "disableallhooks" and _truthy(v):
                    disabled.add(where + (ks,))
                if ks == "command" and isinstance(v, str) and where[:1] == ("hooks",) \
                        and _OWN_HOOK.search(v):
                    commands.add(v)
                stack.append((where + (ks,), v))
        elif isinstance(o, list):
            stack.extend((where + (str(i),), x) for i, x in enumerate(o))
        elif isinstance(o, str):
            name = _setter(o)
            if name:
                facts.append(("set", where, name, o))
    return sorted(facts), commands, disabled, True


def _root_delta(before: str, after: str, path: str) -> str:
    """'' when AFTER sets every protected setting exactly where and as BEFORE does, else the refusal."""
    bf, bc, bd, bok = root_settings(before)
    af, ac, ad, aok = root_settings(after)
    if not aok:
        return ROOT_UNKNOWN.format(path=path, why="The result is not valid JSON and may name one.")
    if not bok:
        # The file as it is now cannot be read as JSON (a comment, a trailing comma) and names a
        # protected setting: what the write keeps of it cannot be compared (4.2.0 review M4).
        return ROOT_UNKNOWN.format(path=path, why="The file as it is now is not valid JSON and names "
                                   "a protected setting, so what this write keeps of it is unknown.")
    if af != bf:
        changed = sorted({f[2] for f in set(af) ^ set(bf)})
        return ROOT_REASON.format(key=", ".join(changed), path=path)
    if ad - bd:
        return ROOT_REASON.format(key="disableAllHooks (true switches every hook off)", path=path)
    if bc - ac:
        return ROOT_REASON.format(key="the hooks (a hook command would be removed: "
                                  + sorted(bc - ac)[0][:120] + ")", path=path)
    return ""


def _root_text(text: str, path: str) -> str:
    """A fragment whose landing place is unknown (an edit that does not apply here): refused when
    it names a protected setting or holds a JSON escape that could spell one."""
    m = _TEXT_NAME.search(text)
    if m:
        return ROOT_REASON.format(key=m.group(1), path=path)
    if _ESCAPE.search(text):
        return ROOT_UNKNOWN.format(path=path, why="The text holds a JSON backslash-u escape.")
    return ""


def _same(header_path: str, target: str) -> bool:
    """Does a patch header's path (relative to the agent's cwd, unknown here) name TARGET?"""
    q = norm(header_path.strip())
    while q.startswith("./"):
        q = q[2:]
    return bool(q) and (q == target or target.endswith("/" + q))


def _patch_update_result(cur: str, written: str, path: str):
    """The file after an apply_patch's Update hunks for PATH, applied in memory, or None when it
    cannot be reconstructed EXACTLY (an Add, Delete or Move of this file, a context header or an
    end-of-file anchor, a chunk with no old lines, old lines found nowhere in order, no hunk for
    it at all). Exact matching only: Codex tries an exact match first, so where this finds one,
    Codex lands there too; where it does not, Codex might match fuzzily where this cannot predict."""
    target = norm(path)
    blocks, cur_block = [], None
    for ln in written.splitlines():
        if ln.startswith("*** Update File: "):
            cur_block = {"path": ln[len("*** Update File: "):], "body": []}
            blocks.append(cur_block)
        elif ln.startswith("*** Add File: ") or ln.startswith("*** Delete File: "):
            if _same(ln.split(": ", 1)[1], target):
                return None
            cur_block = None
        elif ln.startswith("*** Move to: "):
            if cur_block is not None:
                cur_block["move"] = True
        elif ln.startswith("*** Begin Patch") or ln.startswith("*** End Patch"):
            cur_block = None
        elif cur_block is not None:
            cur_block["body"].append(ln)
    mine = [b for b in blocks if _same(b["path"], target)]
    if not mine or any(b.get("move") for b in mine):
        return None
    out = cur.splitlines()
    trailing_nl = cur.endswith("\n")
    for b in mine:
        chunks, ch = [], None
        for ln in b["body"]:
            if ln.startswith("@@"):
                if ln.strip() != "@@":
                    return None          # a context header steers Codex's search: not modelled
                ch = []
                chunks.append(ch)
            elif ln.strip() == "*** End of File":
                return None              # anchors the chunk at the end: not modelled
            elif ch is None:
                return None
            elif ln[:1] in (" ", "-", "+"):
                ch.append(ln)
            elif ln == "":
                ch.append(" ")
            else:
                return None
        pos = 0
        for ch in chunks:
            old = [x[1:] for x in ch if x[0] in " -"]
            new = [x[1:] for x in ch if x[0] in " +"]
            if not old:
                return None
            at = next((i for i in range(pos, len(out) - len(old) + 1) if out[i:i + len(old)] == old), None)
            if at is None:
                return None
            out[at:at + len(old)] = new
            pos = at + len(new)
    return "\n".join(out) + ("\n" if trailing_nl else "")


def judge_roots(tool_input: dict) -> str:
    """'' to allow, else the refusal: the protected-setting DELTA rule above, for every channel
    judge() reads (content, Edit/MultiEdit, an OpenCode x4_edit, an apply_patch or opaque x4_written)."""
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
            return _root_text(new, path)
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
            if _add_only(written):           # the added lines ARE the new file
                added = [ln[1:] for ln in written.splitlines()
                         if ln.startswith("+") and not ln.startswith("***")]
                return _root_delta(cur, "\n".join(added), path)
            result = _patch_update_result(cur, written, path)
            if result is not None:
                return _root_delta(cur, result, path)
        # Not reconstructible, or not a patch: refused when the file holds a protected setting or
        # a hook now -- what survives cannot be proven (4.2.0 review I1) -- or the text names one.
        bf, bc, bd, bok = root_settings(cur)
        if not bok or bf or bc or bd:
            return ROOT_UNKNOWN.format(path=path, why="This change cannot be applied here exactly as "
                                       "written, and the file holds protected settings it might change.")
        return _root_text(written, path)
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
            if isinstance(new, str):
                why = _root_text(new, path)
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
