# Native persistence dispatch and scoped load correspondence

This unit supplies a fixed host dispatcher, a Lua gate around the existing native
persistence module, and an observation join for one own save/load transition.
It does not launch X4, change profiles, select a desktop or automatically retry.

The native call provenance remains in live-persistence-qualification.md:
SaveGame, the complete standard-slot catalogue, IsSaveValid and the native
deferred LoadGame callback. The gate does not replace those checks. It supplies
correlated host reservation, authorization and settled-file records to their
existing callbacks. Loaded modules alone do not confer permission.

## Trusted entrypoints

PersistenceDispatcher requires a real SlotVerifier, a PersistenceAuthority and a
trusted fixed port callable. The port must route only the operations listed by
live_persistencegate.invoke through the qualified world-bound transport, verify
the actual public native stamp and supply the full measured SessionLink identity
around the result. Arbitrary caller-supplied identity JSON is not a port.

The Lua factory receives the native persistence factory and its native/current
dependencies. Its grant records are local to that instance and bound to the
canonical public stamp. Each ticket and authorization nonce is consumed once.
The host must retain sole ownership of this channel; this is a same-user trusted
dispatcher boundary, not a sandbox against a program bypassing the dispatcher.

The production SDK/provider registration and host port are implemented separately
by live_persistence_extension and PersistencePort; see
live-persistence-extension-qualification.md. The generated loader still requires
reviewed deployment. No registration or runtime success is inferred from import.

## Two distinct authority outcomes

The agent-side decision callback receives the exact named profile, operation,
slot, label, source evidence hash, complete preflight inventory hash and current
identity. It returns AGENT_RECORDED_CONVERSATION with a real conversation
reference and the exact context digest. This is policy evidence supplied by a
trusted agent, not cryptographic proof of a human statement. No TTY is required;
packet booleans, generic continue, development approval and CLI flags cannot
construct the in-process capability.

CANONICAL_GUARD additionally requires a source-qualified complete native effect
scope and an actual non-preview canonical guard evaluation. The exact Action v1,
reply, warnings and permanent-preservation transactions are retained. Ask, deny,
unevaluated, inert, malformed and transport-failed results refuse dispatch.
Only this outcome sets guard_qualified=true.

EXPLICIT_NAMED_PROFILE_NATIVE_EFFECT_EXCEPTION requires that specific policy
exception in the trusted conversation decision. It is never guard-qualified and
does not claim unknown engine temporary/backup filenames were checked. Current
source research proves the native slot and label but not every filesystem effect.
The canonical guard rejects unresolved tree effects and whole-save-folder writes;
this module neither changes that guard nor substitutes an editor-shaped claim of
complete engine effects. No such user exception is established by this document.

The task journal stores opaque issuance and irreversible consumption before
native dispatch. The capability is not reconstructible from serialized evidence.
The verifier rechecks existing related files, catalogue and identity after the
policy callback and before consumption. This is an observation-based interlock,
not an exclusive filesystem lock against another process.

## Save and load evidence

reserve_save requires an actually empty standard slot in the native catalogue
and complete physical save frame. execute preserves the pre-dispatch context,
consumes once, and records possible action before calling the port. A lost or
malformed response leaves UNKNOWN and cannot trigger automatic resend.

settle joins the verifier's complete XML/XML.GZ parsing, stable hash and native
catalogue identity with the same native save ticket. Existing save bytes and
membership must remain unchanged. A file appearing alone is not sufficient.
The observer's original causal_engine_completion=false label is retained.

prepare_load accepts only this dispatcher's own settled save and calls
SlotVerifier.receipt(recheck=True) while the OLD world identity is current.
Load requires a separately recorded applicable operation decision and another
full source recheck. Native deferred scheduling is not invocation or completion.
acknowledge_load requires the old-world native status LOAD_REQUESTED. If loading
disconnects before that acknowledgement is observed, the transition join remains
unqualified; it cannot infer invocation from a lost pipe.

observe_loaded requires an actual new SessionLink and a fresh native current-stamp
observation, the same process and public revision, exactly the next load generation,
a different session and the same nonempty MD world token. The exact own save bytes,
catalogue evidence, old dispatch acknowledgement and whole save frame are retained
and checked again. It produces only OWN_SAVE_LOAD_TRANSITION_OBSERVED for
ONE_OWN_SETTLED_SAVE_AND_BOUND_LOAD. It never changes SessionLink.world_lineage,
which remains UNKNOWN. The native engine has not exposed a loaded-file digest:
engine_loaded_file_digest_measured=false is explicit. Same token alone, skipped
generations, an unobserved LoadGame, a changed file or a different process cannot
qualify this scoped join.

The short deferred native callback can recheck native context and the one-use
grant, but cannot synchronously perform a new host filesystem scan over the pipe.
The host's last file observation is immediately before dispatch; this race window
is explicit and no exclusive lock is claimed.

## Offline tests and remaining boundaries

Tests use the actual Lua gate/native module, actual SlotVerifier parsing of
synthetic temporary saves, the host dispatcher and actual SessionLink/SessionJournal
against synthetic native observations. No user's save, game or real guard action
is used. They cover authority distinctions, malformed guard replies, incomplete
scope, policy-time drift, durable no-replay, deferred versus invoked load, false
completion claims and scoped transition counterexamples.

Real native SaveGame/LoadGame, deployed port/provider runtime, the actual
complete save-effect scope (or specific approved exception), and runtime
save/load correspondence still require their own evidence.

Offline result: 44 controls passed, zero skipped, against the actual Lua gate and
native adapter with synthetic native calls and temporary test saves. The first
run exposed a test-fixture omission (its required task journal directories had
not been created); after that fixture correction, 38 passed, followed by six
additional complete-context refusal controls in the final 44-control run.
Independent review then identified an observed inventory change during authority
consumption that was being adopted as the new baseline. The dispatcher now
compares that freshly read frame with the approved context hash before sending;
a mismatch stays consumed and cannot retry. All 45 controls passed after this
correction, including the targeted consumption-time drift case.
