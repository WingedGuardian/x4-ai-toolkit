"""PreToolUse guard: refuse launching helpers DETACHED (Windows DETACHED_PROCESS).

Scope: only the pattern that causes pop-up SPAM. Deliberately visible terminal work (a new console window,
CREATE_NEW_CONSOLE, Start-Process, a terminal the user opens) is NOT refused.

Why. MEASURED on Windows: a helper started with creationflags NO_WINDOW | NEW_PROCESS_GROUP | DETACHED_PROCESS has no
console, so every console program it starts without CREATE_NO_WINDOW gets a NEW VISIBLE window; a helper that read
processes through powershell.exe once a second produced a console pop-up every second for a whole run.
CREATE_NO_WINDOW alone gives the child a hidden console its own children inherit, and the child still outlives the
caller (measured), so detaching is never needed for that.

Registration. Claude Code only (agent/targets/claude/settings.json, through no-detached-launch.sh). NOT wired for
Codex or OpenCode: the shared x4guard funnel treats a crashed guard as a DENY, which conflicts with this hook's
fail-open design, so that needs its own design. Codex and OpenCode launches are therefore not checked.

What is checked:
- Write / Edit / MultiEdit / NotebookEdit on CODE files. An Edit is judged on the file AFTER the edit (edits applied
  in order), statement by statement around each changed span, so a small edit that appends `| 0x8` to an existing
  creationflags line is seen.
- Bash / PowerShell / Monitor: text that runs or is written as code: -c/-e/-Command and /c bodies after an
  interpreter or shell (nested up to 3 levels), heredoc and PowerShell here-string bodies fed to an interpreter or
  written to a code file, quoted arguments of a command that writes a code file, and the unquoted command itself.
  Commit messages and search patterns are quoted arguments of other commands and are not judged.
Refused, per logical statement (bracketed and `|`-continued lines joined, at most 50 lines; comments, docstrings and
string contents blanked to the same length):
- DETACHED_PROCESS used as a flag (creationflags / Popen / CreateProcess / spawn / `|` / `|=` / assignment) and not
  negated with `~`;
- a creationflags value holding an integer literal with bit 0x8;
- Node `detached: true` / `.detached = true` (libuv sets DETACHED_PROCESS on Windows).
Unreadable input fails OPEN; the refusal is a JSON deny in Claude Code's PreToolUse shape.

Known limits (measured by the 2026-11-24 port review; documented, not fixed here):
- NOT caught: an inline script after a QUOTED or VARIABLE interpreter (`"$PY" -c ...`, `$PY -c ...`,
  `& "C:\\...\\python.exe" -c ...`); only a bare interpreter name before -c/-e/-Command is matched.
- NOT caught: code fed on stdin without a heredoc or here-string (`echo "..." | python -`, `python <<< "..."`,
  `node -p`).
- NOT caught: renamed flag constants, code built inside Python string literals, and helpers started with `pythonw`
  or a `.pyw` (also console-less, so their console children pop windows the same way).
- Known false positives (rare): code that names the flag as data (a constants table `NAME = 0x8`, a test asserting
  the flag is absent, a blocklist holding it), a `creationflags=` value computed from a call holding an 8, and an
  Edit to a shell script judges its whole 400-line window, so a script that already holds a bad launch can refuse
  an unrelated edit nearby. A refusal names the statement; rewrite it or build the token from parts.
"""
import io
import json
import os
import re
import sys
import tokenize

NAMES = r"(?:DETACHED_PROCESS)"
NAMED = re.compile(r"(?<![\w.])(?:[A-Za-z_]\w*\.)*" + NAMES + r"\b")
NAMED_USE = re.compile(r"creation_?flags|Popen|CreateProcess|spawn|(?<!\\)\||\|=|(?<![=!<>])=(?!=)")
FLAGS = re.compile(r"creation_?flags[\"'\]]*\s*(?:[=:(]|\|=)\s*", re.I)
QUOTED = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'")
QUOTED_ML = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'[^']*'")  # shell quoting: may span lines; '...' has no escapes
LITERAL = re.compile(r"(~\s*\(?\s*)?(?<![\w.])(0[xX][0-9a-fA-F]+|\d+)(?![\w.])")
NODE_DETACHED = re.compile(r"""(?:\bdetached["']?\s*:|\.detached\s*=)\s*true\b""")
CODE_FILES = (".py", ".pyw", ".pyi", ".ps1", ".psm1", ".psd1", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".cs",
              ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".ahk", ".bat", ".cmd", ".sh", ".bash", ".zsh", ".vbs",
              ".rs", ".go", ".java", ".ipynb")
INTERP_NAMES = r"(?:python[\d.]*w?|py|pythonw|node|deno|bun|pwsh|powershell|bash|sh|cmd)(?:\.exe)?"
INTERP = re.compile(r"(?i)(?<![\w.$-])" + INTERP_NAMES + r"(?![\w-])")
CODE_PATH = r"[\"']?[^\s\"'|;&<>]+(?:" + "|".join(re.escape(e) for e in CODE_FILES) + r")\b"
WRITES_CODE = re.compile(r"(?i)(?:>>?\s*" + CODE_PATH + r"|\b(?:Set-Content|Add-Content|Out-File|tee)\b[^|;\n]*?" +
                         CODE_PATH + ")")
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
HERESTRING = re.compile(r"@(['\"])\r?\n(.*?)\r?\n\1@", re.S)
# interpreter, then only its own options (-B, -3, -NoProfile, /d /s), then the script flag and a quoted script
#   an option may take one value (-X utf8, -ExecutionPolicy Bypass); combined flags ending in c count (bash -lc)
INLINE = re.compile(r"(?i)(?<![\w.$-])" + INTERP_NAMES +
                    r"(?:\s+(?:-{1,2}[\w.-]+(?:\s+(?![-/\"'])[^\s\"';|&]+)?|/[a-z]))*?\s+"
                    r"(?:-\w*c|-e|--eval|-Command|/c|/k)\s+(\"(?:[^\"\\]|\\.)*\"|'[^']*')")
WINDOW = 200          # lines judged on each side of an edit
MAX_FILE = 4_000_000  # bytes; a bigger file is judged on the new text only
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


def bad_statement(code):
    """True when one logical statement (comments/docstrings already blanked) launches detached."""
    bare = blank_strings(code)
    for m in NAMED.finditer(bare):
        if re.search(r"~\s*\(?\s*$", bare[:m.start()]):
            continue  # flags & ~DETACHED_PROCESS removes it
        if NAMED_USE.search(bare):
            return True
    for expr in flag_values(bare):
        for neg, lit in LITERAL.findall(expr):
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


def first_bad(text, spans=None, style="hash", python=False):
    """The first offending statement, or None. SPANS [(start, end)] limit to statements overlapping any span; the
    whole text is tokenized and comment-stripped FIRST, then cut to a window around the spans (same offsets)."""
    code = python_strings(text) if python else text
    code = strip_comments(code, style)
    if spans:
        code = window(code, spans)
    for s, e, stmt in statements(code):
        if spans and not any(s < b + 1 and e > a for a, b in spans):
            continue
        if bad_statement(stmt):
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
            out.append(command[nl + 1:body_end])
        rest.append(command[pos:nl + 1])
        pos = end.end() if end else len(command)
    rest.append(command[pos:])
    plain = "".join(rest)
    for m in HERESTRING.finditer(plain):  # PowerShell @' ... '@ | python - / | Set-Content x.py
        tail = plain[m.end():m.end() + 200].split("\n")[0]
        if INTERP.search(tail) or WRITES_CODE.search(tail):
            out.append(m.group(2))
    plain = HERESTRING.sub(lambda m: blank(m.group(0)), plain)
    for m in INLINE.finditer(plain):  # the script flag must belong to the interpreter right before it
        body = m.group(1)[1:-1]
        body = body.replace('\\"', '"') if m.group(1)[0] == '"' else body
        out.append(body)
        if depth < 3:
            out.extend(shell_payloads(body, depth + 1))
    for seg in segments(plain):  # echo "..." > x.py / printf '...' >> x.py / "..." | Set-Content x.py
        if WRITES_CODE.search(QUOTED_ML.sub("''", seg)):
            out.extend(m.group(0)[1:-1].replace("\\n", "\n") for m in QUOTED_ML.finditer(seg))
    out.append(QUOTED_ML.sub("''", plain))
    return out


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


def judge_shell(command):
    for payload in shell_payloads(command):
        hit = first_bad(payload)
        if hit:
            return hit
    return None


def judge(tool, inp):
    """The offending statement for this call, or None."""
    path = str(inp.get("file_path") or inp.get("notebook_path") or "").replace("\\", "/")
    if tool in ("Bash", "PowerShell", "Monitor"):
        command = inp.get("command")
        if isinstance(command, list):
            command = " ".join(map(str, command))
        return judge_shell(command) if isinstance(command, str) else None
    if tool not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return None
    low = path.lower()
    if not low.endswith(CODE_FILES):
        return None  # notes and docs may describe the flag
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


def main():
    try:
        raw = sys.stdin.buffer.read()
        payload = json.loads(raw.decode("utf-8-sig", "replace"))
        hit = judge(payload.get("tool_name"), payload.get("tool_input") or {})
    except Exception as error:  # fail open: a parser bug must not block every edit
        print("no-detached-launch: could not read the call ({}); allowed".format(type(error).__name__), file=sys.stderr)
        return 0
    if not hit:
        return 0
    reason = ("no-detached-launch: this launches a process DETACHED ({!r}). A detached process "
              "has no console, so every console program it starts gets a visible window on the user's desktop "
              "(measured: a pop-up every second for a whole headless run). "
              "Use creationflags=CREATE_NO_WINDOW (0x08000000), optionally | CREATE_NEW_PROCESS_GROUP "
              "(0x200): the child gets a hidden console and still outlives the caller.").format(hit[:140])
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
