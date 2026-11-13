"""Every FFI struct the helper declares must match vanilla's declaration of the same name (review r2-9).

LuaJIT keeps the FIRST declaration of a type name, and vanilla's ui Lua redeclares the
same names in several files. Each helper module checks the type it gets against its OWN
proof struct, so a helper declaration that differed from vanilla's would break a vanilla
file loading after it, unseen by those checks. This test compares field lists (type and
name, in order) after expanding `T a,b;` and `const char *a,*b;` declarators.

It needs the unpacked game reference (X4_REFERENCE) and skips without it. Our bodies are
read from the helper sources by the five literal shapes they use; any declared name whose
body cannot be read is a FAILURE, never a silent skip.
"""
import re
from pathlib import Path

import pytest

from x4validate import _paths

ROOT = Path(__file__).resolve().parents[3]
UI = ROOT / "mods" / "x4_toolkit_helper" / "ui"


def canonical(body: str) -> list[tuple[str, str]]:
    """'const char *a,*b; T c,d;' -> [('const char*','a'),('const char*','b'),('T','c'),('T','d')]."""
    body = re.sub(r"//[^\n]*", "", body)
    body = re.sub(r"--[^\n]*", "", body)  # a Lua comment inside a [[...]] body
    out = []
    for decl in body.split(";"):
        decl = " ".join(decl.split())
        if not decl:
            continue
        m = re.match(r"^((?:const\s+|unsigned\s+|signed\s+|struct\s+)*[A-Za-z_]\w*)\s*(.*)$", decl)
        assert m, decl
        base, rest = m.group(1), m.group(2)
        for part in rest.split(","):
            part = part.strip()
            stars = len(part) - len(part.lstrip("*"))
            name = part.lstrip("*").strip()
            array = ""
            am = re.match(r"^(\w+)\s*(\[[^\]]*\])$", name)
            if am:
                name, array = am.group(1), am.group(2)
            out.append((base + "*" * stars + array, name))
    return out


def helper_bodies() -> tuple[dict[str, list[str]], set[str]]:
    """Struct name -> bodies the helper can declare, and the names whose body was not read."""
    bodies: dict[str, list[str]] = {}
    declared: set[str] = set()

    def add(name, body):
        wrapped = re.fullmatch(r"\s*typedef\s+struct\s*\{(.*)\}\s*\w+\s*;\s*", body, re.S)
        bodies.setdefault(name, []).append(wrapped.group(1) if wrapped else body)

    for f in sorted(UI.glob("*.lua")):
        text = f.read_text(encoding="utf-8")
        for body, name in re.findall(r"typedef\s+struct\s*\{([^{}\"']*?)\}\s*(\w+)\s*;", text):
            add(name, body)
        # Name='body' and Name={body='...'} / Name={fields="..."} table entries.
        for name, body in re.findall(r"\b([A-Z]\w+)\s*=\s*['\"]([^'\"]*;[^'\"]*)['\"]", text):
            add(name, body)
        for name, body in re.findall(r"\b([A-Z]\w+)\s*=\s*\{\s*(?:body|fields)\s*=\s*['\"]([^'\"]*)['\"]", text):
            add(name, body)
        # local X=[[...]] / local X="..." used as "typedef struct {"..X.."<extra>} Name;"
        for var, long, short in re.findall(r"local\s+(\w+)\s*=\s*(?:\[\[(.*?)\]\]|\"([^\"]*;[^\"]*)\")", text, re.S):
            for extra, name in re.findall(r"typedef struct \{\"\.\." + var + r"\.\.\"([^\"]*)\} (\w+);", text):
                add(name, (long or short) + extra)
        # structure("Name","body") (live_camerastate.lua); other two-string calls are not declarations.
        for name, body in re.findall(r"\bstructure\(\s*[\"']([A-Z]\w+)[\"']\s*,\s*[\"']([^\"']*;[^\"']*)[\"']\s*\)", text):
            add(name, body)
        declared.update(re.findall(r"typedef\s+struct\s*\{[^}]*\}\s*(\w+)\s*;", text))
        declared.update(re.findall(r"typedef struct \{\"\.\.\w+\.\.\"[^\"]*\} (\w+);", text))
    return bodies, declared - set(bodies)


def vanilla_bodies(reference: Path) -> dict[str, set[tuple]]:
    out: dict[str, set[tuple]] = {}
    for f in sorted((reference / "ui").rglob("*.lua")):
        for body, name in re.findall(r"typedef\s+struct\s*\{(.*?)\}\s*(\w+)\s*;",
                                     f.read_text(encoding="utf-8", errors="replace"), re.S):
            if "{" in body or "}" in body:
                continue  # a nested struct body; this matcher cannot read it (none of ours is nested)
            out.setdefault(name, set()).add(tuple(canonical(body)))
    return out


def test_canonical_expands_declarators():
    assert canonical("const char *a,*b; uint32_t c, d; float e[4];") == [
        ("const char*", "a"), ("const char*", "b"), ("uint32_t", "c"), ("uint32_t", "d"), ("float[4]", "e")]


def test_every_helper_struct_matches_vanilla():
    reference = _paths.reference()
    if reference is None or not (Path(reference) / "ui").is_dir():
        pytest.skip("X4_REFERENCE (the unpacked game ui) is not available")
    ours, unread = helper_bodies()
    assert not unread, f"helper struct bodies the census could not read: {sorted(unread)}"
    vanilla = vanilla_bodies(Path(reference))
    assert len(vanilla) > 100, "vanilla census found too few structs to be trusted"
    differs, absent, same = [], [], []
    for name, bodies in sorted(ours.items()):
        if name not in vanilla:
            absent.append(name)
            continue
        for body in bodies:
            (same if tuple(canonical(body)) in vanilla[name] else differs).append(name)
    assert not differs, f"helper struct layouts that differ from vanilla: {sorted(set(differs))}"
    assert same, "no helper struct was compared"
