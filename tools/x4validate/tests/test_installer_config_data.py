"""A carried config assignment is data, including command-like text. Owned markers only."""
import pytest

from test_install_over_existing import _fresh, _install


@pytest.mark.parametrize("installer", ["sh", "ps1", "ps51"])
@pytest.mark.parametrize("scope", ["root", "legacy", "global"])
def test_backup_timestamp_collisions_never_replace_older_evidence(tmp_path, installer, scope):
    from datetime import datetime, timedelta
    dest = _fresh(tmp_path)
    old = b'X4_GAME="/previous-owned-fixture"\n'
    config = dest / "x4-paths.env"
    config.write_bytes(old)
    if scope == "legacy":
        config = dest / ".claude" / "x4-paths.env"
        config.parent.mkdir()
        config.write_bytes(old)
    elif scope == "global":
        skill = dest / ".claude" / "skills" / "x4-owned" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("# Owned installer fixture\n", encoding="utf-8")
        (dest / "setup.sh").write_text("exit 0\n", encoding="utf-8")
        config = tmp_path / "fake-claude-home" / "settings.json"
        config.parent.mkdir()
        old = b'{"owned_previous":true}\n'
        config.write_bytes(old)
    now = datetime.now()
    previous = {}
    # Cover the entire owned installer invocation without relying on speed or
    # replacing the platform clock. These are small private sentinel files only.
    for offset in range(-5, 180):
        p = config.with_name(config.name + ".bak-" + (now + timedelta(seconds=offset)).strftime("%Y%m%d-%H%M%S"))
        p.write_bytes(b"prior capture " + str(offset).encode())
        previous[p] = p.read_bytes()
    result = _install(installer, tmp_path, dest, "--agent", "claude" if scope == "global" else "generic",
                      method="global" if scope == "global" else "separate")
    assert result.returncode == 0, result.stdout[-2400:] + result.stderr[-2400:]
    assert {p: p.read_bytes() for p in previous} == previous
    new = [p for p in config.parent.glob(config.name + ".bak-*") if p not in previous]
    assert any(p.read_bytes() == old for p in new), "no distinct faithful backup was retained"


@pytest.mark.parametrize("installer", ["sh", "ps1", "ps51"])
@pytest.mark.parametrize("style", ["dollar", "backtick", "operator", "literal"])
def test_carried_config_text_is_never_executed_during_rewrite(tmp_path, installer, style):
    dest = _fresh(tmp_path)
    marker = tmp_path / "owned-marker.txt"
    command = "printf owned > '" + marker.as_posix() + "'"
    values = {"dollar": '"$(' + command + ')"', "backtick": '"`' + command + '`"',
              "operator": "kept; " + command, "literal": "'$(literal text)'"}
    carried = "X4_CUSTOM=" + values[style]
    config = dest / "x4-paths.env"
    config.write_text('X4_GAME="/previous-owned-fixture"\n' + carried + "\n", encoding="utf-8")
    result = _install(installer, tmp_path, dest, "--agent", "generic")
    assert result.returncode == 0, result.stdout[-2400:] + result.stderr[-2400:]
    assert not marker.exists(), "verification executed a carried config assignment"
    assert carried in config.read_text(encoding="utf-8"), "carried data was discarded rather than preserved"
    assert 'X4_GAME="/previous-owned-fixture"' not in config.read_text(encoding="utf-8")
