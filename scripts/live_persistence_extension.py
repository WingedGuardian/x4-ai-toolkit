"""Pure source-bound persistence extension loader; no game data or output writes."""
from __future__ import annotations
import hashlib
from pathlib import Path
import re

SOURCES={name:f"mods/x4_toolkit_helper/ui/live_{name}.lua" for name in
         ("persistence","persistencegate","persistenceprovider")}
ID="x4-persistence-v1"

def build(sources):
    if set(sources)!=set(SOURCES) or any(not isinstance(v,bytes) for v in sources.values()):
        raise ValueError("exact persistence source bytes required")
    normalized={k:v.replace(b"\r\n",b"\n") for k,v in sources.items()}
    pins={k:hashlib.sha256(v).hexdigest() for k,v in normalized.items()}
    factory_bytes=Path(__file__).read_bytes()
    factory_sha=hashlib.sha256(factory_bytes.replace(b"\r\n",b"\n")).hexdigest()
    revision=hashlib.sha256((factory_sha+"".join(k+pins[k] for k in sorted(pins))).encode()).hexdigest()
    chunks=["-- Generated own-code persistence extension.\nlocal factories={}\n"]
    for key,value in normalized.items():
        text=value.decode("utf-8")
        if "\x00" in text:raise ValueError("NUL source")
        chunks.append("factories."+key+"=(function()\n"+text+"\nend)()\n")
    chunks.append("""
local bundle=assert(rawget(_G,"X4ToolkitAutomationBundle"),"public automation bundle not loaded")
assert(type(bundle.register_extension)=="function","extension catalog unavailable")
bundle.register_extension{
 id="x4-persistence-extension-v1",revision="@REV@",
 providers={["x4-persistence-v1"]="@REV@"},
 attach=function(ctx)
  -- Exact native declarations: ego_gameoptions/gameoptions.lua 262/271/278.
  ctx.ffi.cdef[[bool IsSaveValid(const char* filename); bool IsSaveListLoadingComplete(void); void ReloadSaveList(void);]]
  local gate=factories.persistencegate{native=factories.persistence,globals=ctx.globals,C=ctx.C,
      Helper=ctx.Helper,current=ctx.public_current,now=ctx.clock}
  ctx.on_dispose(gate.dispose)
  return factories.persistenceprovider{gate=gate,register=ctx.registry.register,current=ctx.public_current,
      now=ctx.clock,encode=ctx.encode,revision="@REV@"}
 end}
""".replace("@REV@",revision))
    data="".join(chunks).encode()
    if Path(__file__).read_bytes()!=factory_bytes:raise ValueError("extension factory changed during build")
    return {"ui/x4_persistence_extension.lua":data},{"revision":revision,"provider":ID,
        "source_sha256":pins,"factory_sha256":factory_sha,"sha256":hashlib.sha256(data).hexdigest(),
        "runtime_verified":False,"effect_exception_approved":False}
