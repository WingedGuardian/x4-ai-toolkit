"""Pure helper composition proposal. Never writes mod, profile or installed files."""
from __future__ import annotations
import hashlib
import importlib.util
from pathlib import Path
from lxml import etree as E

HERE=Path(__file__).resolve().parent
PINS={}


def sha(data):return hashlib.sha256(data).hexdigest()


def _load(name,pins=None):
    path=HERE/(name+".py");before=path.read_bytes()
    spec=importlib.util.spec_from_file_location("helper_candidate_"+name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    if path.read_bytes()!=before:raise ValueError("generator changed during import")
    (PINS if pins is None else pins)[path]=sha(before)
    return module


BUNDLE=_load("live_automation_bundle")
SESSION=_load("live_session_md")
EXTENSIONS={
    "orbit":("live_orbit_extension", "ui/live_orbit_extension.lua",
             {"ui/live_orbit_extension.lua", "md/X4ToolkitOrbit.xml"}),
    "capture":("live_capture_extension", "ui/live_capture_extension.lua",
               {"ui/live_capture_extension.lua", "md/X4ToolkitCapture.xml"}),
    "placement":("live_placement_extension", "ui/live_placement_extension.lua",
                 {"ui/live_placement_extension.lua", "md/X4ToolkitPlacement.xml", "md/X4ToolkitShipPlacement.xml"}),
    "roomentry":("live_roomentry_extension", "ui/live_roomentry_extension.lua",
                 {"ui/live_roomentry_extension.lua", "md/X4ToolkitRoomEntry.xml"}),
    "tradertext":("live_tradertext_extension", "ui/live_tradertext_extension.lua",
                  {"ui/live_tradertext_extension.lua"}),
    "rooms":("live_rooms_extension", "ui/live_rooms_extension.lua",
             {"ui/live_rooms_extension.lua"}),
    "topology":("live_topology_extension", "ui/live_topology_extension.lua",
                {"ui/live_topology_extension.lua", "md/X4ToolkitTopology.xml"}),
    "conversation":("live_conversation_extension", "ui/live_conversation_extension.lua",
                    {"ui/live_conversation_extension.lua", "md/X4ToolkitConversation.xml"}),
    "persistence":("live_persistence_extension", "ui/x4_persistence_extension.lua",
                   {"ui/x4_persistence_extension.lua"}),
    "surface":("live_surface_extension", "ui/live_surface_extension.lua",
               {"ui/live_surface_extension.lua", "md/X4ToolkitSurface.xml"}),
}
DESCRIPTION=(
    "EXPERIMENTAL: use a disposable test world or save. Companion for the X4 community toolkit. "
    "Legacy queries read engine observations; explicit pause/unpause commands change pause state. "
    "The engine snapshot probe writes profile UI userdata. Bound automation adds MD session variables "
    "and blackboard queues, native menu interaction, order submission/cancellation, camera and SETA control. "
    "Menu actions can purchase or modify equipment and ships. Reviewed installed diagnostic providers "
    "may declare additional actions; inspect their scope before enabling them. Actions require explicit "
    "preparation and execution through a session-bound journal. Acknowledgement is not proof of gameplay "
    "completion. An uncertain action must be reconciled, never automatically replayed. save=false does "
    "not mean executed actions cannot persist in a saved world. The fixed command interfaces expose no "
    "arbitrary Lua/MD evaluation. Object IDs expire with the UI/session; enumerations are not necessarily "
    "complete censuses. Mod Support APIs is a required dependency of the extension and its UI addon; "
    "the live pipe transport needs it. This automation composition is an offline candidate awaiting runtime qualification."
)


def build(manifest:bytes,ui:bytes,sources:dict[str,bytes],*,extensions=(),capacity_profile="interactive"):
    """Return candidate bytes only; optional fixed extensions load before construction."""
    if (not isinstance(extensions,tuple) or
        any(not isinstance(name,str) or name not in EXTENSIONS for name in extensions) or
        len(set(extensions))!=len(extensions)):
        raise ValueError("unique fixed extension names as a tuple required")
    _,capacity=BUNDLE.capacity_contract(capacity_profile)
    build_pins={**PINS,Path(__file__):sha(Path(__file__).read_bytes()),
                BUNDLE.CAPACITY_SOURCE:capacity['source_raw_sha256']}
    for path,digest in PINS.items():
        if sha(path.read_bytes())!=digest:raise ValueError("consumed generator source changed")
    parser=E.XMLParser(resolve_entities=False,no_network=True,remove_blank_text=False)
    content=E.fromstring(manifest,parser)
    addon=E.fromstring(ui,parser)
    if content.tag!="content" or content.get("id")!="x4_toolkit_helper":
        raise ValueError("exact helper manifest required")
    if addon.tag!="addon" or addon.get("name")!="x4_toolkit_helper":
        raise ValueError("exact helper addon required")
    environments=addon.findall("environment")
    if len(environments)!=1 or environments[0].get("type")!="menus":
        raise ValueError("one native menus environment required")
    env=environments[0]
    files=env.findall("file")
    target=[node for node in files if node.get("name")=="ui/live_query.lua"]
    owned_loaders={"ui/live_automation_bundle.lua",*(item[1] for item in EXTENSIONS.values())}
    if len(target)!=1 or any(node.get("name") in owned_loaders for node in files):
        raise ValueError("unambiguous uncomposed helper loader required")
    content.set("description",DESCRIPTION)
    generated=E.Element("file",name="ui/live_automation_bundle.lua")
    generated.tail=target[0].tail
    env.insert(env.index(target[0]),generated)
    for node in env:
        if isinstance(node,E._Comment) and "TWO files, one addon." in (node.text or ""):
            node.text=" Generated own-code automation bundle loads before the legacy pipe and snapshot probe. "
    md,session=SESSION.prepare()
    lua,bundle=BUNDLE.build(sources,session["revision"],capacity_profile=capacity_profile)
    out={"content.xml":E.tostring(content,encoding="utf-8",xml_declaration=True),
         "ui.xml":E.tostring(addon,encoding="utf-8",xml_declaration=True),
         "ui/live_automation_bundle.lua":lua,**md}
    extension_receipts={}
    extension_input_pins={}
    # Canonical ordering, regardless of the caller's request order. Each factory
    # is own-code and returns bytes; there is no generic arbitrary-loader input.
    for name,(factory,loader,expected) in EXTENSIONS.items():
        if name not in extensions:
            continue
        module=_load(factory,build_pins)
        if name=="persistence":
            raw={key:(HERE.parent/path).read_bytes() for key,path in module.SOURCES.items()}
            extension_input_pins.update({HERE.parent/module.SOURCES[key]:sha(value) for key,value in raw.items()})
            extra,receipt=module.build(raw)
            if any((HERE.parent/module.SOURCES[key]).read_bytes()!=value for key,value in raw.items()):
                raise ValueError("persistence source changed during composition")
            receipt={**receipt,"input_sha256":{module.SOURCES[key]:sha(value) for key,value in raw.items()}}
        else:
            extension_input_pins.update({module.ROOT/path:sha((module.ROOT/path).read_bytes())
                                         for path in module.PATHS.values()})
            extra,receipt=module.prepare(**({'capacity_profile':capacity_profile}
                                          if name in {'orbit','capture','roomentry','placement','surface'} else {}))
            if name=='placement':
                receipt={**receipt,'providers':{receipt['provider']:receipt['revision'],
                                                receipt['ship_provider']:receipt['revision']}}
        if set(extra)!=expected or any(not isinstance(value,bytes) for value in extra.values()):
            raise ValueError("unexpected extension candidate set")
        if set(extra)&set(out):
            raise ValueError("extension candidate collision")
        node=E.Element("file",name=loader);node.tail=target[0].tail
        env.insert(env.index(target[0]),node)
        out.update(extra)
        extension_receipts[name]=receipt
    if extension_receipts:
        disclosure=DESCRIPTION
        if "conversation" in extension_receipts:
            disclosure+=" The conversation extension records dialogue state and can invoke native comm menu actions."
        if "persistence" in extension_receipts:
            disclosure+=" The persistence extension can save/load only through separately authorized host dispatch; inclusion does not grant save access."
        if "topology" in extension_receipts:
            disclosure+=" The topology extension observes bounded native normal-cluster queries; returned IDs are observations only and captures are not atomic."
        if "rooms" in extension_receipts:
            disclosure+=" The room extension can request native transport only from an existing engine-opened transporter menu; it cannot open that menu or prove arrival."
        if "tradertext" in extension_receipts:
            disclosure+=" The trader/text extension can purchase licences or blueprints through an already-built trader menu; it cannot open the trader menu or verify purchase completion. It can open and navigate native encyclopedia and owned-property views, close those views, and read bounded built text/icon descriptors; these observations do not verify rendered pixels or a complete owned-property census."
            disclosure+=" It can observe prioritized current-room NPCs, select an observed actor as soft target, and request an eligible native comm or instant-root action; conversation identity, offered sections, recruitment and rendered-menu outcomes require separate observations. CORE instant interaction refuses when its native APIs are unavailable, without fallback."
        if "placement" in extension_receipts:
            disclosure+=" Diagnostic placement can instantly teleport the player to helper-issued player-owned ships, bypassing teleport research; it does not prove piloting, ordinary boarding, docking or walking."
            disclosure+=" It can also warp a helper-issued player-owned ship to a bounded diagnostic pose in its prepared current sector; only a later native pose observation can establish a scoped match, with no flight or collision-safety claim."
        if "roomentry" in extension_receipts:
            disclosure+=" Diagnostic room entry can teleport and position the player at an observed room waypoint, including private rooms; this bypasses ordinary access and proves neither walking nor rendered geometry."
        if "capture" in extension_receipts:
            disclosure+=" Native capture writes screenshots to a fixed subdirectory; its parent location and visual contents require independent observation, and release only clears capture bookkeeping."
        if "orbit" in extension_receipts:
            disclosure+=" Owned camera cutscenes can temporarily replace the player view; requested camera parameters do not establish measured world pose or target distance."
        if "surface" in extension_receipts:
            disclosure+=" Native surface sampling reads one bounded object-relative ray result on an issued owned stationary ship. A returned point or null hit establishes neither inside-hull membership nor complete collision coverage, safety or rendered geometry."
        content.set("description",disclosure)
        out["content.xml"]=E.tostring(content,encoding="utf-8",xml_declaration=True)
        out["ui.xml"]=E.tostring(addon,encoding="utf-8",xml_declaration=True)
    for path,digest in build_pins.items():
        if sha(path.read_bytes())!=digest:raise ValueError("generator changed during composition")
    for path,digest in extension_input_pins.items():
        if sha(path.read_bytes())!=digest:raise ValueError("extension source changed during composition")
    return out,dict(status="OFFLINE_COMPOSITION_PROPOSAL_NOT_APPLIED",runtime_verified=False,
        inputs={"content.xml":sha(manifest),"ui.xml":sha(ui),
                **{BUNDLE.SOURCES[k]:sha(v) for k,v in sources.items()}},
        generators={path.name:digest for path,digest in build_pins.items()},
        outputs={path:sha(data) for path,data in out.items()},bundle=bundle,session=session,
        extensions=extension_receipts,
        changes=["Disclose explicit native automation and MD state effects",
                 "Load the source-bound bundle before the existing live query helper",
                 "Add the public session MD provider"],
        excluded=["private diagnostic loaders",
                  *([] if "capture" in extensions else ["visual capture provider"]),
                  *([] if "persistence" in extensions else ["save/load action integration"]),
                  "save access authorization","installation","profile edits","game launch"],
        version_policy="Existing manifest version retained; release versioning remains separate")
