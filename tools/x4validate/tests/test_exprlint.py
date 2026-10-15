r"""Expression-grammar heuristic linter — the layer XSD is blind to.

Each rule was measured at ~0 false positives vs vanilla 9.0 reference\; these
tests lock in the positive catch AND the discrimination (the valid form nearby
must NOT flag), plus the real escape_pod regression set."""

from lxml import etree

from x4validate import _check, _exprlint, _merge


def _ids(xml: str) -> set[str]:
    return {f.rule_id for f in _exprlint.scan_tree(etree.fromstring(xml), "md/x.xml")}


def test_random_call_flagged_not_property():
    assert "random_call" in _ids('<a exact="$stations.{random(1,$stations.count)}"/>')
    assert "random_call" not in _ids('<a exact="$stations.random"/>')  # the 9.0 fix form


def test_fmt_missing_dot_flagged_not_valid_interp():
    assert "fmt_missing_dot" in _ids("<a text=\"'NPC %s'[this.name, $x]\"/>")
    assert "fmt_missing_dot" not in _ids("<a text=\"'NPC %s'.[this.name, $x]\"/>")  # dot present


def test_literals_cannot_cross_format_boundaries():
    assert "fmt_missing_dot" not in _ids("<a text=\"'first'.[$a] + 'second'.[$b]\"/>")
    assert "fmt_missing_dot" in _ids("<a text=\"'first'.[$a] + 'second'[$b]\"/>")
    assert "fmt_missing_dot" not in _ids("<a text=\"'first'.[$a] + '[second]'.[$b]\"/>")
    assert _ids("<a text=\"'random(1,2) $t.keys.list.count'\"/>") == set()


def test_list_literal_braces_only_on_in_attr():
    assert "list_literal_in_braces" in _ids('<do_for_each in="{entityrole.marine, entityrole.service}"/>')
    assert "list_literal_in_braces" not in _ids('<do_for_each in="[entityrole.marine, entityrole.service]"/>')
    # a {page,line} textref in a NON-`in` attribute must stay clean (no rule fires)
    assert _ids('<a exact="{10002,24}"/>') == set()


def test_keys_list_count_flagged_not_fixed_or_plain():
    assert "keys_list_count" in _ids('<a text="$t.keys.list.count"/>')
    assert "keys_list_count" not in _ids('<a text="$t.keys.count"/>')   # the fix form
    assert "keys_list_count" not in _ids('<a text="$t.keys.list"/>')     # no .count


def test_comments_and_valid_code_are_clean():
    # a real dynamic accessor .{[list]} (valid) and a comment mentioning random(
    xml = ('<mdscript name="X"><!-- pick a random(safe) spot -->'
           '<a exact="$ship.isclass.{[class.ship_s, class.ship_m]}"/>'
           '<a exact="$list.random"/></mdscript>')
    assert _ids(xml) == set()


def test_diff_xpath_sel_and_if_not_linted():
    # sel=/if= carry XPath, where `'x'[` (string then predicate) is valid — must not flag.
    assert _ids("<replace sel=\"//do_if[@value='not @$fw'][@chance='x']/@a\">y</replace>") == set()
    assert _ids("<add sel=\"//a[@n='b']\" if=\"//c[@d='e'][@f='g']\">z</add>") == set()
    # but a real script expression in a normal attr on the same element still flags
    assert "fmt_missing_dot" in _ids("<replace sel=\"//x\" comment=\"'%s'[a]\">y</replace>")


def test_escape_pod_regression_all_four(tmp_path):
    """The 4 pattern-detectable of the 5 real escape_pod 9.0 breaks."""
    mod = tmp_path / "mod"
    (mod / "aiscripts").mkdir(parents=True)
    (mod / "md").mkdir(parents=True)
    (mod / "aiscripts" / "order.move.escapepod.xml").write_text(
        '<aiscript name="order.move.escapepod">'
        '<a exact="$stations.{random(1,$stations.count)}"/></aiscript>', encoding="utf-8")
    (mod / "md" / "escape_pod_npc.xml").write_text(
        '<mdscript name="EscapePodNPC">'
        "<a text=\"'NPC %s triggered'[this.name, $x]\"/>"
        '<do_for_each in="{entityrole.marine, entityrole.service}"/>'
        '<a text="$AvailablePods.keys.list.count"/>'
        '</mdscript>', encoding="utf-8")
    ids = {f.rule_id for f in _exprlint.scan_mod(mod)}
    assert ids == {"random_call", "fmt_missing_dot", "list_literal_in_braces", "keys_list_count"}


def test_check_exprlint_is_advisory_never_gates(tmp_path):
    mod = tmp_path / "mod"
    (mod / "md").mkdir(parents=True)
    (mod / "md" / "x.xml").write_text(
        '<mdscript name="X"><a exact="$s.{random(1,$s.count)}"/>'
        '<a text="$t.keys.list.count"/></mdscript>', encoding="utf-8")
    rep = _check.Report()
    cfg = _merge.Config(reference=tmp_path / "reference")  # exprlint needs no reference tree
    _check.check_exprlint(mod, cfg, rep)
    assert {f.category for f in rep.findings} == {"exprlint"}
    sevs = {f.severity for f in rep.findings}
    assert "warn" in sevs and "info" in sevs        # random_call=warn, keys_list_count=info
    assert not any(f.severity == "error" for f in rep.findings)  # never gates
