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
    (r / "CHANGELOG.md").write_text("# Changelog\n\n## v9.9.9\n", encoding="utf-8")   # a tag names itself
    (r / "tools" / "x4validate").mkdir(parents=True)
    (r / "tools" / "x4validate" / "pyproject.toml").write_text('[project]\nversion = "9.9.9"\n', encoding="utf-8")
    for a in (["init", "-q", "."], ["config", "user.name", "t"], ["config", "user.email", "t@t"],
              ["config", "core.autocrlf", "false"], ["add", "a.txt", "scripts/build-release.sh", "CHANGELOG.md",
                                                     "tools/x4validate/pyproject.toml"],
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


def _release_repo(tmp_path, heading, version, tag="v4.0.1"):
    """A tiny repo holding the build script, a CHANGELOG whose first `## ` heading is `heading`, and a
    pyproject at `version`, tagged `tag`."""
    r = tmp_path / "rel"
    (r / "scripts").mkdir(parents=True)
    (r / "tools" / "x4validate").mkdir(parents=True)
    (r / "scripts" / "build-release.sh").write_bytes(SCRIPT.read_bytes())
    (r / "tools" / "x4validate" / "pyproject.toml").write_text(
        '[project]\nname = "x4validate"\nversion = "%s"\n' % version, encoding="utf-8")
    (r / "CHANGELOG.md").write_text("# Changelog\n\n%s\n\n- a fix\n\n## v4.0.0 - 2026-10-21\n\n- old\n" % heading,
                                    encoding="utf-8")
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false", "-C", str(r)]
    for a in (["init", "-q", "."], ["add", "scripts/build-release.sh", "tools/x4validate/pyproject.toml",
                                    "CHANGELOG.md"], ["commit", "-qm", "release"], ["tag", tag]):
        assert subprocess.run(g[:7] + ["-C", str(r)] + a, capture_output=True).returncode == 0, a
    return r


@pytest.mark.parametrize("heading,version,ok", [
    ("## Unreleased", "4.0.0", False),            # the notes never got their version heading
    ("## Unreleased", "4.0.1", False),            # clause A alone: the top heading is not the tag
    ("## v4.0.1 - 2026-10-25", "4.0.0", False),   # clause B alone: the package says another version
    ("## v4.0.1 - 2026-10-25", "4.0.1", True),    # twin: both agree with the tag
])
def test_a_version_tag_builds_only_when_its_notes_and_package_name_that_version(tmp_path, heading, version, ok):
    """Skyrim toolkit 2026-10 release review, probed here: a tag whose CHANGELOG still opened with
    `## Unreleased` (and whose pyproject still said the previous version) built and verified."""
    _needs(SCRIPT.is_file(), "no scripts/build-release.sh (dev-only script) - not checked")
    r = _release_repo(tmp_path, heading, version)
    p = subprocess.run([_bash(), "scripts/build-release.sh", "v4.0.1"], cwd=str(r), capture_output=True,
                       text=True, timeout=120)
    if ok:
        assert p.returncode == 0, p.stdout + p.stderr
    else:
        assert p.returncode == 2 and "REFUSING" in p.stderr, p.stdout + p.stderr
        assert not (r / "dist" / "X4.Foundations.AI.Assistant.Toolkit-v4.0.1.zip").exists()


def test_a_ref_that_is_not_a_version_tag_still_builds(tmp_path):
    """Twin on the trigger: only a vX.Y.Z ref is a release; a branch build is a dry run and says so."""
    _needs(SCRIPT.is_file(), "no scripts/build-release.sh (dev-only script) - not checked")
    r = _release_repo(tmp_path, "## Unreleased", "4.0.0", tag="rc-test")
    p = subprocess.run([_bash(), "scripts/build-release.sh", "rc-test"], cwd=str(r), capture_output=True,
                       text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "not a vX.Y.Z" in p.stdout


@pytest.mark.parametrize("ref", ["tags/v4.0.1", "refs/tags/v4.0.1"])
@pytest.mark.parametrize("heading,version,ok", [
    ("## Unreleased", "4.0.1", False),            # the notes do not name the tag
    ("## v4.0.1 - 2026-10-25", "4.0.0", False),   # the package names another version
    ("## v4.0.1 - 2026-10-25", "4.0.1", True),    # twin: both agree, and the long spelling builds
])
def test_a_version_tag_spelled_as_a_full_ref_is_still_checked(tmp_path, ref, heading, version, ok):
    """4.0.1 batch: the gate matched only a bare `vX.Y.Z`, so `tags/v4.0.1` and `refs/tags/v4.0.1` --
    the same tag -- skipped the CHANGELOG/pyproject check as a "dry run" and built."""
    _needs(SCRIPT.is_file(), "no scripts/build-release.sh (dev-only script) - not checked")
    r = _release_repo(tmp_path, heading, version)
    p = subprocess.run([_bash(), "scripts/build-release.sh", ref], cwd=str(r), capture_output=True,
                       text=True, timeout=120)
    if ok:
        assert p.returncode == 0, p.stdout + p.stderr
        assert "not a vX.Y.Z" not in p.stdout, p.stdout
        assert (r / "dist" / "X4.Foundations.AI.Assistant.Toolkit-v4.0.1.zip").is_file(), p.stdout
    else:
        # The VERSION check must be what refused: a later `git archive` failure on the slashed
        # name also exits 2 with REFUSING, which made the first draft of this test pass on the bug.
        assert p.returncode == 2 and "at the ref" in p.stderr, p.stdout + p.stderr


# --- 4.3.1 task 6a: the zip the shared core's self-update can ACCEPT -----------------------------
#
# The core's `release.fetch` refuses a release zip that does not hold exactly ONE top folder, and
# then requires a BUILD-INFO inside it whose `version=` is the tag's and whose `commit=` is the commit
# the tag points at. The v3/v4.0-4.3.0 build (`git archive` with no prefix, no BUILD-INFO) fails both.
# So a RELEASE build is now `git archive --prefix=X4AI/` plus ONE virtual member `X4AI/BUILD-INFO`
# (version, full commit), and the proof strips the folder, allows exactly that one extra member and
# refuses everything else. `--selftest` (v3.0.0) keeps the old no-folder shape: it is the control
# that the build METHOD still reproduces the published bytes.

TOP = "X4AI/"


def _core_release():
    """The shared core's own `release` module -- the thing that decides whether the zip is accepted."""
    tools = str(REPO / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from modding_harness import release
    return release


def _commit_of(repo, ref="HEAD"):
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", ref + "^{commit}"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _prefixed_zip(repo, ref, dest, info=None, extra=(), prefix=TOP):
    """A zip shaped like the release build's, composed here with `zipfile` (not by the script's own
    `--add-virtual-file` call), so the verifier is exercised against INDEPENDENTLY made input."""
    cmd = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "-C", str(repo), "archive", "--format=zip"]
    if prefix:
        cmd.append("--prefix=" + prefix)
    assert subprocess.run([*cmd, ref, "-o", str(dest)], capture_output=True).returncode == 0
    with zipfile.ZipFile(dest, "a") as zf:
        if info is not None:
            zf.writestr(prefix + "BUILD-INFO", info)
        for name, data in extra:
            zf.writestr(name, data)
    return dest


def _run_verifier(verify, repo, archive, ref, mode, version=""):
    return subprocess.run([sys.executable, str(verify), str(archive), ref, mode, version], cwd=repo,
                          capture_output=True, text=True, timeout=60)


def test_a_RELEASE_zip_has_one_top_folder_and_a_BUILD_INFO_naming_the_tag_and_its_commit(tmp_path):
    """The point of the task, end to end through the REAL script and the REAL core: the zip the script
    builds for `vX.Y.Z` is accepted by `modding_harness.release._safe_members`, and the BUILD-INFO that
    unpacks from it parses to the tag's version and the tag's commit -- the two facts `fetch` compares."""
    _needs(SCRIPT.is_file(), "no scripts/build-release.sh (dev-only script) - not checked")
    r = _release_repo(tmp_path, "## v4.0.1 - 2026-10-25", "4.0.1")
    p = subprocess.run([_bash(), "scripts/build-release.sh", "v4.0.1"], cwd=str(r), capture_output=True,
                       text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    built = r / "dist" / "X4.Foundations.AI.Assistant.Toolkit-v4.0.1.zip"
    release = _core_release()
    with zipfile.ZipFile(built) as archive:
        top, files = release._safe_members(archive)           # the core's own acceptance test
        names = [i.filename for i in archive.infolist()]
        assert top == "X4AI", top
        assert all(n.startswith(TOP) for n in names), [n for n in names if not n.startswith(TOP)]
        assert "BUILD-INFO" in files and "scripts/build-release.sh" in files, sorted(files)
        assert archive.read(TOP + "BUILD-INFO") == ("version=4.0.1\ncommit=%s\n" % _commit_of(r, "v4.0.1")).encode()
        archive.extractall(tmp_path / "unpacked")
    info = release.build_info(tmp_path / "unpacked" / "X4AI")
    assert release.parse_version(info.get("version")) == (4, 0, 1), info
    assert info.get("commit") == _commit_of(r, "v4.0.1"), info
    assert "member set matches the ref exactly" in p.stdout, p.stdout


def test_a_DRY_RUN_zip_has_a_BUILD_INFO_with_no_version_so_the_core_cannot_take_it_for_a_release(tmp_path):
    _needs(SCRIPT.is_file(), "no scripts/build-release.sh (dev-only script) - not checked")
    r = _release_repo(tmp_path, "## Unreleased", "4.0.0", tag="rc-test")
    p = subprocess.run([_bash(), "scripts/build-release.sh", "rc-test"], cwd=str(r), capture_output=True,
                       text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    built = r / "dist" / "X4.Foundations.AI.Assistant.Toolkit-rc-test.zip"
    release = _core_release()
    with zipfile.ZipFile(built) as archive:
        release._safe_members(archive)                         # the SHAPE is the same as a release
        archive.extractall(tmp_path / "unpacked")
    info = release.build_info(tmp_path / "unpacked" / "X4AI")
    assert info.get("version") == "" and info.get("build") == "dry-run", info
    assert release.parse_version(info.get("version")) is None  # so `fetch` refuses it as a version mismatch
    assert info.get("commit") == _commit_of(r, "rc-test")


def test_TWIN_the_core_REFUSES_a_zip_without_the_top_folder(tmp_path):
    """The twin of the acceptance test above: the pre-4.3.1 build shape, with a BUILD-INFO even, is refused
    by the very function that accepted the new one -- so the acceptance test could have gone red."""
    r = _tiny_repo(tmp_path / "t", {"a.txt": "x\n", "b/c.txt": "y\n"})
    flat = tmp_path / "flat.zip"
    assert subprocess.run(["git", "-C", str(r), "archive", "--format=zip", "HEAD", "-o", str(flat)],
                          capture_output=True).returncode == 0
    with zipfile.ZipFile(flat, "a") as zf:
        zf.writestr("BUILD-INFO", "version=1.2.3\ncommit=%s\n" % _commit_of(r))
    release = _core_release()
    with zipfile.ZipFile(flat) as archive:
        with pytest.raises(ValueError, match="outside the release zip's one folder|exactly one folder"):
            release._safe_members(archive)
    prefixed = _prefixed_zip(r, "HEAD", tmp_path / "ok.zip", info="version=1.2.3\ncommit=x\n")
    with zipfile.ZipFile(prefixed) as archive:
        assert release._safe_members(archive)[0] == "X4AI"


@pytest.mark.parametrize("case,expect", [
    ("good", None),                                              # twin: the one shape that is accepted
    ("extra_member", "IN THE BUNDLE, NOT IN THE REF"),
    ("second_info_lookalike", "IN THE BUNDLE, NOT IN THE REF"),  # `BUILD-INFO.bak` is not THE one extra member
    ("nested_info", "IN THE BUNDLE, NOT IN THE REF"),
    ("wrong_version", "BUILD-INFO IS NOT WHAT THIS BUILD WROTE"),
    ("wrong_commit", "BUILD-INFO IS NOT WHAT THIS BUILD WROTE"),
    ("extra_line", "BUILD-INFO IS NOT WHAT THIS BUILD WROTE"),
    ("missing_info", "MISSING FROM THE BUNDLE"),
    ("outside_folder", "OUTSIDE THE ONE TOP FOLDER"),
    ("no_top_folder", "OUTSIDE THE ONE TOP FOLDER"),
    ("dry_run_info_in_release", "BUILD-INFO IS NOT WHAT THIS BUILD WROTE"),
])
def test_the_RELEASE_member_proof_allows_exactly_ONE_extra_member(planted_verifier, case, expect):
    verify, repo, ref = planted_verifier
    commit = _commit_of(repo)
    info = "version=4.3.1\ncommit=%s\n" % commit
    extra, prefix = (), TOP
    if case == "extra_member":
        extra = [(TOP + "NOT_IN_THE_REF.txt", "shipped without review\n")]
    elif case == "second_info_lookalike":
        extra = [(TOP + "BUILD-INFO.bak", info)]
    elif case == "nested_info":
        extra = [(TOP + "sub/BUILD-INFO", info)]
    elif case == "wrong_version":
        info = "version=4.3.0\ncommit=%s\n" % commit
    elif case == "wrong_commit":
        info = "version=4.3.1\ncommit=%s\n" % ("0" * 40)
    elif case == "extra_line":
        info += "build=hand-made\n"
    elif case == "missing_info":
        info = None
    elif case == "outside_folder":
        extra = [("stray.txt", "x\n")]
    elif case == "no_top_folder":
        prefix, info = "", None
    elif case == "dry_run_info_in_release":
        info = "version=\ncommit=%s\nbuild=dry-run\n" % commit
    archive = _prefixed_zip(repo, ref, Path(repo) / "c.zip", info=info, extra=extra, prefix=prefix)
    if case == "no_top_folder":
        with zipfile.ZipFile(archive, "a") as zf:
            zf.writestr("BUILD-INFO", "version=4.3.1\ncommit=%s\n" % commit)
    got = _run_verifier(verify, repo, archive, ref, "release", "4.3.1")
    if expect is None:
        assert got.returncode == 0, got.stdout + got.stderr
        assert "member set matches the ref exactly" in got.stdout
    else:
        assert got.returncode == 1, got.stdout + got.stderr
        assert expect in got.stderr, got.stderr


def test_the_DRY_RUN_proof_wants_the_dry_run_BUILD_INFO_and_nothing_else(planted_verifier):
    verify, repo, ref = planted_verifier
    commit = _commit_of(repo)
    good = _prefixed_zip(repo, ref, Path(repo) / "d.zip", info="version=\ncommit=%s\nbuild=dry-run\n" % commit)
    assert _run_verifier(verify, repo, good, ref, "dry-run").returncode == 0
    # a dry-run zip may NOT carry a version: it would look like a release to the core
    bad = _prefixed_zip(repo, ref, Path(repo) / "e.zip", info="version=4.3.1\ncommit=%s\nbuild=dry-run\n" % commit)
    got = _run_verifier(verify, repo, bad, ref, "dry-run")
    assert got.returncode == 1 and "BUILD-INFO IS NOT WHAT THIS BUILD WROTE" in got.stderr, got.stderr


@pytest.mark.parametrize("version", ["", "4.3", "v4.3.1", "4.3.1-rc1"])
def test_a_RELEASE_proof_refuses_a_version_that_is_not_X_Y_Z(planted_verifier, version):
    verify, repo, ref = planted_verifier
    archive = _prefixed_zip(repo, ref, Path(repo) / "v.zip",
                            info="version=%s\ncommit=%s\n" % (version, _commit_of(repo)))
    got = _run_verifier(verify, repo, archive, ref, "release", version)
    assert got.returncode == 1 and "X.Y.Z version" in got.stderr, got.stdout + got.stderr


def test_a_TRACKED_BUILD_INFO_at_the_ref_is_REFUSED(tmp_path):
    """A tree must not vouch for itself: with a BUILD-INFO committed at the ref, the one the build adds
    would be a SECOND member of that name. Through the verifier (clear message) and through the script."""
    _needs(SCRIPT.is_file(), "no scripts/build-release.sh (dev-only script) - not checked")
    r = _release_repo(tmp_path, "## v4.0.1 - 2026-10-25", "4.0.1")
    (r / "BUILD-INFO").write_text("version=9.9.9\ncommit=" + "1" * 40 + "\n", encoding="utf-8")
    for a in (["add", "BUILD-INFO"], ["commit", "-qm", "oops"], ["tag", "-f", "v4.0.1"]):
        assert subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(r), *a],
                              capture_output=True).returncode == 0, a
    src = SCRIPT.read_text(encoding="utf-8")
    body = src[src.index("import subprocess, sys, zipfile"):src.index("\nPY\n")]
    verify = tmp_path / "verify.py"
    verify.write_text(body, encoding="utf-8")
    zipped = tmp_path / "tracked.zip"
    assert subprocess.run(["git", "-C", str(r), "archive", "--format=zip", "--prefix=X4AI/", "v4.0.1",
                           "-o", str(zipped)], capture_output=True).returncode == 0
    got = _run_verifier(verify, str(r), zipped, "v4.0.1", "release", "4.0.1")
    assert got.returncode == 1 and "BUILD-INFO is tracked" in got.stderr, got.stdout + got.stderr
    p = subprocess.run([_bash(), "scripts/build-release.sh", "v4.0.1"], cwd=str(r), capture_output=True,
                       text=True, timeout=120)
    assert p.returncode == 1 and "not shippable" in p.stderr, p.stdout + p.stderr


def test_a_git_that_cannot_add_a_virtual_file_is_a_CANNOT_BUILD_not_a_bad_bundle(tmp_path):
    """`git archive --add-virtual-file` (git 2.32+) writes BUILD-INFO. A git without it must exit 2 "cannot
    build" BEFORE building, not produce a zip with no BUILD-INFO and call the bundle corrupt (exit 1)."""
    _needs(SCRIPT.is_file(), "no scripts/build-release.sh (dev-only script) - not checked")
    import os
    import shutil
    real = shutil.which("git")
    _needs(real, "no git on PATH - not checked")
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    # A `git` that answers `archive -h` like an old git (no --add-virtual-file) and forwards the rest.
    (shim_dir / "git").write_text(
        '#!/bin/sh\n'
        'a="$@"\n'
        'case "$a" in *"archive -h"*) echo "usage: git archive [<options>] <tree-ish>"; exit 129 ;; esac\n'
        'exec "%s" "$@"\n' % Path(real).as_posix(), encoding="utf-8", newline="\n")
    r = _release_repo(tmp_path, "## v4.0.1 - 2026-10-25", "4.0.1")
    # Git Bash puts /mingw64/bin first on its own PATH, so a PATH entry from outside never wins; BASH_ENV
    # runs inside the shell and prepends the shim there.
    posix = shim_dir.as_posix()
    if len(posix) > 2 and posix[1] == ":":
        posix = "/" + posix[0].lower() + posix[2:]
    (tmp_path / "shim_env.sh").write_text('export PATH="%s:$PATH"\n' % posix, encoding="utf-8", newline="\n")
    env = dict(os.environ)
    env["BASH_ENV"] = (tmp_path / "shim_env.sh").as_posix()
    p = subprocess.run([_bash(), "scripts/build-release.sh", "v4.0.1"], cwd=str(r), env=env,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 2 and "add-virtual-file" in p.stderr, p.stdout + p.stderr
    assert not (r / "dist").exists() or not list((r / "dist").glob("*.zip"))
