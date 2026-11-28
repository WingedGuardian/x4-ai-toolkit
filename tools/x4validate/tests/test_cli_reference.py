"""`scripts/gen-cli-reference.py` -- the generated `x4-cli-reference` skill must be FRESH.

The skill is the exact surface of every toolkit CLI, generated from argparse so it
cannot drift from the code. That guarantee is only as good as this test: without it a
new flag lands, nobody regenerates, and the "generated" reference is a hand-kept doc
again with a misleading banner.

Every check here can go red on purpose, and one twin per clause proves it.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent
SRC = PKG / "scripts" / "gen-cli-reference.py"


def _load():
    spec = importlib.util.spec_from_file_location("gen_cli_reference_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _load()


@pytest.fixture(scope="module")
def expected() -> dict[str, str]:
    return gen.generate()


# --------------------------------------------------------------------- the real tree


def test_the_generation_has_a_denominator(expected):
    """A generator that enumerated nothing would make every freshness check vacuous."""
    refs = [k for k in expected if k.startswith("reference/")]
    assert len(refs) >= 11, refs
    subs = sum(len(re.findall(r"^## `", expected[k], re.M)) for k in refs)
    assert subs >= 40, subs


def test_the_committed_skill_is_fresh(expected):
    if not (PKG.parents[1] / "agent").is_dir():
        pytest.skip("not a source checkout (no agent/) -- generated-skill freshness NOT checked here")
    found = gen.problems(expected)
    assert found == [], (
        "the generated CLI reference is out of date -- run "
        "`uv run python scripts/gen-cli-reference.py`: %s" % found)


def test_every_generated_file_is_COMMITTABLE(expected):
    """A generated file that .gitignore swallows exists only on the machine that made it.

    MEASURED by review 2026-09-13: the repo's `reference/` rule (for unpacked game data)
    matched `.claude/skills/x4-cli-reference/reference/`, so all 11 help files were never
    committed -- while the freshness test here stayed green, because it reads the DISK. A
    clone failed it immediately. Asked of git per file, with `--no-index` so a file that
    happens to be tracked cannot hide the rule that would drop the next one.
    """
    import subprocess
    root = PKG.parents[1]
    top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root.resolve():
        pytest.skip("not this toolkit's own git checkout -- ignore rules NOT CHECKED")
    ignored = []
    # Every copy must be committable: the source under agent/ and the renderings under .claude/
    # and .agents/ (Plan 2: MEASURED 2026-10-02, the bare `reference/` rule matched .agents/ too).
    for rel in expected:
        for path in (f"agent/skills/x4-cli-reference/{rel}", f".claude/skills/x4-cli-reference/{rel}",
                     f".agents/skills/x4-cli-reference/{rel}", f".opencode/skills/x4-cli-reference/{rel}"):
            r = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "--no-index", path])
            if r.returncode == 0:
                ignored.append(path)
            elif r.returncode != 1:
                pytest.fail(f"git check-ignore could not answer for {path} (rc {r.returncode})")
    assert ignored == [], f".gitignore swallows generated file(s): {ignored}"


def test_the_skill_frontmatter_is_a_loadable_skill(expected):
    head = expected["SKILL.md"].split("\n")
    assert head[0] == "---" and head[1] == "name: x4-cli-reference", head[:3]
    assert head[2].startswith("description: Use when"), head[2]
    assert "---" in head[3:6]


def test_no_personal_path_or_profile_id_reaches_the_skill(expected):
    """Skills ship to every user and hold no personal paths. MEASURED 2026-09-13: the
    help text names only `$X4_*` variables, so this is expected to stay empty."""
    bad = re.compile(r"\b[A-Za-z]:[\\/]|/home/|/Users/|\b\d{8}\b")
    hits = {k: bad.findall(v) for k, v in expected.items() if bad.search(v)}
    assert hits == {}, hits


def test_generation_is_deterministic(expected):
    assert gen.generate() == expected


# ------------------------------------------------------------- falsification twins


@pytest.fixture
def fresh_copy(tmp_path, expected):
    d = tmp_path / "x4-cli-reference"
    for rel, text in expected.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    assert gen.problems(expected, d) == [], "precondition: the copy starts fresh"
    return d


def test_TWIN_a_changed_line_is_STALE(fresh_copy, expected):
    p = fresh_copy / "reference" / "x4save.md"
    p.write_bytes(p.read_bytes().replace(b"usage:", b"usage :", 1))
    assert gen.problems(expected, fresh_copy) == ["STALE    reference/x4save.md"]


def test_TWIN_a_deleted_reference_is_MISSING(fresh_copy, expected):
    (fresh_copy / "reference" / "x4diff.md").unlink()
    assert gen.problems(expected, fresh_copy) == ["MISSING  reference/x4diff.md"]


def test_TWIN_an_extra_file_is_a_GHOST(fresh_copy, expected):
    (fresh_copy / "reference" / "x4retired.md").write_bytes(b"old\n")
    assert gen.problems(expected, fresh_copy) == ["GHOST    reference/x4retired.md"]


def test_TWIN_CRLF_line_endings_alone_are_NOT_stale(fresh_copy, expected):
    """An autocrlf checkout of a correct file must not fail the suite."""
    p = fresh_copy / "SKILL.md"
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    assert gen.problems(expected, fresh_copy) == []


def test_TWIN_the_two_enumerations_disagreeing_REFUSES(monkeypatch):
    """If argparse's invalid-choice list and the help's {...} block disagree, neither is
    written -- a reference built on one of two contradicting answers is a guess."""
    real = gen._surface.subcommands

    def lying(cli, cwd=None):
        subs, note = real(cli, cwd)
        return ((subs or []) + ["ghost-subcommand"], note) if cli == "x4save" else (subs, note)

    monkeypatch.setattr(gen._surface, "subcommands", lying)
    with pytest.raises(gen.GenerationError, match="DISAGREE"):
        gen.generate()


def test_TWIN_an_unenumerable_surface_REFUSES(monkeypatch):
    monkeypatch.setattr(gen._surface, "subcommands",
                        lambda cli, cwd=None: (None, "could not be run"))
    with pytest.raises(gen.GenerationError, match="not enumerable"):
        gen.generate()


def test_TWIN_a_help_that_prints_no_usage_REFUSES():
    with pytest.raises(gen.GenerationError, match="no usage block"):
        gen.capture_help("builtins:print", [])


def test_listed_subcommands_rejoins_a_hyphen_wrap():
    """argparse wraps at hyphens; the index must not read 'cross- checked'."""
    text = ("usage: x [-h] {a,b} ...\n\npositional arguments:\n  {a,b}\n"
            "    a           scan things, cross-\n"
            "                checked against a list\n"
            "    b           other\n")
    assert gen.listed_subcommands(text) == [("a", "scan things, cross-checked against a list"),
                                           ("b", "other")]


def test_the_check_mode_exits_1_on_a_stale_tree(monkeypatch, fresh_copy):
    p = fresh_copy / "SKILL.md"
    p.write_bytes(p.read_bytes() + b"hand edit\n")
    monkeypatch.setattr(gen, "SKILL_DIR", fresh_copy)
    assert gen.main(["--check"]) == 1


def test_the_check_mode_exits_0_on_a_fresh_tree(monkeypatch, fresh_copy):
    monkeypatch.setattr(gen, "SKILL_DIR", fresh_copy)
    assert gen.main(["--check"]) == 0


def test_write_mode_REPORTS_a_ghost_and_never_deletes_it(monkeypatch, fresh_copy, capsys):
    """FX-G3 (v4.0.0 delta review): the write mode unlink()ed every ghost -- a permanent delete
    of a file in the committed skill directory. Now it is named, kept, and the run exits 1."""
    ghost = fresh_copy / "reference" / "x4retired.md"
    ghost.write_bytes(b"old\n")
    monkeypatch.setattr(gen, "SKILL_DIR", fresh_copy)
    assert gen.main([]) == 1
    assert ghost.read_bytes() == b"old\n", "the ghost was deleted"
    err = capsys.readouterr().err
    assert "NOT DELETED" in err and "reference/x4retired.md" in err and "git rm" in err, err


def test_TWIN_write_mode_with_NO_ghost_exits_0(monkeypatch, fresh_copy):
    p = fresh_copy / "SKILL.md"
    p.write_bytes(p.read_bytes() + b"hand edit\n")          # stale, not a ghost: rewritten, rc 0
    monkeypatch.setattr(gen, "SKILL_DIR", fresh_copy)
    assert gen.main([]) == 0
    assert gen.main(["--check"]) == 0


def test_the_generator_writes_into_the_repos_skills_dir():
    """The skill's SOURCE is the repo's agent/skills (gen-agent-trees.py then renders it into
    .claude/skills, the directory both installers copy from), never the package's."""
    assert gen.SKILL_DIR == PKG.parents[1] / "agent" / "skills" / "x4-cli-reference"


# --- Lane J (Plan 3) J1: an undecodable committed file refuses (rc 2), in both modes ---

def test_TWIN_a_non_utf8_committed_file_REFUSES_in_check_mode(monkeypatch, fresh_copy, capsys):
    """An undecodable file is not 'stale' or 'fresh' -- the check could not read it. It must
    say so (rc 2), never die with a traceback (rc 1, which reads as 'stale')."""
    (fresh_copy / "reference" / "x4save.md").write_bytes(b"\xff\xfe not utf-8\n")
    monkeypatch.setattr(gen, "SKILL_DIR", fresh_copy)
    assert gen.main(["--check"]) == 2
    err = capsys.readouterr().err
    assert "REFUSING" in err and "reference/x4save.md" in err and "UTF-8" in err, err


def test_TWIN_a_non_utf8_committed_file_REFUSES_in_write_mode_and_writes_nothing(monkeypatch, fresh_copy):
    bad = fresh_copy / "reference" / "x4save.md"
    bad.write_bytes(b"\xff\xfe not utf-8\n")
    before = {p: p.read_bytes() for p in fresh_copy.rglob("*") if p.is_file()}
    monkeypatch.setattr(gen, "SKILL_DIR", fresh_copy)
    assert gen.main([]) == 2
    assert {p: p.read_bytes() for p in fresh_copy.rglob("*") if p.is_file()} == before


def test_problems_raises_GenerationError_on_an_undecodable_file(fresh_copy, expected):
    (fresh_copy / "SKILL.md").write_bytes(b"\x80")
    with pytest.raises(gen.GenerationError, match=r"SKILL\.md.*not UTF-8"):
        gen.problems(expected, fresh_copy)


def test_TWIN_a_non_utf8_GHOST_is_still_just_a_ghost(fresh_copy, expected):
    """Only EXPECTED files are decoded; a stray binary file is a GHOST, not a refusal."""
    (fresh_copy / "reference" / "x4junk.bin").write_bytes(b"\xff\xfe")
    assert gen.problems(expected, fresh_copy) == ["GHOST    reference/x4junk.bin"]
