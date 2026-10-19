#!/usr/bin/env python3
"""The ONE shared apply_patch path parser (spec section 5.2): every adapter that has to judge a
patch asks this module which files it touches, and nothing else re-implements the grammar.

    parse_patch(text)          -> [(op, path), ...]   Codex's reading of the patch
    parse_patch_opencode(text) -> [(op, path), ...]   OpenCode's reading of the same text
    shell_patch(cmd)           -> (patch_text, cd_dir) | None   an apply_patch run THROUGH the shell

op is add / update / delete / move_from / move_to. A guard must read a patch EXACTLY as the agent
that applies it does: a line the agent treats as a header and the guard treats as content is a
file the agent touches and the guard never judged (R2-F1, v4.0.0: an INDENTED
`  *** Delete File: <reference>/x` after an Add was a delete to Codex and content to this module).
So each reader is a port of that agent's own parser, held to it by the tests:

- parse_patch ports Codex 0.160.0 (codex-rs/apply-patch: parser.rs boundaries, then
  streaming_parser.rs line by line). tests/test_patch_paths.py holds it to a MEASURED record of
  `codex --codex-run-as-apply-patch` over every shape (scripts/capture-codex-patch-oracle.py).
- parse_patch_opencode ports OpenCode v1.18.34 (packages/opencode/src/patch/index.ts parsePatch);
  the test runs the vendored original under node over the same shapes.

Where the agent refuses the patch, the reader raises PatchParseError, and so does a patch that
touches no file. A caller must turn that into a refusal: a partial path list would be an allow for
the paths it missed. Paths are returned exactly as the agent reads them -- Codex keeps a leading
space after `*** Delete File: ` as part of the name, OpenCode trims it; resolving a relative path
is the caller's job, against the payload's cwd. Stdlib only; Python >= 3.10.
"""
from __future__ import annotations

import re

OPS = ("add", "update", "delete", "move_from", "move_to")
BEGIN, END = "*** Begin Patch", "*** End Patch"

#: Rust's char::is_whitespace (Unicode White_Space), which str::trim uses. NOT Python's
#: str.isspace: that adds U+001C..U+001F, so `\x1c*** Delete File:` would be a header here and is
#: not one to Codex (MEASURED, row fs-indented-after-add).
_RUST_WS = ("\t\n\x0b\x0c\r \x85\xa0          "
            "      　")
#: JavaScript's String.prototype.trim (WhiteSpace + LineTerminator): no U+0085, plus U+FEFF.
_JS_WS = ("\t\n\x0b\x0c\r \xa0          "
          "      　﻿")

#: apply_patch at a command position, optionally after `cd <dir> &&` (or `;`). Codex intercepts
#: the shell form (MEASURED P3: `apply_patch <<'PATCH' ... PATCH` arrived as Bash and was applied).
_SHELL_FORM = re.compile(
    r"""^\s*(?:cd\s+(?P<dir>"[^"]*"|'[^']*'|[^\s;&|]+)\s*(?:&&|;)\s*)?(?:apply_patch|applypatch)(?=\s|$|<)""")
#: The same word at ANY command position (after ; && || | or a newline). Used to refuse a shell
#: patch this module cannot attribute to a directory, rather than to ignore it.
_ANY_POSITION = re.compile(r"(?:^|[;&|\n]|\$\()\s*(?:apply_patch|applypatch)(?=\s|$|<)")


#: Codex's own entry point to its patch applier, which it puts on the shell PATH: `codex
#: --codex-run-as-apply-patch '<patch>'` applies the patch given as an ARGUMENT. FX-G2 item 6
#: (v4.0.0 delta review), MEASURED by reviewer A: that form deleted a file in scratch while
#: shell_patch never looked at it. A SINGLE-quoted argument is literal text, so it is read as
#: the patch; any other spelling of it is refused (see _runs_patch).
_CODEX_FLAG = "--codex-run-as-apply-patch"
_CODEX_ARG = re.compile(
    r"""^\s*(?:cd\s+(?P<dir>"[^"]*"|'[^']*'|[^\s;&|]+)\s*(?:&&|;)\s*)?"""
    r"""(?:[^\s;&|<>'"]*[/\\])?codex(?:\.exe|\.cmd)?\s+--codex-run-as-apply-patch\s+'(?P<body>[^']*)'\s*$""")
_PATCH_NAMES = ("apply_patch", "applypatch")
_RUNNER_EXT = re.compile(r"\.(?:exe|bat|cmd|com|ps1|sh)$", re.IGNORECASE)
_PATCH_RUNNERS = ("codex", "node", "npx", "bunx", "bun", "pnpm", "yarn")
#: With no shell parser to ask, the name or the flag ANYWHERE is a patch run (fail closed).
_ANYWHERE = re.compile(r"(?:apply_?patch|--codex-run-as-apply-patch)", re.IGNORECASE)


def _hook_facts():
    """hook_facts, the shell parser every guard shares -- beside this file in a rendered tree,
    in ../claude-hooks in the source tree -- or None."""
    try:
        import hook_facts
        return hook_facts
    except Exception:
        import sys
        from pathlib import Path
        here = Path(__file__).resolve().parent
        for d in (here, here.parent / "claude-hooks"):
            if (d / "hook_facts.py").is_file() and str(d) not in sys.path:
                sys.path.insert(0, str(d))
        try:
            import hook_facts
            return hook_facts
        except Exception:
            return None


def _runs_patch(cmd: str) -> bool:
    """Does ANY command in this shell text run apply_patch? Judged by the guards' own shell
    parser, which already sees through what a regex over the text does not: `{ apply_patch`,
    `FOO=1 apply_patch`, `(apply_patch`, wrappers (`timeout 5`, `env`, `exec`), a path or a
    `.exe/.bat/.cmd` suffix, `bash -c '...'` and the other carriers -- plus Codex's
    `--codex-run-as-apply-patch` under a codex/node launcher (item 6). A mention (`echo
    apply_patch`, `grep -rn apply_patch`) is not a run. A parser that is missing, raises, or
    stopped walking (carriers truncated) answers YES: an unread patch is never an allow."""
    # COST GATE: every shell command passes here, and the walk can translate a PowerShell
    # carrier (a subprocess). Neither name can run without "patch" in the text once quotes and
    # backslashes are removed (`app""ly_pat'c'h` included). ACCEPTED RESIDUAL: a name built
    # from a variable (`$P <<EOF`) -- the shell guard still judges that command as shell.
    if "patch" not in re.sub(r"""["'\\]""", "", cmd).lower():
        return False
    H = _hook_facts()
    if H is None:
        return bool(_ANYWHERE.search(cmd))
    try:
        spliced = H.join_continuations(cmd)
        body = H.strip_comments(H.strip_heredocs(spliced))
        extra = [H.strip_comments(h) for h in H.heredoc_bodies(spliced)]
        cmds, truncated = H.carried_commands(body, extra)
        if truncated:
            return True
        for c in cmds:
            for seg in H.segments(c):
                name = _RUNNER_EXT.sub("", H._verb_name(H.verb(seg)) or "")
                if name in _PATCH_NAMES:
                    return True
                if _CODEX_FLAG in H.tokens_of(seg) and (name.startswith("codex") or name in _PATCH_RUNNERS):
                    return True
    except Exception:
        return True
    return False


class PatchParseError(ValueError):
    pass


# --- Codex 0.160.0 ------------------------------------------------------------------------------

_ADD, _DELETE, _UPDATE = "*** Add File: ", "*** Delete File: ", "*** Update File: "
_MOVE, _EOF, _ENV = "*** Move to: ", "*** End of File", "*** Environment ID:"
_CTX, _CTX_EMPTY = "@@ ", "@@"


def _rust_lines(s: str) -> list[str]:
    """str::lines(): split at \\n, drop ONE \\r before it, no empty piece after a final \\n."""
    parts = s.split("\n")
    out = [p[:-1] if p.endswith("\r") else p for p in parts[:-1]]   # each ended by a \n
    if parts[-1]:
        out.append(parts[-1])                        # an unterminated last line keeps a bare \r
    return out


def _codex_boundaries(lines: list[str]) -> list[str]:
    """parser.rs check_patch_boundaries_lenient: strict first, else a `<<EOF` heredoc wrapper."""
    def strict(ls):
        first = ls[0].strip(_RUST_WS) if ls else None
        last = ls[-1].strip(_RUST_WS) if ls else None
        if first == BEGIN and last == END:
            return None
        if first != BEGIN:
            return "The first line of the patch must be '*** Begin Patch'"
        return "The last line of the patch must be '*** End Patch'"
    err = strict(lines)
    if err is None:
        return lines
    if len(lines) >= 4 and lines[0] in ("<<EOF", "<<'EOF'", '<<"EOF"') and lines[-1].endswith("EOF"):
        inner = lines[1:-1]
        err2 = strict(inner)
        if err2 is None:
            return inner
        raise PatchParseError(f"invalid patch: {err2}")
    raise PatchParseError(f"invalid patch: {err}")


class _Codex:
    """streaming_parser.rs StreamingPatchParser, keeping only what decides which files a patch
    touches: the hunks, and per Update chunk whether it holds lines and ends at End of File."""

    def __init__(self):
        self.mode = "not_started"
        self.update_line_no = 0
        self.hunks: list[dict] = []
        self.env = None
        self.n = 0

    def fail(self, msg: str):
        raise PatchParseError(f"invalid hunk at line {self.n}, {msg}")

    def _last_update(self):
        return self.hunks[-1] if self.hunks and self.hunks[-1]["op"] == "update" else None

    def ensure_update_not_empty(self, line: str):
        h = self._last_update()
        if h is None:
            return
        if not h["chunks"] and self.mode == "update":
            raise PatchParseError(f"invalid hunk at line {self.update_line_no}, Update file hunk for "
                                  f"path '{h['path']}' is empty")
        if h["chunks"] and not h["chunks"][-1]["lines"]:
            self.fail("Update hunk does not contain any lines" if line == END
                      else f"Unexpected line found in update hunk: {line!r}")

    def headers(self, trimmed: str) -> bool:
        if self.mode == "started" and trimmed.startswith(_ENV):
            if self.env is not None:
                raise PatchParseError("invalid patch: apply_patch environment_id cannot be specified more than once")
            env = trimmed[len(_ENV):].strip(_RUST_WS)
            if not env:
                raise PatchParseError("invalid patch: apply_patch environment_id cannot be empty")
            self.env = env
            return True
        if trimmed == END:
            self.ensure_update_not_empty(trimmed)
            self.mode = "ended"
            return True
        for marker, op in ((_ADD, "add"), (_DELETE, "delete"), (_UPDATE, "update")):
            if trimmed.startswith(marker):
                self.ensure_update_not_empty(trimmed)
                self.hunks.append({"op": op, "path": trimmed[len(marker):], "move": None, "chunks": []})
                self.mode = op
                if op == "update":
                    self.update_line_no = self.n
                return True
        return False

    def line(self, line: str):
        trimmed = line.strip(_RUST_WS)
        if self.mode == "not_started":
            if trimmed == BEGIN:
                self.mode = "started"
                return
            raise PatchParseError("invalid patch: The first line of the patch must be '*** Begin Patch'")
        if self.mode in ("started", "add", "delete"):
            if self.headers(trimmed):
                return
            if self.mode == "add" and line.startswith("+"):
                return
            self.fail(f"{trimmed!r} is not a valid hunk header")
        if self.mode == "ended":
            if trimmed:
                raise PatchParseError("invalid patch: The last line of the patch must be '*** End Patch'")
            return
        # update: headers are recognised on the RIGHT-trimmed line only -- an indented
        # `  *** Delete File: x` inside an Update is a context line (MEASURED, indented-after-update)
        u = line.rstrip(_RUST_WS)
        if self.headers(u):
            return
        h = self.hunks[-1]
        chunks = h["chunks"]
        if chunks and chunks[-1]["eof"]:
            if not u:
                return
            if u != _CTX_EMPTY and not u.startswith(_CTX):
                self.fail(f"Expected update hunk to start with a @@ context marker, got: {line!r}")
        if not chunks and h["move"] is None and u.startswith(_MOVE):
            h["move"] = u[len(_MOVE):]
            return
        if (u == _CTX_EMPTY or u.startswith(_CTX)) and chunks and not chunks[-1]["lines"]:
            self.fail(f"Unexpected line found in update hunk: {line!r}")
        if u == _CTX_EMPTY or u.startswith(_CTX):
            chunks.append({"lines": False, "eof": False})
            return
        if u == _EOF:
            if chunks and not chunks[-1]["lines"]:
                self.fail("Update hunk does not contain any lines")
            if chunks:
                chunks[-1]["eof"] = True
            return
        if line == "" or line[0] in " +-":
            if not chunks:
                chunks.append({"lines": False, "eof": False})
            chunks[-1]["lines"] = True
            return
        if chunks and chunks[-1]["lines"]:
            self.fail(f"Expected update hunk to start with a @@ context marker, got: {line!r}")
        self.fail(f"Unexpected line found in update hunk: {line!r}")

    def finish(self, last: str):
        if last:
            self.n += 1
            if last.strip(_RUST_WS) == END:
                self.ensure_update_not_empty(last.strip(_RUST_WS))
                self.mode = "ended"
            else:
                self.line(last)
        if self.mode != "ended":
            raise PatchParseError("invalid patch: The last line of the patch must be '*** End Patch'")


def parse_patch(text: str) -> list[tuple[str, str]]:
    """Codex's reading: parser.rs parse_patch (lenient mode, as Codex runs it) -> the hunks."""
    if not isinstance(text, str):
        raise PatchParseError("patch text is not a string")
    lines = _codex_boundaries(_rust_lines(text.strip(_RUST_WS)))
    body = "\n".join(lines)
    p = _Codex()
    pieces = body.split("\n")
    for piece in pieces[:-1]:                        # push_delta: each \n-terminated line,
        p.n += 1                                     # one more trailing \r dropped
        p.line(piece[:-1] if piece.endswith("\r") else piece)
    p.finish(pieces[-1])
    ops: list[tuple[str, str]] = []
    for h in p.hunks:
        if h["op"] == "update" and h["move"] is not None:
            ops += [("move_from", h["path"]), ("move_to", h["move"])]
        else:
            ops.append((h["op"], h["path"]))
    if not ops:
        raise PatchParseError("the patch touches no file")
    return ops


# --- OpenCode v1.18.34 --------------------------------------------------------------------------

_OC_HEREDOC = re.compile(
    "^(?:cat[%s]+)?<<['\"]?([A-Za-z0-9_]+)['\"]?[%s]*\n([\\s\\S]*?)\n\\1[%s]*\\Z" % ((_JS_WS,) * 3))


def parse_patch_opencode(text: str) -> list[tuple[str, str]]:
    """OpenCode's reading: patch/index.ts parsePatch. Headers count only at column 0, any line it
    does not know is skipped, a hunk body runs to the next line starting `***`, paths are trimmed.
    Raises where OpenCode's apply_patch tool fails the call: no Begin/End, or no hunks."""
    if not isinstance(text, str):
        raise PatchParseError("patch text is not a string")
    cleaned = text.strip(_JS_WS)
    m = _OC_HEREDOC.match(cleaned)
    if m:
        cleaned = m.group(2)
    lines = cleaned.split("\n")
    begin = next((i for i, ln in enumerate(lines) if ln.strip(_JS_WS) == BEGIN), -1)
    end = next((i for i, ln in enumerate(lines) if ln.strip(_JS_WS) == END), -1)
    if begin == -1 or end == -1 or begin >= end:
        raise PatchParseError("Invalid patch format: missing Begin/End markers")

    def body_end(i: int) -> int:                     # parseAddFileContent / parseUpdateFileChunks
        while i < len(lines) and not lines[i].startswith("***"):
            i += 1
        return i

    ops: list[tuple[str, str]] = []
    i = begin + 1
    while i < end:
        line = lines[i]
        kind = next((k for k in ("Add", "Delete", "Update") if line.startswith(f"*** {k} File:")), None)
        if kind is None:
            i += 1
            continue
        path = line[len(f"*** {kind} File:"):].strip(_JS_WS)
        nxt, move = i + 1, None
        if kind == "Update" and nxt < len(lines) and lines[nxt].startswith("*** Move to:"):
            move = lines[nxt][len("*** Move to:"):].strip(_JS_WS)
            nxt += 1
        if not path:                                 # parsePatchHeader returned null
            i += 1
            continue
        if kind == "Add":
            ops.append(("add", path))
            i = body_end(nxt)
        elif kind == "Delete":
            ops.append(("delete", path))
            i = nxt
        else:
            ops += [("move_from", path), ("move_to", move)] if move else [("update", path)]
            i = body_end(nxt)
    if not ops:
        raise PatchParseError("apply_patch verification failed: no hunks found")
    return ops


#: A `cd` target the shell EXPANDS: `$X4_REFERENCE`, `${X}`, `$env:X`, `%X%`, `~`, `$(...)`, a
#: backtick. Single-quoted text is literal in bash and PowerShell alike.
_EXPANDS = re.compile(r"[$`]|%[^%\s]+%|^~")


def _literal_cd(raw: str | None) -> str | None:
    """The `cd` directory a shell patch runs in, dequoted. FX-G4 / reviewer H3, MEASURED: `cd
    $X4_REFERENCE && codex --codex-run-as-apply-patch '...Add File: libraries/x.xml...'` (and
    `$env:X4_REFERENCE`, and `apply_patch <<EOF` after such a cd) judged the LITERAL path
    `<cwd>/$X4_REFERENCE/libraries/x.xml` -- the variable was never expanded. Which value the
    agent's shell gives it is not knowable here (the configured roots live in the bash guards,
    and a shell environment policy may differ from this process's), so a directory that the
    shell expands is REFUSED, never guessed: the patch must name its directory literally."""
    if not raw:
        return None
    # ONE fully single-quoted word only (FX-G5 / reviewer J2, MEASURED): `''$X4_REFERENCE''`
    # and `'a'$X'b'` also start and end with a quote, but are concatenations the shell expands.
    if re.fullmatch(r"'[^']*'", raw):
        return raw[1:-1]
    # ...and ONE fully double-quoted word (still expanded below). ANY other word holding a quote
    # is a concatenation the shell joins (FX-G6 / reviewer K I1, MEASURED: `'<REF>'/libraries`
    # and `"<REF>"'/sub'` were taken WITH their quotes -- a path under no root -- and a Codex /
    # OpenCode shell patch wrote into reference/ unjudged): refused, never dequoted by guess.
    cd = raw[1:-1] if re.fullmatch(r'"[^"]*"', raw) else raw
    if cd is raw and ("'" in raw or '"' in raw):
        raise PatchParseError(f"the patch runs after `cd {raw}`, a directory spelled with mixed "
                              "quoting the guard does not reassemble; cd to the path as ONE quoted "
                              "word, or pass it as the patch tool's own directory")
    if _EXPANDS.search(cd):
        raise PatchParseError(f"the patch runs after `cd {raw}`, a directory the shell expands "
                              "(a variable, `~` or a substitution) and the guard cannot; cd to the "
                              "literal path, or pass it as the patch tool's own directory")
    return cd


def shell_patch(cmd: str) -> tuple[str, str | None] | None:
    """(patch text, cd dir or None) when the shell command runs apply_patch; None when it does not.
    When apply_patch is run but no Begin..End block can be found, the text returned is '' so the
    caller's parse_patch refuses: a shell patch that cannot be read is never an allow."""
    if not isinstance(cmd, str):
        return None
    m = _SHELL_FORM.match(cmd)
    cd = None
    if m:
        cd = _literal_cd(m.group("dir"))
    else:
        mc = _CODEX_ARG.match(cmd)
        if mc:                               # `codex --codex-run-as-apply-patch '<patch>'` (item 6)
            return mc.group("body"), _literal_cd(mc.group("dir"))
        if not (_ANY_POSITION.search(cmd) or _runs_patch(cmd)):
            return None
    start, end = cmd.find(BEGIN), cmd.rfind(END)
    if start < 0 or end < start:
        return "", cd
    if not m:
        return "", None                      # apply_patch somewhere we cannot attribute: refuse
    return cmd[start:end + len(END)], cd


def shell_patch_body(cmd: str) -> str | None:
    found = shell_patch(cmd)
    return None if found is None else found[0]
