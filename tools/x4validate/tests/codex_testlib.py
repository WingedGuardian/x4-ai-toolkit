"""Shared helpers for the Codex adapter tests (lane B): the sandbox roots, the RENDERED
.codex/hooks tree, native Codex payloads built from captured fixtures, and the one parser of
an adapter's output that enforces the only JSON shapes Codex honours (C4)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
REPO = PKG.parents[1]
HOOKS = REPO / ".codex" / "hooks"
ADAPTER = HOOKS / "codex_adapter.py"
FIX = PKG / "tests" / "fixtures" / "codex" / "0.160.0"
HAS_PWSH = bool(shutil.which("pwsh") or shutil.which("powershell"))


def schema_keys(event: str) -> tuple[set, set]:
    """(top-level keys, hookSpecificOutput keys) Codex 0.160.0 accepts in an `event` hook's output,
    read from the schema extracted from the binary (fixtures/codex/0.160.0/schemas/). R7-10: these
    were typed by hand, twice, and could drift from what Codex actually enforces."""
    d = json.loads((FIX / "schemas" / f"{event}.command.output.json").read_text(encoding="utf-8"))
    assert d.get("additionalProperties") is False, f"{event}: the schema no longer forbids extra keys"
    hso = d["properties"]["hookSpecificOutput"]
    ref = (hso.get("allOf") or [hso])[0].get("$ref", "")
    inner = d["definitions"][ref.rsplit("/", 1)[-1]] if ref else hso
    assert inner.get("additionalProperties") is False, f"{event}: hookSpecificOutput allows extra keys"
    return set(d["properties"]), set(inner["properties"])


ALLOWED_TOP, ALLOWED_HSO = schema_keys("pre-tool-use")
SANDBOX_KEYS = ("X4_TOOLKIT", "X4_GAME", "X4_REFERENCE", "X4_PROFILE", "X4_MODS", "X4_EXTENSIONS",
                "X4_SAVES", "X4_DOCUMENTS", "X4_BACKUPS", "X4_CONFIG")


def make_sandbox(tmp_path: Path):
    tk, game = tmp_path / "toolkit", tmp_path / "X4 Foundations"
    copy_shared_runtime(tk)
    for d in (tk / "dev" / "mymod", tk / "reference" / "libraries", game / "extensions",
              tmp_path / "profile" / "save", tmp_path / "mods", tmp_path / "docs", tmp_path / "backups"):
        d.mkdir(parents=True)
    (tk / "reference" / "libraries" / "wares.xml").write_text("ref\n", encoding="utf-8")
    env = dict(os.environ, X4_TOOLKIT=str(tk), X4_GAME=str(game), X4_REFERENCE=str(tk / "reference"),
               X4_PROFILE=str(tmp_path / "profile"), X4_MODS=str(tmp_path / "mods"),
               X4_EXTENSIONS=str(game / "extensions"), X4_SAVES=str(tmp_path / "profile" / "save"),
               X4_DOCUMENTS=str(tmp_path / "docs"), X4_BACKUPS=str(tmp_path / "backups"),
               X4_CONFIG="/nonexistent", X4_CODEX_TOOL_LOG=str(tmp_path / "unknown-tools.log"))
    env.pop("X4_BASH", None)
    env.pop("X4_CODEX_SHELL", None)
    return tmp_path, tk, env


def copy_shared_runtime(root: Path):
    """Populate an owned fixture with the same pinned offline runtime as a real installation."""
    shutil.copytree(REPO / "tools/modding_harness", root / "tools/modding_harness",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "tools/modding-harness.lock.json", root / "tools/modding-harness.lock.json")


FAKE_FINDING = "FAKE-VALIDATOR-FINDING"


def fake_validator(tmp_path: Path, env: dict, tk: Path, mod: str = "mymod") -> dict:
    """Make the post-edit validator path REAL up to the validator itself (R7-2): the mod gets a
    content.xml (without one x4validate-on-edit.sh exits silently, so a post test passed whatever
    the adapter did), and UV/X4V point at a stand-in `uv` that prints one error finding in the
    validator's JSON shape. A post test can then require the finding in the adapter's output."""
    (tk / "dev" / mod / "content.xml").write_text('<content id="m" version="1"/>\n', encoding="utf-8")
    x4v = tmp_path / "fake-x4validate"
    x4v.mkdir(exist_ok=True)
    uv = tmp_path / "fake-uv"
    out = json.dumps({"error_count": 1, "degraded": False, "skipped": [],
                      "findings": [{"severity": "error", "message": FAKE_FINDING, "vpath": "a.xml", "line": 1}]})
    uv.write_bytes(("#!/bin/sh\ncat <<'X4FAKE'\n" + out + "\nX4FAKE\n").encode("utf-8"))
    uv.chmod(0o755)
    return dict(env, UV=uv.as_posix(), X4V=x4v.as_posix())


def native(name: str, cwd, **tool_input) -> dict:
    """A captured fixture with <CWD> filled in and tool_input overridden -- never a hand-built envelope."""
    text = (FIX / f"{name}.json").read_text(encoding="utf-8")
    d = json.loads(text.replace("<CWD>", json.dumps(str(cwd))[1:-1]))
    d["cwd"] = str(cwd)
    # Warning receipts are per session/tool, so separate fixture roots must not
    # inherit the captured session's already-announced unknown-tool receipt.
    d["session_id"] = "fixture:" + str(cwd)
    if "tool_input" in d:
        d["tool_input"].update(tool_input)
    return d


def _engine():
    import importlib.util
    spec = importlib.util.spec_from_file_location("x4conformance", REPO / "scripts" / "x4conformance.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


XC = _engine()
#: The Codex output contract lives in ONE place: the conformance profile's output rules (the
#: C4 shapes; allowed_keys = the keys Codex knows -- one extra key fails the hook OPEN).
CODEX_PROFILE = XC.load_profile("codex", REPO)
assert set(CODEX_PROFILE["output"]["allowed_keys"][""]) == ALLOWED_TOP
assert set(CODEX_PROFILE["output"]["allowed_keys"]["/hookSpecificOutput"]) == ALLOWED_HSO


def parse_output(stdout: bytes):
    """(decision, text) from what a Codex hook printed; asserts the C4 shape contract: an ask, an
    allow-with-reason, an unknown key or unparseable output is "unreadable" and fails here."""
    d, text = XC.decode(CODEX_PROFILE["output"], stdout, 0)
    assert d != "unreadable", text
    return d, text


def run_adapter(env, payload, event="pre_tool_use", shell="powershell", adapter=ADAPTER, raw: bytes | None = None):
    data = raw if raw is not None else json.dumps(payload).encode("utf-8")
    r = subprocess.run([sys.executable, str(adapter), event], input=data, capture_output=True,
                       env=dict(env, X4_CODEX_SHELL=shell), timeout=180)
    lines = r.stdout.decode("utf-8").splitlines()
    assert r.returncode == 0 and len(lines) == 1 and lines[0].startswith("X4OK"), (r.returncode, r.stdout, r.stderr)
    return parse_output(lines[0][len("X4OK"):].encode("utf-8"))
