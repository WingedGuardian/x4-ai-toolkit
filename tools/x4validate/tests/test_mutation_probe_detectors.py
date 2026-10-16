"""Pure detector checks that still execute while artifact publication is blocked."""
from pathlib import Path

import pytest
from x4validate import _effective, _livecli, _livepipe, _registry


@pytest.mark.parametrize("dependent", [False, True], ids=["alphabetical-control", "dependency-wins"])
def test_overlay_order_honors_dependencies(tmp_path, monkeypatch, dependent):
    ext = tmp_path / "extensions"
    for folder, manifest_id, dependency in (("z_provider", "provider", ""),
            ("a_dependent", "dependent", '<dependency id="provider"/>' if dependent else "")):
        directory = ext / folder
        directory.mkdir(parents=True)
        (directory / "content.xml").write_text(
            f'<content id="{manifest_id}" name="{folder}" version="100">{dependency}</content>',
            encoding="utf-8")
    profile = tmp_path / "profile.xml"
    profile.write_text("<content/>", encoding="utf-8")
    monkeypatch.setattr(_registry, "PROFILE_CONTENT", profile)
    monkeypatch.setattr(_registry, "_reference_dlc_dirs", lambda config=None: [])
    mods = _registry.mods("installed", [ext])
    actual = [mod["folder"] for mod, path in _effective.ordered_overlays(mods)]
    assert actual == (["z_provider", "a_dependent"] if dependent else ["a_dependent", "z_provider"])


@pytest.mark.parametrize("verb", ["pause", "unpause"])
def test_write_banner_precedes_any_transport(tmp_path, monkeypatch, capsys, verb):
    calls = []
    pipe = str(tmp_path / "never-opened")
    def stop_before_transport(path, timeout):
        calls.append((path, timeout))
        raise _livepipe.LiveQueryUnavailable("owned fixture stopped before transport")
    monkeypatch.setattr(_livecli, "_live_open", stop_before_transport)
    with pytest.raises(_livepipe.LiveQueryUnavailable, match="owned fixture"):
        _livecli.cmd_write(verb, pipe, 1.0)
    captured = capsys.readouterr()
    assert calls == [(pipe, 1.0)]
    assert captured.err.count(_livecli._BANNER) == 2
    assert f"x4live {verb}" in captured.err
    assert _livecli._BANNER not in captured.out
    assert not Path(pipe).exists()
