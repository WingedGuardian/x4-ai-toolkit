"""run-gates.sh keeps each gate's FULL output and the machine state (GitHub issue #3).

Issue #3: `cross_tool` failed once in a release sweep (`x4effective build succeeded: exit 2`)
and never reproduced. run-gates.sh had kept only `tail -6` of a failing gate's output and
`tail -1` of a could-not-run one, and nothing recorded free memory or load, so the one
failure left nothing to read. These tests run the REAL script against a scratch gates/ tree
(ok / fail / cannot gates), with `uv` stubbed to the current interpreter so the run takes
seconds and touches no real configuration.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
SCRIPT = PKG / "scripts" / "run-gates.sh"
_spec = importlib.util.spec_from_file_location("gitbash_rgl", PKG.parents[1] / "scripts" / "gitbash.py")
gitbash = importlib.util.module_from_spec(_spec)
sys.modules["gitbash_rgl"] = gitbash
_spec.loader.exec_module(gitbash)

#: a FAILING gate prints 40 numbered lines; only the last 6 fit the summary's tail.
FAIL_GATE = ("import sys\n"
             "for i in range(1, 41):\n"
             "    print(f'bad-line-{i:02d}')\n"
             "print('bad-stderr-marker', file=sys.stderr)\n"
             "sys.exit(1)\n")
CANNOT_GATE = "import sys\nprint('cannot-first-line')\nprint('cannot-last-line')\nsys.exit(2)\n"
OK_GATE = "print('ok-gate-output')\n"


def _bash():
    b = gitbash.find_bash() if os.name == "nt" else shutil.which("bash")
    if not b:
        pytest.skip("no bash")
    return b


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "pkg"
    (root / "scripts").mkdir(parents=True)
    (root / "gates").mkdir()
    shutil.copy2(SCRIPT, root / "scripts" / "run-gates.sh")
    # the log-dir refusal asks x4validate._paths which roots the TOOLS resolve
    shutil.copytree(PKG / "x4validate", root / "x4validate",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for name, body in (("aa_ok", OK_GATE), ("bb_fail", FAIL_GATE), ("cc_cannot", CANNOT_GATE)):
        (root / "gates" / f"{name}.py").write_text(body, encoding="utf-8")
    stub = tmp_path / "bin"
    stub.mkdir()
    # `uv run python <file>` -> this interpreter. A shell stub works under Git Bash and POSIX.
    (stub / "uv").write_text('#!/bin/bash\nshift 2\nexec "' + Path(sys.executable).as_posix() + '" "$@"\n',
                             encoding="utf-8")
    os.chmod(stub / "uv", 0o755)
    return root


#: FX-B2 (reviewer C): the default leaked the developer's X4_TOOLKIT / X4_CONFIG, and the
#: copied resolver (no toolkit layout here) then read THEIR config. Every X4 root is dropped.
_DROP = ("X4_GATE_LOG_DIR", "X4_GAME", "X4_REFERENCE", "X4_TOOLKIT", "X4_CONFIG", "X4_EXTENSIONS",
         "X4_GAME_EXTENSIONS", "X4_GAME_ROOT", "X4_PROFILE", "X4_MODS")


def _run(tmp_path, root, drop=_DROP, mode="", **env_extra):
    env = {k: v for k, v in os.environ.items() if k not in drop}
    stub = (tmp_path / "bin").as_posix()
    if os.name == "nt":
        stub = "/" + stub[0].lower() + stub[2:]          # MSYS spelling for Git Bash's PATH
    env["X4_GATES_PATH_PREPEND"] = stub
    env.update(env_extra)
    r = subprocess.run([_bash(), "-c", 'export PATH="$X4_GATES_PATH_PREPEND:$PATH"; exec bash scripts/run-gates.sh '+mode],
                       cwd=root, capture_output=True, env=env, timeout=120)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def test_all_caps_hook_replay_workers_without_changing_other_gate_arguments(tmp_path):
    root = _tree(tmp_path)
    (root / "gates" / "hook_false_positives.py").write_text(
        "import sys\nassert sys.argv[1:] == ['--workers=8'], sys.argv\nprint('eight workers')\n",
        encoding="utf-8")
    (root / "gates" / "aa_ok.py").write_text(
        "import sys\nassert sys.argv[1:] == [], sys.argv\nprint('unchanged arguments')\n",
        encoding="utf-8")
    rc, out, err = _run(tmp_path, root, mode="--all", X4_GATE_LOG_DIR=str(tmp_path / "logs"))
    assert rc == 1, (rc, out, err)  # the fixture's bb_fail still fails
    d = _logdir(out)
    assert (d / "hook_false_positives.log").read_text().strip() == "eight workers"
    assert (d / "aa_ok.log").read_text().strip() == "unchanged arguments"


def _logdir(out: str) -> Path:
    m = re.search(r"^GATE LOGS: (.+)$", out, re.M)
    assert m, "the summary does not name the log directory:\n" + out
    p = m.group(1).strip()
    if os.name == "nt" and re.match(r"^/[a-z]/", p):
        p = p[1].upper() + ":" + p[2:]
    return Path(p)


def test_a_failing_gates_FULL_output_is_readable_afterwards(tmp_path):
    root = _tree(tmp_path)
    logs = tmp_path / "gate-logs"
    rc, out, err = _run(tmp_path, root, X4_GATE_LOG_DIR=str(logs))
    assert rc == 1, (rc, out, err)
    d = _logdir(out)
    assert d.resolve() == logs.resolve(), (d, logs)
    text = (d / "bb_fail.log").read_text(encoding="utf-8")
    assert "bad-line-01" in text and "bad-line-40" in text, text   # the HEAD survives, not only the tail
    assert "bad-stderr-marker" in text, text                       # stderr too
    # every attempted gate has a log, the passing and could-not-run ones included
    assert (d / "aa_ok.log").read_text(encoding="utf-8").strip() == "ok-gate-output"
    assert "cannot-first-line" in (d / "cc_cannot.log").read_text(encoding="utf-8")


def test_TWIN_the_summary_still_shows_only_the_short_tail(tmp_path):
    root = _tree(tmp_path)
    rc, out, err = _run(tmp_path, root, X4_GATE_LOG_DIR=str(tmp_path / "gate-logs"))
    assert "bad-line-40" in out and "bad-line-35" in out, out      # tail -6 still printed
    assert "bad-line-01" not in out and "bad-line-34" not in out, out
    assert "cannot-last-line" in out and "cannot-first-line" not in out, out


def test_machine_state_is_recorded_at_start_and_at_each_failure(tmp_path):
    root = _tree(tmp_path)
    rc, out, err = _run(tmp_path, root, X4_GATE_LOG_DIR=str(tmp_path / "gate-logs"))
    sysf = _logdir(out) / "system.txt"
    text = sysf.read_text(encoding="utf-8")
    assert re.search(r"^=== start ", text, re.M), text
    assert re.search(r"^=== FAIL bb_fail ", text, re.M), text
    assert not re.search(r"^=== FAIL aa_ok", text, re.M), text      # only failures, not every gate
    # memory and load are the two fields issue #3 lacked; each is a value or says it is unavailable
    for field in ("mem_available", "load"):
        assert len(re.findall(rf"^{field}: \S", text, re.M)) >= 2, (field, text)


def test_the_default_log_dir_is_a_fresh_temp_dir_never_the_tree(tmp_path):
    root = _tree(tmp_path)
    tmpd = tmp_path / "tmpdir"
    tmpd.mkdir()
    rc, out, err = _run(tmp_path, root, TMPDIR=str(tmpd))
    d = _logdir(out).resolve()
    assert tmpd.resolve() in d.parents, (d, tmpd)
    assert root.resolve() not in d.parents and d != root.resolve()
    assert (d / "bb_fail.log").is_file()


@pytest.mark.parametrize("var", ["X4_GAME", "X4_REFERENCE"])
def test_a_log_dir_inside_the_game_or_reference_REFUSES_before_any_gate(tmp_path, var):
    root = _tree(tmp_path)
    protected = tmp_path / "protected"
    protected.mkdir()
    rc, out, err = _run(tmp_path, root, **{var: str(protected),
                                           "X4_GATE_LOG_DIR": str(protected / "logs")})
    assert rc == 2 and "REFUSING" in err, (rc, out, err)
    assert not (protected / "logs").exists()
    assert "bb_fail" not in out


def test_TWIN_a_log_dir_OUTSIDE_a_configured_game_tree_runs(tmp_path):
    """The refusal is about WHERE the logs go, not about X4_GAME being set."""
    root = _tree(tmp_path)
    protected = tmp_path / "protected"
    protected.mkdir()
    rc, out, err = _run(tmp_path, root, X4_GAME=str(protected), X4_REFERENCE=str(protected),
                        X4_GATE_LOG_DIR=str(tmp_path / "protected-sibling" / "logs"))
    assert rc == 1 and "REFUSING" not in err, (rc, out, err)
    assert (tmp_path / "protected-sibling" / "logs" / "bb_fail.log").is_file()


def test_a_log_dir_inside_a_game_named_ONLY_in_the_path_config_REFUSES(tmp_path):
    """v4.0.0 review R4-7/R6-10: the refusal looked only at EXPORTED X4_GAME/X4_REFERENCE, so
    a game root named in x4-paths.env -- the installers' normal setup -- was not protected."""
    root = _tree(tmp_path)
    protected = tmp_path / "protected"
    protected.mkdir()
    tk = tmp_path / "tk"
    tk.mkdir()
    (tk / "x4-paths.env").write_text('X4_GAME="%s"' % protected.as_posix() + chr(10), encoding="utf-8")
    rc, out, err = _run(tmp_path, root, X4_TOOLKIT=str(tk), X4_GATE_LOG_DIR=str(protected / "logs"))
    assert rc == 2 and "REFUSING" in err and "X4_GAME" in err, (rc, out, err)
    assert not (protected / "logs").exists()


def test_the_containment_check_needs_no_realpath():
    """`realpath -m` does not exist on macOS: the check failed OPEN there (R4-7)."""
    code = [ln for ln in SCRIPT.read_text(encoding="utf-8").splitlines()
            if not ln.lstrip().startswith("#")]
    # The SHELL command; Python's os.path.realpath (FX-B2: links are resolved) is portable.
    calls = [ln for ln in code if "realpath" in ln and "os.path.realpath" not in ln]
    assert not calls, "run-gates.sh still CALLS realpath"


def _link_dir(link: Path, target: Path) -> None:
    """A directory link: a symlink on POSIX, a JUNCTION on Windows (no privilege needed)."""
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            pytest.skip("cannot create a junction here: " + r.stdout + r.stderr)
    else:
        os.symlink(target, link, target_is_directory=True)


def test_FXB2_a_log_dir_reached_through_a_LINK_into_the_game_REFUSES(tmp_path):
    """Delta review: containment compared abspath only, so a log dir that is a symlink or
    junction INTO a protected tree passed as "outside". Links are resolved now."""
    root = _tree(tmp_path)
    protected = tmp_path / "protected"
    protected.mkdir()
    link = tmp_path / "innocent-looking"
    _link_dir(link, protected)
    rc, out, err = _run(tmp_path, root, X4_GAME=str(protected), X4_GATE_LOG_DIR=str(link / "logs"))
    assert rc == 2 and "REFUSING" in err, (rc, out, err)
    assert not (protected / "logs").exists()


# --- FX-H2 / reviewer M-3: an empty array under `set -u` on bash < 4.4 ----------------------
#: `name=()` declares an array that may stay EMPTY; `name+=(...)` may never run.
_EMPTY_DECL = re.compile(r"^\s*(?:local\s+)?([A-Za-z_][A-Za-z0-9_]*)=\(\)", re.M)


def _unguarded_empty_array_expansions(text: str) -> list[str]:
    """Arrays declared `name=()` that are expanded bare as `"${name[@]}"`.

    Under `set -u`, bash before 4.4 (macOS ships 3.2) treats `"${name[@]}"` of an EMPTY array
    as an unbound variable and aborts. The portable spelling is `${name[@]+"${name[@]}"}`.
    READ, not reproduced: every bash this suite can reach (Git Bash 5.2, Ubuntu CI) is >= 4.4.
    """
    names = set(_EMPTY_DECL.findall(text))
    bad = []
    for n in sorted(names):
        guarded = '${%s[@]+"${%s[@]}"}' % (n, n)
        bare = '"${%s[@]}"' % n
        if text.replace(guarded, "").count(bare):
            bad.append(n)
    return bad


def test_no_possibly_empty_array_is_expanded_bare_under_set_u():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "set -uo pipefail" in text, "fixture assumption: run-gates.sh runs under set -u"
    assert _unguarded_empty_array_expansions(text) == []


def test_TWIN_the_empty_array_rule_can_fail_and_spares_the_guarded_form():
    assert _unguarded_empty_array_expansions('a=()\ncmd "${a[@]}"\n') == ["a"]
    assert _unguarded_empty_array_expansions('a=()\ncmd ${a[@]+"${a[@]}"}\n') == []
    assert _unguarded_empty_array_expansions('a=(x)\ncmd "${a[@]}"\n') == []      # never empty
