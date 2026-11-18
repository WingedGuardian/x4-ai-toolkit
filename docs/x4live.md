# x4live: asking the running game

`x4live` is the toolkit's link to a **running** X4. Every other tool reads files on disk;
`x4live` asks the engine itself what exists right now, what a macro's values resolved to,
and which extensions actually loaded, without quitting and relaunching for each question.

It is **experimental**. Use a throwaway save, not one you are playing. Its answers are
evidence, not truth: they have been wrong before.

## What it needs

1. **The helper mod**, which ships with the toolkit in `mods/x4_toolkit_helper/`.
2. **Mod Support APIs** (`ws_2042901274`, from Steam Workshop or Nexus). The helper
   (version 102 and later) declares it as a **required** dependency, in its manifest and in
   its UI addon. The live pipe is built on its named-pipe library.
3. **The game running**, with the helper enabled, for every command that talks over the
   pipe (`query`, `equipment`, `pausestate`, `pause`, `unpause`, `save`, `harvest`,
   `groundtruth`, `ramp`, `ffi-census`, `automation`). `save` needs helper version 103 or
   later.

Protected UI Mode (in the game's Extensions options) can stop the named-pipe library from
loading even when the helper's own Lua has loaded. The helper records the setting it saw at
load, so a failed connection can say so; the toolkit never changes that setting.

## Installing the helper

Copy `mods/x4_toolkit_helper/` into `{game}/extensions/`, or from `tools/x4validate` run:

```text
uv run python scripts/deploy-mod.py x4_toolkit_helper --apply
```

It refuses a wrong target and re-reads what it wrote. Then enable the helper and Mod
Support APIs in the game's extension list and start (or load) a game.

The pipe name defaults to `x4live`; set `X4_LIVE_PIPE` to use another. Every pipe command
also takes `--pipe` and `--timeout`.

While no `x4live` command is running, the helper retries the pipe at growing intervals (2 s,
up to 10 s) and logs `pipe idle: no client` to `debug.txt`; that is expected, not an error. A
command waits at least 15 s for the game to connect, so the first one after a quiet spell can
take up to about 10 s to start, and it prints a WARNING on stderr when the helper the game
loaded is a different build from the toolkit's copy.

Mod Support APIs 1.95 has a defect in its pipe library: closing a pipe can corrupt the game's
Lua state, which later crashes X4. The helper protects its own pipe connections from it (other
mods' pipes are not changed). `x4live query probe` reports `pipe_unref_guard=active` when that
protection is on. If it reports anything else (for example `inactive:` with a reason, after a
Mod Support APIs update, or `active;wrapper-replaced` when another mod replaced the pipe
function after the helper), it prints a WARNING: finish what you are doing with few commands and
restart X4 afterwards.

## The commands

Run `x4live <command> --help` for the full arguments; the
`x4-cli-reference` skill has every command's help text.

**Reading the running game (pipe):**

- `query <verb> [args]` asks one fixed question. Start with `query probe`: it reports the
  helper's build (is the game running the file on disk) and when the helper last loaded.
  Other verbs list objects, stations and ships, describe one component, read a macro's
  resolved values, and more; see `x4live query --help`.
- `equipment <ship-id>` reads one ship's fitted engines, shields, turrets and weapons.
  Details and limits: [the equipment reference](live-equipment.md).
- `pausestate` reads whether the game is paused and whether this channel made the pause.
  It changes nothing.
- `harvest` and `groundtruth` ask many questions in one connection and write the answers
  to a `.tsv` file (`--out`; by default under `$X4_MODS/_reports`); `ramp` measures how
  large one reply may be.
- `ffi-census` is disabled unless `X4_LIVE_ALLOW_FFI=1` is set. It asks, name by name,
  whether the engine exports each C function the game's own UI declares. It calls nothing.

**Reading what the game saved (no pipe):** `dump`, `extensions`, `errors`, `oracle`,
`archive` and `mappings` read the snapshot the helper writes into your profile's
`uidata.xml`. X4 overwrites that file on exit and it is only complete while the game is
closed; `archive` copies it out so it is not lost. `--file` reads another copy.

**The three write commands:** `pause` pauses the running game and refuses if it is already
paused; `unpause` undoes only a pause this channel made and refuses anyone else's. Both
print a warning before sending and report what the engine reads back afterwards. If a
reply is lost, the command may still have taken effect: run `pausestate` rather than
sending it again.

`save SLOT` asks the running game to write a save. **It can write a save, and nothing else
about it is gated:** by the user's decision of 2026-11-18 it is a plain command like `query`,
with no automation journal and no recorded approval. What it guarantees is that it never
overwrites one:

- `SLOT` is exactly one of `save_001` to `save_010`. Any other name, a wildcard, the
  quicksave or an autosave is refused before anything is sent.
- A slot is empty only when nothing in the save folder starts with its name (a save, a
  `- Copy`, a temp file, another letter case). An occupied slot exits 2 before anything is
  sent. The helper checks again against the game's own save list, and also refuses when the
  game says saving is not possible right now, an online game is active, or the save list is
  still loading.
- Before sending, it records the size, modification time and SHA-256 of every file in the
  save folder. After the reply it waits (`--settle-timeout`, default 180 s) until the slot
  holds a complete save (the whole gzip is read, so a save still being written is waited
  for) whose header name is this request's label, `x4live-<request id>`, which is also
  the name the game's save menu shows. It then checks every other file again.
- A lost reply is resolved by reading the slot, never by sending again. If no save appears
  in time, it exits 3 and says so. Look at the slot before trying again: once the save has
  landed, a later `save` of the same slot refuses it as occupied, while another slot would
  give you a second save. If the configured profile is not the one the game is running, the
  save lands in the game's own profile (still only in a slot its save list shows as empty)
  and this command reports exit 3.
- **The pipe is not authenticated.** Any program running as your Windows user can ask the
  helper for a save the same way. The helper itself refuses an occupied slot, so such a
  request can only fill an empty one.

The save folder is taken from `X4_SAVES`, or from `X4_PROFILE` (its `save` folder), and must
be the folder of the profile the running game uses: the command cannot tell from the game
which profile that is.

### Exit codes

Most `x4live` commands use one contract:

| code | meaning |
|---|---|
| 0 | answered, and the answer is clean (for `pause`/`unpause`: the engine reads back what was asked) |
| 1 | answered, and the answer is a finding: the engine reports no such object, refuses the question, or refuses the write and changed nothing |
| 2 | not answered: the game is not reachable, the input was invalid, or the snapshot is missing |
| 3 | answered badly: a degraded, malformed, partial or untrusted reply; never treat it as a pass |

`save` uses the same contract: 0 when the new save is complete, carries this request's
label, and every other file in the save folder is unchanged; 1 when the game refused and
nothing in the save folder changed; 2 for a bad or occupied slot, an unconfigured or
unreadable save folder, or a game that never connected (nothing was sent); 3 for any
changed, removed or extra file (each one named), a save with another label, a reply that
cannot be trusted, or no complete save within `--settle-timeout`.

`equipment` uses the same contract: 2 for a malformed ship ID (checked before anything is
sent), 1 only when the game answers the first request that the ship no longer exists, was
not issued in this session or is not a ship, and 3 for every other refusal and for a
partial or malformed capture.

`automation` has its own codes. `identity` exits 0 once the session is qualified, and
`query` exits 0 for an OK reply and 2 for any other reply. `prepare`, `execute` and
`status` exit 0 when the action is prepared or completed, 4 while it is still preparing or
running, 2 when it is refused, and 3 when it is uncertain or failed. Any command exits 2
when the game or session is unavailable, and 3 when a reply is degraded or an outcome is
uncertain (for example a failure after an action was dispatched). These are command outcomes only, not a verdict that anything happened in the
game.

## Ids and what a reply can prove

- An object id looks like `33556742ULL`; a sector token looks like `ID: 7479` (quote it:
  it contains a space). The helper only answers about ids it issued itself in the current
  UI session. A save load or an Alt+Enter graphics change reloads the UI, and every id
  issued before that must be fetched again.
- **An enumeration is not a census.** Ownerless objects belong to no faction and are
  invisible to owner queries, hidden factions are opt-in, and names and sectors are
  player knowledge, so many rows read Unknown while ids, positions and flags stay exact.
- A command that changes the game is acknowledged by the engine, which is not proof that
  the change finished or looked right.

## What it writes, and where it refuses to

- The helper writes your profile's `uidata.xml` (UI data, not the save), when asked the
  pause state, and, when `x4live save` asks, one new save into an empty standard slot.
  Its manifest declares `save="false"`, which only means the helper is not recorded in
  your saves; it does not mean it cannot write one.
- Commands that write evidence (`harvest`, `groundtruth`, `ffi-census`, `archive`) write
  to `--out`, by default under `$X4_MODS/_reports`; automation writes only to the journal
  folder you pass with `--journal`.
- A **journal** folder (automation, save/load, audio, capture, clips, dialogue) is refused
  when it is inside, or contains, the game install, the reference folder or your Egosoft
  profiles folder. The check also compares the folders by identity, so another spelling
  of the same folder is caught. A protected folder that is not configured cannot be
  checked; the tools say which ones on stderr. Configure them in `x4-paths.env`.
  If a configured protected folder exists but its identity cannot be read (for example,
  access is denied), every journal is refused, naming that folder, until it is reachable.
- **Two things write a save**: `x4live save` (above), and the save/load dispatcher, a
  Python interface (no command). Both write only to an empty standard slot `save_001` to
  `save_010`, never retry on their own, and only read existing saves to verify them. The
  dispatcher also requires its authority to record an explicit decision for that exact
  operation; `x4live save` does not (user decision 2026-11-18).

## The optional automation bundle

`x4live automation` (`identity`, `query`, `prepare`, `execute`, `status`) drives prepared
actions in the game: menus, orders, camera, game speed and settings, and optional
extensions such as diagnostic placement, room entry, trader menus, screen capture and
save/load. Usage: [the automation reference](live-automation-cli.md).

**The helper as shipped does not load it.** These commands are refused ("automation bundle
not loaded") until a helper composed with `scripts/prepare_live_helper.py` is installed.
That script only returns the files; the toolkit has no command that writes or installs a
composed helper. Nothing about it has been qualified in game.

Before you compose one, know its limits:

- **Anyone on your account can use it.** The pipe does not authenticate its client: any
  program running as the same Windows user can connect and request any action the
  installed extensions offer. The save/load gate in the game only checks that a grant is
  well formed and unused; whether a save or load is allowed is decided on the toolkit side.
- **Some actions change the game for good.** Menu actions can buy or modify equipment and
  ships. An attack order may target any object the player does not own and can make its
  owner hostile, which persists in the save. Options actions change the game's settings.
- **One session per UI load.** Each action provider binds to the first session it sees; a
  later fresh session leaves them refusing until the UI reloads (a save load does that).
- **No upgrade path into older saves.** The helper's MD state is stored in the save, so a
  later helper that changes it cannot reach a save made with this one.
- **A stalled ship placement locks that provider** until the next game start or load.
- **A failed order creation is rolled back.** If a check fails after the helper creates
  an order and before it enables it, the order is removed again in the same step and the
  action reports failed; if that removal fails, the result says the order is left in the
  queue and at which index.
- Every action is prepared once and executed once. A lost reply is reconciled with
  `status`, never resent.

## Host capabilities without a command

The toolkit also has Python interfaces, with offline tests only and no `x4live` command:
capturing the game's audio through a native helper you compile yourself with MSVC
(`tools/native_audio`), short window clips through an FFmpeg you supply, bringing the X4
window to the front, pressing dialogue number keys, and the save/load dispatcher above.
The optional `capture` extra installs Pillow for image decoding.

## More

- [Equipment capture reference](live-equipment.md)
- [Automation command reference](live-automation-cli.md)
- The `x4-live` skill: the traps to know before trusting a live answer.
- The development and qualification records behind these features are in the toolkit's
  git repository under `docs/live-dev/`; they are not part of the release download.
