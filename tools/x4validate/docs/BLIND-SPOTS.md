# BLIND-SPOTS.md — the narrowing-point register

**Why this file exists.** Three tool defects were found in four days (root-`<replace>` drop 08-08,
nested-patch door 08-11, depth-1 flatten 08-11) and **all three were found reactively** — by pointing
a tool at a new question and noticing the answer smelled wrong. If usage is the discovery method,
"another defect every time we use them" is the guaranteed outcome.

All three share one shape: **a step that narrows the data and reports success anyway.** That is a
searchable property. This register is the result of searching for it directly, and it holds every
narrowing point found — including the ones that turned out to be *correct*, because the value of the
register is that each row has a **denominator**.

## Writing an entry — the format is ENFORCED, and one check fails SILENTLY

Three separate checks read these entries, and they read different parts of them. MEASURED
2026-09-20 while adding F126: getting this wrong cost three round trips, and the first
mistake produced no failure at all.

1. **Claim the id with `scripts/next-blind-spot-id.py`** — never read one from memory or
   from another entry. It consults every branch, because an id claimed in one tree is
   invisible to the others.
2. **Write the summary-table ROW first** (`| F126 | ... |`), then the section. The row is the
   cheapest thing to push and it makes the claim visible to concurrent sessions.
   `test_register_bookkeeping_has_not_drifted` fails on a row with no section.
3. **Put the STATUS and a CONFIDENCE on the `## Fnnn` HEADING LINE**, not on the line below:

   ```
   ## F126 — <one-line summary> · **DEFECT (measured)** · confidence 97% · FIXED 2026-09-20
   ```

   ⚠ **`gates/register_rederivation.py` reads FIXED from the HEADING.** A status on the
   next line does not fail — the entry is simply **omitted from the audit**, and the gate
   still prints "No new findings". The only tell is the entry COUNT, which is why the run
   is worth predicting before you look at it. `test_blind_spots_ids.py` separately requires
   the literal word `confidence` on that line.
4. **A FIXED entry must name what re-derives it** — a `**RE-DERIVED BY:**` line citing the
   exact test functions, not "the suite". That is what `register_rederivation` audits.
5. **State the confidence's residual in words.** A bare percentage in permanent record is a
   number the next session cannot interrogate; say what the missing fraction IS.

Original audit date: **2026-08-12**. Corpus then: `reference\` (vanilla + 6 unpacked DLC), 122
installed extension dirs, 2 packed mini-DLC, store `dev\_registry\effective.sqlite` (rebuilt
2026-08-11 post-nested-door).

**STATE RE-VERIFIED 2026-08-22.** Every entry was re-checked **against the code**, not carried
forward from memory or from a handoff — inheriting a state without measuring it is how a wrong claim
got re-quoted as fact here before. Each row's STATE column cites the line, test or commit that backs
it. Corpus now: **123 installed extension dirs** (115 mods after excluding `ego_dlc_*`), store
rebuilt **2026-08-22** (post-F27), 22,966 entities / 582,107 attr rows.

Not everything could be re-measured, and that is stated rather than smoothed over: **F3's figures
are stale** (the verdict stands; the percentages date from 08-12 and the store has since grown via
F2 and F11). It is the only row in that condition — **1 of 33**.

⚠ The summary table below was itself narrowing: it was missing **six findings outright**
(F21, F25, F26, F28, F29, F30) and **15 rows carried 4 of the header's 5 cells**, so their Status
rendered blank. Repaired 2026-08-22; `tests/test_blind_spots_ids.py` now pins the bookkeeping so
drift fails loudly in either direction.

**Rule this register enforces:** *a tool that returns nothing must be able to say whether that is an
absence or a non-answer.* `tools\basex\ask.py` already does this — it refuses to render a zero
result as a finding without a coverage denominator. `x4effective` and `x4stats` do not, yet.

**Adding an entry.** Claim the next id with `uv run python scripts/next-blind-spot-id.py`, run
from `tools/x4validate`: it reads the committed register on every branch, so two sessions cannot hand
out the same number. Write and commit the summary ROW first, then the section. Never take an id from
memory or from another session -- a remembered id was stale within a day here.

---

## Verdict summary

> **Citation convention.** Prefer `file.py::symbol` over `file.py:LINE`. Line numbers rot the
> moment code moves, and silently: they keep resolving, just to the wrong line. MEASURED
> 2026-08-22 — of six line citations written in a single session, **three were already wrong by
> the end of it**, moved by later edits in the same session, and one pointed at a comment that
> the same refactor had deleted. Line numbers in entries dated before 2026-08-22 are historical
> and were accurate when written; treat them as hints, not addresses.

| # | Narrowing point | Verdict | Measured cost | STATE — re-verified against the code 2026-08-22 |
|---|---|---|---|---|
| F1 | Depth-1 `<properties>` flatten (2 tools) | **DEFECT** | 9,197 of 13,291 attrs invisible; 0 drag rows | ✅ **FIXED 2026-08-12** — `MAX_PROP_DEPTH=8` (`_effective.py:287`, mirrored `_stats.py:167`). ⚠ **live residue:** the 08-22 build reports **10 subtrees** abandoned at the guard |
| F2 | Store indexes 3 kinds only | **UNDOCUMENTED SCOPE** | 29.8% of mod XML; ~182 files / 24 registries | ✅ **FIXED 2026-08-13** (`f293d78`) — `BUILDABLE_KINDS` (`_effective.py:541`) = 18 registries + macro + component |
| F3 | Macro coverage overall | **MOSTLY CORRECT SCOPE** | **RE-MEASURED 2026-08-22 on a post-F34 x4eff:** 2,870 of 7,896 missing (36.3%); **balance-relevant 3 of 1,916 (99.8%)**, galaxy map 21.5%, characters/npc 4.8% | ✅ **SCOPE CONFIRMED, FIGURES CURRENT.** The 3 remaining balance misses were all `escape_pod`'s and were an F37 symptom, not a coverage gap. Cross-check: **0 store names absent from x4eff** |
| F4 | mini-DLC assets invisible | **DEFECT** | 26 of 40 macros (65%), 7 balance-relevant | ✅ **FIXED 2026-08-12** — packed branch, `_effective.py:145-153` |
| F5 | `*_macro.xml` filename filter | **DEFECT** | 4 VRO macros (2 bullets + 2 `_marco.xml` typos) | ✅ **FIXED 2026-08-12** — `_defines_macro` content test (`_effective.py:158`) |
| F6 | `find("macro")` vs `iter("macro")` | **DEFECT (low)** | 2,221 macros; **0** of them ships | ✅ **FIXED 2026-08-12** — `_stats.py:225` iterates |
| F7 | `_compat._mod_deps` silent degradation | **LATENT DEFECT** | 0 of 122 today; corrupts load order if ever hit | ✅ **FIXED 2026-08-12** — `dropped` channel wired at both early returns (`_compat.py:151-175`) |
| F8 | x4similar's `_WEIGHTS` whitelist has no handling keys | **SCORING GAP** | 8 keys of 34 numeric axes; only 35 of 816 pairs are true duplicates | ✅ **CLOSED WONTFIX 2026-08-22.** Score is advisory by design and `difference_profile` already renders handling on every pair. What WAS fixed: the oracle's hand-duplicated weights are now pinned equal (`tests/test_similarity_weights_pinned.py`), so drift fails loudly while the oracle stays independent |
| F9 | `properties.*` duplicate rows (introduced by F1, caught same day) | **DEFECT** | 67,333 rows = 23.1% of the store | ✅ **FIXED before release** — that shape (exact double-emission) is gone. ⚠ a **different** duplicate shape is live: see **F33** |
| F10 | x4xref, x4similar AND `gates/similar_audit` blind to the packed mini-DLC — same root cause as F4 | **DEFECT** | xref 13 MD files → 0 rows; similar 3 ships incl. the Hyperion | ✅ **FIXED 2026-08-12** — `dlc_dirs` threaded; `tests/test_dlc_enumeration.py` |
| F11 | `<component>` definitions indexed by nothing | **DEFECT** | **5,011** components / 97,323 slots. ⚠ naive design **infeasible: 33.1M rows** | ✅ **FIXED 2026-08-13** (`33f3b57`) — `extract_components` (`_effective.py:384`, called `:636`), scoped to identity + connection slots |
| F12 | `find_dangling` does not check `<macro ref>` | **DEFECT** | 15,002 occurrences; corrected yield **0 dangling** | ✅ **FIXED 2026-08-13** (with F14) |
| F13 | Lua invisible to every tool | **STATED EXCLUSION** | 53 files / 29 mods | ⏭ **STATED EXCLUSION** — still not parsed. Nuance verified 08-22: `.lua` members ARE read as *source lines* for the `Lua_Loader` check only (`_scan.py:242`) |
| F14 | reference checks only see `<add>` ops in `<diff>` files | **DEFECT** | **1,625 full files / 56 mods — 38% of mod XML — unchecked** | ✅ **FIXED 2026-08-13** — explicit full-file branch (`_check.py:992-997`) |
| F15 | `collect_text_defs` reads a hardcoded **2-entry** `TEXT_FILES` list | **DEFECT** | 1 mod's own 56 strings absent ⇒ **44 FALSE gating errors** | ✅ **FIXED 2026-08-13** — `sorted(rels \| set(TEXT_FILES))` (`_check.py:59`) |
| F16 | `ware_refs` treats script EXPRESSIONS as ids | **DEFECT** | 183 of 236 unresolved @ware refs were false; predicate matches **0 of 2,462** real ids | ✅ **FIXED 2026-08-13** — `_EXPRESSION_CHARS` (`_refs.py:48`) |
| F17 | the GATING scope checked `<component ref>` against the MACRO index only | **DEFECT** | **23 FALSE gating errors** on the live modlist (`standardzone`, `standardregion` are components) | ✅ **FIXED 2026-08-13** — namespace-agnostic union (`_check.py:157`, `:277`) |
| F18 | a BaseX index could not say WHEN it was true | **DEFECT** | x4eff served pre-fix values for 11 days; **140 of 194 (72%)** engine thrust rows wrong | ✅ **FIXED 2026-08-13** — two-axis fingerprint (`_freshness.py`) + banner |
| F19 | `ask.py --db` and the query's own `collection()` could disagree | **DEFECT** | answer scored against the WRONG database; a stale x4eff warned nothing | ✅ **FIXED 2026-08-13** — `--db` + `coverage-<db>.json` (`ask.py:102`) |
| F20 | a SIXTH hand-rolled DLC list, in a directory the guard never scanned | **DEFECT** | correct today; wrong the day a DLC ships, with nothing to say so | ✅ **FIXED 2026-08-13** — derived from Config; `tests/test_dlc_enumeration.py:83` |
| F21 | the SAME staleness hole in the store and the xref index | **DEFECT** | an artifact reported success indefinitely after the world moved | ✅ **FIXED 2026-08-13** — `_xref.py:324` stamps, `:345` compares |
| F22 | `check_variant_consistency` enumerated LOOSE FILES ON DISK — in all THREE of its lookups, incl. the mod under test | **DEFECT** (was filed as a scope gap costing 0) | **378 variant files; only 14 (3.7%) were reachable, 364 (96.3%) invisible** because a PACKED mod under test yielded nothing. Post-fix: 378 examined, **3 real findings, all `vro`** | ✅ **FIXED 2026-08-22.** ⚠ The old row said *"cost 0 today"* — that was measured over the wrong population. `iter_diff_files` (same file, `:529`) was repaired for this identical defect on 2026-07-26 and the fix was never carried across |
| F23 | `parse_debug` dropped every log line it could not classify, and reported the classified count as the log's TOTAL | **DEFECT** | **1,067 of 2,430 (43.9%)** silently discarded; the SVE job-spawn finding sat in the discarded part | ✅ **FIXED 2026-08-13** — `unclassified` channel (`_debugcli.py:207`); coverage 56.1% → 89.5% |
| F24 | `_check_ops` was a SECOND implementation of op-application, evaluating against the PRE-MOD tree | **DEFECT** | **1,206 false-positive findings** (engine-confirmed) + 6 real ones missed | ✅ **FIXED 2026-08-13** — derives from `apply_diff` (`_check.py:606`) |
| F25 | reported: "x4compat's collision winner is wrong" | **NOT A DEFECT** | 0 — x4compat was correct; its wipe counts matched the victim's real ops per file (5 / 3 / 1) | ✅ **CLOSED 2026-08-21** — a LAYER MISMATCH in the inbound report, not a defect. See the landmine note inside the entry |
| F26 | `gates/cross_tool.py` verified ONE collision kind of five | **DEFECT** | 14 of 445 collisions (**3.1%**) checked; HARD/SUBTREE/UNION-KEY never verified against the store at all | ✅ **FIXED 2026-08-21** — per-kind assertions; FULL-OVERRIDE 14/14 · HARD 40/40 · UNION-KEY 2/2 · SUBTREE 148/148 · NAME-CLASH 20/20, **0 disagreements** |
| F27 | `md/` scripts treated as vpath-override, like assets — the engine keys its script registry on FILENAME, so a duplicate is INERT | **DEFECT** | our model returned a mod's 44-line file while the engine ran vanilla's 1,795-line `Setup`; **1** affected vpath of **299** | ✅ **FIXED 2026-08-22** — `_SCRIPT_REGISTRY_DIRS=("md/",)` → `script(inert)` (`_merge.py:476`). Mechanism proven by controlled experiment; `aiscripts/` left untested **with a tripwire** |
| F28 | a known-partial checker was presented as a completeness GATE | **DEFECT** | ship/module completeness compares the `<ware>` wrapper only — the macro interior was never checked, silently | ✅ **FIXED 2026-08-21** — declares the gap through the skip channel (`_check.py:1281`) |
| F29 | the packed-only trap was documented six times and kept happening | **DEFECT** | `_cat.mod_vfs` returns `{}` for a loose mod and says nothing; one scan read 2,681 files instead of 4,401 | ✅ **FIXED 2026-08-22** — `packed_only=` + `# packed-ok:` AST guard (`tests/test_no_packed_only_scan.py`). ⚠ its first form **flooded ~15×/run**; de-flooded by acknowledging at `_cat.read_path` |
| F30 | SUBTREE's `winner` named the WIPER, and callers read it as the live-value owner | **DEFECT (breaking)** | 445 rows; `winner` changed on exactly **148** (all SUBTREE). A wipe undone by a later mod = **3 of 148 (2.0%)** | ✅ **FIXED 2026-08-21** — `wiped_by` + `live_value_owner()` (`_compat.py:63`). **Breaking output change — see CHANGELOG 2.3.0** |
| F31 | `<module group=>` was checked by nothing — `modulegroups` unindexed, `module/@group` unknown to `_refs` | **DEFECT** | the engine rejected 3 references 43× per launch while we reported the mod clean; 146 groups, 22 refs, **3 dangling in 1 of 115 mods** | ✅ **FIXED 2026-08-21** — 21st registry + `check_module_groups` (`_check.py:1177`); engine-verified |
| F32 | a dev-only VARIANT is validated against a tree containing its own ops — Tier B excludes by folder ∪ id, and a variant matches neither | **SCOPE LIMIT** | 8 false alarms on one mod; its installed twin reports 0 | 📋 **SCOPE LIMIT 2026-08-22** — documented, deliberately not "fixed" |
| F33 | a NON-UNIQUE key read with a SINGULAR read — duplicate `(kind,name)` entities, and a non-unique bracket discriminator in prop keys | **LATENT DEFECT** | attrs: 627 groups / 1,071 extra rows, **201 diverge** → **0**. entities: 63 groups / 90 extra rows, **23 diverge** — still open | 🟠 **ATTR AXIS FIXED 2026-08-22** (collision-only `[ident#n]`; pinned at 0). Per item: 582,107 attrs and 22,966 entities unchanged, **entity+value multiset 0 differences**, 1,153 rows changed prop only. **ENTITY AXIS DELIBERATELY OPEN** — `index/macros.xml` decides, not load order (gotcha #18) |
| F34 | `build-effective.py::all_vpaths` walked `reference/` LOOSE-ONLY, so x4eff never saw the packed mini-DLC | **DEFECT** | **119 of 142 mini-DLC documents (84%) absent**; the 23 present arrived incidentally, via two unrelated mods nesting patches under `extensions/ego_dlc_mini_0X/` | ✅ **FIXED 2026-08-22** — `_effective.base_vpaths()` (loose THEN packed) + packed materialization in the copy branch. **142/142** after rebuild. SEVENTH occurrence of this shape; now guarded by `tests/test_no_loose_only_reference_walk.py` |
| F35 | x4eff coverage took its denominator FROM THE ARTIFACT IT AUDITS (`documents_total`, written by the same build) | **DEFECT** | structurally blind: **0 of 210** enumerated failures were mini-DLC, because a vpath never attempted cannot fail. Printed COVERAGE COMPLETE while missing 119 documents | ✅ **FIXED 2026-08-22** — the manifest now records the SCANNED SOURCE SET (9 sources, counts, loose/packed) and coverage fails if any contributed zero. Proven to fail: rc=4 on a zero-contribution source and on a pre-contract manifest |
| F36 | `ENGINE_SOURCES` fingerprinted the MERGE modules but not the ENUMERATION ones | **DEFECT** | `_effective.py`/`_registry.py` decide which documents and which mods exist, yet editing them left the store reporting FRESH. **Near-miss the day it was found:** F34's fix edited `reference_vpaths` and `claims_audit` returned 21/21 green against a store built by the OLD enumeration | ✅ **FIXED 2026-08-22** — both added; dead duplicate list in `staleness.py` deleted. Correct only by luck before: set-equality had been proven BY HAND (4,002 / 7,551 vpaths, 0 ±) |
| F37 | "the mod list" was TWO different sets — engine-loadable vs on-disk — with no name for either | **DEFECT** | 13 call sites: 5 right, 3 defensible-but-silent, **4 wrong, all the same way**. With ONE disabled mod (`escape_pod`, 19 files): x4eff carried 3 macros as live, x4compat listed it in 4 collision rows, **Tier B would resolve a selector against it and report OK — a false pass** | ✅ **FIXED 2026-08-22** — `_registry.mods(scope)` with `scope` positional and required; every site names it with a reason; `tests/test_mod_scope_is_explicit.py` + a store-vs-x4eff agreement gate. Compat re-captured: 445→445, same 4 rows minus escape_pod, **0 winner changes**. Tier B output **identical** on the 6 highest-overlap mods |
| F38 | the test runner's own population: 23 BaseX tests existed, passed, and were NEVER COLLECTED | **DEFECT** | `testpaths = ["tests"]` excluded `tools/basex/`, so the suite printed "593 passed" while saying nothing about 23 tests covering `ask.py` (gates every negative claim) and `staleness.py` (the freshness contract) | ✅ **FIXED 2026-08-22** — `tests/test_basex_tests_are_not_orphaned.py` runs them as a subprocess and SKIPS with a reason when the dev-only tooling is absent, so a public clone still passes |
| F39 | the reference tree fell back to a CWD-relative guess, so an unconfigured run reported the whole base game missing AS MOD ERRORS | **DEFECT** | `REFERENCE = _paths.reference() or Path("reference")`. Cold, `x4validate` exited **1** ("your mod is broken") when the truth was "your toolkit is not set up". Blast radius measured before changing anything: **153 `Config(` sites / 47 files / 31 bare**; **12** tests pass a deliberately non-existent reference, which set the fix's boundary | ✅ **FIXED 2026-08-23** — refusal on *unresolved*, never on "named but absent"; `Config.__post_init__` resolves at construction; all **9** entry points wrapped by `_paths.refuses_unconfigured` → **exit 2**. `tests/test_unconfigured_refusal.py` asserts every pyproject entry point carries it; `scripts/verify-cold.sh` runs all 9 cold |
| F40 | configuration read from `os.environ`, bypassing the layered resolver | **DEFECT** | `_nexus` could not see a key in `.claude/x4-paths.env` — the placement our own `setup.sh` documents. `_effective` bound `X4_EFFECTIVE_DB` at **import** into an argparse default while `gates/_env.py` resolved the same var through `_paths`: **two doors to one question** (F30's shape) | ✅ **FIXED 2026-08-23** — `_paths.value()` / `path_value()`; the split matters because `_pick()` path-translates and would silently corrupt a credential. Guarded by an **AST** scan: grep returns 8 hits, **4 inside the docstrings explaining the rule**; AST returns the 4 real ones. Nexus key deliberately stays optional |
| F41 | shipped user-facing scripts guessed a game dir, or reported "not configured" as exit 1 | **DEFECT** | `generate-baseline.sh` defaulted `GAME_DIR` to `$(pwd)` — a **recovery** artifact silently snapshotted from the wrong install; `bin/unpack-reference.sh` refused with exit 1, colliding with "it ran and failed" | ✅ **FIXED 2026-08-23** — both exit **2** and name what to set; verified by execution with every `X4_*` cleared. Population probe: CLIs and gates were already clean, **every defect was a script that hand-rolled its own resolution** |
| F42 | a module-level gate import took 24 UNRELATED tests down with it, and the summary showed it as 2 skips | **DEFECT** | suite collects **619** warm and **595** cold; the missing 24 are pure-logic tests (collision semantics 15, claims parsing 9) that need no X4 install. F38's shape one level up — and quantitatively misleading, since a module-level skip collapses N tests into one line | ✅ **FIXED 2026-08-23** — both gates resolve configuration on first USE, not at import; cold collection now equals warm. Two traps caught by RUNNING: PEP 562 `__getattr__` does not serve a module's own globals (both gates broke with `NameError`), and an accessor must go via `sys.modules[__name__]` or it bypasses the test's monkeypatch — 21 real rows where the fixture wrote 1 |
| F43 | the freshness CONTENT axis cannot see a mod being ENABLED or DISABLED | **DEFECT** | `_freshness.hash_content` hashes each extension dir name + its own `content.xml` mtime/size + a reference marker. It never reads the PROFILE `content.xml` enable-list — one of the three inputs to `_registry.mods("active")` (gotcha #24). Toggling a mod in-game rewrites the PROFILE manifest only, so every mod's own manifest is untouched and the hash CANNOT move | ✅ **FIXED 2026-08-26** — MEASURED on two archived logs 9 h apart: identical content fingerprint `01639715767615e2`, yet the engine loaded **66 vs 67** mods (delta = exactly `amphitrite`). The vector now carries `enabled_in_profile` per mod, joined by **manifest id, never folder name** (gotcha #30b), defaulting to **True** for a mod the profile has never seen (#30a — inverting that silently drops 54 of 115). Confirmed against the live profile: it reports exactly one disabled mod, `escape_pod`, matching the independently-recorded ground truth in `tests/test_profile_is_a_decision_log.py` |
| F44 | the env-resolution guard was blind to a hardcoded absolute-path LITERAL | **DEFECT** | `tests/test_env_resolution_is_delegated.py` detected only `os.environ` / `os.getenv`, so `tools/basex/stage.py:42`'s `C:\Program Files (x86)\Steam\...\extensions` default sailed through — on any other machine it stages ZERO documents and exits 0. `docs/TRUST.md` row 10 cited that very test as what bans the shape, so the shipped trust document was **overclaiming** | ✅ **FIXED 2026-08-24** — guard extended to location literals (MEASURED: 61 production files, **1 hit, 0 false positives**) and it gates. Three more of the same family fell out, all verified by EXECUTION on a proven-cold checkout: `stage.py` and `staleness.py` returned a raw traceback and **rc 1** ("your thing has findings") where the truth was "not configured" |
| F45 | `apply_diff` is **O(n²) in ops-per-file** | **LIMITATION (accepted)** | doubling ops costs **2.8→3.7×**; cliff at **~32k ops**. Worst real file in the corpus: **1,443 ops (~0.03 s)** — **~22× headroom** | ⚠ **OPEN, deliberately NOT fixed.** The fix touches selector evaluation — the path the release exists to make trustworthy. Re-measure if any file approaches ~10k ops |
| F46 | `coverage.py` took its denominator from the CURRENT DIRECTORY when roots defaulted to `""` | **DEFECT** | `Path("")` is `Path(".")`, so a bare run counted the XML wherever you were standing, called that `expected`, and wrote it. MEASURED: rewrote `expected` **13,684 -> 2,822** and DROPPED the fingerprint; also silently flipped a unit test that read the artifact | ✅ **FIXED 2026-08-24** (parallel v2.6.0 session, `2ae4e7f`) — refuses with exit 2 and names the missing argument; the `--eff-manifest` path returns earlier and is unaffected |
| F47 | BaseX checked NO precondition, so every first-run failure blamed the wrong component | **DEFECT** | REPRODUCED, all three: no JVM → `ask.py` printed `BaseX query failed: [WinError 2]`, blaming BaseX for a missing Java and not even naming the file; no jar → `Could not find or load main class`; **DB never built → `[FODC0002] Resource '…x4raw' not found`, and the one line that says "run build-corpus.sh" sits on the ZERO-RESULT path an unbuilt DB can never reach**. `build-corpus.sh` ran `stage.py` to completion (`:49`) before touching java (`:54`), spending the whole staging pass before it could fail | ✅ **FIXED 2026-08-24** — one shared `preflight.py` (java/jar/db/uv/disk), refusing with **rc 2** not rc 1. Java floor **17** read from the jar (`Build-Jdk-Spec: 17`, bytecode major **61**), not chosen. 22 tests, each check exercised in BOTH directions |
| F48 | `build-effective.sh` reported a CRASHED build as success | **DEFECT** | MEASURED by crash-injection on the real script: builder exited non-zero → run **continued**, indexed a **leftover tree from a previous build**, reconciled the new DB against the **PREVIOUS run's manifest**, stamped it **FRESH**, and exited **0**. Two swallows compounding — `\|\| echo` at `:24-25` defeating `set -e`, and `\|\| true` at `:57-58` discarding the coverage verdict `build-corpus.sh` has always propagated | ✅ **FIXED 2026-08-24** — stale tree + manifest deleted BEFORE building; rc 3 (legitimate partial success, manifest already written) continues; any other non-zero aborts; a missing manifest aborts; coverage rc propagated. Verified in all three directions, including that the **valid** partial path still continues |
| F49 | the guard against ORPHANED BaseX tests was blind to a **new** orphan | **DEFECT** | `TEST_FILES` is a hand-maintained tuple and the tripwire pinned only a **shrinking** set. Adding `tools/basex/test_preflight.py` created **22 tests that passed by hand and were collected by nothing** — the precise orphan the module exists to prevent, reintroduced through its own blind spot. A guard that catches removals but not additions takes its denominator **from itself** | ✅ **FIXED 2026-08-24** — now discovers `test_*.py` on disk and fails on any unlisted one. Proved falsifiable in a hermetic copy. BaseX suite 26 → 48 |
| F50 | `perf_guard` could not tell a SLOW run from a SUSPENDED machine | **DEFECT** | `time.perf_counter()` advances while Windows sleeps, so a sweep left running overnight charged the whole suspend to whichever mod was timing. MEASURED: reported `bh_shader 2.71s -> 2210.88s` (**814.9×**) and **PERF REGRESSION — investigate before shipping**; the same mod re-timed at **3.47s** minutes later. Confirmed independently in the event log — Kernel-Power **131, ResumeCount: 3**, over an 18-hour wall-clock window for a sweep using well under an hour of CPU. **A timing spanning a suspend is a NON-ANSWER rendered as a finding**, and it would have blocked a release on a phantom | ✅ **FIXED 2026-08-25** — a suspected regression is now **re-timed once** and reported only if it reproduces; one that cannot be re-timed is reported **UNCONFIRMED** and still fails, because "could not check" is not "not a regression". Re-timing assumes nothing about whether a given clock advances across sleep, and costs nothing on the normal path. 4 tests incl. the falsification twin |
| F51 | a BaseX build SUCCEEDED and was then reported as never built | **DEFECT** | `.basexhome` is a 0-byte marker in the upstream BaseX archive, and it is what tells BaseX that a directory is its home. The vendoring plan listed the files to ship by size and licence and never asked what each one *did*, so it shipped `BaseX.jar` + `LICENSE` and left the marker behind. MEASURED both directions, same jar, same command: **with** the marker `CREATE DB` writes to `<dir>/data`; **without** it BaseX relocates its entire home to `$HOME/basex` and builds there — successfully, and queryably (`collection()` returned the right answer in both). `preflight._dbpath()` then falls back to `<dir>/data`, finds nothing, and prints *“the 'x4raw' database has not been built — Run: bash build-corpus.sh”*. **A new user's first run would be: run the multi-minute build, then be told to run the build**, with no way out — in the preflight written specifically to make first runs good | ✅ **FIXED 2026-08-25 before shipping** — the marker is vendored, and `preflight` now checks for it and explains the relocation rather than only naming a missing file. 2 tests, both directions |
| F52 | a per-mod schema note goes SILENT about what it did NOT check, whenever it checked anything | **SCOPE** | `_check.check_effective_schema` builds its "N declaring no schema / N brand-new" detail under `if not checked and (new_files or no_schema)` — so the reason is emitted only when **zero** files were validated. A mod that validated one file and skipped two reports "1 merged data file(s) validated" and says nothing about the two. MEASURED 2026-08-25 on three mods deployed the same day: `zzz_personal_overlay_C` correctly says "0 validated — 2 declaring no schema", while `Synthetium_Music` says "1 validated" and stays silent about its other two eligible files. Consequence: attributing a `schema_sweep` pairs delta cannot be read off the notes and has to be re-derived per mod. The guard exists for a real reason — it was added because `if checked:` made "validated 0 files" and "did not run" look identical — but it over-corrected from silent-on-zero to silent-on-any-success | ✅ **FIXED 2026-08-26** — the detail is now emitted whenever `new_files or no_schema`, regardless of `checked`. **The register's own rule is that a step which narrows the data announces it even when it also succeeded at something.** E2E on the mod it was measured against: `Synthetium_Music` now reports "1 merged data file(s) validated … — 2 declaring no schema" where it previously said "1 validated" and stayed silent. ⚠ The `effective-schema: {checked} ` prefix is load-bearing — `gates/schema_sweep.py:427` parses it with `int(note.split()[1])` — so the detail is a SUFFIX; gate re-run confirms 178 pairs, unmoved |
| F53 | the freshness fingerprint is NOT PORTABLE across trees, because it hashes LINE ENDINGS | **SCOPE LIMIT** | `hash_engine()` hashes the raw bytes of the 7 ENGINE_SOURCES, so the SAME commit hashes differently in two checkouts. MEASURED: dev working tree `73517fd69c0c1aad` vs `git archive HEAD` of that commit `490e9a6a64909fa1`, all seven byte-identical after `--strip-trailing-cr`. Mechanism (measured via `git ls-files --eol`): `core.autocrlf=true`, no `.gitattributes`, index `i/lf` for all seven; three files are still as checked out (CRLF) and four were REWRITTEN IN PLACE by a tool emitting LF. Cost: a stopped 26-gate sweep and a wrong first diagnosis | ✅ **FIXED 2026-08-26** - failed SAFE (false STALE, never false FRESH), which is why it went unnoticed; but the message ASSERTED that the merge code was edited when nothing was, and a false REASON is a misleading answer where a false STALE is only a non-answer. `.gitattributes` pinning `*.py text eol=lf` landed in `bf1e191`; the WORKTREE renormalise landed 2026-08-26, converting 48 tracked files CRLF->LF. VERIFIED: the six untouched `ENGINE_SOURCES` are now byte-identical to HEAD, so this tree hashes like a fresh checkout. Announced as one deliberate staleness event together with the F57 content-axis change |
| F54 | a MUTATION WINDOW can turn a previously-sound assertion VACUOUS, with nobody editing the test | **HAZARD** | `gates/mutation_probe.py` edits `x4validate/_merge.py` in place. One of its mutants replaces `if len(targets) > 1:` with `> 99999`, disabling ambiguous-`sel` detection. Any assertion of the form `assert op.ambiguous is False` is then **VACUOUSLY TRUE — it cannot go red**, and it reports PASS exactly as it did when it was meaningful. MEASURED 2026-08-25: a verification script asserting `ambiguous=False` on 3 ops ran while a sweep was mutating the dev tree; re-run on the clean tree it gave identical results, but during the window that particular assertion proved nothing. Distinct from the existing vacuous-assertion entry, which is about a test WRITTEN vacuously (`xpath(...) != ["999"]` passing on `[]`): this is a **sound test MADE vacuous by its environment**, so reading the test teaches you nothing and only the environment betrays it | ✅ **FIXED 2026-08-27** — both mitigations mechanized. **(1) The window announces itself**: `mutation_probe` writes a marker, the CLIs banner on reads and **refuse with rc 2 on any write**, and a killed probe restores from a pristine copy and exits 2 rather than leaving a poisoned tree (F59). **(2) Coverage 1 module → 6, 15 mutants, `killed 15/15, survivors 0, hangs 0`** — every detector named here is now PROVABLY killable rather than assumed so. New mutants target the bare-vs-nested door (#6/F19), alphabetical load order (#13), depth-1 property truncation, and the skipped packed half (F1). ⚠ Honest scope: the hazard cannot be REMOVED — a probe must mutate — so this makes it visible, crash-safe and announced, not absent. Superseded plan: |
| F55 | `corpus_sweep` PRINTED the evidence of 173 dead runs and passed anyway | **DEFECT** | Crash detection was a Traceback substring plus a subprocess timeout. A process killed by the Windows LOADER never starts Python, so it emits no traceback and no output at all. MEASURED from the gate's own log: `tier a exit 3221225794: 52` + `tier b exit 3221225794: 121` = **173 of 242 invocations (71.5%)** dead with `0xC0000142` STATUS_DLL_INIT_FAILED, while the gate printed **CRASHES/HANGS: 0** and returned **0**. Corroborating tell: that run took **193s** where a healthy run takes **1219s** | FIXED 2026-08-25 - any code outside the documented {0 clean, 1 findings, 3 skipped-work} is a crash. 5 tests incl. the falsification twin. The fix provably does not change the verified run: every code there was in {0,1,3} |
| F56 | the freshness fingerprint says THAT the world moved, never WHAT moved | **SCOPE** | `_freshness.hash_content` folds 129 folders into ONE 16-char digest. When it changes, every consumer can say "content changed: a mod was added, removed or updated" and nothing more — the per-folder inputs (name, manifest mtime, size) are consumed and discarded. MEASURED 2026-08-25: localising a single changed mod took **nine investigative steps** and three false leads (set-diff of installed vs indexed mods: identical 120/120; `find -newermt`: nothing since 17:10 — structurally blind to a BACK-DATED write; folder count: unchanged 129). It was only solved because the mod happened to bump its `version`, which the sqlite `mods` table records. **An mtime-only rewrite with identical content would have been undiagnosable from our artifacts.** Ground truth: `distances` (ws_3764127388) rewritten 22:40:41 with all 26 files back-dated to 08-23 15:54:39 | ✅ **FIXED 2026-08-25** — `content_detail()` computes the per-folder vector, `hash_content()` folds it through the single `_fold()`, and it is persisted as a third `meta` row (149 KB / 121 mods / 2,042 files). `diff_detail()` localises by set-diffing two vectors and RAISES `NoBaseline` against a vectorless baseline rather than reporting "nothing changed". Triples are hashed as a SET, not `max(mtime)`, because back-dating is what defeated `find -newermt`. ⚠ Residual, NOT fixed: **NTFS tunneling is ON at OS default**, so a delete+recreate of the same name within ~15 s PRESERVES CreationTime; the tunneling-proof source is the **USN journal**, which requires Administrator and is therefore an advisory rung only |
| F57 | the CONTENT axis could not see a mod's FILES change at all | **DEFECT** | `_freshness.hash_content` stat'd ONLY each folder's `content.xml`, so a mod whose files changed while its manifest did not was invisible — the ordinary overlay-deploy workflow. MEASURED 2026-08-25: **72 of 121 non-DLC mods (59.5%)** have ≥1 file newer than their own manifest, and the pre-fix code returned the IDENTICAL digest `03122df005f47fbd` before and after a file-only edit. Every artifact reported FRESH while describing a different tree — a false **FRESH**, failing in the UNSAFE direction, unlike F53 | ✅ **FIXED 2026-08-25** — per-mod `tree_sha` over `(relpath, mtime, size)` for `.xml/.cat/.dat/.lua`; **0.060 s** for 2,042 files. The suffix filter is correctness, not speed: 8 mods would else churn the hash on README/LICENCE edits. Scope limit declared: a same-size same-mtime edit is still invisible, and a same-size edit reads as `touched` not `content` |
| F58 | a capability that EXISTED, was correct and was in `--help` — and a gap was filed against it | **ROUTING** (no tool misbehaved) | `x4effective dump --chain <vpath>` answers existence (rc 0/1), SOURCE, and supply MODE (`vro:full` vs `base, ego_dlc_x:diff`). A throwaway script hand-rolled a base-only walk instead and labelled **65 of 241 vpaths (27%)** *"paths Egosoft renamed"*. Egosoft renamed nothing. VERIFIED INDEPENDENTLY: all three files the claim named (`missile_cruise`, `missile_heavy`, `turret_multilauncher`) return `vro:full`, as do the six X3-named missiles — 9 files checked. **The direction is the danger**: "renamed" reads as INERT/deletable, when those files actually WIN their vpaths | ✅ **FIXED 2026-08-26** — routing row added to CLAUDE.md's Discovery-vs-Proof table. **Deliberately NO new helper**: `_effective.base_has` was added 08-25 labelled PREVENTIVE for this exact trap and has **ZERO production callers** — the next script did not use it one day later. A banning test was also rejected: the failure was in a throwaway script, which no linter covers |
| F59 | a MUTATING gate's restore lived only in `finally` — which does not run on SIGKILL | **DEFECT** | `gates/mutation_probe.py` edits `x4validate/_merge.py` in place and restored it in a `finally:`. A killed probe therefore left a MUTATED SOURCE on disk, and that file is TRACKED, so `git status` shows an ordinary modification. v2.5.0 shipped exactly that way once (`> 99999` instead of `> 1`, ambiguous-`sel` detection silently off in a public release). Not hypothetical: MEASURED 2026-08-25, a `TaskStop` that REPORTED SUCCESS but did not stop left three gate sweeps racing, so the standing 'never run a mutating gate against a tree in use' rule can be broken BY ACCIDENT. REPRODUCED 2026-08-26 by a real kill: `_merge.py` left at `if len(new_children) != 1 and False:` — the guard disabled | ✅ **FIXED 2026-08-26** — byte-for-byte pristine copies + a `.mutation-probe-active` marker written BEFORE the first mutation. A killed run makes the next invocation REFUSE with **exit 2** naming `--recover`, which restores and says WHICH file was mutated. Recovery is explicit, never automatic. 14 tests, and the crash path proven by ACTUALLY killing a run, not by calling `recover()` |
| F60 | a file-by-file port SPLIT a commit and shipped half of it | **DEFECT** | Porting one file at a time has no notion of a commit. `f5c976d` touched five files; **two crossed over, three did not** — and the two crossed ONLY because the porter had independently edited them, which is not a selection rule anyone chose. Public `master` therefore shipped `_freshness.py:509` telling users to *"run `x4modlist changed`"* while `_modlist.py` had no such subcommand and `_changed.py` did not exist. MEASURED 2026-08-26 over 137 dev-tracked files: 116 identical, **12 real diffs, 5 unexpected dev-only**, 0 mirror-only — and **40 more differing only by line endings**, which is why the documented `diff -rq` proof reported 52 differences where 12 were real. ⚠ NOT on a release: `v2.6.0` has zero mentions, `HEAD` has two | ✅ **FIXED 2026-08-26** — `scripts/verify-port.py` (dev-only): six buckets that must SUM to the population, **committed blobs** not working-tree files, allow-list as a literal with a reason per entry (an unlisted dev-only file is a FINDING), plus the mirror's identifier matcher over the port subset. `--selftest` **7/7** on four planted defects. `QA-PROCESS.md` rule 2 rewritten. Honest limit: it catches an unfaithful port, it does not make porting atomic |
| F61 | the identifier scrub has no dev-side control, so it regressed in 25 hours | **PROCESS** | `2b2b89a` (08-25 15:36) genericised three personal overlay folder names out of the mirrored `gates/schema_sweep.py`; MEASURED 08-26, **8 lines across 2 files** had them back (`de21b8a`, `f5c976d`). ⚠ Severity stated honestly: the guard **would** have caught it — EXECUTED, `scan-identifiers.py` matches both lines and misses two benign controls, so public CI goes red. The finding is that the only control fires **POST-PUSH**, so the same regression can be written twice locally with nothing objecting. Found alongside: a 74-row per-mod table in the same mirrored file naming one person's whole modlist — public copy already names **44 of 129** installed folders, porting unchanged would have made it **79** | ✅ **FIXED 2026-08-26** — `verify-port.py` runs the matcher over the port subset BEFORE anything is copied; falsifiable, not assumed: **9 hits** on the pre-scrub bytes, **0** across 133 files now. Table moved to `AUDIT-2026-08.md` (dev-only), delta down to **+3**, all public third-party mods in load-bearing attributions. Letter→folder key recorded once there, so the scrub is lossless |
| F62 | a silent existence-filter narrowed a mutation scope; the test that should have caught it asserted `any` | **DEFECT** | `mutation_probe.run_tests` dropped missing test paths before invoking pytest — right to DO, wrong to do SILENTLY, and inside the one gate built to detect vacuous assertions. MEASURED: the mirror's `_registry.py` scope lists 4 files and 3 exist, so the public probe ran **3 of 4** and printed the same `killed` line. Two more in the same function, both in dev too: an empty scope returned the verdict **`"hang"`** (an absence rendered as a timeout that was never measured, which the caller then "confirms"), and `test_every_target_has_tests_that_exist` asserted **`any`** while its NAME promised all — three of four present passed. ⚠ A prediction of mine that was WRONG is recorded in the section: I expected a false KILLED via pytest error; the filter runs first | ✅ **FIXED 2026-08-26** — a partial scope NAMES what it dropped; an empty one returns the new verdict **`noscope`**, own bucket, excluded from the killed count, fails the gate; an empty BASELINE returns **rc 2**, never rc 1. `mp_scope_gaps()` returns named gaps, asserted with `all`. Asymmetry is the proof: GREEN on dev, RED on the mirror naming the exact file. 6 tests written first and watched fail; probe re-run **11/11 killed, 0 unmeasured** |
| F63 | a helper that cannot say *"there is no extensions root"* | **DEFECT** | `_effective._ext_root` returns `_registry.GAME_EXTENSIONS` behind `except AttributeError` — a guard that NEVER FIRES, because the attribute exists and its VALUE is `None` when nothing is configured. `_freshness.fingerprint` then refused (correctly — that is F46), unable to tell *"the caller forgot"* from *"the caller looked and there is none"*. MEASURED 2026-08-26: **5 tests fail on any machine with no X4 installed** — every fresh clone, every CI runner — and public CI was **RED for two consecutive runs**, byte-identical to `verify-cold.sh`'s output. ⚠ Nobody looked; the handoff said CI had not run. NOT user-reachable: the cold CLI matrix shows `x4effective` refusing with exit 2 at the door. **Symptom 2, OPEN:** warm, `_ext_root` returns the REAL extensions path for a store built over throwaway test dirs, so that store is stamped with a world it was never built from | ✅ **FIXED 2026-08-27** — symptom 2 closed: `_ext_root` returns None when the config describes a DIFFERENT world, so a test-built store is stamped UNKNOWN rather than with the real game. Population re-derived: 2 call sites, **4** test functions (the register said 5), 3 production consumers all passing `config=None`. Symptom 1 was `fingerprint(config, extensions=_UNSET)`: omitted still RAISES (F46 intact), an explicit `None` records the content axis **UNKNOWN**. `compare()` returns NOT fresh whenever either side is UNKNOWN — `None == None` would otherwise make two unknowns MATCH and report **FRESH**, and the test failed with exactly `Verdict(fresh=True, reasons=[])` before the fix. `_freshness.py` is NOT an `ENGINE_SOURCE`, so the hash held at `b25a2a99853d4c84` and nothing needed rebuilding. Symptom 2 left OPEN on purpose: `_effective.py` IS an engine source and that fix needs its own planned rebuild. Cost today genuinely zero, population stated (2 call sites, 5 tests) so it can be re-derived rather than trusted |
| F64 | `origin` / `chain` records WHO WON, never WHO INTRODUCED IT | **ADJACENT-ANSWER** (no tool misbehaved) | A root `<replace sel="//macros">` swaps the whole document, so the base contributes **no chain entry** — and that is VRO's dominant idiom (848 root-replaces, CLAUDE.md #10). A single-entry chain then reads as *"this mod introduced this"* when it usually means *"this mod re-supplied what vanilla already had"*. PEER-MEASURED over the store: of **35,423** single-op root-replace attributes, **23,182 (65.4%)** also exist in vanilla with the chain hiding it; only **39** vpaths are truly mod-added. Sharpest case `bullet_arg_m_ion_01_mk1_macro`: **vanilla 10, live 10, chain `[vro]`** — identical value, sole credit, no hint a base file exists. It cost a real design conclusion (*"VRO added Kha'ak shield disruption"* — false). ⚠ **The same shape is `x4compat.Collision.winner`** (#18/F25): winning and originating are different questions, and no tool says which it answers | ✅ **FIXED 2026-08-27** — `who-sets` and `dump --chain` now disclose when base+DLC ALSO supply the vpath. Scope is EXISTENCE not the vanilla value, on purpose: reporting the value means re-deriving the property flatten, and a second normaliser implementation is what made the original check report 2.6% where the answer was 65.4%. First production caller of `base_has()`, written preventively for exactly this and unused until now. Silent for the 39 genuinely mod-added vpaths, for pure-base values, and for chains that already name base. Superseded proposal: have `who-sets` state whether a vanilla file exists at the vpath and carries the same prop — no merge-model or schema change. **Deliberately bundled with F63 symptom 2**: both live in `_effective.py`, an `ENGINE_SOURCE`, so either alone forces a rebuild of the effective store AND BaseX `x4eff`. Together that is one rebuild, not two. Found and measured by the parallel session; full table in `KNOWLEDGEBASE.md` § *2026-08-26h* |
| F65 | `EntityDefs` costs its corpus tier PER NAME, on a population model that only holds for a mod | **SCOPE** | `_check.py::EntityDefs.__contains__` falls through to `_search_corpus`, which re-enumerates base + DLC + every overlay **for each unresolved name** (~2.5s each). Its own docstring states the justification and the population: *"MEASURED, 7 distinct references across 114 mods miss the eager tiers — at most 2 for any single mod."* That is correct **for a mod**. A SAVEGAME is a different population: MEASURED 2026-08-26, one save carries **5,022 distinct macro references**, of which ~2,500 miss the eager index, so the lazy tier would run for hours — observed blowing a 600s cap with no result. The bulk path `_scan` builds the whole definition set once in **19.4s**. Nothing misbehaved; the tool answered a per-name question at per-name cost, and the cost model is stated but not enforced | ✅ **FIXED** — `all_names()` (`_check.py:283`) answers the many-name question ONCE (19.4s) instead of ~2,500 per-name corpus scans; the lazy tier is unchanged, because it is right for the mod caller it was built for. Shipped 2026-08-26 with **zero tests**; covered 2026-08-27 with 5, pinning the property that matters: the bulk set is **index ∪ corpus and macro ∪ component**, and **never narrower than `__contains__`** — a narrower one would not crash, it would report defined names as DANGLING and inflate `x4save check` with false findings (macro-only was MEASURED at **1,792 misses**). Includes a falsification twin |
| F66 | producer/consumer key normalisation — **investigated, NO DISAGREEMENT FOUND** | **NEGATIVE** (recorded so the line is not re-opened) | 3 pairs measured: prop keys **18/18 consumers agree** against the store's 44,708 distinct `attrs.prop`; vpath case **0 of 21** external call sites bypass the case-insensitive door; cross-door `_scan`->`_cat` **216 lookups, 12 misses, all the loose `content.xml` manifest, 0 unexplained**. 4 of 5 pre-registered predictions were WRONG | ✅ **CLOSED 2026-08-27.** The structural reason is the finding: **consumers ITERATE the mapping, they do not look it up by key.** Only 2 direct dict accesses exist and both are inside `_cat.py` (`:150` builds the VFS, `:256` is the exact-hit fast path *within* `_get_ci`, which falls through to a folded lookup). ⚠ Nothing ENFORCES this — it is a convention, not a guard. A future key-lookup consumer would reintroduce the whole class silently |
| F67 | nothing verifies the working tree COMPLIES with the `.gitattributes` line-ending pin | **DEFECT** | The pin governs CHECKOUT only. MEASURED 2026-08-27: `_effective.py` sat **fully CRLF (1205 lines)** in this tree while HEAD and all **6** sibling `ENGINE_SOURCES` were LF — the last holdout, undetected for 2 days. Cost: the engine hash here was **`b25a2a99853d4c84`** where a fresh clone of the SAME commit computes **`3239fa6c515b83b6`**, so every recorded "engine hash is X" was machine-local | ✅ **FIXED 2026-08-27** — `tests/test_line_ending_pin_is_obeyed.py` asks GIT what the pin resolves to (`check-attr eol --stdin`) and reads the WORKING TREE, never `git show`; no allow-list. **Population was wider than the finding: 8 of 129 in dev and 14 of 155 in the mirror — 22 files, not 1**, two of them functionally broken by CRLF (`bin/xrcat`, `x4-paths.env.example`, both bash-sourced). All normalised as pure repairs with `git diff HEAD` EMPTY in both trees. Engine hash asserted unchanged. ⚠ The guard itself returned 0 pinned files at first and only its denominator assert caught it — `text=True` subprocess I/O translates newlines on the way IN on Windows. Superseded note: A cheap guard exists in shape: count `\r\n` in each `ENGINE_SOURCES` file and fail if any is non-zero while `.gitattributes` pins LF. ⚠ Note the direction — this fails SAFE (a false STALE, never a false FRESH), which is precisely why it survived F53's "fix" and two days of use |
| F68 | `x4effective attr` printed a confident zero over a key that cannot exist | **DEFECT** | `_reject_unknown_kind` was written for exactly this — its docstring says *"without this an unknown kind reads as a confident empty answer"* — and is wired into `ls`, `show` and `who-sets` but **not `attr`**, the one command whose arguments are both free-form. MEASURED 2026-08-27 against the live store: `attr zzznotakind hull.max` → `0 value(s) for hull.max`, **rc 0**; `attr macro properties.hull.max` → same, **rc 0**. `who-sets` gets the prop case right (`no prop 'x' on y`, rc 1), so `attr` was the lone hole. Reported by the parallel session, who paid for it: a hand-rolled flatten kept the `<properties>` wrapper, matched only keys living OUTSIDE it, and reported **2.6%** where the answer was **65.4%** — plausible and self-consistent, the only tell being that every printed example was `component.ref` | ✅ **FIXED 2026-08-27** — `_reject_unknown_prop` + `_prop_suggestions`, wired into `attr` alongside the existing kind guard (rc 2 unknown kind, rc 1 unknown prop). Prints the denominator first (*"that kind has 8,672 distinct prop(s)"*), then store-derived suggestions, then **the kind that DOES carry the prop** — `attr ship hull.max` now answers *"`hull.max` is carried by kind `macro` (1973 value(s))"*, which is the sharpest miss because both arguments are individually real. 8 tests written first and watched fail: 3 RED, 3 GREEN on the first run — including a falsification twin proving a **true** zero (prop exists, `--class` excludes it) is still rc 0, so the guard cannot pass by rejecting everything empty |
| F69 | the **engine** freshness axis is coarser than the thing it models | **SCOPE** | The axis hashes whole file BYTES of 7 `ENGINE_SOURCES`, so a docstring, a CLI error message or an argument type-guard invalidates every derived artifact exactly as a merge-semantics change does — and the banner then asserts *"the SAME inputs would now merge differently"*, which for those edits is **false**. MEASURED 2026-08-27 by AST over the current sources: **456 of 3,812 lines (12.0%)** are CLI/presentation functions that cannot change a merged value, concentrated in `_effective.py` (**387 lines, 28.5%**) and `_diff.py` (**69, 25.0%**); the other five are 0%. That figure is a **LOWER bound** — the classifier counts only functions whose names match a CLI prefix, so argparse setup inside `main` and module-level display constants are not in it. Cost is a full effective-store **and** BaseX `x4eff` rebuild per such edit, which is the shape that trains you to ignore a banner. ⚠ **The direction is safe** — it over-reports staleness, never under-reports it, so this is a cost-and-credibility finding, not a correctness one | ✅ **FIXED 2026-08-27** — the CLI surface moved OUT of the engine sources: `_effectivecli.py` (503 lines) and `_diffcli.py` (105), leaving `_effective.py` 1,360 → **897** and `_diff.py` 276 → **188**. Entry points repointed; `verify-cold.sh`'s matrix updated (its own agreement test failed the moment it went stale). **Proven in BOTH directions, because a one-sided check passes vacuously:** editing a CLI message leaves the hash at `e9ea20cc827ad4d9`, while appending one line to `_merge.py` still moves it to `c8c0c8890aceeb12`. Pinned by 6 tests written first — no engine source may import `argparse`, define `main`/`_cmd_*`, or print beyond a **literal 2-call allowance** for `_registry.require()`'s unconfigured-install message, plus a test that the allowance carries no slack (an allowance larger than reality is how a cap stops binding). Deliberately NOT done: making the hash cleverer. ⚠ Two seams the refactor broke and the suite caught: `from _effective import base_has` binds BY VALUE, so a monkeypatch of `_effective.base_has` stopped reaching the CLI — the module indirection *is* the test seam; and my own dependency analyser missed `LIBRARY_REGISTRIES` because it did not handle `ast.AnnAssign`, so the analyser was fixed rather than the one symbol |
| F70 | a nested cross-mod SCRIPT patch is validated by **nothing**, and the docstring said otherwise | **DEFECT** (disclosure) + **SCOPE** (coverage) | Two halves. **(a)** `check_script_validation_scope`, shipped 2026-08-27, matched script files with a PLAIN PREFIX TEST — the exact trap `_xsd.eligible`'s own docstring records having fixed on 2026-07-29. MEASURED: **15 files across 7 mods** missed (`vro` 4, `kuertee_additional_agent_actions` 3, `ship_variation_expansion_vro` 3, `kuertee_npc_reactions` 2, one each from `atd_ejection_router`, `kuertee_emergent_missions`, `zzz_personal_overlay_F`), so every figure published for that feature — **77 of 124 / 17 script-only**, written into two CHANGELOGs, a code comment, a test docstring and a peer message — was wrong. True figures **79 of 125 / 18**. **(b)** The bigger half: those 15 are validated by NOTHING. Both halves of `_xsd.validate_mod` filter `count("/") != 1` (direct children only, deliberately, so loose and packed cover the same population), and `_xsd.eligible` excluded them from the effective check while *claiming they were "already covered by `validate_mod`"*. They are not. All 15 are `<diff>`, and the engine demonstrably loads their targets — KNOWLEDGEBASE.md records it running `md.moreroomsforships.Init` and logging 364 warnings from that very file | ✅ **DISCLOSURE FIXED 2026-08-27** — one shared `_xsd.strip_nesting`, called by both `eligible` and the scope check, so they agree by construction; the measurement that FOUND the bug had re-implemented the strip, which is the same defect one level up. The two populations are now reported **separately**, because the ADVICE differs: direct children say *"run `--update`"*, nested say *"NOT VALIDATED BY ANYTHING — not by `--update` either"*. Counting nested files into the `--update` message would have made the number complete and the advice FALSE, which is worse than the under-count. ✅ **COVERAGE CLOSED 2026-08-27** via merged-result validation with an attribution diff (167 of 182 findings proved to be the target's, not the patcher's). Superseded note:  the depth-1 selectors are UNCHANGED. Widening them alters what `--update` validates, may surface findings across 7 mods, and would need its own measurement plus a re-proof of `gates/xsd_fast_parity.py`. Confidence the nested files are live: **90%** — the mechanism is engine-proven for cross-mod generally (#6), the targets are engine-loaded, and our merge applies the DLC-target ones (`base, vro:diff`); what is missing is a direct in-game confirmation of a nested *md* diff specifically |
| F71 | `x4effective dump --chain` rendered a WRONG FORM as a confident ABSENCE | **DEFECT** · ✅ **FIXED** | ⚠ **The original diagnosis was WRONG and is kept as history in the entry.** It was filed as *"dump cannot resolve a mod-owned vpath"*, concluding the fix *"forces a store + BaseX rebuild — worth batching with other engine-source work"*. **Both halves false.** `_effective.build_touch_map` keys by the **LOGICAL** vpath — the document the engine builds — so a mod's own file is keyed `md/morerooms.xml`. I had queried the **PHYSICAL** disk path `extensions/<mod>/md/morerooms.xml`. MEASURED: logical form → **rc 0**, `sources: moreroomsforships:full, zzz_personal_overlay_F:diff(nested:…)`; physical form → rc 1. Same split on `Honshu Solar Cell Generator` and `Ship Ai Core`, so not a one-off; **1,713 of 3,257 touched vpaths are mod-owned**. The asymmetry is the engine's: for a DLC that literal string IS the game vpath (which is why `build_touch_map` declines to rewrite it), for a mod it means nothing. `dump` was right not to find it and wrong to call it an absence | ✅ **FIXED 2026-08-27** — `_effectivecli.logical_vpath()` + a retry in `_cmd_dump` that resolves and **says so out loud**; resolving silently would trade one confident-wrong answer for another. Retry runs **only after the literal lookup genuinely fails**, DLC and double-nested paths are never rewritten, and a **genuine absence is still rc 1**. **`_effectivecli.py` is NOT an `ENGINE_SOURCE`** — no engine-hash move, no rebuild, and neither is `_xsd.py` where F70's remaining work lives, so the rebuild cost that was the whole basis for batching the two never existed. **A cost recorded from a HYPOTHESISED cause is a guess wearing the grammar of a measurement**, and it steered a plan for a day. Verification caught two more: `test_dlc_enumeration` rejected an `ego_dlc_` name-prefix guess (now asks `Config.dlc_dirs()`), and a falsification twin was SHADOWED — 5 clauses, 5 mutants, C2 survived until the twin was rebuilt to satisfy every other clause; **5 of 5 now load-bearing** |
| F72 | the engine oracle checked **32 of the 165 fields** the engine exposes | **SCOPE** · ✅ **SUBSTANTIALLY CLOSED 2026-08-30** (32 → 68 comparable; the blocker was a FILE FORMAT, not modelling; text resolution and the docks/launchtubes family DEFERRED) | `x4live oracle` compares engine ground truth against the effective store, and its DIRECT map holds 14 field names (hull, mass, 6 drag axes, 3 inertia axes, radar range, purpose). MEASURED 2026-08-27 over 10 entities: **165 engine fields, 32 directly comparable (32 agree, 0 differ), 31 engine-DERIVED, 102 not yet mapped.** (This row read 16/149 until 2026-08-27 while the detail section below already read 32 -- a stale INDEX row beside a current entry, which is why a number quoted from a summary must be re-derived from the entry it summarises.) The 149 split two ways and only one is fixable by adding map entries: (a) localised strings the engine resolves through `{page,t}` (`name`, `description`) which our store holds as the unresolved reference, and (b) **DERIVED AGGREGATES our store does not model at all** — `storagecapacity`, `shield`, `docks_s/m/l/xl`, `launchtubes_*`, `unitcapacity`, `efficiencyfactor`. Those are computed by the engine by FOLLOWING the macro's `<connection>` refs to the storage/shield/dock macros; our store records the refs, never the sum. ⚠ **Direction is safe** — unmapped fields are counted and NAMED in the output, never silently dropped, and the buckets are asserted to sum to the field total, so the scope is visible in every run rather than implied by a clean result | ⚠ **OPEN by design, not by oversight.** Widening the map is cheap for (a)-class fields and is the obvious next step; (b) is a real modelling gap and is the more valuable half, because a derived aggregate is exactly where a merge error would hide without changing any single attribute. Deliberately NOT done yet: the oracle shipped with the scope PRINTED (*"a zero here is 'nothing disagreed among what was compared', not 'the model is correct'"*) rather than held back until complete, since a narrow check that states its denominator beats no check |
| F73 | the pre-push identifier guard could not see the one file a port EDITS BY HAND | **DEFECT** · ✅ **FIXED** | `scripts/verify-port.py` runs the mirror's own identifier matcher, but over the **PORT SET** — dev-tracked files being copied across. The mirror's root `CHANGELOG.md` is *mirror-only*: it sits on `ALLOW` precisely because the mirror keeps its own, so it is not a dev-tracked file and never entered the scanned population. **A port COPIES most files and EDITS one by hand, and that one was invisible to the guard.** MEASURED 2026-08-28 during the v2.9.0 release: `verify-port.py` reported *faithful* with a clean identifier scan, the push went out, and CI's personal-data job failed on `CHANGELOG.md:56` — a personal overlay name in the F71 example output. F61's shape (a guard that can only fire post-push), narrowed from a general caution to an exact, closable gap | ✅ **FIXED 2026-08-28** — `hand_edited_mirror_files()` adds every mirror-tracked file outside the ported subdir; hits are prefixed `(mirror)`. ⚠ **The first fix was INERT and reported success**: `mir_tracked` comes from `git ls-files SUBDIR`, already scoped, so the helper returned `[]` and printed *"0 hand-edited mirror file(s)"* while the selftest passed — the pure function was right and the WIRING fed it a pre-filtered list. A green that could not have gone red, caught only because **0 is an implausible count for a repo with a root README and CHANGELOG**. Now 53 files in scope, and PROVEN BY FALSIFICATION: re-planting the exact leak yields `HIT (mirror) CHANGELOG.md:56`, rc 1, restored byte-exact. Selftest 9/9 |
| F74 | the live channel's message-size cap · ⚠ HALF CLOSED 2026-08-29 (bounded below at **64,000 bytes**) · ★ MECHANISM CORRECTED: it does **NOT** truncate silently, it **TEARS THE PIPE DOWN** | **SCOPE** · ⚠ ceiling still unprobed | ★ CORRECTED 2026-08-29 from the PACKED `pipes.lua` (`ext_01.dat` @400716): the famous `:698` TODO is on the **READ** path — the game reading OUR COMMAND, not our replies — and `winpipe` exposes only `ERROR_IO_PENDING`/`ERROR_NO_DATA`, so 234 falls to `:720` `error(...)` → `Close_Pipe`, **ERRORing every pending read and write and destroying the pipe**; partial data is discarded. Replies fail the same way per the api docs. So it is a LOUD TOTAL teardown, never a quiet short answer. On the ceiling: **64,000 bytes round-tripped intact in game** (13 sizes, length+checksum verified), which **REFUTES** the untraceable 2047-byte figure; python buffers 64 KB (`_BUF`), so anything past ~65,472 measures US, not the engine | ⚠ **BOUND still open above 64 KB.** The mitigation INVERTS: length+checksum is detection *after the fact* and cannot prevent a teardown, so size is **bounded before sending** and any unbounded verb caps itself and reports `shown=N of M` using the free `GetNum*` denominator. ★ The durable lesson is the misfiling: this was classified as *"a step that narrows data and reports success"* — this register's signature shape — and **the category fitting is what stopped anyone re-reading the source for four rewrites.** A taxonomy is an instrument and it rots (cf. the `diff -rq` rule, #53) |
| F75 | `qa_sweep` — the CLI-contract sweep — exercised **28 of 41** capabilities | **SCOPE** · ✅ **CLOSED 2026-08-29** | MEASURED 2026-08-28 by `gates/toolkit_usage.py`, which enumerates the surface by ASKING the programs (pyproject `[project.scripts]` + `--help` + argparse's invalid-choice listing). **13 capabilities are absent from `qa_sweep.CELLS`** (this row said 17 until 2026-08-29 — the gate keyed on `argv[0]`, and 7 of 54 cells lead with a global flag): 12 of `x4modlist`'s subcommands (`ingest dashboard needs-review refresh resolve source tracked ignore mark verify snapshot changed`), plus `x4debug baseline`, `x4effective build`, `x4effective coverage`, `x4live mappings`, `x4xref build`. ⚠ **Scope of the claim, precisely:** this says they are not in the CLI sweep's roster — NOT that they are untested. A bare grep of `tests/` for each subcommand word matches **13 of 13** (`resolve` alone in 99 files, `build` in 60), because those are ordinary English in a Python suite — so that grep establishes nothing in either direction. RE-DERIVED 2026-08-29: this sentence read *6 of the 17*, which carried the stale population AND overstated what the instrument could tell you. What IS established is that a CLI-contract regression in those 13 would not be caught by the sweep built to catch exactly that | ✅ **CLOSED 2026-08-29 — 41 of 41 exercised, and the baseline re-recorded to 0 accepted gaps.** 11 cells joined the quick tier and 2 real builds a new `--all` slow tier, each redirected at a throwaway output so the shared store, `x4eff` and `md_xref.tsv` are untouched — verified by the real registry's mtime being unchanged after a full sweep. The default run NAMES the slow cells it skipped, because a sweep that silently runs a subset and prints a clean total is this register's founding defect and a gate is not exempt from it. Closing it surfaced **F77**: the `snapshot` cell wrote into the REAL registry despite `--registry`, so the gap could not be closed honestly until the tool honoured its own documented override. Superseded note: ⚠ **OPEN — the backlog was ACCEPTED, not hidden.** `toolkit_usage` records the 13 in its local baseline and fails only on a NEW gap, because a gate that goes red on a known backlog every run is the same flood as an uncalibrated threshold and trains you to ignore the runner. The known set is still PRINTED on every run. Closing it means adding cells to `qa_sweep`; that is real work on a shared gate and has not been done |
| F76 | every identifier guard scanned `git ls-files`, so the NEWEST file was outside the population | **DEFECT** · ✅ **FIXED** | `git ls-files` reports the INDEX. A file created but not yet staged is invisible to it BY CONSTRUCTION — and the newest file is the one most likely to carry a fresh leak. MEASURED 2026-08-29: the mirror's `scripts/scan-identifiers.py` printed *"scanning 200 tracked file(s) ... clean"* over a port whose new file was untracked; staging it took the population to 201 and the scan then meant something. `scripts/verify-port.py` had the same shape on the dev side. **Sibling of F73**, and the pair is the point: that one was a file a port EDITS BY HAND, this one is a file nobody has DECIDED anything about yet. Both are populations narrowed UPSTREAM of a guard that then reported clean, and in both cases the narrowing was invisible because the guard printed a confident denominator for the population it did see | ✅ **FIXED 2026-08-29** — both scanners add `git ls-files --others --exclude-standard`; ignored build output stays out, because that is not work. `identifier_population()` and `merged_population()` are pure and selftested (**14/14** and **5/5**) with ONE TWIN PER CLAUSE: `ALLOW` still excuses a TRACKED file, and deliberately does NOT excuse an UNTRACKED one — no decision exists to honour. Both population lines now name BOTH halves, so a zero is visible rather than implied. PROVEN BY FALSIFICATION on each side: planting an untracked file carrying a banned token moves the dev population 163+0 → 163+1 and findings 21 → 22 at rc 1; the mirror 202+0 → 202+1 at rc 1; both clean after removal. CI gains a `--selftest` step BEFORE the scan, because in CI the untracked list is ALWAYS empty — so CI alone could never have caught this, and cannot prove the fix either |
| F77 | `--registry` was honoured by every read and ignored by the one path that WRITES | **DEFECT** · ✅ **FIXED** | `x4modlist --registry <somewhere> snapshot` resolved the override for every read and then wrote its output into the DEFAULT registry's folder anyway: `_changed.snapshots_dir()` called `_registry.require(DEFAULT_REGISTRY, ...)` unconditionally, while every neighbouring command threads `args.registry`. FOUND 2026-08-29 by adding a `snapshot` cell to `gates/qa_sweep.py` — the sweep points every `x4modlist` cell at a THROWAWAY copy precisely so a gate cannot mutate the state it inspects, and this one wrote a real snapshot into `dev/_registry/snapshots/`. ⚠ The direction is what makes it dangerous: it does not fail, it writes to the wrong place and reports success. A documented override that ONE path ignores is worse than no override, because everything else honouring it is what earns the trust | ✅ **FIXED 2026-08-29** — `snapshots_dir(registry=None)`, threaded from `args.registry` through `take_snapshot` and `cmd_snapshot`, normalised via `_registry._registry_file` so a directory and a registry FILE both work. The READ side was fixed with it: `load_baseline("latest", registry)` looked in the same wrong place, and fixing only the write would have left `--registry X changed --since latest` writing to X and reading from the default — a half-taught lookup chain is a subtler bug than the one it replaces. 5 tests including a falsification twin (no-argument call must NOT return the test directory, or the other assertions would pass for a function that ignored its argument); 3 pre-existing monkeypatch stubs updated for the new signature, which is how the suite proved the change reached them |
| F78 | three PUBLISHED surfaces sat outside every port check's population | **DEFECT** · ✅ **FIXED** | `scripts/verify-port.py` proves the dev package matches the mirror, and its population is `git ls-files` of ONE repo scoped to `tools/x4validate`. **`.claude/hooks/` and `.claude/skills/` live in the GAME-ROOT `.claude` directory, which is not a git repository at all, and `tools/basex` is its OWN repository** — so all three ship and none was ever compared. MEASURED 2026-08-29: the shipped `protect-bash.sh` was **3,445 bytes** against **17,722** in daily use, its regression suite had **never shipped at all**, six skills were behind the ones in use, `tools/basex` had **6 of 19 files differing** with one public file (`smoke-basex.sh`) absent locally. Third and fourth occurrence of F73/F76's shape — a guard whose population excludes the thing under test — in two more directions | ✅ **FIXED 2026-08-29** — `gates/published_surface_drift.py` compares all three surfaces, with two properties that were both got wrong first: **(a) line endings are not drift.** The first measurement compared raw BYTES and reported every skill and every basex file as differing, with a telltale +N/-N of equal counts; normalised, **9 of 37** really differed. That is `diff -rq`'s exact failure (52 differences, 12 real) reproduced by an instrument written by someone who had already recorded the trap. **(b) it never prints a bare "differs".** It reports lines present on each side SEPARATELY and refuses to name a winner, because six shipped skills were "behind" and still carried cross-platform work — an interpreter pin, Linux profile locations, `$X4_MODS`-based registry resolution — that the newer local copies lacked. Acting on "differs" would have destroyed it. Drift closed 9 → **2**, both NAMED in the baseline (a peer's uncommitted WIP, and a local `.bak`), and the accepted list is printed by name rather than as a count. 12 tests |
| F79 | **every hook we ship was INERT in production** — `cat /dev/stdin` returns nothing in the hook environment | **DEFECT** · ✅ **FIXED** | All five hooks read their JSON payload with `INPUT=$(cat /dev/stdin)`. MEASURED 2026-08-29 by a purpose-built spike: that returns **ZERO BYTES** in the Claude Code hook environment, while a bare `cat` returns the payload — **7 of 7 probes, 0 vs 641–2,840 bytes**, PreToolUse and PostToolUse alike. The failure is invisible by construction: a hook that reads nothing falls through its first guard clause and exits 0, which is **byte-identical to deciding "this is fine"**. So `protect-bash.sh` (rm the game dir, `git add -A`, unscoped searches), `protect-files.sh` (the hard block on `reference/`, `.cat`/`.dat`, the game install), `backup-before-edit.sh` (the entire audit trail), `search-scope.sh` and `x4validate-on-edit.sh` were **all doing nothing, for weeks, in public releases as well as locally**. ⚠ **THE SUITES WERE GREEN THROUGHOUT**, because a suite pipes stdin explicitly (`printf … \| bash hook`) and `/dev/stdin` resolves fine that way. Green in the harness, dead in production — this register's founding shape, sitting underneath every instrument used to hunt it. **Independent confirmation:** no `AUDIT_LOG.txt` existed in the game root or the workspace at all, and the only one anywhere held 17 entries, **all of them the test suite's synthetic `/tmp/tmp.XXXX/…/wares.xml` fixture — not one real edit in five weeks**, while `CLAUDE.md` stated every edit was auto-backed-up | ✅ **FIXED 2026-08-29** — one shared `x4_hook_input()` in `_x4-env.sh` (bare `cat`), used by all five; **0 executable uses of `/dev/stdin` remain**. Plus `x4_require_input`, because the deeper defect is that **a hook could not distinguish "I received no input" from "the input said allow"** — silence IS consent, and that is precisely how this hid. The suite gained **5 probes that close stdin** rather than piping it, reproducing the PRODUCTION condition a pipe never can; proven by falsification (reverting one hook turns both the runtime and static probes red, rc 1, restore byte-identical). **E2E-verified unconfounded by permission mode**: a real `Write` produced an `AUDIT_LOG.txt` where none had ever existed, plus a backup containing the ORIGINAL content — a side effect, not a decision, so bypass mode cannot mask it |
| F80 | the SUBJECT is one shared repo while the REGISTRY of that subject is forked per session | **DEFECT (structural)** · ⚠ **OPEN** | `tools/basex` is its own git repository, shared by every session. The guard that lists its test files — `TEST_FILES` in `tools/x4validate*/tests/test_basex_tests_are_not_orphaned.py` — lives in the **branch-per-session** x4validate worktrees. So a commit to basex is visible to every session **instantly**, while the registry naming it is not. MEASURED 2026-08-29: committing `tools/basex/test_x4v_tree.py` turned a concurrent session's suite red (`BaseX test file(s) present but NOT in TEST_FILES`) and they could not quote a clean suite number until they hand-applied a one-line change on their own branch. **This is guaranteed to recur** for every basex commit, and no amount of care on either side prevents it — it is a repo-boundary mismatch that the test merely reveals, not fragility in the test. ⚠ The peer's first reading was *"this file is untracked"*, which was a WRONG-REPO observation: from inside `tools/x4validate`, git cannot see a different repository, so everything under `tools/basex` reads as untracked regardless of basex's own index | ⚠ **OPEN — no fix attempted, and the obvious ones are worse.** Weakening the orphan guard would reintroduce the silent-orphan defect it exists to prevent (23 tests that passed and were collected by nothing). Moving `TEST_FILES` into basex would put a guard about x4validate's collection inside a repo that knows nothing about it. Merging the repos is a much larger decision. **Recorded so the next session recognises the shape in seconds instead of diagnosing it as an untracked file**, and so the cost is visible if it recurs often enough to justify a real fix |
| F81 | the live mod's save-load safety hook has **never once fired**, and a `probe` field reporting the symbol as PRESENT is what hid it | **DEFECT** (dead guard) + **DISCLOSURE** (a check that could not fail) · confidence 97% | The mod clears its id allowlist on a save load via `RegisterEvent("Lua_Loader.Send_Priority_Ready", …)`, because a REUSED handle in a different save would return another object's data under the id you asked about. MEASURED 2026-08-29/30 across a game start AND a save load: the handler's log line appears **0 times**, while `RegisterEvent absent` and `could not hook game load` are **also 0** — so it registers cleanly and never runs. It **cannot** run: it subscribes from inside `Init`, and `Init` is itself driven by that signal, so it always registers AFTER the event; and the chunk is rebuilt on every load, replacing the listener before it could fire. **Structurally dead.** ⚠ Three things hid it, and each is the register's own signature shape: (a) `probe` reported `RegisterEvent=function` and PRESENCE WAS READ AS WORKING — a symbol existing says nothing about a callback executing; (b) the unit test covering the hook was GREEN throughout, because it asserts the callback behaves correctly *if called*, which is true and which licensed a false belief about production; (c) a code comment asserted "loading a save does NOT re-run Init", which is **false** — a save load IS a full UI reload | ✅ **DISCLOSURE FIXED 2026-08-30.** The safety itself was never absent: MEASURED end-to-end with a control, an id accepted before a save load is refused after it — but by **chunk re-creation**, not by the mechanism written to provide it. So the hook is kept as defence in depth and **instrumented** (`load_hook_fired`, reported by `probe`) so its deadness is visible rather than re-assumed, its log line now says out loud that it had never appeared, and the false comment is corrected with the measurement. Pinned by two tests that assert BOTH states of the field, since one hard-coded to `false` would look identical in game. ★ The durable lesson: **this was nearly "fixed" by persisting the allowlist across reloads — which would have deleted the only clearing that actually works.** A dead guard is dangerous twice: once because it does nothing, and again because removing what compensates for it looks safe |
| F82 | the hook rule set's false-positive rate: ~1,700 of 2,949 non-allow verdicts are noise, in SIX rules of one shape | **DEFECT (measured)** · ⚠ **OPEN — rules not yet edited** | ⚠ SUPERSEDED FIGURES: the first measurement ("deny 8.89%", 10,852 commands, per-rule table) was taken while the live hook was REDEPLOYED mid-run and with only `command` in the payload — see F83; those numbers are history, not baseline. **RE-MEASURED 2026-08-30 on a STABLE hook** (sha identical before, after and at read-back; all three fields sent; 11,340 of 11,340 distinct commands; validated by a second full pass with the stricter instrument: 11,340 shared, **0 changed**, stderr noise 0): **allow 8,391 (73.99%) · advise 1,406 (12.40%) · ask 200 (1.76%) · deny 1,343 (11.84%)**. Every non-allow verdict classified (2,949 of 2,949, buckets sum, every bucket hand-checked from full text): redirect advisory **1,294 of 1,320 FP** (`2>/dev/null` + a game path anywhere; 25 are `>>` appends) · Documents `ask` **193 of 196 FP** (same shape, in a rule added the same night) · LONG JOB **125 of 144 FP** (mention, not invocation) · reference search **67 of 80 FP** (subdirectory-scoped, which the rule's own comment promises to allow) · cp/mv **49 of 86 FP** (copies OUT of the game) · game-delete **7 of 8 FP** (a `.zip` NAMED after the game) · workspace-root **12 of 20 FP** (scoped, or a non-recursive grep the regex reached across segments). **Genuine and staying:** TIMEOUT-above-cap 449 of 449 (newly measurable — the field never reached the hook before), `/tmp` 226 of 227, `git add -A` 186 of 190, and 43 manual-reviewed | ⚠ OPEN: measured, classified, and NOT yet fixed — the user's standing order is re-derive first, edit nothing, then one rule per commit with a prediction stated before each re-measure (`uv run python gates/hook_false_positives.py` compares per rule over the intersection of commands). Classifier: session scratchpad `classify.py`, three of its own bugs fixed before the buckets held |
| F83 | the instrument that measured F82 had FIVE defects of the narrowing-step shape, and the hook it measures had the stdin defect one layer in | **DEFECT (measured)** · ✅ FIXED 2026-08-30 | `gates/hook_false_positives.py` v1: (1) recorded while the live hook was REDEPLOYED mid-run (launch 22:28, deploy 00:08:29, finish 00:15:36 — three independent timestamps; bash re-reads the script per spawn); (2) stored 25 EXAMPLES per rule and no counts — eight rules read exactly 25; (3) overwrote its own baseline every run, so nothing could be diffed; (4) sent only `command` — the hook also reads `run_in_background` and `timeout`, so every backgrounded long job replayed as FOREGROUND and the cap rule could never fire; (5) read a per-command hook FAILURE as allow — MEASURED by review probe: `JQ=no_such_binary` gives rc 0, empty stdout, stderr noise. And the hook itself: `< <(jq ...)` loses jq's exit status, so a failed parse exited 0; the first fix reported that THROUGH jq and stayed silent | Rewritten TDD-first (31 tests, every one watched fail): sha256 of both hook files + `x4-paths.env` before AND after, paths re-resolved at the end, void on drift; uncapped `counts_by_rule` that must sum; `--record` vs compare over the INTERSECTION of commands with moved-pair reporting; all three fields sent RAW; rc≠0 or empty-verdict-with-stderr → refusal, rc 2/3 never 1. Mirror hooks: captured parse with a jq-FREE literal `ask` (67/67 probes, 5 new, all seen red first). **RE-DERIVED BY:** `tests/test_hook_false_positives_gate.py`, `scripts/test-hooks.sh` parser-contract probes |
| F84 | an ADVISORY exits the hook, so every rule below it is unreachable: 298 measured-genuine refusals were suppressed by a note | **DEFECT (measured)** · ⚠ OPEN | `advise()`, like `deny()` and `ask()`, ended in `exit 0`. But an advisory is an ALLOW THAT CARRIES A NOTE, not a decision — and it sat at rule 8 of 19, so a command that earned one never reached the eleven rules below it. MEASURED 2026-08-30 by commenting out the six advisory/ask rule bodies and replaying the **1,846** commands they catch (controls held: known-bad still refused, stage-everything still refused): **1,548 fall through to `allow`, and 298 to a REFUSAL further down** — TIMEOUT-above-the-cap **130**, shared-tmp **64**, durable truncating-open **35**, exit-status-after-a-pipeline **27**, profile-manifest-by-NAME **26**, stage-everything **11**, durable redirect 4, in-place-edit 1. Every one of those rules was classified **genuine** in F82 (TIMEOUT 449/449, shared-tmp 226/227, stage-everything 186/190). So the noisy rules were not merely noisy — they were **suppressing correct refusals**, including the guard against a silently-clamped 10-minute timeout and the one against searching the profile by name (CLAUDE.md #30) | ⚠ Two consequences beyond the fix. (1) **F82's per-rule table is CONDITIONAL, not independent** — each count is "what this rule caught GIVEN every rule above it already ran", and it was presented as though the rules were independent. (2) The predicted effect of scoping the six rules INVERTS on one axis: refusals do not fall to ~1,135, they RISE to ~1,416, and correctly so, while advisories fall 1,406 → ~50 and confirmations 200 → ~7. Fix: `advise` accumulates and is emitted once at the end; the two terminal verdicts stay terminal. **RE-DERIVED BY:** `scripts/test-hooks.sh` § *an advisory never masks a later verdict* (5 probes) |
| F85 | the engine SILENTLY IGNORES an unrecognised sector container and returns the WHOLE GALAXY, labelled as the sector you asked for | **DEFECT** (silent wrong answer) · confidence 99% | MEASURED 2026-08-30, same faction seconds apart: `ID: 514068` -> **310** objects; `ID:` -> **1961**; `NOT_A_SECTOR` -> **1961**; deliberate `-` -> 1961. No error, no flag, **6.3x the data**. The register's signature defect INVERTED: a step that FAILED to narrow, reporting as though it had. Certain to bite, because sector tokens are **not stable across launches** — Argon Prime was `ID: 4305`, `ID: 5936`, `ID: 514068`, `ID: 4303` over four launches — so a token reused from an earlier session is the ORDINARY case. ⚠ Found by a BROKEN INSTRUMENT: an unquoted shell variable split the token, and the nonsensical count was chased instead of shrugged at | ✅ **FIXED 2026-08-30.** Token shape validated (`^ID:%s*%d+$`) in both `run_enumeration` and `compare`, refusing with the measurement and where to get a fresh token; `-`/`galaxy` stay a deliberate galaxy request. 2 tests, 2 mutants. ⚠ We validate the SHAPE, not the identity: a well-formed token for a sector that no longer exists is still silently widened |
| F86 | `GetComponentData` rejects a raw `UniverseID` cdata by returning nothing about any object — and does NOT raise | **DEFECT** · confidence 99% | Vanilla always converts first (`ConvertStringTo64Bit(tostring(...))`) or passes a LuaID. Given a raw cdata from an ffi buffer it returns values that describe nothing. MEASURED 2026-08-30: the galaxy-wide path reported **`enumerated=1539 unclassified=1539 unreadable=0`** — every object unreadable with ZERO read failures, a reply that was well-formed, correctly counted and about nothing. The offline fake accepted any id shape, so the conversion looked optional | ✅ **FIXED 2026-08-30.** `readable_id()` converts only the canonical ULL rendering; LuaIDs (`userdata`) pass through. The fake now returns nils for a ULL-shaped id so the omission FAILS a test. Pinned by asserting the wide path `matched` its rows — the old test asserted only `enumerated`, which counts handles returned and is blind to whether any could be read. ★ Found in one query by `unclassified=`, a counter added days earlier for an UNRELATED crash: **a narrowing counter pays for itself in the bug it was not written for** |
| F87 | a two-repo lockstep was "verified" by two sessions reading the WORKING TREE, while the committed state — the only state a port ships — disagreed for two days | **DEFECT (measured)** · ✅ FIXED 2026-08-30 | A lua mod's `<savedvariable>` name must equal a Python constant or the channel silently decodes zero of everything. A peer reported the de-brand rename complete and lockstep-consistent; I replied that I had "verified every claim". Both of us had read the FILE ON DISK. MEASURED afterwards: `git show HEAD:` on the mod gave the OLD name while the working tree gave the new one, with both files modified and unstaged, and the toolkit half committed 72 seconds after the mod files were written — one operation, two repos, one of them committed. My own `scripts/verify-port.py` carries the rule in its header (*compare COMMITTED BLOBS, not working-tree files*) and I did the opposite while using the word "verified" | ★ **Not a wrong query and not a narrowed population — the QUESTION was right, the POPULATION was right, and the instrument answered correctly. The ARTIFACT was wrong.** Nothing local objects, because both artifacts exist, both are readable, and only one of them ships. Same family as the peer's own root cause the same hour (*"I read the PRODUCER and never the CONSUMER"*): producer-vs-consumer · disk-vs-blob · working-tree-vs-HEAD. **The control is one line: assert the two sides agree IN THE SAME VIEW** — one comparison of the pair that ships, not two eyeball checks of two artifacts. Now the gate for the port. **RE-DERIVED BY:** the port gate comparing the mod's committed `<savedvariable>` against the CLI's committed `DEFAULT_VAR` in a single command |
| F88 | a probe list keyed on ARGUMENT POSITION is keyed on nothing | **DEFECT** · confidence 97% | `recon` buckets the engine globals it calls by argument shape, and `GetMacroUnitStorageCapacity` sat in the OBJECT-id bucket while vanilla passes it a **MACRO** (`menu_map.lua:10461`, `:10468`). MEASURED in our own committed report `recon-20260830-211605.tsv`, against two different ids: **`OK number:0`** — no error raised, a plausible value, and `0` is exactly what a ship with no unit storage would report. This is **F86's shape, four days later**: an engine getter handed the wrong id type answers uselessly and does NOT throw, so `pcall` cannot help. It also lands on the exact family F72 conceded to `_DERIVED`, where a `0` is worse than no reading — it is a datum agreeing with a wrong model | ✅ **FIXED 2026-08-31** — a fourth bucket `RECON_MACRO`, reached through the vanilla hop `GetComponentData(id,'macro')` copied rather than composed; when that hop yields a non-string the probe is SKIPPED **and the skip is printed**, never falling back to the id. 7 mutants killed, 0 survivors, including one that re-introduces F88 exactly and turns the BEHAVIOURAL test red — the structural test alone passes the moment the name moves between two lists. ✅ **The other 90 names WERE re-derived 2026-08-31 and found FIVE more, four of them SILENT; mechanised in `tests/test_recon_buckets_are_vanilla_attested.py`.** Found by reading all 803 function names in the `_G` census, not by a sweep |
| F89 | a guard rewrite made every Bash call 11.3x slower, and NOTHING measured hook latency | **DEFECT (measured)** · ✅ FIXED 2026-08-31 | Eight guard rules were re-scoped to fix a false-positive rate; each hand-rolled its own quote-aware shell parsing in bash. Correctness improved and nobody timed it. MEASURED 2026-08-31, clean machine, warmup discarded, 5 runs: `echo hi` **978 -> 3,379 ms**; a 201-char command **1,205 -> 13,585 ms (max 18,713)** — **11.3x**, and PreToolUse blocks the tool call, so it is pure latency on 100% of Bash calls. Profiled per helper on a 19-segment command: `resolve_var` cost **236 ms for ONE token** and ran per-token inside per-segment loops, while `writes_under` (3,328 ms) and `searches_rooted_at` (2,961 ms) were re-invoked 5 and 4 times, each re-tokenising from scratch — multiplicative, not additive. ⚠ Compounded by a `--record` job found **11.06 h old, 5.9 s CPU, 0 CPU over 5 s — deadlocked — holding 42 stuck hook processes**, 0-byte log, absent rc file; it poisoned the first timing (37-43 s/call, which was contention). **A guard has a second output, the time it costs, and nothing was watching it** | One parse pass (`.claude/hooks/hook_facts.py`), policy and prose left in `protect-bash.sh`. Same conditions: **705 ms**. 19 verdict calls before and after, no rule lost, no verdict kind changed. **RE-DERIVED BY:** `test_hook_facts.py` (97 tests), a mutation harness (12/12 caught by their TARGET test) and a coverage harness (19/19 predicates probed in BOTH directions) |
| F90 | an adversarial code review with no INCIDENCE denominator over-ranks by construction | **PROCESS DEFECT (measured)** · ✅ RECORDED 2026-08-31 | A review of the eight re-scoped rules returned *"NOT READY — three are strictly weaker"*. Re-derived over **12,269 distinct historical Bash commands**: *"escaped-space delete was DENY"* is **false** (`x4_norm` maps `\` to `/` before the old backstop ran, so the old code missed it too) — **0 of 12,269** incidence; the `.`/`..` regression is real — **0 of 12,269**; and *"`rg` is the single commonest full-tree command in this workspace"* is **false — `rg` is invoked via Bash 0 times in 12,269 commands**. Incidence for the rest: `mv -t` 0, `>\|` 0 (all 21 apparent hits were regex alternations), wrapper+grep 11, `grep -r -e` 2. **A constructed input has no natural frequency**, so severity ranked by "how bad" and never by "how often" put six 0-incidence CRITICALs above a defect costing 13 s on every command — which the review rated IMPORTANT and never timed | Ask every finding for its incidence before ranking it; the corpus answers in one query. A 0-incidence finding is worth fixing LATER, never ahead of a measured one. ⚠ **The corpus is the WRONG POPULATION for an installer-facing finding** — the game-delete backstop lost on an unconfigured machine cannot be argued down by a local zero, and was fixed on that ground alone. The review's most defensible finding was **rules with no probe at all** (`sed -i` 0, XRCatTool 0, durable-record 1, timeout cap 1, workspace search 3), now all closed. ⚠ And its inference *"the commonest full-tree command"* was copied into a plan file **in the grammar of a measurement** — record a review's WRONG claims, or it reads as authoritative next time |
| F91 | MSYS TRANSLATES a POSIX path in an ENV VAR crossing to a native Windows process, so every path rule went quiet | **DEFECT (measured)** · ✅ FIXED 2026-08-31 | `protect-bash.sh` passed its roots to `hook_facts.py` through the environment. On Git-Bash/MSYS, exporting a POSIX-looking value to a **native Windows** child rewrites it: bash exported `X4_DOCUMENTS=/tmp/sbx/docs`, python received `C:/Users/<user>/AppData/Local/Temp/sbx/docs`, while the COMMAND TEXT compared against it still said `/tmp/sbx/docs`. They can never match, so the Documents confirm, the save-game confirm, the reference hard block, the mods delete and the deploy advisory ALL silently stopped firing (`writes_documents` read 0 for a write straight into the configured root). **Not test-only:** `README.md` tells users they may write roots as `C:\...` *or* `/c/...` | Roots now travel on **stdin** ahead of the payload, terminated by a sentinel — a byte stream is not translated (argv is not safe either; MSYS converts path-shaped arguments too). ⚠ Two further findings from the same line: `_x4-env.sh` only EXPORTS what came from `x4-paths.env` via `set -a`, so `X4_DOCUMENTS`/`X4_SAVES`/`X4_REFERENCE` were **never exported at all** — the child would have seen them empty even without the translation; and the hook suite's own sandbox lived under `/tmp`, so the shared-`/tmp` rule fired on unrelated write probes, unnoticed because **that rule's regex accepted a double quote and not a single one** and the probes quote with singles — *the harness was passing because of a gap in the rule standing next to it.* Sandbox moved, with a refusal if it lands under `/tmp` again, and both quote styles probed |
| F92 | the BASELINE was never the thing the claim was about | **PROCESS DEFECT (measured)** · confidence 99% | **TEN instances in ONE DAY across two sessions** — one substitution at ten layers: what an INSTRUMENT RETURNED standing in for a FACT ABOUT THE WORLD. A name census over 803 functions became a claim about behaviour ("PERMANENT"); "`GetContainedShipsByOwner` does not return them" became "no faction owns them" (they are `owner=argon`); "absent from the enumerator I probed" became "absent from OUR TOOL", which called a third function that returns them. A peer cited `:536` from a 543-line copy against a 479-line file, naming one crash site of four. BOTH sessions reasoned about who would lose `check_coverage` from their own working copy instead of the MERGE BASE, and the agreed resolution had the polarity backwards — it would have deleted 4 commits. Plus a kill verified against a PID that never existed, a buffered output file read as a hang, and 792s of self-inflicted contention read as a 12.8x regression | ⚠ **RECORDED, not fixable by a test.** The tell is grammatical: you wrote *"X is not there"* where you measured *"what I asked did not return X"*. ⚠ Escalating certainty across successive corrections is the ALARM ("PERMANENT" → "REAL at 2%" → withdrawn). Mitigation is **CLAUDE.md #34**, which loads every session; this register does not. ★ I wrote #34 and broke it three more times within the hour |
| F93 | a CAPABILITY improvement widens a guard nobody re-scoped for it | **DEFECT (measured)** · ✅ FIXED 2026-08-31 | The game-delete HARD BLOCK covered any target UNDER the game folder — deliberate, and defended here, because narrowing it to the root alone would let `rm -rf <game>/extensions` fall through to a confirmation. It was also survivable only because the old bash helper **could not resolve `$VAR`** (it took the FIRST assignment and split on whitespace), so `DST="$GAME/extensions/mymod"; rm -rf "$DST"` reached it unresolved and fell through. The parse-pass rewrite fixed variable resolution as a side effect — and the guard began **hard-denying the documented deploy path**, which `dev/_tools/deploy.py` performs itself. MEASURED over the first 1,000 distinct historical commands: **4 hits, 4 of 4 a single mod folder or one file inside one, ZERO the install root**; two had been plain `allow` before. ★ Its own shape: every other entry here is a guard too NARROW or an instrument answering the wrong question — this one became too BROAD **without being edited**. Its predicate did not change; what changed was how much the machinery underneath could SEE | Hard block is now the install ROOT or `extensions/` WHOLESALE; anything inside falls to the existing confirmation, and the unconfigured-install name backstop is anchored the same way. Across **all 12,482** distinct historical commands: **0 hard blocks, 48 confirmations**. **The rule: when you improve what a shared helper can RESOLVE, re-measure every guard that consumes it** — a capability change is a scope change for every rule downstream and will not appear in a diff of those rules. ⚠ **Nothing hand-written could have caught it**: 122 bash probes, 103 unit tests and 27 E2E cases were green because not one used a variable-resolved deploy path. The corpus replay found what every probe someone thought of missed. Two smaller findings fell out: the `.zip` exclusion on the backstop became UNREACHABLE once the name test was `$`-anchored (the mutation gate reported it as a term no mutation could kill — dead code seen from outside), and the test asserting `extensions/` is still blocked was passing for the WRONG REASON (the name backstop matched it too), so it survived a mutant that removed the clause it names — third shadowed-clause instance in one session |
| F94 | PRECISION about operands bought BLINDNESS to indirection | **DEFECT (measured)** · ✅ FIXED 2026-09-01 | The parse-pass rewrite replaced whole-command-string grepping with structured operands. The old code caught a dangerous path wherever it APPEARED; the new one caught it only where it RESOLVED — so every indirect way of naming a path went dark at once. Measured against `c400a05`: `cd <saves> && rm -f *.xml.gz` went **ask -> allow** (savegames, which nothing backs up), `rm -rf "$(echo <game>)"` went **deny -> allow** (`has_unresolved` tested only `$NAME`/`${NAME}`, so a substitution read as a LITERAL path), and `cd <game> && echo x > f` lost its advisory. Six further shapes were allowed by BOTH (`cd`/`pushd`/subshell into game, reference, extensions; `rm -rf "$X4_GAME"`). ★ `cwd_of()` ALREADY EXISTED and was consumed only by the search rules — `facts()` computed it and popped it before returning. The capability was present, correct, and wired to one caller | Every operand (`rm`, `cp`/`mv`/`tee`, redirects) is now resolved against the directory in force FOR ITS OWN SEGMENT, tracking `cd`/`pushd`/`popd`; `$(...)` and backticks count as unresolvable; a root named only by its env var is recognised by NAME. Conservative for **deletes only** (user decision): a delete is the one channel with no backup — MEASURED, 0 of 186 auto-backups cover anything outside `dev/`. Cost over **13,041** distinct historical commands: **denies 836 -> 836 (+0)**, asks 152 -> 187, prompt rate 1.16% -> 1.43%, and **0 facts went true -> false**. ⚠ **The FP corpus is blind to unconfigured-machine defects**: while fixing this I added a `not u` filter to the NAME backstop, silently removing the only protection an install with no configured paths has — **0 of 13,041** commands have that shape, so the replay could not see it, and only the MUTATION GATE did. Two of my new mutants also turned out BEHAVIOURALLY EQUIVALENT (an unresolved operand still contains `$`, so it can never equal a root); they are documented as not-mutated rather than left permanently red |
| F95 | `install.ps1` did not parse at all on the shell the README tells users to run | **DEFECT (measured)** · ✅ FIXED 2026-09-01 | Nine UTF-8 em-dashes, **no BOM**. Windows PowerShell 5.1 reads a BOM-less `.ps1` as the ANSI codepage, so each `—` became three mojibake characters; one sat inside an interpolated string at line 108 and the parser derailed — **3 parse errors, dead before its first statement**, on the default shell of Windows 10/11. `pwsh` 7 parses it fine, which is exactly how it reached a 3.0 release candidate. **Two more defects were stacked behind it, each masked by the one in front**: neither installer copied `mods/` (so the README's "copy that folder into `{game}/extensions/`" named a directory that never existed — and `.gitignore`'s bare `content.xml` also dropped the mod's manifest from `git archive`, and an X4 extension without a manifest does not load, silently); and `Get-Command bash` resolved to the **WSL stub** in `System32`, present wherever WSL is enabled (every Docker Desktop install), so `setup.sh` died with `execvpe(/bin/bash) failed`. On a machine WITH a distro that is worse — setup would have run inside Linux where `C:\...` does not resolve, a silent wrong install | ASCII-only in `install.ps1`; Git Bash preferred and the known stubs refused by path; `mods` added to both installers' copy lists; `!mods/**/content.xml` un-ignored. **Not one was findable by reading the diff — all three were found by EXECUTING the installer**, which is why "run it, do not read it" is now the rule for bootstrap scripts. Guarded by two checks because they fail differently: an ASCII assertion (catches the cause, runs on every platform including the Linux CI leg) and a real PS-5.1 parse of every tracked `.ps1` (catches any other syntax defect, but only where that engine exists). ⚠ **One check would have been vacuous**: a mutant restoring a single em-dash to the header comment is caught by the ASCII test and **passes the parser test**, because only the em-dash inside an interpolated string breaks parsing |
| F96 | a shell RESERVED WORD in front of a command was a TOTAL guard bypass | **REAL (measured)** · FIXED 2026-09-01 | `protect-bash.sh` splits a command on `;` and `&&`, so `if true; then rm -rf <game>; fi` yields the segment `then rm -rf <game>` and `verb()` returned **`then`** -- every verb-keyed rule (`rm`, `sed`, `git`, `grep`) missed. **MEASURED E2E through the real hook: 90 bypasses over 10 compound forms x 9 seeds** (`if/then`, `if` as the CONDITION, `for`, `until`, `else`, `elif`, `case` arm, `!` negation, `f() { ... }`, nested `if`+`for`). For the three HARD BLOCKS -- game root, extensions wholesale, reference tree -- the move was **deny -> allow** | Reserved words are skipped before the verb is read. Re-derivation: `.claude/hooks/test_hook_facts.py` `TestReservedWordsDoNotHideTheCommand`; 10 compound-form mutators in `scripts/fuzz-guard.py` |
| F97 | `<<<`, `<<` in a comment, and an arithmetic shift each opened a bogus HEREDOC | **REAL (measured)** · FIXED 2026-09-01 | `heredoc_marker()` scanned for `<<` and, on a hit, treated everything after that line as a heredoc BODY -- data no rule inspects. Three constructs open no heredoc at all: a here-string `cat <<< hello` (the scan reached the SECOND `<`, read `<< hello`, marker `hello`), `# shifts a << b` (the marker pass did not stop at a comment), and `n=$((1 << FOO))` (a left SHIFT). **Each blanked the rest of the command**, so a game-directory `rm -rf` the guard refuses unaided was allowed | The scanner recognises all three. Re-derivation: `.claude/hooks/test_hook_facts.py` `TestNotEveryDoubleAngleIsAHeredoc` -- 4 mutants, one per clause, plus controls proving `<<EOF`, `<<'PY'` and `<<-END` are still recognised |
| F98 | `bash -lc` and `eval` carried a command past every rule | **REAL (measured)** · FIXED 2026-09-01 | `_inner_commands()` unwrapped a shell's `-c` argument by matching the **literal token `-c`**, so one character of flag CLUSTERING walked past every rule. MEASURED E2E against a game-root `rm -rf` the guard denies unaided: `sh -c '<rm>'` deny, `xargs ... '<rm>'` deny, **`bash -lc '<rm>'` SILENT ALLOW**, **`eval '<rm>'` SILENT ALLOW** | All seven `-c` spellings and `eval` are unwrapped. Re-derivation: `.claude/hooks/test_hook_facts.py` `TestWrappersThatCarryACommandAsText`, including a control that an ordinary wrapped command stays quiet |
| F99 | the guard fuzzer kept a HAND-WRITTEN copy of the verdict map, and was blind to 26% of the rules | **REAL (measured)** · FIXED 2026-09-01 | `scripts/fuzz-guard.py` decided each command's verdict from two literal tuples, `DENY` and `ASK`, commented *"read off protect-bash.sh. Policy lives there."* It had been read off once and had drifted. **MEASURED against the hook's own mapping: protect-bash.sh maps 19 predicates; the tuples listed 14.** Missing entirely: `search_rooted_reference`, `search_rooted_workspace`, `copy_into_game_or_profile`, `redirect_truncate_into_game_or_profile`, `durable_truncating_redirect`; a sixth, `longjob_foreground`, was classed `ask` where the hook returns `deny` (confirmed E2E) | The policy is DERIVED from the hook rather than copied. Re-derivation: `tests/test_fuzzer_policy_matches_hook.py` |
| F100 | FOUR gates could not report the thing they were built to find, and one of them hid the other three | **DEFECT (measured)** · ✅ FIXED 2026-09-02 | `gates/mutation_probe.py` printed `killed` for **every** survivor. THREE independent causes, and the third was invisible until the first two were fixed and it still said 45/45 — *which is exactly the shape the broken gate produced, so a clean sweep was not evidence*. (1) a self-referential anchor test a mutated tree necessarily fails; (2) `write_text` rewrote the LF-pinned target as CRLF (MEASURED 0 → 790 in `_merge.py`); (3) the probe's OWN marker makes 5 artifact-writing tests refuse — correctly — so the full suite can NEVER be green during a probe (5 failed/1178 passed with the marker, 1183 without). Alongside it: `verify-hook-tests.py` judged each mutant by the PREVIOUS mutant's failures (stale `__pycache__`; CPython invalidates on mtime-in-SECONDS + size, and successive mutants land in the same second at the same size); `gates/control_bytes.py` scanned **171 of 241** tracked files because `git ls-files` ran in the package dir, so it could not see `.claude/hooks/` or `scripts/` — the very code where escapes collapse, and it was blind to the file carrying a literal `0x08`; `gates/obtainability_audit.py` recorded a denominator it never compared or printed | Escalation now compares failure **SETS** against a baseline taken WITH the marker present, so only a NEW failure is a kill; bytes I/O at every mutate/restore site; `PYTHONDONTWRITEBYTECODE` + `__pycache__` removal per mutant; sweep widened to 240 of 241 with the one binary NAMED and the extension allowlist replaced by a content sniff; denominator compared and printed. ★ **The repair was proven by planting an INERT COMMENT EDIT and requiring `LIVED`** — a mutant nothing can catch. It then found **3 real survivors**, each guarding a documented gotcha (#24 active-vs-installed, #18 SUBTREE-is-the-wiper, #6 nested cross-mod path); all three now killed. ⚠ The general rule: **a gate whose output looks the same when it is broken as when it is working cannot be verified by running it** — only by feeding it something whose answer you already know |
| F101 | a guard protected the ASSISTANT's path and not the USER's | **DEFECT (measured)** · ✅ FIXED 2026-09-02 | `bin/unpack-reference.sh` WROTE the sentinel `reference/.unpacked-and-locked` and read it **nowhere**. Its own text — *"reference/ is read-only; remove this file manually to re-unpack"* — was false for the only path that actually unpacks. The lock did exist, but only in `protect-bash.sh`, which sees commands the assistant runs through its Bash tool; it cannot see the XRCatTool invocation this script makes in a CHILD PROCESS, and it does not run at all for a user typing `bash bin/unpack-reference.sh` or `bash install.sh --unpack`. Found by an `install.sh --unpack` that re-unpacked an already-locked reference tree without a word. ⚠ **The same shape in the same session:** an installer E2E test cleared `X4_*` for some cases and not others, and `_x4-env.sh` fell through to `$X4_TOOLKIT` — the REAL toolkit — so the "sandboxed" test read the real config and ran XRCatTool against the real game (CLAUDE.md #26's cold-is-not-cold trap). Aftermath measured at **1 of 510,711 files changed**, the sentinel, because XRCatTool preserves catalog timestamps — harmless, and entirely luck | Script now refuses (rc 2) when the sentinel exists, naming `X4_FORCE_UNPACK=1` as the deliberate override. The test harness routes EVERY invocation through one `sandboxed` helper that clears the whole `X4_*` family including `X4_TOOLKIT`, and a **cold-sandbox control runs FIRST** and fails the run if the resolver can still see the real game or reference. ★ **The rule: a guard that lives in the assistant's tool layer protects only the assistant.** Anything a USER can invoke needs the check inside the thing they invoke — and "the hook covers it" is an answer about a different threat model |
| F102 | the guard could be made to do UNBOUNDED work, and every bound it had was on a different axis | **DEFECT (measured)** · ✅ FIXED 2026-09-06 | `hook_facts.resolve()` substitutes a command's own assignments, looping 5 times under the comment *"bounded: nested vars, never a loop"*. True, and aimed at the wrong axis: what grows is the STRING. A value naming its own variable re-expands every reference on every pass, MEASURED at a clean **x9 per pass** (916, 10316, 94916, 856316, 7708916), so the 4-character token `${B}` reaches **7.7 MB** inside the five passes already allowed. MEASURED on one **949-character** command out of real session history: **23,128,230 characters** of carried command, **531,448 segments**, an **18.3 GB working set** leaving 0.7 GB free of 31.8 GB with the game running. This is the BLOCKING PreToolUse path, so it hangs the whole session, and it needs no adversary — a self-referential assignment is ordinary shell. ⚠ Every neighbouring bound was sensible ALONE (`_MAX_CARRIER_DEPTH` 4, `_MAX_CARRIED` 250 with a census behind it, the 5-pass loop); none bounded SIZE, and nothing multiplied them. The first diagnosis was also wrong — >200,000 `tokens()` calls looked causal, a memoisation was written, MEASURED to change nothing, and reverted | `_MAX_RESOLVED = 65536` (3.3x the longest of 17,268 real commands). On exceeding it, resolve returns the PREVIOUS complete pass with references intact, which `has_unresolved` already reports as unresolved — the file's existing channel for a value a hook cannot know. Refusing to RESOLVE keeps every rule looking; truncating would hand the rules a shorter operand and quietly narrow what they see. Per-item replay over 17,268 commands: **1 verdict moved**, the incident command going from `MemoryError` to a real verdict; **0 loosened**; corpus **297s -> 47s**; exactly **1 of 17,268** reaches the ceiling |
| F103 | the freshness marker did not travel with the tree it describes, so a guard cried wolf every session | **DEFECT (measured)** · ✅ FIXED 2026-09-06 | `check-reference-version.sh` compared the live game build against `$X4_TOOLKIT/.claude/.reference-buildid` — a DETACHED file updated by hand as a separate step, which is the step that gets missed. MEASURED: the two detached copies **disagreed** (`<toolkit>/.claude` 23524486, game-root 23660954) while the sentinel `reference/.unpacked-and-locked` and the live game both said 23660954. So the hook announced a stale `reference/` AT EVERY SESSION START while the tree was current, and the remedy it names is a **~60 GB re-unpack**. ★ It was ALSO blind the other way: with the detached file current and the tree genuinely old it stays **SILENT** (sandbox case 2) — a false negative in the direction that matters. The same stale mtime then produced a WRONG ROOT CAUSE in `gates/schema_sweep.py`, which recorded a schema-floor move as a 2026-09-02 re-unpack; MEASURED, **1 of 510,711 files** under `reference/` has a newer mtime and it IS the sentinel, because XRCatTool preserves catalog timestamps | Hook now reads the **sentinel** (written into the tree by the unpack, naming the build in its text, so it cannot drift from its subject), falling back to the detached file only for a tree predating it, and it names WHICH source it used. The stale copy was corrected; all four sources now read 23660954. 4 sandbox cases; old/new behaviour inverts on two. `schema_sweep`'s attribution is corrected in place to **UNKNOWN**, naming what it is not and naming the candidate ruled out untested — that the CHECK changed (`_xsd.py`, 54d997e and ffe6dd6, both after the baseline) |
| F104 | the guard fuzzer derived its denominator and never used it, so 61% of the guard was fuzzed zero times | **DEFECT (measured)** · ✅ FIXED 2026-09-06 | F99 fixed the POLICY half — the verdict map is derived from `protect-bash.sh` rather than hand-copied, with a `MIN_MAPPED_RULES` refusal. It never added the other direction: how many of those derived rules any SEED exercises. MEASURED 2026-09-06: **9 of 23 (39%)**. FOURTEEN rules had no seed at all — six DENIES and one HARD BLOCK (`writes_reference`) — while the run printed a healthy mutant count and *"no bypass found"*. A green with no denominator, inside the instrument whose job is to find that shape everywhere else. ★ The cause is the same one F99 names and it is worth stating twice: the seed list was grown from shapes that had ALREADY BITTEN US, which is exactly why the untested ones were untested — a bug history is not a specification (CLAUDE.md #36) | Thirteen seeds added → **21 of 23 (91%)**. Two are structurally out of reach and NAMED rather than silently missing: this fuzzer mutates command SYNTAX around a fixed operand, so it cannot construct `carriers_truncated` (a pathological INPUT SIZE) or `timeout_over_cap` (a numeric FIELD of the payload); both are unit-tested instead, and if a mutator ever reaches one the floor rises by itself. TWO REFUSALS, because a fraction nobody acts on is not a fix: a reachable rule with no seed → rc 2 naming it, and a seed that makes NO policy rule true → rc 2 (it is fuzzed for nothing and inflates the mutant count — this caught two of my own new seeds, because `DURABLE` matches durable FILE NAMES and not any path under a root). ★ WHAT THE SEEDS IMMEDIATELY FOUND: **36 bypasses** across the four newly-seeded rules, fixed as B6–B9; the fuzzer is now rc 0 and its control still rediscovers 101 bypasses from a planted defect |
| F105 | two copies of the BaseX tooling drifted apart, and nothing could say which was ahead | **DEFECT (measured)** · ✅ RECONCILED 2026-09-06 | The toolkit repo ships `tools/basex/`; the LIVE install is a second, separate git repo at `<modding root>/tools/basex` with **no remote and a history disjoint from the toolkit's**. Movement between them is a file port, never a merge, and there was no check on it at all — the artifact builds run from the LIVE copy while releases ship the OTHER one. MEASURED 2026-09-06: **11 of 18 files differed**, and the live install had been carrying an uncommitted-then-committed change (`staleness.py` at dbd8f1f, 2026-08-31) the shipping repo NEVER RECEIVED. ⚠ **The instrument problem is the finding.** A byte-level `cmp` said 11 files differed and I read the direction as *the live install is ahead, porting would destroy work* — wrong twice over: **3 of the 11 differ by CRLF vs LF over IDENTICAL content** (CLAUDE.md #56, where `diff -rq` once reported 52 differences of which 12 were real), and the direction test itself returned *"genuinely diverged"* for all 8 remaining files because it passed MSYS-style `/c/...` paths to Windows Python, printing a FileNotFoundError traceback above every confident verdict. Re-run correctly: 7 of 8 were the live install BEHIND the toolkit | Direction established PER FILE against BOTH histories — does each copy match any commit of the other? — rather than assumed from size or date. 11 files ported toolkit → live, 1 (`staleness.py`) live → toolkit, each byte-proved; `stage-manifest.json` correctly stays local (gitignored build artifact naming a machine path). Both trees now byte-identical bar that, 81 tests passing in each from the same sources. ★ The durable rule: **when two copies of one tool exist, the question is never "do they differ" but "which commit of the other does each match"** — and a byte comparison cannot answer that on a Windows checkout |
| F106 | a wrapper flag whose VALUE is a WORD became the command name, walking past all three hard blocks | **DEFECT (measured)** · ✅ FIXED 2026-09-06 | `_verb_token` skips any `-flag`, and after a wrapper it also skips a `_WRAPPER_ARG` — but that pattern matches only a number, `{}` or `+`. A flag whose value is a WORD therefore left the word standing as the verb. MEASURED: `env -u X4_GAME rm -rf <game>` → verb `x4_game` → **ALLOW**; likewise `sudo -u root rm` → `root`, `env -C /tmp rm` → `tmp`, `timeout -s KILL 5 rm` → `kill`, `stdbuf -o L rm` → `L`. `nice -n 5` and `xargs -I{}` worked only because their values happen to be numeric or braced. ★ **`env -u VAR cmd` is not exotic — it is what this workspace types to clear a root, and it was typed several times in the session that found this.** ⚠ The fuzzer could not find it: its four wrapper mutators all use a BARE wrapper or a NUMERIC argument, because they were grown from wrappers that had already bitten us — F104's lesson one layer in | `_WRAPPER_VALUE_OPTS` maps each wrapper to the flags consuming the NEXT token. **Per wrapper, not a union set**: skipping a value a flag does NOT take would consume the REAL verb and return its first argument — a miss, strictly less safe than the bug (twin: `nice -u rm` still resolves `rm`). TWO SITES, ONE TABLE — fixing `_verb_token` alone left 10 measured bypasses, because the two git rules run their own token scan. Five mutators derived from the GRAMMAR of a wrapper prefix now cover the shape, and they found those 10 within seconds of existing. Per-item replay over 17,268 commands: 5 moved, none tightened, none loosened — only the tracked `cwd`, because `env -u FOO cd /dir` now resolves to `cd` |
| F107 | model-facing hook output above 10,000 characters was FILED, and no hook could tell | **DEFECT (measured)** · ✅ FIXED 2026-09-07 | Claude Code files a hook's model-facing output above **10,000 CHARACTERS** and shows the model a ~2 KB preview. No error, exit code unchanged — the failure is indistinguishable from success. MEASURED on CC 2.1.263, four arms per channel, head+tail sentinel, discriminating on whether the TAIL survives: bare stdout 500 BOTH · 10,000 BOTH · 10,001 HEAD · 30,000 HEAD; additionalContext 500 BOTH · **9,950 BOTH** · 10,001 HEAD · 30,000 HEAD. The 9,950 arm is load-bearing — its raw stdout was **10,032** characters and it still arrived whole, so the cap is on the CONTENT the model receives and the JSON envelope does NOT count. Two of our hooks could exceed it: `session-canary.sh` at **36,638 characters** on a 400-item report (crossing at ~81 lost files against 473 tracked), and `x4validate-on-edit.sh` at ~75 findings (real finding lines measured mean 135 chars). ★ **The failure was INVERTED AGAINST SEVERITY**: one lost file sails under the cap, a directory-level loss is the one that gets filed — and because the preview keeps the HEAD, what survived was the inventory and what was cut was *"do not re-run whatever wrote it"*, the only actionable line. Reported as a lead by an outside project (Genesis); every number here re-measured locally, and **two of the four hooks they named are structurally bounded** (`check-reference-version.sh` ~400 chars, `search-scope.sh` ~600) — they picked by CHANNEL where the discriminator is STRUCTURE | `x4_bound` in `_x4-env.sh` owns the constant and is applied ONCE, before either renderer, so jq/python parity is structural rather than two implementations agreeing. `x4_advise` calls it, covering both `additionalContext` hooks for free; `session-canary.sh` writes bare stdout and calls it explicitly. `x4canary._LIST_CAP = 40` caps all three item lists and DISCLOSES the bound, and the recovery directive now prints BEFORE the list in both layers. E2E against live CC with a falsifying control: bounded → the model reports TRUNCATED, unbounded → NEITHER. 8 probes in `test-hooks.sh` |
| F108 | a temp-file reclaim keyed to the CURRENT process, which can never collect the orphans that matter | **DEFECT (measured)** · ✅ FIXED 2026-09-07 | `_effective._write_db` creates `<db>.<pid>.tmp` at `sqlite3.connect`, and its `try:` carried **`finally: con.close()` and nothing else** — closing the HANDLE and never removing the FILE. Every exception between creation and `os.replace` leaked a fully materialised temp permanently; the only cleanup sat in the `os.replace` OSError handler, the one failure mode somebody had already imagined. The sharpest case is architectural rather than exotic: `_mutation.refuse_if_mutating()` is called INSIDE that block **by design** (at the stamping site, so no other entry path slips past it), so any store build overlapping a mutating gate deposited one. MEASURED in the live registry: **three orphans** from 2026-08-26 at 12:50 / 12:52 / 12:54, each **zero rows in every table INCLUDING `meta`**, so they died before `con.commit()` — a leak, never a half-written store; build-then-replace is atomic and the installed store was never at risk. ★ **The generalisable half is the reclaim, not the missing `finally`**: the startup sweep is keyed to `os.getpid()`, so it only ever matches a leftover from THIS process — and an orphan's creator is by definition gone. A reclaim scoped to the current process **looks present in review and is unreachable in fact**. Contrast `gates/mutation_probe.py`, which records a pid but restores BY NAME from a pristine directory: same ingredient, load-bearing in one and decorative in the other | `except BaseException: → unlink → raise`, copied from the shape already proven in `_registry.save:513-541`. Nested rather than chained so the handle is closed BEFORE the unlink (Windows refuses to unlink a file it holds open), and the original exception is re-raised so cleanup cannot mask the diagnosis. PID stays in the tmp name — `test_effective.py:168` pins it deliberately, from a measured 3-racer WinError 32 incident. Automatic sweeping of FOREIGN temps deliberately NOT added: deciding whether a stranger's temp is abandoned means deciding whether its pid is alive, and on Windows `os.kill(pid, 0)` maps to TerminateProcess. Two negative twins added — the file previously asserted "no temp left behind" on the HAPPY PATH ONLY |
| F109 | the installer parity gate compares the two installers as TEXT, so it cannot see a behavioural divergence in either direction | **SCOPE LIMIT (measured)** · ⚠ OPEN 2026-09-07 | `tests/test_installers_agree.py` is the only thing asserting that `install.sh` and `install.ps1` behave alike. MEASURED 2026-09-07: **11 tests carrying 13 substring assertions over the two files' TEXT** — `'cp "$f" "$f.bak-' in sh`, `"function Test-SameDir" in ps`. Two consequences, and the second is the one that bit. (a) A substring is satisfied by PROSE: delete a mechanism, leave the comment that names it, and the assertion still passes (CLAUDE.md #37b — a guard written `assert "FLAG" in text` was satisfied by the comment mentioning FLAG). (b) Every one of those assertions was hand-written AFTER a specific divergence was found, so the gate encodes a BUG HISTORY, not a specification of what agreement means (#36). It can only ever re-detect what somebody already detected. MEASURED denominator: **6 divergences on this file pair**, 5 of them named in the gate's own docstrings and in `install.ps1` — all five `fixed in bash, absent in PowerShell` — and the 6th found 2026-09-07 by a hand-written twin, **in the previously unseen direction**: install.ps1 built its rewrite list from `$srcRoot` correctly while install.sh ran `grep -rl` over the destination and edited the user's own file. The gate was green through all six. ★ The direction matters because the four earlier ones taught "check whether PowerShell got the bash fix", which is a one-way habit that structurally cannot find the sixth | NOT FIXED — recorded, not repaired. The honest repair is behavioural: drive both installers over the same fixture and compare OUTCOMES (files present, contents, backups left, rc), which `test_install_over_existing.py` already does for 20 cases via its `["sh","ps1"]` parametrisation — F109 is the observation that the file NAMED for parity is the weaker of the two instruments, and that new parity claims belong in the behavioural suite rather than as a fourteenth substring |
| F110 | a command name arriving through SUBSTITUTION reached no rule at all, and the fuzzer that should have caught it holds the operand constant | **DEFECT (measured)** · ✅ FIXED 2026-09-08 | MEASURED against the live hook: `rm -rf "<game>"` denies, `$(echo rm) -rf "<game>"` **ALLOWS**, past all three hard blocks. `resolve_verb` only splices a BARE name out of the assignment table, so a substitution is unresolvable by construction. An unknown OPERAND still reaches the conservative branch; an unknown VERB reaches nothing. ★ Why 2,568 fuzz mutants missed it: the fuzzer's VERB axis has FOUR mutators and every one is a SPELLING of a literal name — none is a SUBSTITUTION. A bug history is not a specification (F104 one axis over). ⚠ My first corpus measurement was VACUOUS and I reported it: `facts(payload)` against a `(payload, roots)` signature, 28,963 identical TypeErrors swallowed by `except Exception: continue`, printed as "0 of 28,963 (0.000%)" | Scoped to substitution in the VERB position WITH a rooted operand in the SAME segment, forced by pricing over 28,989 real commands: any unresolved verb = 679 (2.3%, mostly `$JQ`/`$UV` holding a PATH), substituted verb anywhere = 834, **shipped rule = 4 (0.014%)**. Predicted "under 20" for the first, wrong by 34x. Harness now asserts a CONTROL before believing any zero |
| F111 | one resolution applied ONCE, and three rules that re-derived their own walk from the raw text | **DEFECT (measured)** · ✅ FIXED 2026-09-08 | `facts()` resolves verbs once into `seg_cwd`; `search_rooted_reference`, `search_rooted_workspace` and `durable_python_open_w` re-derived their own walks and never saw a resolved verb. MEASURED: `RM=rm; $RM -rf <ref>` True, but `GP=grep; $GP -rn x <ref>` **False** — all three are DENY rules. Two independent paths answering one question. The arc RECRUITED: an in-arc commit rewrote that very comprehension to close 21 wrapper bypasses and did not adopt the resolved segments while it was there | Both walks routed through `seg_cwd`; verified per item that the variable spelling now denies like the plain one and an unrooted `grep -rn foo .` is untouched |
| F112 | a write rule filtered redirects to TRUNCATE, so an APPEND into the read-only tree was allowed | **DEFECT (measured)** · ✅ FIXED 2026-09-08 | `writes_reference` was built from `trunc_redirect`. MEASURED: `echo x > <ref>/w.xml` deny, `echo x >> <ref>/w.xml` **ALLOW**, `tee -a` deny — two spellings of one append disagreeing under a rule whose message is "never write into it". ★ The tell is a NEIGHBOUR with a wider channel on a less protected tree: `writes_documents` three lines away uses the wider `writes_any`. Truncate-only is correct where it came from (the game/profile advisory, whose reason is that an append cannot truncate) and was carried to a tree with a different policy. **A predicate copied between rules brings its old rationale with it** | Built from the full redirect set. Corpus: 0 of 29,001 affected — which is the state in which such a hole survives, since nothing makes it visible |
| F113 | a prune list whose entries carry a DESTRUCTIVE second meaning; the entry added to fix a leak destroyed the recovery store | **DEFECT (measured)** · ✅ FIXED 2026-09-08 | `X4_COPY_PRUNE` is skipped on the way IN and `rm -rf`'d from the DESTINATION on the way out, twice. An in-arc fix added `.claude/backups` to stop the SOURCE's 217 backup files travelling, and the path inherited the second meaning: an upgrade with `--over-existing` erased the destination's entire recovery store, rc 0, "install complete", the word "backup" nowhere in the output. Both installers, both methods, reproduced independently. Live install: **982 files, 60 MB, 33 known-good snapshots back to 2026-06-22**. ★ Three things made it invisible: the file STATES the hazard directly above the offending entry (prose adjacent to a defect is not a guard); `x4lock` deliberately leaves that directory UNLOCKED so the lock precheck could not see it; and the `--over-existing` warning enumerates TOOLKIT files, so the store never appeared in what was at risk — the reader's model is *replaced*, the behaviour was *erased* | Moved to `X4_KEEP_LOCAL`, whose documented semantics were already right: **the category existed and the path was in the wrong one**. Test asserts SUBJECT and TRAVEL as ONE EXACT SET (destination survives AND source does not arrive), because the obvious repair satisfies one by breaking the other, plus a CONTROL that a real build artifact is still pruned |
| F115 | the dev checkout and the public mirror FORKED, and the fork was inside `_freshness.py` — the module whose job is detecting drift. The store read STALE forever from dev while the banner blamed mods that had not moved | **DEFECT (measured)** · ✅ RESOLVED 2026-09-12 by running the toolkit FROM the mirror | Folding the store's OWN recorded detail vector with each tree's code: `meta fingerprint_content = 0cd79c95…`, dev `_fold` → `87f1f21d…` (ENGINE_SOURCES=7), mirror `_fold` → `0cd79c95…` (=8). So the store was built by MIRROR code and dev could never read it fresh: `x4live oracle` refused rc 3 permanently while all 133 manifests were byte-identical and `x4modlist changed` reported "no change, file-for-file". ★ The banner names five mod-related causes and **none of them was the cause** — it has no vocabulary for *"you are standing in a different tree than the one that built this"*. ★★ The reconciliation I first planned was DISPROVEN as safe: ancestry proves DIRECTION, not BEHAVIOUR, and **27 of 104 incoming files (26%) resolve paths ABOVE the package root**; porting the mirror's `control_bytes.py` into dev would have run `git ls-files` against a NON-repo, reducing the sweep to zero files **while still exiting 0** — the exact defect that gate exists to prevent, introduced by the fix for a different one | Not reconciled. `X4_TOOLKIT` repointed at the mirror (env outranks the locked config, so no file was edited and `x4lock` was not bypassed); `X4_ORACLE_LOG` carried over. Verified: oracle rc 0 103/103, `gates/oracle.py` 241/241, validator byte-identical on **8 of 8** real mods |
| F114 | FIVE gates reached a verdict line with no floor under the population it described: "All properties hold" over three unrun checks, "No per-mod regression" over an EMPTY intersection, a completion sentinel over an empty directory, a header-only artifact from an empty input, and a three-channel audit whose third channel was uncounted | **DEFECT (measured)** | 5 gates, all rc 0; each now refuses or returns 3 | v3.1.0 |
| F116 | a PAUSED game "goes silent" shipped as fact; measured live, a paused game answers | **DEFECT (measured)** · ✅ FIXED 2026-09-13 | a user-facing hint sent people with a silent channel to unpause; 4 of 4 separate processes got answers from a paused game | corrected everywhere it appeared; `tests/test_livepipe.py` fails if it returns |
| F117 | a `globals` paging test that could not go red, and a lua mutation harness anchored on text the mod no longer has | **DEFECT (measured)** · ✅ FIXED 2026-09-13 | the "stops paging" mutant SURVIVED on pristine code | the test now requires OK on both pages; the stale harness copy retired with the dev repository |
| F118 | a Windows clone checks the shipped mod's XML out as CRLF, and `deploy-mod.py` compares raw bytes | **DEFECT (measured)** · ✅ FIXED 2026-09-14 | `content.xml` is `i/lf w/crlf` on the author's checkout; a fresh worktree had BOTH XML files CRLF | pinned `mods/**/*.xml text eol=lf`; `tests/test_line_ending_pin_is_obeyed.py` now covers the XML |
| F119 | `x4lock status` run from a git worktree reports the untracked `.claude/x4-paths.env` as MISSING | **DEFECT (measured)** · ✅ FIXED 2026-09-14 | rc 1 in a worktree, rc 0 in the main checkout, 39 of 39 locked in both | a linked worktree's absent local config is waived and announced, the configured toolkit's copy demanded instead; `tests/test_x4lock.py` |
| F120 | the CLAUDE.md ratchet counted a checkout's CRs as content | **DEFECT (measured)** · ✅ FIXED 2026-09-13 | GREW +872 on a file 52 characters UNDER its baseline | each CRLF now counts as one newline; `tests/test_claude_md_budget.py` |
| F121 | `x4effective who-sets` with no property answered with the ENTITY's origin while its properties had been changed, and could not see removals at all | **DEFECT (measured)** · ✅ FIXED 2026-09-14 | asked who set `energycells` it said `base`; 33 of its 49 properties were last set by something else | origins and file-level removals now reported; `show` still lists no removals (OPEN) |
| F122 | `ask.py xq` certified a negative over a query Git Bash had rewritten before Python saw it | **DEFECT (measured)** · ✅ FIXED 2026-09-14 | `collection("x4raw")//ware` arrived as `.../ware`: `NEGATIVE CONFIRMED` rc 0, where PowerShell counted 14,068 | `--file`; under Git Bash an argument-query zero refuses (rc 4) and every result shows the query as received |
| F123 | `bin/unpack-reference.sh` took the LAST `"buildid"` in the Steam manifest, which is a beta branch's, not the installed build | **DEFECT (measured)** · ✅ FIXED 2026-09-14 | this machine's manifest: AppState 23660954, `public_beta` 23524486 -- the script's grep returned 23524486 | one depth-1 reader, `x4_acf_buildid`, for the script and the hook; a test bans a third hand-rolled grep |
| F124 | a client request had NO size cap; a request over ~1997 bytes tears the game-side pipe read down, and the teardown was followed the same session by a game CRASH | **DEFECT (measured)** · ✅ FIXED 2026-09-14 | P6 ramp: 1997 bytes round-tripped, 3998 tore the pipe down (`The pipe has been ended`); the game crashed on the next Esc | `ask()` refuses any request over `MAX_REQUEST_BYTES` (1900) before writing; `ffi-census` and `query ffisyms` gated off by default (`X4_LIVE_ALLOW_FFI`) |
| F125 | a live refusal read the helper's ABSENT load marker as "most likely not installed" -- but the mod arms on GAME LOAD, so at the MAIN MENU the marker is legitimately absent and the install is fine | **DEFECT (measured)** · ✅ FIXED 2026-09-20 | in game 2026-09-20 at the main menu with no save loaded: marker absent, extension present and enabled, refusal said "not installed or not enabled" | the install is MEASURED directly (`helper_is_deployed()` over `mods("active")`) instead of inferred from the log; with deployment unknown the message names BOTH causes |
| F127 | the guard's scope rules matched a search rooted AT a big tree and at the toolkit/game roots, never ABOVE one — so when the dev repo was retired and `X4_TOOLKIT` moved, the parent of the 60 GB `reference\` tree stopped being named by any root and a recursive search there became ALLOW. The ancestor was covered only BY COINCIDENCE | **DEFECT (measured)** · ✅ FIXED 2026-09-21 | 2026-09-21 through the DEPLOYED hook: `cd <parent-of-reference> && grep -rl x .` → ALLOW, while the same shape at the toolkit root, at `reference/` itself and at the game root all → deny. 3 controls denying is what made it a hole, not a rule change. Found by `hook_false_positives` going red (146 of 17,133 shared commands moved). **6 commands affected, not the 3 the first text-based classification found** — the other 3 never contain the string `reference` | `contains_root()` makes a PROPER-ANCESTOR search fire the same rule; 11 tests incl. a split-root fixture, 3 of 3 clause mutants killed, 8-case end-to-end probe on the deployed hook |
| F126 | `build-effective.sh` rebuilt the x4eff DATABASE correctly but skipped the COVERAGE STAMP and still exited 0, so a freshly built index reported STALE indefinitely and refused to back any negative claim | **DEFECT (measured)** · ✅ FIXED 2026-09-20 | 2026-09-20: DB timestamp 02:15:41 with 10,970 documents, while `coverage-x4eff.json` kept mtime 00:22:03 and the PREVIOUS content fingerprint (`1f071ac8` vs the `39c7120a` stamped on `coverage-x4raw.json` minutes earlier); exit code 0 | not fixed. Cause identified -- a staging `rm` hit `_eff/tree/libraries/controlschemes_for_movie_capturing.xml` while BaseX still held it (`Device or resource busy`) and the stamp step never ran. A retry with the lock released stamped correctly, so it is NOT reproduced |
| F128 | `compute_load_order` modelled the COMMUNITY rule (ASCII case-sensitive sort, each mod as early as its dependencies allow) and nothing ever compared it with the ENGINE -- it decides every collision winner | **DEFECT (measured)** · ✅ FIXED 2026-09-25 | 685 of 5,134 ordered pairs inverted (372 file classes, two launches of the engine's own log); every existing winner test still passed | engine rule (case-insensitive UPPERCASE order, repeated passes) implemented: 0 of 5,134 inverted, held to every new log by `gates/load_order_oracle.py`. Signature order = apply order is INFERRED, not probed (AUDIT LO-2/LO-3 OPEN). **2026-09-26: CLOSED** -- the in-game `scripts/load-order-probe.py` run PASSED (order 12/12, apply order 3/3 by its add/replace collision pairs, `ß` after `sz`, DLC before mods): apply order is now MEASURED (LO-2 FIXED); LO-3's dependency shapes FIXED `8f66d1c` |
| F129 | the freshness fingerprint missed three ways an artifact goes stale: a mod MOVING between install roots, code that shapes the store but was not in `ENGINE_SOURCES`, and a reference (game) update, which it blamed on mods | **DEFECT (read)** · ✅ FIXED 2026-09-25 | `ENGINE_SOURCES` named 8 modules, 3 of them never on the store path, and missed 2 that are; x4xref stamped 1 of the 3 roots it indexes; BaseX staleness watched the game-root extensions only | root folded into the content axis; sources re-derived by tracing a build (7 now); reference is its own axis; every index stamps every root it reads |
| F130 | x4diff compared ATTRIBUTES only: an edit whose value is element TEXT was invisible, and the three-way called such an author edit VERBATIM | **DEFECT (measured)** · ✅ FIXED 2026-09-25 | `<replace sel=".../@min">5</replace>` → 999 read "changed files: 0", rc 0; ~48% of installed-mod ops carry their value in text | text keyed as a `text()` pseudo-attribute, positional across children; diff ops keyed by payload identity (839 of 7,022 ops shared tag + sel) |
| F131 | `ask.py` scored a zero against the WHOLE database's denominator whatever the query actually addressed | **DEFECT (measured)** · ✅ FIXED 2026-09-25 | `db:get('x4eff','no/such/typo.xml')//ware` → "NEGATIVE CONFIRMED over 10970 of 10970 documents", rc 0 | every call that reaches a database is classified; a partial, foreign or non-literal address is refused rc 2. Coverage licence revoked before DROP DB and judged per root (BX-3, BX-4) |
| F132 | the safety hooks guarded Bash, Edit and Write but had NO matcher for the PowerShell tool or NotebookEdit, so every guard was one tool choice away from not existing | **DEFECT (measured)** · ✅ FIXED 2026-09-25 | `Remove-Item -Recurse <reference>` through the PowerShell tool ran unguarded; deleting the Edit/Write guard kept all 3 hook suites green | PowerShell parsed by its own AST and translated onto the ONE Bash rule set; unparseable or unmodelled writing commands ASK; a test pins that every file-changing tool reaches a guard |
| F133 | a FILTERED `find -delete` was EXEMPT from the delete rules rather than scoped by them, and `truncate`, `dd of=` and nested `cmd`/`powershell` carriers had no rule | **DEFECT (measured)** · ✅ FIXED 2026-09-25 | `find <saves> -name '*.xml.gz' -delete` and `find <reference> -name '*.xml' -delete` both ALLOW; a test ASSERTED the hole | filtered finds feed the in-tree rules (a cache-name-only filter stays exempt); truncate/dd judged as clobbers; carriers walked. Fuzz then found `stdbuf -o L find <game> -delete` ALLOW (paths read from token 1), also fixed |
| F134 | a PreToolUse hook that TIMES OUT does not block, and the hooks ran with a 5 s timeout at up to 7.6 s under load | **DEFECT (measured)** · ✅ FIXED 2026-09-25 | 1.6-3.8 s idle, 4.1-7.6 s under load, ~0.05 s of it analysis; later, five wedged PowerShell carriers committed 100 s against the 30 s timeout | timeout 30 s; per-call process spawns removed (protect-bash 221-236 → 124-126 ms, protect-files 726-810 → 279 ms); one 15 s translation budget per hook call. The 30 s value itself is pinned by no test |
| F135 | gates that examined NOTHING reported a pass: 0 checked, 0 planted, an unreadable input or a crash repeated twice all read as green | **DEFECT (measured)** · ✅ FIXED 2026-09-25 | `diff_truth` planted 0, found 0 and printed "RESULT: exact" rc 0; `toolkit_usage` rc 0 with no baseline; `provenance_audit`, `consistency_audit` and `cross_tool` audited a STALE store as current, and `claims_audit` refused it with rc 5, which `run-gates.sh` counts as FAIL | a shared floor (`_env.nothing_checked`) and stale-store refusal (`_env.stale_store_refusal`), rc 2 = could not judge; findings outrank refusals |
| F136 | the per-edit hook's `x4validate --file` and the full run gave DIFFERENT verdicts on the same file, and `--file` silently dropped flags | **DEFECT (measured)** · ✅ FIXED 2026-09-25 | a nested patch for an uninstalled target: ERROR rc 1 under `--file`, INFO rc 0 in the full run; `--file` printed "OK: no issues found" with no denominator | both paths share one sel check; `--file` states its denominator and lists every check it did not run; a dropped flag is a degraded skip (exit 3) |
| F137 | BaseX staging read an EMPTY packed-DLC answer as a failure and fell back to a hard-coded mini-DLC pair, indexing both mini-DLC twice | **DEFECT (measured)** · ✅ FIXED 2026-09-25 | 142 documents (deep-equal duplicates at identical paths) indexed twice in x4raw | the answer is used as given; a failure to ask refuses rc 2. A database built before the fix keeps its duplicates until rebuilt |
| F138 | `register_rederivation` credits an entry to ANY existing path it cites, including a path the entry quotes as EVIDENCE of the defect rather than as a check of the fix | **SCOPE (measured)** · ⏳ OPEN | 2 of the 6 entries reviewed for AUDIT DC-3 (F60, F73) were "covered" only by such a mention | open. The rest of the register's covered entries are NOT classified |
| F139 | `mods("active")` records a mod it leaves out (a REQUIRED dependency missing, disabled or cyclic; an unreadable manifest) only into a `dropped=` list the CALLER must pass, and no active-scope caller passed one | **SCOPE (measured)** · ✅ FIXED 2026-09-26 | 0 of 125 installed mods excluded on this install (MEASURED 2026-09-26), so today's cost is zero -- the #23 shape; 16 of 20 active-scope call sites silent before `f634e30` | `mods()` always returns its exclusions (`ModList.dropped`); `dropped_note` is the one line; all 20 sites disclose, held by an AST ban with an EMPTY allowlist (`f634e30`). Also fixed: an engine-excluded nested-patch target was called "DISABLED" |
| F140 | `scripts/test-hooks.sh` assigned its sandbox to `TMP`, which Windows exports, so every native child inherited it; a Windows process whose `TMP` exceeds 260 characters spins forever in `CreateProcessW` the first time it starts a child. And a KILLED identifier scanner (rc 1, no output) was reported as a leaked identifier | **DEFECT (measured)** · ✅ FIXED 2026-09-26 | 1 incident: 15.5 h / 13.75 h CPU, one scanner process, triggered by a long `X4_TEST_SANDBOX` (a default sandbox path is ~62 characters, well under) | `TMP` renamed in `test-hooks.sh` + `smoke-basex.sh` and banned by a test; `scan-identifiers.py` refuses rc 2 above 260; a leak needs the scanner's `::error file=` line (`9d16f2d`, `5609a14`, `3a44aa6`) |
| F141 | `_registry._active_filter` counts a REQUIRED dependency as satisfied by an enabled mod in ANY extensions root; the engine resolves a dependency only within its own root (a cross-root required dependency is refused in BOTH directions) | **DEFECT (measured)** · ✅ FIXED 2026-09-26 | 0 on this install (the profile `extensions\` is empty); any user with mods in two roots got a mod modelled as LOADED that the engine refuses | per-root dependency resolution and a game-root-first walk (`a20e859`), from probe round 3; a later-pass mod beside another root is disclosed UNMEASURED. **ADDENDUM 2026-09-27:** that fix keyed isolation by root POSITION and applied it to the Workshop root too (never measured); now keyed by root KIND and limited to game<->profile (`c45a54d`). Still UNMEASURED and disclosed: the Workshop root, case-only dependency ids, a later-pass game-root mod vs profile-root mods, a nested patch on a later-loading mod |
| F142 | `ask.py`'s guard against a query narrowing its own documents ENUMERATED narrowing shapes, so any unlisted rewrite still printed "NEGATIVE CONFIRMED" over the whole database; and `coverage.py` licensed a negative over an overlay catalog it could not read | **DEFECT (measured)** · ✅ FIXED 2026-09-26 | 11 of 12 release-reviewer rewrites (`[position() le 9]`, `subsequence()`, a `let` then a `where`, `collection#1(...)`, ...) certified a zero, rc 0; an unreadable catalog read `complete`, "NEGATIVE CONFIRMED over 10000 of 10000" | a TOKEN rule: any identity / position / node-order / module / indirect token anywhere in the query withholds the zero (rc 4, naming it); hits still stand (`692203f`). An unreadable catalog makes coverage `unexplained` and is named in `unreadable_catalogs` (`803ea90`) |
| F143 | the store's removal table records each removed node at its path AT THE INSTANT of removal, after earlier removals shifted its siblings, and `cross_tool` compared those paths with x4compat's BASE-tree targets | **DEFECT (measured)** · ✅ FIXED 2026-09-26 | one mod's 5 ware removals on the real install: 3 of 5 records carry a shifted path, 2 of them naming base wares the mod never removed | `cross_tool` replays the removals in application order over a copy of the base tree (`39ff4a3`); HARD rows checked 32/32 -> 37/37. Not modelled: a mod INSERT mid-sibling-list; other consumers of `removed` not checked (OPEN lead) |
| F144 | the installers' prune lists were a hand-kept copy of `.gitignore` and had drifted, so an install from a git checkout copied untracked derived files | **DEFECT (measured)** · ✅ FIXED 2026-09-27 (git sources) | 33 untracked files, 2.3 GB (BaseX databases, `_eff/`, coverage manifests, a `.pytest_cache`) reached the destination from one checkout | a checkout source copies `git ls-files`, filtered by the same lists (`c3f8e47`); derived BaseX paths are KEEP_LOCAL (never copied in, never deleted out). A non-git source still depends on the hand-kept lists |
| F145 | The PowerShell/cmd guard judges an UNRESOLVABLE PowerShell WRITE target by its text (as Bash writes are), not fail-closed; a delete whose target is computed (a list read with Get-Content, an indexed nested array) ASKS; writer methods on unknown objects (`$xml.Save(path)`) are not modelled; a cmd.exe delete of an unquoted spaced path is judged on rejoined operand spans (at most 12 words) | **SCOPE (measured)** · ✅ FIXED 2026-09-27 (the gaps the release review found; these residuals stated) | failing closed on writes MEASURED at +28 false-positive asks and 0 catches over 50,061 historical commands; computed delete targets: 2 of 1,524 historical PowerShell commands | release review hooks lane `5f6f414` `60ff523` `a30d401` `a399e3c` `6d7e702` |
| F146 | Grep and Glob honour `.gitignore`, so a search rooted in a git-ignored folder (a game root kept under git with a whitelist `.gitignore`) sees nothing, and the packed-archive advisory explained the zero as packing | **DEFECT (measured)** · ✅ FIXED 2026-10-02 (hook + agent text) | Grep: 0 of 133 installed manifests (rg --no-ignore: 133); a subagent reported them absent | `search-scope.sh` runs `git check-ignore` on the root: Grep there DENIED with `rg --no-ignore` as the way out, Glob advised, the above-root advisory names `.gitignore`. Open: `.ignore`/`.rgignore` files are not read |
| F147 | A RELATIVE shell operand with no preceding `cd` reached NO path rule: the guard never used the payload's `cwd`, so `rm -f reference/...` from the folder holding reference/ was allowed while the absolute spelling denied | **DEFECT (measured)** · ✅ FIXED 2026-10-02 (Plan 2 lane F) | 4-row probe on the deployed guard: rows 1-2 ALLOW, 3-4 deny; a live Codex run overwrote the file (lane B) | `hook_facts.facts()` seeds `cwd_track` with the payload `cwd`, narrowed by a 53,828-command OLD-vs-NEW replay to 0 new asks and 0 new denies; `x4guard check` sends the caller's cwd. Open: the Codex shell `workdir` is invisible; `git clean`/unknown cmdlets stay unseeded |
| F148 | A bare `git clean -fdx` / `-x` / `-X` / `-d` or `git reset --hard` run FROM the game folder (or another X4 folder) with no folder named reached no rule (F147's open item): the game-root repo's `.gitignore` is `*`, so `-x` there removes the installation's untracked files | **DEFECT (measured)** · ✅ FIXED 2026-10-03 (Plan 3 lane J, decision J-Q1) | the deployed `x4guard check` from the game cwd: `git clean -fdx` -> allow; master's x4guard in a sandbox game cwd -> allow, this tree -> deny | new fact `git_wipe_from_session_dir` (seeded cwd, -x/-X/-d or reset --hard, exclusive of the explicit-folder ASK) -> DENY with a reason, never a prompt |
| F149 | `scripts/test-hooks.sh` 1/187 flake (seen twice under load, probe never recorded): NOT REPRODUCED, 0 of 16 runs under 24 CPU burners plus 3 other lanes | **SCOPE (measured, open)** | 16 runs (4 lanes x 4), 711-1187 s each; every run 186/1, the 1 being the pre-existing identifier finding in Plan 3 docs | a failing `decide()` probe now prints `[hook rc=N; stderr: ...]`, so the next occurrence names itself; open until it does |
| F150 | The write guard judged the PATH, never the FILE: `<game>/KNOWLEDGEBASE.md` was ALLOW while x4lock's read-only lock made the write fail with no word why; and `<game>/X4-NOTES.md`, where the instructions send notes, was hard-denied as a game file | **DEFECT (measured)** · ✅ FIXED 2026-10-03 (Plan 3 lane N) | a live Codex run could not save its notes (orchestrator, 2 probes through `x4guard check`) | exactly `<X4_GAME or X4_TOOLKIT>/X4-NOTES.md` (+ `.pre-4.0.md`) allowed on the resolved path; a read-only file x4lock manages (`x4lock.py protected`) is ADVISED with unlock/edit/relock; the game deny routes notes, facts, mod files. Adjacent hole (the `claude.md` / `agents.md` / `knowledgebase.md` name whitelist matched ANYWHERE in the game tree, MEASURED, 2 probes + controls) ✅ CLOSED 2026-10-04 in `17de4cc` (FX-S): those names are allowed at the project root only |
| F151 | With NO path config the guards defaulted `reference/` to `<toolkit>/reference` SILENTLY, and the two loaders chose the config file by different rules (bash honoured `$X4_CONFIG`, Python ignored it and walked up from the cwd past a named `$X4_TOOLKIT`); a third loader in `generate-baseline.sh` let the file beat the environment | **DEFECT (read + measured)** · ✅ FIXED 2026-10-03 (Plan 3 lane I) | planning census + the matrix test (17 bash/agree rows red before) | ONE rule in both loaders (`$X4_CONFIG` > `<tk>/x4-paths.env` > 3.x `<tk>/.claude/x4-paths.env` > none); state recorded, named once per session and by `x4doctor`; verdicts unchanged: replay 0 of 17,916 hook calls changed (3 passes x 2 hooks), 0 ERROR |
| F152 | Under a POSIX bash (Linux/macOS), a CRLF `x4-paths.env` (saved by any Windows editor) left a trailing CR on EVERY configured root when `_x4-env.sh` sourced it -- `X4_REFERENCE` included -- so every root compare in the guards could miss; Git Bash strips the CR, so no Windows run could see it | **DEFECT (measured in ubuntu:24.04; guard-verdict effect INFERRED)** · ✅ FIXED 2026-10-04 (Plan 3 CI lane, `0e5f7e9`) | CI run 37172347642 (ubuntu): the OpenCode renderer refused "X4_REFERENCE contains a line break" -- correctly | `_x4-env.sh` strips a trailing CR from the config keys only; `test_config_precedence_agrees.py` pins bash and Python reading one CRLF file to the same CR-free values |
| F153 | The apply_patch path parser read a patch by a grammar NEITHER agent uses: a header only at column 0. Codex trims the line first, so `  *** Delete File: <reference>/x` after an Add was a DELETE to Codex and Add content to the guard -- ALLOW; and OpenCode, whose own parser differs again, was judged by Codex's grammar | **DEFECT (measured)** · CRITICAL · ✅ FIXED 2026-10-04 (v4.0.0 review fix lane FX-G) | release review R2 (`codex --codex-run-as-apply-patch`, 12 shapes) | one port per agent: `parse_patch` = Codex 0.160.0's streaming parser, held to a MEASURED record of Codex over 67 shapes (`patch_grammar_oracle.json`); `parse_patch_opencode` = OpenCode v1.18.34's parser, held to the vendored original under node over 85 shapes; the OpenCode adapter uses its own. Before: 8 of 67 shapes had Codex touch a file the guard never named, 2 more named the wrong file |
| F154 | `cd "$X4_REFERENCE" && rm -rf libraries` (and `cd "$X4_GAME" && rm -rf extensions`) was ALLOW while `rm -rf "$X4_REFERENCE/libraries"` was refused: cwd_track never mapped a root VARIABLE to its root, so the directory after the cd was unknowable and every later relative operand reached no rule | **DEFECT (measured)** · PRE-ARC · ✅ FIXED 2026-10-04 (v4.0.0 review, fix lane FX-G) | release review R2-P1 | `subst_root_var`: a leading root variable is its configured root, for a `cd` target exactly as an operand's name already identified it; an assignment in the command still wins, an unset root is not substituted. History replay (52,276 Bash commands, facts OLD vs NEW): 2 verdict facts changed -- 1 new advisory, 1 new deny equal to the literal-path spelling's |
| F155 | The "lifting the reference/ OS deny asks" rule judged an icacls operand without the segment's cwd and without root variables, and knew a fixed interpreter list: `icacls "$X4_REFERENCE" /reset /T`, `icacls reference /reset /T` from the folder above it, and `"$X4_PYTHON" scripts/x4refguard.py remove` (the toolkit's own spelling) were ALLOW | **DEFECT (measured)** · ✅ FIXED 2026-10-04 (v4.0.0 review, fix lane FX-G) | release review R2-F2 | operands go through `subst_root_var` + `join_cwd` like every other path rule; a verb named by a variable, and the conda/pipx/poetry/pdm/hatch/rye/pixi launchers, count as runners. 6 clause mutants in verify-hook-tests |
| F156 | The guards RAN the path config as shell code (`set -a; . x4-paths.env`) while Python parsed it as data, and the agent could write the file: an `exit 0` line silenced every hook (silence is ALLOW) and `X4_GUARD=off` in it relaxed every deny | **DEFECT (measured)** · ✅ FIXED 2026-10-04 (v4.0.0 review, fix lane FX-S); PRE-ARC in its `exit 0` form | release review R1-F1 / R1-P1 | one grammar in both loaders (parsed, never run; code-shaped lines ignored and named); `X4_GUARD` only from the launch environment; agent writes into `x4-paths.env` denied. Grammar matrix in `test_config_precedence_agrees.py`; verdict replay 0 of 5,972 configured rows changed |
| F157 | F146's Grep deny asked `git check-ignore` about the ROOT, which answers "is the folder ignored", not "can Grep see its files": a `dir/`-style ignore (the toolkit's `reference/`) leaves every child visible, so Grep into a reference subfolder was wrongly denied | **DEFECT (measured)** · ✅ FIXED 2026-10-04 (v4.0.0 review, fix lane FX-S) | release review R1-F4 | search-scope.sh decides from the matching pattern: a directory-only pattern leaves the root visible (no deny); a last component matching any name (`*`) hides it (deny) |
| F158 | A heredoc INSIDE a carried command (`$(...)`, `bash -c`) was never stripped or routed: the top level rightly ignores a `<<` inside double quotes, so in `git commit -m "$(cat <<'EOF' ... EOF)"` the body reached the carrier walk as text and every body line was a command -- a message mentioning `> KNOWLEDGEBASE.md` was DENIED. Mirror image: `x=$(bash <<'EOF' ... EOF)` was ALLOW, because the shell test read the opener line's segments (`x=$(bash`) | **DEFECT (measured)** · PRE-ARC · ✅ FIXED 2026-10-04 (v4.0.0 review, fix lane FX-P2) | another lane's false-positive report, reproduced with x4guard | `_carrier_parts`: every carried command gets the top level's heredoc strip plus its shell-fed bodies; `_opener_feeds` also reads the command after a `$(`/backtick on the opener line |
| F159 | An UNQUOTED heredoc body (`<<EOF`) is expanded by bash, so its `$(...)` and backticks RUN -- but the whole body was stripped as data: `cat > notes.md <<EOF` / `$(<delete the game>)` / `EOF` was a silent ALLOW against a hard block | **DEFECT (measured)** · PRE-ARC · ✅ FIXED 2026-10-04 (v4.0.0 review, fix lane FX-P2) | found while reproducing F158 | `heredoc_substitutions`: an expanding body contributes its substitutions (quotes are letters there; backslash escapes; `$((` is arithmetic); a quoted delimiter contributes nothing |
| F160 | A toolkit COPY acted for ANOTHER toolkit: `_paths` located the config through an inherited `$X4_TOOLKIT` (and derived `<X4_TOOLKIT>/reference` from it), so a second copy's `x4refguard apply` resolved the FIRST copy's real reference tree -- 36 `_paths` importers plus x4config, x4doctor and unpack-reference.sh shared the shape | **DEFECT (measured: incident)** · PRE-ARC · ✅ FIXED 2026-10-04 (install red-team, fix lane FX-R) | the 2026-10-04 cold install red-team, live: killed after ~2 min, census 518,056 objects / 0 protected | `_paths.toolkit_root()` = --toolkit > the toolkit the module LIVES IN > `$X4_TOOLKIT` only outside the layout; a differing `$X4_TOOLKIT` gets one notice naming both; system-changing commands refuse without --toolkit |
| F161 | The INSTALLED toolkit's own suite -- which SETUP_PROMPT tells every user to run -- could not run: 3 collection errors, then (measured, full run) 142 failed + 43 errors, from tests that read repo-only content (agent/, docs/, .github/, git) and tests that took a CONFIGURED root for a PRESENT one (an install configures roots before anything is unpacked) | **DEFECT (measured)** · PRE-ARC · ✅ FIXED 2026-10-05 (second install red-team, fix lane FX-R2) | the 2026-10-04 cold install red-team #2 (BLOCKER 1), then real installs by both installers + the full installed suite | `tests/_layout.py`: `require_repo` (skip, REPO-ONLY reason, only in an installed layout AND only when absent) and `reference_unpacked`; conftest treats a configured-but-missing root as unresolvable |
| F162 | `x4doctor`'s `bash.path` row FAILED every stock Windows install: it judged the first `bash` on the Windows PATH (the WSL stub) although no installed hook starts bash from that PATH (Claude runs hooks in Git Bash, Codex uses its PowerShell commandWindows, the guards resolve Git Bash themselves) -- the instrument answered an ADJACENT question; it was also the whole rc 1 (PowerShell) vs 3 (Git Bash) difference | **DEFECT (measured)** · IN-ARC (4.0 doctor) · ✅ FIXED 2026-10-05 (fix lane FX-R2) | the 2026-10-04 cold install red-team #2 (BLOCKER 2) | the row reads the hook definitions (`windows_path_bash_sites`): FAIL only when a hook would start bare bash from a stub-first PATH, or no Git Bash resolves (with the `setx X4_BASH` line) |
| F163 | A test made its scratch folder UNREADABLE and UNDELETABLE: under Python 3.13 pytest's temp root grants access only through an OWNER RIGHTS ACE, and `icacls /setowner` -- even to the same owner -- dropped it; every run stranded one folder (20+ found) | **DEFECT (measured)** · IN-ARC (FX-R test fixture) · ✅ FIXED 2026-10-05 (fix lane FX-R2) | the orchestrator's pytest-temp census | `refguard_owner.take_ownership` grants the user full control back after the owner change; the test asserts write, read and rmdir |
| F164 | The `cross_tool` gate's read-only section (4) PRINTED "check NOT RUN" when the effective store could not be opened, and the gate still exited 0: 1 of its 6 NOT RUN sites printed without counting | **DEFECT (measured)** · PRE-ARC · ✅ FIXED 2026-10-05 (delta review, fix lane FX-T2, `23ea4c1`) | delta review C, reproduced on a corrupt store in scratch | the section calls `not_run()`, so the verdict is 2; a usable read-only store still passes |
| F165 | `import_gate` (conftest) treated a CONFIGURED root that does not exist as unresolvable in ANY layout, so in a checkout a gate whose import raised (TypeError, OSError) was skipped instead of erroring | **DEFECT (measured)** · IN-ARC (FX-R2 skip rule) · ✅ FIXED 2026-10-05 (fix lane FX-T2, `b78a55b`) | delta review C finding 3 | an UNCONFIGURED root skips in any layout; a configured-but-missing one skips only in an installed layout; in a checkout the import error re-raises |
| F166 | The needs-a-reference markers (3 test files) and `test_freshness`'s empty-extensions-root skip skipped whenever the reference was not unpacked, so a checkout whose NAMED reference lost `libraries/wares.xml` skipped instead of failing | **DEFECT (measured)** · IN-ARC (FX-R2 skip rule) · ✅ FIXED 2026-10-05 (fix lane FX-T2, `b78a55b`) | delta review C finding 4 | `_layout.reference_skip_reason`: no reference named, or an installed layout, skips; a checkout with a named broken reference runs and fails |
| F167 | `_layout.installed_layout` keyed on `agent/` alone, so a checkout that LOST `agent/` was treated as an install and every REPO-ONLY test skipped (17 `installed_layout(` and 31 `require_repo(` calls under tests/ at c6fb158) | **DEFECT (measured)** · IN-ARC (FX-R2) · ✅ FIXED 2026-10-05 (fix lane FX-T2, `b78a55b`) | delta review C finding 6 | installed = no `agent/` AND not this toolkit's own checkout (`.git` present AND `git cat-file -e HEAD:agent`); an install inside the game-root repo is still an install |
| F168 | 2 of 2 tests of shipped scripts (`scripts/codex-e2e.py`, `scripts/scan-identifiers.py`) SKIPPED unconditionally when the script was missing, though both ship in every layout | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-05 (fix lane FX-T2, `d3e5283`) | delta review C finding 5 | a missing script FAILS |
| F169 | 2 silently-trivial checks in `test_adapting_doc`: a missing toy adapter dropped the worked-example comparison with no skip, and the bare-`install.ps1` detector saw only `install.ps1 -Switch` | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-05 (fix lane FX-T2, `9ea7af8`) | delta review C finding 7; 4 of 9 shape twins RED on the old detector | a checkout FAILS without the adapter, an install skips (counted); the detector sees a bare script line and `powershell`/`pwsh` without `-ExecutionPolicy Bypass -File` |
| F170 | An installed toolkit inside ANOTHER git repo (the in-game method, into a game root under git) got 2 false reds: the export-ignore audit asked the enclosing repo, and the tmp-reassignment test read the enclosing index (empty population) | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-05 (fix lane FX-T2, `4670e2b`) | delta review C finding 8 | an installed layout skips the export-ignore audit (counted) and walks its own files; in a checkout both still fail |
| F171 | The installed-suite test proves COLLECTION only: `--collect-only` runs no test body, so a runtime false red in an installed tree is not covered by it | **SCOPE (measured)** · ✅ CLOSED 2026-10-05 as documented (fix lane FX-T2, `874f7f9`) | delta review C finding 9; cost ~31 s and ~0.1 GB (two venvs) | the test's docstring states the scope and cost; the full installed run belongs to the release's cold install check |
| F172 | The toy-conformance mutant run picked 12 rows, one spelled with backslashes; off Windows the engine buckets that row `windows_path_dialect`, 11 < `--min-cases 12`, and the CONTROL refused rc 3 -- the floor decided, not the mutant | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-05 (fix lane FX-T2, `66c9753`) | CI4 (ubuntu run on da93d2d) | rows filtered with the engine's own `classify()`; a red run must not be a min-cases refusal |
| F173 | `cross_tool`'s builder-idempotence check passes "0 vs 0 rows" on an EMPTY store: it compares two builds and cannot tell agreement from nothing built | **SCOPE (read, open)** · PRE-ARC · ⏳ OPEN | delta review C residual, reported by FX-T2, not fixed | open lead: require a non-empty build before the comparison counts |
| F174 | `x4guard conformance` decoded a reference guard hook that FAILED TO RUN (bash exit 127, empty stdout) as a CHECKED allow, so the run passed: CLI rc 0, "1 checked" | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-05 (fix lane FX-G3, `37c32c5`) | FX-G3 reproduction with a renamed hook | a reference run that cannot start, times out or exits non-zero is an error with a reason; the CLI refuses (rc 2) naming each case; one bash resolver (`c521e30`) |
| F175 | `scan-identifiers.py`'s account patterns checked only the FIRST occurrence on a line, so a placeholder earlier on the line (`C:/Users/tester/...`, an elided profile path) hid a real identifier later on it | **DEFECT (measured)** · PRE-ARC · ✅ FIXED 2026-10-05 (fix lane FX-G3, `9fa6c7a`) | FX-G3: `account_match` returned None on the mixed line | every occurrence is checked (`finditer`); the full-repo scan over 684 files stays clean |
| F176 | The OpenCode plugin judged every `bash` call as bash off Windows and as PowerShell on Windows, never reading OpenCode's configured `shell`: under a `pwsh` shell `Remove-Item -Recurse -Force <reference>/libraries` was judged as bash -- allow -- where the PowerShell verdict is deny | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-05 (fix lane FX-G3, `7107827`) | FX-G3 with `x4guard check` (`--shell bash` allow, `--shell powershell` deny) | `X4_OPENCODE_SHELL` > configured `shell` > `$SHELL`, fish/nu -> platform default, as OpenCode's `acceptable()`; `cmd` is refused inert. Residual: the configured shell's EXISTENCE is not checked, so OpenCode's own fallback for a missing shell is not mirrored |
| F177 | `x4refguard apply/remove`, `x4lock lock/unlock` and `bin/unpack-reference.sh` (3 mutating CLIs) compared the TOOLKIT (F160) but not the ROOTS: an exported `X4_REFERENCE` outranks the config, so `x4refguard apply --toolkit B --yes` with A's reference exported protected A's tree, exit 0 | **DEFECT (measured)** · IN-ARC (F160 residual) · ✅ FIXED 2026-10-05 (fix lane FX-B2, `0c76880`); inherited `X4_CONFIG` ✅ CLOSED 2026-10-06 (FX-B3 `b5087bc`, `2e34d3a`; FX-B4 `baeea35`) | delta review; `X4_CONFIG`: 3 Python mutating entry points + 1 bash | `_paths.env_root_conflicts`: an exported `X4_REFERENCE`/`X4_GAME`/`X4_MODS` differing from the acting toolkit's config refuses, naming both and `--reference`/`--game`/`--registry`. An inherited `X4_CONFIG` naming a config outside the toolkit (or a missing file) refuses too; `--reference` lifts it for x4refguard only (USER DECISION 2026-10-06: keep the refusal) |
| F178 | A path-config line the grammar refuses (5 reasons: shape, key, subst, operator, guard) was dropped SILENTLY by every Python tool, which fell back to a default with exit 0 | **DEFECT (measured)** · IN-ARC (the F156 grammar) · ✅ FIXED 2026-10-05 (fix lane FX-B2, `73a31f9`) | delta review C I1 | one stderr line per process (line:reason, never a value), `--paths` lists them, `x4doctor`'s `roots.config` FAILs on shape/subst/operator |
| F179 | Windows PowerShell 5.1 turns a native command's REDIRECTED stderr into a terminating NativeCommandError: in `install.ps1` the read-only OpenCode precheck died raw (exit 1), the OpenCode render failed on any warning, and the Layer-2 summary misreported | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-05 (fix lane FX-B2, `d524bf4`) | delta review, reproduced under powershell.exe 5.1 | all 4 such call sites go through `Invoke-X4Native` (error preference relaxed in its own scope) |
| F180 | `test_x4doctor` built `Ctx` without a toolkit, so the doctor acted for the CHECKOUT and read its per-machine `x4-paths.env`; the real-x4lock tests did the same (in CI, with no config, "1 missing" became "2") | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-05 (fix lane FX-B2, `73a31f9`) | CI2 | 6 Ctx sites (4 direct, 2 shared helpers) name a scratch toolkit; the real-x4lock tests carry their own `scripts/x4lock.py` and resolver |
| F181 | `run-gates.sh`'s log-dir containment compared `abspath` only, so a log dir that is a symlink or junction INTO the game or reference tree passed as "outside" | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-05 (fix lane FX-B2, `22350b3`) | delta review | both `abspath` and `realpath` forms are compared; the test uses a real junction/symlink |
| F182 | `scan-identifiers.py`'s profile-id walk read 2 known locations (Windows Documents, OneDrive Documents) and skipped an unreadable one in silence, so a Linux machine derived no id | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-05 (fix lane FX-B2, `22350b3`) | delta review; the PARTIAL exit: 1 walk over 3 locations | 3 locations (adds `~/.config/EgoSoft/X4`); the scan prints how many exist and PARTIAL when one exists but cannot be read. ADDENDUM 2026-10-06: a PARTIAL walk that found nothing exits 3, not 0 (FX-B3 `45efbf0`); its advice names only a remedy that works, and an unreadable location counts as existing (FX-B4 `efb4b3c`) |
| F183 | A delete whose command NAME arrives by substitution loses the delete ADVISORY: `$(echo rm) -rf <game>/libraries` (and the backtick form) is a plain ALLOW while `rm -rf <game>/libraries` gets the X4-directory delete advisory; the substituted verb is denied only when the operand is a root itself | **SCOPE (measured)** · ✅ FIXED 2026-10-06 (fix lane FX-G4, `b1e394f`, `893a319`, `64bce54`) | FX-DOC's lead; re-measured at c6fb158 with `x4guard check`, 6 probes | the advisory is back below a root. With it, reviewer G's PRE-ARC hard-block bypass `x=$(printf rm); $x -rf <reference>` (ALLOW in v3.3.1) is an unresolved verb: deny at a root or inside `reference/` |
| F184 | A root VARIABLE operand (`"$X4_REFERENCE/..."`, `"$X4_TOOLKIT/x4-paths.env"`, `$env:X4_REFERENCE`) was its root for a `cd` (F154) but not for the other rules: writes through it were ALLOWED | **DEFECT (measured)** · ✅ FIXED 2026-10-05 (fix lane FX-G2, `84330ba`) | FX-G2: 34 of 40 Bash and 6 of 8 PowerShell probes allowed; `rm "$X4_TOOLKIT/x4-paths.env"` only advised; `rm -rf "$X4_GAME"` advised instead of the game hard block | `prep()` applies `subst_root_var` to every operand; the config pass sends every root. After: 0 of 48; variable and literal spellings agree on 126 of 126 probes |
| F185 | Windows path ALIASES opened the protected file while the guards read another name: trailing dots/spaces, NTFS stream suffixes (`::$DATA`, `:alt`, `::$INDEX_ALLOCATION`) and 8.3 short names | **DEFECT (measured)** · ✅ FIXED 2026-10-05 (fix lane FX-G2, `7c0954d`) | FX-G2: 19 of 28 probes allowed (x4-paths.env, reference, the game via `X4 Foundations./` and its 8.3 form) | both normalisers drop stream suffixes and trailing dots/spaces per component; an 8.3 name resolves to its long form. After: 0 of 28 |
| F186 | `$((` was stepped over whole as arithmetic, so a command substitution INSIDE arithmetic (`echo $(( $(rm -rf <game>) + 0 ))`, and the backtick form) passed the game hard block | **DEFECT (measured)** · ✅ FIXED 2026-10-05 (fix lane FX-G2, `38416b9`) | FX-G2: 6 of 14 probes allowed | arithmetic yields the substitutions inside it; `$((cmd) )` is a command (one helper, `_dollar_double_paren`) |
| F187 | git accepts any unambiguous PREFIX of a long option, but the wipe rules matched only full spellings: `git stash --a` and `git clean --fo -X` deleted ignored files unseen | **DEFECT (measured)** · ✅ FIXED 2026-10-05 (fix lane FX-G2, `38416b9`) | FX-G2 in a scratch repo (`--al`, `--har`, `--fo` reached no deny) | `_git_long` matches prefixes for --force, --dry-run, --hard, --all, --no-all, --include-untracked, --message |
| F188 | A patch run spelled any way but the plain `apply_patch` was not judged as a patch: `codex --codex-run-as-apply-patch '<patch>'` (on Codex's shell PATH) deleted a file in scratch | **DEFECT (measured)** · ✅ FIXED 2026-10-05 (fix lane FX-G2, `3e3b0c0`) | reviewer A; FX-G2: 11 of 11 forms unseen | the single-quoted Codex form is READ as a patch; every other spelling of a patch run is REFUSED (found by the guards' own shell parser; no parser -> refuse) |
| F189 | A move to the Trash was not a delete: `gio trash <game>`, `gio trash <reference>/a.xml`, `kioclient5 move <game> trash:/` and a Finder `delete` through osascript were allowed, while `core.md` tells Linux/macOS agents to delete that way | **DEFECT (measured)** · ✅ FIXED 2026-10-05 (fix lane FX-G2, `47ad1f0`) | reviewer D-1; FX-G2: 16 of 24 probes wrong | `trash_paths` feeds rm's operand list for every trash command, so each takes rm's verdicts exactly; the Windows Recycle Bin form was already right |
| F190 | Python's `\s` is Unicode and bash's `[:space:]` is ASCII, so a config line holding non-ASCII whitespace (`X4_GAME<NBSP>=/g`) configured the TOOLS and was refused by the GUARDS: one file, two trees | **DEFECT (measured)** · ✅ FIXED 2026-10-05 (fix lane FX-G2, `ad4bd62`) | reviewer A's fuzzer: 18 value diffs in 400 lines; 0 after (seeds 1-3) | both loaders decide on ASCII whitespace and refuse (`shape`) a line holding any of the 23 non-ASCII whitespace characters |
| F191 | FIVE parser bypasses of the hard blocks: (E1) heredoc quote state reset per line, (E2) `EOF)` ending a heredoc inside `$(`, (E3) a heredoc piped into a shell on a LATER line, (E4) `cmd` reading its program from stdin, (E5) an ANSI-C `$'...\'...'` quote never closing -- each let `rm -rf <game>` through as ALLOW | **DEFECT (measured)** · PRE-ARC · SECURITY · ✅ FIXED 2026-10-05 (fix lane FX-G2, `ee040bc`) | reviewer E (E1-E5), each reproduced RED at the facts level and end to end | one heredoc walk carrying quote state across lines; `EOF)` terminates; a body flowing into a piped shell is commands; cmd stdin translated (unreadable -> ask); one shared quote walk with ANSI-C mode |
| F192 | Two rule gaps: (E6) `--work-tree`/`GIT_WORK_TREE`, `--git-dir`/`GIT_DIR` and `-c clean.requireForce=false` did not name or force a git wipe; (E7) icacls `/grant`, `/inheritance`, `/restore`, `/setowner`, `/substitute` and an x4refguard action through xargs or a variable did not count as lifting the reference/ protection | **DEFECT (measured)** · ✅ FIXED 2026-10-05 (fix lane FX-G2, `ee040bc`) | reviewer E (E6, E7) | the spellings name/force the wipe; every ACL-modifying switch lifts (ask); switch values are not paths |
| F193 | An agent could switch the guards OFF by writing `"env": {"X4_GUARD": "off"}` into a Claude Code settings file: a project `.claude/settings.json` `env` reaches every hook process at the next launch | **DEFECT (measured)** · ✅ FIXED 2026-10-05 (fix lane FX-G2, `dbaaf7d`; USER DECISION "block X4_GUARD there") | MEASURED on Claude Code 2.1.290 | `settings_guard.py`: file tools judged on the RESULTING content; any shell WRITE to `.claude/settings*.json` denied; Codex/OpenCode patches judged on their text; the session canary names any settings file that sets it |
| F194 | `test-hooks.sh` unset `X4_DECIDE_DUMP` with the other X4_* keys in its path-config section, so those config-write denies never reached the Codex conformance replay (E8) | **DEFECT (measured)** · ✅ FIXED 2026-10-05 (fix lane FX-G2, `eba4ab2`) | reviewer E8 | the harness switch is kept for that section |
| F195 | `codex-entry.ps1` accepted an ARRAY-valued `permissionDecision` (`@('deny') -cne 'deny'` is an empty, falsy array) that `codex-entry.sh` rejects | **DEFECT (measured)** · ✅ FIXED 2026-10-05 (fix lane FX-G2, `eba4ab2`) | FX-G2, red first with an array fault | a non-string decision fails closed; 46 of 46 after |
| F196 | `verify-hook-tests.py`'s silent? column credited the catch-all `test_TWIN_a_read_is_not_a_write` to EVERY predicate (E10) | **DEFECT (measured)** · ✅ FIXED 2026-10-05 (fix lane FX-G2, `dbaaf7d`) | reviewer E10; over 31 predicates | a failing test is credited only when it (or its class's FACT/KEYS) names the predicate; every predicate keeps >= 1 credited must-not-fire test, the lowest `verb_unresolved` at 3 of 8 |
| F197 | `verify-hook-tests.py` credited a mutant when a SAME-NAMED test in another class failed: 3 of 618 test method names exist in two classes | **DEFECT (measured)** · ✅ FIXED 2026-10-05 (FX-G3 patch, `a2c6590`) | FX-G3 (old parse True, new False) | failures parse as Class.method; targets qualify through an AST index; an ambiguous or unknown target refuses rc 2; all 143 targets resolve to exactly one test |
| F198 | The settings rule (F193) cannot see a settings file written from inside an opaque interpreter (`python -c`, a script) or by a RELATIVE write whose cwd is unknown | **SCOPE (open, accepted)** · ⏳ OPEN | FX-G2 residual, not re-measured here | accepted residual of F193; the session canary still names a settings file that sets `X4_GUARD` at the next start |
| F199 | A patch run whose runner NAME is built from a variable (`$P` with `P=apply_patch`) is not recognised as a patch run | **SCOPE (open, accepted)** · ⏳ OPEN | FX-G2 residual of F188, not re-measured here | accepted residual |
| F200 | An escaped backtick inside double quotes (`"\``) is read as a command substitution | **SCOPE (open, accepted)** · PRE-ARC · ⏳ OPEN | FX-G2 residual, not re-measured here | an ADVISORY false positive only, never a missed block |
| F201 | `cmd /k <cmd>` runs its inline command and THEN reads further commands from stdin, and `cmd /c cmd` hands stdin to a child cmd that reads it; the guard read neither stdin as a cmd program, so a program piped into either reached no rule | **DEFECT (measured)** · ✅ FIXED 2026-10-06 (fix lane FX-G4, `b1e394f`) | reviewer H; `cmd /k` measured in Git Bash | both forms read their stdin as a cmd program, like bare `cmd` (F191 item 4); `/c` and `/r` with another child take none |
| F202 | A root variable under a brace operator (`${X4_GAME:-/x}`, `${X4_REFERENCE%/}`, `:=`, ...) was not its root, and an unassigned root variable under `:-` read as UNSET, so the operator's default was judged instead | **DEFECT (measured)** · ✅ FIXED 2026-10-06 (fix lane FX-G4, `b1e394f`) | reviewer H | a root variable under any brace operator is its configured root; an unconfigured one still refuses a delete; another variable or an in-command assignment is not a root |
| F203 | The Codex/OpenCode adapters judged a shell-run patch after `cd $X4_REFERENCE` (or `$env:X4_REFERENCE`, or `apply_patch <<EOF` after such a cd) against the LITERAL path `<cwd>/$X4_REFERENCE/...` -- inside nothing, ALLOW | **DEFECT (measured)** · ✅ FIXED 2026-10-06 (fix lane FX-G4, `643b598`) | reviewer H | a `cd` target the shell EXPANDS (`$`, `%VAR%`, `~`, a substitution) raises PatchParseError -> the caller's inert refusal, naming the fix; single-quoted text stays literal. 16 refused forms, 3 literal twins |
| F204 | The settings rule (F193) read only a settings file's `env` block: `X4_GUARD=off bash .../protect-bash.sh` as a `hooks` COMMAND string switched that hook off and was ALLOWED | **DEFECT (measured)** · ✅ FIXED 2026-10-06 (fix lane FX-G4, `0da4749`, `16d0ec5`; USER DECISIONS 2026-10-06) | reviewer H; RED against e39f675 | `X4_GUARD`/`X4_GUARD_CHECK` in any key or string of the resulting file is refused, except inside the top-level `permissions` block (a rule string sets nothing) |
| F205 | A settings-file Edit whose `old_string` did not apply to the file as the guard read it was ALLOWED whatever its `new_string` wrote | **DEFECT (measured)** · ✅ FIXED 2026-10-06 (fix lane FX-G4, `0da4749`) | reviewer H (H-M1); RED against e39f675 | the `new_string` is judged on its own; a non-matching edit without the key stays allowed |
| F206 | A `file://` operand was read undecoded, so `X4%20Foundations` hid the game root from every path rule | **DEFECT (measured)** · ✅ FIXED 2026-10-06 (fix lane FX-G4, `b1e394f`) | reviewer H (H-M3) | a `file://` operand is percent-decoded before the path rules see it |
| F207 | `takeown /f <reference> [/r]` lifts the reference deny (ownership grants the right to rewrite the ACL) and reached no rule, while `icacls /setowner` asked | **DEFECT (measured)** · ✅ FIXED 2026-10-06 (fix lane FX-G4, `b1e394f`) | reviewer H (H-M5) | asks like the other lift forms (F192); elsewhere, or an ancestor without `/r`, is not this rule |
| F208 | `<<` inside `(( ))`, `for ((;;))`, `$[ ]`, `${ }` or an assignment subscript `a[ ]=` -- a SHIFT, not a heredoc -- opened a heredoc and hid every later line: `rm -rf <game>` on line 2 was ALLOWED | **DEFECT (measured)** · PRE-ARC · SECURITY · ✅ FIXED 2026-10-06 (fix lane FX-G4, `b1e394f`, `5c05e6f`) | reviewer H (H5); fuzz-guard's 6 syntax-class mutators | those regions are blanked for the heredoc-opener scan; `EOF` followed by a backtick ends a body; a real heredoc beside them still opens |
| F209 | Git config supplied by the ENVIRONMENT (`GIT_CONFIG_COUNT` with `GIT_CONFIG_KEY_n`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_GLOBAL`, ..., or `--config-env=...requireForce`) can set `clean.requireForce=false`, and an unforced `git clean` was judged as the no-op it is by default | **DEFECT (measured)** · ✅ FIXED 2026-10-06 (fix lane FX-G4, `b1e394f`) | reviewer H (H6) | any of them, prefixed or assigned anywhere in the command, makes `git clean` forced |
| F210 | What `echo`/`printf` PRINTS into a shell is that shell's program: `echo rm -rf "<game>" \| bash` (unquoted words too) was ALLOWED | **DEFECT (measured)** · PRE-ARC · SECURITY · ✅ FIXED 2026-10-06 (fix lane FX-G4, `b1e394f`, `5c05e6f`) | reviewer H (H7); allowed at e39853f and at v3.3.1 | the printed text is judged as the program; a printf format that cannot be reproduced is the inability path (ask); a quoted, ANSI-C or pathed `echo` is still the producer (fuzz finding) |
| F211 | A process substitution that IS the script (`bash <(echo rm -rf <game>)`, `source <(...)`, `. <(...)`) was not read as a program: ALLOWED | **DEFECT (measured)** · PRE-ARC · SECURITY · ✅ FIXED 2026-10-06 for an `echo`/`printf` producer (fix lane FX-G4, `b1e394f`, `16d0ec5`); other producers F219 | reviewer H (H8) | an echo/printf producer is judged fully; `bash -n <(...)` executes nothing and never asks or denies |
| F212 | The x4refguard lift rule (F192) asked on ANY unresolved word in an x4refguard call, so the toolkit's own `x4refguard.py apply --toolkit "$X4_TOOLKIT"` asked -- an IN-ARC regression of `ee040bc` | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-06 (fix lane FX-G4, `b1e394f`) | reviewer H (H9) | only the ACTION position names the action: `remove` and an unresolved action still ask; a variable in an option value does not |
| F213 | The OpenCode plugin's refusal for a shell it cannot judge said "set X4_OPENCODE_SHELL", and following it made the guards judge a cmd command as bash: `del /s /q <reference>\libraries`, `rmdir /s /q <reference>` ALLOWED under `--shell bash` | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-06 (fix lane FX-G4, `a0af3c8`) | reviewer G (G-I1) | `X4_OPENCODE_SHELL` can only CONFIRM the detected shell: disagreeing, every bash call is refused (inert); the refusal says to set OpenCode's own `shell` to pwsh, powershell or bash |
| F214 | A configured OpenCode `shell` (or `$SHELL`) that does not resolve to a program makes OpenCode fall back to its platform default, while the plugin judged the configured one | **DEFECT (read)** · IN-ARC · ✅ FIXED 2026-10-06 (fix lane FX-G4, `a0af3c8`) | reviewer G (G-M2), READ `packages/core/src/shell.ts` `select()` | the plugin checks resolvability (rooted: a file; bare: PATH/PATHEXT; Windows `bash`: Git Bash) and refuses rather than guess the fallback |
| F215 | The OpenCode plugin's `configShell` was module-global, so one OpenCode instance's config decided the grammar for every other instance in the process | **DEFECT (inferred, then tested)** · IN-ARC · ✅ FIXED 2026-10-06 (fix lane FX-G4, `a0af3c8`) | reviewer G (G-M3, INFERRED); the node driver now runs two instances | per plugin instance |
| F216 | `x4guard conformance` re-ran a reference deny/ask with `X4_GUARD_CHECK` and treated only exit 2 as special: exit 1 or 127 fell back to the FIRST run's verdict and counted as checked | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-06 (fix lane FX-G4, `56866be`) | reviewer G (G-M4) | any other non-zero re-run is an error, like a failing first run; twins: exit 0 keeps the deny, exit 2 stays inert |
| F217 | `verify-hook-tests.py`'s silent? column showed NONE for all 32 of 32 predicates: `failed_tests()` returns `Class.method` (F197) while `test_sources()` is keyed by method name, so no failing test was ever found -- a VERIFIER bug | **DEFECT (measured)** · ✅ FIXED 2026-10-06 (fix lane FX-G4, `141bb8c`) | FX-G4's coverage run; present at e39f675 | the lookup uses the method part; the G-OUT twin, which the mutant no longer reached after `64bce54`, gained an absolute operand (red with the clause removed) |
| F218 | F183's restored advisory joined a substitution fragment's relative "operand" to the SESSION cwd: debris such as `C=$(jq -r '.x' < f); bash -n -c "$C"` read as a delete in the game root | **DEFECT (measured)** · IN-LANE · ✅ FIXED 2026-10-06 (fix lane FX-G4, `64bce54`) before merge | 6 false X4-directory advisories over the 44,009-command replay | the advisory's operands are judged unseeded; `cd <game> && $(echo rm) -rf libraries` still advises |
| F219 | A process substitution run as a script whose producer is NOT `echo`/`printf` (`bash <(curl ...)`, `source <(sed -n ... f.sh)`) is allowed unjudged, as running a script file the guards cannot see into | **SCOPE (measured, accepted)** · ⏳ OPEN (USER DECISION 2026-10-06) | 4 of 44,009 replay commands (all ordinary work; they asked before the decision) | accepted residual of F211; the same class as running a script file |
| F220 | A Codex/OpenCode patch to a `.claude` settings file is judged on its RAW text, not on the resulting file, so a patch adding a `permissions` entry that merely mentions `X4_GUARD` is still DENIED (the file-edit tool exempts that block, F204) | **SCOPE (read, accepted)** · ⏳ OPEN | FX-G4 residual, not re-measured here | a false DENY on a rare edit, never a missed one; use the file-edit tool |
| F221 | Four ways to weaken the guards through settings or the `.claude` tree are ALLOWED: (a) `"disableAllHooks": true` in a settings file; (b) `X4_CONFIG`, `X4_GAME`, `X4_PYTHON` or `PATH` in a settings `env` (moves every root, or the guards' interpreter); (c) a Write through a junction to `.claude` (`mklink /J jl .claude`, then `jl/settings.json`); (d) a shell REPLACEMENT of `.claude` (`mv <dir> .claude`, `cp -r x/.claude .`, `ln -s` onto `settings.json`) | **SCOPE (measured, open)** · ⏳ OPEN (USER DECISION 2026-10-06: record only for v4.0) | reviewer H, all 4 MEASURED allowed | not fixed in 4.0; the OS-level `reference/` protection, once applied, still blocks a delete inside it whatever the guards do |
| F222 | Hints named a command that REFUSES in the very case they were printed for: `x4doctor`'s `layer2.reference` / `roots.reference` (apply on a tree without the sentinel; the unpack with no `--reference` under an exported `X4_REFERENCE`, or under a foreign `X4_CONFIG`; a bare `x4refguard apply`/`remove`), `unpack-reference.sh`'s lift steps and Layer-2 hints, and the stale-reference session message (relative paths) | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-06 (FX-B3 `c412708`; FX-B4 `892970c`, `3e4b404`; FX-Z `c13afcf`, `14becad`) | reviewers F and I; each hint run against its refusal | every hint is an absolute command with `--toolkit` and `--reference` (the tree judged); one the flags cannot lift says to clear `X4_CONFIG` first |
| F223 | The installers took an INHERITED root variable for user intent: an exported `X4_TOOLKIT` was a "named" destination and `X4_GAME` a named in-game destination, so `--yes` (or no terminal) installed into whatever the shell happened to carry | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-05/06 (FX-B2 `d524bf4`: X4_TOOLKIT; FX-B3 `0255bc3`: X4_GAME) | delta review; 2 of 2 inherited root variables an installer reads as a destination | an inherited value is a default, said out loud; `--yes`/no terminal refuses naming its source; an explicit flag proceeds |
| F224 | Bash and Python read DIFFERENT files for the same `X4_CONFIG` spelling: `x4-paths.env/` and (Windows) `x4-paths.env.` / `x4-paths.env ` were read by every Python tool and "missing" to every guard, which fell back to the default reference root | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-06 (fix lane FX-B4, `e69c8fb`) | reviewer I: 6 of 16 spellings disagreed, now 0 | the bash loader takes the spelling Python opens (trailing separators; on Windows trailing dots/spaces) and exports it, so the guards' name checks see the file read |
| F225 | `bin/unpack-reference.sh`'s `_x4_canon` turned a bare RELATIVE path into `/<name>`: `X4_CONFIG=x4-paths.env` from the toolkit folder was refused as foreign (Python accepts it), and a relative `X4_REFERENCE` was refused as DIFFERENT from the config's own tree | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-06 (fix lane FX-B4, `e69c8fb`) | reviewer I | relative paths resolve against the cwd; the check judges the file the loader read; one spelling on Windows. Residual leads F229 |
| F226 | `x4doctor` read a MISSING reference tree as "not unpacked yet" (TODO: run the ~27 GB unpack) although x4refguard says only "not unpacked yet, or renamed or moved" -- for a tree the user moved, the advice was a needless re-unpack | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-06, PARTIAL (fix lane FX-B4, `892970c`) | reviewer I | UNKNOWN (exit 3), in x4refguard's words, when `<toolkit>/.claude/.reference-buildid` exists or a finished unpack sits at `<toolkit>/reference`; PARTIAL: a move that leaves neither signal still reads TODO |
| F227 | CI's install legs wrote `X4_TOOLKIT` into the Windows runner's REAL `HKCU\Environment`, which persists across steps, so a later leg inherited an earlier leg's toolkit | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-05/06 (FX-B2 `d524bf4`; FX-B3 `6a209da`; FX-B4 `572fc57`) | every install leg that runs on Windows: the PowerShell legs, then the 2 of 2 bash legs with no `if:` | each leg exports its own `X4_INSTALL_ENV_REGKEY` under `X4ToolkitTests` and drops `X4_TOOLKIT`; a test requires every such leg isolated with a unique key |
| F228 | On macOS, a privacy-protected (TCC) `~/Documents` may read as UNREADABLE, so `scan-identifiers.py` would report PARTIAL (exit 3) on a machine with nothing to find | **SCOPE (inferred, open)** · ⏳ OPEN | unverified on a Mac; a simulated EPERM is tested | fails closed (exit 3, never clean); a permission refusal no longer crashes the note |
| F229 | Two FALSE refusals in the root comparisons, both failing closed: `_x4_canon` resolves `..` lexically in a not-yet-existing tail, and an `X4_REFERENCE` ending in a dot is reported DIFFERENT from the same tree in the config | **SCOPE (reported, open)** · ⏳ OPEN | FX-B4 leads, not re-measured here | a refusal with both paths printed, never a wrong action; open leads |
| F230 | A prefix assignment holding an UNQUOTED multi-word substitution named the verb: `A=$(echo a b) rm -rf <game>` (and a backtick form, and `<reference>`) took `a` as the command, so every hard block passed | **DEFECT (measured)** · PRE-ARC (v3.3.1) · ✅ FIXED 2026-10-06 (fix lane FX-G5) | J2 item 3; E2E 3 of 3 spellings deny -> ALLOW, now deny | `_verb_token` collapses an assignment word's substitutions before tokenising |
| F231 | A substituted verb through a variable was hidden by any EARLIER token holding `$(`: `x=$(printf rm); A=$(true) $x -rf <reference>` was allowed | **DEFECT (measured)** · IN-ARC (G-OUT, F183) · ✅ FIXED 2026-10-06 (fix lane FX-G5) | J2 item 3 | only an UNCLOSED substitution (a cut fragment) still refuses the command position |
| F232 | The process-substitution rule (F211) read `bash -n` from ANY `-n` word -- an `echo -n` inside the substitution, or a `-n` after the script -- and took an option's VALUE (`-o pipefail`) as the script operand: `bash <(echo -n rm -rf <game>)` and `bash -o pipefail <(echo ...)` were allowed | **DEFECT (measured)** · IN-ARC · ✅ FIXED 2026-10-06 (fix lane FX-G5) | J2 item 2; 4 of 4 probe shapes allowed, now deny | residual NOT false-positive only (reviewer K, MEASURED): this fix read a lone `-` as the script (`bash - <(...)` deny -> ALLOW, F242), and `bash -n +n <(...)` stayed allowed (F243); `bash -o noexec <(...)` is judged, not exempted |
| F233 | A shell-patch `cd` operand that merely starts and ends with a single quote (`''$X4_REFERENCE''`, `'a'$X'b'`) was dequoted as literal text, though the shell expands it | **DEFECT (measured)** · IN-ARC (F203) · ✅ FIXED 2026-10-06 (fix lane FX-G5) | J2 item 1; 3 spellings x 2 forms | only one fully single-quoted word is literal; residual (reviewer K, MEASURED): MIXED quoting with no expansion (`'<REF>'/libraries`, `"<REF>"'/sub'`) was still taken with its quotes -- 2 of 3 spellings, closed by F245 |
| F234 | `git -c include.path=<file>` (any `include.*`/`includeIf.*`), any `--config-env`, and an in-command `git ... config ... clean.requireForce false` before a clean left an unforced `git clean -dx` in the game folder allowed | **DEFECT (measured)** · IN-ARC (F192, F210) · ✅ FIXED 2026-10-06 (fix lane FX-G5) | J2 item 5; 2 of 2 allowed, now ask | the in-command form is order-blind (a config AFTER the clean also asks) |
| F235 | `${V:+word}` / `${V+word}` of a variable the command never assigned resolved to "" -- `rm -rf "${PATH:+$X4_GAME}"` was allowed | **DEFECT (measured)** · PRE-ARC · ✅ FIXED 2026-10-06 (fix lane FX-G5) | J2 item 9 | an unassigned variable may be set: the alternate word is judged -- but ONLY it: the empty (unset) branch was LOST by this fix (`rm -rf "${NOPE:+zz}<reference>"` deny -> ALLOW) and is RESTORED by F241, which judges every value |
| F236 | Bash and Python still read different files for `x4-paths.env/.`, `./x4-paths.env/./`, `sub/../x4-paths.env/.`, `<tk>/x4-paths.env/.` and (Windows) `x4-paths.env::$DATA` | **DEFECT (measured)** · IN-ARC (F224) · ✅ FIXED 2026-10-06 (fix lane FX-G5) | J2 item 4; 5 of 7 probe spellings disagreed, now 0 of 25 table rows | open lead: a NAMED stream (`x4-paths.env:other`) is left as written and not measured |
| F237 | `.claude/.reference-buildid` was in neither installer's keep-local nor prune list, so installing from an unpacked (non-git) source tree copied it and `x4doctor` read a fresh install as a MOVED reference tree | **DEFECT (measured)** · IN-ARC (F226) · ✅ FIXED 2026-10-06 (fix lane FX-G5) | J2 item 7; 2 of 2 installers | keep-local: never copied in, the destination's own kept |
| F238 | `verify-hook-tests.py`'s silent? column looked a failing test's source up by bare METHOD name, pooling the sources of same-named tests in different classes | **DEFECT (read)** · IN-ARC (141bb8c) · ✅ FIXED 2026-10-06 (fix lane FX-G5) | J2 item 8; 3 shared names in test_hook_facts.py | keyed `Class.method`, as failures and targets are |
| F239 | ADAPTING.md never told an adapter to pass a settings write's TEXT, and `x4guard check` had no way to say "this is the whole file": every adapter built from the doc denied every `.claude/settings*.json` write, and Codex's `Add File` over a settings file that held X4_GUARD was denied where the Write it mirrors is allowed | **DEFECT (measured)** · IN-ARC (F193) · ✅ FIXED 2026-10-06 (fix lane FX-G5) | conformance DISAGREE #204 (Codex, toy) and #224 (toy) on 44c2d00 | `--content`; an add-only patch is judged on its own text; residual: an OpenCode `write` over a file that holds the key is still judged as raw text (F220, false deny) |
| F240 | Two conformance rows (the `exit 0` config probes) replayed as ALLOW against the harness's DENY: the harness rewrote the shared config they were judged with, and the replay re-runs every row against the kept sandbox's END state. The R7-3 drift assertion was masked on 44c2d00 by the #204 failure before it | **DEFECT (measured)** · IN-ARC (test harness) · ✅ FIXED 2026-10-06 (fix lane FX-G5) | 2 of 210 replayed rows | the two rows get their own never-rewritten toolkit folder; other rows sharing a later-rewritten file are not audited |
| F241 | The F235 fix judged `${V:+w}` / `${V+w}` of an UNASSIGNED variable as the alternate word ALONE and lost the empty branch: `rm -rf "${NOPE:+zz}<reference>"`, `"${NOPE+zz}<reference>"` and `"${NOPE:+./junk}<game>"` went deny -> ALLOW (and `"${NOPE:+x}$X4_GAME"` deny -> advise) | **DEFECT (measured)** · IN-ARC (regression of FX-G5 f03d19b) · ✅ FIXED 2026-10-06 (fix lane FX-G6) | reviewer K C1; 4 of K's 11 rows worse than at 44c2d00; now all 11 (+4 new) deny where a root can result and allow the 4 ordinary forms | `resolve_variants`: every state (unset / empty / set) PER NAME, exhaustive to 4 names; past 4, each name in each state with the rest uniform (a mixed 5-name combination is not enumerated); carriers carry every value too, except a PowerShell host payload (one value) |
| F242 | The F232 fix took a lone `-` as the shell's script operand: `bash - <(echo rm -rf <game>)`, `sh - <(...)`, `bash -x - <(...)` went deny -> ALLOW | **DEFECT (measured)** · IN-ARC (regression of FX-G5 f03d19b) · ✅ FIXED 2026-10-06 (fix lane FX-G6) | reviewer K C2; 3 of 3 deny -> allow | `-` and `--` end the options; the NEXT word is the script |
| F243 | `+n` after `-n` (`bash -n +n <(echo rm -rf <game>)`) runs the script and was exempted as a syntax check | **DEFECT (measured)** · IN-ARC (F211, F232) · ✅ FIXED 2026-10-06 (fix lane FX-G6) | reviewer K I3; allowed at 44c2d00 and 47d06cd | only the LAST n-toggle counts, in clustered words (`-xn`, `+xn`) and as `+o noexec`; `-o noexec` is still judged, not exempted (false positive only) |
| F244 | A word holding a `${...}` with a blank, a substitution holding a separator, or a wrapper option VALUE with a blank offered a piece of itself as the command name: `A=${X:-echo a} rm -rf <game>`, `A=$(echo a; echo b) rm -rf <game>`, `env -C $(echo /tmp ) rm -rf <game>` were ALLOWED | **DEFECT (measured)** · PRE-ARC (allowed at 44c2d00) · ✅ FIXED 2026-10-06 (fix lane FX-G6) | reviewer K I4; 3 of 11 c3 rows allowed, now 11 of 11 deny | `_raw_words` (span-aware words) for the verb; `_spanning_view` collapses a separator-holding substitution in an ADDITIONAL view, so no fact is lost; open: other verb-keyed scans that tokenise on their own (e.g. `_git_destructive`'s token walk) were not audited for spans |
| F245 | A shell-patch `cd` operand with MIXED quoting (`'<REF>'/libraries`, `"<REF>"'/sub'`) was taken literally, quotes and all -- a path under no root -- so a Codex/OpenCode shell patch wrote into reference/ unjudged | **DEFECT (measured)** · IN-ARC (F203, F233) · ✅ FIXED 2026-10-06 (fix lane FX-G6) | reviewer K I1; 2 of 3 cd spellings | only ONE fully quoted word is dequoted; any other word holding a quote is refused |
| F246 | Only the literal `requireForce` in an in-command `git config` forced a clean: `"clean.require""Force"`, `clean.require\Force`, `K=clean.requireForce; git config $K false`, and a `HOME=/x` prefix (a global config the guard cannot read) left `git clean -dx` in an X4 folder allowed | **DEFECT (measured)** · IN-ARC (F234); the HOME form PRE-ARC · ✅ FIXED 2026-10-06 (fix lane FX-G6) | reviewer K I2; 4 of 4 allowed, now ask | ANY non-read `git config` in the command (any carried command too), order-blind; the read set is `--get*`, `--list`, `-l`, `--show-*`, `get`, `list`; a `git config user.name x && git clean -dx` now ASKS (the brief's rule) |
| F247 | The OpenCode adapter judged an `edit` of a Claude Code settings file on its NEW TEXT only, so two innocent edits (`"X4_GUA": "x"`, then `A": "x"` -> `ARD": "off"`) built `X4_GUARD=off` | **DEFECT (measured)** · IN-ARC (F193, F220) · ✅ FIXED 2026-10-06 (fix lane FX-G6) | reviewer K I5; 1 of 1 | the edit is APPLIED to the file as read and the result judged; an edit that does not apply here is refused when its text could form the key wherever it lands (fail closed); everything the text reading refused stays refused (a file already naming the key) |
| F248 | The settings rule gave a text apply_patch semantics (the add-only "replaces the file" reading) by SNIFFING it for `*** Begin Patch` / `*** Add File:`, whoever sent it | **DEFECT (read)** · IN-ARC (F239) · ✅ FIXED 2026-10-06 (fix lane FX-G6) | reviewer K M1 | only an explicit patch: the Codex adapter's patch calls, `x4guard check --patch` |
| F249 | `X4_CONFIG` = `x4-paths.env::$DATA/.`, `::$DATA::$DATA` or `::$DATA ` (Windows): Python read the file, the guards read none | **DEFECT (measured)** · IN-ARC (F236 residual) · ✅ FIXED 2026-10-06 (fix lane FX-G6) | reviewer K M2; 3 of 16 probe spellings disagreed, now 0 of 16 | any `::` beyond exactly one trailing `::$DATA` names no config in `_paths` and `x4doctor`; bash already found none (its `-f`), so no bash change |
| F250 | A `cd` whose target has several possible values (an unassigned `${V:+w}`) was judged by ONE of them -- the first landing under ANY root -- so from the toolkit or the game folder `cd "${NOPE:+x}<reference>" && rm -rf libraries` took the relative `x<reference>` (joined under the session folder) and the rm was an advisory | **DEFECT (measured)** · IN-ARC (regression of FX-G6 / F241, e39853f deny -> 8ed563f advise) · ✅ FIXED 2026-10-06 (fix lane FX-G7) | regression corpus #397: 1 of 432 replayed rows; FX-G6's own allk.txt row | `cwd_lanes`: one lane per value, up to 16 lanes; past 16 a lane takes the value under a configured root (the F241 rule). Cost: `cd "${NOPE:+x}<plain folder>" && rm -rf b` from an X4 folder is now an ADVISORY (the set branch lands under it) |
| F251 | A work tree named by an ASSIGNMENT in another segment (`export GIT_WORK_TREE=<X4 folder>; git clean -fdx`, `declare -x`, `GIT_DIR=`) reached no wipe rule: `_git_destructive` reads its own segment only | **DEFECT (measured)** · PRE-ARC (allow at e39853f too) · ✅ FIXED 2026-10-06 (fix lane FX-G7) | 5 of 5 roots ALLOW from a folder outside every root, in a 3 cwd x 6 root x 12 form matrix where every other spelling ASKS | named ask only (never the bare deny's per-segment pairs), so from an X4 folder it stays the deny; order-blind; PowerShell `$env:GIT_WORK_TREE` not probed |
| F252 | `git clean`'s dry run was `-n` or a prefix of `--dry-run` ANYWHERE: `-fdx -n --no-dry-run`, `-n --no-d`, `-e -n`, `--exclude -n`, `--e -n`, `-fe -n` delete (MEASURED, git 2.48.1) and were allowed | **DEFECT (measured)** · PRE-ARC (allow at e39853f) · ✅ FIXED 2026-10-06 (fix lane FX-G7) | 6 of 6 spellings x 2 forms allowed, now ask (`-C`) / deny (bare) | the last of `-n`/`--dry-run`/`--no-dry-run` wins; `-e`/`--exclude`/`--pathspec-from-file` consume a value; `--` ends options; a long prefix counts only when unambiguous among git clean's 14 long spellings. A clustered `-fdxn` is still NOT read as a dry run (false positive only) |
| F253 | Unicode line/paragraph separators shifted AST-indexed justification checks | **DEFECT (measured)** in the silent-swallow checker | 2 of 2 marker readers in that checker used Unicode-aware splitting | ✅ **FIXED 2026-10-07** — Split on LF after universal-newline reading; marked/unmarked twins |
| F254 | Edge sweep's blank environment still discovered the toolkit's private config | **DEFECT (measured)** in the gate fixture | 5 of 39 cells labelled unconfigured returned configured answers | ✅ **FIXED 2026-10-07** — Explicit empty fixture config; blank-config discovery twin |
| F255 | Historical replay fingerprint omitted its filesystem parser and PowerShell translator | **DEFECT (measured)** | 2 of 4 verdict dependencies could change without voiding a replay | ✅ **FIXED 2026-10-08** — Hash all four; mutate each dependency with an unchanged control |
| F256 | Hygiene counted quoted fixture/prose commands, unrelated status and tests outside project scope | **DEFECT (measured)** | 630 of 24,778 calls changed; all classified; 3 unsafe controls retained | ✅ **FIXED 2026-10-08** — Shared shell grammar and recorded cwd; matcher fingerprint refuses incompatible baselines |
| F257 | icacls operand filter discarded absolute slash paths as switches | **DEFECT (measured)** | Both CI OS jobs failed the same reset probe; 13 new reproduction failures | ✅ **FIXED 2026-10-08** — Shared operand/action parser; 200 mutants caught; 24,593 historical verdicts unchanged |
| F258 | Missing generation marker asserted a save load | **DEFECT (measured)** | User-confirmed menu capture printed save load; failing regression | ✅ **FIXED 2026-10-08** — Unmarked session is UNKNOWN; counts and new-game detection retained |
| F259 | Native short-name test assumed TEMP had no aliases | **DEFECT (measured)** | Windows CI expected RUNNER~1 while actual expansion returned runneradmin; local reproduction | ✅ **FIXED 2026-10-08** — Canonical native fixture independent of the guard; no-op expansion still fails |
| F260 | Short-name roots and target stems used different canonical forms | **DEFECT (measured)** | Four native audit failures reproduced; 741 unit and 73 audit tests passed | ✅ **FIXED 2026-10-08** — Shared lexical/native comparison; 202 mutants caught; 24,593 historical verdicts unchanged |
| F261 | Ubuntu skip ceiling described an older test population | **DEFECT (measured)** | 4,773 passed, 203 skipped; all 64 added skips attributed against earlier CI | ✅ **FIXED 2026-10-08** — Measured Ubuntu floor plus existing three-test margin; Windows unchanged |
| F262 | Cold verification stripped Git metadata and placed conformance under shared /tmp | **DEFECT (measured)**: the /tmp sandbox was setup; the three Git-metadata failures were TESTS that assume a repository, which the fix sidestepped by changing the population (see F269) | CI cold suite: four failures and 17 setup errors; main suite passed | ✅ **FIXED 2026-10-08** — Isolated committed-object clone under the test sandbox; cold precondition retained; focused regression controls |
| F263 | Windows ownership fixtures invalidated inherited protection or skipped its precondition | **DEFECT (measured)** in test setup | CI: 31 partial-protection failures/errors and three foreign-owner hint failures; native reproduction | ✅ **FIXED 2026-10-08** — Normalise fresh scratch ACLs before a root-only grant; owned doctor-hint fixtures; product guard unchanged |
| F264 | Git Bash ACL probes ran with administrator backup/restore bypass rights | **DEFECT (measured)** in test context and scope claim | Three newly reachable probes bypassed the deny; removal of both child privileges blocked all three, six controls worked | ✅ **FIXED 2026-10-08** — Disposable child strips only the two bypass privileges; full behavior suite 38/38 on hosted Windows; administrator gap documented |
| F265 | icacls switches spelled `//reset` (Git Bash) were read as PATHS, and icacls accepts every switch that begins with `/inheritance` (`/inheritancezzz:r` strips inherited ACEs), so both lifts of the reference deny were allowed | **DEFECT (measured)** | 12 unit failures across 3 tests; 2 of 2 new must-fire protect-bash probes allowed by the old hooks; real icacls.exe: 1 of 1 `/inheritance*` spelling applied, 14 of 14 other altered or shortened spellings rejected (`/inheritanc:r`, `/inh:r`, `/resetx`, `/res`, ...) | ✅ **FIXED 2026-10-10** — `_icacls_switch` normalises `//x` and the `/inheritance*` class; 432-row corpus 0 weaker; twins per clause; 1 equivalent mutant recorded |
| F266 | `run-release-gates.py` exited 0 with a stage pending (deployment parity), after `--through vht`, and again on `--resume` | **DEFECT (measured)** | 3 of 3 partial shapes exited 0 through the real `main()` | ✅ **FIXED 2026-10-10** — exit read from the checkpoint: 0 all passed, 1 failed, 3 pending/not run; 4 clause mutants caught |
| F267 | Gate report directories were checked against EXPORTED roots only; a root named only in `x4-paths.env` was not protected | **DEFECT (measured)** | 1 of 1 config-only reference root allowed as a report destination | ✅ **FIXED 2026-10-10** — roots resolved through `x4validate._paths`, required by `external_output`; a resolver that raises refuses |
| F268 | `run-gates.sh` expanded possibly-empty arrays bare under `set -u` (bash < 4.4 aborts) | **DEFECT (read)** — not reproducible on any bash here (all >= 4.4) | 3 of 3 `name=()` arrays | ✅ **FIXED 2026-10-10** — `${a[@]+"${a[@]}"}`; class-rule test with a twin |
| F269 | The release zip's population -- `git archive`, no `.git`, installed IN PLACE as the README says -- was never measured; F262 moved the cold leg to a clone, and `installed_layout()` read an in-place install (it has `agent/`) as a checkout | **DEFECT (measured)** | 238 of 239 test files run in an in-place install at 5063aec: 4,543 passed, 54 skipped, 37 failed/errors, all attributed (3 Git metadata, 26 layout, 7 my sandboxed HOME, 1 my TEMP placement); `test_install_over_existing.py` (361 tests) cut off at ~222 by the resource watchdog, unmeasured | ✅ **FIXED 2026-10-11** — one helper (`scripts/toolkit_layout.py`: a root `.git` tracking agent/) for the tests, x4lock and x4doctor; Git tests ask the shipped rules git-free; `verify-cold.sh --archive`; re-verified: all 16 affected files pass on a fresh in-place install of 647b591 |
| F270 | `edge_sweep`'s "unconfigured" cells are an EMPTY config, not NO config; the docstring called them the new user's state | **SCOPE (read)** | all unconfigured cells (one fixture) | 📝 **DOCUMENTED 2026-10-10** — the no-config state is `verify-cold.sh`'s |
| F271 | BaseX queries reached Java as a command-line ARGUMENT, decoded in the Windows codepage: a literal outside Windows-1252 arrived as `?`, and a zero was certified for a query nobody wrote; stdout was decoded with the locale and stderr is cp1252 | **DEFECT (measured)** | codepoint 63 arrived where 28779 was sent; every ask/coverage query containing such a character | ✅ **CLOSED 2026-10-11** (`4b7c5f7`) — query passed as a UTF-8 file via the single runner `tools/basex/basex_run.py`; stdout strict UTF-8; stderr forced UTF-8 |
| F272 | `refs` matched exact values of five attributes while its zero carried a complete DOCUMENT count; a document count says nothing about which reference forms were searched | **DEFECT (measured)** | the issue's worked example missed 18% of references | ✅ **CLOSED 2026-10-11** (`3cf199b`) — also searches bullet/@class, add_blueprints/@wares\|@macros lists, `macro.`/`ware.` spellings, and declares its scope; `$variable` and expression attributes remain out of scope, declared |
| F273 | Contested paths were a SET OF SPELLINGS, so one case alias was written twice; on a case-sensitive filesystem a differently spelled loose overlay would be silently dropped | **DEFECT (measured)** on Windows; case-sensitive effect **INFERRED** (no Linux machine) | 1 of 11,301 documents on the maintainer's install | ✅ **CLOSED 2026-10-11** (`d825d15`) — one entry per case-folded path; off Windows a case-folded loose lookup that refuses a folder holding two spellings |
| F274 | The same-parent duplicate check reported only the first duplicated key, attributed it to every co-adder, and dropped the parent SOFT row; 2 duplicated keys were never reported | **DEFECT (measured)** | 2 to 40 mods named where 2 or 3 provided the key; 6 rows on the maintainer's live modlist | ✅ **CLOSED 2026-10-11** (`65e2a10`) — one row per duplicated key naming its providers only; parent SOFT row kept; the survivor of a duplicate id is NOT engine-measured and the rows say so |
| F275 | Open lead from F274's live run: wildcard icon keys (`icon#ship_*`, `icon#upgrade_*`, `icon#module_*`) reported as duplicate HARD rows, while the engine log showed duplicates only where wildcard EXPANSIONS overlap | **LEAD (measured)** | see F276 | ✅ **CLOSED 2026-10-11** — superseded by F276 |
| F276 | x4compat reported a duplicate for every wildcard icon key two mods declare (HARD for same-parent diff adds, UNION-KEY for full icons.xml files); the engine compares EXPANDED ids | **DEFECT (measured)**; `.dds` alias and no-subfolder search **INFERRED** | live: 3 of 3 wildcard HARD rows and 2 of 2 wildcard UNION-KEY rows false (0 overlapping ids across all installed extensions: 178 exact / 769 with aliases) | ✅ **CLOSED 2026-10-11** (`d49fdae`, `373308c`) — compared by expansion; **residuals stay OPEN** (bare texture paths, diff-vs-full-file wildcards, wildcard vs explicit id, base/DLC as providers) |
| — | 3 suspected findings that were **NOT** defects | correct | see "Cleared" | — |

> F-numbers in this file are **local to this register** and unrelated to the F-series in the
> 2026-08 toolkit audit.

## Fix verification (2026-08-12)

Store 9,227 → **9,257 entities**; attr rows 124,690 → **224,527**. Old-vs-new differential keyed
`(kind, name, vpath, prop)` with packed mods read via `_cat`:

| class | result |
|---|---|
| entities removed | **0** |
| changed VALUE on a pre-existing prop key | **0** |
| changed ORIGIN on a pre-existing prop key | **0** |
| props removed from surviving entities | **0** |
| added *single-segment* props on existing entities | **0** ← proves depth-1 grammar byte-identical |
| `properties.*` duplicate rows | **0** |
| entities added | **30**, all attributed: mini_01 4 + mini_02 22 (F4) + vro 4 (F5) |

Suite **413** (+16). Gates: consistency 250/250 **0 disagreements** (67.5% of eligible rows are new
multi-segment keys, so ~169 of the 250 exercised the new depth), provenance **0 mis-attributed**,
oracle **228/228 100%, 0 false-OK**, determinism **0 non-deterministic** (`x4stats macro` 405→590 B,
stable), similar_audit **808 pairs 0 violations** (unchanged — see F8), noop/tool_properties/
cross_tool/registry_provenance all green, perf_guard **0 regressions** (worst 1.3× on a small mod).
Depth tests proven-to-fail by forcing `MAX_PROP_DEPTH=1` (3 fail, restore → 12 pass).

---

## F1 — Depth-1 `<properties>` flatten · **DEFECT** · confidence 99%

`_effective.py:189` `flatten_with_prov` (docstring: *"one level of children"*), `_stats.py:166`
`flatten_macro_props`, and `_similarity.py:66` (reuses `_stats`).

MEASURED over 339 vanilla ship macros: depth-1 = 54 attr kinds / **4,094** occurrences;
depth-2 = 40 kinds / **9,197** occurrences. More than twice as much data invisible as visible, and
the invisible half is *the entire flight model*: `physics/drag` (8 axes x 225 ships),
`physics/inertia` (3), `jerk/*`, `steeringcurve/point`, plus `software/software` (1,330).
The store holds **zero** rows matching `%drag%` across 9,227 entities.

Severity: it silently defeats any handling/manoeuvrability question — including W3/M2, whose stated
premise was CPSDO "movement and handling changes".

⚠ **Root-cause correction (2026-08-12).** This entry originally also blamed the depth-1 flatten for
`x4similar` scoring two ships identical when they differ only in flight model. **That attribution was
wrong.** `_similarity.py:69` filters the flattened vector through `_WEIGHTS`, an 8-key whitelist that
contains no handling keys at all — so x4similar never saw drag regardless of flatten depth, and
fixing F1 did not change its output by a single pair (audit: 808 pairs, 0 delta). Split out as **F8**.
Caught by reading the consumer instead of assuming the producer's defect propagated.

The merge is sound — `x4validate --tier b` resolves depth-2 selectors correctly (proven:
`zzz_personal_overlay_E`' four `physics/drag/@*` ops on `ship_cpsdo_s_hengdao_02_macro` report 0
errors while the store shows no drag row). **The tree is complete; the projection is lossy.**

## F2 — The store indexes three kinds only · **UNDOCUMENTED SCOPE** · confidence 95%

`_effective.py:291` `BUILDABLE_KINDS = ("ware", "macro", "job")`.

MEASURED over the 4,395 XML files shipped by the 112 enabled extensions: **29.8% sit in an area
`x4effective` cannot see.** Of that, `md`/`aiscripts` (393) and `t/` (344) are *deliberately* out of
scope — x4xref and x4validate cover them, correctly. The real gap is **~182 files across 24
`libraries/*.xml` registries**: `shipgroups` (17), `ships` (15), `god` (15), `factions` (8),
`constructionplans` (6), `stations` (5), `loadouts` (5), `modules` (4), and more.

These are the same union-merged shape as `wares.xml`, and `_extract_registry(tree, kind, child_tag,
klass_attr, vpath, rec)` is **already generic** — wares and jobs are simply the only two rows anyone
added. Extending coverage is mostly a data table, not new machinery.

Concrete cost: `x4effective` has nothing to say about two of our own six overlays
(`libraries/factions.xml`, `libraries/god.xml`, `libraries/material_library.xml`). Verifying those
during the 08-12 overlay re-review required hand-building a merge tree.

## F3 — Macro coverage overall · **MOSTLY CORRECT SCOPE** · confidence 96%

> ✅ **FIGURES REFRESHED 2026-08-22** against a post-F34 `x4eff` and a post-F37 store. The verdict
> is unchanged and the scope is confirmed, not widened:
>
> | class group | missing / total | coverage |
> |---|---|---|
> | **balance-relevant** | **3 / 1,916** | **99.8%** |
> | galaxy map | 1,131 / 1,440 | 21.5% |
> | characters/npc | 1,710 / 1,796 | 4.8% |
> | other | 26 / 2,744 | 99.1% |
>
> 7,896 macros defined, **5,026 in the store**, 2,870 missing (36.3%). Compared against ALL entity
> kinds, not `kind='macro'` alone — a ship macro is filed under `ship`, and checking only `macro`
> is the wrong population. Cross-check: **0 store names are absent from x4eff**, so the store never
> invents a macro. The grouping is not byte-identical to the 2026-08-12 one (an `other` bucket was
> added), so read these as re-derived rather than a like-for-like delta.
>
> **The 3 remaining balance-relevant misses were all `escape_pod`'s, and were an F37 symptom, not a
> coverage gap.** Chasing exactly those three numbers to ground is what uncovered F37 — including a
> false-pass path in Tier B. Writing "3 unexplained misses" here instead was one keystroke away.


MEASURED: 7,995 macros defined across the effective corpus, **4,646 in the store, 3,349 missing
(41.9%)**. That headline is misleading and the per-class breakdown is the honest number:

| class group | missing / total | coverage |
|---|---|---|
| **balance-relevant** (ship_*/weapon/turret/shield/engine/bullet/missile/thruster) | **16 / 1,583** | **99.0%** |
| galaxy map (zone 944, sector 185, highway 122, cluster 120) | 1,371 / 1,404 | ~2% |
| characters/npc (`npc` 639, `<none>` 1,171 — mostly `character_macros.xml`) | 1,810 / 1,829 | ~1% |

So the store is **effectively complete for balance work** and **totally blind to the galaxy map and
characters**. That scope is defensible; being silent about it is not. A question like "which mod
moved this sector" returns an empty result that reads as "nothing did."

Of the 16 balance-relevant misses, triage found **7 are correct behaviour** (see Cleared) and
**9 are genuine** — attributable to F4 (7) and F5 (2).

## F4 — mini-DLC assets never enumerated · **DEFECT** · confidence 97%

`_effective.py:120` `reference_vpaths` enumerates with a filesystem `adir.rglob(pattern)` on
`src / "assets"`. `Config.dlc_dirs()` correctly returns all 8 DLC, but the two mini-DLC resolve to
the **game install**, where `assets/` does not exist on disk — their content lives inside `.cat`.
`adir.is_dir()` is False, `continue`, nothing said.

MEASURED: `ego_dlc_mini_01` 4 of 8 macros missing (50%); `ego_dlc_mini_02` **22 of 32 missing (69%)**
— 26 of 40 overall, including 3 `turret`, 2 `weapon`, 2 `bullet` (the Envoy Pack's
disabler / gatling / shieldpierce line).

This is the packed-blindness lesson the toolkit has **already learned twice** — `_input.py:45` and
`_migration.py:45` both carry comments about it, and `_scan.py` exists precisely because *"six
modules each hand-rolled the same loop — rglob the loose tree."* `reference_vpaths` is the one that
never got migrated to `_scan`. Fix by routing it through the `_cat`-aware scan.

## F5 — `*_macro.xml` filename filter · **DEFECT** · confidence 95%

`_effective.py:140-142`: `reference_vpaths(config, "*_macro.xml")`, then mod files are admitted only
when `low.endswith("_macro.xml") and "assets/" in low`. A macro-defining file named anything else is
never opened.

MEASURED: 2 VRO bullet macros — `bullet_gen_turret_l_rotor`, `bullet_ter_m_graviton` — are missing
for this reason alone. Small, but VRO is the balance baseline for weapons, so these are exactly the
numbers a weapon comparison needs. Fix by detecting `<macro>` content, not filename.

## F6 — `find("macro")` vs `iter("macro")` · **DEFECT (low)** · confidence 95%

`_effective.py:225` and `_compat.py:566` read **every** macro in a file; `_stats.py:172` and
`_similarity.py:60` read only the **first**.

MEASURED: 1.9% of vanilla macro files and 3.2% of installed-mod macro files hold more than one
macro — **2,221 macros unreachable** to x4stats/x4similar. Practical impact is genuinely low (the
bulk are `character_macros.xml` and map macros, which x4similar hard-filters by ship class anyway),
but it is two tools disagreeing about what a file contains — which is how the nested-door defect
started.

## F7 — `_compat._mod_deps` silent degradation · **LATENT DEFECT** · confidence 90%

`_compat.py:122-125`: a `content.xml` that will not parse returns `(folder_name, [])` — i.e. **zero
dependencies**. Dependencies force earlier load order, so losing them changes the computed order,
which decides collision winners. `_compat` has a `Skipped(what, why, degraded)` channel (line 112)
that this path does not use.

MEASURED: **0 of 122** installed `content.xml` files are malformed today, so the current cost is
zero. Contrast `_registry.scan_installed` (line 251), which handles the identical case correctly and
whose docstring explains exactly why silence is wrong. Wire this to the existing channel.

---

## Cleared — suspected, measured, and **not** defects

Recorded because a register without its negatives has no denominator.

- **`amphitrite` and `escape_pod` absent from the store.** Both are installed in `extensions\` and
  their own manifests say `enabled="1"`, but both are **disabled in the profile `content.xml`**
  (`ws_3616342050` and `escape_pod`, `enabled="false"`). Correct exclusion. My first search looked
  for the *folder* name and the profile lists by *manifest id* — the second, differently-shaped
  search is what caught it, and without it this register would have carried a false defect.
- **`tartarus_macro` missing.** `ship_variation_expansion_vro` ships
  `<remove sel="//macros/macro[@name='tartarus_macro']"/>`. The store is **right**; the pre-08-11
  tree that showed the ship was the wrong one.
- **`_merge.py:364-367`, root-`<replace>` taking `new_children[0]`.** Guarded — returns a reason when
  the payload is not exactly one element. This is the 08-08 fix working as designed.

---

## F8 — x4similar scores on an 8-key whitelist with no handling keys · **CLOSED — WONTFIX** · confidence 97%

> ✅ **CLOSED 2026-08-22, deliberately unfixed.** The score is advisory by design, and
> `difference_profile` (`_similarity.py:95`) already renders the unscored handling axes on EVERY
> reported pair — measured top axes `physics.drag.forward` (624), `physics.mass` (514),
> `physics.inertia.pitch/yaw` (497). Re-weighting would break score continuity with every historical
> comparison, to change a number that was never meant to be a verdict.
>
> **What WAS fixed is a different, real hazard:** `gates/similar_audit.py:30` holds a
> hand-duplicated copy of the weights. The duplication is CORRECT and must stay — an oracle that
> imported the implementation's constants would be checking the code against itself, and would
> agree with any bug in it. But nothing tied the two tables together, so editing one alone would
> produce a gate failure that looks like a scoring defect and is really a stale oracle.
> `tests/test_similarity_weights_pinned.py` now pins them equal, by AST rather than import, so it
> still works on a machine with no game installed.


`_similarity.py:29` `_WEIGHTS` = `hull.max, people.capacity, storage.missile, storage.unit,
cargo.max, rotationspeed.max, rotationacceleration.max, secrecy.level`. Line 69 filters the flattened
vector down to exactly those, so **everything F1 made visible is discarded before scoring.**

Consequence: two ships identical in hull/cargo/crew but tuned completely differently in drag,
inertia and jerk still score as near-duplicates. That is the "is this a reskin of a ship I own?"
question answering wrong, not merely incompletely.

**Deliberately NOT fixed with F1.** Adding handling keys is a *scoring* change: it moves real pair
scores, needs new weights chosen on evidence, and re-baselines the 808-pair audit. Bundling it into
a projection fix would have made that audit's delta unreadable. Decide it on its own.

### ✅ RESOLVED 2026-08-13 — as a DIFFERENCE REPORT, not a scoring change

The scoring change was the wrong fix. The requirement, stated by the user: *"strictly, duplicate
means same in every way. but the idea was to be able to see if it is different, and then if so,
how."* A score cannot answer that — reweighting it just moves pairs across a threshold and still
says nothing about *where* two ships diverge.

⇒ `_WEIGHTS` is **untouched**, so the candidate list is unchanged and the exhaustive audit
re-baselines to nothing (**816 reported, 816 verified, 0 violations, 0 unresolved**). Each pair now
carries a `DifferenceProfile` over the UNION of both ships' numeric axes, rendered as a third output
line — appended, never altered, because `gates/similar_audit.py` matches the score row and reads the
NEXT line for class/purpose/compared.

**MEASURED over the 816 pairs at the default threshold:**

| | |
|---|---|
| identical on every shared numeric axis (the strict duplicates) | **35** |
| differ somewhere | **781** |
| axes exposed by a ship macro / axes scored | **34 / 5** |
| most common differing axes | `physics.drag.forward` 624 · `physics.mass` 514 · `physics.inertia.pitch`/`yaw` 497 each · `steeringcurve.point.value` 482 |

Every one of those top axes is unscored, which is the F8 gap made *visible* rather than silently
folded into a number. Worked example from the live run: `ship_par_l_miner_liquid_01_a` vs
`..._solid_01_a` score **100%** and differ **56%** on `physics.inertia.roll`.

Axes present on only one ship are reported separately (`only_in_a` / `only_in_b`) rather than
dropped by intersecting key sets — that intersection would have been this register's own shape.

## F9 — `properties.*` duplicate rows · **FIXED** · introduced and caught 2026-08-12

Self-inflicted, recorded because near-misses belong in the register too. `extract_macros` walks a
macro twice — once whole, once scoped to `<properties>` — which was harmless while the walk was one
level deep. Made recursive, the first walk descended *into* `<properties>` and re-emitted every
property under a `properties.` prefix: **67,333 rows, 23.1% of the store**, exact duplicates.

Caught by noticing the consistency gate's sample keys read `properties.ammunition.value`, then
measuring — not by a test, which is why a test now exists
(`test_properties_subtree_is_not_emitted_twice`). Fixed with a `no_recurse` parameter that emits a
child's own attrs but does not descend. **Lesson: an "additive-only" differential can be all-zero on
every must-be-zero row and still hide 67k rows of junk in the *added* column. Attribute the additions
too, not just the changes.**

## F10 — x4xref cannot see the packed mini-DLC · ✅ **FIXED 2026-08-12** · confidence 97%

> ⚠ **Heading corrected 2026-08-31.** It read `**OPEN**` until today while the body and
> the summary row both said FIXED 2026-08-12. Nothing about the finding changed — only the
> heading, which is what a reader scanning for open work actually sees. Same failure as the
> stale F72 summary row corrected in the same session: a stale index beside a current entry.

`_xref.py:115` enumerates DLC as `reference / "extensions"` directory children. The two mini-DLC are
never unpacked into `reference\`, so they are absent — and the installed-mods loop below it uses
`_registry.scan_installed`, which deliberately **skips `ego_dlc_*`**. Doubly excluded.

MEASURED: `ego_dlc_mini_01` ships 6 and `ego_dlc_mini_02` ships 7 md/aiscript files (including
`gs_hyperion.xml`, `setup_dlc_mini_01.xml`, `gs_dlc_mini_02.xml`, `placedobjects.xml`), and the index
contains **0 rows** from either. Sources indexed: base + exactly 6 `dlc:` + 71 mods.

Why it matters more than the row count: x4xref exists to answer *"who calls / who listens to X"*
**authoritatively**, i.e. to make a negative admissible. A negative is exactly what this silently
breaks for Hyperion Pack and Envoy Pack content. Note `_merge.Config.dlc_dirs()` already returns all
8 correctly — x4xref just didn't use it.

**FIXED 2026-08-12.** Both `_xref.build_index` and `_similarity._collect_all` now take
`dlc_dirs`, defaulting to `Config.dlc_dirs()`. The shared readers (`_scan.iter_mod_xml`) were
already packed-aware, so only the directory list was ever wrong.

**It was in FIVE places, not two.** Chasing it turned up a fifth copy in
`gates/similar_audit.py:76` — and there the symptom was a *label* mismatch, not a visibility gap:
the gate did read the mini-DLC via `_cat`, but labelled them `ego_dlc_mini_01` while the tool now
says `dlc:ego_dlc_mini_01`, so its `(name, source)` lookup missed and scored 8 pairs UNRESOLVED.
That counter is exactly what surfaced the bug, which is why the new AST guard deliberately lints
`x4validate/` only and leaves gates to their own counters.

VERIFIED on the real corpus: xref sources **78 → 80**, rows **148,343 → 150,670**, delta **+2,327 =
mini_01 1,032 + mini_02 1,295 exactly**, no existing source's count changed. similar_audit pairs
verified **808 → 816, unresolved 8 → 0, violations 0** (its independent scan drops 1,019 → 880
because the 6 unpacked DLC had been counted twice under two labels and now dedupe). Suite **417**.
Guard proven-to-fail by reverting `_xref` to the hand-rolled walk.

Cue coverage otherwise is good: **19,650 of 20,089 distinct cue names = 97.8%**.
⚠ My first attempt at that number read the TSV without its header and reported **0%** — the verifier
was the bug, again. Check the checker first.

## F11 — `<component>` definitions are indexed by nothing · ✅ **FIXED 2026-08-13** · confidence 95%

> ⚠ **Heading corrected 2026-08-31**, same as F10: it read `**OPEN**` while the summary row
> recorded FIXED 2026-08-13 (`33f3b57`, `extract_components` scoped to identity + connection
> slots). The scoping is the finding — flattening all 5,011 components measured **33,131,780
> attribute rows, 148x the entire store** — and it is preserved in the body below.

`extract_macros` extracts `<macro>` only. MEASURED **5,018** named `<component>` elements across the
corpus, in no store and no index.

Components are not cosmetic: they define the physical slots a macro's `<connections>` point *at*
(`con_ishield_01`, turret and shield hardpoints). The M1 CPSDO question — "is anything actually
installed into the ishield slot?" — is a component question, and had to be answered by hand-parsing
files. Now that F1 has made `connections` visible (0 → 19,994 rows), the *other half* of that join
is the missing piece.

---

**RE-DERIVED BY:** `tests/test_components.py` -- it pins that a component's identity is
indexed keyed by NAME, that an overlay-patched component carries that overlay as its
origin, and that a part-template miss names the component rather than the index. Added
2026-08-31: the entry has been marked FIXED since 2026-08-13 and named no check, and the
re-derivation gate never noticed, because it read the WRONG ONE of F11's two headings.
## F12 + F14 — the corrected picture (measured 2026-08-12)

**F14 is the real finding; F12 is a subset of it.** Both `check_references` and
`check_file_existence` iterate `iter_diff_files` + `_added_subtrees`, so **all** reference checking
is scoped to `<add>` ops inside `<diff>` files. Installed mods ship **1,625 full (non-diff) XML files
across 56 mods — 38% of their XML — with none.** Inside them: 1,241 `<macro ref>` and 847
`<component ref>`, so even the check that already exists never reaches them.

**Three wrong measurements before the right one — the oracle is subtle:**

| attempt | error | result |
|---|---|---|
| 1 | checked `<component ref>` against the **macro** index | 839 of 847 "missing" (99%) |
| 2 | included **profile-disabled** mods (amphitrite, escape_pod) | their own macros absent from Tier B ⇒ phantom misses |
| 3 | used the index as the definition set | **6 of 7** sampled misses were library-defined and real |

**Corrected result** (right index per context, enabled mods only, definitions = index ∪ corpus):
**11 raw misses → 3 genuine**, and **0 of 1,214 `<macro ref>` dangling.** Tier A shows 794
unresolvable, which is proof the check must never gate at Tier A.

The 3, each verified undefined anywhere in the corpus — candidate upstream reports:
`vro` → `props_surf_ar_radar_01` · `ebi_timelines_faction_use_ship` → `ship_spl_xl_ark_01_c` ·
`code_vgr_battlecruiser` → `weapon_vgr_xl_missile_lunch_01_mk1_video`.

⇒ Ship as **INFO first**, promote to ERROR only once that list re-confirms. Depends on F11 for the
component definition set. Confidence 80%, down from 85% — an oracle I got wrong three times.

### ✅ FIXED 2026-08-13 — and widening the scope exposed three more narrowing points

Building F14 required fixing the ORACLE first, because widening the SCOPE onto a wrong oracle
multiplies its false positives. That produced **F15, F16 and F17 below**, all pre-existing, two of
them in the scope that GATES.

**The oracle, settled:**
- **NOT the store.** It holds 4,676 macro / 3,959 component names but **zero** of the 726 macros in
  `libraries/character_macros.xml` and zero of the 30 components in `character_components.xml`, and
  it is a stale build artifact of the installed set. A validator reading it would inherit a wrong
  tier *and* a 756-name hole — the exact defect class this register exists to remove.
- **Namespace-AGNOSTIC.** `<component ref>` names a macro in `libraries/wares.xml` but a component
  in a macro file, and in a `<diff>` carrying `<replace sel="//component">` at an asset path,
  element ancestry cannot classify it at all — only the selector target can. **Four**
  mis-classifications in one session. Membership against the UNION of both namespaces sidesteps all
  of them: **2,300 references → 3 unresolved, all 3 genuine, 0 false.**
- **Three lazy tiers** — index+libraries (0.3s, resolves 2,293 of 2,300) → the mod's own files →
  full corpus (~13s, 13,579 files). Scoping the corpus tier by path segment was measured and
  REJECTED: `assets/` is 63% of files *and* holds 8,425 of 11,126 definitions, so it saves 9%.

**MEASURED result over the live modlist (114 mods):**

| | before | after |
|---|---|---|
| gating ERRORs | 77 | **54** — every one verified a true positive |
| of which false | 23 (F17) + 44 (F15) | **0** |
| INFO (newly visible) | — | **14**, every one verified genuine (3 macro + 11 text) |
| suite | 437 | **453** |

The 3 macro refs are the predicted upstream defects. The 11 text refs are NEW and were verified
individually: page 20104 holds 1,213 strings but starts at 10000 (so VRO's `{20104,1601/1602}` are
absent), page 2203201 does not exist at all (`shadow_shippack`), and `{98231753,230}` is out of range
on a 54-string page (`cpsdo_zb_modpack`).

### The corpus tier's cost — caught by our own gate, not by a user

The first implementation of the corpus tier built the **whole** definition set, parsing every file.
`gates/perf_guard.py` refused it, reporting **3 regressions** on a per-mod basis:
`ebi_timelines_faction_use_ship` 3.2s → 18.8s (**5.8×**), `cpsdo_vro` 3.9s → 19.1s (4.9×),
`code_vgr_battlecruiser` 3.8s → 18.6s (4.9×), plus `vro` 16.8s → 38.8s (2.3×).

Worth recording *how* it was caught: the aggregate was **606.6s → 728.6s, 1.20×** — a number that
would have passed any eyeball test. The gate compares **per item**, which is the only reason a 5.8×
was visible at all. Same lesson as the 1.00×-total that once hid a 39× and a 51×.

**Fix: search for the name wanted instead of building the set**, with a byte pre-filter and a parse
to CONFIRM. A file whose raw bytes do not contain the name cannot define it, so it is never parsed;
one that does is parsed and XPath-checked, so exactness is unchanged. MEASURED **3.5×** on the
reference tree (8.9s → 2.5s), and `vro` end-to-end **26.6s → 16.3s** with byte-identical findings
(14 errors, 3 info). Affordable per-name because the tier is rarely reached: 7 distinct references
across 114 mods, at most 2 for any one mod, and answers are cached both ways.

`_scan.iter_mod_xml_bytes` shares `iter_mod_xml`'s enumeration and its loose-shadows-packed rule, so
the speedup cannot reintroduce the packed-mod blindness that this register exists to track.

## F15 — `TEXT_FILES` was a hardcoded two-entry list · **DEFECT** · confidence 97%

`collect_text_defs` read exactly `t/0001.xml` and `t/0001-l044.xml`. MEASURED: one installed mod
ships 90 strings under `sfx/weapons/t/`, **56 of them missing** from the definition set — so the
mod's OWN `{page,t}` references read as dangling. **44 false gating errors.** The mod is fine; the
oracle was short. Fixed by discovering t-files by SHAPE at bounded depth (0.06s over the 60 GB
reference tree, versus an `rglob` full walk). Other languages stay OUT deliberately — folding
l007/l033 into one English set would hide a genuinely missing English string behind a German one.

## F16 — script expressions counted as ware ids · **DEFECT** · confidence 95%

`ware_refs` collects `//*[@ware]` blindly, but in `md/` and `aiscripts/` that attribute holds an
**expression**: `$tradeware` (variable), `ware.energycells` (lookup). MEASURED by file area —
md/ **170 of 172** unresolved, aiscripts/ **7 of 7** — and the same forms appear outside those
directories (a mod's `backups/`), so the filter keys on the value SHAPE, not the path. The predicate
matches **0 of 2,462** effective ware ids and **0 of 1,980** vanilla ids; that denominator is the
point, because a filter that could match a real id converts a false positive into a false NEGATIVE.
Residual after filtering: **53 refs, 1 mod, 3 distinct ids**, all genuine.

## F17 — the gating scope had the wrong oracle · **DEFECT** · confidence 97%

The `<add>`-in-`<diff>` scope — the one that GATES — checked `<component ref>` against
`index/macros.xml` alone. MEASURED: **23 of the 77 gating errors** were `standardzone` and
`standardregion`, both defined in `libraries/component.xml` and registered in
`index/components.xml`. Components, checked against the macro index. Two scopes were answering one
question with two oracles — the two-doors shape that already cost this codebase the nested-patch
defect. Both scopes now share `EntityDefs`.


## F11 — re-scoped after measuring (the naive design was impossible)

Planned as "reuse `extract_macros`". MEASURED: a component file is a 3D structure definition
(`source`, `layers`, geometry, materials), and flattening 5,011 of them yields **33,131,780 attribute
rows — 148× the entire store.**

**Scoped design:** identity + `connections/connection` slots only — 5,011 components, 97,323 slots
(97,122 carrying `@tags`) → **~204,634 rows, +91%** (224,527 → ~429,161). That is the half that
joins to a macro's `<connections>` (visible since F1, 0 → 19,994 rows) and answers *"what is
installed in this slot"*. Key attr is **`@name`**, not `@id` — the F2 trap again.

## F13 — Lua is invisible to the whole stack · **stated exclusion**

`_cat._XML_SUFFIXES = (".xml", ".xsd")`. MEASURED **53 Lua files across 29 mods** (28 loose, 25
packed), including `sn_mod_support_apis` (19) — the UI API backbone. Reading Lua means a Lua parser
and a second reference graph: disproportionate. **Recommendation: keep it excluded and SAY SO at
runtime**, so a UI-mod question returns "Lua is not analysed" instead of a confident empty answer.

## Not yet audited (open leads, no denominator taken)

Named so the register does not read as exhaustive when it is not.

- `_compat.py:173` — `mod_path.rglob("*.xml")`; x4compat is known to read packed mods elsewhere, so
  this is probably one branch of a two-branch read. Not confirmed either way.
- `_refs.py` reference catalog — the README already calls it partial. No denominator has ever been
  taken on *how* partial.
- The ~30 unmarked `except`/`continue` sites outside op-application paths. F7 is the only one
  triaged; the rest were filtered out by inspection, not measurement.

## F18 — a BaseX index could not say WHEN it was true · **DEFECT** · confidence 97%

`coverage.py` records how much of the corpus was indexed; nothing recorded *as of when*. So a
database that no longer describes the world reported success indefinitely — a third case alongside
absence and non-answer: **an answer about a world that has moved on.**

**MEASURED, and this is the case that forced the design.** `x4eff` was built **2026-08-02**. The
merge engine was then fixed twice — root-`<replace>` (2026-08-08, 858 ops dropped while reported
applied) and the nested-patch door (2026-08-11). **Neither date changed one input file.** A checker
watching only inputs would have called the index fresh throughout.

The correction, proven per item against source rather than asserted:

| | old `x4eff` (Aug 2) | new `x4eff` (Aug 13) | source of truth |
|---|---|---|---|
| `engine_arg_l_allround_01_mk1_macro` thrust | **3900** | **5283** | vanilla file says 3900; VRO's diff says 5283 |
| engine thrust rows differing | — | **140 of 194 (72%)** | consistent with the 159 engines among VRO's 848 root-`<replace>` ops |

⇒ `staleness.py` records a **two-axis fingerprint** into `coverage-<db>.json`: `content` (installed
extension set + manifest mtime/size + a reference marker) and, for engine-dependent databases,
`engine` (a hash of `_merge`/`_diff`/`_cat`/`_xpath`/`_scan` **bytes** — a git commit does not move
for a dirty tree, and merge changes here are routinely used before being committed).

`ask.py` then prints a loud banner **on every run until rebuilt**, including alongside a POSITIVE
result, and refuses a NEGATIVE outright — the same contract it already applies to missing coverage,
extended from "how much" to "as of when". A coverage file with no fingerprint reads as UNKNOWN, never
fresh: every file written before 2026-08-13 lacks the field, and those are exactly the databases whose
staleness prompted the work. Both axes were proven live by tampering (engine edit → banner; different
extensions dir → banner) and restored.

Rebuild result: x4raw **13,670 of 13,682** documents (ACCOUNTED, 12 malformed named), x4eff **10,679**
(COMPLETE, delta 0).

## F19 — `--db` and the query's own `collection()` could disagree · **DEFECT** · confidence 95%

Found end-to-end while verifying F18. `ask.py xq "count(collection('x4eff')//macro)"` searched
**x4eff** but scored the answer against **x4raw**'s coverage and freshness, printing "in x4raw" — and
a deliberately stalled x4eff produced no warning at all, because x4raw happened to be fresh.

This directly violates the rule `load_coverage`'s own docstring states: *"x4raw and x4eff have
different denominators, so they must never share one."* The flag chose the denominator; the query
text chose the data. Now a mismatch is refused with the correct `--db` named.

## F20 — a SIXTH hand-rolled DLC list, in a directory nothing was linting · **DEFECT** · confidence 99%

`tools/basex/stage.py` carried `MINI_DLC = ("ego_dlc_mini_01", "ego_dlc_mini_02")`. The AST guard
added after the fifth copy scans **only `x4validate/`** and only looks for a hand-rolled *walk*
(`iterdir`/`glob` + `startswith("ego_dlc_")`) — a bare literal has no walk, so the guard was
structurally incapable of seeing this one, in a directory it never opened.

The tuple was *correct*, which is exactly why it survived: a hardcoded list is wrong only on the day
a DLC is added, and on that day nothing here would have said so. Now derived from
`Config.packed_dlc_names()` with the literal kept only as a fallback, and the guard extended to cover
`tools/basex/` **and** the literal shape — permitting a documented fallback but requiring the module
to consult Config. Proven-to-fail by reverting `stage.py` to its pre-fix form.

## F21 — the SAME staleness hole in the store and the xref index · **DEFECT** · confidence 97%

F18 fixed freshness for BaseX. It was never a BaseX problem: **any artifact that persists derived
state and cannot say when it was true** has it, and the two that matter most for a modlist decision
are the sqlite store (`x4effective`) and the `x4xref` TSV index — precisely the tools you consult to
decide *"what does this mod actually change / does anything still need it."*

MEASURED 2026-08-13 by enumerating which tools read persisted state versus recompute live:

| tool | state | exposed? |
|---|---|---|
| `x4effective` | sqlite store | **YES** — `meta` held reference path, mod count and load order, but **no timestamp and no engine fingerprint** |
| `x4xref` | `md_xref.tsv` | **YES** — and its whole purpose is NEGATIVES ("nobody calls X"), the claim a stale index gets wrong |
| `x4stats`, `x4compat`, `x4similar`, `x4validate` | recompute live | no — verified by import inspection, not assumed |

At the moment of the audit the store recorded **112 mods while 115 were installed** (`npc_economy_tweaks`
had arrived at 12:06), and `md_xref.tsv` was a day old — both answering confidently.

⇒ `x4validate/_freshness.py` is now the **single implementation**: `_effective` stamps into the
existing `meta` table (additive, no sidecar to lose), `_xref` stamps a `.freshness.json` sidecar
beside the TSV, and `tools/basex/staleness.py` **delegates** rather than keeping its own copy —
verified to produce a byte-identical fingerprint. The DLC-enumeration bug was written five times
because every caller rolled its own; this one is written once.

Both CLIs print a banner on **every** read until rebuilt. `engine_dependent` is per-artifact: a raw
file index is not a product of the merge, so a merge fix must NOT flag it — a check that cries wolf
is one the reader learns to skip, which is the failure mode a noisy guard always ends in.

**Rebuilt after the fix:** store 112 → **113 mods / 22,807 entities**, xref **150,807 rows**, both
reporting FRESH. Also confirmed while rebuilding: the depth-8 property guard truncated **10** subtrees,
all `gfxeffect`/`mapdataset` (shader and galaxy-map data). Ship/weapon/engine macros max out at
**depth 4**, so no balance figure is affected.

## F22 — the variant-sibling check enumerated loose files on disk, THREE times over · **DEFECT** · confidence 97%

> ⚠ **CORRECTED 2026-08-22 — everything below the horizontal rule is the ORIGINAL entry and its
> headline conclusion was WRONG.** It filed this as a scope gap "costing 0 today". That verdict
> came from measuring the wrong population: it examined the mini-DLC axis only, and never asked
> whether the check could see the mod under test at all.
>
> **MEASURED across 115 installed mods:** of **378** variant-macro files, the check could reach
> **14 (3.7%)**. The other **364 (96.3%)** were invisible, because the mod-under-test walk was
> `mod_dir.rglob("*.xml")` — loose only — and the mods carrying variants are packed: VRO, both
> `ship_variation_expansion` mods, all four `lc4hunter_*` packs, `ebi_m0_vro`.
>
> The two axes the original entry did describe (packed mini-DLC, cross-mod nested) account for
> **2 files each**, and **all four sit inside the 364 that were already unreachable** — so fixing
> only those would have changed nothing observable while reading as a repair.
>
> After the fix: **378 examined, 3 real findings, all in `vro`** — `ship_ter_l_flagship_01`,
> `ship_ter_m_corvette_02`, `ship_ter_s_fighter_04`, each patched in variant `a` only. So the
> true cost was never 0; it was three unreported findings and a 96% blind spot.
>
> **The same defect had already been fixed in the function directly above it.**
> `iter_diff_files` (`_check.py::iter_diff_files`) carries a docstring recording its repair on 2026-07-26 —
> *"walked `mod_dir.rglob("*.xml")` directly, which finds nothing in a mod shipped as
> ext_01.cat/.dat"* — and `_scan.iter_mod_xml` has existed since. The fix was never carried
> across, and no guard looked for the shape a second time.
>
> **Lesson, and it is the one worth keeping:** a register entry that records a measured cost of
> ZERO is the easiest kind to leave alone, and the easiest place for a wrong denominator to sit
> undisturbed. When re-verifying an entry, re-derive its POPULATION, not just its number.

---

*Original entry, 2026-08-13 — retained because the reasoning is the instructive part:*


`_check.py:1149` (the register previously cited `:918`; the line has since moved):

```python
ref_dir = config.reference / vdir
if not ref_dir.is_dir():
    continue                      # <-- silent
siblings = sorted(p.name for p in ref_dir.glob(f"{m.group('base')}_*_macro.xml"))
```

A filesystem `glob` over `reference\`, so it cannot see anything that lives only inside a `.cat` —
**exactly F4's shape**, and it survived the F4/F10 sweep because it was logged as an open lead rather
than measured. Confirmed by reading, not inferred: `reference\extensions\` holds **6** DLC directories
and no `ego_dlc_mini_*`, so for a mod patching a mini-DLC variant `ref_dir.is_dir()` is False and the
check exits without a word.

**But the cost today is zero, for two unrelated reasons — MEASURED over the installed mini-DLC:**

| DLC | ship macro files | variant-shaped | why the check is a no-op anyway |
|---|---|---|---|
| `ego_dlc_mini_01` (Hyperion) | 2 | 1 — `ship_par_l_expeditionary_01_a_macro.xml` | family has **1** member; `len(siblings) < 2` short-circuits regardless |
| `ego_dlc_mini_02` (Envoy) | 4 | **0** — `ship_gen_m_corvette_01/_02_macro.xml` | `VARIANT_RE` needs `_<one char>_macro.xml`; `01`/`02` are two chars ⇒ out of scope for an unrelated reason |

So **0 of the 8 mini-DLC ship files** could ever produce a finding. The gap is real; the loss is not.

**The 6 unpacked DLC are reachable — by path coincidence, not by design.** A mod patching another
extension mirrors the nested path (gotcha #6), so `vdir` carries the `extensions/ego_dlc_split/…`
prefix and `config.reference / vdir` lands on a directory that *does* exist. MEASURED across
`reference\`: **270 lettered ship-variant families, 80 of them multi-sibling** — base 69, split 8,
timelines 3, and 0 in boron/pirate/terran/ventures. All 80 are visible to the check today. That
coincidence is worth writing down precisely because nothing enforces it.

**Verdict: leave it, with the denominator recorded.** Fixing it means routing the lookup through
`_cat`, which is the correct shape but buys **0** findings against the current game install. It
becomes a real defect the day Egosoft ships a lettered variant family in a packed mini-DLC — which
is what this row exists to make visible. Not a silent no-op any more: it is a measured one.

## F23 — `parse_debug` dropped 44% of every log in silence · **DEFECT** · confidence 99%

`parse_debug` walked every `[=ERROR=]` line, matched six regexes, and `continue`d past anything else
without a word. No residue count, no unparsed channel. **The sixth occurrence of this register's one
shape — and the first sitting in a user-facing message**, because `check_debug_correlation` then
printed `"(of 1363 total in the log)"`, a denominator it had never measured.

MEASURED against the 2026-08-13 log:

| | before | after |
|---|---|---|
| `[=ERROR=]` lines read | 2,430 | 2,430 |
| classified | 1,363 (**56.1%**) | 2,176 (**89.5%**) |
| residue | **1,067, silent** | **254, labelled** |

**What sat in the missing 44% is the argument.** The most consequential finding of that day's triage
— a mod adding 22 jobs whose `ship.select.tags` no ship in the effective tree carries, so they can
never spawn — is a `[JobEngine]` line. Every one was invisible, and nothing said so.

Fixed by `parse_log`, which accounts for every line: classified into one of 7 mod-identifying shapes
or 17 new engine-SUBSYSTEM shapes, or labelled `unclassified` and counted. `parse_debug` is
byte-identical (1,363 entries, same 521/386/248/207/1 breakdown) so the two corpus-wide gates and the
GATING correlation check are untouched — subsystem shapes stay behind `parse_log` until promoted on
evidence.

**The residue is a true long tail, and that is the reassuring part:** 254 lines over **121 distinct
shapes**, 81 of them occurring once or twice. Largest single unparsed shape is 41. Nothing like the
310-line `[God Engine]` block that used to hide there.

## F24 — `_check_ops` was a SECOND implementation of "does this op apply" · **DEFECT** · confidence 97%

The validator evaluated every diff op's `sel=` against the tree as it stood **before the mod's own
ops**. The engine applies ops in document order to a **mutating** tree. Two independent code paths
answering one question — `_check_ops` and `_merge.apply_diff` — and they disagreed **in both
directions**.

Not found by any test. Found by `x4debug crosscheck` diffing our predictions against the engine's own
skipped ops **per item**, which is the entire reason that command exists.

**The population count I took first was a floor, and said so — which is why the differential ran.**

| shape | measured | what it counted |
|---|---|---|
| structural: op selects into an earlier `<remove>` | 2 ops, 1 mod | path-prefix match |
| structural: op selects into an earlier `<add>` | 458 ops, 18 mods | path-prefix match |
| **value-predicated: selector predicates on a value an earlier op WROTE** | **invisible to the above** | — |

The third class was the real driver and a prefix match cannot represent it at all:

```xml
<replace sel=".../price[@min='432'][@average='540'][@max='648']/@min">516</replace>
<replace sel=".../price[@min='516'][@average='540'][@max='648']/@average">688</replace>
```

X4_Customizer emits this by default — chained **1,443 ops deep** in one file.

**Full-corpus differential, 115 mods, every finding attributed:**

| | n | verdict |
|---|---|---|
| REMOVED `mlog_deadair_eco_no_da_wares` | 1,195 | false positive — engine logged **0** diff-op errors |
| REMOVED `da_ku_ai_tweaks` | 7 | false positive — engine 0 |
| REMOVED `chillturrets` | 3 | false positive — engine 0 |
| REMOVED `mlog_deadair_scripts_no_da_wares` | 1 | false positive — engine's only line is a benign missing-signature warning |
| ADDED `moreroomsforships` | 2 | genuine — `<remove>` then `<replace>` on the removed node; engine-confirmed |
| ADDED `sve_vro_trim` | 1 | genuine — engine-confirmed; **resolves the one divergence left open by the sweep** |
| ADDED `npc_economy_tweaks` | 3 | genuine — `diff.xsd` restricts `type` to `@&qname;`, so `type="min"` should be `type="@min"`. **The engine never logs this**; the schema is the evidence |

**1,206 removed, 6 added, 0 unexplained.** Runtime over 115 mods **836s → 809s**.

E2E: `x4debug crosscheck` over all 10 mods the engine named — **VALIDATOR BLIND SPOTS 3 → 0.**

Deriving from `apply_diff` also recovers two verdicts the old code dropped through a bare `continue`:
an op tag `diff.xsd` does not admit (`<relace>`), and an unmodelled `add type=`. The tree is
deep-copied first — `apply_diff` mutates, and the merged tree belongs to the caller.

### F24b — the comparison tool committed the register's shape TWICE, against itself

Recorded because both were caught the same way and neither was visible on a single mod — only on the
sweep across all ten:

1. `predicted_ops` matched only `sel matched nothing`, silently dropping all **116**
   `sel matched 2 nodes` predictions for `cpsdo_faction`, then reporting those very ops as
   *"the engine skipped it and we never predicted it — a VALIDATOR BLIND SPOT"*. **A narrowing step
   reporting its own residue as another tool's defect, inside the tool built to detect that shape.**
2. The replacement swallowed the ` (silent)` suffix into the selector, so 6 `xenon_backup` ops could
   never match and were reported as predicted-only.

Fixed by making the selector **structured data** (`Finding.sel`) instead of prose to be scraped, and
by **raising** `UnparsedFinding` on any `sel` finding the comparison cannot read — a silent skip here
does not lose a row, it invents one somewhere else.

**Consequence for the headline number:** cpsdo's much-quoted "342 vs 310" was never the right
comparison — 342 was one file's LINES, 310 the mod's TOTAL findings. Per item: engine skipped 382 ops
/ **175 distinct**, we predicted 195 / 176, **agreed 175, observed-only 0**.

---

## F25 — reported: "x4compat's collision winner is wrong" · **NOT A DEFECT** · confidence 97%

> ⚠ **DATE CORRECTION for F25–F30.** These entries were **written 2026-08-21**. Where they
> say "MEASURED 2026-08-13", the measurement was actually taken on **08-21** against the
> **2026-08-13** engine log and the modlist as installed on 08-21. I dated my own work by
> the artifact I was reading — the exact 'an artifact must declare WHEN it was true' failure
> this register exists for, committed on the date field. Entry ids are kept because other
> records cross-reference them.


An inbound reconnaissance report from another session claimed x4compat's collision winner was wrong
for ~7 attributes across **2 Kha'ak ships + 1 engine** (`ebi_timelines_faction_use_ship`,
`ebi_m0_vro`, `vro`), root-caused to vro's document-root `<replace>`.

**Investigated 2026-08-13. x4compat is CORRECT. The report compared two different layers.**

This entry exists because *a register without negatives has no denominator either* — and because two
separate traps here are each capable of producing a confident wrong answer next time.

### What the mods actually do (MEASURED, true load order)

Load order `ebi_timelines_faction_use_ship` **19** → `vro` **90** → `ebi_m0_vro` **94**
(`_compat.compute_load_order`, 115 mods, 0 dropped manifests):

1. `ebi_timelines_faction_use_ship` sets `people capacity="200"`, `capture allow="1"`, a `<software>`
   block, `<loadouts>`, and a cockpit `<connection>`.
2. `vro` root-`<replace>`s the whole document — **wiping all of it**.
3. `ebi_m0_vro` re-applies **the same content**, so the final value is what the wiped mod wanted —
   but the **owner** is `ebi_m0_vro`.

x4compat's SUBTREE rows are exact. Its wipe counts match the victim's real op counts **per file**:

| vpath | x4compat "wiping N" | victim's actual ops |
|---|---|---|
| `assets/units/size_xl/macros/ship_kha_xl_battleship_01_a_macro.xml` | 5 | **5** |
| `assets/units/size_l/macros/ship_kha_l_destroyer_01_a_macro.xml` | 3 | **3** |
| `assets/props/Engines/macros/engine_kha_l_destroyer_01_allround_01_mk1_macro.xml` | 1 | **1** |

### The root cause: `Collision.winner` is not one question

**On a SUBTREE row, `winner` names the mod that DID THE WIPING — not the mod that owns the final
value.** Cross-checking a SUBTREE `winner` against the effective store's per-attribute `origin`
compares "who clobbered" against "who owns", and they legitimately differ whenever a third mod loads
later. That is what produced the report's ~7 disagreements, and it is a property of the two
questions, not a bug in either tool.

**Consequence for any cross-check (see F26): the assertion must be per kind.**
For SUBTREE the correct assertion is *"the victim contributes no surviving attributes"* — never
*"the winner owns the value"*.

### ⚠ The landmine — do NOT cite the 2026-08-08 line as a refutation

Memory `x4-merge-root-replace-defect` records *"x4compat was proven UNAFFECTED (419 rows, 0 winner
changes)"*, and this report's root cause is also vro's document-root `<replace>`. **Not
contradictory — different layers, both true:**

- **2026-08-08** — the **merge** silently DROPPED root-`<replace>` ops (858), corrupting *values* in
  `x4effective`. x4compat was unaffected because collision topology and load-order winners do not
  depend on the value landing.
- **F25** — about x4compat's **SUBTREE reporting** when such an op exists. A different layer.

### How common is a "wipe with no net effect"? MEASURED — 3 of 148

Because the Kha'ak wipe is undone by a later mod, the obvious follow-up was whether the SUBTREE
advisory is mostly noise. It is not:

| bucket | n | % | denominator |
|---|---|---|---|
| **DESTRUCTIVE** — no mod touches the vpath after the wiper | **145** | 98.0% | 148 SUBTREE rows |
| **CANDIDATE-restored** — a later mod also patches it | **3** | 2.0% | (all three = this Kha'ak set) |
| UNKNOWN | **0** | — | — |

**Decision: no code.** At 3 of 148 the advisory earns its keep as-is; net-effect analysis would be
scope creep into x4compat for a 2% case. Recorded here instead.

### Two traps this investigation hit — both worth more than the finding

1. **My first hypothesis was wrong and I stated it too strongly.** Gotcha #13 says this exact macro
   and attribute read `0` alphabetically and `200` in true load order, so `sorted(mods)` looked like
   an irresistible explanation. It is real (MEASURED: vro alphabetical **101** vs true **90**;
   ebi_m0_vro **27** vs **94**) but it explains **nothing here** — the whole sequence happens in true
   load order. **A hypothesis that matches a known trap is still a hypothesis.** The pull to explain
   a surprise with a tidy story is exactly when to measure instead.
2. **The measurement script committed the register's own shape, and I nearly wrote the residue up as
   an x4compat blind spot.** The first run left **6 of 148 UNKNOWN** — SUBTREE rows at nested
   `extensions/<owner>/<rel>` vpaths that `build_touch_map` had already rewritten to the owner's
   logical path. The cause was my lookup not calling `_compat._strip_nesting`; x4compat was right.
   **Check the checker first** — every "violation" in the 08-09 QA campaign was the verifier's.

### Scope of this claim

**Their harness was never read** (the report is not on disk in this workspace), so this entry states
what OUR tools do, measured, and why the comparison diverges. It does not diagnose their code.

---

## F27 — `md/` scripts are merged as if they were assets · **FIXED 2026-08-22** · confidence 97%

> **FIXED.** `_merge.apply_overlay` now returns mode `script(inert)` for a complete
> (non-`<diff>`) file at an already-supplied `md/` vpath, instead of treating it as a full-file
> override. Both doors to the document share that dispatch, so `x4effective` and Tier B cannot
> diverge on it (the lesson of the nested-door defect).
>
> **Acceptance, per item:** `x4effective dump md/setup.xml` returned cpsdo's 44-line
> `Setup_CPSDO_Modpack`; it now returns vanilla's **1,784-line `<mdscript name="Setup">`**.
>
> ⚠ **Both line counts in this entry are correct — do not "fix" one into the other.** The raw
> `reference\md\setup.xml` on disk is **1,795** lines; `dump` re-serialises through lxml and emits
> **1,784**. MEASURED 2026-08-22, recorded because a mismatched pair of numbers in one entry is
> exactly what invites a confident wrong correction later.
> **Exactly ONE vpath changed hands** — MEASURED over the true load order (114 overlays):
> `md/setup.xml -> cpsdo_faction:script(inert)`, out of **299** distinct md+aiscripts vpaths
> shipped by 115 mods. Gates after the change: oracle **228/228, 0 FALSE OK** · noop_audit
> **13,851 ops, 0 false OK / 0 false alarm** · consistency_audit **0 disagreements** · suite 561.
>
> **Scope is `md/` ONLY, deliberately.** `_merge._SCRIPT_REGISTRY_DIRS` excludes `aiscripts/`
> because the corpus contains **zero** complete-file-at-a-vanilla-vpath instances there — nothing
> to verify the generalisation against, and no observable difference either way. Widening it would
> encode an inference as a fact. `gates/tool_properties.py` carries a tripwire that fails the moment
> a real `aiscripts/` instance appears, so the untested case announces itself rather than being
> silently mismodelled.
>
> Regression tests: `tests/test_script_registry.py` pins both halves of the controlled pair, that a
> `<diff>` at a vanilla script vpath still applies (263 of 264 collisions are diffs — the main
> regression risk), and that asset/library semantics are untouched.

**Original finding, kept for the record:**

> **UPDATE 2026-08-22 — SETTLED. The mechanism is confirmed; F27 is now fixable.**
> The repair was relaunched with its `Ensure_Central` safety net REMOVED, leaving only the
> DLC-pattern `Start` cue — and `$EquipmentTable.{faction.central}` stayed at **0**. So an
> extension's MD `Start` cue runs AFTER base `Setup.Start` and survives vanilla's unconditional
> `set_value md.$EquipmentTable` (`md/setup.xml:87`): **the ordering-wipe hypothesis is dead.**
> That leaves a controlled pair differing in ONE variable — same cue structure, same actions,
> same load position, both script names differing from vanilla's `Setup`:
> `md/setup_personal_central.xml` (new path) registers; `md/setup.xml` (colliding path) does not.
> **The colliding FILE PATH is the mechanism.** The engine registers MD scripts by filename and the
> extension's duplicate never takes effect — no error line anywhere, which is why this cost a
> mod author a silently dead feature and cost us three sessions of inference.
>
> **What this unblocks:** `_merge` may now model `md/`+`aiscripts/` correctly (a non-diff file at a
> vanilla script vpath is INERT, not an override) with a regression test pinning it. Until that
> lands, `x4effective dump md/setup.xml` still returns the overlay's file and hides vanilla's
> 1,795-line `Setup`. **Fix not yet written — this entry stays OPEN as a defect, now with a
> proven rule instead of an inference.**


**The narrowing point.** `_merge.apply_overlay` classifies roots by tag, and its own docstring states
the rule: *"any other non-diff root -> full-file override (asset macro/component files)."* That is
correct for `assets/**`. It is applied unconditionally, so a non-diff `<mdscript>` at an `md/` vpath
is treated the same way — the overlay **replaces** the base document. `_merge.py` contains **zero**
occurrences of `md`, `mdscript`, `aiscript` or `aiscripts`: there is no special case, because nobody
knew one was needed.

**What our model says.** `x4effective dump md/setup.xml` returns `cpsdo_faction`'s 44-line
`<mdscript name="Setup_CPSDO_Modpack">`. Vanilla's 1,795-line `<mdscript name="Setup">` — which
creates `$EquipmentTable`, `$FactionData`, `$SubscribedMissionGroups`, `$PersistentCharacters`,
`$StoryMentors`, `$SplitFactions` and much else — is **absent from the merged tree entirely**.

**What the engine says (MEASURED, from `dev\_reports\debug\debug-2026-08-13T1410.txt`).** With that
same mod installed and the engine's own FileIO log confirming it **opened**
`.\extensions\cpsdo_faction\md\setup.xml`, every global that only vanilla's `md/setup.xml` creates is
present and working:

| probe (set only by vanilla `md/setup.xml`) | `Property lookup failed` lines |
|---|---|
| `$SubscribedMissionGroups`, `$PersistentCharacters`, `$StoryMentors`, `$LastMentorSpeak`, `$SplitFactions` | **0** each |
| `$FactionData.{faction.argon}` | **0** |
| `$EquipmentTable.{faction.argon}` / `{terran}` / `{teladi}` | **0** each |

And the exhaustive list of **all 305** `Property lookup failed` lines in the log contains **no
vanilla-setup global at all**. Vanilla's `Setup` ran in full. **Our merge's answer for this vpath is
wrong.**

### Blast radius — MEASURED, with its denominator

**SCANNED SOURCE SET: 115 mods (69 loose + 46 packed), 8 `ego_dlc_*` excluded because they ARE the
reference tier, 384 `md/` + `aiscripts/` files examined.**

| | n | our merge |
|---|---|---|
| mod script files colliding with a vanilla script filename | **205** | |
| …root is `<diff>` | **204** | ✅ correct (applies the diff to the vanilla base) |
| …root is a full `<mdscript>` | **1** — `cpsdo_faction/md/setup.xml` | ❌ **wrong** |

Egosoft's own corpus is unanimous the same way: **59** DLC md files share a filename with a base
`md/` script, and **59 of 59 are `<diff>`**. Across base + 8 DLC + 115 mods that is **263 of 264
script-path collisions using `<diff>`**, with `cpsdo_faction/md/setup.xml` the single exception in the
entire corpus. This is also exactly what CLAUDE.md already tells modders: *complete XML file only
when introducing a brand-new file that doesn't exist in the base game.*

### Why this is NOT fixed, deliberately

The **divergence** is proven. The **correct rule is not.** Two candidate engine behaviours both fit
every observation above — (i) the engine registers MD scripts by filename and skips a duplicate, so
the overlay is opened and discarded; (ii) it loads both as independent scripts and something else
prevents the overlay's cues from running. They imply *different* merge semantics, and this modlist
contains **no second instance** to discriminate them (n=1 — measured, not assumed).

Encoding an unproven inference into `_merge` would be precisely the failure this register exists to
catch. **Status: documented divergence. The discriminating experiment is an in-game launch** (see the
A1 memo): if adding a uniquely-named mdscript that performs the same registration removes the 41
`md.$EquipmentTable.{faction.central}` errors, behaviour (i) is confirmed and `_merge` can be taught
the rule with a regression test.

### Consequence for callers, today

- **`x4effective dump` on any `md/` or `aiscripts/` vpath may return an overlay's file in place of
  the base one.** Today that is reachable for exactly one vpath (`md/setup.xml`), but the tool gives
  no signal, so the answer is confident and wrong.
- Anything downstream that reads the merged tree for scripts inherits it. `x4xref` indexes MD/aiscript
  **sources** rather than the merged tree and is therefore **not** affected — verified by reading its
  input path, not assumed.
- A `<diff>` at a script path — 204 of the 205 real cases — is unaffected.

### The trap this one nearly set

The first sweep for this reported **92** full-file collisions and named 91 DLC files. That was a
**verifier bug**: the vanilla name set was built from `reference\extensions\*/md`, so every DLC
collided with *its own unpacked copy*. Excluding `ego_dlc_*` took 92 → 1. Check the checker first —
the same lesson as F25 and the 08-09 QA campaign, hit again within one session.

**...and then the IDENTICAL shape recurred later in the same session.** A `modulegroups` census
counted **200 groups / 624 rows / 9 sources**; the true figure is **146 / 447 / 5**. Cause: it
enumerated `reference\` *and* the live `extensions\ego_dlc_*`, which are **the same content** — so
all four DLC that ship the file were counted twice. Knowing the shape, having just written it down,
and having the corrected script one scroll up did **not** prevent the repeat.

That is this register's thesis restated: **a lesson recorded in prose does not stop the next
occurrence — only a shared helper plus a test banning the hand-rolled form does.** Two further
instances the same day, both of the general form *the verification covered a different population
than the finding*: a `LIKE '%cpsdo%'` filter that silently dropped 12 of 116 target ids, and a
`<remove>` analysis run against the SURVIVING set instead of the SHIPPED set (a `<remove>` is
invisible in the post-removal tree by construction). Full write-up: `KNOWLEDGEBASE.md`
§ *2026-08-13f*; the durable one-liners are CLAUDE.md gotchas **#19-#21**.

**Concrete candidate if this is ever fixed in code:** a helper yielding "the installed MOD set" with
`ego_dlc_*` excluded by construction, so no caller can re-derive it by hand — the same remedy that
closed the five hand-rolled enumeration variants.

---

## F26 — `gates/cross_tool.py` verified ONE collision kind of five · **DEFECT** · confidence 97%

```python
overrides = [c for c in report.collisions if c.kind == "FULL-OVERRIDE"]
```

`gates/cross_tool.py:111`. HARD, SUBTREE, UNION-KEY and NAME-CLASH were **never** cross-checked
against the effective store. **MEASURED: 14 of 445 collisions, 3.1%** — the register's own shape, a
step that narrows the population and reports success anyway.

`git log -L 105,115:gates/cross_tool.py` shows the filter present at the file's creation (`30cc740`)
with **no comment justifying it** — an unexamined scope limit, not a documented decision.

### Why one blanket assertion would have been wrong

`Collision.winner` answers a different question per kind (F25, CLAUDE.md #18). The naive extension
the inbound report proposed — assert `winner ∈ store origins` for every kind — produces false
alarms, and did:

| kind | assertion | first attempt | corrected |
|---|---|---|---|
| FULL-OVERRIDE | winner supplies the entity → `entities.origin` | 14/14 | 14/14 |
| UNION-KEY | winner supplies the entity → `entities.origin` | 2/2 | 2/2 |
| **HARD** | winner owns the VALUE → **`attrs.origin`** | 34 agree, **6 FALSE disagreements** | **40/40** |
| **SUBTREE** | winner is the WIPER → assert the **victim** is gone, scoped to `w0` | file-wide: **6 FALSE alarms** | **148/148** |
| NAME-CLASH | winner deliberately `""` → assert it IS empty | not checked | 20/20 |
| SOFT | benign coexistence, no winner claim | — | declared, not asserted |

**Both false-alarm classes were the CHECKER's, not x4compat's** — the third and fourth time this
session. `entities.origin` names who supplied the entity node (a root `<replace>`); `attrs.origin`
names who owns the values. Asking the wrong column produced 6 confident wrong rows.

### The SUBTREE scope rule — measured, not assumed

A wipe is **node-scoped**. A mod replacing `/macros/macro/properties/explosiondamage` removes ONE
node; the victim legitimately keeps its other attributes in the same document. Asserting file-wide
absence flagged 6 of 148.

The translator looked expensive until the shapes were counted. **MEASURED — only 3 distinct `w0`
shapes exist and NONE carries a predicate:**

| `w0` | n | correct assertion |
|---|---|---|
| `/macros` | **140** | whole document replaced → file-wide absence |
| `/macros/macro/properties/<node>` | 6 | node-scoped absence |
| `/macros/macro` | 2 | whole macro element → file-wide absence |

So the two rules are complementary, not competing: **148 of 148 checked, 0 violations, 0 residue.**
The first estimate ("142 untranslatable, 4% coverage") was an artifact of a translator that only
handled `/properties/` — measuring the population turned a 4% token check into 100%.

### Result

Every kind now verified or explicitly declared, with denominators printed each run:
`FULL-OVERRIDE 14/14 · UNION-KEY 2/2 (13 of 15 hold no stored entity) · HARD 40/40 (13 of 53) ·
SUBTREE 148/148 accounted · NAME-CLASH 20/20 · SOFT 195 declared not-cross-checked.`

---

## F28 — a known-partial checker was presented as a completeness GATE · **DEFECT** · confidence 96%

`_refs._entity_kinds` (`_refs.py:195-217`) returns exactly **8** kinds — `component, definition,
description_string, name_string, owner, price, production, restriction` — every one a
`<ware>`-WRAPPER field. Nothing opens the macro.

`_check.py:1207-1217` routes **all three** entity types through it:

```python
# ware / ship / module are all <ware> entries; the analogue defines the footprint.
entity_types = {"ware", "ship", "module"}
```

So `--entity ship:x --like ship:y` compares eight wrapper fields and **reports "0 missing" for a ship
with no `<physics>`, no engine/shield/turret slots and no connections.**

The skill closed the loop: `.claude\skills\x4-scaffold\SKILL.md` said *"DO NOT fabricate meshes —
flag those as manual steps"* and then **"3. Validate completeness"**, inviting the reader to treat a
clean run as a guarantee. The README already called the catalog *partial* — so the defect is **a
known-partial tool presented as a gate**, not "the checker only does 8 fields".

### Fix — the skip channel, not prose

A `Report.skip(...)` entry naming the uncovered interior, so a false clean becomes a **declared
non-answer**. This is the same contract that exists because *"nothing examined rendered as OK"* is
this register's founding defect.

**`degraded=False`, deliberately.** `_cli.py:129` is `return 3 if report.degraded else 0`, so
degrading it would exit 3 on **every** ship scaffold — a check that floods is worse than no check.
The run genuinely did answer its 8 questions; the interior was never in scope. That is a stated
limit, not a failed run.

E2E on a synthetic scaffold:

```
[INFO ] completeness  'mine' matches the <ware>-wrapper footprint of 'analogue'
                      (macro interior NOT checked — see skipped)
  0 error(s), 1 warning(s), 1 info, 1 not checked
  NOT CHECKED:
   - completeness: ship macro interior: ... NOT checked: physics, connections/
     hardpoints, engine/shield/turret slots, storage, hull, software, steering curves
```

A macro-interior completeness recipe remains a **separate project**. What is closed here is the
tool claiming more than it checks.

---

## F29 — the packed-only trap was documented six times and kept happening · **FIXED** · confidence 97%

`_cat.mod_vfs` reads **catalogs only** and returns `{}` for a loose mod, silently. That is the
correct answer to *"what is in this mod's archives"* and the wrong answer to *"what XML does this mod
own"* — and the two questions look identical at the call site.

**MEASURED 2026-08-13.** An ad-hoc corpus scan built on it read **2,681** XML files across 115 mods
and reported three names **"NOT FOUND"**. The correct enumeration reads **4,401** and found all three
immediately, in a **loose** file. Nothing warned. The only thing that caught it was 2,681 looking too
low for 115 mods — intuition, not a control.

**This is the SEVENTH instance of the shape**, and the first six are already written down: the
register's own table lists five, and `_scan.py`'s module docstring was written about it —
*"Six modules each hand-rolled the same loop… Patching the six copies individually guarantees a
seventh."* It guaranteed correctly.

### Why the obvious fixes were the wrong ones

Two candidate fixes were killed by checking before building:

1. **"Build a shared packed+loose helper."** It already exists and is correct:
   `_scan.iter_mod_xml` / `iter_mod_xml_bytes`, loose THEN packed with the engine's
   loose-shadows-packed rule. `tests/test_dlc_enumeration.py` says so outright. The failure was
   **not routing to it**, not a missing abstraction.
2. **"AST-guard the package."** It would **not** have caught this: the bug lived in a throwaway
   script, which no linter sees. The existing `# silent-ok:` guard walks the package only.

### The fix that actually reaches a scratch script

`mod_vfs` now takes **`packed_only: bool = False`**, and when it is about to return `{}` for a
directory that *does* contain loose `*.xml` without that acknowledgement, it logs a warning naming
`_scan.iter_mod_xml`. It fires **only** in the genuinely dangerous case — a packed mod, an empty mod,
and an acknowledged is-it-packed test are all silent — and once per directory, so a 115-mod loop
cannot spam. Proven both ways: the warning fires on the exact bug, and stays silent for all three
legitimate shapes.

`_scan.mod_xml_inventory()` returns a **`Coverage(loose, packed)`** so an ad-hoc measurement gets its
denominator for free instead of remembering to compute one — `cpsdo_faction: 229 XML (229 loose + 0
packed)` is self-evidently not the `0` that `mod_vfs` reports. It **derives** the split from
`iter_mod_xml_bytes` rather than re-enumerating, because a second enumeration here would be the very
defect being fixed.

`tests/test_no_packed_only_scan.py` guards the package and the gates (`packed_only=` kwarg or an
inline `# packed-ok:` marker), with a mutation test proving the guard can fail. **Its docstring
states that it does NOT cover scratch scripts** rather than implying full coverage.

### What the guard found on its first run — and the correction I owe

It flagged **7 call sites**. Six are legitimate loose+packed pairs (`_compat._mod_xml_paths`,
`_effective` packed-DLC branch, `_xsd` script dirs, `oracle`, `similar_audit`, `tool_properties`) and
are now marked. `_scan.py`'s own three calls also tripped the runtime warning — **a false positive in
my own mechanism**, fixed by having the sanctioned reader acknowledge.

⚠ **I reported `gates/noop_audit.py:56` as a real defect. It was not.** Its `mod_docs` reads packed
**then loose** via `glob.iglob`, in the reverse order of `_scan`. My "5,577 seen / 1,719 missed across
72 invisible mods" measured **`mod_vfs` alone, not `mod_docs`** — the measurement answered a different
question from the claim, which is the exact shape this entry is about. Corrected before it reached
permanent record.

The one real (tiny) divergence there: `mod_docs` has no `yielded` shadowing set, so a vpath shipped
BOTH loose and packed is audited twice, packed copy included, though the engine ignores it. MEASURED
over 123 installed mods: **1 mod, 1 file** (`rook`, `t/0001.xml`). Documented in place, not fixed.

---

## F30 — SUBTREE's `winner` named the WIPER · **FIXED (breaking)** · confidence 98%

`Collision.winner` answered a different question per kind, and for SUBTREE it named the mod that did
the **wiping** — not the owner of the final value, because a third mod loading later can re-supply
what was wiped (MEASURED: **3 of 148**, 2.0%). An inbound report compared that field against
`x4effective`'s per-attribute `origin` and concluded x4compat was wrong; it was not (see **F25**).

`NAME-CLASH` already set `winner=""` deliberately, with the comment *"a winner here would be a
confident wrong answer."* **The identical argument applies to SUBTREE**, so the precedent is now
applied rather than re-explained:

- `winner` is **empty** for SUBTREE; the wiper moves to its own field, **`wiped_by`**.
- **`Collision.live_value_owner()`** returns the live mod, or `None` for SUBTREE / NAME-CLASH / SOFT
  — the one place that distinction lives, replacing four copies of it in prose.
- The human report prints `wiped by :` with the caveat, so no information is lost.

⚠ **BREAKING** for `x4compat --json`. Drift measured per row against a pre-change capture, and it is
exactly the specification and nothing else:

```
rows 445 -> 445, identity 0 added / 0 removed
changed winner : 148   (all SUBTREE)      changed detail: 0   changed other: 0
SUBTREE: winner=="" 148/148, wiped_by set 148/148
non-SUBTREE rows carrying wiped_by: 0
```

**The invariant was MIGRATED, not dropped.** `gates/tool_properties.py` asserted `winner in mods`
over a population that includes SUBTREE; exempting SUBTREE would have shrunk it from 425 rows to 277
while still reporting green. It now checks `wiped_by` for those rows instead:
*"the mod a collision NAMES is always one of its mods — 425 checked (148 of them via wiped_by)."*

### ⚠ Stored baselines straddle the schema change — check the date before comparing

`dev\_reports\compat-*.json` captures dated **2026-08-13 and earlier** were written under the OLD
schema: their SUBTREE rows carry a **populated `winner`** (the wiper) and **no `wiped_by` key**.
Diffing one of those against a current capture will show `winner` changing on ~148 rows and will
look like a mass re-attribution. It is not — it is this schema change, and it is the *expected*
result, not a finding.

The first capture on the new schema is **`compat-2026-08-22-post-wiped-by.json`**. Verified on it:
SUBTREE 148/148 `winner==""`, 148/148 `wiped_by` set, **0 violations**, and **0** non-SUBTREE rows
carrying `wiped_by`.

Its row count differs from the 08-13 capture for an unrelated reason, and the two causes must not be
conflated. Diffed per row: **439 → 445, 6 added, 0 removed**, and **all 6 attribute to a single
newly-installed mod** (`npc_economy_tweaks`: 4 HARD + 2 SOFT, on `md/factionlogic_economy.xml`,
`md/job_helper.xml`, `libraries/modules.xml`). The schema change itself added and removed **nothing**.

---

## F31 — `<module group=>` was checked by nothing · **DEFECT** · confidence 97%

**The engine found a real defect that x4validate reported clean.** `cpsdo_faction` declares three
station modules whose `@group` names a module group that exists nowhere:

```
libraries/modules.xml:81   dockarea_ter_hightech -> group dockarea_ter_hightech   (24 engine errors)
libraries/modules.xml:85   dockarea_ter_lowtech  -> group dockarea_ter_lowtech    (17)
libraries/modules.xml:133  processing_central    -> group processing_ter          ( 2)
```

The engine says so 43 times per launch —
`FactoryGenerator::GetAllPossibleMacros(): Station group reference '...' not found or does not
contain any macros` — while `x4validate --tier b` on that mod reported **0 mentions in its 310
errors**. Not a wrong answer: a question the tool could not ask, because `libraries/modulegroups.xml`
was not an indexed registry and nothing in `_refs.py` knew `module/@group` was a reference at all.

**Root shape:** the same one this register keeps recording — a reference class that no oracle covers
produces silence, and silence reads as clean.

### Measured before gating (2026-08-21)

| | |
|---|---|
| groups defined, EFFECTIVE tree (base + 8 DLC incl. both mini-DLC + 115 mods) | **146** |
| shape | every one a bare `<group name="…">`, **no second attribute** |
| `<module group=>` references, whole installed corpus | **22**, in **1** file of **4,391** scanned across **115** mods (0 scan failures) |
| dangling | **3**, all in `cpsdo_faction` |

Because the corpus hit list is 3 and every one is genuine, this gates as an **error** on day one
rather than starting as INFO. That is the exception, not a relaxation of the rule: the rule is that
the hit list must be reviewed in the wild *first*, and here it was — it is three rows long.

### Fixed

- `_effective.LIBRARY_REGISTRIES` gains `modulegroup` — the 21st kind. Store: **146 entities,
  446 attribute rows** (~0.08% of 582,107). It keys by `@name`; `@id` appears **zero** times, so the
  old hardcoded `el.get("id")` would have indexed nothing while reporting success (the F2 trap).
  `klass` reuses the key because there is no second attribute to record — following the existing
  `mapdataset` row rather than inventing a meaning.
- `_check.check_module_groups` verifies every `<module group=>` in the mod under test against the
  merged definition set, in diff `<add>` payloads and full files alike. Run on the RUNTIME tree: a
  group supplied by a later-loading mod is still real at runtime.
- **An empty definition set SKIPs instead of flagging everything.** If `modulegroups.xml` is missing
  or defines nothing, every reference looks dangling — that is an absent oracle, not 22 findings.
  `tests/test_module_groups.py` pins that distinction; it is the whole point of the register.

**Verified against the engine:** re-running `--tier b` on `cpsdo_faction` now reports exactly the
3 the engine complained about — 22 references checked, 3 dangling — and 0 elsewhere in the corpus.

### Near-miss recorded (the corpus check, not the tool)

The first corpus-wide flood check reported **0 dangling across 115 mods** — and was wrong.
`_scan.iter_mod_xml` yields `(vpath, _Element)`, already parsed; the scanner called
`etree.fromstring(data)` on the Element, raised `TypeError` on **every one of 4,391 files**, and a
bare `except Exception: continue` swallowed all of it. A clean corpus was reported over a
population of zero. It was caught only because cpsdo_faction was *known* to have 3 and the answer
said 0. **A checker with no denominator cannot tell an absence from a non-answer** — the rerun
prints files-parsed and scan-failures for exactly that reason.

---

## F32 — a dev-only VARIANT is validated against a tree containing its own ops · **SCOPE LIMIT** · confidence 96%

Tier B excludes the mod under test by **folder name ∪ content.xml `id`**. That is correct for the
normal case — dev copy and installed copy share both keys. It fails for a **variant**: two copies of
one mod that deliberately differ in identity.

MEASURED 2026-08-22:

| copy | folder | content.xml id | Tier B |
|---|---|---|---|
| personal / installed | `X4CapturableXenonXL` | `X4_Capturable_Xenon XL PERSONAL` | **0 errors** |
| public / dev-only | `X4CapturableXenonXL_public` | `X4_Capturable_Xenon XL` | **8 errors** |

The two dev copies differ in exactly one file — `content.xml`. Because neither exclusion key matches,
validating the `_public` copy **merges its own installed twin into the baseline tree**. The twin's
`<remove>` ops have already been applied, so the `_public` copy's identical ops correctly match
nothing and are reported as dead. All 8 are that shape (`<remove> sel matched nothing` on
`ship_xen_xl_*` macros and two `wares.xml` production tags).

**This is a scope limit, not a defect to patch blind.** The obvious "fix" — relax the exclusion to
fuzzy-match folder names — would start excluding genuinely different mods that merely share a prefix,
trading a visible false alarm for an invisible false OK. That is the worse direction, and this
register exists because that trade keeps getting made by accident. **Measure what a looser rule would
hide before changing it.**

**What to do today:** validate the copy you actually ship. The deployed/installed copy is the one
whose load-order position is knowable, and it reports 0 — consistent with the standing rule
*"validate the DEPLOYED copy, not the `dev\` copy, whenever load order could matter."*

**Consequence for the gate:** `gates/README.md` recorded `4 Tier B errors` for this mod as a
baseline. It is now 8 — moved by the modlist growing and by merge-model fixes, not by a regression.
A number produced by a structurally unstable configuration should never have been frozen as a
baseline; the README now says so instead of quoting a figure.

---

## F33 — a NON-UNIQUE key, read with a SINGULAR read · **ATTR AXIS FIXED / ENTITY AXIS OPEN** · confidence 97%

> 🟠 **UPDATED 2026-08-22 — the two axes are now in DIFFERENT states, and collapsing them into one
> verdict would hide the open half.**
>
> **Axis 2 (attr keys) — FIXED.** The flattener now disambiguates a repeated bracket
> **collision-only**: the first claimant keeps its original key, and only the 2nd, 3rd… gain `#1`,
> `#2` — reusing the `connection[name#n]` grammar already in the file rather than inventing a
> second. 627 duplicate groups → **0**, now pinned at zero in `gates/tool_properties.py` so a
> regression FAILS rather than returning quietly.
>
> Verified per item on a full rebuild, never on totals: **582,107 attr rows and 22,966 entities
> unchanged**, entity+value multiset **0 differences**, and **1,153 rows changed their prop string
> and nothing else** — 1,065 that were duplicated props, 82 sibling-bracket clashes whose
> attributes differed, 6 pre-existing `connection[...#n]`. Licensing measurement beforehand: a
> collision-only index moves **1,698 rows (0.29%)** where indexing every key positionally would
> move **358,415 (61.6%)**, and **0 of the 56 lines** in `dev/_registry/CLAIMS.tsv` use a bracket
> prop, so no recorded claim could be invalidated.
>
> **Axis 1 (entity keys) — STILL OPEN, on purpose.** 63 duplicate `(kind, name)` groups, 23
> divergent. This is a different problem: the same macro name defined in base plus five DLC, where
> **`index/macros.xml` decides which one the engine uses, NOT load order** (gotcha #18). A
> positional suffix would be an answer to a question nobody asked.


Found 2026-08-22 while re-verifying this register entry-by-entry, not by pointing a tool at a new
question. It is CLAUDE.md's own narrowing shape — *"`find` where the data repeats"* — living inside
the store's key grammar, in **two** places at once.

### Axis 1 — `(kind, name)` does not identify an entity

MEASURED on `effective.sqlite` (22,966 entity rows): **63 duplicate `(kind, name)` groups, 90 extra
rows (0.39%)**. Two distinct causes, and both are legitimate content:

- **cross-vpath (37 groups)** — the same macro name defined in several documents. `macro/cluster_sm3_background_macro`
  exists in base **plus 5 DLC**. This is the NAME-CLASH shape: `index/macros.xml` decides which one
  the engine uses, *not* load order (gotcha #18).
- **same-vpath (26 groups)** — one document defining the id more than once (`libraries/icons.xml`,
  `libraries/mapdefaults.xml`).

**23 of the 63 groups have copies that genuinely DISAGREE on at least one attribute**, so the choice
is not cosmetic. Two of them look like upstream content defects rather than tool defects, and are
recorded here only because this is where they surfaced:

- `component/Cluster_01` — one copy lives at `assets/environments/cluster/backup/Cluster_01.xml`.
  We index a **backup folder** as if it were a live definition.
- `component/bullet_gen_m_gatling_01_mk2` — defined inside a file named
  `bullet_vgr_l_gatling_01_mk2.xml`. A copy-pasted component name.

### Axis 2 — the bracket discriminator in a prop key is not unique

MEASURED (582,107 attr rows): **627 duplicate `(entity_id, prop)` groups, 1,071 extra rows (0.18%)**,
and **201 of those groups hold genuinely different values under one key**. By kind: region 418,
gfxeffect 154, macro 32, faction 21, ship 2.

The flattener discriminates repeated siblings by a bracketed attribute — `licence[generaluseequipment]`,
`effect[ref_fire_xl_01]` — but that attribute is not unique among the siblings, so several collapse
onto one key. The worst measured case:

    faction/player   licences.licence[generaluseequipment].factions   x8 rows, 8 DISTINCT values
      'alliance antigone argon buccaneers hatikvah holyorder ministry paranid scaleplate teladi trinity'
      'boron'   'loanshark scavenger'   'court freesplit'   ...

A `select a.value ... where prop=?` with `fetchone()` returns an arbitrary one of those eight.

### Why this is NOT F9

F9 was the depth-1→depth-N flatten emitting **byte-identical duplicate rows** (67,333 rows, 23.1% of
the store); that shape is gone and stays gone. This is the opposite: **one key, several genuinely
different values.** Recording it under F9 would have hidden it behind a ✅.

### Scope of this claim — what is and is not established

- **MEASURED:** the populations above, on the 2026-08-22 store.
- **MEASURED:** counts are **identical pre- and post-rebuild** (627 / 1,071 both sides), so this is
  **pre-existing** — not a regression from the F27 merge change or from the rebuild.
- **MEASURED:** **0 of 21** rows in `dev/_registry/CLAIMS.tsv` resolve through an ambiguous entity
  key or an ambiguous prop key, so the current `claims_audit` PASS (21/21) is sound and was not luck.
- **NOT established:** whether any *shipped* answer has ever been wrong because of this. No such case
  has been found; none has been searched for exhaustively either.

### Known singular readers (the ones that would bite first)

`gates/claims_audit.py:81` `_store_vpath` does `select vpath from entities where kind=? and name=?`
→ `fetchone()`, no `ORDER BY`. For a cross-vpath duplicate it would rebuild an arbitrary one of six
vanilla documents. Harmless today only because no claim names such an entity.

### Deliberately not fixed today

The honest fix is a key-grammar change (add a positional index when a discriminator repeats), which
moves prop keys and therefore every consumer, every stored baseline and this register's own numbers.
That is a release-scale change and must be **measured before it is made**, not bundled into a
documentation pass. What is fixed today is the silence: it is now a numbered row with a denominator
instead of an unknown.

### MEASUREMENT PASS — 2026-08-24 (axis 1 only; read-only, nothing changed)

Run against a **freshly rebuilt** store (23,154 entities / 592,500 attr rows, 117 active mods).
Freshness was confirmed *before* quoting anything: the store had been STALE on both axes, and a
measurement against a stale store measures a world that has moved on. **Predictions were written
down first** (`scratchpad/f33-prediction.md`) so the result could contradict them — and twice it did.

**The populations are UNCHANGED from 2026-08-22**, though the store gained 188 entities and 3 mods:
63 groups · 90 extra rows · 37 cross-vpath · 26 same-vpath · **23 that genuinely disagree**. That
stability is itself the finding — these are **vanilla/DLC-inherent, not modlist-dependent**, so the
problem does not grow as mods are added and the `_F33_ENTITY_GROUPS = 63` tripwire still holds.

| question | measured | predicted |
|---|---|---|
| cross-vpath groups the **index** can arbitrate | **37 of 37** — 34 macros via `index/macros.xml` (5,327 names), **3 components via `index/components.xml`** (4,428) | ">=30 of 37" — **exceeded** |
| same-vpath groups the index can arbitrate | **0 of 26**, by construction | 0 — confirmed |
| entity rows a key-grammar change moves | **90** of 23,154 (0.39%) | 90 — confirmed |
| attr rows on those entities | **1,063** of 592,500 (**0.18%**) | "1k–10k, <2%" — confirmed, low end |
| singular readers that would break | **4** | "1–3" — **wrong, and see below** |
| claims resolving through an ambiguous key | **0 of 21** | — |

**Prediction P3 was WRONG, and the reason is worth keeping.** I predicted `component/*` could not be
arbitrated, citing the prior measurement that enumerating from `index/components.xml` loses **487 of
3,959** entities. That is true of **enumeration** and irrelevant to **lookup**: asking "is this
already-known name in the index?" is an exact hit or miss, and all 3 were hits. *A source being an
incomplete ENUMERATOR does not make it an unreliable ARBITER* — different operations, and the prior
number was quietly borrowed across them.

**Prediction P5 was WRONG in the direction that matters.** The register named one singular reader
(`claims_audit._store_vpath`). There are **four**, and the two unrecorded ones are **user-facing**:

    gates/claims_audit.py:114   _store_vpath          select vpath ... fetchone(), no ORDER BY
    gates/claims_audit.py:167   claim value lookup    join on kind/name/prop, fetchone()
    x4validate/_effective.py:1049  x4effective show      SELECT * ... fetchone()
    x4validate/_effective.py:1093  x4effective who-sets  SELECT * ... fetchone()

So `x4effective show component/Cluster_01` returns an arbitrary one of two rows **and says nothing
about the other** — and for that entity the two are a live definition and a copy under
`assets/environments/cluster/backup/`. A gate reading arbitrarily is contained; a CLI answering a
user's direct question arbitrarily is the shape this register exists to name.

**Costing today: nothing provable.** 0 of the 21 rows in `dev/_registry/CLAIMS.tsv` resolve through
an ambiguous key, so the current `claims_audit` PASS is sound and was not luck. (That check was run
twice: the first parse silently took a comment line as the TSV header and reported "0 ambiguous"
over an empty population — a non-answer wearing a zero's clothing. The rerun asserts it parsed a
non-empty population before reporting anything.)

**Recommendation, not yet acted on.** The cheap, correct half is now known: **the index arbitrates
every cross-vpath group (37/37)**, so `_effective` could resolve those at build time by consulting
`_resolve.build_index`. That helper already exists, and `_effective.py` **already imports `_resolve`
— and never calls it** (AST-confirmed 2026-08-24: of the modules it imports from `x4validate`, all
are used except `_resolve`). The wiring was started and never finished, which is why this is a
smaller change than it sounds. That is a
build-time provenance fix, not a key-grammar change, and it does not move a single prop key. The 26
same-vpath groups are a genuinely different problem and stay open. **Deliberately deferred out of
this release: no schema change, no `SCHEMA_VERSION` bump, no tripwire edit was made in this pass.**

---

## F34 — the effective-tree build walked `reference/` loose-only · **DEFECT** · confidence 99%

`tools/basex/build-effective.py::all_vpaths` built its base set from `reference.rglob("*.xml")`.
Six of the eight DLC are unpacked under `reference\`; the two mini-DLC (Hyperion, Envoy) never are —
their content lives in `ext_*.cat`. So the enumeration returned 9,138 vpaths where 9,280 exist.

**MEASURED 2026-08-22:** BaseX `x4eff` held **23 of 142 mini-DLC documents (16%)**. The 23 were not
a partial success — they arrived *incidentally*, because two unrelated mods (`distances`,
`pdrealisticboosters`) nest patches under `extensions/ego_dlc_mini_0X/`. Remove those two mods and
the number goes to zero.

**The merge engine was never at fault.** `_merge._nested_target` and `_build_owned(owner_is_dlc=True)`
resolve packed-DLC vpaths correctly, and the 23 that got in prove it. Only the enumeration feeding
them was blind — which is why the fix is local, and why the values were never wrong, only absent.

**Two halves, or it is worse than before.** `all_vpaths` also returns `untouched`, materialized with
`shutil.copyfile(config.reference / v)`. A packed vpath has no file on disk, so fixing the
enumeration alone would have turned one silent absence into **119 silent copy failures** — and per
F35 the coverage check would still have printed COMPLETE, because those documents would simply never
have been produced. Enumeration and materialization landed in the same commit.

**Fixed** by `_effective.base_vpaths(config, pattern)` — whole-tree, loose THEN packed — with
`reference_vpaths` re-expressed as `base_vpaths` plus an explicit `assets/` filter, so F3's scope is
a visible decision rather than an artifact of where the walk starts. Set-equality was proven before
landing: **4,002 and 7,551 vpaths, 0 added / 0 removed / 0 changed.** After rebuild: **142/142**.

**This was the SEVENTH occurrence of the loose-only-rglob shape** (`_input.py`, `_migration.py`,
`_effective.py`, `_xref.py`, `_similarity.py`, `stage.py`, and this). `_effective.py` carried a comment
naming this exact bug, with this exact denominator, from 2026-08-12 — sitting in the very function
the seventh occurrence had to be written next to, and it was written anyway. (That comment is gone
now: the refactor replaced it, and its content lives in `base_vpaths`' docstring.) **A comment in one
file does not stop the next file** — which is the entire argument for the guard below over another
comment.
`tests/test_no_loose_only_reference_walk.py` now gates it across `x4validate/`, `gates/` and
`tools/basex/`; all 6 surviving sites were hand-verified before it was allowed to gate.

**Cleared while checking** (recorded so the negative carries a denominator): `_xsd.py:425` globs
`reference.rglob("*.xsd")` and is **NOT** a defect — of the 17 `.xsd` the mini-DLC ship, **16 are
byte-identical** to their base copy, and the seventeenth (`mini_02/libraries/stances.xsd`, 212 bytes)
is a stub whose entire body is an `<xs:include>` of the base file.

## F35 — coverage took its denominator from the artifact it was auditing · **DEFECT** · confidence 98%

`tools/basex/coverage.py::coverage_effective` reconciled `documents_total` — written by
`build-effective.py` — against the count BaseX indexed. Both numbers come from the same build, so a
vpath the enumeration never reached is absent from the produced count, absent from the failure list,
and absent from the deficit alike.

**MEASURED:** **0 of the 210** enumerated failures were `ego_dlc_mini`. Status came back `complete`
with `supports_negative_claim: true`, over a population that had already lost 119 documents (F34).
`ask.py:225` gates every negative claim on that single boolean.

**In fairness to the code** — and it was read before being judged — the *human* output did print the
210 and say *"a negative over x4eff is a claim about the tree MINUS those."* The caveat existed. It
died at the machine-readable boundary.

**The lesson is which channel was missing.** Counting FAILED reads was never going to catch this; the
missing channel is the **scanned source set**. "base + 6 DLC" where 8 DLC exist is a defect anyone
can see on sight; "0 unreadable files" is not. The manifest now records every configured source with
its contributed count and whether it was read loose or packed, and coverage fails when any
contributed zero. Proven able to fail: **rc=0** on real data, **rc=4** on a zero-contribution source,
**rc=4** on a manifest predating the contract. The exclusions
(`vpaths_without_effective_tree`, `unparseable_overlays`) are now carried in the JSON so a caller can
render the caveat instead of reading a bare boolean.

`coverage-x4eff.PRE-0813.json` / `coverage-x4raw.PRE-0813.json` are inert — no code reads them — but
they are **historical coverage evidence**, not junk: they are what showed
`vpaths_without_effective_tree` moving 218 to 210. Keep them.

## F36 — the freshness fingerprint covered MERGE but not ENUMERATION · **DEFECT** · confidence 97%

`ENGINE_SOURCES` hashed `_merge/_diff/_cat/_xpath/_scan` — the modules that decide what a document
**merges to**. It did not hash `_effective.py`, which decides which **documents and entities exist**
(`base_vpaths`, `reference_vpaths`, `macro_vpaths`, `component_vpaths`, `build_touch_map`), nor
`_registry.py`, which decides which **mods** exist.

**The near-miss, the same day:** F34's fix edited `reference_vpaths` itself. The store's
`fingerprint_engine` did not move, and `gates/claims_audit.py` returned **21/21 green** against a
store built by the old enumeration. It was correct only because set-equality had been proven **by
hand**. Nothing in the toolkit would have caught the other outcome — which is precisely the
eleven-day `x4eff` failure that produced this contract in the first place.

Both modules added. Rebuilds after each addition measured **0 entities ±, 0 attr rows ±** — the
content was genuinely unaffected, and the fingerprint moving is the whole point.

`staleness.py` also carried a **dead duplicate** of `ENGINE_SOURCES` — defined, never read, because
hashing has always delegated to `_freshness.hash_engine`. Deleted. A dead copy is worse than no copy:
the next person to extend the real list edits it and changes nothing. Same shape as F8's duplicated
weights, and the reason F8's fix is a pin rather than a deletion.

## F37 — "the mod list" was two different sets with no name for either · **DEFECT** · confidence 98%

Two questions, routinely conflated:

    "active"    — what the ENGINE WILL LOAD: installed AND manifest-enabled AND profile-enabled
    "installed" — what is ON DISK, enabled or not

Nothing in the code said which one a caller wanted, so it was decided by whichever helper got
imported. **MEASURED across 13 call sites: 5 right, 3 defensible but silent, and 4 wrong — all wrong
the same way**, modelling the running game from the disk.

With exactly **one** mod installed-but-disabled (`escape_pod`, 19 XML files):

| tool | what it did | why it matters |
|---|---|---|
| `x4eff` | carried its 3 macros as LIVE | x4eff is the index we point at for *"what does the ENGINE see"*. Surplus content corrupts **positive** answers, and unlike a missing document no coverage denominator guards those |
| `x4compat` | listed it as a participant in **4** collision rows | a modlist-shaping tool naming a mod the engine will not load |
| **Tier B** | would resolve a cross-mod selector against it and report **OK** | **a FALSE PASS in the mode built to catch silent no-ops** — the most serious of the three |

The blast radius was one mod only because the machine happened to have one switched off. It scales
with the disabled set, and nothing warned.

**Fixed** by `_registry.mods(scope, ...)`, with `scope` **positional and required** — a default would
simply recreate the bug. Every call site names its scope with a reason; `scan_installed` remains the
raw disk reader, inside `_registry`. `x4raw`, the registry/triage inventory and the discovery tools
(x4stats / x4xref / x4similar) stay on `"installed"` **deliberately, and now visibly** — for x4xref
especially, the broader set is the conservative one, since excluding a disabled mod could turn a real
caller into a false negative.

Guards: `tests/test_mod_scope_is_explicit.py` (bare `scan_installed` banned; the scope must be a
**literal**, so it cannot be computed back out of sight; and both scopes must remain in use, or the
API is decorative) plus a `gates/tool_properties.py` check that the store's mod set and x4eff's
manifest agree **and** that both are the ACTIVE set — two artifacts can agree perfectly while both
modelling the wrong world.

**Verified per item, never on totals:** compat **445 to 445** rows, the same **4** rows each losing
only `escape_pod` (24→23, 19→18, 13→12, 11→10), **0 winner changes**. x4eff **10,799 to 10,789**,
mini-DLC still 142/142, and `x4raw` unchanged at 20 escape_pod documents — correct, because it is the
other question. Tier B findings were **byte-identical** across all 6 highest-overlap mods (541
findings), so that repair is **preventive, not corrective, on today's modlist**: the hole is shut,
and no current answer changes.

**How it was found matters more than the fix.** It was not a hunch. F3's refreshed coverage left
exactly **3** balance-relevant macros unaccounted for, and all three were `escape_pod`'s. Chasing
three stray numbers to ground is what exposed a false-pass path in Tier B. The alternative — writing
*"3 unexplained misses"* into this register and moving on — was one keystroke away.


## F38 — the test runner narrowed its own population · **DEFECT** · confidence 99%

`pyproject.toml` sets `testpaths = ["tests"]`. `tools/basex/` ships `test_ask.py` and
`test_staleness.py` — **23 tests, all passing** — and not one was ever collected. The suite reported
**593 passed** and said nothing whatsoever about 23 tests it had not looked at.

This register's founding shape, in the test runner: *a step that narrows the data and reports success
anyway.* And the two files are not incidental. `ask.py` is what REFUSES to render a zero-result as a
finding without a coverage denominator; `staleness.py` is the freshness contract. A silent regression
in either would remove a guard while every gate stayed green — the guards would have stopped guarding
and nothing would have said so.

**Fixed** by `tests/test_basex_tests_are_not_orphaned.py`, which runs them as a subprocess. A hard
`testpaths` entry was rejected deliberately — originally because BaseX was dev-only and a fresh
public clone would have had nothing to collect. **v2.6.0 ships BaseX, so that reason has expired**;
the subprocess stays for the reason that was always the stronger one: these tests are cwd-sensitive
and insert their own directory into `sys.path`, so collecting them through a shared `testpaths` runs
them from the wrong working directory. Present → run; absent → **SKIP with a reason** (now a pruned
checkout rather than the normal case), so "not checked here" can never read as "checked and fine". A
companion test pins the expected filenames, so a rename cannot silently shrink the set back to zero.

**Found by an audit question, not a failure:** *"do these tests ever actually run?"* Nothing was red.

✅ **Related, since FIXED (recorded here because the note above was written when it was not):**
`tools/basex/` was under no version control at all — no git repo, no history, no rollback — while
holding the F34/F35 fixes and the freshness implementation. It is now a git repository, and as of
v2.6.0 it is vendored into the public bundle as well.

## F39 — the reference tree fell back to a CWD-relative guess · **DEFECT** · confidence 99%

`_merge.py` read:

    REFERENCE = _paths.reference() or Path("reference")

On a machine with nothing configured that resolves to the **relative** path `reference` — whatever
happens to sit under the current directory. Nothing exists there, so every base-game lookup misses,
and the misses are reported as **findings about the user's mod**. `_cli.py` then returned **exit 1**,
which in this toolkit means *"your mod has errors"*. The true statement was *"your toolkit is not
set up"*, and those two require opposite responses from whoever reads the exit code.

Commit `ae79fcd` had already identified CWD-relative fallback as a defect and built `_paths` to end
it — *"invisible here only because this machine's hardcoded defaults happened to be right."* This
line was the last survivor of that family, and it sat in the one module every other tool imports.

**MEASURED blast radius before changing anything:** 153 `Config(` call sites across 47 files, of
which **31** construct it bare. A second measurement decided the fix's shape: **12** tests pass
`Config(reference=tmp_path / "does_not_exist")` deliberately, because they exercise code paths that
never read the tree (`test_exprlint.py:80` says so in as many words). So the refusal fires on
**unresolved (None)**, never on "named but absent" — those are different questions and only the
first is a misconfiguration.

**Fixed** by removing the fallback, resolving the default in `Config.__post_init__` (at construction,
not at class definition — an import-time default is a second snapshot of configuration, and it also
makes the refusal untestable), and raising `_paths.Unconfigured`. Every one of the 9 console scripts
is wrapped by `_paths.refuses_unconfigured`, which turns it into **exit 2**.

**Guarded by** `tests/test_unconfigured_refusal.py`: the refusal names `$X4_REFERENCE` and the config
file, the explicit-but-absent path still works, `x4validate` returns 2 rather than 1, and — the
mechanized part — **every entry point in `pyproject.toml` carries the wrapper**, so a tenth CLI
cannot be added without one. `scripts/verify-cold.sh` then proves it end to end by running all nine
on a genuinely cold checkout.

## F40 — configuration read from `os.environ`, around the resolver · **DEFECT** · confidence 98%

`_paths` resolves every setting in layers: real environment → `.claude/x4-paths.env` → local
fallback. Two consumers read `os.environ` directly and therefore saw only the first layer:

| | |
|---|---|
| `_nexus.nexus_key()` | `setup.sh` tells users they may put `X4_NEXUS_KEY` in the config file. Following our own documentation produced **"X4_NEXUS_KEY not set"**. |
| `_effective` | read `os.environ` at **import** time into `DB_PATH`, which is also an argparse default — while `gates/_env.py` resolved the **same variable** through `_paths`. **Two doors to one question**: a gate and the CLI could disagree about which store was configured. That is F30's shape. |

**Fixed** with one door: `_paths.value()` (raw) and `_paths.path_value()` (translated). The split is
not cosmetic — `_pick()` runs `native()`, which rewrites `/c/x` to `C:/x`. Correct for a path,
**silent corruption for a credential**, so a key resolves byte-for-byte.

**Deliberately NOT included:** the Nexus key is **optional**. Every caller catches `NexusError` and
degrades to local facts (`_modlist.py:203, 377, 521`), and `steam_title()` needs no key at all.
Promoting it to the exit-2 refusal would break offline triage, which is the common case. Pinned by
`test_a_missing_nexus_key_is_recoverable_not_fatal`, because the next reader will reasonably wonder
why this one setting differs.

**Guarded by** `tests/test_env_resolution_is_delegated.py` — AST, not grep. **A text search for
`os.environ` returns 8 hits on the fixed tree, 4 of them inside the docstrings that explain the
rule**; the AST scan returns the 4 real ones. A checker that cannot tell code from prose is the
checker being wrong, which is this workspace's most repeated defect. The escape hatch
(`# env-ok: <reason>`) attaches to the contiguous comment block above a statement, and a test proves
it cannot become a blanket amnesty for the rest of the file.

## F41 — shipped scripts guessed, or reported "not configured" as failure · **DEFECT** · confidence 97%

Two user-facing scripts in the public bundle:

- `scripts/generate-baseline.sh` — `GAME_DIR="${GAME_DIR:-${X4_GAME:-$(pwd)}}"`. A baseline is a
  **recovery** artifact; silently taking it from whatever directory you were standing in writes a
  "known-good" snapshot of the wrong install, and you discover that at the moment you need to restore.
- `bin/unpack-reference.sh` — refused correctly but with **exit 1**, colliding with "it ran and
  something went wrong".

**Fixed:** refuse with **exit 2** and name what to set. Verified by execution, not by reading —
both were run with every `X4_*` variable cleared and returned 2.

**The pattern behind all three of F39–F41:** *an executable that resolves its own environment instead
of delegating.* A probe of the whole population (9 CLIs · 26 gates · 7 BaseX scripts · 3 installers ·
public `bin/` and `scripts/` · 2 harnesses) found the CLIs already clean via `_paths` and the gates
clean via `gates/_env.py`; **every defect was in a script that hand-rolled its own resolution.** The
installers read the environment by design — they are what *writes* the config file.

## F42 — a module-level gate import took 24 unrelated tests down with it · **DEFECT** · confidence 99%

Found by chasing a remainder, not by a failure. The suite collects **619** tests on a configured
machine and **595** on a cold one. Nothing was red — the cold run printed *"586 passed, 11 skipped"*,
which reads like a complete run with a few environment-dependent exceptions.

It was not. **24 tests were never collected at all**, and they appear in that line as **two** skips:

| module | tests hidden | what they actually test |
|---|---|---|
| `tests/test_cross_tool_semantics.py` | **15** | collision-kind semantics — pure mapping logic, touches no path |
| `tests/test_claims_tier.py` | **9** | claims-row TSV parsing — reads nothing from disk |

Neither needs an X4 install. They were blocked because `import_gate` skips at **module scope** when
the gate cannot be imported, and both gates resolved configuration at import: `cross_tool.py` ran
`EXT = _env.extensions()` (which reports "no install" by `raise SystemExit`), and `claims_audit.py`
computed `Path(_registry.DEFAULT_REGISTRY).parent`, which is `Path(None)` → `TypeError` when nothing
is configured.

This is **F38's shape one level up**: the runner narrowed its own population and reported the
narrowed number. F38 was 23 tests that were never collected; this is 24, and the mechanism that hid
it is subtler — a module-level skip collapses N tests into 1 line, so the summary is not merely
incomplete, it is *quantitatively misleading*.

**Fixed** by deferring resolution to first use in both gates, so importing them requires nothing.
Cold collection is now equal to warm.

**Two traps met on the way, both caught by RUNNING rather than reading:**

1. **PEP 562 `__getattr__` does not serve the module's own globals.** It is consulted only for
   attribute access from *outside*. Replacing the constants with a module `__getattr__` left every
   internal use raising `NameError` — both gates broke instantly, and a reading-based review would
   have called the change obviously correct.
2. **An accessor must go through `sys.modules[__name__]`, not call the fallback directly.** Tests
   monkeypatch `claims_audit.CLAIMS` to a fixture; calling `__getattr__("CLAIMS")` skips the module
   dict, so the parser read the developer's REAL registry — **21 rows where the test wrote 1**. The
   test caught it; a green would have meant the fixture seam was silently dead.

## F43 — the freshness CONTENT axis cannot see a mod being enabled or disabled · **DEFECT** · confidence 93%

Found while explaining why one archived log lacked an error that two later logs had. The answer was
mundane — the mod was not loaded — but the *fingerprints said the world had not changed*.

**MEASURED.** `dev\_reports\debug\debug-2026-08-13T1410.txt` and `...T2311.txt`, captured 9 hours
apart, both carry `fingerprint.content = 01639715767615e2`. Enumerating loaded extensions by the
engine's own load-time t-file block (every extension's `t/0001*.xml`, one contiguous alphabetical
block at timestamp 0.00) gives **66 mods at 14:10 and 67 at 23:11, the delta being exactly
`amphitrite`** — no other difference. The same enumerator returns **0 differences** across the
23:11, 08-21 and live 08-22 logs, which is the self-consistency check that makes it trustworthy.

**Cause (READ — `_freshness.hash_content`).** The content axis hashes, per extension directory: the
lowercased dir name, then its own `content.xml` mtime+size (or `<NO-MANIFEST>`), plus a
`libraries/wares.xml` marker for the reference tree. **The profile `content.xml` is never opened.**
Per CLAUDE.md gotcha #24, `_registry.mods("active")` = installed ∩ manifest-enabled ∩ **profile
enabled** — so the third term is invisible to the fingerprint.

**Why the folder-on-disk alternative is excluded:** had `amphitrite` been added between the two
captures, its directory would have entered the hash and the fingerprint would have moved. It did not.
So the folder was present both times and the change was in load state, not in installation.

**Consequence.** Any artifact carrying only the content axis — the effective store, `md_xref.tsv`,
BaseX `x4raw`/`x4eff` — reports **FRESH** across an enable/disable toggle while describing a
different effective tree. This is precisely the third state the freshness contract exists to catch:
not absence, not a non-answer, but *an answer about a world that has moved on*.

**Fix shape (not yet applied).** Fold the profile enable-list into the content axis — cheapest
correct form is to hash the profile `content.xml` mtime+size alongside the reference marker, so any
reconciliation or toggle moves the hash. Note gotcha #16: the profile manifest reconciles against
disk rather than on every launch, so its mtime is a real signal, not noise. A stricter form would
hash the sorted `_registry.mods("active")` id list, which is exact but costs a registry read on
every freshness check.

⚠ **Not yet measured:** how many *existing* persisted artifacts were built either side of such a
toggle. The register records the gap, not its historical blast radius.

## F45 — `apply_diff` is O(n²) in ops-per-file · **LIMITATION (accepted)** · confidence 95%

**Recorded 2026-08-23 by SALVAGE, not by discovery.** This measurement existed in exactly one place —
the memory file `x4_toolkit_v210_release.md` — and nowhere durable. It surfaced only because that
memory was queued for deletion during a memory-index prune. **Deleting it would have silently
destroyed a measured, open limitation of the merge engine**, which is the single most load-bearing
code path in the toolkit. That is the whole argument for the durability rule in CLAUDE.md: *memory is
an index, not the record.*

**MEASURED (v2.1.0 performance work):**

| | |
|---|---|
| scaling | doubling ops-per-file costs **2.8 → 3.7×** wall-clock |
| practical cliff | **~32,000 ops in one file** |
| worst real file in the corpus | **1,443 ops (~0.03 s)** — X4_Customizer's chained-predicate output |
| headroom | **~22×** |

**Why it is accepted rather than fixed.** Severity is low *by measurement*, and the fix touches
selector evaluation — the exact path `x4validate` exists to make trustworthy. Trading proven
correctness for a 22×-headroom speedup is the wrong trade, and CLAUDE.md's "Tooling Comes FIRST"
cuts this way too: a faster merge that is subtly wrong is worse than a slow one that is right.

**The trigger to revisit:** any single file approaching **~10k ops**. Gotcha #17 is the relevant
pressure — X4_Customizer emits value-predicated chains by default and one real file already reaches
1,443. A future bulk-edit tool could plausibly multiply that.

**Not a narrowing defect.** Recorded here anyway because a register without accepted-limitation rows
has no denominator either — the same reason this file keeps its NOT-A-DEFECT verdicts.

## F46 — `coverage.py` took its denominator from the CURRENT DIRECTORY · **DEFECT** · confidence 98% · ✅ FIXED 2026-08-24

**Found 2026-08-24 by the parallel v2.6.0 session; verified here by reading the source, deliberately
NOT by executing it** — a bare run is precisely the action that causes the damage.

```python
p.add_argument("--reference",  required=False, default="")   # coverage.py:178
p.add_argument("--extensions", required=False, default="")   # coverage.py:179
...
for root in roots:
    if not root.is_dir():
        continue                                             # silent skip, no record
...
out_path.write_text(json.dumps({...}))                       # unconditional, :148 and :274
```

Run without those two arguments it does something **worse than enumerating nothing**:
**`Path("")` is `Path(".")`**, so it walked the *current working directory*, counted whatever XML
happened to be there, called that the expected total, reported a deficit against it, **and overwrote
`coverage-<db>.json`**. A denominator taken from the wrong population is worse than no denominator,
because it still gets printed — and printed denominators are what every other tool trusts.

⚠ I first filed this as "enumerates nothing". That was the *charitable* reading and it was wrong;
the true mechanism is a silent population swap. Corrected 2026-08-24 after reading the fix.

**MEASURED blast radius, from the real incident:** one bare invocation rewrote `expected` from
**13,684 to 2,822** (the stage-manifest counts) and dropped the fingerprint entirely. Per the
freshness contract an ABSENT fingerprint reads as **UNKNOWN, which is strictly worse than stale**.

**Two costs from one defect, and the second is the nastier:**
1. the artifact was corrupted; and
2. `test_unimportable_engine_reports_UNKNOWN_not_a_traceback` — which called `main()` with no
   `--coverage` and therefore read the REAL artifact — flipped from pass to fail. It had passed for
   months only because a healthy file happened to be sitting there. **A unit test whose verdict
   depends on mutable state outside `tmp_path` is sampling the machine, not testing the code.**

**Repaired state, as the control:** `expected 13,874 / indexed 13,679 / deficit 195`, reconciling
exactly to Station_ink 177 + zzz_personal_overlay_B 4 + zzz_personal_overlay_A 2 = 183, plus the
12 known malformed = **195, no unexplained remainder**.

**Why this one ranks high despite being "just a default".** This is the register's signature shape —
*a step that narrows the data and reports success anyway* — living in the single tool whose entire
purpose is to supply denominators. `tools/basex/ask.py` refuses to render a zero-result as a finding
without one; `_scan.CorpusScan.verdict` RAISES rather than return an empty verdict. `coverage.py`
should do the same: **refuse when a root is missing or empty, and never write on a refusal.**

## F44 — the env-resolution guard was blind to a hardcoded absolute-path literal · **DEFECT** · confidence 96%

Found 2026-08-24 while scoping BaseX for public shipping. v2.5.0's theme was *"an executable that
resolves its own environment instead of delegating"* (F39–F41), and `tests/test_env_resolution_is_delegated.py`
was written to ban it. It detects `os.environ` and `os.getenv` — **and nothing else.**

A hardcoded literal is invisible to it. That matters because a literal is **the form the family
actually shipped in**: F39 was `Path("reference")` and F41 was `$(pwd)`, both relative, while the
BaseX scripts carried absolute developer paths that were fixed by hand *without a ban being added*.
One survivor sat in the tree the whole time:

    tools/basex/stage.py:42
    GAME_EXTENSIONS = Path(r"C:\Program Files (x86)\Steam\steamapps\common\X4 Foundations\extensions")

On any other machine that stages **zero documents and exits 0**.

**The trust cost is the real finding.** `docs/TRUST.md` row 10 named this exact test as what bans the
shape, and cited *"a corpus build … would index zero documents, exit 0"* as the defect it prevents.
It did not prevent it. A trust document that overstates its own guarantees is the single worst place
for this error, because it is the one artifact a reader cannot check by running something.

### Scope, and what it deliberately does NOT flag

**MEASURED**: 122 `.py` files / 11,650 non-docstring string constants across `x4validate/`, `gates/`,
`scripts/`, `tests/` and `tools/basex/`. The shipped detector scans production only (`tests/` excluded
— its 12 location literals are deliberate fixtures) and returns **1 hit over 61 files, 0 false
positives**. That precision is why it GATES rather than merely informs.

Two exclusions, both chosen after a wider pattern was tried and rejected:

- **URLs.** `_nexus.py:21`'s `https://api.steampowered.com/...` matched a naive `.steam` rule. A URL
  is not a location on this disk.
- **Path DERIVATIONS.** `_cli.py:102`'s `Path.home() / "Documents" / "Egosoft" / "X4"` is a hardcoded
  Windows *layout*, not a hardcoded location — a different defect, owned by the POSIX work. Matching
  the bare proper noun to catch it also flagged `_cli.py:90`, a user-facing message reading *"not
  documented by Egosoft"*. **A gating check that cries wolf on prose is one you train yourself to
  skip**, so the narrower definition wins and the derivation is fixed elsewhere.

### Three more of the same family, every one found by EXECUTION

Reading the source would not have found these. Each was run on a checkout **proven cold first** —
`_paths.registry()/game_extensions()/reference()` all `None`, and note that clearing `X4_*` is NOT
sufficient, because resolution still walks up to `.claude/x4-paths.env` (gotcha #26).

| | what it did cold | why it was invisible |
|---|---|---|
| `stage.py` | raw traceback, **rc 1** | `MINI_DLC = packed_dlc_names()` runs at **module scope**, so the exception fires during IMPORT and `@refuses_unconfigured` on `main` can never catch it. F42's shape, in a script |
| `staleness.py` | raw traceback, **rc 1** | `_defaults()` — the earliest thing that can fail — sat OUTSIDE every `try` in `main`; the handlers wrapped `write()` and `check()` only |
| `staleness.py`'s own test | passed, honestly | it monkeypatches `_core`, which fails **inside the guarded `check()`**, so it pinned the handler while never reaching the unguarded line above it |

**rc 1 is the actively harmful part**, not the traceback. In this toolkit rc 1 means *"the thing you
asked about has findings"*; the truth was *"this toolkit is not set up"*. Those demand opposite
responses from whoever reads the code — precisely the confusion F39 existed to remove, still living
in the scripts three releases later.

### A fourth, adjacent: a unit test that was sampling the machine

The same `staleness.py` test called `main(["--check", "--db", "x4raw"])` with **no `--coverage`**, so
it read the real `basex/coverage-x4raw.json`. `check()` returns early when that file lacks a
fingerprint, never reaching the monkeypatched `_core`, and the test falls through to rc 5. It had
passed for months only because a healthy artifact happened to be sitting on disk.

Now hermetic (writes its own coverage JSON under `tmp_path`), and **verified the only way that
counts: the real artifact was deliberately corrupted and the test still passed.**

⚠ **How this surfaced is worth recording against myself.** The artifact was damaged **by me**, by
running `coverage.py --db x4raw` bare while surveying the BaseX scripts — it WRITES that file, and
bare it rewrote `expected` from **13,684 to 2,822** and dropped the fingerprint. I ran a probe
without checking whether the tool mutates. That root cause is filed separately as **F46**.

Repaired state, reconciled exactly rather than rounded: **expected 13,874 / indexed 13,679 /
deficit 195** = `Station_ink` 177 + `zzz_personal_overlay_B` 4 + `zzz_personal_overlay_A` 2
(= 183 newly deployed) + 12 malformed. An unexplained remainder of **zero**, backed by an identity.

### Fixed

`_location_literals()` beside `_env_reads()`, sharing the one `# env-ok:` escape hatch and
`_annotation_window()` rather than inventing a second; `stage.py` resolves through
`_paths.game_extensions()` and refuses with **rc 2**; `staleness.py` guards `_defaults()` and routes
both failure paths through one `_report_unknown()`; `TRUST.md` row 10 corrected. The guard's
can-it-fail test exercises all five location shapes plus the three deliberate non-findings.

**FIXED 2026-08-24** by the parallel v2.6.0 session (`tools/basex 2ae4e7f`), with a test in
`tools/x4validate 1a6e3ff`. It now refuses with **exit 2** and names which argument is missing rather
than guessing. Taken by them rather than me because it is a **ship-blocker**, not a release note: a new
user running `coverage.py` bare on a fresh clone would corrupt their own artifact on first use, which is
the same "first thing a new user runs" class as F41/F42. The `--eff-manifest` branch returns before the
guard, so `build-effective.sh` is unaffected — verified by reading, while a rebuild was mid-flight.


## F47 — BaseX checked NO precondition, so every first-run failure blamed the wrong thing · **DEFECT** · confidence 97% · ✅ FIXED 2026-08-24

Found while preparing BaseX for public release: the question *"what does a new user see when this
goes wrong?"* had never been asked, because on the development machine it never went wrong.

**All three paths REPRODUCED before anything was written.** That ordering is the point — the
preflight is the largest new artifact in the release, and designing it against a *reading* of the
source is how it would have caught the wrong things.

| trigger | what the tool actually said | what is wrong with it |
|---|---|---|
| `java` off PATH | `error: BaseX query failed: [WinError 2] The system cannot find the file specified` (rc 2) | blames **BaseX** for a missing **JVM**, and on Windows does not even name the file |
| `BaseX.jar` absent | `Could not find or load main class org.basex.BaseX` | accurate to a Java developer, meaningless to anyone else |
| DB never built | `Stopped at <abs path>, 1/17: [FODC0002] Resource '<abs path>/x4raw' not found` (rc 2) | **never mentions `build-corpus.sh`** |

The third is the sharpest. `ask.py` *does* contain a line telling you to run the build script — at
`ask.py:222`, on the **zero-result path**. An unbuilt database returns an *error*, not a zero result,
so that line is unreachable in precisely the situation it was written for. Worst first-run experience
in the tool, and invisible from a configured machine.

`build-corpus.sh` compounded it by ordering: `stage.py` runs to completion at `:49` and java is not
touched until `:54`, so a machine with no JVM spent the entire staging pass before anything could fail.

**The fix is one implementation with three callers** (`build-corpus.sh`, `build-effective.sh`,
`ask.py`) rather than three checks that can drift — the "two doors to one question" shape is already
F30 in this register. `ask.py` runs the filesystem checks up front and the JVM-starting version check
only on a path that has *already* failed, so the happy path pays nothing.

**The Java floor is MEASURED, not chosen.** Read out of the shipped jar: `Implementation-Version:
12.4`, `Build-Jdk-Spec: 17`, and `org/basex/BaseX.class` carries bytecode **major 61**. Major 61 *is*
Java 17 — an older JVM does not run slowly, it refuses to load the class. (`Main-Class` is `BaseXGUI`,
which is why every caller uses `java -cp`, never `java -jar`.)

Refusal is **rc 2** ("not configured"), never rc 1 ("the thing you asked about has findings") — the
same distinction F39 put into the x4validate CLIs. 22 tests, every check exercised in **both**
directions, including that an **unparseable** `java -version` banner refuses rather than assuming a
pass: a non-answer must not be rendered as an answer in either direction.

---

## F48 — `build-effective.sh` reported a CRASHED build as SUCCESS · **DEFECT** · confidence 98% · ✅ FIXED 2026-08-24

The stale-artifact shape (F-series' "an answer about a world that has moved on"), produced by the
build script itself rather than by time passing.

**MEASURED by crash-injection on the real script text** — not a paraphrase, not a harness modelling
its shape. With a stub builder that exited non-zero, and a previous run's tree and manifest left on
disk as they would really be:

1. the builder died (rc 3) — the script printed `== building x4eff ==` and **continued**;
2. BaseX indexed a **leftover tree from the previous build**;
3. `coverage.py` reconciled the **new** DB against the **PREVIOUS run's manifest** (confirmed: the
   stub printed the old marker back);
4. coverage returned non-zero — **swallowed by `|| true`**;
5. `staleness.py --write` stamped the DB **FRESH**;
6. the script exited **0**.

Every downstream reader would have been told a crashed build succeeded, over an index built from
stale input, scored against a denominator describing different content.

**Reading `build-effective.py` before fixing it mattered**, and a blanket "fail fast" would have been
wrong: it returns `0 if not failures else 3` **after** writing the manifest at line 242. So **rc 3 is
legitimate partial success** — tree built, manifest accurate, some vpaths failed to merge — and only
a crash *before* that write leaves nothing to reconcile against. The fix distinguishes them, and the
manifest's **absence** is now the honest signal that the builder never finished.

Two swallows, both closed: `|| echo` at `:24-25` (which defeats `set -e`) and `|| true` at `:57-58`.
`build-corpus.sh` had always propagated its coverage verdict; the asymmetry meant the two halves of
one corpus reported differently for the same condition.

Verified in **all three** directions — hard crash aborts (rc 1), legitimate partial success still
continues and exits 3, clean build exits 0. The middle one is the falsification twin: without it,
"the script now stops" would be indistinguishable from "the script now stops too often".

---

## F49 — the ORPHANED-TEST guard was blind to a NEW orphan · **DEFECT** · confidence 99% · ✅ FIXED 2026-08-24

`tests/test_basex_tests_are_not_orphaned.py` exists because `pyproject.toml` sets
`testpaths = ["tests"]`, so the BaseX test files are not collected by the main suite; it runs them in
a subprocess and asserts they pass. Its own tripwire, `test_the_expected_basex_test_files_still_exist`,
guards against the runner quietly passing over a **shrinking** set.

It does not guard the other direction. `TEST_FILES` is a hand-maintained tuple, and adding
`tools/basex/test_preflight.py` (F47's proof) produced **22 tests that passed when run by hand and
were collected by nothing** — no suite ran them, and nothing reported that nothing ran them.

That is this register's founding shape, reintroduced *inside the module written to prevent it*: **a
guard whose denominator comes from itself can only ever be as complete as the list it checks.**

Caught not by the guard but by asking, after writing the tests, "does anything actually run these?"
— which is the same question the module exists to answer, and it could not answer it about itself.

Now discovers `test_*.py` on disk and fails on any file not in `TEST_FILES`. **Proved falsifiable**
in a hermetic copy (a mutated `TEST_FILES` in a temp tree, so the shared file was never edited while
a background rebuild was reading it): dropping `test_preflight.py` turns it red with the file named.
BaseX suite 26 → **48** tests; main suite 633 → **634**.


## F50 — `perf_guard` could not tell a SLOW run from a SUSPENDED machine · **DEFECT** · confidence 99% · ✅ FIXED 2026-08-25

The gate built to catch the *aggregate-hides-a-per-item-regression* shape (F-series row 8) had a
blind spot of the opposite kind: it reported a per-item regression that **did not exist**.

`measure()` brackets each mod with `time.perf_counter()`. On Windows that clock **advances while the
machine is suspended** — verified here rather than assumed, because the natural assumption
(QueryPerformanceCounter freezes across S3) is what the numbers disproved. A full gate sweep left
running overnight therefore charged the entire sleep to whichever mod happened to be timing:

    bh_shader   2.71s -> 2210.88s  (+2208.16s, 814.9x)
    PERF REGRESSION — investigate before shipping.

**Three independent lines of evidence, none of them the tool's own timing:**

1. the same mod re-timed at **3.47s** minutes later (baseline 2.71s → ~1.3×, noise);
2. the Windows event log: **Kernel-Power 131, `ResumeCount: 3`**, plus `Firmware S3 times`;
3. the sweep's own wall-clock span — **20:51 on 08-24 to 14:45 on 08-25, ~18 hours** — for work that
   used well under an hour of CPU. The same artifact inflated `xsd_fast_parity` to a recorded
   **59,362s (16.5 h)**, which is what made the whole run's timings suspect in the first place.

**An impossible number is worth more than a plausible one.** The 814× figure was *plausible* — big,
specific, and attached to a real mod — and I was one step from reporting a release blocker. What
broke it open was 59,362s, which could not possibly be true and forced the question *"what is wrong
with the clock?"* rather than *"what is wrong with the code?"*. Two anomalies, one cause; the
ridiculous one was the diagnostic.

**The fix does not depend on any clock's suspend behaviour.** A suspected regression is re-timed
once, and reported only if it reproduces under the *same* predicate (a second, looser rule in the
confirmation step would quietly let real regressions through). A spike that does not reproduce is
printed as `DISCARDED ... did NOT reproduce`, never silently dropped. An item that **cannot** be
re-timed is reported **UNCONFIRMED and still fails the gate** — "could not check" is not "not a
regression", and clearing it would turn a confirmation step into a way of losing findings.

Cost is zero on the normal path: nothing is re-timed unless it was already flagged.

Four tests, including the falsification twin (`test_a_spike_that_DOES_reproduce_is_kept`) — without
it, "discards spurious spikes" would be indistinguishable from "discards everything", i.e. a gate
that is silently switched off.

## F51 — a BaseX build SUCCEEDED and was then reported as never built · **DEFECT** · confidence 99% · ✅ FIXED 2026-08-25

Found while vendoring BaseX for v2.6.0, by asking a question the plan never asked: the plan decided
what to ship from a table of **size and licence**, and `.basexhome` is 0 bytes, so it registered as
nothing. Nobody asked what it *does*.

**MEASURED, both directions, same jar, same command, only the marker differing:**

| jar directory contains | `CREATE DB` writes to | `collection()` |
|---|---|---|
| `BaseX.jar` + `LICENSE` | `$HOME/basex/data/probeA` | returns the right answer |
| … + `.basexhome` | `<dir>/data/probeB` | returns the right answer |

Both **build**, and both are **queryable**. That is what makes it dangerous — there is no failure to
notice. What breaks is the agreement between the builder and the checker: `preflight._dbpath()` reads
`<dir>/.basex` for a DBPATH, finds no such file (BaseX wrote its config into the relocated home), and
falls back to `<dir>/data`. Proven directly against the real code, with the database sitting built and
queryable the whole time:

    preflight._db(jar_only_dir, "probeA")  →  "the 'probeA' database has not been built"
    java -cp BaseX.jar org.basex.BaseX -q "count(collection('probeA')//a)"  →  2

So the shipped first run would have been: `bash build-corpus.sh` (minutes of staging and indexing) →
`ask.py` refuses with *“the database has not been built. Run: bash build-corpus.sh”* → repeat. An
unbreakable loop, produced by the component whose entire purpose is a good first run — and it would
have shipped to Nexus, where the audience is ~20:1 and least able to diagnose it.

**The shape is this register's most repeated one, in a new place: a checker answering an ADJACENT
question.** `_dbpath()` did not ask *“where is the database?”* It asked *“where would the database be
if BaseX were configured the way I assume?”* Its docstring even says it reads `.basex` “rather than
assuming the default” — true, but the fallback IS the assumption, and the fallback is the branch a
fresh user always takes.

**Fixed two ways, because either alone leaves a hole.** The marker is vendored (`basex/.basexhome`,
byte-identical to upstream, pinned `-text` so a Windows checkout cannot mangle it). And `preflight`
now checks for it, because a user can still lose it — by copying the jar alone, or pruning dotfiles.
The message names the *relocation*, not just the missing file: a bare “a file is missing” sends the
reader to re-download, which does not help.

**A note on how this was nearly missed.** The plan's vendoring table had a `ship?` column with a
reason for every row, and `.basexhome` was not in the table at all — it had never been enumerated.
The `git ls-files` proof would have passed cleanly, because it proves nothing *dangerous* was staged,
not that everything *necessary* was. **A completeness check and a safety check are different checks**,
and this release had only the second one.

---

## F52 — a per-mod schema note goes SILENT about what it did NOT check, whenever it checked anything · **SCOPE** · confidence 97% · ✅ FIXED 2026-08-26

`_check.check_effective_schema` ends by composing a note. The detail clause naming what it could
*not* validate is guarded like this:

```python
if not checked and (new_files or no_schema):
```

so it appears **only when zero files were validated**. Validate one file out of three and the note
reports the one and says nothing about the other two.

**MEASURED 2026-08-25**, three mods deployed the same day, same run:

| mod | note emitted | what it hid |
|---|---|---|
| `zzz_personal_overlay_C` | `0 … validated — 2 declaring no schema` | nothing |
| `zzz_personal_overlay_D` | `0 … validated — 1 declaring no schema` | nothing |
| `Synthetium_Music` | `1 … validated` | **2 further eligible files, reason unstated** |

**Why it matters, concretely.** `gates/schema_sweep.py` went red on `pairs 177 -> 178`, and its own
re-baseline procedure requires attributing the delta per mod. That attribution could not be read off
the notes — the mod responsible for the +1 is exactly the one whose note is silent — so it had to be
re-derived by running the check per mod and reasoning about vpath eligibility. That is a manual step
the note exists to remove.

**The guard is not arbitrary, which is why this is SCOPE and not DEFECT.** The comment above it
records that `if checked:` was the *previous* bug: a mod where nothing validated printed no schema
line at all, so "we validated 0 files" and "we did not run" were indistinguishable — measured on
`atd_ejection_router`, which produced a bare "OK: no issues found". The repair fixed silent-on-zero
and introduced silent-on-any-success.

**The fix** is to drop `not checked` from the condition and emit the detail whenever
`new_files or no_schema` is non-zero. A mod that validated 1 of 3 files should say so:
`1 merged data file(s) validated — 2 declaring no schema`. That is strictly more information in
every case, and it restores the register's rule — **a step that narrows the data announces it, even
when it also succeeded at something.**

**Not fixed in this session deliberately:** it changes the note text for many mods at once, and a
toolkit release was being cut from the same working tree. The cost today was one re-derivation, not
a wrong answer. ⚠ Per the register's own history, "the cost is zero today" is the line nobody
re-checks — so the cost here is recorded as **one manual attribution per re-baseline**, which is not
zero.

---

## F54 — a MUTATION WINDOW can turn a previously-sound assertion VACUOUS, with nobody editing the test · **HAZARD** · confidence 98% · ✅ FIXED 2026-08-27

`gates/mutation_probe.py` deliberately edits `x4validate/_merge.py` in place, runs targeted tests,
then restores it (gotcha #27). One of its six mutants replaces

```python
if len(targets) > 1:          # ambiguous sel= -> "Multiple matching nodes ... Skipping node"
```

with `> 99999`, i.e. **ambiguous-`sel` detection off**.

**The hazard is not that values go wrong — it is that a CHECK STOPS BEING A CHECK.** Any assertion
shaped `assert applied_op.ambiguous is False` becomes **vacuously true** under that mutant. It cannot
fail. It prints PASS in exactly the same words it printed when it was load-bearing, and nothing in the
test, the diff, or the output distinguishes the two states.

**MEASURED 2026-08-25.** A parallel session's `TaskStop` reported success twice without stopping
anything, leaving a sweep running from the **dev tree**, so `mutation_probe` executed there while
work was in progress. Window (from the sweep's own per-gate logs, their measurement):
**15:42:55 – 15:43:29, 34 seconds, 6 mutants**. Independently corroborable from this side only at its
END — `_merge.py` mtime **15:43:29** (the restore), and **only `_merge.py` of the seven
`ENGINE_SOURCES` was written that day at all**, so no second window is hiding.

A verification script asserting `ambiguous=False` on 3 ops had run at ~15:26, i.e. **outside** the
window; the compat run's JSON is stamped **15:38:30**, also outside. Re-run on the clean tree,
everything was identical (compat **+0/−0 per item**, HARD 54→54, 659 tests, falsification twins still
red). So nothing was actually damaged — **but the re-run is what establishes that, and it only
happened because the window was disclosed.**

### Why this is a separate entry

The register already has a vacuous-assertion case: `xpath(...) != ["999"]` passes when the xpath
returns `[]`. That is a test **written** vacuously — visible by reading it. This one is a **sound test
made vacuous by its environment**. Reading the test teaches you nothing at all; only knowing what was
running does.

### Mitigations, strongest first

1. **Never run a mutating gate against a tree anyone is working in.** The rule already exists as
   gotcha #27; what this incident adds is that it can be violated *by accident*, through a stop
   command that reports success and does not stop.
2. **Pair every "the detector did NOT fire" assertion with a positive case that makes it fire.** A
   disabled detector then surfaces as a FAILURE rather than a pass. This is the falsification-twin
   habit (gotcha #26) applied to detectors rather than to findings.
3. **After any mutation window, re-run rather than reason.** "It should be fine, the mutant only
   affects ambiguity" is exactly the argument that would have left the vacuous assertion undetected.

⚠ **Corollary about incident reporting, learned the same day:** an incident window reported by the
process that caused it is a **claim**, not a fact. The first stated window was "16:05–16:45" —
entirely *after* the real one. The artefact's own mtime settled it. Date the artefact yourself.

## F53 — the freshness fingerprint is NOT PORTABLE across trees · **SCOPE LIMIT** · confidence 99% · ✅ FIXED 2026-08-26

`_freshness.hash_engine()` hashes the raw bytes of the seven `ENGINE_SOURCES`. Line endings are bytes.
So the same commit, materialised twice, hashes twice — and every artifact built under one reports
**STALE** to the other, with nobody having edited anything.

**MEASURED**, dev working tree vs `git archive HEAD` of the same commit:

| file | dev | archive | verdict |
|---|---|---|---|
| `_merge.py` · `_diff.py` · `_cat.py` | CRLF | CRLF | identical |
| `_xpath.py` | LF | CRLF (46) | EOL-only |
| `_scan.py` | LF | CRLF (423) | EOL-only |
| `_effective.py` | LF | CRLF (1162) | EOL-only |
| `_registry.py` | LF | CRLF (633) | EOL-only |

engine hash **`73517fd69c0c1aad`** vs **`490e9a6a64909fa1`**; all seven byte-identical after
`--strip-trailing-cr`.

**Mechanism** (measured with `git ls-files --eol`, and it is what makes this reproducible rather than
folkloric): `core.autocrlf=true`, no `.gitattributes`, index `i/lf` for all seven. A *checkout* writes
CRLF. The three CRLF files are the ones still as checked out; the four LF ones were **rewritten in
place by a tool that emits LF** and never re-checked-out. The tree is internally inconsistent for a
reason, not at random.

**Three ways in.** (1) the same repo checked out twice — what bit us; (2) a tool rewriting a source
file in place; (3) **a routine `git restore` / `git stash` / `git checkout`**, which re-materialises
one of the four LF files as CRLF and moves the hash with no edit at all.

**What it is NOT.** An earlier draft claimed a fresh clone would report every artifact stale on first
run. That was wrong and was withdrawn: `compare()` weighs a stamp written by the *building* tree
against a hash computed by the *checking* tree, and on a fresh clone those are the same tree.
Corroboration: `git ls-files` on the public mirror returns **zero** shipped artifacts — no `.sqlite`,
no `coverage-*.json`, no `md_xref.tsv`, no `basex/data/` — so there is nothing for a mismatched hash
to condemn. **This costs a developer with two trees, not a user.**

**Why SCOPE LIMIT, not DEFECT.** It fails safe: a false STALE, never a false FRESH. That is also why
it survived this long — nothing downstream ever gets a wrong *answer*.

⚠ **The sharp edge, and why it is still worth fixing.** `compare()` does not merely say stale. For an
engine mismatch it emits *"engine changed: the merge code that produced this has been edited, so the
SAME inputs would now merge differently"* — **asserting an edit that never happened.** A developer who
just ran `git stash` reads that as evidence the merge engine changed and goes hunting for it. A false
STALE is a non-answer; a false REASON is a misleading answer, and this register grades those
differently.

**Fix, deliberately deferred past 2.6.0:** a `.gitattributes` pinning `*.py text eol=lf` makes the
bytes deterministic and kills the class. It re-normalises the working tree on next checkout, moving
the engine hash **once** and marking every artifact stale one final time — fine, but it must be
**announced, not discovered**, or the next person hunts a merge bug.


⚠ **CORRECTION 2026-08-27 — this was marked FIXED on incomplete evidence, by me, and the fix was
only completed today.** `.gitattributes` pins `*.py text eol=lf`, and `git check-attr` confirms it.
**But a pin governs CHECKOUT; it does not retroactively normalise a file already on disk.**
MEASURED 2026-08-27: `_effective.py` was still **fully CRLF (1205 lines) in this working tree** while
HEAD stores it LF and **all six sibling `ENGINE_SOURCES` were already LF** — it was the last holdout,
silently, for two days.

**The cost, measured decisively by materialising HEAD's blobs into a temp dir:**

    engine hash a FRESH CLONE of HEAD computes      3239fa6c515b83b6
    engine hash with _effective.py as CRLF (here)   b25a2a99853d4c84

**`b25a2a99853d4c84` is the engine hash this workspace quoted all evening — in the plan file, in
memory, in handoffs — and it is MACHINE-LOCAL.** Same commit, same content, different line endings,
different hash. Nothing shipped wrong (the store is dev-only and only this machine read it), but any
claim of the form *"the engine hash is X"* was unportable, and a fresh clone would have read the
store as stale forever.

**How I got it wrong: I verified the RULE, not the BYTES.** Flipping this to FIXED, I checked that
`.gitattributes` contained the pin and that `git check-attr` agreed — and never counted a `\r\n` in
the files the hash actually reads. **A pin is a statement of intent; the bytes are the fact.**
Normalised 2026-08-27 (content unchanged: 1222 lines before and after, byte delta exactly the CRs
removed). See **F67** for the gap that let it hide.

## F55 — `corpus_sweep` PRINTED the evidence of 173 dead runs and passed anyway · **DEFECT** · confidence 99% · ✅ FIXED 2026-08-25

The gate exists to catch crashes across the whole installed corpus. Its entire detection was a
`Traceback` substring test plus a subprocess timeout, with `return 1 if crashes else 0`.

A process killed by the **Windows loader** never starts Python. No traceback, no output, no timeout —
so neither test can fire, and the verdict is clean.

**MEASURED**, from the gate's own log on a real run:

    tier a  exit 0: 51 | exit 1: 18 | exit 3221225794: 52
    tier b  exit 3221225794: 121
    CRASHES/HANGS: 0            <-- and the gate returned 0

`3221225794` = `0xC0000142`, STATUS_DLL_INIT_FAILED. **173 of 242 invocations (71.5%) never ran, and
the sweep passed.** The cause was process/desktop-heap exhaustion from three gate sweeps accidentally
running concurrently (F54) — but the cause is not the finding. The finding is that the gate could not
tell.

**The shape, and why it earns its own number.** This register's founding shape is *a step that narrows
the data and reports success anyway*. This is that shape with a twist: **the evidence was not hidden,
it was PRINTED.** `exit 3221225794: 121` sat in the distribution block four lines above
`CRASHES/HANGS: 0`. Any human reading the log could see it; nothing in the pass/fail path consulted
it. A summary line and a verdict line, disagreeing, in the same output.

**The corroborating tell was wall-clock.** That run took **193 s**; a healthy run of the same gate
takes **1219 s**. A 6.3× speedup with no optimisation is never good news — it means the work did not
happen. Generalise: **a gate that suddenly gets much FASTER is reporting on itself**, and it is the
cheapest check in this register because the number is already printed.

**What it retroactively costs.** Unlike most entries here, this one weakens evidence already in use:
every previous "corpus_sweep green" means *"no Python traceback"*, not *"no crash"*. Two records
citing it (KNOWLEDGEBASE.md's v2.5.0 release record and a memory file) are qualified rather than
deleted. The v2.5.0 run was **never timed**, so it cannot be re-qualified in either direction and now
says so — an unverifiable claim gets hedged, not quietly kept.

**Fixed** by classifying on the return code: anything outside the documented `{0 clean, 1 findings,
3 skipped-work}` is a crash, named with its hex form. Five tests, both directions, including the
falsification twin. The fix provably does not change the release-verification run: every exit code
there was already in `{0,1,3}` (88+33 tier a, 90+27+4 tier b), so that rc 0 stands.

## F56 — the freshness fingerprint says THAT the world moved, never WHAT · **SCOPE** · confidence 98% · ✅ FIXED 2026-08-25

`_freshness.hash_content` folded 129 folders into ONE 16-char digest. Every consumer could therefore
say *"content changed: a mod was added, removed or updated"* and nothing more — the per-folder inputs
were computed and discarded on the same line.

**MEASURED 2026-08-25.** Localising a single changed mod took **nine investigative steps and three
false leads**:

    set-diff of installed vs indexed mods   identical, 120/120
    folder count                            unchanged, 129
    find -newermt "<artifact build time>"   nothing since 17:10

The third is the instructive one: it is *structurally* blind to a **back-dated** write, because it
tests the mtime VALUE rather than when the write happened. Ground truth turned out to be `distances`
(`ws_3764127388`), rewritten at **22:40:41** with all 26 of its files back-dated to 08-23 15:54:39.

It was solved **only because that mod happened to bump its `version`**, which the sqlite `mods` table
records. A content-identical rewrite with a fresh mtime would have been undiagnosable from our own
artifacts — which is the actual finding, and it is a property of the design rather than of that
incident.

**Fixed** by splitting compute from fold: `content_detail()` returns the per-folder vector and
`hash_content()` folds it through `_fold()` — one implementation, so the two cannot drift. The vector
is persisted beside the digest (**149 KB** for 121 mods / 2,042 files; the per-mod summary alone is
7.9 KB) and `diff_detail()` localises a move by set-diffing two vectors.

**Two design consequences worth stating, because both were nearly got wrong:**

* Per-file identity is hashed as a **SET of (relpath, mtime, size) triples**, not `max(mtime)`. A max
  is exactly what back-dating defeats — the same reason `find -newermt` failed above. A mtime moving
  BACKWARD now registers identically to one moving forward, and there is a test that only passes
  because of it.
* `diff_detail(None, …)` **raises `NoBaseline`** rather than returning `[]`. An artifact predating the
  vector cannot answer "what changed", and reporting *nothing changed* would be a wrong answer where
  a non-answer is the honest one — the same contract as `ask.py` refusing a zero-result without a
  coverage denominator.

**Persistence is additive and OPTIONAL to read.** `fingerprint_detail` is a third `meta` row;
`read_sqlite`/`read_sidecar` still return a verdict-capable dict when it is absent. Requiring it
would have flipped every artifact on disk to UNKNOWN at once — a far worse failure than not being
able to localise, and the specific regression the backwards-compatibility test pins.

## F57 — the CONTENT axis could not see a mod's FILES change at all · **DEFECT** · confidence 99% · ✅ FIXED 2026-08-25

Found while planning F56's fix, and larger than F56. `hash_content` stat'd **only** each folder's
`content.xml`. A mod whose files changed while its manifest did not was therefore invisible — and
that is the ordinary overlay-deploy workflow in this workspace: edit `libraries/god.xml`, copy it
into `extensions\`, manifest untouched.

**MEASURED 2026-08-25, two ways.**

    non-DLC mod folders                                        121
      ...with >=1 file NEWER than their own content.xml         72   (59.5%)

and demonstrated directly against the pre-fix code, which is the half that matters, because the
census above is a state and this is the behaviour:

    file-only edit:  before=03122df005f47fbd  after=03122df005f47fbd   MOVED=False

**Why this outranks F53.** F53 is a false STALE — it nags when nothing changed, and it fails SAFE.
This was a false **FRESH**: every artifact reported that it still described the world while it
demonstrably did not, and a negative claim drawn from it would have been quoted with confidence. It
fails in the UNSAFE direction, which is the direction this register exists to find.

**Fixed** by walking each mod for engine-loadable files and hashing `(relpath, mtime, size)` into a
per-mod `tree_sha`.

**The filter is CORRECTNESS, not speed.** Only `.xml/.cat/.dat/.lua` count. MEASURED: **8 mods**
(`arck_job_registry`, `da_ku_ai_tweaks`, `distances`, `gs_debug_report`, `gs_qol_fleet_transfer_crew`,
`gs_qol_show_on_map`, `kuertee_waypoint_fields_for_deployments`, `pdrealisticboosters`) would
otherwise move the fingerprint purely from README/CHANGES/LICENCE/`live_editor_log.json` churn —
manufactured staleness, which trains the reader to ignore the banner, which is how a noisy check ends
up worse than no check. Every "does not move" test here is PAIRED with a positive twin that does move
(F54 mitigation 2), so a disabled detector fails rather than passes.

**A measurement bug on the way, logged as checker-bug #48.** The deep walk was first measured at
**1.73 s** — via a bash loop spawning 121 `find` processes — and I nearly concluded it was too
expensive for the always-on path and designed an opt-in flag around that. Re-measured with `os.walk`:

    whole extensions\ tree              15,523 files   0.14 s
    filtered to engine suffixes          2,042 files   0.060 s
    filtered + triples + tree_sha        2,042 files   0.092 s

~92% of the original figure was process-spawn overhead. **The instrument answered an adjacent
question** (CLAUDE.md #22), and it would have bought a permanently weaker design. The `hash_content`
docstring's "walking 60 GB is too expensive" is about the **reference** tree and never applied to
`extensions\`.

**SCOPE LIMIT, declared rather than discovered later.** Per-file identity is `(mtime, size)`, not a
content hash — hashing every file would mean reading VRO's multi-hundred-MB `ext_01.cat` on every CLI
run. So an edit changing **neither size nor mtime is invisible**, and an edit keeping the size but
moving the mtime is classified `touched` rather than `content`. Manifests ARE hashed (8 ms for all
129) because separating *touched* from *actually changed* is precisely what F56 turned on.

## F58 — a capability that EXISTED, was correct, and was in `--help` · **ROUTING** · confidence 99% · ✅ FIXED 2026-08-26

Every other entry in this register is a tool that did something wrong. **This one is a tool that did
everything right and was never run.**

**What was filed:** *"nothing answers: does this vpath exist in the LIVE merged tree, and which
mod/DLC supplies it?"*, with a request for a new `_effective.effective_owner()` helper.

**What is true, MEASURED 2026-08-26:**

    x4effective dump --chain .../missile_wasp_macro.xml   -> rc 0   <!-- sources: vro:full -->
    x4effective dump --chain libraries/wares.xml          -> rc 0   <!-- sources: base, ego_dlc_boron:diff, ... -->
    x4effective dump         <an invented vpath>          -> rc 1   "no effective content for ..."

Existence via the exit code, source by name, **and supply mode** — `:full` = supplies the document,
`:diff` = only patches it. That last distinction is the one the finding turned on, and it was
already there. `--chain` is documented in `dump --help`.

**The cost.** A throwaway script hand-rolled a base-only existence walk and reported **65 of 241
target vpaths (27%)** as *"paths Egosoft renamed"*. **Egosoft renamed nothing** — they are files VRO
adds, at plain base-game-looking paths base+DLC never shipped.

**VERIFIED INDEPENDENTLY (9 files), and deliberately including every example the claim NAMED:**

| file | `dump --chain` |
|---|---|
| `missile_cruise.xml` · `missile_heavy.xml` · `turret_multilauncher.xml` | `vro:full` |
| the six X3-named missiles (wasp, hornet, tornado, banshee, wraith, poltergeist) | absent from base+DLC (9,280 vpaths), present in VRO (1,614 docs) |

⚠ **The remaining 56 of 65 are the filer's measurement, not reproduced here.** Quoted as theirs.

**Why the direction matters more than the count.** *"Renamed"* reads as INERT — dead selectors, safe
to delete. The truth is the inverse: those files **win** their vpaths, pinning that content to its
archived state. Inert versus silently-overriding is the difference between *"delete these"* and
*"these are the most dangerous files in the mod."* A wrong answer that points toward deletion is
worse than one that points toward caution.

### Why the fix is a routing row and NOT a helper

Two pieces of evidence, and they point the same way:

1. **`_effective.base_has()` has ZERO production callers.** It was added 2026-08-25, its own
   docstring calls it **"PREVENTIVE"**, aimed at this exact trap — and the very next script did not
   use it. **A helper written one day did not stop the identical error the next day.** A second
   helper was not going to either; it would have been the tenth.
2. **A banning test would not have caught this.** `tests/test_no_loose_only_reference_walk.py` says
   so in its own docstring: *"a throwaway script is not covered by any linter, and the measured
   failure in the `_cat.mod_vfs` sibling guard happened in exactly such a script."* This failure was
   in a throwaway script.

`CLAUDE.md` is always in context; the register, the tests and `--help` are not. That is the only
surface that reaches the author of a throwaway script, which is where this happened.

### The mechanism, stated so it generalises

The filer asserted *"no tool exists"* **without checking `--help`** — a gotcha #9 negative, aimed at
our own toolkit. The same session did it twice: this, and *"VRO has no obtainable version history"*
when Nexus mod 305 archives **73 releases back to 2019**. Both times the thing existed; both times
the negative entered permanent record in the grammar of a fact.

> **Rule: grep your own toolkit's `--help` before filing a gap against it.**

### The systemic half — MEASURED, because one instance is not a pattern

If a correct, documented capability can be missed once, ask how many others are reachable only by
reading `--help`:

    30 subcommands across the 9 CLIs
     9 named anywhere in CLAUDE.md
    21 uncovered  (70%)

⚠ **My own instrument was wrong first, and the correction belongs here.** The first count said
**38 of 50 (76%)** — because argparse prints its `{a,b,c}` subcommand blob **twice** (once under
`usage:`, once under `positional arguments:`), so `x4effective` counted 9 as 18 and `x4modlist` 11
as 22. Caught by a second method disagreeing, then reconciled per CLI. The true figure is 21 of 30.
Cross-checked against a hand count on one CLI (9 = 9) before being written here.

Uncovered examples worth routing, not a licence to dump every flag into the table: `x4effective
who-sets` / `diff-mod` / `coverage`, `x4modlist verify` / `changed`, `x4xref who-calls` /
`who-listens`, `x4stats macro`. **A routing table nobody can scan is the same failure in different
clothes**, so the audit produces candidates, not an inventory.

## F59 — a mutating gate's restore lived only in `finally` · **DEFECT** · confidence 99% · ✅ FIXED 2026-08-26

`gates/mutation_probe.py` works by editing a source file in place, running the tests, and putting it
back. The put-back lived in a `finally:` block. **`finally` does not run on SIGKILL.**

So a killed probe leaves a deliberately-broken source on disk — and because that file is **tracked**,
`git status` reports an ordinary modification and the tree looks entirely normal. That is gotcha
#27's real lesson, and it has already cost something: **v2.5.0 shipped with
`if len(targets) > 99999:` instead of `> 1`**, i.e. ambiguous-`sel` detection silently disabled in a
public release, carried in by a port that read the file during a mutation window.

**Why this stopped being hypothetical.** MEASURED 2026-08-25: a `TaskStop` that **reported success
and did not stop** left three gate sweeps running concurrently, one of them from the dev tree. The
standing rule *"never run a mutating gate against a tree anyone is working in"* (F54 mitigation 1)
assumes you know a gate is running. That assumption failed, by accident, with a tool reporting
otherwise.

**REPRODUCED, deliberately, 2026-08-26** — and reproduced rather than simulated, because the entire
defect is that `finally` does not run, and calling `recover()` directly would exercise the wrong path:

    launched the probe; polled until a target actually differed from HEAD
    caught mid-mutation at t=7.5s; hard-killed 3 processes (no cleanup)

    what a user would then find:
      .mutation-probe-active   present
      x4validate/_merge.py     MUTATED
      line 394                 if len(new_children) != 1 and False:    <- guard DISABLED

### The fix, and why each half is needed

- **Pristine copies** (`.mutation-probe-pristine/`), taken before the first mutation. Recovery must
  not depend on the thing that failed.
- **A marker** (`.mutation-probe-active`), written before the first mutation and removed after the
  last restore. Its presence means *the tree may be mutated right now*.
- **Refuse, don't proceed.** A marker on startup ⇒ **exit 2** naming `--recover`. Exit 2 is
  "cannot run", never 1 ("your code has findings") — the F39/F47 distinction.
- **Recovery is EXPLICIT.** Auto-restoring would require deciding whether the recorded pid is alive,
  and on Windows `os.kill(pid, 0)` does not test liveness — it maps to `TerminateProcess`. Guessing
  wrong there would kill a running probe or overwrite files beneath it. An explicit flag cannot make
  that mistake, and it forces a human to READ that the tree had been poisoned.
- **It names the file.** `_merge.py RESTORED (was mutated)` beside `_compat.py already clean`. A
  silent tidy-up teaches the next person nothing.

### The part worth generalising, and it is not the code

The parallel session verified the window **from outside**, without asking: they observed the marker,
the three pristine copies, and `git diff` showing the mutated file. Their framing is better than
mine and is recorded here as theirs:

> **A control only one side can see is still an assurance.**

That is the argument for the *next* piece of work, which is deliberately NOT in this change: the
CLIs (`x4effective`, `x4compat`, `x4validate`) should check for the marker and refuse or banner,
because during a window they all answer from broken code at once with nothing on screen saying so.
⚠ **When that lands, resolve the marker from the PACKAGE ROOT, never the CWD** — it lives in
`tools/x4validate/`, and the common case is running `x4effective` from the game directory, where a
CWD-relative check would find nothing and cheerfully report all clear. That is F46's `Path("")`
trap wearing new clothes.

### Also in this change (F54 mitigation 2 — cite, do not duplicate; F54 is the parallel session's)

Coverage widened from 1 file to 3: `_merge.py` (6), `_registry.py` (3), `_compat.py` (2).
**11/11 killed, 0 survivors, 0 hangs.** Selection rule: every mutant inverts a guard whose failure
has a MEASURED cost already in the register — a contrived mutant nothing kills is noise, not a
finding. Notably the `prof.get(id, True) -> False` mutant died, so CLAUDE.md #30a's "54 of 115 mods
silently vanish" case is now provably checked rather than merely believed.

Two verdict fixes came with it: the per-mutant timeout dropped **1800s → 120s** (one hanging mutant
used to cost half an hour, which is what made widening unaffordable), and **HUNG is now its own
verdict** — a mutant that hangs is a finding about the suite, not a pass, and collapsing it into
either bucket is the absence-versus-non-answer error this register exists for.

### ✅ The CLI-side half, landed 2026-08-26 (was "deliberately NOT in this change")

`x4validate/_mutation.py` — stdlib-only, imports nothing from the package, because a diagnostic that
needs the world healthy cannot diagnose an unhealthy one (same rule as `tools/basex/preflight.py`).

**Two severities, deliberately not the same response:**

| | |
|---|---|
| **READS** | a **banner** on stderr. A wrong answer can be re-taken once the window closes (~70 s), and refusing would break an unrelated session's work for a value they can simply ask for again. |
| **WRITES** | **refuse, rc 2.** A poisoned *artifact* outlives the window and is trusted afterwards with nothing to say it was born during one. |

The banner rides on `_paths.refuses_unconfigured`, which is the only place already **guaranteed** to
wrap every entry point — `tests/test_unconfigured_refusal.py` asserts that mechanically, so coverage
of all 9 CLIs comes for free rather than by remembering. The refusal sits at the **two stamping
sites** (`_effective.py`, `_xref.py`) rather than at argument parsing, so no other entry path can
slip past it: the durable damage happens where the bytes are written.

⚠ **The trap this avoids, and it would have been silent:** the marker lives in the package root, but
these tools are normally run **from the game directory**. A CWD-relative lookup would find nothing
and report all-clear from the one place it matters most — F46's `Path("")` fallback wearing new
clothes. `marker_path()` resolves from `__file__`, and a test pins it against the probe's own
constant so the two halves cannot drift apart silently.

**A defect found in this change, by running it rather than reading it.** The first cut let
`TreeMutating` propagate out of the CLI: a raw traceback and **rc 1** — which means *"the thing you
asked about has findings"*, not *"cannot run"*. That is precisely the F39/F47 distinction, and it was
reintroduced **by the change whose own docstring warns about it**. Now caught in the decorator →
rc 2, no traceback. E2E verified both directions with the marker planted and then cleared.

**Why the marker earns its keep twice over** — the second use is the parallel session's, and it is
not the one it was designed for. It is not only PROSPECTIVE (*should I read now?*) but
**RETROSPECTIVE** (*is the measurement I already took admissible?*). They ran `claims_audit` and
checked the marker before and after specifically so they could **state** the result was trustworthy
rather than assert it. A result can now be qualified after the fact, not merely avoided beforehand.

## F60 — a file-by-file port SPLIT a commit and shipped half of it · **DEFECT** · confidence 99% · ✅ FIXED 2026-08-26

> **2026-09-26 (AUDIT-2026-09-24 DC-3) — the check this entry cites is RETIRED; the text
> below is history.** `verify-port.py` was retired with the dev repository on 2026-09-13
> (source in that repository's archive bundle). **Nothing re-derives this entry now, and
> nothing needs to:** the defect is a split between TWO repositories, and since 2026-09-14
> there is one, so a file-by-file port no longer exists to split a commit. ⚠
> `gates/register_rederivation.py` still counts this entry as covered, by
> `tests/test_freshness.py` — but that path is quoted in the table below as EVIDENCE of the
> split, not as a check of the fix. A mention satisfies that gate's existence rule.

**NO RE-DERIVATION (2026-09-26):** the population the fix guarded (a dev → mirror port) no
longer exists; see the note above.

**The shape.** Porting dev → public mirror one file at a time has no notion of a commit, so a
change set that spans five files can cross over in part. `f5c976d` (F56/F57/F43) touched
`_freshness.py`, `_effective.py`, `_modlist.py`, `_check.py` and added `_changed.py`. **Two crossed
over. Three did not.** And the selection rule was not a rule: those two crossed **only because I had
independently edited them in my own commits**, so the port carried them as a side effect.

**What public `master` shipped as a result** (READ, in the mirror's own source):

    x4validate/_freshness.py:509   "(run `x4modlist changed` to see which) "
    x4validate/_freshness.py:96    docstring references `x4modlist changed --files`
    x4validate/_modlist.py         ZERO references to `changed` or `snapshot`
    x4validate/_changed.py         does not exist
    tests/test_freshness.py:144    points at tests/test_changed_cli.py, also absent

So on the one screen where a user most needs help — a stale artifact refusing to answer — the tool
**instructs them to run a subcommand it does not have.** Worse than a missing feature.

**MEASURED 2026-08-26**, dev `3d3c683` vs mirror `5a2b373`, both clean:

| | count |
|---|---|
| dev-tracked files | **137** |
| identical (committed blobs) | 116 |
| real content difference | **12** |
| dev-only, deliberate | 4 |
| dev-only, **unexpected** | **5** |
| mirror-only | 0 |
| differing ONLY by line endings | **40** |

**Why nothing caught it.** The documented proof was `diff -rq` (`docs/QA-PROCESS.md:217`), and on
this pair of trees that instrument reports **52 differences where 12 are real** — the other 40 are
line endings, because dev's working tree was renormalised to LF by F53 while the mirror's is a stale
pre-pin checkout and **both repos store the same LF bytes**. A proof that buries 12 findings under
40 non-findings trains you to skip it. The rule was the checker, and the checker was wrong.

**⚠ NOT on a release.** `git show v2.6.0:.../\_freshness.py` has **zero** mentions; `HEAD` has two.
`master`-only, which is why this is a defect and not an incident.

✅ **FIXED 2026-08-26** — `scripts/verify-port.py` (dev-only). Classifies every dev-tracked file into
six buckets that must **sum to the population**, compares **committed blobs** rather than
working-tree files, keeps the dev-only allow-list as a **literal with a reason per entry** (an
unlisted dev-only file is a FINDING, never a default), and runs the mirror's own identifier matcher
over the port subset. `--selftest` plants a divergence, an unlisted dev-only file, a mirror-only
file and an unreadable one and requires each to be reported: **7/7**. `QA-PROCESS.md`'s rule 2 is
rewritten to point at it and to warn off `diff -rq` with the measurement.

**The honest limit.** This catches an unfaithful port; it does not make porting atomic. A commit
that lands in dev *while* a port is in flight is still a split waiting to happen, which is why the
procedure also pins the source SHA and re-checks it before committing the mirror.

---

## F61 — the identifier scrub has no dev-side control, so it regressed in 25 hours · **PROCESS** · confidence 97% · ✅ FIXED 2026-08-26

> **2026-09-26 (AUDIT-2026-09-24 DC-3) — the check this entry's fix cites is RETIRED; the
> text below is history.** `verify-port.py` was retired with the dev repository on
> 2026-09-13, and with one repository there is no "dev side" left. **What covers the class
> now:** `scripts/scan-identifiers.py` itself, run LOCALLY by `scripts/test-hooks.sh` (its
> `--selftest` first, then the tree scan; rc 1 fails the suite) and in CI (the tree scan,
> plus `--history` over the push range, pinned by `tests/test_scan_identifiers_history.py`).
> ⚠ The local run still depends on someone running `scripts/test-hooks.sh`: MEASURED
> 2026-09-26, no git `pre-push` hook is installed in this clone and `core.hooksPath` is
> unset. That is this entry's shape — a control that fires only after the push — reduced,
> not removed.

`2b2b89a` (2026-08-25 15:36) genericised three personal overlay folder names out of
`gates/schema_sweep.py`, because that file is mirrored and the names embed a username. **MEASURED
2026-08-26, the same strings were back:**

| file | lines | introduced by |
|---|---|---|
| `gates/schema_sweep.py` | 246, 247, 249, 253 + 303, 317, 318 (**7**) | `de21b8a` — a new re-baseline block, and a 74-row per-mod table |
| `x4validate/_check.py` | 1728 (**1**) | `f5c976d` — an F52 explanatory comment |

**⚠ State the severity honestly: the guard WOULD have caught this.** EXECUTED, not read —
`scripts/scan-identifiers.py` derives its ban list from the mirror's own commit metadata (14 tokens,
6 banned after allowing deliberate attribution) and matches case-insensitive substrings, so both
lines match while two benign controls (`Synthetium_Music`, a Steam absolute path) do not. Public CI
would have gone **red**. Nothing was going to reach the public silently.

**The finding is that the only control fires POST-PUSH.** Dev has no equivalent, which is why the
same regression could be written twice, in the same file, within a day, with nothing local
objecting. A guard that can only fail after the mistake is published is a guard you cannot use while
working.

**A second thing the re-check found, and it is the larger one.** The 74-row table added by `de21b8a`
names every mod contributing a schema pair — one person's entire modlist — inside a **mirrored**
file. MEASURED against the 129 installed folders: the public copy already names **44**; porting
unchanged would have made it **79 (+35, including six personal overlays)**. The table has moved to
`AUDIT-2026-08.md` (dev-only), taking the delta to **+3** — all public third-party mods, each
load-bearing in an attribution, which is the line `2b2b89a` drew when it kept `Station_ink`.

✅ **FIXED 2026-08-26** — `scripts/verify-port.py` runs the mirror's matcher over the port subset
**before** anything is copied. Proven falsifiable rather than assumed: **9 hits** against the
pre-scrub bytes, **0** across the 133-file port set now. The letter → folder key lives once in
`AUDIT-2026-08.md`, so genericising the comments is lossless — that per-mod attribution exists in no
other durable record.

---

## F62 — a silent existence-filter narrowed a mutation scope, and the test that should have caught it asserted `any` · **DEFECT** · confidence 96% · ✅ FIXED 2026-08-26

`gates/mutation_probe.py::run_tests` did `present = [t for t in tests if (ROOT / t).exists()]` and
handed only `present` to pytest. Filtering a vanished path is the right thing to **do**; doing it
**silently** is the founding defect shape of this register — *a step that narrows the data and
reports success anyway* — and it was sitting inside the one gate built to detect vacuous assertions.

**MEASURED.** The mirror's `TARGETS["_registry.py"]` lists four test files and only three exist
there, because `tests/test_profile_is_a_decision_log.py` was never ported (F60). The public probe
therefore ran **3 of 4** and printed the same `killed` line as a full scope.

**Two more in the same function, both present in dev as well:**

- `run_tests` returned the verdict **`"hang"`** when *no* test file was present. An absence rendered
  as a specific finding — and since `3d3c683` re-runs on `"hang"`, the caller then "confirms" a
  timeout it never measured, against a file that is still not there. Masked only by accident,
  because the re-run path uses `full_suite()`, whose directory always exists.
- **The pinning test was itself the narrowing step.** `tests/test_mutation_probe.py` named a test
  `test_every_target_has_tests_that_exist` and asserted `any((root / t).exists() for t in tests)`.
  The NAME promises every file; the ASSERTION accepts one. Three of four present **passed**.

**⚠ A prediction that was WRONG, recorded because a register of only correct guesses has no
denominator.** I expected the missing path to reach pytest, error, and be read as a false **KILLED**.
It does not: the filter runs first. The truth is milder in effect and worse in family — a silent
narrowing rather than a loud wrong answer.

✅ **FIXED 2026-08-26** — a partial scope now NAMES the files it dropped; an empty scope returns a
new verdict **`noscope`** with its own bucket, excluded from the killed count and failing the gate;
an empty BASELINE scope returns **rc 2** (cannot run), never rc 1 (has findings). The check became
`mp_scope_gaps()`, which returns the **named gaps** rather than a boolean, asserted with `all`.

**The asymmetry is the proof, and it is exact:** `mp_scope_gaps()` is GREEN against the dev tree and
RED against the mirror's, reporting `{'_registry.py': ['tests/test_profile_is_a_decision_log.py']}`.
Six tests written first, all six watched fail for the right reason — one of them printing
`killed 1/1` for a mutant that was never challenged, which is the defect in miniature. Probe re-run
for real afterwards: **11/11 killed, 0 survivors, 0 hangs, 0 unmeasured.**

## F63 — a helper that cannot say "there is no extensions root" · **DEFECT** · confidence 97% · ✅ FIXED 2026-08-27

**One root cause, two symptoms. The first is fixed; the second is open and is the more interesting
of the two.**

`_effective._ext_root(config)` answers *"which extensions directory was this store built over?"* It
returns `_registry.GAME_EXTENSIONS`, guarded by `except AttributeError`. **The guard never fires:
the attribute EXISTS and its VALUE is `None` when nothing is configured.** So the helper has no way
to express *"there is no root"* — it returns `None` and lets the caller decide what that meant.

### Symptom 1 — the cold crash · ✅ FIXED

`_freshness.fingerprint()` refused a `None` extensions root. That refusal is **correct** and is
F46's guard: it must never fall back to `Path("")`, which is `Path(".")` and hashes whatever
directory you are standing in. But it could not distinguish *"the caller forgot"* from *"the caller
looked and there is none"*.

**MEASURED 2026-08-26:** 5 tests in `tests/test_effective.py` fail on any machine with **no X4
installed** — which is every fresh clone and every CI runner. Public CI was **RED for two
consecutive runs** (`32991964950`, `32992437292`), and the CI log is byte-identical to
`verify-cold.sh`'s output. ⚠ **Nobody looked.** The handoff note said CI "had not run due to a
GitHub Actions outage"; it had run, and it had failed. This is F60's real cost — not the dangling
`x4modlist changed` reference, which was cosmetic, but a red master nobody checked.

**NOT reachable by a real user.** The cold CLI matrix shows `x4effective` refusing with **exit 2** at
the door, so `build()` is never reached without configuration. Test-reachable only, which is why it
sat unnoticed once CI stopped being read.

✅ **FIXED** — `fingerprint(config, extensions=_UNSET)`, the sentinel this module already uses for
`profile`. Omitted still **RAISES** (F46 intact); an explicit `None` records the content axis as
**UNKNOWN**. `_freshness.py` is deliberately **not** in `ENGINE_SOURCES`, so the engine hash stayed
`b25a2a99853d4c84`, the effective store stayed fresh, and no BaseX index was invalidated.

⚠ **The trap this had to avoid, caught by writing the test first.** `None == None`, so a naive
comparison makes two UNKNOWN content axes **match** and report **FRESH** — an artifact that cannot
say which world it describes, declaring that it still describes it. The test failed with exactly
`Verdict(fresh=True, reasons=[])` before the fix. `compare()` now returns NOT fresh whenever either
side is UNKNOWN, saying freshness could not be **established** — neither fresh nor known-stale.

Incidentally fixed the same latent crash at `_xref.py:352`, which passes `GAME_EXTENSIONS` straight
through.

### Symptom 2 — the warm wrong-world stamp · ⚠ OPEN, deliberately

Warm, `_ext_root()` returns the **real** game extensions path even for a store built over throwaway
test directories. MEASURED: `_ext_root(Config(reference=/tmp/x))` returns
`C:\...\X4 Foundations\extensions`. **So such a store is stamped with a fingerprint describing a
world it was never built from** — a store claiming provenance it does not have, which is the exact
failure the toolkit's "a tool that cannot distinguish a GUESS from a MEASUREMENT is a defect" rule
exists to prevent.

**Deliberately not fixed here**, and the reason is stated so it is not mistaken for an oversight:
`_effective.py` **IS** an `ENGINE_SOURCE`, so the fix moves the engine hash and requires an effective
store rebuild plus a BaseX `x4eff` rebuild that belongs to another session. That is a planned
operation, not something to bolt onto a port whose job was unblocking red CI.

⚠ **Its cost today is genuinely zero** — only test-built stores are affected and they are discarded.
Gotcha #23 says a recorded cost of zero is exactly where a wrong denominator hides, so the
population is stated: **2 call sites** (`_effective.py:813`, `:872`), reached by **5 tests** and by
`store_freshness()`. No shipped artifact carries a wrong stamp, because every real build resolves a
real root. Re-derive that population when this is picked up, rather than trusting this line.

### The instrument lesson

`scripts/verify-cold.sh` runs `git archive HEAD` — it verifies the **committed** state, not the
working tree. Correct for a release check, and it means a fix under development reads as still
broken until committed. I lost one cycle to that. The script's own preconditions are sound; this is
about what question it answers.

Also: `env -u X4_REFERENCE -u X4_EXTENSIONS ...` did **not** produce a cold environment — resolution
still fell through to `$X4_TOOLKIT`. That is the documented trap `verify-cold.sh` exists to defeat,
and I nearly reasoned from its output. **Only `verify-cold.sh` can answer a cold question here.**

## F64 — `origin` / `chain` record WHO WON, never WHO INTRODUCED IT · **ADJACENT-ANSWER** · confidence 95% · ✅ FIXED 2026-08-27

**RE-DERIVED BY: `tests/test_provenance.py` (behaviour) — but NOT the number.** The four tests pin the note's contract: it fires on a single non-base chain over a vanilla vpath, and is SILENT for a mod-added vpath, a pure-base value, and a chain that already names base. The headline **23,182 of 35,423 (65.4%)** is PEER-MEASURED, one-time, and would drift with the modlist, so it is deliberately quoted rather than asserted — pinning it would produce a gate that fails whenever a mod is added, which trains you to ignore the runner. **This is the stated exception, not an oversight:** the CLAIM is re-derived, the CENSUS is not.
**Not a correctness bug.** `x4effective` is right for its contract: it models the merged tree and
reports which mod supplies the live value. The defect is that it answers an **adjacent question**
confidently, and the output carries no signal that the question was adjacent.

**The mechanism.** A root `<replace sel="//macros">` swaps the whole document, so the base document
contributes **no chain entry at all** — and that is VRO's dominant idiom (**848 root-replaces**,
CLAUDE.md #10). A single-entry chain then reads as *"this mod introduced this value"* when it very
often means *"this mod re-supplied a value vanilla already had."*

**MEASURED by the parallel session** over the store at `content=a42c3d2fcdaeecb9
engine=b25a2a99853d4c84` — quoted as theirs, not reproduced here:

| | count |
|---|---|
| attributes whose chain contains a root-replace | **36,872** across 897 vpaths |
| single-op root-replace attrs with a vanilla file present | 35,423 |
| …attribute **ALSO EXISTS in vanilla**, chain hides it | **23,182 (65.4%)** |
| vpaths with no vanilla counterpart (truly mod-added) | 39 |

Top hidden props: `component.ref` 848 · `identification.name` 701 · `identification.description` 701
· `identification.basename` 668 · `identification.makerrace` 498 · `hull.max` 496 ·
`identification.shortname` 444 · `hull.integrated` 313 · `identification.mk` 274 · `physics.mass` 249.

**Two thirds of root-replaced attributes credit a mod for something vanilla already had.**

**The case that produced it, and it cost a real design conclusion.**

    x4effective who-sets macro bullet_kha_l_beam_02_mk1_macro damage.shielddisruption
      -> vro replace-root:3     chain = [["vro","replace-root",3]]

read as *"VRO added Kha'ak shield disruption."* **False** — vanilla's
`bullet_kha_l_beam_02_mk1_macro.xml` carries `shielddisruption="10"`. The sharpest case is
`bullet_arg_m_ion_01_mk1_macro`: **vanilla 10, live 10, chain `[vro]`** — the value is *identical*
and the mod gets sole credit, with nothing in the output hinting a base file even exists.

**⚠ THE SAME SHAPE APPLIES TO `x4compat.Collision.winner`** (CLAUDE.md #18, F25), which is also a
who-wins answer readable as who-owns. **Winning and originating are different questions across this
whole toolkit, and no tool currently states which one it is answering.** That generalisation is the
reason this earns a register entry rather than a footnote.

**Proposed mitigation, deliberately cheap:** have `who-sets` state whether a vanilla file exists at
the vpath and whether it carries the same prop — e.g. *"(vanilla file exists and ALSO sets this
prop = 10 — chain shows the WINNER, not the origin)"*. No merge-model change, no schema change.

**Deliberately NOT fixed yet, and bundled on purpose:** `_effective.py` is an `ENGINE_SOURCE`, so
this moves the engine hash and forces a rebuild of the effective store **and** BaseX `x4eff`.
**F63 symptom 2 is in the same file** — doing both together costs one rebuild instead of two.

**Credit:** found and measured by the parallel session, recorded in `KNOWLEDGEBASE.md`
§ *2026-08-26h* with the full table and the routing rule (*never ask a provenance-of-invention
question of a merged tree — read `reference\`, per gotcha #14*). Registered here at their request.

**One more checker bug, theirs, worth carrying:** their first pass reported **2.6%** and every
example it printed was `component.ref`. That uniformity was the only tell — their flatten produced
`properties.damage.shielddisruption` while **the store strips the `<properties>` wrapper** to
`damage.shielddisruption`, so only keys OUTSIDE `<properties>` could ever match. **If you compare
store props against raw XML, the store strips `properties.` — and a naive flatten will silently
agree with itself.**

---

## F65 — `EntityDefs` costs its corpus tier PER NAME, on a population model that only holds for a mod · **SCOPE** · confidence 97% · ✅ FIXED 2026-08-27

`_check.py::EntityDefs.__contains__` resolves a name against an eager index, then a mod-local tier,
then falls through to `_search_corpus` — which re-enumerates **base + DLC + every overlay** to answer
for **one name**, at roughly **2.5s** a call.

**This is not a defect, and the docstring is not silent.** It states both the justification and the
population it was measured against:

> Per-name cost is affordable because the tier is rarely reached at all: MEASURED, 7 distinct
> references across 114 mods miss the eager tiers — at most 2 for any single mod.

That is true, and for the caller it was built for — one mod under validation — it is the right
design. `_search_corpus`'s own docstring records that building the whole set instead was measured at
8.9s and rejected because `gates/perf_guard.py` flagged three regressions.

**The blind spot is that the population is an assumption of the CALLER, and nothing enforces it.**

**MEASURED 2026-08-26**, feeding it a savegame's reference set instead of a mod's:

| | count |
|---|---|
| distinct `macro=` references in one save | **5,022** |
| resolved by the eager index (10,783 names) | ~2,500 |
| falling through to the per-name corpus tier | **~2,500** |
| observed outcome | **600s cap exceeded, no result** |
| same question via the bulk path `_scan` | **19.4s** |

**The shape.** A per-item cost that is correct at n≈7 and unusable at n≈2,500, with no signal at the
boundary. The tool does not know its caller's population, and the only thing standing between a
correct answer and an unbounded run is a docstring. It is the mirror image of most entries in this
register: nothing narrowed the data and nothing reported false success — it simply never returned.

**Fix:** a public `EntityDefs.all_names()` that exposes the bulk definition set built once. Without
it, a many-name caller's only option is `EntityDefs._scan(...)` — a cross-module reach into a private
method, which is its own hazard and would break silently in a refactor.

**Scope of this claim:** measured on the savegame caller only. No existing in-tree caller is affected
— `x4validate` passes one mod at a time, which is the population the design assumes. The cost today
is **zero for shipped code**, and stated as such rather than left implied, because "the cost is zero"
is the line this register has twice found nobody re-checks (F22).


## F66 — producer/consumer key normalisation, investigated and CLEAR · **NEGATIVE** · confidence 90%

**Recorded because a register without negatives has no denominator.** This line of inquiry was
opened on the theory that every normalised key has a *producer* and a *consumer* with nothing forcing
them to agree — the founding narrowing-step shape arriving through the back door. **Predictions were
written down BEFORE measuring so a null result would still count.** It is null.

| # | prediction | conf | outcome |
|---|---|---|---|
| P1 | >= 5 distinct normalisation sites | 85% | ✅ true (19 modules carry normalisation calls) |
| P2 | **at least one producer/consumer pair disagrees, silently** | 55% | ❌ **FALSE** over 3 pairs |
| P3 | no test pins the `properties.` strip | 75% | ❌ **FALSE** — `test_prop_depth.py:64` pins it |
| P4 | case-folding is the likeliest disagreement | 60% | ❌ **FALSE** — that is the best-defended pair |
| P5 | any disagreement would be invisible in output | 90% | untested — none found |

**What was measured (2026-08-27), using the toolkit's OWN producer and consumer at every step and
never a reimplementation** — the trap that made a peer's version of this check report 2.6% where the
answer was 65.4%:

| pair | producer | consumer | result |
|---|---|---|---|
| prop keys | the store's **44,708** distinct `attrs.prop` | 18 hand-written literals across `gates/` + `dev/_registry/CLAIMS.tsv` | **18/18 agree**; **0** consumers use the stripped `properties.` prefix |
| vpath case | `_cat.mod_vfs` — case-**preserving** | `_cat._get_ci` / `read_path` — case-**insensitive** | agree by design; **0 of 21** external call sites bypass the door |
| cross-door | `_scan.iter_mod_xml_bytes` | `_cat.read_path` | 216 lookups, 12 misses, **all `content.xml`**, **0 unexplained** |

### The structural property that makes the class absent — and it is UNDEFENDED

**Consumers ITERATE the mapping; they do not look it up by key.** Every external call site is
`for vpath in mod_vfs(...)` or `.items()`, which cannot suffer a case disagreement at all. The only
two direct dict accesses in the package are inside `_cat.py` itself: `:150` building the VFS, and
`:256` the exact-hit fast path **within** `_get_ci`, which falls through to `_folded_vfs` on a miss.

`base_has`'s docstring had already noticed this locally — *"all three ITERATE the mapping rather than
look up a key"* — but as an observation about three call sites, not as a property of the package.

⚠ **Nothing enforces it.** It is a convention held by 21 call sites, not a guard. **A future consumer
that does `vfs[vpath]` or `vfs.get(vpath)` instead of going through `read_path`/`_get_ci` would
reintroduce this entire class silently**, and on Windows it would not even fail locally — NTFS is
case-insensitive, so the bug would appear only on a Linux user's machine or in ubuntu CI.

### Coverage, stated honestly

**3 pairs examined, not all of them.** `_merge._nested_target`'s rewrite and `_xref`'s cue keys were
identified as pairs and NOT round-tripped. So this is *substantially narrowed*, **not exhaustively
closed** — the correct claim is "no disagreement in the three pairs that carry durable keys and have
known live cost", not "the toolkit has no normalisation mismatch."

### Checker errors made while measuring this — 3, all mine, all caught

1. A literal-extraction regex matched **filenames** (`md.xsd`, `modlist.yaml`, `pyproject.toml`) and
   reported **5 false silent-empty consumers**. The extension blocklist missed `.xsd/.sqlite/.yaml/.toml`.
2. `CLAIMS.tsv` was sampled at **column 4** (values) with a header-skip it does not have; the prop key
   is **column 3** and the file has no header row. Fixed parse yields **21 data rows — and
   `claims_audit` independently reports 21/21**, a total the parser could not fake.
3. **A misattributed line number**: `_cat.py:222` is `_folded_vfs`, not `mod_vfs`, so "31 of 207 keys
   are not lowercase" read as a defect when it is correct-by-design case preservation.

**Instrument-error base rate holds: 3 of 3 were the checker, not the finding.**


## F67 — the line-ending pin is never checked against the bytes it governs · **DEFECT** · confidence 97% · ✅ FIXED 2026-08-27

**F53 added `.gitattributes` pinning `*.py text eol=lf` so the engine hash would stop depending on
how a file arrived on disk. That pin is correct and it is not self-enforcing.** It normalises on
checkout; a file already sitting in the tree from before the pin keeps its endings until something
rewrites it. Nothing counts the bytes.

**MEASURED 2026-08-27**, found while making an unrelated edit to the same file:

| file | working tree | HEAD |
|---|---|---|
| `_merge.py` `_diff.py` `_cat.py` `_xpath.py` `_scan.py` `_registry.py` | LF | LF |
| **`_effective.py`** | **CRLF, 1205 lines** | LF |

**6 of 7 had been normalised by ordinary tooling churn; the 7th had not been touched, so it kept
CRLF and nobody looked.** The engine hash reads raw BYTES, so:

    HEAD blobs, all LF (what a fresh clone gets)   -> 3239fa6c515b83b6
    same blobs, _effective.py as CRLF (this box)   -> b25a2a99853d4c84

**The second is the hash this workspace quoted all evening.** It was an artifact of one stale file.

**Why it survived F53 and two days of use: it fails SAFE.** A wrong engine hash produces a false
STALE, never a false FRESH — so the only symptom is a rebuild you did not need, which reads as normal
operation. **The register's own rule applies: a recorded cost of zero is where a wrong denominator
hides** (#23), and "fails safe" is the same trap wearing a different coat.

**The instrument lesson, and it is mine:** I flipped F53 to ✅ FIXED earlier the same session having
verified that the pin **existed** (`.gitattributes` text, `git check-attr eol: lf`) and never having
counted a single `\r\n` in the files the hash reads. **A pin is a statement of intent; the bytes are
the fact, and only the bytes were the question.**

**Proposed guard (not yet built):** a test that, for every `_freshness.ENGINE_SOURCES` file, asserts
`raw.count(b"\r\n") == 0` whenever `.gitattributes` pins LF — falsifiable by planting a CRLF file,
and it would have caught this on the day the pin landed. ⚠ Must run on the **working tree**, not on
`git show`, because git normalises on read and would report clean either way — the same
committed-vs-working-tree distinction that made `diff -rq` useless as a port proof (F60).

### ✅ FIXED 2026-08-27 — and the population was WIDER than the finding that exposed it

`tests/test_line_ending_pin_is_obeyed.py`. It asks **git** what the pin resolves to
(`check-attr eol --stdin`, one subprocess) rather than re-implementing gitattributes matching, and
reads the **working tree**, never `git show`. **No allow-list** — an offender is normalised, not
excused.

**Corrected population.** F67 was raised on **one** file. Scanning the pin's real scope found:

| tree | pinned | carrying CRLF |
|---|---|---|
| dev `tools/x4validate` | 129 | **8** — incl. `_paths.py`, `_savecli.py`, `_xref.py` and 4 gates |
| public mirror | 155 | **14** — incl. `bin/xrcat` and `.claude/x4-paths.env.example` |

**22 files, not 1.** Two of the mirror's are **functionally** broken by CRLF rather than cosmetically:
both are sourced by bash, where a trailing CR puts `$'\r'` inside every value. A fresh clone was
never affected — checkout applies the pin — but nothing would have caught the next file to drift.

All 22 normalised as **pure repairs**: CRLF -> 0, line counts unchanged, byte delta exactly the CRs
removed, and **`git diff HEAD` empty in both trees** because git already stored them LF. Zero overlap
with `ENGINE_SOURCES`, so the engine hash stayed `40b64b579f2e07ab` and the rebuilt store stayed
valid — asserted, not assumed.

⚠ **TWO BUGS IN THE GUARD WHILE BUILDING IT, and they are the reason it carries a denominator.**
It first returned **0 pinned files**, and the deliberate `len(pinned) > 20` assert caught it —
without that it would have passed **vacuously over an empty population and certified the pin as
obeyed**, which is the founding narrowing shape *inside the guard written to detect it*. The cause
was `text=True` on the subprocess: **Python translates newlines on the way IN on Windows**, so an
LF-separated stdin reached git CRLF-separated and `check-attr` answered about paths with a trailing
CR — `unspecified` for all 141. **A line-ending guard defeated by a line ending in its own input.**

And the **first diagnosis was wrong while fitting the evidence perfectly**: I blamed splitting
`ls-files` output on a bare newline, which would also have produced a stray CR. The CR was **added on
the way IN**, not left behind on the way out. Fixing the wrong end changed nothing, and the
denominator assert is the only reason that was visible.

## F68 — `attr` printed a confident zero over a key that cannot exist · **DEFECT** · confidence 99% · ✅ FIXED 2026-08-27

**RE-DERIVED BY: `tests/test_effective_attr_states_its_scope.py`.** The entry did not name it until 2026-08-29, which is its own small instance of this register's theme: a check existed, was correct, and could not be found from the record that depended on it.
`_reject_unknown_kind` exists precisely for this, and its own docstring states the doctrine:
*"without this an unknown kind reads as a confident empty answer."* It is called from `ls`, `show`
and `who-sets`. It was **never called from `attr`** — the one command that takes two free-form
arguments, either of which can be plausible while the pair matches nothing.

MEASURED 2026-08-27 against the live store (content `ef9edde0b425bdc4`):

| command | before | rc |
|---|---|---|
| `attr zzznotakind hull.max` | `0 value(s) for hull.max` | **0** |
| `attr macro properties.hull.max` | `0 value(s) for properties.hull.max` | **0** |
| `attr ship hull.max` | `0 value(s) for hull.max` | **0** |
| `who-sets macro <name> properties.hull.max` | `no prop '...' on <name>` | 1 ✅ |

The third row is the sharpest: `ship` **is** a stored kind (514 entities) and `hull.max` **is** a
stored prop (1,973 values) — they simply live in different kinds, so both arguments survive every
guard the tool had and the pair still returns a clean-looking empty answer at exit 0.

**What the parallel session paid for it.** Comparing store props against raw XML, their flatten
produced `properties.damage.shielddisruption` while the store holds `damage.shielddisruption`. Only
keys OUTSIDE `<properties>` could ever match. First result **2.6%**; true result **65.4%**. Nothing
errored and the numbers were self-consistent; the only tell was that every printed example was
`component.ref` — the one common attribute living outside the wrapper.

**⚠ The obvious fix would have been wrong, and this is the part worth keeping.** The requested
remedy was one documentation line: *"prop names are relative to the macro, with the `<properties>`
wrapper stripped."* MEASURED before writing it: **6,842 rows carry a `properties.` prefix, every one
of them kind='mapdataset'** (`properties.area.sunlight`, `properties.area.economy`), and a
self-join found **0** rows where an entity holds both `x.y` and `properties.x.y` — so F9 has **not**
regressed and those keys are real. The strip is a **macro** convention, not a store convention.
Shipping the one-liner would have installed a new wrong belief covering all 6,842. *A rule is an
instrument too* (CLAUDE.md #22 / F53).

That is why `_prop_suggestions` **asks the store** instead of applying a rule: it offers the
stripped key only when that key actually exists for that kind, so it stays silent on mapdataset.

**The fix.** `_reject_unknown_prop` prints the denominator first, then store-derived suggestions,
then any other kind that carries the prop:

    $ x4effective attr macro properties.hull.max
    no macro carries prop 'properties.hull.max' - that kind has 8672 distinct prop(s)
           did you mean 'hull.max'? (1973 value(s))

    $ x4effective attr ship hull.max
    no ship carries prop 'hull.max' - that kind has 31 distinct prop(s)
           'hull.max' is carried by kind 'macro' (1973 value(s))

**Verification.** 8 tests written first and watched fail — **3 RED, 3 GREEN on the first run**, which
is itself the evidence they are not vacuous: the three that passed before any fix are the ones that
must keep passing. They include a **falsification twin** where the prop exists for the kind and the
`--class` filter legitimately excludes every row: that must stay **rc 0**, so the guard cannot pass
by rejecting whatever returns empty. A second twin pins the mapdataset case as a normal answer, and
a third proves the cross-kind hint stays silent when no other kind carries the prop.

**Found in passing:** the guard's own docstring had rotted. It claimed *"ships are stored as
kind='macro'"*; a full build now stores `ship`, `station` and `module` as kinds in their own right,
which makes three of the seven entries in its special-case hint set unreachable. The entries **stay**
— `--kinds` can build a subset, and that is exactly when the hint earns its place (CLAUDE.md: assume
content is live until proven dead). Only the false sentence was corrected.

## F69 — the engine freshness axis is coarser than the thing it models · **SCOPE** · confidence 90% · ✅ FIXED 2026-08-27

**RE-DERIVED BY: `tests/test_engine_sources_carry_no_cli.py`.** It asserts that no `ENGINE_SOURCES` module imports argparse or otherwise carries CLI surface, which is the property whose absence caused two semantically-empty invalidations. Named here from 2026-08-29; the test predates the naming.
The `engine` axis hashes the whole **bytes** of seven `ENGINE_SOURCES`. That is deliberate and it is
why it survives a dirty tree where a commit hash would not (see the freshness contract). The cost is
that it cannot distinguish *merge semantics changed* from *an error message changed*, and the banner
it produces states the former:

> `engine changed: the merge code that produced this has been edited, so the SAME inputs would now merge differently`

For F68's edit that sentence is **false**. Nothing about any merged value moved; only CLI text and an
argument type-guard did. The hash went `40b64b579f2e07ab` to `caef9277b49246c4`, and both the
effective store and BaseX `x4eff` then required a rebuild that could not change a single row.

**Then it happened AGAIN in the same session, which is the better evidence.** Adding a
**docstring** to `_registry.mods()` — MEASURED by `git diff HEAD`: 10 lines added, 0 removed,
**0 executable** — moved it a second time, to `e3c5f4d5536d239d`. Two consecutive edits, neither
capable of changing a merged value, two full invalidations of every derived artifact. The second
was pure documentation; there is no reading of *"the merge code has been edited, so the SAME
inputs would now merge differently"* under which that is true.

✅ **Cross-checked against a second session 2026-08-27:** the parallel session independently
computed `engine=e3c5f4d5536d239d` from its own tree and quoted it back before this entry was
amended. Two sessions, same bytes, same hash — which is exactly the portability F67 bought, and
it is the reason a hash quoted in this register can now be checked by someone else at all.

**MEASURED 2026-08-27**, by AST over the current sources, counting functions whose names mark them as
CLI/presentation (`_cmd_*`, `main`, `_fmt_*`, `_count_line`, `_reject_*`, `scope_note`):

| source | lines | CLI lines | share |
|---|---|---|---|
| `_effective.py` | 1,360 | 387 | **28.5%** |
| `_diff.py` | 276 | 69 | **25.0%** |
| `_merge.py` · `_cat.py` · `_xpath.py` · `_scan.py` · `_registry.py` | 2,176 | 0 | 0% |
| **total** | **3,812** | **456** | **12.0%** |

**12.0% is a LOWER bound.** The classifier keys on function names, so argparse construction inside
`main` and module-level display constants are not counted. Two of seven sources carry all of it;
five are clean.

**⚠ State the direction plainly: this fails SAFE.** It over-reports staleness and can never
under-report it, so no answer has ever been wrong because of it. What it costs is rebuild time and,
more importantly, **credibility** — a banner that cries stale for a docstring is the shape that
trains you to skip the banner, which is the failure mode the whole freshness contract exists to
prevent (*"a check that floods is worse than no check"*).

**Why this is OPEN rather than fixed.** The tempting fix — hash the AST, or the non-comment tokens —
makes the axis smarter and moves it to the **unsafe** side: a semantic hash that misses one real
merge change reintroduces the 2026-08-13 defect in which a design decision recorded vanilla engine
values as VRO's (**140 of 194 rows, 72%**, moved on rebuild, with no input file having changed).

The mechanical fix is to make the *population* right rather than the *hash* clever: move the
`_cmd_*`/argparse surface into `_effectivecli.py` and `_diffcli.py`, and leave `ENGINE_SOURCES` as
merge semantics only. Provable, boring, no behaviour change, and it moves the hash exactly once. It
also touches a released tool and a shared derived store, so it is a decision to take deliberately —
registered here on the day the edit that provoked it was made, rather than done as a drive-by.

## F70 — a nested cross-mod script patch is validated by nothing · **DEFECT + SCOPE** · confidence 90% · ✅ FIXED 2026-08-27

Found by **red-teaming a plan**, not by a failure — the user asked for the assumptions to be
disproved before implementing, and the first thing that fell over was code shipped hours earlier.

**(a) The disclosure under-counted.** `check_script_validation_scope` matched script files with
`low.startswith(("md/", "aiscripts/"))`. A cross-mod patch lives at
`<mymod>/extensions/<target>/md/foo.xml` and does not start with `md/`. This is the trap
`_xsd.eligible`'s docstring already records having fixed once (2026-07-29, 5 MD scripts).

| | files | mods |
|---|---|---|
| counted by the shipped test | 390 | 77 has-script / 17 script-only |
| counted after stripping `extensions/<target>/` | **405** | **79 / 18** |
| **missed** | **15** | 7 mods |

Every published figure for the feature was therefore wrong — in two CHANGELOGs, a code comment, a
test docstring, and a message to the parallel session. All corrected.

**(b) The larger half: nothing validates them, and a docstring said something did.**
Both halves of `_xsd.validate_mod` filter `count("/") != 1`, with the comment *"Depth matches the
loose glob above — direct children only — so the two halves check the same population."* Meanwhile
`_xsd.eligible` excluded nested scripts from the effective-tree check *because they were "already
covered by `validate_mod`"*. **They are not covered by anything.**

**Are they live?** MEASURED: all 15 are `<diff>`, none a complete `<mdscript>`, so gotcha #21's
filename-inertness rule cannot apply. The nested path is the engine-proven cross-mod form (#6). The
engine demonstrably loads the targets — `KNOWLEDGEBASE.md` records `Error in MD cue
md.moreroomsforships.Init` and **364 warnings** from `moreroomsforships/md/morerooms.xml`. Our merge
applies the DLC-target ones (`x4effective dump --chain extensions/ego_dlc_split/md/story_split.xml`
→ `sources: base, vro:diff`). **Confidence 90%, not 99%:** there is no direct in-game confirmation
of a nested *md* diff specifically, and that would cost a test cycle.

**The fix, and why it is not the obvious one.** The naive repair — count nested files into the
existing message — would have been **worse than the bug**: that message says *"runs only under
`--update`"*, and for nested files that advice is **false**. Two populations, two statements:

    - script-schema: N md/aiscripts file(s) were NOT validated ... runs only under `--update`
    - script-schema-nested: M nested cross-mod script patch(es) ... are NOT VALIDATED BY ANYTHING
      - not by this run and not by `--update` either, which checks direct children only

One shared `_xsd.strip_nesting` now serves both `eligible` and the scope check, so they agree by
construction. ⚠ **The measurement that found this bug re-implemented that strip rather than calling
it** — the same defect one level up, which is exactly why the helper is shared rather than copied.

### ✅ COVERAGE CLOSED 2026-08-27 — and NOT by widening the selectors

The obvious repair — drop the `count("/") != 1` filters — was **rejected**, and the reason is the
finding. Widening them would validate the patch **FILE**: a `<diff>` the engine never parses on its
own, and whose payload libxml2 treats as lax anyway. The mod session proposed the better shape:
validate the document the **engine actually builds**. That also dissolves the depth problem instead
of fighting it, because after merging there is no nesting left to trip a selector.

**⚠ THE ATTRIBUTION DIFF IS NOT A REFINEMENT OF THIS CHECK. IT IS THE CHECK.** MEASURED over all
**16** nested patches in the installed set (up from 15 — the eighth mod is new today, named rather
than absorbed):

| | count |
|---|---|
| findings if you validate ONLY the merged result | **182** |
| …**inherited** from the TARGET, not the patcher | **167 (91.8%)** |
| **introduced** by the patching mod | **15** |
| **fixed** by the patching mod | **1** |
| not checkable | 3 |

`mdscript name='moreroomsforships'` fails `md.xsd`'s `[A-Z]` pattern because that mod's own author
named it so. The patcher neither caused it nor can fix it. **Reporting 182 findings would flood, and
a check that floods is worse than no check** — it trains you to ignore the output. So
`validate_nested_scripts` validates the target ALONE and the target PLUS the patch, and diffs them
**per finding, keyed by MESSAGE** — a patch shifts line numbers, so a line-keyed diff would report
every inherited finding as both removed and added.

**The `fixed` direction is what makes the green mean something** (gotcha #26). The one real fix in
the corpus — `find_ship` missing the required `space` — shows up as `fixed`, independently
reproducing a result the mod session reached by a different route. E2E on the deployed mod:

    - XSD(nested): 1 cross-mod script patch(es) validated via their merged result
      — 0 introduced, 1 fixed, 0 not checkable

**A THIRD population nobody had named, and it is a live defect.** The 3 "not checkable" split two
ways, and conflating them would bury the interesting one:

- **2 — the target mod is not installed.** The engine no-ops these too; not the patcher's defect.
- **1 — the target IS installed and does not supply the file.** `ship_variation_expansion_vro`
  patches `extensions/ship_variation_expansion/md/spawnclaymore.xml`; that mod is installed and ships
  **7** md files (`activatejobs`, `modifygamestart`, `spawnbattleship`, `spawnheavyparacel`,
  `spawnships`, `spawnterdestroyer`, `spawnxlplunderer`) — **none of them `spawnclaymore.xml`.** A
  stale patch against an older version of its target: a **silent no-op with no engine error**, which
  a merged-result check alone could never find, because there is no merged result. It gets its own
  skip reason, pinned by a test asserting the two do NOT read alike.

**Scope is `_registry.mods("active")`, a literal** (CLAUDE.md #24) — a target the engine will not
load cannot contribute to the document the engine builds.

**Verified:** 5 tests written first and watched fail; **4 mutants, one per behaviour, all killed**,
tree restored byte-exact; suite **904**; `gates/xsd_fast_parity.py` re-proven **559 script files,
0 false positives, 0 misses** (559, not the 555 recorded hours earlier — the corpus grew by 4, and
quoting the stale figure was one command away; see the register's *vacuous-wait* entry).

⚠ **Known interaction, not a new defect.** Under **Tier A** the report now carries both
*"no base game file … this patch can never apply"* (the documented Tier A limitation for any
cross-mod patch) and this check's *"validated via their merged result"*. Both are individually
correct and together they read as a contradiction. `--tier b` clears it (rc 0, verified). Use
`--tier b` for any mod that patches another, which the routing table already says.


**Superseded — this was the state until the fix below landed the same day:** **Deliberately still open:** the depth-1 selectors are unchanged. Widening them changes what
`--update` validates, may surface findings across 7 mods, and needs its own measurement plus a
re-proof of `gates/xsd_fast_parity.py` (which passes today: **555 script files, 0 false positives,
0 misses**).

## F71 — `dump --chain` rendered a WRONG FORM as a confident ABSENCE · **DEFECT** · confidence 97% · ✅ FIXED 2026-08-27

⚠ **THIS ENTRY'S ORIGINAL DIAGNOSIS WAS WRONG, AND THE CORRECTION IS WORTH MORE THAN THE FIX.**
It was filed as *"`dump` cannot resolve a mod-owned vpath"* and concluded that fixing it *"moves the
engine hash and forces a store + BaseX rebuild — better batched with other engine-source work."*
**Both halves are false.** Kept in full below, because a superseded diagnosis is the only thing that
lets a later session date the change.

**What it actually is.** `_effective.build_touch_map` keys the map by the **LOGICAL** vpath — the
document the engine builds. A mod's own file at `<mod>/md/morerooms.xml` is keyed `md/morerooms.xml`;
the `extensions/<owner>/<rel>` rewrite fires only for CROSS-MOD nested patches. I had queried the
**PHYSICAL** path — a disk path under the extensions root. MEASURED:

| query | rc | |
|---|---|---|
| `md/morerooms.xml` | **0** | `sources: moreroomsforships:full, zzz_personal_overlay_F:diff(nested:…)` |
| `extensions/moreroomsforships/md/morerooms.xml` | 1 | "no effective content" |
| same two forms, `Honshu Solar Cell Generator` and `Ship Ai Core` | **0** / 1 | not a one-off |
| `extensions/ego_dlc_split/md/story_split.xml` (CONTROL) | **0** | `sources: base, vro:diff` |

**The asymmetry is the engine's, not ours.** For a DLC that literal string IS the game vpath, which
is exactly why `build_touch_map` declines to rewrite it. For a mod the same shape means nothing to
the engine. So `dump` was right not to find it — and wrong to call it an absence. **1,713 of 3,257
touched vpaths are mod-owned**, so the wrong-form case is a third of the surface.

**The fix, and why it is nowhere near where the entry said it was.** `_effectivecli.logical_vpath()`
+ a retry in `_cmd_dump`, which resolves and **says so out loud** — resolving silently would trade
one confident-wrong answer for another:

    <!-- note: 'moreroomsforships' is an installed MOD, not a DLC, so that is a disk path,
         not a game vpath; interpreted as logical vpath 'md/morerooms.xml' -->

The retry runs **only after the literal lookup has genuinely failed**, so every path that resolves
today resolves by exactly the route it does now — and a **genuine** absence is still rc 1 (verified:
`extensions/moreroomsforships/md/does_not_exist.xml` → rc 1).

**`_effectivecli.py` is NOT an `ENGINE_SOURCE`. No engine-hash move, no store rebuild, no BaseX
rebuild, no coordination with the `x4eff` owner.** The entry's rebuild cost — the entire stated
reason for batching F71 with F70 — never existed. **A cost recorded from a hypothesised cause is a
guess wearing the grammar of a measurement**, and it steered a plan for a day.

**Two things the fix's own verification caught, both worth more than the feature.**

1. **The DLC test banned my first implementation, correctly.** I wrote `folder.startswith("ego_dlc_")`
   — a name-prefix *guess*. `tests/test_dlc_enumeration.py` fails any module that pattern-matches
   `ego_dlc_*` without consulting `Config`, because such a module cannot notice a DLC being added;
   the workspace had written that guess six times. Fixed by asking `Config.dlc_dirs()`. **The guard
   caught it on its first full-suite run.**
2. **A falsification twin was SHADOWED — again.** `logical_vpath` has five clauses, so it got five
   mutants, one per clause. C2 (`parts[0] == "extensions"`) **SURVIVED**: the natural twin
   `md/morerooms.xml` has two parts, so it trips the LENGTH guard and returns None for the wrong
   reason. Isolating C2 needs a contrived input satisfying every other clause
   (`libraries/moreroomsforships/md/morerooms.xml`). After the repair: **5 of 5 clauses proven
   load-bearing**, restore byte-exact. This is the third time in one session that a single twin
   against a multi-clause guard proved nothing — see gotcha #26 and register #99.

**And a third, from the verification instrument itself:** the first `verify-cold.sh` run came back
`COLD RUN CLEAN` over **889** tests while the working tree had **899**. `verify-cold.sh` runs
`git archive HEAD`, so it verifies the COMMITTED state; a working-tree fix reads as absent. Already
recorded in the register, walked into anyway. **Commit first, then verify cold.**

---

*Original entry, superseded 2026-08-27 — the diagnosis below is WRONG and is kept as history:*

### The superseded F71 diagnosis, kept verbatim — ❌ WRONG, and that is why it is still here

The routing table sends *"does this vpath exist in the LIVE tree, and WHO supplies it?"* to
`x4effective dump --chain`. MEASURED 2026-08-27:

| vpath | owner | result |
|---|---|---|
| `extensions/ego_dlc_split/md/story_split.xml` | base/DLC | rc 0, `<!-- sources: base, vro:diff -->` |
| `extensions/moreroomsforships/md/morerooms.xml` | a packed MOD | **rc 1, "no effective content"** |
| `extensions/moreroomsforships/md/MoreRooms.xml` | same, exact case | **rc 1** — so not case-folding |

`x4validate --tier b` resolves that same selector at **rc 0, "no issues found"**, so the information
exists and `dump` simply cannot reach it. The direction is what makes it worth a register entry: a
confident **absent** for a file that is present, from the instrument the routing table names for
exactly that question — and it briefly read as a broken personal overlay before Tier B cleared it.

Found while establishing whether nested md patches are live, not by a sweep. `_effective.py` is an
`ENGINE_SOURCE`, so fixing it moves the engine hash and forces a store + BaseX rebuild; better
batched with other engine-source work than taken alone. **Until then: for a mod-owned vpath use
`--tier b`, not `dump`.**

## F72 — the engine oracle: 32 of 165 fields, and the whole thing was UNREACHABLE · **SCOPE** · confidence 95% · ✅ SUBSTANTIALLY CLOSED 2026-08-30 (32 -> 68 comparable; traversal built; text resolution DEFERRED)

> ## ✅ SUBSTANTIALLY CLOSED 2026-08-30 — and the blocker was a FILE FORMAT, not modelling
>
> This entry sat open from 2026-08-27 while the register ran to F87, on the belief that it
> needed the three traversals built. That was wrong twice over.
>
> **THE ACTUAL BLOCKER, in TWO places.** `cmd_mappings` AND `cmd_oracle` both read a
> **uidata dump**, which needs the engine-probe mod deployed and the game CLOSED. With that
> mod removed, `x4live oracle` exits **2** — *"the probe did not run"*. **The entire oracle
> was unreachable**, so no comparison could be made and widening the map would have changed
> nothing observable. Meanwhile `x4live groundtruth` had been writing the identical
> `(librarytype, macro, field, value)` data over the live pipe since 08-29. Nobody connected
> them. Both now take `--from-groundtruth` (`e36a097`).
>
> | | before | after |
> |---|---|---|
> | directly comparable | 32 | **68** (68 agree, 0 disagree) |
> | engine-DERIVED | 76 | 68 |
> | not mapped | 167 | 170 |
>
> Buckets still sum to 306. Falsified both ways: a planted `hull=99999` and a planted
> `storagecapacity=12345` each give rc 1 naming macro, field, engine and store value.
>
> **THE CONNECTION TRAVERSAL WAS NEVER MODELLING WORK EITHER.** The store already held the
> refs — the flatten keeps the whole subtree, so
> `connections.connection[con_storage01].macro.ref` is a plain attr. Summing `cargo.max`
> over connected macros reproduces the engine **5 of 5 exact** (540, 700, 2300, 8200,
> 38000, S through XL). `storagecapacity` is now COMPUTED and has left `_DERIVED`.
> It returns **None, never 0**, when nothing carries `cargo.max`: a fabricated zero could
> agree with the engine by accident and record a computation never made.
>
> ### ⚠ Three corrections to THIS ENTRY's own claims
>
> 1. **`unitcapacity` was never derived.** It was listed as an aggregate we cannot compute;
>    MEASURED it is stored directly — engine 25 == store `storage.unit` 25 on
>    `ship_arg_l_destroyer_01_a_macro`. **So the `_DERIVED` list is a HYPOTHESIS SET, not a
>    fact set**, and the rest of it should be treated that way.
> 2. **"we already own t-file resolution" is FALSE.** `_refs.text_defs` returns
>    `set[tuple[str, str]]` of `(page_id, t_id)` — ids only, never `t.text`. That is
>    EXISTENCE checking. Rendering `{20101,10102}` to *"Discoverer Vanguard"* would need
>    load-order overrides and nested refs, and is **deferred**, not done.
> 3. **`cmd_mappings` could not tell a measurement from a coincidence.** Its soundness rule
>    ("agrees on EVERY macro carrying both") is **trivially true at n=1**. It proposed
>    `weapons_turrets coolingrate -> rotationspeed.max` off ONE turret where both were 200
>    and the real prop `heat.coolrate` was ABSENT so nothing else could match. Rejected by
>    hand. The tool now prints `[n= nd=]` per proposal with *"nd=1 IS A LEAD, NOT AN
>    ANSWER"*. **All six of its proposals came back nd=1**, so the deciding evidence was
>    the field NAME, which is a weaker warrant and is recorded as such.
>
> ### Still open, deliberately
>
> `docks_*`, `launchtubes_*`, `shipstoragecapacity`, `efficiency*` and the weapon DPS
> family stay in `_DERIVED`. **MEASURED: the scout reports engine
> `shipstoragecapacity=0` while carrying a connected shipstorage macro with
> `dock.capacity=10`** — so it is NOT a sum of connected capacities, and P4's success does
> not transfer. Inventing a traversal to close the row would be picking one of several
> defensible definitions and calling it modelled, which is the failure this entry was
> opened to prevent.


> ⚠ **SCOPE CORRECTION 2026-08-29 — the oracle's agreement is WEAKER THAN IT LOOKS, and
> this bounds the whole entry.** `GetLibraryEntry` is the **ENCYCLOPEDIA / UI-library** API:
> what it exposes is what the **menu** needs. For most stats that is the same resolved value
> the simulation uses — but it is not guaranteed to be, and **nothing in this channel can
> tell the two apart.** So *"32 of 32 agree, 0 differ"* means *our store matches what the
> game SHOWS*, which is one step short of *what the game SIMULATES*.
>
> The direction is safe (a UI-facing field agreeing is still evidence, just weaker), and it
> does not touch the DERIVED bucket at all — those are recorded, not compared. But it means
> the oracle cannot be described as validating the simulation, and the printed summary now
> says so. Raised by a peer session while pre-registering a missile measurement, and it is
> the sharper form of a limit neither of us had stated: **an oracle is only as authoritative
> as the layer it queries.**

> ⚠ **RE-DERIVATION STATUS 2026-08-29: this entry's headline figures are currently
> UNREPRODUCIBLE, and that is a property of the evidence, not a doubt about the finding.**
> Both halves fail for different reasons, and the second is the instructive one:
>
> * **The denominator (165 engine fields over 10 entities) needs the engine dump**, which
>   lived in `{profile}/uidata.xml` and was destroyed when X4 rewrote that file on exit
>   after the probe was removed. Nothing on disk reproduces it.
> * **The numerator cannot be re-derived from the mapping tables either, and the near-miss
>   is worth recording.** `_BY_TYPE` holds **29 distinct field NAMES** across 10 library
>   types. That is NOT the 32 in the headline: 32 counts *(entity, field)* comparisons on
>   the sampled entities, 29 counts distinct names. Reporting 29 as "the re-derivation of
>   32" would have compared two different populations and called the difference a drift —
>   CLAUDE.md gotcha #20's shape, arriving inside an audit meant to prevent exactly that.
>
> **`x4live groundtruth` is the repair**, and it makes the figures reproducible on demand
> rather than once: it harvests the engine's values over the live pipe, needs no probe mod
> and no `uidata.xml`, and writes the result to `dev/_reports/`. Until one harvest has run,
> treat 32/165 as MEASURED-once-and-dated, not as a figure anyone can currently re-check.

**NO RE-DERIVATION, AND THE OWNING SESSION HAS RECORDED WHY (2026-08-29).** Both halves are currently unreproducible: the 165-field denominator needs the engine dump, which X4 rewrote on exit, and the numerator cannot be recovered from the mapping tables either — `_BY_TYPE` holds **29 distinct field NAMES**, while the headline **32** counts *(entity, field)* comparisons over the sampled entities. Reporting 29 as "the re-derivation of 32" would compare two different populations and read the 3 as drift. `x4live groundtruth` is the repair and returns this entry to the auditable set after one launch.
> **UPDATE 2026-08-27 (same day).** Coverage **16 -> 32 directly comparable, still 0
> disagreements**, and the single "derived or unmapped" bucket was split into three:
> **32 comparable · 31 engine-DERIVED · 102 not-mapped-yet**. The derived ones are now
> NAMED and printed with the engine's value under `oracle --show-derived` (`dps 290.909`,
> `sustaineddps 253.711`, `timetooverheat 61.384`, `storagecapacity 540`) — recorded as
> ground truth so they become the **test fixture** for the traversal when it is built.
> Two findings made the widening safe rather than merely bigger: the engine answers
> rotational thrust in **radians** (a declared transform, never inferred — 3 false
> disagreements per thruster avoided), and a field NAME does not determine its meaning,
> so the map is keyed by **library type** (`shield` = a generator's capacity vs a ship's
> loadout total). The remaining gap is unchanged in kind: the three traversals
> (connection / recursive dockarea / loadout join) are still unmodelled.

`x4live oracle` is the toolkit's first check that can show our merged tree is **wrong** rather than
merely self-consistent: it compares values the running engine computed against the effective store.
Its first real run agreed on everything it could compare. This entry records what it could **not**.

**MEASURED 2026-08-27**, 10 entities out of one probe dump, on a freshly rebuilt store:

| bucket | n | meaning |
|---|---|---|
| directly comparable | **16** | 16 agree, **0 differ** |
| derived or unmapped | **149** | counted and named, never dropped |
| **total fields on compared entities** | **165** | buckets sum exactly; no remainder |
| entities not in the store | 4 | a faction and three wares — legitimately not macros |

The 149 are two different problems wearing one label, and conflating them would understate the
interesting half:

- **(a) Localised strings.** `name`, `description`, `shiptypename`, `purposename` and friends. The
  engine returns *"Discoverer Vanguard"*; our store holds `{20101,10102}`. Comparable in principle,
  via the t-file resolution we already own. Cheap.
- **(b) DERIVED AGGREGATES we do not model at all.** `storagecapacity`, `shield`, `docks_s/m/l/xl`,
  `launchtubes_*`, `unitcapacity`, `efficiencyfactor`, `shipstoragecapacity`. The engine computes
  these by **following the macro's `<connection>` refs** — `con_storage01` → `storage_arg_s_scout_01_a_macro`
  → its cargo figure. Our store records the ref and never the sum.

**(b) is the valuable half, and the reason this entry exists rather than a TODO.** A derived
aggregate is precisely where a merge error hides *without changing any single attribute*: every
individual value can be right while the thing the player experiences is wrong, because the error is
in which macro got connected. That is the class of defect an oracle is uniquely able to catch, and
right now it is the class the oracle cannot see.

**Why this shipped OPEN rather than complete.** A narrow check that states its denominator beats no
check. Every run prints the buckets and closes with *"a zero here is 'nothing disagreed among what
was compared', not 'the model is correct'"*, and the bucket sum is asserted against the field total
so an unaccounted field is an error rather than a silent omission. The failure mode this register
exists for — a narrowing step that reports success — is therefore structurally absent: the narrowing
is the output.

**Falsifiability proven, both directions.** A control dump carrying the true `hull=2500` exits 0 with
"0 DISAGREE"; the same dump with `hull=99999` exits **1** and names the macro, the field, the engine
value and the store value. Without that, a green here would be decoration (CLAUDE.md #26).

⚠ **One further limit, stated because it is invisible in the output:** the comparison is numeric at
**float32** precision. The engine returns float32 widened to double (`4.8` → `4.8000001907349`), so
a string compare would report every float as a disagreement. A real disagreement smaller than 1e-6
relative would therefore be invisible — acceptable, since the engine's own precision is the floor,
but it is a floor and not zero.

**Next steps, in value order:** (1) resolve `{page,t}` so the (a)-class fields compare; (2) model the
connection-following aggregates, or at minimum have the oracle follow the refs itself and compute
the sum for comparison; (3) widen the DIRECT map for weapons/engines/thrusters, where 69 of the 149
unmapped fields sit and where no modelling work is needed — only map entries.


### 2026-08-31 — the traversal CONFIRMED against the engine, and one row deliberately NOT extended

`storagecapacity` was computed by following `connections.connection[*].macro.ref` and
summing `cargo.max`. That was self-consistent: our traversal against our own store. The
engine has since been asked directly, via `GetStorageData(object)`, and the two agree
across three orders of magnitude — **prediction written before the measurement**:

| macro | engine `GetStorageData.capacity` | our traversal |
|---|---|---|
| `ship_arg_s_scout_01_a_macro` | 540 | **540** |
| `ship_arg_xl_builder_01_a_macro` | 50 | **50** |
| `ship_arg_l_trans_container_05_a_macro` | 45000 | **45000** |

`unitcapacity`, mapped for **l/xl only**, likewise confirmed: engine
`GetMacroUnitStorageCapacity(macro)` = 100 and 15, store `storage.unit` = 100 and 15.

★ **The scoping is now MEASURED rather than cautious.** Two reasons not to widen it:

1. **Stations diverge by construction.** Shipyard: macro-level `0`, instance-level `392`;
   a second station `0` vs `304`. Stations are module assemblies, so the base macro
   legitimately declares nothing. Had `unitcapacity` been mapped from the ship result
   alone, the oracle would report a **false disagreement on every station in the game**.
2. **The engine flags its own guesses.** `GetStorageData` returns `estimated=false` for
   all three ships and **`estimated=true`** for both stations. So station storage stays in
   `_DERIVED` for a principled reason — the engine itself says the number is a guess — not
   as a concession.

⚠ **`unitcapacity` deliberately NOT extended to s/m.** The scout reports engine `0` while
our store has **no `storage.unit` property at all**. Absent is not zero, and coercing them
is the fabrication `_derive_storagecapacity` already refuses by returning `None` rather
than `0`. Justifying a rule here needs a corpus check that EVERY s/m ship lacking
`storage.unit` reports engine 0; the evidence is **n=1**. Recorded so the next session
knows the gap is a decision, not an oversight.


### ⚠ 2026-08-31 — "not found in the store: 6" was FIXTURE CORRUPTION, not a modelling gap

Quoted repeatedly, including by me today, as though it named six entities our store
cannot produce. It named none. PROVEN by set equality, not by inspection:

    not found in the store : 6
    junk entities          : 6
    missing == junk        : True

The six are DESCRIPTION STRINGS parsed as entity names, because
`groundtruth-20260829-175342.tsv` was written before the escaping fix and a description
containing a tab splits its row into extra columns:

    "For those seeking additional speed, the Nodan Vanguard model is available."  docks_l=0
    "This is a basic laser that is used in many light weapons and smaller..."     dps=290.9

The same file reports `UNPARSEABLE=4` — those are the rows the reader could reject. These
six are the ones it could NOT: they parse cleanly into a well-formed `(librarytype, macro)`
key that happens to be prose.

**What survives:** the 68/68/170 split is computed only over entities found in the store
(`compared = sum(... if k[1] not in missing)`), so the junk contributes no fields and
**68 directly comparable, 0 disagreements is unaffected.** What was wrong is the two
headline counts either side of it: "entities in the dump 21" is really 15, and "not found
in the store 6" is really 0.

**The current writer produces a clean fixture** — `groundtruth-20260831-122748.tsv`:
83 rows, 83 parsed, **UNPARSEABLE=0, junk 0, not found in the store 0**. The escaping fix
is proven end to end.

⚠ **But neither fixture is a good oracle input on its own, and that IS the open item.**
The old one has the ships and 6 junk entities; the new one is clean but equipment-heavy —
15 entities yielding **0 directly comparable**, because the direct map covers ship-shaped
fields. Closing this needs one `groundtruth` capture over SHIP macros with the current
writer. That is a game task, not a modelling one.

★ The shape: a denominator polluted by its own input format, quoted for two days as a
property of the model. Nothing in the pipeline was wrong except the fixture, and the
fixture looked fine because the corrupt rows PARSE.


### ★ 2026-08-31 (later) — the oracle compares 103 fields, not 68, and the gap was a fix I wrote

Correcting the correction above. The "not found in the store: 6" note stands, but the
comparable count in it was measured on the OLD fixture, and the NEW one was being read
wrongly by code I had added the day before.

**The defect.** The `*` row carries the engine's all-fields reply, tab-joined
INTERNALLY. The escaping added on 2026-08-30 — to stop a description's tab breaking the
row structure — turns every one of those separators into a two-character `\t`. The
reader still split on a REAL tab, found none, and parsed only the FIRST `key=value`.

MEASURED on `ship_arg_s_scout_01_a_macro`: **37 fields became 10**, and the 27 lost were
`hull`, `mass`, all six `drag_*` axes and all three `inertia_*` axes — **exactly the
directly-comparable ones**. So the oracle reported **0 comparable** on the clean fixture,
and it read as *"this capture is equipment-heavy"* rather than *"this capture is gutted"*.
**A fix for one silent-loss defect created another, one layer along.**

**After the fix**, the reader picks the separator that is PRESENT rather than assuming a
vintage, so both fixture generations parse and both now yield the scout's full 37 fields:

| fixture | entities | not in store | fields | **comparable** | derived | unmapped |
|---|---|---|---|---|---|---|
| `20260829` (pre-escaping, 6 junk) | 21 | 6 | 306 | 68 (68 agree, 0 differ) | 68 | 170 |
| `20260831` (clean) | 15 | **0** | 428 | **103 (103 agree, 0 differ)** | 68 | 257 |

Buckets sum in both. **So F72's real figure is 103 directly comparable with zero
disagreements, over a fixture with no junk entities** — not the 68 that had been quoted,
which was the older capture's.

⚠ **The oracle never lied.** Its output was correct for the input it was handed; the
input had been silently truncated by the reader. Nothing in the bucket arithmetic could
have caught it, because the fields never arrived to be bucketed — the same shape as a
narrowing step that reports success, which is this register's founding defect.

★ The tell that finally exposed it: **the same macro, in two fixtures, with different
field counts.** Not a failing check — a comparison nobody had run, and the reason to run
it was suspicion about an unrelated number.

## F74 — the live channel's message-size cap · **SCOPE** · confidence 95% · ⚠ HALF CLOSED 2026-08-29: bounded below at 64,000 bytes, true ceiling still unprobed · ★ MECHANISM CORRECTED 2026-08-29 — it does NOT truncate silently, it TEARS THE PIPE DOWN

> ★ **MEASURED IN GAME 2026-08-29 against the live engine: every payload up to 64,000 bytes
> round-tripped INTACT** (256 / 512 / 1024 / 1536 / 2000 / 2048 / 3072 / 4096 / 8192 / 16384 /
> 32768 / 60000 / 64000, all ok, length and checksum verified on each).
>
> **The unsourced 2047-byte figure is REFUTED.** It appears nowhere in the mod's readme or
> changelog and is contradicted by a live measurement three orders of magnitude past it.
>
> ⚠ **What is NOT closed: the true ceiling is ABOVE this ramp and cannot be probed from here.**
> Our own read buffer (`_livepipe._BUF`) is 65,536 bytes and the frame header costs ~24, so any
> size past ~65,472 measures THIS MODULE rather than the game. The tool says exactly that instead
> of reporting 64,000 as the answer — *"every size up to 64000 passed, so the ceiling is ABOVE
> this ramp. NOT a measured cap."*
>
> ★ **The ramp nearly measured ITSELF, and that is the durable lesson.** `RAMP_SIZES` originally
> topped at 65536 — exactly our buffer — so the largest size was a 65,560-byte message against a
> 65,536-byte buffer and ALWAYS failed. Run against a stand-in that truncates nothing, it reported
> *"the ceiling lies in (60000, 65536]"*: our own buffer, presented as the engine's transport cap,
> one live run from entering this register as an engine measurement. Caught by an E2E test written
> minutes earlier; the top is now 64,000 and
> `test_the_ramp_cannot_probe_past_our_own_buffer` pins the invariant.
>
> **Detection was never in doubt and still is not**: every reply carries its own byte length and a
> djb2 checksum computed game-side, so a short read always surfaces as a named `TRUNCATED` refusal
> at rc 3. What was missing was the BOUND, and it is now bounded from below at 64,000.
>
> ⚠ This paragraph said *"`pipes.lua:698`'s silent truncation"* until the mechanism correction
> below. There is no silent truncation: an over-long message tears the pipe down. The length and
> checksum still earn their place — they catch any short read whatever its cause — but they are
> not what saves us from `:698`, because `:698` is not a truncation and is not on this direction.

### ★ MECHANISM CORRECTED 2026-08-29 — "silently truncates" was wrong in BOTH directions

**Everything above about the BOUND stands. What this entry said about the FAILURE MODE did
not, and it was wrong in two independent ways.** Found by extracting the packed
`pipes.lua` from `sn_mod_support_apis/ext_01.dat` (offset 400716, length 33966) and reading
it, rather than re-quoting the one comment at `:698` that every previous version of this
entry quoted.

| what this entry said | what the source says |
|---|---|
| the hazard applies to **replies** | `:698` sits on `_Read_Pipe_Raw` — the game reading **OUR COMMAND**. It describes the opposite direction. |
| an over-long message is **cut short and delivered as if complete** | `winpipe` exposes only `ERROR_IO_PENDING` and `ERROR_NO_DATA` (MEASURED: `strings` on the DLL), so there is no constant for 234 to match. It falls through to `pipes.lua:720` `error(...)`, is caught by `Read_Pipe`'s pcall, reaches `Poll_For_Reads:560` → `Close_Pipe`, and **ERRORs every pending read AND write while destroying the pipe.** The partial data is discarded unread. |

The REPLY direction fails the same way, by the api's own documentation: *"If the write
buffer to the server fills up ... or the new message is larger than the entire buffer, the
pipe will be treated as bad and closed."*

**So an over-long message in either direction is a LOUD, TOTAL teardown — never a quiet
short answer.** Three consequences, and the third is why this matters beyond bookkeeping:

1. **The direction of the risk was better than we thought.** A torn pipe cannot be mistaken
   for data. This entry has always argued "direction is safe"; it is safer still.
2. **The MITIGATION inverts.** Length-and-checksum is *detection after the fact*, and it
   remains correct and worth keeping — but it cannot prevent a teardown. Size must be
   **bounded before sending**. Any verb whose result set is unbounded now caps itself and
   reports `shown=N of M`, using the free denominator the engine's own `GetNum*` provides.
3. **This register's own framing was the broken instrument.** The entry classified the
   hazard as *"a step that narrows the data and reports success"* — the shape this whole
   file exists to catch — and that classification is what made it feel understood. It was
   never that shape. **A hazard filed under the right-looking category stops being
   re-examined**, and this one survived four rewrites of the surrounding entry because the
   category fit. Same lesson as the `diff -rq` case: a rule, or a taxonomy, is an
   instrument too, and it rots.

⚠ The "kept for dating" entry below is left UNEDITED as history and still says "silent
truncation" throughout; it is superseded by this section. The *current* half-closed text
above has been annotated in place rather than left to mislead — it sits above this
correction, so a reader hits it FIRST, which is exactly the case a "see below" note fails
to cover. (Noted because I originally wrote "the paragraph below" here and it did not
describe the paragraph I meant. A pointer that names the wrong direction is a pointer to
nothing.)

### (original entry, kept for dating) F74 — the cap is UNMEASURED, and the transport truncates SILENTLY · **SCOPE** · confidence 95% · ⚠ OPEN by design

`x4live query` / `x4live ramp` ship with a hazard they cannot yet close, and it is
disclosed rather than deferred quietly, because the failure mode is the one this whole
register is about.

**The hazard, verbatim from `sn_mod_support_apis/ui/named_pipes/pipes.lua:698`:**

> If the message is larger than the lua side buffer, returns partial data and error
> `ERROR_MORE_DATA`. **TODO: look into this.**

That TODO is unhandled by the API. So an over-long reply is **cut short and delivered as
if complete** — and a truncated TSV row is still a well-formed TSV row. Same shape as
every other entry in this file: a step that narrows the data and reports success.

**What the ceiling actually is: three sources, three answers, none authoritative.**

| source | figure | status |
|---|---|---|
| `X4_Python_Pipe_Server/Classes/Pipe.py:60` | 64 KB each way | READ from the source |
| an earlier session's note | 2047 bytes, from the winpipe DLL | **no traceable source**; not in the KB |
| the mod's `readme.txt` + `change_log.txt` | no limit stated | MEASURED by grep, both files |
| `tests/test_livepipe_e2e.py` | **8192 bytes round-trips intact** | MEASURED, but only across the win32 layer between two PYTHON processes |

They are not in conflict, because they describe **different layers**. The E2E result
bounds the pywin32/kernel half and says nothing about the lua half or the DLL, which is
exactly where the 2047 figure would live if it is real.

**What is shipped instead of a guess.** Every reply carries its own **byte length and
checksum**, computed game-side, so decode clause 7 turns a silent truncation into a named
`TRUNCATED` refusal at rc 3. The hazard is therefore *detected* in every case; what is
missing is the *bound*, which is what would let a caller size a bulk reply confidently.
`x4live ramp` measures it, spanning both candidate figures, and — the part that matters —
**refuses to report its own top size as the answer** when every size passes:

> every size up to 65536 passed, so the ceiling is ABOVE this ramp. NOT a measured cap.

**Direction is safe.** A too-small assumed cap would only chunk more than necessary; a
too-large one is caught by clause 7 rather than silently believed. The gap is that we
cannot yet *plan* a bulk reply, not that we might mis-report one.

⚠ **OPEN by design, and closing it needs the GAME.** The ramp cannot run against the
Python stand-in and mean anything: the stand-in shares this code's understanding of the
protocol, so it would confirm my own model rather than the engine's behaviour — the
"corroborating instrument was never exposed to the mechanism" error already recorded as
register #87. It closes on the first in-game run, in one command.


### 2026-08-31 — the instrument removed from the measurement (the ceiling is still UNMEASURED)

⚠ **The `What is NOT closed` paragraph above is SUPERSEDED as of this date.** It says the
ceiling "cannot be probed from here" because `_livepipe._BUF` is 65,536 bytes. That is no
longer true, and the paragraph is kept because its REASONING is still the point.

**What changed, and what did not.** `_BUF` is now **1 MiB** and `RAMP_SIZES` extends to
**524,288** (toolkit `5d9919d`). **This measures nothing about the engine.** It removes US
from the measurement, which was the blocker — a ramp that stops at its own buffer reports
that buffer as an engine finding, and this one already did once.

**Established by reading the code rather than repeating this module's own docstring** —
the earlier justification for raising the buffer was a claim about us, sourced from us:

- `_read_raw` performs a **single** `ReadFile(self._h, _BUF)` with **no loop on
  `ERROR_MORE_DATA`**, so a reply larger than `_BUF` cannot arrive whole.
- It is **detected, not silent**: `decode_reply` checks the declared LENGTH *before* the
  checksum, deliberately, so a truncated reply is diagnosed as **truncated** rather than as
  generic corruption.
- So our side capped the REPLY path. The SEND path is still bounded by the game's own lua
  buffer (`pipes.lua:698`) — which is what F74 is actually about.

⚠ **STILL OPEN, and it still needs the GAME.** The true ceiling is unmeasured. One
behavioural change matters for whoever runs it: **each failure now costs a whole
connection**, because an over-long message TEARS THE PIPE DOWN rather than truncating. A
ramp that fails at 96,000 does not simply report and continue.

⚠ The invariant that the ramp top stay below our buffer is unchanged and still enforced by
`test_the_ramp_cannot_probe_past_our_own_buffer`. Verified NOT vacuous after the raise: a
mutant putting the ramp top above `_BUF` turns it red. A guard whose margin grows without
anyone re-checking it is how a check quietly stops being able to fail.


### 2026-08-31 (later) — MEASURED IN GAME: the bound moves 64,000 to 524,288 bytes

The widened ramp ran against a live game. **Every size passed, up to and including
524,288 bytes** (524,312 on the wire), each with its length and checksum verified:

    256 · 512 · 1024 · 1536 · 2000 · 2048 · 3072 · 4096 · 8192 · 16384 · 32768
    60000 · 64000 · 96000 · 131072 · 262144 · 524288      all ok

**The lower bound is now 8x what it was, and the untraceable 2047-byte figure is
refuted at 256x its claim.**

⚠ **STILL NOT A MEASURED CAP, and the tool says so itself** rather than reporting the
top of its own range as a finding: *"every size up to 524288 passed, so the ceiling is
ABOVE this ramp. NOT a measured cap."* That sentence is the whole point of the entry.

**What now bounds the ramp is again OURS, not the engine's** — `_livepipe._BUF` is
1 MiB, so anything past ~1,048,512 would measure this module. The instrument reports
that limit in the same breath as the result, which is the behaviour that stopped the
first ramp from entering this register as an engine measurement.

**Practically it is settled even though the ceiling is not.** The mod caps its own
replies at `MAX_PAYLOAD` = 32,000 bytes, so the demonstrated headroom is **16x** what
any verb can emit. Chasing the true ceiling would need `_BUF` raised again and buys
nothing a caller can use; the open half of F74 is now a curiosity rather than a
constraint, and that is worth saying plainly so nobody spends a session on it.

⚠ One behavioural note for whoever does: **a failure costs a whole connection**, since
an over-long message tears the pipe down rather than truncating. It is recoverable —
the re-arm was proven live the same day and reconnects within ~2s — but a ramp that
overshoots is not free.

## F81 — a save-load safety hook that has never fired, hidden by a probe field that only proved the symbol EXISTS · **DEFECT** + **DISCLOSURE** · confidence 97% · ✅ DISCLOSURE FIXED 2026-08-30

**The guard.** `x4_toolkit_live_query` inspects only ids it issued itself, and empties that
allowlist on a save load — because `IsValidComponent` catches a DESTROYED object but not a
**REUSED handle**, and a reused handle returns another object's data under the id you asked
about: a wrong answer wearing the grammar of a right one.

**The defect.** The clearing is written as
`RegisterEvent("Lua_Loader.Send_Priority_Ready", …)` inside `Init`. MEASURED across a game
start and a save load:

```
"id allowlist cleared"     -> 0 occurrences
"RegisterEvent absent"     -> 0     (so the symbol IS present)
"could not hook game load" -> 0     (so registration SUCCEEDED)
```

It registers cleanly and never runs, and it **cannot** run: it subscribes from inside
`Init`, and `Init` is itself driven by that same signal, so it is always registered *after*
the event it waits for. Because the chunk is rebuilt on every load, the next load replaces
the listener before it could ever fire. **Structurally dead, not accidentally dead** — no
change to registration timing could have fixed it.

**Why the safety held anyway.** VERIFIED end to end with a control: an id issued and
confirmed working was refused after a save load, with a fresh load marker in between. A save
load is itself a full UI reload, so the chunk is re-created and `issued_ids` starts empty.
**The protection is real; the mechanism credited with it is not.**

### What hid it — three shapes this register already knows

1. **PRESENCE READ AS EXECUTION.** `probe` reported `RegisterEvent=function`, and the entry
   in `PROBE_SYMBOLS` said in as many words that it was *"needed by Init to reset the id
   allowlist on a save load. If it is ever absent, ids leak between saves and the probe says
   so."* Absence would have been caught; **deadness would not**. A symbol existing says
   nothing about a callback ever running, and the reassuring field is the one nobody
   re-examines.
2. **A CORRECT TEST LICENSING A FALSE BELIEF.** The unit test fires the signal by hand and
   asserts the allowlist empties. It was green throughout and it is not wrong — it asserts
   *"the callback does the right thing IF called"*. It was read as *"ids are cleared on save
   load"*. They are, by something else. **A test can be correct and still underwrite a wrong
   conclusion about production**, which is why the fix pins the real mechanism separately.
3. **A COMMENT ASSERTING THE OPPOSITE OF THE MEASUREMENT.** The code stated *"Loading a
   different save does NOT re-run this Init … so the lua state (and our allowlist) persists
   across the load."* Both halves are false: a save load DOES re-run `Init`, and the state
   does not persist. Written as INFERENCE from `lua_loader`'s `has_sent_ready` guard,
   recorded in the grammar of a fact, and never checked against a log.

### ★ The dangerous part: the obvious fix would have removed the real protection

The user-facing symptom is that ids stop resolving after pressing **Alt-Enter** (MEASURED:
a graphics mode change forces a UI reload; alt-tab does not). The natural repair — *persist
the allowlist across reloads via a global* — was scoped, agreed, and **abandoned one step
before implementation** when the dead hook surfaced: persisting would have deleted the only
clearing that actually works, letting ids survive a save load into the reused-handle hazard.

It was then measured to be **impossible as well as unsafe**: a counter kept on `_G` read
**1, then 1** across two real reloads, because **no lua state survives a UI reload** — the
engine rebuilds the whole environment. No global could have carried the allowlist either.

**A dead guard is dangerous twice: once because it does nothing, and again because removing
whatever quietly compensates for it looks like a safe simplification.**

### The fix (disclosure, not mechanism)

- The hook is **kept** as defence in depth — deleting apparently-working safety code
  deserves its own decision, and it costs nothing if the engine ever stops rebuilding the
  chunk on load.
- It is **instrumented**: `load_hook_fired` is reported by `probe`, and its log line now
  states out loud that it had never once appeared, so a future reader is told rather than
  left to assume.
- The `PROBE_SYMBOLS` note that invited the misreading is corrected in place.
- The false comment is replaced with the measurement, including what it got wrong and why.
- `component`'s refusal now distinguishes *"nothing has been issued by this chunk — that is
  what a UI reload looks like"* from *"this id is probably mistyped"*, so the refusal stops
  blaming the caller for something the engine did.
- Pinned by tests asserting **both** states of `load_hook_fired`, because a field hard-coded
  to `false` would be indistinguishable in game and worthless the day the hook came alive.
  Mutation-tested: 8 planted mutants, 8 killed, 0 survived, 0 skipped.

⚠ **Not closed as a mechanism.** The hook is still dead. What is closed is that its deadness
is now visible, tested, and documented rather than contradicted by the code around it.

**RE-DERIVED BY:** `tests/test_modlua_rearm.py` -- the save-load guard's own behavioural tests, which assert the hook FIRES rather than merely that its symbol is present (the distinction the entry is about).

## F85 — the engine SILENTLY IGNORES an unrecognised container and returns the whole galaxy labelled as your sector · **DEFECT** (silent wrong answer) · confidence 99% · ✅ FIXED 2026-08-30

**The shape.** `GetContainedObjectsByOwner(owner, container)` does not validate its
container. Hand it something it cannot resolve and it does not raise, does not warn, and
does not return empty — it behaves as though no container was passed at all and returns
**every object the owner holds anywhere in the galaxy**. Our header then labelled that
reply with the sector the caller asked for.

**MEASURED in game 2026-08-30**, faction `argon`, same session, seconds apart:

| sector argument | objects returned | our header said |
|---|---|---|
| `ID: 514068` (valid) | **310** | `sector=ID:514068` |
| `ID:` (truncated) | **1961** | `sector=ID:` |
| `NOT_A_SECTOR` | **1961** | `sector=NOT_A_SECTOR` |
| `-` (deliberate galaxy) | 1961 | `sector=galaxy` |

**6.3x the data, no error, no flag.** This is the register's signature defect — a step
that narrows and does not announce it — arriving INVERTED: a step that **failed to
narrow** and reported as though it had.

**Why it would certainly have bitten.** Sector tokens are **not stable across
launches**: MEASURED over four consecutive launches, Argon Prime was `ID: 4305`,
`ID: 5936`, `ID: 514068`, `ID: 4303`. They are stable only WITHIN a session. So a token
copied from an earlier session — the ordinary way anyone would use this — silently
returns the galaxy instead of a sector, and every downstream count is ~6x too large
while looking perfectly well-formed.

**How it was found, and this part matters.** By a **broken instrument**. A shell loop
left `"ID: 514068"` unquoted, which split it into `ID:` and `514068`; the reply came
back with `sector=ID:` and an object count that made no sense. The register's own base
rate says the checker is usually what is wrong — it was — but chasing the anomaly rather
than shrugging at it is what turned a quoting mistake into a real finding.

**Fix.** The token shape is validated before use (`^ID:%s*%d+$`), in `run_enumeration`
and in `compare`, with a refusal that names the measurement and says where to get a
fresh token. `-`/`galaxy` remain a DELIBERATE galaxy-wide request and are a different
thing from a token that did not work. Pinned by two tests (four malformed shapes, both
galaxy forms, one valid token) and two mutants.

⚠ **Not fixed, because it cannot be from our side:** we validate the SHAPE, not the
identity. A well-formed token for a sector that no longer exists is still, as far as we
know, silently widened. The guard turns the common case (a stale or mistyped token) into
a refusal; it does not make the primitive safe.

---

**RE-DERIVED BY:** `tests/test_modlua_rearm.py` -- `bad_sector_token` is asserted to REFUSE a malformed container, with a falsification twin proving that removing the guard turns the test red.

## F86 — `GetComponentData` rejects a raw `UniverseID` cdata by returning nothing, and does NOT raise · **DEFECT** · confidence 99% · ✅ FIXED 2026-08-30

**The shape.** `GetComponentData(id, ...)` requires an id that has been converted —
vanilla always does `ConvertStringTo64Bit(tostring(...))` or passes a LuaID. Given a raw
`UniverseID` cdata straight out of an ffi buffer it returns values that are **not about
any object**, without raising and without any signal.

**MEASURED in game 2026-08-30:** the galaxy-wide enumeration path passed `buf[i]`
directly and reported **`enumerated=1539  unclassified=1539  unreadable=0`** — every
object unreadable, and zero read failures, because nothing failed. The reply was
well-formed, correctly counted, and described nothing.

**Why the offline suite could not catch it.** The test fake accepted any id shape, so
the conversion looked optional. It is now modelled: a ULL-shaped id yields nils, so a
caller that forgets the conversion FAILS a test instead of being discovered in game.

**Fix.** `readable_id()` converts only the canonical ULL rendering and passes LuaIDs
(which the container primitive returns as `userdata`) through untouched. Pinned by an
assertion that the wide path `matched` its rows — the previous test asserted only
`enumerated`, which counts handles returned and is unaffected by whether any could be
read — plus a mutant that removes the conversion.

★ **The durable lesson is the counter that found it.** `unclassified=` had been added
days earlier for an unrelated crash (a nil `classid` raising inside
`Helper.isComponentClass`). It is what turned this from "the sector filter returns
nothing, why" into a one-query diagnosis. **A narrowing counter pays for itself in the
bug it was not written for.**

---

**RE-DERIVED BY:** `tests/test_modlua_rearm.py` -- the id path is asserted to hand the engine a CONVERTED id, never a raw cdata, and the allowlist tests pin that a foreign id is refused rather than passed through.

## F88 — a probe list keyed on ARGUMENT POSITION is keyed on nothing · **DEFECT** · confidence 97% · ✅ FIXED 2026-08-31

`recon` groups the engine globals it calls into lists by argument shape: `RECON_ZERO`
(no args), `RECON_OBJ` (one object id), `RECON_FAC` (one faction id). The bucket decides
what the probe passes.

**`GetMacroUnitStorageCapacity` was in `RECON_OBJ`. It takes a MACRO.**

MEASURED, in our own recorded output (`dev/_reports/recon-20260830-211605.tsv`), against
two different issued ids:

    GetMacroUnitStorageCapacity(33879739ULL)   OK   number:0
    GetMacroUnitStorageCapacity(148052ULL)     OK   number:0

**`status OK`. No error. A plausible number.** And `0` is exactly what a ship with no
unit storage would legitimately report, so nothing about the row looks wrong.

Vanilla passes a macro, two ways, and never an object id:

    ui/addons/ego_detailmonitor/menu_map.lua:10461
        capacity = GetMacroUnitStorageCapacity(menu.macro)
    ui/addons/ego_detailmonitor/menu_map.lua:10468
        capacity = GetMacroUnitStorageCapacity(GetComponentData(ConvertStringTo64Bit(
                   tostring(menu.object)), "macro"))

### Why it matters more than one wrong cell

This is **F86's shape**, the second instance in four days: *an engine getter handed the
wrong id type returns nothing useful and does NOT raise.* F86 was `GetComponentData`
given a raw `UniverseID` cdata — 1539 of 1539 unclassified, 0 read failures. Here it is a
`0` instead of an empty set. **Both are silent, both wear the grammar of an answer, and
`pcall` cannot help because nothing is thrown.**

It also lands on the exact field family F72 conceded. `unitcapacity`, `docks_*`,
`launchtubes_*` and `shipstoragecapacity` sit in `_DERIVED` — *"our store cannot produce
these"* — and this is the engine function that names one of them. A `0` recorded against
it is worse than no reading at all: it is a **datum that agrees with a wrong model**.

### The general rule, which is the durable half

**A probe list keyed on argument POSITION is not keyed on anything. What decides the
bucket is what VANILLA PASSES.** Position is a property of the call we are about to write;
the type is a property of the function. Grouping by the former and hoping it matches the
latter is how a wrong-type call gets written and then recorded as a result.

⚠ **The whole `RECON_OBJ` list rests on this same assumption and has not been re-derived
per name.** 17 names remain in it. This entry fixes the one that was demonstrably wrong;
it does NOT establish that the other 17 are right. That re-derivation is OPEN.

### The fix

A fourth bucket, `RECON_MACRO`, reached through the vanilla hop
(`GetComponentData(id, "macro")`) copied from `menu_map.lua:10468` rather than composed.
**When that hop does not yield a string the probe is SKIPPED and the skip is PRINTED** —
it does not fall back to passing the object id, which is the defect itself. The header
gained `permacro=` and `macros_resolved=` so an unrun probe is visible rather than merely
absent.

### Falsifiability — what makes each test able to go red

Six mutants, each targeting ONE clause, all killed, tree restored byte-identical:

| mutant | test that went red |
|---|---|
| name back in `RECON_OBJ` | the structural test |
| **the real defect re-introduced** — pass `id` where `macro` goes | the BEHAVIOURAL test |
| macro hop falls back to the id | the SKIP twin |
| `--contents` becomes the default | the opt-in test |
| renderer descends one more level | the nesting test |
| keys rendered in reverse | the sorted test |
| per-value budget removed | the bounded test |

⚠ The structural test alone is **not** sufficient and would have been the easy thing to
write: it passes the moment the name moves between two lists, whether or not the argument
changed. The behavioural test is the one that pins the defect, and the mutant that proves
it is the one that re-introduces F88 exactly.

### Found by

Not by a sweep. By reading **all 803 function names** in the engine's `_G` census
(`harvest-20260830-191234.tsv`, 858 globals, parse reproduces the header's own count)
while checking an unrelated claim about ownerless objects. The row had been sitting in a
committed report for a day, `status OK`, looking like data.

**RE-DERIVED BY:** `tests/test_modlua_rearm.py` -- `test_recon_calls_the_macro_probe_with_a_MACRO_STRING_not_the_object_id` asserts the ARGUMENT, not list membership, and its mutant re-introduces F88 exactly. The structural twin alone passes the moment the name moves between two lists, which is why both exist.

### ✅ THE OPEN HALF, CLOSED 2026-08-31 — and re-deriving it found FIVE more

The entry above said the other 17 `RECON_OBJ` names rested on the same unverified
assumption. They did, and so did the 71 in `RECON_ZERO`. Re-derived all 91 against what
vanilla ACTUALLY passes — arity by paren-matching, attestation by call-site count:

| name | bucket said | vanilla | the engine's reply |
|---|---|---|---|
| `GetUIElementRectangleScreenPosition` | 0 args | **3 args** | **RAISED** — loud |
| `GetTargetMonitorDetailsBridge` | 0 args | **3 args** | `OK \| nil` — **silent** |
| `GetLiveData` | 0 args | **never called** | `OK \| nil` — **silent** |
| `GetTargetMonitorDetails` | 0 args | **never called** | `OK \|` — **silent** |
| `GetTransportUnitMacros` | object id | **a MACRO** (`menu_map.lua:30321`) | `OK \|` — **silent** |

★ **FOUR OF FIVE ANSWERED SILENTLY.** So the live run's own header — `ok=90 raised=1` —
was never evidence that 90 calls were sound. A wrong-argument call returns a benign nil
that is indistinguishable from a real absence, which is why this had to be settled from
the corpus and not from a play session.

Two of them (`GetLiveData`, `GetTargetMonitorDetails`) also violated **the mod's own
written admission rule** — *"ATTESTED IN VANILLA... names not called anywhere in vanilla
are therefore NOT probed."* A rule that is only prose is not enforced.

**Fix:** the four unsound names dropped from `RECON_ZERO` (71 → 67);
`GetTransportUnitMacros` moved to `RECON_MACRO`, where the vanilla hop already supplies a
macro.

**Mechanised, not just repaired:** `tests/test_recon_buckets_are_vanilla_attested.py`
re-derives every bucket from the vanilla corpus on every run, so this cannot regress.

⚠ **The gate's own blind spot, found by a surviving mutant and then closed.** The arity
check CANNOT catch F88: a macro and an object id are both ONE argument, so putting
`GetMacroUnitStorageCapacity` back into `RECON_OBJ` left it green. That mutant is what
prompted the type check — narrow and textual, matching only the two forms vanilla writes
(`menu.macro`, `GetComponentData(..., [macro])`), because a heuristic like *"anything
containing mac"* would be F92's substitution in the guard itself. Chasing it is what
surfaced the fifth instance. **5 mutants, 5 red, 0 survivors**, including F88 itself and
the reverse cheat of emptying `RECON_OBJ` into `RECON_MACRO`.

⚠ **Scope:** the gate proves arity and attestation for all 87 remaining names, and
macro-vs-object for the one type we can identify from vanilla's own text. It does NOT
make the buckets type-safe in general.

★ **THE MEASUREMENT ITSELF WAS WRONG TWICE FIRST, and the engine is what exposed it.**
Version one reported **ZERO** defects: it counted a **comment**
(`-- ... GetUIElementRectangleScreenPosition() function which...`) as a 0-arity call
site — for the exact function the engine had already caught raising — and an **ffi.cdef
signature** (`uint32_t GetAllCommanders(CommanderInfo* result, ...)`) as a 4-arity call.
Then the fix for that bit again one level in: a quoted word inside the explanatory
**comment I added to the mod** was read as a list entry, inventing a probe named `macro`.
Counting a declaration or a comment as evidence of how something is CALLED is F92, inside
the instrument built to find F92.

## F73 — the pre-push identifier guard could not see the file a port EDITS BY HAND · **DEFECT** · confidence 98% · ✅ FIXED 2026-08-28

> **2026-09-26 (AUDIT-2026-09-24 DC-3) — the guard this entry describes is RETIRED; the text
> below is history.** `verify-port.py` was retired with the dev repository on 2026-09-13.
> With one repository no file is "edited by hand during a port": every published file lives
> in the tree that `scripts/scan-identifiers.py` scans, tracked AND untracked (its
> `population()`; the untracked half is pinned by its own `--selftest`), run locally by
> `scripts/test-hooks.sh` and in CI. ⚠ `gates/register_rederivation.py` credits this entry
> to `gates/schema_sweep.py`, which the table below quotes as a place an identifier once sat
> — a mention, not a check of this fix.

**Found by shipping it.** During the v2.9.0 release `scripts/verify-port.py` reported the port
*faithful* with a clean identifier scan; the push went out; CI's personal-data job then failed on
`CHANGELOG.md:56`, a personal overlay name inside the F71 example output. **A personal identifier
reached the public repo with the local guard reporting green.**

**The mechanism is structural, not an oversight in the token list.** `verify-port.py` scans the
**port set** — dev-tracked files being copied to the mirror. The mirror's root `CHANGELOG.md` is
mirror-only and sits on `ALLOW` *because the mirror keeps its own*, so it is not dev-tracked and can
never enter that population. **A port copies most of its files and edits exactly one by hand; that
one was the blind spot.** The fix adds every mirror-tracked file outside the ported subdir.

**⚠ THE SECOND DEFECT IS THE INSTRUCTIVE ONE: my first fix was INERT AND REPORTED SUCCESS.**
`mir_tracked` is built by `git ls-files SUBDIR` — already scoped to the ported directory — so the new
`hand_edited_mirror_files()` received a pre-filtered list, returned `[]`, and printed
*"0 hand-edited mirror file(s)"*. The **selftest passed**, because the pure function was correct; the
wiring was not. That is gotcha #26 exactly: a green with no reachable failing branch. **What caught
it was not a test but an implausible number** — zero hand-edited files in a repo that has a root
`README.md`, `CHANGELOG.md`, `install.ps1` and `.github/`. After the wiring fix: **53** in scope.

**Proven by falsification, not by inspection.** Re-planting the exact leaked string reproduces
`HIT (mirror) CHANGELOG.md:56` at rc 1, with the file restored byte-exact afterwards. A guard that
has never gone red is decoration.

**Generalisation worth more than the fix:** *ask what a process does to each class of file it
touches.* This one **copies** (scanned), **allow-lists** (deliberately absent), and **hand-edits**
(unscanned, and nobody had named the class). The population that a guard covers is easy to state and
easy to get wrong, and an ALLOW list is where a file goes to stop being looked at.


### The exposure was 13x larger than first reported — and the DECISION is to leave it

⚠ **If you find this token in public git history, it is KNOWN AND ACCEPTED. Do not re-raise it, and
do not "fix" it with a rewrite.** Measured 2026-08-28 across the whole public repo:

| | |
|---|---|
| commits whose TREE carries the token | **13 of 86** |
| distinct paths that ever carried it | **6** (`_check.py`, `_compat.py`, `_xsd.py`, `gates/schema_sweep.py`, one test, `CHANGELOG.md`) |
| earliest | **2026-08-02**, v2.02 |
| **released ZIP assets carrying it** | **0** — v2.8.0 and v2.9.0 both verified clean |
| current tree | clean (`scan-identifiers.py` rc 0) |

**So the v2.9.0 commit was a RECURRENCE, not the introduction.** I first reported it as a single
line because that is what the scanner named — **the same "a single reported hit is a LOWER BOUND,
not a census" mistake I had flagged to a peer an hour earlier, made about my own finding.** A
scanner stops describing once it has a finding; the count you get is where it stopped looking, not
how much is there.

**The user's decision (2026-08-28): LEAVE IT.** The reasoning, recorded so it does not have to be
re-derived:

- It is a **folder-name fragment, not a credential**, and it has been public for roughly four weeks.
- **No released asset is affected**, so users who download the toolkit never receive it.
- A rewrite means re-pointing **five-plus tags** and breaking the clone of a real **external
  contributor** (`blablup`, two merged PRs).
- A force-push **does not truly purge**: GitHub keeps unreferenced objects reachable by SHA, and
  forks and caches keep their own copies, absent a separate GitHub support request.

**The durable fix is the pair of guards, not the history.** This entry's fix covers hand-edited
mirror files before a push; the parallel session's scrub now fails **locally** rather than
post-push. Both had to exist because the CI job, by construction, can only fire after the thing it
guards has already been published.

## F75 — `qa_sweep` exercised 28 of 41 CLI capabilities · **SCOPE** · confidence 95% · ✅ CLOSED 2026-08-29 (opened 2026-08-28)

**How it was found.** Not by looking for it. The user asked how we could know whether the
assistant actually *reaches for* the toolkit rather than merely knowing it exists.
`gates/toolkit_usage.py` was built to answer that from the session transcripts, and enumerating
the surface properly — by asking each program, not by parsing its source — produced a roster that
could then be compared against `qa_sweep.CELLS`. The coverage gap fell out of a measurement aimed
at something else.

**MEASURED.** 41 capabilities across 11 CLIs. `qa_sweep` exercises **28**. The 13 it does not (⚠ the table below is the ORIGINAL, WRONG list — see the correction under it):

| CLI | not exercised |
|---|---|
| `x4modlist` | `ingest` `dashboard` `needs-review` `refresh` `resolve` `source` `tracked` `ignore` `mark` `verify` `snapshot` `changed` (12 of 12 — the sweep's 5 cells all target the same few) |
| `x4debug` | `baseline` |
| `x4effective` | `build` · `coverage` |
| `x4live` | `mappings` |
| `x4xref` | `build` |

⚠ **THE SCOPE OF THE CLAIM, STATED PRECISELY, BECAUSE THE OBVIOUS OVERSTATEMENT IS WRONG.**
This is *"absent from the CLI sweep's roster"*, **not** *"untested"*. A grep of `tests/` for each
subcommand name matches **6 of the 17** (both figures are the ORIGINAL, WRONG ones — see CORRECTED below) — but that grep also matches unrelated uses of ordinary
words (`source`, `ignore`, `verify`), so it over-counts, while a subcommand tested through a
differently-named helper would be under-counted. **It is a lead in both directions and neither
figure is established.** What IS established is narrower and still worth having: a CLI-contract
regression in those 13 (originally reported as 17) would not be caught by the gate built to catch CLI-contract regressions.

**Two defects in the measuring gate itself, both fixed before it shipped, both worth recording
because they are the shape this register exists for.**

1. Its transcript-directory fallback picked *"the directory with the most bytes"*, which on this
   machine is **an unrelated game's project**. It scanned 39 of those transcripts and reported a
   well-formed, denominator-carrying **"0 invoked, 41 never invoked"** — which reads as *"we never
   use our own toolkit"*. Caught only because the same measurement had been done by hand an hour
   earlier (38 used, 3 never), so the result was impossible on sight. Now the directory is DERIVED
   from the configured game root and the gate REFUSES if that does not resolve, plus an independent
   second guard: zero invocations across the whole surface is treated as the wrong transcripts, not
   as disuse. Two guards, because the first rests on a naming convention this repo does not own.
2. Coverage was keyed on `(tool, argv[0])` for every cell — but `Cell.argv[0]` is a SUBCOMMAND only
   when the CLI has subparsers; for a single-command CLI it is a mod-path ARGUMENT. That made
   `x4validate`, `x4diff` and `x4similar` unmatchable, and the first run reported all three as
   untested when `qa_sweep` exercises them 9, 3 and 2 times. 20 findings, 3 of them false.

**Why the gate does not fail on the 13.** A gate that goes red on a known backlog every run is the
same flood as an uncalibrated threshold — it trains you to skip the runner, which is worse than not
having one. The 13 are recorded in the local gitignored baseline and **printed as `known` on every
run**, so they are accepted rather than silenced; a NEW gap is the finding. Falsified: dropping one
entry from the baseline yields `NEW x4debug baseline` and rc 1.

**Closing it** means adding cells to `qa_sweep` — real work on a shared gate, deliberately not done
in the same block as the measurement that found it.

**★ CORRECTED 2026-08-29 — the figure above was 17 and is 13, and the cause is THIS ENTRY'S OWN
DEFECT (2), a third time.** `Cell.argv[0]` is the subcommand only when nothing precedes it.
**7 of 54 cells lead with a GLOBAL FLAG** — `["--registry", <sandbox path>, "dashboard"]`,
`["--file", <path>, "dump"]` — so the subcommand sits at `argv[2]`. `x4modlist dashboard`,
`needs-review`, `verify` and `source` ARE exercised, against a throwaway registry copy, and were
reported as gaps. The true figures are **28 of 41 exercised, 13 not**, and the `x4modlist` row above
is 8 of 12, not 12 of 12.

**The number is the smaller half. argv has THREE shapes and I enumerated TWO, twice.** Defect (2)
was found, fixed, given falsification twins, and mutation-tested per clause — and the same bug
recurred immediately in the shape nobody had thought of. **Watching a test fail proves the feature is
absent; mutating the code proves the test is present; neither proves the ENUMERATION is complete.**
That is the gap, and it is not closable by more twins of the same kind.

**So the fix is a guard on the CLASS, not a third patch.** `_subcommand_in()` searches argv for a
subcommand against the LIVE surface and skips any element consumed by a flag, and
`Coverage.undecidable` records a cell where none can be found. A FOURTH argv shape now surfaces as a
finding — *"qa_sweep cell has an unrecognised argv shape"* — instead of silently shrinking the
covered set. The heuristic errs toward UNDECIDABLE (a valueless flag before the subcommand makes it
skip one too many); that is the safe direction, and it is pinned by a test so the limit stays
visible. 4 mutants, one per clause, all killed.

⚠ **This matters beyond the entry.** `docs/TRUST.md` claims publicly that *"every defect SHAPE that
has occurred here has a test or gate that mechanically bans its recurrence."* For one day that was
false: the shape had a test and recurred past it. The class guard is what makes the claim true again
— recorded here in that order, because a claim repaired after its counter-example is worth less than
one that never needed repair, and pretending otherwise is the failure this register exists to stop.

**Also corrected: "a grep of `tests/` matches 6 of the 17".** That grep was never evidence in either
direction (it matches ordinary words like `source` and `ignore`), and its denominator has now moved
as well. The claim stands only in its narrow form: these are absent from the CLI sweep's roster, and
whether each is covered elsewhere is **not established**.


### ✅ CLOSED 2026-08-29 — 41 of 41, and the closing surfaced a tool defect

All 13 are now cells. 11 joined the quick tier; the two real builds (`x4effective build`,
`x4xref build`) went into a new **`--all` slow tier**, each redirected at a throwaway output so
the shared store, `x4eff` and `md_xref.tsv` are untouched. Verified by the real registry's mtime
being unchanged after a full sweep, not by reading the code that was supposed to redirect it.

**The default run NAMES the slow cells it skipped.** A sweep that silently runs a subset and
prints a clean total is this register's founding defect, and a gate is not exempt from it because
its exclusion happened to be deliberate.

**Closing it surfaced F77, and that is the part worth keeping.** The `snapshot` cell wrote a real
snapshot into `dev/_registry/snapshots/` on its first run, despite `--registry` pointing at the
throwaway copy — `snapshots_dir()` ignored the override every other command honours. So the gap
could not be closed *honestly* until the tool obeyed its own documented flag. **A backlog item is
not always just unwritten work; sometimes the reason it was never closed is a defect standing
behind it.**

Baseline re-recorded: `missing` went 13 → 0, so the gate now fails on any NEW gap rather than
carrying an accepted list.

## F76 — every identifier guard scanned the INDEX, so the newest file was outside the population · **DEFECT** · confidence 97% · ✅ FIXED 2026-08-29

> **2026-09-26 (AUDIT-2026-09-24 DC-3) — half of this fix was RETIRED; the text below is
> history.** The `verify-port.py` half went with the dev repository on 2026-09-13. The
> `scripts/scan-identifiers.py` half is live: READ 2026-09-26, `population()` still returns
> tracked + untracked files, and `scripts/scan-identifiers.py --selftest` asserts that an
> untracked file is scanned ("an UNTRACKED file is scanned too -- the whole point").

`git ls-files` reports what git has been **told about**. A file you created ten seconds ago and have
not staged is not in it, by construction — and that file is, by construction, the one most likely to
carry something you have not yet thought about.

**MEASURED 2026-08-29.** The mirror's `scripts/scan-identifiers.py` printed *"scanning 200 tracked
file(s) ... clean"* over a port whose new file was untracked. Staging the file took the population to
201, and the scan then meant something. `scripts/verify-port.py` had the identical shape on the dev
side: its `port_set` came from `git ls-files`, so a new dev file was neither compared nor scanned.

**This is F73's sibling, and the pair is worth more than either.** F73 was a file a port **edits by
hand** — outside the population because `ALLOW` had excused it. F76 is a file nobody has **decided
anything about yet** — outside the population because the index had not caught up. Two different
upstream narrowings, one indistinguishable symptom: *a guard printing a confident denominator for the
population it happened to see.*

**The asymmetry in the fix is the design, not an implementation detail.** `ALLOW` still excuses a
TRACKED file from the identifier scan — those name real installed mods on purpose and would fire
every run. It deliberately does **not** excuse an UNTRACKED one, because there is no decision to
honour: nobody has said anything about that file yet. **An exclusion must be one somebody MADE, never
one the tooling made on their behalf.**

**Proven by falsification on both sides, because a guard that has never gone red is decoration.**
Planting an untracked file carrying a banned token: dev population 163+0 → 163+1, findings 21 → 22,
rc 1. Mirror 202+0 → 202+1, rc 1. Both clean at rc 0 after removal, with 0 untracked files left
behind.

**⚠ CI COULD NEVER HAVE CAUGHT THIS, AND CANNOT PROVE THE FIX.** `actions/checkout` produces a tree
with no untracked files, so the second list is always empty there. This was only ever a LOCAL
pre-push defect — which is exactly where it fired. That is why CI gained a `--selftest` step *before*
the scan: the selftest is the only thing in CI that can go red over this, and F61's lesson is that a
guard which can only fire after publication is not a guard.

**Two instrument failures while fixing it**, both this register's own classes:

- An escaped newline inside a heredoc collapsed to a REAL newline crossing the tool boundary,
  producing an unterminated string literal. Third instance of the escaping class in one session. The
  repair uses `chr(10)` and no literal escape at all.
- **The first falsification run produced a FALSE PASS.** The plant targeted a `docs/` directory that
  does not exist in the mirror root, and `$TMPDIR` was unset, so the redirect failed — and `rc=1` was
  duly reported against *"expect 1"*. The check passed for a reason unrelated to the guard. The rerun
  asserts the plant exists before running anything. **A falsification test needs its own
  falsification: if the red branch is reachable without the defect, red proves nothing either.**

**Latent trap removed in passing.** Every `git ls-files` reader used `.split()`, which splits on
spaces as well as newlines, so a path containing one would arrive as two nonexistent files — the
whitespace-splitter shape. MEASURED: **0 such paths in either repo today**, which is precisely the
reading that lets a latent trap sit for another year (gotcha #23). Now `_files()`, line-based, twinned.

## F77 — `--registry` was honoured by every read and ignored by the one path that WRITES · **DEFECT** · confidence 97% · ✅ FIXED 2026-08-29

`x4modlist --registry <somewhere>` redirects everything the tool reads. It did not redirect what
`snapshot` **writes**: `_changed.snapshots_dir()` resolved `_registry.DEFAULT_REGISTRY`
unconditionally, while every neighbouring command in `_modlist.py` threads `args.registry`
explicitly.

**Found by adding a cell to a gate.** `gates/qa_sweep.py` points every `x4modlist` cell at a
throwaway copy of the registry for one reason: *a gate must not mutate the state it inspects.* The
new `snapshot` cell wrote a real snapshot into `dev/_registry/snapshots/` on the first run. Had it
been added without checking, every sweep would have littered the user's triage store — and the
sweep would have reported GREEN while doing it.

**⚠ The direction is what makes this class dangerous.** It does not fail, it does not warn, and it
does not write nothing. It writes to the **wrong place** and reports success. A documented override
that one path ignores is worse than no override at all, because it is precisely the fact that
everything else honours it that makes you stop checking.

**The read side had the same gap and was fixed with it.** `load_baseline("latest")` looked for the
newest snapshot in `snapshots_dir()` — also the default. Fixing only the write would have left
`--registry X changed --since latest` writing to X and reading from the default: a half-taught
lookup chain, which is a subtler bug than the one it replaces, because now the two halves disagree
silently instead of both being wrong in the same visible direction.

**Verification.** Five tests: the directory form, the registry-FILE form (`--registry` is documented
as taking either, and honouring one but not the other is the same defect one level down), the
`latest` read, an empty-directory non-answer, and a **falsification twin** — a no-argument call must
NOT return the test directory. Without that twin the other assertions would pass for a function
that ignored its argument and happened to return the right shape.

Three pre-existing monkeypatch stubs (`lambda: tmp_path`, `lambda spec: ...`) failed on the new
signature. That is the suite proving the change actually reached the code under test, and it is
worth more than the tests I wrote for it.

## F78 — three PUBLISHED surfaces sat outside every port check's population · **DEFECT** · confidence 97% · ✅ FIXED 2026-08-29

**RE-DERIVED BY: `gates/published_surface_drift.py` and `tests/test_published_surface_drift.py`.**

> **2026-09-13: both checks above were RETIRED with the dev repository** (their source is in
> its archive bundle). **RE-DERIVED BY, since then:** `gates/deploy_parity.py` and
> `tests/test_deploy_parity.py`, for the `.claude/` surfaces -- lifted from
> `published_surface_drift.py`, deliberately without its accept-baseline. That gate does NOT
> cover `tools/basex`; that surface is closed by removing its second copy instead, which is
> part of the same retirement.
>
> **2026-09-26 (AUDIT-2026-09-24 DC-3):** the note above was re-checked — `gates/deploy_parity.py`
> and `tests/test_deploy_parity.py` exist. The first `RE-DERIVED BY` line and every
> `verify-port.py` mention below are history.

`scripts/verify-port.py` proves the dev package matches the public mirror. Its population is
`git ls-files` of one repository, scoped to `tools/x4validate`. Three things we ship are not in it:

| surface | why it was invisible |
|---|---|
| `.claude/hooks/` | lives in the GAME-ROOT `.claude` directory, which is not a git repository at all |
| `.claude/skills/` | same |
| `tools/basex/` | its own separate git repository |

**MEASURED 2026-08-29.** The shipped `protect-bash.sh` was **3,445 bytes** while the copy in daily
use was **17,722**, and `test-protect-bash.sh` — the regression suite that makes those rules
trustworthy — had **never shipped at all**. Six skills were behind the ones in use. `tools/basex`
had **6 of 19 tracked files differing**, with one public file (`smoke-basex.sh`) absent locally.

**This is F73 and F76's shape, arriving twice more.** Each time the guard was correct about the
population it had, and the population had been narrowed somewhere upstream: by an `ALLOW` decision
(F73), by the index not having caught up (F76), and here by a repository boundary. The lesson is
not "check harder" — it is that *a guard's population is a claim, and it should be stated.*

### Two properties, both got wrong first

**(a) LINE ENDINGS ARE NOT DRIFT.** The first measurement compared raw bytes and reported every
skill and every basex file as differing, with the telltale +N/−N of equal counts. Normalised,
**9 of 37** really differed. That is `diff -rq`'s exact failure — 52 differences where 12 were
real — reproduced by an instrument written by someone who had already recorded the trap and cited
it in the same session. **Knowing a trap does not protect you from it; only a check does.**

**(b) NEVER A BARE "differs".** The gate reports lines present on each side *separately* and
refuses to name a winner. This is not politeness. Six shipped skills were measurably "behind" and
still carried work the newer local copies lacked: a `--python 3.13` interpreter pin, Linux profile
locations, and `$X4_MODS`-based registry resolution where the local copies hardcoded a workspace
path. Acting on the word "differs" — copying the newer over the older — would have deleted all of
it. Reporting the direction is what makes the output safe to act on.

### What it found, and what remains

Drift closed **9 → 2**. Five skills took the published copies (gaining the pin and the portable
paths), one new skill (`x4-probe`) went the other way after being checked for named mods and
personal paths, and `smoke-basex.sh` came back to the local tree.

The remaining two are NAMED in the baseline rather than counted: a peer session's uncommitted
`staleness.py` work, which is not mine to commit, and a local `protect-bash.sh.bak`, which is a
backup and correctly not published. **An accepted backlog printed as a number is one nobody
notices growing**, so the gate prints the list.

## F79 — every hook we ship was INERT in production · **DEFECT** · confidence 99% · ✅ FIXED 2026-08-29

**RE-DERIVED BY: `scripts/test-hooks.sh` (the stdin-contract probes) and `.claude/hooks/test-protect-bash.sh`.**

All five hooks read their payload with `INPUT=$(cat /dev/stdin)`. In the Claude Code hook
environment that returns **zero bytes**. A bare `cat` returns the payload.

| probe | `/dev/stdin` | bare `cat` |
|---|---|---|
| 7 consecutive PreToolUse and PostToolUse calls | **0 bytes, every time** | 641 – 2,840 bytes |

**The failure is invisible by construction.** A hook that reads nothing falls through its first
guard clause and exits 0 — which is byte-identical to deciding *"this is fine"*. There is no error,
no log line, and no difference in behaviour from a hook that examined the command and approved it.

So for weeks, in public releases as well as locally:

- `protect-bash.sh` — `rm -rf` the game install, `git add -A` in a shared tree, unscoped searches
- `protect-files.sh` — the hard block on `reference/`, `.cat`/`.dat`, and the game directory
- `backup-before-edit.sh` — the entire audit trail
- `search-scope.sh` — shipped that same morning
- `x4validate-on-edit.sh` — advisory validation

…all did **nothing**.

### ⚠ The suites were green the whole time

A suite pipes stdin explicitly (`printf … | bash hook`), and `/dev/stdin` resolves perfectly that
way — verified directly. So every probe passed while the production behaviour was nil. **This
register's founding shape, sitting underneath every instrument being used to hunt it.**

**Independent confirmation, which is what makes this measured rather than argued.** No
`AUDIT_LOG.txt` existed in the game root or the workspace at all. The only one anywhere held 17
entries — *all* of them the test suite's synthetic `/tmp/tmp.XXXX/…/wares.xml` fixture. **Not one
real edit in five weeks**, while `CLAUDE.md` stated that every file edit is auto-backed-up.

### The fix, and the deeper defect underneath it

One shared `x4_hook_input()` in `_x4-env.sh` (bare `cat`), used by all five. Zero executable uses of
`/dev/stdin` remain.

But the read method is only the proximate cause. **The real defect is that a hook could not
distinguish "I received no input" from "the input said allow."** `x4_require_input` closes that: a
guard that cannot see its input now says so instead of falling silent. *Silence is consent, and that
is exactly how this hid.*

The suite gained **five probes that CLOSE stdin** rather than piping it — reproducing the production
condition a pipe never can — plus a static rule banning `cat /dev/stdin` anywhere, which covers
hooks added later. Proven by falsification: reverting one hook turns both the runtime and static
probes red at rc 1, and restores byte-identical.

**E2E-verified in a way permission mode cannot mask.** A real `Write` call produced an
`AUDIT_LOG.txt` where none had ever existed, plus a backup file containing the *original* content —
a side effect, not a permission decision, so bypass mode cannot flatter it.

### What turning them on immediately revealed

Bringing five never-exercised guards to life at once turned bypass-permissions mode into manual
mode: **17 rules could prompt the user**, most of them about nothing more than my own command
hygiene. Two consequences, both now fixed:

- **A false positive within two commands.** The `rm` rule matched *"an `rm` appears anywhere"* AND
  *"the game path appears anywhere"* — so deleting a `/tmp` file was blocked because the command
  text happened to mention the game directory elsewhere. **No rule in these hooks had ever run in
  production, so none of their false-positive rates were known.**
- **The verdict policy was wrong.** `ask` spends the USER's attention; `deny` and advisory context
  spend mine. Anything that is merely my hygiene must never reach them. Reclassified: 12 rules to
  **deny me** (each has a correct alternative I can simply take), 2 to **advise me**, 1 dropped as
  too broad (it fired on an `echo` that merely mentioned a `.cat`). **17 user-facing rules → 5**,
  and all five are deliberate: `content.xml`, profile files, and deleting inside an X4 directory.

All four verdict paths are now proven live: PreToolUse **deny** (it blocked a real command),
PreToolUse **advise** and PostToolUse **advise** (both reached the model — so
[issue #18427](https://github.com/anthropics/claude-code/issues/18427), closed as *not planned*
saying `additionalContext` has no effect, does not describe this version), and a **side effect**
(the backup). `settings.json` hook changes also take effect mid-session, with no restart.

## F80 — the SUBJECT is shared, the REGISTRY of the subject is forked · **DEFECT (structural)** · confidence 95% · ⚠ OPEN 2026-08-29

**NO RE-DERIVATION: this describes a repository arrangement, not a computed value.** The incident
below is reproducible by committing any new test file to `tools/basex`, but there is no number to
re-derive.

`tools/basex` is **its own git repository**, shared by every session on the machine. The guard that
lists its test files — `TEST_FILES` in `tools/x4validate*/tests/test_basex_tests_are_not_orphaned.py`
— lives in the **branch-per-session** x4validate worktrees.

So the two halves move at different speeds. A basex commit is visible to every session the instant
it lands; the registry naming it is not. **Every basex commit therefore reds the other session's
suite until they notice and hand-apply a line on their own branch.**

**MEASURED 2026-08-29.** Committing `tools/basex/test_x4v_tree.py` turned a concurrent session's
suite red with `BaseX test file(s) present but NOT in TEST_FILES`. They could not quote a clean suite
number until they applied the change themselves — and correctly declined to have me reach into their
tree, since adding a file to someone else's registry is a judgement about intent.

**⚠ The first reading of it was a WRONG-REPO observation, and that is the part worth remembering.**
The peer reported the file as *untracked*. From inside `tools/x4validate`, git cannot see a different
repository, so **everything** under `tools/basex` reads as untracked no matter what basex's own index
says. The command succeeded, the output was well-formed, and it described something else — the
adjacent-question shape, in `git` this time. They then attributed the file to a session on that
basis, which turned a tooling error into a statement about someone's work.

### Why it is OPEN rather than fixed

Each obvious repair is worse than the problem:

- **Weakening the orphan guard** reintroduces exactly what it exists to prevent — 23 basex tests that
  passed when run by hand and were collected by nothing, reported by no one.
- **Moving `TEST_FILES` into basex** puts a guard about *x4validate's* collection inside a repository
  that knows nothing about x4validate.
- **Merging the repositories** is a much larger decision than this cost justifies today.

So it is recorded rather than patched. The value of the entry is that the next session recognises the
shape in seconds — *shared subject, forked registry* — instead of spending the diagnosis on an
"untracked file" that was never untracked. If it recurs often enough to be measured in hours rather
than minutes, that is the trigger to reconsider the repository split itself.

## F82 — the hook rule set denies 8.89% of real historical work · **DEFECT (measured)** · confidence 97% · ⚠ OPEN 2026-08-30

> ⚠ **SUPERSEDED 2026-08-30 (later the same day).** Every number below this banner was measured while the live hook was
> REDEPLOYED mid-run (launch 22:28, deploy 00:08:29, finish 00:15:36) and with only `command` in the payload; see **F83**
> for the five instrument defects. Kept verbatim as history. **The current figures are in the summary-table row above:**
> stable hook, all three fields, 11,340 of 11,340, validated by a second full pass with 0 changed verdicts —
> allow 8,391 · advise 1,406 · ask 200 · deny 1,343, of which ~1,700 non-allow verdicts are noise in six rules of one shape.
> The headline "denies 8.89% of real work" is WITHDRAWN as a false-positive claim: on the stable numbers, 449 of the 1,343
> denies are the timeout cap doing its job and the verified-false denies are ~200.

**RE-DERIVED BY: `gates/hook_false_positives.py`**, baselined in
`.hook-false-positive-baseline.json`.

F79 made the hooks run for the first time. Their combined precision had still never been observed:
every rule was written, reviewed, unit-tested against piped fixtures and shipped **without a single
real command ever passing through it**.

Replaying **10,852 distinct historical commands** — all of which ran fine at the time — through the
live hook:

| verdict | count | share |
|---|---|---|
| allow | 8,348 | 76.9% |
| advise | 1,534 | 14.1% |
| ask | 5 | 0.05% |
| **deny** | **965** | **8.89%** |

**One command in eleven blocked. One in seven carrying a spurious advisory.**

### Three false positives, verified individually rather than inferred

| rule | hits | what it actually matches |
|---|---|---|
| redirect into a game dir | **1,450** | `2>/dev/null`. Suppressing stderr reads as "writing into the game directory". The identical command without the redirect is **allowed** |
| delete the game install | 7 | a `.zip` merely **named** `X4 Foundations …`, in an unrelated folder |
| unscoped recursive search | 18 | `cd <root> && grep -rn … tools/x4validate` — scoped to a subdirectory — because the `cd` put the root into the command. The same grep with an absolute path is allowed |

These are instances **seven, eight and nine** of the shape recorded the previous day: *a predicate
matching a TOKEN rather than the THING it is about.* Two more look identical and are **unverified**:
the `/tmp` rule fired 226 times and the reference-tree rule 77 times, on commands that appear only to
*mention* those paths.

⚠ **The game-delete false positive blocked the command written to demonstrate it** — the payload
contained the offending filename. That is the fourth time a guard blocked the work of fixing guards,
and the cleanest possible proof of the defect.

### What is NOT noise

The remainder look like policy working as designed and should stay: **190** real `git add -A`,
**95** real `$?`-after-a-pipeline, **90** durable-record writes with no backup. Those are denies to
read and comply with, not to remove.

So the actionable target is the ~1,500 noise verdicts concentrated in three or four rules, and
**fixing the redirect rule alone removes 1,450 of the 1,534 advisories.**

### ★ Why this entry exists rather than a fix

**This is the reason the F79 fix was not pushed.** Shipping working hooks in this state would give
every installer a guard that blocks one command in eleven — *actively worse than the inert hooks it
replaces*, because an inert guard is merely useless while a wrongly-firing one obstructs. The user
declined the push before this was measured; the measurement is what turned that instinct into a
number.

**And the measurement itself failed three times before producing one**, each caught only by the
control it refuses to run without: an environment where every path rule was unable to fire (a vacuous
300 of 300 allow); `subprocess.run(["bash", ...])` resolving to **WSL's** bash, whose empty stdout the
harness reads as "allow" — which would have reported all 10,852 commands clean; and a stale `rc` file
from that failure misread as the new run's verdict. All three produce the same symptom: a perfect
sweep.

## F83 — the instrument that measured F82 had five defects of the narrowing-step shape · **DEFECT (measured)** · confidence 98% · ✅ FIXED 2026-08-30

**RE-DERIVED BY:** `tests/test_hook_false_positives_gate.py` (31 tests) and the parser-contract probes in `scripts/test-hooks.sh`.

F82's numbers were produced by a gate that could not be trusted, and every defect in it was the shape this register exists for: a step that narrows the data and reports success anyway.

| # | defect | how it was found |
|---|---|---|
| 1 | the live hook was redeployed MID-RUN (launch 22:28 from the transcript timestamp, deploy 00:08:29 from file mtimes, finish 00:15:36 from the log mtime). Bash re-reads the script per spawn, so the tail of the run used a different rule set from the head | `ask` read 5 in the baseline and 16 in the first 500 of a re-run with byte-identical hooks; the first explanation ("rules added after the baseline") was asserted before the timestamps were read, and was wrong |
| 2 | `by_rule` stored 25 EXAMPLES per rule and no counts — eight rules read exactly 25 | eight identical numbers in one table |
| 3 | `OUT.write_text` ran unconditionally, so the baseline could never be diffed against | reading the code while planning the compare |
| 4 | the payload carried only `command`; the hook reads `run_in_background` (LONG JOB rule) and `timeout` (cap rule). Every backgrounded long job replayed as foreground; the cap rule could never fire | writing the Block 1 classifier and asking what the rule actually reads. E2E after the fix: same command deny foreground / allow background; timeout 900000 deny / 60000 allow |
| 5 | `decide()` discarded rc and stderr; an empty stdout read as allow | code-review probe: `JQ=no_such_binary` → rc 0, empty stdout, stderr noise, because the hook exits 0 on an empty `$COMMAND` |

**And the hook had the same defect one layer in.** `< <(jq ...)` loses jq's exit status, so a failed parse produced an empty `$COMMAND` and `exit 0`. The first fix reported that failure through `x4_require_input`, which emits its verdict WITH jq — a broken jq made the refusal itself silent, and the probe still read allow. Both paths now fall back to a static JSON literal. Probes: 1 parser-contract + 4 empty-input-with-broken-jq, all watched fail first (62 pass / 1 fail; 4 of 4 allow), 67/67 after.

**What the rewrite guarantees now:** hooks + `x4-paths.env` hashed before and after and the resolved paths re-resolved (void on any drift); uncapped per-rule counts that must sum to the verdict totals; `--record` writes, default compares per rule over the INTERSECTION of commands with (old → new) moved pairs so a swap cannot hide behind equal counts; `--record --limit` refused without `--out`, partial or old-format baselines refused; rc≠0 or an empty verdict beside stderr is a refusal; exit 2 = refused, 3 = unexpected, and 1 means DRIFT and nothing else. Serial ≡ parallel proven verdict-for-verdict (`--prove-parallel`, 0 mismatches on 60, 40, 20).

Process lesson recorded in the verifier register (2026-08-30): nine checker bugs in the red-team pass that found these, every one caught by a control or a re-read and none by looking at the output. And F81 was claimed without running `scripts/next-blind-spot-id.py` — a peer had it seven minutes earlier; mine moved to F82.

## F84 — an ADVISORY exits the hook, so every rule below it is unreachable · **DEFECT (measured)** · confidence 99% · ⚠ OPEN 2026-08-30

**RE-DERIVED BY:** `scripts/test-hooks.sh` § *an advisory never masks a later verdict* (5 probes).

`advise()` ended in `exit 0`, exactly like `deny()` and `ask()`. But an advisory is **an allow that
carries a note**, not a decision — and it sat at rule 8 of 19. A command that earned one never
reached the eleven rules below it.

**MEASURED** by commenting out the six advisory/ask rule bodies (comments cannot break bash; line
surgery on their multi-line guards did, twice) and replaying the **1,846** commands those rules
catch. Controls held: the known-bad command still refused, stage-everything still refused.

| where the 1,846 land with the six rules off | count |
|---|---|
| `allow` | 1,548 |
| **refused by a rule further down** | **298** |

| the suppressed refusal | count | its F82 classification |
|---|---|---|
| TIMEOUT above the cap | 130 | 449 of 449 genuine |
| output into shared `/tmp` | 64 | 226 of 227 genuine |
| durable record, truncating open | 35 | genuine (policy) |
| exit status read after a pipeline | 27 | genuine (policy) |
| profile manifest searched by NAME | 26 | genuine — CLAUDE.md #30, the query that nearly destroyed a correct memory |
| stage-everything in a shared tree | 11 | 186 of 190 genuine |
| durable record, truncating redirect · in-place edit | 5 | genuine (policy) |

**So the noisy rules were not merely noisy — they were suppressing correct refusals.** The single
largest was the guard against a timeout the harness silently clamps, which exists because that
mistake cost four ten-minute losses in one session (CLAUDE.md #25).

### Two consequences beyond the fix

1. **F82's per-rule table is CONDITIONAL, not independent.** Every count in it is *"what this rule
   caught GIVEN every rule above it already ran"*. It was written as though the rules were
   independent, and any arithmetic that adds or subtracts those counts across rules is wrong.
2. **The predicted effect of scoping the six rules INVERTS on one axis.** Refusals do not fall from
   1,343 to ~1,135; they RISE to ~1,416, and correctly so, because ~298 of them stop being
   suppressed. What falls is what was actually complained about: advisories 1,406 → ~50 and
   confirmations 200 → ~7.

### The fix
`advise` accumulates into a variable and is emitted once at the end; `deny` and `ask` stay terminal,
because those ARE decisions. Found by asking, before editing any predicate, *"what do the commands
this rule currently catches hit if it stops catching them?"* — a question the per-rule table could
not answer and that no test would have raised.
### ✅ CONFIRMED by targeted re-measure, 2026-08-30 — prediction written first, landed within one

The full compare was killed mid-run (crawling at 5x normal because a full test suite and the port
check were running against it — my own harness contending with my own measurement). It was not
needed: **no predicate changed, so an `allow` cannot become non-allow and the two terminal verdicts
cannot move. Only a command whose FIRST verdict was an advisory can change.** Replaying all 1,406
baseline advisories plus a 400-command allow control is provably sufficient, and takes 24 minutes
rather than two hours.

| | predicted, recorded BEFORE the run | measured |
|---|---|---|
| advise | ~1,113 | **1,114** |
| deny | +293 | **+292** |
| allow | must not move at all | **400 of 400 unmoved** |
| ask | unchanged | unchanged — zero advisories became confirmations |

**Corpus totals: allow 8,391 · advise 1,114 · ask 200 · deny 1,635** (sums to 11,340). Refusals are
now 14.4% of the corpus, up from 11.8% — every added one previously suppressed and measured genuine.

The refusing rules, and why they differ from the all-six-off probe above: timeout 122 · shared-tmp 56
· durable truncating-open 35 · exit-status-after-pipeline 24 · **LONG JOB 17 · workspace-root 12 ·
reference-tree 8** (these three were DISABLED in that probe, so its commands fell elsewhere) ·
stage-everything 11 · durable redirect 4 · **profile-manifest-by-name 2** (26 in the probe, because
the Documents confirmation was disabled there; it is terminal and still catches those first) ·
in-place-edit 1. Every rule accounted for, no unexplained remainder.

⚠ One thing NOT to read from the killed run: its progress lines showed deny +48 / advise -44 at the
2,000 mark, which is the right shape — but the corpus grew 11,340 -> 11,529 while two peer sessions
were writing transcripts, so the first 2,000 commands are not the same 2,000 in both runs. Progress
counters are not comparable across runs; the intersection-by-item-hash is.
## F87 — the right question, asked of the wrong ARTIFACT · **DEFECT (measured)** · confidence 99% · ✅ FIXED 2026-08-30

**RE-DERIVED BY:** the port gate that compares the mod's committed `<savedvariable>` name against
the CLI's committed `DEFAULT_VAR` in one command, rather than checking each side separately.

> **2026-09-13: `scripts/verify-port.py` was RETIRED with the dev repository** -- with one
> repository there is no port left for it to check. The one-command committed-blob comparison
> this entry calls for is `gates/lockstep.py`, pinned by `tests/test_lockstep_gate.py`.
>
> **2026-09-26 (AUDIT-2026-09-24 DC-3):** the note above was re-checked — both files exist.
> Every `verify-port.py` mention below is history.

The `x4live` channel decodes a TSV payload out of a lua global. The mod's `<savedvariable>` name and
the CLI's `DEFAULT_VAR` must be identical or the CLI reads **zero of everything** — a NON-ANSWER, not
an error.

A peer reported the de-branding rename done and the lockstep consistent. I checked and replied that
I had *"verified every claim"*. We had both read the file **on disk**.

| artifact | reads |
|---|---|
| working tree (what we both checked) | the new name |
| **committed blob (what a port ships)** | **the old name** |

`git status` showed both mod files modified and unstaged. Dating made it diagnosable rather than
mysterious: the mod files were written at 00:31:39 and the toolkit half committed at 00:32:51 — one
operation, 72 seconds, two repos, only one of them committed. It sat split for two days.

`scripts/verify-port.py` states the rule in its own header — *comparing COMMITTED BLOBS, not
working-tree files, because the two trees have different line endings on disk and identical bytes in
git* — and I did the opposite while using the word "verified".

### Why this is its own shape
Every other entry in this register is a wrong query, a narrowed population, or an instrument
answering an adjacent question. Here the question was right, the population was right, and the
instrument answered correctly. **The artifact was wrong.** Both artifacts exist, both are readable,
and only one of them ships — so nothing local objects, and two independent sessions can each confirm
the same false thing by reading the one view in which it looks fine.

The peer's root cause for their twin failure the same hour was the same family, in different clothes:
*"I read the PRODUCER (the tool's code) and never the CONSUMER (the reports it had already
written)."* Producer-vs-consumer · disk-vs-blob · working-tree-vs-HEAD.

### The control
**Assert the two sides agree IN THE SAME VIEW** — a single comparison of the pair that ships, not two
separate checks of two artifacts. Applied: the port now gates on
`git show HEAD:<mod>/ui.xml` and `git show <branch>:_livedump.py` compared in one command.
Verified holding after the peer's fix (dev `8c6006a`): mod blob and CLI blob both `__x4live_dump`,
zero occurrences of the old name in either.

### ⚠ THREE INSTANCES IN ONE DAY, BETWEEN TWO SESSIONS — the recurrence IS the finding

Filed as one incident, it looks like a slip. It is not: the same substitution happened three times
on 2026-08-30, twice by me and once by a peer, and **none of us saw it coming from inside**.

| # | the artifact consulted | the artifact that decided | outcome |
|---|---|---|---|
| 1 | the mod file **on disk** | its **committed blob** | a lockstep "verified" by two sessions while committed state disagreed, for two days |
| 2 | the tool's **code** (producer) | the **reports it had already written** (consumer) | a peer withdrew a whole planned work block that was already done and sitting in `_reports/` |
| 3 | `cmd_source`'s **implementation** | the **cell's argv** that actually invokes it | I told a peer their gate would mutate my registry. All 13 cells pass `--registry <sandbox copy>`. My "MUST FIX" would have swapped a real check for an excuse — **in the message that cited this very entry** |

★ The tell is identical every time: **the convenient artifact and the deciding artifact both exist,
both are readable, and both answer.** Nothing errors, nothing is empty, no denominator looks wrong.
The only defence is naming, before looking, WHICH artifact decides — and then reading that one.

A related and equally cheap failure the same day, from the peer's `qa_sweep` work: a guard that was
real and correct but at the wrong GRANULARITY. `test_qa_coverage.py` asserted every CLI has at least
one sweep cell, and its docstring described exactly the stale-hand-list failure mode — while 29 of 43
SUBCOMMANDS had no cell. It passed the whole time because the CLI in question had ten other cells.
**Right question, wrong granularity** is the same family as right question, wrong artifact.

**A FOURTH, within the exchange about the first three.** Verifying a peer's cell counts, I compared
the PUBLIC MIRROR copy of the shared gate against numbers they had taken from their own WORKTREE,
got a different answer, and briefly concluded they were wrong. Three copies of that file exist — the
peer's worktree, mine, and the mirror — and I reached for the convenient one. Their numbers were
right for their tree; mine were right for a tree that was not under discussion.

★ **Four instances, one day, two sessions, and the fourth occurred while writing up the first
three.** Knowing the shape confers no protection whatsoever. The only thing that works is
mechanical: **name the deciding artifact BEFORE looking, out loud, and read that one** — "the
committed blob", "the cell's argv", "the peer's worktree at <path>". Every instance was preceded by
a moment where naming it would have taken five seconds.

⚠ A related correction the peer made to their own reasoning, worth keeping because it is the same
error one level up: they had excluded nine subcommands from a coverage sweep as "expensive or
mutating", when the deciding question is **"can a cell REDIRECT it?"** — every one dissolved under a
sandbox path, an `--out` flag or a no-network argument. *An exclusion a sandbox dissolves is an
excuse.* Reasoning from a thing's NATURE rather than from what the harness can DO about it.


## F89 — a guard rewrite made every Bash call 11.3x slower, and nothing measured hook latency · **DEFECT (measured)** · confidence 93% · ✅ FIXED 2026-08-31

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` (97 tests), the mutation harness (12 of 12
planted mutations caught by their target test), and the coverage harness (19 of 19 predicates probed
in both directions).

Eight guard rules were re-scoped on 2026-08-30 to fix a measured false-positive rate. Each rule got
its own quote-aware shell parsing in bash. Correctness improved; **nobody timed the result.**

MEASURED 2026-08-31 on a clean machine, warmup discarded, 5 runs, min/max recorded:

| payload | floor (jq only) | before the re-scope | after |
|---|---|---|---|
| `echo hi` | 239 ms | 978 ms | **3,379 ms** |
| 201-char command, no heredoc | 93 ms | 1,205 ms | **13,585 ms** (max 18,713) |

Claude Code's docs state a PreToolUse hook runs to completion before the tool call proceeds, so this
is pure latency on **100% of Bash calls**. Attributed by profiling each helper on a 19-segment
command: `resolve_var` cost **236 ms for a SINGLE token** and was called per-token inside per-segment
loops; `writes_under` (3,328 ms) and `searches_rooted_at` (2,961 ms) were re-invoked 5 and 4 times,
each re-tokenising from scratch. Cost was multiplicative, not additive.

**Fix:** `.claude/hooks/hook_facts.py` does ONE parse pass; `protect-bash.sh` keeps policy and prose.
Same conditions, short payload: 2,930 / 8,602 / **705 ms** (before-rescope / after-rescope / parse
pass). 19 verdict calls before, 19 after; no rule lost, no verdict kind changed.

### Why it was invisible
Every existing check asked *is the verdict right?* Not one asked *what did asking cost?* The suite
measures correctness, the corpus replay measures verdicts, and both were green. **A guard has a
second output — the time it takes — and nothing in this workspace was watching it.**

⚠ **And it was compounding a second failure.** A `--record` replay job was found **11.06 hours old,
5.9 s CPU total, 0 CPU over a 5 s sample — deadlocked — holding 42 stuck hook processes.** Its log
was 0 bytes and its rc file absent. 14 workers x a hook costing seconds each is the likely cause, and
it silently poisoned every timing taken near it: the first measurement of the live hook read 37-43 s
per call, which is contention, not cost. **A measuring instrument that hangs reports nothing and
looks like nothing is wrong.**

---

## F90 — an adversarial code review with no INCIDENCE denominator over-ranks by construction · **PROCESS DEFECT (measured)** · confidence 95% · ✅ RECORDED 2026-08-31

A code-review subagent examined the eight re-scoped rules and returned **"NOT READY — three rules
are strictly weaker than what they replaced."** Two of its headline claims were wrong, and one was
copied into a plan file **in the grammar of a measurement**.

Re-derived against **12,269 distinct historical Bash commands** (23 transcripts, 140,824 lines):

| review claim | measured |
|---|---|
| `rm -rf <game>` with an escaped space → ALLOW, **"was DENY"** | ALLOW is real; *"was DENY"* is **false** — `x4_norm` maps `\` to `/` before the old name backstop ran, so the old code missed it too. **0 of 12,269** incidence |
| `.`/`..` not canonicalised, DENY became ASK | correct, genuine regression, **0 of 12,269** incidence |
| `rg` recursive-by-default is *"the single commonest full-tree command in this workspace"* | **`rg` is invoked via Bash 0 times in 12,269 commands.** The gap is real; the severity claim was invented |
| `mv -t` / `>\|` / wrapper verbs / `grep -r -e` | all real gaps; incidence 0 / 0 / 1 / 2 |
| several rules have NO probe at all | **correct, and the most defensible finding** — `sed -i` 0 probes, XRCatTool 0, durable-record 1, timeout cap 1, workspace search 3 |

**The shape:** an adversarial reviewer constructs inputs to break a predicate. That is its job, and
it is valuable. But a constructed input has **no natural frequency**, so severity ranked by "how bad
would this be" and never by "how often does this occur" puts a 0-incidence theoretical gap above a
defect costing 13 seconds on every command — which is what happened. The review never mentioned
latency; the thing it did flag (`27 -> 74` subprocesses) it rated IMPORTANT, below six CRITICALs
with no measured incidence between them.

**And the reviewer's inference became my fact.** *"The commonest full-tree command here"* went
straight into a plan file as a reason to prioritise. It was never measured by anyone.

### What to do with a review, mechanically
1. **Ask every finding for its incidence** before ranking it. The transcript corpus answers this in
   one query; there is no excuse for ranking without it.
2. **A zero-incidence finding is still worth fixing** — it is worth fixing *later*, and never at the
   cost of a measured one.
3. ⚠ **The corpus is the WRONG POPULATION for a finding about installers.** One review finding — the
   game-delete hard block losing its name backstop on a machine with no configured paths — cannot be
   argued down by a local zero, because the population is other people's machines (CLAUDE.md #20
   pointed at one's own reasoning). It was fixed on those grounds alone.
4. Record the reviewer's wrong claims, not just its right ones. A review with no error register
   reads as authoritative next time.

---

## F91 — MSYS TRANSLATES a POSIX path in an ENVIRONMENT VARIABLE crossing to a native Windows process · **DEFECT (measured)** · confidence 97% · ✅ FIXED 2026-08-31

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` -- `test_roots_arrive_on_stdin_and_a_posix_root_matches` drives the CLI with a POSIX root on STDIN and asserts the path rule fires, which is the exact comparison the environment silently broke. `test_output_carries_no_carriage_returns` guards the companion defect on the same channel.

`protect-bash.sh` passed its configured roots to `hook_facts.py` through the environment. On
Git-Bash/MSYS, exporting a POSIX-looking value to a **native Windows** child rewrites it:

    bash exported   X4_DOCUMENTS=/tmp/sbx/docs
    python received X4_DOCUMENTS=C:/Users/<user>/AppData/Local/Temp/sbx/docs

while the COMMAND TEXT being compared still said `/tmp/sbx/docs`. The two can never match, so **every
path rule silently stopped firing** — the Documents confirm, the save-game confirm, the reference
hard block, the mods delete, the deploy advisory. MEASURED: `writes_documents` read 0 for a command
writing straight into the configured Documents root.

This is not a test-only concern. `README.md` tells users they may write roots as `C:\...` **or**
`/c/...`, and `x4-paths.env` is sourced verbatim, so a `/c/...` or `/tmp/...` root on any Git-Bash
install hits it.

**Fix:** roots travel on **stdin**, ahead of the payload, terminated by a sentinel. A byte stream is
not translated. (Argv is not a safe channel either — MSYS converts path-shaped arguments too.)

### Two things this also exposed
- `_x4-env.sh` only EXPORTS what came from `x4-paths.env` (via `set -a`). `X4_DOCUMENTS` is computed
  in a loop and `X4_SAVES`/`X4_REFERENCE` come from `: ${VAR:=...}`, so **none of them were exported
  at all** — the child would have seen them empty even without the translation bug. Two independent
  causes of the same silent death, in one line of code.
- The hook suite's own sandbox lived under `/tmp`, so the shared-`/tmp` rule fired on unrelated write
  probes. That went unnoticed because **the `/tmp` rule's regex accepted a double quote and not a
  single one**, and the probes quote with singles: *the harness was passing because of a gap in the
  rule standing next to it.* Sandbox moved out of `/tmp`, with a refusal if it ever lands there
  again, and both quote styles now probed.


## F93 — a CAPABILITY improvement widens a guard nobody re-scoped for it · **DEFECT (measured)** · confidence 96% · ✅ FIXED 2026-08-31

**RE-DERIVED BY:** a 1,000-command replay of real history (`gates/hook_false_positives.py
--limit=1000 --workers=1`), then classified per hit against the parse pass directly.

The game-delete HARD BLOCK covered any delete target **under** the game folder. That breadth was
deliberate and had been defended in this register: narrowing it to the install root alone would let
`rm -rf <game>/extensions` — the entire deploy target — fall through to a mere confirmation.

It was also survivable, because the old bash helper **could not resolve `$VAR` properly**. It took
the FIRST assignment (`head -1`) and split on whitespace, so a deploy script like

    DST="$GAME/extensions/mymod"; rm -rf "$DST"

reached it as an unresolved token and fell through. The parse-pass rewrite fixed variable resolution
as a side effect of fixing everything else — and the guard immediately started **hard-denying the
documented deploy path**. `dev/_tools/deploy.py` does exactly this.

MEASURED over the first 1,000 distinct historical commands: **4 hits of this rule, 4 of 4 were a
single mod folder or one file inside one. Zero were the install root.** Two of the four had been
plain `allow` before the rewrite.

| | before | after the rewrite | after this fix |
|---|---|---|---|
| `rm -rf "$DST"` → `extensions/<one mod>` | allow (var unresolved) | **DENY** | ask |
| `rm -rf <game>` | deny | deny | deny |
| `rm -rf <game>/extensions` | deny | deny | deny |

Now root-scoped: the hard block is the install root **or** `extensions/` wholesale; everything else
inside the tree falls to the existing confirmation. Across **all 12,482** distinct historical
commands: **0 hard blocks, 48 confirmations.**

### Why this is its own shape
Every other entry here is a guard that was too NARROW, or an instrument answering the wrong question.
This is a guard that became too BROAD **without being edited** — its predicate was unchanged; what
changed was how much the machinery underneath could SEE. A guard's scope is a function of its
predicate *and* of the resolution power feeding it, and only one of those two was under review.

**The rule: when you improve what a shared helper can resolve, re-measure every guard that consumes
it.** A capability change is a scope change for every rule downstream, and it will not show up in a
diff of those rules.

⚠ **And nothing in the test suite could have caught it.** 122 bash probes, 103 unit tests and 27 E2E
cases were green, because not one of them used a variable-resolved deploy path — the shape only
exists in real history. **The corpus replay found what every hand-written probe missed**, which is
the argument for running it against real commands rather than only against cases someone thought of.

### Two smaller findings from the same fix
- The archive exclusion on the name backstop (`.zip` etc., added to kill 7 of 8 measured false
  positives) became **unreachable** once the name test was anchored at the end of the path: both
  patterns are `$`-anchored and demand different endings. The mutation gate reported it as a term no
  mutation could kill, which is what dead code looks like from outside. Proven over probes, removed.
- The test asserting `extensions/` is still hard-blocked **passed for the wrong reason** — the name
  backstop matched it too, so it stayed green against a mutant that removed the extensions clause
  entirely. Rewritten against a root deliberately NOT named "X4 Foundations". Third instance in one
  session of a guard clause shadowing the thing under test (CLAUDE.md #26).

---

## F92 — the BASELINE was never the thing the claim was about · **PROCESS DEFECT (measured)** · confidence 99% · ⚠ RECORDED 2026-08-31, mitigation is CLAUDE.md #34

**TEN instances in ONE DAY across two sessions.** Not ten bugs — one substitution, at ten
layers. **What an instrument returned** stands in for **a fact about the world**. It
survives every check aimed at the *finding*, because the finding is usually right. What is
wrong is the thing it was compared against, and nobody checks that.

| # | whose | the substitution | what it cost, or would have |
|---|---|---|---|
| 1 | mine | a search over **803 engine function names** finding no ownerless enumerator → *"no function RETURNS ownerless objects"* → **"the limit is PERMANENT"** | a confident wrong sentence in permanent record, written as denominator-complete — because the NAME census genuinely was |
| 2 | mine | *"`GetContainedShipsByOwner` does not return them"* → **"no faction owns them"** | they are `owner=argon`, `ship_arg_xs_police_01_a_macro`. One `component` call refuted it |
| 3 | mine | *"absent from the enumerator I PROBED"* → **"absent from OUR TOOL"** | **a fix to `ships`, which had nothing wrong with it.** It calls a THIRD function, `GetContainedObjectsByOwner`, which returns those ships |
| 4 | peer | `qa_sweep.py:536` reported from a **543-line copy** against a **479-line file** | ONE crash site named where there were **four**; the proposed one-line fix leaves three live |
| 5 | both | who would lose `check_coverage`, reasoned from **each session's own working copy** instead of the MERGE BASE | the agreed resolution had the polarity **backwards** and would have deleted **4 commits** |
| 6 | peer | `AttributeError` inside `dataclasses`, traceback pointing at the merged file | **their loader** — `module_from_spec` without registering in `sys.modules`. The instrument was wrong, the error named the subject |
| 7 | mine | `Stop-Process -Id 79036` **succeeded** and the check found no such process → *"killed"* | MSYS `ps` shows its own PID namespace; **that PID never existed as a Windows process.** Absent because it never was, read as killed. The real pytest kept running |
| 8 | mine | a redirected output file frozen for 8 minutes → *"hung at collection"* | **stdout block-buffering.** Collection takes 0.28s. I killed a run on that reading and destroyed the evidence of whether anything was ever wrong |
| 9 | mine | **792s vs 56.5s** on the same tree → *"a 12.8× regression from the peer's commit"* | contention from my own concurrent jobs. Re-measured alone: **1247 passed in 56.5s**, slowest test 10.5s. Reporting it would have sent the peer chasing their own commit |
| 10 | peer (F93) | a rewrite that made variable resolution **BETTER** let a hard block finally see through `$DST` | it began denying `rm -rf "$DST"` where DST was the **documented deploy path** — all 4 hits in a 1,000-command replay. The baseline that silently moved was *"what the guard could previously SEE"* |

### The tell, and it is grammatical

**You wrote *"X is not there"* when what you measured was *"the thing I asked did not return
X."*** Those differ whenever the instrument has a scope, a filter, an argument convention,
a buffer, a PID namespace, or a population — which is always.

### ⚠ Escalating certainty across corrections is the alarm, not the cure

Instances 1→2→3 are three successive *corrections of each other*, and the language got MORE
confident as the evidence got thinner: **"PERMANENT"** → **"REAL and MEASURED at 2%"** →
withdrawn entirely. Each round felt like diligence. **If a correction is more confident
than the thing it corrects, re-derive the BASELINE, not the number.**

### What actually caught them

Not vigilance. In every case a **guard that refused to act**, or a check whose failing
branch was reachable:

- a size floor that **accounted for the shrink exactly** rather than tolerating it — a
  fudge factor would have passed the same loss
- an assertion that the merged cell roster equal a number **derived from the two parents**
- verifying a kill by **enumerating survivors**, not by the kill call's return
- re-running a suspect timing **alone** before quoting it
- asking the engine **what the objects ARE** — one `component` call, the cheapest check
  available, skipped through two rounds of correction

★ **I wrote CLAUDE.md #34 about this shape and broke it three more times (7, 8, 9) within
the hour.** Knowing a trap does not protect you from it; only a check does. That is the
entry's real content.

**NO RE-DERIVATION.** This is a reasoning shape, not a code path: no test can go red for
it, and claiming one would be an instance of the defect itself. The mitigation is
CLAUDE.md #34, which is loaded in every session — the register is not.

---

## F94 - PRECISION about operands bought BLINDNESS to indirection - **DEFECT (measured)** - confidence 99% - FIXED 2026-09-01

**The defect.** The parse-pass rewrite replaced whole-command-string grepping with
structured operands. The old code caught a dangerous path wherever it APPEARED; the new
one caught it only where it RESOLVED - so every indirect way of naming a path went dark at
once.

**MEASURED against `c400a05`:**

| shape | before | after the rewrite |
|---|---|---|
| `cd <saves> && rm -f *.xml.gz` | ask | **allow** - savegames, which nothing backs up |
| `rm -rf "$(echo <game>)"` | deny | **allow** - `has_unresolved` tested only `$NAME`/`${NAME}`, so a substitution read as a LITERAL path |
| `cd <game> && echo x > f` | advisory | **lost** |

Six further shapes were allowed by BOTH (`cd`/`pushd`/subshell into game, reference,
extensions; `rm -rf "$X4_GAME"`).

* `cwd_of()` ALREADY EXISTED and was consumed only by the search rules - `facts()` computed
it and popped it before returning. The capability was present, correct, and wired to one
caller.

**The fix.** Every operand (`rm`, `cp`/`mv`/`tee`, redirects) is resolved against the
directory in force FOR ITS OWN SEGMENT, tracking `cd`/`pushd`/`popd`; `$(...)` and
backticks count as unresolvable; a root named only by its env var is recognised by NAME.
Conservative for **deletes only** (user decision): a delete is the one channel with no
backup - MEASURED, 0 of 186 auto-backups cover anything outside `dev/`.

**Cost over 13,041 distinct historical commands:** denies 836 -> 836 (+0), asks 152 -> 187,
prompt rate 1.16% -> 1.43%, and **0 facts went true -> false**.

**The FP corpus is blind to unconfigured-machine defects.** While fixing this I added a
`not u` filter to the NAME backstop, silently removing the only protection an install with
no configured paths has - **0 of 13,041** commands have that shape, so the replay could not
see it, and only the MUTATION GATE did. Two of the new mutants turned out BEHAVIOURALLY
EQUIVALENT (an unresolved operand still contains `$`, so it can never equal a root); they
are documented as not-mutated rather than left permanently red.

**Re-derivation.** `gates/hook_false_positives.py` - the full-corpus replay, both
directions - and `.claude/hooks/test_hook_facts.py`.

---

## F95 - `install.ps1` did not parse at all on the shell the README tells users to run - **DEFECT (measured)** - confidence 99% - FIXED 2026-09-01

**The defect.** Nine UTF-8 em-dashes, **no BOM**. Windows PowerShell 5.1 reads a BOM-less
`.ps1` as the ANSI codepage, so each one became three mojibake characters; one sat inside
an interpolated string at line 108 and the parser derailed - **3 parse errors, dead before
its first statement**, on the default shell of Windows 10/11. `pwsh` 7 parses it fine,
which is exactly how it reached a 3.0 release candidate.

**Two more defects were stacked behind it, each masked by the one in front:**

1. Neither installer copied `mods/`, so the README's *"copy that folder into
   `{game}/extensions/`"* named a directory that never existed - and `.gitignore`'s bare
   `content.xml` also dropped the mod's manifest from `git archive`, and an X4 extension
   without a manifest does not load, silently.
2. `Get-Command bash` resolved to the **WSL stub** in `System32`, present wherever WSL is
   enabled (every Docker Desktop install), so `setup.sh` died with
   `execvpe(/bin/bash) failed`. On a machine WITH a distro installed that is worse - setup
   would have run inside Linux, where a Windows drive path does not resolve: a silent
   wrong install.

**The fix.** ASCII-only in `install.ps1`; Git Bash preferred and the known stubs refused by
path; `mods` added to both installers' copy lists; `!mods/**/content.xml` un-ignored.

* **Not one was findable by reading the diff - all three were found by EXECUTING the
installer**, which is why *"run it, do not read it"* is now the rule for bootstrap scripts.

**One check would have been vacuous.** A mutant restoring a single em-dash to the header
comment is caught by the ASCII test and **passes the parser test**, because only the
em-dash inside an interpolated string breaks parsing. That is why there are two checks
rather than one: they fail differently.

**Re-derivation.** `tests/test_bootstrap_scripts_are_portable.py`
(`test_bootstrap_script_is_pure_ascii` - catches the CAUSE and runs on every platform,
including the Linux CI leg); `tests/test_installers_agree.py`
(`test_the_shipped_game_extension_is_in_both` - the `mods/` copy); and a real Windows
PowerShell 5.1 install in `.github/workflows/ci.yml`, which catches any other syntax defect
but only where that engine exists.

---

## F96 - a shell RESERVED WORD in front of a command was a TOTAL guard bypass · **REAL (measured, FIXED)** · confidence 99% · found 2026-09-01

**The defect.** `protect-bash.sh` splits a command on `;` and `&&`. A compound statement
puts a reserved word in front of the simple command inside a segment, so
`if true; then rm -rf <game>; fi` yields the segment `then rm -rf <game>` - and
`verb()` returned **`then`**. Every verb-keyed rule (`rm`, `sed`, `git`, `grep`) missed.

**MEASURED, E2E through the real hook: 90 bypasses over 10 compound forms x 9 seeds.**
The forms: `if/then` · `if` as the CONDITION · `for` · `until` · `else` · `elif` ·
`case` arm · `!` negation · `f() { ... }` · nested `if`+`for`. For the three HARD BLOCKS
(game root, extensions wholesale, reference tree) the move was **deny -> allow**.

The 10th seed was **immune**, and that is what pins the root cause: it is
**redirect-keyed, not verb-keyed**. The operand was present and correct the whole time;
only the VERB was wrong.

**Why nothing caught it.** 151 unit tests, 35 mutants, 19 predicate probes and a
13,500-command replay were all green. Every one starts from a verb the parser has already
chosen - they test the PREDICATES, and this was the PARSER feeding them. The coverage
report ("0 of 19 predicates uncovered") was true and irrelevant.

**What did catch it: a differential fuzzer extended to cover shell syntax CLASSES**
rather than shapes that had already bitten us. The previous 19 mutators were each written
in reaction to a specific past bug, which is exactly why `<<<` and `if/then` were absent -
nobody had been burned by them yet.

**Real-world cost, MEASURED on 13,503 historical commands.** 1,240 (9.2%) change segment
verbs; **13 change verdict, all TIGHTENING, 0 loosening.** Three of the 13 go
`allow -> ask`, and all three are genuine: a `rmdir` inside the game folder and two `cp`
loops writing into the game install's own `.claude/hooks` - **real mutations of the game
directory that the guard silently allowed.** Not theoretical.

**Fix.** `_strip_reserved()` in `hook_facts.py`, applied inside `_unwrap()` - the single
place every segment passes through, so no rule has to learn shell grammar for itself.
Token equality only, so `do_thing`, `iffy`, `done_marker.sh` keep their names.

**WARNING - the fix introduced its own regression, and the BASELINE caught what the fix
did not.** The first case-arm rule also matched the tail of a **process substitution**:
`diff <(cd "$GAME" && rm -rf extensions) <(echo b)` had `rm -rf extensions)` eaten as a
"label" and the delete went silent - a command the PRE-FIX code handled correctly. Caught
only by diffing the corpus per item (20 commands with a paren-suffixed verb). A case label
is a single glob token and carries **no whitespace**; every dangerous rule needs an
operand, and an operand needs a space - which makes the spaceless restriction sound rather
than merely convenient.

**Re-derivation.** Four `verify-hook-tests.py` mutants, one per clause; 6 tests in
`TestReservedWordsDoNotHideTheCommand`; 10 compound-form mutators in `scripts/fuzz-guard.py`.

---

## F97 - `<<<`, `<<` in a comment, and an arithmetic shift each opened a bogus HEREDOC · **REAL (measured, FIXED)** · confidence 99% · found 2026-09-01

`heredoc_marker()` scanned for `<<` and, on a hit, treated everything after that line as a
heredoc BODY - data no rule inspects. Three constructs open no heredoc at all:

| construct | what the scanner did |
|---|---|
| here-string `cat <<< hello` | reached the **second** `<` of `<<<`, read `<< hello`, marker `hello` |
| `# shifts a << b` | the marker pass did not stop at a comment - marker `b` |
| `n=$((1 << FOO))` | a left SHIFT - marker `FOO` |

Each blanked the rest of the command. E2E: a game-directory `rm -rf` the guard refuses
unaided became a **silent allow** behind any of the three.

**Incidence, MEASURED over 13,503 historical commands:** `<<<` 40 · `<<` after a `#` 15 ·
`$(( ... <<` 8 · **49 distinct (0.36%)**. **0 of the 49 changed verdict** - the shapes were
present in real history but never in front of a dangerous operation. That null argues FOR
the fuzzer, not against the fix: **history contains only what has already happened.**

**Fix.** In `heredoc_marker`: skip a `<` on either side of the pair, stop the scan at the
first unquoted word-initial `#`, and blank `$(( ... ))` before scanning. Real heredocs -
`<<EOF`, `<<'PY'`, `<<-END`, one beside an arithmetic shift, one with a trailing comment -
are all still recognised, which is the half a naive `return None` would have passed.

**Re-derivation.** `.claude/hooks/test_hook_facts.py` -- 4 mutants (one per clause,
mutated separately) + 8 tests in `TestNotEveryDoubleAngleIsAHeredoc` (:135), half of
them controls in the other direction: `test_a_here_string_opens_no_heredoc` and
`test_an_arithmetic_left_shift_opens_no_heredoc` pin the two shapes that must NOT
open a heredoc, while `<<EOF`, `<<'PY'` and `<<-END` must still be recognised.

---

## F98 - `bash -lc` and `eval` carried a command past every rule · **REAL (measured, FIXED)** · confidence 99% · found 2026-09-01

`_inner_commands()` unwrapped a shell's `-c` argument by matching the **literal token
`-c`**. MEASURED E2E against a game-root `rm -rf` the guard denies unaided:

| wrapper | verdict |
|---|---|
| `sh -c '<rm>'` | deny |
| `xargs ... '<rm>'` | deny |
| **`bash -lc '<rm>'`** | *** SILENT ALLOW *** |
| **`eval '<rm>'`** | *** SILENT ALLOW *** |

`-lc` is the ordinary spelling for a login shell; one character of flag clustering stood
between a hard block and nothing. `eval` was not handled at all.

**Fix.** Match a short-flag CLUSTER containing `c` (`-lc`, `-ic`, `-xc`), add `dash`/`ksh`
to the shell list, and treat `eval`'s arguments as a command. Controls: `grep -c` is not a
shell, `bash -lc 'ls -la'` stays quiet, and a quoted `bash -c` is data.

**Incidence:** 3 of 13,503 commands carry a newly-unwrapped carrier; **0 changed verdict**
(none was dangerous), against 57 that already used the handled plain `-c`.

**Re-derivation.** 2 mutants + 6 tests in `TestWrappersThatCarryACommandAsText`; 5 wrapper
mutators in the fuzzer.

### The through-line of F96-F98, in one sentence

**All three were in the PARSE pass, none was found by any suite, and all three were found
by an EXTERNAL ORACLE** - `bash -n`, and a fuzzer that enumerates the shell grammar
instead of our own bug history. A suite can only ask questions someone thought to ask; the
grammar is finite and can be walked. The register now holds **six** parser defects (the
apostrophe bypass, its 87%-FP fix, the quoted Windows path, and these three), which is the
argument for keeping the fuzzer in the gate rather than treating it as a one-off.

**Re-derivation.** `.claude/hooks/test_hook_facts.py` --
`TestWrappersThatCarryACommandAsText` (:274): `test_every_shell_c_spelling_is_unwrapped`
walks all seven spellings (`bash -c`, `bash -lc`, `sh -c`, `sh -ic`, `zsh -c`,
`dash -c`, `ksh -c`) because the original defect was one character of flag CLUSTERING,
`test_eval_is_unwrapped` covers the other carrier, and
`test_a_wrapper_inside_a_compound_is_still_unwrapped` proves the two fixes compose.
`test_a_harmless_wrapped_command_stays_quiet` is the control in the other direction --
unwrapping must not make an ordinary wrapped command fire a rule.

---

## F99 - the guard fuzzer kept a HAND-WRITTEN copy of the verdict map, and was blind to 26% of the rules · **REAL (measured, FIXED)** · confidence 99% · found 2026-09-01

`scripts/fuzz-guard.py` decided each command's verdict from two literal tuples,
`DENY` and `ASK`, with the comment *"read off protect-bash.sh. Policy lives there."*
It had been read off once and had drifted since.

**MEASURED against the hook's own mapping: protect-bash.sh maps 19 predicates; the
tuples listed 14.** Missing entirely: `search_rooted_reference`,
`search_rooted_workspace`, `copy_into_game_or_profile`,
`redirect_truncate_into_game_or_profile`, `durable_truncating_redirect`. A sixth,
`longjob_foreground`, was classed `ask` where the hook returns `deny` (confirmed E2E).

**Why that is a blind spot rather than a cosmetic error.** The fuzzer skips any seed
whose base verdict is `allow`, on the reasoning that there is nothing to weaken. A rule
missing from the map makes its seed read `allow` - so the seed is **dropped in silence**
and a bypass in that rule can never be reported. Two of the twelve seeds were being
discarded on every run, and the summary line said so in words nobody reads:
*"10 seeds (of 12, the rest already allow)"*.

The same shape as every other entry in this register: **a step that narrows the data and
reports success anyway.**

**Fix.** The map is PARSED from `protect-bash.sh`, filtered against the fact names
`hook_facts` actually emits (which keeps the hook's own English prose - "on the",
"on every" - out of it), and a parse yielding fewer than `MIN_MAPPED_RULES` **REFUSES**:
an empty map would make every seed `allow`, skip every seed, and print *"no bypass
found"* over having exercised nothing at all. The run now prints its derived policy
(`19 rules: 14 deny, 3 ask, 2 advise`) and names any predicate that carries no verdict.

**Result:** seeds used 10 of 12 -> **12 of 12**; mutants 550 -> 660. The five previously
invisible rules are now fuzzed.

**Re-derivation.** `tests/test_fuzzer_policy_matches_hook.py`: every hook rule carries a
verdict, no predicate is silently unfuzzed, and the refusal branch is exercised against a
hook that maps nothing.

### The companion finding: skips were invisible too

Pytest's summary says *"1159 passed, 14 skipped"* and never says which or why - the same
collapse that let **125 tests sit dormant for weeks** behind a lookup that could not
succeed in the public tree (F-adjacent, see the `stamp-mod-build` entry). `-rs` is now
permanently on, the count is printed as its own line, and `X4_MAX_SKIPS` turns it into a
failure for a caller that knows its environment. The ceiling is deliberately generous
(CI: 40) because a cold runner legitimately skips more than a configured machine, and a
check that cries wolf is one people learn to ignore. Both branches were exercised: 5
skips against a ceiling of 0 exits 1, against 10 exits 0.

---

## F100 — four gates could not report the thing they were built to find · **DEFECT (measured)** · confidence 98% · FIXED 2026-09-02

**RE-DERIVED BY:** `tools/x4validate/tests/test_mutation_probe.py` --
`test_a_mutant_that_adds_NO_new_failure_is_a_SURVIVOR` and its twin
`test_a_mutant_that_ADDS_a_failure_is_a_KILL` pin the failure-SET escalation;
`test_the_mutation_window_does_not_rewrite_line_endings` reads the target's bytes from
INSIDE the stubbed test run, because `restore_all()` puts them back before main()
returns and the damage is only visible during the window.
`tools/x4validate/tests/test_control_bytes.py` (11 tests, 5 twins) pins the sweep's
scope against the repo root.

**The headline is not any one of the four. It is that a broken gate and a working gate
printed the SAME THING.** `mutation_probe` reported `killed 45/45   survivors: 0` both
before and after two of its three causes were fixed. That output is what a healthy run
looks like, so no amount of reading it could distinguish the two states.

What finally separated them was **planting a mutant whose answer was already known**: an
edit to a COMMENT, which changes no behaviour and therefore cannot be caught by
anything. The gate reported it `killed`. That is impossible, and it is the only
observation in the whole exercise that could not be explained away.

    MEASURED, with the probe's marker present : 5 failed, 1178 passed
    MEASURED, without it                      : 1183 passed
    delta attributable to the marker          : exactly 5

Cause 3 is the interesting one because **it is not a bug**. `x4validate/_mutation.py`
makes every artifact-WRITING path refuse while `.mutation-probe-active` exists --
deliberately, so a poisoned artifact cannot outlive the window. That is correct. It
simply means the full suite can never be green during a probe, so the escalation could
not ask "is the suite green"; it had to ask "did any NEW failure appear".

**The other three, briefly, because they share the shape:**

* `verify-hook-tests.py` judged each mutant by the PREVIOUS mutant's failures. CPython
  invalidates a cached `.pyc` on (source mtime in SECONDS, source size); successive
  mutants are written to the same path within the same second, so whenever two leave
  the file the same size the second run imports the first one's bytecode. MEASURED: 2
  of 54 reported NOT CAUGHT while both, reproduced by hand, turned their target test
  red -- and the tests named in each case belonged to the entry above it in the list.
  The false-alarm direction is what was observed; the silent direction (a mutant
  reported CAUGHT because the previous one's failures happened to include its target)
  is equally reachable and worse.
* `control_bytes.py` scanned **171 of 241** tracked files: `ROOT` was the package dir
  and `git ls-files` ran there. The 57 files it could not see were the whole of
  `.claude/hooks/` and `scripts/` -- precisely where content is written through
  interpreter strings and where these escapes collapse. It was structurally blind to
  the file carrying a literal `0x08`.
* `obtainability_audit.py` recorded `base_macro_files_scanned` and `_unreadable` and
  compared neither, printed neither. A coverage collapse could only ever have surfaced
  disguised as a change in the findings.

★ **The rule.** A gate whose broken output is indistinguishable from its healthy output
cannot be verified by running it. It can only be verified by feeding it an input whose
correct answer is already known -- a mutant nothing can catch, a file with a planted
byte, a population with a known denominator. Everything else is reading tea leaves.

---

## F101 — a guard protected the ASSISTANT's path and not the USER's · **DEFECT (measured)** · confidence 97% · FIXED 2026-09-02

**RE-DERIVED BY:** the installer E2E harness -- a case that creates a locked reference
directory and requires `bin/unpack-reference.sh` to refuse with rc 2, plus a
**cold-sandbox control that runs first** and fails the whole run if the path resolver
can still see the real game or reference tree.

`bin/unpack-reference.sh` **wrote** `reference/.unpacked-and-locked` at the end of every
run and **read it nowhere**. The sentinel's own text says:

    reference\ is read-only; remove this file manually to re-unpack.

That was false for the one path that actually unpacks. The lock was real, but it lived
in `protect-bash.sh` -- the PreToolUse hook -- which guards commands the ASSISTANT runs
through its Bash tool. It cannot see the XRCatTool invocation this script makes in a
child process, and it does not run at all when a person types
`bash bin/unpack-reference.sh` or `bash install.sh --unpack`.

So the protection covered exactly one of the two ways to reach the destructive
operation, and the one it covered is not the one the documentation tells users to use.

**Found by walking into it.** An installer E2E case passed `--game <nonexistent>
--unpack` expecting a failure; `_x4-env.sh` fell through to `$X4_TOOLKIT`, read the REAL
config, and ran XRCatTool against the real game and the real reference tree. That is
CLAUDE.md #26's cold-is-not-cold trap: clearing the obvious `X4_*` variables is NOT
isolation while `$X4_TOOLKIT` still points somewhere real.

    MEASURED aftermath: 1 of 510,711 files under reference/ had a new mtime,
    and it was the sentinel itself -- XRCatTool preserves catalog timestamps.

Harmless, and entirely luck. The correction to my own account matters as much as the
finding: I first reported that "every mtime in reference/ changed", which I had not
measured and which was wrong.

★ **The rule.** A guard in the assistant's tool layer protects the assistant. Anything a
USER can invoke needs the check inside the thing they invoke -- and "the hook covers it"
is an answer about a different threat model. Corollary for test harnesses: a sandbox is
not sandboxed until a CONTROL has proven the resolver cannot see outside it.

**Re-derivation.** `tests/test_reference_fingerprint.py` -- it exercises the sentinel
(`.unpacked-and-locked`) and the recorded build id, which is what the repaired script
now reads before it will write anything. The refusal itself lives in
`bin/unpack-reference.sh` ("THE LOCK, CHECKED", rc 2), and note that `bin/` is not a
prefix this register's own name-matcher can express -- a citation to a `bin/` script
cannot be verified by the re-derivation gate at all, which is worth knowing before
someone reads silence there as absence.

## F102 — the guard could be made to do UNBOUNDED work, and every bound it had was on a different axis · **DEFECT (measured)** · confidence 99% · FIXED 2026-09-06

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py`, five tests. Two go red against the
pre-ceiling module on BEHAVIOUR, not on the constant's absence — their bounds are
literals, because a test that reads the constant it is checking cannot fail when that
constant is missing for the right reason. One goes red only against a deliberate
TRUNCATING mutant. Two are falsification twins that stay green in all three states.

`resolve()` substitutes what a command assigned to itself. It loops five times, under
the comment *"bounded: nested vars, never a loop"*. That is true, and it is about the
wrong axis: the ITERATION COUNT was bounded and the STRING was not.

A value that names its own variable re-expands every reference on every pass. MEASURED
on the real command, growth is a clean **x9 per pass**:

        pass          chars     factor
           1            916     229.00
           2          10316      11.26
           3          94916       9.20
           4         856316       9.02
           5        7708916       9.00

so the four-character token `${B}` reached **7.7 MB** inside the five passes the loop
already allowed. Downstream, for one **949-character** command out of real session
history:

        all_cmds   : 3 entries, 23,128,230 chars total, longest 23,127,268
        segments   : 531,448
        working set: 18.3 GB — 0.7 GB free of 31.8 GB, with the game running

This is the **blocking PreToolUse path**. It hangs the whole session, and it needs no
adversary: a self-referential assignment is ordinary shell.

### Why no existing bound caught it

Each neighbouring limit is sensible on its own, and two have a census behind them:

| bound | value | what it bounds |
|---|---|---|
| `_MAX_CARRIER_DEPTH` | 4 | how deep carriers nest |
| `_MAX_CARRIED` | 250 | how MANY command strings the walk yields (10x the observed max over 13,503 real commands) |
| the `resolve` loop | 5 | how many substitution PASSES |

None bounds SIZE, and nothing multiplied them together. **A product of
individually-sensible bounds is not a bound.**

### The first diagnosis was wrong, and that half is worth more

`tokens()` was called **>200,000** times and fed 8.2 MB. That is a real number and it
looked exactly like a cause: `tokens` is pure, and ~22 call sites re-tokenise each
segment independently. A memoisation was written, and MEASURED: identical call counts,
identical verdicts, and the incident command **still timed out at 45s and still climbed
past 1.7 GB**. It was reverted rather than shipped, because an optimisation carrying a
docstring that explains a cause it does not fix writes a wrong story into permanent
record.

The 200,000 calls were a SYMPTOM of 531,448 segments. **A hot leaf is where time is
SPENT, not where the work is MADE.** Stack sampling showed a flat distribution across
`_scan`, `segments`, `tokens` and `substitutions` at depth 3—8 with no recursion, which
is the signature of "too much input" rather than "a bad loop". The census that settled
it measured each stage: 949 chars in, 23,128,230 chars of `all_cmds` out.

### The fix, and why this DIRECTION

`_MAX_RESOLVED = 65536` — 3.3x the longest of 17,268 real historical commands (19,583
chars). On exceeding it, `resolve` returns the **previous complete pass**, references
intact, which `has_unresolved()` already reports as unresolved: the file's existing
channel for a value a hook cannot know.

The direction is the point. Truncating to the ceiling would also be "bounded", and it
would hand every path rule a SHORTER OPERAND while looking successful — the
narrowing-step defect this register is about. A deliberate truncating mutant passes four
of the five tests, which is why the fifth asserts the MECHANISM structurally: what comes
back must be one of the loop's own intermediate states, and an arbitrary prefix is not.

### Verification

Per-item corpus replay over **17,268** real commands, pre vs post, full `facts()` dict
compared field by field:

* **1 verdict moved**, and it is the incident command — `MemoryError` (no verdict at
  all) becoming a complete one. 17,267 byte-identical. **0 loosened.**
* whole-corpus wall clock **297s -> 47s**, and the command that could not be answered
  now answers.
* exactly **1 of 17,268** reaches the ceiling, measured by applying one more pass rather
  than by a threshold. The first probe used `_MAX_RESOLVED // 2` and missed the only real
  hit by 1,282 chars — the arbitrary constant was mine, and it was the bug.
* largest resolved token corpus-wide: 31,486 chars, under the ceiling.

`gates/hook_false_positives.py`'s `HOOK_TIMEOUT` stays, although this should make it
unreachable: it is the only thing that turned an invisible wedge into a named command,
and the next unbounded axis will not be this one.


## F103 — the freshness marker did not travel with the tree it describes · **DEFECT (measured)** · confidence 98% · FIXED 2026-09-06

**RE-DERIVED BY:** four sandbox cases for `check-reference-version.sh`, run against the
old and the new script side by side. The old/new behaviour **inverts** on two of them.

`check-reference-version.sh` compared the live Steam build against
`$X4_TOOLKIT/.claude/.reference-buildid`. That file is DETACHED from `reference/`: it
must be updated by hand as a separate step after an unpack, and that is the step that
gets missed.

MEASURED 2026-09-06 — it had been missed, and the copies had diverged:

| source | build |
|---|---|
| `<toolkit>/.claude/.reference-buildid` (what the hook read) | **23524486** — stale |
| game-root `.claude/.reference-buildid` | 23660954 |
| `reference/.unpacked-and-locked` (the sentinel, lives IN the tree) | 23660954 |
| the live game (`appmanifest_392160.acf`) | 23660954 |

So the hook announced a stale `reference/` **at every session start** while the tree was
current, and the remedy it names is a **~60 GB re-unpack**. A banner that is always wrong
trains you to ignore the banner — the exact failure `_freshness.py` exists to prevent,
here attached to an expensive recommendation.

**And it was blind in the other direction too.** Sandbox case 2 — sentinel old, detached
file current — the OLD script is **SILENT** on a genuinely stale tree. It was not merely
noisy; it was wrong both ways, and only the harmless direction was visible.

### The knock-on, which is why this is registered and not merely fixed

The stale mtime produced a **wrong root cause in another gate**. `gates/schema_sweep.py`
recorded, in permanent record, that its schema floor moved because `reference/` was
re-unpacked on 2026-09-02 — read off the sentinel's **mtime**. The sentinel is the one
file `bin/unpack-reference.sh` rewrites, so its mtime is evidence about the SCRIPT.
MEASURED by walking the tree: **1 of 510,711 files** has an mtime after the baseline and
it IS the sentinel, because XRCatTool preserves the catalogs' timestamps. Its own
CONTENTS say 2026-06-22. CLAUDE.md #34 exactly.

### The fix

Sentinel first — it is written into the tree by the unpack and names the build in its
text, so it cannot drift from its subject — falling back to the detached file only for a
tree that predates it, and the warning now names WHICH source it used. The stale detached
copy was corrected; all four sources now agree.

`schema_sweep`'s attribution is corrected in place to **UNKNOWN**, naming what it is not
(not the reference tree, not the mods) and naming the candidate that was ruled out
without being tested: that the CHECK changed. `x4validate/_xsd.py` was modified twice
after the baseline — 54d997e (2026-08-27) and ffe6dd6 (2026-08-28) — which is one of
the two causes that gate's own instruction already offers.

**The general rule: a marker that does not travel with its subject is the same shape as
an artifact with no fingerprint.** Put the marker inside the thing it describes.

## F104 — the guard fuzzer derived its denominator and never used it · **DEFECT (measured)** · confidence 99% · FIXED 2026-09-06

**RE-DERIVED BY:** `scripts/fuzz-guard.py` itself, which now REFUSES (rc 2) when a
policy rule a seed could reach has none, and when a seed makes no policy rule true.

F99 closed the POLICY half of this instrument: the verdict map is derived from
`protect-bash.sh` instead of hand-copied, with a `MIN_MAPPED_RULES` floor and a note
for facts that carry no verdict. What it never added is the other direction — how
many of those derived rules any SEED actually exercises.

MEASURED 2026-09-06:

| | |
|---|---|
| policy rules derived | 23 (15 deny, 5 ask, 3 advise) |
| rules exercised by at least one seed | **9 (39%)** |
| rules with NO seed | **14**, including six DENIES and one HARD BLOCK |

and the run printed `no bypass found: every mutant kept its seed's verdict` over a
healthy-looking mutant count. **A green with no denominator, inside the instrument
whose entire job is to find that shape everywhere else.**

★ **The cause is F99's cause, and it is worth writing twice: the seed list was grown
from shapes that had ALREADY BITTEN US.** That is exactly why the untested ones were
untested — nobody had been burned by them yet. A bug history is not a specification;
the POLICY is, and it was already being derived. Only the seeds were not measured
against it.

### The fix

Thirteen seeds, taking coverage to **21 of 23 (91%)**, and two REFUSALS, because a
fraction nobody acts on is not a fix:

* a rule a seed COULD reach but none does — rc 2, naming it;
* a seed that makes NO policy rule true — rc 2, because it is fuzzed for nothing and
  inflates the mutant count. This immediately caught two of my own new seeds:
  `DURABLE` matches durable FILE NAMES, not any path under a root, so `<game>/f.xml`
  exercised neither durable rule.

**Two rules are structurally out of reach and are NAMED rather than silently
missing.** This fuzzer mutates command SYNTAX around a fixed operand — its own
docstring says the dangerous operand is byte-identical in every mutant — so it cannot
construct `carriers_truncated` (a pathological INPUT SIZE) or `timeout_over_cap` (a
numeric FIELD of the tool payload, not syntax at all). Both are unit-tested instead,
and if a mutator ever gains the reach, dropping it from `STRUCTURAL` raises the floor
by itself. This is CLAUDE.md #37's rule applied to our own instrument: name the axis
it holds fixed, and probe that axis another way.

### What the seeds immediately found

**36 bypasses**, across exactly the four rules that had just stopped being unseeded:

| rule | bypasses | root cause |
|---|---|---|
| `durable_python_open_w` | 21 | read `segments(body)` while the carrier list existed |
| `dollarq_after_pipe` | 7 | double `strip_heredocs`, plus parameter expansion |
| `git_discards_x4_files` | 4 | subcommand scan did not step over leading wrappers |
| `git_wipes_x4_dir` | 4 | same |

Fixed as B6—B9. The progression was 36 → 10 → 5 → 2 → 0, and the control still
rediscovers 101 bypasses from a planted defect, so the zero can go red.

★ **The transferable point is the ORDER of events.** Nothing about the guard changed
to create those 36 holes; they were all already there, in rules that had been shipped
and were being relied on. What changed is that the instrument stopped being able to
report success without saying what it had covered. **The bypasses were not found by
looking harder at the guard. They were found by making the instrument state its
denominator.**

## F105 — two copies of the BaseX tooling drifted apart, and nothing could say which was ahead · **DEFECT (measured)** · confidence 97% · RECONCILED 2026-09-06

**RE-DERIVED BY:** a per-file direction test that asks, of each differing file, *which
commit of the OTHER repository does this copy match?* — run over both histories, with
line endings normalised. It is a procedure rather than a gate; the gate that would
catch a future drift does not exist yet, and that is stated below rather than implied.

There are **two** BaseX installs and they are separate repositories:

| | where | role |
|---|---|---|
| shipping copy | `x4-claude-toolkit/tools/basex/` | what a release publishes |
| **live install** | `<modding root>/tools/basex/` | what actually BUILDS `x4raw` and `x4eff`, and holds the 1.9 GB `basex/data` |

The live one has **no remote and a history disjoint from the toolkit's**, so movement
between them is a file port, never a merge — and nothing checked it. This is F87's
shape: the artifact everyone relies on is produced by the copy nobody reviews.

MEASURED 2026-09-06, before the artifact rebuilds: **11 of 18 files differed**, and one
change existed ONLY in the live install — `staleness.py` at dbd8f1f (2026-08-31),
recording why `fingerprint()` omits the per-folder `detail` vector and naming the tool
that answers the question its staleness reason raises. The shipping repo never got it.

### ⚠ The instrument problem IS the finding

A byte-level `cmp` reported 11 differing files. From that I concluded *the live install
is ahead; porting toolkit → live would destroy work*, and nearly inverted the whole
operation. Both halves of that were wrong:

1. **3 of the 11 differ by CRLF vs LF over identical content** — `preflight.py`,
   `stage.py`, `test_preflight.py`. CLAUDE.md #56 exactly, where a documented `diff -rq`
   proof once reported 52 differences of which 12 were real. Git's own staging confirmed
   it afterwards: those three vanish on `git add`.
2. **The direction test was broken.** It passed MSYS-style `/c/Users/...` paths to
   Windows Python, which cannot open them, so every file printed a `FileNotFoundError`
   traceback above a confident *"genuinely diverged"*. Re-run with real paths: **7 of the
   8 remaining files are the LIVE INSTALL BEHIND the toolkit**, matching toolkit commits
   from 2026-08-25 and 2026-08-29.

Gotcha #22's base rate held twice in one investigation. The finding was real; the
reading of it was mine, and so were both errors.

### The resolution

Direction established PER FILE against BOTH histories — *which commit of the other does
this copy match?* — never from size, date, or a byte diff. Eleven files ported
toolkit → live, one (`staleness.py`) live → toolkit, each byte-proved after writing.
`stage-manifest.json` correctly stays local: it is a gitignored build artifact naming a
machine-specific staging directory. Both trees are now byte-identical apart from it,
and 81 tests pass in each from the same sources.

★ **The durable rule: when two copies of one tool exist, the question is never "do they
differ" but "which commit of the other does each one match".** A byte comparison cannot
answer that — on a Windows checkout it cannot even answer the first question reliably.

⚠ **STILL OPEN, and named rather than left implicit:** nothing PREVENTS this drifting
again. The topology fix (one copy, or the live one as a checkout of the shipping repo)
is a change to how the workspace is laid out, not a code change, and was not taken here.
Until then the reconciliation is a procedure someone has to remember to run — which is
the same class of guarantee as a marker that does not travel with its subject (F103).

> **2026-09-14: the topology fix WAS taken.** The separate BaseX repository was retired with the dev
> repository: every one of its code files was an older copy of this repo's `tools/basex/` (its exact
> bytes are in this repo's history), both databases were rebuilt from here (x4raw 14,054 of 14,065
> documents with all 11 exclusions named, the same figures as the retired build; x4eff 10,970,
> complete), the queries compared gave identical per-item answers, and the old repository is archived.
> One copy remains, so there is nothing left for it to drift from.

## F106 — a wrapper flag whose VALUE is a word became the command name · **DEFECT (measured)** · confidence 99% · FIXED 2026-09-06

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` (five tests: the verb directly,
the hard-block consequence, a per-wrapper twin, a numeric/braced regression twin, and a
must-not-fire twin), plus five grammar-derived mutators in `scripts/fuzz-guard.py`.

`_verb_token` resolves the command name through env-assignment prefixes and wrappers.
It skips any token starting with `-`, and after a wrapper it also skips a token matching
`_WRAPPER_ARG` — which is `^(\d+(\.\d+)?[smhd]?|\{\}|\+)$`. **Only a number, a
brace pair, or a plus.** So a wrapper flag whose value is a WORD left that word standing
as the verb:

| command | resolved verb | verdict |
|---|---|---|
| `env -u X4_GAME rm -rf <game>` | `x4_game` | **ALLOW** |
| `sudo -u root rm -rf <game>` | `root` | **ALLOW** |
| `env -C /tmp rm -rf <game>` | `tmp` | **ALLOW** |
| `timeout -s KILL 5 rm -rf <game>` | `kill` | **ALLOW** |
| `stdbuf -o L rm -rf <game>` | `L` | **ALLOW** |
| `nice -n 5 rm -rf <game>` | `rm` | deny |
| `xargs -I{} rm -rf <game>` | `rm` | deny |

The last two worked **by accident of their argument shape**, which is exactly what made
the gap invisible: the wrapper handling looked exercised.

A verb-keyed miss takes every verb-keyed rule with it — all three HARD BLOCKS
included. And `env -u VAR cmd` is not an adversarial spelling: it is the documented way
to clear a root in this workspace, typed several times in the very session that found
this.

### Why the fuzzer could not find it

Its four wrapper mutators are `timeout`, `exec`, `setsid`, `nice` — every one a BARE
wrapper or a NUMERIC argument. They were grown from wrappers that had already bitten us.
That is **F104's lesson one layer in**: F104 fixed the fact that 14 rules had no seed;
this is the mutator set having the same bias as the seed set had. Five mutators derived
from the GRAMMAR of a wrapper prefix (`env -u VALUE`, `env -C VALUE`, `sudo -u VALUE`,
`timeout -s VALUE`, `stdbuf -o VALUE`) now cover it.

### The fix, and why PER WRAPPER

`_WRAPPER_VALUE_OPTS` maps each wrapper to the flags that consume the next token. It is
deliberately **not** a union set: honouring `-u` for a wrapper that has no such flag
would consume the REAL verb and return its first argument instead — a MISS, i.e.
strictly less safe than the bug being fixed. `nice -u rm -rf /x` still resolves `rm`,
and a test pins it.

★ **TWO SITES, ONE TABLE.** Fixing `_verb_token` alone left **10 bypasses**, measured by
the fuzzer within seconds of the new mutators existing: `git_wipes_x4_dir` and
`git_discards_x4_files` run their OWN token scan rather than keying off `verb()`. The
loops stay separate (one streams `tokens()` with its quoted flag, the other indexes a
list) but WHICH flags take a value is one table in one place — this file's history is a
list of things that drifted because two sites computed one answer.

### Verification

* fuzz-guard rc 0 over **100 mutators**, control still rediscovers 116 bypasses from a
  planted defect, so the zero can go red.
* per-item corpus replay, **17,268** real commands: 5 moved, **none tightened, none
  loosened**. The only differing field is the tracked `cwd` — because `env -u FOO cd
  /dir` now resolves its verb to `cd` and is tracked, where the flag's value used to be
  taken as the command.
* 433 unit tests; `test-hooks.sh` 138 passed, 0 skipped; `verify-hook-tests` 0 of 82
  mutations uncaught.

⚠ **Two instruments REFUSED during this fix and both were right**, which is the reason
either can be believed: `fuzz-guard` returned rc 2 *"could not plant the control defect
— an anchor has moved"* (removing two dead locals had orphaned its control), and
`verify-hook-tests` reported *"DID NOT APPLY — the mutant is stale"* (the fix hoisted
`name = _verb_name(t)` out of the line its mutant anchored on). A harness that cannot
plant its defect must say so rather than report a clean run.

## F107 — model-facing hook output above 10,000 characters was FILED, and no hook could tell · **DEFECT (measured)** · confidence 97% · FIXED 2026-09-07

**NO RE-DERIVATION** by an in-repo check, and that is correct rather than a gap: the cap
is a property of the HARNESS, not of our code, so nothing we own can assert it. It is
re-derived by running the probe arms below against a live Claude Code, which is a named
step on a CC version bump (CLAUDE.md gotcha #38 — the number has already moved once,
high-20s K on 2.1.218, 10,000 on 2.1.246, still 10,000 on 2.1.263). The constant lives
in exactly one place, `X4_HOOK_MAX_CHARS` in `_x4-env.sh`. ⚠ `test-hooks.sh` DOES
reference it, but that file lives only in the game-root hooks directory, which is not a
root `register_rederivation` searches — so it is deliberately not cited.

**RE-DERIVED BY (manual, on a CC bump):** four probe arms per channel against live Claude Code
(`claude -p --settings <probe>`), each emitting a head sentinel and a tail
sentinel and discriminating on **whether the TAIL survives** — not on wording,
and not on the exit code, which never changes.

Claude Code files a hook's model-facing output above **10,000 CHARACTERS** and
shows the model a ~2 KB preview. Nothing errors; the exit code is unchanged; the
contribution to that window is simply gone. MEASURED on CC **2.1.263**:

| channel | 500 | 9,950 | 10,000 | 10,001 | 30,000 |
|---|---|---|---|---|---|
| bare stdout | BOTH | — | **BOTH** | HEAD | HEAD |
| `additionalContext` | BOTH | **BOTH** | — | HEAD | HEAD |

**The 9,950 arm is the load-bearing one.** Its raw stdout was **10,032**
characters and it still arrived whole, so the cap is on the CONTENT the model
receives and the JSON envelope does **not** count against it. Without that arm
the obvious remedy is to budget `10,000 minus envelope` — needlessly tight, and a
magic number that rots the moment the envelope changes.

**What was exposed here — two hooks, not the four that were reported:**

| hook | bound | |
|---|---|---|
| `session-canary.sh` | **UNBOUNDED** | **36,638 characters** on a 400-item report; crossed at ~81 lost files against the 473 tracked in the two watched repos |
| `x4validate-on-edit.sh` | **UNBOUNDED** | one line per finding; crosses at ~75, real finding lines measured mean 135 chars (min 98, max 166) |
| `check-reference-version.sh` | bounded | one fixed-format line, ~400 chars |
| `search-scope.sh` | bounded | seven fixed literals, only a filename interpolates, ~600 chars |

★ **THE FAILURE WAS INVERTED AGAINST SEVERITY.** One lost file sails under the
cap; a directory-level loss — the case the canary exists for — is the one that
gets filed. And because the preview keeps the **HEAD**, what survived was the
inventory and what was cut was the recovery directive, the single line that stops
the recoverable state being destroyed. **A warning channel whose output LENGTH
SCALES WITH THE SEVERITY it reports fails silently exactly when it matters most.**

⚠ **The reporting project picked its four hooks by CHANNEL when the discriminator
is STRUCTURE.** Bare stdout versus additionalContext decides whether output can
reach the model at all; it says nothing about whether it can reach the cap. Both
real cases are an unbounded loop printing one line per item; both false positives
are fixed-format lines. The two axes are independent and only the second is a
risk.

⚠ **The control plane is NOT affected, and conflating the two produces a
plausible and wrong panic about guard safety.** MEASURED separately, four arms
discriminating on a disk side effect with a no-hook positive control: a deny is
honoured at 500, 15,000 and 200,000 characters of reason. An ask with no
responder **refuses in ~9 seconds** rather than hanging, so the ask-based guards
fail CLOSED in headless.

**FIX.** `x4_bound` in `_x4-env.sh` owns the constant and is applied **once**,
before either renderer — doing it inside jq and again inside the python fallback
would be two implementations of one rule, and the fallback is what runs on a
machine with no jq, which is exactly when nobody is looking. Renderer parity is
now structural rather than something two code paths re-earn. `x4_advise` calls
it, covering both additionalContext hooks for free; `session-canary.sh` writes
bare stdout and calls it explicitly. `x4canary._LIST_CAP = 40` caps all three item
lists and discloses the bound, and the directive prints BEFORE the list in both
layers.

★ **A defect inside the fix, caught by the suite and missed by my own
spot-check.** `x4_head` sliced in python and wrote through **text-mode stdout**,
which on Windows re-translates LF into CRLF — so a payload already carrying CRLF
came back as CR CR LF and the slice **GREW by one character per line after the
bound had been computed**: 9,700 in, 9,807 out, capped result **10,007** against a
10,000 ceiling. My informal check read the file back with text-mode `open()`,
which collapses CRLF on the way in, and reported an honest-looking 9,900. Two
instruments, one wrong, and the wrong one was the informal one I trusted. Read
bytes, write bytes.

**E2E, with a control that went the other way:** bounded (9,900) gives the model
`TRUNCATED`; unbounded (25,007) gives `NEITHER`. The control is what makes the
pass mean anything.

**STILL OPEN:** nothing independently watches the filings the harness makes independently of this
constant, so a wrong value fails silently in the safe-looking direction. The
number has already moved once across a CC bump (high-20s K on 2.1.218, 10,000 on
2.1.246, still 10,000 on 2.1.263); re-deriving it is a named step on a bump, and
the probe shape plus a small-size control arm is recorded at the constant.

## F108 — a temp-file reclaim keyed to the CURRENT process, which can never collect the orphans that matter · **DEFECT (measured)** · confidence 98% · FIXED 2026-09-07

**RE-DERIVED BY:** opening the three orphans in the live registry and counting
rows per table, rather than inferring their state from their size.

`_effective._write_db` creates `<db>.<pid>.tmp` at `sqlite3.connect`, and its
`try:` carried **`finally: con.close()` and nothing else** — closing the HANDLE
and never removing the FILE. Every exception between creation and `os.replace`
leaked a fully materialised temp, permanently. The only cleanup sat in the
`os.replace` OSError handler: the one failure mode somebody had already imagined.

The sharpest case is architectural rather than exotic.
`_mutation.refuse_if_mutating()` is called **inside** that block by design — at
the stamping site, so no other entry path can slip past it — so any store build
overlapping a mutating gate deposits one.

MEASURED in `dev/_registry/`: **three orphans**, 2026-08-26 at 12:50 / 12:52 /
12:54, each 53,248 bytes, each with **zero rows in every table INCLUDING
`meta`**. So they died before `con.commit()`, which is what makes them a leak
rather than a half-written store — build-then-replace is atomic and the installed
store was never at risk. The residue was the only surviving evidence that three
builds died.

★ **The generalisable half is the reclaim, not the missing `finally`.** The
startup sweep is `os.getpid()`-keyed, so it only ever matches a leftover from
THIS process — and the creator of an orphan is by definition gone. **A reclaim
scoped to the current process looks present in review and is unreachable in
fact**, which is why reading the code never found it.

The contrast that sharpens it lives in this same package:
`gates/mutation_probe.py` also records a pid, and restores **BY NAME** from a
pristine directory with the pid printed for a human to read. Same ingredient,
load-bearing in one and decorative in the other. **The defect is never "a pid
appears in the design", it is "the pid is load-bearing in the reclaim".**

**FIX.** `except BaseException:` then unlink then `raise`, copied unchanged from
`_registry.save:513-541` rather than invented. Nested rather than chained, so the
inner `finally` closes the handle BEFORE the unlink — Windows refuses to unlink a
file it still holds open, and that ordering is the entire reason for the nesting.
The original exception is re-raised so cleanup cannot mask the diagnosis.

The PID **stays** in the temp name. `tests/test_effective.py:168` pins it
deliberately, from a measured 3-racer WinError 32 incident where two builds chose
the same temp and both died. A fix that dropped it would read as a simplification
and would silently re-open that race.

**DELIBERATELY NOT DONE:** an automatic sweep of foreign `<db>.*.tmp`. Deciding
whether the temp of a stranger is abandoned means deciding whether its pid is
alive, and on Windows `os.kill(pid, 0)` maps to TerminateProcess — guessing wrong
either kills a running build or overwrites files underneath it.
`mutation_probe.recover()` reached the same conclusion and made its recovery an
explicit step; so does this.

**HOW IT ANNOUNCED ITSELF, which is worth more than the fix.** While this sat
uncommitted, a peer session in another tree hit `gates/qa_sweep.py` FAIL with
*"Refusing to compare the engine against a store that may not describe the
current world"* — `engine=6cb3a6644b1b95ff` on the artifacts against
`f5b290a2e98c333f` live, narrowed by diffing all eight ENGINE_SOURCES against
HEAD to exactly one hit. So the two-axis freshness contract caught an uncommitted
engine change **made by a different session with no coordination at all**. It was
built to catch our own staleness; this is the first recorded instance of it
catching a stranger.

**STILL OPEN:** the three existing orphans are unreclaimed by design — they are
evidence, and removing them is a user decision, not a decision for the tool.

## F109 — the installer parity gate compares the two installers as TEXT, so it cannot see a behavioural divergence in either direction · **SCOPE LIMIT (measured)** · confidence 96% · OPEN 2026-09-07

**What it is.** `tests/test_installers_agree.py` is the file whose name promises that
`install.sh` and `install.ps1` agree. MEASURED 2026-09-07: it is **11 tests carrying 13
substring assertions over the two files read as TEXT** — `assert 'cp "$f" "$f.bak-' in sh`,
`assert "function Test-SameDir" in ps`, `assert '[ "$SRC" != "$TOOLKIT" ]' not in sh`. Two
of the eleven do parse a structure (the copy-item lists, via regex) and genuinely compare
sets; the rest look for strings.

**Why that is a scope limit rather than a style complaint.**

1. **A substring is satisfied by prose.** Delete a mechanism and leave the comment that
   names it, and the assertion still passes. This is CLAUDE.md #37(b), where a guard
   written `assert "FLAG" in text` was satisfied by the comment mentioning FLAG and the
   test stayed green after the mechanism was removed.
2. **The assertions encode a bug history, not a specification.** Every one was written
   after a specific divergence had already been found by other means. So the gate
   re-detects what somebody already detected and is structurally silent on everything
   else — the same shape as the guard fuzzer's mutator list in F104 and CLAUDE.md #36,
   *a bug history is not a specification*.

**The measured denominator.** Six divergences are known on this file pair. Five are named
in the gate's own docstrings and in `install.ps1` (`the fourth 'fixed in bash, absent in
PowerShell' defect of the release`, and the fifth at `test_both_installers_GATE_the_global_destination`);
the ordinals are consistent between the two files, checked rather than assumed. The sixth
was found 2026-09-07 and is the interesting one:

| | direction | how it was found |
|---|---|---|
| 1–5 | bash fixed, PowerShell behind | four by review, one by a behavioural test |
| **6** | **PowerShell correct, bash behind** | a hand-written twin driving `--method global --over-existing` |

In the sixth, `install.ps1` built its `$CLAUDE_PROJECT_DIR` rewrite list from `$srcRoot`
and was never exposed, while `install.sh` ran `grep -rl` over the DESTINATION directory
and rewrote a user's own file, deleting the only backup. **The gate was green throughout
all six.**

★ **The direction is the finding.** Five same-direction instances taught a one-way habit —
*"check whether PowerShell got the bash fix"* — and that habit cannot find the sixth by
construction. An instrument built from a run of same-shaped incidents encodes their shape,
including their polarity. Cf. CLAUDE.md #36's immune case: there, the one seed that did NOT
bypass named the root cause; here, the one divergence that ran the other way named the
limit of the gate.

**What would actually close it.** Compare OUTCOMES, not text: drive both installers over
one fixture and assert on files present, contents, backups left and rc.
`tests/test_install_over_existing.py` already does exactly this for 20 cases through its
`["sh","ps1"]` parametrisation, and it is what caught divergence 6 — so the capability
exists and is proven; what is missing is the routing rule that new parity claims go there
rather than becoming a fourteenth substring.

**Deliberately NOT done here.** Rewriting the eleven existing tests behaviourally is a
larger change than the finding warrants and would be made against a release in close-out.
Recorded so the next parity claim is routed correctly, and so nobody reads a green
`test_installers_agree.py` as evidence that the two installers behave alike — it is
evidence that thirteen known strings are still present.
## F110 — a command name arriving through SUBSTITUTION reached no rule at all, and the fuzzer that should have found it holds the operand constant by design · **DEFECT (measured)** · confidence 98% · FIXED 2026-09-08

**What it is.** MEASURED 2026-09-08 against the live hook, roots configured:

    rm -rf "<game root>"           deny
    $(echo rm) -rf "<game root>"   ALLOW     <- past all three hard blocks
    `echo rm` -rf "<game root>"    ALLOW

`resolve_verb` exists for exactly this shape and cannot reach it: it only ever splices
a BARE command name out of the assignment table, so a substitution is unresolvable by
construction and `verb()` returned the literal token `$(echo`. **An unknown OPERAND
still reaches the conservative branch; an unknown VERB reaches nothing** — which is
what makes this worse than the variable spelling the resolver was written for, and
why it took every verb-keyed rule with it at once.

★ **WHY 2,568 FUZZ MUTANTS COULD NOT FIND IT, and this is the generalisable half.**
The guard fuzzer has 83 mutators across many axes. Its VERB axis has FOUR, and every
one is a SPELLING of a literal name — `.exe`, absolute path, ANSI-C quoting, Windows
path. **None is a SUBSTITUTION.** The mutator list was grown in reaction to past
bugs, which is precisely why the spelling nobody had been burned by was missing. A
bug history is not a specification; a grammar is, and it is enumerable. This is the
same reading as F104 one axis over: read what an instrument holds CONSTANT, because
that is its blind spot stated in its own words.

**The pricing is the other lesson, and it overturned the fix twice.** Over 28,989
real Bash commands from 110 session transcripts:

| candidate rule | corpus hits |
|---|---|
| any unresolved verb token | **679 (2.3%)** — mostly `$JQ` / `$UV` / `$f`, where the variable holds a PATH so `resolve_verb` correctly leaves it alone |
| a substituted verb, any target | 834 (2.9%) |
| **a substituted verb WITH a rooted operand in the SAME segment** | **4 (0.014%)** |

I predicted "under 20" for the first and was wrong by 34x. Shipping the naive rule
would have denied 2.3% of ordinary work, in a guard whose own policy says a deny must
be something the model can simply fix and retry.

⚠ **AND MY FIRST CORPUS MEASUREMENT WAS VACUOUS AND I REPORTED IT AS A RESULT.** I
called `facts(payload)` where the signature is `facts(payload, roots)`; an
`except Exception: continue` swallowed 28,963 identical TypeErrors and printed
`0 of 28,963 (0.000%)`. A clean zero over a run that examined NOTHING — the defect
class this whole register is about, inside the instrument measuring it. Every corpus
figure quoted here comes from a harness that asserts a CONTROL first: the known
bypass must read True before any zero is believed.

**Fixed** by a `verb_unresolved` fact scoped to substitution in the verb position with
a rooted operand in the same segment, denied with a reason naming the remedy (write
the command name literally). Five probes in `test-hooks.sh` plus four in
`test_hook_facts.py` — the coverage harness copies only `hook_facts.py` and
`test_hook_facts.py`, so probes placed anywhere else are invisible to it, which cost
one round.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` — verified 2026-09-12, 35 matches
for `verb_unresolved`/substitution. The entry already named it in prose; the citation
was not in a form `register_rederivation` could resolve, because a bare name is looked
up as `tests/<name>.py`. `test-hooks.sh` is deliberately NOT cited: it lives only in the
game-root hooks directory, which is not a root this gate searches, so naming it would
read as covered while resolving to nothing.

## F111 — one resolution applied ONCE, and three rules that re-derived their own walk from the raw text · **DEFECT (measured)** · confidence 97% · FIXED 2026-09-08

`facts()` applies `resolve_verb` once and builds `seg_cwd` from the result. Three
rules then re-derived their own segment walks from `all_cmds` and never saw a
resolved verb. MEASURED:

    rm  ->  RM=rm;   $RM -rf <ref>     rm_targets_reference       True
    grep->  GP=grep; $GP -rn x <ref>   search_rooted_reference    FALSE
    py  ->  PY=python; $PY -c open(...,"w")  durable_python_open_w  FALSE

All three are DENY rules, so the variable spelling of a rooted search or a durable
write reached nothing while the same spelling of a rooted delete was caught.

★ **Two independent paths answering one question** — the shape this codebase's own
narrowing table exists to refuse, and the reason a shared helper must be the only
door. The arc RECRUITED for it: an in-arc commit rewrote the very
`durable_python_open_w` comprehension (to close 21 wrapper bypasses) and did not adopt
the resolved segments while it was there. A fix that touches the expression and leaves
the population is the specific way this class survives a sweep.

Fixed by routing both walks through `seg_cwd`. Verified per item that the variable
spelling now denies like the plain one and that an unrooted `grep -rn foo .` is
untouched.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` --
`test_F111_a_variable_spelled_verb_denies_like_the_plain_one` asserts the per-item claim
as an EQUIVALENCE across all three rules (rooted search, durable python write, rooted
delete): each fact must fire on the plain spelling AND on the variable one, so the test
cannot stay green if the rule breaks for both. `test_F111_an_unrooted_recursive_search_is_untouched`
is the other clause, and it needs to be separate -- without it a fix that simply returned
True would satisfy the equivalence.
Falsified 2026-09-13 in a COPY, never the working tree: `resolve_verb` -> identity turns
the equivalence test red on its own assertion (*"fired on the plain spelling but NOT on
the variable one"*) with the module still healthy and 4 other resolution tests red;
forcing the search rule always-True turns the unrooted test red and leaves the
equivalence green. `scripts/fuzz-guard.py` still covers the mechanism and is no longer
the only thing that does.

## F112 — a write rule that filtered redirects to TRUNCATE, so an APPEND into the read-only tree was allowed · **DEFECT (measured)** · confidence 97% · FIXED 2026-09-08

`writes_reference` was built from `trunc_redirect`, which keeps only redirects whose
mode is `truncate`. MEASURED with roots configured:

    echo x >  <ref>/libraries/wares.xml    deny
    echo x >> <ref>/libraries/wares.xml    ALLOW    <- same primitive, same file
    printf a | tee -a <ref>/w.xml          deny     <- the OTHER spelling of an append

so the two spellings of one operation disagreed, under a rule whose own message is
"never write into it".

★ **The tell is a neighbour with a WIDER channel on a LESS protected tree.**
`writes_documents`, three lines away, uses `writes_any` — which includes appends. So
the hard-blocked read-only tree had the narrower channel and the merely-advisory one
the wider. Truncate-only is CORRECT where it came from (the game/profile advisory,
whose stated reason is that an append cannot truncate) and was carried to a tree whose
policy is different. **A predicate copied between rules brings its old rationale with
it, and the rationale is what needs re-deriving, not the code.**

Corpus: 0 of 29,001 real commands affected, so closing it cost nothing — which is
exactly the state in which a hole survives, since nothing makes it visible.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` --
`test_F112_an_APPEND_into_the_read_only_tree_is_denied` asserts `>>` and `tee -a` into
the read-only tree both deny, and keeps the truncating form beside them as a CONTROL so
the append result cannot pass on a rule that has stopped firing entirely.
`test_F112_the_advisory_tree_keeps_the_wider_channel` is the twin the entry asked for:
it pins the ASYMMETRY, asserting the advisory documents tree still sees an append -- so
nobody closes the gap from the wrong end by narrowing the wider channel to match.
Falsified 2026-09-13 in a COPY: rebuilding `writes_reference` from `trunc_redirect`
(this entry's original defect, planted verbatim) turns the append test red and **0**
other tests, which is the cleanest isolation in the round; narrowing `writes_documents`
the same way turns the twin red and 1 other. The 14 `append|>>` matches previously
rejected here remain correctly rejected -- they are generic redirect handling, and these
two tests are what actually name the claim.

## F113 — a prune list whose entries carry a DESTRUCTIVE second meaning, and the entry added to fix a leak destroyed the recovery store · **DEFECT (measured)** · confidence 99% · FIXED 2026-09-08

`X4_COPY_PRUNE` is dual-purpose by construction: every entry is skipped on the way IN
and `rm -rf`'d from the DESTINATION on the way out, twice per run. v3.0.0's list held
three build artifacts, for which that is right. An in-arc fix added `.claude/backups`
to stop the SOURCE machine's 217 backup files travelling — a real defect, correctly
diagnosed — and the path silently inherited the second meaning.

MEASURED: an upgrade with `--over-existing` erased the destination's entire recovery
store, rc 0, "install complete", with the word "backup" appearing nowhere in the
output. Both installers, both methods, reproduced independently by a second session
with a control and a twin. On the live install that was **982 files, 60 MB, 33 named
known-good snapshots reaching back to 2026-06-22**.

★ Three things made it invisible rather than merely wrong, and each is reusable:
- **The file states the hazard directly above the offending entry** — *"a pruned path
  is also DELETED from the destination, which is right for a stale .venv and
  catastrophic for a config"*. Prose adjacent to a defect is not a guard.
- `x4lock` deliberately leaves that directory UNLOCKED, so the lock precheck — the
  one mechanism that would have refused — could not see it.
- The `--over-existing` warning enumerates TOOLKIT files, so the store never appeared
  in the list of what was at risk. The reader's model is *replaced*; the behaviour was
  *erased*.

Fixed by moving the path to `X4_KEEP_LOCAL`, whose documented semantics were already
exactly right. **The category existed; the path was in the wrong one** — no new
mechanism was needed, which is the usual shape once the two lists are read as policies
rather than as string sets.

⚠ The test that pins it asserts SUBJECT and TRAVEL as ONE EXACT SET (the destination's
own store survives AND the source's files do not arrive), because the obvious repair
satisfies one by breaking the other — plus a CONTROL that a real build artifact is
still pruned, without which the test passes for an installer that prunes nothing.

**RE-DERIVED BY:** `tests/test_install_over_existing.py` — specifically
`test_the_upgrade_KEEPS_the_recovery_store_and_still_PRUNES_build_artifacts`, whose name
states both halves of the paragraph above (store survives AND artifacts still pruned).
Located 2026-09-12 by matching the description to the test name; the entry described the
test precisely and simply never named it.


## F114 — a verdict line with no floor under it, in five gates at once (confidence: MEASURED)

**One shape, five places, every one reporting success over a population it never examined.**
Found while closing the v3.1.0 Track 1 review; none was found by reading output.

| gate | the verdict it printed | over what |
|---|---|---|
| `tool_properties` | `All properties hold.` rc 0 | three checks that called `note(True, ..., "SKIPPED")`, which prints `ok` and never reaches `failures` |
| `perf_guard` | `No per-mod regression beyond tolerance.` rc 0 | an EMPTY `set(base) & set(curr)` — zero comparisons. Its `--record` mirror wrote an empty baseline at rc 0, which then made every later run empty too |
| `generate-baseline.sh` | a header-only TSV, rc 1, no message | one EMPTY mod folder aborted the loop under `set -euo pipefail`; the real mod sorted after it and was never recorded |
| `bin/unpack-reference.sh` | the `.unpacked-and-locked` sentinel | an extraction that produced NOTHING. The check at the top of the same script then refuses every later attempt, so the tool that broke the tree will not let you repair it |
| `consistency_audit` | `store vs build_effective vs dump` | TWO channels. `checked` increments BEFORE the dump call, so it cannot drop when the dump channel dies |

**The generalisation is the useful part: a count that is incremented before the step it is supposed to measure cannot report that step failing.** That is literally true of
`consistency_audit`'s `checked`, and structurally true of all five.

**MEASURED, after the fix, per gate:** `tool_properties` rc 3 naming the stale store
(a real condition the old gate printed `ok` over); `perf_guard` rc 2 on an empty
intersection with rc 0/rc 1 twins intact; `generate-baseline` rc 0 with both mods
recorded (`empty_mod 0/0/e3b0c442`, `normal_mod 1/17`); `unpack-reference` rc 2 and NO
sentinel over an empty tree, floor set at 1000 against a real 510,711-file unpack;
`consistency_audit` printing `dump cross-checked : 36` — a number that did not exist.

⚠ **A sixth instance was in MY OWN commit harness and it shipped twice before I saw
it.** The red-then-green script RECORDED the red result and never GATED on it, so two
structural tests that survived their mutation were committed under a log line reading
`RED -> 1 passed`. `scratchpad/redgreen.sh` now refuses on three axes: the plant's
anchor must appear exactly once, GREEN must pass unmutated, and RED must genuinely
FAIL. The tooling that finds this class had the class in it.

## F115 — two checkouts FORKED, and the fork was inside the module whose job is detecting drift · **DEFECT (measured)** · confidence 97% · RESOLVED 2026-09-12 (by switching trees, NOT by reconciling)

**What it is.** The dev checkout could never read the effective store as fresh. MEASURED
2026-09-12 by folding the store's OWN recorded detail vector with each tree's code:

    store meta fingerprint_content = 0cd79c957de67d9e
    dev    _fold(stored detail)    = 87f1f21dff460d34   ENGINE_SOURCES=7
    mirror _fold(stored detail)    = 0cd79c957de67d9e   ENGINE_SOURCES=8   <== MATCHES

The store was built by MIRROR code, so `x4live oracle` refused **rc 3 permanently** from
dev. It returns **rc 0, 103/103 agreeing, from the mirror with no port and no rebuild** —
the oracle was never broken, only unreachable from one of two trees.

★ **THE BANNER NAMED FIVE CAUSES AND NONE WAS THE CAUSE.** It says *"a mod was added,
removed, updated, toggled, or one of its files was edited"* — while **133 of 133
manifests were byte-identical on disk** and `x4modlist changed` independently reported
*"no change: every installed mod matches the baseline, file-for-file"*. Two instruments
over one axis disagreed, and the freshness contract has **no vocabulary for "you are
standing in a different tree than the one that built this artifact"**. That is the
instrument defect; the fork merely exposed it.

★★ **THE FIX I FIRST PLANNED WAS DISPROVEN AS SAFE, AND THAT IS THE TRANSFERABLE HALF.**
Direction was established without a merge base (the repos have DISJOINT histories) by
asking whether each repo had ever CONTAINED the other's current bytes: all 21 forked
modules existed in the mirror's object DB, none of the mirror's in dev's, confirmed as
real ancestry at the same paths. So dev was behind. **But ancestry proves DIRECTION, not
BEHAVIOUR.** MEASURED: **27 of 104 incoming Python files (26%) resolve a path ABOVE the
package root** (`parents[3]`, `.parent.parent.parent`). In the mirror that is the wrapper
repo root; in dev it is `<modding root>`, **which is not a git repository**. Porting
the mirror's `gates/control_bytes.py` into dev would have run `git ls-files` with
`cwd=ROOT` against a non-repo — **reducing the control-byte sweep to zero files while
still exiting 0.** A sweep over nothing reporting clean is precisely the defect that gate
exists to prevent, and my reconciliation would have introduced it.

⚠ **The coupling runs BOTH ways**, so "the mirror is newer" was never the whole answer:
16 mirror tests need the wrapper root, and 3 (`test_livecli`, `test_modlua_rearm`,
`test_recon_buckets_are_vanilla_attested`) need `parents[3]/dev` and are meaningful only
from dev. They skip honestly rather than passing vacuously.

**Resolved by switching trees, not merging them.** `X4_TOOLKIT` repointed at the mirror.
No file was edited and **`x4lock` was not bypassed**: both `x4-paths.env` copies and all
five path-carrying `SKILL.md` files are locked, and `_paths._layers()` is
`[env, file_layer, _LOCAL_FALLBACK]`, so a real env var outranks the locked config.
`X4_ORACLE_LOG` was carried over — it exists only in dev's config and three oracle gates
read it, so it would have gone silently missing.

⚠ **What this does NOT fix.** The 26% layout coupling is routed around, not closed. If
dev is ever used for toolkit work again the trap returns. And `_freshness.py` is still
absent from its own `ENGINE_SOURCES`, so a change to the freshness code remains invisible
to the freshness contract — deferred deliberately, because adding it is self-referential
and the first build afterwards invalidates every artifact in BOTH trees, including the
store used as the control here.

**RE-DERIVED BY:** `gates/tool_properties.py` — it compares the derived mod SETS between
the store and `x4eff`, which is the existing cross-artifact agreement check.
⚠ STATED PLAINLY: that is **adjacent, not exact**. Nothing re-derives *this* defect,
because the discriminating measurement needs TWO trees present and the gate runs in one.
The honest close would be a check comparing the running tree's `_fold` against the
`fingerprint_content` the artifact records — a one-line assertion that would have caught
this on the first run from either tree, and which does not exist.

**Verified at resolution:** oracle rc 0 (103/103) · `gates/oracle.py` 241/241, 0 FALSE OK
· old-vs-new validator **byte-identical on 8 of 8 real mods** (full report bodies, after
a first comparison whose summary-line regex read clean runs as failures) · mirror suite
1705 passed / 2 failed / 14 skipped, the 2 being `test_control_bytes.py` refusing over
two tracked-but-deleted `release/nexus/v3.1.1/` files — unrelated, proven by a three-arm
env control.

⚠ **CORRECTION 2026-09-13, and it is a lesson about the reader rather than the tool.** Two
sessions characterised those deletions as "someone's in-flight release work" and traced
commit history looking for an owner. **They are the USER's, deliberate, and routine: the
Nexus files are deleted locally every release once they have been posted.** Nothing was
in flight and there was no owner to find. The tool was right, the diagnosis of the
mechanism was right, and the STORY attached to it was invented to explain a state neither
session had asked anyone about — one question would have settled it.

**The durable consequence is not the mistake, it is that this recurs EVERY RELEASE.** A
deliberate, repeated user action puts the suite at 2 failed indefinitely, and a
permanently-red suite is how a NEW red goes unnoticed — the same defeat as a check that
cannot fail, arriving from the other side. Restoring the files is not the fix, because
they will be deleted again next release. The real options are: untrack them (they are
generated artifacts posted elsewhere), commit the deletion as part of the release
routine, or have `control_bytes` distinguish a tracked-but-absent file from an unreadable
one. **Not chosen here** — it is the release lane's call, and it is recorded so the next
session does not re-diagnose it as a mystery for a third time.

✅ **DECIDED 2026-09-13 (user): untrack them.** The Nexus packs are the user's local
artifact, not code. Mirror `759f11e` ran `git rm -r --cached release/nexus` (14 files;
the 12 still on disk stay as ignored files), added `release/nexus/` to `.gitignore`, and
added `tools/x4validate/tests/test_nexus_packs_are_never_tracked.py`, which fails if the
rule is missing, if git does not actually ignore a path under it, or if any pack file is
tracked. MEASURED on that commit: `test_control_bytes.py` 2 failed -> green, and the
`control_bytes` gate rc 2 -> rc 0.

## F116 — a PAUSED game "goes silent" shipped as fact, and it was an inference · **DEFECT (measured)** · confidence 95% · FIXED 2026-09-13

**RE-DERIVED BY:** `tests/test_livepipe.py` (`test_the_hint_does_not_blame_a_PAUSED_game`), which fails if the claim returns.

**What shipped.** `_livepipe.py`'s connection hint told a user whose channel had gone silent that
*"a PAUSED game goes silent"*, and to unpause. The refusal text, an outcome table, docstrings, two lua
comments and three test docstrings repeated it, and the lua `ping` comment said equal elapsed times
mean paused.

**What was measured** (live, 2026-09-13, mod build `39162ddc`, by the session that built the write
verbs; READ here from its commits `da02962` and `b241a9f`, not re-run): a paused game kept answering
on an open connection AND accepted a new one -- `pause`, `pausestate`, `unpause`, `pausestate` as four
separate processes agreed 4 of 4 -- and `getElapsedTime()` advanced 76.45 -> 84.81 over 8.35 s of
mostly-paused wall clock, so it is real time, not game time.

**Where the inference came from.** The api's MD heartbeat fires only on unpaused frames; the UI
`onUpdate` half of the channel keeps polling. One half of a two-half channel was read as the whole
channel -- the adjacent-question shape (#22b): the instrument consulted exists, answers, and is not the
one that decides. It was an INSTRUCTION to users, not a comment: a silent channel sent people to unpause
a game that was never the cause.

## F117 — a paging test that could not go red, and a mutation harness whose anchors had moved · **DEFECT (measured)** · confidence 90% · FIXED 2026-09-13

**RE-DERIVED BY:** `tests/test_modlua_rearm.py` (`test_globals_never_exceeds_the_payload_cap`), and the
maintained lua mutation harness `scripts/mutate-mod-lua.py` in this package (excluded from the release
bundle).

**(a) The test** asserted only that each `globals` page is at most 32,000 bytes. `reply()`'s shared cap
turns an oversized reply into a short `ERR`, which also fits, so a `globals` that stopped paging passed.
MEASURED 2026-09-13 by the write-verbs session (READ here from commit `2d40095`): the lua mutant
*"globals stops paging and emits one oversized reply"* SURVIVED, on pristine `5f128b7` as well as on its
branch. The test now also requires status `OK` on both pages.

**(b) The harness.** The dev repository's copy of `mutate-mod-lua.py` anchored mutants on text the mod no
longer contains (reported: `galaxyprobe` 0 matches, `compare` 2 where one is required). Reported by that
session, NOT re-measured here; the copy retired with the dev repository on 2026-09-13.

Both halves are #26. A size cap plus a shared error path let the failure fit inside the assertion, and a
mutant whose anchor matches nothing cannot plant the defect it names.

## F118 — a fresh Windows clone checks the shipped mod's XML out as CRLF, so a byte-comparing deploy sees changes nobody made · **DEFECT (measured)** · confidence 95% · FIXED 2026-09-14

**RE-DERIVED BY:** `tests/test_line_ending_pin_is_obeyed.py` (`test_every_file_pinned_to_LF_actually_holds_LF_on_disk`).

`.gitattributes` pins `mods/**/*.lua` to LF (the F53 reasoning) but not the mod's XML. Under
`core.autocrlf=true` with `* text=auto`, MEASURED 2026-09-13 on the author's main checkout:
`git ls-files --eol mods/` gives `i/lf w/crlf` for `mods/x4_toolkit_helper/content.xml` (1 of its 2 XML
files; the other is LF on disk because a tool rewrote it). `scripts/deploy-mod.py` decides CHANGED by the
sha256 of raw bytes (READ: its `changed = ... sha(src / f) != sha(dst / f)`), so an LF deployed copy and a
CRLF checkout of identical content compare unequal.

The consequence -- a deploy listing CHANGED files nobody edited -- was reported by the write-verbs
session and is consistent with the code as READ; it is not reproduced here. Two remedies, neither taken
yet: pin `mods/**/*.xml` as the lua is, or compare content with line endings normalised. **OPEN.**

**CORRECTED 2026-09-14 (the cause):** this repository's `.git/config` sets `core.autocrlf=false`, which
overrides the system gitconfig's `true`. The CRLF comes from `* text=auto` checking out with Windows'
native line ending, not from autocrlf. MEASURED: a fresh `git worktree add` gave `w/crlf` for BOTH
`content.xml` and `ui.xml`, so "1 of its 2" above was the author's checkout, not the rule.

**FIXED 2026-09-14** (`.gitattributes`, pin `mods/**/*.xml text eol=lf`): the first remedy, chosen
because the blobs were already LF (no repository change) and because the existing F67 guard then
enforces it with no new code. Normalising inside `deploy-mod.py` would have hidden the checkout defect
instead of removing it. With the pin added the guard went RED on both files (`content.xml` 12 CRLF,
`ui.xml` 30), and GREEN after rewriting them with their committed bytes (no diff). The deploy dry run
after the fix: 0 CHANGED / 4 UNCHANGED. The BEFORE figure is scoped by tree, because an independent
review caught the first version of this paragraph comparing two different trees: MEASURED 2026-09-14 in
a fresh worktree at `aee0afc` (pre-pin), 2 CHANGED (`content.xml`, `ui.xml`); the author's main
checkout had shown 1, because only `content.xml` was CRLF there. ⚠ Any existing Windows checkout still
holding the CRLF bytes now fails that guard until the files are checked out again -- the F67
precedent, stated in the commit and the CHANGELOG.

⚠ **The release bundle was affected too, and a prediction here was WRONG.** I predicted `git archive`
ships committed LF blobs regardless of platform. MEASURED 2026-09-14 on this Windows machine: `git
archive aee0afc` emits `content.xml` with 12 CRLF and `ui.xml` with 30 -- `text=auto` applies the native
ending in an archive as it does in a checkout -- and `git archive` of the pinned commit emits 0 and 0.
So a bundle built on Windows before the pin shipped CRLF manifests to users; the blobs never changed.
X4 reads either ending, so the effect was byte comparisons, not the game (INFERRED: no loader
complaint has been recorded).

## F119 — `x4lock` run from a git worktree calls an untracked config file MISSING · **DEFECT (measured)** · confidence 97% · FIXED 2026-09-14

**RE-DERIVED BY:** `tests/test_x4lock.py` (`test_a_linked_WORKTREE_does_not_demand_its_own_x4_paths_env`, `test_the_MAIN_checkout_still_reports_a_deleted_x4_paths_env`, `test_a_SUBMODULE_is_not_a_worktree_and_still_demands_its_env`, `test_a_SUBMODULE_under_a_folder_named_worktrees_is_not_waived`, `test_a_worktree_env_file_that_DOES_exist_is_still_protected`, `test_a_worktree_demands_the_MAIN_checkouts_env_even_without_X4_TOOLKIT`, `test_a_worktree_still_demands_the_CONFIGURED_toolkits_env`, `test_a_RELATIVE_gitdir_is_resolved_against_the_checkout`, `test_an_UNRECOGNISED_git_file_fails_closed`, `test_an_UNREADABLE_git_file_fails_closed`, `test_the_note_NAMES_the_copy_checked_instead`, `test_the_note_names_each_copy_ONCE`, `test_X4_TOOLKIT_pointing_at_the_worktree_itself_still_reports_its_config`, `test_no_waiver_when_NOTHING_can_stand_in_for_the_config`, `test_a_CRLF_commondir_is_read`, `test_an_ABSOLUTE_commondir_is_used_as_is`, `test_a_GARBLED_commondir_fails_closed_without_raising`).

MEASURED 2026-09-13 from a session worktree: `python scripts/x4lock.py status` exits **1** with
`*** 1 PROTECTED FILE(S) MISSING` naming `<worktree>/.claude/x4-paths.env`, above a summary of
`39 protected file(s): 39 locked`. The same command in the main checkout exits **0**.

`.claude/x4-paths.env` is gitignored machine configuration, so a worktree never has one. The manifest is
keyed to the checkout the command runs from, not to the configured toolkit whose file it means. And a
worktree is the isolation CLAUDE.md MANDATES for concurrent sessions, so every session following that rule
sees a red `x4lock` -- the kind of standing alarm that teaches a reader to ignore the tool. Reported by the
write-verbs session; re-measured here. **OPEN.**

**FIXED 2026-09-14** (`scripts/x4lock.py`). A checkout counts as a LINKED worktree only when its `.git`
is a FILE whose `gitdir:` (absolute, or relative to the checkout) names an admin directory holding a
`commondir` file. MEASURED 2026-09-14: git writes `../..` there in this repository's two worktrees,
and a submodule added with `git submodule add` at `vendor/worktrees/toolkit` got a
`.git/modules/vendor/worktrees/toolkit` directory with NO `commondir` (a throwaway repository pair);
anything unreadable, undecodable or unrecognised fails closed.
There, an ABSENT local env file is waived and `status` says so; a present one stays protected.

Dropping the candidate outright was not an option: in the main checkout it is the only report of a
deleted config. `_paths._find_env_file` cannot stand in, because it returns only a file that EXISTS --
from `$X4_TOOLKIT`, else by walking up from the working directory -- so a deleted copy is simply not
found. The waiver therefore demands the copies it stands in for: the MAIN checkout's, derived from
`commondir` only when the common git directory is named `.git`, and `$X4_TOOLKIT`'s when set. The note
names each once, and says so when `$X4_TOOLKIT` points at the worktree itself.

**No waiver with nothing in its place.** A common directory not named `.git` derives no checkout: a bare
repository, a `--separate-git-dir` repository, and a linked worktree OF A SUBMODULE (MEASURED on the
throwaway pair: its `commondir` resolves to `.git/modules/vendor/worktrees/toolkit`). A second review
found the first fix still waived the local config there with `$X4_TOOLKIT` unset, demanding nothing and
exiting 0. Now the waiver applies only when at least one replacement exists; otherwise the local file is
demanded as before. Still over-strict, in the visible direction: a bare clone kept at `project/.git`, or
a main repository whose `core.worktree` points elsewhere, is asked for a config at the derived path (rc
1) that may not be where its user keeps it.

**Review, then fixes.** The first version (`55be451`) passed its own six tests and seven hand-mutants,
and an independent review still returned "with fixes": it demanded a configured copy only when
`$X4_TOOLKIT` was set, waived a submodule added under a folder named `worktrees`, listed a copy twice,
and its "unreadable" test actually tested an unrecognised file. Fixed in the next commit with seven more
tests, RED first as predicted (5 failures, then 1). Ten mutants run against that code, each aimed
at a named test, all KILLED with the file restored byte-identical: a missing `commondir` counted as a
worktree; a relative gitdir not joined; an unreadable `.git` raising; the main checkout's env not
derived; `$X4_TOOLKIT`'s env not demanded; the waiver never applying; the waiver dropping an existing
env; the note listing a copy twice; the self-reference never flagged; the note naming nothing. A second review then
found the no-replacement and undecodable-`commondir` cases (fixed as described above, 4 more tests,
RED first), and three more mutants ran against the final code, all KILLED: an undecodable `commondir`
raising, the waiver applying with nothing in its place, and a main checkout derived from any common
directory name. Not every
clause has a mutant (for example `startswith("gitdir:")` and the bare-repository branch), so this is ten
named mutants, not full clause coverage. Live: the worktree went rc 1 -> rc 0, with `$X4_TOOLKIT` set and
unset, 39 of 39 locked.

## F120 — the CLAUDE.md ratchet counted a checkout's line endings as content · **DEFECT (measured)** · confidence 99% · FIXED 2026-09-13

**RE-DERIVED BY:** `tests/test_claude_md_budget.py` (`test_char_count_is_the_same_for_an_LF_and_a_CRLF_checkout`, `test_TWIN_each_newline_still_counts_once_and_real_growth_shows_in_either_ending`).

`gates/claude_md_budget.py` reported `GREW: shipped (repo root) 71897 -> 72769 (+872 chars)` on a file
nobody had grown. Re-derived per item, never from the verdict:

| the shipped CLAUDE.md | chars | CRLF pairs | chars minus CR |
|---|---|---|---|
| committed blob at `c97185b` | 71,845 | 0 | 71,845 |
| the main checkout on disk (rewritten 21:37 by a fast-forward) | 72,769 | 924 | 71,845 |
| the recorded baseline | 71,897 | | |

The committed content was **52 characters SMALLER** than its baseline. The gate counted raw `CR`s --
deliberately, to avoid `read_text()`'s one-character-per-line loss when a CRLF baseline meets a
normalised measurement. That fixed the mixing half and created the checkout half. Now every CRLF counts
as one newline, whatever the checkout wrote; one twin pins that a newline still counts and that one
added character is one in either ending, and each clause has a mutant that fails its named test.

## F121 — `who-sets` with no property narrowed to the entity's origin, and removals are not properties · **DEFECT (measured)** · confidence 97% · FIXED 2026-09-14

**RE-DERIVED BY:** `tests/test_effective.py` (`test_who_sets_without_a_prop_names_the_origins_of_changed_properties`, `test_TWIN_who_sets_without_a_prop_says_so_when_nothing_changed`, `test_who_sets_does_not_call_an_entity_all_base_when_a_mod_REMOVED_part_of_it`).

**Found by a cold, docs-only agent** (2026-09-14), asked who set the ware `energycells`.
`x4effective who-sets ware energycells` printed `energycells (entity): base` and nothing else --
the ENTITY row's origin -- while `show` revealed a mod setting `price.min` and `price.max`. MEASURED
on the real store: **33 of the ware's 49 properties** were last set by something other than base.
A step that narrows the answer and reports it as the whole answer: this register's founding shape.

**Two assumptions were checked before the fix, and one was wrong.**

| assumption | measured over the real store (591,548 attribute rows) | verdict |
|---|---|---|
| a non-empty provenance chain means a MOD set the value | 123,414 of the 296,608 non-base rows (42%) were last set by a **DLC** | WRONG -- the output names ORIGINS, never "a mod" |
| `origin <> 'base'` and `chain IS NOT NULL` pick the same rows | 294,940 base rows with a NULL chain + 296,608 non-base with a chain = all rows, 0 mismatches | right |

**The review then found a third blind spot, in the fix.** A removal is written to the `removed`
table, not `attrs`, so a property count cannot see it: an entity whose only change was a mod
removing an attribute printed "every property is still its base value". Removals are keyed by vpath
and a positional node path, so they attach to one entity only when the file holds one -- MEASURED:
108 vpaths carry removals; 98 hold one entity, 9 hold several (shared libraries such as
`libraries/wares.xml`), 1 holds none. The output now says which case applies, and states an all-base
entity as "every stored property is its base value, and no removal is recorded against its file".

**OPEN:** `x4effective show` has the same removal gap -- it lists stored properties only. It predates
this fix and is named here rather than left implicit.

## F122 — a verified negative over a query nobody typed: Git Bash rewrites `//` in arguments · **DEFECT (measured)** · confidence 98% · FIXED 2026-09-14

**RE-DERIVED BY:** `tools/basex/test_ask.py` (`test_a_ZERO_from_an_argv_xq_under_Git_Bash_is_NOT_a_negative`,
`test_a_POSITIVE_argv_xq_under_Git_Bash_shows_the_query_AS_RECEIVED`, their twins, and the usage and
unreadable-file tests).

> 2026-09-14: this entry first had to use the register's opt-out, because `register_rederivation`
> resolved citations only under `tests/`, `gates/`, `scripts/` and `.claude/` -- a gap in the gate, not
> in the evidence. The gate now also resolves `tools/basex/test_*.py`, so the citation above is checked
> like any other.

**What happened.** A cold, docs-only agent followed `QUERIES.md`'s own example from Git Bash:
`ask.py xq 'collection("x4raw")//ware'` printed `0 items` and `NEGATIVE CONFIRMED over 14054 of 14065
documents`, exit 0. The same count from PowerShell was **14,068**.

**Root cause, MEASURED by printing what Python received:**

| typed in Git Bash | Python's `sys.argv[1]` |
|---|---|
| `'count(collection("x4raw")//ware)'` | `count(collection("x4raw")/ware)` |
| `"count(collection('x4raw')//ware)"` | unchanged |
| `'//ware'` | `/ware` |
| the first form with `MSYS_NO_PATHCONV=1` | unchanged |

MSYS path conversion rewrites the ARGUMENT when Git Bash starts a native Windows program -- before
any toolkit code runs, so nothing inside `ask.py` can see what was typed. Every guard then did its job
correctly over the wrong query: coverage was real, the index was fresh, the zero was genuine for
`/ware`. **The instrument was right and the channel lied** -- the argv sibling of F91, whose entry had
already noted in passing that "argv is not a safe channel either" and was never applied here.

**The fix names the channel instead of guessing the query.** Git Bash exports `MSYSTEM` to native
children and a PowerShell started on its own does not (measured both; one started FROM Git Bash
inherits it and refuses too, the safe direction). `xq` takes `--file`, which is not rewritten; an
argument query run under `MSYSTEM` is refused as a negative (rc 4, the "cannot back a negative" code)
and shows the query as received on any result, because a rewritten query can also return a wrong
non-zero. `refs` and `attr` build their query in Python from an id or attribute name and are
unaffected. Every clause has a twin and a mutant that fails its named test (7 of 7 killed).

**A second review found three more, fixed the same way.** The conversion is wider than `//`: a
leading `/ware` arrives as `C:/Program Files/Git/ware`, so the output and docs now say "path-like
parts" and the guard keys on the channel, never on a pattern. An empty or comment-only query --
a truncated or unsaved `--file` -- still certified a negative, the same shape through a new door:
it now refuses (exit 2). And with `MSYS_NO_PATHCONV` or `MSYS2_ARG_CONV_EXCL=*` set the conversion
is off (measured), so the refusal is too; a query file's UTF-8 BOM is dropped rather than sent to
BaseX, which misreported it as a context error.

## F123 — `unpack-reference.sh` recorded a beta branch's build: the Steam manifest has more than one `"buildid"` · **DEFECT (measured)** · confidence 97% · FIXED 2026-09-14

**RE-DERIVED BY:** `tests/test_acf_buildid_has_one_parser.py` (`test_the_helper_exists_and_is_the_only_acf_reader`, `test_both_known_callers_use_the_helper`) and four `scripts/test-hooks.sh` probes under `=== check-reference-version.sh ===` (the installed build from a real-shaped manifest, a beta branch listed ABOVE the installed build, the matching-builds twin, a CRLF manifest).

**What happened.** An x4live scoping document listed "`reference/` is build 23524486; game is
23660954" as an open item needing a ~27 GB re-unpack, which would have meant removing the lock
sentinel by hand. MEASURED 2026-09-14 on the reference machine, the tree was never stale:

| source | build |
|---|---|
| `appmanifest_392160.acf`, `"buildid"` directly under `AppState` (the installed build) | 23660954 |
| `reference/.unpacked-and-locked` | 23660954 |
| the detached `.reference-buildid` beside `reference/` | 23660954 |
| `appmanifest_392160.acf`, `PrivateDepots/branches/public_beta/buildid` | **23524486** |

The user's branch is `public` (`UserConfig/BetaKey`), so a manifest lists beta branches whether
or not you are on one.

**Root cause, READ and then MEASURED.** Two hand-rolled greps read that file and disagreed.
`bin/unpack-reference.sh:116` took the LAST match (`grep -i '"buildid"' | grep -oE '[0-9]+' |
tail -1`); run over this manifest it returns 23524486. `check-reference-version.sh:46` took the
FIRST, which is right only while Steam writes AppState's key above the branches. The hook suite's
fixture manifest held ONE `buildid`, so neither shape could go red. The last-match read has been
there since v2.0 (`a18ab2e`); since v3.1.0 (`a1e9a19`) it also writes the build into the
sentinel, which the hook trusts first -- so the next re-unpack would have made the hook announce a
stale `reference/` at every session start and recommend another re-unpack.

⚠ INFERRED, not re-derived: F103's 2026-09-06 finding that a detached `.reference-buildid` said
23524486 while everything else said 23660954 is exactly this script's output from a v2.x-era
unpack. F103 fixed which file the hook believes; it did not find what wrote the wrong number.

**The fix is one reader.** `x4_acf_buildid` in `.claude/hooks/_x4-env.sh` takes the `"buildid"`
at brace depth 1, whatever the key order, and returns nothing (rc 1) when there is none. The script
and the hook both call it, and the structural test refuses a third hand-rolled grep over the
quoted key (the sentinel's unquoted read is a different file and is untouched).

**Checking the checker, twice.**

1. The first mutant run reported 4 of 5 SURVIVED with no FAIL lines at all. Python's
   `subprocess.run(["bash", ...])` on Windows resolves through CreateProcess's search order, which
   reaches System32's WSL relay before `PATH` -- `execvpe(/bin/bash) failed` -- so the suite never
   ran, while `shutil.which("bash")` named Git Bash. The harness now uses the resolved path and
   refuses a run that never reaches the suite's `RESULT:` line.
2. The CR-strip mutant still survived. MEASURED: Git Bash's gawk strips CR on input in text mode
   (a `{\r` line reads length 1, and 2 with `-v BINMODE=3`), while Linux and macOS awk keep it --
   so the clause was exercisable on one platform only. The helper now runs awk with `BINMODE=3`
   (ignored by other awks), and all **5 of 5** mutants fail their named check: depth clause, key
   clause, CR strip, hook reverted to first-match, script reverted to last-match.

---

## F124 — a client pipe request had no size cap: an over-long request tears the channel down, and a game crash followed · **DEFECT (measured)** · confidence 90% · FIXED 2026-09-14

**RE-DERIVED BY:** `tests/test_livepipe.py` (`test_ask_REFUSES_a_request_over_the_cap_BEFORE_writing` — asserts the write count is zero, so the refusal precedes `WriteFile`; `test_ask_STILL_SENDS_a_request_under_the_cap`, the over-cap twin; `test_the_request_cap_sits_below_the_MEASURED_teardown_floor`, pinning the value below 1997) and `tests/test_livecli.py` (`test_ffi_census_is_DISABLED_by_default_and_does_no_work`, `test_query_REFUSES_ffisyms_when_the_gate_is_unset`, and the twin `test_query_still_answers_a_NON_ffi_verb_when_the_gate_is_unset`).

**What happened (MEASURED).** During a live-channel session (P6, 2026-09-14) a request-size ramp
was driven against the running game. `ffisyms` requests grew with the number of names:

| request (frame bytes) | result |
|---|---|
| 993 | OK |
| 1997 | OK |
| 3998 | **`LiveQueryDegraded: read failed: (109, 'ReadFile', 'The pipe has been ended.')`** |

So the teardown floor is in **(1997, 3998]**. This is the send (game-READ) path, not the reply
path: `named_pipes/pipes.lua:698` hands an over-long read to `error()`, which reaches `Close_Pipe`
and destroys the connection, ERRORing every pending read and write. The mod then re-arms — the
`debug.txt` sentinel shows `arm destroyed, re-arming` repeating up to the crash — and **the game
crashed the moment the user next pressed Esc** to open the map/options menu.

**The crash, from the minidump (MEASURED where; INFERRED why).** `Dr. MinGW`'s dump
(`X4_900_2026_09_14_21_47_34.dmp`) was parsed offline:

- `EXCEPTION_ACCESS_VIOLATION` (0xc0000005), a **read of address `0x30`** — a NULL-pointer
  dereference (`cmp qword ptr [rbp+0x30], rbx` with `rbp = 0`).
- **Inside X4.exe's own code, NOT `lua51_64.dll`, NOT the winpipe DLL, and NOT the FFI-restriction
  check.** The faulting function and its callers are X4's UI event-registration/dispatch engine
  (identified by its own strings: `self`, `__scriptIndex`, `Auto event ... registration is not
  allowed`, `Attempt to unregister an unregistered event`, `Script index not found`), reached
  through `lua_pcall`. The two `0xaf72xx` FFI-restriction addresses on the stack are stale deep-
  stack bytes, not live frames.

⚠ **INFERRED, not proven (this is why confidence is 90%, and the crash half lower):** that our
continuously-armed pipe read + delayed-callback re-arm, stressed by the oversized-request
teardowns, is what drove the engine into that null deref. The dump proves *where* (X4's UI event
dispatch) and clears the pipe DLL, the lua runtime and the FFI path; it does not deductively prove
our teardown was the trigger. n=1. Weighed against: the user vouches that Esc has never crashed the
game without the toolkit, and no other installed activity touches this path.

**The fix — a cap at the one chokepoint, plus a precautionary gate.**

1. `LivePipe.ask()` is the ONLY place a request is written. `_livepipe.MAX_REQUEST_BYTES = 1900`
   (safely below the 1997-byte measured-OK ceiling); a larger request raises `LiveRequestTooLarge`
   **before** `WriteFile`. No client path — a `query` passthrough, `ffi-census`, the reply-path
   ramp — can breach it. `LiveRequestTooLarge` is a `ValueError`, not a `LiveQuery*` transport
   error, so the re-arm/retry `except` paths cannot silently swallow it.
2. The FFI-surface verbs (`ffi-census`, and `ffisyms` via `query`) are **gated off by default**
   (`X4_LIVE_ALLOW_FFI=1` re-enables). They index `ffi.C` for ~2074 declared names including ~160
   the running VM never declared. The dump does **not** implicate them — this is precaution until
   the crash is understood, and the cap protects the channel whether the gate is open or shut. The
   batching/alignment logic and its ~15 tests are preserved behind the gate, not deleted.

**Scope note.** The reply-path ramp (`x4live ramp`, `groundtruth --with-ramp`) sends `echo <n>`
where the request is the *number* as text (tiny) and the game echoes back `n` bytes, so it measures
OUR read buffer (`_BUF`), never the request cap — it is unaffected, and unrelated to this teardown.


## F127 — a guard rule that covered the case above it only BY COINCIDENCE, until a path moved · **DEFECT (measured)** · confidence 96% · FIXED 2026-09-21

**Found 2026-09-21 during the v3.2.0 release verification · reproduced through the deployed hook · fixed the same day.** The 4%: the hole, the fix and seven controls are all MEASURED on this machine through the shipped artifact, and the blast radius on OTHER machines is reasoned rather than measured — on a fresh install `X4_REFERENCE` is normally not nested under a directory that used to be `X4_TOOLKIT`, so the coincidence that broke here may never have existed there. I did not enumerate installed layouts to bound that.

**What happened.** Two rules divide recursive text searches: `search_rooted_reference` fires when the searched path IS `reference\` (~60 GB), and `search_rooted_workspace` when it is the toolkit or game root. Both used `is_root()`, an EXACT normalised comparison, deliberately — a search scoped *into* a big tree is considered intentional and must stay allowed.

`reference\` lives at `<...>/Desktop/Modding/X4/reference`, and its parent `<...>/Desktop/Modding/X4` **was** `X4_TOOLKIT` until the dev repo was retired in this very release arc. So a search rooted at that parent was caught by the *workspace* rule, for a reason that had nothing to do with the 60 GB underneath it. When `X4_TOOLKIT` moved to `Projects/x4-claude-toolkit`, the parent stopped being named by any root — and a recursive walk from it, which necessarily traverses the whole reference tree, matched nothing.

MEASURED through the deployed `protect-bash.sh`:

| probe | before the fix |
|---|---|
| `cd <parent-of-reference> && grep -rl "x" --include="*.md" .` | **ALLOW** ← the hole |
| same shape rooted at the current toolkit root | deny (WRONG SCOPE) |
| rooted at `reference/` itself | deny (WRONG TOOL) |
| rooted at the game root | deny (WRONG SCOPE) |

**Three controls that still denied are what make this a hole rather than a rule change** — the guard was working everywhere its authors had looked.

**Why no test could fail.** `test_hook_facts.py`'s fixture sets `TOOLKIT = "C:/Users/tester/Desktop/Modding/X4"` and `REF = TOOLKIT + "/reference"`. The fixture *encoded the coincidence*: toolkit was the parent of reference, so the ancestor case was covered there too, permanently and invisibly. This is CLAUDE.md #37 exactly — an axis the instrument holds constant by design is unreachable by any amount of that instrument — and the axis here was *the relationship between two roots*, which no single-root probe can vary.

**How it was found.** Not by the suite, which stayed green throughout. `gates/hook_false_positives.py` replays every historical Bash command through the real hook and diffs per rule against a baseline: **146 of 17,133 shared commands moved**, 38 of them into `allow`. Classifying that permissive direction per item — rather than reading the per-rule totals — is what isolated it. ⚠ The gate had been red and **unobserved**: it is slow, it was not in the four-gate sweep the previous verification recorded, and an earlier run in this same session was killed for contending with the suite.

**The defect was TWICE the size the first classification reported, and the way that surfaced is the point.** A substring heuristic over the command text flagged 6 candidates: 3 this defect, and 3 a *separate* fixed false positive (hook test harnesses whose text merely QUOTES reference commands — verified benign by an 8-case probe in which all five real writes to `reference\` still hard-block). Re-running the whole comparison AFTER the fix and accounting for every departure gave the true figure: **6 commands left the `allow` set, not 3, and 0 entered it.** The three the heuristic missed were ancestor walks like `cd <parent> && grep -rli x --include="*.md" .` whose text never contains the string `reference`, so a text predicate could not see them while the *path* predicate could. I had filed them under "retired tree, correctly allowed".

★ **What made that under-count safe rather than lucky: the fix was written to the AXIS (proper ancestry), not to the three commands in hand.** Had it been keyed to the observed cases — a path list, a regex on those commands — it would have shipped covering half the defect, with a green gate and a per-item classification both agreeing it was complete. The predicted departure count was 3; the measured one was 6, and **the discrepancy was chased rather than accepted because it fell in the favourable direction** (CLAUDE.md #23: an unexplained remainder is a lead, however small).

**The fix.** `contains_root(path, root)` — true when `path` is a PROPER ancestor of `root`, compared on normalised paths with a trailing separator so `/a/bc` does not read as living under `/a/b`. `search_rooted_reference` becomes `rooted(reference) or above(reference)`. An ancestor search is strictly worse than the exact case the rule was written for, so it earns the same refusal, and the message now names both shapes.

**RE-DERIVED BY:** `test_hook_facts.py::TestSearchRootedAboveReference` — `test_contains_root_is_true_for_a_proper_ancestor`, `test_contains_root_is_NOT_is_root`, `test_contains_root_respects_the_path_SEPARATOR`, `test_contains_root_is_false_for_a_DESCENDANT`, `test_search_rooted_above_reference_FIRES_when_the_toolkit_is_elsewhere`, `test_an_explicit_ancestor_path_fires_too`, `test_a_HIGHER_ancestor_fires`, `test_the_scoped_subdirectory_search_STILL_does_not_fire`, `test_a_SIBLING_of_the_parent_does_not_fire`, `test_an_unrelated_tree_does_not_fire`, `test_it_fires_under_the_OLD_coincidental_layout_too`. `SPLIT_ROOTS` deliberately breaks the fixture's coincidence so the regression is reachable at all.

**Falsification twins, one per clause** (a compound condition needs one each, because each clause shadows the ones behind it): dropping the exact half kills `test_rg_without_a_flag_at_reference_fires` + `test_cd_then_dot_is_rooted`; dropping the ancestor half kills the three ancestor tests; dropping the path separator kills `test_contains_root_respects_the_path_SEPARATOR`. **3 of 3 killed**, tree restored byte-identical.

★ **A fourth clause was written and then DELETED because its mutant SURVIVED.** An explicit `if p == r: return False` guard could never change an answer — `r.startswith(r + "/")` is already False — so it was decoration (CLAUDE.md #26), and the partition is pinned by behaviour instead. Worth recording that the mutant roster removed code rather than only confirming it.

**The lesson that generalises past this rule.** Ask of every guard: *which two facts must stay in their current relationship for this to keep working?* Here it was "the parent of the big tree happens to be a named root". Nothing declared that dependency, nothing tested it, and the release that broke it is the release that renamed the root. A guard whose coverage rests on a coincidence has an **unowned axis**, and the tell is that its tests never vary the relationship — only the values.

## F126 — a fresh index that reports STALE, behind an exit code of 0 · **DEFECT (measured)** · confidence 97% · FIXED 2026-09-20

**Found 2026-09-20 · n=1, not reproduced · fixed the same day.** The 3%: the symptom was read DIRECTLY off the artifacts (database timestamp, coverage mtime, both fingerprints, exit 0) and the mechanism is confirmed by the code that fixed it -- but the TRIGGER, a staging `rm` losing a race with a BaseX file handle, was never replayed. A different failure of the same unchecked call would look identical.

**What happened.** `bash build-corpus.sh && bash build-effective.sh`, run to clear a stale
`x4eff`. The corpus half worked and stamped `content=39c7120a9189db25`. The effective half
serialized the tree, built the BaseX database (10,970 documents, `TIMESTAMP
2026-09-21T02:15:41Z`), then hit

```
rm: cannot remove '.../basex/_eff/tree/libraries/controlschemes_for_movie_capturing.xml': Device or resource busy
```

and **exited 0 without writing the coverage stamp**. `coverage-x4eff.json` kept its previous
mtime (00:22:03) and its previous fingerprint (`1f071ac8...`). The database was CURRENT; the
artifact describing it was not, so every consumer read STALE and `ask.py` refused to back a
negative. A retry, once the lock had cleared, stamped `39c7120a9189db25` and the banner went.

**Why it is worth a row despite n=1.** The shape is the one this register exists for: *a step
that does not produce its artifact and reports success anyway.* Two details make it worse than
a plain flake.

1. **It fails in the safe direction, which is why it can persist.** The freshness contract
   refused rather than over-claiming, so nothing wrong was ever asserted from it. That is the
   design working -- and it also means the only symptom is a stale banner after a build that
   said it succeeded. The natural response is to rebuild again, in a loop, which is exactly
   what CLAUDE.md #22 warns against and what nearly happened here.
2. **`&&` chaining hides it twice over.** `build-corpus.sh` legitimately exits **3** on
   SKIPCORRUPT exclusions (11 malformed VRO/cpsdo files the engine cannot read either), so
   `build-corpus.sh && build-effective.sh` silently skips the second script entirely. The
   documented invocation in the freshness banner uses `&&`. Chain with `;` and check each rc,
   or treat 3 as success for this script.

**MECHANISM, settled -- and it was NEITHER of the two I floated.** I wrote that the cause was
either the failing `rm` aborting the tail or the stamp being ordered after cleanup, and said a
read of the script would decide. It was a third thing: **`staleness.py --write` ran in a
subshell whose rc was never captured**, so the stamp DID run, DID fail, and nobody looked.
The script then exited on the coverage verdict alone. ★ Worth keeping as a lesson about
lessons: the entry declined to guess, and both available guesses were wrong. A mechanism that
feels one read away is still not known until someone does the read.

**FIXED 2026-09-20 (peer session, `4e21af4`).** Both scripts capture the rc and refuse,
naming the re-stamp command -- **both**, because the identical unchecked call sat in each and
fixing one would have left its twin. The `&&` chaining was fixed in every copy of the
guidance (the banner in two places, plus the shipped `CLAUDE.md`).

**RE-DERIVED BY:** `tests/test_build_scripts_check_their_stamp.py` --
`test_the_stamp_call_CAPTURES_its_return_code[build-corpus.sh|build-effective.sh]` (the rc is
captured), `test_a_FAILED_stamp_reaches_an_exit[...]` (and is acted on -- capturing without a
branch would satisfy the first test while still exiting 0), and
`test_the_two_builds_are_NOT_chained_with_and_in_any_guidance` (the `&&` half). All four
mutation-checked: discarding the rc turns two red, and re-introducing `&&` in a markdown file
turns the third red.

⚠ **What those tests do NOT cover**, so the coverage is not overread: they assert the SHAPE
of the shell source, not runtime behaviour. A stamp that SUCCEEDS while writing the wrong
fingerprint would pass all of them. The stronger check -- compare the stamped fingerprint
against the one the build just computed, both of which the build already knows -- remains
unwritten.

## F125 — an absent load marker was read as a missing install, but the mod arms on GAME LOAD · **DEFECT (measured)** · confidence 95% · FIXED 2026-09-20

**RE-DERIVED BY:** `tests/test_live_load_marker.py` (`test_MENU_no_save_loaded_does_NOT_say_not_installed` -- the case itself; `test_NOT_DEPLOYED_points_at_the_install` and `test_marker_absent_and_deployment_UNKNOWN_names_BOTH_causes`, its twins; `test_helper_is_deployed_asks_for_ACTIVE_not_INSTALLED`, pinning the scope; `test_an_UNCONFIGURED_toolkit_REFUSES_rather_than_claiming_NOT_deployed`).

**What happened (MEASURED).** `helper_loaded_this_session()` shipped on 2026-09-20 to read the
mod's load marker out of a live `debug.txt` so a refusal could say WHICH failure it was. In the
in-game session the same day it produced a false positive in its own new branch:

| game state | marker in live debug.txt | extension in `mods("active")` | what the refusal SAID |
|---|---|---|---|
| main menu, no save loaded | absent | **present and enabled** | *"most likely not installed or not enabled"* |
| save loaded, windowed | present | present | correct |

The helper initialises on **game load**, not at process start, so at the main menu the marker is
legitimately absent. The refusal sent a user to check an install that was fine.

**Why the check could not have been right.** The marker reports a CONSEQUENCE of the install, so
its absence has two causes -- not installed, and not yet initialised -- and no amount of reading
the log separates them. This is the "a set difference is a QUESTION, not an answer" shape
(CLAUDE.md #34): what was measured is *"the marker did not appear"*; what was claimed is *"the
extension is not there"*. The instrument had a scope (the mod's own initialisation) and the claim
did not respect it.

⚠ **The obvious repair was the wrong one.** The first idea was to find a game-start marker in
`debug.txt` and require it before concluding "not installed". The only existing detector is
`_debugcli._NEW_GAME_MARKER = "Universe generation begins"`.

- **READ 2026-09-20**, in this machine's 15,447,025-byte `debug.txt` (the whole file, one run):
  the marker appears **exactly once**, at line 12,316, and that run was a NEW game -- line 3,952
  reads `pendingGameStart = new custom game start`. The helper's own load marker first appears at
  line 14,104, *after* it, and at engine time 0.03 against the menu phase's 129.17, so the engine
  clock restarts at game start and the helper arms only once a game exists. That is consistent
  with the in-game measurement and is the same fact from a second instrument.
- ⚠ **INFERRED, NOT MEASURED: that a save LOAD writes no counterpart.** It follows from
  `_debugcli`'s own recorded note (*"A new game generates the galaxy; a save load does not"*) and
  from the marker's absence anywhere else in the file -- but **this machine has exactly one
  `debug.txt` and its run was a new game**, so no save-load log was available to check. What
  would confirm it: one game session that LOADS a save, then a grep of the resulting log.

So that route risked trading a false "not installed" for a false "no save loaded" on every loaded
save, on a premise this session could not measure. It was not taken.

**The fix.** Measure the install directly instead of inferring it: `helper_is_deployed()` asks
`_registry.mods("active")` whether `x4_toolkit_helper` is in the set the ENGINE would load --
never `"installed"` (CLAUDE.md #24), because a folder on disk the profile has switched off is not
loaded, and calling it deployed would be the same false pass the scope argument exists to prevent.
MEASURED on this machine: 125 active mods, the helper among them, in 0.013 s -- and it runs only
on a refusal path. True / False / **None**, with None for an unconfigured toolkit, because *"I was
never told where `extensions\` is"* is a claim about our configuration and must not be reported
as a claim about the user's install.

Branch order is deployment first (a direct measurement) EXCEPT that a marker reading LOADED
outranks it, since a mod cannot log from a live run without being installed; that combination
means the mod set moved under the running game. With deployment unknown the message names BOTH
causes rather than picking one -- picking one is the defect.

⚠ **AND THE FIX HAD THE SAME DEFECT ONE LAYER DOWN, found the same day by running it against a
deliberately wrong config instead of a stub.** A **configured but NONEXISTENT** `extensions\`
root raises nothing: `_paths` resolves it happily and `scan_installed` walks a directory that is
not there. MEASURED: **0 active mods, 0 dropped, no exception** -- so the `except
Unconfigured/OSError` guard never fired and `any()` over the empty list answered **False**,
reporting *"the helper extension is NOT in the set the engine would load"* about a machine the
tool had never looked at. MEASURED from the file: of the five tests, TWO stubbed `_registry.mods` to RAISE and
the other THREE returned a POPULATED list -- **not one returned an EMPTY one**, so every stub
expressed either an exception or a result and the empty middle, the only shape that actually
broke, was never expressed at all. The real-config run is what found it. `helper_is_deployed` now returns **None on an empty
active set** -- an empty population cannot support a negative (CLAUDE.md #9), and a genuinely
vanilla install loses specificity rather than being told something false. Guarded in both
directions by `test_an_EMPTY_mod_set_REFUSES_instead_of_reporting_NOT_deployed` and its twin
`test_a_NON_EMPTY_mod_set_WITHOUT_the_helper_still_reports_FALSE`, and by two mutants
(`if False:` / `if True:`), each killed by the test written for it.

★ **The transferable lesson: a stub that raises cannot test a path that returns quietly.** The
whole point of this function is to refuse rather than guess, and the one way it failed to refuse
was the one way no stub reproduced. Exercise a refusal path against a real wrong configuration
at least once.

**Scope note: what is still NOT measured.** A HUNG game. `IsHungAppWindow` is not exported by this
`pywin32` (VERIFIED 2026-09-20 and again on re-check) and a hang cannot be induced on demand, so
that state is left unreported rather than guessed at. `game_is_minimized()` covers the common
case in both display modes; hung remains the open axis, and is the least valuable of the four.

---

**F128-F138 come from the 2026-09-24 correctness audit** (`AUDIT-2026-09-24.md`, which holds
every finding row by row with its commit). The register records one entry per TOOL defect
CLASS, not per finding; the audit row IDs are cited so either can be reached from the other.

## F128 — the load order modelled the community rule, and nothing compared it with the engine · **DEFECT (measured)** · confidence 97% · ✅ FIXED 2026-09-25

**Found 2026-09-24 (AUDIT LO-1) · fixed in `1cb00bf`.** The 3%: what is measured is the
engine's SIGNATURE-CHECK order. That it is also the PATCH-APPLY order — the order that decides
winners — is INFERRED from the multi-pass pattern (the check runs inside the dependency-aware
walk), not probed (LO-2), and shapes the installed modlist never exercises are unobserved:
missing/optional/disabled dependencies, cycles, duplicate ids, non-ASCII names, DLC position,
other install roots (LO-3). `scripts/load-order-probe.py` is built to settle both and has not
been run in game yet.

> **2026-09-26 -- the 3% is spent.** The probe was run in game and `score` PASSED: signature
> order 12/12 as predicted; **apply order 3/3** -- each collision pair `<replace>`s an attribute
> another probe `<add>`s, and the replace succeeded exactly when the adder loads earlier, so
> signature order = apply order is now MEASURED, not inferred (AUDIT LO-2 FIXED); a folder with
> `ß` loads after one with `sz` (`sort_key` keeps `ß` as one character); all DLC load before
> mods. The dependency/enable shapes it measured are applied by `mods("active")` (LO-3,
> `8f66d1c`). Still unobserved: other install roots, non-NTFS filesystems. The paragraph above
> is kept as the state before the run.

**What it did.** `_loadorder.compute_load_order` sorted folders ASCII (case-sensitive) and
placed each mod as early as its dependencies allowed (Kahn). That is the community convention;
no official source pins a rule (LO-4: Egosoft documents dependencies, not order, and the one
community tool that implements an order says in its own TODO that it is unverified).

**What the engine does.** It signature-checks every unsigned extension file as it reads it, so
for any path two mods ship, the order of those `debug.txt` lines is the order it walked them.
MEASURED over two launches two weeks apart: **372 file classes, 5,134 ordered pairs**. The old
code inverted **685**. The engine's rule — folders in case-insensitive UPPERCASE order (`_`
after letters, a space before `_`), walked in repeated passes, each pass loading every mod
whose installed dependencies have already loaded (including earlier in the same pass) —
inverts **0**. Refuted with the same data: lowercase order, the profile `content.xml` entry
order, and sorting by manifest `id` (51 of 72 logged mods have id ≠ folder, so that test
discriminates).

**Why no test could fail.** Every winner test passed before and after the fix: their fixtures
never put two folders where the two rules disagree. The mutation audit found this; the LO-1
tests below are the ones that DO discriminate.

**Also fixed with it (LO-6).** `Config.dlc_dirs` appended packed DLC after unpacked; it now
uses the same key for both. Tier B assumed an UNINSTALLED dev mod loads LAST (the optimistic
tree, which hides ops the engine skips); it now places the mod by the rule from its own
manifest.

**RE-DERIVED BY:** `tests/test_load_order_oracle.py` — `test_LO1_folder_names_compare_case_insensitively`,
`test_LO1_underscore_sorts_after_letters`, `test_LO1_space_sorts_before_underscore`,
`test_LO1_unmet_dependency_waits_for_the_next_pass`,
`test_LO1_dependency_loaded_earlier_in_the_same_pass_counts`,
`test_LO1_uninstalled_dependency_does_not_hold_a_mod_back`; `tests/test_tierb.py` —
`test_tier_b_uninstalled_mod_is_placed_where_the_engine_would_load_it`,
`test_tier_b_uninstalled_mod_waits_for_a_dependency_that_sorts_later`; and
`gates/load_order_oracle.py` against every new `debug.txt` (it refuses rc 2 when the log
describes another modlist rather than convict the code).

## F129 — freshness missed a mod MOVING, code outside its source list, and a GAME update · **DEFECT (read)** · confidence 93% · ✅ FIXED 2026-09-25

**Found 2026-09-24 (AUDIT FR-1..FR-4, FR-6) · fixed in `488a5a5`.** The 7%: the defects were
READ at the code and pinned by tests, not reproduced on a real rebuild; and FR-6 is pinned only
as far as the root list — no test moves a profile-root mod and asserts STALE (READ 2026-09-26).

Four ways an artifact stayed FRESH while its answer moved:

- **FR-1** `_freshness._fold` hashed each mod's folder NAME, not its install ROOT: moving a mod
  from the game root to the profile left every artifact FRESH, while `diff_detail` over the
  same vectors reported it removed and added — two answers from one input.
- **FR-2** `ENGINE_SOURCES` named 8 modules. Tracing a store build showed 3 of them never run
  on it (`_diff`, `_xpath`, `_scan` — their edits cried wolf) and 2 that do were missing
  (`_provenance`, and the `mod_xml_paths` enumeration then living in the CLI module `_compat`,
  now `_modfiles`). The list is 7 today — derive it from the module, never retype it.
  `tools/basex/build-effective.py`, outside the package, is folded into x4eff's engine axis
  only.
- **FR-3** x4xref indexed every install root and stamped one.
- **FR-4** after a game update the banner blamed MODS and `x4modlist changed` said "no change";
  the reference side is now its own axis, named in the banner.
- **FR-6** (found while fixing) BaseX staleness fingerprinted the game-root extensions only,
  while x4raw/x4eff are built over every root.

**RE-DERIVED BY:** `tests/test_audit0924_analysis.py` —
`test_FR1_moving_a_mod_between_roots_moves_the_content_digest`,
`test_FR2_every_module_that_shapes_the_store_is_an_engine_source`,
`test_FR2_every_engine_source_runs_on_the_store_build`,
`test_FR3_a_change_in_an_indexed_root_marks_the_index_stale`,
`test_FR4_the_banner_attributes_a_reference_move_to_the_reference`,
`test_FR4_changed_does_not_say_no_change_after_a_reference_move`; `tools/basex/test_staleness.py`
— `test_the_x4eff_build_script_is_on_x4effs_engine_axis_only`.

## F130 — x4diff compared attributes only, so an edit held in element TEXT did not exist · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-09-25

**Found 2026-09-24 (AUDIT DF-1..DF-4) · fixed in `9151a4e`, `cd877a5`, `8d0066f`, `2d883aa`.**
The 5%: the fixed text model (whitespace collapsed per segment, segments positional across
children) is a design choice checked against the cases below, not against every installed op.

`_diff._index` mapped each node to its attributes. A `<replace sel=".../@min">5</replace>` →
`999` edit read **"changed files: 0", rc 0** (ORCH-M), and `_threeway` counted a text-only
author edit as VERBATIM. The audit put the exposure at ~48% of installed-mod ops, which carry
their value in text, as does every t-file string.

The same class, three more ways: diff ops were keyed on (tag, sel), and **839 of 7,022**
installed ops (12%, the reviewer's measurement) share both, so inserting one op reported its
untouched siblings as removed + added and a selector-only edit as remove + add (DF-3); a
stack whose first supplier ships the file as a `<diff>` returned no baseline at all, "the OLD
copy would not parse" (DF-2); `--file <added file>` printed nothing about it (DF-4).

**RE-DERIVED BY:** `tests/test_threeway.py` — `test_a_TEXT_only_author_edit_is_NOT_reported_verbatim`,
`test_REINDENTING_a_document_is_not_an_edit`; `tests/test_audit0924_merge.py` —
`test_text_on_either_side_of_a_child_is_positional`,
`test_reindenting_a_multi_line_string_is_not_an_edit`,
`test_ops_sharing_tag_and_sel_are_keyed_by_payload_not_position`,
`test_a_selector_only_edit_is_a_sel_change_not_remove_plus_add`.

## F131 — `ask.py` certified a negative over what the query never addressed · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-09-25

**Found 2026-09-24 (AUDIT BX-1, BX-3, BX-4, BX-5) · fixed in `eecec0e`, `6fb768a`, `0df00f8`.**
The 5%: the classifier covers the database-reaching calls it names (db module,
`collection`/`uri-collection`/`doc`); I did not enumerate XQuery's other document-reaching
forms against it.

The foreign-database guard matched `name('<db>')` with nothing after the literal, so
`db:get('x4eff','no/such/typo.xml')//ware` printed **"NEGATIVE CONFIRMED over 10970 of 10970
documents", rc 0** (ORCH-M): a zero over ONE, nonexistent, document scored against the whole
database. Two-argument `db:get`, a db name in a variable, `doc()` and cross-db calls all walked
past it. Three siblings in the same licence: a failed rebuild left the previous
`coverage-<db>.json` beside a partial database (BX-3); "accounted" was judged on the aggregate,
so +5 under one root and −5 under another netted to zero (BX-4); a lone `false` or `""` was
printed as a hit (BX-5).

**RE-DERIVED BY:** `tests/test_audit0924_gates.py` —
`test_bx1_a_zero_from_ONE_addressed_document_is_not_a_negative_over_the_database`,
`test_bx1_every_partial_or_unreadable_address_is_refused`,
`test_bx1_TWIN_a_whole_database_zero_is_still_confirmed`,
`test_bx1_a_zero_from_a_query_naming_NO_database_is_not_a_negative`,
`test_bx3_the_old_coverage_licence_is_revoked_before_the_db_is_dropped`,
`test_bx4_offsetting_per_root_errors_do_not_license_a_negative`,
`test_bx5_a_database_argparse_rejects_is_never_advised`.

## F132 — the PowerShell tool reached no guard at all · **DEFECT (measured)** · confidence 94% · ✅ FIXED 2026-09-25

**Found 2026-09-24 (AUDIT HK-1, HK-5) · fixed in `3b9562f`, then seven review items
(`faf9099` … `c39d9a1`), `44a2439`, `e588de4`, `79b037a`.** The 6%: the translator maps the
PowerShell it models; the fail-closed paths (unparseable, unmodelled writing cmdlet, unresolved
splat or `iex` text, no `pwsh` on the machine) ASK rather than allow, which bounds a miss but
does not make one impossible.

`.claude/settings.json` had PreToolUse matchers for Bash, Edit and Write only. MEASURED:
`Remove-Item -Recurse <reference>` through the PowerShell tool ran with no guard. NotebookEdit
likewise. No test noticed: deleting the Edit/Write guard kept all three hook suites green (the
audit's mutation lane), because nothing asserted matcher COVERAGE.

**The fix is one rule set with two front-ends** (user decision). `ps_translate.ps1` parses the
command with PowerShell's own parser, binds parameters as PowerShell binds them, and emits the
equivalent POSIX command, which the unchanged Bash analysis judges. The seven review rounds
each found a real hole in the translator (splats, file-system METHODS, `iex` of unreadable
text, provider-qualified paths, cmd `%VAR%`, unmapped writing cmdlets, an unbounded
translation time) — see the commits. Friction replay: 23,064 historical Bash + 531 PowerShell
commands through the new hook; 37 Bash verdicts moved allow → ask, all 37 traced to two
causes and fixed (0 remaining). `scripts/fuzz-guard.py`: 0 bypasses over 3,103 mutants.

**RE-DERIVED BY:** `.claude/hooks/test_audit0924_hooks.py` —
`test_every_file_changing_tool_reaches_a_protect_guard`,
`test_the_post_edit_validator_sees_every_file_editing_tool`,
`test_a_payload_read_from_a_FILE_is_seen`; `.claude/hooks/test_hook_facts.py` —
`test_remove_item_recurse_reference_is_the_reference_block`,
`test_no_powershell_is_a_refusal`.

## F133 — a filtered `find -delete` was exempt from the delete rules, not scoped by them · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-09-25

**Found 2026-09-24 (AUDIT HK-2) · fixed in `3b9562f`, `44a2439`.** The 5%: the in-place
writers named here are the ones found; the fuzzer covers the grammar it seeds.

`find_deletes` returned `[]` for any filtered find, so every rule read "no delete": `find
<saves> -name '*.xml.gz' -delete` and `find <reference> -name '*.xml' -delete` were ALLOW
(ORCH-M), and `test_a_filtered_find_under_the_GAME_is_also_scoped` ASSERTED the hole (it
checked only the whole-install block). `truncate` and `dd of=` had no rule; `cmd //c` and
`powershell -c` were not walked as carriers. A filter naming only a regenerable cache
(`__pycache__`, `*.pyc`) stays exempt — the cleanup the exemption was written for.

**Found by the fuzzer the fix seeded:** find's paths were read from token 1, so a wrapper with
a flag (`nice -n 5 find`, `stdbuf -o L find <game> -delete`) yielded NO path — the last was
ALLOW before this work too. Paths are now read from the verb token.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` — `test_every_save_by_filter_asks`,
`test_a_filtered_delete_in_reference_is_still_a_reference_delete`,
`test_TWIN_a_pycache_cleanup_is_still_silent_everywhere`, `test_truncate_into_reference`,
`test_dd_of_into_reference`.

## F134 — a hook that times out does not block, and the timeout was shorter than the hook · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-09-25

**Found 2026-09-24 (AUDIT HK-4) · fixed in `8efa95a`, `b48b188`.** The 10%: "a timed-out hook
does not block" is the Claude Code documentation's statement, not reproduced here; and the 30 s
value in `.claude/settings.json` is asserted by no test (my search of the hook and package
tests for an assertion on it found none), so a regression to a short timeout would stay green.

Every PreToolUse hook carried `"timeout": 5`. MEASURED: 1.6-3.8 s idle, 4.1-7.6 s under load,
~0.2 s per process spawn and ~0.05 s of actual analysis — so under load a guard could run out
of time and let the command through. Fix (user decision): 30 s, and the spawns cut
(protect-bash 221-236 ms → 124-126 ms; protect-files 726-810 ms → 279 ms, median of 5).
The PowerShell front-end then reopened the same class: each translation had its own 20 s
timeout, so five distinct carriers in one Bash command committed 100 s against the 30 s hook
timeout (MEASURED with a stubbed wedged `pwsh`). Now one 15 s budget per hook call; past it,
further PowerShell text is refused as untranslated → ask.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` — `test_the_budget_is_per_call`.

## F135 — a gate that examined nothing reported a pass · **DEFECT (measured)** · confidence 93% · ✅ FIXED 2026-09-25

**Found 2026-09-24 (AUDIT GT-1..GT-4, GT-6, GT-7, GT-8) · fixed in `aa88db3`, `7de1142`,
`69ff68f`, `2fd8b2b`, `4296c64`, `d303e32` and the review commits.** The 7%: floors were added
to the gates the audit named; the other gates were not re-walked for the same shape here.

The shape, gate by gate: `similar_audit`, `provenance_audit` and `registry_provenance` passed
over zero items, and `xsd_fast_parity` counted files the schema path never validated as
parity (GT-3); `noop_audit` collected unreadable catalogs and never read the list (GT-1);
`determinism_audit` dropped the return code, so a crash repeated twice was "stable" (GT-2);
`edge_sweep` and `stress_sweep` judged only tracebacks, so a wrong exit 0 passed (GT-4);
`diff_truth` planted 0, found 0 and printed "RESULT: exact" rc 0 (GT-6); `toolkit_usage`
returned 0 with no baseline (GT-8); `provenance_audit`, `consistency_audit` and `cross_tool`
audited a stale store as current, and `claims_audit` refused it with rc 5, which
`run-gates.sh` counts as FAIL rather than CANNOT (GT-6, GT-7).

**The fix is two shared helpers, not a comment per gate:** `_env.nothing_checked` (rc 2) and
`_env.stale_store_refusal` (rc 2). A real finding still outranks a refusal (rc 1).
Verified end to end on this machine at the time: the real store was stale and
`provenance_audit` and `claims_audit` both refused with rc 2.

**RE-DERIVED BY:** `tests/test_audit0924_gates.py` —
`test_gt1_an_unreadable_catalog_is_not_a_clean_noop_audit`,
`test_gt2_a_command_that_fails_twice_is_not_deterministic_output`,
`test_gt3_similar_audit_over_zero_pairs_is_not_a_pass`,
`test_gt3_provenance_audit_over_an_empty_store_is_not_a_pass`,
`test_gt3_registry_provenance_over_zero_rows_is_not_a_pass`,
`test_gt3_xsd_parity_over_files_with_no_schema_is_not_parity`,
`test_gt4_edge_sweep_does_not_pass_hostile_inputs_that_all_exit_0`,
`test_gt6_diff_truth_nothing_planted_is_not_exact`,
`test_gt6_provenance_audit_refuses_a_stale_store`,
`test_gt8_toolkit_usage_with_no_baseline_is_not_a_pass`.

## F136 — the per-edit hook's `--file` run and the full run disagreed on the same file · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-09-25

**Found 2026-09-24 (AUDIT VA-2, VA-3, VA-4, VA-8, VA-14) · fixed in `fa73f53`.** The 5%:
agreement is pinned for the shapes below; the two paths now share `_sel_check_file`, so a new
divergence would have to come from outside it.

`x4validate --file` — what the per-edit hook runs — had its own sel path, lacking the
inactive-target and inert-bare-path verdicts. MEASURED: a nested patch for an uninstalled
target was ERROR rc 1 under `--file` and INFO rc 0 in the full run. `--file` also guessed the
vpath as the bare filename when a relative/absolute path mix made `relative_to` raise (VA-4),
silently dropped `--entity/--like/--update/--debug` (VA-3), and printed "OK: no issues found"
with no denominator (VA-14); `--sel-only` was parsed and never read (VA-8).

**RE-DERIVED BY:** `tests/test_audit0924_validate.py` —
`test_va2_file_mode_agrees_with_full_run_on_an_inactive_nested_patch`,
`test_va2_file_mode_reports_an_inert_bare_path_patch`,
`test_va14_file_mode_prints_its_denominator_and_what_it_skipped`.

## F137 — an EMPTY packed-DLC answer was read as a failure, so both mini-DLC were indexed twice · **DEFECT (measured)** · confidence 96% · ✅ FIXED 2026-09-25

**Found 2026-09-24 (AUDIT BX-2) · fixed in `1c73f71`, `e4f8d69`.** The 4%: an x4raw built
before the fix still holds the duplicates until it is rebuilt — this fixes the staging, not
an existing database.

`stage.py` returned `names or ("ego_dlc_mini_01", "ego_dlc_mini_02")`, which cannot tell
Config's correct "nothing is packed-only" (every DLC unpacked into `reference/`) from a failure
to ask. Both mini-DLC were staged from their archives on top of the reference copies: **142
documents**, verified deep-equal duplicates at identical paths, indexed twice in x4raw. Now the
answer is used as given, and ANY failure to ask — not only the four exception types first
caught — refuses rc 2.

**RE-DERIVED BY:** `tests/test_audit0924_gates.py` — `test_bx2_an_empty_packed_dlc_answer_is_honoured`,
`test_bx2_TWIN_a_nonempty_answer_is_staged_as_given`,
`test_bx2_ANY_config_failure_is_a_refusal_not_a_traceback`.

## F138 — the re-derivation gate credits a path the entry cites as EVIDENCE, not as a check · **SCOPE (measured)** · confidence 90% · ⏳ OPEN

**Found 2026-09-26 while writing the AUDIT DC-3 notes on F60/F61/F73/F76/F78/F87.** The 10%:
the rate is measured on those 6 entries only; the other covered entries are not classified.

`gates/register_rederivation.py` covers an entry when at least one path it cites exists. It
cannot tell a path cited as the check of a fix from a path quoted as where the defect was seen.
Of the 6 entries reviewed, **2** were "covered" only that way: F60 by `tests/test_freshness.py`
(quoted as an absent file in the split it describes) and F73 by `gates/schema_sweep.py` (quoted
as a place an identifier once sat). F60 now declares NO RE-DERIVATION; F73's crediting path is
unchanged. The gate's own docstring already records that widening "cannot admit a mere
MENTION" only in the sense that the file must EXIST — existence is what it checks, not role.

Open: classify every covered entry's crediting path (check vs mention) before deciding whether
the fix is a citation grammar (e.g. only paths on a `RE-DERIVED BY` line count) or a baseline.

## F139 — the active mod set's exclusions are recorded only if the caller asks · **SCOPE (measured)** · confidence 95% · ✅ FIXED 2026-09-26

**Found 2026-09-26 (AUDIT-2026-09-24 final review).** `_registry.mods("active")` models the
engine's load decision (MEASURED by the load-order probe): a mod whose REQUIRED dependency is
missing, disabled or cyclic does not load, and neither does one whose manifest will not parse.
Leaving it out is right. Saying nothing is not: each exclusion is appended to `dropped` only
when the caller passes one, and `_registry` cannot print (it is an engine source,
`tests/test_engine_sources_carry_no_cli.py`). MEASURED at the fix commit: **20** active-scope
call sites across `x4validate/`, `gates/` and `tools/basex/` (the `_effective.active_mods`
wrapper not counted); **0** passed `dropped`.

**Fixed for 4:** Tier B (`_check.tier_b_trees`: a non-degraded NOT CHECKED skip + a note per
mod), x4compat (`analyze`: NOT ANALYSED), `x4effective build` (a progress line) and BaseX
`build-effective.py` (stderr). **Still silent (16):** `_check.py` (3 non-Tier-B lookups),
`_effectivecli` dump, `_livecli`, `_livepipe`, `_savecli`, `_similarity`, `_stats`, and the
gates `consistency_audit`, `load_order_oracle`, `obtainability_audit`, `oracle_index`,
`oracle_reverse`, `similar_audit`, `tool_properties`. Cost today: 0 of 125 installed mods are
excluded on this install (MEASURED 2026-09-26) -- which is exactly where a wrong denominator
hides (CLAUDE.md #23), and it grows with every user's broken dependency.

**FIXED 2026-09-26 in `f634e30`** (the entry above is the PARTLY FIXED state, kept as
history). `mods()` now ALWAYS collects its exclusions and returns them on the result, a
`ModList` (a `list` subclass) carrying `.dropped`; `dropped=` still receives the same records.
`_registry` still prints nothing. One pure formatter, `_registry.dropped_note()`, and
`left_out()` (folder -> reason) for a caller that must classify; `_check.note_not_loaded()`
is the Report sink, deduplicated across checks so one cause reads as one line. Each of the 16
routes it to the sink it already had: a NOT CHECKED skip (`_check` variant sibling check and
nested-script scope, `_savecli`), stderr (`_effectivecli` dump, `_similarity`, `_stats`,
`consistency_audit`, `obtainability_audit`, `oracle_reverse`, `similar_audit`), stdout
(`_livecli`, `load_order_oracle`, `oracle_index`, `tool_properties`), and the x4live
no-connection refusal via the new `_livepipe.helper_deployment()`. The two lazy gate caches
say it once, when the cache is built.

**A second defect found doing it:** `_check._disabled_folders` was `installed - active`, so a
nested patch aimed at an ENABLED mod the engine leaves out read "installed but DISABLED" -- a
claim about a switch nobody touched. `_inactive_folders()` keeps the two reasons apart and the
message now names the dependency. And `helper_deployment()` answers False with the reason, not
the "could not look" None, when mods were found but none of them load.

MEASURED: re-derived population by AST (`_registry.mods` with literal `"active"`,
`active_mods`) over `x4validate/`, `gates/`, `tools/basex/`: 21 calls = 20 sites + the
`_effective.active_mods` wrapper, exactly the 16 silent sites named above. New tests: 22 of 29
in the file failed before the fix, 29 of 29 pass after. E2E on a scratch install
(`better_trade_hub` requiring an absent `sn_mod_support_apis`): `x4validate --tier b`,
`x4stats wares`, `x4effective dump` and `x4similar` each print the line naming the mod and the
dependency; the twin with the dependency installed prints none (x4stats and x4effective
dump re-run: 0 matching lines in each).

The 5%: the AST ban proves each function REACHES the channel, not that what it reaches is
printed -- that half rests on the one behavioural test per site. `_registry.py` is an engine
source, so this moves the freshness engine axis: stores built before it read STALE.

**RE-DERIVED BY:** `tests/test_active_exclusions_disclosed.py` --
`test_every_active_scope_caller_discloses` (the AST ban, EMPTY allowlist) and
`test_the_ban_can_fail`; `test_mods_ALWAYS_carries_its_exclusions_without_being_asked`,
`test_dropped_note_is_None_when_nothing_was_left_out`;
`test_F139_an_engine_excluded_target_is_NOT_called_disabled` and its twin (the
`_inactive_folders` site); 15 `test_F139_*_discloses`, one per other former silent site; the
four original
`test_*_discloses_the_excluded_mod` tests and the Tier B twin.


## F140 — a sandbox assigned to `TMP` hangs every native Windows child that starts a process · **DEFECT (measured)** · confidence 97% · ✅ FIXED 2026-09-26

**Found 2026-09-26.** A `python scan-identifiers.py`, started by `scripts/test-hooks.sh` in a
since-removed worktree, ran for 15.5 hours on 13.75 hours of CPU -- spinning, not blocked --
until killed. The first theory (the worktree was deleted under it) was WRONG: a scanner
started in a removed worktree exits rc 2 in 0.08 s.

**Mechanism (MEASURED).** `test-hooks.sh:28` did `TMP="$(mktemp -d "$_SBX/hooks.XXXXXX")"`.
On Windows `TMP` is an inherited, EXPORTED variable, so the assignment changed the temp
directory of every native process the suite started. A native Windows process whose own
`TMP` is longer than 260 characters spins inside `CreateProcessW` (stack-sampled:
`CreateProcessInternalW -> BasepQueryAppCompat -> GetTempPathW`) the first time it starts a
child -- and the identifier scanner is the first process in the suite that spawns one
(`git`). Threshold: `TMP` of 260 characters starts a child in 0.05 s, 261 hangs (reproduced
independently twice). The incident run used a deliberately long `X4_TEST_SANDBOX` (to
reproduce an earlier long-path flake), which put `TMP` at 276 characters. A subprocess
`timeout=` cannot help: the call never gets far enough to start the timer.

**The second defect it exposed (MEASURED).** Killed with `taskkill /F`, the scanner exits 1
with no output -- the same code it uses for "a personal identifier reached a tracked file" --
and `test-hooks.sh` reported exactly that. rc alone cannot tell a finding from a kill.

**Fix.** The variable is `SBX_TMP` in `test-hooks.sh` (57 uses) and `smoke-basex.sh`;
`scan-identifiers.py` refuses rc 2 before spawning anything when `TMP`/`TEMP` exceeds 260
characters on Windows; `scripts/_scan-classify.sh` calls a non-zero exit a leak only when the
scanner printed an `::error file=` line, otherwise "could not confirm". E2E: a 297-character
sandbox now completes (rc 0, 173 passed); a normal run is unchanged (rc 0, 173 passed).

Scope: the default sandbox is `<repo>/.test-sandbox/hooks.XXXXXX`, about 62 characters here,
so the hang needs a long `X4_TEST_SANDBOX` or a repo path near 233 characters. Linux CI is
not affected. Session environments measured at `TMP` = 33 characters.

**RE-DERIVED BY:** `tests/test_shell_scripts_never_reassign_tmp.py` (the ban; failed on
`test-hooks.sh:28` and `smoke-basex.sh:22` before the fix); `tests/test_scan_classify.py`
(a killed scanner is `unknown`, not `leak`; 3 of 6 failed with the guard removed);
`tests/test_scan_identifiers_tmp_hang_guard.py` (the rc-2 refusal, 10 of 11 failed before).


## F141 — a required dependency is counted as satisfied across extensions roots · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-09-26

**Found 2026-09-26, load-order probe round 2.** The engine READS the profile's
`extensions\` folder (its Extensions dialog lists all three probe mods placed there), but a
REQUIRED dependency does not cross roots: a profile-root mod requiring a game-root mod and a
game-root mod requiring a profile-root mod were BOTH shown red in that dialog and neither
appears in the log. `_registry._active_filter` (READ, `x4validate/_registry.py` ~L463-471)
builds one `provided` set from every enabled mod of every root, so it models both as loaded.

Scope: 0 on this install (the profile root is empty, the Workshop root absent); a real error
for a user with mods in two roots, in every tool that models the running game (Tier B,
x4compat, x4effective, the stores, the oracles).

Why OPEN: whether an error-free profile-root mod's patches APPLY, and where they fall in the
order, is unmeasured (round 2's log never names that folder). Round 3 of
`scripts/load-order-probe.py` gives each cross-root probe a patch that can never match, so the
engine logs a line for it iff it was applied, in apply order. The dependency rule and the
cross-root ORDER are fixed together from that result, rather than one now and one later.
The 10%: the dialog's red is read as "the cross-root dependency is unresolved" because each red
probe's only dependency loaded (x_a) or was listed error-free (x_d); the dialog's own reason
text for those two rows was not captured.

**FIXED 2026-09-26 (`a20e859`), from probe round 3.** Each cross-root probe carried a patch
that can never match, so the engine logged it iff applied: apply order game a, game c,
profile b, profile d -- profile-root mods APPLY, after every game-root mod; the chain's one
miss was the game-root mod reading a profile-root attribute. `scan_installed` records
`root_rank`; `_active_filter` resolves required dependencies within the mod's own root (DLC
counts for every root -- UNMEASURED for a non-game root, stated in the docstring);
`compute_load_order` walks (root, name) in repeated passes with no cross-root edge;
`place_candidate` gives a candidate its installed copy's root, else the game root. Still
UNMEASURED and DISCLOSED in `dropped`: where a game-root mod that waits a pass falls relative
to profile-root mods (round 3's probes all loaded in pass one). Real install unchanged (one
root; order hash identical); oracle on the round-3 launch log: 0 of 4,634 pairs inverted.

**ADDENDUM 2026-09-27 (v3.3.0 release review) -- the fix over-reached.** `a20e859` keyed the
isolation by root POSITION and applied it to EVERY root, the Steam Workshop root included,
though only game<->profile was measured: a game-root mod requiring a Workshop mod was dropped
as "NOT LOADED by the engine", stated as fact, and an optional edge to a Workshop mod was
silently cut from the order. And a dependency id differing only in CASE dropped the mod, a
behaviour nobody observed. FIXED in `c45a54d`: `scan_installed` records `root_kind`
(game/profile/workshop by comparison with the configured roots, anything else `custom`); a
dependency is refused, and an edge cut, ONLY between the game and profile roots
(`_loadorder.crosses_isolated_roots`); Workshop and custom roots keep the pre-F141 pooled
model and are disclosed as UNMEASURED (`_registry._root_notes`); a case-only id match is
SATISFIED with a note. The records ride on `ModList.notes` and reach every caller that orders
the set (`c45c46c`). Still UNMEASURED, each disclosed where it applies: anything about the
Workshop root; whether the engine compares dependency ids case-sensitively; where a game-root
mod that waits a pass falls relative to profile-root mods; what the engine does with a nested
patch on an active mod that loads LATER (Tier B now WARNs instead of a path ERROR, `17e8346`).

**RE-DERIVED BY:** `tests/test_extension_roots.py` (each of the four original clauses mutated
separately is killed; the Workshop and case rules by
`test_a_required_dependency_on_a_WORKSHOP_mod_resolves`,
`test_a_WORKSHOP_mod_requiring_a_game_or_profile_mod_resolves`,
`test_an_optional_edge_to_a_WORKSHOP_mod_still_orders`,
`test_the_rule_follows_the_KIND_not_the_position`,
`test_a_case_only_dependency_mismatch_is_SATISFIED_and_noted`,
`test_a_case_only_match_across_game_and_profile_is_still_refused`),
`tests/test_load_order_notes_reach_users.py`, `tests/test_tierb_nested_later_target.py`
(`test_a_nested_patch_on_a_LATER_loading_active_mod_is_a_WARN_naming_it` and its twins) and
`tests/test_load_order_probe.py` (`test_round2_probes_exist_with_their_roots`,
`test_every_chain_probe_carries_a_proof_op`).


## F142 — a zero certified by a blocklist of narrowing SHAPES, and a negative licensed over an unreadable catalog · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-09-26

**Found 2026-09-26 by the v3.3.0 release review.** `tools/basex/ask.py` prints "NEGATIVE
CONFIRMED over N of M documents" only when the query addressed every document it scores the
zero against. The 2026-09-25/26 fixes (`d1d92b3`, `bafce2d`) refused a scope narrowed outside
the call's arguments by listing the SHAPES of narrowing it had seen: a predicate, a `where`
or a comparison on `document-uri`/`base-uri`/`db:path`. The reviewer walked **11 of 12**
rewrites past that list -- `[position() le 9]`, `subsequence()`, a `let` then a `where` on
the variable, an arrow into `contains()`, `tokenize(...)[3]`, `switch`, a `filter()`
callback, `db:node-pre`, `collection#1(...)`, `string-length(...) < 30` -- and each printed
NEGATIVE CONFIRMED over the whole database, rc 0. A list of shapes cannot converge: every way
of USING a value is a new shape.

**Fix (`692203f`).** The rule is about TOKENS. The query, with comments removed and string
literal contents blanked in one lexing pass, is searched anywhere for a token that can select
documents by where they are rather than what they contain: identity (`document-uri`,
`base-uri`, `path`, `node-pre`, `node-id`, `generate-id`), position (`position`, `last`,
`subsequence`, `head`/`tail`, the XQuery 4 callbacks, numeric predicates such as `[1]`, FLWOR
`at`/`count`/`window`), node order (`<<`, `>>`, `is`), the `util:`/`hof:`/`array:`/`map:`/
`random:` modules, and indirect routes (`#` function references, `Q{...}` EQNames, string
evaluation). If one is present the query still RUNS and its hits stand; only a zero is
withheld (rc 4, naming the token). Selecting by content is unaffected. The old pre-run
refusal is gone, so `$d => document-uri()` runs and reports its hits.

**Residual limits, stated in `ask.py`.** A quote in a direct element constructor's TEXT
content can desynchronise the string blanking; a user-defined function in a prolog is not
followed (a prolog already makes the count wrapper fail, and that zero is refused on its
own); and the rule over-withholds on purpose -- `//ware[1]` loses its certificate, because a
text scan cannot tell a positional predicate on documents from one on elements.

**The same review found F35's shape in `coverage.py` (`803ea90`).** `_modfiles.overlay_vpaths`
records an overlay whose packed catalog cannot be read as ONE failure, and
`coverage_effective` counted it as one "vpath with no effective tree" -- a disclosed absence
that still licenses a negative. MEASURED by the reviewer: status `complete`,
`supports_negative_claim` true, "NEGATIVE CONFIRMED over 10000 of 10000 documents", while
every packed member of that catalog was never enumerated. Now coverage is `unexplained`,
`coverage-x4eff.json` names the catalog in `unreadable_catalogs`, and `ask.py`'s refusal
names it.

The 10%: the token set is a closed list too, of NAMES rather than shapes; a document-selecting
route that spells none of them (a new BaseX module, say) would be certified. The indirect
class (`#`, `Q{`, evaluation) closes the known ways of spelling a listed name differently.

**RE-DERIVED BY:** `tools/basex/test_ask.py` --
`test_a_ZERO_from_a_query_that_can_narrow_by_address_is_NOT_certified`,
`test_the_uncertified_zero_NAMES_the_token_it_saw`,
`test_TWIN_a_whole_database_content_query_is_still_certified`,
`test_TWIN_the_same_queries_RUN_and_their_hits_are_reported`,
`test_an_unreadable_CATALOG_is_named_in_the_refusal`; `tools/basex/test_coverage.py` --
`test_an_UNREADABLE_CATALOG_does_not_support_a_negative` and its two twins;
`tests/test_audit0924_gates.py` --
`test_bx1_a_zero_from_a_query_that_can_narrow_its_documents_is_not_a_negative`.


## F143 — the removal table records a node's path at the instant it was removed, not its base-tree position · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-09-26

**Found 2026-09-26 by the v3.3.0 release review (gates lane).** The effective store's
removal records (`removed.node_path`) carry the path the node had AT THE INSTANT of removal
(`_merge._path_of`), after every earlier removal had already shifted its later siblings.
x4compat's targets are positions in the UNMODIFIED base tree. `gates/cross_tool.py` bucketed
an `ENTITY_REMOVED` HARD row (the entity is in the base tree, gone from the store) as
explained and never checked compat's winner against who removed it; matching it by exact
path would blame the wrong node. MEASURED on the real install: one mod's five ware removals
were recorded as `ware[1703]`, `[1704]`, `[1695]`, `[1696]`, `[1697]` for base wares 1703,
1705, 1695, 1697 and 1699 -- **3 of 5 records carry a shifted path; two of them (1704, 1696)
name base wares the mod never removed, and two removed wares (1705, 1699) sit at no recorded
path.**

**Fix (`39ff4a3`).** `_replayed_removers` replays the records in application order over a
copy of the base tree (DLC-sourced records skipped, the tree already has them) and turns
each instant path back into a base node; the `removed_by` branch uses the same replay,
falling back to the exact lookup only when the target does not resolve in the base tree.
Real install: HARD rows checked 32/32 -> 37/37, the 5 formerly-bucketed rows all agreeing,
0 failures.

**Not modelled:** a mod INSERTING into the middle of a sibling list is not in the removal
table, so a later removal among those siblings is replayed without that shift. **OPEN
lead:** other consumers of `removed` (x4effective's provenance, the other gates) were not
checked for the same exact-path assumption. The 10% is that lead.

**RE-DERIVED BY:** `tests/test_cross_tool_semantics.py` --
`test_an_ENTITY_REMOVED_row_whose_owner_did_not_remove_it_is_a_FAILURE`,
`test_an_ENTITY_REMOVED_row_agrees_through_SHIFTED_removal_paths`,
`test_a_removal_at_the_SAME_instant_path_but_a_different_base_node_does_not_agree`,
`test_the_replay_skips_DLC_removals_already_in_the_base_tree`.


## F144 — the installers' prune lists were a hand-kept copy of `.gitignore`, and drifted · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-09-27 (git sources)

**Found 2026-09-26 by the v3.3.0 release review (scripts lane).** `install.sh` and
`install.ps1` walked the source folder and skipped what their prune lists named. Those lists
restated `.gitignore` by hand and had drifted from it: an install from a git checkout copied
**33 untracked files, 2.3 GB** -- the BaseX databases (`tools/basex/basex/data`), `_eff/`,
`stage-manifest.json`, the coverage manifests and a `.pytest_cache`. A release zip holds only
tracked files, so a zip install was unaffected.

**Fix (`c3f8e47`).** When the source is a git work tree, both installers copy `git ls-files`,
filtered through the same prune and keep-local lists, under two clauses each with its own
falsification twin: the source is the TOP of its work tree, and the tracked set covers every
copy item present on disk. Otherwise the disk walk runs as before, with a note saying why
when the source is a checkout. The derived BaseX paths are in KEEP_LOCAL (never copied in,
never deleted out) -- not the prune list, whose second meaning is a delete in the
destination and would erase a user's built databases on every upgrade.

**Still open:** a NON-git source (an unpacked folder that is not a zip and not a checkout)
still depends on the hand-kept lists. The 5%: that path.

**RE-DERIVED BY:** `tests/test_install_over_existing.py` --
`test_a_GIT_CHECKOUT_source_copies_only_its_TRACKED_files`,
`test_a_PLAIN_FOLDER_source_still_copies_what_is_on_disk_minus_the_derived_paths`,
`test_TWIN_a_source_that_is_not_the_TOP_of_a_populated_work_tree_keeps_the_walk`,
`test_the_DESTINATIONS_own_BaseX_databases_survive_an_upgrade`.


## AUDIT-2026-10-01-TOOLS — false negatives and unreadable input boundaries · FIXED (reviewed, E2E)

Independent hands-on audit on `aa57efc`; no implementation fixes. Full evidence,
controls, confidence, and limitations are in
[the audit report](../../../docs/audits/2026-10-01-tools.md).

- BaseX interpolates `attr` input as query syntax without name validation;
  `name[false()]` receives a complete-database negative certificate (TA-01).
- BaseX's numeric-predicate scan misses `if/then/else` returning a position;
  a known cue disappears when only the first document is searched, yet the
  complete-database certificate is printed (TA-02).
- Xref silently ignores a shortened TSV row, then describes the remaining
  rows as a real negative; non-numeric line fields instead crash (TA-03).
- Invalid DEFLATE saves and corrupt SQLite stores escape the expected
  unreadable-input handlers (TA-04/05).
- Outside-repository older tools: empty-template scaffold success and legacy
  deploy `-O` / junction guard failures (TA-06/07/08).

All findings have positive or refusal controls. Raw evidence stays in the
untracked audit worktree. The existing unit suite passing is not coverage for
these newly exercised adversarial inputs. Do not mark these fixed without new
regression tests, review, and fresh CLI verification.

2026-10-02 follow-up: regression-backed repairs are installed in toolkit/dev/x4cat
after review and E2E (toolkit 2914 passed / 49 skipped). Existing production
artifacts require explicit rebuild for fresh negative claims. See
[repair status and compatibility changes](../../../docs/audits/2026-10-02-tool-fixes.md).
The findings above describe the audited baseline, not the patched behavior.

## F145 — the PowerShell/cmd guard's stated residuals after the v3.3.0 release review · **SCOPE (measured)** · confidence 90% · ✅ FIXED 2026-09-27

**Found 2026-09-26/27 by the v3.3.0 release review** (hooks reviewer: 1 CRITICAL, 3 IMPORTANT,
all in the PowerShell front-end and cmd/PowerShell carrier handling this release added). The
CRITICAL: a PowerShell delete whose target reached `Remove-Item` through the pipeline or a
variable holding command output (`Join-Path`, `Get-Item`, `gci`, `Resolve-Path`) became an
unresolved placeholder with nothing marked unknown, so the rules ALLOWED it -- against the
translator's own contract that an unresolvable write target asks. The IMPORTANTs: PowerShell
hosts fed by `-CommandWithArgs`, `-File -` or stdin; cmd.exe `/R`, `^`-escaped verbs and
`cd /d`; and unmodelled .NET writers / scriptblock execution.

**Fixed** (hooks lane, test-first): one resolver for delete targets (literals, variables holding
command output, loop variables, `$_`, `.FullName`), which DENY like the literal form and ASK when
unresolvable; host payloads translated; cmd carriers un-escaped and `cd /d`-aware; .NET writers
and scriptblocks modelled. Coordinator verification then found one more: a cmd.exe delete of an
UNQUOTED path containing a space (`cmd //c "rd /s /q C:\...\X4 Foundations"`) split into two
operands that matched no root -> fixed by judging rejoined operand spans (`6d7e702`).

**Residuals, by measurement not by guess:**
- An unresolvable PowerShell WRITE target is judged by its TEXT (fires only if it names a
  protected root), exactly like Bash writes. Failing closed on it was MEASURED over 50,061
  historical commands (48,439 Bash, 1,524 PowerShell): +28 asks, every one a false positive,
  0 catches -- so it was not shipped.
- A delete whose target is COMPUTED (a list read with `Get-Content`, an indexed nested array)
  ASKS: 2 of 1,524 historical PowerShell commands, both outside X4.
- Writer methods on unknown objects (`$xml.Save(path)`) are not modelled.
- A cmd.exe delete of an unquoted spaced path is judged on rejoined operand spans of at most 12
  words; the carrier's cmd payload under Bash-syntax mutation is unit-tested, not fuzzed.

History replay, old vs new rules over those 50,061 commands: 273 verdicts change, all classified
(254 false-positive bare-python denies REMOVED; 8 PowerShell syntax errors ask->deny, intended;
the rest intended or true positives, 1 false positive in a hook-probe script that quotes
delete commands). fuzz-guard 0 bypasses; verify-hook-tests 0 of 85 mutants uncaught.

**RE-DERIVED BY:** `.claude/hooks/test_audit0924_hooks.py` (the `TestRR*` classes, end to end on
fake roots) and `.claude/hooks/test_hook_facts.py` (`TestRRCmdCarrier`, `TestRRPreArcBash` and
the bare-python unit tests).


## F146 — Grep and Glob honour `.gitignore`, so a git-ignored search root is INVISIBLE, and the packed-archive advisory explained the zero wrongly · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-02 (hook + agent text)

**Found 2026-10-02 by a fresh-session E2E check.** The `mod-research` agent reported "no
`content.xml` under `extensions\`" while 133 were there. It then explained the zero as "the mod
is packed" -- the explanation `search-scope.sh` had just offered it -- although a mod's
`content.xml` is always a loose file.

**Root cause (MEASURED on the reference machine).** The game root is a git repo whose
`.gitignore` is a whitelist (`*`), and Claude Code's Grep and Glob honour `.gitignore`:
- `rg --files` rooted in `extensions/`: 0 of 133 manifests; with `--no-ignore`: 133.
- Grep: 0 results in all 4 folder-rooted forms tried (no path; the game root with a glob;
  `extensions/`; one mod folder). Naming a single FILE works.
- Glob is inconsistent: `*/content.xml` rooted at `extensions/` -> "No files found";
  `**/content.xml` and `extensions/*/content.xml` from the game root -> found.
- A first theory -- the parentheses in `Program Files (x86)` -- was FALSIFIED: `*/f.txt` worked
  under folders named `paren(x)`, `with space` and `both (x)`. `reference\` is not in a git repo
  and is fully visible (510,710 of 510,710 files).

**Fix.** `search-scope.sh` runs `git check-ignore` on the search root (the `path`, else the
payload's `cwd`; ~20 ms): a Grep rooted in an ignored folder is DENIED with the way out
(`rg --no-ignore`, or a single file); a Glob there gets an UNRELIABLE-ZERO advisory; a Grep
rooted ABOVE an ignored `extensions/` leads its advisory with that cause instead of blaming
packing. `mod-research` and `cross-file-impact` now say how to search installed mods.

**Still open:** ripgrep also honours `.ignore`/`.rgignore` files, which `git check-ignore` does
not read; and why Glob finds ignored files for some patterns is an observation, not a
documented contract. The 5%: those.

**RE-DERIVED BY:** `scripts/test-hooks.sh` -- the GIT-IGNORED ROOT block (a fixture game with
a whitelist `.gitignore`: Grep in an ignored folder denies, no-path with an ignored cwd denies,
Glob advises, an un-ignored folder of the same repo is allowed, the above-root advisory names
`.gitignore`).

## F147 — a RELATIVE shell operand reached no path rule: the guard ignored the payload's `cwd` · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-02 (Plan 2 lane F)

**Found 2026-10-02 by Plan 2 lane B** (4 of 4 probes, Claude hook and Codex chain alike). A live
Codex run OVERWROTE a file in `reference/` through a relative path. With the payload `cwd` = the
folder that holds `reference/`, on the deployed guard: `rm -f reference/libraries/__p.xml` and
`echo x > reference/libraries/__p.xml` were ALLOW, while the absolute spelling and
`cd <folder> && rm -f reference/...` denied.

**Root cause (READ).** `hook_facts.facts()` called `cwd_track(c)` with no base, so a relative
operand with no preceding `cd` joined to "" and reached no path rule. `join_cwd`'s docstring
justified that with "the hook does not know the shell's starting directory". That is no longer
true: Claude Code and Codex both send `cwd` at the payload's top level. `x4guard check --kind
shell` sent no `cwd` at all.

**Fix.** `session_cwd(payload)` seeds `cwd_track`, and `x4guard` puts `os.getcwd()` in the shell
payload. The Codex adapter has already entered the Codex payload's cwd. Edit/Write paths were
already absolute: 0 of 12,396 historical calls carried a relative one (MEASURED), so
`protect-files.sh` is unchanged.

**Narrowed by measurement, not taste.** The OLD-vs-NEW replay covered 53,828 unique historical
Bash/PowerShell commands, each with its own transcript cwd. On the unnarrowed prototype, 113
rows changed: 105 advise, 5 deny and 3 ask, and every deny and ask was a false positive. Each
narrowing below removed a class:
- a `cd` to an unresolved target, taken from the SEED, is unknowable (`cd "$X4_TOOLKIT" && sed
  -i ...` from the game root);
- parse debris (quote, paren, redirect, pipe, backtick) and Windows device names (`2>nul`)
  keep their pre-seed directory;
- carriers are seeded only when nothing changes directory;
- `git clean`/`reset --hard` and unknown PowerShell cmdlets, which ASK outside the profile,
  stay unseeded.

Shipped: **3 of 53,828** rows change facts: 2 allow->advise and 1 deny that stays deny. Rows where
OLD was stricter: 0. New asks: 0. New denies: 0. Detail:
`docs/superpowers/measurements/2026-10-02-cwd-seed-replay.md`.

**Still open (the 5%).**
- Codex's shell `workdir` is invisible to hooks, so a relative operand is judged against the
  session cwd.
- A bare `git clean -fdx` from a toolkit or game cwd still reaches no rule, as before.
- Two historical commands get a false advisory from mis-segmented quoted text. That is a
  segmenter limit, not the seed.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py`
`TestRelativeOperandsResolveAgainstThePayloadCwd` and
`TestTheSeedIsNarrowedWhereTheReplayFoundFalsePositives`, with 17 verify-hook-tests mutants, one
per clause. Also `.claude/hooks/test_audit0924_hooks.py` `TestLaneFRelativePathsUseThePayloadCwd` (the 4-row
table E2E), `tools/x4validate/tests/test_x4guard_check.py` `test_F_*`, and the conformance extras
`*relative-delete*`.

## F148 — a bare destructive `git clean` / `reset --hard` from an X4 session folder reached no rule · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-03 (Plan 3 lane J, decision J-Q1)

**What.** F147 seeded relative operands with the payload's `cwd`, but deliberately left
`git clean -f` and `git reset --hard` UNSEEDED. Their rule ASKS, and the user had ruled out new
prompts. So `git clean -fdx`, run with the game folder as the session folder, was ALLOWED
(MEASURED with the deployed `x4guard check`, cwd = the game root). The game-root repo's
`.gitignore` is a whitelist (`*`), so every untracked file there is ignored, and `-x` would remove
them (INFERRED from the `.gitignore`; never run).

**Fix (user decision J-Q1: a DENY, never a prompt).** `git_wipes_ignored_targets` is the same
destructive-git parse as `git_wipes_worktree_targets`, limited to `clean` with `-x`, `-X` or
`-d`, and `reset --hard`. It is judged from the SEEDED cwd, and `git_wipe_from_session_dir` is
true only when the explicit-folder fact `git_wipes_x4_dir` is false, so `cd <root> && ...` and
`git -C <root> ...` keep their ASK. `protect-bash.sh` denies it with a reason that names
`git -C` as the way to state intent.

**Scope left open.** A plain `git clean -f` (no -x/-X/-d) from an X4 folder still reaches no rule:
it removes untracked, non-ignored files, of which a whitelist repo has none. Codex prefix rules
cannot see the cwd, so the rule lives only in the hook (codex-rules.yaml row
`git-wipe-from-session-dir`, hook_only).

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` `TestJQ1BareGitWipeFromAnX4SessionDirIsADeny`
(one twin per clause: cwd outside, no cwd, no -x/-X/-d, dry run / unforced, soft reset, the
explicit-folder form, a `cd` away) and `tools/x4validate/tests/test_x4guard_check.py`
`test_JQ1_*` (end to end through protect-bash.sh). No verify-hook-tests mutant was added for the
new line; the orchestrator may anchor one.

## F149 — the `test-hooks.sh` 1/187 flake: 0 of 16 under synthetic load · **SCOPE (measured, open)** · confidence n/a

**What was known.** Twice, under heavy load, one probe of 187 failed, and the probe was never
recorded. `decide()` discarded the hook's stderr and exit code and read an EMPTY stdout as
`allow`, so a hook that crashed looked exactly like one that allowed.

**Instrument (shipped).** A failing `decide()` line now ends
`[hook rc=N; stderr: <first 300 chars>]`. Counts, `EXPECT` and the `X4_DECIDE_DUMP` record are
unchanged. Falsified on a scratch copy: a flipped expectation printed `[hook rc=0; stderr: ]`,
and a missing hook printed `[hook rc=127; stderr: ... No such file or directory]`.

**Reproduction attempt (MEASURED 2026-10-03).** 4 concurrent suite lanes, with `burn.py`
running 24 busy-loop processes on 28 logical CPUs, and 3 other lanes' tests sharing the machine.
16 runs completed (4 per lane), at 711-1187 s each against 126 s idle. Every run ended
`186 passed, 1 failed`, and in all 16 the one failure was the identifier scan flagging 3 lines
of the Plan 3 plan docs. That finding is pre-existing on master and fixed there in a791117, so
it is not the flake. **0 of 16 reproduced it.** The 5th round was stopped unfinished: the
orchestrator killed the burners because they held the machine at 100% CPU, which breaks the
user's 80% rule. Any further hunt must stay within that rule: at most 8 idle-priority burners,
at most 10 minutes per sample, and only when the machine is under 60% before the sample.

**Classification:** decision-tree row 4 (NOT REPRODUCED). No fix. The instrument makes the next
occurrence self-identifying. Recommended: tee every gate run's `test-hooks.sh` output to the
scratchpad so that occurrence is kept.

## F150 — the write guard's ALLOW did not mean the write could succeed, and its deny contradicted the instructions · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-03 (Plan 3 lane N) · adjacent hole ✅ CLOSED 2026-10-04 (`17de4cc`)

**Found 2026-10-03 in a live Codex run** (game root = project root), MEASURED by the orchestrator:
the agent could not save its investigation notes anywhere it was told to.

- `x4guard check --kind write --path <game>/KNOWLEDGEBASE.md` said ALLOW, and the write failed:
  `scripts/x4lock.py` keeps that file read-only. The guard judged the PATH and never the FILE, so
  its allow answered an adjacent question ("may you?") and nothing said why the write failed or
  that `x4lock.py unlock` was the way through.
- `<game>/X4-NOTES.md` was hard-denied as a game-installation file, while `core.md` tells every
  agent "your own notes go in `X4-NOTES.md` in the project root". The deny said only "work in your
  mod folder".

**Fix.** (1) exactly `<X4_GAME or X4_TOOLKIT>/X4-NOTES.md` and `X4-NOTES.pre-4.0.md` are allowed,
decided on the RESOLVED path, so the same name deeper in the game tree stays denied. (2) A write to
an existing read-only file asks `x4lock.py protected <path>` (x4lock's own manifest, exit 0/1/2)
and, when x4lock manages it, ADVISES the exact unlock, edit and relock commands. A writable or
absent file pays no subprocess (`-f`/`-w` are builtins; `-w` honours the Windows read-only
attribute under Git Bash, MEASURED with an unlocked control in three path spellings). (3) The
game-install deny names where notes, game facts and mod files go.

**Still OPEN, found while fixing (MEASURED, 1 probe each plus a control, 2026-10-03).** The NAME
whitelist `*/claude.md|*/agents.md|*/knowledgebase.md` matches those names ANYWHERE.
`<game>/libraries/KNOWLEDGEBASE.md` and `<game>/extensions/x/CLAUDE.md` are ALLOW, while the
controls `<game>/libraries/a.xml` (deny) and `<game>/extensions/x/a.xml` (ask) are not. It is the
same shape as the X4-NOTES twins above. Not changed here: a mod may legitimately ship its own
`CLAUDE.md`, so narrowing it needs a measurement over real edits first.

**RE-DERIVED BY:** `tools/x4validate/tests/test_notes_and_locked_files.py` (19 tests through the
Claude hook, `x4guard check` and the Codex adapter; a name-only mutant and an any-read-only mutant
were each killed by a twin).

**✅ 2026-10-04: the adjacent hole is CLOSED** (v4.0 release review R1-F6 / R6-09, lane FX-S).
CLAUDE.md, AGENTS.md and KNOWLEDGEBASE.md now take the X4-NOTES path (root-exact, plus Claude's
.claude/CLAUDE.md). The measurement asked for above, MEASURED 2026-10-04: 4 of 60,228 files under
the reference machine's game root carry one of those names -- 3 at its root, 1 under
.claude/backups/ (whitelisted) -- and 0 under extensions/. RE-DERIVED BY: scripts/test-hooks.sh
(5 probes per layout; RED against master 6 of 10).


## F151 — no path config was a SILENT `<toolkit>/reference` default, and the two loaders picked the config file by different rules · **DEFECT (read + measured)** · confidence 95% · ✅ FIXED 2026-10-03 (Plan 3 lane I)

**Found while planning the config move (Plan 3 lane I), READ then MEASURED.**

- `_x4-env.sh` filled `X4_REFERENCE` with `<toolkit>/reference` when nothing configured it
  (`: "${X4_REFERENCE:=...}"`), and nothing said so: no hook suite exercised the no-config state,
  because every harness exports the roots or sets `X4_CONFIG=/nonexistent` beside an exported
  `X4_REFERENCE`. A deleted config therefore protected a guessed tree with no word anywhere.
- The two loaders disagreed on which FILE to read. Bash honoured `$X4_CONFIG`; Python ignored it.
  With `$X4_TOOLKIT` naming a toolkit that held no config, Python walked up from the cwd and could
  read an unrelated file; bash never walked. `scripts/generate-baseline.sh` sourced the config
  itself -- a third loader, in which the FILE beat the environment.

**Fix.** One selection rule, mirrored in `_x4-env.sh` and `_paths._locate_config`:
`$X4_CONFIG` (explicit; naming no file reads NONE) > `<tk>/x4-paths.env` > the 3.x
`<tk>/.claude/x4-paths.env` (deprecated, read for all of 4.x) > none; Python walks only when
`$X4_TOOLKIT` is unset. Bash records `_x4_cfg_src` / `_x4_ref_defaulted` and prints nothing per
call; the gap is named once per session (`session-canary.sh`, Claude Code and Codex) and by
`x4doctor roots.config` (FAIL). Every verdict is unchanged -- the default `reference/` hard block
stays. `generate-baseline.sh` uses the shared loader.

**Denominators (MEASURED 2026-10-03,** `docs/superpowers/measurements/2026-10-03-config-location-replay.md`).
Stage 1: the post-source shell state was identical OLD vs NEW outside 5 allowlisted names in all 3
passes (configured, legacy, unconfigured). Stage 2: 0 of 17,916 hook calls changed verdict
(1,986 shell + 1,000 edit rows x 2 trees x 3 passes), 0 ERROR, all 18 controls held.
⚠ The replay's own first stage-1 run compared 13 of ~130 variables (a split at the first of two
`@@FUNCS@@` markers) and called that identical -- caught by reading the counts, then made a refusal.

**RE-DERIVED BY:** `tools/x4validate/tests/test_config_precedence_agrees.py` (the 8-row location
matrix for both loaders plus their agreement) and
`.claude/hooks/test_audit0924_hooks.py::TestLaneIConfigLocation` (the hard block without a config,
both locations read by the hooks, the banner per state).


## F152 — a CRLF path config put a carriage return on every guard root under POSIX bash · **DEFECT (measured; verdict effect inferred)** · confidence 85% · ✅ FIXED 2026-10-04 (Plan 3 CI lane)

**Found 2026-10-04** by CI run 37172347642 (ubuntu): `test_a_CRLF_config_is_still_recognised_as_UNCHANGED`
failed because the OpenCode deny-rule renderer refused `X4_REFERENCE contains a line break`. The renderer
was right; the cause is upstream. `. x4-paths.env` under a POSIX bash KEEPS the CR of a CRLF line
(MEASURED in an ubuntu:24.04 container: `X4_REFERENCE="/a/b"<CR>` -> `/a/b<CR>`); Git Bash strips it,
which is why every Windows run, local and CI, was green.

**Scope.** PRE-ARC: every shipped release's `_x4-env.sh` sourced the config the same way, so a Linux or
macOS user whose config was saved with CRLF had every guard root ending in a CR. **INFERRED, not run end to
end:** a path compare against `<root><CR>` matches nothing, so the hard block on `reference/` (and every
other root rule) would not fire for them. What would confirm it: a POSIX run of `test-hooks.sh` with a
CRLF config before the fix.

**Fix.** `_x4-env.sh` strips one trailing CR from the configured keys only, in-shell; the restored
environment never carried one. **RE-DERIVED BY:** `tools/x4validate/tests/test_config_precedence_agrees.py`
(bash and Python read one CRLF file to the same values, with no CR in either).

**SUPERSEDED IN PART 2026-10-04 (F156):** the config is now PARSED, and the parser drops a
trailing CR from EVERY line, for every key -- the per-key strip above is gone with the `source`
it compensated for. The same test row still pins it (`crlf` in the grammar matrix).

## F153 — the guard read apply_patch by a grammar no agent uses · **DEFECT (measured)** · CRITICAL · confidence 95% · ✅ FIXED 2026-10-04 (v4.0.0 review, fix lane FX-G)

**Found 2026-10-04** by the v4.0.0 release review (R2-F1): `patch_paths.parse_patch` decided header-ness with
`line.startswith("*** ")` on the RAW line. Codex 0.160.0 trims the line first (`streaming_parser.rs`
`process_line`: `line.trim()` in the Begin/Add/Delete states). So

    *** Begin Patch
    *** Add File: h.txt
    +hi
      *** Delete File: <reference>/libraries/wares.xml
    *** End Patch

was an Add to the guard (the indented line read as Add content) and an Add plus a DELETE to Codex. MEASURED end
to end: the rendered `.codex/hooks/codex_adapter.py` printed nothing (allow) for it and a deny for the flush form.

**Scope -- MEASURED over 67 shapes** (`scripts/capture-codex-patch-oracle.py`, each run through
`codex --codex-run-as-apply-patch` in a scratch folder): before the fix, 8 shapes had Codex change a file the
guard never named (an indented header after an Add or a Delete, in five whitespace spellings: space, tab, VT,
FF, NBSP, U+3000), and 2 more had the guard name a different file than Codex touched (Codex keeps the space
in `*** Delete File:  x` as part of the name; the guard stripped it). A further 18 shapes were read as patches
the guard would judge while Codex refused them -- harmless (Codex applies nothing) but not a match. The
grammar was introduced in this release arc (Plan 2 lane B), so no 3.x user had it.

**OpenCode, same root cause.** The OpenCode adapter used the same Codex-shaped parser, but OpenCode runs
its OWN (`packages/opencode/src/patch/index.ts`, READ at v1.18.34): headers only at column 0, any unknown line
skipped, `*** Add File:x` accepted, paths trimmed. Codex's grammar refuses `*** Delete File:x`, so the
guard turned an OpenCode delete into an inert refusal instead of judging its path.

**Fix.** One port per agent, each held to that agent: `parse_patch` ports Codex's parser (boundaries
from `parser.rs`, then `streaming_parser.rs` line by line: trimmed headers in the Begin/Add/Delete states,
RIGHT-trimmed only inside an Update -- where an indented header is a context line -- Rust's whitespace set,
not Python's, verbatim paths, the `<<EOF` leniency and `*** Environment ID:`); `parse_patch_opencode` ports
OpenCode's. **RE-DERIVED BY:** `tests/test_patch_paths.py` -- every row of the Codex record
(`fixtures/codex/0.160.0/patch_grammar_oracle.json`) must name exactly the files Codex touched or refuse
where Codex refused, and the vendored OpenCode parser (`fixtures/opencode/patch-v1.18.34.mts`) is run under
node over the same 67 shapes plus 18 OpenCode-specific ones. Seven clause mutants of the port (raw-line
header test, full trim inside an Update, Python's whitespace set, stripped paths, no heredoc leniency, no
environment id, any Add content) each turn the record red. Re-capture the record on every Codex upgrade.

## F154 — a `cd` to a root VARIABLE left the guard blind to every relative operand after it · **DEFECT (measured)** · PRE-ARC · confidence 95% · ✅ FIXED 2026-10-04 (v4.0.0 review, fix lane FX-G)

**Found 2026-10-04** by the v4.0.0 release review (R2-P1). `cd "$X4_REFERENCE" && rm -rf libraries` and
`cd "$X4_GAME" && rm -rf extensions` were ALLOW; the direct spellings (`rm -rf "$X4_REFERENCE/libraries"`) were
refused. `cwd_track` resolved a `cd` target only through the command's own assignments, so a root variable
stayed unresolved; from the session seed an unresolved target makes the directory unknowable, and a relative
operand joined onto an unknowable directory reaches no path rule. The operand rules already treated the
variable NAME as identifying its root (`ROOT_VARS`, `root_vars_named`); the directory tracker did not.

**Scope.** Pre-arc: every 3.x release has the same `cwd_track`. Same blind spot for the lift rule's icacls
operands (F155).

**Fix.** `subst_root_var` turns a LEADING root variable into its configured root, in `cwd_track` (seeded and
unseeded tracks). An assignment in the command wins (it is resolved first); an unset root is not substituted;
what follows the variable is kept (`$X4_REFERENCE/$SUB` stays inside the root). **MEASURED blast radius:**
OLD (f35ff82) vs NEW `hook_facts.facts()` over the 52,276 Bash commands of the history corpus, same roots and
payload cwd: 276 rows changed, 275 of them only the informational `cwd` fact (no rule reads it; READ, two
greps), 1 `rm_in_x4_dir` (an advisory) and 1 `search_rooted_workspace` (a deny: `cd "$X4_TOOLKIT" && grep -r
... .`, which the literal-path spelling already got). 0 errors. **RE-DERIVED BY:**
`.claude/hooks/test_hook_facts.py::TestARootVariableCdIsTheRoot` (each cd form judged like its literal-path form, with twins
for an unset root, an assignment, a non-root variable and a sibling suffix) and the verify-hook-tests mutants
"a cd to a root variable lands in that root", "an unset root is never substituted", "only the LEADING variable
is replaced".

## F155 — the lift rule judged icacls operands where they are not run · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-04 (v4.0.0 review, fix lane FX-G)

**Found 2026-10-04** by the v4.0.0 release review (R2-F2). `_lifts_reference_deny` (added this release arc, Plan 2
lanes D/E) compared `resolve(operand)` with the reference root: no join onto the segment's directory and no root
variables. ALLOW, where the rule must ask: `icacls "$X4_REFERENCE" /reset /T`, `icacls reference /reset /T` run
from the folder holding reference/, `cd "$X4_REFERENCE" && icacls . /reset /T`; and `"$X4_PYTHON"
scripts/x4refguard.py remove`, because the runner test was a fixed prefix list (python/py/uv/uvx/x4refguard).

**Fix.** Each operand is `join_cwd(cwd, subst_root_var(resolve(...)))`, as the other path rules resolve theirs;
a verb that is itself a variable counts as a runner, and the common launchers were added. **RE-DERIVED BY:**
`test_hook_facts.py::TestLiftingTheReferenceDenyIsJudgedWhereItRuns` (each shape, with twins: the same relative
operand elsewhere, a non-recursive reset of the parent, a separate tree, a read, a variable runner doing
`status`, an `echo` mention) and the verify-hook-tests mutants for each clause. The rule is in-arc: no 3.x
release had it.



## F156 — the guards RAN the path config as shell code: an `exit 0` line silenced every hook, and `X4_GUARD=off` in it switched them off · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-04 (v4.0 release review, lane FX-S)

**Found by the v4.0 release review (R1-F1 in-arc, R1-P1 pre-arc), MEASURED.** `_x4-env.sh` loaded
`x4-paths.env` with `set -a; . "$cfg"; set +a` -- the file was CODE to every guard, on every tool
call, while `_paths` PARSED it as data. An agent can write that file (protect-files allowed
`<toolkit>/x4-paths.env` in the separate layout). Three consequences, each reproduced:

- **PRE-ARC (every release since the loader existed):** a line `exit 0` ended the sourcing hook
  before it emitted anything, and an empty hook output is ALLOW -- the reference/ hard block
  included. RED probe: master's protect-files.sh returned `allow` for a Write into a configured
  reference/ whose config began `exit 0`; protect-bash.sh likewise. Any other command in the file
  ran on every tool call.
- **IN-ARC (v4.0's X4_GUARD):** `X4_GUARD=off` in the file turned every deny into an advisory,
  under a banner and an advisory both saying "X4_GUARD=off at launch", and x4doctor reported the
  environment's (unset) value as OK.
- The sourcing loader kept the file's value for every key except a named twelve, so an exported
  `X4_BACKUPS` or `X4_PYTHON` lost to the file in bash while it won in Python.

**Fix.** `_x4_cfg_read` PARSES the file in-shell (no process), by the grammar of
`_paths.parse_env_report` (new): `[export ]KEY=value`, KEY `X4_[A-Z0-9_]+` or `XRCATTOOL`; `'..'`
literal; `".."` with `\\ \" \$ \`` and `$NAME`/`${NAME}` expansion; unquoted ` #` comments; CR and BOM
dropped for every key (supersedes F152's per-key strip). A line that is not configuration is IGNORED
and recorded as LINE:REASON (`shape`, `key`, `subst`, `operator`, `guard`), never a value; the
session banner names it. `X4_GUARD`/`X4_GUARD_CHECK` come from the launch environment only.
protect-files.sh DENIES an agent Edit/Write of the config (both locations, an explicit X4_CONFIG);
protect-bash.sh DENIES a command that writes or deletes it (hook_facts re-run with the config as
its only `reference` root, only for a command whose text names it). The environment wins for every
key. x4doctor (lane FX-I) reads X4_GUARD from the environment and reports a config line.

**Residual (accepted, stated in protect-bash.sh):** a write whose target text never names the file
(`x4-pa""ths.env`, a variable set outside the command, a python -c open()) is not seen by the Bash
rule; this stops accidents, not intent. The config is still read, so a USER who writes a bad line
gets it ignored and named, not run.

**Denominators (MEASURED 2026-10-04).** Grammar: 30 rows through BOTH loaders + 2 twins
(`tests/test_config_precedence_agrees.py`); RED -- old bash 14 of 32 failing, master `_paths` 30 of 30
rows (no report API; 9 with wrong values). Hooks: 17 probes in `scripts/test-hooks.sh`, RED against
master 12 of 17. Verdict replay, OLD f35ff82 vs NEW: lane I's harness (same seed-20261003 sample: 1,986 shell + 1,000 edit rows), OLD f35ff82 vs NEW 3e765e5, 3 passes, 17,916 hook calls: configured and 3.x-legacy config 0 of 5,972 changed; unconfigured 2 of 2,986 changed -- both a write under Documents/Egosoft/X4 going advise -> ask, the predicted R1-F3 class, written down before the run; 0 ERROR; all 18 controls held. Stage 1: the 12 configured X4_*/XRCATTOOL variables identical in value AND export flag OLD vs NEW (5 unconfigured).

**RE-DERIVED BY:** `tests/test_config_precedence_agrees.py` (`test_both_loaders_parse_the_config_by_one_grammar`,
the X4_GUARD twin), `scripts/test-hooks.sh` section "the path config is DATA, never run".


## F157 — F146's "ignored root" test answered an adjacent question: a root git ignores AS A DIRECTORY is still fully visible to Grep · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-04 (v4.0 release review, lane FX-S)

**Found by the v4.0 release review (R1-F4), MEASURED.** search-scope.sh denied a Grep whenever
`git check-ignore -q -- <root>` said the root was ignored. That is not the question Grep's
visibility turns on: ripgrep reads every file under a root it was GIVEN unless a pattern matches the
children themselves. MEASURED 2026-10-04: `rg --files` under a `reference/`-ignored folder -> 1 of 1
file; under the game root's `*` -> 0 of 1. So in the default layout (the toolkit's `.gitignore`
lists `reference/`) a Grep of `<toolkit>/reference/libraries` was DENIED with a reason that was false.

**The reviewer's suggested fix would not have worked, and the instrument said why.** "Deny only when a
child path is also ignored" fails because `git check-ignore` calls EVERY child of an ignored directory
ignored (MEASURED: `build/x` -> matched by `build/`). And `rg` itself is not on PATH in a hook (Claude
Code provides it as a shell function), so it cannot be the oracle.

**Fix.** `check-ignore -v` (the same one process) names the PATTERN: directory-only (`build/`) ->
children visible, no deny; a last component that matches any name (`*`) -> invisible, deny (Grep) /
advisory (Glob); a pattern that names the root itself (`extra*`) -> no fire. One probe per clause.
The deny also honours X4_GUARD=off now (R1-F5). **RE-DERIVED BY:** `scripts/test-hooks.sh` section
"search-scope.sh: a dir/-ignored root is still searchable" (RED against master 5 of 7).

## F158 — a heredoc inside a carried command was read as commands, and one feeding a shell inside `$(...)` was not read at all · **DEFECT (measured)** · PRE-ARC · confidence 95% · ✅ FIXED 2026-10-04 (v4.0.0 review, fix lane FX-P2)

**Reported by another lane** as a false DENY ("truncating redirect onto a durable record") on a
commit whose message was fed by a heredoc. `git commit -F - <<'EOF'` itself does NOT reproduce it:
MEASURED with `x4guard check`, 160 of 160 `-F -` shapes (8 openers x 20 bodies containing `>`,
`> KNOWLEDGEBASE.md`, `CHANGELOG.md`, `x > y`, backticks, quotes) were allow. The idiom that does is
`git commit -m "$(cat <<'EOF' ... EOF)"` -- deny on 3 of 3 bodies naming a durable record.

**Root cause.** `heredoc_marker` correctly ignores a `<<` inside quotes (`echo "a <<M b"` opens
nothing), so at the top level the double-quoted substitution's body is not stripped. The `$(...)`
text is then walked by `carried_commands` -- and carried commands never got the heredoc handling the
top level gets, so each body line became a command. The same gap had a silent-allow twin:
`x=$(bash <<'EOF'` / `<delete the game>` / `EOF` / `)` was ALLOW (and `x=$(sh -s <<EOF ...)` with a
durable redirect), because `heredoc_bodies` asked whether the opener LINE's segments run a shell, and
the only segment there is `x=$(bash <<'EOF'`.

**Fix.** `_carrier_parts`: each carried command is the top level's shape -- bodies stripped, plus the
bodies that really run (fed to a shell, or fed to a PowerShell host, kept as raw text exactly as
before so nothing previously analysed goes dark, plus an expanding body's substitutions, F159).
`_opener_feeds` also tests the command after each `$(` / `<(` / `>(` / backtick on the opener line;
it only adds candidates. **Priced:** an in-process replay of 41,756 distinct historical
(command, cwd) pairs from 455 transcripts (8,776 containing `<<`), old blob vs new: **0 fact
changes, 0 errors**; controls -- a game delete True in both, `ls` False in both, and the reported
idiom True (old) -> False (new) through the same instrument. **RE-DERIVED BY:**
`.claude/hooks/test_hook_facts.py::TestAHeredocInsideASubstitutionIsStillData` (RED before the fix on 5 subtests)
and two `verify-hook-tests.py` mutants ("FX-P2: a carried command's heredoc body is stripped",
"... a shell inside the opener's substitution takes the body").

## F159 — an unquoted heredoc's `$(...)` and backticks run, and no rule saw them · **DEFECT (measured)** · PRE-ARC · confidence 95% · ✅ FIXED 2026-10-04 (v4.0.0 review, fix lane FX-P2)

**Found while reproducing F158, MEASURED with `x4guard check`:** `cat <<EOF` / `$(rm -rf "<game>")` /
`EOF` and the backtick form were both **allow**, while the bare delete is the hard block. With an
UNQUOTED delimiter bash expands the body, so the substitution RUNS; `strip_heredocs` removed the whole
body as data. A quoted delimiter (`<<'EOF'`, `<<"EOF"`, `<<\EOF`, `<<E'OF'`) expands nothing.

**Fix.** `_heredoc_open` reports whether the delimiter word carries any quote or backslash;
`heredoc_substitutions` returns the `$(...)` / backtick texts of every expanding body (any opener),
which join the carried commands. Inside a body quotes are letters (`it's` must not hide what follows),
a backslash escapes the next character, `$((` is arithmetic. The rest of the body stays data, so
`cat > notes.md <<EOF` with prose -- including prose quoting a delete -- is unchanged. **Priced** by
the same replay as F158: 0 of 41,756 changed. **Known residual (unmeasured):** a `$((...))` that
itself contains a command substitution runs it too and is skipped here, as `substitutions()` does at
the top level. **RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py::TestAnUnquotedHeredocRunsItsSubstitutions` (RED before the fix on
8 subtests; 4 twins: quoted delimiter, escaped substitution, plain body text, arithmetic) and three
mutants ("FX-P2: an expanding body runs its substitutions", "... a quoted delimiter expands nothing",
"... an escaped substitution in a body is text").

## F160 — a toolkit copy acted for ANOTHER toolkit through an inherited `X4_TOOLKIT` · **DEFECT (measured: incident)** · PRE-ARC · confidence 95% · ✅ FIXED 2026-10-04 (install red-team, fix lane FX-R)

**The incident (2026-10-04, live).** A cold, docs-only install red-team installed a second toolkit
copy (B) in a scratch folder, in a shell that had inherited `X4_TOOLKIT` naming the user's real
toolkit (A), and ran B's `x4refguard.py apply`. B's script imported B's own `_paths`, which located
the config through `$X4_TOOLKIT` -- A's `x4-paths.env` -- and resolved A's `X4_REFERENCE`: the
user's REAL reference tree. It was killed after ~2 minutes. A full census afterwards: 518,056
objects, 0 protected, state absent -> nothing was applied. The same red-team saw `x4doctor`'s
parity row compare B's deployed tree against A's source.

**Two routes, one shape.** (1) `_locate_config` took `<toolkit>` from `$X4_TOOLKIT`. (2) Even with
no config in B, the env layer's `X4_TOOLKIT` answered `reference()` by derivation
(`<X4_TOOLKIT>/reference`) -- the second route to A's tree, pinned RED by
`test_INCIDENT_B_without_its_own_config_never_derives_A_reference`.

**Population (MEASURED by grep over tools/, scripts/, bin/, tests excluded):** 36 modules import
`_paths` (all inherit the fix centrally); scripts reading `X4_TOOLKIT` themselves: `x4config._root`
(fixed), `x4doctor.Ctx` (fixed), `bin/unpack-reference.sh` via the guards' `_x4-env.sh` (fixed: it
now exports its own toolkit, or --toolkit, before sourcing), `x4refguard._dangerous` (reads it to
REFUSE more roots -- safety-only, kept), `x4lock._waiver_replacements` (kept, residual below),
`x4conformance` / `codex-e2e` / `fuzz-guard` / `test-hooks.sh` (set or snapshot it for child
processes: not a resolution of their own toolkit). The deployed GUARD copies keep reading
`$X4_TOOLKIT` by design: they live in a game root and legitimately reach a separate toolkit.

**Fix (policy: DECISIONS.md, lane FX-R).** `_paths.toolkit_root()` = `--toolkit` (`use_toolkit`) >
the toolkit the module lives in (`<root>/tools/x4validate/x4validate/`) > `$X4_TOOLKIT` only for
code outside that layout. A differing `$X4_TOOLKIT` earns ONE stderr line naming both roots, is not
used for the config, and is replaced by the acting root in the env layer (closing route 2).
`x4refguard apply/remove`, `x4config migrate --apply`, `x4lock lock/unlock` and
`bin/unpack-reference.sh` REFUSE (exit 2) under a foreign `$X4_TOOLKIT` unless `--toolkit` is given.
`x4doctor` acts for its own toolkit (`--toolkit` to choose) and names the conflict.

**Known residual (unmeasured):** `x4lock._waiver_replacements` still demands `$X4_TOOLKIT`'s config
inside a LINKED git worktree (F119's waiver); lock/unlock are refused under a conflict without
--toolkit, so it can widen a status manifest but not a write by accident. A user-level `X4_CONFIG`
naming another toolkit's file is still honoured as explicit (no installer sets one).

**RE-DERIVED BY:** `tests/test_toolkit_binding.py` -- two scratch toolkit copies A and B,
`X4_TOOLKIT=A`, B's scripts run with `X4_CONFIG=""`, `X4_REFERENCE=""` and
`X4_REFGUARD_SANDBOX` confined to B: status acts for B and names both roots; apply/remove/migrate
--apply/lock/unlock refuse; `--toolkit B` is accepted and protects B and never A (Windows, real
icacls on scratch). RED before the fix on 18 of 20 (the 2 passing were the designed twins); the
doctor's three B2 tests RED against master's x4doctor.

## F161 — the installed toolkit's own suite could not run where the setup prompt runs it · **DEFECT (measured)** · PRE-ARC · confidence 90% · ✅ FIXED 2026-10-05 (second install red-team, fix lane FX-R2)

**Finding (2026-10-04, cold red-team #2, BLOCKER 1).** `SETUP_PROMPT.txt` has every user's agent
run x4validate's suite in the installed toolkit. There `uv run pytest -q` stopped in collection:
3 errors, `FileNotFoundError` on `agent/rules/codex-rules.yaml` and two `agent/guards/adapters/`
sources, read at import. An install is runtime-only BY DESIGN (no `agent/`, no root `docs/`, no
`.github/`, no `.git`), so this was never the install's defect -- the suite assumed a checkout.

**Population (MEASURED, real install by `install.sh`, full suite with `--continue-on-collection-errors`):**
142 failed + 43 errors of 4,398 collected. Classified BY FILE (32 files; the 75 installer-harness failures in bulk, one cause confirmed by a re-run): (a) repo-only content -- agent/ sources,
generator inputs, docs/, .github/, git-only facts, and the installer harness installing FROM the
toolkit it lives in; (b) a CONFIGURED root taken for a PRESENT one -- three `needs_reference`
markers asked only `_paths.reference() is None`, and `import_gate`'s "unresolvable" check asked
only for None, while an install configures every root before anything is unpacked (SETUP_PROMPT
runs the suite BEFORE the unpack); (c) instrument artefacts of the first measurement -- X4_* roots
pinned in the environment over the install's own config, and the scratch box sitting under
%TEMP% (one test asserts the conformance run dir is NOT under the system temp). (d) this lane's own new tests run against an install made before their code (a sync artefact).
(c) and (d) were removed from the harness, not from the suite. (b) also failed in a fresh worktree of master (stash
baseline: 5 failed), green only where a real reference is unpacked.

**Fix.** `tests/_layout.py` is the one door: `require_repo(...)` skips -- reason prefixed
`REPO-ONLY` and naming what is absent -- only when the tree is an installed layout (no `agent/` at
the root) AND the content is absent; a checkout that lost such a file still FAILS.
`reference_unpacked()` is the one definition of "a reference is usable" (configured AND holding
`libraries/wares.xml`); conftest's `_environment_is_unresolvable` treats a configured root that
does not exist as unresolvable (consulted only after a gate import FAILED, so a gate that imports
still runs). Known residual (UNMEASURED): only `--agent all` installs were run end to end; an
install of fewer targets lacks e.g. `.claude/`, which several sandbox fixtures copy.

**RE-DERIVED BY:** `tests/test_layout_repo_only.py` (each clause and its twin) and
`tests/test_install_over_existing.py::test_R2B1_an_INSTALLED_toolkits_suite_COLLECTS_and_counts_its_repo_only_skips`
(a real install by each installer, then `pytest --collect-only` inside it: RED before `_layout.py`
shipped -- ModuleNotFoundError -- green after). Full installed run after the fix: 4,017 passed, 366 skipped, 6 failed (install.sh install, 17 min); the 6 had three causes, all fixed and re-run focused: a non-LiteralPath Remove-Item this lane added to install.ps1 (an in-arc regression test_installer_literal_paths caught), a token census reading agent/skills, and the freshness skip calling iterdir on a missing root. A full re-run of the install.sh install after those fixes was NOT done. A full run of an install.ps1 install (2026-10-05, Windows, `--agent all`, separate method, `--no-env`) WAS done afterwards: 4,018 passed, 377 skipped, 0 failed, rc 0, 19 min 20 s.

## F162 — `x4doctor`'s `bash.path` judged the Windows PATH, not what the hooks run · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-05 (fix lane FX-R2)

**Finding (cold red-team #2, BLOCKER 2).** On a stock Windows PATH the first `bash` is
System32's WSL stub, so the row FAILED on every fresh install -- while the toolkit resolves Git
Bash itself. READ from the installed hook definitions: Claude's `.claude/settings.json` commands
are `bash "$CLAUDE_PROJECT_DIR/..."`, run by Claude Code in Git Bash (MEASURED here: this
machine's PowerShell PATH has the stub first and those hooks run); Codex's `hooks.json` carries a
`commandWindows` PowerShell wrapper; OpenCode starts python, and every guard resolves bash via
`x4guard.resolve_bash`. The row answered "what does PATH say", not "what would a hook run" --
the adjacent-question shape. It was also the whole difference between the doctor's exit 1 from
PowerShell and 3 from Git Bash on the same install (MEASURED: the same 5 UNKNOWN rows in both).

**Fix.** `windows_path_bash_sites` reads the hook definitions: a Claude hook with a non-bash
`shell`, or a Codex hook whose Windows command (`commandWindows`, else `command` under cmd.exe)
starts bare `bash`. FAIL only when such a hook exists AND the first bash on PATH is a stub, or
when no Git Bash resolves (the `setx X4_BASH` line is printed); otherwise OK, naming the stub as
information. **RE-DERIVED BY:** `tests/test_x4doctor.py` -k R2B2 -- one falsification twin per
clause (stub + no site OK; stub + codex site without commandWindows FAIL; the same with a
PowerShell commandWindows OK; site + Git Bash first OK; Claude hook with `shell: powershell`
FAIL; no Git Bash FAIL with the setx line).

## F163 — a test stranded an undeletable folder on every run · **DEFECT (measured)** · IN-ARC · confidence 95% · ✅ FIXED 2026-10-05 (fix lane FX-R2)

**Finding.** `test_OWNERSHIP_the_real_setowner_command_is_accepted` ran `icacls /setowner` on a
folder its user already owned. MEASURED (Python 3.13.14): pytest's temp root is made with
`mkdir(0o700)`, whose ACL reaches the user only through an OWNER RIGHTS ACE; after `/setowner`
the folder was unreadable and undeletable (WinError 5). 20+ such folders were found in pytest's
garbage. The owner keeps WRITE_DAC, so `icacls <dir> /grant *<SID>:(OI)(CI)F` repairs one
WITHOUT elevation (MEASURED on a probe folder: grant, then delete, succeeded).

**Fix.** `refguard_owner.take_ownership` (one helper, used by `own_or_skip` and the test) grants
the user full control back after the owner change; a failed owner change grants nothing.
**RE-DERIVED BY:** `tests/test_x4refguard.py::test_OWNERSHIP_the_real_setowner_command_is_accepted`
(asserts the folder is still writable, readable and removable: RED before -- Errno 13 on the
write -- green after) and the fake-owner fixture tests pinning both steps.

## F164 — the `cross_tool` gate printed NOT RUN for its read-only check and still exited 0 · **DEFECT (measured)** · PRE-ARC · confidence 95% · ✅ FIXED 2026-10-05 (delta review, fix lane FX-T2)

**Finding (delta review C).** Section 4 (`check_sql_hardening`) caught `ValueError` /
`sqlite3.Error` when the effective store could not be opened and only PRINTED "check NOT RUN";
the gate's verdict never counted it, so a run whose read-only check examined nothing exited 0.
v3.3.1 had the same shape ("no store; read-only connection check skipped"), hence PRE-ARC.
**Denominator (READ at c6fb158):** `gates/cross_tool.py` has 6 `not_run(...)` sites; this was the
one site that printed instead of calling it. **Fix (`23ea4c1`):** the section calls `not_run()`,
so the verdict is 2. FX-T2 checked it on a corrupt store in scratch: the CANNOT line is printed
and counted.

**RE-DERIVED BY:** `tests/test_cross_tool_semantics.py` --
`test_sql_hardening_survives_an_UNUSABLE_store` (the cannot-list entry),
`test_an_UNUSABLE_store_makes_the_gate_exit_2_not_0` (`main()` returns 2 with every other
section passing) and its twin `test_a_USABLE_store_lets_the_sql_hardening_section_pass`.

## F165 — a gate's import error was skipped in a checkout when a configured root was missing · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-05 (fix lane FX-T2)

**Finding (delta review C, finding 3).** F161's fix taught conftest to treat a CONFIGURED root that
does not exist as unresolvable, so that an install, which configures roots before anything is
unpacked, could run its suite. The rule applied in EVERY layout: on a dev checkout, a gate whose
import raised a real error (TypeError, OSError) was skipped as "environment unresolvable"
instead of erroring. **Fix (`b78a55b`):** an UNCONFIGURED root is unresolvable in any layout; a
configured-but-missing root only in an installed layout; in a checkout `import_gate` re-raises.
The skip reason names which roots and why. The derived `<X4_TOOLKIT>/reference` default with
nothing there counts as not configured.

**RE-DERIVED BY:** `tests/test_layout_repo_only.py` --
`test_a_CONFIGURED_root_that_does_not_exist_is_unresolvable_in_an_INSTALL`, its twin
`test_TWIN_a_CONFIGURED_root_that_does_not_exist_is_RESOLVABLE_in_a_CHECKOUT`,
`test_an_UNCONFIGURED_root_is_unresolvable_in_EITHER_layout` and
`test_import_gate_RE_RAISES_in_a_checkout_with_a_configured_missing_root`.

## F166 — needs-a-reference tests skipped in a checkout whose named reference was broken · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-05 (fix lane FX-T2)

**Finding (delta review C, finding 4).** The needs-a-reference markers (3 test files:
`test_dump_accepts_physical_mod_vpath.py`, `test_fast_script_pass_runs_by_default.py`,
`test_script_files_disclose_unvalidated.py`) and `test_freshness`'s empty-extensions-root skip
skipped whenever no reference was unpacked. A checkout whose NAMED reference had lost
`libraries/wares.xml` therefore skipped, though that is a broken dev tree, not an install.
**Fix (`b78a55b`):** `_layout.reference_skip_reason` / `needs_reference_mark`: skip when no
reference is named, or in an installed layout; a checkout with a named, broken reference runs
and fails. The derived default with nothing unpacked still skips (nothing was named).

**RE-DERIVED BY:** `tests/test_layout_repo_only.py` --
`test_a_configured_but_NOT_UNPACKED_reference_skips_only_in_an_INSTALL`,
`test_TWIN_a_configured_but_NOT_UNPACKED_reference_RUNS_in_a_CHECKOUT`,
`test_an_UNCONFIGURED_reference_skips_in_EITHER_layout`,
`test_an_UNPACKED_reference_runs_in_EITHER_layout`,
`test_the_DERIVED_reference_default_with_nothing_unpacked_skips_even_in_a_CHECKOUT`; and
`tests/test_freshness.py::test_an_EMPTY_configured_extensions_root_skips_only_in_an_INSTALL`.

## F167 — a checkout that lost `agent/` was treated as an installed toolkit · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-05 (fix lane FX-T2)

**Finding (delta review C, finding 6).** `_layout.installed_layout` keyed on the absence of
`agent/` alone, so a checkout that LOST `agent/` read as an install, and every REPO-ONLY test
skipped instead of failing -- the exact case F161's fix promised would still fail.
**Population (MEASURED by grep at c6fb158):** 17 `installed_layout(` and 31 `require_repo(` calls
under `tests/`. **Fix (`b78a55b`):** installed = no `agent/` AND not this toolkit's own checkout:
a `.git` directory or worktree file whose `HEAD` tracks `agent/` (`git cat-file -e HEAD:agent`).
Not plain "no `.git`": the in-game method installs INTO the game root, which can be a git repo
(this machine's is), and neither installer copies `.git`.

**RE-DERIVED BY:** `tests/test_layout_repo_only.py` --
`test_a_CHECKOUT_that_lost_agent_is_NOT_installed_and_does_NOT_skip`,
`test_a_WORKTREE_that_lost_agent_is_NOT_installed`,
`test_TWIN_an_install_inside_ANOTHER_git_repo_is_still_installed` and
`test_TWIN_a_dot_git_that_git_cannot_answer_for_is_treated_as_installed`. FX-T2 measured 11 of
these clauses RED on the old predicates.

## F168 — the tests of two shipped scripts skipped when the script was missing · **DEFECT (measured)** · IN-ARC · confidence 95% · ✅ FIXED 2026-10-05 (fix lane FX-T2)

**Finding (delta review C, finding 5).** 2 of 2 such tests: `scripts/codex-e2e.py` and
`scripts/scan-identifiers.py` are tracked, not export-ignored and copied by both installers, so
they ship in every layout; an unconditional skip on their absence hid a broken tree.
**Fix (`d3e5283`):** a missing script FAILS.

**RE-DERIVED BY:** `test_a_MISSING_codex_e2e_script_FAILS_rather_than_skips`
(`tests/test_codex_e2e_rows.py`) and `test_a_MISSING_scan_identifiers_script_FAILS_rather_than_skips`
(`tests/test_scan_identifiers_profile_id.py`): each points the script path at nothing and
requires the AssertionError.

## F169 — two checks in `test_adapting_doc` could pass by examining nothing · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-05 (fix lane FX-T2)

**Finding (delta review C, finding 7).** (1) `_toy_adapter_numbers()` returning None (no toy
adapter) silently dropped the ADAPTING worked-example comparison -- no skip, no failure.
(2) `_bare_ps1_invocations` caught only `install.ps1 -Switch`; a line starting with the script and
no switch, or `powershell`/`pwsh ... install.ps1` without `-ExecutionPolicy Bypass -File`, passed.
**Fix (`9ea7af8`):** a checkout without the adapter FAILS, an install skips (counted, REPO-ONLY);
the detector sees both shapes, prose mentions stay clean. 4 of its 9 shape twins were RED on the
old detector; the three user docs still pass.

**RE-DERIVED BY:** `tests/test_adapting_doc.py` --
`test_TWIN_a_missing_toy_adapter_FAILS_in_a_checkout`,
`test_TWIN_a_missing_toy_adapter_is_a_COUNTED_skip_in_an_install` and
`test_TWIN_bare_ps1_detector_per_shape`.

## F170 — an installed toolkit inside another git repo got two false reds · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-05 (fix lane FX-T2)

**Finding (delta review C, finding 8).** The in-game method installs into the game root, which can
be a git repo of its own. There (a) `test_build_release`'s export-ignore audit asked the
ENCLOSING repo's `git check-attr`, which knows none of the toolkit's attributes, and (b)
`test_shell_scripts_never_reassign_tmp` read the enclosing repo's index, which tracks none of the
toolkit's files: an empty population. 2 false reds in a healthy install. **Fix (`4670e2b`):** an
installed layout skips the export-ignore audit (counted, REPO-ONLY) and walks its own files; in a
checkout a failing `git check-attr` now FAILS (it used to skip), and an empty population fails.

**RE-DERIVED BY:** `tests/test_build_release.py` --
`test_TWIN_an_install_inside_ANOTHER_git_repo_SKIPS_the_export_ignore_audit`,
`test_TWIN_a_CHECKOUT_without_the_attribute_FAILS_the_export_ignore_audit`; and
`tests/test_shell_scripts_never_reassign_tmp.py` --
`test_TWIN_an_empty_population_skips_only_in_an_install`,
`test_an_install_INSIDE_another_git_repo_still_walks_its_own_files` (RED on the old index
branch).

## F171 — the installed-suite test proves collection, not a run · **SCOPE (measured)** · confidence 95% · ✅ CLOSED 2026-10-05 as documented (fix lane FX-T2)

**Finding (delta review C, finding 9).** The test that guards F161 installs with each installer and
runs `pytest --collect-only` inside the install. Collection imports every module and evaluates
skip markers but runs no test body, so a test that fails at RUNTIME in an installed tree (F170's
false reds are the example) is not covered by it. **Cost (MEASURED by FX-T2, Windows):** each case
builds its own venv: 18.3 s (sh) + 12.9 s (ps1), ~31 s, ~0.1 GB written to the temp drive -- under
the 60 s line, so no reduction is proposed. **Closed as documented (`874f7f9`):** the docstring
states the scope and the cost; a full run of the installed suite belongs to the release's cold
install check (the 2026-10-05 runs are in F161).

**RE-DERIVED BY:** `tests/test_install_over_existing.py::test_R2B1_an_INSTALLED_toolkits_suite_COLLECTS_and_counts_its_repo_only_skips`
(the test whose scope this states).

## F172 — the toy-conformance mutant run was decided by its case floor off Windows · **DEFECT (measured)** · IN-ARC · confidence 95% · ✅ FIXED 2026-10-05 (fix lane FX-T2)

**Finding (CI4, ubuntu run on da93d2d).** The mutant run picked 12 rows; one spells its path with
backslashes, which off Windows the engine buckets `windows_path_dialect`. 11 < `--min-cases 12`,
so the CONTROL refused rc 3 for `always_allow` and `crash_exits_nonzero`: the floor decided, not
the mutant. **Fix (`66c9753`):** rows are filtered with the engine's own `classify()`, and a red
run must also not be a min-cases refusal. On Windows the selection is unchanged.

**RE-DERIVED BY:** `tests/test_toy_agent_conformance.py` --
`test_each_adapter_mutant_turns_conformance_red` and
`test_TWIN_a_backslash_path_row_is_replayable_on_Windows_only`.

## F173 — `cross_tool`'s builder-idempotence check passes on an empty store · **SCOPE (read, open)** · PRE-ARC · confidence 70% · ⏳ OPEN

**Lead (delta review C residual, reported by FX-T2, not fixed in this release).** The check builds
twice and compares row counts; on an EMPTY store it passes as "0 vs 0 rows", which cannot tell
agreement from nothing built -- a check that cannot go red there. The 70% is the reading: no run
on an empty store has been recorded in this register. Open: require a non-empty build (or report
NOT RUN) before the comparison counts.

## F174 — `x4guard conformance` took a reference hook that failed to run for a checked allow · **DEFECT (measured)** · IN-ARC · confidence 95% · ✅ FIXED 2026-10-05 (fix lane FX-G3)

**Finding (MEASURED by FX-G3).** `reference_verdict` ignored the reference hook's exit code, so a
renamed hook (bash exit 127, empty stdout) decoded as a CHECKED allow and the run passed: CLI rc
0, "1 checked". The proof the toolkit offers any adapter could therefore pass against guards that
never ran. **Fix (`37c32c5`):** a reference run that cannot start, times out or exits non-zero is
an error with a reason; `summarise` returns 2 (REFERENCE ERROR per item), the CLI refuses (rc 2)
naming each case, and an extra-case control that errors refuses too. `c521e30` removed a second,
private bash resolver: the engine asks `scripts/gitbash.find_bash()`. The identity control over
the whole dumped corpus still passes, so no real guard case exits non-zero.

**RE-DERIVED BY:** `tests/test_x4guard_conformance_cli.py` --
`test_reference_CONTROL_exit_0_empty_stdout_is_allow`,
`test_reference_a_MISSING_hook_exit_127_is_an_error`,
`test_reference_exit_1_with_NO_output_is_an_error`, `test_reference_a_TIMEOUT_is_an_error`,
`test_reference_a_MISSING_interpreter_is_an_error`,
`test_the_CLI_refuses_2_when_a_reference_hook_cannot_run`; `tests/test_x4conformance_engine.py`
-- `test_a_case_with_NO_reference_verdict_cannot_pass`,
`test_TWIN_the_same_case_WITH_a_reference_verdict_passes`,
`test_ONE_bash_resolver_the_engine_asks_gitbash`, `test_TWIN_an_X4_BASH_that_exists_is_used`.

## F175 — `scan-identifiers.py` checked only the first account-pattern occurrence on a line · **DEFECT (measured)** · PRE-ARC · confidence 95% · ✅ FIXED 2026-10-05 (fix lane FX-G3)

**Finding (MEASURED by FX-G3).** `account_match` used `rx.search`, so a placeholder or an elided
path EARLIER on a line (`C:/Users/tester/...`, a placeholder profile path) was found, skipped, and
hid a real identifier LATER on the same line: `account_match` returned None. v3.3.1 has the same
`rx.search` (PRE-ARC). **Fix (`9fa6c7a`):** every occurrence is checked (`finditer`); three new
selftest cases plus a placeholder-only twin. FX-G3's full-repo scan over 684 files stayed clean.

**RE-DERIVED BY:** `tests/test_scan_identifiers_profile_id.py` --
`test_a_placeholder_EARLIER_on_the_line_does_not_hide_a_real_user_path_LATER`,
`test_a_placeholder_profile_id_EARLIER_does_not_hide_a_real_one_LATER`,
`test_TWIN_two_placeholders_on_one_line_are_still_clean`,
`test_TWIN_the_real_path_ALONE_is_caught_too`; and `scripts/scan-identifiers.py --selftest`.

## F176 — the OpenCode plugin judged a command in a shell grammar OpenCode was not running · **DEFECT (measured)** · IN-ARC · confidence 85% · ✅ FIXED 2026-10-05 (fix lane FX-G3)

**Finding.** `shellName()` answered "bash" for EVERY shell off Windows, never read OpenCode's
configured `shell`, and on Windows called sh, zsh and cmd "powershell". MEASURED with
`x4guard check`: `Remove-Item -Recurse -Force <reference>/libraries` is allow under `--shell bash`
and deny under `--shell powershell`, so a user whose OpenCode runs pwsh (config `shell`, or
`$SHELL` off Windows) had it waved through by the plugin. **Fix (`7107827`):** mirrors OpenCode's
`acceptable()` (READ: `packages/core/src/shell.ts` at f2cf607): `X4_OPENCODE_SHELL` > the
configured `shell` (captured by the plugin's `config` hook) > `$SHELL`; fish and nu, which
OpenCode refuses, fall back to the platform default (PowerShell on Windows, else bash); POSIX
names judge as bash, pwsh/powershell as PowerShell, anything else (cmd) is passed by name and the
adapter refuses it INERT. The node tests were run by the orchestrator at merge (`8e72678`: 119
passed). The 85% is that nothing was run inside a real OpenCode (its section in the README says
so). **Residual (READ, open):** the configured shell is not checked for EXISTENCE, so OpenCode's
own fallback to the platform default for a shell that is not installed is not mirrored.
**SUPERSEDED IN PART 2026-10-06 (the record above is kept as written):** `X4_OPENCODE_SHELL` no
longer outranks the detected shell, it can only confirm it (F213), and the residual is fixed: an
unresolvable configured shell is refused (F214).

**RE-DERIVED BY:** `tests/test_opencode_plugin_node.py` -- `test_SHELL_decides_the_grammar`,
`test_the_CONFIGURED_shell_beats_SHELL`,
`test_TWIN_a_REFUSED_configured_shell_falls_back_to_the_default_NOT_to_SHELL`,
`test_TWIN_a_config_without_a_shell_leaves_SHELL_in_charge`,
`test_X4_OPENCODE_SHELL_can_only_CONFIRM_the_detected_shell` (it replaced `X4_OPENCODE_SHELL_overrides_config_and_SHELL` when the override became confirm-only, F213),
`test_a_shell_OpenCode_refuses_falls_back_to_the_platform_default`,
`test_no_SHELL_at_all_is_the_platform_default`,
`test_a_cmd_SHELL_is_named_cmd_and_the_REAL_adapter_refuses_it_inert`,
`test_TWIN_cmd_through_the_REAL_adapter_is_INERT`,
`test_a_POWERSHELL_delete_under_a_pwsh_config_THROWS_through_the_real_guards`.

## F177 — an exported root moved a mutating command off the acting toolkit's config · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-05 (fix lane FX-B2) · inherited `X4_CONFIG` ✅ CLOSED 2026-10-06 (FX-B3, FX-B4)

**Finding (delta review).** F160's fix made `x4refguard apply/remove`, `x4lock lock/unlock` and
`bin/unpack-reference.sh` -- the 3 programs that change the system -- refuse an inherited
`X4_TOOLKIT` naming another toolkit. They did not compare the ROOTS: the environment layer
outranks the config, so `x4refguard apply --toolkit B --yes` with `X4_REFERENCE=<A>/reference`
exported protected A's tree (and `remove` lifted it), exit 0, with no notice when `X4_TOOLKIT`
itself was not inherited. **Fix (`0c76880`):** `_paths.env_root_conflicts` / `env_root_refusal`
compare each root resolved WITH the environment against the acting toolkit's config alone; a
difference refuses, naming both roots and the flag that chooses one (`--reference`, `--game`,
`--registry`). Only when a config file is read: with none, the environment IS the configuration.
`status` prints one line; `unpack-reference.sh` refuses the same way and also refuses an empty or
unknown refguard state. ~~**Open:** an inherited `X4_CONFIG` naming another toolkit's file is still
honoured as explicit (F160's residual; no installer sets one).~~ **CLOSED 2026-10-06** (the
record above is kept as written): `X4_CONFIG` was trusted as "this toolkit's config" by 3 Python
mutating entry points and 1 bash one (`bin/unpack-reference.sh`); the commands are
`x4refguard apply/remove`, `x4lock lock/unlock` and the unpack. Each now refuses while an inherited `X4_CONFIG` names a config
outside the acting toolkit, or a file that does not exist (FX-B3 `b5087bc`, `2e34d3a`); `status`
prints one notice line. **USER DECISION 2026-10-06: keep the refusal.** `--reference` lifts it
for x4refguard, which acts on that one root; nothing lifts it for x4lock (its manifest locks
the config `X4_CONFIG` picks) or for the unpack (FX-B4 `baeea35`: with `--reference` the
foreign config still supplied `X4_GAME` and `X4_APPMANIFEST`, so the tree was filled from THE
OTHER game -- MEASURED RED -- and 6 of the 8 keys the unpack reads have no flag). The refusal
names the way out (clear `X4_CONFIG`).

**RE-DERIVED BY (the `X4_CONFIG` part):** `tests/test_toolkit_binding.py` --
`test_FXB3_inherited_X4_CONFIG_naming_A_makes_B_REFUSE_naming_both`,
`test_FXB3_X4_CONFIG_naming_a_MISSING_file_REFUSES_too`,
`test_FXB3_TWIN_X4_CONFIG_naming_B_s_OWN_config_is_not_refused`,
`test_FXB3_TWIN_reference_flag_lifts_it_for_x4refguard`,
`test_FXB3_x4lock_REFUSES_on_an_inherited_X4_CONFIG_and_flags_do_not_lift_it`; and
`tests/test_unpack_reference_layer2.py` --
`test_FXB3_an_X4_CONFIG_OUTSIDE_the_toolkit_REFUSES_and_writes_nothing`,
`test_FXB4_reference_does_NOT_lift_the_X4_CONFIG_refusal_the_config_still_supplies_the_game`,
`test_FXB4_TWIN_reference_with_X4_CONFIG_UNSET_proceeds`.

**RE-DERIVED BY:** `tests/test_toolkit_binding.py` --
`test_FXB2_inherited_X4_REFERENCE_makes_toolkit_B_REFUSE_naming_both`,
`test_FXB2_TWIN_inherited_X4_REFERENCE_EQUAL_to_the_config_is_not_refused`,
`test_FXB2_TWIN_reference_flag_chooses_and_acts_on_the_chosen_root`,
`test_FXB2_TWIN_no_config_file_means_the_environment_IS_the_config`,
`test_FXB2_x4lock_lock_REFUSES_on_an_inherited_X4_GAME_differing_from_the_config`,
`test_FXB2_TWIN_x4lock_game_flag_chooses`, `test_FXB2_env_root_conflicts_in_process`; and
`tests/test_unpack_reference_layer2.py` --
`test_FXB2_an_INHERITED_X4_REFERENCE_differing_from_the_config_REFUSES`,
`test_FXB2_TWIN_an_inherited_X4_REFERENCE_EQUAL_to_the_config_proceeds`,
`test_FXB2_TWIN_reference_chooses_the_tree_explicitly`,
`test_FXB2_an_EMPTY_layer2_state_on_an_existing_tree_REFUSES`.

## F178 — a config line the grammar refuses was dropped in silence by the Python tools · **DEFECT (measured)** · IN-ARC · confidence 95% · ✅ FIXED 2026-10-05 (fix lane FX-B2)

**Finding (delta review C, I1).** F156 gave both loaders one grammar that IGNORES a line it refuses.
The guards name such lines at session start; every Python tool dropped them SILENTLY and fell back
to a default (`<toolkit>/reference`) with exit 0. An unquoted `X4_REFERENCE=<dir>/ref&x` meant the
tools quietly used another folder. **Population (READ, `_paths.IGNORE_REASONS`):** 5 reasons --
`shape` (not KEY=value), `key` (not an X4_* key), `subst` (`$( )` or a backtick), `operator` (an
unquoted `; & | < >`), `guard` (`X4_GUARD`/`X4_GUARD_CHECK`). **Fix (`73a31f9`):** one stderr line
per process per file naming line:reason, never a value (the file may hold `X4_NEXUS_KEY`);
`x4validate --paths` lists them; `config_ignored()` exposes them; `x4doctor`'s `roots.config`
FAILs on `shape`, `subst` and `operator` (`guard` has its own FAIL in `guard.escape`; `key`
configures nothing either way).

**RE-DERIVED BY:** `tests/test_paths.py` -- `test_FXB2_an_ignored_line_is_NAMED_on_stderr_and_in_paths`,
`test_FXB2_TWIN_a_clean_config_prints_no_ignored_notice`; `tests/test_x4doctor.py` --
`test_roots_config_FAILS_on_a_line_every_loader_IGNORES`,
`test_TWIN_roots_config_a_QUOTED_operator_or_a_foreign_key_is_not_a_FAIL`.

## F179 — Windows PowerShell 5.1 made a Python warning fatal inside `install.ps1` · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-05 (fix lane FX-B2)

**Finding (delta review).** Under Windows PowerShell 5.1 a native command whose stderr is
redirected raises a terminating NativeCommandError when it writes to stderr. In `install.ps1` the
read-only OpenCode precheck died raw (exit 1) instead of printing its refusal, the OpenCode render
failed on any warning, and the Layer-2 summary said "no reference/ tree yet" when the state could
not be read. Separately (CI1), the script fell off its end, so `& .\install.ps1` left
`$LASTEXITCODE` = 2 from the last native call after "install complete" (MEASURED under 5.1 and 7).
**Population (MEASURED by grep at c6fb158):** 4 call sites, all now through `Invoke-X4Native`
(error preference relaxed in its own scope, stderr kept as text). **Fix (`d524bf4`):** that
helper; an unreadable protection state is said as such (both installers); the script ends
`exit 0`; CI's install legs use their own registry test key and drop the inherited `X4_TOOLKIT`.

**RE-DERIVED BY:** `tests/test_install_over_existing.py` --
`test_FXB2_2_the_READ_ONLY_opencode_refusal_is_the_crafted_one_on_EVERY_powershell`,
`test_FXB2_2_the_summary_names_an_UNREADABLE_protection_state_not_no_tree`,
`test_FXB2_CI1_a_COMPLETE_install_run_IN_PROCESS_exits_0`.

## F180 — the doctor and x4lock tests read the checkout's own per-machine config · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-05 (fix lane FX-B2)

**Finding (CI2).** Since F160 the doctor acts for the toolkit it LIVES in. `test_x4doctor` built
`Ctx` without naming a toolkit, so every such scenario acted for the CHECKOUT and read its real,
per-machine `x4-paths.env`; the real-x4lock tests did the same, and in CI (no config) "1 missing"
became "2". Results depended on the machine running them. **Population (MEASURED by grep at
c6fb158):** 6 Ctx construction sites (4 direct, 2 shared helpers) now name a scratch toolkit; the
2 B2 tests that test the default on purpose are unchanged. **Fix (`73a31f9`):** that, and the
real-x4lock tests carry their own `scripts/x4lock.py` and resolver.

**RE-DERIVED BY:** `tests/test_x4doctor.py` --
`test_x4lock_row_asks_the_REAL_x4lock_with_the_roots_environment` and
`test_TWIN_x4lock_REAL_a_deleted_CLAUDE_md_is_MISSING` (the real-x4lock pair), run in CI where no
per-machine config exists.

## F181 — `run-gates.sh` log-dir containment was fooled by a link · **DEFECT (measured)** · IN-ARC · confidence 95% · ✅ FIXED 2026-10-05 (fix lane FX-B2)

**Finding (delta review).** `X4_GATE_LOG_DIR` must not lie inside the game or reference tree. The
check compared `abspath` only, so a log dir that is a symlink or junction INTO either tree passed
as "outside". **Fix (`22350b3`):** both the `abspath` and the (non-strict) `realpath` forms of the
log dir and of each root are compared.

**RE-DERIVED BY:** `tests/test_run_gates_logs.py::test_FXB2_a_log_dir_reached_through_a_LINK_into_the_game_REFUSES`
(a real junction on Windows, a symlink elsewhere).

## F182 — the profile-id walk covered Windows only and skipped an unreadable location in silence · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-05 (fix lane FX-B2) · PARTIAL exit ✅ FIXED 2026-10-06 (FX-B3, FX-B4)

**Finding (delta review).** `scan-identifiers.py` derives the machine's X4 profile ids (banned bare,
never printed) by walking the known profile locations: 2 of them, Windows `Documents` and
OneDrive `Documents`. A Linux machine (native client: `~/.config/EgoSoft/X4`) derived nothing,
silently, and a location that existed but could not be read was skipped with no word.
**Fix (`22350b3`):** 3 locations; the scan prints how many of them exist here, and PARTIAL, naming
the location, when one exists but cannot be read.

**RE-DERIVED BY:** `tests/test_scan_identifiers_profile_id.py` --
`test_FXB2_a_LINUX_profile_folder_is_derived_too`,
`test_FXB2_an_UNREADABLE_location_makes_the_walk_say_PARTIAL`,
`test_FXB2_TWIN_absent_locations_are_not_PARTIAL`.

**ADDENDUM 2026-10-06 (delta review, FX-B3 and FX-B4).** The fix SAID "PARTIAL" and still exited
0 when nothing was found: a partial input walk reported clean (1 walk, 3 locations), the shape
this register exists for. **Fix (`45efbf0`):** a PARTIAL walk with nothing found exits 3 (the
history mode too); a finding still wins (exit 1); the unreadable list is per call, not
module-global. **Then (`efb4b3c`):** the exit-3 advice "or name the id through X4_PROFILE" is
dropped -- exit 3 stands whatever X4_PROFILE says, correctly: one named id says nothing of what
an unreadable location may hold; and the note counted `is_dir()`, which is False for an
unreadable location ("0 of 3 exist; PARTIAL -- 1 could not be read") and re-raised EPERM, so a
macOS-style permission refusal crashed it after the walk. One classification feeds both. macOS
itself: F228.

**RE-DERIVED BY (addendum):** `tests/test_scan_identifiers_profile_id.py` --
`test_FXB3_a_PARTIAL_walk_with_nothing_found_EXITS_3_not_0`,
`test_FXB3_TWIN_the_same_scan_with_the_location_ABSENT_is_clean`,
`test_FXB3_TWIN_a_FINDING_still_wins_over_PARTIAL`,
`test_FXB3_the_unreadable_list_is_PER_CALL_not_module_global`,
`test_FXB4_the_PARTIAL_verdict_does_not_advise_X4_PROFILE_which_cannot_lift_it`,
`test_FXB4_the_note_counts_an_UNREADABLE_location_as_one_that_EXISTS`.

## F183 — a substituted command name drops the delete advisory below a root · **SCOPE (measured)** · confidence 90% · ✅ FIXED 2026-10-06 (fix lane FX-G4)

**Lead (FX-DOC, 2026-10-05; re-measured by FX-DOC2 at c6fb158 with `x4guard check`, bash grammar,
every root pinned to scratch, 6 probes):**

| command | verdict | advisory |
|---|---|---|
| `rm -rf <game>/libraries` | advise | the X4-directory delete advisory |
| `$(echo rm) -rf <game>/libraries` | **allow** | **none** |
| `` `echo rm` -rf <game>/libraries `` | **allow** | **none** |
| `$(echo rm) -rf <game>` (control) | deny | -- (verb unresolved) |
| `$(echo rm) -rf <reference>` (control) | deny | -- (verb unresolved) |
| `rm -rf <reference>` (control) | deny | -- |

The substituted-verb deny (`verb_unresolved`) fires only when the operand IS a protected root; one
level below it the command reaches no rule and the advisory the plain spelling earns is lost.
**This is advisory loss, not a missing block**: the hard blocks above still hold. The 90% is that
the sandbox game folder was a scratch tree, not a real install. Open lead for the guard lane:
treat a substituted verb with an operand inside an X4 folder at least as the plain delete is
treated.

**FIXED 2026-10-06 (fix lane FX-G4; the lead above is kept as written).** Reviewer G found the
harder case beside it, PRE-ARC: `x=$(printf rm); $x -rf "<reference>"` was ALLOW (v3.3.1,
f35ff82, c6fb158) while `x=rm; $x` denies (F111 covered only the literal value). **Fix
(`b1e394f`):** a verb from a variable holding a substitution is an unresolved verb (deny at a
root or inside `reference/`), and a substituted verb below the game root gets the X4-directory
delete advisory back. Two in-lane false-advisory sources, each MEASURED over the 44,009-command
replay and fixed before merge: a token after a `$(`/backtick in its segment is not a command
position (`893a319`, 13 advisories), and the advisory's relative operands do not join the session
folder (`64bce54`, 6 advisories; F218).

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` (class `TestGOutSubstitutedVerb`:
`test_a_variable_holding_a_substitution_is_an_unresolved_verb`,
`test_a_substituted_verb_below_the_game_root_keeps_the_delete_advisory`,
`test_TWIN_a_literal_verb_variable_or_an_operand_elsewhere_is_not_this_rule`).

## F184 — a root variable operand was its root for `cd` only, not for the write and delete rules · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-05 (fix lane FX-G2)

**Finding (MEASURED by FX-G2).** F154 made a leading root variable its root for a `cd` target, but
`prep()` did not apply `subst_root_var` to other operands, and the path-config pass sent only the
config. 34 of 40 Bash and 6 of 8 PowerShell probes ALLOWED writes through a root variable --
`> "$X4_TOOLKIT/x4-paths.env"`, `cp`/`tee`/`sed -i`/`mv` into `"$X4_REFERENCE/..."`,
`Set-Content "$env:X4_REFERENCE/..."`, writes into `"$X4_PROFILE"` -- while the literal spellings
were refused; `rm "$X4_TOOLKIT/x4-paths.env"` only advised, and `rm -rf "$X4_GAME"` /
`mv "$X4_GAME"` read as an advisory instead of the game hard block. **Fix (`84330ba`):** every
operand goes through `subst_root_var`; the config pass sends every root (plus a `config` root for
`$X4_CONFIG`). After: 0 of 48, and the variable and literal spellings agree on 126 of 126
verb x root x tail probes. `18a4f01` keeps a test on the name-only branch that a leading root
variable no longer reaches (`"${PFX}$X4_GAME/x"`).

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` (class `TestG2ARootVariableOperandIsItsRoot`:
`test_every_root_variable_and_verb_judges_like_the_literal`, `test_the_measured_bypasses_are_refused`
and its five TWINs) and `scripts/test-hooks.sh`.

## F185 — Windows path aliases reached a protected file under a name the guards did not recognise · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-05 (fix lane FX-G2)

**Finding (MEASURED by FX-G2).** Windows opens `x4-paths.env.`, `x4-paths.env `, `x4-paths.env::$DATA`,
`reference./`, `reference::$INDEX_ALLOCATION/` and an 8.3 short name (`C:/PROGRA~2/...` for the game
folder on the maintainer's machine) as the protected file or folder; the guards compared names and
saw something else. 19 of 28 probes allowed. **Fix (`7c0954d`):** both normalisers
(`hook_facts.norm`, `x4_norm`) drop, per component, an NTFS stream suffix and trailing dots and
spaces -- the conservative superset of `GetFullPathNameW` (MEASURED: it strips a final component's
dots AND spaces but one trailing dot of an intermediate one); `protect-files.sh` canonicalises
the path before any name test; a `~<digit>` path resolves to its long form. After: 0 of 28.
The 90% is that only this machine's 8.3 names were probed.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` (class `TestG2WindowsPathAliases`:
`test_trailing_dots_spaces_and_stream_suffixes_are_the_file`, `test_the_rules_see_the_aliases`,
`test_an_8dot3_short_name_is_its_long_name` and the TWINs) and `scripts/test-hooks.sh`.

## F186 — a command substitution inside `$(( ))` passed the hard blocks · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-05 (fix lane FX-G2)

**Finding (MEASURED by FX-G2).** `$((` was stepped over whole as arithmetic, at the top level and in
an expanding heredoc body, but bash runs a substitution inside it: `echo $(( $(rm -rf <game>) + 0 ))`
and the backtick form were ALLOWED past the game hard block (6 of 14 probes). F159's residual
("a `$((...))` that...") is the same shape. **Fix (`38416b9`):** arithmetic yields the
substitutions inside it, and `$((cmd) )` -- a command substitution holding a subshell (MEASURED in
bash) -- is a command. One helper, `_dollar_double_paren`.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` (class `TestG2SubstitutionInsideArithmetic`:
`test_a_substitution_inside_arithmetic_reaches_the_hard_block`,
`test_the_helper_tells_arithmetic_from_a_subshell`, `test_TWIN_plain_arithmetic_is_still_not_a_command`,
`test_TWIN_single_quotes_and_a_quoted_heredoc_stay_literal`).

## F187 — an abbreviated git long option slipped past the wipe rules · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-05 (fix lane FX-G2)

**Finding (MEASURED by FX-G2 in a scratch repo).** git accepts any unambiguous prefix of a long
option: `git stash --a` and `git clean --fo -X` deleted ignored files, while the wipe rules
(F148, `git stash --all`) matched only the full spellings; `--al`, `--har`, `--fo` reached no deny.
**Fix (`38416b9`):** `_git_long` matches prefixes for `--force`, `--dry-run`, `--hard`, `--all`,
`--no-all`, `--include-untracked` and `--message`.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` (class `TestG2GitLongOptionAbbreviations`:
`test_an_abbreviated_destructive_option_is_the_option` and three TWINs).

## F188 — a patch run spelled other than `apply_patch` was not judged as a patch · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-05 (fix lane FX-G2)

**Finding (reviewer A, MEASURED).** `codex --codex-run-as-apply-patch '<patch>'` -- Codex puts it on
the shell's PATH -- deleted a file in scratch while `shell_patch` never looked at it. FX-G2: 11 of
11 other forms were unseen too (`{ apply_patch`, `FOO=1 apply_patch`, `apply_patch.bat`,
`'apply_patch'`, wrappers, `bash -c`, a double-quoted or npx-launched Codex flag). **Fix
(`3e3b0c0`):** the single-quoted Codex form (literal text) is READ as a patch; every other
spelling of a patch run is REFUSED, found by the guards' own shell parser rather than a regex; a
mention (`echo`/`grep apply_patch`) is still not a run; with no parser available the name anywhere
refuses. Residual: F199.

**RE-DERIVED BY:** `tests/test_patch_paths.py` -- `test_codex_run_as_apply_patch_is_read_as_a_patch`,
`test_every_other_spelling_of_a_patch_run_is_refused`, `test_TWIN_a_mention_is_not_a_patch_run`,
`test_TWIN_no_shell_parser_fails_closed`.

## F189 — a move to the Trash was not treated as a delete · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-05 (fix lane FX-G2)

**Finding (reviewer D-1; MEASURED by FX-G2, 16 of 24 probes wrong).** `core.md` tells Linux and macOS
agents to delete by moving to the Trash (the user's rule, kept), and the guards ALLOWED
`gio trash '<X4_GAME>'`, `gio trash '<X4_GAME>/extensions'`, `gio trash '<X4_REFERENCE>/a.xml'`, a
trashed save, `kioclient5 move <game> trash:/` and a Finder `delete` through `osascript`.
**Fix (`47ad1f0`):** `trash_paths` feeds rm's operand list for `gio trash/remove/rm` and
move-to-`trash:`, `trash`/`trash-put`/`gvfs-trash`/`gvfs-rm`/`rmtrash`, `kioclient*`
remove/move-to-`trash:`, and `osascript -e` scripts that `delete` a POSIX file/folder/alias, so
each takes rm's verdicts exactly (game and reference deny, saves and profile ask, other X4
folders advise). The Windows Recycle Bin form was already right; regression probes added.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` (class `TestG2MoveToTrashIsADelete`:
`test_every_trash_form_judges_like_rm` and four TWINs).

## F190 — non-ASCII whitespace split one config file into two configurations · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-05 (fix lane FX-G2)

**Finding (reviewer A's fuzzer: 18 value diffs in 400 lines).** Python's `\s` / `str.isspace()` are
Unicode and bash's `[:space:]` is ASCII in the C locale, so `X4_GAME<NBSP>=/g` configured the TOOLS
and was refused by the GUARDS. **Fix (`ad4bd62`):** both loaders decide blank/comment on ASCII
whitespace only and refuse (`shape`, so F178 reports it) any other line holding one of the 23
characters `isspace()` adds beyond ASCII (verified by FX-G2 equal to `isspace()` minus ASCII over
the BMP). 0 diffs after, seeds 1-3.

**RE-DERIVED BY:** `tests/test_config_unicode_space.py` --
`test_a_line_with_non_ascii_whitespace_is_refused_by_BOTH` (13 characters x 5 positions),
`test_TWIN_ascii_whitespace_is_still_a_configuration`,
`test_TWIN_a_comment_or_blank_line_is_not_reported`.

## F191 — five parser bypasses of the hard blocks · **DEFECT (measured)** · PRE-ARC · SECURITY · confidence 95% · ✅ FIXED 2026-10-05 (fix lane FX-G2)

**Finding (reviewer E, E1-E5; each MEASURED red at the facts level and end to end through
`protect-bash.sh` first).** Each let a command the hard blocks exist for -- `rm -rf <game>` -- run
as ALLOW:

- **E1** heredoc detection was per line, so a `<<WORD` on the second line of a multi-line quoted
  string (`git commit -m "notes` / `use cat <<EOF here"`) opened a skip region that never closed.
- **E2** `EOF)` ends a heredoc opened inside `$(` (bash warns, then RUNS the next line); the body
  ran to the end of input.
- **E3** a heredoc piped into a shell on a LATER line (`cat <<EOF |` ... `bash`,
  `{ cat <<EOF ... } | bash`) was stripped as data.
- **E4** `cmd` with no `/c` reads its program from stdin (`echo rd ... | cmd`, `cmd <<EOF`).
- **E5** an ANSI-C `$'it\'s'` opened a quote that never closed in every scanner.

**Fix (`ee040bc`):** one heredoc walk (`_heredoc_walk`) carrying the quote state across lines and
reading several heredocs per line in order; `EOF)` terminates; a body in a command where a
piped-into segment is a shell is executed text, and a line ending in `|` continues its pipeline;
cmd stdin is translated (`cmd_heredoc_bodies`), an unreadable one asks; one shared quote walk
(`_qwalk`) with an ANSI-C mode. 15 new mutants in `scripts/verify-hook-tests.py`; the fuzz
control re-anchored (rc 0).

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` (classes `TestE1HeredocQuoteStateCrossesLines`,
`TestE2TerminatorInsideASubstitution`, `TestE3AHeredocPipedIntoAShellOnALaterLine`,
`TestE4CmdReadsItsProgramFromStdin`, `TestE5AnsiCQuoting`) and `scripts/verify-hook-tests.py`.

## F192 — git work-tree spellings and five icacls switches reached no rule · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-05 (fix lane FX-G2)

**Finding (reviewer E, E6 and E7).** (E6) `--work-tree X` / `--work-tree=X`, `GIT_WORK_TREE=`,
`--git-dir` / `GIT_DIR` did not name the folder a git wipe acts on, and `-c
clean.requireForce=false` did not make an unforced `git clean` a delete. (E7) the "lifting the
reference/ protection asks" rule (F155) knew `/remove` and `/reset` only: `icacls /grant`,
`/inheritance`, `/restore` (ancestor, no `/T` needed), `/setowner` and `/substitute` lift it too,
and an x4refguard action passed through `xargs` or a variable was not seen. **Fix (`ee040bc`):**
those spellings name/force the wipe; every ACL-modifying switch lifts (ask); an action the guard
cannot read asks; switch values are not paths.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` (classes `TestE6GitWorkTreeAndRequireForce`,
`TestE7EveryAclLiftAsks`).

## F193 — an agent could switch the guards off through a Claude Code settings file · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-05 (fix lane FX-G2; user decision)

**Finding (MEASURED on Claude Code 2.1.290).** A project `.claude/settings.json` `env` block reaches
every hook process, so an agent writing `"env": {"X4_GUARD": "off"}` there switched the guards off
at the next launch -- the route F156 closed for `x4-paths.env`, open through another file.
**USER DECISION 2026-10-05:** block `X4_GUARD` there. **Fix (`dbaaf7d`, one implementation,
`settings_guard.py`):** Write/Edit/MultiEdit are judged on the RESULTING content; setting
`X4_GUARD`/`X4_GUARD_CHECK` in any `env` is denied, a result that may spell it but cannot be
parsed is denied with a reason, every other settings edit is allowed. Any SHELL write to
`.claude/settings*.json` is denied with the file-edit tool as the remedy (its content cannot be
seen); a read, delete or move away is allowed. Codex/OpenCode `apply_patch` and OpenCode
write/edit are judged on the written text; `x4guard check` on a settings path without the text is
denied. `session-canary.sh` names any project or user settings file that sets it. `abd4158`
seeds the fuzzer for the new rule (4,280 mutants, 0 bypasses). Residual: F198.

**RE-DERIVED BY:** `tests/test_settings_guard.py` (9 tests, 8 RED with the rule disabled:
`test_write_that_sets_X4_GUARD_is_denied_and_twin_allowed`, `test_edit_is_judged_on_the_RESULTING_file`,
`test_an_unreadable_result_that_could_spell_it_is_denied`,
`test_TWIN_a_file_merely_named_settings_elsewhere_is_not_this_rule`,
`test_a_shell_write_is_denied_and_a_read_is_allowed`,
`test_codex_patch_adding_X4_GUARD_is_denied_and_twin_allowed`,
`test_codex_patch_on_a_file_that_ALREADY_sets_it_is_denied`,
`test_x4guard_check_needs_the_written_text_for_a_settings_file`,
`test_the_canary_names_a_settings_file_that_sets_it`) and `.claude/hooks/test_hook_facts.py`
(class `TestAShellWriteToAgentSettingsIsRefused`).

## F194 — the config-write denies never reached the Codex conformance replay · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-05 (fix lane FX-G2)

**Finding (reviewer E8).** `scripts/test-hooks.sh` unset `X4_DECIDE_DUMP` along with every other
X4_* key in its path-config section, so that section's denies were never dumped and never
replayed through the Codex chain. **Fix (`eba4ab2`):** the harness switch is kept; the joined R3-1
probe became one probe per refusal, and the caller-directory leftover check runs last.

**RE-DERIVED BY:** `scripts/test-hooks.sh` (its path-config section now in the dump the conformance
replay reads).

## F195 — the Windows Codex wrapper accepted an array-valued decision · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-05 (fix lane FX-G2)

**Finding (MEASURED red first by FX-G2).** `codex-entry.ps1` checked `permissionDecision -cne 'deny'`;
for an ARRAY `@('deny')` that comparison yields an empty, falsy array, so the PowerShell wrapper
accepted output that `codex-entry.sh` rejects. **Fix (`eba4ab2`):** a non-string decision fails
closed; 46 of 46 wrapper cases after.

**RE-DERIVED BY:** `tests/test_codex_wrapper.py` (the array fault in its FAULTS table).

## F196 — `verify-hook-tests` credited one catch-all test to every predicate · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-05 (fix lane FX-G2)

**Finding (reviewer E10).** The silent? column credited the catch-all `test_TWIN_a_read_is_not_a_write`
as a must-not-fire test of EVERY predicate, so a predicate with no test of its own looked covered.
**Fix (`dbaaf7d`):** a failing test is credited only when its source (or its class's FACT/KEYS)
names the predicate. Over all 31 predicates every one keeps at least one credited must-not-fire
test, so no real gap appeared; the lowest is `verb_unresolved`, with 3 of its 8 credited. (The
commit message reads "5 -> 3"; 3 of 8 is the corrected figure, per the orchestrator, not
re-measured in this lane.)

**RE-DERIVED BY:** `scripts/verify-hook-tests.py` (its `test_sources` / `names_predicate` crediting).

## F197 — `verify-hook-tests` credited a mutant to a same-named test in another class · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-05 (FX-G3 patch)

**Finding (MEASURED by FX-G3).** 3 of 618 test method names exist in two classes
(`test_suffix_strip`, `test_pattern_replace`, `test_array_index`); a FAIL in the OTHER class's
same-named test was credited as the mutant caught (old parse True, new False). **Fix
(`a2c6590`):** failures parse as `Class.method` (Python 3.10 and 3.11+ formats); targets qualify
through an AST index; an ambiguous or unknown target refuses rc 2. Applied after the FX-G2 merge:
all 143 mutant targets resolve to exactly one test.

**RE-DERIVED BY:** `scripts/verify-hook-tests.py` (its `test_index` resolution, which refuses an
ambiguous target).

## F198 — a settings file written by an opaque interpreter or a cwd-relative write is not judged · **SCOPE (open, accepted)** · confidence 80% · ⏳ OPEN

**Residual of F193 (reported by FX-G2, not re-measured in this lane).** The shell rule recognises
write primitives aimed at `.claude/settings*.json`; a write from inside an opaque interpreter
(`python -c`, a script file) or a RELATIVE write whose cwd is unknown names no such path to it.
Accepted: the session canary still names, at the next start, any settings file that sets
`X4_GUARD`, so the switch-off is not silent.

## F199 — a patch runner whose name is built from a variable is not a patch run · **SCOPE (open, accepted)** · confidence 80% · ⏳ OPEN

**Residual of F188 (reported by FX-G2, not re-measured in this lane).** The refusal finds patch
runs by command name through the shell parser; a name assembled from a variable (`P=apply_patch;
$P ...`) is not recognised. Accepted residual.

## F200 — an escaped backtick inside double quotes is read as a substitution · **SCOPE (open, accepted)** · PRE-ARC · confidence 80% · ⏳ OPEN

**Residual (reported by FX-G2, pre-existing, not re-measured in this lane).** Inside double quotes
`\`` is a literal backtick to bash, but the scanner reads it as opening a command substitution.
The effect is an ADVISORY false positive only: text is judged as a command, never a command
missed. Accepted.

## F201 — `cmd /k` and `cmd /c cmd` read their program from stdin, and the guard did not · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06 (fix lane FX-G4)

**Finding (reviewer H, delta review).** F191 item 4 made bare `cmd` (no `/c`) read its stdin as a
program. Two more forms do the same: `cmd /k <cmd>` runs its inline command and then reads stdin
(MEASURED in Git Bash), and `cmd /c cmd` hands stdin to a child cmd that reads it. A deletion piped
into either reached no rule. **Fix (`b1e394f`):** both are read as cmd programs; `/c` and `/r`
with another child take no stdin program. Each reproduced RED end to end (protect-bash.sh, scratch
roots, deny control) and at the facts level against e39f675; a named mutant in
verify-hook-tests.py (re-anchored E4).

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` (class `TestH1CmdKReadsStdin`:
`test_cmd_k_reads_its_program_from_stdin_too`, `test_cmd_c_cmd_reads_stdin_through_the_child`,
`test_TWIN_c_and_r_with_another_child_take_no_stdin_program`) and `scripts/test-hooks.sh` (the
FX-G4 H1 row).

## F202 — a root variable under a brace operator was not its root · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06 (fix lane FX-G4)

**Finding (reviewer H).** F184 made a root variable its root for every rule, but only in the plain
`$X4_GAME` / `${X4_GAME}` spellings. Under a brace operator (`${X4_GAME:-/x}`, `${X4_REFERENCE%/}`,
`:=`, ...) the operand was judged as text, and an unassigned root variable under `:-` was read as
UNSET, so the operator's default was judged instead of the root. **Fix (`b1e394f`; fuzz follow-up
`5c05e6f`: `${OTHER:-${X4_GAME}}`, another variable whose default is a root variable, is that
root):** a root variable under any brace operator is its configured root; an unconfigured root
still refuses a delete; another variable, or an assignment in the command, is not a root.
Re-anchored: the two root-variable anchors in verify-hook-tests.py.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` (class `TestH2RootVariableUnderAnOperator`:
`test_a_root_variable_under_a_brace_operator_is_its_root`,
`test_an_unconfigured_root_under_an_operator_still_refuses_a_delete`,
`test_TWIN_another_variable_or_an_assignment_in_the_command_is_not_a_root`).

## F203 — an adapter judged a shell patch after an EXPANDED `cd` as a literal path · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06 (fix lane FX-G4)

**Finding (MEASURED by reviewer H).** `cd $X4_REFERENCE && codex --codex-run-as-apply-patch
'...Add File: libraries/x.xml...'` (also `$env:X4_REFERENCE`, and `apply_patch <<EOF` after such a
cd) was judged against `<cwd>/$X4_REFERENCE/libraries/x.xml`, a path inside nothing: ALLOW. The
value the agent's shell gives the variable cannot be known in the adapter (the configured roots
live in the bash guards, and a shell environment policy may differ). **Fix (`643b598`):** a `cd`
target holding `$`, `%VAR%`, `~` or a substitution raises PatchParseError, which the caller turns
into an inert refusal whose reason names the fix; single-quoted text stays literal. 16 refused
forms, 3 literal twins.

**RE-DERIVED BY:** `tests/test_patch_paths.py` -- `test_a_shell_patch_after_an_EXPANDED_cd_is_refused`,
`test_TWIN_a_literal_cd_still_names_the_directory`.

## F204 — the settings rule read only `env`: `X4_GUARD=off` in a hooks command switched a hook off · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06 (fix lane FX-G4; user decisions)

**Finding (MEASURED by reviewer H).** F193's rule judged a `.claude` settings file's `env` block
only. `X4_GUARD=off bash .../protect-bash.sh` written as a `hooks` COMMAND string switched that one
hook off at the next launch and was allowed. **USER DECISION 2026-10-06 ("block X4_GUARD there"),
fix `0da4749`:** the resulting content is refused when `X4_GUARD` or `X4_GUARD_CHECK` appears in any
key or string (the `env` block is still read as before). RED against e39f675. **USER DECISION
2026-10-06, `16d0ec5`:** the top-level `permissions` block is exempt -- a rule string such as
`Bash(export X4_GUARD=off)` sets nothing; the same text in `env`, a hooks command or anywhere else
is still denied. Residual: F220 (a patch is judged on its raw text).

**RE-DERIVED BY:** `tests/test_settings_guard.py` --
`test_X4_GUARD_ANYWHERE_in_the_file_is_denied_not_only_in_env`,
`test_a_PERMISSIONS_rule_mentioning_X4_GUARD_is_allowed_the_same_text_elsewhere_is_not`.

## F205 — a settings Edit whose `old_string` did not apply was allowed whatever it wrote · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06 (fix lane FX-G4)

**Finding (reviewer H, H-M1).** F193 judges an Edit on the RESULTING file. When the `old_string`
did not occur in the file as the guard read it, there was no resulting file to judge, and the edit
was allowed whatever its `new_string` wrote. **Fix (`0da4749`):** the `new_string` is then judged
on its own; a non-matching edit without the key stays allowed. RED against e39f675.

**RE-DERIVED BY:** `tests/test_settings_guard.py` --
`test_an_edit_whose_old_string_does_not_apply_is_judged_on_its_new_text`.

## F206 — a percent-encoded `file://` operand hid the game root · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06 (fix lane FX-G4)

**Finding (reviewer H, H-M3).** A `file://` operand was compared undecoded, so
`file:///C:/Program%20Files%20(x86)/.../X4%20Foundations` named no root. **Fix (`b1e394f`):** the
operand is percent-decoded before the path rules see it.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` -- `test_a_percent_encoded_file_url_is_decoded`.

## F207 — `takeown` of the reference tree reached no rule · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06 (fix lane FX-G4)

**Finding (reviewer H, H-M5).** Taking ownership of `reference/` lets the owner rewrite its ACL, so
it lifts the Layer-2 deny as `icacls /setowner` does (F192), but `takeown /f <reference> [/r]` was
allowed. **Fix (`b1e394f`):** it asks like the other lift forms; `takeown` elsewhere, or of an
ancestor without `/r`, is not this rule.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` -- `test_takeown_of_reference_lifts_its_deny`,
`test_TWIN_takeown_elsewhere_or_of_an_ancestor_without_r`.

## F208 — a `<<` shift inside arithmetic opened a heredoc and hid every later line · **DEFECT (measured)** · PRE-ARC · SECURITY · confidence 95% · ✅ FIXED 2026-10-06 (fix lane FX-G4)

**Finding (reviewer H, H5; present before 4.0).** `<<` inside `(( ))`, `for ((;;))`, `$[ ]`, `${ }`
or an assignment subscript `a[ ]=` is a left SHIFT, but the heredoc-opener scan read it as a heredoc
and treated every later line as its body: `rm -rf <game>` on line 2 was ALLOWED. A body ended by
`EOF` followed by a backtick was not seen to end either. **Fix (`b1e394f`):** those regions are
blanked for the opener scan, and `EOF` followed by a backtick ends a body. `5c05e6f` added six
syntax-class mutators to fuzz-guard for exactly these regions (`(( ))`, `for ((;;))`, `$[ ]`,
`a[ ]=`, `${x//<<EOF/}`, `EOF` + backtick); re-anchored E2 (`EOF)`).

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` (classes `TestH5ShiftIsNotAHeredoc`,
`TestH5BacktickTerminator`: `test_shift_in_arithmetic_command_subscript_or_expansion_opens_no_heredoc`,
`test_TWIN_a_real_heredoc_after_or_beside_those_forms_still_opens`, `test_EOF_backtick_ends_the_body`,
`test_TWIN_a_backtick_elsewhere_on_the_line_is_body`) and `scripts/fuzz-guard.py`.

## F209 — git config from the environment can unforce `git clean`, and the guard did not see it · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06 (fix lane FX-G4)

**Finding (reviewer H, H6).** An unforced `git clean` is a no-op by default (`clean.requireForce`),
so the wipe rules judge it as harmless. Git also reads configuration from the environment
(`GIT_CONFIG_COUNT` with `GIT_CONFIG_KEY_n`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_GLOBAL`, ...) and
from `--config-env=...requireForce`, any of which can set `requireForce=false`; F192 covered only
`-c clean.requireForce=false`. **Fix (`b1e394f`):** any of them, as a prefix or assigned anywhere in
the command, makes `git clean` forced. The fuzz seed (`5c05e6f`) starts with `env`: a path glued
onto an assignment is a command that never runs git (a mutant, not a bypass).

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` (class `TestH6GitConfigFromTheEnvironment`:
`test_git_config_env_forces_an_unforced_clean`, `test_TWIN_other_env_or_no_env_leaves_the_clean_unforced`).

## F210 — what `echo`/`printf` prints into a shell is its program · **DEFECT (measured)** · PRE-ARC · SECURITY · confidence 90% · ✅ FIXED 2026-10-06 (fix lane FX-G4)

**Finding (reviewer H, H7; allowed at e39853f and in v3.3.1).** `echo rm -rf "<game>" | bash` (the
words unquoted too) ran the deletion and was ALLOWED: text piped into a shell was not read as a
program. **Fix (`b1e394f`):** what `echo`/`printf` prints into a shell is judged as its program; a
printf format that cannot be reproduced is the inability path (ask). The fuzz run (`5c05e6f`) found
that a quoted, ANSI-C or pathed `echo` left a quote residue that swallowed the program; fixed with a
regression test.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` (class `TestH7H8TextPipedIntoAShell`:
`test_echo_or_printf_piped_into_a_shell_is_its_program`,
`test_TWIN_a_harmless_program_a_non_shell_or_a_script_argument_is_not`,
`test_a_quoted_or_pathed_echo_is_still_the_producer`).

## F211 — a process substitution run as the script was not read as a program · **DEFECT (measured)** · PRE-ARC · SECURITY · confidence 90% · ✅ FIXED 2026-10-06 for an echo/printf producer (fix lane FX-G4)

**Finding (reviewer H, H8; allowed at e39853f and in v3.3.1).** `bash <(echo rm -rf <game>)` (and
`sh`, `source`, `.` with a `<(...)`) runs what the substitution prints, and was ALLOWED. **Fix
(`b1e394f`):** a process substitution that IS the script is its program. The fuzz run (`5c05e6f`)
found `x) bash <(echo ...) ;;` read as an unclosed `<(` (an ask); fixed. **USER DECISION 2026-10-06
(`16d0ec5`):** a producer other than echo/printf is allowed unjudged (F219); `bash -n <(...)`
executes nothing and never asks or denies. Echo/printf producers stay fully judged.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` --
`test_a_process_substitution_run_as_a_script_is_its_program`.

## F212 — the x4refguard lift rule asked on the toolkit's own `apply --toolkit "$X4_TOOLKIT"` · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-06 (fix lane FX-G4)

**Finding (reviewer H, H9).** `ee040bc` (F192) made an x4refguard action passed through a variable
ask, by asking on ANY unresolved word in the call -- so the documented
`x4refguard.py apply --toolkit "$X4_TOOLKIT"` asked too: an in-arc regression. **Fix (`b1e394f`):**
only the ACTION position names the action; `remove` and an unresolved action still ask, a variable
in an option value does not.

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` (class `TestH9RefguardActionPosition`:
`test_an_unresolved_ACTION_asks`, `test_TWIN_a_variable_in_an_OPTION_value_is_not_the_action`).

## F213 — following the OpenCode refusal's advice made cmd commands judged as bash · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-06 (fix lane FX-G4)

**Finding (MEASURED by reviewer G, G-I1).** For a shell it cannot judge (`cmd`) the plugin refused
every bash call and said "set X4_OPENCODE_SHELL". Since F176's fix the override BEAT the detected
shell, so following that advice made the guards judge a cmd command as bash: `del /s /q
<reference>\libraries` and `rmdir /s /q <reference>` were allowed under `--shell bash`. **Fix
(`a0af3c8`):** `X4_OPENCODE_SHELL` no longer replaces the detected grammar. When it disagrees with
the shell derived from OpenCode's `shell` config (or `$SHELL`) every bash call is refused (inert);
when it agrees it changes nothing. It remains the installers' declaration for rendering the
OpenCode skills for bash on Windows. The refusal now says to set OpenCode's own `shell` to pwsh,
powershell or bash.

**RE-DERIVED BY:** `tests/test_opencode_plugin_node.py` --
`test_X4_OPENCODE_SHELL_can_only_CONFIRM_the_detected_shell`,
`test_TWIN_an_AGREEING_X4_OPENCODE_SHELL_changes_nothing`, `test_SHELL_decides_the_grammar`.

## F214 — an unresolvable configured shell: OpenCode falls back, the plugin did not · **DEFECT (read)** · IN-ARC · confidence 85% · ✅ FIXED 2026-10-06 (fix lane FX-G4)

**Finding (reviewer G, G-M2; READ OpenCode `packages/core/src/shell.ts` `select()`).** When the
configured `shell` (or `$SHELL`) does not resolve to a program, OpenCode silently runs its platform
default (Windows `shell: zsh` without zsh runs pwsh), while the plugin judged the configured one.
**Fix (`a0af3c8`):** the plugin checks resolvability (rooted: a file; bare: PATH/PATHEXT; Windows
`bash`: Git Bash) and refuses rather than guess the fallback.

**RE-DERIVED BY:** `tests/test_opencode_plugin_node.py` --
`test_a_shell_that_does_not_RESOLVE_is_refused_not_guessed`.

## F215 — the OpenCode plugin's configured shell was shared across instances · **DEFECT (inferred, then tested)** · IN-ARC · confidence 85% · ✅ FIXED 2026-10-06 (fix lane FX-G4)

**Finding (reviewer G, G-M3, INFERRED).** `configShell` was a module-global, so in one process one
OpenCode instance's config decided the grammar for every other. **Fix (`a0af3c8`):** per plugin
instance; the node driver gained `instances` to test it.

**RE-DERIVED BY:** `tests/test_opencode_plugin_node.py` -- `test_the_configured_shell_is_PER_INSTANCE`.

## F216 — a conformance re-run that failed counted as checked · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-06 (fix lane FX-G4)

**Finding (reviewer G, G-M4).** `x4guard conformance` re-runs each reference deny/ask under
`X4_GUARD_CHECK` to tell an inert verdict from a checked one. Only exit 2 was special: exit 1 or 127
fell back to the first run's verdict and counted as checked -- the F174 shape, one step later.
**Fix (`56866be`):** any other non-zero re-run is an error, like a failing first run. Twins: re-run
exit 0 keeps the deny, exit 2 stays inert.

**RE-DERIVED BY:** `tests/test_x4guard_conformance_cli.py` --
`test_reference_the_GUARD_CHECK_rerun_exit_decides`.

## F217 — `verify-hook-tests`' silent? column found no test for any predicate · **DEFECT (measured)** · VERIFIER · confidence 95% · ✅ FIXED 2026-10-06 (fix lane FX-G4)

**Finding (MEASURED by FX-G4's coverage run).** The silent? column (which test must NOT fire for each
predicate) read NONE for all 32 of 32 predicates. `failed_tests()` returns `Class.method` since F197
(`a2c6590`), while `test_sources()` is keyed by the bare method name (F196), so the lookup found no
source for any failing test. Present at e39f675 -- a defect in the CHECKER, not in any guard.
**Fix (`141bb8c`):** the
lookup uses the method part. The same run found the mutant "G-OUT: only at a real command position"
no longer caught after `64bce54` (the unseeded join hid the twin's operand); the twin gained an
absolute operand inside the fragment (red with the clause removed, green with it).

**RE-DERIVED BY:** `scripts/verify-hook-tests.py` (its silent? lookup) and
`.claude/hooks/test_hook_facts.py` -- `test_TWIN_a_relative_operand_does_not_join_the_SESSION_directory`.

## F218 — the restored F183 advisory joined substitution debris to the session folder · **DEFECT (measured)** · IN-LANE · confidence 95% · ✅ FIXED 2026-10-06 (fix lane FX-G4, before merge)

**Finding (MEASURED over the 44,009-command replay).** F183's fix gave a substituted verb below the
game root the X4-directory delete advisory back. Its operands joined the SESSION cwd -- the game root
in the replay -- so a substitution fragment the segmenter read as a verb (`C=$(jq -r '.x' < f); bash
-n -c "$C"`, `$([ "$(git ...)" = ...`) put its debris operand inside the game folder: 6 false
advisories. Never on master (fixed inside the lane). **Fix (`64bce54`):** the advisory's operands
are judged unseeded (as lane F's other ask-or-advise rules that name no path); `cd <game> && $(echo
rm) -rf libraries` still advises. Twin: the replay's own shape (red with the seed).

**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` --
`test_TWIN_a_relative_operand_does_not_join_the_SESSION_directory`.

## F219 — a process substitution from another producer is run unjudged · **SCOPE (measured, accepted)** · confidence 90% · ⏳ OPEN (USER DECISION 2026-10-06)

**Accepted residual of F211.** A process substitution run as a script whose producer is not
`echo`/`printf` (`bash <(curl ...)`, `source <(sed -n ... f.sh)`) is allowed unjudged: what it
prints cannot be known before it runs, as with running a script file the guards cannot see into.
Judging it as the inability path asked on 4 of the 44,009 replay commands, all ordinary work
(reported by FX-G4); **USER DECISION 2026-10-06:** allow them. `bash -n <(...)` executes nothing.

## F220 — a settings patch is judged on its raw text, so a harmless `permissions` mention is denied · **SCOPE (read, accepted)** · confidence 80% · ⏳ OPEN

**Accepted residual of F204 (reported by FX-G4, not re-measured here).** The file-edit tools are
judged on the RESULTING settings file, where the top-level `permissions` block is exempt. A
Codex/OpenCode patch to a `.claude` settings file is judged on its raw text, so a patch adding a
`permissions` entry that merely mentions `X4_GUARD` is still DENIED. A false deny on a rare edit,
never a missed one; the remedy is the file-edit tool.

## F221 — four ways to weaken the guards through settings or the `.claude` tree · **SCOPE (measured, open)** · confidence 90% · ⏳ OPEN (USER DECISION 2026-10-06: record only for v4.0)

**Finding (MEASURED by reviewer H, delta review; every probe allowed).** (a) `"disableAllHooks":
true` in a Claude Code settings file switches every hook off. (b) `X4_CONFIG`, `X4_GAME`,
`X4_PYTHON` or `PATH` in a settings `env` block reaches every hook, so it can move every root (or
the interpreter the guards run under) away from what they protect. (c) A Write through a directory
junction to `.claude` (`mklink /J jl .claude`, then a Write to `jl/settings.json`) lands in `.claude`
under a path no rule names. (d) A shell REPLACEMENT of `.claude` (`mv <dir> .claude`, `cp -r
x/.claude .`, `ln -s` onto `settings.json`) was allowed. **USER DECISION 2026-10-06:** record only; not fixed in 4.0. What still holds: the OS-level
`reference/` protection, once applied, blocks a delete inside it whatever the guards do; and the
session start names a settings file that sets `X4_GUARD` (F193).

## F222 — hints named a command that refuses in the case they were printed for · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-06 (FX-B3, FX-B4, FX-Z)

**Finding (reviewers F and I, delta review; every case run against its refusal).** The 4.0 refusals
(F160, F177, `X4_CONFIG`) made several printed hints commands that cannot run where they are
printed. `x4doctor`'s `layer2.reference` said `apply` for a tree without `.unpacked-and-locked`,
which `apply` refuses (FX-B3 `c412708`). Its unpack hint had no `--reference`, refused under an
exported `X4_REFERENCE` differing from the config, and said nothing of a foreign `X4_CONFIG`, which
the unpack refuses whatever the flags (FX-B4 `892970c`). `unpack-reference.sh`'s lift steps and its
no-python, failed-apply and unreadable-state hints were relative (`python scripts/x4refguard.py
remove`, `bash bin/unpack-reference.sh`): runnable only from the toolkit folder, refused by a foreign
`X4_TOOLKIT` (FX-B4 `3e4b404`). And (FX-Z, MEASURED RED at 4d92732) `x4doctor`'s
`python ".../x4refguard.py" apply` refused "name DIFFERENT roots" under an exported `X4_REFERENCE`
and "names a config OUTSIDE this toolkit" under a foreign `X4_CONFIG` (`c13afcf`), while the
stale-reference session message gave relative commands (`14becad`). **Fix:** every hint is an
absolute command with `--toolkit` and `--reference` naming the tree just judged; where no flag can
lift a refusal, the hint says to clear `X4_CONFIG` first (bash and PowerShell spellings).

**RE-DERIVED BY:** `tests/test_x4doctor.py` -- `test_FXB3_layer2_WITHOUT_the_sentinel_says_UNPACK_not_apply`,
`test_FXB4_the_unpack_hint_names_the_tree_the_doctor_JUDGED`,
`test_FXB4_an_inherited_FOREIGN_X4_CONFIG_hint_says_clear_it_FIRST`,
`test_FXZ_the_apply_hint_RUNS_under_an_exported_X4_REFERENCE`,
`test_FXZ_the_apply_hint_RUNS_under_a_FOREIGN_X4_CONFIG`,
`test_FXZ_TWIN_a_hint_WITHOUT_the_flags_REFUSES_under_the_same_export` (the instrument can go red);
`tests/test_unpack_reference_layer2.py` -- `test_FXB4_the_LIFT_steps_are_absolute_commands_with_toolkit`,
`test_FXB4_a_FAILED_apply_hint_is_an_absolute_command_with_toolkit`,
`test_FXB4_the_NO_PYTHON_hint_is_an_absolute_command_with_toolkit`,
`test_FXB4_the_UNREADABLE_layer2_hint_is_an_absolute_command_with_toolkit`; and `scripts/test-hooks.sh`
(the three stale-reference rows).

## F223 — the installers took an inherited root variable for a destination the user named · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-06 (FX-B2, FX-B3)

**Finding (delta review).** Both installers read two root variables as a destination: `X4_TOOLKIT`
(the separate layout) and `X4_GAME` (the in-game layout). An exported value was treated as one the
user had NAMED, so `--yes` (or no terminal) installed into whatever the shell happened to carry, and
the over-existing refusal offered a rerun line for it. 2 of 2 such variables. **Fix:** FX-B2
`d524bf4` (`X4_TOOLKIT`) and FX-B3 `0255bc3` (`X4_GAME`): an inherited value is a default, said out
loud; `--yes`/no terminal refuses naming its source; no rerun line is offered for it; `--method
global` refuses an inherited `X4_TOOLKIT` naming another toolkit; an explicit flag proceeds.

**RE-DERIVED BY:** `tests/test_install_over_existing.py` --
`test_FXB2_3_an_INHERITED_X4_TOOLKIT_is_not_a_named_destination`,
`test_FXB2_3_TWIN_an_explicit_toolkit_with_an_inherited_one_proceeds`,
`test_FXB3_an_INHERITED_X4_GAME_is_not_a_named_in_game_destination`,
`test_FXB3_TWIN_an_explicit_game_with_an_inherited_X4_GAME_proceeds`.

## F224 — bash and Python read different files for the same `X4_CONFIG` spelling · **DEFECT (measured)** · IN-ARC · confidence 95% · ✅ FIXED 2026-10-06 (fix lane FX-B4)

**Finding (reviewer I; 6 of 16 spellings disagreed).** The bash loader tested the raw `X4_CONFIG`
with `-f`, while Python opens the path the OS resolves: `x4-paths.env/` and, on Windows,
`x4-paths.env.` and `x4-paths.env ` were READ by every Python tool and "missing" to every guard,
which then fell back to the default reference root -- the two halves of the toolkit configured from
different files (the F151 shape). **Fix (`e69c8fb`):** the bash loader takes the spelling Python
opens (trailing separators; on Windows trailing dots and spaces), only when that is a file, and
`X4_CONFIG` carries it, so the guards' name checks see the file actually read. 0 of 16 disagree
after; every clause mutation-killed.

**RE-DERIVED BY:** `tests/test_config_precedence_agrees.py` --
`test_FXB4_both_loaders_read_the_SAME_file_for_every_X4_CONFIG_spelling`; and
`tests/test_unpack_reference_layer2.py` -- `test_FXB4_the_unpack_and_python_AGREE_on_every_X4_CONFIG_spelling`.

## F225 — the unpack turned a relative path into `/<name>` · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-06 (fix lane FX-B4)

**Finding (MEASURED by FX-B4 for reviewer I).** `bin/unpack-reference.sh`'s `_x4_canon` made a bare
relative path absolute at `/`: `cd <toolkit>; X4_CONFIG=x4-paths.env bash bin/unpack-reference.sh`
was refused as a foreign config (Python accepts it), and `X4_REFERENCE=refrel` was refused as
DIFFERENT from the config's own tree while that tree did not exist. **Fix (`e69c8fb`):** relative
paths resolve against the cwd; the check judges the file the loader read; on Windows the result is
one spelling (`pwd -P` says `/tmp/...` for `C:/...Temp` and `/c/...` for `/c/...Temp`, MEASURED).
Residual leads: F229.

**RE-DERIVED BY:** `tests/test_unpack_reference_layer2.py` --
`test_FXB4_an_inherited_RELATIVE_X4_REFERENCE_is_the_tree_it_names`.

## F226 — `x4doctor` read a moved reference tree as "not unpacked yet" · **DEFECT (measured)** · IN-ARC · confidence 85% · ✅ FIXED 2026-10-06, PARTIAL (fix lane FX-B4)

**Finding (reviewer I).** x4refguard reports a missing tree as "does not exist (not unpacked yet, or
renamed or moved)": it cannot tell. `x4doctor`'s `roots.reference` and `layer2.reference` said "not
unpacked" (TODO: run the ~27 GB unpack) regardless -- for a tree the user had moved, a needless
re-unpack into the old place. **Fix (`892970c`):** TODO only when nothing suggests a move; UNKNOWN
(exit 3), in x4refguard's words, when `<toolkit>/.claude/.reference-buildid` exists (written only
after a finished unpack) or a finished unpack (its sentinel) sits at `<toolkit>/reference` while the
config names somewhere else. **PARTIAL:** a move that leaves neither signal still reads TODO.

**RE-DERIVED BY:** `tests/test_x4doctor.py` --
`test_FXB4_a_MOVED_tree_is_UNKNOWN_not_a_fresh_unpack__buildid_marker`,
`test_FXB4_a_MOVED_tree_is_UNKNOWN__a_finished_unpack_at_the_DEFAULT_place`,
`test_FXB4_TWIN_an_empty_default_folder_is_no_evidence_of_a_move`.

## F227 — CI install legs wrote the Windows runner's real user environment · **DEFECT (measured)** · IN-ARC · confidence 90% · ✅ FIXED 2026-10-06 (FX-B2, FX-B3, FX-B4)

**Finding (delta review).** The installers set `X4_TOOLKIT` at OS level (Windows: `HKCU\Environment`
through `scripts/x4-userenv.ps1`). A GitHub Windows runner keeps HKCU across steps, so a later
install leg inherited an earlier leg's toolkit -- the F160 shape inside CI. Fixed in three passes as
each set of legs was found: the separate PowerShell legs (FX-B2 `d524bf4`), the in-game and global
PowerShell legs (FX-B3 `6a209da`), and the 2 of 2 bash legs with no `if:`, which also run on Windows
(FX-B4 `572fc57`). **Fix:** each leg exports its own `X4_INSTALL_ENV_REGKEY` under `X4ToolkitTests`
(the seam `x4-userenv.ps1` honours; nothing reads it on Linux) and drops `X4_TOOLKIT`.

**RE-DERIVED BY:** `tests/test_install_over_existing.py` --
`test_FXB3_every_CI_powershell_install_leg_is_ISOLATED`,
`test_FXB4_every_CI_BASH_install_leg_that_runs_on_Windows_is_ISOLATED_too` (every install step not
gated to Linux, with keys unique across bash and PowerShell legs).

## F228 — a macOS privacy-protected Documents folder may make the identifier scan PARTIAL · **SCOPE (inferred, open)** · confidence 60% · ⏳ OPEN

**Lead (FX-B4, unverified on a Mac).** macOS can refuse a process access to `~/Documents` (TCC)
without the folder being missing. `scan-identifiers.py` would then report that location unreadable,
PARTIAL, exit 3 (F182), on a machine with nothing to find. It fails closed (never "clean"), and a
permission refusal no longer crashes the note (`efb4b3c`, tested with a simulated EPERM:
`test_FXB4_a_PERMISSION_refusal_like_macOS_privacy_is_PARTIAL_and_never_crashes`). Open until run on
a Mac.

## F229 — two false refusals in the root comparisons, both failing closed · **SCOPE (reported, open)** · confidence 60% · ⏳ OPEN

**Leads (reported by FX-B4, not re-measured here).** (1) `_x4_canon` in `bin/unpack-reference.sh`
resolves `..` lexically in a tail that does not exist yet, which can refuse a path that names the
same tree. (2) An `X4_REFERENCE` ending in a dot (`<tree>.`) is reported DIFFERENT
from the same tree in the config, although Windows opens both as one folder (the F185 alias). Each
is a refusal printing both paths, never a wrong action. Open leads.

## F230 — an unquoted multi-word substitution in a prefix assignment named the verb · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-06

**Finding (FX-G5, found while fixing reviewer J2 item 3; PRE-ARC, v3.3.1 gives the same verb).**
`tokens()` splits an UNQUOTED `A=$(echo a b)` at its spaces, and `_verb_token` skipped only the
first word as an assignment, so `a` became the command: `A=$(echo a b) rm -rf <game>`, the backtick
form and the reference spelling were ALLOWED end to end (deny for the plain command). **Fix:**
`_collapse_assignment_substs` replaces each unquoted, quote-aware-balanced substitution inside an
assignment word with `$(_)` before tokenising; an unbalanced one (a segment cut inside it) is left
as written. **RE-DERIVED BY:** `TestGOutSubstitutedVerb.test_a_prefix_ASSIGNMENT_does_not_hide_the_verb`,
`scripts/test-hooks.sh` row "J2-3: an unquoted multi-word prefix substitution hides no verb".

## F231 — a closed prefix substitution hid a substituted verb · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-06

**Finding (reviewer J2 item 3).** `_subst_verb` refused the command position whenever ANY earlier
token held `$(` -- written for a segment cut inside an assignment's own substitution (13 corpus
false advisories) -- so `x=$(printf rm); A=$(true) $x -rf <reference>` was allowed. **Fix:** only an
UNCLOSED substitution refuses; a closed one (an assignment, a wrapper's value) is stepped over.
**RE-DERIVED BY:** the same test, and the corpus twin with the ROOT as the fragment's operand
(`test_TWIN_a_literal_verb_variable_or_an_operand_elsewhere_is_not_this_rule`).

## F232 — the process-substitution rule's `bash -n` and option handling · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-06

**Finding (reviewer J2 item 2).** Any `-n`-bearing word anywhere in the segment exempted it, and the
script operand was the first non-dash word, so `bash <(echo -n rm -rf <game>)`, `bash <(echo ...) -n`
and `bash -o pipefail <(echo ...)` were allowed. **Fix:** only the shell's own option words before
the operand count; `-o`/`-O`/`+o` (also clustered) and `--rcfile`/`--init-file` step over their
value; `+n` is not `-n`. Producers other than echo/printf stay allowed (F219, user decision).
Residual (corrected by reviewer K, MEASURED): not false-positive only -- the fix took a lone `-`
as the script, so `bash - <(echo rm -rf <game>)`, `sh - <(...)` and `bash -x - <(...)` went deny ->
ALLOW (F242), and `bash -n +n <(...)` stayed allowed (F243); `bash -o noexec <(...)` is judged.
**RE-DERIVED BY:**
`.claude/hooks/test_hook_facts.py`, `TestH7H8TextPipedIntoAShell.test_only_the_shells_OWN_options_and_their_values_are_stepped_over`.

## F233 — a quoted-looking `cd` concatenation was taken as literal · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-06

**Finding (reviewer J2 item 1).** `patch_paths._literal_cd` dequoted any operand that started and
ended with `'`, so `cd ''$X4_REFERENCE'' && codex --codex-run-as-apply-patch ...` judged a literal
path. **Fix:** literal only when the operand is exactly `'[^']*'`; anything else meets the
expanded-cd refusal (F203). Residual (reviewer K, MEASURED): a MIXED-quoted operand with no
expansion (`'<REF>'/libraries`, `"<REF>"'/sub'`) was still read with its quotes and judged under no
root -- closed by F245. **RE-DERIVED BY:** `tests/test_patch_paths.py`
`test_a_shell_patch_after_an_EXPANDED_cd_is_refused` (3 new spellings x 2 forms).

## F234 — git configuration from a file or from the same command forced no clean · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06

**Finding (reviewer J2 item 5).** `-c include.path=<file>` and `git -C <game> config
clean.requireForce false && git -C <game> clean -dx` both deleted with no `-f` and were allowed.
**Fix:** `-c include.*`/`includeIf.*`, any `--config-env`, and an in-command `git ... config ...`
naming requireForce or an include make the clean forced (the rule asks). Order-blind by design.
**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py`, `TestJ2GitCleanForcedByConfigIncludeOrCommand` (twins: `-c core.x`,
`-c user.include`, `git log --grep requireForce`, `echo requireForce`).

## F235 — the alternate word of an unassigned variable was dropped · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-06

**Finding (reviewer J2 item 9; PRE-ARC).** `_apply_op` read a variable the command never assigned
as UNSET, so `${PATH:+$X4_GAME}` resolved to "" and `rm -rf "${PATH:+$X4_GAME}"` was allowed.
**Fix:** for `+`/`:+` the alternate word is judged unless the command itself assigned the variable
(an assigned empty value still gives "" for `:+`). **This fix lost the empty branch** (reviewer K
C1, MEASURED): judging the alternate word ALONE allowed `rm -rf "${NOPE:+zz}<reference>"`,
`"${NOPE+zz}<reference>"` and `"${NOPE:+./junk}<game>"`, denied at 44c2d00. **Restored by F241**:
every value is judged. **RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py`, `TestJ2AlternateOfAnUnknownVariable`,
`TestK_C1EveryValueOfAnUnknownVariable`.

## F236 — `.`/`..` components and `::$DATA` in `X4_CONFIG` · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06

**Finding (reviewer J2 item 4, the F224 class).** Python's pathlib/Win32 open `x4-paths.env/.`,
`sub/../x4-paths.env/.` and `x4-paths.env::$DATA`; the bash `-f` test did not, so the guards fell
back while the tools read the file. **Fix:** both halves (and `x4doctor._config_spelling`) drop a
case-insensitive `::$DATA` suffix on Windows, then resolve `.`/`..` lexically (normpath;
`_x4_lexnorm` in `_x4-env.sh`). **Open lead:** a NAMED stream (`x4-paths.env:other`) is left as
written in both halves; what each half reads for it is not measured. **RE-DERIVED BY:**
`test_config_precedence_agrees.py` SPELLINGS (9 new rows) and
`test_FXG5_python_names_the_file_by_the_SAME_spelling_bash_takes`; `test_x4doctor.py` `test_FXG5_*`.

## F237 — the finished-unpack marker travelled with a source tree · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-06

**Finding (reviewer J2 item 7).** READ: `.claude/.reference-buildid` was in neither installer's
lists (git-ignored, so only a non-git source carried it). MEASURED: both installers copied it from
a synthetic unpacked source. **Fix:** keep-local in `install.sh` and `install.ps1`. **RE-DERIVED BY:**
`tests/test_install_over_existing.py`, `test_the_SOURCE_machines_reference_buildid_marker_does_not_travel[sh|ps1]` (twin: the destination's
own marker survives a reinstall, which a prune-list fix would fail).

## F238 — verify-hook-tests pooled same-named test sources · **DEFECT (read)** · confidence 90% · ✅ FIXED 2026-10-06

**Finding (reviewer J2 item 8).** After 141bb8c the silent? column looked sources up by method
name while failures and targets are `Class.method`; `vht.test_sources` concatenated same-named tests'
sources, so a failure in one class was credited with what its namesake names. **Fix:** keyed
`Class.method`, looked up via `credited()`. **RE-DERIVED BY:** `tests/test_verify_hook_tests.py`.

## F239 — a correct third-party adapter could not pass a settings file's text · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06

**Finding (coordinator item 10, MEASURED on 44c2d00).** `test_codex_conformance` and
`test_toy_agent_conformance` failed: #204 ("TWIN: Write settings.json without X4_GUARD: allowed")
and #224 ("TWIN H4a: an ordinary hooks command"). The engine already passes the content (`/args/text`,
`{{CONTENT}}`); the toy adapter, written from ADAPTING.md alone, dropped it -- ADAPTING.md never said
a settings write needs its text -- and `x4guard check` had only `--command` (judged as patch text,
which cannot prove an existing key removed). Codex's `Add File` over the replayed settings.json
(which holds X4_GUARD at the end of the harness) was denied for that reason. **Fix:** `x4guard check
--content` (a whole-file write, judged as Claude Code's Write); `settings_guard` judges an add-only
patch on its own text (an `Add File` replaces the file or fails -- INFERRED from the operation's
meaning, not measured against Codex); ADAPTING.md section 2 says when and how to pass the text; the
toy adapter passes `--content` for a settings `write_file`. READ: `codex.py` and `opencode.py` pass
the text for settings files in real use. Residual: an OpenCode `write` (whole file) is passed as raw
text, so over a file that already holds the key it is a false deny (F220). **RE-DERIVED BY:**
`test_settings_guard.py` `test_x4guard_check_content_is_judged_as_the_whole_resulting_file`,
`test_codex_ADD_FILE_over_a_file_that_sets_it_is_judged_on_what_it_writes`; the two conformance tests.

## F240 — conformance rows judged against a config the harness later rewrote · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06

**Finding (FX-G5, while closing F239).** With #204/#224 fixed, `test_every_replayable_case_agrees`
went on to its drift assertion (R7-3) and failed: #153/#154 ("an 'exit 0' line ...") were DENY at
decide() time and ALLOW on replay, because `scripts/test-hooks.sh` overwrites `cfgtk/x4-paths.env`
(to `X4_GUARD=off`, then empty) after them, and the replay reads the sandbox as it was LEFT. The
same end-state shape made #204 replay against a settings.json holding X4_GUARD. **Fix:** the two rows
use their own folder (`cfgtk-exit0`) that nothing rewrites. **Open:** no audit of other rows whose
inputs the harness rewrites later; the drift assertion is what would show one. **RE-DERIVED BY:**
`tests/test_codex_conformance.py::test_every_replayable_case_agrees` (0 drift of 210).

## F241 — the F235 fix judged one value of an unassigned variable and lost the empty one · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-06

**Finding (reviewer K C1; IN-ARC, FX-G5 f03d19b).** F235 made `${V:+w}` of a variable the command
never assigned return the alternate word -- ONE of its values. The unset value (`""`) was no longer
judged: `rm -rf "${NOPE:+zz}<reference>"` IS `rm -rf <reference>` when NOPE is unset, and it went
deny -> ALLOW, with `${NOPE+zz}<reference>` and `"${NOPE:+./junk}<game>"`. **Fix:**
`hook_facts.resolve_variants` returns every value: each unassigned name under `-`/`=`/`+` (colon or
not) is unset, empty or set (its own value, unresolved), enumerated PER NAME (exhaustive to 4
names, then each name in each state with the rest uniform). Every path rule iterates them (operands
via `resolve_all`, the substituted-verb root test, the search roots, `xrcat`, `icacls`/`takeown`, the
durable-record rule) and every carrier (`bash -c`, `eval`, `cmd /c`) carries one command per
value -- found by this lane's fuzz seeds: one resolve() there judged `bash -c 'rm -rf
"${NOPE:+zz}<reference>"'` as the alternate only; a `cd` takes the first value landing under a
root, else the plain one. **Open:** a PowerShell host payload (`powershell -c ...`) still
resolves ONE value (a translation per value costs a pwsh run).
**RE-DERIVED BY:** `TestK_C1EveryValueOfAnUnknownVariable`; E2E rows `K-C1` in `scripts/test-hooks.sh`.

## F242 — a lone `-` was read as the script of a process-substitution shell · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-06

**Finding (reviewer K C2; IN-ARC, FX-G5 f03d19b).** `_procsub_program` treated `-` as the script
operand, so `bash - <(echo rm -rf <game>)`, `sh - <(...)` and `bash -x - <(...)` (deny at 44c2d00)
were allowed. In bash `-`, like `--`, ends the options and the NEXT word is the script. **Fix:**
exactly that. **RE-DERIVED BY:** `test_a_lone_dash_ends_the_options_and_plus_n_cancels_minus_n` and
its twin (`bash -n -- +n <(...)`: the script is the file `+n`).

## F243 — `+n` after `-n` still counted as a syntax check · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-06

**Finding (reviewer K I3).** `bash -n +n <(echo rm -rf <game>)` executes the script; any `-n` among
the options exempted it. **Fix:** the LAST n-toggle decides (`-n`/`+n`, clustered `-xn`/`+xn`, and
`+o noexec` turning it off). `-o noexec` is not exempted (false positive only, as before).
**RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py`,
`test_a_lone_dash_ends_the_options_and_plus_n_cancels_minus_n` and its twin (as F242).

## F244 — a word holding a span with a blank or a separator split into a false command name · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06

**Finding (reviewer K I4; allowed at 44c2d00 and 47d06cd).** `tokens()` splits at every unquoted
blank and `segments()` at every unquoted separator, inside `$(...)`, backticks and `${...}` too. So
`A=${X:-echo a} rm -rf <game>` named `a}` the command, `env -C $(echo /tmp ) rm -rf <game>` named
`/tmp`, and `A=$(echo a; echo b) rm -rf <game>` was cut into `A=$(echo a` and `echo b) rm -rf
<game>` (verb `echo`) -- all allowed. **Fix:** `_verb_token` walks `_raw_words` (a word ends only
outside every span; an UNCLOSED span -- a cut fragment -- splits as before), the substituted-verb
position test reads the same words, and `facts()` walks `_spanning_view` of each command (closed,
unquoted, separator-holding substitutions collapsed) IN ADDITION to the command as written, the
top level's view seeded like the top level. Additive: no fact the written command produces is lost.
**Open:** scans that tokenise a segment on their own (`_git_destructive`'s token walk, the wrapper
skips in other rules) were not audited for spans. **RE-DERIVED BY:**
`test_a_word_holding_a_span_is_ONE_word`, `test_TWIN_spans_and_views_change_nothing_else`.

## F245 — a mixed-quoted `cd` before a shell patch kept its quotes · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-06

**Finding (reviewer K I1).** `patch_paths._literal_cd` dequoted only an operand wrapped whole in one
pair of quotes and took any other spelling as written, so `cd '<REF>'/libraries && codex
--codex-run-as-apply-patch '...'` judged the path `'<REF>'/libraries/...` -- under no root.
**Fix:** ONE fully single- or double-quoted word is dequoted (a double-quoted one is still checked
for expansions); any other word holding a quote is refused. **RE-DERIVED BY:** `tests/test_patch_paths.py`
`test_a_shell_patch_after_an_EXPANDED_cd_is_refused` (6 new spellings x 2 forms) and the literal-cd twin.

## F246 — other spellings of a git config write, and a moved HOME, left a clean unforced · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06

**Finding (reviewer K I2).** F234 matched the literal word `requireForce`, but git accepts the key
spelled `"clean.require""Force"` or `clean.require\Force`, or through a variable; and `HOME=/x` (or
`XDG_CONFIG_HOME`) makes git read a global config the guard cannot see. All left `git clean -dx`
in an X4 folder allowed. **Fix:** any `git config` in the command that is not a read (`--get*`,
`--list`, `-l`, `--show-*`, the `get`/`list` subcommands; and not a bare `git config`) forces the
clean, order-blind, in any carried command; a `HOME=` / `XDG_CONFIG_HOME=` assignment anywhere does
too. Behaviour change: `git config user.name x && git clean -dx` in an X4 folder now asks.
**RE-DERIVED BY:** `test_ANY_writing_git_config_or_a_moved_HOME_forces_the_clean`,
`test_TWIN_a_READ_of_git_config_leaves_the_clean_unforced`.

## F247 — an OpenCode settings edit was judged on its text, not its result · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06

**Finding (reviewer K I5).** `opencode.translate` passed an `edit`'s new text as the written text,
and `settings_guard` judged that text plus the file's CURRENT keys -- never the file after the
edit. Two edits, neither naming the key, built `"X4_GUARD": "off"`. **Fix:** the adapter passes the
edit (`old`, `new`, `replace_all`) and `settings_guard._judge_edit` applies it to the file as read
and judges the result; an empty `old` is judged both as the whole file and as prepended text. An
edit that does not apply here (the agent's tool may match fuzzily) is refused when its text could
form the key wherever it lands (`_could_form_key`: names it, holds a `\u` escape, is a piece of it,
starts with its tail, ends with its head, or could finish/open a `\u` escape) -- fail closed. A file
that already names the key still refuses any edit, as before. **RE-DERIVED BY:**
`tests/test_opencode_adapter.py` `test_a_two_step_edit_that_BUILDS_X4_GUARD_is_denied` and its twin.

## F248 — patch semantics chosen by sniffing the text · **DEFECT (read)** · confidence 90% · ✅ FIXED 2026-10-06

**Finding (reviewer K M1).** `settings_guard` read any written text containing `*** Begin Patch`
and only `*** Add File:` headers as an add-only patch -- whose result ignores the file's current
content -- whoever sent it. **Fix:** only when the caller says so: `x4guard.guard_payload(...,
patch=True)` sets `x4_patch`, from the Codex adapter's patch calls and `x4guard check --patch`.
**RE-DERIVED BY:** `tests/test_settings_guard.py` `test_patch_semantics_only_when_the_caller_SAYS_it_is_a_patch`.

## F249 — further `::` stream suffixes on `X4_CONFIG` · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06

**Finding (reviewer K M2; residual of F236).** On Windows, `x4-paths.env::$DATA/.`,
`x4-paths.env::$DATA::$DATA` and `x4-paths.env::$DATA ` open the file from Python and are missing
to the bash loader (3 of 16 probe spellings disagreed). **Fix:** a `::` beyond exactly one trailing
`::$DATA` names no config in `_paths` (`config_names_stream`) and `x4doctor` (`_config_stream`); the
bash loader already found none, so it is unchanged. **RE-DERIVED BY:** the spelling table in
`tests/test_config_precedence_agrees.py` (+7 rows) and `test_FXG6_a_further_stream_suffix_names_NO_config`.

## F250 — a `cd` with several possible targets judged by one · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-06

**Finding (FX-G7, the 432-row regression corpus replayed e39853f vs 8ed563f, row #397).** F241
made `cwd_track` take, of a `cd` target's values, the first that landed under ANY configured root.
From the toolkit, `cd "${NOPE:+x}<reference>"` has the values `x<reference>` (relative: joined
under the toolkit, a root) and `<reference>`; the first won and `rm -rf libraries` was judged in
the toolkit -- an advisory where e39853f denied. One chosen value is the wrong shape whatever the
rule: the strictest directory depends on the operand judged. **Fix:** `cwd_lanes` keeps one lane
per value (each with its own pushd stack); facts() judges every segment once per lane, pairing the
seeded and unseeded walks lane for lane (lanes split per VALUE, which does not depend on the
base). Bounded at 16 lanes; past it a lane takes the root-landing value. Probed: `cd`, `pushd`,
`cd` in `bash -c` and `eval`, a second relative `cd`, a redirect write, from the toolkit, the game
and an outside folder. **RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` `TestG7EveryCdValueIsALane`.

## F251 — a git work tree named through the environment by another segment · **DEFECT (measured)** · confidence 90% · ✅ FIXED 2026-10-06

**Finding (FX-G7).** Measured over 3 session folders x 6 targets x 12 spellings: `git -C`, `cd &&`,
`--work-tree X`, `--work-tree=X`, `--git-dir`+`--work-tree`, a `GIT_WORK_TREE=` prefix and `env
GIT_WORK_TREE=` all ASK for an X4 folder from every session folder; `export GIT_WORK_TREE=<X4
folder>; git clean -fdx` was ALLOW from a folder outside every root (e39853f and 8ed563f alike). It
was DENY from an X4 session folder only because the bare-wipe rule judged the session folder.
**Not a defect, recorded with it:** e39853f DENIED `--work-tree`/`GIT_WORK_TREE` wipes from the
toolkit (corpus #208-#212, #306) for the same reason -- it never read the work tree and judged the
session folder; from an outside folder it ALLOWED them, and from the toolkit it denied
`--work-tree=<plain folder>` too. 8ed563f's ASK is the `-C` form's verdict. **Fix:** an assigned
`GIT_WORK_TREE` / `GIT_DIR` (parent) feeds the named-wipe ASK of every wiping git segment, never the
bare deny's pairs. **RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` `TestG7GitWorkTreeNamedByAnAssignment`.

## F252 — `git clean` dry run read from any `-n` · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-06

**Finding (FX-G7).** In a scratch repo (git 2.48.1) `git clean -fdx -n --no-dry-run`, `-n --no-d`,
`-e -n`, `--exclude -n` and `--e -n` deleted both files; `--d` and `--dry` deleted nothing; `--n` is
refused as ambiguous. The guard returned "dry run" for any `-n` or prefix of `--dry-run`. **Fix:**
`_git_clean_dry` -- the last toggle wins, value options consume their value, `--` ends options, and
`_git_clean_long` accepts a prefix only when it names exactly one of git clean's long options
(with their `--no-` forms). **RE-DERIVED BY:** `.claude/hooks/test_hook_facts.py` `TestG7GitCleanDryRunIsTheLastWord`.

## F253 — Unicode separators shift AST marker lookup · **DEFECT (measured)** · confidence 99% · ✅ FIXED 2026-10-07

MEASURED: the pre-fix silent-swallow checker accused `_paths.py:444`, whose handler already
carried its justification. `str.splitlines()` also splits U+2028/U+2029 in a preceding docstring;
Python AST line numbers do not. Both AST-indexed marker readers in
`tests/test_no_silent_swallow.py` now split on LF after `read_text` newline normalization.
No production handler or exception policy changed. RE-DERIVED BY:
`tests/test_no_silent_swallow.py` `test_unicode_separators_do_not_shift_ast_marker_lines`
and `test_unicode_separators_do_not_shift_control_flow_markers`: marked and unmarked twins.

READ, open follow-up: `test_no_loose_only_reference_walk.py`, `test_no_packed_only_scan.py`,
and `test_mod_scope_is_explicit.py` also index splitlines output by AST line number.
Their current findings were not shown to be wrong in this takeover; this closure covers
the two silent-swallow readers, not every source-location consumer.

## F254 — Unconfigured gate cells discovered the acting toolkit's config · **DEFECT (measured)** · confidence 99% · ✅ FIXED 2026-10-07

MEASURED on toolkit 385cf6a: `edge_sweep` passed 34 of 39 cells; five unconfigured
cells instead resolved real installed data. The gate blanked X4_CONFIG and moved
CWD, but `_paths._locate_config` treats empty X4_CONFIG as unset and discovers
the acting toolkit's config through its source location. A read-only subprocess
probe reproduced that selection with every gate path variable blank; an explicit
missing-config control resolved neither game nor reference. No config values
were printed. This is a fixture isolation defect, not evidence that production
auto-discovery should be removed.

The gate now explicitly selects a real, empty config in its temporary fixture.
MEASURED after repair: all 39 cells passed, with the five refused calls returning
2 as originally required; no expected-code sets or production resolver changed.
RE-DERIVED BY: `tests/test_hook_false_positives_gate.py`
`test_edge_sweep_empty_config_overrides_self_discovery_with_blank_twin` proves
explicit empty selection and that blanking the override recreates self discovery.
`gates/edge_sweep.py` exercises the 39 actual CLI cells; the existing hostile
exit-code regression in `tests/test_audit0924_gates.py` remains enforced.

## F255 — Replay stability omitted verdict dependencies · **DEFECT (measured)** · confidence 99% · ✅ FIXED 2026-10-08

READ: `protect-bash.sh` invokes `hook_facts.py`, which invokes `ps_translate.ps1`
for PowerShell carriers inside Bash. The replay hashed only the two shell files.
MEASURED: changing each of four dependency fixtures voided only two of four readings;
after including the parser and translator, all four changes void the reading, and
an unchanged control passes. No hook policy changed. Old receipts lack these two
fingerprints and cannot retrospectively prove their stability. RE-DERIVED BY:
`tests/test_hook_false_positives_gate.py` `test_changing_each_verdict_dependency_voids_the_replay`.

## F276 — x4compat reported a duplicate for every shared wildcard icon key · **DEFECT (measured)** · confidence 92% · ✅ CLOSED 2026-10-11 (residuals OPEN)

MEASURED: x4compat reported a duplicate for every wildcard icon key (`icon#ship_*` and the
like) that two mods declare -- HARD for same-parent diff adds, UNION-KEY for full
`icons.xml` files. The engine compares the EXPANDED ids, not the wildcard keys. On the
maintainer's live modlist 3 of 3 wildcard HARD rows and 2 of 2 wildcard UNION-KEY rows were
false: 0 overlapping ids among the expansions of all installed extensions (178 exact, 769
with aliases). The engine log's 2 wildcard duplicates came from two mods no longer installed.
Fix: `d49fdae` (same-parent diff adds) and `373308c` (full-file UNION-KEY rows) compare the
expansions and report one row per shared id.

STILL OPEN within F276 (declared, not fixed): bare texture paths are NOT CHECKED (1 live
case); a diff-added wildcard is not compared with a full-file wildcard of the same key;
a wildcard expansion vs an explicit icon id is not compared; base/DLC never take part as
providers. INFERRED, not measured: the `.dds` alias and the absence of a subfolder search.
Related: GitHub issue #9 (`_merge._union_children` replaces same-named wildcard
declarations; FX-H3 reads per-mod files, so is unaffected) -- not fixed in this release.

RE-DERIVED BY: `tests/test_compat.py` `test_F276_TWIN_disjoint_expansions_are_SOFT_only`,
`test_F276_overlapping_expansions_are_one_HARD_row_per_shared_ID`,
`test_F276_full_file_overlap_is_a_UNION_KEY_row_per_shared_ID`.

## F275 — Wildcard icon keys reported as duplicates (open lead) · **LEAD (measured)** · confidence 90% · ✅ CLOSED 2026-10-11 by F276

F274's live run surfaced wildcard icon keys reported as duplicate HARD rows, while the
engine log showed duplicates only where wildcard EXPANSIONS overlap. Superseded by F276,
which holds the measurement, the fix and the residuals.

RE-DERIVED BY: `tests/test_compat.py` `test_F276_TWIN_disjoint_expansions_are_SOFT_only`.

## F274 — The same-parent duplicate check named the wrong mods and lost keys · **DEFECT (measured)** · confidence 92% · ✅ CLOSED 2026-10-11

MEASURED: the check reported only the first duplicated key, attributed it to every co-adder
(2 to 40 mods named where 2 or 3 provided the key; 6 rows on the maintainer's live modlist),
and dropped the parent SOFT row; 2 duplicated keys were never reported. Fix `65e2a10`: one
row per duplicated key naming that key's providers only, parent SOFT row kept. The survivor
of a duplicate id is NOT engine-measured; the rows say so.

RE-DERIVED BY: `tests/test_compat.py` `test_4_duplicate_row_names_only_the_keys_providers`,
`test_4_every_duplicated_key_gets_its_own_row`,
`test_4_the_parent_SOFT_row_still_names_every_adder`.

## F273 — Contested paths were a set of spellings · **DEFECT (measured)** · confidence 90% (Windows; 75% Linux, inferred) · ✅ CLOSED 2026-10-11

MEASURED: one case alias was written twice (1 of 11,301 documents on the maintainer's
install). INFERRED, no Linux machine: on a case-sensitive filesystem a differently spelled
loose overlay would have been silently dropped. Fix `d825d15`: one entry per case-folded
path; off Windows a case-folded loose lookup that refuses a folder holding two spellings.

RE-DERIVED BY: `tests/test_case_alias_vpaths.py` `test_6_two_spellings_are_ONE_contested_vpath`,
`test_6_overlay_root_REFUSES_two_spellings_in_one_folder`.

## F272 — `refs` zero carried a document count, not the forms searched · **DEFECT (measured)** · confidence 90% · ✅ CLOSED 2026-10-11

MEASURED: `refs` matched exact values of five attributes while its zero carried a complete
document count; a document count says nothing about which reference forms were searched.
The issue's worked example missed 18% of references. Fix `3cf199b`: searches bullet/@class,
add_blueprints/@wares|@macros lists and `macro.`/`ware.` spellings, and declares its scope.
`$variable` and expression attributes remain out of scope, declared.

RE-DERIVED BY: `tools/basex/test_ask.py` `test_5_a_refs_ZERO_names_the_forms_searched_and_what_is_not`;
`tools/basex/test_ask_e2e.py` `test_5_refs_finds_each_reference_form`.

## F271 — BaseX queries were decoded in the Windows codepage · **DEFECT (measured)** · confidence 92% · ✅ CLOSED 2026-10-11

MEASURED: the query was passed to Java as a command-line argument, which Java decodes in
the Windows codepage, so a literal outside Windows-1252 arrived as `?` (codepoint 63 instead
of 28779) and a zero was certified for a query nobody wrote. stdout was decoded with the
locale (crash on 0x81); stderr is cp1252. Population: every ask/coverage query containing
such a character. Fix `4b7c5f7`: the query is a UTF-8 file passed through the single runner
`tools/basex/basex_run.py`; stdout strict UTF-8; stderr forced UTF-8.

RE-DERIVED BY: `tools/basex/test_ask.py` `test_7_the_query_travels_as_a_utf8_file_never_as_an_argument`;
`tools/basex/test_ask_e2e.py` `test_7_non_cp1252_LITERAL_reaches_basex_intact`.

## F270 — Edge sweep's "unconfigured" is an EMPTY config, not NO config · **SCOPE (read)** · confidence 95% · 📝 DOCUMENTED 2026-10-10

READ (reviewer M-4): since F254, every unconfigured `edge_sweep` cell blanks all path
variables AND selects an explicit, empty `X4_CONFIG` file, because blanking alone let the
toolkit discover its own private config from any CWD. That is an empty configuration. The
state a new user is in -- no `x4-paths.env` anywhere, `X4_CONFIG` unset -- reaches a
different branch of discovery, which the sweep cannot exercise on a configured machine.
The docstring said "the state a new user is actually in". It now says what the cells are
and names `scripts/verify-cold.sh` as the check for the no-config state: it refuses to run
until registry, game extensions and reference all resolve to None, then runs the suite and
every CLI. No cell changed; nothing was measured beyond reading both scripts.

RE-DERIVED BY: `gates/edge_sweep.py` docstring; `scripts/verify-cold.sh` precondition.

## F269 — The release zip, installed in place, was never measured · **DEFECT (measured)** · confidence 95% · ✅ FIXED 2026-10-11

The README's default install extracts the release zip (`git archive`: `agent/` present, no
`.git`) and runs the installer IN PLACE; SETUP_PROMPT then has the agent run the suite there.
F262 replaced the cold `git archive` leg with a clone when three tests failed for want of Git
metadata -- it fixed the setup by changing the POPULATION, so the failing population left the
evidence instead of the tests being fixed. F262's own text said so ("an exported release
bundle remains outside this evidence's scope"); nothing measured it afterwards.

MEASURED 2026-10-10 (FX-H2), at 5063aec: `git archive HEAD` extracted outside any repository
(`GIT_CEILING_DIRECTORIES` set; `git -C <extract> rev-parse` failed, the precondition), then
`install.sh --method separate` in place with the installer tests' sandbox (scratch HOME,
USERPROFILE, CODEX_HOME, CLAUDE_CONFIG_DIR, a test registry key, `--no-env`, every X4 root in
scratch, `X4_TOOLKIT` unset): install rc 0, no `--over-existing` needed, the test key was not
created. SETUP_PROMPT's `uv run pytest -q` then ran SERIALLY for about an hour and was stopped
at 37% with 3 failures (names not yet read: `-q` prints them only in the final summary). A
per-file re-run was stopped before it started: the machine fell below its resource floors (C:
141 GB free against 150, commit 50.6 of 52.9 GB), none of it this lane's load. So the failure
list, its attribution, and whether `installed_layout()` (an in-place install HAS `agent/`, so
it reads as a checkout and repo-only tests run instead of skipping) must change are UNMEASURED.

MEASURED 2026-10-11, the population re-run one test FILE per pytest session (4 at a time,
same sandbox, the extract under TEMP): 238 of 239 files completed -- 4,543 passed, 54 skipped,
29 failed, 8 errors. Every one of the 37 attributed, buckets summing to 37:
  *  3  Git metadata (the plan's three): `test_build_release`, `test_gen_codex_tree`,
        `test_opencode_config` -- `git check-ignore`/`check-attr` exit 128 with no repository.
  * 26  `installed_layout()` read the install as a CHECKOUT (agent/ present), so every
        "configured but not present yet" skip became a failure: `import_gate` re-raised a
        gate's SystemExit 2 (`test_qa_coverage` 9, `test_qa_sweep_newest_groundtruth` 3,
        `test_corpus_sweep_crash_detection` 8 errors), the freshness root twin (1), and the
        reference-dependent `test_dump_accepts_physical_mod_vpath` 1,
        `test_fast_script_pass_runs_by_default` 3, `test_script_files_disclose_unvalidated` 1.
  *  7  MY SANDBOX, not the product: `test_codex_wrapper` 6 -- the wrapper's `python3` is the
        WindowsApps alias, which fails (adapter exit 101) under a redirected USERPROFILE;
        MEASURED: the same test passes with the real profile and fails with the sandboxed one.
        `test_codex_conformance` 1 (#202, `~/.config/EgoSoft/X4` with HOME redirected) --
        INFERRED to be the same cause, unconfirmed.
  *  1  MY PLACEMENT: `test_x4conformance_engine` refuses a run dir under system TEMP, and the
        extract was under TEMP.
`test_install_over_existing.py` (361 tests) was killed at about 222 by the resource watchdog
(commit floor) with its log unwritten: UNMEASURED. The parallel failures were not re-run
serially (the job was killed first); none of the 37 is attributed to parallelism.

Fixed (FX-H2, I-1 a/b): `installed_layout()` -- a checkout is a `.git` AT the root whose HEAD
tracks agent/, anything else is installed; the three Git tests ask the SHIPPED
`.gitignore`/`.gitattributes` in a scratch repo (git-free) and their repository only when
`own_checkout(root)`. `scripts/x4lock.py` `_is_installed_toolkit` and `scripts/x4doctor.py`'s parity row keyed on agent/
too (maintainer's decision 2026-10-10: fix them the same way). One helper now answers for all
three, `scripts/toolkit_layout.py`. MEASURED 2026-10-11 on scratch trees, OLD 5063aec vs NEW:
an in-place install (zip extract, no repo visible) -- `x4lock lock` locked 1 file OLD, 131 NEW
(14 under `.claude/hooks`), and `.claude/hooks/protect-bash.sh` was read-only on disk only NEW
(the file mode read independently, then `unlock --all`); x4doctor `parity.claude` and
`parity.codex` OK -> N/A, its `x4lock` row UNKNOWN -> OK. A real checkout (a clone) at each
commit: `x4lock status` byte-identical, x4doctor 32 of 32 row statuses identical.
VERIFIED 2026-10-11 (MEASURED): a fresh `git archive` of 647b591, extracted OUTSIDE TEMP with no
repository visible, installed in place with the same installer sandbox, then the 13 previously
failing files plus `test_layout_repo_only`, `test_x4lock` and `test_x4doctor` run serially with
the user's REAL HOME (as SETUP_PROMPT runs them): 16 of 16 files exit 0, 0 failed. Remaining
skips name a zip's real absences (no repository, no tags). This also settles the #202
attribution: `test_codex_conformance` passes 22 of 22 with the real HOME, so it was the
sandboxed HOME (MEASURED, no longer inferred); `test_codex_wrapper` 46 of 46 with the real
profile; `test_x4conformance_engine` 65 of 65 outside TEMP. Still UNMEASURED in this population:
`test_install_over_existing.py` (361 tests), cut off by the resource watchdog in the first run.

Fixed so far: `scripts/verify-cold.sh --archive` restores the archive population as a second
cold leg, with the ceiling and a refusal so it cannot pass vacuously (twins: no ceiling ->
refusal rc 2; neither -> the stub sees a repository, rc 6); CI runs it on Linux. It is EXPECTED
to fail until the git-metadata tests are fixed, which is the point.

Also seen: `scripts/run-pytest-batches.py` cannot run in an extract (`source_fingerprint` calls
`git ls-files` with `check=True`); a maintainer-only tool, recorded, not fixed.

RE-DERIVED BY: `tests/test_verify_cold_checkout.py` (archive-leg tests); the measurement
scripts are scratch (not shipped).

## F268 — `run-gates.sh` expanded possibly-empty arrays bare under `set -u` · **DEFECT (read)** · confidence 90% · ✅ FIXED 2026-10-10

READ (reviewer M-3): `gate_args` is empty for every gate but `hook_false_positives`, and
`"${gate_args[@]}"` of an empty array under `set -u` aborts with "unbound variable" in bash
before 4.4 -- macOS ships 3.2. NOT REPRODUCED: every bash this toolkit's suite reaches
(Git Bash 5.2, Ubuntu CI) is >= 4.4, so this is a READ finding with no measured failure.
The new class rule found three such arrays (`gate_args`, `run`, `all_gates`); `all_gates`
is provably non-empty (an exit guards it) and `run` is empty only for a degenerate gate
set, but all three now use `${a[@]+"${a[@]}"}`. The rule: any array declared `name=()`
and expanded bare fails; a twin shows it can fail and spares the guarded and never-empty
forms. Denominator: 3 of 3 `name=()` arrays in the script.

RE-DERIVED BY: `tests/test_run_gates_logs.py`
`test_no_possibly_empty_array_is_expanded_bare_under_set_u` and its TWIN.

## F267 — Gate report directories checked only the EXPORTED roots · **DEFECT (measured)** · confidence 99% · ✅ FIXED 2026-10-10

MEASURED (reviewer M-2): with `X4_REFERENCE` named only in an `X4_CONFIG` file and not
exported, the tools resolved the reference root, and `gate_support.external_output()`
ALLOWED a report directory inside it: it read `X4_GAME`, `X4_REFERENCE`, `X4_PROFILE` from
the environment only. `run-gates.sh`'s log-dir check had already been moved to the tools'
resolver for this reason (R4-7/R6-10); these two runners had not.
`protected_roots()` now resolves game, reference and profile through `x4validate._paths`
(environment, then config) and refuses when a resolver raises; an unconfigured root is
None and protects nothing. `external_output()` REQUIRES the resolved roots, so no caller
can fall back to the environment alone; both callers (`run-release-gates.py`,
`run-pytest-batches.py`) pass them. Tests: config-only root refused, outside twin allowed;
a raising resolver refuses; omitting the roots is a TypeError. Each clause mutated
(config roots dropped, resolver error swallowed, roots optional) turned one test red.

RE-DERIVED BY: `tests/test_parallel_runners.py`
`test_a_root_named_only_in_the_config_file_is_protected`,
`test_a_resolver_that_cannot_run_refuses`, `test_external_output_requires_the_resolved_roots`.

## F266 — A pending or partial release-gate run exited 0 · **DEFECT (measured)** · confidence 99% · ✅ FIXED 2026-10-10

MEASURED (reviewer M-1), with the real `main()` over fake stages: a pytest stage recorded
"pending deployment parity" exited 0, `--through vht` (pytest never run) exited 0, and
`--resume` over a pending checkpoint reused the row and exited 0 again. HEADROOM-GATES.md
said the pending stage's "nonzero exit [was] retained"; the stage's rc was stored in the
checkpoint, but the process exit was 0, which is what a caller tests.
`verdict(state, names)` now computes the exit from the CHECKPOINT: 0 only when every stage
is recorded passed, 1 a stage failed, 3 pending or not run -- so a reused row is judged like
a fresh one and `--resume` cannot launder it. RED: 3 of the 5 new tests. Twins: a failed
stage stays 1; resuming until the remaining stage passes gives 0. Four mutants (failed
clause, `status == passed`, the `--through` break, the verdict's use) each turned a test red.

RE-DERIVED BY: `tests/test_parallel_runners.py`
`test_a_PENDING_stage_exits_3_and_resume_cannot_launder_it`,
`test_stopping_early_with_through_exits_3_until_the_rest_runs`,
`test_every_stage_passed_is_the_only_exit_0`, `test_verdict_reads_only_recorded_state`.

## F265 — icacls switches spelled `//switch`, and the `/inheritance*` prefix class · **DEFECT (measured)** · confidence 97% · ✅ FIXED 2026-10-10

MEASURED (reviewer L, and beyond it): Git Bash users double a switch's slash (`//reset`) so
MSYS does not rewrite it as a path; icacls receives `/reset`. `_icacls_parts` looked the
token up as typed, so `//reset` was a PATH operand and `icacls <reference> //reset //T //C`
was ALLOWED (also `//grant:r`, `//remove:d`, `//inheritance:r`, `//setowner`, `//restore` on
an ancestor, `//substitute`). Beyond the plan, MEASURED 2026-10-10 on a scratch file with
the real `icacls.exe`: it accepts ANY switch that begins with `/inheritance` --
`/inheritancezzz:r` removed 4 of 4 inherited ACEs, while `/inheritanc:r` and `/inh:r` were
rejected as invalid parameters, and the other lift switches (`/resetx`, `/grantx`,
`/removex`, `/setownerx`, `/restorex`, `/substitutex`, `/res`, `/gra`, `/rem`) were all
rejected too. So `/inheritancelevel` was one spelling of an open class, not dead and not
complete: `/inheritancezzz:r` on a reference child was allowed. A reviewer mutant that
dropped `/inheritancelevel` survived only because no test named it.
Fix (`_icacls_switch`): a token that is ENTIRELY `//<letters>` is the switch (a UNC
`//server/share` has a second slash and stays a path), and every `/inheritance*` spelling
is `/inheritance`; the separate `/inheritancelevel` entry is subsumed. RED: 12 unit failures
across 3 tests; on the OLD deployed hooks `test-protect-bash.sh` failed exactly the two new
must-fire probes (`//reset //T //C`, `/inheritancezzz:r`) while both twins stayed allow.
GREEN: `test_hook_facts.py` 748 OK; protect-bash 0 failures. Twins: UNC, a rejected prefix,
doubled non-lift switches (`//deny`, `//save`), `/findsid`'s value. Five named mutants in
`verify-hook-tests.py` (E7 re-anchored). One mutant is EQUIVALENT and recorded, not tested:
`startswith("//")` for the `fullmatch`, because the stripped token must still equal a switch.
Residual (UNMEASURED): whether other icacls switches accept prefixes on other Windows builds;
the measurement is one build (this machine's).

RE-DERIVED BY: `.claude/hooks/test_hook_facts.py` (source `agent/guards/claude-hooks/test_hook_facts.py`) `TestH2IcaclsSwitchSpellings`;
`test-protect-bash.sh` probes `icacls-dblslash-reset`, `icacls-inherit-prefix` and twins.

## F264 — Elevated Git Bash bypasses the ACL probe's assumption · **DEFECT (measured)** · confidence 99% · ✅ FIXED 2026-10-08

MEASURED: hosted run 37829869175 at 204842f passed 31 of the previous 34 Windows
failed/error cases; three setup-error cases reached assertions and failed. It had 4,945 passed, 54 skipped, three failures and zero
setup errors. Previously unreachable `test_the_deny_STOPS` cases `bash_redirect`,
`rm_f`, and `rm_rf_tree` reached their assertions and changed protected scratch
trees. No new skip or skip-ceiling waiver was introduced.

The product already names Windows administrators as a limitation, but its opening
claim of protection against EVERY process was too broad. A separate scratch-only
hosted diagnostic, run 37844985751 at 7639f6c, compared the same three commands
with and without SeBackupPrivilege/SeRestorePrivilege in disposable children:
12 cases, six of six unprotected controls changed, three of three protected
privileged probes changed, three of three protected probes without both rights
were blocked. All 12 children started with both rights available, including after
earlier children removed them: the launcher retained privilege availability.
The rights were initially disabled, so merely disabling is not the tested remedy.
This measurement establishes the pair's effect, not which single right suffices.

Only the three Git Bash test probes change their process context. A disposable
Python child removes both available privileges with SE_PRIVILEGE_REMOVED, re-reads
their absence, then runs the original command. This is NOT full unelevation;
all other token attributes remain unchanged. Every call is confined beneath
X4_REFGUARD_SANDBOX under the system temporary directory. The child must report
that it ran; the protected and unprotected disk assertions remain unchanged.
The parent token and production ACL mechanism are not modified. The product
docstring now states the measured administrator/Git Bash limitation explicitly.

MEASURED: all 38 behavior/control tests passed locally (zero skips), then all 38
passed with zero skips on elevated hosted Windows, run 37845999821 at e4dba1f.
The fixture source tested there is byte-identical to this checkout's helper and
behavior module. Those diagnostic workflow/script files remain on a separate
throwaway branch; they are not part of this repair.
MEASURED final full CI: [run 37846813450](https://github.com/WingedGuardian/x4-claude-toolkit/actions/runs/37846813450)
executed a38e3cf and all three jobs succeeded. Windows: 4,948 passed, 54 skipped;
Ubuntu main and fresh cold clone: each 4,776 passed, 204 skipped. All suites had
zero failures/setup errors; the identifier job also passed. This execution SHA
precedes the documentation-only update recording these results.

Primary API semantics (removed privileges cannot be re-enabled):
https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-adjusttokenprivileges
RE-DERIVED BY: `tests/test_x4refguard_behaviour.py` `test_the_deny_STOPS` and
`test_CONTROL_the_primitive_changes_an_unprotected_tree`, using
`tests/refguard_bash_probe.py`; the paired disk controls reject a child that did
not actually execute its probe.

## F263 — Ownership-fixture ACL and hint preconditions · **DEFECT (measured)** · confidence 99% · ✅ FIXED 2026-10-08

MEASURED: Windows CI run 37802022931 at 5567db7 had 14 failures and 20
setup errors, with 4,910 passed and 54 skipped. Every failed/error case is
attributed: 31 reported partial protection after ownership repair; three
doctor-hint tests stopped at foreign ownership on the elevated runner.
The 54 skips remained within the unchanged 56 ceiling.

A native regression forced the ownership-repair branch even when the local
user already owned the scratch root. It reproduced the same partial result.
WITHDRAWN: removing /T from the grant alone solves this. The root-only
variant also failed. Numeric before/after ACL snapshots showed explicit child
allows appearing during the ownership repair, so the conservative product
checker correctly refused to certify the inherited deny.

Only the fresh test fixtures change: after setting the owner, reset inherited
scratch ACLs, then grant inheritable access on the root only. Every operation
uses x4refguard's sandboxed mutation choke point. Doctor-hint fixtures satisfy
ownership on their own targets, then still require the unconfirmed hint to
refuse before applying protection. No real reference ACL or product guard
source was changed. Microsoft documents /reset as restoring inherited ACLs:
https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/icacls

MEASURED: 11 focused tests passed without skips. The audited batch runner
then collected 409 cases across reference-guard, behaviour, doctor, unpack
and toolkit-binding modules: 406 passed, three real POSIX-chmod exclusions,
zero failures or session errors. Changed/unreviewed modules remained exclusive;
four was the requested worker maximum, not four simultaneous test processes.
Follow-up MEASURED: run 37829869175 passed 31 of the 34 preceding failed/error
cases; three former setup errors reached assertions and exposed the separate F264
administrator-context assumption. Final run 37846813450 at a38e3cf passed all
4,948 Windows cases, with 54 accounted skips and zero failures/setup errors.

RE-DERIVED BY: `tests/test_x4refguard.py`
`test_OWNERSHIP_repair_preserves_the_inherited_deny`,
`test_OWNERSHIP_fixture_takes_the_root_when_it_can`, and
`tests/test_x4doctor.py` doctor-hint tests.

## F262 — Cold-checkout metadata and sandbox mismatch · **DEFECT (measured)** · confidence 99% · ✅ FIXED 2026-10-08

MEASURED: Ubuntu CI run 37802022931 at 5567db7 passed its main suite
(4,773 passed, 203 skipped, zero failures). Its separate cold suite failed:
four failures, 17 setup errors, 4,716 passed and 232 skipped. All 17 setup
errors came from the conformance harness refusing a sandbox under /tmp.
Three failures queried Git metadata in an archive with no .git; the fourth
asserted that the neutral conformance directory must not be under system temp.

Cold verification now clones committed objects without shared hardlinks into
a unique child of the toolkit's existing .test-sandbox. Git metadata and tags
are available, while untracked private config and virtualenvs are excluded.
All X4 variables are still cleared. The None|None|None path-resolution
precondition and the full suite/CLI refusal checks remain mandatory.

MEASURED locally: 51 focused tests passed, zero skipped. The regression runs
real Git and Bash with a uv seam: selected older tag, ignore/export queries,
private-config exclusion, neutral location, warm refusal before pytest,
and unknown-ref refusal. The uncorrected script failed that checkout probe.
MEASURED hosted proof: final run 37846813450 at a38e3cf passed the full Linux
cold suite: 4,776 passed, 204 skipped, zero failures/setup errors. Compared with
the old cold run, 36 Git-metadata checks resumed, 21 failed/error cases passed,
and three new cold tests passed: 60 additional passes. Seven newly discovered
PowerShell parser cases and one new Windows-native case add eight accounted
skips, giving 232 - 36 + 8 = 204. Comparison is per file/reason group, not
parameter node ID. This proves a fresh Git checkout; an exported release bundle
remains outside this evidence's scope.

RECLASSIFIED 2026-10-10 (FX-H2, reviewer M): only the 17 setup errors (the /tmp sandbox) and
the neutral-location assertion were checkout SETUP. The three Git-metadata failures were
tests that assume a repository, and the release zip a user installs has none: switching the
leg to a clone made them pass by removing that population from the evidence. It is restored
as `verify-cold.sh --archive` and tracked as F269 (OPEN).

RE-DERIVED BY: `tests/test_verify_cold_checkout.py` and
`tests/test_cli_enumerations_agree.py`.

## F261 — Stale Ubuntu skip ceiling · **DEFECT (measured)** · confidence 99% · ✅ FIXED 2026-10-08

MEASURED: CI run 37746049392 at fbf5641 ran 4,773 tests successfully and
skipped 203, with zero failed tests. All 203 exclusions are accounted for:
156 require Windows semantics or a case-insensitive filesystem; 47 require
configured X4 data, registry, or deployed copies. The job failed because its
skip ceiling was 145. Every grouped skip was compared by file and reason against run
37172347642, which had 139 skips. All 64 added exclusions are Windows-specific:
bash resolver 7, config spellings 15, UNC 1, installers 24, toolkit binding 1,
unpack path spellings 4, doctor 9, and NTFS guard 3. Changes in the other
groups only clarify reasons; their counts are unchanged. The Ubuntu limit is
now its measured floor plus the existing three-test margin, 206. Windows
remains 56 pending its own measurement. Residual: runner capabilities and
test populations can change; the next per-job result remains authoritative.

**NO RE-DERIVATION: external CI observations require the completed per-job
logs and their grouped skip reasons; an in-repo test cannot establish a hosted
runner's measured skip population.** Private item receipts retain both runs.

## F260 — Short-name comparison and provider composition gap · **DEFECT (measured)** · confidence 99% · ✅ FIXED 2026-10-08

MEASURED: Windows CI run 37746049392 passed parse-pass tests but failed four audit
tests. All four reproduced locally with a native short-name temporary base.
Configured roots were expanded by `main`, while provider-qualified targets,
unresolved write stems and pipeline targets could retain their short alias.
The comparison then missed the same protected directory. Six controlled path
forms failed a new portable API-oracle regression. The shared normalizer now
expands an existing native prefix for comparisons and peels provider/device
syntax through the same lexical helper before querying that prefix. Unknown
suffixes remain unverified; no ACL or operating-system protection is modified.
MEASURED after repair: all 741 source unit tests, all 73 native audit tests
under a verified short-name TEMP ancestor, and 232 focused generation/register/
gate tests passed, zero skipped. Across 266 verification trials, all 202
deliberate defects were caught and all 32 predicates had both coverage directions.
The fresh full four-worker Bash replay matched all 24,593 accepted baseline
verdicts: zero changed, added or removed; zero stderr noise; runtime/config
fingerprints stable and matching the deployed copy. The baseline was retained.
All 24,778 frozen hygiene classifications were unchanged; only its matcher
fingerprint was rebound after proof, retaining the original baseline period.

The preceding eight-worker artifact had four allow-to-ask changes, each a
PowerShell analysis non-answer and none carrying a short-name path. Two serial
repeats of every changed call matched the baseline, followed by the fresh full
four-worker replay above. Those artifacts remain separate. Contention during
that run is a plausible cause, not a measured diagnosis of the four non-answers;
no parser timeout was increased and no result was spliced into an artifact.
Residual: the native API can fail to resolve a prefix; unresolved suffixes do
not become evidence that a path exists. The tests establish the listed forms,
not every filesystem or junction configuration.

RE-DERIVED BY: `agent/guards/claude-hooks/test_hook_facts.py`
`test_short_alias_comparison_peels_prefixes_and_keeps_unknown_tails` and
`agent/guards/claude-hooks/test_audit0924_hooks.py`.

## F259 — Native short-name test assumed TEMP was already long · **DEFECT (measured)** · confidence 99% · ✅ FIXED 2026-10-08

MEASURED: Windows CI run 37744168461 passed the previously failing icacls suite,
then failed `test_an_8dot3_short_name_is_its_long_name`. The temporary ancestor
already used an 8.3 alias. The guard correctly expanded that ancestor, but the
fixture's expected path retained the alias. A native local reproduction with a
short-name temporary base failed at the same assertion before repair and passed
afterward. Disabling `H.long_name` expansion still made the repaired test fail.
The fixture obtains its long directory independently through GetLongPathNameW,
checks native API success, and then obtains its short form for the guard probe.
Production guard bytes and the volume-disabled skip condition are unchanged.

MEASURED: the final fixture deliberately uses the temporary parent's native short
form, so an alias-capable Windows machine exercises this ancestor case locally.
All 740 source tests and 142 generation/register tests passed, zero skipped.
Sealed native controls returned old fixture 1, repaired fixture 0, and disabled
guard expansion 1; source/test bytes remained stable throughout that check.

RE-DERIVED BY: `agent/guards/claude-hooks/test_hook_facts.py`
`test_an_8dot3_short_name_is_its_long_name`.

## F258 — An absent generation marker asserted a save load · **DEFECT (measured)** · confidence 99% · ✅ FIXED 2026-10-08

MEASURED: triage called a user-confirmed menu-only startup capture a "save load".
READ: `_debugcli.triage` selected that label solely when `_is_new_game` was false.
Absence of the galaxy-generation marker cannot distinguish menu startup from a
save load. A new regression failed before the label repair. Unmarked sessions
now render UNKNOWN (menu or save load); confirmed new games retain their label.
The regression preserves all four error rows in its fixture, and the existing
new-game test remains the positive control. Parsing, buckets and return codes
are unchanged. Independently confirmed session provenance remains separate from
what the tool can establish from the log alone.

MEASURED: 205 debug/parser/generation/register tests passed, zero skipped. On the
same pinned startup capture, the complete triage output differed only in the
session-label line; all 330 error rows and every bucket remained identical.

RE-DERIVED BY: `tests/test_debugcli.py`
`test_triage_does_not_infer_a_save_load_from_an_absent_generation_marker` and
`test_triage_states_the_logs_age_and_whether_it_is_a_new_game`.

## F257 — Absolute icacls operands mistaken for switches · **DEFECT (measured)** · confidence 99% · ✅ FIXED 2026-10-08

MEASURED: throwaway CI run 37732310081 at 4cc0ccf failed
`icacls-reset-reference` on both Windows and Ubuntu: wanted ask, got allow.
The clean checkout's reference path begins with `/`; `_icacls_paths` discarded
EVERY slash-leading token. Existing unit fixtures used drive-letter paths and
therefore missed this. New reproduction produced 13 failures across absolute
POSIX/MSYS operands and switch-value handling. Full source unit suite after
repair: 739 tests, OK; hook E2E: 305 passed, 0 failed, 0 skipped. Comparing
old/new parser facts over all 24,593 distinct historical inputs changed zero
facts, including the reference-lift predicate. This is a parser comparison,
not a Bash verdict replay; full replay results are recorded below. Three mutation
anchors moved with the repair; two added mutants restore the operand/value bugs.

FOLLOW-UP MEASURED: the deployed ccefcf1 hook's full Bash replay matched all
24,593 baseline inputs with zero verdict changes and zero stderr noise. A native
read-only Git Bash query `icacls.exe /c` processed one C-drive target successfully;
therefore the first NAME operand must take precedence over the `/C` switch.
A native options-first read-only query returned rc 87. The first-operand repair
passed all 740 source unit tests, including a read-only `/c` control and an
outside-reference path with a later `/C` switch. Its follow-up checks at 23c76c1
caught all 200 mutants with zero coverage gaps across 32 predicates. The fresh
full eight-worker Bash replay followed by saved-result comparison matched all
24,593 accepted baseline verdicts: zero changed, added or removed; stderr noise
zero. The artifact fingerprints matched the deployed hook dependencies, which
remained stable during execution. The accepted historical baseline was retained.

Targets and modifying actions now share `_icacls_parts`, which recognizes named
switches and colon modifiers while excluding their values from both sets. A
`/restore/...` pathname cannot impersonate the action; a `/save` value cannot
impersonate a `/restore` switch. Ancestor recursion, Windows drive paths, reads,
and applying a deny retain their existing semantics. Switch names/value arity
are checked against [Microsoft's icacls syntax](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/icacls);
the existing `/inheritance` spelling remains supported alongside `/inheritancelevel`.
This does not execute icacls or change a filesystem ACL.

RE-DERIVED BY: `agent/guards/claude-hooks/test_hook_facts.py`
`test_icacls_absolute_slash_operands_are_paths_not_switches`,
`test_icacls_switch_values_are_not_target_paths`,
`test_icacls_drive_operand_is_not_the_continue_switch`.

## F256 — Hygiene regex matched data and the wrong status · **DEFECT (measured)** · confidence 99% · ✅ FIXED 2026-10-08

MEASURED: four safe probes were counted as hygiene failures: a correctly captured
test status after an earlier, unrelated pipeline; Python source containing a pytest
example; a git commit message mentioning an earlier pytest run; and a data heredoc
containing test/commit text. These are not evidence of unsafe shell execution.
The shared guard grammar now strips comments/data heredocs and quoted prose for
the bare-python and test/commit patterns, and follows pipeline status by command
boundary. Three real failure controls still count. Five other patterns retain
their original raw-regex scope. Opaque programs/unresolved executable names remain
outside these lower-bound checks; this is not a full shell execution model.

The baseline now carries a matcher fingerprint. A missing/different fingerprint
REFUSES comparison; source changes during the census also refuse. Do not reset the
historical period merely to remove a red result: re-measure that SAME population
with the corrected matcher or explicitly accept a new period.

MEASURED: the frozen 24,778-call census has recorded cwd for every call. The bare
pytest check now follows the shared guard's project-code scope using that cwd;
dependency-free hook tests outside that scope are excluded. It retains its
existing pytest-only population, rather than adding script/module invocations.
All 630 changed calls were classified. The reconstructed historical prefix of
10,767 calls reproduces all eight legacy baseline rates; the original baseline
did not store input identities, so this is reconstruction evidence, not proof of
the original identity set. Corrected prefix/new-window counts are respectively
pipeline 124/60, bare project pytest 0/5, and test/commit 61/69 over 10,767/14,011
calls. The five other patterns are unchanged. Full receipts and the original
baseline remain private outside git. These are lower-bound shape findings;
opaque execution and the shared parser's PIPESTATUS suppression remain limits.
RE-DERIVED BY: `tests/test_instrument_hygiene.py`
`test_data_and_intervening_commands_are_not_hygiene_failures`,
`test_real_hygiene_failures_still_match`,
`test_a_baseline_from_another_matcher_is_not_compared`, and
`test_a_matcher_changed_during_the_scan_voids_the_reading`.
