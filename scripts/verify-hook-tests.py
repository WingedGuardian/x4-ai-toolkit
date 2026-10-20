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
FILES = ("hook_facts.py", "test_hook_facts.py", "ps_translate.ps1")

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
     "        return glob_covers(n, g) or glob_covers(n, g + \"/extensions\")",
     "        return False",
     "test_a_trailing_glob_is_still_the_installation"),
    ("a glob operand cannot walk past a ROOT hard block",
     "            elif glob_covers(norm(path), norm(root)):",
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
     "        if name in WRAPPERS:", "        if False:",
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
    ("a non-int timeout still counts",
     '"timeout_over_cap": _as_ms(timeout) > (7200000 if background is True else 600000),',
     '"timeout_over_cap": isinstance(timeout, int) and timeout > (7200000 if background is True else 600000),',
     "test_a_float_over_the_cap_fires"),
    # 2026-10-02: a background call has its own 2 h cap. The mutant drops it, so the
    # foreground cap judges background calls again -- the false deny that was fixed.
    ("a background call has the background cap",
     '"timeout_over_cap": _as_ms(timeout) > (7200000 if background is True else 600000),',
     '"timeout_over_cap": _as_ms(timeout) > 600000,',
     "test_a_BACKGROUND_call_has_the_background_cap"),
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
     "    all_cmds, carriers_truncated = carried_commands(body, extra, dialects)",
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
     'out.append((r if unres else long_name(join_cwd(d, r)), unres, r))',
     'out.append((r if unres else long_name(r), unres, r))',
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
     "    all_cmds, carriers_truncated = carried_commands(body, extra, dialects)",
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
     "            if t in _FIND_EXEC and i + 1 < len(toks):",
     '            if t == "-exec" and i + 1 < len(toks):',
     "test_execdir_is_a_delete"),
    ("the find -exec verb is NORMALISED",
     "                nxt = _verb_name(toks[i + 1])",
     "                nxt = toks[i + 1]",
     "test_an_absolute_delete_under_exec"),
    ("a mv SOURCE that is a root is a delete",
     '        mv_src += prep(move_sources(s), c_cwd, c_old, operation="move")',
     '        mv_src += []',
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
     "                    out.extend(resolve_variants(toks[i + 1][0], assignments(cmd)))",
     "                    out.append(toks[i + 1][0])",
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
    ("a verb carried in a variable is resolved before the rules see it",
     "            rs = resolve_verb(s, assigns)",
     "            rs = s",
     "test_rm_through_a_variable_still_hits_the_game_root"),
    ("carriers are followed more than one level",
     "    for _ in range(_MAX_CARRIER_DEPTH):", "    for _ in range(0):",
     "test_a_shell_inside_a_shell"),
    ("a filter must actually narrow",
     "    return arg not in _UNIVERSAL", "    return True",
     "test_a_universal_name_glob_is_not_a_narrowing"),
    ("the carrier walk ANNOUNCES its bound",
     "                        return out[:_MAX_CARRIED], True",
     "                        return out[:_MAX_CARRIED], False",
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
     "                for inner in _carrier_parts(i_):",
     "                for inner in [i_]:",
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
     "                r = subst_root_var(r, roots)", "                pass",
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
     "                    out.extend(resolve_variants(toks[i + 1][0], assignments(cmd)))",
     "                    out.append(resolve(toks[i + 1][0], assignments(cmd)))",
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
    # --- F10 (v4.0): a command name the guard cannot identify (variable, substitution,
    # cmd %R%/!R!). One mutant per clause; each target names its own class.
    ('F10: an unknown command name contributes its readings',
     '            readings = dynamic_program_readings(seg)',
     '            readings = []',
     'TestF10DynamicProgramWord.test_the_words_after_an_unknown_name_are_judged_as_a_command'),
    ('F10: a quoted word after an unknown name is read as eval reads it',
     '        for r in (rest, joined):',
     '        for r in (rest,):',
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
     '        if not has_unresolved(subst_root_var(resolve(_verb_token(s), assigns), roots)):' + NL + '            continue',
     '        if False:' + NL + '            continue',
     'TestF10DynamicProgramWord.test_TWIN_a_known_name_given_a_protected_path_is_not_this_rule'),
    ('F10: a segment cut inside a substitution is no command position',
     '    return not any(_span_open(x) for x in toks[:k])',
     '    return True',
     'TestF10DynamicProgramWord.test_TWIN_a_known_name_given_a_protected_path_is_not_this_rule'),
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


def run(work: Path):
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
    p = subprocess.run([sys.executable, "test_hook_facts.py"], cwd=work,
                       capture_output=True, text=True, errors="replace", env=env)
    out = p.stdout + p.stderr
    failed = failed_tests(out)
    ran = re.search(r"Ran (\d+) tests", out)
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


def evaluate_source(source):
    wait_for_dispatch()
    with tempfile.TemporaryDirectory(prefix="vht-case-") as td:
        work = Path(td)
        for f in FILES:
            shutil.copy2(HOOKS / f, work / f)
        (work / "hook_facts.py").write_text(source, encoding="utf-8", newline="")
        started = time.monotonic()
        rc, failed, ran = run(work)
        return {"rc": rc, "failed": sorted(failed), "ran": ran,
                "seconds": time.monotonic()-started}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=range(1,9), default=1)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--sample", type=int, help="benchmark N evenly spaced trials; exit 3, never a release pass")
    args = parser.parse_args(argv)
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

        rc, failed, ran = run(work)
        if rc != 0 or ran < 20:
            print(f"REFUSING: baseline is not green (rc={rc}, ran={ran}, "
                  f"{len(failed)} failed). No mutation result would mean anything.",
                  file=sys.stderr)
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
        if args.sample is not None:
            if not 1 <= args.sample <= population:
                parser.error("--sample must be between 1 and the trial population")
            cases = [cases[i*population//args.sample] for i in range(args.sample)]
        # A GitHub Actions runner is an ephemeral VM running nothing but this job, so the
        # rb admission this guard stands for has nothing to protect there. CI needs the
        # workers: 298 serial trials of a 782-test file were still running 4 h into the
        # 300-minute job limit (ci/v4-rc10, 2026-10-20).
        if (args.workers > 1 and not os.environ.get("RB_PRESSURE_FILE")
                and os.environ.get("GITHUB_ACTIONS") != "true"):
            print("REFUSING: parallel trials require rb.py run (or a GitHub Actions runner)")
            return 2
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            values = []
            for result in pool.map(evaluate_source, [s for _,s in cases]):
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
        print(f"{'MUTATION':<42} {'target test':<12} verdict")
        print("-" * 78)
        for i, (label, old, new, tgt) in enumerate(MUTANTS):
            if pristine.count(old) != 1:
                print(f"{label:<42} {'-':<12} *** DID NOT APPLY "
                      f"({pristine.count(old)} matches) -- the mutant is stale ***")
                bad += 1
                continue
            target.write_text(pristine.replace(old, new), encoding="utf-8", newline="")
            result = results["mutation:"+str(i)]
            rc, failed, ran = result["rc"], set(result["failed"]), result["ran"]
            if ran < 20:
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
    print(f"mutations not caught by their target test: {bad} of {len(MUTANTS)}")
    print(f"predicates with a coverage gap:            {gaps} of {len(keys)}")
    print("  fires?   = predicate pinned FALSE, so a must-FIRE test must break")
    print("  silent?  = predicate pinned TRUE,  so a must-NOT-fire test NAMING it must break")
    return 1 if (bad or gaps) else 0


if __name__ == "__main__":
    raise SystemExit(main())
