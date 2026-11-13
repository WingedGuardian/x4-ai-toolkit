# Fixed installed-provider MD channel

The channel reuses the source-backed public bootstrap queue pattern: native RegisterEvent, GetNPCBlackboard/SetNPCBlackboard, AddUITriggeredEvent and Helper delayed callbacks. Installed provider code declares a fixed screen, event, queue and at most eight controls. Host packets cannot select native symbols or arbitrary blackboards. This is an integration dependency for the fixture and visual-capture providers, not a sandbox for untrusted installed code.

Construction performs no native query, subscription or send. Subscription events are ignored before a public binding exists; retired callbacks never consume a replacement queue. Every queue operation checks the canonical public session and exact converted player identity. A clear requires a preceding read and the same player. The native queue read/clear sequence is not an atomic snapshot claim.

A send schedules its 10.25-second UI deadline before the native event, with input blocking disabled. At most64 deadlines may remain pending. Each deadline callback is consumed once; uncertain sends and protocol failures are sticky and never automatically retried. Disposal makes queued callbacks inert. The provider supplies its own timeout/status handling.

Component conversion is observation only and never issues an action-capable helper ID. An unreadable component can be recorded UNKNOWN by the provider without retiring the queue; canonical-session failure still retires it. The public-current dependency is the already validated public session API, not caller JSON.

MEASURED:25 actual-LuaJIT controls passed, zero skips, resource76a3dd9f5169 (0.11 seconds). Independent source review is clear. Controls cover exact and unsafe IDs, changed player between read/clear, old session, early/retired events, one deadline, duplicate callbacks,64-deadline capacity, uncertain sends, immutable configured names and local unreadable observations. Native functions are synthetic callbacks; no engine, profile or deployed file was accessed. Native queue/event timing remains runtime-unverified.
