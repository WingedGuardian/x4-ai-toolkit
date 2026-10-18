from lxml import etree

from x4validate import _compat_state, _merge


def test_positional_selector_after_sibling_replacement_tracks_actual_contribution():
    base = etree.fromstring(b'<root><actions><set_value name="$MissionCount" exact="1"/></actions></root>')
    first = etree.fromstring(b'''<diff><replace sel="(//set_value[@name='$MissionCount'])[1]">
        <set_value name="reset" exact="0"/>
        <set_value name="$MissionCount" exact="0"/>
        <do_if value="true"><set_value name="$MissionCount" exact="10"/></do_if>
        </replace></diff>''')
    second = etree.fromstring(b'''<diff><replace sel="(//set_value[@name='$MissionCount'])[1]">
        <set_value name="$MissionCount" exact="7"/></replace></diff>''')
    expected = etree.fromstring(etree.tostring(base))
    _merge.apply_diff(expected, first)
    _merge.apply_diff(expected, second)
    records, final, qualified = _compat_state.trace("md/test.xml", base, [("a", first), ("b", second)], expected)
    assert qualified
    original = records[("a", 0)].targets[0]
    later = records[("b", 0)].targets[0]
    assert later != original
    assert later in records[("a", 0)].created
    assert expected.xpath(final[later])[0].get("exact") == "7"
    assert expected.xpath("//do_if/set_value/@exact") == ["10"]


def test_identity_survives_position_shift_for_existing_nodes():
    base = etree.fromstring(b'<root><item id="a"/><item id="b" value="0"/></root>')
    first = etree.fromstring(b'''<diff><add sel="/root" pos="prepend"><item id="new"/></add></diff>''')
    second = etree.fromstring(b'''<diff><replace sel="/root/item[@id='b']/@value">2</replace></diff>''')
    expected = etree.fromstring(etree.tostring(base))
    _merge.apply_diff(expected, first)
    _merge.apply_diff(expected, second)
    records, final, qualified = _compat_state.trace("assets/a.xml", base, [("a", first), ("b", second)], expected)
    assert qualified
    target = records[("b", 0)].targets[0]
    assert target == "/root/item[2]/@value"
    assert final[target] == "/root/item[3]/@value"


def test_instrument_disagreement_is_never_qualified():
    base = etree.fromstring(b"<root/>")
    _records, _paths, qualified = _compat_state.trace("md/a.xml", base, [], etree.fromstring(b"<different/>"))
    assert qualified is False


def test_removing_an_added_node_records_its_actual_removal_position():
    base = etree.fromstring(b'<root><item id="original"/></root>')
    first = etree.fromstring(b'<diff><add sel="/root" pos="prepend"><item id="new" value="1"/></add></diff>')
    second = etree.fromstring(b'<diff><remove sel="/root/item[@id=\'new\']"/></diff>')
    expected = etree.fromstring(etree.tostring(base))
    _merge.apply_diff(expected, first)
    _merge.apply_diff(expected, second)
    records, final, qualified = _compat_state.trace("assets/a.xml", base, [("a", first), ("b", second)], expected)
    assert qualified
    identity = records[("b", 0)].targets[0]
    assert identity not in final
    assert records[("b", 0)].removed_paths == ((identity, "/root/item[1]"),)


def _trace(base, *diffs):
    base = etree.fromstring(base)
    expected = etree.fromstring(etree.tostring(base))
    roots = []
    for name, text in diffs:
        root = etree.fromstring(text)
        _merge.apply_diff(expected, root)
        roots.append((name, root))
    records, final, qualified = _compat_state.trace("md/test.xml", base, roots, expected)
    assert qualified
    return records, final


def test_a_split_replacement_records_each_inserted_sibling():
    """Issue #11: a later op landing on ONE of k siblings an earlier replace inserted is a
    PARTIAL overwrite, so the op must record which top-level elements it inserted."""
    records, final = _trace(
        b'<root><actions><set_value name="$M" exact="1"/></actions></root>',
        ("a", b'''<diff><replace sel="(//set_value[@name='$M'])[1]"><set_value name="r"/>'''
              b'''<set_value name="$M" exact="0"/><do_if/></replace></diff>'''))
    inserted = records[("a", 0)].inserted
    assert len(inserted) == 3
    assert [final[i] for i in inserted] == ["/root/actions/set_value[1]",
                                            "/root/actions/set_value[2]", "/root/actions/do_if"]


def test_TWIN_a_one_for_one_replacement_inserts_nothing_new():
    """The replaced slot continues, so it is not content the mod ADDED."""
    records, _final = _trace(
        b'<root><actions><set_value name="$M" exact="1"/></actions></root>',
        ("a", b'''<diff><replace sel="//set_value"><set_value name="$M" exact="3"/></replace></diff>'''))
    assert records[("a", 0)].inserted == ()


def test_TWIN_an_add_records_its_inserted_children_but_not_their_descendants():
    records, final = _trace(
        b'<root><cues/></root>',
        ("a", b'<diff><add sel="//cues"><cue name="N"><actions/></cue><cue name="O"/></add></diff>'))
    assert [final[i] for i in records[("a", 0)].inserted] == ["/root/cues/cue[1]", "/root/cues/cue[2]"]
