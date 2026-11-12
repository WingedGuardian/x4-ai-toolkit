#!/usr/bin/env python3
"""Prove the hook tests can FAIL, and that every guard rule is probed in both directions.

    python scripts/verify-hook-tests.py

`test_hook_facts.py` passing tells you the code agrees with the tests. It does not tell
you the tests could ever have disagreed. This repository has shipped several guards that
were inert for entire releases while their suite was green, so a green result is only
evidence once the red result is reachable (CLAUDE.md "if you cannot make a check fail on
purpose, you have not verified anything").

Two passes, both on a COPY of the hooks -- never the working tree. A mutation gate that
edits real files makes them deliberately wrong for the duration, and a release port once
read a mutated file and shipped it.

  MUTATION  plant one specific defect; the NAMED test for it must go red. Requiring a
            named test, not merely "something failed", is what stops a mutation that
            breaks the import from counting as coverage for anything.

  COVERAGE  pin each predicate false -- a must-FIRE test must break; pin it true -- a
            must-NOT-fire test must break. This is how "every rule is probed both ways"
            becomes a measurement instead of a claim about the test file.

Exit 0 if both pass, 1 if either finds a hole, 2 if the baseline is not green (in which
case neither result means anything and no verdict is given).

Two narrower runs, neither of which is ever a release pass:

  --check-anchors  no trials at all: every mutant's anchor appears exactly once and every target
                   names one test. A stale anchor refuses the whole gate at the start of its CI
                   run (5 of them did at the 4.1.0 candidate), so this is cheap enough for the
                   pre-push gate and the unit suite.
  --only REGEX     the mutants whose label matches, run against their target test's class only,
                   and no predicate pairs. For the code you just changed; exit 3 when all are
                   caught. The whole table still runs on CI.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import argparse
import json
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

#: A literal newline, built from its byte value. Multi-line mutant
#: anchors need one, and an escape written here would cross a tool
#: boundary that has collapsed one into a control byte before now.
NL = chr(10)

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / ".claude" / "hooks"
# ps_translate.ps1 travels with them: hook_facts.powershell_to_sh runs it from its OWN
# directory, so a copy without it makes every PowerShell test a refusal and the baseline
# red for a reason that has nothing to do with the code under test.
# The mod-list exemption uses the native-path bridge beside its parser. Keep
# the isolated baseline self-contained instead of mistaking a missing helper
# for a production failure or a caught mutant.
FILES = ("hook_facts.py", "test_hook_facts.py", "ps_translate.ps1", "save_policy.py")

# (label, exact source text, replacement, the test that MUST go red)
MUTANTS = [
    ("a leading ~ / $HOME is expanded",
     "    return expand_home(out)", "    return out",
     "test_all_home_spellings_reach_the_same_verdict_as_the_absolute_form"),
    ("only a LEADING home reference is expanded",
     'if tok == "~" or (tok[:1] == "~" and tok[1:2] in _SEPS):',
     'if "~" in tok:',
     "test_only_a_LEADING_home_reference_is_expanded"),
    # --- wrappers that carry a command as TEXT ---------------------------------
    ("a shell -c flag CLUSTER is unwrapped",
     r'_DASH_C = re.compile(r"^-[A-Za-z]*c[A-Za-z]*$")',
     r'_DASH_C = re.compile(r"^-c$")',
     "test_every_shell_c_spelling_is_unwrapped"),
    ("eval carries a command",
     '        elif v == "eval":', '        elif False:',
     "test_eval_is_unwrapped"),
    # --- reserved words, one mutant per clause ---------------------------------
    # The fuzzer found this class; these keep it found. Each clause is mutated on its
    # own: a single mutant of the whole helper cannot distinguish "this clause is
    # untested" from "an earlier clause returns before it".
    ("reserved words leave the segment",
     '        if head and head[0] in RESERVED:', '        if False:',
     "test_every_compound_form_still_shows_the_command"),
    ("`case WORD in` is consumed",
     '        if head and head[0] == "case":', '        if False and head:',
     "test_every_compound_form_still_shows_the_command"),
    ("a function header is consumed",
     '        m = _FUNC_HEAD.match(s)', '        m = None',
     "test_every_compound_form_still_shows_the_command"),
    # The regression the fix itself introduced: the ORIGINAL over-wide label regex,
    # which ate `rm -rf extensions)` out of a process substitution. The NO-WHITESPACE
    # class is the whole safety property -- the trailing `\s` never was, which is why
    # dropping it on 2026-09-04 to catch `*)cmd` was safe. If this mutant ever stops
    # being caught, that hole is open again.
    ("a case label carries no whitespace",
     r'_CASE_ARM = re.compile(r"^\(?[^\s()&;]+\)")',
     r'_CASE_ARM = re.compile(r"^\(?[^()|&;]*\)")',
     "test_a_process_substitution_tail_is_not_a_case_arm_label"),
    # --- the PARSER, one mutant per clause -------------------------------------
    # Added 2026-09-01. The gate reported "31 of 31 caught, 0 of 19 predicate gaps"
    # while three total-guard bypasses were live, because every mutant targeted a
    # PREDICATE and none targeted the parse pass that feeds them. `<<` opens a skip
    # region, so one wrong marker blanks the rest of the command and every rule below
    # goes silent. Each clause is mutated SEPARATELY: a single mutant of the whole
    # condition cannot tell "this clause is untested" from "an earlier clause shadows it".
    ("a here-string `<<<` is not a heredoc",
     'if i > 0 and line[i - 1] == chr(60):\n                continue',
     'if False:\n                continue',
     "test_a_here_string_opens_no_heredoc"),
    ("a `<<` in a COMMENT is not a heredoc",
     "stop = _comment_start(line, mask)", "stop = len(line)",
     "test_a_double_angle_in_a_COMMENT_opens_no_heredoc"),
    ("an arithmetic left-shift is not a heredoc",
     "line = _blank_arith(line, mask)", "line = line",
     "test_an_arithmetic_left_shift_opens_no_heredoc"),
    # ...and the other direction: the exclusions must not swallow REAL heredocs, or the
    # body stripping dies silently and heredoc text is read as commands.
    ("the exclusions must not kill real heredocs",
     "            m = _HD.match(line, i)", "            m = None",
     "test_a_real_heredoc_is_still_recognised"),
    ("rg/ag recurse by default", '"rg": True, "ag": True, "ack": True',
     '"rg": False, "ag": False, "ack": False', "test_rg_is_recursive_BY_DEFAULT"),
    ("game-delete name backstop",
     'or rm_named_game),\n        "rm_targets_reference"',
     'or False),\n        "rm_targets_reference"',
     "test_unconfigured_root_falls_back_to_the_NAME"),
    # The hard block must stay scoped to the catastrophic cases. Widening it back to
    # "anything under the tree" hard-denies the documented deploy path -- measured over a
    # 1,000-command sample, all 4 hits were exactly that.
    # These anchor on the equality test inside hits_game_root. It grew a glob arm in
    # 2026-09-04, and the mutants went STALE rather than surviving -- the harness said
    # "DID NOT APPLY (0 matches)" instead of counting them as caught, which is the only
    # reason the change did not silently lose two mutants.
    ("hard block is ROOT-scoped, not under-scoped",
     'if n == g or n == g + "/extensions":', "if n.startswith(g):",
     "test_deleting_ONE_deployed_mod_is_NOT_a_hard_block"),
    ("extensions/ wholesale is still a hard block",
     'if n == g or n == g + "/extensions":', "if n == g:",
     "test_deleting_extensions_WHOLESALE_is_a_hard_block"),
    ("a glob operand cannot walk past the game hard block",
     '        return (glob_covers(n, g, expand_braces=norm(_raw) in brace_patterns)\n'
     '                or glob_covers(n, g + "/extensions", expand_braces=norm(_raw) in brace_patterns))',
     "        return False",
     "test_a_trailing_glob_is_still_the_installation"),
    ("a glob operand cannot walk past a ROOT hard block",
     "            elif glob_covers(norm(path), norm(root), expand_braces=norm(raw) in brace_patterns):",
     "            elif False:",
     "test_glob_suffix_on_reference_is_still_the_reference"),
    ("the name backstop is ROOT-anchored",
     r'GAME_ROOTISH = re.compile(r"(x4 foundations|egosoft/x4)(/extensions)?$", re.I)',
     r'GAME_ROOTISH = re.compile(r"(x4 foundations|egosoft/x4)", re.I)',
     "test_unconfigured_backstop_does_not_catch_a_mod_folder"),
    ("heredoc << must be OUTSIDE quotes",
     "if line[i] == chr(60) and line[i + 1] == chr(60) and not mask[i] and not mask[i + 1]:",
     "if line[i] == chr(60) and line[i + 1] == chr(60):",
     "test_marker_inside_quotes_does_NOT_open_a_skip"),
    # Anchored on strip_heredocs' own call: heredoc_bodies() added a SECOND
    # `t = heredoc_marker(line)` and the bare line now matches twice.
    # Re-anchored FX-G2 (E1): strip_heredocs, heredoc_bodies and heredoc_substitutions now
    # share ONE walk, _heredoc_walk, which carries the quote state between lines.
    ("the heredoc marker may itself be QUOTED",
     "        opens = _heredoc_opens(line, q)",
     "        opens = _heredoc_opens(blank_quoted(line), q)",
     "test_a_QUOTED_marker_still_opens_a_heredoc"),
    ("noclobber >| is a redirect", r'_REDIR = re.compile(r"(\d?)>(\|?)(>?)")',
     r'_REDIR = re.compile(r"(\d?)>()(>?)")', "test_noclobber_override_is_a_truncate"),
    # Re-anchored v4.0 (CI run 39187589960): copy_dests gained a `--` stop and judges a
    # quoted -t too (the shell strips the quotes). Same clause: the -t destination lookup.
    ("cp/mv -t destination",
     'toks = tokens(seg)\n    for i, (t, quoted) in enumerate(toks):\n'
     '        if t == "--":\n            break\n'
     '        if t in ("-t", "--target-directory"):',
     'toks = tokens(seg)\n    for i, (t, quoted) in enumerate(toks):\n'
     '        if t == "--":\n            break\n'
     '        if False:',
     "test_dash_t_names_the_destination"),
    # Re-anchored 2026-09-06: B10 hoisted the name out (`name = _verb_name(t)`) so the
    # wrapper's identity could key `_WRAPPER_VALUE_OPTS`. The old anchor stopped
    # matching and the harness said so rather than passing -- the third orphaned anchor
    # this session, and the third time refusing beat reporting a clean run.
    ("wrapper verbs are seen through",
     "        if name in WRAPPERS:\n            seen_wrapper = True",
     "        if False:\n            seen_wrapper = True",
     "test_sees_through_a_wrapper"),
    ("dot-segment canonicalisation", "s = posixpath.normpath(s)", "s = s",
     "test_dot_segment_is_canonicalised"),
    ("-e consumes its argument", 'if base in _ARG_FLAGS and "=" not in t:\n                skip = True',
     "if False:\n                skip = True",
     "test_dash_e_supplies_the_pattern_so_the_path_is_not_consumed"),
    ("tee writes EVERY file operand", 'return ops if v == "tee" else [ops[-1]]',
     "return [ops[-1]]", "test_tee_writes_EVERY_file_operand"),
    ("last assignment wins", "found[m.group(1)] = m.group(2)",
     "found.setdefault(m.group(1), m.group(2))", "test_last_assignment_wins"),
    # --- delete verbs and timeout shapes (2026-09-01) -----------------------------
    ("find -delete is a delete", "    return trash_paths(seg) or find_deletes(seg)",
     "    return trash_paths(seg)",
     "test_find_delete_on_the_game_is_a_game_delete"),
    ("FX-G2 item 7: a move to the trash is a delete",
     "    return trash_paths(seg) or find_deletes(seg)", "    return find_deletes(seg)",
     "test_every_trash_form_judges_like_rm"),
    ("a FILTERED find is scoped, not a tree delete",
     "    filtered = any(_narrows(toks, i) for i, t in enumerate(toks) if t in _FIND_FILTERS)",
     "    filtered = False",
     "test_a_FILTERED_find_is_scoped_and_does_not_fire"),
    # AUDIT-2026-09-24 HK-2: a filtered find-delete is SCOPED, not exempt ...
    ("a filtered find-delete still reaches the in-tree rules",
     "        scoped_rm_t += prep(find_scoped_deletes(s), c_cwd, c_old, operation=\"delete\", scope=\"filtered-tree\")",
     "        pass",
     "test_every_save_by_filter_asks"),
    # ... except a regenerable-cache cleanup, which is why the exemption existed.
    ("a cache-only filter stays exempt",
     "    return [] if _only_regenerable(toks) else paths", "    return paths",
     "test_TWIN_a_pycache_cleanup_is_still_silent_everywhere"),
    ("truncate / dd of= are truncating writes",
     "        redir_t += [(\"truncate\",) + o for o in prep(clobber_targets(s), c_cwd, c_old, False, operation=\"write\")]",
     "        pass",
     "test_truncate_into_reference"),
    ("a find WITHOUT -delete is not", 'deletes = "-delete" in toks', "deletes = True",
     "test_a_find_that_does_NOT_delete_is_not_a_delete"),
    # Anchors re-cut for a825d86, which put `not codex_async_shell and` in front of the cap.
    ("a non-int timeout still counts",
     '"timeout_over_cap": not codex_async_shell and _as_ms(timeout) > (7200000 if background is True else 600000),',
     '"timeout_over_cap": not codex_async_shell and isinstance(timeout, int) and timeout > (7200000 if background is True else 600000),',
     "test_a_float_over_the_cap_fires"),
    # 2026-10-02: a background call has its own 2 h cap. The mutant drops it, so the
    # foreground cap judges background calls again -- the false deny that was fixed.
    ("a background call has the background cap",
     '"timeout_over_cap": not codex_async_shell and _as_ms(timeout) > (7200000 if background is True else 600000),',
     '"timeout_over_cap": not codex_async_shell and _as_ms(timeout) > 600000,',
     "test_a_BACKGROUND_call_has_the_background_cap"),
    # a825d86: a Codex shell call is exempt from the two Claude scheduling facts. One mutant
    # per clause: each fact's exemption, the exact host value, and where the host is read.
    ("a Codex shell is not held to the Bash timeout cap",
     '"timeout_over_cap": not codex_async_shell and _as_ms(timeout) > (7200000 if background is True else 600000),',
     '"timeout_over_cap": _as_ms(timeout) > (7200000 if background is True else 600000),',
     "test_a_codex_shell_is_not_held_to_the_bash_timeout_cap"),
    ("a Codex shell long job is not a foreground long job",
     '"longjob_foreground": longjob and background is not True and not codex_async_shell,',
     '"longjob_foreground": longjob and background is not True,',
     "test_a_codex_shell_long_job_is_not_a_foreground_long_job"),
    ("only the codex host is exempt",
     'codex_async_shell = payload.get("x4_execution_host") == "codex"',
     'codex_async_shell = bool(payload.get("x4_execution_host") or True)',
     "test_TWIN_only_the_codex_host_is_exempt_from_the_scheduling_facts"),
    ("the host is read from the payload top level, never tool_input",
     'codex_async_shell = payload.get("x4_execution_host") == "codex"',
     'codex_async_shell = (payload.get("x4_execution_host") or inp.get("x4_execution_host")) == "codex"',
     "test_TWIN_the_command_payload_cannot_claim_the_codex_host"),
    # 2026-10-26 (user): under a Codex shell the two scheduling facts become ADVISORIES, never a
    # silent allow. One mutant per clause of each Codex advisory fact.
    ("a Codex shell long job is advised",
     '"longjob_codex": longjob and background is not True and codex_async_shell,',
     '"longjob_codex": False,',
     "test_a_codex_shell_long_job_is_ADVISED"),
    ("only a Codex shell long job is advised",
     '"longjob_codex": longjob and background is not True and codex_async_shell,',
     '"longjob_codex": longjob and background is not True,',
     "test_TWIN_only_the_codex_host_is_exempt_from_the_scheduling_facts"),
    ("a backgrounded Codex long job is not advised",
     '"longjob_codex": longjob and background is not True and codex_async_shell,',
     '"longjob_codex": longjob and codex_async_shell,',
     "test_TWIN_a_backgrounded_codex_long_job_is_not_advised"),
    ("a Codex shell over the cap is advised",
     '"timeout_over_cap_codex": codex_async_shell and _as_ms(timeout) > (7200000 if background is True else 600000),',
     '"timeout_over_cap_codex": False,',
     "test_a_codex_shell_over_the_cap_is_ADVISED"),
    ("only a Codex shell over the cap is advised",
     '"timeout_over_cap_codex": codex_async_shell and _as_ms(timeout) > (7200000 if background is True else 600000),',
     '"timeout_over_cap_codex": _as_ms(timeout) > (7200000 if background is True else 600000),',
     "test_TWIN_only_the_codex_host_is_exempt_from_the_scheduling_facts"),
    ("the Codex advisory needs a timeout ABOVE the cap",
     '"timeout_over_cap_codex": codex_async_shell and _as_ms(timeout) > (7200000 if background is True else 600000),',
     '"timeout_over_cap_codex": codex_async_shell,',
     "test_TWIN_a_codex_shell_without_a_long_job_or_over_cap_timeout_is_not_advised"),
    # 2026-10-27 (user): removing a DEPLOYED MOD asks, once per command, naming every mod.
    # One mutant per clause of _deployed_mods / rm_deployed_mod.
    ("removing a deployed mod is rm_deployed_mod",
     '"rm_deployed_mod": bool(_mods),', '"rm_deployed_mod": False,',
     "test_removing_a_deployed_mod_fires_rm_deployed_mod"),
    ("only a path under extensions/ is a deployed mod",
     "                if n.startswith(root):", '                if n.startswith(_g + "/"):',
     "test_TWIN_other_game_install_deletes_are_not_a_deployed_mod"),
    ("several targets name each mod ONCE",
     "    _mods = list(dict.fromkeys(_deployed_mods(", "    _mods = list((_deployed_mods(",
     "test_one_command_names_EVERY_deployed_mod_once"),
    ("the mod list is capped at ten",
     '    _mod_list = ", ".join(_mods[:10])', '    _mod_list = ", ".join(_mods)',
     "test_the_mod_list_is_CAPPED_at_ten"),
    ("a substituted verb reaches the deployed-mod rule",
     "_deployed_mods(rm_t + mv_src + subst_rm_t)", "_deployed_mods(rm_t + mv_src)",
     "test_a_substituted_verb_on_a_deployed_mod_is_rm_deployed_mod"),
    ("a filtered find over extensions/ reaches any mod",
     "                elif filtered and n == root[:-1]:", "                elif False:",
     "test_a_filtered_find_over_extensions_names_any_matching_mod"),
    ("find | xargs over extensions/ reaches any mod",
     '                if seg == "${xargs_item}":', "                if False:",
     "test_a_filtered_find_over_extensions_names_any_matching_mod"),
    # 8957579 / 2359217: an interpreter reading a heredoc as its PROGRAM is scanned for a
    # transcript write. The scan itself, and each way a body can feed the interpreter.
    ("a heredoc program is scanned for a transcript write",
     "    transcript_inline = _heredoc_program_writes_transcript(spliced, base)",
     "    transcript_inline = False",
     "test_a_heredoc_program_writing_a_transcript_fires"),
    # Anchored on the transcript scan's own sink line: the opener test also appears in the
    # F331 link heredoc scan, so the bare line matched twice (rc5, 2026-10-27).
    ("only a body that feeds the interpreter is its program",
     "        return _reads_program_from_stdin(_unwrap(sg))\n"
     "    for opener, _t, _e, body in _heredoc_walk(spliced)[0]:\n"
     "        if _opener_feeds(opener, sink) or _flows_to(opener, sink):",
     "        return _reads_program_from_stdin(_unwrap(sg))\n"
     "    for opener, _t, _e, body in _heredoc_walk(spliced)[0]:\n"
     "        if True:",
     "test_TWIN_a_heredoc_that_is_not_an_interpreter_program_is_not"),
    ("a heredoc on the interpreter's own line feeds it",
     "        return _reads_program_from_stdin(_unwrap(sg))\n"
     "    for opener, _t, _e, body in _heredoc_walk(spliced)[0]:\n"
     "        if _opener_feeds(opener, sink) or _flows_to(opener, sink):",
     "        return _reads_program_from_stdin(_unwrap(sg))\n"
     "    for opener, _t, _e, body in _heredoc_walk(spliced)[0]:\n"
     "        if _flows_to(opener, sink):",
     "test_a_heredoc_program_writing_a_transcript_fires"),
    # NOT MUTATED: dropping `or _flows_to(opener, sink)` is EQUIVALENT today (MEASURED: the
    # mutant survived the whole file, 0 failed). On the opener line alone _flows_to tests the
    # piped-in subset of the very segments _opener_feeds already tests, so it can never add a
    # verdict; `cat <<'EOF' | python -` is caught by _opener_feeds (its `python -` segment).
    # NOT MUTATED: the bool guard in _as_ms is BEHAVIOURALLY EQUIVALENT today --
    # float(True) is 1.0, which is under the cap either way, so removing it cannot
    # change a verdict. Kept as a guard because Python treats True as an int and a
    # future cap of 0 or 1 would make it load-bearing; not mutated, because a mutant
    # that can never be caught sits here permanently red and trains everyone to skim
    # this table.

    # --- prose must not blind the guard (C1, 2026-09-01) --------------------------
    # One apostrophe in an English comment disabled EVERY rule after it. Four separate
    # clauses had to change; each gets its own mutant, because mutating the feature as a
    # whole cannot tell which clause a test is actually exercising.
    ("a backslash outside quotes escapes the next char",
     # Re-anchored FX-G2 (E5): the walk is _qwalk now, shared by _scan and the masks.
     "        elif c == chr(92) and i + 1 < n:\n            yield i, c, False, \"\"\n            i += 1\n            yield i, s[i], False, \"\"          # escaped: never opens a quote",
     "        elif False:\n            yield i, c, False, \"\"\n            i += 1\n            yield i, s[i], False, \"\"",
     "test_an_escaped_apostrophe_does_not_blind_the_next_command"),
    ("comments are stripped before parsing",
     "    body = strip_comments(strip_heredocs(spliced))",
     "    body = strip_heredocs(cmd)",
     "test_comment_apostrophe_does_not_hide_a_game_delete"),
    # Re-anchored 2026-09-06. This used to mutate `stripped = body`, the input to the
    # longjob loop. B4 moved that loop onto `all_cmds` -- which is the correct input,
    # since `body` still carries wrappers -- and that left `stripped` DEAD, so the old
    # mutant changed nothing and its target test stayed green. The gate caught the
    # orphan; the two dead locals are now gone and this points at the real input.
    ("the string rules read the CLEANED text, not the raw command",
     "    for c in all_cmds:" + chr(10) + "        for s in segments(c):",
     "    for c in [cmd]:" + chr(10) + "        for s in segments(c):",
     "test_a_comment_apostrophe_does_not_hide_a_long_job"),
    # The parseability check moved OUT of hook_facts into protect-bash.sh, which asks
    # `bash -n`. It is covered E2E in scripts/test-hooks.sh -- a mutation of the real
    # shell parser is not something this gate can plant.
    # Re-anchored v3.3.0 (hooks lane): the heredoc list is now built as `extra`, so the
    # PowerShell-fed heredocs can join it. Same subject: the RAW command is walked.
    ("a heredoc BODY is data for the operand rules too",
     "    all_cmds, carriers_truncated = carried_commands(body, extra, dialects," + NL
     + "                                                    exact=None if no_exact else exact_texts)",
     "    all_cmds, carriers_truncated = carried_commands(cmd, [], dialects)",
     "test_a_delete_inside_a_heredoc_body_is_not_a_delete"),
    ("a comment keeps its newline, which is a separator",
     "            while i < len(s) and s[i] != \"\\n\":      # keep the newline: it is a separator",
     "            while i < len(s) and s[i] != \"\\r\":",
     "test_a_comment_keeps_its_newline_which_is_a_separator"),

    # Native prefix composition is tested through a controlled API oracle on every OS.
    ("comparisons expand short aliases in unresolved stems",
     "    return _lexical_norm(long_name(p))",
     "    return _lexical_norm(p)",
     "test_short_alias_comparison_peels_prefixes_and_keeps_unknown_tails"),
    ("native alias lookup peels provider and device prefixes",
     '        w = _MSYS_DRIVE.sub(lambda m: m.group(1) + ":/", _lexical_norm(p))',
     '        w = _MSYS_DRIVE.sub(lambda m: m.group(1) + ":/", p.replace(chr(92), "/"))',
     "test_short_alias_comparison_peels_prefixes_and_keeps_unknown_tails"),

    # --- operands resolve against the command's own cwd (2026-09-01) ---------------
    # Each clause gets its OWN mutant. Mutating the whole feature to a no-op cannot
    # distinguish "the join is untested" from "some earlier guard covers it".
    ("a relative operand is joined to the cwd",
     'joined = r if unres else join_cwd(d, r)', 'joined = r',
     "test_cd_then_relative_delete_of_extensions_is_the_game_delete"),
    # The NAME backstop is the opposite call from hits_game_root, and the difference is
    # load-bearing: it must NOT skip unresolved operands, because on an unconfigured
    # machine it is the only protection there is and the visible text is the evidence.
    # A `not u` filter added here on 2026-09-01 removed that defence; the 13,041-command
    # corpus could not see it (no historical command has the shape) and only this gate did.
    ("the NAME backstop does NOT skip unresolved operands",
     "rm_named_game = any(GAME_ROOTISH.search(norm(p or raw)) for p, _u, raw in rm_t)",
     "rm_named_game = any(not _u and GAME_ROOTISH.search(norm(p or raw)) for p, _u, raw in rm_t)",
     "test_the_NAME_backstop_still_fires_on_an_unresolvable_path"),
    #
    # NOT MUTATED, deliberately -- two guards whose removal is BEHAVIOURALLY EQUIVALENT
    # today, so a mutant for them would sit here permanently "not caught" and train
    # everyone to ignore this table:
    #   * join_cwd's `is_abs(cwd)` check. A relative cwd joined to a relative operand
    #     yields a relative path, which matches no root either way.
    #   * hits_game_root's `unres` check. An unresolved operand still contains `$`, so
    #     norm(path) can never equal the game root.
    # Both are kept as PROSPECTIVE guards: if resolve() ever learns to expand
    # environment variables, each becomes load-bearing immediately -- which is exactly
    # the F93 shape, a capability improvement widening a guard nobody re-scoped.
    ("pushd relocates as well as cd",
     'DIR_VERBS = {"cd", "pushd"}', 'DIR_VERBS = {"cd"}',
     "test_pushd_relocates_like_cd"),
    ("popd pops, rather than being ignored",
     'elif v == "popd" and stack:\n            cwd, from_seed = stack.pop()',
     'elif v == "popd" and stack:\n            pass',
     "test_popd_returns_to_the_previous_directory"),
    ("subshell punctuation is stripped from a segment",
     "    return [_unwrap(p) for p in parts if p.strip()]",
     "    return [p for p in parts if p.strip()]",
     "test_subshell_cd_relocates"),
    ("command substitution counts as unresolved",
     "    return bool(_EXPANSION.search(tok) or _SUBST.search(tok))",
     "    return bool(_EXPANSION.search(tok))",
     "test_command_substitution_counts_as_unresolved"),
    ("a root named only by its ENV VAR is still that root",
     "                if conservative and key in root_vars_named(raw):\n                    return True",
     "                if False:\n                    return True",
     "test_delete_of_a_root_env_var_by_name"),
    ("bash -c is parsed too",
     "    all_cmds, carriers_truncated = carried_commands(body, extra, dialects," + NL
     + "                                                    exact=None if no_exact else exact_texts)",
     "    all_cmds, carriers_truncated = [body], False",
     "test_delete_inside_bash_c_is_seen"),

    # --- COMMAND RESOLUTION (2026-09-02). Six total bypasses, one root cause: the
    # parse pass modelled shell GRAMMAR and not command RESOLUTION. Each mutant below
    # restores one half of the pre-fix behaviour.
    ("a verb token is resolved to a command NAME",
     "    n = posixpath.basename(norm(t))", "    n = t",
     "test_the_hard_block_survives_every_spelling"),
    ("a .exe suffix is dropped",
     '    if n.endswith(".exe"):', "    if False:",
     "test_a_dot_exe_suffix_is_the_same_command"),
    ("ANSI-C quoting is not part of the name",
     # Re-anchored 2026-09-06: B2 replaced this line, because popping the sigil was
     # only half the job -- the $'...' body also has to be DECODED.
     '            dollar = bool(buf) and buf[-1] == "$"',
     '            dollar = False',
     "test_ansi_c_quoting_is_not_part_of_the_name"),
    ("a wrapper's VALUE argument is not the command",
     "        if seen_wrapper and _WRAPPER_ARG.match(t):", "        if False:",
     "test_a_duration_is_not_mistaken_for_the_command"),
    ("a substitution carries a command",
     "    return [o for o in out if o.strip()]", "    return []",
     "test_dollar_paren"),
    ("single-quoted text is NOT a substitution",
     "        if k == chr(39):                        # single-quoted: literal",
     "        if False:",
     "test_inside_SINGLE_quotes_nothing_runs"),
    ("trap carries a command",
     '        elif v == "trap":', "        elif False:",
     "test_trap_runs_its_first_operand"),
    ("eval is found wherever it sits",
     "            k = next((i for i, (t, _) in enumerate(toks)" + NL
     + '                      if _verb_name(t) == "eval"), 0)',
     "            k = 0",
     "test_a_wrapper_may_precede_eval"),
    ("only a SHELL runs its heredoc body",
     "            return verb(_unwrap(sg)) in _SHELL_SINKS",
     "            return True",
     "test_a_python_heredoc_does_NOT"),
    # ---- round 3: a command reaches the shell without being seen -------------
    ("a line continuation is spliced, not treated as a separator",
     "    spliced = join_continuations(cmd)", "    spliced = cmd",
     "test_the_game_root"),
    ("a continuation inside SINGLE quotes stays literal",
     '        if q == "' + "'" + '":' + NL
     + '            if c == "' + "'" + '":',
     '        if False:' + NL
     + '            if c == "' + "'" + '":',
     "test_inside_SINGLE_quotes_it_stays_literal"),
    ("a shell with no script operand reads its program from stdin",
     "    rest = _drop_redirects(toks[1:])",
     "    return False" + NL + "    rest = _drop_redirects(toks[1:])",
     "test_a_here_string"),
    ("a redirect is not an operand",
     "    out = []" + NL + "    i = 0" + NL + "    while i < len(toks):",
     "    return list(toks)" + NL + "    out = []" + NL + "    i = 0" + NL
     + "    while i < len(toks):",
     "test_separated_and_attached_here_strings"),
    ("ANY parameter expansion is unresolved, not just the two _VAR names",
     "    return bool(_EXPANSION.search(tok) or _SUBST.search(tok))",
     "    return bool(_VAR.search(tok) or _SUBST.search(tok))",
     "TestAnUnresolvedExpansionIsNotALiteralPath.test_suffix_strip"),
    ("coproc is a reserved word",
     '"coproc", "[[", "]]"}', '"[[", "]]"}',
     "test_coproc_does_not_hide_a_delete"),
    # ---- round 3b: resolution, not just grammar ------------------------------
    ("an expansion carrying an operator is resolved",
     "        out = _VAR_OP.sub(sub_op, _VAR.sub(sub, out))",
     "        out = _VAR.sub(sub, out)",
     "test_default_when_unset"),
    ("a GLOB pattern is NOT guessed at",
     "        if set(pat) & _GLOB_CHARS or not pat:",
     "        if not pat:",
     "test_a_GLOB_pattern_is_left_unresolved"),
    ("an array keeps its quoting when captured",
     '        for m in re.finditer(r"(?:^|\\s)([A-Za-z_][A-Za-z0-9_]*)=\\(", seg):',
     '        for m in []:',
     "test_a_spaced_path_stays_ONE_element"),
    ("cd resolves its operand",
     "        cands = [subst_root_var(r, roots) for r in resolve_variants(ops[0], assigns)]",
     "        cands = [subst_root_var(ops[0], roots)]",
     "test_a_plain_variable"),
    # ---- round 3c: the IMPORTANTs -------------------------------------------
    ("find -execdir/-ok/-okdir are exec too",
     "            if t in _FIND_EXEC and _exec_deletes(toks[i + 1:_exec_end(toks, i)]):",
     '            if t == "-exec" and _exec_deletes(toks[i + 1:_exec_end(toks, i)]):',
     "test_execdir_rm_of_an_archive_fires"),
    ("a mv SOURCE that is a root is a delete",
     '        mv_src += prep(move_sources(s) + replaced_sources(s), c_cwd, c_old, operation="move")',
     '        mv_src += prep(replaced_sources(s), c_cwd, c_old, operation="move")',
     "test_moving_the_game_root_away"),
    ("mv keeps every operand but the LAST as a source",
     "    return ops[:-1] if len(ops) > 1 else []",
     "    return ops",
     "test_deploying_INTO_the_game_is_not_a_root_delete"),
    ("git stage-everything is normalised",
     "    return any(_stages_everything(t) for t in toks[at + 1:])",
     "    return any(t in GIT_ADD_ALL for t in toks[at + 1:])",
     "test_git_add_colon_slash"),
    ("git stage is a synonym for add",
     '_GIT_ADD_VERBS = ("add", "stage")',
     '_GIT_ADD_VERBS = ("add",)',
     "test_git_stage_dash_A"),
    ("a carrier argument is resolved",
     "                    values = resolve_variants(arg, assignments(cmd))",
     "                    values = [arg]",
     "test_a_command_carried_in_a_variable"),
    ("the durable rule needs a python interpreter",
     "            and _verb_name(verb(sg)) in _PYTHONS",
     "            and True",
     "test_prose_naming_a_record_is_not_a_write"),
    # --- the round-4 guard fixes. Each reverts ONE fix to the code that shipped the
    # --- defect, so the test written for that defect is the one that must go red.
    ("an unresolvable operand is scoped to the OPERAND, not the whole command",
     "                if norm(root) in norm(raw):",
     "                if norm(root) in ncmd:",
     "test_naming_reference_elsewhere_does_not_make_an_rm_target_it"),
    ("$? is looked for in expansion positions, not in single-quoted text",
     "    live = blank_single_quoted(stripped)",
     "    live = stripped",
     "test_single_quoted_prose_is_not_a_hit"),
    ("the $? rule reads the comment-stripped body, not the raw command",
     '"dollarq_after_pipe": any(dollarq_after_pipe(c, assigns) for c in all_cmds),',
     '"dollarq_after_pipe": dollarq_after_pipe(cmd),',
     "test_a_comment_mentioning_the_trap_is_not_a_hit"),
    ("a WRITE into reference/ is a rule at all",
     '"writes_reference": hit(copy_t + [(pp, uu, rr) for _m, pp, uu, rr in redir_t]\n                                + sed_t + out_t + mod_t, "reference"),',
     '"writes_reference": False,',
     "test_a_truncating_redirect_into_reference_fires"),
    # --- writes_game_archive: one per clause, so a twin cannot shelter behind another clause
    ('game archive: the fact reaches the hook',
     '"writes_game_archive": writes_game_archive,',
     '"writes_game_archive": False,',
     'test_a_delete_of_an_archive_fires'),
    ('game archive: copies count',
     '_archive_written = copy_t + redir_t_all',
     '_archive_written = redir_t_all',
     'test_a_copy_destination_on_an_archive_fires'),
    ('game archive: redirects count',
     '_archive_written = copy_t + redir_t_all',
     '_archive_written = copy_t',
     'test_a_redirect_onto_an_archive_fires_truncating_or_appending'),
    ('game archive: sed -i counts',
     '+ sed_t + out_t + mod_t\n    _archive_removed',
     '+ out_t + mod_t\n    _archive_removed',
     'test_sed_in_place_on_an_archive_fires'),
    ('game archive: output flags count',
     '+ sed_t + out_t + mod_t\n    _archive_removed',
     '+ sed_t + mod_t\n    _archive_removed',
     'test_an_output_flag_on_an_archive_fires'),
    ('game archive: touch counts',
     '+ sed_t + out_t + mod_t\n    _archive_removed',
     '+ sed_t + out_t\n    _archive_removed',
     'test_touch_on_an_archive_fires'),
    ('game archive: deletes count',
     '_archive_removed = rm_t + mv_src + archive_moved_t',
     '_archive_removed = mv_src + archive_moved_t',
     'test_a_delete_of_an_archive_fires'),
    ('game archive: moves count',
     '_archive_removed = rm_t + mv_src + archive_moved_t',
     '_archive_removed = rm_t + archive_moved_t',
     'test_a_move_away_of_an_archive_fires'),
    ('game archive: rsync moves count',
     '_archive_removed = rm_t + mv_src + archive_moved_t',
     '_archive_removed = rm_t + mv_src',
     'test_rsync_removing_its_source_archive_fires'),
    ('game archive: rsync moves need the flag',
     'if verb(seg) != "rsync" or "--remove-source-files" not in tokens_of(seg):',
     'if verb(seg) != "rsync":',
     'test_TWIN_rsync_keeping_its_source_is_a_read'),
    ('game archive: the game root is a home',
     '    if n == g or _is_dlc_folder(n, g):',
     '    if _is_dlc_folder(n, g):',
     'test_the_game_root_itself_holds_archives'),
    ('game archive: a DLC is a home',
     '    if n == g or _is_dlc_folder(n, g):',
     '    if n == g:',
     'test_an_official_dlc_archive_fires'),
    ('game archive: a pattern can name the game folder',
     'return _is_pattern(n) and n.count("/") == g.count("/") and fnmatch.fnmatchcase(g, n)',
     'return False',
     'test_a_pattern_for_a_game_folder_not_on_disk_fires'),
    ("game archive: that pattern must be the game's depth",
     'return _is_pattern(n) and n.count("/") == g.count("/") and fnmatch.fnmatchcase(g, n)',
     'return _is_pattern(n) and fnmatch.fnmatchcase(g, n)',
     'test_TWIN_a_pattern_above_the_game_folder_does_not_fire'),
    ('game archive: a DLC folder is directly below extensions',
     'if n.startswith(prefix) and "/" not in n[len(prefix):]:',
     'if n.startswith(prefix):',
     'test_TWIN_a_write_deeper_in_a_dlc_is_not_an_archive'),
    ('game archive: the ending names an archive',
     '    if _ARCHIVE_EXT.search(name):\n        return True',
     '    if False:\n        return True',
     'test_a_delete_of_an_archive_fires'),
    ('game archive: the ending is anchored',
     '_ARCHIVE_EXT = re.compile(r"[.](cat|dat)$")',
     '_ARCHIVE_EXT = re.compile(r"[.](cat|dat)")',
     'test_TWIN_another_extension_does_not_fire'),
    ('game archive: the contents of an archive operand count',
     'or (n.endswith("/*") and _is_game_archive(n[:-2], g)))',
     ')',
     'test_contents_of_an_archive_named_operand_read_as_the_archive'),
    ('game archive: only a /* operand is read that way',
     'or (n.endswith("/*") and _is_game_archive(n[:-2], g)))',
     'or _is_game_archive(n[:-2], g))',
     'test_TWIN_another_extension_does_not_fire'),
    ('game archive: that operand must name an archive',
     'or (n.endswith("/*") and _is_game_archive(n[:-2], g)))',
     'or n.endswith("/*"))',
     'test_TWIN_contents_of_another_operand_do_not'),
    ('game archive: a copy into a folder needs an archive name',
     '    if copy_names_archive(s):',
     '    if True:',
     'test_TWIN_another_source_or_a_mod_destination_does_not_fire'),
    ("game archive: robocopy's destination is judged",
     'archive_into_t += dests_ + prep(robo_copy, c_cwd, c_old)',
     'archive_into_t += dests_',
     'test_robocopy_of_a_named_archive_into_a_home_folder_fires'),
    # --- writes_game_archive 4.1.1: unknown root, xargs -I, option words, the find reader
    ("game archive: the install name, not the profile's",
     'if (m and m.group(1).lower() == "x4 foundations" and not m.group(2)',
     'if (m and not m.group(2)',
     'test_TWIN_an_unknown_game_root_leaves_other_dat_files_alone'),
    ('game archive: the install folder, not its extensions',
     'if (m and m.group(1).lower() == "x4 foundations" and not m.group(2)',
     'if (m and m.group(1).lower() == "x4 foundations"',
     'test_TWIN_an_unknown_game_root_leaves_other_dat_files_alone'),
    ('game archive: the install name is a whole segment',
     'and (m.start() == 0 or p[m.start() - 1] == "/")):',
     '):',
     'test_TWIN_an_unknown_game_root_leaves_other_dat_files_alone'),
    ('game archive: a copy into a home folder counts',
     '        or _hits(archive_into_t, _is_archive_home)\n',
     '',
     'test_an_archive_named_source_into_a_home_folder_fires'),
    ('game archive: rsync emptying a home folder counts',
     '        or _hits(archive_moved_t, _is_archive_home)\n',
     '',
     'test_rsync_moving_everything_out_of_a_home_folder_fires'),
    ('game archive: a listed archive counts',
     'lambda n, g: _is_game_archive(n, g) or _is_archive_home(n, g))',
     'lambda n, g: _is_archive_home(n, g))',
     'test_a_listing_of_archives_piped_to_xargs_rm_fires'),
    ('game archive: a listed home folder counts',
     'lambda n, g: _is_game_archive(n, g) or _is_archive_home(n, g))',
     'lambda n, g: _is_game_archive(n, g))',
     'test_a_listing_of_a_home_folder_piped_to_xargs_rm_fires'),
    ('game archive: a listing needs xargs rm',
     'if not xargs_feed(seg, prev) or verb(prev) == "find" or not xargs_passes_item(seg):',
     'if prev is None or verb(prev) == "find" or not xargs_passes_item(seg):',
     'test_TWIN_a_listing_piped_to_a_reader_does_not_fire'),
    ('game archive: find keeps its own reading',
     'if not xargs_feed(seg, prev) or verb(prev) == "find" or not xargs_passes_item(seg):',
     'if not xargs_feed(seg, prev) or not xargs_passes_item(seg):',
     'test_TWIN_find_piped_to_xargs_rm_elsewhere_does_not_fire'),
    ('game archive: a listing replaced elsewhere passes nothing',
     'if not xargs_feed(seg, prev) or verb(prev) == "find" or not xargs_passes_item(seg):',
     'if not xargs_feed(seg, prev) or verb(prev) == "find":',
     'test_TWIN_xargs_replacing_into_another_folder_does_not_fire'),
    ('game archive: find piped needs xargs rm',
     'if xargs_feed(s, prev) and verb(prev) == "find" and xargs_passes_item(s):',
     'if prev is not None and verb(prev) == "find" and xargs_passes_item(s):',
     'test_TWIN_find_piped_to_a_reader_does_not_fire'),
    ('game archive: find replaced elsewhere passes nothing',
     'if xargs_feed(s, prev) and verb(prev) == "find" and xargs_passes_item(s):',
     'if xargs_feed(s, prev) and verb(prev) == "find":',
     'test_TWIN_xargs_replacing_into_another_folder_does_not_fire'),
    ('game archive: xargs -I into a folder is judged',
     'archive_listed_t += prep(xargs_replaced_folders(s, prev), c_cwd, c_old)',
     'pass',
     'test_xargs_replacing_into_a_home_folder_fires'),
    ('game archive: xargs -I without the item passes nothing',
     'return repl is None or any(w.startswith(repl) for w in words)',
     'return True',
     'test_TWIN_xargs_replacing_into_another_folder_does_not_fire'),
    ('game archive: xargs -I with the item passes it',
     'return repl is None or any(w.startswith(repl) for w in words)',
     'return repl is None',
     'test_xargs_replacing_the_item_itself_still_fires'),
    ('game archive: xargs -I R apart',
     'if t == "-I" and i + 1 < vi:',
     'if False:',
     'test_TWIN_xargs_replacing_into_another_folder_does_not_fire'),
    ('game archive: xargs -IR joined',
     'elif t.startswith("-I") and len(t) > 2:',
     'elif False:',
     'test_TWIN_xargs_replacing_into_another_folder_does_not_fire'),
    ('game archive: xargs --replace=R',
     'elif t.startswith("--replace="):',
     'elif False:',
     'test_TWIN_xargs_replacing_into_another_folder_does_not_fire'),
    ('game archive: an option word is not a copied name',
     'and not o.startswith("-") and o not in values]',
     'and o not in values]',
     'test_TWIN_an_exclude_pattern_is_not_a_copied_name'),
    ("game archive: an rsync option's value is not a copied name",
     'if verb(seg) == "rsync" else set())',
     'if False else set())',
     'test_TWIN_an_exclude_pattern_is_not_a_copied_name'),
    ("game archive: robocopy's names stop at a switch",
     '            if seen >= 2:\n                break',
     '            if False:\n                break',
     'test_TWIN_an_exclude_pattern_is_not_a_copied_name'),
    ("game archive: robocopy's name list is read",
     '        if seen > 2:\n            out.append(t)',
     '        if seen > 2:\n            pass',
     'test_robocopy_of_a_named_archive_into_a_home_folder_fires'),
    ('game archive: an expression it cannot read may reach',
     '    if parsed is None:\n        return True',
     '    if parsed is None:\n        return False',
     'test_an_expression_it_cannot_parse_may_reach_an_archive'),
    ('game archive: an unknown primary is not read',
     'raise ValueError("an unknown primary")',
     'return ("const", False)',
     'test_an_expression_it_cannot_parse_may_reach_an_archive'),
    ('game archive: a long expression is not read',
     '    if len(words) > _FIND_MAX_WORDS:\n        return None',
     '    if False:\n        return None',
     'test_a_find_expression_past_the_bounds_may_reach_an_archive'),
    ('game archive: a deep expression is not read',
     '            if depth >= _FIND_MAX_DEPTH:',
     '            if False:',
     'test_a_find_expression_past_the_bounds_may_reach_an_archive'),
    ('game archive: groups are read',
     'if pos < len(words) and words[pos] == "(":',
     'if False:',
     'test_TWIN_a_group_or_negated_group_sparing_archives_does_not_fire'),
    ('game archive: NOT inverts',
     '        return None if v is None else not v',
     '        return v',
     'test_a_negated_find_group_is_modelled'),
    ('game archive: NOT of an unknown stays unknown',
     '        return None if v is None else not v',
     '        return False if v is None else not v',
     'test_a_negated_regex_may_always_reach_an_archive'),
    ('game archive: AND stops at a False',
     '            if v is False:\n                return False',
     '            if v is False:\n                val = None',
     'test_TWIN_a_find_name_filter_for_something_else_keeps_its_verdict'),
    ('game archive: OR stops at a True',
     '            if v is True:\n                return True',
     '            if v is True:\n                val = None',
     'test_TWIN_an_OR_that_prunes_the_archives_first_does_not_fire'),
    ('game archive: a test it cannot read may match',
     '        if w in _FIND_UNKNOWN1 or _FIND_NEWER_XY.match(w):\n            take()\n            return ("const", None)',
     '        if w in _FIND_UNKNOWN1 or _FIND_NEWER_XY.match(w):\n            take()\n            return ("const", False)',
     'test_a_find_filter_it_cannot_read_may_reach_an_archive'),
    ('game archive: -iname folds case',
     '    v = value.lower() if fold else value',
     '    v = value',
     'test_a_find_delete_named_for_an_archive_fires'),
    ('game archive: -name keeps case',
     '    v = value.lower() if fold else value',
     '    v = value.lower()',
     'test_a_case_sensitive_name_in_capitals_may_still_match'),
    ('game archive: a regex alternation needs no ending',
     '        if "|" in v:\n            return None',
     '        if False:\n            return None',
     'test_a_find_regex_it_cannot_decide_may_reach_an_archive'),
    ('game archive: a regex operator ends its literal tail',
     'tail = _literal_tail(body, ".^$*+?{}[]()|", _RX_SELF_ESCAPES)',
     'tail = _literal_tail(body, "", _RX_SELF_ESCAPES)',
     'test_a_find_regex_it_cannot_decide_may_reach_an_archive'),
    ("game archive: a regex's closing $ is an anchor",
     'body = v[:-1] if v.endswith("$") and escapes % 2 == 0 else v',
     'body = v',
     'test_TWIN_a_find_regex_whose_ending_no_archive_has_does_not_fire'),
    ('game archive: a brace list is expanded',
     'yield from (_brace_expand(n) if norm(raw) in brace_patterns else [n])',
     'yield n',
     'test_a_brace_list_of_archives_fires'),
    # --- writes_game_archive 4.1.1 round 2: no name list, F8 DLC deletes, recursion, `.`, -exec wrappers
    ('the find -exec verb is NORMALISED',
     '    if _verb_name(verb(text)) in DELETE_VERBS:' + NL + '        return True',
     '    if words[0] in DELETE_VERBS:' + NL + '        return True',
     'test_an_absolute_delete_under_exec'),
    ('find -exec reads its verb through the wrapper table',
     '    if _verb_name(verb(text)) in DELETE_VERBS:' + NL + '        return True',
     '    if _verb_name(words[0]) in DELETE_VERBS:' + NL + '        return True',
     'test_a_find_exec_through_a_wrapper_fires'),
    ('find -exec walks a carrier',
     '    return any(_verb_name(verb(s)) in DELETE_VERBS for x in _inner_commands(text) for s in segments(x))',
     '    return False',
     'test_a_find_exec_through_a_wrapper_fires'),
    ('game archive: a DLC name is ego_dlc_',
     '    prefix = g + "/extensions/ego_dlc_"' + NL + '    if n.startswith(prefix) and "/" not in n[len(prefix):]:',
     '    prefix = g + "/extensions/ego_dlc"' + NL + '    if n.startswith(prefix) and "/" not in n[len(prefix):]:',
     'test_TWIN_a_dlc_shaped_folder_elsewhere_does_not_fire'),
    ('game archive: a pattern can reach a DLC',
     'return _is_pattern(n) and _glob_may_meet(n, _glob_lit(prefix) + [_GLOB_SEGMENT])',
     'return False',
     'test_a_write_through_a_dlc_folder_glob_fires'),
    ('game archive: a DLC folder pattern is one segment',
     'return _is_pattern(n) and _glob_may_meet(n, _glob_lit(prefix) + [_GLOB_SEGMENT])',
     'return _is_pattern(n) and _glob_may_meet(n, _glob_lit(prefix) + [_GLOB_ALL])',
     'test_TWIN_a_write_deeper_in_a_dlc_is_not_an_archive'),
    ('game archive: inside a DLC, a literal path',
     'if n.startswith(prefix) and "/" in n[len(prefix):].rstrip("/"):',
     'if False:',
     'test_inside_a_dlc_folder_by_the_install_name_alone'),
    ('game archive: inside a DLC, a pattern',
     'return _is_pattern(n) and _glob_may_meet(n, _glob_lit(prefix) + [_GLOB_SEGMENT, ("lit", "/"),',
     'return False and _glob_may_meet(n, _glob_lit(prefix) + [_GLOB_SEGMENT, ("lit", "/"),',
     'test_a_delete_or_move_inside_a_dlc_folder_is_denied_not_asked'),
    ('game archive: a bare glob is tried against archive endings',
     'return _is_pattern(name) and any(_glob_may_meet(name, [_GLOB_DISK_NAME] + _glob_lit(e))',
     'return False and any(_glob_may_meet(name, [_GLOB_DISK_NAME] + _glob_lit(e))',
     'test_a_bare_glob_covering_an_archive_name_fires'),
    ('game archive: an archive name is lower case on disk',
     '_GLOB_DISK_NAME = ("star", True, frozenset("/"), (("A", "Z"),))',
     '_GLOB_DISK_NAME = ("star", True, frozenset("/"), ())',
     'test_TWIN_a_name_no_archive_could_have_does_not_fire'),
    ('game archive: an archive name has its ending',
     '_ARCHIVE_ENDINGS = (".cat", ".dat")',
     '_ARCHIVE_ENDINGS = ("",)',
     'test_TWIN_a_bare_glob_covering_no_archive_name_does_not_fire'),
    ('game archive: a DLC folder delete counts',
     'return (_is_game_archive(n, g) or _is_dlc_folder(n, g) or _in_dlc_folder(n, g)' + NL + '',
     'return (_is_game_archive(n, g) or _in_dlc_folder(n, g)' + NL + '',
     'test_an_unknown_game_root_falls_back_to_the_install_folder_name'),
    ('game archive: a file delete counts',
     'return (_is_game_archive(n, g) or _is_dlc_folder(n, g) or _in_dlc_folder(n, g)' + NL + '',
     'return (_is_dlc_folder(n, g) or _in_dlc_folder(n, g)' + NL + '',
     'test_a_delete_of_an_archive_fires'),
    ('game archive: a delete inside a DLC counts',
     'return (_is_game_archive(n, g) or _is_dlc_folder(n, g) or _in_dlc_folder(n, g)' + NL + '',
     'return (_is_game_archive(n, g) or _is_dlc_folder(n, g)' + NL + '',
     'test_inside_a_dlc_folder_by_the_install_name_alone'),
    ('game archive: a DLC named in the mod list is denied',
     '        or bool(_dlc_named)' + NL + '',
     '',
     'test_a_substituted_delete_inside_a_dlc_is_denied'),
    ('game archive: a DLC is never asked as a mod',
     '    _mods = [m for m in _mods if not m.startswith("ego_dlc_")]',
     '    pass',
     'test_a_dlc_is_never_named_as_a_deployed_mod'),
    ('game archive: no root falls back to the install name',
     'return list(dict.fromkeys(([_g] if _g else []) + named_game_roots(n)))',
     'return [_g] if _g else []',
     'test_an_unknown_game_root_falls_back_to_the_install_folder_name'),
    ('game archive: a configured root keeps the install-name fallback',
     'return list(dict.fromkeys(([_g] if _g else []) + named_game_roots(n)))',
     'return [_g] if _g else named_game_roots(n)',
     'test_a_configured_root_keeps_the_folder_name_fallback_for_a_second_install'),
    ('game archive: the configured root is read',
     'return list(dict.fromkeys(([_g] if _g else []) + named_game_roots(n)))',
     'return list(dict.fromkeys(named_game_roots(n)))',
     'test_a_configured_root_with_another_name_is_read'),
    ('game archive: a find delete is judged',
     '        archive_finds += [(op, s, "delete", True)' + NL + '                          for op in prep(_find_delete(s, default_root=True)[0], c_cwd, c_old)]',
     '        pass',
     'test_a_find_delete_named_for_an_archive_fires'),
    ('game archive: a find delete with no path is .',
     'prep(_find_delete(s, default_root=True)[0], c_cwd, c_old)]',
     'prep(_find_delete(s)[0], c_cwd, c_old)]',
     'test_a_find_or_listing_with_no_path_reads_the_current_directory'),
    ('game archive: the default root is `.`',
     'return (out or (["."] if default_root else [])), filtered',
     'return out, filtered',
     'test_a_find_or_listing_with_no_path_reads_the_current_directory'),
    ('game archive: find piped to xargs is judged',
     '            archive_finds += [(op, prev, "print", xargs_rec)' + NL + '                              for op in prep(_find_roots(prev) or ["."], c_cwd, c_old)]',
     '            pass',
     'test_find_piped_to_xargs_rm_fires'),
    ('game archive: a piped find with no path is .',
     'for op in prep(_find_roots(prev) or ["."], c_cwd, c_old)]',
     'for op in prep(_find_roots(prev), c_cwd, c_old)]',
     'test_a_find_or_listing_with_no_path_reads_the_current_directory'),
    ('xargs: a find with no path feeds `.`',
     'for o in _find_roots(prev) or ["."]]',
     'for o in _find_roots(prev)]',
     'test_a_find_or_listing_with_no_path_reads_the_current_directory'),
    ('xargs: a bare ls feeds `.`',
     'for o in _operands(prev) or (["."] if pv in ("ls", "dir") else [])]',
     'for o in _operands(prev)]',
     'test_a_find_or_listing_with_no_path_reads_the_current_directory'),
    ('game archive: a bare ls lists `.`',
     'return _operands(prev) or (["."] if verb(prev) in ("ls", "dir") else [])',
     'return _operands(prev)',
     'test_a_find_or_listing_with_no_path_reads_the_current_directory'),
    ('game archive: a find reaches only what is under its root',
     '            head = r + "/"',
     '            head = ""',
     'test_TWIN_a_find_in_one_mod_does_not_fire'),
    ('game archive: a find root can be a pattern',
     'if not _glob_may_meet(r, path, tail=(("lit", "/"), _GLOB_ALL)):' + NL + '                continue',
     'if True:' + NL + '                continue',
     'test_a_path_filter_under_a_pattern_root_reads_only_its_ending'),
    ('game archive: a find in a DLC folder is denied',
     '    if _is_dlc_folder(r, g) or _in_dlc_folder(r, g):' + NL + '        return True',
     '    if False:' + NL + '        return True',
     'test_inside_a_dlc_folder_by_the_install_name_alone'),
    ('game archive: only an action of the mode counts',
     '        if node[1] in modes:',
     '        if True:',
     'test_only_the_deleting_action_is_judged'),
    ('game archive: find prints when it has no action',
     'if reach or ("print" in modes and not acted and v is not False):',
     'if reach:',
     'test_find_piped_to_xargs_rm_fires'),
    ('game archive: an action stops the implicit print',
     'if reach or ("print" in modes and not acted and v is not False):',
     'if reach or ("print" in modes and v is not False):',
     'test_TWIN_a_find_printing_to_a_file_feeds_xargs_nothing'),
    ('game archive: -exec rm is a deleting action',
     '            if not _exec_deletes(ran):' + NL + '                # A command that CHANGES',
     '            if True:' + NL + '                # A command that CHANGES',
     'test_a_find_exec_rm_named_for_an_archive_fires'),
    ('game archive: a recursive -exec delete removes folders',
     'return ("act", "rdelete" if deletes_recursively(ran) else "delete", None)',
     'return ("act", "delete", None)',
     'test_a_recursive_delete_of_a_found_dlc_folder_fires'),
    ('game archive: only a recursive delete removes a found folder',
     '    dir_modes = {"delete": ("rdelete", "move"),', '    dir_modes = {"delete": ("delete", "rdelete", "move"),',
     'test_TWIN_a_non_recursive_or_other_folder_delete_does_not_fire'),
    ('game archive: only a recursive xargs removes a found folder',
     '"print": ("print",) if rec else (),', '"print": ("print",),',
     'test_TWIN_a_non_recursive_or_other_folder_delete_does_not_fire'),
    ('game archive: xargs recursion is read',
     'xargs_rec = bool(listed_ or verb(prev or "") == "find") and deletes_recursively(tokens_of(s))',
     'xargs_rec = False',
     'test_a_recursive_delete_of_a_found_dlc_folder_fires'),
    ('game archive: a recursive listing of a DLC holder counts',
     '        or _hits(archive_listed_rec_t, _holds_a_dlc)' + NL + '',
     '',
     'test_a_recursive_delete_of_a_found_dlc_folder_fires'),
    ('game archive: a DLC holder is extensions/ or above',
     '        return n.rstrip("/") in cands',
     '        return True',
     'test_TWIN_a_non_recursive_or_other_folder_delete_does_not_fire'),
    ('recursion: an -r cluster',
     'if t == "--recursive" or (re.match(r"^-[A-Za-z]+$", t) and set(t[1:]) & set("rR")):',
     'if t == "--recursive":',
     'test_a_recursive_delete_of_a_found_dlc_folder_fires'),
    ('recursion: only an -r flag',
     'if t == "--recursive" or (re.match(r"^-[A-Za-z]+$", t) and set(t[1:]) & set("rR")):',
     'if True:',
     'test_TWIN_a_non_recursive_or_other_folder_delete_does_not_fire'),
    ('game archive: -name can rule an entry out',
     '        if not _glob_may_meet(v, name):' + NL + '            return False',
     '        if False:' + NL + '            return False',
     'test_TWIN_a_find_name_filter_for_something_else_keeps_its_verdict'),
    ('game archive: -name can hold for every entry',
     '        return True if _glob_covers(v, ending, False) else None',
     '        return None',
     'test_a_name_test_true_for_every_archive_still_spares_them'),
    ('game archive: -path can rule an entry out',
     '    if not _glob_may_meet(v, hay, slash=True):' + NL + '        return False',
     '    if False:' + NL + '        return False',
     'test_a_path_filter_under_a_pattern_root_reads_only_its_ending'),
    ('game archive: -path can hold for every entry',
     '    return True if _glob_covers(v, ending, True) else None',
     '    return None',
     'test_a_name_test_true_for_every_archive_still_spares_them'),
    ("game archive: under a pattern root the entry's name is known",
     '            hay = [_GLOB_ALL, ("lit", "/")] + name',
     '            hay = [_GLOB_ALL]',
     'test_a_path_filter_under_a_pattern_root_reads_only_its_ending'),
    ('game archive: -iregex and -ipath fold case',
     '    if fold:' + NL + '        hay, name = _glob_fold(hay), _glob_fold(name)',
     '    if False:' + NL + '        hay, name = _glob_fold(hay), _glob_fold(name)',
     'test_a_find_regex_it_cannot_decide_may_reach_an_archive'),
    ('game archive: a regex ending no entry has cannot match',
     '        return None if _glob_meet([_GLOB_ALL] + _glob_lit(tail), hay) else False',
     '        return None',
     'test_TWIN_a_find_regex_whose_ending_no_archive_has_does_not_fire'),
    ('glob: a backslash is also read literally',
     '    for esc in ((True, False) if chr(92) in pat else (True,)):',
     '    for esc in ((True,) if chr(92) in pat else (True,)):',
     'test_every_reading_of_a_backslash_or_caret_is_tried'),
    ('glob: a backslash is also read as an escape',
     '    for esc in ((True, False) if chr(92) in pat else (True,)):',
     '    for esc in ((False,) if chr(92) in pat else (True,)):',
     'test_every_reading_of_a_backslash_or_caret_is_tried'),
    ('glob: [^ is also read as a member',
     '        for caret in ((True, False) if "[^" in pat else (False,)):',
     '        for caret in ((True,) if "[^" in pat else (False,)):',
     'test_every_reading_of_a_backslash_or_caret_is_tried'),
    ('glob: a backslash never covers every name',
     'if chr(92) in pat or "[^" in pat or not pat.startswith("*"):',
     'if "[^" in pat or not pat.startswith("*"):',
     'test_every_reading_of_a_backslash_or_caret_is_tried'),
    ('glob: [^ never covers every name',
     'if chr(92) in pat or "[^" in pat or not pat.startswith("*"):',
     'if chr(92) in pat or not pat.startswith("*"):',
     'test_a_name_with_a_backslash_or_caret_may_match_and_never_spares'),
    ('glob: a star may match nothing',
     '        if i < len(a) and a[i][0] == "star":' + NL + '            todo.append((i + 1, j))',
     '        if False:' + NL + '            todo.append((i + 1, j))',
     'test_glob_meet_is_exact_on_small_cases'),
    ('glob: a range is read',
     '    hit = c in t[2] or any(lo <= c <= hi for lo, hi in t[3])',
     '    hit = c in t[2]',
     'test_glob_meet_is_exact_on_small_cases'),
    ('glob: stand-ins cover a range',
     '            for lo, hi in t[3]:' + NL + '                alpha |= {lo, hi,',
     '            for lo, hi in []:' + NL + '                alpha |= {lo, hi,',
     'test_glob_meet_is_exact_on_small_cases'),
    ("game archive: an option word names no game folder",
     '    if n.startswith("-"):' + NL + '        return []',
     '    if False:' + NL + '        return []',
     'test_TWIN_an_option_word_naming_an_archive_is_not_a_path'),
    ('glob: a shell star stays in its segment',
     '    noslash = frozenset() if slash else frozenset("/")',
     '    noslash = frozenset()',
     'test_glob_meet_is_exact_on_small_cases'),
    # --- 4.1.1 review round 3: one per clause of each new reading
    ('glob: the meet gives up past its bound (A)',
     '        if steps > _GLOB_MAX_STEPS:' + NL + '            return True',
     '        if False:' + NL + '            return True',
     'test_a_glob_past_the_meet_bound_is_read_as_may_match_at_once'),
    ('glob: the bound leaves an ordinary meet decided (A)',
     '_GLOB_MAX_STEPS = 50000', '_GLOB_MAX_STEPS = 0',
     'test_TWIN_a_glob_within_the_meet_bound_is_still_decided'),
    ('glob: a POSIX class closes after its own bracket (B)',
     '            if has_class:' + NL, '            if False:' + NL,
     'test_a_posix_class_in_a_bracket_may_match_any_character'),
    ('glob: a POSIX class never covers every name (B)',
     '    if any(len(t) > 4 for t in toks):' + NL + '        return False',
     '    if False:' + NL + '        return False',
     'test_a_posix_class_never_covers_every_archive'),
    ('path: a POSIX class is not a stream suffix (C)',
     'i = _POSIX_CLASS.sub(lambda m: "#" * len(m.group()), c).find(":")', 'i = c.find(":")',
     'test_a_globbed_install_folder_name_is_an_install'),
    ('game archive: a pattern may spell the install name (C)',
     '        elif _globs_install_name(p):', '        elif False:',
     'test_a_globbed_install_folder_name_is_an_install'),
    ('game archive: a near-empty pattern is not the install name (C)',
     '    if lits < 2 and not head.endswith("/steamapps/common"):', '    if False:',
     'test_TWIN_a_folder_pattern_that_is_not_the_install_name'),
    ('game archive: ...except directly under steamapps/common (C)',
     '    if lits < 2 and not head.endswith("/steamapps/common"):', '    if lits < 2:',
     'test_a_globbed_install_folder_name_is_an_install'),
    ('game archive: the folder pattern must meet the install name (C)',
     '    return _glob_may_meet(last, _INSTALL_NAME)', '    return True',
     'test_TWIN_a_folder_pattern_that_is_not_the_install_name'),
    ('game archive: a find over steamapps/common may reach an install (C)',
     'return out + ([lib + "/x4 foundations"] if lib.endswith("/steamapps/common") else [])',
     'return out', 'test_a_find_over_a_steam_library_may_reach_an_install'),
    ('game archive: ...and only over steamapps/common (C)',
     'return out + ([lib + "/x4 foundations"] if lib.endswith("/steamapps/common") else [])',
     'return out + [lib + "/x4 foundations"]', 'test_TWIN_a_find_over_a_steam_library_for_other_files'),
    ('find: -maxdepth drops deeper entries (D)',
     'if (maxd is None or e[4][0] <= maxd) and', 'if True and',
     'test_a_depth_limited_find_over_extensions_keeps_the_mod_confirmation'),
    ('find: -mindepth drops shallower entries (D)',
     'and (mind is None or e[4][1] is None or e[4][1] >= mind)]', 'and True]',
     'test_TWIN_a_depth_no_archive_sits_at'),
    ('find: an entry of any depth meets every -mindepth (D)',
     'and (mind is None or e[4][1] is None or e[4][1] >= mind)]',
     'and (mind is None or (e[4][1] or 0) >= mind)]',
     'test_TWIN_a_depth_that_reaches_a_dlc_still_fires'),
    ('find: the depth options are read (D)',
     'depth[w] = int(v) if v.isdigit() else None', 'depth[w] = None',
     'test_a_depth_limited_find_over_extensions_keeps_the_mod_confirmation'),
    ('find: a depth not spelled as a number bounds nothing (D)',
     'depth[w] = int(v) if v.isdigit() else None', 'depth[w] = int(v) if v.isdigit() else 0',
     'test_TWIN_a_depth_that_reaches_a_dlc_still_fires'),
    ('find: each separator is a level (D)',
     '    least = 1 + sum(1 for t in rel if t == ("lit", "/"))', '    least = 1',
     'test_a_depth_limited_find_over_extensions_keeps_the_mod_confirmation'),
    ('find: a token that holds `/` has no deepest level (D)',
     '    deep = any(t[0] != "lit" and _glob_accepts(t, "/") for t in rel)', '    deep = False',
     'test_TWIN_a_depth_that_reaches_a_dlc_still_fires'),
    ('game archive: a find at extensions reaches DLC content (E)',
     '        if content and not at_ext:', '        if content:',
     'test_a_find_at_extensions_deleting_any_file_a_dlc_may_hold_fires'),
    ('game archive: ...and only at extensions (E)',
     '        if content and not at_ext:', '        if False:',
     'test_TWIN_a_find_from_the_game_folder_or_one_mod_for_other_files'),
    ('game archive: a pattern root may be extensions (E)',
     'else _glob_may_meet(r, _glob_lit(ext))', 'else False',
     'test_a_find_at_extensions_deleting_any_file_a_dlc_may_hold_fires'),
    ('game archive: a run-time name may be an archive (loop)',
     '    if "$" in name or name.count(chr(96)) >= 2:', '    if False:',
     'test_a_name_filled_in_at_run_time_may_be_an_archive'),
    # "...but not the xargs item" was removed with its clause (4.1.1 final round): the bare item returns
    # earlier in _may_name_archive, so the clause was dead and no test could kill its mutant.
    ("game archive: the bare xargs item names no archive (loop)",
     '        return bool(tail) and any(', '        return any(',
     'test_TWIN_a_run_time_name_in_a_mod_or_a_find_item'),
    # --- 4.1.1 review round 4: one per clause
    ('find: the root itself is a depth-0 entry (r4, 1)',
     '    if _holds_a_dlc(r, g):' + NL + '        base =', '    if False:' + NL + '        base =',
     'test_a_recursive_delete_of_a_root_holding_a_dlc_at_depth_0_fires'),
    ('find: ...only for a root holding a DLC (r4, 1)',
     '    if _holds_a_dlc(r, g):' + NL + '        base =', '    if True:' + NL + '        base =',
     'test_TWIN_a_depth_0_root_that_is_not_removed_whole_or_holds_no_dlc'),
    ('find: ...only for a recursive delete: the root is a FOLDER entry (r4, 1)',
     '        entries.append((hay, name, ending, dir_modes, (0, 0), True))',
     '        entries.append((hay, name, ending, file_modes, (0, 0), True))',
     'test_TWIN_a_depth_0_root_that_is_not_removed_whole_or_holds_no_dlc'),
    ('find: a root not spelled as one path is read as any root (r4, 1)',
     '            hay, name, ending = [_GLOB_ALL], [_GLOB_SEGMENT], ""',
     '            hay, name, ending = _glob_lit(r), _glob_lit(base), base',
     'test_a_recursive_delete_of_a_root_holding_a_dlc_at_depth_0_fires'),
    ('find: the depth-0 root has its own name (r4, 1)',
     '            hay, name, ending = _glob_lit(plain), _glob_lit(base), base',
     '            hay, name, ending = [_GLOB_ALL], [_GLOB_SEGMENT], ""',
     'test_TWIN_a_depth_0_root_that_is_not_removed_whole_or_holds_no_dlc'),
    ('segments: an escaped character is literal (r4, 7)',
     '            esc = False' + NL + '            yield c, True' + NL + '            continue',
     '            esc = False' + NL + '            yield c, inq' + NL + '            continue',
     'test_an_escaped_terminator_ends_the_exec_and_find_reads_on'),
    ('segments: an escaped backslash escapes nothing more (r4, 7)',
     '            esc = False' + NL + '            yield c, True', '            yield c, True',
     'test_an_escaped_operator_does_not_split_a_command'),
    ('tmux: a separator glued to a word (r4, 7)',
     '        if h != ";" and h.endswith(";") and w.endswith(chr(92) + ";"):',
     '        if False:', 'test_tmux_chained_with_an_escaped_semicolon'),
    ('game archive: a globbed install name as the last segment (r4, 5)',
     '        elif _globs_install_name(p):', '        elif at >= 0 and _globs_install_name(p):',
     'test_a_find_rooted_at_a_globbed_install_name'),
    ('game archive: a backtick pair is a run-time name (r4, 6)',
     '    if "$" in name or name.count(chr(96)) >= 2:',
     '    if "$" in name:',
     'test_a_backtick_name_or_a_run_time_folder_in_the_game_folder'),
    ('game archive: ...a pair, not one backtick (r4, 6)',
     '    if "$" in name or name.count(chr(96)) >= 2:',
     '    if "$" in name or name.count(chr(96)) >= 1:',
     'test_TWIN_a_run_time_folder_holding_no_archive_or_not_in_the_game_folder'),
    ('game archive: a run-time folder directly in the game folder (r4, 6)',
     '        if not below or "/" in below or ("$" not in below and below.count(chr(96)) < 2):',
     '        if True:', 'test_a_backtick_name_or_a_run_time_folder_in_the_game_folder'),
    ('game archive: ...directly, not deeper (r4, 6)',
     '        if not below or "/" in below or ("$" not in below and below.count(chr(96)) < 2):',
     '        if not below or ("$" not in below and below.count(chr(96)) < 2):',
     'test_TWIN_a_run_time_folder_holding_no_archive_or_not_in_the_game_folder'),
    ('game archive: ...a backtick pair folder too (r4, 6)',
     '        if not below or "/" in below or ("$" not in below and below.count(chr(96)) < 2):',
     '        if not below or "/" in below or "$" not in below:',
     'test_a_backtick_name_or_a_run_time_folder_in_the_game_folder'),
    # --- 4.1.1 review round 5: one per clause
    ('carrier: an escaped separator is a split for a shell (r5, 1)',
     '            if _EVAL_SPLITS.match(c):' + NL + '                return True',
     '            if False:' + NL + '                return True',
     'test_an_escaped_separator_reaches_the_shell_a_carrier_hands_its_words_to'),
    ('carrier: ...for a carrier that joins for a shell only (r5, 1)',
     '    escaped = shell and _escapes_a_split(rest)', '    escaped = _escapes_a_split(rest)',
     'test_TWIN_a_carrier_that_runs_its_words_directly_keeps_the_separator_literal'),
    ('carrier: an argv carrier asks whether it joins for a shell (r5, 1)',
     '_as_program(tail, _joins_for_a_shell(v, words[:at]))', '_as_program(tail)',
     'test_an_escaped_separator_reaches_the_shell_a_carrier_hands_its_words_to'),
    ('carrier: watch -x runs its words directly (r5, 1)',
     ' or re.fullmatch(r"-[A-Za-z]*x[A-Za-z]*", h) for h in heads)', ' for h in heads)',
     'test_TWIN_a_carrier_that_runs_its_words_directly_keeps_the_separator_literal'),
    ('carrier: ...and watch --exec (r5, 1)',
     'return not any(h == "--exec" or ', 'return not any(',
     'test_TWIN_a_carrier_that_runs_its_words_directly_keeps_the_separator_literal'),
    ('carrier: wsl -e runs its words directly (r5, 1)',
     '        return not any(h in ("-e", "--exec") for h in heads)', '        return True',
     'test_TWIN_a_carrier_that_runs_its_words_directly_keeps_the_separator_literal'),
    ('carrier: wsl joins for a shell (r5, 1)',
     '        return not any(h in ("-e", "--exec") for h in heads)', '        return False',
     'test_an_escaped_separator_reaches_the_shell_a_carrier_hands_its_words_to'),
    ('carrier: no other argv carrier joins for a shell (r5, 1)',
     '    return v == "gsudo"', '    return True',
     'test_TWIN_a_carrier_that_runs_its_words_directly_keeps_the_separator_literal'),
    ('carrier: ssh joins for a shell (r5, 1)',
     'for r in _as_program(tail[k:], True)]', 'for r in _as_program(tail[k:])]',
     'test_an_escaped_separator_reaches_the_shell_a_carrier_hands_its_words_to'),
    ('carrier: the not-found handler may join for a shell (r5, 1)',
     'for r in _as_program(whole[k:], True)]', 'for r in _as_program(whole[k:])]',
     'test_an_escaped_separator_reaches_the_shell_a_carrier_hands_its_words_to'),
    ('carrier: a parallel template with no ::: joins for a shell (r5, 1)',
     '        return ["xargs " + r for r in _as_program(tail, True)]',
     '        return ["xargs " + " ".join(tail)]',
     'test_an_escaped_separator_reaches_the_shell_a_carrier_hands_its_words_to'),
    ('carrier: a dynamic program may be eval, which re-reads an escape (r5, 1)',
     '        for r in [rest, joined] + _requoted(toks, _escapes_a_split(rest)):',
     '        for r in [rest, joined]:',
     'test_an_escaped_separator_reaches_the_shell_a_carrier_hands_its_words_to'),
    ('carrier: an escaped separator keeps a quoted word whole too (r5, 1)',
     '    return [" ".join(_sh_quote(t) if q else t for t, q in toks)] if escaped else []',
     '    return []', 'test_an_escaped_separator_before_a_quoted_path_judges_the_path_whole'),
    ('carrier: ...for an argv carrier (r5, 1)',
     '    return [rest, " ".join(t for t, _q in toks)] + _requoted(toks, escaped)',
     '    return [rest, " ".join(t for t, _q in toks)]',
     'test_an_escaped_separator_before_a_quoted_path_judges_the_path_whole'),
    ('carrier: ...for eval (r5, 1)',
     '                out.extend(_requoted(toks[k + 1:], _escapes_a_split(seg)))', '                pass',
     'test_an_escaped_separator_before_a_quoted_path_judges_the_path_whole'),
    ('eval: an operand keeps its flags (r5, 2)',
     '            parts = [t for t, _ in toks[k + 1:]]' + NL,
     '            parts = [t for t, _ in toks[k + 1:] if not t.startswith("-")]' + NL,
     'test_eval_keeps_the_flags_of_the_command_it_runs'),
    ('find: -type is read (r5, 3)',
     '        if w in ("-type", "-xtype"):' + NL + '            return ("name", w, take())',
     '        if False:' + NL + '            return ("name", w, take())',
     'test_TWIN_a_depth_0_root_skipped_by_its_type'),
    ('find: a type naming no folder skips the root (r5, 3)',
     'return False if folder and re.fullmatch(r"[bcpfsD](?:,[bcpfsD])*", value) else None',
     'return None',
     'test_TWIN_a_depth_0_root_skipped_by_its_type'),
    ('find: ...the root only (r5, 3)',
     'return False if folder and re.fullmatch(', 'return False if re.fullmatch(',
     'test_a_depth_0_root_matched_by_its_type_still_fires'),
    ('find: ...a link to a folder is not skipped (r5, 3)',
     're.fullmatch(r"[bcpfsD](?:,[bcpfsD])*", value)', 're.fullmatch(r"[bcpfsDl](?:,[bcpfsDl])*", value)',
     'test_a_depth_0_root_matched_by_its_type_still_fires'),
    ('find: ...nor a folder in a list (r5, 3)',
     're.fullmatch(r"[bcpfsD](?:,[bcpfsD])*", value)', 're.search(r"[bcpfsD]", value)',
     'test_a_depth_0_root_matched_by_its_type_still_fires'),
    ('find: the root is the folder entry (r5, 3)',
     '        entries.append((hay, name, ending, dir_modes, (0, 0), True))',
     '        entries.append((hay, name, ending, dir_modes, (0, 0), False))',
     'test_TWIN_a_depth_0_root_skipped_by_its_type'),
    # --- 4.1.1 review round 6 (GitHub issue #18): one per clause
    ('find: an -exec command is a reading of its own (r6, A)',
     '    if v == "find" and ("-exec" in seg or "-ok" in seg):', '    if False:',
     'test_a_find_exec_command_is_judged_as_a_command'),
    ('find: -exec words are quoted back, not joined bare (r6, A)',
     '    text = " ".join(_sh_quote(w) for w in words)', '    text = " ".join(words)',
     'test_a_shell_text_of_one_escaped_word_keeps_its_separator'),
    ('find: every segment of a carried -exec text (r6, A)',
     'in DELETE_VERBS for x in _inner_commands(text) for s in segments(x))',
     'in DELETE_VERBS for x in _inner_commands(text) for s in [x])',
     'test_a_shell_text_of_one_escaped_word_keeps_its_separator'),
    ('find: `+` ends an -exec only after {} (r6, A)',
     '(words[j] == "+" and words[j - 1] == "{}")', '(words[j] == "+")',
     'test_TWIN_harmless_finds_keep_their_verdict'),
    ('find: a regenerable-cache find drops a bare {} (r6, A)',
     '                    if w == "{}" and regenerable:', '                    if False:',
     'test_TWIN_harmless_finds_keep_their_verdict'),
    ('find: {} is an entry under the starting point (r6, A)',
     '                    parts.append(item.join(_sh_quote(p) if p else "" for p in pieces) if len(pieces) > 1',
     '                    parts.append(_sh_quote(w) if len(pieces) > 1',
     'test_the_entry_is_a_path_under_the_starting_point'),
    ('find: -execdir runs in the entry folder (r6, A)',
     '            if act in ("-exec", "-ok"):', '            if True:',
     'test_execdir_runs_in_the_entry_folder'),
    ('carrier: sshpass runs its argv (r6, B)',
     '    "sshpass": ({"-p", "-f", "-d", "-P"}, 0, False),', '',
     'test_a_wrapper_that_runs_its_argv_carries_the_command'),
    ('carrier: valgrind runs its argv (r6, B)',
     '    "valgrind": (frozenset(), 0, False),', '',
     'test_a_wrapper_that_runs_its_argv_carries_the_command'),
    ('carrier: mintty runs its argv (r6, B)',
     '    "mintty": ({"-c", "--config",', '    "mintty_": ({"-c", "--config",',
     'test_a_wrapper_that_runs_its_argv_carries_the_command'),
    ('carrier: the special carriers are carriers (r6, B)',
     '_ALL_CARRIER_VERBS = (frozenset(_ARGV_CARRIERS) | _TEXT_C_CARRIERS | _SPECIAL_CARRIERS',
     '_ALL_CARRIER_VERBS = (frozenset(_ARGV_CARRIERS) | _TEXT_C_CARRIERS',
     'test_a_wrapper_that_runs_its_argv_carries_the_command'),
    ('carrier: gdb runs what follows --args (r6, B)',
     '        at = heads.index("--args") + 1 if "--args" in heads else None', '        at = None',
     'test_a_wrapper_that_runs_its_argv_carries_the_command'),
    ('carrier: autossh is an ssh (r6, B)',
     '        return ["ssh " + " ".join(words[k:])] if k < len(words) else []', '        return []',
     'test_a_wrapper_that_runs_its_argv_carries_the_command'),
    ('carrier: start steps over its switches (r6, B)',
     '    while k < len(words) and heads[k][:1] == "/" and len(heads[k]) > 1 and "/" not in heads[k][1:]:',
     '    while False:',
     'test_a_wrapper_that_runs_its_argv_carries_the_command'),
    ('carrier: ...and its title (r6, B)',
     '    if k < len(words) and words[k][:1] == chr(34):', '    if False:',
     'test_a_wrapper_that_runs_its_argv_carries_the_command'),
    ('carrier: an unknown wrapper may run a shell-joining carrier (r6, B)',
     '        if v not in _NOT_RUNNERS and v not in WRAPPERS and _MAY_CARRY_JOINER.search(seg):',
     '        if False:',
     'test_an_unknown_wrapper_before_a_shell_joining_carrier'),
    ('carrier: ...but a printer runs nothing (r6, B)',
     '        if v not in _NOT_RUNNERS and v not in WRAPPERS and _MAY_CARRY_JOINER.search(seg):',
     '        if v not in WRAPPERS and _MAY_CARRY_JOINER.search(seg):',
     'test_TWIN_harmless_wrapper_use'),
    ('carrier: ...only before ssh, watch or wsl (r6, B)',
     '_verb_name(_head(words[k])) not in _SHELL_JOINERS:', '_verb_name(_head(words[k])) in ("",):',
     'test_TWIN_harmless_wrapper_use'),
    ('ssh: an unplaced host records its command (r6, C)',
     '        _SSH_UNPLACED.extend(remote)', '        pass',
     'test_a_destructive_command_to_an_unplaced_host_is_advised'),
    ('ssh: ...and this machine is still judged as here (r6, C)',
     '    if not _is_this_machine(_head(words[at])):', '    if True:',
     'test_TWIN_a_harmless_command_or_this_machine_is_not_that_advisory'),
    ('ssh: only a destructive command is advised (r6, C)',
     'if any(whole or not outer.get(k) for k in _SSH_MEMO[text]):', 'if True:',
     'test_TWIN_a_harmless_command_or_this_machine_is_not_that_advisory'),
    ('find: an -exec that changes the entry is an action (r6, D)',
     '                return ("act", _exec_entry_effect(ran) or "exec", None)',
     '                return ("act", "exec", None)',
     'test_a_find_that_changes_what_it_finds_can_reach_an_archive'),
    ('find: ...the -exec reaches the archive rule (r6, D)',
     '                          for op in prep(find_entry_write_roots(s), c_cwd, c_old)]',
     '                          for op in prep([], c_cwd, c_old)]',
     'test_a_find_that_changes_what_it_finds_can_reach_an_archive'),
    ('find: a changed file entry counts (r6, D)',
     '    file_modes = ("delete", "rdelete", "write", "move") if mode == "delete" else ("print",)',
     '    file_modes = ("delete", "rdelete") if mode == "delete" else ("print",)',
     'test_a_find_that_changes_what_it_finds_can_reach_an_archive'),
    ('find: a folder moved away counts (r6, D)',
     '    dir_modes = {"delete": ("rdelete", "move"),', '    dir_modes = {"delete": ("rdelete",),',
     'test_a_find_that_changes_what_it_finds_can_reach_an_archive'),
    ('find: a write into a DLC folder counts (r6, D)',
     '    dlc_dir_modes = dir_modes + {"delete": ("write",), "write": ("print",)}.get(mode, ())',
     '    dlc_dir_modes = dir_modes',
     'test_a_find_that_changes_what_it_finds_can_reach_an_archive'),
    ('find: ...into a DLC folder only (r6, D)',
     '        modes = (dlc_dir_modes if ending == "" else dir_modes) if folder else file_modes',
     '        modes = dlc_dir_modes if folder else file_modes',
     'test_TWIN_copying_what_a_find_finds_out_is_no_write'),
    ('find: a write reaches DLC content from the game folder (r6, D)',
     '    wide = bool(changes) and _holds_a_dlc(r, g)', '    wide = False',
     'test_a_find_that_changes_what_it_finds_can_reach_an_archive'),
    ('find: the entry as a source is no write (r6, D)',
     '    return p == _FIND_ENTRY or p.startswith(_FIND_ENTRY + "/")', '    return True',
     'test_TWIN_copying_what_a_find_finds_out_is_no_write'),
    ('find: a write INTO the entry counts (r6, D)',
     '    return p == _FIND_ENTRY or p.startswith(_FIND_ENTRY + "/")', '    return p == _FIND_ENTRY',
     'test_a_find_that_changes_what_it_finds_can_reach_an_archive'),
    ('find: moving the entry is a move (r6, D)',
     '            if any(_is_entry(h) for h in move_sources(s) + replaced_sources(s) + rsync_moved_sources(s)):',
     '            if False:',
     'test_a_find_that_changes_what_it_finds_can_reach_an_archive'),
    ('find: perl -i writes the entry (r6, D)',
     '            if _verb_name(verb(s)) == "perl" and', '            if False and',
     'test_a_find_that_changes_what_it_finds_can_reach_an_archive'),
    ('find: `+` after {} ends a changing -exec (r6, D)',
     ' and (words[j] != "+" or words[j - 1] != "{}"):', ':',
     'test_a_find_that_changes_what_it_finds_can_reach_an_archive'),
    ('find: ...and only after {} (r6, D)',
     ' and (words[j] != "+" or words[j - 1] != "{}"):', ' and words[j] != "+":',
     'test_TWIN_copying_what_a_find_finds_out_is_no_write'),
    ('xargs: a writer fed by a find reaches the archive rule (r6, D)',
     '        x_effect = xargs_entry_effect(s, prev)', '        x_effect = None',
     'test_a_find_that_changes_what_it_finds_can_reach_an_archive'),
    ('xargs: -I puts the item where it says (r6, D)',
     '    words = [w.replace(repl, _FIND_ENTRY) for w in words] if repl else words + [_FIND_ENTRY]',
     '    words = words + [_FIND_ENTRY]',
     'test_TWIN_copying_what_a_find_finds_out_is_no_write'),
    ('game archive: {} with a literal tail is an archive only if it can end one (r6, D)',
     '        return bool(tail) and any(tail.endswith(e) if len(tail) >= len(e) else e.endswith(tail)',
     '        return True or any(tail.endswith(e) if len(tail) >= len(e) else e.endswith(tail)',
     'test_TWIN_copying_what_a_find_finds_out_is_no_write'),
    # --- 4.1.1 review round 7: one per clause
    ('ssh: other unplaced ssh segments are no-ops in a splice (r7, 1)',
     '        others = {m: [":"] for m in ssh_here if m != n}', '        others = {}',
     'test_a_chain_of_harmless_ssh_commands_is_quick_and_quiet'),
    ('ssh: the advisory stops at its budget and advises (r7, 1)',
     '                if _SSH_JUDGED[0] >= _MAX_SSH_JUDGED:', '                if False:',
     'test_a_spent_advisory_budget_advises'),
    ('ssh: more remote commands than are judged advise (r7, 1)',
     '    if len(distinct) > _MAX_SSH_UNPLACED and not _SSH_NESTED[0]:', '    if False:',
     'test_a_spent_advisory_budget_advises'),
    ('deadline: past it the analysis stops (r7, 1)',
     '    if d is not None and _clock() > d:', '    if False:',
     'test_an_analysis_past_its_deadline_is_refused'),
    ('deadline: ...and the analyser reports rc 6 (r7, 1)',
     '    if f.get("analysis_timeout"):', '    if False:',
     'test_an_analysis_past_its_deadline_is_refused'),
    ('positional: a shell text is read with its parameters filled in (r7, 2)',
     '                    if filled != toks[at][0]:', '                    if False:',
     'test_a_shell_texts_positional_parameters_are_its_operands'),
    ('positional: $@ starts at $1 (r7, 2)',
     'vals = args[1:] if name in "@*" else', 'vals = args if name in "@*" else',
     'test_a_shell_texts_positional_parameters_are_its_operands'),
    ('positional: "$@" is one word per argument (r7, 2)',
     '            rep = (chr(34) + " " + chr(34)).join(', '            rep = " ".join(',
     'test_a_shell_texts_positional_parameters_are_its_operands'),
    ('positional: not inside single quotes (r7, 2)',
     '        if k in ("' + chr(39) + '", "$' + chr(39) + '") or (m.start()', '        if (m.start()',
     'test_TWIN_a_positional_parameter_that_is_only_read'),
    ('positional: ...nor escaped (r7, 2)',
     ' or (m.start() and text[m.start() - 1] == chr(92)):', ':',
     'test_TWIN_a_positional_parameter_that_is_only_read'),
    ('compressor: a replacing one takes its operand away (r7, 2)',
     'shorts & set("kctl"):' + NL + '        return []' + NL + '    return ops',
     'shorts & set("kctl"):' + NL + '        return []' + NL + '    return []',
     'test_a_replacing_compressor_rename_or_moving_rsync_moves_its_operand'),
    ('compressor: ...unless it keeps it (r7, 2)',
     '    if longs & _KEEPING_LONG or shorts & set("kctl"):',
     '    if longs & _KEEPING_LONG:',
     'test_TWIN_a_compressor_that_keeps_its_operand'),
    ('compressor: zstd keeps it without --rm (r7, 2)',
     '_RM_TO_REPLACE = frozenset({"zstd", "unzstd", "brotli"})', '_RM_TO_REPLACE = frozenset({"unzstd", "brotli"})',
     'test_TWIN_a_compressor_that_keeps_its_operand'),
    ('rename: its files are moved (r7, 2)',
     '    if v == "rename":' + NL + '        return _operands(seg)[1:]',
     '    if v == "rename":' + NL + '        return []',
     'test_a_replacing_compressor_rename_or_moving_rsync_moves_its_operand'),
    ('compressor: the plain form reaches the move rules (r7, 2)',
     '        mv_src += prep(move_sources(s) + replaced_sources(s), c_cwd', '        mv_src += prep(move_sources(s), c_cwd',
     'test_a_replacing_compressor_rename_or_moving_rsync_moves_its_operand'),
    ('find: rsync --remove-source-files of the entry is a move (r7, 2)',
     'move_sources(s) + replaced_sources(s) + rsync_moved_sources(s)', 'move_sources(s) + replaced_sources(s)',
     'test_a_replacing_compressor_rename_or_moving_rsync_moves_its_operand'),
    ('xargs: a pass-on filter keeps the find as the producer (r7, 2)',
     '            prev = _stdin_producer([t[0] for t in tracked[:k]])', '            prev = tracked[k - 1][0] if k else None',
     'test_a_pass_on_filter_between_find_and_xargs'),
    ('xargs: ...a filter reading a file does not (r7, 2)',
     '    return len(ops) <= (1 if v in ("grep", "egrep", "fgrep", "awk", "gawk", "mawk") else 0)', '    return True',
     'test_a_pass_on_filter_between_find_and_xargs'),
    ('find: gfind is find (r7, 3)',
     '    if n == "gfind":', '    if False:', 'test_gfind_and_fd_exec'),
    ('fd: -x runs a command per result (r7, 3)',
     '    if v in ("fd", "fdfind") and', '    if False and', 'test_gfind_and_fd_exec'),
    ('fd: the result is appended with no placeholder (r7, 3)',
     '            parts = [_sh_quote(w) for w in ran] + [item]', '            parts = [_sh_quote(w) for w in ran]',
     'test_gfind_and_fd_exec'),
    # --- 4.1.1 review round 8: one per clause
    ('positional: a later use past the budget is unknown (r8, F1)',
     '        if name in seen and budget <= 0:', '        if False:',
     'test_a_repeated_positional_fill_is_bounded'),
    ('positional: ...but a FIRST use is always filled (r8, F1)',
     '        if name in seen and budget <= 0:', '        if budget <= 0:',
     'test_a_repeated_positional_fill_is_bounded'),
    ('positional: an empty "$@" is no word (r8)',
     '            start, end, rep = start - 1, end + 1, ""   # an empty "$@" is no word',
     '            rep = ""', 'test_an_empty_quoted_at_is_no_word'),
    ('positional: xargs supplies "$@" (r8)',
     '                        args = args + _producer_items(_stdin_producer(segs[:n]))',
     '                        args = args', 'test_an_empty_quoted_at_is_no_word'),
    ('positional: a variable assigned a positional is written out (r8, F3)',
     '                        out.append(_inline_assigned(filled, toks[at][0]))',
     '                        out.append(filled)', 'test_a_positional_through_a_variable'),
    ('readings: one segment has a bounded number (r8, F1)',
     '            if len(carried_here) > _MAX_SEGMENT_READINGS:', '            if False:',
     'test_many_find_actions_are_bounded'),
    ('carriers: the carried text is bounded in characters (r8, F1)',
     '                    if size > _MAX_CARRIED_CHARS:', '                    if False:',
     'test_many_find_actions_are_bounded'),
    ('find: an -exec is read up to its terminator (r8, F1)',
     '_exec_deletes(toks[i + 1:_exec_end(toks, i)])', '_exec_deletes(toks[i + 1:])',
     'test_many_find_actions_are_bounded'),
    ('powershell: no time left is a timeout (r8, F2)',
     '    if left <= 0:', '    if False:', 'test_a_powershell_translation_is_bounded_by_the_deadline'),
    ('powershell: a call is bounded by the time left (r8, F2)',
     '    cap = min(_PS_CALL_CAP_S, remaining, left)', '    cap = min(_PS_CALL_CAP_S, remaining)',
     'test_a_powershell_translation_is_bounded_by_the_deadline'),
    ('compressor: a short option value is no flag (r8, F3)',
     '            if c in values:', '            if False:', 'test_replacing_programs_and_their_option_values'),
    ('decompressor: its output is a write (r8, F3)',
     'modify_targets(s) + decompress_outputs(s), c_cwd', 'modify_targets(s), c_cwd',
     'test_replacing_programs_and_their_option_values'),
    ('zip -m moves its files (r8, F3)',
     '        return _operands(seg)[1:] if moves else []', '        return []',
     'test_replacing_programs_and_their_option_values'),
    ('zip: ...only with -m (r8, F3)',
     '        return _operands(seg)[1:] if moves else []', '        return _operands(seg)[1:]',
     'test_TWIN_replacing_programs_that_keep_the_file'),
    ('tar --remove-files moves its files (r8, F3)',
     '        return _tar_removed(seg)', '        return []',
     'test_replacing_programs_and_their_option_values'),
    ('brotli/zstd keep the file without --rm (r8, F3)',
     '    if v in _RM_TO_REPLACE and "--rm" not in longs:', '    if False:',
     'test_TWIN_replacing_programs_that_keep_the_file'),
    ('filters: sed -i passes nothing on (r8, e)',
     '            return False                     # sed -i edits files', '            return True                      # sed -i edits files',
     'test_sed_awk_and_cut_pass_the_find_on'),
    ('fd: the reach test of a find (r8, F3)',
     '        fd_find = _fd_as_find(s) if verb(s) in ("fd", "fdfind") else None',
     '        fd_find = None', 'test_fd_reaches_like_find'),
    ('fd: --search-path is a search path (r8, F3)',
     '    paths = search + pos[1:] if search else pos[1:]', '    paths = pos[1:]',
     'test_fd_reaches_like_find'),
    ('ssh: the remote command is spliced in place too (r6, C)',
     '        _SSH_UNPLACED_SPLICED.extend(_reading_variants(cmd, segs, {n: remote, **others}))',
     '        pass',
     'test_the_remote_command_is_expanded_here_first'),
    ('ssh: ...counting only what this command does not already do (r6, C)',
     'if any(whole or not outer.get(k) for k in _SSH_MEMO[text]):',
     'if _SSH_MEMO[text]:',
     'test_TWIN_this_commands_own_delete_is_not_the_remote_one'),
    ('ssh: ...one hop further is still judged (r6, C)',
     '    keys = _SSH_DESTRUCTIVE + ("ssh_host_unplaced",)', '    keys = _SSH_DESTRUCTIVE',
     'test_a_destructive_command_to_an_unplaced_host_is_advised'),
    # The PowerShell half (ps_translate.ps1 WithUnquoted, r4 3/4) is not in this table, which
    # mutates hook_facts.py only: its four mutants were run by hand (BLIND-SPOTS F356).
    ("a verb carried in a variable is resolved before the rules see it",
     "            rs = resolve_verb(s, assigns)",
     "            rs = s",
     "test_rm_through_a_variable_still_hits_the_game_root"),
    ("carriers are followed more than one level",
     "    for depth in range(_MAX_CARRIER_DEPTH + 1):", "    for depth in range(0):",
     "test_a_shell_inside_a_shell"),
    ("a filter must actually narrow",
     "    return arg not in _UNIVERSAL", "    return True",
     "test_a_universal_name_glob_is_not_a_narrowing"),
    ("the carrier walk ANNOUNCES its bound",
     "                        return finish(out[:_MAX_CARRIED], True)",
     "                        return finish(out[:_MAX_CARRIED], False)",
     "test_a_TANGLED_command_says_so_instead_of_passing_quietly"),
    ("no fact-stream value can carry a separator",
     '    return str(v).replace(chr(13), " ").replace(chr(10), " ").replace(chr(9), " ")',
     "    return str(v)",
     "test_no_value_can_carry_a_field_separator"),
    ("the name backstop sees a relative operand",
     "    rm_named_game = any(GAME_ROOTISH.search(norm(p or raw)) for p, _u, raw in rm_t)",
     "    rm_named_game = any(GAME_ROOTISH.search(norm(p)) for p, _u, raw in rm_t)",
     "test_a_named_operand_after_a_RELATIVE_cd_fires"),
    # --- lifting the OS deny on reference/ (Plan 2 lane E), one mutant per clause ----
    ("x4refguard REMOVE is a lift",
     '                    and ("remove" in toks[i + 1:]', "                    and (False",
     "test_x4refguard_remove_fires"),
    ("only a RUN x4refguard counts, not a mention",
     "    if v.startswith(_REFGUARD_RUNNERS) or has_unresolved(v):", "    if True:",
     "test_x4refguard_status_or_apply_does_not_fire"),
    # --- R2-F2 / R2-P1 (v4.0.0 review, lane FX-G): a lift and a cd judged where they run ---
    ("a runner named by a VARIABLE counts",
     "    if v.startswith(_REFGUARD_RUNNERS) or has_unresolved(v):",
     "    if v.startswith(_REFGUARD_RUNNERS):",
     "test_a_runner_named_by_a_variable_or_a_launcher_lifts"),
    ("an icacls operand that is a root variable is its root",
     "        r = join_cwd(cwd, subst_root_var(rv, roots))      # every value: FX-G6 / K C1",
     "        r = join_cwd(cwd, rv)      # every value: FX-G6 / K C1",
     "test_a_root_variable_operand_lifts"),
    ("an icacls operand is judged where it runs",
     "        r = join_cwd(cwd, subst_root_var(rv, roots))      # every value: FX-G6 / K C1",
     "        r = subst_root_var(rv, roots)      # every value: FX-G6 / K C1",
     "test_a_relative_operand_resolves_against_the_cwd"),
    ("a cd to a root variable lands in that root",
     "        cands = [subst_root_var(r, roots) for r in resolve_variants(ops[0], assigns)]",
     "        cands = list(resolve_variants(ops[0], assigns))",
     "test_a_cd_to_a_root_variable_judges_like_the_direct_form"),
    ("R2-F5: a segment that names its own folder keeps its ask, not the deny",
     "        not any(hit(named, k, conservative=True) for k in _wipe_keys)",
     "        True",
     "test_TWIN_the_explicit_folder_form_still_ASKS_and_is_not_this_deny"),
    ("R2-F5: a folder named ELSEWHERE does not excuse a bare wipe",
     '        "git_wipe_from_session_dir": git_wipe_bare,',
     '        "git_wipe_from_session_dir": git_wipe_bare and not git_wipe_named,',
     "test_a_named_wipe_ELSEWHERE_in_the_command_does_not_hide_a_bare_one"),
    ("an unset root is never substituted",
     "    if not root:" + NL + "        return tok" + NL + "    return root + tok[end:]",
     "    return root + tok[end:]",
     "test_TWIN_an_unset_root_is_not_the_session_directory"),
    ("only the LEADING variable is replaced",
     "    return root + tok[end:]", "    return root",
     "test_what_follows_the_variable_is_kept"),
    ("H2: a root variable under a brace OPERATOR is its root",
     "        m2 = _ROOT_VAR_OP_HEAD.match(tok)" + NL + "        if not m2:",
     "        m2 = None" + NL + "        if not m2:",
     "test_a_root_variable_under_a_brace_operator_is_its_root"),
    ("H2: an unconfigured root under an operator is still NAMED",
     r'_VAR_ANY = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)|\$([A-Za-z_][A-Za-z0-9_]*)")',
     r'_VAR_ANY = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")',
     "test_an_unconfigured_root_under_an_operator_still_refuses_a_delete"),
    ("an icacls lift needs /remove or /reset",
     "    if not any(t in _ICACLS_LIFTS for t in switches):", "    if False:",
     "test_icacls_reading_applying_or_elsewhere_does_not_fire"),
    ("an icacls ancestor counts only with /T",
     '    recursive = "/t" in switches', "    recursive = True",
     "test_icacls_reading_applying_or_elsewhere_does_not_fire"),
    ("an icacls ancestor with /T counts",
     "        if under(r, ref) or (walks and contains_root(r, ref)):",
     "        if under(r, ref):",
     "test_icacls_reset_recursive_from_an_ANCESTOR_of_reference_fires"),
    ("an icacls lift INSIDE reference counts",
     "        if under(r, ref) or (walks and contains_root(r, ref)):",
     "        if is_root(r, ref) or (walks and contains_root(r, ref)):",
     "test_icacls_remove_or_reset_on_reference_fires"),
    # --- lane F: relative operands resolve against the payload cwd, one mutant per clause ---
    ("the payload cwd seeds the shell's directory",
     '    return c if isinstance(c, str) else ""', '    return ""',
     "test_a_relative_delete_and_write_resolve_against_the_payload_cwd"),
    ("only a STRING cwd is passed on",
     '    return c if isinstance(c, str) else ""', '    return c or ""',
     "test_TWIN_no_relative_or_garbage_cwd_is_unchanged_from_before"),
    ("a carrier runs in the session cwd when nothing moves",
     '        tracked = cwd_lanes(c, base if (top or not moves) else "", seeded=True, roots=roots)',
     '        tracked = cwd_lanes(c, base if top else "", seeded=True, roots=roots)',
     "test_a_carrier_runs_in_the_session_cwd_when_nothing_changes_directory"),
    ("a carrier after a directory change is not seeded",
     '        tracked = cwd_lanes(c, base if (top or not moves) else "", seeded=True, roots=roots)',
     '        tracked = cwd_lanes(c, base, seeded=True, roots=roots)',
     "test_TWIN_a_carrier_after_a_directory_change_is_not_seeded"),
    ("cd/pushd count as a directory change",
     '    moves = any(verb(s) in DIR_VERBS or verb(s) == "popd"',
     '    moves = any(verb(s) == "popd"',
     "test_TWIN_a_carrier_after_a_directory_change_is_not_seeded"),
    ("popd counts as a directory change",
     '    moves = any(verb(s) in DIR_VERBS or verb(s) == "popd"',
     '    moves = any(verb(s) in DIR_VERBS',
     "test_TWIN_a_carrier_after_a_directory_change_is_not_seeded"),
    ("an unresolved cd from the seed is unknowable",
     "                elif from_seed and has_unresolved(tgt) and not is_abs(tgt):",
     "                elif False:",
     "test_an_unresolved_cd_from_the_seed_is_unknowable"),
    ("...only while the directory is still the seed",
     "                elif from_seed and has_unresolved(tgt) and not is_abs(tgt):",
     "                elif has_unresolved(tgt) and not is_abs(tgt):",
     "test_TWIN_after_an_absolute_cd_the_sticky_join_stands"),
    ("...only for an UNRESOLVED target",
     "                elif from_seed and has_unresolved(tgt) and not is_abs(tgt):",
     "                elif from_seed and not is_abs(tgt):",
     "test_TWIN_a_cd_wins_over_the_payload_cwd"),
    ("...only for a RELATIVE target",
     "                elif from_seed and has_unresolved(tgt) and not is_abs(tgt):",
     "                elif from_seed and has_unresolved(tgt):",
     "test_TWIN_an_unresolved_but_absolute_cd_target_is_joined"),
    ("an absolute cd ends the seed",
     "                    from_seed = from_seed and not is_abs(tgt)",
     "                    pass",
     "test_TWIN_after_an_absolute_cd_the_sticky_join_stands"),
    ("popd restores whether the directory is the seed",
     "            cwd, from_seed = stack.pop()", "            cwd, _ = stack.pop()",
     "test_popd_restores_whether_the_directory_is_the_seed"),
    ("the seed provenance is for facts() only",
     "    from_seed = seeded and bool(base)", "    from_seed = bool(base)",
     "test_the_bare_python_rule_keeps_its_own_base"),
    ("parse debris keeps its pre-seed directory",
     "                d = c_old if (_DEBRIS.search(r) or _DEVICE.match(r)) else c_cwd",
     "                d = c_old if _DEVICE.match(r) else c_cwd",
     "test_parse_debris_is_not_joined_onto_the_seed"),
    ("a Windows device name keeps its pre-seed directory",
     "                d = c_old if (_DEBRIS.search(r) or _DEVICE.match(r)) else c_cwd",
     "                d = c_old if _DEBRIS.search(r) else c_cwd",
     "test_a_windows_device_name_is_not_a_file_in_the_directory"),
    ("git clean/reset --hard is judged unseeded",
     "        gitwipe_t += prep(git_wipes_worktree_targets(s), c_old, c_old)",
     "        gitwipe_t += prep(git_wipes_worktree_targets(s), c_cwd, c_old)",
     "test_git_wipe_does_not_ask_from_the_seed_alone"),
    # v4.0.0 review P2: `git stash -a/--all` (and `-u`) is a git wipe. One per clause.
    ("P2: stash --all reaches the session-dir deny",
     '    return _git_destructive(seg, {"clean", "reset", "stash"}, deep=True)',
     '    return _git_destructive(seg, {"clean", "reset"}, deep=True)',
     "test_bare_stash_all_from_the_game_root_is_the_deny"),
    ("P2: a named stash -a / -u reaches the ask",
     '    return _git_destructive(seg, {"clean", "reset", "stash"})',
     '    return _git_destructive(seg, {"clean", "reset"})',
     "test_a_named_folder_ASKS_and_is_not_the_deny"),
    ("P2: only push/save stash anything",
     '        if rest[0] not in ("push", "save"):',
     "        if False:",
     "test_TWIN_a_stash_subcommand_other_than_push_is_not_a_wipe"),
    ("P2: stash -u is not the deep (deny) reach",
     "    return every if deep else (every or untracked)",
     "    return every or untracked",
     "test_include_untracked_takes_the_plain_clean_f_verdict"),
    ("P2: a stash message is not a flag",
     '        if t == "-m" or _git_long(t, "--message") or _git_long(t, "--pathspec-from-file"):',
     "        if False:",
     "test_TWIN_a_message_that_reads_like_the_flag_is_not_the_flag"),
    # v4.0.0 review FX-P2: heredocs inside a carried command, and expanding heredoc bodies.
    ("FX-P2: a carried command's heredoc body is stripped",
     "                for n_, inner in enumerate(_carrier_parts(i_)):",
     "                for n_, inner in enumerate([i_]):",
     "test_the_reported_commit_idiom_is_not_a_durable_write"),
    ("FX-P2: a shell inside the opener's substitution takes the body",
     "    return any(sink(sg) for c in cands for sg in segments(c))",
     "    return any(sink(sg) for sg in segments(opener))",
     "test_a_shell_fed_body_inside_a_substitution_still_runs"),
    ("FX-P2: an expanding body runs its substitutions",
     "    extra += heredoc_substitutions(spliced)",
     "    extra += []",
     "test_dollar_paren_and_backticks_in_an_unquoted_body_run"),
    ("FX-P2: a quoted delimiter expands nothing",
     """                        not any(ch in word for ch in ("'", '"', chr(92))))""",
     "                        True)",
     "test_TWIN_a_quoted_delimiter_expands_nothing"),
    ("FX-P2: an escaped substitution in a body is text",
     "            i += 2                              # an escaped character is text",
     "            i += 1",
     "test_TWIN_an_escaped_substitution_is_text"),
    ("an unknown cmdlet is judged unseeded",
     "            ops = prep(_operands(s), c_old, c_old)",
     "            ops = prep(_operands(s), _c, c_old)",
     "test_an_unknown_cmdlet_does_not_ask_from_the_seed_alone"),
    # --- FX-G2 (v4.0.0 delta review): one mutant per fix --------------------------------
    ("G2 items 1-2: a root variable operand is its root",
     "                r = r if literal else subst_root_var(r, roots)", "                pass",
     "test_every_root_variable_and_verb_judges_like_the_literal"),
    ("G2 item 3: Windows aliases are the file",
     '        s = head + _SLASHES.sub("/", _win_alias(_SLASHES.sub("/", rest)))',
     '        s = head + _SLASHES.sub("/", rest)',
     "test_trailing_dots_spaces_and_stream_suffixes_are_the_file"),
    ("G2 item 3: a command NAME keeps its colon",
     "            if i > 0 and n > 0:", "            if i > 0:",
     "test_an_UNQUOTED_windows_path_is_not_that_command_at_all"),
    ("G2 item 4: arithmetic yields its substitutions",
     "                    out.extend(substitutions(text))", "                    pass",
     "test_a_substitution_inside_arithmetic_reaches_the_hard_block"),
    ("G2 item 5: a git long-option PREFIX is the option",
     '    return len(name) >= least and name.startswith("--") and opt.startswith(name)',
     "    return name == opt",
     "test_an_abbreviated_destructive_option_is_the_option"),
    ("E1: the quote state crosses lines",
     "        q = _line_quotes(line, q)[1]", '        q = ""',
     "test_a_marker_inside_a_multiline_quote_hides_nothing"),
    ("E2: `EOF)` ends a heredoc",
     '    return s.startswith(term) and s[len(term):].lstrip()[:1] in (")", chr(96))',
     "    return False",
     "test_EOF_paren_ends_the_body"),
    ("H5: a backtick after the delimiter ends a heredoc",
     '    return s.startswith(term) and s[len(term):].lstrip()[:1] in (")", chr(96))',
     '    return s.startswith(term) and s[len(term):].lstrip()[:1] in (")",)',
     "test_EOF_backtick_ends_the_body"),
    ("H5: arithmetic, $[ ], ${ } and a[ ]= open no heredoc",
     '        elif c == "(" and d == "(" and (i == 0 or line[i - 1] in " " + chr(9) + ";&|({!"):',
     '        elif False:',
     "test_shift_in_arithmetic_command_subscript_or_expansion_opens_no_heredoc"),
    ("E3: a body flowing into a piped shell runs",
     "            if piped or _opener_feeds(opener, sink)]",
     "            if _opener_feeds(opener, sink)]",
     "test_a_body_that_flows_into_a_piped_shell_is_commands"),
    ("E3: a trailing `|` continues the pipeline",
     "        elif sep == \"|\" and next_sep == chr(10):", "        elif False:",
     "test_a_pipe_ending_a_line_continues_onto_the_next"),
    ("E4: cmd with no /c reads stdin",
     "        return inline + [cmd_to_sh([ln]) for t_ in texts for ln in t_.split(chr(10)) if ln.strip()]",
     "        return inline",
     "test_echo_into_cmd_and_a_cmd_heredoc"),
    ("H1: cmd /k reads stdin after its inline command",
     "        if _CMD_KEEP_SWITCH.fullmatch(t):" + NL + "            return True",
     "        if _CMD_KEEP_SWITCH.fullmatch(t):" + NL + "            return False",
     "test_cmd_k_reads_its_program_from_stdin_too"),
    ("H1: cmd /c cmd hands stdin to a cmd that reads it",
     '            return bool(rest) and _verb_name(rest[0]) == "cmd" and _cmd_toks_read_stdin(rest)',
     "            return False",
     "test_cmd_c_cmd_reads_stdin_through_the_child"),
    ("E5: `$'` opens an ANSI-C quote",
     "            q = \"$'\" if (c == \"'\" and dollar) else c", "            q = c",
     "test_an_escaped_quote_in_ansi_c_ends_nothing"),
    ("E6: --work-tree names the wiped folder",
     "            elif t == \"--work-tree\":", "            elif False:",
     "test_the_work_tree_spellings_name_the_folder"),
    ("E6: clean.requireForce=false forces a clean",
     'or any(_git_long(t, "--force") for t in rest) or forced',
     'or any(_git_long(t, "--force") for t in rest)',
     "test_requireForce_false_makes_an_unforced_clean_delete"),
    ("E7: every ACL-modifying icacls switch lifts",
     '_ICACLS_LIFTS = ("/remove", "/reset", "/grant", "/inheritance", "/restore", "/setowner",',
     '_ICACLS_LIFTS = ("/remove", "/reset", "/zz1", "/zz2", "/zz3", "/zz4",',
     "test_every_acl_modifying_switch_lifts"),
    ("F257: slash-rooted operands are not icacls switches",
     "        if lo in _ICACLS_SWITCHES and out:", "        if lo.startswith(chr(47)):",
     "test_icacls_absolute_slash_operands_are_paths_not_switches"),
    ("F257: the first /c operand is the drive, not the /C switch",
     "        if lo in _ICACLS_SWITCHES and out:", "        if lo in _ICACLS_SWITCHES:",
     "test_icacls_drive_operand_is_not_the_continue_switch"),
    ("F257: switch values cannot become paths or action switches",
     "    out, switches, take = [], [], 0\n    for o in ([] if k is None else toks[k + 1:]):\n        if take:",
     "    out, switches, take = [], [], 0\n    for o in ([] if k is None else toks[k + 1:]):\n        if False:",
     "test_icacls_switch_values_are_not_target_paths"),
    # FX-H2 / reviewer L: the switch icacls receives, not the token as typed
    ("H2-L: a doubled-slash token is the switch (Git Bash //reset)",
     '    if re.fullmatch(r"//[a-z?]+", lo):' + NL + '        lo = lo[1:]' + NL, "",
     "test_a_doubled_slash_switch_is_the_switch"),
    ("H2-L: every /inheritance* spelling is /inheritance (MEASURED icacls prefix)",
     '    if lo.startswith("/inheritance"):' + NL + '        lo = "/inheritance"' + NL, "",
     "test_every_inheritance_spelling_icacls_accepts_lifts"),
    ("H2-L: a prefix icacls rejects is not /inheritance",
     'if lo.startswith("/inheritance"):', 'if lo.startswith("/inh"):',
     "test_TWIN_a_prefix_icacls_rejects_is_not_inheritance"),
    ("H2-L: /findsid takes a value",
     '"/substitute", "/findsid")', '"/substitute")',
     "test_findsid_value_is_not_a_path_and_findsid_is_not_a_lift"),
    ("H2-L: //T walks from an ancestor",
     '    recursive = "/t" in switches', '    recursive = False',
     "test_a_doubled_T_walks_from_an_ancestor"),
    ("settings rule: a shell write to .claude/settings*.json",
     "            _AGENT_SETTINGS.search(norm(pp or rr))", "            False",
     "test_every_write_primitive_to_a_settings_file_fires"),
    # --- FX-G4 (v4.0.0 delta review, reviewers G and H) -------------------------------------
    ("H6: git config from the environment forces a clean",
     "    if _GIT_ENV_CONFIG[0] or any(_GIT_CONFIG_ENV_SET.match(t) for t in toks):",
     "    if False:",
     "test_git_config_env_forces_an_unforced_clean"),
    ("H7: what echo/printf prints into a shell is its program",
     "                out.extend(_echo_printf_program(prev))", "                pass",
     "test_echo_or_printf_piped_into_a_shell_is_its_program"),
    ("H8: `bash -n` executes nothing",
     '                syntax_only = t[0] == "-"',
     "                syntax_only = False",
     "test_TWIN_a_harmless_program_a_non_shell_or_a_script_argument_is_not"),
    ("H8: a process substitution run as a script is its program",
     "        out.extend(_procsub_program(seg))", "        pass",
     "test_a_process_substitution_run_as_a_script_is_its_program"),
    ("G-OUT: a variable holding a substitution is an unresolved verb",
     "        return bool(_SUBST.search(resolve(t, assigns)))",
     "        return False",
     "test_a_variable_holding_a_substitution_is_an_unresolved_verb"),
    ("G-OUT: only at a real command position",
     "        if any(_span_open(x) for x in toks[:k]):",
     "        if False:",
     "test_TWIN_a_literal_verb_variable_or_an_operand_elsewhere_is_not_this_rule"),
    ("F183: a substituted verb below the game root advises",
     '                        or hit(subst_rm_t, "game"),', "                        ,",
     "test_a_substituted_verb_below_the_game_root_keeps_the_delete_advisory"),
    ("F183: a relative operand does not join the session directory",
     "            subst_rm_t += prep(_operands(s), c_old, c_old)",
     "            subst_rm_t += prep(_operands(s), c_cwd, c_old)",
     "test_TWIN_a_relative_operand_does_not_join_the_SESSION_directory"),
    ("H9: only the ACTION position can name the x4refguard action",
     "                         or has_unresolved(_refguard_action(toks[i + 1:]))):",
     "                         or any(has_unresolved(a) for a in toks[i + 1:])):",
     "test_TWIN_a_variable_in_an_OPTION_value_is_not_the_action"),
    ("H9: an unresolved action still asks",
     "                         or has_unresolved(_refguard_action(toks[i + 1:]))):",
     "                         or False):",
     "test_an_unresolved_ACTION_asks"),
    ("H-M5: takeown of reference lifts its deny",
     '    if v in ("takeown", "takeown.exe"):', '    if False:',
     "test_takeown_of_reference_lifts_its_deny"),
    ("H-M3: a file:// URL is percent-decoded",
     "    return unquote(_FILE_URL.sub(\"\", o))", "    return _FILE_URL.sub(\"\", o)",
     "test_a_percent_encoded_file_url_is_decoded"),
    # --- FX-G5 (v4.0.0 delta review, reviewer J2) --------------------------------------------
    ("J2-9: the alternate of an UNASSIGNED variable is judged",
     '        return rest if (known or name not in assigns) else ""',
     '        return rest if known else ""',
     "test_the_alternate_of_an_unassigned_variable_is_judged"),
    ("J2-5: -c include.* forces a clean",
     '            elif t == "-c" and toks[i + 1].lower().startswith(("include.", "includeif.")):',
     "            elif False:",
     "test_an_include_or_config_env_forces_the_clean"),
    ("J2-5: an in-command git config of requireForce forces a clean",
     "        _git_config_writes(s_) for c_ in all_cmds for s_ in segments(c_))",
     "        False for c_ in all_cmds for s_ in segments(c_))",
     "test_an_in_command_git_config_of_requireForce_forces_the_clean"),
    ("J2-2: a shell option's VALUE is stepped over",
     '            elif ch in "oO":                         # each o/O takes the next word',
     "            elif False:",
     "test_only_the_shells_OWN_options_and_their_values_are_stepped_over"),
    ("J2-3: an assignment's substitution is one word",
     "    token_index = 0" + NL + "    for w in _raw_words(seg):",
     "    token_index = 0" + NL + "    for w in seg.split():",
     "test_a_prefix_ASSIGNMENT_does_not_hide_the_verb"),
    # --- FX-G6 (v4.0.0 delta review, reviewer K) ---------------------------------------------
    ("K-C1: an unassigned variable has MORE than one value",
     "    if not seen:" + NL + "        return out",
     "    if True:" + NL + "        return out",
     "test_the_empty_branch_is_judged_again"),
    ("K-C1: the states are taken PER NAME",
     "        combos = itertools.product(_UNASSIGNED_STATES, repeat=len(names))",
     "        combos = [(s_,) * len(names) for s_ in _UNASSIGNED_STATES]",
     "test_the_empty_branch_is_judged_again"),
    ("K-C1: past the cap, each name in each state",
     "        combos = [tuple(s if j == i else u for j in range(len(names)))",
     "        combos = [tuple(u for j in range(len(names)))",
     "test_the_empty_branch_is_judged_again"),
    ("K-C1: the colon treats EMPTY as unset",
     '            is_set = state == "empty" and not colon   # `:` treats EMPTY as unset',
     '            is_set = state == "empty"',
     "test_TWIN_the_colon_treats_empty_as_unset"),
    ("K-C1 / FX-G7: every value of a cd target is a lane",
     "            lanes = [step(seg, v, ln, c) for ln in lanes for c in cands]",
     "            lanes = [step(seg, v, ln, c) for ln in lanes for c in cands[:1]]",
     "test_the_empty_branch_is_judged_again"),
    ("FX-G7: a cd with several values splits the lanes (not the one-value fallback)",
     "        if len(lanes) * len(cands) <= _MAX_CD_LANES:",
     "        if False:",
     "test_every_value_of_a_cd_target_anchors_a_relative_operand"),
    ("FX-G7: past the lane bound a lane still takes a root value",
     "            lanes = [step(seg, v, ln, next((c for c in cands",
     "            lanes = [step(seg, v, ln, next((c for c in cands[:1]",
     "test_past_the_lane_bound_a_root_value_is_still_taken"),
    ("FX-G7: each lane keeps its own pushd stack",
     "        cwd, from_seed, stack = lane[0], lane[1], list(lane[2])",
     "        cwd, from_seed, stack = lane[0], lane[1], []",
     "test_TWIN_popd_unwinds_every_lane"),
    ("FX-G7: a work tree named by an assignment names the wipe folder",
     "        if git_wipes_worktree_targets(s):" + NL + "            gitwipe_t += prep([_gitdir_parent(v_)",
     "        if False:" + NL + "            gitwipe_t += prep([_gitdir_parent(v_)",
     "test_an_exported_work_tree_names_the_folder"),
    ("FX-G7: ...only for a segment that IS a wipe",
     "        if git_wipes_worktree_targets(s):" + NL + "            gitwipe_t += prep([_gitdir_parent(v_)",
     "        if True:" + NL + "            gitwipe_t += prep([_gitdir_parent(v_)",
     "test_TWIN_only_a_wipe_reads_the_assignment"),
    ("FX-G7: ...only the GIT_WORK_TREE / GIT_DIR names",
     '                               if k_ in ("GIT_WORK_TREE", "GIT_DIR") and isinstance(v_, str)],',
     '                               if isinstance(v_, str)],',
     "test_TWIN_another_variable_is_not_a_work_tree"),
    ("FX-G7: a later --no-dry-run undoes -n",
     "            elif o == \"--no-dry-run\":" + NL + "                dry = False",
     "            elif o == \"--no-dry-run\":" + NL + "                pass",
     "test_a_later_negation_or_an_exclude_value_is_not_a_dry_run"),
    ("FX-G7: the value of --exclude is not an option",
     '            elif o in ("--exclude", "--pathspec-from-file") and "=" not in t:',
     "            elif False:",
     "test_a_later_negation_or_an_exclude_value_is_not_a_dry_run"),
    ("FX-G7: ...nor the value of -e",
     '            skip = t.find("e", 1) == len(t) - 1',
     "            skip = False",
     "test_a_later_negation_or_an_exclude_value_is_not_a_dry_run"),
    ("FX-G7: a -e with its value attached consumes nothing more",
     '            skip = t.find("e", 1) == len(t) - 1',
     '            skip = "e" in t',
     "test_TWIN_a_real_dry_run_stays_allowed"),
    ("FX-G7: an abbreviation is the option only when unambiguous",
     "    return hits[0] if len(hits) == 1 else None",
     "    return hits[0] if hits else None",
     "test_TWIN_an_ambiguous_prefix_is_not_a_dry_run"),
    ("K-C2: a lone `-` ends the options; the NEXT word is the script",
     "            first = toks[j + 1] if j + 1 < len(toks) else None",
     "            first = (t, q)",
     "test_a_lone_dash_ends_the_options_and_plus_n_cancels_minus_n"),
    ("K-C2: after `--` a `+n` is the script, not an option",
     '        if not q and t in ("-", "--"):',
     "        if False:",
     "test_TWIN_the_last_n_toggle_and_a_script_after_the_dash_decide"),
    ("K-I3: `+n` turns `-n` back off",
     '                syntax_only = t[0] == "-"',
     '                syntax_only = syntax_only or t[0] == "-"',
     "test_a_lone_dash_ends_the_options_and_plus_n_cancels_minus_n"),
    ("K-I3: `+o noexec` turns `-n` back off",
     '                if ch == "o" and val == "noexec" and t[0] == "+":',
     "                if False:",
     "test_a_lone_dash_ends_the_options_and_plus_n_cancels_minus_n"),
    ("K-I4: a `${...}` span is part of its word",
     '                elif (c == "{" and last == "$" or c == chr(96) or c == "(" and (',
     '                elif (c == "" and last == "$" or c == chr(96) or c == "(" and (',
     "test_a_word_holding_a_span_is_ONE_word"),
    ("K-I4: a substitution the segmenter cut gets its own view",
     "    all_cmds[1:1] = views",
     "    all_cmds[1:1] = []",
     "test_a_word_holding_a_span_is_ONE_word"),
    ("K-I4: the view collapses only a span holding a separator",
     '        if any(not mask[e] and cmd[e] in ";&|" + chr(10) for e in range(j + 1, end)):',
     "        if True:",
     "test_TWIN_spans_and_views_change_nothing_else"),
    ("K-I4: the top level's view runs where the top level runs",
     "        top = i_ == 0 or c == top_view",
     "        top = i_ == 0",
     "test_a_word_holding_a_span_is_ONE_word"),
    ("K-I4: the verb-position test reads the same words",
     "        toks = _raw_words(seg)",
     "        toks = tokens_of(seg)",
     "test_a_word_holding_a_span_is_ONE_word"),
    ("K-I2: a READ of git config does not force",
     '    if any(a.startswith(("--get", "--show-")) or a in _GIT_CONFIG_READS for a in args):',
     "    if False:",
     "test_TWIN_a_READ_of_git_config_leaves_the_clean_unforced"),
    ("K-I2: the get/list subcommands read",
     '    return next((a for a in args if not a.startswith("-")), "") not in ("get", "list")',
     "    return True",
     "test_TWIN_a_READ_of_git_config_leaves_the_clean_unforced"),
    ("K-I2: ANY writing git config forces a clean",
     '    if i >= len(toks) or toks[i] != "config" or not toks[i + 1:]:',
     "    if True:",
     "test_ANY_writing_git_config_or_a_moved_HOME_forces_the_clean"),
    ("K-C1: a shell -c carrier carries every value",
     "                    values = resolve_variants(arg, assignments(cmd))",
     "                    values = [resolve(arg, assignments(cmd))]",
     "test_the_empty_branch_is_judged_again"),
    ("K-C1: eval carries every value",
     '                out.extend(resolve_variants(" ".join(parts), assignments(cmd)))   # FX-G6 / K C1',
     '                out.append(resolve(" ".join(parts), assignments(cmd)))',
     "test_the_empty_branch_is_judged_again"),
    ("K-I2: a moved HOME forces a clean",
     "                              or _GIT_HOME_SET.search(body))",
     "                              or False)",
     "test_ANY_writing_git_config_or_a_moved_HOME_forces_the_clean"),
    # --- v4.0 release review, lane F6 ----------------------------------------------------------
    ("C1: an unquoted brace word is recorded unresolved",
     "            _BRACE_WORDS.add(text)",
     "            pass",
     "test_unquoted_brace_rows_are_unresolved_in_every_verb_shape"),
    ("I1: a startup-steering variable makes the carrier a blocker",
     "    if _SHELL_STARTUP_ENV[0]:" + NL + "        return False",
     "    if False:" + NL + "        return False",
     "test_startup_steering_makes_the_carrier_a_blocker"),
    ("I1: only a bare -c is transparent",
     '        return v != "zsh" and rest[:1] == ["-c"] and len(rest) > 1',
     "        return any(_DASH_C.match(t) for t in rest)",
     "test_startup_steering_makes_the_carrier_a_blocker"),
    ("M1: a directory destination that receives a .jsonl",
     "        transcript_copy = transcript_copy or _copy_lands_a_transcript(s, dests_, assigns)",
     "        transcript_copy = transcript_copy",
     "test_M1_a_directory_destination_that_receives_a_jsonl_fires"),
    ("P1: a process substitution is a span of its word",
     'last in ("$", "<", ">")',
     'last in ("$",)',
     "test_the_verb_and_every_hard_block_survive_each_prefix"),
    ("P1: env word-splitting makes the command name unknowable",
     "        if _env_split_index(seg) is not None:" + NL + "            return True",
     "        if False:" + NL + "            return True",
     "test_env_word_splitting_makes_the_command_name_unknowable"),
    ("C6: a redirect target is judged at every value",
     "else resolve_variants(p, plain_assigns)",
     "else [resolve(p, plain_assigns)]",
     "test_C6_redirect_and_output_flag_targets_are_judged_at_every_value"),
    ("C7: a lane overflow is reported, never a picked directory",
     "            _CD_LANES_TRUNCATED[0] = True",
     "            pass",
     "test_C7_past_the_lane_bound_the_directory_is_unknown_and_refused_from_every_root"),
    # --- programs that run programs (watch, ssh, su, env -S, wsl, script, tmux, parallel, busybox,
    # brace and glob names, builtin assignments, zsh flags). One mutant per clause.
    ('carrier: an argv carrier contributes its words as a reading',
     '    if v in _ARGV_CARRIERS:', '    if False:',
     'TestProgramsThatRunPrograms.test_busybox_and_sibling_runners'),
    ('carrier: a quoted word is opened, since these run through a shell',
     '    return [rest, " ".join(t for t, _q in toks)]', '    return [rest]',
     'TestProgramsThatRunPrograms.test_watch_runs_its_words_and_its_text'),
    ('carrier: a value option ending a short-flag cluster takes the next word',
     '            if t in opts or (t[1] != "-" and len(t) > 2 and "-" + t[-1] in opts' + NL
     + '                             and not any("-" + c in opts for c in t[1:-1])):',
     '            if t in opts:',
     'TestProgramsThatRunPrograms.test_ssh_to_this_machine_is_a_local_command'),
    ('carrier: -c text carriers contribute their text',
     '    if v in _TEXT_C_CARRIERS:', '    if False:',
     'TestProgramsThatRunPrograms.test_su_and_script_run_their_c_text'),
    ('carrier: an unresolvable -c text keeps the words after it',
     '        return [text + " " + " ".join(following)] if has_unresolved(text) and following else [text]',
     '        return [text]',
     'TestProgramsThatRunPrograms.test_an_unreadable_program_in_a_carrier_is_an_unknown_program'),
    ('carrier: ssh to this machine is read',
     '    if v == "ssh":', '    if False:',
     'TestProgramsThatRunPrograms.test_ssh_to_this_machine_is_a_local_command'),
    ('carrier: ssh to another machine is NOT read',
     '    if not _is_this_machine(_head(words[at])):', '    if False:',
     'TestProgramsThatRunPrograms.test_TWIN_ssh_to_another_machine_is_not_this_machines_folder'),
    ('carrier: an ssh reading does not start in this directory',
     '    remote = ["cd " + _UNKNOWN_CWD + " && " + r for r in _as_program(tail[k:], True)]',
     '    remote = _as_program(tail[k:], True)',
     'TestProgramsThatRunPrograms.test_TWIN_ssh_relative_paths_are_not_read_in_this_directory'),
    ('carrier: parallel is read',
     '    elif v == "parallel":', '    elif False:',
     'TestProgramsThatRunPrograms.test_parallel_runs_its_template_over_its_arguments'),
    ('carrier: parallel with no ::: reads its arguments from stdin',
     '        return ["xargs " + r for r in _as_program(tail, True)]', '        return []',
     'TestProgramsThatRunPrograms.test_parallel_runs_its_template_over_its_arguments'),
    ('carrier: tmux is read',
     '    elif v == "tmux":', '    elif False:',
     'TestProgramsThatRunPrograms.test_tmux_commands_that_run_a_shell'),
    ('carrier: a tmux value option is not a shell text',
     '        if not quoted and t in _TMUX_VALUE_OPTS:', '        if False:',
     'TestProgramsThatRunPrograms.test_tmux_commands_that_run_a_shell'),
    ('carrier: mapfile -C is read',
     '    elif v in ("mapfile", "readarray"):', '    elif False:',
     'TestProgramsThatRunPrograms.test_mapfile_evals_its_callback'),
    ('carrier: the mapfile callback receives the here-string line',
     '    return [text + " 0 " + _sh_quote(hs) if hs else text]', '    return [text]',
     'TestProgramsThatRunPrograms.test_mapfile_evals_its_callback'),
    ('carrier: env -S is read',
     '        out.extend(_env_split_readings(seg))', '        pass',
     'TestProgramsThatRunPrograms.test_env_S_runs_its_split_text'),
    # NOT MUTATED since 4.1.1: `if clustered and j < len(words):` in _env_split_readings
    # ('carrier: a value option at the end of an env cluster') is EQUIVALENT today. 08873dcf
    # taught _verb_word (learned=True, which verb() uses) that a short-flag cluster whose LAST
    # letter takes a value consumes the next word, for every wrapper in _WRAPPER_VALUE_OPTS, so
    # verb() reads `env -iu X rm ...` as `rm` itself and the env reading repeats the segment's
    # own command. Both walks split words the same way (_raw_words + tokens' head), env's value
    # letters are the same (u, C, with S returning earlier), and the learned=False reading runs
    # only for exec/sudo. MEASURED 2026-11-08 (CI run 42517544460 reported it uncaught): the
    # mutant survived the whole test_hook_facts.py (1189 tests, OK), and over 195 variants (15
    # env prefixes -- behind sudo/nice/command, `\env`, `$E`, `--`, an assignment, an attached
    # or unresolved value -- x 13 payloads: delete, sh -c, eval, xargs, find -delete, tee, cp,
    # sed -i, git clean, python, relative delete after a cd) the set of true facts differed in
    # 0. Kept in the guard as a second reading; not mutated, because a mutant that can never
    # be caught sits here permanently red.
    ('carrier: a word a new wrapper value option swallows stays judged',
     '        out.extend(_value_word_readings(seg))', '        pass',
     'TestProgramsThatRunPrograms.test_a_word_a_new_value_option_would_swallow_is_still_judged'),
    ('carrier: exec -a takes a value',
     '    "exec": {"-a"},' + NL, '',
     'TestProgramsThatRunPrograms.test_exec_a_and_sudo_value_options_are_not_the_command'),
    ('carrier: sudo -D and friends take a value',
     '             "-D", "--chdir", "-R", "--chroot", "-T", "--command-timeout",' + NL
     + '             "-a", "--auth-type", "-c", "--login-class"},',
     '             },',
     'TestProgramsThatRunPrograms.test_exec_a_and_sudo_value_options_are_not_the_command'),
    ('carrier: a shell steps over options after -c',
     '_shell_text_at(toks, i + 1, t)', 'i + 1',
     'TestProgramsThatRunPrograms.test_a_shell_reads_options_after_c_before_its_text'),
    ('carrier: wsl spells this machine under /mnt',
     '            if v == "wsl":', '            if False:',
     'TestProgramsThatRunPrograms.test_wsl_spells_this_machines_drive_under_mnt'),
    ('carrier: a reading replaces its segment, so a cd before it applies',
     '        variants = _reading_variants(cmd, segs, carrying)',
     '        variants = [r for rs in carrying.values() for r in rs]',
     'TestProgramsThatRunPrograms.test_a_carrier_keeps_the_directory_changed_before_it'),
    ('carrier: a cd only a reading holds does not count as moving',
     'for c_ in all_cmds if c_ not in _READING_TEXTS for s in segments(c_))',
     'for c_ in all_cmds for s in segments(c_))',
     'TestProgramsThatRunPrograms.test_a_cd_inside_a_reading_keeps_the_session_directory_of_other_carriers'),
    ('carrier: a brace-expanded name is read',
     '    if vt and "," in vt and vt in _BRACE_WORDS:', '    if False:',
     'TestProgramsThatRunPrograms.test_a_brace_expanded_command_name'),
    ('carrier: a brace word is not a group',
     ' and not _starts_with_brace_word(s):', ':',
     'TestProgramsThatRunPrograms.test_a_brace_expanded_command_name'),
    ('carrier: a glob name is read as an unknown program',
     '        elif _glob_program(seg, vt):', '        elif False:',
     'TestProgramsThatRunPrograms.test_a_glob_as_the_command_name'),
    ('carrier: a glob name with no readings adds none',
     '            readings = dynamic_program_readings(seg)' + NL + '            if readings:' + NL + '                carrying[n] = readings',
     '            carrying[n] = dynamic_program_readings(seg)',
     'TestProgramsThatRunPrograms.test_TWIN_a_glob_name_alone_beside_another_reading'),
    ('carrier: a glob name reaches the unknown-program backstop',
     NL + '                and not _glob_program(s, _verb_token(s))):', '):',
     'TestProgramsThatRunPrograms.test_a_glob_that_may_be_the_delete_itself_is_an_unknown_program'),
    ('carrier: a quoted glob is a literal name',
     '    bare = "".join(" " if inq else c for _i, c, inq, _k in _qwalk(w))', '    bare = w',
     'TestProgramsThatRunPrograms.test_TWIN_glob_like_names_that_are_not_globs'),
    ('carrier: a @ transform keeps the value',
     '    text = _VALUE_TRANSFORM.sub(lambda m: "${" + m.group(1) + "}", cmd) if "@" in cmd else cmd',
     '    text = cmd',
     'TestProgramsThatRunPrograms.test_a_value_transform_keeps_the_value'),
    ('carrier: prompt expansion runs the substitution in the stored value',
     '    if "@P}" in cmd:', '    if False:',
     'TestProgramsThatRunPrograms.test_prompt_expansion_runs_a_substitution_in_the_stored_value'),
    ('carrier: printf -v assigns',
     '            for name, value in _builtin_assignment(seg).items():',
     '            for name, value in ():',
     'TestProgramsThatRunPrograms.test_printf_v_assigns'),
    ('carrier: read <<< assigns',
     '    if v == "read":' + NL + '        hs = _here_string(seg)',
     '    if False:' + NL + '        hs = _here_string(seg)',
     'TestProgramsThatRunPrograms.test_read_from_a_here_string_assigns'),
    ('carrier: a not-found handler reads every command',
     '    not_found_handler = "command_not_found_handle" in cmd', '    not_found_handler = False',
     'TestProgramsThatRunPrograms.test_a_not_found_handler_may_run_any_command'),
    ('carrier: zsh flags and $- are read as plain expansions',
     '    text = _FLAGGED_EXPANSION.sub(_plain_expansion, text)', '    pass',
     'TestProgramsThatRunPrograms.test_zsh_flags_and_dollar_dash_are_expansions'),
    ('carrier: the resolver itself keeps the literal reading of a flagged expansion',
     '_EXPANSION = re.compile(r"' + chr(92) + '$' + chr(92) + '{|' + chr(92) + '$[A-Za-z_]|' + chr(92) + '$[0-9@*#?!$]")',
     '_EXPANSION = re.compile(r"' + chr(92) + '$' + chr(92) + '{|' + chr(92) + '$[A-Za-z_]|' + chr(92) + '$[0-9@*#?!$-]|' + chr(92) + '$[=~^+]")',
     'TestProgramsThatRunProgramsReview.test_a_flagged_expansion_keeps_its_literal_verdict'),
    ('carrier: the reading from before a value option was learned is kept',
     '    at = _verb_word(seg, learned=False)[1]', '    at = _verb_word(seg)[1]',
     'TestProgramsThatRunProgramsReview.test_a_later_flags_value_is_still_read_as_the_command'),
    ('carrier: a glob must be able to spell a dangerous verb',
     '    return any(fnmatch.fnmatchcase(v, name) for v in _GLOB_SPELLABLE)', '    return True',
     'TestProgramsThatRunProgramsReview.test_a_glob_that_cannot_spell_a_dangerous_verb_is_left_alone'),
    ('carrier: a glob in the directory leaves the literal name',
     '    if not _GLOB_NAME.search(name):' + NL + '        return False' + NL + '    import fnmatch',
     '    import fnmatch',
     'TestProgramsThatRunProgramsReview.test_a_glob_in_the_directory_is_the_literal_name'),
    ('carrier: a brace range is not a brace word',
     ' or "," not in first[0]:', ':',
     'TestProgramsThatRunProgramsReview.test_a_brace_range_as_a_word_is_data'),
    ('carrier: a range inside a brace name does not mark the operand scope',
     '        _OPERAND_EXPANSION_TRUNCATED[0] = was', '        pass',
     'TestProgramsThatRunProgramsReview.test_a_brace_range_as_a_word_is_data'),
    ('carrier: a not-found reading does not read itself',
     '''variants = [t.replace("command_not_found_handle", "x4_read_handler")''',
     '''variants = [t''',
     'TestProgramsThatRunProgramsReview.test_a_not_found_handler_is_read_once'),
    ('carrier: - ends a shell\'s options',
     '        if t in ("--", "-"):', '        if t == "--":',
     'TestProgramsThatRunProgramsReview.test_shell_option_ends_and_value_clusters_after_c'),
    ('carrier: an o in a cluster takes a value',
     '    return 0 if t[:2] == "--" else t.count("o") + t.count("O")', '    return 0',
     'TestProgramsThatRunProgramsReview.test_shell_option_ends_and_value_clusters_after_c'),
    ('carrier: a long shell option can take a value',
     '(1 if t in _SHELL_LONG_VALUE_OPTS else _shell_option_values(t))', '_shell_option_values(t)',
     'TestProgramsThatRunProgramsReview.test_shell_option_ends_and_value_clusters_after_c'),
    ('carrier: the walk to env steps over a wrapper value',
     '            if t in _WRAPPER_VALUE_OPTS.get(wrapper, ()):' + NL + '                i += 1', '            pass',
     'TestProgramsThatRunProgramsReview.test_env_S_behind_a_wrapper_with_a_value'),
    ('carrier: the walk to env steps over a wrapper number',
     ' or (wrapper and _WRAPPER_ARG.match(t))):', '):',
     'TestProgramsThatRunProgramsReview.test_env_S_behind_a_wrapper_with_a_value'),
    # HISTORY (not mutated in 4.1.1): `if tmux_chain:` in _inner_commands ('carrier: a tmux chain
    # continues past an escaped semicolon') no longer decides any CORRECT verdict. It joined a
    # tmux segment that ended in `\` to the next segment, for the days when segments() split at
    # `\;`. Round 4/7 (_scan_ops) made an escaped `;` literal, so `tmux ls \; run '<cmd>'` is ONE
    # segment that _tmux_readings reads by itself (its own mutant: 'tmux: a separator glued to a
    # word (r4, 7)', same target test). A segment can now end in `\` only before an UNESCAPED
    # separator (`tmux ls \\; run x`, `tmux ls \ ; run x`), where bash really ends the tmux
    # command and runs the next segment as an ordinary command -- so the chain reading there is
    # a misreading, and pinning it in a test would pin a false positive. MEASURED 2026-11-08 (CI
    # run 42517544460 reported it uncaught): the mutant survived the whole test_hook_facts.py
    # (1189 tests, OK); over 9 spellings it changed only `tmux ls \\; run '<del ref>'` (True ->
    # False), the misreading. REMOVED in 4.1.2, with its mutant 'carrier: only tmux starts a chain':
    # test_TWIN_a_backslash_ended_segment_of_another_program now pins the tmux rows False too.
    ('carrier: a read option takes its value',
     '            elif t[-1] in "pdnNtui":', '            elif False:',
     'TestProgramsThatRunProgramsReview.test_read_options_before_the_name'),
    ('carrier: su --session-command',
     '''(t in ("--command", "--session-command") or''', '''(t in ("--command",) or''',
     'TestProgramsThatRunProgramsReview.test_su_session_command'),
    ('carrier: numeric host spellings',
     '        return n == 0 or n >> 24 == 127', '        return False',
     'TestProgramsThatRunProgramsReview.test_every_loopback_spelling_is_this_machine'),
    ('carrier: an address outside loopback is another machine',
     '        return n == 0 or n >> 24 == 127', '        return True',
     'TestProgramsThatRunProgramsReview.test_TWIN_addresses_that_are_not_this_machine'),
    # No row for `a.ipv4_mapped` in _is_loopback: from Python 3.12.4 on, is_loopback and is_unspecified
    # read a mapped address themselves, so the mutant survives there. The clause is for 3.10 and 3.11.
    ('carrier: ssh options that run locally',
     '        m = _SSH_LOCAL_COMMAND.fullmatch(value)', '        m = None',
     'TestProgramsThatRunProgramsReview.test_ssh_options_that_run_a_local_command'),
    # --- the carrier code those rows missed (4.1.0 review D-C1: none of this had ever run). Each row
    # is one clause. A clause left out is a speed guard the next clause already covers, recorded in
    # the 4.1.0 lane's report: mutated, it changes no verdict.
    ('carrier: a brace word is one this command expanded',
     '    return bool(toks) and toks[0][0] in _BRACE_WORDS', '    return bool(toks)',
     'TestProgramsThatRunProgramsReview.test_a_brace_range_as_a_word_is_data'),
    ('carrier: only printf and read assign',
     '    if v not in ("printf", "read"):' + NL + '        return {}', '    if not v:' + NL + '        return {}',
     'TestProgramsThatRunPrograms.test_TWIN_builtin_assignments_of_ordinary_values'),
    ('carrier: read -aNAME names the variable',
     '            elif t[1] == "a" and len(t) > 2:', '            elif False:',
     'TestProgramsThatRunProgramsReview.test_read_options_before_the_name'),
    ('carrier: printf -v NAME',
     '    if args[:1] == ["-v"]:', '    if False:',
     'TestProgramsThatRunPrograms.test_printf_v_assigns'),
    ('carrier: printf -vNAME',
     '    elif args[:1] and args[0].startswith("-v"):', '    elif False:',
     'TestProgramsThatRunPrograms.test_printf_v_assigns'),
    ('carrier: printf -v steps over --',
     '    if args[:1] == ["--"]:' + NL + '        args = args[1:]' + NL + '    if not args or not re.fullmatch',
     '    if not args or not re.fullmatch',
     'TestProgramsThatRunPrograms.test_printf_v_assigns'),
    ('carrier: a printf -v redirect is not an argument',
     '    args = _drop_redirects([t for t, _q in tokens(seg)[at + 1:]])' + NL + '    if args[:1] == ["-v"]:',
     '    args = [t for t, _q in tokens(seg)[at + 1:]]' + NL + '    if args[:1] == ["-v"]:',
     'TestProgramsThatRunPrograms.test_printf_v_assigns'),
    ('carrier: a printf -v name must be a name',
     '    if not args or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):', '    if not args:',
     'TestProgramsThatRunPrograms.test_TWIN_builtin_assignments_of_ordinary_values'),
    ('carrier: a later value option keeps its word as the command before it is learned',
     '                want_value = learned or t not in _LATER_VALUE_OPTS.get(wrapper, ())',
     '                want_value = True',
     'TestProgramsThatRunProgramsReview.test_a_later_flags_value_is_still_read_as_the_command'),
    ('carrier: exec -a is a later value option',
     '    "exec": frozenset({"-a"}),' + NL, '',
     'TestProgramsThatRunPrograms.test_a_word_a_new_value_option_would_swallow_is_still_judged'),
    ('carrier: a glob reaches the dynamic-program test',
     '    if not t or not (has_unresolved(t) or _glob_program(seg, t)):', '    if not t or not has_unresolved(t):',
     'TestProgramsThatRunPrograms.test_a_glob_that_may_be_the_delete_itself_is_an_unknown_program'),
    ('carrier: a glob is dynamic past the plain-word test',
     '    if w and not has_unresolved(blank_single_quoted(w)) and not _glob_program(seg, t):',
     '    if w and not has_unresolved(blank_single_quoted(w)):',
     'TestProgramsThatRunPrograms.test_a_glob_that_may_be_the_delete_itself_is_an_unknown_program'),
    ('carrier: a shell option walk stops at the first non-option',
     '        if not _SHELL_OPT_WORD.fullmatch(t):' + NL + '            break', '        if False:' + NL + '            break',
     'TestProgramsThatRunPrograms.test_a_shell_reads_options_after_c_before_its_text'),
    ('carrier: a shell with nothing after its options has no text',
     '    return k if k < len(toks) else j', '    return k',
     'TestProgramsThatRunProgramsReview.test_TWIN_shell_with_nothing_after_its_option'),
    ('carrier: a positional word before the program is stepped over',
     '        if positional > 0:', '        if False:',
     'TestProgramsThatRunPrograms.test_busybox_and_sibling_runners'),
    ('carrier: a numeric word before the program is stepped over',
     '        if numeric and _NUMERIC_WORD.fullmatch(t):', '        if False:',
     'TestProgramsThatRunPrograms.test_busybox_and_sibling_runners'),
    ('carrier: watch has value options',
     '    "watch": ({"-n", "--interval", "-q", "--equexit"}, 0, False),', '    "watch": (frozenset(), 0, False),',
     'TestProgramsThatRunPrograms.test_watch_runs_its_words_and_its_text'),
    ('carrier: wsl has value options',
     '    "wsl": ({"-d", "--distribution", "--distribution-id", "-u", "--user", "--cd", "--shell-type"}, 0, False),',
     '    "wsl": (frozenset(), 0, False),',
     'TestProgramsThatRunPrograms.test_wsl_runs_its_words'),
    ('carrier: chroot takes its directory first',
     '    "chroot": ({"--userspec", "--groups"}, 1, False),', '    "chroot": ({"--userspec", "--groups"}, 0, False),',
     'TestProgramsThatRunPrograms.test_busybox_and_sibling_runners'),
    ('carrier: screen has value options',
     '    "screen": ({"-S", "-c", "-T", "-h", "-p", "-t", "-e", "-s"}, 0, False),', '    "screen": (frozenset(), 0, False),',
     'TestProgramsThatRunPrograms.test_busybox_and_sibling_runners'),
    ('carrier: screen stuff and exec are not the program',
     '            if v == "screen" and _head(tail[0]) in ("stuff", "exec"):', '            if False:',
     'TestProgramsThatRunPrograms.test_busybox_and_sibling_runners'),
    ('carrier: --command= is text',
     '        if t.startswith(("--command=", "--session-command=")):', '        if False:',
     'TestProgramsThatRunPrograms.test_su_and_script_run_their_c_text'),
    ('carrier: a -c cluster is text',
     'or (not quoted and _DASH_C.match(t))) and following:', 'or False) and following:',
     'TestProgramsThatRunPrograms.test_su_and_script_run_their_c_text'),
    ('carrier: su reads options after -c as a shell does',
     '            k = _shell_text_at([(_head(x), False) for x in following], 0)', '            k = 0',
     'TestProgramsThatRunProgramsReview.test_su_session_command'),
    ('carrier: a text glued to -c is text',
     '        elif t.startswith("-c") and len(t) > 2 and _EVAL_SPLITS.search(t):', '        elif False:',
     'TestProgramsThatRunProgramsReview.test_su_session_command'),
    ('carrier: the walk to env sees through a wrapper',
     '        if name in WRAPPERS:' + NL + '            wrapper = name', '        if False:' + NL + '            wrapper = name',
     'TestProgramsThatRunProgramsReview.test_env_S_behind_a_wrapper_with_a_value'),
    ('carrier: env behind any other program is not env',
     '        elif not (_ASSIGNMENT.match(t) or (wrapper and _WRAPPER_ARG.match(t))):' + NL + '            return []',
     '        elif not (_ASSIGNMENT.match(t) or (wrapper and _WRAPPER_ARG.match(t))):' + NL + '            pass',
     'TestProgramsThatRunProgramsReview.test_TWIN_a_program_named_before_a_word_env_is_not_env'),
    ('carrier: an assignment in env is stepped over',
     '        if _ASSIGNMENT.match(t):' + NL + '            j += 1' + NL + '            continue' + NL + '        if not t.startswith("-") or t == "-":',
     '        if not t.startswith("-") or t == "-":',
     'TestProgramsThatRunPrograms.test_env_S_runs_its_split_text'),
    ('carrier: env -i env -S',
     '            if _verb_name(t) == "env":' + NL + '                j += 1', '            if False:' + NL + '                j += 1',
     'TestProgramsThatRunPrograms.test_env_S_runs_its_split_text'),
    ('carrier: env --split-string',
     '            if t.startswith("--s"):', '            if False:',
     'TestProgramsThatRunPrograms.test_env_S_runs_its_split_text'),
    ('carrier: env --unset takes a value',
     '            j += 2 if t in ("--unset", "--chdir") else 1', '            j += 1',
     'TestProgramsThatRunPrograms.test_env_S_runs_its_split_text'),
    ('carrier: env -Stext',
     '                if body[k + 1:]:' + NL + '                    return [" ".join([body[k + 1:]] + words[j + 1:])]',
     '                if False:' + NL + '                    return [" ".join([body[k + 1:]] + words[j + 1:])]',
     'TestProgramsThatRunPrograms.test_env_S_runs_its_split_text'),
    ('carrier: env -u takes a value',
     '            if c in "uC":', '            if False:',
     'TestProgramsThatRunPrograms.test_env_S_runs_its_split_text'),
    ('carrier: an octal host spelling',
     'int(p, 8) if p[:1] == "0" and len(p) > 1 else int(p, 10)', 'int(p, 10)',
     'TestProgramsThatRunProgramsReview.test_every_loopback_spelling_is_this_machine'),
    ('carrier: a hex host spelling',
     'int(p, 16) if p[:2] in ("0x", "0X") else int(p, 8)', 'int(p, 8)',
     'TestProgramsThatRunProgramsReview.test_every_loopback_spelling_is_this_machine'),
    ('carrier: a short address fills its last part',
     '    return n * 256 ** (5 - len(vals)) + vals[-1]', '    return n * 256 + vals[-1]',
     'TestProgramsThatRunProgramsReview.test_every_loopback_spelling_is_this_machine'),
    ('carrier: loopback names',
     '    if h in _LOOPBACK_NAMES:', '    if False:',
     'TestProgramsThatRunPrograms.test_ssh_to_this_machine_is_a_local_command'),
    ('carrier: IPv6 loopback and unspecified',
     '        return a.is_loopback or a.is_unspecified or bool(', '        return bool(',
     'TestProgramsThatRunProgramsReview.test_every_loopback_spelling_is_this_machine'),
    ('carrier: a host in a variable may be this machine',
     '    if has_unresolved(host):' + NL + '        return True', '    if False:' + NL + '        return True',
     'TestProgramsThatRunPrograms.test_ssh_to_this_machine_is_a_local_command'),
    ('carrier: ssh:// is a host spelling',
     '    if h.startswith("ssh://"):', '    if False:',
     'TestProgramsThatRunProgramsReview.test_every_loopback_spelling_is_this_machine'),
    ('carrier: the user and path around a host',
     '    h = h.rpartition("@")[2].rstrip(".").partition("/")[0]', '    h = h.rstrip(".").partition("/")[0]',
     'TestProgramsThatRunPrograms.test_ssh_to_this_machine_is_a_local_command'),
    ('carrier: a bracketed IPv6 host',
     '    if h.startswith("["):', '    if False:',
     'TestProgramsThatRunProgramsReview.test_every_loopback_spelling_is_this_machine'),
    ('carrier: a loopback host with a port',
     ' or (h.count(":") == 1 and _is_loopback(h.partition(":")[0])):', ':',
     'TestProgramsThatRunProgramsReview.test_every_loopback_spelling_is_this_machine'),
    ('carrier: this computer by name',
     '    return h in {n.lower().partition(".")[0] for n in own if n} or h.partition(".")[0] in {' + NL + '        n.lower() for n in own if n}',
     '    return False',
     'TestProgramsThatRunProgramsReview.test_this_computers_own_name_is_this_machine'),
    ('carrier: ssh has value options',
     '_SSH_VALUE_OPTS = frozenset(("-" + c) for c in "bcDEeFIiJLlmOopQRSWwBP")', '_SSH_VALUE_OPTS = frozenset()',
     'TestProgramsThatRunPrograms.test_ssh_to_this_machine_is_a_local_command'),
    ('carrier: ssh -oOPTION',
     '        value = t[2:] if t.startswith("-o") and len(t) > 2 else (', '        value = (',
     'TestProgramsThatRunProgramsReview.test_ssh_options_that_run_a_local_command'),
    ('carrier: parallel with stdin reads what the producer prints',
     '        return list(_echo_printf_program(prev)) if prev and _verb_name(verb(prev)) in ("echo", "printf") else []',
     '        return []',
     'TestProgramsThatRunPrograms.test_parallel_runs_its_template_over_its_arguments'),
    ('carrier: a parallel :::: names files, whose names are not arguments',
     '            in_file = t.startswith("::::")', '            in_file = False',
     'TestProgramsThatRunPrograms.test_TWIN_parallel_ordinary_forms'),
    ('carrier: parallel with no template runs each argument',
     '    if not template:' + NL + '        return [_head(a) for a in args]', '    if False:' + NL + '        return [_head(a) for a in args]',
     'TestProgramsThatRunPrograms.test_parallel_runs_its_template_over_its_arguments'),
    ('carrier: a parallel slot takes the argument',
     '    filled = (_PARALLEL_SLOT.sub(lambda _m: joined_args, opened) if _PARALLEL_SLOT.search(opened)' + NL + '              else opened + " " + joined_args)',
     '    filled = opened + " " + joined_args',
     'TestProgramsThatRunPrograms.test_parallel_runs_its_template_over_its_arguments'),
    ('carrier: parallel has value options',
     '    "-j", "--jobs", "-P", "--max-procs",', '    "--jobs", "-P", "--max-procs",',
     'TestProgramsThatRunPrograms.test_parallel_runs_its_template_over_its_arguments'),
    ('carrier: tmux -c',
     '        if t == "-c" and i + 1 < len(words):' + NL + '            out.append(_head(words[i + 1]))',
     '        if False:' + NL + '            out.append(_head(words[i + 1]))',
     'TestProgramsThatRunPrograms.test_tmux_commands_that_run_a_shell'),
    ('carrier: tmux global options take a value',
     '        elif t in _TMUX_GLOBAL_VALUE_OPTS:', '        elif False:',
     'TestProgramsThatRunPrograms.test_tmux_commands_that_run_a_shell'),
    ('carrier: a tmux global flag is stepped over',
     '        elif t.startswith("-"):' + NL + '            i += 1' + NL + '        else:' + NL + '            break' + NL + '    chunk = []',
     '        else:' + NL + '            break' + NL + '    chunk = []',
     'TestProgramsThatRunPrograms.test_tmux_commands_that_run_a_shell'),
    ('carrier: tmux chains with a quoted semicolon',
     '        if h != ";":', '        if True:',
     'TestProgramsThatRunPrograms.test_tmux_commands_that_run_a_shell'),
    ('carrier: only a tmux command that runs a shell is read',
     '            if sub in _TMUX_ALIASES or (len(sub) >= 3 and any(f.startswith(sub) for f in _TMUX_RUNS)):', '            if True:',
     'TestProgramsThatRunPrograms.test_TWIN_tmux_ordinary_forms'),
    ('carrier: a tmux alias',
     '            if sub in _TMUX_ALIASES or (len(sub) >= 3', '            if (len(sub) >= 3',
     'TestProgramsThatRunPrograms.test_tmux_commands_that_run_a_shell'),
    ('carrier: a tmux command by its prefix',
     'or (len(sub) >= 3 and any(f.startswith(sub) for f in _TMUX_RUNS)):', ':',
     'TestProgramsThatRunPrograms.test_tmux_commands_that_run_a_shell'),
    ('carrier: a number before a tmux shell text is stepped over',
     '        if not quoted and re.fullmatch(r"[0-9]+%?", t):', '        if False:',
     'TestProgramsThatRunPrograms.test_tmux_commands_that_run_a_shell'),
    ('carrier: a quoted tmux word with a command in it is a shell text',
     '        if quoted and _EVAL_SPLITS.search(t):' + NL + '            out.append(t)', '        if False:' + NL + '            out.append(t)',
     'TestProgramsThatRunPrograms.test_tmux_commands_that_run_a_shell'),
    ('carrier: an unquoted tmux tail is argv',
     '        elif argv_at is None and not quoted:', '        elif False:',
     'TestProgramsThatRunPrograms.test_tmux_commands_that_run_a_shell'),
    ('carrier: mapfile -Ctext',
     '        if t.startswith("-C") and len(t) > 2:', '        if False:',
     'TestProgramsThatRunPrograms.test_mapfile_evals_its_callback'),
    ('carrier: a flagged ${ stays a brace',
     'else "${" if s.startswith("${") else "$"', 'else "$"',
     'TestProgramsThatRunPrograms.test_a_flagged_brace_is_the_whole_value'),
    ('carrier: $- is an expansion in a reading',
     '|' + chr(92) + '$[=~^+](?=[A-Za-z_{])|' + chr(92) + '$-")', '|' + chr(92) + '$[=~^+](?=[A-Za-z_{])")',
     'TestProgramsThatRunPrograms.test_zsh_flags_and_dollar_dash_are_expansions'),
    ('carrier: a builtin assignment reaches ${P}',
     'text = re.sub(r"' + chr(92) + '$' + chr(92) + '{" + name + r"' + chr(92) + '}|' + chr(92) + '$" + name + r"(?![A-Za-z0-9_])",',
     'text = re.sub(r"' + chr(92) + '$" + name + r"(?![A-Za-z0-9_])",',
     'TestProgramsThatRunPrograms.test_printf_v_assigns'),
    ('carrier: env behind a wrapper is a candidate',
     '    if v == "env" or _env_candidate(seg):', '    if v == "env":',
     'TestProgramsThatRunProgramsReview.test_env_S_behind_a_wrapper_with_a_value'),
    ('carrier: exec reaches the value-word reading',
     '    if "exec" in seg or "sudo" in seg:', '    if "sudo" in seg:',
     'TestProgramsThatRunPrograms.test_a_word_a_new_value_option_would_swallow_is_still_judged'),
    ('carrier: sudo reaches the value-word reading',
     '    if "exec" in seg or "sudo" in seg:', '    if "exec" in seg:',
     'TestProgramsThatRunPrograms.test_a_word_a_new_value_option_would_swallow_is_still_judged'),
    ('carrier: a not-found handler also reads from the second word',
     '            readings = [r for k in (0, 1) if whole[k:] for r in _as_program(whole[k:], True)]',
     '            readings = [r for k in (0,) if whole[k:] for r in _as_program(whole[k:], True)]',
     'TestProgramsThatRunPrograms.test_a_not_found_handler_may_run_any_command'),
    ('carrier: a not-found handler reads from the first word',
     '            readings = [r for k in (0, 1) if whole[k:] for r in _as_program(whole[k:], True)]',
     '            readings = [r for k in (1,) if whole[k:] for r in _as_program(whole[k:], True)]',
     'TestProgramsThatRunPrograms.test_a_not_found_handler_may_run_any_command'),
    ('carrier: a carrier word in a glob-named segment keeps both readings',
     '            carrying[n] = carrying.get(n, []) + carried_here',
     '            carrying[n] = carried_here',
     'TestProgramsThatRunProgramsReview.test_a_glob_name_and_a_carrier_reading_in_one_segment'),
    ('carrier: the reading set is per call',
     '    _READING_TEXTS.clear()', '    pass',
     'TestProgramsThatRunProgramsReview.test_TWIN_a_reading_of_one_call_is_not_a_reading_of_the_next'),
    ('carrier: text inside a reading is a reading',
     '                        _READING_TEXTS.add(inner)', '                        pass',
     'TestProgramsThatRunProgramsReview.test_a_cd_nested_inside_a_reading_keeps_the_session_directory'),
    # --- 4.1.0 G-M3: the deployed-mod ask sees $X4_EXTENSIONS and a separately configured root. ---
    ('G-M3: the variable reaches the deployed-mod ask',
     '    text = _extensions_spelled(text)', '    pass',
     'TestPreviouslyUnprobedRules.test_the_extensions_variable_names_a_deployed_mod'),
    ('G-M3: the variable means the configured root',
     '    _EXTENSIONS_ROOT[0] = (roots.get("extensions")' + NL + '                           or (roots["game"]',
     '    _EXTENSIONS_ROOT[0] = (""' + NL + '                           or (roots["game"]',
     'TestPreviouslyUnprobedRules.test_all_of_a_separately_configured_root_names_every_mod'),
    ('G-M3: with none configured it means the game layout',
     '(roots["game"].rstrip("/" + chr(92)) + "/extensions" if roots.get("game") else "")', '""',
     'TestPreviouslyUnprobedRules.test_all_of_the_game_layout_stays_the_hard_block_through_the_variable'),
    ('G-M3: unquoted, the root is quoted',
     '    return _EXTENSIONS_VAR.sub(lambda m: root if quote.get(m.start()) else _sh_quote(root), text)',
     '    return _EXTENSIONS_VAR.sub(lambda m: root, text)',
     'TestPreviouslyUnprobedRules.test_the_extensions_variable_names_a_deployed_mod'),
    ('G-M3: inside quotes, the root is not quoted again',
     '    return _EXTENSIONS_VAR.sub(lambda m: root if quote.get(m.start()) else _sh_quote(root), text)',
     '    return _EXTENSIONS_VAR.sub(lambda m: _sh_quote(root), text)',
     'TestPreviouslyUnprobedRules.test_the_extensions_variable_names_a_deployed_mod'),
    ('G-M3: a root this cannot quote gives no reading',
     ' or any(c in root for c in "' + chr(92) + '"' + "'" + '$`") or _EXTENSIONS_SET', ' or _EXTENSIONS_SET',
     'TestPreviouslyUnprobedRules.test_TWIN_a_root_that_cannot_be_written_into_a_reading_is_skipped'),
    ('G-M3: a command that assigns the variable gives no reading',
     ' or _EXTENSIONS_SET.search(text):', ':',
     'TestPreviouslyUnprobedRules.test_TWIN_a_command_that_assigns_the_variable_deletes_under_its_own_value'),
    ('G-M3: the longer name is another variable',
     '|' + chr(92) + '$X4_(?:GAME_)?EXTENSIONS(?![A-Za-z0-9_])")', '|' + chr(92) + '$X4_(?:GAME_)?EXTENSIONS")',
     'TestPreviouslyUnprobedRules.test_TWIN_a_longer_variable_name_is_another_variable'),
    ('G-M3: a configured root is a deployed-mod root',
     '            for root in (_ext, _other_ext):', '            for root in (_ext,):',
     'TestPreviouslyUnprobedRules.test_a_separately_configured_extensions_root_is_a_deployed_mod_root'),
    ('G-M3: the game layout stays a deployed-mod root beside it',
     '            for root in (_ext, _other_ext):', '            for root in (_other_ext,):',
     'TestPreviouslyUnprobedRules.test_the_game_layout_still_counts_beside_a_configured_root'),
    ('G-M3: all of a configured root is asked',
     '                elif root == _other_ext and n == root[:-1]:', '                elif False:',
     'TestPreviouslyUnprobedRules.test_all_of_a_separately_configured_root_names_every_mod'),
    ('G-M3: a root of / names nothing',
     '    if _other_ext == "/" or _other_ext == _ext:', '    if _other_ext == _ext:',
     'TestPreviouslyUnprobedRules.test_TWIN_an_extensions_root_that_is_the_filesystem_root_names_nothing'),
    ('G-M3: a configured root that is the game layout stays the hard block alone',
     '    if _other_ext == "/" or _other_ext == _ext:', '    if _other_ext == "/":',
     'TestPreviouslyUnprobedRules.test_TWIN_a_configured_root_that_is_the_game_layout_stays_the_hard_block'),
    # --- 4.1.0 R2-C1: the folder a cd through a root variable lands in, with an assignment around it. ---
    ('R2-C1: a cd through a root variable lands in that root, assigned or not',
     '        cands = [subst_root_var(r, roots) for r in resolve_variants(ops[0], assigns)]',
     '        cands = list(resolve_variants(ops[0], assigns))',
     'TestHK1PowerShellFrontEnd.test_assigning_a_root_variable_does_not_move_a_cd_through_it'),
    ('R2-C1: a cd to an unknown name keeps the root it left',
     '                    cwd = physical_join(cwd, tgt) if physical else join_cwd(cwd, tgt)',
     '                    cwd = "" if has_unresolved(tgt) else physical_join(cwd, tgt) if physical else join_cwd(cwd, tgt)',
     'TestHK1PowerShellFrontEnd.test_an_unrelated_assigned_variable_does_not_leave_the_root'),
    # --- 4.1.0 F9: the save locks are the user's decision. One mutant per clause (it had no row). ---
    ('save lock: the rule is wired',
     '"save_lock_decision": any(_save_lock_decision(sg) for sg, _c in seg_cwd),',
     '"save_lock_decision": False,',
     'TestPreviouslyUnprobedRules.test_save_lock_changes_are_the_users_decision'),
    ('save lock: an interpreter runs the harness',
     'if v == "agent-harness.py" or v.startswith(("python", "py", "uv", "uvx",',
     'if v == "agent-harness.py" or v.startswith(("uv", "uvx",',
     'TestPreviouslyUnprobedRules.test_save_lock_changes_are_the_users_decision'),
    ('save lock: the harness runs as a program',
     'if v == "agent-harness.py" or v.startswith(("python", "py", "uv", "uvx",',
     'if v.startswith(("python", "py", "uv", "uvx",',
     'TestPreviouslyUnprobedRules.test_the_harness_run_as_a_program_changes_the_save_locks_too'),
    ("save lock: pipenv is an interpreter's wrapper",
     '"pipx", "pipenv",',
     '"pipx",',
     'TestPreviouslyUnprobedRules.test_the_harness_run_through_pipenv_changes_the_save_locks_too'),
    ('save lock: an unknown program may be the interpreter',
     '"poetry", "pdm", "hatch", "rye", "pixi", "xargs")) or has_unresolved(v):',
     '"poetry", "pdm", "hatch", "rye", "pixi", "xargs")):',
     'TestPreviouslyUnprobedRules.test_an_unknown_program_may_be_the_interpreter'),
    ('save lock: only agent-harness.py',
     'if base(t) == "agent-harness.py" and toks[i + 1:i + 2] == ["save-lock"]:',
     'if toks[i + 1:i + 2] == ["save-lock"]:',
     'TestPreviouslyUnprobedRules.test_TWIN_reading_or_naming_the_save_locks_is_not_a_decision'),
    ('save lock: only its save-lock subcommand',
     'if base(t) == "agent-harness.py" and toks[i + 1:i + 2] == ["save-lock"]:',
     'if base(t) == "agent-harness.py":',
     'TestPreviouslyUnprobedRules.test_TWIN_the_harness_as_a_program_reading_the_save_locks_is_not_a_decision'),
    ('save lock: reading is no decision',
     'or has_unresolved(action) or action in ("on", "off", "unlock")',
     'or has_unresolved(action) or True',
     'TestPreviouslyUnprobedRules.test_TWIN_reading_or_naming_the_save_locks_is_not_a_decision'),
    ('save lock: an unreadable action is asked',
     'or has_unresolved(action) or action in ("on", "off", "unlock")',
     'or action in ("on", "off", "unlock")',
     'TestPreviouslyUnprobedRules.test_a_save_lock_action_the_guard_cannot_read_is_asked'),
    ('save lock: xargs feeds an action',
     'return "xargs" in toks[:i] or has_unresolved(action)',
     'return has_unresolved(action)',
     'TestPreviouslyUnprobedRules.test_a_save_lock_action_the_guard_cannot_read_is_asked'),
    ('save lock: setup.sh by itself',
     'if v in ("bash", "sh", "setup.sh") or has_unresolved(v):',
     'if v in ("bash", "sh") or has_unresolved(v):',
     'TestPreviouslyUnprobedRules.test_save_lock_changes_are_the_users_decision'),
    ('save lock: only a shell runs setup.sh',
     'if v in ("bash", "sh", "setup.sh") or has_unresolved(v):',
     'if True:',
     'TestPreviouslyUnprobedRules.test_TWIN_reading_or_naming_the_save_locks_is_not_a_decision'),
    ('save lock: only with --save-lock',
     'return at >= 0 and "--save-lock" in toks[at:]',
     'return at >= 0',
     'TestPreviouslyUnprobedRules.test_TWIN_reading_or_naming_the_save_locks_is_not_a_decision'),
    ("save lock: only setup.sh's --save-lock",
     'return at >= 0 and "--save-lock" in toks[at:]',
     'return "--save-lock" in toks',
     'TestPreviouslyUnprobedRules.test_TWIN_reading_or_naming_the_save_locks_is_not_a_decision'),
    # --- F10 (v4.0): a command name the guard cannot identify (variable, substitution,
    # cmd %R%/!R!). One mutant per clause; each target names its own class.
    ('F10: an unknown command name contributes its readings',
     '            readings = dynamic_program_readings(seg)' + NL + '            if readings:' + NL + '                dynamic[n] = readings',
     '            readings = []' + NL + '            if readings:' + NL + '                dynamic[n] = readings',
     'TestF10DynamicProgramWord.test_the_words_after_an_unknown_name_are_judged_as_a_command'),
    ('F10: a quoted word after an unknown name is read as eval reads it',
     '        for r in [rest, joined] + _requoted(toks, _escapes_a_split(rest)):',
     '        for r in [rest] + _requoted(toks, _escapes_a_split(rest)):',
     'TestF10DynamicProgramWord.test_a_quoted_word_after_an_unknown_name_is_read_as_eval_reads_it'),
    ('F10: the readings step past every further unknown word, not only the first',
     '            out.append("powershell " + rest)' + NL + '        cur = rest',
     '            out.append("powershell " + rest)' + NL + '        break',
     'TestF10DynamicProgramWord.test_the_words_after_an_unknown_name_are_judged_as_a_command'),
    ('F10: an unknown name before a git subcommand may be git',
     '            out.append("git " + rest)',
     '            pass',
     'TestF10DynamicProgramWord.test_an_unknown_name_before_git_add_all'),
    ('F10: a reading goes IN PLACE of its segment, keeping the cd before it',
     '    out.extend(_reading_variants(cmd, segs, dynamic))',
     '    out.extend(r for rs in dynamic.values() for r in rs)',
     'TestF10DynamicProgramWord.test_the_reading_keeps_the_directory_a_cd_set'),
    ('F10: a name this command assigns is spliced in before the carrier arms',
     '            seg = resolve_verb(seg, assigns_)',
     '            pass',
     'TestF10PowerShellUnreadableCommandName.test_each_unreadable_name_shape_reaches_the_plain_commands_rule'),
    ('F10: a cmd program word that is an expansion is read speculatively',
     '    if ws and _CMD_DYN.search(ws[0]) and _depth < _MAX_DYNAMIC_READINGS:',
     '    if False:',
     'TestF10DynamicProgramWord.test_cmd_percent_and_delayed_expansion_program_words'),
    ("F10: cmd's !NAME! delayed expansion is a variable",
     '_CMD_VAR = re.compile(r"%%|%([A-Za-z_][A-Za-z0-9_]*)%|!([A-Za-z_][A-Za-z0-9_]*)!")',
     '_CMD_VAR = re.compile(r"%%|%([A-Za-z_][A-Za-z0-9_]*)%|(?!x)x()")',
     'TestF10DynamicProgramWord.test_cmd_percent_and_delayed_expansion_program_words'),
    ('F10: the literal remainder of a cmd program word is a reading (%R%del)',
     '        for alt in (([lit] + ws[1:]) if lit.strip() else [], ws[1:]):',
     '        for alt in (ws[1:],):',
     'TestF10DynamicProgramWord.test_cmd_percent_and_delayed_expansion_program_words'),
    ('F10: an unknown name given a protected PATH asks',
     '               for k in ("game", "reference", "profile", "saves", "documents")):' + NL + '            program_dynamic = True',
     '               for k in ()):' + NL + '            program_dynamic = True',
     'TestF10DynamicProgramWord.test_an_unknown_name_given_a_protected_path_asks'),
    ('F10: an unknown name given a protected root as TEXT asks',
     '        if any(norm(roots[k]) in norm(o) or k in root_vars_named(o)',
     '        if False and any(norm(roots[k]) in norm(o) or k in root_vars_named(o)',
     'TestF10DynamicProgramWord.test_an_unknown_name_given_a_protected_path_asks'),
    ('F10: a ROOT variable naming a program is a known program',
     '        if (not has_unresolved(subst_root_var(resolve(_verb_token(s), assigns), roots))' + NL,
     '        if (False' + NL,
     'TestF10DynamicProgramWord.test_TWIN_a_known_name_given_a_protected_path_is_not_this_rule'),
    ('F10: a segment cut inside a substitution is no command position',
     '    return not any(_span_open(x) for x in toks[:k])',
     '    return True',
     'TestF10DynamicProgramWord.test_TWIN_a_known_name_given_a_protected_path_is_not_this_rule'),
    # --- F331 follow-up: link CREATION judged by its target. One mutant per clause. ---
    ("F331 link: ln -s makes a SYMBOLIC link (relative target read from the link's folder)",
     '                if ch == "s":' + NL + '                    sym = True',
     '                if False:' + NL + '                    sym = True',
     'TestF331LinkCreationIntoAProtectedRoot.test_a_relative_target_resolves_where_it_runs'),
    ("F331 link: ln -t DIR places the link in DIR",
     '                    if ch == "t":' + NL + '                        tdir = val',
     '                    if False:' + NL + '                        tdir = val',
     'TestF331LinkCreationIntoAProtectedRoot.test_a_relative_target_resolves_where_it_runs'),
    ("F331 link: ln --target-directory places the link in DIR",
     '                if name == "--target-directory":' + NL + '                    tdir = val',
     '                if False:' + NL + '                    tdir = val',
     'TestF331LinkCreationIntoAProtectedRoot.test_a_relative_target_resolves_where_it_runs'),
    ("F331 link: with a target directory every operand is a target",
     '    if tdir is not None:' + NL + '        return ops, tdir, sym',
     '    if False:' + NL + '        return ops, tdir, sym',
     'TestF331LinkCreationIntoAProtectedRoot.test_a_relative_target_resolves_where_it_runs'),
    ("F331 link: ln's LAST operand is the link, not a target",
     '    return ops[:-1], (ops[-1] if ops else None), sym',
     '    return ops, None, sym',
     'TestF331LinkCreationIntoAProtectedRoot.test_a_relative_target_resolves_where_it_runs'),
    ("F331 link: mklink's Git Bash //J switch is a switch",
     '_MKLINK_SWITCH = re.compile(r"^/{1,2}([dhjDHJ])$")',
     '_MKLINK_SWITCH = re.compile(r"^/([dhjDHJ])$")',
     'TestF331LinkCreationIntoAProtectedRoot.test_every_shape_aimed_at_each_protected_root_fires'),
    ("F331 link: mklink /H is a HARDLINK (target from the cwd only)",
     '"d": "symbolic", "h": "hard", "j": "junction"',
     '"d": "symbolic", "h": "symbolic", "j": "junction"',
     'TestF331LinkCreationIntoAProtectedRoot.test_TWIN_a_hardlink_target_is_read_from_the_cwd_only'),
    ("F331 link: mklink's SECOND operand is the target",
     '        return [(ops[1], ops[0], kind)] if len(ops) == 2 else [(None, None, kind)]',
     '        return [(ops[0], ops[1], kind)] if len(ops) == 2 else [(None, None, kind)]',
     'TestF331LinkCreationIntoAProtectedRoot.test_every_shape_aimed_at_each_protected_root_fires'),
    ("F331 link: fsutil hardlink create is a link verb",
     '        if [o.lower() for o in ops[:2]] != ["hardlink", "create"]:' + NL + '            return []',
     '        if True:' + NL + '            return []',
     'TestF331LinkCreationIntoAProtectedRoot.test_every_shape_aimed_at_each_protected_root_fires'),
    ("F331 link: fsutil's EXISTING file is the target",
     '        return [(ops[3], ops[2], "hard")] if len(ops) >= 4',
     '        return [(ops[2], ops[3], "hard")] if len(ops) >= 4',
     'TestF331LinkCreationIntoAProtectedRoot.test_every_shape_aimed_at_each_protected_root_fires'),
    ("F331 link: python os.symlink",
     '    (re.compile(r"\\bos\\.symlink\\(\\s*(?:src\\s*=\\s*)?"), "symbolic"),' + NL,
     '',
     'TestF331LinkCreationIntoAProtectedRoot.test_every_shape_aimed_at_each_protected_root_fires'),
    ("F331 link: python os.link",
     '    (re.compile(r"\\bos\\.link\\(\\s*(?:src\\s*=\\s*)?"), "hard"),' + NL,
     '',
     'TestF331LinkCreationIntoAProtectedRoot.test_every_shape_aimed_at_each_protected_root_fires'),
    ("F331 link: python _winapi.CreateJunction",
     '    (re.compile(r"\\b_winapi\\.CreateJunction\\(\\s*"), "junction"),' + NL,
     '',
     'TestF331LinkCreationIntoAProtectedRoot.test_every_shape_aimed_at_each_protected_root_fires'),
    ("F331 link: python Path.symlink_to",
     '    (re.compile(r"\\.symlink_to\\(\\s*(?:target\\s*=\\s*)?"), "symbolic"),' + NL,
     '',
     'TestF331LinkCreationIntoAProtectedRoot.test_every_shape_aimed_at_each_protected_root_fires'),
    ("F331 link: python Path.hardlink_to",
     '    (re.compile(r"\\.hardlink_to\\(\\s*(?:target\\s*=\\s*)?"), "hard"),' + NL,
     '',
     'TestF331LinkCreationIntoAProtectedRoot.test_every_shape_aimed_at_each_protected_root_fires'),
    ("F331 link: a python call's link literal is read for a non-hard link",
     '            if kind != "hard" and lit:',
     '            if kind == "symbolic" and lit:',
     'TestF331LinkCreationIntoAProtectedRoot.test_a_relative_target_resolves_where_it_runs'),
    ("F331 link: Path(<link>).symlink_to reads its receiver as the link",
     '                    link = r.group("p") if r else None',
     '                    link = None',
     'TestF331LinkCreationIntoAProtectedRoot.test_a_relative_target_resolves_where_it_runs'),
    ("F331 link: os.symlink's second argument is the link",
     '                    link = lit2.group("p") if lit2 else None',
     '                    link = None',
     'TestF331LinkCreationIntoAProtectedRoot.test_a_relative_target_resolves_where_it_runs'),
    ("F331 link: a python -c program's link calls are read",
     '    out = link_creations(seg)' + NL + '    name = _verb_name(verb(seg))' + NL + '    if name in _PYTHONS and name != "uv":',
     '    out = link_creations(seg)' + NL + '    name = _verb_name(verb(seg))' + NL + '    if False:',
     'TestF331LinkCreationIntoAProtectedRoot.test_every_shape_aimed_at_each_protected_root_fires'),
    ("F331 link: a link verb with no readable target is unresolved",
     '    if target is None:' + NL + '        return "unresolved"',
     '    if target is None:' + NL + '        return ""',
     'TestF331LinkCreationIntoAProtectedRoot.test_an_unreadable_target_is_refused'),
    ("F331 link: an unresolved target that names a protected root is protected",
     '            if any(norm(p) in norm(r) for p in prot) or any(k in root_vars_named(r) for k in _LINK_KEYS):',
     '            if False:',
     'TestF331LinkCreationIntoAProtectedRoot.test_a_root_variable_target_is_its_root'),
    ("F331 link: any other unresolved target is unresolved",
     '            verdict = "unresolved"' + NL + '            continue' + NL + '        cands = [join_cwd(cwd, r)]',
     '            pass' + NL + '            continue' + NL + '        cands = [join_cwd(cwd, r)]',
     'TestF331LinkCreationIntoAProtectedRoot.test_an_unreadable_target_is_refused'),
    ("F331 link: a relative non-hard target is read from a target DIRECTORY",
     '                    cands += [join_cwd(lp, r), join_cwd(posixpath.dirname(norm(lp)), r)]',
     '                    cands += [join_cwd(posixpath.dirname(norm(lp)), r)]',
     'TestF331LinkCreationIntoAProtectedRoot.test_a_relative_target_resolves_where_it_runs'),
    ("F331 link: a relative non-hard target is read from the link's folder",
     '                    cands += [join_cwd(lp, r), join_cwd(posixpath.dirname(norm(lp)), r)]',
     '                    cands += [join_cwd(lp, r)]',
     'TestF331LinkCreationIntoAProtectedRoot.test_a_relative_target_resolves_where_it_runs'),
    ("F331 link: a junction's relative target gets the link-folder reading too",
     '        if kind != "hard" and not is_abs(r) and link:',
     '        if kind == "symbolic" and not is_abs(r) and link:',
     'TestF331LinkCreationIntoAProtectedRoot.test_a_relative_target_resolves_where_it_runs'),
    ("F331 link: a hardlink's relative target is read from the cwd only",
     '        if kind != "hard" and not is_abs(r) and link:',
     '        if not is_abs(r) and link:',
     'TestF331LinkCreationIntoAProtectedRoot.test_TWIN_a_hardlink_target_is_read_from_the_cwd_only'),
    ("F331 link: a relative target with no known folder is unresolved",
     '        if not cands:' + NL + '            verdict = "unresolved"',
     '        if False:' + NL + '            verdict = "unresolved"',
     'TestF331LinkCreationIntoAProtectedRoot.test_a_relative_target_with_no_known_folder_is_refused'),
    ("F331 link: a target ABOVE a protected root is protected",
     '        if any(under(c, p) or contains_root(c, p) for c in cands for p in prot):',
     '        if any(under(c, p) for c in cands for p in prot):',
     'TestF331LinkCreationIntoAProtectedRoot.test_every_shape_aimed_at_each_protected_root_fires'),
    ("F331 link: a target INSIDE a protected root is protected",
     '        if any(under(c, p) or contains_root(c, p) for c in cands for p in prot):',
     '        if any(contains_root(c, p) for c in cands for p in prot):',
     'TestF331LinkCreationIntoAProtectedRoot.test_every_shape_aimed_at_each_protected_root_fires'),
    ("F331 link: a heredoc PROGRAM's links are judged",
     '    for tgt_, at_, kind_ in _heredoc_program_links(spliced):',
     '    for tgt_, at_, kind_ in []:',
     'TestF331LinkCreationIntoAProtectedRoot.test_every_shape_aimed_at_each_protected_root_fires'),
    ("F331 link: only a PYTHON heredoc program is read for link calls",
     '        return _reads_program_from_stdin(_unwrap(sg)) and _verb_name(verb(_unwrap(sg))) in _PYTHONS',
     '        return _reads_program_from_stdin(_unwrap(sg))',
     'TestF331LinkCreationIntoAProtectedRoot.test_TWIN_a_link_verb_with_nothing_to_link_or_a_mention_is_not'),
    ("F331 link: a heredoc program's protected verdict reaches the fact",
     '        j_ = _judge_link(tgt_, at_, kind_, base, {}, roots)' + NL + '        link_protected = link_protected or j_ == "protected"',
     '        j_ = _judge_link(tgt_, at_, kind_, base, {}, roots)' + NL + '        link_protected = link_protected or False',
     'TestF331LinkCreationIntoAProtectedRoot.test_every_shape_aimed_at_each_protected_root_fires'),
    ("F331 link: a heredoc program's unresolved verdict reaches the fact",
     '        link_protected = link_protected or j_ == "protected"' + NL + '        link_unresolved = link_unresolved or j_ == "unresolved"' + NL + '    # The save bridge',
     '        link_protected = link_protected or j_ == "protected"' + NL + '        link_unresolved = link_unresolved or False' + NL + '    # The save bridge',
     'TestF331LinkCreationIntoAProtectedRoot.test_an_unreadable_target_is_refused'),
    ("F331 link: a segment's protected verdict reaches the fact",
     '            link_protected = link_protected or j_ == "protected"',
     '            link_protected = link_protected or False',
     'TestF331LinkCreationIntoAProtectedRoot.test_every_shape_aimed_at_each_protected_root_fires'),
    ("F331 link: a segment's unresolved verdict reaches the fact",
     '            link_unresolved = link_unresolved or j_ == "unresolved"',
     '            link_unresolved = link_unresolved or False',
     'TestF331LinkCreationIntoAProtectedRoot.test_an_unreadable_target_is_refused'),
    ("F331 link: the link a mklink/fsutil places is a write where it lands",
     '        return [at for _t, at, _k in link_creations(seg) if at]',
     '        return []',
     'TestF331LinkCreationIntoAProtectedRoot.test_a_link_placed_inside_reference_is_a_write_there'),
    ("F331 link replay: a `for` header holds no link command",
     "        for tgt_, at_, kind_ in ([] if is_for_ else _segment_links(s, assigns)):     # F331",
     "        for tgt_, at_, kind_ in _segment_links(s, assigns):     # F331",
     'TestF331LinkCreationIntoAProtectedRoot.test_TWIN_a_for_loop_variable_named_ln_is_no_ln_command'),
    ("F331 review: a header's flag is by POSITION (a reserved word before `for` is stepped over)",
     '        while words and words[0] in RESERVED and words[0] != "for":',
     '        while False:',
     'TestF331LinkCreationIntoAProtectedRoot.test_a_for_header_is_judged_by_position_not_by_text'),
    ("F331 review: $PWD is the segment's folder",
     '        target = _PWD_REF.sub(lambda _m: cwd, target)',
     '        target = target',
     'TestF331LinkCreationIntoAProtectedRoot.test_PWD_is_the_segments_own_folder'),
    ("F331 review: $PWD only when the folder is known",
     '    if cwd and "PWD" not in assigns:',
     '    if "PWD" not in assigns:',
     'TestF331LinkCreationIntoAProtectedRoot.test_PWD_is_the_segments_own_folder'),
    ("4.0.1: the translator's PowerShell starts without network calls",
     'input=text.encode("utf-8"), capture_output=True, timeout=timeout, env=env,',
     'input=text.encode("utf-8"), capture_output=True, timeout=timeout,',
     'TestPowerShellStartsWithoutNetworkCalls.test_telemetry_and_update_check_are_off_and_the_env_is_kept'),
    # --- 4.1.1: a tee onto the profile mod list is the redirect's exempt write ----
    ("4.1.1: tee copies stdin, so it has no source operand to check",
     "                    if scope == 'copy-destination' and not reads_stdin:",
     "                    if scope == 'copy-destination':",
     "TestProfileModlistWritePolicy.test_tee_onto_the_modlist_is_the_same_write_as_a_redirect"),
    ("4.1.1: a producer pipeline into tee is one mod-list write",
     "             or _modlist_tee_pipeline(all_cmds[0], [s for s, _d in seg_cwd]))",
     "             or False)",
     "TestProfileModlistWritePolicy.test_tee_onto_the_modlist_is_the_same_write_as_a_redirect"),
    ("4.1.1: only producers may feed the mod-list tee",
     '            and all(_bare_command(s, _MODLIST_PIPE_PRODUCERS) for s in segs[:-1]))',
     '            and True)',
     "TestProfileModlistWritePolicy.test_TWIN_tee_onto_anything_else_or_from_a_writer_keeps_its_confirmation"),
    ("4.1.1 review: a wrapped tee (xargs, sudo, env...) is not the bare mod-list tee",
     '    return first in names and _verb_name(verb(seg)) == first',
     '    return _verb_name(verb(seg)) in names',
     "TestProfileModlistWritePolicy.test_TWIN_a_wrapped_tee_is_not_the_exempt_write"),
    ("4.1.1 review: reads_stdin takes the same bare-tee rule",
     'reads_stdin=_bare_command(s, {"tee"}))',
     'reads_stdin=_verb_name(verb(s)) == "tee")',
     "TestProfileModlistWritePolicy.test_TWIN_a_wrapped_tee_is_not_the_exempt_write"),
    ("4.1.1: only a pure pipeline (no ; && ||) is one mod-list write",
     "    if len(segs) < 2 or piped_in(cmd) != [False] + [True] * (len(segs) - 1):",
     "    if len(segs) < 2:",
     "TestProfileModlistWritePolicy.test_TWIN_tee_onto_anything_else_or_from_a_writer_keeps_its_confirmation"),
    # --- 4.1.1 literal-dollar port (6cbeb825, 51129cde, ed667b4a): one per clause
    ('literal $: an ANSI-C word ends at its first NUL',
     "    return ''.join(out).split(chr(0), 1)[0]", "    return ''.join(out)",
     'TestLiteralDollarPaths.test_an_ansi_c_word_ends_at_its_first_nul'),
    ('literal $: \\u above 0x7F is kept as written',
     "            if h and (n == 'x' or int(h, 16) <= 0x7F):", "            if h:",
     'TestLiteralDollarPaths.test_ansi_c_u_and_c_escapes_decode_as_git_bash_does'),
    ('literal $: \\c is decoded',
     "        elif n == 'c' and i + 2 < len(body):", "        elif False:",
     'TestLiteralDollarPaths.test_ansi_c_u_and_c_escapes_decode_as_git_bash_does'),
    ('literal $: only an unquoted $ is the ANSI-C sigil',
     '            dollar = bool(buf) and buf[-1] == "$" and mbuf[-1] == "$"',
     '            dollar = bool(buf) and buf[-1] == "$"',
     'TestLiteralDollarPaths.test_a_quoted_dollar_is_no_ansi_c_sigil'),
    ('literal $: a double-quoted $ stays expansion-active',
     '                xbuf.append(chr(0) if q == "' + chr(39) + '" else c)', '                xbuf.append(chr(0))',
     'TestLiteralDollarPaths.test_TWIN_a_dollar_the_shell_expands_stays_unresolved'),
    ('literal $: an unquoted glob or brace disqualifies the word',
     '                                        and not any(ch in mbuf for ch in "*?[{"))',
     '                                        )',
     'TestLiteralDollarPaths.test_TWIN_an_inner_layer_that_may_expand_keeps_it_unresolved'),
    ('literal $: an unterminated quote proves nothing',
     '        if (q or unterminated) and literal_expansions is not None:', '        if False:',
     'TestLiteralDollarPaths.test_TWIN_a_dollar_the_shell_expands_stays_unresolved'),
    ('literal $: a -c text is exact only if the outer shell expanded nothing',
     '                        if lx.get(arg, False) and arg not in _BRACE_WORDS:', '                        if True:',
     'TestLiteralDollarPaths.test_TWIN_an_inner_layer_that_may_expand_keeps_it_unresolved'),
    ('literal $: zsh -c is never exact (.zshenv)',
     '                    if (exact is not None and seg == written and v in _EXACT_SHELLS',
     '                    if (exact is not None and seg == written',
     'TestLiteralDollarPaths.test_TWIN_an_inner_layer_that_may_expand_keeps_it_unresolved'),
    ('literal $: sudo -s hands on no exact -c text',
     '                            and arg in values and not _reparsing_wrapper(seg)):',
     '                            and arg in values):',
     'TestLiteralDollarPaths.test_TWIN_an_inner_layer_that_may_expand_keeps_it_unresolved'),
    ('literal $: a non-exact route lowers a carried text',
     '                    if ex.get(child, False) and not (handed and ex.get(parent, False)):',
     '                    if False:',
     'TestLiteralDollarPaths.test_TWIN_an_inner_layer_that_may_expand_keeps_it_unresolved'),
    ('literal $: an expanding heredoc is never exact',
     '        ok = (not expands and len(segs_) == 1 and len(_heredoc_opens(opener)) == 1',
     '        ok = (len(segs_) == 1 and len(_heredoc_opens(opener)) == 1',
     'TestLiteralDollarPaths.test_TWIN_an_inner_layer_that_may_expand_keeps_it_unresolved'),
    ('literal $: a heredoc whose stdin is replaced is never exact',
     '              and plain.count("<") == 2 and ">(" not in plain', '              and ">(" not in plain',
     'TestLiteralDollarPaths.test_TWIN_an_inner_layer_that_may_expand_keeps_it_unresolved'),
    ('literal $: a startup-steering variable means no literal',
     '    no_exact = ("$" not in cmd or _SSH_NESTED[0] > 0 or _SHELL_STARTUP_ENV[0]',
     '    no_exact = ("$" not in cmd or _SSH_NESTED[0] > 0',
     'TestLiteralDollarPaths.test_TWIN_an_inner_layer_that_may_expand_keeps_it_unresolved'),
    ('literal $: an unplaced ssh remote command is judged with no literal',
     '    no_exact = ("$" not in cmd or _SSH_NESTED[0] > 0 or _SHELL_STARTUP_ENV[0]',
     '    no_exact = ("$" not in cmd or _SHELL_STARTUP_ENV[0]',
     'TestLiteralDollarPaths.test_an_unplaced_ssh_host_is_judged_with_the_dollar_unresolved'),
    ('literal $: a definition anywhere means no literal',
     '        if _redefines_commands(text):' + NL + '            return True',
     '        if False:' + NL + '            return True',
     'TestLiteralDollarPaths.test_TWIN_a_definition_or_eval_anywhere_keeps_every_dollar_unresolved'),
    ('literal $: eval or source anywhere means no literal',
     '            if any(_verb_name(t) in ("eval", "source") for t in words_):', '            if False:',
     'TestLiteralDollarPaths.test_TWIN_a_definition_or_eval_anywhere_keeps_every_dollar_unresolved'),
    ('literal $: a command name that is not a plain word means no literal',
     '            if name and (name == "." or has_unresolved(name) or not _PLAIN_NAME.fullmatch(name)):',
     '            if False:',
     'TestLiteralDollarPaths.test_TWIN_a_definition_or_eval_anywhere_keeps_every_dollar_unresolved'),
    ('literal $: a shell with startup files or aliases means no literal',
     '            if any(_verb_name(t) in _SHELLS for t in words_) and any(',
     '            if False and any(',
     'TestLiteralDollarPaths.test_TWIN_a_definition_or_eval_anywhere_keeps_every_dollar_unresolved'),
    ('literal $: sudo -s / -i re-parses the command',
     '            return any(_SUDO_SHELL.match(t) for t in words_[k + 1:] if t.startswith("-"))',
     '            return False',
     'TestLiteralDollarPaths.test_TWIN_sudo_through_a_shell_keeps_it_unresolved'),
    ('literal $: a wrapper flag cluster ending in a value flag takes the next word',
     '                want_value = k_ == len(t) - 1', '                want_value = False',
     'TestLiteralDollarPaths.test_a_clustered_wrapper_flag_takes_its_value'),
    ('literal $: sudo -h takes no separate value in a cluster',
     '                           and not (wrapper == "sudo" and ch == "h")), None)', '                           ), None)',
     'TestLiteralDollarPaths.test_a_clustered_wrapper_flag_takes_its_value'),
    ('literal $: only an absolute path qualifies',
     '        return ("$" in path and literal_expansions.get(path, False) and is_abs(path)',
     '        return ("$" in path and literal_expansions.get(path, False)',
     'TestLiteralDollarPaths.test_only_an_absolute_literal_path_qualifies'),
    ('literal $: a pattern spelling never qualifies',
     '                and not any(char in path for char in "*?[]{}"))', '                )',
     'TestLiteralDollarPaths.test_TWIN_a_dollar_the_shell_expands_stays_unresolved'),
    ('literal $ (PowerShell): a function or alias in the RAW command means no literal',
     '                or _redefines_commands(raw_cmd) or _redefines_commands(cmd)',
     '                or _redefines_commands(cmd)',
     'TestLiteralDollarPathsPowerShell.test_TWIN_an_expanding_or_redefining_command_keeps_it_unresolved'),
    # --- 4.1.1 final round: xargs-fed writes (F360), one per clause
    ('xargs write: the command xargs runs is a reading (final)',
     '    if prev is not None and "xargs" in seg:' + NL + '        out.extend(_xargs_item_readings(seg, prev))',
     '    if False:' + NL + '        out.extend(_xargs_item_readings(seg, prev))',
     'test_an_xargs_fed_write_is_judged_like_its_literal'),
    ('xargs write: ...only when xargs runs it (final)',
     '    if vt not in toks or not any(_verb_name(t) == "xargs" for t in toks[:toks.index(vt)]):' + NL
     + '        return []' + NL + '    items = _producer_items(prev)',
     '    if vt not in toks:' + NL + '        return []' + NL + '    items = _producer_items(prev)',
     'test_TWIN_an_xargs_fed_write_elsewhere_or_a_read_does_not_fire'),
    ('xargs write: ...fed by an echo or printf only (final)',
     '    if verb(prev) not in ("echo", "printf"):' + NL + '        return []' + NL + '    toks = tokens_of(seg)',
     '    if False:' + NL + '        return []' + NL + '    toks = tokens_of(seg)',
     'test_TWIN_an_xargs_fed_write_elsewhere_or_a_read_does_not_fire'),
    ('xargs write: -I replaces the item, not appends it (final)',
     '    if repl:' + NL + '        if len(items) > _XARGS_ITEMS:',
     '    if False:' + NL + '        if len(items) > _XARGS_ITEMS:',
     'test_TWIN_an_xargs_fed_write_elsewhere_or_a_read_does_not_fire'),
    # --- 4.1.1 final round: bash's other pushd/popd forms, one per clause
    ('dir stack: the other forms are read (final)',
     '            if moved is not None:' + NL + '                return moved',
     '            if False:' + NL + '                return moved',
     'TestBashDirectoryStackForms.test_the_directory_in_force_after_each_form'),
    ('dir stack: a bare pushd swaps the top two (final)',
     '            return (top[0], top[1], stack)', '            return (cwd, from_seed, stack)',
     'TestBashDirectoryStackForms.test_the_directory_in_force_after_each_form'),
    ('dir stack: pushd -n adds below the top (final)',
     '        if len(words) > 1 and not _DIRSTACK_N.match(words[1]):', '        if False:',
     'TestBashDirectoryStackForms.test_the_directory_in_force_after_each_form'),
    ('dir stack: popd -n removes below the top (final)',
     '            stack.pop()                               # the entry below the top goes, no cd',
     '            pass',
     'TestBashDirectoryStackForms.test_the_directory_in_force_after_each_form'),
    ('dir stack: -N counts from the right (final)',
     '    idx = n if first[0] == "+" else len(dirs) - 1 - n', '    idx = n',
     'TestBashDirectoryStackForms.test_the_directory_in_force_after_each_form'),
    ('dir stack: popd +0 pops the top (final)',
     '        dirs = dirs[1:] or dirs', '        dirs = dirs',
     'TestBashDirectoryStackForms.test_the_directory_in_force_after_each_form'),
    ('dir stack: popd +N removes that entry (final)',
     '        dirs = dirs[:idx] + dirs[idx + 1:]', '        dirs = dirs',
     'TestBashDirectoryStackForms.test_the_directory_in_force_after_each_form'),
    # --- 4.1.1 final round: a write through an existing link (F360)
    ('link: a target is judged where its link lands too (final)',
     '                if not unres:' + NL + '                    real = link_resolved(seen',
     '                if False:' + NL + '                    real = link_resolved(seen',
     'test_a_write_or_delete_through_the_link_is_the_reference'),
    ('link: ...only when a link changes it (final)',
     '    return "" if same else real', '    return real',
     'test_link_resolved_names_the_target_only_through_a_link'),
    ('link: ...only for a resolved absolute path (final)',
     '    if not p or (not literal and has_unresolved(p)) or not is_abs(p):' + NL + '        return ""' + NL + '    s = p.replace(',
     '    if not p:' + NL + '        return ""' + NL + '    s = p.replace(',
     'test_link_resolved_names_the_target_only_through_a_link'),
    # --- 4.1.1 review A1: link resolution is bounded, one per clause
    ('link bound: a drive that is not local is not resolved (A1)',
     '        if _drive_type(s[0]) not in _LOCAL_DRIVE_TYPES:', '        if False:',
     'test_a_remote_or_rootless_drive_is_never_resolved'),
    ('link bound: a remote drive is not local (A1)',
     '_LOCAL_DRIVE_TYPES = frozenset((2, 3, 5, 6))', '_LOCAL_DRIVE_TYPES = frozenset((2, 3, 4, 5, 6))',
     'test_a_remote_or_rootless_drive_is_never_resolved'),
    ('link bound: the deadline is checked before each realpath (A1)',
     '    _check_deadline()' + NL + '    if _LINK_RESOLVED[0] >= _MAX_LINK_RESOLVED:',
     '    if _LINK_RESOLVED[0] >= _MAX_LINK_RESOLVED:',
     'test_the_deadline_is_checked_before_a_link_leafs_realpath'),
    ('link budget: the deadline is checked before each lstat',
     '        _check_deadline()' + NL + '        try:' + NL + '            st = os.lstat(_native(s))',
     '        try:' + NL + '            st = os.lstat(_native(s))',
     'test_the_deadline_is_checked_before_each_lstat'),
    ('link bound: at most _MAX_LINK_RESOLVED realpaths (A1)',
     '    if _LINK_RESOLVED[0] >= _MAX_LINK_RESOLVED:', '    if False:',
     'test_realpath_calls_are_capped_per_hook_call'),
    ('link bound: ...the realpath count per hook call (A1)',
     "    _LINK_RESOLVED[0] = 0               # link_resolved's", "    pass  # link_resolved's",
     'test_realpath_calls_are_capped_per_hook_call'),
    ('link bound: the drive type is cached (A1)',
     '    if key not in _DRIVE_TYPES:', '    if True:',
     'test_the_drive_type_is_asked_once_per_drive_per_call'),
    ('link bound: ...the drive types per hook call (A1)',
     '    _DRIVE_TYPES.clear()', '    pass',
     'test_the_drive_type_is_asked_once_per_drive_per_call'),
    # --- 4.1.1 review (link budget): past the bound an operand FAILS CLOSED, and a folder is resolved once
    ('link budget: past the bound is links_unresolved, never "no link"',
     '    if _LINK_RESOLVED[0] >= _MAX_LINK_RESOLVED:' + NL + '        _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse' + NL,
     '    if _LINK_RESOLVED[0] >= _MAX_LINK_RESOLVED:' + NL,
     'test_past_the_budget_the_operand_is_links_unresolved'),
    ('link budget: the flag reaches the facts',
     '    result["links_unresolved"] = _LINKS_UNRESOLVED[0]', '    result["links_unresolved"] = False',
     'test_RED_decoys_then_a_junction_is_never_read_as_no_link'),
    ('link budget: ...the flag per hook call',
     '    _LINK_DIRS.clear()' + NL + '    _LINKS_UNRESOLVED[0] = False' + NL,
     '    _LINK_DIRS.clear()' + NL + '    pass' + NL,
     'test_realpath_calls_are_capped_per_hook_call'),
    ('link budget: the last-component probe is bounded',
     '        if _LINK_PROBED[0] >= _MAX_LINK_PROBED:' + NL + '            _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse' + NL + '            return ""',
     '        if False:' + NL + '            _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse' + NL + '            return ""',
     'test_the_last_component_probe_is_bounded_too'),
    ('link budget: ...past its bound, links_unresolved',
     '        if _LINK_PROBED[0] >= _MAX_LINK_PROBED:' + NL + '            _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse' + NL + '            return ""',
     '        if _LINK_PROBED[0] >= _MAX_LINK_PROBED:' + NL + '            return ""',
     'test_the_last_component_probe_is_bounded_too'),
    ('link budget: a folder is resolved once (cached)',
     '        _LINK_DIRS[key] = rhead', '        pass',
     'test_a_folder_is_resolved_once_per_hook_call'),
    ('link budget: ...the folder cache per hook call',
     '    _LINK_DIRS.clear()' + NL + '    _LINKS_UNRESOLVED[0] = False', '    _LINKS_UNRESOLVED[0] = False',
     'test_a_folder_is_resolved_once_per_hook_call'),
    ('link budget: a last component that is a link is followed (POSIX symlink)',
     '            is_link = stat.S_ISLNK(st.st_mode) or bool(',
     '            is_link = False or bool(',
     'test_a_link_leaf_in_a_cached_folder_is_still_followed'),
    ('link budget: a last component that is a link is followed (Windows reparse point)',
     ' or bool(getattr(st, "st_file_attributes", 0) & 0x400)', '',
     'test_a_link_leaf_in_a_cached_folder_is_still_followed'),
    ('link budget: an lstat that cannot answer resolves the whole path',
     '            is_link = unknown = getattr(exc, "winerror", None) != 123', '            is_link = unknown = False',
     'test_a_last_component_lstat_cannot_read_is_resolved_whole'),
    # --- 4.1.1 CI round (cba3a1ce): a filesystem that cannot answer is links_unresolved; a respelling is no link
    ('link budget: a realpath that raised is links_unresolved, never "no link"',
     '"no link" (CI round for 4.1.1): refused when `refuse`.' + NL + '        _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse' + NL,
     '"no link" (CI round for 4.1.1): refused when `refuse`.' + NL,
     'test_RED_a_realpath_that_raises_is_links_unresolved'),
    ('link budget: a leaf lstat error with realpath showing no link is links_unresolved',
     '    if same and unknown:', '    if False:',
     'test_RED_a_leaf_lstat_error_with_a_realpath_showing_no_link_is_links_unresolved'),
    ('link budget: a realpath that only expands an 8.3 short name is no link',
     '        os.name == "nt" and _lexical_norm(real) == _lexical_norm(long_name(s)))', '        False)',
     'test_RED_a_respelled_realpath_of_a_plain_folder_is_not_a_link_simulated'),
    ('link budget: a name Windows rejects (ERROR_INVALID_NAME) is an answer, not "cannot tell"',
     '!= 123' + NL, ' is None or True' + NL,
     'test_TWIN_a_name_windows_rejects_is_not_links_unresolved'),
    ('link entries: a matching entry whose lstat fails is links_unresolved',
     '"no link".' + NL + '                        _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse' + NL,
     '"no link".' + NL,
     'test_RED_a_matching_entry_whose_lstat_fails_is_links_unresolved'),
    ('link budget: a drive-root file is not resolved from the current folder',
     '            head += "/"' + NL, '            pass' + NL,
     'test_a_file_at_a_drive_root_resolves_from_the_root_not_the_current_folder'),
    ('link budget: ...the last-component probe count per hook call',
     '    _LINK_PROBED[0] = 0' + NL, '    pass' + NL,
     'test_the_last_component_probe_is_bounded_too'),
    # --- 4.1.1 review (link budget round 2, F369): literal spellings, read-only operands, the ssh advisory
    ('link literal: a literal `$` is not an expansion to link_resolved',
     '    if not p or (not literal and has_unresolved(p)) or not is_abs(p):',
     '    if not p or has_unresolved(p) or not is_abs(p):',
     'test_RED_a_literal_dollar_in_a_folder_name_behind_a_junction'),
    ('link literal: ...the caller passes what it knows',
     'link_resolved(seen, literal=literal, refuse', 'link_resolved(seen, literal=False, refuse',
     'test_RED_a_literal_dollar_in_a_folder_name_behind_a_junction'),
    ('link literal: a stream suffix is resolved as Windows opens it',
     '        s = _win_alias(s)' + NL, '',
     'test_RED_an_ntfs_stream_suffix_behind_a_junction_is_judged_where_it_lands'),
    ("link head: an unresolved operand's literal head is followed",
     '                    real = link_head_resolved(seen or r, refuse=not reads_only)' + NL + '                    if real:',
     '                    real = link_head_resolved(seen or r, refuse=not reads_only)' + NL + '                    if False:',
     'test_an_expansion_behind_a_junction_reads_as_it_does_on_the_root'),
    ('link head: ...the head stops at the first unresolved component',
     '    head = "/".join(parts[:i])' + NL + '    if i == len(parts) or not head or not is_abs(head):' + NL + '        return ""',
     '    head = "/".join(parts[:i + 1])' + NL + '    if i == len(parts) or not head or not is_abs(head):' + NL + '        return ""',
     'test_an_expansion_behind_a_junction_reads_as_it_does_on_the_root'),
    ('link entries: a link the unresolved component may name is followed',
     '                    for real in link_entries_matched(seen or r, refuse=not reads_only):',
     '                    for real in []:',
     'test_RED_a_variable_inside_the_component_that_names_the_junction'),
    ('link entries: ...only an entry starting with the literal prefix',
     '                if not name.startswith(prefix):', '                if False:',
     'test_TWIN_a_variable_component_with_no_link_to_match'),
    ('link entries: ...only an entry that is a link',
     '                if not (stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400):',
     '                if False:',
     'test_TWIN_a_variable_component_with_no_link_to_match'),
    ('link entries: the prefix is read as Windows opens it',
     'for f in (_win_alias, _msys_spelling)})', 'for f in (_msys_spelling,)})',
     'test_RED_a_variable_inside_the_component_that_names_the_junction'),
    ('link entries: past the probe bound, links_unresolved',
     '        if _LINK_PROBED[0] >= _MAX_LINK_PROBED:' + NL + '            _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse' + NL + '            return out',
     '        if _LINK_PROBED[0] >= _MAX_LINK_PROBED:' + NL + '            return out',
     'test_a_variable_component_spends_the_link_budget'),
    ('link reads: search operands are read-only',
     'c_cwd, c_old, reads_only=True)', 'c_cwd, c_old)',
     'test_RED_a_read_only_search_over_many_folders_is_not_refused'),
    ('link reads: ...and never refuse past the realpath budget',
     '    if _LINK_RESOLVED[0] >= _MAX_LINK_RESOLVED:' + NL + '        _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse' + NL + '        return None',
     '    if _LINK_RESOLVED[0] >= _MAX_LINK_RESOLVED:' + NL + '        _LINKS_UNRESOLVED[0] = True' + NL + '        return None',
     'test_RED_a_read_only_search_over_many_folders_is_not_refused'),
    ('link reads: ...the flag is passed down',
     'real = link_resolved(seen, literal=literal, refuse=not reads_only)',
     'real = link_resolved(seen, literal=literal, refuse=True)',
     'test_RED_a_read_only_search_over_many_folders_is_not_refused'),
    ('link ssh: an unplaced remote text past the link budget is advised',
     '"verb_unresolved",' + NL + '                    "links_unresolved")', '"verb_unresolved")',
     'test_RED_an_unplaced_ssh_command_past_the_link_budget_is_advised'),
    ('link ssh: ...judged on its own links',
     '                _LINKS_UNRESOLVED[0] = False' + NL + '                sub = facts(',
     '                sub = facts(',
     'test_RED_an_unplaced_ssh_command_past_the_link_budget_is_advised'),
    # --- 4.1.1 review A2/A4: xargs items past the bound, and GNU's old -i spellings
    ('xargs items: past the bound is a refusal, never a cut (A2)',
     '        if len(items) > _XARGS_ITEMS:', '        if False:',
     'test_an_xargs_item_past_the_reading_bound_is_never_dropped'),
    ('xargs -i: a bare -i / --replace is {} (A4)',
     '        elif t in ("-i", "--replace"):', '        elif False:',
     'test_the_old_gnu_replace_spellings_are_read'),
    ('xargs -i: -iX attaches its replacement (A4)',
     '        elif t.startswith("-i") and len(t) > 2:', '        elif False:',
     'test_the_old_gnu_replace_spellings_are_read'),
    ('xargs -i: only the words after xargs count (A4)',
     '        if i <= xi:' + NL + '            continue', '        if False:' + NL + '            continue',
     'test_the_old_gnu_replace_spellings_are_read'),
    ('xargs -i: -i takes no separate value (A4)',
     '    "xargs": {"-I", "-n",', '    "xargs": {"-I", "-i", "-n",',
     'test_the_old_gnu_replace_spellings_are_read'),
    # --- 4.1.1 review A3: a pop that may not happen splits the lanes
    ('dir stack: a maybe-pop takes each value per lane (A3)',
     '            if v == "popd" and words and has_unresolved(words[0]):', '            if False:',
     'test_the_reader_splits_a_pop_that_may_not_happen'),
    ('dir stack: ...and splits the lanes (A3)',
     '        if v == "popd":' + NL + '            # Only an operand', '        if False:' + NL + '            # Only an operand',
     'test_the_reader_splits_a_pop_that_may_not_happen'),
    # --- 4.1.2: F368's two clauses with no catching test, and Git Bash's spelling of a link's name
    ('link budget: a path under a FILE (NotADirectoryError) is an answer, not "cannot tell"',
     '        except (FileNotFoundError, NotADirectoryError):' + NL + '            is_link = False                 # nothing there',
     '        except FileNotFoundError:' + NL + '            is_link = False                 # nothing there',
     'test_TWIN_a_path_under_a_file_is_not_links_unresolved'),
    ('link budget: an lstat ValueError is "cannot tell"',
     '            is_link = unknown = True        # cannot tell: resolve the whole path',
     '            is_link = unknown = False',
     'test_RED_an_lstat_that_raises_ValueError_is_links_unresolved'),
    ("msys spelling: Git Bash's spelling of the path is resolved too",
     '        if alt == s:' + NL, '        if True:' + NL,
     'test_RED_a_junction_named_with_a_character_git_bash_maps'),
    ('msys spelling: ...control characters are mapped too',
     ' + "".join(chr(i) for i in range(1, 32)))', ')',
     'test_RED_a_junction_named_with_a_character_git_bash_maps'),
    ('msys spelling: ...opened verbatim, so a trailing dot or space is kept',
     '        other = _resolve_spelling("//?/" + alt, refuse)', '        other = _resolve_spelling(alt, refuse)',
     'test_RED_a_junction_named_with_a_trailing_dot_or_space'),
    ('msys spelling: ...the last component is lstat-ed verbatim',
     '            st = os.lstat(_native(s))', '            st = os.lstat(s)',
     'test_RED_a_junction_named_with_a_trailing_dot_or_space'),
    ('msys spelling: two spellings landing in different places are refused',
     '_lexical_norm(_unmsys(other)):' + NL + '            _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse' + NL,
     '_lexical_norm(_unmsys(other)):' + NL + '            pass' + NL,
     'test_two_spellings_landing_in_different_places_are_refused'),
    ('msys spelling: one place spelled two ways is not refused',
     '_lexical_norm(_unmsys(other))', '_lexical_norm(other)',
     'test_an_expansion_behind_a_junction_reads_as_it_does_on_the_root'),
    ('msys spelling: ...resolved from the path as written, not its long-name form',
     '                    real = link_resolved(seen, literal=literal, refuse=not reads_only)',
     '                    real = link_resolved(long_name(seen), literal=literal, refuse=not reads_only)',
     'test_RED_a_short_named_folder_keeps_git_bash_spelling'),
    ('link entries: the prefix is read as Git Bash spells it',
     'for f in (_win_alias, _msys_spelling)})', 'for f in (_win_alias,)})',
     'test_RED_a_variable_in_a_mapped_junction_name'),
    # --- 4.1.2 release review (T1): clauses of the Git Bash spelling code that had no mutant
    ('msys spelling: a disagreement needs a Win32 landing (real and)',
     '        if real and other and _lexical_norm(_unmsys(real))', '        if other and _lexical_norm(_unmsys(real))',
     'test_RED_a_junction_named_with_a_character_git_bash_maps'),
    ('msys spelling: ...and a Git Bash landing (other and)',
     '        if real and other and _lexical_norm(_unmsys(real))', '        if real and _lexical_norm(_unmsys(real))',
     'test_TWIN_the_win32_spelling_is_still_followed'),
    ("msys spelling: Git Bash's landing is returned when Win32's is none",
     '        return real or other' + NL, '        return real' + NL,
     'test_RED_a_junction_named_with_a_character_git_bash_maps'),
    ('msys spelling: the drive is not respelled',
     '                                 for part in parts[1:]])',
     '                                 for part in parts[1:]]).replace(":", chr(0xF03A), 1)',
     'test_RED_a_junction_named_with_a_trailing_dot_or_space'),
    ('msys spelling: the mapping is undone before comparing',
     '    return "".join(chr(ord(c) - 0xF000) if 0xF000', '    return s if True else "".join(chr(ord(c) - 0xF000) if 0xF000',
     'test_an_expansion_behind_a_junction_reads_as_it_does_on_the_root'),
    # --- 4.1.2 release review: the fixes (I1a-c, I2-I5, P1-P3), one mutant per clause. Left out as EQUIVALENT, each
    # with its reason (so not a silent gap): `os.path.realpath(_native(s))` -> `realpath(s)` in _realpath_budgeted
    # (MEASURED on 3.12.10: ntpath.realpath turns `/` into `\` itself, so `//?/<w>/j./x`, `//?/<w>/s /x` and
    # `//?/<w>/k/../x` resolved exactly as their backslashed forms, 3 of 3; os.lstat does not, and its mutant is
    # caught); and join_as_received's `if not cwd or` (is_abs("") is False). CORRECTION 2026-11-11 (4.1.2 release
    # review round 3, T4): "the walk skipping `.`" and "the walk keeping `..` at a drive root", once listed here as
    # equivalent, are NOT (MEASURED, probe_equiv.py: they change the realpath count in 1 and 5 of 13 paths); each now has
    # a row (`dot dot: ... (round 3, T4)`).
    ('msys spelling: the Win32 landing is un-mapped too (I4)',
     '_lexical_norm(_unmsys(real)) != _lexical_norm(_unmsys(other))', '_lexical_norm(real) != _lexical_norm(_unmsys(other))',
     'test_RED_one_place_whose_target_has_a_mapped_name_is_not_refused'),
    ("msys spelling: Git Bash's landing is unwrapped (I5)",
     '        other = _unverbatim(other)' + NL, '',
     'test_the_git_bash_landing_is_returned_without_its_verbatim_prefix'),
    ('unverbatim: a verbatim or device drive prefix is dropped (P1)',
     '    m = _VERBATIM_DRIVE.match(s)' + NL + '    if m:', '    m = _VERBATIM_DRIVE.match(s)' + NL + '    if False:',
     'test_RED_a_verbatim_or_device_spelled_operand_is_resolved'),
    ('unverbatim: a verbatim UNC prefix is //host/share (I5)',
     '    return "//" + s[m.end():] if m else s', '    return s',
     'test_the_git_bash_landing_is_returned_without_its_verbatim_prefix'),
    ('link verbatim: an operand spelled //?/X: is resolved (P1)',
     '":/", _unverbatim(s))', '":/", s)',
     'test_RED_a_verbatim_or_device_spelled_operand_is_resolved'),
    ('link verbatim: ...and listed (P1)',
     '":/", _unverbatim(head))', '":/", head)',
     'test_RED_a_verbatim_or_device_spelled_operand_is_resolved'),
    ('link budget: a folder spelled alike both ways is resolved once (I2)',
     '    key = head[4:] if os.name == "nt" and head.startswith("//?/") else head', '    key = head',
     'test_RED_one_folder_spelled_the_same_both_ways_costs_one_resolution'),
    ('link budget: ...only a verbatim head is unwrapped (I2)',
     '    key = head[4:] if os.name == "nt" and head.startswith("//?/") else head', '    key = head[4:]',
     'test_a_write_or_delete_through_the_link_is_the_reference'),
    ('link reading: an operand as the program receives it (I1a)',
     '    return cwd.replace(chr(92), "/").rstrip("/") + "/" + p.replace(chr(92), "/")', '    return join_cwd(cwd, p)',
     'test_RED_dot_dot_after_a_link_is_physical'),
    ("link reading: ...the cwd keeps the spelling Git Bash opened (I1a)",
     '                pd_ = "" if execution_context else physical_cwd', '                pd_ = ""',
     'test_RED_a_cd_into_a_git_bash_spelled_junction'),
    ("link reading: ...only when it names the same folder (I1a)",
     '    if norm(spelled) == norm(cwd):', '    if False:',
     'test_RED_a_cd_into_a_git_bash_spelled_junction'),
    ('link reading: parse debris is read from the seeded directory (I1a)',
     ' or join_as_received(c_cwd, r, pd_)', '',
     'test_RED_parse_debris_is_read_from_the_seeded_directory'),
    ("link head: a relative operand's head is joined to its directory (P3)",
     '                    real = link_head_resolved(seen or r, refuse', '                    real = link_head_resolved(r, refuse',
     'test_RED_an_unresolved_relative_operand_is_read_through_its_link'),
    ('link entries: a relative operand is listed in its directory (P3)',
     '                    for real in link_entries_matched(seen or r, refuse', '                    for real in link_entries_matched(r, refuse',
     'test_RED_an_unresolved_relative_operand_is_read_through_its_link'),
    ('link entries: an entry is resolved verbatim by its own name (I1b)',
     '                    real = _realpath_budgeted(named.rstrip("/") + "/" + e.name if os.name == "nt"',
     '                    real = _realpath_budgeted(e.path.replace(chr(92), "/") if os.name == "nt"',
     'test_RED_a_listed_entry_is_resolved_by_its_own_name'),
    ('link entries: ...under the folder as Win32 names it (I1b)',
     '        heads = [(head, "//?/" + os.path.normpath(_win_alias(head)).replace(chr(92), "/"))]', '        heads = [(head, "//?/" + head)]',
     'test_TWIN_the_win32_listing_is_still_followed'),
    ('link entries: ...without turning a device name into the device (round 2, A)',
     '"//?/" + os.path.normpath(_win_alias(head))', '"//?/" + os.path.abspath(_win_alias(head))',
     'test_RED_a_folder_named_like_a_device_is_listed_under_its_own_name'),
    ('link entries: ...with trailing dots dropped as Win32 lists it (round 2, A)',
     '"//?/" + os.path.normpath(_win_alias(head))', '"//?/" + os.path.normpath(head)',
     'test_TWIN_the_win32_listing_is_still_followed'),
    ('link entries: ...and returned as a drive path (I1b)',
     '                    out.append(_unverbatim(real.replace(chr(92), "/")).rstrip("/")',
     '                    out.append(real.replace(chr(92), "/").rstrip("/")',
     'test_RED_a_listed_entry_is_resolved_by_its_own_name'),
    ("link entries: the head is listed under Git Bash's spelling too (I1c)",
     '        if _msys_spelling(head) != _win_alias(head):' + NL + '            heads.append(',
     '        if False:' + NL + '            heads.append(',
     'test_RED_a_head_folder_git_bash_spelled_is_listed'),
    ('link entries: a listing that fails moves on to the next (I1c)',
     '        except OSError:' + NL + '            continue' + NL + '    return out',
     '        except OSError:' + NL + '            return out' + NL + '    return out',
     'test_RED_a_head_folder_git_bash_spelled_is_listed'),
    ('link disagree: two spellings landing apart are named (I3)',
     '            _LINKS_DISAGREE[0] = _LINKS_DISAGREE[0] or refuse' + NL + '        return real or other',
     '        return real or other',
     'test_two_spellings_landing_in_different_places_are_refused'),
    ('link disagree: the fact reaches the facts (I3)',
     '    result["links_disagree"] = _LINKS_DISAGREE[0]', '    result["links_disagree"] = False',
     'test_two_spellings_landing_in_different_places_are_refused'),
    ('link disagree: ...per hook call (I3)',
     '    _LINKS_DISAGREE[0] = False' + NL, '',
     'test_two_spellings_landing_in_different_places_are_refused'),
    ('dot dot: a `..` is read physically on Windows (P2)',
     '    if os.name == "nt" and ".." in s.split("/"):', '    if False:',
     'test_RED_dot_dot_after_a_link_is_physical'),
    ('dot dot: ...only a path that has one (P2)',
     '    if os.name == "nt" and ".." in s.split("/"):', '    if os.name == "nt":',
     'test_a_write_or_delete_through_the_link_is_the_reference'),
    ('dot dot: the folder before a `..` is resolved (P2)',
     '        r = _dir_realpath(pre + walked, refuse)', '        r = pre + walked',
     'test_RED_dot_dot_after_a_link_is_physical'),
    ('dot dot: ...and its parent taken (P2)',
     ' else posixpath.dirname(r)' + NL, ' else posixpath.dirname(walked.rstrip("/"))' + NL,
     'test_RED_dot_dot_after_a_link_is_physical'),
    ('dot dot: a link crossed is noticed (P2)',
     '        crossed = crossed or not _same_place(r, walked)', '        crossed = False',
     # round 3 (G1): with the walked landing returned even when no link was crossed, this flag decides only whether
     # the LEXICAL reading is compared, so its catching test is the two-readings refusal
     'test_dot_dot_read_two_ways_landing_apart_is_refused'),
    ('dot dot: no link crossed costs no lexical realpath (P2)',
     '    if not crossed:' + NL + '        # 4.1.2 release review round 3 (G1)', '    if False:' + NL + '        # 4.1.2 release review round 3 (G1)',
     'test_TWIN_dot_dot_with_no_link_or_a_link_outside_the_roots'),
    ('dot dot: the walked place is the landing when it holds no link (P2)',
     '    phys = res or pre + walked', '    phys = res',
     'test_RED_dot_dot_after_a_link_is_physical'),
    ('dot dot: a lexical reading with no link gives the physical one (P2)',
     '    if not lex:' + NL + '        return phys_answer', '    if False:' + NL + '        return phys_answer',
     'test_RED_dot_dot_after_a_link_is_physical'),
    ('dot dot: two readings landing apart are refused (P2)',
     '_lexical_norm(_unmsys(_unverbatim(phys))):' + NL + '        _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse' + NL,
     '_lexical_norm(_unmsys(_unverbatim(phys))):' + NL,
     'test_dot_dot_read_two_ways_landing_apart_is_refused'),
    # --- 4.1.2, coordinator decisions after 4140faa5: a relative operand STARTING with an expansion is not read from
    # its directory; a folder before `..` that resolves off the local drives is refused at once
    ('link reading: a relative operand starting with an expansion gets no directory reading (F374 scope)',
     '                    seen = r if _leads_with_expansion(r) else seen', '                    pass',
     'test_RED_a_relative_operand_that_STARTS_with_an_expansion_is_not_listed'),
    ('link reading: ...only when its first component has an expansion at all (F374 scope)',
     '    return bool(at) and min(at) == 0', '    return min(at) == 0',
     'test_TWIN_a_literal_prefix_or_a_literal_first_folder_is_still_read'),
    ('link reading: ...only when that expansion is at the very start (F374 scope)',
     '    return bool(at) and min(at) == 0', '    return bool(at)',
     'test_TWIN_a_literal_prefix_or_a_literal_first_folder_is_still_read'),
    ('dot dot: a folder resolving off a drive path is not walked on (P2)',
     '        if not re.match(r"^[A-Za-z]:(/|$)", r) or _drive_type', '        if False or _drive_type',
     'test_RED_a_link_off_the_local_drives_before_a_dot_dot_is_refused_at_once'),
    ('dot dot: ...nor one on a drive that is not local (P2)',
     ' or _drive_type(r[0]) not in _LOCAL_DRIVE_TYPES:' + NL + '            _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse' + NL + '            return ""' + NL + '        crossed',
     ':' + NL + '            _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse' + NL + '            return ""' + NL + '        crossed',
     'test_RED_a_link_off_the_local_drives_before_a_dot_dot_is_refused_at_once'),
    ('dot dot: ...refused, never walked on (P2)',
     '_LOCAL_DRIVE_TYPES:' + NL + '            _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse' + NL + '            return ""' + NL + '        crossed',
     '_LOCAL_DRIVE_TYPES:' + NL + '            return ""' + NL + '        crossed',
     'test_RED_a_link_off_the_local_drives_before_a_dot_dot_is_refused_at_once'),
    ('dot dot: ...and named as a disagreement (P2)',
     '        _LINKS_DISAGREE[0] = _LINKS_DISAGREE[0] or refuse' + NL + '    # Only one landing', '    # Only one landing',
     'test_dot_dot_read_two_ways_landing_apart_is_refused'),
    # --- 4.1.2 release review round 2 (2026-11-11): A-E and the clauses (I) that had no mutant. Left out as EQUIVALENT,
    # each with its reason: `phys_answer = phys` and `return phys_answer` without `or lex` (CORRECTED in round 3, T4, to
    # cover a READ-ONLY operand too, whose landings profile_search_by_name reads: `phys_answer` differs from `phys` only
    # when the physical landing is the text's own lexical place, which the caller's lexical entry already carries, so no
    # rule, write or read, sees a new place; and `or lex` is reached only if the physical landing is that place while the
    # lexical reading goes through a link, which cannot happen, since the walked path IS that lexical place and
    # _resolve_spelling would find the same link on it -- INFERRED from the code, not measured); `pd_ if d == c_cwd` (`spelled` is used only
    # when it norm-equals the directory, and the physical cwd tracks c_cwd, so for d = c_old it is never used); in
    # _leads_with_expansion `i < len(parts) - 1` and `and i > 0` (an unresolved operand has a `$` in some component that
    # is neither `.` nor empty, so the loop stops before the end; a leading empty component is an ABSOLUTE operand,
    # whose root head neither link reading uses); the `X:` -> `X:/` line after the drive-root rule (a bare `X:` is
    # joined as `X:/c` or, ending the path, completed by _resolve_spelling's own `C:` rule: CORRECTION 2026-11-11, round 3
    # T4, MEASURED by probe_equiv.py: NOT equivalent, `//?/C:/Users/..` returned `//?//?/C:` and `C:/Users/..` returned
    # `C:`; it now has a row). The `os.name == "nt" and` note once here described code never committed; the committed
    # clauses have rows since round 3 (G3). RETIRED in round 2, all three
    # equivalent since its fixes: `...only one Win32 opens the same (I2)` (every drive folder is now opened verbatim, so
    # the condition was removed from the code) and `...an unknowable directory invents nothing (I1a)` (with cwd "" the
    # `spelled` "" norm-equals it and normpath gives `.`, a RELATIVE reading every link reading rejects; the guard stays,
    # as in join_cwd, and test_TWIN_a_relative_operand_with_no_known_directory_is_not_invented still pins the outcome);
    # and `...only a plain drive key (round 2, B)` (once a `//?/` head is cached without its prefix, every key on Windows
    # is a `X:/` drive folder, so the `re.match` was removed from the code: its mutant survived --only).
    ('link budget: a folder is resolved verbatim (round 2, B)',
     '_realpath_budgeted("//?/" + key if os.name == "nt" else key, refuse)',
     '_realpath_budgeted(key, refuse)',
     'test_RED_a_junction_named_like_a_device_is_resolved_verbatim'),
    ('link budget: ...and the answer unwrapped (round 2, B)',
     '        rhead = _unverbatim(rhead)' + NL, '',
     'test_the_git_bash_landing_is_returned_without_its_verbatim_prefix'),
    ('link reading: the directory\'s own dot segments are collapsed (round 2, C)',
     '        cwd = posixpath.normpath(spelled.replace(chr(92), "/"))', '        cwd = spelled',
     'test_RED_an_absolute_cd_through_a_link_and_dot_dot_is_logical'),
    ('link reading: a leading `./` is skipped (round 2, D)',
     'while i < len(parts) - 1 and (parts[i] == "." or (parts[i] == "" and i > 0)):', 'while False:',
     'test_RED_a_relative_operand_that_STARTS_with_an_expansion_is_not_listed'),
    ('link reading: ...a `.` component (round 2, D)',
     '(parts[i] == "." or (parts[i] == "" and i > 0))', '(parts[i] == "" and i > 0)',
     'test_RED_a_relative_operand_that_STARTS_with_an_expansion_is_not_listed'),
    ('link reading: ...and an empty one after it (round 2, D)',
     '(parts[i] == "." or (parts[i] == "" and i > 0))', '(parts[i] == ".")',
     'test_RED_a_relative_operand_that_STARTS_with_an_expansion_is_not_listed'),
    ('dot dot: a drive root is its own parent (round 2, E)',
     'walked = r + "/" if re.match(r"^[A-Za-z]:$", r) else posixpath.dirname(r)', 'walked = posixpath.dirname(r)',
     'test_RED_a_link_to_a_drive_root_keeps_its_drive'),
    ('dot dot: the verbatim spelling is walked as such (round 2, I)',
     '    pre = "//?/" if s.startswith("//?/") else ""', '    pre = ""',
     'test_RED_a_dot_dot_after_a_junction_only_git_bash_names'),
    ('dot dot: a lexical reading with no link is none (round 2, I)',
     '    lex = "" if lex is None or _same_place(lex, s) else lex', '    lex = "" if lex is None else lex',
     'test_RED_dot_dot_after_a_link_is_physical'),
    ('dot dot: ...the lexical reading is compared at all (round 2, I)',
     '    lex = "" if lex is None or _same_place(lex, s) else lex', '    lex = ""',
     'test_dot_dot_read_two_ways_landing_apart_is_refused'),
    ('dot dot: a read-only operand sets no disagreement (round 2, I)',
     '        _LINKS_DISAGREE[0] = _LINKS_DISAGREE[0] or refuse' + NL + '    # Only one landing',
     '        _LINKS_DISAGREE[0] = True' + NL + '    # Only one landing',
     'test_dot_dot_read_two_ways_landing_apart_is_refused'),
    ('msys spelling: a read-only operand sets no disagreement (round 2, I)',
     '            _LINKS_DISAGREE[0] = _LINKS_DISAGREE[0] or refuse' + NL + '        return real or other',
     '            _LINKS_DISAGREE[0] = True' + NL + '        return real or other',
     'test_TWIN_a_read_only_operand_never_sets_a_disagreement'),
    ('link reading: an absolute operand is read as written (round 2, I)',
     '    if is_abs(p):' + NL + '        return p' + NL + '    if not cwd or not is_abs(cwd):' + NL + '        return ""' + NL + '    if norm(spelled)',
     '    if False:' + NL + '        return p' + NL + '    if not cwd or not is_abs(cwd):' + NL + '        return ""' + NL + '    if norm(spelled)',
     'test_a_write_or_delete_through_the_link_is_the_reference'),
    # --- 4.1.2 release review round 3 (2026-11-11): G1, G3, T2 and the three walk clauses (T4) once listed as equivalent
    ('dot dot: a walked landing the text does not name lexically is returned (round 3, G1)',
     '        return res or ("" if _same_place(pre + walked, s) else pre + walked)', '        return res',
     'test_RED_a_dot_dot_above_the_drive_root_stays_at_the_root'),
    ('dot dot: ...but not where the text lands anyway (round 3, G1)',
     '("" if _same_place(pre + walked, s) else pre + walked)', '(pre + walked)',
     'test_TWIN_dot_dot_with_no_link_or_a_link_outside_the_roots'),
    ('dot dot: `.` is skipped without a lookup (round 3, T4)',
     '        if c in ("", "."):' + NL, '        if c in ("",):' + NL,
     'test_a_dot_dot_at_the_drive_root_costs_no_lookup_and_lands_on_the_root'),
    ('dot dot: `..` at a drive root costs no lookup (round 3, T4)',
     '        if walked.endswith(":/"):' + NL + '            continue', '        if False:' + NL + '            continue',
     'test_a_dot_dot_at_the_drive_root_costs_no_lookup_and_lands_on_the_root'),
    ('dot dot: a drive-root parent is `X:/` (round 3, T4)',
     '        walked = walked + "/" if walked.endswith(":") else walked' + NL, '',
     'test_a_dot_dot_at_the_drive_root_costs_no_lookup_and_lands_on_the_root'),
    ('link budget: `//?/` is a prefix only on Windows (round 3, G3)',
     '    key = head[4:] if os.name == "nt" and head.startswith("//?/") else head',
     '    key = head[4:] if head.startswith("//?/") else head',
     'test_a_verbatim_looking_posix_folder_keeps_its_name'),
    ('link budget: ...and a folder is opened verbatim only there (round 3)',
     '_realpath_budgeted("//?/" + key if os.name == "nt" else key, refuse)', '_realpath_budgeted("//?/" + key, refuse)',
     'test_a_write_or_delete_through_the_link_is_the_reference'),
    ('link bound: a leaf link is resolved inside the budget and the deadline (round 3, T2)',
     '        real = _realpath_budgeted(s, refuse) if is_link else',
     '        real = os.path.realpath(_native(s)).replace(chr(92), "/") if is_link else',
     'test_the_deadline_is_checked_before_a_link_leafs_realpath'),
]

SHIM = '''

# --- coverage harness shim (appended to a COPY, never to the real file) ---
_ORIG_FACTS = facts
_PIN_KEY = "{key}"
_PIN_VAL = {val}


def facts(payload, roots):          # noqa: F811
    d = _ORIG_FACTS(payload, roots)
    if _PIN_KEY in d:
        d[_PIN_KEY] = _PIN_VAL
    return d
'''

# `background` is a passthrough of the caller's own flag -- an INPUT the long-job rule
# consumes, not a rule predicate. Excluded by name and printed, never quietly dropped.
NOT_A_RULE = {"background"}

# Mutants of code that runs on ONE operating system, by label -> (the os.name it runs under, why).
# Elsewhere no test can reach the code, so the trial would read "NOT CAUGHT" for a reason that is
# not a coverage hole. They are left out of the trials on other systems -- named in the output,
# never quietly -- and still run, and must be caught, where the code runs. The same rule as the
# codex adapter mutants' Windows-only skip (8bdaa55). Every label must name exactly one mutant
# (anchor_problems), so a renamed mutant cannot leave a stale entry that excuses nothing.
_DRIVE_TYPE_ONLY = ("nt", "link_resolved asks GetDriveTypeW only when os.name == 'nt'; its target "
                    "tests skip elsewhere (drive letters are a Windows notion). MEASURED: uncaught on "
                    "ubuntu shards 1, 2 and 4 of CI run 42517544460")
# 4.1.2 release review (T3): the reason named TestLinkNamesGitBashSpellsDifferently, which is not where every row's
# target lives (`one place spelled two ways` targets a test that runs everywhere); what holds for every row is the code.
# 4.1.2 release review round 3 (T4): "runs only when os.name == 'nt'" was false for rows whose code also runs on POSIX
# and acts there on nothing; the reasons below are true of every row they cover.
_MSYS_ONLY = ("nt", "the code it mutates either runs only when os.name == 'nt' (the Windows branches of link_resolved, "
              "_resolve_spelling and link_entries_matched, and what they call) or, where it also runs on POSIX, acts only "
              "on what a POSIX path never carries (a `//?/` prefix, a U+F0xx name, a drive letter), so elsewhere no test "
              "can see it")
_VERBATIM_ONLY = ("nt", "a `//?/` folder reaches _dir_realpath only from Windows code (link_resolved's Git Bash spelling, "
                  "and _resolve_dot_dot walking that spelling); elsewhere the clause is never reached")
_DISAGREE_ONLY = ("nt", "only Windows code sets links_disagree (two spellings of a name, or `..` read two ways); "
                  "elsewhere the fact is False whatever this line does")
_DOTDOT_ONLY = ("nt", "_resolve_dot_dot runs only when os.name == 'nt': POSIX realpath already reads `..` physically "
                "(posixpath._joinrealpath splits the RESOLVED path)")
_POSIX_KEY_ONLY = ("posix", "on Windows the clause is always true, so the mutant changes nothing there; on POSIX it keeps a "
                   "`//?/` folder a path of its own and a folder resolved as written")
OS_ONLY = {
    "link bound: a drive that is not local is not resolved (A1)": _DRIVE_TYPE_ONLY,
    "link bound: a remote drive is not local (A1)": _DRIVE_TYPE_ONLY,
    "link bound: the drive type is cached (A1)": _DRIVE_TYPE_ONLY,
    "link bound: ...the drive types per hook call (A1)": _DRIVE_TYPE_ONLY,
    "link budget: a last component that is a link is followed (Windows reparse point)":
        ("nt", "a junction carries FILE_ATTRIBUTE_REPARSE_POINT and is not S_ISLNK; elsewhere the test's "
               "link is a symlink, which S_ISLNK alone catches"),
    "link budget: a last component that is a link is followed (POSIX symlink)":
        ("posix", "on Windows the test's link is a junction, caught by the reparse-point clause alone"),
    "link budget: a name Windows rejects (ERROR_INVALID_NAME) is an answer, not \"cannot tell\"":
        ("nt", "only Windows' lstat raises winerror 123 for a `*` leaf; on POSIX it is FileNotFoundError"),
    "link budget: a realpath that only expands an 8.3 short name is no link":
        ("nt", "the long-name reading runs only when os.name == 'nt'; its target tests skip elsewhere (8.3 short "
               "names are a Windows notion)"),
    "link budget: a drive-root file is not resolved from the current folder":
        ("nt", "a `C:` folder head is a Windows notion; its target test skips elsewhere"),
    "link entries: the prefix is read as Windows opens it":
        ("nt", "_win_alias on the prefix runs only when os.name == 'nt'; its subtest (`<junction>::$INDEX_ALLOCATION`) "
               "is Windows-only"),
    "link literal: a stream suffix is resolved as Windows opens it":
        ("nt", "_win_alias runs in link_resolved only when os.name == 'nt' (NTFS streams); elsewhere the "
               "lexical rules alone read `x::$DATA` as x"),
    "msys spelling: Git Bash's spelling of the path is resolved too": _MSYS_ONLY,
    "msys spelling: ...control characters are mapped too": _MSYS_ONLY,
    "msys spelling: ...opened verbatim, so a trailing dot or space is kept": _MSYS_ONLY,
    "msys spelling: ...the last component is lstat-ed verbatim": _MSYS_ONLY,
    "msys spelling: two spellings landing in different places are refused": _MSYS_ONLY,
    "msys spelling: one place spelled two ways is not refused": _MSYS_ONLY,
    "msys spelling: ...resolved from the path as written, not its long-name form": _MSYS_ONLY,
    "link entries: the prefix is read as Git Bash spells it": _MSYS_ONLY,
    "msys spelling: a disagreement needs a Win32 landing (real and)": _MSYS_ONLY,
    "msys spelling: ...and a Git Bash landing (other and)": _MSYS_ONLY,
    "msys spelling: Git Bash's landing is returned when Win32's is none": _MSYS_ONLY,
    "msys spelling: the drive is not respelled": _MSYS_ONLY,
    "msys spelling: the mapping is undone before comparing": _MSYS_ONLY,
    "msys spelling: the Win32 landing is un-mapped too (I4)": _MSYS_ONLY,
    "msys spelling: Git Bash's landing is unwrapped (I5)": _MSYS_ONLY,
    "unverbatim: a verbatim or device drive prefix is dropped (P1)": _MSYS_ONLY,
    "unverbatim: a verbatim UNC prefix is //host/share (I5)": _MSYS_ONLY,
    "link verbatim: an operand spelled //?/X: is resolved (P1)": _MSYS_ONLY,
    "link verbatim: ...and listed (P1)": _MSYS_ONLY,
    "link entries: an entry is resolved verbatim by its own name (I1b)": _MSYS_ONLY,
    "link entries: ...under the folder as Win32 names it (I1b)": _MSYS_ONLY,
    "link entries: ...and returned as a drive path (I1b)": _MSYS_ONLY,
    "link entries: the head is listed under Git Bash's spelling too (I1c)": _MSYS_ONLY,
    "link entries: a listing that fails moves on to the next (I1c)": _MSYS_ONLY,
    "link budget: a folder spelled alike both ways is resolved once (I2)": _VERBATIM_ONLY,
    "link disagree: two spellings landing apart are named (I3)": _DISAGREE_ONLY,
    "link disagree: the fact reaches the facts (I3)": _DISAGREE_ONLY,
    "link disagree: ...per hook call (I3)": _DISAGREE_ONLY,
    "dot dot: a `..` is read physically on Windows (P2)": _DOTDOT_ONLY,
    "dot dot: ...only a path that has one (P2)": _DOTDOT_ONLY,
    "dot dot: the folder before a `..` is resolved (P2)": _DOTDOT_ONLY,
    "dot dot: ...and its parent taken (P2)": _DOTDOT_ONLY,
    "dot dot: a link crossed is noticed (P2)": _DOTDOT_ONLY,
    "dot dot: no link crossed costs no lexical realpath (P2)": _DOTDOT_ONLY,
    "dot dot: the walked place is the landing when it holds no link (P2)": _DOTDOT_ONLY,
    "dot dot: a lexical reading with no link gives the physical one (P2)": _DOTDOT_ONLY,
    "dot dot: two readings landing apart are refused (P2)": _DOTDOT_ONLY,
    "dot dot: ...and named as a disagreement (P2)": _DOTDOT_ONLY,
    "dot dot: a folder resolving off a drive path is not walked on (P2)": _DOTDOT_ONLY,
    "dot dot: ...nor one on a drive that is not local (P2)": _DOTDOT_ONLY,
    "dot dot: ...refused, never walked on (P2)": _DOTDOT_ONLY,
    "dot dot: a walked landing the text does not name lexically is returned (round 3, G1)": _DOTDOT_ONLY,
    "dot dot: ...but not where the text lands anyway (round 3, G1)": _DOTDOT_ONLY,
    "dot dot: `.` is skipped without a lookup (round 3, T4)": _DOTDOT_ONLY,
    "dot dot: `..` at a drive root costs no lookup (round 3, T4)": _DOTDOT_ONLY,
    "dot dot: a drive-root parent is `X:/` (round 3, T4)": _DOTDOT_ONLY,
    "link budget: `//?/` is a prefix only on Windows (round 3, G3)": _POSIX_KEY_ONLY,
    "link budget: ...and a folder is opened verbatim only there (round 3)": _POSIX_KEY_ONLY,
    "link budget: a folder is resolved verbatim (round 2, B)": _MSYS_ONLY,
    "link budget: ...and the answer unwrapped (round 2, B)": _MSYS_ONLY,
    "link entries: ...without turning a device name into the device (round 2, A)": _MSYS_ONLY,
    "link entries: ...with trailing dots dropped as Win32 lists it (round 2, A)": _MSYS_ONLY,
    "msys spelling: a read-only operand sets no disagreement (round 2, I)": _MSYS_ONLY,
    "dot dot: a drive root is its own parent (round 2, E)": _DOTDOT_ONLY,
    "dot dot: the verbatim spelling is walked as such (round 2, I)": _DOTDOT_ONLY,
    "dot dot: a lexical reading with no link is none (round 2, I)": _DOTDOT_ONLY,
    "dot dot: ...the lexical reading is compared at all (round 2, I)": _DOTDOT_ONLY,
    "dot dot: a read-only operand sets no disagreement (round 2, I)": _DOTDOT_ONLY,
    "link reading: the directory's own dot segments are collapsed (round 2, C)":
        ("nt", "on POSIX physical_join already normalises an absolute cd target (posixpath.normpath), so the directory "
               "reaching join_as_received has no dot segment there"),
}


def runs_here(label: str, os_name: str | None = None) -> bool:
    """Can a test reach this mutant's code on this operating system?"""
    need = OS_ONLY.get(label)
    return need is None or need[0] == (os.name if os_name is None else os_name)


def test_sources(text: str) -> dict:
    """{"Class.method" (a module-level test: its bare name): its own source, plus its class's
    non-test body (FACT/KEYS attributes)}. Keyed exactly as failed_tests() reports a failure
    and test_index()/qualify() name a target (FX-G5 / reviewer J2 item 8): keyed by the bare
    METHOD, two classes sharing a name POOLED their sources, so a red test in one class was
    credited with the predicate its namesake in another class mentions.

    For the silent? column (reviewer E10, v4.0.0 delta review): pinning a predicate TRUE broke
    the catch-all `test_TWIN_a_read_is_not_a_write` -- which asserts EVERY fact is false -- for
    every predicate at once, so every rule read "probed" whether or not any test meant to keep
    THAT rule silent existed. A failing test now credits a predicate only when its source
    names the predicate (`"rm_hits_game"`), i.e. it was written about that rule."""
    import ast
    lines = text.splitlines(keepends=True)

    def src(node):
        return "".join(lines[node.lineno - 1:node.end_lineno])

    out: dict = {}
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            out[node.name] = out.get(node.name, "") + src(node)
        elif isinstance(node, ast.ClassDef):
            attrs = "".join(src(n) for n in node.body
                            if not (isinstance(n, ast.FunctionDef) and n.name.startswith("test_")))
            for n in node.body:
                if isinstance(n, ast.FunctionDef) and n.name.startswith("test_"):
                    out[f"{node.name}.{n.name}"] = src(n) + attrs
    return out


def names_predicate(source: str, key: str) -> bool:
    return ('"%s"' % key) in source or ("'%s'" % key) in source


def credited(failed: set, srcs: dict, key: str) -> set:
    """The failed tests ("Class.method") whose OWN source names predicate *key* (E10)."""
    return {t for t in failed if names_predicate(srcs.get(t, ""), key)}


LAST_OUTPUT = ""


def baseline_refusal(rc, failed, ran, output, keep=6000):
    """What a refused baseline prints: the tests that failed and the end of their output, where unittest puts the
    tracebacks. An intermittent failure cannot be chased from a count."""
    names = "\n".join("  " + str(f) for f in sorted(failed))
    return (f"REFUSING: baseline is not green (rc={rc}, ran={ran}, {len(failed)} failed). "
            f"No mutation result would mean anything.\n{names}\n--- end of the test output ---\n{output[-keep:]}")


def run(work: Path, tests=()):
    # PYTHONDONTWRITEBYTECODE, and it is not hygiene -- without it this gate returns
    # WRONG VERDICTS. CPython invalidates a cached .pyc on (source mtime in SECONDS,
    # source size). Successive mutants are written to the same path within the same
    # second, so whenever two of them leave the file the same size, the second run
    # imports the FIRST one's bytecode and the mutant is judged by the previous
    # mutant's failures.
    #
    # MEASURED 2026-09-02: 2 of 54 mutants reported "NOT CAUGHT by its target test"
    # while each, reproduced by hand, turned its target test red. The tell was that
    # the failing tests named in each case belonged to the mutant BEFORE it in the
    # list. With bytecode off: 0 of 54 uncaught.
    #
    # The false-alarm direction is the one that was observed; the silent direction is
    # worse and equally reachable -- a mutant reported CAUGHT because the previous
    # mutant's failures happened to include its target test.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for pyc in work.rglob("__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)
    p = subprocess.run([sys.executable, "test_hook_facts.py", *tests], cwd=work,
                       capture_output=True, text=True, errors="replace", env=env)
    out = p.stdout + p.stderr
    global LAST_OUTPUT
    LAST_OUTPUT = out
    failed = failed_tests(out)
    ran = re.search(r"Ran (\d+) tests?", out)
    return p.returncode, failed, int(ran.group(1)) if ran else -1


# A failed test is "Class.method", never the bare method (FX-G3, v4.0.0 delta review). The bare
# name collides: test_hook_facts.py defines test_suffix_strip, test_pattern_replace and
# test_array_index in TWO classes each (MEASURED: 3 of 618 test names), so a mutant whose target
# is "test_suffix_strip" was credited as CAUGHT when the OTHER class's test went red.
_FAILED = re.compile(r"^(?:FAIL|ERROR): (\w+) \(([\w.]+)\)", re.M)


def failed_tests(out: str) -> set:
    """Qualified "Class.method" for every FAIL/ERROR line. Python <= 3.10 prints
    `test_x (module.Class)`; 3.11+ prints `test_x (module.Class.test_x)`."""
    found = set()
    for meth, dotted in _FAILED.findall(out):
        parts = dotted.split(".")
        cls = parts[-2] if parts[-1] == meth and len(parts) > 1 else parts[-1]
        found.add(f"{cls}.{meth}")
    return found


def test_index(source: str) -> dict:
    """{method name: [classes defining it]} for every test method in test_hook_facts.py."""
    import ast
    idx: dict = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef):
            for m in node.body:
                if isinstance(m, ast.FunctionDef) and m.name.startswith("test"):
                    idx.setdefault(m.name, []).append(node.name)
    return idx


def qualify(tgt: str, idx: dict):
    """(qualified target, None) or (None, why). A bare method name is accepted only when exactly
    ONE class defines it; an ambiguous or unknown target refuses rather than guessing."""
    if "." in tgt:
        cls, meth = tgt.split(".", 1)
        return (tgt, None) if cls in idx.get(meth, []) else (None, f"{tgt}: no such test")
    owners = idx.get(tgt, [])
    if len(owners) == 1:
        return f"{owners[0]}.{tgt}", None
    if not owners:
        return None, f"{tgt}: no such test"
    return None, f"{tgt}: AMBIGUOUS, defined in {', '.join(owners)} -- write Class.{tgt}"


def wait_for_dispatch():
    """Cooperate with rb; stale/unreadable telemetry is an error, never permission."""
    path = os.environ.get("RB_PRESSURE_FILE")
    while path:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        if time.time() - state["updated"] > 30:
            raise RuntimeError("resource monitor heartbeat is stale")
        if not state["blocked"]:
            return
        time.sleep(1)


def evaluate_source(source, tests=()):
    wait_for_dispatch()
    with tempfile.TemporaryDirectory(prefix="x4-vht-case-") as td:
        work = Path(td)
        for f in FILES:
            shutil.copy2(HOOKS / f, work / f)
        (work / "hook_facts.py").write_text(source, encoding="utf-8", newline="")
        started = time.monotonic()
        rc, failed, ran = run(work, tests)
        return {"rc": rc, "failed": sorted(failed), "ran": ran,
                "seconds": time.monotonic()-started}


def _shard_arg(text: str) -> tuple:
    try:
        k, n = (int(x) for x in text.split("/"))
    except ValueError:
        raise argparse.ArgumentTypeError("expected K/N, e.g. 2/4")
    if not 1 <= k <= n:
        raise argparse.ArgumentTypeError("K must be between 1 and N")
    return k, n


def shard_groups(n_mutants: int, keys: list, k: int, n: int) -> set:
    """The trial keys of shard k of n. One group per mutant and one per predicate (its False
    AND True trials together, because a predicate's verdict reads both), dealt round-robin,
    so every group lands in exactly one shard and the shards' union is the whole population."""
    groups = [["mutation:" + str(i)] for i in range(n_mutants)]
    groups += [["predicate:%s:%s" % (key, v) for v in ("False", "True")] for key in keys]
    return {t for g in groups[k - 1::n] for t in g}


def anchor_problems(pristine: str, test_source: str, mutants=None) -> list:
    """Every reason the mutant table cannot run against this source, without running anything:
    an anchor that does not appear exactly once, a replacement that changes nothing, a target that
    does not name exactly one test, or a table with nothing in it."""
    mutants = MUTANTS if mutants is None else mutants
    if not mutants:
        return ["the mutant set is EMPTY, so 0 of 0 uncaught would be a statement about nothing"]
    idx = test_index(test_source)
    out = []
    for label, old, new, tgt in mutants:
        count = pristine.count(old)
        if count != 1:
            out.append("%s: DID NOT APPLY (%d matches) -- stale mutant" % (label, count))
        if old == new:
            out.append("%s: the replacement is the anchor, so nothing is mutated" % label)
        if qualify(tgt, idx)[0] is None:
            out.append("%s: %s" % (label, qualify(tgt, idx)[1]))
    labels = [m[0] for m in mutants]
    for label in sorted({x for x in labels if labels.count(x) > 1}):
        out.append("%s: the label names %d mutants, so a report line cannot say which"
                   % (label, labels.count(label)))
    if mutants is MUTANTS:
        for label in sorted(set(OS_ONLY) - set(labels)):
            out.append("%s: an OS_ONLY entry that names no mutant" % label)
    return out


def check_anchors() -> int:
    """--check-anchors: the table against the hooks, no trials. 0 clean, 1 a problem, 2 no hooks."""
    try:
        pristine = (HOOKS / "hook_facts.py").read_text(encoding="utf-8")
        source = (HOOKS / "test_hook_facts.py").read_text(encoding="utf-8")
    except OSError as exc:
        print("REFUSING: the hooks could not be read (%s)" % type(exc).__name__, file=sys.stderr)
        return 2
    problems = anchor_problems(pristine, source)
    for line in problems:
        print(line)
    if problems:
        print("REFUSING: %d problem(s) in the mutant table of %d, no trial was run"
              % (len(problems), len(MUTANTS)))
        return 1
    print("%d mutant anchors each appear exactly once and name one test; no trial was run" % len(MUTANTS))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=range(1,9), default=1)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--sample", type=int, help="benchmark N evenly spaced trials; exit 3, never a release pass")
    parser.add_argument("--shard", type=_shard_arg, metavar="K/N",
                        help="run only shard K of N (1-based): every mutant, and each predicate's "
                             "False/True pair, lands in exactly one shard; all N must pass")
    parser.add_argument("--check-anchors", action="store_true",
                        help="check the mutant table against the hooks, run no trial")
    parser.add_argument("--only", type=re.compile, metavar="REGEX",
                        help="run the mutants whose label matches, each against its target test's "
                             "class only, and no predicate pairs; exit 3, never a release pass")
    args = parser.parse_args(argv)
    if args.check_anchors:
        return check_anchors()
    if args.shard and args.sample is not None:
        parser.error("--shard and --sample do not combine")
    if args.only and (args.shard or args.sample is not None):
        parser.error("--only does not combine with --shard or --sample")
    selected = {i for i, m in enumerate(MUTANTS) if args.only.search(m[0])} if args.only else None
    if selected is not None and not selected:
        print("REFUSING: --only matched no mutant label", file=sys.stderr)
        return 2
    def inputs():
        return {str(p.relative_to(REPO)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [Path(__file__).resolve(),*[HOOKS/f for f in FILES]]}
    fingerprint=inputs()
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        for f in FILES:
            src = HOOKS / f
            if not src.is_file():
                print(f"REFUSING: {src} not found", file=sys.stderr)
                return 2
            shutil.copy2(src, work / f)
        target = work / "hook_facts.py"
        pristine = target.read_text(encoding="utf-8")

        # --only runs the target classes alone, so the floor is "the class ran at all".
        floor, base_tests = 20, ()
        if selected is not None:
            floor = 1
            index = test_index((HOOKS / "test_hook_facts.py").read_text(encoding="utf-8"))
            targets = (qualify(MUTANTS[i][3], index)[0] for i in selected)
            base_tests = sorted({t.split(".")[0] for t in targets if t})
        rc, failed, ran = run(work, base_tests)
        if rc != 0 or ran < floor:
            print(baseline_refusal(rc, failed, ran, LAST_OUTPUT), file=sys.stderr)
            return 2
        print(f"baseline: {ran} tests green\n")

        # A target test that does not exist reports "NOT CAUGHT" -- the same words as
        # a real coverage hole, and the message you would act on by writing a test that
        # is already there. MEASURED 2026-09-02: five mutants added that day named test
        # CLASSES, which unittest never prints in a `FAIL:` line, and all five read as
        # coverage holes. The direction is safe, so this is legibility, not a bypass.
        # AN EMPTY MUTANT SET SCORES A PERFECT ZERO. "mutations not caught by
        # their target test: 0 of 0" reads exactly like a clean run, and
        # `return 1 if (bad or gaps) else 0` makes it rc 0 — a PASS from an
        # instrument that examined nothing, in the tool whose whole subject is
        # whether the tests examine anything.
        if not MUTANTS:
            print("REFUSING: the mutant set is EMPTY, so 0 of 0 uncaught would "
                  "be a statement about nothing.", file=sys.stderr)
            return 2
        source = (HOOKS / "test_hook_facts.py").read_text(encoding="utf-8")
        idx = test_index(source)
        qualified, missing = {}, []
        for _l, _o, _n, t in MUTANTS:
            q, why = qualify(t, idx)
            if q is None:
                missing.append(why)
            qualified[t] = q
        if missing:
            print("REFUSING: %d mutant target(s) do not name exactly one test in test_hook_facts.py: %s"
                  % (len(set(missing)), "; ".join(sorted(set(missing)))), file=sys.stderr)
            print("  (a target is a test METHOD name, or Class.method when the name is defined "
                  "in more than one class)", file=sys.stderr)
            return 2

        stale = [(label, pristine.count(old)) for label, old, _, _ in MUTANTS
                 if pristine.count(old) != 1]
        if stale:
            for label, count in stale:
                print(f"{label}: DID NOT APPLY ({count} matches) -- stale mutant")
            print(f"REFUSING: {len(stale)} stale anchor(s), before expensive trials")
            return 1
        import importlib.util
        spec = importlib.util.spec_from_file_location("vht_pristine", target)
        H = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(H)
        keys = sorted(k for k,v in H.facts({"tool_input":{"command":"echo x"}}, {}).items()
                      if isinstance(v,bool) and k not in NOT_A_RULE)
        if not keys:
            print("REFUSING: empty predicate population")
            return 2
        cases = [("mutation:"+str(i), pristine.replace(old,new))
                 for i, (_,old,new,_) in enumerate(MUTANTS)]
        cases += [("predicate:"+k+":"+v, pristine+SHIM.format(key=k,val=v))
                  for k in keys for v in ("False","True")]
        population = len(cases)
        if selected is not None:
            cases = [c for c in cases if c[0] in {"mutation:" + str(i) for i in selected}]
            print(f"--only: {len(cases)} of {population} trials, each against its target test's class\n")
        # CI SHARDS (4.0.1): the whole population took ~2 h 10 m on 4 workers at 228 mutants and
        # was still running 5 h 20 m into the 360-minute job at 288 (ci/v4.0.1-rc6), because each
        # trial's hook file went 41 s -> 75 s (the PowerShell link tests start one pwsh per shape).
        if args.shard:
            mine = shard_groups(len(MUTANTS), keys, *args.shard)
            cases = [c for c in cases if c[0] in mine]
            if not cases:
                print("REFUSING: shard %d/%d holds no trial -- an empty shard would pass "
                      "having examined nothing" % args.shard, file=sys.stderr)
                return 2
            print(f"shard {args.shard[0]}/{args.shard[1]}: {len(cases)} of {population} trials\n")
        # Code another operating system runs: named and left out here, run where it runs (OS_ONLY).
        elsewhere = [c for c in cases if c[0].startswith("mutation:")
                     and not runs_here(MUTANTS[int(c[0].split(":")[1])][0])]
        if elsewhere:
            cases = [c for c in cases if c not in elsewhere]
            print("not run on os.name %r, whose code runs only on another system: %d" % (os.name, len(elsewhere)))
            for key, _s in elsewhere:
                label = MUTANTS[int(key.split(":")[1])][0]
                print("  %s (needs os.name %r)" % (label, OS_ONLY[label][0]))
            print()
            if not cases:
                print("REFUSING: every selected trial runs only on another system -- nothing was examined",
                      file=sys.stderr)
                return 2
        if args.sample is not None:
            if not 1 <= args.sample <= population:
                parser.error("--sample must be between 1 and the trial population")
            cases = [cases[i*population//args.sample] for i in range(args.sample)]
        # A GitHub Actions runner is an ephemeral VM running nothing but this job, so the
        # rb admission this guard stands for has nothing to protect there. CI needs the
        # workers: 298 serial trials of a 782-test file were still running 4 h into the
        # 300-minute job limit (ci/v4-rc10, 2026-10-20).
        if (args.workers > 1 and not os.environ.get("RB_PRESSURE_FILE")
                and not (os.environ.get("GITHUB_ACTIONS") == "true"
                         and os.environ.get("RUNNER_TEMP"))):
            print("REFUSING: parallel trials require rb.py run (or a GitHub Actions runner)")
            return 2
        def tests_of(key):
            if selected is None:
                return ()
            return (qualified[MUTANTS[int(key.split(":")[1])][3]].split(".")[0],)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            values = []
            for result in pool.map(evaluate_source, [s for _,s in cases], [tests_of(k) for k,_ in cases]):
                values.append(result)
                print(f"trial {len(values)}/{len(cases)} completed", flush=True)
        results = dict(zip([key for key,_ in cases], values))
        if inputs()!=fingerprint:
            raise RuntimeError("hook verification inputs changed during run")
        if args.report:
            args.report.write_text(json.dumps({"inputs":fingerprint,"python":sys.version,
                                              "workers":args.workers,"population":population,
                                              "sample":args.sample,"cases":results},indent=2),
                                   encoding="utf-8")
        if args.sample is not None:
            print(f"BENCHMARK ONLY: {len(cases)} of {population} trials; no release verdict")
            return 3

        bad = 0
        n_mut = n_keys = 0
        print(f"{'MUTATION':<42} {'target test':<12} verdict")
        print("-" * 78)
        for i, (label, old, new, tgt) in enumerate(MUTANTS):
            if "mutation:" + str(i) not in results:
                continue                    # another shard's trial
            n_mut += 1
            if pristine.count(old) != 1:
                print(f"{label:<42} {'-':<12} *** DID NOT APPLY "
                      f"({pristine.count(old)} matches) -- the mutant is stale ***")
                bad += 1
                continue
            target.write_text(pristine.replace(old, new), encoding="utf-8", newline="")
            result = results["mutation:"+str(i)]
            rc, failed, ran = result["rc"], set(result["failed"]), result["ran"]
            if ran < floor:
                print(f"{label:<42} {'BROKE':<12} *** broke the suite, proves nothing ***")
                bad += 1
            elif qualified[tgt] in failed:
                extra = len(failed) - 1
                print(f"{label:<42} {'RED':<12} correct"
                      + (f" (+{extra} other)" if extra else " (only this test)"))
            else:
                print(f"{label:<42} {'green':<12} *** NOT CAUGHT by its target test ***")
                bad += 1
        target.write_text(pristine, encoding="utf-8", newline="")

        sys.path.insert(0, str(work))
        import hook_facts as H
        keys = sorted(k for k, v in H.facts({"tool_input": {"command": "echo x"}}, {}).items()
                      if isinstance(v, bool) and k not in NOT_A_RULE)
        # THE SAME HOLE ON THE COVERAGE SIDE, and this one is reachable without
        # anybody editing a list: `keys` is derived from a LIVE call to
        # `H.facts(...)`, so a module whose shape changed — rules renamed,
        # made non-boolean, or moved behind a branch this sample input does not
        # take — yields an empty list, and the run prints "predicates with a
        # coverage gap: 0 of 0" and exits 0. Probing NO predicate is not the
        # same as probing every predicate successfully.
        if not keys:
            print("REFUSING: no boolean rule predicate was enumerated from "
                  "hook_facts.facts(), so 0 of 0 coverage gaps would be a "
                  "statement about nothing. The module shape has probably "
                  "changed.", file=sys.stderr)
            return 2
        print(f"\nexcluded as not-a-rule: {sorted(NOT_A_RULE)}")
        print(f"\n{'PREDICATE':<42} {'fires?':<14} silent?")
        print("-" * 78)
        gaps = 0
        srcs = test_sources(source)
        for k in keys:
            if "predicate:" + k + ":False" not in results:
                continue                    # another shard's pair
            n_keys += 1
            row = []
            for val in ("False", "True"):
                target.write_text(pristine + SHIM.format(key=k, val=val),
                                  encoding="utf-8", newline="")
                result = results["predicate:"+k+":"+val]
                _rc, f, r = result["rc"], set(result["failed"]), result["ran"]
                if val == "True":
                    # silent?: only a must-NOT-fire test ABOUT this predicate counts (E10).
                    # `f` holds "Class.method" (FX-G3), and so does srcs (FX-G5 / J2 item 8): a
                    # bare-method lookup first found nothing (FX-G4, 32 of 32 NONE), then pooled
                    # the sources of same-named tests in different classes.
                    f = credited(f, srcs, k)
                row.append("BROKE" if r < 20 else ("probed" if f else "*** NONE ***"))
            if "*** NONE ***" in row or "BROKE" in row:
                gaps += 1
            print(f"{k:<42} {row[0]:<14} {row[1]}")
        target.write_text(pristine, encoding="utf-8", newline="")

    print("-" * 78)
    if args.shard:
        print(f"shard {args.shard[0]}/{args.shard[1]} only -- the verdict covers ALL shards only "
              f"when every one of the {args.shard[1]} passes")
    print(f"mutations not caught by their target test: {bad} of {n_mut}")
    print(f"predicates with a coverage gap:            {gaps} of {n_keys}")
    print("  fires?   = predicate pinned FALSE, so a must-FIRE test must break")
    print("  silent?  = predicate pinned TRUE,  so a must-NOT-fire test NAMING it must break")
    if selected is not None:
        print("--only: %d mutant(s), no predicate pair and no release verdict" % n_mut)
        return 1 if bad else 3
    return 1 if (bad or gaps) else 0


if __name__ == "__main__":
    raise SystemExit(main())
