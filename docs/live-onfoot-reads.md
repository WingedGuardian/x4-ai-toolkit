# On-foot legacy reads

The legacy player read reports occupiedship=0ULL and occupiedship_present=false
when no ship is occupied. It retains the separately observed player entity ID and
does not read ship fields, sector, class, position or rotation. Reporting that
absence does not issue zero into the component allowlist. A player entity is not
silently substituted for a ship.

The probe still reports its galaxy-wide enumeration. Its ship-sector branch
reports unavailable:no occupied ship without passing zero to a component getter.
containerprobe returns ABSENT for that prerequisite. A nil enumeration for a
nonzero ship stays nil; it is not relabelled as absence of a ship.

Native analogue READ: menu_docked.lua211–212 and 234–235 checks occupiedship~=0
before GetComponentData. In this helper, four occupied-ship getter calls appear:
probe, containerprobe, its later wire-rendering observation and player. The two
probe/player paths and containerprobe prerequisite are guarded; the later
wire-only observation does not pass the handle to an object getter.

MEASURED offline: the five new controls failed before the fix (080865ff38fa).
The initial correction passed 259 controls. Independent review caught a conflation
between a nil native list and no occupied ship; its separate flag plus regression
passed 260 controls, zero skips (93f0e427430f). The group includes the full legacy
Lua suite, raw evidence and new on-foot cases. These use a real Lua interpreter
with synthetic engine boundaries. No claim of zero in-game errors follows until
an approved run verifies the on-foot native control. BUILD 52fae046 identifies the
corrected query bytes; deployment and loaded-code confirmation remain separate.
