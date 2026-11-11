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

Most functions are pure. Mod-list exemptions verify physical file targets;
Windows 8.3 aliases use
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
import importlib.util
import re
import shutil
import stat
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


_POSIX_CLASS = re.compile(r"\[:[A-Za-z]*:\]")


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
            # A glob's POSIX class (`X4[[:space:]]Foundations`) is not a stream: no Windows name
            # holds a colon, so `[:name:]` can only be a pattern (4.1.1 review round 3, C).
            i = _POSIX_CLASS.sub(lambda m: "#" * len(m.group()), c).find(":")
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


def link_resolved(p: str, literal: bool = False, refuse: bool = True) -> str:
    """The path a write or delete of `p` reaches through an EXISTING link on it (a symlink or a Windows
    junction), when that differs from `p` as written; "" when nothing on it is a link, it is not an
    absolute drive path, or the filesystem cannot answer -- which, when `refuse`, also sets links_unresolved
    (a realpath that raised, a leaf lstat that failed with realpath showing no link); a realpath that only
    respells `p` (an 8.3 short name expanded) is no link (4.1.1 final round, F360). MEASURED at 7f4895a2: `>`, `touch` and `rm` through a junction into reference/ were allowed (3 of 3) while
    the file tools, which canonicalise, denied. The caller judges this path IN ADDITION to the
    lexical one, so the stricter verdict stands. os.path.realpath resolves the longest existing
    prefix and keeps the rest as written (non-strict); a UNC or device path is left lexical.

    `literal`: the caller knows every `$` in `p` is a literal character (single-quoted or escaped), so
    has_unresolved() does not apply. On Windows each component is resolved as Win32 opens it
    (_win_alias: an NTFS stream suffix and trailing dots/spaces dropped) and as Git Bash does (_msys_spelling;
    4.1.2: a junction named `j` U+F07C or `j.` was written through by Git Bash and read as no link). 4.1.1 review (link budget round
    2), MEASURED at a70dcedd: `touch '<junction>/libraries/wares.xml::$DATA'` read writes_reference False,
    because has_unresolved() read the literal `$DATA` as a variable, and `touch '<junction>:s'` (a write
    to it lands in a stream of the TARGET folder, MEASURED) False, because the leaf lstat'd as no link.
    `refuse=False` (operands only read checks use): past the budget the path is left lexical WITHOUT
    setting links_unresolved -- no write or delete verdict reads such an operand."""
    if not p or (not literal and has_unresolved(p)) or not is_abs(p):
        return ""
    s = p.replace(chr(92), "/")
    if os.name == "nt":
        s = _MSYS_DRIVE.sub(lambda m: m.group(1) + ":/", s)
        if not re.match(r"^[A-Za-z]:/", s):
            return ""
        # 4.1.1 review A1: a realpath cannot be interrupted, and on a drive mapped to an offline server
        # one took 21 s (MEASURED: realpath of an unreachable UNC path), so two such operands outran the
        # hook's 30 s and the call failed OPEN. Only a local drive is resolved.
        if _drive_type(s[0]) not in _LOCAL_DRIVE_TYPES:
            return ""
        # Two programs, two spellings of one text: Win32 (PowerShell, the file tools) opens `j.` as `j` and
        # rejects `j|`; Git Bash opens `j.` as written and `j|` as j U+F07C (_msys_spelling). Both are resolved,
        # the second only when it differs; when both land through links in different places, refused.
        alt = _msys_spelling(s)
        s = _win_alias(s)
        real = _resolve_spelling(s, refuse)
        if alt == s:
            return real
        other = _resolve_spelling("//?/" + alt, refuse)
        other = other[4:] if other.startswith("//?/") else other
        if real and other and _lexical_norm(real) != _lexical_norm(_unmsys(other)):
            _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse
        return real or other
    return _resolve_spelling(s, refuse)


#: Characters Git Bash (the MSYS runtime) writes as U+F000 plus their code, because Win32 forbids them in
#: a name. MEASURED 2026-11-11 with Git Bash's own `touch`: `|*?<>:` and 0x01-0x1f each became U+F0xx; `"`
#: (in the runtime's documented set, not measured here: the probe could not pass it in argv) is included.
_MSYS_MAPPED = frozenset('|*?<>:"' + "".join(chr(i) for i in range(1, 32)))


def _msys_spelling(s: str) -> str:
    """`s` (an absolute `C:/...` path with slashes) as Git Bash names it on disk: each component after the
    drive with its _MSYS_MAPPED characters as U+F000 plus their code, and trailing dots and spaces KEPT
    (MEASURED: `pq.` and `pq ` are written as named, where Win32 opens `pq`). For a file-system call, this
    spelling needs the `\\\\?\\` prefix, which keeps Win32 from stripping those trailing characters."""
    parts = s.split("/")
    return "/".join(parts[:1] + ["".join(chr(0xF000 + ord(c)) if c in _MSYS_MAPPED else c for c in part)
                                 for part in parts[1:]])


def _unmsys(s: str) -> str:
    """_msys_spelling's mapping undone, to compare where two spellings land (`<dir>/*.xml` and `<dir>/` U+F02A
    `.xml` name one place, spelled by two programs)."""
    return "".join(chr(ord(c) - 0xF000) if 0xF000 < ord(c) < 0xF080 and chr(ord(c) - 0xF000) in _MSYS_MAPPED
                   else c for c in s)


def _native(s: str) -> str:
    """`s` for a file-system call: a `//?/` (verbatim) spelling takes backslashes, which that prefix requires."""
    return s.replace("/", chr(92)) if s.startswith("//?/") else s


def _resolve_spelling(s: str, refuse: bool) -> str:
    """link_resolved for ONE spelling of an absolute local path: a `C:/...` path as Win32 opens it, or a
    `//?/C:/...` one, opened verbatim. "" when nothing on it is a link or the filesystem cannot answer."""
    # Bounded per hook call, and inside the analysis deadline (past it the command is refused). The folder
    # is resolved once per hook call (cached), and the last component is followed only when lstat says it
    # is a link, so a long list of files in a few folders spends a few resolutions. 4.1.1 review (link
    # budget), MEASURED at 5404046c: past the bound this returned "" -- "no link" -- silently, so 64 local
    # decoys before a junction operand made a write into reference/ read as allowed. An operand past the
    # bound now sets links_unresolved, which the hook REFUSES: it is never read as "no link". The analysis
    # deadline is checked before EACH uninterruptible call (every realpath, every lstat).
    head, leaf = posixpath.split(s)
    unknown = False
    if (not leaf or leaf in (".", "..") or _SHORT_NAME.search(leaf)
            or any(c in (".", "..") for c in head.split("/"))):
        real = _realpath_budgeted(s, refuse)    # dot segments / a short leaf: the whole path, as before
    else:
        if head.endswith(":"):
            head += "/"                     # `C:` alone is the drive's CURRENT folder, never its root
        rhead = _LINK_DIRS.get(head)
        if rhead is None:
            rhead = _realpath_budgeted(head, refuse)
            if rhead is None:
                return ""
            _LINK_DIRS[head] = rhead
        if _LINK_PROBED[0] >= _MAX_LINK_PROBED:
            _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse
            return ""
        _LINK_PROBED[0] += 1
        _check_deadline()
        try:
            st = os.lstat(_native(s))
            is_link = stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)
        except (FileNotFoundError, NotADirectoryError):
            is_link = False                 # nothing there: it lands where its folder resolves
        except OSError as exc:
            # A name Win32 rejects (ERROR_INVALID_NAME 123: a glob's `*`/`?`, `<`, `|`) names no file ON THIS
            # SPELLING, so no link either; MEASURED: `<save>/*` from a PowerShell Get-ChildItem raised it. Git Bash
            # opens such a name under its own spelling, which link_resolved resolves too (_msys_spelling).
            # Anything else: cannot tell.
            is_link = unknown = getattr(exc, "winerror", None) != 123
        except ValueError:
            is_link = unknown = True        # cannot tell: resolve the whole path
        real = _realpath_budgeted(s, refuse) if is_link else rhead.rstrip("/") + "/" + leaf
    if real is None:
        return ""
    # A link is reported only when realpath's answer is not merely a respelling of `p`. On Windows realpath
    # also expands an 8.3 short name (CI, MEASURED at cba3a1ce: the runner's TEMP is C:/Users/RUNNER~1/...,
    # so a plain folder there read as a link); GetLongPathNameW expands short names and follows no link.
    same = _lexical_norm(real) == _lexical_norm(s) or (
        os.name == "nt" and _lexical_norm(real) == _lexical_norm(long_name(s)))
    if same and unknown:
        # The leaf's lstat could not answer, and realpath showed no link either. On POSIX realpath(strict=False)
        # IGNORES every lstat error (posixpath: `ignored_error = OSError`), so a link it could not read comes
        # back as written. CI, MEASURED at cba3a1ce on ubuntu: this read as "no link". Cannot tell = refused.
        _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse
    return "" if same else real


def link_head_resolved(p: str, refuse: bool = True) -> str:
    """For an operand that cannot be resolved (an expansion or a substitution in it): where its literal
    head -- the absolute components before the first one carrying either -- lands through a link, plus
    the rest as written; "" when the head is not absolute or holds no link (link_resolved). A glob or
    brace component in the head is resolved as written: realpath is non-strict (MEASURED on Windows:
    `<junction>/lib*/$X` resolves its head)."""
    parts = p.replace(chr(92), "/").split("/")
    i = next((n for n, c in enumerate(parts) if has_unresolved(c)), len(parts))
    head = "/".join(parts[:i])
    if i == len(parts) or not head or not is_abs(head):
        return ""
    real = link_resolved(head, refuse=refuse)
    return real.rstrip("/") + "/" + "/".join(parts[i:]) if real else ""


def link_entries_matched(p: str, refuse: bool = True) -> list:
    """For an operand that cannot be resolved: where it lands through each LINK its first unresolved
    component may name, plus the rest as written. The component is read as a pattern -- its literal text
    before the first expansion or substitution, then anything (`refj$X`, `ref${X}j`, `refj$(echo)` all
    start `ref`) -- and every entry of the literal head's folder that starts so and is a symlink or a
    reparse point is resolved. Re-review of 180263b8, MEASURED: `touch "<work>/refj$X/libraries/wares.xml"`
    and `rm -rf <work>/refj$(echo)` read no link, where `<reference>$X/...` is caught by the root in its text.
    On Windows the literal prefix is read through _win_alias, so `refj::$INDEX_ALLOCATION` matches refj.
    One listing is one probe and each link found one realpath of link_resolved's budget; past it
    links_unresolved (when `refuse`), never "no link". A folder that cannot be listed (absent, denied, not a
    local drive) adds nothing: the operand is judged as written, unresolved, as before this reading."""
    parts = p.replace(chr(92), "/").split("/")
    i = next((n for n, c in enumerate(parts) if has_unresolved(c)), len(parts))
    head = "/".join(parts[:i])
    if i == len(parts) or not head or not is_abs(head):
        return []
    if os.name == "nt":
        head = _MSYS_DRIVE.sub(lambda m: m.group(1) + ":/", head)
        if not re.match(r"^[A-Za-z]:", head) or _drive_type(head[0]) not in _LOCAL_DRIVE_TYPES:
            return []
        head = head if "/" in head else head + "/"
    comp = parts[i]
    cut = min(m.start() for m in (_EXPANSION.search(comp), _SUBST.search(comp)) if m)
    prefix = comp[:cut]
    if os.name == "nt":
        # Win32's and Git Bash's spelling of the prefix (link_resolved): an entry starting with either.
        prefix = tuple({f("x/" + prefix).split("/", 1)[1].casefold() for f in (_win_alias, _msys_spelling)})
    if _LINK_PROBED[0] >= _MAX_LINK_PROBED:
        _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse
        return []
    _LINK_PROBED[0] += 1
    _check_deadline()
    rest = "/".join(parts[i + 1:])
    out = []
    try:
        with os.scandir(head) as it:
            for n, e in enumerate(it):
                if n % 256 == 0:
                    _check_deadline()
                name = e.name.casefold() if os.name == "nt" else e.name
                if not name.startswith(prefix):
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue                # gone since the listing: nothing lands through it
                except OSError:
                    # A matching entry whose lstat cannot answer may be a link: refused, never "no link".
                    _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse
                    continue
                if not (stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400):
                    continue
                real = _realpath_budgeted(e.path.replace(chr(92), "/"), refuse)
                if real is None:
                    return out
                out.append(real.rstrip("/") + ("/" + rest if rest else ""))
    except OSError:
        return out
    return out


def _realpath_budgeted(s: str, refuse: bool = True):
    """os.path.realpath of `s` with slashes, inside link_resolved's per-call budget: None when the budget is
    spent (and, when `refuse`, links_unresolved is set -- a refusal, never "no link") or the filesystem
    cannot answer (a realpath that raised also sets links_unresolved, when `refuse`)."""
    _check_deadline()
    if _LINK_RESOLVED[0] >= _MAX_LINK_RESOLVED:
        _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse
        return None
    _LINK_RESOLVED[0] += 1
    try:
        return os.path.realpath(_native(s)).replace(chr(92), "/")
    except Exception:
        # A realpath that raised is a non-answer, never "no link" (CI round for 4.1.1): refused when `refuse`.
        _LINKS_UNRESOLVED[0] = _LINKS_UNRESOLVED[0] or refuse
        return None


#: link_resolved's per-hook-call bounds (4.1.1 review A1); facts() resets them. Past either bound an
#: operand is links_unresolved (refused), never "no link" (4.1.1 review, link budget).
_MAX_LINK_RESOLVED = 64
_LINK_RESOLVED = [0]
#: ...the lstat of each operand's last component (cheap on a local drive; bounded all the same).
_MAX_LINK_PROBED = 4096
_LINK_PROBED = [0]
#: ...each folder's realpath, once per hook call.
_LINK_DIRS: dict = {}
_LINKS_UNRESOLVED = [False]
_DRIVE_TYPES: dict = {}
#: GetDriveTypeW: 2 removable, 3 fixed, 5 CD-ROM, 6 RAM disk. Not resolved: 0 unknown, 1 no root
#: directory, 4 remote (a mapped network drive, whose server may be offline).
_LOCAL_DRIVE_TYPES = frozenset((2, 3, 5, 6))


def _drive_type(letter: str) -> int:
    """GetDriveTypeW of `letter:` (0 when it cannot be asked), cached per drive per hook call."""
    key = letter.upper()
    if key not in _DRIVE_TYPES:
        try:
            import ctypes
            _DRIVE_TYPES[key] = int(ctypes.windll.kernel32.GetDriveTypeW(key + ":" + chr(92)))
        except Exception:
            _DRIVE_TYPES[key] = 0
    return _DRIVE_TYPES[key]


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


def is_profile_modlist(path: str, profile: str) -> bool:
    """Exact configured mod-list or root content.xml of a numeric X4 profile.

    Paths passed by the shell parser retain its ordinary link/hard-block checks.
    This classifies only a write target; it never authorizes deletes or saves.
    """
    path = norm(path)
    if (profile and not norm(profile).endswith('/egosoft/x4')
            and path == norm(profile).rstrip('/') + '/content.xml'):
        return True
    marker = '/egosoft/x4/'
    return marker in path and bool(re.fullmatch(r'[0-9]+/content[.]xml', path.split(marker, 1)[1]))


def profile_modlist_write(path: str, physical: str, roots: dict, dialect: str,
                          source_files: list[str] | None = None) -> bool:
    """Grant the write exemption only after checking the actual filesystem target.

    Preserve case for filesystem I/O. A directory operand is not a file; a link
    to config/save/game data must not inherit the lexical content.xml exemption.
    Failure to establish a physical target retains the ordinary confirmation.
    """
    if not physical or not is_profile_modlist(path, roots.get('profile', '')):
        return False
    try:
        # Load the bridge beside THIS parser, not an ambient sys.path module
        # from another checkout. facts() also supports standalone importlib users.
        bridge_path = os.path.join(os.path.dirname(__file__), 'save_policy.py')
        key = '_x4_modlist_bridge_' + os.path.normcase(os.path.abspath(bridge_path)).encode('utf-8').hex()
        bridge = sys.modules.get(key)
        if bridge is None:
            spec = importlib.util.spec_from_file_location(key, bridge_path)
            bridge = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(bridge)
            sys.modules[key] = bridge
        target = bridge.native(physical, dialect).resolve()
        if target.exists() and (not target.is_file() or target.stat().st_nlink != 1):
            return False
        if source_files is not None:
            if len(source_files) != 1:
                return False
            source = bridge.native(source_files[0], dialect)
            if source.is_symlink() or not source.is_file():
                return False
        for key in ('reference', 'game', 'saves'):
            if roots.get(key):
                protected = bridge.native(roots[key]).resolve()
                if target == protected or protected in target.parents:
                    return False
        profile = (str(bridge.native(roots['profile']).resolve())
                   if roots.get('profile') else '')
        return is_profile_modlist(str(target), profile)
    except (OSError, ValueError, RuntimeError):
        return False


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


def _scan_ops(s: str):
    """_scan for the walks that find where a command ENDS (segments, piped_in): a character a
    backslash escapes outside quotes is literal there, as a quoted one is. `find ... -exec echo {}
    \\; -delete` is ONE command: split at the `\\;`, find lost everything after it, so the
    -delete reached no rule while the `';'` spelling was denied (4.1.1 review round 4, 7). Only
    these two walks: blank_quoted must keep the `r` of `\\rm`."""
    esc = False
    for _i, c, inq, _k in _qwalk(s):
        if esc:
            esc = False
            yield c, True
            continue
        # A quoted backslash needs no `not inq`: its closing quote always comes before the next
        # unquoted character and clears the flag (the mutation gate cannot tell the two apart).
        esc = c == chr(92)
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
    _check_deadline()                   # round 8, F1
    parts, buf, chars = [], [], list(_scan_ops(cmd))
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


#: 4.1.1: stages that write nothing themselves and may feed the final `tee` of a mod-list write.
_MODLIST_PIPE_PRODUCERS = frozenset({":", "echo", "printf", "cat"})


def _modlist_tee_pipeline(cmd: str, segs: list) -> bool:
    """`echo x | tee <profile>/content.xml` (4.1.1): ONE pipeline whose every stage but the last
    is a producer above and whose last stage is `tee` -- the same data write as `echo x > <file>`,
    which the mod-list exemption already covers. Anything joined by `;`, `&&` or `||`, a stage
    that is not one of those, or a segment walked in more than one directory, keeps the
    ordinary confirmation. Each written path still qualifies on its own (profile_modlist_write)."""
    if len(segs) < 2 or piped_in(cmd) != [False] + [True] * (len(segs) - 1):
        return False
    return (_bare_command(segs[-1], {"tee"})
            and all(_bare_command(s, _MODLIST_PIPE_PRODUCERS) for s in segs[:-1]))


def _bare_command(seg: str, names) -> bool:
    """The stage's LITERAL first word is one of `names`, and it is the command run: no wrapper (xargs, sudo, env,
    timeout, nice, command...) and no assignment prefix. verb() sees through those, and `xargs tee <modlist>` APPENDS
    its stdin to tee's files (4.1.1 review: `echo <save> | xargs tee <modlist>` truncates the save)."""
    toks = tokens(seg)
    if not toks:                # an assignment prefix (`X=1 tee`) is a first word that is not `tee`
        return False
    first = _verb_name(toks[0][0])
    return first in names and _verb_name(verb(seg)) == first


def piped_in(cmd: str) -> list:
    """For each segment segments(cmd) returns, in order: is it fed by a PIPE (`|`)?

    The same walk as segments(), recording which operator ENDED the previous part. A
    program read from STDIN only exists when something is piped in: `ls; pwsh -NoProfile`
    reads the tool's own (empty) stdin, not ls's output (v3.3.0 release review, found by
    this lane's history replay -- pairing on ANY separator asked on `command -v pwsh`).
    """
    out, buf, chars, sep = [], [], list(_scan_ops(cmd)), ""
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


def _starts_with_brace_word(s: str) -> bool:
    """`{eval,} <cmd>` opens no group: that needs a blank after the brace. It is one word bash
    brace-expands, and stripping the brace left the name `eval,}`, which no rule knows."""
    if s[:1] != "{" or s[1:2].isspace():
        return False
    first = _raw_words(s, spans=False)[:1]
    # A substitution in the word is walked on its own, and a range (`{1..3}`) names no program.
    if not first or "$" in first[0] or chr(96) in first[0] or "," not in first[0]:
        return False
    toks = tokens(first[0])
    return bool(toks) and toks[0][0] in _BRACE_WORDS


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
    while s[:1] in ("(", "{") and not _starts_with_brace_word(s):
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
    r"""Decode one ANSI-C quoted body.

    The hex and octal forms are the ones that matter: two different spellings of
    `rm` that reached `_verb_name` as literal escape text and came out as `x6d`
    and `155`. B2, MEASURED as a total bypass of all three hard blocks.

    The result STOPS at the first NUL, however it is spelled (`\x00`, `\0`, `\000`,
    `\400`, `\u0000`, `\U00000000`, `\c@`, or a raw NUL): bash builds a C string, so
    `$'<reference>\x00junk'` IS `<reference>` -- and was read as the sibling
    `<reference>junk` (MEASURED in review of 6cbeb825; the gap is older than that).
    `\c` and `\u`/`\U` up to 0x7F are decoded as bash does (MEASURED with Git Bash
    `printf`), so `$'<ref>\u002flibraries'` is a path inside reference/; a `\u`/`\U`
    above 0x7F is locale-dependent in bash and is kept as written.
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
        if n in 'xuU':
            h = ''
            j = i + 2
            width = {'x': 2, 'u': 4, 'U': 8}[n]
            while j < len(body) and len(h) < width and body[j] in '0123456789abcdefABCDEF':
                h += body[j]
                j += 1
            if h and (n == 'x' or int(h, 16) <= 0x7F):
                # \x is a BYTE. \u/\U above 0x7F depend on the locale: MEASURED in Git
                # Bash (no LANG), `\u00e9` became one byte, `\uD800`, `\U0010FFFF` and
                # `\u041B` stayed as their escape TEXT -- whose backslash is a separator
                # there. Such an escape is left exactly as written (the pre-change reading).
                out.append(chr(int(h, 16)))
                i = j
                continue
        elif n == 'c' and i + 2 < len(body):
            x = body[i + 2]
            j = i + 3
            if x == chr(92) and j < len(body) and body[j] == chr(92):
                j += 1                          # bash: `\c\\` is control-backslash
            out.append(chr(127) if x == '?' else chr(ord(x.upper()) & 0x1F))
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
    return ''.join(out).split(chr(0), 1)[0]


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


def tokens(seg: str, *, literal_expansions: dict | None = None) -> list[tuple[str, bool]]:
    """(text, was_quoted) per token. Quotes are stripped from the text; a
    backslash-escaped space JOINS, which is why `X4\\ Foundations` must stay one token
    -- the bash tokeniser split it and the game-delete block stopped firing.

    A word whose unquoted part holds a brace expansion is recorded in _BRACE_WORDS (C1).

    `literal_expansions`, when given, receives text -> "no `$` in this word is
    expansion-active" (every `$` single-quoted, escaped, or ANSI-C decoded; no unquoted
    glob or brace character). A text spelled both ways in one segment is False: one
    occurrence never qualifies another (see facts' literal_dollar_path)."""
    out, buf, quoted, started = [], [], False, False
    mbuf = []                               # buf with every quoted/escaped char as NUL
    xbuf = []                               # only expansion-active characters survive
    q = ""
    unterminated = False                    # a `$'...` run that never closed
    i = 0

    def emit():
        text = "".join(buf)
        if "{" in mbuf and _has_brace_expansion("".join(mbuf)):
            _BRACE_WORDS.add(text)
        if literal_expansions is not None:
            # An UNQUOTED glob or brace character also disqualifies the word: the shell
            # may replace it with other text, so its spelling here proves nothing.
            literal_expansions[text] = (literal_expansions.get(text, True)
                                        and not has_unresolved("".join(xbuf))
                                        and not any(ch in mbuf for ch in "*?[{"))
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
                xbuf.append(chr(0))
            else:
                buf.append(c)
                mbuf.append(chr(0))
                xbuf.append(chr(0) if q == "'" else c)
        elif c in "\"'":
            # ANSI-C / locale quoting: `$'rm'` and `$"rm"` are the word `rm`. The `$`
            # is a quoting SIGIL, not part of the name. Without this the token was
            # `$rm` AND flagged quoted, so verb()'s `if quoted: return t` handed every
            # verb-keyed rule a name no rule has ever heard of -- a total bypass.
            # Only an UNQUOTED, unescaped `$` is a sigil: in `'dir$'"/x"` and `\$'x'` the
            # `$` is a character of the name (literal-dollar port, 6cbeb825).
            dollar = bool(buf) and buf[-1] == "$" and mbuf[-1] == "$"
            if dollar:
                buf.pop()
                mbuf.pop()
                xbuf.pop()
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
                xbuf.extend(chr(0) * len(dec))
                quoted = started = True
                unterminated = unterminated or j >= len(seg)
                i = j + 1
                continue
            q = c
            quoted = started = True
        elif c == chr(92) and i + 1 < len(seg):
            i += 1
            buf.append(seg[i])          # \<space> joins; \x keeps x
            mbuf.append(chr(0))         # an escaped `{` / `,` is literal (C1)
            xbuf.append(chr(0))
            started = True
        elif c.isspace():
            if started:
                emit()
            buf, quoted, started = [], False, False
            mbuf = []
            xbuf = []
        else:
            buf.append(c)
            mbuf.append(c)
            xbuf.append(c)
            started = True
        i += 1
    if started:
        emit()
        if (q or unterminated) and literal_expansions is not None:
            # An UNTERMINATED quote: what a shell does with this word is not this parse's
            # to say, so it proves nothing literal.
            literal_expansions[out[-1][0]] = False
    return out


def words(seg: str) -> list[str]:
    return [t for t, _ in tokens(seg)]


# -------------------------------------------------------------- assignments
_ASSIGN = re.compile(r"(?:^|[;&|\s])([A-Za-z_][A-Za-z0-9_]*)=")


def _builtin_assignment(seg: str) -> dict:
    """What `printf -v NAME FORMAT ARGS` and `read NAME <<< WORD` assign. MEASURED: `printf -v P
    %s <reference>; rm -rf "$P"` carried no guard flag. A format _printf_render cannot reproduce
    gives nothing here. Read by _value_readings, never by the assignment table: `P=<reference>;
    false && printf -v P %s /tmp; rm -rf "$P"` must keep the verdict it has."""
    v = verb(seg)
    if v not in ("printf", "read"):
        return {}
    at = _verb_at(seg)
    if at is None:
        return {}
    if v == "read":
        hs = _here_string(seg)
        args = [t for t, _q in tokens(seg)[at + 1:]]
        name, i = "", 0
        while hs and i < len(args) and not name:
            t = args[i]
            i += 1
            if t.startswith("<"):
                break
            if len(t) < 2 or t[0] != "-":
                name = t
            elif t[1] == "a" and len(t) > 2:
                name = t[2:]
            elif t[-1] in "pdnNtui":
                i += 1
        return {name: hs} if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else {}
    args = _drop_redirects([t for t, _q in tokens(seg)[at + 1:]])
    if args[:1] == ["-v"]:
        name, args = (args[1] if len(args) > 1 else ""), args[2:]
    elif args[:1] and args[0].startswith("-v"):
        name, args = args[0][2:], args[1:]
    else:
        return {}
    if args[:1] == ["--"]:
        args = args[1:]
    if not args or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return {}
    value = _printf_render(args[0], args[1:])
    return {} if value is None else {name: value}


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


# ------------------------------------------------- literal `$` in a path (4.1.1)
# Ported by hand from session/literal-dollar-v2 (6cbeb825, 51129cde, ed667b4a): a `$` that
# EVERY shell layer keeps literal -- single-quoted, escaped, ANSI-C decoded -- is part of the
# name, so `rm -rf '<root>$R1234'` names the sibling `<root>$R1234` (a Recycle Bin item)
# rather than an unknown scope. Only texts a shell parses exactly as written qualify (see
# facts: exact_texts), and nothing qualifies in a command that can re-run a word as shell
# text. Every rule below only ever takes the qualification AWAY: the old, unresolved reading.

#: A command that DEFINES something able to re-run a later word as shell text: a function
#: (`rm(){ eval command rm "$@"; }`), an alias (`alias rm='eval rm'`), a sourced file, a
#: trap, a builtin switched off, or their PowerShell counterparts. Once any is present
#: `rm -rf '<ref>$X'` may expand `$X` after all (MEASURED in Git Bash: the function form
#: removed `<ref>`), so NO text in such a command is exact (see facts). Matched over the
#: raw command, quotes and all: a match inside a string only costs strictness.
_REDEFINES = re.compile(
    r"(?:^|[\s;&|({`!])(?:function|alias|source|trap|enable|shopt|declare|typeset|export|"
    r"readonly|local|unalias|unset|hash|filter|set-alias|new-alias|sal|nal|"
    r"import-module|ipmo|set-item|new-item|ni|si|"
    # PowerShell's eval: a definition built at run time evades every pattern here, as
    # `eval $'rm\x28\x29 ...'` does in bash (4.1.1 port: no literal anywhere beside one).
    r"iex|invoke-expression|icm|invoke-command|new-module|nmo|add-type)(?=[\s;&|)}(\"']|$)"
    r"|(?:^|[\s;&|({`!])\.(?=\s)"
    r"|[^\s;&|()<>'\"]\s*\(\s*\)"
    r"|(?:function|alias|variable):|scriptblock|invokecommand|newscriptblock"
    r"|BASH_ENV|ENV=|PROMPT_COMMAND|BASH_FUNC_|BASH_ALIASES|BASH_CMDS",
    re.IGNORECASE)


def _redefines_commands(text: str) -> bool:
    """Might `text` define a function or alias -- anything a LATER word can run through?"""
    return bool(_REDEFINES.search(text or ""))


#: A command name that is a plain word: nothing a quote, escape or expansion built.
_PLAIN_NAME = re.compile(r"[A-Za-z0-9_./:+@%,~-]+")
#: A shell flag that loads startup files or enables aliases (`-i`, `-l`, `-O`/`+O`).
_SHELL_RC_FLAG = re.compile(r"^(?:[-+][A-Za-z]*[ilO][A-Za-z]*|--login|--rcfile|--init-file"
                            r"|--rcfile=.*|--init-file=.*)$")


def _no_literal_anywhere(texts) -> bool:
    """BROAD rule: may anything in this command re-run a word as shell text, so that NO
    `$` in it can be called literal? True when any text (the command, every carried or
    decoded inner command) holds: a definition `_REDEFINES` sees; an `eval`, `source` or
    `.` anywhere; a command name that is not a plain word (a variable, a substitution, a
    quoted or escaped spelling such as `"rm"'()'` -- a definition can be BUILT at run time,
    `eval $'rm\\x28\\x29 {...}'`); or a shell started with startup files or aliases
    (`-i`, `-l`, `-O`, `+O`, `--rcfile`, `--init-file`, `--login`). Over-matching only
    restores the old, stricter reading."""
    for text in texts:
        if not text:
            continue
        if _redefines_commands(text):
            return True
        for seg in segments(text):
            words_ = [t for t, _q in tokens(seg)]
            if any(_verb_name(t) in ("eval", "source") for t in words_):
                return True
            name = _verb_token(seg) or ""
            if name and (name == "." or has_unresolved(name) or not _PLAIN_NAME.fullmatch(name)):
                return True
            if any(_verb_name(t) in _SHELLS for t in words_) and any(
                    _SHELL_RC_FLAG.match(t) for t in words_):
                return True
    return False


#: sudo options that make it hand the command to a SHELL's `-c` (`-s`, `-i` and long
#: forms, in any cluster such as `-is`, `-su root`, `-Es`). sudo backslash-escapes every
#: character there EXCEPT `$`, so `sudo -s rm -rf '<ref>$X'` expands `$X` in that shell.
_SUDO_SHELL = re.compile(r"^(?:-[A-Za-z]*[si][A-Za-z]*|--shell|--login)$")


def _reparsing_wrapper(seg: str) -> bool:
    """Does a wrapper in this segment run the command through a shell that re-parses it
    (sudo -s / -i)? Any option token after a `sudo` word counts -- an over-match is only
    stricter. Such a segment is never exact, nor is a `-c` string it carries."""
    words_ = [t for t, _q in tokens(seg)]
    for k, w in enumerate(words_):
        if _verb_name(w) == "sudo":
            return any(_SUDO_SHELL.match(t) for t in words_[k + 1:] if t.startswith("-"))
    return False


def exact_heredoc_bodies(cmd: str) -> set:
    """Top-level heredoc bodies that a shell parses EXACTLY as written: a QUOTED delimiter
    (`<<'EOF'`, so nothing in the body was expanded) on an opening line that is ONE plain
    shell command reading its program from stdin, with no other redirection that could
    replace that stdin. Anything else -- an unquoted delimiter, a body reaching the shell
    through a pipe (`cat <<'EOF' | sed ... | bash` may rewrite it), a substitution, a
    second heredoc on the line -- is not proven exact, so its `$` stays unresolved.
    Bodies are returned as facts() keeps them (comments stripped)."""
    docs, _kept = _heredoc_walk(cmd)
    exact, loose = set(), set()
    for opener, _term, expands, body in docs:
        text = strip_comments(chr(10).join(body))
        segs_ = segments(opener)
        plain = blank_quoted(opener)
        ok = (not expands and len(segs_) == 1 and len(_heredoc_opens(opener)) == 1
              and plain.count("<") == 2 and ">(" not in plain and "$(" not in plain
              and chr(96) not in plain)
        if ok:
            sg = _unwrap(segs_[0])
            ok = (sg == segs_[0].strip() and _verb_name(verb(sg)) in _EXACT_SHELLS
                  and _reads_stdin_program(sg) and not has_unresolved(_verb_token(sg) or ""))
        (exact if ok else loose).add(text)
    return exact - loose


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
    if n == "gfind":
        n = "find"                      # GNU find's name on macOS and the BSDs (4.1.1 review round 7, 3)
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
             "-C", "--close-from", "-r", "--role", "-t", "--type", "-U", "--other-user",
             "-D", "--chdir", "-R", "--chroot", "-T", "--command-timeout",
             "-a", "--auth-type", "-c", "--login-class"},
    "exec": {"-a"},
    "doas": {"-u", "-C"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    # `-i` and `--replace` take NO separate value (GNU: an optional one ATTACHED, `-iX`, `--replace=X`);
    # listed here they ate the verb (4.1.1 review A4). `-e`/`-l` likewise attach theirs and are not listed.
    "xargs": {"-I", "-n", "--max-args", "-P", "--max-procs",
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
    _check_deadline()                   # round 8, F1: the word walk is the hot path of every reader
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


#: Value options _verb_word learned after the first releases. Before that their value read as the
#: command, and `_value_word_readings` keeps that reading judged.
_LATER_VALUE_OPTS = {
    "exec": frozenset({"-a"}),
    "sudo": frozenset({"-D", "--chdir", "-R", "--chroot", "-T", "--command-timeout", "-a", "--auth-type",
                       "-c", "--login-class"}),
}


def _verb_word(seg: str, learned: bool = True) -> tuple[str, int | None]:
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
            opts_ = _WRAPPER_VALUE_OPTS.get(wrapper, ())
            if t in opts_:
                want_value = learned or t not in _LATER_VALUE_OPTS.get(wrapper, ())
            elif learned and len(t) > 2 and t[1] != "-":
                # ...or a CLUSTER of short flags ending in one that takes a value: `sudo -su
                # root rm ...` -- `root` is -u's value, not the command (MEASURED on
                # session/literal-dollar-v2, 51129cde: verb() read `root`, so even `sudo -su root
                # rm -rf <reference>` reached no delete rule). getopt order: the FIRST
                # value-taking letter ends the cluster and the rest of the word is its value
                # (`-uroot`); only when it is the LAST letter does the value come next. sudo's
                # `-h` takes no separate value in a cluster (`-Eh` is help). learned=False keeps
                # the old reading, which _value_word_readings still judges.
                k_ = next((k for k, ch in enumerate(t[1:], 1) if "-" + ch in opts_
                           and not (wrapper == "sudo" and ch == "h")), None)
                want_value = k_ == len(t) - 1
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


#: A glob bash would expand in a command name: `ev?l`, `r[m]`, `/bin/r*`. A bare `[` or `[[` is the
#: test command, which is why a bracket needs its closing partner.
_GLOB_NAME = re.compile(r"[*?]|\[[^\]]+\]")
_GLOB_NAME_CHARS = re.compile(r"[A-Za-z0-9_.:/" + chr(92) + r"+*?\[\]!^-]+")


def _glob_program(seg: str, t: str) -> bool:
    """Is the command name an UNQUOTED glob whose BASENAME could spell a verb that changes files or
    re-reads text? MEASURED: `ev?l 'rm -rf <reference>'` and `r? -rf <reference>` carried no guard
    flag. A glob in the directory leaves a literal name, which verb() already reads
    (`/c/Python3*/python`), and one that spells nothing dangerous (`./scripts/*.sh`) is left alone:
    asking on those cost a prompt for a read-only command."""
    if not ("*" in t or "?" in t or "[" in t) or not _GLOB_NAME_CHARS.fullmatch(t):
        return False
    w = _verb_word_text(seg)[0]
    bare = "".join(" " if inq else c for _i, c, inq, _k in _qwalk(w))
    if not _GLOB_NAME.search(bare):
        return False
    name = _verb_name(t)
    if not _GLOB_NAME.search(name):
        return False
    import fnmatch                       # only a glob command name pays for this
    return any(fnmatch.fnmatchcase(v, name) for v in _GLOB_SPELLABLE)


def _dynamic_program(seg: str) -> bool:
    """F10 (v4.0): is this segment's COMMAND NAME unidentifiable here -- a variable this
    command does not assign a bare name to (`$R`, `"$R"`, `${A[0]}`), or a substitution?
    Call it on a segment resolve_verb() has already seen. A segment cut inside an open
    substitution is not a command position (the substitution is walked on its own), the
    same exclusion _subst_verb makes."""
    t = _verb_token(seg)
    if not t or not (has_unresolved(t) or _glob_program(seg, t)):
        return False
    w, _rest = _verb_word_text(seg)
    # SINGLE-quoted text is literal: `'$GAME/x'` names a program called that, it expands
    # nothing (MEASURED in the corpus replay: python source lines carried as commands read
    # `'... $GAME ...'` as an unknown name and asked).
    if w and not has_unresolved(blank_single_quoted(w)) and not _glob_program(seg, t):
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
        # An escaped one too, which an eval re-reads as a separator: _requoted reads it with each
        # quoted word kept whole (4.1.1 review round 5, 1).
        joined = " ".join(t for t, _q in toks) if any(
            q and _EVAL_SPLITS.search(t) for t, q in toks) else rest
        for r in [rest, joined] + _requoted(toks, _escapes_a_split(rest)):
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


def _save_lock_decision(seg: str) -> bool:
    """Does this segment change the optional save locks? 4.1.0 review F9: on, off and unlock all belong to the user, so
    an agent's ask, in both directions: `on` makes locks only an elevated prompt can lift. `agent-harness.py save-lock`
    RUN by an interpreter or as a program of its own with any action but status, an action the guard cannot read
    included; and `setup.sh --save-lock` run by a shell. Accepted residual, as for lifts_reference_deny: an opaque
    interpreter (`python -c`) is not seen -- this stops accidents, not intent. The switch file itself is kept by the
    save guard."""
    toks = [t.lower() for t in tokens_of(seg)]
    v = _verb_name(verb(seg)).lower()
    base = lambda t: t.replace(chr(92), "/").rsplit("/", 1)[-1]
    if v == "agent-harness.py" or v.startswith(("python", "py", "uv", "uvx", "conda", "mamba", "micromamba", "pipx", "pipenv",
                                                "poetry", "pdm", "hatch", "rye", "pixi", "xargs")) or has_unresolved(v):
        for i, t in enumerate(toks):
            if base(t) == "agent-harness.py" and toks[i + 1:i + 2] == ["save-lock"]:
                action = _refguard_action(toks[i + 2:])
                return "xargs" in toks[:i] or has_unresolved(action) or action in ("on", "off", "unlock")
    if v in ("bash", "sh", "setup.sh") or has_unresolved(v):
        at = 0 if v == "setup.sh" else next((i for i, t in enumerate(toks) if base(t) == "setup.sh"), -1)
        return at >= 0 and "--save-lock" in toks[at:]
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


def _robocopy_ops(seg: str) -> list:
    """A robocopy's non-switch words: SRC, DST, then any file names."""
    toks = tokens_of(seg)
    k = _verb_at(seg, 0)                # P1: see _verb_at
    return [t for t in toks[k + 1:] if not _ROBO_SWITCH.match(t)
            and not t.startswith(("<", ">")) and not re.match(r"^\d+>", t)]


def robocopy_effects(seg: str) -> tuple:
    """(copy destinations, deleted-content targets, moved sources) of a robocopy.
    `robocopy SRC DST /MIR` (or /PURGE) deletes what DST has and SRC lacks -- with an
    empty SRC, everything; /MOV and /MOVE take the source away."""
    if verb(seg) != "robocopy":
        return [], [], []
    toks = tokens_of(seg)
    k = _verb_at(seg, 0)                # P1: see _verb_at
    sw = [t.lstrip("/").split(":", 1)[0].lower() for t in toks[k + 1:] if _ROBO_SWITCH.match(t)]
    ops = _robocopy_ops(seg)
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
    return _producer_items(prev)


def _producer_items(prev) -> list[str]:
    """What a producer hands an `xargs` (xargs_feed): an echo/printf's words, a find's or a listing's
    folders as `<folder>/${XARGS_ITEM}`."""
    if prev is None:
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
        # find's PATHS only -- its predicates' values are not places. None is `.` (GNU find),
        # and a bare `ls` lists `.` too (4.1.1 review round 2, F3).
        return [o.rstrip("/" + chr(92)) + "/${XARGS_ITEM}" for o in _find_roots(prev) or ["."]]
    return [o.rstrip("/" + chr(92)) + "/${XARGS_ITEM}"
            for o in _operands(prev) or (["."] if pv in ("ls", "dir") else [])]


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
#: The filters whose value names an entry, so the text can decide what they match.
_FIND_NAME_TESTS = {"-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename",
                    "-regex", "-iregex"}


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


def _find_delete(seg: str, default_root: bool = False) -> tuple:
    """(paths a `find` deletes under, whether a NARROWING filter scopes it). `default_root`: a
    find with no path searches `.`, as GNU find does (the archive rule only, 4.1.1 round 2 F3)."""
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
            # The command, or a shell carrying one (`-exec bash -c '<cmd>' _ {} ;`): _exec_deletes,
            # shared with the archive rule's expression reader.
            # Only this action's words, up to its terminator (round 8, F1): the rest of the expression
            # read as one command made 200 -exec actions quadratic (6 s).
            if t in _FIND_EXEC and _exec_deletes(toks[i + 1:_exec_end(toks, i)]):
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
    return (out or (["."] if default_root else [])), filtered


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


#: Compressors and decompressors that REPLACE their operand by default (`gzip x` leaves only `x.gz`,
#: `gunzip x.gz` only `x`), unless told to keep it, write to stdout, test or list (4.1.1 review round
#: 7, 2; round 8 added the decompressors). zstd, unzstd and brotli keep it unless given --rm.
_REPLACING = frozenset({"gzip", "gunzip", "bzip2", "bunzip2", "xz", "unxz", "lzma", "unlzma", "lzip", "compress",
                        "uncompress", "pigz", "unpigz", "pbzip2", "zstd", "unzstd", "brotli"})
_RM_TO_REPLACE = frozenset({"zstd", "unzstd", "brotli"})
_KEEPING_LONG = frozenset({"--keep", "--stdout", "--to-stdout", "--test", "--list"})
#: Their short options that take a VALUE, attached (`-S.lck`) or as the next word (round 8, F3: the
#: letters of `-S.lck` and `-Ccrc32` were read as flags, so `c` and `k` in them hid the replacement).
_REPLACING_VALUE_SHORT = {"gzip": "S", "gunzip": "S", "pigz": "Sbp", "unpigz": "Sbp", "xz": "CFMST",
                          "unxz": "CFMST", "lzma": "CFMST", "unlzma": "CFMST", "zstd": "oDT", "unzstd": "oDT",
                          "brotli": "oqwS", "lzip": "bmoS", "pbzip2": "pbm", "compress": "b"}
_REPLACING_VALUE_LONG = frozenset({"--suffix", "--format", "--check", "--threads", "--memlimit", "--memory",
                                   "--output", "--dict", "--quality", "--lgwin", "--block-size"})
_DECOMPRESSING = {"gunzip", "bunzip2", "unxz", "unlzma", "uncompress", "unpigz", "unzstd"}
_PACKED_SUFFIX = re.compile(r"[.](?:gz|z|xz|lzma|bz2|lz|Z|zst|br|tgz)$", re.IGNORECASE)


def _replacing_parse(seg: str):
    """(name, short flags, long flags, operands) of a replacing (de)compressor, or None."""
    v = verb(seg)
    if v not in _REPLACING:
        return None
    toks = tokens(seg)
    shorts, longs, ops, i, k = set(), set(), [], _verb_at(seg, 0) + 1, len(toks)
    values = _REPLACING_VALUE_SHORT.get(v, "")
    while i < k:
        t, q = toks[i]
        i += 1
        if q or not t.startswith("-") or t == "-":
            ops.append(t)
            continue
        if t == "--":
            ops += [x for x, _q in toks[i:]]
            break
        if t.startswith("--"):
            name = t.partition("=")[0]
            longs.add(name)
            if name in _REPLACING_VALUE_LONG and "=" not in t:
                i += 1
            continue
        for j, c in enumerate(t[1:]):
            if c in values:
                if not t[j + 2:]:
                    i += 1                  # the value is the next word
                break
            shorts.add(c)
    return v, shorts, longs, _drop_redirects(ops)


def replaced_sources(seg: str) -> list[str]:
    """What a replacing (de)compressor, `rename`, `zip -m` or `tar --remove-files` takes away: each a
    move of its operand, as `mv` is. `rename FROM TO FILE...` (util-linux) and `rename EXPR FILE...`
    (perl) alike: every operand after the first may be a file, read conservatively."""
    v = verb(seg)
    if v == "rename":
        return _operands(seg)[1:]
    if v == "zip":
        toks = tokens(seg)
        flags = [t for t, q in toks if not q and t.startswith("-") and not t.startswith("--")]
        moves = any("m" in f[1:] for f in flags) or "--move" in tokens_of(seg)
        return _operands(seg)[1:] if moves else []
    if v == "tar":
        return _tar_removed(seg)
    got = _replacing_parse(seg)
    if got is None:
        return []
    v, shorts, longs, ops = got
    if v in _RM_TO_REPLACE and "--rm" not in longs:
        return []
    if longs & _KEEPING_LONG or shorts & set("kctl"):
        return []
    return ops


def decompress_outputs(seg: str) -> list[str]:
    """The files a replacing decompressor WRITES: each operand without its packed suffix
    (`gunzip <game>/01.cat.gz` writes `<game>/01.cat`; round 8, F3)."""
    got = _replacing_parse(seg)
    if got is None:
        return []
    v, shorts, longs, ops = got
    if not (v in _DECOMPRESSING or "d" in shorts or longs & {"--decompress", "--uncompress"}):
        return []
    if longs & _KEEPING_LONG - {"--keep"} or shorts & set("ctl"):
        return []
    return [_PACKED_SUFFIX.sub("", o) for o in ops if _PACKED_SUFFIX.search(o)]


def _tar_removed(seg: str) -> list[str]:
    """The files `tar --remove-files` takes away: its operands, without the archive (`-f X`, `fX`
    in an old-style cluster) or a `-C <dir>` value."""
    toks = tokens(seg)
    words = [t for t, _q in toks[_verb_at(seg, 0) + 1:]]
    if "--remove-files" not in words:
        return []
    out, i = [], 0
    while i < len(words):
        w = words[i]
        i += 1
        if w.startswith("--"):
            if w in ("--file", "--directory", "--exclude", "--files-from") and "=" not in w:
                i += 1
            continue
        if w.startswith("-") or (i == 1 and re.fullmatch(r"[A-Za-z]+", w) and "f" in w):
            body = w.lstrip("-")
            for j, c in enumerate(body):
                if c in "fCbHKLNTVX":
                    if not body[j + 1:]:
                        i += 1
                    break
            continue
        out.append(w)
    return out


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


_DIRSTACK_N = re.compile(r"^[+-][0-9]+$")


def _dirstack_forms(v: str, words: list, cwd: str, from_seed: bool, stack: list, join=None):
    """(cwd, from_seed, stack) after the pushd/popd forms that are not "push this directory" or "pop the top", or
    None for those two (cwd_lanes reads them). 4.1.1 final round, MEASURED at 7f4895a2: from `cd <reference>; pushd
    <mod>`, a bare `pushd` (swap the top two), `pushd +1` (rotate) and `pushd -n <dir>` (add <dir> below the top, no
    cd) all leave bash in <reference>, and each was read as staying in <mod> (or moving to `<mod>/+1`), so a relative
    delete there was advised instead of denied. bash's list is the directory in force, then `stack` from its end;
    +N counts from the left of that list, -N from the right, and a number past its end changes nothing (bash refuses)."""
    if v == "pushd" and not words:
        if stack:                                     # swap the top two
            top = stack.pop()
            stack.append((cwd, from_seed))
            return (top[0], top[1], stack)
        return (cwd, from_seed, stack)
    first = words[0] if words else ""
    if v == "pushd" and first == "-n":
        if len(words) > 1 and not _DIRSTACK_N.match(words[1]):
            stack.append(((join or join_cwd)(cwd, words[1]), from_seed and not is_abs(words[1])))
        return (cwd, from_seed, stack)
    if v == "popd" and first == "-n":
        if stack:
            stack.pop()                               # the entry below the top goes, no cd
        return (cwd, from_seed, stack)
    if not _DIRSTACK_N.match(first):
        return None
    dirs = [(cwd, from_seed)] + stack[::-1]
    n = int(first[1:])
    idx = n if first[0] == "+" else len(dirs) - 1 - n
    if not 0 <= idx < len(dirs):
        return (cwd, from_seed, stack)
    if v == "pushd":                                  # rotate: entry idx becomes the top
        dirs = dirs[idx:] + dirs[:idx]
    elif idx == 0:                                    # popd +0: the top goes, as a plain popd
        dirs = dirs[1:] or dirs
    else:                                             # popd +N: entry N goes, no cd
        dirs = dirs[:idx] + dirs[idx + 1:]
    return (dirs[0][0], dirs[0][1], dirs[1:][::-1])


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
        if v in ("pushd", "popd"):
            words = _operands(seg, include_flags=True)
            if v == "popd" and words and has_unresolved(words[0]):
                # A pop that may not happen (4.1.1 review A3: the PowerShell translator's `popd
                # ${X4_PS_LOCATION_STACK:+-n}`): this lane takes one of the operand's values.
                words = ([tgt] if tgt else []) + words[1:]
            moved = _dirstack_forms(v, words, cwd, from_seed, stack,
                                    physical_join if physical else join_cwd)
            if moved is not None:
                return moved
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
        if v == "popd":
            # Only an operand with several values splits the lanes; a plain popd form is read in step().
            ops = [o for o in _operands(seg, include_flags=True)[:1] if has_unresolved(o)]
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


# ------------------------------------------------- base-game and official DLC archives
# The file tools refuse a write to <game>/*.cat|dat and <game>/extensions/ego_dlc_*/*.cat|dat;
# a shell write, move or delete of the same file was only advised (some were plain allows), and
# only a Steam verify restores one. MEASURED 2026-11-05 through the deployed hooks, same at v4.0.0.
# A MOD's own ext_*.cat is not here: packing and retiring a mod stays with the deploy rules.
# Only ever fed text; the disk is never read, and an unresolved folder proves nothing.
_ARCHIVE_EXT = re.compile(r"[.](cat|dat)$")
#: No list of archive NAMES: a pattern is an archive when SOME name ending .cat or .dat could
#: match it. The stems differ per DLC and grow with patches (READ 2026-11-05: ventures alone has
#: ext_v610, ext_v620, ext_v700, ext_v710 and two _diff_v900 pairs), so a sample list went stale
#: once already (4.1.1 review round 2, F1).
_ARCHIVE_ENDINGS = (".cat", ".dat")
#: The parts of a glob, as tokens: ("lit", c) one character; ("one", neg, chars, ranges) one
#: character in (or, neg, outside) the set; ("star", neg, chars, ranges) any run of such characters.
_GLOB_ALL = ("star", True, frozenset(), ())               # anything, `/` included
_GLOB_SEGMENT = ("star", True, frozenset("/"), ())         # anything inside one path segment
#: A name on disk: one segment, lower case. Every archive and DLC folder name READ is lower case.
_GLOB_DISK_NAME = ("star", True, frozenset("/"), (("A", "Z"),))


def _is_pattern(text: str) -> bool:
    return any(c in text for c in _OPERAND_PATTERN_CHARS)


def _glob_lit(text: str) -> list:
    return [("lit", c) for c in text]


def _glob_parse(pat: str, slash: bool, escapes: bool, caret: bool) -> list:
    """A glob as tokens. `slash`: `*`, `?` and a bracket may match `/` (find -path), or not (a
    shell path, find -name). `escapes`: a backslash quotes the next character. `caret`: `[^` is a
    negation, as `[!` is."""
    out, i, n = [], 0, len(pat)
    noslash = frozenset() if slash else frozenset("/")
    while i < n:
        c = pat[i]
        if c == chr(92) and escapes and i + 1 < n:
            out.append(("lit", pat[i + 1]))
            i += 2
        elif c == "*":
            if not (out and out[-1][0] == "star"):
                out.append(("star", True, noslash, ()))
            i += 1
        elif c == "?":
            out.append(("one", True, noslash, ()))
            i += 1
        elif c == "[":
            j = i + 1
            neg = j < n and (pat[j] == "!" or (caret and pat[j] == "^"))
            j += neg
            # A `]` first in the set is a member. A POSIX class (`[:alpha:]`, `[=a=]`, `[.a.]`)
            # holds a `]` of its own, so the set closes after it, not at it (4.1.1 review round 3, B).
            k, close, has_class = (j + 1 if j < n and pat[j] == "]" else j), -1, False
            while k < n:
                if pat[k] == "[" and k + 1 < n and pat[k + 1] in ":=.":
                    end = pat.find(pat[k + 1] + "]", k + 2)
                    if end >= 0:
                        has_class, k = True, end + 2
                        continue
                if pat[k] == "]":
                    close = k
                    break
                k += 1
            if close < 0:
                out.append(("lit", c))
                i += 1
                continue
            if has_class:
                # Read as ANY one character, negated or not: wider than the set, so a meet it
                # finds may be false but none is missed. Marked, so _glob_covers never relies on it.
                out.append(("one", True, noslash, (), "class"))
                i = close + 1
                continue
            body, chars, ranges, m = pat[j:close], set(), [], 0
            while m < len(body):
                if m + 2 < len(body) and body[m + 1] == "-":
                    ranges.append((body[m], body[m + 2]))
                    m += 3
                else:
                    chars.add(body[m])
                    m += 1
            if neg:
                chars |= noslash
            out.append(("one", neg, frozenset(chars), tuple(ranges)))
            i = close + 1
        else:
            out.append(("lit", c))
            i += 1
    return out


def _glob_accepts(t: tuple, c: str) -> bool:
    if t[0] == "lit":
        return t[1] == c
    hit = c in t[2] or any(lo <= c <= hi for lo, hi in t[3])
    return hit != t[1]


#: The bound on one _glob_meet: characters tried. Its cost is positions x positions x stand-in
#: characters, and a crafted pattern of 3,000 one-range sets ran the hook 37.6 s (4.1.1 review round
#: 3, A, MEASURED), past its 30 s timeout -- and a hook that times out does not block. Past the bound
#: the answer is "may meet", which every caller reads as the wider verdict.
_GLOB_MAX_STEPS = 50000


def _glob_meet(a: list, b: list) -> bool:
    """Some string matches both token lists. A walk over pairs of positions; a character is tried
    from a literal on either side, else from stand-ins that cover every class: each character the
    patterns name, a range's ends and their neighbours, and one that nothing names. True, unread,
    past _GLOB_MAX_STEPS."""
    alpha = {"/", chr(0xE000)}
    for t in a + b:
        if t[0] == "lit":
            alpha.add(t[1])
        else:
            alpha |= t[2]
            for lo, hi in t[3]:
                alpha |= {lo, hi, chr(ord(lo) + 1), chr(ord(hi) + 1), chr(max(ord(lo) - 1, 0)),
                          chr(max(ord(hi) - 1, 0))}
    _check_deadline()                   # round 7, 1: many bounded walks still add up
    seen, todo, steps = set(), [(0, 0)], 0
    while todo:
        i, j = todo.pop()
        if (i, j) in seen:
            continue
        seen.add((i, j))
        if not len(seen) & 4095:
            _check_deadline()               # round 7, 1: the analysis deadline covers the glob walk
        if i == len(a) and j == len(b):
            return True
        if i < len(a) and a[i][0] == "star":
            todo.append((i + 1, j))
        if j < len(b) and b[j][0] == "star":
            todo.append((i, j + 1))
        if i == len(a) or j == len(b):
            continue
        ta, tb = a[i], b[j]
        cands = (ta[1],) if ta[0] == "lit" else (tb[1],) if tb[0] == "lit" else alpha
        steps += len(cands)
        if steps > _GLOB_MAX_STEPS:
            return True
        for c in cands:
            if _glob_accepts(ta, c) and _glob_accepts(tb, c):
                todo.append((i + (ta[0] != "star"), j + (tb[0] != "star")))
    return False


def _glob_may_meet(pat: str, other: list, slash: bool = False, tail: tuple = ()) -> bool:
    """`pat` (then the tokens `tail`) and the tokens `other` can match one string, under every
    reading a tool might give `pat`: a backslash as an escape or as itself, `[^` as a negation or
    as a member (F5)."""
    for esc in ((True, False) if chr(92) in pat else (True,)):
        for caret in ((True, False) if "[^" in pat else (False,)):
            if _glob_meet(_glob_parse(pat, slash, esc, caret) + list(tail), other):
                return True
    return False


def _glob_covers(pat: str, ending: str, slash: bool) -> bool:
    """Every name ending in `ending` matches `pat`: leading `*`s, then characters that each accept
    their place in the ending. A backslash or `[^` is never read this way (F5)."""
    if chr(92) in pat or "[^" in pat or not pat.startswith("*"):
        return False
    # Read with `[^` as a negation, its widest reading: only the refusal above keeps it from counting.
    toks = _glob_parse(pat, slash, True, True)
    if any(len(t) > 4 for t in toks):
        return False                    # a POSIX class is read only as "any character"
    while toks and toks[0][0] == "star":
        toks = toks[1:]
    if len(toks) > len(ending) or any(t[0] == "star" for t in toks):
        return False
    return all(_glob_accepts(t, c) for t, c in zip(toks, ending[len(ending) - len(toks):]))


#: The operand xargs_feed gives an `xargs rm` fed by another command, as norm() spells it.
_XARGS_ITEM = "${xargs_item}"


def _may_name_archive(name: str) -> bool:
    """A file name, or a pattern for one, that some .cat or .dat could have."""
    if _ARCHIVE_EXT.search(name):
        return True
    # A name the shell fills in at run time (`ls <game> | while read f; do rm "<game>/$f"`) may be
    # any archive's, as the same loop under reference/ is denied (user decision 2026-11-05). The
    # xargs item stands for what a producer prints, which the find and listing readers judge.
    # A backtick pair is the older spelling of $(...) (4.1.1 review round 4, 6).
    if name.startswith(_XARGS_ITEM) and not re.search(r"[$`*?\[]", name[len(_XARGS_ITEM):]):
        # A find's `{}` with a literal tail (`{}.bak`, round 6): an archive only if the tail can end one.
        tail = name[len(_XARGS_ITEM):].lower()
        return bool(tail) and any(tail.endswith(e) if len(tail) >= len(e) else e.endswith(tail)
                                  for e in _ARCHIVE_ENDINGS)
    # No `name != _XARGS_ITEM` clause: the bare item returned above (an empty tail is no archive), so it was
    # dead -- the mutation gate could not kill it (4.1.1 final round).
    if "$" in name or name.count(chr(96)) >= 2:
        return True
    return _is_pattern(name) and any(_glob_may_meet(name, [_GLOB_DISK_NAME] + _glob_lit(e))
                                     for e in _ARCHIVE_ENDINGS)


def _is_dlc_folder(n: str, g: str) -> bool:
    """`n`, normalised, is an official DLC folder, or a pattern reaching one. Same test as the
    file tools' DLC refusal: the segment directly below the game's extensions/ is ego_dlc_*."""
    prefix = g + "/extensions/ego_dlc_"
    if n.startswith(prefix) and "/" not in n[len(prefix):]:
        return True
    return _is_pattern(n) and _glob_may_meet(n, _glob_lit(prefix) + [_GLOB_SEGMENT])


def _in_dlc_folder(n: str, g: str) -> bool:
    """`n` is INSIDE an official DLC folder, at any depth, or a pattern reaching inside one. User
    ruling 2026-11-05: a delete or move there is denied as an archive's is, never asked as a mod's."""
    prefix = g + "/extensions/ego_dlc_"
    if n.startswith(prefix) and "/" in n[len(prefix):].rstrip("/"):
        return True
    return _is_pattern(n) and _glob_may_meet(n, _glob_lit(prefix) + [_GLOB_SEGMENT, ("lit", "/"),
                                                                     _GLOB_ALL])


def _is_archive_home(n: str, g: str) -> bool:
    """`n` is a folder whose direct .cat and .dat files belong to the game."""
    if n == g or _is_dlc_folder(n, g):
        return True
    return _is_pattern(n) and n.count("/") == g.count("/") and fnmatch.fnmatchcase(g, n)


def _is_game_archive(n: str, g: str) -> bool:
    """`n` names a direct .cat or .dat in the game root or a DLC, or a pattern reaching one."""
    home, sep, name = n.rpartition("/")
    if not sep:
        return False
    if not _is_archive_home(home, g):
        # A folder filled in at run time directly in the game folder may be `.` or
        # `extensions/ego_dlc_x` (4.1.1 review round 4, 6: `rm "<game>/$f/01.cat"` was advised).
        below = home[len(g) + 1:] if home.startswith(g + "/") else ""
        if not below or "/" in below or ("$" not in below and below.count(chr(96)) < 2):
            return False
    return _may_name_archive(name)


def _removes_game_archive(n: str, g: str) -> bool:
    """A delete or move of `n` takes an archive away, the DLC folder holding them, or anything
    else inside a DLC folder (F8). `<x>/*` is how the PowerShell front end spells
    `Get-ChildItem <x> | Remove-Item`, so <x> counts too."""
    return (_is_game_archive(n, g) or _is_dlc_folder(n, g) or _in_dlc_folder(n, g)
            or (n.endswith("/*") and _is_game_archive(n[:-2], g)))


def _holds_a_dlc(n: str, g: str) -> bool:
    """`n` is the game's extensions/, the game folder, or a folder above it, or a pattern for one:
    a RECURSIVE delete of it takes every official DLC with it (F2)."""
    cands = [g + "/extensions", g] + [g[:i] for i in range(len(g)) if g[i] == "/" and i]
    if not _is_pattern(n):
        return n.rstrip("/") in cands
    return any(_glob_may_meet(n, _glob_lit(c)) for c in cands)


def deletes_recursively(words: list) -> bool:
    """The delete verb among `words` (a command's words, wrappers and carriers included) removes
    folders whole: `rm -r`/`-R`/`--recursive` in any cluster, as the translated `rd /s` is too."""
    text = " ".join(words)
    for c in [text] + _inner_commands(text):
        toks = tokens_of(c)
        vt = _verb_token(c)
        if _verb_name(verb(c)) not in DELETE_VERBS or vt not in toks:
            continue
        for t in toks[toks.index(vt) + 1:]:
            if t == "--recursive" or (re.match(r"^-[A-Za-z]+$", t) and set(t[1:]) & set("rR")):
                return True
    return False


def named_game_roots(n: str) -> list:
    """The game folders a normalised path names by the install's folder name, for a machine with
    no configured game root. The same name test as the `rm -rf <game>` backstop (GAME_ROOTISH,
    rm_named_game), at a whole path segment, and only its `x4 foundations` spelling: the other one
    is the PROFILE, whose stray .dat files are not the game's. An option word (`--reference=<path>`,
    which a reader may keep as a target) is not a path, so it names no game folder."""
    if n.startswith("-"):
        return []
    out, at = [], 0
    while True:
        at = n.find("/", at + 1)
        p = n if at < 0 else n[:at]
        m = GAME_ROOTISH.search(p)
        if (m and m.group(1).lower() == "x4 foundations" and not m.group(2)
                and (m.start() == 0 or p[m.start() - 1] == "/")):
            out.append(p)
        elif _globs_install_name(p):
            # The last segment too, as the literal name counts there (4.1.1 review round 4, 5:
            # `find /d/Games/X4*Foundations -name '*.cat' -delete` was allowed).
            out.append(p)
        if at < 0:
            return out


#: The install's folder name as a path segment, for a pattern to meet: `X4*Foundations`, `X4?F...`.
_INSTALL_NAME = _glob_lit("x4 foundations")


def _globs_install_name(p: str) -> bool:
    """The last segment of `p` is a pattern that may spell the install's folder name (4.1.1 review
    round 3, C). One that spells it with fewer than two literal characters (`*`, `?*`) is read so
    only directly under steamapps/common: elsewhere `C:/*/01.dat` names every folder, not a game."""
    head, _sep, last = p.rpartition("/")
    if not last or not _is_pattern(last):
        return False
    lits = sum(1 for t in _glob_parse(last, False, True, False) if t[0] == "lit")
    if lits < 2 and not head.endswith("/steamapps/common"):
        return False
    return _glob_may_meet(last, _INSTALL_NAME)


#: rsync options whose value is the NEXT word when not joined by `=`: a filter rule, a list file,
#: a folder, a program. `--exclude '*.dat'` names what is NOT copied, so it is never a source.
_RSYNC_VALUE_OPTS = {"--exclude", "--include", "--filter", "-f", "--exclude-from", "--include-from",
                     "--files-from", "-e", "--rsh", "--rsync-path", "--log-file", "--log-file-format",
                     "-T", "--temp-dir", "--partial-dir", "--compare-dest", "--copy-dest", "--link-dest",
                     "--backup-dir", "--suffix", "--chmod", "--chown", "--usermap", "--groupmap", "-B",
                     "--block-size", "--max-size", "--min-size", "--bwlimit", "--timeout", "--contimeout",
                     "--port", "--password-file", "--out-format", "--skip-compress", "--iconv", "--info",
                     "--debug", "--sockopts", "--modify-window", "--max-delete", "--protocol",
                     "--checksum-choice", "--compress-choice", "--compress-level", "-M", "--remote-option",
                     "--write-batch", "--read-batch", "--only-write-batch", "--stop-after", "--stop-at"}


def _copy_sources(seg: str) -> list:
    """A copy's words other than its destination, an option, or an rsync option's value."""
    toks = tokens_of(seg)
    values = ({toks[i + 1] for i, t in enumerate(toks[:-1]) if t in _RSYNC_VALUE_OPTS}
              if verb(seg) == "rsync" else set())
    dests = copy_dests(seg)
    return [o for o in _operands(seg) if o not in dests and not o.startswith("-") and o not in values]


def _robocopy_names(seg: str) -> list:
    """The file names a robocopy copies: the words after SRC and DST, up to its next switch. The
    words after a switch, as in `/XF *.dat`, are that switch's own and name what is skipped."""
    seen, out = 0, []
    for t in tokens_of(seg)[_verb_at(seg, 0) + 1:]:     # P1: see _verb_at
        if _ROBO_SWITCH.match(t):
            if seen >= 2:
                break
            continue
        if t.startswith(("<", ">")) or re.match(r"^\d+>", t):
            continue
        seen += 1
        if seen > 2:
            out.append(t)
    return out


def copy_names_archive(seg: str) -> bool:
    """A copy whose SOURCE is named as an archive: into a directory it lands under that name."""
    v = verb(seg)
    if v == "robocopy":
        names = _robocopy_names(seg)
    elif v in COPY_VERBS:
        names = _copy_sources(seg)
    else:
        return False
    return any(_ARCHIVE_EXT.search(norm(o)) for o in names)


def rsync_moved_sources(seg: str) -> list:
    """`rsync --remove-source-files` takes its sources away, as `mv` does. Fed to this rule only:
    a wider reach for the same flag is a separate change."""
    if verb(seg) != "rsync" or "--remove-source-files" not in tokens_of(seg):
        return []
    return _copy_sources(seg)


def _xargs_replace(seg: str) -> tuple:
    """(replacement string or None, the delete verb's words that carry it) of `xargs -I R rm ...`:
    `-I R`, `-IR`, `--replace=R`, and GNU's older spellings `-i` / `--replace` (`{}`) and `-iR` (4.1.1
    review A4; before it the wrapper table read the word after a bare `-i` as its value, F356). Only the
    xargs's own options count, the words between the last `xargs` and the verb."""
    toks = tokens_of(seg)
    vt = _verb_token(seg)
    if vt not in toks:
        return None, []
    vi = toks.index(vt)
    xi = max((i for i, t in enumerate(toks[:vi]) if _verb_name(t) == "xargs"), default=-1)
    repl = None
    for i, t in enumerate(toks[:vi]):
        if i <= xi:
            continue
        if t == "-I" and i + 1 < vi:
            repl = toks[i + 1]
        elif t.startswith("-I") and len(t) > 2:
            repl = t[2:]
        elif t.startswith("--replace="):
            repl = t[len("--replace="):]
        elif t in ("-i", "--replace"):
            repl = "{}"
        elif t.startswith("-i") and len(t) > 2:
            repl = t[2:]
    if not repl:
        return None, []
    return repl, [w for w in toks[vi + 1:] if repl in w]


def xargs_passes_item(seg: str) -> bool:
    """The item an `xargs rm` reads is itself an operand: no -I, or a word that starts with it."""
    repl, words = _xargs_replace(seg)
    return repl is None or any(w.startswith(repl) for w in words)


def xargs_replaced_folders(seg: str, prev) -> list:
    """`xargs -I{} rm <dir>/{}` deletes inside <dir>, whatever the producer printed: `<dir>/*`."""
    if not xargs_feed(seg, prev):
        return []
    repl, words = _xargs_replace(seg)
    return [w[:w.index(repl)] + "*" for w in words if not w.startswith(repl)]


def xargs_listed_operands(seg: str, prev) -> list:
    """What a producer other than find hands an `xargs rm`, read as named: `ls <game>/*.dat`.
    find's own operands include its tests, so it goes through find_can_reach_archive."""
    if not xargs_feed(seg, prev) or verb(prev) == "find" or not xargs_passes_item(seg):
        return []
    # A bare `ls` lists the current directory (4.1.1 review round 2, F3).
    return _operands(prev) or (["."] if verb(prev) in ("ls", "dir") else [])


# ---- find expressions. Parsed, never run: a regex the user wrote is not handed to `re`, because
# `-regex '(.*)*zzz'` backtracked for minutes, past the hook's own timeout (4.1.1 review, MEASURED).
#: Always true, no value: positional options, -true, -prune (which only stops descent).
_FIND_TRUE0 = {"-depth", "-d", "-xdev", "-mount", "-follow", "-noleaf", "-daystart", "-warn",
               "-nowarn", "-ignore_readdir_race", "-noignore_readdir_race", "-true", "-prune"}
#: Always true, one value.
_FIND_TRUE1 = {"-maxdepth", "-mindepth", "-regextype"}
#: Tests the text cannot decide, no value and one value.
_FIND_UNKNOWN0 = {"-empty", "-readable", "-writable", "-executable", "-nouser", "-nogroup", "-quit"}
_FIND_UNKNOWN1 = {"-size", "-user", "-group", "-uid", "-gid", "-perm", "-links",
                  "-inum", "-mtime", "-atime", "-ctime", "-mmin", "-amin", "-cmin", "-used", "-fstype",
                  "-context", "-samefile", "-newer", "-anewer", "-cnewer", "-lname", "-ilname"}
_FIND_NEWER_XY = re.compile(r"^-newer[aBcmt][aBcmt]$")
#: Bounds: past them the expression is not read, and an unread expression may reach anything.
_FIND_MAX_WORDS, _FIND_MAX_DEPTH = 400, 32
#: Escaped, these stand for themselves in every regex dialect find accepts.
_RX_SELF_ESCAPES = set("./-_")


def _exec_deletes(words: list) -> bool:
    """`-exec <words>` deletes: the command is a delete verb, or a shell carrying one."""
    if not words:
        return False
    # Through the shared wrapper table (verb) and carrier walk (_inner_commands), as any other
    # command: `-exec env rm`, `-exec busybox rm`, `-exec cmd /c del` (4.1.1 review round 2, F4).
    # `find <root> -exec bash -c '<cmd>' _ {} ;` is the same carrier as anywhere else.
    # Each word QUOTED back (4.1.1 review round 6, A): find hands its words over as argv, so
    # `sh -c echo\ a\;\ rm\ -rf\ <x>` is ONE word holding a whole program; joined bare, it fell
    # apart at its blanks and the shell's text read as `echo`. And every SEGMENT of a carried
    # text, not only its first: `sh -c 'echo a; rm -rf <x>'`.
    text = " ".join(_sh_quote(w) for w in words)
    if _verb_name(verb(text)) in DELETE_VERBS:
        return True
    return any(_verb_name(verb(s)) in DELETE_VERBS for x in _inner_commands(text) for s in segments(x))


def _exec_end(words: list, i: int) -> int:
    """The index of the terminator of the -exec at `i` (`;`, or `+` right after `{}`), or len(words)."""
    j = i + 1
    while j < len(words) and words[j] != ";" and (words[j] != "+" or words[j - 1] != "{}"):
        j += 1
    return j


#: The entry a find or an xargs hands its command, as a path no other operand can spell (round 6, D).
_FIND_ENTRY = "/x4-find-entry/" + "entry"
_PERL_IN_PLACE = re.compile(r"^-[A-Za-z]*i")


def _is_entry(p: str) -> bool:
    return p == _FIND_ENTRY or p.startswith(_FIND_ENTRY + "/")


def _entry_effect(text: str):
    """"move" when a command (a carried text too) moves or renames _FIND_ENTRY, "write" when it
    writes it or into it, else None. The same readers every other write uses: copy and move
    destinations, redirects, truncate/dd, sed -i, output flags, touch/chmod/chown/ln, plus
    `perl -i`. The entry as a SOURCE being copied elsewhere is no change (`cp {} <backup>/`)."""
    effect = None
    for x in [text] + _inner_commands(text):
        for s in segments(x):
            if any(_is_entry(h) for h in move_sources(s) + replaced_sources(s) + rsync_moved_sources(s)):
                return "move"
            hits = (copy_dests(s) + [t for _m, t in redirects(s)] + clobber_targets(s) + sed_in_place_targets(s)
                    + output_targets(s) + modify_targets(s) + decompress_outputs(s))
            toks = tokens(s)
            if _verb_name(verb(s)) == "perl" and any(not q and _PERL_IN_PLACE.match(t) for t, q in toks):
                hits += [t for t, _q in toks]
            if any(_is_entry(h) for h in hits):
                effect = "write"
    return effect


def _exec_entry_effect(words: list):
    """_entry_effect of an -exec command, `{}` standing for the entry."""
    if not words or not any("{}" in w for w in words):
        return None
    return _entry_effect(" ".join(_sh_quote(w.replace("{}", _FIND_ENTRY)) for w in words))


#: Programs that pass their stdin on (filtered or not) when given no input file (round 7, 2), so
#: `find ... | sort | xargs mv -t <dir>` is read as fed by the find. tee writes files and passes it on.
_STDIN_FILTERS = frozenset({"sort", "uniq", "head", "tail", "tr", "cat", "tee", "grep", "egrep", "fgrep",
                            "sed", "awk", "gawk", "mawk", "cut"})       # round 8 (e): sed, awk, cut


def _passes_stdin_through(seg: str) -> bool:
    v = verb(seg)
    if v not in _STDIN_FILTERS:
        return False
    if v in ("tee", "tr"):
        return True
    ops = [o for o in _operands(seg) if not re.fullmatch(r"[+-]?[0-9]+", o)]
    if v == "sed":
        flags = [t for t in tokens_of(seg) if t.startswith("-")]
        if any(f.startswith("--in-place") or (not f.startswith("--") and "i" in f[1:]) for f in flags):
            return False                     # sed -i edits files; it passes nothing on
        return len(ops) <= 1 + sum(1 for f in flags if f in ("-e", "-f", "--expression", "--file"))
    if v == "cut":
        ops = [o for o in ops if len(o) > 1]  # `-d ,`: a one-character delimiter is no file
    return len(ops) <= (1 if v in ("grep", "egrep", "fgrep", "awk", "gawk", "mawk") else 0)


def _stdin_producer(before: list):
    """The segment whose output reaches the next one: the one just before it, or, through pass-on
    filters (_STDIN_FILTERS), a find further back -- only a find, which the find readers judge."""
    if not before:
        return None
    j = len(before) - 1
    while j > 0 and _passes_stdin_through(before[j]):
        j -= 1
    if j < len(before) - 1 and verb(before[j]) == "find":
        return before[j]
    return before[-1]


def find_entry_write_roots(seg: str) -> list:
    """The starting points of a find whose -exec moves or writes what it finds (round 6, D)."""
    if verb(seg) != "find" or not ("-exec" in seg or "-ok" in seg):
        return []
    words = tokens_of(seg)
    for i, t in enumerate(words):
        if t in _FIND_EXEC:
            j = _exec_end(words, i)
            if j < len(words) and _exec_entry_effect(words[i + 1:j]):
                return _find_roots(seg) or ["."]
    return []


def xargs_entry_effect(seg: str, prev):
    """"move"/"write" when an `xargs <cmd>` fed by a find moves or writes each item: `{}`-style
    replacement (-I R) or the item appended, as xargs does (round 6, D)."""
    if prev is None or verb(prev) != "find":
        return None
    toks = tokens_of(seg)
    vt = _verb_token(seg)
    if vt not in toks or not any(_verb_name(t) == "xargs" for t in toks[:toks.index(vt)]):
        return None
    words = toks[toks.index(vt):]
    repl, _w = _xargs_replace(seg)
    words = [w.replace(repl, _FIND_ENTRY) for w in words] if repl else words + [_FIND_ENTRY]
    return _entry_effect(" ".join(_sh_quote(w) for w in words))


#: How many items of an echo/printf an `xargs -I` reading is built for (_xargs_item_readings). More is
#: too tangled to analyse (carriers_truncated, a refusal), never a silent cut (4.1.1 review A2: the
#: 9th item of 8 was dropped, so a reference file there was allowed). The segment bound is the same.
_XARGS_ITEMS = 32                       # = _MAX_SEGMENT_READINGS, defined further down


def _xargs_item_readings(seg: str, prev) -> list:
    """`echo <x> | xargs <cmd> [args]` runs `<cmd> [args] <x>` (or `<cmd>` with -I's replacement string
    replaced by <x>): that command, as a reading in its own right (4.1.1 final round, F360). Only the delete
    verbs read xargs' stdin (xargs_feed), so `echo <x4-paths.env> | xargs touch` and `echo <reference file> |
    xargs truncate -s 0` reached no write rule while the literal spellings were denied. Only an echo/printf
    feeds a reading: its words ARE the items, judged like the literal operands, unsplit as xargs_feed reads
    them (xargs splits a blank-holding word, so the reading is the stricter one). A find's or a listing's
    items are entries the guard cannot name, which the find and listing readers already judge."""
    if verb(prev) not in ("echo", "printf"):
        return []
    toks = tokens_of(seg)
    vt = _verb_token(seg)
    if vt not in toks or not any(_verb_name(t) == "xargs" for t in toks[:toks.index(vt)]):
        return []
    items = _producer_items(prev)
    if not items:
        return []
    ran = _drop_redirects(toks[toks.index(vt):])
    repl, _w = _xargs_replace(seg)
    if repl:
        if len(items) > _XARGS_ITEMS:
            _READINGS_TRUNCATED[0] = True
        return [" ".join(_sh_quote(w.replace(repl, item)) for w in ran) for item in items[:_XARGS_ITEMS]]
    return [" ".join(_sh_quote(w) for w in ran + items)]


#: fd's options that take a value, so its search paths can be told from them (_fd_exec_readings).
_FD_VALUE_OPTS = frozenset({"-e", "--extension", "-t", "--type", "-E", "--exclude", "-d", "--max-depth",
                            "--min-depth", "--exact-depth", "-j", "--threads", "-S", "--size", "--changed-within",
                            "--changed-before", "-o", "--owner", "--base-directory", "--path-separator",
                            "--max-results", "-c", "--color", "--ignore-file", "--format", "--batch-size"})
_FD_PLACEHOLDER = re.compile(r"[{](?:/|//|[.]|/[.])?[}]")


def _fd_exec_readings(seg: str) -> list:
    """`fd [pattern] [path...] -x|-X <cmd>` runs <cmd> per result (or once for all), with `{}`, `{/}`,
    `{//}`, `{.}`, `{/.}` standing for the result, or the result appended when none is given: a carrier
    like find's -exec (4.1.1 review round 7, 3). A result is an entry under each search path."""
    paths, ran, _opts = _fd_parse(seg)
    if not ran:
        return []
    out = []
    for root in paths[:_FIND_EXEC_ROOTS]:
        item = _sh_quote(root.rstrip("/") + "/") + _XARGS_ITEM
        if any(_FD_PLACEHOLDER.search(w) for w in ran):
            parts = [item.join(_sh_quote(p) if p else "" for p in _FD_PLACEHOLDER.split(w))
                     if _FD_PLACEHOLDER.search(w) else _sh_quote(w) for w in ran]
        else:
            parts = [_sh_quote(w) for w in ran] + [item]
        out.append(" ".join(parts))
    return list(dict.fromkeys(out))


def _fd_parse(seg: str) -> tuple:
    """(search paths, the -x/-X command, {option: value}) of an `fd` (round 8, F3). The pattern is
    the first operand and the paths the rest; `--search-path` adds a path and stops the first
    operand being one, `--base-directory` is where relative paths start."""
    toks = tokens(seg)
    words = [t for t, _q in toks[_verb_at(seg, 0) + 1:]]
    pos, search, opts, i, ran = [], [], {}, 0, None
    while i < len(words):
        w = words[i]
        if w in ("-x", "--exec", "-X", "--exec-batch"):
            ran = words[i + 1:]
            break
        name, eq, val = w.partition("=")
        if name == "--search-path" and (eq or i + 1 < len(words)):
            search.append(val if eq else words[i + 1])
            i += 1 if eq else 2
            continue
        if w.startswith("-") and w != "-":
            if name in _FD_VALUE_OPTS and not eq and i + 1 < len(words):
                opts.setdefault(name, []).append(words[i + 1])
                i += 2
            else:
                opts.setdefault(name, []).append(val if eq else "")
                i += 1
            continue
        pos.append(w)
        i += 1
    if ran and ran[-1] == ";":
        ran = ran[:-1]
    paths = search + pos[1:] if search else pos[1:]
    base = (opts.get("--base-directory") or [""])[-1]
    paths = [p if (not base or is_abs(p)) else base.rstrip("/") + "/" + p for p in paths] or [base or "."]
    return paths, ran, opts


def _fd_as_find(seg: str):
    """The `find` an `fd ... -x|-X <cmd>` amounts to, for the find readers' reach test (round 8, F3: `fd .
    <game> -x rm` only advised where `find <game> -exec rm {} ;` denies). The pattern becomes a
    `-regex` (fd matches it anywhere in the name; read conservatively), `-e EXT` a `-name '*.EXT'`,
    `-g GLOB` a `-name`, `-d N` a `-maxdepth`; the command runs once per result with `{}`."""
    paths, ran, opts = _fd_parse(seg)
    if not ran:
        return None
    toks = tokens(seg)
    words = [t for t, _q in toks[_verb_at(seg, 0) + 1:]]
    first = next((w for w in words if not w.startswith("-")), None)
    pattern = None if opts.get("--search-path") or first in (None, "-x", "-X", "--exec", "--exec-batch") \
        else first
    expr = []
    for d in (opts.get("-d") or opts.get("--max-depth") or [])[-1:]:
        expr += ["-maxdepth", d]
    exts = (opts.get("-e") or []) + (opts.get("--extension") or [])
    if exts:
        expr += ["("] + sum((["-o", "-name", "*." + e.lstrip(".")] for e in exts), [])[1:] + [")"]
    if "-g" in opts or "--glob" in opts:
        if pattern:
            expr += ["-name", pattern]
    elif pattern and pattern not in (".", "", "^", ".*"):
        expr += ["-regex", ".*" + pattern + ".*"]
    cmd = [_FD_PLACEHOLDER.sub("{}", w) for w in ran]
    if not any("{}" in w for w in cmd):
        cmd.append("{}")
    return " ".join(["find"] + [_sh_quote(p) for p in paths[:_FIND_EXEC_ROOTS]] + [_sh_quote(w) for w in expr]
                    + ["-exec"] + [_sh_quote(w) for w in cmd] + ["';'"])


#: How many starting points a find's -exec readings are built for (_find_exec_readings).
_FIND_EXEC_ROOTS = 4


def _find_exec_readings(seg: str) -> list:
    """Each command a find's -exec/-execdir/-ok/-okdir runs, as a command in its own right (4.1.1
    review round 6, A; GitHub issue #18). The delete rules read a find only as deleting under its
    starting point, so `find . -exec rm -rf <reference> \\;`, `-exec git add -A \\;` and `-exec sh
    -c '<cmd>' \\;` reached no rule. find runs the words as argv, so each is quoted back as written
    and nothing is re-split; `{}` is an entry the guard cannot name, under the starting point
    (`<root>/${xargs_item}`, as xargs_feed spells one). `-execdir`/`-okdir` run in the entry's own
    folder: under the starting point, in it, or (for the starting point itself) its parent, with
    `{}` as `./<name>` -- one reading each. A find that can only match a regenerable cache
    (_only_regenerable) drops a bare `{}`, so its cleanup stays the hygiene it always was."""
    toks = tokens(seg)
    start = _verb_at(seg, 0) + 1        # P1: see _verb_at
    words = [t for t, _q in toks[start:]]
    roots = []
    for t, quoted in toks[start:]:
        if not quoted and t.startswith(("-", "(", "!")):
            break
        roots.append(t)
    roots = roots[:_FIND_EXEC_ROOTS] or ["."]
    regenerable = _only_regenerable([t for t, _q in toks])
    out, i = [], len(roots)
    while i < len(words):
        if words[i] not in _FIND_EXEC:
            i += 1
            continue
        act, j = words[i], i + 1
        while j < len(words) and not (words[j] == ";" or (words[j] == "+" and words[j - 1] == "{}")):
            j += 1
        ran, i = words[i + 1:j], j + 1
        if j >= len(words) or not ran:
            continue                     # find refuses an unterminated -exec and runs nothing
        for root in roots:
            base = root.rstrip("/" + chr(92)) or root
            if act in ("-exec", "-ok"):
                places = [("", _sh_quote(base + "/") + _XARGS_ITEM)]
            else:
                parent, slash, name = base.replace(chr(92), "/").rpartition("/")
                if name in ("", ".", ".."):
                    parent, name = base + "/..", "."
                elif slash and (not parent or parent.endswith(":")):
                    parent += "/"
                places = [("cd " + _sh_quote(base) + " && ", "./" + _XARGS_ITEM),
                          ("cd " + _sh_quote(base + "/") + _XARGS_ITEM + " && ", "./" + _XARGS_ITEM),
                          ("cd " + _sh_quote(parent or ".") + " && ", _sh_quote("./" + name))]
            for cd, item in places:
                parts = []
                for w in ran:
                    if w == "{}" and regenerable:
                        continue
                    pieces = w.split("{}")
                    parts.append(item.join(_sh_quote(p) if p else "" for p in pieces) if len(pieces) > 1
                                 else _sh_quote(w))
                out.append(cd + " ".join(parts))
            if act in ("-exec", "-ok") and "{}" not in "".join(ran):
                break                    # the same command whatever the root: read it once
    return list(dict.fromkeys(out))


def _find_parse(words: list):
    """(tree, has an action, (mindepth, maxdepth)) for a find expression, or None when it cannot be
    read. The depths are find's global options, None when not given (the last one counts, as in
    GNU find).

    Nodes: ("and"|"or"|",", [nodes]), ("not", node), ("const", True|False|None),
    ("name", test, value), ("act", "delete"|"print"|"exec", value). None is "cannot tell"."""
    if len(words) > _FIND_MAX_WORDS:
        return None
    pos, acted, depth = 0, False, {}

    def take():
        nonlocal pos
        if pos >= len(words):
            raise ValueError("a test with no value")
        pos += 1
        return words[pos - 1]

    def primary():
        nonlocal pos, acted
        w = take()
        if w in _FIND_NAME_TESTS:
            return ("name", w, take())
        if w in _FIND_TRUE0:
            return ("const", True)
        if w == "-false":
            return ("const", False)
        if w in _FIND_TRUE1:
            v = take()
            if w in ("-maxdepth", "-mindepth"):
                # A depth not spelled as a number (`$N`) bounds nothing that can be read.
                depth[w] = int(v) if v.isdigit() else None
            return ("const", True)
        if w in _FIND_UNKNOWN0:
            return ("const", None)
        if w in ("-type", "-xtype"):
            return ("name", w, take())     # decided only for an entry known to be a folder
        if w in _FIND_UNKNOWN1 or _FIND_NEWER_XY.match(w):
            take()
            return ("const", None)
        acted = True
        if w == "-delete":
            return ("act", "delete", True)
        if w in ("-print", "-print0", "-ls"):
            return ("act", "print", True)
        if w == "-printf":
            take()
            return ("act", "print", True)
        if w in ("-fprint", "-fprint0", "-fls"):
            take()
            return ("const", True)
        if w == "-fprintf":
            take()
            take()
            return ("const", True)
        if w in _FIND_EXEC:
            start = pos
            while pos < len(words) and words[pos] not in (";", "+"):
                pos += 1
            if pos >= len(words) or pos == start:
                raise ValueError("an unterminated -exec")
            pos += 1
            ran = words[start:pos - 1]
            if not _exec_deletes(ran):
                # A command that CHANGES the entry (round 6, D): moves it ("move") or writes it.
                return ("act", _exec_entry_effect(ran) or "exec", None)
            # A recursive delete also removes a matched FOLDER whole (F2).
            return ("act", "rdelete" if deletes_recursively(ran) else "delete", None)
        raise ValueError("an unknown primary")

    def unary(depth):
        nonlocal pos
        negate = False
        while pos < len(words) and words[pos] in ("!", "-not"):
            negate, pos = not negate, pos + 1
        if pos < len(words) and words[pos] == "(":
            if depth >= _FIND_MAX_DEPTH:
                raise ValueError("too deep")
            pos += 1
            node = chain(depth + 1, 0)
            if take() != ")":
                raise ValueError("an unclosed group")
        else:
            node = primary()
        return ("not", node) if negate else node

    _LEVELS = ((",",), ("-o", "-or"), ("-a", "-and"))

    def chain(depth, level):
        # comma, then OR, then AND (implicit between adjacent terms), lowest binding first
        def sub():
            return unary(depth) if level == 2 else chain(depth, level + 1)
        nonlocal pos
        items = [sub()]
        while pos < len(words) and words[pos] != ")":
            if words[pos] in _LEVELS[level]:
                pos += 1
            elif level < 2 or words[pos] in (",", "-o", "-or"):
                break
            items.append(sub())
        return items[0] if len(items) == 1 else (_LEVELS[level][0], items)

    try:
        tree = chain(0, 0) if words else ("const", True)
    except ValueError:
        return None
    if pos != len(words):
        return None
    return tree, acted, (depth.get("-mindepth"), depth.get("-maxdepth"))


def _literal_tail(pat: str, specials: str, self_escapes: set) -> str:
    """The literal text every full match of `pat` must END with; '' when none can be read."""
    out, i = [], len(pat) - 1
    while i >= 0:
        c, j = pat[i], i - 1
        while j >= 0 and pat[j] == chr(92):
            j -= 1
        if (i - 1 - j) % 2:                         # escaped
            if c not in self_escapes:
                break
            out.append(c)
            i = j
            continue
        if c in specials or c == chr(92):
            break
        out.append(c)
        i -= 1
    return "".join(reversed(out))


def _glob_fold(toks: list) -> list:
    """Tokens with every literal lower-cased, for a case-folding test."""
    return [("lit", t[1].lower()) if t[0] == "lit" else t for t in toks]


def _find_name_test(kind: str, value: str, hay: list, name: list, ending: str):
    """True, False, or None (cannot tell) for one entry. `hay` is the path as find prints it, as
    tokens (a `*` stands for a root not spelled as plain text); `name` is the entry's own name and
    `ending` the text every such name ends with. Names are not listed: an entry is any lower-case
    name with its ending (READ: every archive and DLC folder name is lower case on disk)."""
    fold = kind.startswith("-i")
    v = value.lower() if fold else value
    if fold:
        hay, name = _glob_fold(hay), _glob_fold(name)
    if kind in ("-name", "-iname"):
        if not _glob_may_meet(v, name):
            return False
        return True if _glob_covers(v, ending, False) else None
    if kind in ("-regex", "-iregex"):
        # Never run. A regex whose every match must END in text no entry can end in cannot
        # match; anything else may. An alternation anywhere means no ending is required.
        if "|" in v:
            return None
        escapes = len(v[:-1]) - len(v[:-1].rstrip(chr(92)))
        body = v[:-1] if v.endswith("$") and escapes % 2 == 0 else v
        # Only its last characters: a match ending in all of it ends in them, so a False stays
        # sound, and a crafted tail of thousands cannot outrun the meet's bounds (round 3, A).
        tail = _literal_tail(body, ".^$*+?{}[]()|", _RX_SELF_ESCAPES)[-64:]
        if not tail:
            return None
        return None if _glob_meet([_GLOB_ALL] + _glob_lit(tail), hay) else False
    if not _glob_may_meet(v, hay, slash=True):
        return False
    return True if _glob_covers(v, ending, True) else None


def _find_eval(node, test, reach: list, modes: tuple):
    """Three-valued: True, False, None. An action in `modes` reached on any path, certain or not,
    is recorded in `reach`; an AND stops at a False, an OR at a True, as find does."""
    kind = node[0]
    if kind == "const":
        return node[1]
    if kind == "name":
        return test(node[1], node[2])
    if kind == "act":
        if node[1] in modes:
            reach.append(node)
        return node[2]
    if kind == "not":
        v = _find_eval(node[1], test, reach, modes)
        return None if v is None else not v
    val = None
    if kind == "-a":
        val = True
        for sub in node[1]:
            v = _find_eval(sub, test, reach, modes)
            if v is False:
                return False
            val = val if v is True else None
    elif kind == "-o":
        val = False
        for sub in node[1]:
            v = _find_eval(sub, test, reach, modes)
            if v is True:
                return True
            val = val if v is False else None
    else:                               # `,`: every part runs, the last one's value is the value
        for sub in node[1]:
            val = _find_eval(sub, test, reach, modes)
    return val


def _archive_entries(g: str) -> list:
    """(path, name, ending, is a folder, DLC content) for what a find over the game may remove: an
    archive in the game folder or in a DLC folder; for a recursive delete only, a DLC folder,
    extensions/ and the game folder themselves (F2); and anything else inside a DLC folder, at any
    depth and under any name (F8, 4.1.1 review round 3, E). Paths and names are glob tokens."""
    dlc = _glob_lit(g + "/extensions/ego_dlc_") + [_GLOB_DISK_NAME]
    out = []
    for e in _ARCHIVE_ENDINGS:
        name = [_GLOB_DISK_NAME] + _glob_lit(e)
        out.append((_glob_lit(g + "/") + name, name, e, False, False))
        out.append((dlc + [("lit", "/")] + name, name, e, False, False))
    out.append((dlc, _glob_lit("ego_dlc_") + [_GLOB_DISK_NAME], "", True, False))
    out.append((_glob_lit(g + "/extensions"), _glob_lit("extensions"), "extensions", True, False))
    # The install folder's own name, in any case: it is spelled `X4 Foundations` on disk.
    base = g.rpartition("/")[2]
    out.append((_glob_lit(g), [("one", False, frozenset((c.lower(), c.upper())), ()) for c in base],
                base, True, False))
    out.append((dlc + [("lit", "/"), _GLOB_ALL], [_GLOB_SEGMENT], "", False, True))
    return out


def _entry_depths(rel: list) -> tuple:
    """(least, most) depth below a find's root of an entry whose path below it is `rel`; most is
    None when a token may hold any number of `/`."""
    least = 1 + sum(1 for t in rel if t == ("lit", "/"))
    deep = any(t[0] != "lit" and _glob_accepts(t, "/") for t in rel)
    return least, (None if deep else least)


def find_can_reach_archive(seg: str, root: str, raw: str, g: str, mode: str, rec: bool = False) -> bool:
    """Can a `find` from `root` (as written: `raw`) delete, or print to an `xargs rm` (`mode`
    "print"; `rec` when that rm is recursive), a base-game or DLC archive, or remove a folder that
    holds one? Mode "delete" also counts an -exec that moves or writes the entry (round 6, D), and
    modes "move"/"write" are a find printing to an `xargs` that moves or writes each item. Each entry under `root` walks the expression as find would, three-valued: a test the
    text cannot decide may go either way, and an action reached on either branch counts. An
    expression it cannot read may reach every entry. A root in or at a DLC folder is denied
    whatever it matches (F8).

    A root AT extensions/ also reaches every other file in a DLC folder (F8, round 3 E); a root at
    the game folder or above does not, by decision: from there `find . -name __pycache__ -exec rm
    -rf {} +` is routine repository cleanup (BLIND-SPOTS F356). A global -maxdepth or -mindepth
    drops the entries it cannot reach (round 3, D)."""
    r = root.rstrip("/")
    if _is_dlc_folder(r, g) or _in_dlc_folder(r, g):
        return True
    file_modes = ("delete", "rdelete", "write", "move") if mode == "delete" else ("print",)
    dir_modes = {"delete": ("rdelete", "move"), "print": ("print",) if rec else (), "move": ("print",),
                 "write": ()}[mode]
    # Writing INTO a folder harms only a DLC folder (anything in it is game content); moving one
    # away removes it like a delete (round 6, D).
    dlc_dir_modes = dir_modes + {"delete": ("write",), "write": ("print",)}.get(mode, ())
    plain = raw.replace(chr(92), "/").rstrip("/")
    if any(c in plain for c in "$~`") or _is_pattern(plain) or not plain:
        plain = None
    ext = g + "/extensions"
    at_ext = (r == ext) if not _is_pattern(r) else _glob_may_meet(r, _glob_lit(ext))
    entries = []
    # A write or a move reaches every file in a DLC folder from any root that holds one: the routine
    # cleanup the delete exemption below keeps is a delete, never a rewrite (round 6, D).
    changes = ("write", "move") if mode == "delete" else ("print",) if mode in ("write", "move") else ()
    wide = bool(changes) and _holds_a_dlc(r, g)
    for path, name, ending, folder, content in _archive_entries(g):
        modes = (dlc_dir_modes if ending == "" else dir_modes) if folder else file_modes
        if content and not at_ext:
            modes = changes if wide else ()
        if not modes:
            continue
        if _is_pattern(r):
            if not _glob_may_meet(r, path, tail=(("lit", "/"), _GLOB_ALL)):
                continue
            hay = [_GLOB_ALL, ("lit", "/")] + name
            span = (1, None)
        else:
            head = r + "/"
            if len(path) <= len(head) or "".join(t[1] for t in path[:len(head)] if t[0] == "lit") != head \
                    or any(t[0] != "lit" for t in path[:len(head)]):
                continue
            rel = path[len(head):]
            hay = (_glob_lit(plain + "/") if plain is not None else [_GLOB_ALL, ("lit", "/")]) + rel
            span = _entry_depths(rel)
        entries.append((hay, name, ending, modes, span, False))
    # The root itself is a depth-0 entry. A recursive delete of a root that is the game folder,
    # extensions/ or a folder above them takes every DLC with it, whatever -maxdepth says. The walk
    # above only sees entries BELOW the root, which deeper entries masked until -maxdepth 0 dropped
    # them (4.1.1 review round 4, 1: `find <game> -maxdepth 0 | xargs rm -rf` advised).
    if _holds_a_dlc(r, g):
        base = (plain or r).rpartition("/")[2]
        if plain is None or _is_pattern(r):
            hay, name, ending = [_GLOB_ALL], [_GLOB_SEGMENT], ""
        else:
            hay, name, ending = _glob_lit(plain), _glob_lit(base), base
        entries.append((hay, name, ending, dir_modes, (0, 0), True))
    if not entries:
        return False
    k = _verb_at(seg, 0) + 1            # P1: see _verb_at
    parsed = _find_parse(tokens_of(seg)[k + len(_find_roots(seg)):])
    if parsed is None:
        return True
    tree, acted, (mind, maxd) = parsed
    entries = [e for e in entries
               if (maxd is None or e[4][0] <= maxd) and (mind is None or e[4][1] is None or e[4][1] >= mind)]
    for hay, name, ending, modes, _span, folder in entries:
        _check_deadline()
        def test(kind, value, hay=hay, name=name, ending=ending, folder=folder):
            if kind in ("-type", "-xtype"):
                # The root is a folder, or a link to one: a type naming neither skips it (4.1.1
                # review round 5, 3: `find <game> -maxdepth 0 -type f | xargs rm -rf` was denied).
                # Any other entry, and a type not spelled as letters, cannot be told.
                return False if folder and re.fullmatch(r"[bcpfsD](?:,[bcpfsD])*", value) else None
            return _find_name_test(kind, value, hay, name, ending)
        reach = []
        v = _find_eval(tree, test, reach, modes)
        if reach or ("print" in modes and not acted and v is not False):
            return True
    return False




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


_POSITIONAL = re.compile(r"[$](?:[{]([0-9]+|[@*])[}]|([0-9@*]))")


#: What a positional fill may add beyond its own input (4.1.1 review round 8, F1). Each parameter is
#: filled IN FULL at its first use, so no argument is ever hidden; a later use past the budget is an
#: unknown value instead. MEASURED at a933b5c0: `sh -c 'echo "$@" x400' _ a0..a399` filled 160,000
#: words and the hook ran 38 s, past its 30 s timeout.
_POSITIONAL_EXTRA = 4096
_POSITIONAL_UNKNOWN = "${x4_positional}"


def _positional_text(text: str, args: list) -> str:
    """`text` with `$0`..`$9`, `${N}`, `$@`, `$*` (quoted or not) replaced by `args` (args[0] is `$0`),
    as the shell running `sh -c TEXT ARGS...` fills them in. A parameter past the last argument is
    empty, and an empty `"$@"` is no word at all. Inside double quotes the value is escaped for them;
    elsewhere it is quoted as one word each, so a value with a blank stays one operand (4.1.1 review
    round 7, 2). Bounded: see _POSITIONAL_EXTRA."""
    if "$" not in text or not args:
        return text
    kinds = {i: k for i, _c, _inq, k in _qwalk(text)}
    out, last, seen = [], 0, set()
    budget = 4 * (len(text) + sum(len(a) + 1 for a in args)) + _POSITIONAL_EXTRA
    for m in _POSITIONAL.finditer(text):
        k = kinds.get(m.start(), "")
        if k in ("'", "$'") or (m.start() and text[m.start() - 1] == chr(92)):
            continue
        name = m.group(1) or m.group(2)
        vals = args[1:] if name in "@*" else ([args[int(name)]] if int(name) < len(args) else [""])
        start, end = m.start(), m.end()
        if name in seen and budget <= 0:
            rep = _POSITIONAL_UNKNOWN           # past the budget: unknown, never dropped
        elif name == "@" and not vals and k == chr(34) and text[start - 1:start] == chr(34) \
                and text[end:end + 1] == chr(34):
            start, end, rep = start - 1, end + 1, ""   # an empty "$@" is no word
        elif k == chr(34) and name == "@":
            # "$@" is one word per argument: `"pre$@post"` is "pre<a1>" "<a2>post".
            rep = (chr(34) + " " + chr(34)).join(
                re.sub(r"([" + chr(92) + chr(92) + chr(34) + "$`])", chr(92) + chr(92) + r"\1", x) for x in vals)
        elif k == chr(34):
            rep = re.sub(r"([" + chr(92) + chr(92) + chr(34) + "$`])", chr(92) + chr(92) + r"\1", " ".join(vals))
        else:
            rep = " ".join(_sh_quote(x) for x in vals if x)
        seen.add(name)
        budget -= len(rep)
        out.append(text[last:start])
        out.append(rep)
        last = end
    return "".join(out) + text[last:] if out else text


def _inline_assigned(filled: str, text: str) -> str:
    """`filled` with each variable it ASSIGNS a filled value to (`d=$1` became `d='<x>'`) written out
    where it is read, as the shell would (round 8, F3: `sh -c 'd=$1; rm -rf "$d"' _ <ref>`). The
    unfilled text assigns the same names an unknown value, and assignments() keeps the FIRST one it
    meets, so without this the fill never reached the delete."""
    was, now = assignments(text), assignments(filled)
    names = [k for k, v in now.items() if was.get(k) != v and not has_unresolved(v)]
    if not names:
        return filled
    kinds = {i: k for i, _c, _inq, k in _qwalk(filled)}
    pat = re.compile(r"[$](?:[{](" + "|".join(map(re.escape, names)) + r")[}]|(" + "|".join(map(re.escape, names))
                     + r")(?![A-Za-z0-9_]))")
    out, last = [], 0
    for m in pat.finditer(filled):
        k = kinds.get(m.start(), "")
        if k in ("'", "$'"):
            continue
        v = now[m.group(1) or m.group(2)]
        out.append(filled[last:m.start()])
        out.append(re.sub(r"([" + chr(92) + chr(92) + chr(34) + "$`])", chr(92) + chr(92) + r"\1", v)
                   if k == chr(34) else _sh_quote(v))
        last = m.end()
    return "".join(out) + filled[last:]


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


#: What a refusal may say about WHY translation failed. A `why` can carry command text, a
#: translator message or a configured path, so it stays out of the refusal; this names which kind
#: of failure it was, in fixed words only. The first category with a match wins.
PS_TIMED_OUT = "PowerShell translation timed out"
PS_BUDGET_SPENT = "PowerShell translation time budget spent"
_PS_CATEGORIES = (
    (PS_TIMED_OUT, lambda w: w == "PowerShell could not be run (TimeoutExpired)"),
    (PS_BUDGET_SPENT, lambda w: w.startswith("the PowerShell translation budget ")),
    ("no PowerShell available", lambda w: w.startswith("no PowerShell was found ")),
    ("PowerShell translator file missing", lambda w: w.startswith("ps_translate.ps1 is missing ")),
    ("PowerShell could not be started", lambda w: re.fullmatch(r"PowerShell could not be run \(\w+\)", w)),
    ("PowerShell translator returned no verdict", lambda w: w.startswith("the PowerShell translator returned no ")),
)


def untranslated_category(whys) -> str:
    """The fixed category for these failure reasons, never any of their text."""
    whys = [str(w) for w in whys]
    for category, matches in _PS_CATEGORIES:
        if any(matches(w) for w in whys):
            return category
    return "part of the command could not be translated"


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
    left = (_DEADLINE[0] - _clock()) if _DEADLINE[0] is not None else _PS_CALL_CAP_S
    if left <= 0:
        raise AnalysisTimeout()         # round 8, F2: no time left before the analysis deadline
    cap = min(_PS_CALL_CAP_S, remaining, left)
    t0 = _clock()
    try:
        got = _translate_ps(text, cap)
    finally:
        _ps_spent[0] += max(0.0, _clock() - t0)
    if got[0] is not None or cap >= min(_PS_CALL_CAP_S, remaining):
        _PS_CACHE[text] = got           # a call cut short by the deadline is not a verdict on the text
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
    # No telemetry or update check: each is a network call at PowerShell startup, and a hook
    # pays the startup once per command it parses. (4.0.1: ci/v4.0.1-rc7 and -rc8 each had a
    # ~40 s window on the ubuntu runner where every translation hit the 10 s cap; the probe
    # on that image measured the slowest of 60 calls at 3.23 s with them on, 1.14 s off. The
    # window's cause is NOT established; this removes one candidate.)
    env = dict(os.environ, POWERSHELL_TELEMETRY_OPTOUT="1", POWERSHELL_UPDATECHECK="Off")
    try:
        p = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", script],
            input=text.encode("utf-8"), capture_output=True, timeout=timeout, env=env,
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


#: A shell option word after `-c`: bash reads every option before its program text, so
#: `bash -c -- '<cmd>'` and `bash -c -x '<cmd>'` run <cmd>, and the word after `-c` was all that
#: was walked (MEASURED: no guard flag). Each `o`/`O` in a cluster takes the next word as its value,
#: and `-` ends the options as `--` does.
_SHELL_OPT_WORD = re.compile(r"[-+][A-Za-z]+|--[a-z][a-z-]*")
_SHELL_LONG_VALUE_OPTS = ("--rcfile", "--init-file")


def _shell_option_values(t: str) -> int:
    return 0 if t[:2] == "--" else t.count("o") + t.count("O")


def _shell_text_at(toks: list, j: int, flag: str = "") -> int:
    """The index of a shell's program text, given the index just after its `-c` (`flag`, whose own
    `o` takes a value too in `-co pipefail`)."""
    k = j + _shell_option_values(flag)
    while k < len(toks):
        t = toks[k][0]
        if t in ("--", "-"):
            k += 1
            break
        if not _SHELL_OPT_WORD.fullmatch(t):
            break
        k += 1 + (1 if t in _SHELL_LONG_VALUE_OPTS else _shell_option_values(t))
    return k if k < len(toks) else j


#: Programs that run the program named in their ARGUMENTS, and are not in WRAPPERS because their
#: own verb must stay what the rest of this file sees (a save-lift blocker, and no rule of its
#: own). name -> (options taking a separate value, positional words before the program, whether
#: numeric words before the program are stepped over). MEASURED: each ran `rm -rf <reference>`
#: with no guard flag.
_ARGV_CARRIERS = {
    "watch": ({"-n", "--interval", "-q", "--equexit"}, 0, False),
    "wsl": ({"-d", "--distribution", "--distribution-id", "-u", "--user", "--cd", "--shell-type"}, 0, False),
    "busybox": (frozenset(), 0, False),
    "toybox": (frozenset(), 0, False),
    "winpty": (frozenset(), 0, False),
    "pkexec": ({"--user"}, 0, False),
    "runuser": ({"-u", "--user", "-g", "--group", "-G", "--supp-group", "-s", "--shell"}, 0, False),
    "chroot": ({"--userspec", "--groups"}, 1, False),
    "flock": ({"-w", "--timeout", "-E", "--conflict-exit-code"}, 1, False),
    "nsenter": ({"-t", "--target", "-S", "--setuid", "-G", "--setgid"}, 0, False),
    "unshare": ({"--map-user", "--map-group", "--setuid", "--setgid", "-S", "-G", "--propagation",
                 "--setgroups"}, 0, False),
    "taskset": ({"-c", "--cpu-list"}, 0, True),
    "chrt": ({"-T", "-P", "-D"}, 0, True),
    "screen": ({"-S", "-c", "-T", "-h", "-p", "-t", "-e", "-s"}, 0, False),
    # 4.1.1 review round 6, B: each ran its argv unseen (MEASURED: `sshpass -p x ssh localhost
    # rm -rf <reference>`, `torsocks git add -A`, `mintty -e bash -c '<cmd>'` were allowed).
    "sshpass": ({"-p", "-f", "-d", "-P"}, 0, False),
    "torsocks": ({"-u", "--user", "-p", "--pass", "-a", "--address", "-P", "--port"}, 0, False),
    "valgrind": (frozenset(), 0, False),
    "caffeinate": ({"-t", "-w"}, 0, False),
    "mintty": ({"-c", "--config", "-C", "--loadconfig", "-h", "--hold", "-i", "--icon", "-l", "--log",
                "-o", "--option", "-p", "--position", "-s", "--size", "-t", "--title", "-T", "--class",
                "-w", "--window", "-B", "--Border", "-R", "--Report", "--dir"}, 0, False),
    "gsudo": ({"-u", "--user", "-i", "--integrity"}, 0, False),
}
#: Carriers whose program sits behind a word of their own, read by _special_carrier_readings: `gdb
#: --args <cmd>`, `start [/switch]... ["title"] <cmd>` (cmd.exe's, which Git Bash's `start` calls),
#: and `autossh -M <port> <ssh arguments>`, which IS an ssh.
_SPECIAL_CARRIERS = frozenset({"gdb", "start", "autossh"})
#: start's switches that take the next word as their value (`/D <dir>`, `/NODE <n>`, `/AFFINITY <m>`).
_START_VALUE_SWITCHES = frozenset({"/d", "/node", "/affinity"})
_NUMERIC_WORD = re.compile(r"0[xX][0-9a-fA-F]+|[0-9][0-9,-]*")

#: Programs that run TEXT given to a `-c` option, exactly as `bash -c` does. The extra shells are
#: here and not in _SHELLS because _carries_program_text lifts a save ask for what _SHELLS names.
_TEXT_C_CARRIERS = frozenset({"su", "runuser", "script", "flock", "ash", "fish", "mksh", "csh", "tcsh", "yash"})

_SSH_VALUE_OPTS = frozenset(("-" + c) for c in "bcDEeFIiJLlmOopQRSWwBP")
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
                             "0.0.0.0", "::1", "0:0:0:0:0:0:0:1"})
_PARALLEL_VALUE_OPTS = frozenset({
    "-j", "--jobs", "-P", "--max-procs", "-N", "--max-args", "-n", "-L", "--max-lines", "-a", "--arg-file",
    "-I", "-d", "--delimiter", "-S", "--sshlogin", "--delay", "--timeout", "--joblog", "--results",
    "--colsep", "-C", "--arg-sep", "--arg-file-sep", "--retries", "--tmpdir", "--workdir", "--wd",
    "--tagstring", "--load", "--memfree", "--block", "--halt", "-s", "--max-chars"})
_PARALLEL_SEPS = (":::", "::::", ":::+", "::::+")
_PARALLEL_SLOT = re.compile(r"\{(?:[.]|/|//|/[.]|[0-9]+)?\}")
_TMUX_GLOBAL_VALUE_OPTS = frozenset({"-L", "-S", "-f", "-T"})
_TMUX_RUNS = ("new-session", "new-window", "split-window", "respawn-pane", "respawn-window", "run-shell",
              "display-popup", "if-shell", "pipe-pane", "send-keys")
_TMUX_ALIASES = frozenset({"new", "neww", "splitw", "respawnp", "respawnw", "run", "popup", "if", "pipep", "send"})
_TMUX_VALUE_OPTS = frozenset({"-s", "-n", "-c", "-t", "-x", "-y", "-F", "-e", "-l", "-T", "-w", "-b"})
_WSL_MOUNT = re.compile(r"(?<![^\s'\"=:])/mnt/([A-Za-z])(?=/)")

#: Texts the carrier readings below produced, and everything found inside them, for this facts()
#: call. They are extra judgements: they must not change what the command's OWN text means, and
#: `moves` (does anything change directory?) would otherwise drop the session directory from an
#: unrelated carrier because a `cd` appears inside `tmux run '...'`.
_READING_TEXTS: set = set()


def _head(word: str) -> str:
    """A raw word's text with its quoting removed."""
    return (tokens(word) or [("", False)])[0][0]


def _program_at(words: list, opts, positional: int = 0, numeric: bool = False):
    """The index in `words` of the program a carrier runs, past its options, or None. A short-flag
    cluster whose LAST letter takes a value (`ssh -ti key`) consumes the next word as well."""
    i = 0
    while i < len(words):
        t = _head(words[i])
        if t == "--":
            return i + 1 if i + 1 < len(words) else None
        if len(t) > 1 and t[0] == "-":
            i += 1
            if t in opts or (t[1] != "-" and len(t) > 2 and "-" + t[-1] in opts
                             and not any("-" + c in opts for c in t[1:-1])):
                i += 1
            continue
        if positional > 0:
            positional -= 1
            i += 1
            continue
        if numeric and _NUMERIC_WORD.fullmatch(t):
            i += 1
            continue
        return i
    return None


def _escapes_a_split(text: str) -> bool:
    """`text` has a backslash before a blank or an operator: `a\\;`, `rm\\ -rf`. Unquoted, the word
    holds the character literally, and a shell that is handed the words joined reads it as a
    separator again."""
    if chr(92) not in text:
        return False
    esc = False
    for _i, c, _inq, _k in _qwalk(text):
        if esc:
            if _EVAL_SPLITS.match(c):
                return True
            esc = False
            continue
        # A quoted backslash needs no `not inq`: a quoted split character is the other clause of
        # every caller (the mutation gate cannot tell the two apart).
        esc = c == chr(92)
    return False


def _as_program(words: list, shell: bool = False) -> list:
    """Readings of `words` as a program: as written (run as argv), and with quoted words opened
    (run through a shell, which re-reads a quoted `rm -rf x` as three words). `shell`: the carrier
    joins its words for a shell (`watch`, `wsl`, `ssh`), which re-reads an ESCAPED separator too
    (`watch echo a\\; rm -rf x`); a carrier that runs its words as argv keeps it literal (4.1.1
    review round 5, 1: segments() stopped splitting at `\\;` in round 4)."""
    rest = " ".join(words)
    toks = tokens(rest)
    escaped = shell and _escapes_a_split(rest)
    if not (any(q and _EVAL_SPLITS.search(t) for t, q in toks) or escaped):
        return [rest]
    return [rest, " ".join(t for t, _q in toks)] + _requoted(toks, escaped)


def _requoted(toks: list, escaped: bool) -> list:
    """With an escaped separator, the words again with each QUOTED one kept whole. The shell that
    re-reads them splits a quoted `"<game>"` at its blank, so `watch echo a\\; rm -rf "<game>"`
    deletes `<steam>/X4` and `Foundations`; 4.1.0 judged the quoted path, and so does this
    reading (4.1.1 review round 5, 1)."""
    return [" ".join(_sh_quote(t) if q else t for t, q in toks)] if escaped else []


def _joins_for_a_shell(v: str, options: list) -> bool:
    """An argv carrier that hands its words to a shell: `watch` without -x/--exec, `wsl` without
    -e/--exec. `options`: the words before the program."""
    heads = [_head(w) for w in options]
    if v == "watch":
        return not any(h == "--exec" or re.fullmatch(r"-[A-Za-z]*x[A-Za-z]*", h) for h in heads)
    if v == "wsl":
        return not any(h in ("-e", "--exec") for h in heads)
    # INFERRED, not measured here: gsudo hands its words to the shell it was called from (`bash -c`
    # under Git Bash). Read as joining, which only ADDS readings (4.1.1 review round 6, B).
    return v == "gsudo"


def _text_option(words: list) -> list:
    """The program text of a `-c TEXT`, `-qc TEXT`, `-cTEXT`, `--command=TEXT` or `--command TEXT`
    option (_TEXT_C_CARRIERS). An unresolvable text also takes the words after it, so `su -c "$C"
    <reference>` reaches the unknown-program backstop."""
    for i, w in enumerate(words):
        toks = tokens(w)
        if not toks:
            continue
        t, quoted = toks[0]
        text = None
        following = words[i + 1:]
        if t.startswith(("--command=", "--session-command=")):
            text = t.partition("=")[2]
        elif (t in ("--command", "--session-command") or (not quoted and _DASH_C.match(t))) and following:
            k = _shell_text_at([(_head(x), False) for x in following], 0)
            text, following = _head(following[k]), following[k + 1:]
        elif t.startswith("-c") and len(t) > 2 and _EVAL_SPLITS.search(t):
            text = t[2:]
        if text is None:
            continue
        return [text + " " + " ".join(following)] if has_unresolved(text) and following else [text]
    return []


def _env_split_readings(seg: str) -> list:
    """`env -S TEXT`, `env -iS TEXT`, `env --split-string=TEXT`: env SPLITS the text and runs it, so
    it is a carrier _verb_word cannot read (it steps over -S's value). Also `env -iu VAR <cmd>` and
    `env -iC DIR <cmd>`: the value option in a cluster, which _verb_word reads as the command."""
    words = _raw_words(seg)
    i, wrapper = 0, ""
    while i < len(words):                # step over assignments and other wrappers to `env`
        t = _head(words[i])
        name = _verb_name(t)
        if name == "env":
            break
        i += 1
        if name in WRAPPERS:
            wrapper = name
        elif t.startswith("-") and wrapper:
            if t in _WRAPPER_VALUE_OPTS.get(wrapper, ()):
                i += 1
        elif not (_ASSIGNMENT.match(t) or (wrapper and _WRAPPER_ARG.match(t))):
            return []
    else:
        return []
    j, clustered = i + 1, False
    while j < len(words):
        t = _head(words[j])
        if t == "--":
            j += 1
            break
        if _ASSIGNMENT.match(t):
            j += 1
            continue
        if not t.startswith("-") or t == "-":
            if _verb_name(t) == "env":
                j += 1
                continue
            break
        if t.startswith("--"):
            if t.startswith("--s"):
                if "=" in t:
                    text, after = t.partition("=")[2], words[j + 1:]
                else:
                    text, after = (_head(words[j + 1]), words[j + 2:]) if j + 1 < len(words) else ("", [])
                return [" ".join([text] + after)] if text else []
            j += 2 if t in ("--unset", "--chdir") else 1
            continue
        body = t[1:]
        for k, c in enumerate(body):
            if c == "S":
                if body[k + 1:]:
                    return [" ".join([body[k + 1:]] + words[j + 1:])]
                if j + 1 < len(words):
                    return [" ".join([_head(words[j + 1])] + words[j + 2:])]
                return []
            if c in "uC":
                if k == len(body) - 1:
                    j += 1
                    clustered = clustered or len(body) > 1
                break
        j += 1
    if clustered and j < len(words):
        return [" ".join(words[j:])]
    return []


def _value_word_readings(seg: str) -> list:
    """The words from where the command name sat before `_LATER_VALUE_OPTS` were learned, for
    `exec -a rm -rf <x>` and `sudo -u root -a rm -rf <x>`: each read as a delete then, and stays a
    judged reading rather than dropping out of every rule."""
    at = _verb_word(seg, learned=False)[1]
    if at is None or at == _verb_word(seg)[1]:
        return []
    index = 0
    words = _raw_words(seg)
    for k, w in enumerate(words):
        if index == at:
            return [" ".join(words[k:])]
        index += len(tokens(w))
    return []


def _ipv4_number(h: str):
    """The address inet_aton reads from `h` (`0`, `2130706433`, `0x7f.1`, `0177.0.0.1`), or None."""
    parts = h.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    try:
        vals = [int(p, 16) if p[:2] in ("0x", "0X") else int(p, 8) if p[:1] == "0" and len(p) > 1 else int(p, 10)
                for p in parts]
    except ValueError:
        return None
    n = 0
    for v in vals[:-1]:
        n = n * 256 + v
    return n * 256 ** (5 - len(vals)) + vals[-1]


def _is_loopback(h: str) -> bool:
    if h in _LOOPBACK_NAMES:
        return True
    n = _ipv4_number(h)
    if n is not None:
        return n == 0 or n >> 24 == 127
    if ":" in h:
        import ipaddress
        try:
            a = ipaddress.ip_address(h)
        except ValueError:
            return False
        return a.is_loopback or a.is_unspecified or bool(a.ipv4_mapped and _is_loopback(str(a.ipv4_mapped)))
    return False


def _is_this_machine(host: str) -> bool:
    """Might this ssh destination be THIS machine? A loopback spelling or this computer's own name
    is; so is a host in a variable, which this command cannot name. Anything else is another
    machine, whose paths are not this one's protected folders."""
    if has_unresolved(host):
        return True
    h = host.lower()
    if h.startswith("ssh://"):
        h = h[6:]
    h = h.rpartition("@")[2].rstrip(".").partition("/")[0]
    if h.startswith("["):
        h = h[1:].partition("]")[0]
    if _is_loopback(h) or (h.count(":") == 1 and _is_loopback(h.partition(":")[0])):
        return True
    h = h.partition(":")[0] or h
    import socket                        # only a command that actually runs ssh pays for this
    own = {os.environ.get("COMPUTERNAME", ""), os.environ.get("HOSTNAME", "")}
    try:
        own.add(socket.gethostname())
    except OSError:
        pass
    return h in {n.lower().partition(".")[0] for n in own if n} or h.partition(".")[0] in {
        n.lower() for n in own if n}


#: The remote commands of an `ssh <host>` the guard cannot place (not recognised as this machine),
#: for this facts() call, and how deep facts() is in judging them (round 6, C): `ssh a ssh b <cmd>`
#: is one level more, and _MAX_SSH_DEPTH bounds it.
_SSH_UNPLACED: list = []
#: ...and the same commands spliced into the command around them, which this machine expands first.
_SSH_UNPLACED_SPLICED: list = []
_SSH_NESTED = [0]
#: Per hook call (round 7, 1): judgements already made, by text, and how many facts() runs the advisory
#: spent. Past _MAX_SSH_JUDGED runs, or _SSH_ADVISORY_S seconds, the advisory stops and ADVISES (it could
#: not judge the rest); it never costs the verdict, which is computed before it.
_SSH_MEMO: dict = {}
_SSH_JUDGED = [0]
_MAX_SSH_JUDGED = 24                  # every remote and spliced text of _MAX_SSH_UNPLACED ssh segments, and more
_SSH_ADVISORY_S = 2.0
_MAX_SSH_DEPTH = 4                    # as deep as the carrier walk goes (_MAX_CARRIER_DEPTH)
#: How many of them facts() judges; past it the rest are not judged, and the advisory says nothing
#: about them (it is advice, never a verdict).
_MAX_SSH_UNPLACED = 8
#: The facts that make a remote command DESTRUCTIVE had it run here: each is a deny or an ask in
#: protect-bash.sh, or the in-X4-folder delete advisory.
_SSH_DESTRUCTIVE = ("rm_hits_game", "rm_targets_reference", "writes_reference", "writes_game_archive",
                    "writes_host_transcript", "writes_agent_settings", "link_into_protected", "rm_in_profile",
                    "rm_deployed_mod", "rm_in_x4_dir", "rm_saves", "writes_profile_other", "git_wipes_x4_dir",
                    "git_wipe_from_session_dir", "sed_i_in_game_or_profile", "git_add_all",
                    "durable_truncating_redirect", "program_dynamic_protected", "verb_unresolved",
                    "links_unresolved")

#: ssh options whose value runs on THIS machine, whatever the destination.
_SSH_LOCAL_COMMAND = re.compile(r"(?:proxycommand|localcommand|knownhostscommand)\s*[= ]\s*(.*)", re.I | re.S)


def _ssh_readings(words: list) -> list:
    """`ssh <this machine> <cmd>`. ssh's remote command starts in the login directory, never in
    this command's directory, so the reading anchors there: a relative path stays unknown."""
    at = _program_at(words, _SSH_VALUE_OPTS)
    out = []
    for i, w in enumerate(words if at is None else words[:at]):
        t = _head(w)
        value = t[2:] if t.startswith("-o") and len(t) > 2 else (
            _head(words[i + 1]) if t == "-o" and i + 1 < len(words) else "")
        m = _SSH_LOCAL_COMMAND.fullmatch(value)
        if m:
            out.append(m.group(1))
    if at is None or at >= len(words):
        return out
    tail = words[at + 1:]
    k = _program_at(tail, _SSH_VALUE_OPTS)
    if k is None or k >= len(tail):
        return out
    remote = ["cd " + _UNKNOWN_CWD + " && " + r for r in _as_program(tail[k:], True)]
    if not _is_this_machine(_head(words[at])):
        # Another machine's paths are not this one's protected folders, so these are no reading
        # here. But the guard cannot tell a LAN address, a Tailscale name or an ~/.ssh/config
        # alias for this machine from another one: facts() judges them apart and only ADVISES
        # (4.1.1 review round 6, C).
        _SSH_UNPLACED.extend(remote)
        return out
    return out + remote


def _parallel_readings(words: list, prev) -> list:
    """GNU parallel: a command template and its arguments after `:::`. It runs the template through
    a shell with `{}` filled in or the argument appended; with no template each argument IS a
    command; with no `:::` the arguments come from stdin, which is what xargs_feed reads."""
    at = _program_at(words, _PARALLEL_VALUE_OPTS)
    if at is None:
        return list(_echo_printf_program(prev)) if prev and _verb_name(verb(prev)) in ("echo", "printf") else []
    tail = words[at:]
    sep = next((i for i, w in enumerate(tail) if _head(w) in _PARALLEL_SEPS), None)
    if sep is None:
        # The template still runs through a shell, which re-reads an escaped separator (4.1.1
        # review round 5, 1: `... | parallel echo a\\; rm -rf <x>`).
        return ["xargs " + r for r in _as_program(tail, True)]
    template, args, in_file = tail[:sep], [], False
    for w in tail[sep:]:
        t = _head(w)
        if t in _PARALLEL_SEPS:
            in_file = t.startswith("::::")
        elif not in_file:
            args.append(w)
    if not template:
        return [_head(a) for a in args]
    joined_args = " ".join(args)
    opened = " ".join(t for t, _q in tokens(" ".join(template)))
    filled = (_PARALLEL_SLOT.sub(lambda _m: joined_args, opened) if _PARALLEL_SLOT.search(opened)
              else opened + " " + joined_args)
    out = [" ".join(template + args), filled]
    return list(dict.fromkeys(out))


def _tmux_readings(words: list) -> list:
    """tmux commands that run a shell command: every quoted word holding a blank is such a text,
    and an unquoted tail is argv. `send-keys` types its text into a shell, which is the same thing
    as far as a guard can tell. `tmux -c TEXT` runs TEXT. Chained with `;`."""
    out = []
    i = 0
    while i < len(words):
        t = _head(words[i])
        if t == "-c" and i + 1 < len(words):
            out.append(_head(words[i + 1]))
            i += 2
        elif t in _TMUX_GLOBAL_VALUE_OPTS:
            i += 2
        elif t.startswith("-"):
            i += 1
        else:
            break
    chunk = []
    for w in words[i:] + [";"]:
        h = _head(w)
        if h != ";" and h.endswith(";") and w.endswith(chr(92) + ";"):
            # `tmux ls\; run '<cmd>'`: a separator glued to the word before it. segments() split
            # there until it learned that `\;` is literal (4.1.1 review round 4, 7).
            chunk.append(w[:-2])
            h = ";"
        if h != ";":
            chunk.append(w)
            continue
        if chunk:
            sub = _head(chunk[0])
            if sub in _TMUX_ALIASES or (len(sub) >= 3 and any(f.startswith(sub) for f in _TMUX_RUNS)):
                out.extend(_tmux_command_texts(chunk[1:]))
        chunk = []
    return out


def _tmux_command_texts(words: list) -> list:
    out, i, argv_at = [], 0, None
    while i < len(words):
        toks = tokens(words[i])
        t, quoted = toks[0] if toks else ("", False)
        if not quoted and t == "--":
            argv_at = i + 1
            break
        if not quoted and t in _TMUX_VALUE_OPTS:
            i += 2
            continue
        if not quoted and t.startswith("-"):
            i += 1
            continue
        if not quoted and re.fullmatch(r"[0-9]+%?", t):
            i += 1
            continue
        if quoted and _EVAL_SPLITS.search(t):
            out.append(t)
        elif argv_at is None and not quoted:
            argv_at = i
            break
        i += 1
    if argv_at is not None and argv_at < len(words):
        out.extend(_as_program(words[argv_at:]))
    return out


def _mapfile_readings(words: list) -> list:
    """`mapfile -C CALLBACK -c N`: bash EVALS `CALLBACK <index> <line>` for each line read."""
    text = ""
    for i, w in enumerate(words):
        t = _head(w)
        if t == "-C" and i + 1 < len(words):
            text = _head(words[i + 1])
            break
        if t.startswith("-C") and len(t) > 2:
            text = t[2:]
            break
    if not text:
        return []
    seg = " ".join(words)
    hs = _here_string(seg)
    return [text + " 0 " + _sh_quote(hs) if hs else text]


#: `${P@X}` transforms that keep the value's path: P/E re-expand escapes, Q quotes it, the rest
#: change case or list it. MEASURED: `P=<reference>; rm -rf "${P@P}"` carried no guard flag.
_VALUE_TRANSFORM = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)@[PEQUuLKk]\}")

#: `$-` is bash's flag string; zsh's `$=X` `$~X` `$^X` `$+X` `${=X}` read X with a flag. Bash and the
#: resolver take most of these literally: MEASURED, `rm -rf <reference>$-` read as a sibling folder
#: with no guard flag. Read here as the expansions they may be, never in the resolver itself, which
#: lost verdicts the literal reading has (`> $-` from the game root).
_FLAGGED_EXPANSION = re.compile(r"\$\{[=~^]+(?=[A-Za-z_])|\$[=~^+](?=[A-Za-z_{])|\$-")


def _plain_expansion(m) -> str:
    s = m.group(0)
    return "${X4_SHELL_FLAGS}" if s == "$-" else "${" if s.startswith("${") else "$"


#: The extensions root this call judges by: the configured one, else the game's (x4-paths.env.example
#: documents that default). Set by facts(), read by the reading below.
_EXTENSIONS_ROOT = [""]
_EXTENSIONS_VAR = re.compile(r"\$\{X4_(?:GAME_)?EXTENSIONS\}|\$X4_(?:GAME_)?EXTENSIONS(?![A-Za-z0-9_])")
_EXTENSIONS_SET = re.compile(r"(?<![A-Za-z0-9_])X4_(?:GAME_)?EXTENSIONS=")


def _extensions_spelled(text: str) -> str:
    """`$X4_EXTENSIONS` written out as the folder, for a reading. It is not in ROOT_VARS because a root
    variable is known wherever it is read: as a program name it would stop being an unknown program, and
    `${X4_EXTENSIONS:-<x>}` would stop being read as <x>. A root this cannot quote gives no reading, and
    neither does a command that assigns the variable itself, which is the value it then deletes under."""
    root = _EXTENSIONS_ROOT[0]
    if not root or "EXTENSIONS" not in text or any(c in root for c in "\"'$`") or _EXTENSIONS_SET.search(text):
        return text
    quote = {i: kind for i, _c, _inq, kind in _qwalk(text)}
    return _EXTENSIONS_VAR.sub(lambda m: root if quote.get(m.start()) else _sh_quote(root), text)


def _value_readings(cmd: str, segs: list) -> list:
    """The command again with `${P@X}` and zsh's flagged forms read as plain expansions and each
    `printf -v`/`read <<<` variable written out as its value. ADDITIVE, like every reading here: the command as written is still
    judged with those names unknown, so nothing it was flagged for can be lost."""
    text = _VALUE_TRANSFORM.sub(lambda m: "${" + m.group(1) + "}", cmd) if "@" in cmd else cmd
    text = _FLAGGED_EXPANSION.sub(_plain_expansion, text)
    text = _extensions_spelled(text)
    if ("printf" in cmd and "-v" in cmd) or ("read" in cmd and "<<<" in cmd):
        for seg in segs:
            for name, value in _builtin_assignment(seg).items():
                text = re.sub(r"\$\{" + name + r"\}|\$" + name + r"(?![A-Za-z0-9_])",
                              lambda _m, v=value: v, text)
    return [text] if text != cmd else []


def _program_carrier_readings(seg: str, v: str, vt: str, prev) -> list:
    """What a program that runs ANOTHER program may run, as readings of this segment. Like
    dynamic_program_readings (and spliced in place the same way, so a `cd` before it still
    applies) they are ADDITIVE: the segment's own verb and verdict stand, a reading that is not a
    command matches no rule, and each goes through the same tolerant walk as any carried text.
    An unreadable inner command is not a clean pass either: a variable there reaches
    `program_dynamic_protected` as any other unknown program does."""
    out = []
    if vt and "," in vt and vt in _BRACE_WORDS:
        # `{eval,} <cmd>`, `{rm,-rf,<x>}`: bash brace-expands the name into words. A range inside
        # one is not a truncated operand scope: no operand is being judged here.
        was = _OPERAND_EXPANSION_TRUNCATED[0]
        parts = [_sh_quote(p) for p in _brace_expand(vt) if p]
        _OPERAND_EXPANSION_TRUNCATED[0] = was
        rest = _verb_word_text(seg)[1].strip()
        if parts:
            out.append(" ".join(parts + ([rest] if rest else [])))
    if v == "env" or _env_candidate(seg):
        out.extend(_env_split_readings(seg))
    if "exec" in seg or "sudo" in seg:
        out.extend(_value_word_readings(seg))
    if v == "find" and ("-exec" in seg or "-ok" in seg):
        out.extend(_find_exec_readings(seg))
    if v in ("fd", "fdfind") and ("-x" in seg or "-X" in seg or "--exec" in seg):
        out.extend(_fd_exec_readings(seg))
    if prev is not None and "xargs" in seg:
        out.extend(_xargs_item_readings(seg, prev))
    if v not in _ALL_CARRIER_VERBS:
        if v not in _NOT_RUNNERS and v not in WRAPPERS and _MAY_CARRY_JOINER.search(seg):
            out.extend(_joiner_behind_unknown(seg))
        return out
    words = _raw_words(_verb_word_text(seg)[1].strip())
    if v in _ARGV_CARRIERS:
        opts, positional, numeric = _ARGV_CARRIERS[v]
        at = _program_at(words, opts, positional, numeric)
        if at is not None and at < len(words):
            tail = words[at:]
            if v == "screen" and _head(tail[0]) in ("stuff", "exec"):
                tail = tail[1:]
            readings = _as_program(tail, _joins_for_a_shell(v, words[:at])) if tail else []
            if v == "wsl":
                # WSL spells this machine's drives /mnt/<drive>/.
                readings += [_WSL_MOUNT.sub(lambda m: m.group(1) + ":", r) for r in readings if "/mnt/" in r]
            out.extend(readings)
    if v in _SPECIAL_CARRIERS:
        out.extend(_special_carrier_readings(v, words))
    if v in _TEXT_C_CARRIERS:
        out.extend(_text_option(words))
    if v == "ssh":
        out.extend(_ssh_readings(words))
    elif v == "parallel":
        out.extend(_parallel_readings(words, prev))
    elif v == "tmux":
        out.extend(_tmux_readings(words))
    elif v in ("mapfile", "readarray"):
        out.extend(_mapfile_readings(words))
    return out


def _special_carrier_readings(v: str, words: list) -> list:
    """The program `gdb --args`, `start` and `autossh` run (_SPECIAL_CARRIERS)."""
    heads = [_head(w) for w in words]
    if v == "gdb":
        # Without --args gdb's operand is a program FILE it loads, not one it runs.
        at = heads.index("--args") + 1 if "--args" in heads else None
        return _as_program(words[at:]) if at is not None and at < len(words) else []
    if v == "autossh":
        k = 0
        while k < len(words):
            if heads[k] == "-M":
                k += 2
            elif heads[k] in ("-f", "-V") or heads[k].startswith("-M"):
                k += 1
            else:
                break
        return ["ssh " + " ".join(words[k:])] if k < len(words) else []
    # start: `/b`, `/wait`, `/min`, `/D <dir>`, then an optional DOUBLE-QUOTED title, then the program.
    k = 0
    while k < len(words) and heads[k][:1] == "/" and len(heads[k]) > 1 and "/" not in heads[k][1:]:
        k += 2 if heads[k].lower() in _START_VALUE_SWITCHES else 1
    if k < len(words) and words[k][:1] == chr(34):
        k += 1
    return _as_program(words[k:]) if k < len(words) else []


#: Verbs that print or look up the words after them rather than running them, for
#: _joiner_behind_unknown: `echo ssh localhost rm -rf <x>` runs nothing.
_NOT_RUNNERS = frozenset({"echo", "printf", "man", "which", "type", "whereis", "whatis", "apropos", "info",
                          "help", "tldr", "hash", "alias", "unalias", "complete", "compgen", "declare",
                          "local", "export", "readonly", "set", "unset", "grep", "egrep", "fgrep", "rg"})
_SHELL_JOINERS = ("ssh", "watch", "wsl")
_MAY_CARRY_JOINER = re.compile(r"(?<![A-Za-z0-9_.-])(?:ssh|watch|wsl)(?:[.]exe)?(?![A-Za-z0-9_.-])")


def _joiner_behind_unknown(seg: str) -> list:
    """`<unknown wrapper> [-opts] ssh|watch|wsl ...`: a program this file does not know, whose first
    operand is a carrier that joins its words for a shell, may be running it -- `sshpass`-like tools
    nobody has listed yet. The words from that carrier on are a reading of their own, so its escaped
    separator is re-read as it would be (4.1.1 review round 6, B). Additive, like every reading."""
    words = _raw_words(_verb_word_text(seg)[1].strip())
    k = next((i for i, w in enumerate(words) if not _head(w).startswith("-")), None)
    if k is None or k + 1 >= len(words) or _verb_name(_head(words[k])) not in _SHELL_JOINERS:
        return []
    return [" ".join(words[k:])]


_ALL_CARRIER_VERBS = (frozenset(_ARGV_CARRIERS) | _TEXT_C_CARRIERS | _SPECIAL_CARRIERS
                      | {"ssh", "parallel", "tmux", "mapfile", "readarray"})
#: The shells whose `-c` text (or quoted-delimiter heredoc) may be EXACT: zsh is left out because
#: it reads .zshenv on every run (see _carries_program_text), so its text is not all it runs.
_EXACT_SHELLS = frozenset({"bash", "sh", "dash", "ksh"})
#: The command names a glob must be able to spell before its name counts as unknown.
_GLOB_SPELLABLE = (DELETE_VERBS | COPY_VERBS | _TRASH_VERBS | WRAPPERS | _ALL_CARRIER_VERBS | set(_SHELLS)
                   | {"eval", "source", ".", "trap", "ln", "mklink", "touch", "truncate", "dd", "sed", "find",
                      "git", "cmd", "powershell", "pwsh", "mkdir", "chmod", "chown", "del", "erase", "rd", "icacls"})
_ENV_FLAG = re.compile(r"\s-[A-Za-z0-9]*[SuC]|\s--s")


def _env_candidate(seg: str) -> bool:
    """The cheap test in front of _env_split_readings: env is usually absent."""
    return "nv" in seg and bool(_ENV_FLAG.search(seg))


def _inner_commands(cmd: str, exact: set | None = None) -> list[str]:
    """Command strings hidden inside a wrapper, so the rules see them too.

    Carriers: a shell's `-c` argument, `eval`, `trap`, a shell reading stdin, and (HK-2)
    `cmd /c` and a PowerShell host's -Command/-EncodedCommand. Each takes TEXT and runs
    it as a command, so anything it carries is invisible to every rule that inspects
    segments.

    `exact`, when given, receives the carried texts that are BYTE-FOR-BYTE what the inner
    shell is handed: a `-c` argument (bash/sh/dash/ksh, the segment as written) in which the
    OUTER shell expanded nothing (every `$` and backtick quoted or escaped, no unquoted glob
    or brace). Only such a text keeps its own quoting as evidence that a `$` in it is
    literal (see facts' literal_dollar_path). Every other carrier -- eval, a heredoc or
    here-string, echo | sh, a translated cmd/PowerShell command, a substitution, a reading,
    positional parameters filled in, ssh/watch/wsl and the other joiners -- may have been
    expanded or re-quoted on the way in, so its `'REF$X'` may really be `reference/` itself.
    A text produced by any non-exact route is never exact, even if an exact route produced
    it too (4.1.1 port of 6cbeb825).
    """
    out = []
    exact_idx = set()
    segs = segments(cmd)
    pipes = piped_in(cmd)
    if len(pipes) != len(segs):          # never guess the pairing short: assume piped
        pipes = [True] * len(segs)
    assigns_ = None
    dynamic = {}                         # F10: segment index -> its readings
    carrying = {}                        # _program_carrier_readings: the same, kept apart
    handled = {}                         # ...and the not-found handler's, read once
    tmux_chain = False                   # the last segment was a tmux command ending in `\\;`
    ssh_here = {}                        # round 6, C: segment index -> its unplaced remote commands
    not_found_handler = "command_not_found_handle" in cmd
    if "@P}" in cmd:
        # `${P@P}` runs prompt expansion, whose `$(...)` executes, over P's stored value.
        table = assignments(cmd)
        for name in set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)@P\}", cmd)):
            out.extend(substitutions(table.get(name, "")))
    for n, seg in enumerate(segs):
        written = seg
        # F10: a command NAME from a variable. One this command assigns a bare name to is
        # spliced in first, so `R=eval; $R '<cmd>'` reaches the eval arm below (MEASURED:
        # allowed); one it cannot name contributes its speculative readings.
        vt = _verb_token(seg)
        if has_unresolved(vt or ""):
            if assigns_ is None:
                assigns_ = assignments(cmd)
            seg = resolve_verb(seg, assigns_)
            readings = dynamic_program_readings(seg)
            if readings:
                dynamic[n] = readings
            vt = _verb_token(seg)
        elif _glob_program(seg, vt):
            readings = dynamic_program_readings(seg)
            if readings:
                carrying[n] = readings
        v = _verb_name(vt)              # one read per segment: this walk runs on every hook call
        _check_deadline()
        unplaced = len(_SSH_UNPLACED)
        carried_here = _program_carrier_readings(seg, v, vt, segs[n - 1] if n and pipes[n] else None)
        if len(_SSH_UNPLACED) > unplaced:
            ssh_here[n] = _SSH_UNPLACED[unplaced:]
        if tmux_chain:
            carried_here = carried_here + _tmux_readings(_raw_words(seg))
        tmux_chain = (tmux_chain or v == "tmux") and seg.rstrip().endswith(chr(92))
        if not_found_handler:
            # A function bash calls with the words of any command it cannot find. It may run
            # them, whichever word it starts from.
            whole = _raw_words(seg)
            readings = [r for k in (0, 1) if whole[k:] for r in _as_program(whole[k:], True)]
            if readings:
                handled[n] = readings
        if carried_here:
            carried_here = list(dict.fromkeys(carried_here))
            if len(carried_here) > _MAX_SEGMENT_READINGS:
                # Each reading is the WHOLE command again (_reading_variants): unbounded, the text
                # grows as readings x length (round 8: 200 -exec actions ran 6.8 s). Past the bound
                # the command is too tangled to analyse, and says so (carriers_truncated).
                _READINGS_TRUNCATED[0] = True
                carried_here = carried_here[:_MAX_SEGMENT_READINGS]
            carrying[n] = carrying.get(n, []) + carried_here
        if v in ("cmd",) + _PS_EXES:
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
                    at = _shell_text_at(toks, i + 1, t)
                    arg = toks[at][0]
                    values = resolve_variants(arg, assignments(cmd))
                    if (exact is not None and seg == written and v in _EXACT_SHELLS
                            and arg in values and not _reparsing_wrapper(seg)):
                        lx = {}
                        tokens(seg, literal_expansions=lx)
                        if lx.get(arg, False) and arg not in _BRACE_WORDS:
                            exact_idx.add(len(out) + values.index(arg))
                    out.extend(values)
                    # ...and with its positional parameters filled in from the words after it
                    # (round 7, 2): `sh -c 'rm -rf "$1"' _ <ref>`, `-exec sh -c 'echo > "$1"' _ {} ;`.
                    args = _drop_redirects([w for w, _q in toks[at + 1:]])
                    prev_ = segs[n - 1] if n and pipes[n] else None
                    if prev_ is not None and any(_verb_name(w) == "xargs" for w, _q in toks[:i]):
                        # `... | xargs sh -c '<cmd> "$@"' _`: xargs appends what it reads, so "$@" is
                        # the producer's items, not empty (round 8: an empty fill read as `.`).
                        args = args + _producer_items(_stdin_producer(segs[:n]))
                    filled = _positional_text(toks[at][0], args)
                    if filled != toks[at][0]:
                        out.append(_inline_assigned(filled, toks[at][0]))
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
            # Every word is the command's, its flags included (4.1.1 review round 5, 2: `eval git
            # add -A` read as `git add`). eval's own `--` stays: a reading of `-- git add -A` is
            # still judged as git (the mutation gate cannot tell a step over it apart).
            parts = [t for t, _ in toks[k + 1:]]
            if parts:
                out.extend(resolve_variants(" ".join(parts), assignments(cmd)))   # FX-G6 / K C1
                out.extend(_requoted(toks[k + 1:], _escapes_a_split(seg)))
        elif v == "trap":
            # `trap <cmd> <SIGNAL...>` runs its first operand as a command when the
            # signal fires. That operand is normally single-quoted, which is exactly
            # why nothing saw it: quoted text is data everywhere else in this file.
            k = next((i for i, (t, _) in enumerate(toks)
                      if _verb_name(t) == "trap"), 0)
            rest = [t for t, _ in toks[k + 1:] if not t.startswith("-")]
            if rest:
                out.append(rest[0])
    for n, remote in ssh_here.items():
        # ...each IN PLACE, as every carrier reading is: the remote command is expanded HERE first, so
        # `FZ=<ref>; ssh <host> rm -rf "$FZ"` sends the path (round 6, C). Every OTHER unplaced ssh is a
        # no-op there, so one judgement never re-judges the rest (round 7, 1: n segments recursed n deep).
        others = {m: [":"] for m in ssh_here if m != n}
        _SSH_UNPLACED_SPLICED.extend(_reading_variants(cmd, segs, {n: remote, **others}))
    out.extend(_reading_variants(cmd, segs, dynamic))
    rewritten = _value_readings(cmd, segs)
    if rewritten:
        _READING_TEXTS.update(rewritten)
        out.extend(rewritten)
    if carrying:
        variants = _reading_variants(cmd, segs, carrying)
        _READING_TEXTS.update(variants)
        out.extend(variants)
    if handled:
        # Without the handler's name, or each reading would read itself one word shorter until the
        # depth bound reported a harmless `a b c d e f` as too tangled to analyse.
        variants = [t.replace("command_not_found_handle", "x4_read_handler")
                    for t in _reading_variants(cmd, segs, handled)]
        _READING_TEXTS.update(variants)
        out.extend(variants)
    if exact is not None:
        loose = {t for k, t in enumerate(out) if k not in exact_idx}
        exact.update(out[k] for k in exact_idx if out[k] not in loose)
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

#: Round 8 (F1): the readings of one segment, and the characters of every carried text together. A
#: reading or a fill repeats the command around it, so a count bound alone still let the TEXT grow
#: quadratically. Past either, the walk stops and reports carriers_truncated (a refusal).
_MAX_SEGMENT_READINGS = 32
_MAX_CARRIED_CHARS = 400000
_READINGS_TRUNCATED = [False]


def carried_commands(body: str, extra: list, dialects: dict | None = None, *,
                     exact: dict | None = None) -> tuple:
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

    `exact` (text -> bool), when given, is SEEDED by the caller for `body` and each
    `extra` (True only where that text is exactly what a shell parses) and is filled for
    every text returned: exact only when EVERY route that reaches it is exact -- an exact
    Bash parent handing on a `-c` argument the outer shell expanded nothing in (see
    _inner_commands). Anything not known exact is False, which keeps a `$` in it an
    unresolved expansion: the stricter reading.
    """
    edges = []                          # (parent, child, exact hand-off?) -- every route

    def finish(result, trunc):
        if exact is not None:
            # A root (`body`, an `extra`) starts from its seed; a carried text starts
            # True and is lowered by any route into it that is not an exact hand-off.
            roots_ = {body, *extra}
            ex = {t: (exact.get(t, False) if t in roots_ else True) for t in result}
            ex[body] = exact.get(body, False)
            changed = True
            while changed:              # only ever LOWERS a value, so it terminates
                changed = False
                for parent, child, handed in edges:
                    if ex.get(child, False) and not (handed and ex.get(parent, False)):
                        ex[child] = False
                        changed = True
            exact.clear()
            exact.update(ex)
        return result, trunc

    d = {} if dialects is None else dialects
    d.setdefault(body, "bash")
    seen = {body}
    out = [body]
    frontier = [body]
    truncated = False
    size = len(body) + sum(len(e) for e in extra)
    for e in extra:
        d.setdefault(e, WINDOWS_DIALECT if e in _WIN_DIALECT else "bash")
        if e not in seen:
            if len(out) >= _MAX_CARRIED:
                return finish(out, True)
            seen.add(e)
            out.append(e)
            frontier.append(e)
    # One discovery-only pass distinguishes a leaf at the boundary from a
    # reachable command that would otherwise disappear beyond the depth cap.
    for depth in range(_MAX_CARRIER_DEPTH + 1):
        nxt = []
        for c in frontier:
            _check_deadline()
            exact_inners = set()
            inners = _inner_commands(c, exact_inners)
            subs = substitutions(c)
            for i_, kind, handed in ([(x, WINDOWS_DIALECT if x in _WIN_DIALECT else "bash",
                                       x in exact_inners and x not in _WIN_DIALECT
                                       and d.get(c, "bash") == "bash")
                                      for x in inners]
                                     + [(x, d.get(c, "bash"), False) for x in subs]):
                for n_, inner in enumerate(_carrier_parts(i_)):
                    if inner:
                        d.setdefault(inner, kind)
                        # Recorded BEFORE the dedup below: a text reached again by a
                        # non-exact route must lose exactness it was granted earlier.
                        edges.append((c, inner, handed and n_ == 0))
                    if not inner or inner in seen:
                        continue
                    if c in _READING_TEXTS or i_ in _READING_TEXTS:
                        _READING_TEXTS.add(inner)
                    if depth == _MAX_CARRIER_DEPTH:
                        return finish(out, True)
                    seen.add(inner)
                    nxt.append(inner)
                    size += len(inner)
                    if size > _MAX_CARRIED_CHARS:
                        out.extend(nxt)
                        return finish(out, True)
                    if len(out) + len(nxt) >= _MAX_CARRIED:
                        # STOP, and SAY SO. Dropping the rest and returning a verdict
                        # would be a step that narrows its data and reports success --
                        # the shape behind every tool defect found in this workspace.
                        out.extend(nxt)
                        return finish(out[:_MAX_CARRIED], True)
        if not nxt:
            break
        out.extend(nxt)
        frontier = nxt
    return finish(out, truncated)


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


#: THE ANALYSIS DEADLINE for one hook call, in seconds (4.1.1 review round 7, 1). Claude Code gives the
#: hook 30 s and a hook that times out does NOT block, so an analysis that outran it failed OPEN (MEASURED
#: at 9cd83006: nine `ssh <host> ls` segments took 128 s and were allowed). Every bounded walk below
#: checks it; past it the command is refused (`analysis_timeout`), never allowed. The save bridge passes
#: its own, earlier deadline (save_policy.HOOK_BUDGET_S, less the backend's share).
_ANALYSIS_BUDGET_S = 18.0
_DEADLINE = [None]


class AnalysisTimeout(Exception):
    """The analysis deadline passed: the command was not fully analysed."""


def _check_deadline():
    d = _DEADLINE[0]
    if d is not None and _clock() > d:
        raise AnalysisTimeout()


def facts(payload: dict, roots: dict, *, evidence: list | None = None, execution_context: bool = False,
          protected_save_folders=(), deadline: float | None = None) -> dict:
    """The facts of one command (_facts), within the analysis deadline. A judgement nested inside one
    (the ssh advisory) shares its caller's deadline and lets a timeout reach it."""
    if _SSH_NESTED[0]:
        return _facts(payload, roots, evidence=evidence, execution_context=execution_context,
                      protected_save_folders=protected_save_folders)
    _DEADLINE[0] = deadline if deadline is not None else _clock() + _ANALYSIS_BUDGET_S
    _SSH_MEMO.clear()
    _SSH_JUDGED[0] = 0
    _LINK_RESOLVED[0] = 0               # link_resolved's realpath budget and drive cache: per hook call
    _LINK_PROBED[0] = 0
    _LINK_DIRS.clear()
    _LINKS_UNRESOLVED[0] = False
    _DRIVE_TYPES.clear()
    _ps_spent[0] = 0.0                  # the translation budget is per hook call, never per judgement
    try:
        return _facts(payload, roots, evidence=evidence, execution_context=execution_context,
                      protected_save_folders=protected_save_folders)
    except AnalysisTimeout:
        return {"command": (payload.get("tool_input") or {}).get("command") or "",
                "analysis_timeout": "the guard's analysis deadline passed before this command was fully "
                                    "analysed"}
    finally:
        _DEADLINE[0] = None


def _facts(payload: dict, roots: dict, *, evidence: list | None = None, execution_context: bool = False,
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
    _READING_TEXTS.clear()
    del _SSH_UNPLACED[:]                # round 6, C: per call (a nested judgement clears its own)
    del _SSH_UNPLACED_SPLICED[:]
    _READINGS_TRUNCATED[0] = False
    _EXTENSIONS_ROOT[0] = (roots.get("extensions")
                           or (roots["game"].rstrip("/" + chr(92)) + "/extensions" if roots.get("game") else "")
                           ).replace(chr(92), "/")
    _CD_LANES_TRUNCATED[0] = False      # C7: per call
    _OPERAND_EXPANSION_TRUNCATED[0] = False

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
            return {"command": cmd, "powershell_error": why or "not translated",
                    "untranslated_category": untranslated_category([why])}
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
    # LITERAL `$` (4.1.1 port of 6cbeb825/51129cde/ed667b4a): which texts a shell parses
    # exactly as they are spelled here -- the top level itself, and quoted-delimiter heredoc
    # bodies fed straight to a shell. Everything else is seeded False, and carried_commands
    # lowers, never raises. NOTHING is exact when the command can redefine what a later word
    # runs through (function, alias, source, trap ...), names a startup-steering variable,
    # holds a raw NUL (the text bash receives ends there), is a remote command judged for an
    # unplaced ssh (the remote shell re-parses it), or holds no `$` at all (nothing to qualify).
    raw_cmd = inp.get("command") or ""
    no_exact = ("$" not in cmd or _SSH_NESTED[0] > 0 or _SHELL_STARTUP_ENV[0]
                or _redefines_commands(raw_cmd) or _redefines_commands(cmd)
                or chr(0) in raw_cmd or chr(0) in cmd)
    exact_texts = {} if no_exact else {e: False for e in extra}
    if not no_exact:
        exact_bodies = exact_heredoc_bodies(spliced)
        exact_texts = {e: e in exact_bodies and extra.count(e) == 1 for e in extra}
        exact_texts[body] = body not in exact_texts
    all_cmds, carriers_truncated = carried_commands(body, extra, dialects,
                                                    exact=None if no_exact else exact_texts)
    carriers_truncated = carriers_truncated or _READINGS_TRUNCATED[0]
    if not no_exact and (carriers_truncated or _no_literal_anywhere(
            [cmd, body] + list(extra) + list(all_cmds))):
        # (A PowerShell tool command is read here as its TRANSLATION; its raw text went
        # through _redefines_commands above. Bash rules over PowerShell syntax misread it.)
        exact_texts = {}
    # ...and each one with its separator-holding substitutions collapsed (FX-G6 / reviewer
    # K I4): `A=$(echo a; echo b) rm -rf <game>` -- see _spanning_view. Inserted after the
    # top level, so the last command (and the `cwd` fact read from it) is unchanged; the
    # top level's own view is seeded with the session directory exactly as the top level is.
    top_view = _spanning_view(body)
    views = []
    for c_ in all_cmds:
        v_ = _spanning_view(c_)
        if v_ != c_ and v_ not in all_cmds and v_ not in views:
            if c_ in _READING_TEXTS:
                _READING_TEXTS.add(v_)
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
    seg_exact = []                      # is the segment's text exactly what a shell parses?
    base = session_cwd(payload)
    # A carried command (bash -c, a heredoc fed to a shell, $(...)) is walked on its own,
    # so it cannot see a `cd` made around it. When nothing anywhere changes directory it
    # runs in the session's directory too; once anything does, where it runs is not
    # known here, and it keeps today's "unknowable" rather than a guessed directory.
    moves = any(verb(s) in DIR_VERBS or verb(s) == "popd"
                for c_ in all_cmds if c_ not in _READING_TEXTS for s in segments(c_))
    for i_, c in enumerate(all_cmds):
        top = i_ == 0 or c == top_view
        tracked = cwd_lanes(c, base if (top or not moves) else "", seeded=True, roots=roots)
        # The directory each segment had BEFORE lane F (no session seed), for the two
        # rules whose verdict is an ASK outside the profile -- see `gitwipe_t` below.
        # Lane j of one walk is lane j of the other (cwd_lanes splits per VALUE, not per
        # directory), so a segment is judged once per (seeded, unseeded) lane pair (FX-G7).
        bare = cwd_lanes(c, roots=roots)
        physical = cwd_lanes(c, base if (top or not moves) else "", seeded=True,
                             roots=roots, physical=True)
        heads = _for_headers(c)
        for k, (s, ds) in enumerate(tracked):
            uds = bare[k][1] if k < len(bare) else [""]
            rs = resolve_verb(s, assigns)
            # The segment BEFORE each one in the same carried command: what feeds an xargs.
            prev = _stdin_producer([t[0] for t in tracked[:k]])
            pds = physical[k][1] if physical else [""] * len(ds)
            for d, ud, pd in dict.fromkeys(zip(ds, uds if len(uds) == len(ds) else uds[:1] * len(ds), pds)):
                seg_dialect.append(dialects.get(c, "bash"))
                seg_exact.append(exact_texts.get(c, False))
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

    modlist_writes = set()
    non_modlist_writes = set()
    # A move is not on the list: it also removes its source, which may be a save.
    # One recognized data-write primitive only. Compound commands can change a
    # destination's kind or link state before a later write. Retain their prior
    # profile confirmation rather than infer future filesystem state from now.
    simple_modlist_write = (len(all_cmds) == 1
        and (len(seg_cwd) == 1
             and _verb_name(verb(seg_cwd[0][0])) in {':', 'echo', 'printf', 'cat', 'tee', 'cp', 'touch'}
             or _modlist_tee_pipeline(all_cmds[0], [s for s, _d in seg_cwd]))
        and not any(marker in body for marker in ('$(', '`', '<(', '>(')))

    literal_expansions = {}

    def literal_words(seg, exact_text):
        # Which of this segment's words have NO expansion-active `$` -- but only when the
        # segment's text is exactly what a shell parses (seg_exact) and no wrapper in it runs the
        # command through a re-parsing shell (sudo -s). In a command handed on by eval, an
        # unquoted heredoc, `echo | sh`, ssh/watch/wsl and the other carriers, a translated
        # cmd/PowerShell line, or a `-c` string the outer shell expanded, `'REF$X'` may already
        # be `REF<value of X>`, i.e. reference/ itself: those texts are never exact. (The
        # carrier's OWN segment -- `ssh host rm '<x>$X'` -- reaches no operand rule as `rm`; a
        # per-word carrier test here was MEASURED unobservable through every rule and dropped.)
        found = {}
        if exact_text and not _reparsing_wrapper(seg):
            tokens(seg, literal_expansions=found)
        return found

    def literal_dollar_path(path):
        # Only a plain absolute path whose dollars were all quoted/escaped, in a segment a
        # shell parses exactly as written (literal_words). Pattern membership and relative
        # execution scope remain unqualified.
        return ("$" in path and literal_expansions.get(path, False) and is_abs(path)
                and not any(char in path for char in "*?[]{}"))

    def prep(paths, c_cwd, c_old, expand=True, operation="", scope="file", copy_sources=None,
             reads_stdin=False, reads_only=False):
        """(path resolved where it runs, unresolvable?, the token as written). A whole
        array reference -- a for-loop variable, `"${A[@]}"` -- yields one entry PER
        element (resolve_all).

        `expand=False` for REDIRECT / output-flag targets: an operand naming an array
        (for-loop) variable stays UNRESOLVED there, as it was before loops were modelled.
        MEASURED in this lane's history replay: expanding them turned 37 historical
        commands from allow into a DENY on the /tmp and durable-record hygiene rules
        (`for g in ...; do ... > /tmp/g_$g.txt`), which is friction on rules the
        pre-arc note never concerned -- so it was scoped out, not shipped.

        `c_old` is the segment's directory WITHOUT the session seed (lane F); see below.

        `reads_only=True` for operands that feed READ checks only (search_files): their links are still
        resolved within the budget, but past it they never set links_unresolved (link_resolved `refuse`).
        4.1.1 review (link budget round 2), MEASURED at a70dcedd: `grep -rn foo` over files in 65 folders
        was refused."""
        out = []
        for p in paths:
            literal = literal_dollar_path(p)
            # expand=False: resolved exactly as before this lane -- loop words and
            # `$(...)` values are not substituted, so such an operand stays unresolved.
            # C6 (v4.0 release review): EVERY value here too. One resolve() judged
            # `> "${NOPE:+x}<reference>/libraries/wares.xml"` as the alternate only -- ADVISED
            # where the plain path denies (broken since FX-G5 47d06cd; 0801a1a fixed the
            # other rules only). resolve_variants' first value is the plain resolve().
            rs = [p] if literal else (resolve_all(p, assigns) if expand else resolve_variants(p, plain_assigns))
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
                r = r if literal else subst_root_var(r, roots)
                unres = not literal and has_unresolved(r)
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
                qualifies_as_modlist = False
                if simple_modlist_write and operation == 'write' and scope != 'tree' and not unres:
                    placed = physical_join('' if execution_context else physical_cwd, r,
                                           posix_bash=POSIX_HOST and cur_dialect[0] != WINDOWS_DIALECT)
                    source_files = None
                    # 4.1.1: `tee` copies its STDIN, as `echo x > <file>` does; it has no source
                    # operand to check, and an empty source list disqualified every tee.
                    if scope == 'copy-destination' and not reads_stdin:
                        source_files = []
                        for source in copy_sources or []:
                            for resolved in resolve_all(source, assigns):
                                resolved = subst_root_var(resolved, roots)
                                if has_unresolved(resolved) or re.search(r'[*?\[\]{}]', resolved):
                                    source_files.append('')
                                else:
                                    source_files.append(physical_join(physical_cwd, resolved,
                                        posix_bash=POSIX_HOST and cur_dialect[0] != WINDOWS_DIALECT))
                    if profile_modlist_write(out[-1][0], placed, roots, cur_dialect[0], source_files):
                        qualifies_as_modlist = True
                if operation == 'write':
                    # Same spelling can occur as both a copy and a redirect.
                    # A nonqualifying occurrence always dominates a qualifying one.
                    (modlist_writes if qualifies_as_modlist else non_modlist_writes).add(out[-1])
                if evidence is not None and operation:
                    # A cd prefix may fail and execution may continue. The existing parser
                    # does not prove control-flow dominance, so native context checks
                    # never use it to invent the omitted tool execution directory.
                    posix_bash = POSIX_HOST and cur_dialect[0] != WINDOWS_DIALECT
                    physical = physical_join("" if execution_context else physical_cwd, r,
                                             posix_bash=posix_bash)
                    row = {"operation": operation, "path": physical, "dialect": cur_dialect[0],
                                     "raw": r, "unresolved": unres or not physical
                                     or (not literal and has_unresolved(physical))
                                     or bool(_DEBRIS.search(r)) or bool(_DEBRIS.search(physical))
                                     # C1: an unquoted brace-expanded word names SEVERAL paths
                                     # (or none), never the one literal path written here.
                                     or p in _BRACE_WORDS or r in _BRACE_WORDS,
                                     "scope": scope}
                    if copy_sources is not None:
                        sources = []
                        for source in copy_sources:
                            source_literal = literal_dollar_path(source)
                            for resolved in ([source] if source_literal else resolve_all(source, assigns)):
                                resolved = resolved if source_literal else subst_root_var(resolved, roots)
                                placed = physical_join(physical_cwd, resolved, posix_bash=posix_bash)
                                sources.append({"path": placed, "unresolved": not placed
                                                or (not source_literal and (has_unresolved(resolved)
                                                                            or has_unresolved(placed)))
                                                or bool(_DEBRIS.search(resolved))
                                                or source in _BRACE_WORDS or resolved in _BRACE_WORDS})
                        row["copy_sources"] = sources
                    evidence.append(row)
                # 4.1.1 final round (F360): a link on the path (a junction into reference/) is judged
                # where it lands as well; every rule ORs over these entries, so the stricter stands.
                if not unres:
                    real = link_resolved(out[-1][0], literal=literal, refuse=not reads_only)
                    if real:
                        out.append((real, False, r))
                else:
                    # ...and an UNRESOLVED operand's literal head (link budget round 2): `<junction>/$X/f`
                    # and `'<junction>/a${b}/f'` (a quoted brace is not literal_dollar_path) read their
                    # lexical text only, where `<reference>/$X/f` is caught by the root in its text. The
                    # head's landing place plus the rest as written is judged the same way (unresolved).
                    real = link_head_resolved(r, refuse=not reads_only)
                    if real:
                        out.append((real, True, real))
                    # ...and through a link the unresolved component itself may name (`<work>/refj$X/f`).
                    for real in link_entries_matched(r, refuse=not reads_only):
                        out.append((real, True, real))
        return out

    cur_dialect = ["bash"]                # the dialect of the segment prep() is reading
    rm_t, copy_t, redir_t, mv_src = [], [], [], []
    sed_t, out_t, search_files = [], [], []
    gitwipe_t, gitdiscard_t, gitwipe_pairs = [], [], []
    scoped_rm_t, mod_t, subst_rm_t = [], [], []
    # For writes_game_archive only: moved rsync sources, folders receiving an archive-named
    # copy, what a listing feeds `xargs rm`, and (root operand, segment, mode) of each find that
    # deletes ("delete") or prints to an `xargs rm` ("print").
    archive_moved_t, archive_into_t, archive_listed_t, archive_finds = [], [], [], []
    archive_listed_rec_t = []           # ...and what a listing feeds a RECURSIVE `xargs rm` (F2)
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
    for (s, c_cwd), prev, c_old, physical_cwd, cur_dialect[0], is_for_, exact_ in zip(
            seg_cwd, seg_prev, unseeded, physical_dirs, seg_dialect, seg_for, seg_exact):
        _check_deadline()
        literal_expansions = literal_words(s, exact_)
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
        mod_t += prep(modify_targets(s) + decompress_outputs(s), c_cwd, c_old, operation="write")
        # A FILTERED find-delete removes entries INSIDE its tree: it feeds every in-tree
        # delete rule, and never the whole-install hard block (see find_scoped_deletes).
        scoped_rm_t += prep(find_scoped_deletes(s), c_cwd, c_old, operation="delete", scope="filtered-tree")
        archive_moved_t += prep(rsync_moved_sources(s), c_cwd, c_old)
        listed_ = prep(xargs_listed_operands(s, prev), c_cwd, c_old)
        archive_listed_t += listed_
        archive_listed_t += prep(xargs_replaced_folders(s, prev), c_cwd, c_old)
        xargs_rec = bool(listed_ or verb(prev or "") == "find") and deletes_recursively(tokens_of(s))
        if xargs_rec:
            archive_listed_rec_t += listed_
        archive_finds += [(op, s, "delete", True)
                          for op in prep(_find_delete(s, default_root=True)[0], c_cwd, c_old)]
        archive_finds += [(op, s, "delete", True)
                          for op in prep(find_entry_write_roots(s), c_cwd, c_old)]
        fd_find = _fd_as_find(s) if verb(s) in ("fd", "fdfind") else None
        if fd_find:
            archive_finds += [(op, fd_find, "delete", True)
                              for op in prep(_find_delete(fd_find)[0] + find_entry_write_roots(fd_find), c_cwd, c_old)]
        x_effect = xargs_entry_effect(s, prev)
        if x_effect:
            x_roots = _find_roots(prev) or ["."]
            archive_finds += [(op, prev, x_effect, False) for op in prep(x_roots, c_cwd, c_old)]
        if xargs_feed(s, prev) and verb(prev) == "find" and xargs_passes_item(s):
            archive_finds += [(op, prev, "print", xargs_rec)
                              for op in prep(_find_roots(prev) or ["."], c_cwd, c_old)]
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
        mv_src += prep(move_sources(s) + replaced_sources(s), c_cwd, c_old, operation="move")
        dests_ = prep(copy_dests(s), c_cwd, c_old, operation="write", scope="copy-destination",
                      copy_sources=exact_copy_sources(s), reads_stdin=_bare_command(s, {"tee"}))
        copy_t += dests_
        if copy_names_archive(s):
            archive_into_t += dests_ + prep(robo_copy, c_cwd, c_old)
        transcript_copy = transcript_copy or _copy_lands_a_transcript(s, dests_, assigns)
        transcript_inline = transcript_inline or _inline_program_writes_transcript(s, c_cwd, assigns)
        for tgt_, at_, kind_ in ([] if is_for_ else _segment_links(s, assigns)):     # F331
            j_ = _judge_link(tgt_, at_, kind_, c_cwd, assigns, roots)
            link_protected = link_protected or j_ == "protected"
            link_unresolved = link_unresolved or j_ == "unresolved"
        sed_t +=prep(sed_in_place_targets(s), c_cwd, c_old, operation="write")
        out_t += prep(output_targets(s), c_cwd, c_old, False, operation="write")
        search_files += prep(search_paths(s, require_recursive=False), c_cwd, c_old, reads_only=True)
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
    # A cmdlet the guard does not model may re-parse its argument (Invoke-Expression inside a
    # module function), so no `$` in its operands is literal: the old reading.
    literal_expansions = {}
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
        # (literal_expansions stays empty: a program the guard cannot name may re-parse anything.)
        if not _dynamic_program(s):
            continue
        if (not has_unresolved(subst_root_var(resolve(_verb_token(s), assigns), roots))
                and not _glob_program(s, _verb_token(s))):
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
    # A separately configured extensions root counts as well (4.1.0 G-M3). All of it is asked here, as
    # the deny for all of the game's extensions/ cannot reach a folder outside the game.
    _other_ext = norm(roots.get("extensions") or "").rstrip("/") + "/"
    if _other_ext == "/" or _other_ext == _ext:
        _other_ext = ""

    def _deployed_mods(ops, filtered=False):
        # prep() already substituted a root variable ($X4_GAME, F154) and, for an unresolved
        # operand, `path` IS its resolved text -- so an unknown mod name stays as written.
        names = []
        for path, _unres, _raw in ops:
            n = norm(path)
            for root in (_ext, _other_ext):
                if not root:
                    continue
                if n.startswith(root):
                    seg = n[len(root):].split("/")[0]
                    if seg == "${xargs_item}":
                        # `find <extensions> ... | xargs rm`: each found entry, any mod
                        names.append("<any mod the find matches>")
                    elif seg:
                        names.append(seg)
                elif filtered and n == root[:-1]:
                    # a FILTERED find over extensions/ itself reaches into any mod it matches
                    names.append("<any mod the find matches>")
                elif root == _other_ext and n == root[:-1]:
                    names.append("<every mod in the extensions folder>")
        return names

    _mods = list(dict.fromkeys(_deployed_mods(rm_t + mv_src + subst_rm_t)
                               + _deployed_mods(scoped_rm_t, filtered=True)))
    # An official DLC is not a deployed mod: removing one of its files is denied with the archives
    # (user ruling 2026-11-05, 4.1.1 review round 2 F8), so it never reaches the mod confirmation.
    _dlc_named = [m for m in _mods if m.startswith("ego_dlc_")]
    _mods = [m for m in _mods if not m.startswith("ego_dlc_")]
    _mod_list = ", ".join(_mods[:10]) + (f" and {len(_mods) - 10} more" if len(_mods) > 10 else "")

    # A shell write, move or delete of a base-game or official DLC archive (see _is_game_archive).
    def _spellings(ops):
        # Unquoted braces expand as glob_covers reads them; `p or raw` as rm_named_game does.
        for path, _u, raw in ops:
            n = norm(path or raw)
            yield from (_brace_expand(n) if norm(raw) in brace_patterns else [n])

    def _games(n):
        # The configured game root, AND the install's folder name in the path, as rm_named_game
        # reads it: a second install is an install too (4.1.1 review round 2, F7).
        return list(dict.fromkeys(([_g] if _g else []) + named_game_roots(n)))

    def _hits(ops, test):
        return any(test(n, g) for n in _spellings(ops) for g in _games(n))

    def _find_games(n):
        # ...and, for a find, a Steam library's common/ folder: the install's usual parent, so it may
        # hold one (4.1.1 review round 3, C). Its archives and DLC folders, as for any root above
        # extensions/ (see find_can_reach_archive).
        out = _games(n)
        lib = n.rstrip("/")
        return out + ([lib + "/x4 foundations"] if lib.endswith("/steamapps/common") else [])

    _archive_written = copy_t + redir_t_all + sed_t + out_t + mod_t
    _archive_removed = rm_t + mv_src + archive_moved_t
    writes_game_archive = (
        _hits(_archive_written, _is_game_archive)
        or _hits(_archive_removed, _removes_game_archive)
        or _hits(archive_moved_t, _is_archive_home)
        or _hits(archive_into_t, _is_archive_home)
        or _hits(archive_listed_t, lambda n, g: _is_game_archive(n, g) or _is_archive_home(n, g))
        or _hits(archive_listed_rec_t, _holds_a_dlc)
        or bool(_dlc_named)
        or any(find_can_reach_archive(seg_, n, op[2], g, mode_, rec_)
               for op, seg_, mode_, rec_ in archive_finds for n in _spellings([op])
               for g in dict.fromkeys(_find_games(n))))
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
        # WHY, in fixed words, for the save bridge's refusal. Bash rules read only the bool above.
        "untranslated_category": untranslated_category(_UNTRANSLATED) if _UNTRANSLATED else "",
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
        "writes_game_archive": writes_game_archive,
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
        # Routine mod-list writes do not need another approval. Keep the full
        # writes_profile fact above truthful; mixed config/save writes and every
        # destructive target retain the existing confirmation policy.
        "writes_profile_other": hit([
            op for op in writes_any
            if op not in modlist_writes or op in non_modlist_writes
        ], "profile") or hit(rm_t + scoped_rm_t, "profile", conservative=True),
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
        # The optional save locks, in either direction, are the user's decision (F9, 4.1.0 review).
        "save_lock_decision": any(_save_lock_decision(sg) for sg, _c in seg_cwd),
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
    # 4.1.1 review (link budget): an operand whose links the per-call budget left unresolved is not "no
    # link" -- the hook refuses it (see link_resolved). Captured after every field, like the flag above.
    result["links_unresolved"] = _LINKS_UNRESOLVED[0]
    result["ssh_host_unplaced"] = _ssh_unplaced_destructive(result, roots, protected_save_folders)
    return result


def _ssh_unplaced_destructive(outer: dict, roots: dict, protected_save_folders=()) -> bool:
    """Would a remote command of an `ssh <host>` the guard cannot place be destructive, had it run on
    THIS machine (4.1.1 review round 6, C)? Each is judged by the whole rule set on its own, last, so
    nothing it sets can reach this command's verdict -- the answer is only an advisory, because the
    host is most likely another machine, whose paths are not this one's protected folders. A remote
    command spliced into the command around it counts only for a fact `outer` (this command's own
    facts) does not already have: the rest of that text is this command, already judged."""
    distinct = list(dict.fromkeys(_SSH_UNPLACED))
    if len(distinct) > _MAX_SSH_UNPLACED and not _SSH_NESTED[0]:
        return True                      # more remote commands than are judged: advise (round 7, 1)
    remote = distinct[:_MAX_SSH_UNPLACED]
    spliced = [t for t in dict.fromkeys(_SSH_UNPLACED_SPLICED) if t not in remote][:_MAX_SSH_UNPLACED]
    if not remote or _SSH_NESTED[0] >= _MAX_SSH_DEPTH:
        return False
    keys = _SSH_DESTRUCTIVE + ("ssh_host_unplaced",)
    was = _DEADLINE[0]
    soon = _clock() + _SSH_ADVISORY_S
    _DEADLINE[0] = soon if was is None else min(was, soon)
    _SSH_NESTED[0] += 1
    try:
        for text, whole in [(t, True) for t in remote] + [(t, False) for t in spliced]:
            if text not in _SSH_MEMO:
                if _SSH_JUDGED[0] >= _MAX_SSH_JUDGED:
                    return True              # the budget is spent with texts unjudged: advise (round 7, 1)
                _SSH_JUDGED[0] += 1
                # The remote text's OWN link verdict (the outer one is captured already): a remote text
                # over the link budget is advised (links_unresolved is in _SSH_DESTRUCTIVE).
                _LINKS_UNRESOLVED[0] = False
                sub = facts({"tool_name": "Bash", "tool_input": {"command": text}}, roots,
                            protected_save_folders=protected_save_folders)
                _SSH_MEMO[text] = {k for k in keys if sub.get(k)}
            if any(whole or not outer.get(k) for k in _SSH_MEMO[text]):
                return True
        return False
    except Exception:                    # noqa: BLE001 -- unjudged (a timeout too) is advised, never clean
        return True
    finally:
        _SSH_NESTED[0] -= 1
        _DEADLINE[0] = was


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


def ipc(payload: dict, roots: dict, *, evidence: list | None = None, protected_save_folders=(),
        deadline: float | None = None):
    """(exit code, stdout bytes, facts) exactly as main() reports them to protect-bash.sh.

    K5 (v4.0 release review): the save bridge computes the facts ONCE with this and hands the
    same bytes to the Bash backend (`--facts-stdin`), instead of a second process re-parsing
    the command and re-running the PowerShell translation -- that doubled the hook's latency.
    """
    f = facts(payload, roots, evidence=evidence, protected_save_folders=protected_save_folders, deadline=deadline)
    if f.get("analysis_timeout"):
        # rc 6: the analysis deadline passed (round 7, 1). Nothing was fully analysed: the caller DENIES.
        return 6, str(f["analysis_timeout"]).encode("utf-8", "replace"), f
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
    g.pop("untranslated_category", None)   # likewise
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
