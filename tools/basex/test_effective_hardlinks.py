"""Reference staging shares immutable bytes and refuses unsupported links."""
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools/x4validate'))


@pytest.mark.parametrize('refused', [False, True])
def test_untouched_reference_is_linked_or_explicitly_unstaged(tmp_path, monkeypatch, refused):
    spec = importlib.util.spec_from_file_location('effective_link_test', Path(__file__).with_name('build-effective.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    reference, out = tmp_path/'reference', tmp_path/'output/tree'
    source = reference/'libraries/control.xml'
    source.parent.mkdir(parents=True)
    original = b'<wares><ware id="control"/></wares>'
    source.write_bytes(original)
    def config(reference=None, overlays=()):
        return SimpleNamespace(reference=Path(reference) if reference else source.parents[1],
                               overlays=overlays, dlc_dirs=lambda: [], packed_dlc_names=lambda: set())
    monkeypatch.setattr(mod._merge, 'Config', config)
    monkeypatch.setattr(mod._effective, 'require_base_documents', lambda *a: None)
    monkeypatch.setattr(mod, 'installed_in_load_order', lambda *a: [])
    monkeypatch.setattr(mod._modfiles, 'overlay_vpaths', lambda *a: (set(), {'libraries/control.xml'}, {'libraries/control.xml': 'base'}))
    monkeypatch.setattr(mod, 'recycle_or_raise', lambda paths: None if not out.exists() else pytest.fail('test output unexpectedly exists'))
    if refused:
        def no_link(*args):
            raise OSError('cross-volume link refused')
        monkeypatch.setattr(mod.os, 'link', no_link)
    assert mod.main(['--out', str(out), '--reference', str(reference)]) == (3 if refused else 0)
    result = json.loads((out.parent/'effective-manifest.json').read_text())
    assert source.read_bytes() == original
    if refused:
        assert not (out/'libraries/control.xml').exists()
        assert result['counts']['documents_total'] == 0
        assert any('cross-volume link refused' in f for f in result['failures'])
    else:
        assert os.path.samefile(source, out/'libraries/control.xml')
        assert result['counts']['untouched_hardlinked'] == 1
        assert result['counts']['untouched_materialized'] == 0
