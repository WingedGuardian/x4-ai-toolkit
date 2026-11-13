# Read-only menu observation paging

Experimental and offline-qualified only. No game, native menu callback, order,
deployment or profile operation is performed by this unit. It wraps the existing
`live_menu.observe()` contract without changing that reader or its actions.

## Why this exists

MEASURED through the existing synthetic menu fixture and actual Lua wire encoder:
the minimal observation is 1,781 UTF-8 bytes. The same valid fixture with one
allowed 4,096-byte native option label is 5,860 bytes. The bound envelope permits
at most 4,096 bytes in its inner payload string. This demonstrates a reachable
contract-capacity gap; it does **not** measure a typical in-game menu.

The private source copy is never exposed as purported native SHA evidence.
Lua compares exact canonical source bytes; Python computes SHA-256 only after
joining the complete capture. That digest identifies the bytes received, not a
cryptographic attestation from the game or an independent engine measurement.

## Lua API

`live_menupages.lua` returns a factory accepting only an injected `observe()`
function. Integration supplies the unchanged qualified menu reader.

- `capture(id)`: the host supplies a fresh 32-character lowercase hex nonce.
  Reads the whole source observation twice and requires an identical model,
  excluding only the advancing observation age. Keeps one immutable capture;
  a new capture supersedes the previous one. Nonces cannot be reused within
  the instance; at most 128 attempts are retained. The host must never reuse
  a capture nonce after instance replacement.
- `page(id, index)`: zero-based page index. Re-reads and exactly compares the
  current source model before and after building every page. Any difference,
  missing model, backward clock or wrong capture invalidates the capture.
  Age may advance; the returned document retains its original captured age.
  Frame, epoch, model values, widget states and available option populations
  remain in the comparison. There is no silent recapture or retry.

Pages carry revision `menu-observation-pages-v1`, capture nonce, byte/node/string
denominators, page count, 1,000-byte chunk size, offset, actual chunk length and
ASCII lowercase hexadecimal data. Splitting bytes this way never guesses a
UTF-8 boundary; UTF-8 is decoded strictly only after all bytes are joined.
A page and its metadata fit the existing 4,096-byte payload-string limit.
The ordinary public BoundPipe still qualifies process/session/helper identity.

The typed JSON document encodes each Lua table as
`{"$table":[["n",1,value],["s","name",value]]}`, numeric keys first, then string
keys in byte order. This preserves integer versus string keys, sparse and mixed
tables, and empty tables without inventing array/object distinctions. The Python
value uses dictionaries with the original string or integer key types. Exact
integer markers and unresolved-function markers from the original menu reader
remain ordinary model data. Functions are never invoked to manufacture text.

Capture limits are 12,000 total value/key nodes, 131,072 source string bytes,
4,096 bytes per string, depth 20 including observation wrappers, and 1 MiB
encoded data. These are whole-capture ceilings; an underlying reader that
succeeds separately on several large widget models can still exceed this
aggregate ceiling and refuse explicitly. No population is silently truncated.

## Python reconstruction

`MenuCapture(raw_manifest)`, then `add(raw_page)` exactly once for every page
in increasing order, then `finish()`. Callers fetch through the qualified
session-bound transport. This reader neither opens a connection nor retries a
page. Missing, duplicated, reordered or mixed pages, inconsistent headers,
unknown revisions, malformed JSON, duplicate keys, nonfinite numbers, invalid
UTF-8 and mismatched reconstructed counts all refuse. A failed or finished
collector cannot be reused.

The result has two separate claims:

- `capture_complete=true` means every page was received, decoded and reconciled
  to the stated counts.
- `read_complete=true` means all five requested widget models were OBSERVED by
  the underlying reader. UNKNOWN or UNAVAILABLE widget observations remain in
  the document and make this false. Dropdown counts are integers only when
  that widget's native option read succeeded; otherwise they are `"UNKNOWN"`.
  A successful empty read may yield zero; a failed read cannot.

Neither flag certifies rendered text/pixels, resolved function-valued labels,
a menu entry route, eligibility of a future action, economic success or atomic
engine state. Page validity means the observed model matched at the check
landmarks; a transient change and return between reads is not ruled out.
Action preparation must still use the native menu adapter's own current-state
checks. Pagination is never a substitute for them.

## Verification

Tests exercise the actual unchanged menu observer and LuaJIT FFI fixture,
the new Lua pager, the existing wire encoder including the outer payload
string budget, and the strict Python join. Positive coverage includes large
labels, UTF-8, mixed key types, empty tables, deep values and advancing age.
Refusals cover stale/mid-page models, clock rollback, supersession, overflow,
unknown option reads, malformed headers, source-count lies and incomplete
or mixed page streams. Tests run in bounded child processes with assertion,
timeout and exit checks; protected LuaJIT exceptions do not become misleading
pytest Windows crash banners. No broad test suite or runtime result is implied.

The first attempt stopped at collection because the canonical installed Python
package lacked this new module; the corrected command explicitly selects the
isolated worktree package. The subsequent 35-control run passed but produced
protected-exception diagnostic noise. The child-process successor retains those
controls and adds strict reconstructed-count and typed-model refusals.

Final targeted result after independent review's huge-integer-age refusal: 48 of
48 controls passed, zero skips, in 24.94 seconds; resource-budget admission GO,
child exit 0, peak job memory 54 MB. The wrapper's
implausible CPU peak is not compliance evidence. The explicit isolated Python
package path was used. This is offline synthetic qualification, not runtime
menu availability or engine behavior.
