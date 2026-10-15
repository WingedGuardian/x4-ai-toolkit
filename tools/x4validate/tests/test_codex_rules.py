"""Codex .rules: every protect-bash verdict site classified (forbidden / prompt / hook_only), the
generated x4.rules proven by Codex's own policy checker, and no rule stricter than the guard.

The INVENTORY is derived from the guard source, never retyped: a new verdict site in
protect-bash.sh with no row in agent/rules/codex-rules.yaml turns this red.
"""
import json
import re
import shutil
import subprocess
import sys

import pytest
from ruamel.yaml import YAML

from _layout import require_repo
from codex_testlib import REPO, make_sandbox

require_repo("agent/rules/codex-rules.yaml", module_level=True,
             why="the rule rows the rendered .codex/rules are checked against")

ROWS = YAML(typ="safe").load((REPO / "agent" / "rules" / "codex-rules.yaml").read_text(encoding="utf-8"))
RULES = REPO / ".codex" / "rules" / "x4.rules"
GUARD = REPO / ".codex" / "hooks" / "x4guard.py"
CODEX = shutil.which("codex")
#: A verdict CALL: `deny "..."`, `on <fact> && ask "..."`, `  && advise "..."` (never the
#: `deny() {` definitions, which have no space-quote after the name).
SITE = re.compile(r'^\s*(?:on\s+(\w+)\s+)?(?:&&\s*)?(?:\[[^\]\r\n]*\]\s*&&\s*)*(deny|ask|advise)\s+"')
RANK = {"allow": 0, "advise": 1, "ask": 2, "deny": 3}
RULE_ROWS = [r for r in ROWS if r["bucket"] != "hook_only"]


def guard_rule_sites():
    src = (REPO / "agent" / "guards" / "claude-hooks" / "protect-bash.sh").read_text(encoding="utf-8")
    return [(i + 1, m.group(2), line) for i, line in enumerate(src.splitlines()) if (m := SITE.match(line))]


def test_TWIN_the_site_pattern_finds_and_skips():
    assert SITE.match('on git_add_all && deny "X"') and SITE.match('  ask "X"') and SITE.match('  && advise "X"')
    assert SITE.match('on rm_saves && [ "$_harness_save_qualified" != 1 ] && ask "X"').group(2) == 'ask'
    assert SITE.match('on fact && [ -e x ] && [ "$x" != 1 ] && deny "X"').group(1) == 'fact'
    assert not SITE.match('deny() { VERDICT=deny; emit deny "$1"; exit 0; }')
    assert not SITE.match('# deny "commented"')


def test_buckets_sum_to_the_rule_inventory():
    sites = guard_rule_sites()
    assert len(sites) >= 20, f"only {len(sites)} verdict sites found -- the inventory pattern is suspect"
    problems = []
    for lineno, verdict, line in sites:
        hits = [r for r in ROWS if r["source"] == "protect-bash.sh" and r["anchor"] in line]
        if len(hits) != 1:
            problems.append(f"protect-bash.sh:{lineno} matches {len(hits)} rows: {line.strip()[:90]}")
        elif hits[0]["bucket"] != "hook_only" and hits[0].get("verdict") != verdict:
            problems.append(f"{hits[0]['id']}: classified as {hits[0].get('verdict')} but the guard says {verdict}")
    matched = {r["id"] for r in ROWS for _, _, line in sites if r["anchor"] in line}
    stale = [r["id"] for r in ROWS if r["id"] not in matched and not r.get("optional")]
    assert not problems and not stale, {"unclassified/ambiguous": problems, "stale rows": stale}
    counts = {b: sum(1 for r in ROWS if r["bucket"] == b and r["id"] in matched)
              for b in ("forbidden", "prompt", "hook_only")}
    assert sum(counts.values()) == len(sites), (counts, len(sites))
    print("codex rule buckets:", counts, "of", len(sites), "sites")


def test_every_rule_row_is_complete():
    for r in RULE_ROWS:
        assert r["pattern"] and r["match"] and r["not_match"] and r["justification"], r["id"]


def test_generated_rules_carry_exactly_the_rule_rows():
    text = RULES.read_text(encoding="utf-8")
    assert text.count("prefix_rule(") == len(RULE_ROWS)
    for r in RULE_ROWS:
        assert f"(X4 toolkit rule {r['id']})" in text


@pytest.mark.skipif(not CODEX, reason="codex CLI absent -- rule parsing NOT checked here (CI installs it)")
def test_generated_rules_parse_and_examples_hold():
    r = subprocess.run([CODEX, "execpolicy", "check", "--rules", str(RULES), "echo", "x"], capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr     # a wrong match/not_match example is a PARSE error (MEASURED)


@pytest.mark.skipif(not CODEX, reason="codex CLI absent -- rule matching NOT checked here (CI installs it)")
@pytest.mark.parametrize("row", RULE_ROWS, ids=lambda r: r["id"])
def test_each_rule_matches_and_misses(row):
    def check(ex):
        r = subprocess.run([CODEX, "execpolicy", "check", "--rules", str(RULES), *ex], capture_output=True, timeout=60)
        return json.loads(r.stdout)
    for ex in row["match"]:
        assert check(ex).get("decision") == row["bucket"], ex
    for ex in row["not_match"]:
        assert not check(ex).get("matchedRules"), ex


@pytest.mark.parametrize("row", RULE_ROWS, ids=lambda r: r["id"])
def test_rule_is_never_stricter_than_the_guard(row, tmp_path):
    """forbidden needs the guard to DENY every match example; prompt at least ASK; both shells."""
    _, _, env = make_sandbox(tmp_path)
    floor = {"forbidden": 3, "prompt": 2}[row["bucket"]]
    for ex in row["match"]:
        for shell in ("bash", "powershell"):
            r = subprocess.run([sys.executable, str(GUARD), "check", "--kind", "shell", "--shell", shell,
                                "--command", " ".join(ex)], capture_output=True, env=env, timeout=120)
            v = json.loads(r.stdout)
            assert not v["inert"] and RANK[v["decision"]] >= floor, (ex, shell, v["decision"], v["reason"])
