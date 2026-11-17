"""x4fresh gives the job wrapper the memory a BaseX build is BOUNDED to, not a number that follows run history.

MEASURED 2026-11-17: a wrapper cap that came from earlier runs (6 GB, from weeks of ~3.3 GB peaks) killed x4eff when an
unbounded JVM reached 6219 MB. Two halves fix it: the build's Java heap is fixed (tools/basex/basex_run.py,
X4_BASEX_HEAP, tested in tools/basex/test_basex_heap.py) and x4fresh hands the wrapper heap + a named allowance through
the `{mem_gb}` token of X4_JOB_WRAPPER.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from x4validate import _freshcli as fc

# rb's own flags, as a user would write them: the estimate and the cap both follow the figure.
RB = ["python", "rb.py", "run", "--name", "{name}", "--cpu", "25", "--ram", "{mem_gb|6}", "--mem-cap", "{mem_gb|6}",
      "--wait-until-fits", "30", "--"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("X4_JOB_WRAPPER", raising=False)
    monkeypatch.delenv("X4_BASEX_HEAP", raising=False)


def _wrap(monkeypatch, argv):
    monkeypatch.setenv("X4_JOB_WRAPPER", json.dumps(list(argv)))


def _expected(name, mem):
    return [a.replace("{name}", f"x4fresh-{name}").replace("{mem_gb|6}", mem) for a in RB]


@pytest.mark.parametrize("name", ["x4raw", "x4eff"])
def test_a_basex_builder_gets_the_heap_derived_estimate_and_cap(monkeypatch, name):
    _wrap(monkeypatch, RB)
    argv = fc._wrapper(name)
    assert argv == _expected(name, "5.5")
    assert argv[argv.index("--ram") + 1] == argv[argv.index("--mem-cap") + 1] == "5.5"


@pytest.mark.parametrize("name", ["effective", "xref"])
def test_TWIN_the_other_artifacts_keep_the_fallback_not_the_basex_figure(monkeypatch, name):
    _wrap(monkeypatch, RB)
    assert fc._wrapper(name) == _expected(name, "6")


def test_the_figure_follows_the_heap_setting(monkeypatch):
    _wrap(monkeypatch, RB)
    monkeypatch.setenv("X4_BASEX_HEAP", "6g")
    assert fc._wrapper("x4eff") == _expected("x4eff", "7.5")
    monkeypatch.setenv("X4_BASEX_HEAP", "6144m")
    assert fc._wrapper("x4raw") == _expected("x4raw", "7.5")
    assert fc._wrapper("xref") == _expected("xref", "6")          # an artifact with no heap is not moved by it


def test_the_figure_is_what_the_build_scripts_will_ask_java_for(monkeypatch):
    """The cap and the heap are two reads of one setting: the figure must be heap + the allowance, from the SAME module
    the scripts ask for `heap-flag`, not a number retyped here."""
    spec = importlib.util.spec_from_file_location("heap_src", fc.BASEX_ROOT / "basex_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for heap in ("4g", "5g", "3000m"):
        monkeypatch.setenv("X4_BASEX_HEAP", heap)
        _wrap(monkeypatch, ["w", "{mem_gb}"])
        figure = float(fc._wrapper("x4eff")[1])
        assert figure >= mod.heap_mb() / 1024 + mod.JVM_NON_HEAP_MB / 1024 - 1e-9
        assert figure > mod.heap_mb() / 1024, "the cap must sit above the heap it bounds"
        assert mod.heap_flag() == f"-Xmx{heap}"


def test_a_bare_token_on_an_artifact_with_no_figure_is_refused(monkeypatch):
    _wrap(monkeypatch, ["w", "--ram", "{mem_gb}"])
    assert fc._wrapper("x4raw") == ["w", "--ram", "5.5"]
    for name in ("effective", "xref"):
        with pytest.raises(ValueError, match="mem_gb"):
            fc._wrapper(name)


@pytest.mark.parametrize("token", ["{mem_gb|abc}", "{mem_gb|.5}", "{mem_gb |6}", "{mem_gb|6", "{MEM_GB}", "{mem_gb|6|7}",
                                   "{mem_gb|-1}", "{ mem_gb}", "{Mem_Gb|6}", "x{mem_gbx}"])
@pytest.mark.parametrize("name", ["x4eff", "effective"])
def test_a_token_that_is_not_one_is_refused_never_passed_through(monkeypatch, token, name):
    _wrap(monkeypatch, ["w", "--ram", token])
    with pytest.raises(ValueError, match="mem_gb"):
        fc._wrapper(name)


@pytest.mark.parametrize("token", ["{mem_gb|0}", "{mem_gb|0.0}", "{mem_gb|00}"])
@pytest.mark.parametrize("name", ["x4raw", "xref"])
def test_a_zero_figure_is_refused_on_every_artifact(monkeypatch, token, name):
    """Used or not: on x4raw the figure is computed, but a zero the user wrote is still a mistake to name."""
    _wrap(monkeypatch, ["w", "--ram", token])
    with pytest.raises(ValueError, match="positive"):
        fc._wrapper(name)


@pytest.mark.parametrize("token,expect", [("{mem_gb|6}", "6"), ("{mem_gb|0.5}", "0.5"), ("{mem_gb|12.25}", "12.25")])
def test_TWIN_a_positive_fallback_is_accepted(monkeypatch, token, expect):
    _wrap(monkeypatch, ["w", "--ram", token])
    assert fc._wrapper("xref") == ["w", "--ram", expect]
    assert fc._wrapper("x4eff") == ["w", "--ram", "5.5"]


def test_a_wrapper_without_the_token_is_untouched_and_never_reads_the_basex_folder(monkeypatch, tmp_path):
    """The twin of the above: no `{mem_gb}`, no dependence on basex_run (a broken folder or a bad heap changes nothing),
    so a user whose wrapper predates the token sees the old argv exactly."""
    _wrap(monkeypatch, ["rb", "run", "--name", "{name}", "--ram", "6", "--"])
    monkeypatch.setattr(fc, "BASEX_ROOT", tmp_path / "missing")
    monkeypatch.setenv("X4_BASEX_HEAP", "garbage")
    assert fc._wrapper("x4eff") == ["rb", "run", "--name", "x4fresh-x4eff", "--ram", "6", "--"]


def test_a_bad_heap_stops_the_wrapped_build_with_the_reason(monkeypatch):
    _wrap(monkeypatch, RB)
    monkeypatch.setenv("X4_BASEX_HEAP", "lots")
    with pytest.raises(ValueError, match="X4_BASEX_HEAP"):
        fc._wrapper("x4eff")
    assert fc._wrapper("effective")[0] == "python"                  # the non-BaseX artifacts do not read the heap


def test_the_wrapper_process_really_receives_the_figures(monkeypatch, tmp_path):
    """A REAL wrapper process around a REAL command through _run: what reaches it is the argv, not what _wrapper()
    returns in isolation."""
    seen = tmp_path / "argv.json"
    wrapper = tmp_path / "rb.py"
    wrapper.write_text("import json, subprocess, sys\n"
                       f"open({str(seen)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
                       "cmd = sys.argv[sys.argv.index('--') + 1:]\n"
                       "sys.exit(subprocess.run(cmd).returncode)\n", encoding="utf-8")
    _wrap(monkeypatch, [sys.executable, str(wrapper), "--name", "{name}", "--ram", "{mem_gb|6}",
                        "--mem-cap", "{mem_gb|6}", "--"])
    monkeypatch.setattr(fc, "builder", lambda _n: [sys.executable, "-c", "pass"])
    rc, _warnings, _wrapper_lines = fc._run("x4eff", tmp_path / "log" / "x4eff.log")
    assert rc == 0
    got = json.loads(seen.read_text(encoding="utf-8"))
    assert got[:got.index("--") + 1] == ["--name", "x4fresh-x4eff", "--ram", "5.5", "--mem-cap", "5.5", "--"]
    assert got[got.index("--") + 1:] == [sys.executable, "-c", "pass"], "the builder follows the wrapper unchanged"


def test_the_help_documents_both_settings():
    doc = fc.__doc__
    assert "{mem_gb}" in doc and "{mem_gb|N}" in doc and "X4_BASEX_HEAP" in doc
    assert "1.5 GiB" in doc and "5.5 by default" in doc
    spec = importlib.util.spec_from_file_location("heap_doc_src", fc.BASEX_ROOT / "basex_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.JVM_NON_HEAP_MB == 1536 and mod.job_memory_gb() == "5.5"
