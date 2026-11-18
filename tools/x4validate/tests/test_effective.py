"""End-to-end: enumerate -> merge -> extract -> store -> query, with provenance."""

import os
import sqlite3

import pytest

from x4validate import _effective, _effectivecli, _merge, _mutation


def _world(tmp_path):
    """A mini game: reference (wares + one base engine macro), one DLC, two mods:
    modA diffs the base engine thrust; modB adds a brand-new macro file."""
    ref = tmp_path / "reference"
    (ref / "libraries").mkdir(parents=True)
    (ref / "libraries" / "wares.xml").write_bytes(
        b'<wares><ware id="ore" group="minerals"><price min="1" average="2" max="3"/></ware></wares>')
    eng = ref / "assets" / "props" / "engines" / "macros"
    eng.mkdir(parents=True)
    (eng / "engine_arg_s_01_macro.xml").write_bytes(
        b'<macros><macro name="engine_arg_s_01_macro" class="engine">'
        b'<properties><thrust forward="100" reverse="30"/></properties></macro></macros>')
    # a DLC that unions in another ware
    dlc = ref / "extensions" / "ego_dlc_split" / "libraries"
    dlc.mkdir(parents=True)
    (dlc.parent / "libraries" / "wares.xml").write_bytes(
        b'<wares><ware id="spice" group="minerals"><price min="5" average="9" max="12"/></ware></wares>')

    exts = tmp_path / "extensions"
    # modA: diff the base engine thrust
    a = exts / "aaa_thrust" / "assets" / "props" / "engines" / "macros"
    a.mkdir(parents=True)
    (a / "engine_arg_s_01_macro.xml").write_bytes(
        b'<diff><replace sel="//macro[@name=\'engine_arg_s_01_macro\']/properties/thrust/@forward">'
        b'250</replace></diff>')
    (exts / "aaa_thrust" / "content.xml").write_bytes(
        b'<content id="aaa_thrust" name="A" version="1"/>')
    # modB: add a new engine macro file of its own
    b = exts / "bbb_newengine" / "assets" / "props" / "engines" / "macros"
    b.mkdir(parents=True)
    (b / "engine_new_01_macro.xml").write_bytes(
        b'<macros><macro name="engine_new_01_macro" class="engine">'
        b'<properties><thrust forward="900"/></properties></macro></macros>')
    (exts / "bbb_newengine" / "content.xml").write_bytes(
        b'<content id="bbb_newengine" name="B" version="1"/>')
    return ref, exts


def _no_mods(tmp_path):
    """An EMPTY extensions root. `dirs=[]` means "the configured roots" (scan_installed's
    `dirs or default_installed_dirs()`), so on a machine with X4 these tests used to merge,
    and since toolkit 4.3 also snapshot, the whole real install (~44 s each, MEASURED)."""
    d = tmp_path / "no-mods"
    d.mkdir(exist_ok=True)
    return d


def _build(tmp_path, monkeypatch):
    ref, exts = _world(tmp_path)
    # active_mods() reads a profile content.xml; force "no profile" -> all enabled.
    monkeypatch.setattr(_effective._registry, "ingest_content_xml",
                        lambda *a, **k: [])
    db = tmp_path / "eff.sqlite"
    _effective.build(_merge.Config(reference=ref), db, dirs=[exts],
                     kinds=("ware", "macro"))
    return db


def test_build_and_provenance(tmp_path, monkeypatch):
    db = _build(tmp_path, monkeypatch)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    # base engine thrust was overridden by modA
    row = con.execute(
        "SELECT a.value, a.origin, a.chain FROM attrs a JOIN entities e ON e.id=a.entity_id "
        "WHERE e.name='engine_arg_s_01_macro' AND a.prop='thrust.forward'").fetchone()
    assert row["value"] == "250"
    assert row["origin"] == "aaa_thrust"
    assert row["chain"] is not None  # modified -> chain recorded

    # untouched sibling attr stays base (no chain)
    rev = con.execute(
        "SELECT a.chain AS chain FROM attrs a JOIN entities e ON e.id=a.entity_id "
        "WHERE e.name='engine_arg_s_01_macro' AND a.prop='thrust.reverse'").fetchone()
    assert rev["chain"] is None

    # modB's new macro is present and attributed to modB
    nb = con.execute("SELECT origin FROM entities WHERE name='engine_new_01_macro'").fetchone()
    assert nb["origin"] == "bbb_newengine"

    # DLC-unioned ware coexists with base ware
    wares = {r[0] for r in con.execute("SELECT name FROM entities WHERE kind='ware'")}
    assert {"ore", "spice"} <= wares


def test_cli_queries(tmp_path, monkeypatch, capsys):
    db = _build(tmp_path, monkeypatch)
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])

    assert _effectivecli.main(["--db", str(db), "ls", "macro", "--class", "engine"]) == 0
    out = capsys.readouterr().out
    assert "engine_arg_s_01_macro" in out and "engine_new_01_macro" in out

    assert _effectivecli.main(["--db", str(db), "attr", "macro", "thrust.forward",
                            "--class", "engine", "--sort", "num"]) == 0
    out = capsys.readouterr().out
    assert "250" in out and "900" in out

    assert _effectivecli.main(["--db", str(db), "who-sets", "macro",
                            "engine_arg_s_01_macro", "thrust.forward"]) == 0
    assert "aaa_thrust" in capsys.readouterr().out

    assert _effectivecli.main(["--db", str(db), "diff-mod", "aaa_thrust"]) == 0
    assert "thrust.forward" in capsys.readouterr().out


def test_sql_rejects_writes(tmp_path, monkeypatch, capsys):
    db = _build(tmp_path, monkeypatch)
    assert _effectivecli.main(["--db", str(db), "sql", "DELETE FROM entities"]) == 2


# --------------------------------------------------------------------------
# build-time guards, both found 2026-08-09 by racing builders in a concurrency
# probe (the probe's own first version silently stopped testing the race
# because it passed the newly-rejected kind -- which is how #2 surfaced).
# --------------------------------------------------------------------------

def test_build_rejects_an_unknown_kind(tmp_path, monkeypatch):
    """`--kinds shieldgenerator` (a CLASS, not a kind) used to write an empty
    store and exit 0, so every later query answered from an empty database and
    nothing ever said the name was wrong. The read side already refuses this."""
    import pytest
    ref = tmp_path / "reference"
    (ref / "libraries").mkdir(parents=True)
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    with pytest.raises(ValueError) as exc:
        _effective.build(_merge.Config(reference=ref), tmp_path / "x.sqlite",
                         dirs=[], kinds=("shieldgenerator",))
    msg = str(exc.value)
    assert "shieldgenerator" in msg
    for kind in _effective.BUILDABLE_KINDS:
        assert kind in msg          # tells the user what IS buildable


def test_build_accepts_every_advertised_kind(tmp_path, monkeypatch):
    """Both sides asserted: a guard that rejected everything would pass the test
    above. BUILDABLE_KINDS must be exactly what build() will accept."""
    ref = tmp_path / "reference"
    (ref / "libraries").mkdir(parents=True)
    (ref / "libraries" / "wares.xml").write_bytes(b"<wares/>")
    (ref / "libraries" / "jobs.xml").write_bytes(b"<jobs/>")
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    db = _effective.build(_merge.Config(reference=ref), tmp_path / "all.sqlite",
                          dirs=[_no_mods(tmp_path)], kinds=_effective.BUILDABLE_KINDS)
    assert db.is_file()


def test_build_temp_file_is_unique_per_process(tmp_path, monkeypatch):
    """Two concurrent builds picked the SAME `<db>.tmp` and raced on os.replace,
    so BOTH died with a raw PermissionError (WinError 32) and no store was
    installed at all. Measured after the fix: 3 racers, all rc=0, store intact."""
    ref = tmp_path / "reference"
    (ref / "libraries").mkdir(parents=True)
    (ref / "libraries" / "jobs.xml").write_bytes(b"<jobs/>")
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])

    seen = []
    real_replace = _effective.os.replace

    def spy(src, dst):
        seen.append(str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(_effective.os, "replace", spy)
    db = tmp_path / "eff.sqlite"
    _effective.build(_merge.Config(reference=ref), db, dirs=[_no_mods(tmp_path)], kinds=("job",))
    assert seen, "build never installed a temp file"
    assert str(os.getpid()) in seen[0], f"temp path not process-unique: {seen[0]}"
    assert not list(tmp_path.glob("*.tmp")), "temp file left behind"


def test_a_build_that_FAILS_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    """The NEGATIVE twin of the assertion above, which only covered the happy path.

    `_write_db` creates the temp at `sqlite3.connect` and its `try:` carried only
    `finally: con.close()` -- which closes the handle and never unlinks the file.
    So every exception between creation and `os.replace` leaked a fully
    materialised temp permanently, while the ONLY cleanup sat in the `os.replace`
    OSError handler.

    The sharpest case is architectural rather than exotic:
    `_mutation.refuse_if_mutating()` is called INSIDE that block by design, so any
    store build overlapping a mutating gate deposited one. MEASURED in the live
    registry: three orphans from 2026-08-26 (12:50, 12:52, 12:54), each with zero
    rows in every table INCLUDING meta -- so they died before `con.commit()`,
    which is what makes them a leak rather than a corrupted store.

    Injecting at `stamp_sqlite` rather than at `refuse_if_mutating` deliberately:
    the guarantee is "no exception leaks a temp", not "this particular guard does
    not", and pinning the specific caller would pass while the next one added
    inside the block leaked again.
    """
    ref = tmp_path / "reference"
    (ref / "libraries").mkdir(parents=True)
    (ref / "libraries" / "jobs.xml").write_bytes(b"<jobs/>")
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])

    def _boom(*a, **k):
        raise RuntimeError("injected mid-build failure")

    monkeypatch.setattr(_effective._freshness, "stamp_sqlite", _boom)
    db = tmp_path / "eff.sqlite"
    with pytest.raises(RuntimeError, match="injected mid-build failure"):
        _effective.build(_merge.Config(reference=ref), db, dirs=[_no_mods(tmp_path)], kinds=("job",))

    leaked = list(tmp_path.glob("*.tmp"))
    assert not leaked, (
        "a failed build left %d temp file(s) behind: %s -- and the startup reclaim "
        "is os.getpid()-scoped, so no later run can ever collect them"
        % (len(leaked), [p.name for p in leaked]))


def test_the_ORIGINAL_failure_is_what_propagates(tmp_path, monkeypatch):
    """Cleanup must not mask the diagnosis the caller actually needs.

    `_registry.save` states the rule at its own equivalent block: the real failure
    is re-raised, so nothing is hidden. A cleanup that swallowed the error would
    turn a refused build into a silent one.
    """
    ref = tmp_path / "reference"
    (ref / "libraries").mkdir(parents=True)
    (ref / "libraries" / "jobs.xml").write_bytes(b"<jobs/>")
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])

    def _boom(*a, **k):
        raise _mutation.TreeMutating("refusing to build: a mutating gate is running")

    monkeypatch.setattr(_effective._freshness, "stamp_sqlite", _boom)
    db = tmp_path / "eff.sqlite"
    with pytest.raises(_mutation.TreeMutating, match="mutating gate"):
        _effective.build(_merge.Config(reference=ref), db, dirs=[_no_mods(tmp_path)], kinds=("job",))
    assert not list(tmp_path.glob("*.tmp"))


# --------------------------------------------------------------------------
# who-sets with no property (cold E2E agent, 2026-09-14): asked who set a ware, it
# answered `base` -- the ENTITY's origin -- while `show` revealed price.min and
# price.max set by a mod. A narrowing that does not announce itself.
# --------------------------------------------------------------------------

def test_who_sets_without_a_prop_names_the_origins_of_changed_properties(tmp_path, monkeypatch, capsys):
    db = _build(tmp_path, monkeypatch)
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    assert _effectivecli.main(["--db", str(db), "who-sets", "macro", "engine_arg_s_01_macro"]) == 0
    out = capsys.readouterr().out
    assert "1 of " in out, out
    assert "aaa_thrust" in out and "thrust.forward" in out, out


def test_TWIN_who_sets_without_a_prop_says_so_when_nothing_changed(tmp_path, monkeypatch, capsys):
    """The twin: an all-base entity must be STATED as all-base, not merely left unsaid --
    and the count must not be inflated into a claim that something changed."""
    db = _build(tmp_path, monkeypatch)
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    assert _effectivecli.main(["--db", str(db), "who-sets", "ware", "ore"]) == 0
    out = capsys.readouterr().out
    # Leading spaces: "10 of 10" also contains "0 of " (review, 2026-09-14).
    assert "  0 of " in out, out
    assert "no removal is recorded" in out, out


def test_who_sets_does_not_call_an_entity_all_base_when_a_mod_REMOVED_part_of_it(tmp_path, monkeypatch, capsys):
    """A removal is not stored as a property (it goes to the `removed` table), so counting
    properties alone printed "every property is still its base value" for an entity a mod had
    removed an attribute from (review, 2026-09-14, reproduced on this fixture)."""
    ref, exts = _world(tmp_path)
    rm = exts / "ccc_remove" / "assets" / "props" / "engines" / "macros"
    rm.mkdir(parents=True)
    (rm / "engine_arg_s_01_macro.xml").write_bytes(
        b'<diff><remove sel="//macro[@name=\'engine_arg_s_01_macro\']/properties/thrust/@reverse"/></diff>')
    (exts / "ccc_remove" / "content.xml").write_bytes(b'<content id="ccc_remove" name="C" version="1"/>')
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    db = tmp_path / "eff.sqlite"
    _effective.build(_merge.Config(reference=ref), db, dirs=[exts], kinds=("ware", "macro"))
    assert _effectivecli.main(["--db", str(db), "who-sets", "macro", "engine_arg_s_01_macro"]) == 0
    out = capsys.readouterr().out
    assert "removal" in out and "ccc_remove" in out, out
    assert "every stored property is its base value" not in out, out


# --- AN-3: "modified" = a MOD in the entity's, an attribute's, or a sole-entity
#     file's removal provenance -- never a DLC, never a removal in a shared file ---

def test_modified_sql_counts_a_sole_entity_removal_but_not_a_shared_file_one():
    import sqlite3
    from x4validate import _effectivecli
    con = sqlite3.connect(":memory:")
    con.executescript(_effective._SCHEMA)
    con.execute("INSERT INTO mods VALUES('m', 'm', 'm', '1', 0, 1, 0)")
    con.executemany("INSERT INTO entities VALUES(?,?,?,?,?,?,?)", [
        (1, "macro", "solo_macro", "ship_s", "a/solo.xml", "base", None),
        (2, "ware", "w1", "g", "libraries/wares.xml", "base", None),
        (3, "ware", "w2", "g", "libraries/wares.xml", "base", None),
        (4, "ware", "dlc", "g", "libraries/wares.xml", "ego_dlc_x",
         '[["ego_dlc_x", "union-add", 0]]'),
    ])
    con.executemany("INSERT INTO removed VALUES(?,?,?,?)", [
        ("a/solo.xml", "/macros/macro/properties/hull/@max", "m", 1),
        ("libraries/wares.xml", "/wares/ware[1]/@tags", "m", 1),
    ])
    got = {r[0] for r in con.execute(
        "SELECT name FROM entities WHERE " + _effectivecli._MODIFIED_SQL)}
    assert got == {"solo_macro"}, got


# --- finding 4: `--limit 0` must mean unlimited, matching the tool's own advice ------
#
# `_count_line`'s TRUNCATED message literally says "use --limit N (or 0) for all", but
# `args.limit` was passed straight through as a SQL `LIMIT ?` parameter, where 0 means
# LIMIT 0 -- zero rows. Following the tool's own advice used to produce nothing.

def test_ls_limit_0_means_unlimited_not_zero_rows(tmp_path, monkeypatch, capsys):
    db = _build(tmp_path, monkeypatch)
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    assert _effectivecli.main(["--db", str(db), "ls", "macro", "--limit", "0"]) == 0
    out = capsys.readouterr().out
    assert "engine_arg_s_01_macro" in out and "engine_new_01_macro" in out, out
    assert "TRUNCATED" not in out, out


def test_attr_limit_0_means_unlimited_not_zero_rows(tmp_path, monkeypatch, capsys):
    db = _build(tmp_path, monkeypatch)
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    assert _effectivecli.main(
        ["--db", str(db), "attr", "macro", "thrust.forward", "--limit", "0"]) == 0
    out = capsys.readouterr().out
    assert "engine_arg_s_01_macro" in out and "engine_new_01_macro" in out, out
    assert "TRUNCATED" not in out, out


def test_diff_mod_limit_0_means_unlimited_not_zero_rows(tmp_path, monkeypatch, capsys):
    db = _build(tmp_path, monkeypatch)
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    assert _effectivecli.main(["--db", str(db), "diff-mod", "aaa_thrust", "--limit", "0"]) == 0
    out = capsys.readouterr().out
    assert "thrust.forward" in out, out
    assert "TRUNCATED" not in out, out


@pytest.mark.parametrize("cmd", [
    ["ls", "macro", "--limit", "1"],
    ["attr", "macro", "thrust.forward", "--limit", "1"],
])
def test_a_real_positive_limit_still_truncates(tmp_path, monkeypatch, capsys, cmd):
    """Twin: `--limit 0` is special-cased, but an ordinary positive limit must still
    behave exactly as before. (diff-mod isn't exercised here: this fixture's
    aaa_thrust owns only one value, so --limit 1 would never truncate it either way.)"""
    db = _build(tmp_path, monkeypatch)
    monkeypatch.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    assert _effectivecli.main(["--db", str(db)] + cmd) == 0
    out = capsys.readouterr().out
    assert "1 of" in out and "TRUNCATED" in out, out
