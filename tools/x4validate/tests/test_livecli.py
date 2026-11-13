"""`x4live`'s EXIT CODES are the contract, and each one must be reachable.

The tool exists to stop "I could not ask" from looking like "there is no disagreement",
so the mapping is the feature: **2** = non-answer, **3** = degraded/malformed, **1** =
a real finding, **0** = clean. A test suite that only proves the happy path would leave
the entire point of the tool unverified.

`extensions` and `oracle` read the registry and the effective store, so they are covered
by `gates/qa_sweep.py` against the real installation rather than mocked here. What is
tested here is everything that can be decided from a dump alone.
"""
from __future__ import annotations

import pytest

from x4validate import _livecli as C
from x4validate import _livedump as L
from test_livedump import good_rows, uidata


def write(tmp_path, text, name="uidata.xml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# ------------------------------------------------------------ exit-code contract

def test_missing_file_is_rc2_not_rc0(tmp_path):
    assert C.main(["--file", str(tmp_path / "absent.xml"), "dump"]) == 2


def test_a_file_that_is_not_uidata_is_rc2(tmp_path):
    assert C.main(["--file", write(tmp_path, "[project]\n"), "dump"]) == 2


def test_the_running_game_stub_is_rc2_never_an_empty_report(tmp_path):
    """The single most dangerous input: valid XML that parses to zero of everything."""
    stub = '<?xml version="1.0" encoding="UTF-8"?>\n<uidata version="1"/>\n'
    assert C.main(["--file", write(tmp_path, stub), "dump"]) == 2


def test_a_malformed_dump_is_rc3_not_rc2(tmp_path):
    """Degraded and unavailable are different states and get different codes."""
    bad = uidata([["HDR", "schema=2"], ["EXT", "m"], ["END", "999"]])
    assert C.main(["--file", write(tmp_path, bad), "dump"]) == 3


def test_a_probe_that_DIED_is_rc3_not_rc0(tmp_path):
    """rc 0 means "checked and clean". A probe that ran and failed recorded why, and
    reporting that as a clean capture is the could-not-check-as-clean-result class."""
    fatal = [["HDR", "schema=2", "FATAL", "attempt to index a nil value"]]
    assert C.main(["--file", write(tmp_path, uidata(fatal)), "dump"]) == 3


def test_the_FATAL_message_is_the_PROBE_s_account_not_a_transport_guess(tmp_path, capsys):
    """A FATAL frame is a single HDR row by construction, so the truncation clause would
    otherwise claim it and print "the true length is unknown" -- a guess about the
    transport, over the probe's own record of what went wrong. Ordering is the fix, and
    this is what pins it."""
    fatal = [["HDR", "schema=2", "FATAL", "attempt to index a nil value"]]
    C.main(["--file", write(tmp_path, uidata(fatal)), "dump"])
    err = capsys.readouterr().err
    assert "attempt to index a nil value" in err, err
    assert "the probe FAILED" in err, err
    assert "TRUNCATED" not in err, err


def test_a_TRUNCATED_dump_is_also_rc3_but_says_something_ELSE(tmp_path, capsys):
    """The twin. Both are rc 3, so the exit code cannot tell them apart and the MESSAGE
    has to -- one says re-reading will not help, the other says the read was short."""
    rows = good_rows(3)
    truncated = rows[:3]
    C.main(["--file", write(tmp_path, uidata(truncated)), "dump"])
    err = capsys.readouterr().err
    assert "TRUNCATED" in err, err
    assert "the probe FAILED" not in err, err


def test_a_valid_dump_is_rc0(tmp_path):
    assert C.main(["--file", write(tmp_path, uidata(good_rows(2))), "dump"]) == 0


@pytest.mark.parametrize("cmd", ["dump", "errors"])
def test_every_subcommand_maps_a_bad_dump_to_rc3(tmp_path, cmd):
    """The mapping lives in main(), so it must hold for each subcommand, not just one."""
    bad = uidata([["HDR", "s=1"], ["EXT", "m"], ["END", "42"]])
    assert C.main(["--file", write(tmp_path, bad), cmd]) == 3


# --------------------------------------------------------------- what it discloses

def test_dump_prints_the_kind_census_and_states_no_remainder(tmp_path, capsys):
    rc = C.main(["--file", write(tmp_path, uidata(good_rows(3))), "dump"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "TOTAL" in out and "no unexplained remainder" in out
    assert "SCOPE" in out, "must say what it did NOT capture"


def test_errors_without_an_ERR_STATUS_row_is_rc3_not_an_empty_log(tmp_path):
    """No ERR_STATUS means the probe never asked. That is not 'zero errors'."""
    rows = [["HDR", "s=1"], ["EXT", "m"], ["END", "3"]]
    assert C.main(["--file", write(tmp_path, uidata(rows)), "errors"]) == 3


def test_errors_discloses_the_cap_and_the_true_total(tmp_path, capsys):
    """A capped list that hides its denominator is the narrowing step we refuse."""
    rows = [["HDR", "s=1"],
            ["ERR_STATUS", "OK", "5524", "cap=400", "emitting=400"],
            ["ERR", "5524", "2", "1787876497", "something went wrong"],
            ["END", "4"]]
    rc = C.main(["--file", write(tmp_path, uidata(rows)), "errors"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "5524" in out and "cap=400" in out
    assert "SAMPLE" in out, "a capped list must say it is a sample"


def test_a_probe_that_could_not_read_the_log_is_rc3(tmp_path):
    rows = [["HDR", "s=1"], ["ERR_STATUS", "ABSENT"], ["END", "3"]]
    assert C.main(["--file", write(tmp_path, uidata(rows)), "errors"]) == 3


# ------------------------------------------------------- the float32 comparison rule

@pytest.mark.parametrize("engine, store, same", [
    ("4.8000001907349", "4.8", True),      # float32 widened to double
    ("3.9000000953674", "3.9", True),
    ("2500", "2500", True),
    ("2500", "99999", False),
    ("fight", "fight", True),
    ("fight", "trade", False),
    ("6.3429999351501", "6.343", True),
])
def test_values_compare_at_float32_precision_not_as_strings(engine, store, same):
    """A string compare would flag EVERY float and bury a real disagreement."""
    assert C._agree(engine, store) is same


def test_the_mapping_table_is_not_empty():
    """Guard the guard: an empty table would make `oracle` compare nothing and pass."""
    assert len(C._BY_TYPE) >= 5
    assert all(m for m in C._BY_TYPE.values()), "a library type maps nothing"
    assert C._mapping_for("shiptypes_s", "hull") == ("hull.max", "identity")


def test_a_field_name_does_not_determine_its_meaning():
    """The reason the table is keyed by LIBRARY TYPE and not by field alone.

    `shield` on a shield generator is that generator's own capacity. `shield` on a
    SHIP is the loadout-derived total, which our store does not model. A flat map
    would apply one meaning to the other -- #18's category error in a lookup table.
    """
    assert C._mapping_for("shieldgentypes", "shield") == ("recharge.max", "identity")
    assert C._mapping_for("shiptypes_s", "shield") is None, (
        "a ship's `shield` must NOT map to a generator's recharge.max")
    # `shield` above is the type-specificity example. `hull` is NOT one: MEASURED
    # 2026-09-19 (P7 discriminating harvest), engines/shields/turrets/missiles all report a
    # real hull that equals hull.max (engine hull VARIES 4033/16180/25330), which refutes
    # the earlier "equipment hull is a flat 1000 matching nothing" note. So hull is
    # type-UNIVERSAL, and a type with no hull mapping (weapons_lasers) still returns None.
    assert C._mapping_for("shiptypes_s", "hull") == ("hull.max", "identity")
    assert C._mapping_for("enginetypes", "hull") == ("hull.max", "identity")
    assert C._mapping_for("shieldgentypes", "hull") == ("hull.max", "identity")
    assert C._mapping_for("weapons_lasers", "hull") is None


def test_P7_adopted_mappings_2026_09_19():
    """The 2026-09-19 discriminating-harvest adoptions, each MEASURED unambiguous. Reject
    rows are asserted ABSENT so a future re-adoption of a known coincidence fails here."""
    adopted = {
        ("weapons_turrets", "hull"): ("hull.max", "identity"),           # n=11 nd=11
        # CORRECTED 2026-09-20 (release review): the engine totals a SALVO while the
        # store holds ONE warhead, so identity reported a false disagreement on the only
        # amount>1 missile in the fixture -- engine 1680, store 210, missile.amount 8.
        ("missiletypes", "explosiondamage"): ("explosiondamage.value", "per_salvo"),  # n=4
        ("enginetypes", "thrust_forward"): ("thrust.forward", "identity"),            # n=3
        ("enginetypes", "thrust_reverse"): ("thrust.reverse", "identity"),            # n=3
        ("enginetypes", "boost_accfactor"): ("boost.acceleration", "identity"),       # n=2
        ("missiletypes", "shieldexplosiondamage"): ("explosiondamage.shield", "identity"),  # n=1
    }
    for (lt, f), want in adopted.items():
        assert C._mapping_for(lt, f) == want, f"{lt}.{f} not adopted as {want}"
    # coincidence rejections (value collisions, field name unrelated to prop) stay OUT
    for lt, f in [("weapons_turrets", "coolingrate"), ("weapons_turrets", "shielddisruption"),
                  ("weapons_lasers", "shielddisruption"), ("weapons_lasers", "shieldonlydamage"),
                  ("weapons_lasers", "surfaceelementmultiplier")]:
        assert C._mapping_for(lt, f) is None, f"{lt}.{f} is a known coincidence -- must not map"


def test_an_unknown_library_type_maps_nothing_rather_than_guessing():
    assert C._mapping_for("no_such_type", "hull") is None


def test_rotational_thrust_declares_the_radians_transform():
    """MEASURED: engine 3.8397243022919 rad == store 220 deg, on three axes."""
    for f in ("thrust_pitch", "thrust_yaw", "thrust_roll"):
        prop, tname = C._mapping_for("thrustertypes", f)
        assert tname == "degrees", f"{f} must declare the unit transform"
    assert C._TRANSFORMS["degrees"](3.8397243022919) == pytest.approx(220.0, abs=1e-4)
    assert C._TRANSFORMS["degrees"](4.1887903213501) == pytest.approx(240.0, abs=1e-4)
    assert C._TRANSFORMS["identity"](7.5) == 7.5


def test_derived_fields_are_named_not_folded_into_unmapped():
    """A known modelling gap must not hide inside a generic bucket (F72)."""
    # `storagecapacity` was in this list until 2026-08-30. It now has a MEASURED
    # traversal (_DERIVE, 5 of 5 exact across the fixture), so asserting it is
    # uncomputable would assert something false. `shipstoragecapacity` replaces it and
    # is still genuinely underived -- see P5.
    #
    # `dps` and its four per-channel siblings left the same way on 2026-09-20, on the
    # same warrant and a higher bar: 38 of 39 exact against a live engine harvest. See
    # tests/test_dps_derivation.py. `sustaineddps` STAYS -- it folds in the heat model,
    # which the shot-rate formula does not reproduce, so it is still a real gap and is
    # the example this test now leans on.
    for f in ("shipstoragecapacity", "docks_m", "launchtubes_s", "sustaineddps"):
        assert f in C._DERIVED
    # and a derived field must never also carry a direct mapping
    for ltype, m in C._BY_TYPE.items():
        clash = set(m) & C._DERIVED
        assert not clash, f"{ltype} maps {clash}, which is also declared DERIVED"


def test_a_field_is_never_claimed_by_BOTH_registries():
    """The invariant behind both promotions, asserted once instead of per-field.

    A field cannot be both "we cannot compute this" (`_DERIVED`) and "here is how we
    compute it" (`_DERIVE`). Whichever list is consulted second would be dead, and which
    one that is depends on `cmd_oracle`'s branch order rather than on anything declared --
    so the contradiction would present as a silently wrong BUCKET, not as an error.
    """
    clash = set(C._DERIVE) & set(C._DERIVED)
    assert not clash, f"{clash} is claimed by both _DERIVE and _DERIVED"


@pytest.mark.parametrize("value, props, degenerate", [
    ("0", {"a": "0"}, True),                       # zero matches any zero
    ("1", {"a": "1"}, True),                       # so does one
    ("", {"a": ""}, True),
    ("2500", {"a": "2500"}, False),                # informative
    ("2500", {"a": "2500", "b": "2500", "c": "2500"}, True),  # shared by 3+ props
])
def test_degeneracy_rule(value, props, degenerate):
    """Value-matching alone invents mappings; this is what stops it."""
    assert C._is_degenerate(value, props) is degenerate


def test_a_wrong_unit_transform_is_CAUGHT_and_NAMED(tmp_path, capsys, monkeypatch):
    r"""The falsification twin for 5a: a declared transform must be able to go RED.

    If the transform were inferred at comparison time -- picking whichever of
    identity/degrees makes the values agree -- this check could never fail, which
    is the shape CLAUDE.md #26 exists to refuse. So we plant a RAW radian value
    against a degrees-mapped field and require BOTH that it is reported as a
    disagreement AND that it is labelled TRANSFORM-SUSPECT, because the raw value
    would have agreed.
    """
    rows = [["HDR", "schema=2"],
            # engine reports radians; the store holds 220 degrees. 220 raw would
            # only agree if the transform were being skipped.
            ["LIB_ENTRY_VAL", "thrustertypes", "m_fake", "thrust_pitch", "220"],
            ["END", "3"]]
    monkeypatch.setattr(C, "_store_props", lambda con, macro: {"thrust.pitch": "220"})
    monkeypatch.setattr(C, "_load", lambda p: L.parse(uidata(rows)))

    class _Fresh:
        fresh = True
    monkeypatch.setattr("x4validate._effective.store_freshness", lambda con: _Fresh())
    monkeypatch.setattr("x4validate._effective._connect", lambda db: _StubCon())
    monkeypatch.setattr("x4validate._effective.effective_db",
                        lambda: __import__("pathlib").Path(__file__))

    rc = C.cmd_oracle(None)
    out = capsys.readouterr().out
    assert rc == 1, "a wrong transform must be a FINDING, not a pass"
    assert "1 DISAGREE" in out
    assert "TRANSFORM-SUSPECT" in out, (
        "the raw value agreed, so the output must name the transform as the likely "
        "cause instead of silently correcting it")


def _fake_store(monkeypatch, props):
    """Point cmd_oracle at an in-memory store that is unconditionally FRESH."""
    import pathlib

    class _Fresh:
        fresh = True
    monkeypatch.setattr(C, "_store_props", lambda con, macro: props)
    monkeypatch.setattr("x4validate._effective.store_freshness", lambda con: _Fresh())
    monkeypatch.setattr("x4validate._effective._connect", lambda db: _StubCon())
    monkeypatch.setattr("x4validate._effective.effective_db",
                        lambda: pathlib.Path(__file__))


def test_engine_DERIVED_fields_are_counted_and_listed_separately(capsys, monkeypatch):
    """F72's gap must be NAMED, not folded into a generic "unmapped" bucket.

    Folding them together makes a known modelling gap look like a lookup table
    that merely needs more entries -- which is how a real gap stays invisible.
    """
    # ⚠ The example was `dps` until 2026-09-20, when `dps` GAINED a traversal and stopped
    # being a gap. `sustaineddps` is the replacement: still genuinely underived, because
    # it folds in the heat model.
    rows = [["HDR", "schema=2"],
            ["LIB_ENTRY_VAL", "weapons_lasers", "w", "sustaineddps", "290.9"],
            ["LIB_ENTRY_VAL", "weapons_lasers", "w", "zzz_not_a_field", "1"],
            ["END", "4"]]
    monkeypatch.setattr(C, "_load", lambda p: L.parse(uidata(rows)))
    _fake_store(monkeypatch, {"heat.coolrate": "2000"})

    rc = C.cmd_oracle(None, show_derived=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "engine-DERIVED (F72)    1" in out, "sustaineddps must land in the DERIVED bucket"
    assert "not mapped yet          1" in out, "an unknown field is NOT 'derived'"
    assert "290.9" in out, "--show-derived must print the engine's ground-truth value"


def test_a_traversal_that_REFUSES_gets_its_own_bucket_not_the_unmapped_one(capsys, monkeypatch):
    """★ A THIRD STATE, added 2026-09-20 with the `dps` traversal and MEASURED on the real
    harvest before it was written: promoting a field silently moved 30 rows -- the six
    decorative `*_video_macro` entries, which carry no bullet at all, x five channels --
    out of the NAMED derived bucket and into the generic `not mapped yet` one.

    That is exactly the hiding `_DERIVED` exists to prevent, arriving by the back door the
    moment a field was promoted: the rows looked like a lookup table needing more entries,
    when in fact a traversal had looked at them and declined. "I could not compute this" is
    not "nobody has mapped this".

    ⚠ RE-POINTED 2026-09-20 from `dps` to `storagecapacity`. `dps` was held out of
    `_DERIVE` for v3.2.0 (see `test_dps_is_HELD_OUT_of_the_derived_registry_for_v3_2_0`),
    so it no longer has a traversal that can decline. The BUCKET is what this test is
    about, not which field happens to exercise it: `_derive_storagecapacity` refuses the
    same way, returning None when it sees no connections. The rationale above is the
    original one and still stands.
    """
    rows = [["HDR", "schema=2"],
            ["LIB_ENTRY_VAL", "shiptypes_s", "w", "storagecapacity", "540"],
            ["LIB_ENTRY_VAL", "shiptypes_s", "w", "zzz_not_a_field", "1"],
            ["END", "4"]]
    monkeypatch.setattr(C, "_load", lambda p: L.parse(uidata(rows)))
    # no connections, so the storagecapacity traversal looks and refuses
    _fake_store(monkeypatch, {"heat.coolrate": "2000"})

    rc = C.cmd_oracle(None)
    out = capsys.readouterr().out
    assert rc == 0
    assert "derivation REFUSED      1" in out, (
        "a traversal that declined must be named, not folded into 'not mapped yet'")
    assert "not mapped yet          1" in out, (
        "and the genuinely unmapped field must STILL be counted there -- the twin that "
        "stops the new bucket swallowing everything")


def test_show_derived_is_off_by_default(capsys, monkeypatch):
    rows = [["HDR", "schema=2"],
            ["LIB_ENTRY_VAL", "weapons_lasers", "w", "dps", "290.9"],
            ["END", "3"]]
    monkeypatch.setattr(C, "_load", lambda p: L.parse(uidata(rows)))
    _fake_store(monkeypatch, {"heat.coolrate": "2000"})
    C.cmd_oracle(None)
    assert "ground truth" not in capsys.readouterr().out


def test_module_exposes_no_argparse_at_import_time():
    """F69: the CLI must stay out of the freshness-hashed engine sources.

    `_livecli` is the CLI and `_livedump` is the library; the split only means
    something if the library half never grows a main().
    """
    assert not hasattr(L, "main")
    assert "argparse" not in dir(L)


# --------------------------------------------------------------------------- #
# the live half: a refusal must not look like an empty result
# --------------------------------------------------------------------------- #

def test_a_ramp_that_never_connects_prints_NO_TABLE_HEADER():
    """A header above zero rows reads as "zero results"; the truth is "never ran".

    That is the same narrowing-step-reports-success shape the whole toolkit is built
    against, so the header is printed only after the game connects. Without this test
    the ordering is a comment, not a contract.
    """
    import io

    from x4validate import _livecli, _livepipe

    def refuse(pipe, timeout):
        raise _livepipe.LiveQueryUnavailable("nothing connected")

    buf = io.StringIO()
    orig = _livecli._live_open
    _livecli._live_open = refuse
    try:
        with pytest.raises(_livepipe.LiveQueryUnavailable):
            _livecli.cmd_ramp(None, 1.0, out=buf)
    finally:
        _livecli._live_open = orig
    assert buf.getvalue() == "", f"printed a header with no table: {buf.getvalue()!r}"


def test_the_ramp_spans_BOTH_candidate_size_limits():
    """The cap is unknown: an unsourced 2047 from the winpipe DLL, and python's
    64 KB buffer. A ramp that stopped below either would report its OWN limit as the
    finding -- so it must straddle both, with a step either side.
    """
    from x4validate._livecli import RAMP_SIZES

    assert min(RAMP_SIZES) < 2047 < max(RAMP_SIZES)
    assert any(n < 2047 for n in RAMP_SIZES) and any(n > 2047 for n in RAMP_SIZES)
    # Was `>= 65536` until 2026-08-29. That premise was WRONG: our own read buffer is
    # 64 KiB, so a 65536-byte payload cannot round-trip through this module at all and
    # the ramp reported its OWN limit as the transport's cap. The honest requirement is
    # that it reaches the 64 KiB neighbourhood -- see
    # test_the_ramp_cannot_probe_past_our_own_buffer for the hard bound.
    assert max(RAMP_SIZES) >= 60000


def test_the_ramp_cannot_probe_past_our_own_buffer():
    """A ramp size at or above our own read buffer measures US, not the game.

    MEASURED 2026-08-29: with a top size of 65536 and a ~24-byte frame header, the
    message is 65560 bytes against a 65536-byte buffer, so the largest size always
    "failed" and the ramp reported `the ceiling lies in (60000, 65536]`. That is this
    module's buffer presented as the transport's cap -- and it was one live run away
    from being written into F74 as an engine measurement.
    """
    from x4validate._livecli import RAMP_SIZES
    from x4validate._livepipe import _BUF

    assert max(RAMP_SIZES) + 64 < _BUF, (
        f"ramp top {max(RAMP_SIZES)} + header is not safely below our {_BUF}-byte "
        f"buffer; the ramp would measure itself")


# --- F72: the groundtruth TSV as an oracle/mappings input --------------------- #
#
# The whole reason F72 sat open for three days: `cmd_mappings` AND `cmd_oracle` both read
# a uidata dump, which needs the engine-probe mod deployed and the game CLOSED. With that
# mod removed, `x4live oracle` exits 2 with "the probe did not run" and NO comparison can
# be made at all. `groundtruth` writes the identical data over the live pipe. It was a
# file-format mismatch, not modelling work.

TAB, NL = chr(9), chr(10)


class _StubCon:
    """A stand-in store connection. cmd_oracle/cmd_mappings now CLOSE the connection they
    open (v4.0.0 delta review), so a stub must be closable -- and records that it was."""
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def _gt(tmp_path, rows):
    p = tmp_path / "gt.tsv"
    p.write_text("# header comment" + NL + "librarytype\tmacro\tfield\tengine_value" + NL
                 + NL.join(rows) + NL, encoding="utf-8")
    return p


def _mappings_out(tmp_path, monkeypatch, rows, store):
    """cmd_mappings over a groundtruth TSV and a stubbed store."""
    import io

    _Con = _StubCon

    # Stub the REAL preconditions, and with raising=True (the default) so a rename cannot
    # make the patch a silent no-op. The first version used `_store_exists`, which does not
    # exist, with raising=False: it passed here only because this machine HAS an effective
    # store, and verify-cold caught it in a cold tree where cmd_mappings refused rc 2.
    # cmd_mappings imports these INSIDE the function, so the patch has to land on
    # _effective itself, not on the _livecli namespace.
    from x4validate import _effective as E

    store_path = tmp_path / 'effective.sqlite3'
    store_path.write_bytes(b'')
    monkeypatch.setattr(E, 'effective_db', lambda: store_path)
    monkeypatch.setattr(E, '_connect', lambda db: _Con())
    monkeypatch.setattr(E, 'store_freshness',
                        lambda con: type('F', (), {'fresh': True, 'banner': lambda self, w: ''})())
    monkeypatch.setattr(C, '_store_props', lambda con, macro: store.get(macro))
    buf = io.StringIO()
    rc = C.cmd_mappings(None, out=buf, groundtruth=str(_gt(tmp_path, rows)))
    return rc, buf.getvalue()


def test_a_candidate_that_DISAGREES_anywhere_is_not_proposed(tmp_path, monkeypatch):
    """The soundness rule this function's docstring promises -- 'a candidate must agree on
    EVERY macro carrying both the field and the prop; one disagreement disqualifies it
    outright' -- was never implemented: the loop appended True on agreement and recorded
    nothing on disagreement, so no candidate could ever be removed. MEASURED 2026-09-20:
    `missiletypes explosiondamage` was still offered while disagreeing with the store on a
    salvo missile in the same file, and that unearned warrant is what the adoption comments
    cite for eight mappings."""
    rows = ['t	m1	foo	100', 't	m2	foo	200']
    store = {'m1': {'bar.value': '100'}, 'm2': {'bar.value': '999'}}
    rc, out = _mappings_out(tmp_path, monkeypatch, rows, store)
    assert rc == 0, out
    assert 'bar.value' not in out, out


def test_TWIN_a_candidate_that_agrees_EVERYWHERE_is_still_proposed(tmp_path, monkeypatch):
    """The over-firing direction: the rule must not remove a sound candidate."""
    rows = ['t	m1	foo	100', 't	m2	foo	200']
    store = {'m1': {'bar.value': '100'}, 'm2': {'bar.value': '200'}}
    rc, out = _mappings_out(tmp_path, monkeypatch, rows, store)
    # rc 1 == 'there are proposals', which is the point here; rc 2 would be a refusal.
    assert rc == 1, out
    assert 'bar.value' in out, out


def test_a_field_whose_EVERY_candidate_disagrees_does_not_crash(tmp_path, monkeypatch):
    """A key DISQUALIFIED with nothing informative: the default for a missing informative
    entry was a tuple, and `tuple - set` raised TypeError. Latent since before 2026-09-24;
    gates/qa_sweep's `x4live mappings` cell hit it on the real groundtruth once RT-5 parsed
    its rows correctly."""
    # The key is only iterated when SOMETHING agreed (it has votes). The crash needs that
    # agreement to be DEGENERATE (a 0 -- not informative) while another prop DISAGREES.
    rows = ['t	m1	foo	0']
    store = {'m1': {'bar.value': '0', 'baz.value': '5'}}
    rc, out = _mappings_out(tmp_path, monkeypatch, rows, store)
    assert rc in (0, 1), out
    assert 'baz.value' not in out, out


def test_groundtruth_reader_takes_BOTH_row_shapes(tmp_path):
    """A per-field row carries one field; a `*` row carries the engine's ALL-FIELDS reply
    tab-joined inside column 4. A naive 4-way split drops every `*` row -- it dropped 15
    of 15 while this was being written."""
    star = "shiptypes_s\tm1\t*\thull=2500" + TAB + "mass=6.34"
    per = "shiptypes_s\tm1\tspeed\t120"
    entries, st = C._entries_from_groundtruth(_gt(tmp_path, [star, per]))
    assert entries[("shiptypes_s", "m1")] == {"hull": "2500", "mass": "6.34",
                                              "speed": "120"}
    assert st["star_rows"] == 1 and st["field_rows"] == 1 and st["unparseable"] == 0


def test_a_PER_FIELD_row_WINS_over_the_same_cell_in_a_star_row(tmp_path):
    """The all-fields reply is one flattened string, so a value containing `=` or a tab is
    ambiguous inside it. The dedicated row is the more precise record of the same cell."""
    rows = ["shiptypes_s\tm1\t*\thull=1", "shiptypes_s\tm1\thull\t2500"]
    entries, _ = C._entries_from_groundtruth(_gt(tmp_path, rows))
    assert entries[("shiptypes_s", "m1")]["hull"] == "2500"


def test_unparseable_lines_are_COUNTED_not_silently_dropped(tmp_path):
    """A fixture that quietly loses rows reads downstream as 'the engine does not report
    that field'. Older fixtures predate the escaping fix and DO contain such lines --
    MEASURED: 4 of 104 in groundtruth-20260829."""
    rows = ["shiptypes_s\tm1\thull\t2500", "this line has no tabs at all"]
    entries, st = C._entries_from_groundtruth(_gt(tmp_path, rows))
    assert st["unparseable"] == 1 and st["parsed"] == 1
    assert entries[("shiptypes_s", "m1")]["hull"] == "2500"


def test_the_groundtruth_WRITER_escapes_tabs_and_newlines(tmp_path):
    """The writer joined rows with tabs and values could CONTAIN tabs -- a description
    does, and a `*` payload is itself tab-joined. Unescaped, the row structure breaks.
    Same defect `harvest` had; this is the second location."""
    # The backslash is the point. Without it this fixture cannot detect the
    # sequential-replace defect: MEASURED 2026-09-01, the old implementation
    # round-tripped "a<TAB>b<NL>c" correctly and mangled every Windows path.
    nasty = "C:" + chr(92) + "temp" + chr(92) + "x " + TAB + " " + NL + " end"
    escaped = (nasty.replace(chr(92), chr(92) * 2)
                    .replace(TAB, chr(92) + "t")
                    .replace(NL, chr(92) + "n"))
    assert TAB not in escaped and NL not in escaped
    assert C._unescape(escaped) == nasty, "the round trip must be lossless"


def test_unescape_is_a_NO_OP_on_a_fixture_written_before_the_fix():
    """Older fixtures contain no escapes, so reversing them must not corrupt anything."""
    assert C._unescape("hull=2500") == "hull=2500"


def test_a_DERIVED_field_never_also_carries_a_direct_mapping():
    """The invariant that forced a real decision: `unitcapacity` was declared DERIVED and
    then measured to be stored DIRECTLY (engine 25 == store `storage.unit` 25). A field
    cannot be both 'we cannot compute this' and 'here is how'. The measurement won and the
    _DERIVED entry was removed."""
    assert "unitcapacity" not in C._DERIVED
    prop, tname = C._mapping_for("shiptypes_l", "unitcapacity")
    assert prop == "storage.unit" and tname == "identity"
    # ...and NOT generalised past the evidence: it was proposed for l/xl only.
    assert C._mapping_for("shiptypes_s", "unitcapacity") is None


def test_coolingrate_is_NOT_mapped_for_turrets():
    """A coincidence the tool proposed and a human must reject: on the one sampled turret
    engine coolingrate=200 and rotationspeed.max=200, while the real prop `heat.coolrate`
    was ABSENT so nothing else could match. Cooling rate is not a rotation speed. Two
    engine fields claiming one prop is the tell, and nd=1 is why it survived."""
    assert C._mapping_for("weapons_turrets", "coolingrate") is None
    prop, _ = C._mapping_for("weapons_turrets", "rotation")
    assert prop == "rotationspeed.max"


# --- F72 P4: the connection traversal ----------------------------------------- #


def test_connected_macros_reads_the_refs_out_of_the_flattened_subtree():
    """F72 called this traversal unmodelled. The data was always in the store -- the
    flatten keeps the whole connection subtree, so the refs are plain attrs."""
    props = {
        "connections.connection[con_storage01].ref": "con_storage01",
        "connections.connection[con_storage01].macro.ref": "storage_x_macro",
        "connections.connection[con_dock_xs].macro.ref": "dock_y_macro",
        "connections.connection[con_dock_xs].macro.connection": "Connection_component",
        "hull.max": "2500",
    }
    got = C._connected_macros(props)
    assert sorted(got) == ["dock_y_macro", "storage_x_macro"]
    assert "con_storage01" not in got, "the .ref key is not a macro ref"


def test_storagecapacity_sums_cargo_max_over_connected_macros():
    """MEASURED 5 of 5 exact across the fixture (540, 700, 2300, 8200, 38000)."""
    store = {
        "ship": {"connections.connection[a].macro.ref": "s1",
                 "connections.connection[b].macro.ref": "s2"},
        "s1": {"cargo.max": "500"},
        "s2": {"cargo.max": "40"},
    }

    class _Con:
        pass

    import x4validate._livecli as M
    orig = M._store_props
    M._store_props = lambda con, m: store.get(m)
    try:
        assert M._derive_storagecapacity(_Con(), store["ship"]) == "540"
    finally:
        M._store_props = orig


def test_storagecapacity_returns_NONE_not_ZERO_when_nothing_carries_cargo():
    """⚠ The distinction the whole toolkit turns on. A fabricated 0 would be compared
    against the engine and could AGREE by accident, recording a computation we never
    made. An absence must stay an absence."""
    store = {"ship": {"connections.connection[a].macro.ref": "d1"},
             "d1": {"dock.capacity": "10"}}

    class _Con:
        pass

    import x4validate._livecli as M
    orig = M._store_props
    M._store_props = lambda con, m: store.get(m)
    try:
        assert M._derive_storagecapacity(_Con(), store["ship"]) is None
    finally:
        M._store_props = orig


def test_a_field_with_a_TRAVERSAL_is_no_longer_declared_uncomputable():
    """Same invariant that forced the unitcapacity decision: a field cannot be both
    'we cannot compute this' (_DERIVED) and 'here is how' (_DERIVE)."""
    assert "storagecapacity" in C._DERIVE
    assert "storagecapacity" not in C._DERIVED
    assert not (set(C._DERIVE) & C._DERIVED), "a field is both computed and uncomputable"


def test_shipstoragecapacity_is_still_DERIVED_because_no_traversal_reproduces_it():
    """P5, recorded rather than guessed. MEASURED: the scout reports engine
    shipstoragecapacity=0 while carrying a connected shipstorage macro with
    dock.capacity=10 -- so it is NOT a sum of connected capacities. Inventing a formula
    to close the row would be picking one of several defensible definitions and calling
    it modelled."""
    assert "shipstoragecapacity" in C._DERIVED
    assert "shipstoragecapacity" not in C._DERIVE


# --- unitcapacity: mapped where the evidence reaches, and no further ---------- #


def test_unitcapacity_is_mapped_for_M_because_the_POSITIVE_case_was_verified():
    """MEASURED in game 2026-08-31: `ship_arg_m_frigate_01_a_macro` returns engine 15
    and the store holds storage.unit 15.

    That matters because until then every M/S observation had been the NULL case
    (engine 0, store absent). A mapping verified only on nulls is one you have never
    seen discriminate -- it agrees whenever both sides are empty, which proves nothing
    about whether it reads the right property."""
    assert C._mapping_for("shiptypes_m", "unitcapacity") == ("storage.unit", "identity")
    assert C._mapping_for("shiptypes_l", "unitcapacity") == ("storage.unit", "identity")
    assert C._mapping_for("shiptypes_xl", "unitcapacity") == ("storage.unit", "identity")


def test_unitcapacity_is_NOT_mapped_for_S_and_the_reason_is_MEASURED():
    """Not caution -- arithmetic. MEASURED over the effective store: exactly TWO
    S-class macros carry `storage.unit` in the whole corpus
    (ship_kha_s_fighter_01_a_macro and _02_a_) and BOTH ARE ZERO.

    So there is no positive case to verify and none to get wrong: the mapping could
    never discriminate. M has 16 carriers, 12 nonzero. Mapping S would add a
    comparison that agrees vacuously forever, which is worse than an honest gap
    because it LOOKS like coverage."""
    assert C._mapping_for("shiptypes_s", "unitcapacity") is None
    assert C._mapping_for("shiptypes_xs", "unitcapacity") is None


def test_unitcapacity_is_not_both_mapped_and_conceded():
    """The invariant that forced the original decision: a field cannot be both
    'we cannot compute this' and 'here is how'."""
    assert "unitcapacity" not in C._DERIVED


# --- the star row: split on the ESCAPE, not on a real tab --------------------- #


def test_a_star_row_written_by_the_CURRENT_writer_yields_every_field(tmp_path):
    """★ The defect this pins, and it was self-inflicted.

    The `*` payload is the engine's all-fields reply, tab-joined INTERNALLY. The
    writer's escaping (added 2026-08-30 to stop a description's tab breaking the row
    structure) turns every one of those separators into a two-character escape. The
    reader still split on a REAL tab, found none, and parsed only the FIRST key=value.

    MEASURED: it cut `ship_arg_s_scout_01_a_macro` from 37 fields to 10, and the 27 it
    dropped were hull, mass, all six drag axes and all three inertia axes -- exactly
    the DIRECTLY COMPARABLE ones. The oracle then reported 0 comparable on that fixture
    and it read as "this capture is equipment-heavy" rather than "this capture is
    gutted". A fix for one silent-loss defect created another.
    """
    esc = chr(92) + "t"
    p = tmp_path / "gt.tsv"
    p.write_text(
        "librarytype\tmacro\tfield\tengine_value\n"
        "shiptypes_s\tm1\t*\thull=2500" + esc + "mass=6.34" + esc + "drag_forward=1.5\n",
        encoding="utf-8")
    got, st = C._entries_from_groundtruth(p)
    assert got[("shiptypes_s", "m1")] == {
        "hull": "2500", "mass": "6.34", "drag_forward": "1.5"}, (
        "only the first key=value survived -- the reader is splitting on a real tab")
    assert st["star_rows"] == 1


def test_a_star_row_from_a_PRE_ESCAPING_fixture_still_parses(tmp_path):
    """The twin, and it is why the reader picks the separator that is PRESENT rather
    than assuming a vintage. Fixtures written before 2026-08-30 carry REAL tabs inside
    the payload; they are the evidence base for F72 and must keep parsing."""
    p = tmp_path / "gt.tsv"
    p.write_text(
        "librarytype\tmacro\tfield\tengine_value\n"
        "shiptypes_s\tm1\t*\thull=2500\tmass=6.34\n", encoding="utf-8")
    got, _ = C._entries_from_groundtruth(p)
    assert got[("shiptypes_s", "m1")] == {"hull": "2500", "mass": "6.34"}


def test_the_real_fixtures_BOTH_yield_the_scouts_full_field_set():
    """End to end over the actual artifacts, both vintages. A unit fixture cannot
    catch a writer/reader disagreement that only shows up on real captures."""
    # ⚠ WHERE `groundtruth` WRITES, not a path relative to this file. It was
    # `parents[3] / "dev" / "_reports"`, the layout of the retired dev repository; in this
    # repository that directory does not exist on ANY machine, so the test skipped
    # everywhere -- MEASURED 2026-09-14 on a machine holding both fixtures in
    # `$X4_MODS/_reports`. A check that cannot run reports the same as one with nothing to do.
    base = C._archive_dir()
    if base is None:
        pytest.skip("$X4_MODS is not configured -- no groundtruth archive to read")
    key = ("shiptypes_s", "ship_arg_s_scout_01_a_macro")
    seen = 0
    for name in ("groundtruth-20260829-175342.tsv", "groundtruth-20260831-122748.tsv"):
        f = base / name
        if not f.is_file():
            continue
        seen += 1
        got, _ = C._entries_from_groundtruth(f)
        assert len(got.get(key, {})) == 37, (
            f"{name}: scout has {len(got.get(key, {}))} fields, expected 37")
    if seen == 0:
        pytest.skip(f"no groundtruth fixtures present in {base}")


def test_an_UNMODELLED_row_kind_is_REPORTED_and_fails(tmp_path, capsys):
    """The twin that was impossible before 2026-09-02.

    `accounts_for_every_row()` compared a Counter built from `rows` against
    `len(rows)` -- true by construction, MEASURED over 20,000 randomised dumps as
    never False and never raising. So this whole branch, including its `return 3`,
    was unreachable, and the reassurance below it printed on every parseable dump.
    Three tests used the predicate as a control that "must PASS".
    """
    # Built so the END row's own count stays right: parse() compares the game's
    # declared row count against what it parsed, and a mismatch is a DIFFERENT
    # failure (a corrupt dump) that would shadow the one under test here.
    rows = good_rows(3)[:-1]
    rows.append(["WAT_IS_THIS", "some", "payload"])
    rows.append(["END", str(len(rows) + 1)])
    rc = C.main(["--file", write(tmp_path, uidata(rows)), "dump"])
    out = capsys.readouterr().out
    assert rc == 3, out
    assert "WAT_IS_THIS x1" in out, out
    assert "does not model" in out, out


def test_a_dump_of_only_KNOWN_kinds_is_clean(tmp_path, capsys):
    """The other half: a predicate that rejected everything would pass the test above."""
    rc = C.main(["--file", write(tmp_path, uidata(good_rows(3))), "dump"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "does not model" not in out


# --- the groundtruth WRITER must not trust its own write ------------------------- #
#
# MEASURED 2026-09-05 by `gates/mutation_probe.py`: mutating `if back != data:` to
# `if False:` in `cmd_groundtruth` SURVIVED -- 47 of 48 mutants killed, this one alive.
# The mechanism was present and correct; nothing proved it stays that way, which is the
# difference between "the code is right" and "the code is pinned". A harvested fixture
# could be written short and reported complete, and this fixture is what later decides
# between two defensible definitions of a derived field -- so a silently truncated one
# does not read as missing, it reads as the engine's answer.

class _StubPipe:
    """Answers ABSENT to everything: a REAL answer, so the accounting still balances
    (present + absent + errored == asked) and execution reaches the write."""

    path = "stub-pipe"

    def ask(self, *a, **k):
        from x4validate import _livepipe
        return _livepipe.Reply(seq=1, status="ABSENT", payload="")


def _stub_live_open(monkeypatch):
    import contextlib as _c

    from x4validate import _livecli

    @_c.contextmanager
    def fake(pipe, timeout):
        yield _StubPipe()

    monkeypatch.setattr(_livecli, "_live_open", fake)


def test_groundtruth_REFUSES_when_the_file_does_not_read_back(tmp_path, monkeypatch):
    """The write lands SHORT and the re-read must catch it."""
    import io
    from pathlib import Path

    from x4validate import _livecli, _livedump

    _stub_live_open(monkeypatch)
    dest = tmp_path / "gt.tsv"

    real_write = Path.write_bytes

    def short_write(self, data):
        # A truncating write: exactly the failure mode a re-read exists to see, and
        # one that reports success to its caller.
        return real_write(self, data[: len(data) // 2])

    monkeypatch.setattr(Path, "write_bytes", short_write)
    with pytest.raises(_livedump.LiveDumpCorrupt, match="read back"):
        _livecli.cmd_groundtruth(None, 1.0, out_file=str(dest), out=io.StringIO())


def test_groundtruth_SUCCEEDS_when_the_file_does_read_back(tmp_path, monkeypatch):
    """The control. Without it, a `raise` added unconditionally would pass the test
    above while breaking every real harvest -- a red that cannot go green is worth as
    little as a green that cannot go red."""
    import io

    from x4validate import _livecli

    _stub_live_open(monkeypatch)
    dest = tmp_path / "gt.tsv"
    rc = _livecli.cmd_groundtruth(None, 1.0, out_file=str(dest), out=io.StringIO())
    assert dest.is_file(), "the harvest must actually write when the write is honest"
    assert dest.read_bytes(), "and it must not be empty"
    assert rc in (0, 2), rc          # 2 = nothing harvested, which this stub guarantees


# --- groundtruth --macros FILE / --contents ------------------------------------ #

class _RecordingPipe(_StubPipe):
    def __init__(self):
        self.calls = []

    def ask(self, *a, **k):
        self.calls.append(a)
        return super().ask(*a, **k)


def _open_with(monkeypatch, pipe):
    import contextlib as _c

    from x4validate import _livecli

    @_c.contextmanager
    def fake(p, timeout):
        yield pipe

    monkeypatch.setattr(_livecli, "_live_open", fake)


def _never_open(monkeypatch):
    from x4validate import _livecli

    def refuse(*a, **k):
        raise AssertionError("the game was contacted before the list was validated")

    monkeypatch.setattr(_livecli, "_live_open", refuse)


def _run_groundtruth(tmp_path, **kw):
    import io

    from x4validate import _livecli
    buf = io.StringIO()
    rc = _livecli.cmd_groundtruth(None, 1.0, out_file=str(tmp_path / "gt.tsv"), out=buf, **kw)
    return rc, buf.getvalue()


@pytest.mark.parametrize("body, needle", [
    (None, "cannot read"),
    ("# only a comment\n\n", "lists no macros"),
    ("shiptypes_s ship_a_macro\nonecolumn\n", ":2: expected"),
    ("shiptypes_s ship_a_macro extra\n", ":1: expected"),
])
def test_groundtruth_macros_REFUSES_an_unusable_list_BEFORE_the_game(
        tmp_path, monkeypatch, capsys, body, needle):
    _never_open(monkeypatch)
    f = tmp_path / "list.tsv"
    if body is not None:
        f.write_text(body, encoding="utf-8")
    rc, _ = _run_groundtruth(tmp_path, macros_file=str(f))
    assert rc == 2
    assert needle in capsys.readouterr().err
    assert not (tmp_path / "gt.tsv").exists()


def test_groundtruth_macros_REFUSES_a_name_the_store_does_not_hold(tmp_path, monkeypatch, capsys):
    from x4validate import _livecli
    _never_open(monkeypatch)
    monkeypatch.setattr(_livecli, "_missing_from_store", lambda pairs: ["invented_macro"])
    f = tmp_path / "list.tsv"
    f.write_text("missiletypes invented_macro\nshiptypes_s real_macro\n", encoding="utf-8")
    rc, _ = _run_groundtruth(tmp_path, macros_file=str(f))
    assert rc == 2
    err = capsys.readouterr().err
    assert "1 of 2" in err and "invented_macro" in err


def test_groundtruth_macros_REFUSES_when_the_store_cannot_be_asked(tmp_path, monkeypatch, capsys):
    from x4validate import _livecli
    _never_open(monkeypatch)
    monkeypatch.setattr(_livecli, "_missing_from_store", lambda pairs: None)
    f = tmp_path / "list.tsv"
    f.write_text("shiptypes_s real_macro\n", encoding="utf-8")
    rc, _ = _run_groundtruth(tmp_path, macros_file=str(f))
    assert rc == 2
    assert "cannot be checked" in capsys.readouterr().err


def test_groundtruth_macros_harvests_EXACTLY_the_listed_macros(tmp_path, monkeypatch):
    from x4validate import _livecli
    pipe = _RecordingPipe()
    _open_with(monkeypatch, pipe)
    monkeypatch.setattr(_livecli, "_missing_from_store", lambda pairs: [])
    f = tmp_path / "list.tsv"
    f.write_text("shiptypes_s ship_a_macro  # why\n\nweapons_lasers weapon_b_macro\n",
                 encoding="utf-8")
    _run_groundtruth(tmp_path, macros_file=str(f))
    asked = {(c[1], c[2]) for c in pipe.calls if c[0] == "macro"}
    assert asked == {("shiptypes_s", "ship_a_macro"), ("weapons_lasers", "weapon_b_macro")}
    head = (tmp_path / "gt.tsv").read_text(encoding="utf-8")
    assert "# macros=list.tsv n=2 contents=no" in head


def test_groundtruth_CONTENTS_goes_on_the_all_fields_ask_ONLY(tmp_path, monkeypatch):
    """A per-field reply is one value; and an old helper build reads the flag as a
    property name, so it must never ride on the per-field asks."""
    pipe = _RecordingPipe()
    _open_with(monkeypatch, pipe)
    _run_groundtruth(tmp_path, contents=True)
    macro_calls = [c for c in pipe.calls if c[0] == "macro"]
    star = [c for c in macro_calls if len(c) == 4 and c[3] == "--contents"]
    per_field = [c for c in macro_calls if not (len(c) == 4 and c[3] == "--contents")]
    from x4validate import _livecli
    assert len(star) == len(_livecli.GROUND_TRUTH_MACROS), star
    assert per_field and all("--contents" not in c for c in per_field)
    assert "contents=yes" in (tmp_path / "gt.tsv").read_text(encoding="utf-8")


def test_groundtruth_WITHOUT_contents_sends_no_flag(tmp_path, monkeypatch):
    pipe = _RecordingPipe()
    _open_with(monkeypatch, pipe)
    _run_groundtruth(tmp_path)
    assert not any("--contents" in c for c in pipe.calls)
    head = (tmp_path / "gt.tsv").read_text(encoding="utf-8")
    assert "# macros=built-in n=" in head and "contents=no" in head


# --- groundtruth, review 2026-09-14 -------------------------------------------- #

class _MacroPipe:
    """Answers `macro` like the helper. `knows_contents=False` models a build that predates the
    flag: it reads `--contents` as a PROPERTY name and answers ABSENT."""

    path = "macro-pipe"

    def __init__(self, knows_contents=True, star_absent_for=(), per_field_ok=False,
                 degrade_plain=False):
        self.knows_contents, self.star_absent_for = knows_contents, set(star_absent_for)
        self.per_field_ok, self.degrade_plain = per_field_ok, degrade_plain
        self.calls = []

    def ask(self, verb, *args, **k):
        from x4validate import _livepipe
        self.calls.append((verb, *args))
        ltype, macro, *rest = args
        absent = _livepipe.Reply(seq=1, status="ABSENT", payload=f"{ltype}\t{macro}")
        if rest and rest != ["--contents"]:
            # A per-field ask. `per_field_ok` models the case review round 1 MEASURED: the
            # derived fields answer, so the old code harvested values and exited 0.
            if self.per_field_ok:
                return _livepipe.Reply(seq=1, status="OK", payload="7")
            return absent
        if not rest and self.degrade_plain:
            raise _livepipe.LiveQueryDegraded("simulated: the re-ask came back mangled")
        if rest == ["--contents"] and not self.knows_contents:
            return absent
        if macro in self.star_absent_for:
            return absent
        return _livepipe.Reply(seq=1, status="OK", payload="hull=1\tweapons=<table>")


def test_groundtruth_CONTENTS_against_a_build_that_predates_it_is_REFUSED(tmp_path, monkeypatch,
                                                                         capsys):
    """MEASURED by review: an old build answers ABSENT to `macro <lt> <m> --contents`, and the
    harvest counted every one as 'a real answer' -- rc 0, zero `*` rows, header contents=yes."""
    # per_field_ok: without it the OLD code also exited 2 ("NOTHING was harvested"), so the rc
    # assertion could not tell old from new (review round 2).
    pipe = _MacroPipe(knows_contents=False, per_field_ok=True)
    _open_with(monkeypatch, pipe)
    rc, out = _run_groundtruth(tmp_path, contents=True)
    assert rc == 2, out
    assert "--contents" in capsys.readouterr().err
    assert not (tmp_path / "gt.tsv").exists(), "a refused harvest must not leave a fixture"


def test_groundtruth_CONTENTS_on_a_build_that_knows_it_still_harvests(tmp_path, monkeypatch):
    pipe = _MacroPipe(knows_contents=True)
    _open_with(monkeypatch, pipe)
    rc, out = _run_groundtruth(tmp_path, contents=True)
    assert rc == 0, out
    assert "\t*\thull=1" in (tmp_path / "gt.tsv").read_text(encoding="utf-8")


def test_groundtruth_an_ABSENT_all_fields_answer_is_NAMED_not_just_counted(tmp_path, monkeypatch):
    """A listed macro whose all-fields call is ABSENT may carry the wrong library type (the store
    check sees names only) or be an entry the library does not hold; the harvest cannot tell
    which, so it must say WHICH macros, in the output and the header."""
    from x4validate import _livecli
    pipe = _MacroPipe(star_absent_for={"ship_b_macro"})
    _open_with(monkeypatch, pipe)
    monkeypatch.setattr(_livecli, "_missing_from_store", lambda pairs: [])
    f = tmp_path / "list.tsv"
    f.write_text("shiptypes_s ship_a_macro\nweapons_lasers ship_b_macro\n", encoding="utf-8")
    rc, out = _run_groundtruth(tmp_path, macros_file=str(f))
    assert "ship_b_macro" in out and "ABSENT" in out, out
    assert "star_absent=1" in (tmp_path / "gt.tsv").read_text(encoding="utf-8")


def test_groundtruth_a_DEGRADED_re_ask_does_not_relabel_a_real_ABSENT(tmp_path, monkeypatch):
    """Review round 2: the --contents re-ask is a second request, and if IT comes back mangled
    the genuine all-fields ABSENT was recorded as `!DEGRADED` -- the re-ask is only a probe for
    an old build, so its failure must leave the first answer standing."""
    pipe = _MacroPipe(star_absent_for={"ship_arg_s_scout_01_a_macro"}, degrade_plain=True)
    _open_with(monkeypatch, pipe)
    rc, out = _run_groundtruth(tmp_path, contents=True)
    text = (tmp_path / "gt.tsv").read_text(encoding="utf-8")
    assert "!DEGRADED" not in text, text[:400]
    assert "shiptypes_s ship_arg_s_scout_01_a_macro" in out, out


def test_missing_from_store_treats_a_CORRUPT_store_file_as_UNASKABLE(tmp_path, monkeypatch):
    """Review round 2, MEASURED: `_connect` opens read-only LAZILY, so a garbage file passes it
    and `sqlite3.DatabaseError: file is not a database` crashed the lookup instead."""
    from x4validate import _effective, _livecli
    db = tmp_path / "store.sqlite"
    db.write_bytes(b"this is not an sqlite database, not even close" * 10)
    monkeypatch.setattr(_effective, "effective_db", lambda *a, **k: db)
    assert _livecli._missing_from_store([("shiptypes_s", "x")]) is None


def test_groundtruth_a_DUPLICATE_macro_line_is_asked_ONCE_and_said(tmp_path, monkeypatch):
    from x4validate import _livecli
    pipe = _MacroPipe()
    _open_with(monkeypatch, pipe)
    monkeypatch.setattr(_livecli, "_missing_from_store", lambda pairs: [])
    f = tmp_path / "list.tsv"
    f.write_text("shiptypes_s ship_a_macro\nshiptypes_s ship_a_macro\n", encoding="utf-8")
    rc, out = _run_groundtruth(tmp_path, macros_file=str(f))
    stars = [c for c in pipe.calls if c[0] == "macro" and len(c) == 3]
    assert len(stars) == 1, stars
    assert "1 duplicate" in out, out
    assert "# macros=list.tsv n=1" in (tmp_path / "gt.tsv").read_text(encoding="utf-8")


def test_missing_from_store_treats_a_SystemExit_from_the_store_as_UNASKABLE(tmp_path, monkeypatch):
    """`_connect` reports an unusable store with SystemExit, a BaseException that
    `except Exception` does not catch -- so the refusal path was a crash (review)."""
    from x4validate import _effective, _livecli
    db = tmp_path / "store.sqlite"
    db.write_bytes(b"not a database")
    monkeypatch.setattr(_effective, "effective_db", lambda *a, **k: db)

    def boom(*a, **k):
        raise SystemExit(2)

    monkeypatch.setattr(_effective, "_connect", boom)
    assert _livecli._missing_from_store([("shiptypes_s", "x")]) is None


# --- ffi-surface verbs are DISABLED by default (crash containment, 2026-09-14) ---- #

def test_ffi_census_is_DISABLED_by_default_and_does_no_work(tmp_path, monkeypatch, capsys):
    """Default-off. The refusal must come BEFORE parsing reference/ or opening the pipe:
    a census that first read the corpus, then refused, would still pay the cost the gate
    exists to avoid. census() and _live_open both raise if reached."""
    import io
    from x4validate import _ffinames, _livecli
    monkeypatch.delenv("X4_LIVE_ALLOW_FFI", raising=False)

    def must_not_parse(config):
        raise AssertionError("parsed the ffi source while the verb was disabled")

    monkeypatch.setattr(_ffinames, "census", must_not_parse)
    _never_open(monkeypatch)
    rc = _livecli.cmd_ffi_census(None, 1.0, out_file=str(tmp_path / "c.tsv"), out=io.StringIO())
    assert rc == 2
    err = capsys.readouterr().err
    assert "DISABLED" in err and "X4_LIVE_ALLOW_FFI" in err


def test_query_REFUSES_ffisyms_when_the_gate_is_unset(monkeypatch, capsys):
    """`query ffisyms` is the other client path to the FFI-index verb; it is gated the
    same way, and refuses before the pipe is opened."""
    import io
    from x4validate import _livecli
    monkeypatch.delenv("X4_LIVE_ALLOW_FFI", raising=False)
    _never_open(monkeypatch)
    rc = _livecli.cmd_query("ffisyms", ["GetPlayerID"], None, 1.0, out=io.StringIO())
    assert rc == 2
    assert "X4_LIVE_ALLOW_FFI" in capsys.readouterr().err


def test_query_still_answers_a_NON_ffi_verb_when_the_gate_is_unset(monkeypatch):
    """The twin: the gate is scoped to `ffisyms` alone. Without this, a gate that
    refused every query verb would pass the test above and silence the channel."""
    import io
    from x4validate import _livecli
    monkeypatch.delenv("X4_LIVE_ALLOW_FFI", raising=False)
    pipe = _SymPipe({"GetPlayerID": "exported|cdata"})
    _open_with(monkeypatch, pipe)
    rc = _livecli.cmd_query("ping", [], None, 1.0, out=io.StringIO())
    assert rc == 0 and pipe.requests, "a non-ffi verb was refused by the ffi gate"


# --- ffi-census: batch vanilla's declared C functions through `ffisyms` --------- #

def _fake_census(names):
    from x4validate import _ffinames
    return _ffinames.Census(names={n: [f"ui/{n.lower()}.lua"] for n in names},
                            files_scanned=len(names) + 1, files_with_names=len(names),
                            blocks=len(names), unparsed=[("ui/dyn.lua", 1)])


class _SymPipe:
    """Answers `ffisyms` the way the lua verb does, from a class table; records requests."""

    path = "sym-pipe"

    def __init__(self, classes, err_on=None, drop_row_on=None, status="OK", swap=False,
                 err_payload="boom", lose_on_request=None, rows_override=None):
        self.classes, self.err_on, self.drop_row_on = classes, err_on, drop_row_on
        self.status, self.swap = status, swap
        self.err_payload, self.lose_on_request = err_payload, lose_on_request
        self.rows_override = rows_override
        self.requests = []

    def ask(self, verb, *names, **k):
        from x4validate import _livepipe
        self.requests.append((verb, names))
        if self.lose_on_request == len(self.requests):
            raise _livepipe.LiveQueryUnavailable("simulated: the pipe went away")
        if self.err_on and self.err_on in names:
            return _livepipe.Reply(seq=1, status="ERR", payload=self.err_payload)
        rows = [f"{n}|{self.classes.get(n, 'undeclared')}" for n in names
                if n != self.drop_row_on]
        if self.rows_override is not None:
            rows = self.rows_override(names)
        if self.swap:
            rows.reverse()
        counts: dict[str, int] = {}
        for n in names:
            key = self.classes.get(n, "undeclared").split("|")[0]
            counts[key] = counts.get(key, 0) + 1
        head = f"asked={len(names)} " + " ".join(f"{c}={v}" for c, v in counts.items())
        # `status` may be ERR over a perfectly shaped payload: the case where only the
        # status check stands between a refusal and a believed answer.
        return _livepipe.Reply(seq=1, status=self.status, payload="\t".join([head, *rows]))


def _census_run(tmp_path, monkeypatch, names, pipe, **kw):
    import io

    from x4validate import _ffinames, _livecli
    # ffi-census is gated OFF by default (crash containment); these tests exercise the
    # batching/alignment LOGIC behind the gate, so they open it explicitly.
    monkeypatch.setenv("X4_LIVE_ALLOW_FFI", "1")
    monkeypatch.setattr(_ffinames, "census", lambda config: _fake_census(names))
    _open_with(monkeypatch, pipe)
    buf = io.StringIO()
    dest = tmp_path / "census.tsv"
    rc = _livecli.cmd_ffi_census(None, 1.0, out_file=str(dest), out=buf, **kw)
    return rc, buf.getvalue(), dest


def test_ffi_census_CLASSIFIES_every_name_and_the_buckets_SUM(tmp_path, monkeypatch):
    names = ["GetA", "GetB", "GetC"]
    pipe = _SymPipe({"GetA": "exported|cdata", "GetB": "notexported"})
    rc, out, dest = _census_run(tmp_path, monkeypatch, names, pipe)
    assert rc == 0, out
    rows = [l.split("\t") for l in dest.read_text(encoding="utf-8").splitlines()
            if l and not l.startswith("#")]
    assert rows[0][:2] == ["name", "class"]
    body = {r[0]: r for r in rows[1:]}
    assert set(body) == set(names)
    assert body["GetA"][1] == "exported" and body["GetB"][1] == "notexported"
    assert body["GetC"][1] == "undeclared"
    assert body["GetA"][-1] == "ui/geta.lua", "provenance travels with every row"
    assert "exported=1" in out and "notexported=1" in out and "undeclared=1" in out
    # Scope is printed, never implied: what the source parse could not read.
    assert "unparsed" in out and "ui/dyn.lua" in out


def test_ffi_census_BATCHES_within_both_bounds(tmp_path, monkeypatch):
    names = ["Fn%03d_%s" % (i, "x" * 20) for i in range(400)]
    pipe = _SymPipe({})
    rc, out, _ = _census_run(tmp_path, monkeypatch, names, pipe, batch_bytes=600)
    assert rc == 0, out
    sent = [n for _, batch in pipe.requests for n in batch]
    assert sorted(sent) == sorted(names), "every name asked exactly once"
    for verb, batch in pipe.requests:
        assert verb == "ffisyms"
        assert len(batch) <= 150
        assert len("\t".join(batch).encode()) <= 600, len("\t".join(batch).encode())


def test_ffi_census_an_ERR_batch_is_a_NON_ANSWER_for_its_names_not_a_gap(tmp_path, monkeypatch):
    names = ["GetA", "GetB"]
    pipe = _SymPipe({"GetA": "exported|cdata", "GetB": "exported|cdata"}, err_on="GetB")
    rc, out, dest = _census_run(tmp_path, monkeypatch, names, pipe, batch_bytes=5)
    assert rc == 3, out
    text = dest.read_text(encoding="utf-8")
    assert "GetB\terrored" in text, text
    assert "GetA\texported" in text, "the batch that answered is still recorded"


def test_ffi_census_a_reply_with_the_WRONG_row_count_is_REFUSED(tmp_path, monkeypatch):
    """Rows are aligned by POSITION (an invalid name is not echoed), so a short reply would
    shift every later classification onto the wrong name."""
    names = ["GetA", "GetB", "GetC"]
    pipe = _SymPipe({}, drop_row_on="GetB")
    rc, out, dest = _census_run(tmp_path, monkeypatch, names, pipe)
    assert rc == 3, out
    assert "GetC\tundeclared" not in dest.read_text(encoding="utf-8")


def test_ffi_census_with_NO_names_never_contacts_the_game(tmp_path, monkeypatch, capsys):
    import io

    from x4validate import _ffinames, _livecli
    monkeypatch.setenv("X4_LIVE_ALLOW_FFI", "1")  # this test is about the 0-names path, not the gate
    monkeypatch.setattr(_ffinames, "census", lambda config: _fake_census([]))
    _never_open(monkeypatch)
    rc = _livecli.cmd_ffi_census(None, 1.0, out_file=str(tmp_path / "c.tsv"), out=io.StringIO())
    assert rc == 2
    assert "0 names" in capsys.readouterr().err


# Twins: each clause below is otherwise SHADOWED by an earlier guard, so its mutant would
# survive every test above (MEASURED by the hand-mutant run that added these).

def test_TWIN_ffi_census_REFUSES_a_batch_size_the_pipe_cannot_carry(tmp_path, monkeypatch, capsys):
    """`ask()` raises LiveRequestTooLarge -- a ValueError, because it is a client bug --
    and cmd_ffi_census caught only the LiveQuery* transport errors, so main() re-raised it:
    a traceback, exit 1, and NO TSV, losing every batch already answered. The bound is a
    constant, so it is refused once, up front, naming the cap."""
    names = ["F%03d" % i for i in range(10)]
    pipe = _SymPipe({})
    from x4validate import _livepipe
    rc, out, _dest = _census_run(tmp_path, monkeypatch, names, pipe, batch_bytes=100000)
    err = capsys.readouterr().err
    assert rc == 2, out
    assert "REFUSING" in err and str(_livepipe.MAX_REQUEST_BYTES) in err, err
    assert not pipe.requests, "nothing may be sent once the size is known to be illegal"


def test_ffi_census_BATCHES_within_the_NAME_bound_when_bytes_allow_more(tmp_path, monkeypatch):
    names = ["F%03d" % i for i in range(400)]
    pipe = _SymPipe({})
    # The LARGEST byte budget a request can actually carry. This used to pass 100_000,
    # which no real pipe accepts -- so the byte bound was unreachable here and the test
    # could not tell the two bounds apart. At the real ceiling the NAME bound still
    # binds first (150 names of ~5 bytes is well under it), which is what this asserts.
    # The room the command itself computes from the real frame header (RT-7: the old
    # `MAX - len('ffisyms') - 2` reserved 9 bytes for a 16+-byte header).
    room = C._ffi_request_room()[1]
    rc, out, _ = _census_run(tmp_path, monkeypatch, names, pipe, batch_bytes=room)
    assert rc == 0, out
    assert all(len(b) <= 150 for _, b in pipe.requests), [len(b) for _, b in pipe.requests]
    assert len(pipe.requests) >= 3


def test_ffi_census_an_ERR_reply_shaped_like_an_answer_is_STILL_refused(tmp_path, monkeypatch):
    pipe = _SymPipe({"GetA": "exported|cdata"}, status="ERR")
    rc, out, dest = _census_run(tmp_path, monkeypatch, ["GetA"], pipe)
    assert rc == 3, out
    assert "GetA\terrored" in dest.read_text(encoding="utf-8")


def test_ffi_census_a_reply_missing_its_LAST_row_is_REFUSED(tmp_path, monkeypatch):
    """The earlier rows still line up by name, so only the row COUNT can catch this."""
    pipe = _SymPipe({}, drop_row_on="GetC")
    rc, out, dest = _census_run(tmp_path, monkeypatch, ["GetA", "GetB", "GetC"], pipe)
    assert rc == 3, out
    text = dest.read_text(encoding="utf-8")
    assert "GetA\terrored" in text and "GetC\terrored" in text, text


def test_ffi_census_rows_in_the_WRONG_ORDER_are_REFUSED(tmp_path, monkeypatch):
    """Right count, wrong names: only the per-row alignment check can catch this."""
    pipe = _SymPipe({"GetA": "exported|cdata"}, swap=True)
    rc, out, dest = _census_run(tmp_path, monkeypatch, ["GetA", "GetB"], pipe)
    assert rc == 3, out
    assert "GetA\texported" not in dest.read_text(encoding="utf-8")


def test_ffi_census_an_UNKNOWN_class_is_REFUSED(tmp_path, monkeypatch):
    pipe = _SymPipe({"GetA": "bogus"})
    rc, out, dest = _census_run(tmp_path, monkeypatch, ["GetA"], pipe)
    assert rc == 3, out
    assert "GetA\terrored" in dest.read_text(encoding="utf-8")


# --- review 2026-09-14 --------------------------------------------------------- #

def _body(dest):
    return [l for l in dest.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]


def test_ffi_census_every_TSV_row_keeps_FOUR_columns_whatever_the_error_text(tmp_path, monkeypatch):
    """MEASURED by review: an ERR payload carrying a tab produced a 5-column row. A substring
    check (`GetB\\terrored`) cannot see a shifted column; the column COUNT can."""
    pipe = _SymPipe({}, err_on="GetA", err_payload="bad\tthing\nmore\rstill")
    rc, out, dest = _census_run(tmp_path, monkeypatch, ["GetA", "GetB"], pipe, batch_bytes=5)
    assert rc == 3, out
    rows = _body(dest)
    assert all(len(r.split("\t")) == 4 for r in rows), rows


def test_ffi_census_a_channel_LOST_mid_run_KEEPS_what_was_answered(tmp_path, monkeypatch, capsys):
    """MEASURED by review: a LiveQueryUnavailable on batch 2 raised out of the command, so no
    TSV was written and batch 1's answers were lost. The likely cause in practice is the one
    the batching exists for -- a request the game-side read could not take."""
    pipe = _SymPipe({"GetA": "exported|cdata"}, lose_on_request=2)
    rc, out, dest = _census_run(tmp_path, monkeypatch, ["GetA", "GetB", "GetC"], pipe,
                                batch_bytes=5)
    assert rc == 3, out
    assert len(pipe.requests) == 2, "nothing more is sent once the channel is gone"
    text = dest.read_text(encoding="utf-8")
    assert "GetA\texported" in text, text
    assert "GetB\terrored" in text and "GetC\terrored" in text, text
    assert "channel lost" in text, text
    # Review round 2: LiveQueryUnavailable also means "connected, then no reply" -- a minimized
    # game -- and its message carries the fix. Before the catch it reached the terminal via
    # main(); the diagnosis must still reach the terminal, not only a TSV cell.
    assert "simulated: the pipe went away" in capsys.readouterr().err


@pytest.mark.parametrize("row", ["<invalid>|exported|cdata", "GetA|invalid"])
def test_ffi_census_invalid_must_PAIR_both_ways(tmp_path, monkeypatch, row):
    """`<invalid>` only ever answers with class `invalid`, and a NAMED row never does."""
    pipe = _SymPipe({}, rows_override=lambda names: [row])
    rc, out, dest = _census_run(tmp_path, monkeypatch, ["GetA"], pipe)
    assert rc == 3, out
    assert "GetA\terrored" in dest.read_text(encoding="utf-8")


def test_ffi_census_an_INVALID_name_is_never_SENT_and_is_a_NON_ANSWER(tmp_path, monkeypatch):
    long_name = "X" * 101
    pipe = _SymPipe({"GetA": "exported|cdata"})
    rc, out, dest = _census_run(tmp_path, monkeypatch, ["GetA", "bad-name", long_name], pipe)
    assert rc == 3, out
    sent = [n for _, b in pipe.requests for n in b]
    assert sent == ["GetA"], sent
    text = dest.read_text(encoding="utf-8")
    assert "bad-name\tinvalid" in text and f"{long_name}\tinvalid" in text, text


# --- the WRITE subcommands: `pausestate`, `pause`, `unpause` ---------------------- #
#
# The exit code is the contract a caller acts on, so every engine outcome maps to one:
#   0  the verb ACTED and the engine's read-back AGREES with what was asked
#   1  the engine REFUSED without acting (already paused, not ours, ...)
#   2  we could not ask -- and, if the command was already sent, it MAY have landed
#   3  anything that cannot be trusted: acted but disagreed, raised, unverified,
#      unwrapped, or a reply whose advisory row is missing or not first

BANNER = "!" * 78


def _adv(**kw):
    """An advisory row as the game-side mod writes it. Builds INPUT, never an expectation."""
    row = {"verb": "pause", "reason": "ok", "acted": "yes", "before": "false",
           "after": "true", "want": "true", "agree": "yes", "owner": "us",
           "ownerreset": "no", "build": "deadbeef"}
    row.update(kw)
    return "write=yes " + " ".join(f"{k}={v}" for k, v in row.items())


class _WritePipe:
    """Answers one canned reply and RECORDS every ask, so a test can assert what was sent."""

    path = "stub-pipe"

    def __init__(self, status="OK", payload="", raises=None):
        self.status, self.payload, self.raises = status, payload, raises
        self.asks = []

    def ask(self, verb, *args):
        self.asks.append((verb, args))
        if self.raises is not None:
            raise self.raises
        from x4validate import _livepipe
        return _livepipe.Reply(seq=1, status=self.status, payload=self.payload)


def _serve(monkeypatch, pipe):
    import contextlib as _c

    @_c.contextmanager
    def fake(p, timeout):
        yield pipe

    monkeypatch.setattr(C, "_live_open", fake)


@pytest.mark.parametrize("verb", ["pause", "unpause"])
def test_a_write_exits_0_when_it_ACTED_and_the_read_back_AGREES(verb, monkeypatch, capsys):
    _serve(monkeypatch, _WritePipe("OK", _adv(verb=verb) + "\tprose"))
    assert C.main([verb]) == 0, capsys.readouterr()


@pytest.mark.parametrize("status,fields,rc", [
    ("OK", {"reason": "disagree", "agree": "no"}, 3),
    ("ERR", {"reason": "already-paused", "acted": "no"}, 1),
    ("ERR", {"reason": "not-ours", "acted": "no", "owner": "other"}, 1),
    ("ERR", {"reason": "raised", "acted": "yes", "agree": "no"}, 3),
    ("ERR", {"reason": "unverified", "acted": "yes", "agree": "unknown"}, 3),
    ("ERR", {"reason": "unwrapped", "acted": "unknown", "agree": "unknown"}, 3),
    ("OK", {"reason": "ok", "acted": "no"}, 3),
], ids=["disagree", "already-paused", "not-ours", "raised", "unverified", "unwrapped",
        "OK-without-acting"])
def test_each_write_outcome_maps_to_its_exit_code(status, fields, rc, monkeypatch, capsys):
    _serve(monkeypatch, _WritePipe(status, _adv(**fields) + "\tprose"))
    assert C.main(["pause"]) == rc, capsys.readouterr()


def test_a_write_reply_WITHOUT_the_advisory_is_UNTRUSTED(monkeypatch, capsys):
    _serve(monkeypatch, _WritePipe("OK", "the game is PAUSED"))
    assert C.main(["pause"]) == 3


def test_the_advisory_must_be_the_FIRST_field_not_merely_present(monkeypatch, capsys):
    _serve(monkeypatch, _WritePipe("OK", "prose first\t" + _adv()))
    assert C.main(["pause"]) == 3


def test_a_write_prints_its_BANNER_on_stderr_and_never_on_stdout(monkeypatch, capsys):
    """stdout gets piped into files and fixtures; a warning there becomes data."""
    _serve(monkeypatch, _WritePipe("OK", _adv() + "\tprose"))
    C.main(["pause"])
    out, err = capsys.readouterr()
    lines = err.splitlines()
    assert lines.count(BANNER) == 2, err
    block = err.split(BANNER)[1]
    assert "x4live pause" in block
    assert BANNER not in out


def test_the_banner_is_printed_even_when_the_game_cannot_be_reached(monkeypatch, capsys):
    from x4validate import _livepipe

    def unreachable(p, timeout):
        raise _livepipe.LiveQueryUnavailable("the game never connected")

    monkeypatch.setattr(C, "_live_open", unreachable)
    assert C.main(["pause"]) == 2
    err = capsys.readouterr().err
    assert BANNER in err.splitlines()
    assert "may already have" not in err.lower(), (
        "nothing was sent, so it must not suggest the write might have landed")


def test_a_reply_LOST_after_sending_says_the_write_MAY_HAVE_LANDED(monkeypatch, capsys):
    """`ask` writes the command BEFORE it reads the reply and never resends, so a lost
    reply is not a lost write. The only honest next step is a read."""
    from x4validate import _livepipe

    pipe = _WritePipe(raises=_livepipe.LiveQueryUnavailable("no reply within 1s"))
    _serve(monkeypatch, pipe)
    assert C.main(["pause"]) == 2
    err = capsys.readouterr().err
    assert "may already have" in err.lower() and "x4live pausestate" in err, err
    assert pipe.asks == [("pause", ())]


@pytest.mark.parametrize("verb", ["pause", "unpause"])
def test_a_write_sends_its_verb_EXACTLY_once_with_NO_arguments(verb, monkeypatch, capsys):
    pipe = _WritePipe("OK", _adv(verb=verb) + "\tprose")
    _serve(monkeypatch, pipe)
    C.main([verb])
    assert pipe.asks == [(verb, ())]


def test_the_python_write_verb_list_is_exactly_pause_and_unpause():
    assert set(C.WRITE_VERBS) == {"pause", "unpause"}


@pytest.mark.parametrize("verb", ["pause", "unpause"])
def test_query_REFUSES_a_write_verb_BEFORE_opening_the_pipe(verb, monkeypatch, capsys):
    """`query` passes its verb through as free text. Without this refusal it would be a
    second, bannerless door to every write."""
    def must_not_open(p, timeout):
        raise AssertionError("query opened the pipe for a WRITE verb")

    monkeypatch.setattr(C, "_live_open", must_not_open)
    assert C.main(["query", verb]) == 2
    assert f"x4live {verb}" in capsys.readouterr().err


def test_query_still_passes_a_READ_verb_through(monkeypatch, capsys):
    """The twin: a refusal that swallowed every verb would pass the test above."""
    pipe = _WritePipe("OK", "pong\t1\t5.0")
    _serve(monkeypatch, pipe)
    assert C.main(["query", "ping"]) == 0
    assert pipe.asks == [("ping", ())]


@pytest.mark.parametrize("status,rc", [("OK", 0), ("ERR", 1)])
def test_pausestate_exit_code_follows_the_engine_answer(status, rc, monkeypatch, capsys):
    pipe = _WritePipe(status, "paused=false owner=none build=deadbeef\trunning")
    _serve(monkeypatch, pipe)
    assert C.main(["pausestate"]) == rc
    out, err = capsys.readouterr()
    assert pipe.asks == [("pausestate", ())]
    assert BANNER not in err.splitlines(), "pausestate is a READ and must not announce a write"


def test_a_write_reply_for_a_DIFFERENT_verb_is_UNTRUSTED(monkeypatch, capsys):
    """An `unpause` advisory answering a `pause` request is a desync, not a result."""
    _serve(monkeypatch, _WritePipe("OK", _adv(verb="unpause") + "\tprose"))
    assert C.main(["pause"]) == 3


def test_an_INTERRUPTED_wait_after_sending_says_the_write_MAY_HAVE_LANDED(monkeypatch, capsys):
    """Ctrl-C while waiting for the reply is the same situation as a lost reply."""
    _serve(monkeypatch, _WritePipe(raises=KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        C.main(["pause"])
    assert "may already have" in capsys.readouterr().err.lower()


# --- finding 9: the "foreground only" claim was superseded by measurement -------------

def test_with_ramp_help_no_longer_claims_the_game_runs_ONLY_in_the_foreground(capsys):
    """README (2026-08-30/09-20, MEASURED): unfocused/alt-tabbed-away costs nothing --
    only MINIMIZING the window stops the engine. `--with-ramp`'s own help still said
    "the game executes only in the foreground", which is the superseded claim."""
    with pytest.raises(SystemExit) as exc:
        C.main(["groundtruth", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "only in the foreground" not in out, out
    assert "MINIMIZ" in out.upper(), out


@pytest.mark.parametrize("exc", [ValueError("incompatible effective-store schema: 7; expected 8"),
                                 __import__("sqlite3").DatabaseError("file is not a database")])
def test_mappings_REFUSES_an_unreadable_store_instead_of_a_traceback(tmp_path, monkeypatch, capsys, exc):
    """v4.0.0 review R5-4: `_connect` reports a missing/incompatible store with ValueError
    (it used to SystemExit), and cmd_mappings called it bare -- a traceback, where cmd_oracle
    two screens up refuses rc 2 naming the store."""
    import io
    from x4validate import _effective as E
    store_path = tmp_path / "effective.sqlite3"
    store_path.write_bytes(b"")
    monkeypatch.setattr(E, "effective_db", lambda: store_path)

    def boom(db):
        raise exc
    monkeypatch.setattr(E, "_connect", boom)
    rc = C.cmd_mappings(None, out=io.StringIO(),
                        groundtruth=str(_gt(tmp_path, ["t\tm1\tfoo\t100"])))
    assert rc == 2
    assert "could not be opened" in capsys.readouterr().err


# --- v4.0.0 delta review: the store connection is closed on EVERY path --------- #

def _stub_store_for(monkeypatch, tmp_path, freshness):
    from x4validate import _effective as E
    store_path = tmp_path / "effective.sqlite3"
    store_path.write_bytes(b"")
    con = _StubCon()
    monkeypatch.setattr(E, "effective_db", lambda: store_path)
    monkeypatch.setattr(E, "_connect", lambda db: con)
    monkeypatch.setattr(E, "store_freshness", freshness)
    monkeypatch.setattr(C, "_store_props", lambda c, macro: {"x.y": "100"})
    return con


def _raises(con):
    raise __import__("sqlite3").DatabaseError("file is not a database")


def _stale(con):
    return type("F", (), {"fresh": False, "banner": lambda self, w: "STALE"})()


def _fresh(con):
    return type("F", (), {"fresh": True, "banner": lambda self, w: ""})()


@pytest.mark.parametrize("cmd", ["cmd_mappings", "cmd_oracle"])
@pytest.mark.parametrize("freshness,want_rc", [(_raises, 2), (_stale, 3), (_fresh, None)],
                         ids=["freshness-raises", "stale", "fresh"])
def test_the_store_connection_is_CLOSED_on_every_path(tmp_path, monkeypatch, capsys,
                                                      cmd, freshness, want_rc):
    """Delta review C: `_connect` succeeded, then `store_freshness` raised -- and the
    connection was never closed (nor on the stale refusal, nor on success). Each path is
    its own case, so a close added to one branch only cannot pass the others; the
    raising case also pins the refusal (rc 2), where cmd_oracle used to traceback."""
    import io
    con = _stub_store_for(monkeypatch, tmp_path, freshness)
    rc = getattr(C, cmd)(None, out=io.StringIO(),
                         groundtruth=str(_gt(tmp_path, ["t	m1	foo	100"])))
    if want_rc is not None:
        assert rc == want_rc, (rc, capsys.readouterr().err)
    assert con.closed == 1, f"{cmd}: connection closed {con.closed} time(s) on this path"


def test_automation_after_global_file_option_refuses_cleanly(tmp_path, capsys):
    # Review r1-2: `x4live --file X automation` skipped the argv[0] hand-off and fell
    # through to cmd_mappings, which raised AttributeError (no from_groundtruth).
    assert C.main(["--file", str(tmp_path / "absent.xml"), "automation"]) == 2
    assert "x4live automation" in capsys.readouterr().err


def test_mappings_still_reaches_cmd_mappings(tmp_path):
    # Twin: the explicit mappings branch still runs (absent file -> its own rc 2).
    assert C.main(["--file", str(tmp_path / "absent.xml"), "mappings"]) == 2
