"""Engine-rejected MD constructs must not appear in the GENERATED helper (BLIND-SPOTS F370).

Neither x4validate nor X4CodeSense models these two rules, so this test scans the
composed helper output itself: every md/*.xml the generators emit, with every public
extension, under both capacity profiles.

1. A unit literal ending in ``mps`` does not exist (engine: "Operator expected" right
   after ``1mps``). MEASURED: 0 of 399 vanilla md/aiscripts files use it; vanilla writes
   speeds in ``m`` (``speed lt 5m``).
2. A cue whose ``<conditions>`` hold no event condition must carry ``onfail`` or
   ``checkinterval``. The engine rejected a check_value-only sub-cue ("Found non-event
   condition 'check_value', event condition required!"). MEASURED 2026-11-13 over the
   vanilla+DLC reference md (382 files): 2,648 of 2,648 cues with event-less conditions
   carry onfail or checkinterval; none carries neither.
"""
import importlib.util
import re
from pathlib import Path

import pytest
from lxml import etree as E

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("helper_engine_rules", ROOT / "scripts/prepare_live_helper.py")
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

MPS = re.compile(r"\d(?:\.\d+)?mps\b")


def _compose(profile):
    mod = ROOT / "mods/x4_toolkit_helper"
    sources = {key: (ROOT / path).read_bytes() for key, path in P.BUNDLE.SOURCES.items()}
    files, _ = P.build(mod.joinpath("content.xml").read_bytes(), mod.joinpath("ui.xml").read_bytes(),
                       sources, extensions=tuple(P.EXTENSIONS), capacity_profile=profile)
    return files


def eventless_cues(data: bytes):
    """Cues whose conditions hold no event_* element and that carry neither onfail nor checkinterval."""
    root = E.fromstring(data)
    bad = []
    for cue in root.iter("cue"):
        cond = cue.find("conditions")
        if cond is None:
            continue
        if any(isinstance(el.tag, str) and el.tag.startswith("event_") for el in cond.iter()):
            continue
        if cue.get("onfail") or cue.get("checkinterval"):
            continue
        bad.append(cue.get("name"))
    return bad


@pytest.fixture(scope="module", params=["interactive", "campaign_v1"])
def md_files(request):
    files = _compose(request.param)
    md = {path: data for path, data in files.items() if path.startswith("md/")}
    # Denominator: every MD file the ten public extensions plus the session emit.
    assert len(md) == 9, sorted(md)
    return md


def test_no_mps_unit_literal_in_generated_md(md_files):
    hits = {path: MPS.findall(data.decode("utf-8")) for path, data in md_files.items()}
    assert {path: found for path, found in hits.items() if found} == {}


def test_no_eventless_condition_without_onfail_or_checkinterval(md_files):
    hits = {path: eventless_cues(data) for path, data in md_files.items()}
    assert {path: names for path, names in hits.items() if names} == {}


def test_generated_md_still_contains_conditioned_cues(md_files):
    # Twin: the scan reaches real cues (an empty scan would pass the ban vacuously).
    total = sum(len(E.fromstring(data).xpath("//cue[conditions]")) for data in md_files.values())
    assert total > 0


@pytest.mark.parametrize("snippet,expected", [
    (b"<mdscript><cues><cue name='A'><conditions><check_value value='1'/></conditions></cue></cues></mdscript>", ["A"]),
    (b"<mdscript><cues><cue name='A' onfail='cancel'><conditions><check_value value='1'/></conditions></cue></cues></mdscript>", []),
    (b"<mdscript><cues><cue name='A' checkinterval='1s'><conditions><check_value value='1'/></conditions></cue></cues></mdscript>", []),
    (b"<mdscript><cues><cue name='A'><conditions><event_cue_signalled/><check_value value='1'/></conditions></cue></cues></mdscript>", []),
    (b"<mdscript><cues><cue name='A'><conditions><check_any><event_cue_signalled/></check_any></conditions></cue></cues></mdscript>", []),
    (b"<mdscript><cues><cue name='A'><delay exact='1s'/></cue></cues></mdscript>", []),
])
def test_eventless_rule_control(snippet, expected):
    # Control: the checker itself goes red on the rejected shape and stays green on the vanilla shapes.
    assert eventless_cues(snippet) == expected


@pytest.mark.parametrize("text,expected", [("speed le 1mps", ["1mps"]), ("speed le 0.5mps and", ["0.5mps"]),
                                           ("speed le 1m", []), ("$speed_mps", []), ("max_speed_mps=1", [])])
def test_mps_rule_control(text, expected):
    assert MPS.findall(text) == expected


def test_ui_addon_declares_its_mod_support_apis_load_order_dependency():
    # MEASURED in game (KB 2026-11-10 item 7): without a declared UI dependency, another addon
    # reordered the UI load and the pipe half's `require` of Mod Support APIs failed
    # ("module ... not found", "Register_OnLoad_Init absent"). The deployed helper carried the
    # dependency only as a hand edit (b13308ee); the source and the composition must carry it.
    source = E.fromstring((ROOT / "mods/x4_toolkit_helper/ui.xml").read_bytes())
    composed = E.fromstring(_compose("interactive")["ui.xml"])
    for root in (source, composed):
        names = [node.get("name") for node in root.findall("environment/dependency")]
        assert names == ["sn_mod_support_apis", "ego_detailmonitorHelper"], names


def test_manifest_requires_mod_support_apis_like_the_ui_addon():
    # Review r2-3: content.xml marked the dependency optional while ui.xml requires it.
    manifest = E.fromstring((ROOT / "mods/x4_toolkit_helper/content.xml").read_bytes())
    deps = {node.get("id"): node.get("optional") for node in manifest.findall("dependency") if node.get("id")}
    assert "ws_2042901274" in deps and deps["ws_2042901274"] in (None, "false", "0"), deps
