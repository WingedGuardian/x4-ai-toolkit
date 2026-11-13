# Read-only fitted-equipment capture

`x4live equipment <issued-ship-ID>` reads a single live ship's engine, shield, turret
and weapon slots. Obtain the opaque ID from this UI session, for example with
`x4live query player`. IDs from before a UI reload or save load must be reacquired.
The command outputs JSON and makes no game changes. As with all experimental x4live
work, use a throwaway save. **This equipment path has offline tests, not live qualification.**

The live pipe requires Mod Support APIs' native named-pipe library. Protected UI Mode
can prevent that library from loading even when the helper's Lua has loaded. Using
the native pipe may require turning Protected UI Mode off in the game's Extensions
options; that is the user's choice. The toolkit never changes this setting.

The helper's first LOAD marker records the engine's GetUISafeModeOption() result
as ui_safe_mode=true, false or unknown. A connection refusal uses only the latest
marker in a recently written configured debug log; stale, absent, failed and ambiguous
readings remain unknown. This records the setting **at load**, not after later toggles,
and log recency does not prove process identity. A loaded helper without a connection
does not establish that the game is hung or that its frame loop stopped.

Every result names the exact requested ship ID, loaded Lua BUILD fingerprint,
loaded-at clock value and capture start/end. Times are UI elapsed seconds, not
simulation time. Enumeration happens within one bounded callback; engine-wide
atomicity is not established. Later pages retrieve the same captured bytes without
reading equipment again.

## What the result means

- `slots`: one row per visited 1-based native slot. `installed` requires a nonzero,
  valid component and its actual macro. `empty` requires a zero component plus the
  native direct-slot macro query returning an empty string. Read failures and grouped
  slots whose individual component cannot be resolved remain `unknown`.
- `groups`: the native group descriptors, including context ID, path and group.
  `group_summaries` records the separate native count, operational, total,
  currentcomponent, currentmacro and slot-size values for each non-root descriptor
  and engine/shield/turret type. These summaries are **never added to slot totals**;
  no group-to-slot mapping or mixed-group model is inferred.
  Component and context handles inside these rows are observations, **not newly
  issued IDs** for the general `query component` allowlist.
- `enumeration_complete` compares captured rows with each native denominator.
  A failed count is `?`, never a measured zero.
- `reads_complete` reports whether any required reads failed.
  `equipment_identities_complete` also requires all individual slot states resolved.
  It says nothing about compatible loadouts, ammunition, crew, damage performance,
  correct equipment choices or whether the ship can fight.

Exit 0 means complete requested identity coverage. A malformed ship ID exits 2
before any pipe opens. Exit 1 is a finding about the ID, only on the first request:
the ship no longer exists (ABSENT), was not issued by this UI session, or is
definitely not a ship. Any other refusal (a class the helper could not read, an
expired or superseded capture while paging, missing FFI declarations, no clock),
partial coverage or malformed protocol exits 3. Pipe-level failures retain x4live's existing
transport exit codes. No automatic retry, game launch, pause or control action occurs.
JSON escapes control characters; equipment text cannot inject additional rows.

## Bounds and continuation

One capture visits at most 1,024 slots in engine/shield/turret/weapon order and 256
group descriptors, with at most 8,192 budgeted native reads/conversions. UI clock
reads and local serialization are outside that read counter. Captured row bytes are
capped at 240,000; page payloads at 32,000 bytes; total retained page bytes at 262,144.
Native string fields over 256 bytes, or the reserved single-character `?` sentinel,
are unreadable rather than silently truncated or confused with an unknown value.
The captured rows and page assembly are also bounded; only one completed capture is
retained. Hitting a bound reports partial coverage, never an empty or complete result.

The CLI sends `equipment <ship-ID> <fresh-UUID-hex>`, then
`equipment-page <capture-token> <page>`. Tokens bind the caller nonce to a per-chunk
capture serial. Generate a **fresh UUID for every capture**, including after reload;
this is a correlation protocol, not authentication. Loaded-at time is not assumed
unique. A reload clears the cache; a newer accepted request supersedes it; continuation
also refuses after 60 UI seconds or a backwards clock change. A deliberately reused
nonce after reload is outside the contract. Concurrent captures can invalidate one
another; the reader refuses rather than combining their pages.

## Native provenance and remaining qualification

READ: `ui/addons/ego_detailmonitor/menu_ship_configuration.lua` in the 9.0 reference,
`prepareComponentUpgradeSlots` (10643–10718), loops 1-based
`GetNumUpgradeSlots` / `GetUpgradeSlotGroup` / `GetUpgradeSlotCurrentComponent`.
The menu calls `GetUpgradeSlotCurrentMacro` directly only for engine pseudogroups
or root path `..` with empty group; a deliberately blank grouped value is not proof
of an empty slot. Its `GetUpgradeGroups2` / `GetUpgradeGroupInfo2` path (4302–4318)
carries group context. The `UpgradeGroup` and `UpgradeGroup2` structs are distinct;
the latter layout is also declared in `menu_docked.lua` (49–53).

INFERRED, 90–95% source-transfer confidence: these native reads should expose the
same fitted identities through the helper's namespace. Synthetic tests qualify
guards, accounting, encoding and continuation, not this engine binding.
Live qualification still needs a known fitted ship, a known empty direct slot,
a grouped installation, an unresolved/destroyed component case where practical,
and comparison with an independent native component census or the game's equipment
UI. An automated comparison must bind the same ship and components and disclose
its coverage; authored loadout requests are not independent live observations.
Confirm the loaded BUILD and retain per-slot discrepancies. A failure should
narrow the unsupported query or mark it
unresolved, never guess a model from compatibility lists.
