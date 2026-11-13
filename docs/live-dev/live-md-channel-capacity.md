# Local MD-channel deadline capacity

The MD channel retains at most 64 scheduled deadline callbacks. Each accepted
send schedules one callback at getElapsedTime()+10.25. Reading or clearing a
reply does not retire that callback. The scheduler retires it once; disposal
does not pretend it has already fired. The limit and interval are shared
constants used by the actual send path and the readout.

The read-only capacity() method copies this lexical count and its limit without
a native call, clock read, session binding, scheduling, callback, reset or cleanup.
It also exposes disposal/fault state, stamp_bound and tick_armed.
lifecycle_open means only not disposed/not faulted. It is not send readiness,
available timer space, a valid current public stamp or evidence that native MD
loaded. A fault remains a fault after timers retire.

The fixed bound read md_channel_capacity takes no arguments for the complete
attached extension catalog, or one registered extension ID for that extension's
owned channel counters. Provider IDs and arbitrary channel/native names are not
selectors. The catalog is the helper's attached UI descriptors, not the game's
extension census or proof their MD scripts loaded. A known extension can own
zero MD channels; the explicit denominator distinguishes this from unknown.

Channels are recorded immediately after their disposer is registered. Fixed
construction/cleanup bounds permit at most 16 channels per descriptor; at most 16
descriptors are accepted. Selected responses are encoded through the actual
bound capture/paging path with a32000-byte cap. An unavailable getter refuses;
it does not report zero. The legacy read namespace cannot replace this verb.

Counts belong to the current helper/channel instance. MD-world operation counts
are a different layer and can survive UI reconstruction. The deadline interval
is in the native UI clock's units; do not infer wall-clock timing, mix it with MD
player age, or assume elapsed time retired callbacks. Sample actual state again.
A stage's declared burst/rate policy must respect remaining slots. This read
does not reserve them or authorize sends, retries, channel resets or UI reloads.

Source confidence 98% rests on reading the existing lexical counter and preserving
the same 64/10.25 send/scheduler behavior. Offline tests use actual channel LuaJIT
state plus the generated bundle/router/codec/host capture path with synthetic
native boundaries. They are not runtime qualification.

Qualification:214 affected controls passed with zero skips. This covers the actual channel, generated bundle/router/codec/host
capture path, both-profile nine-loader lifecycle controls and existing bundle/
composition controls. Independent source review found no blocker. An initial
run's ten failures came from a missing globals table in the synthetic extension
fixture; only that test setup was corrected before the passing run. No native MD
XML or scheduler/send behavior changed in this unit.

