<#
  X4 AI Assistant Toolkit installer - Windows (PowerShell).

  Three install methods, all with fully configurable paths (nothing hardcoded):
    in-game   Copy the toolkit INTO your X4 game folder (one workspace).
    separate  Keep the toolkit in its OWN folder, pointed at the game via config.
    global    Install skills/agents into %USERPROFILE%\.claude and write the X4_* paths
              into your global Claude settings, so they work across MANY mod repos.

  Every location is auto-detected where possible and overridable by parameter. Chosen paths
  are written to <toolkit>\x4-paths.env (the source of truth the hooks/scripts/tools read). A
  3.x config at <toolkit>\.claude\x4-paths.env is MOVED there.

  NOTE: the toolkit's hooks & bin/ scripts are bash; install with PowerShell, but to RUN the
  toolkit you still need Git Bash (https://git-scm.com/download/win), as upstream expects.

  -CodexDocMaxBytes N: write project_doc_max_bytes = N (32768..1048576) into the project
  .codex\config.toml (needs the Codex target). Codex reads the root AGENTS.md and every nested
  one into ONE 32,768-byte budget. Opt-in; a different existing value is never overwritten.

  X4_TOOLKIT: set for your user (HKCU\Environment) when unset; a DIFFERENT existing value is
  reported and left alone; -NoEnv touches nothing. -Agent auto installs the agents found on
  PATH or already in the destination (none found: all, and it says so).

  Test seams (for the installer test suite only, not for users): X4_INSTALL_ENV_REGKEY
  (a registry key used instead of HKCU\Environment; refused unless under
  HKCU\Software\X4ToolkitTests\) and X4_INSTALL_DETECT_PATH (the PATH -Agent auto walks).

  Example:
    powershell -ExecutionPolicy Bypass -File install.ps1 -Method global
    powershell -ExecutionPolicy Bypass -File install.ps1 -Method separate -Game "D:\Steam\steamapps\common\X4 Foundations"
#>
[CmdletBinding()]
param(
  [ValidateSet('in-game','separate','global')] [string]$Method,
  [string]$Game, [string]$Profile, [string]$Toolkit, [string]$Mods,
  [string]$Reference, [string]$Extensions, [string]$XRCatTool,
  # claude | codex | generic | opencode | all | auto (user decision #10, 2026-10-02: default all;
  # L-Q1: all includes opencode). auto = the agents found on PATH or already in the destination;
  # none found = all, said. Validated by hand below, case-sensitively, like install.sh.
  [string]$Agent = 'all',
  [switch]$Unpack, [switch]$Yes, [switch]$OverExisting, [switch]$DryRun, [switch]$NoEnv,
  # project_doc_max_bytes for the project .codex\config.toml (opt-in). A STRING, checked by
  # hand below, so a bad value refuses with rc 2 like install.sh rather than a binding error.
  [string]$CodexDocMaxBytes,
  # -Help: the same help as `install.sh --help`, in this installer's spelling (v4.0.0 review
  # R4-1: the docs named `.\install.ps1 -Help`, and there was no such parameter -- "A parameter
  # cannot be found that matches parameter name 'Help'" on both 5.1 and 7).
  [switch]$Help
)
$ErrorActionPreference = 'Stop'
$SRC = Split-Path -Parent $MyInvocation.MyCommand.Path
#: R2-f (second install red-team): the -OverExisting refusal ended in `...`. It now prints the
#: user's OWN command line back, plus the switch that was missing. Captured at SCRIPT scope:
#: inside a function $PSBoundParameters is that function's.
$X4OrigParams = [ordered]@{}
foreach ($k in $PSBoundParameters.Keys) { $X4OrigParams[$k] = $PSBoundParameters[$k] }
$X4ScriptPath = $MyInvocation.MyCommand.Path
function Get-R2RerunCmd([string]$extra) {
  $out = 'powershell -ExecutionPolicy Bypass -File "' + $X4ScriptPath + '"'
  foreach ($k in $X4OrigParams.Keys) {
    $v = $X4OrigParams[$k]
    if ($v -is [System.Management.Automation.SwitchParameter]) {
      if ($v.IsPresent) { $out += ' -' + $k }
    } else {
      $out += ' -' + $k + ' "' + [string]$v + '"'
    }
  }
  return ($out + ' ' + $extra)
}

# THE HELP TEXT. test_installers_agree.py pins it to install.sh's usage(): every option
# there must be named here, and every parameter here must be one install.sh has. Printed
# BEFORE anything else runs, and nothing is read or written.
function Show-Usage {
  Write-Output @'
X4 AI Assistant Toolkit installer - Windows (PowerShell).
Three install methods, all with fully configurable paths (nothing hardcoded):

  in-game   Copy the toolkit INTO your X4 game folder (the upstream model). One workspace.
  separate  Keep the toolkit in its OWN folder, pointed at the game via config.
  global    Install the skills/agents into %USERPROFILE%\.claude and write the X4_* paths into
            your global Claude settings, so they work across MANY mod repos (multi-project).

Every location is auto-detected where possible and overridable by parameter. The chosen
paths are written to <toolkit>\x4-paths.env (the single source of truth the hooks, bin/
scripts and tools read). A 3.x config at <toolkit>\.claude\x4-paths.env is MOVED there.

Run it through powershell.exe: Windows' default execution policy refuses a bare .\install.ps1.

Usage: powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -Method in-game|separate|global [options]
  -Game DIR          X4 install (folder with 01.cat..09.cat)   [auto-detected]
  -Profile DIR       user profile (saves/config/debug log)     [auto-detected]
  -Toolkit DIR       where the toolkit lives (separate/global) [repo dir / game dir]
  -Mods DIR          your mod source repos root
  -Reference DIR     unpacked base game (default <toolkit>\reference)
  -Extensions DIR    live deploy target (default <game>\extensions)
  -XRCatTool PATH    XRCatTool.exe location
  -Agent LIST        claude | codex | generic | opencode | all | auto   [all]
                     (a comma list picks several: claude,codex)
                     which agent's instructions, guards and skills to install;
                     auto = the agents found on PATH or already in the destination
                     (none found: all, and it says so)
  -Unpack            also unpack reference/ now (needs -Game + XRCatTool, and Git Bash)
  -OverExisting      REQUIRED to install over an existing installation
  -DryRun            print the destination and the item list; write nothing
  -CodexDocMaxBytes N
                     write project_doc_max_bytes = N (32768..1048576) into the
                     project's .codex\config.toml (needs the Codex target). Codex
                     reads the root AGENTS.md and every nested one into ONE
                     32,768-byte budget, so an AGENTS.md of your own lower in the
                     tree can cut the toolkit's off. Opt-in; never overwrites.
  -NoEnv             do NOT set X4_TOOLKIT for your user (by default it is set when
                     unset; a DIFFERENT existing value is reported and left alone)
  -Yes               don't prompt; accept detected/blank values (never a
                     detected DESTINATION -- name that with -Game/-Toolkit)
  -Help              this help

NOTE: the toolkit's hooks and bin/ scripts are bash: install with PowerShell, but to RUN the
toolkit you still need Git Bash (https://git-scm.com/download/win).

Test seams (for the installer test suite only, not for users):
  X4_INSTALL_ENV_REGKEY   registry key used instead of HKCU\Environment
                          (refused unless under HKCU\Software\X4ToolkitTests\)
  X4_INSTALL_DETECT_PATH  the PATH -Agent auto walks instead of $env:PATH
'@
}
if ($Help) { Show-Usage; exit 0 }
Write-Host "X4 AI Assistant Toolkit installer (Windows) - source: $SRC"

# Did a HUMAN name the destination, or did we find it by scanning? An explicit
# switch or an env var is a deliberate act; a Steam-folder scan is not.
# EXCEPT X4_TOOLKIT (FX-B2, delta review): it is INHERITED by every shell a working toolkit has
# touched -- it names the user's LIVE toolkit -- so adopting it as a "named" destination made a
# second install's refusal offer -OverExisting against that live toolkit, and -Method global
# copied ITS skills. Only -Toolkit names the toolkit destination; an inherited value is a
# default, said out loud ($X4ToolkitFromEnv) and never paired with an -OverExisting suggestion.
# AND X4_GAME (FX-B3, reviewer F M6): the same class. -Method global writes X4_GAME into
# ~/.claude/settings.json env, so every agent session inherits it, and an in-game install under
# -Yes adopted it as a NAMED destination -- the user's live game root. Only -Game names it; an
# inherited value is a default, said out loud ($X4GameFromEnv).
$GameNamed    = if ($Game)    { 'named' } else { 'detected' }
$X4GameFromEnv = (-not $Game) -and [bool]$env:X4_GAME
$ToolkitNamed = if ($Toolkit) { 'named' } else { 'detected' }
$X4ToolkitFromEnv = (-not $Toolkit) -and [bool]$env:X4_TOOLKIT
#: What this process inherited as X4_TOOLKIT, before anything here sets it (R4-3: the process
#: environment is one more place an existing value lives).
$X4InheritedToolkit = $env:X4_TOOLKIT
#: The path variables this run takes from the ENVIRONMENT, not a parameter. Each is printed
#: before any write (FX-B2: an inherited X4_REFERENCE went into a new config silently).
$X4InheritedPaths = @()
foreach ($pair in @(@('X4_GAME', $Game), @('X4_PROFILE', $Profile), @('X4_MODS', $Mods),
                    @('X4_REFERENCE', $Reference), @('X4_EXTENSIONS', $Extensions),
                    @('XRCATTOOL', $XRCatTool))) {
  if (-not $pair[1] -and [Environment]::GetEnvironmentVariable($pair[0])) { $X4InheritedPaths += $pair[0] }
}

# env fallbacks
if (-not $Game)      { $Game      = $env:X4_GAME }
if (-not $Profile)   { $Profile   = $env:X4_PROFILE }
if (-not $Toolkit)   { $Toolkit   = $env:X4_TOOLKIT }
if (-not $Mods)      { $Mods       = $env:X4_MODS }
if (-not $Reference) { $Reference  = $env:X4_REFERENCE }
if (-not $Extensions){ $Extensions = $env:X4_EXTENSIONS }
if (-not $XRCatTool) { $XRCatTool  = $env:XRCATTOOL }

# THE DRY-RUN GATE. Called from every function that WRITES, never from the
# dispatch -- the same placement as refuse_if_dry_run in install.sh, and for the
# same reason: an arm added later cannot write without passing through one of
# these three, so it cannot silently escape the flag.
#
# It had to exist at all because $DryRun was consulted in exactly ONE place,
# inside Show-Target, which the global arm never reaches. MEASURED in a sandbox:
# `-Method global -DryRun` overwrote x4-paths.env and printed
# '=== install complete (global) ==='.
#
# Defined here, above every writer, because PowerShell defines a function when
# execution REACHES it -- the same trap that already moved Write-PathsEnv's
# helper above the dispatch.
function Refuse-IfDryRun($what, $where) {
  if (-not $DryRun) { return }
  Write-Host ''
  Write-Host ('  -DryRun: NOT ' + $what + ':')
  Write-Host ('      ' + $where)
  Show-DryRunExtras $Toolkit
  Write-Host ''
  Write-Host '=== dry run complete: nothing was changed ==='
  exit 0
}

# UTF-8 WITHOUT BOM, identical under Windows PowerShell 5.1 and pwsh 7. 5.1's
# -Encoding UTF8 writes a BOM, which bash reads as a command when it sources
# x4-paths.env - the whole bash half of the toolkit then fails on line 1.
function Write-Utf8NoBom($path, [string]$content) {
  [IO.File]::WriteAllText($path, $content, (New-Object System.Text.UTF8Encoding($false)))
}

#: THE ONE WAY this script runs a native command whose stderr it redirects (FX-B2, delta
#: review). Under $ErrorActionPreference='Stop', Windows PowerShell 5.1 -- what
#: `powershell -ExecutionPolicy Bypass -File install.ps1` runs -- turns ANY line a native
#: command writes to a redirected stderr (`2>$null`, `*> $null`, `2>&1`) into a TERMINATING
#: NativeCommandError; pwsh 7 does not. MEASURED: the read-only OpenCode precheck died with a
#: raw error and exit 1 instead of its READ-ONLY refusal, and the Layer-2 summary swallowed the
#: exception and said "no reference/ tree yet". The preference is relaxed in THIS function's
#: scope only. -MergeStderr returns stderr lines as plain text (5.1 wraps them in ErrorRecords);
#: without it stderr is discarded. Returns @{ Out = <text>; Rc = <exit code> }; Rc is -1 when
#: the command could not be started at all.
function Invoke-X4Native {
  param([string]$Exe, [object[]]$Arguments = @(), [switch]$MergeStderr)
  $ErrorActionPreference = 'Continue'
  $global:LASTEXITCODE = 0
  try {
    if ($MergeStderr) { $o = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" } }
    else { $o = & $Exe @Arguments 2>$null }
    return @{ Out = (@($o) -join "`n"); Rc = $LASTEXITCODE }
  } catch {
    return @{ Out = $_.Exception.Message; Rc = -1 }
  }
}

function Ask($cur, $prompt, $def) {
  if ($cur) { $def = $cur }
  if ($Yes) { return $def }
  $ans = Read-Host "$prompt [$(if($def){$def}else{'blank'})]"
  if ([string]::IsNullOrWhiteSpace($ans)) { return $def } else { return $ans }
}

function Detect-Game {
  if ($Game) { return $Game }
  $roots = @("${env:ProgramFiles(x86)}\Steam", "$env:ProgramFiles\Steam")
  foreach ($root in $roots) {
    $p = Join-Path $root 'steamapps\common\X4 Foundations'
    if (Test-Path -LiteralPath $p) { return $p }
    $vdf = Join-Path $root 'steamapps\libraryfolders.vdf'
    if (Test-Path -LiteralPath $vdf) {
      foreach ($m in [regex]::Matches((Get-Content -Raw -LiteralPath $vdf), '"path"\s*"([^"]+)"')) {
        $lib = $m.Groups[1].Value -replace '\\\\','\'
        $p = Join-Path $lib 'steamapps\common\X4 Foundations'
        if (Test-Path -LiteralPath $p) { return $p }
      }
    }
  }
  return $Game
}

# ONE implementation of "where is the user's home", for the same reason as
# Get-GlobalClaudeDir below. `$env:USERPROFILE` is a WINDOWS-only variable: under
# PowerShell on Linux and macOS it is $null, and `Join-Path $null '.claude'` throws
# "Cannot bind argument to parameter 'Path' because it is null" -- which is exactly
# how `-Method global` died on every POSIX runner. install.sh has always written
# `${USERPROFILE:-$HOME}`; this is that same precedence, and nothing more.
function Get-UserHome {
  if ($env:USERPROFILE) { $env:USERPROFILE } elseif ($env:HOME) { $env:HOME } else { $HOME }
}

function Detect-Profile {
  if ($Profile) { return $Profile }
  $base = Join-Path (Get-UserHome) 'Documents\Egosoft\X4'
  if (Test-Path -LiteralPath $base) {
    $d = Get-ChildItem -Directory -LiteralPath $base | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($d) { return $d.FullName }
  }
  return $Profile
}

function Detect-XRCat {
  if ($XRCatTool) { return $XRCatTool }
  foreach ($c in @("$SRC\tools\XRCatTool\XRCatTool.exe", "$SRC\XTools\XRCatTool.exe")) {
    if (Test-Path -LiteralPath $c) { return $c }
  }
  return $XRCatTool
}

# Paths never copied between toolkits, pruned from the destination BOTH before and
# after the copy. One list, named once: two passes over two hand-written lists is how
# they drift, and the defect this fixes was one of the passes not running at all.
# Mirrors X4_COPY_PRUNE in install.sh -- machine-local paths that must not travel.
$X4CopyPrune = @('tools\x4validate\.venv',
                 'tools\x4validate\.pytest_cache',
                 'tools\basex\.pytest_cache',
                 'tools\x4validate\.mutation-probe-pristine',
                 '.claude\hooks\__pycache__',
                 '.claude\hooks\.pytest_cache',
                 'scripts\__pycache__',
                 'tools\.pytest_cache',
                 'tools\basex\__pycache__',
                 'tools\basex\basex\.basex',
                 'tools\x4validate\.perf-baseline.json',
                 'tools\x4validate\.obtainability-baseline.json',
                 'tools\x4validate\.claude-md-budget-baseline.json',
                 'tools\x4validate\.toolkit-usage-baseline.json',
                 'tools\x4validate\.hook-false-positive-baseline.json',
                 'tools\x4validate\.instrument-hygiene-baseline.json',
                 'tools\x4validate\.schema-sweep-baseline.json',
                 'tools\x4validate\gates\__pycache__',
                 'tools\x4validate\scripts\__pycache__',
                 'tools\x4validate\tests\__pycache__',
                 'tools\x4validate\x4validate\__pycache__')

# Copy $SRC\REL to DEST\REL, never descending into a pruned relative path.
#
# The virtualenv used to be copied in full and deleted on arrival, while the
# CHANGELOG said it no longer travels. Copy-Item -Exclude cannot express this: it
# matches LEAF NAMES, not paths, and is unreliable with -Recurse. So the walk is
# explicit, and ONLY where needed -- a directory containing no pruned path is
# still copied in a single call, so only tools\ is ever descended into.
#
# The Copy-Item target is the destination's PARENT: -Recurse into the path itself
# nests on an upgrade, which is the .claude\.claude shape.
#: Per-machine files that must NEVER travel from the source. The mirror of
#: $X4KeepLocal in install.sh, and deliberately SEPARATE from $X4CopyPrune: a
#: pruned path is also DELETED from the destination, which is right for a stale
#: .venv and catastrophic for a config.
#: The BaseX build products share these semantics exactly (this machine's, the
#: destination's own, never copied in and never deleted out), so they live HERE and
#: not in $X4CopyPrune, whose second meaning would erase a user's built databases
#: on every upgrade. See X4_KEEP_LOCAL in install.sh.
$X4KeepLocal = @('x4-paths.env','x4-housekeep.json','.claude\x4-paths.env','.claude\settings.local.json','.claude\backups','.codex\hooks.json','.codex\config.toml',
                 '.opencode\opencode.jsonc','.claude\.reference-buildid',
                 'tools\basex\basex\data','tools\basex\basex\coverage-x4raw.json',
                 'tools\basex\basex\coverage-x4eff.json','tools\basex\_eff',
                 'tools\basex\stage-manifest.json')

#: THE TRACKED SET, when the source is a git checkout -- the mirror of
#: _tracked_copy_set in install.sh, same two clauses: $SRC is the TOP of its work
#: tree (a toolkit inside SOMEONE ELSE's repo is governed by that repo), and the
#: tracked set COVERS every copy item present on disk (a repo tracking .claude but
#: not tools would otherwise install no tools, silently). Otherwise $false, the copy
#: walks the disk exactly as before, and $script:X4TrackedWhy says why. Paths come
#: back with backslashes, filtered through the same two lists as the walk. Cached.
$script:X4TrackedState = ''
$script:X4TrackedWhy = ''
$script:X4Tracked = @()
$script:X4TrackedAbsent = 0
function Get-TrackedCopySet {
  if ($script:X4TrackedState) { return ($script:X4TrackedState -eq 'yes') }
  $script:X4TrackedState = 'no'
  $git = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $git) { return $false }
  $prevEAP = $ErrorActionPreference
  $prevEnc = $null
  # UTF-8, or a non-ASCII tracked name decodes to a path that does not exist and
  # is silently counted as absent.
  try { $prevEnc = [Console]::OutputEncoding; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { $prevEnc = $null }
  $ErrorActionPreference = 'Continue'
  try {
    $top = & $git.Source -C $SRC rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $top) { return $false }
    if (-not (Test-SameDir ([string]$top).Trim() $SRC)) {
      $script:X4TrackedWhy = "the source sits inside another repository ($top)"
      return $false
    }
    $raw = (& $git.Source -C $SRC ls-files -z 2>$null) -join "`n"
    if ($LASTEXITCODE -ne 0) { return $false }
  } finally {
    $ErrorActionPreference = $prevEAP
    if ($prevEnc) { try { [Console]::OutputEncoding = $prevEnc } catch { } }
  }
  $all = @($raw.Split([char]0) | Where-Object { $_ })
  $heads = @{}
  foreach ($p in $all) { $heads[($p -split '/', 2)[0]] = $true }
  foreach ($i in $X4Items) {
    if (-not (Test-Path -LiteralPath (Join-Path $SRC $i))) { continue }
    if (-not $heads.ContainsKey($i)) {
      $script:X4TrackedWhy = "git tracks nothing under '$i', which is on disk"
      return $false
    }
  }
  $files = New-Object System.Collections.Generic.List[string]
  $absent = 0
  foreach ($p in $all) {
    $rel = $p.Replace([char]47, [char]92)
    $first = ($rel -split '\\', 2)[0]
    $skip = -not ($X4Items -contains $first)
    foreach ($junk in ($X4CopyPrune + $X4KeepLocal)) {
      if ($rel -ieq $junk -or $rel -like ($junk + [char]92 + '*')) { $skip = $true }
    }
    foreach ($junk in $X4KeepLocal) {
      if ($rel -ieq ($junk + '.example')) { continue }
      if ($rel -like ($junk + '.*')) { $skip = $true }
    }
    if ($skip) { continue }
    if (Test-Path -LiteralPath (Join-Path $SRC $rel) -PathType Leaf) { $files.Add($rel) } else { $absent++ }
  }
  $script:X4Tracked = $files.ToArray()
  $script:X4TrackedAbsent = $absent
  $script:X4TrackedState = 'yes'
  return $true
}

#: Copy the tracked set into $dest. Copy-Item runs in-process, so one call per
#: file is cheap here (install.sh batches by directory instead).
function Copy-TrackedSet($dest) {
  foreach ($rel in $script:X4Tracked) {
    $target = Join-Path $dest $rel
    $parent = Split-Path -Parent $target
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Copy-Item -Force -LiteralPath (Join-Path $SRC $rel) -Destination $target
  }
}

#: The key=value lines Write-PathsEnv OWNS, as it would write them now. Factored
#: out so the precondition and the writer cannot disagree about what "would
#: change" means.
#: A DERIVED path, spelled as install.sh spells it: '<base>/<leaf>'. Join-Path wrote a
#: backslash, so one input gave two configs (v4.0.0 review, pre-arc minor). The base keeps
#: whatever spelling the user gave it; only the derived separator is pinned.
function Join-DerivedPath($base, $leaf) {
  return (([string]$base).TrimEnd([char]92, [char]47) + '/' + $leaf)
}

function Get-OwnedEnvLines($t) {
  $ref = if ($Reference) { $Reference } else { Join-DerivedPath $t 'reference' }
  $ext = if ($Extensions) { $Extensions } elseif ($Game) { Join-DerivedPath $Game 'extensions' } else { '' }
  $lines = @(('X4_TOOLKIT="' + (Get-EscapedEnvValue $t) + '"'))
  if ($Game)      { $lines += ('X4_GAME="' + (Get-EscapedEnvValue $Game) + '"') }
  $lines += ('X4_REFERENCE="' + (Get-EscapedEnvValue $ref) + '"')
  if ($Profile)   { $lines += ('X4_PROFILE="' + (Get-EscapedEnvValue $Profile) + '"')
                    $lines += ('X4_DEBUGLOG="' + (Get-EscapedEnvValue (Join-DerivedPath $Profile 'debug.txt')) + '"') }
  if ($Mods)      { $lines += ('X4_MODS="' + (Get-EscapedEnvValue $Mods) + '"') }
  if ($ext)       { $lines += ('X4_EXTENSIONS="' + (Get-EscapedEnvValue $ext) + '"') }
  if ($XRCatTool) { $lines += ('XRCATTOOL="' + (Get-EscapedEnvValue $XRCatTool) + '"') }
  return ,$lines
}

#: The same keys as they stand in the file today. Comments and carried keys are
#: excluded deliberately: carried keys come FROM the file so they can never
#: differ, and the timestamp header changes on every run, which would make every
#: upgrade look like a change and defeat the check.
function Get-OwnedEnvLinesFromFile($f) {
  $owned = @('X4_TOOLKIT','X4_GAME','X4_REFERENCE','X4_PROFILE','X4_DEBUGLOG','X4_MODS','X4_EXTENSIONS','XRCATTOOL')
  $out = @()
  if (Test-Path -LiteralPath $f) {
    # READABLE, or say so. Held open FileShare::None by an editor, an AV scanner or
    # a sync client, this threw a raw PowerShell dump naming the line and the file:
    # no crafted message, no INCOMPLETE accounting. Same class as the unguarded
    # Move-Item, one step earlier in the same flow. Cannot-read must never be
    # silently promoted to "differs".
    $lines0 = $null
    try { $lines0 = Get-Content -LiteralPath $f -ErrorAction Stop } catch { return ,@('__X4_UNREADABLE__') }
    foreach ($line in $lines0) {
      if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
      # TRIMMED, matching Write-PathsEnv below and install.sh. This one was not,
      # so the two halves of this installer disagreed about whether an indented
      # key is owned -- it happened to fail safe, which is not the same as correct.
      $key = ($line -split '=',2)[0].Trim()
      if ($owned -contains $key) { $out += $line }
    }
  }
  return ,$out
}

#: PRECONDITION, checked before ANY file is written and in -DryRun too.
#:
#: x4lock marks the path config read-only and README tells users to run it, so the
#: documented upgrade path met
#:   Exception calling "WriteAllText": Access to the path ... is denied
#: after the copy had already run, leaving TWO orphaned backups behind. MEASURED
#: 2026-09-07 in a sandbox, the same defect install.sh had with a different error
#: surface -- which is why the parity test drives BOTH and asserts the verdict.
#:
#: An upgrade that does not need to CHANGE the config never writes it, so the lock
#: is irrelevant. One that must change it REFUSES, naming the unlock command:
#: silently unlocking would defeat the mechanism the user turned on.
#: THE COPY SET, named ONCE -- the mirror of $X4_COPY_ITEMS in install.sh. It was
#: written out twice here as well (the copy and the dry-run listing), and the
#: locked-target precheck below is a third caller.
#:
#: COMMON to every agent target; each agent's own files live in $X4AgentItems only.
$X4CopyItems = @('tools','bin','scripts','mods','KNOWLEDGEBASE.md','KNOWLEDGEBASE.local.md.example','README.md',
                 'CHANGELOG.md','LICENSE','setup.sh','install.sh','install.ps1','SETUP_PROMPT.txt','ADAPTING.md','RETENTION.md','x4-housekeep.json.example',
                 '.gitignore','.gitattributes','x4-paths.env.example')

#: PER-AGENT sets -- the mirror of X4_AGENT_ITEMS_* in install.sh (audit F8). `agent\`,
#: the neutral source, is in NO set: an installed toolkit is runtime-only (decision #9).
$X4AgentItems = @{
  claude  = @('.claude','CLAUDE.md')
  codex   = @('AGENTS.md','.codex','.agents')
  generic = @('AGENTS.md','.agents')
  opencode = @('AGENTS.md','.opencode')
}
#: `all` (the default) installs EVERY name here, OpenCode included (user decision L-Q1,
#: 2026-10-02). OpenCode is BEST EFFORT -- from its docs and source, not measured.
$X4AgentNames = @('claude','codex','generic','opencode')

#: -Agent auto (lane H): how each agent is DETECTED -- the mirror of X4_AGENT_DETECT_* /
#: X4_AGENT_MARK_* in install.sh. An agent with no row is never detected. Never a bare
#: `.claude\`: every 3.x install created one (x4-paths.env lived there), whatever the agent.
$X4AgentDetect = @{
  claude = @('claude')
  codex  = @('codex')
  opencode = @('opencode')
}
$X4AgentMark = @{
  claude = @('CLAUDE.md','.claude/settings.json')
  codex  = @('.codex')
  opencode = @('.opencode','opencode.json','opencode.jsonc')
}
#: NAME or NAME + one of these, on Windows. Get-Command and Git Bash's `command -v` disagree
#: about which codex shim they find (MEASURED), so both installers walk PATH themselves.
$X4DetectExts = @('.exe','.cmd','.bat','.ps1')

#: The skill token and its rendering PER OS, as in install.sh: Codex runs PowerShell on
#: Windows, where `$X4_TOOLKIT` is an EMPTY variable (MEASURED, lane A).
$X4ToolkitToken = '{{TOOLKIT}}'
$X4ToolkitRender = @{ windows = '$env:X4_TOOLKIT'; posix = '$X4_TOOLKIT' }
$X4TokenDirs = @('.agents','.opencode')
#: Windows PowerShell 5.1 has no $IsWindows and only runs on Windows.
$X4OnWindows = ($PSVersionTable.PSEdition -ne 'Core') -or $IsWindows
$X4CodexHooksTmpl = 'agent/targets/codex/hooks.json.tmpl'

# --- resolve -Agent, BEFORE anything is written --------------------------------------
if ($Agent -ceq 'all') { $X4Agents = $X4AgentNames }
elseif ($Agent -ceq 'auto') { $X4Agents = @() }   # resolved per destination by Resolve-HAutoAgents
elseif ($X4AgentNames -ccontains $Agent) { $X4Agents = @($Agent) }
elseif ($Agent -match '[,\s]') {
  # C1 (install red-team 2026-10-04): a COMMA LIST (`claude,codex`), each item validated before
  # anything is written; duplicates collapse; all/auto stand alone. Whitespace separates too:
  # `-Agent claude,codex` typed inside PowerShell binds an ARRAY, which [string] joins with spaces.
  $X4Agents = @()
  foreach ($it in ($Agent -split '\s*,\s*|\s+')) {
    if ($it -ceq '') {
      Write-Host ("REFUSING: -Agent '" + $Agent + "' has an empty item. Supported: " + ($X4AgentNames -join ' ') + ' (comma-separated), all, auto. Nothing has been changed.') -ForegroundColor Red
      exit 2
    }
    if ($it -ceq 'all' -or $it -ceq 'auto') {
      Write-Host ("REFUSING: -Agent '" + $it + "' cannot be combined with other agents in a list ('" + $Agent + "'). Nothing has been changed.") -ForegroundColor Red
      exit 2
    }
    if (-not ($X4AgentNames -ccontains $it)) {
      Write-Host ("REFUSING: unknown agent '" + $it + "' in -Agent '" + $Agent + "'. Supported: " + ($X4AgentNames -join ' ') + ' (comma-separated), all, auto. Nothing has been changed.') -ForegroundColor Red
      exit 2
    }
    if (-not ($X4Agents -ccontains $it)) { $X4Agents += $it }
  }
  $picked = $X4Agents
  $X4Agents = @($X4AgentNames | Where-Object { $picked -ccontains $_ })   # canonical order, as install.sh
}
else {
  Write-Host ("REFUSING: unknown -Agent '" + $Agent + "'. Supported: " + ($X4AgentNames -join ' ') + ' all. Nothing has been changed.') -ForegroundColor Red
  exit 2
}
#: THE RESOLVED COPY SET -- common items plus each selected agent's, once each. Every
#: consumer reads this, so the copy, the listing and the prechecks cannot disagree.
function Resolve-HItems {
  $script:X4Items = @($X4CopyItems)
  foreach ($a0 in $script:X4Agents) {
    foreach ($i0 in $X4AgentItems[$a0]) { if ($script:X4Items -notcontains $i0) { $script:X4Items += $i0 } }
  }
}
Resolve-HItems

#: Where $n is found on the detect PATH, or $null. X4_INSTALL_DETECT_PATH is a TEST SEAM
#: (the harness points it at a sandbox); unset, the real PATH is walked.
function Test-HOnPath($n) {
  $p = if ($env:X4_INSTALL_DETECT_PATH) { $env:X4_INSTALL_DETECT_PATH } else { $env:PATH }
  if (-not $p) { return $null }
  foreach ($d in ($p -split [regex]::Escape([string][IO.Path]::PathSeparator))) {
    if (-not $d) { continue }
    if ($X4OnWindows) {
      foreach ($e in (@('') + $X4DetectExts)) {
        $f = Join-Path $d ($n + $e)
        if (Test-Path -LiteralPath $f -PathType Leaf) { return $f }
      }
    } else {
      $f = Join-Path $d $n
      if (Test-Path -LiteralPath $f -PathType Leaf) {
        $mode = $null
        try { $mode = (Get-Item -LiteralPath $f).UnixMode } catch { }
        if (-not $mode -or $mode -match 'x') { return $f }
      }
    }
  }
  return $null
}

#: -Agent auto: select exactly the agents detected for $dest, each with its reason; none
#: detected selects all (user decision H-Q1) and SAYS so. Then the copy set is re-derived.
function Resolve-HAutoAgents($dest) {
  if ($Agent -cne 'auto') { return }
  $det = @(); $says = @()
  foreach ($a in $X4AgentNames) {
    $why = $null
    foreach ($n in @($X4AgentDetect[$a])) {
      if (-not $n) { continue }
      $f = Test-HOnPath $n
      if ($f) { $why = 'on PATH: ' + $f; break }
    }
    if (-not $why) {
      foreach ($m in @($X4AgentMark[$a])) {
        if (-not $m) { continue }
        $t = Join-Path $dest $m
        if (Test-Path -LiteralPath $t -PathType Container) { $why = 'destination has ' + $m + '/'; break }
        if (Test-Path -LiteralPath $t) { $why = 'destination has ' + $m; break }
      }
    }
    if ($why) { $det += $a; $says += ($a + ' (' + $why + ')') }
  }
  if ($det.Count) {
    $script:X4Agents = $det
    Write-Host ('  -Agent auto: ' + ($says -join ', '))
  } else {
    $script:X4Agents = $X4AgentNames
    Write-Host '  -Agent auto: no agent detected (none on PATH, no marker in the destination);'
    Write-Host ('               installing all: ' + ($X4AgentNames -join ' '))
  }
  Resolve-HItems
}

#: -CodexDocMaxBytes (lane H): the NUMBER is checked here, before anything is written; that
#: it needs the Codex target is checked per arm, once -Agent auto has resolved.
$X4CodexDocMin = 32768
$X4CodexDocMax = 1048576
if ($CodexDocMaxBytes) {
  $okN = ($CodexDocMaxBytes -match '^[1-9][0-9]{0,6}$') -and ([int]$CodexDocMaxBytes -ge $X4CodexDocMin) -and ([int]$CodexDocMaxBytes -le $X4CodexDocMax)
  if (-not $okN) {
    Write-Host ("REFUSING: -CodexDocMaxBytes '" + $CodexDocMaxBytes + "' is not a whole number from $X4CodexDocMin to $X4CodexDocMax.") -ForegroundColor Red
    Write-Host '  Nothing has been changed.' -ForegroundColor Red
    exit 2
  }
}

#: Failures that make the install INCOMPLETE. Defined HERE, above every writer that
#: records into it (the Codex writers run inside the dispatch).
$script:failed = @()
$script:X4CodexHooksWritten = $false

function Test-ItemSelected($i) { return ($X4Items -contains $i) }

function Get-AgentsLanded {
  $out = @()
  foreach ($a in $X4Agents) {
    $ok = $true
    foreach ($i in $X4AgentItems[$a]) { if (-not (Test-Path -LiteralPath (Join-Path $SRC $i))) { $ok = $false } }
    if ($ok) { $out += $a }
  }
  return ($out -join ', ')
}

#: Text with CR removed and trailing newlines trimmed -- the comparison install.sh makes
#: with `$(tr -d '\r' < f)`. CASE-SENSITIVE (-ceq) at every use, as bash's is.
function Get-NormText($p) { return ([IO.File]::ReadAllText($p)).Replace("`r", '').TrimEnd("`n") }

# --- AGENTS.md: never overwrite one the toolkit did not write -------------------------
# 0 of 23 tagged releases before 4.0 shipped AGENTS.md (MEASURED 2026-10-02), so one in a
# destination today is its user's, and "differs from ours" is exactly "not ours".
# A KNOWN shipped AGENTS.md (scripts/shipped-instruction-hashes.txt, generated from every
# release tag) is ours, so it is replaced rather than moved aside -- see install.sh.
function Get-AgentsMdMoveTarget($dest) {
  if (-not (Test-ItemSelected 'AGENTS.md')) { return $null }
  $s = Join-Path $SRC 'AGENTS.md'
  $d = Join-Path $dest 'AGENTS.md'
  if (-not (Test-Path -LiteralPath $s -PathType Leaf) -or -not (Test-Path -LiteralPath $d -PathType Leaf)) { return $null }
  if ((Get-NormText $s) -ceq (Get-NormText $d)) { return $null }
  $h = Get-HCanonicalSha256 $d
  if ($h -and ((Test-HKnownHash $h 'AGENTS.md') -eq 'yes')) { return $null }
  $n = 'AGENTS.pre-4.0.md'
  if (-not (Test-Path -LiteralPath (Join-Path $dest $n))) { return $n }
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $n = "AGENTS.pre-4.0.$stamp.md"
  $k = 0
  while (Test-Path -LiteralPath (Join-Path $dest $n)) { $k++; $n = "AGENTS.pre-4.0.$stamp-$k.md" }
  return $n
}

function Save-UserAgentsMd($dest) {
  $to = Get-AgentsMdMoveTarget $dest
  if (-not $to) { return }
  Refuse-IfDryRun 'moving your AGENTS.md aside in' $dest
  try {
    Move-Item -LiteralPath (Join-Path $dest 'AGENTS.md') -Destination (Join-Path $dest $to) -ErrorAction Stop
  } catch {
    Write-Host ("ERROR: could not move AGENTS.md aside to " + $to + ": " + $_.Exception.Message) -ForegroundColor Red
    Write-Host '       Nothing else has been changed.' -ForegroundColor Red
    exit 1
  }
  Write-Host '  [note] your AGENTS.md differs from the one this toolkit ships, so it was KEPT as:'
  Write-Host ('           ' + (Join-Path $dest $to))
}

# --- shipped-version hashes (lane H) -- the mirror of install.sh's _h_* helpers -------
#: The data file both installers read; absent means nothing is known to be ours, so every
#: differing file is KEPT (the safe direction).
$X4ShippedHashes = 'scripts/shipped-instruction-hashes.txt'

#: THE CANONICAL HASH (one definition, three implementations): drop a leading UTF-8 BOM,
#: delete every CR (13), trim trailing LFs (10), SHA-256, lowercase hex. On BYTES, never
#: decoded, so invalid UTF-8 cannot change the answer. $null when the file is unreadable.
function Get-HCanonicalSha256($path) {
  try { $b = [IO.File]::ReadAllBytes($path) } catch { return $null }
  $start = 0
  if ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF) { $start = 3 }
  $out = New-Object 'System.Collections.Generic.List[byte]' -ArgumentList $b.Length
  for ($k = $start; $k -lt $b.Length; $k++) { if ($b[$k] -ne 13) { $out.Add($b[$k]) } }
  $n = $out.Count
  while ($n -gt 0 -and $out[$n - 1] -eq 10) { $n-- }
  if ($n -lt $out.Count) { $out.RemoveRange($n, $out.Count - $n) }
  $sha = [Security.Cryptography.SHA256]::Create()
  try { $h = $sha.ComputeHash($out.ToArray()) } finally { $sha.Dispose() }
  return (-join ($h | ForEach-Object { $_.ToString('x2') }))
}

#: Is ($hash, $name) a row of the shipped list? 'yes', 'no', or 'nolist'.
function Test-HKnownHash($hash, $name) {
  $f = Join-Path $SRC $X4ShippedHashes
  if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { return 'nolist' }
  foreach ($line in [IO.File]::ReadAllLines($f)) {
    $p = $line.Trim() -split '\s+'
    if ($p.Count -lt 2 -or $p[0] -eq '' -or $p[0].StartsWith('#')) { continue }
    if ($p[0] -ceq $hash -and $p[1] -ceq $name) { return 'yes' }
  }
  return 'no'
}

#: Is $dest\$name a file the toolkit SHIPPED -- the source's copy, or any release's?
function Test-HIsShipped($dest, $name) {
  $h = Get-HCanonicalSha256 (Join-Path $dest $name)
  if (-not $h) { return $false }
  $s = Get-HCanonicalSha256 (Join-Path $SRC $name)
  if ($s -and ($s -ceq $h)) { return $true }
  return ((Test-HKnownHash $h $name) -eq 'yes')
}

# -OverExisting does not confer ownership of personal hooks or permissions.
function Test-ProjectSettingsPrecheck($dest) {
  if (-not (Test-ItemSelected '.claude')) { return }
  $dir = Join-Path $dest '.claude'
  $file = Join-Path $dir 'settings.json'
  foreach ($path in @($dir, $file)) {
    $entry = Get-Item -Force -LiteralPath $path -ErrorAction SilentlyContinue
    if ($entry -and ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      throw 'Project settings traverse a link; review this destination separately. Nothing has been changed.'
    }
  }
  if (-not (Test-Path -LiteralPath $file)) { return }
  if ((Test-Path -LiteralPath $file -PathType Leaf) -and (Test-HIsShipped $dest '.claude/settings.json')) { return }
  Write-Host 'REFUSING: custom project settings need a reviewed upgrade; -OverExisting cannot replace them.' -ForegroundColor Red
  Write-Host '  Use tools/agent-harness.py upgrade-stage to retain and review your settings, handlers and permissions.'
  throw 'Custom project settings were retained. Nothing has been changed.'
}

#: The first free name $base.md / $base.<stamp>.md / $base.<stamp>-N.md in $dest.
function Get-HAsideName($dest, $base) {
  $n = "$base.md"
  if (-not (Test-Path -LiteralPath (Join-Path $dest $n))) { return $n }
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $n = "$base.$stamp.md"
  $k = 0
  while (Test-Path -LiteralPath (Join-Path $dest $n)) { $k++; $n = "$base.$stamp-$k.md" }
  return $n
}

#: 3.x -> 4.0: the name $dest's CLAUDE.md would be KEPT as, or $null when it is ours or no
#: Claude copy happens. One function for the dry-run listing and the mover.
function Get-HClaudeMdMoveTarget($dest) {
  if (-not (Test-ItemSelected 'CLAUDE.md')) { return $null }
  if (-not (Test-Path -LiteralPath (Join-Path $SRC 'CLAUDE.md') -PathType Leaf) -or
      -not (Test-Path -LiteralPath (Join-Path $dest 'CLAUDE.md') -PathType Leaf)) { return $null }
  if (Test-HIsShipped $dest 'CLAUDE.md') { return $null }
  return (Get-HAsideName $dest 'X4-NOTES.pre-4.0')
}

#: Why that decision is narrower than it looks, when it is: SAID, never silent.
function Show-HHashCaveat {
  if (-not (Test-Path -LiteralPath (Join-Path $SRC $X4ShippedHashes) -PathType Leaf)) {
    Write-Host ('  [note] this source has no ' + $X4ShippedHashes + ', so no shipped version is known:')
    Write-Host '         any CLAUDE.md that differs from this one is treated as yours and kept.'
  }
}

#: The twin of install.sh's _old_example_action / retire_old_paths_example (install red-team
#: 2026-10-04): the 3.x `.claude/x4-paths.env.example` is removed ONLY when it is an unedited
#: shipped copy; an edited one is kept and named. Returns 'remove', 'keep' or ''.
function Get-HOldExampleAction($dest) {
  $old = Join-Path (Join-Path $dest '.claude') 'x4-paths.env.example'
  if (-not (Test-Path -LiteralPath $old -PathType Leaf)) { return '' }
  if (-not (Test-Path -LiteralPath (Join-Path $SRC 'x4-paths.env.example') -PathType Leaf)) { return '' }
  if (Test-HIsShipped $dest '.claude/x4-paths.env.example') { return 'remove' }
  return 'keep'
}
function Remove-HOldPathsExample($dest) {   # AFTER the copy
  $old = Join-Path (Join-Path $dest '.claude') 'x4-paths.env.example'
  switch (Get-HOldExampleAction $dest) {
    'remove' {
      try { Remove-Item -LiteralPath $old -ErrorAction Stop } catch { }
      if (Test-Path -LiteralPath $old) {
        Write-Host ('  [warn] could not remove the stale 3.x ' + $old + ' (it is unedited, so deleting it is safe).')
      } else {
        Write-Host "  [note] removed the 3.x .claude/x4-paths.env.example (an unedited shipped copy); 4.0's"
        Write-Host '         example is x4-paths.env.example at the toolkit root.'
      }
    }
    'keep' {
      Write-Host ('  [note] kept ' + $dest + '/.claude/x4-paths.env.example: it differs from every example a')
      Write-Host '         release shipped (your edits?). 4.0 never reads it; delete it when you are done.'
    }
  }
}

#: R2-e (second install red-team): the kept copy is the WHOLE old CLAUDE.md, and the user wants
#: their EDITS. The release that shipped the original is named by the destination's
#: CHANGELOG.md (read BEFORE the copy replaces it): its first `## vX` heading. install.sh's
#: _r2_prev_version / _r2_your_edits_hint are the twin.
$X4ReleasesUrl = 'https://github.com/WingedGuardian/x4-ai-toolkit/releases'
$X4RawUrl = 'https://raw.githubusercontent.com/WingedGuardian/x4-ai-toolkit'
function Get-R2PrevVersion($dest) {
  $f = Join-Path $dest 'CHANGELOG.md'
  if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { return '' }
  foreach ($line in (Get-Content -LiteralPath $f -Encoding UTF8)) {
    $m = [regex]::Match($line, '^## (v[0-9][0-9.]*[0-9])')
    if ($m.Success) { return $m.Groups[1].Value }
  }
  return ''
}
function Write-R2YourEditsHint($dest, $to, $prev) {
  Write-Host '         To see only YOUR edits, compare it with the CLAUDE.md the toolkit shipped:'
  if ($prev) {
    Write-Host ('           ' + $X4RawUrl + '/' + $prev + '/CLAUDE.md   (' + $prev + ': your previous install, per its CHANGELOG.md)')
  } else {
    Write-Host ('           the CLAUDE.md of the release you had installed: ' + $X4ReleasesUrl)
  }
  Write-Host ('           save it, then:  git diff --no-index "<saved CLAUDE.md>" "' + (Join-Path $dest $to) + '"')
}

function Save-HUserClaudeMd($dest) {
  $to = Get-HClaudeMdMoveTarget $dest
  if (-not $to) { return }
  $prev = Get-R2PrevVersion $dest
  Refuse-IfDryRun 'keeping your CLAUDE.md as X4-NOTES.pre-4.0 in' $dest
  try {
    Move-Item -LiteralPath (Join-Path $dest 'CLAUDE.md') -Destination (Join-Path $dest $to) -ErrorAction Stop
  } catch {
    Write-Host ("ERROR: could not move CLAUDE.md aside to " + $to + ": " + $_.Exception.Message) -ForegroundColor Red
    Write-Host '       Nothing else has been changed.' -ForegroundColor Red
    exit 1
  }
  Write-Host '  [note] your CLAUDE.md is not one this toolkit ever shipped, so it was KEPT (not lost) as:'
  Write-Host ('           ' + (Join-Path $dest $to))
  Write-Host '         That copy is NO LONGER LOADED by any agent: the 4.0 CLAUDE.md replaced it.'
  Write-Host '         To bring your notes back, merge what you want into KNOWLEDGEBASE.local.md in'
  Write-Host '         the same folder -- every agent''s instructions read it (and a legacy X4-NOTES.md),'
  Write-Host '         and the toolkit never overwrites it.'
  Write-R2YourEditsHint $dest $to $prev
  Show-HHashCaveat
}

# --- {{TOOLKIT}} in the Codex / generic skills ----------------------------------------
#: PER TARGET SHELL (v4.0.0 review R4-6) -- install.sh's _toolkit_render_value: the OpenCode
#: copy gets the bash form on Windows only when X4_OPENCODE_SHELL=bash says OpenCode runs bash.
function Get-ToolkitRenderValue($d) {
  if ($X4OnWindows -and -not ($d -eq '.opencode' -and $env:X4_OPENCODE_SHELL -eq 'bash')) { $X4ToolkitRender['windows'] }
  else { $X4ToolkitRender['posix'] }
}

#: Rewrite the token in the files THIS install copied -- enumerated from the SOURCE,
#: never from a destination glob -- and verify each file afterwards.
function Invoke-ToolkitTokenRender($dest) {
  Refuse-IfDryRun 'rendering the skill token in' $dest
  $n = 0
  $said = @()
  foreach ($d in $X4TokenDirs) {
    if (-not (Test-ItemSelected $d)) { continue }
    $from = Join-Path $SRC $d
    if (-not (Test-Path -LiteralPath $from -PathType Container)) { continue }
    $to = Get-ToolkitRenderValue $d
    $said += ($d + '/ as ' + $to)
    $fromFull = (Get-Item -LiteralPath $from -Force).FullName
    foreach ($f in (Get-ChildItem -LiteralPath $from -Recurse -File -Force -ErrorAction SilentlyContinue)) {
      $rel = $f.FullName.Substring($fromFull.Length).TrimStart([char]92, [char]47)
      $t = Join-Path (Join-Path $dest $d) $rel
      if (-not (Test-Path -LiteralPath $t -PathType Leaf)) { continue }
      $c = [IO.File]::ReadAllText($t)
      if (-not $c.Contains($X4ToolkitToken)) { continue }
      try { Write-Utf8NoBom $t ($c.Replace($X4ToolkitToken, $to)) } catch { }
      if ([IO.File]::ReadAllText($t).Contains($X4ToolkitToken)) {
        $script:failed += ($d + '/' + $rel + ' (the ' + $X4ToolkitToken + ' token could not be rendered)')
      } else { $n++ }
    }
  }
  if ($n -gt 0) { Write-Host ('  rendered ' + $X4ToolkitToken + ' in ' + $n + ' skill file(s): ' + ($said -join ', ')) }
}

#: In place nothing is copied and nothing is rendered (a toolkit checkout holds the
#: generator's output). Said, never silent.
function Show-InPlaceTokenNote {
  foreach ($d in $X4TokenDirs) {
    if (-not (Test-ItemSelected $d)) { continue }
    $from = Join-Path $SRC $d
    if (-not (Test-Path -LiteralPath $from -PathType Container)) { continue }
    $hit = Get-ChildItem -LiteralPath $from -Recurse -File -Force -ErrorAction SilentlyContinue |
           Where-Object { ([IO.File]::ReadAllText($_.FullName)).Contains($X4ToolkitToken) } | Select-Object -First 1
    if ($hit) {
      Write-Host ('  [note] installing in place: ' + $d + '/ keeps the ' + $X4ToolkitToken + ' token unrendered.')
      Write-Host ('         An agent reading those skills should read it as ' + (Get-ToolkitRenderValue $d) + '.')
    }
  }
}

# --- the Codex hook definitions -------------------------------------------------------
function Test-CodexSelected { return ((Test-ItemSelected '.codex') -and (Test-Path -LiteralPath (Join-Path $SRC '.codex') -PathType Container)) }
function Get-CodexTemplatePath { return (Join-Path $SRC $X4CodexHooksTmpl) }
function Get-CodexHooksJsonPath($dest) { return (Join-Path (Join-Path $dest '.codex') 'hooks.json') }

#: The template with {{ROOT}} (forward slashes) and {{ROOT_WIN}} (backslashes) filled,
#: each JSON-escaped -- lane B's render_codex_hooks_json contract, re-implemented because
#: an installer cannot import the generator. $null when it cannot be rendered.
function Get-CodexHooksJson($dest) {
  try { $root = (Resolve-Path -LiteralPath $dest -ErrorAction Stop).ProviderPath } catch { return $null }
  if (-not (Test-CodexRootSafe $root)) { return $null }
  $bs = [string][char]92
  $root = $root.TrimEnd([char]92, [char]47)
  $posix = $root.Replace($bs, '/')
  $win = $posix.Replace('/', $bs)
  try { $t = [IO.File]::ReadAllText((Get-CodexTemplatePath)) } catch { return $null }
  $t = $t.Replace("`r", '').TrimEnd("`n")
  $t = $t.Replace('{{ROOT_WIN}}', $win.Replace($bs, $bs + $bs)).Replace('{{ROOT}}', $posix.Replace($bs, $bs + $bs))
  if ($t.Contains('{{ROOT')) { return $null }
  return $t
}

function Test-CodexRootSafe($root) {
  foreach ($c in @('"', [string][char]10, [string][char]13, '%', '!', '$', [string][char]96)) {
    if ($root.Contains($c)) { return $false }
  }
  return $true
}

function Test-CodexMatchesTemplate($dest, $expected) {
  $f = Get-CodexHooksJsonPath $dest
  if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { return $false }
  if ((Get-NormText $f) -ceq $expected) { return $true }
  $helper = Join-Path $SRC 'tools/x4_harness/x4_upgrade_render.py'
  if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { return $false }
  $py = Find-OcPython
  if (-not $py) { return $false }
  $extra = @($py | Select-Object -Skip 1)
  $checked = Invoke-X4Native $py[0] (@($extra) + @('-B', $helper, 'check-codex', '--root', $dest,
                                                '--template', (Get-CodexTemplatePath)))
  return ($checked.Rc -eq 0)
}

#: Is $dest's .codex/hooks.json an UNEDITED rendering of a template some release SHIPPED?
#: The mirror of install.sh's _codex_is_shipped_rendering: un-render this root's two
#: JSON-escaped spellings back to {{ROOT_WIN}} / {{ROOT}} (the inverse of Get-CodexHooksJson)
#: and look the canonical hash up in the shipped list under the template's name (release
#: review H4: the first template change after a release refused every unedited upgrade).
function Test-CodexShippedRendering($dest) {
  $f = Get-CodexHooksJsonPath $dest
  if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { return $false }
  try { $root = (Resolve-Path -LiteralPath $dest -ErrorAction Stop).ProviderPath } catch { return $false }
  if (-not (Test-CodexRootSafe $root)) { return $false }
  $bs = [string][char]92
  $root = $root.TrimEnd([char]92, [char]47)
  $posix = $root.Replace($bs, '/')
  $win = $posix.Replace('/', $bs)
  try { $b = [IO.File]::ReadAllBytes($f) } catch { return $false }
  $start = 0
  if ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF) { $start = 3 }
  $t = (New-Object Text.UTF8Encoding($false)).GetString($b, $start, $b.Length - $start)
  $t = $t.Replace("`r", '').TrimEnd("`n")
  $t = $t.Replace($win.Replace($bs, $bs + $bs), '{{ROOT_WIN}}').Replace($posix.Replace($bs, $bs + $bs), '{{ROOT}}')
  $sha = [Security.Cryptography.SHA256]::Create()
  try { $h = $sha.ComputeHash((New-Object Text.UTF8Encoding($false)).GetBytes($t)) } finally { $sha.Dispose() }
  $hex = -join ($h | ForEach-Object { $_.ToString('x2') })
  return ((Test-HKnownHash $hex $X4CodexHooksTmpl) -eq 'yes')
}

#: PRECONDITION before any write: a READ-ONLY hooks.json this install would CHANGE.
#: The "unlock, re-run, lock" steps, aimed at the DESTINATION's own x4lock (FX-B2, delta review;
#: install.sh's _x4lock_steps). `scripts/x4lock.py` relative to the cwd is the SOURCE's copy,
#: whose manifest never holds a separate install's files: `unlock` answered "not in the
#: protected manifest", exit 0, and the re-run refused again -- a loop. No file: --all.
function Write-HX4LockSteps($d, $file, [switch]$Red) {
  $lk = Join-Path (Join-Path $d 'scripts') 'x4lock.py'
  $what = if ($file) { '"' + $file + '"' } else { '--all' }
  if (Test-Path -LiteralPath $lk -PathType Leaf) {
    $lines = @(('      python "' + $lk + '" unlock ' + $what + ' --toolkit "' + $d + '"'),
               '      <re-run this command>',
               ('      python "' + $lk + '" lock --toolkit "' + $d + '"'))
  } else {
    $lines = @(('      (no x4lock in ' + $d + ': clear the read-only attribute on the file(s) above by hand --'),
               '       attrib -R on Windows, chmod u+w elsewhere -- then re-run this command)')
  }
  foreach ($l in $lines) { if ($Red) { Write-Host $l -ForegroundColor Red } else { Write-Host $l } }
}

function Test-CodexHooksPrecheck($dest) {
  if (-not (Test-CodexSelected)) { return }
  if (-not (Test-CodexRootSafe $dest)) {
    throw 'Codex hooks cannot safely quote this destination. Choose a root without shell expansion characters, quotes or line breaks. Nothing has been changed.'
  }
  $f = Get-CodexHooksJsonPath $dest
  if (-not (Test-Path -LiteralPath $f)) { return }
  $ro = $false
  try { $ro = (Get-Item -LiteralPath $f -Force).IsReadOnly } catch { $ro = $false }
  if (-not (Test-Path -LiteralPath (Get-CodexTemplatePath))) { return }
  $new = Get-CodexHooksJson $dest
  if ($null -eq $new) { return }
  if (Test-CodexMatchesTemplate $dest $new) { return }
  if (-not $ro) {
    if (Test-CodexShippedRendering $dest) { return }   # an older SHIPPED rendering: ours to replace
    Write-Host 'REFUSING: custom Codex hook definitions need a reviewed upgrade; -OverExisting cannot replace them.' -ForegroundColor Red
    Write-Host '  Use tools/agent-harness.py upgrade-stage. Nothing has been changed.'
    exit 1
  }
  Write-Host ''
  Write-Host 'REFUSING: the Codex hook definitions must change, and the file is READ-ONLY.'
  Write-Host "      $f"
  Write-Host '  This is x4lock doing its job. Nothing has been changed. Unlock, re-run, lock:'
  Write-HX4LockSteps $dest $f
  exit 1
}

function Write-CodexHooksJson($dest) {
  if (-not (Test-CodexSelected)) { return }
  $f = Get-CodexHooksJsonPath $dest
  Refuse-IfDryRun 'rendering the Codex hook definitions into' $f
  if (-not (Test-Path -LiteralPath (Get-CodexTemplatePath))) {
    $script:failed += ('.codex/hooks.json (the source has no ' + $X4CodexHooksTmpl + ", so Codex's hooks were NOT installed)")
    return
  }
  $new = Get-CodexHooksJson $dest
  if ($null -eq $new) {
    $script:failed += ('.codex/hooks.json (could not render it for ' + $dest + ': a path containing a quote or a newline cannot be written into it)')
    return
  }
  if (Test-CodexMatchesTemplate $dest $new) {
    Write-Host "  [note] $f already matches; left untouched, so its Codex review still holds"
    return
  }
  New-Item -ItemType Directory -Force -Path (Join-Path $dest '.codex') | Out-Null
  $tmp = "$f.tmp$PID"
  try {
    Write-Utf8NoBom $tmp ($new + "`n")
    Move-Item -Force -LiteralPath $tmp -Destination $f -ErrorAction Stop
  } catch {
    Remove-Item -Force -LiteralPath $tmp -ErrorAction SilentlyContinue
    $script:failed += ('.codex/hooks.json (could not write ' + $f + ': ' + $_.Exception.Message + ')')
    return
  }
  $script:X4CodexHooksWritten = $true
  Write-Host "  wrote $f"
}

# --- the OpenCode permission deny rules (lane L, BEST EFFORT) -------------------------
#: The mirror of write_opencode_config in install.sh: the toolkit's own renderer writes the
#: per-machine .opencode\opencode.jsonc (deny rules for THIS install's roots, from the
#: guards' own loader), never over a file without its banner, every failure NAMED.
$X4OpenCodeNote = 'OpenCode:  BEST EFFORT, CLI only, from docs, not measured. The OpenCode desktop app is NOT supported (its plugin hooks never fire, anomalyco/opencode#38604).'
function Test-OpenCodeSelected { return ((Test-ItemSelected '.opencode') -and (Test-Path -LiteralPath (Join-Path $SRC '.opencode') -PathType Container)) }

#: The first Python >= 3.10 (X4_PYTHON, then py -3 on Windows, python3, python; with
#: X4_NO_PYTHON_FALLBACK=1 only X4_PYTHON) -- the plugin's own order. $null when none.
function Find-OcPython {
  $cands = @()
  if ($env:X4_PYTHON) { $cands += , @($env:X4_PYTHON) }
  if ($env:X4_NO_PYTHON_FALLBACK -ne '1') {
    if ($X4OnWindows) { $cands += , @('py', '-3') }
    $cands += , @('python3'); $cands += , @('python')
  }
  foreach ($c in $cands) {
    $cmd = Get-Command $c[0] -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) { continue }
    $rest = @($c | Select-Object -Skip 1)
    $r = Invoke-X4Native $cmd.Source (@($rest) + @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'))
    if ($r.Rc -eq 0) { return , (@($cmd.Source) + $rest) }
  }
  return $null
}

# --- R6-04 (v4.0.0 review): the OS protection on reference/ is PRINTED, never applied -----
#: The twin of install.sh's print_refguard_step: Layer 2 (scripts/x4refguard.py) is an ACL
#: change, so it is never applied for the user; when x4refguard reports reference/ present and
#: not protected, the step is named. Every state is stated (C4 below), none silently.
#: C4 (install red-team 2026-10-04): the summary ALWAYS states the protection.
#: FX-B2: a state x4refguard could not give (no parseable answer) is said as such, never as
#: "no reference/ tree yet".
#: R2-b (second install red-team): a HUMAN reads this, so plain `apply` -- it shows the folder
#: and a count and ASKS (B3). `--yes` is for a non-interactive caller (an agent, after you agreed).
$X4RefguardStepCmd = 'python scripts/x4refguard.py apply'
function Write-RefguardStep($tk) {
  $script = Join-Path (Join-Path $tk 'scripts') 'x4refguard.py'
  if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { return }
  $state = ''
  $sentinel = ''
  $py = Find-OcPython
  if ($py) {
    $prevTk = $env:X4_TOOLKIT
    $env:X4_TOOLKIT = $tk
    Push-Location -LiteralPath $tk
    try {
      $rest = @($py | Select-Object -Skip 1)
      $out = (Invoke-X4Native $py[0] (@($rest) + @('scripts/x4refguard.py', 'status', '--json'))).Out
      $m = [regex]::Match([string]$out, '"state": "([a-z]*)"')
      # FX-B2: no parseable state is a NON-ANSWER -- never "no reference/ tree yet".
      $state = if ($m.Success) { $m.Groups[1].Value } else { 'noanswer' }
      $ms = [regex]::Match([string]$out, '"sentinel": ([a-z]*)')
      if ($ms.Success) { $sentinel = $ms.Groups[1].Value }
    } finally {
      Pop-Location
      $env:X4_TOOLKIT = $prevTk
    }
  } else {
    $ref = if ($Reference) { $Reference } else { Join-Path $tk 'reference' }
    if (Test-Path -LiteralPath $ref -PathType Container) { $state = 'unknown (no Python >= 3.10 to ask x4refguard)' }
  }
  Write-Host ''
  if ($state -ceq 'protected') {
    Write-Host 'Reference: reference/ carries the OS-level deny-delete protection (x4refguard: protected).'
  } elseif ($state -ceq '' -or $state -ceq 'unconfigured') {
    Write-Host 'Reference: no reference/ tree yet, so nothing is OS-protected. The protection is not applied by the installer:'
    Write-Host '           bin/unpack-reference.sh applies it to the tree it unpacks (see Next below).'
  } elseif ($state -ceq 'unsupported') {
    Write-Host 'Reference: this platform has no OS-level protection mechanism (a disclosed gap; not applied by the installer).'
  } elseif ($state -ceq 'noanswer') {
    Write-Host 'Reference: the OS-level protection state could NOT be read (x4refguard gave no answer). Check it, in'
    Write-Host ('           ' + $tk + ':  python scripts/x4refguard.py status')
  } elseif ($sentinel -ceq 'false') {
    # R2-a: apply REFUSES a tree without the sentinel, so naming it here was a dead end.
    Write-Host 'Reference: reference/ exists but has no .unpacked-and-locked sentinel, i.e. it is not a finished unpack,'
    Write-Host '           so it is not OS-protected and cannot be yet (see Next below).'
  } else {
    $shown = if ($state -ceq 'absent') { 'not applied' } else { $state }
    Write-Host ('Reference: reference/ exists and is NOT OS-protected (x4refguard: ' + $shown + ').')
    Write-Host '           It is not applied by the installer (a permission change is yours to make). To add the'
    Write-Host ('           OS-level deny-delete layer (any process, hooks or not), run in ' + $tk + ':')
    Write-Host ('             ' + $X4RefguardStepCmd)
    Write-Host '           (it shows the folder and a file count, then asks you; --yes answers for you, e.g. when'
    Write-Host '           an agent runs it after you agreed)'
  }
}

#: C5 (install red-team 2026-10-04): the twin of install.sh's Next line -- true for THIS install.
#: The unpack runs through Git Bash: from PowerShell a bare `bash` is the WSL stub (B1).
function Write-NextStep($tk) {
  $bashCmd = if ($env:X4_BASH) { '& "' + $env:X4_BASH + '"' } else { '& "<Git Bash>"' }
  $unpack = 'cd "' + $tk + '"; ' + $bashCmd + ' bin/unpack-reference.sh'
  $ref = if ($Reference) { $Reference } else { Join-Path $tk 'reference' }
  if (-not $Game) {
    Write-Host ('Next:      X4_GAME is blank -- set it in ' + (Join-Path $tk 'x4-paths.env') + ', then  ' + $unpack + '  to build reference/.')
  } elseif (Test-Path -LiteralPath (Join-Path $ref '.unpacked-and-locked') -PathType Leaf) {
    Write-Host ('Next:      reference/ is already unpacked; open your agent in "' + $tk + '" and paste SETUP_PROMPT.txt.')
  } elseif ((Test-Path -LiteralPath $ref -PathType Container) -and @(Get-ChildItem -LiteralPath $ref -Force -ErrorAction SilentlyContinue).Count -gt 0) {
    # R2 cosmetic: reference/ exists, but without .unpacked-and-locked nothing says it is finished.
    Write-Host ('Next:      reference/ exists but has no .unpacked-and-locked (not a finished unpack): ' + $unpack + '  to unpack it; if it IS a complete unpack you made yourself, python scripts/x4refguard.py apply  prints how to mark it.')
  } else {
    Write-Host ('Next:      ' + $unpack + '  to build reference/ from your game.')
  }
}

#: The twin of install.sh's precheck_opencode_config (v4.0.0 review R4-5): a READ-ONLY
#: .opencode/opencode.jsonc that would have to change refuses before anything is written.
function Test-OpenCodeConfigPrecheck($dest) {
  if (-not (Test-OpenCodeSelected)) { return }
  $f = Join-Path (Join-Path $dest '.opencode') 'opencode.jsonc'
  if (-not (Test-Path -LiteralPath $f)) { return }
  $ro = $false
  try { $ro = (Get-Item -LiteralPath $f -Force).IsReadOnly } catch { $ro = $false }
  if (-not $ro) { return }
  $renderer = Join-Path (Join-Path (Join-Path $SRC '.opencode') 'hooks') 'opencode_config.py'
  if (-not (Test-Path -LiteralPath $renderer -PathType Leaf)) { return }
  $py = Find-OcPython
  if (-not $py) { return }
  $prev = $env:X4_TOOLKIT
  $env:X4_TOOLKIT = $dest
  try {
    $rest = @($py | Select-Object -Skip 1)
    $fresh = ((Invoke-X4Native $py[0] (@($rest) + @($renderer, 'check', '--root', $dest))).Rc -eq 0)
  } finally { $env:X4_TOOLKIT = $prev }
  if ($fresh) { return }
  Write-Host ''
  Write-Host 'REFUSING: the OpenCode deny rules must change, and the file is READ-ONLY.'
  Write-Host "      $f"
  Write-Host '  This is x4lock doing its job. Nothing has been changed. Unlock, re-run, lock:'
  Write-HX4LockSteps $dest $f
  exit 1
}

function Write-OpenCodeConfig($dest) {
  if (-not (Test-OpenCodeSelected)) { return }
  $cfg = Join-Path (Join-Path $dest '.opencode') 'opencode.jsonc'
  Refuse-IfDryRun 'rendering the OpenCode deny rules into' $cfg
  $renderer = Join-Path (Join-Path (Join-Path $dest '.opencode') 'hooks') 'opencode_config.py'
  if (-not (Test-Path -LiteralPath $renderer -PathType Leaf)) {
    $script:failed += '.opencode/opencode.jsonc (the permission deny layer was NOT installed: .opencode/hooks/opencode_config.py is missing)'
    return
  }
  $py = Find-OcPython
  if (-not $py) {
    $script:failed += ('.opencode/opencode.jsonc (the permission deny layer was NOT installed: no Python >= 3.10; set X4_PYTHON and run: python .opencode/hooks/opencode_config.py write --root "' + $dest + '")')
    return
  }
  # X4_TOOLKIT=DEST: the roots of THIS install, as its guards will see them once
  # X4_TOOLKIT points here -- never a previous toolkit's that the caller still exports.
  $prevTk = $env:X4_TOOLKIT
  $env:X4_TOOLKIT = $dest
  try {
    $rest = @($py | Select-Object -Skip 1)
    $r = Invoke-X4Native $py[0] (@($rest) + @($renderer, 'write', '--root', $dest)) -MergeStderr
    $out = ([string]$r.Out).Trim(); $rc = $r.Rc
  } finally {
    $env:X4_TOOLKIT = $prevTk
  }
  if ($rc -ne 0) {
    $why = ($out -replace "[`r`n]+", ' ')
    if ($why.Length -gt 300) { $why = $why.Substring(0, 300) }
    $script:failed += ('.opencode/opencode.jsonc (the permission deny layer was NOT installed: ' + $why + '; fix it, then run: python .opencode/hooks/opencode_config.py write --root "' + $dest + '")')
    return
  }
  Write-Host "  $out"
}

#: Destination files the copy would overwrite that CANNOT be written.
#:
#: MEASURED 2026-09-07 with one locked hook in the destination: this installer
#: returned 0 and OVERWROTE it, because Copy-Item -Force CLEARS the read-only
#: attribute -- so it destroyed exactly what the lock was protecting and reported
#: success. install.sh died mid-copy instead. Opposite verdicts on one input, and
#: nothing decided which was right; refusing before either writes is the only
#: answer correct for both.
function Get-DestinationProblem($dest, $rel, $problem) {
  if ($problem -eq 'locked') {
    $target = Join-Path $dest $rel
    $entry = Get-Item -Force -LiteralPath $target -ErrorAction SilentlyContinue
    if ($entry -and $entry.IsReadOnly) { return $target }
    return
  }
  $path = $dest
  foreach ($part in ($rel -split '[/\\]')) {
    $path = Join-Path $path $part
    $entry = Get-Item -Force -LiteralPath $path -ErrorAction SilentlyContinue
    if ($entry -and ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $path }
  }
}

function Get-LockedTargets($dest, $problem = 'locked') {
  $blocked = @()
  # THE SAME SET THE COPY WRITES. From a git checkout that is the tracked set; a
  # locked destination file where an UNTRACKED source file sits is never written.
  if (Get-TrackedCopySet) {
    foreach ($rel in $script:X4Tracked) {
      $issue = Get-DestinationProblem $dest $rel $problem
      if ($issue) { $blocked += $issue }
    }
    return ,$blocked
  }
  foreach ($item in $X4Items) {
    $from = Join-Path $SRC $item
    if (-not (Test-Path -LiteralPath $from)) { continue }
    if (Test-Path -LiteralPath $from -PathType Container) {
      foreach ($f in (Get-ChildItem -LiteralPath $from -Recurse -File -ErrorAction SilentlyContinue)) {
        $rel = $f.FullName.Substring($from.Length).TrimStart([char]92, [char]47)
        $skip = $false
        foreach ($junk in ($X4CopyPrune + $X4KeepLocal)) {
          $probe = Join-Path $item $rel
          if ($probe -ieq $junk -or $probe -like ($junk + [char]92 + '*')) { $skip = $true }
        }
        if ($skip) { continue }
        $issue = Get-DestinationProblem $dest (Join-Path $item $rel) $problem
        if ($issue) { $blocked += $issue }
      }
    } else {
      $issue = Get-DestinationProblem $dest $item $problem
      if ($issue) { $blocked += $issue }
    }
  }
  return ,$blocked
}

function Test-LockedTargetsPrecheck($dest) {
  $redirected = Get-LockedTargets $dest 'redirected'
  if ($redirected -and $redirected.Count -gt 0) {
    Write-Host 'REFUSING: destination links redirect toolkit writes. Nothing has been changed.'
    foreach ($path in ($redirected | Sort-Object -Unique | Select-Object -First 8)) { Write-Host $path }
    throw 'Review the linked paths and select an independent destination before installing.'
  }
  $blocked = Get-LockedTargets $dest
  if (-not $blocked -or $blocked.Count -eq 0) { return }
  Write-Host ''
  Write-Host ("REFUSING: " + $blocked.Count + " file(s) this install would overwrite are READ-ONLY.")
  foreach ($b in ($blocked | Select-Object -First 8)) { Write-Host ("      " + $b) }
  if ($blocked.Count -gt 8) { Write-Host ("      ... and " + ($blocked.Count - 8) + " more NOT LISTED") }
  Write-Host ''
  Write-Host '  This is x4lock doing its job -- README tells you to run it, and it'
  Write-Host '  cannot tell an installer from any other process that writes here.'
  Write-Host '  Nothing has been changed.'
  Write-Host ''
  Write-Host '  Unlock, re-run this installer, then lock again:'
  Write-HX4LockSteps $dest
  exit 1
}

#: Get-IAssignLines FILE -> the comparable content of a path config (Plan 3 lane I): its
#: KEY=value lines with CR and indent stripped, comments and blanks dropped, sorted ordinally.
#: The mirror of install.sh's _i_assign_lines and of _paths._assignments.
function Get-IAssignLines($file) {
  $text = ''
  try { $text = [IO.File]::ReadAllText($file) } catch { return @() }
  $out = New-Object System.Collections.Generic.List[string]
  foreach ($ln in ($text -split "`n")) {
    $l = $ln.Replace("`r", '').TrimStart(' ', "`t")
    if ($l -and -not $l.StartsWith('#') -and $l.Contains('=')) { $out.Add($l) }
  }
  $arr = $out.ToArray()
  [Array]::Sort($arr, [StringComparer]::Ordinal)
  return ,$arr
}

#: Get-IDifferingKeys A B -> the KEY NAMES whose assignments differ; never a value.
function Get-IDifferingKeys($a, $b) {
  $la = @(Get-IAssignLines $a); $lb = @(Get-IAssignLines $b)
  $keys = @{}
  foreach ($l in ($la + $lb)) {
    if ((@($la) -ccontains $l) -and (@($lb) -ccontains $l)) { continue }
    $k = ($l -split '=', 2)[0].Trim()
    if ($k.StartsWith('export ')) { $k = $k.Substring(7).Trim() }
    $keys[$k] = 1
  }
  $names = @($keys.Keys)
  [Array]::Sort($names, [StringComparer]::Ordinal)
  return ($names -join ' ')
}

function New-ConfigBackup([string]$source) {
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  while ($true) {
    $backup = $source + '.bak-' + $stamp + '.' + [guid]::NewGuid().ToString('N')
    try {
      # CreateNew reserves atomically; a UUID alone does not prevent clobbering.
      $output = [IO.File]::Open($backup, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
      break
    } catch {
      $cause = $_.Exception
      while ($cause.InnerException) { $cause = $cause.InnerException }
      if (($cause -is [IO.IOException]) -and
          (($cause.HResult -band 65535) -in @(80,183)) -and (Test-Path -LiteralPath $backup)) { continue }
      throw
    }
  }
  try {
    $inputStream = $null
    try {
      $inputStream = [IO.File]::OpenRead($source)
      $inputStream.CopyTo($output)
    } finally {
      if ($inputStream) { $inputStream.Dispose() }
      $output.Dispose()
    }
    # .NET is available in both 5.1 and 7; Get-FileHash need not be installed.
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
      $expected = [Convert]::ToBase64String($algorithm.ComputeHash([IO.File]::ReadAllBytes($source)))
      $actual = [Convert]::ToBase64String($algorithm.ComputeHash([IO.File]::ReadAllBytes($backup)))
    } finally { $algorithm.Dispose() }
    if ($expected -ne $actual) {
      throw 'backup read-back disagrees with the source'
    }
  } catch {
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    throw
  }
  return $backup
}

function Test-IConfigsAgree($a, $b) {
  return (((Get-IAssignLines $a) -join "`n") -ceq ((Get-IAssignLines $b) -join "`n"))
}

#: Move-ILegacyPathsEnv TOOLKIT -- MOVE a 3.x .claude\x4-paths.env to <toolkit>\x4-paths.env.
#: The mirror of install.sh's _i_migrate_paths_env: a rename, never a copy; a read-only
#: (x4lock) file is renamed too and stays read-only (MEASURED, Plan 3 lane I M6); agreeing
#: copies retire the 3.x one to .bak-<stamp>; differing copies were refused by the precheck.
function Move-ILegacyPathsEnv($t) {
  $new = Join-Path $t 'x4-paths.env'
  $old = Join-Path $t (Join-Path '.claude' 'x4-paths.env')
  if (-not (Test-Path -LiteralPath $old -PathType Leaf)) { return }
  if (-not (Test-Path -LiteralPath $new)) {
    try { Move-Item -LiteralPath $old -Destination $new -ErrorAction Stop } catch {
      Write-Host ("ERROR: could not move " + $old + " -> " + $new + ": " + $_.Exception.Message) -ForegroundColor Red
      Write-Host '       It is still where it was.' -ForegroundColor Red
      exit 1
    }
    Write-Host ("  [migrated] " + $old + " -> " + $new + " (the 3.x location; 4.0 reads the toolkit root). To undo: move it back.")
    return
  }
  if (Test-IConfigsAgree $new $old) {
    try {
      while ($true) {
        $bak = $old + '.bak-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.' + [guid]::NewGuid().ToString('N')
        try {
          # The two-argument .NET move refuses replacement and retains a locked
          # legacy file's attributes; copying then deleting cannot retire read-only files.
          [IO.File]::Move($old, $bak)
          break
        } catch {
          $cause = $_.Exception
          while ($cause.InnerException) { $cause = $cause.InnerException }
          if (($cause -is [IO.IOException]) -and (($cause.HResult -band 65535) -in @(80,183))) { continue }
          throw
        }
      }
    } catch {
      Write-Host ("ERROR: could not rename the agreeing 3.x " + $old + ": " + $_.Exception.Message) -ForegroundColor Red
      exit 1
    }
    Write-Host ("  [migrated] the 3.x " + $old + " agreed with " + $new + "; renamed it to " + $bak + " (to undo: rename it back)")
    return
  }
  Write-Host ("ERROR: " + $new + " and the 3.x " + $old + " DIFFER on: " + (Get-IDifferingKeys $new $old) + " -- refusing to choose.") -ForegroundColor Red
  exit 1
}

function Test-ConfigPrecheck($t) {
  $f = Join-Path $t 'x4-paths.env'
  $old = Join-Path $t (Join-Path '.claude' 'x4-paths.env')
  # TWO CONFIGS THAT DIFFER (Plan 3 lane I): refused before anything is written, -DryRun too.
  if ((Test-Path -LiteralPath $f -PathType Leaf) -and (Test-Path -LiteralPath $old -PathType Leaf) -and
      -not (Test-IConfigsAgree $f $old)) {
    Write-Host ''
    Write-Host 'REFUSING: two path configs that DIFFER:'
    Write-Host "      $f"
    Write-Host "      $old   (the 3.x location)"
    Write-Host ('  They differ on: ' + (Get-IDifferingKeys $f $old))
    Write-Host '  Nothing has been changed. Keep the values you want in the first, delete'
    Write-Host '  or rename the second, then re-run. To see them side by side:'
    # --root, and the SOURCE's copy: without --root `status` reports X4_TOOLKIT's (or the
    # cwd's) config -- possibly another toolkit (v4.0.0 review R4-11).
    Write-Host ('      python "' + (Join-Path $SRC (Join-Path 'scripts' 'x4config.py')) + '" status --root "' + $t + '"')
    exit 1
  }
  if ($DryRun -and (Test-Path -LiteralPath $old -PathType Leaf)) {
    if (Test-Path -LiteralPath $f) { Write-Host ("  -DryRun: would RENAME the agreeing 3.x " + $old + " to " + $old + ".bak-<stamp>") }
    else { Write-Host ("  -DryRun: would MOVE " + $old + " -> " + $f + " (the 3.x location; 4.0 reads the toolkit root)") }
  }
  if (-not (Test-Path -LiteralPath $f)) { $f = $old }    # judged where it IS: the move keeps its bytes
  if (-not (Test-Path -LiteralPath $f)) { return }
  $now = (Get-OwnedEnvLinesFromFile $f) -join "`n"
  if ($now -eq '__X4_UNREADABLE__') {
    Write-Host "REFUSING: $f exists but cannot be READ, so this run cannot tell whether"
    Write-Host '      your paths would change. Something is holding it open -- an editor,'
    Write-Host '      an AV scanner, or a sync client. Nothing has been changed.'
    exit 1
  }
  $new = (Get-OwnedEnvLines $t) -join "`n"
  if ($now -eq $new) { return }
  $ro = $false
  try { $ro = (Get-Item -LiteralPath $f).IsReadOnly } catch { $ro = $false }
  if (-not $ro) { return }
  Write-Host ''
  Write-Host 'REFUSING: your path config must change, and it is READ-ONLY.'
  Write-Host "      $f"
  Write-Host ''
  Write-Host '  This is x4lock doing its job -- README tells you to run it, and it'
  Write-Host '  cannot tell an installer from any other process that writes here.'
  Write-Host '  Nothing has been changed.'
  Write-Host ''
  Write-Host '  Unlock, re-run this installer, then lock again:'
  Write-HX4LockSteps $t
  Write-Host ''
  Write-Host '  (An upgrade that does NOT change your paths does not need this: it'
  Write-Host '   leaves the config untouched and the lock never applies.)'
  exit 1
}

function Copy-Excluding([string]$rel, [string]$dest) {
  foreach ($junk in ($X4CopyPrune + $X4KeepLocal)) { if ($junk -ieq $rel) { return } }
  # $fromPath, NOT $src. PowerShell variable names are CASE-INSENSITIVE, so a local
  # `$src` IS the script-scope `$SRC` -- assigning it overwrote the source root, and
  # the first recursion then built `src\tools\tools\basex` and died with
  # "Cannot find path". In bash those are two different variables; here they are one.
  $fromPath = Join-Path $SRC $rel
  $needsWalk = $false
  foreach ($junk in ($X4CopyPrune + $X4KeepLocal)) {
    if ($junk -like ($rel + [char]92 + '*') -or $junk -like ($rel + '/*')) {
      $needsWalk = $true
    }
  }
  # AND EVERY SIBLING of a keep-local file -- .bak-<stamp> and .tmp<pid> carry
  # X4_NEXUS_KEY and travelled because this matched two literal names. The
  # .example templates SHIP, so they are re-included, exactly as .gitignore
  # negates them.
  foreach ($junk in $X4KeepLocal) {
    if ($rel -ieq ($junk + '.example')) { continue }
    if ($rel -ilike ($junk + '.*')) { return }
  }
  $isDir = Test-Path -LiteralPath $fromPath -PathType Container
  $target = Join-Path $dest $rel
  if (-not $isDir -or -not $needsWalk) {
    $parent = Split-Path -Parent $target
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    # NO Remove-Item on the target first. A draft cleared it to avoid nesting and
    # thereby deleted the .bak files Copy-Toolkit had just written into $dest\.claude
    # -- 11 of 17 harness cases red, including "a backup was made". This is the exact
    # form the original loop used, and the upgrade case proves it merges rather than
    # nesting.
    Copy-Item -Recurse -Force -LiteralPath $fromPath -Destination $parent
    return
  }
  New-Item -ItemType Directory -Force -Path $target | Out-Null
  foreach ($c in (Get-ChildItem -Force -LiteralPath $fromPath)) {
    Copy-Excluding ($rel + [char]92 + $c.Name) $dest
  }
}

function Get-X4RawSha256($path) {
  $stream = [IO.File]::OpenRead($path)
  $sha = [Security.Cryptography.SHA256]::Create()
  try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
  finally { $sha.Dispose(); $stream.Dispose() }
}

function Copy-Toolkit($dest) {
  Refuse-IfDryRun 'copying the toolkit into' $dest
  Test-ProjectSettingsPrecheck $dest
  New-Item -ItemType Directory -Force -Path (Join-Path $dest '.claude') | Out-Null
  $notes = Join-Path $dest 'KNOWLEDGEBASE.md'
  $reference = Join-Path $SRC 'KNOWLEDGEBASE.md'
  # An UNEDITED KB some release shipped is ours to replace (release review H6; install.sh's
  # copy_toolkit): only a KB that is neither the source's nor a shipped one can hold notes.
  if ((Test-Path -LiteralPath $notes -PathType Leaf) -and
      (Get-X4RawSha256 $notes) -ne (Get-X4RawSha256 $reference) -and
      -not (Test-HIsShipped $dest 'KNOWLEDGEBASE.md')) {
    $py = Find-OcPython
    if (-not $py) { throw 'Preserving the existing knowledgebase needs Python >= 3.10; no toolkit files were copied.' }
    $extra = @($py | Select-Object -Skip 1)
    $saved = Invoke-X4Native $py[0] (@($extra) + @((Join-Path $SRC 'tools/agent-harness.py'), 'notes-preserve',
                                                 '--destination', $dest, '--reference', $reference))
    if ($saved.Rc -ne 0) { throw 'Legacy notes preservation failed; no toolkit files were copied.' }
    Write-Host $saved.Out
  }

  # BACK UP BEFORE THE COPY, and that ordering IS the fix. The Copy-Item loop below
  # overwrites .claude wholesale, so anything saved afterwards is the SOURCE machine's
  # file being reported as the user's. MEASURED on the bash side against a source that
  # had itself been set up -- the destination's X4_NEXUS_KEY was gone from every file
  # while the run printed "kept your existing".
  #
  # This side had no backup at all: it ended with a bare Remove-Item of both files, no
  # copy, no message, exit 0. Windows is the platform this toolkit targets, and
  # install.ps1 is the README's own Windows command.
  # THE BACKUP/RESTORE ROUND TRIP IS GONE, not repaired -- the mirror of the same
  # removal in install.sh. These two files are in $X4KeepLocal, so the copy never
  # touches them, and a file that is never overwritten needs no backup and no
  # restore. MEASURED before the change: this left TWO orphaned .bak files and the
  # restore itself failed on a read-only config, after the copy had succeeded.

  # 'mods' carries the game extension x4live needs (README: "copy that folder into
  # {game}/extensions/"). Omitting it shipped a documented instruction pointing at a
  # directory the installer never created.
  $items = $X4Items
  # -LiteralPath throughout. Without it PowerShell treats [ and ] as WILDCARDS, so a
  # source folder named e.g. "x4-claude-toolkit [v3.0.0]" -- the shape a download gives
  # you -- matches nothing. MEASURED 2026-09-01: bare Test-Path returned False and bare
  # Copy-Item copied 0 files, while -LiteralPath copied all of them. The installer then
  # printed "install complete" over an empty destination, and the README says "extract
  # it anywhere".
  # PRUNE THE DESTINATION FIRST. The same list runs after the copy loop, and running
  # it ONLY after is what broke the bash installer on the documented upgrade path:
  # uv hardlinks package files from a shared cache, so once both trees have been synced
  # the same file has the same inode in each and a recursive copy refuses. A cleanup
  # that only runs after the copy cannot make the copy possible. Kept in step with
  # install.sh deliberately -- one installer correct by accident is how the two drift.
  foreach ($junk in $X4CopyPrune) {
    $p = Join-Path $dest $junk
    if (Test-Path -LiteralPath $p) { Remove-Item -Recurse -Force -LiteralPath $p -ErrorAction SilentlyContinue }
  }

  $tracked = Get-TrackedCopySet
  if ($tracked) {
    # ANNOUNCED: a step that narrows the data says so.
    Write-Host ("  source is a git checkout: copying the " + $script:X4Tracked.Count + " file(s) git tracks in the copy set;")
    Write-Host "  untracked and ignored files (built databases, caches, scratch output) stay behind."
  } elseif ($script:X4TrackedWhy) {
    Write-Host ("  [note] not copying git's tracked set: " + $script:X4TrackedWhy + " -- copying what is on disk.")
    if ($script:X4TrackedAbsent -gt 0) {
      Write-Host ("  [note] " + $script:X4TrackedAbsent + " tracked file(s) are deleted in the source working tree, so not copied")
    }
  }
  $missing = @()
  foreach ($i in $items) {
    $s = Join-Path $SRC $i
    if (Test-Path -LiteralPath $s) {
      if (-not $tracked) { Copy-Excluding $i $dest }
    } else {
      # NAMED, never silently skipped: that silence is how the absent mods/ folder
      # survived a whole release.
      $missing += $i
    }
  }
  if ($tracked) { Copy-TrackedSet $dest }
  if ($missing.Count) {
    Write-Host ("  [note] not in the source, so not copied: " + ($missing -join ", "))
  }

  # A VIRTUALENV MUST NOT TRAVEL. uv hardlinks package files from a shared cache, so
  # once both trees have been synced independently the same file has the same inode in
  # each -- and a recursive copy then fails with "are the same file", 1,334 times, part
  # way through. pyvenv.cfg and the Scripts shims also embed absolute paths, so a copied
  # venv points back at the source. setup.sh recreates it anyway.
  # ...and again afterwards, so the SOURCE machine's virtualenv does not linger here.
  foreach ($junk in $X4CopyPrune) {
    $p = Join-Path $dest $junk
    if (Test-Path -LiteralPath $p) { Remove-Item -Recurse -Force -LiteralPath $p -ErrorAction SilentlyContinue }
  }

  # PUT THE USER'S BACK. Whatever the copy just landed came from the SOURCE machine --
  # its paths, its Nexus key -- and has no business on this one. PRESERVED, not merely
  # archived: setup.sh only recreates settings.local.json when it is ABSENT, so keeping
  # it here is what makes an upgrade non-destructive rather than merely recoverable.
}

# MOVED ABOVE THE DISPATCH. PowerShell defines a function when execution REACHES
# it, so this sitting below `switch ($Method)` meant Write-PathsEnv's
# can-bash-source-this check called a name that did not exist yet and the install
# died with CommandNotFoundException after the copy.
# Strip a trailing path separator. `-Toolkit "$SRC\"` is not -eq to
# $SRC, so the copy branch is taken and the tree is copied onto itself. The bash
# twin of this was MEASURED failing with 'are the same file' and writing no
# config at all. A drive root keeps its separator.
# Are these the SAME directory? Asked canonically, never as a string -- the bash
# twin of this compared an MSYS spelling against a Windows one and copied the tree
# onto itself. Resolve-Path settles dialect, trailing separators and relative
# segments; a destination that does not exist yet cannot be the source.
function Test-SameDir([string]$a, [string]$b) {
  if (-not $a -or -not $b) { return $false }
  try {
    $ra = (Resolve-Path -LiteralPath $a -ErrorAction Stop).ProviderPath
    $rb = (Resolve-Path -LiteralPath $b -ErrorAction Stop).ProviderPath
  } catch { return $false }
  return ([System.IO.Path]::GetFullPath($ra).TrimEnd([char]92, [char]47) -ieq
          [System.IO.Path]::GetFullPath($rb).TrimEnd([char]92, [char]47))
}

function Remove-TrailingSep([string]$p) {
  if (-not $p) { return $p }
  while ($p.Length -gt 1 -and ($p.EndsWith('/') -or $p.EndsWith([char]92))) {
    if ($p.Length -eq 3 -and $p[1] -eq ':') { break }   # C:/ or C:\
    $p = $p.Substring(0, $p.Length - 1)
  }
  return $p
}

function Find-GitBash {
  $cands = @()
  # `if ($base)` below skips a null base correctly -- but the ARRAY IS BUILT FIRST, so
  # `Join-Path $env:LOCALAPPDATA 'Programs'` was evaluated before that guard could run.
  # LOCALAPPDATA is Windows-only, so under PowerShell on Linux/macOS this threw
  # "Cannot bind argument to parameter 'Path' because it is null" -- out of Find-GitBash,
  # which EVERY method calls. install.ps1 was 100% dead on POSIX for -Method separate
  # and -Method global alike. The guard was one level too late: it protected the loop
  # variable, not the expression that produced it.
  $localPrograms = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Programs' } else { $null }
  foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $localPrograms)) {
    if ($base) { $cands += (Join-Path $base 'Git\bin\bash.exe') }
  }
  foreach ($c in $cands) { if (Test-Path -LiteralPath $c) { return (Get-Command $c) } }
  # Fall back to PATH, but never to a WSL stub.
  foreach ($c in @(Get-Command bash -All -ErrorAction SilentlyContinue)) {
    $p = $c.Source
    if ($p -and $p -notmatch '\\(System32|SysWOW64|WindowsApps)\\') { return $c }
  }
  return $null
}

function Get-EscapedEnvValue($v) {
  # The value lands inside a bash DOUBLE-QUOTED string, so a trailing separator is not
  # cosmetic: X4_TOOLKIT="C:\path\" never closes its quote, `set -a; . "$cfg"` aborts on
  # it, and EVERY X4_* comes out unset while this installer reports success. Windows
  # tab-completion appends that backslash, and a drive root IS one.
  # MEASURED: with a trailing separator the sourced config yielded X4_GAME, X4_REFERENCE
  # and X4_EXTENSIONS all UNSET, exit 0, "=== install complete ===".
  if ($null -eq $v) { return '' }
  $v = ([string]$v).TrimEnd('/','\')
  $v = $v.Replace('\','\\')      # backslashes FIRST, or we double the ones added below
  $v = $v.Replace('"','\"')
  $v = $v.Replace('$','\$')
  $v = $v.Replace('`','\`')
  return $v
}

function Write-PathsEnv($t) {
  # THE GATE COMES FIRST. `New-Item -Force` CREATES the directory, so a -DryRun run
  # made a directory and then printed "nothing was changed". install.sh gets the order
  # right (refuse_if_dry_run, then mkdir). Reachable via `-Method global -Toolkit <new
  # dir> -DryRun` and via `-Method in-game -DryRun` when Test-SameDir is true.
  #
  # `test_installers_agree.py::test_every_powershell_WRITER_consults_the_dry_run_flag`
  # asserts `ps.count("Refuse-IfDryRun '") >= 3` -- a COUNT OF CALL SITES CANNOT SEE
  # ORDERING, which is why this survived. -DryRun is executed nowhere in CI.
  $dir = $t
  $f = Join-Path $dir 'x4-paths.env'
  # THE GATE IS THE FIRST STATEMENT, and it has to stay there. The fast path
  # below was inserted ABOVE it, which made -DryRun perform a REAL install
  # whenever the toolkit is already at the destination: no copy runs, so this is
  # the only writer reached, the fast path returned before the gate, and setup.sh
  # ran for real. The comment a few lines up already warned that a COUNT OF CALL
  # SITES CANNOT SEE ORDERING -- and the same arc then moved the gate below a
  # return anyway. A count cannot see nesting either, and neither can it see a
  # return placed in front.
  Refuse-IfDryRun 'writing the path config into' $f
  # A 3.x config MOVES to the root first (Plan 3 lane I), AFTER the gate, so the fast path
  # below then judges it in its new place -- an unchanged locked config is left untouched.
  Move-ILegacyPathsEnv $t

  # NOTHING TO CHANGE, NOTHING TO WRITE. An upgrade resolving the same paths used
  # to rewrite this file anyway, which failed a locked config for a write that
  # would have changed nothing -- and lost hand-written COMMENTS every run, since
  # only key=value lines are carried over.
  if (Test-Path -LiteralPath $f) {
    if (((Get-OwnedEnvLinesFromFile $f) -join "`n") -eq ((Get-OwnedEnvLines $t) -join "`n")) {
      Write-Host "  [note] $f already matches these paths; left untouched"
      return
    }
  }

  New-Item -ItemType Directory -Force -Path $dir | Out-Null

  # BACKED UP HERE, not at the call sites -- the placement install.sh uses, so a
  # method added later cannot write without a backup. The bash side got this fix;
  # this one did not, which made it the fourth 'fixed in bash, absent in
  # PowerShell' defect of the release.
  #
  # Less severe than the bash case was, and worth stating rather than glossing:
  # the carry-over loop below preserves every key this function does not own, so
  # X4_NEXUS_KEY and hand-added lines survive. But the OWNED keys are replaced
  # outright, and nothing else records what they used to say.
  #
  # A backup that silently did not happen is worse than none, because the message
  # would have claimed it did -- so a failure is reported, not swallowed.
  if (Test-Path -LiteralPath $f) {
    try {
      $backup = New-ConfigBackup $f
      Write-Host ('  [note] kept your previous x4-paths.env as ' + $backup)
    } catch {
      Write-Host ('  [warn] could not back up x4-paths.env: ' + $_.Exception.Message)
      Write-Host '         Refusing to replace it without a verified backup.'
      exit 1
    }
  }
  $ref = if ($Reference) { $Reference } else { Join-Path $t 'reference' }
  $ext = if ($Extensions) { $Extensions } elseif ($Game) { Join-Path $Game 'extensions' } else { '' }

  # KEYS THIS FUNCTION DOES NOT OWN ARE CARRIED OVER. setup.sh tells the user to keep
  # X4_NEXUS_KEY in this file and its own header says "edit freely", yet every upgrade
  # rebuilt it from scratch -- so the key survived only in a .bak nobody opens.
  $owned = @('X4_TOOLKIT','X4_GAME','X4_REFERENCE','X4_PROFILE','X4_DEBUGLOG','X4_MODS','X4_EXTENSIONS','XRCATTOOL')
  $carried = @()
  if (Test-Path -LiteralPath $f) {
    # GUARDED, and this one refuses rather than degrading. These are the keys this
    # function does NOT own -- X4_NEXUS_KEY among them -- so a read that fails here
    # would carry over NOTHING and silently drop the user's secret from the file it
    # is about to rewrite. A raw error is bad; quietly losing a key is worse.
    $lines1 = $null
    try { $lines1 = Get-Content -LiteralPath $f -ErrorAction Stop } catch {
      Write-Host ("ERROR: cannot read " + $f + " to carry over your own settings: " + $_.Exception.Message) -ForegroundColor Red
      Write-Host "       Refusing rather than rewriting it without them. Nothing has been changed." -ForegroundColor Red
      exit 1
    }
    foreach ($line in $lines1) {
      if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
      $key = ($line -split '=', 2)[0].Trim()
      if ($owned -notcontains $key) { $carried += $line }
    }
  }

  # ONE RENDERING, as install.sh now does. These eight lines were re-emitted
  # by hand while Get-OwnedEnvLines claimed to exist so the precondition and
  # the writer could not disagree.
  $lines = @("# Written by install.ps1 ($(Get-Date -Format s)) - edit freely. All paths overridable.")
  $lines += (Get-OwnedEnvLines $t)
  if ($carried.Count) {
    $lines += '# --- carried over from your previous x4-paths.env ---'
    $lines += $carried
  }

  # UTF-8 WITHOUT BOM, LF endings: this file is sourced by bash (set -euo pipefail),
  # and Windows PowerShell 5.1's -Encoding UTF8 writes a BOM that bash reads as a
  # command ("command not found" on line 1, exit 127 before any path resolves).
  # RENDER TO A TEMP, VERIFY THE TEMP, THEN MOVE. This wrote the LIVE config and
  # verified it afterwards, so a render that failed part way left the user with a
  # half-written file and the check below then reported a failure about something
  # it was already too late to protect. install.sh had the identical inversion.
  # Build beside, prove, then move.
  $tmp = "$f.tmp$PID"
  # GUARDED, as install.sh guards the same render. Unguarded this is a raw .NET
  # dump with no crafted message and no $failed accounting -- the surface the
  # Move-Item guard below was added to remove, one statement earlier.
  try {
    Write-Utf8NoBom $tmp (($lines -join "`n") + "`n")
  } catch {
    Remove-Item -Force -LiteralPath $tmp -ErrorAction SilentlyContinue
    Write-Host ("ERROR: could not write " + $tmp + ": " + $_.Exception.Message) -ForegroundColor Red
    Write-Host "       Your existing config is untouched." -ForegroundColor Red
    exit 1
  }

  # Parse the artifact without execution. Carried assignments are user data;
  # sourcing them would execute substitutions/operators refused by the runtime loader.
  # Skipped, loudly, only when no Git Bash is available.
  $bash = Find-GitBash
  if ($bash) {
    # Pass a plain path argument, with no embedded shell program or quote layer.
    # Relax ErrorActionPreference so syntax diagnostics reach the refusal below on 5.1 too.
    $bp = $tmp.Replace('\','/')
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
      $probe = & $bash -n -- $bp 2>&1
    } finally {
      $ErrorActionPreference = $prevEAP
    }
    if ($LASTEXITCODE -ne 0) {
      Remove-Item -Force -LiteralPath $tmp -ErrorAction SilentlyContinue
      Write-Host "ERROR: the config this run would write has invalid assignment syntax." -ForegroundColor Red
      Write-Host "       It was parsed without executing any carried settings." -ForegroundColor Red
      Write-Host "       NOTHING was changed: your existing $f is untouched." -ForegroundColor Red
      Write-Host ("       bash said: " + ($probe -join ' ')) -ForegroundColor Red
      exit 1
    }
  } else {
    Write-Host "  [warn] no bash found, so the new config syntax was NOT checked"
  }
  # GUARDED, as install.sh guards the identical `mv -f`. Unguarded this throws an
  # IOException when the destination is held open with FileShare::None -- an
  # editor, an AV scanner, OneDrive -- and the TEMP SURVIVES ON DISK holding
  # X4_NEXUS_KEY plus that machine's absolute paths, with a raw .NET error and no
  # INCOMPLETE accounting. That is the exact gap
  # test_the_config_write_leaves_no_temp_behind names in its own docstring.
  try {
    Move-Item -Force -LiteralPath $tmp -Destination $f -ErrorAction Stop
  } catch {
    Remove-Item -Force -LiteralPath $tmp -ErrorAction SilentlyContinue
    Write-Host ("ERROR: could not install " + $f + ": " + $_.Exception.Message) -ForegroundColor Red
    Write-Host "       Your existing config is untouched and the temp was removed." -ForegroundColor Red
    exit 1
  }
  Write-Host "  wrote $f"
  if ($carried.Count) { Write-Host ("  [note] carried over " + $carried.Count + " setting(s) you had added") }
}

function Test-LooksInstalled($d) {
  foreach ($m in @('.claude\hooks\protect-bash.sh','tools\x4validate','CLAUDE.md','KNOWLEDGEBASE.md')) {
    if (Test-Path -LiteralPath (Join-Path $d $m)) { return $true }
  }
  return $false
}

function Assert-Direction($dest, $named) {
  # MEASURED 2026-09-02 on the bash side: an install with an auto-detected destination
  # and -Yes wrote 1,642 files into a real Steam install and exited 0. Every X4_*
  # variable had been cleared first and none of it mattered, because the Steam path is
  # HARDCODED. Seven personal files were overwritten, including a 145 KB CLAUDE.md and a
  # 631 KB KNOWLEDGEBASE.md, recovered only from a Volume Shadow Copy.
  #
  # The rule (user-set): a new install must not proceed over an EXISTING install without
  # strict DIRECTION -- a switch naming the intent -- not merely an approval that can be
  # clicked through. And a destination nobody named is not a destination at all when
  # nobody is watching.
  #
  # TWO triggers, matching install.sh. -Yes was the only one here, so a
  # NON-INTERACTIVE run without -Yes auto-detected and wrote anyway: Read-Host on a
  # redirected stdin returns immediately and the detected default is accepted in
  # silence. The rule is "a destination the user never named is not a destination at
  # all when nobody is there to read the prompt".
  #
  # MEASURED 2026-09-04, and it is why this uses IsInputRedirected: with stdin from
  # a file, a pipe, and a plain tool-driven launch, [Environment]::UserInteractive
  # was True in all THREE. A guard keyed on it would never have fired. Both are
  # tested anyway -- UserInteractive False is a real service case that redirection
  # does not cover.
  $unattended = [Console]::IsInputRedirected -or -not [Environment]::UserInteractive
  if ($named -eq 'detected' -and ($Yes -or $unattended)) {
    if ($Yes) {
      Write-Host "REFUSING: -Yes with an auto-detected destination." -ForegroundColor Red
    } else {
      Write-Host "REFUSING: an auto-detected destination with no terminal to confirm on." -ForegroundColor Red
    }
    Write-Host "  Detected: $dest" -ForegroundColor Red
    if ($X4ToolkitFromEnv -and $Method -ne 'in-game') {
      Write-Host '  Nothing named that path - it came from $env:X4_TOOLKIT, INHERITED from your environment' -ForegroundColor Red
      Write-Host '  (usually your live toolkit), and no one will see this before the write starts.' -ForegroundColor Red
      Write-Host '  Name the destination explicitly:  -Toolkit "<the folder you mean>"' -ForegroundColor Red
    } elseif ($X4GameFromEnv -and $Method -eq 'in-game') {
      Write-Host '  Nothing named that path - it came from $env:X4_GAME, INHERITED from your environment' -ForegroundColor Red
      Write-Host '  (a -Method global install puts it in every agent session), and no one will see this' -ForegroundColor Red
      Write-Host '  before the write starts. Name it explicitly:  -Game "<the game folder you mean>"' -ForegroundColor Red
    } else {
      Write-Host "  Nothing named that path - it came from scanning the usual Steam locations," -ForegroundColor Red
      Write-Host "  and -Yes means no one will see this before the write starts." -ForegroundColor Red
      Write-Host "  Name it explicitly:  -Game `"$dest`"   (or -Toolkit for separate/global)" -ForegroundColor Red
    }
    exit 2
  }
  if ((Test-LooksInstalled $dest) -and -not $OverExisting) {
    Write-Host "REFUSING: there is already an installation at the destination." -ForegroundColor Red
    Write-Host "  Destination: $dest" -ForegroundColor Red
    Write-Host "  Found:" -ForegroundColor Red
    foreach ($m in @('.claude\hooks\protect-bash.sh','tools\x4validate','CLAUDE.md','KNOWLEDGEBASE.md')) {
      if (Test-Path -LiteralPath (Join-Path $dest $m)) { Write-Host "      $m" -ForegroundColor Red }
    }
    Write-Host "" -ForegroundColor Red
    Write-Host "  Installing over it REPLACES those files. An edited CLAUDE.md or AGENTS.md is" -ForegroundColor Red
    Write-Host "  KEPT beside it (X4-NOTES.pre-4.0.md / AGENTS.pre-4.0.md); your own" -ForegroundColor Red
    Write-Host "  KNOWLEDGEBASE.md and customised skills are replaced. x4-paths.env and" -ForegroundColor Red
    Write-Host "  settings.local.json are preserved." -ForegroundColor Red
    Write-Host "" -ForegroundColor Red
    if ($named -ne 'named') {
      # FX-B2: NOT the rerun line -- see install.sh's require_direction.
      Write-Host '  This destination was not named on the command line, so no upgrade command is offered.' -ForegroundColor Red
      Write-Host '  Name the folder you mean with -Toolkit DIR (-Game DIR for in-game); add' -ForegroundColor Red
      Write-Host '  -OverExisting only if you mean to UPGRADE the installation at that path.' -ForegroundColor Red
      exit 2
    }
    Write-Host "  To upgrade it anyway, say so explicitly -- your own command, plus -OverExisting:" -ForegroundColor Red
    Write-Host ("      " + (Get-R2RerunCmd '-OverExisting')) -ForegroundColor Red
    exit 2
  }
}

# Says WHERE, and nothing else -- see announce_target in install.sh for why the
# dry-run listing moved out: it only makes sense where a copy happens, and having
# both jobs in one function kept the global arm silent about its destination.
function Show-Target($dest) {
  Write-Host ""
  Write-Host "  About to write the toolkit into:"
  Write-Host "      $dest"
}

#: THE REST OF THE PREVIEW -- the twin of install.sh's announce_dry_run_extras (v4.0.0 review
#: R4-4): the Codex hooks.json render, the OpenCode deny-rule render, the .codex/config.toml
#: prepend, and the X4_TOOLKIT line, from the copy plan AND from the dry-run gate.
$script:X4DryExtrasShown = $false
function Show-DryRunExtras($dest) {
  if ($script:X4DryExtrasShown) { return }
  $script:X4DryExtrasShown = $true
  if (-not $dest) { return }
  if ($Method -ne 'global' -and (Test-CodexSelected)) {
    Write-Host '  .codex/hooks.json would be (re)rendered for this folder (Codex skips a changed hook until you re-approve it in /hooks)'
  }
  if ($Method -ne 'global' -and (Test-OpenCodeSelected)) {
    Write-Host "  .opencode/opencode.jsonc (the OpenCode deny rules) would be rendered for this folder's roots"
  }
  if ($CodexDocMaxBytes) {
    $f = Get-HCodexConfigPath $dest
    $old = Get-HCodexDocValue $f
    if ($null -ne $old -and "$old" -ne '') { Write-Host ('  ' + $f + ' already sets project_doc_max_bytes = ' + $old + ': it would be left unchanged') }
    else { Write-Host ('  project_doc_max_bytes = ' + $CodexDocMaxBytes + ' would be prepended to ' + $f) }
  }
  Show-HUserEnvPreview $dest
}

# The dry-run listing, for the arms where a COPY would actually happen. The gate
# itself is Refuse-IfDryRun, which sits inside all three writers.
function Show-CopyPlan($dest) {
  if ($DryRun) {
    Write-Host "  -DryRun: nothing will be written. Items that would be copied:"
    foreach ($i in $X4Items) {
      if (Test-Path -LiteralPath (Join-Path $SRC $i)) { Write-Host "      $i" }
    }
    if (Get-TrackedCopySet) {
      Write-Host ("  (the source is a git checkout: only the " + $script:X4Tracked.Count + " file(s) git tracks would be copied)")
    }
    Write-Host ('  agents that would be installed: ' + (Get-AgentsLanded))
    # SAID in the dry run exactly as the real run would do it: one function decides both.
    $to = Get-AgentsMdMoveTarget $dest
    if ($to) { Write-Host ('  your AGENTS.md differs from the shipped one: it would be KEPT as ' + $to + ', not overwritten') }
    $to = Get-HClaudeMdMoveTarget $dest
    if ($to) {
      Write-Host ('  your CLAUDE.md is not one this toolkit shipped: it would be KEPT as ' + $to + ', not overwritten')
      Show-HHashCaveat
    }
    switch (Get-HOldExampleAction $dest) {
      'remove' { Write-Host '  the 3.x .claude/x4-paths.env.example (an unedited shipped copy) would be removed' }
      'keep'   { Write-Host '  the 3.x .claude/x4-paths.env.example differs from every shipped one: it would be KEPT' }
    }
    Show-DryRunExtras $dest
    Write-Host ""
    Write-Host "=== dry run complete: nothing was changed ==="
    exit 0
  }
}

# --- Codex project_doc_max_bytes (lane H, opt-in) -- the mirror of install.sh ----------
$script:X4CodexDocWritten = ''
function Get-HCodexConfigPath($dest) { return (Join-Path (Join-Path $dest '.codex') 'config.toml') }

#: The ROOT-table project_doc_max_bytes value (keys before the first [table]), or $null.
function Get-HCodexDocValue($f) {
  if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { return $null }
  foreach ($line in [IO.File]::ReadAllLines($f)) {
    if ($line -match '^\s*\[') { return $null }
    if ($line -match '^\s*project_doc_max_bytes\s*=\s*([^#]*)') { return $Matches[1].Trim() }
  }
  return $null
}

function Assert-HCodexForDocCap {
  if (-not $CodexDocMaxBytes) { return }
  if (Test-CodexSelected) { return }
  Write-Host ('REFUSING: -CodexDocMaxBytes configures Codex, and this install does not select Codex (agents: ' + ($X4Agents -join ' ') + '). Nothing has been changed.') -ForegroundColor Red
  exit 2
}

function Test-HCodexDocCapPrecheck($dest) {
  if (-not $CodexDocMaxBytes) { return }
  $f = Get-HCodexConfigPath $dest
  if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { return }
  if (-not (Get-Item -LiteralPath $f -Force).IsReadOnly) { return }
  if ($null -ne (Get-HCodexDocValue $f)) { return }   # present: never changed, so no write
  Write-Host ''
  Write-Host 'REFUSING: -CodexDocMaxBytes must add a line to a READ-ONLY file.' -ForegroundColor Red
  Write-Host ('      ' + $f) -ForegroundColor Red
  Write-Host '  This is x4lock doing its job. Nothing has been changed. Unlock, re-run, lock:' -ForegroundColor Red
  Write-HX4LockSteps $dest $f -Red
  exit 1
}

function Write-HCodexDocCap($dest) {   # after the dispatch
  if (-not $CodexDocMaxBytes) { return }
  $f = Get-HCodexConfigPath $dest
  Refuse-IfDryRun 'writing project_doc_max_bytes into' $f
  $line = 'project_doc_max_bytes = ' + $CodexDocMaxBytes + '  # X4 toolkit installer (--codex-doc-max-bytes)'
  $old = Get-HCodexDocValue $f
  if ($null -ne $old) {
    if ($old -ceq $CodexDocMaxBytes) { Write-Host ('  [note] ' + $f + ' already sets project_doc_max_bytes = ' + $old + '; left untouched') }
    else { Write-Host ('  [WARNING] ' + $f + ' already sets project_doc_max_bytes = ' + $old + ', not ' + $CodexDocMaxBytes + '. Left unchanged.') }
    return
  }
  try {
    $dir = Join-Path $dest '.codex'
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { $null = New-Item -ItemType Directory -Path $dir }
    # ROOT keys must precede every [table], so the line goes FIRST; the rest is kept BYTE for byte.
    $rest = [byte[]]@()
    if (Test-Path -LiteralPath $f -PathType Leaf) { $rest = [IO.File]::ReadAllBytes($f) }
    $head = (New-Object System.Text.UTF8Encoding($false)).GetBytes($line + "`n")
    $tmp = $f + '.tmp' + $PID
    [IO.File]::WriteAllBytes($tmp, [byte[]]($head + $rest))
    Move-Item -LiteralPath $tmp -Destination $f -Force -ErrorAction Stop
  } catch {
    if ($tmp -and (Test-Path -LiteralPath $tmp)) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    $script:failed += ('.codex/config.toml (could not write project_doc_max_bytes into ' + $f + ')')
    return
  }
  $script:X4CodexDocWritten = $CodexDocMaxBytes
  Write-Host ('  wrote project_doc_max_bytes = ' + $CodexDocMaxBytes + ' into ' + $f)
}

# --- X4_TOOLKIT in the user environment (lane H; user decision 1) ----------------------
#: Set when unset; a DIFFERENT existing value is REPORTED and LEFT; -NoEnv touches nothing.
#: The writer is scripts/x4-userenv.ps1, the ONE mechanism both installers call (install.sh
#: through powershell.exe), so the two cannot write different things.
$X4UserEnvPs1 = 'scripts/x4-userenv.ps1'
$script:X4HEnvState = ''
$script:X4HEnvMsg = ''

#: The value as written: the resolved path, backslashes, no trailing separator.
function Get-HNativePath($t) {
  try { $v = (Resolve-Path -LiteralPath $t -ErrorAction Stop).ProviderPath } catch { $v = [string]$t }
  if ($X4OnWindows) { $v = $v.Replace([string][char]47, [string][char]92) }
  while ($v.Length -gt 3 -and ($v.EndsWith([string][char]92) -or $v.EndsWith([string][char]47))) {
    $v = $v.Substring(0, $v.Length - 1)
  }
  return $v
}

#: Canonical equality: one separator, no trailing one, an MSYS /c/ drive, case-insensitive.
function Get-HCanonPath($v) {
  $v = ([string]$v).Replace([string][char]47, [string][char]92)
  if ($v -match '^\\([A-Za-z])(\\|$)') { $v = $Matches[1] + ':' + [char]92 + $v.Substring([Math]::Min(3, $v.Length)) }
  while ($v.Length -gt 3 -and $v.EndsWith([string][char]92)) { $v = $v.Substring(0, $v.Length - 1) }
  return $v.ToLowerInvariant()
}
function Test-HSamePath($a, $b) { return ((Get-HCanonPath $a) -ceq (Get-HCanonPath $b)) }

function Get-HManualEnvCmd($v) { return ('setx X4_TOOLKIT "' + $v + '"') }

#: The last `export X4_TOOLKIT=` value in a startup file, quotes removed; $null when none.
#: install.sh's _h_profile_value, for Git Bash's own files (v4.0.0 review R4-3).
function Get-HProfileValue($f) {
  if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { return $null }
  $line = @(Get-Content -LiteralPath $f -ErrorAction SilentlyContinue |
            Where-Object { $_ -match '^\s*export\s+X4_TOOLKIT=' }) | Select-Object -Last 1
  if (-not $line) { return $null }
  $v = ($line -replace '^\s*export\s+X4_TOOLKIT=', '').TrimEnd([char]13)
  if ($v.Length -ge 2 -and (($v[0] -eq [char]39 -and $v[-1] -eq [char]39) -or ($v[0] -eq [char]34 -and $v[-1] -eq [char]34))) {
    $v = $v.Substring(1, $v.Length - 2)
  }
  return $v
}

#: The X4_TOOLKIT in the `env` block of the global Claude settings.json (-Method global writes
#: it), or $null. Read ONCE, before the dispatch ($X4SettingsToolkit), for the reason
#: install.sh's _h_settings_toolkit gives: the global arm rewrites that file.
function Get-HSettingsToolkit {
  $f = Join-Path (Get-GlobalClaudeDir) 'settings.json'
  if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { return $null }
  try {
    $j = Get-Content -Raw -LiteralPath $f | ConvertFrom-Json
    if ($j.env -and $j.env.X4_TOOLKIT) { return [string]$j.env.X4_TOOLKIT }
  } catch { }
  return $null
}

#: Git Bash's startup files: $HOME when set (Git Bash honours it), else the profile folder.
function Get-HProfileFiles {
  $h = if ($env:HOME) { $env:HOME } else { Get-UserHome }
  return @('.bashrc', '.bash_profile', '.profile') | ForEach-Object { Join-Path $h $_ }
}

#: THE DECISION, read by the -DryRun line and the writer: @{State; Old; Why}. Old lists EVERY
#: existing value, 'VALUE (WHERE)' joined by '; ' (v4.0.0 review R4-3: only the user scope was
#: read, so a MACHINE value or a Git Bash ~/.bashrc export was invisible and a SECOND,
#: conflicting value was written). Nothing is written beside a value that differs.
function Get-HUserEnvPlan($t) {
  if (-not $X4OnWindows) {
    return @{ State = 'skip'; Why = 'install.ps1 sets X4_TOOLKIT only on Windows; use install.sh on Linux/macOS' }
  }
  $ue = Join-Path $SRC $X4UserEnvPs1
  if (-not (Test-Path -LiteralPath $ue -PathType Leaf)) { return @{ State = 'fail'; Why = ('the source has no ' + $X4UserEnvPs1) } }
  $want = Get-HNativePath $t
  $found = @(); $diff = $false
  foreach ($scope in @(@('get', 'your user environment'), @('get-machine', 'the machine environment'))) {
    $global:LASTEXITCODE = 0
    $v = & $ue $scope[0]
    if ($LASTEXITCODE -ne 0) { return @{ State = 'fail'; Why = ($X4UserEnvPs1 + ' ' + $scope[0] + ' failed') } }
    $v = (@($v) -join '').Trim()
    if ($v) { $found += ($v + ' (' + $scope[1] + ')'); if (-not (Test-HSamePath $v $want)) { $diff = $true } }
  }
  foreach ($f in Get-HProfileFiles) {
    $v = Get-HProfileValue $f
    if ($v) { $found += ($v + ' (' + $f + ')'); if (-not (Test-HSamePath $v $want)) { $diff = $true } }
  }
  # FX-B2 (R4-3 completed): the global settings.json env block, and this PROCESS's environment.
  # Each counted only when it DIFFERS (see install.sh): an equal value there is not where the
  # user-level variable lives, and must not turn "set" into "already set" with nothing written.
  $v = $script:X4SettingsToolkit
  if ($v -and -not (Test-HSamePath $v $want)) {
    $found += ($v + ' (' + (Join-Path (Get-GlobalClaudeDir) 'settings.json') + ' env)'); $diff = $true
  }
  $v = $X4InheritedToolkit
  if ($found.Count -eq 0 -and $v -and -not (Test-HSamePath $v $want)) {
    $found += ($v + " (this process's environment)"); $diff = $true
  }
  if ($found.Count -eq 0) { return @{ State = 'set' } }
  if (-not $diff) { return @{ State = 'same'; Old = ($found -join '; ') } }
  return @{ State = 'different'; Old = ($found -join '; ') }
}

function Show-HUserEnvPreview($t) {
  if ($NoEnv) { Write-Host '  -NoEnv: X4_TOOLKIT would not be touched'; return }
  $plan = Get-HUserEnvPlan $t
  switch ($plan.State) {
    'set'       { Write-Host ('  X4_TOOLKIT would be set for your user: ' + (Get-HNativePath $t)) }
    'same'      { Write-Host '  X4_TOOLKIT is already set to this toolkit: nothing would change' }
    'different' { Write-Host ('  X4_TOOLKIT is set to ' + $plan.Old + ', not this toolkit: it would be left unchanged') }
    default     { Write-Host ('  X4_TOOLKIT would not be set: ' + $plan.Why) }
  }
}

function Set-HUserToolkitEnv($t) {   # after the dispatch, before setup.sh
  Refuse-IfDryRun 'setting X4_TOOLKIT for your user to' $t
  $v = Get-HNativePath $t
  $cmd = Get-HManualEnvCmd $v
  $plan = Get-HUserEnvPlan $t
  switch ($plan.State) {
    'same' { $script:X4HEnvState = 'same'; $script:X4HEnvMsg = "X4_TOOLKIT already set to this toolkit ($v)" }
    'different' {
      $script:X4HEnvState = 'different'
      $script:X4HEnvMsg = ('[WARNING] X4_TOOLKIT is set to ' + $plan.Old + ', not this toolkit ' + $v + '. Left unchanged. To point it here: ' + $cmd)
    }
    'skip' { $script:X4HEnvState = 'skip'; $script:X4HEnvMsg = $plan.Why }
    'fail' { $script:X4HEnvState = 'fail'; $script:failed += ('X4_TOOLKIT (could not set: ' + $plan.Why + '; run: ' + $cmd + ')') }
    'set' {
      $global:LASTEXITCODE = 0
      & (Join-Path $SRC $X4UserEnvPs1) set $v
      if ($LASTEXITCODE -eq 0) {
        $script:X4HEnvState = 'set'; $script:X4HEnvMsg = "X4_TOOLKIT set for your user: $v (new terminals only)"
      } else {
        $script:X4HEnvState = 'fail'; $script:failed += ('X4_TOOLKIT (could not set: ' + $X4UserEnvPs1 + ' set failed; run: ' + $cmd + ')')
      }
    }
  }
  if ($script:X4HEnvMsg) { Write-Host ('  ' + $script:X4HEnvMsg) }
}

# ONE implementation of "where is the global Claude config". Install-Global resolved it
# inline, so the gate below would have been a second copy of the same expression -- and
# this file's history is a list of things that drifted because two places computed one
# answer.
function Get-GlobalClaudeDir {
  if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path (Get-UserHome) '.claude' }
}

function Assert-GlobalOverExisting($t) {
  # THE GLOBAL DESTINATION WAS NEVER GATED. -OverExisting is consulted in exactly one
  # place -- Assert-Direction, on the copy-to-destination path -- which the global arm
  # never reaches. So -Method global force-overwrote a user's own ~\.claude\skills\x4-*
  # with no prompt, no -OverExisting, and no backup of the skills, then rewrote their
  # global settings.json. MEASURED in a sandbox: an edited x4-balance\SKILL.md was
  # replaced by the shipped one; only the settings.json half was ever backed up.
  #
  # install.sh has refused this since its own global arm was gated. This is the fifth
  # "fixed in bash, absent in PowerShell" of the release; the other four are named in
  # tests/test_installers_agree.py.
  #
  # BEFORE Write-PathsEnv, not inside Install-Global: refusing after the path config has
  # already been rewritten is a partial write, which is the shape of the bug rather than
  # a fix. install.sh gates in the same position, ahead of its own write_paths_env, so a
  # dry run refuses here too -- which is the useful answer, because it says what the real
  # run would do.
  $hc = Get-GlobalClaudeDir
  $sk = Join-Path $hc 'skills'
  # NO EARLY RETURN ON A MISSING skills DIR. It used to return here, which skipped the
  # agents check below entirely -- so a destination carrying an edited agent but no
  # skills directory sailed straight through the gate. Caught by testing exactly that
  # shape rather than the one the gate was written for.
  # -LiteralPath for the path and -Filter for the pattern: the bare parameters read a
  # home directory containing [ or ] as a WILDCARD character class, which is the defect
  # already recorded further down this file.
  # ENUMERATED FROM THE TOOLKIT, then checked against the destination -- the shape the
  # agents leg below already uses, and the property the comment above already claims.
  # This listed every x4-* directory IN THE DESTINATION, so a user's own `x4-mycustom/`
  # was named as a file the install "would REPLACE" and the run refused until they
  # passed -OverExisting, against a directory this installer never touches.
  $existing = @()
  $srcSkills = Join-Path $t '.claude/skills'
  if ((Test-Path -LiteralPath $sk) -and (Test-Path -LiteralPath $srcSkills)) {
    $existing = @(Get-ChildItem -Directory -LiteralPath $srcSkills -Filter 'x4-*' -ErrorAction SilentlyContinue |
                  Where-Object { Test-Path -LiteralPath (Join-Path $sk $_.Name) })
  }

  # AGENTS TOO. This gate enumerated skills only, while Install-Global copies every
  # <toolkit>/.claude/agents/*.md over the destination with -Force -- so a user's own
  # edited ~/.claude/agents/mod-research.md was replaced with no prompt, no
  # -OverExisting, no backup, and rc 0. Raised by the v3.1.0 release reviewer, who
  # measured exactly that in a sandbox. The comment at the top of this function says
  # "THE GLOBAL DESTINATION WAS NEVER GATED"; it was covering half that destination.
  #
  # Only files this install would actually WRITE are named: an unrelated agent of the
  # user's own is not at risk and must not be listed as though it were.
  $ag = Join-Path $hc 'agents'
  $shipped = @()
  $srcAgents = Join-Path $t '.claude/agents'
  if (Test-Path -LiteralPath $srcAgents) {
    $shipped = @(Get-ChildItem -File -LiteralPath $srcAgents -Filter '*.md' -ErrorAction SilentlyContinue |
                 ForEach-Object { $_.Name })
  }
  $agentsHit = @()
  if ((Test-Path -LiteralPath $ag) -and $shipped.Count -gt 0) {
    $agentsHit = @($shipped | Where-Object { Test-Path -LiteralPath (Join-Path $ag $_) })
  }

  if (($existing.Count -eq 0 -and $agentsHit.Count -eq 0) -or $OverExisting) { return }
  Write-Host "REFUSING: $hc already carries files this install would REPLACE." -ForegroundColor Red
  Write-Host "  Found:" -ForegroundColor Red
  foreach ($e in $existing) { Write-Host "      skills\$($e.Name)" -ForegroundColor Red }
  foreach ($a in $agentsHit) { Write-Host "      agents\$a" -ForegroundColor Red }
  Write-Host "" -ForegroundColor Red
  Write-Host "  Installing over them REPLACES those files. If you have edited any in" -ForegroundColor Red
  Write-Host "  place, they are gone -- this method keeps no backup of skills. It also" -ForegroundColor Red
  Write-Host "  rewrites $hc\settings.json (that half IS backed up)." -ForegroundColor Red
  Write-Host "" -ForegroundColor Red
  Write-Host "  To replace them anyway, say so explicitly -- your own command, plus -OverExisting:" -ForegroundColor Red
  Write-Host ("      " + (Get-R2RerunCmd '-OverExisting')) -ForegroundColor Red
  exit 2
}


function Assert-GlobalLockedTargets($t) {
  # HOISTED out of Install-Global, which the dispatch calls AFTER Write-PathsEnv --
  # so a refused run had already rewritten the path config and then printed
  # "Nothing has been changed." This file states the rule itself, two functions up:
  # refusing after the path config has been rewritten is a partial write, which is
  # the shape of the bug rather than a fix. Assert-GlobalOverExisting was moved ahead
  # of the writer for exactly that reason; this check was not, until 2026-09-08.
  $hc = Get-GlobalClaudeDir
  # Track exactly what WE copy - the $CLAUDE_PROJECT_DIR rewrite below must never
  # touch a user's pre-existing skills/agents (they may use that variable on purpose).
  # LOCKED TARGETS IN THIS DESTINATION TOO. Test-LockedTargetsPrecheck covers the
  # 16 copy items against $Toolkit; this arm writes to a SECOND destination that
  # neither precheck knew about. MEASURED: Copy-Item -Force CLEARS the read-only
  # attribute, so a user's edited, locked skill was overwritten and this returned
  # 0 -- and Assert-GlobalOverExisting's own refusal says "this method keeps no
  # backup of skills", so the loss is unrecoverable. install.sh skipped the same
  # file and also returned 0. Opposite outcomes, both success: exactly what
  # 18b220d called unacceptable, one destination later.
  $blockedG = @()
  Get-ChildItem -Directory -LiteralPath (Join-Path $t '.claude\skills') -Filter 'x4-*' -ErrorAction SilentlyContinue |
    ForEach-Object {
      $dstRoot0 = Join-Path (Join-Path $hc 'skills') $_.Name
      if (Test-Path -LiteralPath $dstRoot0) {
        foreach ($f in (Get-ChildItem -Recurse -File -LiteralPath $dstRoot0 -ErrorAction SilentlyContinue)) {
          try { if ($f.IsReadOnly) { $blockedG += $f.FullName } } catch { }
        }
      }
    }
  Get-ChildItem -File -LiteralPath (Join-Path $t '.claude\agents') -Filter '*.md' -ErrorAction SilentlyContinue |
    ForEach-Object {
      $a0 = Join-Path (Join-Path $hc 'agents') $_.Name
      if (Test-Path -LiteralPath $a0) {
        try { if ((Get-Item -LiteralPath $a0 -Force).IsReadOnly) { $blockedG += $a0 } } catch { }
      }
    }
  if ($blockedG.Count -gt 0) {
    Write-Host ("REFUSING: " + $blockedG.Count + " file(s) in " + $hc + " are READ-ONLY and this would overwrite them.")
    foreach ($b in ($blockedG | Select-Object -First 8)) { Write-Host ("      " + $b) }
    if ($blockedG.Count -gt 8) { Write-Host ("      ... and " + ($blockedG.Count - 8) + " more NOT LISTED") }
    Write-Host '      Unlock them, or move them aside, and re-run. Nothing has been changed.'
    exit 1
  }
}

function Install-Global($t) {
  Refuse-IfDryRun 'installing the global Claude config for' $t
  $hc = Get-GlobalClaudeDir
  New-Item -ItemType Directory -Force -Path (Join-Path $hc 'skills'),(Join-Path $hc 'agents') | Out-Null

  $copied = @()
  Get-ChildItem -Directory -LiteralPath (Join-Path $t '.claude\skills') -Filter 'x4-*' -ErrorAction SilentlyContinue |
    ForEach-Object {
      $dst = Join-Path $hc 'skills'
      Copy-Item -Recurse -Force -LiteralPath $_.FullName -Destination $dst
      # DERIVED FROM THE SOURCE, never from a destination glob -- the rule install.sh
      # states two lines above its own loop, and the agents leg below already follows.
      # Enumerating the DESTINATION swept in any pre-existing `.md` in a user's
      # ~/.claude/skills/x4-<name>/ that this toolkit does not ship, and then rewrote
      # its contents: exactly what "a pre-existing user skill named x4-* must not
      # match" forbids.
      $srcRoot = $_.FullName
      $dstRoot = Join-Path $dst $_.Name
      # EVERY shipped file, not only *.md -- install.sh rewrites `find -type f` and
      # this filtered, so a shipped script or template kept $CLAUDE_PROJECT_DIR on
      # Windows and resolved to whatever repo the user had open. Zero cost when the
      # divergence was created (no skill ships a non-.md file); latent from the day
      # one does, and invisible to a parity suite that compares the two files as text.
      $copied += Get-ChildItem -Recurse -File -LiteralPath $srcRoot |
        ForEach-Object {
          # BYTE VALUES, not quoted literals. This shipped as TrimStart('', '/') --
          # a lone backslash that collapsed to an empty string at authoring time --
          # and an empty string is not convertible to [char], so -Method global threw
          # here on EVERY run, on 5.1 and 7 alike, after x4-paths.env had been
          # rewritten and the first skill force-copied. Test-SameDir dodges the same
          # hazard the same way; a quoted separator can be re-broken silently.
          $rel = $_.FullName.Substring($srcRoot.Length).TrimStart([char]92, [char]47)
          Get-Item -LiteralPath (Join-Path $dstRoot $rel) -ErrorAction SilentlyContinue
        }
    }
  $agentsDst = Join-Path $hc 'agents'
  Get-ChildItem -File -LiteralPath (Join-Path $t '.claude\agents') -Filter '*.md' -ErrorAction SilentlyContinue |
    ForEach-Object {
      Copy-Item -Force -LiteralPath $_.FullName -Destination $agentsDst
      # NB: two-argument Join-Path only - the 3-arg form is pwsh 7+ and this script
      # must run under Windows PowerShell 5.1 (the README's own command).
      $copied += Get-Item -LiteralPath (Join-Path $agentsDst $_.Name)
    }
  # global skills/agents run from any repo -> resolve validator via $X4_TOOLKIT
  foreach ($file in $copied) {
    $c = Get-Content -Raw -LiteralPath $file.FullName
    if ($c.Contains('$CLAUDE_PROJECT_DIR')) {
      Write-Utf8NoBom $file.FullName ($c.Replace('$CLAUDE_PROJECT_DIR','$X4_TOOLKIT'))
    }
  }
  # "I copied nothing" must never print as "installed". The whole function used bare
  # path cmdlets, so a toolkit path containing `[` or `]` -- which PowerShell reads as a
  # WILDCARD CHARACTER CLASS, not as text -- matched nothing, `-ErrorAction
  # SilentlyContinue` swallowed the miss, and this said "installed x4 skills + agents"
  # over an empty directory. MEASURED 2026-09-01 under a path named `toolkit [v3]`:
  # bare Get-ChildItem found 0, -LiteralPath found 1, in both the skills and agents legs.
  if ($copied.Count -eq 0) {
    Write-Host "  ERROR: copied NOTHING from $t (.claude\skills\x4-* and .claude\agents\*.md)."
    Write-Host "         Nothing was installed into $hc -- this is a FAILURE, not a no-op."
    exit 1
  }
  Write-Host "  installed x4 skills + agents into $hc ($($copied.Count) file(s))"
  # STATED, because it is the difference between this layout and the other two.
  # The guards are registered in a repo's .claude/settings.json, which this
  # method does not copy, and each hook is addressed as
  # $CLAUDE_PROJECT_DIR/.claude/hooks/... -- globally that resolves to whatever
  # repo the user has open, not to the toolkit. They are NOT installed here on
  # purpose: running them in every unrelated project, on the blocking hook path,
  # to guard X4 paths that are not present, is how a guard gets switched off.
  Write-Host ""
  Write-Host "  NOTE: this layout installs the SKILLS AND AGENTS ONLY."
  Write-Host "        The safety guards -- the command and file hooks, and the automatic"
  Write-Host "        backup before every edit -- are NOT installed by -Method global."
  Write-Host "        They are per-project: they live in a repo's .claude/settings.json"
  Write-Host "        and resolve their paths from that project. Use -Method in-game or"
  Write-Host "        -Method separate in a mod repo to get them there."
  # merge env into settings.json
  $sj = Join-Path $hc 'settings.json'
  # GUARDED, and it was the only unguarded operation left in this function while its
  # other four writes all gained try/catch. An unparseable settings.json threw RAW --
  # "Cannot index into a null array" on an empty file, an ArgumentException on a
  # malformed one -- with a CategoryInfo dump, no crafted message, and no $failed
  # accounting. install.sh degrades with a crafted ERROR on the same inputs.
  #
  # EMPTY refuses too, deliberately: an empty settings.json is indistinguishable from
  # a truncated one, and overwriting the user's GLOBAL config on that guess is the
  # wrong call. Nothing has been written at this point, so refusing here costs the
  # user only a re-run.
  $cfg = [pscustomobject]@{}
  if (Test-Path -LiteralPath $sj) {
    $raw = $null
    try { $raw = Get-Content -Raw -LiteralPath $sj -ErrorAction Stop } catch {
      Write-Host ("ERROR: could not read " + $sj + ": " + $_.Exception.Message) -ForegroundColor Red
      Write-Host "       Nothing has been changed." -ForegroundColor Red
      exit 1
    }
    if ($null -eq $raw -or -not $raw.Trim()) {
      Write-Host ("ERROR: " + $sj + " is EMPTY, so this run cannot tell an intentionally") -ForegroundColor Red
      Write-Host "       blank file from a truncated one and will not overwrite your global" -ForegroundColor Red
      Write-Host "       settings on that guess. Restore or delete it, then re-run. Nothing" -ForegroundColor Red
      Write-Host "       has been changed." -ForegroundColor Red
      exit 1
    }
    try { $cfg = $raw | ConvertFrom-Json -ErrorAction Stop } catch {
      Write-Host ("ERROR: " + $sj + " is not valid JSON, so the X4_* env cannot be merged") -ForegroundColor Red
      Write-Host ("       into it: " + $_.Exception.Message) -ForegroundColor Red
      Write-Host "       Fix or move that file, then re-run. Nothing has been changed." -ForegroundColor Red
      exit 1
    }
    if ($null -eq $cfg) { $cfg = [pscustomobject]@{} }
  }
  if (-not $cfg.PSObject.Properties['env']) { $cfg | Add-Member -NotePropertyName env -NotePropertyValue ([pscustomobject]@{}) }
  $ref = if ($Reference) { $Reference } else { Join-DerivedPath $t 'reference' }
  $ext = if ($Extensions) { $Extensions } elseif ($Game) { Join-DerivedPath $Game 'extensions' } else { '' }
  function setenv($k,$v){ if ($v) { if ($cfg.env.PSObject.Properties[$k]) { $cfg.env.$k = $v } else { $cfg.env | Add-Member -NotePropertyName $k -NotePropertyValue $v } } }
  setenv X4_TOOLKIT $t; setenv X4_REFERENCE $ref; setenv X4_GAME $Game; setenv X4_PROFILE $Profile
  if ($Profile) { setenv X4_DEBUGLOG (Join-DerivedPath $Profile 'debug.txt') }
  setenv X4_MODS $Mods; setenv X4_EXTENSIONS $ext; setenv XRCATTOOL $XRCatTool
  # BACK IT UP FIRST. Write-PathsEnv and Copy-Toolkit both do; this one did not, and
  # it rewrites the user's GLOBAL settings.json.
  if (Test-Path -LiteralPath $sj) {
    # GUARDED, as install.sh guards this same backup with an explicit refusal. A
    # backup that silently did not happen is worse than none, because the message
    # on the next line would have said it did.
    try {
      $bak = New-ConfigBackup $sj
    } catch {
      Write-Host ("ERROR: could not back up " + $sj + ": " + $_.Exception.Message) -ForegroundColor Red
      Write-Host "       Refusing to merge into it unbacked. Nothing has been changed." -ForegroundColor Red
      exit 1
    }
    Write-Host "  backed up $sj -> $(Split-Path -Leaf $bak)"
  }
  # GUARDED. A raw .NET dump here leaves the merged settings half-written with no
  # crafted message and no $failed accounting -- and the backup above is the only
  # copy of what was there.
  try {
    Write-Utf8NoBom $sj (($cfg | ConvertTo-Json -Depth 20) + "`n")
  } catch {
    Write-Host ("ERROR: could not write " + $sj + ": " + $_.Exception.Message) -ForegroundColor Red
    Write-Host ("       Your previous settings are in " + $bak) -ForegroundColor Red
    exit 1
  }
  # VERIFY THE ARTIFACT, not the fact that a write statement ran -- the rule this
  # installer already applies to x4-paths.env, and install.sh applies to this very
  # file with `jq -e '.env.X4_TOOLKIT'` and exit 1.
  $back = $null
  try { $back = Get-Content -Raw -LiteralPath $sj | ConvertFrom-Json } catch { }
  if (-not $back -or -not $back.env -or -not $back.env.X4_TOOLKIT) {
    Write-Error "wrote $sj but reading it back does not show .env.X4_TOOLKIT -- the merge did not land."
    exit 1
  }
  Write-Host "  merged X4_* env into $sj (verified)"
}

if (-not $Method) {
  Write-Host "`nInstall method:  1) in-game   2) separate   3) global (multi-repo)"
  $m = if ($Yes) { '2' } else { Read-Host "Choose [1/2/3]" }
  $Method = switch ($m) { '1' {'in-game'} '3' {'global'} default {'separate'} }
}
Write-Host "Method: $Method"
$script:X4SettingsToolkit = Get-HSettingsToolkit   # BEFORE any write (see Get-HSettingsToolkit)
foreach ($n in $X4InheritedPaths) {
  Write-Host ('  [note] ' + $n + ' from your environment (not a parameter): ' + [Environment]::GetEnvironmentVariable($n) + ' -- this install''s config will use it; pass the parameter to choose another.')
}
if ($X4ToolkitFromEnv -and $Method -ne 'in-game') {
  Write-Host ('  [note] no -Toolkit given: the destination below comes from $env:X4_TOOLKIT, inherited from your')
  Write-Host ('         environment (' + $env:X4_TOOLKIT + '). An inherited value is NOT a named destination.')
}

$Game = Detect-Game; $Profile = Detect-Profile; $XRCatTool = Detect-XRCat
$Game      = Ask $Game      'X4 game folder (01.cat..09.cat)' $Game
$Profile   = Ask $Profile   'X4 user profile folder'          $Profile
$XRCatTool = Ask $XRCatTool 'XRCatTool.exe path'              $XRCatTool

# Normalised here and again after the in-arm Ask below: a destination can arrive
# either way, and a single up-front pass would miss the interactive one.
$Game    = Remove-TrailingSep $Game
$Profile = Remove-TrailingSep $Profile
$Toolkit = Remove-TrailingSep $Toolkit

# B1 (install red-team 2026-10-04): Git Bash, resolved ONCE, BEFORE the dispatch. On a stock
# Windows PATH `bash` is the WSL stub (Git for Windows adds only <Git>\cmd), so the OpenCode
# renderer -- which runs inside the dispatch, through the guards' resolver -- found no bash and
# `-Agent all` ended INCOMPLETE. Found here, it is exported as X4_BASH for THIS run, which every
# resolver below (x4guard.resolve_bash, setup.sh's children) honours first. The user's own
# environment is never changed by this; a missing bash prints the exact setx line instead.
$bash = Find-GitBash
if ($bash -and -not $env:X4_BASH) { $env:X4_BASH = $bash.Source }
if (-not $bash) {
  Write-Host '  [warn] no Git Bash found (Git for Windows locations, then PATH past the WSL stub).'
  Write-Host '         The guards, setup.sh and the OpenCode renderer need it. Install Git for Windows,'
  Write-Host '         or point the toolkit at your bash.exe (takes effect in NEW shells):'
  Write-Host '           setx X4_BASH "C:\Program Files\Git\bin\bash.exe"   (adjust if Git lives elsewhere)'
}

switch ($Method) {
  'in-game'  {
    if (-not $Game) { throw 'in-game needs -Game' }
    $Toolkit = $Game
    Show-Target $Toolkit
    Resolve-HAutoAgents $Toolkit   # BEFORE anything reads $X4Items
    Assert-HCodexForDocCap
    # Test-ConfigPrecheck OUTSIDE, Test-LockedTargetsPrecheck INSIDE -- the config
    # is written on both branches, the copy is not. install.sh makes the same split.
    # DIRECTION FIRST, matching install.sh, which carries a nine-line comment at
    # both of its call sites declaring this order load-bearing. Both prechecks can
    # exit telling the user to unlock and re-run -- against a destination they have
    # not authorised replacing, so acting on that advice leads straight back to a
    # refusal for the real reason. 811a9a7 fixed bash and left this side; seventh
    # occurrence of the class on this file pair, bash -> ps1 this time.
    if (-not (Test-SameDir $SRC $Toolkit)) { Assert-Direction $Toolkit $GameNamed }
    Test-ConfigPrecheck $Toolkit
    Test-CodexHooksPrecheck $Toolkit   # written on BOTH branches, so OUTSIDE the guard
    Test-OpenCodeConfigPrecheck $Toolkit   # likewise
    Test-HCodexDocCapPrecheck $Toolkit
    if (-not (Test-SameDir $SRC $Toolkit)) {
      Test-ProjectSettingsPrecheck $Toolkit
      Test-LockedTargetsPrecheck $Toolkit
      Show-CopyPlan $Toolkit
      Save-UserAgentsMd $Toolkit        # after every precheck, before the copy
      Save-HUserClaudeMd $Toolkit       # 3.x -> 4.0: same position, same shape
      Copy-Toolkit $Toolkit
      Remove-HOldPathsExample $Toolkit   # after the copy: the 4.0 example is in place
      Invoke-ToolkitTokenRender $Toolkit
    } else {
      Show-InPlaceTokenNote
    }
    Write-PathsEnv $Toolkit
    Write-CodexHooksJson $Toolkit
    Write-OpenCodeConfig $Toolkit
  }
  'separate' {
    if (-not $Toolkit) { $Toolkit = $SRC }
    $Toolkit = Ask $Toolkit 'Toolkit folder' $Toolkit
    $Toolkit = Remove-TrailingSep $Toolkit
    Show-Target $Toolkit
    Resolve-HAutoAgents $Toolkit   # BEFORE anything reads $X4Items
    Assert-HCodexForDocCap
    # Test-ConfigPrecheck OUTSIDE, Test-LockedTargetsPrecheck INSIDE -- the config
    # is written on both branches, the copy is not. install.sh makes the same split.
    # DIRECTION FIRST, matching install.sh, which carries a nine-line comment at
    # both of its call sites declaring this order load-bearing. Both prechecks can
    # exit telling the user to unlock and re-run -- against a destination they have
    # not authorised replacing, so acting on that advice leads straight back to a
    # refusal for the real reason. 811a9a7 fixed bash and left this side; seventh
    # occurrence of the class on this file pair, bash -> ps1 this time.
    if (-not (Test-SameDir $SRC $Toolkit)) { Assert-Direction $Toolkit $ToolkitNamed }
    Test-ConfigPrecheck $Toolkit
    Test-CodexHooksPrecheck $Toolkit   # written on BOTH branches, so OUTSIDE the guard
    Test-OpenCodeConfigPrecheck $Toolkit   # likewise
    Test-HCodexDocCapPrecheck $Toolkit
    if (-not (Test-SameDir $SRC $Toolkit)) {
      Test-ProjectSettingsPrecheck $Toolkit
      Test-LockedTargetsPrecheck $Toolkit
      Show-CopyPlan $Toolkit
      Save-UserAgentsMd $Toolkit        # after every precheck, before the copy
      Save-HUserClaudeMd $Toolkit       # 3.x -> 4.0: same position, same shape
      Copy-Toolkit $Toolkit
      Remove-HOldPathsExample $Toolkit   # after the copy: the 4.0 example is in place
      Invoke-ToolkitTokenRender $Toolkit
    } else {
      Show-InPlaceTokenNote
    }
    Write-PathsEnv $Toolkit
    Write-CodexHooksJson $Toolkit
    Write-OpenCodeConfig $Toolkit
  }
  'global'   {
    # THE GLOBAL LAYOUT IS CLAUDE-ONLY (see install.sh): an explicit non-Claude target is
    # REFUSED rather than half installed; the default proceeds and says what it leaves out.
    if ($CodexDocMaxBytes) {
      Write-Host 'REFUSING: -CodexDocMaxBytes configures Codex, and -Method global is a Claude-only layout.' -ForegroundColor Red
      Write-Host '  Nothing has been changed.' -ForegroundColor Red
      exit 2
    }
    if ($Agent -cne 'all' -and $Agent -cne 'auto' -and (($X4Agents -join ' ') -cne 'claude')) {   # C1: a list too
      Write-Host "REFUSING: -Method global is a Claude-only layout; it cannot install -Agent $Agent." -ForegroundColor Red
      Write-Host '  Use -Method in-game or -Method separate for Codex, OpenCode and generic agents.' -ForegroundColor Red
      Write-Host '  Nothing has been changed.' -ForegroundColor Red
      exit 2
    }
    if ($Agent -ceq 'all' -or $Agent -ceq 'auto') {
      Write-Host '  [note] -Method global is a Claude-only layout: only the Claude target is installed.'
      Write-Host '         Codex, OpenCode and generic agents need -Method in-game or -Method separate.'
    }
    $X4Agents = @('claude')
    Resolve-HItems
    if (-not $Toolkit) { $Toolkit = $SRC }
    # FX-B2: global copies the skills and agents of the toolkit at $Toolkit. An INHERITED
    # X4_TOOLKIT naming another toolkit silently installed THAT toolkit's skills.
    if ($X4ToolkitFromEnv -and -not (Test-SameDir $SRC $Toolkit)) {
      Write-Host ('REFUSING: -Method global installs the skills of the toolkit at ' + $Toolkit + ', which came from') -ForegroundColor Red
      Write-Host '  $env:X4_TOOLKIT inherited from your environment, not from a parameter. Nothing has been changed.' -ForegroundColor Red
      Write-Host ('  Name it:  -Toolkit "' + $SRC + '"   (this toolkit)   or   -Toolkit "' + $Toolkit + '"   (that one)') -ForegroundColor Red
      exit 2
    }
    Show-Target $Toolkit
    # Ahead of every write, exactly where install.sh gates its own global arm.
    Assert-GlobalOverExisting $Toolkit
    Assert-GlobalLockedTargets $Toolkit   # BEFORE the config write, not inside the copier
    Test-ConfigPrecheck $Toolkit   # -Method global writes the config and never copied
    Write-PathsEnv $Toolkit
    Install-Global $Toolkit
  }
}

# Opt-in Codex doc cap: ONE call, after every arm (global refused it up front).
Write-HCodexDocCap $Toolkit
# X4_TOOLKIT for the user: ONE call, after every arm, so no arm can skip or repeat it.
if (-not $NoEnv) { Set-HUserToolkitEnv $Toolkit }

# wire x4validate (needs bash/uv); skip gracefully if bash missing
#
# `Get-Command bash` is NOT good enough. On any Windows machine with WSL enabled --
# which includes every Docker Desktop install -- `bash` resolves to the WSL stub at
# C:\Windows\System32\bash.exe (or the WindowsApps alias), NOT to Git Bash. MEASURED
# 2026-09-01 on a machine whose only distro is docker-desktop: setup.sh died with
#   WSL (9 - Relay) ERROR: CreateProcessCommon:640: execvpe(/bin/bash) failed
# and the installer reported INCOMPLETE. Worse, a machine WITH a real distro would
# have run setup.sh inside Linux, where C:\... paths do not resolve at all -- a
# silent wrong install rather than a loud failure.
#
# So: prefer a real Git Bash, and refuse the known stubs by path.

# ($bash was resolved once, before the dispatch -- B1.)
# ($failed is defined above the dispatch, beside -Agent's resolution: the Codex writers
#  inside the dispatch record into it.)
if ($bash) {
  Push-Location -LiteralPath $Toolkit
  # CLAUDE_PROJECT_DIR must be set explicitly: setup.sh falls back to $(pwd), and
  # if the caller already exports it (running from inside Claude Code is the
  # documented path) the fallback never applies and we would wire up THEIR repo
  # instead of the toolkit. install.sh has always passed it; this did not.
  $prev = $env:CLAUDE_PROJECT_DIR
  $env:CLAUDE_PROJECT_DIR = $Toolkit
  try {
    # $ErrorActionPreference='Stop' does NOT trap a native exit code, so each
    # call needs its own check - otherwise a failed unpack still reached
    # "=== install complete ===" and the user believed a broken install.
    # GATED HERE TOO, not only in the writers. These run at TOP LEVEL, so
    # Refuse-IfDryRun placed inside Copy-Toolkit / Write-PathsEnv / the global arm
    # is STRUCTURALLY unable to cover them -- it only ever ran because one of
    # those happened to exit first. A dry run that syncs dependencies or unpacks
    # the game archives is not a preview.
    if ($DryRun) {
      Write-Host '  --dry-run: NOT running setup.sh'
      if ($Unpack) { Write-Host '  --dry-run: NOT unpacking reference/' }
    } else {
    $env:X4_SETUP_CONFIG_WRITTEN = '1'
    try { & $bash.Source setup.sh } finally { Remove-Item -LiteralPath env:X4_SETUP_CONFIG_WRITTEN -ErrorAction SilentlyContinue }
    if ($LASTEXITCODE -ne 0) { $failed += "setup.sh (exit $LASTEXITCODE)" }
    if ($Unpack) {
      # --toolkit (FX-B2): the unpack acts for the toolkit it LIVES in and refuses an inherited
      # X4_TOOLKIT naming another one unless told -- and this run INSTALLED $Toolkit.
      # --reference (FX-B3, reviewer F M1): the tree THIS run configured. Without it a shell
      # exporting a different X4_REFERENCE made `-Reference R2 -Unpack` refuse and the install
      # end INCOMPLETE. The unpack also reads THIS run's config and game: an inherited
      # X4_CONFIG would have chosen another config, an inherited X4_GAME outranks -Game.
      $unpackRef = if ($Reference) { $Reference } else { Join-DerivedPath $Toolkit 'reference' }
      $savedCfg = $env:X4_CONFIG; $savedGame = $env:X4_GAME
      try {
        Remove-Item -LiteralPath env:X4_CONFIG -ErrorAction SilentlyContinue
        if ($Game) { $env:X4_GAME = $Game }
        & $bash.Source bin/unpack-reference.sh --toolkit $Toolkit --reference $unpackRef
        $unpackRc = $LASTEXITCODE
      } finally {
        if ($null -ne $savedCfg) { $env:X4_CONFIG = $savedCfg }
        if ($null -ne $savedGame) { $env:X4_GAME = $savedGame }
        else { Remove-Item -LiteralPath env:X4_GAME -ErrorAction SilentlyContinue }
      }
      if ($unpackRc -ne 0) { $failed += "bin/unpack-reference.sh (exit $unpackRc)" }
    }
    }
  } finally {
    $env:CLAUDE_PROJECT_DIR = $prev
    Pop-Location
  }
} else {
  # Git for Windows only adds ...\Git\cmd to PATH by default; bash.exe lives in
  # ...\Git\bin. Name the actual fix rather than telling them to run a command
  # they equally cannot run.
  Write-Host "  [note] no Git Bash found, so x4validate was NOT wired up."
  Write-Host "         Git for Windows ships bash in <install>\bin (e.g. C:\Program Files\Git\bin)."
  Write-Host "         Point the toolkit at it, open a NEW PowerShell, and re-run this installer:"
  Write-Host '           setx X4_BASH "C:\Program Files\Git\bin\bash.exe"   (adjust if Git lives elsewhere)'
  $failed += 'Git Bash not found (x4validate not wired up): setx X4_BASH "C:\Program Files\Git\bin\bash.exe", then re-run'
}

if ($failed.Count) {
  Write-Host "`n=== install INCOMPLETE ($Method) ===" -ForegroundColor Yellow
  foreach ($f in $failed) { Write-Host "  failed: $f" -ForegroundColor Yellow }
  Write-Host "Toolkit: $Toolkit"
  Write-Host "Fix the above and re-run, or complete the step by hand."
  exit 1
}

Write-Host "`n=== install complete ($Method) ==="
# R2 cosmetic: -Toolkit may be typed with `/`, and Join-Path adds `\`: the summary mixed both.
# Shown in the platform's own spelling from here on (Test-Path accepts either).
if ([System.IO.Path]::DirectorySeparatorChar -eq [char]92) { $Toolkit = $Toolkit -replace '/', '\' }
Write-Host "Toolkit: $Toolkit"
Write-Host ('Agents:  ' + (Get-AgentsLanded))
Write-Host "Config:  $Toolkit\x4-paths.env  (edit any path here)"
# CODEX RUNS NO HOOK IT HAS NOT REVIEWED, silently (MEASURED, spike 2026-09-30), and the
# installer must never approve hooks or trust the project for the user (spec section 8).
if ($Method -ne 'global' -and (Test-CodexSelected)) {
  Write-Host ''
  Write-Host 'Codex:   the guards in .codex/hooks.json are INERT until you review them in Codex.'
  Write-Host "         1. run  codex  in $Toolkit  and trust the folder when asked"
  Write-Host '         2. type  /hooks  and approve each X4 hook'
  Write-Host "         3. verify:  python scripts/x4doctor.py --root `"$Toolkit`""
  if ($script:X4CodexHooksWritten) { Write-Host '         (the definitions were just (re)written: any earlier review no longer holds)' }
  if ($script:X4CodexDocWritten) {
    Write-Host ('         project_doc_max_bytes = ' + $script:X4CodexDocWritten + ' is in .codex\config.toml; it takes')
    Write-Host '         effect once you trust this folder in Codex.'
  }
}
if ($Method -ne 'global' -and (Test-OpenCodeSelected)) {
  Write-Host ''
  Write-Host $X4OpenCodeNote
  Write-Host "         start  opencode  in $Toolkit  (its config and plugin are found from there), then"
  Write-Host "         verify:  python scripts/x4doctor.py --root `"$Toolkit`" --agent opencode"
}
if ($Method -eq 'global') { Write-Host "Global:  skills/agents + X4_* env added to your ~/.claude - works from any mod repo." }
Write-RefguardStep $Toolkit
Write-NextStep $Toolkit
Write-Host ""
if ($script:X4HEnvState -eq 'set' -or $script:X4HEnvState -eq 'same') {
  Write-Host ('X4_TOOLKIT: ' + $script:X4HEnvMsg)
} else {
  # -NoEnv, a different existing value, or not Windows: the user decides.
  Write-Host "IMPORTANT - set X4_TOOLKIT in your user environment so the tools find the config"
  Write-Host "above from ANY directory (they are often run from the game folder, which may hold"
  Write-Host "no x4-paths.env)."
  if ($NoEnv) { Write-Host '  -NoEnv: this installer did not touch it. To set it yourself:' }
  elseif ($script:X4HEnvState -eq 'different') { Write-Host '  It names another toolkit (the [WARNING] above). To point it here instead:' }
  elseif ($script:X4HEnvState -eq 'skip') { Write-Host ('  ' + $script:X4HEnvMsg + '. To set it yourself:') }
  else { Write-Host "  It was not set (see 'failed:' above). To set it yourself:" }
  Write-Host ('         ' + (Get-HManualEnvCmd (Get-HNativePath $Toolkit)) + '        (takes effect in NEW shells)')
}
Write-Host "Verify:  cd `"$Toolkit\tools\x4validate`" ; uv run x4validate --paths"
# A COMPLETE INSTALL EXITS 0, EXPLICITLY (FX-B2, CI on da93d2d). Without it the script fell off
# its end and the caller saw whatever native command ran LAST: Write-RefguardStep's
# `x4refguard status` exits 2 for "no reference/ tree yet", so `& .\install.ps1` printed
# "=== install complete ===" and left $LASTEXITCODE = 2 (MEASURED, 5.1 and 7).
exit 0
