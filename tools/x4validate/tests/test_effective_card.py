"""x4effective card: one equipment macro's identity, each value with the source that set it.

Why it exists (2026-11-14): a session labelled a turret "point defence" from its bullet's stat shape at
70% confidence; in game it was an artillery turret, weapon system `turret_longrange`. Every fact needed to
classify it was in the store, three lookups apart (name text, bullet.class -> the bullet's weapon.system,
the ware), and the lookups were skipped. Joining them by hand through `sql` also hit text rows holding
TABs. The card does the joins and labels every value: resolved, absent (the store holds the thing and it
has no such value) or NOT RESOLVED (a reference the store cannot follow) -- never blank.

Every fixture id is synthetic.
"""
from __future__ import annotations

import json

import pytest

from x4validate import _effective, _effectivecli, _merge

TEXTS = (
    b'<language id="44"><page id="20105">'
    b'<t id="1">Test Laser</t><t id="2">A synthetic gun.</t>'
    b'<t id="11">(label)Test Artillery Turret</t>'
    b'<t id="12">Long range.&#9;Slow.&#10;Second line with a \\ backslash.</t>'
    b'<t id="21">Test Missile</t><t id="31">Test Shield</t>'
    b'</page></language>')


def _macro(name, klass, body=""):
    return (f'<macros><macro name="{name}" class="{klass}"><component ref="{name[:-6]}"/>'
            f'<properties>{body}</properties></macro></macros>').encode()


def _component(name, klass, conn, tags):
    return (f'<components><component name="{name}" class="{klass}"><connections>'
            f'<connection name="container" tags="contents" value="0"/>'
            f'<connection name="{conn}" tags="{tags}"/>'
            f'<connection name="con_barrel_01" tags="laser "/>'
            f'</connections></component></components>').encode()


def _world(tmp_path):
    ref = tmp_path / "reference"
    (ref / "t").mkdir(parents=True)
    (ref / "t" / "0001-l044.xml").write_bytes(TEXTS)
    ws = ref / "assets" / "props" / "WeaponSystems" / "test"
    (ws / "macros").mkdir(parents=True)
    macros = {
        "weapon_test_m_laser_macro": ("weapon", '<identification name="{20105,1}" description="{20105,2}"/>'
                                                '<bullet class="bullet_test_laser_macro"/>'),
        "bullet_test_laser_macro": ("bullet", '<weapon system="weapon_standard"/>'),
        "turret_test_m_art_macro": ("turret", '<identification name="{20105,11}" description="{20105,12}"/>'
                                              '<bullet class="bullet_test_art_macro"/>'),
        "bullet_test_art_macro": ("bullet", '<weapon system="turret_longrange"/>'),
        "turret_test_m_orphan_macro": ("turret", '<identification name="{20105,99}"/>'
                                                 '<bullet class="bullet_not_in_store_macro"/>'),
        "missile_test_macro": ("missile", '<identification name="{20105,21}"/><weapon system="missile_guided"/>'),
        "weapon_test_m_launcher_macro": ("missilelauncher", '<identification name="{20105,1}"/>'
                                                            '<ammunition tags="missile medium"/>'
                                                            '<bullet class="missile_test_macro"/>'),
        "turret_test_m_missile_noammo_macro": ("missileturret", '<identification name="{20105,21}"/>'
                                                                '<bullet class="missile_test_macro"/>'),
        "weapon_test_m_ammo_macro": ("weapon", '<identification name="{20105,1}"/><ammunition tags="missile"/>'
                                               '<bullet class="missile_test_macro"/>'),
        "shield_test_m_macro": ("shieldgenerator", '<identification name="{20105,31}"/>'
                                                   '<recharge max="100" rate="10" delay="1"/>'),
    }
    for name, (klass, body) in macros.items():
        (ws / "macros" / f"{name}.xml").write_bytes(_macro(name, klass, body))
    for name, klass, conn, tags in (
            ("weapon_test_m_laser", "weapon", "con_weapon_01", "medium weapon component "),
            ("turret_test_m_art", "turret", "con_turret_01", "advanced combat component medium turret "),
            ("shield_test_m", "shieldgenerator", "ParentConnection", "component medium shield ")):
        (ws / f"{name}.xml").write_bytes(_component(name, klass, conn, tags))
    (ref / "libraries").mkdir()
    (ref / "libraries" / "wares.xml").write_bytes(
        b'<wares>'
        b'<ware id="turret_test_m_art" name="{20105,11}" group="turrets" tags="equipment turret">'
        b'<price min="100" average="200" max="300"/><component ref="turret_test_m_art_macro"/></ware>'
        b'<ware id="weapon_test_m_laser" name="{20105,1}" group="weapons" tags="equipment weapon">'
        b'<price min="10" average="20" max="30"/><component ref="weapon_test_m_laser_macro"/></ware>'
        b'<ware id="weapon_test_m_laser_variant" name="{20105,1}" group="weapons" tags="equipment weapon">'
        b'<price min="11" average="21" max="31"/><component ref="weapon_test_m_laser_macro"/></ware>'
        b'</wares>')
    (ref / "libraries" / "loadouts.xml").write_bytes(
        b'<loadouts><loadout id="test_loadout_a" macro="ship_test_m_macro"><macros>'
        b'<turret macro="turret_test_m_art_macro" path="../con_turret_01"/>'
        b'<turret macro="turret_test_m_art_macro" path="../con_turret_02"/>'
        b'</macros></loadout></loadouts>')
    ships = ref / "assets" / "units" / "size_m" / "macros"
    ships.mkdir(parents=True)
    (ships / "ship_test_m_macro.xml").write_bytes(
        b'<macros><macro name="ship_test_m_macro" class="ship_m"><component ref="ship_test_m"/>'
        b'<connections><connection ref="con_shield_01"><macro ref="shield_test_m_macro" connection="ShieldConnection"/>'
        b'</connection></connections></macro></macros>')

    exts = tmp_path / "extensions"
    mod = exts / "zz_overrider"
    (mod / "libraries").mkdir(parents=True)
    (mod / "content.xml").write_bytes(b'<content id="zz_overrider" name="Z" version="1"/>')
    (mod / "libraries" / "wares.xml").write_bytes(
        b"<diff><replace sel=\"//ware[@id='turret_test_m_art']/price/@max\">999</replace></diff>")
    return ref, exts


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("card")
    ref, exts = _world(tmp_path)
    mp = pytest.MonkeyPatch()
    mp.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    path = tmp_path / "eff.sqlite"
    try:
        _effective.build(_merge.Config(reference=ref), path, dirs=[exts],
                         kinds=("macro", "component", "ware", "loadout"))
    finally:
        mp.undo()
    return path


def _card(db, capsys, name, *extra):
    rc = _effectivecli.main(["--db", str(db), "card", name, *extra])
    out, err = capsys.readouterr()
    return rc, out, err


def _json(db, capsys, name):
    rc, out, _ = _card(db, capsys, name, "--json")
    return rc, json.loads(out)


# --------------------------------------------------------------------------- the classification facts

def test_a_turret_names_its_bullet_and_the_bullets_weapon_system(db, capsys):
    rc, doc = _json(db, capsys, "turret_test_m_art_macro")
    assert rc == 0
    assert doc["schema"] == "x4effective.card/1"
    assert doc["class"] == "turret"
    f = doc["fields"]
    assert f["fires"]["value"] == "bullet_test_art_macro" and f["fires"]["status"] == "ok"
    assert f["system"]["value"] == "turret_longrange" and f["system"]["status"] == "ok"
    assert f["system"]["via"] == "bullet_test_art_macro"
    assert f["name"]["text"] == "Test Artillery Turret" and f["name"]["raw"] == "{20105,11}"


def test_a_weapon_is_classified_the_same_way(db, capsys):
    rc, doc = _json(db, capsys, "weapon_test_m_laser_macro")
    assert rc == 0 and doc["fields"]["system"]["value"] == "weapon_standard"
    assert doc["fields"]["fitting"]["value"][0]["size"] == "medium"


def test_a_missile_carries_its_own_weapon_system_and_fires_nothing(db, capsys):
    rc, doc = _json(db, capsys, "missile_test_macro")
    f = doc["fields"]
    assert rc == 0 and f["system"]["value"] == "missile_guided" and "via" not in f["system"]
    assert f["fires"]["status"] == "absent" and f["fires"]["value"] is None


def test_a_shield_says_its_class_has_no_system_field_rather_than_blank(db, capsys):
    rc, doc = _json(db, capsys, "shield_test_m_macro")
    f = doc["fields"]
    assert rc == 0 and f["system"]["status"] == "absent" and f["system"]["why"]
    assert f["fitting"]["value"] == [{"connection": "ParentConnection", "tags": "component medium shield ",
                                      "size": "medium", "origin": "base", "set_by": "vanilla"}]


def test_a_bullet_the_store_does_not_hold_is_NOT_RESOLVED_not_absent(db, capsys):
    """Twin of the turret: same shape, but the reference cannot be followed. Absence and non-answer differ."""
    rc, doc = _json(db, capsys, "turret_test_m_orphan_macro")
    f = doc["fields"]
    assert rc == 0
    assert f["fires"]["status"] == "ok" and f["fires"]["value"] == "bullet_not_in_store_macro"
    assert f["system"]["status"] == "unresolved" and "bullet_not_in_store_macro" in f["system"]["why"]
    assert f["name"]["status"] == "unresolved"              # {20105,99} is dangling
    assert f["fitting"]["status"] == "unresolved"           # no component file in the store
    assert f["ware"]["status"] == "absent"                  # wares were indexed; none points at it


# --------------------------------------------------------------------------- ware, price, origin

def test_the_ware_and_its_price_name_the_mod_that_set_each_value(db, capsys):
    rc, doc = _json(db, capsys, "turret_test_m_art_macro")
    f = doc["fields"]
    assert f["ware"]["value"] == "turret_test_m_art" and f["ware"]["set_by"] == "vanilla"
    price = f["price"]["value"]
    assert price["min"] == {"value": "100", "origin": "base", "set_by": "vanilla"}
    assert price["max"] == {"value": "999", "origin": "zz_overrider", "set_by": "mod"}


def test_the_text_output_says_still_vanilla_and_names_the_mod(db, capsys):
    rc, out, _ = _card(db, capsys, "turret_test_m_art_macro")
    assert rc == 0
    price = next(ln for ln in out.splitlines() if ln.lstrip().startswith("price"))
    assert "999" in price and "mod zz_overrider" in price
    ware = next(ln for ln in out.splitlines() if ln.lstrip().startswith("ware"))
    assert "no override, still vanilla" in ware


# --------------------------------------------------------------------------- carriers

def test_carriers_list_the_loadout_and_its_ship_macro(db, capsys):
    rc, doc = _json(db, capsys, "turret_test_m_art_macro")
    car = doc["fields"]["carried_by"]
    assert car["status"] == "ok"
    assert {"kind": "loadout", "name": "test_loadout_a", "ship": "ship_test_m_macro", "slots": 2,
            "origin": "base", "set_by": "vanilla"} in car["value"]


def test_a_ship_macro_that_fits_it_directly_is_a_carrier(db, capsys):
    rc, doc = _json(db, capsys, "shield_test_m_macro")
    names = [(c["kind"], c["name"]) for c in doc["fields"]["carried_by"]["value"]]
    assert ("macro", "ship_test_m_macro") in names


def test_nothing_naming_it_is_NOT_a_measured_absence_of_carriers(db, capsys):
    """Review 2026-11-14: loadoutrules (most NPC fitting) are not indexed, so zero literal mentions in
    loadouts.xml and ship macros does not show that nothing carries the item."""
    rc, doc = _json(db, capsys, "missile_test_macro")
    car = doc["fields"]["carried_by"]
    assert car["status"] == "unresolved" and car["value"] == []
    assert "0 loadouts" in car["why"] and "loadoutrules" in car["why"] and "NOT evidence" in car["why"]


def test_the_carrier_population_is_stated_in_json_and_text(db, capsys):
    rc, doc = _json(db, capsys, "turret_test_m_art_macro")
    assert "loadoutrules" in doc["fields"]["carried_by"]["population"]
    rc, out, _ = _card(db, capsys, "turret_test_m_art_macro")
    line = next(ln for ln in out.splitlines() if ln.lstrip().startswith("carried by"))
    assert "1 loadout(s) in loadouts.xml" in line and "loadoutrules not indexed" in line


@pytest.fixture(scope="module")
def db_no_loadouts(tmp_path_factory):
    """The same world built WITHOUT kind 'loadout', as `x4effective build --kinds macro,component,ware`."""
    tmp_path = tmp_path_factory.mktemp("card_noload")
    ref, exts = _world(tmp_path)
    mp = pytest.MonkeyPatch()
    mp.setattr(_effective._registry, "ingest_content_xml", lambda *a, **k: [])
    path = tmp_path / "eff.sqlite"
    try:
        _effective.build(_merge.Config(reference=ref), path, dirs=[exts], kinds=("macro", "component", "ware"))
    finally:
        mp.undo()
    return path


def test_a_store_that_never_indexed_loadouts_is_NOT_RESOLVED(db_no_loadouts, capsys):
    """MEASURED by the reviewer: the guard `loadout not in kinds and macro not in kinds` could never fire,
    so a store built without loadouts reported carriers ABSENT about loadouts it never read."""
    rc, doc = _json(db_no_loadouts, capsys, "turret_test_m_art_macro")
    car = doc["fields"]["carried_by"]
    assert car["status"] == "unresolved" and "kind 'loadout'" in car["why"]


def test_the_same_store_still_finds_a_ship_macro_carrier(db_no_loadouts, capsys):
    """Twin: without loadouts the field is a non-answer, but a direct ship-macro mention is still shown."""
    rc, doc = _json(db_no_loadouts, capsys, "shield_test_m_macro")
    car = doc["fields"]["carried_by"]
    assert car["status"] == "unresolved"
    assert [(c["kind"], c["name"]) for c in car["value"]] == [("macro", "ship_test_m_macro")]


# --------------------------------------------------------------------------- review 2026-11-14

def _dup_bullet(db, tmp_path):
    import shutil
    import sqlite3
    copy = tmp_path / "dup.sqlite"
    shutil.copyfile(db, copy)
    con = sqlite3.connect(copy)
    eid, klass, origin = con.execute("SELECT id, klass, origin FROM entities WHERE name='bullet_test_art_macro'"
                                     ).fetchone()
    con.execute("INSERT INTO entities VALUES(?,?,?,?,?,?,?)",
                (9100, "macro", "bullet_test_art_macro", klass, "assets/other/bullet_test_art_macro.xml", origin, None))
    con.commit()
    con.close()
    return copy


def test_a_duplicated_bullet_gets_the_same_NOTE_as_a_duplicated_macro(db, capsys, tmp_path):
    rc, out, err = _card(_dup_bullet(db, tmp_path), capsys, "turret_test_m_art_macro", "--json")
    assert rc == 0
    assert "NOTE: 2 entities share (macro, 'bullet_test_art_macro')" in err
    assert json.loads(out)["fields"]["system"]["duplicates"] == 2


def test_a_unique_bullet_carries_no_duplicate_note(db, capsys):
    rc, out, err = _card(db, capsys, "turret_test_m_art_macro", "--json")
    assert "NOTE:" not in err and "duplicates" not in json.loads(out)["fields"]["system"]


def test_a_launcher_says_fires_is_only_the_default_and_ammunition_decides(db, capsys):
    rc, doc = _json(db, capsys, "weapon_test_m_launcher_macro")
    fires = doc["fields"]["fires"]
    assert "ammunition tags" in fires["caveat"] and fires["ammunition_tags"]["value"] == "missile medium"
    rc, out, _ = _card(db, capsys, "weapon_test_m_launcher_macro")
    assert any("ammunition tags decide" in ln for ln in out.splitlines())


def test_a_missile_turret_WITHOUT_ammunition_tags_still_gets_the_caveat_by_its_class(db, capsys):
    """The class clause on its own (review 2026-11-14): with no ammunition tags, only LAUNCHER_CLASSES
    can raise the caveat, so emptying that list must turn this red."""
    rc, doc = _json(db, capsys, "turret_test_m_missile_noammo_macro")
    fires = doc["fields"]["fires"]
    assert rc == 0 and "ammunition_tags" not in fires and "ammunition tags" in fires["caveat"]


def test_any_class_WITH_ammunition_tags_gets_the_caveat(db, capsys):
    """Twin, the other clause: a class outside LAUNCHER_CLASSES that still declares ammunition tags."""
    rc, doc = _json(db, capsys, "weapon_test_m_ammo_macro")
    assert rc == 0 and "ammunition tags" in doc["fields"]["fires"]["caveat"]


def test_a_turret_carries_no_launcher_caveat(db, capsys):
    rc, doc = _json(db, capsys, "turret_test_m_art_macro")
    assert "caveat" not in doc["fields"]["fires"]


def test_card_and_sql_json_carry_the_freshness_verdict(db, capsys, monkeypatch):
    from x4validate import _freshness
    monkeypatch.setattr(_effectivecli, "store_freshness",
                        lambda con: _freshness.Verdict(False, ["content axis UNKNOWN: test"]))
    rc, doc = _json(db, capsys, "turret_test_m_art_macro")
    assert doc["freshness"] == {"fresh": False, "verdict": "STALE", "reasons": ["content axis UNKNOWN: test"]}
    rc = _effectivecli.main(["--db", str(db), "sql", "SELECT 1", "--json"])
    assert json.loads(capsys.readouterr().out)["freshness"]["verdict"] == "STALE"


def test_a_fresh_store_says_FRESH_in_json(db, capsys, monkeypatch):
    from x4validate import _freshness
    monkeypatch.setattr(_effectivecli, "store_freshness", lambda con: _freshness.Verdict(True, []))
    rc, doc = _json(db, capsys, "turret_test_m_art_macro")
    assert doc["freshness"] == {"fresh": True, "verdict": "FRESH", "reasons": []}


def test_the_price_names_its_ware_when_several_wares_point_at_the_macro(db, capsys):
    rc, doc = _json(db, capsys, "weapon_test_m_laser_macro")
    assert doc["fields"]["ware"]["also"] == ["weapon_test_m_laser_variant"]
    assert doc["fields"]["price"]["ware"] == "weapon_test_m_laser"
    rc, out, _ = _card(db, capsys, "weapon_test_m_laser_macro")
    price = next(ln for ln in out.splitlines() if ln.lstrip().startswith("price"))
    assert "for ware weapon_test_m_laser" in price


def test_a_single_ware_price_line_is_not_cluttered(db, capsys):
    rc, out, _ = _card(db, capsys, "turret_test_m_art_macro")
    price = next(ln for ln in out.splitlines() if ln.lstrip().startswith("price"))
    assert "for ware" not in price


# --------------------------------------------------------------------------- text safety

def test_a_description_with_a_tab_and_a_newline_is_exact_in_json(db, capsys):
    rc, doc = _json(db, capsys, "turret_test_m_art_macro")
    assert doc["fields"]["description"]["text"] == "Long range.\tSlow.\nSecond line with a \\ backslash."


def test_the_same_description_prints_on_ONE_quoted_escaped_line(db, capsys):
    rc, out, _ = _card(db, capsys, "turret_test_m_art_macro")
    line = next(ln for ln in out.splitlines() if ln.lstrip().startswith("description"))
    assert "\t" not in line
    assert '"Long range.\\tSlow.\\nSecond line with a \\\\ backslash."' in line
    assert "{20105,12}" in line
    assert not any(ln.startswith("Second line") for ln in out.splitlines())


# --------------------------------------------------------------------------- refusals

def test_an_unknown_macro_exits_1_and_says_it_is_not_proof(db, capsys):
    rc, out, err = _card(db, capsys, "no_such_test_macro")
    assert rc == 1 and out == ""
    assert "NOT proof" in err


def test_an_unknown_macro_under_json_is_still_exit_1_with_no_stdout(db, capsys):
    rc, out, err = _card(db, capsys, "no_such_test_macro", "--json")
    assert rc == 1 and out == "" and "NOT proof" in err


def test_a_ware_id_points_at_its_macro(db, capsys):
    rc, out, err = _card(db, capsys, "turret_test_m_art")
    assert rc == 1 and "turret_test_m_art_macro" in err


def test_every_field_is_present_and_none_is_blank(db, capsys):
    """The contract: each field has a status, and only `ok` may carry a value."""
    for name in ("turret_test_m_art_macro", "weapon_test_m_laser_macro", "missile_test_macro",
                 "shield_test_m_macro", "turret_test_m_orphan_macro"):
        rc, doc = _json(db, capsys, name)
        assert set(doc["fields"]) == {"name", "description", "system", "fires", "fitting", "ware", "price",
                                      "carried_by"}, name
        for key, fld in doc["fields"].items():
            assert fld["status"] in ("ok", "absent", "unresolved"), (name, key)
            if fld["status"] != "ok":
                assert fld.get("why"), (name, key)


def test_the_help_states_defaults_and_exit_codes(capsys):
    with pytest.raises(SystemExit):
        _effectivecli.main(["card", "--help"])
    out = capsys.readouterr().out
    assert "--json" in out and "--limit" in out and "default" in out and "exit" in out.lower()
    assert "loadoutrules" in out and "literal" in out
