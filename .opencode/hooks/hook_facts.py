"""One parse pass over a Bash hook payload, answering every guard rule's question.

protect-bash.sh keeps the POLICY -- which verdict, and the prose explaining it. This
module supplies the FACTS. That split exists because the previous design had each of
eight rules hand-roll its own quote-aware shell parsing in bash, and:

  * MEASURED 2026-08-31, clean machine, 201-char command: 13,585 ms per Bash call,
    against 1,205 ms before the rules were re-scoped -- 11.3x. PreToolUse blocks the
    tool call, so that is pure latency on every command. Attributed by profiling:
    resolve_var cost 236 ms for ONE token and was called per-token inside per-segment
    loops; writes_under and searches_rooted_at were re-invoked 5 and 4 times, each
    re-tokenising from scratch.
  * Every gap a code review found lived in that duplicated parsing -- `mv -t`, `>|`,
    wrapper verbs (time/nice/env/sudo/xargs), `grep -r -e`, rg being recursive by
    default, and a heredoc marker inside a quoted string opening a skip region.

Fixing those one predicate at a time meant writing the same parser eight more times.
This is the "one implementation, asked for by everyone else" rule from CLAUDE.md's
narrowing-step table, applied to the guards themselves.

Most functions are pure. Two deliberate exceptions: Windows 8.3 aliases use
GetLongPathNameW to verify an existing prefix, and PowerShell text (the PowerShell TOOL's payload, or a
`powershell -c` / `pwsh -Command` nested in a Bash command) is parsed by PowerShell's
own parser: `powershell_to_sh` hands it to ps_translate.ps1, which answers "what does
this do to the filesystem" as an equivalent POSIX-shell command, and that command then
goes through the SAME pass as any other -- one rule set, two front-ends
(AUDIT-2026-09-24 HK-1). The subprocess runs only when PowerShell text is present.
Everything here is unit-tested directly in test_hook_facts.py.
"""
from __future__ import annotations

import base64
import json
import os
import posixpath
import fnmatch
import itertools
import re
import shutil
import subprocess
import sys

# --------------------------------------------------------------------- paths

_DRIVE = re.compile(r"(^|[^a-z0-9])([a-z]):/")


#: `\\?\\` (extended-length) and `\\.\\` (device), after backslashes have been folded
#: to slashes. The `UNC/` form wraps a network path and unwraps back to a `//host/share`.
#: Matched at the START only: these are prefixes, and a `//?/` appearing mid-path is not
#: one.
_WIN_PREFIX = re.compile(r"^/{1,2}[?]/(unc/)?|^//[.]/(unc/)?")
#: ONE leading slash is matched for the `?` form and not for `.`, and that asymmetry is
#: deliberate. Inside double quotes bash turns `\\\\` into `\\`, so the operand that reaches
#: tokens() is `\\?\\C:\\...` -- one backslash -- which folds to `/?//c/...`. MEASURED
#: 2026-09-02: `\\\\?\\<path>` DELETED a real directory, so this is a live vector, not
#: the inert spelling a review reported it as. `/./x` is a legitimate POSIX path
#: that normpath already collapses, and stripping it would make `/./foo` RELATIVE;
#: `?` is not legal in a Windows path component, so it has no such reading.


#: A PowerShell provider qualifier, lowercased and slash-folded. Leading only.
_PROVIDER = re.compile(r"^(microsoft[.]powershell[.]core/)?filesystem::")

#: Two or more separators in a row. See norm() for why the leading pair survives.
_SLASHES = re.compile(r"/{2,}")


def _win_alias(s: str) -> str:
    """Each `/`-separated component as WINDOWS opens it: an NTFS stream suffix dropped
    (`x::$DATA` IS x, `dir::$INDEX_ALLOCATION` IS dir, `x:alt` is a stream OF x) and trailing
    dots and spaces dropped (`reference.` IS reference). `.`, `..` and an all-dot/space
    component are kept for normpath / as written.

    FX-G2 item 3, MEASURED 2026-10-05 on NTFS: Python wrote INTO `x4-paths.env.`,
    `x4-paths.env `, `x4-paths.env::$DATA` and `<dir>./file` -- each the real file -- while
    every guard compared them equal to nothing. GetFullPathNameW strips a final component's
    trailing dots AND spaces but only ONE trailing dot of an intermediate one; this strips
    all of both everywhere, the conservative superset (a folder genuinely named `a ` is
    judged as `a`). Not Windows-gated: on POSIX `foo.` is a different file, and reading it
    as `foo` can only make a rule fire, never stop one."""
    out = []
    for n, c in enumerate(s.split("/")):
        if c and c not in (".", ".."):
            i = c.find(":")
            # Not in the FIRST component of a relative token: there a colon is a drive-relative
            # `C:x` or a command NAME (verb() reads names through norm -- `C:toolsrm` must stay
            # that word, test_an_UNQUOTED_windows_path_is_not_that_command_at_all). A relative
            # OPERAND reaches the rules joined to its directory, i.e. absolute, so it is covered.
            if i > 0 and n > 0:
                c = c[:i]
            c = c.rstrip(". ") or c
        out.append(c)
    return "/".join(out)


_SHORT_NAME = re.compile(r"~[0-9]")
_MSYS_DRIVE = re.compile(r"^/([A-Za-z])(/|$)")


def long_name(p: str) -> str:
    """An 8.3 SHORT name (`C:/PROGRA~2/.../X4FOUN~1`) as its long form, so it compares equal
    to the configured root. FX-G2 item 3, MEASURED 2026-10-05: this machine's game root has
    the short form `C:/PROGRA~2/Steam/STEAMA~1/common/X4FOUN~1`, and `rm -rf` of it reached no
    rule (the name backstop reads `x4 foundations`). GetLongPathNameW answers only for a path
    that EXISTS, so the longest existing prefix is resolved and the lexically normalized
    remainder is retained without claiming it exists. Provider/device prefixes must be
    peeled before the native query, using the same lexical rules as comparisons.
    One syscall per prefix, and only for a path carrying `~<digit>` on Windows: every other
    path returns at the regex. Any failure returns the path unchanged (today's verdict)."""
    if os.name != "nt" or not p or not _SHORT_NAME.search(p):
        return p
    try:
        import ctypes
        w = _MSYS_DRIVE.sub(lambda m: m.group(1) + ":/", _lexical_norm(p))
        parts = w.split("/")
        buf = ctypes.create_unicode_buffer(32768)
        for i in range(len(parts), 0, -1):
            head = "/".join(parts[:i])
            if not _SHORT_NAME.search(head):
                return p
            n = ctypes.windll.kernel32.GetLongPathNameW(head, buf, 32768)
            if 0 < n < 32768:
                return "/".join([buf.value.replace(chr(92), "/")] + parts[i:])
    except Exception:
        pass
    return p


def _lexical_norm(p: str) -> str:
    """Lowercase, backslashes to slashes, drive dialect unified, dot segments resolved.

    Windows-to-MSYS is the safe direction: "c:/" is unambiguous, since a colon is
    illegal elsewhere in a Windows path, whereas "/c/" also occurs mid-path. The guard
    on the preceding character is what keeps "https://" from matching as a drive "s".
    """
    if not p:
        return ""
    s = p.replace(chr(92), "/").lower()
    # A PowerShell PROVIDER qualifier names the same file: `FileSystem::C:\x` and
    # `Microsoft.PowerShell.Core\FileSystem::C:\x` are `C:\x`. Unstripped, they compared
    # equal to no root, so a PowerShell delete of reference/ spelled that way was ALLOW
    # (AUDIT-2026-09-24 HK-1 review item 4). Stripped BEFORE the extended-length prefix,
    # because the two combine: `FileSystem::\\?\C:\x`.
    s = _PROVIDER.sub("", s)
    # The EXTENDED-LENGTH prefix goes FIRST, and the order is the fix rather than a
    # detail: applied after the drive rule below it turns `//?/c:/users` into
    # `//?//c/users`, which still matches nothing.
    #
    # MEASURED 2026-09-02, E2E: `rm -rf "//?/<reference>"` was ALLOW while the plain
    # form denied, and so was the saves confirmation. Windows accepts this spelling for
    # any path, the Write tool passes it through, Git Bash's own `rm` honours it, and
    # `_x4-env.sh` and `backup-before-edit.sh` were both taught to strip it earlier the
    # same day -- this normaliser, the one protect-bash.sh funnels through, was not.
    #
    # The game root hid it in my own probe: its folder name appears in the command text,
    # so the NAME backstop fired. Reference and saves have no backstop. A control that
    # passes for a reason unrelated to the fix is worse than no control.
    s = _WIN_PREFIX.sub(lambda m: "//" if m.group(1) else "", s)
    s = _DRIVE.sub(lambda m: m.group(1) + "/" + m.group(2) + "/", s)
    # COLLAPSE RUNS OF `/`, keeping a leading `//`.
    #
    # Both POSIX and Windows treat an interior `//` as one separator, so
    # `<root>//reference/x` and `<root>/reference/x` are the same file -- but the
    # first compared equal to nothing and walked straight past the reference/ HARD
    # BLOCK. MEASURED 2026-09-03, E2E, in both channels: the write was ALLOW here
    # and in protect-files.sh, while the single-slash spelling denied.
    #
    # normpath below would have collapsed it -- but it only runs when a DOT
    # segment is present, so the `//` case never reached the one function that
    # would have fixed it. A doubled separator is the ordinary concatenation
    # artefact (`"$DIR/" + "/reference/..."`), not something anyone types.
    #
    # The leading `//` is preserved: on Windows that is a UNC share, a real and
    # DIFFERENT location, and collapsing it would retarget a path rather than
    # normalise it. The extended-length rewrite above emits `//` for that reason.
    if "://" not in s:
        head, rest = (s[:2], s[2:]) if s.startswith("//") else ("", s)
        s = head + _SLASHES.sub("/", _win_alias(_SLASHES.sub("/", rest)))
    # Only canonicalise something that is actually a path. normpath would happily
    # rewrite "https://a/b" to "https:/a/b".
    if "://" not in s and ("/./" in s or "/../" in s or s.endswith(("/.", "/.."))):
        s = posixpath.normpath(s)
    if len(s) > 1:
        s = s.rstrip("/")
    return s


def norm(p: str) -> str:
    """Lexical path form plus verified Windows 8.3 prefix expansion.

    Comparisons see unresolved target stems too; expanding only fully resolved
    operands leaves those stems unequal to the same expanded configured root.
    long_name uses _lexical_norm, so prefix peeling never recurses into this call.
    """
    return _lexical_norm(long_name(p))


def under(path: str, root: str) -> bool:
    """True if `path` is `root` or sits inside it. False when root is empty --
    an unconfigured machine must get no rule rather than a rule against ""."""
    if not root or not path:
        return False
    r, p = norm(root), norm(path)
    return p == r or p.startswith(r + "/")


def is_root(path: str, root: str) -> bool:
    """True only if `path` IS `root`. A search scoped to a subdirectory is deliberate
    and must not be blocked; over-blocking is the worse failure because the reason is
    not obvious from the message."""
    return bool(root) and bool(path) and norm(path) == norm(root)


def contains_root(path: str, root: str) -> bool:
    """True if `path` is a PROPER ANCESTOR of `root` -- i.e. a recursive walk from
    `path` necessarily descends into `root`.

    The mirror image of `is_root`, and the direction it cannot see. `is_root` is
    deliberately exact so that a search scoped INTO a big tree stays allowed; but a
    search rooted ABOVE one is strictly worse than a search rooted AT it, and matched
    nothing.

    MEASURED 2026-09-21, the case that found this: `reference\\` (~60 GB) sits at
    `<...>/Desktop/Modding/X4/reference`, and its parent `<...>/Desktop/Modding/X4`
    WAS `X4_TOOLKIT` until the dev repo was retired. So the ancestor was covered only
    BY COINCIDENCE, through the toolkit root, and the retirement silently removed that
    coverage: `cd <...>/Desktop/Modding/X4 && grep -rl x .` was ALLOWED while the same
    shape at the toolkit root, at reference/ itself, and at the game root all denied.
    A guard that depends on two paths happening to coincide is a guard with an
    unowned axis (CLAUDE.md #37).

    Compares NORMALISED paths with a trailing separator, so `/a/bc` is not treated as
    living under `/a/b`.

    PROPER ancestry (`path != root`) falls out of that separator and is NOT tested for
    separately: an explicit `if p == r: return False` was written here first and a
    mutant SURVIVED its removal, because `r.startswith(r + "/")` is already False. A
    clause no twin can kill is decoration (CLAUDE.md #26), so it is gone and the
    partition is pinned by `test_contains_root_is_NOT_is_root` on the behaviour instead.
    """
    if not path or not root:
        return False
    p, r = norm(path), norm(root)
    return r.startswith(p.rstrip("/") + "/")


def is_abs(p: str) -> bool:
    """Absolute after normalisation -- `C:/x` and `/c/x` are both absolute."""
    return norm(p).startswith("/")


def join_cwd(cwd: str, p: str) -> str:
    """Resolve `p` against the directory in force, or return "" when that is unknowable.

    Returning "" rather than guessing is the whole safety property: when the hook does
    not know the shell's starting directory, `cd extensions && rm -rf amod` must reach
    NO rule. Inventing a root there would fire on unrelated work, which is the failure
    mode that gets a guard ignored. Since lane F (2026-10-02) the starting directory IS
    known whenever the payload carries an absolute `cwd` (session_cwd); "" remains the
    answer when it does not, or after a `cd` the hook cannot resolve.
    """
    if not p:
        return cwd
    if is_abs(p):
        return p
    if not cwd or not is_abs(cwd):
        return ""
    return norm(norm(cwd) + "/" + p)


#: The HOST's path semantics for physical evidence (LINUX GAP, v4.0 release review). On POSIX a
#: backslash written in a BASH command is a filename character, not a separator; only text a
#: Windows dialect (PowerShell, cmd) spelled uses it as one. Module-level so a test can force
#: the POSIX branch on Windows.
POSIX_HOST = os.name != "nt"
#: Evidence `dialect` values: "bash" (POSIX shell text) or "windows" (PowerShell/cmd text).
WINDOWS_DIALECT = "windows"


def physical_join(cwd: str, path: str, *, posix_bash: bool = False) -> str:
    """Keep case for file I/O; never reuse a case-folded comparison path.

    `posix_bash`: the path was written in a Bash command on a POSIX host, where only `/`
    separates and `C:/x` or `\\tmp\\x` is RELATIVE -- so nothing is folded or guessed."""
    if posix_bash:
        if not path:
            return cwd
        if posixpath.isabs(path):
            joined = posixpath.normpath(path)
        elif not cwd or not posixpath.isabs(cwd):
            return ""
        else:
            joined = posixpath.normpath(cwd + "/" + path)
        # normpath drops a trailing `/`; the Windows branch keeps it, so evidence for
        # `cp f "$X4_SAVES/"` differed by host (ubuntu CI run 39187589960,
        # test_save_provenance). save_policy decides a directory destination by is_dir(), so
        # this is spelling parity -- the row says what the command wrote, on every host.
        if path.endswith("/") and not joined.endswith("/"):
            joined += "/"
        return joined
    def provider_file(value):
        if not value:
            return value
        folded = value.replace(chr(92), "/")
        provider = _PROVIDER.match(folded.lower())
        return folded[provider.end():] if provider else value
    cwd, path = provider_file(cwd), provider_file(path)
    if not path:
        return cwd
    if is_abs(path):
        return path
    if not cwd or not is_abs(cwd):
        return ""
    return posixpath.normpath(cwd.replace(chr(92), "/") + "/" + path.replace(chr(92), "/"))


# ---------------------------------------------------------------- tokenising
_OPERATORS = ("&&", "||", ";", "|", "&", "\n")

# Inside DOUBLE quotes bash treats a backslash as an escape ONLY before these five.
# Before anything else it is a LITERAL backslash -- which is how every Windows path is
# written.
#
# MEASURED 2026-09-01: unescaping unconditionally turned a quoted Windows path into one
# with the separators deleted, so it matched no root and a delete of the game install
# fired NOTHING -- the HARD BLOCK bypassed by the most natural way a Windows user writes
# a path. c400a05 denied it; the parse pass allowed it. Five call sites carried the same
# wrong rule; this constant is the single answer.
_DQ_ESCAPES = "$" + chr(96) + chr(34) + chr(92) + chr(10)


def _qwalk(s: str, q0: str = "", end=None):
    """THE quote walk every scanner here shares: yield (index, char, in_quote, kind), where
    kind is the quote in force -- "'" single, '"' double, "$'" ANSI-C, "" none. `q0` is the
    quote state the text STARTS in (a line inside a multi-line string); `end`, a list, receives
    the state it ENDS in.

    ANSI-C QUOTING (FX-G2 / reviewer E5, v4.0.0 delta review): inside `$'...'` a backslash
    escapes ANY character, the quote included. Every scanner here read `$'it\\'s'` as a single-
    quoted `it\\` and then OPENED a new quote at `s'` that never closed -- so `echo $'it\\'s';
    rm -rf <game>` was ONE segment whose verb is echo, and the hard block never saw the rm.
    tokens() already decoded the form; the scanners that decide where commands start did not.

    A BACKSLASH OUTSIDE QUOTES ESCAPES THE NEXT CHARACTER, so `don\\'t` opens nothing and
    `\\$'x'` is not ANSI-C. Inside double quotes it escapes only `$ \\` " \\\\ newline`."""
    q = q0
    dollar = False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if q:
            yield i, c, True, q
            if q == "$'":
                if c == chr(92) and i + 1 < n:
                    i += 1
                    yield i, s[i], True, q
                elif c == "'":
                    q = ""
            elif c == q:
                q = ""
            elif (c == chr(92) and q == '"' and i + 1 < n
                  and s[i + 1] in _DQ_ESCAPES):
                i += 1
                yield i, s[i], True, q
            dollar = False
        elif c == chr(92) and i + 1 < n:
            yield i, c, False, ""
            i += 1
            yield i, s[i], False, ""          # escaped: never opens a quote
            dollar = False
        elif c in "\"'":
            q = "$'" if (c == "'" and dollar) else c
            yield i, c, True, q
            dollar = False
        else:
            yield i, c, False, ""
            dollar = c == "$"
        i += 1
    if end is not None:
        end.append(q)


def _scan(s: str):
    """Yield (char, in_quote) so every helper can respect quoting identically (_qwalk).

    A BACKSLASH OUTSIDE QUOTES ESCAPES THE NEXT CHARACTER. Without that, `don\\'t`
    opened a quote state that never closed, and everything after it read as quoted --
    which `blank_quoted` then erased and `segments` refused to split. `tokens()` already
    honoured the escape, so the two disagreed: tokens saw `dont`, _scan saw an open
    quote (MEASURED 2026-09-01).
    """
    for _i, c, inq, _k in _qwalk(s):
        yield c, inq


def join_continuations(s: str) -> str:
    """Remove backslash-newline, which bash removes before it does anything else.

    A line continuation is NOT a separator -- the shell splices the two lines into one
    command. `segments()` split on it anyway, because `_scan` reports the escaped
    newline with in_quote False and nothing marks it as ESCAPED. MEASURED 2026-09-02:

        rm -rf \\<NL>  "<game>"   ->  segments ['rm -rf \\', '"<game>"']
                                        verb 'rm' with operand \\, and the
                                        path in a segment of its own with no verb

    So every verb-keyed rule lost its operand at once, including all three hard blocks.
    Found against 6 of 12 fuzz seeds; the other 6 have no whitespace to break at.

    Inside SINGLE quotes it is literal text and is left alone. Inside double quotes bash
    does splice it, so it is removed there too.
    """
    out = []
    q = ""
    i = 0
    while i < len(s):
        c = s[i]
        if q == "'":
            if c == "'":
                q = ""
            out.append(c)
            i += 1
            continue
        if c == chr(92) and i + 1 < len(s) and s[i + 1] == chr(10):
            i += 2                      # the splice: emit neither character
            continue
        if q == '"':
            if c == '"':
                q = ""
            elif c == chr(92) and i + 1 < len(s):
                out.append(c)
                i += 1
                out.append(s[i])
                i += 1
                continue
        elif c in ("'", '"'):
            q = c
        elif c == chr(92) and i + 1 < len(s):
            out.append(c)
            i += 1
            out.append(s[i])
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def strip_comments(s: str) -> str:
    """Blank `# ...` to end of line, when the `#` starts a word outside quotes.

    The word-boundary test is what keeps `$#`, `${x#y}` and `http://a#b` intact -- in
    all three the `#` is preceded by a non-space, so none is a comment.
    """
    out, q, i, prev = [], "", 0, ""
    while i < len(s):
        c = s[i]
        if q == "$'":                               # ANSI-C: `\'` does not close (E5)
            out.append(c)
            if c == chr(92) and i + 1 < len(s):
                i += 1
                out.append(s[i])
            elif c == "'":
                q = ""
        elif q:
            out.append(c)
            if c == q:
                q = ""
            elif (c == chr(92) and q == '"' and i + 1 < len(s)
                  and s[i + 1] in _DQ_ESCAPES):
                i += 1
                out.append(s[i])
        elif c == chr(92) and i + 1 < len(s):
            out.append(c)
            i += 1
            out.append(s[i])
        elif c in "\"'":
            q = "$'" if (c == "'" and prev == "$") else c
            out.append(c)
        elif c == "#" and (prev == "" or prev.isspace()):
            while i < len(s) and s[i] != "\n":      # keep the newline: it is a separator
                i += 1
            continue
        else:
            out.append(c)
        prev = c
        i += 1
    return "".join(out)


def blank_quoted(s: str) -> str:
    """Replace the CONTENTS of quoted strings with spaces, keeping length and the
    quote characters. Flag detection must not read a hyphenated search PATTERN as
    flags, and a heredoc marker inside a string is data, not a marker."""
    out = []
    for c, inq in _scan(s):
        out.append(" " if (inq and c not in "\"'") else c)
    return "".join(out)


def blank_single_quoted(s: str) -> str:
    """Blank the contents of SINGLE-quoted strings only, keeping length and quotes.

    The distinction matters wherever the question is "would the shell expand this":
    `'$?'` is literal text, `"$?"` is an expansion. blank_quoted() erases both, which
    is right for flag and marker detection and wrong for expansion detection -- using
    it in dollarq_after_pipe would have silenced `echo "rc=$?"`, a genuine hit.
    """
    out, q, prev = [], "", ""
    i = 0
    while i < len(s):
        c = s[i]
        if q == "$'":                               # ANSI-C: literal, `\'` does not close (E5)
            out.append(c if c == "'" else " ")
            if c == chr(92) and i + 1 < len(s):
                i += 1
                out.append(" ")
            elif c == "'":
                q = ""
        elif q == "'":
            out.append(c if c == "'" else " ")
            if c == "'":
                q = ""
        elif q == '"':
            out.append(c)
            if c == '"':
                q = ""
            elif (c == chr(92) and i + 1 < len(s) and s[i + 1] in _DQ_ESCAPES):
                i += 1
                out.append(s[i])
        elif c == chr(92) and i + 1 < len(s):
            out.append(c)
            i += 1
            out.append(s[i])
        elif c in "\"'":
            q = "$'" if (c == "'" and prev == "$") else c
            out.append(c)
        else:
            out.append(c)
        prev = c
        i += 1
    return "".join(out)


def segments(cmd: str, raw: bool = False) -> list[str]:
    """Split on shell separators that are OUTSIDE quotes. A `|` inside grep -E 'a|b'
    is not a pipeline. `raw`: the same pieces before _unwrap (reserved words kept), one per
    segment, in the same order."""
    parts, buf, chars = [], [], list(_scan(cmd))
    i = 0
    while i < len(chars):
        c, inq = chars[i]
        if not inq:
            # A REDIRECT OPERATOR is consumed whole, before any separator test. `>|`
            # contains a pipe and `2>&1` contains an ampersand: splitting on those tore
            # the redirect away from its target, so every write rule saw a redirect with
            # nothing after it. Found by E2E, because redirects() had only ever been
            # tested on a string that was never split.
            j = i
            if c == "&" and i + 1 < len(chars) and not chars[i + 1][1] \
                    and chars[i + 1][0] == ">":
                j = i + 1
            if chars[j][0] in "<>":
                k = j + 1
                while k < len(chars) and not chars[k][1] and chars[k][0] in "<>|&":
                    k += 1
                buf.extend(chars[m][0] for m in range(i, k))
                i = k
                continue
            two = c + (chars[i + 1][0] if i + 1 < len(chars) and not chars[i + 1][1] else "")
            if two in ("&&", "||"):
                parts.append("".join(buf))
                buf = []
                i += 2
                continue
            if c in ";|&\n":
                parts.append("".join(buf))
                buf = []
                i += 1
                continue
        buf.append(c)
        i += 1
    parts.append("".join(buf))
    if raw:
        return [p for p in parts if p.strip()]
    return [_unwrap(p) for p in parts if p.strip()]


def piped_in(cmd: str) -> list:
    """For each segment segments(cmd) returns, in order: is it fed by a PIPE (`|`)?

    The same walk as segments(), recording which operator ENDED the previous part. A
    program read from STDIN only exists when something is piped in: `ls; pwsh -NoProfile`
    reads the tool's own (empty) stdin, not ls's output (v3.3.0 release review, found by
    this lane's history replay -- pairing on ANY separator asked on `command -v pwsh`).
    """
    out, buf, chars, sep = [], [], list(_scan(cmd)), ""
    i = 0

    def close(next_sep):
        nonlocal buf, sep
        part = "".join(buf)
        if part.strip():
            out.append(sep == "|")
        elif sep == "|" and next_sep == chr(10):
            # `a |` NEWLINE `b` is ONE pipeline: bash continues a line that ends in `|`.
            # The newline used to overwrite the pipe, so `b` read as not piped (E3/E4).
            buf = []
            return
        buf = []
        sep = next_sep
    while i < len(chars):
        c, inq = chars[i]
        if not inq:
            j = i
            if c == "&" and i + 1 < len(chars) and not chars[i + 1][1] \
                    and chars[i + 1][0] == ">":
                j = i + 1
            if chars[j][0] in "<>":
                k = j + 1
                while k < len(chars) and not chars[k][1] and chars[k][0] in "<>|&":
                    k += 1
                buf.extend(chars[m][0] for m in range(i, k))
                i = k
                continue
            two = c + (chars[i + 1][0] if i + 1 < len(chars) and not chars[i + 1][1] else "")
            if two in ("&&", "||"):
                close(two)
                i += 2
                continue
            if c in ";|&\n":
                close(c)
                i += 1
                continue
        buf.append(c)
        i += 1
    close("")
    return out


#: Shell RESERVED WORDS that can stand immediately before a simple command inside one
#: `;`-delimited segment. MEASURED 2026-09-01 by the syntax-class fuzzer: because the
#: splitter cuts on `;` and `&&`, `if true; then rm -rf <game>; fi` yields the segment
#: `then rm -rf <game>`, whose verb() is `then` -- so EVERY verb-keyed rule missed and
#: the hook fell silent. 90 bypasses over 10 compound forms x 9 seeds, including the
#: three HARD BLOCKS (game root, extensions wholesale, reference tree). The 10th seed
#: was immune because it is redirect-keyed, not verb-keyed, which is what pins the
#: root cause: the operand was always there, the VERB was the keyword.
#:
#: `time`/`nice`/`sudo` are handled separately by WRAPPERS in verb(); this set exists
#: because the keyword has to leave the SEGMENT before any rule looks at it, so that
#: one fix serves every rule instead of each rule learning the grammar.
RESERVED = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until",
            "case", "esac", "in", "for", "select", "function", "!", "{", "}", "coproc", "[[", "]]"}

#: K1: the verbs whose every file mutation reaches an evidence row (rm/mv/cp/redirect/tee/
#: touch/mkdir targets), plus verbs with no file effect of their own. A segment whose verb
#: is anything else -- an interpreter, a script, a cmdlet the translator passed through --
#: may mutate a file no row shows, so the save bridge must not lift a profile ask for it.
#: Deliberately an ALLOWLIST: an unknown verb costs an ask, never a silent advise.
SAVE_MODELLED_VERBS = frozenset({
    "", "rm", "rmdir", "unlink", "mv", "cp", "tee", "touch", "mkdir", "echo", "printf",
    "cat", "ls", "cd", "pushd", "popd", "true", "false", ":", "test", "[", "pwd",
    "get-childitem", "get-item", "test-path", "get-content", "write-output", "write-host",
}) | (RESERVED - {"coproc"})

#: The `in` of `case WORD in`. A TOKEN, not the substring: see the block in
#: strip_compound_prefix() for the bypass that shape caused.
_CASE_IN = re.compile(r"\s+in(?:\s|$)")

#: A `case` ARM LABEL, and nothing else. The label is a single glob token --
#: `x)`, `*)`, `*.txt)`, `a|b)`, `"a")` -- so it carries NO WHITESPACE, and THAT is
#: what makes this safe: every dangerous rule needs an OPERAND, an operand needs a
#: space, so a spaceless label can never hide one.
#:
#: MEASURED 2026-09-01, and it is why whitespace must stay excluded: the first
#: version was `^[^()|&;]*\)\s`, whose class allowed SPACES, so it also matched the
#: tail of a PROCESS SUBSTITUTION -- `diff <(cd "$GAME" && rm -rf extensions) <(echo b)`
#: splits to `rm -rf extensions)`, the whole thing was eaten as a "label", and the
#: delete went silent. A fix for one bypass that opens another is worse than the bug,
#: and only the corpus diff -- 20 commands with a paren-suffixed verb -- surfaced it.
#:
#: MEASURED 2026-09-04: three MORE legal spellings walked past this, each a total
#: bypass of every hard block, found only once the fuzzer was taught to vary its
#: template PARAMETERS (it had always emitted `case x in x)`):
#:   `case $x in *)rm ...`    -- no space after `)`, so the old trailing `\s` missed it
#:   `case $x in (*) rm ...`  -- the POSIX `(pattern)` arm; `(` was excluded outright
#:   `case $x in "a") rm ...` -- a quoted label; the guard rejected ANY quoted char
#: The trailing `\s` was never the safety property -- the no-whitespace CLASS is --
#: so dropping it costs nothing. `rm -rf extensions)` still cannot match: `[^\s()&;]+`
#: stops at the first space, long before the `)`.
_CASE_ARM = re.compile(r"^\(?[^\s()&;]+\)")

#: A function DEFINITION header is not a reserved word, so it survived the first
#: version of this and `f() { rm -rf <game>; }; f` stayed blind while the other nine
#: compound forms were fixed. Both spellings: `f() {` and `function f {`.
_FUNC_HEAD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*\(\)\s*")


def _strip_reserved(s: str) -> str:
    """Drop leading shell reserved words (and a `case` arm label) from a segment.

    Token equality, never a prefix match: a program called `do_thing` or `iffy` keeps
    its name. Stops at the first token that is not reserved, so `for i in 1` becomes
    `i in 1` (harmless -- it holds no command) rather than being consumed whole.
    """
    prev = None
    while s and s != prev:
        prev = s
        head = s.split(None, 1)
        if head and head[0] == "case":
            # `case WORD in LABEL) cmd` -- WORD is not reserved, so a plain keyword loop
            # stops on it and the label rule below never gets to run. Consume through the
            # `in`, and the label rule then removes `LABEL)`.
            rest = head[1] if len(head) > 1 else ""
            # TOKEN boundary, never a SUBSTRING. `rest.index("in")` cut inside the
            # WORD for `case $string in`, `case $line in`, `case install in` --
            # leaving `ary in *) rm -rf <game>` behind, so verb() returned `ary` and
            # EVERY verb-keyed rule missed at once. MEASURED 2026-09-04 E2E against
            # both hook copies: a TOTAL bypass of all three HARD BLOCKS, and the NAME
            # backstop died with it because rm_t was empty. 41 of 15,619 historical
            # commands use `case ... in`; 0 carry an "in"-bearing word, so closing it
            # can only tighten. The `in` keyword always FOLLOWS the word, so it always
            # has whitespace before it -- which keeps `case in in *)` correct too,
            # where an `(?:^|\s)` form would consume the word instead.
            m = _CASE_IN.search(rest)
            s = (rest[m.end():] if m else rest).lstrip()
            continue
        if head and head[0] == "function":
            # `function NAME {` -- the NAME is not reserved, so drop it with the keyword
            rest = head[1].lstrip() if len(head) > 1 else ""
            nxt = rest.split(None, 1)
            s = (nxt[1].lstrip() if len(nxt) > 1 else "") if nxt else ""
            continue
        if head and head[0] in RESERVED:
            s = head[1].lstrip() if len(head) > 1 else ""
            continue
        m = _FUNC_HEAD.match(s)
        if m:
            s = s[m.end():].lstrip()
            continue
        # `case x in x) rm ...` -- the arm label sits between `in` and the command and
        # is not a token, so it needs its own step. Two conditions, each scoped to what
        # it is actually about rather than to the whole span:
        #   * the CLOSING `)` must be UNQUOTED -- that is what makes this an arm rather
        #     than part of a quoted string. (The old test rejected any quoted char
        #     anywhere, which threw away the legitimate `"a")` label with it.)
        #   * no `(` may OPEN inside the label, or `rm -rf $(echo x)` is torn apart.
        #     A single LEADING `(` is the POSIX arm and is legitimate.
        m = _CASE_ARM.match(s)
        if m:
            marks = list(_scan(s[:m.end()]))
            closing_ok = bool(marks) and marks[-1][0] == ")" and not marks[-1][1]
            inner_open = any(ch == "(" and not inq
                             for i, (ch, inq) in enumerate(marks) if i != 0)
            if closing_ok and not inner_open:
                s = s[m.end():].lstrip()
    return s


def _unwrap(seg: str) -> str:
    """Strip subshell/group punctuation at a segment's edges.

    `(cd X && rm -rf Y)` splits into `(cd X` and ` rm -rf Y)`, so the verb carried a
    leading paren and the operand a trailing one -- MEASURED 2026-09-01: verb was
    `'(cd'` and the operand `'extensions)'`, so neither matched anything and the
    subshell form of a game delete was silently allowed.

    A trailing `)` is punctuation only when UNBALANCED. `rm -rf $(echo x)` closes its
    own paren, and stripping that would tear up the token.
    """
    s = seg.strip()
    while s[:1] in ("(", "{"):
        s = s[1:].lstrip()
    while s and s[-1] in ")}":
        marks = list(_scan(s))
        if marks and marks[-1][1]:          # quoted -- it is data, not punctuation
            break
        opens = sum(1 for ch, inq in marks if not inq and ch in "({")
        closes = sum(1 for ch, inq in marks if not inq and ch in ")}")
        if closes <= opens:
            break
        s = s[:-1].rstrip()
    # LAST: a reserved word left in front of the command makes verb() return the
    # KEYWORD, and every verb-keyed rule then misses. Done here, at the one place every
    # segment passes through, so no rule has to know shell grammar for itself.
    return _strip_reserved(s)


#: Bash ANSI-C escapes, decoded inside a $'...' run ONLY. The $"..." form is
#: locale translation and its body is literal, so it is deliberately not decoded.
_ANSI_C_SIMPLE = {'a': chr(7), 'b': chr(8), 'e': chr(27), 'E': chr(27),
                  'f': chr(12), 'n': chr(10), 'r': chr(13), 't': chr(9),
                  'v': chr(11), chr(92): chr(92), chr(39): chr(39),
                  chr(34): chr(34), '?': '?'}


def _ansi_c_decode(body: str) -> str:
    """Decode one ANSI-C quoted body.

    The hex and octal forms are the ones that matter: two different spellings of
    `rm` that reached `_verb_name` as literal escape text and came out as `x6d`
    and `155`. B2, MEASURED as a total bypass of all three hard blocks.
    """
    out = []
    i = 0
    while i < len(body):
        c = body[i]
        if c != chr(92) or i + 1 >= len(body):
            out.append(c)
            i += 1
            continue
        n = body[i + 1]
        if n == 'x':
            h = ''
            j = i + 2
            while j < len(body) and len(h) < 2 and body[j] in '0123456789abcdefABCDEF':
                h += body[j]
                j += 1
            if h:
                out.append(chr(int(h, 16)))
                i = j
                continue
        elif n in '01234567':
            o = ''
            j = i + 1
            while j < len(body) and len(o) < 3 and body[j] in '01234567':
                o += body[j]
                j += 1
            out.append(chr(int(o, 8) & 0xFF))
            i = j
            continue
        elif n in _ANSI_C_SIMPLE:
            out.append(_ANSI_C_SIMPLE[n])
            i += 2
            continue
        # An unknown escape keeps its backslash, as bash does.
        out.append(c)
        i += 1
    return ''.join(out)


#: C1 (v4.0 release review): the dequoted text of every word whose UNQUOTED part holds a
#: brace expansion (`save_00{0,1,2}.xml.gz`, `x{1..3}`, `{a,b}/c`). tokens() returns the
#: word as written, so a rule reading it sees ONE literal path where bash runs the command on
#: several: MEASURED, `rm <saves>/save_00{0,1,2}.xml.gz` reached the save bridge as one save
#: that did not exist, was "backed up" as nothing, and lifted the profile ask. Filled by
#: tokens() as a side channel (keyed by TEXT, so a quoted word spelled identically in the same
#: command is caught too -- the fail-closed direction), cleared per facts() call; facts()
#: marks such an operand's evidence row unresolved. A quoted `'{0,1}'` is a literal name and
#: is not recorded.
_BRACE_WORDS: set = set()
_BRACE_SEQ = re.compile(r"^(-?[0-9]+|[A-Za-z])[.][.](-?[0-9]+|[A-Za-z])([.][.]-?[0-9]+)?$")


def _has_brace_expansion(m: str) -> bool:
    """Does `m` (a word with every QUOTED character replaced by NUL) hold a brace expansion
    bash would perform: an unquoted `{` not opening `${`, its matching unquoted `}`, and
    between them a top-level unquoted `,` or a `x..y` sequence? Over-matching only makes an
    operand unresolved, which keeps the ask -- never the reverse."""
    for i, c in enumerate(m):
        if c != "{" or (i and m[i - 1] == "$"):
            continue
        depth = 0
        for j in range(i, len(m)):
            if m[j] == "{":
                depth += 1
            elif m[j] == "}":
                depth -= 1
                if depth == 0:
                    body, d2 = m[i + 1:j], 0
                    for ch in body:
                        d2 += (ch == "{") - (ch == "}")
                        if ch == "," and d2 == 0:
                            return True
                    if _BRACE_SEQ.match(body):
                        return True
                    break
    return False


def tokens(seg: str) -> list[tuple[str, bool]]:
    """(text, was_quoted) per token. Quotes are stripped from the text; a
    backslash-escaped space JOINS, which is why `X4\\ Foundations` must stay one token
    -- the bash tokeniser split it and the game-delete block stopped firing.

    A word whose unquoted part holds a brace expansion is recorded in _BRACE_WORDS (C1)."""
    out, buf, quoted, started = [], [], False, False
    mbuf = []                               # buf with every quoted/escaped char as NUL
    q = ""
    i = 0

    def emit():
        text = "".join(buf)
        if "{" in mbuf and _has_brace_expansion("".join(mbuf)):
            _BRACE_WORDS.add(text)
        out.append((text, quoted))

    while i < len(seg):
        c = seg[i]
        if q:
            if c == q:
                q = ""
            elif (c == chr(92) and q == '"' and i + 1 < len(seg)
                  and seg[i + 1] in _DQ_ESCAPES):
                i += 1
                buf.append(seg[i])
                mbuf.append(chr(0))
            else:
                buf.append(c)
                mbuf.append(chr(0))
        elif c in "\"'":
            # ANSI-C / locale quoting: `$'rm'` and `$"rm"` are the word `rm`. The `$`
            # is a quoting SIGIL, not part of the name. Without this the token was
            # `$rm` AND flagged quoted, so verb()'s `if quoted: return t` handed every
            # verb-keyed rule a name no rule has ever heard of -- a total bypass.
            dollar = bool(buf) and buf[-1] == "$"
            if dollar:
                buf.pop()
                mbuf.pop()
            if dollar and c == chr(39):
                # $'...' -- take the WHOLE run and decode it. Popping the sigil was
                # only half the job: inside a single-quoted run this loop appends
                # every character literally (the escape branch below is guarded by
                # q == '\"'), so the hex and octal spellings of a verb stayed as
                # literal escape TEXT and _verb_name reduced them to `x6d` / `155`.
                # B2, MEASURED: both ALLOW `rm -rf <game root>` past a hard block.
                j = i + 1
                body = []
                while j < len(seg) and seg[j] != chr(39):
                    if seg[j] == chr(92) and j + 1 < len(seg):
                        body.append(seg[j])
                        j += 1
                    body.append(seg[j])
                    j += 1
                dec = _ansi_c_decode(''.join(body))
                buf.append(dec)
                mbuf.extend(chr(0) * len(dec))
                quoted = started = True
                i = j + 1
                continue
            q = c
            quoted = started = True
        elif c == chr(92) and i + 1 < len(seg):
            i += 1
            buf.append(seg[i])          # \<space> joins; \x keeps x
            mbuf.append(chr(0))         # an escaped `{` / `,` is literal (C1)
            started = True
        elif c.isspace():
            if started:
                emit()
            buf, quoted, started = [], False, False
            mbuf = []
        else:
            buf.append(c)
            mbuf.append(c)
            started = True
        i += 1
    if started:
        emit()
    return out


def words(seg: str) -> list[str]:
    return [t for t, _ in tokens(seg)]


# -------------------------------------------------------------- assignments
_ASSIGN = re.compile(r"(?:^|[;&|\s])([A-Za-z_][A-Za-z0-9_]*)=")


def assignments(cmd: str) -> dict[str, str]:
    """NAME -> value for assignments made in this same command. LAST wins: the bash
    helper took the first, so a reassigned variable resolved to the stale value."""
    found: dict[str, str] = {}
    for seg in segments(cmd):
        for tok, _ in tokens(seg):
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", tok, re.S)
            if m:
                found[m.group(1)] = m.group(2)
        # An ARRAY is re-read from the RAW segment, because `tokens()` has already
        # stripped the quotes that hold a spaced path together -- and splitting the
        # de-quoted text gave `C:/Program` as element 0 of a Windows game path. That is
        # a wrong answer rather than a missing one, and a wrong one is compared against
        # the roots and cleared. Overrides the de-quoted value captured above.
        for m in re.finditer(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)=\(", seg):
            close = _match_paren(seg, m.end() - 1)
            if close > 0:
                found[m.group(1)] = seg[m.end() - 1:close + 1]
    # Read from the RAW command, because segments() splits inside `$( )` and strips the
    # `for` keyword (v3.3.0 release review, pre-arc notes).
    mask = _quote_mask(cmd)
    # NAME=$(...) / NAME="$(...)": the WHOLE substitution, not tokens()'s `$(realpath`.
    # Still unresolvable (has_unresolved) unless identity_subst can read it -- but a delete
    # through it now reaches the conservative branch with the text that names its root.
    for m in _ASSIGN_SUBST.finditer(cmd):
        if mask[m.start(1)]:
            continue
        open_ = m.end() - 1
        close = _match_paren(cmd, open_)
        if close > 0:
            found[m.group(1)] = "$" + cmd[open_:close + 1]
    # `for NAME in WORDS; do ...` -- NAME takes each word. Stored as an ARRAY so a whole
    # `"$NAME"` operand expands to EVERY word (resolve_all), not to the first alone.
    for m in _FOR_IN.finditer(cmd):
        if mask[m.start(1)]:
            continue
        rest = cmd[m.end():]
        end = len(rest)
        for i, (ch, inq) in enumerate(_scan(rest)):
            if not inq and ch in ";&|)" + chr(10):
                end = i
                break
        items = rest[:end].strip()
        if items:
            found[m.group(1)] = "(" + items + ")"
    return found


_ASSIGN_SUBST = re.compile(r"(?:^|[\s;&|()])([A-Za-z_][A-Za-z0-9_]*)=\"?\$\(")
_FOR_IN = re.compile(r"(?:^|[\s;&|()])for\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s")


_OPERAND_EXPANSION_TRUNCATED = [False]  # per facts() call, like the cwd/carrier scope signals


def _bounded_operand_values(values):
    out = []
    for value in values:
        if len(out) == 64:
            _OPERAND_EXPANSION_TRUNCATED[0] = True
            break
        out.append(value)
    return out


def resolve_all(tok: str, assigns: dict) -> list:
    """Every value a WHOLE-token reference can take: each element of an array (`"$f"` for
    a for-loop variable, `"${A[@]}"`), else the one resolve() gives -- read through
    identity_subst, so `$(realpath X)` is X."""
    m = re.fullmatch(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)(\[[@*]\])?\}?", tok)
    if m and (m.group(0).startswith("${") == m.group(0).endswith("}")):
        elems = _array_elements(assigns.get(m.group(1), ""))
        if elems:
            return [identity_subst(r) for e in elems for r in resolve_variants(e, assigns)]
    # `"$f/extensions"` inside `for f in A B`: one path PER element. Plain resolve()
    # substitutes the whole `(A B)` text, which is no path at all.
    arrays = {n for n in (a or b for a, b in _VAR.findall(tok))
              if _array_elements(assigns.get(n, ""))}
    if len(arrays) == 1:
        name = arrays.pop()
        ref = re.compile(r"\$\{" + name + r"\}|\$" + name + r"(?![A-Za-z0-9_])")
        return _bounded_operand_values(identity_subst(r) for e in _array_elements(assigns[name])
                for r in resolve_variants(ref.sub(lambda _m, e=e: e, tok), assigns))
    # Every value an unassigned `${V:+w}` / `${V-w}` can give (FX-G6 / reviewer K C1).
    out = []
    for one in resolve_variants(tok, assigns):
        # ...and a value that resolves, through other assignments, TO an array's text
        # (`Z="$f"; rm -rf "$Z"`) is that array's elements, not its literal `(a b)`.
        elems = _array_elements(one)
        if elems and not _array_elements(tok):
            out += _bounded_operand_values(identity_subst(r) for e in elems for r in resolve_variants(e, assigns))
        else:
            out.append(identity_subst(one))
    return out


_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

#: ANY parameter expansion, not just the two shapes `_VAR` can name. `_VAR` exists to
#: EXTRACT an identifier and only matches `${NAME}` with the brace closing straight
#: after the name -- so `${DST%/}`, `${VAR:-default}`, `${VAR//a/b}` and `${ARR[0]}`
#: matched neither alternative, `has_unresolved` returned False, and the token was
#: treated as a fully-resolved literal path.
#:
#: The DIRECTION is the problem. `hit(..., conservative=True)` exists so an operand
#: the hook cannot resolve still refuses; believing we HAD resolved it skips that
#: branch entirely. MEASURED 2026-09-02: `Z="<game>ZZ"; rm -rf "${Z%ZZ}"` was a
#: silent ALLOW, and 3 expansion mutators weakened every seed they applied to.
#:
#: Separate from `_VAR` on purpose: "is there an expansion here" and "what is it
#: called" are different questions, and one regex answering both is what made the
#: narrow one authoritative.
_EXPANSION = re.compile(r"\$\{|\$[A-Za-z_]|\$[0-9@*#?!$]")


#: `~` and `$HOME` are values a hook genuinely CAN know, unlike `$(...)`. Without them a
#: save delete written the ordinary way was invisible. MEASURED 2026-09-01, E2E through
#: protect-bash.sh, all spellings of the SAME file:
#:      rm -f "C:/Users/.../Egosoft/X4/<id>/save/s.xml.gz"  -> ask
#:      rm -f ~/Documents/Egosoft/X4/<id>/save/s.xml.gz     -> *** SILENT ALLOW ***
#:      rm -f $HOME/Documents/.../save/s.xml.gz             -> *** SILENT ALLOW ***
#: Saves are the one thing here with no backup and no undo, which is why they ask at all.
#: (Every PATH dialect was already handled -- absolute, relative after `cd`, the MSYS
#: `/c/...` form and backslashes all resolved correctly. Only home did not.)
#:
#: Deliberately NOT a name backstop: this resolves the operand to a real path and lets the
#: existing root comparison decide, so it cannot fire on something merely home-shaped.
_HOME = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
_HOME_VARS = ("$HOME", "${HOME}", "$USERPROFILE", "${USERPROFILE}")
_SEPS = ("/", chr(92))


def expand_home(tok: str) -> str:
    """A LEADING `~`, `$HOME` or `$USERPROFILE` becomes the real home directory.

    Leading only: `a~b` and `x/$HOME` are not home references, and rewriting them would
    invent a path the user never wrote.
    """
    if not _HOME:
        return tok
    if tok == "~" or (tok[:1] == "~" and tok[1:2] in _SEPS):
        return _HOME + tok[1:]
    for v in _HOME_VARS:
        if tok == v:
            return _HOME
        if tok.startswith(v) and tok[len(v):len(v) + 1] in _SEPS:
            return _HOME + tok[len(v):]
    return tok


#: `${NAME[idx]<op><word>}` -- the forms `_VAR` cannot name. Restricted to a body with
#: no nested brace, because a nested expansion is not resolvable from text anyway and a
#: greedy match there would swallow the wrong closing brace.
_VAR_OP = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)"
                     r"(\[[0-9]+\])?"
                     r"([#%/:^,+=-][^{}]*)?\}")

#: A pattern carrying any of these is a GLOB, and matching it needs the filesystem
#: semantics this hook deliberately does not have. Such an expansion is left unresolved.
_GLOB_CHARS = set("*?[]")


def _array_elements(value: str) -> list:
    """Elements of an array assignment `(a "b c" d)`, or [] if it is not one."""
    v = value.strip()
    if not (v.startswith("(") and v.endswith(")")):
        return []
    return [t for t, _q in tokens(v[1:-1])]


#: The three states a variable the command never ASSIGNED can be in, for resolve_variants().
_UNASSIGNED_STATES = ("unset", "empty", "set")


def _apply_op(name: str, idx, op: str, assigns: dict, env=None, seen=None) -> str:
    """bash's value for one expansion, or None when text alone cannot say.

    Returning None is not a failure -- it routes the token to the conservative path,
    which is the correct answer for `${V%/*}` (a glob) or `${V:2:5}` (an offset).

    `env` / `seen` (FX-G6 / reviewer K C1): a variable the command never assigned has
    MORE THAN ONE possible value under `-`/`=`/`+` (with or without the colon) -- unset,
    set but empty, or set to something -- and the single answer returned without `env`
    is only one of them. `seen` collects such names; `env` maps a name to the state
    (`_UNASSIGNED_STATES`) a caller wants this expansion judged under. resolve_variants()
    enumerates them, so a verdict is the strictest over every value the shell can give.
    """
    unassigned = name not in assigns and idx is None and name.upper() not in ROOT_VARS
    if unassigned and op and (op[0] in "-=+" or (op[0] == ":" and op[1:2] in ("-", "=", "+"))):
        colon = op[0] == ":"
        okind, orest = (op[1], op[2:]) if colon else (op[0], op[1:])
        if seen is not None:
            seen.add(name)
        state = (env or {}).get(name)
        if state == "set":
            # Its own value, which text cannot know -- unresolved -- or the alternate word.
            return orest if okind == "+" else None
        if state in ("unset", "empty"):
            is_set = state == "empty" and not colon   # `:` treats EMPTY as unset
            if okind == "+":
                return orest if is_set else ""
            return "" if is_set else orest
    known = name in assigns
    value = assigns.get(name, "")
    if not known and name.upper() in ROOT_VARS:
        # FX-G4 / H2: a ROOT variable the command did not assign is SET in the environment
        # the shell runs in -- `${X4_GAME:-/x}` is the game root, not `/x`. Unknown here, so
        # subst_root_var judges the token as that root.
        return None
    if idx is not None:
        elems = _array_elements(value)
        n = int(idx[1:-1])
        if not elems or n >= len(elems):
            return None
        value, known = elems[n], True
    elif known:
        elems = _array_elements(value)
        if elems:                      # `A=(x y)` used as plain `$A` is element 0
            value = elems[0]

    if not op:
        return value if known else None

    kind, rest = op[0], op[1:]
    if kind == ":" and rest[:1] in ("-", "=", "+", "?"):
        kind, rest = rest[0], rest[1:]
        known = known and value != ""      # `:-` also treats EMPTY as unset
    if kind in ("-", "="):
        return value if known else rest
    if kind == "+":
        # FX-G5 / reviewer J2 item 9 (pre-arc, MEASURED: `rm -rf "${PATH:+$X4_GAME}"` was
        # allowed): a variable this command never ASSIGNED is not known to be unset -- its value
        # comes from the environment -- so the alternate word may be what the shell gives.
        # It is ONE of the values (FX-G6 / reviewer K C1, MEASURED: this answer alone lost
        # the EMPTY one, and `rm -rf "${NOPE:+zz}<reference>"` went deny -> allow); every
        # rule that judges a path asks resolve_variants(), which offers both.
        return rest if (known or name not in assigns) else ""
    if kind == "?":
        return value if known else None
    if not known:
        return None
    if kind in ("#", "%"):
        greedy = rest[:1] == kind
        pat = rest[1:] if greedy else rest
        if set(pat) & _GLOB_CHARS or not pat:
            return None
        if kind == "%":
            return value[:-len(pat)] if value.endswith(pat) else value
        return value[len(pat):] if value.startswith(pat) else value
    if kind == "/":
        every = rest[:1] == "/"
        body = rest[1:] if every else rest
        pat, sep, rep = body.partition("/")
        if not pat or set(pat) & _GLOB_CHARS:
            return None
        return value.replace(pat, rep) if every else value.replace(pat, rep, 1)
    return None                            # ^ , : offsets -- not reproducible from text


#: A ceiling on the SIZE of one resolved token. The loop below is bounded at 5
#: iterations and its own comment says "never a loop" -- true, and it was watching the
#: wrong axis: what grows is the STRING, and a value that refers to its own name grows
#: it MULTIPLICATIVELY. `B=...${B}${B}...` re-expands every reference on every pass.
#:
#: MEASURED 2026-09-06 on one 949-character command out of real session history:
#: growth is a clean x9 per pass -- 916, 10316, 94916, 856316, 7708916 -- so the
#: 4-character token "${B}" reached 7.7 MB in the five passes the loop already allowed.
#: Downstream that became 23,128,230 characters of carried command and 531,448
#: segments, and the guard process reached an 18.3 GB working set, leaving 0.7 GB free
#: of 31.8 GB WITH THE GAME RUNNING. This is the BLOCKING PreToolUse path, so it is a
#: hang of the whole session, and it needs no adversary: a self-referential assignment
#: is ordinary shell.
#:
#: 65536 is 3.3x the LONGEST of 17,268 real historical commands (19,583 chars), so no
#: real work can reach it. On exceeding it we return the PREVIOUS pass with the
#: variable references still in it, which `has_unresolved` reports as unresolved --
#: the file's existing channel for a value a hook cannot know. That is deliberate and
#: one-directional: refusing to RESOLVE keeps every rule looking, where truncating the
#: string would hand the rules a shorter operand and quietly narrow what they see.
_MAX_RESOLVED = 65536


def resolve(tok: str, assigns: dict[str, str], env=None, seen=None) -> str:
    """Substitute what this command itself assigned. Text is all a hook can see, so
    this is the most that could ever be resolved.

    ONE value. A token with an unassigned variable under `-`/`=`/`+` has several; a rule
    that judges a path asks resolve_variants() (`env` / `seen`: see _apply_op)."""
    def sub(m):
        name = m.group(1) or m.group(2)
        return assigns.get(name, m.group(0))

    def sub_op(m):
        got = _apply_op(m.group(1), m.group(2), m.group(3) or "", assigns, env, seen)
        return m.group(0) if got is None else got

    prev = None
    out = tok
    # Bounded on BOTH axes now: five passes AND _MAX_RESOLVED characters. The comment
    # here used to name only the first, which was true and was about the wrong one --
    # what grows is the string, and it grew x9 per pass.
    for _ in range(5):
        prev = out
        out = _VAR_OP.sub(sub_op, _VAR.sub(sub, out))
        if len(out) > _MAX_RESOLVED:
            # STOP, and leave the references standing. Returning `prev` is what makes
            # this safe rather than merely fast: the token still contains "${...}", so
            # has_unresolved() is True and the rules treat the operand as unknowable.
            #
            # WHAT THIS BOUND DOES NOT COVER, stated rather than implied: the check is
            # AFTER the substitution, so one oversized pass is allocated before it is
            # refused. That is deliberate -- pre-computing the size means re-doing the
            # substitution's own work -- and it is bounded: `prev` is at most
            # _MAX_RESOLVED, so a single pass is at most (references in prev) x (longest
            # assigned value), both of which are bounded by the command text. For the
            # longest real command in 17,268 (19,583 chars) the worst case is ~94 MB,
            # once, and then refused. The defect being fixed was 18.3 GB and unbounded.
            return expand_home(prev)
        if out == prev:
            break
    return expand_home(out)


#: How many distinct unassigned variables resolve_variants() enumerates EXHAUSTIVELY
#: (3 states each: 81 resolves at 4). Past it, every state of each name is still tried with
#: the others held in each uniform state -- see resolve_variants.
_MAX_BRANCH_NAMES = 4


def resolve_variants(tok: str, assigns: dict) -> list:
    """EVERY value resolve() can give `tok`, the plain resolve() first (FX-G6 / reviewer K
    C1). An unassigned variable under `${V:+w}` is unset (`""`) or set (`w`); under
    `${V-w}` unset (`w`), empty (`""`) or set (its own value, unresolved) -- and a rule must
    judge each, because `"${NOPE:+zz}<reference>"` IS `<reference>` when NOPE is unset.
    MEASURED: judging one value made that, `"${NOPE+zz}<reference>"` and
    `"${NOPE:+./junk}<game>"` ALLOWED (FX-G5 had dropped the empty branch to gain the
    alternate one). The states are taken PER NAME, so `${A:+./junk}${B:+<game>}` is judged
    with A unset and B set."""
    seen = set()
    out = [resolve(tok, assigns, seen=seen)]
    if not seen:
        return out
    names = sorted(seen)
    if len(names) <= _MAX_BRANCH_NAMES:
        combos = itertools.product(_UNASSIGNED_STATES, repeat=len(names))
    else:
        # Bounded, never silently one value: each name in each state, the rest uniform.
        # This samples rather than enumerates every combination. A mixed protected
        # branch may be missing, so the caller must refuse incomplete analysis.
        _OPERAND_EXPANSION_TRUNCATED[0] = True
        combos = [tuple(s if j == i else u for j in range(len(names)))
                  for u in _UNASSIGNED_STATES for i in range(len(names))
                  for s in _UNASSIGNED_STATES]
    for combo in combos:
        r = resolve(tok, assigns, env=dict(zip(names, combo)))
        if r not in out:
            out.append(r)
    return out


# `$(...)` and `` `...` `` are values this hook can NEVER know. Without them a
# substitution read as a LITERAL path: MEASURED 2026-09-01, `rm -rf "$(echo <game>)"`
# matched no root and was allowed, while c400a05 -- which grepped the whole command
# string -- denied it. Precision about operands bought blindness to indirection.
_SUBST = re.compile(r"\$\(|" + chr(96))

# An unexpanded root variable is the ONLY evidence there is: `rm -rf "$X4_GAME"` never
# contains the game path as text, so no amount of string matching can find it. The
# variable NAME is what identifies the root.
ROOT_VARS = {"X4_GAME": "game", "X4_REFERENCE": "reference", "X4_PROFILE": "profile",
             "X4_SAVES": "saves", "X4_MODS": "mods", "X4_TOOLKIT": "toolkit",
             "X4_DOCUMENTS": "documents",
             # The path config itself (FX-G2 item 1). Only protect-bash.sh's config pass sends
             # a `config` root, so `> "$X4_CONFIG"` resolves to the file it names there; no
             # rule targets the key, so everywhere else it is inert.
             "X4_CONFIG": "config"}


#: Interpreters this rule is about. Spelled through `_verb_name`, so an absolute
#: path or a `.exe` suffix folds in as it does everywhere else in this file.
_PYTHONS = ("python", "python3", "python2", "py", "pypy", "pypy3", "uv")


def has_unresolved(tok: str) -> bool:
    return bool(_EXPANSION.search(tok) or _SUBST.search(tok))


_ROOT_VAR_HEAD = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def subst_root_var(tok: str, roots: dict | None) -> str:
    """A token that STARTS with a root variable -- `$X4_REFERENCE`, `${X4_GAME}/extensions` --
    as that root's configured path plus the rest; every other token unchanged.

    R2-P1 (v4.0.0 review): a `cd "$X4_REFERENCE"` left the shell's directory unknowable, so
    `cd "$X4_REFERENCE" && rm -rf libraries` reached no rule while `rm -rf
    "$X4_REFERENCE/libraries"` was refused -- the variable NAME is what identifies the root
    (ROOT_VARS), for a directory exactly as for an operand. Only a LEADING variable is
    taken; what follows stays as written (`$X4_REFERENCE/$SUB` -> `<ref>/$SUB`, still inside
    the root; `${X4_REFERENCE}x` -> a sibling `<ref>x`, inside nothing). A root that is not
    configured is not substituted: the token stays unresolved, as before. Call it AFTER
    resolve(): an assignment in the command itself wins."""
    if not roots:
        return tok
    m = _ROOT_VAR_HEAD.match(tok)
    end = m.end() if m else 0
    name = (m.group(1) or m.group(2)) if m else ""
    if not m:
        # FX-G4 / reviewer H2, MEASURED: `rm -rf "${X4_GAME:-/x}"`, `"${X4_GAME%/}"`,
        # `"${X4_GAME:=/x}"` and `echo x > "${X4_REFERENCE:-/nope}/libraries/wares.xml"`
        # were ALLOWED -- only `$X` / `${X}` were taken. A root variable under ANY brace
        # operator may expand to that root (or, `%`, an ancestor of it), so it is judged AS
        # the root: conservative in the refusing direction, never a guess that it is safe.
        m2 = _ROOT_VAR_OP_HEAD.match(tok)
        if not m2:
            return tok
        name, end = m2.group(1), _brace_end(tok, 1) + 1
        if end <= 0:
            # UNCLOSED: the word was cut at a space inside the braces (`G=${X4_GAME:-C:/X4
            # Foundations}` tokenises as `${X4_GAME:-C:/X4`). The whole token is that root.
            end = len(tok)
        if not ROOT_VARS.get(name.upper()):
            # `${OTHER:-${X4_GAME}}`: another variable whose DEFAULT is a root variable may
            # expand to that root (fuzz-guard's default-value mutator, FX-G4).
            op = re.match(r":?[-=]", tok[m2.end():])
            if op:
                inner = tok[m2.end() + op.end():(end - 1 if tok[end - 1:end] == "}" else end)]
                sub = subst_root_var(inner, roots)
                if sub != inner:
                    return sub + tok[end:]
            return tok
    key = ROOT_VARS.get(name.upper())
    root = (roots.get(key) or "") if key else ""
    if not root:
        return tok
    return root + tok[end:]


#: `${NAME` followed by an OPERATOR (`:-`, `%`, `/`, `:=`, `[`, ...): not `}` and not a
#: name character, which `_ROOT_VAR_HEAD` already covers.
_ROOT_VAR_OP_HEAD = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?=[^}A-Za-z0-9_])")
_VAR_ANY = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)|\$([A-Za-z_][A-Za-z0-9_]*)")


def _brace_end(tok: str, i: int) -> int:
    """Index of the `}` closing the `{` at tok[i], or -1."""
    depth = 0
    for j in range(i, len(tok)):
        if tok[j] == "{":
            depth += 1
        elif tok[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return -1


def root_vars_named(tok: str) -> set:
    """Root KEYS named by an unexpanded environment variable in this token -- under any
    brace operator too (`${X4_GAME:-x}`, FX-G4 / H2)."""
    out = set()
    for m in _VAR_ANY.finditer(tok):
        key = ROOT_VARS.get((m.group(1) or m.group(2)).upper())
        if key:
            out.add(key)
    return out


# ------------------------------------------------------------------ heredocs
#: A heredoc OPENER's delimiter. Three spellings, because the delimiter is a WORD,
#: not an identifier: single-quoted, double-quoted, or bare.
#:
#: MEASURED 2026-09-04: the old pattern was `[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?`,
#: which TRUNCATES the marker at the first character an identifier cannot contain.
#: `<<'E-O-F'` yielded `E`, `<<'EOF.md'` yielded `EOF`, and `<<"my marker"` yielded
#: `my`. The terminator line then never compares equal, so strip_heredocs' skip
#: region runs to END OF INPUT -- and everything after the heredoc reaches NO RULE
#: AT ALL. Not just the path rules: `git add -A` and the profile-name search went
#: blind too. Found by teaching the fuzzer to vary its template PARAMETERS; 996
#: mutants had never emitted a non-identifier marker.
#:
#: Direction is one-way. The old form MISSED markers, so its skip region was too
#: LONG and hid commands; matching them correctly can only reveal more commands to
#: the rules, never fewer.
#: A heredoc delimiter WORD. Bash applies QUOTE REMOVAL to it, so `EOF`,
#: `'EOF'`, `"EOF"`, `\\EOF` and `E'OF'` all terminate at EOF -- the word is
#: assembled from quoted runs, escaped characters and plain characters, and
#: dequoted by `_dequote_marker` below.
#:
#: B1, MEASURED 2026-09-06: the previous form had three fixed spellings and a
#: bare alternative whose class ALLOWED a backslash and a quote, so `<<\\EOF`
#: captured `\\EOF` and `<<E'OF'` captured `E'OF'`. Neither matches the real
#: terminator, `strip_heredocs` compares the body line verbatim, and the skip
#: region therefore ran to END OF INPUT -- deleting every following command
#: from `body` before a single rule could read it. Three HARD BLOCKS included.
_HD = re.compile(r"""<<-?[ \t]*((?:'[^']*'|"[^"]*"|\\.|[^\s;&|<>()`'"])+)""")


def _dequote_marker(word: str) -> str:
    """POSIX quote removal for a heredoc delimiter.

    Only the TEXT matters here. Whether the delimiter was quoted also decides
    if the body is expanded, and we do not care: nothing reads the body.
    """
    out = []
    i = 0
    while i < len(word):
        c = word[i]
        if c == "'":
            j = word.find("'", i + 1)
            if j < 0:
                out.append(word[i + 1:])
                break
            out.append(word[i + 1:j])
            i = j + 1
        elif c == '"':
            j = word.find('"', i + 1)
            if j < 0:
                out.append(word[i + 1:])
                break
            out.append(word[i + 1:j])
            i = j + 1
        elif c == chr(92) and i + 1 < len(word):
            out.append(word[i + 1])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _line_quotes(s: str, q0: str = "") -> tuple:
    """(mask, quote state at the END of `s`) for one LINE that starts in quote state `q0`.
    The mask is True at every index inside (or on) a quote. A word-initial `#` outside quotes
    ends the line's code: a quote in a comment opens nothing (`# it's`). FX-G2 / reviewer E1:
    the heredoc scan carries this state from line to line, so a `<<WORD` on the second line of
    a multi-line quoted string is TEXT, as bash reads it -- not a skip region that never ends."""
    mask = [False] * len(s)
    end = []
    for i, c, inq, _k in _qwalk(s, q0, end):
        if (not inq and c == chr(35) and (i == 0 or s[i - 1] in " " + chr(9))
                and not (i > 0 and s[i - 1] == chr(92))):
            return mask, ""
        mask[i] = inq
    return mask, (end[0] if end else q0)


def _quote_mask(s: str) -> list[bool]:
    """True at every index that sits inside (or is) a quote. Index-aligned with `s`."""
    return _line_quotes(s)[0]


def heredoc_marker(line: str):
    """The terminator opened by this line, or None. See `_heredoc_open`."""
    o = _heredoc_open(line)
    return o[0] if o else None


def heredoc_expands(line: str) -> bool:
    """Does the heredoc this line opens EXPAND its body? Bash: only when no part of the
    delimiter word is quoted or escaped (`<<EOF`, never `<<'EOF'`, `<<"EOF"`, `<<\\EOF`).
    An expanding body RUNS every `$(...)` and backtick in it (see heredoc_substitutions)."""
    o = _heredoc_open(line)
    return bool(o) and o[1]


def _heredoc_open(line: str, q0: str = ""):
    """(terminator, expands) for the heredoc this line opens, or None.

    The `<<` must be OUTSIDE quotes -- otherwise `echo "a <<MARK b"` opens a skip region
    and hides every following command from three refusal rules. But the MARKER ITSELF is
    usually quoted (`<<'PY'` is the commonest form here), so the earlier approach of
    blanking quoted strings before searching blanked the marker name and stopped
    recognising heredocs at all. Test both directions or one of them silently wins.
    """
    o = _heredoc_opens(line, q0)
    return o[0] if o else None


def _heredoc_opens(line: str, q0: str = "") -> list:
    """Every (terminator, expands) this line opens, IN ORDER -- bash reads their bodies one
    after another (`cat <<A; cat <<B`). `q0` is the quote state the line starts in (see
    _heredoc_walk): a `<<` inside a string that began on an earlier line is text (E1)."""
    found = []
    mask = _line_quotes(line, q0)[0]
    line = _blank_arith(line, mask)
    stop = _comment_start(line, mask)
    for i in range(min(len(line) - 1, stop)):
        if line[i] == chr(60) and line[i + 1] == chr(60) and not mask[i] and not mask[i + 1]:
            # A HERE-STRING is not a heredoc. `cat <<< word` feeds one word on stdin and
            # opens no skip region -- but the scan reaches the SECOND `<` of `<<<`, sees
            # `<< word`, and reports a marker. Everything after that line then became
            # "heredoc body" and was invisible to every rule.
            if i > 0 and line[i - 1] == chr(60):
                continue
            if i + 2 < len(line) and line[i + 2] == chr(60):
                continue
            m = _HD.match(line, i)
            if m:
                # ONE group now, dequoted: bash removes quoting from the
                # delimiter, so `<<\\EOF`, `<<E'OF'` and `<<'EOF'` all
                # terminate at the same word.
                word = m.group(1)
                found.append((_dequote_marker(word),
                              not any(ch in word for ch in ("'", '"', chr(92)))))
    return found


def _comment_start(line: str, mask: list) -> int:
    """Index of the first UNQUOTED word-initial `#`, or len(line).

    A `#` mid-word is not a comment (`http://x#frag`, `a#b`), and a quoted one is data.
    """
    for i, c in enumerate(line):
        if c == chr(35) and not mask[i] and (i == 0 or line[i - 1] in " 	"):
            return i
    return len(line)


_SUBSCRIPT_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\[")


def _match_close(line: str, mask: list, j: int, op: str, cl: str) -> int:
    """Index of the unquoted `cl` closing the `op` at `j` (depth-counted), or len(line)-1
    when the line ends first -- bash continues the region onto the next line."""
    depth = 0
    while j < len(line):
        if not mask[j]:
            if line[j] == op:
                depth += 1
            elif line[j] == cl:
                depth -= 1
                if depth == 0:
                    return j
        j += 1
    return len(line) - 1


def _blank_arith(line: str, mask: list) -> str:
    """Blank the regions where `<<` is NOT a heredoc opener: `$(( ))`, the arithmetic
    COMMAND `(( ))` (incl. `for ((;;))` / `if ((..))`), `$[ ]`, `${ }` and an assignment
    subscript `a[ ]=`. FX-G4 / reviewer H5, MEASURED in bash: `((x=1<<EOF))`,
    `for ((i=0;i<1<<EOF;i++))`, `echo $[1<<EOF]`, `x=${y//<<EOF/z}`, `a[1<<EOF]=1` and
    `echo ${#a[1<<EOF]}` all RUN the next line, while this scan opened a heredoc there and
    hid every later line -- `rm -rf <game>` on line 2 was allowed past the hard block. Only
    `$((` was blanked before. `echo x[1<<EOF]` and `declare a[1<<EOF]=3` DO open a heredoc
    in bash; the second is blanked anyway (no trailing-`]=` test can tell it from an
    assignment), which errs toward showing the rules MORE lines, never fewer."""
    if not any(s in line for s in ("((", "$[", "${", "[")):
        return line
    out = list(line)
    i = 0
    n = len(line)
    while i < n - 1:
        if mask[i]:
            i += 1
            continue
        c, d = line[i], line[i + 1]
        j = None
        if c == "$" and d == "(" and i + 2 < n and line[i + 2] == "(":
            j = _match_close(line, mask, i + 1, "(", ")")
        elif c == "$" and d == "[":
            j = _match_close(line, mask, i + 1, "[", "]")
        elif c == "$" and d == "{":
            j = _match_close(line, mask, i + 1, "{", "}")
        elif c == "(" and d == "(" and (i == 0 or line[i - 1] in " " + chr(9) + ";&|({!"):
            j = _match_close(line, mask, i, "(", ")")
        elif (i == 0 or not (line[i - 1].isalnum() or line[i - 1] in "_$")):
            m = _SUBSCRIPT_ASSIGN.match(line, i)
            if m:
                k = _match_close(line, mask, m.end() - 1, "[", "]")
                if line[k + 1:k + 2] == "=" or line[k + 1:k + 3] == "+=":
                    i, j = m.end() - 1, k
        if j is not None:
            for k in range(i, min(j + 1, n)):
                out[k] = " "
            i = j + 1
            continue
        i += 1
    return "".join(out)


def _ps_reads_stdin(sg: str) -> bool:
    """A PowerShell host whose program comes from stdin (`-Command -`, `-File -`, or no
    program argument at all)."""
    ws = words(sg)
    k = _verb_at(sg)
    if k is None or _verb_name(ws[k]) not in _PS_EXES:      # P1: the verb, not a PS-named word
        k = next((i for i, t in enumerate(ws) if _verb_name(t) in _PS_EXES), None)
    return k is not None and _ps_host_payload(_drop_redirects(ws[k:]))[0] in ("stdin", "none")


def ps_heredoc_bodies(cmd: str) -> list[str]:
    """Heredoc bodies fed to a PowerShell host that reads its program from stdin
    (finding 2). PowerShell text: the caller TRANSLATES them, never hands them to the Bash
    rules as they stand."""
    return heredoc_bodies(cmd, sink=lambda sg: _verb_name(verb(_unwrap(sg))) in _PS_EXES
                          and _ps_reads_stdin(_unwrap(sg)))


def _is_heredoc_end(line: str, term: str) -> bool:
    """Does this body line END the heredoc? Its stripped text is the delimiter (or the
    delimiter and `;`, a deliberate over-reading kept from before) -- or the delimiter followed
    by `)`, which closes the `$(` the heredoc was opened in. FX-G2 / reviewer E2, MEASURED in
    bash: `x=$(cat <<EOF` / `hi` / `EOF)` ENDS the body (with a warning) and the next line
    RUNS, while this scan kept reading body to the end of the input -- so `rm -rf <game>` on
    the next line reached no rule. Ending a body too EARLY shows the rules more text; too
    late hides it, so every doubt resolves toward ending. FX-G4 / reviewer H5, MEASURED: the
    backtick form ends the same way -- `` x=`cat <<EOF `` / `hi` / `` EOF` `` runs the next
    line -- so a delimiter followed by a backtick ends the body too."""
    s = line.strip()
    if s == term or s == term + ";":
        return True
    return s.startswith(term) and s[len(term):].lstrip()[:1] in (")", chr(96))


def _heredoc_walk(cmd: str) -> tuple:
    """(docs, kept): every heredoc in `cmd` as (opening line, terminator, expands, body lines),
    and the lines that are NOT body -- the command with its bodies removed.

    ONE walk for strip_heredocs, heredoc_bodies and heredoc_substitutions, carrying the QUOTE
    STATE from line to line. FX-G2 / reviewer E1, MEASURED: each line was scanned as if it
    began unquoted, so `git commit -m "notes` / `use cat <<EOF here"` opened a heredoc on the
    SECOND line of a quoted message -- a skip region that never closed -- and `rm -rf <game>`
    on the next line was ALLOWED past the hard block (single quotes too, and a > write into
    reference/). A line that opens several heredocs has their bodies read in order, as bash
    does; a terminator line's text after the delimiter (`EOF)` -> `)`) is kept as code."""
    lines = cmd.split(chr(10))
    docs, kept = [], []
    q = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        opens = _heredoc_opens(line, q)
        q = _line_quotes(line, q)[1]
        kept.append(line)
        i += 1
        for term, expands in opens:
            body = []
            while i < len(lines):
                ln = lines[i]
                i += 1
                if _is_heredoc_end(ln, term):
                    rest = ln.strip()[len(term):].strip()
                    if rest and rest != ";":
                        kept.append(rest)
                    break
                body.append(ln)
            docs.append((line, term, expands, body))
    return docs, kept


_CMD_RUN_SWITCH = re.compile(r"/{1,2}[cCrR]")
_CMD_KEEP_SWITCH = re.compile(r"/{1,2}[kK]")


def _cmd_toks_read_stdin(toks: list) -> bool:
    """Does the cmd.exe at toks[0] read a PROGRAM from stdin? FX-G4 / reviewer H1, MEASURED
    in Git Bash: no switch -> yes (E4); `/k` -> yes, it runs its inline command and THEN
    reads stdin (`cmd //k <<EOF` / `echo hi> m.txt` / `exit` created the file); `/c` and
    `/r` -> no, the child gets stdin -- unless that child is ITSELF a cmd that reads it
    (`echo ... | cmd //c cmd` ran the piped line)."""
    for i, t in enumerate(toks[1:], 1):
        if _CMD_KEEP_SWITCH.fullmatch(t):
            return True
        if _CMD_RUN_SWITCH.fullmatch(t):
            rest = toks[i + 1:]
            return bool(rest) and _verb_name(rest[0]) == "cmd" and _cmd_toks_read_stdin(rest)
    return True


def _cmd_reads_stdin(sg: str) -> bool:
    """A cmd.exe whose program is (partly) its stdin: see _cmd_toks_read_stdin."""
    toks = tokens_of(sg)
    k = _verb_at(sg)
    if k is None or _verb_name(toks[k]) != "cmd":            # P1: see _verb_at
        k = next((i for i, t in enumerate(toks) if _verb_name(t) == "cmd"), None)
    return k is not None and _cmd_toks_read_stdin(toks[k:])


def cmd_heredoc_bodies(cmd: str) -> list[str]:
    """Heredoc bodies fed to a cmd.exe reading its program from stdin, TRANSLATED line by
    line (cmd_to_sh) -- `cmd <<EOF` / `rd /s /q <ref>` / `EOF` was ALLOWED (reviewer E4)."""
    out = []
    for h in heredoc_bodies(cmd, sink=lambda sg: _verb_name(verb(_unwrap(sg))) == "cmd"
                            and _cmd_reads_stdin(_unwrap(sg))):
        sh = chr(10).join(cmd_to_sh([ln]) for ln in h.split(chr(10)) if ln.strip())
        if sh.strip():
            _WIN_DIALECT.add(sh)
            out.append(sh)
    return out


def _flows_to(cmd_without_bodies: str, sink) -> bool:
    """Is any segment that READS A PIPE a `sink`? FX-G2 / reviewer E3, MEASURED: `cat <<EOF |`
    / body / `EOF` / `bash` and `{ cat <<EOF` / body / `EOF` / `} | bash` ran the body as
    commands while the opener line named no shell, so it was stripped as data. Where a
    heredoc's output goes cannot be proven from its opener alone, so when ANY piped-into
    segment of the command is a sink, every body in it is treated as possibly executed."""
    segs = segments(cmd_without_bodies)
    pins = piped_in(cmd_without_bodies)
    if len(pins) != len(segs):
        pins = [True] * len(segs)
    return any(p and sink(sg) for sg, p in zip(segs, pins))


def heredoc_bodies(cmd: str, sink=None) -> list[str]:
    """Bodies whose OPENING LINE runs a SHELL, because a shell executes its heredoc.

    `bash <<SH ... SH` is a command carrier exactly like `bash -c`, and stripping the
    body -- which is correct for the file-payload case -- made it invisible to every
    rule in this file.

    SHELL OPENERS ONLY, and that restriction is load-bearing in BOTH directions:
      * `cat > notes.md <<X ... X` is a file being WRITTEN. Its body is data, it is
        pinned as not-a-delete by test_hook_facts, and routing it would hard-deny
        writing documentation that happens to quote a dangerous command.
      * `python - <<PY ... PY` is PYTHON. A line such as an assignment of a path to a
        name would parse as a delete verb under a shell tokeniser, so routing it would
        invent deletes out of assignments. Python writes have their own rule already.
    ...plus every body of a command in which a sink READS A PIPE (E3, see _flows_to).
    """
    if sink is None:
        def sink(sg):
            return verb(_unwrap(sg)) in _SHELL_SINKS
    docs, kept = _heredoc_walk(cmd)
    if not docs:
        return []
    piped = _flows_to(chr(10).join(kept), sink)
    return [chr(10).join(body) for opener, _t, _e, body in docs
            if piped or _opener_feeds(opener, sink)]


def _opener_feeds(opener: str, sink) -> bool:
    """Does any command on a heredoc's opening line take the body? The line's own segments,
    AND the text after each `$(` / `<(` / `>(` / backtick it opens (FX-P2): in
    `x=$(bash <<'EOF'` the command that owns the heredoc is `bash`, but the line's only
    segment is `x=$(bash <<'EOF'`, whose verb is not a shell -- so a heredoc that really
    feeds a shell reached no rule. Only ever ADDS candidates."""
    cands = [opener]
    for i, c in enumerate(opener):
        if c in "$<>" and opener[i + 1:i + 2] == "(" and opener[i + 2:i + 3] != "(":
            cands.append(opener[i + 2:])
        elif c == chr(96):
            cands.append(opener[i + 1:])
    return any(sink(sg) for c in cands for sg in segments(c))


def heredoc_substitutions(cmd: str) -> list[str]:
    """The commands an EXPANDING heredoc body runs: every `$(...)` and backtick in it.

    FX-P2, MEASURED: `cat > notes.md <<EOF` / `$(<delete the game>)` / `EOF` was a silent
    ALLOW against a hard block -- the body was stripped as data whole, and with an
    unquoted delimiter bash expands it. Only the substitutions run; the rest stays data.
    Quotes are NOT quoting inside a heredoc body (an apostrophe in `it's` is a letter), so
    this scan ignores them; a backslash escapes `$`, backtick and itself; `$((` is
    arithmetic. A quoted delimiter expands nothing and contributes nothing here."""
    out = []
    for _opener, _term, expands, body in _heredoc_walk(cmd)[0]:
        if expands:
            out += _body_substitutions(chr(10).join(body))
    return out


def _body_substitutions(body: str) -> list[str]:
    """`$(...)` and backtick texts in an expanding heredoc body (see heredoc_substitutions)."""
    out = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == chr(92):
            i += 2                              # an escaped character is text
            continue
        if c == "$" and body[i + 1:i + 2] == "(":
            end = _match_paren(body, i + 1)
            if body[i + 2:i + 3] == "(":        # arithmetic -- whose substitutions RUN (item 4)
                kind, text, end = _dollar_double_paren(body, i)
                if kind == "arith":
                    out.extend(_body_substitutions(text))
                elif kind == "subst":
                    out.append(text)
                i = (end + 1) if end != -1 else i + 3
                continue
            if end != -1:
                out.append(body[i + 2:end])
                i = end + 1
                continue
        elif c == chr(96):
            j = i + 1
            while j < len(body) and body[j] != chr(96):
                j += 2 if body[j] == chr(92) else 1
            if j < len(body):
                out.append(body[i + 1:j])
                i = j + 1
                continue
        i += 1
    return [t for t in out if t.strip()]  # (distinct line: a mutant anchors the other)


def _carrier_parts(text: str) -> list[str]:
    """A carried command as the rules must see it (FX-P2): its heredoc bodies STRIPPED, as
    the top level's are, plus the bodies that really run -- fed to a shell, or the
    substitutions of an expanding body. The top level could not do this for it: a `<<`
    inside a double-quoted `"$(cat <<'EOF' ...)"` is correctly not a heredoc THERE, so the
    body reached the carrier walk as text and every body line became a command --
    `git commit -m "$(cat <<'EOF' ... > KNOWLEDGEBASE.md ...)"` was a DENY.
    A body fed to a PowerShell host is kept as raw text, exactly as before this change,
    so nothing that was analysed stops being analysed."""
    if "<<" not in text or not _heredoc_walk(text)[0]:
        return [text]
    return ([strip_heredocs(text)] + heredoc_bodies(text) + ps_heredoc_bodies(text)
            + cmd_heredoc_bodies(text) + heredoc_substitutions(text))


def strip_heredocs(cmd: str) -> str:
    """Remove heredoc BODIES: they are the payload of a file being written, not
    commands being run."""
    # ONE walk, quote state carried across lines (E1) -- see _heredoc_walk.
    return chr(10).join(_heredoc_walk(cmd)[1])


_REDIR = re.compile(r"(\d?)>(\|?)(>?)")


def redirects(seg: str) -> list[tuple[str, str]]:
    """[(mode, target)] for every redirect that WRITES a file. `>|` overrides
    noclobber and still truncates; `2>&1` duplicates an fd and writes nothing; the
    null device is not a file anyone cares about."""
    out = []
    b = blank_quoted(seg)
    flat = seg
    i = 0
    while i < len(b):
        # `->` IS NOT A REDIRECT. Two characters, and no shell grammar produces them:
        # `-` is not an fd and not an operator prefix. It is, however, an arrow, and
        # printing one is routine -- `print(f'{name}: {n} values ->', vals)` inside a
        # `python -c` payload was read as a truncating redirect whose target was the
        # rest of the python source. Combined with a `cd` into the read-only reference
        # tree, that became a WRITE to it.
        #
        # MEASURED 2026-09-03: 1 row in 13,503 historical commands -- the last
        # false positive standing after the grep-`-o` and sed-script fixes, and the
        # third latent defect in this family that only became visible once a HARD
        # BLOCK consumed the result.
        #
        # THE DEEPER CAUSE IS NOT FIXED HERE, and saying so is the point: that `->`
        # should have been blanked as quoted text, and was not, because the shell-level
        # quote scanner loses track inside a `python -c` payload that nests single
        # quotes and backslash-escaped double quotes. A minimised repro did NOT
        # reproduce it, so the exact mis-tracking point is UNISOLATED. Rewriting the
        # scanner is a change under every rule in this file; excluding two characters
        # that cannot be a redirect is not. Registered as a blind spot rather than
        # silently absorbed.
        #
        # The trade is stated rather than assumed: this gives up `cat x->y`, which
        # bash really does parse as `cat x-` redirecting into `y`. Nobody writes that;
        # the arrow is written constantly.
        m = _REDIR.match(b, i)
        if not m or (i > 0 and b[i - 1] in "<-"):
            i += 1
            continue
        mode = "append" if m.group(3) else "truncate"
        j = m.end()
        while j < len(flat) and flat[j] in " \t":
            j += 1
        if j < len(flat) and flat[j] == "&":
            i = m.end()
            continue                                    # fd duplication
        rest = flat[j:]
        tgt = words(rest)[0] if words(rest) else ""
        if tgt and norm(tgt) != "/dev/null":
            out.append((mode, tgt))
        i = m.end()
    return out


# --------------------------------------------------------------------- verbs
#: Programs that RUN ANOTHER PROGRAM named in their arguments. Every one of these in
#: front of a command made verb() return the wrapper, so no verb-keyed rule fired.
#: MEASURED 2026-09-02, E2E, each against a game-root delete the guard catches bare:
#:      timeout 5 rm -rf <game>  -> ALLOW      exec rm -rf <game>    -> ALLOW
#:      setsid rm -rf <game>     -> ALLOW
#: `timeout` is doubly important: CLAUDE.md #25 recommends it, so it is a prefix this
#: workspace types on purpose.
WRAPPERS = {"time", "nice", "env", "sudo", "xargs", "command", "nohup", "stdbuf",
            "exec", "timeout", "setsid", "ionice", "builtin", "doas", "chronic",
            "unbuffer", "ltrace", "strace", "proxychains", "catchsegv"}

#: A token that cannot be a command NAME. Only consulted AFTER a wrapper has been
#: seen, where a bare value is an argument to that wrapper rather than the command:
#: `timeout 5 rm ...` gave verb `5`, and `xargs -I {} rm ...` gave verb `{}`.
_WRAPPER_ARG = re.compile(r"^(\d+(\.\d+)?[smhd]?|\{\}|\+)$")


def _verb_name(t: str) -> str:
    """The command NAME carried by a verb token: basename, minus a `.exe` suffix.

    `/bin/rm`, `C:/Windows/System32/cmd.exe` and `rm.exe` are the same commands as
    `rm` and `cmd`, and every verb-keyed rule compared the token VERBATIM -- so any
    absolute or suffixed spelling walked past all of them at once.

    norm() runs FIRST and is not optional: it folds backslashes to `/` and lowercases,
    and posixpath.basename does NOT split on a backslash, so without it a Windows-style
    path yields the whole string back as one "name".

    Only `.exe` is stripped, and only as a whole suffix. A general extension strip is
    UNSAFE -- test_hook_facts.py pins `done_marker.sh`, `function_helper.py` and
    `casefold.py` by exact equality, because those ARE the command names.
    """
    if not t:
        return t
    n = posixpath.basename(norm(t))
    if n.endswith(".exe"):
        n = n[:-4]
    return n or t


#: A leading `VAR=value` assignment. POSIX allows any number of them before the
#: command name, so the VERB is not always token 0 -- and any scan that assumes it
#: is goes quiet on `FOO=bar git clean -fdx` (B3). Shared so the two readers of
#: this shape cannot drift apart, which is exactly how that bypass existed:
#: `_verb_token` skipped assignments and `_git_destructive` did not.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?[+]?=")    # P1: `+=`, `A[i]=`


#: Wrapper flags that consume the NEXT token as their VALUE, per wrapper.
#:
#: MEASURED 2026-09-06: without this, `env -u X4_GAME rm -rf <game>` resolved its verb
#: to `x4_game` and walked past ALL THREE HARD BLOCKS. So did `sudo -u root rm`,
#: `env -C /tmp rm` and `timeout -s KILL 5 rm`. The cause is narrow: _verb_token skips
#: any `-flag`, and after a wrapper it also skips a _WRAPPER_ARG -- but that pattern
#: matches only a number, `{}` or `+`, so a flag whose value is a WORD left the word
#: standing as the command name. `nice -n 5` and `xargs -I{}` worked only because
#: their values happen to be numeric or braced.
#:
#: `env -u VAR cmd` is not exotic; it is what this workspace types to clear a root.
#:
#: PER WRAPPER, and deliberately not a union set. Skipping a value that a flag does
#: NOT take would consume the REAL verb and return its first argument instead -- a
#: miss, i.e. strictly less safe than the bug. Only the attached forms (`-oL`,
#: `--flag=value`) need no skip, and they do not match these exact tokens.
_WRAPPER_VALUE_OPTS = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "sudo": {"-u", "--user", "-g", "--group", "-p", "--prompt", "-h", "--host",
             "-C", "--close-from", "-r", "--role", "-t", "--type", "-U", "--other-user"},
    "doas": {"-u", "-C"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "xargs": {"-I", "-i", "--replace", "-n", "--max-args", "-P", "--max-procs",
              "-d", "--delimiter", "-E", "-L", "--max-lines", "-s", "--max-chars",
              "-a", "--arg-file"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "--class", "-n", "--classdata", "-p", "--pid"},
    "stdbuf": {"-i", "--input", "-o", "--output", "-e", "--error"},
    "strace": {"-o", "-e", "-p", "-s"},
    "ltrace": {"-o", "-e", "-p", "-s"},
    "proxychains": {"-f"},
    "chronic": set(),
    "setsid": set(),
    "nohup": set(),
    "time": set(),
    "command": set(),
    "builtin": set(),
    "unbuffer": set(),
    "catchsegv": set(),
}


def _raw_words(seg: str, spans: bool = True) -> list:
    """The WORDS of `seg` as written (quotes kept), split only at an unquoted, unescaped
    blank that sits OUTSIDE every `$(...)`, `$((...))`, backtick and `${...}` span -- the
    word boundaries bash itself uses (FX-G6 / reviewer K I4). tokens() splits inside such a
    span, so `A=${X:-echo a}` and `env -C $(echo /tmp )` offered a piece of the span as the
    next word, and that piece became the command name: `A=${X:-echo a} rm -rf <game>` and
    `env -C $(echo /tmp ) rm -rf <game>` were ALLOWED (MEASURED E2E).

    A span still OPEN at the end of `seg` (a segment cut inside a substitution) is not
    guessed at: from the word that opened it, the rest splits at blanks exactly as tokens()
    splits it (`spans=False`)."""
    out, opened = _scan_words(seg, spans)
    if opened is not None:
        k, start = opened
        return out[:k] + _raw_words(seg[start:], spans=False)
    return out


#: P1: the head of a word that opens an ARRAY value (`A=(` / `A+=(`) or a SUBSCRIPT (`A[`).
_ARRAY_HEAD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*[+]?=$")
_SUBSCRIPT_HEAD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _subscript_assigns(seg: str, i: int) -> bool:
    """Is the `[` at seg[i] a subscript ASSIGNMENT (`A[i + 1]=v`, `A[x]+=v`)? Only then does bash
    keep its blanks inside the word; a glob `a[b c]` is split like any other word."""
    depth = 0
    for j in range(i, len(seg)):
        if seg[j] == "[":
            depth += 1
        elif seg[j] == "]":
            depth -= 1
            if depth == 0:
                return seg.startswith("=", j + 1) or seg.startswith("+=", j + 1)
    return False


def _span_open(word: str) -> bool:
    """Does `word` hold a substitution / `${` span it never closes (a segment cut inside
    one)? Quote-aware, unlike counting parens: `A=$(echo "(")` is closed."""
    return _scan_words(word, True)[1] is not None


def _scan_words(seg: str, spans: bool) -> tuple:
    """_raw_words' walk: (words, None) -- or, when a span is still open at the end,
    (words so far, (index of the word that opened it, its start offset in `seg`))."""
    out, buf, stack = [], [], []
    esc, last, opened = False, "", None      # opened: (len(out), buf-start) of the outer span
    inner = []                               # the word being read INSIDE a span (case/esac)
    for i, c, inq, _k in _qwalk(seg):
        if esc:
            esc, last = False, ""
            buf.append(c)
            continue
        if not inq:
            if c == chr(92):
                esc = True
                buf.append(c)
                continue
            if c.isspace() and not stack:
                if buf:
                    out.append("".join(buf))
                buf, last = [], ""
                continue
            if spans:
                top = stack[-1] if stack else ""
                if stack and (c.isspace() or c in ";|&()" + chr(96)):
                    # P1: a `case` inside a substitution: its pattern `a)` closes nothing.
                    w = "".join(inner)
                    if w == "case" and top != chr(96):
                        stack.append("esac")
                    elif w == "esac" and top == "esac":
                        stack.pop()
                    top = stack[-1] if stack else ""
                    inner = []
                elif stack:
                    inner.append(c)
                # The word so far, joined only for `(` / `[` at the outer level (O(n) per word:
                # joining on every character made a 100 KB nested command quadratic).
                head = "".join(buf) if c in "([" and not stack and len(buf) < 256 else ""
                if c == chr(96) and top == chr(96):
                    stack.pop()
                elif top == "esac" and c == ")":
                    pass
                elif (c == "{" and last == "$" or c == chr(96) or c == "(" and (
                        last in ("$", "<", ">") or top in (")", "esac") or (not stack and _ARRAY_HEAD.match(head)))
                      or c == "[" and (last == "$" or not stack and _SUBSCRIPT_HEAD.match(head)
                                       and _subscript_assigns(seg, i))):
                    # P1 (v4.0 release review): every span bash keeps inside ONE word -- `$(`,
                    # `${`, backticks, and also process substitution `<(` / `>(`, legacy
                    # arithmetic `$[`, an array value `A=(`, a subscript `A[i + 1]=`. MEASURED:
                    # `X=<(echo a) rm -rf <game>` split at the blank and `a)` became the verb.
                    if not stack:
                        opened = (len(out), i - len(buf))
                    stack.append({"(": ")", "{": "}", "[": "]"}.get(c, chr(96)))
                    inner = []
                elif stack and c == top:
                    stack.pop()
        elif stack and spans:
            inner.append(c)
        last = c if not inq else ""
        buf.append(c)
    if stack and opened is not None:
        return out, opened
    if buf:
        out.append("".join(buf))
    return out, None


def _spanning_view(cmd: str) -> str:
    """`cmd` with every unquoted, CLOSED `$(...)` / backtick span that holds a command
    SEPARATOR (`;`, `&`, `|`, newline) collapsed to `$(_)` / `` `_` `` -- or `cmd` itself
    when there is none (FX-G6 / reviewer K I4).

    segments() splits at every unquoted separator, inside a substitution too, so `A=$(echo a;
    echo b) rm -rf <game>` became `A=$(echo a` and `echo b) rm -rf <game>` -- whose verb is
    `echo`: ALLOWED (MEASURED E2E). facts() walks this view IN ADDITION to the command as
    written (the substitution's own commands are carried separately, as before), so it can
    only add what the cut hid: never a fact lost. A span with no separator is left alone,
    which keeps the view identical to `cmd` -- and so absent -- for nearly every command."""
    if "$(" not in cmd and chr(96) not in cmd:
        return cmd
    mask = _quote_mask(cmd)
    n, out, i, j = len(cmd), [], 0, 0
    while j < n:
        if mask[j] or not (cmd.startswith("$(", j) or cmd[j] == chr(96)):
            j += 1
            continue
        end = -1
        if cmd[j] == chr(96):
            end = next((e for e in range(j + 1, n) if cmd[e] == chr(96) and not mask[e]), -1)
            rep = chr(96) + "_" + chr(96)
        else:
            depth = 0
            for e in range(j + 1, n):
                if mask[e] or (e > 0 and cmd[e - 1] == chr(92)):
                    continue
                depth += {"(": 1, ")": -1}.get(cmd[e], 0)
                if depth == 0:
                    end = e
                    break
            rep = "$(_)"
        if end < 0:
            break                           # unclosed: the rest stays as written
        if any(not mask[e] and cmd[e] in ";&|" + chr(10) for e in range(j + 1, end)):
            out.append(cmd[i:j] + rep)
            i = end + 1
        j = end + 1
    out.append(cmd[i:])
    return "".join(out)


def _verb_word(seg: str) -> tuple[str, int | None]:
    """The RAW token carrying the command name, exactly as written.

    Split out of verb() for resolve_verb(): splicing a variable's value back into a
    segment needs the token AS WRITTEN (`$RM`), while every rule wants the normalised
    NAME. verb() below is unchanged in behaviour -- it is now literally
    _verb_name(_verb_token(seg)) -- so all 22 call sites are untouched.
    """
    seen_wrapper = False
    wrapper = ""
    want_value = False
    # ⚠ THE `quoted` SHORT-CIRCUIT USED TO BE THE FIRST BRANCH HERE, and it was a
    # TOTAL BYPASS of every verb-keyed rule -- the seventh parser defect in this file
    # and the one with the widest blast radius, because a wrong verb takes all three
    # hard blocks with it at once.
    #
    # MEASURED by the v3.1.0 release reviewer, per item over 9 seeds: quoting ONE token
    # in the prefix flipped 7 of 9 rules from fire to allow -- rm_hits_game,
    # rm_targets_reference, rm_saves, git_add_all, git_wipes_x4_dir, writes_reference,
    # sed_i_in_game_or_profile. The two immune rules are dollarq_after_pipe and
    # longjob_foreground, which are the only two that are NOT verb-keyed; that is what
    # pins the cause to verb resolution rather than to any predicate.
    #
    #     FOO="bar" rm -rf <game>          -> verb `foo=bar`   -> ALLOW
    #     env -u "X4_GAME" rm -rf <game>   -> verb `x4_game`   -> ALLOW
    #     sudo -u "root"   rm -rf <game>   -> verb `root`      -> ALLOW
    #
    # PRE-ARC -- the same ordering is in v3.0.0 -- and it also defeated this arc's own
    # _WRAPPER_VALUE_OPTS fix, because one quote is enough to skip past it.
    #
    # There is no branch now: a quoted token that survives the skips is returned by the
    # ordinary `return t` below, so a genuinely quoted COMMAND NAME still resolves. What
    # changed is that a quoted ASSIGNMENT, WRAPPER or FLAG VALUE no longer short-circuits
    # ahead of the skip that exists to step over it. Realized incidence in 33,837 real
    # commands: 568 parse errors, 0 carrying a dangerous verb -- ranked on failure mode,
    # not on frequency.
    # FX-G5 / reviewer J2 item 3, MEASURED E2E (deny -> ALLOW): tokens() splits an UNQUOTED
    # `A=$(echo a b)` at its spaces, and the word `a` became the verb, so `A=$(echo a b) rm
    # -rf <game>` passed every hard block. An assignment's substitutions are collapsed first.
    # FX-G6 / reviewer K I4 (MEASURED E2E: allowed): the same held for a `${...}` with a blank
    # (`A=${X:-echo a} rm`), a substitution the segmenter cut (`A=$(echo a; echo b) rm`, see
    # _spanning_view) and a wrapper option's VALUE (`env -C $(echo /tmp ) rm`). One word is
    # now one word, whatever spans it holds: _raw_words, and its first token for the tests.
    token_index = 0
    for w in _raw_words(seg):
        tk = tokens(w)
        at = token_index
        token_index += len(tk)
        if not tk:
            continue
        t = tk[0][0]
        if want_value:
            # The previous token was a wrapper flag that takes a separate value, so
            # THIS token is that value and never the command.
            want_value = False
            continue
        if _ASSIGNMENT.match(t) or _ASSIGNMENT.match(w):     # P1: `A[i + 1]=v` is one word
            continue
        name = _verb_name(t)
        if name in WRAPPERS:
            seen_wrapper = True
            wrapper = name
            continue
        if t.startswith("-"):
            if t in _WRAPPER_VALUE_OPTS.get(wrapper, ()):
                want_value = True
            continue
        if seen_wrapper and _WRAPPER_ARG.match(t):
            continue                    # a duration or a placeholder, not a command
        return t, at
    return "", None


_SPLIT_EXPANSION = re.compile(r"[$](?=[A-Za-z_{(\[0-9@*#?!-])|[<>][(]|" + chr(96))


def _env_split_index(seg: str):
    """P1 (v4.0 release review): after a WRAPPER (`env`), `NAME=value` is an ordinary argument,
    so an UNQUOTED expansion in it is WORD-SPLIT and a later field becomes the command bash
    runs. MEASURED in Git Bash: `env X=$(echo a rm) -rf <dir>` runs `rm -rf <dir>`, and the guard
    allowed it with `<game>` (verb() read `<game>`). Returns the tokens() index just past such a
    word -- everything from there may be the command and its arguments -- or None."""
    if "=" not in seg or not any(w in seg for w in WRAPPERS):
        return None                     # the common case pays two substring scans
    seen_wrapper = False
    index = 0
    for w in _raw_words(seg):
        tk = tokens(w)
        index += len(tk)
        if not tk:
            continue
        t = tk[0][0]
        if _verb_name(t) in WRAPPERS:
            seen_wrapper = True
            continue
        if seen_wrapper and _ASSIGNMENT.match(t):
            if any(not inq and _SPLIT_EXPANSION.match(w, i) for i, _c, inq, _k in _qwalk(w)):
                return index
            continue
        if seen_wrapper and t.startswith("-"):
            continue
        if not seen_wrapper and _ASSIGNMENT.match(t):
            continue
        return None
    return None


def _verb_at(seg: str, default=None):
    """The tokens(seg) INDEX of the command name (_verb_word's), or `default`. P1 (v4.0
    release review): `next(i for i, t in enumerate(toks) if t == vt)` took the FIRST token
    spelled like the verb -- inside an assignment's `<( echo a )` or `$(echo a b)` that is the
    wrong one, and every helper reading "the words after the verb" read the prefix instead."""
    at = _verb_word(seg)[1]
    return default if at is None else at


def _verb_offset(seg: str) -> int:
    """The CHARACTER offset in `seg` of the word holding the command name, or -1."""
    at = _verb_word(seg)[1]
    if at is None:
        return -1
    pos, index = 0, 0
    for w in _raw_words(seg):
        start = seg.find(w, pos)
        if start < 0:
            return -1
        if index == at:
            return start
        index += len(tokens(w))
        pos = start + len(w)
    return -1


def _verb_token(seg: str) -> str:
    """The command token; position comes from the same wrapper/assignment walk."""
    return _verb_word(seg)[0]


def verb(seg: str) -> str:
    """The command actually being run, seeing through env-assignment prefixes and
    wrappers. `write_targets` required the verb to be the segment's FIRST word, so
    `echo x | sudo tee <docs>/n.txt` and `time cp ...` lost their destination.

    The name is NORMALISED (see _verb_name): every caller compares it against a bare
    command name, and none of the twelve echoes it back to the user, so normalising
    here fixes all of them at once rather than at each comparison site.
    """
    return _verb_name(_verb_token(seg))


#: A resolved verb must look like a command NAME. Anything with a space, a quote, a
#: redirect or a separator in it is a value that happens to be assigned, not a command,
#: and splicing it in would rewrite the segment into something the user never typed.
_BARE_VERB = re.compile(r"^[A-Za-z0-9_.:/" + chr(92) + r"+-]+$")


def resolve_verb(seg: str, assigns: dict[str, str]) -> str:
    """Rewrite a segment whose COMMAND NAME arrives through a variable.

    `RM=rm; $RM -rf "<game>"` gave verb `$rm`, so every verb-keyed rule missed at
    once -- including all three hard blocks. MEASURED 2026-09-03, E2E: both game
    hard blocks returned ALLOW for the variable spelling while the plain spelling
    denied. One indirection past the whole file.

    verb() itself cannot do this: it is reached from 22 call sites and not one of them
    holds the assignment table. Re-deriving the table inside verb() is exactly what
    caused the 11.3x latency regression recorded in this file's header, so the
    substitution happens ONCE, here, where segments are prepared and `assigns` is
    already in hand.

    Conservative by construction -- it only ever rewrites when the value is a bare
    command name, and returns the segment untouched otherwise.
    """
    t = _verb_token(seg)
    if not t or not has_unresolved(t):
        return seg
    r = resolve(t, assigns)
    if r == t or has_unresolved(r) or not _BARE_VERB.match(r):
        return seg
    # P1: the verb's OWN word, not the first place its text occurs (an assignment can hold it).
    o = _verb_offset(seg)
    i = seg.find(t, o) if o >= 0 else seg.find(t)
    if i < 0:                            # tokens() de-quoted it beyond recognition
        return seg
    return seg[:i] + r + seg[i + len(t):]


def _verb_word_text(seg: str) -> tuple:
    """(the raw WORD holding the command name, the text after it), or ("", "")."""
    at = _verb_word(seg)[1]
    if at is None:
        return "", ""
    pos, index = 0, 0
    for w in _raw_words(seg):
        start = seg.find(w, pos)
        if start < 0:
            return "", ""
        if index == at:
            return w, seg[start + len(w):]
        index += len(tokens(w))
        pos = start + len(w)
    return "", ""


def _dynamic_program(seg: str) -> bool:
    """F10 (v4.0): is this segment's COMMAND NAME unidentifiable here -- a variable this
    command does not assign a bare name to (`$R`, `"$R"`, `${A[0]}`), or a substitution?
    Call it on a segment resolve_verb() has already seen. A segment cut inside an open
    substitution is not a command position (the substitution is walked on its own), the
    same exclusion _subst_verb makes."""
    t = _verb_token(seg)
    if not t or not has_unresolved(t):
        return False
    w, _rest = _verb_word_text(seg)
    # SINGLE-quoted text is literal: `'$GAME/x'` names a program called that, it expands
    # nothing (MEASURED in the corpus replay: python source lines carried as commands read
    # `'... $GAME ...'` as an unknown name and asked).
    if w and not has_unresolved(blank_single_quoted(w)):
        return False
    toks = _raw_words(seg)
    k = next((i for i, x in enumerate(toks) if x == w), 0)
    return not any(_span_open(x) for x in toks[:k])


#: git's subcommands that a rule keys on (git_add_all, the wipe/discard rules). A dynamic
#: program word followed by one of them may BE git: `$G add -A`.
_GIT_RULE_SUBCOMMANDS = frozenset({"add", "stage", "clean", "reset", "stash", "checkout", "restore"})

#: A quoted word that eval would re-read as more than one word, or as an operator.
_EVAL_SPLITS = re.compile(r"[\s;&|<>()" + chr(96) + r"]")     # a bare `$X` re-expands only a value

#: How many program words in a row one segment's readings step past (`$A $B $C rm ...`).
_MAX_DYNAMIC_READINGS = 8


def dynamic_program_readings(seg: str) -> list:
    """F10 (v4.0): what a segment whose command name is UNIDENTIFIABLE may run, as extra
    command texts for every rule to judge. MEASURED (Skyrim sibling, Windows): with the
    name held in a variable, each of these really deletes and the guard allowed them all --
    `$R rm -rf X` (R=command/sudo/...), `$R 'rm -rf X'` (R=eval: the quoted word is ONE
    token, so "read the next words as a command" alone misses it), `$G add -A` (G=git).

    The readings are SPECULATIVE and ADDITIVE -- none is assumed to be the one that runs:
      * the words after it, as written (the name is a wrapper: command, sudo, call ...);
      * their VALUES joined by blanks (the name is eval / a shell that runs its text);
      * `git <words>` when the first word is a git subcommand some rule keys on;
    repeated past each further unidentifiable word. A reading that is not a command at all
    (`-c "<python>"` read as shell) simply matches no rule: these texts go through the same
    tolerant segmenter as every carried command and never through `bash -n`, so a reading
    that does not parse cannot fail the parse of the command it came from, and nothing is
    ever marked "fully analysed" because of one. The backstop for a name that may itself be
    the destructive program (`$R -rf <game>`) is facts()' `program_dynamic_protected`."""
    out, cur = [], seg
    for _ in range(_MAX_DYNAMIC_READINGS):
        if not _dynamic_program(cur):
            break
        rest = _verb_word_text(cur)[1].strip()
        if not rest:
            break
        toks = tokens(rest)
        # The eval reading only where re-reading CHANGES the words: a quoted word holding a
        # blank or an operator. Joining a quoted plain path changes nothing an eval would run,
        # but un-quotes it for the text rules -- MEASURED in the corpus replay, a quoted
        # script path then read as a foreground long job (4 historical commands).
        joined = " ".join(t for t, _q in toks) if any(
            q and _EVAL_SPLITS.search(t) for t, q in toks) else rest
        for r in (rest, joined):
            if r and r not in out:
                out.append(r)
        first = toks[0][0].lower() if toks else ""
        if first in _GIT_RULE_SUBCOMMANDS:
            out.append("git " + rest)
        # The name may be a HOST that runs its text: cmd (`$R /c del <x>`) or a PowerShell
        # (`$R -Command 'Remove-Item <x>'`). Only the spellings no other program shares --
        # `-c` is python's and bash's too, and their reading is the joined one above.
        if _CMD_RUN_SWITCH.fullmatch(first) or _CMD_KEEP_SWITCH.fullmatch(first):
            out.append("cmd " + rest)
        elif first in ("-ec", "-enc", "-encodedcommand") or first.startswith("-comm"):
            out.append("powershell " + rest)
        cur = rest
    return out


def tokens_of(seg: str) -> list[str]:
    """Raw token strings of a segment, flags included."""
    return [t for t, _q in tokens(seg)]


#: Verbs that can RUN x4refguard.py: an interpreter or launcher, or the script itself. A
#: verb named by a VARIABLE (`"$X4_PYTHON"`, the toolkit's own spelling) counts too: what
#: it runs is unknowable here, and the docs tell agents to run the script exactly that way.
_REFGUARD_RUNNERS = ("python", "py", "uv", "uvx", "x4refguard", "conda", "mamba", "micromamba",
                     "pipx", "poetry", "pdm", "hatch", "rye", "pixi")


def _lifts_reference_deny(seg: str, assigns: dict, roots: dict, cwd: str = "") -> bool:
    """Does this segment lift the OS deny-delete/write on reference/? Two shapes:
    `x4refguard.py remove` RUN by an interpreter (not merely named, e.g. by echo), whatever
    root is configured -- its --path form reaches an old root; and a raw `icacls` with
    /remove or /reset whose OWN operand is the reference root or inside it, or -- with /T --
    an ancestor that a recursive reset walks into. Applying the deny (/deny) or reading
    the ACL is not a lift.

    An operand is judged WHERE IT RUNS, like every other path rule (R2-F2, v4.0.0 review):
    a root variable is its root (subst_root_var), and a relative operand joins the segment's
    `cwd` -- `icacls reference /reset /T` from the folder above it is a lift."""
    ref = roots.get("reference") or ""
    toks = [t.lower() for t in tokens_of(seg)]
    v = _verb_name(verb(seg)).lower()
    if v.startswith(_REFGUARD_RUNNERS) or has_unresolved(v):
        for i, t in enumerate(toks):
            if t.replace(chr(92), "/").rsplit("/", 1)[-1] in ("x4refguard.py", "x4refguard") \
                    and ("remove" in toks[i + 1:]
                         # FX-G2 / reviewer E7: the action may not be IN the text -- `echo
                         # remove | xargs python x4refguard.py` (xargs supplies it), or a
                         # variable/substitution. An action the guard cannot read is asked.
                         or "xargs" in toks[:i]
                         # FX-G4 / H9, MEASURED in the corpus replay: EVERY argument was
                         # tested, so `x4refguard.py apply --toolkit "$X4_TOOLKIT"` and
                         # `status --toolkit "$W"` ASKED (6 of 6 new hits in history).
                         # Only the ACTION position can name the action.
                         or has_unresolved(_refguard_action(toks[i + 1:]))):
                return True
    if v in ("takeown", "takeown.exe"):
        # FX-G4 / reviewer H-M5, MEASURED: `takeown /f <ref> /r` was allowed. Taking
        # OWNERSHIP lets the new owner rewrite the ACL, deny included -- the same lift as
        # icacls /setowner. `/f <path>` names it; `/r` walks into it from an ancestor.
        rec = any(re.fullmatch(r"/{1,2}r", t) for t in toks)
        raw = tokens_of(seg)
        for i, t in enumerate(toks[:-1]):
            if re.fullmatch(r"/{1,2}f", t):
                for rv in resolve_variants(raw[i + 1], assigns):     # FX-G6 / K C1
                    r = join_cwd(cwd, subst_root_var(rv, roots))
                    if under(r, ref) or (rec and contains_root(r, ref)):
                        return True
        return False
    if "icacls" not in v:                # an unset ref: under()/contains_root() are False for ""
        return False
    # EVERY ACL-modifying switch, not only /remove and /reset (reviewer E7, MEASURED): an
    # explicit /grant overrides the INHERITED deny (x4refguard's own _child_ok says so),
    # /inheritance:r strips it, /setowner hands the object to someone who may rewrite it,
    # /restore and /substitute rewrite ACLs wholesale. /deny (applying) and a read are not lifts.
    paths, switches = _icacls_parts(seg)
    if not any(t in _ICACLS_LIFTS for t in switches):
        return False
    recursive = "/t" in switches
    # /restore applies a saved ACL file to paths UNDER the folder named: an ancestor of
    # reference/ reaches it without /T.
    walks = recursive or "/restore" in switches
    for rv in (rv for o_ in paths for rv in resolve_variants(o_, assigns)):
        r = join_cwd(cwd, subst_root_var(rv, roots))      # every value: FX-G6 / K C1
        if under(r, ref) or (walks and contains_root(r, ref)):
            return True
    return False


def _refguard_action(args: list) -> str:
    """x4refguard.py's ACTION: its first argument that is not an option (the top-level
    parser takes no options of its own). '' when there is none."""
    return next((a for a in args if not a.startswith("-")), "")


#: icacls switches that change an ACL in a way that can lift a deny (E7), and those that take
#: a VALUE -- a trustee:perm spec or a file -- which is not a path the ACL is applied to.
_ICACLS_LIFTS = ("/remove", "/reset", "/grant", "/inheritance", "/restore", "/setowner",
                 "/substitute")
_ICACLS_VALUED = ("/grant", "/deny", "/remove", "/setowner", "/restore", "/save",
                  "/setintegritylevel", "/substitute", "/findsid")
_ICACLS_SWITCHES = frozenset(_ICACLS_LIFTS + _ICACLS_VALUED
                            + ("/t", "/c", "/l", "/q", "/verify", "/?"))


def _icacls_switch(o: str) -> str:
    """The switch icacls itself would see for operand *o*, lower-cased, modifier dropped.

    FX-H2 / reviewer L: Git Bash users double a switch's slash so MSYS does not rewrite it
    as a path, and icacls receives `/reset` for `//reset`. Only a token that is ENTIRELY
    `//<letters>` is one: a UNC `//server/share` has a second slash and stays a path.
    icacls also accepts ANY switch that begins with `/inheritance` (MEASURED 2026-10-10:
    `/inheritancezzz:r` removed every inherited ACE of a scratch file, while `/inheritanc:r`
    and `/inh:r` were invalid parameters), so that whole class is `/inheritance`.
    """
    lo = o.lower().split(":", 1)[0]
    if re.fullmatch(r"//[a-z?]+", lo):
        lo = lo[1:]
    if lo.startswith("/inheritance"):
        lo = "/inheritance"
    return lo


def _icacls_parts(seg: str) -> tuple:
    """Paths and documented switches, excluding switch values from BOTH sets.

    A slash-rooted path is not a switch. Colon modifiers belong to the named
    switch; a slash in a pathname does not. Microsoft icacls syntax is the source
    for the switch names; /inheritance retains the existing supported spelling.
    """
    toks = tokens_of(seg)
    k = next((i for i, t in enumerate(toks) if "icacls" in _verb_name(t).lower()), None)
    out, switches, take = [], [], 0
    for o in ([] if k is None else toks[k + 1:]):
        if take:
            take -= 1
            continue
        lo = _icacls_switch(o)
        # icacls NAME is the first operand. In Git Bash /c is the C drive;
        # a later /C remains the continue-on-error switch. Do not discard NAME.
        if lo in _ICACLS_SWITCHES and out:
            switches.append(lo)
            if lo in _ICACLS_VALUED:
                take = 2 if lo == "/substitute" else 1
            continue
        if not _drop_redirects([o]):
            continue
        out.append(o)
    return out, switches


def _icacls_paths(seg: str) -> list:
    """The PATH operands of an icacls segment: switches and their values excluded."""
    return _icacls_parts(seg)[0]


def _operands(seg: str, *, include_flags: bool = False) -> list[str]:
    """Non-redirect operands after the verb; opaque access also inspects flags.

    Unknown programs can carry paths in --option=PATH or -oPATH. Their option
    grammar is unverified, so the possible-access policy must not drop them.
    """
    out, skip = [], False
    # P1: the verb is where _verb_word finds it (whole words), not the first token that is
    # not an assignment -- a spaced `X=<(echo a)` made `a)` the verb and `rm` an operand.
    vi = _verb_word(seg)[1]
    if vi is None:
        return []
    for t, quoted in tokens(seg)[vi + 1:]:
        if skip:
            skip = False
            continue
        if t in ("<", ">", ">>", ">|", "&>"):
            skip = True
            continue
        if not quoted and (t.startswith(("<", ">")) or re.match(r"^\d+>", t)):
            continue
        if not include_flags and not quoted and t.startswith("-"):
            continue
        out.append(t)
    return out


#: instrument_hygiene.py's own shape `bare-python-on-project-code`: MEASURED at
#: 0.63% of ~23,000 historical Bash commands (up from 0.03%), and that gate rates
#: it "viable as a PreToolUse rule" itself. On this machine bare `python`/`python3`/
#: `py` IS the system Python 3.10, with none of the project's dependencies
#: installed and a syntax it does not even parse (nested f-strings) -- so it fails
#: with ModuleNotFoundError or SyntaxError, which reads exactly like a genuine test
#: failure rather than a wrong interpreter.
#:
#: DIFFERS from instrument_hygiene's own regex in several MEASURED respects,
#: stated rather than left to be discovered:
#:   * SEGMENT- AND VERB-SCOPED, not pure text: instrument_hygiene's pattern is
#:     "no `uv run` ANYWHERE earlier in the command". This predicate uses the
#:     same discipline every other rule in this file uses, so a `uv run`
#:     mentioned in an unrelated earlier segment, a comment, or a quoted string
#:     cannot immunise (or fake-trigger) a real bare invocation.
#:   * WIDER on the INTERPRETER and the SHAPE: `py` counts too (instrument_hygiene
#:     names only `python3?`), and it is not limited to `-m pytest` -- `-m
#:     x4validate` and a script path under toolkit code count too.
#:   * NARROWER on WHAT COUNTS AS "toolkit code" than CLAUDE.md's own routing
#:     table wording ("tools/, scripts/, gates/, .claude/hooks/") -- see
#:     `_PROJECT_DIR` below for the measurement that cut it down to
#:     `tools/x4validate/` + `gates/`. Classifying every historical hit (not a
#:     sample) found the wider wording flagged files that genuinely run fine
#:     under this machine's real bare Python 3.10: `.claude/hooks/*.py` is
#:     invoked bare BY THE HOOK INFRASTRUCTURE ITSELF, and the toolkit-root
#:     `scripts/*.py` files (x4lock.py, x4canary.py, scan-identifiers.py, ...)
#:     import stdlib only. A rule is not "viable as a PreToolUse rule" (this
#:     gate's own words) if a third of its fires are advice against something
#:     that already works.
#: `python2`/`pypy`/`pypy3` (unlike `_PYTHONS` above) are DELIBERATELY EXCLUDED:
#: this rule is about the one spelling this workspace actually types by reflex,
#: not every Python-family executable.
#: `python3.10` / `python3.12` are the same system interpreter under a versioned name, and
#: walked past the bare word (v3.3.0 release review, finding 6).
_BARE_PY_WORD = re.compile(r"^(?:python(?:3(?:\.\d+)?)?|py)(?:\.exe)?$", re.IGNORECASE)


_VENV_DIR = re.compile(r"(^|/)\.?venv(/|$)", re.IGNORECASE)


def _is_venv_interpreter(path: str) -> bool:
    """A virtual environment's interpreter: `pyvenv.cfg` sits one level above its
    `bin/`/`Scripts/` (true of every venv, `uv`'s included, and of no system
    install), or -- for a path this machine cannot stat -- a `.venv`/`venv` dir."""
    p = path.replace(chr(92), "/")
    if _VENV_DIR.search(p):
        return True
    try:
        return os.path.isfile(os.path.join(os.path.dirname(os.path.dirname(p)),
                                           "pyvenv.cfg"))
    except (OSError, ValueError):
        return False


def _is_bare_python_word(tok: str) -> bool:
    """True for the word `python`/`python3`/`py`, or an ABSOLUTE path to such an
    interpreter that is not a virtual environment's.

    The harm is the INTERPRETER, not the spelling. 2026-09-26, fuzz-guard: the first
    version treated every path as "not bare", so `/usr/bin/python3 -m pytest` and
    `"C:\\...\\Python310\\python.exe" -m pytest` -- the very system interpreter this rule
    exists for -- walked past it. A RELATIVE path (`.venv/Scripts/python`) stays
    allowed: the hook cannot resolve it against the command's own cwd reliably. This
    checks the RAW verb token, not `_verb_name`'s basename-folded one, so a venv path
    and the bare word never compare equal.
    """
    if not tok:
        return False
    if "/" not in tok and chr(92) not in tok:
        return bool(_BARE_PY_WORD.match(tok))
    p = tok.replace(chr(92), "/")
    absolute = p.startswith("/") or bool(re.match(r"^[A-Za-z]:/", p))
    if not absolute or not _BARE_PY_WORD.match(p.rsplit("/", 1)[-1]):
        return False
    return not _is_venv_interpreter(p)


#: Directory components that make an argument TOOLKIT CODE -- i.e. that it needs
#: the `tools/x4validate` venv (third-party deps, or the `x4validate` package
#: itself) to run at all. NARROWER than the CLAUDE.md routing table's own
#: "tools/, scripts/, gates/, .claude/hooks/" wording, and MEASURED, not guessed:
#: classifying every historical hit (23,216 commands; see the commit that added
#: this rule) found bare `python`/`python3`/`py` on `.claude/hooks/*.py` and on the
#: toolkit-ROOT `scripts/*.py` (`x4lock.py`, `x4canary.py`, `scan-identifiers.py`,
#: `verify-hook-tests.py`, `fuzz-guard.py`, `audit-coverage.py`, ...) genuinely
#: WORKS on this machine's real bare Python 3.10 -- every one of those imports
#: stdlib only (or a local sibling module), and `.claude/hooks/` is invoked bare
#: BY THE HOOK INFRASTRUCTURE ITSELF (protect-bash.sh's own `"$PY" "$HOOK_DIR/
#: hook_facts.py"`, never `uv run`), so denying it would be advising against the
#: one thing the toolkit's own machinery does on purpose. `tools/basex/*.py` is
#: the same story (`ask.py`/`staleness.py` import only a local sibling module).
#: That is 963+ of the pre-fix corpus's 1,788 hits accounted for as measured false
#: positives, not a sample -- so this predicate covers only what is LEFT: the
#: `tools/x4validate` subtree (its own package, gates, tests and nested scripts/
#: all share ONE venv and ONE documented invocation, "uv run --frozen python from
#: tools/x4validate") plus a bare `gates` component, kept because it is the one
#: directory name that exists nowhere else in this repository (measured: a single
#: `gates/` folder, under `tools/x4validate/`), so a relative `gates/x.py` is
#: unambiguous even with no cwd evidence at all.
_PROJECT_DIR = re.compile(r"(^|/)tools/x4validate(/|$)|(^|/)gates(/|$)")

#: `-m pytest` / `-m x4validate` -- the two shapes instrument_hygiene's own docstring
#: names. They are toolkit code only WHERE the toolkit is: run from a directory under
#: tools/x4validate, or pointed at a path there. By NAME alone the deny fired on
#: `python -m pytest` in any unrelated project (v3.3.0 release review, finding 6).
_PROJECT_MODULES = {"pytest", "x4validate"}

#: Stands in for the shell's starting directory when the payload does not carry one, so a
#: RELATIVE `cd tools/x4validate` is still visible to the bare-python rule (join_cwd needs
#: an absolute base). Used by that rule ONLY: every other rule keeps "unknowable -> no
#: match", which is the safety property join_cwd documents.
_UNKNOWN_CWD = "/x4-unknown-cwd"


#: A redirect operator, alone or glued to its target/fd (`2>&1`, `>>out.txt`,
#: `<<PYEOF`). MEASURED: without this, `python - <<PYEOF ... PYEOF` -- reading a
#: heredoc-fed script from stdin, a routine idiom for a throwaway edit -- had its
#: HEREDOC MARKER (`<<PYEOF`, `<<EOF`, bare `<<`...) read as if it were the script
#: PATH, and over half of every historical hit (963 of 1,788) was exactly this:
#: a marker text that happened to resolve to nothing in particular, saved only by
#: the shell's own cwd already being under toolkit code. Never a script.
_REDIR_TOKEN = re.compile(r"^\d*(<<<|<<|<|>>|>\|?|&>)")

#: Characters no relative PATH operand carries once the shell has parsed it, and that
#: mis-split shell text does: a quote left by `\"$G\"`, the `)` of a `$(...)` the segmenter
#: cut through, a glued `2>/dev/null`, a pipe. Lane F joins only CLEAN relative operands
#: onto the session seed; debris keeps its pre-seed directory (see prep in facts()).
_DEBRIS = re.compile("[\"'()<>|`]")
#: A Windows DEVICE name is not a file in the directory: `2>nul` from the game root writes
#: nothing there. MEASURED in the lane-F replay: 9 historical `2>nul` / `>nul` commands run
#: from the game root became "redirecting into a game directory" advisories with the seed.
_DEVICE = re.compile(r"^(nul|con|prn|aux|com[1-9]|lpt[1-9]):?$", re.I)


def _python_inert_literal_program(code: str) -> bool:
    """Small positive proof for dropping argv after -c; opaque code keeps argv.

    In particular exec/eval, indirect calls and imported helpers may consume argv
    as code. This is deliberately limited to pass, prose and literal print calls.
    """
    import ast
    if len(code) > 65536:
        return False
    try:
        tree = ast.parse(code)
        if sum(1 for _ in ast.walk(tree)) > 256:
            return False
        for statement in tree.body:
            if isinstance(statement, ast.Pass):
                continue
            if not isinstance(statement, ast.Expr):
                return False
            node = statement.value
            if isinstance(node, ast.Constant):
                continue
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'print'
                    and all(isinstance(arg, ast.Constant) for arg in node.args)
                    and all(keyword.arg in ('sep', 'end') and isinstance(keyword.value, ast.Constant) for keyword in node.keywords)):
                return False
        return True
    except (SyntaxError, ValueError, RecursionError):
        return False


def _python_literal_source(segment: str) -> str:
    """First actual -c program for the durable literal-open heuristic.

    Module/script arguments are data to Python, even when they resemble code.
    For -c only proven inert literal programs can discard trailing argv: an
    opaque program may execute those arguments through exec or another helper.
    Keep the old conservative scan for unknown flags, launchers, interactive
    execution and timeit/pdb (which intentionally execute argument text).
    This does not inspect arbitrary script/module implementations.
    """
    verb_token, index = _verb_word(segment)
    name = _verb_name(verb_token)
    if name not in _PYTHONS or name == 'uv' or index is None:
        return segment
    rest = tokens_of(segment)[index + 1:]
    i = 0
    while i < len(rest):
        word = rest[i]
        redirect = _REDIR_TOKEN.match(word)
        if redirect:
            i += 2 if redirect.end() == len(word) else 1
            continue
        if word == '--' or not word.startswith('-'):
            return ''  # first positional script; subsequent argv is not inline code
        if word == '-':
            return segment  # supplied stdin is outside this literal argument proof
        if name == 'py' and re.fullmatch(r'-[23](?:\.\d+)?(?:-(?:32|64))?|-V:.+', word):
            i += 1
            continue
        if word == '--check-hash-based-pycs':
            i += 2
            continue
        if word in ('--help', '--version'):
            return ''
        flags, k = word[1:], 0
        while k < len(flags):
            flag = flags[k]
            if flag in 'cm':
                value = flags[k + 1:] or (rest[i + 1] if i + 1 < len(rest) else '')
                if flag == 'c':
                    return value if _python_inert_literal_program(value) else segment
                return segment if value in ('timeit', 'pdb') else ''
            if flag in 'WX':
                if k + 1 == len(flags):
                    i += 1  # separate option value is not another Python option
                break
            if flag in 'hV':
                return ''
            if flag == 'i' or flag not in 'bBdEIOPqRsSuvx':
                return segment
            k += 1
        i += 1
    return segment


def _save_read_only_program(segment: str) -> bool:
    """Narrow exemptions for the possible-access policy, never a profile-ask lift.

    Redirects/substitutions still go through their own mutation analysis. gzip
    without stdout can replace its input and must retain the opaque-save policy.
    """
    name = verb(segment)
    if name in {"head", "tail", "du", "zcat"}:
        return True
    if name == "gzip":
        _name, index = _verb_word(segment)
        words = tokens_of(segment)[index + 1:] if index is not None else []
        i = 0
        while i < len(words):
            word = words[i]
            # Stop before operands: POSIXLY_CORRECT can disable getopt permutation.
            if word == "--" or word == "-" or not word.startswith("-"):
                break
            if word in ("--stdout", "--to-stdout"):
                return True
            if word == "--suffix":
                i += 2
                continue
            if not word.startswith("--"):
                for k, flag in enumerate(word[1:], 1):
                    if flag == "c":
                        return True
                    if flag in "Sb":  # suffix/legacy bit-count arguments consume the rest or next word
                        if k == len(word) - 1:
                            i += 1
                        break
            i += 1
    if name in _PYTHONS and name != "uv":
        source = _python_literal_source(segment)
        return bool(source and source != segment and _python_inert_literal_program(source))
    return False


def _python_script_target(rest: list):
    """(`"module"`|`"file"`, value) for the first thing a python invocation would
    actually EXECUTE, given the tokens AFTER its verb -- or None when nothing runs
    at all: a flag-only call (`--version`), `-c` whose payload is inline text, or
    bare `-`/no operand at all -- python's own "read the script from stdin" idiom,
    almost always paired with a heredoc. Neither `-c` nor `-` names a file on disk,
    so `python --version` and `python - <<PYEOF ... PYEOF` must stay allowed even
    from inside tools/x4validate: they run no PROJECT FILE at all, whatever the
    heredoc body imports (which this predicate cannot see, any more than it can
    see the guts of a `-c` string).

    A redirect token (or its target, when the two are separate words) is skipped
    outright, never mistaken for the positional script argument.
    """
    i = 0
    while i < len(rest):
        t = rest[i]
        m = _REDIR_TOKEN.match(t)
        if m:
            # A BARE operator (`<<` on its own) still has its target as the NEXT
            # token (`<< PYEOF`, spelled with a space); a GLUED one (`<<PYEOF`,
            # `2>&1`) is already whole and consumes nothing further.
            i += 2 if m.end() == len(t) else 1
            continue
        if t == "-m":
            return ("module", rest[i + 1]) if i + 1 < len(rest) else None
        if t in ("-c", "-"):
            return None
        if t.startswith("-"):
            i += 1
            continue
        return ("file", t)
    return None


def _bare_python_targets_project_code(sg: str, c_cwd: str, assigns: dict) -> bool:
    """True when segment `sg`'s own verb is a bare `python`/`python3`/`py` AND what
    it runs is toolkit code: `-m pytest`/`-m x4validate`, a script path that is
    (or resolves, joined against the shell's OWN `c_cwd` from cwd_track) under
    tools/x4validate/ or a bare `gates/` reference.

    JOINS with cwd rather than treating cwd as an independent yes/no signal --
    MEASURED, and it is the fix for the false positives that shape produced: a
    scratch script's path (`"$S/xedit.py"`, `$(cygpath -w .../scratchpad/x.py)`,
    an absolute `/tmp/...`) does not turn into toolkit code merely because the
    shell happened to `cd tools/x4validate` first for an unrelated later command
    in the same chain -- `join_cwd` already returns the path UNCHANGED for an
    absolute operand and "" (unknowable, so no match) for a relative one with no
    known cwd, which is exactly the discipline every other rule in this file uses
    (see `prep()`). An operand this hook cannot resolve at all (a live `$(...)` or
    an unassigned `$VAR`) is left alone rather than guessed at -- conservative,
    per "err toward fewer denies".

    Does not re-derive verb resolution: `sg` already comes out of `resolve_verb`
    (see facts()), so a variable-spelled verb (`PY=python; $PY -m pytest`) is seen
    exactly as the plain spelling is -- the same F111 guarantee `durable_python_open_w`
    relies on.
    """
    verb_tok = _verb_token(sg)
    if not _is_bare_python_word(verb_tok):
        return False
    toks = tokens_of(sg)
    try:
        idx = toks.index(verb_tok)
    except ValueError:
        return False
    got = _python_script_target(toks[idx + 1:])
    if got is None:
        return False
    kind, val = got
    if kind == "module":
        if val not in _PROJECT_MODULES:
            return False
        if c_cwd and _PROJECT_DIR.search(norm(c_cwd)):
            return True
        # ...or pointed at toolkit code explicitly: `python -m pytest tools/x4validate/tests`.
        rest = toks[toks.index(val, idx + 1) + 1:] if val in toks[idx + 1:] else []
        return any(_PROJECT_DIR.search(norm(resolve(t, assigns))) for t in rest
                   if not t.startswith("-"))
    resolved = resolve(val, assigns)
    if has_unresolved(resolved):
        return False
    if _PROJECT_DIR.search(norm(resolved)):
        return True
    full = join_cwd(c_cwd, resolved)
    return bool(full) and bool(_PROJECT_DIR.search(norm(full)))


COPY_VERBS = {"cp", "mv", "move", "copy", "tee", "install", "rsync"}


def copy_dests(seg: str) -> list[str]:
    """Where a copy/move/tee WRITES. cp/mv write their LAST operand -- unless -t names
    the destination directory. tee writes EVERY file operand, so its destination is the
    FIRST; taking "the last token" for tee picked up the source of a `< input`."""
    v = verb(seg)
    if v not in COPY_VERBS:
        return []
    toks = tokens(seg)
    for i, (t, quoted) in enumerate(toks):
        if t == "--":
            break
        if t in ("-t", "--target-directory"):
            if i + 1 < len(toks):
                return [toks[i + 1][0]]
        if t.startswith("--target-directory="):
            return [t.split("=", 1)[1]]
    ops = _operands(seg)
    if not ops:
        return []
    return ops if v == "tee" else [ops[-1]]


def exact_copy_sources(seg: str) -> list[str] | None:
    """Literal cp/mv source operands for directory destination expansion only.

    Sources must later qualify as individual regular files; directory trees,
    aliasing, backup-suffix and unfamiliar options remain unqualified.
    This does not change the Bash rule facts or execute the requested copy.
    """
    if verb(seg) not in ("cp", "mv"):
        return None
    operands, directory = [], None
    options, skip = True, False
    for token, quoted in tokens(seg)[_verb_at(seg, -1) + 1:]:      # P1: see _verb_at
        if skip:
            directory, skip = token, False
            continue
        if options and token == "--":
            options = False
            continue
        if options and token in ("-t", "--target-directory"):
            skip = True
            continue
        if options and token.startswith("--target-directory="):
            directory = token.split("=", 1)[1]
            continue
        if options and token.startswith("-"):
            if token not in ("-f", "--force", "-i", "--interactive", "-n", "--no-clobber", "-v", "--verbose",
                              "-r", "-R", "--recursive"):
                return None
            continue
        if not quoted and (token.startswith(("<", ">")) or token in ("|", "&")):
            return None
        operands.append(token)
    if skip or not operands:
        return None
    return operands if directory is not None else operands[:-1] if len(operands) >= 2 else None


DELETE_VERBS = {"rm", "rmdir", "unlink", "shred"}


# ---------------------------------------------- v3.3.0 release review: pre-arc gaps
# One principle, applied where the Bash half had no model at all: a delete or write target
# the text makes RESOLVABLE is judged exactly like the literal spelling (so a hard block
# still denies), and one it does not -- but that sits against a protected root -- reaches
# the conservative branch (ask). Each shape below was ALLOW, reproduced E2E on fake roots.

#: rsync flags that DELETE in the destination: every entry there that the source lacks.
_RSYNC_DELETE = re.compile(r"^--(delete|delete-before|delete-during|delete-delay|"
                           r"delete-after|delete-excluded|del)$")


def rsync_deletes(seg: str) -> list[str]:
    """`rsync --delete SRC DST` removes DST's contents that SRC lacks -- with an empty
    SRC, all of them. Reported as `<DST>/*`, which the rules judge as `rm -rf <DST>/*`."""
    if verb(seg) != "rsync" or not any(_RSYNC_DELETE.match(t) for t in tokens_of(seg)):
        return []
    return [d.rstrip("/" + chr(92)) + "/*" for d in copy_dests(seg)]


#: A robocopy switch: `/MIR`, `/XD`, `/R:3` -- and Git Bash's `//MIR`. A path such as
#: `/c/Users/x` has further slashes, so it is not one.
_ROBO_SWITCH = re.compile(r"^/{1,2}[A-Za-z]+(:[^/]*)?$")


def robocopy_effects(seg: str) -> tuple:
    """(copy destinations, deleted-content targets, moved sources) of a robocopy.
    `robocopy SRC DST /MIR` (or /PURGE) deletes what DST has and SRC lacks -- with an
    empty SRC, everything; /MOV and /MOVE take the source away."""
    if verb(seg) != "robocopy":
        return [], [], []
    toks = tokens_of(seg)
    k = _verb_at(seg, 0)                # P1: see _verb_at
    sw = [t.lstrip("/").split(":", 1)[0].lower() for t in toks[k + 1:] if _ROBO_SWITCH.match(t)]
    ops = [t for t in toks[k + 1:] if not _ROBO_SWITCH.match(t)
           and not t.startswith(("<", ">")) and not re.match(r"^\d+>", t)]
    if len(ops) < 2:
        return [], [], []
    src, dst = ops[0], ops[1]
    dels = [dst.rstrip("/" + chr(92)) + "/*"] if ("mir" in sw or "purge" in sw) else []
    moved = [src] if ("mov" in sw or "move" in sw) else []
    return [dst], dels, moved


#: Verbs that MODIFY a file in place without writing its content: its timestamps, mode,
#: owner, or (ln -f) the directory entry itself. Judged as writes, so reference/'s hard
#: block covers them as it covers `>` (reviewer's pre-arc note: all three were ALLOW).
_TOUCH_VALUE_OPTS = {"-d", "--date", "-r", "--reference", "-t"}


def modify_targets(seg: str) -> list[str]:
    v = verb(seg)
    if _verb_name(v).lower() in ("mklink", "fsutil"):
        # F331 follow-up: the LINK a cmd `mklink` / `fsutil hardlink create` places is a write
        # where it lands, exactly as `ln`'s is below. MEASURED: neither was any write before.
        return [at for _t, at, _k in link_creations(seg) if at]
    if v not in ("touch", "chmod", "chown", "chgrp", "ln"):
        return []
    toks = tokens(seg)
    k = _verb_at(seg, 0)                # P1: see _verb_at
    ops, skip, target_dir = [], False, None
    for i, (t, q) in enumerate(toks[k + 1:], k + 1):
        if skip:
            skip = False
            continue
        if not q and v == "touch" and t in _TOUCH_VALUE_OPTS:
            skip = True
            continue
        if not q and v == "ln" and t in ("-t", "--target-directory"):
            target_dir = toks[i + 1][0] if i + 1 < len(toks) else None
            skip = True
            continue
        if not q and t.startswith("--reference="):
            continue
        if not q and (t.startswith("-") or t.startswith(("<", ">")) or re.match(r"^\d+>", t)):
            continue
        ops.append(t)
    if v == "touch":
        return ops
    if v == "ln":
        if target_dir:
            return [target_dir]
        return [ops[-1]] if len(ops) >= 2 else []
    return ops[1:]                              # chmod/chown/chgrp: MODE/OWNER first


def xargs_feed(seg: str, prev) -> list[str]:
    """Delete operands an `xargs <delete verb>` receives on STDIN from `prev`.

    `echo <R> | xargs rm -rf` deletes <R>, and rm_paths() saw an rm with no operand. The
    words of an echo/printf are the operands themselves (resolvable: judged like the
    literal). Any other producer yields names the guard cannot read, so each of ITS
    operands becomes `<operand>/${XARGS_ITEM}` -- an unresolved path that still names
    the tree it came from, which is exactly what the conservative delete branch reads.
    """
    toks = tokens_of(seg)
    vt = _verb_token(seg)
    if prev is None or verb(seg) not in DELETE_VERBS or vt not in toks:
        return []
    if not any(_verb_name(t) == "xargs" for t in toks[:toks.index(vt)]):
        return []
    pv = verb(prev)
    if pv in ("echo", "printf"):
        pt = tokens(prev)
        pvt = _verb_token(prev)
        k = next((i for i, (t, _q) in enumerate(pt) if t == pvt), 0)
        words_ = [t for t, q in pt[k + 1:] if q or not t.startswith("-")]
        if pv == "printf" and words_ and "%" in words_[0]:
            words_ = words_[1:]
        return words_
    if pv == "find":
        # The cache cleanup the find-delete rules already exempt (_REGENERABLE), spelled
        # through xargs: every name the find can match is a regenerated cache, `-o` or not
        # (MEASURED in this lane's replay: `find <toolkit>/x4validate -name __pycache__ -o
        # -name "*.pyc" | xargs -r rm -rf` asked).
        ft = tokens_of(prev)
        names = [ft[i + 1] for i, t in enumerate(ft[:-1]) if t in ("-name", "-iname")]
        if names and all(n in _REGENERABLE for n in names):
            return []
        # find's PATHS only -- its predicates' values are not places.
        return [o.rstrip("/" + chr(92)) + "/${XARGS_ITEM}" for o in _find_roots(prev)]
    return [o.rstrip("/" + chr(92)) + "/${XARGS_ITEM}" for o in _operands(prev)]


def _find_roots(seg: str) -> list:
    """The starting points of a find: operands after the verb, before the first `-flag`."""
    toks_q = tokens(seg)
    start = _verb_at(seg, 0) + 1        # P1: see _verb_at
    out = []
    for t, quoted in toks_q[start:]:
        if not quoted and t.startswith(("-", "(", "!")):
            break
        out.append(t)
    return out


#: `$(realpath X)`, `$(readlink -f X)`, `$(cygpath -u X)`, `$(echo X)`: a substitution
#: whose output IS its one operand, spelled differently. Resolved to that operand so a
#: delete through it is judged as the literal is (the reviewer's pre-arc note:
#: `t=$(realpath <R>/x); rm -rf "$t"` was ALLOW).
_IDENTITY_SUBST = re.compile(r"^\$\((realpath|readlink|cygpath|echo)\s(.*)\)$", re.S)


def identity_subst(tok: str) -> str:
    m = _IDENTITY_SUBST.match(tok.strip())
    if not m or _SUBST.search(m.group(2)) or re.search(r"[|;&<>]", blank_quoted(m.group(2))):
        return tok
    inner = tokens(m.group(2))
    flags = [t for t, q in inner if not q and t.startswith("-")]
    ops = [t for t, q in inner if q or not t.startswith("-")]
    if m.group(1) == "readlink" and not any(set(f[1:]) & set("fem") for f in flags):
        return tok                               # plain readlink returns the LINK TARGET
    return ops[0] if len(ops) == 1 else tok


#: A find carrying any of these is SCOPED to matching entries, not the whole tree.
_FIND_FILTERS = {"-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename",
                 "-regex", "-iregex", "-samefile", "-newer", "-size", "-user", "-group"}


#: Patterns that match everything, so a filter carrying one narrows nothing.
_UNIVERSAL = {"*", "**", "*.*", ".*", "?*", "*/*", ""}


#: Names that are REGENERATED by the tool that owns them, never authored: deleting them
#: loses nothing. The one exemption a filtered find-delete keeps, and it is the reason the
#: exemption existed at all -- MEASURED 2026-09-01, all 40 prompts a filtered find caused
#: over 13,282 commands were a __pycache__ cleanup.
_REGENERABLE = {"__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".mypy_cache",
                ".ruff_cache"}


def _narrows(toks: list, i: int) -> bool:
    """True when the filter at index `i` genuinely restricts the match set."""
    arg = toks[i + 1] if i + 1 < len(toks) else ""
    return arg not in _UNIVERSAL


#: find operators that make the expression an OR, so one test no longer bounds the set.
_FIND_OR = {"-o", "-or", ","}


def _only_regenerable(toks: list) -> bool:
    """The find can only match a regenerable cache: some -name/-iname names one, and the
    expression has no OR. find's tests are ANDed by default, so every OTHER filter --
    `-not -path './.venv/*'`, `-path '*/gates/*'`, `-type d` -- can only SHRINK the set
    further. MEASURED in the friction replay (AUDIT-2026-09-24 HK-2): requiring EVERY
    filter to be a cache name asked on 36 historical `find . -name __pycache__ -type d
    -not -path ... -exec rm -rf {} +` cleanups, all false positives."""
    if any(t in _FIND_OR for t in toks):
        return False
    return any(t in ("-name", "-iname") and i + 1 < len(toks) and toks[i + 1] in _REGENERABLE
               for i, t in enumerate(toks))


#: find actions that RUN a command per match. `-execdir`/`-okdir` differ from
#: `-exec`/`-ok` only in the working directory they run from; they delete identically.
_FIND_EXEC = ("-exec", "-execdir", "-ok", "-okdir")


def find_deletes(seg: str) -> list:
    """Paths a `find` would delete. `find <root> -delete` and `find <root> -exec rm ...`
    remove files just as surely as rm does, and DELETE_VERBS knew neither.

    MEASURED 2026-09-01 over 13,277 historical commands: `-delete` appears 4 times (none
    on a protected root) and `-exec rm` 0 times. So this is a 0-incidence gap -- fixed
    because the failure mode is an unguarded delete of the game install, not because it
    was observed. Recording the denominator is the point: a finding with no incidence
    over-ranks by construction (F90).
    """
    paths, filtered = _find_delete(seg)
    return [] if filtered else paths


def find_scoped_deletes(seg: str) -> list:
    """Paths UNDER which a FILTERED `find` deletes matching entries.

    A filter makes the delete SCOPED -- it removes entries inside the tree, never the
    tree itself -- so it must not reach the whole-install HARD BLOCK (find_deletes).
    It used to reach NOTHING: the filter exemption returned [] and every rule read that
    as "no delete here". AUDIT-2026-09-24 HK-2, MEASURED: `find <saves> -name
    '*.xml.gz' -delete` (every save game) and `find <reference> -name '*.xml' -delete`
    (the read-only base data) were ALLOW, while `rm` of one file in either is an
    ask/deny. These paths now feed the in-tree delete rules exactly as `rm <path>/x`
    does. A filter naming only a regenerable cache (_REGENERABLE) still reaches no rule:
    that cleanup is what the exemption was written for.
    """
    paths, filtered = _find_delete(seg)
    if not filtered:
        return []
    toks = [t for t, _q in tokens(seg)]
    return [] if _only_regenerable(toks) else paths


def _find_delete(seg: str) -> tuple:
    """(paths a `find` deletes under, whether a NARROWING filter scopes it)."""
    if verb(seg) != "find":
        return [], False
    toks = [t for t, q in tokens(seg)]
    # A find NARROWED by a name/path filter deletes matching entries, not the tree. That
    # distinction is the whole rule: `find <game> -delete` removes the install, while
    # `find . -name __pycache__ -exec rm -rf {} +` is routine hygiene. MEASURED
    # 2026-09-01: treating both alike added 40 prompts across 13,282 commands, every one
    # a __pycache__ cleanup -- noise by this project's own standard, since a prompt must
    # be reserved for what is genuinely the user's decision.
    # A filter only NARROWS if its pattern excludes something. `-name` with a
    # universal glob matches every entry, so `find <game> -name "*" -delete` is a
    # whole-tree delete wearing a filter's clothing -- and it was exempted by the
    # very rule that exists to allow genuinely-scoped cleanups.
    filtered = any(_narrows(toks, i) for i, t in enumerate(toks) if t in _FIND_FILTERS)
    deletes = "-delete" in toks
    if not deletes:
        for i, t in enumerate(toks):
            # All four spellings, and the verb NORMALISED. This compared `-exec` to a
            # bare token, three functions below the `_verb_name()` that exists to fold
            # `/bin/rm` and `rm.exe` onto `rm` -- so the rule caught the one form it was
            # written against and missed `-execdir` (which find's own documentation
            # recommends OVER -exec), `-ok`, `-okdir`, and any absolute spelling.
            if t in _FIND_EXEC and i + 1 < len(toks):
                nxt = _verb_name(toks[i + 1])
                if nxt in DELETE_VERBS:
                    deletes = True
                    break
                # `find <root> -exec bash -c '<cmd>' _ {} ;` -- the shell is a carrier
                # here exactly as it is anywhere else, so it goes through the same walk
                # rather than a second implementation.
                if nxt in _SHELLS:
                    rest = " ".join(toks[i + 1:])
                    if any(_verb_name(verb(x)) in DELETE_VERBS
                           for x in _inner_commands(rest)):
                        deletes = True
                        break
    if not deletes:
        return [], filtered
    # find's PATHS are the operands before the first predicate (a `-flag`) -- counted
    # from the VERB, not from token 0. `tokens(seg)[1:]` assumed `find` was the first
    # word, so behind a wrapper with a flag (`nice -n 5 find <saves> ... -delete`,
    # `sudo -u root find ...`) the walk stopped at the wrapper's own `-n` and returned
    # NO path. Found by scripts/fuzz-guard.py on the HK-2 seed (AUDIT-2026-09-24): 9
    # wrapper spellings, ask -> allow.
    toks_q = tokens(seg)
    start = _verb_at(seg, 0) + 1        # P1: see _verb_at
    out = []
    for t, quoted in toks_q[start:]:
        if not quoted and t.startswith("-"):
            break
        out.append(t)
    return out, filtered


#: Verbs that DESTROY a file's content in place without deleting it. AUDIT-2026-09-24
#: HK-2, MEASURED: `truncate -s 0 <reference>/f` and `dd of=<reference>/f` were ALLOW,
#: while the identical effect spelled `> <reference>/f` is a HARD BLOCK. Each is
#: reported as a TRUNCATING REDIRECT to its target, so every rule that already judges
#: `>` -- the reference block, Documents, durable records, shared /tmp -- judges these
#: too, rather than a second copy of those rules growing here.
_TRUNCATE_VALUE_OPTS = {"-s", "--size", "-r", "--reference"}


def clobber_targets(seg: str) -> list[str]:
    """Files `truncate` or `dd of=` overwrite in place."""
    v = verb(seg)
    if v == "truncate":
        out, skip, seen = [], False, False
        for t, quoted in tokens(seg):
            if not seen:
                seen = _verb_name(t) == "truncate"
                continue
            if skip:
                skip = False
                continue
            if not quoted and t in _TRUNCATE_VALUE_OPTS:
                skip = True
                continue
            if not quoted and t.startswith("-"):
                continue
            out.append(t)
        return out
    if v == "dd":
        # `conv=notrunc` still OVERWRITES from the start, so it is not an exemption.
        return [t[3:] for t, _q in tokens(seg) if t.startswith("of=") and len(t) > 3]
    return []


def move_sources(seg: str) -> list[str]:
    """What a `mv` takes AWAY. Every operand but the last is a source.

    `copy_dests` only ever looked at a copy/move DESTINATION, so moving a protected root
    elsewhere was silent while deleting it was a hard block. The install is equally gone
    either way: the game stops working, every deployed mod goes with it, and Steam has to
    re-validate. A guard that blocks the delete and waves the move through is not
    protecting the thing it names.

    `cp` is deliberately excluded -- copying FROM a root reads it and leaves it in place.
    """
    if verb(seg) not in ("mv", "move"):
        return []
    toks = tokens(seg)
    for i, (t, quoted) in enumerate(toks):
        # With -t the destination is named by the flag, so EVERY operand is a source.
        if not quoted and t in ("-t", "--target-directory"):
            return _operands(seg)
        if not quoted and t.startswith("--target-directory="):
            return _operands(seg)
    ops = _operands(seg)
    return ops[:-1] if len(ops) > 1 else []


#: MOVE-TO-TRASH commands, which are DELETES of their operands (FX-G2 item 7, v4.0.0 delta
#: review, reviewer D-1). The user's rule -- deletions go to the Recycle Bin / Trash -- stays;
#: core.md tells Linux/macOS agents to delete this way, and the guards ALLOWED `gio trash
#: '<X4_GAME>'`, `gio trash '<X4_REFERENCE>/a.xml'`, `kioclient5 move <game> trash:/` and a
#: Finder `delete` through osascript. A trashed install is as gone for the game as a deleted one,
#: so each takes rm's verdicts exactly: game/extensions deny, reference deny, saves/profile ask.
_TRASH_VERBS = {"trash", "trash-put", "gvfs-trash", "gvfs-rm", "rmtrash"}
_GIO_DELETE = {"trash", "remove", "rm"}
_KIO_DELETE = {"remove", "rm", "del"}
_KIOCLIENT = re.compile(r"^kioclient\d*$")
_FILE_URL = re.compile(r"^file://(?:localhost)?(?:/(?=[A-Za-z]:))?", re.IGNORECASE)
#: A Finder object an AppleScript `delete` names by path: `POSIX file "/x"`, `folder "x"`, ...
_AS_PATH = re.compile(r"(?:POSIX\s+file|POSIX\s+path|file|folder|alias|item|disk\s+item)\s+"
                      r"\"([^\"]+)\"", re.IGNORECASE)


def _url_path(o: str) -> str:
    """A `file://` URL's PATH, percent-DECODED (FX-G4 / reviewer H-M3, MEASURED: `gio trash
    "file://.../X4%20Foundations"` only advised -- `%20` hid the space, so the operand was
    a sibling of the game root, not the root). Any other operand unchanged."""
    if not _FILE_URL.match(o):
        return o
    from urllib.parse import unquote
    return unquote(_FILE_URL.sub("", o))


def trash_paths(seg: str) -> list[str]:
    """Operands a move-to-trash command takes out of their folder (see _TRASH_VERBS)."""
    v = verb(seg)
    if v in _TRASH_VERBS:
        return [_url_path(o) for o in _operands(seg)]
    ops = _operands(seg)
    if v == "gio" and ops:
        if ops[0] in _GIO_DELETE:
            return [_url_path(o) for o in ops[1:]]
        if ops[0] in ("move", "mv") and len(ops) > 2 and ops[-1].lower().startswith("trash:"):
            return [_url_path(o) for o in ops[1:-1]]
        return []
    if _KIOCLIENT.match(v or "") and ops:
        if ops[0] in _KIO_DELETE:
            return [_url_path(o) for o in ops[1:]]
        if ops[0] in ("move", "mv") and len(ops) > 2 and ops[-1].lower().startswith("trash:"):
            return [_url_path(o) for o in ops[1:-1]]
        return []
    if v == "osascript":
        toks = tokens_of(seg)
        script = " ".join(toks[i + 1] for i, t in enumerate(toks[:-1]) if t == "-e")
        if re.search(r"\bdelete\b", script, re.IGNORECASE):
            return _AS_PATH.findall(script)
    return []


def rm_paths(seg: str) -> list[str]:
    if verb(seg) in DELETE_VERBS:
        return _operands(seg)
    return trash_paths(seg) or find_deletes(seg)


# ------------------------------------------------------------------ searches
# rg and ag recurse with NO flag at all. The bash rule gated on a recursive FLAG, so a
# full-tree `rg` -- the exact command the rule exists to stop -- was allowed.
SEARCH_VERBS = {"grep": False, "egrep": False, "fgrep": False,
                "rg": True, "ag": True, "ack": True}
_RECURSIVE = re.compile(r"^-[a-zA-Z]*[rR][a-zA-Z]*$")


# Flags that CONSUME the next argument. Without this, `grep -r -e foo /ref` counted
# "foo" as a path: -e supplied the pattern, so the operand walk must skip it too --
# not merely know that a pattern was given.
_ARG_FLAGS = {"-e", "-f", "-m", "-A", "-B", "-C", "-d", "-g", "-t",
              "--regexp", "--file", "--max-count", "--include", "--exclude",
              "--exclude-dir", "--binary-files", "--color", "--colour", "--glob"}
_PATTERN_FLAGS = {"-e", "-f", "--regexp", "--file"}


def search_paths(seg: str, require_recursive: bool = True) -> list[str]:
    """Paths a search runs over. The first non-flag operand is the PATTERN -- unless
    -e/-f supplied it, in which case every remaining operand is a path.

    `require_recursive=False` gives the file operands of a NON-recursive search too.
    The profile rule needs those (grepping a manifest by name is not recursive) and
    used to reach for the raw command text instead, which is what made it fire on a
    grep of a local file that merely sat beside a mention of the profile. One
    implementation of the pattern-vs-file logic, two callers."""
    v = verb(seg)
    if v not in SEARCH_VERBS:
        return []
    recursive = SEARCH_VERBS[v]
    pattern_given = False
    ops: list[str] = []
    skip = False
    vi = _verb_word(seg)[1]             # P1: see _operands
    for t, quoted in (tokens(seg)[vi + 1:] if vi is not None else []):
        if skip:
            skip = False
            continue
        if not quoted and t.startswith("-") and t != "-":
            if _RECURSIVE.match(t) or t == "--recursive":
                recursive = True
            base = t.split("=", 1)[0]
            if base in _PATTERN_FLAGS:
                pattern_given = True
            if base in _ARG_FLAGS and "=" not in t:
                skip = True
            continue
        if not quoted and (t.startswith(("<", ">")) or re.match(r"^\d+>", t)):
            continue
        ops.append(t)
    if require_recursive and not recursive:
        return []
    return ops if pattern_given else ops[1:]


DIR_VERBS = {"cd", "pushd"}


def session_cwd(payload: dict) -> str:
    """The directory the shell STARTS in: the payload's top-level `cwd`, when it is an
    absolute path, else "" (unknowable -- join_cwd's safety property).

    Claude Code and Codex both send it with every call (Codex: MEASURED 0.160.0, the
    session cwd; Claude Code: the directory in force, which follows the Bash tool's own
    `cd`). Without it a RELATIVE operand reached no path rule at all: MEASURED
    2026-10-02, `rm -f reference/libraries/x.xml` from the folder holding reference/ was
    ALLOW while the absolute spelling denied, and a live Codex run overwrote the file.
    """
    c = payload.get("cwd")
    # Only a STRING is passed on; join_cwd already turns a relative or garbage base into
    # "" for every operand, so an `is_abs` test here would be a clause no input can reach.
    return c if isinstance(c, str) else ""




def cwd_track(cmd: str, base: str = "", seeded: bool = False, roots: dict | None = None) -> list:
    """[(segment, the directory in force FOR that segment)], in command order.

    Positional, not end-state: in `cd X && rm -rf Y` the `cd` itself still runs from
    the base, and only `rm` sees X. `pushd` pushes, `popd` pops -- without the stack,
    ignoring `popd` would resolve a later relative path against a directory the shell
    had already left, inventing a false positive.

    A subshell's `cd` is deliberately NOT unwound at the closing paren. Modelling that
    needs a real parser, and for a guard the relocated directory is the safe error.

    `roots`: a `cd` to a root VARIABLE lands in that root (subst_root_var, R2-P1). Without
    it the directory after `cd "$X4_REFERENCE"` was unknowable and every later relative
    operand reached no rule.
    """
    return [(seg, d) for seg, ds in cwd_lanes(cmd, base, seeded, roots)
            for d in dict.fromkeys(ds)]


#: How many directories cwd_lanes() keeps in force at once (FX-G7). Each `cd` whose target
#: has several values (an unassigned variable: resolve_variants) splits every lane; past
#: this bound a lane takes the value that lands under a configured root instead (the
#: FX-G6 single choice) -- never silently the plain resolve.
_MAX_CD_LANES = 16
#: C7 (v4.0 release review): set when a walk passed _MAX_CD_LANES. Picking one value per lane
#: there was the shape FX-G7 itself calls wrong -- MEASURED from the toolkit root: `cd
#: "${A:+a}${B:+b}." && cd "${C:+c}${D:+d}${E:+e}<reference>" && rm -rf libraries` ADVISED (v3.3.1
#: denied). Past the bound the directory is UNKNOWN, so facts() reports `cd_lanes_truncated`
#: and protect-bash.sh DENIES (split the command), exactly as for carriers_truncated.
_CD_LANES_TRUNCATED = [False]


def cwd_lanes(cmd: str, base: str = "", seeded: bool = False, roots: dict | None = None, *, physical=False) -> list:
    """[(segment, [every directory that may be in force FOR it])], in command order --
    cwd_track()'s model with one LANE per possible directory (FX-G7, corpus #397).

    FX-G6 judged a `cd` with several possible targets by ONE of them: the first that landed
    under any configured root. MEASURED: from the toolkit, `cd "${NOPE:+x}<reference>" &&
    rm -rf libraries` took `x<reference>` -- RELATIVE, so joined under the toolkit, which is
    a root -- and the rm was an advisory where e39853f denied (NOPE unset lands in
    reference). One chosen value is the wrong shape whatever the choice rule, because the
    strictest directory depends on the operand judged against it. So every value is kept:
    lane j of every segment is one consistent history of `cd`s, and a rule judging a
    relative operand against every lane takes the strictest verdict (facts() ORs them).
    Lanes are split per candidate VALUE, which does not depend on `base`, so the seeded and
    unseeded walks of one command have the same lanes in the same order."""
    #: Each lane: [cwd, from_seed, pushd stack].
    #: from_seed is True while the directory in force is still the SESSION's (the seed, or a resolved
    #: relative `cd` from it) -- no absolute `cd` has said where the shell is. Lane F,
    #: MEASURED over the history replay: from a seeded game root, `cd "$X4_TOOLKIT" && sed
    #: -i ... scripts/x` joined the unresolved target ONTO the game root (the sticky join
    #: below) and a toolkit edit read as a sed -i of the GAME -- a hard deny on routine
    #: work. A seed is where the shell started, not evidence about where an unknown `cd`
    #: lands, so from the seed an unresolved target is unknowable; after an absolute `cd`
    #: the sticky join stands exactly as before.
    #: Only for a caller that passes the SESSION cwd as `seeded`; the bare-python rule's
    #: stand-in base keeps its own, older semantics.
    from_seed = seeded and bool(base)
    #: The directory is what every later RELATIVE operand is judged against, so an
    #: operand read as literal text here disarms the path rules for the rest of the
    #: command -- `cd "$FZ" && rm -rf extensions` walked through a hard block on that.
    #: Every other rule resolves before matching; this one did not.
    assigns = assignments(cmd)

    def step(seg, v, lane, tgt):
        """One lane through one segment; `tgt` is the value its cd/pushd takes in this lane."""
        cwd, from_seed, stack = lane[0], lane[1], list(lane[2])
        if v in DIR_VERBS:
            ops = _operands(seg)
            if ops:
                if v == "pushd":
                    stack.append((cwd, from_seed))
                # `cd -` returns somewhere this hook cannot know; refuse to guess.
                if ops[0] == "-":
                    cwd = ""
                elif from_seed and has_unresolved(tgt) and not is_abs(tgt):
                    cwd = ""
                else:
                    from_seed = from_seed and not is_abs(tgt)
                    # RESOLVED, then joined exactly as before. Blanking the directory
                    # when the target stays unresolved looks more careful and is not:
                    # MEASURED 2026-09-02, `cd <game> && cd "$NOPE" && rm -rf extensions`
                    # went deny -> allow under that rule, because the sticky join keeps
                    # the operand under the root we last knew about. Leaving the join
                    # alone makes this change a pure tightening.
                    # Comparison paths deliberately fold case. Preservation needs
                    # the spelling the command actually opens, especially on POSIX.
                    cwd = physical_join(cwd, tgt) if physical else join_cwd(cwd, tgt)
        elif v == "popd" and stack:
            cwd, from_seed = stack.pop()
        return (cwd, from_seed, stack)

    out, lanes = [], [(base, from_seed, [])]
    for seg in segments(cmd):
        out.append((seg, [ln[0] for ln in lanes]))
        v = verb(seg)
        ops = _operands(seg) if v in DIR_VERBS else []
        if not ops:
            lanes = [step(seg, v, ln, "") for ln in lanes]
            continue
        # One directory, several possible values (FX-G6 / reviewer K C1): `cd
        # "${NOPE:+x}<reference>"` is <reference> when NOPE is unset and x<reference> when
        # set -- so EVERY value gets its own lane (FX-G7).
        cands = [subst_root_var(r, roots) for r in resolve_variants(ops[0], assigns)]
        if len(lanes) * len(cands) <= _MAX_CD_LANES:
            lanes = [step(seg, v, ln, c) for ln in lanes for c in cands]
        else:
            # Past the bound: the directory is UNKNOWN (C7) -- reported, and refused by
            # protect-bash.sh. The lanes below only keep the walk going for the other facts.
            _CD_LANES_TRUNCATED[0] = True
            lanes = [step(seg, v, ln, next((c for c in cands
                                            if any(rt and under(join_cwd(ln[0], c), rt)
                                                   for rt in (roots or {}).values())),
                                           cands[0]))
                     for ln in lanes]
    return out


# ------------------------------------------------- rules that were raw-string regexes
# Each ANDed two independent predicates over the WHOLE command text, so it fired when
# both merely APPEARED anywhere -- and missed when the real invocation did not match the
# regex's assumed shape. MEASURED 2026-09-01, both directions on the same four rules:
#   FALSE POSITIVES  an in-place edit of a local file with the game named in a COMMENT
#                    a grep whose PATTERN mentions the shared temp dir
#                    a grep of a local file beside a cat of a profile manifest
#   BYPASSES (found by scripts/fuzz-guard.py, which mutates only surrounding syntax)
#                    `( git add -A )` and `PYTHONIOENCODING=utf-8 git add -A` -> allow
# Same root cause in both directions, so one fix: ask the parser which VERB runs and
# what its OPERANDS are, instead of searching the text.

GIT_ADD_ALL = {"-A", "--all", "."}

#: Every operand that means "everything", normalised. This was an exact-token set of
#: three, so four spellings of the same action walked past -- and `:/` is the WORST of
#: them: it stages from the repository root regardless of the current directory, so it
#: is strictly broader than the `.` the rule was written for. `git stage` is a real
#: built-in synonym for `git add`.
_GIT_ADD_VERBS = ("add", "stage")


def _stages_everything(tok: str) -> bool:
    t = tok.rstrip("/")
    return t in ("", ".", ":", ":/", "*", "-A", "--all") or tok in GIT_ADD_ALL


def git_adds_everything(seg):
    """`git add -A|--all|.|./|:/|*`, and `git stage` -- seen through a subshell, an env
    prefix, a wrapper and `git -C <path> add`, none of which the old anchored regex
    allowed."""
    if verb(seg) != "git":
        return False
    toks = [t for t, _q in tokens(seg)]
    at = next((i for i, t in enumerate(toks) if t in _GIT_ADD_VERBS), -1)
    if at < 0:
        return False
    return any(_stages_everything(t) for t in toks[at + 1:])


#: Subcommands that OVERWRITE OR DELETE the working tree. Deliberately not every
#: destructive git command -- only the ones that discard uncommitted work in files
#: that already exist, which is the shape that loses data nobody else has.
_GIT_DESTRUCTIVE = {"clean", "reset", "stash", "checkout", "restore"}


def git_wipes_worktree_targets(seg):
    """UNBOUNDED destructive git: `clean -f`, `reset --hard` and `stash -u`/`-a` (P2).

    Split from the targeted form deliberately. These name no paths, so they reach
    every file in the repository INCLUDING untracked ones -- which have no history and
    no other copy. That is the shape that loses something irreplaceable, and it is the
    one CLAUDE.md's hook policy calls genuinely the user's decision ('deleting inside
    an X4 directory').
    """
    return _git_destructive(seg, {"clean", "reset", "stash"})


def git_wipes_ignored_targets(seg):
    """The subset of git_wipes_worktree_targets that reaches IGNORED files or whole untracked
    directories: `clean` with -x, -X or -d, `reset --hard` (Plan 3 decision J-Q1), and
    `stash --all`/`-a` (v4.0.0 review P2), which stashes the ignored files and then DELETES
    them from the working tree. In the
    game-root repo `.gitignore` is a whitelist (`*`), so every untracked file there is ignored
    and `git clean -fdx` removes the installation's own files. Judged from the SESSION cwd
    (seeded) by `git_wipe_from_session_dir`, whose verdict is a DENY, never a prompt."""
    return _git_destructive(seg, {"clean", "reset", "stash"}, deep=True)


def git_discards_named_files(seg):
    """TARGETED destructive git: `checkout -- <path>`, `restore <path>`.

    Also overwrites files past the read-only lock, but every path it can name is by
    definition TRACKED, so the content is recoverable from the object store. Reporting
    it to the model is useful; interrupting the USER for it is not -- restoring a
    source file after a mutation run is command hygiene, not a decision that belongs
    to them. MEASURED over 13,503 real commands: 4 rows, every one exactly that.
    """
    return _git_destructive(seg, {"checkout", "restore"})


#: `GIT_CONFIG_COUNT=`, `GIT_CONFIG_KEY_0=`, `GIT_CONFIG_PARAMETERS=`, `GIT_CONFIG=`,
#: `GIT_CONFIG_GLOBAL=` ... -- an assignment (or PowerShell `$env:` one) to any of them.
_GIT_CONFIG_ENV_SET = re.compile(r"(?<![A-Za-z0-9_])GIT_CONFIG[A-Z0-9_]*\s*=", re.I)
#: Set per facts() call: does this command set git config through the environment?
_GIT_ENV_CONFIG = [False]
#: FX-G6 / reviewer K I2: where git reads its GLOBAL config from. `HOME=/x git clean -dx` reads
#: `/x/.gitconfig`, which may set clean.requireForce false -- unreadable here, so forced. (The
#: GIT_CONFIG_GLOBAL / GIT_CONFIG_SYSTEM spellings are already _GIT_CONFIG_ENV_SET.)
_GIT_HOME_SET = re.compile(r"(?<![A-Za-z0-9_$])(?:HOME|XDG_CONFIG_HOME)\s*=")
#: Options of `git config` that only READ (and `--show-origin`/`--show-scope`: `--show-*`).
_GIT_CONFIG_READS = ("--get", "--list", "-l")
#: git's global options that take the NEXT word as their value.
_GIT_TOP_VALUE_OPTS = ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                       "--config-env", "--super-prefix", "--list-cmds", "--attr-source")


def _git_config_writes(seg: str) -> bool:
    """Is this segment a `git config` that may WRITE (FX-G6 / reviewer K I2)? FX-G5 matched the
    literal `requireForce` in the command, and the key has other spellings git accepts:
    `"clean.require""Force"`, `clean.require\\Force`, `K=clean.requireForce; git config $K
    false` all switched it off unseen (MEASURED E2E: allowed). So ANY `git config` that is not
    a read (`--get*`, `--list`, `-l`, `--show-*`, or the `get` / `list` subcommands) counts:
    a clean in the same command is judged as forced -- an ask, never a deny."""
    if verb(seg) != "git":
        return False
    toks = tokens_of(seg)
    i = next((j for j, t in enumerate(toks) if _verb_name(t) == "git"), len(toks)) + 1
    while i < len(toks) and toks[i].startswith("-"):
        i += 2 if toks[i] in _GIT_TOP_VALUE_OPTS else 1
    if i >= len(toks) or toks[i] != "config" or not toks[i + 1:]:
        return False
    args = toks[i + 1:]
    if any(a.startswith(("--get", "--show-")) or a in _GIT_CONFIG_READS for a in args):
        return False
    return next((a for a in args if not a.startswith("-")), "") not in ("get", "list")


def _gitdir_parent(v):
    """The folder holding a `--git-dir` / `GIT_DIR` repository (`<x>/.git` -> `<x>`)."""
    v = v.rstrip("/" + chr(92))
    return v[:-5] if v.lower().endswith(".git") and v[-5:-4] in ("/", chr(92)) else v


def _git_destructive(seg, wanted, deep=False):
    r"""The directory a destructive git subcommand would act on, or [].

    MEASURED 2026-09-04, and it is why this exists at all: **git ignores the
    read-only attribute entirely.** `git checkout HEAD~1 -- <locked file>` overwrote
    a locked file AND left it unlocked afterwards; `git clean -fdx` deleted one. So
    `scripts/x4lock.py` -- which stops 11 of 14 write primitives on Windows and 9
    of 14 on POSIX (x4lock.py:29; `unlink` and `rename`-over are authorised by
    DIRECTORY permission there) -- stops none of
    these, and the hook is the only layer that can see them.

    Scoped to the segment's own cwd (or `git -C <path>`), never to a root named
    anywhere in the command line: `cd "<root>" && git clean -fdx` is the real shape,
    and `join_cwd` already returns "" when the directory is unknowable, so an
    ordinary `git clean` in an unknown cwd reaches no rule rather than firing on
    unrelated work.

    NOT destructive, and each of these has a must-NOT-fire test:
      * `git checkout <branch>` / `-b <new>` -- navigation. Only the explicit `--`
        pathspec form discards file contents.
      * `git reset` / `--soft` / `--mixed` -- these move refs and the index; the
        working tree survives. Only `--hard` overwrites files.
      * `git clean -n` / `--dry-run` -- prints what it would remove.
    """
    if verb(seg) != "git":
        return []
    toks = [t for t, _q in tokens(seg)]
    base = ""
    skip = set()
    # EVERY pre-subcommand option that consumes the NEXT token, not just -C.
    # MEASURED 2026-09-04: `git -c core.fileMode=false clean -fdx` was an ALLOW while
    # the same command without `-c` is an ask. `-c` was treated as a bare flag, so its
    # VALUE (`core.fileMode=false`) became the "subcommand" and matched nothing in
    # `wanted`. One config option silenced the only layer that can see a destructive
    # git -- in the rule whose own docstring records that git ignores the read-only
    # lock entirely. 15 `git -c` commands in 13,503 of history, 0 with a destructive
    # subcommand: the same denominator this file already accepted for `find -delete`.
    _GIT_VALUE_OPTS = ("-C", "-c", "--git-dir", "--work-tree", "--namespace",
                       "--exec-path", "--config-env")
    # WHERE THE FILES ARE, and whether -f is needed (FX-G2 / reviewer E6). `--work-tree X`,
    # `--work-tree=X` and a `GIT_WORK_TREE=X` prefix NAME the tree a clean/reset deletes in --
    # only -C was read, so `git --git-dir=<game>/.git --work-tree <game> clean -fdx` reached no
    # rule. A `--git-dir`/`GIT_DIR` repo's own folder is judged too (its core.worktree may point
    # there; reading it as a target can only add an ask). And `-c clean.requireForce=false`
    # makes a clean with NO -f delete (MEASURED by the reviewer), so it counts as forced.
    worktree, gitdirs, forced = None, [], False

    for i, t in enumerate(toks):
        if t in _GIT_VALUE_OPTS and i + 1 < len(toks):
            if t == "-C":          # only -C names the directory acted on
                base = toks[i + 1]
            elif t == "--work-tree":
                worktree = toks[i + 1]
            elif t == "--git-dir":
                gitdirs.append(_gitdir_parent(toks[i + 1]))
            elif t == "--config-env":
                # Its VALUE lives in an environment variable the guard cannot read, so ANY
                # key may be (or include) requireForce (FX-G5 / reviewer J2 item 5).
                forced = True
            elif t == "-c" and toks[i + 1].lower().startswith(("include.", "includeif.")):
                # `-c include.path=<file>` reads a config FILE the guard cannot see (J2 item 5).
                forced = True
            elif t == "-c" and toks[i + 1].lower().split("=", 1)[0] == "clean.requireforce":
                # git's false spellings; a bare `-c clean.requireForce` means true.
                kv = toks[i + 1].lower().split("=", 1)
                # `forced or`: a later `-c` must not undo an include seen before it.
                forced = forced or (len(kv) == 2 and kv[1] in ("false", "no", "off", "0", ""))
            skip.add(i + 1)
        elif t.startswith("--config-env="):
            forced = True
        elif t.startswith("--work-tree="):
            worktree = t.split("=", 1)[1]
        elif t.startswith("--git-dir="):
            gitdirs.append(_gitdir_parent(t.split("=", 1)[1]))
        elif t.startswith("GIT_WORK_TREE="):
            worktree = t.split("=", 1)[1]
        elif t.startswith("GIT_DIR="):
            gitdirs.append(_gitdir_parent(t.split("=", 1)[1]))
    # FX-G4 / reviewer H6, MEASURED: `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=clean.requireForce
    # GIT_CONFIG_VALUE_0=false git -C <game> clean -dx` (and GIT_CONFIG_PARAMETERS) DELETED
    # with no -f and was allowed. git config set through the environment -- as a prefix or
    # assigned/exported anywhere in the command -- may switch requireForce off, and its
    # value cannot always be read (a variable, a file), so such a clean counts as forced.
    if _GIT_ENV_CONFIG[0] or any(_GIT_CONFIG_ENV_SET.match(t) for t in toks):
        forced = True
    # THE VERB IS NOT ALWAYS toks[0]. `verb()` skips leading VAR=value assignments,
    # this scan did not, and `toks[1:]` therefore started ON the `git` token when a
    # prefix was present -- so `sub` became "git", matched nothing in `wanted`, and
    # every destructive-git rule went silent. `git_adds_everything` was unaffected
    # because it searches ALL tokens; only this scan assumed a position.
    # ...AND NOT ALWAYS AFTER THE ASSIGNMENTS EITHER. B3 taught this scan to step over
    # leading `VAR=value`; a leading WRAPPER walks past it the same way, and `verb()`
    # already steps over both -- so `verb(seg)` said "git" while this scan called the
    # literal token `git` the SUBCOMMAND and matched nothing in `wanted`.
    #
    # MEASURED 2026-09-06 by scripts/fuzz-guard.py, once `git_wipes_x4_dir` and
    # `git_discards_x4_files` finally had seeds (they had none, so nothing had ever
    # tried): `exec`, `setsid`, `nice -n 5` and `timeout 5` each turned a destructive
    # git inside the game root from ask/advise into ALLOW -- 8 bypasses. `timeout` is
    # the one that matters most, because CLAUDE.md #25 recommends typing it.
    # Consults the SAME `_WRAPPER_VALUE_OPTS` table `_verb_token` uses. The loops are
    # separate because that one streams `tokens()` with its quoted flag while this one
    # indexes a plain list, but WHICH flags take a value is one table in one place --
    # this file's history is a list of things that drifted because two sites computed
    # one answer. MEASURED 2026-09-06: fixing only `_verb_token` left 10 bypasses here
    # (`env -u X4_GAME git -C <game> clean -fdx` and four more spellings), because
    # these two rules do their own scan rather than keying off verb().
    # P1 (v4.0 release review): the SAME verb index _verb_word computes, from whole WORDS --
    # it steps over assignments and wrappers exactly as this loop did, and an assignment
    # holding a spaced `<(...)` no longer offers a fragment (`a)`) as the command.
    vi = _verb_word(seg)[1]
    if vi is None or vi >= len(toks):
        return []
    si = next((i for i, t in enumerate(toks[vi + 1:], vi + 1)
               if not t.startswith("-") and i not in skip), None)
    sub = toks[si] if si is not None else None
    if sub not in wanted:
        return []
    # Slice from the FOUND index, not `toks.index(sub)`: the subcommand name can
    # also appear earlier as an assignment VALUE (`X=clean git clean -fdx`), and
    # index() would return that one and shift every following operand.
    rest = toks[si + 1:]
    if sub == "clean":
        # -f is required by git itself before it deletes anything; -n/--dry-run wins.
        if _git_clean_dry(rest):
            return []
        hot = any(t.startswith("-") and not t.startswith("--") and "f" in t[1:]
                  for t in rest) or any(_git_long(t, "--force") for t in rest) or forced
        # deep (J-Q1): only a clean that reaches ignored files (-x / -X) or untracked
        # directories (-d). A plain `clean -f` removes untracked NON-ignored files only.
        if deep:
            hot = hot and any(t.startswith("-") and not t.startswith("--")
                              and set(t[1:]) & set("xXd") for t in rest)
    elif sub == "reset":
        hot = any(_git_long(t, "--hard") for t in rest)
    elif sub == "stash":
        hot = _stash_removes_untracked(rest, deep)
    elif sub == "checkout":
        hot = "--" in rest          # the pathspec form; a branch name is navigation
    else:                            # restore -- always about file contents
        hot = True
    if not hot:
        return []
    if worktree is not None:          # the work tree REPLACES the -C/cwd folder (E6)
        tree = worktree if (is_abs(worktree) or not base) else base.rstrip("/") + "/" + worktree
        return [tree] + gitdirs
    return [base] + gitdirs


def _git_long(tok, opt, least=3):
    """`tok` spells git's long option `opt`: exactly, or as an ABBREVIATION, which git's
    parse-options accepts for any unambiguous prefix (`--al` is `--all`, `--har` is `--hard`,
    `--fo` is `--force`, `--dry` is `--dry-run`). FX-G2 item 5 (v4.0.0 delta review), MEASURED
    in a scratch repo: `git stash --a` deleted an ignored file and `git clean --fo -X` another,
    while this file matched the full spellings only. An AMBIGUOUS prefix makes git refuse and
    do nothing, so reading one as the option can only cost a false positive. `least` is the
    shortest accepted spelling (`--x`; 6 = `--no-x` for a negation). A `=value` is not part of
    the name."""
    name = tok.split("=", 1)[0]
    return len(name) >= least and name.startswith("--") and opt.startswith(name)


#: git clean's long options (git builtin/clean.c), each also negatable as `--no-<name>`.
#: An abbreviation names an option only when it prefixes exactly ONE of these (FX-G7).
#: MEASURED (git 2.48.1): `--n` is refused as ambiguous (--no-force / --no-interactive).
_GIT_CLEAN_LONG = ("--quiet", "--dry-run", "--force", "--interactive", "--exclude",
                   "--pathspec-from-file", "--pathspec-file-nul")
_GIT_CLEAN_LONG_ALL = _GIT_CLEAN_LONG + tuple("--no-" + o[2:] for o in _GIT_CLEAN_LONG)


def _git_clean_long(tok):
    """The git clean long option `tok` spells -- exactly, or as a prefix of exactly one of
    `_GIT_CLEAN_LONG_ALL` -- else None (not an option, or ambiguous: git then refuses)."""
    name = tok.split("=", 1)[0]
    if len(name) < 3 or not name.startswith("--"):
        return None
    if name in _GIT_CLEAN_LONG_ALL:
        return name
    hits = [o for o in _GIT_CLEAN_LONG_ALL if o.startswith(name)]
    return hits[0] if len(hits) == 1 else None


def _git_clean_dry(rest):
    """Does this `git clean` only PRINT (FX-G7)? The LAST of `-n` / `--dry-run` /
    `--no-dry-run` wins, any unambiguous abbreviation counting (`--d`, `--dry`, `--no-d`).
    MEASURED in a scratch repo (git 2.48.1): `clean -fdx -n --no-dry-run`, `-n --no-d`,
    `-e -n`, `--exclude -n` and `--e -n` each DELETED, while the guard read every one as a
    dry run -- a `-n` that is the VALUE of -e/--exclude is a pattern, and a later
    `--no-dry-run` undoes an earlier `-n`. `--` ends the options."""
    dry, skip = False, False
    for t in rest:
        if skip:
            skip = False
            continue
        if t == "--":
            break
        if t == "-n":
            dry = True
        elif t.startswith("--"):
            o = _git_clean_long(t)
            if o == "--dry-run":
                dry = True
            elif o == "--no-dry-run":
                dry = False
            elif o in ("--exclude", "--pathspec-from-file") and "=" not in t:
                skip = True
        elif t.startswith("-") and len(t) > 1:
            # `-e <pattern>`, also last in a cluster (`-fe -n`); `-epat` carries its own.
            skip = t.find("e", 1) == len(t) - 1
    return dry


def _stash_removes_untracked(rest, deep):
    """Does this `git stash` remove UNTRACKED files from the working tree? (v4.0.0 review P2)

    A plain stash touches tracked files only, and every one is recoverable from the stash
    ref. `-u` / `--include-untracked` also stashes-and-deletes untracked NOT-ignored files --
    the reach of `git clean -f`, so it answers the shallow rule only. `-a` / `--all` takes
    the IGNORED files too -- the reach of `git clean -fdx`, so it answers `deep` as well: in
    the game-root repo, whose `.gitignore` is a whitelist (`*`), the ignored files ARE the
    installation.

    Git's own dispatch: no argument, or a first argument starting with `-`, is `push`;
    otherwise the first argument names the action, and only `push` / `save` stash anything.
    `-m`/`--message` (and `--pathspec-from-file`) consume the next token, so a message that
    reads `-a` is not the flag; in a short cluster an `m` ends it (`-am wip`). `--no-all` /
    `--no-include-untracked` undo an earlier flag, as parse_options does.
    """
    if rest and not rest[0].startswith("-"):
        if rest[0] not in ("push", "save"):
            return False        # list / show / pop / apply / drop / clear / branch / ...
        rest = rest[1:]
    every = untracked = skip = False
    for t in rest:
        if skip:
            skip = False
            continue
        if t == "--":
            break
        if t == "-m" or _git_long(t, "--message") or _git_long(t, "--pathspec-from-file"):
            skip = "=" not in t
        elif _git_long(t, "--no-all", 6):
            every = False
        elif _git_long(t, "--no-include-untracked", 6):
            untracked = False
        elif _git_long(t, "--all"):
            every = True
        elif _git_long(t, "--include-untracked"):
            untracked = True
        elif t.startswith("-") and not t.startswith("--"):
            for k, ch in enumerate(t[1:], 1):
                if ch == "a":
                    every = True
                elif ch == "u":
                    untracked = True
                elif ch == "m":
                    skip = k == len(t) - 1      # `-am <msg>`; `-mfoo` carries its own
                    break
    return every if deep else (every or untracked)


def sed_in_place_targets(seg):
    """Paths an in-place `sed` would rewrite. `-i` may carry a suffix (`-i.bak`).

    THE SCRIPT IS NOT A TARGET. This used to return it anyway, on the reasoning -- its
    own words -- that "a script never resolves under a root, so keeping it costs
    nothing while dropping it would need -e/-f handling."

    MEASURED 2026-09-03: that is false, and in the most ordinary way. A sed script that
    REWRITES a path contains that path, so

        sed -i -e 's|<reference>/.unpacked-and-locked|$VAR/...|g' "$f"

    read as an in-place edit OF the reference tree, when the only file touched is
    `$f` in the toolkit. The cost really was zero -- right up until a hard block
    consumed the result, which is precisely the shape gotcha #23 warns about: a
    recorded cost of zero is the line nobody re-checks.

    POSIX rule, now implemented: with any -e/--expression or -f/--file present EVERY
    non-flag operand is a file; without one the FIRST non-flag operand is the script
    and the rest are files.
    """
    if verb(seg) not in ("sed", "gsed"):
        return []
    toks = list(tokens(seg))
    inplace = any((not q) and (t.startswith("-i") or t.startswith("--in-place"))
                  for t, q in toks)
    if not inplace:
        return []
    files, skip, script_flag = [], False, False
    vi = _verb_word(seg)[1]             # P1: see _operands
    for t, q in (toks[vi + 1:] if vi is not None else []):
        if skip:
            skip = False
            continue
        if (not q) and t in ("-e", "-f", "--expression", "--file"):
            script_flag = True
            skip = True                  # its argument is a SCRIPT, never a target
            continue
        if (not q) and (t.startswith("--expression=") or t.startswith("--file=")):
            script_flag = True
            continue
        if (not q) and (t.startswith("-") or t in ("<", ">", ">>", ">|", "&>")):
            skip = t in ("<", ">", ">>", ">|", "&>")
            continue
        files.append(t)
    return files if script_flag else files[1:]


_OUT_FLAGS = ("-o", "--output")

#: Splits a token at the points where a NEW command can begin inside it. tokens() does
#: not break on these, so `loc=$(grep` arrives as one token and any verb-keyed question
#: about it answers about `loc=$(grep` rather than about `grep`.
_CMD_SPLIT = re.compile(r"[$]\(|" + chr(96) + r"|[|;&]")


def _command_names(seg: str) -> set:
    """Every command NAME that appears anywhere in a segment, substitutions included.

    verb() answers "what does this segment run", which is a different question and the
    wrong one when a command is nested: `loc=$(grep -o 'pat' f)` has verb `loc=$(grep`
    -- or, once the assignment prefix is skipped, the quoted PATTERN.
    """
    out = set()
    for t, q in tokens(seg):
        if q:
            continue
        for part in _CMD_SPLIT.split(t):
            part = part.strip()
            if "=" in part:
                part = part.split("=", 1)[1]
            if part and not part.startswith("-"):
                out.add(_verb_name(part))
    return out


def output_targets(seg):
    """Files a command writes via an explicit output flag: `-o PATH`, `--output PATH`,
    `--output=PATH`. Kept deliberately: scoping the shared-temp rule to redirects alone
    would silently stop covering a download written with an output flag, which the old
    regex did cover."""
    # `-o` IS NOT AN OUTPUT FLAG FOR A SEARCH VERB. For grep/egrep/fgrep/rg/ag/ack it
    # is --only-matching and takes no argument, so the next token is the PATTERN --
    # and treating that as a path made `cd <reference> && grep -o 'radius="[^"]*"' f`
    # look like a write to <reference>/radius="[^"]*".
    #
    # Latent for as long as this helper has existed, and harmless while only the
    # shared-temp rule consumed the result. Promoting it into a hard block is what made
    # it visible -- the same shape as F93, where a capability improvement widened a
    # guard nobody had re-scoped for it.
    #
    # MEASURED 2026-09-03 over 13,503 historical commands: of the 21 rows the new
    # reference-write rule gained, 20 were this -- every one a read-only research
    # command (`cd <reference> && grep -o ...`) that would have become a
    # NON-OVERRIDABLE deny. Not one unit test or E2E probe saw it; the per-item corpus
    # diff is the only instrument that did.
    # _command_names, not verb(): the grep is very often inside a substitution, as in
    # `loc=$(grep -o 'pat' "$f")`, and tokens() keeps `loc=$(grep` as ONE token -- so
    # verb() returns the assignment prefix and the real command name is lost. That
    # accounted for the 3 rows still misfiring after the first version of this fix.
    flags = tuple(f for f in _OUT_FLAGS if f != "-o") \
        if (_command_names(seg) & SEARCH_NAMES) else _OUT_FLAGS
    out, toks, i = [], tokens(seg), 0
    # Bash's own -o selects an option; its value is not an output filename.
    # Exclude only flags before the script/-c/-- boundary. Substitution and
    # carried-command writes still have their own output flags and targets.
    shell_options = set()
    if verb(seg) == "bash":
        _vt, k = _verb_word(seg)
        j = k + 1 if k is not None else len(toks)
        while j < len(toks):
            t = toks[j][0]
            if t in ("-", "--", "+") or t[:1] not in ("-", "+"):
                break
            if t.startswith("--"):
                j += 2 if t in ("--rcfile", "--init-file") else 1
                continue
            if "c" in t[1:]:
                break
            if t == "-o":
                shell_options.add(j)
            j += 1 + sum(ch in "oO" for ch in t[1:])
    while i < len(toks):
        t, q = toks[i]
        if (not q) and t in flags and i not in shell_options and i + 1 < len(toks):
            out.append(toks[i + 1][0])
            i += 2
            continue
        if (not q) and t.startswith("--output="):
            out.append(t.split("=", 1)[1])
        i += 1
    return out


SEARCH_NAMES = {"grep", "egrep", "fgrep", "rg", "ag", "ack", "findstr", "select-string"}


def searches(seg):
    return verb(seg).lower() in SEARCH_NAMES


# ------------------------------------------------------------------- $? rule
def dollarq_after_pipe(cmd: str, assigns: dict | None = None) -> bool:
    """`cmd | head; echo $?` reports HEAD's exit code. Only the same segment or the one
    immediately before can be the referent. A pipe inside a process substitution runs in
    a subshell, so its status never becomes $?."""
    # NO strip_heredocs HERE. It used to, and that was a DOUBLE strip: the only
    # caller passes `all_cmds`, every element of which derives from `body`, which is
    # already stripped. strip_heredocs removes a body and its terminator and KEEPS the
    # opener line -- so a second pass met a dangling `<<X` with no terminator left and
    # consumed everything after it, INCLUDING the command being judged.
    #
    # MEASURED 2026-09-06 by scripts/fuzz-guard.py, once this rule had a seed: any
    # data heredoc written BEFORE the real command turned this deny into an allow --
    # 5 of its 7 remaining bypasses, across plain, quoted, hyphenated, dotted and
    # space-containing markers. The rule was not weak about heredocs; it was deleting
    # its own input.
    stripped = cmd
    # PIPESTATUS is tested against the UNBLANKED text on purpose: the escape hatch is
    # normally written `"${PIPESTATUS[0]}"`, i.e. inside double quotes, and blanking
    # first would hide it and fire on the very idiom the message recommends.
    if "PIPESTATUS" in stripped:
        return False
    # Only SINGLE-quoted content is blanked. Inside single quotes `$?` is literal text
    # (prose, a message, a regex); inside DOUBLE quotes it still expands, so
    # `cmd | head; echo "rc=$?"` is a real hit. blank_quoted() blanks both and would
    # have turned a live rule off -- the opposite mistake to the one being fixed.
    # Callers pass `body`, so comments are already gone.
    live = blank_single_quoted(stripped)
    # An ESCAPED dollar does not expand. `git commit -m "fix \$? after a pipe"` is
    # PROSE -- the backslash makes it literal text in double quotes and unquoted alike,
    # so the shell never reads a status there. Without this the rule was a
    # non-overridable DENY on a commit message describing the very trap it enforces,
    # and on a grep whose PATTERN contains the token. Both happened.
    #
    # Single-quoted content is already blanked above, where a backslash is itself
    # literal, so removing the pair here cannot reach that case.
    live = live.replace(chr(92) + "$", " ")
    if "$?" not in live and not (assigns and "${" in live):
        return False
    prev_piped = False
    for raw in re.split(r"[;\n]|&&|\|\|", live):
        chk = re.sub(r"[<>]\([^)]*\)", "", blank_quoted(raw))
        piped = bool(re.search(r"[^|]\|[^|]", chk))
        # RESOLVED PER SEGMENT, not once over the whole command. A parameter
        # expansion can carry the status out of sight -- `FZ="rc=$?QQ"` then
        # `echo "${FZ%QQ}"` puts no literal `$?` after the pipe at all -- and
        # `_apply_op` already models suffix-strip, default-value and array-index, so
        # the rule only had to ask. Resolving GLOBALLY does not work and that was the
        # first attempt: `$?` is present in the command, inside the ASSIGNMENT, so a
        # whole-string test short-circuits before it ever looks at the segment that
        # actually follows the pipe. MEASURED 2026-09-06 by the fuzzer.
        hot = "$?" in raw
        if not hot and assigns and "${" in raw:
            hot = "$?" in resolve(raw, assigns)
        if hot and (piped or prev_piped):
            return True
        prev_piped = piped
    return False


# ------------------------------------------------------------------- facts
DURABLE = re.compile(r"(memory[/\\][A-Za-z0-9_.-]+\.md|MEMORY\.md|KNOWLEDGEBASE\.md"
                     r"|CLAUDE\.md|BLIND-SPOTS\.md)")
LONG_JOBS = ("corpus_sweep", "perf_guard", "build-effective.sh", "build-corpus.sh",
             "stage.py")
INVOKERS = re.compile(r"\b(uv run|python|python3|bash)\b")
LEGACY_GAME = re.compile(r"x4 foundations|egosoft/x4", re.I)
# ROOT-scoped: the path must END at the game folder (or at its extensions/), not
# merely contain the name. Without the anchor the backstop re-created the very
# over-block it sits beside, by a different route.
#: Characters that make an OPERAND a pattern rather than a literal path. Distinct from
#: `_GLOB_CHARS` above, which is about a ${VAR%pat} expansion and deliberately excludes
#: `{`: brace expansion is not glob matching, but it IS something the shell does to an
#: operand before the command runs, so it belongs here. Naming it separately is not
#: tidiness -- the first draft reused `_GLOB_CHARS`, shadowed a set with a string, and
#: broke every parameter-expansion test in the suite.
_OPERAND_PATTERN_CHARS = "*?[{"


def _brace_expand(pat, _depth=0):
    """Expand `a{b,c}d` into ["abd", "acd"]. One primitive bash does before globbing.

    Bounded deliberately: 6 levels and 64 results. A crafted `{a,b}{a,b}{a,b}...`
    expands combinatorially, and a guard that can be made to hang is a guard that can
    be removed -- the whole point here is to answer quickly on every command.
    Hitting a bound marks incomplete scope. Partial candidates must never be
    certified as complete: the omitted target could be the protected one.
    """
    i = pat.find("{")
    if i < 0:
        return [pat]
    if _depth > 6:
        _OPERAND_EXPANSION_TRUNCATED[0] = True
        return [pat]
    depth, j = 0, -1
    for k in range(i, len(pat)):
        if pat[k] == "{":
            depth += 1
        elif pat[k] == "}":
            depth -= 1
            if depth == 0:
                j = k
                break
    if j < 0:
        return [pat]
    head, body, tail = pat[:i], pat[i + 1:j], pat[j + 1:]
    parts, depth, cur = [], 0, ""
    for ch in body:
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        depth += (ch == "{") - (ch == "}")
        cur += ch
    parts.append(cur)
    if len(parts) == 1:
        # Bash ranges are not modelled here. Never call their one literal-looking
        # spelling a complete expansion. Non-range single braces stay literal.
        if ("{" in body + tail or
                re.fullmatch(r"(?:-?\d+\.\.-?\d+|[A-Za-z]\.\.[A-Za-z])(?:\.\.-?\d+)?", body)):
            _OPERAND_EXPANSION_TRUNCATED[0] = True
        return [pat]
    out = []
    for p in parts:
        for rest in _brace_expand(head + p + tail, _depth + 1):
            out.append(rest)
            if len(out) > 64:
                _OPERAND_EXPANSION_TRUNCATED[0] = True
                return out[:64]
    return out


def glob_covers(operand, target, *, expand_braces=True):
    """Would this operand, expanded by the shell, act on `target`?

    WHY THIS EXISTS. The HARD BLOCKS compared the operand to the root with `==`, so a
    single glob character walked straight past them. MEASURED 2026-09-04, all ALLOW or
    ASK where the plain form is DENY:

        rm -rf "<game>"*        rm -rf "<game>"/*
        rm -rf ".../[X]4 Foundations"      rm -rf ".../X4?Foundations"
        rm -rf ".../X4 Foundation"{s,}

    Every one really does delete the installation. `scripts/fuzz-guard.py` could not
    have found them: its own docstring says it mutates "the SYNTAX AROUND the dangerous
    operation... the dangerous operand is byte-identical in every mutant", so the
    operand is the one axis 996 mutants hold fixed by construction.

    This is NOT the F93 "unresolvable operand" case, and the distinction matters. An
    unexpanded `$DST` cannot be PROVEN to be the install root, so it must not reach a
    non-overridable deny. A glob is fully present in the text: whether it covers the
    root is decidable, and this decides it rather than guessing.

    Two ways to cover a target: BE it (`<game>*` expands to include `<game>`), or
    contain it (`<game>/*` deletes everything in it, which is the same loss).
    """
    if not operand or not target:
        return False
    if not any(c in operand for c in _OPERAND_PATTERN_CHARS):
        return False                      # a literal path; `==` already handled it
    for cand in _brace_expand(operand) if expand_braces else [operand]:
        if fnmatch.fnmatchcase(target, cand):
            return True
        head, sep, tailseg = cand.rpartition("/")
        if sep and tailseg and any(c in tailseg for c in _OPERAND_PATTERN_CHARS) \
                and fnmatch.fnmatchcase(target, head):
            return True                   # `<target>/<glob>` -- deletes its contents
    return False


GAME_ROOTISH = re.compile(r"(x4 foundations|egosoft/x4)(/extensions)?$", re.I)




def _quote_kinds(s: str) -> list:
    """The quote character in force at each index ("" when unquoted).

    _quote_mask answers "is this quoted", which is not enough here: inside DOUBLE
    quotes a command substitution still runs, inside SINGLE quotes it is literal text.
    """
    kinds = [""] * len(s)
    for i, _c, inq, k in _qwalk(s):
        kinds[i] = ("'" if k == "$'" else k) if inq else ""   # ANSI-C is literal too (E5)
    return kinds


def _match_paren(s: str, start: int) -> int:
    """Index of the paren closing the one at `start`, or -1."""
    depth = 0
    for j in range(start, len(s)):
        if s[j] == "(":
            depth += 1
        elif s[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _dollar_double_paren(s: str, i: int) -> tuple:
    """`$((` at `i`: ("arith", the expression, end) for `$(( ... ))`, or ("subst", the command,
    end) for `$( (subshell) ... )` -- whose inner paren closes BEFORE the outer one -- or
    ("", "", -1) when unbalanced.

    FX-G2 item 4 (v4.0.0 delta review), MEASURED: `$((` was stepped over WHOLE as arithmetic,
    so `echo $(( $(rm -rf <game>) + 0 ))`, the backtick form, and the same inside an expanding
    heredoc were ALLOWED past the game hard block -- an arithmetic expression EXPANDS its
    substitutions before it evaluates them. And `$((rm -rf <game>) )` is not arithmetic at all
    (bash reads it as a command substitution holding a subshell; MEASURED: `$((echo hi) )`
    prints hi, `$((echo hi))` is an arithmetic syntax error)."""
    end = _match_paren(s, i + 1)
    if end == -1:
        return "", "", -1
    if _match_paren(s, i + 2) == end - 1:
        return "arith", s[i + 3:end - 1], end
    return "subst", s[i + 2:end], end


def substitutions(cmd: str) -> list[str]:
    """Command text inside a substitution: dollar-paren, backticks, and process
    substitution.

    Every one of these RUNS its contents, and the parse pass treated all of them as
    ordinary text -- so wrapping a command in three characters walked past every rule
    at once. MEASURED 2026-09-02, each against a game-root delete the guard catches
    when written bare: all three returned a silent ALLOW. This file already admitted
    that "precision about operands bought blindness to indirection", about the
    dollar-paren form alone; the CLASS was never enumerated.

    Single-quoted regions are skipped: there the text is literal and nothing runs.
    A doubled open paren is ARITHMETIC, not a subshell, and is stepped over.
    """
    out = []
    kinds = _quote_kinds(cmd)
    i = 0
    while i < len(cmd):
        k = kinds[i]
        c = cmd[i]
        if k == chr(39):                        # single-quoted: literal
            i += 1
            continue
        if c == "$" and i + 1 < len(cmd) and cmd[i + 1] == "(":
            if i + 2 < len(cmd) and cmd[i + 2] == "(":
                kind, text, end = _dollar_double_paren(cmd, i)
                if kind == "arith":
                    out.extend(substitutions(text))
                    i = end + 1
                    continue
                if kind == "subst":
                    out.append(text)
                    i = i + 2
                    continue
                i += 3
                continue
            end = _match_paren(cmd, i + 1)
            if end != -1:
                out.append(cmd[i + 2:end])
                i = i + 2
                continue
        elif c in ("<", ">") and i + 1 < len(cmd) and cmd[i + 1] == "(" and k == "":
            end = _match_paren(cmd, i + 1)
            if end != -1:
                out.append(cmd[i + 2:end])
                i = i + 2
                continue
        elif c == chr(96):
            end = cmd.find(chr(96), i + 1)
            if end != -1:
                out.append(cmd[i + 1:end])
                i = end + 1
                continue
        i += 1
    return [o for o in out if o.strip()]


#: Shells whose `-c` argument is a command string. `dash`/`ksh` cost nothing to add and
#: a missing one is a total bypass.
_SHELLS = ("bash", "sh", "zsh", "dash", "ksh")

#: A SHORT-FLAG CLUSTER containing `c`. Not the literal token `-c`: real invocations
#: combine flags, and `bash -lc '<cmd>'` is the ordinary way to get a login shell.
#: MEASURED 2026-09-01, E2E through protect-bash.sh, against a game-root `rm -rf` the
#: guard denies on its own:
#:      sh -c '<rm>'      -> deny        bash -lc '<rm>'  -> *** SILENT ALLOW ***
#:      xargs ... '<rm>'  -> deny        eval '<rm>'      -> *** SILENT ALLOW ***
#: One character of flag clustering, and the hard block was gone.
_DASH_C = re.compile(r"^-[A-Za-z]*c[A-Za-z]*$")


#: Verbs that RUN what arrives on stdin. `source` and `.` re-run a file or stream in
#: the CURRENT shell, which is the same thing for our purposes.
_SHELL_SINKS = tuple(_SHELLS) + ("source", ".")


def _reads_stdin_program(seg: str) -> bool:
    """A shell with no script to run, so its program can only come from stdin.

    Deliberately narrow. `bash script.sh` and `bash -c '...'` both have somewhere else
    to get their program, and pairing those with a nearby `echo` would invent a command
    the user never piped -- a false DENIAL, which is worse here than a miss.
    """
    toks = [t for t, _q in tokens(seg)]
    if not toks or _verb_name(toks[0]) not in _SHELL_SINKS:
        return False
    rest = _drop_redirects(toks[1:])
    for t in rest:
        if _DASH_C.match(t):
            return False                       # -c carries its own program
        if not t.startswith("-"):
            return False                       # a script file, or /dev/stdin's operand
    return True


#: Redirect operators that CONSUME the following word. A redirect is not an operand, and
#: reading one as a script file is what kept `bash <<< '<cmd>'` an ALLOW after the rest of
#: C2 was fixed -- the here-string is the program, so the single carrier whose payload is
#: plainly visible in the command text was the one we declined to inspect.
_REDIR_WORD = ("<<<", "<", ">", ">>", "<>", ">|", "<<", "<<-")


def _drop_redirects(toks: list[str]) -> list[str]:
    """Tokens with redirect operators and the words they consume removed.

    Handles both spellings: separated (`<<< payload`) and attached (`<<<payload`,
    `>file`), plus an fd prefix (`2>err`, `1>&2`). Deliberately syntactic only -- it
    answers "is this token an operand", never "where does the data go".
    """
    out = []
    i = 0
    while i < len(toks):
        t = toks[i]
        core = t.lstrip("0123456789")          # an fd prefix: 2>err, 1>&2
        hit = ""
        for op in _REDIR_WORD:                 # longest first, so <<< beats <<
            if core.startswith(op) and len(op) > len(hit):
                hit = op
        if hit:
            if core == hit:
                i += 2                         # `<<< payload` -- the word is consumed
            else:
                i += 1                         # `<<<payload` -- attached, nothing follows
            continue
        out.append(t)
        i += 1
    return out


_PRINTF_ESC = {"n": chr(10), "t": chr(9), chr(92): chr(92), "a": "", "r": "", "v": "", "f": ""}


def _printf_render(fmt: str, args: list):
    """printf's output for `%s`/`%b`/`%%` formats with the common escapes, or None when the
    format holds anything else (a width, `%d`, `%q`...) -- text this cannot reproduce."""
    out, ai = [], 0
    while True:
        i = 0
        while i < len(fmt):
            c = fmt[i]
            if c == chr(92) and i + 1 < len(fmt):
                e = _PRINTF_ESC.get(fmt[i + 1])
                if e is None:
                    return None
                out.append(e)
                i += 2
            elif c == "%":
                d = fmt[i + 1:i + 2]
                if d == "%":
                    out.append("%")
                elif d in ("s", "b"):
                    out.append(args[ai] if ai < len(args) else "")
                    ai += 1
                else:
                    return None
                i += 2
            else:
                out.append(c)
                i += 1
        if ai == 0 or ai >= len(args):
            return "".join(out)


def _echo_printf_program(prev: str) -> list:
    """The program an `echo`/`printf` segment prints into a piped shell (H7): what it
    PRINTS, plus its words exactly as written. [] after recording an inability when the
    printf format cannot be reproduced."""
    toks = tokens(prev)
    vt = _verb_token(prev)
    k = _verb_at(prev, 0)               # P1: see _verb_at
    args = [t for t, _q in toks[k + 1:]]
    args = _drop_redirects(args)
    if _verb_name(verb(prev)) == "echo":
        while args and re.fullmatch(r"-[neE]+", args[0]):
            args = args[1:]
        printed = " ".join(args)
    else:
        if args[:1] == ["--"]:
            args = args[1:]
        printed = _printf_render(args[0], args[1:]) if args else ""
        if printed is None:
            _UNTRANSLATED.append("a printf whose format the guard cannot reproduce, "
                                 "piped into a shell")
            return []
    out = [ln for ln in printed.split(chr(10)) if ln.strip()]
    # The producer's words AS WRITTEN: everything after the verb WORD -- stepping past the rest
    # of a quoted or ANSI-C verb (`"echo"`, `$'echo'`, `"C:/x/echo.exe"`), whose closing
    # quote would otherwise open a string over the program (fuzz-guard, FX-G4).
    o = _verb_offset(prev)              # P1: the verb's own word, not its first spelling
    i = (prev.find(vt, o) if o >= 0 else prev.find(vt)) if vt else -1
    raw = ""
    if i >= 0:
        mask = _quote_mask(prev)
        j = i + len(vt)
        while j < len(prev) and (mask[j] or not prev[j].isspace()):
            j += 1
        raw = prev[j:].strip()
    if raw:
        out.append(raw)
    return out


def _procsub_program(seg: str) -> list:
    """FX-G4 / reviewer H8, MEASURED: `bash <(echo rm -rf "<game>")`, `source <(...)` and
    `. <(...)` were ALLOWED. A process substitution that is a shell's SCRIPT operand (its
    first non-option word) is executed text: what an echo/printf inside it prints is the
    program (as for `echo ... | bash`, H7). Any other producer (`source <(sed -n ... f.sh)`,
    `bash <(curl ...)`) is ALLOWED unjudged, exactly as running a script file the guards
    cannot see into (USER DECISION 2026-10-06; an accepted BLIND-SPOTS residual), and
    `bash -n` runs nothing at all. A `<(...)` that is an argument to a script, or to any
    other verb (`diff <(a) <(b)`), is not a program."""
    toks = tokens(seg)
    if not toks or _verb_name(verb(seg)) not in _SHELL_SINKS:
        return []
    k = _verb_at(seg)                   # P1: see _verb_at
    if k is None:
        return []
    # The SHELL'S OWN option words, up to its script operand (FX-G5 / reviewer J2 item 2,
    # MEASURED: allowed). Only these can make it `bash -n`: an `echo -n` inside the
    # substitution, or a `-n` after the script (its $1), is not one. An option that takes a
    # VALUE (`-o pipefail`, `-O extglob`, `+o posix`, `-eo pipefail`, `--rcfile f`,
    # `--init-file f`) is stepped over WITH its value, or the value reads as the script.
    # FX-G6 / reviewer K C2 (REGRESSION of f03d19b, MEASURED E2E deny -> allow): a lone `-`,
    # like `--`, ENDS the options -- the NEXT word is the script (`bash - <(echo ...)`,
    # `bash -x - <(...)`). K I3 (MEASURED: allowed): `+n` turns `-n` back OFF, so only the LAST
    # n-toggle counts -- in a clustered word too (`-xn`, `+xn`), and `+o noexec` cancels too.
    first, syntax_only, j = None, False, k + 1
    while j < len(toks):
        t, q = toks[j]
        if not q and t in ("-", "--"):
            first = toks[j + 1] if j + 1 < len(toks) else None
            break
        if q or t[:1] not in ("-", "+") or t == "+":
            first = (t, q)
            break
        if t.startswith("--"):
            j += 2 if t in ("--rcfile", "--init-file") else 1
            continue
        j += 1
        for ch in t[1:]:
            if ch == "n":
                syntax_only = t[0] == "-"
            elif ch in "oO":                         # each o/O takes the next word
                val = toks[j][0] if j < len(toks) else ""
                if ch == "o" and val == "noexec" and t[0] == "+":
                    syntax_only = False          # `-o noexec` is NOT exempted: only `-n` is
                j += 1
    if not first or first[1] or not first[0].startswith("<("):
        return []
    if syntax_only:
        return []                      # `bash -n`: a syntax check, nothing is executed
    mask = _quote_mask(seg)
    # P1: the first `<(` AFTER the shell's own word -- an assignment prefix can hold one.
    at = next((i for i in range(max(_verb_offset(seg), 0), len(seg) - 1)
               if seg[i:i + 2] == "<(" and not mask[i]), -1)
    close = _match_paren(seg, at + 1) if at >= 0 else -1
    # No `)` in the segment: the segmenter took it as a case-arm/subshell close (`x) bash
    # <(echo ...) ;;`). What is left of the segment is the inner text (fuzz-guard, FX-G4).
    if at < 0:
        return []
    inner = seg[at + 2:close] if close >= 0 else seg[at + 2:]
    segs = segments(inner)
    if segs and _verb_name(verb(segs[-1])) in ("echo", "printf"):
        return _echo_printf_program(segs[-1])
    return []


def _here_string(seg: str) -> str:
    """The operand of a `<<<` here-string, or ''."""
    toks = [t for t, _q in tokens(seg)]
    for i, t in enumerate(toks):
        if t == "<<<" and i + 1 < len(toks):
            return toks[i + 1]
        if t.startswith("<<<") and len(t) > 3:
            return t[3:]
    return ""


# ------------------------------------------------- Windows command carriers (HK-2)
# `cmd //c '<cmd.exe command>'` and `powershell -c '<PowerShell>'` run TEXT, exactly as
# `bash -c` does, and were followed by nothing. AUDIT-2026-09-24 HK-2, MEASURED:
# `cmd //c rd /s /q "<reference>"` and `powershell -c "Remove-Item -Recurse <game>"`
# were ALLOW while `rm -rf` of the same path denies. Both are now walked like any other
# carrier, and what they carry is TRANSLATED into the vocabulary the rules already speak
# rather than given rules of their own.

#: Why a PowerShell payload could not be analysed, recorded for the facts() in progress.
#: A carrier the walk cannot open is not a clean pass: facts() turns a non-empty list
#: into `carrier_untranslated`, which protect-bash.sh ASKS on.
_UNTRANSLATED: list = []
#: Command texts produced by translating PowerShell or cmd (LINUX GAP): their paths are
#: Windows-spelled. Cleared per facts() call, like _UNTRANSLATED.
_WIN_DIALECT: set = set()

#: text -> (sh or None, reason). One translation per distinct text per process: the
#: carrier walk revisits strings, and each translation is a PowerShell start.
_PS_CACHE: dict = {}

#: THE TOTAL PowerShell budget for ONE hook call, in seconds (AUDIT-2026-09-24 HK-1
#: review item 7). Each translation had its own 20 s timeout, so five distinct `pwsh -c`
#: carriers in one Bash command could commit 100 s -- MEASURED with a stubbed wedged
#: pwsh -- against a 30 s hook timeout, and a hook that times out does NOT block: the
#: call would proceed unchecked. The budget is spent across every translation in the
#: call and sits well under the hook timeout (a translation takes ~0.4-1 s); once it is
#: spent, further PowerShell text is REFUSED (ask), never waited for.
_PS_BUDGET_S = 15.0
#: No single translation may take longer than this, even with budget left.
_PS_CALL_CAP_S = 10.0
_ps_spent = [0.0]
_clock = __import__("time").monotonic


def _pwsh_exe():
    """The PowerShell to parse with: $X4_PWSH if set (and then ONLY it -- a configured
    interpreter that is missing is an error, not a cue to pick another, the same rule
    x4_python follows), else pwsh, else Windows PowerShell."""
    want = os.environ.get("X4_PWSH")
    if want:
        return shutil.which(want) or (want if os.path.isfile(want) else None)
    for c in ("pwsh", "powershell"):
        p = shutil.which(c)
        if p:
            return p
    return None


def powershell_to_sh(text: str) -> tuple:
    """(sh, unknown, "") or (None, [], why it could not be made).

    `unknown` lists the parts of a TRANSLATED command whose write/delete target could not
    be resolved -- an unresolvable splat, a method on an unknown object, Invoke-Expression
    of computed text. The caller treats each as untranslated (ASK); the rest of the command
    is still judged, so a deny elsewhere in it still wins.

    PowerShell's OWN parser does the work (ps_translate.ps1: Parser::ParseInput and
    StaticParameterBinder), so aliases, parameter prefixes and positional binding
    resolve the way PowerShell resolves them. None is a REFUSAL, never an allow: the
    caller asks, as protect-bash.sh does for a Bash command `bash -n` rejects.
    """
    if text in _PS_CACHE:
        return _PS_CACHE[text]
    remaining = _PS_BUDGET_S - _ps_spent[0]
    if remaining < 0.5:
        return None, [], ("the PowerShell translation budget for one command (%.0f s) is "
                          "spent, so this part was not analysed" % _PS_BUDGET_S)
    t0 = _clock()
    try:
        got = _translate_ps(text, min(_PS_CALL_CAP_S, remaining))
    finally:
        _ps_spent[0] += max(0.0, _clock() - t0)
    _PS_CACHE[text] = got
    return got


def _translate_ps(text: str, timeout: float = _PS_CALL_CAP_S) -> tuple:
    exe = _pwsh_exe()
    if not exe:
        return None, [], ("no PowerShell was found to parse it (pwsh, or powershell"
                      + (", or X4_PWSH=" + os.environ["X4_PWSH"] if os.environ.get("X4_PWSH")
                         else "") + ")")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ps_translate.ps1")
    if not os.path.isfile(script):
        return None, [], "ps_translate.ps1 is missing beside hook_facts.py"
    try:
        p = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", script],
            input=text.encode("utf-8"), capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        return None, [], "PowerShell could not be run (%s)" % type(exc).__name__
    try:
        res = json.loads(p.stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, [], ("the PowerShell translator returned no verdict (exit %d)"
                      % p.returncode)
    if not isinstance(res, dict) or not res.get("ok"):
        return None, [], str((res or {}).get("reason") or "not translated")
    sh = res.get("command")
    if not isinstance(sh, str):
        return None, [], "the PowerShell translator returned no command"
    unknown = res.get("unknown") or []
    if not isinstance(unknown, list):
        unknown = [str(unknown)]
    return sh, [str(u) for u in unknown], ""


_PS_EXES = ("powershell", "pwsh")
#: PowerShell host parameters that take a VALUE (full names; any prefix of 3+ letters,
#: or a listed alias, is the same parameter).
_PS_VALUE_PARAMS = ("executionpolicy", "windowstyle", "workingdirectory",
                    "configurationname", "outputformat", "inputformat", "version",
                    "psconsolefile", "custompipename", "settingsfile")
_PS_VALUE_ALIASES = {"ep", "ex", "w", "wd", "o", "of", "if", "v", "config"}


def _ps_host_payload(toks: list) -> tuple:
    """For `powershell|pwsh <args>`: ("cmd", text) / ("encoded", b64) / ("file", "") /
    ("stdin", "") / ("none", "").

    ("none", "") -- no -Command, no -File, no bare script -- means the host reads its
    program from STDIN when stdin is redirected (`echo '<ps>' | pwsh`), so the caller
    treats it exactly like `-Command -` (v3.3.0 release review, finding 2)."""
    exe = _verb_name(toks[0])
    i = 1
    while i < len(toks):
        t = toks[i]
        if t[:1] in ("-", "/") and len(t) > 1:
            name = t[1:].lower()
            if name == "-":
                break
            # -CommandWithArgs (pwsh 7.4+): the NEXT word is the program, the rest are its
            # $args. Its prefix `-c...` spelling is not a -Command prefix, so it read as a
            # switch and the program walked past every rule (finding 2).
            if name == "cwa" or (len(name) >= 8 and "commandwithargs".startswith(name)):
                return "cmd", (toks[i + 1] if i + 1 < len(toks) else "")
            if re.fullmatch(r"c(o(m(m(a(n(d)?)?)?)?)?)?", name):
                rest = toks[i + 1:]
                if rest[:1] == ["-"]:
                    return "stdin", ""
                return "cmd", " ".join(rest)
            if name == "ec" or "encodedcommand".startswith(name):
                return "encoded", (toks[i + 1] if i + 1 < len(toks) else "")
            if name in ("f", "file") or (len(name) >= 2 and "file".startswith(name)):
                # `-File -` is the program on stdin, exactly as `-Command -` is.
                if toks[i + 1:i + 2] == ["-"]:
                    return "stdin", ""
                return "file", ""
            if name in _PS_VALUE_ALIASES or (len(name) >= 3 and any(
                    full.startswith(name) for full in _PS_VALUE_PARAMS)):
                i += 2
                continue
            i += 1                                   # a switch: -NoProfile, -NonInteractive ...
            continue
        # The first bare argument: Windows PowerShell runs it AS A COMMAND; pwsh (6+)
        # runs it as a SCRIPT FILE, which, like `bash script.sh`, is not text we hold.
        if exe == "powershell":
            return "cmd", " ".join(toks[i:])
        return "file", ""
    return "none", ""


_SH_BARE = re.compile(r"[A-Za-z0-9_./:@%+=,-]+")


def _sh_quote(s: str) -> str:
    if _SH_BARE.fullmatch(s):
        return s
    return "'" + s.replace("'", "'" + chr(92) + "''") + "'"


#: cmd.exe's `%NAME%` expansion. `%%` is a literal percent. F10: and `!NAME!`, DELAYED
#: expansion (`cmd /v:on`) -- MEASURED in the Skyrim sibling, `!R! del <x>` with R=call
#: really deletes, and `!R!` was carried as a literal name. Read as a variable whether or
#: not /v:on is given: conservative, never a guess that delayed expansion is off.
_CMD_VAR = re.compile(r"%%|%([A-Za-z_][A-Za-z0-9_]*)%|!([A-Za-z_][A-Za-z0-9_]*)!")
#: A cmd word holding ANY expansion -- wider than _CMD_VAR (`%ProgramFiles(x86)%`).
_CMD_DYN = re.compile(r"%[^%\s]+%|![^!\s]+!")


def _cmd_word(w: str) -> str:
    """One cmd.exe word as a shell word, `%NAME%` becoming `"${NAME}"`.

    AUDIT-2026-09-24 HK-1 review item 5: `%X4_REFERENCE%` stayed literal text, so
    `cmd //c rd /s /q "%X4_REFERENCE%"` named no root. As a shell variable it reaches the
    rules exactly as `$X4_REFERENCE` does: a root variable names its root, and any other
    is an unresolved operand -- the Bash semantics, not a new rule. cmd's names are
    case-insensitive; the root variables are upper case.
    """
    out, pos = [], 0
    for m in _CMD_VAR.finditer(w):
        if m.start() > pos:
            out.append(_sh_quote(w[pos:m.start()]))
        name = m.group(1) or m.group(2)
        out.append(_sh_quote("%") if name is None
                   else '"${' + name.upper() + '}"')
        pos = m.end()
    if pos < len(w) or not out:
        out.append(_sh_quote(w[pos:]))
    return "".join(out)


#: cmd.exe verbs, as the rule set already names them.
_CMD_VERBS = {"del": "rm -f", "erase": "rm -f", "rd": "rm -rf", "rmdir": "rm -rf",
              "copy": "cp", "xcopy": "cp -r", "move": "mv", "ren": "mv", "rename": "mv",
              "type": "cat"}
_CMD_TOKEN = re.compile(r'"([^"]*)"|(&&|\|\||>>|[&|<>]|[^\s"&|<>]+)')
_CMD_SWITCH = re.compile(r"^/[A-Za-z0-9?-]{1,3}(:.*)?$")


#: cmd.exe's CARET escape (v3.3.0 release review, finding 3). Outside double quotes `^X`
#: is the literal X: `r^d` is `rd`, and `^&` is an ampersand, not a separator. An escaped
#: metacharacter travels as a placeholder through the tokeniser and is restored after, so
#: it can neither split a command nor vanish.
_CMD_ESC = {"&": chr(16), "|": chr(17), "<": chr(18), ">": chr(19), "^": chr(20)}
_CMD_UNESC = {v: k for k, v in _CMD_ESC.items()}


def _cmd_uncaret(s: str) -> str:
    out, q, i = [], False, 0
    while i < len(s):
        c = s[i]
        if c == '"':
            q = not q
        elif c == "^" and not q and i + 1 < len(s):
            i += 1
            out.append(_CMD_ESC.get(s[i], s[i]))
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _cmd_restore(w: str) -> str:
    return "".join(_CMD_UNESC.get(c, c) for c in w)


def _cmd_words(rest: list) -> list:
    """cmd.exe words. A token that arrived QUOTED with spaces in it is one word -- unless
    it is the whole payload (`cmd //c "rd /s /q X & del Y"`), which cmd re-reads."""
    if len(rest) == 1:
        src = [rest[0]]
    else:
        src = rest
    out = []
    for piece in src:
        if len(src) > 1 and " " in piece:
            out.append(piece)
            continue
        # Placeholders stay in until cmd_to_sh has split on the REAL operators; _cmd_line
        # restores them, so an escaped `^&` is an ampersand in a word, never a separator.
        for m in _CMD_TOKEN.finditer(_cmd_uncaret(piece)):
            out.append(m.group(1) if m.group(1) is not None else m.group(2))
    return out


def cmd_to_sh(rest: list) -> str:
    """The cmd.exe command line after `/c`, as POSIX-shell lines."""
    lines, cur = [], []
    for w in _cmd_words(rest) + ["&"]:
        if w in ("&", "&&", "||", "|"):
            if cur:
                lines.append(_cmd_line(cur))
            cur = []
            continue
        cur.append(w)
    return "\n".join(ln for ln in lines if ln)


#: cmd.exe's directory changers. `cd /d X` changes drive AND directory; read as `cd` with
#: the operand `/d`, the directory was lost and a relative delete after it escaped every
#: root rule (finding 3). cmd does not need quotes around a spaced path here: everything
#: after the switch is the directory.
_CMD_CD = {"cd", "chdir", "pushd"}


def _cmd_line(ws: list, _depth: int = 0) -> str:
    ws = [_cmd_restore(w) for w in ws]
    if ws and _CMD_DYN.search(ws[0]) and _depth < _MAX_DYNAMIC_READINGS:
        # F10: the PROGRAM word is an expansion (`%R% del <x>`, `!R! del <x>`, `%R%del <x>`
        # with R=`call `, MEASURED in the Skyrim sibling to really delete). It was carried as a
        # literal name, so no rule saw the `del`. Now: the line itself with the name as a shell
        # variable (facts' `program_dynamic_protected` asks when an operand is protected), PLUS
        # every speculative reading cmd could run -- the words after it as a command, and the
        # name's literal remainder glued to them (`%R%del` -> `del`). Additive, never a choice.
        head = _cmd_word(ws[0])
        if "${" not in head:
            head = '"${CMD_DYNAMIC_PROGRAM}"' + head
        lines = [" ".join([head] + [_cmd_word(a) for a in ws[1:]])]
        lit = _CMD_DYN.sub("", ws[0])
        for alt in (([lit] + ws[1:]) if lit.strip() else [], ws[1:]):
            if alt:
                ln = _cmd_line(alt, _depth + 1)
                if ln and ln not in lines:
                    lines.append(ln)
        return "\n".join(lines)
    v = _verb_name(ws[0])
    if v in _CMD_CD:
        rest = [w for w in ws[1:] if w.lower() != "/d"]
        return ("pushd " if v == "pushd" else "cd ") + _cmd_word(" ".join(rest)) if rest else ""
    args, redir, i = [], "", 1
    while i < len(ws):
        w = ws[i]
        if w in (">", ">>"):
            if i + 1 < len(ws) and ws[i + 1].lower() != "nul":
                redir += " %s %s" % (w, _cmd_word(ws[i + 1]))
            i += 2
            continue
        if w == "<":
            i += 2
            continue
        args.append(w)
        i += 1
    if v in _CMD_VERBS:
        args = [a for a in args if not _CMD_SWITCH.match(a)]
        # An UNQUOTED spaced path: cmd splits `rd /s /q C:\...\X4 Foundations` into two
        # operands, so what it deletes is not the root -- but the text plainly names it,
        # and a guard that reads it as two harmless names is one quoting slip from the
        # real thing. A delete also judges every contiguous span of its operands rejoined
        # with a space (coordinator verification, v3.3.0 hooks lane). Bounded: 12 words.
        if _CMD_VERBS[v].startswith("rm") and 1 < len(args) <= 12:
            args = args + [" ".join(args[i:j]) for i in range(len(args))
                           for j in range(i + 2, len(args) + 1)]
        if v in ("ren", "rename") and len(args) >= 2 and not re.search(r"[/\\]", args[1]):
            src = args[0].replace(chr(92), "/")
            cut = src.rfind("/")
            args = [args[0], (src[:cut + 1] if cut >= 0 else "") + args[1]]
        head = _CMD_VERBS[v]
    else:
        head = _sh_quote(ws[0])
    return " ".join([head] + [_cmd_word(a) for a in args]) + redir


def _ps_stdin_program(seg: str, prev) -> tuple:
    """(texts, unreadable) -- the program a PowerShell host reads from STDIN.

    Readable when it is visible in the command text: a here-string, or an echo/printf of
    literal words just before it (the Bash-shell form `echo '<cmd>' | bash` already uses
    that pairing). An input redirect from a file, or any other producer feeding it, is a
    program the guard cannot read -- UNREADABLE, so the caller asks. Nothing before it and
    nothing redirected: the host has no program at all (texts == [], not unreadable).

    PowerShell text is TRANSLATED as PowerShell, which is why the hosts are not simply
    added to _SHELL_SINKS: that set's pairing hands the text to the Bash rules verbatim.
    """
    hs = _here_string(seg)
    if hs:
        return [hs], False
    for t, q in tokens(seg):
        if not q and t.lstrip("0123456789").startswith("<") and not t.startswith("<<"):
            return [], True
    if prev is None:
        return [], False
    v = verb(prev)
    if v in ("echo", "printf"):
        toks = tokens(prev)
        k = _verb_at(prev, 0)           # P1: see _verb_at
        words = [t for t, q in toks[k + 1:] if q or not t.startswith("-")]
        if v == "printf" and words and "%" in words[0]:
            words = words[1:]                  # the format string
        return ([" ".join(words)] if words else []), False
    return [], True


def _text_assignments(cmd: str) -> dict:
    """assignments(cmd) minus the two kinds this lane added -- a whole `$(...)` and a
    for-loop's word list. Both are BASH text: spliced into a cmd.exe or PowerShell payload
    they make it unparseable, so the carrier ASKED where it used to translate (MEASURED in
    this lane's history replay: 3 commands). Those variables stay unresolved there, as
    they always were."""
    return plain_assignments(assignments(cmd), [cmd])


def plain_assignments(assigns: dict, texts: list) -> dict:
    """`assigns` without a whole-`$(...)` value or a for-loop word list -- the table as
    it read before this lane. A genuine `A=(x y)` array stays: `${A[0]}` resolved before
    and must still (fuzz-guard's array-index mutator, which the first cut of this broke)."""
    loops = {m.group(1) for t in texts for m in _FOR_IN.finditer(t)}
    return {k: v for k, v in assigns.items() if k not in loops and not v.startswith("$(")}


def _windows_carrier(seg: str, cmd: str, prev=None) -> list:
    """The command text a `cmd /c` or a PowerShell host in `seg` runs, translated.
    `prev` is the segment before it, which may be feeding its stdin."""
    # The OUTER shell's redirects (`... 2>&1 | head`) are not part of the carried text:
    # joined into it, `2>&1` twice made PowerShell reject the payload, and an untranslated
    # payload ASKS (MEASURED in the friction replay). Only UNQUOTED redirect tokens are
    # dropped, so a `>` inside the quoted payload is still the payload's own.
    raw = tokens(seg)
    vi = _verb_at(seg)
    if vi is not None and _verb_name(raw[vi][0]) in ("cmd",) + _PS_EXES:
        raw = raw[vi:]                  # P1: from the verb's own word (see _verb_at)
    toks, i = [], 0
    while i < len(raw):
        t, q = raw[i]
        if not q and _drop_redirects([t]) == [] :
            core = t.lstrip("0123456789")
            i += 2 if core in _REDIR_WORD else 1
            continue
        toks.append(t)
        i += 1
    k = next((i for i, t in enumerate(toks)
              if _verb_name(t) in ("cmd",) + _PS_EXES), None)
    if k is None:
        return []
    toks = toks[k:]
    if _verb_name(toks[0]) == "cmd":
        inline = []
        for i, t in enumerate(toks[1:], 1):
            # /R is cmd's older synonym of /C (finding 3).
            if re.fullmatch(r"/{1,2}[cCkKrR]", t):
                # Every value of the text (FX-G6 / reviewer K C1, fuzz-guard): one carried
                # command per value of an unassigned `${V:+w}`, joined on a NUL no token holds.
                raw = chr(0).join(toks[i + 1:])
                inline = [cmd_to_sh(r_.split(chr(0)))
                          for r_ in resolve_variants(raw, _text_assignments(cmd))] if raw else []
                break
        if not _cmd_toks_read_stdin(toks):
            return inline
        # NO /c: cmd reads its PROGRAM FROM STDIN, one command per line (FX-G2 / reviewer E4,
        # MEASURED: `echo rd /s /q "<ref>" | cmd` and `cmd <<EOF` were ALLOWED, and Git Bash
        # runs both). Read exactly as a PowerShell host's stdin is (_ps_stdin_program): an
        # echo/printf or a here-string is translated; a file or any other producer is
        # UNREADABLE and reported; the heredoc form is cmd_heredoc_bodies' job. FX-G4 / H1:
        # `/k` runs its inline command AND THEN reads stdin, so both are returned.
        texts, unreadable = _ps_stdin_program(seg, prev)
        if unreadable:
            _UNTRANSLATED.append("a cmd reading its program from stdin, fed by something "
                                 "the guard cannot read")
            return inline
        return inline + [cmd_to_sh([ln]) for t_ in texts for ln in t_.split(chr(10)) if ln.strip()]
    kind, payload = _ps_host_payload(toks)
    if kind == "cmd":
        # C6: every value bash can hand the host (`-Command "${NOPE:+x}Remove-Item <ref>"`),
        # walked as one program -- a superset of each variant's commands, one translation.
        text = chr(10).join(dict.fromkeys(resolve_variants(payload, _text_assignments(cmd))))
    elif kind == "encoded":
        try:
            text = base64.b64decode(payload, validate=True).decode("utf-16-le")
        except (ValueError, UnicodeDecodeError):
            _UNTRANSLATED.append("an -EncodedCommand that does not decode")
            return []
    elif kind in ("stdin", "none"):
        # The program arrives on STDIN (finding 2): translate it when the command text
        # shows it, ask when something the guard cannot read is feeding it.
        texts, unreadable = _ps_stdin_program(seg, prev)
        # A heredoc body is carried separately (ps_heredoc_bodies), so it is not missing.
        heredoc = re.search(r"<<(?!<)", seg) is not None
        if unreadable or (kind == "stdin" and not texts and prev is None and not heredoc):
            _UNTRANSLATED.append("a PowerShell reading its program from stdin, fed by "
                                 "something the guard cannot read")
            return []
        if not texts:
            return []
        text = chr(10).join(texts)
    else:
        return []
    sh, unknown, why = powershell_to_sh(text)
    if sh is None:
        _UNTRANSLATED.append(why)
        return []
    _UNTRANSLATED.extend(unknown)
    return [sh]


def _inner_commands(cmd: str) -> list[str]:
    """Command strings hidden inside a wrapper, so the rules see them too.

    Carriers: a shell's `-c` argument, `eval`, `trap`, a shell reading stdin, and (HK-2)
    `cmd /c` and a PowerShell host's -Command/-EncodedCommand. Each takes TEXT and runs
    it as a command, so anything it carries is invisible to every rule that inspects
    segments.
    """
    out = []
    segs = segments(cmd)
    pipes = piped_in(cmd)
    if len(pipes) != len(segs):          # never guess the pairing short: assume piped
        pipes = [True] * len(segs)
    assigns_ = None
    dynamic = {}                         # F10: segment index -> its readings
    for n, seg in enumerate(segs):
        # F10: a command NAME from a variable. One this command assigns a bare name to is
        # spliced in first, so `R=eval; $R '<cmd>'` reaches the eval arm below (MEASURED:
        # allowed); one it cannot name contributes its speculative readings.
        if has_unresolved(_verb_token(seg) or ""):
            if assigns_ is None:
                assigns_ = assignments(cmd)
            seg = resolve_verb(seg, assigns_)
            readings = dynamic_program_readings(seg)
            if readings:
                dynamic[n] = readings
        if _verb_name(verb(seg)) in ("cmd",) + _PS_EXES:
            translated = _windows_carrier(seg, cmd, segs[n - 1] if n and pipes[n] else None)
            _WIN_DIALECT.update(translated)
            out.extend(translated)
        # `echo '<cmd>' | bash` and `printf '%s' '<cmd>' | sh`. The consumer must have
        # NO script operand (see _reads_stdin_program) and the producer must be an echo
        # or printf of a literal, so an ordinary `echo ... > file && bash script.sh`
        # cannot pair by accident.
        if n and _reads_stdin_program(seg):
            prev = segs[n - 1]
            if _verb_name(verb(prev)) in ("echo", "printf"):
                lit = [t for t, q in tokens(prev)[1:] if q and not t.startswith("-")]
                for piece in lit:
                    if piece.strip() and piece not in ("%s", "%s" + chr(92) + "n"):
                        out.append(piece)
                # FX-G4 / reviewer H7, MEASURED: `echo rm -rf "<game>" | bash` was ALLOWED
                # -- only QUOTED words were taken, and here the program is the unquoted
                # words. The shell runs what the producer PRINTS, so that is rebuilt
                # (echo: its words joined; printf: its format applied) -- and the producer's
                # words AS WRITTEN are added too, so a quoted path with a space stays one
                # operand. A printf format this cannot reproduce is the inability path.
                out.extend(_echo_printf_program(prev))
        # `bash <<< '<cmd>'` -- the here-string IS the program. A SEPARATE `if`, not an
        # arm of the chain below: this lived as an `elif` for one measurement and never
        # ran, because `bash` matches the `-c` arm first and that arm appends nothing when
        # there is no `-c`. A shell can be handed a program by `-c` AND on stdin in the
        # same command, so they were never alternatives to begin with.
        if _reads_stdin_program(seg):
            hs = _here_string(seg)
            if hs:
                out.append(hs)
        out.extend(_procsub_program(seg))
        v = verb(seg)
        toks = tokens(seg)
        if v in _SHELLS:
            for i, (t, quoted) in enumerate(toks):
                if not quoted and _DASH_C.match(t) and i + 1 < len(toks):
                    # RESOLVED before it is walked. `C='rm -rf "<root>"'; bash -c "$C"`
                    # appended the token `$C` verbatim, although `assignments()` had
                    # already computed C from this very command and the delete rules
                    # were using it. Same-command assignment is exactly what that
                    # machinery is for; the carrier walk was the one consumer not using
                    # it, so a two-statement command any script writes lost the block.
                    # EVERY value (FX-G6 / reviewer K C1, found by fuzz-guard): one resolve()
                    # judged `bash -c 'rm -rf "${NOPE:+zz}<ref>"'` as the alternate only.
                    out.extend(resolve_variants(toks[i + 1][0], assignments(cmd)))
                    break
        elif v == "eval":
            # `eval` concatenates its arguments and runs the result.
            #
            # Sliced from AFTER THE `eval` TOKEN, never from toks[1:]. `eval` is not
            # necessarily token 0 -- a wrapper can precede it -- and `time eval <cmd>`
            # then produced the string "eval <cmd>", whose own verb is `eval`, so no
            # delete rule fired on that either. Matching on the NAME rather than the
            # token also picks up an absolute spelling, which verb() now normalises.
            k = next((i for i, (t, _) in enumerate(toks)
                      if _verb_name(t) == "eval"), 0)
            parts = [t for t, _ in toks[k + 1:] if not t.startswith("-")]
            if parts:
                out.extend(resolve_variants(" ".join(parts), assignments(cmd)))   # FX-G6 / K C1
        elif v == "trap":
            # `trap <cmd> <SIGNAL...>` runs its first operand as a command when the
            # signal fires. That operand is normally single-quoted, which is exactly
            # why nothing saw it: quoted text is data everywhere else in this file.
            k = next((i for i, (t, _) in enumerate(toks)
                      if _verb_name(t) == "trap"), 0)
            rest = [t for t, _ in toks[k + 1:] if not t.startswith("-")]
            if rest:
                out.append(rest[0])
    out.extend(_reading_variants(cmd, segs, dynamic))
    return out


def _reading_variants(cmd: str, segs: list, dynamic: dict) -> list:
    """F10: each speculative reading IN PLACE of the segment it reads, so the command around
    it -- a `cd <abs>` before it, the assignments, the pipe feeding it -- still applies.
    Standalone, a reading is a carried command whose directory is unknown once anything
    changes directory, so `cd <game> && $R rm -rf extensions` lost the deny its plain
    spelling gets. Variant i puts EVERY dynamic segment's i-th reading (its last, when it has
    fewer) in place at once: one variant per reading, never one per COMBINATION, which on a
    script with several `"$PY" ...` lines would multiply. A segment that cannot be located in
    the text (unwrapping changed it) contributes its readings standalone instead. EVERY
    segment is located in order, so a dynamic one is never matched inside an earlier
    segment's quoted text (`echo "$R x"; $R x`)."""
    if not dynamic:
        return []
    spans, pos, loose = [], 0, []
    for n, written in enumerate(segs):
        i = cmd.find(written, pos)
        if i >= 0:
            pos = i + len(written)
        if n not in dynamic:
            continue
        if i < 0:
            loose.extend(dynamic[n])
            continue
        spans.append((i, i + len(written), dynamic[n]))
    out = []
    for k in range(max((len(r) for _a, _b, r in spans), default=0)):
        parts, last = [], 0
        for a, b, readings in spans:
            parts.append(cmd[last:a])
            parts.append(readings[min(k, len(readings) - 1)])
            last = b
        parts.append(cmd[last:])
        out.append("".join(parts))
    return out + loose


#: How many times a carrier may nest before the walk stops. `bash -c` inside
#: `bash -c` inside a substitution is three levels, and each is a real construct
#: someone can type; beyond that the shape is pathological rather than plausible.
#: Bounded because this runs on the BLOCKING PreToolUse path -- an unbounded walk
#: over an adversarial string is a hang, and a hang here stops the session.
_MAX_CARRIER_DEPTH = 4

#: A hard ceiling on how many command strings the walk will produce, whatever their
#: shape. _MAX_CARRIER_DEPTH does NOT bound this on its own: substitutions() descends
#: into nested `$( )` inside a single pass, so one call already flattens the whole
#: tree. MEASURED with unique text at every level (so dedup cannot collapse it):
#: a 128 KB command yielded 9,841 carried commands and 4.1 s in facts(), on the
#: BLOCKING PreToolUse path.
#:
#: MEASURED over all 13,503 real historical commands: max carried = 25, p99 = 5,
#: p50 = 1, and ZERO commands exceed 50. So 250 is 10x the observed maximum -- it is
#: unreachable by ordinary work, and it bounds the adversarial case well under a second.
#: (An earlier draft of this comment guessed "~180x" and was wrong; the census is the
#: only reason the number in it is now true.)
_MAX_CARRIED = 250


def carried_commands(body: str, extra: list, dialects: dict | None = None) -> tuple:
    """Every command string reachable from `body`, following carriers.

    _inner_commands was applied EXACTLY ONCE, to the top level, so a command one
    level further in was invisible: `bash -c 'sh -c "<cmd>"'` reached no rule. It
    also took only the FIRST `-c` per segment.

    Deduplicated, because two carriers can yield the same text and the rules below
    are pure functions of it -- re-running them buys nothing and costs latency on
    the blocking path.

    `dialects` (text -> "bash"/"windows", seeded with `body`) is filled for every text:
    a PowerShell/cmd translation is Windows-spelled, a shell carrier's text is Bash, and
    a substitution keeps its parent's dialect (LINUX GAP).
    """
    d = {} if dialects is None else dialects
    d.setdefault(body, "bash")
    seen = {body}
    out = [body]
    frontier = [body]
    truncated = False
    for e in extra:
        d.setdefault(e, WINDOWS_DIALECT if e in _WIN_DIALECT else "bash")
        if e not in seen:
            if len(out) >= _MAX_CARRIED:
                return out, True
            seen.add(e)
            out.append(e)
            frontier.append(e)
    # One discovery-only pass distinguishes a leaf at the boundary from a
    # reachable command that would otherwise disappear beyond the depth cap.
    for depth in range(_MAX_CARRIER_DEPTH + 1):
        nxt = []
        for c in frontier:
            inners = _inner_commands(c)
            subs = substitutions(c)
            for i_, kind in ([(x, WINDOWS_DIALECT if x in _WIN_DIALECT else "bash") for x in inners]
                             + [(x, d.get(c, "bash")) for x in subs]):
                for inner in _carrier_parts(i_):
                    if inner:
                        d.setdefault(inner, kind)
                    if not inner or inner in seen:
                        continue
                    if depth == _MAX_CARRIER_DEPTH:
                        return out, True
                    seen.add(inner)
                    nxt.append(inner)
                    if len(out) + len(nxt) >= _MAX_CARRIED:
                        # STOP, and SAY SO. Dropping the rest and returning a verdict
                        # would be a step that narrows its data and reports success --
                        # the shape behind every tool defect found in this workspace.
                        out.extend(nxt)
                        return out[:_MAX_CARRIED], True
        if not nxt:
            break
        out.extend(nxt)
        frontier = nxt
    return out, truncated


def _ipc_value(key: str, v) -> str:
    """One field of the `key<TAB>value` stream, with no way to forge another field.

    protect-bash.sh splits the stream at the FIRST sentinel, and `on()` matches
    "<NL>key<TAB>1<NL>" ANYWHERE in what precedes it. So any value carrying a newline
    can invent a fact that no rule ever computed -- and two of the values are not
    booleans this file produced: `timeout` and `run_in_background` are passed straight
    through from the caller's payload.

    Not reachable through the documented schema, where both are a number and a bool.
    This is defence in depth, on exactly the reasoning that already justifies `_as_ms`
    accepting a value "whatever shape it arrived in": a schema describes intent, it
    does not guarantee bytes.
    """
    if isinstance(v, bool):
        return "1" if v else "0"
    if key == "timeout":
        return str(int(_as_ms(v)))          # a number, always
    # Anything else stays on ONE field of ONE line.
    return str(v).replace(chr(13), " ").replace(chr(10), " ").replace(chr(9), " ")


def _as_ms(v) -> float:
    """A timeout in milliseconds, whatever shape it arrived in. Unparseable -> 0, which
    keeps the rule off rather than firing on nonsense."""
    if isinstance(v, bool) or v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return 0.0


#: A Claude Code settings file, any level (FX-G2 settings rule, user decision 2026-10-05).
_AGENT_SETTINGS = re.compile(r"(^|/)\.claude/settings[^/]*\.json$")
#: H1 (v4.0 release review): a HOST SESSION TRANSCRIPT. The save bridge reads a typed human
#: save approval from it, so an agent write there could forge one (MEASURED: a one-line jsonl
#: produced a grant matching a 3-save delete). Claude Code: ~/.claude/projects/**.jsonl;
#: Codex: ~/.codex/sessions/**.jsonl. Memory notes (.md) beside them are not matched.
_HOST_TRANSCRIPT = re.compile(r"(^|/)\.claude/projects/.*\.jsonl$|(^|/)\.codex/sessions/.*\.jsonl$")
#: M1: the transcript TREES, for a copy/move whose destination is a directory there (`cp x.jsonl
#: ~/.claude/projects/p/`, `cp -t <dir> x.jsonl` were ALLOWED -- the file lands as <dir>/x.jsonl).
_HOST_TRANSCRIPT_TREE = re.compile(r"(^|/)\.claude/projects(/|$)|(^|/)\.codex/sessions(/|$)")
#: ...and the same trees RELOCATED by the host's own variables: Claude Code keeps transcripts in
#: $CLAUDE_CONFIG_DIR/projects, Codex in $CODEX_HOME/sessions. Matched as written (`$CODEX_HOME/
#: sessions/...`) and, when the hook's environment holds the variable, by its value.
_RELOCATED_TREE = re.compile(r"\$\{?(CLAUDE_CONFIG_DIR\}?/projects|CODEX_HOME\}?/sessions)(/|$)")


def _transcript_trees() -> list:
    out = []
    for var, sub in (("CLAUDE_CONFIG_DIR", "/projects"), ("CODEX_HOME", "/sessions")):
        v = os.environ.get(var)
        if v:
            out.append(norm(v).rstrip("/") + sub)
    return out


def _in_transcript_tree(path: str, raw: str) -> bool:
    n = norm(path or raw)
    return bool(_HOST_TRANSCRIPT_TREE.search(n) or _RELOCATED_TREE.search(raw or "")
                or any(n == t or under(n, t) for t in _transcript_trees()))


def _is_transcript(path: str, raw: str) -> bool:
    n = norm(path or raw)
    return bool(_HOST_TRANSCRIPT.search(n)) or (n.endswith(".jsonl") and _in_transcript_tree(path, raw))


_COPY_RECURSIVE = re.compile(r"^-[A-Za-z]*[rRa][A-Za-z]*$|^--(recursive|archive)$")


def _copy_lands_a_transcript(seg: str, dests: list, assigns: dict) -> bool:
    """M1: does this cp/mv/install/rsync put a `.jsonl` INTO a transcript tree under the SOURCE's
    name (a directory destination)? Fail closed: a source that is unresolved, a pattern, or a
    recursive copy (it may carry a .jsonl inside) counts too."""
    if verb(seg) not in COPY_VERBS or verb(seg) == "tee":
        return False
    if not any(_in_transcript_tree(pp, rr) for pp, _u, rr in dests):
        return False
    toks = tokens_of(seg)
    if verb(seg) == "rsync" or any(_COPY_RECURSIVE.match(t) for t in toks):
        return True
    dest_raw = set(copy_dests(seg))
    sources = [o for o in _operands(seg) if o not in dest_raw]
    if not sources:
        return True
    for o in sources:
        for r in resolve_all(o, assigns):
            if has_unresolved(r) or any(c in r for c in _OPERAND_PATTERN_CHARS) or                     norm(r).rstrip("/").endswith(".jsonl"):
                return True
    return False


#: H1 follow-up (probed 2026-10-25 from the Skyrim toolkit's release review): an INLINE interpreter
#: program writing a transcript (`python -c "open('<T>','a')"`, `node -e "fs.appendFileSync('<T>')"`)
#: was ALLOWED while `echo x >> <T>` was refused. Each pattern captures the WRITE call's own target
#: literal (`p`, also as `open(file=...)`) and, where the call takes one, its mode (`m`, which must hold w/a/x/+). Narrow on
#: purpose -- MEASURED over 44,002 historical commands, every one of the 28 interpreter programs that
#: named a transcript .jsonl READ it, several while writing a scratch file, so a mention, or a mention
#: beside an unrelated write, would refuse ordinary transcript analysis. RESIDUAL: a target held in a
#: program variable or argv, perl/ruby, and a script file are not seen (F290's opaque-program class).
_INLINE_WRITE_TARGETS = (
    re.compile(r"""\bopen\(\s*(?:file\s*=\s*)?[rRbBuU]{0,2}(['"])(?P<p>[^'"]+)\1\s*,\s*(?:mode\s*=\s*)?[rRbBuU]{0,2}(['"])(?P<m>[^'"]*)\3"""),
    re.compile(r"""\bPath\(\s*[rRbBuU]{0,2}(['"])(?P<p>[^'"]+)\1\s*\)\s*\.(?:write_text|write_bytes|open\(\s*(?:mode\s*=\s*)?(['"])(?P<m>[^'"]*)\3)"""),
    re.compile(r"""\b(?:append|write)File(?:Sync)?\(\s*(['"`])(?P<p>[^'"`]+)\1"""),
    re.compile(r"""\bcreateWriteStream\(\s*(['"`])(?P<p>[^'"`]+)\1"""),
    re.compile(r"""\b(?:shutil\.(?:copy\w*|move)|os\.(?:replace|rename))\(\s*[^,()]+,\s*[rR]?(['"])(?P<p>[^'"]+)\1"""),
)
_NODES = ("node", "nodejs")


def _inline_program_writes_transcript(seg: str, cwd: str, assigns: dict) -> bool:
    """Does this python -c / node -e program's own write call target a host transcript?"""
    name = _verb_name(verb(seg))
    if name in _PYTHONS and name != "uv":
        texts = [_python_literal_source(v) for v in [seg] + list(resolve_variants(seg, assigns))]
    elif name in _NODES and any(t in ("-e", "--eval", "-p", "--print") or t.startswith(("--eval=", "--print="))
                                for t in tokens_of(seg)):
        texts = [seg] + list(resolve_variants(seg, assigns))
    else:
        return False
    return any(_program_writes_transcript(text, cwd) for text in texts)


def _program_writes_transcript(text: str, cwd: str) -> bool:
    """Does this interpreter program text hold a WRITE call (_INLINE_WRITE_TARGETS) whose own
    target is a host transcript?"""
    for pattern in _INLINE_WRITE_TARGETS:
        for m in pattern.finditer(text or ""):
            mode = m.groupdict().get("m")
            if mode is not None and not any(ch in mode for ch in "wax+"):
                continue
            raw = m.group("p").replace(chr(92) * 2, "/").replace(chr(92), "/")
            path = join_cwd(cwd, raw) if cwd else raw
            if _is_transcript(path or raw, raw):
                return True
    return False


#: Python options whose VALUE is the next word (`-W ignore`, `-X utf8`); node's are below.
_PY_VALUE_OPTS = ("-W", "-X")
_NODE_VALUE_OPTS = ("-r", "--require", "--import", "--loader", "--experimental-loader")


def _reads_program_from_stdin(seg: str) -> bool:
    """Is this python/node invocation's PROGRAM its stdin? Yes for `python`, `python -`,
    `py -3.12 -`, `node`, `node -`; no once a -c/-m/-e/-p program or a script name appears
    (then stdin is that program's DATA). Stops at the first redirection or operator word."""
    toks = [t for t, _q in tokens(seg)]
    k = _verb_at(seg)
    if k is None or k >= len(toks):
        return False
    name = _verb_name(toks[k])
    if name in _PYTHONS and name != "uv":
        py = True
    elif name in _NODES:
        py = False
    else:
        return False
    skip = False
    for t in toks[k + 1:]:
        if skip:
            skip = False
            continue
        if not t or t[0] in "<>|&;" or re.match(r"\d+[<>]", t):
            break
        if t == "-":
            return True
        if t == "--":
            continue
        if py:
            if t in _PY_VALUE_OPTS:
                skip = True
                continue
            if re.fullmatch(r"-[A-Za-z]+", t) and not t.startswith(("-W", "-X")) and ("c" in t or "m" in t):
                return False
        else:
            if t in ("-e", "--eval", "-p", "--print") or t.startswith(("--eval=", "--print=")):
                return False
            if t in _NODE_VALUE_OPTS:
                skip = True
                continue
        if t.startswith("-"):
            continue
        return False
    return True


def _heredoc_program_writes_transcript(spliced: str, cwd: str) -> bool:
    """4.0.1 batch: the _INLINE_WRITE_TARGETS scan, over a heredoc body that an interpreter
    reads as its PROGRAM (`python - <<'EOF'` / `open('<T>','a')` / `EOF`, `node <<EOF`, or a
    body piped into one). The body was stripped as data, so only -c/-e programs were seen.
    Same narrow write-call patterns: a body that only READS a transcript stays allowed.
    Judged PER HEREDOC (review of 8957579): only a body whose OWN opening line feeds the
    interpreter -- directly, or through a pipe on that line (`cat <<EOF | python -`) -- is a
    program. heredoc_bodies' whole-command pipe rule made a `cat > notes.md` body a program
    whenever any later `| python` appeared. RESIDUAL: `{ cat <<EOF ... } | python` is not seen."""
    def sink(sg):
        return _reads_program_from_stdin(_unwrap(sg))
    for opener, _t, _e, body in _heredoc_walk(spliced)[0]:
        if _opener_feeds(opener, sink) or _flows_to(opener, sink):
            if _program_writes_transcript(chr(10).join(body), cwd):
                return True
    return False


# ------------------------------------------------------------- link creation (F331)
#: F331 follow-up (release review of 4.0.1): a link CREATED into a protected root lets every
#: later write through it land there, and the Bash-channel write rules compare paths
#: lexically. MEASURED: 72 of 72 link-creating commands aimed at reference/, the game, the
#: profile and the saves were ALLOWED. The link's TARGET is judged here; the link's own PATH
#: is a write where it is placed (modify_targets), as `ln` already was.
_LINK_KEYS = ("reference", "game", "profile", "saves")
_MKLINK_SWITCH = re.compile(r"^/{1,2}([dhjDHJ])$")
#: A Python program's link call, the TARGET as its first argument. Only these spellings:
#: a bare `symlink(` / `link(` after `from os import ...` is not seen (residual, F331).
_PY_LINK_CALLS = (
    (re.compile(r"\bos\.symlink\(\s*(?:src\s*=\s*)?"), "symbolic"),
    (re.compile(r"\bos\.link\(\s*(?:src\s*=\s*)?"), "hard"),
    (re.compile(r"\b_winapi\.CreateJunction\(\s*"), "junction"),
    (re.compile(r"\.symlink_to\(\s*(?:target\s*=\s*)?"), "symbolic"),
    (re.compile(r"\.hardlink_to\(\s*(?:target\s*=\s*)?"), "hard"),
)
_PWD_REF = re.compile(r"\$\{PWD\}|\$PWD(?![A-Za-z0-9_])")
_PY_STR = re.compile(r"""[rRbBuU]{0,2}(['"])(?P<p>[^'"]*)\1""")
#: The link literal of `Path(<lit>).symlink_to(...)`, ending right where the call begins.
_PY_LINK_RECEIVER = re.compile(r"""Path\(\s*[rRbBuU]{0,2}(['"])(?P<p>[^'"]*)\1\s*\)\s*$""")


def _ln_link_operands(seg: str) -> tuple:
    """`ln`'s (targets, link-or-directory or None, symbolic?). GNU forms: `ln T L`, `ln T`
    (a link in the cwd), `ln T... DIR`, `ln -t DIR T...` / `--target-directory[=]DIR`."""
    vi = _verb_word(seg)[1]
    if vi is None:
        return [], None, False
    ops, sym, tdir, after_dd = [], False, None, False
    toks = tokens(seg)[vi + 1:]
    i = 0
    while i < len(toks):
        t, q = toks[i]
        i += 1
        if not after_dd and t in ("<", ">", ">>", ">|", "&>"):
            i += 1
            continue
        if not q and (t.startswith(("<", ">")) or re.match(r"^\d+>", t)):
            continue
        if after_dd or not t.startswith("-") or t == "-":
            ops.append(t)
            continue
        if t == "--":
            after_dd = True
        elif t.startswith("--"):
            name, eq, val = t.partition("=")
            if name == "--symbolic":
                sym = True
            elif name in ("--target-directory", "--suffix"):
                if not eq:
                    val = toks[i][0] if i < len(toks) else ""
                    i += 1
                if name == "--target-directory":
                    tdir = val
        else:
            flags = t[1:]
            for k, ch in enumerate(flags):
                if ch == "s":
                    sym = True
                elif ch in "tS":
                    val = flags[k + 1:]
                    if not val:
                        val = toks[i][0] if i < len(toks) else ""
                        i += 1
                    if ch == "t":
                        tdir = val
                    break
    if tdir is not None:
        return ops, tdir, sym
    if len(ops) == 1:
        return ops, None, sym
    return ops[:-1], (ops[-1] if ops else None), sym


def link_creations(seg: str) -> list:
    """The links this SHELL segment creates: (target, link-or-None, kind), kind in
    symbolic/hard/junction. A link verb whose target cannot be read is (None, None, kind)."""
    v = _verb_name(verb(seg)).lower()
    if v == "ln":
        targets, at, sym = _ln_link_operands(seg)
        return [(t, at, "symbolic" if sym else "hard") for t in targets]
    if v == "mklink":
        kind, ops = "symbolic", []
        for t in _operands(seg, include_flags=True):
            m = _MKLINK_SWITCH.match(t)
            if m:
                kind = {"d": "symbolic", "h": "hard", "j": "junction"}[m.group(1).lower()]
            else:
                ops.append(t)
        if not ops:
            return []
        return [(ops[1], ops[0], kind)] if len(ops) == 2 else [(None, None, kind)]
    if v == "fsutil":
        ops = _operands(seg)
        if [o.lower() for o in ops[:2]] != ["hardlink", "create"]:
            return []
        return [(ops[3], ops[2], "hard")] if len(ops) >= 4 else [(None, None, "hard")]
    return []


def _program_links(text: str) -> list:
    """The links a Python program text creates (_PY_LINK_CALLS): (target, link, kind), the
    target None when its first argument is not a string literal."""
    out = []
    for pattern, kind in _PY_LINK_CALLS:
        for m in pattern.finditer(text or ""):
            lit = _PY_STR.match(text, m.end())
            target = lit.group("p") if lit else None
            link = None
            if kind != "hard" and lit:
                rest = text[lit.end():]
                if m.group(0).startswith("."):
                    r = _PY_LINK_RECEIVER.search(text[:m.start()])
                    link = r.group("p") if r else None
                else:
                    second = re.match(r"\s*,\s*(?:dst\s*=\s*)?", rest)
                    lit2 = _PY_STR.match(rest, second.end()) if second else None
                    link = lit2.group("p") if lit2 else None
            fold = (lambda s: s.replace(chr(92) * 2, "/").replace(chr(92), "/") if s else s)
            out.append((fold(target), fold(link), kind))
    return out


def _segment_links(seg: str, assigns: dict) -> list:
    """Every link a segment creates: a shell link verb, or a python -c program's link call
    (read in every resolved variant of the segment, like the transcript check)."""
    out = link_creations(seg)
    name = _verb_name(verb(seg))
    if name in _PYTHONS and name != "uv":
        for v in [seg] + list(resolve_variants(seg, assigns)):
            for row in _program_links(_python_literal_source(v)):
                if row not in out:
                    out.append(row)
    return out


def _judge_link(target, link, kind, cwd: str, assigns: dict, roots: dict) -> str:
    """'protected' when the TARGET is inside, or an ancestor of, a protected root; 'unresolved'
    when it cannot be read; '' otherwise. A RELATIVE symlink target is read from the link's own
    folder as well as from the cwd -- the OS resolves it from the link. A junction's too: where
    a relative junction target is anchored was not measured, so it gets both readings. Only a
    HARDLINK's target -- an existing file the call opens -- is read from the cwd alone."""
    if target is None:
        return "unresolved"
    if cwd and "PWD" not in assigns:
        # F331 review: `$PWD` / `${PWD}` IS the segment's folder, which is known here.
        target = _PWD_REF.sub(lambda _m: cwd, target)
        link = _PWD_REF.sub(lambda _m: cwd, link) if link else link
    prot = [roots[k] for k in _LINK_KEYS if roots.get(k)]
    verdict = ""
    for rv in resolve_variants(target, assigns):
        r = subst_root_var(rv, roots)
        if has_unresolved(r):
            if any(norm(p) in norm(r) for p in prot) or any(k in root_vars_named(r) for k in _LINK_KEYS):
                return "protected"
            verdict = "unresolved"
            continue
        cands = [join_cwd(cwd, r)]
        if kind != "hard" and not is_abs(r) and link:
            for lv in resolve_variants(link, assigns):
                lp = subst_root_var(lv, roots)
                if has_unresolved(lp):
                    continue
                lp = join_cwd(cwd, lp)
                if lp:
                    cands += [join_cwd(lp, r), join_cwd(posixpath.dirname(norm(lp)), r)]
        cands = [c for c in cands if c]
        if not cands:
            verdict = "unresolved"
            continue
        if any(under(c, p) or contains_root(c, p) for c in cands for p in prot):
            return "protected"
    return verdict


def _for_headers(cmd: str) -> list:
    """For each segment segments(cmd) returns, in order: is it a `for NAME in WORDS` header?
    segments() strips the `for` and leaves `NAME in WORDS`, so a loop variable named like a
    link verb read as one -- MEASURED in the F331 replay: `for ln in 3124 3253` from the game
    folder was a false hard deny. Judged by POSITION, never by text (review of 3e4b4fd: a real
    `ln in <ref> .` spelled like an earlier header was skipped too). Read only by the link
    rule; every other rule's reading of the header is unchanged."""
    out = []
    for piece in segments(cmd, raw=True):
        words = piece.replace("(", " ").replace("{", " ").split()
        while words and words[0] in RESERVED and words[0] != "for":
            words = words[1:]
        out.append(bool(words) and words[0] == "for")
    return out


def _heredoc_program_links(spliced: str) -> list:
    """The _program_links of each heredoc body a Python interpreter reads as its PROGRAM
    (the same per-heredoc opener rule as _heredoc_program_writes_transcript)."""
    def sink(sg):
        return _reads_program_from_stdin(_unwrap(sg)) and _verb_name(verb(_unwrap(sg))) in _PYTHONS
    out = []
    for opener, _t, _e, body in _heredoc_walk(spliced)[0]:
        if _opener_feeds(opener, sink) or _flows_to(opener, sink):
            out += _program_links(chr(10).join(body))
    return out


#: I1: a variable that makes a shell run code its `-c` text never shows (startup file, exported
#: function, xtrace prompt, zsh's dot-dir), named ANYWHERE in the command -- a prefix, `env`,
#: `export`/`declare`/a plain assignment, `printf -v`, PowerShell `$env:` (whose translation
#: drops the assignment, so the raw PowerShell text is searched too). Case-sensitive for the
#: shell text; the PowerShell form is case-insensitive, as Windows variables are.
_SHELL_STARTUP_NAMES = r"BASH_ENV|ENV|SHELLOPTS|BASHOPTS|PS4|PROMPT_COMMAND|ZDOTDIR|BASH_FUNC_[^\s=]*"
_SHELL_STARTUP_SET = re.compile(r"(?<![A-Za-z0-9_])(?:" + _SHELL_STARTUP_NAMES + r")(?![A-Za-z0-9_])")
_PS_STARTUP_SET = re.compile(r"\$env:(?:" + _SHELL_STARTUP_NAMES + r")(?![A-Za-z0-9_])|"
                             r"(?<![A-Za-z0-9_$:])(?:BASH_ENV|SHELLOPTS|BASHOPTS|PS4|PROMPT_COMMAND|"
                             r"ZDOTDIR|BASH_FUNC_[^\s=]*)(?![A-Za-z0-9_])|[\"']ENV[\"']", re.IGNORECASE)
_SHELL_STARTUP_ENV = [False]


def _carries_program_text(seg: str) -> bool:
    """K1: is this segment a CARRIER whose program arrives as TEXT in the command (`bash -c`,
    `cmd /c`, `eval`, a PowerShell host's -Command/-EncodedCommand)? That text is walked as its
    own segments, each judged against SAVE_MODELLED_VERBS, so the carrier verb itself hides
    nothing. A script file (`bash x.sh`, `pwsh -File x.ps1`) or a program on stdin is not text
    held here, and stays a blocker."""
    v = verb(seg)
    toks = tokens_of(seg)
    k = _verb_at(seg)                   # P1: see _verb_at
    if k is None:
        return False
    rest = toks[k + 1:]
    # I1 (v4.0 release review): a carrier is transparent only when the TEXT is all it runs.
    # A shell also runs STARTUP CODE that the text never shows -- MEASURED, each lifted the
    # profile ask once one save qualified: `BASH_ENV=<f> bash -c '...'`, `env BASH_ENV=<f> bash
    # -c true`, `bash --rcfile <f> -i -c true`, `bash -lc true`, PowerShell `$env:BASH_ENV=...;
    # bash -c 'true'`. So: no startup-steering variable anywhere in the command, and bash/sh/
    # dash/ksh with exactly a bare `-c` (zsh reads .zshenv from $ZDOTDIR/$HOME on EVERY run);
    # cmd only with /d (AutoRun off); a PowerShell host only with -NoProfile.
    if _SHELL_STARTUP_ENV[0]:
        return False
    if v == "cmd":
        switches = [t for t in rest[:next((i for i, t in enumerate(rest) if _CMD_RUN_SWITCH.fullmatch(t)
                                           or _CMD_KEEP_SWITCH.fullmatch(t)), len(rest)) + 1]]
        return (any(_CMD_RUN_SWITCH.fullmatch(t) or _CMD_KEEP_SWITCH.fullmatch(t) for t in rest)
                and any(re.fullmatch(r"/{1,2}[dD]", t) for t in switches))
    if v in _SHELLS:
        return v != "zsh" and rest[:1] == ["-c"] and len(rest) > 1
    if v == "eval":
        return bool(rest)
    if v in _PS_EXES:
        # Only a switch BEFORE the program text counts (`-Command x -NoProfile` is text).
        head = rest[:next((i for i, t in enumerate(rest) if re.fullmatch(
            r"[-/](c(o(m(m(a(n(d)?)?)?)?)?)?|ec|e(n(c(o(d(e(d(c(o(m(m(a(n(d)?)?)?)?)?)?)?)?)?)?)?)?)?|cwa|"
            r"commandwitha(r(g(s)?)?)?)", t, re.I)), len(rest))]
        no_profile = any(t[:1] in ("-", "/") and len(t) >= 4 and "noprofile".startswith(t[1:].lower())
                         for t in head)
        return no_profile and _ps_host_payload(toks[k:])[0] in ("cmd", "encoded")
    return False


def facts(payload: dict, roots: dict, *, evidence: list | None = None, execution_context: bool = False,
          protected_save_folders=()) -> dict:
    inp = payload.get("tool_input") or {}
    cmd = inp.get("command") or ""
    timeout = inp.get("timeout", 0)
    background = inp.get("run_in_background", False)
    # Adapter-owned context: Codex exec_command yields a running session instead
    # of Claude Bash's foreground timeout. Native Codex hooks can relabel it Bash
    # and omit yield_time_ms, so absence of Claude's flag is not foreground proof.
    # Command text and tool_input cannot opt into this host scheduling contract.
    codex_async_shell = payload.get("x4_execution_host") == "codex"
    del _UNTRANSLATED[:]
    _WIN_DIALECT.clear()
    _BRACE_WORDS.clear()                # C1: per call, like the two above
    _CD_LANES_TRUNCATED[0] = False      # C7: per call
    _OPERAND_EXPANSION_TRUNCATED[0] = False
    _ps_spent[0] = 0.0                  # the translation budget is per hook call

    # THE POWERSHELL TOOL (AUDIT-2026-09-24 HK-1). It had no PreToolUse guard at all, so
    # every rule below was one tool choice away from not existing. Its command is
    # translated by PowerShell's own parser into the equivalent POSIX-shell command, and
    # EVERYTHING after this point is the Bash analysis, unchanged -- one rule set, two
    # front-ends. Untranslatable (does not parse, no PowerShell to parse it) is reported
    # as `powershell_error`, and main() turns that into a refusal, never an allow.
    from_powershell = payload.get("tool_name") == "PowerShell"
    if from_powershell:
        sh, unknown, why = powershell_to_sh(cmd)
        if sh is None:
            return {"command": cmd, "powershell_error": why or "not translated"}
        _UNTRANSLATED.extend(unknown)
        cmd = sh

    # PARSE THE COMMANDS, NOT THE PROSE. Heredoc bodies are data (a body line reading
    # `rm -rf <game>` is text being written, and used to hard-deny), and a `#` comment is
    # not a command at all -- while an apostrophe inside one blinded every rule after it.
    # Line continuations FIRST: bash splices them before it parses anything, and
    # treating one as a separator cost every verb-keyed rule its operand (C1).
    spliced = join_continuations(cmd)
    body = strip_comments(strip_heredocs(spliced))
    # FX-G4 / reviewer H6: git config from the ENVIRONMENT (see _git_destructive).
    _GIT_ENV_CONFIG[0] = bool(_GIT_CONFIG_ENV_SET.search(body)
                              or _GIT_CONFIG_ENV_SET.search(inp.get("command") or "")
                              or _GIT_HOME_SET.search(body))
    # I1: a variable that steers a shell's startup code, named anywhere (see _carries_program_text).
    _SHELL_STARTUP_ENV[0] = bool(_SHELL_STARTUP_SET.search(body) or _SHELL_STARTUP_SET.search(cmd)
                                 or from_powershell and _PS_STARTUP_SET.search(inp.get("command") or ""))
    # Heredoc bodies come from the RAW command: strip_heredocs has already removed
    # them from `body`, and only the ones opened by a shell are commands at all.
    extra = [strip_comments(h) for h in heredoc_bodies(spliced)]
    # ...and an EXPANDING body (unquoted delimiter) runs its `$(...)` / backticks (FX-P2).
    extra += heredoc_substitutions(spliced)
    # ...and a heredoc fed to a cmd.exe reading stdin is a cmd PROGRAM (reviewer E4).
    extra += cmd_heredoc_bodies(spliced)
    # A heredoc fed to a PowerShell host is a PowerShell PROGRAM (finding 2): translated,
    # or reported untranslated -- never dropped as file payload.
    for h in ps_heredoc_bodies(spliced):
        sh_, unknown_, why_ = powershell_to_sh(h)
        if sh_ is None:
            _UNTRANSLATED.append(why_)
        else:
            _UNTRANSLATED.extend(unknown_)
            _WIN_DIALECT.add(sh_)
            extra.append(sh_)
    # Which dialect spelled each command's paths (LINUX GAP): see carried_commands.
    dialects = {body: WINDOWS_DIALECT if from_powershell else "bash"}
    all_cmds, carriers_truncated = carried_commands(body, extra, dialects)
    # ...and each one with its separator-holding substitutions collapsed (FX-G6 / reviewer
    # K I4): `A=$(echo a; echo b) rm -rf <game>` -- see _spanning_view. Inserted after the
    # top level, so the last command (and the `cwd` fact read from it) is unchanged; the
    # top level's own view is seeded with the session directory exactly as the top level is.
    top_view = _spanning_view(body)
    views = []
    for c_ in all_cmds:
        v_ = _spanning_view(c_)
        if v_ != c_ and v_ not in all_cmds and v_ not in views:
            views.append(v_)
            dialects.setdefault(v_, dialects.get(c_, "bash"))
    carried = list(all_cmds)            # the assignment tables read the commands as written
    all_cmds[1:1] = views
    # FX-G6 / reviewer K I2: a `git config` that may WRITE, anywhere in the command (any
    # carried command too), may switch clean.requireForce off -- see _git_config_writes.
    _GIT_ENV_CONFIG[0] = _GIT_ENV_CONFIG[0] or any(
        _git_config_writes(s_) for c_ in all_cmds for s_ in segments(c_))
    assigns = assignments(body)
    # Assignments made INSIDE a carrier (`bash -c 'D=<root>; rm -rf "$D"'`, a heredoc fed
    # to a shell, eval) are the carried command's own; the top level's win on a clash.
    # MEASURED by fuzz-guard's new seeds (v3.3.0 release review): a for-loop or a
    # realpath assignment inside any carrier reached no rule.
    for c_ in carried[1:]:
        for k_, v_ in assignments(c_).items():
            assigns.setdefault(k_, v_)
    # The table as it was before this lane (no `$(...)` values, no loop word lists), for
    # the targets whose rules were deliberately NOT widened -- see prep(expand=False).
    plain_assigns = plain_assignments(assigns, carried)
    ncmd = norm(cmd)

    # Every operand is classified WHERE IT RUNS. `cwd_track` was the missing link: the
    # directory was already computed and only the search rules ever consumed it, so a
    # path named relative to a `cd` reached no other rule at all.
    # resolve_verb runs HERE, once per segment, with the assignment table already
    # computed above: a command name arriving through a variable (`RM=rm; $RM -rf ...`)
    # otherwise reaches no verb-keyed rule at all, hard blocks included.
    seg_cwd, seg_prev, unseeded, physical_dirs, seg_dialect = [], [], [], [], []
    seg_for = []                        # F331: is THIS segment a `for` header (_for_headers)?
    base = session_cwd(payload)
    # A carried command (bash -c, a heredoc fed to a shell, $(...)) is walked on its own,
    # so it cannot see a `cd` made around it. When nothing anywhere changes directory it
    # runs in the session's directory too; once anything does, where it runs is not
    # known here, and it keeps today's "unknowable" rather than a guessed directory.
    moves = any(verb(s) in DIR_VERBS or verb(s) == "popd"
                for c_ in all_cmds for s in segments(c_))
    for i_, c in enumerate(all_cmds):
        top = i_ == 0 or c == top_view
        tracked = cwd_lanes(c, base if (top or not moves) else "", seeded=True, roots=roots)
        # The directory each segment had BEFORE lane F (no session seed), for the two
        # rules whose verdict is an ASK outside the profile -- see `gitwipe_t` below.
        # Lane j of one walk is lane j of the other (cwd_lanes splits per VALUE, not per
        # directory), so a segment is judged once per (seeded, unseeded) lane pair (FX-G7).
        bare = cwd_lanes(c, roots=roots)
        physical = cwd_lanes(c, base if (top or not moves) else "", seeded=True,
                             roots=roots, physical=True) if evidence is not None else []
        heads = _for_headers(c)
        for k, (s, ds) in enumerate(tracked):
            uds = bare[k][1] if k < len(bare) else [""]
            rs = resolve_verb(s, assigns)
            # The segment BEFORE each one in the same carried command: what feeds an xargs.
            prev = tracked[k - 1][0] if k else None
            pds = physical[k][1] if physical else [""] * len(ds)
            for d, ud, pd in dict.fromkeys(zip(ds, uds if len(uds) == len(ds) else uds[:1] * len(ds), pds)):
                seg_dialect.append(dialects.get(c, "bash"))
                seg_for.append(k < len(heads) and heads[k])
                seg_cwd.append((rs, d))
                seg_prev.append(prev)
                unseeded.append(ud)
                physical_dirs.append(pd)
    # A SUBSTITUTED COMMAND NAME REACHES NO RULE AT ALL. An unknown OPERAND still
    # reaches the conservative branch; an unknown VERB reaches nothing, so it takes
    # all three hard blocks with it. MEASURED 2026-09-08 against the live hook:
    #     rm -rf "<game>"           deny
    #     $(echo rm) -rf "<game>"   ALLOW   <- total bypass
    #     `echo rm` -rf "<game>"    ALLOW
    #
    # SCOPED TO SUBSTITUTION IN THE VERB POSITION, AND TO A ROOTED OPERAND IN THE
    # SAME SEGMENT. Both narrowings were forced by measurement, not taste, over
    # 28,989 real Bash commands from 110 session transcripts:
    #
    #   any unresolved verb token          679 hits (2.3%)  -- mostly $JQ / $UV / $f,
    #                                      where the variable holds a PATH so
    #                                      resolve_verb correctly leaves it alone
    #   substituted verb, any target       834 hits (2.9%)
    #   substituted verb + rooted operand    4 hits (0.014%)   <- this rule
    #
    # I predicted "under 20" for the first of those and was wrong by 34x, which is
    # the whole argument for pricing a guard change before shipping it (CLAUDE.md
    # #36). The root test is on THIS SEGMENT'S OWN OPERANDS, never on a root
    # appearing anywhere in the command -- that conjunction over the whole string is
    # the shape four false positives came from in one day.
    def _subst_verb(seg):
        """Is this segment's command name a substitution -- written in place, or (FX-G4 /
        reviewer G-OUT, MEASURED: `x=$(printf rm); $x -rf "<reference>"` was ALLOWED while
        `x=rm; $x` denied) through a variable this command assigned a substitution to?
        P1: or a field WORD-SPLIT out of an `env NAME=<unquoted expansion>` (_env_split_index)."""
        if _env_split_index(seg) is not None:
            return True
        t = _verb_token(seg)
        if not t:
            return False
        if _SUBST.search(t):
            return True
        # Through a variable only at a real COMMAND position: a segment cut inside an
        # assignment's own substitution (`f=$(find "$P" ...` -- the verb token reads `$P`)
        # is not one. MEASURED in the corpus replay: 13 false advisories before this.
        # A prefix word whose substitutions CLOSE is not that case (FX-G5 / reviewer J2 item 3,
        # MEASURED: `x=$(printf rm); A=$(true) $x -rf <reference>` was allowed): an assignment, or
        # a wrapper's value (`sudo -u $(whoami) $x`), is stepped over exactly as _verb_token steps
        # over it to find the command word. Only an UNCLOSED one is the cut fragment above.
        # The same WORDS _verb_token walks (FX-G6 / reviewer K I4): `env -C $(echo /tmp ) $x`.
        toks = _raw_words(seg)
        k = next((i for i, w in enumerate(toks) if (tokens(w) or [("",)])[0][0] == t), 0)

        if any(_span_open(x) for x in toks[:k]):
            return False
        return bool(_SUBST.search(resolve(t, assigns)))

    def _subst_verb_at_root(seg, d=""):
        t = _verb_token(seg)
        # `_SUBST.search`, NOT startswith. MEASURED 2026-09-09, by the seed this rule
        # shipped without: `/usr/bin/$(which rm) -rf <game>` was a DENY -> ALLOW. A
        # substitution EMBEDDED in the command name makes it exactly as unknowable as
        # one that opens it, and `_SUBST` -- the module's own answer to "is this
        # unresolvable?", already used by has_unresolved -- was sitting three
        # definitions away while this hand-rolled a narrower one. Two independent
        # paths answering one question, the same shape as the verb-resolver defects
        # above. The narrow conjunct is untouched: a ROOT operand in this segment is
        # still required, which is what priced this rule at 4 hits in 28,989.
        if not _subst_verb(seg):
            return False
        # Every value of every operand (FX-G6 / reviewer K C1): `"${NOPE:+x}<game>"` too.
        split = _env_split_index(seg)
        ops = _operands(seg) if split is None else [
            t for t, q in tokens(seg)[split:] if q or not t.startswith("-")]
        for r in (r for o in ops for r in resolve_variants(o, assigns)):
            if any(v and is_root(r, v) for v in roots.values()):
                return True
            # ...and anything INSIDE reference/, whose every delete and write is a hard
            # block (FX-G4 / G-OUT): `$(echo rm) -rf <reference>/libraries` was ALLOWED.
            if roots.get("reference") and under(join_cwd(d, r) if d else r, roots["reference"]):
                return True
        return False

    verb_unresolved = any(_subst_verb_at_root(s, d) for s, d in seg_cwd)
    # K1 (v4.0 release review): the save bridge may lift the PROFILE asks only when every
    # mutation in the command is one the evidence collector modelled. A command name that
    # arrives by substitution or a variable, or any verb outside SAVE_MODELLED_VERBS (an
    # interpreter, a script, an unknown cmdlet), can write or delete what no evidence row
    # shows -- MEASURED: `rm <save>; python -c "os.remove('<profile>/content.xml')"` and
    # `rm <save>; $(echo rm) <profile>/content.xml` were advised once one save qualified.
    save_lift_blockers = sorted({
        "a command name from a substitution or variable"
        if (_subst_verb(s) or has_unresolved(_verb_token(s) or "")) else verb(s)
        for s, _d in seg_cwd
        if _subst_verb(s) or has_unresolved(_verb_token(s) or "")
        or verb(s) not in SAVE_MODELLED_VERBS and not _carries_program_text(s)})
    segs = [s for s, _ in seg_cwd]
    cwd = seg_cwd[-1][1] if seg_cwd else ""

    def prep(paths, c_cwd, c_old, expand=True, operation="", scope="file", copy_sources=None):
        """(path resolved where it runs, unresolvable?, the token as written). A whole
        array reference -- a for-loop variable, `"${A[@]}"` -- yields one entry PER
        element (resolve_all).

        `expand=False` for REDIRECT / output-flag targets: an operand naming an array
        (for-loop) variable stays UNRESOLVED there, as it was before loops were modelled.
        MEASURED in this lane's history replay: expanding them turned 37 historical
        commands from allow into a DENY on the /tmp and durable-record hygiene rules
        (`for g in ...; do ... > /tmp/g_$g.txt`), which is friction on rules the
        pre-arc note never concerned -- so it was scoped out, not shipped.

        `c_old` is the segment's directory WITHOUT the session seed (lane F); see below."""
        out = []
        for p in paths:
            # expand=False: resolved exactly as before this lane -- loop words and
            # `$(...)` values are not substituted, so such an operand stays unresolved.
            # C6 (v4.0 release review): EVERY value here too. One resolve() judged
            # `> "${NOPE:+x}<reference>/libraries/wares.xml"` as the alternate only -- ADVISED
            # where the plain path denies (broken since FX-G5 47d06cd; 0801a1a fixed the
            # other rules only). resolve_variants' first value is the plain resolve().
            rs = resolve_all(p, assigns) if expand else resolve_variants(p, plain_assigns)
            for r in rs:
                # A LEADING ROOT VARIABLE IS ITS ROOT, for every operand (FX-G2 items 1-2,
                # v4.0.0 delta review). Only `cd` (R2-P1) and the icacls rule substituted it,
                # so a WRITE through the variable stayed unresolved -- and the write rules are
                # deliberately not conservative about unresolved operands. MEASURED: `echo x >
                # "$X4_REFERENCE/libraries/wares.xml"`, `cp f "$X4_TOOLKIT/reference/..."`, the
                # same into "$X4_PROFILE", and every spelling of a write to
                # "$X4_TOOLKIT/x4-paths.env" were ALLOWED (34 of 40 probes) while the literal
                # path was refused. The variable NAME identifies the root (ROOT_VARS); a root
                # that is not configured is left unsubstituted, exactly as before.
                r = subst_root_var(r, roots)
                unres = has_unresolved(r)
                # PARSE DEBRIS IS NOT JOINED ONTO THE SESSION SEED (lane F). `c_old` is the
                # directory this segment had before the seed existed; a token carrying a
                # quote, a paren, a redirect or a pipe character, or naming a Windows
                # device (`nul`), is judged against it, i.e. exactly as before. MEASURED in the history replay: with the seed,
                # `2>/dev/null` read as a sed -i TARGET under the game root and an escaped
                # `\"$G\"` as a relative path there -- 3 hard denies on probe harnesses and
                # one-liners, every one a token no shell would treat as that path.
                d = c_old if (_DEBRIS.search(r) or _DEVICE.match(r)) else c_cwd
                # An 8.3 short name is its long name (FX-G2 item 3; see long_name).
                out.append((r if unres else long_name(join_cwd(d, r)), unres, r))
                if evidence is not None and operation:
                    # A cd prefix may fail and execution may continue. The existing parser
                    # does not prove control-flow dominance, so native context checks
                    # never use it to invent the omitted tool execution directory.
                    posix_bash = POSIX_HOST and cur_dialect[0] != WINDOWS_DIALECT
                    physical = physical_join("" if execution_context else physical_cwd, r,
                                             posix_bash=posix_bash)
                    row = {"operation": operation, "path": physical, "dialect": cur_dialect[0],
                                     "raw": r, "unresolved": unres or not physical or has_unresolved(physical)
                                     or bool(_DEBRIS.search(r)) or bool(_DEBRIS.search(physical))
                                     # C1: an unquoted brace-expanded word names SEVERAL paths
                                     # (or none), never the one literal path written here.
                                     or p in _BRACE_WORDS or r in _BRACE_WORDS,
                                     "scope": scope}
                    if copy_sources is not None:
                        sources = []
                        for source in copy_sources:
                            for resolved in resolve_all(source, assigns):
                                resolved = subst_root_var(resolved, roots)
                                placed = physical_join(physical_cwd, resolved, posix_bash=posix_bash)
                                sources.append({"path": placed, "unresolved": not placed or has_unresolved(resolved)
                                                or has_unresolved(placed) or bool(_DEBRIS.search(resolved))
                                                or source in _BRACE_WORDS or resolved in _BRACE_WORDS})
                        row["copy_sources"] = sources
                    evidence.append(row)
        return out

    cur_dialect = ["bash"]                # the dialect of the segment prep() is reading
    rm_t, copy_t, redir_t, mv_src = [], [], [], []
    sed_t, out_t, search_files = [], [], []
    gitwipe_t, gitdiscard_t, gitwipe_pairs = [], [], []
    scoped_rm_t, mod_t, subst_rm_t = [], [], []
    search_seg, git_all = False, False
    transcript_copy = False             # M1, see _copy_lands_a_transcript
    # H1 follow-up, see _inline_program_writes_transcript; a heredoc PROGRAM is judged once,
    # against the session directory (4.0.1, see _heredoc_program_writes_transcript).
    transcript_inline = _heredoc_program_writes_transcript(spliced, base)
    # F331 follow-up: a link a command creates, judged by its TARGET (see _judge_link); a heredoc
    # PROGRAM's links once, against the session directory, like the transcript check above.
    link_protected = link_unresolved = False
    for tgt_, at_, kind_ in _heredoc_program_links(spliced):
        j_ = _judge_link(tgt_, at_, kind_, base, {}, roots)
        link_protected = link_protected or j_ == "protected"
        link_unresolved = link_unresolved or j_ == "unresolved"
    # The save bridge supplies its single authoritative folder enumeration. This
    # parser stays independently importable and does not discover filesystem roots.
    save_folders = [norm(str(folder)) for folder in protected_save_folders]
    save_spellings = set(save_folders) | {str(folder).replace(chr(92), "/").casefold()
                                         for folder in protected_save_folders}
    save_mentions = [re.compile(re.escape(folder) + r"(?=$|[/\s\"'`<>);,*?\[\]{}$])")
                     for folder in save_spellings if folder]
    for (s, c_cwd), prev, c_old, physical_cwd, cur_dialect[0], is_for_ in zip(
            seg_cwd, seg_prev, unseeded, physical_dirs, seg_dialect, seg_for):
        if (save_folders and evidence is not None and not execution_context
                and verb(s) not in SAVE_MODELLED_VERBS and not _carries_program_text(s)
                and not _save_read_only_program(s)):
            # Possible access is not a measured mutation. Only SAVE operands feed
            # this conservative policy; the accepted game/reference residual stays scoped.
            for operand in _operands(s, include_flags=True):
                start = len(evidence)
                # Resolve configured roots even inside an option or program-text
                # candidate, only to discover possible access. A glued value is
                # still not qualified membership and must remain unresolved.
                candidates = {operand}
                for match in _VAR_ANY.finditer(operand):
                    candidates.add(operand[:match.start()] + subst_root_var(operand[match.start():], roots))
                prep(sorted(candidates), c_cwd, c_old, operation="write")
                possible = evidence[start:]
                del evidence[start:]
                for row in possible:
                    path = norm(row["path"])
                    raw = norm(row["raw"])
                    raw_spelling = str(row["raw"]).replace(chr(92), "/").casefold()
                    exact = any(under(path, folder) for folder in save_folders)
                    # A glued short option can put a letter before C:/, where
                    # norm deliberately does not recognize a standalone drive.
                    # Match native and normalized spellings without extracting
                    # or guessing an unknown program's actual option value.
                    embedded = any(pattern.search(spelling) for pattern in save_mentions
                                   for spelling in (raw, raw_spelling))
                    if not (exact or embedded):
                        continue
                    # Program text or a glued option is not an exact physical operand.
                    # Refuse its unresolved scope instead of backing up a guessed subset.
                    if embedded and not exact:
                        row["unresolved"] = True
                    row["possible_opaque_save_access"] = True
                    if any(path == folder for folder in save_folders):
                        row["scope"] = "tree"
                    evidence.append(row)
        if execution_context:
            # Context-only evidence; ordinary save membership/rules unchanged.
            prep(git_wipes_worktree_targets(s), c_cwd, c_old, operation="delete")
        rm_t += prep(rm_paths(s), c_cwd, c_old, operation="delete")
        rm_t += prep(xargs_feed(s, prev), c_cwd, c_old, operation="delete")
        rm_t += prep(rsync_deletes(s), c_cwd, c_old, operation="delete", scope="tree")
        robo_copy, robo_del, robo_mv = robocopy_effects(s)
        copy_t += prep(robo_copy, c_cwd, c_old, operation="write", scope="tree")
        rm_t += prep(robo_del, c_cwd, c_old, operation="delete", scope="tree")
        mv_src += prep(robo_mv, c_cwd, c_old, operation="move", scope="tree")
        mod_t += prep(modify_targets(s), c_cwd, c_old, operation="write")
        # A FILTERED find-delete removes entries INSIDE its tree: it feeds every in-tree
        # delete rule, and never the whole-install hard block (see find_scoped_deletes).
        scoped_rm_t += prep(find_scoped_deletes(s), c_cwd, c_old, operation="delete", scope="filtered-tree")
        # A command name that arrives by substitution MAY be rm: its operands feed the X4
        # directory delete ADVISORY (FX-G4 / BLIND-SPOTS F183, MEASURED: `$(echo rm) -rf
        # <game>/libraries` was a plain allow where `rm -rf` advises). Advisory only; an
        # operand that IS a root is denied by verb_unresolved above.
        if _subst_verb(s):
            # UNSEEDED (lane F): a relative operand joins only a directory the COMMAND entered,
            # never the session's. MEASURED in the corpus replay: 6 false advisories, each a
            # parse fragment of a substitution whose debris joined the session cwd (the game).
            subst_rm_t += prep(_operands(s), c_old, c_old)
        # truncate / dd of=are truncating writes, judged as `>` is (see clobber_targets).
        redir_t += [("truncate",) + o for o in prep(clobber_targets(s), c_cwd, c_old, False, operation="write")]
        mv_src += prep(move_sources(s), c_cwd, c_old, operation="move")
        dests_ = prep(copy_dests(s), c_cwd, c_old, operation="write", scope="copy-destination",
                      copy_sources=exact_copy_sources(s))
        copy_t += dests_
        transcript_copy = transcript_copy or _copy_lands_a_transcript(s, dests_, assigns)
        transcript_inline = transcript_inline or _inline_program_writes_transcript(s, c_cwd, assigns)
        for tgt_, at_, kind_ in ([] if is_for_ else _segment_links(s, assigns)):     # F331
            j_ = _judge_link(tgt_, at_, kind_, c_cwd, assigns, roots)
            link_protected = link_protected or j_ == "protected"
            link_unresolved = link_unresolved or j_ == "unresolved"
        sed_t +=prep(sed_in_place_targets(s), c_cwd, c_old, operation="write")
        out_t += prep(output_targets(s), c_cwd, c_old, False, operation="write")
        search_files += prep(search_paths(s, require_recursive=False), c_cwd, c_old)
        # UNSEEDED, deliberately (lane F). `git clean -f` / `reset --hard` name no path, so
        # with the session seed EVERY one run from the game or toolkit root becomes an ASK
        # -- a new prompt outside the profile, which the user has ruled out (2026-10-02:
        # approval fatigue). MEASURED in the replay: 1 historical row, a probe harness.
        # The rule keeps exactly its pre-lane reach: an explicit `cd <root>` or `git -C`.
        gitwipe_t += prep(git_wipes_worktree_targets(s), c_old, c_old)
        # FX-G7: a work tree named by an ASSIGNMENT in another segment (`export
        # GIT_WORK_TREE=<game>; git clean -fdx`) reaches git through the environment, and
        # _git_destructive reads only this segment's tokens. MEASURED: from a folder outside
        # every root that was ALLOW (old and new) where every other spelling of the same work
        # tree asks. Named-only (it feeds this ask, never the pairs below), so a bare wipe
        # from an X4 directory keeps its deny.
        if git_wipes_worktree_targets(s):
            gitwipe_t += prep([_gitdir_parent(v_) if k_ == "GIT_DIR" else v_
                               for k_, v_ in assigns.items()
                               if k_ in ("GIT_WORK_TREE", "GIT_DIR") and isinstance(v_, str)],
                              c_old, c_old)
        # ...and SEEDED for J-Q1's deny: the same wipe judged where the session runs. Its
        # verdict is a deny with a reason, which reaches the agent and never the user.
        # PER SEGMENT (R2-F5): (this segment's named form, its seeded form), so a folder
        # named by ANOTHER segment cannot excuse this one -- see git_wipe_from_session_dir.
        gitwipe_pairs.append((prep(git_wipes_worktree_targets(s), c_old, c_old),
                              prep(git_wipes_ignored_targets(s), c_cwd, c_old)))
        gitdiscard_t += prep(git_discards_named_files(s), c_cwd, c_old, operation="write")
        search_seg = search_seg or searches(s)
        git_all = git_all or git_adds_everything(s)
        for mode, tgt in redirects(s):
            redir_t += [(mode,) + o for o in prep([tgt], c_cwd, c_old, False, operation="write")]

    # Only unquoted brace syntax is expanded by Bash. Reuse the tokenizer's
    # quote-aware side channel, resolving roots exactly as prep() did. Quoted
    # literal range-looking filenames must not trigger an expansion refusal.
    brace_patterns = {norm(subst_root_var(value, roots)) for word in tuple(_BRACE_WORDS)
                      for value in resolve_all(word, assigns)}

    def hit(ops, key, conservative=False):
        """`conservative` is the delete-only rule (user decision 2026-09-01): an
        operand this hook cannot resolve is not evidence of safety. Deletes are the one
        channel with nothing behind them -- MEASURED, 0 of 186 auto-backups cover
        anything outside dev/, and savegames are covered by nothing at all. Writes keep
        their existing verdicts so this cannot add prompts to routine work."""
        root = roots.get(key)
        if not root:
            return False
        for path, unres, raw in ops:
            if unres:
                # The OPERAND, not the whole command. This read `norm(root) in ncmd`,
                # i.e. the entire raw command including comments and quoted prose, so
                # `ls "<ref>" && rm -rf "$TMPDIR/build"` was a non-overridable DENY --
                # the root is in the command, just not in the thing being deleted.
                # MEASURED 2026-09-03: 3 of 3 probes of that shape denied, across five
                # rules, two of which hard-deny. `raw` is the operand after resolve(),
                # so `rm -rf "<ref>/$SUB"` -- the case this branch exists for -- still
                # fires: its resolved text does contain the root.
                if norm(root) in norm(raw):
                    return True
                if conservative and key in root_vars_named(raw):
                    return True
            elif under(path, root):
                return True
            elif glob_covers(norm(path), norm(root), expand_braces=norm(raw) in brace_patterns):
                # `under()` compares literal paths, so `rm -rf "<ref>"*` -- which is a
                # SIBLING pattern that expands to include the root -- slipped past every
                # root rule. The operand is fully present in the text, so this is
                # decidable rather than a guess; see glob_covers.
                return True
        return False

    # AN UNKNOWN POWERSHELL CMDLET THAT MAY WRITE (review item 6). ps_translate.ps1 cannot
    # know every module's cmdlets, so a cmdlet outside its map whose verb is not a
    # read-only one arrives as `x4-unknown-cmdlet <Name> <args...>`. Only this side holds
    # the roots: if an argument names a PROTECTED tree, that write reached no rule, and
    # the part is reported untranslated -> ask. The workspaces (toolkit, mods) are not
    # protected trees, so ordinary module commands there stay silent.
    # UNSEEDED (lane F), for the same reason as `gitwipe_t`: this ends in an ASK, and an
    # unknown cmdlet's RELATIVE arguments are not known to be paths at all. MEASURED in the
    # replay: 2 historical rows, both a user-defined PowerShell function called with
    # relative names from the game root -- each would have become a new prompt.
    for (s, _c), c_old in zip(seg_cwd, unseeded):
        if verb(s) == "x4-unknown-cmdlet":
            ops = prep(_operands(s), c_old, c_old)
            if any(hit(ops, k, conservative=True)
                   for k in ("game", "reference", "profile", "saves", "documents")):
                name = (_operands(s) or ["?"])[0]
                _UNTRANSLATED.append("the cmdlet %s, which the guard does not model, is "
                                     "given a protected path" % name)

    # F10 (v4.0): A PROGRAM THE GUARD CANNOT NAME, GIVEN A PROTECTED PATH. The same question
    # as the unknown cmdlet above, for a command name from a variable or a substitution
    # (`$R -rf <game>`, `"$R" <profile>/content.xml`, `& $R Remove-Item <save>` from the
    # PowerShell front end, `%R% del <x>` from cmd): it may be a delete, and no rule can
    # say. The readings (dynamic_program_readings) catch the wrapper and eval shapes with
    # the plain command's own verdict; this is the backstop for the name that may BE the
    # destructive program. UNSEEDED for the same reason as the unknown cmdlet. A command
    # name that is a ROOT variable (`"$X4_GAME/X4.exe"`) is a known program, not this case.
    program_dynamic = False
    for (s, _c), c_old in zip(seg_cwd, unseeded):
        if not _dynamic_program(s):
            continue
        if not has_unresolved(subst_root_var(resolve(_verb_token(s), assigns), roots)):
            continue
        ops = prep(_operands(s), c_old, c_old)
        if any(hit(ops, k, conservative=True)
               for k in ("game", "reference", "profile", "saves", "documents")):
            program_dynamic = True
            break
        # ...or NAMES one inside a longer word: `$R -c 'Remove-Item <ref>'` (R = a PowerShell
        # host) hands the root over as TEXT, which no path reading of the word can see.
        if any(norm(roots[k]) in norm(o) or k in root_vars_named(o)
               for o in _operands(s)
               for k in ("game", "reference", "profile", "saves", "documents") if roots.get(k)):
            program_dynamic = True
            break

    redir_t_all = [(p, u, r) for _m, p, u, r in redir_t]
    writes_any = copy_t + rm_t + scoped_rm_t + mod_t + [(p, u, r) for _, p, u, r in redir_t]
    trunc_redirect = [(p, u, r) for m, p, u, r in redir_t if m == "truncate"]

    # The game-delete HARD BLOCK is scoped to what is actually catastrophic: the install
    # root itself, or extensions/ wholesale (which destroys every deployed mod). Anything
    # INSIDE the tree falls through to the confirmation, which is the verdict meant for
    # it.
    #
    # MEASURED 2026-08-31 over a 1,000-command corpus sample: all 4 hits of this rule were
    # `rm -rf "$DST"` where DST resolved to extensions/<one mod> -- the documented deploy
    # path, which dev/_tools/deploy.py performs itself. A hard deny there blocks routine
    # work, and it only started happening because variable resolution got BETTER: the old
    # helper could not see through $DST at all. A capability improvement widened a guard
    # nobody re-scoped for it.
    #
    # The name backstop is root-scoped for the same reason. It is the only protection an
    # installation with no configured paths has, so it stays -- but a path merely CONTAINING
    # the game's name is not the install, and the archive exclusion is what removed the
    # measured false positive (7 of 8 were a .zip named after the game).
    def hits_game_root(op):
        g = norm(roots.get("game") or "")
        path, unres, _raw = op
        if not g or unres:
            # An unresolvable operand never reaches the HARD BLOCK. It cannot be
            # PROVEN to be the install root, and a deny the user cannot override is
            # the F93 failure -- it goes to the confirmation below instead.
            return False
        n = norm(path)
        if n == g or n == g + "/extensions":
            return True
        # A GLOB is not the F93 case. `$DST` cannot be proven to be the install root,
        # so it must not reach a non-overridable deny; a glob is fully present in the
        # text, so whether it covers the root is decidable -- and `rm -rf "<game>"*`
        # deletes the installation exactly as the literal form does.
        return (glob_covers(n, g, expand_braces=norm(_raw) in brace_patterns)
                or glob_covers(n, g + "/extensions", expand_braces=norm(_raw) in brace_patterns))

    # No archive exclusion: GAME_ROOTISH is anchored at $ and so is ARCHIVE, and they
    # demand different endings, so nothing can match both -- PROVEN over probes, and
    # the mutation gate reported the term as unkillable, which is what dead code looks
    # like from the outside. The anchoring subsumes it; the .zip false positive that
    # motivated the exclusion (7 of 8 hits) can no longer reach this line.
    # NB: deliberately does NOT skip unresolved operands, unlike hits_game_root above.
    # The two ask different questions. hits_game_root compares against a CONFIGURED
    # root, and an operand carrying a `$` can never be proven equal to one. This is the
    # NAME backstop -- the only protection an unconfigured machine has -- and there the
    # visible text IS the evidence: `rm -rf "$BUILD/X4 Foundations"` still ends in the
    # game's name. Adding a `not u` filter here (as this line briefly did on
    # 2026-09-01) silently removed that last line of defence, and the 13,041-command
    # corpus could not see it because no historical command has that shape.
    # `p or raw`, and the fallback is the whole point. prep() stores
    # join_cwd(cwd, resolved) in element 0, and join_cwd returns "" whenever the
    # operand is RELATIVE and the shell's directory is unknowable -- which is correct
    # for the path rules (inventing a root there would fire on unrelated work) but
    # silently disarms the NAME backstop, whose entire job is the operand that bears
    # the game's name WITHOUT being a resolvable path.
    #
    # MEASURED 2026-09-02: `rm -rf "X4 Foundations"` and `cd sub && rm -rf "X4
    # Foundations"` both read False, while the same delete after a cd to an ABSOLUTE
    # directory read True. The backstop was working only in the case it was least
    # needed.
    rm_named_game = any(GAME_ROOTISH.search(norm(p or raw)) for p, _u, raw in rm_t)

    # OVER THE RESOLVED SEGMENTS. This re-derived its own walk from all_cmds and
    # never saw a verb resolve_verb had spliced, so the two search DENIES were
    # blind to the variable spelling the resolver exists for. MEASURED:
    #     rm  -> `RM=rm;  $RM -rf <ref>`   rm_targets_reference        True
    #     grep-> `GP=grep; $GP -rn x <ref>` search_rooted_reference    FALSE
    # Two independent paths answering one question, which is the shape this
    # file's own narrowing table exists to refuse.
    search_roots = []
    for s, c_cwd in seg_cwd:
        for p in search_paths(s):
                # Every value (FX-G6 / K C1): `find "${NOPE:+x}<game>"` searches <game>.
                for r in resolve_variants(p, assigns):
                    search_roots.append(c_cwd if r in (".", "./") else r)

    def rooted(root):
        return bool(root) and any(is_root(p, root) for p in search_roots)

    def above(root):
        """A searched path that CONTAINS `root`: the walk reaches it and then some."""
        return bool(root) and any(contains_root(p, root) for p in search_roots)

    # `body` already has heredocs AND comments removed. Deriving these from the raw
    # command left the string-matching rules (git_add_all, sed -i, longjob, the profile
    # search) blind to anything after an apostrophe in a comment -- 1 of the 5 measured
    # bypasses survived the parser fix for exactly this reason, because it read a
    # different string from the one that had been cleaned.
    # B4: over `all_cmds`, not `body`. These two read the string with its WRAPPERS
    # INTACT while every path rule reads the unwrapped carrier list, so they were
    # answering a question about a different command. MEASURED 2026-09-06: a single
    # `bash -c` wrapper hid a foreground long job, and two hid it from `$?`-after-a-
    # pipeline as well -- both silent, both trivially reachable by ordinary scripting.
    #
    # Safe on the heredoc axis by construction: heredoc_bodies() already admits only
    # bodies whose OPENER RUNS A SHELL, so a `cat > notes.md <<X` payload quoting a
    # long job's name never reaches all_cmds and cannot fire this.
    longjob = False
    for c in all_cmds:
        for s in segments(c):
            b = blank_quoted(s)
            if any(j in b for j in LONG_JOBS) or ("x4effective" in b and "build" in b):
                if INVOKERS.search(b):
                    longjob = True

    _wipe_keys = ("game", "profile", "mods", "toolkit", "reference")
    git_wipe_named = any(hit(gitwipe_t, k, conservative=True) for k in _wipe_keys)
    # J-Q1's deny, per SEGMENT: a bare wipe judged from the session dir, unless THAT segment
    # names its own folder (then it keeps the ask above). R2-F5 (v4.0.0 review): this was
    # `not git_wipe_named` over the whole command, so `git -C <mods> clean -fdx; git clean
    # -fdx` from the game root traded the bare segment's deny for the named one's ask.
    git_wipe_bare = any(
        not any(hit(named, k, conservative=True) for k in _wipe_keys)
        and any(hit(seeded, k, conservative=True) for k in _wipe_keys)
        for named, seeded in gitwipe_pairs)

    # 2026-10-27 (user, narrowing 2026-10-26): a delete that removes a DEPLOYED MOD -- a
    # folder directly under the game's extensions/, or anything inside one -- ASKS, ONCE per
    # command, naming every mod it touches. Every other delete in the game install keeps the
    # rm_in_x4_dir advisory (bd45eef); extensions/ itself stays the rm_hits_game hard block.
    # Same operand channels as rm_in_x4_dir's game clause. Names are lower-cased by norm().
    _g = norm(roots.get("game") or "")
    _ext = _g + "/extensions/" if _g else ""

    def _deployed_mods(ops, filtered=False):
        # prep() already substituted a root variable ($X4_GAME, F154) and, for an unresolved
        # operand, `path` IS its resolved text -- so an unknown mod name stays as written.
        names = []
        for path, _unres, _raw in ops:
            if not _ext:
                break
            n = norm(path)
            if n.startswith(_ext):
                seg = n[len(_ext):].split("/")[0]
                if seg == "${xargs_item}":
                    # `find <extensions> ... | xargs rm`: each found entry, any mod
                    names.append("<any mod the find matches>")
                elif seg:
                    names.append(seg)
            elif filtered and n == _ext[:-1]:
                # a FILTERED find over extensions/ itself reaches into any mod it matches
                names.append("<any mod the find matches>")
        return names

    _mods = list(dict.fromkeys(_deployed_mods(rm_t + mv_src + subst_rm_t)
                               + _deployed_mods(scoped_rm_t, filtered=True)))
    _mod_list = ", ".join(_mods[:10]) + (f" and {len(_mods) - 10} more" if len(_mods) > 10 else "")
    result = {
        "command": cmd,
        "timeout": timeout,
        "background": background,

        # A command too tangled to analyse IN FULL is not a clean pass. The carrier
        # walk is bounded (see _MAX_CARRIED); when that bound is hit, some command
        # text reached no rule, and saying nothing would be a step that narrows its
        # data and reports success. Unreachable by ordinary work: MEASURED over
        # 13,503 real commands, the largest walk produced 25 of the 250 allowed.
        "carriers_truncated": carriers_truncated,
        "cd_lanes_truncated": _CD_LANES_TRUNCATED[0],
        # A cmd/PowerShell carrier whose text could not be translated (HK-2): some
        # command reached NO rule, so -- like the truncation above -- not a clean pass.
        "carrier_untranslated": bool(_UNTRANSLATED),
        "from_powershell": from_powershell,
        "verb_unresolved": verb_unresolved,
        # F10: a command name from a variable/substitution, given a protected path.
        "program_dynamic_protected": program_dynamic,
        # Verbs whose file effects no evidence row models (K1); non-empty keeps the
        # save bridge from lifting any profile ask for this command.
        "save_lift_blockers": save_lift_blockers,

        # A `mv` SOURCE that is a protected root is a delete of that root: the
        # install is equally gone whether it was removed or moved away. Sources
        # INSIDE a root are deliberately left to the existing confirmation below,
        # so the deploy path -- moving a built mod into extensions/ -- is untouched.
        "rm_hits_game": (any(hits_game_root(o) for o in rm_t + mv_src)
                         or rm_named_game),
        "rm_targets_reference": hit(rm_t + mv_src + scoped_rm_t, "reference",
                                    conservative=True),
        # A WRITE into reference/, which had no Bash rule at all -- only deletes were
        # covered, while protect-files.sh hard-blocks the same tree for Edit/Write and
        # CLAUDE.md lists it under "Hard blocked". MEASURED 2026-09-03: a truncating
        # redirect, a cp and a sed -i into reference/ were all ALLOW. Long-standing
        # gap, not a regression.
        #
        # Not `conservative`: an unresolvable operand fires only when its own text
        # names the root, which is the write convention throughout this file (deletes
        # are the one channel with nothing behind them). `bin/unpack-reference.sh` is
        # unaffected -- invoking a script passes no reference path as an operand.
        # APPENDS TOO. This used trunc_redirect, which filters redirects to
        # m == "truncate", so `echo x >> <ref>/w.xml` was ALLOW while
        # `echo x > <ref>/w.xml` hard-blocked -- the same primitive on the same
        # file, under a rule whose own message says "never write into it".
        # `tee -a` was already caught, so the two spellings of one append
        # disagreed with each other.
        #
        # Truncate-only is correct for the GAME/PROFILE advisory below, whose
        # stated reason is that an append cannot truncate. It is not this tree's
        # policy: reference/ is read-only source of truth, and writes_documents
        # beside it already uses the WIDER writes_any -- so the less valuable
        # tree had the wider channel and the hard-blocked one the narrower.
        "writes_reference": hit(copy_t + [(pp, uu, rr) for _m, pp, uu, rr in redir_t]
                                + sed_t + out_t + mod_t, "reference"),
        "rm_in_x4_dir": any(hit(rm_t + mv_src + scoped_rm_t, k, conservative=True) for k in
                            ("game", "profile", "mods", "toolkit")) or rm_named_game
                        or hit(subst_rm_t, "game"),
        # 2026-10-27 (user): removing a DEPLOYED MOD asks -- one prompt per command, naming
        # every mod (first 10, then "and N more"). See _deployed_mods above.
        "rm_deployed_mod": bool(_mods),
        "rm_deployed_mod_names": _mod_list,
        "rm_saves": hit(rm_t + mv_src + scoped_rm_t, "saves", conservative=True),
        # The PROFILE keeps a confirmation when rm_in_x4_dir became an advisory (user,
        # 2026-10-02): a bad content.xml or save="1" can damage saves.
        "rm_in_profile": hit(rm_t + mv_src + scoped_rm_t, "profile", conservative=True),

        # git IGNORES the read-only attribute (MEASURED 2026-09-04: `git checkout`
        # overwrote a locked file and left it unlocked; `git clean -fdx` deleted one),
        # so x4lock cannot cover this and the hook is the only layer that sees it.
        # `conservative` for the same reason the delete rules use it: there is nothing
        # behind this one either.
        "git_wipes_x4_dir": git_wipe_named,
        # J-Q1 (Plan 3): the BARE form from an X4 session dir, which the ask above cannot
        # see (unseeded). Exclusive of it: a command that names the folder keeps its ask.
        "git_wipe_from_session_dir": git_wipe_bare,
        "git_discards_x4_files": any(
            hit(gitdiscard_t, k, conservative=True)
            for k in ("game", "profile", "mods", "toolkit", "reference")),

        "writes_documents": hit(writes_any, "documents"),
        # A shell WRITE to a Claude Code settings file (user decision 2026-10-05: "Block
        # X4_GUARD there"). Its `env` block reaches every hook, so `"X4_GUARD": "off"` there turns
        # the guards off at the next launch -- and a shell write's resulting content cannot be
        # seen, so every one is refused with the file-edit tool as the remedy (settings_guard.py
        # judges that channel's content). A delete or a move AWAY is not a write and sets nothing.
        "writes_agent_settings": any(
            _AGENT_SETTINGS.search(norm(pp or rr))
            for pp, _u, rr in copy_t + redir_t_all + sed_t + out_t),
        "writes_profile": hit(writes_any, "profile"),
        # H1: any shell write/append/copy/move ONTO a host session transcript (see _HOST_TRANSCRIPT).
        # M1: also a directory destination that receives a .jsonl, and the host-relocated trees.
        "writes_host_transcript": transcript_copy or transcript_inline or any(
            _is_transcript(pp, rr)
            for pp, _u, rr in copy_t + redir_t_all + sed_t + out_t + mod_t),
        "copy_into_game_or_profile": bool(copy_t) and (
            hit(copy_t, "game") or hit(copy_t, "profile")),
        "redirect_truncate_into_game_or_profile": (
            hit(trunc_redirect, "game") or hit(trunc_redirect, "profile")),

        # The FILE sed rewrites must be under a root. Was: the text "sed -i" anywhere
        # AND a root named anywhere -- so editing a local file while a comment happened
        # to mention the game was a non-overridable DENY.
        "sed_i_in_game_or_profile": (hit(sed_t, "game") or hit(sed_t, "profile")
                                     or any((not u) and LEGACY_GAME.search(pp)
                                            for pp, u, _r in sed_t)),

        # re.M is load-bearing: the bash original used grep, which is LINE based, so `^`
        # matched every line start. Without it this only saw a command whose very first
        # characters were `git add`, and multi-line commands are routine here.
        # Parsed, not matched. The old anchor required `git` to begin a segment, so a
        # subshell wrap and an env-assignment prefix both slipped past it -- found by
        # scripts/fuzz-guard.py, not by any hand-written test.
        "git_add_all": git_all,
        # Matched on the token AS WRITTEN as well as resolved: a durable record is
        # recognised by its filename, which survives either form.
        "durable_truncating_redirect": any(DURABLE.search(p) or DURABLE.search(raw)
                                           for p, _u, raw in trunc_redirect),
        # Evaluated over `body` -- comments and heredoc bodies removed -- like every
        # neighbouring rule. Reading the RAW command made this the last rule in the file
        # ANDing two independent predicates over the whole text, which is the exact shape
        # the 2026-09-01 rewrite existed to remove. MEASURED 2026-09-02: it fired on the
        # reviewer's own ordinary commands twice, and on mine twice, because a comment
        # mentioning a durable record plus an unrelated `open(x, "w")` anywhere in the
        # command was enough. A non-overridable deny on writing a scratch file is the
        # failure mode this file's own header names: a rule that fires on ordinary work
        # gets bypassed.
        # ...and only when something in the command is actually a PYTHON interpreter.
        # A sentence naming a durable record and containing the call as PROSE satisfied
        # both text predicates and was a non-overridable DENY on printing it. MEASURED
        # 2026-09-02: it fired on the reviewer twice and on me twice, and then on the
        # very command that applied this fix. The rule is named for a PYTHON write to a
        # durable record, so the interpreter belongs in the claim.
        #
        # The two-predicate AND is kept rather than collapsed into one regex demanding
        # the filename inside the call: the case this exists for usually writes through
        # a variable, and the tighter regex would miss precisely that.
        # PER SEGMENT. This ANDed two WHOLE-BODY predicates with "some segment is
        # python", so prose in one segment and an unrelated write in another fired it:
        # `echo 'appending to BLIND-SPOTS.md' && python -c "open('/x/o.txt','w')"` was
        # a DENY. The truncating python must itself be the one naming the record.
        #
        # Each segment is checked AS WRITTEN and RESOLVED, because the record name
        # usually arrives through a variable -- which the whole-body form got for free
        # and a naive per-segment rewrite would have silently dropped.
        "durable_python_open_w": any(
            (DURABLE.search(sg) or any(DURABLE.search(x) for x in resolve_variants(sg, assigns)))
            # `[^,]*`, NOT `[^)]*`: a class excluding `)` cannot cross one, and this
            # machine's game root sits under `Program Files (x86)`. MEASURED 2026-09-04:
            # the rule was structurally DEAD for the two files its own docstring names --
            # the game-root CLAUDE.md and KNOWLEDGEBASE.md, the pair a reviewer's
            # installer probe actually overwrote. A path may hold parens; the first
            # argument of open() rarely holds a comma.
            # The MODE can arrive through an expansion just as the path can:
            # `FZ="wQQ"` then `open(<durable>, "${FZ%QQ}")`. The path half already
            # consulted `resolve`; the mode half did not, which the fuzzer measured
            # as 3 bypasses of a DENY on 2026-09-06.
            # Only the executable -c text can supply the literal open call.
            # Keep durable names in argv visible: actual code may open argv[1].
            and (re.search(r"open\([^,]*,\s*[\"']w[\"']", _python_literal_source(sg))
                 or any(re.search(r"open\([^,]*,\s*[\"']w[\"']", x)
                        for x in (_python_literal_source(value) for value in resolve_variants(sg, assigns))))
            and _verb_name(verb(sg)) in _PYTHONS
            # OVER `all_cmds`, not `segments(body)` -- the same defect as B4, in the
            # rule with the most to lose. MEASURED 2026-09-06 by the fuzzer, once this
            # rule finally had a seed: 21 bypasses, every one a wrapper, a carrier or
            # a stdin form the carrier walk ALREADY resolves. `bash -c` alone was
            # enough to overwrite KNOWLEDGEBASE.md unseen.
            # RESOLVED SEGMENTS, for the same reason as search_roots above: this
            # walked segments(c) raw, so `PY=python; $PY -c "open(...,'w')"`
            # reached no rule while the plain spelling denied.
            for sg, _c in seg_cwd),

        # instrument_hygiene.py's own shape `bare-python-on-project-code`: bare
        # `python`/`python3`/`py` invoking TOOLKIT code reports ModuleNotFoundError
        # or SyntaxError from the system interpreter, which reads exactly like a
        # real test failure. `uv run ... python`, an absolute interpreter, and a
        # .venv spelling are unaffected (_is_bare_python_word checks the RAW verb
        # token, never the basename-folded one); a flag-only call or an inline
        # `-c` payload runs no FILE at all and is unaffected too
        # (_python_script_target). See the commit that added this rule for the
        # measured fire rate and the false-positive classification over the
        # historical corpus.
        #
        # Judged against the SESSION's directory (the payload's `cwd`, which Claude Code
        # sends with every call) or a stand-in for it, so `cd tools/x4validate && python -m
        # pytest` counts and `python -m pytest` in an unrelated project does not
        # (finding 6). Same segments as seg_cwd, in the same order.
        "bare_python_on_project_code": any(
            _bare_python_targets_project_code(resolve_verb(sg, assigns), py_cwd, assigns)
            for c in all_cmds
            for sg, py_cwd in cwd_track(c, str(payload.get("cwd") or "") or _UNKNOWN_CWD)),

        # TWO CLAUSES, and each needs its own falsification twin: the search is rooted
        # AT reference\, or ABOVE it. The second was added 2026-09-21 after the
        # hook_false_positives gate went red -- see `contains_root` for the measurement.
        # An ancestor search is strictly worse than the exact one this rule was written
        # for, because it traverses the 60 GB AND everything beside it.
        "search_rooted_reference": (rooted(roots.get("reference"))
                                    or above(roots.get("reference"))),
        # `mods` was here and is deliberately NOT, from 2026-09-04. The rule's own
        # message cites 300 s and "GBs of binary database pages" -- true of the TOOLKIT
        # root, where tools/basex/basex/data lives, and false of the mod source tree.
        # MEASURED on this machine: a recursive grep over the whole mods root is 230 ms
        # over 1,190 files. Refusing that, with a reason that does not apply to it, is
        # a guard being wrong at the user's expense -- and a rule whose stated reason is
        # visibly false where it fires is one people learn to route around.
        "search_rooted_workspace": any(rooted(roots.get(k)) for k in
                                       ("toolkit", "game")),
        # The SEARCHED PATH must be the profile. Was: any search verb anywhere AND the
        # profile named anywhere AND "content" anywhere -- so a grep of a local file
        # beside an unrelated cat of the manifest was a DENY.
        "profile_search_by_name": (
            search_seg
            # An operand naming the profile ENV VAR is the profile, even though its
            # text never contains the path -- the same evidence the delete rules use.
            # Without this, the env-var form was lost when the rule stopped matching
            # raw command text.
            and any("content" in norm(_r) and (
                        ("profile" in root_vars_named(_r))
                        or (roots.get("profile") and (not u)
                            and under(pp, roots["profile"])))
                    for pp, u, _r in search_files)
            and not re.search(r"ws_[0-9]{4,}", cmd)),

        "dollarq_after_pipe": any(dollarq_after_pipe(c, assigns) for c in all_cmds),
        # A redirect or output-flag TARGET under the shared temp dir. Was a raw regex
        # with no heredoc strip and no quote blanking, so it fired on a grep PATTERN, on
        # quoted prose and on heredoc bodies -- while its message tells you to use the
        # scratchpad. It denied three of this session's own analysis commands.
        "write_to_tmp": any(under(pp, "/tmp") for pp, u, _r in redir_t_all + out_t
                            if not u),
        # A JSON number can be a float, and a client may send a string. The old
        # isinstance(int) turned the rule OFF for both -- silently, which is the
        # wrong direction for a cap. MEASURED: 0 of 13,277 historical calls used
        # anything but int, so this is robustness, not an observed bug.
        # `bool` is excluded deliberately: in Python True is an int.
        # A BACKGROUND call has its own cap: 7200000 ms (READ: the Bash tool description,
        # 2026-10-02). Judged by the foreground cap, a 25-minute background job was denied
        # by a message telling it to run in the background. `is True`, as for longjob below.
        "timeout_over_cap": not codex_async_shell and _as_ms(timeout) > (7200000 if background is True else 600000),
        "longjob_foreground": longjob and background is not True and not codex_async_shell,
        # 2026-10-26 (user): under a Codex shell the two facts above are not silently dropped --
        # the same conditions set these, which protect-bash.sh ADVISES on (fine if the shell
        # yields a running session; otherwise background it or shorten it).
        "longjob_codex": longjob and background is not True and codex_async_shell,
        "timeout_over_cap_codex": codex_async_shell and _as_ms(timeout) > (7200000 if background is True else 600000),
        # THE LAST WHOLE-COMMAND AND-OF-INDEPENDENT-PREDICATES RULE IN THIS FILE,
        # and it hard-denied PROSE. It tested `cmd`/`ncmd` -- the RAW text rather
        # than `body` -- so a comment or a quoted string naming the tool, the
        # output flag and the reference path was a NON-OVERRIDABLE DENY on a
        # command that unpacks nothing. It fired on me while I was testing it:
        # the probe listing the cases was itself refused.
        #
        # This is the shape the 2026-09-01 rewrite removed everywhere else in
        # this file ("Each ANDed two independent predicates over the WHOLE
        # command text"); it is the one the sweep missed. Scoped now to a SEGMENT
        # whose VERB is the tool and whose OWN operands name the reference root.
        "xrcat_reunpack": any(
            "xrcat" in _verb_name(verb(sg)).lower()
            and any(tk.startswith("-out") for tk in tokens_of(sg))
            and bool(roots.get("reference"))
            and any(is_root(r_, roots["reference"])
                    for o_ in _operands(sg) for r_ in resolve_variants(o_, assigns))
            for sg, _c in seg_cwd),
        # Lifting the OS deny on reference/ (Plan 2 lane D). D8: only the USER lifts a
        # protection, so an agent's lift is an ASK. Segment-scoped like xrcat_reunpack.
        "lifts_reference_deny": any(_lifts_reference_deny(sg, assigns, roots, _c)
                                    for sg, _c in seg_cwd),
        # F331 follow-up: a link CREATED with its target in (or above) reference/, the game, the
        # profile or the saves -- every later write through it lands there -- and a link verb
        # whose target the guard cannot read.
        "link_into_protected": link_protected,
        "link_target_unresolved": link_unresolved,
        "cwd": cwd,
    }
    # Several fields above evaluate glob membership; capture scope AFTER every
    # field has run so a late overflow cannot disappear from the result.
    result["operand_expansion_truncated"] = _OPERAND_EXPANSION_TRUNCATED[0]
    return result


# ----------------------------------------------------------------------- CLI
# Emits `key<TAB>0|1` lines, then a sentinel, then the RAW command to EOF. The command
# goes last and unescaped because it may be multi-line -- heredocs are routine here, and
# any escaping scheme would change what the messages print. Bash splits on the sentinel
# with parameter expansion, so nothing is ever eval'd: the values reaching the shell are
# only ever 0, 1, or an integer.
SENTINEL = "__X4_COMMAND__"

# (roots no longer come from the environment -- see main(); MSYS translates them)


ROOT_SEP = "--X4-ROOTS-END--"

#: The prefix ps_translate.ps1 gives a PARSE failure (its Translate-Text), as distinct from
#: a PowerShell that could not be found or run. Pinned by a test on both sides.
PS_PARSE_ERROR = "does not parse as PowerShell"


def ipc_roots(head: str) -> dict:
    """The root packet's `name<TAB>path` lines, as main() reads them."""
    roots = {}
    for line in head.splitlines():
        if "\t" in line:
            k, v = line.split("\t", 1)
            roots[k.strip()] = long_name(v.strip())   # a short-name ROOT too (FX-G2)
    return roots


def ipc(payload: dict, roots: dict, *, evidence: list | None = None, protected_save_folders=()):
    """(exit code, stdout bytes, facts) exactly as main() reports them to protect-bash.sh.

    K5 (v4.0 release review): the save bridge computes the facts ONCE with this and hands the
    same bytes to the Bash backend (`--facts-stdin`), instead of a second process re-parsing
    the command and re-running the PowerShell translation -- that doubled the hook's latency.
    """
    f = facts(payload, roots, evidence=evidence, protected_save_folders=protected_save_folders)
    if f.get("powershell_error"):
        # rc 4 + the reason on stdout: the caller ASKS with it. Nothing was analysed, so
        # no fact line may be emitted -- a partial stream would read as a clean pass.
        # rc 5 when the reason is that the command DOES NOT PARSE as PowerShell: that is
        # the caller's own syntax error, which the caller DENIES with the parser's message
        # so it is fixed and re-run -- hygiene spends Claude's attention, never the user's
        # (v3.3.0 release review, finding 7). Only a missing/failing PowerShell still asks.
        why = str(f["powershell_error"])
        return (5 if why.startswith(PS_PARSE_ERROR) else 4), why.encode("utf-8", "replace"), f
    g = dict(f)
    cmd = g.pop("command", "")
    g.pop("cwd", None)
    g.pop("save_lift_blockers", None)   # the save bridge's, not a Bash rule's
    out = []
    for k, v in sorted(g.items()):
        out.append(k + "\t" + _ipc_value(k, v))
    out.append(SENTINEL)
    # BINARY, and UTF-8 encoded explicitly. Python's text-mode stdout translates "\n"
    # to "\r\n" on Windows, which put a trailing CR on EVERY value: the shell then
    # compared "1\r" against "1", every predicate read false, and the hook allowed
    # everything while looking perfectly healthy. The sentinel split failed for the
    # same reason. Encoding first also means a non-cp1252 character in a command can
    # never raise mid-write and truncate the output.
    return 0, ("\n".join(out) + "\n" + cmd).encode("utf-8", "replace"), f


def main() -> int:
    import json

    # Roots arrive on STDIN, not in the environment, and that is not a style choice.
    # MSYS/Git-Bash TRANSLATES a POSIX-looking value when it hands an environment
    # variable to a NATIVE Windows process: bash exported "/tmp/x/docs" and this
    # process received "C:/Users/.../AppData/Local/Temp/x/docs", while the command text
    # it was compared against still said "/tmp/x/docs". They can never match, so every
    # path rule silently stopped firing. The README tells users they may write roots in
    # either "C:\..." or "/c/..." form, so this is a real installation, not a test
    # artefact. A byte stream is not translated.
    raw = sys.stdin.read()
    if not raw.strip():
        return 2                      # no payload: the caller must ASK, never allow
    roots = {}
    if ROOT_SEP in raw:
        head, raw = raw.split(ROOT_SEP, 1)
        roots = ipc_roots(head)
    try:
        payload = json.loads(raw)
    except ValueError:
        return 3                      # unparseable: likewise a refusal, not an allow
    rc, out, _f = ipc(payload, roots)
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.flush()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
