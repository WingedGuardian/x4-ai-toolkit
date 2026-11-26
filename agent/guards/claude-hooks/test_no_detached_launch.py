"""no-detached-launch: run as a process with real hook payloads, and through its bash wrapper.

Run: python -m pytest .claude/hooks/test_no_detached_launch.py (the repo pytest `testpaths` is `tests`, so CI runs
this folder's file by name). HOOK_PYTHON="py -3.12" runs the hook under another interpreter than pytest."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).parent / "no-detached-launch.py"
# The hook runs under the deployed interpreter (`py -3`), which may be newer than the one running pytest:
# HOOK_PYTHON="py -3.12" judges the hook on that tokenizer.
PY = os.environ.get("HOOK_PYTHON", "").split() or [sys.executable]


def run(tool, tool_input, env=None):
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input}).encode()
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run(PY + ["-B", str(HOOK)], input=payload, capture_output=True, timeout=30, env=e)
    assert r.returncode == 0, r.stderr
    return r


def decide(tool, tool_input):
    out = run(tool, tool_input).stdout.decode().strip()
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else "allow"


INCIDENT = ("    p = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,\n"
            "                         creationflags=NO_WINDOW | 0x00000200 | 0x00000008)  # no window, new group, detached\n")


def W(content, path="x.py"):
    return ("Write", {"file_path": path, "content": content})


DENY = {
    "incident-write": W(INCIDENT),
    "named-constant": W("subprocess.Popen(c, creationflags=subprocess.DETACHED_PROCESS)\n"),
    "named-or": ("Edit", {"file_path": "missing.py", "old_string": "a", "new_string": "flags = CREATE_NO_WINDOW | DETACHED_PROCESS\n"}),
    "named-or-spaced": W("base = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP\n"),
    "or-assign": W("flags |= subprocess.DETACHED_PROCESS\n"),
    "plain-assign": W("flags = subprocess.DETACHED_PROCESS\nPopen(c, creationflags=flags)\n"),
    "create-process": W("win32process.CreateProcess(None, cmd, None, None, 0, win32process.DETACHED_PROCESS, None, None, si)\n"),
    "c-multiline": W("ok = CreateProcessW(NULL, cmd, NULL, NULL, FALSE,\n    DETACHED_PROCESS,\n    NULL, NULL, &si, &pi);\n", "x.cpp"),
    "cpp-scope": W("BOOL ok = ::CreateProcessW(NULL, cmd, NULL, NULL, FALSE, DETACHED_PROCESS, NULL, NULL, &si, &pi);\n", "x.cpp"),
    "multiline-paren": W("p = Popen(c, creationflags=(\n    NO_WINDOW |\n    0x8))\n"),
    "trailing-or": W("p = Popen(c, creationflags=NO_WINDOW |\n    subprocess.DETACHED_PROCESS,\n    cwd=x)\n"),
    "csharp-trailing-or": W("uint flags = CREATE_NO_WINDOW |\n    DETACHED_PROCESS;\n", "x.cs"),
    "dict-key":W('kw = {"creationflags": 0x8}\n'),
    "kw-index": W('kw["creationflags"] = 8\n'),
    "rust": W("Command::new(x).creation_flags(0x8).spawn()?;\n", "x.rs"),
    "detached-with-startupinfo":W("Popen(c, creationflags=0x8 | 0x10, startupinfo=si)\n"),
    "node-detached": W("spawn(cmd, args, { detached: true, stdio: 'ignore' })\n", "x.js"),
    "short-hex": ("Edit", {"file_path": "missing.py", "old_string": "a", "new_string": "Popen(c, creationflags=0x8)"}),
    "decimal": W("Popen(c, creationflags=8)"),
    "leading-zero": W("Popen(c, creationflags=0008)"),
    "combined-literal": W("Popen(c, creationflags=0x08000208)"),
    "hash-in-string": W('Popen(["echo", " #1"], creationflags=8)\n'),
    "uppercase-ext": W("Popen(c, creationflags=0x8)\n", "X.PY"),
    "backslash-path": W("Popen(c, creationflags=0x8)\n", "C:\\work\\x.py"),
    "multiedit": ("MultiEdit", {"file_path": "missing.py", "edits": [{"old_string": "a", "new_string": "ok = 1"},
                                                                     {"old_string": "b", "new_string": "Popen(c, creationflags=0x00000008)"}]}),
    "bash-inline": ("Bash", {"command": "python -c \"import subprocess;subprocess.Popen(['x'],creationflags=0x00000008)\""}),
    "bash-heredoc-python": ("Bash", {"command": "python - <<'EOF'\nimport subprocess\nsubprocess.Popen(['x'], creationflags=0x8)\nEOF"}),
    "bash-heredoc-to-file": ("Bash", {"command": "cat > drive.py <<'EOF'\np = Popen(c, creationflags=NO_WINDOW | 0x8)\nEOF"}),
    "bash-list-command": ("Bash", {"command": ["python", "-c", "Popen(c, creationflags=0x8)"]}),
    "powershell-inline": ("PowerShell", {"command": "python -c \"import subprocess as s; s.Popen(['x'], creationflags=s.DETACHED_PROCESS)\""}),
    "monitor": ("Monitor", {"command": "python -c \"from subprocess import *; Popen(['x'], creationflags=8)\""}),
    "notebook": ("NotebookEdit", {"notebook_path": "n.ipynb", "new_source": "Popen(c, creationflags=DETACHED_PROCESS)"}),
    "same-name-elsewhere": W(INCIDENT, "C:/x/other/test_no_detached_launch.py"),
    # re-review (R2-R11)
    "usage-constant-then-code": W('USAGE = """run it\n"""\np = Popen(c, creationflags=0x8)\ndef main():\n    """Entry."""\n'),
    "echo-to-code-file": ("Bash", {"command": 'echo "p = Popen(c, creationflags=0x8)" > drive.py'}),
    "printf-append-code-file": ("Bash", {"command": "printf '%s\\n' 'flags |= subprocess.DETACHED_PROCESS' >> x.py"}),
    "ps-herestring-set-content": ("PowerShell", {"command": "@'\nPopen(c, creationflags=0x8)\n'@ | Set-Content drive.py"}),
    "ps-herestring-python": ("PowerShell", {"command": "@'\nimport subprocess\nsubprocess.Popen(['x'], creationflags=subprocess.DETACHED_PROCESS)\n'@ | python -"}),
    "bash-c-nested": ("Bash", {"command": "bash -c \"python -c 'import subprocess as s; s.Popen([1], creationflags=s.DETACHED_PROCESS)'\""}),
    "cmd-c-nested": ("PowerShell", {"command": "cmd /c \"python -c \\\"Popen(c, creationflags=0x8)\\\"\""}),
    "inline-single-quote": ("Bash", {"command": "python -c 'Popen(c, creationflags=0x8)'"}),
    "node-dot-detached": W("opts.detached = true;\nspawn(cmd, args, opts);\n", "x.js"),
    "node-json-detached": W('spawn(cmd, args, {"detached": true});\n', "x.js"),
    "c-define": W("#define FLAGS (NO_WINDOW | DETACHED_PROCESS)\n", "x.c"),
    "js-glob-string":W('globby("src/**/*.js");\nspawn(cmd, args, {detached: true});\n/** doc */\n', "x.js"),
    "sh-glob": W('cp build/* out/\nnode -e "spawn(c, {detached: true})"\nls */\n', "x.sh"),
    "python-floor-div-then-flag": W("half = n // 2; p = Popen(c, creationflags=0x8)\n"),
    "printf-multiline-to-code": ("Bash", {"command": "printf '%s' 'import subprocess\nsubprocess.Popen(c, creationflags=0x8)' > x.py"}),
    "pwsh-option-values": ("PowerShell", {"command": "pwsh -NoProfile -ExecutionPolicy Bypass -Command \"python -c 'import subprocess as s; s.Popen([1], creationflags=s.DETACHED_PROCESS)'\""}),
    "python-x-utf8": ("Bash", {"command": 'python -X utf8 -c "Popen(c, creationflags=8)"'}),
    "bash-lc": ("Bash", {"command": "bash -lc \"python -c 'import subprocess as s; s.Popen([1], creationflags=s.DETACHED_PROCESS)'\""}),
    # quoted / variable interpreters and code on stdin; positional fallback, keyword before a group, Go
    "var-python-heredoc": ("Bash", {"command": "\"$PY\" - <<'EOF'\nimport subprocess\nsubprocess.Popen(['x'], creationflags=8)\nEOF"}),
    "getattr-fallback":W('Popen(c, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0x00000008))\n'),
    "getattr-fallback-or": W('Popen(c, creationflags=NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 8))\n'),
    "keyword-else-group": W("Popen(c, creationflags=NO_WINDOW if quiet else (0x200 | 0x8))\n"),
    "go-sysprocattr": W("cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.DETACHED_PROCESS}\n", "x.go"),
    "or-in-parens":W("flags = (DETACHED_PROCESS | GROUP)\n"),
    "quoted-path-interp":("Bash", {"command": '"C:/Program Files/Python313/python.exe" -c "Popen(c, creationflags=8)"'}),
    "dq-var-interp": ("Bash", {"command": '"$PY" -c "Popen(c, creationflags=8)"'}),
    "bare-var-interp": ("Bash", {"command": '$PY -c "Popen(c, creationflags=8)"'}),
    "braced-var-interp": ("Bash", {"command": '${X4_PY} -B -c "Popen(c, creationflags=8)"'}),
    "variable-interp-in-loop": ("Bash", {"command": 'for f in x; do $cmd -c "creationflags=0x8"; done'}),
    "ps-amp-path": ("PowerShell", {"command": '& "C:\\Python313\\python.exe" -c "Popen(c, creationflags=8)"'}),
    "ps-amp-var": ("PowerShell", {"command": '& $py -c "Popen(c, creationflags=8)"'}),
    "echo-pipe-python": ("Bash", {"command": 'echo "Popen(c, creationflags=8)" | python -'}),
    "here3-python": ("Bash", {"command": 'python <<< "Popen(c, creationflags=8)"'}),
    "ps-string-pipe-python": ("PowerShell", {"command": "'Popen(c, creationflags=8)' | python -"}),
    "node-p": ("Bash", {"command": 'node -p "spawn(c, {detached: true})"'}),
    "bash-lc-writes-code":("Bash", {"command": "bash -lc \"echo 'Popen(c, creationflags=8)' > x.py\""}),
    "js-slashes-in-string": W('spawn(" //x", {detached: true});\n', "x.js"),
    "spawn-only-use":W("spawn(cmd, DETACHED_PROCESS)\n", "x.js"),
    "leading-or-join": W("var creationflags = NO_WINDOW\n    | 0x8;\n", "x.cs"),
    "backslash-join": W("x = dict(a=1)\ncreationflags = NO_WINDOW \\\n    + 0x8\n"),
}

ALLOW = {
    "prose-docstring": W('"""Windows: a hidden console, never CREATE_NO_WINDOW | DETACHED_PROCESS."""\nx = 1\n'),
    "string-mention": W('msg = "never use DETACHED_PROCESS"\n'),
    # deliberately visible terminal work is allowed; only detaching (the spam) is refused
    "visible-new-console": W("Popen(c, creationflags=subprocess.CREATE_NEW_CONSOLE)\n"),
    "visible-new-console-literal": W("Popen(c, creationflags=0x10)\n"),
    "visible-console-startupinfo-none": W("Popen(c, creationflags=CREATE_NEW_CONSOLE, startupinfo=None)\n"),
    "start-process-visible": ("PowerShell", {"command": "Start-Process pwsh -ArgumentList '-NoExit','-Command','Get-Content log.txt -Wait'"}),
    "hidden-new-console": W("si.wShowWindow = SW_HIDE\np = subprocess.run(c, creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=si)\n"),
    "hidden-new-console-literal": W("si.wShowWindow = win32con.SW_HIDE\np = subprocess.run(c, creationflags=0x10, startupinfo=si)\n"),
    "multiline-prose-string": W('X = """\nNO_WINDOW | DETACHED_PROCESS\n"""\n'),
    "string-holds-flags": W('raise SystemExit("never use creationflags=0x8")\n'),
    "inline-print-flags": ("Bash", {"command": "python -c \"print('was creationflags=8')\""}),
    "bracket-string-in-value": W('p = Popen(c, creationflags=pick("(", 0x200), timeout=8)\n'),
    "grep-c-after-python": ("Bash", {"command": 'python -m pytest -q; grep -c "DETACHED_PROCESS|creationflags=8" tools/x.py'}),
    "sed-e-after-py": ("Bash", {"command": "py -3 a.py | sed -e 's/creationflags=0x8 | DETACHED_PROCESS//'"}),
    "js-string-holds-use": W('const msg = "flags = DETACHED_PROCESS was the bug";\n', "x.js"),
    "python-heredoc-prose":("Bash", {"command": "python - <<'EOF'\nrep('''Scope: never creationflags=NO_WINDOW | 0x8,\nflags = DETACHED_PROCESS is the bug''')\nEOF"}),
    "echo-pipe-python-prose": ("Bash", {"command": "echo \"print('''flags = DETACHED_PROCESS''')\" | python -"}),
    # a real false positive of an earlier version: nested "$(git -C "..." ...)" quoting before a
    # commit message that quotes the flag's spelling with \" escapes and mentions <<EOF
    "commit-msg-nested-quotes": ("Bash", {"command": r'''cd .. && git add -A x && git -c user.email="$(git -C "/c/G G/S" config user.email)" commit -q -m "fix

A1 getattr(subprocess, \"DETACHED_PROCESS\", 0x8) fallback was blanked; A2 else (A | 0x8) follows; A3 any \$VAR
as the command word; \"\$PY\" - <<EOF still counts, cat > \"\$NOTES\" <<EOF does not)." && git push -q'''}),
    "heredoc-to-var-notes":("Bash", {"command": "cat > \"$NOTES\" <<'EOF'\ndrop creationflags=NO_WINDOW | DETACHED_PROCESS\nEOF"}),
    "ps-herestring-to-var": ("PowerShell", {"command": "@'\ndrop creationflags=NO_WINDOW | DETACHED_PROCESS\n'@ | Set-Content $notesPath"}),
    "rg-var-pattern": ("Bash", {"command": 'rg "$PAT" -e "creationflags=8" tools'}),
    "git-var-c": ("Bash",{"command": '$GIT -c "user.name=x" commit -m "y"'}),
    # false positives that are not launches
    "assert-flag-absent": W("assert not (kw['creationflags'] & subprocess.DETACHED_PROCESS)\n"),
    "constants-table": W("DETACHED_PROCESS = 0x00000008\nCREATE_NO_WINDOW = 0x08000000\n"),
    "blocklist-tuple": W("BANNED = (subprocess.DETACHED_PROCESS, 'x')\n"),
    "flag-from-table-index": W("p = Popen(c, creationflags=table[8])\n"),
    "flag-from-call-arg": W("p = Popen(c, creationflags=pick(level=8))\n"),
    "rg-unquoted": ("Bash", {"command": "rg -n creationflags=8 ."}),
    "fstring-prose": W('HELP = f"""\nUse NO_WINDOW | {GROUP}, never DETACHED_PROCESS | creationflags=0x8\n"""\n'),
    "write-then-commit": ("Bash", {"command": 'git show HEAD~1:tools/v.py > old.py && git commit -m "drop creationflags=0x8"'}),
    "write-then-search": ("Bash", {"command": 'git show HEAD~1:tools/v.py > old.py && rg "DETACHED_PROCESS|creationflags=8" old.py'}),
    "python-floor-div": W("half = n // 2\nPopen(c, creationflags=NO_WINDOW)\n"),
    "js-block-comment":W("/* old: spawn(c, {detached: true}) */\nspawn(c, {windowsHide: true});\n", "x.js"),
    "js-bracket-in-string": W('let o = "(";\nconsole.log(DETACHED_PROCESS);\nspawn(c);\n', "x.js"),
    "unbalanced-regex-literal": W("let r = /[(]/;\n" + "let z = 1;\n" * 60 + "console.log(DETACHED_PROCESS);\nspawn(c);\n", "x.js"),
    "definition-then-launch": W("win32con.DETACHED_PROCESS = 0x8; Popen(c)\n"),
    "js-template-in-call": W("run(`\ncreationflags: NO_WINDOW\nretries: 8\n`);\n", "x.js"),
    "ps-herestring-notes":("PowerShell", {"command": "@'\ncreationflags=0x8 was the bug\n'@ | Set-Content notes.md"}),
    "echo-to-notes":("Bash", {"command": 'echo "creationflags=0x8 was the bug" > notes.md'}),
    "multiline-commit": ("Bash", {"command": "git commit -m 'fix\n\nflags = DETACHED_PROCESS was the bug'"}),
    "flag-then-other-kwargs":W("p = subprocess.run(cmd, capture_output=True, creationflags=0x08000000, timeout=120)\n"),
    "value-ends-at-line": W('options = dict(\n    x=1)\nif nt:\n    options["creationflags"] = NO_WINDOW\nelse:\n    tail = 64 * 1024 + 8\n'),
    "code-in-long-string": W("patch('''\nif nt:\n    options[\"creationflags\"] = NO_WINDOW\ntail = 64 * 1024 + 8\n''')\n"),
    "bracket-in-string": W('opener = "("\nprint(subprocess.DETACHED_PROCESS)\nx = 1\n'),
    "name-read-not-used":W("print(subprocess.DETACHED_PROCESS)\n"),
    "token-compare":W('if tok.string == "DETACHED_PROCESS":\n    bad = True\n'),
    "removes-flag-name": W("Popen(c, creationflags=flags & ~subprocess.DETACHED_PROCESS)\n"),
    "removes-flag-literal": W("Popen(c, creationflags=flags & ~0x8)\n"),
    "grep-search": ("Bash", {"command": r'grep -rn "0x00000008\|DETACHED_PROCESS" tools --include=*.py'}),
    "rg-search": ("Bash", {"command": 'rg -n "DETACHED_PROCESS|creationflags" tools'}),
    "select-string": ("PowerShell", {"command": "Select-String -Pattern 'DETACHED_PROCESS|CREATE_NO_WINDOW' *.py"}),
    "git-commit": ("Bash", {"command": 'git commit -m "drop DETACHED_PROCESS: was creationflags=0x8"'}),
    "docstring-flags": W('def f():\n    """Was creationflags=0x8 once; now hidden."""\n    return 1\n'),
    "heredoc-to-notes": ("Bash", {"command": "cat > notes.md <<'EOF'\ncreationflags=NO_WINDOW | DETACHED_PROCESS\nEOF"}),
    "markdown-note": W(INCIDENT, "notes.md"),
    "hidden-only": W("Popen(c, creationflags=0x08000000)\n"),
    "hidden-group": W("Popen(c, creationflags=NO_WINDOW | 0x00000200)\n"),
    "named-no-window": W("Popen(c, creationflags=subprocess.CREATE_NO_WINDOW)\n"),
    "the-fix-comment": W("    creationflags=NO_WINDOW | 0x00000200)  # hidden console, new group. NEVER detached (0x8):\n"),
    "comment-holds-old-flags": W("x = Popen(c, creationflags=NO_WINDOW)  # was creationflags=NO_WINDOW | 0x00000008\n"),
    "comment-slash": W("x = spawn(c, {windowsHide: true}); // was detached: true\n", "x.js"),
    "comment-names-it": W("# never use DETACHED_PROCESS here\nx = 1\n"),
    "batch-rem": W("REM creationflags=8 DETACHED_PROCESS = bad\r\n:: Popen(c, creationflags=8)\r\necho ok\r\n", "x.bat"),
    "unrelated-0x8": W("caps = 0x1000 | 0x400000 | 0x8\n"),
    "huge-literal": W("Popen(c, creationflags=0x100000008)\n"),
    "plain-bash": ("Bash", {"command": "git status"}),
    "read-tool": ("Read", {"file_path": "x.py"}),
}


@pytest.mark.parametrize("name", sorted(DENY))
def test_denies(name):
    assert decide(*DENY[name]) == "deny", name


@pytest.mark.parametrize("name", sorted(ALLOW))
def test_allows(name):
    assert decide(*ALLOW[name]) == "allow", name


def test_small_edit_judged_in_context(tmp_path):
    f = tmp_path / "drive.py"
    f.write_text("p = subprocess.Popen(cmd,\n    creationflags=NO_WINDOW | 0x00000200)\n", encoding="utf-8")
    edit = {"file_path": str(f), "old_string": "NO_WINDOW | 0x00000200)", "new_string": "NO_WINDOW | 0x00000200 | 0x00000008)"}
    assert decide("Edit", edit) == "deny"
    harmless = {"file_path": str(f), "old_string": "cmd,", "new_string": "cmd2,"}
    assert decide("Edit", harmless) == "allow"


def test_edit_elsewhere_in_a_file_with_an_old_bad_line_is_allowed(tmp_path):
    f = tmp_path / "old.py"
    f.write_text("a = Popen(c, creationflags=0x8)\n\n\n\ndef other():\n    return 1\n", encoding="utf-8")
    assert decide("Edit", {"file_path": str(f), "old_string": "return 1", "new_string": "return 2"}) == "allow"


def test_edit_below_a_docstring(tmp_path):
    f = tmp_path / "drive.py"
    f.write_text('"""Driver for the headless run.\n\nMore text.\n"""\nimport subprocess\n'
                 "p = subprocess.Popen(cmd,\n    creationflags=NO_WINDOW | 0x00000200)\n", encoding="utf-8")
    edit = {"file_path": str(f), "old_string": "NO_WINDOW | 0x00000200)", "new_string": "NO_WINDOW | 0x00000200 | 0x00000008)"}
    assert decide("Edit", edit) == "deny"


def test_replace_all_judges_every_site(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("GROUP = 0x00000200\n\n\n\np = Popen(c, creationflags=NO_WINDOW | 0x00000200)\n", encoding="utf-8")
    edit = {"file_path": str(f), "old_string": "0x00000200", "new_string": "0x00000208", "replace_all": True}
    assert decide("Edit", edit) == "deny"


def test_crlf_file_multiline_old_string(tmp_path):
    f = tmp_path / "x.py"
    f.write_bytes(b"p = subprocess.Popen(cmd,\r\n    creationflags=NO_WINDOW\r\n    | GROUP)\r\nq = 1\r\n")
    # the new text alone holds no creationflags: only judging it IN the file (LF-normalised) sees the bit
    edit = {"file_path": str(f), "old_string": "NO_WINDOW\n    | GROUP)", "new_string": "NO_WINDOW\n    | GROUP | 8)"}
    assert decide("Edit", edit) == "deny"
    harmless = {"file_path": str(f), "old_string": "    | GROUP)\nq = 1", "new_string": "    | GROUP)\nq = 2"}
    assert decide("Edit", harmless) == "allow"


def test_multiedit_applies_in_order(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("p = Popen(c,\n    creationflags=NO_WINDOW)\n", encoding="utf-8")
    edits = [{"old_string": "NO_WINDOW)", "new_string": "NO_WINDOW | GROUP)"},
             {"old_string": "GROUP)", "new_string": "GROUP | 0x8)"}]
    assert decide("MultiEdit", {"file_path": str(f), "edits": edits}) == "deny"


def test_docstring_edit_in_a_big_file(tmp_path):
    body = ["import subprocess", "", "def outer():", "    if True:", "        x = 1"]
    body += ["        y = %d" % i for i in range(300)]
    body += ["    z = 2"]  # a window starting inside the 8-space block cannot tokenize this dedent
    body += ["", "def later():", '    """Notes.', "", "    never NO_WINDOW | DETACHED_PROCESS here", '    """', "    return 1", ""]
    f = tmp_path / "big.py"
    f.write_text("\n".join(body), encoding="utf-8")
    edit = {"file_path": str(f), "old_string": "    never NO_WINDOW | DETACHED_PROCESS here",
            "new_string": "    never NO_WINDOW | DETACHED_PROCESS anywhere"}
    assert decide("Edit", edit) == "allow"


def test_multiedit_after_replace_all_keeps_spans(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("foo\nfoo\nfoo\nfoo\nfoo\n\n\n\np = Popen(c,\n    creationflags=NO_WINDOW)\n", encoding="utf-8")
    edits = [{"old_string": "NO_WINDOW)", "new_string": "NO_WINDOW | 0x8)"},
             {"old_string": "foo", "new_string": "x" * 200, "replace_all": True}]
    assert decide("MultiEdit", {"file_path": str(f), "edits": edits}) == "deny"


def test_unbalanced_comment_does_not_glue_the_file(tmp_path):
    f = tmp_path / "x.js"
    f.write_text("/**\n * see (below\n */\nfunction a() {}\n" + "let z = 1;\n" * 60 + "spawn(c, {detached: true});\n",
                 encoding="utf-8")
    edit = {"file_path": str(f), "old_string": "function a() {}", "new_string": "function a() { return 1; }"}
    assert decide("Edit", edit) == "allow"


def test_bom_payload_is_read():
    payload = b"\xef\xbb\xbf" + json.dumps({"tool_name": "Write", "tool_input": {"file_path": "x.py", "content":
                                                                                "Popen(c, creationflags=8)"}}).encode()
    r = subprocess.run(PY + ["-B", str(HOOK)], input=payload, capture_output=True, timeout=30)
    assert r.returncode == 0 and b'"deny"' in r.stdout


def test_garbage_fails_open():
    r = subprocess.run(PY + ["-B", str(HOOK)], input=b"{not json", capture_output=True, timeout=30)
    assert r.returncode == 0 and r.stdout.strip() == b""


def test_reason_names_the_fix():
    r = run("Write", {"file_path": "x.py", "content": INCIDENT})
    assert r.returncode == 0
    assert "CREATE_NO_WINDOW" in json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def test_reason_is_generic():
    reason = json.loads(run("Write", {"file_path": "x.py", "content": INCIDENT}).stdout)["hookSpecificOutput"][
        "permissionDecisionReason"]
    assert "0x08000000" in reason and "DETACHED" in reason
    # the public text names no private rule file and carries no date (a re-added incident note must turn this red)
    assert "CLAUDE.md" not in reason and "No visible helper" not in reason
    assert not re.search(r"20\d\d-\d\d-\d\d", reason), reason


# ---- the bash wrapper (the registered command) ----------------------------------------------------------------------

WRAPPER = Path(__file__).parent / "no-detached-launch.sh"
BASH = os.environ.get("X4_BASH") or shutil.which("bash")
if BASH and "system32" in BASH.lower():  # the WSL stub is not a usable bash
    BASH = None
GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
if not BASH and os.path.isfile(GIT_BASH):
    BASH = GIT_BASH
needs_bash = pytest.mark.skipif(not BASH, reason="no bash: the wrapper is NOT checked here")


def wrapper(stdin, **env):
    e = {k: v for k, v in os.environ.items() if k not in ("X4_PYTHON", "PYTHONUTF8", "PYTHONDONTWRITEBYTECODE")}
    e.update(env)
    return subprocess.run([BASH, str(WRAPPER)], input=stdin, capture_output=True, timeout=60, env=e)


def _payload(tool, tool_input):
    return json.dumps({"tool_name": tool, "tool_input": tool_input}).encode()


@needs_bash
def test_wrapper_denies_and_allows_like_the_hook():
    r = wrapper(_payload("Write", {"file_path": "x.py", "content": INCIDENT}), X4_PYTHON=sys.executable)
    assert r.returncode == 0 and json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny", r
    r = wrapper(_payload("Write", {"file_path": "x.py", "content": "print(1)\n"}), X4_PYTHON=sys.executable)
    assert r.returncode == 0 and r.stdout.strip() == b"", r


@needs_bash
def test_wrapper_fails_open_on_garbage_stdin():
    r = wrapper(b"{not json", X4_PYTHON=sys.executable)
    assert r.returncode == 0 and r.stdout.strip() == b"", r


@needs_bash
def test_wrapper_fails_open_without_python(tmp_path):
    """X4_PYTHON set to something that does not resolve: x4_resolve_python returns nothing; the guard is silent."""
    r = wrapper(_payload("Write", {"file_path": "x.py", "content": INCIDENT}), X4_PYTHON=str(tmp_path / "no-such-python"))
    assert r.returncode == 0 and r.stdout.strip() == b"", r


@needs_bash
def test_wrapper_fails_open_when_the_hook_crashes(tmp_path):
    """A hook that prints half a verdict and exits 1 must not become a deny, partial output or a nonzero exit."""
    for name in ("no-detached-launch.sh", "_x4-env.sh"):
        shutil.copy(WRAPPER.parent / name, tmp_path / name)
    (tmp_path / "no-detached-launch.py").write_text("import sys\nprint('{\"hookSpecificOutput\": ')\nsys.exit(1)\n",
                                                    encoding="utf-8")
    ok = subprocess.run([BASH, str(tmp_path / "no-detached-launch.sh")], input=b"{}", capture_output=True, timeout=60,
                        env=dict(os.environ, X4_PYTHON=sys.executable))
    assert ok.returncode == 0 and ok.stdout == b"", ok
