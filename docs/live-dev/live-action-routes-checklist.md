# In-game checklist for the ROR action routes

Each row is one measurement that offline tests cannot make. The synthetic Lua suites prove
the routes refuse and dispatch as written. They do not prove that the game accepts the call,
shows the screen or changes the world. A row passes only on a read-back from the game. An
`attempted`, `running` or `DISPATCHED_UNVERIFIED` status alone is not a pass.

**Every row needs the user's approval for that launch; throwaway save; x4live is
experimental.** Approval for one row, for an earlier launch or for this checklist as a
whole does not cover the next launch. One launch may cover several rows only if the user
approves that launch for exactly those rows. Never a save the user is playing.

| # | Route | What to do | Pass means (read back) | Code it exercises |
|---|---|---|---|---|
| 1 | Start menu, new game | From the main menu, prepare and execute `open_new`, then take your hands off the keyboard and mouse. | Within 30 s a start-menu observe reports `currentOption == "new"` (the ordinary new-game page). No world starts. | `live_startmenu.lua:150` (prepare), `:158-159` (open_new preconditions), `:182` (30 s recheck window), `:233` (`openSubmenu("new","new")`), `:58` (page `new` check) |
| 2 | Options route to the ROR settings page | Open options, `prepare_submenu` to the ROR page, then one `prepare_select` or `prepare_button` on it. | The page id observed is not on the reserved list, and the select or button produces the ROR settings marker the mod writes for that setting. Record the page id. | `live_options.lua:230-231` (reserved pages: main, save, load, extensions, bonus, new, profile_load, simple_menu_extension_options), `:195` submenu, `:212` select, `:226` button, `:271` execute |
| 3 | One dock order | `order.submit` a `dock` for one player ship at one host station. | The ship's order list shows `DockAndWait` at that host, and later the ship is docked there. | `live_nativeproviders.lua:162` (`order.submit`), `live_orders.lua:235` (`dock` maps to `DockAndWait`), `:178` |
| 4 | One move beyond a gate | `order.submit` a `move` to a sector one gate away. | The order shows `MoveWait` with the target sector. Record whether the ship routes through the gate and arrives in the target sector, or stops short. | `live_orders.lua:235` (`move` maps to `MoveWait`), `:176`, `:275` |
| 5 | Shipyard resolve, then one purchase | Resolve a shipyard read-only with `stations <faction>` and `component <id>`. Then buy one ship through the ShipConfigurationMenu route (`menu.open`, `menu.select`/`menu.add`, `menu.confirm`). | Player money drops by the quoted price, and the build-task capture shows one new task at that shipyard with the expected macro and price. | `live_query.lua:2116` (`stations`), `:1233` (`component`), `live_nativeproviders.lua:48`, `:152` (menu actions), `live_buildtasks.lua:113` (capture), `:176` (task), `live_playerstate.lua:145` (money) |
| 6a | One save | One save through `PersistenceDispatcher` to a throwaway slot. Approve this launch separately from 6b. | The slot verifier sees a new save in that slot with a fresh timestamp. No other slot changed. | `tools/x4validate/x4validate/_livepersistdispatch.py:125` (class), `:159` (`reserve_save`) |
| 6b | One load | Load the save from 6a through `PersistenceDispatcher`. Approve this launch separately from 6a. | After the reload, the session identity changes and the world matches the 6a save. Issued ids from before the load are refused, as expected after a UI reload. | `_livepersistdispatch.py:174` (`prepare_load`) |
| 7 | Licence trader: open, then buy | Get the trader with `representative <faction>`. Then `trader.open {trader, mode='licence'}`, then `trader.observe`. Then the existing `trader.select` and `trader.confirm` for one licence the player can afford and lacks. | `trader.observe` returns `OBSERVED` for that trader in licence mode. After confirm, money has dropped by the quoted price and `HasLicence` reports the licence held. | `live_query.lua:2096` (`representative`), `live_tradermenu.lua:204` (opener preconditions), `:219` (`prepare_open`), `:229` (observe), `:266-267` (select/confirm), `:268` (execute), `live_playerstate.lua:145`, `:156` (money, licence read-back) |

## Notes per row

- **Row 1.** "Hands-off" is the measurement. If the page arrives only after a key press or
  mouse move, record a fail with that note. Do not count it as a pass.
- **Row 2.** The reserved list is a code fact, `live_options.lua:230-231`. A ROR page id on
  that list fails the row by design. Report it rather than editing the list.
- **Rows 3 and 4.** A reply of `running` means the order was handed over. The order list and
  the ship's position are the result. Row 4 records gate routing as observed, whatever it is.
- **Row 5.** The read-only resolve needs no approval of its own beyond the launch. The
  purchase spends in-game money, which is why the save must be a throwaway.
- **Row 6.** The save and the load are two separately approved operations even when they
  run in one session.
- **Row 7.** The opener mirrors the representative conversation's parameters,
  `[0, 0, $FactionRepresentative, 'licence']` (`md/npc_factionrepresentative.xml:272`,
  opened at `:295`). It does not mirror the `nolicencesale` faction tag or the diplomat
  route. For this row, pick a faction that sells licences. The game-side unknowns are listed
  in `live-trader-menu-qualification.md` ("Licence trader opener").

## Recording a result

For each row, record: date, game version, which launch approval it ran under, the exact
requests sent, the raw replies, and the read-back that decided pass or fail. An empty or
refused read-back is a non-answer, not a fail of the route, until the cause is known.
