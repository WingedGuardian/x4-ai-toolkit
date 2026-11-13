"""Pure public capture generation/source-binding controls; no engine calls."""
from pathlib import Path
import importlib.util
import pytest

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('capture_extension_checks',ROOT/'scripts/live_capture_extension.py')
G=importlib.util.module_from_spec(spec);spec.loader.exec_module(G)

def test_exact_two_outputs_and_current_dependencies():
    outputs,receipt=G.prepare()
    assert set(outputs)=={'md/X4ToolkitCapture.xml','ui/live_capture_extension.lua'}
    assert receipt['output_sha256']=={p:G.sha(b) for p,b in outputs.items()}
    assert receipt['source_sha256']=={p:G.sha((ROOT/p).read_bytes()) for p in G.PATHS.values()}
    assert receipt['runtime_verified'] is False
    assert receipt['release']=='BOOKKEEPING_ONLY_NOT_FILE_ATTESTATION'
    assert '@REVISION@' not in outputs['ui/live_capture_extension.lua'].decode()
    assert '@MD_REVISION@' not in outputs['ui/live_capture_extension.lua'].decode()

@pytest.mark.parametrize('key',list(G.PATHS))
def test_source_drift_during_generation_refuses(monkeypatch,key):
    target=ROOT/G.PATHS[key];original=Path.read_bytes;seen=0
    def read(path):
        nonlocal seen
        raw=original(path)
        if path==target:
            seen+=1
            if seen>1:return raw+b'\n'
        return raw
    monkeypatch.setattr(Path,'read_bytes',read)
    with pytest.raises(ValueError,match='drift|changed'):G.prepare()

def test_sdk_source_change_changes_extension_revision(monkeypatch):
    _,before=G.prepare();target=ROOT/G.PATHS['sdk'];original=Path.read_bytes
    monkeypatch.setattr(Path,'read_bytes',lambda p:original(p)+(b'\n-- synthetic source change\n' if p==target else b''))
    _,after=G.prepare()
    assert before['revision']!=after['revision']
    assert before['native_revision']==after['native_revision']
