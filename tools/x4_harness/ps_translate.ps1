#requires -Version 5.1
# ps_translate.ps1 -- the PowerShell FRONT-END of the Bash guard (AUDIT-2026-09-24 HK-1).
#
# stdin : one PowerShell command string (UTF-8).
# stdout: one JSON object (UTF-8):
#     {"ok": true,  "command": "<an equivalent POSIX-shell command>"}
#     {"ok": false, "reason":  "<why nothing could be translated>"}
#
# WHY A TRANSLATOR AND NOT A SECOND RULE SET. The Bash guard's rules live in ONE place --
# hook_facts.py answers every predicate, protect-bash.sh holds the policy -- and every gap
# ever found in it lived in a place where a rule had been written twice. So PowerShell
# gets no rules of its own: this script only answers "what does this command DO to the
# filesystem", in the vocabulary those rules already understand, and hook_facts.py runs
# the result through the same pass as any Bash command.
#
#     Remove-Item -Recurse <p>        ->  rm -rf '<p>'
#     gci <p> -Filter *.x | ri        ->  find '<p>' -name '*.x' -delete
#     Move-Item a b / Copy-Item a b   ->  mv 'a' 'b' / cp -r 'a' 'b'
#     Set-Content / Out-File <p>      ->  : > '<p>'        (-Append: >>)
#     Add-Content <p>                 ->  : >> '<p>'
#     Clear-Content <p>               ->  truncate -s 0 '<p>'
#     cmd > <p> / cmd >> <p>          ->  ... > '<p>' / >> '<p>'
#     [IO.File]::Delete(<p>)          ->  rm -rf '<p>'  (and Write*/Append*/Move/Copy)
#     iex '<text>'                    ->  <text>, translated in turn
#     anything else (git, python ...) ->  the command, word for word
#
# THE PARSING IS POWERSHELL'S OWN (user decision 2026-09-25): Parser::ParseInput for the
# syntax tree and StaticParameterBinder for parameter binding, so aliases (`rm`, `ri`,
# `del`), unambiguous parameter prefixes (`-fo` for -Force) and positional binding are
# resolved exactly as PowerShell resolves them. A hand-rolled tokenizer is what every
# parser defect in the Bash half came from.
#
# A value this script cannot resolve -- a variable assigned at runtime, `$_`, an
# expression -- becomes a shell VARIABLE in the output ("${NAME}"), so it reaches the
# rules exactly as an unresolvable `$X` in a Bash command does: an unknown operand is
# not proof of safety for a delete, and `$env:X4_REFERENCE` still names its root.
#
# FAILS CLOSED: a command that does not PARSE is not translated ({"ok": false}); the
# caller DENIES it with the parser's message -- a syntax error is fixed, not confirmed.
# The reason's prefix "does not parse as PowerShell" is how hook_facts.py tells a parse
# failure from a missing PowerShell (PS_PARSE_ERROR there): keep them in step.
using namespace System.Management.Automation.Language

$ErrorActionPreference = 'Stop'

function Out-Json($obj) {
    $bytes = [Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Compress -Depth 4))
    $out = [Console]::OpenStandardOutput()
    $out.Write($bytes, 0, $bytes.Length)
    $out.Flush()
}

# Unresolved values travel as  <SOH>NAME<STX>  inside a string and become "${NAME}" in
# the output. Control bytes cannot occur in a path.
$SOH = [char]1
$STX = [char]2
# OPAQUE vs NAMED unresolved values (v3.3.0 release review, finding 1). An ENVIRONMENT
# variable reaches the rules as `$X` does in Bash -- a root variable names its root, any
# other is an operand the Bash half already has a convention for. A PowerShell-LOCAL value
# the translator could not resolve (command output, `$_` from an unknown producer, an
# expression) has no such convention: it is a value nobody can name, so a write or delete
# aimed at one is reported UNKNOWN and the hook asks. Recorded by marker name.
$script:Opaque = New-Object 'Collections.Generic.HashSet[string]'
function UVar([string]$name, [bool]$opaque = $true) {
    $n = $name -replace '^env:', ''
    if ($n -eq '_' -or $n -eq 'PSItem') { $n = 'PS_PIPELINE_ITEM' }
    $n = $n -replace '[^A-Za-z0-9_]', '_'
    if ($n -notmatch '^[A-Za-z_]') { $n = 'PS_' + $n }
    if ($opaque) { [void]$script:Opaque.Add($n) }
    return "$SOH$n$STX"
}
function IsOpaque([string]$v) {
    if ($null -eq $v) { return $true }
    foreach ($m in [regex]::Matches($v, "$SOH([^$STX]*)$STX")) {
        if ($script:Opaque.Contains($m.Groups[1].Value)) { return $true }
    }
    return $false
}
function AnyOpaque($vals) {
    foreach ($v in @($vals)) { if (IsOpaque ([string]$v)) { return $true } }
    return $false
}

# Bash-quote one word, rendering unresolved parts as "${NAME}".
#
# Written the way a person would type it into Bash, NOT maximally quoted -- and that is
# a correctness requirement, not style. Several rules read the segment with QUOTED text
# blanked (a name inside a string literal is a mention, not an invocation), so
# `'uv' 'run' 'python' 'gates/corpus_sweep.py'` hid a foreground long job that
# `uv run python gates/corpus_sweep.py` reveals. Likewise a python payload containing
# apostrophes goes out in DOUBLE quotes, as it would be typed, because the durable-record
# rule looks for `open(...,'w')` in the text.
$BARE = '^[A-Za-z0-9_./:@%+=,-]+$'
function Lit([string]$p) {
    if ($p -match $BARE) { return $p }
    if (-not $p.Contains("'")) { return "'" + $p + "'" }
    if ($p -notmatch '["$`\\!]') { return '"' + $p + '"' }
    return "'" + $p.Replace("'", "'\''") + "'"
}
function Q([string]$s) {
    if ($null -eq $s) { $s = '' }
    $out = New-Object Text.StringBuilder
    $parts = $s -split "[$SOH$STX]"
    # -split on both markers alternates literal, name, literal, name ...
    for ($i = 0; $i -lt $parts.Count; $i++) {
        $p = $parts[$i]
        if ($i % 2 -eq 1) { [void]$out.Append('"${' + $p + '}"') }
        elseif ($p.Length -gt 0) { [void]$out.Append((Lit $p)) }
    }
    if ($out.Length -eq 0) { return "''" }
    return $out.ToString()
}

# ------------------------------------------------------------------ values
$script:Assign = @{}      # variable name (lower) -> string[] ; $null = ambiguous
$script:Tables = @{}      # variable name (lower) -> literal hashtable (key -> value AST); $null = ambiguous
$script:AssignAst = @{}   # variable name (lower) -> the one assigned AST; $null = ambiguous
$script:AssignAll = @{}   # variable name (lower) -> EVERY assigned AST (for target resolution)

# WHAT COULD NOT BE RESOLVED. A write or delete whose TARGET this script cannot name --
# an unresolvable splat, a method on an unknown object, Invoke-Expression of computed
# text -- is reported here rather than dropped, and the hook ASKS on it. Dropping it was
# the review finding: a construct the translator did not model reached NO rule, which is
# a narrowing step reporting success (AUDIT-2026-09-24 HK-1 follow-up).
$script:Unknown = New-Object Collections.Generic.List[string]
function Unknown([string]$why) { if (-not $script:Unknown.Contains($why)) { $script:Unknown.Add($why) } }
$script:Depth = 0

function Flatten($ast) {
    # An expression we do not evaluate: keep what it NAMES. `Join-Path $env:X4_REFERENCE
    # 'libraries'` becomes "${X4_REFERENCE}/libraries", which is what it means.
    $bits = @()
    foreach ($n in $ast.FindAll({ param($x) $x -is [StringConstantExpressionAst] -or $x -is [VariableExpressionAst] }, $true)) {
        if ($n -is [StringConstantExpressionAst]) {
            if ($n.Parent -is [CommandAst] -and $n.Parent.CommandElements[0] -eq $n) { continue }   # a command NAME
            $bits += $n.Value
        } else { $bits += (VarValue $n) | Select-Object -First 1 }
    }
    if ($bits.Count -eq 0) { return @(UVar 'PS_EXPR') }
    return @(($bits -join '/'))
}

function VarValue([VariableExpressionAst]$v) {
    $name = $v.VariablePath.UserPath
    $key = $name.ToLowerInvariant()
    if ($key -eq 'home') { return @(UVar 'HOME' $false) }
    if ($key -like 'env:*') { return @(UVar $name $false) }
    if ($key -eq 'pwd') { return @('.') }
    if ($key -eq 'null' -or $key -eq 'true' -or $key -eq 'false') { return @('') }
    if ($script:Assign.ContainsKey($key) -and $null -ne $script:Assign[$key]) { return $script:Assign[$key] }
    # A variable holding COMMAND OUTPUT (`$t = Join-Path ...`, `$d = Get-Item ...`, a
    # foreach loop variable) or the pipeline item: resolved to the paths it holds when
    # they can be read statically, so it denies exactly as the literal spelling does.
    if ($script:AssignAll.ContainsKey($key) -or
        $key -eq '_' -or $key -eq 'psitem') {
        $r = Finalize (Resolve-Target $v)
        if ($r.known -and -not $r.filter -and @($r.paths).Count -ge 1) { return @($r.paths) }
    }
    return @(UVar $name)
}

function Vals($ast) {
    if ($null -eq $ast) { return @() }
    if ($ast -is [StringConstantExpressionAst]) { return @($ast.Value) }
    if ($ast -is [ConstantExpressionAst]) { return @([string]$ast.Value) }
    if ($ast -is [VariableExpressionAst]) { return VarValue $ast }
    if ($ast -is [ExpandableStringExpressionAst]) {
        $s = $ast.Value
        foreach ($n in $ast.NestedExpressions) {
            $rep = if ($n -is [VariableExpressionAst]) { (VarValue $n) | Select-Object -First 1 }
                   else { @((Finalize (Resolve-Target $n)).paths) | Select-Object -First 1 }
            # NestedExpressions carry their own source text; the Value holds it verbatim.
            $s = $s.Replace($n.Extent.Text, [string]$rep)
        }
        return @($s)
    }
    if ($ast -is [ArrayLiteralAst]) { $r = @(); foreach ($e in $ast.Elements) { $r += Vals $e }; return $r }
    if ($ast -is [CommandExpressionAst]) { return Vals $ast.Expression }
    if ($ast -is [ParenExpressionAst] -or $ast -is [ArrayExpressionAst]) {
        $inner = if ($ast -is [ParenExpressionAst]) { $ast.Pipeline } else { $ast.SubExpression }
        # `@('a','b')` holds a STATEMENT BLOCK: read its one statement, or the elements
        # were flattened into the single path 'a/b' (found by this lane's replay).
        if ($inner -is [StatementBlockAst] -and $inner.Statements.Count -eq 1) { $inner = $inner.Statements[0] }
        if ($inner -is [PipelineAst] -and $inner.PipelineElements.Count -eq 1 -and $inner.PipelineElements[0] -is [CommandExpressionAst]) {
            return Vals $inner.PipelineElements[0].Expression
        }
        return Flatten $ast
    }
    if ($ast -is [BinaryExpressionAst] -and $ast.Operator -eq [TokenKind]::Plus) {
        $l = @(Vals $ast.Left); $r = @(Vals $ast.Right)
        if ($l.Count -eq 1 -and $r.Count -eq 1) { return @($l[0] + $r[0]) }
    }
    return Flatten $ast
}

# ------------------------------------------------------------------ commands
$ALIASES = @{
    'rm' = 'remove-item'; 'del' = 'remove-item'; 'erase' = 'remove-item'; 'ri' = 'remove-item'
    'rd' = 'remove-item'; 'rmdir' = 'remove-item'
    'mv' = 'move-item'; 'move' = 'move-item'; 'mi' = 'move-item'
    'cp' = 'copy-item'; 'copy' = 'copy-item'; 'cpi' = 'copy-item'
    'ren' = 'rename-item'; 'rni' = 'rename-item'
    'sc' = 'set-content'; 'ac' = 'add-content'; 'clc' = 'clear-content'; 'ni' = 'new-item'
    'md' = 'new-item'; 'mkdir' = 'new-item'
    'cd' = 'set-location'; 'sl' = 'set-location'; 'chdir' = 'set-location'; 'pushd' = 'push-location'
    'gci' = 'get-childitem'; 'ls' = 'get-childitem'; 'dir' = 'get-childitem'; 'gi' = 'get-item'
    'iwr' = 'invoke-webrequest'; 'irm' = 'invoke-restmethod'; 'cli' = 'clear-item'
    'sp' = 'set-itemproperty'; 'rp' = 'remove-itemproperty'; 'cpp' = 'copy-itemproperty'; 'mp' = 'move-itemproperty'
    'si' = 'set-item'
    'sls' = 'select-string'; 'iex' = 'invoke-expression'; 'saps' = 'start-process'; 'start' = 'start-process'
    'tee' = 'tee-object'; 'where' = 'where-object'; '?' = 'where-object'; '%' = 'foreach-object'
    'foreach' = 'foreach-object'; 'select' = 'select-object'; 'sort' = 'sort-object'
}

function Canon([string]$name) {
    if (-not $name) { return '' }
    $n = [IO.Path]::GetFileName($name.Replace('\', '/')).ToLowerInvariant()
    if ($n -like '*.exe') { $n = $n.Substring(0, $n.Length - 4) }
    if ($ALIASES.ContainsKey($n)) { return $ALIASES[$n] }
    return $n
}

# A SPLAT (`Remove-Item @p`) is invisible to StaticParameterBinder: it binds nothing, so the
# delete had no path and reached no rule. A literal hashtable assigned in the same command
# is read here as if its keys were written as parameters. $script:SplatFor pins the table
# to the binding of the command that carries the splat, so an upstream Get-ChildItem bound
# in Scan-Upstream never reads the downstream command's splat.
$script:SplatNow = @{}
$script:SplatFor = $null
$SPLAT_ALIASES = @{ 'pspath' = 'literalpath'; 'lp' = 'literalpath'; 'fullname' = 'literalpath' }
function Bound($binding, [string[]]$names) {
    if ($null -ne $binding) {
        foreach ($n in $names) {
            if ($binding.BoundParameters.ContainsKey($n)) { return $binding.BoundParameters[$n] }
        }
    }
    if ($null -ne $script:SplatFor -and [object]::ReferenceEquals($binding, $script:SplatFor)) {
        foreach ($n in $names) {
            $want = $n.ToLowerInvariant()
            foreach ($k in $script:SplatNow.Keys) {
                $kk = if ($SPLAT_ALIASES.ContainsKey($k)) { $SPLAT_ALIASES[$k] } else { $k }
                if ($kk -eq $want) { return [pscustomobject]@{ Value = $script:SplatNow[$k]; ConstantValue = $null } }
            }
        }
    }
    return $null
}
function BVals($binding, [string[]]$names) {
    $b = Bound $binding $names
    if ($null -eq $b) { return @() }
    if ($null -ne $b.Value) { return @(Vals $b.Value) }
    if ($null -ne $b.ConstantValue) { return @([string]$b.ConstantValue) }
    return @()
}
function IsSet($binding, [string]$name) {
    $b = Bound $binding @($name)
    if ($null -eq $b) { return $false }
    if ($b.ConstantValue -is [bool]) { return $b.ConstantValue }
    if ($b.Value -is [VariableExpressionAst] -and $b.Value.VariablePath.UserPath -eq 'false') { return $false }
    return $true
}

$SOURCES = @('get-childitem', 'get-item')
$NARROWERS = @('where-object', 'select-object', 'select-string')

# ------------------------------------------------------------------ targets
# WHAT A VALUE NAMES AS A PATH (v3.3.0 release review, finding 1 -- CRITICAL). A delete
# target reaching Remove-Item through the PIPELINE, or through a variable holding command
# output, became `"${PS_PIPELINE_INPUT}"` / `"${t}"` -- an unresolved operand naming no
# root, so the delete ALLOWED while the literal spelling hard-denied. One mechanism now
# answers "which paths does this AST hold" for every writer: literals, variables assigned
# a literal OR command output earlier in the same command, foreach loop variables, `$_`,
# `.FullName`-style members, `(Join-Path ...)`, and the producers Get-Item /
# Get-ChildItem / Resolve-Path / Convert-Path / Join-Path / Write-Output. What it cannot
# read comes back known=$false, and a writer given such a target reports it UNKNOWN (ask).
#
# Result: @{ known; paths; filter; children; recurse }. `children` = the entries UNDER each
# path (Get-ChildItem), expanded by Finalize into `<p>/*` -- the spelling the Bash rules
# judge with glob_covers, so `gci <game> | Remove-Item` denies as `rm -rf <game>/*` does
# (finding 5).
$script:RDepth = 0
function RT([bool]$known, $paths, $filter = $null, [bool]$children = $false, [bool]$recurse = $false) {
    return @{ known = $known; paths = @($paths); filter = $filter; children = $children; recurse = $recurse }
}
function Unres($ast) { return (RT $false @(Flatten $ast)) }
$PATH_MEMBERS = @('fullname', 'pspath', 'path', 'providerpath', 'literalpath', 'fullpath')

function Finalize($r) {
    if ($null -eq $r) { return (RT $false @(UVar 'PS_EXPR')) }
    if ($r.children -and -not $r.filter) {
        $kids = @($r.paths | ForEach-Object { ([string]$_).TrimEnd('/', '\') + '/*' })
        return (RT $r.known $kids $null $false $r.recurse)
    }
    return $r
}

function Resolve-Target($e) {
    if ($null -eq $e -or $script:RDepth -gt 12) { return (RT $false @(UVar 'PS_EXPR')) }
    $script:RDepth++
    try { return (Resolve-Target1 $e) } finally { $script:RDepth-- }
}

function Resolve-Target1($e) {
    if ($e -is [StringConstantExpressionAst] -or $e -is [ConstantExpressionAst] -or
        $e -is [ExpandableStringExpressionAst] -or $e -is [BinaryExpressionAst]) {
        $v = @(Vals $e)
        return (RT (-not (AnyOpaque $v)) $v)
    }
    if ($e -is [ArrayLiteralAst]) {
        $known = $true; $ps = @()
        foreach ($x in $e.Elements) { $r = Finalize (Resolve-Target $x); $known = $known -and $r.known; $ps += $r.paths }
        return (RT $known $ps)
    }
    if ($e -is [CommandExpressionAst]) { return (Resolve-Target $e.Expression) }
    if ($e -is [ParenExpressionAst]) { return (Resolve-Output $e.Pipeline) }
    if ($e -is [SubExpressionAst] -or $e -is [ArrayExpressionAst]) {
        $sb = $e.SubExpression
        if ($sb.Statements.Count -eq 1) { return (Resolve-Output $sb.Statements[0]) }
        return (Unres $e)
    }
    if ($e -is [PipelineAst] -or $e -is [StatementBlockAst]) { return (Resolve-Output $e) }
    if ($e -is [ConvertExpressionAst]) { return (Resolve-Target $e.Child) }
    if ($e -is [VariableExpressionAst]) {
        $name = $e.VariablePath.UserPath
        $k = $name.ToLowerInvariant()
        if ($k -like 'env:*' -or $k -eq 'home' -or $k -eq 'pwd' -or $k -eq 'null') { return (RT $true @(VarValue $e)) }
        if ($k -eq '_' -or $k -eq 'psitem') {
            $up = ItemSource $e
            if ($null -ne $up -and $up.known) { return $up }
            return (RT $false @(UVar 'PS_PIPELINE_ITEM'))
        }
        if ($script:Assign.ContainsKey($k) -and $null -ne $script:Assign[$k]) {
            $v = @($script:Assign[$k]); return (RT (-not (AnyOpaque $v)) $v)
        }
        # Assigned more than once (a loop body, an if/else): the UNION of every value --
        # known only when all of them are. Reading one would under-report the targets.
        if ($script:AssignAll.ContainsKey($k) -and $script:AssignAll[$k].Count -eq 1) {
            return (Resolve-Target $script:AssignAll[$k][0])     # keeps a gci's filter
        }
        if ($script:AssignAll.ContainsKey($k)) {
            $known = $true; $ps = @()
            foreach ($a in $script:AssignAll[$k]) {
                $r = Finalize (Resolve-Target $a)
                $known = $known -and $r.known -and -not $r.filter
                $ps += @($r.paths)
            }
            return (RT $known $ps)
        }
        return (RT $false @(UVar $name))
    }
    if ($e -is [InvokeMemberExpressionAst]) {
        if ($e.Static -and $e.Expression -is [TypeExpressionAst]) {
            $tail = TypeTail $e.Expression.TypeName.FullName
            $mn = MemberName $e
            if ($tail -eq 'path' -and $mn -in 'combine', 'join') {
                $known = $true; $bits = @()
                foreach ($x in $e.Arguments) {
                    $r = Finalize (Resolve-Target $x)
                    if (@($r.paths).Count -ne 1) { $known = $false }
                    $known = $known -and $r.known
                    $bits += @($r.paths)[0]
                }
                return (RT $known @(($bits | ForEach-Object { ([string]$_).TrimEnd('/', '\') }) -join '/'))
            }
            if (($tail -eq 'path' -and $mn -eq 'getfullpath') -or
                ($mn -eq 'new' -and $FILE_OBJECT_TAILS -contains $tail)) {
                if ($e.Arguments.Count -ge 1) { return (Resolve-Target $e.Arguments[0]) }
            }
        }
        return (Unres $e)
    }
    if ($e -is [MemberExpressionAst]) {
        $mn = MemberName $e
        if (-not $e.Static -and $PATH_MEMBERS -contains $mn) { return (Resolve-Target $e.Expression) }
        # `.Name` of a file object is its LEAF: `Join-Path $dst $_.Name` over gci output.
        if (-not $e.Static -and $mn -eq 'name') {
            $r = Finalize (Resolve-Target $e.Expression)
            return (RT $r.known @($r.paths | ForEach-Object { LeafOf $_ }))
        }
        return (Unres $e)
    }
    return (Unres $e)
}

# The objects a pipeline (or a single statement) OUTPUTS, as paths.
function Resolve-Output($p) {
    if ($p -is [StatementBlockAst]) {
        if ($p.Statements.Count -eq 1) { return (Resolve-Output $p.Statements[0]) }
        return (RT $false @(UVar 'PS_EXPR'))
    }
    if ($p -isnot [PipelineAst]) { return (Unres $p) }
    $els = @($p.PipelineElements)
    $last = $els[$els.Count - 1]
    if ($last -is [CommandExpressionAst]) {
        if ($els.Count -eq 1) { return (Resolve-Target $last.Expression) }
        return (Unres $p)
    }
    if ($last -is [CommandAst]) { return (Resolve-Produced $last $p) }
    return (Unres $p)
}

# The paths a bound parameter holds, or $null when it is not bound.
function BTarget($binding, [string[]]$names) {
    $b = Bound $binding $names
    if ($null -eq $b) { return $null }
    if ($null -ne $b.Value) { return (Resolve-Target $b.Value) }
    if ($null -ne $b.ConstantValue) { return (RT $true @([string]$b.ConstantValue)) }
    return $null
}

$PATH_PRODUCERS = @('get-item', 'resolve-path', 'convert-path', 'get-itemproperty')
$NON_FS_PROVIDER = '(?i)^(env|variable|function|alias|hklm|hkcu|hkcr|hku|hkcc|registry|cert|wsman)(::|:)'
function LeafOf([string]$p) {
    $t = $p.TrimEnd('/', '\')
    $cut = [Math]::Max($t.LastIndexOf('/'), $t.LastIndexOf('\'))
    if ($cut -ge 0) { return $t.Substring($cut + 1) }
    return $t
}
function ParentOf([string]$p) {
    $t = $p.TrimEnd('/', '\')
    $cut = [Math]::Max($t.LastIndexOf('/'), $t.LastIndexOf('\'))
    if ($cut -gt 0) { return $t.Substring(0, $cut) }
    return '.'
}
# What ONE command at the end of a pipeline outputs, as paths.
function Resolve-Produced([CommandAst]$cmd, $pipe) {
    $cn = Canon $cmd.GetCommandName()
    $b = $null
    try { $b = [StaticParameterBinder]::BindCommand($cmd, $true) } catch { $b = $null }
    $idx = if ($pipe -is [PipelineAst]) { [array]::IndexOf(@($pipe.PipelineElements), $cmd) } else { -1 }
    $fed = { if ($idx -gt 0) { Scan-Upstream $pipe $idx } else { $null } }
    if ($cn -eq 'join-path') {
        $base = BTarget $b @('Path')
        if ($null -eq $base) { $base = & $fed }
        $child = BTarget $b @('ChildPath')
        if ($null -eq $base -or $null -eq $child) { return (Unres $cmd) }
        $base = Finalize $base; $child = Finalize $child
        # Every base with every child: a loop variable over a literal list is several.
        $known = $base.known -and $child.known
        $tails = @($child.paths)
        $extra = Bound $b @('AdditionalChildPath')
        if ($null -ne $extra) {
            $ex = Finalize (Resolve-Target $extra.Value)
            $known = $known -and $ex.known
            $tails = @($tails | ForEach-Object { $_ + '/' + (@($ex.paths) -join '/') })
        }
        $out = @()
        foreach ($bp in @($base.paths)) { foreach ($tp in $tails) { $out += ([string]$bp).TrimEnd('/', '\') + '/' + $tp } }
        return (RT $known $out)
    }
    if ($cn -eq 'split-path') {
        $base = BTarget $b @('Path', 'LiteralPath')
        if ($null -eq $base) { $base = & $fed }
        if ($null -eq $base) { return (Unres $cmd) }
        $base = Finalize $base
        $leaf = IsSet $b 'Leaf'
        return (RT $base.known @($base.paths | ForEach-Object { if ($leaf) { LeafOf $_ } else { ParentOf $_ } }))
    }
    if ($PATH_PRODUCERS -contains $cn -or $SOURCES -contains $cn) {
        $base = BTarget $b @('Path', 'LiteralPath')
        if ($null -eq $base) { $base = & $fed }
        if ($null -eq $base) { $base = RT $true @('.') }
        $base = Finalize $base
        if ($cn -ne 'get-childitem') { return $base }
        $f = @(BVals $b @('Filter', 'Include')) | Select-Object -First 1
        return (RT $base.known $base.paths $f $true (IsSet $b 'Recurse'))
    }
    if ($cn -in 'write-output', 'echo') {
        $v = BTarget $b @('InputObject')
        if ($null -eq $v) { $v = & $fed }
        if ($null -ne $v) { return $v }
        return (Unres $cmd)
    }
    if ($NARROWERS -contains $cn -or $cn -eq 'sort-object' -or $cn -eq 'foreach-object') {
        $up = & $fed
        if ($null -ne $up) { return $up }
        return (Unres $cmd)
    }
    if ($cn -eq 'new-object' -and $null -ne $b) {
        $t = @(BVals $b @('TypeName')) | Select-Object -First 1
        if ($t -and $FILE_OBJECT_TAILS -contains (TypeTail $t)) {
            $al = Bound $b @('ArgumentList')
            if ($null -ne $al -and $null -ne $al.Value) {
                $v = $al.Value
                if ($v -is [ArrayLiteralAst]) { $v = $v.Elements[0] }
                return (Resolve-Target $v)
            }
        }
    }
    return (Unres $cmd)
}

# `$_` / `$PSItem`: the item of the ForEach-Object / Where-Object whose script block it sits
# in, i.e. whatever feeds THAT command.
function ItemSource([VariableExpressionAst]$v) {
    $n = $v.Parent
    while ($null -ne $n) {
        if ($n -is [ScriptBlockExpressionAst]) {
            $cmd = $n.Parent
            if ($cmd -is [CommandParameterAst]) { $cmd = $cmd.Parent }
            if ($cmd -is [CommandAst] -and (Canon $cmd.GetCommandName()) -in 'foreach-object', 'where-object') {
                $pipe = $cmd.Parent
                if ($pipe -is [PipelineAst]) {
                    $idx = [array]::IndexOf(@($pipe.PipelineElements), $cmd)
                    if ($idx -gt 0) { return (Finalize (Scan-Upstream $pipe $idx)) }
                }
            }
            return $null
        }
        $n = $n.Parent
    }
    return $null
}

# What arrives on this command's pipeline input: $null (nothing), or
# @{known; paths; filter; recurse; children}.
function Upstream([CommandAst]$c) {
    $node = $c
    while ($true) {
        $pipe = $node.Parent
        if ($pipe -is [PipelineAst]) {
            $idx = [array]::IndexOf(@($pipe.PipelineElements), $node)
            if ($idx -gt 0) { return (Scan-Upstream $pipe $idx) }
        }
        # Not fed directly. Inside a script block handed to ForEach-Object/Where-Object,
        # `$_` is THAT command's pipeline item, so look upstream of it instead.
        $outer = $node.Parent
        while ($null -ne $outer -and $outer -isnot [CommandAst]) { $outer = $outer.Parent }
        if ($null -eq $outer) { return $null }
        if ((Canon $outer.GetCommandName()) -notin @('foreach-object', 'where-object')) { return $null }
        $node = $outer
    }
}

# The objects arriving at element $idx of $pipe, walking back over pass-through commands.
# NOT finalized: a Get-ChildItem source keeps `children`, so a narrower downstream can
# still scope it to a filtered find.
function Scan-Upstream([PipelineAst]$pipe, [int]$idx) {
    $narrowed = $false
    $r = $null
    for ($i = $idx - 1; $i -ge 0; $i--) {
        $e = $pipe.PipelineElements[$i]
        if ($e -is [CommandExpressionAst]) { $r = Resolve-Target $e.Expression; break }
        if ($e -isnot [CommandAst]) { break }
        $cn = Canon $e.GetCommandName()
        if ($NARROWERS -contains $cn) { $narrowed = $true; continue }
        if ($cn -eq 'sort-object' -or $cn -eq 'foreach-object') { continue }
        $r = Resolve-Produced $e $pipe
        break
    }
    if ($null -eq $r) { return (RT $false @(UVar 'PS_PIPELINE_INPUT')) }
    if ($narrowed -and -not $r.filter) {
        $r = RT $r.known $r.paths 'PS_FILTERED' $false $r.recurse
    }
    return $r
}

# The paths a destructive cmdlet acts on, seeing through the pipeline where it must.
# Returns @{paths; scoped} -- `scoped` = only SOME entries under each path (a filter).
#
# A DELETE target ($delete: Remove-Item, a Move-Item/Rename-Item SOURCE) that cannot be
# resolved is reported UNKNOWN (ask), never passed as a path that merely names nothing
# (finding 1, CRITICAL): deletes are the one channel with nothing behind them, which is
# also why the Bash half judges an unresolved delete operand conservatively.
#
# A WRITE target that cannot be resolved is NOT: it goes to the rules as its text, which
# is the Bash half's standing write convention (an unresolved operand fires only when it
# names a root or a root variable). MEASURED before scoping it this way, over the 1,524
# historical PowerShell commands: failing closed on writes too added 28 asks on routine
# work (Copy-Item/Set-Content/New-Item into scratch dirs through loop variables), every
# one a false positive -- friction that could not be scoped away, so it was not shipped.
function Targets([CommandAst]$c, $binding, [string[]]$names, [bool]$delete = $false) {
    $r = BTarget $binding $names
    if ($null -eq $r) {
        $r = Upstream $c
        if ($null -eq $r) {
            # A writing cmdlet with NO resolvable target is not a no-op to the guard: it is
            # an UNKNOWN target (a splat, a runtime-only value), and the hook asks.
            Unknown "$($c.GetCommandName()): no target path could be resolved"
            return @{ paths = @(); scoped = $false }
        }
    }
    $r = Finalize $r
    # A NON-FILESYSTEM provider path is not a file: `Remove-Item Env:X4_GAME` unsets a
    # variable. Read as a path it named the root its variable name spells (MEASURED in
    # this lane's replay: an ask on clearing X4_* variables before an installer test).
    $r = RT $r.known @($r.paths | Where-Object { [string]$_ -notmatch $NON_FS_PROVIDER }) $r.filter $false $r.recurse
    if ($delete -and (-not $r.known -or (AnyOpaque $r.paths))) {
        Unknown "$($c.GetCommandName()): a delete target the guard cannot resolve: $($c.Extent.Text)"
    }
    return @{ paths = @($r.paths); scoped = [bool]$r.filter; filter = $r.filter; recurse = $r.recurse }
}

# A write DESTINATION (`-Destination`, `-DestinationPath`, `-OutFile` ...): the paths it
# resolves to (a variable holding Join-Path/Get-Item output included), or its text. The
# write convention above: no UNKNOWN. $null when not given at all.
function WriteDest($binding, [string[]]$names, [string]$what) {
    $r = BTarget $binding $names
    if ($null -eq $r) { return $null }
    return @((Finalize $r).paths)
}

function Words([string[]]$ws) { return (($ws | ForEach-Object { Q $_ }) -join ' ') }

function Redirs([CommandBaseAst]$c) {
    $r = ''
    foreach ($x in $c.Redirections) {
        if ($x -isnot [FileRedirectionAst]) { continue }
        $loc = $x.Location
        if ($loc -is [VariableExpressionAst] -and $loc.VariablePath.UserPath -eq 'null') { continue }
        $t = @(Vals $loc) | Select-Object -First 1
        $op = if ($x.Append) { '>>' } else { '>' }
        $r += " $op " + (Q $t)
    }
    return $r
}

$WRITERS = @('remove-item', 'move-item', 'copy-item', 'rename-item', 'set-content', 'out-file',
             'add-content', 'clear-content', 'new-item', 'clear-item', 'set-itemproperty',
             'remove-itemproperty', 'clear-itemproperty', 'new-itemproperty', 'rename-itemproperty',
             'set-acl', 'copy-itemproperty', 'move-itemproperty', 'expand-archive', 'compress-archive')

#: Verbs whose cmdlets do not write files (reading, formatting, stream output, flow). An
#: UNKNOWN cmdlet with any OTHER verb that names a protected path is reported (the hook
#: checks the path against its roots and asks): the translator cannot know every module's
#: cmdlets, and a write it does not model must not read as no write (review item 6).
#: The write-capable cmdlets that DO carry one of these verbs are mapped explicitly
#: above the default: Out-File, Add-Content, New-Item(Property), Start-Transcript,
#: Start-Process -Redirect*, Invoke-WebRequest/RestMethod -OutFile, Add-Type -OutputAssembly.
$SAFE_VERBS = @('get', 'test', 'select', 'format', 'measure', 'join', 'split', 'resolve',
                'convertto', 'convertfrom', 'compare', 'find', 'group', 'sort', 'where', 'foreach',
                'wait', 'show', 'read', 'write', 'trace', 'import', 'out', 'start', 'stop', 'push',
                'pop', 'enter', 'exit', 'debug', 'receive', 'invoke', 'new', 'add', 'use', 'tee',
                'suspend', 'resume', 'confirm', 'approve', 'search', 'watch', 'ping', 'step')

function Translate-Command([CommandAst]$c) {
    $lines = New-Object Collections.Generic.List[string]
    $raw = $c.GetCommandName()
    if ($null -eq $raw) {
        $first = $c.CommandElements[0]
        if ($first -is [ScriptBlockExpressionAst]) { return $lines }   # its body is walked on its own
        $raw = @(Vals $first) | Select-Object -First 1
    }
    $name = Canon $raw
    $binding = $null
    try { $binding = [StaticParameterBinder]::BindCommand($c, $true) } catch { $binding = $null }
    if ($null -eq $binding) { $binding = [pscustomobject]@{ BoundParameters = @{} } }
    $red = Redirs $c
    # Splats on this command: a literal hashtable is resolved; anything else leaves the
    # parameters UNKNOWN.
    $script:SplatNow = @{}; $script:SplatFor = $binding
    $splatUnknown = ''
    foreach ($e in $c.CommandElements) {
        if ($e -is [VariableExpressionAst] -and $e.Splatted) {
            $key = $e.VariablePath.UserPath.ToLowerInvariant()
            if ($script:Tables.ContainsKey($key) -and $null -ne $script:Tables[$key]) {
                foreach ($k in $script:Tables[$key].Keys) { $script:SplatNow[$k] = $script:Tables[$key][$k] }
            } else { $splatUnknown = $e.VariablePath.UserPath }
        }
    }
    if ($WRITERS -contains $name) {
        if ($splatUnknown) { Unknown "$raw @${splatUnknown}: splatted parameters that cannot be resolved" }
    }
    switch ($name) {
        'remove-item' {
            $t = Targets $c $binding @('Path', 'LiteralPath') $true
            $pat = @(BVals $binding @('Filter', 'Include')) | Select-Object -First 1
            if ($pat -and $pat -ne '*' -and $pat -ne '*.*') { $t.scoped = $true; $t.filter = $pat }
            foreach ($p in $t.paths) {
                if ($t.scoped) { $lines.Add("find $(Q $p) -name $(Q $t.filter) -delete$red") }
                else { $lines.Add("rm -rf $(Q $p)$red") }
            }
            return $lines
        }
        { $_ -in 'move-item', 'copy-item' } {
            $t = Targets $c $binding @('Path', 'LiteralPath') ($name -eq 'move-item')
            $dst = @(WriteDest $binding @('Destination') $raw) | Select-Object -First 1
            if (-not $dst) { $dst = '.' }
            $verb = if ($name -eq 'move-item') { 'mv' } else { 'cp -r' }
            if ($t.paths.Count) { $lines.Add("$verb $(Words $t.paths) $(Q $dst)$red") }
            return $lines
        }
        'rename-item' {
            $t = Targets $c $binding @('Path', 'LiteralPath') $true
            $nn = @(BVals $binding @('NewName')) | Select-Object -First 1
            foreach ($p in $t.paths) {
                $dst = $nn
                if ($nn -and $nn -notmatch '[/\\]') {
                    $cut = [Math]::Max($p.LastIndexOf('/'), $p.LastIndexOf('\'))
                    $dst = if ($cut -ge 0) { $p.Substring(0, $cut + 1) + $nn } else { $nn }
                }
                $lines.Add("mv $(Q $p) $(Q $dst)$red")
            }
            return $lines
        }
        { $_ -in 'set-content', 'out-file', 'add-content' } {
            $t = Targets $c $binding @('Path', 'LiteralPath', 'FilePath')
            $op = if ($name -eq 'add-content' -or (IsSet $binding 'Append')) { '>>' } else { '>' }
            foreach ($p in $t.paths) { $lines.Add(": $op $(Q $p)$red") }
            return $lines
        }
        # Export-* and Tee-Object write a file only when given one: Export-ModuleMember and
        # `Tee-Object -Variable` take none, and must not read as a write with no target.
        { $_ -like 'export-*' -or $_ -eq 'tee-object' } {
            $ps = @(WriteDest $binding @('Path', 'LiteralPath', 'FilePath') $raw)
            if ($ps.Count -eq 0) { break }
            $op = if (IsSet $binding 'Append') { '>>' } else { '>' }
            foreach ($p in $ps) { $lines.Add(": $op $(Q $p)$red") }
            return $lines
        }
        'clear-item' {
            $t = Targets $c $binding @('Path', 'LiteralPath')
            foreach ($p in $t.paths) { $lines.Add("truncate -s 0 $(Q $p)$red") }
            return $lines
        }
        # Properties and ACLs of a FILE: a modification that truncates nothing.
        { $_ -in 'set-itemproperty', 'remove-itemproperty', 'clear-itemproperty', 'new-itemproperty',
                 'rename-itemproperty', 'set-acl' } {
            $t = Targets $c $binding @('Path', 'LiteralPath')
            foreach ($p in $t.paths) { $lines.Add(": >> $(Q $p)$red") }
            return $lines
        }
        { $_ -in 'copy-itemproperty', 'move-itemproperty' } {
            $d = @(WriteDest $binding @('Destination') $raw) | Select-Object -First 1
            if (IsResolved $d) { $lines.Add(": >> $(Q $d)$red") } else { Unknown "$raw to a destination that cannot be resolved" }
            if ($name -eq 'move-itemproperty') {
                $t = Targets $c $binding @('Path', 'LiteralPath')
                foreach ($p in $t.paths) { $lines.Add(": >> $(Q $p)$red") }
            }
            return $lines
        }
        'expand-archive' {
            $t = Targets $c $binding @('Path', 'LiteralPath')
            $d = @(WriteDest $binding @('DestinationPath') $raw) | Select-Object -First 1
            if (-not $d) { $d = '.' }
            $lines.Add("cp -r $(Words $t.paths) $(Q $d)$red")
            return $lines
        }
        'compress-archive' {
            $d = @(WriteDest $binding @('DestinationPath') $raw) | Select-Object -First 1
            if (-not $d) { Unknown "Compress-Archive with no resolvable -DestinationPath"; return $lines }
            $op = if (IsSet $binding 'Update') { '>>' } else { '>' }
            $lines.Add(": $op $(Q $d)$red")
            return $lines
        }
        { $_ -in 'invoke-webrequest', 'invoke-restmethod' } {
            foreach ($p in @(WriteDest $binding @('OutFile') $raw)) { $lines.Add(": > $(Q $p)$red") }
            return $lines
        }
        'start-transcript' {
            $ps = @(WriteDest $binding @('Path', 'LiteralPath', 'OutputDirectory') $raw)
            $op = if (IsSet $binding 'Append') { '>>' } else { '>' }
            foreach ($p in $ps) { $lines.Add(": $op $(Q $p)$red") }
            return $lines
        }
        'add-type' {
            foreach ($p in @(WriteDest $binding @('OutputAssembly') $raw)) { $lines.Add(": > $(Q $p)$red") }
            return $lines
        }
        'clear-content' {
            $t = Targets $c $binding @('Path', 'LiteralPath')
            foreach ($p in $t.paths) { $lines.Add("truncate -s 0 $(Q $p)$red") }
            return $lines
        }
        'new-item' {
            $paths = @(WriteDest $binding @('Path') $raw)
            if ($paths.Count -eq 0) { $paths = @('.') }
            $nm = @(BVals $binding @('Name')) | Select-Object -First 1
            $type = (@(BVals $binding @('ItemType')) | Select-Object -First 1)
            $isDir = ([IO.Path]::GetFileName($raw.Replace('\', '/')).ToLowerInvariant() -in 'md', 'mkdir') -or ($type -and $type.ToLowerInvariant() -eq 'directory')
            foreach ($p in $paths) {
                $full = if ($nm) { $p.TrimEnd('/', '\') + '/' + $nm } else { $p }
                if ($isDir) { $lines.Add("mkdir -p $(Q $full)$red") }
                elseif (IsSet $binding 'Force') { $lines.Add(": > $(Q $full)$red") }
                else { $lines.Add(": >> $(Q $full)$red") }
            }
            return $lines
        }
        { $_ -in 'set-location', 'push-location' } {
            $p = @(BVals $binding @('Path', 'LiteralPath')) | Select-Object -First 1
            if ($p) { $lines.Add("cd $(Q $p)") }
            return $lines
        }
        'select-string' {
            $pat = @(BVals $binding @('Pattern')) | Select-Object -First 1
            $paths = @(BVals $binding @('Path', 'LiteralPath'))
            $flag = ''
            if ($paths.Count -eq 0) {
                $up = Upstream $c
                if ($null -ne $up -and $up.known) { $paths = $up.paths; if ($up.recurse) { $flag = ' -r' } }
            }
            $lines.Add("grep$flag -e $(Q $pat) $(Words $paths)$red")
            return $lines
        }
        'invoke-expression' {
            $cb = Bound $binding @('Command')
            $ast = if ($null -ne $cb) { $cb.Value } else { $null }
            foreach ($l in (Translate-Code $ast "Invoke-Expression" $c.Extent.Text)) { $lines.Add($l) }
            return $lines
        }
        # .NET file WRITERS built with New-Object (finding 4): `New-Object IO.StreamWriter
        # <p>` truncates <p> exactly as [IO.StreamWriter]::new(<p>) does.
        'new-object' {
            $tn = @(BVals $binding @('TypeName')) | Select-Object -First 1
            $al = Bound $binding @('ArgumentList')
            $args2 = @()
            if ($null -ne $al -and $null -ne $al.Value) {
                $v = $al.Value
                # `New-Object T($a, $b)` binds the PARENTHESISED list as one argument.
                if ($v -is [ParenExpressionAst] -and $v.Pipeline -is [PipelineAst] -and
                    $v.Pipeline.PipelineElements.Count -eq 1 -and $v.Pipeline.PipelineElements[0] -is [CommandExpressionAst]) {
                    $v = $v.Pipeline.PipelineElements[0].Expression
                }
                $args2 = if ($v -is [ArrayLiteralAst]) { @($v.Elements) } else { @($v) }
            }
            if ($tn) { foreach ($l in (DotNet-Call $tn 'new' $args2 $c.Extent.Text)) { $lines.Add($l) } }
            return $lines
        }
        'start-process' {
            $fp = @(BVals $binding @('FilePath')) | Select-Object -First 1
            $al = @(BVals $binding @('ArgumentList'))
            # Start-Process JOINS its argument list into one command line.
            $words = @($fp)
            foreach ($m in [regex]::Matches(($al -join ' '), '"([^"]*)"|(\S+)')) {
                $words += if ($m.Groups[1].Success) { $m.Groups[1].Value } else { $m.Groups[2].Value }
            }
            if ($fp) { $lines.Add((Words $words) + $red) }
            foreach ($p in @(BVals $binding @('RedirectStandardOutput', 'RedirectStandardError'))) {
                $lines.Add(": > $(Q $p)")
            }
            return $lines
        }
    }
    # An UNKNOWN cmdlet with a verb that may write: every argument goes to the hook under
    # a marker verb, and the hook -- which holds the roots -- asks if one names a
    # protected tree (see $SAFE_VERBS).
    $vn = [IO.Path]::GetFileName($raw.Replace('\', '/'))
    if ($vn -match '^([A-Za-z]+)-[A-Za-z]' -and $vn -notmatch '[./\\]' -and
        $SAFE_VERBS -notcontains $Matches[1].ToLowerInvariant()) {
        $uw = @('x4-unknown-cmdlet', $vn)
        for ($i = 1; $i -lt $c.CommandElements.Count; $i++) {
            $e = $c.CommandElements[$i]
            if ($e -is [CommandParameterAst]) {
                if ($null -ne $e.Argument) { $uw += @(Vals $e.Argument) }
            } else { $uw += @(Vals $e) }
        }
        $lines.Add((Words $uw))
    }
    # Anything else -- a native program (git, python, bash -c, cmd /c, pwsh -c) or a
    # harmless cmdlet -- goes through WORD FOR WORD, so the Bash rule set sees it exactly
    # as it would have seen it typed into Bash.
    $w = New-Object Collections.Generic.List[string]
    $w.Add((Q $raw))
    for ($i = 1; $i -lt $c.CommandElements.Count; $i++) {
        $e = $c.CommandElements[$i]
        if ($e -is [CommandParameterAst]) {
            # A parameter name is an identifier, so it goes out BARE: a quoted '-A' is an
            # operand to the rules, not a flag, and `git add -A` must still read as one.
            $pn = '-' + $e.ParameterName
            $w.Add($(if ($pn -match '^-[A-Za-z0-9_?-]+$') { $pn } else { Q $pn }))
            if ($null -ne $e.Argument) { foreach ($v in (Vals $e.Argument)) { $w.Add((Q $v)) } }
        } else { foreach ($v in (Vals $e)) { $w.Add((Q $v)) } }
    }
    $lines.Add(($w -join ' ') + $red)
    return $lines
}

# ------------------------------------------------------------------ methods
# File-system METHODS, static and instance (AUDIT-2026-09-24 HK-1 review item 2). Only
# [IO.File]/[IO.Directory] statics were mapped, so `(Get-Item <p>).Delete()`, a
# `$_.Delete()` over gci output, [FileInfo]/[DirectoryInfo] instances, `[Directory]` under
# `using namespace System.IO` and VisualBasic's FileSystem reached no rule.
#
# Types are matched on the LAST segment of their name, so a `using namespace` spelling and
# the full one agree. A static on any OTHER type is not a file operation -- `[string]::Copy`
# and `[array]::Copy` are ordinary.
$FILE_STATIC_TAILS = @('file', 'directory', 'filesystem')
$FILE_OBJECT_TAILS = @('fileinfo', 'directoryinfo', 'filesysteminfo')
#: Instance members that are a file operation on ANY receiver we cannot identify: an
#: unresolved receiver with one of these is UNKNOWN (ask), never "not a file".
$STRONG_MEMBERS = @('delete', 'moveto', 'encrypt', 'decrypt', 'create', 'createtext',
                    'appendtext', 'openwrite', 'createsubdirectory', 'setaccesscontrol')
#: Members that are ALSO ordinary on strings/arrays/streams ('abc'.Replace, $a.CopyTo):
#: a file operation only when the receiver is identified as a file object.
$AMBIG_MEMBERS = @('copyto', 'replace', 'open')

function TypeTail($typeName) {
    $n = [string]$typeName
    return $n.Split('.')[-1].ToLowerInvariant()
}

function MemberName($m) { return ($m.Member.Extent.Text).Trim("'", '"').ToLowerInvariant() }

function FirstVal($ast) { return @(Vals $ast) | Select-Object -First 1 }

function IsResolved([string]$v) { return ($null -ne $v) -and ($v -notmatch "[$SOH$STX]") }

# What a receiver IS: @{ kind = 'file'; paths; filter } | @{ kind = 'notfile' } |
# @{ kind = 'unknown' }.
function Resolve-FileObject($e, [int]$depth = 0) {
    if ($depth -gt 6 -or $null -eq $e) { return @{ kind = 'unknown' } }
    if ($e -is [StringConstantExpressionAst] -or $e -is [ExpandableStringExpressionAst] -or
        $e -is [ConstantExpressionAst] -or $e -is [ArrayLiteralAst] -or $e -is [HashtableAst]) {
        return @{ kind = 'notfile' }
    }
    if ($e -is [ParenExpressionAst] -or $e -is [SubExpressionAst]) {
        $p = if ($e -is [ParenExpressionAst]) { $e.Pipeline } else { $e.SubExpression }
        if ($p -is [StatementBlockAst] -and $p.Statements.Count -eq 1) { $p = $p.Statements[0] }
        if ($p -is [PipelineAst] -and $p.PipelineElements.Count -ge 1) {
            $last = $p.PipelineElements[$p.PipelineElements.Count - 1]
            if ($last -is [CommandExpressionAst] -and $p.PipelineElements.Count -eq 1) {
                return Resolve-FileObject $last.Expression ($depth + 1)
            }
            if ($last -is [CommandAst]) { return Resolve-Producer $last $p ($depth + 1) }
        }
        return @{ kind = 'unknown' }
    }
    if ($e -is [ConvertExpressionAst]) {
        if ($FILE_OBJECT_TAILS -contains (TypeTail $e.Type.TypeName.FullName)) {
            $v = FirstVal $e.Child
            return @{ kind = 'file'; paths = @($v) }
        }
        return Resolve-FileObject $e.Child ($depth + 1)
    }
    if ($e -is [InvokeMemberExpressionAst] -and $e.Static -and $e.Expression -is [TypeExpressionAst] -and
        (MemberName $e) -eq 'new' -and $FILE_OBJECT_TAILS -contains (TypeTail $e.Expression.TypeName.FullName)) {
        if ($e.Arguments.Count -ge 1) { return @{ kind = 'file'; paths = @(FirstVal $e.Arguments[0]) } }
        return @{ kind = 'unknown' }
    }
    if ($e -is [VariableExpressionAst]) {
        $n = $e.VariablePath.UserPath.ToLowerInvariant()
        if ($n -eq '_' -or $n -eq 'psitem') {
            # The pipeline item of the ForEach-Object/Where-Object this sits in.
            $outer = $e.Parent
            while ($null -ne $outer -and $outer -isnot [CommandAst]) { $outer = $outer.Parent }
            if ($null -eq $outer) { return @{ kind = 'unknown' } }
            $up = Upstream $outer
            if ($null -eq $up) {
                $pipe = $outer.Parent
                if ($pipe -is [PipelineAst]) {
                    $idx = [array]::IndexOf(@($pipe.PipelineElements), $outer)
                    if ($idx -gt 0) { $up = Scan-Upstream $pipe $idx }
                }
            }
            if ($null -eq $up -or -not $up.known) { return @{ kind = 'unknown' } }
            return (FromSource $up)
        }
        if ($script:AssignAst.ContainsKey($n) -and $null -ne $script:AssignAst[$n]) {
            return Resolve-FileObject $script:AssignAst[$n] ($depth + 1)
        }
        return @{ kind = 'unknown' }
    }
    if ($e -is [CommandExpressionAst]) { return Resolve-FileObject $e.Expression ($depth + 1) }
    if ($e -is [PipelineAst]) {
        $last = $e.PipelineElements[$e.PipelineElements.Count - 1]
        if ($last -is [CommandAst]) { return Resolve-Producer $last $e ($depth + 1) }
        if ($last -is [CommandExpressionAst]) { return Resolve-FileObject $last.Expression ($depth + 1) }
    }
    return @{ kind = 'unknown' }
}

# The objects a Get-Item/Get-ChildItem/New-Object at the end of a pipeline produce.
function Resolve-Producer([CommandAst]$cmd, $pipe, [int]$depth) {
    $cn = Canon $cmd.GetCommandName()
    $b = $null
    try { $b = [StaticParameterBinder]::BindCommand($cmd, $true) } catch { $b = $null }
    if ($SOURCES -contains $cn) {
        if ($null -eq $b) { return @{ kind = 'unknown' } }
        $p = @(BVals $b @('Path', 'LiteralPath'))
        if ($p.Count -eq 0) { $p = @('.') }
        $f = @(BVals $b @('Filter', 'Include')) | Select-Object -First 1
        return (FromSource (RT $true $p $f ($cn -eq 'get-childitem')))
    }
    if ($NARROWERS -contains $cn -or $cn -eq 'sort-object') {
        $idx = [array]::IndexOf(@($pipe.PipelineElements), $cmd)
        if ($idx -gt 0) {
            $up = Scan-Upstream $pipe $idx
            if ($null -ne $up -and $up.known) {
                if (-not $up.filter) { $up = RT $true $up.paths 'PS_FILTERED' }
                return (FromSource $up)
            }
        }
        return @{ kind = 'unknown' }
    }
    if ($cn -eq 'new-object' -and $null -ne $b) {
        $t = @(BVals $b @('TypeName')) | Select-Object -First 1
        if ($t -and $FILE_OBJECT_TAILS -contains (TypeTail $t)) {
            $al = @(BVals $b @('ArgumentList'))
            if ($al.Count -ge 1) { return @{ kind = 'file'; paths = @($al[0]) } }
            return @{ kind = 'unknown' }
        }
        if ($t) { return @{ kind = 'notfile' } }
    }
    return @{ kind = 'unknown' }
}

function FromSource($up) {
    # Get-ChildItem's entries become `<p>/*` (Finalize) -- a glob the Bash rules judge with
    # glob_covers, as they judge `rm -rf <p>/*` (finding 5).
    $r = Finalize $up
    return @{ kind = 'file'; paths = @($r.paths); filter = $r.filter }
}

function MemberLines([string]$member, $paths, [string]$filter, $args2) {
    $out = @()
    foreach ($p in $paths) {
        if (-not (IsResolved $p)) {
            Unknown ".${member}() on an object whose path cannot be resolved"
        }
        switch -regex ($member) {
            '^delete$' {
                if ($filter) { $out += "find $(Q $p) -name $(Q $filter) -delete" } else { $out += "rm -rf $(Q $p)" }
            }
            '^(moveto|replace)$' {
                $d = $args2 | Select-Object -First 1
                if (IsResolved $d) { $out += "mv $(Q $p) $(Q $d)" } else { Unknown ".${member}() to a destination that cannot be resolved" }
            }
            '^copyto$' {
                $d = $args2 | Select-Object -First 1
                if (IsResolved $d) { $out += "cp -r $(Q $p) $(Q $d)" } else { Unknown ".${member}() to a destination that cannot be resolved" }
            }
            '^(create|createtext|openwrite|open)$' { $out += ": > $(Q $p)" }
            '^(appendtext|encrypt|decrypt|setaccesscontrol)$' { $out += ": >> $(Q $p)" }
            '^createsubdirectory$' {
                $d = $args2 | Select-Object -First 1
                $out += ": >> $(Q ($p.TrimEnd('/', '\') + '/' + $d))"
            }
        }
    }
    return $out
}

# Text that is RUN as PowerShell -- Invoke-Expression, [scriptblock]::Create,
# $ExecutionContext.InvokeCommand.InvokeScript/NewScriptBlock, [powershell]::AddScript
# (finding 4: only iex was modelled). Readable text is translated in turn; anything else,
# or nesting past the limit, or text that does not parse, is UNKNOWN (ask).
# Only TEXT counts as readable: a literal, an expandable string, a variable, a
# concatenation. A command's output -- `(Get-Content x.ps1 -Raw)` -- flattens to its
# constants (".\x.ps1"), which is not the program it produces.
function Translate-Code($ast, [string]$what, [string]$label) {
    $textual = $null -ne $ast -and (
        $ast -is [StringConstantExpressionAst] -or $ast -is [ExpandableStringExpressionAst] -or
        $ast -is [VariableExpressionAst] -or $ast -is [BinaryExpressionAst])
    $txt = if ($textual) { FirstVal $ast } else { $null }
    if (-not $textual -or -not $txt -or $txt -match "[$SOH$STX]") {
        Unknown "$what of text the guard cannot read: $label"
        return @()
    }
    if ($script:Depth -ge 4) {
        Unknown "$what nested past the guard's depth limit (4)"
        return @()
    }
    $script:Depth++
    try { return @(Translate-Text $txt) }
    catch [FormatException] { Unknown "$what of text that does not parse as PowerShell: $label"; return @() }
    finally { $script:Depth-- }
}

# .NET types in System.IO that write nothing by being constructed or called, or whose
# writes are modelled elsewhere (File/Directory statics, FileInfo/DirectoryInfo methods,
# StreamWriter/FileStream below). Any OTHER System.IO type is unmodelled: its arguments go
# to the hook, which asks when one names a protected tree (finding 4).
$SAFE_IO_TAILS = @('path', 'file', 'directory', 'fileinfo', 'directoryinfo', 'filesysteminfo',
                   'streamreader', 'stringreader', 'stringwriter', 'memorystream', 'binaryreader',
                   'binarywriter', 'textreader', 'textwriter', 'driveinfo', 'searchoption',
                   'fileattributes', 'filemode', 'fileaccess', 'fileshare', 'seekorigin',
                   'fileoptions', 'enumerationoptions', 'ioexception', 'filenotfoundexception',
                   'directorynotfoundexception', 'streamwriter', 'filestream', 'bufferedstream',
                   'unmanagedmemorystream', 'filesystemwatcher', 'notifyfilters',
                   'compressionlevel', 'compressionmode', 'unixfilemode', 'matchtype', 'matchcasing')

# A FileMode (and FileAccess) as the redirect it amounts to: '>' truncates, '>>' writes
# without truncating, '' only reads. An unreadable mode is a write.
function FileModeOp($mode, $access) {
    $m = [string]$mode; $a = [string]$access
    if ($m -match '(?i)truncate|create' -or $m -in '1', '2', '5') { return '>' }
    if ($m -match '(?i)append|openorcreate' -or $m -in '4', '6') { return '>>' }
    if ($m -match '(?i)open' -or $m -eq '3') { if ($a -match '(?i)write' -or $a -in '2', '3') { return '>>' }; return '' }
    if (-not $m) { return '' }
    return '>>'
}

# A value that is a STREAM rather than a path (`[IO.StreamWriter]::new($fs)` where $fs was
# opened just before): its write is modelled where the stream was opened.
function IsStreamValue($ast) {
    $src = $ast
    if ($ast -is [VariableExpressionAst]) {
        $k = $ast.VariablePath.UserPath.ToLowerInvariant()
        if ($script:AssignAst.ContainsKey($k) -and $null -ne $script:AssignAst[$k]) { $src = $script:AssignAst[$k] }
    }
    if ($null -eq $src -or $src -is [StringConstantExpressionAst] -or $src -is [ExpandableStringExpressionAst]) { return $false }
    return ($src.Extent.Text -match '(?i)stream\]::new|new-object\s+(-typename\s+)?(system\.)?io\.\w*stream\b|::open\w*\s*\(|\.open\w*\s*\(|::create\w*\s*\(')
}

function DotNet-Call([string]$typeName, [string]$member, $argAsts, [string]$label) {
    $tail = TypeTail $typeName
    $member = $member.ToLowerInvariant()
    $argAsts = @($argAsts | Where-Object { $null -ne $_ })
    if ($tail -eq 'scriptblock' -and $member -eq 'create') {
        return @(Translate-Code $argAsts[0] '[scriptblock]::Create' $label)
    }
    $op = $null
    if ($tail -eq 'streamwriter' -and $member -eq 'new' -and $argAsts.Count -ge 1) {
        if (IsStreamValue $argAsts[0]) { return @() }
        $app = $argAsts.Count -ge 2 -and $argAsts[1] -is [VariableExpressionAst] -and
               $argAsts[1].VariablePath.UserPath -eq 'true'
        $op = if ($app) { '>>' } else { '>' }
    } elseif ((($tail -eq 'filestream' -and $member -eq 'new') -or ($tail -eq 'file' -and $member -eq 'open')) -and
              $argAsts.Count -ge 1) {
        $mode = if ($argAsts.Count -ge 2) { FirstVal $argAsts[1] } else { '' }
        $acc = if ($argAsts.Count -ge 3) { FirstVal $argAsts[2] } else { '' }
        $op = FileModeOp $mode $acc
    }
    if ($null -ne $op) {
        if (-not $op) { return @() }
        $r = Finalize (Resolve-Target $argAsts[0])
        return @($r.paths | ForEach-Object { ": $op $(Q $_)" })
    }
    # A READ on an unmodelled System.IO type writes nothing (ZipFile::OpenRead, ...).
    if ($member -match '^(openread|opentext|read|get|exists|enumerate|test)') { return @() }
    if ($typeName -match '(?i)^(system\.)?io\.' -and $SAFE_IO_TAILS -notcontains $tail -and $argAsts.Count -ge 1) {
        $uw = @('x4-unknown-cmdlet', "[$typeName]::$member")
        foreach ($x in $argAsts) {
            $r = Finalize (Resolve-Target $x)
            $uw += @($r.paths)
        }
        return @(Words $uw)
    }
    return @()
}

function Translate-Member([InvokeMemberExpressionAst]$m) {
    $member = MemberName $m
    $a = @(); foreach ($x in $m.Arguments) { $a += FirstVal $x }
    if ($m.Static) {
        if ($m.Expression -isnot [TypeExpressionAst]) { return @() }
        $tn = $m.Expression.TypeName.FullName
        $tt = TypeTail $tn
        if ($FILE_STATIC_TAILS -notcontains $tt -or ($tt -eq 'file' -and $member -eq 'open')) {
            return @(DotNet-Call $tn $member @($m.Arguments) $m.Extent.Text)
        }
        $kind = switch -regex ($member) {
            '^(delete|deletefile|deletedirectory)$' { 'rm' ; break }
            '^(write|create|openwrite)'              { 'trunc' ; break }
            '^append'                                { 'append' ; break }
            '^(encrypt|decrypt|setattributes|setlastwritetime|setcreationtime|setlastaccesstime|setaccesscontrol)' { 'append' ; break }
            '^(move|movefile|movedirectory)$'        { 'mv' ; break }
            '^replace$'                              { 'replace' ; break }
            '^(copy|copyfile|copydirectory)$'        { 'cp' ; break }
            default                                  { '' }
        }
        if (-not $kind) { return @() }
        $need = if ($kind -in 'mv', 'cp', 'replace') { 2 } else { 1 }
        if ($a.Count -lt $need) {
            Unknown "[$($m.Expression.TypeName.FullName)]::$($m.Member.Extent.Text)() with no path the guard can read"
            return @()
        }
        # Unresolved is UNKNOWN (ask) -- and the line is still emitted, so a path that
        # names a root through its variable (`"$env:X4_REFERENCE/a"`) still reaches the
        # root rules and a deny still wins, exactly as it does for Remove-Item.
        if (-not (IsResolved $a[0]) -or ($need -eq 2 -and -not (IsResolved $a[1]))) {
            Unknown "[$($m.Expression.TypeName.FullName)]::$($m.Member.Extent.Text)() on a path that cannot be resolved"
        }
        switch ($kind) {
            'rm'      { return @("rm -rf $(Q $a[0])") }
            'trunc'   { return @(": > $(Q $a[0])") }
            'append'  { return @(": >> $(Q $a[0])") }
            'mv'      { return @("mv $(Q $a[0]) $(Q $a[1])") }
            'cp'      { return @("cp -r $(Q $a[0]) $(Q $a[1])") }
            # File.Replace(source, destination, backup): destination is overwritten by source
            'replace' { return @("mv $(Q $a[0]) $(Q $a[1])") }
        }
        return @()
    }
    if ($member -in 'invokescript', 'newscriptblock', 'addscript') {
        return @(Translate-Code (@($m.Arguments)[0]) ".$member()" $m.Extent.Text)
    }
    $strong = $STRONG_MEMBERS -contains $member
    $ambig = $AMBIG_MEMBERS -contains $member
    if (-not ($strong -or $ambig)) { return @() }
    $obj = Resolve-FileObject $m.Expression
    if ($obj.kind -eq 'file') { return @(MemberLines $member $obj.paths $obj.filter $a) }
    if ($obj.kind -eq 'unknown' -and $strong) {
        Unknown "$($m.Extent.Text): a file-system method on an object the guard cannot identify"
    }
    return @()
}

function Collect-Assignments($ast) {
    # A `foreach ($f in <collection>)` loop variable holds each item of <collection>:
    # `foreach ($f in gci <S>) { Remove-Item $f.FullName }` deletes <S>'s entries, and read
    # as an unassigned `$f` it named nothing (the reviewer's pre-arc note). FIRST, because an
    # assignment's value is read eagerly below: `$cfg = "$SP\$eng-x"` inside
    # `foreach ($eng in 'a','b')` must see $eng's collection (measured in the replay).
    foreach ($fe in $ast.FindAll({ param($x) $x -is [ForEachStatementAst] }, $true)) {
        $key = $fe.Variable.VariablePath.UserPath.ToLowerInvariant()
        if ($script:AssignAst.ContainsKey($key)) { $script:AssignAst[$key] = $null }
        else { $script:AssignAst[$key] = $fe.Condition }
        if (-not $script:AssignAll.ContainsKey($key)) { $script:AssignAll[$key] = New-Object Collections.ArrayList }
        [void]$script:AssignAll[$key].Add($fe.Condition)
        $script:Assign[$key] = $null
        $script:Tables[$key] = $null
    }
    foreach ($as in $ast.FindAll({ param($x) $x -is [AssignmentStatementAst] }, $true)) {
        if ($as.Left -isnot [VariableExpressionAst] -or $as.Operator -ne [TokenKind]::Equals) { continue }
        $key = $as.Left.VariablePath.UserPath.ToLowerInvariant()
        $val = $null
        if ($as.Right -is [CommandExpressionAst]) { $val = @(Vals $as.Right.Expression) }
        # A literal hashtable, for splatting: key -> value AST. Anything else assigned to
        # the same name makes it ambiguous ($null), never "the last one wins".
        $tbl = $null
        if ($as.Right -is [CommandExpressionAst] -and $as.Right.Expression -is [HashtableAst]) {
            $tbl = @{}
            foreach ($kv in $as.Right.Expression.KeyValuePairs) {
                $kn = @(Vals $kv.Item1) | Select-Object -First 1
                $v = $kv.Item2
                if ($v -is [PipelineAst] -and $v.PipelineElements.Count -eq 1 -and $v.PipelineElements[0] -is [CommandExpressionAst]) {
                    $v = $v.PipelineElements[0].Expression
                }
                if ($kn) { $tbl[$kn.ToLowerInvariant()] = $v }
            }
        }
        if ($script:AssignAst.ContainsKey($key)) { $script:AssignAst[$key] = $null }
        else { $script:AssignAst[$key] = $as.Right }
        if (-not $script:AssignAll.ContainsKey($key)) { $script:AssignAll[$key] = New-Object Collections.ArrayList }
        [void]$script:AssignAll[$key].Add($as.Right)
        if ($script:Tables.ContainsKey($key)) { $script:Tables[$key] = $null }
        else { $script:Tables[$key] = $tbl }
        if ($script:Assign.ContainsKey($key)) {
            $old = $script:Assign[$key]
            if ($null -eq $old -or $null -eq $val -or (($old -join "`n") -ne ($val -join "`n"))) { $script:Assign[$key] = $null }
        } else { $script:Assign[$key] = $val }
    }
}

function Translate-Text([string]$src) {
    $toks = $null; $errs = $null
    $ast = [Parser]::ParseInput($src, [ref]$toks, [ref]$errs)
    if ($errs.Count -gt 0) {
        $msg = ($errs | Select-Object -First 3 | ForEach-Object { $_.Message }) -join ' | '
        throw [FormatException]::new("does not parse as PowerShell: $msg")
    }
    Collect-Assignments $ast
    $out = New-Object Collections.Generic.List[string]
    $nodes = $ast.FindAll({ param($x) $x -is [CommandAst] -or $x -is [InvokeMemberExpressionAst] }, $true)
    foreach ($n in ($nodes | Sort-Object { $_.Extent.StartOffset })) {
        $got = if ($n -is [CommandAst]) { Translate-Command $n } else { Translate-Member $n }
        foreach ($l in $got) { if ($l) { $out.Add($l) } }
    }
    # A redirect on a bare expression (`'x' > <p>`) has no CommandAst to ride on.
    foreach ($e in $ast.FindAll({ param($x) $x -is [CommandExpressionAst] -and $x.Redirections.Count -gt 0 }, $true)) {
        $r = Redirs $e
        if ($r) { $out.Add(':' + $r) }
    }
    return $out
}

try {
    $stdin = [Console]::OpenStandardInput()
    $ms = New-Object IO.MemoryStream
    $stdin.CopyTo($ms)
    $src = [Text.Encoding]::UTF8.GetString($ms.ToArray())
    if ($src.Length -gt 0 -and $src[0] -eq [char]0xFEFF) { $src = $src.Substring(1) }
    $lines = Translate-Text $src
    Out-Json @{ ok = $true; command = ($lines -join "`n"); unknown = @($script:Unknown) }
} catch [FormatException] {
    Out-Json @{ ok = $false; reason = $_.Exception.Message }
} catch {
    Out-Json @{ ok = $false; reason = ("the translator failed: " + $_.Exception.Message) }
}
exit 0
