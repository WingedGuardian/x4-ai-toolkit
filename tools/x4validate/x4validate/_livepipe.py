r"""Ask the RUNNING engine a question, over a Windows named pipe.

THE COMPLEMENT TO `_livedump`. That module reads `{profile}/uidata.xml`, which the
engine truncates to 61 bytes while the game is running and writes back only on exit
-- an OFFLINE BULK oracle. This one is the LIVE SPOT CHECK: a fixed vocabulary asked
of the engine mid-session. They are complements, not alternatives.

THE TRANSPORT, AND WHY WE ARE THE SERVER. `sn_mod_support_apis` ships a lua pipe API
whose `ui/named_pipes/pipes.lua` opens a CLIENT handle to `\\.\pipe\<name>`. It does
not care who created that pipe, so `Pipe_Server_Host` / `Register_Module` /
`permissions.json` -- which exist only so a mod can ship a python file the HOST
auto-launches -- are not needed. We create the pipe; the game connects to it. That
makes the game-side mod four files and zero MD (content.xml, ui.xml, and the two
lua under ui/).

READ `pipes.lua:127`: `Schedule_Read(pipe_name, callback, continuous_read)`. With
`continuous_read` true the callback stays armed (`pipes.lua:513-515` declines to pop
the FIFO entry), so the SERVER PUSHES and the game never polls. `md/hotkey_api.xml`
runs exactly this shape in production.

    ---------------------------------------------------------------------------
    THE HAZARD THIS MODULE EXISTS TO REFUSE
    ---------------------------------------------------------------------------
    `pipes.lua:698`, verbatim:

        If the message is larger than the lua side buffer, returns partial
        data and error ERROR_MORE_DATA.  TODO: look into this.

    CORRECTED 2026-08-29 by reading the PACKED `pipes.lua` (it ships in a .cat,
    not loose; extracted from `ext_01.dat` at offset 400716, length 33966). The
    TODO is unhandled, but until today this docstring described the consequence
    wrongly in TWO ways, and both corrections matter:

      * DIRECTION. That comment sits on `_Read_Pipe_Raw` -- the game reading OUR
        COMMAND. It says nothing about our replies.
      * OUTCOME. It is NOT silent truncation. `winpipe` exposes only
        ERROR_IO_PENDING and ERROR_NO_DATA (`strings` on the DLL), so there is no
        constant to compare against and ERROR_MORE_DATA (234) falls through to
        `pipes.lua:720` `error(...)`. That is caught by `Read_Pipe`'s pcall,
        reaches `Poll_For_Reads:560` -> `Close_Pipe`, and ERRORs every pending
        read AND write while destroying the pipe. The partial data is discarded.

    The REPLY direction fails the same way, by the api's own documentation:
    "If the write buffer to the server fills up ... or the new message is larger
    than the entire buffer, the pipe will be treated as bad and closed."

    So an over-long message in EITHER direction is a loud, total pipe teardown --
    never a quiet short answer. That is the safer failure (a torn pipe cannot be
    mistaken for data), but it inverts the mitigation: SIZE MUST BE BOUNDED
    BEFORE SENDING, not detected afterwards. Any verb whose result set is
    unbounded caps itself and reports `shown=N of M`.

    The ceiling is still not fully known: python buffers 1 MiB (`_BUF`, raised from
    ramp MEASURED every size up to 64,000 bytes round-tripping intact on
    2026-08-29 -- which refuted an earlier untraceable 2047-byte figure. The true
    ceiling lies above our own buffer and cannot be probed without raising it.
    See BLIND-SPOTS F74.

    Every reply still carries its own BYTE LENGTH and a checksum computed
    game-side over the payload, so a short read fails clause 7 by construction.
    ---------------------------------------------------------------------------

THE FOUR OUTCOMES, matching `_livedump` so the two oracles ladder identically:

  | outcome                                   | meaning                    | exit |
  |-------------------------------------------|----------------------------|------|
  | not Windows / no pywin32 / never connects  | cannot ask                 | 2    |
  | connects, then silence                     | loaded, not executing      | 2    |
  | reply malformed, short, or mis-sequenced   | a NON-ANSWER               | 3    |
  | reply self-checks                          | a real answer, even ABSENT | 0/1  |

Note the third row. `ERROR`, `TIMEOUT` and `CANCELLED` are reserved sentinels in the
lua api's DATA channel, so a naive reader cannot tell them from a payload that
happens to equal one. Ours can: every real reply is a 7-field frame led by `MR`, and
a bare sentinel has no tabs at all.

Never collapse "not loaded" into "not answering". The Skyrim VR session shipped a
liveness probe that was wrong for three releases because it hung in exactly the case
it existed to detect.
"""
from __future__ import annotations

import pathlib
import re
import time
from dataclasses import dataclass

from . import _paths

#: Default pipe name. Override with `X4_LIVE_PIPE`.
#: ⚠ Resolved through `_paths.value()`, NEVER `path_value()` -- the latter runs
#: `native()`, which would mangle a `\\.\pipe\...` string into a filesystem path.
DEFAULT_PIPE = "x4live"

#: Bumped only on a frame change. A mod and a toolkit that disagree must say so
#: rather than mis-parse each other; clause 3 enforces it.
PROTO = 1

CMD_TAG = "MQ"
REPLY_TAG = "MR"
_REPLY_FIELDS = 7

#: A reply's `status`. ABSENT is an ANSWER -- the engine was asked and said no such
#: thing -- and must never be confused with "we could not ask", which is rc 2.
STATUSES = ("OK", "ABSENT", "ERR")

#: Our read buffer, and the size hint for both named-pipe buffers.
#:
#: ESTABLISHED 2026-08-31 by reading the code rather than repeating this module's own
#: docstring: OUR SIDE CAPS FIRST on the reply path. `_read_raw` does a SINGLE
#: `ReadFile(self._h, _BUF)` -- no loop on ERROR_MORE_DATA -- so a reply larger than
#: this cannot arrive whole. It is DETECTED rather than silent: `decode_reply` checks
#: the declared LENGTH before the checksum, precisely so the diagnosis is "truncated"
#: and not "corrupt". But detected-and-refused still means the ramp stops HERE, and a
#: ramp that stops at its own limit reports that limit as an engine finding (which it
#: did once, as "the ceiling lies in (60000, 65536]").
#:
#: Raised 64 KiB -> 1 MiB so F74's ceiling can be probed at all. This does NOT measure
#: the engine; it removes US from the measurement. The send path is still bounded by
#: the game's own lua buffer (`pipes.lua:698`), which is the thing F74 is about, and
#: an over-long message there TEARS THE PIPE DOWN rather than truncating.
_BUF = 1024 * 1024


#: The largest request we will WRITE, in bytes of the whole UTF-8 message (frame
#: header included). CRASH CONTAINMENT, added 2026-09-14.
#:
#: MEASURED in P6 (request-size ramp against the running game): a request of 1997
#: bytes round-tripped intact, and one of 3998 bytes TORE THE PIPE DOWN on the game's
#: READ path -- `pipes.lua:698` hands an over-long read to `error()`, which reaches
#: `Close_Pipe` and destroys the connection, ERRORing every pending read and write.
#: The mod then re-arms, and that teardown/re-arm churn on the UI thread is implicated
#: in a game crash the same session (minidump: a NULL-pointer dereference inside X4's
#: own UI event-dispatch code, reached through `lua_pcall`, NOT in the pipe DLL or lua
#: runtime). The teardown floor is therefore in (1997, 3998]; this stays safely below
#: it. `ask()` is the ONE place a request is written, so enforcing here means no client
#: path -- a query passthrough, ffi-census, the ramp -- can breach it.
MAX_REQUEST_BYTES = 1900


#: Appended to a "nothing connected" refusal ONLY when the window state could not be
#: measured. A future session reading one of these must be able to tell a RETRYABLE state
#: from a broken one -- shrugging at a minimized game and reporting "failed" is exactly the
#: outcome this text prevents.
#:
#: ⚠ THIS USED TO BE A 1,798-CHARACTER ESSAY appended to EVERY refusal, because nothing
#: could measure which cause applied and it hedged across all of them. `game_is_minimized`
#: now measures the main one, so the essay is replaced by a one-line diagnosis where the
#: answer is known and by this short residue where it is not. It also shed two things that
#: had stopped being its job: "grep debug.txt for the load marker" (read for the user by
#: `helper_loaded_this_session`) and "the mod is probably not installed" (measured directly
#: by `helper_is_deployed`, which names the extension and its dependency in its own branch).
#:
#: THE FULL PROVENANCE of the three claims below, kept here rather than in user output
#: because two measurements of different ages must not be flattened -- collapsing them is
#: what made the pre-2026-08-31 advice ("X4 STOPS EXECUTING WHEN IT IS NOT IN THE
#: FOREGROUND") wrong:
#:   * UNFOCUSED IS FINE -- 2026-08-30, sampling the engine's own getElapsedTime() over a
#:     30 s wall window: windowed unfocused 32.98 s engine / 32.98 s wall, windowed focused
#:     32.24 / 32.24, a ratio of 1.00 in BOTH. A 370-query harvest completed unfocused.
#:   * MINIMIZED STOPS IT -- originally one figure from exclusive fullscreen only (574.76 s
#:     of engine time across 70.4 min of wall clock, 13.6%, and zero bytes to debug.txt for
#:     5.5 min), which never separated minimized from merely unfocused and is how the old
#:     generalisation arose. MEASURED 2026-09-20 in BOTH display modes and now direct:
#:     windowed-minimized 0.031 CPU core-s/s and fullscreen-alt-tabbed 0.016, against
#:     0.81-25.9 while active. So the rule is MINIMIZED, in either mode -- not "background".
#:   * A PAUSED GAME IS NOT A CAUSE -- MEASURED 2026-09-13, a paused game kept answering on
#:     an open connection and accepted a new one. The earlier "a PAUSED game goes silent"
#:     was INFERRED from the api's MD heartbeat firing only on unpaused frames, and is
#:     WITHDRAWN: the UI `onUpdate` half of the frame detector keeps polling regardless.
#: MECHANISM, read from source: the pipe is polled by `Poll_For_Reads` via
#: `Time.Register_NewFrame_Callback`, driven by `SetScript("onUpdate", ...)`.
MINIMIZED_HINT = (
    " The channel is polled from the FRAME LOOP, so a game that is not drawing frames "
    "cannot answer, and I could NOT determine this window's state. MEASURED: being "
    "MINIMIZED stops it, in windowed and exclusive fullscreen alike (2026-09-20); merely "
    "UNFOCUSED does NOT (2026-08-30, a ratio of 1.00); a PAUSED game does NOT (MEASURED "
    "2026-09-13, it kept answering). So restore the window if it is minimized and run it "
    "again -- this is RETRYABLE, not a failure, and retrying costs nothing because the mod "
    "re-arms itself at least every 10s (every 2s right after a client)."
)

#: Said instead of the hint above once `game_is_minimized` has ANSWERED. One line each,
#: because a measured state needs a diagnosis rather than a survey of the alternatives.
MINIMIZED_DIAGNOSIS = (
    " MEASURED: X4's window is MINIMIZED, and the frame loop stops when it is -- in "
    "windowed and exclusive fullscreen alike (2026-09-20). The channel is polled from that "
    "loop, so nothing could answer. Restore the window and run it again: this is RETRYABLE, "
    "not a failure, and the mod re-arms itself at least every 10s."
)

NOT_MINIMIZED_DIAGNOSIS = (
    " MEASURED: X4's window is NOT minimized, so the frame loop should be running and this "
    "is NOT the usual cause -- do not go and un-minimize anything. Merely UNFOCUSED is fine "
    "(2026-08-30) and a PAUSED game still answers (2026-09-13). This is RETRYABLE, not a "
    "failure: retry once, since the mod re-arms at least every 10s. If it keeps failing, inspect "
    "the helper and Mod Support APIs load errors and the configured pipe name; this "
    "observation does not establish that the game is hung."
)


def game_is_minimized() -> bool | None:
    """Is X4's window MINIMIZED? True / False / **None when it could not be determined**.

    This is the one health signal that separates the common retryable failure from a real
    one, and it replaces an essay that had to hedge across every cause because nothing could
    measure any of them.

    ⚠ NO VISIBLE WINDOW IS **None**, NOT False. "I found no window to ask about" is a fact
    about the search, not about the window -- answering False there would put the second
    claim in the first one's grammar, which is CLAUDE.md #34 exactly.

    VERIFIED IN GAME 2026-09-20, which was the open risk: the handle SURVIVES exclusive
    fullscreen and alt-tab-away (same hwnd throughout, and across a display-mode change), so
    `IsIconic` carries the diagnosis in BOTH display modes. Not covered: a HUNG game.
    `IsHungAppWindow` is not exported by this pywin32 and a hang cannot be induced on demand,
    so that state is deliberately left unmeasured rather than guessed at.
    """
    from . import _livedump          # deferred, like `game_is_running` below
    pids = _livedump.game_pids()
    if not pids:
        return None
    try:
        import win32gui
        import win32process
    except ImportError:
        # silent-ok: pywin32 is a DEV-ONLY, Windows-only extra (pyproject `dev` group). Its
        # absence costs this one diagnosis and nothing else -- the caller falls back to the
        # hint, which is what every refusal said before this function existed.
        return None
    wanted = set(pids)
    states: list[bool] = []

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            _tid, wpid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:      # noqa: BLE001 - silent-ok: one unreadable hwnd out of many
            return             # must not abort the sweep; it just is not evidence.
        if wpid in wanted:
            states.append(bool(win32gui.IsIconic(hwnd)))

    try:
        win32gui.EnumWindows(visit, None)
    except Exception:          # noqa: BLE001
        # silent-ok: the THIRD state again. A failed enumeration is "could not ask".
        return None
    if not states:
        return None            # see the docstring: no window found is NOT "not minimized"
    # ALL, not ANY: X4 can own more than one visible top-level window, and the frame loop
    # stops only when the game itself is down. One non-iconic window is enough to refute it.
    return all(states)


class LiveQueryUnavailable(Exception):
    """Exit 2 -- a NON-ANSWER. We could not ask, so there is no finding here.

    Raised when the platform cannot host the channel at all, when the game never
    connects, and when it connects but stays silent. The message must always name
    WHICH of those it is: a probe that collapses "not loaded", "not executing" and "hung"
    into one verdict is wrong in exactly the case it exists for.
    """


class LiveQueryDegraded(Exception):
    """Exit 3 -- degraded. Something answered, but the answer cannot be trusted.

    Truncation, corruption, protocol skew and FIFO desync all land here. This is
    NOT exit 2: a mangled reply is evidence the channel is misbehaving, which is a
    louder fact than silence, not a quieter one.
    """


class LiveRequestTooLarge(ValueError):
    """A caller tried to send a request over `MAX_REQUEST_BYTES`. Refused BEFORE the
    write, because the write itself is what tears the pipe down (see MAX_REQUEST_BYTES).

    A ValueError, not a LiveQuery* transport error, on purpose: this is a CLIENT bug --
    a request that should have been split -- not a channel that misbehaved, so it must
    NOT be caught by the `except LiveQueryDegraded/Unavailable` retry/re-arm paths and
    silently swallowed. It surfaces loudly and the caller is fixed.
    """


# --------------------------------------------------------------------------- #
# codec -- pure, no win32, so the whole frame contract is unit-testable off-Windows
# --------------------------------------------------------------------------- #

def checksum(payload: str) -> int:
    """djb2 over the UTF-8 BYTES of `payload`, mod 2**32.

    Byte-wise, not character-wise, and the game side must agree exactly: lua's `#s`
    and `string.byte` are both byte operations, so a codepoint-based checksum here
    would disagree with the game on every non-ASCII payload -- and disagree SILENTLY,
    reporting corruption where there is none. Chosen over CRC32 because LuaJIT has no
    CRC32 and a hand-rolled one is a second thing to get wrong; djb2 is four lines on
    each side and truncation (the actual threat) is caught by the length clause
    regardless.
    """
    h = 5381
    for b in (payload if isinstance(payload, bytes) else payload.encode("utf-8")):
        h = ((h * 33) + b) & 0xFFFFFFFF
    return h


def byte_len(payload) -> int:
    """Length in BYTES, because lua's `#s` is bytes. See `checksum`.

    Accepts bytes unchanged: decode_reply measures the payload BEFORE decoding it, so
    that a repair cannot change the number being checked.
    """
    return len(payload if isinstance(payload, bytes) else payload.encode("utf-8"))


def encode_command(seq: int, verb: str, args: tuple[str, ...] = ()) -> str:
    """Frame a command for the game.

    ⚠ Never emits an empty string: the lua pipe api delivers an empty write as nil,
    so an empty message is indistinguishable from no message. The tag and sequence
    make that structurally impossible.
    """
    if not verb:
        raise ValueError("a command needs a verb; an empty message arrives as nil")
    for a in args:
        if "\t" in a or "\n" in a:
            raise ValueError(f"argument {a!r} contains a frame separator")
    return "\t".join((CMD_TAG, str(PROTO), str(seq), verb, *args))


@dataclass(frozen=True)
class Reply:
    """One decoded, self-checked reply. Reaching here means all 8 clauses passed."""

    seq: int
    status: str
    payload: str

    @property
    def ok(self) -> bool:
        return self.status == "OK"

    @property
    def fields(self) -> list[str]:
        """The payload split on tabs. Empty payload -> empty list, never `['']`."""
        return self.payload.split("\t") if self.payload else []


def decode_reply(text: "str | bytes | None", expect_seq: int) -> Reply:
    """Decode one reply, or raise.

    EIGHT clauses, each with its own fixture and its own separately-applied mutant.
    They are listed in this order deliberately: a guard that fires first SHADOWS the
    ones behind it, so a single falsification twin only ever exercises the first
    clause it trips (CLAUDE.md #26, register #86/#99). Ordering choice worth naming:
    the LENGTH check precedes the CHECKSUM check so that a truncated reply is
    reported as TRUNCATED -- the diagnosis a caller can act on -- rather than as
    generic corruption.
    """
    # BYTES throughout. The payload used to arrive already repaired by
    # errors="replace", and clauses 7 and 8 re-encoded that repair to measure it --
    # so they measured a string we had invented, not the frame that arrived. Each
    # replacement character adds +2 bytes, which both invents truncations and cancels
    # real ones. A `str` argument is still accepted (every test passes one) and is
    # encoded losslessly here.
    data = text.encode("utf-8") if isinstance(text, str) else text

    # 1. Nothing at all. The lua api hands an empty write over as nil.
    if not data:
        raise LiveQueryDegraded(
            "empty reply: the lua pipe api delivers an empty write as nil, so this "
            "is a framing fault game-side, not an empty answer"
        )

    def _t(b: bytes) -> str:
        """Bytes -> text FOR A MESSAGE ONLY. Never for a comparison or a measurement."""
        return b.decode("utf-8", errors="replace")

    parts = data.split(b"\t", _REPLY_FIELDS - 1)

    # 2. Not one of our frames. Catches the api's reserved data-channel sentinels
    #    (ERROR / TIMEOUT / CANCELLED), which a naive reader cannot tell from data.
    if parts[0] != REPLY_TAG.encode("utf-8"):
        head = _t(data[:40]).replace("\n", " ")
        hint = (
            " -- this is one of the lua api's reserved sentinels, not a payload"
            if _t(data).strip() in ("ERROR", "TIMEOUT", "CANCELLED")
            else ""
        )
        raise LiveQueryDegraded(
            f"reply is not a {REPLY_TAG} frame (starts {head!r}){hint}"
        )

    # 3. Protocol skew: a mod and a toolkit from different versions.
    if len(parts) < 2 or parts[1] != str(PROTO).encode("utf-8"):
        got = _t(parts[1]) if len(parts) > 1 else "<absent>"
        raise LiveQueryDegraded(
            f"protocol {got!r}, expected {PROTO!r} -- the deployed mod and this "
            f"toolkit disagree about the frame; redeploy the live-query mod"
        )

    # 4. Header truncated. Distinct from clause 7: this is a frame too short to
    #    even carry its own length, so nothing downstream can be trusted.
    if len(parts) != _REPLY_FIELDS:
        raise LiveQueryDegraded(
            f"reply has {len(parts)} fields, expected {_REPLY_FIELDS} -- the frame "
            f"header itself is truncated"
        )

    _, _, b_seq, b_status, b_len, b_sum, payload_bytes = parts
    # The header fields are ASCII by construction; only the PAYLOAD may be arbitrary,
    # and it is deliberately left as bytes until clauses 7 and 8 have measured it.
    raw_seq, status = _t(b_seq), _t(b_status)
    raw_len, raw_sum = _t(b_len), _t(b_sum)

    # 5. FIFO desync. Correlation is positional in the lua api, so the sequence
    #    number travels INSIDE the message; this is what makes it worth having.
    try:
        seq = int(raw_seq)
    except ValueError:
        raise LiveQueryDegraded(f"reply sequence {raw_seq!r} is not a number") from None
    if seq != expect_seq:
        raise LiveQueryDegraded(
            f"reply is for sequence {seq}, expected {expect_seq} -- the pipe FIFO is "
            f"out of step; a previous reply was probably dropped"
        )

    # 6. Unknown status: the game answered in a vocabulary we do not model.
    if status not in STATUSES:
        raise LiveQueryDegraded(
            f"unknown reply status {status!r}, expected one of {', '.join(STATUSES)}"
        )

    # 7. TRUNCATION -- the clause this whole module is built around. See the
    #    module docstring: the lua side cuts an over-long message short and says
    #    nothing, and a short TSV row is still a well-formed TSV row.
    try:
        declared = int(raw_len)
    except ValueError:
        raise LiveQueryDegraded(f"declared length {raw_len!r} is not a number") from None
    actual = byte_len(payload_bytes)
    if declared != actual:
        raise LiveQueryDegraded(
            f"TRUNCATED: the game declared {declared} payload bytes and {actual} "
            f"arrived. This is the `ERROR_MORE_DATA` case pipes.lua:698 leaves "
            f"unhandled -- ask for less, or chunk the reply"
        )

    # 8. Corruption that preserved the length. Rarer than truncation, and it is
    #    only because clause 7 runs first that this one gets to mean that.
    try:
        declared_sum = int(raw_sum)
    except ValueError:
        raise LiveQueryDegraded(f"declared checksum {raw_sum!r} is not a number") from None
    if declared_sum != checksum(payload_bytes):
        raise LiveQueryDegraded(
            f"checksum mismatch on a payload of the declared length -- the bytes "
            f"changed in transit, which truncation alone does not explain"
        )

    # 9. Decoded LAST, and STRICTLY. The bytes have now passed both the length and
    #    the checksum, so anything undecodable here is a third, distinct fact -- not
    #    something to paper over with a replacement character, which is what made
    #    clauses 7 and 8 measure a string we had invented.
    try:
        payload = payload_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LiveQueryDegraded(
            f"UNDECODABLE: {len(payload_bytes)} payload bytes matched the declared "
            f"length and checksum, but byte {exc.start} is not valid UTF-8 "
            f"({exc.reason}). The frame arrived intact and the game encoded it wrong."
        ) from None
    return Reply(seq=seq, status=status, payload=payload)


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #

def game_is_running() -> bool | None:
    """True / False / **None when we cannot tell** -- three states, never two.

    Delegates to _livedump.game_is_running, which is the same question with the same
    answer. This was a SECOND implementation, and it broke its own promise: it chained
    `.stdout` straight onto subprocess.run, so the RETURNCODE was structurally
    unavailable and a query that FAILED returned a confident False.

    MEASURED 2026-09-01 with a deliberately failing tasklist filter (rc=1, empty
    stdout): this shape returned False -- 'the game is not running' -- where the honest
    answer is None. wait_for_game would then tell a user whose game IS running to launch
    it. The only reason it never decided an outcome is that it is used solely to shape a
    message.
    """
    from . import _livedump
    return _livedump.game_is_running()


#: How recently debug.txt must have been written for us to treat it as THIS run's log.
#:
#: debug.txt OUTLIVES the run that wrote it, so a marker in an old file proves the mod loaded in
#: SOME session, not this one. There is no cheap, dependency-free way to read the game process's
#: start time (`game_is_running` shells out to `tasklist`, which does not report it), so recency
#: is the bound available. 300s is deliberately generous: the cost of it being too LONG is a
#: wrong answer, the cost of too SHORT is only a refusal, so it is set where a quiet-but-live
#: game still counts and a previous session almost never does.
LOG_LIVE_WINDOW_S = 300.0

#: The GENERIC suffix of the mod's load marker -- never the deployment-specific prefix. Spelling
#: the full marker in a shipped file is what F73 was raised for (it put a personal identifier
#: into the published package). MINIMIZED_HINT greps for this same substring.
_LOAD_MARKER = "_LIVE loaded"


def _recent_debug_text() -> str | None:
    """Read the configured log within the existing recency bound; not a process-ID proof."""
    try:
        p = _paths.debug_log()
        if p is None:
            return None
        if (time.time() - p.stat().st_mtime) > LOG_LIVE_WINDOW_S:
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # silent-ok: None is explicit unknown evidence, never an absent marker or mode-off claim.
        return None


def helper_ui_safe_mode_at_load() -> bool | None:
    """Explicit value on the LATEST helper marker in a recent log, or unknown.

    This is an at-load observation, not the current setting: toggles can happen
    later. Recency does not prove process identity. Never read profile config or
    search older markers when the latest marker omits/refuses the field.
    """
    text = _recent_debug_text()
    if text is None:
        return None
    for line in reversed(text.splitlines()):
        if _LOAD_MARKER not in line:
            continue
        values = [token.partition("=")[2] for token in line.split()
                  if token.startswith("ui_safe_mode=")]
        if len(values) == 1 and values[0] in ("true", "false"):
            return values[0] == "true"
        return None
    return None


def helper_loaded_this_session() -> bool | None:
    """Is a helper load marker present in the recent configured log? True/False/None.

    None is returned whenever the question cannot be answered HONESTLY: no log path resolves,
    the file cannot be read, or the log is older than `LOG_LIVE_WINDOW_S` and therefore cannot
    be treated as recent evidence. Staleness DOMINATES content in both directions -- a stale
    log answers None whether or not it carries the marker. Even a recent log is not proof
    of the current process or session; the historical function name is kept for compatibility.

    ⚠ Like `game_is_running`, this only ever shapes a MESSAGE; it never decides an outcome. A
    machine whose log is unreadable loses specificity and never gets a different verdict.
    """
    text = _recent_debug_text()
    if text is None:
        return None
    return _LOAD_MARKER in text


#: The helper's idle re-arm cap: `REARM_IDLE_MAX` in the shipped
#: `mods/x4_toolkit_helper/ui/live_query.lua`, pinned equal by
#: tests/test_live_connect_window.py. While no x4live process is serving the pipe the helper
#: backs its re-arm off 2 -> 4 -> 8 -> 10 s, so a fresh call may wait up to this long for the
#: game's next connection attempt.
HELPER_IDLE_REARM_MAX_S = 10.0

#: The shortest connect window `_livecli._live_open` uses: the cap plus margin for frame jitter
#: and one fast cycle. Only the CONNECT wait is floored; each reply keeps `--timeout`.
CONNECT_WINDOW_MIN_S = HELPER_IDLE_REARM_MAX_S + 5.0

_BUILD_LINE = re.compile(r'^local BUILD = "([0-9a-f]{8})"$', re.M)
_HELPER_LUA = ("x4_toolkit_helper", "ui", "live_query.lua")


def expected_helper_build() -> str | None:
    """The BUILD this toolkit ships for the helper's live_query.lua, or None.

    Read from the repo's own `mods/` copy (the same file `scripts/stamp-mod-build.py`
    stamps). None when this install carries no helper source -- then there is nothing to
    compare against, and the mismatch warning stays silent rather than guessing.
    """
    p = pathlib.Path(__file__).resolve().parents[3].joinpath("mods", *_HELPER_LUA)
    try:
        m = _BUILD_LINE.search(p.read_text(encoding="utf-8"))
    except OSError:
        # silent-ok: None is the documented "no shipped copy to compare" answer.
        return None
    return m.group(1) if m else None


def helper_loaded_build() -> str | None:
    """`build=` on the LATEST helper load marker in a recent log, or None.

    Same recency bound and the same caveat as `helper_loaded_this_session`: a recent log is
    not proof of the current process. It only feeds a WARNING.

    Runs on EVERY pipe command, so it does not read the whole log: it reads BACKWARDS in
    `_TAIL_CHUNK` pieces and stops at the first marker. The marker is written once per load
    and debug.txt grows by MBs per session (the 2026-11-14 copy: 4.1 MB, marker ~2.9 MB in),
    so a fixed small tail would miss it -- hence a bounded walk, not a tail. Past
    `_TAIL_MAX` bytes it answers None (unknown), never "no marker".
    """
    line = _latest_marker_line()
    if line is None:
        return None
    values = [tok.partition("=")[2] for tok in line.split() if tok.startswith("build=")]
    return values[0] if len(values) == 1 and values[0] else None


_TAIL_CHUNK = 256 * 1024
_TAIL_MAX = 64 * 1024 * 1024


def _latest_marker_line() -> str | None:
    """The last line of a RECENT log holding the load marker, read backwards and bounded."""
    marker = _LOAD_MARKER.encode("ascii")
    try:
        p = _paths.debug_log()
        if p is None or (time.time() - p.stat().st_mtime) > LOG_LIVE_WINDOW_S:
            return None
        with open(p, "rb") as fh:
            pos = fh.seek(0, 2)
            carry, read = b"", 0
            while pos > 0 and read < _TAIL_MAX:
                n = min(_TAIL_CHUNK, pos, _TAIL_MAX - read)
                pos -= n
                fh.seek(pos)
                lines = (fh.read(n) + carry).split(b"\n")
                read += n
                # The first piece may be the tail of a line that started earlier: keep it
                # for the next (earlier) chunk unless this chunk began at the file start.
                carry = lines.pop(0) if pos > 0 else b""
                for raw in reversed(lines):
                    if marker in raw:
                        return raw.decode("utf-8", errors="replace")
    except OSError:
        # silent-ok: None is explicit unknown evidence, as for the other marker reads.
        return None
    return None


def helper_build_warning(expected: str | None, reported: str | None,
                         source: str) -> str | None:
    """The stderr warning for a helper whose loaded BUILD differs from the shipped one.

    None when they agree, and None when either is unknown: nothing was compared, so nothing
    is claimed. Never a refusal -- a mismatched helper often still answers, and the user
    decides whether that is acceptable.
    """
    if not expected or not reported or expected == reported:
        return None
    return (f"WARNING: the X4 helper the game reports ({source}) is build={reported}, but this "
            f"toolkit ships build={expected}. The game is running a different helper than "
            f"this toolkit's -- redeploy mods/x4_toolkit_helper and reload the game (a save "
            f"load reloads the UI) before trusting behaviour that changed between them.")


def pipe_unref_guard_warning(fields) -> str | None:
    """The stderr warning when the helper's F124 pipe unref guard is not active, or None.

    The helper reports `pipe_unref_guard=` in its probe reply: `active` (its own pipe files
    are stamped, so their close cannot touch X4's Lua registry), `armed` (installed but no
    own file seen yet) or `inactive:<reason>` (refused, nothing written); a `;wrapper-replaced`
    suffix means another mod replaced Connect_Pipe after the helper wrapped it, which may or may
    not still call the helper's wrapper (`pipe_unref_stamped` tells). Anything but `active` warns, and so does a reply with NO such field: that helper predates the guard,
    so its connections are unprotected too. Never a refusal -- the reply itself is fine.
    """
    values = [f.split("=", 1)[1] for f in fields if f.startswith("pipe_unref_guard=")]
    if len(values) == 1 and values[0] == "active":
        return None
    if not values:
        state = "absent (this helper predates the guard)"
    elif len(values) > 1:
        state = f"reported {len(values)} times"
    else:
        state = values[0]
    return (f"WARNING: the X4 helper's pipe unref guard is {state}: connection churn can "
            f"corrupt X4's Lua registry (F124); restart X4 after this session.")


#: The helper extension's own manifest id, as shipped in this repo at
#: `mods/x4_toolkit_helper/content.xml`. Not a deployment-specific detail and therefore not an
#: F73 problem: we author the mod, so the id is ours and is identical in every install.
HELPER_EXTENSION_ID = "x4_toolkit_helper"


def helper_is_deployed() -> bool | None:
    """Is the helper extension one the ENGINE WOULD LOAD? True / False / **None**.

    The companion question to `helper_loaded_this_session`, and the one that disambiguates it.
    An absent load marker has TWO causes -- the mod is not installed/enabled, or the game has
    not loaded a game yet (the mod initialises on game load, so at the MAIN MENU the marker is
    legitimately absent). The marker alone cannot separate them; this can, because it measures
    the install directly instead of inferring it from a log.

    Scope is `"active"`, never `"installed"` (CLAUDE.md #24): a folder on disk that the profile
    has switched off is NOT something the engine loads, and calling it deployed would reproduce
    the exact false pass that made the scope argument mandatory.

    None when the question cannot be answered honestly -- an unconfigured toolkit, an extensions
    root that cannot be read, or **an EMPTY active set**. That last one is not a nicety: a
    CONFIGURED BUT NONEXISTENT `extensions\\` root raises nothing at all. `_paths` resolves it
    happily and `scan_installed` walks a directory that is not there, yielding 0 mods and 0
    drops, in silence -- so `any()` over it answered False and this function reported "not
    installed" about a machine it had never looked at. MEASURED 2026-09-20 against a deliberately
    wrong config. An empty population cannot support a negative (CLAUDE.md #9).

    ⚠ One inherited soft edge, documented rather than re-implemented: `_registry.mods` FAILS
    OPEN on an unreadable profile `content.xml`, so on such a machine a profile-disabled helper
    reads True. Like every signal here this only ever shapes a MESSAGE; it never decides an
    outcome.

    The verdict half of :func:`helper_deployment`, which also returns what the read left out.
    """
    return helper_deployment()[0]


def helper_deployment() -> tuple[bool | None, str | None]:
    """(:func:`helper_is_deployed`'s answer, the `_registry.dropped_note` of that read).

    The note matters most when the answer is False: the helper is ENABLED but the engine will
    not load it because its REQUIRED dependency (the named-pipe API mod) is missing or off, or
    its manifest will not parse. "Not in the load set" without that reason sends the user to
    check a switch that is already on (BLIND-SPOTS F139).
    """
    try:
        from . import _registry
        active = _registry.mods("active")
    except (_paths.Unconfigured, OSError):
        # silent-ok: None IS the channel here -- the same documented third state the marker
        # read uses. A toolkit that was never told where `extensions\` is cannot be allowed to
        # report "the mod is not installed", which is a claim about the world rather than
        # about our configuration. Costs specificity, never a verdict.
        return None, None
    note = _registry.dropped_note(active)
    if not active and note is None:
        # The denominator IS the finding. Zero mods means the scan looked somewhere wrong far
        # more often than it means a bare install, and only one of those two readings is safe
        # to be wrong about. A genuinely vanilla install loses specificity and still gets a
        # message naming both causes. (Zero LOADED mods with some left out is not that case:
        # the scan found mods, and the note says why none of them load.)
        return None, None
    return any(m.get("id") == HELPER_EXTENSION_ID for m in active), note


def _frame_loop_text(minimized: bool | None) -> str:
    """The frame-loop half of a refusal: a DIAGNOSIS once measured, the hint when not.

    One place, so the three call sites cannot drift apart again -- two of them already had.
    """
    if minimized is True:
        return MINIMIZED_DIAGNOSIS
    if minimized is False:
        return NOT_MINIMIZED_DIAGNOSIS
    return MINIMIZED_HINT


def _no_connection_reason(path: str, timeout: float, running: bool | None,
                          loaded: bool | None, deployed: bool | None,
                          minimized: bool | None, not_loaded: str | None = None,
                          ui_safe_mode_at_load: bool | None = None) -> str:
    """The refusal text for "nothing connected", built from what was MEASURED.

    Pure and separate from the wait loop so every branch is testable without a pipe, a game, or
    a clock. The branches are ordered by how much they narrow the user's next action.

    *deployed* and *minimized* are required, not defaulted, for the reason `_registry.mods`'
    scope argument is: a default would let a caller silently get one world's answer while
    meaning the other.

    *not_loaded* is the `_registry.dropped_note` of the read behind *deployed* -- the mods our
    model says the engine leaves out, and why. Defaulted, unlike the two above, because it only
    ADDS a reason to the not-deployed branch; it never selects one.
    """
    if running is False:
        return (f"X4 is NOT RUNNING, so nothing could connect to {path}. "
                f"Launch the game -- a FRESH launch if the mod's lua changed, "
                f"since a save/load is not verified to re-read it from disk.")
    head = (f"X4 IS RUNNING but nothing connected to {path} within {timeout:.0f}s."
            if running is True else
            f"nothing connected to {path} within {timeout:.0f}s, and I could not "
            f"determine whether X4 is running.")
    # Deployment is read BEFORE the marker because it measures the install directly, while the
    # marker only reports a consequence of it -- but a marker that says LOADED outranks it,
    # since a mod cannot log from a live run without being installed. That combination means
    # the mod set changed under the running game, not that it never loaded.
    if deployed is False and loaded is not True:
        return (head + " MEASURED: the helper extension is NOT in the set the engine would "
                "load, so it could not have answered. Check the extension is in the GAME-ROOT "
                "`extensions\\` folder (not the profile's), that it is enabled -- the profile's "
                "entry decides; with no entry, the manifest's own `enabled` is the default -- "
                "and that its named-pipe dependency (Mod Support APIs) is installed in the game "
                "root too. Only once all of those hold is this a frame-loop problem."
                + (f" NOT LOADED by our model of the engine: {not_loaded}." if not_loaded
                   else ""))
    if running is True and loaded is False:
        if deployed is True:
            # THE NARROWING THIS BRANCH EXISTS FOR, and the defect it replaces. Until
            # 2026-09-20 an absent marker was reported as "most likely not installed" -- which
            # is wrong at the MAIN MENU, where the mod is installed and simply has not
            # initialised yet. MEASURED in game that day: menu, no save loaded, marker absent,
            # extension present and enabled.
            return (head + " MEASURED: the helper extension IS installed and enabled, so this "
                    "is NOT a deployment problem -- but it has not logged its load marker in a "
                    "live debug.txt, and it initialises on GAME LOAD, not at the main menu. So "
                    "the game has most likely not loaded a save yet (or is still loading one). "
                    "Load a save and retry.")
        # deployed is None: two live causes and no evidence that separates them. Name both,
        # cheapest check first, rather than picking one -- picking one is the defect above.
        return (head + " MEASURED: the mod did NOT log its load marker in a live debug.txt, so "
                "it has not initialised -- and I could NOT determine whether it is installed, "
                "so this is TWO possibilities, not one. (1) No game is loaded yet: the mod "
                "initialises on game load, not at the main menu -- if you are at the menu, load "
                "a save and retry. (2) It is not installed or not enabled: check the extension "
                "is in the GAME-ROOT `extensions\\` folder (not the profile's), that it is "
                "enabled, and that Mod Support APIs is installed too.")
    if running is True and loaded is True:
        if ui_safe_mode_at_load is True:
            return (head + " The latest helper load marker in the recently written debug log "
                    "reports Protected UI Mode ON at load (ui_safe_mode=true). The live "
                    "channel needs the native named-pipe library, which this mode can block. "
                    "Review Protected UI Mode in the game's Extensions options; turning it "
                    "off is your choice, and this tool does not change it. The marker does "
                    "not prove the setting is still on, or that this is the only cause."
                    + (_frame_loop_text(minimized) if minimized is True else ""))
        return (head + " The recently written configured debug log contains a helper load "
                "marker; that does not establish the current process or session. The "
                "named-pipe connection was not established. Check the "
                "helper and Mod Support APIs load errors and the configured pipe name."
                + _frame_loop_text(minimized))
    return head + _frame_loop_text(minimized)


def pipe_name() -> str:
    return _paths.value("X4_LIVE_PIPE") or DEFAULT_PIPE


def _win32():
    """Import pywin32, or refuse with a NON-ANSWER naming the reason.

    Deliberately lazy and deliberately optional. The toolkit's CI runs ubuntu, and
    a hard Windows-only dependency would make the whole package uninstallable
    there; `winpipe.lua:44` gates itself on `package.config` for the same reason.
    """
    import sys

    if sys.platform != "win32":
        raise LiveQueryUnavailable(
            f"named pipes to X4 are Windows-only; this is {sys.platform}. The "
            f"offline oracle (`x4live oracle`) works everywhere"
        )
    try:
        import win32file
        import win32pipe
        import winerror
    except ImportError as exc:
        raise LiveQueryUnavailable(
            f"pywin32 is not installed ({exc}); the live channel needs it. "
            f"`uv add --group dev pywin32`, or use the offline `x4live oracle`"
        ) from exc
    return win32pipe, win32file, winerror


class LivePipe:
    """A named-pipe server the running game connects to. Context manager.

    Non-blocking throughout, with an explicit deadline on every wait, because a CLI
    that hangs is indistinguishable from a game that is hung -- which is the exact
    failure this channel exists to diagnose.
    """

    def __init__(self, name: str | None = None, timeout: float = 10.0,
                 connect_timeout: float | None = None) -> None:
        self.name = name or pipe_name()
        self.path = r"\\.\pipe" + "\\" + self.name
        self.timeout = timeout
        #: How long `wait_for_game` waits for the game to CONNECT; `timeout` bounds each
        #: reply. Separate because the helper's idle back-off can put its next connection
        #: attempt up to HELPER_IDLE_REARM_MAX_S away (`_live_open` floors it).
        self.connect_timeout = timeout if connect_timeout is None else connect_timeout
        self._h = None
        self._seq = 0
        self._connected = False

    # -- lifecycle ---------------------------------------------------------- #

    def __enter__(self) -> LivePipe:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        win32pipe, _win32file, winerror = _win32()
        try:
            self._h = self._create(win32pipe)
        except Exception as exc:
            # ERROR_PIPE_BUSY (231): `nMaxInstances` is 1, so exactly one process can
            # serve this pipe. MEASURED 2026-08-29 -- a concurrent session was holding
            # it and this raised a raw pywintypes.error, i.e. an unhandled traceback,
            # which is NONE of the four outcomes. A collision is a NON-ANSWER with a
            # specific and actionable cause, so it says so.
            if getattr(exc, "winerror", None) == winerror.ERROR_PIPE_BUSY:
                raise LiveQueryUnavailable(
                    f"another process is already serving {self.path}. Only one server "
                    f"can hold it (nMaxInstances=1) -- a concurrent x4live, or another "
                    f"session. Stop that one, or use --pipe with a different name"
                ) from exc
            raise LiveQueryUnavailable(
                f"could not create {self.path}: {exc}") from exc

    def _create(self, win32pipe):
        return win32pipe.CreateNamedPipe(
            self.path,
            win32pipe.PIPE_ACCESS_DUPLEX,
            # MESSAGE mode both ways, so one Read yields exactly one Write and we
            # never have to re-frame a byte stream. NOWAIT so every wait below is
            # ours to bound -- see the class docstring.
            (win32pipe.PIPE_TYPE_MESSAGE
             | win32pipe.PIPE_READMODE_MESSAGE
             | win32pipe.PIPE_NOWAIT),
            1,          # nMaxInstances
            _BUF,       # out buffer
            _BUF,       # in buffer -- must exceed any single message, else the
                        # client's write fails with error code 0 (Pipe.py:238)
            300,        # default timeout, ms
            None,       # security: system defaults
        )

    def close(self) -> None:
        if self._h is not None:
            _win32pipe, win32file, _winerror = _win32()
            try:
                win32file.CloseHandle(self._h)
            except Exception:  # silent-ok: closing a dead handle is not a finding
                pass
            self._h = None
            self._connected = False

    # -- connection --------------------------------------------------------- #

    def wait_for_game(self, timeout: float | None = None) -> None:
        """Block until the game connects, or refuse with a NON-ANSWER.

        A timeout here means nothing CONNECTED -- the game is not running, the helper is
        not deployed or not yet initialised (it starts on game load, not at the menu),
        its native pipe library failed to load, or its frame loop is not executing.
        `_no_connection_reason`
        tells those apart from what it can measure; do not collapse them to "the mod is
        not loaded". It is a DIFFERENT state from "connected but silent", which `ask`
        reports, and the two must never be merged.
        """
        win32pipe, _win32file, winerror = _win32()
        window = self.connect_timeout if timeout is None else timeout
        deadline = time.monotonic() + window
        while True:
            try:
                win32pipe.ConnectNamedPipe(self._h, None)
                self._connected = True
                return
            except Exception as exc:
                code = getattr(exc, "winerror", None)
                if code == winerror.ERROR_PIPE_CONNECTED:
                    self._connected = True
                    return
                if code not in (winerror.ERROR_PIPE_LISTENING, winerror.ERROR_NO_DATA):
                    raise LiveQueryDegraded(
                        f"the pipe {self.path} failed unexpectedly: {exc}"
                    ) from exc
            if time.monotonic() >= deadline:
                # THREE distinct states, and the middle one is the trap. Until
                # 2026-08-29 this said "not running, or not deployed" for ALL of
                # them -- so a game that was merely not executing read as a deployment
                # failure, and the correct response (restore the window, retry) looked
                # like the wrong one (go debug the mod).
                #
                # The cause is stated once, in MINIMIZED_HINT, and this comment
                # deliberately does NOT restate it. An earlier version of these two
                # texts disagreed about the evidence tier of the same claim -- this
                # comment said MEASURED where the hint said "INFERRED, not verified"
                # -- on the same code path. One source of truth, or they drift again.
                running = game_is_running()
                # The load marker is only consulted when the game IS running -- on a closed
                # game it could add nothing, and reading a log to describe a process that is
                # not there is how a stale file gets quoted as current state.
                loaded = helper_loaded_this_session() if running is True else None
                # Deployment is read on the same condition and for the same reason: describing
                # an install is only useful while there is a process it could have answered on.
                deployed, not_loaded = (helper_deployment() if running is not False
                                        else (None, None))
                # Same condition again: a window state is only worth reporting while there is
                # a process that owns one.
                minimized = game_is_minimized() if running is not False else None
                safe_mode = (helper_ui_safe_mode_at_load()
                             if running is True and loaded is True else None)
                raise LiveQueryUnavailable(
                    _no_connection_reason(self.path, window, running, loaded, deployed,
                                          minimized, not_loaded=not_loaded,
                                          ui_safe_mode_at_load=safe_mode))
            time.sleep(0.05)

    # -- exchange ----------------------------------------------------------- #

    def _read_raw(self, deadline: float) -> bytes:
        _win32pipe, win32file, winerror = _win32()
        while True:
            try:
                _err, data = win32file.ReadFile(self._h, _BUF)
                # RAW BYTES. Decoding here with errors="replace" repaired the frame
                # before anything had measured it, and the length/checksum clauses
                # then measured the repair. decode_reply decodes strictly, last.
                return data
            except Exception as exc:
                if getattr(exc, "winerror", None) not in (
                    winerror.ERROR_NO_DATA,
                    winerror.ERROR_PIPE_LISTENING,
                ):
                    raise LiveQueryDegraded(f"read failed: {exc}") from exc
            if time.monotonic() >= deadline:
                raise LiveQueryUnavailable(
                    f"the game connected to {self.path} but sent nothing within "
                    f"{self.timeout:.0f}s. The mod IS loaded -- so this is the "
                    f"game not EXECUTING, not a deployment problem. It is minimized in "
                    f"exclusive fullscreen, or hung: the poller stops with the frame loop. "
                    f"A PAUSED game is not a cause -- MEASURED 2026-09-13, it still answers."
                    + _frame_loop_text(game_is_minimized())
                )
            time.sleep(0.02)

    def ask(self, verb: str, *args: str) -> Reply:
        """Send one command and return its self-checked reply."""
        _win32pipe, win32file, _winerror = _win32()
        if not self._connected:
            self.wait_for_game()
        self._seq += 1
        msg = encode_command(self._seq, verb, tuple(args))
        encoded = msg.encode("utf-8")
        # CRASH CONTAINMENT: refuse an over-long request BEFORE writing it. The write is
        # what tears the pipe down (MAX_REQUEST_BYTES), so this must precede WriteFile.
        if len(encoded) > MAX_REQUEST_BYTES:
            raise LiveRequestTooLarge(
                f"refusing to send a {len(encoded)}-byte request for verb {verb!r}: the "
                f"request-size cap is {MAX_REQUEST_BYTES} bytes. A larger request tears the "
                f"pipe down on the game side (MEASURED teardown in (1997, 3998]); split it "
                f"into smaller calls."
            )
        try:
            win32file.WriteFile(self._h, encoded)
        except Exception as exc:
            raise LiveQueryUnavailable(
                f"could not write to {self.path}: {exc}. The game most likely "
                f"closed the pipe (a save/load or /reloadui destroys it SILENTLY, "
                f"with no signal to the mod)"
            ) from exc
        return decode_reply(self._read_raw(time.monotonic() + self.timeout), self._seq)
