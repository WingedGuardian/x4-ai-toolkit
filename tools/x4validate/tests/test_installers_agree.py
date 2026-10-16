"""The two installers must copy the SAME set of items.

Nothing pinned them equal, and the drift was real: `mods` -- the game extension this
release ships -- was missing from BOTH lists, and the README's instruction to "copy that
folder into {game}/extensions/" pointed at a directory neither installer created. A
divergence here is invisible until a user on the other platform reports a missing file.

Both lists are PARSED from their files rather than restated here, so this test cannot
become a third copy of the same list, drifting independently of the two it guards.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
SH = ROOT / "install.sh"
PS1 = ROOT / "install.ps1"

#: A lone backslash is a LINE CONTINUATION, not an item. Dropping it by name beats
#: stripping "backslash then newline": the first version of this parser did that, still
#: yielded a stray token, and reported a DIVERGENCE between the two installers when the
#: lists were in fact identical. A parser bug that looks like a finding is the worst kind.
NOT_AN_ITEM = {chr(92), ""}


def sh_items() -> list[str]:
    """The `X4_COPY_ITEMS=` list in install.sh.

    It used to be parsed out of `for item in ... ; do`. The list is now named ONCE
    and consumed by three callers -- the copy, the dry-run listing, and the
    locked-target precheck -- so the definition is what to read. Parsing a loop
    header would now find `$X4_COPY_ITEMS` and report an empty set as agreement.
    """
    text = SH.read_text(encoding="utf-8")
    m = re.search(r'^X4_COPY_ITEMS="([^"]*)"', text, re.M)
    assert m, "could not find X4_COPY_ITEMS in install.sh"
    return sorted(w for w in m.group(1).split() if w not in NOT_AN_ITEM)


def ps1_items() -> list[str]:
    """The `$X4CopyItems = @(...)` list in install.ps1."""
    text = PS1.read_text(encoding="utf-8")
    m = re.search(r"\$X4CopyItems\s*=\s*@\((.*?)\)", text, re.S)
    assert m, "could not find $X4CopyItems in install.ps1"
    return sorted(w for w in re.findall(r"'([^']+)'", m.group(1)) if w not in NOT_AN_ITEM)


def test_both_installers_copy_the_same_items():
    sh, ps = sh_items(), ps1_items()
    # A parser returning nothing -- or implausibly little -- must not be able to report
    # "the lists agree". Both installers ship well over a dozen items.
    assert len(sh) >= 12, f"install.sh parse looks broken: {sh}"
    assert len(ps) >= 12, f"install.ps1 parse looks broken: {ps}"
    only_sh = sorted(set(sh) - set(ps))
    only_ps = sorted(set(ps) - set(sh))
    assert not only_sh and not only_ps, (
        "the installers copy different item sets.\n"
        f"  only install.sh : {only_sh}\n"
        f"  only install.ps1: {only_ps}")


def test_the_shipped_game_extension_is_in_both():
    """The specific regression this file exists for: `mods/` carries the game extension
    x4live needs, and the README tells users to copy it into {game}/extensions/."""
    assert "mods" in sh_items(), "install.sh does not copy mods/"
    assert "mods" in ps1_items(), "install.ps1 does not copy mods/"


# --- the round-4 installer fixes, guarded in BOTH files -----------------------------
#
# Five of round 3's own fixes shipped guarded by nothing. These are source-level
# assertions rather than a full install, because the E2E harnesses live outside the
# repo -- but each names the exact construct whose absence was the defect, so
# reverting any one of them turns a named test red.

def test_neither_installer_compares_source_and_destination_as_STRINGS():
    """`$SRC` is MSYS-style under Git Bash; `--toolkit` is whatever the user pasted,
    and every documented path is Windows-style. Comparing the two spellings as text
    said COPY for a destination that IS the source, `cp -r` reported "are the same
    file", and `set -e` killed the script before the FAILED accounting existed -- no
    banner, no failed: line, no config. MEASURED in a sandbox: rc 1.
    """
    sh = SH.read_text(encoding="utf-8")
    ps = PS1.read_text(encoding="utf-8")
    assert '[ "$SRC" != "$TOOLKIT" ]' not in sh, (
        "install.sh is back to a string comparison of source vs destination")
    assert "($SRC -ne $Toolkit)" not in ps, (
        "install.ps1 is back to a string comparison of source vs destination")
    assert "same_dir()" in sh, "install.sh lost its canonical same-directory test"
    assert "function Test-SameDir" in ps, (
        "install.ps1 lost its canonical same-directory test")


def test_every_powershell_WRITER_consults_the_dry_run_flag():
    """`$DryRun` was consulted in exactly ONE place -- inside Show-Target, which the
    global arm never reaches. MEASURED in a sandbox: `-Method global -DryRun`
    overwrote x4-paths.env and printed "=== install complete (global) ===".

    The guard belongs in the WRITERS, as install.sh does it, so an arm added later
    cannot write without passing through one of them.
    """
    ps = PS1.read_text(encoding="utf-8")
    assert "function Refuse-IfDryRun" in ps, "install.ps1 lost its dry-run gate"
    assert ps.count("Refuse-IfDryRun '") >= 3, (
        f"only {ps.count(chr(82) + 'efuse-IfDryRun ' + chr(39))} writer(s) call the "
        "dry-run gate; Write-PathsEnv, Copy-Toolkit and Install-Global all must")


def test_both_installers_back_up_the_path_config_before_rewriting_it():
    """install.sh moved this backup INTO write_paths_env so a caller could not be
    added without one; PowerShell's Write-PathsEnv, which all three methods call,
    never got it. Fourth 'fixed in bash, absent in PowerShell' of the release."""
    sh = SH.read_text(encoding="utf-8")
    ps = PS1.read_text(encoding="utf-8")
    # THE BACKUP, not the reassurance. This asserted a literal that occurs exactly
    # once per file -- inside the echo/Write-Host that ANNOUNCES the backup, never
    # in the code that takes one. Mutants deleting the backup and keeping the
    # message stayed GREEN, which is precisely the failure the code's own comment
    # names: "a backup that silently did not happen is worse than none, because
    # the message above would have said it did".
    assert '_backup="$(_i_backup_file "$f")"' in sh, (
        "install.sh no longer COPIES the path config to a .bak (a message saying "
        "it did is not the same thing)")
    assert "$backup = New-ConfigBackup $f" in ps, (
        "install.ps1 no longer COPIES the path config to a .bak")


def test_neither_installer_copies_the_tree_WHOLESALE():
    """The virtualenv must be excluded at COPY time, not deleted on arrival.

    It used to be copied in full -- 1,407 files, 38 MB -- and pruned afterwards, while
    the CHANGELOG said "A virtualenv no longer travels." The waste was the small half:
    under `set -e` a failure among those copies aborts BEFORE both the prune and the
    config restore, so the most expensive thing copied was also the likeliest thing to
    break the install, and nothing wanted it copied.

    An end-state check cannot tell "never copied" from "copied then removed" -- the
    existing "the SOURCE venv did not travel" case passed before the fix too. So this
    pins the CONSTRUCT, and the stubbed-copy probes in the scratchpad pin the
    behaviour: 0 invocations naming .venv on either platform.
    """
    sh = SH.read_text(encoding="utf-8")
    ps = PS1.read_text(encoding="utf-8")
    assert 'cp -r "$SRC/$item"' not in sh, (
        "install.sh copies each item wholesale again; the prune list is not consulted "
        "until after the copy")
    assert "copy_item()" in sh, "install.sh lost its excluding copy walk"
    assert "Copy-Item -Recurse -Force -LiteralPath $s -Destination $dest" not in ps, (
        "install.ps1 copies each item wholesale again")
    assert "function Copy-Excluding" in ps, "install.ps1 lost its excluding copy walk"


def test_the_prune_list_is_named_ONCE_in_each_installer():
    """Both walks and both prunes read the same list, so an entry cannot be
    half-applied. Two hand-written copies is how the two passes drifted in the first
    place -- one of them was not running at all."""
    sh = SH.read_text(encoding="utf-8")
    ps = PS1.read_text(encoding="utf-8")
    assert sh.count("X4_COPY_PRUNE=") == 1, "install.sh defines the prune list twice"
    assert ps.count("$X4CopyPrune = @(") == 1, (
        "install.ps1 defines the prune list twice")


# --- the round-11 installer fixes (Track 2 audit), guarded in BOTH files -------------

def _ps_single_quoted(s: str) -> list[str]:
    """Every PowerShell single-quoted literal in `s`, with '' decoded to a quote.

    Hand-scanned rather than regexed: `''` is BOTH an empty string and the escape for
    one quote, and a regex that gets that wrong reports a defect where there is none --
    which this file's own parser note already warns about.
    """
    out, i, n = [], 0, len(s)
    while i < n:
        if s[i] != "'":
            i += 1
            continue
        i += 1
        buf = []
        while i < n:
            if s[i] == "'":
                if i + 1 < n and s[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                i += 1
                break
            buf.append(s[i])
            i += 1
        out.append("".join(buf))
    return out


def _trim_call_arguments(text: str) -> list[tuple[int, str]]:
    """(line number, argument text) for every .Trim/.TrimStart/.TrimEnd call."""
    calls = []
    for m in re.finditer(r"\.Trim(?:Start|End)?\(", text):
        depth, i = 0, m.end() - 1
        while i < len(text):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        calls.append((text.count("\n", 0, m.start()) + 1, text[m.end():i]))
    return calls


def test_no_powershell_char_argument_is_an_EMPTY_string():
    """`.TrimStart('', '/')` shipped in v3.0.0 and made `-Method global` throw on EVERY
    run, on Windows PowerShell 5.1 and pwsh 7 alike: an empty string is not convertible
    to [char], and $ErrorActionPreference='Stop' makes that fatal.

    It threw AFTER x4-paths.env had been rewritten and the first x4-* skill had been
    force-copied over the user's own, so it was a partial write, not a clean failure.

    The cause was a lone backslash collapsing to nothing at authoring time, which is why
    this pins the CLASS -- any Trim argument that is not exactly one character -- rather
    than the one line. Test-SameDir already used [char]92/[char]47 to dodge the same
    hazard; the empty literal is what that habit exists to prevent.
    """
    text = PS1.read_text(encoding="utf-8")
    calls = _trim_call_arguments(text)
    # A scanner that finds nothing must not be able to report "all arguments are fine".
    assert len(calls) >= 3, f"the Trim-call scanner looks broken: found {calls}"
    bad = []
    for lineno, args in calls:
        for lit in _ps_single_quoted(args):
            if len(lit) != 1:
                bad.append(f"install.ps1:{lineno}  .Trim*(...{args}...)  literal {lit!r} "
                           f"is {len(lit)} characters, not 1")
    assert not bad, (
        "a Trim/TrimStart/TrimEnd argument is not a single character, so it cannot "
        "convert to [char] and the call throws at runtime:\n  " + "\n  ".join(bad))


def test_both_installers_GATE_the_global_destination():
    r"""Fifth 'fixed in bash, absent in PowerShell' of this release.

    -OverExisting is consulted in exactly one place on the PowerShell side --
    Assert-Direction, on the copy-to-destination path -- which the global arm never
    reaches. So `-Method global` force-overwrote a user's own ~/.claude\skills\x4-*
    with no prompt and no backup of the skills, and rewrote their global settings.json.
    MEASURED in a sandbox: an edited x4-balance\SKILL.md was replaced by the shipped
    one; only the settings.json half was ever backed up.

    install.sh has refused this since its own global arm was gated. install.ps1 now does
    the same, and this pins BOTH so neither can lose it alone.
    """
    sh = SH.read_text(encoding="utf-8")
    ps = PS1.read_text(encoding="utf-8")
    # ⚠ THIS ASSERTION USED TO PIN THE DEFECT. It required the literal
    # `ls "$_hc/skills"/x4-*` -- the DESTINATION glob that named a user's own
    # `x4-mycustom/` as a file the install "would REPLACE". Correcting the gate to
    # enumerate from the toolkit therefore turned a NAMED parity test red, which
    # reads as a regression to whoever hits it. Inverted in the same commit as the
    # fix, and stated here so it cannot be quietly reverted to make a red go away.
    #
    # A substring is the wrong instrument for this either way (BLIND-SPOTS F109):
    # what matters is that BOTH gates enumerate the SOURCE, which
    # test_install_over_existing.py now asserts behaviourally on both installers.
    assert 'ls "$_hc/skills"/x4-*' not in sh, (
        "install.sh's global gate is enumerating the DESTINATION again; it must list "
        "only skills this toolkit ships, or a user's own x4-* is named as at risk")
    assert '"$TOOLKIT/.claude/skills/"x4-*' in sh, (
        "install.sh's global arm no longer checks for pre-existing x4-* skills")
    assert "function Assert-GlobalOverExisting" in ps, (
        "install.ps1 lost its global over-existing gate")
    # The FUNCTION BODY, not a fixed character window: the explanatory comment is long
    # enough that a magic slice cut the assertion off and reported a defect that was not
    # there -- the parser-bug-as-finding this file already warns about.
    body = ps.split("function Assert-GlobalOverExisting", 1)[1]
    body = body.split(chr(10) + "function ", 1)[0]   # no backslash escapes here
    assert "$OverExisting" in body, (
        "install.ps1's global gate no longer consults -OverExisting, so it either "
        "always refuses or never does")
    assert "exit 2" in body, (
        "install.ps1's global gate no longer refuses with rc 2, the toolkit-wide "
        "refusal code install.sh uses for the same gate")


def test_the_global_gate_runs_BEFORE_the_global_arm_writes_anything():
    """Position is the whole point. Refusing from inside Install-Global would leave
    x4-paths.env already rewritten -- a partial write, which is the shape of the bug
    rather than a fix. install.sh gates ahead of its own write_paths_env for the same
    reason. MEASURED in a sandbox: with the gate in place, a refused run creates no
    x4-paths.env at all.
    """
    ps = PS1.read_text(encoding="utf-8")
    m = re.search(r"'global'\s*\{(.*?)\n  \}", ps, re.S)
    assert m, "could not find the 'global' dispatch arm in install.ps1"
    arm = m.group(1)
    assert "Assert-GlobalOverExisting" in arm, (
        "the global arm does not call the over-existing gate")
    assert "Write-PathsEnv" in arm, "the global arm parse looks wrong: no Write-PathsEnv"
    assert arm.index("Assert-GlobalOverExisting") < arm.index("Write-PathsEnv"), (
        "the global gate runs AFTER Write-PathsEnv, so a refusal still leaves the "
        "user's path config rewritten")


def test_the_global_claude_dir_is_resolved_in_ONE_place():
    r"""Two copies of `CLAUDE_CONFIG_DIR else USERPROFILE\.claude` is how the gate and
    the installer would come to disagree about which directory they are talking about.
    This file's history is a list of things that drifted for exactly that reason."""
    ps = PS1.read_text(encoding="utf-8")
    assert ps.count("$env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR }") == 1, (
        "install.ps1 resolves the global Claude config directory in more than one "
        "place; use Get-GlobalClaudeDir")


# --- per-agent targets (Plan 2 lane C; audit F8) -------------------------------------- #
#
# Each agent's files are named ONCE per installer, in an agent set, and both installers
# must hold the same sets. Parsed from both files, never restated here.

def sh_agent_items() -> dict[str, list[str]]:
    text = SH.read_text(encoding="utf-8")
    got = {m.group(1): sorted(m.group(2).split())
           for m in re.finditer(r'^X4_AGENT_ITEMS_(\w+)="([^"]*)"', text, re.M)}
    assert got, "no X4_AGENT_ITEMS_* in install.sh"
    return got


def ps1_agent_items() -> dict[str, list[str]]:
    text = PS1.read_text(encoding="utf-8")
    m = re.search(r"^\$X4AgentItems\s*=\s*@\{(.*?)^\}", text, re.S | re.M)
    assert m, "no $X4AgentItems in install.ps1"
    got = {k: sorted(re.findall(r"'([^']+)'", v))
           for k, v in re.findall(r"(\w+)\s*=\s*@\(([^)]*)\)", m.group(1))}
    assert got, "the $X4AgentItems block parsed to nothing"
    return got


def test_both_installers_define_the_SAME_agent_sets():
    sh, ps = sh_agent_items(), ps1_agent_items()
    assert set(sh) == set(ps) >= {"claude", "codex", "generic"}, (sorted(sh), sorted(ps))
    for k in sh:
        assert sh[k] == ps[k], f"agent {k}: install.sh {sh[k]} vs install.ps1 {ps[k]}"


def test_F8_codex_and_generic_ship_AGENTS_md_and_claude_ships_CLAUDE_md():
    a = sh_agent_items()
    assert "AGENTS.md" in a["codex"] and "AGENTS.md" in a["generic"]
    assert ".agents" in a["codex"] and ".agents" in a["generic"]
    assert ".codex" in a["codex"] and ".codex" not in a["generic"]
    assert "CLAUDE.md" in a["claude"] and ".claude" in a["claude"]
    assert "CLAUDE.md" not in sh_items() and ".claude" not in sh_items(), (
        "agent-specific items must live in exactly one place: the agent set")


def test_the_neutral_source_tree_is_in_NO_installed_set():
    """User decision #9 (2026-10-02): an installed toolkit is runtime-only."""
    every = set(sh_items()).union(*sh_agent_items().values())
    assert "agent" not in every
    every_ps = set(ps1_items()).union(*ps1_agent_items().values())
    assert "agent" not in every_ps


def test_no_item_is_both_common_and_agent_specific():
    common = set(sh_items())
    for k, v in sh_agent_items().items():
        assert not common & set(v), f"{k}: {sorted(common & set(v))} is in both lists"


def test_both_installers_offer_opencode_as_best_effort():
    """Plan 3 lane L REPLACES the old M8 refusal: --agent opencode installs a best-effort
    OpenCode target, and both installers say so."""
    for p in (SH, PS1):
        text = p.read_text(encoding="utf-8")
        assert "opencode" in text and "BEST EFFORT" in text and "desktop app" in text, p.name
        assert "M8" not in text, p.name


def _sh_list(name: str) -> list[str]:
    m = re.search(r'^%s="([^"]*)"' % re.escape(name), SH.read_text(encoding="utf-8"), re.M)
    assert m, f'no {name}="..." in install.sh'
    return m.group(1).split()


def _ps1_list(name: str) -> list[str]:
    m = re.search(r"^\$%s\s*=\s*@\(([^)]*)\)" % re.escape(name), PS1.read_text(encoding="utf-8"), re.M)
    assert m, f"no ${name} = @(...) in install.ps1"
    return re.findall(r"'([^']+)'", m.group(1))


def test_ALL_includes_opencode_in_both_installers():
    """User decision L-Q1 (2026-10-02): `--agent all` INCLUDES OpenCode (and so does the
    default, which is `all`)."""
    sh, ps = _sh_list("X4_AGENT_NAMES"), _ps1_list("X4AgentNames")
    assert sh == ps, (sh, ps)
    assert set(sh) == {"claude", "codex", "generic", "opencode"}
    assert sorted(sh_agent_items()["opencode"]) == [".opencode", "AGENTS.md"]


def test_both_installers_render_the_token_in_the_SAME_skill_dirs():
    assert _sh_list("X4_TOKEN_DIRS") == _ps1_list("X4TokenDirs") == [".agents", ".opencode"]


def test_the_rendered_opencode_config_never_TRAVELS_from_the_source():
    """`.opencode/opencode.jsonc` names this machine's absolute roots: rendered per install."""
    assert ".opencode/opencode.jsonc" in _sh_list("X4_KEEP_LOCAL")
    keep = re.search(r"\$X4KeepLocal\s*=\s*@\((.*?)\)", PS1.read_text(encoding="utf-8"), re.S)
    assert keep and ".opencode" + chr(92) + "opencode.jsonc" in re.findall(r"'([^']+)'", keep.group(1))


def _sh_assign(name: str) -> str:
    m = re.search(r"^%s='([^']*)'" % re.escape(name), SH.read_text(encoding="utf-8"), re.M)
    assert m, f"no {name}='...' in install.sh"
    return m.group(1)


def _ps1_render_table() -> dict[str, str]:
    text = PS1.read_text(encoding="utf-8")
    m = re.search(r"^\$X4ToolkitRender\s*=\s*@\{(.*?)\}", text, re.S | re.M)
    assert m, "no $X4ToolkitRender in install.ps1"
    return dict(re.findall(r"(\w+)\s*=\s*'([^']*)'", m.group(1)))


def test_both_installers_render_the_skill_token_the_SAME_way_per_OS():
    """User decision #2 (2026-10-02): the generated Codex/generic skills keep the
    `{{TOOLKIT}}` token and the INSTALLER renders it. Codex runs PowerShell on Windows,
    where `$X4_TOOLKIT` expands to EMPTY (MEASURED, lane A) -- so the rendering follows
    the OS, and both installers must hold the same two renderings."""
    sh = {"windows": _sh_assign("X4_TOOLKIT_RENDER_windows"),
          "posix": _sh_assign("X4_TOOLKIT_RENDER_posix")}
    ps = _ps1_render_table()
    assert sh == ps, (sh, ps)
    assert sh == {"windows": "$env:X4_TOOLKIT", "posix": "$X4_TOOLKIT"}


def test_the_installers_render_the_token_the_GENERATOR_writes():
    gen = (ROOT / "tools" / "x4validate" / "scripts" / "gen-agent-trees.py").read_text(encoding="utf-8")
    m = re.search(r'^TOKEN\s*=\s*"([^"]+)"', gen, re.M)
    assert m, "gen-agent-trees.py no longer names its TOKEN"
    assert _sh_assign("X4_TOOLKIT_TOKEN") == m.group(1)
    ps = re.search(r"^\$X4ToolkitToken\s*=\s*'([^']+)'", PS1.read_text(encoding="utf-8"), re.M)
    assert ps and ps.group(1) == m.group(1)


def test_a_rendered_codex_hooks_json_never_TRAVELS_from_the_source():
    """`.codex/hooks.json` holds the absolute root it was rendered for (lane B, C14): it
    is per-machine config, rendered by the installer for ITS destination."""
    assert ".codex/hooks.json" in re.search(
        r'^X4_KEEP_LOCAL="([^"]*)"', SH.read_text(encoding="utf-8"), re.M).group(1).split()
    keep = re.search(r"\$X4KeepLocal\s*=\s*@\((.*?)\)", PS1.read_text(encoding="utf-8"), re.S)
    assert keep and ".codex" + chr(92) + "hooks.json" in re.findall(r"'([^']+)'", keep.group(1))


def test_the_repo_ships_exactly_ONE_AGENTS_md():
    """Codex reads the root AGENTS.md and every nested one into ONE 32,768-byte budget
    (MEASURED, lane A, 2/2), so a second AGENTS.md anywhere in the copy set would eat the
    root file's budget and cut its tail silently."""
    import subprocess
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True)
    if r.returncode != 0:
        import pytest
        pytest.skip("not a git checkout: the tracked set cannot be read")
    names = [p for p in r.stdout.decode("utf-8").split("\0")
             if p and p.rsplit("/", 1)[-1].lower() == "agents.md"]
    assert names == ["AGENTS.md"], names


# --- lane H: --agent auto detection tables -------------------------------------------- #
#
# Both installers walk one PATH with one name/extension list and read one marker table, so
# Git Bash's `command -v` and PowerShell's `Get-Command` (which disagree about which codex
# shim they find, MEASURED) are never consulted. Parsed from both files, never restated.

def _sh_tables(prefix: str) -> dict[str, list[str]]:
    text = SH.read_text(encoding="utf-8")
    return {m.group(1): sorted(m.group(2).split())
            for m in re.finditer(r'^%s(\w+)="([^"]*)"' % re.escape(prefix), text, re.M)}


def _ps1_table(name: str) -> dict[str, list[str]]:
    text = PS1.read_text(encoding="utf-8")
    m = re.search(r"^\$%s\s*=\s*@\{(.*?)^\}" % re.escape(name), text, re.S | re.M)
    assert m, f"no ${name} in install.ps1"
    return {k: sorted(re.findall(r"'([^']+)'", v))
            for k, v in re.findall(r"(\w+)\s*=\s*@\(([^)]*)\)", m.group(1))}


def test_both_installers_detect_agents_with_the_SAME_names_extensions_and_markers():
    sh_detect, ps_detect = _sh_tables("X4_AGENT_DETECT_"), _ps1_table("X4AgentDetect")
    sh_mark, ps_mark = _sh_tables("X4_AGENT_MARK_"), _ps1_table("X4AgentMark")
    assert sh_detect == ps_detect and sh_detect.get("claude") == ["claude"], (sh_detect, ps_detect)
    assert sh_detect.get("codex") == ["codex"], sh_detect
    # markers compared with one separator: install.ps1 may spell a path either way
    norm = lambda t: {k: sorted(x.replace(chr(92), "/") for x in v) for k, v in t.items()}
    assert norm(sh_mark) == norm(ps_mark), (sh_mark, ps_mark)
    # F5: every install makes .claude/, so a bare .claude is NEVER a Claude marker
    assert ".claude" not in sh_mark.get("claude", []), sh_mark
    assert "CLAUDE.md" in sh_mark["claude"] and ".claude/settings.json" in sh_mark["claude"]
    sh_ext = re.search(r'^X4_DETECT_EXTS="([^"]*)"', SH.read_text(encoding="utf-8"), re.M)
    ps_ext = re.search(r"^\$X4DetectExts\s*=\s*@\(([^)]*)\)", PS1.read_text(encoding="utf-8"), re.M)
    assert sh_ext and ps_ext, "an installer lost its detection extension list"
    assert sorted(sh_ext.group(1).split()) == sorted(re.findall(r"'([^']+)'", ps_ext.group(1)))
    # every detectable agent is a real agent name
    names = re.search(r'^X4_AGENT_NAMES="([^"]*)"', SH.read_text(encoding="utf-8"), re.M).group(1).split()
    assert set(sh_detect) | set(sh_mark) <= set(names), (sh_detect, sh_mark, names)


def test_both_installers_set_X4_TOOLKIT_through_the_ONE_userenv_script_with_the_same_flags():
    """Lane H T4: one Windows writer (scripts/x4-userenv.ps1), one opt-out flag per dialect,
    the same test seams. A second mechanism is a second thing to keep equal."""
    sh, ps = SH.read_text(encoding="utf-8"), PS1.read_text(encoding="utf-8")
    assert re.search(r'^X4_USERENV_PS1="scripts/x4-userenv\.ps1"', sh, re.M), "install.sh"
    assert re.search(r"^\$X4UserEnvPs1\s*=\s*'scripts/x4-userenv\.ps1'", ps, re.M), "install.ps1"
    assert (ROOT / "scripts" / "x4-userenv.ps1").is_file()
    assert "--no-env)" in sh and "[switch]$NoEnv" in ps
    for seam in ("X4_INSTALL_ENV_REGKEY", "X4_INSTALL_DETECT_PATH"):
        assert seam in sh or seam in (ROOT / "scripts" / "x4-userenv.ps1").read_text(encoding="utf-8"), seam
    assert "X4_INSTALL_DETECT_PATH" in sh and "X4_INSTALL_DETECT_PATH" in ps
    # neither installer reaches for setx/reg add to WRITE (the manual hint may name setx)
    assert "reg add" not in sh and "reg add" not in ps
    assert not re.search(r"^\s*setx\b", sh, re.M) and not re.search(r"^\s*setx\b", ps, re.M)


def test_both_installers_keep_the_codex_config_local_and_use_the_SAME_doc_cap_bounds():
    sh, ps = SH.read_text(encoding="utf-8"), PS1.read_text(encoding="utf-8")
    keep_sh = re.search(r'^X4_KEEP_LOCAL="([^"]*)"', sh, re.M).group(1).split()
    keep_ps = re.findall(r"'([^']+)'", re.search(r"\$X4KeepLocal\s*=\s*@\((.*?)\)", ps, re.S).group(1))
    assert ".codex/config.toml" in keep_sh
    assert ".codex" + chr(92) + "config.toml" in keep_ps
    b_sh = (re.search(r"^X4_CODEX_DOC_MIN=(\d+)", sh, re.M), re.search(r"^X4_CODEX_DOC_MAX=(\d+)", sh, re.M))
    b_ps = (re.search(r"^\$X4CodexDocMin\s*=\s*(\d+)", ps, re.M), re.search(r"^\$X4CodexDocMax\s*=\s*(\d+)", ps, re.M))
    assert all(b_sh) and all(b_ps), "an installer lost its doc-cap bounds"
    assert [m.group(1) for m in b_sh] == [m.group(1) for m in b_ps] == ["32768", "1048576"]
    assert "--codex-doc-max-bytes)" in sh and "[string]$CodexDocMaxBytes" in ps


# --- Plan 3 lane I: the path config at the toolkit root -------------------------------- #

def _body(text: str, start: str, end_marker: str) -> str:
    i = text.index(start)
    j = text.index(end_marker, i + len(start))
    return text[i:j]


def test_both_installers_migrate_inside_the_writer_AFTER_the_dry_run_gate():
    sh = (ROOT / "install.sh").read_text(encoding="utf-8")
    ps = (ROOT / "install.ps1").read_text(encoding="utf-8")
    sb = _body(sh, "write_paths_env() {", "\n}\n")
    pb = _body(ps, "function Write-PathsEnv($t) {", "\n}\n")
    assert 0 <= sb.index("refuse_if_dry_run") < sb.index("_i_migrate_paths_env"), "install.sh"
    assert 0 <= pb.index("Refuse-IfDryRun") < pb.index("Move-ILegacyPathsEnv"), "install.ps1"


def test_both_installers_keep_the_root_config_local():
    sh = (ROOT / "install.sh").read_text(encoding="utf-8")
    ps = (ROOT / "install.ps1").read_text(encoding="utf-8")
    keep_sh = sh.split('X4_KEEP_LOCAL="', 1)[1].split('"', 1)[0].split()
    copy_sh = sh.split('X4_COPY_ITEMS="', 1)[1].split('"', 1)[0].split()
    keep_ps = ps.split("$X4KeepLocal = @(", 1)[1].split(")", 1)[0]
    copy_ps = ps.split("$X4CopyItems = @(", 1)[1].split(")", 1)[0]
    assert "x4-paths.env" in keep_sh and ".claude/x4-paths.env" in keep_sh, keep_sh
    assert "'x4-paths.env'" in keep_ps and ".claude" + chr(92) + "x4-paths.env" in keep_ps, keep_ps
    assert "x4-paths.env.example" in copy_sh, copy_sh
    assert "'x4-paths.env.example'" in copy_ps, copy_ps


# --- R4-1 (v4.0.0 review): install.ps1 -Help, the same help as install.sh --help ---------

#: install.sh flag -> install.ps1 parameter where the mechanical CamelCase is not the name.
_PS_SPELLING = {"xrcattool": "XRCatTool"}


def _ps_name(flag: str) -> str:
    return _PS_SPELLING.get(flag) or "".join(w[:1].upper() + w[1:] for w in flag.split("-"))


def _run_help(cmd: list, cwd: pathlib.Path):
    import subprocess
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd), timeout=120)


def test_install_ps1_HAS_a_Help_switch_naming_every_install_sh_option(tmp_path):
    """The docs and SETUP_PROMPT named `install.ps1 -Help`, and there was no such parameter:
    'A parameter cannot be found that matches parameter name Help' on 5.1 and 7 (MEASURED by
    the reviewer, review/scratch-R4/help51.txt). The two help texts must name the SAME options,
    each in its own spelling, read from what each installer PRINTS -- never a third list."""
    import importlib.util
    import shutil
    ps = shutil.which("powershell") or shutil.which("pwsh")
    # The repo's Git Bash resolver, never `shutil.which("bash")`: on Windows that is often the
    # WSL stub, which cannot open a C:/ path (FX-B2, reviewer C).
    spec = importlib.util.spec_from_file_location("gitbash_agree", SH.parent / "scripts" / "gitbash.py")
    gb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gb)
    bash = gb.find_bash()
    if not ps or not bash:
        import pytest
        pytest.skip("needs both PowerShell and bash to compare the two help texts")
    rs = _run_help([bash, SH.as_posix(), "--help"], tmp_path)
    rp = _run_help([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS1), "-Help"], tmp_path)
    assert rs.returncode == 0, rs.stderr
    assert rp.returncode == 0, (rp.stdout + rp.stderr)[-800:]
    assert list(tmp_path.iterdir()) == [], "a help run WROTE something"
    sh_flags = set(re.findall(r"^\s+--([a-z][a-z-]*)", rs.stdout, re.M)) - {"help"}
    ps_flags = set(re.findall(r"^\s+-([A-Z][A-Za-z]*)", rp.stdout, re.M)) - {"Help"}
    assert len(sh_flags) >= 14, sorted(sh_flags)            # a parser that found nothing agrees
    assert {_ps_name(f) for f in sh_flags} == ps_flags, (
        "the help texts DIFFER -- only in install.sh: %s; only in install.ps1: %s" % (
            sorted({_ps_name(f) for f in sh_flags} - ps_flags),
            sorted(ps_flags - {_ps_name(f) for f in sh_flags})))
    assert "-Method" in rp.stdout and "--method" in rs.stdout
    # the invocation the help itself names must be one Windows accepts by default
    assert "powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1" in rp.stdout


def test_every_ps1_help_option_is_a_REAL_parameter():
    """The help may not name a parameter param() does not declare (the -Help defect, inverted)."""
    text = PS1.read_text(encoding="utf-8")
    block = text.split("param(", 1)[1].split("\n)", 1)[0]
    declared = set(re.findall(r"\$([A-Z][A-Za-z]*)\s*(?:=|,|$)", block, re.M))
    usage = text.split("function Show-Usage", 1)[1].split("'@", 1)[0]
    named = set(re.findall(r"^\s+-([A-Z][A-Za-z]*)", usage, re.M))
    assert named and named <= declared, sorted(named - declared)


def test_both_installers_PRINT_the_same_refguard_step_and_neither_APPLIES_it():
    """R6-04: Layer 2 is an ACL change -- named, never applied by an installer. This reads the
    SOURCE; the behaviour (a real install leaves the state `absent`) is measured by
    test_install_over_existing.test_an_UNPROTECTED_existing_reference_PRINTS_the_x4refguard_step."""
    sh = SH.read_text(encoding="utf-8")
    ps = PS1.read_text(encoding="utf-8")
    # R2-b (second install red-team 2026-10-04): a HUMAN reads this, so plain `apply`, which
    # shows the folder and a count and ASKS (B3); --yes is only explained, for an agent.
    assert "X4_REFGUARD_STEP_CMD='python scripts/x4refguard.py apply'" in sh
    assert "$X4RefguardStepCmd = 'python scripts/x4refguard.py apply'" in ps
    assert "print_refguard_step \"$TOOLKIT\"" in sh and "Write-RefguardStep $Toolkit" in ps
    for text, name in ((sh, "install.sh"), (ps, "install.ps1")):
        assert "x4refguard.py' 'apply'" not in text and "x4refguard.py apply\"" not in text, name
