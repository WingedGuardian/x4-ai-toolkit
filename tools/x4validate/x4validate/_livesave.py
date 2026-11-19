"""`x4live save SLOT`: ask the RUNNING game to write one save into an EMPTY standard slot.

USER DECISION 2026-11-18: this is a plain CLI capability, like `x4live query` and
`x4live equipment` ("we dont need to PROVE it was asked for. just make it a cli capability
like the other x4live capabilities"). So there is NO automation journal session and NO
AGENT_RECORDED_CONVERSATION authority here, and the save/load dispatcher's `decide`
requirement (`_livepersistdispatch`) does not apply to this command. That dispatcher, and
the automation bundle's persistence extension, are unchanged and separate.

What stays is NEVER-OVERWRITE, which is save protection rather than proof of asking:

  * exactly one slot, and only an EMPTY one of save_001..save_010
    (`_livepersistence.STANDARD_SAVE_SLOT`); no wildcard, no other name;
  * "empty" is `SlotVerifier.reserve`'s physical rule: no member of the save folder whose
    name starts with the slot name (a copy, a temp, another case) -- refused, exit 2,
    BEFORE anything is sent, and again inside the connection right before EVERY ask
    (connecting can wait up to 15 s for the game, and another run's save can land
    meanwhile). The helper repeats the check against the engine's own save list, which
    it reloads first (refusing `catalogue-loading` while it loads; this request is then
    asked again, same id, for up to CATALOGUE_WAIT_S), and against every slot it already
    sent SaveGame for since the UI loaded;
  * before sending, a manifest of every file in the save folder (size, mtime and sha256);
    after the reply, the new save must exist as a complete file carrying this request's
    label, and every other file must be unchanged -- any change, removal or extra file
    exits 3 and is named;
  * a LOST reply is resolved by reading the slot, never by sending again.

The pipe is not authenticated: any program running as the same Windows user can ask the
helper for a save the same way. The helper itself refuses an occupied slot, so that is the
whole of what such a program can do through this verb.

Exit codes follow the other x4live commands: 0 the save landed and verifies; 1 the game
refused and nothing in the save folder changed (a helper older than 103 answering
`unknown verb: save` included: nothing ran, so there is no slot to wait for); 2 not asked
(bad slot, occupied slot, save folder unknown, game unreachable before sending); 3
anything untrusted or unverified.
"""
from __future__ import annotations

import gzip
import math
import stat
import sys
import time
import uuid
import zlib
from pathlib import Path
from xml.parsers import expat

from ._livepersistence import (PersistenceUnavailable, SlotVerifier, _file, _route,
                               STANDARD_SAVE_SLOT as SLOT)

#: Injected by tests; the real clock and sleep otherwise.
_clock = time.monotonic
_sleep = time.sleep

POLL_S = 0.5
MAX_MEMBERS = 4096
MAX_DEPTH = 8
MAX_FILE_BYTES = 4 * 1024 ** 3
#: The header must close within this many expanded bytes. MEASURED on one real save
#: (scratch copy, 2026-11-18): `</info>` ends inside the first 2 KB.
HEADER_MAX = 1024 * 1024
MAX_EXPANDED = 32 * 1024 ** 3

_BANNER = "!" * 78


class _HeaderDone(Exception):
    pass


class SaveIncomplete(Exception):
    """The slot file exists but is not (yet) a complete save. Keep waiting."""


def label_for(request_id: str) -> str:
    """The save name the helper gives the save. Unique per request, so a save found in the
    slot can be tied to THIS request rather than to anyone else's."""
    return "x4live-" + request_id


def slot_refusal(slot: str) -> str | None:
    if isinstance(slot, str) and SLOT.fullmatch(slot):
        return None
    return (f"save takes exactly one standard slot, save_001 to save_010 (got {slot!r}); "
            "no wildcard, quicksave, autosave or other name is accepted")


def banner(slot: str) -> str:
    return "\n".join((
        _BANNER,
        f"!! WRITE -- `x4live save {slot}` asks the RUNNING game to write a save.",
        "!!   Only an EMPTY standard slot is used; an existing save is never",
        "!!   overwritten, and every other file in the save folder is checked after.",
        "!!   The pipe is not authenticated: any program running as this Windows user",
        "!!   can request a save the same way (empty slots only).",
        "!!   A lost reply is resolved by reading the slot; the request is never resent.",
        _BANNER,
    ))


def snapshot(save_dir: Path) -> dict[str, dict]:
    """Every member of the save folder: files as size/mtime/sha256, folders by identity.

    Refuses (PersistenceUnavailable) a linked, hardlinked or noncanonical route and a folder
    past its bounds, rather than comparing less than it claims to."""
    _route(save_dir, directory=True)
    rows: dict[str, dict] = {}

    def walk(folder: Path, depth: int) -> None:
        if depth > MAX_DEPTH:
            raise PersistenceUnavailable("save folder depth limit")
        for p in sorted(folder.iterdir(), key=lambda q: q.name.casefold()):
            if len(rows) >= MAX_MEMBERS:
                raise PersistenceUnavailable("save folder member limit")
            rel = p.relative_to(save_dir).as_posix()
            st = p.lstat()
            if stat.S_ISDIR(st.st_mode):
                _route(p, directory=True)
                rows[rel] = {"dir": [st.st_dev, st.st_ino]}
                walk(p, depth + 1)
            else:
                f = _file(p, MAX_FILE_BYTES)
                rows[rel] = {"size": f["size"], "mtime_ns": f["stat"][3], "sha256": f["sha256"]}

    walk(save_dir, 0)
    return rows


def occupants(rows: dict[str, dict], slot: str) -> list[str]:
    """SlotVerifier.reserve's physical rule, applied to every member."""
    return [name for name in rows if SlotVerifier._related(name, slot)]


#: USER DECISION 2026-11-18: Steam rewrites this one file in the save folder, so a CHANGE
#: to it (exact name, any letter case, top level only) is reported, never a failure. Its
#: removal, a copy, a longer name or one in a subfolder is not excepted.
AUTOCLOUD = "steam_autocloud.vdf"


def is_autocloud(rel: str) -> bool:
    # ASCII first, then lower(): casefold() also maps look-alikes onto the name (U+017F
    # long s -> "s", U+FB05/U+FB06 ligatures -> "st"), which would except another file.
    return "/" not in rel and rel.isascii() and rel.lower() == AUTOCLOUD


def compare(before: dict[str, dict], after: dict[str, dict], new: str | None,
            notes: list[str] | None = None) -> list[str]:
    """Every difference except the one new save, each one named. A changed
    steam_autocloud.vdf goes to `notes` instead (user decision 2026-11-18)."""
    problems = []
    for name, row in before.items():
        if name not in after:
            problems.append(f"removed: {name}")
        elif after[name] != row:
            if is_autocloud(name) and notes is not None:
                notes.append(f"note: {name} changed (reported, not a failure: Steam "
                             "rewrites it; user decision 2026-11-18)")
            else:
                problems.append(f"changed: {name}")
    for name in after:
        if name not in before and name != new:
            problems.append(f"added: {name}")
    return problems


def _listing(save_dir: Path, slot: str) -> dict[str, tuple[int, int]]:
    out = {}
    for name in (slot + ".xml.gz", slot + ".xml"):
        p = save_dir / name
        try:
            st = p.lstat()
        except FileNotFoundError:
            continue  # silent-ok: the slot not existing yet is the state being polled.
        if stat.S_ISREG(st.st_mode):
            out[name] = (st.st_size, st.st_mtime_ns)
    return out


def read_save(path: Path) -> dict[str, str]:
    """The save's header, after checking the WHOLE file is complete.

    A `.gz` is decompressed to its end, so its CRC decides completeness: a save still being
    written raises SaveIncomplete. The header is parsed with expat, DTD and entities refused,
    and must close within HEADER_MAX bytes."""
    found: dict[str, str] = {}
    stack: list[str] = []
    parser = expat.ParserCreate()

    def start(name, attrs):
        stack.append(name)
        if len(stack) == 1:
            found["root"] = name
        if stack == ["savegame", "info", "save"]:
            found["save_name"] = attrs.get("name", "")
        if stack == ["savegame", "info", "game"]:
            found["game_id"] = attrs.get("id", "")

    def end(name):
        stack.pop()
        if stack == ["savegame"] and name == "info":
            raise _HeaderDone()

    def forbidden(*_):
        raise PersistenceUnavailable("DTD/entity declarations refused")

    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.StartDoctypeDeclHandler = parser.EntityDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden
    expanded, header_done, tail = 0, False, b""
    gz = path.name.endswith(".gz")
    try:
        with path.open("rb") as raw:
            stream = gzip.GzipFile(fileobj=raw, mode="rb") if gz else raw
            try:
                while chunk := stream.read(1024 * 1024):
                    expanded += len(chunk)
                    if expanded > MAX_EXPANDED:
                        raise PersistenceUnavailable("expanded byte limit")
                    if not header_done:
                        try:
                            parser.Parse(chunk, False)
                        except _HeaderDone:
                            header_done = True
                        if not header_done and expanded > HEADER_MAX:
                            raise PersistenceUnavailable("save header did not close")
                    tail = (tail + chunk)[-64:]
            finally:
                if stream is not raw:
                    stream.close()
    except (EOFError, gzip.BadGzipFile, zlib.error) as exc:
        raise SaveIncomplete(f"{path.name} is not a complete gzip yet ({exc})") from exc
    except expat.ExpatError as exc:
        raise SaveIncomplete(f"{path.name} header does not parse yet ({exc})") from exc
    if not header_done:
        raise SaveIncomplete(f"{path.name} ended before its header closed")
    if not gz and not tail.rstrip().endswith(b"</savegame>"):
        raise SaveIncomplete(f"{path.name} does not end with </savegame> yet")
    return found


def wait_for_slot(save_dir: Path, slot: str, *, timeout: float, settle: float):
    """Poll the SLOT ONLY until one complete save sits there, unchanged for `settle` s.

    Returns (name, header) or (None, last reason). Never sends anything."""
    deadline = _clock() + timeout
    last, since, why = None, None, "no file appeared in the slot"
    while True:
        now = _clock()
        names = _listing(save_dir, slot)
        if len(names) > 1:
            why = "the slot exists as both .xml and .xml.gz"
            last = None
        elif names:
            (name, sig), = names.items()
            if sig[0] == 0:
                why, last = f"{name} is still empty", None
            elif sig != last:
                last, since = sig, now
                why = f"{name} is still changing"
            elif now - since >= settle:
                try:
                    return name, read_save(save_dir / name)
                except SaveIncomplete as exc:
                    why, since = str(exc), now
                except (OSError, PersistenceUnavailable) as exc:
                    why, since = f"{name} could not be read ({exc})", now
        if now >= deadline:
            return None, why
        _sleep(POLL_S)


#: How long a `catalogue-loading` refusal (the helper reloaded the engine's save list and
#: it is still loading) is answered by asking again, every POLL_S. INFERRED bound: the
#: reload's duration in game is unmeasured; past it the refusal is reported (exit 1).
CATALOGUE_WAIT_S = 15.0

#: The helper dispatcher's reply to a verb it does not have (helper 102 and older for
#: `save`), sent before any verb function runs.
UNKNOWN_SAVE_VERB = "unknown verb: save"


def _ready(save_dir: Path, slot: str):
    """(manifest, None) when the folder reads safely and the slot is empty, else (None, why)."""
    try:
        rows = snapshot(save_dir)
    except (PersistenceUnavailable, OSError) as exc:
        return None, f"the save folder cannot be read safely ({exc})."
    taken = occupants(rows, slot)
    if taken:
        return None, (f"slot {slot} is occupied ({', '.join(taken)}); x4live save never "
                      "overwrites a save. Choose an empty slot.")
    return rows, None


def _catalogue_loading(reply, slot: str, label: str) -> bool:
    """THIS request's trusted refusal saying the save list is still loading (acted=no)."""
    from ._livecli import _parse_advisory
    if reply is None or reply.status != "ERR":
        return False
    adv = _parse_advisory(reply.fields)
    return bool(adv) and adv.get("verb") == "save" and adv.get("slot") == slot and \
        adv.get("label") == label and adv.get("acted") == "no" and \
        adv.get("reason") == "catalogue-loading"


def _refused(before: dict, save_dir: Path, out, why: str) -> int:
    """A refusal that ran nothing: 1 when the save folder is unchanged, else 3."""
    notes: list[str] = []
    try:
        problems = compare(before, snapshot(save_dir), None, notes)
    except (PersistenceUnavailable, OSError) as exc:
        problems = [f"the save folder could not be re-read ({exc})"]
    if problems:
        for p in problems:
            print(f"  {p}", file=out)
        print("\nUNTRUSTED: the game refused, but the save folder changed anyway (see above).",
              file=out)
        return 3
    for n in notes:
        print(f"  {n}", file=out)
    print(f"\nrefused, and nothing in the save folder changed ({why}).", file=out)
    return 1


def run(slot: str, open_pipe, save_dir: Path | None, *, settle_timeout: float,
        settle: float, out=None, err=None) -> int:
    """The whole command. `open_pipe()` is a context manager yielding a live pipe."""
    from . import _livepipe
    from ._livecli import _parse_advisory

    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err

    def refuse(msg: str, rc: int) -> int:
        print(f"error: {msg}", file=err)
        return rc

    why = slot_refusal(slot)
    if why:
        return refuse(why, 2)
    if not (math.isfinite(settle_timeout) and 0 < settle_timeout <= 3600):
        return refuse("--settle-timeout must be more than 0 and at most 3600 seconds", 2)
    if not (math.isfinite(settle) and 0.5 <= settle <= 60):
        return refuse("--settle must be between 0.5 and 60 seconds", 2)
    if save_dir is None:
        return refuse("the save folder is not configured: set X4_SAVES, or X4_PROFILE (its "
                      "save/ folder is used). Nothing was sent.", 2)
    before, why = _ready(Path(save_dir), slot)
    if why:
        return refuse(why + " Nothing was sent.", 2)

    request = uuid.uuid4().hex
    label = label_for(request)
    print(banner(slot), file=err)
    sent, reply, lost, path = False, None, None, "?"
    try:
        with open_pipe() as lp:
            path = lp.path
            deadline = _clock() + CATALOGUE_WAIT_S
            while True:
                # Re-taken right before EVERY ask: connecting can wait for the game (up to
                # 15 s), and another run's save can land in the slot meanwhile (4.3 review
                # D1). The manifest is re-taken with it, so a change made before the ask is
                # not blamed on it.
                before, why = _ready(Path(save_dir), slot)
                if why:
                    return refuse(why + (" Nothing more was sent: every earlier ask was "
                                         "refused, acting on nothing." if sent else
                                         " Nothing was sent."), 2)
                sent = True
                # Cleared first: if THIS ask's reply is lost, an earlier refusal must not
                # stand in for it (the rehearsal's lost-reply case caught exactly that).
                reply = None
                reply = lp.ask("save", slot, request)
                if not _catalogue_loading(reply, slot, label) or _clock() >= deadline:
                    break
                # This request's own trusted refusal, acted=no: the helper reloaded its save
                # list and it is still loading. Asking again is safe once the folder shows
                # nothing changed; otherwise the refusal path below reports it.
                try:
                    if compare(before, snapshot(Path(save_dir)), None, []):
                        break
                except (PersistenceUnavailable, OSError):
                    break
                _sleep(POLL_S)
    except (_livepipe.LiveQueryUnavailable, _livepipe.LiveQueryDegraded) as exc:
        if not sent:
            raise
        lost = exc
        print(f"!! the save request was sent, then its reply was lost ({exc}). It may have "
              "taken effect. It is NOT resent: the slot is read instead.", file=err)

    print(f"pipe    : {path}", file=out)
    print(f"verb    : save {slot}", file=out)
    print(f"label   : {label}", file=out)
    trusted_ok = None  # None = reply lost; True = trusted acted reply; False = untrusted
    if reply is not None:
        print(f"status  : {reply.status}", file=out)
        adv = _parse_advisory(reply.fields)
        if reply.status == "ERR" and reply.payload == UNKNOWN_SAVE_VERB:
            # The dispatcher answers this BEFORE it looks up any verb function
            # (live_query.lua, `unknown verb: `), so nothing ran: no slot to wait for.
            print(f"reply   : {reply.payload}", file=out)
            print("the helper running in the game does not know `save` (helper 103 or newer "
                  "is needed; a FRESH launch loads a new one). Nothing was run.", file=out)
            return _refused(before, Path(save_dir), out, "the helper has no save verb")
        if adv is None:
            print(f"reply   : {reply.payload}", file=out)
            print("UNTRUSTED: the reply does not lead with its `write=yes` advisory row (the "
                  "helper may be older than this toolkit). Reading the slot.", file=out)
            trusted_ok = False
        else:
            print(f"advisory: {reply.fields[0]}", file=out)
            for line in reply.fields[1:]:
                print(f"  {line}", file=out)
            matches = (adv.get("verb") == "save" and adv.get("slot") == slot
                       and adv.get("label") == label)
            acted = adv.get("acted")
            if matches and reply.status == "ERR" and acted == "no":
                return _refused(before, Path(save_dir), out, f"reason={adv.get('reason', '?')}")
            trusted_ok = matches and reply.status == "OK" and acted == "yes"
            if not trusted_ok:
                print(f"UNTRUSTED reply (reason={adv.get('reason', '?')}, acted={acted}): "
                      "the request may still have acted. It is never resent; reading the slot.",
                      file=out)

    name, header = wait_for_slot(Path(save_dir), slot, timeout=settle_timeout, settle=settle)
    try:
        after = snapshot(Path(save_dir))
    except (PersistenceUnavailable, OSError) as exc:
        print(f"\nUNVERIFIED: the save folder could not be re-read ({exc}).", file=out)
        return 3
    notes: list[str] = []
    problems = compare(before, after, name, notes)
    for p in problems + notes:
        print(f"  {p}", file=out)
    if name is None:
        print(f"\nUNVERIFIED: no complete save appeared in {slot} within {settle_timeout:g} s "
              f"({header}). Do not resend: the request may still complete. Read the slot "
              f"later; once a save lands there, `x4live save {slot}` refuses it as occupied.",
              file=out)
        return 3
    row = after.get(name, {})
    print(f"slot    : {name} size={row.get('size')} sha256={row.get('sha256')}", file=out)
    print(f"header  : root={header.get('root')} save name={header.get('save_name')} "
          f"game id={header.get('game_id')}", file=out)
    if header.get("root") != "savegame" or header.get("game_id") != "X4" or \
            header.get("save_name") != label:
        print(f"\nUNTRUSTED: the save in {slot} does not carry this request's label "
              f"{label} (or is not an X4 savegame). Do not resend.", file=out)
        return 3
    if problems:
        print("\nUNTRUSTED: the save landed, but other files in the save folder changed "
              "(listed above).", file=out)
        return 3
    reported = f"; {len(notes)} reported change(s) listed above" if notes else ""
    print(f"others  : {len(before) - len(notes)} other file(s) unchanged (size, mtime and "
          f"sha256){reported}", file=out)
    if trusted_ok is False:
        print("\nUNTRUSTED: the save landed and verifies, but the game's reply was not a "
              "trusted acknowledgement (see above).", file=out)
        return 3
    note = " The reply was lost; this was established by reading the slot." if lost else ""
    print(f"\nverified: {slot} holds this request's complete save and nothing else in the "
          f"save folder changed{' except the reported file above' if notes else ''}.{note}", file=out)
    return 0


__all__ = ["SLOT", "run", "snapshot", "compare", "occupants", "read_save", "wait_for_slot",
           "slot_refusal", "label_for", "banner", "SaveIncomplete"]
