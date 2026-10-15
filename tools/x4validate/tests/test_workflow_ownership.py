"""Every external action is immutable; every job has a measured/bounded window."""
import re
from pathlib import Path

def test_workflow_actions_are_pinned_and_jobs_bounded():
    root = Path(__file__).resolve().parents[3]
    workflows = sorted((root / ".github/workflows").glob("*.yml"))
    assert workflows, "no workflow source set was examined"
    jobs = 0
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        assert "\njobs:\n" in text, (path, "unsupported job layout")
        body = text.split("\njobs:\n", 1)[1]
        # Deliberately scope this stdlib check to the repository's plain-key job
        # layout. An unfamiliar two-space key fails instead of being omitted.
        headers = list(re.finditer(r"^  (\S.*)$", body, re.M))
        assert headers, path
        for index, header in enumerate(headers):
            assert re.fullmatch(r"[A-Za-z0-9_-]+:\s*(?:#.*)?", header.group(1)), (path, header.group(1))
            name = header.group(1).split(":")[0]
            block = body[header.end():headers[index + 1].start() if index + 1 < len(headers) else len(body)]
            jobs += 1
            timeouts = re.findall(r"^    timeout-minutes:\s*(\d+)\s*(?:#.*)?$", block, re.M)
            assert len(timeouts) == 1 and 0 < int(timeouts[0]) <= 360, (path, name)
        uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", text, re.M)
        assert uses, path
        for action in uses:
            if not action.startswith("./"):
                assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), (path, action)
    assert jobs >= 3
