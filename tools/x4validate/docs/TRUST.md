# Why you can trust this toolkit — and exactly where you cannot

This is the honest answer to *"how do I know these tools are telling me the truth?"*

It is not a claim that the toolkit is bug-free. Over a hundred defects have been found in
it so far, and the sixtieth exists. All but one were caught by the toolkit or its own
gates rather than by a user getting a wrong answer — the exception is **F58**, and it is
named here rather than rounded away: a correct, documented capability went unused, a
hand-rolled substitute produced a confidently wrong answer, and that answer reached a
planning document before anyone re-derived it. No tool misbehaved; the routing did. The claim is narrower and checkable:

> **Every defect SHAPE that has occurred here has a test or gate that mechanically
> bans its recurrence; every answer that could be wrong carries a denominator and a
> freshness stamp; and where the engine can arbitrate, it does.**

An "audit found nothing" is not evidence — that criterion resets every time someone
looks harder. The table below is the evidence, and each row cites the file that
enforces it, so *"is this shape guarded?"* is a lookup rather than a feeling.

---

## The defect register

| # | Defect shape | MEASURED cost when it happened | What bans it now |
|---|---|---|---|
| 1 | **Enumeration narrowing** — a walk that sees only part of the corpus (loose files only, so packed mods and the two packed mini-DLC are invisible) | BaseX `x4eff` held **23 of 142** mini-DLC documents (16%) and reported success. The same shape was written **7 times** in different files | `tests/test_no_loose_only_reference_walk.py`, `tests/test_no_packed_only_scan.py`; one shared helper (`_scan.iter_mod_xml`, `_effective.base_vpaths`) that every caller must use. **Recurred 2026-08-24 inside the test runner itself**: the guard against orphaned BaseX tests pinned a *shrinking* file list but not a growing one, so 22 newly added tests were collected by nothing and nothing said so. A guard whose denominator comes from its own hand-maintained list can only ever be as complete as that list; it now discovers the files on disk |
| 2 | **Wrong population** — the check covers a different set than the finding | A `modulegroups` census reported **200** groups from 9 sources; the answer was **146** from 5, because `reference/` and `extensions/ego_dlc_*` are the same content counted twice | `gates/tool_properties.py`; the routing table in `CLAUDE.md`; `_scan.iter_corpus_xml` excludes `ego_dlc_*` by construction |
| 3 | **Wrong mod scope** — "installed on disk" used where "what the engine loads" was meant | One disabled mod put 3 macros into the effective index as live, named it in 4 collision rows, and let **Tier B resolve a selector against it and report OK** — a false pass in the mode built to catch silent no-ops | `tests/test_mod_scope_is_explicit.py` (scope must be a **literal**), `_registry.mods(scope)` with scope positional and required |
| 4 | **A stale artifact answering for a world that moved** | BaseX `x4eff` served pre-merge-fix values for **11 days**; on rebuild **140 of 194** engine-thrust rows changed, with no input file altered | `_freshness.py` two-axis fingerprint (content + engine bytes); `ask.py` and `gates/claims_audit.py` **refuse** rather than answer from a stale store. The content axis once hashed only each mod's own `content.xml`, so enabling or disabling a mod in the **profile** could not move it (F43); it now folds in each mod's EFFECTIVE enabled state (the profile entry, else the manifest default), so a toggle moves the fingerprint (`tests/test_freshness_vector.py::test_a_profile_ENABLE_TOGGLE_moves_the_content_axis`). Additions and removals move it too (positive control: 3 deployments, 123→126 folders, hash moved) |
| 5 | **A silent no-op reported as applied** | A bare `continue` dropped **858** root-`<replace>` operations while marking them applied, so the effective store served vanilla values for real mod overrides | `AppliedOp.ok` must carry a reason; `gates/noop_audit.py` re-proves it corpus-wide |
| 6 | **A zero rendered as a finding** — "nothing found" reported without saying whether anything was looked at, or a verdict that ignores evidence the same tool printed | A corpus sweep threw on **all 4,391 files**, swallowed it, and reported *"0 dangling across 115 mods"* when the answer was 3 | `CorpusScan.verdict()` **raises** rather than render a zero over an empty population; `ask.py` requires a coverage denominator before printing a negative. ⚠ **A second form of this was found 2026-08-25 and is worth knowing:** the corpus sweep's crash detector looked only for a Python traceback, so **173 of 242 runs (71.5%) killed by the OS loader before Python started** were counted as clean — while the gate's own output printed `exit 3221225794: 121` four lines above `CRASHES/HANGS: 0`. The evidence was not hidden; the verdict simply never consulted it. It now classifies on the exit code, and the corroborating tell is recorded too: that run took 193s where a healthy one takes 1219s, and **a gate that suddenly gets much faster is reporting on itself** |
| 7 | **The checker was wrong, not the code** | **83 of 83** checking-step errors were the instrument, not the finding — that is this repository's own count across twelve sessions; the wider workspace register that also covers shell and OS instruments (`ls`, `awk`, `comm`, `git status` on Windows) stands higher, and the two are deliberately different populations rather than a disagreement — most recently a probe for *"what does `attr` do with an unknown KIND?"* that used `ship`, which is a real stored kind (514 entities) — it returned the predicted confident zero for the wrong reason entirely (an unknown PROP), and only `ls ship` returning 514 rows separated the two defects; before that a line-ending pin verified by reading the RULE (`.gitattributes`, `git check-attr`) and never counting a `\r\n` in the bytes the hash actually reads, which left the engine hash machine-local for two days (**F67**) — most recently a gate reporting a **814× performance regression** that was the machine having been asleep, and a TSV reader that took a comment line as its header and reported "0 problems" over an **empty population**. Earlier ones: `$?` after a pipeline, a text splitter arbitrating a structured format, an AST scan under the wrong interpreter reporting a file "unparseable", a "cold" verification that was warm because path resolution walks up to a config file, and a unit test whose verdict came from a mutable artifact outside its fixture — most recently, and it is the cleanest specimen in the row: a doc edit verified against a hand-rolled backup that **was a week old**, because the `cp` that should have made it lived in a command bash rejected at PARSE time, so none of it ran. The diff reported *2547 lines added* against a true answer of **52 insertions, 1 deletion**. Nothing was wrong with the file. **For a TRACKED file, git is the baseline** — `git diff --numstat` needs no backup, cannot be stale, and cannot be another session's (F76) — and the 2026-08-29 hook work added seven at once, of which two are worth naming: a false-positive rate measured as **300 of 300 allow** because the replay environment left the paths unconfigured, so the rules being measured *could not fire* (a vacuous 100%, caught only by adding a positive control that had to produce a deny before any rate was quoted); and a rule that flagged `uv run --frozen python -m pytest` as a bare interpreter call because its lookbehind only inspected the seven characters before `python` — **52 hits, of which the true count was 2**, found by SAMPLING the matches rather than trusting the total | Planted-truth gates (`gates/diff_truth.py`, `gates/oracle*.py`) — the checker is validated against known answers; every AST guard **raises** on a parse failure instead of skipping; `scripts/verify-cold.sh` **refuses to run until it has proven the environment is unconfigured**. ⚠ **2026-08-29 — this row's claim was briefly FALSE, and the reason is worth more than the instance.** A coverage checker keyed on `argv[0]`; that broke for a single-command CLI, was fixed, given falsification twins, and mutation-tested per clause — then recurred immediately on a THIRD argv shape (a global flag before the subcommand), publishing a coverage figure of 17 where the answer is 13. **Watching a test fail proves the feature is absent; mutating the finished code proves the test is present; neither proves the ENUMERATION of cases is complete**, and no number of twins of the same kind closes that. The fix is therefore a guard on the CLASS: the subcommand is searched for against the LIVE surface, and a cell where none can be found is recorded as UNDECIDABLE — so a FOURTH shape surfaces as a finding instead of silently shrinking the covered set (F75) |
| 8 | **An aggregate hiding a per-item regression** | Two mods went **2.8 s → 112 s** and **2.4 s → 121 s** while the total moved **1.00×**, because a third got faster | `gates/perf_guard.py` compares **items, never totals**. ⚠ **The same gate had the opposite defect until 2026-08-25**, and it is worth knowing about: its clock advances while the machine is *suspended*, so a sweep left running overnight reported a **814× regression** on a mod that re-timed at **3.47 s**. A timing that spans a suspend is a non-answer, not a finding. A suspected regression is now **re-timed once** and reported only if it reproduces; one that cannot be re-timed is reported UNCONFIRMED and still fails, because "could not check" is not "not a regression" |
| 9 | **Two doors to one question** — the same thing resolved by two independent code paths | Tier B and `x4effective` gave contradictory answers about the same value, each internally consistent | One implementation per question; `gates/cross_tool.py` asserts tools agree |
| 10 | **An executable that resolves its own environment** — instead of delegating to the one resolver | A fingerprint script defaulted to a developer's absolute path: on any other machine it fingerprinted nothing and reported **FRESH forever**. A corpus build did the same and would index **zero documents, exit 0** | `tests/test_env_resolution_is_delegated.py` (AST, with a scoped escape hatch); `scripts/verify-cold.sh` runs all 10 CLIs on a genuinely cold checkout. **This row overclaimed until 2026-08-24**: the guard detected only `os.environ`/`os.getenv`, so a hardcoded absolute-path *literal* — the form this very defect shipped in — was invisible to it, and one survived in `tools/basex/stage.py`. It now covers literals too, and three further scripts that returned **rc 1 ("has findings") instead of rc 2 ("not configured")** were found by running them cold, not by reading them |
| 11 | **Failing late, and blaming the wrong component** — the error a user actually sees names something other than the cause | Reproduced 2026-08-24: with no JVM installed, the corpus tool reported `BaseX query failed: [WinError 2]` — blaming BaseX for a missing Java. With no index built it printed a raw resource error and **never named the build script**, because the one line that does sits on a code path an unbuilt index can never reach. The build script spent its entire multi-minute staging pass before reaching the check that would fail | One shared `preflight.py`, called by all three entry points, that checks the JVM (floor read from the shipped jar's own bytecode level, not chosen), the jar, the index, `uv` and free disk **before** any long work, and refuses with **exit 2**. 24 tests exercise every check in **both** directions, including that an unrecognised `java -version` banner refuses rather than assuming a pass. ⚠ **The same row's defect recurred inside the fix, and was caught before shipping (2026-08-25):** the preflight reported *“the database has not been built”* over a database that was built and queryable, because the vendoring left out a 0-byte upstream marker and BaseX had silently relocated its home. A first run would have been *build → told to build*, forever. The check now looks for the marker and names the relocation |
| 12 | **A capability that exists, is correct, and is never reached** — the failure is routing, not code | A gap was filed against a command that already answered it. A throwaway script hand-rolled a base-only substitute and labelled **65 of 241 vpaths (27%)** as renamed-and-deletable when they were mod-supplied and actually WIN those paths. MEASURED alongside it: **21 of 30** CLI subcommands (70%) appear in no routing surface | A row in `CLAUDE.md`'s Discovery-vs-Proof table — the only surface always in context. Deliberately **not** a new helper: the one added for this exact trap the day before had **zero callers**, and deliberately **not** a lint, because the failure was in a throwaway script and no linter covers those (F58) |
| 13 | **A tool that breaks the tree on purpose, and cannot put it back if it dies** | The mutation gate restored its target in a `finally:`, which does not run on SIGKILL — so a killed run left a deliberately-broken TRACKED file that `git status` shows as an ordinary modification. **v2.5.0 shipped that way once**: ambiguous-`sel` detection silently disabled in a public release. REPRODUCED by a real kill, not simulated | Pristine copies taken before the first mutation plus a `.mutation-probe-active` marker, so recovery never depends on the thing that failed. A killed run makes the next invocation **refuse with exit 2** and name `--recover`, which restores and says WHICH file was poisoned. A second party can see the marker without asking — **a control only one side can see is still an assurance** (F59) |
| 14 | **A copy that is not the thing it copies** — and a proof that reports non-findings | Porting dev to the public mirror one FILE at a time has no notion of a COMMIT. A five-file change set crossed over in part: two files, and only because they had independently been edited by the porter. Public `master` then shipped a stale-artifact message instructing users to run a subcommand the public build does not have — nothing crashed, so nothing noticed. The documented proof was `diff -rq`, which on these two trees reports **52 differences where 12 are real**; the other 40 are line endings, and both repos store identical bytes. A proof that buries findings under non-findings teaches you to skip it | `scripts/verify-port.py` classifies every tracked file into buckets that must **sum to the population**, compares **committed blobs** rather than working-tree files, treats an unlisted dev-only file as a FINDING rather than a default, and runs the identifier matcher over the port subset before anything is copied. `--selftest` proves it can fail on four planted defects (F60, F61, F62). ⚠ **Retired 2026-09-14 with the second repository:** one repository has no port for this shape to arise from, so the script went with it (it is in the dev archive). The instrument lesson stands and has other homes: `gates/lockstep.py` compares committed blobs in one command, and `scripts/verify-cold.sh` runs the suite over committed objects, never the working tree: a fresh clone (the contributor's population, with Git metadata) and, with `--archive`, a `git archive` extract with no repository visible (the release zip's population; F262 had dropped that leg, FX-H2 restored it) |

---

## What the guarantees actually are

**A negative answer carries its denominator, or it is refused.** "Nothing references X"
is only printed when the tool can say *over how many documents* it looked and name
every exclusion. A bare zero is a lead, never a fact.

**A stored answer says when it was true.** Every persisted artifact carries a two-axis
fingerprint: the installed content, and a hash of the merge/enumeration engine's own
bytes. A merge fix changes the right answer for identical inputs — so the artifact
goes stale and the tools say so, rather than serving the old answer confidently.

**The engine axis hashes source BYTES, so a comment change marks artifacts stale.**
That is deliberate, and worth stating because it looks like a fault the first time
it happens: demonstrated 2026-08-24, when a two-line comment edit moved the engine
fingerprint and every derived artifact reported STALE with nothing semantic
changed. The alternative — deciding which edits "really" change behaviour — needs
an equivalence oracle nobody has, and would fail in the one direction that
matters, by calling a real change cosmetic. A spurious rebuild costs minutes; a
missed one silently serves wrong values, as it did for eleven days. Rebuild after
editing the merge sources, even if you only touched a comment.

**And the cry-wolf cost is now measured, which is what settles the argument.**
MEASURED 2026-08-26: a two-line addition to `_effective.py` (an import and one
guard call, both provably unreachable from extraction or merge) moved the engine
hash and staled every derived artifact. A parallel session had **21 published
figures** stamped with the superseded hash. Re-deriving all 21 against the rebuilt
store took **one script and about a minute**, and **21 of 21 reproduced exactly**.
So the false alarm cost a minute; the eleven-day false FRESH cost a design decision
written on vanilla values mistaken for a mod's. **A conservative hash that
occasionally cries wolf is cheap precisely because confirming the wolf is absent is
cheap** — which is the argument against ever softening this into a
"did-it-really-matter" heuristic. Note also where the value showed up: not in the
tool that changed, but in a different consumer having to ask whether its own
published numbers were still true.

**Enabling or disabling a mod moves it too.** The content axis reads each installed mod's
own manifest AND the profile's decision for it (the profile entry decides; the manifest's
`enabled` is only the default), so installing, removing or toggling a mod each moves the
fingerprint. That was a measured hole once (F43: two logs 9 h apart, one fingerprint, 66 vs
67 mods loaded) and is now pinned by a test.

**A guess never wears the grammar of a measurement.** Provenance travels with the
value; nothing derived from a guess is promoted into a confident state.

**An unconfigured toolkit refuses.** Every CLI exits **2** — never 0 with a
plausible-looking answer computed from a directory that does not exist, and never 1,
which means "the thing you asked about has findings".

**The engine is the referee where it can be.** `x4debug crosscheck` compares what the
game actually skipped against what the validator predicted, per item. It found 1,206
false positives in our own tool — the half of the comparison Egosoft wrote cannot be
fooled by our assumptions.

---

## Where you cannot trust it (stated, not hidden)

- **Load order between mods is MEASURED, not documented.** It is the engine's own
  signature-check order (0 of 5,134 ordered pairs inverted, BLIND-SPOTS F128), and the
  in-game load-order probe (2026-09-26) MEASURED that it is also the patch-APPLY order and
  settled cycles, missing dependencies, non-ASCII names and DLC position. The profile's
  `extensions\` root is measured too: it applies after the game root, and a dependency does
  not cross between the two. The Steam Workshop root and non-NTFS filesystems are unobserved.
- **Every finding is recorded individually** — the register runs past F140 (derive the
  current end with `scripts/next-blind-spot-id.py`), each with a measured cost, and
  where a limit was accepted rather than fixed, the reason it was accepted. The
  per-finding register (`docs/BLIND-SPOTS.md`) ships with the package since 2026-09-13;
  the findings fixed in each release are summarised in `CHANGELOG.md`, and the shapes
  are the table above.
- **A clean validate is necessary, not sufficient.** It does not replace an in-game
  test and a `debug.txt` read. The reference/completeness catalog is partial.
- **BaseX indexes are discovery instruments.** Use the effective store for claims
  about live values.
- **Windows is the tested platform, and on Linux there is a specific unfixed gap —
  do not read a green Linux test run as a Linux guarantee.** X4's own filesystem
  folds case; NTFS does too, so the toolkit never had to. Four lookups compare
  paths case-sensitively, and on a case-sensitive filesystem a mod shipping `MD/`
  or `AIScripts/` resolves to nothing. **One of the four fails silently** — the
  subtree simply contributes zero files and nothing says so, which by this
  document's own standard is worse than the three that fail loudly. Note the
  inconsistency this produces: packed archives ARE read case-insensitively, so on
  Linux a packed mod resolves while its loose twin does not.
  Linux CI runs and is expected to pass, but it is **informational, not gating**,
  precisely because passing tests do not close that gap. It will be promoted to a
  gate when the case-folding work lands, not before.

## How to check any of this yourself

```bash
uv run python -m pytest -q          # the suite
bash scripts/verify-cold.sh         # behaves on a machine with no X4 installed
uv run python gates/<name>.py       # any single gate; see gates/README.md
```

Every gate prints its denominator. If one prints a finding without one, that is a bug
in the gate, and it is the kind we most want reported.
