"""The Codex fixtures are REAL captured payloads (spec section 7.2), sanitised, and shaped as the
0.160.0 schema says. A hand-written fixture would encode our assumptions, not Codex's.

Provenance: tests/fixtures/codex/0.160.0/README.md. The raw captures stay out of the repo (they
carry personal paths); scripts/capture-codex-fixtures.py is the capture hook and the sanitiser.
"""
import json
import re
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "codex" / "0.160.0"
#: A drive path, a POSIX home, or an 8-digit id (the X4 profile folder is one). Run over the
#: DECODED string values, not the JSON text: in the text, "Output:\nSuccess" reads as `t:\`.
PERSONAL = re.compile(r"\b[A-Za-z]:[\\/]|/home/|/Users/|\b\d{8}\b")


NOT_PAYLOADS = {"trust_vectors.json",       # Codex-produced hash vectors (test_codex_trust.py)
                "patch_grammar_oracle.json"}  # Codex's apply_patch outcomes (test_patch_paths.py)


def payloads():
    return sorted(p for p in FIX.glob("*.json") if p.name not in NOT_PAYLOADS)


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def test_fixtures_exist_for_every_required_shape():
    names = {p.stem for p in payloads()}
    for need in ("bash_powershell", "apply_patch_add", "apply_patch_update", "apply_patch_delete",
                 "apply_patch_move", "apply_patch_multi", "session_start", "post_tool_use_apply_patch",
                 "bash_shell_heredoc_patch", "bash_subagent", "spawn_agent"):
        assert need in names, f"missing captured fixture {need}"


def test_fixtures_are_sanitised():
    for p in payloads():
        d = json.loads(p.read_text(encoding="utf-8"))
        hits = [s for s in _strings(d) if PERSONAL.search(s)]
        assert not hits, f"{p.name} carries a personal path: {hits[:3]}"
        assert d["cwd"] == "<CWD>", f"{p.name}: cwd placeholder missing"
        assert d["transcript_path"] == "<TRANSCRIPT>", p.name


def test_the_patch_oracle_is_sanitised_too():
    """Its stderr lines named the scratch folder until the capture replaced it with <WORK>."""
    d = json.loads((FIX / "patch_grammar_oracle.json").read_text(encoding="utf-8"))
    hits = [s for s in _strings(d) if PERSONAL.search(s)]
    assert not hits and len(d["rows"]) >= 60, f"patch_grammar_oracle.json carries a personal path: {hits[:3]}"


def test_TWIN_the_personal_pattern_can_fire():
    """The sanitiser check is only evidence if its pattern fires on what it exists to catch."""
    home = "/" + "home" + "/u/x"         # split so the identifier scan does not flag the probe itself
    for bad in ("C:\\Users\\someone\\x", "C:/Program Files (x86)/x", home, "profile 87654321"):
        assert PERSONAL.search(bad), bad
    assert not PERSONAL.search("Exit code: 0\nOutput:\nSuccess")


def test_fixtures_carry_exactly_the_schema_required_keys():
    schema = json.loads((FIX / "schemas" / "pre-tool-use.command.input.json").read_text(encoding="utf-8"))
    allowed, required = set(schema["properties"]), set(schema["required"])
    n = 0
    for p in payloads():
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("hook_event_name") != "PreToolUse":
            continue
        assert required <= set(d) <= allowed, (p.name, set(d) ^ required)
        n += 1
    assert n >= 10, f"only {n} PreToolUse fixtures were checked"


def test_the_output_key_contract_is_codexs_own_schema():
    """R7-10 (v4.0.0 review): the allowed output keys were typed by hand in codex_testlib AND the
    conformance profile. codex_testlib now READS them from the captured 0.160.0 schemas and holds
    the profile to them (at import); this pins the other events the adapter renders, and the
    wrapper's own allow-list, to the same source."""
    from codex_testlib import ALLOWED_HSO, CODEX_PROFILE, REPO, schema_keys
    assert set(CODEX_PROFILE["output"]["allowed_keys"]["/hookSpecificOutput"]) == ALLOWED_HSO
    for ev in ("post-tool-use", "session-start"):
        top, hso = schema_keys(ev)
        assert "hookSpecificOutput" in top and {"hookEventName", "additionalContext"} <= hso, (ev, hso)
    from _layout import require_repo
    require_repo("agent/guards/adapters/codex-entry.ps1", why="the wrapper SOURCE")
    ps = (REPO / "agent" / "guards" / "adapters" / "codex-entry.ps1").read_text(encoding="utf-8")
    # The strict raw JSON grammar keeps quoted key spellings. Both the base
    # event keys and the PreToolUse-only additions must match the capture.
    allowed = set(re.findall(r"'\"(\w+)\"'", " ".join(re.findall(r"\$allowed (?:=|\+=) @\(([^)]*)\)", ps))))
    assert allowed and allowed <= ALLOWED_HSO, allowed - ALLOWED_HSO
