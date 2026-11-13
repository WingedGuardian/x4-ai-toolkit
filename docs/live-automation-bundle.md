# Loaded automation bundle identity

scripts/live_automation_bundle.py is a pure candidate builder. It embeds exactly the listed toolkit-owned Lua modules, normalizes CRLF to LF, and hashes the complete generated Lua with its single BUILD field masked. The separate MD revision is included in those bytes. The receipt labels module hashes as normalized-source hashes; raw input/deployment bytes still need their own preservation checks.

The generated chunk declares one shared X4ToolkitAutomationBundle. Re-evaluation retires the previous bundle before replacing it. A failed disposal stays failed. Instance construction forces the attested module set/build/revision rather than accepting caller replacements. The bootstrap remains responsible for provider retirement and exact native dependencies.

Measured offline:18 controls pass, zero skips, resource7dd87234cea7. They load the actual combined module definitions without calling the native bootstrap, demonstrate that each module and the MD revision change the build, reject tampering, and check disposal/replacement using a synthetic bootstrap. Independent source review is clear.

This does not prove native loading, rendering or gameplay. It addresses the narrower problem that the old live_query fingerprint alone could not identify changes to auxiliary Lua modules. The build constant is source-generated attestation, not a hostile-code security boundary. Qualify the actual consumed MD source, candidate/deployment hashes, helper envelope build and runtime controls together.

The builder writes nothing. Composition/application to a helper mod, ui.xml ordering and main pipe dispatch remain separate integration steps.

## Known limits in 4.2.0

READ in the code; none of these is measured in game.

- **One session per UI load.** Each action provider and MD channel binds to the
  first public session it sees. A later `fresh` session in the same UI load (the MD
  accepts `fresh` only when the world has no session) leaves them refusing until the
  UI reloads; a save load reloads the UI.
- **The pipe does not authenticate its client.** Any program running as the same
  Windows user that can open the named pipe can request any action the installed
  extensions offer. The save/load gate in the game (`live_persistencegate.lua`)
  checks only that a grant is well formed and unused; whether a save or load is
  allowed is decided by the toolkit's host side.
- **State cues have no version or patch path.** Each MD script's `State` cue sets
  `$Binding` once and its request cues refuse any other value. MD keeps that state in
  the save, so a later helper that changes a `$Binding` value (or the State layout)
  cannot reach a save made with the earlier one until it adds a cue `version` with a
  `<patch>`. Treat this as a constraint on the next helper change.
- **A stalled ship placement locks that provider.** `$Busy` clears only when a status
  read observes the requested pose, or on a game start or load; the receipt says
  `failed_dispatch_locks_provider_until_load`.
- **FFI type declarations** follow vanilla's: the helper declares 24 struct names
  vanilla also declares, and each matches vanilla's field list
  (`tests/test_livehelper_ffi_vanilla.py`, which needs the unpacked reference).

## Mandatory native assembly successor

The fixed module inventory now includes nativeproviders, nativeassembly, playerstate
and buildtasks (14 modules total). Before bootstrap, the builder merges the native
assembly's closed read vocabulary with the supplied legacy reads and refuses
collisions; caller mutation cannot expand the captured vocabulary. The native
provider allow entry is exactly x4-native-v1=full bundle BUILD. A conflicting
caller revision refuses.

After bootstrap, the bundled assembly receives the actual session.current and
encoder plus the explicitly supplied native dependencies. Its provider is
registered and its read handlers become available. No silent legacy-only
automation fallback exists. A constructor, registration or retirement failure
leaves a sticky construction fault; owned callbacks/caches are retired as far as
their checked disposers permit. Disposal never adds gameplay cleanup.

The earlier18-control result above is historical. Successor controls extend
module fingerprints, mandatory provider registration, mixed namespace rejection,
constructor/registration/cleanup failures, caller-mutation isolation and actual
14-module bootstrap/assembly construction with throwing native-data/action stubs.
The latter permits synthetic event registration only; it does not call X4.
Successor33/33 controls passed, zero skips, childexit0, rb88aa35bbab7d,
pytest0.14s. Generator7e766aa5082df15b9f9666b68f1cfc33baa91b8431374df97b8e2b39180e9dce;
test7768b28f91f3af9d61c0215bf905db2692b7745eeebf939f9fe420414a3a6856.
Independent READ review found no remaining integration blocker. Caught LuaJIT
SEH diagnostics remained visible in the real-constructor control; no game was
queried or acted on, and the process completed successfully. The native engine
boundary, actual deployment and game behavior remain unverified.

## Installed extension catalog successor

Installed Lua code may call `X4ToolkitAutomationBundle.register_extension` before
the first `new()` attempt. Its exact descriptor is `{id, revision, providers,
attach}`: a token ID, lowercase 64-hex source revision, provider-ID-to-revision
map, and callback. Metadata and callback references are copied. Maximum eight
extensions, each declaring one to sixteen providers; duplicate extension or
provider IDs, the reserved native provider, and malformed metadata refuse.
The catalog freezes on the first construction attempt, including a failed one.
Caller `deps.provider_revisions` is no longer accepted; the frozen catalog and
native BUILD supply the allow map. Extension revisions remain separately
declared source identities; registering code is not itself hashed into BUILD.
Deployment qualification must bind the actual extension files to those revisions.

Each bootstrap attaches every extension afresh. The callback receives
`public_current`, `encode`, `build`, `extension_id`, `extension_revision`,
`registry={register}`, `on_dispose`, `ffi`, `C`, `globals`, `Helper`, `native`,
`resolve_issued`, and `clock`. The register-only facade permits exactly the
declared provider IDs/revisions, once each, during attachment. Each specification
must include a disposer. Attachment must return a handle with `dispose` and
register every declared provider. No general host evaluation or new router verb
is introduced.

Call `ctx.on_dispose(fn)` immediately after acquiring a resource, before a later
step can fail. Up to sixteen hooks are accepted during attachment. Disposers must
return exactly true; errors, false or nil preserve a sticky failure. Identical
function references registered as hook, provider and handle share a once-only
wrapper. Different functions must not independently destroy the same resource.
All registered hooks run on partial attachment failure, including thrown errors,
malformed handles and missing providers. Cleanup continues after another cleanup
fails. Retirement invalidates provider sessions, invocations and delayed delivery
before disposing resources; captured old contexts cannot register later.

This is a lifecycle contract for trusted, source-reviewed installed code, not a
sandbox. Hidden side effects that code fails to register cannot be recovered by
the catalog. No automatic gameplay cleanup is implied. The prior 33-control
receipt remains historical. Reentrant construction/disposal from an attaching
extension refuses before changing bundle ownership.

Measured successor qualification: 63/63 controls passed, zero skips, child exit0,
pytest0.18s, through resource-budget `live-extension-catalog` (execution06ba92).
Generator9dd83ea5c16d4da3e51e2bdcee3f9aebed98a662388c9b228093bea33f3569ed;
test518a3aa973370df7a05d04a9ce81a590cb4e62fe097d16f2cc8dce75525320d4.
The source review found no remaining blocker after adding reentrancy controls.
Coverage includes partial attach and cleanup failures, metadata copying,
collision/capacity/refusal paths, late callbacks, fresh attachment and actual
bundled bootstrap registration with native data/action functions forbidden.
Caught LuaJIT SEH diagnostics were visible; the child completed successfully.
This is offline qualification only; no native runtime execution is claimed.

The next catalog successor adds `mdchannel` to the fixed inventory (15 modules).
During attachment only, `ctx.create_md_channel(config)` constructs the bundled
fixed-name channel with the same public-current and native dependencies, and
immediately registers its disposer before returning. It consumes one of the
sixteen cleanup slots. Channel configuration is supplied by reviewed installed
code, never a host packet. No native query, subscription or send occurs during
construction. Successor66/66 controls passed, zero skips, childexit0,
pytest0.19s, rb0f9fbe54683d. These include actual15-module construction and channel
creation with native data/action functions forbidden, scoped dependency binding,
immediate disposal after later attach failure and post-attach refusal. Independent
READ review found no blocker. Native engine/event behavior remains unqualified.

The dialogue-probe successor adds `dialogprobe` to the fixed inventory (16
modules) and one zero-argument bound `dialog_probe` read. It reports native option
model evidence with actor/section/rendered visibility UNKNOWN; it adds no
conversation selection action. Integrated140 controls passed (31 probe,42
assembly,67 bundle), zero skips, resourcefed6415df34f. See
live-dialogprobe-qualification.md for exact source and runtime limitations.

The UI-start successor includes startmenu, startpipe and startbootstrap (19
modules). The main helper starts this channel before its world-only Init using
the supported `extensions.sn_mod_support_apis.ui.named_pipes.Pipes` module.
It declares IsStartmenu before lookup, schedules once, and never initializes
shared MSA polling. The bundle owns UI retirement before world attachment;
retirement failure is sticky and a cleanup callback cannot reenter construction.
The extension catalog stays open until world construction, so reviewed private
loaders can still register after the helper's initial UI startup.

MEASURED:329 focused bundle/main/legacy/composition controls passed, zero skips,
resource13e48a125db8. Independent READ review is clear after correcting the native
declaration and retirement reentrancy. Main helper BUILD cd90954e. These tests
use synthetic native APIs and do not establish actual pre-world loading, pipe
connection, widget operation or successful world entry. No deployment occurred.

The native-path successor adds paths to the fixed inventory (20 modules) and
native_save_path to the closed native read vocabulary. It returns the engine's
save-folder observation only, never an inferred screenshot root. Integrated164
controls passed, followed by135 affected controls after two additional actual
observer/assembly tests; the unchanged31 path controls retain their prior result.
Both runs had zero skips (81fba399d320, a6631cd43e7f). No runtime qualification is
implied by these controlled native doubles.

## Catalog capacity for combined diagnostics

The bounded catalog now accepts16 distinct extension descriptors; the17th
refuses. The former8 limit could not accommodate the planned8 public diagnostic
extensions together with a separate private fixture extension. Provider-ID
collision, descriptor copying, per-descriptor provider bounds and lifecycle
freeze checks remain enforced.

Bundle/helper controls passed132/132, zero skips (430a7c359580), with independent
source review. A nine-descriptor construction control verifies every declared
provider revision and registration; it uses synthetic providers and does not
prove the actual nine implementations work together in the engine. The test was
renamed to state that narrower evidence scope without changing its body.
Actual composed installation and runtime qualification remain open.

