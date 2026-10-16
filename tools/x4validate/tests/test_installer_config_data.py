"""A carried config assignment is data, including command-like text. Owned markers only."""
import pytest

from test_install_over_existing import _fresh, _install


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
