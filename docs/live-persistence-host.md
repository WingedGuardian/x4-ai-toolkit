# Save-slot observation and settlement verifier

Source-prepared infrastructure; integration and runtime are unqualified. This
module never calls SaveGame/LoadGame, changes a save, creates a profile placeholder
or issues approval. It reads a configured profile and writes only exclusive task
journal evidence. An opaque observation handle cannot authorize a game action.

## Bound inputs and evidence

SlotVerifier requires existing absolute canonical profile/save and journal
directories. Journal and profile cannot contain each other. All ancestors and
members reject symlinks/reparse points, hardlinked files and case-alias collisions.
Directory identities are retained. These are before/after consistency checks,
not an OS sandbox or an atomic filesystem lock; concurrent replacement remains a
race that must be addressed by the eventual dispatcher.

Three trusted callbacks are required:
- identity(): actual qualified SessionLink identity, including process peer and
  public session/UI/world fields.
- profile_peer(): actual ProfilePeer verification, returned as peer plus its
  numeric profile_id. It must match both identity.peer and the canonical profile
  directory basename. A caller-supplied profile string is not process evidence.
- catalogue(): the complete current live_persistence.observe result transported
  through the qualified bound channel. Its exact native stamp must match identity.
  Native count must equal the row denominator; only exact empty {} with count=0
  is normalized to an empty list after actual Lua JSON encoding.

No method accepts caller JSON as an authenticated observation or approval.
Dependency injection permits synthetic tests; production callbacks remain to be
wired and qualified. Trusted identity refresh must preserve the existing live
channel checks; an unchanged PID alone does not prove an unchanged world.
The configured canonical profile path is still a trusted integration input:
matching its basename to a measured profile ID does not discover the engine's
default personal-directory location independently.

## API

reserve(request_id32hex, fixedManualSlot) reads the full bounded save directory
frame and native catalogue. Both native slot/unique label and all physical related
members must be absent. Related members include case variants, backups, temporary
suffixes and nested copies whose basename begins with the slot. Unknown variants
refuse instead of being ignored. Fixed manual slots are save_001 through save_010.

It writes an exclusive per-slot claim and request evidence in the task journal
using the existing flushed _liveactions._write_new helper. Claims are not silently
reused after a restart or partial write. They coordinate only that journal; they
are not system-wide save locks. No file is created under the profile.

The returned Python object is stored by object identity in that verifier instance.
Serialized descriptions, caller booleans, foreign handles and a fresh verifier
cannot recreate it. preflight(handle) repeats identity/profile, native catalogue,
journal and full physical preservation checks. Its result explicitly says
EMPTY_SLOT_OBSERVED_NOT_AUTHORIZATION, approved=false, guard_qualified=false and
exclusive_lock=false.

observe_settlement(handle) is bounded, caller-paced read-only polling. It accepts
only one new exact root save_SLOT.xml or .xml.gz representation; every existing
file hash/stat and directory identity/membership must be unchanged. A new file's
zero bytes, absent native identity or insufficient stability time remain pending.
Unexpected members, old-save drift, invalid native rows or corrupt stable content
refuse and fault that reservation.

After two identical physical/native observations separated by the configured
interval (default two seconds), the verifier parses the complete XML stream. Gzip
must reach its checked end; DTD/entity declarations refuse, expanded bytes/depth
are bounded, and exactly one savegame/info/save label and one info/game id=X4 must
match. It rereads the complete physical frame and native identity afterward.
Parser structure follows the streaming Expat/no-DTD mechanism already used by
_savecli.extract_refs; this verifier additionally checks the unique save header
and declared byte/depth limits.

The exclusive settled evidence journal produces a second opaque handle.
receipt(handle) gives historical observed evidence; receipt(handle,recheck=True)
rereads current save bytes, preservation frame, journal and public/native identity.
It still never grants load approval. Its status is
SETTLED_SAVE_OBSERVED_NOT_AUTHORIZATION; causal_engine_completion=false,
load_approved=false and guard_qualified=false. A matching file and label prove
observed bytes, not who caused them or that X4 has loaded them.

Default bounds: 64 in-process reservations; 64 polls and 300 seconds per
reservation; 4,096 filesystem members, 32 nested directory levels, 512 MiB per
physical file, 8 GiB total hashed preservation data and 2 GiB expanded XML.
Repeated full-frame hashing can be substantial: callers must admit this work
under the resource policy. The verifier does not start a background poller.
A smaller bound is a refusal boundary, never silently truncated coverage.

## Existing guard API research and future bridge

READ of ADAPTING.md 208–268 and tools/agent-harness.py 229–246: Action v1 files
surface supplies exact paths and operations to the shared backend; read operations
are excluded from mutation calls. tools/x4_harness/save_policy.py 250–275 recognizes
.xml/.xml.gz saves, resolves the complete affected set and rejects unresolved
scope. Its enforce path preserves recognized existing saves through Dispatcher.pre;
opaque-program effects can produce ASK. No evaluations were made against user saves.

A proposed write-surface action naming both possible slot representations could
request policy evaluation of those named filesystem effects. It does **not**
establish that a remote engine SaveGame call has only those effects, reserve the
slot, issue user consent, or authorize LoadGame. An unknown MCP surface is explicitly
unguarded, not an alternate route. Do not misclassify native execution as an editor
operation to avoid an opaque-scope refusal. Existing permanent save preservation,
strict guard reply validation and actual explicit user approval must remain in
the dispatcher; no guard code change is proposed.

Proposed integration, still unimplemented:
1. A trusted host dispatcher obtains an opaque verifier reservation and binds it
   to exact public stamp, process/profile, request digest, slot and label.
2. Real launch/save/load-specific authorization and supported operation guard
   evaluation occur in that host. Any ASK/DENY, unguarded scope or unresolved native
   effects stop dispatch. The observation receipt is never used as approval.
3. A bounded one-use host capability can be correlated to the Lua request. It must
   be checked at dispatch, not represented by arbitrary caller JSON or a constant
   true callback. Current Lua callback injection is a contract, not this transport.
4. Native catalogue and settled-file observations provide independent post-save
   evidence. Loading requires a new explicit capability and a fresh receipt
   recheck; it cannot be chained automatically from save completion.
5. After load, establish the new canonical session and correlate the loaded save
   separately. A disconnection or changed UI alone is not successful load evidence.

## Qualification status

Synthetic tests use temporary profile-shaped directories only. They cover plain
and gzip settlement, preserved files, occupied/unknown slot variants, identity and
catalogue drift, claim/journal tampering, corrupt XML/gzip, DTD refusal, unstable
writes, foreign handles and link refusal. Initial resource admission returned WAIT
before launching a child. The admitted final combined host/Lua run passed 99
checks, with one Windows symlink-creation privilege skip; hardlink refusal ran.
Actual Lua producer plus wire encoding covers empty and populated catalogues.
Windows pathname stat and descriptor stat can expose different ctime semantics:
cross-API checks use device/inode/size/mtime; full same-API before/after signatures
remain checked. Controls exercise constant cross-API ctime differences and actual
descriptor ctime drift. Final catalogue callbacks cannot change session identity
without refusal. These synthetic results do not qualify production callbacks,
authorization, native save/load effects or runtime integration.
