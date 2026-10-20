"""v4.0.0 release review R6-12: maintainer working papers stay in the repo but out of the release
bundle, the evidence the docs cite stays IN it, and nothing shipped links to a dropped file.

build-release.sh makes the bundle with `git archive`, so `export-ignore` is the mechanism; these
tests ask git (`check-attr`) rather than re-implementing its pattern matching."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DROPPED = ("docs/superpowers/plans/", "docs/handoffs/", "docs/SUBAGENT-BRIEF.md",
           "docs/REVIEW-SCOPE.md", "docs/AUDIT-framework-2026-10-01.md", "docs/audits/",
           "docs/superpowers/specs/", "tools/x4validate/docs/superpowers/specs/")
KEPT = ("docs/superpowers/measurements/",)


def _tracked(prefix: str = "") -> list[str]:
    r = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "--", prefix] if prefix else
                       ["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True)
    if r.returncode != 0:
        pytest.skip("not a git checkout -- export-ignore NOT checked here")
    return [p for p in r.stdout.decode("utf-8").split("\0") if p]


def export_ignored(paths: list[str], source: str | None = None) -> dict[str, bool]:
    """{path: True if `git archive` would drop it}. `source` reads the attributes of a commit
    instead of the working tree (used to prove the test fails on the pre-fix tree)."""
    # A `dir/ export-ignore` pattern matches the DIRECTORY, which git archive prunes before
    # descending: a per-file query answers "unspecified" for every file under it. So each
    # path's ancestors are asked too, in trailing-slash form -- as build-release.sh does.
    def ancestors(path):
        parts = path.split("/")[:-1]
        return [path] + ["/".join(parts[:k]) + "/" for k in range(1, len(parts) + 1)]
    queries = sorted({q for p in paths for q in ancestors(p)})
    cmd = ["git", "-C", str(REPO), "check-attr"] + (["--source", source] if source else []) + [
        "-z", "--stdin", "export-ignore"]
    a = subprocess.run(cmd, input="\0".join(queries).encode("utf-8") + b"\0", capture_output=True)
    assert a.returncode == 0, a.stderr
    f = a.stdout.decode("utf-8").split("\0")
    rows = list(zip(f[0::3], f[1::3], f[2::3]))
    assert len(rows) == len(queries), (len(rows), len(queries))   # one answer per query, or refuse
    hit = {q for q, _, v in rows if v not in ("unspecified", "unset")}
    return {p: any(q in hit for q in ancestors(p)) for p in paths}


@pytest.mark.parametrize("prefix", DROPPED)
def test_maintainer_working_papers_are_dropped_from_the_bundle(prefix):
    paths = _tracked(prefix)
    assert paths, f"nothing tracked under {prefix} -- the check would pass on an empty population"
    shipped = [p for p, ignored in export_ignored(paths).items() if not ignored]
    assert shipped == [], shipped


@pytest.mark.parametrize("prefix", KEPT)
def test_TWIN_the_cited_evidence_still_ships(prefix):
    paths = _tracked(prefix)
    assert paths, f"nothing tracked under {prefix}"
    dropped = [p for p, ignored in export_ignored(paths).items() if ignored]
    assert dropped == [], dropped


_LINK = re.compile(r"\]\(([^)#\s]+)")


def test_no_shipped_markdown_links_to_a_dropped_file():
    md = [p for p in _tracked() if p.endswith(".md")]
    ignored = export_ignored(md)
    dangling = []
    for p in md:
        if ignored[p]:
            continue
        text = (REPO / p).read_text(encoding="utf-8", errors="replace")
        for target in _LINK.findall(text):
            if "://" in target:
                continue
            rel = (Path(p).parent / target).as_posix()
            norm = Path(rel).as_posix()
            parts: list[str] = []
            for seg in norm.split("/"):
                if seg == "..":
                    if parts:
                        parts.pop()
                elif seg not in ("", "."):
                    parts.append(seg)
            resolved = "/".join(parts)
            if any(resolved.startswith(d) for d in DROPPED):
                dangling.append(f"{p} -> {target}")
    assert dangling == [], dangling


def test_archive_matches_the_document_policy_and_retains_repair_docs():
    import io, tarfile
    tracked = _tracked()
    result = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar",
                             "--worktree-attributes", "HEAD"], capture_output=True)
    assert result.returncode == 0, result.stderr
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        shipped = {p.name for p in archive if p.isfile()}
    for prefix in DROPPED:
        assert not any(p.startswith(prefix) for p in shipped), prefix
    measured = [p for p in tracked if p.startswith(KEPT)]
    assert measured and all(p in shipped for p in measured)
    assert {"README.md", "ADAPTING.md", "RETENTION.md"} <= shipped
