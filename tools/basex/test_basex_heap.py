"""Every java a BaseX BUILD starts carries the one heap flag, and the flag is the one place that decides it.

MEASURED 2026-11-17: the builds started java with no -Xmx, so the JVM could grow its heap to a quarter of physical RAM
(8.5 GB here) without collecting; x4eff then reached 6219 MB under the job wrapper's 6 GB cap and was killed (x4raw the
same way on 2026-11-16). With `-Xmx4g` the same x4eff build peaked at 3985 MB in the same 277 s (x4raw at 4g: 4409 MB peak, 371 s).

The checks read the launchers' STRUCTURE (shell command position, Python AST), never a substring of prose, and each has
a planted twin that must go red: a check that cannot fail is decoration.
"""
from __future__ import annotations

import ast
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import basex_run

HERE = Path(__file__).resolve().parent
BUILD_SCRIPTS = ("build-corpus.sh", "build-effective.sh")
#: The shell scripts that start java and are NOT a BaseX build: a three-document smoke test of the vendored jar.
NOT_A_BUILD = ("smoke-basex.sh",)
FLAG_ASSIGNMENT = re.compile(r'^JAVA_HEAP_FLAG="\$\(cd "\$X4VALIDATE" && uv run python "\$HERE/basex_run\.py" '
                             r'heap-flag\)"$')


# --------------------------------------------------------------------------- the shell launchers

# A command starts after a line start, ; & | ( ) a backtick or $( -- and after any run of shell keywords that introduce
# one (then do else elif if while until ! {), each of which must itself sit at command position, so `pseudo java` and
# `echo "do java"` are not launches. Then any run of wrappers that do not change what is run: VAR=value, exec, nohup,
# time, command, env (and its options), nice, timeout (options and duration), xargs (and its options).
_COMMAND_START = r"(?:^|[;&|()`]|\$\()\s*(?:(?:then|do|else|elif|if|while|until|!|\{)\s+)*"
_WRAPPERS = (r"(?:(?:[A-Za-z_]\w*=(?:[^\s`$]|\$(?!\())*|exec|nohup|time|command|builtin|env|nice|timeout|xargs|-\S+"
             r"|[0-9]+(?:\.[0-9]+)?[smhd]?)\s+)*")
# the program: java itself (bare, or by path: /usr/bin/java), or a variable that holds it ("$JAVA", "${JAVA_BIN}",
# "$JAVA_HOME/bin/java")
_PROGRAM = r"""(?:"?\$\{?(?!JAVA_HEAP_FLAG)\w*JAVA\w*\}?[^\s"]*"?|"?(?:[^\s"]*/)?java(?:\.exe)?"?)(?=\s)"""
_JAVA_AT_COMMAND_POSITION = re.compile(_COMMAND_START + _WRAPPERS + _PROGRAM + r"(?P<rest>.*)$")
_NAMES_BASEX = re.compile(r"org\.basex\.BaseX|BaseX\.jar")
_CLASSPATH_FLAG = re.compile(r"(?:^|\s)(?:-cp|-classpath|--class-path|-jar)(?:\s|$)")


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """(first line number, code) per shell command line: backslash continuations joined, comment lines and trailing
    comments dropped (a # inside quotes is kept)."""
    out, buf, start = [], "", 0
    for n, raw in enumerate(text.splitlines(), 1):
        if not buf:
            start = n
        if not buf and raw.strip().startswith("#"):
            continue
        piece = _strip_comment(raw)
        if piece.rstrip().endswith("\\"):
            buf += piece.rstrip()[:-1] + " "
            continue
        out.append((start, (buf + piece).strip()))
        buf = ""
    if buf:
        out.append((start, buf.strip()))
    return [(n, c) for n, c in out if c]


def _strip_comment(line: str) -> str:
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


def java_launches(text: str) -> list[tuple[int, str, str]]:
    """(line number, code, arguments BEFORE the classpath flag) of every command that starts java on BaseX."""
    found = []
    for n, code in _logical_lines(text):
        m = _JAVA_AT_COMMAND_POSITION.search(code)
        if not m or not _NAMES_BASEX.search(code):
            continue
        rest = m.group("rest")
        found.append((n, code, _CLASSPATH_FLAG.split(rest, 1)[0]))
    return found


def bare_java_launches(text: str) -> list[tuple[int, str]]:
    """(line number, code) of every BaseX java launch in shell `text` whose arguments BEFORE the classpath flag carry no
    heap flag: `"$JAVA_HEAP_FLAG"` or a literal -Xmx. Launches are found at command position, through exec / nohup /
    env / timeout / time / nice wrappers, a variable holding java, and over backslash continuations."""
    return [(n, code) for n, code, before in java_launches(text)
            if not re.search(r'"\$JAVA_HEAP_FLAG"|(?:^|\s)-Xmx\d+[mMgG](?:\s|$)', before)]


def _launch_count(text: str) -> int:
    return len(java_launches(text))


@pytest.mark.parametrize("script", BUILD_SCRIPTS)
def test_every_java_launch_of_a_build_script_carries_the_heap_flag(script):
    text = (HERE / script).read_text(encoding="utf-8")
    assert _launch_count(text) == 1, "the denominator moved: a build script now launches BaseX a different number of times"
    assert bare_java_launches(text) == []


@pytest.mark.parametrize("script", BUILD_SCRIPTS)
def test_the_flag_comes_from_basex_run_and_is_set_before_the_first_launch(script):
    lines = (HERE / script).read_text(encoding="utf-8").splitlines()
    assigned = [n for n, line in enumerate(lines) if FLAG_ASSIGNMENT.match(line)]
    assert len(assigned) == 1, "JAVA_HEAP_FLAG must be assigned exactly once, from basex_run.py heap-flag"
    first = next(n for n, line in enumerate(lines) if bare_java_launches(line + "\n") == [] and
                 _launch_count(line) == 1)
    assert assigned[0] < first
    # The assignment must be refused-on-failure: `set -e` is on, and nothing in the line swallows the exit status.
    assert any(line.strip() == "set -euo pipefail" for line in lines)


def test_no_other_shell_script_starts_a_build_without_the_flag():
    """The denominator: every tools/basex/*.sh, so a new build script cannot slip past the two named above."""
    scripts = sorted(HERE.glob("*.sh"))
    assert {p.name for p in scripts} >= set(BUILD_SCRIPTS) | set(NOT_A_BUILD)
    offenders = {p.name: bare_java_launches(p.read_text(encoding="utf-8"))
                 for p in scripts if p.name not in NOT_A_BUILD}
    assert {k: v for k, v in offenders.items() if v} == {}


def test_TWIN_a_hard_coded_or_unfailing_assignment_is_not_the_one_source():
    for wrong in ('JAVA_HEAP_FLAG="-Xmx4g"', 'JAVA_HEAP_FLAG=-Xmx4g',
                  'JAVA_HEAP_FLAG="$(cd "$X4VALIDATE" && uv run python "$HERE/other.py" heap-flag)"'):
        assert not FLAG_ASSIGNMENT.match(wrong), wrong
    assert FLAG_ASSIGNMENT.match('JAVA_HEAP_FLAG="$(cd "$X4VALIDATE" && uv run python "$HERE/basex_run.py" heap-flag)"')


CONT = chr(92) + chr(10)          # a shell line continuation: backslash, newline


# the twins: one per clause of the check above
@pytest.mark.parametrize("planted", [
    'java -cp BaseX.jar org.basex.BaseX -c "INFO"',                                  # no flag at all
    'java -cp BaseX.jar "$JAVA_HEAP_FLAG" org.basex.BaseX -c "INFO"',                # flag AFTER -cp is not a JVM flag
    'FOO=1 java -cp BaseX.jar org.basex.BaseX -c "INFO"',                            # an env prefix hides nothing
    'x | java -cp BaseX.jar org.basex.BaseX -c "INFO"',                              # after a pipe
    '(cd d && java -cp BaseX.jar org.basex.BaseX -c "INFO")',                        # in a subshell
    'java -Xms1g -cp BaseX.jar org.basex.BaseX -c "INFO"',                           # a different -X flag is not the heap
    '# java "$JAVA_HEAP_FLAG"\njava -cp BaseX.jar org.basex.BaseX -c "INFO"',        # the flag in a comment
    'java -cp BaseX.jar org.basex.BaseX -c "INFO" # "$JAVA_HEAP_FLAG"',
    'exec java -cp BaseX.jar org.basex.BaseX -c "INFO"',
    'nohup java -cp BaseX.jar org.basex.BaseX -c "INFO" &',
    'timeout 600 java -cp BaseX.jar org.basex.BaseX -c "INFO"',
    'timeout -k 5 600 java -cp BaseX.jar org.basex.BaseX -c "INFO"',
    'env X=1 java -cp BaseX.jar org.basex.BaseX -c "INFO"',
    'env -i X=1 FOO=2 nice -n 5 java -cp BaseX.jar org.basex.BaseX',
    'time java -cp BaseX.jar org.basex.BaseX -c "INFO"',
    '"$JAVA" -cp BaseX.jar org.basex.BaseX -c "INFO"',
    '${JAVA_BIN} -cp BaseX.jar org.basex.BaseX -c "INFO"',
    'java.exe -cp BaseX.jar org.basex.BaseX -c "INFO"',
    'if x; then java -cp BaseX.jar org.basex.BaseX -c "INFO"; fi',
    'java -cp BaseX.jar ' + CONT + '  org.basex.BaseX -c "INFO"',
    'java ' + CONT + '  -cp BaseX.jar org.basex.BaseX -c "INFO"',
    'java -jar BaseX.jar -c "INFO"',
    'java -classpath BaseX.jar org.basex.BaseX -c "INFO"',
    'if java -cp BaseX.jar org.basex.BaseX -c "INFO"; then x; fi',
    'while java -cp BaseX.jar org.basex.BaseX -c "INFO"; do x; done',
    'until java -cp BaseX.jar org.basex.BaseX -c "INFO"; do x; done',
    '! java -cp BaseX.jar org.basex.BaseX -c "INFO"',
    '{ java -cp BaseX.jar org.basex.BaseX -c "INFO"; }',
    'bx() { java -cp BaseX.jar org.basex.BaseX -c "INFO"; }',
    'x=`java -cp BaseX.jar org.basex.BaseX -c "INFO"`',
    'x=$(java -cp BaseX.jar org.basex.BaseX -c "INFO")',
    'FOO=$X java -cp BaseX.jar org.basex.BaseX',
    '/usr/bin/java -cp BaseX.jar org.basex.BaseX -c "INFO"',
    '"$JAVA_HOME/bin/java" -cp BaseX.jar org.basex.BaseX -c "INFO"',
    'echo x | xargs java -cp BaseX.jar org.basex.BaseX',
    'echo x | xargs -n1 -I{} java -cp BaseX.jar org.basex.BaseX',
    'if a; then if b; then java -cp BaseX.jar org.basex.BaseX; fi; fi',
    'case x in a) java -cp BaseX.jar org.basex.BaseX ;; esac',
])
def test_TWIN_a_planted_bare_launch_is_found(planted):
    assert bare_java_launches(planted + "\n") != []


@pytest.mark.parametrize("fine", [
    'java "$JAVA_HEAP_FLAG" -cp BaseX.jar org.basex.BaseX -c "INFO"',
    'java -Xmx4g -cp BaseX.jar org.basex.BaseX -c "INFO"',
    'x | java "$JAVA_HEAP_FLAG" -Dfoo=1 -cp BaseX.jar org.basex.BaseX',
    'exec timeout 5 env X=1 "$JAVA" "$JAVA_HEAP_FLAG" -cp BaseX.jar org.basex.BaseX',
    'java ' + CONT + '  "$JAVA_HEAP_FLAG" ' + CONT + '  -cp BaseX.jar org.basex.BaseX',
    'echo "x # y"; java "$JAVA_HEAP_FLAG" -cp BaseX.jar org.basex.BaseX',
    'command -v java >/dev/null 2>&1',                                              # not a launch
    'if java "$JAVA_HEAP_FLAG" -cp BaseX.jar org.basex.BaseX; then x; fi',
    '{ "$JAVA_HOME/bin/java" "$JAVA_HEAP_FLAG" -cp BaseX.jar org.basex.BaseX; }',
    'x=`/usr/bin/java "$JAVA_HEAP_FLAG" -cp BaseX.jar org.basex.BaseX`',
    'x=$(java "$JAVA_HEAP_FLAG" -cp BaseX.jar org.basex.BaseX)',
    'echo x | xargs java "$JAVA_HEAP_FLAG" -cp BaseX.jar org.basex.BaseX',
    'pseudo java -cp BaseX.jar org.basex.BaseX',                                    # a word ending in "do" is no keyword
    'pseudo do java -cp BaseX.jar org.basex.BaseX',
    'echo "do java -cp BaseX.jar org.basex.BaseX"',                                 # printed text, not a command
    'echo then java -cp BaseX.jar org.basex.BaseX',
    'myjava -cp BaseX.jar org.basex.BaseX',
    'java -version',                                                                  # not BaseX
])
def test_TWIN_the_scanner_accepts_a_correct_launch(fine):
    assert bare_java_launches(fine + "\n") == []


# --------------------------------------------------------------------------- the Python launchers

def _java_lists(tree: ast.AST):
    """Every list literal that is a java argv for BaseX: first element "java", and it names org.basex.BaseX."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant)
                and node.elts[0].value == "java"
                and any(isinstance(e, ast.Constant) and e.value == "org.basex.BaseX" for e in node.elts)):
            yield node


def _carries_heap_before_cp(node: ast.List) -> bool:
    """A `*_jvm_args(...)` element before the "-cp" element."""
    for e in node.elts:
        if isinstance(e, ast.Constant) and e.value == "-cp":
            return False
        if (isinstance(e, ast.Starred) and isinstance(e.value, ast.Call)
                and getattr(e.value.func, "id", None) == "_jvm_args"):
            return True
    return False


def test_every_python_java_argv_in_the_basex_folder_carries_the_heap_hook():
    found = []
    for py in sorted(HERE.glob("*.py")):
        if py.name.startswith("test_") or py.name == "conftest.py":
            continue
        for node in _java_lists(ast.parse(py.read_text(encoding="utf-8"))):
            found.append((py.name, node.lineno, _carries_heap_before_cp(node)))
    assert sorted({f[0] for f in found}) == ["basex_run.py"], "a BaseX java argv outside basex_run (test_ask bans it too)"
    assert len(found) == 2, f"the denominator moved: {found}"
    assert all(ok for _n, _l, ok in found), found


def test_TWIN_a_planted_python_launch_without_the_hook_is_found():
    bare = ast.parse('subprocess.run(["java", "-cp", "BaseX.jar", "org.basex.BaseX", "-c", c])')
    late = ast.parse('subprocess.run(["java", "-cp", "BaseX.jar", *_jvm_args(True), "org.basex.BaseX"])')
    other = ast.parse('subprocess.run(["java", *props(True), "-cp", "BaseX.jar", "org.basex.BaseX"])')
    for tree in (bare, late, other):
        (node,) = list(_java_lists(tree))
        assert _carries_heap_before_cp(node) is False
    good = ast.parse('subprocess.run(["java", *_jvm_args(heap), "-cp", "BaseX.jar", "org.basex.BaseX"])')
    assert [_carries_heap_before_cp(n) for n in _java_lists(good)] == [True]


#: The one module that answers a user's query and so keeps the JVM default heap. EVERY other Python module in this folder
#: runs only inside a build (or its swap) and must ask for the heap on every BaseX call it makes.
QUERY_TIME_MODULES = ("ask.py",)


def _basex_calls(tree: ast.AST):
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("run_command", "run_query")]


def _heap_true(call: ast.Call) -> bool:
    return any(k.arg == "heap" and isinstance(k.value, ast.Constant) and k.value.value is True for k in call.keywords)


def test_every_basex_call_in_a_build_module_asks_for_the_heap():
    """Caller side: a build module that calls run_query / run_command without heap=True starts a default-heap JVM (a
    quarter of RAM) inside a capped build. coverage.py did exactly that, from both build scripts."""
    seen, offenders = {}, []
    for py in sorted(HERE.glob("*.py")):
        if py.name.startswith("test_") or py.name in ("conftest.py", "basex_run.py"):
            continue
        calls = _basex_calls(ast.parse(py.read_text(encoding="utf-8")))
        if calls:
            seen[py.name] = len(calls)
        if py.name not in QUERY_TIME_MODULES:
            offenders += [f"{py.name}:{c.lineno}" for c in calls if not _heap_true(c)]
    assert seen == {"ask.py": 1, "coverage.py": 1, "dbswap.py": 4}, f"the denominator moved: {seen}"
    assert offenders == []


def test_TWIN_a_call_without_heap_true_is_found():
    for src in ('r.run_command("INFO", home)', 'r.run_query(q, home, heap=False)', 'r.run_query(q, home, heap=x)',
                '_basex_run().run_query(xquery, BASEX_DIR)'):
        (call,) = _basex_calls(ast.parse(src))
        assert not _heap_true(call), src
    for src in ('r.run_command("INFO", home, heap=True)', '_basex_run().run_query(xquery, BASEX_DIR, heap=True)'):
        (ok,) = _basex_calls(ast.parse(src))
        assert _heap_true(ok), src


def test_TWIN_the_query_time_exemption_is_the_answering_module_only():
    assert QUERY_TIME_MODULES == ("ask.py",)
    assert "heap=True" not in (HERE / "ask.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- the one setting

@pytest.fixture(autouse=True)
def _no_ambient_heap(monkeypatch):
    monkeypatch.delenv("X4_BASEX_HEAP", raising=False)
    monkeypatch.setattr(basex_run, "_physical_ram_mb", lambda: 32 * 1024)   # a fixed machine: no test depends on this one


class _Proc:
    returncode = 0
    stdout = b"r\n"
    stderr = b""


def _argv(monkeypatch, tmp_path, call):
    seen = {}

    def fake(argv, **_kw):
        seen["argv"] = argv
        return _Proc()
    monkeypatch.setattr(basex_run.subprocess, "run", fake)
    call(tmp_path)
    return seen["argv"]


@pytest.mark.parametrize("call", [lambda d: basex_run.run_command("INFO", d, heap=True),
                                  lambda d: basex_run.run_query("1", d, heap=True)])
def test_heap_true_puts_the_flag_before_dash_cp(monkeypatch, tmp_path, call):
    argv = _argv(monkeypatch, tmp_path, call)
    assert argv[0] == "java" and "-Xmx4g" in argv
    assert argv.index("-Xmx4g") < argv.index("-cp")


@pytest.mark.parametrize("call", [lambda d: basex_run.run_command("INFO", d),
                                  lambda d: basex_run.run_query("1", d)])
def test_TWIN_an_interactive_query_keeps_the_jvm_default(monkeypatch, tmp_path, call):
    """ask.py's queries are not builds: no heap flag unless the caller says it is one."""
    assert not [a for a in _argv(monkeypatch, tmp_path, call) if a.startswith("-Xmx")]


def test_the_environment_changes_the_one_setting_everywhere(monkeypatch, tmp_path):
    monkeypatch.setenv(basex_run.HEAP_ENV, "6144M")
    assert basex_run.heap_flag() == "-Xmx6144m"
    assert "-Xmx6144m" in _argv(monkeypatch, tmp_path, lambda d: basex_run.run_command("INFO", d, heap=True))


@pytest.mark.parametrize("bad", ["4", "g", "0g", "4gb", "-4g", "4.5g", "4g -Xms1g", "4g;x", " 4 g", "0", "1t",
                                 "1m", "255m", "99999999999g", "33g", "33000m"])
def test_a_bad_heap_is_refused_not_defaulted_or_passed_to_java(monkeypatch, tmp_path, bad):
    monkeypatch.setenv(basex_run.HEAP_ENV, bad)
    with pytest.raises(ValueError, match=basex_run.HEAP_ENV):
        basex_run.heap_flag()
    monkeypatch.setattr(basex_run.subprocess, "run", lambda *a, **k: pytest.fail("java started on a refused heap"))
    with pytest.raises(ValueError):
        basex_run.run_command("INFO", tmp_path, heap=True)


@pytest.mark.parametrize("good", ["4g", "4G", "6144m", "1g", "16g", "256m", "32g"])
def test_TWIN_a_good_heap_is_accepted(monkeypatch, good):
    monkeypatch.setenv(basex_run.HEAP_ENV, good)
    assert basex_run.heap_flag() == f"-Xmx{good.lower()}"


def test_the_range_edges_are_exact(monkeypatch):
    for ok, bad in (("256m", "255m"), ("32g", "32769m")):
        monkeypatch.setenv(basex_run.HEAP_ENV, ok)
        assert basex_run.heap_flag() == f"-Xmx{ok}"
        monkeypatch.setenv(basex_run.HEAP_ENV, bad)
        with pytest.raises(ValueError):
            basex_run.heap_flag()


def test_the_upper_bound_is_physical_ram_and_a_fixed_cap_when_it_cannot_be_read(monkeypatch):
    monkeypatch.setattr(basex_run, "_physical_ram_mb", lambda: 8 * 1024)
    monkeypatch.setenv(basex_run.HEAP_ENV, "9g")
    with pytest.raises(ValueError, match="physical RAM"):
        basex_run.heap_flag()
    monkeypatch.setattr(basex_run, "_physical_ram_mb", lambda: None)
    monkeypatch.setenv(basex_run.HEAP_ENV, "64g")
    assert basex_run.heap_flag() == "-Xmx64g"
    monkeypatch.setenv(basex_run.HEAP_ENV, "65g")
    with pytest.raises(ValueError, match="could not be read"):
        basex_run.heap_flag()


def test_the_real_machine_reports_its_ram():
    """The autouse fixture replaces the reader; a FRESH copy of the module has the real one. MEASURED here: it answers
    (Windows GlobalMemoryStatusEx, POSIX sysconf). A platform where it cannot gets the fixed bound, tested above."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("basex_run_real_ram", HERE / "basex_run.py")
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    ram = fresh._physical_ram_mb()
    if ram is None:
        pytest.skip("physical RAM is unreadable on this platform; the fixed upper bound applies")
    assert 512 <= ram < 100 * 1024 * 1024


def test_an_empty_setting_means_the_default(monkeypatch):
    monkeypatch.setenv(basex_run.HEAP_ENV, "  ")
    assert basex_run.heap_flag() == f"-Xmx{basex_run.DEFAULT_HEAP}" == "-Xmx4g"


# --------------------------------------------------------------------------- the job figure for the wrapper

def test_the_job_figure_is_the_heap_plus_the_named_allowance():
    heap_gb = basex_run.heap_mb() / 1024
    assert heap_gb == 4
    assert float(basex_run.job_memory_gb()) == heap_gb + basex_run.JVM_NON_HEAP_MB / 1024 == 5.5
    assert basex_run.job_memory_gb() == "5.5"


def test_TWIN_the_figure_reads_the_allowance_constant_and_the_heap(monkeypatch):
    monkeypatch.setattr(basex_run, "JVM_NON_HEAP_MB", 0)
    assert basex_run.job_memory_gb() == "4"
    monkeypatch.setattr(basex_run, "JVM_NON_HEAP_MB", 1536)
    monkeypatch.setenv(basex_run.HEAP_ENV, "6g")
    assert basex_run.job_memory_gb() == "7.5"
    monkeypatch.setenv(basex_run.HEAP_ENV, "1000m")
    assert basex_run.job_memory_gb() == "2.48"        # 2536 MB is 2.4765625 GB: rounded UP, never down
    assert float(basex_run.job_memory_gb()) >= (1000 + 1536) / 1024


def test_the_allowance_covers_what_a_g1_heap_can_add_beyond_it():
    """INFERRED, not measured: G1 bookkeeping alone can reach 10-20% of the heap (0.4-0.8 GB at 4g) and the code cache
    reserves 240 MB, so an allowance under 1 GiB would put the cap below a heap-full build."""
    assert basex_run.JVM_NON_HEAP_MB >= 1024


def test_the_command_line_prints_the_flag_and_the_figure():
    env = {k: v for k, v in os.environ.items() if k != basex_run.HEAP_ENV}   # env-ok: copies the environment for a child
    run = lambda *a, **e: subprocess.run([sys.executable, str(HERE / "basex_run.py"), *a], capture_output=True,
                                         text=True, env={**env, **e}, timeout=60)
    ok = run("heap-flag")
    assert (ok.returncode, ok.stdout.strip(), ok.stderr) == (0, "-Xmx4g", "")
    assert run("job-gb").stdout.strip() == "5.5"
    assert run("heap-flag", X4_BASEX_HEAP="8g").stdout.strip() == "-Xmx8g"
    bad = run("heap-flag", X4_BASEX_HEAP="lots")
    assert bad.returncode == 2 and bad.stdout == "" and "X4_BASEX_HEAP" in bad.stderr
    assert run().returncode == 2 and run("nope").returncode == 2


@pytest.mark.skipif(shutil.which("java") is None, reason="needs java on PATH")
def test_a_real_jvm_takes_the_flag_and_it_beats_java_tool_options(monkeypatch):
    """MEASURED 2026-11-17 (Java 24): the command-line -Xmx wins over JAVA_TOOL_OPTIONS. Read here from the JVM's own
    PrintFlagsFinal, so a JDK that changed the precedence turns this red instead of the cap quietly not holding."""
    env = {**os.environ, "JAVA_TOOL_OPTIONS": "-Xmx1g"}  # env-ok: copies the environment for a child
    env.pop("_JAVA_OPTIONS", None)
    out = subprocess.run(["java", basex_run.heap_flag(), "-XX:+PrintFlagsFinal", "-version"], capture_output=True,
                         text=True, env=env, timeout=60)
    m = re.search(r"MaxHeapSize\s*=\s*(\d+)", out.stdout)
    assert m, out.stdout[:400]
    assert int(m.group(1)) == 4 * 1024 ** 3, m.group(0)
