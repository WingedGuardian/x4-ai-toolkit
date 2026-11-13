# Native logbook window

The fixed `logbook_window` read accepts a one-based start index and count 1–16.
It calls the same native `GetNumLogbook("all")` / `GetLogbook(start,count,"all")`
functions as `menu_playerinfo.lua`2830/2856/2909 and `menu_map.lua`16802–16816.
These are READ source analogues, not runtime qualification of this instrument.

The result preserves time, title, text, category, entityname and factionname.
Money/bonus and other unprojected fields are not observed. It neither opens nor
clears a menu and does not acknowledge notifications. Ticker notifications are
explicitly outside this instrument. Indices are window positions, not durable
event IDs; matching text or time alone does not establish a causal campaign event.

Two projected reads and two totals must agree under the same canonical public
stamp and nondecreasing game time. This catches ordinary races but does not prove
an atomic snapshot or exclude a change-and-revert between samples. A zero requires
two native zero totals; nil, holes, count mismatch, oversized data and unavailable
native functions produce UNAVAILABLE without invented rows or totals. Out-of-range
requests and disposed observers do not call native functions. Returned data does
not alias the native tables.

The wire contains at most 30,000 encoded bytes. Oversize windows explicitly refuse
and recommend fewer rows; no text is silently truncated. Reports include the
requested/returned counts, native total, next index and whether this window covers
the total at observation. Multiple windows are not an atomic whole-log census.

Offline evidence:27 native controls passed;154 assembly,
bundle and composition controls passed (4592fd269db1), including actual observer
zero/unavailable handling through the fixed read route. Independent review found
no blocking issue and requested the precise projected-read stability wording.
The final projected-wording successor passed all 181 combined controls, zero skips
(5b9e9c9394c3); independent wiring review is clear. Synthetic fixtures
are not measurements from X4. No deployment or game action occurred.

Integration adds this observer to the public source-bound bundle and its native
assembly lifecycle. The read remains subject to outer session/build/profile and
transport qualification. Final combined source packet and runtime controls remain
required before accepting a campaign logbook case.
