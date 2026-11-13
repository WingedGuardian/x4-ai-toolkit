# Session-bound read capture

Offline qualification, 2026-11-09. No deployed helper or game was used.

The host captures each whitelisted read once and retrieves its immutable UTF-8 bytes through bounded pages. This permits existing equipment responses up to 32,000 bytes without exceeding the structured action wire's 4,096-byte string limit. The original OK, ABSENT or ERR status survives reassembly. Transport completeness never implies a complete object census or a successful gameplay test.

The router retains one capture, at most 32,000 bytes, and a bounded history of 4,096 non-reusable capture nonces per UI instance. A new capture supersedes the previous payload. Pages expire after 300 simulation seconds and remain bound to the same public session, UI generation, bundle and source interval. Each page holds at most 1,000 source bytes encoded as hex. The host requires ordered, matching pages and exact byte counts, rejects invalid UTF-8, and hashes the reassembled bytes locally. That hash is not an engine signature. Captures are explicitly non-atomic.

Independent source review found a clock rollback gap between the source's finish timestamp and the enclosing reply. The router now rejects that rollback; the host also checks the finish against the qualified envelope age. The 10-to-20-to-15 counterexample is covered.

MEASURED: resource-gated regression command covering test_livereadpages.py, test_livebound.py, test_liveautomation.py and test_livecli.py passed 236 controls with one skip (2.34 seconds, receipt 93de6048510d). The skipped legacy archive test needs X4_MODS ground-truth data; it is not a pass. The first invocation named a nonexistent test file and ran no tests; the corrected invocation above is the evidence.

Controls include empty and 32,000-byte captures, UTF-8 page boundaries, one source invocation, preserved error statuses, changing live source after capture, mixed/missing/duplicate pages, superseded and expired captures, byte-count lies and clock rollback. Native transport timing and complete in-game workflow remain unverified.
