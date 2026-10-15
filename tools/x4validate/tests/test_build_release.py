r"""The release bundle must be REPRODUCIBLE, and the build must prove it is the tag.

WHY. v3.0.0 shipped `X4.Foundations.Claude.Code.Toolkit.v3.0.0.zip` and nothing in the
repo made it. A bundle assembled by hand cannot be diffed against the tag, so nothing
could have said whether its contents were the reviewed contents -- and this repo has
already shipped a release whose bundle carried a MUTATED `_merge.py`, picked up from a
working tree while a mutation gate was running (CLAUDE.md #27). `git archive` reads the
committed object store and cannot see a working-tree edit at all, so it is immune to
that window by construction rather than by anyone remembering.

MEASURED 2026-09-07: `git archive --format=zip v3.0.0` reproduces the published asset
BYTE FOR BYTE. That is what makes `--selftest` a control rather than a claim.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "build-release.sh"

# NEVER a bare "bash": on Windows `shutil.which("bash")` returns the WSL stub in
# System32 (a bash.exe that is not a shell), which runs a Linux bash in a
# filesystem where the C: drive does not exist -- so every path-shaped assertion
# silently changes meaning. `scripts/gitbash.py` exists because that cost three
# debugging sessions here, and my first draft of this file walked straight into it.
_spec = importlib.util.spec_from_file_location("_gitbash", REPO / "scripts" / "gitbash.py")
_gitbash = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gitbash)


def _bash():
    b = _gitbash.find_bash()
    if not b:
        pytest.skip("no real (non-WSL) bash on this machine -- not checked")
    return str(b)


def _git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, text=True)


def _needs(cond, why):
    if not cond:
        pytest.skip(why)          # skip, never a bare return -- a return is invisible


def test_the_build_script_exists_and_is_syntactically_valid():
    _needs(SCRIPT.is_file(), "no scripts/build-release.sh (dev-only script) — not checked")
    r = subprocess.run([_bash(), "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_SELFTEST_still_reproduces_the_published_v300_asset():
    """The control. If `git archive` ever stops reproducing the one asset whose bytes
    are publicly known, this says so BEFORE a release does."""
    _needs(SCRIPT.is_file(), "no scripts/build-release.sh (dev-only script) — not checked")
    _needs(_git("rev-parse", "--git-dir").returncode == 0,
           "not a git checkout, so no ref can be archived — not checked")
    _needs(_git("rev-parse", "-q", "--verify", "v3.0.0^{}").returncode == 0,
           "tag v3.0.0 not present in this clone — not checked")
    r = subprocess.run([_bash(), str(SCRIPT), "--selftest"],
                       cwd=str(REPO), capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SELFTEST PASSED" in r.stdout


@pytest.mark.parametrize("hostile", ["tz_utc", "eol_lf"])
def test_the_SELFTEST_does_not_depend_on_the_builders_TZ_or_line_ending(hostile, tmp_path):
    """The published v3.0.0 zip encodes TWO facts about the machine that built it, and the
    selftest must pin both rather than inherit them. MEASURED 2026-10-04 (CI run 37172347642,
    the first run with tags -- the earlier one SKIPPED this control for want of v3.0.0):
    - TZ: a zip member's DOS timestamp is LOCAL time. TZ=UTC here reproduced the windows
      runner's sha 7287d2ac... exactly (same size, other bytes); TZ=EST5EDT gives 273d242c....
    - line ending: `* text=auto` checks text out with the NATIVE eol, so a Windows build wrote
      CRLF into 37 members. Ubuntu (UTC, LF) reproduced the ubuntu runner's 6,506,664 B
      sha 1e514962... exactly in an ubuntu:24.04 container; TZ=EST5EDT plus core.eol=crlf
      there reproduced 273d242c....
    One hostile axis per case, so each pin is proved separately."""
    _needs(SCRIPT.is_file(), "no scripts/build-release.sh (dev-only script) — not checked")
    _needs(_git("rev-parse", "-q", "--verify", "v3.0.0^{}").returncode == 0,
           "tag v3.0.0 not present in this clone — not checked")
    import os
    env = dict(os.environ)
    cfg = tmp_path / "gitconfig"
    if hostile == "tz_utc":
        env["TZ"] = "UTC"
        cfg.write_text("", encoding="utf-8")
    else:
        env["TZ"] = "EST5EDT"
        cfg.write_text("[core]\n\teol = lf\n\tautocrlf = false\n", encoding="utf-8")
        env["GIT_CONFIG_NOSYSTEM"] = "1"     # a system autocrlf=true would otherwise rescue it
    env["GIT_CONFIG_GLOBAL"] = str(cfg)
    r = subprocess.run([_bash(), str(SCRIPT), "--selftest"], cwd=str(REPO), env=env,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, (hostile, r.stdout + r.stderr)
    assert "SELFTEST PASSED" in r.stdout


def test_a_bundle_that_is_NOT_the_ref_is_REFUSED():
    """The twin, and the point of the whole script: not "did a zip appear" but "is
    what is in it what the tag says". Both directions matter -- a missing file ships a
    broken toolkit, an extra file ships something nobody reviewed. Checked by handing
    the verifier a v3.0.0 bundle against HEAD, which has more files."""
    _needs(SCRIPT.is_file(), "no scripts/build-release.sh (dev-only script) — not checked")
    _needs(_git("rev-parse", "-q", "--verify", "v3.0.0^{}").returncode == 0,
           "tag v3.0.0 not present in this clone — not checked")
    src = SCRIPT.read_text(encoding="utf-8")
    body = src[src.index("import subprocess, sys, zipfile"):src.index("\nPY\n")]
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        z = os.path.join(td, "x.zip")
        assert _git("archive", "--format=zip", "v3.0.0", "-o", z).returncode == 0
        v = Path(td) / "v.py"
        v.write_text(body, encoding="utf-8")
        r = subprocess.run([sys.executable, str(v), z, "HEAD"],
                           cwd=str(REPO), capture_output=True, text=True)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "MISSING FROM THE BUNDLE" in r.stderr


def test_the_bundle_carries_the_whole_TRACKED_set_and_no_wrapper_folder():
    """v3.0.0 shipped the tag's tracked set exactly -- no exclusions, no additions and
    no top-level wrapper directory. Pinned because an install script that resolves
    paths relative to the archive root breaks the moment someone adds a wrapper."""
    _needs(_git("rev-parse", "-q", "--verify", "v3.0.0^{}").returncode == 0,
           "tag v3.0.0 not present in this clone — not checked")
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        z = os.path.join(td, "x.zip")
        assert _git("archive", "--format=zip", "v3.0.0", "-o", z).returncode == 0
        members = {i.filename for i in zipfile.ZipFile(z).infolist() if not i.is_dir()}
    tracked = set(_git("ls-tree", "-r", "--name-only", "v3.0.0").stdout.split())
    assert members == tracked, sorted(members ^ tracked)[:10]
    assert "install.sh" in members, "the installer must sit at the archive ROOT"


def test_an_EXTRA_file_in_the_bundle_is_REFUSED_too(planted_verifier):
    """Both the script and the sibling test say "Both directions matter", and only
    MISSING was exercised: a mutant setting `extra = []` left all four tests green.

    A hand-assembled zip, a stray wrapper directory, or an `export-ignore` inversion
    ships content nobody reviewed, and the verifier passed it. This is the direction
    that matters for a RELEASE, because a bundle with an extra file is a bundle
    carrying something outside the reviewed range.
    """
    verify, repo, ref = planted_verifier
    import zipfile, subprocess, sys, os
    z = os.path.join(repo, "extra.zip")
    assert subprocess.run(["git", "-C", repo, "archive", "--format=zip", ref, "-o", z],
                          capture_output=True).returncode == 0
    with zipfile.ZipFile(z, "a") as zf:
        zf.writestr("NOT_IN_THE_REF.txt", "shipped without review\n")
    r = subprocess.run([sys.executable, str(verify), z, ref],
                       cwd=repo, capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "IN THE BUNDLE, NOT IN THE REF" in r.stderr, r.stderr


@pytest.mark.parametrize("defect", ["content", "duplicate"])
def test_same_member_names_do_not_certify_changed_or_duplicate_content(planted_verifier, defect):
    verify, repo, ref = planted_verifier
    archive = Path(repo) / "candidate.zip"
    subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "-C", repo,
                    "archive", "--format=zip", ref, "-o", str(archive)], check=True)
    with zipfile.ZipFile(archive) as bundle:
        records = [(entry, bundle.read(entry)) for entry in bundle.infolist()]
    with zipfile.ZipFile(archive, "w") as bundle:
        for entry, data in records:
            bundle.writestr(entry, b"changed unreviewed implementation\n" if defect == "content" and entry.filename == "a.txt" else data)
        if defect == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate name"):
                bundle.writestr("a.txt", b"x\n")
    result = subprocess.run([sys.executable, str(verify), str(archive), ref], cwd=repo,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    assert ("CONTENT DIFFERS" if defect == "content" else "DUPLICATE MEMBER") in result.stderr


@pytest.mark.parametrize("case", ["declared_text", "binary", "undeclared_text", "global_text", "local_info"])
def test_only_committed_text_attributes_can_allow_line_ending_conversion(planted_verifier, case):
    import os
    verify, repo, ref = planted_verifier
    root = Path(repo)
    if case in ("declared_text", "binary"):
        (root / ".gitattributes").write_text("a.txt " + ("text eol=crlf" if case == "declared_text" else "binary") + "\n")
        subprocess.run(["git", "-C", repo, "add", "--", ".gitattributes"], check=True, capture_output=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "attributes"], check=True, capture_output=True)
    archive = root / "candidate.zip"
    subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "-C", repo,
                    "archive", "--format=zip", ref, "-o", str(archive)], check=True)
    with zipfile.ZipFile(archive) as bundle:
        records = [(entry, bundle.read(entry)) for entry in bundle.infolist()]
    with zipfile.ZipFile(archive, "w") as bundle:
        for entry, data in records:
            bundle.writestr(entry, b"x\r\n" if entry.filename == "a.txt" else data)
    environment = dict(os.environ)
    if case == "global_text":
        attrs = root.parent / "global-attributes"
        attrs.write_text("* text\n")
        config = root.parent / "global-config"
        config.write_text('[core]\nattributesFile = "' + attrs.as_posix() + '"\n')
        environment["GIT_CONFIG_GLOBAL"] = str(config)
    elif case == "local_info":
        (root / ".git/info/attributes").write_text("* text\n")
    result = subprocess.run([sys.executable, str(verify), str(archive), ref], cwd=repo, env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == (0 if case == "declared_text" else 1), result.stdout + result.stderr
    if case != "declared_text":
        assert ("info/attributes" if case == "local_info" else "CONTENT DIFFERS") in result.stderr


def test_the_bundle_is_named_for_the_v4_product(tmp_path):
    """v4.0 renamed the product (Plan 3 DECISIONS, Wave 2): the asset is
    `X4.Foundations.AI.Assistant.Toolkit-<tag>.zip`. Run the REAL script on a tiny tagged repo
    and read the name it actually wrote, rather than grepping the script for a string."""
    _needs(SCRIPT.is_file(), "no scripts/build-release.sh (dev-only script) - not checked")
    import shutil
    r = tmp_path / "repo"
    (r / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, r / "scripts" / "build-release.sh")
    (r / "a.txt").write_text("x\n", encoding="utf-8")
    for a in (["init", "-q", "."], ["config", "user.name", "t"], ["config", "user.email", "t@t"],
              ["config", "core.autocrlf", "false"], ["add", "a.txt", "scripts/build-release.sh"],
              ["commit", "-qm", "base"], ["tag", "v9.9.9"]):
        assert subprocess.run(["git", "-C", str(r), *a], capture_output=True).returncode == 0, a
    p = subprocess.run([_bash(), "scripts/build-release.sh", "v9.9.9"], cwd=str(r),
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    built = sorted(x.name for x in (r / "dist").iterdir())
    assert built == ["X4.Foundations.AI.Assistant.Toolkit-v9.9.9.zip"], built


@pytest.fixture()
def planted_verifier(tmp_path):
    """The verifier body, extracted from the script, plus a tiny repo to run it on."""
    _needs(SCRIPT.is_file(), "no scripts/build-release.sh (dev-only script) - not checked")
    import subprocess
    src = SCRIPT.read_text(encoding="utf-8")
    body = src[src.index("import subprocess, sys, zipfile"):src.index("\nPY\n")]
    v = tmp_path / "verify.py"
    v.write_text(body, encoding="utf-8")
    r = tmp_path / "repo"
    r.mkdir()
    for a in (["init", "-q", "."], ["config", "user.name", "t"],
              ["config", "user.email", "t@t"]):
        subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    (r / "a.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "add", "a.txt"], capture_output=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "base"], capture_output=True)
    return v, str(r), "HEAD"


def test_the_internal_audit_harness_is_NOT_in_the_bundle(tmp_path):
    """v4.0.0 review R5-12: tools/x4validate/audit/ is a maintainer's audit harness (its own
    README: "a local audit instrument, not a shipped CLI") and shipped in every install.
    `export-ignore` keeps it in the repo and out of `git archive`, like release/.

    FX-H2 / I-1: the SHIPPED .gitattributes is asked git-free first, so a release zip (no .git)
    still checks the rule; the repository itself only when this is the toolkit's checkout."""
    import subprocess
    from _layout import own_checkout, shipped_rules_repo
    root = Path(__file__).resolve().parents[3]
    rules = shipped_rules_repo(tmp_path, root)
    r = subprocess.run(["git", "-C", str(rules), "check-attr", "export-ignore", "--",
                        "tools/x4validate/audit/driver.py", "tools/x4validate/x4validate/_paths.py"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    got = dict(line.split(": export-ignore: ") for line in r.stdout.strip().splitlines())
    assert got["tools/x4validate/audit/driver.py"] == "set", got
    assert got["tools/x4validate/x4validate/_paths.py"] == "unspecified", got   # twin
    if own_checkout(root):
        _audit_export_ignore(root)


def _audit_export_ignore(root: Path) -> None:
    import subprocess
    # v4.0.0 delta review: "git check-attr failed" was the only skip, so an INSTALLED toolkit
    # sitting inside ANOTHER git repo (the in-game method installs into the game root, which
    # can be one) asked that repo and went red. Not this toolkit's own checkout -> counted
    # skip; in a checkout a failing git is a FAILURE, not a skip.
    from _layout import REASON, installed_layout
    if installed_layout(root):
        pytest.skip(f"{REASON}: the export-ignore audit needs this toolkit's own git checkout")
    r = subprocess.run(["git", "-C", str(root), "check-attr", "export-ignore", "--",
                        "tools/x4validate/audit/driver.py", "tools/x4validate/x4validate/_paths.py"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    got = dict(line.split(": export-ignore: ") for line in r.stdout.strip().splitlines())
    assert got["tools/x4validate/audit/driver.py"] == "set", got
    assert got["tools/x4validate/x4validate/_paths.py"] == "unspecified", got   # twin


def _tiny_repo(root: Path, files: dict) -> Path:
    import subprocess
    root.mkdir(parents=True)
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    for a in (("init", "-q"), ("add", *files),
              ("-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
               "commit", "-qm", "x")):
        assert subprocess.run(["git", *a], cwd=root, capture_output=True).returncode == 0, a
    return root


def test_TWIN_an_install_inside_ANOTHER_git_repo_SKIPS_the_export_ignore_audit(tmp_path):
    game = _tiny_repo(tmp_path / "game", {"readme.txt": "x"})        # no agent/ tracked
    (game / "tools").mkdir()
    with pytest.raises(pytest.skip.Exception, match="export-ignore audit"):
        _audit_export_ignore(game)


def test_TWIN_a_CHECKOUT_without_the_attribute_FAILS_the_export_ignore_audit(tmp_path):
    co = _tiny_repo(tmp_path / "co", {"agent/a.md": "x", "tools/x4validate/audit/driver.py": ""})
    with pytest.raises(AssertionError):
        _audit_export_ignore(co)
    (co / ".gitattributes").write_text("tools/x4validate/audit/** export-ignore\n",
                                       encoding="utf-8")
    _audit_export_ignore(co)                                           # and with it, passes
