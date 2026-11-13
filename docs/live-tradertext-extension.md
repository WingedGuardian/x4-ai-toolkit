# Optional built trader and encyclopedia extension

Offline implementation qualified against synthetic native bindings; independently source-reviewed. Pure `scripts/live_tradertext_extension.py:prepare()` returns one own-code Lua candidate and a source-bound receipt, writing nothing. It embeds the qualified trader/text adapters plus their closed provider. The optional loader must follow the public bundle and register before first bundle construction. It requires no MD changes or campaign identifiers.

Fixed provider `x4-trader-text-v1`, revision computed from all consumed sources and generator: `trader.observe {trader,mode,start}`, `text.observe {section,start}`, `page {capture,index}`, `trader.expand {trader,mode,item,expanded}`, `trader.select {trader,mode,item,price}`, `trader.confirm {trader,mode,item,price}`. Read operations also use ordinary journal prepare/execute/status. They capture only the adapter's bounded current window. A subsequent capture supersedes the previous retained bytes. Pages are immutable 500-byte hex chunks of the exact captured JSON, with byte/page denominators, source status and originating request ID. Page completion means retained bytes retrieved, not a successful source observation or full catalogue. Captures expire after 300 UI seconds.

Actions retain native private tickets and dispatch once. Exact argument/revision/session/lifetime checks precede dispatch. Status returns retained outcome without replaying actions or querying a new menu. Button return remains `running`/unverified; exceptions after possible dispatch remain `unknown`. No direct grant, payment, menu opener, arbitrary callback, rendered-pixel or purchase-success assertion exists. Separate current observations and economic/ownership evidence must qualify any result.

The loader normalizes menu Lua IDs or FFI uint64 to an exact positive uint64. Data-global calls convert back through native `ConvertStringToLuaID` and require exact roundtrip equality; no `tonumber(uint64)` precision loss. Native `C.GetContextByClass` consumes the normalized uint64. This respects native trader126–133's differing input representations without assuming raw globals accept FFI cdata. The module uses `getElapsedTime`, separate from game time. Each adapter is immediately registered for disposal before provider registration; retirement is sticky and cleanup failures refuse replacement. Construction performs declarations/metadata checks only.

Production composition, deployment, native runtime ABI, real trader opening, charge/acquisition, and rendered text remain separate unqualified stages.

## Offline qualification

MEASURED: `tests/test_livetradertext_extension.py` passed **36/36 controls, zero skips**, pytest 16.87 seconds, child exit 0. Each Lua control runs in an isolated subprocess with the actual generated loader, both actual adapters, SDK and wire encoder; native menus/functions are synthetic. No game calls occurred. The earlier 1000-byte page design exceeded the real nested receipt budget for blueprint observations; the final 500-byte design passes exact encoded-envelope bounds, Unicode byte reconstruction and immutable page retrieval. The request mutation control rejects changed re-preparation and verifies that SDK execution retains the original 100-credit ticket. Historical failed tests are not qualification.

READ: independent services review found no remaining concrete source blocker in the six action schemas, private tickets, consume-before-dispatch, source-status scope, identity conversion, disposal or generator source pins. Review did not rerun tests. Separate previously qualified adapter suites remain 81 trader and 62 encyclopedia controls; these were not rerun or counted in the 36 extension controls.

Exact SHA-256 inputs for this qualification:

| Source | SHA-256 |
| --- | --- |
| `live_tradertext_provider.lua` | `c0b6fcc1c61b3ce076739e4568456928516dc93296d9691d2b120eda96bb94c9` |
| `live_tradertext_extension.py` | `c7841d4a30b7b94645a1ae853047b1ee532bc86779e38ffd95461526fe8ebf7f` |
| `test_livetradertext_extension.py` | `39d5f69f9490e25344a3039e99ec8547b2a9ea076444a5edd744c6d8f954f5de` |
| `live_tradermenu.lua` | `78914f35d774ec7482889d622491a6c45fe1324d2ff58ac6295fbb5a35cab178` |
| `live_encyclopedia.lua` | `372d757863d4c816936a630cef3a7b2ca278176d87bebd745eef089a9bbe6295` |
| `live_actions.lua` | `feec4e303d1537f08b641247217ddefe8830d349018cbbbe8a39006dc245faf6` |
| `live_actionwire.lua` | `60803b1b71c100fef87cd72b01e64bc604aae5eb584d85a3088dc6cc34199fe2` |

Production `prepare()` supplies a fresh candidate hash and revision bound to the exact consumed bytes. An SDK action marked completed means the scoped observation or retained page was produced; it never proves economic success, actual rendered pixels, or complete catalogue coverage.

## Fixed optional helper composition

`prepare_live_helper.build(..., extensions=('tradertext',))` includes exactly the
own-code loader through the supported catalog. It rechecks the adapter, text
observer, provider and factory source bytes; duplicate or unexpected outputs
refuse. Canonical ordering is tradertext, rooms, topology, conversation,
persistence, independent of caller order. Omitting an extension preserves its
absence. Manifest disclosure names possible licence/blueprint purchase effects
and the separate menu-opening, pixel and economic-outcome limitations.

Combined helper composition and actual extension controls passed 75/75, zero
skipped. Independent narrow composition review found no
blocker. This qualifies candidate generation and synthetic routing only; no
loader was deployed and no real purchase or encyclopedia rendering was tested.
