"""Unsupported catalog payloads refuse only the paths they could override."""
import pytest
from test_geometry import World, GeometryError, pack, xmf


@pytest.mark.parametrize('overrides_binary', [False, True])
@pytest.mark.parametrize('version', [900, None])
def test_unrelated_dlc_version_catalog_does_not_hide_base_binary(tmp_path, overrides_binary, version):
    reference, game = tmp_path/'reference', tmp_path/'game'
    reference.mkdir()
    dlc = reference/'extensions/ego_dlc_test'
    dlc.mkdir(parents=True)
    installed = game/'extensions/ego_dlc_test'
    installed.mkdir(parents=True)
    pack(game, 1, xmf(), 'assets/body.xmf')
    # Reproduce the Ventures XML/Lua-only catalog without reading any payload.
    path = 'ASSETS/BODY.XMF' if overrides_binary else 'libraries/wares.xml'
    (installed/'ext_01_diff_v900.cat').write_text(f'{path} 0 0 d41d8cd98f00b204e9800998ecf8427e\n')
    class Config:
        def __init__(self): self.reference = reference
        def dlc_dirs(self): return [dlc]
    world = World(Config(), game=game)
    world.game_version = version
    if overrides_binary:
        with pytest.raises(GeometryError, match='requires qualification'):
            world.binary('assets/body.xmf')
    else:
        assert world.binary('assets/body.xmf') == xmf()
        assert world.receipts[-1]['binary_root'] == str(game)
    assert len(world.scope['unqualified_version_catalogs']) == 1
