"""Actual menu observer -> Lua pages -> strict Python join, synthetic native only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from x4validate._livemenupages import MenuCapture, MenuPagesUnavailable, _tree

ROOT = Path(__file__).resolve().parents[3]
PAGER = ROOT / "mods/x4_toolkit_helper/ui/live_menupages.lua"
WIRE = ROOT / "mods/x4_toolkit_helper/ui/live_actionwire.lua"
SPEC = importlib.util.spec_from_file_location("menu_fixture_for_pages", Path(__file__).with_name("test_livemenu.py"))
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
ID = "a" * 32


def runtime(code=""):
    r = FIXTURE.build(code)
    r.globals().pagerfactory = r.execute(PAGER.read_text(encoding="utf-8"))
    r.globals().wire = r.execute(WIRE.read_text(encoding="utf-8"))()
    r.execute("observes=0;pager=pagerfactory({observe=function() observes=observes+1;return api.observe() end})")
    return r


def payload(r, value):
    return r.globals().wire.encode(value)


def capture(r, nonce=ID):
    manifest = r.globals().pager.capture(nonce)
    assert manifest["status"] == "CAPTURED", manifest["reason"]
    raw = payload(r, manifest)
    pages = []
    for i in range(manifest["pages"]):
        page = r.globals().pager.page(nonce, i)
        assert page["status"] == "PAGE", page["reason"]
        rawpage = payload(r, page)
        assert len(rawpage.encode()) <= 4096
        # Exercise the actual outer string budget too.
        outer = payload(r, r.table_from({"payload": rawpage}))
        assert json.loads(outer)["payload"] == rawpage
        pages.append(rawpage)
    return raw, pages


def finish(manifest, pages):
    reader = MenuCapture(manifest)
    for page in pages:
        reader.add(page)
    return reader.finish()


def python_table(value):
    if hasattr(value, "items"):
        return {k: python_table(v) for k, v in value.items()}
    return value


def check_existing_minimal_fixture_fits_but_valid_long_label_needs_pages():
    r = runtime()
    observed = r.globals().api.observe()
    # View/repair observations add exactly250 bytes to this unchanged purchase
    # fixture: read_only=false and two explicitly unavailable repair widgets.
    assert observed['read_only'] is False
    assert set(observed['repair_widgets'].keys()) == {'repair_tab','repair_select'}
    assert all(v['status']=='UNAVAILABLE' for v in observed['repair_widgets'].values())
    assert len(payload(r, observed).encode()) == 2031
    r.execute("nativeoptions[102][1].text=string.rep('x',4096)")
    assert len(payload(r, r.globals().api.observe()).encode()) == 6110
    manifest, pages = capture(r)
    out = finish(manifest, pages)
    assert out["capture_complete"] and out["read_complete"]
    assert out["value"] == python_table(r.globals().api.observe())
    assert out["option_counts"] == {"class": 1, "ship": 1, "loadout": 1}
    assert out["atomic"] is False and out["action_preparation_verified"] is False
    joined = b"".join(bytes.fromhex(json.loads(p)["hex"]) for p in pages)
    assert out["sha256"] == hashlib.sha256(joined).hexdigest()
    assert out["digest_scope"] == "HOST_REASSEMBLED_CANONICAL_BYTES"
    assert len(r.globals().calls) == 0


def check_complete_empty_options_are_measured_not_guessed():
    r = runtime("cells.ship.properties.options={};nativeoptions[102]={}")
    out = finish(*capture(r))
    assert out["read_complete"] and out["option_counts"]["ship"] == 0
    assert out["value"]["widgets"]["ship"]["model"]["options"] == {}


@pytest.mark.parametrize("code", ["nulltext=true", "shortrows=true", "badcount=2049"])
def check_failed_native_options_are_unknown_not_empty(code):
    r = runtime(code)
    out = finish(*capture(r))
    assert out["capture_complete"] and not out["read_complete"]
    assert out["option_counts"]["ship"] == "UNKNOWN"
    assert out["value"]["widgets"]["ship"]["status"] != "OBSERVED"


def check_utf8_empty_tables_and_mixed_integer_string_keys_preserved():
    r = runtime()
    r.globals().label = "Ã©èˆ¹ðŸ™‚" * 90
    r.execute("nativeoptions[102][1].text=label;menu.crew={[-2]='negative',[1]='integer',['1']='string',empty={}}")
    result = finish(*capture(r))["value"]
    assert result == python_table(r.globals().api.observe())
    assert result["model"]["crew"] == {-2: "negative", 1: "integer", "1": "string", "empty": {}}
    assert result["widgets"]["ship"]["model"]["options"][1]["text"] == "Ã©èˆ¹ðŸ™‚" * 90


def check_deeper_than_wire_model_is_not_silently_flattened():
    r = runtime("menu.crew={a={b={c={d={e={f={g='deep'}}}}}}}")
    with pytest.raises(Exception):
        payload(r, r.globals().api.observe())
    out = finish(*capture(r))
    assert out["value"] == python_table(r.globals().api.observe())


@pytest.mark.parametrize("change", [
    "menu.total=101", "epoch='other'", "age=9",
    "nativeoptions[102][1].text='changed'", "frame.id=78;menu.frames[2]=78",
])
def check_current_model_change_invalidates_remaining_pages(change):
    r = runtime()
    m = r.globals().pager.capture(ID)
    r.execute(change)
    page = r.globals().pager.page(ID, 0)
    assert page["status"] == "REFUSED" and page["capture_complete"] is False
    assert r.globals().pager.page(ID, 0)["status"] == "REFUSED"
    assert len(r.globals().calls) == 0


def check_model_drift_during_page_refuses_even_when_first_read_matches():
    r = runtime()
    r.execute("pager=pagerfactory({observe=function() observes=observes+1;if observes==4 then menu.total=101 end;return api.observe() end})")
    assert r.globals().pager.capture(ID)["status"] == "CAPTURED"
    assert r.globals().pager.page(ID, 0)["status"] == "REFUSED"


def check_advancing_age_does_not_mix_snapshots():
    r = runtime()
    manifest = payload(r, r.globals().pager.capture(ID))
    r.execute("age=11")
    reader = MenuCapture(manifest)
    for i in range(json.loads(manifest)["pages"]):
        reader.add(payload(r, r.globals().pager.page(ID, i)))
    out = reader.finish()
    assert out["value"]["age"] == 10
    assert r.globals().api.observe()["age"] == 11


def check_capture_supersession_and_nonce_reuse_refuse():
    r = runtime()
    assert r.globals().pager.capture(ID)["status"] == "CAPTURED"
    assert r.globals().pager.capture("b"*32)["status"] == "CAPTURED"
    assert r.globals().pager.page(ID, 0)["status"] == "REFUSED"
    assert r.globals().pager.capture(ID)["status"] == "REFUSED"


@pytest.mark.parametrize("index", [-1, 0.5, 10000, True])
def check_outside_page_refuses(index):
    r = runtime()
    assert r.globals().pager.capture(ID)["status"] == "CAPTURED"
    assert r.globals().pager.page(ID, index)["status"] == "REFUSED"


@pytest.mark.parametrize("code", [
    "menu.crew={};for i=1,7000 do menu.crew[i]=i end",
    "menu.crew={};for i=1,40 do menu.crew[i]=string.rep('x',4096) end",
    "menu.crew.self=menu.crew",
    "menu.shown=false",
])
def check_unreadable_or_overflowed_model_never_captures(code):
    r = runtime(code)
    assert r.globals().pager.capture(ID)["status"] == "REFUSED"


@pytest.mark.parametrize("field,value", [
    ("capture", "b"*32), ("offset", 1), ("index", 1), ("length", 0),
    ("total_nodes", 1), ("read_complete", False), ("hex", "GG"),
    ("pages", True), ("captured_age", float("nan")), ("revision", "other"),
])
def check_host_refuses_changed_or_malformed_page_and_cannot_recover(field, value):
    r = runtime()
    manifest, pages = capture(r)
    reader = MenuCapture(manifest)
    bad = json.loads(pages[0]);bad[field] = value
    with pytest.raises(MenuPagesUnavailable):
        reader.add(json.dumps(bad))
    with pytest.raises(MenuPagesUnavailable):
        reader.add(pages[0])


def check_host_missing_duplicate_and_out_of_order_pages_never_complete():
    manifest, pages = capture(runtime())
    missing = MenuCapture(manifest)
    missing.add(pages[0])
    with pytest.raises(MenuPagesUnavailable):
        missing.finish()
    duplicate = MenuCapture(manifest)
    duplicate.add(pages[0])
    with pytest.raises(MenuPagesUnavailable):
        duplicate.add(pages[0])
    late = MenuCapture(manifest)
    with pytest.raises(MenuPagesUnavailable):
        late.add(pages[1])


def check_host_rejects_duplicate_json_and_missing_manifest_fields():
    manifest, _ = capture(runtime())
    with pytest.raises(MenuPagesUnavailable):
        MenuCapture(manifest[:-1] + ',"version":1}')
    data = json.loads(manifest);del data["read_complete"]
    with pytest.raises(MenuPagesUnavailable):
        MenuCapture(json.dumps(data))


@pytest.mark.parametrize("field,value", [
    ("total_nodes", 1), ("total_string_bytes", 1), ("captured_age", 9),
    ("read_complete", False), ("option_counts", {"class": 0, "ship": 0, "loadout": 0}),
])
def check_header_cannot_lie_about_reassembled_source(field, value):
    manifest, pages = capture(runtime())
    header = json.loads(manifest);header[field] = value
    reader = MenuCapture(json.dumps(header))
    for raw in pages:
        page = json.loads(raw);page[field] = value
        reader.add(json.dumps(page))
    with pytest.raises(MenuPagesUnavailable):
        reader.finish()


@pytest.mark.parametrize("value", [
    {"$table": [["s", "same", 1], ["s", "same", 2]]},
    {"$table": [["s", "z", 1], ["s", "a", 2]]},
    {"$table": [["n", True, 1]]},
    {"$table": [["n", 1.5, 1]]},
    {"$table": [["s", "a", None]]},
    {"$table": [["s", "a", float("inf")]]},
    {"$table": [], "extra": True},
])
def check_malformed_typed_model_refuses(value):
    with pytest.raises(MenuPagesUnavailable):
        _tree(value)


def check_oversized_integer_age_is_normalized_refusal():
    manifest, _ = capture(runtime())
    header = json.loads(manifest);header["captured_age"] = 10**400
    with pytest.raises(MenuPagesUnavailable):
        MenuCapture(json.dumps(header))


def _cases():
    cases = []
    for name, fn in list(globals().items()):
        if not name.startswith("check_") or not callable(fn):
            continue
        params = [m for m in getattr(fn, "pytestmark", []) if m.name == "parametrize"]
        if params:
            assert len(params) == 1
            for index, args in enumerate(params[0].args[1]):
                values = [args] if len(params[0].args[0].split(",")) == 1 else list(args)
                cases.append(pytest.param(name, values, id=f"{name}-{index}"))
        else:
            cases.append(pytest.param(name, [], id=name))
    return cases


@pytest.mark.parametrize("name,args", _cases())
def test_paging_control(name, args):
    result = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), name, json.dumps(args)],
        capture_output=True, text=True, timeout=20,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "CONTROL_PASSED", result.stdout + result.stderr


if __name__ == "__main__":
    case = globals()[sys.argv[1]]
    assert sys.argv[1].startswith("check_") and callable(case)
    case(*json.loads(sys.argv[2]))
    print("CONTROL_PASSED")
