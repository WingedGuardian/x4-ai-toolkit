# Native ticker-cache window

`live_ticker.lua` is a read-only factory accepting `ffi`, `C`, `current` (canonical public stamp), `now` (observer UI clock) and `encode`. `read(start, limit)` observes a one-based window; `dispose()` retires it. Native assembly exposes the fixed ticker_window route with exactly two canonical integer arguments. No native write, menu opening, clearing or acknowledgement is included.

READ source: `ui/addons/ego_detailmonitor/menu_playerinfo.lua:12,114–120,220,248,2828–2906` defines unsigned 64-bit TickerCacheID, TickerCacheEntry (id, double time, category/title/text pointers), GetNumTickerCache and GetTickerCache. Native calls pass the empty category filter, one-based start and identical allocation/query counts. The module retains all five native fields. It does not invent the UI's placeholder faction/entity names or money values.

OBSERVED means two identical sampled windows with equal native totals and unchanged canonical public identity. Complete_window applies to the requested window. Covers_total is true only for a window starting at one whose returned count equals the observed total. Next_index is zero at the observed cache end. Different windows are separate observations and must not be silently stitched into an atomic census. Cache contents do not establish all notifications ever emitted or prove pixels were displayed. Entry time is retained in its native, unqualified clock domain; it is not compared to the observer UI clock or MD player age.

A total of zero requires two equal native count reads and returns a qualified empty window. Starting one beyond a nonempty cache returns an empty requested window while retaining the nonzero total; starting farther beyond refuses. Missing/failed/nonfinite/over-bound counts, short fills, changed rows or totals, missing strings and session drift produce UNAVAILABLE with no partial rows. Native ticker IDs remain exact decimal strings, including zero if returned; they are not component IDs. IDs are not deduplicated or asserted globally unique without a native contract proving that property.

Bounds are 1,048,576 observed cache entries, at most 16 rows per call, native category/title/text lengths of 256/4,096/16,384 bytes, and 30,000 encoded result bytes. The actual supplied wire encoder additionally enforces its string/node limits (currently a 4,096-byte individual string limit); a longer native text refuses rather than truncating. A caller may request a smaller window after an explicit payload-bound refusal. This read-only retry does not submit or repeat a game action. Failed native calls never become an empty success.

FFI verification checks unsigned identity width/type, struct size/alignment/field offsets, pointer types and the double timestamp representation before any native call. Private comparison buffers are not passed to the native getter. These checks and synthetic LuaJIT callbacks qualify defensive behavior, not the game binary's parameter ABI. Native runtime and rendered-notification attribution remain unqualified.

The bundle hashes this module and shared disposal retires it. Standalone controls
passed 47 with zero skips (resource ff3953f4f29f); combined assembly/bundle/
composition/main controls passed 399 with zero skips (bbfa60e25634), including
actual zero-window and getter-failure distinctions through assembly.
Independent source and wiring reviews are clear.
