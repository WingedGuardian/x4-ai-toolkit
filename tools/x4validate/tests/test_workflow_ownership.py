"""Every external action is immutable; every job has a measured/bounded window."""
import re
from pathlib import Path

LF = chr(10)


def job_timeouts(block):
    """Every `timeout-minutes:` value of one job block, read as the shared ci-policy contract's field() reads it
    (modding-agent-harness ci/contracts.py): everything after the colon, trailing space stripped, comment INCLUDED."""
    return re.findall(r"^    timeout-minutes:\s*(\S.*?)\s*$", block, re.M)


def bounded(block):
    """The contract's rule for a runner job: exactly one timeout-minutes, a plain decimal, 1..360."""
    found = job_timeouts(block)
    return len(found) == 1 and found[0].isdecimal() and 0 < int(found[0]) <= 360


def test_RED_a_trailing_comment_after_timeout_minutes_is_not_a_bound():
    """The shared contract's field() captures everything after the colon, so `180 # cold archive` is not a decimal
    there: Fast checks went red in 6 s (4.2.0 round, a5d416d6) while this test passed, because its regex allowed a
    trailing comment."""
    assert not bounded("    timeout-minutes: 180 # cold archive" + LF)


def test_TWIN_a_plain_timeout_is_a_bound():
    assert bounded("    timeout-minutes: 180" + LF)
    assert bounded("    timeout-minutes:   180   " + LF)


def test_TWIN_two_timeouts_or_an_out_of_range_one_are_not_bounds():
    assert not bounded("    timeout-minutes: 10" + LF + "    timeout-minutes: 20" + LF)
    assert not bounded("    timeout-minutes: 361" + LF)
    assert not bounded("    timeout-minutes: 0" + LF)
    assert not bounded("      timeout-minutes: 10" + LF), "a step's timeout is not the job's"


def test_workflow_actions_are_pinned_and_jobs_bounded():
    root = Path(__file__).resolve().parents[3]
    from _layout import require_repo
    require_repo(".github/workflows", why="workflow source is not copied into installed runtimes", root=root)
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
            if re.search(r"^    uses:", block, re.M):
                assert name == "qualification" and "uses: ./.github/workflows/full-qualification.yml" in block
                assert not job_timeouts(block) and (root / ".github/workflows/full-qualification.yml").is_file()
            else:
                assert bounded(block), (path, name, job_timeouts(block))
        uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", text, re.M)
        assert uses, path
        for action in uses:
            if not action.startswith("./"):
                assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), (path, action)
    assert jobs >= 3
