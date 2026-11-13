# Verified handshake exchange records

`SessionLink.handshake_receipt()` returns a copied record of the last successfully verified fresh/resume exchange. It adds no native query and changes neither session allocation nor readiness rules. A new probe/connection attempt, peer/journal failure, or rejected native identity invalidates access to that record.

The record includes the matched request fields, exact parsed native reply, checked process peer before/after, and the qualified host identity. The native reply's age is **MD player age**. Host load generation, UI generation, and process FILETIME retain their existing meanings; these are not interchangeable clocks.

This is trusted transport callback evidence, not cryptographic authentication or a machine evidence tier. A synthetic callback still produces synthetic evidence. Calling the accessor checks the current host peer/journal only: it cannot detect a native load that has not yet been observed through the current-state protocol. World lineage remains UNKNOWN.

The actual MD producer in `scripts/live_session_md.py` emits:
`X4TKSESSION HANDSHAKE mode=… kind=… session=… generation=… challenge=… age=…`.
Its separate LOAD marker carries the provider revision. The handshake marker does not carry configuration, helper build, process identity, or save lineage. A downstream log correlation must match the exact retained request challenge and identity, account for duplicate/invalidation markers, and preserve log provenance. Native formatted-age precision has not been runtime qualified; do not invent an age tolerance or compare MD age with UI/envelope clocks.

Offline controls use synthetic transports and inspect the actual pure MD generator. They do not execute MD, connect to X4, or establish native runtime correctness.
