"""Destination rendering for private X4 upgrade reviews; no installed writes."""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

UNSAFE_CODEX_ROOT = ('"', '\n', '\r', '%', '!', '$', '`')
TEMPLATE = "tools/x4_harness/codex-hooks.json.tmpl"
OPEN_TOKEN = "{" * 2
ROOT_TOKEN, WINDOWS_ROOT_TOKEN, TOOLKIT_TOKEN = (OPEN_TOKEN + value + "}}" for value in ("ROOT", "ROOT_WIN", "TOOLKIT"))


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise ValueError("duplicate JSON settings field")
        result[key] = value
    return result


def _json(raw):
    if len(raw) > 1024 * 1024:
        raise ValueError("settings exceed the 1 MiB parser budget")
    value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs)
    if not isinstance(value, dict):
        raise ValueError("hook settings must be an object")
    json.dumps(value, allow_nan=False)
    return value


def _read(path):
    with Path(path).open("rb") as stream:
        return stream.read(1024 * 1024 + 1)


def render_codex(template, root):
    value = str(root)
    bad = [c for c in UNSAFE_CODEX_ROOT if c in value]
    if bad:
        raise ValueError("refusing a Codex hook root containing " + repr(bad) + ": " + repr(value))
    posix = json.dumps(value.replace("\\", "/"))[1:-1]
    windows = json.dumps(value.replace("/", "\\"))[1:-1]
    rendered = template.replace(WINDOWS_ROOT_TOKEN, windows).replace(ROOT_TOKEN, posix)
    if OPEN_TOKEN in rendered:
        raise ValueError("hooks.json template has an unrendered token")
    _json(rendered.encode("utf-8"))
    return rendered


def prepare(root, staged, files, config):
    if (not isinstance(config, dict) or set(config) != {"v", "agent_shell", "opencode_shell"} or
            type(config["v"]) is not int or config["v"] != 1 or
            any(config[key] not in ("bash", "powershell") for key in ("agent_shell", "opencode_shell"))):
        raise ValueError("X4 render configuration needs version 1 and explicit agent_shell/opencode_shell")
    rendered = {}
    for name in files:
        if name.endswith(".md") and (name.startswith(".agents/") or name.startswith(".opencode/skills/")):
            raw = (staged / name).read_bytes()
            if TOOLKIT_TOKEN.encode() in raw:
                shell = config["opencode_shell"] if name.startswith(".opencode/") else config["agent_shell"]
                value = b"$env:X4_TOOLKIT" if shell == "powershell" else b"$X4_TOOLKIT"
                rendered[name] = raw.replace(TOOLKIT_TOKEN.encode(), value)
    if ".codex/hooks.json" in files or any(name.startswith(".codex/hooks/") for name in files):
        if TEMPLATE not in files:
            raise ValueError("candidate omits the canonical Codex template needed for review")
        template = (staged / TEMPLATE).read_bytes().decode("utf-8-sig")
        rendered[".codex/hooks.json"] = render_codex(template, root).encode("utf-8")
    validate(root, tuple({"path": name, "data": raw} for name, raw in rendered.items()))
    return rendered


def validate(root, changes):
    for row in changes:
        name = row["path"]
        selected = (name == ".codex/hooks.json" or name == "AGENTS.md" or
                    name.endswith(".md") and (name.startswith(".agents/") or name.startswith(".opencode/skills/")))
        if not selected:
            continue
        raw = row["data"] if "data" in row else Path(row["source"]).read_bytes()
        text = raw.decode("utf-8-sig")
        if any(token in text for token in (TOOLKIT_TOKEN, ROOT_TOKEN, WINDOWS_ROOT_TOKEN)):
            raise ValueError("candidate needs destination rendering before application: " + name)
        if name == "AGENTS.md" and len(raw) > 32768:
            raise ValueError("rendered agent instructions exceed the 32768-byte budget")
        if name == ".codex/hooks.json":
            render_codex(text, root)  # also refuses unsafe root spelling


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check-codex",))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    args = parser.parse_args()
    try:
        expected = render_codex(_read(args.template).decode("utf-8-sig"), args.root)
        canonical = lambda value: json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False)
        same = canonical(_json(_read(args.root / ".codex/hooks.json"))) == canonical(_json(expected.encode("utf-8")))
        raise SystemExit(0 if same else 1)
    except (ValueError, OSError) as exc:
        parser.exit(2, "Codex settings could not be checked: " + type(exc).__name__ + "\n")
