"""Fixed trusted-generation capacity profiles; never caller-supplied wire limits.

These are logical retained-value charges, not process RSS or a guarantee that a
whole campaign fits. Selection belongs to the installed/generated constructor.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

_INTERACTIVE = {
    "sdk_history": 256, "router_history": 256, "read_history": 4096,
    "sdk_retained_bytes": 64 * 1024**2, "router_retained_bytes": 16 * 1024**2,
    "sdk_result_bytes": 131072, "sdk_request_bytes": 24576,
    "sdk_record_overhead": 1024, "packet_bytes": 1400,
    "native_history": 128,
    "orbit_history": 64, "orbit_calls": 256,
    "capture_history": 512, "capture_native_records": 512, "capture_calls": 2048,
    "host_capture_records": 512, "host_directory_members": 512,
    "capture_total_bytes": 8 * 1024**3, "camera_total_bytes": 16 * 1024**2,
    "roomentry_records": 64, "roomentry_calls": 256,
}
_CAMPAIGN = {
    **_INTERACTIVE,
    "sdk_history": 8192, "router_history": 8192, "read_history": 65536,
    "sdk_result_bytes": 16384, "sdk_request_bytes": 4096,
    "native_history": 512,
    "orbit_history": 2048, "orbit_calls": 16384,
    "capture_history": 4096, "capture_native_records": 2048, "capture_calls": 16384,
    "host_capture_records": 2048, "host_directory_members": 2048,
    "roomentry_records": 256, "roomentry_calls": 8192,
}
_PROFILES = {"interactive": _INTERACTIVE, "campaign_v1": _CAMPAIGN}
SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
REVISION = hashlib.sha256(json.dumps(
    {"version": 1, "profiles": _PROFILES, "source_sha256": SOURCE_SHA256},
    sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def resolve(name: str | None = None) -> dict:
    """Return an independent closed profile; unknown names/numeric overrides refuse."""
    if name is None:
        name = "interactive"
    if type(name) is not str or name not in _PROFILES:
        raise ValueError("unknown fixed capacity profile")
    return {"name": name, "revision": REVISION, "source_sha256": SOURCE_SHA256,
            **_PROFILES[name]}

def lua_source() -> str:
    """Pure source for the resolver captured by trusted generated constructors."""
    rows = []
    for name in _PROFILES:
        row = resolve(name)
        fields = []
        for key, value in sorted(row.items()):
            encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
            fields.append("[" + json.dumps(key) + "]=" + encoded)
        rows.append("[" + json.dumps(name) + "]={" + ",".join(fields) + "}")
    return ("local profiles={" + ",".join(rows) + "}\n"
            "return function(name)\n"
            "  if name==nil then name='interactive' end\n"
            "  assert(type(name)=='string' and profiles[name], 'unknown fixed capacity profile')\n"
            "  local out={};for k,v in pairs(profiles[name]) do out[k]=v end\n"
            "  return out\nend\n")
