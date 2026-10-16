# codex-entry.ps1 -- the fail-closed Windows entry for the toolkit's Codex hooks.
#
#   pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "<root>\.codex\hooks\codex-entry.ps1" <event>
#     || powershell ... (same)          (hooks.json commandWindows; payload on stdin)
#
# Why a wrapper at all (MEASURED on Codex 0.160.0): a hook that crashes, exits non-zero, prints
# unparseable output or a JSON key Codex does not know is "Failed" -- and the tool call RUNS.
# Only a parsed JSON deny blocks. So every path through this file prints a JSON verdict and
# exits 0: the adapter's own answer when it is well-formed, otherwise a fail-closed deny
# (PreToolUse) or an advisory (other events) that names the cause.
#
# Why PowerShell and not bash on Windows (MEASURED): Codex runs hooks through cmd.exe, and
# when Codex is launched from PowerShell `bash` resolves to the WSL stub, which cannot run the
# guard: the hook fails and the command runs. Runs on Windows PowerShell 5.1 and pwsh 7.
#
# Test-only knobs: X4_WRAPPER_TIMEOUT_S (default 50; 25 for session_start), and
# X4_NO_PYTHON_FALLBACK=1 (use X4_PYTHON only, so a missing interpreter is really missing).
# Never logs the environment: hooks inherit all of it, secrets included (MEASURED).

$ErrorActionPreference = 'Stop'
$script:emitted = $false
$script:hookEvent = if ($args.Count -ge 1) { [string]$args[0] } else { '' }

function Write-Verdict([string]$text) {
    if ($text.Length -gt 0) { [Console]::Out.Write($text) }
    [Console]::Out.Flush()
    $script:emitted = $true
}

function Write-Fail([string]$cause) {
    # The cause goes into a JSON string literal: keep an allowlist, never escape by hand.
    $safe = [regex]::Replace([string]$cause, '[^A-Za-z0-9 ._:/()=,+-]', '?')
    if ($safe.Length -gt 300) { $safe = $safe.Substring(0, 300) }
    switch ($script:hookEvent) {
        'session_start' {
            Write-Verdict ('{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"X4 GUARDS NOT LIVE: the toolkit session hook failed (' + $safe + '). Tell the user the X4 guards may not be running and ask them to run x4doctor."}}')
        }
        'post_tool_use' {
            Write-Verdict ('{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"X4 VALIDATION DID NOT RUN: the toolkit hook failed (' + $safe + ')."}}')
        }
        default {
            Write-Verdict ('{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"X4 GUARD INERT: the Codex hook wrapper failed (' + $safe + '). NOTHING was checked; this is a refusal, not a verdict. Ask the user to run x4doctor."}}')
        }
    }
}

function Find-Python {
    $cands = @()
    if ($env:X4_PYTHON) { $cands += , @($env:X4_PYTHON) }
    if ($env:X4_NO_PYTHON_FALLBACK -ne '1') { $cands += , @('py', '-3'); $cands += , @('python3'); $cands += , @('python') }
    foreach ($c in $cands) {
        $cmd = Get-Command $c[0] -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cmd) { return , (@($cmd.Source) + @($c | Select-Object -Skip 1)) }
    }
    return $null
}

# A strict string-only JSON grammar rejects PowerShell's permissive JSON extensions,
# duplicate keys and nonstring verdict fields before anything reaches the host.
$script:JStr = '"(?:[^"\\\x00-\x1f]|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})*"'
$script:JPair = $script:JStr + '[ \t]*:[ \t]*' + $script:JStr
$script:JBody = '\A\{[ \t]*"hookSpecificOutput"[ \t]*:[ \t]*\{[ \t]*(' + $script:JPair + '(?:[ \t]*,[ \t]*' + $script:JPair + ')*)[ \t]*\}[ \t]*\}\z'
function Test-Body([string]$body) {
    $match = [regex]::Match($body, $script:JBody)
    if (-not $match.Success) { return 'adapter output is not a verdict JSON' }
    # ConvertFrom-Json can silently replace an unpaired surrogate. Remove valid pairs
    # from a temporary view, then reject any remaining actual (odd-backslash) escape.
    $withoutPairs = [regex]::Replace($body, '(?<!\\)((?:\\\\)*)\\u[dD][89abAB][0-9a-fA-F]{2}\\u[dD][c-fC-F][0-9a-fA-F]{2}', '$1')
    if ($withoutPairs -match '(?<!\\)(?:\\\\)*\\u[dD][89abABc-fC-F]') { return 'adapter output has unpaired Unicode' }
    $eventName = switch ($script:hookEvent) { 'session_start' { 'SessionStart' }; 'post_tool_use' { 'PostToolUse' }; default { 'PreToolUse' } }
    $allowed = @('"hookEventName"', '"additionalContext"')
    if ($eventName -ceq 'PreToolUse') { $allowed += @('"permissionDecision"', '"permissionDecisionReason"') }
    $seen = @{}
    foreach ($pair in [regex]::Matches($match.Groups[1].Value, '(' + $script:JStr + ')[ \t]*:[ \t]*(' + $script:JStr + ')')) {
        $key, $value = $pair.Groups[1].Value, $pair.Groups[2].Value
        if ($allowed -cnotcontains $key) { return 'hookSpecificOutput has an unknown key' }
        if ($seen.ContainsKey($key)) { return 'hookSpecificOutput repeats a key' }
        $seen[$key] = $true
        if ($key -ceq '"hookEventName"' -and $value -cne ('"' + $eventName + '"')) { return 'adapter output names another event' }
        if ($key -ceq '"permissionDecision"' -and $value -cne '"deny"') { return 'adapter answered a permissionDecision other than deny' }
    }
    if (-not $seen.ContainsKey('"hookEventName"')) { return 'adapter output names no event' }
    return $null
}

function Stop-Adapter($proc) {
    try { $proc.Kill() } catch { }
}

function Start-Adapter([string]$exe, [string]$arguments) {
    # Framework Process.Start can leak the host's OTHER inheritable pipe handles
    # to descendants. An explicit Windows handle list isolates exactly these three
    # pipes. Start suspended, attach an owned job, then resume: timeout/cleanup
    # cannot leave an orphan retaining any of the adapter's output handles.
    # https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
public sealed class X4GuardProcess : IDisposable {
    [StructLayout(LayoutKind.Sequential)] struct SA { public int length; public IntPtr descriptor; public int inherit; }
    [StructLayout(LayoutKind.Sequential)] struct SI {
        public int cb; public IntPtr reserved, desktop, title;
        public int x, y, xsize, ysize, xchars, ychars, fill, flags;
        public short show, reservedSize; public IntPtr reservedBytes, input, output, error;
    }
    [StructLayout(LayoutKind.Sequential)] struct SIX { public SI si; public IntPtr attrs; }
    [StructLayout(LayoutKind.Sequential)] struct PI { public IntPtr process, thread; public int pid, tid; }
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool CreatePipe(out IntPtr read, out IntPtr write, ref SA sa, int size);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetHandleInformation(IntPtr h, uint mask, uint flags);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool InitializeProcThreadAttributeList(IntPtr p, int n, int flags, ref IntPtr size);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool UpdateProcThreadAttribute(IntPtr p, uint flags, IntPtr attr, IntPtr value, IntPtr size, IntPtr previous, IntPtr returned);
    [DllImport("kernel32.dll")] static extern void DeleteProcThreadAttributeList(IntPtr p);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool CreateProcess(string exe, StringBuilder cmd, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr env, string cwd, ref SIX si, out PI pi);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr sa, string name);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateJobObject(IntPtr job, uint code);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateProcess(IntPtr process, uint code);
    [DllImport("kernel32.dll", SetLastError=true)] static extern uint WaitForSingleObject(IntPtr h, uint ms);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr h, out uint code);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    IntPtr process, job;
    public FileStream InputStream, OutputStream, ErrorStream;
    public int Id { get; private set; }
    static void Check(bool ok) { if (!ok) throw new Win32Exception(Marshal.GetLastWin32Error()); }
    static void Close(ref IntPtr h) { if (h != IntPtr.Zero) { CloseHandle(h); h = IntPtr.Zero; } }
    static FileStream Stream(ref IntPtr h, FileAccess access) {
        var safe = new SafeFileHandle(h, true); h = IntPtr.Zero;
        try { return new FileStream(safe, access, 4096, false); } catch { safe.Dispose(); throw; }
    }
    public static X4GuardProcess Start(string exe, string arguments) {
        var result = new X4GuardProcess(); PI pi = new PI();
        IntPtr childIn=IntPtr.Zero, parentIn=IntPtr.Zero, childOut=IntPtr.Zero, parentOut=IntPtr.Zero;
        IntPtr childErr=IntPtr.Zero, parentErr=IntPtr.Zero, attrs=IntPtr.Zero, values=IntPtr.Zero;
        bool initialized=false;
        try {
            var sa = new SA(); sa.length = Marshal.SizeOf(typeof(SA)); sa.inherit = 1;
            Check(CreatePipe(out childIn, out parentIn, ref sa, 0));
            Check(CreatePipe(out parentOut, out childOut, ref sa, 0));
            Check(CreatePipe(out parentErr, out childErr, ref sa, 0));
            Check(SetHandleInformation(parentIn, 1, 0)); Check(SetHandleInformation(parentOut, 1, 0)); Check(SetHandleInformation(parentErr, 1, 0));
            IntPtr bytes=IntPtr.Zero; InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref bytes);
            attrs=Marshal.AllocHGlobal(bytes); Check(InitializeProcThreadAttributeList(attrs, 1, 0, ref bytes)); initialized=true;
            values=Marshal.AllocHGlobal(IntPtr.Size*3);
            Marshal.WriteIntPtr(values, 0, childIn); Marshal.WriteIntPtr(values, IntPtr.Size, childOut); Marshal.WriteIntPtr(values, IntPtr.Size*2, childErr);
            Check(UpdateProcThreadAttribute(attrs, 0, new IntPtr(0x20002), values, new IntPtr(IntPtr.Size*3), IntPtr.Zero, IntPtr.Zero));
            SIX si=new SIX(); si.si.cb=Marshal.SizeOf(typeof(SIX)); si.si.flags=0x100;
            si.si.input=childIn; si.si.output=childOut; si.si.error=childErr; si.attrs=attrs;
            result.job=CreateJobObject(IntPtr.Zero, null); Check(result.job != IntPtr.Zero);
            Check(CreateProcess(exe, new StringBuilder("\"" + exe + "\" " + arguments), IntPtr.Zero, IntPtr.Zero, true,
                                0x08080004, IntPtr.Zero, null, ref si, out pi));
            result.process=pi.process; pi.process=IntPtr.Zero; result.Id=pi.pid;
            Check(AssignProcessToJobObject(result.job, result.process));
            result.InputStream=Stream(ref parentIn, FileAccess.Write);
            result.OutputStream=Stream(ref parentOut, FileAccess.Read);
            result.ErrorStream=Stream(ref parentErr, FileAccess.Read);
            Check(ResumeThread(pi.thread) != UInt32.MaxValue);
            return result;
        } catch {
            if (result.process != IntPtr.Zero) TerminateProcess(result.process, 1);
            result.Dispose(); throw;
        } finally {
            Close(ref pi.thread); Close(ref pi.process);
            Close(ref childIn); Close(ref childOut); Close(ref childErr);
            Close(ref parentIn); Close(ref parentOut); Close(ref parentErr);
            if (initialized) DeleteProcThreadAttributeList(attrs);
            if (attrs != IntPtr.Zero) Marshal.FreeHGlobal(attrs);
            if (values != IntPtr.Zero) Marshal.FreeHGlobal(values);
        }
    }
    public bool WaitForExit(int milliseconds) {
        uint answer=WaitForSingleObject(process, (uint)Math.Max(0, milliseconds));
        if (answer == 0) return true;
        if (answer == 258) return false;
        throw new Win32Exception(Marshal.GetLastWin32Error());
    }
    public int ExitCode { get { uint code; Check(GetExitCodeProcess(process, out code)); return unchecked((int)code); } }
    public void Kill() { if (job != IntPtr.Zero) Check(TerminateJobObject(job, 1)); }
    public void Dispose() {
        try { Kill(); } catch { }
        Close(ref process); Close(ref job);
        if (InputStream != null) InputStream.Dispose();
        if (OutputStream != null) OutputStream.Dispose();
        if (ErrorStream != null) ErrorStream.Dispose();
    }
}
'@
    return [X4GuardProcess]::Start($exe, $arguments)
}

$proc = $null
try {
    $stdinStream = [Console]::OpenStandardInput()
    $ms = New-Object System.IO.MemoryStream
    $stdinStream.CopyTo($ms)
    $payload = $ms.ToArray()

    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    $adapter = Join-Path $here 'codex_adapter.py'
    if (-not (Test-Path -LiteralPath $adapter -PathType Leaf)) { Write-Fail 'codex_adapter.py is missing'; exit 0 }
    $py = Find-Python
    if (-not $py) { Write-Fail 'no Python found (set X4_PYTHON)'; exit 0 }

    $limit = 50
    if ($script:hookEvent -eq 'session_start') { $limit = 25 }
    if ($env:X4_WRAPPER_TIMEOUT_S) { $limit = [int]$env:X4_WRAPPER_TIMEOUT_S }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $py[0]
    $argList = @($py | Select-Object -Skip 1) + @($adapter, $script:hookEvent)
    # Quote only what needs it: py.exe reads its RAW command line, and a quoted "-3" is not its
    # version switch (it went on to Python as an unknown option, exit 2 -- MEASURED).
    $psi.Arguments = ($argList | ForEach-Object {
            $a = [string]$_ -replace '"', ''
            if ($a -match '\s') { '"' + $a + '"' } else { $a }
        }) -join ' '
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $proc = Start-Adapter $psi.FileName $psi.Arguments
    $outBytes = [System.IO.MemoryStream]::new()
    $outTask = $proc.OutputStream.CopyToAsync($outBytes)
    $errTask = $proc.ErrorStream.CopyToAsync([System.IO.Stream]::Null)
    $deadline = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() + $limit * 1000
    $timedOut = $false
    try {
        $writeTask = $proc.InputStream.WriteAsync($payload, 0, $payload.Length)
        if (-not $writeTask.Wait([Math]::Max(1, $limit * 1000))) { $timedOut = $true }
        else { $proc.InputStream.Close() }
    } catch { } # A child that exits without reading is judged by its exit/output below.
    if (-not $timedOut) {
        $left = [int][Math]::Max(1, $deadline - [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
        if (-not $proc.WaitForExit($left)) { $timedOut = $true }
    }
    if ($timedOut) {
        Stop-Adapter $proc
        Write-Fail "the adapter did not answer within $limit s"
        exit 0
    }
    $left = [int][Math]::Max(1, $deadline - [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
    if (-not $outTask.Wait($left)) { Stop-Adapter $proc; Write-Fail 'adapter output did not close within its budget'; exit 0 }
    $left = [int][Math]::Max(1, $deadline - [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
    if (-not $errTask.Wait($left)) { Stop-Adapter $proc; Write-Fail 'adapter error output did not close within its budget'; exit 0 }
    $out = ([System.Text.UTF8Encoding]::new($false, $true)).GetString($outBytes.ToArray())
    if ($proc.ExitCode -ne 0) { Write-Fail "the adapter exited $($proc.ExitCode)"; exit 0 }
    $lines = @(($out -replace "`r", '').TrimEnd("`n") -split "`n")
    if ($out.Trim().Length -eq 0) { Write-Fail 'the adapter printed nothing'; exit 0 }
    if ($lines.Count -ne 1) { Write-Fail "the adapter printed $($lines.Count) lines"; exit 0 }
    if (-not $lines[0].StartsWith('X4OK')) { Write-Fail 'the adapter output lacks the X4OK prefix'; exit 0 }
    $body = $lines[0].Substring(4).Trim()
    if ($body.Length -eq 0) { Write-Verdict ''; exit 0 }
    $bad = Test-Body $body
    if ($bad) { Write-Fail $bad; exit 0 }
    Write-Verdict $body
    exit 0
}
catch {
    if (-not $script:emitted) { Write-Fail ('wrapper error: ' + $_.Exception.GetType().Name) }
    exit 0
}
finally {
    if (-not $script:emitted) { Write-Fail 'the wrapper ended without a verdict' }
    if ($proc) { try { $proc.Dispose() } catch { } }
}
