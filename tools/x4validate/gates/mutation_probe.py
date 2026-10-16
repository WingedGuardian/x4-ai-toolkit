#!/usr/bin/env python
"""Mutation probe — does the suite actually KILL a broken detector, or just pass?

A passing test count is a count, not a quality. The only honest measure is: break
the code deliberately and see whether anything notices. A mutant that SURVIVES is
a line the suite does not really check — which is exactly how a `continue` that
discarded 858 mod operations sat in `_do_replace` for months with a green suite
above it.

WHY THIS COVERS MORE THAN `_merge.py` NOW (F54 mitigation 2). F54 records that a
mutation window can turn a sound assertion VACUOUS: with the ambiguous-`sel`
guard disabled, an assertion that it did NOT fire cannot go red. The general form
of that worry is "which of our detectors would nobody notice losing?", and a
mutant is the only positive control that cannot itself go vacuous. MEASURED
2026-08-25: 6/6 killed on `_merge.py` — those detectors are provably checked, and
nothing else had that proof.

Each mutation is a small, plausible edit against a guard whose failure has a
MEASURED cost in docs/BLIND-SPOTS.md or CLAUDE.md. That selection rule is
load-bearing: a contrived mutant that nothing kills is noise, not a finding.

⚠ THIS GATE DELIBERATELY BREAKS THE WORKING TREE WHILE IT RUNS. The mutated file
is a TRACKED file, so the tree looks normal — that is gotcha #27's actual lesson,
and it is why `.mutation-probe-active` exists and why you should tell anyone
sharing the tree before starting.

Run:  uv run python gates/mutation_probe.py [--verbose] [--recover]
Exit: 0 every mutant killed · 1 survivors or hangs · 2 cannot run (see --recover)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "x4validate"
VERBOSE = "--verbose" in sys.argv
RECOVER = "--recover" in sys.argv

#: Written before the first mutation, removed after the last restore. Its presence
#: means the tree may be mutated RIGHT NOW.
MARKER = ROOT / ".mutation-probe-active"
#: Byte-for-byte copies taken before any mutation, so recovery never depends on
#: `finally` having run. `finally` does NOT run on SIGKILL, and a killed probe
#: leaving a mutated source on disk is how v2.5.0 nearly shipped a disabled
#: ambiguous-sel check in a public release.
PRISTINE = ROOT / ".mutation-probe-pristine"

#: A mutant that has not failed in this long is not "slow", it is a finding about
#: the suite. This bounds a TARGETED run (the few test files in TARGETS), where the
#: previous 1800s meant one hanging mutant cost half an hour and made widening this
#: gate unaffordable.
#:
#: ⚠ The claim that used to sit here -- "the whole suite runs in ~20s" -- was a
#: measurement of the TARGETED runs that read as one about the whole suite, and it
#: rotted. MEASURED 2026-09-08 at ce986b2: the full suite is 1,649 tests in
#: **665.52s**, 33x that figure. Because `full_suite_failures()` shared this
#: constant, it timed out at 120s, returned its None non-answer, and the gate
#: REFUSED with rc 2 -- correctly, but it meant mutation_probe could not report at
#: all. Found by the v3.1.0 release sweep, where it was the one gate of 29 left.
TEST_TIMEOUT = 120

#: The WHOLE suite is a different population from a targeted run and needs its own
#: budget. Derived from the measurement above (665.52s) with ~2.7x headroom for a
#: slower machine and for the `.mutation-probe-active` marker being present.
#: Deliberately NOT shared with TEST_TIMEOUT: a hanging MUTANT must still be caught
#: in 120s, and one number serving two populations is what broke this gate.
FULL_SUITE_TIMEOUT = 1800

#: target filename -> the tests that should notice it breaking.
#: Scoped for speed; a survivor is re-checked against the FULL suite before being
#: reported, so scoping can never manufacture a false survivor.
TARGETS: dict[str, list[str]] = {
    "_merge.py": ["tests/test_merge.py", "tests/test_check.py", "tests/test_provenance.py"],
    "_registry.py": ["tests/test_registries.py", "tests/test_registry_unconfigured.py",
                     "tests/test_mod_scope_is_explicit.py",
                     "tests/test_profile_is_a_decision_log.py"],
    "_compat.py": ["tests/test_compat.py", "tests/test_compat_dropped.py"],
    "_check.py": ["tests/test_check.py", "tests/test_tierb.py",
                  "tests/test_reference_scope.py"],
    "_effective.py": ["tests/test_effective.py", "tests/test_prop_depth.py",
                      "tests/test_effective_scope.py", "tests/test_provenance.py"],
    "_scan.py": ["tests/test_scan.py", "tests/test_corpus_scan.py",
                 "tests/test_no_packed_only_scan.py"],
    #: The ORACLE. Everything else here proves the toolkit self-consistent; these
    #: two are the only code that can say our model disagrees with the ENGINE, so
    #: a silently-passing guard here is worse than elsewhere -- it would turn the
    #: one external check into a rubber stamp.
    "_livedump.py": ["tests/test_livedump.py"],
    "_livecli.py": ["tests/test_livecli.py", "tests/test_livearchive.py"],
    "_livepipe.py": ["tests/test_livepipe.py"],
}


@dataclass
class Mutant:
    target: str       # filename within x4validate/
    name: str
    old: str
    new: str
    why: str          # what a SURVIVOR would mean


#: Each anchor must be UNIQUE in its file, or the probe would mutate the wrong
#: site and report a meaningless result — so a non-unique anchor is itself a fail.
MUTANTS = [
    # --- _merge.py: the diff-application core every other tool trusts ----------
    Mutant("_merge.py", "root-replace payload guard disabled",
           "if len(new_children) != 1:",
           "if len(new_children) != 1 and False:",
           "the multi-payload guard on a root replace is unchecked"),
    Mutant("_merge.py", "ok always True (THE ORIGINAL BUG)",
           "applied.append(AppliedOp(op.tag, sel, line, reason is None,",
           "applied.append(AppliedOp(op.tag, sel, line, True,",
           "a no-op reported as applied and nothing notices"),
    Mutant("_merge.py", "ambiguous-sel guard removed",
           "if len(targets) > 1:",
           "if len(targets) > 99999:",
           "RFC-5261 single-node rule unenforced; ops apply where the engine skips"),
    Mutant("_merge.py", "empty-target guard removed",
           "if not targets:",
           "if not targets and False:",
           "a sel matching nothing would be reported as applied"),
    Mutant("_merge.py", "provenance dropped on root replace",
           'recorder.full_override(Origin(origin.source, "replace-root", origin.line))',
           "pass  # mutant: provenance dropped",
           "values land but origin stays base — the subtle half of the defect"),
    Mutant("_merge.py", "add pos=prepend never taken",
           'if pos == "prepend":',
           'if pos == "__never__":',
           "prepend ordering is unverified"),

    # --- _registry.py: which mods count (CLAUDE.md #24, #30a) ------------------
    Mutant("_registry.py", "profile default inverted (absent means disabled)",
           'enabled = [m for m in installed if prof.get(m["id"], m["enabled"])]',
           'enabled = [m for m in installed if prof.get(m["id"], False)]',
           "MEASURED #30a: 54 of 115 installed mods silently vanish from Tier B, "
           "x4compat, x4effective and x4eff at once, and nothing raises"),
    Mutant("_registry.py", "manifest-disabled mods treated as active",
           'enabled = [m for m in installed if prof.get(m["id"], m["enabled"])]',
           'enabled = [m for m in installed if prof.get(m["id"], True)]',
           "a mod disabled in its OWN manifest, with no profile entry, would be "
           "modelled as loaded (load-order probe 2026-09-26, case c)"),
    Mutant("_registry.py", "a missing REQUIRED dependency no longer excludes",
           'if not optional and d != m["id"]]',
           'if False]',
           "the engine does not load a mod whose required dependency is missing, "
           "disabled or cyclic (load-order probe 2026-09-26, cases d/f/g)"),
    Mutant("_registry.py", "scope validation removed",
           "if scope not in MOD_SCOPES:",
           "if scope not in MOD_SCOPES and False:",
           "a mistyped scope would silently choose a behaviour instead of raising"),

    # --- _compat.py: collision semantics (CLAUDE.md #18, F25) -----------------
    Mutant("_compat.py", "live_value_owner names a winner it cannot know",
           'if self.kind in ("SUBTREE", "NAME-CLASH", "SOFT") or self.target_tree == "unavailable" or not self.winner_qualified:',
           'if self.kind in ("SOFT",) or self.target_tree == "unavailable" or not self.winner_qualified:',
           "SUBTREE names the WIPER as owner and NAME-CLASH invents one — the "
           "exact conflation CLAUDE.md #18 exists to prevent"),
    Mutant("_compat.py", "NAME-CLASH claims a load-order winner",
           'kind="NAME-CLASH", target=name, mods=folders, winner="",',
           'kind="NAME-CLASH", target=name, mods=folders, winner=folders[0],',
           "index/macros.xml decides a name clash, not load order; naming one is "
           "a guess wearing the grammar of a measurement"),
    # --- _check.py: the bare-vs-nested door (gotcha #6, F19) -------------------
    Mutant("_check.py", "nested-path rewrite disabled",
           "nested = _merge._nested_target(vpath, config.packed_dlc_names())",
           "nested = None  # mutant: mod-on-mod patches lose their target",
           "a cross-mod patch silently no-ops; the engine never opens the bare form"),

    # --- _effective.py: load order and property depth --------------------------
    Mutant("_effective.py", "load order replaced by ALPHABETICAL (gotcha #13)",
           "order = _compat.compute_load_order(mods)",
           'order = sorted(m["folder"] for m in mods)  # mutant: alphabetical',
           "the wrong mod wins: people.capacity reads 0 alphabetically, 200 in true order"),
    Mutant("_effective.py", "property recursion truncated at depth 1",
           "if depth >= MAX_PROP_DEPTH:",
           "if depth >= 1:  # mutant: the whole flight model disappears",
           "9,197 of 13,291 ship attributes vanish and the store still reports success"),

    # --- _scan.py: loose THEN packed (F1, written 7 times) ---------------------
    Mutant("_scan.py", "packed half of iter_mod_xml never entered",
           '    for vpath, member in sorted(_cat.mod_vfs(mod_dir, packed_only=True).items()):\n        if not vpath.lower().endswith(".xml") or vpath.lower() in yielded:\n            continue\n        if predicate is not None and not predicate(vpath):\n            continue\n        try:\n            root = _merge.parse_bytes(_cat.read_member(member))',
           '    for vpath, member in []:  # mutant: packed half never entered\n        if not vpath.lower().endswith(".xml") or vpath.lower() in yielded:\n            continue\n        if predicate is not None and not predicate(vpath):\n            continue\n        try:\n            root = _merge.parse_bytes(_cat.read_member(member))',
           "62% of mod XML invisible; a negative becomes a confident false absence"),

    # --- _livedump.py: the four outcomes must never collapse -------------------
    # Six guards, mutated SEPARATELY because a guard that fires first SHADOWS the
    # ones behind it -- verified 2026-08-27, where clause 4's mutant survived the
    # generic test and was caught only by its own dedicated one.
    Mutant("_livedump.py", "not-a-uidata detection removed",
           'if "<uidata" not in text:',
           "if False:  # mutant",
           "an arbitrary file gets the stub explanation: right rc, confidently wrong reason"),
    Mutant("_livedump.py", "running-game stub reads as data",
           'if "<data" not in text:',
           "if False:  # mutant",
           "X4 truncates uidata.xml while running; the 61-byte stub would report '0 extensions'"),
    Mutant("_livedump.py", "variable-absent refusal downgraded",
           'raise LiveDumpUnavailable(\n            f"the variable {var!r} is not assigned in this uidata.xml - the probe "\n            f"did not run, or its ui.xml does not declare the savedvariable")',
           'return ""  # mutant: missing probe becomes an empty payload',
           "'the probe never ran' becomes indistinguishable from a malformed dump"),
    Mutant("_livedump.py", "HDR guard removed",
           "if not rows or rows[0][0] != HDR:",
           "if False:  # mutant",
           "a payload truncated at the FRONT parses as a short valid answer"),
    Mutant("_livedump.py", "terminator guard removed",
           "if rows[-1][0] != END:",
           "if False:  # mutant",
           "a truncated dump reports fewer rows and calls it a finding"),
    Mutant("_livedump.py", "row-count self-check removed",
           "if claimed != len(rows):",
           "if False:  # mutant",
           "the game's own row count stops being checked; a mis-decoded payload passes"),

    # --- _livecli.py: the oracle's comparison rules ----------------------------
    Mutant("_livecli.py", "unit transform never applied",
           "cooked = str(_TRANSFORMS[tname](float(ev)))",
           "cooked = ev  # mutant",
           "engine radians compared to stored degrees: 3 false disagreements per thruster"),
    # NOTE the leading newline: the 12-space form is a SUBSTRING of the 20-space
    # one in cmd_mappings, so without it the anchor matches twice and the clause is
    # silently left UNMUTATED -- i.e. untested while looking covered.
    Mutant("_livecli.py", "transform auto-picked instead of declared",
           chr(10) + "            if _agree(cooked, sv):",
           chr(10) + "            if _agree(cooked, sv) or _agree(ev, sv):  # mutant",
           "the comparison can no longer FAIL on a wrong unit: a green that cannot go red"),
    Mutant("_livecli.py", "TRANSFORM-SUSPECT never reported",
           'suspect = tname != "identity" and _agree(ev, sv)',
           "suspect = False  # mutant",
           "a wrong unit transform is reported as a model disagreement with no cause named"),
    Mutant("_livecli.py", "mapping table no longer keyed by library type",
           "return _BY_TYPE.get(ltype, {}).get(field)",
           "return next((m[field] for m in _BY_TYPE.values() if field in m), None)  # mutant",
           "a ship's `shield` resolves to a generator's recharge.max -- #18 in a lookup table"),
    Mutant("_livecli.py", "degeneracy rule accepts 1",
           "    if f in (0.0, 1.0):\n        return True",
           "    if f in (0.0,):  # mutant\n        return True",
           "value-matching on 1 invents mappings: drag_forward=1 -> identification.deployable"),
    Mutant("_livecli.py", "engine-DERIVED folded back into unmapped",
           "                if f in _DERIVED:",
           "                if False:  # mutant",
           "F72's known modelling gap hides inside a generic bucket and looks like missing rows"),

    # --- _livecli.py: the ARCHIVE contract. Ground truth costs a play session and
    # lives in a file X4 overwrites on exit -- one has already been lost that way.
    # NOTE: "copies without decoding first" has NO honest mutant here. The guarantee is
    # STATEMENT ORDER (parse, then copy), which a one-line substitution cannot invert --
    # and a mutant that dies of NameError would report "killed" for a reason unrelated to
    # the claim, which is worse than no mutant: it certifies a test that never ran.
    # tests/test_livearchive.py asserts the ordering directly instead.
    Mutant("_livecli.py", "archive size floor removed",
           "    if len(data) < 4096:",
           "    if False:  # mutant",
           "a truncated read archives cleanly and becomes what a future session quotes"),
    Mutant("_livecli.py", "archive stops being content-addressed",
           "    if existing is not None:",
           "    if False:  # mutant",
           "every run accumulates a near-duplicate; no row can be quoted with confidence"),
    # --- _livecli.py: the WRITE verbs. Each mutant removes one clause of the contract
    # that decides whether a change to the RUNNING game is reported as verified.
    Mutant("_livecli.py", "write banner suppressed",
           "    print(_write_banner(verb), file=sys.stderr)",
           "    pass  # mutant",
           "a write to the running game is sent with no announcement at all"),
    Mutant("_livecli.py", "query stops refusing the write verbs",
           "    if verb in WRITE_VERBS:",
           "    if False:  # mutant",
           "`query pause` becomes a second, bannerless door to every write"),
    Mutant("_livecli.py", "a disagreeing read-back is reported as verified",
           '    if r.status == "OK" and acted == "yes" and agree == "yes":',
           '    if r.status == "OK" and acted == "yes":  # mutant',
           "exit 0 over a game that is NOT in the state that was asked for"),
    Mutant("_livecli.py", "an ERR after ACTING is reported as a clean refusal",
           '    if r.status == "ERR" and acted == "no":',
           '    if r.status == "ERR":  # mutant',
           "a raised or unverified write reads as 'nothing changed' when something may have"),
    Mutant("_livecli.py", "the advisory row is accepted anywhere, not only FIRST",
           '    tokens = fields[0].split(" ")',
           '    tokens = next((f for f in fields if f.startswith("write=yes")), fields[0]).split(" ")  # mutant',
           "prose that merely mentions the marker is read as the marker"),
    Mutant("_livecli.py", "a lost write reply no longer warns it may have landed",
           "        if sent:",
           "        if False:  # mutant",
           "a caller resends a pause blind, or assumes nothing happened"),
    # NOTE the leading newline + EIGHT spaces. The archive check sits inside a
    # try-block and the groundtruth one does not, so the 4-space form is a
    # SUBSTRING of this one -- anchoring on it matches twice and leaves the clause
    # UNMUTATED while looking covered (register #99, caught here by the
    # anchor-uniqueness test rather than by luck).
    Mutant("_livecli.py", "archive trusts the write instead of re-reading",
           '\n        if back != data:',
           '\n        if False:  # mutant',
           "'N written' is the writer's intent, not the file's state -- #98's exact shape"),
    Mutant("_livecli.py", "groundtruth trusts its write instead of re-reading",
           '\n    if back != data:',
           '\n    if False:  # mutant',
           "the harvested fixture can be written short and reported as complete"),
    Mutant("_livecli.py", "the archive hint never stops firing",
           '    if _verified_archive(d, sha) is not None:',
           "    if False:  # mutant",
           "a reminder that always fires trains you to ignore the channel it shares"),

    # --- _livepipe.py: the live-channel frame contract -------------------------
    # Eight clauses, mutated SEPARATELY. Clause 3 is COMPOUND and gets TWO mutants,
    # one per half, because each guard shadows the ones behind it -- a single twin
    # against a multi-clause condition only ever tests the half it trips first
    # (register #86, and #99 where the shadowed half survived twice).
    Mutant("_livepipe.py", "empty reply treated as an empty answer",
           "if not data:",
           "if False:  # mutant",
           "the lua api's nil-for-empty-write becomes a valid zero-field answer"),
    Mutant("_livepipe.py", "foreign frames and reserved sentinels accepted",
           'if parts[0] != REPLY_TAG.encode("utf-8"):',
           "if False:  # mutant",
           "a bare ERROR/TIMEOUT/CANCELLED from the api reads as payload data"),
    Mutant("_livepipe.py", "clause 3a: the length half dropped (compound guard)",
           'if len(parts) < 2 or parts[1] != str(PROTO).encode("utf-8"):',
           'if parts[1] != str(PROTO).encode("utf-8"):  # mutant',
           "a bare tag raises IndexError -- a CRASH, which is not one of the four outcomes"),
    Mutant("_livepipe.py", "clause 3b: the protocol half dropped (compound guard)",
           'if len(parts) < 2 or parts[1] != str(PROTO).encode("utf-8"):',
           "if len(parts) < 2:  # mutant",
           "a mod and a toolkit from different versions mis-parse each other in silence"),
    Mutant("_livepipe.py", "header-truncation guard removed",
           "if len(parts) != _REPLY_FIELDS:",
           "if False:  # mutant",
           "a frame too short to carry its own length unpacks anyway, or crashes"),
    Mutant("_livepipe.py", "FIFO desync guard removed",
           "if seq != expect_seq:",
           "if False:  # mutant",
           "correlation is positional; one dropped reply hands back the PREVIOUS question's answer"),
    Mutant("_livepipe.py", "unknown status accepted",
           "if status not in STATUSES:",
           "if False:  # mutant",
           "the game answers in a vocabulary we do not model and we act on it"),
    Mutant("_livepipe.py", "TRUNCATION guard removed (the module's whole reason)",
           "if declared != actual:",
           "if False:  # mutant",
           "pipes.lua:698's unhandled ERROR_MORE_DATA lands as a short but well-formed row"),
    Mutant("_livepipe.py", "truncation no longer NAMED as truncation",
           'f"TRUNCATED: the game declared {declared} payload bytes and {actual} "',
           'f"problem: the game declared {declared} payload bytes and {actual} "  # mutant',
           "clause ordering stops being a contract; the only actionable advice is never printed"),
    #: Clause 9 and the transport, the two halves of "decode LAST and STRICTLY".
    #: Both survived the full suite on 2026-09-02 because every test passed a str,
    #: which is encoded losslessly -- so the strict-decode branch was structurally
    #: unreachable from the suite. Killed now by byte-domain fixtures.
    Mutant("_livepipe.py", "clause 9 decodes leniently again",
           "payload = payload_bytes.decode(\"utf-8\")",
           "payload = payload_bytes.decode(\"utf-8\", errors=\"replace\")  # mutant",
           "a payload the game encoded wrong is served as a clean answer with the bad bytes silently rewritten"),
    Mutant("_livepipe.py", "the transport decodes before anything measures it",
           "                return data",
           "                return data.decode(\"utf-8\", errors=\"replace\")  # mutant",
           "each replacement character adds +2 bytes, which invents truncations and CANCELS real ones"),
    Mutant("_livepipe.py", "corruption guard removed",
           "if declared_sum != checksum(payload_bytes):",
           "if False:  # mutant",
           "length-preserving corruption passes as a clean answer"),
    Mutant("_livepipe.py", "declared length counts CHARACTERS not bytes",
           'return len(payload if isinstance(payload, bytes) else payload.encode("utf-8"))',
           "return len(payload)  # mutant",
           "disagrees with lua's `#s` on every non-ASCII payload, reporting truncation where there is none"),
    Mutant("_livepipe.py", "checksum over latin-1 instead of utf-8",
           'for b in (payload if isinstance(payload, bytes) else payload.encode("utf-8")):',
           'for b in bytes(str(payload), "latin-1", "replace"):  # mutant',
           "both python sides move together and agree; only the fixed lua side disagrees"),
    #: The 2026-09-02 fix: _read_raw used to decode with errors="replace", so clauses
    #: 7 and 8 measured a REPAIRED string. U+FFFD is 3 bytes, so each bad input byte
    #: added +2 -- inventing truncations and, worse, cancelling real ones.
    Mutant("_livepipe.py", "the payload is repaired before it is measured",
           "    data = text.encode(" + chr(34) + "utf-8" + chr(34) + ") if isinstance(text, str) else text",
           "    data = (text.encode() if isinstance(text, str) else "
           "text.decode(" + chr(34) + "utf-8" + chr(34) + ", " + chr(34) + "replace" + chr(34)
           + ").encode())  # mutant",
           "clauses 7 and 8 go back to measuring a string we invented, not what arrived"),
    Mutant("_livepipe.py", "payload re-split on every tab",
           r'parts = data.split(b"\t", _REPLY_FIELDS - 1)',
           r'parts = data.split(b"\t")  # mutant',
           "a multi-column answer loses every field after the first tab, and a short row is still a row"),
]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def targets_in_play() -> list[str]:
    return sorted({m.target for m in MUTANTS})


def take_pristine() -> None:
    """Copy every target aside BEFORE any mutation, and record that we did."""
    PRISTINE.mkdir(exist_ok=True)
    state = {"pid": os.getpid(), "targets": {}}
    for name in targets_in_play():
        src = PKG / name
        shutil.copy2(src, PRISTINE / name)
        state["targets"][name] = _sha(src)
    MARKER.write_text(json.dumps(state, indent=1), encoding="utf-8")


def restore_all(verify: bool = True) -> list[str]:
    """Put every target back from its pristine copy. Returns what actually differed."""
    changed = []
    for name in targets_in_play():
        src, dst = PRISTINE / name, PKG / name
        if not src.is_file():
            continue
        if not dst.is_file() or _sha(dst) != _sha(src):
            changed.append(name)
        shutil.copy2(src, dst)
        if verify and _sha(dst) != _sha(src):
            raise RuntimeError(f"RESTORE FAILED for {name} - tree is left MUTATED")
    return changed


def clear_marker() -> None:
    MARKER.unlink(missing_ok=True)
    shutil.rmtree(PRISTINE, ignore_errors=True)


def recover() -> int:
    """Explicit recovery. Deliberately NOT automatic.

    Auto-restoring would mean deciding whether the recorded pid is still alive,
    and on Windows `os.kill(pid, 0)` does not mean "probe liveness" - it maps to
    TerminateProcess. Guessing wrong in that direction would either kill a running
    probe or overwrite files underneath it. An explicit flag cannot make that
    mistake, and it forces a human to read what happened rather than skip past it.
    """
    if not MARKER.is_file():
        print("nothing to recover: no .mutation-probe-active marker.")
        return 0
    state = json.loads(MARKER.read_text(encoding="utf-8"))
    print(f"recovering from a probe that did not finish (pid {state.get('pid')}):")
    changed = restore_all()
    for name in targets_in_play():
        mark = "RESTORED (was mutated)" if name in changed else "already clean"
        print(f"  {name:<16} {mark}")
    clear_marker()
    print("tree is clean. Re-run the probe when ready.")
    return 0


#: Verdicts that make the GATE fail. `fail` is absent on purpose: it means the
#: MUTANT died, which is the outcome this gate exists to confirm.
FAILING_VERDICTS = frozenset({"pass", "hang", "noscope"})


#: The environment every mutant run must use, plus a cache purge to go with it.
#:
#: NOT hygiene -- without this the gate returns WRONG VERDICTS. CPython invalidates a
#: cached .pyc on (source mtime in SECONDS, source size). Successive mutants are
#: written to the same path within the same second, so whenever two of them leave the
#: file the SAME SIZE, the second run imports the FIRST one's bytecode and is judged by
#: the previous mutant's failures.
#:
#: `scripts/verify-hook-tests.py` already carries this fix and records the measurement:
#: 2 of 54 mutants reported "NOT CAUGHT" while each, reproduced by hand, turned its
#: target test red -- the tell being that the failing tests named belonged to the
#: mutant BEFORE it. This gate mutates `_merge.py` the same way and had no such guard.
#:
#: It bites the RESTORE too, which is the worse direction and the one measured on
#: 2026-09-03: a mutant turning `return 2` into `return 0` is identical in LENGTH, so
#: after restoring the pristine source -- verified byte-identical by sha256 -- the
#: stale .pyc was still imported and every later test in that session ran the MUTANT
#: while the file on disk was correct. A byte-identity check cannot see this: it looks
#: at the source, and the defect lives in the cache.
def _no_bytecode_env() -> dict:
    # env-ok: PYTHONDONTWRITEBYTECODE is an interpreter switch for the CHILD
    # process, not toolkit configuration -- there is no config-file layer for it
    # and _paths would have nothing to say about it.
    return dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


def _purge_bytecode() -> None:
    for pyc in ROOT.rglob("__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)


def run_tests(tests: list[str], deselect: tuple[str, ...] = ()) -> str:
    """'pass' = mutant survived | 'fail' = killed | 'hang' = did not finish
    | 'noscope' = there were no tests to run, which is a NON-ANSWER.

    A missing path is filtered out rather than handed to pytest, which is the
    right thing to DO and was the wrong thing to do SILENTLY (F62): the public
    copy of this gate ran 3 of the 4 `_registry` test files -- the fourth was
    never ported -- and printed the same `killed` line as a full scope.
    """
    present = [t for t in tests if (ROOT / t).exists()]
    missing = [t for t in tests if t not in present]
    if missing:
        print(f"    scope narrowed: {len(present)} of {len(tests)} test file(s) "
              f"present; MISSING {', '.join(missing)}")
    if not present:
        # NOT "hang". Reporting an absence as a timeout asserts a measurement
        # that was never taken -- and the caller then re-runs it as though it
        # were confirming a hang, which cannot resolve a file that is not there.
        return "noscope"
    try:
        args = ["uv", "run", "--project", str(ROOT), "pytest", "-q", *present]
        for d in deselect:
            args += ["--deselect", d]
        _purge_bytecode()
        p = subprocess.run(args,
                           cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=TEST_TIMEOUT, env=_no_bytecode_env())
    except subprocess.TimeoutExpired:
        return "hang"
    return "pass" if p.returncode == 0 else "fail"


#: Tests that assert a property of the PRISTINE tree, and therefore fail for EVERY
#: mutant regardless of whether that mutant was caught. They are tests OF the probe,
#: not of the code under mutation, so including them in the escalation below turns
#: every survivor into a "kill".
#:
#: MEASURED 2026-09-02, and it had disabled this gate's entire purpose. `full_suite()`
#: is only reached when the SCOPED tests PASS -- i.e. only for a genuine survivor --
#: and it returned "fail" for every one of them, so each was printed `killed`. TWO
#: independent causes, either sufficient on its own:
#:   1. `test_every_anchor_is_unique_in_its_real_target` reads the real source and
#:      asserts each anchor appears exactly once; with a mutant applied that mutant's
#:      own anchor is gone, so the count is 0. Deselected here.
#:   2. `path.write_text()` re-wrote the LF-pinned target in CRLF (measured: 0 to 790
#:      CRLF in _merge.py), so the line-ending pin gate failed too. That one is fixed
#:      at the I/O sites below rather than by deselecting, because it was a real
#:      defect the probe itself was introducing, not a property of measurement.
FULL_SUITE_DESELECT = (
    "tests/test_mutation_probe.py::test_every_anchor_is_unique_in_its_real_target",
)


def full_suite() -> str:
    return run_tests(["tests"], FULL_SUITE_DESELECT)


def full_suite_failures() -> set | None:
    """The SET of failing test ids across the whole suite, or None if it could not run.

    A BOOLEAN is not enough here, and that is the third cause of the false kill.
    `x4validate/_mutation.py` makes every artifact-writing path REFUSE while
    `.mutation-probe-active` exists -- deliberately, so a poisoned artifact cannot
    outlive the window -- so 5 tests in tests/test_effective.py fail for the whole
    probe run. MEASURED 2026-09-02: 5 failed / 1178 passed with the marker present,
    1183 passed without it. `full_suite()` was therefore red for every mutant, and
    every survivor was printed `killed`.

    So the escalation compares failure SETS: a mutant is caught only if it makes a
    test fail that was NOT already failing. That is the specific consequence, rather
    than "something changed" -- and it needs no exception list to rot.
    """
    args = ["uv", "run", "--project", str(ROOT), "pytest", "-q", "tests",
            "--tb=no", "-rf"]
    for d in FULL_SUITE_DESELECT:
        args += ["--deselect", d]
    try:
        _purge_bytecode()
        p = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=FULL_SUITE_TIMEOUT, env=_no_bytecode_env())
    except subprocess.TimeoutExpired:
        # silent-ok: None IS this function's non-answer channel, not a swallow. The
        # caller REFUSES on None (rc 2) rather than treating it as "nothing failed",
        # which is the distinction this rule exists to enforce.
        return None
    out = set()
    for line in p.stdout.splitlines():
        for tag in ("FAILED ", "ERROR "):
            if line.startswith(tag):
                out.add(line[len(tag):].split(" ")[0])
    if p.returncode not in (0, 1):
        # 2 = interrupted, 3 = internal error, 4 = usage. None of those is a
        # measurement, and an empty set from one would read as "nothing failed".
        return None
    return out


def main() -> int:
    if RECOVER:
        return recover()

    if MARKER.is_file():
        print("REFUSING: .mutation-probe-active exists, so either another probe is "
              "running or a previous one died mid-mutation.", file=sys.stderr)
        print("  If no probe is running, the tree may be MUTATED right now. Recover with:",
              file=sys.stderr)
        print("      uv run python gates/mutation_probe.py --recover", file=sys.stderr)
        return 2

    baseline = run_tests(sorted({t for ts in TARGETS.values() for t in ts}))
    if baseline == "noscope":
        print("BASELINE has NO TEST FILES in scope - every path in TARGETS is "
              "missing, so this gate cannot measure anything.", file=sys.stderr)
        return 2
    if baseline == "hang":
        print(f"BASELINE did not finish within {TEST_TIMEOUT}s - fix that first.",
              file=sys.stderr)
        return 2
    if baseline == "fail":
        print("BASELINE IS RED - fix the suite before measuring mutants.", file=sys.stderr)
        return 1

    by_target: dict[str, list] = {}
    for m in MUTANTS:
        by_target.setdefault(m.target, []).append(m)
    print(f"MUTATION PROBE - {len(MUTANTS)} mutants across {len(by_target)} file(s)")
    print("=" * 88)

    survivors, hangs, noscopes = [], [], []
    take_pristine()
    # AFTER take_pristine, deliberately: the marker is now on disk, so this baseline
    # includes the tests that refuse BECAUSE of it. Taking it before would compare two
    # different worlds and call every mutant a kill -- which is exactly what happened.
    baseline_failures = full_suite_failures()
    if baseline_failures is None:
        restore_all()
        clear_marker()
        print("REFUSING: the full suite could not be run on the pristine tree, so no "
              "escalation below could distinguish a survivor from a kill.",
              file=sys.stderr)
        return 2
    if baseline_failures:
        print(f"note: {len(baseline_failures)} test(s) already fail with the mutation "
              f"marker present; a mutant counts as caught only if it adds to them.")
    try:
        for target, group in by_target.items():
            path = PKG / target
            # BYTES, not text. read_text/write_text round-trip through universal
            # newlines, and Python text mode on Windows re-writes every line ending as
            # CRLF -- so mutating an LF-pinned file rewrote the WHOLE file (measured:
            # 790 CRLF in _merge.py). That turned the line-ending gate red for every
            # mutant, which made every survivor read as a kill.
            original_bytes = path.read_bytes()
            original = original_bytes.decode("utf-8")
            print(f"\n  {target}")
            for m in group:
                n = original.count(m.old)
                if n != 1:
                    print(f"    SKIP   {m.name:<44} anchor appears {n}x (need exactly 1)")
                    survivors.append((m, f"anchor not unique ({n}) - probe cannot aim"))
                    continue
                path.write_bytes(original.replace(m.old, m.new, 1).encode("utf-8"))
                verdict = run_tests(TARGETS[target])
                if verdict == "hang":
                    # Confirm before reporting. `subprocess.run(timeout=)` counts
                    # WALL CLOCK, so a machine suspend fires it while no CPU time
                    # passed -- MEASURED 2026-08-26 in corpus_sweep, which reported
                    # a 20-minute HANG for a mod that re-ran in 13s. On this machine
                    # the sleep timer counts USER inactivity, and neither the agent's
                    # commands nor CPU load reset it, so any long unattended run can
                    # be interrupted. Same rule as perf_guard (F50) and now
                    # corpus_sweep: a timing that spans a suspend is a NON-ANSWER.
                    verdict = run_tests(TARGETS[target])
                if verdict == "pass":
                    # Scoped tests missed it. Before calling it a survivor, ask the
                    # WHOLE suite - scoping must never manufacture a false survivor.
                    # Same shape as perf_guard's confirm-before-reporting (F50).
                    #
                    # NEW failures only. The baseline already contains the tests that
                    # refuse because the marker exists; counting those as a kill is
                    # what turned every survivor into one.
                    now = full_suite_failures()
                    if now is None:
                        verdict = "hang"
                    else:
                        new = now - baseline_failures
                        verdict = "fail" if new else "pass"
                path.write_bytes(original_bytes)
                if verdict == "noscope":
                    # Never challenged, so not a kill. F62 in one line: the public
                    # copy of this gate ran 3 of 4 _registry test files and still
                    # printed `killed`.
                    print(f"    NOSCOPE {m.name:<43} no test file in scope exists")
                    noscopes.append((m, "no test file in this target's scope exists"))
                elif verdict == "hang":
                    print(f"    HUNG   {m.name:<44} exceeded {TEST_TIMEOUT}s")
                    hangs.append((m, f"tests did not finish within {TEST_TIMEOUT}s"))
                elif verdict == "pass":
                    print(f"    LIVED  {m.name:<44} {m.why[:40]}")
                    survivors.append((m, m.why))
                else:
                    print(f"    killed {m.name:<44}")
    finally:
        restore_all()
        clear_marker()

    killed = len(MUTANTS) - len(survivors) - len(hangs) - len(noscopes)
    print("\n" + "=" * 88)
    print(f"killed {killed}/{len(MUTANTS)}   survivors: {len(survivors)}   "
          f"hangs: {len(hangs)}   unmeasured: {len(noscopes)}")
    for m, why in survivors:
        print(f"\n  SURVIVOR: {m.target} - {m.name}")
        print(f"    edit:  {m.old[:66]}")
        print(f"      ->   {m.new[:66]}")
        print(f"    means: {why}")
    for m, why in hangs:
        print(f"\n  HUNG: {m.target} - {m.name}")
        print(f"    means: {why} (a mutant that hangs is a finding, not a pass)")
    for m, why in noscopes:
        print(f"    UNMEASURED: {m.target} - {m.name}")
        print(f"    means: {why}. Nothing was asked of this mutant, so nothing "
              f"was learned - 'could not check' is never 'nothing wrong'.")
    return 1 if (survivors or hangs or noscopes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
