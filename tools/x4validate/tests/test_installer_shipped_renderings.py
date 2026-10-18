"""Upgrades recognise every SHIPPED version, not only the current one (release review H4, H6).

H4: the Codex .codex/hooks.json precheck accepted only a rendering of the CURRENT template, so
the first template change after a release would refuse every upgrade of an unedited install
("custom Codex hook definitions need a reviewed upgrade"). A rendering of any template a
release shipped (scripts/shipped-instruction-hashes.txt, the list .claude/settings.json uses)
is the toolkit's own and may be replaced.

H6: notes-preserve saved a KNOWLEDGEBASE.pre-4.0-<sha>.md on every upgrade whose KB differed
from the source's -- including an UNEDITED KB a previous release shipped. A shipped KB is now
replaced without a preserved copy; an edited one is still preserved.
"""
import json
import shutil
import subprocess
import sys

import pytest

from _layout import require_repo
from test_install_over_existing import ROOT, _fresh, _install, _ok

sys.path.insert(0, str(ROOT / "tools" / "x4_harness"))
import x4_upgrade_render  # noqa: E402

TEMPLATE = "agent/targets/codex/hooks.json.tmpl"
HASHES = "scripts/shipped-instruction-hashes.txt"


def _canonical_sha256(data: bytes) -> str:
    import hashlib
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return hashlib.sha256(data.replace(b"\r", b"").rstrip(b"\n")).hexdigest()


@pytest.fixture(scope="module")
def source_copy(tmp_path_factory):
    """A Git-less copy of this source tree (git archive HEAD) carrying THIS tree's installers
    and shipped list, so a test can add a row to the list without touching the repository."""
    require_repo(".git", why="the source copy is archived from git")
    root = tmp_path_factory.mktemp("shipped-render-source")
    archive = subprocess.check_output(["git", "-C", str(ROOT), "archive", "--format=tar", "HEAD"])
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(root, filter="data")
    for name in ("install.sh", "install.ps1", HASHES, TEMPLATE):
        shutil.copy2(ROOT / name, root / name)
    return root


def _old_template() -> str:
    """A template a 'previous release' shipped: the current one with one value changed."""
    text = (ROOT / TEMPLATE).read_text(encoding="utf-8")
    old = text.replace('"timeout": 60', '"timeout": 61', 1)
    assert old != text, "the template no longer has the planted value"
    return old


def _source_with_row(source_copy, tmp_path, listed: bool):
    src = tmp_path / "src"
    shutil.copytree(source_copy, src, symlinks=True)
    if listed:
        with (src / HASHES).open("a", encoding="utf-8", newline="\n") as f:
            f.write("%s  %s  v0.0-test\n" % (_canonical_sha256(_old_template().encode("utf-8")), TEMPLATE))
    return src


@pytest.mark.parametrize("installer", ["sh", "ps1", "ps51"])
@pytest.mark.parametrize("listed", [True, False])
def test_H4_a_rendering_of_a_SHIPPED_older_codex_template_upgrades(tmp_path, source_copy, installer, listed):
    src = _source_with_row(source_copy, tmp_path, listed)
    dest = _fresh(tmp_path)
    hooks = dest / ".codex" / "hooks.json"
    hooks.parent.mkdir()
    hooks.write_text(x4_upgrade_render.render_codex(_old_template(), dest.resolve()), encoding="utf-8")
    r = _install(installer, tmp_path, dest, "--agent", "codex", source=src)
    out = r.stdout + r.stderr
    if listed:
        assert r.returncode == 0, _ok(r)
        current = x4_upgrade_render.render_codex((ROOT / TEMPLATE).read_text(encoding="utf-8"), dest.resolve())
        assert json.loads(hooks.read_text(encoding="utf-8")) == json.loads(current)
    else:   # TWIN: not a shipped template -> still the user's, still refused
        assert r.returncode != 0 and "custom Codex hook definitions" in out, _ok(r)


@pytest.mark.parametrize("installer", ["sh", "ps1", "ps51"])
@pytest.mark.parametrize("edited", [False, True])
def test_H6_an_UNEDITED_shipped_knowledgebase_is_not_preserved_as_notes(tmp_path, installer, edited):
    require_repo(".git", why="reads a released KNOWLEDGEBASE.md from a tag")
    shipped = subprocess.check_output(["git", "-C", str(ROOT), "show", "v3.3.1:KNOWLEDGEBASE.md"])
    assert shipped != (ROOT / "KNOWLEDGEBASE.md").read_bytes(), "the planted KB must DIFFER from the source's"
    dest = _fresh(tmp_path)
    (dest / "KNOWLEDGEBASE.md").write_bytes(shipped + (b"\n## My own note\nkeep me\n" if edited else b""))
    r = _install(installer, tmp_path, dest, "--agent", "claude")
    assert r.returncode == 0, _ok(r)
    saved = sorted(p.name for p in dest.glob("KNOWLEDGEBASE.pre-*.md"))
    if edited:   # TWIN: the user's edits are still preserved
        assert len(saved) == 1, saved
        assert b"keep me" in (dest / saved[0]).read_bytes()
    else:
        assert saved == [], saved
