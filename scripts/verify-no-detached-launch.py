#!/usr/bin/env python3
"""Mutation gate for the no-detached-launch guard: one mutant per clause, each must turn its test file red.

    python scripts/verify-no-detached-launch.py                 # every mutant (CI only: ~60 pytest runs)
    python scripts/verify-no-detached-launch.py --check-anchors # no pytest runs: every anchor appears exactly once
    python scripts/verify-no-detached-launch.py --only REGEX    # just the mutants whose name matches (plus the baseline)

Source under test: agent/guards/claude-hooks/no-detached-launch.py (the hook) and no-detached-launch.sh (the bash
wrapper), tested by test_no_detached_launch.py in the same folder. Every trial runs on a COPY in a temp folder, never
the working tree: a gate that edits real files makes them deliberately wrong for its duration.

Rules (from the original packet's gate, which survived four review rounds):
- the UNMUTATED copy must pass first, or no mutant result means anything (exit 2);
- a kill counts only when pytest reports a FAILED test, not a crash, a collection error or "no tests ran";
- pytest runs with cwd = the temp folder and the test file named, because on the Windows runner
  `pytest <tempdir>` collected nothing and the old gate counted that as a kill;
- an anchor that is not found exactly once is a stale mutant: the gate refuses (exit 2) before running any trial.

Exit 0 when every mutant is killed, 1 when one survived, 2 when the gate could not run (baseline red, stale anchor,
no mutant matched). The f-string mutant is skipped where the hook's interpreter has no f-string tokens (tokenize
before 3.12): there it is equivalent. The two wrapper mutants need bash and are skipped, by name, without it.
HOOK_PYTHON="py -3.12" runs the hook under another interpreter than the one running pytest (see the test file).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "agent" / "guards" / "claude-hooks"
TEST = "test_no_detached_launch.py"
HOOK = "no-detached-launch.py"
WRAPPER = "no-detached-launch.sh"
SUPPORT = "_x4-env.sh"   # the wrapper sources it from its own folder

BS = "\\"

#: (name, file, anchor, replacement). The anchor must occur exactly once in its file.
M = [
    # names
    ("named-check", HOOK, "    for m in NAMED.finditer(bare):", "    for m in []:"),
    ("named-needs-use", HOOK, "        if NAMED_USE.search(bare):", "        if True:"),
    ("named-tilde-skip", HOOK, "            continue  # flags & ~DETACHED_PROCESS removes it", "            pass"),
    # strings / comments / docstrings
    ("strings-are-prose", HOOK, "    bare = blank_strings(code)", "    bare = code"),
    ("key-kept", HOOK, "        return s if KEY.fullmatch(s[1:-1]) else s[0] + blank(s[1:-1]) + s[-1]",
     "        return s[0] + blank(s[1:-1]) + s[-1]"),
    ("python-strings", HOOK, "    code = python_strings(text) if python else text", "    code = text"),
    # statements
    ("py-key-kept", HOOK, "        if KEY.fullmatch(s[q + ql:len(s) - ql]):", "        if False:"),
    ("fstring-middles", HOOK, "        if t.type in MIDDLE_TOKENS:", "        if False:"),
    ("line-comment", HOOK, "            for k in range(i, j):\n                chars[k] = \" \"",
     "            for k in []:\n                chars[k] = \" \""),
    ("quote-tracking", HOOK, "        elif ch in quotes:\n            q = ch", "        elif False:\n            q = ch"),
    ("block-comments", HOOK, "            block = True\n", "            block = False\n"),
    ("hash-only-hash-langs", HOOK, 'ch == "#" and style == "hash"', 'ch == "#"'),
    ("batch-rem", HOOK, r'blank(l) if re.match(r"\s*(?i:rem\s|::)", l) else l', "l"),
    ("comment-style-slash", HOOK, '    if low_path.endswith(SLASH_FILES):\n        return "slash"',
     '    if False:\n        return "slash"'),
    ("inline-script", HOOK, "    for m in INLINE.finditer(plain):", "    for m in []:"),
    ("inline-bound-to-interp", HOOK, r"""|/[a-z]))*?\s+"
                    r"(?:-\w*c""", r"""|/[a-z]))*?.*?\s+"
                    r"(?:-\w*c"""),
    ("quoted-newline-not-a-cut", HOOK, '    bare = QUOTED_ML.sub(lambda m: "x" * len(m.group(0)), command)',
     '    bare = QUOTED_ML.sub(lambda m: blank(m.group(0)), command)'),
    ("option-values", HOOK, r"""(?:\s+(?![-/\"'])[^\s\"';|&]+)?""", ""),
    ("combined-c-flags", HOOK, r"(?:-\w*c|-e|", r"(?:-c|-e|"),
    ("nested-shells", HOOK, "        if depth < 3:", "        if False:"),
    ("quoted-written-to-code", HOOK, "    for seg in segments(plain):", "    for seg in []:"),
    ("writes-per-segment", HOOK, "    for seg in segments(plain):", "    for seg in [plain]:"),
    ("shell-files", HOOK, "    shell_file = low.endswith(SHELL_FILES)", "    shell_file = False"),
    ("replace-all-shift-count", HOOK, "a + delta * sum(h < a for h in hits), b + delta * sum(h < a for h in hits)",
     "a + (delta if a > hits[0] else 0), b + (delta if a > hits[0] else 0)"),
    ("window-after-tokenize", HOOK,
     "    code = python_strings(text) if python else text\n    code = strip_comments(code, style)\n    if spans:\n        code = window(code, spans)",
     "    code = window(text, spans) if spans else text\n    code = python_strings(code) if python else code\n    code = strip_comments(code, style)"),
    ("depth-ignores-strings", HOOK, "        bare = blank_strings(line)", "        bare = line"),
    ("join-brackets", HOOK, "        joins = depth > 0 or bare.rstrip()", "        joins = False or bare.rstrip()"),
    ("join-trailing", HOOK, '        joins = depth > 0 or bare.rstrip().endswith(("|", "' + BS + BS + '")) or nxt',
     "        joins = depth > 0 or nxt"),
    ("join-leading-or", HOOK, 'or nxt.startswith("|") and not nxt.startswith("||")', "or False"),
    ("max-join", HOOK, "        if joins and idx + 1 < len(lines) and n < MAX_JOIN:",
     "        if joins and idx + 1 < len(lines):"),
    # flag values
    ("flags-expression", HOOK, "    for expr in flag_values(bare):", "    for expr in []:"),
    ("flags-dict-key", HOOK, 'flags[' + BS + '"\'' + BS + ']]*' + BS + 's*(?:', 'flags' + BS + 's*(?:'),
    ("flag-value-stops-at-comma", HOOK, '            elif ch in ",;" and depth == 0:', "            elif False:"),
    ("flag-value-stops-at-line", HOOK, '            elif ch == "' + BS + 'n" and depth == 0 and not (',
     "            elif False and not ("),
    ("flag-value-backslash", HOOK, 'bare[m.end():i].rstrip().endswith(("|", "' + BS + BS + '"))',
     'bare[m.end():i].rstrip().endswith(("|",))'),
    ("literal-tilde-skip", HOOK, "            if neg:\n                continue", "            if False:\n                continue"),
    ("leading-zero", HOOK, '                value = int(lit, 10) if re.fullmatch(r"0' + BS + 'd+", lit) else int(lit, 0)',
     "                value = int(lit, 0)"),
    ("upper-bound", HOOK, "            if value >= 0x100000000:\n                continue",
     "            if False:\n                continue"),
    ("node-detached", HOOK, "    return bool(NODE_DETACHED.search(bare))", "    return False"),
    # shell
    ("heredoc-interp", HOOK, "        if INTERP.search(header) or WRITES_CODE.search(header):",
     "        if WRITES_CODE.search(header):"),
    ("heredoc-code-file", HOOK, "        if INTERP.search(header) or WRITES_CODE.search(header):",
     "        if INTERP.search(header):"),
    ("heredoc-body-not-plain", HOOK, "        pos = end.end() if end else len(command)", "        pos = nl + 1"),
    ("herestring-judged", HOOK, "        if INTERP.search(tail) or WRITES_CODE.search(tail):", "        if False:"),
    ("quotes-blanked", HOOK, "    out.append(QUOTED_ML.sub(\"''\", plain))", "    out.append(plain)"),
    ("quotes-span-lines", HOOK, "    out.append(QUOTED_ML.sub(\"''\", plain))", "    out.append(QUOTED.sub(\"''\", plain))"),
    ("list-command", HOOK, '            command = " ".join(map(str, command))', "            command = None"),
    # edits
    ("edit-in-context", HOOK, "    done = after_edits(path, edits)", "    done = None"),
    ("span-limit", HOOK, "        if spans and not any(s < b + 1 and e > a for a, b in spans):", "        if False:"),
    ("replace-all-sites", HOOK, '            if not e.get("replace_all"):\n                break', "            break"),
    ("crlf-normalised", HOOK, 'text = f.read().replace("' + BS + 'r' + BS + 'n", "' + BS + 'n")', "text = f.read()"),
    ("edits-in-order", HOOK,
     '        text = text.replace(old, new) if e.get("replace_all") else text[:hits[0]] + new + text[hits[0] + len(old):]',
     "        text = text"),
    # tools and paths
    ("bash-covered", HOOK, '    if tool in ("Bash", "PowerShell", "Monitor"):', '    if tool in ("PowerShell", "Monitor"):'),
    ("powershell-covered", HOOK, '    if tool in ("Bash", "PowerShell", "Monitor"):', '    if tool in ("Bash", "Monitor"):'),
    ("monitor-covered", HOOK, '    if tool in ("Bash", "PowerShell", "Monitor"):', '    if tool in ("Bash", "PowerShell"):'),
    ("write-covered", HOOK, '    if tool == "Write":', '    if tool == "WriteX":'),
    ("notebook-covered", HOOK, '    if tool == "NotebookEdit":', '    if tool == "NotebookEditX":'),
    ("multiedit-covered", HOOK,
     '    edits = [inp] if tool == "Edit" else [e for e in (inp.get("edits") or []) if isinstance(e, dict)]',
     '    edits = [inp] if tool == "Edit" else []'),
    ("code-files-only", HOOK, "    if not low.endswith(CODE_FILES):", "    if False:"),
    ("backslash-paths", HOOK, 'or "").replace("' + BS + BS + '", "/")', 'or "")'),
    ("lowercase-ext", HOOK, "    low = path.lower()", "    low = path"),
    # protocol
    ("bom-read", HOOK, 'raw.decode("utf-8-sig", "replace")', 'raw.decode("utf-8", "replace")'),
    ("deny-printed", HOOK, '    print(json.dumps({"hookSpecificOutput"', '    0 and print(json.dumps({"hookSpecificOutput"'),
    ("fix-named", HOOK, "creationflags=CREATE_NO_WINDOW (0x08000000)", "creationflags=hidden"),
    # the bash wrapper (needs bash)
    ("wrapper-crash-not-swallowed", WRAPPER,
     '"$HOOK_DIR/no-detached-launch.py" 2>/dev/null) || exit 0', '"$HOOK_DIR/no-detached-launch.py" 2>/dev/null)'),
    ("wrapper-answer-printed", WRAPPER, '[ -n "$ANSWER" ] && printf \'%s\' "$ANSWER"', "true"),
]
WRAPPER_MUTANTS = {name for name, f, _a, _b in M if f == WRAPPER}


def bash_available():
    path = os.environ.get("X4_BASH") or shutil.which("bash")
    if path and "system32" in path.lower():
        path = None   # the WSL stub is not a usable bash
    return bool(path) or os.path.isfile(r"C:\Program Files\Git\bin\bash.exe")


def pytest_in(d):
    # cwd=d + the file by name: on the Windows runner `pytest <tempdir>` collected nothing ("no tests ran")
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", TEST],
                          capture_output=True, text=True, cwd=d)


def stage(d, sources, mutant=None):
    """Copy the test, the hook, the wrapper and its support file into D; apply MUTANT=(file, anchor, new) to its file."""
    for name in (TEST, HOOK, WRAPPER, SUPPORT):
        text = sources[name]
        if mutant and mutant[0] == name:
            text = text.replace(mutant[1], mutant[2])
        (Path(d) / name).write_text(text, encoding="utf-8", newline="")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check-anchors", action="store_true", help="only check every anchor appears exactly once")
    ap.add_argument("--only", metavar="REGEX", help="run only the mutants whose name matches (never a release pass)")
    args = ap.parse_args()

    sources = {name: (HOOKS / name).read_text(encoding="utf-8") for name in (TEST, HOOK, WRAPPER, SUPPORT)}
    mutants = list(M)
    # f-string literal tokens exist only on Python 3.12+: that mutant is equivalent (unkillable) on an older hook Python.
    hook_py = os.environ.get("HOOK_PYTHON", "").split() or [sys.executable]
    has_fstring_tokens = subprocess.run(
        hook_py + ["-c", "import tokenize,sys; sys.exit(0 if hasattr(tokenize, 'FSTRING_MIDDLE') else 1)"],
        capture_output=True).returncode == 0
    if not has_fstring_tokens:
        mutants = [m for m in mutants if m[0] != "fstring-middles"]
        print("hook Python has no f-string tokens: fstring-middles skipped")
    if not bash_available():
        mutants = [m for m in mutants if m[0] not in WRAPPER_MUTANTS]
        print("no bash: {} skipped".format(", ".join(sorted(WRAPPER_MUTANTS))))
    bad = [name for name, f, a, _b in mutants if sources[f].count(a) != 1]
    if bad:
        print("mutant anchors not found exactly once: " + ", ".join(bad))
        return 2
    if args.check_anchors:
        print("{} mutant anchors, each found exactly once".format(len(mutants)))
        return 0
    if args.only:
        mutants = [m for m in mutants if re.search(args.only, m[0])]
        if not mutants:
            print("no mutant matches --only " + args.only)
            return 2

    with tempfile.TemporaryDirectory() as d:
        stage(d, sources)
        base = pytest_in(d)
        if base.returncode != 0:
            print(base.stdout[-2000:])
            print("baseline is not green: no mutant result means anything")
            return 2

    survivors = []
    for name, f, a, b in mutants:
        with tempfile.TemporaryDirectory() as d:
            stage(d, sources, (f, a, b))
            r = pytest_in(d)
            killed = r.returncode == 1 and " failed" in r.stdout
            print(("killed   " if killed else "SURVIVED ") + name)
            if not killed:
                survivors.append(name)
    print("{} mutants, {} survived".format(len(mutants), len(survivors)))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
