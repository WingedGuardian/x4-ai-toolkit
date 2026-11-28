"""No detached launches: find code or commands that start a process DETACHED (Windows DETACHED_PROCESS, 0x8).

A detached process has no console, so every console program it starts without CREATE_NO_WINDOW gets a NEW VISIBLE
window. MEASURED on Windows: a helper started with NO_WINDOW | NEW_PROCESS_GROUP | DETACHED_PROCESS read processes
through powershell.exe once a second, and the user got a console pop-up every second for a whole run.
CREATE_NO_WINDOW (0x08000000, optionally | CREATE_NEW_PROCESS_GROUP 0x200) gives a hidden console that the child's own
children inherit, and the child still outlives the caller, so detaching is never needed for that.

Scope: only DETACHED_PROCESS. A new visible console (CREATE_NEW_CONSOLE, Start-Process, a terminal the user opens) is
deliberate, visible work and is NOT reported.

Game- and agent-neutral; a toolkit's guard decides what a finding means. Every judge returns the offending statement
(text) or None:
- judge(tool, inp, exempt=EXEMPT, cwd=""): a Claude Code-shaped call. Write / Edit / MultiEdit / NotebookEdit on code
  files (an edit is judged on the file AFTER the edit, statement by statement around the changed lines; a relative
  path is read against CWD); Bash / PowerShell / Monitor commands, read by judge_shell AND judge_words.
- judge_shell(command): inline -c / -e / -Command scripts after an interpreter (bare, quoted path or variable),
  heredoc, here-string and <<< bodies fed to an interpreter or written to a code file, and quoted text written to a
  code file.
- judge_words(command, shell=""): the same scripts found by shell WORDS, so any quoting reaches them (a Codex argv
  list), and so does an interpreter after env / timeout / nohup / an assignment, or given as a variable ("$PY",
  & $env:PY). Nested shells are read in their own quoting, three levels deep.
- judge_patch(text, cwd=""): an apply_patch text (Codex and OpenCode grammar). An added file whole; an updated file in
  the file on disk around its added lines; a hunk that cannot be placed, on its own lines. Removed lines are gone.
- judge_text(path, text, spans=None): TEXT as the content of code file PATH.
`exempt` (judge, judge_patch, judge_text): file paths ending in one of these "/"-separated entries are never judged
(case-sensitive). It REPLACES the default: a toolkit passes `EXEMPT + (its own guard and test files,)`. It applies to
the files a call writes, not to a file a shell command writes (`cat > path <<EOF` is judged whatever the path).

Reported: DETACHED_PROCESS USED as a flag (not its definition or a mask test), a creationflags value with bit 0x8,
Node `detached: true`.

Known limits:
- A flag built at run time (arithmetic, a variable holding 8, a helper's keyword argument) or a launch inside a script
  the command only names is not seen; code generated inside Python string literals is not read.
- Helpers started with pythonw or a .pyw are not reported (also console-less; some legitimate helpers run that way).
- Size: judge() cuts a Write's content at 4,000,000 characters, and an Edit to a file over 4,000,000 bytes is judged
  on its new text alone. Nothing else is capped (an Edit's new text, NotebookEdit, judge_text, judge_patch). The
  words of a command past its first 20,000 are read as text only.
- A variable command word is read as an interpreter, so its -e / -p / -c value is judged as code:
  `"$RG" -e 'creationflags=8' src` is reported (a search for the flag text through a variable).
- A patch with more than 2,000 hunks, or whose hunks take over 5 s to place in the file, is judged hunk by hunk.
- An interpreter's option is assumed to take at most one value (`python -B script.py -c` reads -c as python's).
- Node `detached: true` is reported anywhere in JS/TS code, a test asserting it is absent included.
- An Edit to a shell script judges a 400-line window, so a script already holding a bad launch can be reported for an
  unrelated edit near it.

Errors are not caught here: a caller that must fail open catches them and records that the call was not judged.

Provenance: the statement detector (blank, statements, first_bad, judge_shell and the file half of judge) is the
reviewed packet WingedGuardian/skyrimvr-save-guard-ci @23918cc (no_detached_launch/no-detached-launch.py); the word
and patch readers are from the Skyrim AI Assistant Toolkit v4.3.0 (tools/skyguard/detached.py), after four review
rounds. Changed here: `exempt` is the caller's, judge() takes `cwd`, and judge() also reads shell words.
"""
from __future__ import annotations

import io
import json
import os
import re
import shlex
import tokenize

NAMES = r"(?:DETACHED_PROCESS)"
NAMED = re.compile(r"(?<![\w.])(?:[A-Za-z_]\w*\.)*" + NAMES + r"\b")
LAUNCH_CONTEXT = re.compile(r"creation_?flags|Popen|CreateProcess|spawn", re.I)  # Go: CreationFlags:
FLAGS = re.compile(r"creation_?flags[\"'\]]*\s*(?:[=:(]|\|=)\s*", re.I)
QUOTED = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'")
QUOTED_ML = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'[^']*'")  # shell quoting: may span lines; '...' has no escapes
LITERAL = re.compile(r"(~\s*\(?\s*)?(?<![\w.])(0[xX][0-9a-fA-F]+|\d+)(?![\w.])")
NODE_DETACHED = re.compile(r"""(?:\bdetached["']?\s*:|\.detached\s*=)\s*true\b""")
CODE_FILES = (".py", ".pyw", ".pyi", ".ps1", ".psm1", ".psd1", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".cs",
              ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".ahk", ".bat", ".cmd", ".sh", ".bash", ".zsh", ".vbs",
              ".rs", ".go", ".java", ".ipynb")
# Paths never judged: this detector and its tests must stay editable. A toolkit passes its own list as `exempt`.
EXEMPT = ("modding_harness/detached.py", "tests/test_detached.py")
INTERP_NAMES = r"(?:python[\d.]*w?|py|pythonw|node|deno|bun|pwsh|powershell|bash|sh|cmd)(?:\.exe)?"
# an interpreter word: bare (python) or a quoted path ("C:/Program Files/Python313/python.exe"); a variable ($PY,
# ${PY}, "$X4_PY", PowerShell & $py) counts only as the COMMAND word, never elsewhere (cat > "$NOTES" <<EOF is prose)
INTERP_BASE = (r"(?:(?<![\w.$-])" + INTERP_NAMES + r"(?![\w-])"
               r"|[\"'](?:[^\"'\n]*[\\/])?" + INTERP_NAMES + r"[\"'])")
VAR = r"\"?\$\{?[A-Za-z_]\w*\}?\"?"
CMD_POS = r"(?:^|[\n|;(&]|\b(?:do|then|else|exec)\b)\s*"
INTERP_TOKEN = r"(?:" + INTERP_BASE + r"|" + CMD_POS + VAR + r")"
INTERP = re.compile(r"(?im)" + INTERP_TOKEN)  # heredoc headers, here-string tails ("$PY" - <<EOF counts)
PIPE_TO_INTERP = re.compile(r"(?i)\|\s*&?\s*(?:" + INTERP_BASE + r"|" + VAR + r")(?:\s+-{1,2}[\w.-]+)*(?:\s+-)?\s*$")
HERE3 = re.compile(r"(?im)" + INTERP_TOKEN + r"[^|;&\n]*?<<<\s*(\"(?:[^\"\\]|\\.)*\"|'[^']*')")
CODE_PATH = r"[\"']?[^\s\"'|;&<>]+(?:" + "|".join(re.escape(e) for e in CODE_FILES) + r")\b"
WRITES_CODE = re.compile(r"(?i)(?:>>?\s*" + CODE_PATH + r"|\b(?:Set-Content|Add-Content|Out-File|tee)\b[^|;\n]*?" +
                         CODE_PATH + ")")
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
HERESTRING = re.compile(r"@(['\"])\r?\n(.*?)\r?\n\1@", re.S)
# interpreter, then only its own options (-B, -3, -NoProfile, /d /s), then the script flag and a quoted script
#   an option may take one value (-X utf8, -ExecutionPolicy Bypass); combined flags ending in c count (bash -lc)
INLINE = re.compile(r"(?im)" + INTERP_TOKEN +
                    r"(?:\s+(?:-{1,2}[\w.-]+(?:\s+(?![-/\"'])[^\s\"';|&]+)?|/[a-z]))*?\s+"
                    r"(?:-\w*c|-e|-p|--eval|--print|-Command|/c|/k)\s+(\"(?:[^\"\\]|\\.)*\"|'[^']*')")
WINDOW = 200          # lines judged on each side of an edit
MAX_FILE = 4_000_000  # an edited file over this many bytes is judged on the new text only; a Write is cut at it (chars)
MAX_JOIN = 50         # physical lines one statement may span


# ---- same-length blanking ------------------------------------------------------------------------------------------

def blank(s):
    return re.sub(r"[^\n]", " ", s)


MIDDLE_TOKENS = {getattr(tokenize, n) for n in ("FSTRING_MIDDLE", "TSTRING_MIDDLE") if hasattr(tokenize, n)}
KEY = re.compile(r"(?i)creation_?flags|detached")  # keys kept visible: kw["creationflags"], {"detached": true}


def blank_strings(text, rx=QUOTED):
    """String contents to spaces, quotes kept, same length (so offsets survive). A string that IS the key
    `creationflags` (kw["creationflags"] = 8) is kept, so the flag value is still found."""
    def one(m):
        s = m.group(0)
        return s if KEY.fullmatch(s[1:-1]) else s[0] + blank(s[1:-1]) + s[-1]
    return rx.sub(one, text)


SLASH_FILES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".cs", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp",
               ".rs", ".go", ".java")
BATCH_FILES = (".bat", ".cmd")
SHELL_FILES = (".sh", ".bash", ".zsh", ".ps1", ".psm1", ".bat", ".cmd")


def comment_style(low_path):
    """'slash' (// and /* */), 'batch' (REM / ::), 'none' (.ahk/.vbs: not stripped), else 'hash' (#)."""
    if low_path.endswith(SLASH_FILES):
        return "slash"
    if low_path.endswith(BATCH_FILES):
        return "batch"
    if low_path.endswith((".ahk", ".vbs")):
        return "none"
    return "hash"


def strip_comments(text, style="hash"):
    """Same-length comment blanking, quote-aware, per language: hash = `#` to line end (after a word boundary);
    slash = `//` to line end and `/* */` blocks (template literals in backticks count as quotes); batch = REM/:: lines."""
    if style == "batch":
        return "\n".join(blank(l) if re.match(r"\s*(?i:rem\s|::)", l) else l for l in text.split("\n"))
    if style == "none":
        return text
    chars, n, i, q, block = list(text), len(text), 0, None, False
    quotes = "'\"`" if style == "slash" else "'\""
    while i < n:
        ch = text[i]
        if block:
            if text.startswith("*/", i):
                chars[i] = chars[i + 1] = " "
                block = False
                i += 2
                continue
            if ch != "\n":
                chars[i] = " "
        elif q:
            if ch == "\\":
                i += 2
                continue
            if ch == q or ch == "\n" and q != "`":
                q = None
        elif ch in quotes:
            q = ch
        elif style == "slash" and text.startswith("/*", i):
            block = True
            chars[i] = chars[i + 1] = " "
            i += 2
            continue
        elif (ch == "#" and style == "hash" or style == "slash" and text.startswith("//", i)) and \
                (i == 0 or text[i - 1] in " \t;(,\n"):
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                chars[k] = " "
            i = j
            continue
        i += 1
    return "".join(chars)


def python_strings(text):
    """Same-length, by Python's own tokenizer: blank the contents of every string token (docstrings and multi-line
    prose included; prefix and quotes kept), except a string that is the key `creationflags`. Untokenizable text
    (a partial window, a syntax error) is returned unchanged."""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    starts = [0]
    for line in text.split("\n"):
        starts.append(starts[-1] + len(line) + 1)
    chars = list(text)
    for t in toks:
        if t.type in MIDDLE_TOKENS:  # 3.12+: f-string (3.14: t-string) literal parts are their own tokens
            if KEY.fullmatch(t.string):
                continue
            a = starts[t.start[0] - 1] + t.start[1]
            b = starts[t.end[0] - 1] + t.end[1]
        elif t.type == tokenize.STRING:
            s = t.string
            q = min(i for i in (s.find('"'), s.find("'")) if i >= 0)
            ql = 3 if s[q:q + 3] in ('"""', "'''") else 1
            if KEY.fullmatch(s[q + ql:len(s) - ql]):
                continue
            a = starts[t.start[0] - 1] + t.start[1] + q + ql
            b = starts[t.end[0] - 1] + t.end[1] - ql
        else:
            continue
        for k in range(a, min(b, len(chars))):
            if chars[k] != "\n":
                chars[k] = " "
    return "".join(chars)


def statements(code):
    """Logical statements of blanked CODE: [(start, end, text)], text being the original slice (same offsets)."""
    lines = code.split("\n")
    out, start, depth, pos, n = [], 0, 0, 0, 0
    for idx, line in enumerate(lines):
        if n == 0:
            start = pos
        n += 1
        bare = blank_strings(line)
        depth = max(depth + sum(bare.count(c) for c in "([{") - sum(bare.count(c) for c in ")]}"), 0)
        pos += len(line) + 1
        nxt = lines[idx + 1].lstrip() if idx + 1 < len(lines) else ""
        joins = depth > 0 or bare.rstrip().endswith(("|", "\\")) or nxt.startswith("|") and not nxt.startswith("||")
        if joins and idx + 1 < len(lines) and n < MAX_JOIN:
            continue
        out.append((start, pos, code[start:pos]))
        n, depth = 0, 0
    return out


# ---- the rule -----------------------------------------------------------------------------------------------------

def flag_values(bare):
    """Each creationflags value in string-blanked BARE: up to the next top-level `,` `;`, closing bracket, or a line
    end outside brackets that is not continued with `|`."""
    out = []
    for m in FLAGS.finditer(bare):
        depth, i = 0, m.end()
        while i < len(bare):
            ch = bare[i]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif ch in ",;" and depth == 0:
                break
            elif ch == "\n" and depth == 0 and not (bare[m.end():i].rstrip().endswith(("|", "\\")) or
                                                     bare[i + 1:].lstrip().startswith("|")):
                break  # a value continues past a line end only inside brackets or around `|`
            i += 1
        out.append(bare[m.end():i])
    return out


def outer_only(expr):
    """EXPR with subscripts and keyword-argument values blanked: in creationflags=table[8] or pick(level=8) the 8 is an
    index or an option, not a flag. Positional call arguments stay judged (getattr(subprocess, "DETACHED_PROCESS",
    0x8) is the usual portable spelling), and so do grouping parentheses, including after a keyword (else (A | 0x8))."""
    out, frames = list(expr), []  # frame: [kind, in_keyword_value]; kind "index" | "call" (any parenthesis: a
    for i, ch in enumerate(expr):  # grouping one holds no name=value, so treating it as a call changes nothing)
        if ch in "([{":
            frames.append(["index" if ch == "[" else "call", False])
            continue
        if ch in ")]}":
            if frames:
                frames.pop()
            continue
        if frames and frames[-1][0] == "call":
            if ch == ",":
                frames[-1][1] = False
            elif ch == "=" and re.search(r"(?<![=!<>])[A-Za-z_]\w*\s*$", expr[:i]) and expr[i + 1:i + 2] != "=":
                frames[-1][1] = True
                continue
        if any(f[0] == "index" or f[0] == "call" and f[1] for f in frames):
            out[i] = " "
    return "".join(out)


def named_use(bare, m):
    """Is this occurrence of the flag's name USED as a launch flag?"""
    before, after = bare[:m.start()], bare[m.end():]
    if re.search(r"[~&]\s*\(?\s*$", before):
        return False  # flags & ~DETACHED_PROCESS removes it; flags & DETACHED_PROCESS tests it
    if re.match(r"\s*=(?!=)", after):
        return False  # the constant's own definition: DETACHED_PROCESS = 0x00000008
    return bool(re.search(r"(?<!\\)\|\s*\(?\s*$|(?<![=!<>])=\s*$", before) or  # A | X ; flags = X ; flags |= X
                re.match(r"\s*\)?\s*\|(?![|=])", after) or
                LAUNCH_CONTEXT.search(bare))


def bad_statement(code, literals=True):
    """True when one logical statement (comments/docstrings already blanked) launches detached. LITERALS False skips
    the creationflags number check (plain shell text: `rg creationflags=8 .` is a search, not code)."""
    bare = blank_strings(code)
    for m in NAMED.finditer(bare):
        if named_use(bare, m):
            return True
    for expr in (flag_values(bare) if literals else []):
        for neg, lit in LITERAL.findall(outer_only(expr)):
            if neg:
                continue
            try:
                value = int(lit, 10) if re.fullmatch(r"0\d+", lit) else int(lit, 0)
            except ValueError:
                continue
            if value >= 0x100000000:
                continue
            if value & 0x8:
                return True
    return bool(NODE_DETACHED.search(bare))


def first_bad(text, spans=None, style="hash", python=False, literals=True):
    """The first offending statement, or None. SPANS [(start, end)] limit to statements overlapping any span; the
    whole text is tokenized and comment-stripped FIRST, then cut to a window around the spans (same offsets)."""
    code = python_strings(text) if python else text
    code = strip_comments(code, style)
    if spans:
        code = window(code, spans)
    for s, e, stmt in statements(code):
        if spans and not any(s < b + 1 and e > a for a, b in spans):
            continue
        if bad_statement(stmt, literals):
            return " ".join(stmt.split())
    return None


def shell_payloads(command, depth=0):
    """Parts of a shell command that run as code or are written into a code file (see module docstring)."""
    out, rest, pos = [], [], 0
    for m in HEREDOC.finditer(command):
        if m.start() < pos:
            continue
        nl = command.find("\n", m.end())
        if nl < 0:
            break
        header = command[command.rfind("\n", 0, m.start()) + 1:nl]
        end = re.compile(r"(?m)^[ \t]*" + re.escape(m.group(2)) + r"[ \t]*$").search(command, nl + 1)
        body_end = end.start() if end else len(command)
        if INTERP.search(header) or WRITES_CODE.search(header):
            out.append(as_python(header, command[nl + 1:body_end]))
        rest.append(command[pos:nl + 1])
        pos = end.end() if end else len(command)
    rest.append(command[pos:])
    plain = "".join(rest)
    for m in HERESTRING.finditer(plain):  # PowerShell @' ... '@ | python - / | Set-Content x.py
        tail = plain[m.end():m.end() + 200].split("\n")[0]
        if INTERP.search(tail) or WRITES_CODE.search(tail):
            out.append(as_python(tail, m.group(2)))
    plain = HERESTRING.sub(lambda m: blank(m.group(0)), plain)
    for m in list(INLINE.finditer(plain)) + list(HERE3.finditer(plain)):  # python -c "..." / python <<< "..."
        body = m.group(1)[1:-1]
        body = body.replace('\\"', '"') if m.group(1)[0] == '"' else body
        out.append(as_python(plain[m.start():m.start(1)], body))
        if depth < 3:
            out.extend(shell_payloads(body, depth + 1))
    for seg in segments(plain):  # echo "..." > x.py / "..." | Set-Content x.py / echo "..." | python -
        writes = WRITES_CODE.search(QUOTED_ML.sub("''", seg))
        piped = PIPE_TO_INTERP.search(seg.rstrip(" \t;&"))
        if writes or piped:
            kind = (writes.group(0) if writes else "") + (piped.group(0) if piped else "")
            out.extend(as_python(kind, m.group(0)[1:-1].replace("\\n", "\n")) for m in QUOTED_ML.finditer(seg))
    out.append(PLAIN + QUOTED_ML.sub("''", plain))
    return out


PLAIN = "\0plain\0"  # marks the plain command text: judged without the creationflags number check
PY = "\0py\0"        # marks code fed to a Python interpreter or written to a .py file: judged with Python's tokenizer
IS_PYTHON = re.compile(r"(?i)python[\d.]*w?(?:\.exe)?|(?<![\w.-])py(?:\.exe)?(?![\w-])|\.pyw?\b")


def as_python(context, body):
    """BODY marked for Python judging when CONTEXT (the interpreter or target) is Python."""
    return PY + body if IS_PYTHON.search(context) else body


def segments(command):
    """COMMAND split on `;` `&&` `||` and line ends outside quotes (pipes stay together: `"..." | Set-Content x.py`)."""
    bare = QUOTED_ML.sub(lambda m: "x" * len(m.group(0)), command)  # a line break INSIDE quotes is not a cut
    cuts = [0] + [m.end() for m in re.finditer(r";|&&|\|\||\n", bare)] + [len(command)]
    return [command[a:b] for a, b in zip(cuts, cuts[1:])]


def after_edits(path, edits):
    """(text_after, spans) applying EDITS in order to the file, LF-normalised; None when not applicable."""
    try:
        if os.path.getsize(path) > MAX_FILE:
            return None
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            text = f.read().replace("\r\n", "\n")
    except OSError:
        return None
    spans = []
    for e in edits:
        old = str(e.get("old_string") or "").replace("\r\n", "\n")
        new = str(e.get("new_string") or "").replace("\r\n", "\n")
        if not old or old not in text:
            return None
        hits, i = [], text.find(old)
        while i >= 0:
            hits.append(i)
            if not e.get("replace_all"):
                break
            i = text.find(old, i + len(old))
        shift, delta = 0, len(new) - len(old)
        spans = [(a + delta * sum(h < a for h in hits), b + delta * sum(h < a for h in hits)) for a, b in spans]
        for h in hits:
            spans.append((h + shift, h + shift + len(new)))
            shift += delta
        text = text.replace(old, new) if e.get("replace_all") else text[:hits[0]] + new + text[hits[0] + len(old):]
    return text, spans


def window(text, spans):
    """Cut TEXT to WINDOW lines around SPANS (same offsets: outside lines are blanked)."""
    lo = max(0, min(a for a, _ in spans))
    hi = max(b for _, b in spans)
    a = text.rfind("\n", 0, lo)
    for _ in range(WINDOW):
        a = text.rfind("\n", 0, a) if a > 0 else -1
    b = hi
    for _ in range(WINDOW):
        b = text.find("\n", b + 1)
        if b < 0:
            b = len(text)
            break
    a = max(a, 0)
    return blank(text[:a]) + text[a:b] + blank(text[b:])


def _exempt(path, exempt):
    return any(path.endswith("/" + s) or path == s for s in exempt)


def _resolve(path, cwd):
    if not isinstance(path, str) or not path or not cwd or os.path.isabs(path):
        return path
    return os.path.join(cwd, path)


def judge_shell(command):
    for payload in shell_payloads(command):
        plain, python = payload.startswith(PLAIN), payload.startswith(PY)
        text = payload[len(PLAIN):] if plain else payload[len(PY):] if python else payload
        hit = first_bad(text, python=python, literals=not plain)
        if hit:
            return hit
    return None


def judge(tool, inp, exempt=EXEMPT, cwd=""):
    """The offending statement for this Claude Code-shaped call (TOOL and its input dict INP), or None.
    Shell tools (Bash, PowerShell, Monitor) are read as text and as words; file tools on code files only, a relative
    path read against CWD (a native-OS str or path; any other type is ignored)."""
    cwd = os.fspath(cwd) if isinstance(cwd, (str, os.PathLike)) else ""
    path = str(_resolve(inp.get("file_path") or inp.get("notebook_path") or "", cwd)).replace("\\", "/")
    if tool in ("Bash", "PowerShell", "Monitor"):
        command = inp.get("command")
        if isinstance(command, list):
            command = shlex.join(map(str, command))  # keeps the script argument quoted, so -c "..." is seen
        if not isinstance(command, str):
            return None
        return judge_shell(command) or judge_words(command, "powershell" if tool == "PowerShell" else "bash")
    if tool not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return None
    low = path.lower()
    if not low.endswith(CODE_FILES) or _exempt(path, exempt):
        return None  # notes and docs may describe the flag; the hook's own packet must stay editable
    style, python = comment_style(low), low.endswith((".py", ".pyw", ".pyi"))
    shell_file = low.endswith(SHELL_FILES)
    if tool == "Write":
        content = str(inp.get("content") or "")
        if len(content) > MAX_FILE:
            content = content[:MAX_FILE]
        if shell_file:  # a script file holds the same -c / -e / heredoc shapes as a command line
            return judge_shell(strip_comments(content, style)) or first_bad(content, style=style)
        if low.endswith(".ipynb"):
            try:
                content = "\n".join("".join(c.get("source") or []) for c in json.loads(content).get("cells", []))
                python = True
            except (ValueError, AttributeError, TypeError):
                pass
        return first_bad(content, style=style, python=python)
    if tool == "NotebookEdit":
        return first_bad(str(inp.get("new_source") or ""), python=True)
    edits = [inp] if tool == "Edit" else [e for e in (inp.get("edits") or []) if isinstance(e, dict)]
    done = after_edits(path, edits)
    if done and done[1]:
        if shell_file:
            hit = judge_shell(window(strip_comments(done[0], style), done[1]))
            if hit:
                return hit
        return first_bad(done[0], spans=done[1], style=style, python=python)
    for e in edits:  # file unreadable / old text absent: judge the new text alone
        hit = first_bad(str(e.get("new_string") or ""), style=style, python=python)
        if hit:
            return hit
    return None


# ---- apply_patch texts --------------------------------------------------------------------------------------------

PATCH_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File:\s*(.+?)\s*$")
PATCH_MOVE = re.compile(r"^\*\*\* Move to:\s*(.+?)\s*$")


def judge_text(path, text, spans=None, exempt=EXEMPT):
    """TEXT as the new content (or, with SPANS, the content after an edit) of the code file PATH."""
    path = str(path).replace("\\", "/")
    low = path.lower()
    if not low.endswith(CODE_FILES) or _exempt(path, exempt):
        return None
    style, python = comment_style(low), low.endswith((".py", ".pyw", ".pyi"))
    if low.endswith(SHELL_FILES):
        shell = strip_comments(text, style)
        hit = judge_shell(window(shell, spans) if spans else shell)
        if hit:
            return hit
    return first_bad(text, spans=spans, style=style, python=python)


def _patch_files(text):
    """The files an apply_patch text (Codex or OpenCode grammar) writes: [{kind, path, dest, hunks}], each hunk
    {"anchor": the @@ text, "lines": [(mark, line)]} with mark '+' added, '-' removed, ' ' context. Outside an Update a
    header is read trimmed, with or without a space after `File:`, as both appliers accept it; inside an Update an
    indented `***` line is context, as Codex reads it."""
    files, cur = [], None
    for line in text.replace("\r\n", "\n").split("\n"):
        updating = cur is not None and cur["kind"] == "Update"
        head = line if updating else line.strip()
        m = PATCH_FILE.match(head) if head.startswith("***") else None
        if m:
            cur = None if m.group(1) == "Delete" else {"kind": m.group(1), "path": m.group(2), "dest": "",
                                                       "hunks": [{"anchor": "", "lines": []}]}
            if cur:
                files.append(cur)
            continue
        if cur is None:
            continue
        if head.startswith("***"):
            mv = PATCH_MOVE.match(head)
            if mv:
                cur["dest"] = mv.group(1)
            if cur["hunks"][-1]["lines"]:
                cur["hunks"].append({"anchor": "", "lines": []})
            continue
        if line.startswith("@@") and updating:
            if cur["hunks"][-1]["lines"] or cur["hunks"][-1]["anchor"]:
                cur["hunks"].append({"anchor": "", "lines": []})
            cur["hunks"][-1]["anchor"] = line[2:].strip()
            continue
        if line.startswith("+"):
            cur["hunks"][-1]["lines"].append(("+", line[1:]))
        elif updating and line.startswith("-"):
            cur["hunks"][-1]["lines"].append(("-", line[1:]))
        elif updating and (line.startswith(" ") or line == ""):
            cur["hunks"][-1]["lines"].append((" ", line[1:]))
    return files


MAX_HUNKS = 2000
PLACE_SECONDS = 5.0   # placing hunks in the file; past it, each hunk is judged on its own lines


def _in_file(path, hunks):
    """(text_after, spans of the added lines, hunks not placed) with the hunks applied in order to the file on disk,
    line by line (whole lines only; after a hunk's @@ anchor line when it has one; a pure insertion goes right after
    its anchor). None when the file is unreadable or too big, or there are more than MAX_HUNKS hunks. Linear: the
    result is built from pieces, and each hunk's first old line is looked up in an index."""
    try:
        if not path or os.path.getsize(path) > MAX_FILE or len(hunks) > MAX_HUNKS:
            return None
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            lines = f.read().replace("\r\n", "\n").split("\n")
    except (OSError, TypeError, ValueError):
        return None
    import bisect      # here, not at the top: the packet's own import block stays as reviewed
    import time
    where, bare = {}, {}
    for n, line in enumerate(lines):
        where.setdefault(line, []).append(n)
        bare.setdefault(line.strip(), []).append(n)
    deadline = time.monotonic() + PLACE_SECONDS

    def after(index, key, start):
        at = index.get(key, [])
        return (at[k] for k in range(bisect.bisect_left(at, start), len(at)))     # no copy of the tail

    def seek(seq, start):
        for n in after(where, seq[0], start):
            if lines[n:n + len(seq)] == seq:
                return n
            if time.monotonic() > deadline:
                raise TimeoutError
        return -1

    out, added, unplaced, cursor = [], [], [], 0
    try:
        for hunk in hunks:
            old = [line for mark, line in hunk["lines"] if mark != "+"]
            kept = [(mark, line) for mark, line in hunk["lines"] if mark != "-"]
            start = cursor
            if hunk["anchor"]:
                found = next(after(where, hunk["anchor"], cursor), None)
                if found is None:
                    found = next(after(bare, hunk["anchor"], cursor), None)
                if found is not None:
                    start = found + 1
            i = seek(old, start) if old else (start if hunk["anchor"] else -1)
            if i < 0:
                if any(mark == "+" for mark, _ in hunk["lines"]):
                    unplaced.append(hunk)
                continue
            out.extend(lines[cursor:i])
            for mark, line in kept:
                if mark == "+":
                    added.append(len(out))
                out.append(line)
            cursor = i + len(old)
    except TimeoutError:
        return None                          # too slow to place: every hunk is then judged on its own lines
    out.extend(lines[cursor:])
    text, spans, pos, mark_at = [], [], 0, set(added)
    for n, line in enumerate(out):
        if n in mark_at:
            spans.append((pos, pos + len(line)))
        pos += len(line) + 1
    return "\n".join(out), spans, unplaced


def _hunk_alone(name, hunk, exempt=EXEMPT):
    body, spans, pos = [], [], 0
    for mark, line in hunk["lines"]:
        if mark == "-":
            continue
        if mark == "+":
            spans.append((pos, pos + len(line)))
        body.append(line)
        pos += len(line) + 1
    return judge_text(name, "\n".join(body) + "\n", spans, exempt) if spans else None


def judge_patch(text, cwd="", exempt=EXEMPT):
    """An apply_patch text: an added file whole; an updated file IN the file on disk around its added lines (as an
    Edit is judged); a hunk that cannot be placed there, or every hunk when the file cannot be read, on its own lines
    around its added lines. Removed lines are gone and never judged."""
    for f in _patch_files(text):
        name = f["dest"] or f["path"]
        if f["kind"] == "Add":
            body = [line for hunk in f["hunks"] for mark, line in hunk["lines"] if mark == "+"]
            hit = judge_text(name, "\n".join(body) + "\n", None, exempt) if body else None
            if hit:
                return hit
            continue
        done = _in_file(_resolve(f["path"], cwd), f["hunks"])
        if done:
            text_after, spans, alone = done
            hit = judge_text(name, text_after, spans, exempt) if spans else None
            if hit:
                return hit
        else:
            alone = f["hunks"]
        for hunk in alone:
            hit = _hunk_alone(name, hunk, exempt)
            if hit:
                return hit
    return None


# ---- shell words: a script after -c given in any quoting --------------------------------------------------------

SEPARATORS = (";", "|", "||", "&", "&&", "\n", "(", ")")
WRAPPERS = frozenset(("env", "timeout", "nohup", "exec", "sudo", "time", "nice", "command", "builtin", "start",
                      "call", "&", ".", "then", "do", "else"))
SCRIPT_FLAG = re.compile(r"(?i)^(?:-\w*c|-e|-p|--eval|--print|-command|/c|/k)$")
INTERP_WORD = re.compile(r"(?i)^(?:.*[\\/])?" + INTERP_NAMES + r"$")
MAX_WORDS = 20000
SHELL_WORD = re.compile(r"(?i)^(?:.*[\\/])?(?:pwsh|powershell|bash|sh|cmd)(?:\.exe)?$")
CMD_WORD = re.compile(r"(?i)^(?:.*[\\/])?cmd(?:\.exe)?$")


def _option(word, cmd_exe):
    """An interpreter's own option: -x always, /x for cmd only, and a variable that is no path ("${X4_PYI[@]}"
    expands to options; "$T/find.py" is a script)."""
    if word.startswith("-"):
        return True
    if word.startswith("/"):
        return cmd_exe
    return word.startswith("$") and not any(c in word for c in "/\\.")


HEREDOC_OP = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([A-Za-z_][\w.-]*))")


def _skip_heredocs(command, i, pending):
    """The index after the bodies of the PENDING heredocs [(strip_tabs, delimiter)], whose lines start at I."""
    n = len(command)
    for dash, delim in pending:
        while i < n:
            eol = command.find("\n", i)
            eol = n if eol < 0 else eol
            line = command[i:eol].rstrip("\r")
            i = eol + 1
            if (line.lstrip("\t") if dash else line) == delim:
                break
    return i


def _words(command, mode):
    """COMMAND split into shell words with the quoting undone, MODE 'bash' or 'powershell'. Separators are words of
    their own. bash: '...' is literal (so 'a'"'"'b' is a'b), "..." takes backslash escapes. PowerShell: '' inside
    '...' is one quote, "..." takes backtick escapes. A heredoc body (bash, `<<` outside quotes) and a here-string
    (PowerShell @'...'@) are data, not commands: they are skipped here (one placeholder word for a here-string), and
    shell_payloads() judges them where they are fed to an interpreter or written to a code file."""
    out, cur, have, i, n, pending, arith = [], [], False, 0, len(command), [], 0
    while i < n and len(out) < MAX_WORDS:
        ch = command[i]
        if mode == "bash" and command.startswith("<<<", i):
            cur.append("<<<")                   # a here-string (`cat <<< "x"`), never a heredoc
            have, i = True, i + 3
            continue
        if ch == "#" and not have:              # a comment, to the end of the line (bash and PowerShell)
            eol = command.find("\n", i)
            i = n if eol < 0 else eol
            continue
        if mode == "bash" and command.startswith("$((", i):
            cur.append("$((")                   # arithmetic: `<<` inside it is a shift; depth counts its brackets
            have, arith, i = True, arith + 2, i + 3
            continue
        if arith and ch in "()":
            arith = max(arith + (1 if ch == "(" else -1), 0)
        if mode == "bash" and ch == "<" and command.startswith("<<", i) and not arith:   # $((a<<b)) is a shift
            m = HEREDOC_OP.match(command, i)
            if m:
                if have:
                    out.append("".join(cur))
                    cur, have = [], False
                pending.append((m.group(1) == "-", next(g for g in m.groups()[1:] if g is not None)))
                i = m.end()
                continue
        if mode == "powershell" and ch == "@" and command[i + 1:i + 2] in ("'", '"') and \
                command[i + 2:i + 4].lstrip("\r")[:1] == "\n":
            end = command.find("\n" + command[i + 1] + "@", i + 2)
            i = n if end < 0 else end + 3
            cur.append("@here-string")
            have = True
            continue
        if ch == "'":
            j = i + 1
            while j < n:
                if command[j] == "'":
                    if mode == "powershell" and command.startswith("''", j):
                        cur.append("'")
                        j += 2
                        continue
                    break
                cur.append(command[j])
                j += 1
            have, i = True, j + 1
            continue
        if ch == '"':
            esc = "\\" if mode == "bash" else "`"
            j = i + 1
            while j < n and command[j] != '"':
                if command[j] == esc and j + 1 < n:
                    cur.append(command[j + 1])
                    j += 2
                    continue
                cur.append(command[j])
                j += 1
            have, i = True, j + 1
            continue
        if ch in " \t\r\n;|&()":
            if have:
                out.append("".join(cur))
                cur, have = [], False
            if ch == "\n":
                out.append("\n")
                if pending:
                    i = _skip_heredocs(command, i + 1, pending)
                    pending = []
                    continue
            elif ch in ";|&()":
                two = command[i:i + 2]
                if two in ("||", "&&"):
                    out.append(two)
                    i += 2
                    continue
                out.append(ch)
            i += 1
            continue
        if ch == "\\" and mode == "bash" and i + 1 < n:
            cur.append(command[i + 1])
            have, i = True, i + 2
            continue
        cur.append(ch)
        have, i = True, i + 1
    if have:
        out.append("".join(cur))
    return out


def judge_words(command, shell="", depth=0):
    """A script after an interpreter's -c / -e / -Command, found by WORDS, so any quoting reaches it (a Codex argv
    list is quoted with '"'"' or ''), and so does an interpreter after env, timeout, nohup or an assignment, or given
    as a variable or quoted path ("$PY", & $env:PY)."""
    if depth > 3:
        return None
    modes =("bash",) if shell == "bash" else ("powershell",) if shell == "powershell" else ("bash", "powershell")
    for mode in modes:
        words = _words(command, mode)
        start, prefix, i = True, False, 0
        while i < len(words):
            w = words[i]
            if w in SEPARATORS:
                start, prefix = True, False
                i += 1
                continue
            if start:
                if re.match(r"^[A-Za-z_]\w*=", w) or w.lower() in WRAPPERS:
                    prefix = True
                    i += 1
                    continue
                if prefix and re.match(r"^(?:-\S*|\d+(?:\.\d+)?[smhd]?)$", w):
                    i += 1
                    continue
                if INTERP_WORD.match(w) or w.startswith("$"):
                    # the interpreter's own options (a value may follow one), then the script flag; the first
                    # other word is a script path, and what follows it is that script's arguments, not code
                    j, cmd_exe = i + 1, bool(CMD_WORD.match(w))
                    while j + 1 < len(words) and words[j] not in SEPARATORS:
                        if SCRIPT_FLAG.match(words[j]) and (cmd_exe or not words[j].startswith("/")):
                            body = words[j + 1]
                            inner = SHELL_WORD.match(w)
                            name = re.split(r"[\\/]", w)[-1].lower()
                            dialect = ("powershell" if name.startswith(("pwsh", "powershell")) else
                                       "bash" if name in ("bash", "sh", "bash.exe", "sh.exe") else "")
                            hit = (first_bad(body, python=bool(IS_PYTHON.search(w))) or judge_shell(body) or
                                   (judge_words(body, dialect, depth + 1) if inner else None))
                            if hit:
                                return hit
                            break
                        if words[j] == "-m":
                            break                               # python -m module: what follows is the module's
                        if _option(words[j], cmd_exe):
                            j += 1
                            if (j + 1 < len(words) and not _option(words[j], cmd_exe) and
                                    words[j] not in SEPARATORS and _option(words[j + 1], cmd_exe)):
                                j += 1                          # an option's value: -X utf8 -c ...
                            continue
                        break
                start, prefix = False, False
            i += 1
    return None
