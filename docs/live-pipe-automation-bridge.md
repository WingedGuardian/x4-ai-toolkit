# Main helper automation bridge

Offline-tested, not qualified in game. The helper in `mods/x4_toolkit_helper` does not load the bundle (its ui.xml lists only live_query.lua and engine_probe.lua); only a helper composed with `scripts/prepare_live_helper.py` does. Native dependencies are now supplied to the bundle; mandatory provider composition is tracked separately.

The main pipe has three dedicated structured routes: ui_state, session_connect and bound. They do not enter the legacy pause advisory formatter. The host CLI blocks these raw transport names from query. Bound read capture invokes only the fixed legacy read whitelist, requires exactly one synchronous reply and restores its interception slot on failure; malformed reads cannot leak an extra frame into the next request.

Bootstrap creation is lazy after the bundle is loaded. Native MD invalidation clears issued IDs, retires the old bootstrap and permits a new instance on the next UI probe. Disposal failure stays refused. A session timeout is scheduled before sending its request, with the native Helper callback set to avoid blocking input. The host handshake timeout must exceed 10.25 seconds to receive that explicit timeout response; automation CLI defaults to 15 seconds.

Evidence:232 main-dispatch/legacy controls pass, zero skips, resource 972f1b88ecb2. An earlier combined run had 264 passes and two new test-harness failures (a callback list called as one function); those two tests were corrected. Its 34 bootstrap controls passed after the pure encode API exposure, with the already disclosed caught LuaJIT exception diagnostic noise. Source review is clear.

Confidence estimates:95% for preserving the tested legacy dispatch/capture mechanics;85% for native bootstrap/event integration until the approved runtime controls. Assumptions are the documented native event, blackboard and Helper scheduler behavior. No synthetic test establishes those engine facts.

The issued-ID resolver permits only tokens emitted by this helper instance. Object and sector token formats remain distinct. Native converters must preserve the exact positive uint64 identity; a double at or above 2^53 refuses. Generic objects require current native validity; ship, station and sector requests require the current native class. This establishes identity/class only: ownership and action eligibility remain adapter checks. Invalidation replaces the issued-ID table, retiring every old token.

The main bridge supplies native globals, Helper, the resolver and game clock to the bundle. Malformed bundle returns and failed construction remain sticky refusals rather than triggering repeat construction. Review caught and corrected unsafe secondary conversion, bounds checked after native conversion, and malformed-return retry gaps.

MEASURED successor:266 combined issued-resolver, main-pipe and legacy controls pass with zero skips (resource 4bcab4a93136,1.99 seconds), helper BUILD cbbdcc4f. Actual LuaJIT integer semantics are exercised with synthetic native calls; this is not a live-engine test. Pytest's faulthandler plugin was disabled for this run because caught LuaJIT exceptions generate misleading Windows fatal-exception diagnostics; assertions and process exit checking remained enabled. Earlier45 focused controls passed with that diagnostic noise still enabled.

Large read paging is implemented and separately qualified in live-readpages-qualification.md. Still required: generated bundle/MD and ui.xml composition, private provider registration, independent final integration review, installed qualification and native controls. Complete transport does not prove complete enumeration.

