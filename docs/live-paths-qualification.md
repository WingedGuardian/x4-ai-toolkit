# Native save-folder observation

`live_paths.lua` is a pure factory accepting `{ffi, C, current}`. Its `read()` method calls only the native `GetSaveFolderPath()` getter, observes the canonical public stamp before and after, and returns:

`{status="OBSERVED", scope="NATIVE_SAVE_FOLDER_PATH", stamp=..., save_folder=...}`

The returned bytes preserve the engine path. The module does not normalize it, choose a profile, touch the filesystem, create a directory or infer a screenshot root. Host consumers must validate the actual canonical path and their own required scope. Capture discovery and save verification are separate consumers; a save-folder observation is not evidence that a screenshot was produced there.

Native analogue: `ui/addons/ego_gameoptions/gameoptions.lua`, declaration at line 193 (`const char* GetSaveFolderPath(void)`) and UI display through `ffi.string` at 6306. The reviewed source SHA256 is `cff978c89336297ce182ad0b0fb6937c994448396f53359be70efa94fc3ee58f`. No native game source is distributed.

The adapter requires a non-null const-char pointer and reads at most 4097 bytes, stopping at the first NUL. It accepts nonempty strings through 4096 bytes; oversized, missing, unreadable or control-containing paths return UNAVAILABLE without a save_folder field. Non-ASCII bytes are preserved. A malformed, absent or changed public stamp also refuses. The observation has no cached path and must be freshly requested; its identity copy cannot alias a later mutable stamp. Disposal prevents further native calls.

The pointer is supplied by the trusted native API; this is not a sandbox for arbitrary addresses. Canonical filesystem checks and UTF-8 transport decoding remain host responsibilities. There is no engine-wide atomicity guarantee or proof that another process cannot change filesystem state after observation.

Offline qualification:31 actual LuaJIT controls passed, zero skips, with synthetic native paths and pointers. They include native-first/helper-first retained function signatures, null/missing/wrong-type/empty/overlong paths, boundary lengths, UTF-8 preservation, every public-identity field drifting during the getter, malformed stamps before native calls, fresh reads and retirement. No X4 process or real personal path was queried.

Public integration now exposes the exact zero-argument bound read native_save_path
through nativeassembly, includes this source in the 20-module bundle identity and
owns its disposal. It preserves OBSERVED/UNAVAILABLE inside the transport reply;
an OK transport response is not proof of a path observation. No new CLI command,
save/load permission or game action is added. Actual engine observation remains
unqualified.

MEASURED:the integrated module/assembly/bundle/composition group passed 164 controls,
zero skips (resource 81fba399d320). Two more actual-observer-through-assembly controls
were then added; the affected 135-control group passed, zero skips
(resource a6631cd43e7f), while the unchanged 31 native-path controls retain their
previous result. Independent READ review is clear. No game or filesystem action
was performed by the observer.
