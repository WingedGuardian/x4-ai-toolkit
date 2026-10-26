"""Bulk file policy: the same decisions, one backend, complete fail-closed replies."""
import importlib.util
import time

import pytest

from codex_testlib import REPO


def load_guard():
    path = REPO / "agent/guards/claude-hooks/x4guard.py"
    spec = importlib.util.spec_from_file_location("batch_guard", path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    return guard


@pytest.fixture
def guard(monkeypatch, tmp_path):
    roots = {
        "X4_TOOLKIT": REPO, "X4_GAME": tmp_path / "game",
        "X4_REFERENCE": tmp_path / "reference", "X4_MODS": tmp_path / "dev",
        "X4_PROFILE": tmp_path / "profile", "X4_EXTENSIONS": tmp_path / "game/extensions",
        "X4_DOCUMENTS": tmp_path / "Documents", "X4_SAVES": tmp_path / "profile/save",
        "X4_CONFIG": tmp_path / "no-config",
    }
    for key, value in roots.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv("X4_GUARD", raising=False)
    return load_guard(), roots


def test_batch_same_policy_per_item(guard):
    g, r = guard
    cases = [
        (r["X4_MODS"] / "one.xml", "allow"),
        (r["X4_REFERENCE"] / "one.xml", "deny"),
        (r["X4_GAME"] / "libraries/one.xml", "deny"),
        (r["X4_PROFILE"] / "config.xml", "ask"),
        (r["X4_MODS"] / "one.cat", "deny"),
        (r["X4_MODS"] / "content.xml", "advise"),
        (r["X4_DOCUMENTS"] / "other.txt", "advise"),
        (r["X4_MODS"] / ".codex/sessions/session.jsonl", "deny"),
    ]
    paths = [str(path) for path, _ in cases]
    batch = g.write_verdicts(paths, deadline=time.monotonic() + 35, save_qualified=False)
    assert len(batch) == len(cases)
    for (path, expected), bulk in zip(cases, batch):
        single = g.verdict_for("write", None, None, str(path), save_qualified=False)
        assert bulk == single, (path, bulk, single)
        assert bulk["decision"] == expected and not bulk["inert"], bulk


def test_batch_uses_one_backend_and_checks_every_path(guard, monkeypatch):
    g, r = guard
    original = g._run_bounded
    invocations = []
    def observe(*args, **kwargs):
        invocations.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(g, "_run_bounded", observe)
    paths = [str(r["X4_MODS"] / f"file-{n}.xml") for n in range(128)]
    replies = g.write_verdicts(paths, deadline=time.monotonic() + 35, save_qualified=False)
    assert len(replies) == 128 and all(v["decision"] == "allow" and not v["inert"] for v in replies)
    assert len(invocations) == 1


@pytest.mark.parametrize("output", [b"", b"allow\\0", b"garbage", b"allow\\0\\0\\0extra"])
def test_batch_incomplete_output_is_inert(guard, monkeypatch, output):
    g, r = guard
    monkeypatch.setattr(g, "_run_bounded", lambda *a, **kw: (0, output, b""))
    replies = g.write_verdicts([str(r["X4_MODS"] / "a")], deadline=time.monotonic() + 5,
                              save_qualified=False)
    assert replies and all(v["decision"] == "deny" and v["inert"] for v in replies)


@pytest.mark.parametrize("position", [0, 64, 127])
def test_forbidden_path_at_every_batch_position(guard, position):
    g, r = guard
    paths = [str(r["X4_MODS"] / f"file-{n}.xml") for n in range(128)]
    paths[position] = str(r["X4_REFERENCE"] / "forbidden.xml")
    replies = g.write_verdicts(paths, deadline=time.monotonic() + 35, save_qualified=False)
    assert len(replies) == 128
    assert [i for i, v in enumerate(replies) if v["decision"] == "deny"] == [position]
    assert not any(v["inert"] for v in replies)


def test_aliases_settings_and_unicode_match_single(guard, monkeypatch):
    g, r = guard
    monkeypatch.setenv("CODEX_HOME", str(r["X4_MODS"] / "relocated"))
    paths = [
        str(r["X4_MODS"] / "space üñïcode [1].xml"),
        str(r["X4_REFERENCE"]) + "./other.xml",
        str(r["X4_REFERENCE"] / "one.xml") + "::$DATA",
        str(r["X4_MODS"] / "../reference/one.xml"),
        str(r["X4_MODS"] / ".claude/settings.json"),
        str(r["X4_MODS"] / "relocated/sessions/transcript.jsonl"),
        str(r["X4_GAME"] / ".claude/hooks/../../libraries/one.xml"),
        str(r["X4_GAME"] / "extensions/a/content.xml"),
    ]
    bulk = g.write_verdicts(paths, deadline=time.monotonic() + 35, save_qualified=False)
    single = [g.verdict_for("write", None, None, p, save_qualified=False) for p in paths]
    assert bulk == single
    assert [v["decision"] for v in bulk] == ["allow", "deny", "deny", "deny",
                                           "deny", "deny", "deny", "deny"]


def test_guards_off_retains_annotations_and_cannot_relax_faults(guard, monkeypatch):
    g, r = guard
    monkeypatch.setenv("X4_GUARD", "off")
    paths = [str(r["X4_MODS"] / "a"), str(r["X4_REFERENCE"] / "b")]
    bulk = g.write_verdicts(paths, deadline=time.monotonic() + 10, save_qualified=False)
    assert bulk == [g.verdict_for("write", None, None, p, save_qualified=False) for p in paths]
    assert all(v["decision"] == "advise" and "GUARDS OFF" in v["context"] for v in bulk)
    monkeypatch.setattr(g, "_run_bounded", lambda *a, **kw: (None, b"", b""))
    assert all(v["inert"] and v["decision"] == "deny" for v in
               g.write_verdicts(paths, deadline=time.monotonic() + 10, save_qualified=False))


@pytest.mark.parametrize("rc,out", [
    (None, b""), (2, b""), (1, b""),
    (0, b"X4FILE1\0" + b"2\0allow\0\0\0"),
    (0, b"X4FILE1\0" + b"1\0allow\0\0\0extra\0"),
    (0, b"X4FILE1\0" + b"1\0allow\0\0\0\xff"),
    (0, b"X4FILE1\0" + b"1\0ask\0X4 GUARD INERT: could not check\0\0"),
    (0, b"X4FILE1\0" + b"1\0allow\0unexpected reason\0\0"),
    (0, b"X4FILE1\0" + b"1\0advise\0\0\0"),
])
def test_faults_refuse_entire_batch(guard, monkeypatch, rc, out):
    g, r = guard
    monkeypatch.setattr(g, "_run_bounded", lambda *a, **kw: (rc, out, b"fixture failure"))
    replies = g.write_verdicts([str(r["X4_MODS"] / "a")], deadline=time.monotonic() + 5,
                              save_qualified=False)
    assert replies and all(v["decision"] == "deny" and v["inert"] for v in replies)


def test_missing_backend_and_expired_deadline_are_inert(guard, monkeypatch, tmp_path):
    g, r = guard
    paths = [str(r["X4_MODS"] / "a")]
    assert g.write_verdicts(paths, deadline=time.monotonic() - 1, save_qualified=False)[0]["inert"]
    monkeypatch.setattr(g, "resolve_bash", lambda: (None, "fixture missing bash"))
    assert g.write_verdicts(paths, deadline=time.monotonic() + 5, save_qualified=False)[0]["inert"]
    monkeypatch.setattr(g, "HERE", tmp_path)
    assert g.write_verdicts(paths, deadline=time.monotonic() + 5, save_qualified=False)[0]["inert"]


def test_3589_path_request_fits_unchanged_deadline(guard):
    g, r = guard
    paths = [str(r["X4_MODS"] / f"ror/assets/ships/ship-{n}/macros/ship.xml") for n in range(3589)]
    start = time.monotonic()
    replies = g.write_verdicts(paths, deadline=start + 35, save_qualified=False)
    elapsed = time.monotonic() - start
    assert len(replies) == 3589
    assert all(v["decision"] == "allow" and not v["inert"] for v in replies)
    assert elapsed < 35, elapsed


def test_normalizer_output_followed_by_failure_is_inert(guard, monkeypatch, tmp_path):
    import shutil
    g, r = guard
    hooks = tmp_path / "fault-hooks"
    shutil.copytree(g.HERE, hooks, ignore=shutil.ignore_patterns("__pycache__"))
    env_script = hooks / "_x4-env.sh"
    env_script.write_text(env_script.read_text(encoding="utf-8") +
                          "\nx4_norm_stream() { command cat; return 9; }\n", encoding="utf-8")
    monkeypatch.setattr(g, "HERE", hooks)
    replies = g.write_verdicts([str(r["X4_MODS"] / "a")], deadline=time.monotonic() + 10,
                              save_qualified=False)
    assert replies[0]["inert"], replies


@pytest.mark.parametrize("fault", ["relocated-root", "alias-output"])
def test_remaining_normalization_faults_are_inert(guard, monkeypatch, tmp_path, fault):
    import shutil
    g, r = guard
    hooks = tmp_path / "normalization-fault-hooks"
    shutil.copytree(g.HERE, hooks, ignore=shutil.ignore_patterns("__pycache__"))
    script = hooks / "_x4-env.sh"
    text = script.read_text(encoding="utf-8")
    if fault == "relocated-root":
        monkeypatch.setenv("CODEX_HOME", str(r["X4_MODS"] / "relocated"))
        text = text.replace("x4_norm_stream() {", "original_norm_stream() {")
        text += """
x4_norm_stream() {
  local input
  input="$(cat)"
  [ "$input" = "$CODEX_HOME/sessions" ] && return 9
  printf '%s' "$input" | original_norm_stream
}
"""
        path = str(r["X4_MODS"] / "relocated/sessions/transcript.jsonl")
    else:
        text += """
sed() {
  command sed "$@" || return $?
  case "$2" in s\\#*) printf 'ALIAS_FAULT_INJECTED\\n' >&2; return 9 ;; esac
}
"""
        # abspath on Windows strips a final dot before Bash ever sees it.
        # ADS survives that step and exercises x4_winpath's alias sed.
        path = str(r["X4_MODS"] / "alias.xml") + "::$DATA"
    script.write_text(text, encoding="utf-8")
    monkeypatch.setattr(g, "HERE", hooks)
    replies = g.write_verdicts([path], deadline=time.monotonic() + 10, save_qualified=False)
    assert replies[0]["inert"], replies
    if fault == "alias-output":
        assert "ALIAS_FAULT_INJECTED" in replies[0]["reason"]



def test_request_framing_cannot_extend_shared_deadline(guard, monkeypatch):
    g, r = guard
    clock = [0]
    monkeypatch.setattr(g, "_clock", lambda: clock[0])
    original = g.guard_payload
    def framing(*args, **kwargs):
        clock[0] = 100
        return original(*args, **kwargs)
    monkeypatch.setattr(g, "guard_payload", framing)
    launched = []
    def backend(*args, **kwargs):
        launched.append(args)
        return None, b"", b""
    monkeypatch.setattr(g, "_run_bounded", backend)
    replies = g.write_verdicts([str(r["X4_MODS"] / "a")], deadline=50, save_qualified=False)
    assert not launched
    assert replies[0]["inert"]


def test_single_file_ignores_inherited_batch_hint(guard, monkeypatch):
    g, r = guard
    monkeypatch.setenv("_X4_BATCH_NP", str(r["X4_MODS"] / "ordinary.xml"))
    path = str(r["X4_MODS"] / ".codex/sessions/transcript.jsonl")
    single = g.verdict_for("write", None, None, path, save_qualified=False)
    assert single["decision"] == "deny" and not single["inert"], single


@pytest.mark.parametrize("separator", ["\n", "\r"])
def test_unusual_line_paths_use_legacy_backend(guard, monkeypatch, separator):
    g, r = guard
    paths = [str(r["X4_MODS"] / ("line" + separator + "name.xml")), str(r["X4_MODS"] / "a")]
    original = g._run_bounded
    invocations = []
    def observe(*args, **kwargs):
        invocations.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(g, "_run_bounded", observe)
    bulk = g.write_verdicts(paths, deadline=time.monotonic() + 10, save_qualified=False)
    assert len(invocations) == 2 and all("--batch" not in argv for argv in invocations)
    assert bulk == [g.verdict_for("write", None, None, p, save_qualified=False) for p in paths]


def test_posix_symlink_canonicalization_matches_single(guard, monkeypatch, tmp_path):
    import os
    g, r = guard
    target = r["X4_REFERENCE"]
    target.mkdir()
    link = tmp_path / "linked-reference"
    if os.name == "nt":
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            if exc.winerror == 1314:
                pytest.skip("native Windows symlink privilege unavailable; junction parity is tested separately")
            raise
        def posix(p):
            p = str(p).replace("\\", "/")
            return "/" + p[0].lower() + p[2:]
    else:
        link.symlink_to(target, target_is_directory=True)
        posix = str
    # Exercise Bash's POSIX path rule directly on Windows too.
    monkeypatch.setattr(g.os.path, "abspath", lambda p: p)
    for key in ("X4_REFERENCE", "X4_MODS", "X4_GAME", "X4_PROFILE", "X4_EXTENSIONS"):
        monkeypatch.setenv(key, posix(r[key]))
    paths = [posix(link / "a.xml"), posix(r["X4_MODS"] / "a.xml")]
    bulk = g.write_verdicts(paths, deadline=time.monotonic() + 10, save_qualified=False)
    assert bulk == [g.verdict_for("write", None, None, p, save_qualified=False) for p in paths]
    assert [v["decision"] for v in bulk] == ["deny", "allow"]


def test_batch_probes_stable_working_roots_once(guard, monkeypatch, tmp_path):
    import shutil
    g, r = guard
    hooks = tmp_path / "count-hooks"
    shutil.copytree(g.HERE, hooks, ignore=shutil.ignore_patterns("__pycache__"))
    script = hooks / "_x4-env.sh"
    # Counts link probes of the TOOLKIT working roots only: F331's per-path link walk
    # (x4_win_has_link) probes each path's own components, a different and intended cost.
    script.write_text(script.read_text(encoding="utf-8") + """
_X4_TEST_L_COUNT=0
[() {
  if [[ "$1" = "-L" && "$2" == "$X4_TOOLKIT"/* ]]; then _X4_TEST_L_COUNT=$((_X4_TEST_L_COUNT+1)); fi
  builtin [ "$@"
}
trap 'printf "LINK_PROBES=%s\\\\n" "$_X4_TEST_L_COUNT" >&2' EXIT
""", encoding="utf-8")
    monkeypatch.setattr(g, "HERE", hooks)
    original = g._run_bounded
    errors = []
    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        errors.append(result[2].decode("utf-8", "replace"))
        return result
    monkeypatch.setattr(g, "_run_bounded", observe)
    replies = g.write_verdicts([str(r["X4_MODS"] / f"a-{i}") for i in range(128)],
                              deadline=time.monotonic() + 10, save_qualified=False)
    assert all(v["decision"] == "allow" and not v["inert"] for v in replies), replies
    count = int(errors[0].split("LINK_PROBES=")[1].strip())
    assert count <= 26, count


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows junction parity")
def test_windows_junction_matches_single_policy(guard, monkeypatch, tmp_path):
    import subprocess
    g, r = guard
    target = r["X4_REFERENCE"]
    target.mkdir()
    link = tmp_path / "junction"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
    def posix(p):
        p = str(p).replace("\\", "/")
        return "/" + p[0].lower() + p[2:]
    monkeypatch.setattr(g.os.path, "abspath", lambda p: p)
    paths = [posix(link / "a.xml"), posix(r["X4_MODS"] / "ordinary.xml")]
    # This is equivalence evidence only. A Windows junction is not evidence of
    # POSIX native-symlink resolution; that check above may require privileges.
    bulk = g.write_verdicts(paths, deadline=time.monotonic() + 10, save_qualified=False)
    assert bulk == [g.verdict_for("write", None, None, p, save_qualified=False) for p in paths]
    assert not any(v["inert"] for v in bulk)



def _make_link(kind, link, target, request):
    """A junction or directory symlink made by THIS test, in its own tmp dir, removed at
    teardown (the link only: os.rmdir on a link never touches the target)."""
    import os
    import subprocess
    if kind == "junction":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        assert r.returncode == 0, (r.stdout, r.stderr)
    elif kind == "symlink":
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("native Windows symlink privilege unavailable (no Developer Mode)")
            raise
    else:
        link.mkdir(parents=True)
    request.addfinalizer(lambda: os.rmdir(link) if link.exists() or link.is_symlink() else None)


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows-form paths exist only on Windows")
@pytest.mark.parametrize("kind", ["junction", "symlink", "plain-dir"])
# toolkit-dev reaches only reference/: a WHOLE toolkit working subfolder that is itself a link
# is whitelisted by design (the `_X4_BATCH_LINKED` branch), and only the hard blocks above the
# whitelist -- reference/ among them -- outrank it. The same holds for POSIX forms (F331 residual).
@pytest.mark.parametrize("where,root,expected", [
    ("mods", "X4_REFERENCE", "deny"), ("mods", "X4_GAME", "deny"), ("mods", "X4_PROFILE", "ask"),
    ("toolkit-dev", "X4_REFERENCE", "deny")])
def test_windows_form_write_through_a_link_follows_the_real_target(
        guard, monkeypatch, tmp_path, request, kind, where, root, expected):
    """F331: a Windows-form path (what Write/Edit always send on Windows) never reached
    realpath, so `<toolkit>\\dev\\x.xml` with `dev` a junction into reference/ was judged as
    the toolkit file it is spelled as -- ALLOW in single and batch alike. The verdict must
    follow the real target. The plain-dir twin pins that a non-link path is unchanged."""
    g, r = guard
    real = r[root] / "libraries" if root != "X4_PROFILE" else r[root]
    real.mkdir(parents=True)
    if where == "mods":
        link = r["X4_MODS"] / "linked"
        r["X4_MODS"].mkdir(parents=True)
    else:
        toolkit = tmp_path / "tk"
        toolkit.mkdir()
        monkeypatch.setenv("X4_TOOLKIT", str(toolkit))
        link = toolkit / "dev"
    _make_link(kind, link, real, request)
    path = str(link / "new.xml")
    single = g.verdict_for("write", None, None, path, save_qualified=False)
    bulk = g.write_verdicts([path, str(r["X4_REFERENCE"] / "direct.xml")],
                            deadline=time.monotonic() + 30, save_qualified=False)
    assert bulk[0] == single, (bulk[0], single)
    assert bulk[1]["decision"] == "deny"
    want = "allow" if kind == "plain-dir" else expected
    assert single["decision"] == want and not single["inert"], single


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows-form paths exist only on Windows")
@pytest.mark.parametrize("fault", ["realpath", "cygpath"])
@pytest.mark.parametrize("mode", ["single", "batch"])
def test_windows_form_link_resolution_failure_is_inert(guard, monkeypatch, tmp_path, request,
                                                      mode, fault):
    """F331 fail-closed twin: if resolving a Windows-form path through its link fails (either
    step), the verdict is an inert refusal -- never the unresolved spelling's verdict (an allow
    here, since the spelling is inside the mods root)."""
    import shutil
    g, r = guard
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    r["X4_MODS"].mkdir(parents=True)
    _make_link("junction", r["X4_MODS"] / "linked", elsewhere, request)
    hooks = tmp_path / "realpath-fault-hooks"
    shutil.copytree(g.HERE, hooks, ignore=shutil.ignore_patterns("__pycache__"))
    script = hooks / "_x4-env.sh"
    script.write_text(script.read_text(encoding="utf-8") +
                      f"\n{fault}() {{ return 1; }}\n", encoding="utf-8")
    monkeypatch.setattr(g, "HERE", hooks)
    path = str(r["X4_MODS"] / "linked" / "a.xml")
    if mode == "single":
        v = g.verdict_for("write", None, None, path, save_qualified=False)
    else:
        v = g.write_verdicts([path], deadline=time.monotonic() + 10, save_qualified=False)[0]
    assert v["inert"] and v["decision"] == "deny", v


def test_root_normalizer_failure_refuses_batch(guard, monkeypatch, tmp_path):
    import shutil
    g, r = guard
    hooks = tmp_path / "root-fault-hooks"
    shutil.copytree(g.HERE, hooks, ignore=shutil.ignore_patterns("__pycache__"))
    script = hooks / "_x4-env.sh"
    text = script.read_text(encoding="utf-8").replace("x4_norm_stream() {", "original_norm_stream() {")
    text += """
x4_norm_stream() {
  local input
  input="$(cat)"
  [ "$input" = "$X4_REFERENCE" ] && return 9
  printf '%s' "$input" | original_norm_stream
}
"""
    script.write_text(text, encoding="utf-8")
    monkeypatch.setattr(g, "HERE", hooks)
    replies = g.write_verdicts([str(r["X4_MODS"] / "a")], deadline=time.monotonic() + 10,
                              save_qualified=False)
    assert replies[0]["inert"], replies
