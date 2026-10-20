"""Unit tests for hook_facts.py -- the single parse pass behind protect-bash.sh.

Runnable with no dependencies:  python .claude/hooks/test_hook_facts.py

WHY THIS FILE EXISTS. Eight guard rules each hand-rolled quote-aware shell parsing in
bash. MEASURED 2026-08-31 on a clean machine: that cost 13,585 ms per Bash call on a
201-char command, against 1,205 ms before the rules were re-scoped -- 11.3x -- because
resolve_var re-derived its assignment table per TOKEN inside per-SEGMENT loops, and
writes_under / searches_rooted_at were each re-invoked 5 and 4 times with no memoising.
Every remaining gap the code review found also lived in that duplicated parsing.

Dangerous tokens are BUILT FROM PARTS. A literal here is read by the live hook when
this file is written, and a guard blocking the work of fixing guards has already
happened four times.
"""
import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hook_facts as H  # noqa: E402

D = "r" + "m"                      # the delete verb, never a literal here
BS = chr(92)                       # backslash

# FIXTURE paths, and they must stay GENERIC. These are compared as strings against
# command text -- nothing here touches a real filesystem -- so a real machine's layout
# buys the test nothing and ships someone's username and game-profile id inside a public
# file. Caught 2026-08-31 by scripts/verify-port.py before any push; the author's intent
# and the file's subject matter protect nothing, only the scan does.
GAME = "C:/Program Files (x86)/Steam/steamapps/common/X4 Foundations"
PROF = "C:/Users/tester/Documents/Egosoft/X4/12345678"
REF = "C:/Users/tester/Desktop/Modding/X4/reference"
TOOLKIT = "C:/Users/tester/Desktop/Modding/X4"
DOCS = "C:/Users/tester/Documents"
ROOTS = {"game": GAME, "profile": PROF, "reference": REF, "toolkit": TOOLKIT,
         "mods": TOOLKIT + "/dev", "documents": DOCS, "saves": PROF + "/save"}


Q = chr(39)                        # apostrophe
DQ = chr(34)                       # double quote
DEL_GAME = D + ' -rf "' + GAME + '"'
DEL_REF = D + ' -rf "' + REF + '"'


def F(command, timeout=0, background=False, roots=None):
    payload = {"tool_input": {"command": command, "timeout": timeout,
                              "run_in_background": background}}
    return H.facts(payload, ROOTS if roots is None else roots)


# ---------------------------------------------------------------- primitives
class TestNorm(unittest.TestCase):
    def test_lowercases_and_slashes(self):
        self.assertEqual(H.norm("C:" + BS + "Foo" + BS + "Bar"), "/c/foo/bar")

    def test_unifies_drive_dialect(self):
        # MSYS "/c/..." and Windows "C:/..." must compare EQUAL. Without this the same
        # write asked in one form was ALLOWED in the other across 2,553 commands.
        self.assertEqual(H.norm("C:/Users/x"), H.norm("/c/Users/x"))

    def test_does_not_eat_a_url_scheme(self):
        self.assertEqual(H.norm("https://a/b"), "https://a/b")

    def test_strips_a_powershell_provider_qualifier(self):
        """AUDIT-2026-09-24 HK-1 review item 4: `FileSystem::C:\\x` is `C:\\x`."""
        plain = H.norm("C:" + BS + "R" + BS + "x")
        for pre in ("FileSystem::", "Microsoft.PowerShell.Core" + BS + "FileSystem::",
                    "FileSystem::" + BS + BS + "?" + BS):
            self.assertEqual(H.norm(pre + "C:" + BS + "R" + BS + "x"), plain, pre)
        self.assertTrue(F("rm -rf 'FileSystem::" + REF + "'")["rm_targets_reference"])

    def test_canonicalises_dot_segments(self):
        self.assertEqual(H.norm("/a/./b"), "/a/b")
        self.assertEqual(H.norm("/a/x/../b"), "/a/b")


class TestSegments(unittest.TestCase):
    def test_splits_on_operators(self):
        self.assertEqual(len(H.segments("a && b || c ; d | e")), 5)

    def test_a_separator_inside_quotes_does_not_split(self):
        self.assertEqual(len(H.segments("grep -E 'a|b' f")), 1)

    def test_newline_splits(self):
        self.assertEqual(len(H.segments("a\nb")), 2)

    def test_a_redirect_operator_is_not_a_separator(self):
        # `>|` contains a pipe and `2>&1` contains an ampersand. Splitting on those tore
        # the redirect away from its target, so `echo x >| <docs>/n.txt` wrote nowhere as
        # far as every write rule was concerned -- found by E2E, not by unit tests,
        # because redirects() was only ever tested on an unsplit string.
        self.assertEqual(len(H.segments("echo x >| a")), 1)
        self.assertEqual(len(H.segments("cmd 2>&1")), 1)
        self.assertEqual(len(H.segments("cmd &> a")), 1)


class TestAssignments(unittest.TestCase):
    def test_last_assignment_wins(self):
        # The bash helper used head -1, so a reassigned variable resolved to the OLD
        # value and the conservative fallback could never save it.
        self.assertEqual(H.assignments("X=/one; X=/two; echo $X")["X"], "/two")

    def test_quoted_value_with_spaces(self):
        self.assertEqual(H.assignments('G="/a b/c"; echo $G')["G"], "/a b/c")


class TestResolve(unittest.TestCase):
    def test_expands_both_forms(self):
        a = {"G": "/x"}
        self.assertEqual(H.resolve("$G/f", a), "/x/f")
        self.assertEqual(H.resolve("${G}/f", a), "/x/f")

    def test_unresolved_is_reported(self):
        self.assertTrue(H.has_unresolved(H.resolve("$NOPE/f", {})))


class TestHeredocs(unittest.TestCase):
    def test_body_is_removed(self):
        s = H.strip_heredocs("cat > f <<MARK\nsecret line\nMARK\necho after")
        self.assertNotIn("secret line", s)
        self.assertIn("echo after", s)

    def test_a_QUOTED_marker_still_opens_a_heredoc(self):
        # `<<'PY'` is the commonest form in this workspace. Blanking quoted strings before
        # looking for the marker blanked the MARKER NAME too, so the body was never
        # stripped and its text reached three refusal rules as though it were commands.
        s = H.strip_heredocs("python - <<'PY'\nsecret line\nPY\necho after")
        self.assertNotIn("secret line", s)
        self.assertIn("echo after", s)

    def test_a_quoted_marker_body_is_data_for_the_rules(self):
        cmd = "python - <<'PY'" + chr(10) + "guard = 'grep content.xml against $X4_PROFILE'" + chr(10) + "PY"
        self.assertFalse(F(cmd)["profile_search_by_name"])

    def test_marker_inside_quotes_does_NOT_open_a_skip(self):
        # The bash version scanned the raw line, so a quoted marker opened a skip
        # region and hid every following command from three deny rules.
        s = H.strip_heredocs('echo "a <<MARK b"\ngit add -A')
        self.assertIn("git add -A", s)


class TestNotEveryDoubleAngleIsAHeredoc(unittest.TestCase):
    """`<<` appears in three constructs that open NO heredoc, and treating any of them as
    one blanks the REST OF THE COMMAND -- so every rule below it goes silent and the hook
    allows.

    MEASURED 2026-09-01, all three E2E through protect-bash.sh: a game-directory
    `rm -rf` that the guard refuses on its own became a SILENT ALLOW when preceded by a
    here-string, by `<<` inside a comment, or by an arithmetic left-shift.

    Same class as the apostrophe bypass and the `$( )` nesting error: the PARSER feeding
    the predicates, not the predicates. 151 unit tests, 31 mutants and a 13k-command
    replay were green throughout -- the coverage report was true about the predicates and
    silent about their input. That is why the controls below matter as much as the cases.
    """

    # --- B1: the delimiter is QUOTE-REMOVED before it terminates anything -----------
    #
    # MEASURED 2026-09-06 against the guard AS SHIPPED, ten guard commits after the
    # heredoc-marker work: `<<\EOF` and `<<E'OF'` were TOTAL bypasses. `_HD`'s bare
    # alternative allowed a backslash and a quote, so the captured markers were `\EOF`
    # and `E'OF'` -- neither of which ever equals the body's `EOF`, so the skip region
    # ran to END OF INPUT and every following command vanished before any rule read it.
    #
    # Bash applies quote removal to a heredoc delimiter. These pin every spelling of
    # the SAME terminator, so a future regex change cannot fix one and lose another.

    def test_an_escaped_delimiter_terminates_at_the_bare_word(self):
        self.assertEqual(H.heredoc_marker("cat <<" + BS + "EOF"), "EOF")

    def test_a_PARTIALLY_quoted_delimiter_terminates_at_the_bare_word(self):
        self.assertEqual(H.heredoc_marker("cat <<E'OF'"), "EOF")
        self.assertEqual(H.heredoc_marker('cat <<"EO"F'), "EOF")

    def test_the_spellings_that_ALREADY_worked_still_do(self):
        """The controls. Quote removal must not trade one spelling for another --
        `<<'E-O-F'`, `<<'EOF.md'` and `<<"my marker"` are the three a previous fix
        added, and a marker with a SPACE only survives because the quoted run is kept
        whole."""
        for line, want in ((r"cat <<EOF", "EOF"),
                           (r"cat <<'EOF'", "EOF"),
                           (r'cat <<"EOF"', "EOF"),
                           (r"cat <<-END", "END"),
                           (r"cat <<'E-O-F'", "E-O-F"),
                           (r"cat <<'EOF.md'", "EOF.md"),
                           (r'cat <<"my marker"', "my marker")):
            self.assertEqual(H.heredoc_marker(line), want, line)

    def test_a_dangerous_command_after_an_escaped_delimiter_is_still_seen(self):
        """The consequence, not the parse. With the marker wrong the whole command was
        blanked, so this asserts the RULE fires -- which is what the bypass defeated."""
        cmd = ("cat <<" + BS + "EOF >/dev/null" + chr(10) + "x" + chr(10) + "EOF" + chr(10)
               + 'rm -rf "' + GAME + '"')
        self.assertTrue(F(cmd)["rm_hits_game"])
        cmd2 = ("cat <<E'OF' >/dev/null" + chr(10) + "x" + chr(10) + "EOF" + chr(10)
                + 'rm -rf "' + GAME + '"')
        self.assertTrue(F(cmd2)["rm_hits_game"])

    def test_a_REAL_heredoc_body_is_still_skipped(self):
        """The other direction, and the reason this fix is one-way: a correct marker can
        only make the skip region SHORTER. A dangerous-looking line INSIDE a body is
        data, not a command, and must stay invisible."""
        cmd = ("cat <<EOF > notes.txt" + chr(10) + 'rm -rf "' + GAME + '"' + chr(10) + "EOF")
        self.assertFalse(F(cmd)["rm_hits_game"])

    def test_a_here_string_opens_no_heredoc(self):
        # The scan reaches the SECOND `<` of `<<<`, sees `<< word`, and reports a marker.
        self.assertIsNone(H.heredoc_marker("cat <<< hello"))
        self.assertIsNone(H.heredoc_marker("cat <<<hello"))

    def test_a_double_angle_in_a_COMMENT_opens_no_heredoc(self):
        self.assertIsNone(H.heredoc_marker("# shifts a << b"))
        self.assertIsNone(H.heredoc_marker("echo hi   # a << b"))

    def test_an_arithmetic_left_shift_opens_no_heredoc(self):
        self.assertIsNone(H.heredoc_marker("n=$((1 << FOO))"))

    def test_the_rules_still_SEE_a_delete_after_each_of_them(self):
        rm = 'rm -rf "C:/Program Files (x86)/Steam/steamapps/common/X4 Foundations/x"'
        for prefix in ("cat <<< hello", "# shifts a << b", "n=$((1 << 2))"):
            with self.subTest(prefix=prefix):
                self.assertTrue(F(prefix + chr(10) + rm)["rm_in_x4_dir"],
                                "syntax before the delete made the guard blind to it")

    # --- the other direction, or `return None` passes every test above --------------
    def test_a_real_heredoc_is_still_recognised(self):
        self.assertEqual(H.heredoc_marker("cat > f <<EOF"), "EOF")
        self.assertEqual(H.heredoc_marker("cat > f <<'PY'"), "PY")
        self.assertEqual(H.heredoc_marker("cat <<-END"), "END")

    def test_a_real_heredoc_beside_the_new_exclusions_still_opens(self):
        self.assertEqual(H.heredoc_marker("n=$((1 << 2)); cat <<EOF"), "EOF")
        self.assertEqual(H.heredoc_marker("cat <<EOF   # write it"), "EOF")

    def test_a_hash_that_is_not_a_comment_does_not_truncate(self):
        # a mid-word `#` is a URL fragment, and a quoted one is data -- neither is a comment
        self.assertEqual(H.heredoc_marker("curl http://x#y <<EOF"), "EOF")
        self.assertEqual(H.heredoc_marker("grep '#' f <<EOF"), "EOF")

    def test_a_real_heredoc_BODY_is_still_stripped(self):
        rm = 'rm -rf "C:/Program Files (x86)/Steam/steamapps/common/X4 Foundations/x"'
        self.assertFalse(F("cat > f <<EOF" + chr(10) + rm + chr(10) + "EOF")["rm_in_x4_dir"],
                         "a heredoc body is text being written, not a command")


class TestReservedWordsDoNotHideTheCommand(unittest.TestCase):
    """A shell RESERVED WORD in front of a simple command was a TOTAL guard bypass.

    The splitter cuts on `;` and `&&`, so `if true; then rm -rf <game>; fi` yields the
    segment `then rm -rf <game>` -- and verb() returned `then`. Every verb-keyed rule
    (rm, sed, git, grep) therefore missed, INCLUDING the three hard blocks: the game
    root, extensions wholesale, and the reference tree.

    MEASURED 2026-09-01 by the syntax-class fuzzer: 90 bypasses over 10 compound forms
    x 9 seeds. The 10th seed was immune because it is REDIRECT-keyed rather than
    verb-keyed, which is exactly what pins the root cause -- the operand was present
    and correct the whole time; the VERB was the keyword.

    Nothing in 151 unit tests, 35 mutants, a 13,500-command replay or 19 predicate
    probes could see this: they all start from a verb the parser has already chosen.
    """

    GAME = "C:/Program Files (x86)/Steam/steamapps/common/X4 Foundations"

    def _verbs(self, cmd):
        return [H.verb(seg) for seg, _ in
                H.cwd_track(H.strip_comments(H.strip_heredocs(cmd)))]

    def test_every_compound_form_still_shows_the_command(self):
        rm = 'rm -rf "%s"' % self.GAME
        forms = {
            "if/then":        "if true; then " + rm + "; fi",
            "if condition":   "if " + rm + "; then :; fi",
            "for/do":         "for i in 1; do " + rm + "; done",
            "until/do":       "until true; do " + rm + "; break; done",
            "else":           "if false; then :; else " + rm + "; fi",
            "elif":           "if false; then :; elif true; then " + rm + "; fi",
            "case arm":       "case x in x) " + rm + " ;; *) :;; esac",
            "case arm glob":  "case $v in *) " + rm + " ;; esac",
            # The WORD contains the substring "in". `rest.index("in")` cut inside it,
            # so verb() returned `ary`/`all` and every verb-keyed rule missed at once
            # -- a TOTAL bypass of all three hard blocks (MEASURED E2E 2026-09-04).
            # 0 of the 388 tests used such a word, which is why 996 fuzz mutants and
            # 82 mutation probes were all green over it.
            "case word has in": "case $string in *) " + rm + " ;; esac",
            "case word is install": "case install in *) " + rm + " ;; esac",
            "case word IS in":  "case in in *) " + rm + " ;; esac",
            "negation":       "! " + rm,
            "function posix": "f() { " + rm + "; }; f",
            "function kw":    "function f { " + rm + "; }; f",
            "nested if+for":  "if true; then for i in 1; do " + rm + "; done; fi",
        }
        for name, cmd in forms.items():
            with self.subTest(form=name):
                self.assertIn("rm", self._verbs(cmd),
                              "the keyword hid the command from every verb-keyed rule")

    def test_the_hard_block_survives_a_compound_wrapper(self):
        # the case that matters most: a hard block must not become an allow
        self.assertTrue(F('if true; then rm -rf "%s"; fi' % self.GAME)["rm_hits_game"])

    # --- must NOT strip: a real program keeps its name ---------------------------
    def test_a_program_whose_name_merely_starts_with_a_keyword_is_untouched(self):
        for cmd, want in (("do_thing --flag", "do_thing"),
                          ("iffy --x", "iffy"),
                          ("done_marker.sh", "done_marker.sh"),
                          ("function_helper.py run", "function_helper.py"),
                          ("casefold.py", "casefold.py")):
            with self.subTest(cmd=cmd):
                self.assertEqual(H.verb(H._unwrap(cmd)), want)

    def test_a_keyword_inside_quotes_is_data(self):
        self.assertEqual(H.verb(H._unwrap('echo "then rm -rf /"')), "echo")

    def test_a_case_with_no_dangerous_op_stays_quiet(self):
        self.assertFalse(F("case $v in a) echo hi ;; esac")["rm_hits_game"])

    # --- the regression this fix ITSELF introduced, pinned ------------------------
    def test_a_process_substitution_tail_is_not_a_case_arm_label(self):
        """The first version of the case-arm rule matched `rm -rf extensions) ` and ate
        the whole command. The BASELINE caught that one, so the fix was briefly worse
        than the bug. A case label is a single glob token and carries no whitespace;
        every dangerous rule needs an operand, and an operand needs a space -- which is
        what makes the spaceless restriction sound rather than merely convenient."""
        cmd = 'diff <(cd "%s" && rm -rf extensions) <(echo b)' % self.GAME
        self.assertIn("rm", self._verbs(cmd),
                      "the case-arm rule swallowed a process substitution")


class TestWrappersThatCarryACommandAsText(unittest.TestCase):
    """`bash -c`, its flag-cluster spellings, and `eval` all run TEXT as a command.

    Anything they carry is invisible to every rule that inspects segments, so each one
    is a total bypass on its own. The unwrapper matched the literal token `-c` only.

    MEASURED 2026-09-01, E2E through protect-bash.sh, against a game-root `rm -rf` that
    the guard denies unaided:
        sh -c '<rm>'   -> deny            bash -lc '<rm>' -> *** SILENT ALLOW ***
        xargs '<rm>'   -> deny            eval '<rm>'     -> *** SILENT ALLOW ***
    One character of flag clustering stood between a hard block and nothing.
    """

    GAME = "C:/Program Files (x86)/Steam/steamapps/common/X4 Foundations"

    def _rm(self):
        return 'rm -rf "%s"' % self.GAME

    def test_every_shell_c_spelling_is_unwrapped(self):
        for w in ("bash -c", "bash -lc", "sh -c", "sh -ic", "zsh -c", "dash -c", "ksh -c"):
            with self.subTest(wrapper=w):
                cmd = "%s '%s'" % (w, self._rm())
                self.assertTrue(F(cmd)["rm_hits_game"], "%s hid the command" % w)

    def test_eval_is_unwrapped(self):
        self.assertTrue(F("eval '%s'" % self._rm())["rm_hits_game"])

    def test_a_wrapper_inside_a_compound_is_still_unwrapped(self):
        """The two fixes have to compose: a keyword in front AND a wrapper around."""
        self.assertTrue(F("if true; then eval '%s'; fi" % self._rm())["rm_hits_game"])

    # --- must NOT fire ------------------------------------------------------------
    def test_an_unrelated_dash_c_is_not_a_shell(self):
        # `grep -c` counts; it is not a shell and carries no command
        self.assertFalse(F("grep -c foo file.txt")["rm_hits_game"])

    def test_a_harmless_wrapped_command_stays_quiet(self):
        self.assertFalse(F("bash -lc 'ls -la'")["rm_hits_game"])

    def test_a_quoted_dash_c_is_data(self):
        self.assertFalse(F("echo \"bash -c rm -rf /\"")["rm_hits_game"])


class TestHomeReferencesResolve(unittest.TestCase):
    """`~` and `$HOME` are values a hook CAN know, and not knowing them hid save deletes.

    MEASURED 2026-09-01 E2E through protect-bash.sh, on the SAME file: the absolute form
    asked, while `~/Documents/.../save/s.xml.gz` and `$HOME/...` were **silent allows**.
    Saves are the one thing in this workspace with no backup and no undo.

    Every other operand dialect was already correct -- absolute, relative after `cd`, the
    MSYS `/c/...` form, and backslashes. Home was the only gap, which is why this is an
    expansion rather than a new name backstop.
    """

    #: The FIXTURE profile id, not a real one. The first draft of this used the real
    #: id -- which both failed to match the synthetic saves root (so the control could
    #: not fire) and would have shipped a personal identifier in a public file. The
    #: broken control is what surfaced it, before scan-identifiers ever ran.
    TAIL = "Documents/Egosoft/X4/12345678/save/s.xml.gz"

    #: The fixture home, matching this file's synthetic roots. Patched rather than read
    #: from the environment: a test that depends on the real machine's home is not
    #: hermetic, and hard-coding a real one would ship a username in a public file.
    HOME = "C:/Users/tester"

    def test_all_home_spellings_reach_the_same_verdict_as_the_absolute_form(self):
        import unittest.mock as mock
        with mock.patch.object(H, "_HOME", self.HOME):
            want = F('rm -f "%s/%s"' % (self.HOME, self.TAIL))["rm_saves"]
            self.assertTrue(want, "the absolute control must fire, or this proves nothing")
            for spell in ("~", "$HOME", "${HOME}", "$USERPROFILE", "${USERPROFILE}"):
                with self.subTest(spelling=spell):
                    self.assertTrue(F("rm -f %s/%s" % (spell, self.TAIL))["rm_saves"],
                                    "%s did not resolve to the same file" % spell)

    def test_only_a_LEADING_home_reference_is_expanded(self):
        # `a~b` is a filename, and `x/$HOME` is not a home reference; rewriting either
        # would invent a path the user never wrote.
        self.assertEqual(H.expand_home("./a~b.txt"), "./a~b.txt")
        self.assertEqual(H.expand_home("x/$HOME/y"), "x/$HOME/y")

    def test_an_unrelated_file_under_home_still_does_not_fire(self):
        import unittest.mock as mock
        with mock.patch.object(H, "_HOME", self.HOME):
            self.assertFalse(F("rm -f ~/notes.txt")["rm_saves"])

    def test_expansion_is_inert_when_there_is_no_home(self):
        import unittest.mock as mock
        with mock.patch.object(H, "_HOME", ""):
            self.assertEqual(H.expand_home("~/x"), "~/x")


# ------------------------------------------------------------ redirect targets
class TestRedirects(unittest.TestCase):
    def test_truncate_vs_append(self):
        self.assertEqual(H.redirects("echo x > a"), [("truncate", "a")])
        self.assertEqual(H.redirects("echo x >> a"), [("append", "a")])

    def test_noclobber_override_is_a_truncate(self):
        self.assertEqual(H.redirects("echo x >| a"), [("truncate", "a")])

    def test_fd_redirect_to_devnull_is_not_a_target(self):
        self.assertEqual(H.redirects("cmd 2>/dev/null"), [])

    def test_fd_duplication_is_not_a_target(self):
        self.assertEqual(H.redirects("cmd 2>&1"), [])


# ------------------------------------------------------------------- verbs
class TestVerb(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(H.verb("cp a b"), "cp")

    def test_sees_through_a_wrapper(self):
        for w in ("time", "nice", "env", "sudo", "xargs"):
            self.assertEqual(H.verb(w + " cp a b"), "cp", w)

    def test_sees_through_an_env_assignment_prefix(self):
        self.assertEqual(H.verb("FOO=1 cp a b"), "cp")


class TestCopyDestinations(unittest.TestCase):
    def test_cp_writes_its_last_operand(self):
        self.assertEqual(H.copy_dests("cp a b"), ["b"])

    def test_dash_t_names_the_destination(self):
        self.assertEqual(H.copy_dests("mv -t /dest a b"), ["/dest"])

    def test_tee_writes_EVERY_file_operand(self):
        # TWO operands deliberately: with one, "first" and "last" are the same token,
        # so the test passed against a mutant that took the last -- it could not go red.
        self.assertEqual(H.copy_dests("tee a.txt b.txt"), ["a.txt", "b.txt"])

    def test_a_redirect_is_not_a_copy_operand(self):
        self.assertEqual(H.copy_dests("cp a b > log.txt"), ["b"])


# ----------------------------------------------------------------- searches
class TestSearches(unittest.TestCase):
    def test_grep_needs_a_recursive_flag(self):
        self.assertEqual(H.search_paths("grep foo /ref"), [])
        self.assertEqual(H.search_paths("grep -r foo /ref"), ["/ref"])

    def test_recursive_letter_anywhere_in_a_bundle(self):
        self.assertEqual(H.search_paths("grep -rn foo /ref"), ["/ref"])

    def test_rg_is_recursive_BY_DEFAULT(self):
        # rg and ag need no flag at all. The bash rule gated on a flag, so a full-tree
        # rg was allowed -- the exact command the rule exists to stop.
        self.assertEqual(H.search_paths("rg foo /ref"), ["/ref"])

    def test_dash_e_supplies_the_pattern_so_the_path_is_not_consumed(self):
        self.assertEqual(H.search_paths("grep -r -e foo /ref"), ["/ref"])

    def test_a_hyphenated_pattern_is_data_not_flags(self):
        self.assertEqual(H.search_paths("grep 'a-r-b' file.txt"), [])


# ------------------------------------------------------------- rule predicates
class TestDeletePredicates(unittest.TestCase):
    def test_quoted_game_root_hits(self):
        self.assertTrue(F(D + ' -rf "' + GAME + '"')["rm_hits_game"])

    def test_backslash_escaped_space_still_hits(self):
        esc = GAME.replace(" ", BS + " ")
        self.assertTrue(F(D + " -rf " + esc)["rm_hits_game"])

    def test_dot_segment_is_canonicalised(self):
        # Asserted against the REFERENCE root, not the game: the game predicate also
        # carries a name backstop, which caught this path by name and let the test pass
        # against a mutant with canonicalisation removed. A guard in front of the clause
        # under test shadows it (CLAUDE.md #26).
        p = REF.replace("/reference", "/./reference")
        self.assertTrue(F(D + ' -rf "' + p + '"')["rm_targets_reference"])

    def test_dotdot_segment_is_canonicalised(self):
        p = REF.replace("/reference", "/other/../reference")
        self.assertTrue(F(D + ' -rf "' + p + '"')["rm_targets_reference"])

    def test_dot_segment_in_the_game_path_also_hits(self):
        p = GAME.replace("/X4 Foundations", "/./X4 Foundations")
        self.assertTrue(F(D + ' -rf "' + p + '"')["rm_hits_game"])

    def test_deleting_extensions_WHOLESALE_is_a_hard_block(self):
        # Wiping extensions/ destroys every deployed mod. Narrowing the block to the
        # install root alone would let that fall through to a mere confirmation.
        #
        # The root here is deliberately NOT named "X4 Foundations": with the real name the
        # legacy backstop also matches, so the test passed against a mutant that removed
        # the extensions clause entirely -- it was asserting the right OUTCOME through the
        # wrong MECHANISM. Third instance of a guard clause shadowing the thing under test
        # in one session (CLAUDE.md #26).
        r = dict(ROOTS)
        r["game"] = "C:/Games/PlainlyNamedInstall"
        self.assertTrue(F(D + ' -rf "C:/Games/PlainlyNamedInstall/extensions"',
                          roots=r)["rm_hits_game"])
        # ...and the same root, one level deeper, must NOT be a hard block.
        self.assertFalse(F(D + ' -rf "C:/Games/PlainlyNamedInstall/extensions/mymod"',
                           roots=r)["rm_hits_game"])

    def test_deleting_ONE_deployed_mod_is_NOT_a_hard_block(self):
        # MEASURED 2026-08-31 over a 1,000-command corpus sample: all 4 hits of this rule
        # were `rm -rf "$DST"` where DST resolved to extensions/<one mod> -- the documented
        # deploy path, which deploy.py itself performs. Hard-denying it blocks routine
        # work. It must still CONFIRM (rm_in_x4_dir), which is the verdict meant for it.
        cmd = 'DST="' + GAME + '/extensions/mymod"; ' + D + ' -rf "$DST"'
        f = F(cmd)
        self.assertFalse(f["rm_hits_game"])
        self.assertTrue(f["rm_in_x4_dir"])

    def test_deleting_a_file_inside_a_deployed_mod_is_NOT_a_hard_block(self):
        cmd = D + ' -f "' + GAME + '/extensions/mymod/music/track.mp3"'
        f = F(cmd)
        self.assertFalse(f["rm_hits_game"])
        self.assertTrue(f["rm_in_x4_dir"])

    def test_unconfigured_backstop_does_not_catch_a_mod_folder(self):
        # The name backstop must be root-scoped too, or an unconfigured machine gets the
        # same over-block by a different route.
        r = dict(ROOTS)
        r["game"] = ""
        self.assertFalse(F(D + ' -rf "/opt/games/X4 Foundations/extensions/mymod"',
                           roots=r)["rm_hits_game"])

    def test_an_archive_merely_NAMED_after_the_game_does_not_hit(self):
        self.assertFalse(F(D + ' -f "/c/backups/X4 Foundations v2.zip"')["rm_hits_game"])

    def test_a_temp_delete_that_merely_MENTIONS_the_game_does_not_hit(self):
        cmd = 'G="' + GAME + '"; ' + D + " -f /c/tmp/scratch.txt"
        self.assertFalse(F(cmd)["rm_hits_game"])

    def test_unconfigured_root_falls_back_to_the_NAME(self):
        # An installer with no configured paths must still get the hard block. The
        # re-scoped bash rule dropped this backstop entirely.
        r = dict(ROOTS)
        r["game"] = ""
        self.assertTrue(F(D + ' -rf "/opt/games/X4 Foundations"', roots=r)["rm_hits_game"])


class TestWritePredicates(unittest.TestCase):
    def test_stderr_suppression_is_not_a_write_into_the_game(self):
        cmd = 'ls "' + GAME + '/extensions" 2>/dev/null'
        self.assertFalse(F(cmd)["redirect_truncate_into_game_or_profile"])

    def test_a_real_truncating_redirect_into_the_game_fires(self):
        cmd = 'echo x > "' + GAME + '/f.txt"'
        self.assertTrue(F(cmd)["redirect_truncate_into_game_or_profile"])

    def test_append_into_the_game_does_not_fire(self):
        cmd = 'echo x >> "' + GAME + '/f.txt"'
        self.assertFalse(F(cmd)["redirect_truncate_into_game_or_profile"])

    def test_reading_under_documents_is_not_a_write(self):
        self.assertFalse(F('cat "' + DOCS + '/notes.txt"')["writes_documents"])

    def test_writing_under_documents_fires(self):
        self.assertTrue(F('echo x > "' + DOCS + '/notes.txt"')["writes_documents"])

    def test_tee_into_documents_through_a_wrapper_fires(self):
        cmd = 'echo x | sudo tee "' + DOCS + '/n.txt"'
        self.assertTrue(F(cmd)["writes_documents"])


class TestSearchPredicates(unittest.TestCase):
    def test_rooted_at_reference_fires(self):
        self.assertTrue(F('grep -rn foo "' + REF + '"')["search_rooted_reference"])

    def test_rg_without_a_flag_at_reference_fires(self):
        self.assertTrue(F('rg foo "' + REF + '"')["search_rooted_reference"])

    def test_a_scoped_subdirectory_search_does_NOT_fire(self):
        cmd = 'grep -rn foo "' + REF + '/libraries"'
        self.assertFalse(F(cmd)["search_rooted_reference"])

    def test_cd_then_dot_is_rooted(self):
        cmd = 'cd "' + REF + '" && grep -rn foo .'
        self.assertTrue(F(cmd)["search_rooted_reference"])

    def test_rooted_at_the_workspace_fires(self):
        # The rule a code review found had NO probe at all, in either direction.
        self.assertTrue(F('grep -rn foo "' + TOOLKIT + '"')["search_rooted_workspace"])

    def test_rooted_at_the_game_fires(self):
        self.assertTrue(F('grep -rn foo "' + GAME + '"')["search_rooted_workspace"])

    def test_cd_to_root_then_scoped_subdir_does_NOT_fire(self):
        # The rule's own message recommends exactly this form.
        cmd = 'cd "' + TOOLKIT + '" && grep -rn foo tools/x4validate'
        self.assertFalse(F(cmd)["search_rooted_workspace"])

    def test_wrapper_before_grep_still_fires(self):
        cmd = 'time grep -rn foo "' + REF + '"'
        self.assertTrue(F(cmd)["search_rooted_reference"])


# A search rooted ABOVE reference\ -- the axis this fixture used to hold constant.
#
# ROOTS above sets TOOLKIT to the PARENT of REF, which is what the real machine looked
# like until the dev repo was retired. So `search_rooted_workspace` covered the ancestor
# BY COINCIDENCE and no test here could fail when that coincidence ended. SPLIT_ROOTS
# reproduces the post-retirement layout, where the toolkit lives somewhere else entirely
# and the parent of reference\ is named by no root at all.
#
# MEASURED 2026-09-21, on the real machine, before the fix:
#   cd <parent-of-reference> && grep -rl x .   -> ALLOW      <-- the hole
#   same shape at the toolkit root             -> deny (WRONG SCOPE)
#   rooted at reference/ itself                -> deny (WRONG TOOL)
#   rooted at the game root                    -> deny (WRONG SCOPE)
# Three controls that still denied are what made it a hole and not a rule change.
PARENT_OF_REF = "C:/Users/tester/Desktop/Modding/X4"
SPLIT_ROOTS = dict(ROOTS, toolkit="C:/Users/tester/Projects/x4-claude-toolkit")


class TestSearchRootedAboveReference(unittest.TestCase):
    def test_contains_root_is_true_for_a_proper_ancestor(self):
        self.assertTrue(H.contains_root(PARENT_OF_REF, REF))

    def test_contains_root_is_NOT_is_root(self):
        # The two predicates must partition, or the ancestor clause silently duplicates
        # the exact one and its own twin cannot fail.
        self.assertFalse(H.contains_root(REF, REF))
        self.assertTrue(H.is_root(REF, REF))

    def test_contains_root_respects_the_path_SEPARATOR(self):
        # A prefix match without a separator would put /a/bc under /a/b.
        self.assertFalse(H.contains_root("C:/a/b", "C:/a/bc/d"))
        self.assertTrue(H.contains_root("C:/a/b", "C:/a/b/c"))

    def test_contains_root_is_false_for_a_DESCENDANT(self):
        # Direction matters: searching INSIDE reference is the scoped case that must
        # stay allowed.
        self.assertFalse(H.contains_root(REF + "/libraries", REF))

    def test_search_rooted_above_reference_FIRES_when_the_toolkit_is_elsewhere(self):
        # THE REGRESSION. Rooted at the parent of reference\, with no root naming it.
        cmd = 'cd "' + PARENT_OF_REF + '" && grep -rl foo --include="*.md" .'
        self.assertTrue(F(cmd, roots=SPLIT_ROOTS)["search_rooted_reference"])

    def test_an_explicit_ancestor_path_fires_too(self):
        cmd = 'grep -rn foo "' + PARENT_OF_REF + '"'
        self.assertTrue(F(cmd, roots=SPLIT_ROOTS)["search_rooted_reference"])

    def test_a_HIGHER_ancestor_fires(self):
        # Every ancestor traverses the 60 GB, so each one is the rule's own case.
        cmd = 'grep -rn foo "C:/Users/tester/Desktop"'
        self.assertTrue(F(cmd, roots=SPLIT_ROOTS)["search_rooted_reference"])

    def test_the_scoped_subdirectory_search_STILL_does_not_fire(self):
        # The control that keeps the fix from becoming over-blocking, which this file
        # calls the worse failure.
        cmd = 'grep -rn foo "' + REF + '/libraries"'
        self.assertFalse(F(cmd, roots=SPLIT_ROOTS)["search_rooted_reference"])

    def test_a_SIBLING_of_the_parent_does_not_fire(self):
        cmd = 'grep -rn foo "C:/Users/tester/Desktop/Modding/X4-notes"'
        self.assertFalse(F(cmd, roots=SPLIT_ROOTS)["search_rooted_reference"])

    def test_an_unrelated_tree_does_not_fire(self):
        cmd = 'grep -rn foo "C:/Users/tester/Documents/other"'
        self.assertFalse(F(cmd, roots=SPLIT_ROOTS)["search_rooted_reference"])

    def test_it_fires_under_the_OLD_coincidental_layout_too(self):
        # Belt and braces: the fix must not depend on the split layout either.
        cmd = 'grep -rn foo "' + PARENT_OF_REF + '"'
        self.assertTrue(F(cmd)["search_rooted_reference"])


class TestMiscPredicates(unittest.TestCase):
    def test_git_add_all_fires(self):
        self.assertTrue(F("git add -A")["git_add_all"])

    def test_git_add_all_inside_a_heredoc_body_is_DATA(self):
        self.assertFalse(F("cat > f <<MARK\ngit add -A\nMARK")["git_add_all"])

    def test_git_add_explicit_path_does_not_fire(self):
        self.assertFalse(F("git add .gitattributes")["git_add_all"])

    def test_git_add_all_on_a_LATER_LINE_fires(self):
        # The bash rule used grep, which is line-based, so `^` matched every line start.
        # The Python port lost that: without re.M the rule only saw a command whose FIRST
        # characters were `git add`. Multi-line commands are routine here.
        self.assertTrue(F("echo setup" + chr(10) + "git add -A")["git_add_all"])

    def test_noclobber_redirect_into_documents_fires(self):
        # Same shape as the segments bug: proven at the redirects() level, but the fact
        # was False because the segment splitter had already torn `>|` apart.
        self.assertTrue(F('echo x >| "' + DOCS + '/n.txt"')["writes_documents"])

    def test_dollarq_after_a_pipeline_fires(self):
        self.assertTrue(F("cmd | head; echo $?")["dollarq_after_pipe"])

    def test_dollarq_after_a_process_substitution_does_not_fire(self):
        self.assertFalse(F("diff <(a | sort) <(b | sort); echo $?")["dollarq_after_pipe"])

    # 2026-10-02: X4-folder deletes and Documents writes became advisories; the PROFILE keeps
    # its confirmation through these two facts. Each is pinned both ways.
    def test_rm_in_profile_fires_on_a_profile_delete(self):
        self.assertTrue(F('rm -f "' + PROF + '/config.xml"')["rm_in_profile"])

    def test_TWIN_rm_in_profile_is_silent_for_a_mod_delete(self):
        self.assertFalse(F('rm -f "' + TOOLKIT + '/dev/m/x.xml"')["rm_in_profile"])

    def test_writes_profile_fires_on_a_profile_write(self):
        self.assertTrue(F('echo x > "' + PROF + '/config.xml"')["writes_profile"])

    def test_TWIN_writes_profile_is_silent_for_a_documents_write(self):
        self.assertFalse(F('echo x > "' + DOCS + '/notes.txt"')["writes_profile"])

    def test_timeout_over_cap_fires(self):
        self.assertTrue(F("sleep 1", timeout=900000)["timeout_over_cap"])

    def test_timeout_at_cap_does_not_fire(self):
        self.assertFalse(F("sleep 1", timeout=600000)["timeout_over_cap"])

    def test_a_BACKGROUND_call_has_the_background_cap(self):
        """Background calls cap at 7200000 ms, not 600000 (READ: the Bash tool description,
        2026-10-02). The foreground cap denied a 25-minute background job."""
        self.assertFalse(F("sleep 1", timeout=1500000, background=True)["timeout_over_cap"])
        self.assertFalse(F("sleep 1", timeout=7200000, background=True)["timeout_over_cap"])

    def test_TWIN_a_background_call_over_ITS_cap_still_fires(self):
        self.assertTrue(F("sleep 1", timeout=7200001, background=True)["timeout_over_cap"])
        self.assertTrue(F("sleep 1", timeout=900000, background=False)["timeout_over_cap"])
        self.assertTrue(F("sleep 1", timeout=900000, background="true")["timeout_over_cap"])

    def test_longjob_invocation_fires(self):
        self.assertTrue(F("uv run python gates/corpus_sweep.py")["longjob_foreground"])

    def test_longjob_backgrounded_does_not_fire(self):
        cmd = "uv run python gates/corpus_sweep.py"
        self.assertFalse(F(cmd, background=True)["longjob_foreground"])

    def test_longjob_merely_NAMED_does_not_fire(self):
        self.assertFalse(F("grep -n 'corpus_sweep' gates/README.md")["longjob_foreground"])


class TestPreviouslyUnprobedRules(unittest.TestCase):
    """Every rule that a code review found had NO probe at all, or only one.

    A guard that has never been shown able to fire is decoration (CLAUDE.md #26), and
    these had shipped unproven: sed -i (0 probes), XRCatTool re-unpack (0), the two
    durable-record rules (1 between them), the timeout cap (1), shared-/tmp, and the
    save-game and X4-directory delete rules. Each gets must-fire AND must-not-fire.
    """

    # --- sed -i in the game or profile tree
    def test_sed_i_in_the_game_fires(self):
        self.assertTrue(F('sed -i s/a/b/ "' + GAME + '/f.xml"')["sed_i_in_game_or_profile"])

    def test_sed_i_elsewhere_does_not_fire(self):
        self.assertFalse(F("sed -i s/a/b/ /c/tmp/f.xml")["sed_i_in_game_or_profile"])

    def test_sed_WITHOUT_i_in_the_game_does_not_fire(self):
        self.assertFalse(F('sed s/a/b/ "' + GAME + '/f.xml"')["sed_i_in_game_or_profile"])

    # --- XRCatTool re-unpack into a locked reference tree
    def test_xrcat_unpack_into_reference_fires(self):
        cmd = 'XRCatTool.exe -in 01.cat -out "' + REF + '"'
        self.assertTrue(F(cmd)["xrcat_reunpack"])

    def test_xrcat_without_out_does_not_fire(self):
        self.assertFalse(F('XRCatTool.exe -in "' + REF + '/01.cat"')["xrcat_reunpack"])

    def test_xrcat_unpacking_elsewhere_does_not_fire(self):
        self.assertFalse(F("XRCatTool.exe -in 01.cat -out /c/tmp/out")["xrcat_reunpack"])

    # --- lifting the OS deny on reference/ (Plan 2 lanes D+E; D8: only the user lifts it)
    def test_icacls_remove_or_reset_on_reference_fires(self):
        for cmd in ('icacls "' + REF + '" /remove:d *S-1-5-21-1',
                    'icacls "' + REF + '" /reset /T /C',
                    'icacls "' + REF + '/libraries" /reset',
                    'icacls "' + REF.replace("/", BS) + '" /REMOVE:d *S-1-5-21-1'):
            self.assertTrue(F(cmd)["lifts_reference_deny"], cmd)

    def test_icacls_reset_recursive_from_an_ANCESTOR_of_reference_fires(self):
        self.assertTrue(F('icacls "C:/Users/tester/Desktop" /reset /T /C')["lifts_reference_deny"])

    def test_icacls_absolute_slash_operands_are_paths_not_switches(self):
        for ref in ("/home/tester/toolkit/reference", H.norm(REF), "/restore/toolkit/reference"):
            roots = dict(ROOTS, reference=ref)
            for action in ("/reset /T /C", "/grant:r user:F", "/remove:d user", "/restore acl.txt"):
                with self.subTest(ref=ref, action=action):
                    self.assertTrue(F('icacls "' + ref + '" ' + action, roots=roots)["lifts_reference_deny"])
            self.assertFalse(F('icacls "' + ref + '"', roots=roots)["lifts_reference_deny"])
            self.assertFalse(F('icacls "/elsewhere/tree" /reset /T', roots=roots)["lifts_reference_deny"])

    def test_icacls_switch_values_are_not_target_paths(self):
        ref = "/home/tester/toolkit/reference"
        self.assertEqual(H._icacls_paths('icacls "/elsewhere/tree" /restore "' + ref + '" /T'), ["/elsewhere/tree"])
        self.assertEqual(H._icacls_paths('icacls "' + ref + '" /grant:r "user:F" /T /C'), [ref])
        for root in (ref, REF):
            self.assertFalse(F('icacls "' + root + '" /save "/restore"', roots=dict(ROOTS, reference=root))["lifts_reference_deny"])

    def test_icacls_drive_operand_is_not_the_continue_switch(self):
        self.assertTrue(F('icacls /c /reset /T /C')["lifts_reference_deny"])
        self.assertFalse(F('icacls /c')["lifts_reference_deny"])
        self.assertFalse(F('icacls C:/tmp/outside /reset /C')["lifts_reference_deny"])
        self.assertEqual(H._icacls_paths('icacls /c /reset /T /C'), ['/c'])

    def test_x4refguard_remove_fires(self):
        for cmd in ("python scripts/x4refguard.py remove",
                    'uv run --no-project python "' + TOOLKIT + '/scripts/x4refguard.py" remove --path "' + REF + '"',
                    "py -3 " + Q + "scripts" + BS + "x4refguard.py" + Q + " remove"):
            self.assertTrue(F(cmd)["lifts_reference_deny"], cmd)

    def test_icacls_reading_applying_or_elsewhere_does_not_fire(self):
        for cmd in ('icacls "' + REF + '"',                                       # read only
                    'icacls "' + REF + '" /deny *S-1-5-21-1:(OI)(CI)(DE,DC)',     # APPLYING it
                    'icacls "C:/tmp/x" /reset /T',                                # elsewhere
                    'icacls "C:/Users/tester/Desktop" /reset',                    # ancestor, NOT recursive
                    "echo icacls " + REF + " /reset"):                            # a mention
            self.assertFalse(F(cmd)["lifts_reference_deny"], cmd)

    def test_x4refguard_status_or_apply_does_not_fire(self):
        for cmd in ("python scripts/x4refguard.py status", "python scripts/x4refguard.py apply",
                    "echo x4refguard.py remove is the user's step"):
            self.assertFalse(F(cmd)["lifts_reference_deny"], cmd)

    def test_lifting_with_no_reference_root_configured_does_not_fire(self):
        roots = dict(ROOTS, reference="")
        self.assertFalse(F('icacls "' + REF + '" /reset /T', roots=roots)["lifts_reference_deny"])

    # --- durable records
    def test_truncating_redirect_onto_a_durable_record_fires(self):
        self.assertTrue(F("echo x > KNOWLEDGEBASE.md")["durable_truncating_redirect"])

    def test_APPEND_onto_a_durable_record_does_not_fire(self):
        # >> cannot truncate, and appending to the knowledgebase is the normal way to
        # add an entry -- blocking it would make the routine case impossible.
        self.assertFalse(F("echo x >> KNOWLEDGEBASE.md")["durable_truncating_redirect"])

    def test_redirect_onto_an_ordinary_md_does_not_fire(self):
        self.assertFalse(F("echo x > notes.md")["durable_truncating_redirect"])

    def test_python_open_w_naming_a_durable_record_fires(self):
        cmd = "python -c \"open('MEMORY.md', 'w').write(x)\""
        self.assertTrue(F(cmd)["durable_python_open_w"])

    def test_python_open_r_naming_a_durable_record_does_not_fire(self):
        cmd = "python -c \"open('MEMORY.md', 'r').read()\""
        self.assertFalse(F(cmd)["durable_python_open_w"])

    # A `)` IN THE PATH. `open\([^)]*,` cannot cross one, and this workspace's game
    # root is under `Program Files (x86)` -- so the rule was structurally DEAD for
    # the two files its own docstring names as the real incident.
    def test_a_paren_in_the_path_does_not_kill_the_rule(self):
        cmd = ("python -c \"open('C:/Program Files (x86)/X4/CLAUDE.md','w')\"")
        self.assertTrue(F(cmd)["durable_python_open_w"])

    def test_a_paren_in_the_path_KNOWLEDGEBASE(self):
        cmd = ("python -c \"open('C:/Program Files (x86)/X4/KNOWLEDGEBASE.md','w')\"")
        self.assertTrue(F(cmd)["durable_python_open_w"])

    # --- measurement output into shared /tmp
    def test_redirect_into_tmp_fires(self):
        self.assertTrue(F("uv run x4validate > /tmp/out.log")["write_to_tmp"])

    def test_reading_from_tmp_does_not_fire(self):
        self.assertFalse(F("cat /tmp/out.log")["write_to_tmp"])

    def test_scratchpad_write_does_not_fire(self):
        self.assertFalse(F("echo x > /c/scratch/out.log")["write_to_tmp"])

    # --- deleting a save game
    def test_deleting_a_save_fires(self):
        self.assertTrue(F(D + ' -f "' + PROF + '/save/save_001.xml.gz"')["rm_saves"])

    def test_reading_a_save_does_not_fire(self):
        self.assertFalse(F('cat "' + PROF + '/save/save_001.xml.gz"')["rm_saves"])

    # --- deleting inside an X4 directory
    def test_deleting_inside_the_mods_tree_fires(self):
        self.assertTrue(F(D + ' -rf "' + ROOTS["mods"] + '/mymod"')["rm_in_x4_dir"])

    def test_deleting_an_unrelated_temp_file_does_not_fire(self):
        self.assertFalse(F(D + " -f /c/tmp/scratch.txt")["rm_in_x4_dir"])

    # --- the profile-by-name measurement trap
    def test_grepping_the_profile_by_name_fires(self):
        self.assertTrue(F('grep -i somemod "' + PROF + '/content.xml"')["profile_search_by_name"])

    def test_grepping_the_profile_by_MANIFEST_ID_does_not_fire(self):
        cmd = 'grep -i ws_3691358137 "' + PROF + '/content.xml"'
        self.assertFalse(F(cmd)["profile_search_by_name"])

    def test_grepping_an_unrelated_file_does_not_fire(self):
        self.assertFalse(F("grep -i somemod /c/tmp/other.xml")["profile_search_by_name"])

    # --- copy into the game or profile
    def test_copy_INTO_the_game_fires(self):
        self.assertTrue(F('cp -r mymod "' + GAME + '/extensions/"')["copy_into_game_or_profile"])

    def test_copy_OUT_of_the_game_does_not_fire(self):
        cmd = 'cp -r "' + GAME + '/extensions/mymod" /c/tmp/'
        self.assertFalse(F(cmd)["copy_into_game_or_profile"])

    # --- deleting the reference tree
    def test_deleting_reference_fires(self):
        self.assertTrue(F(D + ' -rf "' + REF + '"')["rm_targets_reference"])

    def test_reading_reference_does_not_fire(self):
        self.assertFalse(F('ls "' + REF + '"')["rm_targets_reference"])


class TestBareSystemPythonOnToolkitCode(unittest.TestCase):
    """instrument_hygiene.py's shape `bare-python-on-project-code`, now a PreToolUse
    rule. Positives (must DENY) and near-misses (must NOT), per its own definition:
    the command WORD is `python`/`python3`/`py`, and the thing it runs needs the
    `tools/x4validate` venv -- `-m pytest`/`-m x4validate`, a path under
    tools/x4validate/ (including one resolved by JOINING a relative operand
    against the shell's OWN cwd), or a bare `gates/...` reference.

    NARROWER than CLAUDE.md's routing-table wording ("tools/, scripts/, gates/,
    .claude/hooks/") on purpose -- see hook_facts.py's `_PROJECT_DIR` comment.
    Classifying every historical hit found `.claude/hooks/*.py` and the
    toolkit-ROOT `scripts/*.py` genuinely run fine under this machine's real bare
    Python 3.10 (stdlib only, or `.claude/hooks/` invoked bare BY THE HOOK
    INFRASTRUCTURE ITSELF), so this predicate does not cover them; several tests
    below pin that as a MEASURED near-miss, not an oversight.
    """

    # --- must DENY -----------------------------------------------------------
    # `-m pytest` / `-m x4validate` count WHERE the toolkit is -- a cwd under
    # tools/x4validate, or an explicit path there (v3.3.0 release review, finding 6).
    def test_bare_python_dash_m_pytest_fires(self):
        self.assertTrue(F("cd tools/x4validate && python -m pytest -q tests/")
                        ["bare_python_on_project_code"])
        self.assertTrue(F("python -m pytest -q tools/x4validate/tests")
                        ["bare_python_on_project_code"])

    def test_bare_python_dash_m_x4validate_fires(self):
        self.assertTrue(F("cd tools/x4validate && python -m x4validate --paths")
                        ["bare_python_on_project_code"])

    def test_the_payload_cwd_is_where_the_shell_starts(self):
        def f(cwd):
            return H.facts({"tool_input": {"command": "python -m pytest -q"}, "cwd": cwd},
                           ROOTS)["bare_python_on_project_code"]
        self.assertTrue(f(TOOLKIT + "/tools/x4validate"))
        self.assertTrue(f("C:" + BS + "tk" + BS + "tools" + BS + "x4validate" + BS + "tests"))
        self.assertFalse(f("/c/work/other-project"))

    def test_versioned_interpreter_names_are_bare_too(self):
        for v in ("python3.10", "python3.12", "python3.10.exe"):
            with self.subTest(v=v):
                self.assertTrue(F(v + " gates/claims_audit.py")["bare_python_on_project_code"])
        self.assertFalse(F("python2.7 gates/claims_audit.py")["bare_python_on_project_code"])
        self.assertFalse(F("python3.10-config --libs")["bare_python_on_project_code"])

    def test_TWIN_dash_m_pytest_outside_the_toolkit_does_not_fire(self):
        for cmd in ("python -m pytest -q", "cd /c/work/myproject && python -m pytest -q",
                    "python -m x4validate --paths", "python -m pytest tests/ -k tools"):
            with self.subTest(cmd=cmd):
                self.assertFalse(F(cmd)["bare_python_on_project_code"])

    def test_bare_python3_a_gates_script_fires(self):
        self.assertTrue(F("python3 gates/claims_audit.py")["bare_python_on_project_code"])

    def test_bare_py_a_tools_x4validate_path_fires(self):
        self.assertTrue(
            F("py tools/x4validate/gates/claims_audit.py")["bare_python_on_project_code"])

    def test_bare_python_a_bare_relative_name_with_toolkit_cwd_fires(self):
        cmd = "cd " + TOOLKIT + "/tools/x4validate && python local_helper.py"
        self.assertTrue(F(cmd)["bare_python_on_project_code"])

    def test_a_relative_package_path_joins_against_the_toolkit_cwd(self):
        # "x4validate/_livecli.py" alone names nothing project-shaped -- it is
        # only toolkit code once joined against a cwd already inside
        # tools/x4validate/, exactly as `tools/x4validate/x4validate/_livecli.py`
        # is laid out on disk. A real historical shape (xedit.py mutation probes).
        cmd = "cd " + TOOLKIT + "/tools/x4validate && python x4validate/_livecli.py"
        self.assertTrue(F(cmd)["bare_python_on_project_code"])

    def test_a_quoted_verb_still_fires(self):
        self.assertTrue(F('"python" gates/claims_audit.py')["bare_python_on_project_code"])

    def test_a_variable_spelled_verb_still_fires(self):
        # The F111 guarantee: resolve_verb splices the assignment BEFORE this rule
        # ever sees the segment, so the plain and variable spellings must agree.
        self.assertTrue(F("cd tools/x4validate && PY=python; $PY -m pytest -q")
                        ["bare_python_on_project_code"])

    def test_a_wrapper_does_not_get_you_out_of_it(self):
        # Consistent with every other verb-keyed rule in this file: `nice`/`env`/
        # `timeout` step OVER the verb, they do not hide it.
        self.assertTrue(
            F("nice -n 5 python gates/claims_audit.py")["bare_python_on_project_code"])

    # --- must NOT fire (the important half) -----------------------------------
    def test_uv_run_frozen_python_dash_m_pytest_does_not_fire(self):
        self.assertFalse(
            F("uv run --frozen python -m pytest -q")["bare_python_on_project_code"])

    def test_uv_run_python_a_gates_script_does_not_fire(self):
        self.assertFalse(
            F("uv run python gates/claims_audit.py")["bare_python_on_project_code"])

    # 2026-09-26, fuzz-guard: spelling the SYSTEM interpreter by its absolute path
    # walked past the rule ("VERB absolute path" / "VERB windows path" mutators).
    # The harm is the interpreter, not the spelling: absolute = bare unless it is a
    # virtual environment's (a pyvenv.cfg beside its bin/Scripts, or a .venv/venv dir).
    def test_an_absolute_SYSTEM_interpreter_path_fires(self):
        for verb in ("/usr/bin/python3", '"C:' + BS + "tools" + BS + 'python.exe"',
                     "C:/Users/user/AppData/Local/Programs/Python/Python310/python.exe"):
            with self.subTest(verb=verb):
                self.assertTrue(F("cd tools/x4validate && " + verb + " -m pytest -q")
                                ["bare_python_on_project_code"])

    def test_an_absolute_VENV_interpreter_path_does_not_fire(self):
        for verb in ("/c/proj/tools/x4validate/.venv/Scripts/python.exe",
                     "/home/user/proj/venv/bin/python3"):
            with self.subTest(verb=verb):
                self.assertFalse(F(verb + " -m pytest -q")["bare_python_on_project_code"])

    def test_an_absolute_interpreter_beside_a_real_pyvenv_cfg_does_not_fire(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            env = os.path.join(d, "myenv")
            os.makedirs(os.path.join(env, "Scripts"))
            with open(os.path.join(env, "pyvenv.cfg"), "w", encoding="utf-8") as fh:
                fh.write("home = x\n")
            exe = os.path.join(env, "Scripts", "python.exe").replace(BS, "/")
            self.assertFalse(F(exe + " -m pytest -q")["bare_python_on_project_code"])

    def test_a_venv_interpreter_path_does_not_fire(self):
        self.assertFalse(
            F(".venv/Scripts/python gates/claims_audit.py")["bare_python_on_project_code"])

    def test_an_unresolved_x4_python_variable_does_not_fire(self):
        self.assertFalse(
            F("$X4_PYTHON -m pytest -q")["bare_python_on_project_code"])

    def test_bare_python_version_does_not_fire(self):
        self.assertFalse(F("python --version")["bare_python_on_project_code"])

    def test_bare_python_dash_c_on_nothing_project_related_does_not_fire(self):
        self.assertFalse(F('python -c "print(1)"')["bare_python_on_project_code"])

    def test_bare_python_dash_c_from_a_toolkit_cwd_still_does_not_fire(self):
        # The cwd branch exists for a resolvable SCRIPT PATH, not for inline -c
        # code -- `-c`'s payload is not "toolkit code" merely for sitting in that
        # directory.
        cmd = "cd " + TOOLKIT + '/tools/x4validate && python -c "print(1)"'
        self.assertFalse(F(cmd)["bare_python_on_project_code"])

    def test_heredoc_fed_stdin_from_a_toolkit_cwd_does_not_fire(self):
        # MEASURED: `python - <<PYEOF ... PYEOF` -- python's own "read the script
        # from stdin" idiom -- had its HEREDOC MARKER misread as the script path
        # before this was fixed; 963 of 1,788 pre-fix hits were exactly this
        # shape. Neither the marker nor a toolkit cwd makes the piped-in text a
        # PROJECT FILE.
        cmd = ("cd " + TOOLKIT + "/tools/x4validate && python - <<PYEOF" + chr(10)
               + "print(1)" + chr(10) + "PYEOF")
        self.assertFalse(F(cmd)["bare_python_on_project_code"])

    def test_a_scratch_script_does_not_fire_despite_a_toolkit_cwd(self):
        # MEASURED false-positive shape: `S=<scratchpad>; cd tools/x4validate &&
        # python "$S/xedit.py" ...` -- a real recurring pattern (25 hits
        # pre-fix). The SCRIPT being run resolves to an absolute scratch path;
        # the shell merely being inside tools/x4validate for an unrelated later
        # command in the same chain must not make that script toolkit code.
        cmd = ('S="/c/scratch/fu-hook"; cd ' + TOOLKIT
               + '/tools/x4validate && python "$S/xedit.py" a b')
        self.assertFalse(F(cmd)["bare_python_on_project_code"])

    def test_a_standalone_scratch_script_does_not_fire(self):
        self.assertFalse(
            F("python /c/scratch/fu-hook/probe.py")["bare_python_on_project_code"])

    def test_inside_a_heredoc_body_is_DATA_not_a_command(self):
        cmd = "cat > notes.md <<X" + chr(10) + "python gates/claims_audit.py" + chr(10) + "X"
        self.assertFalse(F(cmd)["bare_python_on_project_code"])

    # --- MEASURED near-misses: these genuinely run fine bare on this machine ---
    def test_dot_claude_hooks_does_not_fire(self):
        # protect-bash.sh itself invokes hook_facts.py through a bare `$PY`, never
        # `uv run` -- denying this would be advice against the hook infrastructure's
        # own intended invocation. Stdlib only; MEASURED to actually work (ran this
        # very file's suite under the real system Python 3.10 while building this
        # rule).
        self.assertFalse(
            F("python .claude/hooks/test_hook_facts.py")["bare_python_on_project_code"])

    def test_toolkit_root_scripts_do_not_fire(self):
        # scripts/x4lock.py imports stdlib only (os, stat, argparse, pathlib) --
        # MEASURED across the historical corpus's most frequent hits before this
        # predicate was scoped down (x4lock.py, x4canary.py, scan-identifiers.py,
        # verify-hook-tests.py, fuzz-guard.py, audit-coverage.py: 616 hits between
        # them, none a real ModuleNotFoundError risk).
        cmd = "cd " + TOOLKIT + " && python scripts/x4lock.py status"
        self.assertFalse(F(cmd)["bare_python_on_project_code"])

    def test_tools_basex_does_not_fire(self):
        # ask.py/staleness.py import a local sibling module, not a project
        # dependency -- a different venv-less corner of the toolkit from
        # tools/x4validate.
        cmd = "cd " + TOOLKIT + "/tools/basex && python staleness.py --check"
        self.assertFalse(F(cmd)["bare_python_on_project_code"])


class TestWrappedCommands(unittest.TestCase):
    """`bash -c "<command>"` hid everything inside it from every rule. Pre-existing, and
    segment-splitting made the pipeline form structural -- so the parse pass descends."""

    def test_delete_inside_bash_c_is_seen(self):
        inner = D + ' -rf "' + GAME + '"'
        self.assertTrue(F("bash -c '" + inner + "'")["rm_hits_game"])

    def test_search_inside_bash_c_is_seen(self):
        self.assertTrue(F('bash -c \'grep -rn foo "' + REF + '"\'')["search_rooted_reference"])

    def test_a_harmless_bash_c_does_not_fire(self):
        self.assertFalse(F("bash -c 'echo hello'")["rm_hits_game"])


class TestCliContract(unittest.TestCase):
    """The CLI is what protect-bash.sh actually calls, and it has its own failure modes.

    Roots travel on STDIN rather than the environment because MSYS/Git-Bash TRANSLATES a
    POSIX-looking value when passing an env var to a NATIVE Windows process: bash
    exported "/tmp/x/docs" and Python received "C:/Users/.../AppData/Local/Temp/x/docs",
    while the command text still said "/tmp/x/docs". No path rule could match, and every
    one of them went quiet while the hook looked healthy.
    """

    def _run(self, stdin_bytes, env=None):
        import os
        import subprocess
        e = dict(os.environ)
        if env:
            e.update(env)
        return subprocess.run([sys.executable, str(pathlib.Path(__file__).parent / "hook_facts.py")],
                              input=stdin_bytes, capture_output=True, env=e)

    def _payload(self, cmd):
        import json
        return json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})

    def test_roots_arrive_on_stdin_and_a_posix_root_matches(self):
        head = "documents\t/tmp/sbx/docs\n" + H.ROOT_SEP
        body = self._payload("echo x > '/tmp/sbx/docs/notes.txt'")
        p = self._run((head + body).encode())
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("writes_documents\t1", p.stdout.decode())

    def test_output_carries_no_carriage_returns(self):
        # Text-mode stdout on Windows turned every "1" into "1\r", the shell compared it
        # against "1", every predicate read false, and the hook allowed everything.
        head = "game\tC:/g\n" + H.ROOT_SEP
        p = self._run((head + self._payload("echo hi")).encode())
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn(b"\r", p.stdout)

    def test_empty_payload_refuses_rather_than_allowing(self):
        self.assertEqual(self._run(b"").returncode, 2)

    def test_unparseable_payload_refuses_rather_than_allowing(self):
        head = "game\tC:/g\n" + H.ROOT_SEP
        self.assertEqual(self._run((head + "not json").encode()).returncode, 3)

    def test_the_command_survives_verbatim_after_the_sentinel(self):
        cmd = "echo one" + chr(10) + "echo two"
        head = "game\tC:/g\n" + H.ROOT_SEP
        out = self._run((head + self._payload(cmd)).encode()).stdout.decode()
        self.assertTrue(out.endswith(cmd), repr(out[-60:]))


# ------------------------------------------------- operands resolve against cwd
class TestCwdRelativeOperands(unittest.TestCase):
    """A path named RELATIVE to a `cd` is the same path.

    MEASURED 2026-09-01 against c400a05 (the last state where the rules ran): the
    parse-pass rewrite traded whole-string grepping for structured operands, and so
    became blind to every INDIRECT way of naming a path. Nine cases, three of them
    verdicts the old hook produced and this one had lost:

        cd <saves> && rm -f *.xml.gz        ask    -> allow   REGRESSION
        rm -rf "$(echo <game>)"             deny   -> allow   REGRESSION
        cd <game> && echo x > notes.txt     advise -> allow   REGRESSION
        cd <game> && rm -rf .               allow  -> allow   pre-existing gap
        cd <reference> && rm -rf assets     allow  -> allow   pre-existing gap
        ... and the pushd / subshell / extensions variants

    The cause is ONE thing, which is why the fix is one thing: an operand was
    classified as written instead of as resolved. `cwd_of` already existed and was
    consumed only by the search rules.
    """

    def test_cd_then_relative_delete_of_extensions_is_the_game_delete(self):
        f = F('cd "%s" && %s -rf extensions' % (GAME, D))
        self.assertTrue(f["rm_hits_game"])

    def test_cd_then_delete_dot_is_the_game_root(self):
        f = F('cd "%s" && %s -rf .' % (GAME, D))
        self.assertTrue(f["rm_hits_game"])

    def test_cd_then_relative_delete_under_reference(self):
        f = F('cd "%s" && %s -rf assets' % (REF, D))
        self.assertTrue(f["rm_targets_reference"])

    def test_cd_then_relative_delete_of_saves(self):
        f = F('cd "%s" && %s -f *.xml.gz' % (ROOTS["saves"], D))
        self.assertTrue(f["rm_saves"])

    def test_pushd_relocates_like_cd(self):
        f = F('pushd "%s" && %s -rf extensions' % (GAME, D))
        self.assertTrue(f["rm_hits_game"])

    def test_popd_returns_to_the_previous_directory(self):
        # Without a stack, ignoring popd would resolve `build` against the game and
        # invent a false positive. The pop must actually pop.
        f = F('pushd "%s" && ls; popd && %s -rf build' % (GAME, D))
        self.assertFalse(f["rm_in_x4_dir"])

    def test_subshell_cd_relocates(self):
        f = F('(cd "%s" && %s -rf extensions)' % (GAME, D))
        self.assertTrue(f["rm_hits_game"])

    def test_cd_into_extensions_then_delete_one_mod_confirms(self):
        # One deployed mod is a redeploy, not a catastrophe: confirm, never hard block.
        f = F('cd "%s/extensions" && %s -rf amod' % (GAME, D))
        self.assertTrue(f["rm_in_x4_dir"])
        self.assertFalse(f["rm_hits_game"])

    def test_cd_then_relative_redirect_is_a_write_into_the_game(self):
        f = F('cd "%s" && echo x > notes.txt' % GAME)
        self.assertTrue(f["redirect_truncate_into_game_or_profile"])

    def test_cd_then_relative_copy_into_the_game(self):
        f = F('cd "%s" && cp /c/tmp/a extensions/a' % GAME)
        self.assertTrue(f["copy_into_game_or_profile"])

    # --- the other side: a relocation that is NOT into a protected root ----------
    def test_cd_elsewhere_then_delete_is_untouched(self):
        f = F('cd /c/build && %s -rf out' % D)
        self.assertFalse(f["rm_in_x4_dir"])
        self.assertFalse(f["rm_hits_game"])

    def test_a_relative_cd_cannot_be_resolved_and_must_not_guess(self):
        # The hook does not know the shell's real cwd, so `cd extensions` is
        # unresolvable. Guessing a root here would fire on unrelated work.
        f = F('cd extensions && %s -rf amod' % D)
        self.assertFalse(f["rm_in_x4_dir"])

    def test_delete_outside_any_root_still_allowed(self):
        f = F('%s -f /c/Windows/Temp/scratch.txt' % D)
        self.assertFalse(f["rm_in_x4_dir"])
        self.assertFalse(f["rm_hits_game"])


# ------------------------------------------- unresolvable operands, deletes only
class TestUnresolvedDeleteOperands(unittest.TestCase):
    """An operand a hook CANNOT resolve is not evidence of safety.

    Scoped to deletes by user decision (2026-09-01): a delete is the one channel
    with no backup behind it -- MEASURED, 0 of 186 auto-backups cover anything
    outside dev/, and savegames are covered by nothing. Writes keep their existing
    verdicts, so this cannot add prompts to routine work.
    """

    def test_command_substitution_counts_as_unresolved(self):
        # `$(...)` and backticks were invisible to has_unresolved, which tests only
        # for $NAME / ${NAME}. So the whole token read as a literal path, matched no
        # root, and a delete of the game root through `$(echo ...)` was allowed.
        self.assertTrue(H.has_unresolved("$(echo x)"))
        self.assertTrue(H.has_unresolved(chr(96) + "echo x" + chr(96)))
        self.assertTrue(H.has_unresolved("$NAME"))
        self.assertTrue(H.has_unresolved("${NAME}"))
        self.assertFalse(H.has_unresolved("/plain/path"))

    def test_delete_through_command_substitution_naming_the_root(self):
        f = F('%s -rf "$(echo %s)"' % (D, GAME))
        self.assertTrue(f["rm_in_x4_dir"])

    def test_delete_of_a_root_env_var_by_name(self):
        # $X4_GAME never appears expanded in the text, so no amount of string
        # matching finds the root. The VARIABLE NAME is the evidence.
        f = F('%s -rf "$X4_GAME"' % D)
        self.assertTrue(f["rm_in_x4_dir"])
        # FX-G2: a LEADING root variable now resolves to its root (prep -> subst_root_var), so
        # the name-only branch is what still catches one that does NOT lead the operand.
        f = F('%s -rf "${PFX}$X4_GAME/x"' % D)
        self.assertTrue(f["rm_in_x4_dir"])

    def test_delete_of_the_saves_env_var_by_name(self):
        f = F('%s -rf "$X4_SAVES"' % D)
        self.assertTrue(f["rm_saves"])

    def test_the_NAME_backstop_still_fires_on_an_unresolvable_path(self):
        """The name backstop must NOT skip unresolved operands.

        It is the only protection an unconfigured machine has, and there the visible
        text is the evidence: this path still ends in the game's name. A `not u` filter
        added here on 2026-09-01 removed that last defence, and the 13,041-command
        corpus could not detect it because no historical command has this shape -- only
        the mutation gate did.
        """
        f = F('%s -rf "$BUILD/X4 Foundations"' % D)
        self.assertTrue(f["rm_hits_game"])

    def test_the_NAME_backstop_fires_even_with_NO_roots_configured(self):
        f = F('%s -rf "$BUILD/X4 Foundations"' % D, roots={})
        self.assertTrue(f["rm_hits_game"])

    def test_an_unrelated_variable_is_not_assumed_dangerous(self):
        f = F('%s -rf "$BUILD_DIR"' % D)
        self.assertFalse(f["rm_in_x4_dir"])
        self.assertFalse(f["rm_hits_game"])

    def test_an_unresolved_WRITE_is_still_not_guessed_at(self):
        # Deletes only. A write through an unresolved variable keeps today's verdict.
        f = F('echo x > "$SOME_DIR/notes.txt"')
        self.assertFalse(f["redirect_truncate_into_game_or_profile"])


# ------------------------------------------- prose must not blind the guard (C1)
class TestUnbalancedQuoteDoesNotBlindTheGuard(unittest.TestCase):
    """One apostrophe in an English comment used to disable EVERY rule after it.

    MEASURED 2026-09-01 against c400a05, which refused both members of every pair:
    5 of 5 refusals became a silent allow -- the game-delete HARD BLOCK, the reference
    block, the savegame confirm, the reference-search advisory and `git add -A`. The
    parse pass is quote-aware everywhere, so an unclosed quote converts the rest of the
    command into text no rule can see, and a rule that sees nothing returns False, which
    is indistinguishable from "this is fine". A REGRESSION the rewrite introduced: the
    old bash hook grepped the raw string and was unaffected.

    Position pins the mechanism: the same apostrophe placed AFTER the command still
    fires, because only text following the stray quote is blinded.
    """
    NL = chr(10)

    def _pair(self, prose_plain, prose_apos, tail, key):
        plain = F(prose_plain + self.NL + tail)
        apos = F(prose_apos + self.NL + tail)
        self.assertTrue(plain[key], "control did not fire; the test proves nothing")
        self.assertTrue(apos[key], "an apostrophe in a comment blinded %s" % key)

    def test_comment_apostrophe_does_not_hide_a_game_delete(self):
        self._pair("# clean up: this does not need it", "# clean up: this doesn't need it",
                   '%s -rf "%s"' % (D, GAME), "rm_hits_game")

    def test_comment_apostrophe_does_not_hide_a_reference_delete(self):
        self._pair("# it is a cleanup", "# it's a cleanup",
                   '%s -rf "%s"' % (D, REF), "rm_targets_reference")

    def test_comment_apostrophe_does_not_hide_a_save_delete(self):
        self._pair("# it is a cleanup", "# it's a cleanup",
                   '%s -f "%s/save_001.xml.gz"' % (D, ROOTS["saves"]), "rm_saves")

    def test_comment_apostrophe_does_not_hide_git_add_all(self):
        self._pair("# it is a fresh tree", "# it's a fresh tree",
                   "git add -A", "git_add_all")

    def test_comment_apostrophe_does_not_hide_a_rooted_search(self):
        self._pair("# we do not need a denominator", "# we don't need a denominator",
                   'grep -rn foo "%s"' % REF, "search_rooted_reference")

    def test_an_apostrophe_AFTER_the_command_was_never_the_problem(self):
        f = F('%s -rf "%s"  # we don%st need it' % (D, GAME, chr(39)))
        self.assertTrue(f["rm_hits_game"])
class TestEscapesOutsideQuotes(unittest.TestCase):
    """A backslash outside quotes escapes the next character, so it must not open a
    quote state. Exercised through the CONSEQUENCE: with the escape unhandled, `_scan`
    stops splitting on `&&` and the delete after it becomes invisible.

    An earlier version of this test asked ends_open_quote() instead -- which had its own
    escape handling, so it passed with _scan's removed and the planted mutant survived.
    A test must touch the code it claims to pin.
    """

    def test_an_escaped_apostrophe_does_not_blind_the_next_command(self):
        f = F("echo don" + BS + "'t && " + D + ' -rf "%s"' % GAME)
        self.assertTrue(f["rm_hits_game"])

    def test_a_comment_apostrophe_does_not_hide_a_long_job(self):
        # pins that the STRING-matching rules read the cleaned body, not the raw command
        f = F("# it's a sweep" + chr(10) + "uv run python gates/corpus_sweep.py")
        self.assertTrue(f["longjob_foreground"])


class TestFindDeletes(unittest.TestCase):
    """`find <root> -delete` and `find <root> -exec rm` remove files as surely as rm.

    MEASURED 2026-09-01 over 13,277 historical commands: `-delete` appears 4 times (none
    on a protected root) and `-exec rm` 0 times -- a 0-incidence gap, fixed because the
    failure mode is an unguarded delete of the game install, not because it was observed.
    """

    def test_find_delete_on_the_game_is_a_game_delete(self):
        self.assertTrue(F('find "%s" -delete' % GAME)["rm_hits_game"])

    def test_find_exec_rm_on_the_game_is_a_delete(self):
        self.assertTrue(F('find "%s" -exec %s -rf {} ;' % (GAME, D))["rm_in_x4_dir"])

    def test_a_find_that_does_NOT_delete_is_not_a_delete(self):
        """Deliberately UNFILTERED (-type is not a name filter), so the delete check is
        the clause actually under test. With `-name x` the filter guard returns first and
        SHADOWS it -- a mutant removing the delete check then survived, which is how a
        test can look like coverage it does not provide."""
        self.assertFalse(F('find "%s" -type d' % GAME)["rm_hits_game"])

    def test_a_FILTERED_find_is_scoped_and_does_not_fire(self):
        """`find . -name __pycache__ -exec rm -rf {} +` is routine hygiene, not a tree
        delete. MEASURED 2026-09-01: treating it like one added 40 prompts across 13,285
        commands, every one a cache cleanup -- noise by this project's own standard."""
        cmd = ('cd "%s" && find . -name __pycache__ -type d -exec %s -rf {} +'
               % (TOOLKIT, D))
        self.assertFalse(F(cmd)["rm_in_x4_dir"])

    def test_a_filtered_find_under_the_GAME_is_also_scoped(self):
        """SCOPED, not EXEMPT. A filter keeps a find-delete off the whole-install hard
        block -- it removes entries inside the tree, not the tree -- but it is still a
        delete in an X4 directory. This test used to assert only the first half, and the
        code honoured it by returning NO delete at all, so every in-tree rule went blind
        (AUDIT-2026-09-24 HK-2: `find <saves> -name '*.xml.gz' -delete` was ALLOW)."""
        f = F('find "%s" -name x -delete' % GAME)
        self.assertFalse(f["rm_hits_game"])
        self.assertTrue(f["rm_in_x4_dir"])

    def test_a_find_delete_outside_every_root_is_untouched(self):
        self.assertFalse(F("find /c/tmp -delete")["rm_in_x4_dir"])


class TestTimeoutShapes(unittest.TestCase):
    """A JSON number may be a float and a client may send a string; isinstance(int)
    turned the cap OFF for both. MEASURED: 0 of 13,277 historical calls used anything but
    int, so this is robustness rather than an observed bug -- recorded as such."""

    def test_an_int_over_the_cap_fires(self):
        self.assertTrue(F("sleep 1", timeout=900000)["timeout_over_cap"])

    def test_a_float_over_the_cap_fires(self):
        self.assertTrue(F("sleep 1", timeout=900000.0)["timeout_over_cap"])

    def test_a_string_over_the_cap_fires(self):
        self.assertTrue(F("sleep 1", timeout="900000")["timeout_over_cap"])

    def test_under_the_cap_does_not(self):
        self.assertFalse(F("sleep 1", timeout=500000)["timeout_over_cap"])

    def test_a_bool_is_not_a_timeout(self):
        # True is an int in Python; without the explicit exclusion it would read as 1.
        self.assertFalse(F("sleep 1", timeout=True)["timeout_over_cap"])

    def test_unparseable_keeps_the_rule_OFF_rather_than_firing_on_nonsense(self):
        self.assertFalse(F("sleep 1", timeout="abc")["timeout_over_cap"])


class TestStripComments(unittest.TestCase):
    """`#` starts a comment only at a word boundary outside quotes. The boundary test is
    what keeps $#, ${x#y} and a URL fragment intact."""

    def test_a_comment_keeps_its_newline_which_is_a_separator(self):
        out = H.strip_comments("# note" + chr(10) + "echo hi")
        self.assertIn(chr(10), out)
        self.assertIn("echo hi", out)

    def test_parameter_expansion_hash_is_not_a_comment(self):
        self.assertEqual(H.strip_comments('echo "${p#/a}"'), 'echo "${p#/a}"')

    def test_a_url_fragment_is_not_a_comment(self):
        self.assertEqual(H.strip_comments("curl http://a#b"), "curl http://a#b")

    def test_a_quoted_hash_is_not_a_comment(self):
        self.assertEqual(H.strip_comments("grep -n '#define' f.c"), "grep -n '#define' f.c")


class TestHeredocBodyIsDataForEveryRule(unittest.TestCase):
    """A heredoc body is text being WRITTEN, not commands. It was stripped for four
    rules and not for the other seven, so a body line reading like a delete produced a
    non-overridable hard deny on a command that only writes a file."""

    def test_a_delete_inside_a_heredoc_body_is_not_a_delete(self):
        cmd = ("cat > notes.md <<X" + chr(10)
               + '%s -rf "%s"' % (D, GAME) + chr(10) + "X")
        self.assertFalse(F(cmd)["rm_hits_game"])

    def test_a_search_inside_a_heredoc_body_is_not_a_search(self):
        cmd = ("cat > notes.md <<X" + chr(10)
               + 'grep -rn foo "%s"' % REF + chr(10) + "X")
        self.assertFalse(F(cmd)["search_rooted_reference"])

    def test_but_a_REAL_delete_beside_a_heredoc_still_fires(self):
        cmd = ("cat > notes.md <<X" + chr(10) + "just text" + chr(10) + "X" + chr(10)
               + '%s -rf "%s"' % (D, GAME))
        self.assertTrue(f_ := F(cmd)["rm_hits_game"], f_)


# ---------------------------------------------------- COMMAND RESOLUTION (2026-09-02)
#
# One root cause behind six separate total bypasses: the parse pass modelled shell
# GRAMMAR well and shell COMMAND RESOLUTION not at all. Two halves --
#   (1) what a verb token NAMES: `/bin/rm`, `rm.exe` and `$'rm'` are all `rm`, and
#       every verb-keyed rule compared the token verbatim;
#   (2) which constructs CARRY a command: a substitution, a shell heredoc, `trap`,
#       and a nested `-c` each run text that reached no rule.
# MEASURED E2E through protect-bash.sh before the fix: 14 of 16 probes returned a
# silent ALLOW against a game-root delete the guard catches when written bare.


class TestVerbNamesAreResolved(unittest.TestCase):
    def test_an_absolute_path_is_the_same_command(self):
        self.assertEqual(H.verb(H._unwrap("/bin/" + D + " -rf x")), D)

    def test_a_windows_path_is_the_same_command(self):
        # QUOTED, which is how a Windows path is actually written in a shell command.
        # norm() must run BEFORE basename: posixpath.basename does not split on a
        # backslash, so without it the whole string comes back as one "name".
        self.assertEqual(
            H.verb(H._unwrap(chr(34) + "C:" + BS + "tools" + BS + D + ".exe" + chr(34)
                             + " -rf x")), D)

    def test_an_UNQUOTED_windows_path_is_not_that_command_at_all(self):
        """Not a gap -- bash's own semantics. Unquoted, each backslash is an ESCAPE, so
        the token is the single word `C:toolsrm` and bash would look for a command by
        exactly that name. Resolving it to the delete verb would be inventing a threat
        the shell will never execute."""
        self.assertEqual(H.verb(H._unwrap("C:" + BS + "tools" + BS + D + " -rf x")),
                         "c:tools" + D)

    def test_a_dot_exe_suffix_is_the_same_command(self):
        self.assertEqual(H.verb(H._unwrap(D + ".exe -rf x")), D)

    def test_ansi_c_quoting_is_not_part_of_the_name(self):
        # `$'rm'` tokenised to `$rm` AND set the quoted flag, so verb()'s
        # `if quoted: return t` short-circuited with a name no rule has heard of.
        self.assertEqual(H.verb(H._unwrap("$" + chr(39) + D + chr(39) + " -rf x")), D)

    def test_the_hard_block_survives_every_spelling(self):
        for cmd in ("/bin/" + D + ' -rf "%s"' % GAME,
                    D + '.exe -rf "%s"' % GAME,
                    "$" + chr(39) + D + chr(39) + ' -rf "%s"' % GAME):
            with self.subTest(cmd=cmd):
                self.assertTrue(F(cmd)["rm_hits_game"], cmd)

    # --- must NOT over-strip -------------------------------------------------
    def test_only_dot_exe_is_stripped(self):
        """A general extension strip would break these: they ARE the command names."""
        for cmd, want in (("done_marker.sh", "done_marker.sh"),
                          ("function_helper.py run", "function_helper.py"),
                          ("casefold.py", "casefold.py")):
            with self.subTest(cmd=cmd):
                self.assertEqual(H.verb(H._unwrap(cmd)), want)


class TestWrapperVerbs(unittest.TestCase):
    def test_each_wrapper_is_seen_through(self):
        for w in ("timeout 5", "exec", "setsid", "nice -n 5", "ionice -c2"):
            with self.subTest(wrapper=w):
                self.assertTrue(F(w + " " + D + ' -rf "%s"' % GAME)["rm_hits_game"], w)

    def test_a_duration_is_not_mistaken_for_the_command(self):
        self.assertEqual(H.verb(H._unwrap("timeout 30s " + D + " -rf x")), D)

    def test_an_xargs_placeholder_is_not_the_command(self):
        self.assertEqual(H.verb(H._unwrap("xargs -I {} " + D + " -rf x")), D)

    def test_a_bare_number_is_still_a_verb_without_a_wrapper(self):
        """The skip is scoped to AFTER a wrapper. Outside that it must not apply, or
        a command really named oddly would silently lose its verb."""
        self.assertEqual(H.verb(H._unwrap("5 --flag")), "5")


class TestSubstitutionsCarryCommands(unittest.TestCase):
    def test_dollar_paren(self):
        self.assertTrue(F("echo $(" + D + ' -rf "%s")' % GAME)["rm_hits_game"])

    def test_backticks(self):
        self.assertTrue(F("echo " + chr(96) + D + ' -rf "%s"' % GAME + chr(96))["rm_hits_game"])

    def test_process_substitution(self):
        self.assertTrue(F("cat <(" + D + ' -rf "%s")' % GAME)["rm_hits_game"])

    def test_inside_SINGLE_quotes_nothing_runs(self):
        """The twin. Single quotes make it literal text, and treating it as a command
        would deny writing documentation that quotes one."""
        self.assertFalse(F("echo " + chr(39) + "$(" + D + ' -rf "%s")' % GAME
                           + chr(39) + " >> notes.md")["rm_hits_game"])

    def test_arithmetic_is_not_a_subshell(self):
        self.assertFalse(F("n=$((1 << 4)); echo $n")["rm_hits_game"])

    def test_the_extractor_returns_the_inner_text(self):
        self.assertEqual(H.substitutions("echo $(ls -la)"), ["ls -la"])
        self.assertEqual(H.substitutions("echo " + chr(96) + "ls" + chr(96)), ["ls"])
        self.assertEqual(H.substitutions("echo " + chr(39) + "$(ls)" + chr(39)), [])


class TestTrapCarriesACommand(unittest.TestCase):
    def test_trap_runs_its_first_operand(self):
        self.assertTrue(F("trap " + chr(39) + D + ' -rf "%s"' % GAME
                          + chr(39) + " EXIT")["rm_hits_game"])

    def test_a_benign_trap_is_still_benign(self):
        self.assertFalse(F("trap " + chr(39) + "echo bye" + chr(39) + " EXIT")["rm_hits_game"])


class TestEvalIsNotAlwaysTokenZero(unittest.TestCase):
    def test_a_wrapper_may_precede_eval(self):
        # `time eval <cmd>` sliced from toks[1:] produced the string "eval <cmd>",
        # whose own verb is `eval`, so no delete rule fired on that either.
        self.assertTrue(F("time eval " + chr(39) + D + ' -rf "%s"' % GAME
                          + chr(39))["rm_hits_game"])

    def test_bare_eval_still_works(self):
        self.assertTrue(F("eval " + chr(39) + D + ' -rf "%s"' % GAME
                          + chr(39))["rm_hits_game"])


class TestHeredocBodiesOnlyRunForAShell(unittest.TestCase):
    def _hd(self, opener, body):
        return opener + " <<" + chr(39) + "EOF" + chr(39) + chr(10) + body + chr(10) + "EOF"

    def test_a_shell_runs_its_heredoc(self):
        self.assertTrue(F(self._hd("bash", D + ' -rf "%s"' % GAME))["rm_hits_game"])

    def test_cat_writing_a_file_does_NOT(self):
        """Pinned: the body is the PAYLOAD of a file being written. Routing it would
        hard-deny writing documentation that quotes a dangerous command."""
        self.assertFalse(F(self._hd("cat > notes.md", D + ' -rf "%s"' % GAME))["rm_hits_game"])

    def test_a_python_heredoc_does_NOT(self):
        """Python, not shell. An assignment of a path to a name would parse as a
        delete verb under a shell tokeniser, inventing deletes out of assignments."""
        body = D + " = " + chr(34) + GAME + chr(34)
        self.assertFalse(F(self._hd("python -", body))["rm_hits_game"])

    def test_heredoc_bodies_returns_only_shell_openers(self):
        self.assertEqual(H.heredoc_bodies(self._hd("bash", "ls")), ["ls"])
        self.assertEqual(H.heredoc_bodies(self._hd("cat > n.md", "ls")), [])


NL = chr(10)
BQ = chr(96)                       # backtick


class TestAHeredocInsideASubstitutionIsStillData(unittest.TestCase):
    """v4.0.0 review FX-P2, reported by another lane: `git commit -m "$(cat <<'EOF' ... EOF
    )"` -- the standard commit-message idiom -- was DENIED as "truncating redirect onto a
    durable record" when the message mentioned `> KNOWLEDGEBASE.md`. Root cause: the top
    level correctly ignores a `<<` inside double quotes (`echo "a <<M b"` opens nothing), so
    the body stayed in the text; the `$(...)` was then walked as a carried command WITHOUT
    the heredoc strip the top level gets, and its body lines became commands.
    The mirror image was a miss: `x=$(bash <<'EOF' ... EOF)` -- a heredoc that really feeds a
    shell -- reached no rule, because the shell test looked at the opener LINE's segments
    (`x=$(bash`), not at the command inside the substitution."""

    def _sub(self, opener, body, tail="", dq=True):
        q = DQ if dq else ""
        return ("x=" + q + "$(" + opener + " <<" + Q + "EOF" + Q + NL + body + NL + "EOF" + NL
                + tail + ")" + q)

    def test_the_reported_commit_idiom_is_not_a_durable_write(self):
        cmd = ("git commit -m " + DQ + "$(cat <<" + Q + "EOF" + Q + NL
               + "fix: a > KNOWLEDGEBASE.md is now denied" + NL + NL
               + "Co-Authored-By: X <noreply@example.com>" + NL + "EOF" + NL + ")" + DQ)
        self.assertFalse(F(cmd)["durable_truncating_redirect"])

    def test_a_delete_in_a_data_body_inside_a_substitution_is_not_a_delete(self):
        for dq in (True, False):
            with self.subTest(dq=dq):
                self.assertFalse(F(self._sub("cat", DEL_GAME, dq=dq))["rm_hits_game"])

    def test_a_shell_fed_body_inside_a_substitution_still_runs(self):
        """The control the fix must keep (quoted) and the miss it closes (unquoted)."""
        for opener in ("bash", "sh -s", "cat | bash"):
            for dq in (True, False):
                with self.subTest(opener=opener, dq=dq):
                    self.assertTrue(F(self._sub(opener, DEL_GAME, dq=dq))["rm_hits_game"])
        self.assertTrue(F(self._sub("sh -s", "echo a > KNOWLEDGEBASE.md", dq=False))
                        ["durable_truncating_redirect"])

    def test_a_real_command_after_the_body_in_the_same_substitution_still_runs(self):
        for dq in (True, False):
            with self.subTest(dq=dq):
                self.assertTrue(F(self._sub("cat", "text", "echo a > KNOWLEDGEBASE.md", dq=dq))
                                ["durable_truncating_redirect"])
                self.assertTrue(F(self._sub("cat", "text", DEL_GAME, dq=dq))["rm_hits_game"])


class TestAnUnquotedHeredocRunsItsSubstitutions(unittest.TestCase):
    """v4.0.0 review FX-P2, found while reproducing the false positive above: with an
    UNQUOTED delimiter bash expands the body, so a `$(...)` or backtick in it RUNS. The body
    was stripped as data whole, so `cat <<EOF` / `$(<delete the game>)` / `EOF` was a silent
    ALLOW against a hard block. Only the substitutions run -- the rest is still data."""

    def _hd(self, delim, body, opener="cat > notes.md"):
        return opener + " <<" + delim + NL + body + NL + "EOF"

    def test_dollar_paren_and_backticks_in_an_unquoted_body_run(self):
        for body in ("$(" + DEL_GAME + ")", BQ + DEL_GAME + BQ,
                     "it" + Q + "s prose, then $(" + DEL_GAME + ")",
                     'a " quote, then ' + BQ + DEL_GAME + BQ):
            for opener in ("cat > notes.md", "git commit -F -"):
                with self.subTest(body=body, opener=opener):
                    self.assertTrue(F(self._hd("EOF", body, opener))["rm_hits_game"])

    # --- one falsification twin per clause ---
    def test_TWIN_a_quoted_delimiter_expands_nothing(self):
        for delim in (Q + "EOF" + Q, DQ + "EOF" + DQ, BS + "EOF", "E" + Q + "OF" + Q):
            with self.subTest(delim=delim):
                self.assertFalse(F(self._hd(delim, "$(" + DEL_GAME + ")"))["rm_hits_game"])

    def test_TWIN_an_escaped_substitution_is_text(self):
        for body in (BS + "$(" + DEL_GAME + ")", BS + BQ + DEL_GAME + BS + BQ):
            with self.subTest(body=body):
                self.assertFalse(F(self._hd("EOF", body))["rm_hits_game"])

    def test_TWIN_body_text_outside_a_substitution_is_still_data(self):
        self.assertFalse(F(self._hd("EOF", DEL_GAME))["rm_hits_game"])
        self.assertFalse(F(self._hd("EOF", "a > KNOWLEDGEBASE.md"))["durable_truncating_redirect"])

    def test_TWIN_arithmetic_in_an_unquoted_body_is_not_a_command(self):
        self.assertFalse(F(self._hd("EOF", "$((1 << 4)) " + DEL_GAME))["rm_hits_game"])


class TestCarriersNest(unittest.TestCase):
    def test_valid_quoted_carrier_depth_never_hides_unseen_commands(self):
        import shlex
        root = "C:/x4-guard-fixture/game"
        for depth in (1, 4, 5, 6):
            command = D + " -rf " + root
            for _ in range(depth):
                command = "bash -c " + shlex.quote(command)
            with self.subTest(depth=depth):
                result = F(command, roots={"game": root})
                self.assertEqual(result["carriers_truncated"], depth > H._MAX_CARRIER_DEPTH)
                if depth <= H._MAX_CARRIER_DEPTH:
                    self.assertTrue(result["rm_hits_game"])
        command = "echo harmless"
        for _ in range(5):
            command = "bash -c " + shlex.quote(command)
        self.assertTrue(F(command)["carriers_truncated"])

    def test_extra_seed_commands_obey_the_same_count_bound(self):
        out, truncated = H.carried_commands("echo body", ["echo extra%d" % i for i in range(251)])
        self.assertTrue(truncated)
        self.assertLessEqual(len(out), H._MAX_CARRIED)

    def test_a_shell_inside_a_shell(self):
        self.assertTrue(F("bash -c " + chr(39) + "sh -c " + chr(34) + D + " -rf "
                          + REF + chr(34) + chr(39))["rm_targets_reference"])

    def test_a_shell_inside_a_substitution(self):
        self.assertTrue(F("echo $(bash -c " + chr(39) + D + " -rf " + REF
                          + chr(39) + ")")["rm_targets_reference"])

    def test_the_walk_is_BOUNDED(self):
        """This runs on the blocking PreToolUse path; an unbounded walk over an
        adversarial string is a hang, and a hang here stops the session."""
        self.assertLessEqual(H._MAX_CARRIER_DEPTH, 8)
        deep = "echo ok"
        for _ in range(40):
            deep = "bash -c " + chr(39) + deep + chr(39)
        self.assertIsInstance(F(deep), dict)     # terminates

    def test_carried_commands_deduplicates(self):
        out, _trunc = H.carried_commands("echo $(ls) $(ls)", [])
        self.assertEqual(len(out), len(set(out)))

    def test_an_ordinary_command_is_never_truncated(self):
        """MEASURED over 13,503 real historical commands: the largest walk produced
        25 of the 250 allowed, p99 was 5 and p50 was 1. The cap must be unreachable
        by ordinary work or it becomes a prompt nobody reads."""
        for cmd in ("echo hello && ls -la",
                    "bash -c " + chr(39) + "sh -c " + chr(34) + "echo x" + chr(34) + chr(39),
                    "echo $(ls) $(pwd) $(date)"):
            with self.subTest(cmd=cmd):
                self.assertFalse(F(cmd)["carriers_truncated"], cmd)

    def test_a_TANGLED_command_says_so_instead_of_passing_quietly(self):
        """The walk is bounded because this runs on the blocking path -- MEASURED: a
        128 KB command with unique text at every nesting level produced 9,841 command
        strings and 4.1 s before the bound existed. But a bound that drops data and
        still returns a verdict is the narrowing-step defect, so it must ANNOUNCE."""
        def build(depth, n, tag="q"):
            if depth == 0:
                return "echo " + tag
            return " ".join("$(" + build(depth - 1, n, tag + chr(97 + i)) + ")"
                            for i in range(n))
        f = F("echo " + build(8, 3))
        self.assertTrue(f["carriers_truncated"],
                        "the walk was cut short and said nothing")

    def test_the_cap_is_reported_by_carried_commands_itself(self):
        deep = " ".join("$(echo a%d)" % i for i in range(400))
        _out, trunc = H.carried_commands("echo " + deep, [])
        self.assertTrue(trunc)


class TestAFilterThatFiltersNothing(unittest.TestCase):
    def test_a_universal_name_glob_is_not_a_narrowing(self):
        self.assertTrue(F("find " + chr(34) + GAME + chr(34) + " -name "
                          + chr(39) + "*" + chr(39) + " -delete")["rm_hits_game"])

    def test_a_REAL_filter_is_still_scoped(self):
        """The twin, and it is the whole reason the exemption exists: treating a
        genuinely-narrowed find as a tree delete added 40 prompts across 13,282
        commands, every one a __pycache__ cleanup."""
        self.assertFalse(F("find " + chr(34) + GAME + chr(34)
                           + " -name __pycache__ -delete")["rm_hits_game"])


class TestTheFactStreamCannotBeForged(unittest.TestCase):
    """protect-bash.sh splits the stream at the FIRST sentinel and `on()` matches
    "<NL>key<TAB>1<NL>" ANYWHERE before it, so a value carrying a newline can invent a
    fact no rule computed. `timeout` and `run_in_background` are passed through from
    the caller's payload, so they are the two values this file did not produce.

    E2E, before the fix: a benign `echo hi` with a forged `run_in_background` came back
    DENY. `on()` only ever tests for 1, so injection can ADD a fact and never remove
    one -- the reachable impact is a false refusal, not a bypass.
    """
    INJ = "x" + chr(10) + "rm_hits_game" + chr(9) + "1" + chr(10)

    def test_booleans_are_emitted_as_0_or_1(self):
        self.assertEqual(H._ipc_value("background", True), "1")
        self.assertEqual(H._ipc_value("background", False), "0")

    def test_a_timeout_is_always_a_number(self):
        self.assertEqual(H._ipc_value("timeout", 5), "5")
        self.assertEqual(H._ipc_value("timeout", "600000"), "600000")
        self.assertEqual(H._ipc_value("timeout", self.INJ), "0")

    def test_no_value_can_carry_a_field_separator(self):
        for key in ("timeout", "background", "anything"):
            with self.subTest(key=key):
                v = H._ipc_value(key, self.INJ)
                self.assertNotIn(chr(10), v)
                self.assertNotIn(chr(13), v)
                self.assertNotIn(chr(9), v)

    def test_a_real_value_still_survives(self):
        """The twin: a sanitiser that emptied everything would pass the test above."""
        self.assertEqual(H._ipc_value("something", "plain"), "plain")


class TestTheNameBackstopSurvivesARelativeOperand(unittest.TestCase):
    """prep() stores join_cwd(cwd, resolved) in element 0, and join_cwd returns "" when
    the operand is RELATIVE and the shell's directory is unknowable. That is correct for
    the PATH rules -- inventing a root there would fire on unrelated work -- but it
    silently disarmed the NAME backstop, whose whole job is an operand that bears the
    game's name WITHOUT being a resolvable path.

    MEASURED 2026-09-02: the backstop fired only when the delete followed a cd to an
    ABSOLUTE directory, i.e. only in the case it was least needed.
    """
    def test_a_bare_named_operand_fires(self):
        self.assertTrue(F(D + ' -rf "X4 Foundations"')["rm_hits_game"])

    def test_a_named_operand_after_a_RELATIVE_cd_fires(self):
        self.assertTrue(F('cd sub && ' + D + ' -rf "X4 Foundations"')["rm_hits_game"])

    def test_a_named_operand_after_an_ABSOLUTE_cd_still_fires(self):
        self.assertTrue(F('cd /somewhere && ' + D + ' -rf "X4 Foundations"')["rm_hits_game"])

    def test_an_ordinary_relative_delete_does_NOT_fire(self):
        """The twin, and the reason join_cwd returns "" in the first place: a backstop
        that fired on any relative delete would be a prompt on routine work."""
        for cmd in (D + " -rf build", "cd sub && " + D + " -rf dist",
                    D + " -rf __pycache__", "cd sub && " + D + " -rf .venv"):
            with self.subTest(cmd=cmd):
                self.assertFalse(F(cmd)["rm_hits_game"], cmd)


# --------------------------------------------------- round 3: the four bypass classes
# Each class below was a MEASURED silent ALLOW on 2026-09-02, against a delete the guard
# refuses when it is written plainly. Every class carries a twin that must stay ALLOWED:
# the failure this guard can least afford is a refusal on ordinary work -- 64 verified
# false denials were removed to earn the present precision and none may come back.
NLc = chr(10)
CONT = BS + NLc                    # a line continuation, built from parts


class TestALineContinuationIsNotASeparator(unittest.TestCase):
    """bash splices backslash-newline away before it parses anything; segments() split
    on it.

    So a delete continued onto a second line became TWO segments -- verb `rm` holding a
    lone backslash, and the path alone with no verb -- and every verb-keyed rule lost its
    operand at once, all three hard blocks included. MEASURED against 6 of 12 fuzz seeds;
    the other 6 have no whitespace to break at. This is how anyone writes a long command.
    """
    def test_the_game_root(self):
        self.assertTrue(F(D + " -rf " + CONT + '  "' + GAME + '"')["rm_hits_game"])

    def test_the_reference_tree(self):
        self.assertTrue(F(D + " -rf " + CONT + '  "' + REF + '"')["rm_targets_reference"])

    def test_split_between_the_verb_and_its_flag(self):
        self.assertTrue(F(D + " " + CONT + '  -rf "' + GAME + '"')["rm_hits_game"])

    def test_inside_SINGLE_quotes_it_stays_literal(self):
        """The twin. bash does NOT splice inside single quotes, so a continuation there
        is two literal characters of data, and removing it would corrupt text a rule
        then reads. An implementation that spliced unconditionally passes every test
        above and fails only this one."""
        payload = Q + "a" + CONT + "b" + Q
        self.assertIn(BS, H.join_continuations("echo " + payload))

    def test_an_ordinary_continued_command_is_untouched(self):
        self.assertFalse(F("echo one " + CONT + "  two && ls -la")["rm_hits_game"])


class TestAShellFedOnStdinIsACommandCarrier(unittest.TestCase):
    """Every way of handing a shell its program on stdin was invisible.

    `_inner_commands` modelled `-c`, `eval` and `trap`; a program arriving by pipe, by
    here-string or through /dev/stdin was not in the carrier set at all. Separately,
    `heredoc_bodies` asked whether the OPENER's verb is a shell -- but an opener is a
    PIPELINE, and where the first verb is `cat` the body was discarded as file payload
    while the shell on the far end of the pipe ran it unseen.
    """
    def test_echo_piped_into_bash(self):
        self.assertTrue(F("echo " + Q + DEL_GAME + Q + " | bash")["rm_hits_game"])

    def test_printf_piped_into_sh(self):
        cmd = "printf " + Q + "%s" + Q + " " + Q + DEL_GAME + Q + " | sh"
        self.assertTrue(F(cmd)["rm_hits_game"])

    def test_a_heredoc_piped_into_bash(self):
        cmd = "cat <<" + Q + "EOF" + Q + " | bash" + NLc + DEL_GAME + NLc + "EOF"
        self.assertTrue(F(cmd)["rm_hits_game"])

    def test_a_here_string(self):
        self.assertTrue(F("bash <<< " + Q + DEL_GAME + Q)["rm_hits_game"])

    def test_a_here_string_with_dash_s(self):
        self.assertTrue(F("bash -s <<< " + Q + DEL_GAME + Q)["rm_hits_game"])

    def test_source_dev_stdin(self):
        cmd = ("source /dev/stdin <<" + Q + "EOF" + Q + NLc + DEL_REF + NLc + "EOF")
        self.assertTrue(F(cmd)["rm_targets_reference"])

    # ---- twins: none of these hands a shell a program on stdin --------------------
    def test_a_shell_given_a_real_SCRIPT_does_not_pair_with_a_nearby_echo(self):
        """The pairing is deliberately narrow -- the consumer must have NO script
        operand. A rule that paired any echo with any later shell would refuse this
        shape, which appears throughout this repo's own tooling."""
        cmd = "echo " + Q + "note" + Q + " > n.md && bash script.sh"
        self.assertFalse(F(cmd)["rm_hits_game"])

    def test_a_heredoc_written_to_a_FILE_is_payload_not_a_program(self):
        cmd = ("cat > notes.md <<" + Q + "X" + Q + NLc + DEL_GAME + NLc + "X")
        self.assertFalse(F(cmd)["rm_hits_game"])

    def test_a_PYTHON_heredoc_is_not_routed_through_the_shell_parser(self):
        """A Python assignment whose first word is the delete verb. Routing non-shell
        heredoc bodies through the shell rules invents a delete nobody wrote -- which is
        why the OPENER's verb, not the presence of a body, decides."""
        cmd = ("python - <<" + Q + "PY" + Q + NLc + D + ' = "/tmp/x"' + NLc + "PY")
        self.assertFalse(F(cmd)["rm_hits_game"])


class TestAnUnresolvedExpansionIsNotALiteralPath(unittest.TestCase):
    """`_VAR` answered "what is this variable called" and was also asked "is there an
    expansion here". It matches a braced form only when the brace closes straight after
    the name, so a suffix strip, a default value, a pattern replace and an array index
    each matched NEITHER alternative and `has_unresolved` returned False.

    The DIRECTION is what makes this worse than a miss: the conservative branch exists so
    an operand the hook cannot resolve still refuses, and believing we HAD resolved it
    skips that branch -- the guard was most confident exactly where it knew least.
    """
    def test_suffix_strip(self):
        self.assertTrue(H.has_unresolved("${Z%ZZ}"))

    def test_default_value(self):
        self.assertTrue(H.has_unresolved("${NOPE:-" + GAME + "}"))

    def test_pattern_replace(self):
        self.assertTrue(H.has_unresolved("${Z//QQ/}"))

    def test_array_index(self):
        self.assertTrue(H.has_unresolved("${A[0]}"))

    def test_positional_and_special(self):
        for tok in ("$1", "$@", "$*", "$?", "$$"):
            with self.subTest(tok=tok):
                self.assertTrue(H.has_unresolved(tok), tok)

    def test_a_plain_literal_is_still_RESOLVED(self):
        """The twin: a predicate returning True unconditionally passes everything above
        while making every operand in the repo unresolvable -- refusals everywhere."""
        for tok in (GAME, "build", "./dist", "a-b_c.d", "100%"):
            with self.subTest(tok=tok):
                self.assertFalse(H.has_unresolved(tok), tok)

    def test_an_already_resolved_expansion_is_not_re_flagged(self):
        """resolve() substitutes what it can, and what comes back must not still look
        unresolved -- or an ordinary build-directory delete becomes a prompt."""
        self.assertFalse(F("DST=build; " + D + ' -rf "${DST%/}"')["rm_hits_game"])


class TestCoprocHidesACommand(unittest.TestCase):
    """The same shape as the compound-form bypass closed on 2026-09-01, still open
    because that fix enumerated the forms a fuzzer had produced instead of bash's own
    list of reserved words."""
    def test_coproc_does_not_hide_a_delete(self):
        self.assertTrue(F("coproc { " + DEL_GAME + "; }")["rm_hits_game"])

    def test_coproc_around_benign_work_is_still_allowed(self):
        self.assertFalse(F("coproc { echo hi; }")["rm_hits_game"])


class TestARedirectIsNotAnOperand(unittest.TestCase):
    """`_reads_stdin_program` refuses on any non-flag token, reading it as a script file.
    A REDIRECT is not a script file, and a here-string operator plus its word were what
    it tripped over -- so the single carrier whose payload is plainly visible in the
    command text was the one the check declined to look at."""
    def test_separated_and_attached_here_strings(self):
        for toks in (["<<<", "payload"], ["<<<payload"]):
            with self.subTest(toks=toks):
                self.assertEqual(H._drop_redirects(toks), [])

    def test_an_fd_prefixed_redirect(self):
        self.assertEqual(H._drop_redirects(["2>", "err", "-x"]), ["-x"])
        self.assertEqual(H._drop_redirects(["2>err", "-x"]), ["-x"])

    def test_a_real_operand_SURVIVES(self):
        """The twin: dropping too much makes a shell given a real script look like a
        stdin-fed one, and every suite this repo runs would start pairing with whatever
        echo precedes it."""
        self.assertEqual(H._drop_redirects(["script.sh"]), ["script.sh"])
        self.assertEqual(H._drop_redirects([">", "out", "script.sh"]), ["script.sh"])
        self.assertFalse(H._reads_stdin_program("bash script.sh"))
        self.assertFalse(H._reads_stdin_program("bash > out script.sh"))


# ------------------------------------------- round 3b: resolution, not just grammar
# Found by the twelve mutators added to fuzz-guard.py for the classes above -- 7 further
# bypasses, every one of them ALSO present on the committed tree, so these are holes the
# fuzzer previously could not EMIT rather than anything the round-3 fixes introduced.


class TestAnExpansionCarryingAnOperatorIsStillResolved(unittest.TestCase):
    """`resolve()` substituted with `_VAR`, which names only the two brace-free shapes,
    so every operator form stayed opaque and was matched against the roots as literal
    text. Resolving MORE can only turn an allow into a refusal on a command whose text
    really does name a protected root; it cannot invent one."""
    def test_suffix_strip(self):
        self.assertEqual(H.resolve("${V%QQ}", {"V": GAME + "QQ"}), GAME)

    def test_greedy_suffix_strip(self):
        self.assertEqual(H.resolve("${V%%QQ}", {"V": GAME + "QQ"}), GAME)

    def test_prefix_strip(self):
        self.assertEqual(H.resolve("${V#ZZ}", {"V": "ZZ" + GAME}), GAME)

    def test_default_when_unset(self):
        self.assertEqual(H.resolve("${NOPE:-" + GAME + "}", {}), GAME)

    def test_default_is_NOT_used_when_set(self):
        self.assertEqual(H.resolve("${V:-other}", {"V": GAME}), GAME)

    def test_pattern_replace(self):
        self.assertEqual(H.resolve("${V//QQ/}", {"V": GAME + "QQ"}), GAME)

    def test_array_index(self):
        a = H.assignments("A=(" + DQ + GAME + DQ + ")")
        self.assertEqual(H.resolve("${A[0]}", a), GAME)

    # ---- twins: what text alone CANNOT decide stays unresolved ------------------
    def test_a_GLOB_pattern_is_left_unresolved(self):
        """`${V%/*}` is the dirname idiom and needs pattern matching this hook does not
        do. Guessing here would produce a path that never appeared in the command, and a
        wrongly resolved operand is compared against the roots and CLEARED -- strictly
        worse than an unresolved one, which takes the conservative path."""
        out = H.resolve("${V%/*}", {"V": GAME + "/sub"})
        self.assertTrue(H.has_unresolved(out), out)

    def test_a_SUBSTRING_offset_is_left_unresolved(self):
        out = H.resolve("${V:2:5}", {"V": GAME})
        self.assertTrue(H.has_unresolved(out), out)

    def test_an_UNKNOWN_variable_with_no_default_is_left_unresolved(self):
        out = H.resolve("${NOPE%QQ}", {})
        self.assertTrue(H.has_unresolved(out), out)

    def test_a_suffix_that_does_not_match_leaves_the_value_alone(self):
        self.assertEqual(H.resolve("${V%ZZ}", {"V": GAME}), GAME)


class TestAnArrayKeepsItsQUOTINGWhenCaptured(unittest.TestCase):
    """`tokens()` strips quotes, so reading an array value from a token split the one
    element of a spaced Windows path into three and made element 0 `C:/Program`. The
    value is re-read from the raw segment for that reason."""
    def test_a_spaced_path_stays_ONE_element(self):
        a = H.assignments("A=(" + DQ + GAME + DQ + ")")
        self.assertEqual(H._array_elements(a["A"]), [GAME])

    def test_several_elements_still_split(self):
        a = H.assignments("A=(build dist out)")
        self.assertEqual(H._array_elements(a["A"]), ["build", "dist", "out"])

    def test_a_plain_assignment_is_not_an_array(self):
        a = H.assignments("A=" + DQ + GAME + DQ)
        self.assertEqual(H._array_elements(a["A"]), [])

    def test_an_index_past_the_end_is_unresolved(self):
        a = H.assignments("A=(build)")
        self.assertTrue(H.has_unresolved(H.resolve("${A[3]}", a)))


class TestCdThroughAVariableIsStillCd(unittest.TestCase):
    """`cwd_track` handed `_operands(seg)[0]` -- the RAW token -- to `join_cwd`, so a
    `cd` through any variable made the directory unknowable and EVERY later relative
    operand unattributable. MEASURED 2026-09-02: that walked a plain `$VAR` straight
    through the extensions hard block, which is the most ordinary idiom there is."""
    def test_a_plain_variable(self):
        cmd = "FZ=" + DQ + GAME + DQ + "; cd " + DQ + "$FZ" + DQ + " && " + D + " -rf extensions"
        self.assertTrue(F(cmd)["rm_hits_game"])

    def test_a_braced_variable(self):
        cmd = "FZ=" + DQ + GAME + DQ + "; cd " + DQ + "${FZ}" + DQ + " && " + D + " -rf extensions"
        self.assertTrue(F(cmd)["rm_hits_game"])

    def test_an_operator_bearing_expansion(self):
        cmd = ("FZ=" + DQ + GAME + "QQ" + DQ + "; cd " + DQ + "${FZ%QQ}" + DQ
               + " && " + D + " -rf extensions")
        self.assertTrue(F(cmd)["rm_hits_game"])

    def test_an_array_element(self):
        cmd = ("FZA=(" + DQ + GAME + DQ + "); cd " + DQ + "${FZA[0]}" + DQ
               + " && " + D + " -rf extensions")
        self.assertTrue(F(cmd)["rm_hits_game"])

    # ---- twins ------------------------------------------------------------------
    def test_an_UNKNOWABLE_cd_does_not_invent_a_root(self):
        """`cd "$NOPE"` names nothing this hook can see, and the command text contains
        no protected path. Refusing here would be a prompt on work that is not ours to
        judge -- the directory is UNKNOWN, which is a different answer from `the game`."""
        cmd = "cd " + DQ + "$NOPE" + DQ + " && " + D + " -rf extensions"
        self.assertFalse(F(cmd)["rm_hits_game"])

    def test_ordinary_relative_work_through_a_variable(self):
        cmd = "DIR=build; cd " + DQ + "$DIR" + DQ + " && " + D + " -rf dist"
        self.assertFalse(F(cmd)["rm_hits_game"])

    def test_an_array_of_ordinary_directories(self):
        cmd = "A=(build dist); cd " + DQ + "${A[0]}" + DQ + " && " + D + " -rf out"
        self.assertFalse(F(cmd)["rm_hits_game"])


# ----------------------------------------------- round 3c: spellings of a known action
# Each of these is a way of writing something the guard already refuses in its commonest
# form. None needed a new concept; all six were a set or a comparison that had been
# written against one example.


class TestFindRunsACommandFourWays(unittest.TestCase):
    """`-execdir`, `-ok` and `-okdir` delete exactly as `-exec` does -- find's own
    documentation recommends `-execdir` OVER `-exec` -- and the verb after them was
    compared as a bare token three functions below the `_verb_name()` that folds
    `/bin/rm` and `rm.exe` onto `rm`."""
    def test_exec_is_a_delete(self):
        self.assertTrue(F('find "' + GAME + '" -exec ' + D + ' -rf {} +')["rm_hits_game"])

    def test_execdir_is_a_delete(self):
        self.assertTrue(F('find "' + GAME + '" -execdir ' + D + ' -rf {} +')["rm_hits_game"])

    def test_okdir_is_a_delete(self):
        self.assertTrue(F('find "' + GAME + '" -okdir ' + D + ' -rf {} ;')["rm_hits_game"])

    def test_ok_is_a_delete(self):
        self.assertTrue(F('find "' + GAME + '" -ok ' + D + ' -rf {} ;')["rm_hits_game"])

    def test_an_absolute_delete_under_exec(self):
        self.assertTrue(F('find "' + GAME + '" -exec /bin/' + D + ' -rf {} +')["rm_hits_game"])

    def test_a_SHELL_under_exec_is_followed(self):
        cmd = ('find "' + GAME + '" -exec bash -c ' + Q + D + ' -rf $1' + Q + ' _ {} ;')
        self.assertTrue(F(cmd)["rm_hits_game"])

    # ---- twins ------------------------------------------------------------------
    def test_a_SCOPED_cleanup_is_still_allowed(self):
        """The rule that exempts a narrowed find exists because treating it like a tree
        delete added 40 prompts over 13,282 commands, every one a cache cleanup."""
        self.assertFalse(F('find . -name __pycache__ -exec ' + D + ' -rf {} +')["rm_hits_game"])

    def test_a_find_that_deletes_NOTHING_is_not_a_delete(self):
        self.assertFalse(F('find . -name ' + Q + '*.py' + Q)["rm_hits_game"])


class TestMovingARootAwayIsDestroyingIt(unittest.TestCase):
    """`copy_dests` only ever inspected a copy/move DESTINATION, so moving a protected
    root elsewhere was silent while deleting it was a hard block. The install is equally
    gone: the game stops working, every deployed mod goes with it, and Steam has to
    re-validate."""
    def test_moving_the_game_root_away(self):
        self.assertTrue(F('mv "' + GAME + '" /tmp/x')["rm_hits_game"])

    def test_moving_extensions_wholesale(self):
        self.assertTrue(F('mv "' + GAME + '/extensions" /tmp/x')["rm_hits_game"])

    def test_moving_the_reference_tree(self):
        self.assertTrue(F('mv "' + REF + '" /tmp/x')["rm_targets_reference"])

    def test_a_dash_t_move_makes_every_operand_a_source(self):
        self.assertTrue(F('mv -t /tmp/x "' + GAME + '"')["rm_hits_game"])

    # ---- twins ------------------------------------------------------------------
    def test_deploying_INTO_the_game_is_not_a_root_delete(self):
        """The deploy path is the whole reason this is scoped to the SOURCE. Writing
        into the game still asks -- that is a separate, designed confirmation -- but it
        must never reach the non-overridable hard block."""
        f = F('mv dev/mymod "' + GAME + '/extensions/mymod"')
        self.assertFalse(f["rm_hits_game"])
        # The DESTINATION must not be read as something being taken away. Asserting the
        # hard block alone was not enough to pin this: a mv whose destination sits
        # INSIDE extensions/ is not the root, so treating every operand as a source
        # tripped the workspace confirmation instead and no test noticed.
        self.assertFalse(f["rm_in_x4_dir"])
        self.assertTrue(f["copy_into_game_or_profile"])

    def test_an_ordinary_rename_is_silent(self):
        self.assertFalse(F("mv build/a.txt build/b.txt")["rm_hits_game"])

    def test_COPYING_from_a_root_is_not_a_delete(self):
        """`cp` reads and leaves the original in place, which is why only `mv` counts."""
        self.assertFalse(F('cp -r "' + GAME + '/extensions/x" /tmp/y')["rm_hits_game"])


class TestEverySpellingOfStagingEverything(unittest.TestCase):
    """The set was three exact tokens. `git add :/` stages from the repository root
    regardless of the current directory -- strictly broader than the `.` the rule was
    written for -- and it was the one form allowed. `git stage` is a real synonym."""
    def test_git_add_dot_slash(self):
        self.assertTrue(F("git add ./")["git_add_all"])

    def test_git_add_colon_slash(self):
        self.assertTrue(F("git add :/")["git_add_all"])

    def test_git_add_star(self):
        self.assertTrue(F("git add *")["git_add_all"])

    def test_git_stage_dash_A(self):
        self.assertTrue(F("git stage -A")["git_add_all"])

    def test_the_original_spelling_still_fires(self):
        for c in ("git add .", "git add -A", "git add --all"):
            with self.subTest(c=c):
                self.assertTrue(F(c)["git_add_all"], c)

    # ---- twins ------------------------------------------------------------------
    def test_explicit_paths_are_not_staging_everything(self):
        for c in ("git add src/a.py src/b.py", "git add -p", "git add .gitignore",
                  "git add tests/", "git stage src/a.py"):
            with self.subTest(c=c):
                self.assertFalse(F(c)["git_add_all"], c)


class TestACommandCarriedInAVariable(unittest.TestCase):
    """`_inner_commands` appended the token `$C` verbatim, although `assignments()` had
    already computed C from the same command and the delete rules were using it. The
    carrier walk was the one consumer not resolving."""
    def test_a_command_carried_in_a_variable(self):
        cmd = ("C=" + Q + D + ' -rf "' + GAME + '"' + Q + '; bash -c "$C"')
        self.assertTrue(F(cmd)["rm_hits_game"])

    def test_eval_of_a_variable(self):
        cmd = ("C=" + Q + D + ' -rf "' + GAME + '"' + Q + '; eval "$C"')
        self.assertTrue(F(cmd)["rm_hits_game"])

    def test_a_benign_carried_command_is_silent(self):
        cmd = "C=" + Q + "ls -la" + Q + '; bash -c "$C"'
        self.assertFalse(F(cmd)["rm_hits_game"])


class TestProseAboutARecordIsNotAWriteToIt(unittest.TestCase):
    """A FALSE POSITIVE, and the costlier direction: a non-overridable deny on ordinary
    work. The rule ANDed two independent predicates over the RAW command, so a comment
    naming a durable record plus an unrelated write call anywhere was enough. It fired on
    the reviewer twice, on me twice, and finally on the very command that fixed it."""
    def test_a_comment_naming_a_record_is_not_a_write(self):
        cmd = ("python -c " + DQ + "import io; io.open(" + Q + "x" + Q + "," + Q + "w"
               + Q + ")" + DQ + "  # per CLAUDE.md")
        self.assertFalse(F(cmd)["durable_python_open_w"])

    def test_a_heredoc_body_naming_a_record_is_not_a_write(self):
        cmd = ("cat > s.py <<" + Q + "PY" + Q + NLc + "# see CLAUDE.md" + NLc
               + "open(" + Q + "x" + Q + ", " + Q + "w" + Q + ")" + NLc + "PY")
        self.assertFalse(F(cmd)["durable_python_open_w"])

    def test_prose_naming_a_record_is_not_a_write(self):
        cmd = ("echo " + DQ + "KNOWLEDGEBASE.md says never use open(p, " + Q + "w" + Q
               + ")" + DQ)
        self.assertFalse(F(cmd)["durable_python_open_w"])

    # ---- the twin that matters most: the rule must still DO its job -------------
    def test_a_real_python_write_to_a_durable_record_still_fires(self):
        cmd = ("python -c " + DQ + "open(" + Q + "CLAUDE.md" + Q + ", " + Q + "w" + Q
               + ").write(x)" + DQ)
        self.assertTrue(F(cmd)["durable_python_open_w"])

    def test_the_variable_case_this_rule_EXISTS_for_still_fires(self):
        """The two-predicate AND is kept precisely for this: the path is in a variable,
        so a regex demanding the filename inside the call would miss it."""
        cmd = ("python -c " + DQ + "p=" + Q + "KNOWLEDGEBASE.md" + Q + "; open(p, " + Q
               + "w" + Q + ")" + DQ)
        self.assertTrue(F(cmd)["durable_python_open_w"])


class WritesReference(unittest.TestCase):
    """reference/ is read-only base game data. The DELETE case has been blocked for
    months; the WRITE case had no Bash rule at all, while protect-files.sh hard-blocks
    the identical write through the Edit/Write channel. Two channels, one tree,
    opposite verdicts -- and the Bash side was the permissive one."""

    def test_a_SUBSTITUTED_command_name_at_a_root_fires(self):
        """An unknown OPERAND still reaches the conservative branch; an unknown
        VERB reached NO rule, so this walked past all three hard blocks.
        MEASURED: `$(echo rm) -rf <game>` was ALLOW where `rm -rf <game>`
        denied."""
        self.assertTrue(F('$(echo rm) -rf "' + GAME + '"')["verb_unresolved"])

    def test_the_BACKTICK_spelling_of_a_substituted_verb_fires(self):
        self.assertTrue(F('`echo rm` -rf "' + GAME + '"')["verb_unresolved"])

    def test_a_substituted_ARGUMENT_is_not_a_substituted_verb(self):
        """The must-NOT-fire half, and the reason this is keyed on the VERB
        position rather than on a substitution appearing anywhere: over 28,989
        real commands, keying on any unresolved verb matched 679 (mostly $JQ /
        $UV, where the variable holds a PATH); this rule matches 4."""
        self.assertFalse(F('ls -la $(pwd)')["verb_unresolved"])
        self.assertFalse(F('echo $(date)')["verb_unresolved"])

    def test_a_substituted_verb_EMBEDDED_in_a_path_fires(self):
        """The rule tested `startswith("$(")`, so a substitution embedded in the
        command NAME escaped it. MEASURED 2026-09-09 by the seed this rule shipped
        WITHOUT: `/usr/bin/$(which rm) -rf <game>` was DENY -> ALLOW, found by the
        fuzzer's own absolute-path mutator within seconds of the seed existing.
        A substitution anywhere in the name makes the command exactly as unknowable
        as one that opens it, which is what `_SUBST` -- the module's own predicate,
        already behind has_unresolved -- has always said."""
        self.assertTrue(F('/usr/bin/$(which rm) -rf "' + GAME + '"')["verb_unresolved"])
        self.assertTrue(F('./$(echo rm) -rf "' + GAME + '"')["verb_unresolved"])
        self.assertTrue(F('/usr/bin/`which rm` -rf "' + GAME + '"')["verb_unresolved"])

    def test_an_embedded_substitution_still_needs_a_rooted_operand(self):
        """The must-NOT-fire twin for the widening above: only the VERB test moved,
        and the conjunct that priced this rule at 4 hits in 28,989 is untouched."""
        self.assertFalse(F('/usr/bin/$(which rm) -rf /tmp/scratch')["verb_unresolved"])
        self.assertFalse(F('/usr/bin/env ls -la "' + GAME + '"')["verb_unresolved"])

    def test_a_substituted_verb_AWAY_from_every_root_does_not_fire(self):
        """Scoped to the segment's own rooted operands, never to a root appearing
        anywhere in the command."""
        self.assertFalse(F('$(echo rm) -rf /tmp/scratch')["verb_unresolved"])

    def test_a_truncating_redirect_into_reference_fires(self):
        self.assertTrue(F('echo x > "' + REF + '/libraries/wares.xml"')
                        ["writes_reference"])

    def test_a_copy_into_reference_fires(self):
        self.assertTrue(F('cp a.xml "' + REF + '/libraries/wares.xml"')
                        ["writes_reference"])

    def test_sed_in_place_on_a_reference_file_fires(self):
        self.assertTrue(F('sed -i s/a/b/ "' + REF + '/libraries/wares.xml"')
                        ["writes_reference"])

    # ---- the other direction: it must stay silent on everything else -------------
    def test_READING_under_reference_is_not_a_write(self):
        self.assertFalse(F('cat "' + REF + '/libraries/wares.xml"')
                         ["writes_reference"])

    def test_a_write_somewhere_else_does_not_fire(self):
        self.assertFalse(F('echo x > "' + TOOLKIT + '/notes.txt"')
                         ["writes_reference"])

    def test_merely_NAMING_reference_beside_another_write_does_not_fire(self):
        """The G1 shape. Before the operand fix this whole family denied."""
        self.assertFalse(F('ls "' + REF + '" && echo x > "$TMP/out.txt"')
                         ["writes_reference"])


class DashOIsNotAnOutputFlagForASearchVerb(unittest.TestCase):
    """`grep -o` is --only-matching and takes no argument, so the next token is the
    PATTERN, not a path.

    output_targets() treated `-o` as a path for every verb. Latent and harmless while
    only the shared-temp rule read the result; promoting it into the reference-write
    HARD BLOCK is what made it visible. MEASURED over 13,503 historical commands: 20
    of the 21 rows that rule gained were `cd <reference> && grep -o ...` -- read-only
    research that would have become a non-overridable DENY. No unit test and no E2E
    probe saw it; only the per-item corpus diff did.
    """

    def test_grep_dash_o_yields_no_output_target(self):
        self.assertEqual(H.output_targets("grep -o 'pat' f.xml"), [])

    def test_rg_dash_o_yields_no_output_target(self):
        self.assertEqual(H.output_targets("rg -o 'pat' f.xml"), [])

    def test_a_search_after_cd_into_reference_is_not_a_write(self):
        self.assertFalse(F('cd "' + REF + '" && grep -o ' + Q + 'pat' + Q + ' md/x.xml')
                         ["writes_reference"])

    # ---- the flag is still an output flag everywhere it really is one ------------
    def test_curl_dash_o_is_still_an_output_target(self):
        self.assertEqual(H.output_targets("curl -o out.bin http://x"), ["out.bin"])

    def test_sort_dash_o_is_still_an_output_target(self):
        self.assertEqual(H.output_targets("sort -o sorted.txt in.txt"), ["sorted.txt"])

    def test_curl_dash_o_into_reference_still_fires(self):
        """The twin that stops the fix from being a deletion: a real output flag
        writing into the read-only tree must still be blocked."""
        self.assertTrue(F('cd "' + REF + '" && curl -o out.bin http://x')
                        ["writes_reference"])

    def test_long_form_output_is_unaffected_for_a_search_verb(self):
        self.assertEqual(H.output_targets("grep --output=o.txt pat f"), ["o.txt"])

    def test_a_grep_inside_a_SUBSTITUTION_is_still_recognised(self):
        """tokens() keeps `loc=$(grep` as ONE token, so verb() returns the assignment
        prefix and the command name is lost. This is what left 3 rows still misfiring
        after the first version of the fix."""
        seg = "loc=$(grep -o " + Q + "pat" + Q + " f.xml | head -1)"
        self.assertIn("grep", H._command_names(seg))
        self.assertEqual(H.output_targets(seg), [])


class AnArrowIsNotARedirect(unittest.TestCase):
    """`->` cannot be a redirect: `-` is neither an fd nor an operator prefix.

    Printing one is routine, and `print(f'{n} values ->', vals)` inside a `python -c`
    payload was read as a truncating redirect whose target was the rest of the python
    source -- which, after a `cd` into the read-only reference tree, became a WRITE to
    it. One row in 13,503 historical commands, and the last false positive standing.
    """

    def test_an_arrow_yields_no_redirect(self):
        self.assertEqual(H.redirects("echo 'a -> b'"), [])

    def test_an_arrow_in_a_python_payload_is_not_a_write(self):
        cmd = ('cd "' + REF + '" && python -c ' + DQ + "print(f'v ->', z)" + DQ)
        self.assertFalse(F(cmd)["writes_reference"])

    # ---- every real redirect shape must survive ---------------------------------
    def test_a_plain_truncating_redirect_still_parses(self):
        self.assertEqual(H.redirects("echo x > out.txt"), [("truncate", "out.txt")])

    def test_an_append_still_parses(self):
        self.assertEqual(H.redirects("echo x >> out.txt"), [("append", "out.txt")])

    def test_a_noclobber_override_still_parses(self):
        self.assertEqual(H.redirects("echo x >| out.txt"), [("truncate", "out.txt")])

    def test_a_redirect_with_no_space_still_parses(self):
        self.assertEqual(H.redirects("echo x >out.txt"), [("truncate", "out.txt")])

    def test_a_redirect_after_a_FLAG_still_parses(self):
        """The exclusion is the two-character sequence `-` then `>`, not "a flag
        appeared somewhere". `ls -l >out` has a space and `ls -l>out` ends in `l`."""
        self.assertEqual(H.redirects("ls -l >out.txt"), [("truncate", "out.txt")])
        self.assertEqual(H.redirects("ls -l>out.txt"), [("truncate", "out.txt")])

    def test_a_real_redirect_into_reference_still_fires(self):
        self.assertTrue(F('echo x > "' + REF + '/md/x.xml"')["writes_reference"])


class SedScriptIsNotATarget(unittest.TestCase):
    """`sed -i -e 's|<root>/a|$V/a|g' f` edits `f`, not <root>.

    The helper returned the SCRIPT as a target, documented as costing nothing because
    "a script never resolves under a root". A script that REWRITES a path contains that
    path, so it resolves under one exactly when it matters. Free until something
    hard-blocked on it.
    """

    def test_the_script_after_dash_e_is_not_a_file(self):
        self.assertEqual(H.sed_in_place_targets("sed -i -e 's|a|b|g' file.txt"),
                         ["file.txt"])

    def test_the_bare_first_operand_is_still_treated_as_the_script(self):
        self.assertEqual(H.sed_in_place_targets("sed -i 's|a|b|g' file.txt"),
                         ["file.txt"])

    def test_several_files_after_a_bare_script(self):
        self.assertEqual(H.sed_in_place_targets("sed -i.bak 's|a|b|' f1 f2"),
                         ["f1", "f2"])

    def test_sed_without_in_place_edits_nothing(self):
        self.assertEqual(H.sed_in_place_targets("sed 's|a|b|' f"), [])

    def test_a_script_MENTIONING_reference_is_not_a_write_to_it(self):
        cmd = ("sed -i -e " + Q + "s|" + REF + "/x|$V/x|g" + Q + ' "$f"')
        self.assertFalse(F(cmd)["writes_reference"])

    # ---- and the twin: a real in-place edit of the tree must still fire ----------
    def test_sed_dash_i_dash_e_INTO_reference_still_fires(self):
        self.assertTrue(F('sed -i -e s/a/b/ "' + REF + '/md/x.xml"')
                        ["writes_reference"])


class UnresolvedOperandIsScopedToTheOperand(unittest.TestCase):
    """hit()'s unresolved branch compared the root against the WHOLE raw command.

    So any command that so much as mentioned a protected root, in a comment or in an
    unrelated argument, made every unresolvable operand elsewhere in it look like that
    root. Five rules share the branch and two of them hard-deny, so this was a
    non-overridable refusal on ordinary work.
    """

    def test_naming_reference_elsewhere_does_not_make_an_rm_target_it(self):
        self.assertFalse(F('ls "' + REF + '" && rm -rf "$TMPDIR/build"')
                         ["rm_targets_reference"])

    def test_a_comment_naming_reference_does_not_arm_an_unrelated_rm(self):
        self.assertFalse(F('rm -rf "$WORK/tmp"  # never touch ' + REF)
                         ["rm_targets_reference"])

    # ---- the case the branch EXISTS for. If this goes green the fix was a deletion.
    def test_an_unresolvable_operand_UNDER_reference_still_fires(self):
        self.assertTrue(F('rm -rf "' + REF + '/$SUB"')["rm_targets_reference"])

    def test_the_root_VARIABLE_spelling_still_fires(self):
        """`rm -rf "$X4_REFERENCE/x"` never contains the path as text; the variable
        NAME is the only evidence there is, and it must survive the scoping change."""
        self.assertTrue(F('rm -rf "$X4_REFERENCE/x"')["rm_targets_reference"])


class DollarQIsAboutExpansionNotText(unittest.TestCase):
    """`$?` inside a comment or a single-quoted string is not a read of `$?`.

    The rule read the RAW command, so a comment WARNING about the trap was itself a
    DENY. The fix is deliberately not blank_quoted(): inside DOUBLE quotes `$?` still
    expands, so blanking both kinds would have turned a live rule off.
    """

    def test_a_comment_mentioning_the_trap_is_not_a_hit(self):
        self.assertFalse(F('ls -1 | wc -l ; true  # never read $? after a pipeline')
                         ["dollarq_after_pipe"])

    def test_single_quoted_prose_is_not_a_hit(self):
        cmd = "printf " + Q + "%s" + Q + " " + Q + "the trap is $? after a pipe" + Q               + " | cat"
        self.assertFalse(F(cmd)["dollarq_after_pipe"])

    # ---- and the three that must still fire ------------------------------------
    def test_a_real_dollar_q_after_a_pipeline_still_fires(self):
        self.assertTrue(F('grep -c foo bar | head -1; echo $?')["dollarq_after_pipe"])

    def test_dollar_q_inside_DOUBLE_quotes_still_fires(self):
        """The reason blank_quoted() is the wrong tool here: this one is real."""
        self.assertTrue(F('grep -c foo bar | head -1; echo "rc=$?"')
                        ["dollarq_after_pipe"])

    def test_PIPESTATUS_in_double_quotes_still_disables_the_rule(self):
        """The escape hatch the message recommends is normally written inside double
        quotes. Blanking quotes before looking for it would fire on the very idiom the
        rule tells you to use."""
        self.assertFalse(F('grep -c foo bar | head -1; echo "${PIPESTATUS[0]}"')
                         ["dollarq_after_pipe"])


class DurablePythonIsPerSegment(unittest.TestCase):
    """The rule ANDed two WHOLE-BODY predicates with "some segment is python", so
    prose in one segment plus an unrelated write in another fired it."""

    def test_prose_in_one_segment_and_a_write_in_another_does_not_fire(self):
        cmd = ("echo " + Q + "appending to BLIND-SPOTS.md" + Q + " && python -c " + DQ
               + "open(" + Q + "/tmp/o.txt" + Q + ", " + Q + "w" + Q + ")" + DQ)
        self.assertFalse(F(cmd)["durable_python_open_w"])

    def test_the_record_named_in_a_shell_variable_in_an_EARLIER_segment_still_fires(self):
        """Per-segment evaluation must not lose the case the rule mainly exists for.
        Each segment is checked resolved as well as as written, which the whole-body
        form got for free."""
        cmd = ("P=KNOWLEDGEBASE.md; python -c " + DQ + "open(" + Q + "$P" + Q + ", "
               + Q + "w" + Q + ")" + DQ)
        self.assertTrue(F(cmd)["durable_python_open_w"])


class AVerbCarriedInAVariable(unittest.TestCase):
    """verb() was the one consumer that never called resolve(), so one indirection
    walked past every verb-keyed rule at once -- all three hard blocks included."""

    def test_rm_through_a_variable_still_hits_the_game_root(self):
        self.assertTrue(F('RM=rm; $RM -rf "' + GAME + '"')["rm_hits_game"])

    def test_rm_through_a_variable_still_hits_the_extensions_folder(self):
        self.assertTrue(F('RM=rm; $RM -rf "' + GAME + '/extensions"')["rm_hits_game"])

    def test_the_braced_spelling_resolves_too(self):
        self.assertTrue(F('RM=rm; ${RM} -rf "' + GAME + '"')["rm_hits_game"])

    # ---- it must not invent a verb ---------------------------------------------
    def test_an_unassigned_variable_verb_is_left_alone(self):
        """Nothing to resolve to, so the segment is untouched and no rule fires on a
        guess."""
        self.assertFalse(F('$UNSET_CMD -rf "' + GAME + '"')["rm_targets_reference"])

    def test_a_variable_holding_a_NON_command_is_not_spliced_in(self):
        """Only a bare command NAME is substituted. A value with a space in it is a
        path or a message that happens to be assigned, not a verb, and rewriting the
        segment with it would invent a command the user never typed."""
        self.assertEqual(H.resolve_verb('$P -rf x', {"P": "some path/with space"}),
                         '$P -rf x')

    def test_a_resolvable_verb_is_spliced_in(self):
        self.assertEqual(H.resolve_verb('$RM -rf x', {"RM": "rm"}), 'rm -rf x')


class BlankSingleQuoted(unittest.TestCase):
    """The helper the $? fix turns on. blank_quoted() erases both quote kinds, which
    is right for flag detection and wrong for expansion detection."""

    def test_single_quoted_content_is_blanked(self):
        self.assertEqual(H.blank_single_quoted("a " + Q + "bc" + Q + " d"),
                         "a " + Q + "  " + Q + " d")

    def test_double_quoted_content_is_KEPT(self):
        self.assertEqual(H.blank_single_quoted('a "bc" d'), 'a "bc" d')

    def test_length_is_preserved(self):
        s = "echo " + Q + "hello world" + Q + " | cat"
        self.assertEqual(len(H.blank_single_quoted(s)), len(s))

class DoubledSeparatorIsOneSeparator(unittest.TestCase):
    """`<root>//reference/x` names the same file as `<root>/reference/x`.

    POSIX and Windows both collapse an interior run of separators, so the doubled form
    is the ordinary string-concatenation artefact -- `"$DIR/" + "/reference/..."` --
    not something anyone types. It compared equal to nothing and walked past the
    reference/ HARD BLOCK. MEASURED 2026-09-03, E2E, in BOTH channels: the write was
    ALLOW while the single-slash spelling denied.

    norm() already called posixpath.normpath, which would have collapsed it -- but
    only when a DOT segment was present, so the `//` case never reached the one
    function that would have fixed it.
    """

    def test_an_interior_run_collapses(self):
        self.assertEqual(H.norm("C:/a//b///c"), "/c/a/b/c")

    def test_a_doubled_separator_is_still_under_the_root(self):
        self.assertTrue(H.under(REF[:REF.rindex("/")] + "//reference/x", REF))

    def test_a_write_through_a_doubled_separator_still_fires(self):
        parent = REF[:REF.rindex("/")]
        self.assertTrue(F('cp m.xml "' + parent + '//reference/libraries/w.xml"')
                        ["writes_reference"])

    def test_a_delete_through_a_doubled_separator_still_fires(self):
        parent = REF[:REF.rindex("/")]
        self.assertTrue(F('rm -rf "' + parent + '//reference"')
                        ["rm_targets_reference"])

    # ---- and the twin: a LEADING // is a UNC share, a DIFFERENT location ---------
    def test_a_leading_double_slash_is_PRESERVED(self):
        """Collapsing it would retarget a path rather than normalise it. The
        extended-length prefix rewrite emits `//` deliberately for the same reason."""
        self.assertEqual(H.norm("//server/share/x"), "//server/share/x")

    def test_an_unrelated_path_is_unaffected(self):
        self.assertFalse(F('cp m.xml "C:/somewhere/else//file.xml"')
                         ["writes_reference"])


class TestDestructiveGitInAnX4Directory(unittest.TestCase):
    r"""git IGNORES the read-only attribute, so x4lock cannot cover these.

    MEASURED 2026-09-04 on a real repository: `git checkout HEAD~1 -- <locked file>`
    overwrote the file AND left it unlocked afterwards, and `git clean -fdx` deleted a
    locked untracked file. The lock stops 11 of 14 write primitives on Windows, 9 of
    14 on POSIX; it stops none of
    these, which makes the hook the only layer that can see them.

    The must-NOT-fire half is the larger half on purpose. `git checkout <branch>`,
    `-b`, `reset --soft` and `clean --dry-run` are ordinary work, and a guard that
    fires on ordinary work gets switched off -- which protects nothing at all.
    """

    MODS = ROOTS["mods"]

    def _fires(self, cmd):
        f = F(cmd)
        return f["git_wipes_x4_dir"] or f["git_discards_x4_files"]

    # --- must FIRE ---------------------------------------------------------------
    def test_clean_force_in_a_mod_root(self):
        self.assertTrue(self._fires('cd "' + self.MODS + '" && git clean -fdx'))

    def test_clean_long_force(self):
        self.assertTrue(self._fires('cd "' + self.MODS + '" && git clean --force'))

    def test_reset_hard(self):
        self.assertTrue(self._fires('cd "' + self.MODS + '" && git reset --hard'))

    # `-c` consumes the NEXT token. Treating it as a bare flag made its VALUE the
    # "subcommand", so `clean`/`reset` were never seen and one config option
    # silenced the rule entirely (MEASURED 2026-09-04: ALLOW vs ask).
    def test_config_option_does_not_hide_clean(self):
        self.assertTrue(self._fires(
            'cd "' + self.MODS + '" && git -c core.fileMode=false clean -fdx'))

    # B3, MEASURED 2026-09-06 against the guard AS SHIPPED: a leading `VAR=value`
    # assignment took all three destructive forms from ask to ALLOW. POSIX allows any
    # number of them before the command name, and `verb()` already skipped them -- but
    # the subcommand scan started at `toks[1:]`, assuming the verb sits at index 0, so
    # with a prefix `sub` became the token "git" itself and matched nothing.
    #
    # This one matters beyond the bypass: this rule's own docstring records that git
    # IGNORES the read-only attribute, so x4lock stops none of these and the hook is
    # the only layer that can see them.
    def test_an_assignment_prefix_does_not_hide_clean(self):
        self.assertTrue(self._fires(
            'cd "' + self.MODS + '" && FOO=bar git clean -fdx'))

    def test_an_assignment_prefix_does_not_hide_reset_hard(self):
        self.assertTrue(self._fires(
            'cd "' + self.MODS + '" && FOO=bar git reset --hard'))

    def test_an_assignment_prefix_does_not_hide_a_dash_C_target(self):
        self.assertTrue(self._fires('FOO=bar git -C "' + self.MODS + '" clean -fdx'))

    def test_several_assignments_are_skipped_not_just_one(self):
        """POSIX permits any number; a fix that skipped exactly one would pass the
        three above and still be wrong."""
        self.assertTrue(self._fires(
            'cd "' + self.MODS + '" && A=1 B=2 C=3 git clean -fdx'))

    def test_the_subcommand_name_as_an_assignment_VALUE_does_not_shift_the_operands(self):
        """`rest` was sliced with `toks.index(sub)`, which finds the FIRST occurrence of
        that STRING -- so an assignment whose value happens to equal the subcommand
        would have shifted every following operand by one."""
        self.assertTrue(self._fires(
            'cd "' + self.MODS + '" && X=clean git clean -fdx'))

    def test_config_option_does_not_hide_reset_hard(self):
        self.assertTrue(self._fires(
            'cd "' + self.MODS + '" && git -c a.b=c reset --hard'))

    def test_config_option_on_a_HARMLESS_subcommand_still_does_not_fire(self):
        self.assertFalse(self._fires(
            'cd "' + self.MODS + '" && git -c core.fileMode=false status'))

    def test_checkout_pathspec_discards_file_contents(self):
        self.assertTrue(self._fires('cd "' + self.MODS + '" && git checkout -- .'))

    def test_restore_is_always_about_files(self):
        self.assertTrue(self._fires('cd "' + self.MODS + '" && git restore a.yaml'))

    def test_dash_C_names_the_directory_without_a_cd(self):
        self.assertTrue(self._fires('git -C "' + self.MODS + '" clean -fdx'))

    def test_inside_the_game_installation(self):
        self.assertTrue(self._fires('cd "' + GAME + '" && git reset --hard'))


    # --- the split is the point: which side does each shape land on? -------------
    def test_unbounded_forms_ASK_because_they_reach_untracked_files(self):
        """clean/reset --hard name no paths, so they also delete files with no
        history and no other copy. That is the user's decision, per the hook policy."""
        for c in ("git clean -fdx", "git reset --hard"):
            with self.subTest(cmd=c):
                f = F('cd "' + self.MODS + '" && ' + c)
                self.assertTrue(f["git_wipes_x4_dir"], c + " should ASK")
                self.assertFalse(f["git_discards_x4_files"])

    def test_targeted_forms_only_ADVISE_because_the_content_is_recoverable(self):
        """Every path `checkout --`/`restore` can name is tracked, so the content is
        in the object store. Interrupting the USER for that spends their attention on
        my command hygiene -- MEASURED: all 4 corpus rows this rule touches are
        exactly this shape (restoring a source file after a mutation run)."""
        for c in ("git checkout -- .", "git restore a.yaml"):
            with self.subTest(cmd=c):
                f = F('cd "' + self.MODS + '" && ' + c)
                self.assertTrue(f["git_discards_x4_files"], c + " should ADVISE")
                self.assertFalse(f["git_wipes_x4_dir"], c + " must not reach the ask")

    # --- must NOT fire: ordinary git ---------------------------------------------
    def test_a_dry_run_removes_nothing(self):
        self.assertFalse(self._fires('cd "' + self.MODS + '" && git clean -nd'))
        self.assertFalse(self._fires('cd "' + self.MODS + '" && git clean --dry-run -d'))

    def test_checking_out_a_BRANCH_is_navigation(self):
        self.assertFalse(self._fires('cd "' + self.MODS + '" && git checkout main'))
        self.assertFalse(self._fires('cd "' + self.MODS + '" && git checkout -b feature'))

    def test_reset_without_hard_leaves_the_working_tree(self):
        self.assertFalse(self._fires('cd "' + self.MODS + '" && git reset'))
        self.assertFalse(self._fires('cd "' + self.MODS + '" && git reset --soft HEAD~1'))

    def test_read_only_git_is_untouched(self):
        for c in ("git status", "git log --oneline", "git diff HEAD", "git add a.py"):
            with self.subTest(cmd=c):
                self.assertFalse(self._fires('cd "' + self.MODS + '" && ' + c))

    # --- must NOT fire: destructive, but not in an X4 directory -------------------
    def test_an_unknowable_cwd_reaches_no_rule(self):
        """`join_cwd` returns "" when the directory is unknown. Inventing a root there
        would fire on every unrelated repository on the machine."""
        self.assertFalse(self._fires("git clean -fdx"))
        self.assertFalse(self._fires("git checkout -- ."))

    def test_somewhere_else_entirely(self):
        self.assertFalse(self._fires('cd "C:/Users/x/some-other-project" && git clean -fdx'))



class TestAGlobOperandCannotWalkPastAHardBlock(unittest.TestCase):
    r"""One glob character defeated every root rule.

    MEASURED 2026-09-04: `rm -rf "<game>"*` was ALLOW where the identical literal path
    is DENY, and so were `[X]4 Foundations`, `X4?Foundations` and `X4 Foundation{s,}`.
    All of them really do delete the installation.

    `scripts/fuzz-guard.py` could not have found this. Its own docstring says it
    mutates "the SYNTAX AROUND the dangerous operation... the dangerous operand is
    byte-identical in every mutant" -- so the operand is the single axis 996 mutants
    hold fixed by construction. An instrument's stated invariant is its blind spot.

    This is NOT the F93 unresolvable-operand case: `$DST` cannot be proven to be the
    root and must not reach a non-overridable deny, whereas a glob is fully present in
    the text and decidable. The must-NOT-fire half below pins that distinction.
    """

    def _deny(self, cmd):
        return F(cmd)["rm_hits_game"]

    def test_the_literal_form_still_denies(self):
        self.assertTrue(self._deny(D + ' -rf "' + GAME + '"'))

    def test_a_trailing_glob_is_still_the_installation(self):
        self.assertTrue(self._deny(D + ' -rf "' + GAME + '"*'))

    def test_deleting_the_contents_is_the_same_loss(self):
        self.assertTrue(self._deny(D + ' -rf "' + GAME + '"/*'))

    def test_a_character_class_spelling_the_root(self):
        alt = GAME.replace("/X4 Foundations", "/[X]4 Foundations")
        self.assertTrue(self._deny(D + ' -rf "' + alt + '"'))

    def test_a_question_mark_matching_the_space(self):
        alt = GAME.replace("X4 Foundations", "X4?Foundations")
        self.assertTrue(self._deny(D + ' -rf "' + alt + '"'))

    def test_brace_expansion(self):
        alt = GAME[:-1]                      # "...X4 Foundation"
        self.assertTrue(self._deny(D + ' -rf "' + alt + '"{s,}'))

    def test_extensions_wholesale_via_a_glob(self):
        self.assertTrue(self._deny(D + ' -rf "' + GAME + '/extensions"*'))

    def test_glob_suffix_on_reference_is_still_the_reference(self):
        """The same bypass on the OTHER hard block, which goes through `hit()` rather
        than `hits_game_root` -- two code paths, one defect."""
        self.assertTrue(F(D + ' -rf "' + REF + '"*')["rm_targets_reference"])
        self.assertTrue(F(D + ' -rf "' + REF + '"/*')["rm_targets_reference"])
        self.assertFalse(F(D + ' -rf "C:/Users/x/other"*')["rm_targets_reference"])

    # --- must NOT fire ------------------------------------------------------------
    def test_an_ordinary_glob_elsewhere_is_untouched(self):
        for c in (D + ' -rf "C:/Users/x/project/build"*',
                  D + " -rf build/*",
                  D + ' -rf "C:/Users/x/tmp"/*'):
            with self.subTest(cmd=c):
                self.assertFalse(self._deny(c))

    def test_a_glob_INSIDE_the_tree_is_not_the_root(self):
        """Deleting one mod's files is ordinary work; it must reach the confirmation,
        not the hard block."""
        f = F(D + ' -rf "' + GAME + '/extensions/mymod"/*')
        self.assertFalse(f["rm_hits_game"])
        self.assertTrue(f["rm_in_x4_dir"], "it should still CONFIRM")

    def test_an_unresolvable_operand_still_does_NOT_hard_block(self):
        """F93: `$DST` cannot be proven to be the root, so a deny the user cannot
        override is wrong there. A glob is decidable; a variable is not."""
        self.assertFalse(self._deny(D + ' -rf "$DST"'))


class TestBraceExpansionIsBounded(unittest.TestCase):
    """A guard that can be made to hang is a guard that gets removed."""

    def test_a_combinatorial_pattern_terminates_and_stays_bounded(self):
        out = H._brace_expand("a" + "{x,y}" * 12 + "b")
        self.assertLessEqual(len(out), 64)

    def test_an_unbalanced_brace_is_returned_untouched(self):
        self.assertEqual(H._brace_expand("a{b,c"), ["a{b,c"])

    def test_overflow_is_reported_instead_of_certifying_a_partial_operand_set(self):
        base = "C:/x4-guard-fixture"
        roots = {"game": base + "/game", "reference": base + "/reference"}
        for count in (64, 65):
            values = [base + "/junk%d" % i for i in range(count - 1)] + [roots["game"]]
            commands = [
                D + " -rf " + base + "/{" + ",".join(["junk%d" % i for i in range(count - 1)] + ["game"]) + "}",
                "for f in " + " ".join(values) + '; do ' + D + ' -rf "$f/extensions"; done',
                "A=(" + " ".join(values) + '); ' + D + ' -rf "$A/extensions"',
                "A=(" + " ".join(values) + '); Z="$A"; ' + D + ' -rf "$Z"',
            ]
            for command in commands:
                with self.subTest(count=count, command=command):
                    result = F(command, roots=roots)
                    self.assertEqual(bool(result.get("operand_expansion_truncated")), count > 64)
                    if count == 64:
                        self.assertTrue(result["rm_hits_game"])

    def test_benign_overflow_is_still_incomplete_and_the_next_call_is_clear(self):
        roots = {"game": "C:/x4-guard-fixture/game"}
        pattern = "C:/x4-guard-fixture/{" + ",".join("junk%d" % i for i in range(65)) + "}"
        result = F(D + " -rf " + pattern, roots=roots)
        self.assertTrue(result.get("operand_expansion_truncated"))
        self.assertFalse(result["rm_hits_game"])
        self.assertFalse(F(D + " -rf C:/x4-guard-fixture/ordinary", roots=roots).get("operand_expansion_truncated"))

    def test_unmodelled_numeric_range_does_not_claim_complete_analysis(self):
        roots = {"game": "C:/x4-guard-fixture/42", "reference": "C:/x4-guard-fixture/r42"}
        for pattern in ("{1..64}", "r{1..64}", "{100..164}"):
            with self.subTest(pattern=pattern):
                self.assertTrue(F(D + " -rf C:/x4-guard-fixture/" + pattern,
                                  roots=roots).get("operand_expansion_truncated"))

    def test_literal_parent_braces_do_not_hide_later_expansions(self):
        for leaf, pattern in (("game", "{junk,game}"), ("42", "{1..64}")):
            base = "C:/x4-guard-fixture/{literal}/"
            result = F(D + " -rf " + base + pattern, roots={"game": base + leaf})
            self.assertTrue(result["rm_hits_game"] or result.get("operand_expansion_truncated"))

    def test_parameter_sampling_is_not_certified_as_complete(self):
        for count in (4, 5):
            suffix = "".join("${%s:+%s}" % (chr(65 + i), chr(97 + i)) for i in range(count))
            result = F(D + ' -rf "C:/x4-guard-fixture/' + suffix + '"',
                       roots={"game": "C:/x4-guard-fixture/ab"})
            if count == 4:
                self.assertTrue(result["rm_hits_game"])
                self.assertFalse(result.get("operand_expansion_truncated"))
            else:
                self.assertTrue(result.get("operand_expansion_truncated"))

    def test_quoted_numeric_braces_are_a_literal_filename(self):
        result = F(D + " -rf 'C:/x4-guard-fixture/{1..64}'",
                   roots={"game": "C:/x4-guard-fixture/42"})
        self.assertFalse(result["rm_hits_game"])
        self.assertFalse(result.get("operand_expansion_truncated"))

    def test_a_literal_path_is_never_treated_as_a_pattern(self):
        self.assertFalse(H.glob_covers("/c/games/x4", "/c/games/x4"))

    def test_the_two_pattern_constants_did_not_collide(self):
        """The first draft of glob_covers reused the name `_GLOB_CHARS`, shadowing an
        existing SET with a STRING and breaking every parameter-expansion test.
        They mean different things and must stay distinct."""
        self.assertIsInstance(H._GLOB_CHARS, set)
        self.assertIsInstance(H._OPERAND_PATTERN_CHARS, str)
        self.assertIn("{", H._OPERAND_PATTERN_CHARS)
        self.assertNotIn("{", H._GLOB_CHARS)



class TestAnEscapedDollarIsProseNotAStatusRead(unittest.TestCase):
    r"""`\$` does not expand, so `$?` behind a backslash is text.

    MEASURED 2026-09-04: `git commit -m "fix \$? after a pipe"` was a NON-OVERRIDABLE
    DENY -- the guard refusing a commit message that DESCRIBES the trap it enforces.
    It fired on this session's own grep too, where the token was a search PATTERN.

    The must-NOT-fire half is the whole risk: an unescaped `$?` in double quotes DOES
    expand, and turning that off would be the opposite mistake, silently.
    """

    BS = chr(92)

    def _fires(self, cmd):
        return F(cmd)["dollarq_after_pipe"]

    def test_escaped_in_a_double_quoted_message(self):
        self.assertFalse(self._fires(
            'cat f | wc -l; git commit -m ' + DQ + 'fix ' + self.BS + '$? handling' + DQ))

    def test_escaped_and_unquoted(self):
        self.assertFalse(self._fires("ls | head; echo " + self.BS + "$?"))

    def test_single_quoted_is_still_literal(self):
        self.assertFalse(self._fires("ls | head; echo " + Q + "literal $? here" + Q))

    # --- must STILL fire ----------------------------------------------------------
    def test_a_real_read_after_a_pipe(self):
        self.assertTrue(self._fires("ls | head; echo $?"))

    def test_a_real_read_inside_DOUBLE_quotes_still_expands(self):
        self.assertTrue(self._fires("ls | head; echo " + DQ + "rc=$?" + DQ))

    def test_PIPESTATUS_is_still_the_recommended_escape(self):
        self.assertFalse(self._fires("ls | head; echo " + DQ + "${PIPESTATUS[0]}" + DQ))


class TestTheModsRootIsSearchable(unittest.TestCase):
    """The wrong-scope refusal cited a cost that is false for the mod source tree.

    Its message names 300 s and "GBs of binary database pages" -- true of the TOOLKIT
    root, where tools/basex/basex/data lives. MEASURED over the real mods root: 230 ms,
    1,190 files. A rule whose stated reason is visibly false where it fires is one
    people learn to route around, which costs more than the rule was ever worth.
    """

    def _fires(self, cmd):
        return F(cmd)["search_rooted_workspace"]

    def test_the_mods_root_is_no_longer_refused(self):
        self.assertFalse(self._fires('grep -rn faction ' + DQ + ROOTS["mods"] + DQ))

    def test_the_toolkit_root_is_STILL_refused(self):
        """This is where the GBs actually are."""
        self.assertTrue(self._fires('grep -rn faction ' + DQ + TOOLKIT + DQ))

    def test_the_game_root_is_STILL_refused(self):
        self.assertTrue(self._fires('grep -rn faction ' + DQ + GAME + DQ))

    def test_reference_has_its_own_rule_and_keeps_it(self):
        self.assertTrue(F('grep -rn faction ' + DQ + REF + DQ)["search_rooted_reference"])


# --------------------------------------------------------------- resolve size ceiling
# MEASURED 2026-09-06: a value that names its own variable makes resolve() grow
# MULTIPLICATIVELY -- x9 per pass on the real command, over the 5 passes the loop
# already allowed. The guard process reached an 18.3 GB working set on the BLOCKING
# PreToolUse path. The iteration count was bounded; the SIZE was the unwatched axis.
#
# Every bound below is a LITERAL, never _MAX_RESOLVED: a test that reads the
# constant it is checking cannot fail when the constant is absent for the right
# reason. MEASURED against the pre-ceiling module, same file otherwise:
#   3 self-references ->     6,140,996 chars
#   4                 ->    74,099,304 chars
#   6                 -> 2,660,511,704 chars in one string, 7.55 s


def test_a_self_referential_assignment_cannot_grow_the_token_without_bound():
    """PRE-CEILING this returns 6,140,996 characters."""
    assigns = {"B": "x" * 200 + "${B}" * 3}
    got = H.resolve("${B}", assigns)
    assert len(got) < 200_000, (
        "resolve() returned %d chars -- the size axis is unguarded" % len(got))


def test_the_ceiling_costs_a_BOUNDED_amount_of_work_not_merely_a_bounded_result():
    """A guard's second output is the TIME it costs (gotcha #35). PRE-CEILING this
    shape allocates 74,099,304 characters before returning."""
    assigns = {"B": "y" * 200 + "${B}" * 4}
    got = H.resolve("${B}", assigns)
    assert len(got) < 1_000_000, "allocated %d chars on the blocking path" % len(got)


def test_a_token_that_hit_the_ceiling_is_a_WHOLE_PASS_not_a_truncation():
    """The DIRECTION, and it needed a structural assertion rather than a behavioural
    one. `has_unresolved` was the obvious check and it is DECORATION here: MEASURED
    against a deliberate truncating mutant (`return expand_home(out[:_MAX_RESOLVED])`),
    all five tests in this block stayed green, because an arbitrary prefix of an
    exponentially-expanded string still contains "${...}" too.

    So assert the MECHANISM: what comes back must be one of the loop's own intermediate
    states -- the last complete pass under the ceiling. A truncation is not any pass,
    and that is what separates "we stopped expanding" from "we handed the rules a
    shorter operand", which would quietly narrow what every path rule sees.
    """
    assigns = {"B": "/some/path/" + "${B}" * 3}
    got = H.resolve("${B}", assigns)

    def one_pass(s):
        return H._VAR_OP.sub(
            lambda m: (lambda g: m.group(0) if g is None else g)(
                H._apply_op(m.group(1), m.group(2), m.group(3) or "", assigns)),
            H._VAR.sub(lambda m: assigns.get(m.group(1) or m.group(2), m.group(0)), s))

    states, s = [], "${B}"
    for _ in range(6):
        states.append(H.expand_home(s))
        s = one_pass(s)
    assert got in states, (
        "resolve() returned a string that is not any complete pass -- %d chars, "
        "starts %r" % (len(got), got[:60]))
    assert H.has_unresolved(got), "and it must still read as unresolved"


def test_ordinary_nested_variables_still_resolve_fully():
    """Falsification twin: this must NOT change, or the ceiling is just a blindfold.
    Two levels of indirection, which is why the loop exists at all."""
    assigns = {"A": "/tmp/one", "B": "${A}/two", "C": "${B}/three"}
    assert H.resolve("${C}", assigns) == "/tmp/one/two/three"


def test_a_long_but_finite_expansion_is_not_refused():
    """Twin for the other clause: BIG is fine, only UNBOUNDED is not. 40 KB of real
    value sits under the ceiling and must come back fully resolved."""
    assigns = {"P": "z" * 40000}
    got = H.resolve("${P}", assigns)
    assert got == "z" * 40000 and not H.has_unresolved(got)


# ------------------------------------------------- B2: ANSI-C quoting hid the verb
# tokens() already popped the `$` sigil before a quote, so `$'rm'` resolved correctly
# and the existing test passed. Inside the single-quoted run every character was
# appended literally, so the HEX and OCTAL spellings survived as escape TEXT and
# _verb_name's basename(norm(t)) reduced them to `x6d` and `155`.
#
# MEASURED 2026-09-06 against the guard as shipped: both spellings ALLOW
# `rm -rf <game root>` past a HARD BLOCK, and the same for reference/.

_BS = chr(92)
_SQ = chr(39)


def _ansi(body):
    """Build a $'...' token without writing an escape this file's own reader would eat."""
    return "$" + _SQ + body + _SQ


def test_ansi_c_HEX_escapes_resolve_to_the_real_verb():
    """$'\x72\x6d' is bash for `rm`. Pre-fix the verb was `x6d`."""
    seg = _ansi(_BS + "x72" + _BS + "x6d") + " -rf /tmp/x"
    assert H._verb_name(H._verb_token(seg)) == "rm"


def test_ansi_c_OCTAL_escapes_resolve_to_the_real_verb():
    """$'\162\155' is the same word by the other spelling. Pre-fix: `155`."""
    seg = _ansi(_BS + "162" + _BS + "155") + " -rf /tmp/x"
    assert H._verb_name(H._verb_token(seg)) == "rm"


def test_an_ansi_c_spelled_rm_still_reaches_the_game_HARD_BLOCK():
    """The consequence, not just the token: this is the rule that was bypassed."""
    cmd = _ansi(_BS + "x72" + _BS + "x6d") + ' -rf "' + ROOTS["game"] + '"'
    assert F(cmd)["rm_hits_game"] is True


def test_the_LOCALE_form_is_not_decoded_because_bash_does_not_decode_it():
    """$"..." is locale translation with a LITERAL body. Decoding it would be us
    inventing a rule bash does not have -- the twin that keeps the fix honest.

    Asserts the EXACT token, not merely that it is not "rm". The weaker form was
    decoration: MEASURED against a mutant that drops the `c == chr(39)` guard, the
    body scan then runs to end-of-string looking for a closing single quote, produces
    garbage, and `!= "rm"` is satisfied by the garbage. Almost anything passes a
    not-equal assertion, which is why it caught nothing.
    """
    assert H._ansi_c_decode(_BS + "x72" + _BS + "x6d") == "rm"
    seg = '$"' + _BS + 'x72' + _BS + 'x6d" -rf /tmp/x'
    toks = [t for t, _q in H.tokens(seg)]
    assert toks[0] == _BS + "x72" + _BS + "x6d", toks[:2]
    assert toks[1] == "-rf", "the run must not swallow the rest of the segment"


def test_an_UNKNOWN_escape_keeps_its_backslash_as_bash_does():
    """Falsification twin: the decoder must not swallow what it does not know."""
    assert H._ansi_c_decode(_BS + "q") == _BS + "q"
    assert H._ansi_c_decode("plain") == "plain"


def test_ordinary_single_quoted_text_is_untouched():
    """Twin for the guard clause: no `$` sigil means no decoding at all, so a quoted
    search string that merely LOOKS like an escape stays literal."""
    seg = "grep -r " + _SQ + _BS + "x72" + _SQ + " ."
    toks = [t for t, _q in H.tokens(seg)]
    assert toks[2] == _BS + "x72"


# ------------------- B4: two rules read `body`, every path rule reads `all_cmds`
# MEASURED 2026-09-06: a SINGLE `bash -c` wrapper hid a foreground long job, and two
# hid `$?`-after-a-pipeline. They were answering a question about a different string
# from the one the path rules see. Both are DENIES, so the cost was a silent allow.

_LONG = "uv run python gates/corpus_sweep.py"


def test_a_long_job_inside_a_shell_wrapper_is_still_a_long_job():
    """One wrapper. Pre-fix: False."""
    cmd = "bash -c " + chr(39) + _LONG + chr(39)
    assert F(cmd)["longjob_foreground"] is True


def test_a_long_job_TWO_wrappers_deep_is_still_a_long_job():
    """The carrier walk already reached this; only these two rules did not."""
    cmd = "bash -c " + chr(39) + 'sh -c "' + _LONG + '"' + chr(39)
    assert F(cmd)["longjob_foreground"] is True


def test_a_long_job_carried_by_eval_is_still_a_long_job():
    cmd = "eval " + chr(39) + _LONG + chr(39)
    assert F(cmd)["longjob_foreground"] is True


def test_a_long_job_in_a_SHELL_heredoc_is_still_a_long_job():
    cmd = "bash <<EOF" + chr(10) + _LONG + chr(10) + "EOF"
    assert F(cmd)["longjob_foreground"] is True


def test_a_long_job_NAMED_IN_A_DATA_HEREDOC_does_not_fire():
    """The must-NOT-fire half, and the reason this fix is safe to widen at all.

    heredoc_bodies() admits only bodies whose OPENER RUNS A SHELL, so a file being
    written that happens to quote a long job's name never reaches all_cmds. Without
    that filter, widening to all_cmds would deny writing documentation.
    """
    cmd = "cat > notes.md <<EOF" + chr(10) + "run " + _LONG + chr(10) + "EOF"
    assert F(cmd)["longjob_foreground"] is not True


def test_dollarq_after_a_pipe_survives_a_shell_wrapper():
    """Pre-fix: False, because dollarq_after_pipe read `body`."""
    cmd = "bash -c " + chr(39) + 'ls | grep x; echo "rc=$?"' + chr(39)
    assert F(cmd)["dollarq_after_pipe"] is True


def test_an_ordinary_command_still_trips_neither():
    """Falsification twin: widening the input must not make these fire on everything."""
    f = F("ls -la")
    assert f["longjob_foreground"] is not True
    assert f["dollarq_after_pipe"] is not True


# ---------------- B6-B9: four bypasses the fuzzer could only find once it had SEEDS
# MEASURED 2026-09-06. Seed coverage was 9 of 23 derived policy rules (39%), so six
# DENIES and a HARD BLOCK had never been fuzzed at all. Adding the seeds took the
# fuzzer from "no bypass found" to 36 bypasses; these four fixes took it to 0.

_DUR = "/KNOWLEDGEBASE.md"


def test_a_WRAPPER_does_not_hide_a_destructive_git():
    """B7, the B3 shape one step out: the scan stepped over leading assignments and
    not over leading wrappers, so `verb()` said git while the scan called the literal
    token `git` the SUBCOMMAND. `timeout` matters most -- CLAUDE.md #25 recommends it."""
    # The value-flag spellings are here too (B10): these two rules do their OWN token
    # scan rather than keying off verb(), so fixing _verb_token alone left 10 bypasses
    # measured by the fuzzer -- `env -u X4_GAME git -C <game> clean -fdx` among them.
    for pre in ("exec ", "setsid ", "nice -n 5 ", "timeout 5 ", "env FOO=1 ",
                "env -u X4_GAME ", "env -C /tmp ", "sudo -u root ",
                "timeout -s KILL 5 ", "stdbuf -o L "):
        cmd = pre + 'git -C "' + ROOTS["game"] + '" clean -fdx'
        assert F(cmd)["git_wipes_x4_dir"] is True, pre
        cmd = pre + 'git -C "' + ROOTS["game"] + '" checkout -- f.py'
        assert F(cmd)["git_discards_x4_files"] is True, pre


def test_a_wrapper_does_not_INVENT_a_destructive_git():
    """Falsification twin: stepping over wrappers must not make navigation destructive."""
    for cmd in ('timeout 5 git -C "' + ROOTS["game"] + '" clean -n',
                'exec git -C "' + ROOTS["game"] + '" checkout main',
                'nice -n 5 git -C "' + ROOTS["game"] + '" status'):
        f = F(cmd)
        assert f["git_wipes_x4_dir"] is not True, cmd
        assert f["git_discards_x4_files"] is not True, cmd


def test_a_durable_python_write_inside_a_wrapper_is_still_seen():
    """B6, the B4 shape: this rule read segments(body) while the carrier list existed.
    `bash -c` alone was enough to overwrite KNOWLEDGEBASE.md unseen."""
    inner = 'python -c ' + chr(39) + 'open("' + ROOTS["game"] + _DUR + '", "w")' + chr(39)
    assert F(inner)["durable_python_open_w"] is True            # control
    # A SHELL HEREDOC, because it nests without quote conflict. Naive attempts to
    # wrap `inner` in single quotes produce nested single quotes, which is malformed
    # shell -- an invalid command, not a bypass, and it briefly read as one.
    heredoc = "bash <<" + chr(39) + "EOF" + chr(39) + chr(10) + inner + chr(10) + "EOF"
    assert F(heredoc)["durable_python_open_w"] is True
    assert F("exec " + inner)["durable_python_open_w"] is True
    assert F("nice -n 5 " + inner)["durable_python_open_w"] is True


def test_a_data_heredoc_BEFORE_a_command_no_longer_deletes_it():
    """B8, and it is the sharpest of the four: dollarq_after_pipe called
    strip_heredocs on input that was ALREADY stripped. strip_heredocs keeps the
    OPENER, so the second pass met a dangling `<<X` with no terminator and consumed
    everything after it -- including the command being judged. The rule was not weak
    about heredocs; it was deleting its own input."""
    cmd = ("cat > /dev/null <<" + chr(39) + "X" + chr(39) + chr(10)
           + "data" + chr(10) + "X" + chr(10) + 'ls | grep x; echo "rc=$?"')
    assert F(cmd)["dollarq_after_pipe"] is True


def test_a_parameter_expansion_cannot_carry_the_status_out_of_sight():
    """B9. `_apply_op` already modelled suffix-strip and array-index; the rule never
    asked. Resolved PER SEGMENT -- a whole-string test short-circuits, because `$?`
    IS present in the command, inside the assignment."""
    assert F('FZ="rc=$?QQ"; ls | grep x; echo "${FZ%QQ}"')["dollarq_after_pipe"] is True
    assert F('FZA=("rc=$?"); ls | grep x; echo "${FZA[0]}"')["dollarq_after_pipe"] is True


def test_a_parameter_expansion_cannot_hide_a_durable_write_MODE():
    """B9's other half: the PATH already consulted resolve(), the MODE did not."""
    py = ('python -c ' + chr(39) + 'FZ=1' + chr(39))    # unused, keeps the line short
    cmd = ('FZ="wQQ"; python -c ' + chr(39) + 'open("' + ROOTS["game"] + _DUR
           + '", "${FZ%QQ}")' + chr(39))
    assert F(cmd)["durable_python_open_w"] is True


def test_the_PIPESTATUS_escape_hatch_survives_the_expansion_awareness():
    """The twin that matters most: the rule's own message recommends PIPESTATUS, and
    resolving more text must not turn the recommended idiom into a deny."""
    assert F('ls | grep x; echo "${PIPESTATUS[0]}"')["dollarq_after_pipe"] is not True
    assert F("git commit -m " + chr(39) + "fix $? after a pipe" + chr(39)
             )["dollarq_after_pipe"] is not True
    assert F('ls; echo "rc=$?"')["dollarq_after_pipe"] is not True


# ------------------- B10: a wrapper flag whose VALUE is a word became the verb
# MEASURED 2026-09-06, and this is a HARD BLOCK bypass, not a near miss:
#   env -u X4_GAME rm -rf <game>   -> verb `x4_game`   -> ALLOW
#   sudo -u root    rm -rf <game>  -> verb `root`      -> ALLOW
#   env -C /tmp     rm -rf <game>  -> verb `tmp`       -> ALLOW
#   timeout -s KILL 5 rm -rf ...   -> verb `kill`      -> ALLOW
# _verb_token skips any `-flag`, and after a wrapper it also skips a _WRAPPER_ARG --
# but that matches only a number, `{}` or `+`. `nice -n 5` and `xargs -I{}` worked
# purely because their values are numeric or braced; a WORD value stayed standing as
# the command name. PRE-ARC. `env -u VAR cmd` is what this workspace types to clear a
# root, so it is the reachable one.


def test_a_wrapper_flag_VALUE_is_not_the_command():
    """The verb, directly. Each of these resolved to its flag's value before."""
    for seg, want in [
        ("env -u X4_GAME rm -rf /x", "rm"),
        ("env -C /tmp rm -rf /x", "rm"),
        ("sudo -u root rm -rf /x", "rm"),
        ("timeout -s KILL 5 rm -rf /x", "rm"),
        ("stdbuf -o L rm -rf /x", "rm"),
    ]:
        assert H._verb_name(H.verb(seg)) == want, seg


def test_those_spellings_still_reach_the_game_HARD_BLOCK():
    """The consequence. A verb-keyed miss takes every rule with it."""
    for pre in ("env -u X4_GAME ", "env -C /tmp ", "sudo -u root ",
                "timeout -s KILL 5 ", "stdbuf -o L "):
        cmd = pre + 'rm -rf "' + ROOTS["game"] + '"'
        assert F(cmd)["rm_hits_game"] is True, cmd


def test_the_value_skip_is_PER_WRAPPER_not_a_union():
    """The twin that keeps the fix from being worse than the bug. Consuming a value a
    flag does NOT take would eat the REAL verb and return its first argument -- a
    miss, which is strictly less safe. `-u` belongs to env/sudo; it must not be
    honoured for a wrapper that has no such flag."""
    assert H._verb_name(H.verb("nice -u rm -rf /x")) == "rm"
    assert H._verb_name(H.verb("nohup -u rm -rf /x")) == "rm"


def test_the_numeric_and_braced_forms_that_already_worked_still_work():
    """Falsification twin: these passed before via _WRAPPER_ARG and must not regress."""
    assert H._verb_name(H.verb("nice -n 5 rm -rf /x")) == "rm"
    assert H._verb_name(H.verb("timeout 5 rm -rf /x")) == "rm"
    assert H._verb_name(H.verb("xargs -I{} rm -rf /x")) == "rm"


def test_a_wrapper_flag_does_not_swallow_an_ORDINARY_command():
    """And the guard must not start firing on innocent work."""
    f = F("env -u X4_GAME ls -la")
    assert f["rm_hits_game"] is not True
    assert H._verb_name(H.verb("env -u X4_GAME ls -la")) == "ls"

# ------------- the v3.1.0 release review, group A: a quoted token in verb position
# `for t, quoted in tokens(seg): if quoted: return t` fired BEFORE the assignment,
# wrapper and flag-value skips, so quoting ONE token in the prefix flipped 7 of 9
# verb-keyed rules from fire to allow. PRE-ARC (same ordering at v3.0.0), and it also
# defeated this arc's own _WRAPPER_VALUE_OPTS fix -- one quote was enough.


def test_a_quoted_ASSIGNMENT_prefix_does_not_become_the_verb():
    """`FOO="bar" rm -rf <game>` resolved its verb to `foo=bar`."""
    for pre in ('FOO="bar" ', "FOO='bar' ", 'A=1 B="2" ',
                'PYTHONIOENCODING="utf-8" '):
        assert H._verb_name(H.verb(pre + "rm -rf /x")) == "rm", pre


def test_a_quoted_WRAPPER_VALUE_does_not_become_the_verb():
    """One quote defeated _WRAPPER_VALUE_OPTS, because the short-circuit was first."""
    for pre in ('env -u "X4_GAME" ', 'sudo -u "root" ', 'timeout -s "KILL" 5 '):
        assert H._verb_name(H.verb(pre + "rm -rf /x")) == "rm", pre


def test_those_quoted_spellings_still_reach_the_game_HARD_BLOCK():
    """The consequence: a wrong verb takes every verb-keyed rule with it at once."""
    for pre in ('FOO="bar" ', 'env -u "X4_GAME" ', 'sudo -u "root" '):
        cmd = pre + 'rm -rf "' + ROOTS["game"] + '"'
        assert F(cmd)["rm_hits_game"] is True, pre


def test_a_genuinely_QUOTED_COMMAND_NAME_still_resolves():
    """The twin. Removing the short-circuit must not lose a quoted command name --
    it now falls through to the ordinary return instead of jumping the queue."""
    assert H._verb_name(H.verb('"my prog" -x')) == "my prog"


def test_a_quoted_ORDINARY_command_is_not_newly_blocked():
    """And nothing innocent starts firing."""
    assert F('"ls" -la')["rm_hits_game"] is not True
    assert H._verb_name(H.verb('"ls" -la')) == "ls"


# ------------------------------------------------- F111 / F112 re-derivation
# Added 2026-09-13 to close two BLIND-SPOTS entries that were marked FIXED while
# naming no check that re-derives them. A FIXED with no re-derivation is
# indistinguishable from a FIXED that regressed, which is why these exist as NAMED
# tests rather than as coverage of the mechanism: `scripts/fuzz-guard.py` already
# exercises the resolved-segment path, but nothing asserted F111's per-item claim.


def test_F111_a_variable_spelled_verb_denies_like_the_plain_one():
    """F111: `facts()` resolves once into `seg_cwd`, but three rules re-derived their
    own walk from the raw text and so never saw a resolved verb. MEASURED pre-fix:
    the variable spelling of a rooted DELETE was caught while the same spelling of a
    rooted SEARCH and of a durable python write were not.

    Asserted as an EQUIVALENCE rather than as three separate truths: the claim is that
    the two spellings agree, so the plain form is checked in the same breath. A test
    that only asserted the variable form would stay green if the rule broke for both.
    """
    ref = DQ + REF + DQ
    durable = Q + 'open("KNOWLEDGEBASE.md","w")' + Q
    pairs = [
        ("search_rooted_reference", "grep -rn x " + ref,
                                    "GP=grep; $GP -rn x " + ref),
        ("durable_python_open_w",   "python -c " + durable,
                                    "PY=python; $PY -c " + durable),
        ("rm_targets_reference",    "rm -rf " + ref,
                                    "RM=rm; $RM -rf " + ref),
    ]
    for key, plain, spelled in pairs:
        got_plain = bool(F(plain).get(key))
        got_var = bool(F(spelled).get(key))
        assert got_plain, "%s did not fire on the PLAIN spelling: %s" % (key, plain)
        assert got_var, (
            "%s fired on the plain spelling but NOT on the variable one -- F111 has "
            "regressed for this rule: %s" % (key, spelled))


def test_F111_an_unrooted_recursive_search_is_untouched():
    """The other half of F111, and it needs its own test: routing the search rule
    through resolved segments must not make it fire on a search that names no root.
    Without this, a fix that simply returned True would satisfy the equivalence test
    above. This is the clause that proves the fix did not over-widen.
    """
    assert not F("grep -rn foo .").get("search_rooted_reference"), (
        "an unrooted recursive search now reads as rooted at the reference tree")
    assert not F("GP=grep; $GP -rn foo .").get("search_rooted_reference"), (
        "same, through a variable-spelled verb")


def test_F112_an_APPEND_into_the_read_only_tree_is_denied():
    """F112: `writes_reference` was built from `trunc_redirect`, which keeps only
    redirects whose mode is `truncate`. MEASURED pre-fix: `>` into the read-only tree
    denied and `>>` into the same file was ALLOWED -- two spellings of one operation
    disagreeing under a rule whose message is "never write into it". `tee -a` is the
    other spelling of an append and is asserted beside it.

    The truncating form is included as a CONTROL: if it ever stops firing, this test
    must not keep passing on the append alone.
    """
    target = DQ + REF + "/libraries/wares.xml" + DQ
    assert F("echo x > " + target).get("writes_reference"), (
        "the TRUNCATING write into the read-only tree stopped being detected -- the "
        "control for this test is broken, so its append result means nothing")
    assert F("echo x >> " + target).get("writes_reference"), (
        "an APPEND into the read-only tree is allowed again -- F112 has regressed")
    assert F("printf a | tee -a " + DQ + REF + "/w.xml" + DQ).get("writes_reference"), (
        "tee -a into the read-only tree is allowed again -- the other append spelling")


def test_F112_the_advisory_tree_keeps_the_wider_channel():
    """The asymmetry that PRODUCED F112, pinned so it cannot come back the other way.
    `writes_documents` always used the wider `writes_any`, which includes appends; the
    hard-blocked read-only tree had the NARROWER channel and the merely-advisory tree
    the wider one. Truncate-only was correct where it came from and wrong where it was
    copied to -- so this asserts the advisory side still sees an append, rather than
    someone "restoring symmetry" by narrowing it to match.
    """
    assert F("echo x >> " + DQ + DOCS + "/x.txt" + DQ).get("writes_documents"), (
        "the advisory documents tree no longer sees an APPEND -- the wider channel "
        "was narrowed to match the read-only tree, which inverts F112's fix")
    assert F("echo x > " + DQ + DOCS + "/x.txt" + DQ).get("writes_documents"), (
        "the advisory documents tree no longer sees a truncating write either")


# ------------------------------------------------ AUDIT-2026-09-24 HK-2 (Bash holes)
SAVES = PROF + "/save"


class TestHK2FilteredFindIsScopedNotExempt(unittest.TestCase):
    """A filtered find-delete used to reach NO rule: MEASURED, `find <saves> -name
    '*.xml.gz' -delete` and `find <reference> -name '*.xml' -delete` were ALLOW."""

    def test_every_save_by_filter_asks(self):
        f = F("find " + DQ + SAVES + DQ + " -name " + Q + "*.xml.gz" + Q + " -delete")
        self.assertTrue(f["rm_saves"])
        self.assertFalse(f["rm_hits_game"])

    def test_a_filtered_delete_in_reference_is_still_a_reference_delete(self):
        f = F("find " + DQ + REF + DQ + " -name " + Q + "*.xml" + Q + " -delete")
        self.assertTrue(f["rm_targets_reference"])

    def test_a_wrapper_with_a_flag_does_not_hide_finds_paths(self):
        """fuzz-guard, on this class's seed: find's paths were read from token 1, so
        `nice -n 5 find ...` stopped at the wrapper's `-n` and found none."""
        for w in ("nice -n 5 ", "sudo -u " + DQ + "root" + DQ + " ", "timeout -s KILL 5 "):
            f = F(w + "find " + DQ + SAVES + DQ + " -name x -delete")
            self.assertTrue(f["rm_saves"], w)
        self.assertTrue(F("stdbuf -o L find " + DQ + GAME + DQ + " -delete")["rm_hits_game"])

    def test_exec_rm_with_a_filter_counts_too(self):
        f = F("find " + DQ + REF + DQ + " -name x -exec " + D + " {} +")
        self.assertTrue(f["rm_targets_reference"])

    # ---- twins: the cleanup the exemption was written for ----------------------
    def test_TWIN_a_pycache_cleanup_is_still_silent_everywhere(self):
        for root in (GAME, REF, TOOLKIT):
            f = F("find " + DQ + root + DQ + " -name __pycache__ -type d -exec "
                  + D + " -rf {} +")
            self.assertFalse(f["rm_in_x4_dir"] or f["rm_targets_reference"], root)

    def test_TWIN_a_pyc_glob_is_regenerable_too(self):
        f = F("find " + DQ + TOOLKIT + DQ + " -name " + Q + "*.pyc" + Q + " -delete")
        self.assertFalse(f["rm_in_x4_dir"])

    def test_TWIN_an_extra_AND_filter_keeps_a_cache_cleanup_exempt(self):
        """Friction replay, 36 historical commands: `-not -path` / `-path` beside a
        cache name only SHRINKS the set (find ANDs its tests)."""
        for extra in (" -not -path " + DQ + "./.venv/*" + DQ, " -path " + DQ + "*/gates/*" + DQ):
            f = F("find " + DQ + TOOLKIT + DQ + " -name " + DQ + "__pycache__" + DQ
                  + " -type d" + extra + " -exec " + D + " -rf {} + 2>/dev/null")
            self.assertFalse(f["rm_in_x4_dir"], extra)

    def test_TWIN_a_cache_name_does_not_launder_a_second_filter(self):
        """`-path` beside a cache name is not a cache-only filter."""
        f = F("find " + DQ + REF + DQ + " -path " + Q + "*/libraries/*" + Q
              + " -o -name __pycache__ -delete")
        self.assertTrue(f["rm_targets_reference"])


class TestHK2InPlaceClobbers(unittest.TestCase):
    """`truncate` and `dd of=` destroy content in place; they were ALLOW into reference/
    while `> <reference>/f` hard-blocks. They are judged as a truncating redirect."""

    def test_truncate_into_reference(self):
        self.assertTrue(F("truncate -s 0 " + DQ + REF + "/libraries/wares.xml" + DQ)
                        ["writes_reference"])

    def test_truncate_long_option_value_is_not_the_target(self):
        f = F("truncate --size 0 " + DQ + REF + "/a.xml" + DQ)
        self.assertTrue(f["writes_reference"])

    def test_dd_of_into_reference(self):
        self.assertTrue(F("dd if=/dev/zero of=" + DQ + REF + "/a.xml" + DQ)
                        ["writes_reference"])

    def test_truncate_a_durable_record(self):
        self.assertTrue(F("truncate -s 0 KNOWLEDGEBASE.md")["durable_truncating_redirect"])

    def test_TWIN_dd_reading_FROM_reference_is_not_a_write(self):
        f = F("dd if=" + DQ + REF + "/a.xml" + DQ + " of=./copy.bin")
        self.assertFalse(f["writes_reference"])

    def test_TWIN_truncate_reference_as_the_SIZE_SOURCE_is_not_a_write(self):
        f = F("truncate -r " + DQ + REF + "/a.xml" + DQ + " ./mine.bin")
        self.assertFalse(f["writes_reference"])


class TestHK2DeleteVerbsStayCovered(unittest.TestCase):
    """shred/unlink were in the audit's list; they were ALREADY delete verbs. Pinned so
    they stay that way."""

    def test_shred_and_unlink(self):
        for v in ("shred -u", "unlink"):
            self.assertTrue(F(v + " " + DQ + REF + "/a.xml" + DQ)["rm_targets_reference"], v)
            self.assertTrue(F(v + " " + DQ + SAVES + "/a.xml.gz" + DQ)["rm_saves"], v)


class TestHK2CmdCarrier(unittest.TestCase):
    """`cmd //c` runs cmd.exe text; it reached no rule."""

    def test_rd_under_cmd(self):
        f = F("cmd //c rd /s /q " + DQ + REF + DQ)
        self.assertTrue(f["rm_targets_reference"])

    def test_single_slash_and_whole_payload_quoted(self):
        f = F("cmd /c " + DQ + "del /q " + SAVES.replace("/", BS) + BS + "a.xml.gz & echo ok" + DQ)
        self.assertTrue(f["rm_saves"])

    def test_a_redirect_under_cmd(self):
        f = F("cmd //c " + DQ + "echo x > " + REF + "/a.xml" + DQ)
        self.assertTrue(f["writes_reference"])

    def test_TWIN_a_harmless_cmd(self):
        f = F("cmd //c dir " + DQ + REF + DQ)
        self.assertFalse(f["rm_targets_reference"] or f["writes_reference"])

    def test_a_percent_variable_is_a_shell_variable(self):
        self.assertEqual(H.cmd_to_sh(["rd", "/s", "%x4_reference%" + BS + "lib"]),
                         'rm -rf "${X4_REFERENCE}"' + "'" + BS + "lib'")
        self.assertTrue(F('cmd //c rd /s /q "%X4_REFERENCE%"')["rm_targets_reference"])
        self.assertEqual(H.cmd_to_sh(["echo", "100%%"]), "echo 100%")

    def test_translation_drops_switches_and_maps_verbs(self):
        self.assertEqual(H.cmd_to_sh(["rd", "/s", "/q", "C:/x y"]), "rm -rf 'C:/x y'")
        self.assertEqual(H.cmd_to_sh(["ren C:/a/b.txt c.txt"]), "mv C:/a/b.txt C:/a/c.txt")


class TestRRCmdCarrier(unittest.TestCase):
    """v3.3.0 release review, finding 3: `cd /d X` lost the directory, `/R` was not /C,
    and a caret-escaped verb was an unknown command."""

    def test_cd_d_keeps_the_directory_and_joins_a_spaced_path(self):
        self.assertEqual(H.cmd_to_sh(["cd /d C:/x y && rd /s /q ext"]),
                         "cd 'C:/x y'" + chr(10) + "rm -rf ext")
        self.assertEqual(H.cmd_to_sh(["pushd", "C:/x"]), "pushd C:/x")
        self.assertEqual(H.cmd_to_sh(["chdir /D C:/x"]), "cd C:/x")

    def test_carets_are_escapes_outside_quotes(self):
        self.assertEqual(H.cmd_to_sh(["r^d /s /q C:/r"]), "rm -rf C:/r")
        self.assertEqual(H.cmd_to_sh(["^d^e^l C:/r/a"]), "rm -f C:/r/a")
        # an ESCAPED separator is a character, not a second command
        self.assertEqual(H.cmd_to_sh(["echo a^&b"]), "echo 'a&b'")
        # inside double quotes a caret is literal
        self.assertEqual(H.cmd_to_sh(['echo "a^b"']), "echo 'a^b'")

    def test_an_unquoted_spaced_root_in_a_cmd_delete_is_still_that_root(self):
        """Coordinator verification: cmd splits an unquoted `...\\X4 Foundations` into two
        operands, so none matched the root and the delete ALLOWED. GAME has a space."""
        g = GAME.replace("/", BS)
        for payload in ("r^d /s /q " + g, "rd /s /q " + g, "rmdir /s /q " + g + BS + "extensions"):
            with self.subTest(payload=payload):
                self.assertTrue(F("cmd //c " + DQ + payload + DQ)["rm_hits_game"])
        self.assertTrue(F("cmd //c " + DQ + "del /q " + g + BS + "a.txt" + DQ)["rm_in_x4_dir"])
        # the spans are for DELETES, and only rejoin what is really there
        self.assertEqual(H.cmd_to_sh(["copy a b"]), "cp a b")
        self.assertEqual(H.cmd_to_sh(["rd /s /q a b"]), "rm -rf a b 'a b'")
        self.assertFalse(F("cmd //c " + DQ + "rd /s /q build dist" + DQ)["rm_in_x4_dir"])

    def test_TWIN_bash_eats_unquoted_backslashes_before_cmd_sees_them(self):
        """`cmd //c rd /s /q C:\\a\\b` UNQUOTED in Bash: bash turns `\\a` into `a`, so cmd
        receives `C:ab` -- not the root. The guard reads what Bash hands over, and so
        does not invent a match (NOT a defect: the command genuinely deletes elsewhere)."""
        self.assertEqual(H.tokens("cmd //c rd /s /q C:" + BS + "t" + BS + "X4")[-1][0], "C:tX4")

    def test_slash_r_is_slash_c(self):
        f = F("cmd //r rd /s /q " + DQ + REF + DQ)
        self.assertTrue(f["rm_targets_reference"])
        self.assertTrue(F("cmd /R del /q " + DQ + REF + "/a.xml" + DQ)["rm_targets_reference"])

    def test_a_relative_delete_after_cd_d_is_judged_where_it_runs(self):
        self.assertTrue(F("cmd //c " + DQ + "cd /d " + GAME + " && rd /s /q extensions" + DQ)
                        ["rm_hits_game"])

    def test_TWIN_harmless(self):
        self.assertFalse(F("cmd //c " + DQ + "cd /d C:/work && rd /s /q build" + DQ)["rm_in_x4_dir"])
        self.assertFalse(F("cmd //r dir /b")["carrier_untranslated"])


class TestRRPowerShellHostPayload(unittest.TestCase):
    """Finding 2: -CommandWithArgs / -cwa and `-File -` were not payload forms."""

    def test_forms(self):
        P = H._ps_host_payload
        self.assertEqual(P(["pwsh", "-cwa", "Get-Date", "x"]), ("cmd", "Get-Date"))
        self.assertEqual(P(["pwsh", "-NoProfile", "-CommandWithArgs", "Get-Date"]), ("cmd", "Get-Date"))
        self.assertEqual(P(["pwsh", "-File", "-"]), ("stdin", ""))
        self.assertEqual(P(["pwsh", "-Command", "-"]), ("stdin", ""))
        self.assertEqual(P(["pwsh", "-NoProfile"]), ("none", ""))
        self.assertEqual(P(["pwsh", "-File", "x.ps1"]), ("file", ""))
        self.assertEqual(P(["pwsh", "-Command", "Get-Date"]), ("cmd", "Get-Date"))

    def test_stdin_program(self):
        S = H._ps_stdin_program
        self.assertEqual(S("pwsh -NoProfile", "echo " + DQ + "Get-Date" + DQ), (["Get-Date"], False))
        self.assertEqual(S("pwsh", "printf '%s' 'Get-Date'"), (["Get-Date"], False))
        self.assertEqual(S("pwsh -Command - <<< 'Get-Date'", None), (["Get-Date"], False))
        self.assertEqual(S("pwsh -NoProfile", "cat x.ps1"), ([], True))
        self.assertEqual(S("pwsh -File - < x.ps1", None), ([], True))
        self.assertEqual(S("pwsh -NoProfile", None), ([], False))

    def test_only_a_PIPE_feeds_stdin(self):
        self.assertEqual(H.piped_in("a | b; c && d | e"), [False, True, False, False, True])
        self.assertEqual(H.piped_in("echo 'a | b' | c"), [False, True])
        self.assertEqual(H.piped_in("a 2>&1 | b"), [False, True])
        self.assertEqual(len(H.piped_in("x; ; y")), len(H.segments("x; ; y")))
        # replay FPs: a lookup of the host, or a host after an unrelated command, asked
        for cmd in ("command -v powershell.exe; command -v pwsh.exe", "which pwsh; pwsh -v",
                    "ls x; pwsh -NoProfile"):
            with self.subTest(cmd=cmd):
                self.assertFalse(F(cmd)["carrier_untranslated"])
        self.assertTrue(F("cat x.ps1 | pwsh -NoProfile")["carrier_untranslated"])

    def test_ps_reads_stdin(self):
        self.assertTrue(H._ps_reads_stdin("pwsh -NoProfile <<'EOF'"))
        self.assertTrue(H._ps_reads_stdin("pwsh -Command - <<EOF"))
        self.assertFalse(H._ps_reads_stdin("pwsh -File x.ps1 <<EOF"))
        self.assertFalse(H._ps_reads_stdin("bash <<EOF"))


class TestRRPreArcBash(unittest.TestCase):
    """The reviewer's pre-arc notes, unit level (E2E in test_audit0924_hooks.py)."""

    def test_xargs_feed(self):
        self.assertEqual(H.xargs_feed("xargs rm -rf", "echo " + Q + REF + Q), [REF])
        self.assertEqual(H.xargs_feed("xargs -0 rm -rf", "printf '%s' " + Q + REF + Q), [REF])
        self.assertEqual(H.xargs_feed("xargs rm -f", "find " + Q + REF + Q + " -name x"),
                         [REF + "/${XARGS_ITEM}"])
        self.assertEqual(H.xargs_feed("xargs rm -f", "ls " + Q + REF + Q), [REF + "/${XARGS_ITEM}"])
        # the cache cleanup find-delete already exempts, spelled through xargs
        self.assertEqual(H.xargs_feed("xargs -r rm -rf", "find " + Q + TOOLKIT + Q
                                      + " -name __pycache__ -o -name '*.pyc'"), [])
        self.assertNotEqual(H.xargs_feed("xargs rm -rf", "find " + Q + TOOLKIT + Q
                                         + " -name __pycache__ -o -name '*.xml'"), [])
        self.assertEqual(H.xargs_feed("rm -rf x", "echo " + REF), [])        # no xargs
        self.assertEqual(H.xargs_feed("xargs grep x", "echo " + REF), [])    # not a delete
        self.assertEqual(H.xargs_feed("xargs rm -rf", None), [])

    def test_xargs_facts(self):
        self.assertTrue(F("echo " + Q + REF + Q + " | xargs rm -rf")["rm_targets_reference"])
        self.assertTrue(F("printf '%s' " + Q + GAME + Q + " | xargs -0 rm -rf")["rm_hits_game"])
        f = F("find " + Q + GAME + "/extensions" + Q + " -name '*.bak' | xargs rm -f")
        self.assertTrue(f["rm_in_x4_dir"])
        self.assertFalse(f["rm_hits_game"])
        self.assertFalse(F("find . -name '*.pyc' | xargs rm -f")["rm_in_x4_dir"])

    def test_for_loop_and_arrays_expand_every_element(self):
        self.assertEqual(H.assignments("for f in a 'b c' d; do echo; done")["f"], "(a 'b c' d)")
        self.assertEqual(H.resolve_all("$f", {"f": "(a 'b c')"}), ["a", "b c"])
        self.assertEqual(H.resolve_all("${A[@]}", {"A": "(x y)"}), ["x", "y"])
        self.assertEqual(H.resolve_all("$f/sub", {"f": "(a b)"}), ["a/sub", "b/sub"])
        self.assertEqual(H.resolve_all("${f}x", {"f": "(a b)", "fx": "no"}), ["ax", "bx"])
        self.assertEqual(H.resolve_all("$fx", {"f": "(a b)", "fx": "no"}), ["no"])
        self.assertTrue(F("for g in x " + Q + GAME + Q + "; do rm -rf " + DQ + "$g/extensions"
                          + DQ + "; done")["rm_hits_game"])
        self.assertTrue(F("for f in " + Q + REF + Q + "/*; do rm -rf " + DQ + "$f" + DQ + "; done")
                        ["rm_targets_reference"])
        self.assertTrue(F("for d in build " + Q + GAME + Q + "; do rm -rf " + DQ + "$d" + DQ
                          + "; done")["rm_hits_game"])
        self.assertFalse(F("for f in *.tmp; do rm -f " + DQ + "$f" + DQ + "; done")["rm_in_x4_dir"])
        # quoted prose is not a loop
        self.assertNotIn("f", H.assignments("echo 'for f in x y'"))

    def test_substitution_assignments_and_identity(self):
        self.assertEqual(H.assignments("t=$(realpath -m 'a b/c'); rm -rf x")["t"],
                         "$(realpath -m 'a b/c')")
        self.assertEqual(H.assignments('t="$(ls x | wc -l)"')["t"], "$(ls x | wc -l)")
        self.assertEqual(H.identity_subst("$(realpath -m 'a b/c')"), "a b/c")
        self.assertEqual(H.identity_subst("$(cygpath -u C:/x)"), "C:/x")
        self.assertEqual(H.identity_subst("$(readlink -f x)"), "x")
        self.assertEqual(H.identity_subst("$(readlink x)"), "$(readlink x)")
        self.assertEqual(H.identity_subst("$(realpath a b)"), "$(realpath a b)")
        self.assertEqual(H.identity_subst("$(echo x | tr a b)"), "$(echo x | tr a b)")
        self.assertEqual(H.identity_subst("$(mktemp -d)"), "$(mktemp -d)")
        self.assertTrue(F("t=$(realpath -m " + Q + REF + "/libraries" + Q + "); rm -rf "
                          + DQ + "$t" + DQ)["rm_targets_reference"])
        self.assertTrue(F("t=$(cygpath -u " + Q + GAME + Q + "); rm -rf " + DQ + "$t" + DQ)
                        ["rm_hits_game"])
        # not an identity, but its text names the root: the conservative branch
        self.assertTrue(F("t=$(ls " + Q + GAME + Q + " | head -1); rm -rf " + DQ + "$t" + DQ)
                        ["rm_in_x4_dir"])
        self.assertFalse(F("t=$(mktemp -d); rm -rf " + DQ + "$t" + DQ)["rm_in_x4_dir"])
        # a case arm's `)` and a carrier's own assignments (fuzz-guard, this lane)
        self.assertTrue(F("case $x in *)t=$(realpath -m " + Q + REF + Q + "); rm -rf "
                          + DQ + "$t" + DQ + " ;; esac")["rm_targets_reference"])
        self.assertTrue(F("bash -c " + Q + "t=$(realpath -m " + DQ + REF + DQ + "); rm -rf "
                          + DQ + "$t" + DQ + Q)["rm_targets_reference"])
        self.assertTrue(F("bash -c " + Q + "for f in " + DQ + GAME + DQ + "; do rm -rf "
                          + DQ + "$f" + DQ + "; done" + Q)["rm_hits_game"])
        self.assertTrue(F("Z=" + DQ + "$f" + DQ + "; for f in " + Q + GAME + Q + "; do rm -rf "
                          + DQ + "$Z" + DQ + "; done")["rm_hits_game"])

    def test_rsync_delete(self):
        self.assertEqual(H.rsync_deletes("rsync -a --delete e/ " + Q + GAME + "/" + Q), [GAME + "/*"])
        self.assertEqual(H.rsync_deletes("rsync -a e/ x/"), [])
        self.assertEqual(H.rsync_deletes("rsync -a --delete-after e/ x/"), ["x/*"])
        self.assertTrue(F("rsync -a --delete empty/ " + Q + GAME + "/" + Q)["rm_hits_game"])
        f = F("rsync -a --delete ./m/ " + Q + GAME + "/extensions/amod/" + Q)
        self.assertTrue(f["rm_in_x4_dir"])
        self.assertFalse(f["rm_hits_game"])
        self.assertFalse(F("rsync -a --delete ./a/ ./b/")["rm_in_x4_dir"])

    def test_robocopy(self):
        self.assertEqual(H.robocopy_effects("robocopy C:/e " + Q + GAME + Q + " //MIR"),
                         ([GAME], [GAME + "/*"], []))
        self.assertEqual(H.robocopy_effects("robocopy a b /E /R:2"), (["b"], [], []))
        self.assertEqual(H.robocopy_effects("robocopy a b *.xml /MOV"), (["b"], [], ["a"]))
        self.assertEqual(H.robocopy_effects("robocopy /c/a /c/b /PURGE"), (["/c/b"], ["/c/b/*"], []))
        self.assertTrue(F("robocopy C:/empty " + Q + GAME + Q + " /MIR")["rm_hits_game"])
        f = F("robocopy ./m " + Q + GAME + "/extensions/amod" + Q + " /E")
        self.assertTrue(f["copy_into_game_or_profile"])
        self.assertFalse(f["rm_in_x4_dir"])
        self.assertTrue(F("robocopy " + Q + REF + Q + " C:/x /MOVE")["rm_targets_reference"])

    def test_modify_targets(self):
        M = H.modify_targets
        self.assertEqual(M("touch -d yesterday a b"), ["a", "b"])
        self.assertEqual(M("chmod -R 000 a"), ["a"])
        self.assertEqual(M("chown u:g a"), ["a"])
        self.assertEqual(M("ln -sf /dev/null a"), ["a"])
        self.assertEqual(M("ln -s x"), [])
        self.assertEqual(M("ln -s -t d a b"), ["d"])
        self.assertEqual(M("cat a"), [])
        for cmd in ("touch " + Q + REF + "/a.xml" + Q, "chmod 000 " + Q + REF + "/libraries" + Q,
                    "ln -sf /dev/null " + Q + REF + "/a.xml" + Q):
            with self.subTest(cmd=cmd):
                self.assertTrue(F(cmd)["writes_reference"])
        for cmd in ("touch ./x", "chmod +x ./s.sh", "ln -s " + Q + REF + "/libraries" + Q + " ./lib"):
            with self.subTest(cmd=cmd):
                self.assertFalse(F(cmd)["writes_reference"])


_PWSH = H._pwsh_exe()


@unittest.skipUnless(_PWSH, "no PowerShell on this machine: the PowerShell front-end "
                            "cannot be exercised here (it fails closed without one)")
class TestHK1PowerShellFrontEnd(unittest.TestCase):
    """AUDIT-2026-09-24 HK-1: the PowerShell tool had no guard. Its payload is parsed by
    PowerShell's own parser and translated into the Bash vocabulary; the rules are the
    same ones."""

    def P(self, command):
        return H.facts({"tool_name": "PowerShell", "tool_input": {"command": command}}, ROOTS)

    def test_remove_item_recurse_reference_is_the_reference_block(self):
        f = self.P("Remove-Item -Recurse " + Q + REF + Q)
        self.assertTrue(f["rm_targets_reference"])
        self.assertTrue(f["from_powershell"])

    def test_an_alias_and_a_parameter_prefix_bind_as_powershell_binds_them(self):
        self.assertTrue(self.P("ri -r -fo " + DQ + GAME + DQ)["rm_hits_game"])

    def test_a_variable_resolves(self):
        f = self.P("$p = " + Q + REF + Q + "; Remove-Item " + DQ + "$p/libraries" + DQ)
        self.assertTrue(f["rm_targets_reference"])

    def test_an_env_root_variable_names_its_root(self):
        self.assertTrue(self.P("Remove-Item $env:X4_REFERENCE/x")["rm_targets_reference"])

    def test_set_content_is_a_write(self):
        self.assertTrue(self.P("Set-Content -Path " + Q + REF + "/a.xml" + Q + " -Value x")
                        ["writes_reference"])

    def test_a_redirect_is_a_write(self):
        self.assertTrue(self.P(Q + "x" + Q + " > " + Q + REF + "/a.xml" + Q)
                        ["writes_reference"])

    def test_a_piped_filtered_delete_of_saves_asks(self):
        f = self.P("Get-ChildItem " + Q + SAVES + Q + " -Filter *.gz | Remove-Item")
        self.assertTrue(f["rm_saves"])
        self.assertFalse(f["rm_hits_game"])

    def test_a_dotnet_delete(self):
        self.assertTrue(self.P("[IO.File]::Delete(" + Q + REF + "/a.xml" + Q + ")")
                        ["rm_targets_reference"])

    def test_invoke_expression_is_followed(self):
        inner = "Remove-Item -Recurse " + REF
        self.assertTrue(self.P("iex " + Q + inner + Q)["rm_targets_reference"])

    def test_hygiene_rules_apply_too(self):
        self.assertTrue(self.P("git add -A")["git_add_all"])

    def test_bare_python_on_toolkit_code_fires_through_powershell(self):
        # A native program (not a cmdlet) goes through WORD FOR WORD (see
        # ps_translate.ps1's Translate-Command), so this is the SAME rule, not a
        # second implementation.
        self.assertTrue(
            self.P("python gates/claims_audit.py")["bare_python_on_project_code"])

    def test_uv_run_python_does_not_fire_through_powershell(self):
        self.assertFalse(
            self.P("uv run python gates/claims_audit.py")["bare_python_on_project_code"])

    def test_nested_pwsh_in_bash(self):
        f = F("pwsh -NoProfile -Command " + DQ + "Remove-Item -Recurse " + Q + REF + Q + DQ)
        self.assertTrue(f["rm_targets_reference"])

    def test_nested_encoded_command_in_bash(self):
        import base64
        enc = base64.b64encode(("Remove-Item -Recurse " + Q + REF + Q).encode("utf-16-le"))
        self.assertTrue(F("powershell -EncodedCommand " + enc.decode())["rm_targets_reference"])

    def test_an_unparseable_command_is_a_refusal_not_an_allow(self):
        f = self.P("Remove-Item (")
        self.assertIn("does not parse", f.get("powershell_error", ""))

    def test_an_unparseable_NESTED_command_is_flagged(self):
        self.assertTrue(F("powershell -c " + DQ + "Remove-Item (" + DQ)["carrier_untranslated"])

    # ---- twins ---------------------------------------------------------------------
    def test_TWIN_a_read_is_not_a_write(self):
        f = self.P("Get-Content " + Q + REF + "/libraries/wares.xml" + Q)
        self.assertFalse(any(v is True for k, v in f.items() if k != "from_powershell"), f)

    def test_the_OUTER_shells_redirect_is_not_part_of_the_payload(self):
        """Friction replay: `powershell -Command "...; java -version 2>&1" 2>&1 | head`
        joined bash's own `2>&1` into the payload, PowerShell rejected it, and it ASKED."""
        f = F("powershell -NoProfile -Command " + DQ + "java -version 2>&1" + DQ
              + " 2>&1 | head -6")
        self.assertFalse(f["carrier_untranslated"])

    def test_TWIN_a_nested_read(self):
        f = F("powershell -c " + DQ + "Get-Content " + Q + REF + "/a.xml" + Q + DQ)
        self.assertFalse(f["rm_targets_reference"] or f["writes_reference"])


class TestHK1TranslationBudget(unittest.TestCase):
    """Review item 7: every translation had its own 20 s timeout, so five carriers could
    commit 100 s against a 30 s hook timeout -- and a timed-out hook does not block.
    Stubbed: each translation takes its FULL timeout (a wedged PowerShell)."""

    def setUp(self):
        import subprocess as sp
        import types
        self.clock, self.calls = [0.0], []

        def fake_run(args, **kw):
            self.calls.append(kw["timeout"])
            self.clock[0] += kw["timeout"]
            raise sp.TimeoutExpired(args, kw["timeout"])
        self.saved = (H.subprocess, H._clock)
        H.subprocess = types.SimpleNamespace(run=fake_run, TimeoutExpired=sp.TimeoutExpired,
                                             SubprocessError=sp.SubprocessError)
        H._clock = lambda: self.clock[0]
        H._PS_CACHE.clear()

    def tearDown(self):
        H.subprocess, H._clock = self.saved
        H._PS_CACHE.clear()

    def test_the_total_is_bounded_well_under_the_hook_timeout(self):
        cmd = " && ".join('pwsh -c "Get-Date %d"' % i for i in range(5))
        f = F(cmd)
        self.assertTrue(f["carrier_untranslated"])
        self.assertLessEqual(sum(self.calls), H._PS_BUDGET_S)
        self.assertLess(H._PS_BUDGET_S, 25)
        self.assertTrue(any("budget" in r for r in H._UNTRANSLATED), H._UNTRANSLATED)

    def test_the_budget_is_per_call(self):
        F('pwsh -c "Get-Date 1"')
        F('pwsh -c "Get-Date 2"')
        self.assertEqual(self.calls, [H._PS_CALL_CAP_S, H._PS_CALL_CAP_S])


class TestHK1UnknownCmdletMarker(unittest.TestCase):
    """ps_translate.ps1 hands an unmodelled, possibly-writing cmdlet to this side as
    `x4-unknown-cmdlet <Name> <args>`; only here are the roots known (review item 6)."""

    def test_a_protected_path_is_untranslated(self):
        self.assertTrue(F("x4-unknown-cmdlet Frob-Thing " + DQ + REF + "/a.xml" + DQ)
                        ["carrier_untranslated"])
        self.assertTrue(F("x4-unknown-cmdlet Set-Item " + DQ + SAVES + "/a" + DQ)
                        ["carrier_untranslated"])

    def test_TWIN_a_workspace_path_is_not(self):
        self.assertFalse(F("x4-unknown-cmdlet Frob-Thing " + DQ + TOOLKIT + "/x" + DQ)
                         ["carrier_untranslated"])
        self.assertFalse(F("Frob-Thing " + DQ + REF + "/a.xml" + DQ)["carrier_untranslated"])


class TestHK1ABashCommandIsNotPowerShell(unittest.TestCase):
    def test_a_bash_payload_is_not_marked_as_translated(self):
        """from_powershell only relabels the command in messages; a Bash payload that
        claimed it would misquote every reason."""
        self.assertFalse(F("ls")["from_powershell"])
        self.assertFalse(F("ls")["carrier_untranslated"])


class TestHK1FailsClosedWithoutPowerShell(unittest.TestCase):
    def test_no_powershell_is_a_refusal(self):
        import os
        saved = os.environ.get("X4_PWSH")
        os.environ["X4_PWSH"] = "x4-no-such-powershell-binary"
        H._PS_CACHE.clear()
        try:
            f = H.facts({"tool_name": "PowerShell", "tool_input": {"command": "Get-Date"}},
                        ROOTS)
            self.assertIn("no PowerShell", f.get("powershell_error", ""))
            self.assertTrue(F("powershell -c Get-Date")["carrier_untranslated"])
        finally:
            H._PS_CACHE.clear()
            if saved is None:
                os.environ.pop("X4_PWSH", None)
            else:
                os.environ["X4_PWSH"] = saved


def FC(command, cwd, **kw):
    """facts() with the payload's TOP-LEVEL `cwd`, where Claude Code and Codex put it."""
    payload = {"tool_input": {"command": command}}
    if cwd is not _NO_CWD:
        payload["cwd"] = cwd
    return H.facts(payload, ROOTS)


_NO_CWD = object()
#: The folder that HOLDS reference/ in these fixtures (TOOLKIT), and one that does not.
_REL_DEL = D + " -f reference/libraries/w.xml"
_REL_WRITE = "echo x > reference/libraries/w.xml"
_ELSEWHERE = "C:/work/other"


class TestARootVariableCdIsTheRoot(unittest.TestCase):
    """R2-P1 (pre-arc, v4.0.0 review): `cd "$X4_REFERENCE" && rm -rf libraries` was ALLOW while
    `rm -rf "$X4_REFERENCE/libraries"` was refused. cwd_track never mapped a root VARIABLE to
    its root, so the cd target stayed unresolved and every later relative operand reached no
    rule. A root variable names its root (ROOT_VARS) for a `cd` exactly as for an operand."""

    #: (the cd form, the same action spelled with the root's literal path)
    PAIRS = [
        ('cd "$X4_REFERENCE" && ' + D + ' -rf libraries', D + ' -rf "' + REF + '/libraries"'),
        ('cd "${X4_REFERENCE}/libraries" && ' + D + ' -f wares.xml', D + ' -f "' + REF + '/libraries/wares.xml"'),
        ("cd $X4_REFERENCE; echo x > libraries/w.xml", 'echo x > "' + REF + '/libraries/w.xml"'),
        ('cd "$X4_GAME" && ' + D + ' -rf extensions', D + ' -rf "' + GAME + '/extensions"'),
        ('pushd "$X4_PROFILE" && ' + D + ' -f content.xml', D + ' -f "' + PROF + '/content.xml"'),
    ]
    KEYS = ("rm_targets_reference", "writes_reference", "rm_in_x4_dir", "rm_in_profile",
            "rm_hits_game")          # the game pair names extensions/ wholesale (reviewer E)

    def test_a_cd_to_a_root_variable_judges_like_the_direct_form(self):
        for cd_form, direct in self.PAIRS:
            for cwd in (_ELSEWHERE, _NO_CWD):
                with self.subTest(cmd=cd_form, cwd=cwd):
                    a, b = FC(cd_form, cwd), FC(direct, cwd)
                    self.assertEqual({k: a[k] for k in self.KEYS}, {k: b[k] for k in self.KEYS})
                    self.assertTrue(any(a[k] for k in self.KEYS), cd_form)

    def test_TWIN_a_non_root_variable_or_an_unset_root_still_reaches_nothing(self):
        self.assertFalse(FC('cd "$BUILD_DIR" && ' + D + ' -rf libraries', _ELSEWHERE)["rm_targets_reference"])
        roots = dict(ROOTS, reference="")
        payload = {"tool_input": {"command": 'cd "$X4_REFERENCE" && ' + D + " -rf libraries"},
                   "cwd": _ELSEWHERE}
        self.assertFalse(H.facts(payload, roots)["rm_targets_reference"])

    def test_what_follows_the_variable_is_kept(self):
        self.assertTrue(FC('cd "$X4_REFERENCE/$SUB" && ' + D + ' -rf x', _ELSEWHERE)["rm_targets_reference"])
        self.assertFalse(FC('cd "${X4_REFERENCE}x" && ' + D + ' -rf libraries', _ELSEWHERE)["rm_targets_reference"])

    def test_TWIN_an_unset_root_is_not_the_session_directory(self):
        """With the game root unset, `cd "$X4_GAME"` goes somewhere unknowable -- it must not
        read as staying in the session dir (cwd TOOLKIT holds reference/ here)."""
        payload = {"tool_input": {"command": 'cd "$X4_GAME" && ' + D + " -rf reference/libraries"},
                   "cwd": TOOLKIT}
        self.assertFalse(H.facts(payload, dict(ROOTS, game=""))["rm_targets_reference"])
        payload["tool_input"]["command"] = D + " -rf reference/libraries"            # control
        self.assertTrue(H.facts(payload, dict(ROOTS, game=""))["rm_targets_reference"])

    def test_TWIN_an_assignment_in_the_command_wins_over_the_root(self):
        cmd = 'X4_REFERENCE=/c/tmp/x; cd "$X4_REFERENCE" && ' + D + " -rf libraries"
        self.assertFalse(FC(cmd, _ELSEWHERE)["rm_targets_reference"])


class TestLiftingTheReferenceDenyIsJudgedWhereItRuns(unittest.TestCase):
    """R2-F2 (v4.0.0 review): _lifts_reference_deny resolved an icacls operand with no cwd join
    and no root variable, and knew a fixed interpreter list -- so `icacls "$X4_REFERENCE" /reset
    /T`, `icacls reference /reset /T` from the folder above it and `"$X4_PYTHON"
    scripts/x4refguard.py remove` were ALLOW where they must ask."""

    def test_a_root_variable_operand_lifts(self):
        for cmd in ('icacls "$X4_REFERENCE" /reset /T', 'icacls "${X4_REFERENCE}/libraries" /reset',
                    'icacls "$X4_TOOLKIT" /reset /T'):          # TOOLKIT holds reference/ here
            with self.subTest(cmd=cmd):
                self.assertTrue(F(cmd)["lifts_reference_deny"], cmd)

    def test_a_relative_operand_resolves_against_the_cwd(self):
        self.assertTrue(FC("icacls reference /reset /T", TOOLKIT)["lifts_reference_deny"])
        self.assertTrue(FC("icacls . /reset /T", REF)["lifts_reference_deny"])
        self.assertTrue(FC('cd "$X4_REFERENCE" && icacls . /reset /T', _ELSEWHERE)["lifts_reference_deny"])

    def test_TWIN_the_same_relative_operand_elsewhere_or_not_recursive_does_not_lift(self):
        self.assertFalse(FC("icacls reference /reset /T", _ELSEWHERE)["lifts_reference_deny"])
        self.assertFalse(FC("icacls . /reset", TOOLKIT)["lifts_reference_deny"])
        self.assertFalse(F('icacls "$X4_GAME" /reset /T')["lifts_reference_deny"])  # a separate tree
        self.assertFalse(F('icacls "$X4_REFERENCE"')["lifts_reference_deny"])       # a read

    def test_a_runner_named_by_a_variable_or_a_launcher_lifts(self):
        for cmd in ('"$X4_PYTHON" scripts/x4refguard.py remove',
                    "${PY} scripts/x4refguard.py remove",
                    # a valued interpreter flag first: F10's reading of the words after an
                    # unknown name then names `utf8`, so only the variable-runner clause sees it
                    '"$X4_PYTHON" -X utf8 scripts/x4refguard.py remove',
                    "conda run -n x4 python scripts/x4refguard.py remove",
                    "pipx run x4refguard remove"):
            with self.subTest(cmd=cmd):
                self.assertTrue(F(cmd)["lifts_reference_deny"], cmd)

    def test_TWIN_a_variable_runner_that_does_not_remove_or_a_mention_does_not_lift(self):
        for cmd in ('"$X4_PYTHON" scripts/x4refguard.py status',
                    'echo "$X4_PYTHON" scripts/x4refguard.py remove',
                    '"$X4_PYTHON" scripts/other.py remove'):
            with self.subTest(cmd=cmd):
                self.assertFalse(F(cmd)["lifts_reference_deny"], cmd)


class TestRelativeOperandsResolveAgainstThePayloadCwd(unittest.TestCase):
    """Lane F (2026-10-02). facts() called cwd_track(c) with no base, so a RELATIVE operand
    with no preceding `cd` resolved to "" and reached NO path rule. MEASURED on the deployed
    guard, cwd = the folder holding reference/: `rm -f reference/...` and `echo x >
    reference/...` were ALLOW while the absolute spelling denied; a live Codex run
    overwrote the file. The payload's `cwd` is the directory the shell starts in."""

    def test_a_relative_delete_and_write_resolve_against_the_payload_cwd(self):
        self.assertTrue(FC(_REL_DEL, TOOLKIT)["rm_targets_reference"])
        self.assertTrue(FC(_REL_WRITE, TOOLKIT)["writes_reference"])
        # Windows-spelled, as Claude Code sends it on Windows.
        self.assertTrue(FC(_REL_DEL, TOOLKIT.replace("/", BS))["rm_targets_reference"])

    def test_the_absolute_and_cd_spellings_still_deny(self):
        self.assertTrue(FC(D + " -f " + REF + "/libraries/w.xml", TOOLKIT)["rm_targets_reference"])
        self.assertTrue(FC("cd " + TOOLKIT + " && " + _REL_DEL, _NO_CWD)["rm_targets_reference"])

    def test_TWIN_an_unrelated_cwd_resolves_elsewhere(self):
        self.assertFalse(FC(_REL_DEL, _ELSEWHERE)["rm_targets_reference"])
        self.assertFalse(FC(_REL_WRITE, _ELSEWHERE)["writes_reference"])

    def test_TWIN_no_relative_or_garbage_cwd_is_unchanged_from_before(self):
        for cwd in (_NO_CWD, "", None, "Modding/X4", "::", 5, ["C:/x"], {"a": 1}):
            with self.subTest(cwd=cwd):
                f = FC(_REL_DEL, cwd)
                self.assertFalse(f["rm_targets_reference"])
                self.assertFalse(FC(_REL_WRITE, cwd)["writes_reference"])

    def test_TWIN_a_cd_wins_over_the_payload_cwd(self):
        self.assertFalse(FC("cd " + _ELSEWHERE + " && " + _REL_DEL, TOOLKIT)["rm_targets_reference"])
        # ...and a relative cd is joined ONTO the payload cwd.
        self.assertTrue(FC("cd dev && " + D + " -f ../reference/libraries/w.xml", TOOLKIT)
                        ["rm_targets_reference"])

    def test_a_carrier_runs_in_the_session_cwd_when_nothing_changes_directory(self):
        self.assertTrue(FC("bash -c '" + _REL_DEL + "'", TOOLKIT)["rm_targets_reference"])

    def test_TWIN_a_carrier_after_a_directory_change_is_not_seeded(self):
        """A carried command is walked on its own and cannot see the `cd` around it, so
        after any directory change it keeps the old "unknowable" -- never the session cwd,
        which is no longer where it runs. One case per clause of the directory-change test."""
        for move in ("cd " + _ELSEWHERE, "pushd " + _ELSEWHERE, "popd"):
            with self.subTest(move=move):
                self.assertFalse(FC(move + " && bash -c '" + _REL_DEL + "'", TOOLKIT)
                                 ["rm_targets_reference"])


class TestTheSeedIsNarrowedWhereTheReplayFoundFalsePositives(unittest.TestCase):
    """Lane F's history replay (OLD vs NEW per command, each with its own transcript cwd)
    found the seed firing on work that touched no protected file. Each narrowing below is
    pinned in BOTH directions: the false positive stays gone, the real case still fires."""

    def test_an_unresolved_cd_from_the_seed_is_unknowable(self):
        """`cd "$X4_TOOLKIT" && sed -i ... scripts/x` from the game root read as a sed -i of
        the GAME (hard deny): the unresolved target was joined onto the seed."""
        self.assertFalse(FC("cd \"$NOPE\" && sed -i s/a/b/ scripts/x.sh", GAME)
                         ["sed_i_in_game_or_profile"])
        self.assertFalse(FC("cd \"$NOPE\" && " + D + " -rf extensions", GAME)["rm_in_x4_dir"])

    def test_TWIN_after_an_absolute_cd_the_sticky_join_stands(self):
        """The 2026-09-02 rule: `cd <game> && cd "$NOPE" && rm -rf extensions` stays under
        the root we last knew about -- with or without a session seed."""
        cmd = "cd \"" + GAME + "\" && cd \"$NOPE\" && " + D + " -rf extensions"
        self.assertTrue(FC(cmd, _ELSEWHERE)["rm_in_x4_dir"])
        self.assertTrue(FC(cmd, _NO_CWD)["rm_in_x4_dir"])

    def test_TWIN_an_unresolved_but_absolute_cd_target_is_joined(self):
        self.assertTrue(FC("cd \"" + GAME + "/$SUB\" && " + D + " -rf extensions", _ELSEWHERE)
                        ["rm_in_x4_dir"])

    def test_popd_restores_whether_the_directory_is_the_seed(self):
        cmd = ("pushd \"" + GAME + "/extensions\" && popd && cd \"$NOPE\" && "
               + D + " -rf extensions")
        self.assertFalse(FC(cmd, GAME)["rm_in_x4_dir"])

    def test_the_bare_python_rule_keeps_its_own_base(self):
        """`seeded` is for facts()'s path rules only; the bare-python rule's stand-in base
        keeps its pre-lane join (an unresolved cd from a toolkit cwd still counts)."""
        self.assertTrue(FC("cd \"$NOPE\" && python -m pytest -q", TOOLKIT + "/tools/x4validate")
                        ["bare_python_on_project_code"])

    def test_parse_debris_is_not_joined_onto_the_seed(self):
        """`2>/dev/null` read as a sed -i target and an escaped quote as a path, both under
        the game root -- hard denies on probe harnesses."""
        self.assertFalse(FC("sed -i s/a/b/ /dev/null 2>/dev/null", GAME)["sed_i_in_game_or_profile"])
        self.assertFalse(FC("sed -i s/a/b/ " + BS + DQ + "f.xml" + BS + DQ, GAME)
                         ["sed_i_in_game_or_profile"])

    def test_TWIN_a_clean_relative_operand_from_the_seed_still_fires(self):
        self.assertTrue(FC("sed -i s/a/b/ libraries/x.xml", GAME)["sed_i_in_game_or_profile"])
        self.assertTrue(FC("echo x > notes.txt", GAME)["redirect_truncate_into_game_or_profile"])

    def test_a_windows_device_name_is_not_a_file_in_the_directory(self):
        self.assertFalse(FC("echo x > nul", GAME)["redirect_truncate_into_game_or_profile"])
        self.assertFalse(FC("echo x > NUL:", GAME)["redirect_truncate_into_game_or_profile"])

    def test_git_wipe_does_not_ask_from_the_seed_alone(self):
        """An ASK outside the profile is a new prompt (user, 2026-10-02): `git clean -fdx`
        names no path, so with the seed every one from the game root would ask."""
        self.assertFalse(FC("git clean -fdx", GAME)["git_wipes_x4_dir"])
        self.assertTrue(FC("cd \"" + GAME + "\" && git clean -fdx", _ELSEWHERE)["git_wipes_x4_dir"])

    def test_an_unknown_cmdlet_does_not_ask_from_the_seed_alone(self):
        """ps_translate's `x4-unknown-cmdlet` ends in an ASK; its relative arguments are not
        known to be paths (2 historical rows: a user-defined PowerShell function)."""
        self.assertFalse(FC("x4-unknown-cmdlet Copy-Thing libraries/x.xml", GAME)
                         ["carrier_untranslated"])
        self.assertTrue(FC("x4-unknown-cmdlet Copy-Thing \"" + GAME + "/libraries/x.xml\"", _ELSEWHERE)
                        ["carrier_untranslated"])



class TestJQ1BareGitWipeFromAnX4SessionDirIsADeny(unittest.TestCase):
    """Plan 3 decision J-Q1 (2026-10-02): a bare `git clean` with -x/-X/-d, or `git reset
    --hard`, whose SESSION cwd is the game folder or another X4 dir is a DENY with a reason,
    never a prompt. The game-root repo ignores everything but its own files (`.gitignore` = `*`),
    so `git clean -fdx` there deletes the installation's untracked files. Lane F left it
    UNSEEDED (an ask would be a new prompt); a deny reaches the agent, not the user.
    The explicit-folder forms (`cd <root> &&`, `git -C <root>`) keep their ASK."""
    FACT = "git_wipe_from_session_dir"

    def test_bare_clean_with_x_or_d_or_X_from_the_game_root_fires(self):
        for c in ("git clean -fdx", "git clean -fx", "git clean -fd", "git clean -fX",
                  "git clean -f -x", "git clean --force -d"):
            with self.subTest(c=c):
                self.assertTrue(FC(c, GAME)[self.FACT], c)

    def test_bare_reset_hard_from_the_game_root_fires(self):
        self.assertTrue(FC("git reset --hard", GAME)[self.FACT])
        self.assertTrue(FC("git reset --hard HEAD~1", GAME)[self.FACT])

    def test_every_x4_dir_counts_not_only_the_game(self):
        for d in (PROF, REF, TOOLKIT, TOOLKIT + "/dev/mymod"):
            with self.subTest(d=d):
                self.assertTrue(FC("git clean -fdx", d)[self.FACT], d)

    # --- one falsification twin per clause ---
    def test_TWIN_a_cwd_outside_every_x4_dir_does_not_fire(self):
        self.assertFalse(FC("git clean -fdx", _ELSEWHERE)[self.FACT])
        self.assertFalse(FC("git reset --hard", _ELSEWHERE)[self.FACT])

    def test_TWIN_no_cwd_in_the_payload_does_not_fire(self):
        self.assertFalse(FC("git clean -fdx", _NO_CWD)[self.FACT])

    def test_TWIN_clean_without_x_X_or_d_does_not_fire(self):
        """`git clean -f` removes untracked, NOT-ignored files; in a whitelist repo there are
        none. The decision names -x/-d (and -X removes only the ignored ones: all of them)."""
        self.assertFalse(FC("git clean -f", GAME)[self.FACT])

    def test_TWIN_a_dry_run_or_an_unforced_clean_does_not_fire(self):
        self.assertFalse(FC("git clean -n -dx", GAME)[self.FACT])
        self.assertFalse(FC("git clean --dry-run -fdx", GAME)[self.FACT])
        self.assertFalse(FC("git clean -dx", GAME)[self.FACT])

    def test_TWIN_a_soft_or_mixed_reset_does_not_fire(self):
        self.assertFalse(FC("git reset --soft HEAD~1", GAME)[self.FACT])
        self.assertFalse(FC("git reset HEAD~1", GAME)[self.FACT])

    def test_TWIN_the_explicit_folder_form_still_ASKS_and_is_not_this_deny(self):
        cmd = "cd " + DQ + GAME + DQ + " && git clean -fdx"
        for cwd in (_ELSEWHERE, GAME):
            with self.subTest(cwd=cwd):
                f = FC(cmd, cwd)
                self.assertTrue(f["git_wipes_x4_dir"], cwd)
                self.assertFalse(f[self.FACT], cwd)
        f = FC("git -C " + DQ + GAME + DQ + " clean -fdx", GAME)
        self.assertTrue(f["git_wipes_x4_dir"])
        self.assertFalse(f[self.FACT])

    def test_a_named_wipe_ELSEWHERE_in_the_command_does_not_hide_a_bare_one(self):
        """R2-F5 (v4.0.0 review): the exclusion was command-wide, so `git -C <mods> clean -fdx;
        git clean -fdx` from the game root turned the bare segment's DENY into the named one's
        ASK. The exclusion is per SEGMENT: only a segment that names its own folder asks."""
        for cmd in ("git -C " + DQ + TOOLKIT + "/dev" + DQ + " clean -fdx; git clean -fdx",
                    "git clean -fdx; git -C " + DQ + PROF + DQ + " clean -fdx"):
            with self.subTest(cmd=cmd):
                f = FC(cmd, GAME)
                self.assertTrue(f[self.FACT], cmd)
                self.assertTrue(f["git_wipes_x4_dir"], cmd)

    def test_TWIN_a_cd_AWAY_from_the_x4_session_dir_does_not_fire(self):
        self.assertFalse(FC("cd " + DQ + _ELSEWHERE + DQ + " && git clean -fdx", GAME)[self.FACT])


class TestP2GitStashAllIsAWipe(unittest.TestCase):
    """v4.0.0 review P2 (pre-arc, user chose to fix): `git stash --all` / `-a` stashes the
    IGNORED files and then DELETES them from the working tree -- in the game-root repo, whose
    `.gitignore` is a whitelist (`*`), that is the installation itself. It is the same wipe as
    `git clean -fdx` with a stash ref on the side, so it takes the same two verdicts: DENY when
    bare from an X4 session dir (J-Q1), ASK when a folder is named.
    `-u` / `--include-untracked` removes untracked NOT-ignored files only -- the reach of a
    plain `git clean -f` -- so it takes exactly that rule's verdict: the named ASK, no deny."""
    FACT = "git_wipe_from_session_dir"
    ALL = ("git stash -a", "git stash --all", "git stash push -a", "git stash push --all",
           "git stash save -a", "git stash save --all wip", "git stash -ka", "git stash -am wip",
           "git stash push -m wip --all", "git stash push -q -a -- libraries",
           "git -c core.x=y stash --all")

    def test_bare_stash_all_from_the_game_root_is_the_deny(self):
        for c in self.ALL:
            with self.subTest(c=c):
                self.assertTrue(FC(c, GAME)[self.FACT], c)

    def test_bare_stash_all_from_every_x4_dir(self):
        for d in (PROF, REF, TOOLKIT, TOOLKIT + "/dev/mymod"):
            with self.subTest(d=d):
                self.assertTrue(FC("git stash --all", d)[self.FACT], d)

    def test_a_named_folder_ASKS_and_is_not_the_deny(self):
        for cmd in ("git -C " + DQ + GAME + DQ + " stash -a",
                    "cd " + DQ + GAME + DQ + " && git stash push --all"):
            for cwd in (_ELSEWHERE, GAME):
                with self.subTest(cmd=cmd, cwd=cwd):
                    f = FC(cmd, cwd)
                    self.assertTrue(f["git_wipes_x4_dir"], cmd)
                    self.assertFalse(f[self.FACT], cmd)

    def test_include_untracked_takes_the_plain_clean_f_verdict(self):
        """Named: ASK (as `git -C <game> clean -f` does). Bare: no deny (as `git clean -f`)."""
        for c in ("stash -u", "stash --include-untracked", "stash push -u", "stash -ku"):
            with self.subTest(c=c):
                self.assertTrue(FC("git -C " + DQ + GAME + DQ + " " + c, _ELSEWHERE)
                                ["git_wipes_x4_dir"], c)
                self.assertFalse(FC("git " + c, GAME)[self.FACT], c)

    # --- one falsification twin per clause ---
    def test_TWIN_a_plain_stash_is_not_a_wipe(self):
        """Clause: -a/--all. A plain stash touches tracked files only, all recoverable."""
        for c in ("git stash", "git stash push", "git stash -k", "git stash push -m wip",
                  "git stash save wip", "git stash -p"):
            with self.subTest(c=c):
                self.assertFalse(FC(c, GAME)[self.FACT], c)
                self.assertFalse(FC("git -C " + DQ + GAME + DQ + c[3:], _ELSEWHERE)
                                 ["git_wipes_x4_dir"], c)

    def test_TWIN_a_stash_subcommand_other_than_push_is_not_a_wipe(self):
        """Clause: the action is push/save. `list -a`-shaped spellings included, so the flag
        clause alone cannot be what decides."""
        for c in ("git stash list", "git stash pop", "git stash apply", "git stash show -p",
                  "git stash drop", "git stash branch a", "git stash list --all",
                  "git stash show -a"):
            with self.subTest(c=c):
                self.assertFalse(FC(c, GAME)[self.FACT], c)
                self.assertFalse(FC("git -C " + DQ + GAME + DQ + c[3:], _ELSEWHERE)
                                 ["git_wipes_x4_dir"], c)

    def test_TWIN_a_message_that_reads_like_the_flag_is_not_the_flag(self):
        for c in ("git stash -m -a", "git stash push --message --all", "git stash -m all"):
            with self.subTest(c=c):
                self.assertFalse(FC(c, GAME)[self.FACT], c)

    def test_TWIN_a_negated_all_is_not_a_wipe(self):
        self.assertFalse(FC("git stash --all --no-all", GAME)[self.FACT])

    def test_TWIN_stash_all_from_a_non_x4_dir_does_not_fire(self):
        """Clause: the session dir is an X4 dir."""
        for c in self.ALL:
            with self.subTest(c=c):
                self.assertFalse(FC(c, _ELSEWHERE)[self.FACT], c)
        self.assertFalse(FC("git stash -a", _NO_CWD)[self.FACT])

class TestG2ARootVariableOperandIsItsRoot(unittest.TestCase):
    """FX-G2 items 1-2 (v4.0.0 delta review): a WRITE through a root variable was never
    substituted -- only `cd` (R2-P1) and icacls were -- and the write rules are not conservative
    about an unresolved operand. MEASURED, 34 of 40 probes ALLOWED: `echo x >
    "$X4_REFERENCE/libraries/wares.xml"`, `cp f "$X4_TOOLKIT/reference/..."`, the same into
    "$X4_PROFILE", and every spelling of a write to "$X4_TOOLKIT/x4-paths.env". Now every
    operand's leading root variable is its root (prep -> subst_root_var), so the variable and
    the literal spelling judge alike."""

    KEYS = ("writes_reference", "rm_targets_reference", "writes_profile", "rm_in_profile",
            "rm_hits_game", "rm_in_x4_dir", "sed_i_in_game_or_profile",
            "copy_into_game_or_profile", "redirect_truncate_into_game_or_profile")
    VARS = {"X4_REFERENCE": REF, "X4_PROFILE": PROF, "X4_GAME": GAME, "X4_TOOLKIT": TOOLKIT}
    VERBS = ("echo x > {}", "echo x >> {}", "cp a {}", "mv /x/y {}", "echo x | tee {}",
             "sed -i s/a/b/ {}", D + " -rf {}", "mv {} /x/y")

    def test_every_root_variable_and_verb_judges_like_the_literal(self):
        for var, lit in self.VARS.items():
            for tail in ("", "/libraries/wares.xml", "/reference/libraries/w.xml"):
                for v in self.VERBS:
                    for spell in ("$" + var, "${" + var + "}"):
                        a = F(v.format(DQ + spell + tail + DQ))
                        b = F(v.format(DQ + lit + tail + DQ))
                        with self.subTest(var=spell, tail=tail, verb=v):
                            self.assertEqual({k: a[k] for k in self.KEYS},
                                             {k: b[k] for k in self.KEYS})

    def test_the_measured_bypasses_are_refused(self):
        for cmd in ('echo x > "$X4_REFERENCE/libraries/wares.xml"',
                    'cp f "$X4_REFERENCE/libraries/wares.xml"',
                    'echo x > "$X4_TOOLKIT/reference/libraries/wares.xml"',
                    'sed -i s/a/b/ "${X4_REFERENCE}/libraries/wares.xml"'):
            with self.subTest(cmd=cmd):
                self.assertTrue(F(cmd)["writes_reference"], cmd)
        self.assertTrue(F('echo x > "$X4_PROFILE/content.xml"')["writes_profile"])
        self.assertTrue(F(D + ' -rf "$X4_GAME"')["rm_hits_game"])

    def test_the_config_pass_sees_every_root_variable_spelling(self):
        """protect-bash.sh's config pass: the config IN PLACE OF the reference root, every
        other root kept. Sending the config ALONE left `$X4_TOOLKIT` unsubstituted."""
        cfg = TOOLKIT + "/x4-paths.env"
        roots = dict(ROOTS, reference=cfg, config=cfg)
        for cmd in ('echo X4_REFERENCE=/x > "$X4_TOOLKIT/x4-paths.env"',
                    'echo a >> "${X4_TOOLKIT}/x4-paths.env"',
                    'cp /dev/null "$X4_TOOLKIT/x4-paths.env"',
                    'cd "$X4_TOOLKIT" && echo a > x4-paths.env',
                    'echo a > "$X4_CONFIG"',
                    D + ' "$X4_TOOLKIT/x4-paths.env"'):
            with self.subTest(cmd=cmd):
                f = H.facts({"tool_input": {"command": cmd}, "cwd": _ELSEWHERE}, roots)
                self.assertTrue(f["writes_reference"] or f["rm_targets_reference"], cmd)
        # TWIN: the config ALONE (the old pass) cannot resolve the toolkit variable.
        f = H.facts({"tool_input": {"command": 'echo a > "$X4_TOOLKIT/x4-paths.env"'}},
                    {"reference": cfg})
        self.assertFalse(f["writes_reference"])

    # --- one falsification twin per clause ---
    def test_TWIN_an_unconfigured_root_is_not_substituted(self):
        """Clause: the root is configured. Unset, the token stays unresolved as before."""
        f = H.facts({"tool_input": {"command": 'echo x > "$X4_REFERENCE/libraries/w.xml"'}},
                    dict(ROOTS, reference=""))
        self.assertFalse(f["writes_reference"])

    def test_TWIN_only_a_LEADING_root_variable_is_its_root(self):
        """Clause: the variable leads the token. `${X4_REFERENCE}x` is a sibling, and a root
        variable in the MIDDLE of a path is not the root."""
        self.assertFalse(F('echo x > "${X4_REFERENCE}x/w.xml"')["writes_reference"])
        self.assertFalse(F('echo x > "/c/build/$X4_REFERENCE/w.xml"')["writes_reference"])

    def test_TWIN_a_non_root_variable_is_not_a_root(self):
        """Clause: the name is in ROOT_VARS."""
        self.assertFalse(F('echo x > "$X4_REFERENCES/w.xml"')["writes_reference"])
        self.assertFalse(F('echo x > "$BUILD/w.xml"')["writes_reference"])

    def test_TWIN_an_assignment_in_the_command_wins(self):
        self.assertFalse(F('X4_REFERENCE=/c/tmp/x; echo x > "$X4_REFERENCE/w.xml"')
                         ["writes_reference"])

    def test_TWIN_a_READ_through_the_variable_is_not_a_write(self):
        self.assertFalse(F('cat "$X4_REFERENCE/libraries/wares.xml" > out.txt')["writes_reference"])


class TestG2WindowsPathAliases(unittest.TestCase):
    """FX-G2 item 3 (v4.0.0 delta review): Windows opens `x.`, `x `, `x::$DATA`, `x:alt`,
    `dir::$INDEX_ALLOCATION` and `dir./` as the real x / dir (MEASURED 2026-10-05 on NTFS:
    Python wrote INTO x4-paths.env through each), and an 8.3 short name as its long name --
    while norm() compared every one equal to nothing."""

    def test_trailing_dots_spaces_and_stream_suffixes_are_the_file(self):
        base = H.norm(REF + "/libraries/wares.xml")
        for alias in (REF + "./libraries/wares.xml", REF + " /libraries/wares.xml",
                      REF + "/libraries/wares.xml.", REF + "/libraries/wares.xml. . ",
                      REF + "/libraries/wares.xml::$DATA", REF + "/libraries/wares.xml:alt",
                      REF + "::$INDEX_ALLOCATION/libraries/wares.xml",
                      REF.replace("/", BS) + "." + BS + "libraries" + BS + "wares.xml"):
            with self.subTest(alias=alias):
                self.assertEqual(H.norm(alias), base)

    def test_the_rules_see_the_aliases(self):
        self.assertTrue(F('echo x > "' + REF + './libraries/w.xml"')["writes_reference"])
        self.assertTrue(F(D + ' -rf "' + REF + '."')["rm_targets_reference"])
        self.assertTrue(F(D + ' -rf "' + GAME + '. "')["rm_hits_game"])
        self.assertTrue(F("cp a '" + REF + "/w.xml::$DATA'")["writes_reference"])

    # --- one falsification twin per clause ---
    def test_TWIN_dots_INSIDE_a_name_and_dot_segments_are_kept(self):
        """Clause: TRAILING only. `a.b`, `reference..old` and `...` are other names."""
        self.assertEqual(H.norm("/c/a.b/x"), "/c/a.b/x")
        self.assertFalse(F('echo x > "' + REF + '..old/w.xml"')["writes_reference"])
        self.assertEqual(H.norm("/c/a/.../b"), "/c/a/.../b")
        self.assertEqual(H.norm("/c/a/./b/../d"), "/c/a/d")

    def test_TWIN_a_url_scheme_colon_is_not_a_stream(self):
        """Clause: the colon is IN a component, not a `scheme://`."""
        self.assertEqual(H.norm("https://a/b"), "https://a/b")

    def test_TWIN_a_sibling_is_still_not_the_root(self):
        self.assertFalse(F('echo x > "' + REF + 'x/w.xml"')["writes_reference"])

    @unittest.skipUnless(os.name == "nt", "8.3 short names exist only on Windows")
    def test_an_8dot3_short_name_is_its_long_name(self):
        import ctypes
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            buf = ctypes.create_unicode_buffer(32768)
            # Exercise a short-name ancestor even when the caller's TEMP is long.
            n = ctypes.windll.kernel32.GetShortPathNameW(td, buf, 32768)
            self.assertTrue(0 < n < 32768, "native temporary parent must resolve")
            longdir = os.path.join(buf.value, "A Long Folder Name For Short")
            os.makedirs(longdir)
            # TEMP itself can carry an 8.3 ancestor (Windows CI uses RUNNER~1).
            # Obtain the expected LONG fixture independently of H.long_name.
            n = ctypes.windll.kernel32.GetLongPathNameW(longdir, buf, 32768)
            self.assertTrue(0 < n < 32768, "native long fixture path must resolve")
            longdir = buf.value
            n = ctypes.windll.kernel32.GetShortPathNameW(longdir, buf, 32768)
            self.assertTrue(0 < n < 32768, "native short fixture path must resolve")
            sdir = buf.value
            if "~" not in os.path.basename(sdir):
                self.skipTest("8.3 name generation is disabled on this volume")
            # the existing prefix resolves; a tail that does not exist yet is kept
            got = H.long_name(sdir + BS + "new" + BS + "f.xml")
            self.assertEqual(H.norm(got), H.norm(longdir + "/new/f.xml"))
            roots = dict(ROOTS, reference=longdir.replace(BS, "/"))
            payload = {"tool_input": {"command": 'echo x > "' + sdir.replace(BS, "/") + '/w.xml"'}}
            self.assertTrue(H.facts(payload, roots)["writes_reference"])
            # TWIN: a path with no `~<digit>` is returned untouched (no syscall)
            self.assertEqual(H.long_name(longdir + "/x"), longdir + "/x")

    def test_short_alias_comparison_peels_prefixes_and_keeps_unknown_tails(self):
        """Controlled native API oracle: prefix composition and partial paths on every OS."""
        import ctypes
        from types import SimpleNamespace
        from unittest.mock import patch
        short = "C:/PROGRA~1/Mod"
        long = "C:/Program Files/Mod"
        def expand(head, buf, size):
            if head.replace(BS, "/").lower() != short.lower():
                return 0
            buf.value = long
            return len(long)
        fake = SimpleNamespace(kernel32=SimpleNamespace(GetLongPathNameW=expand))
        tail = "/new/$pending"
        forms = [short, "FileSystem::" + short,
                 "Microsoft.PowerShell.Core" + BS + "FileSystem::" + short,
                 BS * 2 + "?" + BS + short,
                 BS * 2 + "." + BS + short,
                 "FileSystem::" + BS * 2 + "?" + BS + short]
        with patch.object(H.os, "name", "nt"), patch.object(ctypes, "windll", fake, create=True):
            for path in forms:
                with self.subTest(path=path):
                    self.assertEqual(H.norm(path + tail), H.norm(long + tail))
                    self.assertTrue(H.under(path + tail, long))
                    self.assertFalse(H.under(path + tail, long + "/sibling"))


class TestG2SubstitutionInsideArithmetic(unittest.TestCase):
    """FX-G2 item 4 (v4.0.0 delta review): `$((` was stepped over WHOLE, so a substitution
    inside arithmetic -- which bash EXPANDS before evaluating -- reached no rule, at the top
    level and in an expanding heredoc body (MEASURED: 6 of 14 probes ALLOWED past the game
    hard block). `$((cmd) )` is a command substitution holding a subshell, not arithmetic."""

    BT = chr(96)
    CASES = ("echo $(( $(" + DEL_GAME + ") + 0 ))",
             "x=$(( " + chr(96) + DEL_GAME + chr(96) + " + 0 ))",
             "echo $(( 1 + $(( $(" + DEL_GAME + ") )) ))",
             "echo $((" + DEL_GAME + ") )",
             "cat > n.txt <<EOF\n$(( $(" + DEL_GAME + ") + 0 ))\nEOF",
             "cat > n.txt <<EOF\n$(( " + chr(96) + DEL_GAME + chr(96) + " ))\nEOF")

    def test_a_substitution_inside_arithmetic_reaches_the_hard_block(self):
        for c in self.CASES:
            with self.subTest(c=c):
                self.assertTrue(F(c)["rm_hits_game"], c)

    def test_the_helper_tells_arithmetic_from_a_subshell(self):
        self.assertEqual(H._dollar_double_paren("$(( 1 + 2 ))", 0)[:2], ("arith", " 1 + 2 "))
        self.assertEqual(H._dollar_double_paren("$(((1+2)*3))", 0)[:2], ("arith", "(1+2)*3"))
        self.assertEqual(H._dollar_double_paren("$((echo hi) )", 0)[:2], ("subst", "(echo hi) "))
        self.assertEqual(H._dollar_double_paren("$(( 1", 0)[0], "")

    # --- one falsification twin per clause ---
    def test_TWIN_plain_arithmetic_is_still_not_a_command(self):
        """Clause: only a SUBSTITUTION inside it runs. `1 << 2` is not a heredoc or a write."""
        for c in ("echo $(( 1 + 2 ))", "echo $(( 1 << 2 ))", "echo $(( x > 5 ? 1 : 0 ))"):
            with self.subTest(c=c):
                self.assertEqual(H.substitutions(c), [])
                self.assertFalse(F(c)["rm_hits_game"])

    def test_TWIN_single_quotes_and_a_quoted_heredoc_stay_literal(self):
        """Clause: the expansion actually happens."""
        self.assertFalse(F("echo '$(( $(" + DEL_GAME.replace(DQ, "") + ") ))'")["rm_hits_game"])
        self.assertFalse(F("cat > n.txt <<'EOF'\n$(( $(" + DEL_GAME + ") ))\nEOF")["rm_hits_game"])


class TestG2GitLongOptionAbbreviations(unittest.TestCase):
    """FX-G2 item 5 (v4.0.0 delta review): git accepts any unambiguous PREFIX of a long option,
    and the wipe rules matched only the full spelling. MEASURED in a scratch repo: `git stash
    --a` deleted an ignored file, `git clean --fo -X` another, `--dry` is a dry run and `--mes
    -a` a message reading `-a`."""
    FACT = "git_wipe_from_session_dir"

    def test_an_abbreviated_destructive_option_is_the_option(self):
        for c in ("git stash --al", "git stash --a", "git stash push --al", "git reset --har",
                  "git reset --ha HEAD", "git clean --fo -dx", "git clean --forc -x"):
            with self.subTest(c=c):
                self.assertTrue(FC(c, GAME)[self.FACT], c)
        self.assertTrue(FC("git -C " + DQ + GAME + DQ + " stash --incl", _ELSEWHERE)
                        ["git_wipes_x4_dir"])

    # --- one falsification twin per clause ---
    def test_TWIN_an_abbreviated_DRY_RUN_or_NEGATION_still_counts(self):
        """Clause: the protective options abbreviate too -- or the twin of the rule is wrong."""
        for c in ("git clean -fdx --dry", "git clean -fdx --d", "git stash --al --no-al",
                  "git stash --a --no-a"):
            with self.subTest(c=c):
                self.assertFalse(FC(c, GAME)[self.FACT], c)

    def test_TWIN_an_abbreviated_MESSAGE_consumes_its_value(self):
        for c in ("git stash --mes -a", "git stash push --m -a"):
            with self.subTest(c=c):
                self.assertFalse(FC(c, GAME)[self.FACT], c)
        self.assertTrue(FC("git stash --mes=wip -a", GAME)[self.FACT])   # `=` carries it

    def test_TWIN_a_non_prefix_or_bare_dashes_is_not_the_option(self):
        """Clause: a PREFIX of the option's name, at least one letter long."""
        for c in ("git reset --mixed", "git reset --", "git stash --", "git stash --keep",
                  "git stash --alx", "git reset --hardx"):
            with self.subTest(c=c):
                self.assertFalse(FC(c, GAME)[self.FACT], c)
        self.assertFalse(H._git_long("--", "--all"))
        self.assertTrue(H._git_long("--a", "--all"))


class TestG2MoveToTrashIsADelete(unittest.TestCase):
    """FX-G2 item 7 (v4.0.0 delta review, reviewer D-1): core.md tells Linux/macOS agents to
    delete through the Trash, and `gio trash '<X4_GAME>'`, `kioclient5 move <game> trash:/` and a
    Finder `delete` through osascript were ALLOWED. A move to the trash is a delete of its
    operands, with rm's verdicts."""

    GAME_FORMS = ("gio trash '{p}'", "gio trash -f '{p}'", "gio remove '{p}'",
                  "gio trash 'file://{p}'", "gio move '{p}' trash:///", "trash-put '{p}'",
                  "trash '{p}'", "gvfs-trash '{p}'", "kioclient5 move '{p}' trash:/",
                  "kioclient move '{p}' trash:/",
                  "osascript -e 'tell application " + DQ + "Finder" + DQ + " to delete POSIX file "
                  + DQ + "{p}" + DQ + "'")

    def test_every_trash_form_judges_like_rm(self):
        keys = ("rm_hits_game", "rm_targets_reference", "rm_saves", "rm_in_profile", "rm_in_x4_dir")
        for form in self.GAME_FORMS:
            for p in (GAME, GAME + "/extensions", REF + "/a.xml", PROF + "/save/s.xml.gz",
                      GAME + "/extensions/mymod"):
                with self.subTest(form=form, p=p):
                    a, b = F(form.format(p=p)), F(D + " -rf '" + p + "'")
                    self.assertEqual({k: a[k] for k in keys}, {k: b[k] for k in keys})
                    self.assertTrue(any(a[k] for k in keys))

    # --- one falsification twin per clause ---
    def test_TWIN_a_non_deleting_subcommand_is_not_a_delete(self):
        """Clause: the subcommand trashes/removes (gio, kioclient)."""
        for c in ("gio list '" + GAME + "'", "gio info '" + REF + "/a.xml'",
                  "gio copy '" + REF + "/a.xml' /c/x/", "kioclient5 copy '" + REF + "/a.xml' /c/x/",
                  "kioclient5 cat '" + REF + "/a.xml'", "gio trash --empty"):
            with self.subTest(c=c):
                f = F(c)
                self.assertFalse(f["rm_targets_reference"] or f["rm_hits_game"] or f["rm_in_x4_dir"], c)

    def test_TWIN_a_move_NOT_into_the_trash_is_not_this_rule(self):
        """Clause: the destination is `trash:`."""
        self.assertEqual(H.trash_paths("gio move '" + GAME + "' /c/elsewhere"), [])
        self.assertEqual(H.trash_paths("kioclient5 move '" + GAME + "' /c/elsewhere"), [])

    def test_TWIN_osascript_without_delete_names_nothing(self):
        """Clause: the AppleScript says `delete`."""
        self.assertEqual(H.trash_paths("osascript -e 'tell application " + DQ + "Finder" + DQ
                                       + " to open POSIX file " + DQ + GAME + DQ + "'"), [])

    def test_TWIN_a_trash_outside_every_root_is_silent(self):
        f = F("gio trash /c/elsewhere/x")
        self.assertFalse(f["rm_in_x4_dir"] or f["rm_hits_game"] or f["rm_targets_reference"])


_NL = chr(10)


class TestE1HeredocQuoteStateCrossesLines(unittest.TestCase):
    """Reviewer E1 (pre-arc): the heredoc scan judged each line as if it began unquoted, so a
    `<<WORD` on the 2nd line of a multi-line QUOTED string opened a skip region that never
    closed -- `rm -rf <game>` on the next line was ALLOWED past the hard block."""

    def test_a_marker_inside_a_multiline_quote_hides_nothing(self):
        for q_open, q_close in ((DQ, DQ), (Q, Q)):
            for tail, key in ((DEL_GAME, "rm_hits_game"),
                              (D + ' -rf "' + REF + '/libraries"', "rm_targets_reference"),
                              ('echo x > "' + REF + '/a.xml"', "writes_reference")):
                c = "git commit -m " + q_open + "notes" + _NL + "use cat <<EOF here" + q_close + _NL + tail
                with self.subTest(q=q_open, tail=tail):
                    self.assertTrue(F(c)[key], c)

    # --- one falsification twin per clause ---
    def test_TWIN_a_real_heredoc_after_a_CLOSED_multiline_quote_is_still_data(self):
        """Clause: the quote is still OPEN where `<<` stands."""
        c = 'echo "a' + _NL + 'b"' + _NL + "cat > n.md <<EOF" + _NL + DEL_GAME + _NL + "EOF"
        self.assertFalse(F(c)["rm_hits_game"])

    def test_TWIN_a_quote_in_a_COMMENT_opens_nothing(self):
        """Clause: the quote is CODE. `# it's` must not swallow the next heredoc opener."""
        c = "# it's" + _NL + "cat > n.md <<EOF" + _NL + DEL_GAME + _NL + "EOF"
        self.assertFalse(F(c)["rm_hits_game"])

    def test_two_heredocs_on_one_line_are_read_in_order(self):
        c = ("cat > a.md <<A; cat > b.md <<B" + _NL + "x" + _NL + "A" + _NL + "it's" + _NL
             + "B" + _NL + DEL_GAME)
        self.assertTrue(F(c)["rm_hits_game"])
        self.assertEqual([d[1] for d in H._heredoc_walk(c)[0]], ["A", "B"])


class TestE2TerminatorInsideASubstitution(unittest.TestCase):
    """Reviewer E2 (pre-arc): `EOF)` ends a heredoc opened inside `$(` (MEASURED in bash: a
    warning, then the next line RUNS); the scan read body to the end of input."""

    def test_EOF_paren_ends_the_body(self):
        c = "x=$(cat <<EOF" + _NL + "hi" + _NL + "EOF)" + _NL + DEL_GAME
        self.assertTrue(F(c)["rm_hits_game"])
        self.assertTrue(H._is_heredoc_end("EOF)", "EOF"))
        self.assertTrue(H._is_heredoc_end("  EOF )", "EOF"))

    def test_TWIN_a_body_line_merely_STARTING_with_the_word_is_body(self):
        """Clause: only `)` may follow the delimiter."""
        self.assertFalse(H._is_heredoc_end("EOFX", "EOF"))
        self.assertFalse(H._is_heredoc_end("EOF and more", "EOF"))
        c = "cat > n.md <<EOF" + _NL + "EOFX " + DEL_GAME + _NL + "EOF"
        self.assertFalse(F(c)["rm_hits_game"])


class TestE3AHeredocPipedIntoAShellOnALaterLine(unittest.TestCase):
    """Reviewer E3 (pre-arc): `cat <<EOF |` / body / `EOF` / `bash` and `{ cat <<EOF ... } |
    bash` RUN the body, but the opener line names no shell, so it was stripped as data."""

    def test_a_body_that_flows_into_a_piped_shell_is_commands(self):
        for c in ("cat <<EOF |" + _NL + D + ' -rf "' + REF + '/libraries"' + _NL + "EOF" + _NL + "bash",
                  "{ cat <<EOF" + _NL + DEL_GAME + _NL + "EOF" + _NL + "} | bash"):
            with self.subTest(c=c):
                f = F(c)
                self.assertTrue(f["rm_hits_game"] or f["rm_targets_reference"], c)

    def test_a_pipe_ending_a_line_continues_onto_the_next(self):
        self.assertEqual(H.piped_in("echo a |" + _NL + "bash"), [False, True])

    # --- one falsification twin per clause ---
    def test_TWIN_no_piped_shell_leaves_the_body_data(self):
        """Clause: a sink READS A PIPE. A file payload beside an unrelated pipe stays data."""
        c = "cat > n.md <<EOF" + _NL + DEL_GAME + _NL + "EOF" + _NL + "ls | wc -l"
        self.assertFalse(F(c)["rm_hits_game"])

    def test_TWIN_a_newline_is_not_a_pipe(self):
        self.assertEqual(H.piped_in("echo a" + _NL + "bash"), [False, False])


class TestE4CmdReadsItsProgramFromStdin(unittest.TestCase):
    """Reviewer E4 (pre-arc): `echo rd /s /q "<ref>" | cmd` and `cmd <<EOF` ran with no /c,
    and only `/c` was a carrier. Both are read as cmd programs now (translated)."""

    def test_echo_into_cmd_and_a_cmd_heredoc(self):
        for c in ('echo rd /s /q "' + REF + '" | cmd',
                  "cmd <<EOF" + _NL + 'rd /s /q "' + REF + '"' + _NL + "EOF",
                  "CMD.EXE <<EOF" + _NL + 'rd /s /q "' + REF + '"' + _NL + "EOF"):
            with self.subTest(c=c):
                self.assertTrue(F(c)["rm_targets_reference"], c)

    def test_an_unreadable_stdin_program_is_reported(self):
        self.assertTrue(F("cmd < script.bat")["carrier_untranslated"])

    # --- one falsification twin per clause ---
    def test_TWIN_cmd_with_c_takes_no_stdin_program(self):
        """Clause: NO /c /k /r. Its heredoc feeds the child, not cmd's parser."""
        self.assertFalse(H._cmd_reads_stdin("cmd /c sort"))
        self.assertEqual(H.cmd_heredoc_bodies("cmd /c sort <<EOF" + _NL + "rd /s /q x" + _NL + "EOF"), [])

    def test_TWIN_a_bare_cmd_with_nothing_piped_runs_nothing(self):
        f = F("cmd")
        self.assertFalse(f["carrier_untranslated"] or f["rm_targets_reference"])


class TestE5AnsiCQuoting(unittest.TestCase):
    """Reviewer E5 (pre-arc): `$'it\\'s'` -- a backslash escapes the quote in ANSI-C quoting --
    opened a never-closing quote in every scanner, so `; rm -rf <game>` was one segment."""

    def test_an_escaped_quote_in_ansi_c_ends_nothing(self):
        c = "echo $'it" + BS + "'s'; " + DEL_GAME
        self.assertTrue(F(c)["rm_hits_game"])
        self.assertEqual(len(H.segments(c)), 2)
        self.assertNotIn("#", H.strip_comments("echo $'a" + BS + "'b' # c"))
        self.assertEqual(H.blank_single_quoted("$'a" + BS + "'b' x"), "$'    ' x")

    # --- one falsification twin per clause ---
    def test_TWIN_ansi_c_text_is_still_literal(self):
        """Clause: ANSI-C is a QUOTE -- a command inside it does not run."""
        self.assertFalse(F("echo $'" + D + " -rf " + GAME + "'")["rm_hits_game"])
        self.assertEqual(H.substitutions("echo $'$(" + D + " x)'"), [])

    def test_TWIN_an_escaped_dollar_does_not_open_ansi_c(self):
        """Clause: the `$` is unescaped. `\\$'a\\'` is `$` then a SINGLE-quoted `a\\`."""
        self.assertEqual(len(H.segments("echo " + BS + "$'a" + BS + "'; " + DEL_GAME)), 2)


class TestE6GitWorkTreeAndRequireForce(unittest.TestCase):
    """Reviewer E6 (pre-arc): only -C set the wipe directory, and -f was assumed required."""

    def test_the_work_tree_spellings_name_the_folder(self):
        for c in ('git --git-dir="' + GAME + '/.git" --work-tree "' + GAME + '" clean -fdx',
                  'git --work-tree "' + GAME + '" clean -fdx',          # each spelling ALONE
                  'git --work-tree="' + GAME + '" clean -fdx',
                  'GIT_WORK_TREE="' + GAME + '" git clean -fdx',
                  'GIT_DIR="' + GAME + '/.git" git reset --hard'):
            with self.subTest(c=c):
                self.assertTrue(FC(c, _ELSEWHERE)["git_wipes_x4_dir"], c)

    def test_requireForce_false_makes_an_unforced_clean_delete(self):
        for v in ("false", "no", "off", "0", "FALSE"):
            c = 'git -C "' + GAME + '" -c clean.requireForce=' + v + " clean -dx"
            with self.subTest(v=v):
                self.assertTrue(FC(c, _ELSEWHERE)["git_wipes_x4_dir"], c)

    # --- one falsification twin per clause ---
    def test_TWIN_requireForce_true_or_another_key_is_not_forced(self):
        for c in ('git -C "' + GAME + '" -c clean.requireForce=true clean -dx',
                  'git -C "' + GAME + '" -c clean.requireForce clean -dx',
                  'git -C "' + GAME + '" -c core.fileMode=false clean -dx'):
            with self.subTest(c=c):
                self.assertFalse(FC(c, _ELSEWHERE)["git_wipes_x4_dir"], c)

    def test_TWIN_a_work_tree_outside_every_root_is_silent(self):
        self.assertFalse(FC('git --work-tree="C:/work/x" clean -fdx', _ELSEWHERE)["git_wipes_x4_dir"])


class TestE7EveryAclLiftAsks(unittest.TestCase):
    """Reviewer E7: `/grant` (an explicit allow overrides the INHERITED deny), `/inheritance:r`,
    `/restore` on an ancestor, `/setowner`, and an x4refguard `remove` supplied by xargs or a
    variable were not lifts."""

    def test_every_acl_modifying_switch_lifts(self):
        for c in ('icacls "' + REF + '/libraries" /grant "*S-1-1-0:(OI)(CI)F" /T',
                  'icacls "' + REF + '/libraries" /grant:r user:F',
                  'icacls "' + REF + '/libraries" /inheritance:r',
                  'icacls "' + REF + '" /setowner someone',
                  'icacls "' + TOOLKIT + '" /restore acl.txt',
                  'icacls "' + REF + '" /substitute S-1-1-0 S-1-5-32-545'):
            with self.subTest(c=c):
                self.assertTrue(F(c)["lifts_reference_deny"], c)

    def test_an_action_the_guard_cannot_read_lifts(self):
        for c in ("echo remove | xargs python scripts/x4refguard.py",
                  'python scripts/x4refguard.py "$ACTION"'):
            with self.subTest(c=c):
                self.assertTrue(F(c)["lifts_reference_deny"], c)

    # --- one falsification twin per clause ---
    def test_TWIN_applying_the_deny_or_reading_is_not_a_lift(self):
        for c in ('icacls "' + REF + '" /deny "*S-1-1-0:(OI)(CI)(DE,DC)"', 'icacls "' + REF + '"',
                  'icacls "' + REF + '" /save acl.txt /T', "python scripts/x4refguard.py status"):
            with self.subTest(c=c):
                self.assertFalse(F(c)["lifts_reference_deny"], c)

    def test_TWIN_a_grant_ABOVE_the_tree_without_T_or_elsewhere_is_not_a_lift(self):
        """Clause: under the tree, or /T or /restore walking into it."""
        self.assertFalse(F('icacls "' + TOOLKIT + '" /grant user:F')["lifts_reference_deny"])
        self.assertFalse(F('icacls "C:/work/x" /grant user:F /T')["lifts_reference_deny"])

    def test_TWIN_a_switch_VALUE_is_not_a_path(self):
        """The trustee spec / ACL file after a switch is not where the ACL is applied."""
        self.assertEqual(H._icacls_paths('icacls "C:/a" /grant "' + REF + ':F"'), ["C:/a"])


class TestH2IcaclsSwitchSpellings(unittest.TestCase):
    """FX-H2 / reviewer L: Git Bash users double a switch's slash (`//reset`) so MSYS does not
    rewrite it as a path; icacls receives `/reset`. `//reset` was read as a PATH, so
    `icacls <ref> //reset //T` was allowed. And icacls takes ANY switch that starts with
    `/inheritance` (MEASURED 2026-10-10 on a scratch file: `/inheritancezzz:r` removed 4 of 4
    inherited ACEs; `/inheritanc:r` and `/inh:r` were rejected as invalid parameters), so
    `/inheritancelevel` was one spelling of an open class, not the only alternative."""

    def test_a_doubled_slash_switch_is_the_switch(self):
        for c in ('icacls "' + REF + '" //reset //T //C',
                  'icacls "' + REF + '/libraries" //grant:r user:F',
                  'icacls "' + REF + '" //REMOVE:d *S-1-5-21-1',
                  'icacls "' + REF + '/libraries" //inheritance:r',
                  'icacls "' + REF + '" //setowner someone',
                  'icacls "' + TOOLKIT + '" //restore acl.txt',
                  'icacls "' + REF + '" //substitute S-1-1-0 S-1-5-32-545'):
            with self.subTest(c=c):
                self.assertTrue(F(c)["lifts_reference_deny"], c)

    def test_a_doubled_T_walks_from_an_ancestor(self):
        """Clause: `//T` must count as /T, or an ancestor reset is not a walk."""
        self.assertTrue(F('icacls "C:/Users/tester/Desktop" //reset //T //C')["lifts_reference_deny"])
        self.assertFalse(F('icacls "C:/Users/tester/Desktop" //reset //C')["lifts_reference_deny"])

    def test_every_inheritance_spelling_icacls_accepts_lifts(self):
        for sw in ("/inheritance:r", "/inheritancelevel:r", "/inheritancezzz:r", "/INHERITANCEx:d",
                   "//inheritancelevel:e"):
            c = 'icacls "' + REF + '/libraries" ' + sw
            with self.subTest(c=c):
                self.assertTrue(F(c)["lifts_reference_deny"], c)
                self.assertEqual(H._icacls_paths(c), [REF + "/libraries"])

    # --- one falsification twin per clause ---
    def test_TWIN_a_UNC_path_is_not_a_doubled_switch(self):
        """`//server/share` cannot fully match `//<letters>`: it stays the NAME operand."""
        self.assertEqual(H._icacls_paths("icacls //server/share /reset"), ["//server/share"])
        self.assertEqual(H._icacls_paths("icacls //reset/x /reset"), ["//reset/x"])

    def test_TWIN_a_prefix_icacls_rejects_is_not_inheritance(self):
        """MEASURED: `/inheritanc:r` and `/inh:r` are invalid parameters to icacls."""
        for sw in ("/inheritanc:r", "/inh:r"):
            self.assertFalse(F('icacls "' + REF + '/libraries" ' + sw)["lifts_reference_deny"], sw)

    def test_TWIN_doubled_non_lift_switches_stay_non_lifts(self):
        for c in ('icacls "' + REF + '" //deny "*S-1-1-0:(OI)(CI)(DE,DC)"',
                  'icacls "' + REF + '" //save acl.txt //T'):
            with self.subTest(c=c):
                self.assertFalse(F(c)["lifts_reference_deny"], c)
        self.assertEqual(H._icacls_paths('icacls "C:/a" //grant "' + REF + ':F"'), ["C:/a"])

    def test_findsid_value_is_not_a_path_and_findsid_is_not_a_lift(self):
        self.assertEqual(H._icacls_paths('icacls "C:/a" /findsid "' + REF + '" /T'), ["C:/a"])
        self.assertFalse(F('icacls "' + REF + '" /findsid *S-1-1-0 /T')["lifts_reference_deny"])
        self.assertFalse(F('icacls "C:/a" /findsid "' + REF + '" /T')["lifts_reference_deny"])


class TestAShellWriteToAgentSettingsIsRefused(unittest.TestCase):
    """User decision 2026-10-05 ("Block X4_GUARD there"): a settings `env` block reaches every
    hook, and a shell write's resulting content cannot be seen -- so every shell WRITE to a
    `.claude/settings*.json` is refused (the file-edit tools are judged on content instead)."""
    FACT = "writes_agent_settings"
    S = TOOLKIT + "/.claude/settings.json"

    def test_every_write_primitive_to_a_settings_file_fires(self):
        for c in ("echo x > '" + self.S + "'", "echo x >> '" + self.S + "'",
                  "jq . a.json > '" + TOOLKIT + "/.claude/settings.local.json'",
                  "cp a.json '" + self.S + "'", "mv a.json '" + self.S + "'",
                  "echo x | tee '" + self.S + "'", "sed -i s/a/b/ '" + self.S + "'",
                  "echo x > " + DQ + "$HOME/.claude/settings.json" + DQ,
                  "cd '" + TOOLKIT + "/.claude' && echo x > settings.json"):
            with self.subTest(c=c):
                self.assertTrue(FC(c, _ELSEWHERE)[self.FACT], c)

    # --- one falsification twin per clause ---
    def test_TWIN_a_read_a_delete_or_a_move_AWAY_is_not_a_write(self):
        """Clause: a WRITE. Removing the file sets nothing."""
        for c in ("cat '" + self.S + "'", "jq .env '" + self.S + "'", D + " '" + self.S + "'",
                  "mv '" + self.S + "' /c/elsewhere/old.json", "cp '" + self.S + "' /c/x/bak.json"):
            with self.subTest(c=c):
                self.assertFalse(FC(c, _ELSEWHERE)[self.FACT], c)

    def test_TWIN_another_name_or_folder_is_not_a_settings_file(self):
        """Clause: `.claude/settings*.json`."""
        for c in ("echo x > '" + TOOLKIT + "/.claude/hooks/x.json'",
                  "echo x > '" + TOOLKIT + "/settings.json'",
                  "echo x > '" + TOOLKIT + "/.claude/settings.json.bak'",
                  "echo x > '" + TOOLKIT + "/.claudex/settings.json'"):
            with self.subTest(c=c):
                self.assertFalse(FC(c, _ELSEWHERE)[self.FACT], c)


class TestH1AShellWriteToAHostTranscriptIsRefused(unittest.TestCase):
    """H1 (v4.0 release review): the save guard reads the user's typed approvals from the host
    transcript, so a shell line appended there forges one. Every shell write/append/copy/move ONTO
    `.claude/projects/**.jsonl` or `.codex/sessions/**.jsonl` is refused. Added after CI run
    39187589960: verify-hook-tests.py found this predicate probed by NO test in either direction."""
    FACT = "writes_host_transcript"
    T = "/srv/h/.claude/projects/C--work/0b1e.jsonl"
    X = "/srv/h/.codex/sessions/2026/rollout-1.jsonl"

    def test_every_write_primitive_onto_a_transcript_fires(self):
        for p in (self.T, self.X):
            for c in ("echo '{}' >> '" + p + "'", "echo x > '" + p + "'", "cp a.jsonl '" + p + "'",
                      "mv a.jsonl '" + p + "'", "echo x | tee -a '" + p + "'",
                      "sed -i s/a/b/ '" + p + "'"):
                with self.subTest(c=c):
                    self.assertTrue(FC(c, _ELSEWHERE)[self.FACT], c)

    def test_M1_a_directory_destination_that_receives_a_jsonl_fires(self):
        """M1 (v4.0 release review): `cp x.jsonl <projects>/p/` and `cp -t <dir> x.jsonl` were
        ALLOWED -- the file lands as <dir>/x.jsonl. The source's NAME decides; an unresolved or
        pattern source, or a recursive copy, may carry one, so it fires too."""
        for d in ("/srv/h/.claude/projects/C--work", "/srv/h/.codex/sessions/2026"):
            for c in ("cp /srv/a.jsonl '" + d + "/'", "cp -t '" + d + "' /srv/a.jsonl",
                      "cp --target-directory='" + d + "' /srv/a.jsonl", "mv /srv/a.jsonl '" + d + "'",
                      "install /srv/a.jsonl '" + d + "/'", "cp /srv/*.jsonl '" + d + "/'",
                      "cp \"$SRC\" '" + d + "/'", "cp -r /srv/dir '" + d + "/'", "rsync /srv/a '" + d + "/'"):
                with self.subTest(c=c):
                    self.assertTrue(FC(c, _ELSEWHERE)[self.FACT], c)

    def test_M1_TWIN_a_non_jsonl_source_or_a_destination_outside_the_tree_does_not(self):
        """Clauses: the destination is IN a transcript tree, and the landing name is a .jsonl."""
        for c in ("cp /srv/notes.md '/srv/h/.claude/projects/C--work/memory/'",
                  "cp -t /srv/h/.claude/projects/C--work/memory /srv/notes.md",
                  "cp /srv/a.jsonl /srv/other/", "cp -t /srv/other /srv/a.jsonl"):
            with self.subTest(c=c):
                self.assertFalse(FC(c, _ELSEWHERE)[self.FACT], c)

    def test_M1_relocated_trees_from_the_hosts_own_variables(self):
        """$CLAUDE_CONFIG_DIR/projects and $CODEX_HOME/sessions: as written, and by value."""
        for c in ("echo x >> \"$CODEX_HOME/sessions/2026/r.jsonl\"",
                  "echo x >> \"${CLAUDE_CONFIG_DIR}/projects/p/s.jsonl\"",
                  "cp /srv/a.jsonl \"$CLAUDE_CONFIG_DIR/projects/p/\""):
            with self.subTest(c=c):
                self.assertTrue(FC(c, _ELSEWHERE)[self.FACT], c)
        saved = {k: os.environ.get(k) for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        try:
            os.environ["CLAUDE_CONFIG_DIR"] = "/srv/cfg"
            os.environ["CODEX_HOME"] = "/srv/cx"
            self.assertTrue(FC("echo x >> /srv/cfg/projects/p/s.jsonl", _ELSEWHERE)[self.FACT])
            self.assertTrue(FC("cp /srv/a.jsonl /srv/cx/sessions/2026/", _ELSEWHERE)[self.FACT])
            self.assertFalse(FC("echo x >> /srv/cfg/projects/p/memory/n.md", _ELSEWHERE)[self.FACT])
            self.assertFalse(FC("echo x >> /srv/cfg/other/s.jsonl", _ELSEWHERE)[self.FACT])
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    # --- one falsification twin per clause ---
    def test_TWIN_a_read_of_a_transcript_is_not_a_write(self):
        """Clause: a WRITE."""
        for c in ("cat '" + self.T + "'", "grep x '" + self.X + "'", "cp '" + self.T + "' /c/x/copy.jsonl"):
            with self.subTest(c=c):
                self.assertFalse(FC(c, _ELSEWHERE)[self.FACT], c)

    def test_TWIN_a_memory_note_or_a_jsonl_elsewhere_is_not_a_transcript(self):
        """Clauses: under `.claude/projects` or `.codex/sessions`, and a `.jsonl`."""
        for c in ("echo x >> '/srv/h/.claude/projects/C--work/memory/note.md'",
                  "echo '{}' >> '/c/work/log.jsonl'",
                  "echo '{}' >> '/srv/h/.claude/other/0b1e.jsonl'",
                  "echo '{}' >> '/srv/h/.codex/config/x.jsonl'"):
            with self.subTest(c=c):
                self.assertFalse(FC(c, _ELSEWHERE)[self.FACT], c)


def _evidence(command, cwd=TOOLKIT, roots=None):
    ev = []
    H.facts({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd},
            ROOTS if roots is None else roots, evidence=ev)
    return ev


#: HOST-NATIVE fixture roots for the evidence tests. On a POSIX host a `C:/...` path is a
#: FOREIGN drive path, unresolved for its own reason -- so a twin asserting "resolved" could not
#: test its own clause there (CI rc9, ubuntu: 6 subtests of the C1 twin failed for exactly that).
_NATIVE_PROF = PROF if os.name == "nt" else "/home/tester/Documents/Egosoft/X4/12345678"
_NATIVE_TK = TOOLKIT if os.name == "nt" else "/home/tester/Desktop/Modding/X4"
_NATIVE_ROOTS = dict(ROOTS, profile=_NATIVE_PROF, saves=_NATIVE_PROF + "/save",
                     documents=_NATIVE_PROF.split("/Egosoft/")[0], toolkit=_NATIVE_TK,
                     mods=_NATIVE_TK + "/dev", reference=_NATIVE_TK + "/reference")


class TestC1BraceExpansionIsUnresolvedEvidence(unittest.TestCase):
    """C1 (v4.0 release review): an unquoted brace word reached the save bridge as ONE literal
    path -- `rm <saves>/save_00{0,1,2}.xml.gz` advised with 0 backups. Every evidence row from an
    unquoted `{a,b}` / `{x..y}` word is unresolved; a quoted or escaped one is a literal name."""
    S = _NATIVE_PROF + "/save/"

    def _rows(self, command):
        return [r for r in _evidence(command, _NATIVE_TK, _NATIVE_ROOTS)
                if r.get("operation") != "unmodelled"]

    def test_unquoted_brace_rows_are_unresolved_in_every_verb_shape(self):
        for c in (D + " " + self.S + "save_00{0,1,2}.xml.gz", D + " " + self.S + "s{0..2}.xml.gz",
                  D + " -f " + self.S + "{a,b}.xml.gz", D + " " + self.S + "a.xml.gz{,}",
                  "mv " + self.S + "s{0,1}.xml.gz /c/x", "printf '' | tee " + self.S + "s{0,1}.xml.gz",
                  "cd '" + self.S + "' && " + D + " s{0,1}.xml.gz",
                  "bash -c '" + D + " " + self.S + "s{0,1}.xml.gz'"):
            with self.subTest(c=c):
                rows = [r for r in self._rows(c) if "{" in r.get("raw", "")]
                self.assertTrue(rows, c)
                self.assertTrue(all(r["unresolved"] for r in rows), (c, rows))

    def test_TWIN_quoted_escaped_or_parameter_braces_stay_literal(self):
        """Clause: UNQUOTED, and a real brace expansion (a `,` or `..` inside, not `${`)."""
        for c in (D + " '" + self.S + "s{0,1}.xml.gz'", D + ' "' + self.S + 's{0,1}.xml.gz"',
                  D + " " + self.S + "s'{0,1}'.xml.gz", D + " " + self.S + "s" + BS + "{0,1" + BS + "}.xml.gz",
                  D + " " + self.S + "s$'{0,1}'.xml.gz", D + " " + self.S + "s{0}.xml.gz",
                  D + " " + self.S + "s{}.xml.gz", D + " " + self.S + "s{a.b}.xml.gz"):
            with self.subTest(c=c):
                rows = [r for r in self._rows(c) if "{" in r.get("raw", "")]
                self.assertTrue(rows, c)
                self.assertFalse(any(r["unresolved"] for r in rows), (c, rows))

    def test_the_side_channel_is_cleared_per_call(self):
        _evidence(D + " " + self.S + "s{0,1}.xml.gz", _NATIVE_TK, _NATIVE_ROOTS)
        rows = self._rows(D + " '" + self.S + "s{0,1}.xml.gz'")
        self.assertFalse(any(r["unresolved"] for r in rows), rows)

    def test_has_brace_expansion_grammar(self):
        yes = ("a{b,c}", "{a,b}", "{1..3}", "{a..z}", "x{-1..2..1}", "{a,{b,c}}", "{,}")
        no = ("a{b}", "${a,b}", "{}", "a{b.c}", "{a", "a}", "{1...3}", chr(0) + "a,b}")
        for w in yes:
            self.assertTrue(H._has_brace_expansion(w), w)
        for w in no:
            self.assertFalse(H._has_brace_expansion(w), w)


class TestP1AnAssignmentPrefixIsOneWordWhateverSpanItHolds(unittest.TestCase):
    """P1 (v4.0 release review, pre-existing): `X=<(echo a) rm -rf <game>` was ALLOWED past every
    Bash rule -- the word reader split the process substitution at its blank and `a)` became the
    verb. The CLASS, from the grammar (each shape run in Git Bash first: the command DOES run):
    `<(`/`>(` (also mid-word, nested, quoted `)`, escaped blank), `$[ ]`, an array value, `+=`, a
    subscript with blanks, and a `case` inside `$( )`."""
    PREFIXES = ("X=<(echo a)", "X=>(cat -n)", "X=<( echo a )", "X=a<(echo b)c", "X=$[1 + 2]",
                "X=(a b)", "X+=<(echo a)", "X[1 + 1]=a", "X=<(echo $(echo a b))",
                "X=$(echo a) Y=<(echo b c)", "X=<(echo " + Q + "a)" + Q + " b)", "X=<(echo a" + BS + " b)",
                "X=$(case a in a) echo x y;; esac)", "X=$((1 + 2))", "X=${Y:- a}", "X=$(echo a b)")

    def test_the_verb_and_every_hard_block_survive_each_prefix(self):
        for p in self.PREFIXES:
            for wrap in ("", "env "):
                with self.subTest(p=wrap + p):
                    self.assertEqual(H.verb(wrap + p + " " + D + " -rf /x"), D)
                    f = F(wrap + p + " " + DEL_GAME)
                    self.assertTrue(f["rm_hits_game"] or f.get("verb_unresolved"), (p, f))
            with self.subTest(p=p, rule="git add -A"):
                self.assertTrue(F(p + " git add -A")["git_add_all"])

    def test_operands_start_after_the_real_verb(self):
        self.assertEqual(H._operands("X=<(echo a) " + D + " -rf /x"), ["/x"])
        self.assertEqual(H._operands("sudo -u root " + D + " /x"), ["/x"])

    def test_TWIN_a_glob_with_a_bracket_is_still_split_and_a_plain_word_untouched(self):
        """Clause: a subscript joins only when `]=` follows (a glob `a[b c]` is two words)."""
        self.assertEqual(H._raw_words("ls a[b c]"), ["ls", "a[b", "c]"])
        self.assertEqual(H._raw_words("cat <(true) x"), ["cat", "<(true)", "x"])
        self.assertEqual(H._raw_words("X=1 " + D + " a"), ["X=1", D, "a"])

    def test_env_word_splitting_makes_the_command_name_unknowable(self):
        """After `env`, `NAME=$(...)` is an ordinary argument: an UNQUOTED expansion is word-split
        and a field becomes the command. MEASURED in Git Bash: `env X=$(echo a rm) -rf <dir>` runs
        rm. The guard read `<game>` as the verb and ALLOWED it."""
        for w in ("X=$(echo a " + D + ")", "X=${Y:- " + D + "}", "X=" + chr(96) + "echo a " + D + chr(96),
                  "X=$V"):
            with self.subTest(w=w):
                self.assertTrue(F("env " + w + ' -rf "' + GAME + '"')["verb_unresolved"])
        # TWIN: quoted (no splitting), or no wrapper (a real assignment): the verb is known.
        for c in ("env X=" + DQ + "$(echo a b)" + DQ + " " + D + " -rf /x", "X=$(echo a b) " + D + " -rf /x"):
            self.assertIsNone(H._env_split_index(c), c)


def _blockers(command, tool="Bash"):
    return H.facts({"tool_name": tool, "tool_input": {"command": command}, "cwd": TOOLKIT},
                   ROOTS).get("save_lift_blockers") or []


class TestI1ACarrierIsTransparentOnlyWhenItsTextIsAllItRuns(unittest.TestCase):
    """I1 (v4.0 release review, 254ec43 `_carries_program_text`): the K1 carrier exemption lifted
    the profile ask for shells that also run STARTUP code the text never shows. MEASURED E2E, each
    advised with one backed-up save: `BASH_ENV=<f> bash -c '...'`, `env BASH_ENV=<f> bash -c true`,
    `bash --rcfile x -i -c true`, `bash -lc true`, PowerShell `$env:BASH_ENV=...; bash -c 'true'`."""
    SAVE = D + " '" + PROF + "/save/a.xml.gz'; "

    def test_startup_steering_makes_the_carrier_a_blocker(self):
        for tail in ("BASH_ENV=/srv/e.sh bash -c true", "env BASH_ENV=/srv/e.sh bash -c true",
                     "export BASH_ENV=/srv/e.sh; bash -c true", "BASH_ENV=/srv/e.sh; bash -c true",
                     "printf -v BASH_ENV %s /srv/e.sh; bash -c true", "ENV=/srv/e.sh sh -c true",
                     "env 'BASH_FUNC_true%%=() { :; }' bash -c true", "SHELLOPTS=xtrace bash -c true",
                     "PS4='$(true)' bash -xc true", "ZDOTDIR=/srv bash -c true",
                     "bash -lc true", "bash -l -c true", "bash --login -c true", "bash -ic true",
                     "bash --rcfile /srv/e.sh -i -c true", "bash --init-file /srv/e.sh -c true",
                     "bash -e -c true", "zsh -c true",
                     "cmd //c 'echo hi'", "powershell -Command 'Write-Output 1'",
                     "pwsh -c 'Write-Output 1'", "powershell -Command 'x' -NoProfile"):
            with self.subTest(tail=tail):
                self.assertTrue(_blockers(self.SAVE + tail), tail)

    def test_powershell_env_assignment_is_seen_although_its_translation_drops_it(self):
        save = "Remove-Item -LiteralPath '" + PROF.replace("/", BS) + BS + "save" + BS + "a.xml.gz'; "
        for tail in ("$env:BASH_ENV = '/srv/e.sh'; bash -c 'true'", "$Env:bash_env='x'; bash -c 'true'",
                     "$env:ENV = 'x'; sh -c 'true'"):
            with self.subTest(tail=tail):
                self.assertTrue(_blockers(save + tail, "PowerShell"), tail)
        self.assertFalse(_blockers(save + "bash -c 'true'", "PowerShell"))      # TWIN

    def test_TWIN_each_clause_keeps_its_exemption_when_the_text_is_all_it_runs(self):
        """The positive twin for every clause of _carries_program_text: bash/sh/dash/ksh with a
        bare -c, eval, cmd with /d, a PowerShell host with -NoProfile (-Command / -nop -c /
        -EncodedCommand), and an unrelated assignment in front."""
        enc = "VwByAGkAdABlAC0ATwB1AHQAcAB1AHQAIAAxAA=="       # Write-Output 1 (UTF-16LE)
        for tail in ("bash -c true", "sh -c 'echo hi'", "dash -c true", "ksh -c true", "eval 'true'",
                     "X=1 bash -c true", "env X=1 bash -c true", "cmd //d //c 'echo hi'", "cmd /d /c echo",
                     "powershell -NoProfile -Command 'Write-Output 1'", "pwsh -nop -c 'Write-Output 1'",
                     "powershell -NoProfile -EncodedCommand " + enc, "echo environment; bash -c true"):
            with self.subTest(tail=tail):
                self.assertEqual(_blockers(self.SAVE + tail), [], tail)


# ======================================================================================
# FX-G4 (v4.0.0 delta review, reviewers G and H). Each RED was reproduced end to end
# through protect-bash.sh against scratch roots, with a deny control, before the fix.
# ======================================================================================
BT = chr(96)                       # backtick


class TestH5ShiftIsNotAHeredoc(unittest.TestCase):
    """H5 (pre-arc): `<<` inside `(( ))`, `$[ ]`, `${ }` or an assignment subscript is a SHIFT or
    text (MEASURED in bash: the next line RUNS), but the scan opened a heredoc there and hid every
    later line -- `rm -rf <game>` on line 2 was allowed past the hard block."""

    def test_shift_in_arithmetic_command_subscript_or_expansion_opens_no_heredoc(self):
        for first in ("((x=1<<2))", "((x=1<<EOF))", "if ((1<<2)); then :; fi",
                      "for ((i=0;i<1<<EOF;i++)); do :; done", "a[1<<2]=1", "a[1<<EOF]+=1",
                      "echo $[1<<EOF]", "y=${x//<<EOF/z}", "echo ${#a[1<<EOF]}"):
            with self.subTest(first=first):
                self.assertTrue(F(first + _NL + DEL_GAME)["rm_hits_game"], first)

    # --- one falsification twin per clause ---
    def test_TWIN_a_real_heredoc_after_or_beside_those_forms_still_opens(self):
        """Clauses: blanking stops at the closing bracket; `echo x[1<<EOF]` (no `]=`) and a
        `<(` process substitution are not blanked -- both DO open a heredoc in bash."""
        for first in ("((x=1)); cat <<EOF", "a[1]=2 cat <<EOF", "echo ${x} <<EOF",
                      "echo $[1] <<EOF", "cat <(cat <<EOF", "echo x[1<<EOF"):
            with self.subTest(first=first):
                self.assertFalse(F(first + _NL + DEL_GAME + _NL + "EOF")["rm_hits_game"], first)


class TestH5BacktickTerminator(unittest.TestCase):
    def test_EOF_backtick_ends_the_body(self):
        """MEASURED in bash: x=`cat <<EOF` / hi / EOF` runs the next line."""
        c = "x=" + BT + "cat <<EOF" + _NL + "hi" + _NL + "EOF" + BT + _NL + DEL_GAME
        self.assertTrue(F(c)["rm_hits_game"])
        self.assertTrue(H._is_heredoc_end("EOF" + BT, "EOF"))

    def test_TWIN_a_backtick_elsewhere_on_the_line_is_body(self):
        self.assertFalse(H._is_heredoc_end("EOFX" + BT, "EOF"))
        self.assertFalse(H._is_heredoc_end("a EOF" + BT, "EOF"))


class TestH1CmdKReadsStdin(unittest.TestCase):
    """H1 (MEASURED in Git Bash): `cmd /k` runs its inline command and THEN reads its program from
    stdin; `/c` and `/r` do not -- unless the child is itself a cmd that reads it."""

    RD = 'rd /s /q "' + REF + '"'

    def test_cmd_k_reads_its_program_from_stdin_too(self):
        for c in ("cmd /k <<EOF" + _NL + self.RD + _NL + "EOF",
                  "cmd //K <<EOF" + _NL + self.RD + _NL + "EOF",
                  "echo " + self.RD + " | cmd /k",
                  "echo " + self.RD + " | cmd //k echo hi",
                  "cmd //k <<< " + Q + self.RD + Q):
            with self.subTest(c=c):
                self.assertTrue(F(c)["rm_targets_reference"], c)
        self.assertTrue(H._cmd_reads_stdin("cmd /k dir"))

    def test_cmd_c_cmd_reads_stdin_through_the_child(self):
        for c in ("echo " + self.RD + " | cmd /c cmd", "cmd //c cmd <<EOF" + _NL + self.RD + _NL + "EOF"):
            with self.subTest(c=c):
                self.assertTrue(F(c)["rm_targets_reference"], c)

    # --- one falsification twin per clause ---
    def test_TWIN_c_and_r_with_another_child_take_no_stdin_program(self):
        for c in ("echo " + self.RD + " | cmd /c sort", "cmd /r sort <<EOF" + _NL + self.RD + _NL + "EOF",
                  "echo " + self.RD + " | cmd //c echo /k"):
            with self.subTest(c=c):
                self.assertFalse(F(c)["rm_targets_reference"], c)


class TestH2RootVariableUnderAnOperator(unittest.TestCase):
    """H2: `${X4_GAME:-/x}`, `${X4_GAME%/}`, `${X4_GAME:=/x}` were allowed -- an unassigned root
    variable was read as UNSET (so `:-` gave the default), and only `$X`/`${X}` were taken."""

    def test_a_root_variable_under_a_brace_operator_is_its_root(self):
        for op in (":-/x", "%/", ":=/x", "-/x", ":+/x", "#x", "//a/b", ":?no"):
            c = D + ' -rf "${X4_GAME' + op + '}"'
            with self.subTest(c=c):
                self.assertTrue(FC(c, _ELSEWHERE)["rm_hits_game"], c)
        self.assertTrue(FC('echo x > "${X4_REFERENCE:-/nope}/libraries/w.xml"', _ELSEWHERE)["writes_reference"])
        self.assertEqual(H.subst_root_var("${X4_REFERENCE:-/n}/a", ROOTS), REF + "/a")
        # another variable whose DEFAULT is a root variable (fuzz-guard, FX-G4)
        self.assertTrue(FC(D + ' -rf "${FZ_UNSET:-${X4_GAME:-/x}}"', _ELSEWHERE)["rm_hits_game"])
        # a default holding a space cuts the word inside the braces: still that root
        self.assertTrue(FC("G=${X4_GAME:-" + GAME + '}; ' + D + ' -rf "$G"', _ELSEWHERE)["rm_hits_game"])

    def test_an_unconfigured_root_under_an_operator_still_refuses_a_delete(self):
        """Its root unset, the operand stays unresolved -- and must still NAME the root for the
        conservative delete branch, as `$X4_GAME` does."""
        roots = dict(ROOTS, game="")
        self.assertIn("game", H.root_vars_named("${X4_GAME:-/x}/a"))
        self.assertEqual(H.subst_root_var("${X4_GAME:-/x}", roots), "${X4_GAME:-/x}")

    # --- one falsification twin per clause ---
    def test_TWIN_another_variable_or_an_assignment_in_the_command_is_not_a_root(self):
        self.assertFalse(FC('echo x > "${OTHER:-/nope}/libraries/w.xml"', _ELSEWHERE)["writes_reference"])
        self.assertFalse(FC(D + ' -rf "${TMPX:-/tmp/zz}"', _ELSEWHERE)["rm_hits_game"])
        self.assertFalse(FC('X4_GAME=/tmp/g; ' + D + ' -rf "${X4_GAME:-/x}"', _ELSEWHERE)["rm_hits_game"])
        self.assertEqual(H.root_vars_named("${#X4_GAME}"), set())


class TestJ2AlternateOfAnUnknownVariable(unittest.TestCase):
    """FX-G5 / reviewer J2 item 9 (pre-arc, MEASURED: allowed): `${PATH:+$X4_GAME}` -- a variable
    the command never assigned was read as UNSET, so the alternate word was dropped. Its value
    is unknown, so the alternate may be what the shell substitutes: it is judged."""

    def test_the_alternate_of_an_unassigned_variable_is_judged(self):
        for op in (":+", "+"):
            c = D + ' -rf "${PATH' + op + '$X4_GAME}"'
            with self.subTest(c=c):
                self.assertTrue(FC(c, _ELSEWHERE)["rm_hits_game"], c)
        self.assertEqual(H._apply_op("NOPE", None, ":+/x", {}), "/x")

    # --- one falsification twin per clause ---
    def test_TWIN_an_assigned_variable_still_decides(self):
        # assigned EMPTY: `:+` gives "" (and `+` the alternate: set-but-empty is set)
        self.assertEqual(H._apply_op("V", None, ":+/x", {"V": ""}), "")
        self.assertEqual(H._apply_op("V", None, "+/x", {"V": ""}), "/x")
        self.assertEqual(H._apply_op("V", None, ":+/x", {"V": "v"}), "/x")
        self.assertFalse(FC('V=; ' + D + ' -rf "${V:+$X4_GAME}"', _ELSEWHERE)["rm_hits_game"])
        # `:-` of an unknown non-root variable is unchanged: the default word
        self.assertEqual(H._apply_op("NOPE", None, ":-/x", {}), "/x")


class TestK_C1EveryValueOfAnUnknownVariable(unittest.TestCase):
    """FX-G6 / reviewer K C1 (REGRESSION of FX-G5 f03d19b, MEASURED E2E deny -> allow): judging
    `${NOPE:+w}` as the alternate word ALONE lost the EMPTY branch -- `rm -rf "${NOPE:+zz}<ref>"`
    IS `rm -rf <ref>` when NOPE is unset. An unassigned variable under `-`/`=`/`+` (colon or
    not) has several values -- unset, empty, set -- and every path rule judges each one."""

    def test_the_empty_branch_is_judged_again(self):
        # REF, not GAME: the game root's folder NAME has a text backstop of its own, which
        # would pass these rows with or without the branches (measured by the mutants)
        for c in (D + ' -rf "${NOPE:+zz}' + REF + '"', D + ' -rf "${NOPE+zz}' + REF + '"',
                  'cd "${NOPE:+x}' + REF + '" && ' + D + ' -rf libraries',
                  D + ' -rf "${NOPE-./junk}' + REF + '"',
                  # states are PER NAME: A unset and B set
                  D + ' -rf "${A:+./junk}${B:+' + REF + '}"',
                  # past _MAX_BRANCH_NAMES: one name set, the rest uniform
                  D + ' -rf "${A:+./j}${B:+x}${C:+y}${D:+z}${E:+' + REF + '}"',
                  # ...and inside a CARRIER, whose text was resolved to ONE value (fuzz-guard)
                  "bash -c " + Q + D + ' -rf "${NOPE:+zz}' + REF + '"' + Q,
                  "eval " + Q + D + ' -rf "${NOPE:+zz}' + REF + '"' + Q,
                  "cmd //c rd /s /q " + DQ + "${NOPE:+zz}" + REF + DQ):
            with self.subTest(c=c):
                self.assertTrue(FC(c, _ELSEWHERE)["rm_targets_reference"], c)
        for c in (D + ' -rf "${NOPE:+./junk}' + GAME + '"', D + ' -rf "${NOPE:+x}$X4_GAME"',
                  # `-` without the colon: set-but-EMPTY gives "" (the "empty" state)
                  D + ' -rf "${NOPE-./junk}' + GAME + '"',
                  # states are PER NAME: A unset and B set
                  D + ' -rf "${A:+./junk}${B:+' + GAME + '}"',
                  # past _MAX_BRANCH_NAMES: one name set, the rest uniform
                  D + ' -rf "${A:+./j}${B:+x}${C:+y}${D:+z}${E:+' + GAME + '}"',
                  # ...and the alternate branch (FX-G5) still stands
                  D + ' -rf "${PATH:+$X4_GAME}"'):
            with self.subTest(c=c):
                self.assertTrue(FC(c, _ELSEWHERE)["rm_hits_game"], c)

    def test_C6_redirect_and_output_flag_targets_are_judged_at_every_value(self):
        """C6 (v4.0 release review, IN-ARC since FX-G5 47d06cd): prep(expand=False) -- REDIRECT and
        output-flag targets -- took ONE resolve(), the alternate. MEASURED E2E: `echo x >
        "${NOPE:+x}<reference>/libraries/wares.xml"` ADVISED where the plain path denies."""
        w = REF + "/libraries/wares.xml"
        for c in ('echo x > "${NOPE:+x}' + w + '"', 'echo x >> "${NOPE:+x}' + w + '"',
                  'echo x > "${NOPE+x}' + w + '"', 'echo x 2> "${NOPE:+x}' + w + '"',
                  'curl -o "${NOPE:+x}' + w + '" http://127.0.0.1:9/x',
                  'curl --output "${NOPE:+x}' + w + '" http://127.0.0.1:9/x'):
            with self.subTest(c=c):
                self.assertTrue(FC(c, _ELSEWHERE)["writes_reference"], c)
        # TWIN: the plain path is unchanged, and an alternate word elsewhere stays harmless.
        self.assertTrue(FC('echo x > "' + w + '"', _ELSEWHERE)["writes_reference"])
        self.assertFalse(FC('echo x > "${NOPE:+x}/srv/out.txt"', _ELSEWHERE)["writes_reference"])

    def test_resolve_variants_offers_every_state(self):
        self.assertEqual(sorted(H.resolve_variants("${NOPE:+zz}", {})), ["", "zz"])
        self.assertEqual(sorted(H.resolve_variants("${NOPE-w}x", {})), ["${NOPE-w}x", "wx", "x"])
        self.assertEqual(H.resolve_variants("${NOPE:+zz}", {})[0], "zz")   # resolve() first

    # --- one falsification twin per clause ---
    def test_TWIN_an_assigned_variable_has_one_value(self):
        self.assertEqual(H.resolve_variants("${V:+zz}" + REF, {"V": "x"}), ["zz" + REF])
        self.assertFalse(FC('V=x; ' + D + ' -rf "${V:+zz}' + REF + '"', _ELSEWHERE)["rm_targets_reference"])

    def test_TWIN_no_operator_is_not_branched(self):
        self.assertEqual(H.resolve_variants("$NOPE/x", {}), ["$NOPE/x"])
        self.assertEqual(H.resolve_variants("${NOPE%/x}", {}), ["${NOPE%/x}"])

    def test_TWIN_the_colon_treats_empty_as_unset(self):
        self.assertEqual(H._apply_op("N", None, ":-w", {}, env={"N": "empty"}), "w")
        self.assertEqual(H._apply_op("N", None, "-w", {}, env={"N": "empty"}), "")
        self.assertEqual(H._apply_op("N", None, ":+w", {}, env={"N": "empty"}), "")
        self.assertEqual(H._apply_op("N", None, "+w", {}, env={"N": "empty"}), "w")
        self.assertIsNone(H._apply_op("N", None, ":-w", {}, env={"N": "set"}))
        self.assertEqual(H._apply_op("N", None, ":+w", {}, env={"N": "unset"}), "")

    def test_TWIN_ordinary_alternate_words_stay_harmless(self):
        for c in (D + ' -rf "${TMPDIR:+$TMPDIR/}build"', D + ' -rf "build${SUFFIX:+-$SUFFIX}"',
                  'export PATH="/x/bin${PATH:+:$PATH}"', 'cp -r a "${OUT:+$OUT/}dist"'):
            with self.subTest(c=c):
                f = FC(c, _ELSEWHERE)
                self.assertFalse(f["rm_hits_game"] or f["rm_targets_reference"] or f["rm_in_x4_dir"], c)


class TestG7EveryCdValueIsALane(unittest.TestCase):
    """FX-G7 / regression corpus #397 (MEASURED: e39853f deny -> 8ed563f advise): FX-G6 judged a
    `cd` with several possible targets by ONE value -- the first landing under ANY root. From
    the toolkit, `cd "${NOPE:+x}<reference>"` took `x<reference>` (relative, so joined under the
    toolkit, a root) and `rm -rf libraries` was judged in the toolkit. Every value is now a lane
    and a relative operand is judged against each (cwd_lanes)."""
    CWDS = (TOOLKIT + "/work", GAME, _ELSEWHERE)

    def test_every_value_of_a_cd_target_anchors_a_relative_operand(self):
        for c in ('cd "${NOPE:+x}' + REF + '" && ' + D + ' -rf libraries',
                  'cd "${NOPE+x}' + REF + '" && ' + D + ' -rf libraries',
                  'pushd "${NOPE:+x}' + REF + '" && ' + D + ' -rf libraries',
                  'cd "${NOPE:+x}' + REF + '" && cd libraries && ' + D + ' -f w.xml',
                  "bash -c " + Q + 'cd "${NOPE:+x}' + REF + '" && ' + D + ' -rf libraries' + Q,
                  "eval " + Q + 'cd "${NOPE:+x}' + REF + '" && ' + D + ' -rf libraries' + Q):
            for cwd in self.CWDS:
                with self.subTest(c=c, cwd=cwd):
                    self.assertTrue(FC(c, cwd)["rm_targets_reference"], (c, cwd))
        for cwd in self.CWDS:
            with self.subTest(cwd=cwd):
                self.assertTrue(FC('cd "${NOPE:+x}' + REF + '" && echo y > libraries/w.xml',
                                   cwd)["writes_reference"])

    def test_the_seeded_and_unseeded_walks_have_the_same_lanes(self):
        c = 'cd "${A:+x}' + REF + '" && pushd "${B-y}' + GAME + '" && popd && ls'
        seeded = H.cwd_lanes(c, TOOLKIT, seeded=True, roots=ROOTS)
        bare = H.cwd_lanes(c, roots=ROOTS)
        self.assertEqual([len(ds) for _s, ds in seeded], [len(ds) for _s, ds in bare])
        self.assertGreater(len(seeded[-1][1]), 1)

    def test_past_the_lane_bound_a_root_value_is_still_taken(self):
        saved = H._MAX_CD_LANES
        H._MAX_CD_LANES = 1
        try:
            self.assertTrue(FC('cd "${NOPE:+x}' + REF + '" && ' + D + ' -rf libraries',
                               _ELSEWHERE)["rm_targets_reference"])
        finally:
            H._MAX_CD_LANES = saved

    def test_C7_past_the_lane_bound_the_directory_is_unknown_and_refused_from_every_root(self):
        """C7 (v4.0 release review), MEASURED from the toolkit root at the REAL bound: `cd
        "${A:+a}${B:+b}." && cd "${C:+c}${D:+d}${E:+e}<reference>" && rm -rf libraries` ADVISED
        (v3.3.1 denied): past 16 lanes each lane took the first value under ANY root -- the
        toolkit -- never the reference. The test above runs only from OUTSIDE every root, so it
        could not fail for that. Past the bound now: `cd_lanes_truncated`, which protect-bash
        denies, from every cwd."""
        for tail in (REF + '" && ' + D + ' -rf libraries', PROF + '" && ' + D + ' -rf save'):
            c = 'cd "${A:+a}${B:+b}." && cd "${C:+c}${D:+d}${E:+e}' + tail
            for cwd in self.CWDS + (TOOLKIT,):
                with self.subTest(c=c, cwd=cwd):
                    self.assertTrue(FC(c, cwd)["cd_lanes_truncated"], (c, cwd))

    def test_C7_TWIN_within_the_bound_nothing_is_truncated_and_the_flag_resets(self):
        c = 'cd "${A:+a}${B:+b}." && cd "${C:+c}' + REF + '" && ' + D + ' -rf libraries'   # 4 x 3 = 12
        for cwd in self.CWDS + (TOOLKIT,):
            with self.subTest(cwd=cwd):
                f = FC(c, cwd)
                self.assertFalse(f["cd_lanes_truncated"])
                self.assertTrue(f["rm_targets_reference"])

    # --- one falsification twin per clause ---
    def test_TWIN_an_assigned_variable_is_one_lane(self):
        """Clause: only an UNASSIGNED variable splits."""
        c = 'V=1; cd "${V:+x}' + REF + '" && ' + D + ' -rf libraries'
        for cwd in self.CWDS:
            with self.subTest(cwd=cwd):
                self.assertFalse(FC(c, cwd)["rm_targets_reference"])
        self.assertEqual(len(H.cwd_lanes(c, roots=ROOTS)[-1][1]), 1)

    def test_TWIN_popd_unwinds_every_lane(self):
        """Clause: each lane keeps its own pushd stack."""
        self.assertFalse(FC('pushd "${NOPE:+x}' + REF + '" && popd && ' + D + ' -rf libraries',
                            _ELSEWHERE)["rm_targets_reference"])

    def test_TWIN_a_plain_cd_elsewhere_stays_silent(self):
        for c in ('cd "C:/work/b" && ' + D + ' -rf build', 'cd "${NOPE:+x}C:/work/b" && ls',
                  'cd build && ' + D + ' -rf out'):
            with self.subTest(c=c):
                f = FC(c, _ELSEWHERE)
                self.assertFalse(f["rm_targets_reference"] or f["rm_hits_game"] or f["rm_in_x4_dir"], c)


class TestG7GitWorkTreeNamedByAnAssignment(unittest.TestCase):
    """FX-G7 (R2 of the regression corpus). MEASURED over 6 cwds x 6 roots x 12 forms: every
    spelling of a work tree in the X4 dirs -- `-C`, `cd &&`, `--work-tree X`, `--work-tree=X`,
    `--git-dir`+`--work-tree`, a `GIT_WORK_TREE=` prefix, `env GIT_WORK_TREE=` -- is the same
    ASK from every session cwd, except `export GIT_WORK_TREE=<root>; git clean -fdx`: ALLOW
    from a folder outside every root (e39853f and 8ed563f alike), because the work tree
    reached git through the environment and _git_destructive read only its own segment."""

    def test_an_exported_work_tree_names_the_folder(self):
        for c in ('export GIT_WORK_TREE="' + GAME + '"; git clean -fdx',
                  'declare -x GIT_WORK_TREE="' + GAME + '"; git clean -fdx',
                  'GIT_WORK_TREE="' + REF + '"; git reset --hard',
                  'export GIT_DIR="' + GAME + '/.git"; git clean -fdx'):
            with self.subTest(c=c):
                self.assertTrue(FC(c, _ELSEWHERE)["git_wipes_x4_dir"], c)

    def test_every_spelling_gets_the_verdict_of_the_C_form(self):
        """The -C form is the policy (an ASK: `git_wipes_x4_dir`, never the bare deny) -- and
        a work tree named any other way gets exactly that from outside every root."""
        forms = ('git -C "{t}" clean -fdx', 'git --work-tree "{t}" clean -fdx',
                 'git --work-tree="{t}" clean -fdx', 'GIT_WORK_TREE="{t}" git clean -fdx',
                 'env GIT_WORK_TREE="{t}" git clean -fdx',
                 'git --git-dir="{t}/.git" --work-tree="{t}" clean -fdx',
                 'export GIT_WORK_TREE="{t}"; git clean -fdx')
        for f in forms:
            with self.subTest(f=f):
                r = FC(f.format(t=GAME), _ELSEWHERE)
                self.assertEqual((r["git_wipes_x4_dir"], r["git_wipe_from_session_dir"]),
                                 (True, False), f)

    # --- one falsification twin per clause ---
    def test_TWIN_a_bare_wipe_from_an_x4_dir_keeps_its_deny(self):
        """Clause: the assignment feeds the named ASK only, never the per-segment pairs."""
        self.assertTrue(FC('export GIT_WORK_TREE="' + GAME + '"; git clean -fdx', GAME)
                        ["git_wipe_from_session_dir"])

    def test_TWIN_a_work_tree_outside_every_root_is_silent(self):
        self.assertFalse(FC('export GIT_WORK_TREE="C:/work/x"; git clean -fdx', _ELSEWHERE)
                         ["git_wipes_x4_dir"])

    def test_TWIN_only_a_wipe_reads_the_assignment(self):
        """Clause: `if git_wipes_worktree_targets(s)` -- a status, or a dry run, is not a wipe."""
        for c in ('export GIT_WORK_TREE="' + GAME + '"; git status',
                  'export GIT_WORK_TREE="' + GAME + '"; git clean -n -fdx',
                  'export GIT_WORK_TREE="' + GAME + '"; git reset --soft HEAD~1'):
            with self.subTest(c=c):
                self.assertFalse(FC(c, _ELSEWHERE)["git_wipes_x4_dir"], c)

    def test_TWIN_another_variable_is_not_a_work_tree(self):
        """Clause: the names GIT_WORK_TREE / GIT_DIR."""
        self.assertFalse(FC('export GIT_EDITOR="' + GAME + '"; git clean -fdx', _ELSEWHERE)
                         ["git_wipes_x4_dir"])


class TestG7GitCleanDryRunIsTheLastWord(unittest.TestCase):
    """FX-G7. MEASURED in a scratch repo (git 2.48.1): `git clean -fdx -n --no-dry-run`,
    `-n --no-d`, `-e -n`, `--exclude -n`, `--e -n` each DELETED both files, while the guard
    read each as a dry run (`-n` anywhere, or any prefix of --dry-run). `--d` / `--dry` ARE dry
    runs (deleted nothing) -- an abbreviation git accepts because it prefixes exactly one of
    git clean's long options."""
    FACT = "git_wipe_from_session_dir"

    def test_a_later_negation_or_an_exclude_value_is_not_a_dry_run(self):
        for a in ("-n --no-dry-run", "-n --no-d", "-e -n", "--exclude -n", "--e -n", "-fe -n",
                  "--pathspec-from-file -n"):
            c = "git clean -fdx " + a
            with self.subTest(c=c):
                self.assertTrue(FC(c, GAME)[self.FACT], c)

    def test_an_abbreviation_is_the_option_only_when_unambiguous(self):
        self.assertEqual(H._git_clean_long("--d"), "--dry-run")
        self.assertEqual(H._git_clean_long("--no-d"), "--no-dry-run")
        self.assertEqual(H._git_clean_long("--e"), "--exclude")
        for t in ("--n", "--p", "--pathspec-f", "--x", "--", "-n"):
            with self.subTest(t=t):
                self.assertIsNone(H._git_clean_long(t), t)

    # --- one falsification twin per clause ---
    def test_TWIN_a_real_dry_run_stays_allowed(self):
        for a in ("--d", "--dry", "--dry-run", "-n", "--no-dry-run -n", "--exclude=-n -n",
                  "-e x -n", "-epat -n"):
            c = "git clean -fdx " + a
            with self.subTest(c=c):
                self.assertFalse(FC(c, GAME)[self.FACT], c)

    def test_TWIN_an_ambiguous_prefix_is_not_a_dry_run(self):
        """Clause: `len(hits) == 1`. With a second `--d...` option `--d` is refused by git."""
        saved = H._GIT_CLEAN_LONG_ALL
        H._GIT_CLEAN_LONG_ALL = saved + ("--dummy",)
        try:
            self.assertIsNone(H._git_clean_long("--d"))
            self.assertTrue(FC("git clean -fdx --d", GAME)[self.FACT])
        finally:
            H._GIT_CLEAN_LONG_ALL = saved


class TestH6GitConfigFromTheEnvironment(unittest.TestCase):
    """H6 (MEASURED: deletes): git config from the ENVIRONMENT switches requireForce off."""

    C = 'git -C "' + GAME + '" clean -dx'

    def test_git_config_env_forces_an_unforced_clean(self):
        for pre in ("GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=clean.requireForce GIT_CONFIG_VALUE_0=false ",
                    "GIT_CONFIG_PARAMETERS=\"'clean.requireForce'='false'\" ",
                    "export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=clean.requireForce GIT_CONFIG_VALUE_0=false; ",
                    "GIT_CONFIG_GLOBAL=/tmp/g ", "env GIT_CONFIG_COUNT=1 "):
            with self.subTest(pre=pre):
                self.assertTrue(FC(pre + self.C, _ELSEWHERE)["git_wipes_x4_dir"], pre)
        self.assertTrue(FC('git --config-env=clean.requireForce=V -C "' + GAME + '" clean -dx',
                           _ELSEWHERE)["git_wipes_x4_dir"])

    # --- one falsification twin per clause ---
    def test_TWIN_other_env_or_no_env_leaves_the_clean_unforced(self):
        for pre in ("", "GIT_AUTHOR_NAME=x ", "MY_GIT_CONFIG_X=1 "):
            with self.subTest(pre=pre):
                self.assertFalse(FC(pre + self.C, _ELSEWHERE)["git_wipes_x4_dir"], pre)


class TestJ2GitCleanForcedByConfigIncludeOrCommand(unittest.TestCase):
    """FX-G5 / reviewer J2 item 5: `-c include.path=<file>` (any `include.*`/`includeIf.*`, any
    `--config-env`) reads config the guard cannot see, and an in-command `git config ...
    requireForce` switches it off -- a clean after either counts as forced (an ask)."""

    C = 'git -C "' + GAME + '" clean -dx'

    def test_an_include_or_config_env_forces_the_clean(self):
        for opt in ("-c include.path=C:/w/c.cfg", "-c Include.Path=/w/c", "-c includeIf.gitdir:/x/.path=/w/c",
                    "--config-env include.path=V", "--config-env=core.x=V",
                    # a later `-c` does not undo the include before it
                    "-c include.path=/w/c -c clean.requireForce=true"):
            c = "git " + opt + ' -C "' + GAME + '" clean -dx'
            with self.subTest(c=c):
                self.assertTrue(FC(c, _ELSEWHERE)["git_wipes_x4_dir"], c)

    def test_an_in_command_git_config_of_requireForce_forces_the_clean(self):
        for pre in ('git -C "' + GAME + '" config clean.requireForce false && ',
                    'git -C "' + GAME + '" config --local clean.requireforce 0; ',
                    'git config set clean.requireForce false; ',
                    'git -C "' + GAME + '" config include.path /w/c && '):
            with self.subTest(pre=pre):
                self.assertTrue(FC(pre + self.C, _ELSEWHERE)["git_wipes_x4_dir"], pre)

    def test_ANY_writing_git_config_or_a_moved_HOME_forces_the_clean(self):
        """FX-G6 / reviewer K I2 (MEASURED E2E: allowed): the key has spellings the literal
        `requireForce` match never saw, and a value can arrive through a variable -- so ANY
        `git config` that is not a read forces a clean in the same command; and HOME /
        XDG_CONFIG_HOME move the global config file git reads."""
        G_ = 'git -C "' + GAME + '" config '
        for pre in (G_ + '"clean.require""Force" false && ', G_ + "clean.require" + BS + "Force false && ",
                    "K=clean.requireForce; " + G_ + "$K false && ", G_ + "user.name x && ",
                    G_ + "--unset core.x; ", G_ + "set clean.requireforce false; ",
                    "HOME=/x ", "XDG_CONFIG_HOME=/x ", "env HOME=/x ", "export HOME=/x; ",
                    "GIT_CONFIG_SYSTEM=/x ", "bash -c 'git config a.b c'; "):
            with self.subTest(pre=pre):
                self.assertTrue(FC(pre + self.C, _ELSEWHERE)["git_wipes_x4_dir"], pre)

    # --- one falsification twin per clause ---
    def test_TWIN_a_READ_of_git_config_leaves_the_clean_unforced(self):
        G_ = 'git -C "' + GAME + '" config '
        for pre in (G_ + "--get user.name && ", G_ + "--list; ", G_ + "-l; ", G_ + "--get-all a.b; ",
                    G_ + "--get-regexp a; ", G_ + "--show-origin --list; ", G_ + "get user.name; ",
                    G_ + "list; ", "git config; ", "echo $HOME; ", "MYHOME=/x "):
            with self.subTest(pre=pre):
                self.assertFalse(FC(pre + self.C, _ELSEWHERE)["git_wipes_x4_dir"], pre)
        self.assertFalse(H._git_config_writes("git -c a.b=c log config"))
        self.assertTrue(H._git_config_writes("git -c a.b=c config x y"))

    # --- one falsification twin per clause ---
    def test_TWIN_other_config_leaves_the_clean_unforced(self):
        for c in ("git -c core.fileMode=false -C " + DQ + GAME + DQ + " clean -dx",
                  "git -c user.include=x -C " + DQ + GAME + DQ + " clean -dx",
                  'echo requireForce && ' + self.C,
                  'git log --grep requireForce && ' + self.C):
            with self.subTest(c=c):
                self.assertFalse(FC(c, _ELSEWHERE)["git_wipes_x4_dir"], c)


class TestH7H8TextPipedIntoAShell(unittest.TestCase):
    """H7/H8 (pre-arc): what a shell READS as its program -- an unquoted `echo ... | bash`, or a
    process substitution that is its script -- is commands."""

    def test_echo_or_printf_piped_into_a_shell_is_its_program(self):
        for c in ("echo " + DEL_GAME + " | bash", "echo -n " + DEL_GAME + " | sh",
                  "echo rm -rf " + GAME.replace(" ", BS + " ") + " | bash",
                  "printf '%s" + BS + "n' '" + DEL_GAME + "' | bash",
                  "printf 'rm -rf %s' " + Q + DQ + GAME + DQ + Q + " | bash"):
            with self.subTest(c=c):
                self.assertTrue(F(c)["rm_hits_game"], c)

    def test_a_process_substitution_run_as_a_script_is_its_program(self):
        for v in ("bash", "sh", "source", "."):
            c = v + " <(echo " + DEL_GAME + ")"
            with self.subTest(c=c):
                self.assertTrue(F(c)["rm_hits_game"], c)
        # fuzz-guard (FX-G4): in a case arm the segmenter takes the `)`
        self.assertTrue(F("case x in x) bash <(echo " + DEL_GAME + ") ;; esac")["rm_hits_game"])

    def test_a_quoted_or_pathed_echo_is_still_the_producer(self):
        """fuzz-guard (FX-G4): `"echo"`, `$'echo'` and a Windows path to echo.exe left a quote
        residue that swallowed the program as written."""
        for v in (DQ + "echo" + DQ, "$" + Q + "echo" + Q, DQ + "C:" + BS + "tools" + BS + "echo.exe" + DQ):
            c = v + " " + DEL_GAME + " | bash"
            with self.subTest(c=c):
                self.assertTrue(F(c)["rm_hits_game"], c)
        self.assertTrue(F("printf '%5s' x | bash")["carrier_untranslated"])

    def test_only_the_shells_OWN_options_and_their_values_are_stepped_over(self):
        """FX-G5 / reviewer J2 item 2 (MEASURED: allowed): an `echo -n` INSIDE the substitution
        or a `-n` AFTER the script (its $1) was read as `bash -n`; and `-o pipefail` left
        `pipefail` standing as the script operand."""
        for c in ("bash <(echo -n " + DEL_GAME + ")", "bash <(echo " + DEL_GAME + ") -n",
                  "bash <(echo " + DEL_GAME + ") x -n", "bash -o pipefail <(echo " + DEL_GAME + ")",
                  "bash -O extglob <(echo " + DEL_GAME + ")", "bash +o posix <(echo " + DEL_GAME + ")",
                  "bash -eo pipefail <(echo " + DEL_GAME + ")", "bash -x <(echo " + DEL_GAME + ")",
                  "bash --rcfile /w/rc <(echo " + DEL_GAME + ")",
                  "bash --init-file /w/rc --norc <(echo " + DEL_GAME + ")",
                  "bash -o pipefail -- <(echo " + DEL_GAME + ")", "sh +n <(echo " + DEL_GAME + ")"):
            with self.subTest(c=c):
                self.assertTrue(F(c)["rm_hits_game"], c)

    def test_a_lone_dash_ends_the_options_and_plus_n_cancels_minus_n(self):
        """FX-G6 / reviewer K C2 (REGRESSION of f03d19b, MEASURED E2E deny -> allow): `-` (like
        `--`) ends the options and the NEXT word is the script. K I3 (MEASURED: allowed): `+n`
        turns `-n` off again, so only the LAST n-toggle makes it a syntax check."""
        P = " <(echo " + DEL_GAME + ")"
        for c in ("bash -" + P, "sh -" + P, "bash -x -" + P, "bash --" + P, "bash -o pipefail -" + P,
                  "bash -n +n" + P, "bash -xn +xn" + P, "bash -n +o noexec" + P,
                  "bash -o noexec +n" + P, "sh -n +n" + P, "bash -n -x +n -" + P,
                  # conservative, as at 47d06cd: only `-n` is the exemption (user decision)
                  "bash -o noexec" + P):
            with self.subTest(c=c):
                self.assertTrue(F(c)["rm_hits_game"], c)

    # --- one falsification twin per clause ---
    def test_TWIN_the_last_n_toggle_and_a_script_after_the_dash_decide(self):
        P = " <(echo " + DEL_GAME + ")"
        for c in ("bash +n -n" + P, "bash +xn -xn" + P, "bash +o noexec -n" + P,
                  "bash -n -" + P, "bash -n --" + P,
                  # after `--` the next word is the SCRIPT even when it looks like `+n`
                  "bash -n -- +n" + P,
                  # after `-`, a script FILE is the program and the substitution its argument
                  "bash - ./x.sh" + P, "bash -- ./x.sh" + P):
            with self.subTest(c=c):
                f = F(c)
                self.assertFalse(f["rm_hits_game"] or f["carrier_untranslated"], c)

    # --- one falsification twin per clause ---
    def test_TWIN_a_harmless_program_a_non_shell_or_a_script_argument_is_not(self):
        for c in ("echo ls | bash", "diff <(echo " + DEL_GAME + ") <(echo b)",
                  "bash ./x.sh <(echo " + DEL_GAME + ")", "echo " + DEL_GAME + " | cat",
                  "bash <(echo ls)",
                  # USER DECISION 2026-10-06: a non-echo producer is allowed as before (an
                  # opaque script), and `bash -n` executes nothing -- never ask or deny.
                  "bash <(curl -s http://x)", 'source <(sed -n "/^f()/,/^}/p" install.sh)',
                  "bash -n <(echo " + DEL_GAME + ")", "bash -n <(sed -n 1,9p ci.yml)",
                  # FX-G5 twins: `-n` among the shell's own options, wherever they sit there
                  "bash -o pipefail -n <(echo " + DEL_GAME + ")", "bash -en <(echo " + DEL_GAME + ")",
                  "bash --norc -n <(echo " + DEL_GAME + ")",
                  # ...a value option before a script FILE: the substitution is its argument
                  "bash -o pipefail ./x.sh <(echo " + DEL_GAME + ")",
                  # ...and a non-echo producer behind options stays allowed (user decision)
                  "bash -O extglob <(curl -s http://x)"):
            with self.subTest(c=c):
                f = F(c)
                self.assertFalse(f["rm_hits_game"] or f["carrier_untranslated"], c)


class TestGOutSubstitutedVerb(unittest.TestCase):
    """G-OUT (pre-arc): a verb from a variable whose value is a substitution reached no rule;
    F183: a substituted verb lost the X4-directory delete advisory below a root."""

    def test_a_variable_holding_a_substitution_is_an_unresolved_verb(self):
        for c in ("x=$(printf rm); $x -rf " + DQ + REF + DQ, "x=" + BT + "echo rm" + BT + "; $x -rf " + DQ + GAME + DQ,
                  "x=$(printf rm); $x -rf " + DQ + REF + "/libraries" + DQ,
                  "$(echo rm) -rf " + DQ + REF + "/libraries" + DQ):
            with self.subTest(c=c):
                self.assertTrue(FC(c, _ELSEWHERE)["verb_unresolved"], c)

    def test_TWIN_a_relative_operand_does_not_join_the_SESSION_directory(self):
        """Corpus replay: debris of a substitution (`$(jq -r '.tool_input.command' < f)`) read
        as a verb with a relative operand, joined to a session cwd in the game -> advisory."""
        c = "C=$(jq -r " + Q + ".tool_input.command" + Q + ' < /c/w/p.json); bash -n -c "$C"'
        self.assertFalse(FC(c, GAME)["rm_in_x4_dir"])
        self.assertTrue(FC('cd "' + GAME + '" && $(echo rm) -rf libraries', _ELSEWHERE)["rm_in_x4_dir"])

    def test_a_substituted_verb_below_the_game_root_keeps_the_delete_advisory(self):
        for c in ("$(echo rm) -rf " + DQ + GAME + "/libraries" + DQ, BT + "echo rm" + BT + " -rf " + DQ + GAME + "/x" + DQ):
            with self.subTest(c=c):
                f = FC(c, _ELSEWHERE)
                self.assertTrue(f["rm_in_x4_dir"] and not f["verb_unresolved"], c)

    def test_a_prefix_ASSIGNMENT_does_not_hide_the_verb(self):
        """FX-G5 / reviewer J2 item 3 (MEASURED: allowed): an earlier token holding `$(` refused
        the command position, so `A=$(true) $x` hid the verb; and an UNQUOTED multi-word
        substitution in a prefix assignment (`A=$(echo a b) rm ...`) split into words, one of
        which became the verb -- every hard block bypassed (MEASURED E2E: deny -> allow)."""
        for c in ("x=$(printf rm); A=$(true) $x -rf " + DQ + REF + DQ,
                  "x=$(printf rm); A=" + BT + "true" + BT + " B=$(echo a b) $x -rf " + DQ + REF + DQ,
                  "x=$(printf rm); env A=$(true) $x -rf " + DQ + REF + DQ,
                  "x=$(printf rm); A=" + DQ + "$(echo a b)" + DQ + " $x -rf " + DQ + REF + DQ,
                  "x=$(printf rm); A=$(echo " + DQ + "(" + DQ + ") $x -rf " + DQ + REF + DQ,
                  "x=$(printf rm); sudo -u $(whoami) $x -rf " + DQ + REF + DQ):
            with self.subTest(c=c):
                self.assertTrue(FC(c, _ELSEWHERE)["verb_unresolved"], c)
        for c in ("A=$(echo a b) " + DEL_GAME, "A=" + BT + "echo a b" + BT + " " + DEL_GAME,
                  "A=$(echo $(echo a b) c) B=x " + DEL_GAME, "A=$((1 + 2)) " + DEL_GAME):
            with self.subTest(c=c):
                self.assertTrue(F(c)["rm_hits_game"], c)
        self.assertTrue(F("A=$(echo a b) " + DEL_REF)["rm_targets_reference"])
        self.assertEqual(H.verb("A=$(echo a b) " + D + " -rf x"), D)
        # twin: the collapsed prefix leaves a harmless command harmless
        self.assertFalse(F("A=$(echo a b) ls " + DQ + GAME + DQ)["rm_hits_game"])
        self.assertEqual(H.verb("A=$(echo a b) ls x"), "ls")

    def test_a_word_holding_a_span_is_ONE_word(self):
        """FX-G6 / reviewer K I4 (MEASURED E2E: allowed): a `${...}` with a blank, a substitution
        holding a separator (the segmenter cut it), or a wrapper option's VALUE with a blank
        offered a piece of itself as the command name."""
        for c in ("A=${X:-echo a} " + DEL_GAME, "A=$(echo a; echo b) " + DEL_GAME,
                  "A=$(ls | wc -l) " + DEL_GAME, "A=" + BT + "echo a; echo b" + BT + " " + DEL_GAME,
                  "A=$(echo a && echo b) B=x " + DEL_GAME, "env -C $(echo /tmp ) " + DEL_GAME,
                  "sudo -u $(id -un) " + DEL_GAME, "timeout -s ${S:-KILL x} 5 " + DEL_GAME):
            with self.subTest(c=c):
                self.assertTrue(F(c)["rm_hits_game"], c)
        self.assertTrue(F("A=$(echo a; echo b) " + DEL_REF)["rm_targets_reference"])
        # ...inside a carrier too, whose text resolve() rewrote (`A=echo a rm`): fuzz-guard
        self.assertTrue(F("bash -c " + Q + "A=${X:-echo a} " + DEL_REF + Q)["rm_targets_reference"])
        # the top level's view runs where the top level runs: a `cd` INSIDE the substitution
        # (a subshell) moves nothing, and the relative operand is judged from the session cwd
        self.assertTrue(FC("A=$(cd /x; echo b) " + D + " -rf extensions", GAME)["rm_hits_game"])
        self.assertTrue(FC("x=$(printf rm); env -C $(echo /tmp ) $x -rf " + DQ + REF + DQ,
                           _ELSEWHERE)["verb_unresolved"])
        self.assertEqual(H.verb("env -C $(echo /tmp ) " + D + " x"), D)
        self.assertEqual(H._raw_words("A=${X:-echo a} " + D), ["A=${X:-echo a}", D])

    # --- one falsification twin per clause ---
    def test_TWIN_spans_and_views_change_nothing_else(self):
        # an UNCLOSED span (a segment cut inside one) splits at blanks, as tokens() does
        self.assertEqual(H._raw_words('f=$(find "$P" -name x'), ["f=$(find", '"$P"', "-name", "x"])
        self.assertTrue(H._span_open("f=$(find") and not H._span_open('A=$(echo "(")'))
        # the view collapses only a CLOSED, UNQUOTED span holding a SEPARATOR
        for c in ("A=$(echo a b) x", 'echo "$(a; b)"', "A=$(echo a; b", "echo a; b"):
            with self.subTest(c=c):
                self.assertEqual(H._spanning_view(c), c)
        self.assertEqual(H._spanning_view("A=$(a; b) c"), "A=$(_) c")
        self.assertEqual(H._spanning_view("A=" + BT + "a | b" + BT + " c"), "A=" + BT + "_" + BT + " c")
        # ...and a harmless command behind them stays harmless
        for c in ("A=$(echo a; echo b) ls " + DQ + GAME + DQ, "env -C $(echo /tmp ) ls " + DQ + GAME + DQ,
                  "A=${X:-echo a} ls " + DQ + GAME + DQ):
            with self.subTest(c=c):
                self.assertFalse(F(c)["rm_hits_game"], c)

    # --- one falsification twin per clause ---
    def test_TWIN_a_literal_verb_variable_or_an_operand_elsewhere_is_not_this_rule(self):
        for c in ("x=rm; $x -rf /tmp/a", "x=$(printf ls); $x /tmp/a", "$(echo ls) /tmp/a",
                  '"$X4_PYTHON" ' + DQ + REF + DQ,
                  # the corpus replay's false advisory: a segment cut inside an assignment's
                  # own substitution reads `$P` as its verb -- not a command position
                  'P=$(ls -d "' + GAME + '/extensions/x" 2>/dev/null); f=$(find "$P" -name ' + Q + 'a.lua' + Q
                  + ' -type f 2>/dev/null | head -1); echo "$f"',
                  # ...with an ABSOLUTE operand in that fragment (the session cwd plays no part)
                  'P=$(ls -d /c/w/x); f=$(find "$P" "' + GAME + '/extensions" -name a.lua)',
                  # FX-G5: ...and with the ROOT itself as an operand, the fragment's `$P` (holding a
                  # substitution) is still not a command position -- the UNCLOSED `$(` says so
                  'P=$(printf x); f=$(find "$P" "' + REF + '" -name a.lua | head -1)'):
            with self.subTest(c=c):
                f = FC(c, GAME)                 # run FROM the game root, as the replay's were
                self.assertFalse(f["verb_unresolved"] or f["rm_in_x4_dir"], c)


class TestH9RefguardActionPosition(unittest.TestCase):
    """H9 (IN-ARC regression of ee040bc, MEASURED in the corpus replay): every argument after
    x4refguard.py was tested for a variable, so `apply --toolkit "$X4_TOOLKIT"` ASKED."""

    def test_an_unresolved_ACTION_asks(self):
        for c in ('python scripts/x4refguard.py "$ACT"', "python scripts/x4refguard.py $(echo remove)",
                  "python scripts/x4refguard.py --yes $A", "python scripts/x4refguard.py remove --yes"):
            with self.subTest(c=c):
                self.assertTrue(F(c)["lifts_reference_deny"], c)

    # --- one falsification twin per clause ---
    def test_TWIN_a_variable_in_an_OPTION_value_is_not_the_action(self):
        for c in ('python scripts/x4refguard.py apply --toolkit "$X4_TOOLKIT"',
                  'python scripts/x4refguard.py status --toolkit "$W" --json',
                  '"$X4_PYTHON" scripts/x4refguard.py status --reference "$R"'):
            with self.subTest(c=c):
                self.assertFalse(F(c)["lifts_reference_deny"], c)


class TestHM5TakeownAndHM3FileUrls(unittest.TestCase):
    def test_takeown_of_reference_lifts_its_deny(self):
        """H-M5: taking ownership lets the owner rewrite the ACL, deny included."""
        for c in ('takeown /f "' + REF + '" /r', 'takeown //f "' + REF + '/libraries/w.xml"',
                  'takeown /F "' + TOOLKIT + '" /R /D Y', 'takeown /f "$X4_REFERENCE" /r'):
            with self.subTest(c=c):
                self.assertTrue(FC(c, _ELSEWHERE)["lifts_reference_deny"], c)

    def test_TWIN_takeown_elsewhere_or_of_an_ancestor_without_r(self):
        for c in ('takeown /f "' + TOOLKIT + '"', 'takeown /f "' + GAME + '" /r', "takeown /f C:/tmp/x /r"):
            with self.subTest(c=c):
                self.assertFalse(FC(c, _ELSEWHERE)["lifts_reference_deny"], c)

    def test_a_percent_encoded_file_url_is_decoded(self):
        """H-M3: `%20` hid the space, so the game root was a sibling path (advise, not deny)."""
        url = "file://" + GAME.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
        self.assertTrue(F("gio trash '" + url + "'")["rm_hits_game"])
        self.assertEqual(H._url_path("file:///C:/a%20b"), "C:/a b")
        self.assertEqual(H._url_path("C:/a%20b"), "C:/a%20b")       # TWIN: not a URL, literal


class TestF10DynamicProgramWord(unittest.TestCase):
    """F10 (v4.0): a command NAME the guard cannot identify -- a variable (`$R`, `"$R"`,
    `${R}`, `${A[0]}`), a substitution, cmd's `%R%` / `!R!` -- reached no rule, so with the
    name held in a variable each of these REALLY deletes (MEASURED in the Skyrim sibling on
    Windows) and the guard ALLOWED every one (MEASURED here, 125 of 125 shapes). Its words are
    now read speculatively (wrapper, eval, git) and judged like the plain command, and a name
    that may itself BE the delete asks when it is given a protected path."""

    def test_the_words_after_an_unknown_name_are_judged_as_a_command(self):
        for pre in ("$R ", DQ + "$R" + DQ + " ", "${R} ", "${A[0]} ", "nice $R ", "$A $B $C ",
                    "$A $B $C $D $E $F ",          # past the carrier walk's depth (4)
                    "R=$(printf eval); $R ", "$(echo command) "):
            with self.subTest(pre=pre):
                self.assertTrue(F(pre + DEL_REF)["rm_targets_reference"], pre)
                self.assertTrue(F(pre + DEL_GAME)["rm_hits_game"], pre)

    def test_a_quoted_word_after_an_unknown_name_is_read_as_eval_reads_it(self):
        """`$R 'rm -rf X'` with R=eval: the quoted word is ONE token, so reading only "the
        next words as the command" misses it (the Skyrim sibling's lesson)."""
        for pre in ("$R ", "$(echo eval) ", "R=$(printf eval); $R ", "$A $B "):
            with self.subTest(pre=pre):
                self.assertTrue(F(pre + Q + DEL_REF + Q)["rm_targets_reference"], pre)

    def test_a_name_this_command_assigns_reaches_the_carrier_it_names(self):
        self.assertTrue(F("R=eval; $R " + Q + DEL_REF + Q)["rm_targets_reference"])
        self.assertTrue(F("R=bash; $R -c " + Q + DEL_GAME + Q)["rm_hits_game"])

    def test_an_unknown_name_before_git_add_all(self):
        for c in ("$R git add -A", "$R add -A", "$G stage .", "$R " + Q + "git add -A" + Q):
            with self.subTest(c=c):
                self.assertTrue(F(c)["git_add_all"], c)

    def test_the_reading_keeps_the_directory_a_cd_set(self):
        """A reading carried ON ITS OWN has no directory once anything changes one, so
        `cd <game> && $R rm -rf extensions` lost the hard block the plain spelling gets."""
        c = "cd " + DQ + GAME + DQ + " && $R " + D + " -rf extensions"
        self.assertTrue(F(c)["rm_hits_game"])
        self.assertTrue(F("cd " + DQ + GAME + DQ + " && $R " + Q + D + " -rf extensions" + Q)["rm_hits_game"])

    def test_cmd_percent_and_delayed_expansion_program_words(self):
        for payload in ("%R% del " + REF + "/a.xml", "!R! del " + REF + "/a.xml",
                        "%R%del " + REF + "/a.xml", "%R% rd /s /q " + REF,
                        "%A% %B% del " + REF + "/a.xml"):
            with self.subTest(payload=payload):
                for carrier in ("cmd //c ", "cmd //v:on //c "):
                    self.assertTrue(F(carrier + DQ + payload + DQ)["rm_targets_reference"], payload)
        self.assertIn("rm -f x", H.cmd_to_sh(["%R% del x"]))
        self.assertIn("rm -f x", H.cmd_to_sh(["%R%del x"]))
        self.assertIn("rm -f x", H.cmd_to_sh(["!R! del x"]))
        # `!NAME!` in an OPERAND is a variable too (delayed expansion), never literal text
        self.assertEqual(H.cmd_to_sh(["type !F!"]), 'cat "${F}"')
        self.assertTrue(F("cmd //c " + DQ + "%R% git add -A" + DQ)["git_add_all"])

    def test_an_unknown_name_given_a_protected_path_asks(self):
        for c in ("$R -rf " + DQ + GAME + DQ, DQ + "$R" + DQ + " " + DQ + PROF + "/content.xml" + DQ,
                  "$R " + DQ + PROF + "/save/a.xml.gz" + DQ, "${R} " + DQ + REF + "/libraries" + DQ,
                  "$R -rf " + DQ + "$X4_GAME" + DQ, "cd " + DQ + GAME + DQ + " && $R -rf extensions",
                  "cmd //c " + DQ + "%R% " + REF + "/a.xml" + DQ,
                  # the root as TEXT inside a word (R = a PowerShell host): no path reading sees it
                  "$R -c " + Q + "Remove-Item " + REF + "/libraries" + Q):
            with self.subTest(c=c):
                self.assertTrue(F(c)["program_dynamic_protected"], c)

    def test_TWIN_an_unknown_name_away_from_protected_paths_is_silent(self):
        for c in ("$R --version", DQ + "$PY" + DQ + " -c " + DQ + "import os; print(os.getcwd())" + DQ,
                  "$R " + D + " -rf ./build", "$JQ -r .x data.json", "$UV run pytest -q",
                  "cmd //c " + DQ + "%R% --version" + DQ, "R=" + D + "; $R -rf ./build",
                  DQ + "$X4_GAME/X4.exe" + DQ + " -debug all", "$R " + Q + "echo hi" + Q):
            with self.subTest(c=c):
                f = F(c)
                self.assertFalse(f["program_dynamic_protected"], c)
                self.assertFalse(f["rm_targets_reference"] or f["rm_hits_game"] or f["git_add_all"], c)

    def test_TWIN_a_known_name_given_a_protected_path_is_not_this_rule(self):
        """A name this command assigns (`R=cat`), or a ROOT variable naming a program, is
        identifiable: the ordinary rules judge it, and this one stays silent."""
        self.assertFalse(F("R=cat; $R " + DQ + REF + "/a.xml" + DQ)["program_dynamic_protected"])
        self.assertFalse(F(DQ + "$X4_GAME/X4.exe" + DQ + " " + DQ + PROF + "/content.xml" + DQ)
                         ["program_dynamic_protected"])
        # a segment CUT inside a substitution is no command position: `$P` there is find's
        # operand, and the substitution itself is walked (and judged) on its own
        self.assertFalse(F("f=$(find " + DQ + "$P" + DQ + " " + DQ + REF + DQ + " -name x | head -1)")
                         ["program_dynamic_protected"])

    def test_TWIN_many_unknown_names_do_not_multiply_into_a_truncation(self):
        """One variant per reading, never one per combination: a script of `"$PY" ...` lines
        must not walk into the carrier bound and be refused for its length."""
        c = "; ".join(DQ + "$PY" + DQ + " s%d.py " % i + DQ + "$A" + DQ + " " + DQ + "$B" + DQ
                      + " 'x y'" for i in range(12))
        f = F(c)
        self.assertFalse(f["carriers_truncated"])
        self.assertFalse(f["program_dynamic_protected"])

    def test_readings_are_speculative_and_never_fail_the_parse(self):
        """A reading that is not shell (`-c "<python>"` read as shell) matches no rule and
        changes no other verdict -- lesson 1 of the sibling: it must not abort the parse."""
        c = DQ + "$PY" + DQ + " -c " + DQ + "import os; print(os.listdir('(')); x = [1, 2" + DQ
        f = F(c)
        self.assertFalse(any(f[k] for k in ("rm_targets_reference", "rm_hits_game", "git_add_all",
                                            "program_dynamic_protected", "carriers_truncated")))


@unittest.skipUnless(_PWSH, "no PowerShell on this machine: the PowerShell front-end cannot be "
                            "exercised here (it fails closed without one)")
class TestF10PowerShellUnreadableCommandName(unittest.TestCase):
    """F10 (v4.0): `& $R`, `. $R`, `& $R '<text>'`, `& "$($R)-Item"` -- MEASURED in the Skyrim
    sibling to really delete with R = powershell / Invoke-Expression / Remove -- were
    translated with the name as a literal, so no rule knew a delete was possible."""

    def P(self, command):
        return H.facts({"tool_name": "PowerShell", "tool_input": {"command": command}}, ROOTS)

    def test_each_unreadable_name_shape_reaches_the_plain_commands_rule(self):
        for c in ("& $R Remove-Item " + Q + REF + Q, ". $R Remove-Item " + Q + REF + Q,
                  "& $R " + DQ + "Remove-Item " + Q + REF + Q + DQ,
                  "& " + DQ + "$($R)-Item" + DQ + " " + Q + REF + Q,
                  "& $env:R Remove-Item " + Q + REF + Q,
                  "& (Get-Command $n) Remove-Item " + Q + REF + Q,
                  "& $R rm -rf " + Q + REF + Q):
            with self.subTest(c=c):
                self.assertTrue(self.P(c)["rm_targets_reference"], c)
        self.assertTrue(self.P("& $R git add -A")["git_add_all"])
        # a host this command NAMES through a variable is spliced in before the carrier arms:
        # `-c` is no host-only spelling, so no speculative reading would find this one
        f = F("R=powershell; $R -c " + DQ + "Remove-Item " + Q + REF + Q + DQ)
        self.assertTrue(f["rm_targets_reference"])
        self.assertTrue(self.P("& $R " + Q + "git add -A" + Q)["git_add_all"])

    def test_an_unreadable_name_given_a_protected_path_asks(self):
        self.assertTrue(self.P("& $R " + Q + PROF + "/content.xml" + Q)["program_dynamic_protected"])
        self.assertTrue(self.P("& (Get-Command $n) " + Q + GAME + Q)["program_dynamic_protected"])

    def test_TWIN_readable_names_and_harmless_unreadable_ones(self):
        for c in ("& $exe --version", "& $py -m pytest -q tests", "& $exe build --out ./out",
                  "& 'git' status", "& $py -c " + DQ + "print(1)" + DQ):
            with self.subTest(c=c):
                f = self.P(c)
                self.assertFalse(f.get("powershell_error"), c)
                self.assertFalse(f["program_dynamic_protected"] or f["rm_targets_reference"]
                                 or f["carrier_untranslated"], c)
        # a name this command assigns literally is READ: not this rule. (Its parameters do not
        # bind statically, so the translator already reports the target unresolved -> ask.)
        f = self.P("$n = 'Remove-Item'; & $n " + Q + REF + Q)
        self.assertFalse(f["program_dynamic_protected"])
        self.assertTrue(f["carrier_untranslated"])


def load_tests(loader, standard_tests, pattern):
    """unittest.main() collects TestCase SUBCLASSES ONLY, so every module-level
    `def test_*` in this file was invisible to it.

    MEASURED by the v3.1.0 release reviewer: the runner CI uses
    (`python .claude/hooks/test_hook_facts.py`, ci.yml:122) reported **403 tests, OK**
    while pytest reported **433**. The 30 in the gap were added by this arc and are the
    tests for its headline work -- the resolve() size ceiling, the ANSI-C hex/octal
    bypass, and four hard-block bypasses. Reverting the wrapper-value fix left CI
    printing "Ran 403 tests ... OK" while pytest named the four failures.

    `verify-hook-tests.py` drives the same runner, so its "baseline: 403 tests green"
    and its 0-of-82 mutation result were computed over a population that excluded the
    very tests those mutants target.

    This is the unittest load_tests protocol: it wraps each module-level function so
    BOTH runners see the same 433. None of them takes a fixture argument, which is what
    makes FunctionTestCase sufficient; a test that grows one will fail loudly here
    rather than vanish.
    """
    import types
    for name, obj in sorted(globals().items()):
        if name.startswith("test_") and isinstance(obj, types.FunctionType):
            standard_tests.addTest(unittest.FunctionTestCase(obj, description=name))
    return standard_tests


if __name__ == "__main__":
    unittest.main(verbosity=2)
