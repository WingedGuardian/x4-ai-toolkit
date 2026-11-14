"""`x4effective card <macro>`: the facts that classify one equipment macro, each with its source.

WHY IT EXISTS. On 2026-11-14 a session labelled a turret "point defence" from the stat shape of its
bullet, at 70% confidence. In game it is an artillery turret, weapon system `turret_longrange`. Every
fact that answered the question was already in the store -- the in-game name and description, the
bullet the turret fires and that bullet's `weapon.system`, the ware -- but three lookups apart, and they
were skipped. Joining them by hand through `sql` then hit text rows containing TABs.

So the card does the joins, and every value carries the source that WON it (an attribute's `origin`,
never re-derived) and whether that source is a mod (the store's `mods` table) or vanilla (base or a DLC).

THREE STATES, NEVER A BLANK. Each field is one of:
  * ``ok``         -- resolved; ``value`` holds it.
  * ``absent``     -- the store holds the thing asked about and it has no such value (a MEASURED absence
                      over that entity, e.g. a shield has no weapon system).
  * ``unresolved`` -- a reference the store cannot follow (a bullet macro it does not hold, a dangling
                      `{page,id}`, a kind the build did not index): a NON-ANSWER, not evidence of absence.

Text is free text from t-files and can hold TABs, newlines and backslashes (MEASURED: 4 / 52 / 2,591 of
80,533 texts rows), so the text form prints every text as one JSON string literal and every identifier
through `tsv_cell`; `--json` carries the exact values.

Nothing here can change a merged value: this module is display only, and is not an ENGINE_SOURCE.
"""
from __future__ import annotations

import json
import re

from x4validate import _text

SCHEMA = "x4effective.card/1"
FIELDS = ("name", "description", "system", "fires", "fitting", "ware", "price", "carried_by")
#: Size tokens a connection's tags use, smallest first.
SIZES = ("extrasmall", "small", "medium", "large", "extralarge")
_CONN_TAGS = re.compile(r"^connection\[(.+)\]\.tags$")
_LABEL = {"name": "name", "description": "description", "system": "system", "fires": "fires",
          "fitting": "fitting", "ware": "ware", "price": "price", "carried_by": "carried by"}


def _esc(value) -> str:
    """An identifier on one line: TAB, newline, CR and backslash escaped as `tsv_cell` does."""
    from x4validate._effectivecli import tsv_cell
    return "" if value is None else tsv_cell(value)


def _quote(text: str) -> str:
    """Free text as one JSON string literal: quoted, with every control character escaped."""
    return json.dumps(text, ensure_ascii=False)


class _Card:
    def __init__(self, con, ent):
        self.con = con
        self.ent = ent
        self.mods = {r[0] for r in con.execute("SELECT folder FROM mods")}
        self.kinds = {r[0] for r in con.execute("SELECT DISTINCT kind FROM entities")}
        self.attrs = self._attrs(ent["id"])
        self._texts = None

    # ------------------------------------------------------------------ helpers
    def _attrs(self, eid) -> dict[str, tuple[str, str]]:
        return {r[0]: (r[1], r[2]) for r in self.con.execute(
            "SELECT prop, value, origin FROM attrs WHERE entity_id=? ORDER BY rowid", (eid,))}

    def _src(self, origin: str | None) -> dict:
        return {"origin": origin, "set_by": "mod" if origin in self.mods else "vanilla"}

    def _ok(self, value, origin=None, **extra) -> dict:
        out = {"status": "ok", "value": value}
        if origin is not None:
            out.update(self._src(origin))
        out.update(extra)
        return out

    @staticmethod
    def _no(status: str, why: str, **extra) -> dict:
        return {"status": status, "value": None, "why": why, **extra}

    def _entity(self, kind: str, name: str):
        return self.con.execute("SELECT * FROM entities WHERE kind=? AND name=? ORDER BY id LIMIT 1",
                                (kind, name)).fetchone()

    def _resolver(self):
        if self._texts is None:
            texts = _text.load(self.con)
            self._texts = _text.Resolver(texts) if texts else False
        return self._texts

    # ------------------------------------------------------------------ fields
    def text(self, prop: str) -> dict:
        if prop not in self.attrs:
            return self._no("absent", f"the macro carries no {prop}")
        raw, origin = self.attrs[prop]
        res = self._resolver()
        if not res:
            return self._no("unresolved", "this store has no display-text table; rebuild with "
                            "`uv run x4effective build`", raw=raw, prop=prop, **self._src(origin))
        shown, status = res.value(raw or "")
        common = {"raw": raw, "prop": prop, **self._src(origin),
                  "text_provenance": "not recorded: the texts table keeps the merged text, not who set it"}
        if status not in ("ok", "literal"):
            return {"status": "unresolved", "value": None, "text": None,
                    "why": f"{raw} is a {status} text reference", **common}
        return {"status": "ok", "value": shown, "text": shown, **common}

    def fires(self) -> dict:
        extra = {}
        if "ammunition.tags" in self.attrs:
            v, o = self.attrs["ammunition.tags"]
            extra["ammunition_tags"] = {"value": v, **self._src(o)}
        if "bullet.class" not in self.attrs:
            return self._no("absent", f"class {self.ent['klass']} macro carries no bullet.class", **extra)
        v, o = self.attrs["bullet.class"]
        return self._ok(v, o, prop="bullet.class", **extra)

    def system(self, fires: dict) -> dict:
        if "weapon.system" in self.attrs:
            v, o = self.attrs["weapon.system"]
            return self._ok(v, o, prop="weapon.system")
        if fires["status"] != "ok":
            return self._no("absent", f"class {self.ent['klass']} carries no weapon.system and no "
                            "bullet.class, so the store holds no role field for it")
        bullet = fires["value"]
        b = self._entity("macro", bullet)
        if b is None:
            return self._no("unresolved", f"its bullet macro {bullet} is not in the store, so its "
                            "weapon.system cannot be read", via=bullet)
        battrs = self._attrs(b["id"])
        if "weapon.system" not in battrs:
            return self._no("absent", f"its bullet macro {bullet} carries no weapon.system", via=bullet)
        v, o = battrs["weapon.system"]
        return self._ok(v, o, prop="weapon.system", via=bullet)

    def fitting(self) -> dict:
        if "component.ref" not in self.attrs:
            return self._no("absent", "the macro carries no component.ref")
        cref = self.attrs["component.ref"][0]
        comp = self._entity("component", cref)
        if comp is None:
            why = (f"component {cref} is not in the store" if "component" in self.kinds
                   else "this store did not index kind 'component'")
            return self._no("unresolved", why, component=cref)
        plugs = []
        for prop, (tags, origin) in self._attrs(comp["id"]).items():
            m = _CONN_TAGS.match(prop)
            if not m or "component" not in (tags or "").split():
                continue
            size = next((s for s in SIZES if s in tags.split()), None)
            plugs.append({"connection": m.group(1), "tags": tags, "size": size, **self._src(origin)})
        if not plugs:
            return self._no("absent", f"no connection of component {cref} is tagged 'component' (the "
                            "connection that plugs the item into a slot)", component=cref)
        return {"status": "ok", "value": plugs, "component": cref}

    def wares(self) -> list:
        return self.con.execute(
            "SELECT e.id, e.name, a.origin FROM attrs a JOIN entities e ON e.id=a.entity_id "
            "WHERE e.kind='ware' AND a.prop='component.ref' AND a.value=? ORDER BY e.id",
            (self.ent["name"],)).fetchall()

    def ware(self, wares) -> dict:
        if "ware" not in self.kinds:
            return self._no("unresolved", "this store did not index kind 'ware'")
        if not wares:
            return self._no("absent", "no ware's component.ref names this macro")
        w = wares[0]
        extra = {"also": [r["name"] for r in wares[1:]]} if len(wares) > 1 else {}
        return self._ok(w["name"], w["origin"], **extra)

    def price(self, ware: dict, wares) -> dict:
        if ware["status"] != "ok":
            return self._no(ware["status"], "no ware, so no price" if ware["status"] == "absent"
                            else ware["why"])
        attrs = self._attrs(wares[0]["id"])
        got = {k: {"value": attrs[f"price.{k}"][0], **self._src(attrs[f"price.{k}"][1])}
               for k in ("min", "average", "max") if f"price.{k}" in attrs}
        if not got:
            return self._no("absent", f"ware {ware['value']} carries no price")
        return {"status": "ok", "value": got}

    def carried_by(self) -> dict:
        if "loadout" not in self.kinds and "macro" not in self.kinds:
            return self._no("unresolved", "this store indexed neither loadouts nor macros")
        rows = self.con.execute(
            "SELECT e.id, e.kind, e.name, e.klass, a.prop, a.origin FROM attrs a "
            "JOIN entities e ON e.id=a.entity_id WHERE a.value=? AND e.id != ? ORDER BY e.kind, e.name, e.id",
            (self.ent["name"], self.ent["id"])).fetchall()
        carriers: dict[tuple, dict] = {}
        others: dict[str, int] = {}
        for r in rows:
            if r["kind"] == "ware" and r["prop"] == "component.ref":
                continue                                    # that is the ware field, not a carrier
            is_ship = r["kind"] == "macro" and (r["klass"] or "").startswith("ship")
            if r["kind"] != "loadout" and not is_ship:
                others[r["kind"]] = others.get(r["kind"], 0) + 1
                continue
            key = (r["kind"], r["name"], r["id"])
            c = carriers.setdefault(key, {"kind": r["kind"], "name": r["name"], "slots": 0, "_o": set()})
            c["slots"] += 1
            c["_o"].add(r["origin"])
        out = []
        for (kind, _name, eid), c in carriers.items():
            origins = sorted(c.pop("_o"))
            entry = {"kind": kind, "name": c["name"]}
            if kind == "loadout":
                ship = self.con.execute("SELECT value FROM attrs WHERE entity_id=? AND prop='@macro'",
                                        (eid,)).fetchone()
                entry["ship"] = ship[0] if ship else None
            entry["slots"] = c["slots"]
            entry["origin"] = ",".join(o or "?" for o in origins)
            entry["set_by"] = "mod" if any(o in self.mods for o in origins) else "vanilla"
            out.append(entry)
        if not out:
            return {"status": "absent", "value": [], "others": others,
                    "why": "no loadout (libraries/loadouts.xml) and no ship macro names this macro"}
        return {"status": "ok", "value": out, "others": others}

    def build(self) -> dict:
        fires = self.fires()
        wares = self.wares()
        ware = self.ware(wares)
        fields = {"name": self.text("identification.name"),
                  "description": self.text("identification.description"),
                  "system": self.system(fires), "fires": fires, "fitting": self.fitting(),
                  "ware": ware, "price": self.price(ware, wares), "carried_by": self.carried_by()}
        e = self.ent
        return {"schema": SCHEMA, "macro": e["name"], "class": e["klass"], "vpath": e["vpath"],
                "entity_origin": e["origin"], "fields": {k: fields[k] for k in FIELDS},
                "not_resolved": [k for k in FIELDS if fields[k]["status"] == "unresolved"]}


def card(con, ent) -> dict:
    """The card for one stored macro entity (a row of `entities`)."""
    return _Card(con, ent).build()


# ---------------------------------------------------------------------- rendering

def _tag(f: dict) -> str:
    if f.get("set_by") == "mod":
        return f"[mod {_esc(f['origin'])}]"
    if f.get("origin") is None:
        return ""
    return f"[no override, still vanilla: {_esc(f['origin'])}]"


def render(doc: dict, limit: int) -> list[str]:
    lines = [f"card macro {_esc(doc['macro'])}  class={_esc(doc['class'])}  vpath={_esc(doc['vpath'])}"]
    for key in FIELDS:
        f = doc["fields"][key]
        label = f"  {_LABEL[key]:<12} "
        if f["status"] != "ok" and key != "carried_by":
            word = "NOT RESOLVED" if f["status"] == "unresolved" else "ABSENT"
            raw = f"  {f['raw']}" if f.get("raw") else ""
            lines.append(f"{label}{word} -- {_esc(f['why'])}{_esc(raw)}")
            continue
        if key in ("name", "description"):
            lines.append(f"{label}{_quote(f['text'])}  {_esc(f['raw'])}  {_tag(f)}")
        elif key == "system":
            via = f"  (weapon.system of its bullet {_esc(f['via'])})" if f.get("via") else "  (weapon.system)"
            lines.append(f"{label}{_esc(f['value'])}{via}  {_tag(f)}")
        elif key == "fires":
            ammo = f.get("ammunition_tags")
            tail = f"  ammunition tags {_quote(ammo['value'])} {_tag(ammo)}" if ammo else ""
            lines.append(f"{label}{_esc(f['value'])}  (bullet.class)  {_tag(f)}{tail}")
        elif key == "fitting":
            for i, p in enumerate(f["value"]):
                size = f"size={_esc(p['size'])}" if p["size"] else "size NOT STATED in its tags"
                lines.append(f"{label if i == 0 else ' ' * len(label)}{_esc(p['connection'])}  {size}  "
                             f"tags={_quote(p['tags'])}  {_tag(p)}")
        elif key == "ware":
            also = f"  (also: {', '.join(_esc(w) for w in f['also'])})" if f.get("also") else ""
            lines.append(f"{label}{_esc(f['value'])}{also}  {_tag(f)}")
        elif key == "price":
            parts = [f"{k} {_esc(v['value'])} {_tag(v)}" for k, v in f["value"].items()]
            lines.append(label + " / ".join(parts))
        elif key == "carried_by":
            lines.extend(_render_carriers(label, f, limit))
    nr = doc["not_resolved"]
    lines.append("")
    lines.append(f"{len(nr)} of {len(FIELDS)} field(s) NOT RESOLVED" + (f": {', '.join(nr)}" if nr else "")
                 + "; ABSENT is a measured absence on the stored entity, NOT RESOLVED is a non-answer.")
    lines.append("origin = the source that WON each value, not necessarily the one that introduced it (F64); "
                 "`x4effective who-sets macro <name> <prop>` gives the chain. Text content carries no "
                 "provenance in the store.")
    return lines


def _render_carriers(label: str, f: dict, limit: int) -> list[str]:
    cars = f["value"]
    loadouts = [c for c in cars if c["kind"] == "loadout"]
    ships = {c["ship"] for c in loadouts if c.get("ship")}
    direct = [c for c in cars if c["kind"] != "loadout"]
    others = ", ".join(f"{_esc(k)} {n}" for k, n in sorted(f.get("others", {}).items())) or "none"
    head = (f"{label}{len(loadouts)} loadout(s) on {len(ships)} ship macro(s); {len(direct)} ship macro(s) "
            f"name it directly; other references: {others}")
    if f["status"] != "ok":
        return [f"{label}ABSENT -- {_esc(f['why'])}; other references: {others}"]
    out = [head]
    shown = cars if limit <= 0 else cars[:limit]
    for c in shown:
        ship = f"  ship={_esc(c['ship'])}" if c["kind"] == "loadout" else ""
        out.append(f"      {c['kind']:<8} {_esc(c['name'])}{ship}  slots={c['slots']}  {_tag(c)}")
    if len(shown) < len(cars):
        out.append(f"      ... showing {len(shown)} of {len(cars)} carrier(s); --limit 0 for all")
    return out
