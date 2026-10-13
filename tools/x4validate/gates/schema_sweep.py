r"""Gate: the effective-schema check must keep agreeing with its own measurement.

W3.1 was measured across the whole installed modlist BEFORE it was written, and
the implementation was then required to reproduce that number. This gate freezes
the result so a later "optimisation" cannot quietly change what the check reports.

The bars, all measured 2026-07-29 over the 102 installed non-DLC mods:

  * 127 (mod,file) pairs validated
  * 105 findings introduced == 45 gating + 57 advisory + 3 suppressed
  * 10 of 102 mods flagged
  * the KNOWN-REAL defects all still reported (there were four when this was
    written; the roster is KNOWN_REAL below and the verdict line COUNTS it, because
    both this sentence and that line said "four" while six were pinned)

Why the totals are pinned and not just the four defects: a check that reports the
right four while inventing 200 others is not usable, and a check that reports the
right four because it reports everything is not a check. The composition is the
claim, so the composition is what is frozen.

Why they are pinned SEPARATELY: totals can be met by accident after a tuning
change that silences a real finding and adds a spurious one. These are the ones
with independent evidence behind them, so silencing any of them is a regression
no matter what the totals say.

PER-MOD BASELINE (AUDIT-2026-09-24 GT-5, design default, added 2026-09-25). The
totals above can hold while findings MOVE between mods -- one mod gains three
gating findings as another loses three, and every pinned number reads UNCHANGED.
That happened for real on 2026-09-06 (`mods flagged` and `pairs` unchanged while one
mod left and another arrived), and only a hand diff of the per-mod table caught it.
So every mod's (gating, advisory, suppressed, pairs, NOT checked) row is recorded
in a LOCAL baseline and compared PER ITEM; a mod present in only one of the two
runs is named too. The file is machine-local and gitignored -- it describes this
install's modlist, like `.obtainability-baseline.json`.

Run: `uv run python gates/schema_sweep.py [--record]`
Exit: 0 totals, KNOWN_REAL and every per-mod row match (with --record: they match and the
        per-mod baseline was written; --record never writes over a failing run)
      1 any of them moved (a finding outranks everything below)
      2 no per-mod baseline, or one this version cannot read -- the per-item
        comparison did not happen, so a clean total is NOT a pass. `--record`
        writes it (after you have attributed the current per-mod table).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import _env

from x4validate import _check, _merge

#
# RE-MEASURED 2026-08-01 after the schema-resolution fix (audit finding F1).
# Quoted rather than quietly re-baselined, because this gate's whole job is to
# make a moved number visible:
#
#            pairs  gating  advisory  suppressed  NOT checked  mods flagged
#   before     127      45        57           3          31*            10
#   after      157      45        91           3            1            39
#   delta      +30       0       +34           0          -30           +29
#
#   * the 31 were always happening; they were invisible until EXPECT_SKIPPED
#     existed, which is exactly why it now exists.
#
# What moved and why: 30 mods ship a root `ui.xml` whose declared schema
# resolves nowhere from where the file actually sits, so they were skipped with
# the false reason "not bundled in .../libraries" — the schema IS bundled, at
# reference/ui/core/coreaddon.xsd. Those 30 documents are now validated.
#
# GATING DID NOT MOVE (45 -> 45). The +34 advisories are one benign class:
# coreaddon.xsd constrains addon/@name to the pattern 'ego_.+', Egosoft's own
# naming convention, which every third-party UI mod violates harmlessly. That is
# the textbook "XSD stricter than the engine" case _schema_gates exists to
# downgrade, and it is why more coverage cost zero new errors.
#
# RE-MEASURED 2026-08-02 for the F7+F14 severity split — the movement was
# measured across all 91 advisories BEFORE the code was written, and the
# implementation was then required to reproduce it:
#
#            pairs  gating  advisory  suppressed  NOT checked  mods flagged
#   before     157      45        91           3            1            39
#   after      157      60        76           3            1            39
#   delta        0     +15       -15           0            0             0
#
# The 15 that moved are the two classes whose "XSD lags the engine" excuse
# cannot apply, each verified individually against the packed-inclusive corpus:
#   * 7  enum-undefined (F14): cpsdo_faction race='central' — defined by the
#        XSD floor nowhere AND by the effective 102-mod tree nowhere.
#   * 8  dead-attr (F7): (element, attribute) pairs vanilla never uses —
#        category/@matchextension x3 (vanilla's 140 uses are ALL on <location>;
#        a real attribute on the wrong element) and element/@forkmaterial x5
#        (invented by VRO, 4 corpus hits, all VRO itself).
# The 76 that stayed advisory: 36 pattern facets, 34 non-lookup enums (mods
# cannot extend those by defining something, but the engine may still accept
# more than the XSD lists — e.g. ship_variation_expansion's list-in-enum
# relation='[friend, ally]' x27, recorded as a possible upstream defect the
# captured log cannot settle), 2 key-constraint cascades, 4 cascade noise.
#
# RE-MEASURED 2026-08-02 (same day, later): the MODLIST changed, not the check —
# rer_boronphaser became discoverable (its content.xml had been one folder too
# deep since extraction; F19-era install hygiene moved it up). It contributes
# 2 pairs, 1 advisory, 0 gating:
#
#            pairs  gating  advisory  suppressed  NOT checked  mods flagged
#   before     157      60        76           3            1            39
#   after      159      60        77           3            1            40
#   delta       +2       0        +1           0            0            +1
#
# RE-MEASURED 2026-08-08: the MODLIST changed, not the check — and unlike the
# previous re-baselines this one is attributed per-mod, so the numbers are
# explained rather than merely observed.
#
# The check is provably unchanged: this sweep produced these exact numbers BEFORE
# and AFTER the root-<replace> merge fix landed the same day (72aea46). The fix
# alters values, not schema shapes.
#
# Population change, established from extension folder timestamps (not memory):
#   + 12 mods added 2026-08-07/08. Only three contribute anything:
#       ter_radar_turret       1 gating   0 advisory
#       lc4hunter_argon_titan  0 gating   1 advisory
#       arck_job_registry      0 gating   1 advisory
#     the other nine are clean.
#   - xspvro removed 2026-08-08, taking its KNOWN_REAL 2 gating / 9 advisory with
#     it (entry deleted below).
#
# The arithmetic closes exactly on three of the four constants:
#   gating       60 - 2 + 1 = 59   observed 59
#   advisory     77 - 9 + 2 = 70   observed 70
#   mods flagged 40 - 1 + 3 = 42   observed 42
#   pairs       159 -> 168 (+9)    coverage growth from the new mods' own files
#
#            pairs  gating  advisory  suppressed  NOT checked  mods flagged
#   before     159      60        77           3            1            40
#   after      168      59        70           3            1            42
#   delta       +9      -1        -7           0            0            +2
# RE-MEASURED 2026-08-23 (toolkit 2.4.0). The MODLIST changed, not the check --
# and unlike a bare re-baseline, what is and is NOT attributed is written down.
#
# The check is provably unchanged. schema_sweep calls exactly one x4validate
# function, `_check.check_effective_schema`, whose call graph is
# `_merge.build_effective` + `_xsd.*` + `iter_mod_xml_roots` + local helpers. The
# intersection with every function 2.4.0 touched is EMPTY. Its population comes
# from `ext.iterdir()`, never `_registry.mods()`, so the F37 mod-scope change
# cannot reach it either, and `_xsd.py`'s only 2.4.0 change is comment-only.
#
#   pairs  168 -> 169  (+1)  ATTRIBUTED: npc_economy_tweaks, installed 2026-08-13,
#                            i.e. AFTER the 08-08 baseline. One checkable pair,
#                            ZERO findings -- confirmed absent from the flagged list.
#   gating       59 -> 59    unchanged. The severity that actually gates did not move.
#   suppressed    3 ->  3    unchanged.
#   NOT checked   1 ->  1    unchanged.
#   advisory 70 -> 69  (-1)  UNATTRIBUTED.
#   mods     42 -> 41  (-1)  UNATTRIBUTED.
#
# THE HONEST PART: one mod that previously produced exactly one advisory no longer
# does, and WHICH mod cannot be recovered -- the 08-08 re-baseline recorded totals
# and three named contributors, not a full per-mod list, so there is nothing to
# diff against. Three folders were modified after that baseline (one public
# revert mod and two personal overlays) and none is flagged now, which is
# SUGGESTIVE and is not evidence. Recorded as open rather than explained away.
#
# Why re-baseline at all rather than leave the gate red: a permanently-failing
# gate is not a preserved question, it is noise -- and noise trains you to skim
# past the run it IS right about. The delta is preserved here in prose instead.
#
# FOR THE NEXT RE-BASELINE: dump the PER-MOD table alongside the totals, so the
# next drift is attributable by diffing rather than by memory. That omission is
# the entire reason one of these numbers is unattributable today.
# ---------------------------------------------------------------------------
# RE-BASELINE 2026-08-25 — FULLY ATTRIBUTED, per item, and the per-mod table is
# recorded this time (the previous block asked its successor to do exactly that,
# because one number in it could not be attributed for want of one).
#
# CAUSE: the modlist grew, the check did not. Three mods were deployed 2026-08-24
# (the three most recently modified folders under extensions\), taking the active
# set 114 -> 117. Measured by running the gate's OWN check on just those three:
#
#     personal overlay A            pairs=0  gating=0  advisory=0
#     personal overlay B            pairs=2  gating=0  advisory=0
#     Station_ink                   pairs=6  gating=1  advisory=0
#                                   -------  --------
#                                   pairs=8  gating=1
#
#   pairs  169 -> 177  (+8)  ATTRIBUTED in full: 6 + 2 + 0 from the three above.
#   gating  59 ->  60  (+1)  ATTRIBUTED: Station_ink, libraries/god.xml —
#                            "Element 'pilot': This element is not expected"
#                            introduced by that mod into the merged god.xml.
#                            An UPSTREAM finding about the mod, not a tool change.
#   mods    41 ->  42  (+1)  ATTRIBUTED: Station_ink (the only newly flagged mod).
#   advisory     69 ->  69   unchanged.
#   suppressed    3 ->   3   unchanged.
#   NOT checked   1 ->   1   unchanged.
#
# NOTHING IS UNATTRIBUTED IN THIS RE-BASELINE. A first attempt at attribution was
# WRONG and is worth recording: counting md/aiscript files in the three new mods
# gave 2, not 8, leaving 6 unexplained. The error was the instrument — "pairs"
# is whatever `check_effective_schema` reports in its own note, not an
# md/aiscript file count. Re-measured with the gate's own code, it reconciled
# exactly. An unexplained remainder is a lead; this one led to my own checker.
#
# AMENDED 2026-08-25 (same day): Station_ink's single gating finding was FIXED
# at source, so it drops out of both counts. The mod added a `pilot` block to
# libraries/god.xml using ships.xml's content model; libraries.xsd (the schema
# god.xml declares) defines no such element and vanilla god.xml contains none,
# and the engine logged no complaint -- it was silently ignored, so the block
# only ever tripped validation. Removed; the inner <station> now reads
# <select> then <loadout>, matching vanilla's shipyard_argon_01 exactly.
#
#   gating  60 -> 59  (-1)  ATTRIBUTED: Station_ink libraries/god.xml, fixed.
#   mods    42 -> 41  (-1)  ATTRIBUTED: Station_ink no longer flagged.
#   pairs        177        unchanged -- the file is still validated, it now passes.
#
# Verified per item BEFORE re-baselining: the gate's own check run against
# Station_ink alone reports pairs=6 gating=0 advisory=0 (was gating=1).
#
# PER-MOD TABLE AS OF 2026-08-25 (41 rows; sums verified = 59 gating, 69
# advisory). ⚠ THE TABLE IS THE 2026-08-25 STATE AND THE CONSTANTS ARE NOT:
# EXPECT_ERR is now 77 and EXPECT_INFO 71, and the vro row below still reads 5
# gating where KNOWN_REAL['vro'] is 20. Diff against it for SHAPE -- which mod
# moved -- not for totals.
#
# The header said '42 flagged; sums verified = 60 gating, 69 advisory' until
# 2026-09-06. Parsed from the right (this file's own awk NF==4 warning), the
# rows are 41 / 59 / 69, so it disagreed with the table beneath it. The
# 2026-09-06 note below fixed the OTHER copy of the same sentence and missed
# this one; the release reviewer found the survivor.
#     arck_job_registry                       0 gating   1 advisory
#     battle_repair_support                   0 gating   1 advisory
#     cpsdo_faction                          14 gating   1 advisory
#     cpsdo_zb_modpack                        0 gating   1 advisory
#     deploy_all_defense                      0 gating   1 advisory
#     ebi_timelines_faction_use_ship          3 gating   0 advisory
#     escape_pod                              1 gating   0 advisory
#     gs_debug_report                         0 gating   1 advisory
#     gs_lib                                  0 gating   1 advisory
#     gs_qol_fleet_transfer_crew              0 gating   1 advisory
#     gs_qol_show_on_map                      0 gating   1 advisory
#     higher_dimensional_space                1 gating   0 advisory
#     kuertee_additional_agent_actions        0 gating   1 advisory
#     kuertee_alternatives_to_death           0 gating   1 advisory
#     kuertee_emergent_missions               0 gating   1 advisory
#     kuertee_high_security_rooms_are_locked  0 gating   1 advisory
#     kuertee_loot_mining                     0 gating   1 advisory
#     kuertee_military_exercises              0 gating   1 advisory
#     kuertee_npc_reactions                   0 gating   1 advisory
#     kuertee_ship_scanner                    0 gating   1 advisory
#     kuertee_station_scanner                 0 gating   1 advisory
#     kuertee_surface_element_targeting       0 gating   1 advisory
#     kuertee_teleport_from_transporter_room  0 gating   1 advisory
#     kuertee_ui_accept_mission_for_later_button  0 gating   1 advisory
#     kuertee_ui_active_mission_button        0 gating   1 advisory
#     kuertee_ui_extensions                   0 gating   3 advisory
#     kuertee_ui_grouped_save_files           0 gating   2 advisory
#     kuertee_waypoint_fields_for_deployments  0 gating   1 advisory
#     lc4hunter_argon_titan                   0 gating   1 advisory
#     mlog_deadair_eco_no_da_wares           30 gating   0 advisory
#     mycu_equipment_tooltips                 0 gating   1 advisory
#     mycu_verbose_transaction_log            0 gating   1 advisory
#     rer_boronphaser                         0 gating   1 advisory
#     shadow_tracker                          0 gating   1 advisory
#     ship_testfield_gamestart                1 gating   1 advisory
#     ship_variation_expansion                3 gating  27 advisory
#     sn_mod_support_apis                     0 gating   2 advisory
#     stars                                   0 gating   2 advisory
#     station_production_overview             0 gating   1 advisory
#     ter_radar_turret                        1 gating   0 advisory
#     vro                                     5 gating   3 advisory
# RE-MEASURED 2026-08-25. The MODLIST changed, not the check.
#
# Three mods were deployed this session and one FILE was deleted:
#   + Synthetium_Music                (music mod, first deployment)
#   + personal overlay C              (restores vanilla music_bigfight_5)
#   + personal overlay D              (repairs a vanilla aiscript bug)
#   - amphitrite_vro's truncated ship_l_arethusa_raider_macro.xml (unparseable;
#     an identical repaired copy already ships in personal overlay A)
#
#   pairs   177 -> 178  (+1)  ATTRIBUTED IN FULL, per-mod:
#                               Synthetium_Music               pairs=1 gating=0 advisory=0
#                               personal overlay C             pairs=0 -- both its files
#                                 declare no schema
#                               personal overlay D             pairs=0 -- aiscripts/ is not
#                                 schema-eligible, and content.xml declares no schema
#   gating       59 -> 59     unchanged
#   advisory     69 -> 69     unchanged
#   suppressed    3 ->  3     unchanged
#   NOT checked   1 ->  1     unchanged
#   mods flagged 41 -> 41     unchanged -- none of the three is flagged
#
# NOTHING IS UNATTRIBUTED THIS TIME. The 08-23 re-baseline left one advisory delta
# permanently unexplainable because it recorded totals and three named contributors
# rather than a full per-mod list, and asked its successor to fix that. Done below.
#
# NOTE FOR WHOEVER READS THE PER-MOD NOTE NEXT: `_check.check_effective_schema`
# emits its "N declaring no schema / brand-new" detail ONLY when `checked == 0`
# (`_check.py`, `if not checked and (new_files or no_schema)`). So a mod that
# validated ANY file reports no reason for the files it did NOT validate --
# Synthetium_Music shows "1 validated" and stays silent about its other two. That
# is why the attribution above had to be derived per mod rather than read off the
# notes, and it is a mild instance of the register's own rule: a step that narrows
# the data should announce it even when it also succeeded at something.
#
# ⚠ Two folder names contain SPACES ('xenon e class ship', 'Pirates and Buccaneers
# stop shooting Satellites'). A field-count parse of this table (awk NF==4) silently
# drops both and undercounts pairs by 6. Parse from the RIGHT: the last three
# columns are numbers, everything before them is the name.
#
# PER-MOD TABLE — 41 FLAGGED mods, 2026-08-25. NOT HERE: this file is MIRRORED to the public
# repo, and the table names every installed mod, i.e. one person's entire modlist. It is
# also not reproducible by anyone else — it describes one install — so publishing it costs
# privacy and buys a public reader nothing.
#
# It lives in `AUDIT-2026-08.md` (beside this package; overlays appear only by letter), § "Per-mod schema baseline". Diff against THAT
# next time instead of reasoning from memory. Its columns sum to the constants below, which
# is what makes it an audit rather than a note: pairs 178, gating 59, advisory 69
# AS OF 2026-08-25. Those two figures are now 77 and 71 (EXPECT_ERR/EXPECT_INFO);
# the 2026-09-06 re-baseline moved them and this sentence was not re-pinned.
#
# ⚠ CORRECTED 2026-09-06: this paragraph read "74 mods ... 42 flagged; sums verified =
# 60 gating, 69 advisory" while the table above it has 41 rows summing to 59/69 — the
# Station_ink amendment removed that row and updated the constants, but not the sentence
# introducing them. A summary line that no longer sums is the defect class this gate
# exists to catch, sitting inside the gate. Found because an extraction REFUSED to hand
# back a table that could not reproduce the total it claimed.
# ---------------------------------------------------------------------------
# RE-BASELINE 2026-09-06 — FULLY ATTRIBUTED, and the cause is a THIRD one this
# gate's own instruction does not name.
#
# ⚠ THE CAUSE IS UNKNOWN, AND THIS COMMENT SAID OTHERWISE. Corrected 2026-09-06,
# same day it was written. It claimed `reference/` — the SCHEMA FLOOR every one of
# these findings is measured against — had been re-unpacked on 2026-09-02, AFTER the
# 2026-08-25 baseline. That was read off `reference/.unpacked-and-locked`'s MTIME,
# and the sentinel is the one file `bin/unpack-reference.sh` rewrites: its mtime is
# evidence about the SCRIPT, not about the tree. Gotcha #34 exactly — what an
# instrument returned, standing in for a fact about the world, written as fact.
#
# WHAT IT IS NOT (each measured, so the next reader does not re-walk these):
#   * NOT a reference/ content change. MEASURED 2026-09-06 by walking the tree:
#     1 of 510,711 files has an mtime after 2026-08-25, and it IS the sentinel.
#     XRCatTool preserves catalog timestamps, so the re-unpack rewrote the marker
#     and changed nothing else. The sentinel's own CONTENTS say 2026-06-22.
#   * NOT the mods. Both drifted mods are byte-frozen well before the baseline
#     (vro's newest file 2026-07-27, cpsdo_zb_modpack's 2026-07-24).
#
# THE CANDIDATE THAT WAS RULED OUT WITHOUT BEING TESTED, and it is one of the two
# the instruction below already names: THE CHECK DID CHANGE. `x4validate/_xsd.py`
# was modified twice after the baseline — 54d997e (2026-08-27, +42/-8, "the cheap
# script check now runs on every validate") and ffe6dd6 (2026-08-28, +131). Whether
# either reaches the effects/particle path these `amplitude` findings live on is
# UNMEASURED. It is the first thing to test, not the last.
#
# An honest unknown beats a tidy wrong story, and the REMEDY does not depend on
# which answer wins: this gate baselines the modlist and has NO fingerprint for the
# schema floor, so a schema change and a regression in the check are indistinguishable
# to it. **That is the finding worth carrying forward — not the numbers.**
#
# HOW THE SCHEMA WAS ISOLATED, without an old copy of the XSD to diff:
#   * both drifted mods are BYTE-FROZEN well before the baseline — vro's newest file
#     is 2026-07-27, cpsdo_zb_modpack's 2026-07-24.
#   * vro's `forkmaterial` count moved 5 -> 4 from an unchanged file that contains
#     exactly 4 occurrences. Same input, different error count: the only variable
#     left is what it was validated AGAINST. (The old entry said "x5 ... 4 corpus-wide
#     occurrences" — internally inconsistent all along; it is 1:1 now.)
#   * two independent, unchanged mods gained the SAME new error class simultaneously.
#
#            pairs  gating  advisory  suppressed  NOT checked  mods flagged
#   before     178      59        69           3            1            41
#   after      178      77        71           3            1            41
#   delta        0     +18        +2           0            0             0
#
# PER-ITEM, and it reconciles to the point — every unit of +18/+2 is a named mod:
#   escape_pod        -1 gating   REMOVED from extensions\ entirely (verified on disk).
#                                 KNOWN_REAL entry deleted below, xspvro-style.
#   vro              +15 gating   +16 `amplitude` (new class) and -1 `forkmaterial`.
#   cpsdo_zb_modpack  +4 gating   +4 `amplitude`. It bundles VRO content: byte-identical
#                                 payloads (`amplitude="0.025" attackduration="0.1" ...`).
#   x4_toolkit_helper +2 advisory OUR OWN mod, deployed 2026-08-31 i.e. after the
#                                 baseline. Both are the benign `ego_.+` addon-name
#                                 pattern class this file already documents — every
#                                 third-party UI mod trips it.
#   mods flagged 41 -> 41 and pairs 178 -> 178 are NET ZERO, not "unchanged":
#   escape_pod left and x4_toolkit_helper arrived. Two moving parts that cancel is
#   exactly the shape an aggregate hides (CLAUDE.md § compare PER ITEM), which is why
#   this was diffed per mod rather than read off the totals.
#
# WHY THE NEW CLASS IS GATING, BY THIS FILE'S OWN F7 CRITERION AND NOT BY JUDGEMENT.
# `amplitude` is real — but the schema declares it a CHILD ELEMENT
# (`<xs:element name="amplitude" type="amplitude"/>`, effects.xsd), while <low>/<high>
# take `attributeGroup ref="profile"`, which has no such attribute. These two mods
# write it as an ATTRIBUTE. That is F7's "a real attribute on the WRONG element",
# the same rationale `forkmaterial` and `category/@matchextension` are already pinned
# on. MEASURED packed-inclusive over the installed corpus — 125 mods, 4,618 XML files
# parsed, 11 unreadable:
#     as an ATTRIBUTE on <low>/<high>:  20 total — vro 16, cpsdo_zb_modpack 4, NOBODY ELSE
#     base+DLC (9 effects.xml docs):     0 as an attribute, 99 as a child element
# 20 occurrences produce exactly 20 findings, 1:1. The engine's own view is UNKNOWN
# (an ignored attribute logs nothing — CLAUDE.md #29), so this rests on the corpus
# denominator, exactly as forkmaterial's does.
#
# NOTHING IS UNATTRIBUTED IN THIS RE-BASELINE.
#
# RE-BASELINED 2026-09-26: advisory 71 -> 74, mods flagged 41 -> 42. MEASURED per item on
# the same installed world with the pre-remediation code (4815310: 71 / 41, OK) and the
# new code: the ONLY difference is +3 advisory in ONE mod, ebi_pirate_chaos_conflict --
# three `Element 'area': Character content is not allowed` in libraries/mapdefaults.xml.
# That mod ships `<add sel=".../properties/area">factionlogic=true</add>` three times (an
# attribute add missing its `type="@..."`). The old merge dropped a text-only add, so the
# schema never saw the text; AUDIT-2026-09-24 MG-2 models it, and the schema pass now
# reports a real authoring defect. Pairs, errors, suppressed and NOT-checked unchanged.
#
# RE-BASELINED 2026-10-13 (v4.0 final gate run, master 46238fc): pairs 178 -> 219, gating
# 77 -> 78, advisory 74 -> 80, mods flagged 42 -> 50; suppressed and NOT-checked unchanged.
# ATTRIBUTED IN FULL, per mod, to the MODLIST (no code change): 40 mods added (+47 pairs,
# +4 gating: re_expansion_pirate 3, otas_shippack 1; +7 advisory: Basestar_hades-class,
# ice_headhunter, ice_mission_commander, ice_mk4_ship_mods, print_extension_list,
# shield_visuals_rework, x4_ors; 9 of them flagged); 5 removed (-6 pairs, -1 advisory and
# -1 flagged: lc4hunter_argon_titan); and ONE in-place move, ship_variation_expansion gating
# 3 -> 0: the mod was updated v212 -> v221 (2026-10-08; every file rewritten while the
# folder mtime stayed 2026-08-03) and fixed its own three libraries/jobs.xml errors. MEASURED:
# current code on the v212 backup gives 3/27/3 (= the old row), and baseline-era code
# (adb0be0) on v221 gives 0/27/3 (= the new row), so the code did not move it.
# ---------------------------------------------------------------------------
EXPECT_PAIRS = 219
EXPECT_ERR = 78
EXPECT_INFO = 80
EXPECT_SUPPRESSED = 3
EXPECT_MODS_FLAGGED = 50
#: Files this sweep could NOT schema-check. Pinned from 2026-08-01, because the
#: gate previously froze only what WAS checked — so 31 documents skipped with a
#: false reason never moved a single number here. A rise means coverage was lost
#: silently. The 1 that remains is honest: shadergl.xsd is declared by
#: bh_shader but ships in no unpacked layer, so there is genuinely nothing to
#: validate against.
EXPECT_SKIPPED = 1

#: Independently evidenced against `reference\`, not just "the tool said so".
#: Each entry: mod -> (gating, advisory, what makes it real).
KNOWN_REAL = {
    "mlog_deadair_eco_no_da_wares": (30, 0,
        "removes <production>, orphaning the <limits> sibling — structural damage "
        "caused BY a diff, which no other check in this package can see"),
    "cpsdo_faction": (14, 1,
        "race='central' x7 — the effective race list (base+DLC+all 102 mods) has 10 "
        "entries and 'central' is not one of them; GATING since the F14 split "
        "(2026-08-02), joining the 7 element-not-expected it already had"),
    "ebi_timelines_faction_use_ship": (3, 0,
        "category/@matchextension x3 — a real attribute on the WRONG element: "
        "vanilla uses matchextension 140 times, every one on <location>, never on "
        "<category>, so the engine drops the intended DLC-matching silently (F7)"),
    "vro": (20, 3,
        "libraries/effects.xml, two F7 dead-attr classes: amplitude= as an ATTRIBUTE "
        "on <low>/<high> x16 -- the schema declares amplitude a CHILD ELEMENT and "
        "<low>/<high> take attributeGroup 'profile', which has no such attribute; "
        "MEASURED packed-inclusive over 125 mods / 4,618 files, 20 occurrences exist "
        "corpus-wide and 16 are VRO's, while base+DLC has 0 as an attribute against "
        "99 as an element. Plus element/@forkmaterial x4 -- invented attribute, all "
        "of them VRO itself (F7). Was 5 forkmaterial against the 2026-08-25 baseline "
        "figure; the file has 4 occurrences and now reports 4, 1:1. The old 5 was inconsistent with the file all along, so it is a corrected count rather than evidence that anything moved"),
    # xspvro removed 2026-08-08 (mod moved out of extensions\). Its entry was
    # (2 gating, 9 advisory) — "job ids containing a SPACE against the id pattern
    # facet" — and its departure accounts for the whole gating/advisory drop in
    # the re-measurement above. Restore this entry if the mod ever returns.
    "cpsdo_zb_modpack": (4, 1,
        "the same amplitude-as-an-ATTRIBUTE class as VRO's, x4 -- and the payloads are "
        "byte-identical to VRO's, so this mod is carrying VRO content. Pinned "
        "SEPARATELY from vro on purpose: they are two shipped copies of one upstream "
        "defect, and silencing either alone must stay visible"),
    "ter_radar_turret": (1, 0,
        "patch shipped at libraries/factiongoal_hold_space.xml when vanilla is "
        "md/factiongoal_hold_space.xml — schema-checked against the wrong shape"),
    # escape_pod removed 2026-09-06 (mod no longer present in extensions\). Its entry
    # was (1 gating, 0 advisory) -- "<filter> placed directly under <sound>; all 309
    # vanilla <filter> elements sit under <effects>, and <sound>'s content model has no
    # filter child" -- and its departure is the whole -1 in the gating delta above.
    # Restore this entry if the mod ever returns. (Same treatment as xspvro, 2026-08-08.)
}

_RE_SUP = re.compile(r"; (\d+) enumeration failure")

RECORD = "--record" in sys.argv
BASELINE = Path(__file__).resolve().parent.parent / ".schema-sweep-baseline.json"
#: Bump when the row shape changes. A baseline of another format is REFUSED (rc 2),
#: never read with guessed fields.
BASELINE_FORMAT = 1
FIELDS = ("gating", "advisory", "suppressed", "pairs", "not_checked")


def load_baseline() -> tuple[dict[str, dict[str, int]] | None, str]:
    """(per-mod rows, "") or (None, why the comparison cannot run)."""
    if not BASELINE.is_file():
        return None, f"no per-mod baseline at {BASELINE.name}"
    try:
        data = json.loads(BASELINE.read_bytes().decode("utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"{BASELINE.name} exists but cannot be read ({exc})"
    if not isinstance(data, dict) or data.get("_format") != BASELINE_FORMAT \
            or not isinstance(data.get("mods"), dict):
        return None, (f"{BASELINE.name} is not a format-{BASELINE_FORMAT} per-mod baseline "
                      f"(found _format={data.get('_format') if isinstance(data, dict) else '?'})")
    return data["mods"], ""


def compare_per_mod(base: dict[str, dict[str, int]],
                    now: dict[str, dict[str, int]]) -> list[str]:
    """Every per-mod difference, named. Items, never totals."""
    out = []
    for name in sorted(set(base) | set(now)):
        b, n = base.get(name), now.get(name)
        if b is None:
            out.append(f"{name}: not in the baseline (new since it was recorded): {n}")
        elif n is None:
            out.append(f"{name}: in the baseline, absent now (was {b})")
        else:
            moved = [f"{f} {b.get(f)} -> {n.get(f)}" for f in FIELDS if b.get(f) != n.get(f)]
            if moved:
                out.append(f"{name}: " + ", ".join(moved))
    return out


def main() -> int:
    ext = _env.extensions()
    mods = [d for d in sorted(ext.iterdir())
            if d.is_dir() and not d.name.lower().startswith("ego_dlc_")]
    cfg = _merge.Config()

    pairs = err = info = sup = skipped = 0
    skip_why: dict[str, int] = {}
    per_mod: dict[str, tuple[int, int]] = {}
    rows: dict[str, dict[str, int]] = {}          # EVERY mod, for the per-item baseline
    for d in mods:
        report = _check.Report()
        _check.check_effective_schema(d, cfg, report)
        # Files the check could NOT validate. Unpinned until 2026-08-01, which is
        # precisely how 31 false "not bundled" skips survived: the gate froze what
        # WAS checked and had no denominator for what was not.
        for s in report.skipped:
            skipped += 1
            skip_why[s.why.split(":")[0][:60]] = skip_why.get(s.why.split(":")[0][:60], 0) + 1
        e = sum(1 for f in report.findings if f.severity == "error")
        i = sum(1 for f in report.findings if f.severity == "info")
        mp = ms = 0
        for note in report.notes:
            if note.startswith("effective-schema:"):
                mp += int(note.split()[1])
            m = _RE_SUP.search(note)
            if m:
                ms += int(m.group(1))
        pairs += mp
        sup += ms
        err += e
        info += i
        if e or i:
            per_mod[d.name] = (e, i)
        rows[d.name] = {"gating": e, "advisory": i, "suppressed": ms, "pairs": mp,
                        "not_checked": len(report.skipped)}

    print(f"{len(mods)} non-DLC mods | {pairs} (mod,file) pairs validated "
          f"| {skipped} NOT checked")
    print(f"{err} gating + {info} advisory + {sup} suppressed = "
          f"{err + info + sup} introduced | {len(per_mod)} mods flagged\n")
    for why, n in sorted(skip_why.items(), key=lambda kv: -kv[1]):
        print(f"   NOT CHECKED x{n}: {why}")
    if skip_why:
        print()
    for name, (e, i) in sorted(per_mod.items(), key=lambda kv: -sum(kv[1])):
        mark = "*" if name in KNOWN_REAL else " "
        print(f" {mark} {name:36} {e:>3} gating  {i:>3} advisory")

    fail: list[str] = []
    for label, got, want in (("pairs", pairs, EXPECT_PAIRS),
                             ("gating", err, EXPECT_ERR),
                             ("advisory", info, EXPECT_INFO),
                             ("suppressed", sup, EXPECT_SUPPRESSED),
                             ("NOT checked", skipped, EXPECT_SKIPPED),
                             ("mods flagged", len(per_mod), EXPECT_MODS_FLAGGED)):
        if got != want:
            fail.append(f"{label}: got {got}, measured baseline is {want}")

    for name, (we, wi, why) in KNOWN_REAL.items():
        got = per_mod.get(name)
        if got is None:
            fail.append(f"{name}: REPORTS NOTHING — this defect is real ({why})")
        elif got != (we, wi):
            fail.append(f"{name}: {got[0]} gating/{got[1]} advisory, expected {we}/{wi} ({why})")

    # --- per-item: findings MOVING between mods cannot net to zero here ---------
    refuse = ""
    if RECORD and fail:
        # Never record over a failing run: the totals / KNOWN_REAL checks above just
        # said the check no longer matches its measurement, and writing today's rows
        # would make that regression the per-mod "normal" (review of a202814).
        print("\nNOT RECORDING the per-mod baseline: the totals/KNOWN_REAL checks failed "
              "(below). Attribute and fix those first.", file=sys.stderr)
    elif RECORD:
        blob = json.dumps({"_format": BASELINE_FORMAT, "mods": rows}, indent=1,
                          sort_keys=True) + "\n"
        BASELINE.write_bytes(blob.encode("utf-8"))
        print(f"\nrecorded per-mod baseline -> {BASELINE.name} ({len(rows)} mods)")
    else:
        base, why = load_baseline()
        if base is None:
            refuse = why
        else:
            drift = compare_per_mod(base, rows)
            print(f"\nper-mod vs {BASELINE.name}: {len(drift)} row(s) moved "
                  f"(of {len(set(base) | set(rows))} mods in either run)")
            for line in drift:
                print(f"   ~ {line}")
            fail.extend(f"per-mod: {line}" for line in drift)

    print()
    if fail:
        print("FAIL — the check no longer matches its own measurement:")
        for f in fail:
            print(f"  ! {f}")
        print("\nInvestigate before re-baselining. A moved number means the modlist "
              "changed, or the SCHEMA FLOOR did (this gate has NO fingerprint for it, so "
              "it cannot tell that from a regression -- on 2026-09-06 the movement was "
              "attributed per mod but its CAUSE was never established), or the "
              "check did (that is the regression this gate exists to catch). Diff the "
              "PER-MOD table above, not the totals: on 2026-09-06 both `mods flagged` and "
              "`pairs` read UNCHANGED while one mod left the install and another arrived.")
        return 1
    if refuse:
        print(f"NOT CHECKED PER MOD: {refuse}. The totals and KNOWN_REAL match, but totals "
              "can hold while findings move between mods, so this is not a pass. Attribute "
              "the per-mod table above, then run with --record.", file=sys.stderr)
        return 2
    print("OK — matches the recorded baseline exactly (see the re-measurement table "
          "above the constants), every per-mod row matches, and all %d "
          "independently-evidenced defects are still reported." % len(KNOWN_REAL))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
