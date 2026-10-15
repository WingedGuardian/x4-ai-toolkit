import gzip
from collections import Counter

import pytest

from x4validate import _savecli


@pytest.mark.parametrize("chunk", [1, 7, 997, 1 << 22])
def test_comments_quotes_entities_and_boundaries(tmp_path, monkeypatch, chunk):
    path = tmp_path / "save.xml.gz"
    path.write_bytes(gzip.compress(b'''<savegame><!-- <component macro="phantom"/> -->
      <component macro='single'/><unit macro="double"/><item macro="a&amp;b"/>
      <connection macro="not-a-reference"/><entry macro="single"/></savegame>'''))
    monkeypatch.setattr(_savecli, "_CHUNK", chunk)
    assert _savecli.extract_refs(path) == Counter({"single": 2, "double": 1, "a&b": 1})


@pytest.mark.parametrize("body", [b'<savegame><component macro="partial"/>',
                                 b'<savegame><component macro="bad\xff"/></savegame>',
                                 b'<!DOCTYPE s [<!ENTITY x "m">]><s><item macro="&x;"/></s>'])
def test_malformed_xml_never_returns_partial_success(tmp_path, body):
    path = tmp_path / "bad.xml.gz"
    path.write_bytes(gzip.compress(body))
    with pytest.raises(_savecli.SaveUnreadable):
        _savecli.extract_refs(path)
