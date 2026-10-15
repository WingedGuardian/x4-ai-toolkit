"""An owned destination must not redirect installer writes into a sibling tree."""
import os
import io
import shutil
import subprocess
import zipfile

import pytest

from test_install_over_existing import ROOT, _fresh, _install, _ok


def _directory_link(tmp_path, link, outside):
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        shell = shutil.which("powershell")
        if shell is None:
            pytest.skip("Windows junction creation requires Windows PowerShell")
        script = tmp_path / "make-junction.ps1"
        script.write_text("param($Link, $Target)\nNew-Item -ItemType Junction -Path $Link -Target $Target -ErrorAction Stop | Out-Null\n")
        subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(script), str(link), str(outside)],
                       check=True, capture_output=True, timeout=30)
    else:
        link.symlink_to(outside, target_is_directory=True)


@pytest.fixture(scope="module")
def plain_source(tmp_path_factory):
    root = tmp_path_factory.mktemp("plain-link-source")
    archive = subprocess.check_output(["git", "-C", str(ROOT), "archive", "--format=zip", "HEAD"])
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        bundle.extractall(root)
    for name in ("install.sh", "install.ps1"):
        shutil.copy2(ROOT / name, root / name)
    return root


@pytest.mark.parametrize("installer", ["sh", "ps1", "ps51"])
@pytest.mark.parametrize("relative", ["tools", "tools/x4_harness"])
@pytest.mark.parametrize("layout", ["checkout", "plain"])
def test_destination_directory_link_refuses_before_any_write(tmp_path, installer, relative, layout, plain_source):
    dest = _fresh(tmp_path)
    outside = tmp_path / "separate-project"
    outside.mkdir()
    sentinel = outside / "x4_upgrade_render.py"
    sentinel.write_bytes(b"separate project's private implementation")
    _directory_link(tmp_path, dest / relative, outside)
    instructions = dest / "AGENTS.md"
    instructions.write_bytes(b"existing project instructions")
    result = _install(installer, tmp_path, dest, "--agent", "generic",
                      source=plain_source if layout == "plain" else None)
    assert result.returncode != 0, _ok(result)
    assert "redirect" in (result.stdout + result.stderr).lower(), _ok(result)
    assert instructions.read_bytes() == b"existing project instructions"
    assert {p.relative_to(outside).as_posix(): p.read_bytes() for p in outside.rglob("*") if p.is_file()} == {
        "x4_upgrade_render.py": b"separate project's private implementation"}


@pytest.mark.parametrize("installer", ["sh", "ps1", "ps51"])
def test_link_outside_the_copy_set_does_not_prevent_installation(tmp_path, installer):
    dest = _fresh(tmp_path)
    outside = tmp_path / "separate-project"
    outside.mkdir()
    (outside / "private.txt").write_bytes(b"untouched")
    _directory_link(tmp_path, dest / "tools/private-not-shipped", outside)
    result = _install(installer, tmp_path, dest, "--agent", "generic")
    assert result.returncode == 0, _ok(result)
    assert (dest / "tools/agent-harness.py").is_file()
    assert {p.name: p.read_bytes() for p in outside.iterdir()} == {"private.txt": b"untouched"}
