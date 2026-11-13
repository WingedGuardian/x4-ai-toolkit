# Encyclopedia and property-list navigation

This optional trader/text extension successor supplies fixed native menu routes and bounded observations of built UI descriptors. It does not prove visible pixels, a completed transition, or that an encyclopedia entry describes an object correctly. No game session has exercised this successor.

Current checkpoint: independently source reviewed and 153 distinct offline controls qualified (45 navigation,46 generated-extension/SDK/wire,62 unchanged encyclopedia observer). This is isolated engineering source, not a release/deployment or replacement of the validated staging packet. Full combined helper initialization remains for the later aggregate successor.

## Native source and confidence

READ against the installed X4 UI sources:

| Link | Native analogue | Source confidence and limit |
|---|---|---|
| Root encyclopedia | Helper.topLevelMenus, helper.lua:9782, checks at 9829 | 98%: exact name and two zero parameters; runtime opening unmeasured |
| Exact issued ship/module entry | InteractMenu.buttonEncyclopedia:2390–2416 | 95%: component-derived macro/infolibrary, fixed Ships/Stations; excludes sector and arbitrary caller macro |
| Encyclopedia entry selection | menu_encyclopedia.lua:1965–2014; Helper rowChanged:959–973 | 95%: captured actual rowChanged wrapper and retained native rowDataMap; marks known items read and may schedule menu refresh |
| Index expansion | menu_encyclopedia.lua:1478–1526 | 95%: exact registered button wrapper; no assignment to expanded state |
| Property sidebar | menu_map.lua:19797–19856 and 27064–27073 | 95%: fixed propertyowned row; panel mode uses native row-change wrapper, otherwise button wrapper |
| Property descriptors | menu_map.lua:8162–8190,8987 onward | 95%: actual ten-column built table, property component rows and static descriptor strings; no ownership census or viewport claim |

Confidence applies to source transfer, not runtime qualification. The independent native opening/refresh/pixel checks remain required. The generated loader uses existing OpenMenu and Helper.convertComponentIDs; exact uint64/Lua ID roundtrip precedes component reads. Native IsComponentClass and IsStoryFeatureUnlocked declarations come from Helper 398/411. No constructor calls those APIs.

## Fixed actions

Provider remains x4-trader-text-v1. Existing trader actions, text.observe and page remain supported.

- text.open {} opens the encyclopedia root when registered menus are closed.
- text.open_component {subject} uses a helper-issued object token, resolves it again at dispatch, and derives the ship/module entry from current native properties. Arbitrary macro/ware strings are refused.
- text.entries {start} observes up to four currently built encyclopedia index rows and issues private row tokens.
- text.select {row} invokes the captured native row-change wrapper for an exact observed entry. Ware selection is available through actual built entry rows; a caller-supplied ware name is not authority.
- text.expand {row} invokes the sole registered expansion button on an observed index/property row. Ambiguous or disabled buttons refuse.
- property.open_map {} opens the native map root. It does not fabricate infomode parameters.
- property.open {} selects the actual propertyowned sidebar route, or reveals its hidden panel. An already open property panel refuses this mutation.
- property.observe {start} observes four built owned-property table rows, their exact component IDs when present, and static text/icon descriptors.
- view.close {domain} accepts only encyclopedia or property and calls that exact shown menu's native close handler.
- capacity_observe {} returns local provider history and navigation ticket/window counts, canonical profile/revision/source, and exact ceilings. The sample includes its own provider request. It allocates no navigation ticket/window or MD operation.

Opening requires other registered menus closed. Close and read actions allow a deliberate automatic sequence between map and encyclopedia. A property mode transition may require a subsequent property.open to reveal its panel; inspect the built state after each dispatch. No action automatically retries.

Observation responses use the existing immutable 500-byte hex pages. Source status remains in the page header; a completed page transfer is not a successful native observation. Row tokens are valid only in the most recent successful window, the same view/row identities and a 15-second UI lifetime. Page retrieval does not create new row observations.

## Scope and lifetime

Names and icon strings are built descriptor values, including native text markup; they are not OCR, decoded icons or pixels. Dynamic callbacks are UNKNOWN and never invoked by observation. The denominator is the current built table, which can exclude filtered, collapsed, offscreen or otherwise unbuilt objects. Property rows are observations only and do not issue general component authority.

Preparation captures public session/world/generation, menu/frame/table IDs, native rowdata and handler references. Execute consumes its ticket before rechecking; expired, rebuilt, disabled and rebound routes refuse. Native exceptions after an attempted call remain UNCERTAIN. Native callback return remains DISPATCHED_UNVERIFIED; status does not redispatch or claim successful selection.

The trusted generation's canonical profile controls the navigation history/window ceiling via native_history (interactive 128, campaign 512), and the provider history via sdk_history (interactive 256, campaign 8192). No wire argument selects a quota. Legacy nil-resolver interactive construction retains its fixed defaults. Records are never evicted to make room. These local ceilings are not a whole-campaign fit claim.

Every accepted new SDK preparation consumes one SDK history entry and one provider entry, including page and capacity reads and preparations subsequently refused by the native adapter. Successful navigation preparation additionally consumes one navigation ticket. A successful text.entries/property.observe execute consumes one window; fetching its pages does not. text.observe uses the existing descriptor observer without a navigation window. Each page is a separate SDK/provider request. capacity_observe executes no game query and returns counters at that execute only; subsequent SDK status retains the original sample, with current_counts_verified=false. Failed argument/identity checks before provider allocation are not charged to its history, although SDK accounting remains independently authoritative.

The candidate adds one own-code Lua source to the existing generated extension. It does not modify the approved 54-file staging tree, create XML, add an extension descriptor or change the global bundle. Full composed-constructor validation belongs to the later combined successor.

## Offline evidence

Final combined run completed 152 passes and one test-fixture failure, zero skips,20.01s/67MB. The property test had retained EncyclopediaMenu.mode='Ships' when converting its synthetic menu into MapMenu, and was correctly refused. The only correction sets that fixture's mode=nil; the exact failed case then passed in (1pass,45deselected,0skip,0.57s/65MB). Production sources were unchanged. The union qualifies 153 distinct controls, including both canonical capacity profiles and retained terminal counter status after later requests. Historical initial 32/32 and 41/41 precede this final hardening and are not added to that denominator.

Independent READ covered the native analogues, exact component conversion, row/frame/handler rebinding, final observation checks and scope labels. Two earlier expanded test admissions were NOT_LAUNCHED under the game reserve, including 27.43GiB used against a25.47GiB ceiling. The final run began only after admission recovered. No native calls or full-helper constructor run were substituted for those holds.

Tested raw SHA256 pins:

- navigation: bfa17745177cdffd122b4303972c52cf13c3cedcd869efda0695146b7782aef4
- provider:77984fc06571de2ddeaddfd506a0535c5368cbca6308786d3a379a26dbe67d59
- factory:fb7b5855a39dfc19772aa2ddb9d01819e96971aa488b506d2365e4bf7864a7e5
- navigation tests:4f684e30af416247dad043b4c223ef5de64c499c0e995c2a9031288c62ecd2fa
- corrected SDK tests:7d86685a13fd10a8fff391a85081fab2323d5da456447760de5a98cb635a3139

The LuaJIT refusal controls emit caught Windows exception code 0xe24c4a02 diagnostics under CPython faulthandler; actual child exit and pytest outcomes remain the execution evidence. No native game API, OS input, deployed file, profile or game state was changed.
