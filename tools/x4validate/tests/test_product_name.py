"""v4.0 renames the product to "X4 AI Assistant Toolkit" (Plan 3 Wave 2, lane K).

Every CURRENT surface -- README, installers, setup, CI, the instruction sources, the release zip --
must carry the new name, and no current surface may still carry an old one. The old names stay
where they are a HISTORICAL fact: release notes, dated plans and measurements, the blind-spots
register, comments that record what a past release shipped, and fixtures that stand for a folder a
user already has. Those are allow-listed below, by CLASS where the class is derivable from the path
and by (path, exact line text) otherwise -- never by line number, which rots on the next edit.

A stale allow-list entry (one that no longer matches any real line) FAILS, so the allow-list
cannot quietly grow into a list of things nobody checks.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
NEW = "X4 AI Assistant Toolkit"

#: Every spelling of the old product name found by the 2026-10-03 census (29 hits, 19 files).
OLD = re.compile(
    r"claude[ ._-]?code[ ._-]?modding|x4-claude-toolkit|X4\.Foundations\.Claude|claude[ -]toolkit"
    r"|Claude Code Toolkit|X4 Claude|modding toolkit", re.I)

SELF = "tools/x4validate/tests/test_product_name.py"

#: Whole-file KEEP classes: the old name in these is a historical record.
_DATED = re.compile(r"\d{4}-\d{2}-\d{2}")


def keep_by_class(path: str) -> bool:
    if path == "CHANGELOG.md":                       # release history
        return True
    if path.startswith("docs/superpowers/"):        # dated specs, plans, measurements
        return True
    if path == "tools/x4validate/docs/BLIND-SPOTS.md":   # the register is history
        return True
    if path.startswith("docs/") and _DATED.search(Path(path).name):   # dated audits
        return True
    return False


#: Line-level KEEP: (path or basename rule, exact text the line must contain, why).
KEEP_LINES = [
    ("tools/modding_harness/opencode_plugin.mjs", 'const label = options.label || "Modding toolkit"',
     "pinned shared runtime's agent-neutral fallback; the X4 wrapper supplies its product label"),
    ("README.md", "skyrimvr-claude-toolkit", "a different product (the sibling project)"),
    ("README.md", 'Until v4.0 this was the "X4 Foundations Claude Code Modding Toolkit"',
     "the rename note: the old name, said as history"),
    ("scripts/build-release.sh", "v3.0.0 shipped `X4.Foundations.Claude.Code.Toolkit.v3.0.0.zip`",
     "names the asset v3.0.0 really shipped"),
    ("tools/x4validate/tests/test_build_release.py",
     "v3.0.0 shipped `X4.Foundations.Claude.Code.Toolkit.v3.0.0.zip`",
     "names the asset v3.0.0 really shipped"),
    ("install.ps1", '"x4-claude-toolkit [v3.0.0]"', "the shape a v3.0.0 download really had"),
    ("tools/x4validate/tests/test_adapting_doc.py", '"Claude Code Modding Toolkit" not in PROMPT',
     "asserts the OLD name is ABSENT"),
    ("*/test_hook_facts.py", 'toolkit="C:/Users/tester/Projects/x4-claude-toolkit"',
     "a fixture standing for a checkout folder a user already has"),
]


def _rule_matches(rule: str, path: str) -> bool:
    if rule.startswith("*/"):
        return path == rule[2:] or path.endswith("/" + rule[2:])
    return path == rule


def classify(path: str, line: str) -> str | None:
    """'KEEP:<why>' for an allowed historical hit, 'RENAME' for a hit that must go, None if the line
    carries no old name at all."""
    if not OLD.search(line):
        return None
    if path == SELF:
        return "KEEP:this test"
    if keep_by_class(path):
        return "KEEP:history"
    for rule, text, why in KEEP_LINES:
        if _rule_matches(rule, path) and text in line:
            return "KEEP:" + why
    return "RENAME"


def _hits() -> list[tuple[str, int, str]]:
    r = subprocess.run(["git", "-C", str(REPO), "grep", "-z", "-n", "-I", "-i", "-E", OLD.pattern],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode not in (0, 1):
        pytest.skip("not a git checkout, or git grep failed (rc %d) -- not checked" % r.returncode)
    out = []
    for rec in r.stdout.splitlines():
        parts = rec.split("\0")
        if len(parts) >= 3:
            out.append((parts[0], int(parts[1]), parts[2]))
    if not out:
        # The census found 29; the KEEP set alone is never empty, so zero hits means the scan
        # looked at nothing (a broken pattern, a shallow export) -- a non-answer, never a pass.
        pytest.fail("git grep returned no hit at all, not even the KEEP set: the scan is broken")
    return out


def test_no_current_surface_carries_an_old_product_name():
    bad = ["%s:%d: %s" % (p, n, t.strip()[:120]) for p, n, t in _hits()
           if classify(p, t) == "RENAME"]
    assert not bad, "old product name on a current surface:\n" + "\n".join(bad)


def test_every_line_level_keep_entry_still_matches_a_real_line():
    hits = _hits()
    stale = [(rule, text) for rule, text, _ in KEEP_LINES
             if not any(_rule_matches(rule, p) and text in t for p, _n, t in hits)]
    assert not stale, "allow-list entries that match nothing (remove them): %r" % stale


@pytest.mark.parametrize("path,line,want", [
    ("README.md", "# X4 Foundations Claude Code Modding Toolkit", "RENAME"),
    ("setup.sh", 'echo "=== x4 claude code modding toolkit setup ==="', "RENAME"),
    ("scripts/build-release.sh", 'NAME_PREFIX="X4.Foundations.Claude.Code.Toolkit"', "RENAME"),
    ("README.md", "(https://github.com/WingedGuardian/x4-claude-toolkit/releases)", "RENAME"),
    ("CHANGELOG.md", "# X4 Claude Toolkit", "KEEP"),                        # class: changelog
    ("docs/superpowers/plans/x.md", "X4 Claude Toolkit", "KEEP"),            # class: superpowers
    ("tools/x4validate/docs/BLIND-SPOTS.md", "x4-claude-toolkit/tools", "KEEP"),
    ("docs/AUDIT-framework-2026-10-01.md", "x4-claude-toolkit/actions", "KEEP"),   # dated doc
    ("docs/ADAPTING-COLD-TEST.md", "X4 Claude Toolkit", "RENAME"),           # undated doc: twin
    ("README.md", "[skyrimvr-claude-toolkit](...)", "KEEP"),                 # line rule
    ("install.sh", "[skyrimvr-claude-toolkit](...)", "RENAME"),              # same text, other path
    (".codex/hooks/test_hook_facts.py",
     'SPLIT_ROOTS = dict(ROOTS, toolkit="C:/Users/tester/Projects/x4-claude-toolkit")', "KEEP"),
    ("README.md", "nothing old here", None),
])
def test_the_classifier_can_go_red_and_each_keep_clause_is_needed(path, line, want):
    got = classify(path, line)
    assert (got.split(":")[0] if got else None) == want, (path, line, got)


@pytest.mark.parametrize("path,needle", [
    ("README.md", "# " + NEW),
    ("setup.sh", "=== %s setup ===" % NEW),
    ("install.sh", "%s installer" % NEW),
    ("install.ps1", "%s installer" % NEW),
    (".github/workflows/ci.yml", NEW),
    ("agent/instructions/claude.md", "(%s)" % NEW),
    ("CLAUDE.md", "(%s)" % NEW),
    ("SETUP_PROMPT.txt", NEW),
])
def test_each_current_surface_carries_the_new_name(path, needle):
    if path.startswith((".github/", "agent/")):          # R2-B1: never installed
        from _layout import require_repo
        require_repo(path)
    assert needle in (REPO / path).read_text(encoding="utf-8"), (path, needle)


def test_the_repo_url_is_the_new_slug():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert "github.com/WingedGuardian/x4-ai-toolkit" in text
