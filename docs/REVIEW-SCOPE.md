# What gets reviewed, and what does not

## Why this file exists

The release rule in the maintainer's global instructions ORIGINALLY read: *"Not a review of
the last change, or of the risky-looking part. The whole release surface: every commit since
the previous release, and every file the bundle ships."* It now reads "every file that range
adds or modifies" and says to scope to what the release changes, not to everything the bundle
ships: the correction this file argues for. The quote is kept as the history of why.

This file covers releases of the TOOLKIT. Publishing an X4 mod you made uses the
`x4-mod-release-review` skill: the same diff scoping, but the review changes nothing and every
finding goes to the mod's author before any fix, because the mod's correctness is judged in game.

Applied literally, that turns every release review into a **whole-repository audit**.
MEASURED on the v3.0.0 round-6 review, which was aimed at "the 9,163 lines earlier
rounds declared unread":

| | |
|---|---|
| findings | 3 CRITICAL + 13 IMPORTANT |
| of those, **predating the 3.0 arc** | **15 of 19 defect sites** (oldest 2026-06-23) |
| both CRITICALs | **already shipped in v2.8.0**, byte-identical |

So the round spent most of its budget finding defects that were already in users' hands
and that shipping or not shipping v3.0.0 would not change either way. That is a real
audit and worth doing — but it is not a **release gate**, and conflating the two is what
made three consecutive rounds each return ~15 findings and never converge.

**The split this file establishes: a RELEASE review is scoped to what the release
CHANGES. A CODEBASE AUDIT is scoped by blast radius and runs on its own schedule.**

---

## Track 1 — the RELEASE review (gates the release)

**Scope: every production file the arc ADDED or MODIFIED, and nothing else.**
Derive it, never list it from memory:

    BASE=$(git rev-list -n1 <previous release tag>)
    git diff --diff-filter=AM --name-only $BASE..HEAD

A file the arc did not touch cannot contain a regression the arc caused. If it holds a
defect, that defect is already released, and it belongs to Track 2.

**Two things this scope must NOT inherit from a diff-scoped review** (CLAUDE.md #37 --
a diff-scoped review cannot find a defect that predates the diff):

1. A file the arc **modified** is reviewed **in full**, not just its hunks. The arc's
   change may be correct while the function around it is not, and the arc is what puts
   it in front of new users.
2. A subsystem the arc **added** is reviewed in full before its first release, however
   large. MEASURED: `live_query.lua` (2,501 lines) was added on 2026-08-31 and first
   read in round 6 -- it carried 1 CRITICAL + 3 IMPORTANT, every in-arc finding of the
   round.

**Release exit criterion:** zero CRITICAL and zero IMPORTANT **among in-arc findings**.
Pre-existing findings are recorded to Track 2 and do not hold the release.

**Release exercises** (run once per release, before this review closes):
- The cold ADAPTING exercise, [`docs/ADAPTING-COLD-TEST.md`](ADAPTING-COLD-TEST.md): a fresh
  subagent given only `ADAPTING.md` and the toy agent's docs must write an adapter that passes
  `x4guard conformance`. A failure means `ADAPTING.md` is wrong or stale.

---

## Track 2 — the CODEBASE AUDIT (does not gate any release)

Ranked by one question: **does a defect here produce a confidently wrong FACT?**
That is the maintainer's criterion -- "the tools that are actually going to be spitting
truth values at us and declaring something is factual or not" -- and it sorts the repo
better than size or age does.

### Tier 1 — ORACLES. A defect writes a wrong fact into permanent record.

> ⚠ **THESE LINE COUNTS ARE A SNAPSHOT, NOT AN INVARIANT, and the claim that they
> could "never disagree" with `COVERAGE.tsv` was itself false when written.**
> Corrected 2026-09-06 (second pass): counted with `audit-coverage.py::line_count`,
> three of twelve entries matched NO commit anywhere in `v3.0.0..HEAD` at the moment
> the invariance sentence was added -- `_freshness.py` 678 (really 727),
> `_provenance.py` 119 (118), `_nexus.py` 240 (250). Four later commits in the same
> arc then moved `_compat.py` (906 -> 852), `_effective.py` (897 -> 977) and
> `_merge.py` (974 -> 1013) with no re-pin.
>
> **Use them for TIERING, which is what they are for, and re-derive before quoting
> one as a fact.** A page cannot pin a number in a tree that keeps moving; only
> `audit-coverage.py` can, because it derives the population from `git ls-tree` at a
> named rev and REFUSES a ledger that disagrees. Found by the v3.1.0 release
> reviewer, which is the second time this page's own numbers rotted.

| file | lines | why it ranks here |
|---|---|---|
| `_compat.py` | 906 | computes LOAD ORDER -- decides who wins every conflict (#13) |
| `_effective.py` | 897 | builds the effective tree every "effective value" claim quotes |
| `_merge.py` | 974 | applies diffs; where 858 dropped-ops-reported-applied lived |
| `_resolve.py` | 247 | reference resolution (partial coverage only) |
| `_diff.py` | 188 | the diff primitive |
| `_xpath.py` | 46 | the selector primitive |

~3,258 lines. **Highest-value review in the repository.** Every Three-Values answer,
every Tier B verdict and every provenance claim rests on these six. All shipped since
v2.5.0, so none is a release blocker -- and all are load-bearing for correctness.

### Tier 2 — INSTRUMENTS. A defect means a gate cannot go red.

`gates/` (31 files, ~7,146 lines), `scripts/run-gates.sh`, `verify-cold.sh`,
`fuzz-guard.py`. Review the **contract**, not the logic: for each gate ask *what,
concretely, would make this go red?* (#26). MEASURED precedent -- round 5's B5 found a
FALSE OK that could not fail the run; round 6 found `qa_sweep` accepting
`expect=(0,1,3)` and `--sel-only` declared but never read. Both are cells with no
reachable failing branch.

### Tier 3 — FRESHNESS / PROVENANCE. A defect makes a stale artifact read as current.

`_freshness.py` (678, partial), `_provenance.py` (119).

### Tier 4 — REPORTING CLIs. A defect produces visibly wrong output.

`_effectivecli.py`, `_debugcli.py`, `_savecli.py`, `_diffcli.py`, `_changed.py`,
`_threeway.py`, `_debuglog.py`. Lower priority **because a wrong number on screen is
seen, while a wrong fact in the store is not.**

---

## Explicitly OUT of scope for human review

| | lines | why, and what covers it instead |
|---|---|---|
| `tests/` | 23,796 | **Not read. Swept MECHANICALLY.** A test that does not assert is exactly a tool declaring something factual when it is not -- so the risk is real, but it is a SHAPE, and shapes are better caught by an AST scan over all 107 files than by reading any of them. Sweeps: bare `return` as a precondition bail (round 5's B6 -- 3 tests reported PASSED without asserting), an assertion satisfied by a COMMENT or docstring (round 5's B7), `assert` on a substring where a structure is meant, and a mutation that leaves the named test green. `tests/test_no_bare_return_in_a_test.py` is the pattern to extend. |
| `tools/basex/` | 3,151 | Discovery, not proof -- its answers are never quoted as facts without a denominator. **Exception: `ask.py` is Tier 2**, since refusing a zero-result without coverage is the standard the rest of the repo is measured against. |
| `_nexus.py` | 240 | External API client. A defect yields a failed lookup, not a wrong local fact. |
| vendored `BaseX.jar` | -- | Third-party binary, BSD-3-Clause. Not ours to review. |

---

## The rule that makes this converge

**Record every finding with its ORIGIN, measured, not assumed:**

    git blame -L <line>,<line> --porcelain -- <file>

and bucket it IN-ARC or PRE-ARC against an explicit `<tag>..HEAD` range. Never
`--since`: it filters on COMMITTER date while `--date=short` prints AUTHOR date, which
silently drops commits and mis-reports a file as untouched (#37).

A review that does not separate the two will keep re-finding the repository's whole
history at every release, and will read as "the fixes are causing new bugs" when the
measured answer is the opposite.

### ★ The third bucket: a PRE-ARC defect this release RECRUITS FOR

IN-ARC and PRE-ARC are not exhaustive, and the gap between them shipped once already.
**A defect can be old while the thing that makes users REACH it is new** — and those two
halves blame to different commits, so a review that blames only the defect will bucket
the whole finding PRE-ARC and wave it through.

> **The case (2026-09-07, v3.1.0).** `install.sh` cannot run over an install that
> `scripts/x4lock.py` has locked: it has no `trap`, so a failure between the backup and
> the restore skips the restore, and it dies on the first item with
> `cp: … Permission denied`. Blamed correctly to `2d4582b`, an ancestor of v3.0.0, with
> `x4lock` itself shipping IN v3.0.0. PRE-ARC, therefore not a gate, therefore ship it.
>
> **MEASURED, and it inverts the conclusion: `git show v3.0.0:README.md | grep -ci x4lock`
> is ZERO.** The line telling users to lock their files was added in the release under
> review, hours before. v3.1.0 was not merely the first upgrade over a locked install —
> it was the first release that INSTRUCTS people into the state that breaks the upgrade.
> The defect is old; its blast radius is new, and the commit that widened it is in-arc.

**So ask two questions of every PRE-ARC finding, not one:**

1. When was the DEFECT introduced? (`git blame` on the broken line.)
2. When was the thing that makes users ENCOUNTER it introduced? A new doc line, a new
   default, a new install step, a newly-advertised feature, a new code path that reaches
   old code. `git show <tag>:<file>` on the DOCS, not only on the source.

If (1) is pre-arc and (2) is in-arc, the finding is **in-arc for triage purposes** even
though not one line of the defective code changed. Fix the half that is yours — usually
the recruitment, which is usually cheap — and say plainly which half you left.

⚠ **The reviewer who found this had the right defect and the wrong baseline**, reading
the README from the WORKING TREE and treating it as shipped state. That is #34's family
one level out: not "what an instrument returned" standing in for a fact, but "what the
repo says today" standing in for "what the last release said". `git show <tag>:<path>`
is the whole remedy and it costs nothing.
