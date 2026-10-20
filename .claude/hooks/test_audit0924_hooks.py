"""AUDIT-2026-09-24 Phase 1 -- hook-harness findings HK-5 and HK-6.

Run from a complete toolkit: python .claude/hooks/test_audit0924_hooks.py

These retained regressions exercise the corrected behavior. Native hook fixtures
carry the pinned shared runtime; no game or save operation is executed.

Conventions follow test_hook_facts.py: dangerous tokens are BUILT FROM PARTS and fixture
paths are generic. The one test that runs a hook (sed -i) points every X4 path at a throwaway
temp directory and feeds the payload on stdin; the hook only DECIDES, it never executes the
command it is shown.
"""
import json
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HOOKS = pathlib.Path(__file__).resolve().parent
REPO = next(p for p in HOOKS.parents if (p / "tools/modding-harness.lock.json").is_file())
_spec = importlib.util.spec_from_file_location("audit_gitbash", REPO / "scripts/gitbash.py")
_resolver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_resolver)
BASH = _resolver.find_bash()


def _owned_env(tmp, **roots):
    """Real policy dependencies and every writable root belong to this fixture."""
    toolkit = pathlib.Path(roots.get("X4_TOOLKIT", tmp / "tk"))
    (toolkit / "tools").mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO / "tools/modding_harness", toolkit / "tools/modding_harness",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "tools/modding-harness.lock.json", toolkit / "tools/modding-harness.lock.json")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("X4_") and k not in ("CLAUDE_PROJECT_DIR", "HOOK_DIR", "PYTHONPATH")}
    env.update(X4_TOOLKIT=str(toolkit), X4_GAME=str(tmp / "game"),
               X4_PROFILE=str(tmp / "profile"),
               X4_SAVES=str(pathlib.Path(roots.get("X4_PROFILE", tmp / "profile")) / "save"),
               X4_DOCUMENTS=str(tmp / "documents"), X4_BACKUPS=str(tmp / "backups"),
               X4_MODS=str(toolkit / "dev"), X4_PYTHON=sys.executable, X4_BASH=BASH or "",
               X4_HOUSEKEEP_SWEEP="off")   # never a real temp sweep / Recycle Bin from a test
    env.update({k: str(v) for k, v in roots.items()})
    return env

#: Every tool that can change a file on disk, and so must reach a protect-* guard.
FILE_CHANGING_TOOLS = ("Bash", "Edit", "Write", "NotebookEdit", "PowerShell")


def _pretool_matchers():
    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    out = []
    for entry in settings.get("hooks", {}).get("PreToolUse", []):
        cmds = " ".join(h.get("command", "") for h in entry.get("hooks", []))
        out.append((entry.get("matcher") or "", cmds))
    return out


class TestHK5MatcherCoverage(unittest.TestCase):
    def test_every_file_changing_tool_reaches_a_protect_guard(self):
        """AUDIT-2026-09-24 HK-5 (and the HK-1 gap it catches): no test pinned matcher
        coverage -- deleting the Edit|Write guard kept all three hook suites green. Fails
        today because NotebookEdit and PowerShell match no PreToolUse guard at all."""
        import re
        guarded = set()
        for matcher, cmds in _pretool_matchers():
            if "protect-" not in cmds:
                continue
            for tool in FILE_CHANGING_TOOLS:
                if re.fullmatch(matcher, tool):
                    guarded.add(tool)
        self.assertEqual(sorted(set(FILE_CHANGING_TOOLS) - guarded), [],
                         "file-changing tools with NO protect-* PreToolUse guard")


    def test_the_post_edit_validator_sees_every_file_editing_tool(self):
        """Re-review 3: NotebookEdit reached the PreToolUse guards but not the PostToolUse
        x4validate advisory, and the advisory read `file_path` only."""
        import re
        settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
        covered = set()
        for entry in settings.get("hooks", {}).get("PostToolUse", []):
            cmds = " ".join(h.get("command", "") for h in entry.get("hooks", []))
            if "x4validate-on-edit" in cmds:
                covered |= {t for t in ("Edit", "Write", "NotebookEdit")
                            if re.fullmatch(entry.get("matcher") or "", t)}
        self.assertEqual(covered, {"Edit", "Write", "NotebookEdit"})
        self.assertIn("notebook_path",
                      (HOOKS / "x4validate-on-edit.sh").read_text(encoding="utf-8"))


class TestHK5ProductionReadPath(unittest.TestCase):
    """AUDIT-2026-09-24 HK-5: every harness PIPES stdin, and "a test that pipes stdin
    cannot reproduce the production condition" is how five hooks sat inert while their
    suites passed (`cat /dev/stdin` read 0 bytes in the hook environment). These feed the
    payload from a FILE, the other shape stdin arrives in, and each carries a pipe twin so
    a difference between the two read paths is what fails -- not the rule."""

    def _run(self, hook, payload, env, via_file):
        if not via_file:
            return subprocess.run([BASH, str(HOOKS / hook)], input=payload, capture_output=True,
                                  text=True, env=env, timeout=120)
        f = pathlib.Path(env["X4_TOOLKIT"]).parent / "payload.json"
        f.write_text(payload, encoding="utf-8")
        with open(f, "r", encoding="utf-8") as fh:
            return subprocess.run([BASH, str(HOOKS / hook)], stdin=fh, capture_output=True,
                                  text=True, env=env, timeout=120)

    def _verdict(self, p):
        if not p.stdout.strip():
            return "allow"
        return json.loads(p.stdout)["hookSpecificOutput"].get("permissionDecision", "advise")

    def test_a_payload_read_from_a_FILE_is_seen(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="hk5_"))
        try:
            ref = tmp / "ref"
            (ref / "libraries").mkdir(parents=True)
            env = _owned_env(tmp, X4_GAME=str(tmp / "game"), X4_TOOLKIT=str(tmp / "tk"),
                       X4_REFERENCE=str(ref), X4_CONFIG=str(tmp / "none.env"))
            target = (ref / "libraries" / "wares.xml").as_posix()
            cases = [
                ("protect-bash.sh",
                 json.dumps({"tool_name": "Bash", "tool_input": {"command": "r" + "m -f " + target}}),
                 "deny"),
                ("protect-bash.sh",
                 json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}), "allow"),
                ("protect-files.sh",
                 json.dumps({"tool_name": "Edit", "tool_input": {"file_path": target}}), "deny"),
                ("protect-files.sh",
                 json.dumps({"tool_name": "NotebookEdit",
                             "tool_input": {"notebook_path": target}}), "deny"),
            ]
            for hook, payload, want in cases:
                for via_file in (True, False):
                    got = self._verdict(self._run(hook, payload, env, via_file))
                    self.assertEqual(got, want, (hook, payload[:90], "file" if via_file else "pipe"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestHK6Minor(unittest.TestCase):
    def test_env_resolution_comment_states_the_real_precedence(self):
        """AUDIT-2026-09-24 HK-6: _x4-env.sh's header says `x4-paths.env > existing env var`
        while the code (and the paragraph right below it) make THE ENVIRONMENT WIN."""
        head = (HOOKS / "_x4-env.sh").read_text(encoding="utf-8").splitlines()[:12]
        line = next(ln for ln in head if "Resolution order" in ln)
        self.assertLess(line.find("env var"), line.find("x4-paths.env"),
                        "stated order puts the config file above the environment: " + line)

    def test_session_canary_uses_the_shared_python_lookup(self):
        """AUDIT-2026-09-24 HK-6: session-canary.sh resolves python itself (`command -v
        python`) instead of x4_python, so it ignores X4_PYTHON like no other hook does."""
        text = (HOOKS / "session-canary.sh").read_text(encoding="utf-8")
        self.assertIn("x4_python", text)

    def test_session_canary_not_checked_line_reaches_stdout(self):
        """AUDIT-2026-09-24 HK-6: the rc-0 NOT CHECKED disclosure is printed to STDERR,
        which a SessionStart hook does not put in the model's context (INFERRED from the
        hook contract; stdout is what SessionStart adds)."""
        text = (HOOKS / "session-canary.sh").read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines() if "'NOT CHECKED:'" in ln)
        self.assertNotIn(">&2", line, line)

    def test_next_blind_spot_id_consults_remote_tracking_branches(self):
        """AUDIT-2026-09-24 HK-6: next-blind-spot-id.py promises 'EVERY branch' but lists
        refs/heads only, so an id claimed on origin/* by another clone is invisible."""
        text = (REPO / "tools" / "x4validate" / "scripts" / "next-blind-spot-id.py"
                ).read_text(encoding="utf-8")
        self.assertIn("refs/remotes", text)

    def test_a_deny_does_not_tell_the_reader_to_confirm(self):
        """AUDIT-2026-09-24 HK-6: `sed -i` on a game file is DENIED with a reason reading
        'confirm: ...' -- there is nothing to confirm on a deny. Either the verdict (ask) or
        the wording is wrong; which one is a design decision."""
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="hk6_"))
        try:
            game = tmp / "game"
            (game / "libraries").mkdir(parents=True)
            env = _owned_env(tmp, X4_GAME=str(game), X4_TOOLKIT=str(tmp / "tk"),
                       X4_REFERENCE=str(tmp / "ref"), X4_EXTENSIONS=str(game / "extensions"))
            target = str(game / "libraries" / "wares.xml").replace(chr(92), "/")
            cmd = "se" + "d -i s/a/b/ " + target
            payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
            p = subprocess.run([BASH, str(HOOKS / "protect-bash.sh")], input=payload,
                               capture_output=True, text=True, env=env, timeout=60)
            out = json.loads(p.stdout)["hookSpecificOutput"]
            self.assertEqual(out.get("permissionDecision"), "deny",
                             "fixture premise moved: " + p.stdout[:200])
            self.assertNotIn("confirm", out.get("permissionDecisionReason", "").lower())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------------------------------
# AUDIT-2026-09-24 HK-1 review follow-up: gaps in the PowerShell FRONT-END, pinned END TO END
# (the payload is piped into protect-bash.sh as JSON, as Claude Code sends it). The hook only
# DECIDES; nothing here executes the commands it is shown.
import sys as _sys
_sys.path.insert(0, str(HOOKS))
import hook_facts as _H  # noqa: E402

BS = chr(92)

#: T2 (v4.0 release review): reasons that mean the guard FAILED, whatever verdict carries them.
ENGINE_FAILURES = ("policy engine failed", "X4 save dispatcher", "GUARD INERT",
                   "preservation/policy failed")


def engine_failure(reason):
    return any(failure in (reason or "") for failure in ENGINE_FAILURES)


@unittest.skipUnless(_H._pwsh_exe(), "no PowerShell here: the front-end cannot be exercised "
                                     "(without one normal dispatch refuses PowerShell commands)")
class _PSE2E(unittest.TestCase):
    """Fixture roots in a throwaway directory, Windows-spelled (backslashes), because that is
    how a PowerShell user writes them."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="psfe_"))
        t = cls.tmp
        cls.game, cls.ref, cls.tk = t / "X4 Foundations", t / "reference", t / "tk"
        cls.docs = t / "Documents"
        cls.prof = cls.docs / "Egosoft" / "X4" / "123"
        for d in (cls.game / "libraries", cls.ref / "libraries", cls.tk, cls.prof / "save"):
            d.mkdir(parents=True)
        cls.env = _owned_env(t, X4_GAME=str(cls.game), X4_REFERENCE=str(cls.ref),
                       X4_TOOLKIT=str(cls.tk), X4_PROFILE=str(cls.prof),
                       X4_DOCUMENTS=str(cls.docs), X4_MODS=str(cls.tk / "dev"),
                       X4_CONFIG=str(t / "none.env"))
        cls.env.pop("CLAUDE_PROJECT_DIR", None)
        # Windows spellings of the roots, for PowerShell text
        cls.R = str(cls.ref).replace("/", BS)
        cls.G = str(cls.game).replace("/", BS)
        cls.S = str(cls.prof / "save").replace("/", BS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def verdict(self, command, tool="PowerShell"):
        return self.verdict_with(command, tool)[0]

    def verdict_with(self, command, tool="PowerShell"):
        payload = json.dumps({"tool_name": tool, "tool_input": {"command": command}})
        p = subprocess.run([BASH, str(HOOKS / "protect-bash.sh")], input=payload,
                           capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        if not p.stdout.strip():
            return "allow", ""
        h = json.loads(p.stdout)["hookSpecificOutput"]
        return h.get("permissionDecision", "advise"), h.get("permissionDecisionReason", "")

    def assert_scope_refusal(self, command, tool="PowerShell"):
        verdict, reason = self.verdict_with(command, tool)
        self.assertEqual(verdict, "deny", command)
        self.assertIn("scope could not be completely evaluated", reason, command)
        self.assertIn("use literal targets and an available parser", reason, command)

    def expect(self, cases, tool="PowerShell"):
        for want, command in cases:
            if want == "scope-refusal":
                self.assert_scope_refusal(command, tool)
            else:
                got, reason = self.verdict_with(command, tool)
                self.assertEqual(got, want, command)
                if want == "deny":
                    # T2: the POLICY's deny, never an engine failure that also says deny.
                    self.assertFalse(engine_failure(reason), (command, reason))

    def expect_with_save(self, cases, tool="PowerShell"):
        """C1 (v4.0 release review): an ABSENT delete target keeps the profile ask, so a probe of
        the resolved-save path (advise + backup) needs the save to EXIST -- for these cases only:
        a file in the folder changes what a whole-folder pipeline resolves to elsewhere."""
        save = self.prof / "save" / "a.xml.gz"
        save.write_bytes(b"opaque save fixture")
        try:
            self.expect(cases, tool)
        finally:
            save.unlink()                # this run's own fixture (same-run scratch)


class TestPS1Splatting(_PSE2E):
    """StaticParameterBinder cannot see a splat, so `Remove-Item @p` bound NO path and the
    delete reached no rule."""

    def test_a_literal_hashtable_splat_is_resolved(self):
        self.expect_with_save([
            ("deny", "$p=@{Path='" + self.R + BS + "libraries'; Recurse=$true}; Remove-Item @p"),
            ("advise", "$p=@{LiteralPath='" + self.S + BS + "a.xml.gz'}; Remove-Item @p"),
            ("deny", "$q=@{Path='" + self.R + BS + "a.xml'; Value='x'}; Set-Content @q"),
        ])

    def test_an_unresolvable_splat_on_a_writing_cmdlet_refuses(self):
        self.expect([("scope-refusal", "Remove-Item @args"),
                     ("scope-refusal", "$p = Get-Params; Remove-Item @p")])

    def test_an_unresolvable_splat_BESIDE_a_given_path_still_refuses(self):
        """Re-review 2: with a path present, the "no target path" report cannot fire, so
        only the splat report stands between these and an allow -- the splat may carry
        -Destination (or -Path) pointing anywhere."""
        self.expect([("scope-refusal", "$p = Get-Params; Move-Item ." + BS + "a @p"),
                     ("scope-refusal", "Copy-Item ." + BS + "a @args")])

    def test_TWIN_harmless_splats(self):
        self.expect([("allow", "$p=@{Path='.' + '" + BS + "x.txt'}; Remove-Item @p"),
                     ("allow", "$o=@{Object='x'}; Write-Output @o"),
                     ("allow", "$o = Get-Stuff; Write-Output @o")])


class TestPS2MethodCalls(_PSE2E):
    """Instance and type-qualified file methods reached no rule: only [IO.File]/[IO.Directory]
    static calls were mapped."""

    def test_instance_deletes_on_an_identified_object(self):
        R = self.R
        self.expect([
            ("deny", "(Get-Item '" + R + BS + "a.xml').Delete()"),
            ("deny", "gci '" + R + "' | % { $_.Delete() }"),
            ("deny", "[System.IO.FileInfo]::new('" + R + BS + "a.xml').Delete()"),
            ("deny", "(New-Object System.IO.DirectoryInfo '" + R + "').Delete($true)"),
            ("deny", "([IO.FileInfo]'" + R + BS + "a.xml').MoveTo('C:" + BS + "x.xml')"),
            ("deny", "$f = Get-Item '" + R + BS + "a.xml'; $f.Delete()"),
        ])

    def test_type_qualified_statics(self):
        R = self.R
        self.expect([
            ("deny", "using namespace System.IO" + chr(10) + "[Directory]::Delete('" + R + "', $true)"),
            ("deny", "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory('" + R
             + "', 'DeleteAllContents')"),
            ("deny", "[System.IO.File]::Replace('C:" + BS + "n.xml', '" + R + BS + "a.xml', $null)"),
            ("deny", "[IO.File]::Encrypt('" + R + BS + "a.xml')"),
        ])

    def test_an_unresolvable_target_refuses(self):
        self.expect([
            ("scope-refusal", "$f.Delete()"),
            ("scope-refusal", "[IO.File]::WriteAllText($x, 'y')"),
            ("scope-refusal", "$d.MoveTo('C:" + BS + "y')"),
            ("scope-refusal", "Get-Things | % { $_.Delete() }"),
        ])

    def test_a_root_variable_target_still_denies(self):
        """Unresolved is ASK -- but a target naming a root through its variable must still
        reach the root rule, where the deny outranks the ask (as for Remove-Item)."""
        self.expect([
            ("deny", '[IO.File]::Delete("$env:X4_REFERENCE' + BS + 'a.xml")'),
            ("deny", '(Get-Item "$env:X4_REFERENCE").Delete()'),
        ])

    def test_TWIN_harmless_methods(self):
        self.expect([
            ("allow", "'abc'.Replace('a','b')"),
            ("allow", "$s.Replace('a','b'); $list.CopyTo($arr, 0)"),
            ("allow", "(Get-Item ." + BS + "x.txt).Delete()"),
            ("allow", "[IO.Path]::Combine('a','b'); [string]::Copy('x')"),
            ("allow", "[IO.File]::ReadAllText('" + self.R + BS + "a.xml')"),
        ])


class TestPS3InvokeExpression(_PSE2E):
    """iex of text the translator cannot read, or nested past its depth limit, was DROPPED:
    the command it runs reached no rule. Bash's `eval "$X"` is the model -- except that a
    carrier the walk could not open is reported, never passed."""

    def test_computed_text_refuses(self):
        self.expect([("scope-refusal", "iex $cmd"),
                     ("scope-refusal", "Invoke-Expression -Command (Get-Content .\\x.ps1 -Raw)"),
                     ("scope-refusal", "$c = 'Remove-' + $n; iex $c")])

    def test_past_the_depth_limit_refuses(self):
        inner = "Get-Date"
        for _ in range(6):
            inner = "iex '" + inner.replace("'", "''") + "'"
        self.assert_scope_refusal(inner)

    def test_resolvable_text_is_still_judged(self):
        self.expect([("deny", "iex 'Remove-Item -Recurse " + self.R + "'"),
                     ("deny", "$c = 'Remove-Item -Recurse " + self.R + "'; iex $c")])

    def test_TWIN_harmless_iex(self):
        self.expect([("allow", "iex 'Get-Date'"), ("allow", "iex \"Get-ChildItem .\"")])


class TestPS4ProviderAndPrefixedPaths(_PSE2E):
    """A provider-qualified or extended-length spelling names the same file, and compared
    equal to no root."""

    def spellings(self, p):
        return ["Microsoft.PowerShell.Core" + BS + "FileSystem::" + p, "FileSystem::" + p,
                BS + BS + "?" + BS + p, BS + BS + "." + BS + p]

    def test_reference_game_profile(self):
        cases = []
        for sp in self.spellings(self.R):
            cases.append(("deny", "Remove-Item -Recurse -LiteralPath '" + sp + "'"))
            cases.append(("deny", "Set-Content -LiteralPath '" + sp + BS + "a.xml' -Value x"))
        for sp in self.spellings(self.G):
            cases.append(("deny", "Remove-Item -Recurse -Force '" + sp + "'"))
        for sp in self.spellings(self.S):
            # Off Windows a `\\?\` / `\\.\` device prefix is a Windows anchor that names nothing
            # on the POSIX host, so the save evidence is ForeignPath (save_policy.native_text,
            # LINUX GAP 3d5dee4): unresolved, no backup, no lift -- the profile ASK stands.
            # MEASURED with hook_facts.POSIX_HOST injected; ubuntu CI run 39187589960 got ask.
            foreign = os.name != "nt" and sp.startswith(BS + BS)
            cases.append(("ask" if foreign else "advise",
                          "Remove-Item -LiteralPath '" + sp + BS + "a.xml.gz'"))
        self.expect_with_save(cases)

    def test_in_a_nested_bash_carrier_too(self):
        sp = "FileSystem::" + self.R
        self.expect([("deny", 'powershell -c "Remove-Item -Recurse \'' + sp + '\'"')], tool="Bash")

    def test_TWIN_reads_stay_allowed(self):
        self.expect([("allow", "Get-ChildItem 'FileSystem::" + self.R + "'"),
                     ("allow", "Get-Content '" + BS + BS + "?" + BS + self.R + BS + "a.xml'")])


class TestPS5CmdPercentVariables(_PSE2E):
    """`%VAR%` in a cmd.exe carrier was literal text, so `%X4_REFERENCE%` named no root.
    Now it reaches the rules as `${VAR}` does in Bash: a root variable names its root,
    any other is an unknown operand."""

    def test_root_variables(self):
        self.expect_with_save([
            ("deny", 'cmd //c rd /s /q "%X4_REFERENCE%"'),
            ("deny", 'cmd /c "rd /s /q %X4_REFERENCE%' + BS + 'libraries"'),
            # I1 (v4.0 release review): cmd is transparent only with /d (AutoRun off); without it
            # the profile ask stands -- the twin of the advised /d form.
            ("advise", 'cmd //d //c del /q "%X4_SAVES%' + BS + 'a.xml.gz"'),
            ("ask", 'cmd //c del /q "%X4_SAVES%' + BS + 'a.xml.gz"'),
        ], tool="Bash")

    def test_TWIN_an_unknown_variable_is_an_unknown_operand(self):
        self.expect([("allow", 'cmd //c rd /s /q "%TEMP%' + BS + 'build"'),
                     ("allow", 'cmd //c echo 100%% done')], tool="Bash")


class TestPS6OtherWritingCmdlets(_PSE2E):
    """Cmdlets outside the translator's switch passed through word for word, so their
    writes reached no rule."""

    def test_mapped_writers(self):
        R = self.R
        f = "'" + R + BS + "a.xml'"
        self.expect([
            ("deny", "Clear-Item " + f),
            ("deny", "Set-ItemProperty " + f + " -Name IsReadOnly -Value $false"),
            ("deny", "Remove-ItemProperty -Path " + f + " -Name x"),
            ("deny", "Copy-ItemProperty -Path ." + BS + "x -Destination " + f + " -Name y"),
            ("deny", "Expand-Archive ." + BS + "x.zip -DestinationPath '" + R + "'"),
            ("deny", "Compress-Archive -Path ." + BS + "x -DestinationPath '" + R + BS + "a.zip'"),
            ("deny", "Get-Process | Export-Csv -Path '" + R + BS + "a.csv'"),
            ("deny", "Get-Process | Export-Json -Path '" + R + BS + "a.json'"),
            ("deny", "'x' | Tee-Object -FilePath '" + R + BS + "a.txt'"),
            ("deny", "Invoke-WebRequest https://example.invalid -OutFile '" + R + BS + "a'"),
            ("deny", "Start-Transcript -Path '" + R + BS + "t.txt'"),
            ("deny", "New-Item " + f + " -Force"),
            # Set-Acl exists only in Windows PowerShell's security module: elsewhere the
            # static binder cannot bind its positional path, and the translator fails CLOSED
            # -- on a platform where the cmdlet cannot run at all (CI ubuntu). v3.3.0 asked;
            # since the synchronous save dispatcher (64df0c5) an untranslated PowerShell
            # command is refused before protect-bash.sh's ask (save_policy.ScopeError), the
            # same refusal an unknown mutating cmdlet gets (ubuntu CI run 39187589960: deny).
            ("deny" if os.name == "nt" else "scope-refusal", "Set-Acl " + f + " -AclObject $a"),
            ("deny", "Start-Process git -RedirectStandardOutput '" + R + BS + "o.txt'"),
        ])

    def test_an_unknown_mutating_cmdlet_on_a_protected_path_refuses(self):
        self.expect([("scope-refusal", "Frob-Thing '" + self.R + BS + "a.xml'"),
                     ("scope-refusal", "Update-Widget -Target '" + self.S + BS + "x'"),
                     ("scope-refusal", "Set-Item '" + self.G + BS + "libraries" + BS + "w.xml' -Value x")])

    def test_TWIN_harmless(self):
        R = self.R
        self.expect([
            ("allow", "Get-FileHash '" + R + BS + "a.xml'; Test-Path '" + R + "'"),
            ("allow", "Frob-Thing ." + BS + "x"),
            ("allow", "Expand-Archive x.zip -DestinationPath ." + BS + "out"),
            ("allow", "Import-Csv '" + R + BS + "a.csv' | Select-Object -First 1"),
            ("allow", "Select-String -Path '" + R + BS + "md" + BS + "x.xml' -Pattern y"),
            ("allow", "Write-Host '" + R + "'; 'x' | Tee-Object -Variable v"),
            ("allow", "Export-ModuleMember -Function f"),
        ])


class TestPSEmptyTranslation(_PSE2E):
    """Re-review item 1: a translation can be EMPTY (everything in it was unresolved, or it
    did nothing). protect-bash.sh's `[ -z "$COMMAND" ] && exit 0` would ALLOW the first
    kind; it asked only because `$(...)` stripped the newline after the sentinel, the split
    failed, and COMMAND held the raw fact dump -- which the ask then quoted."""

    def reason(self, command):
        payload = json.dumps({"tool_name": "PowerShell", "tool_input": {"command": command}})
        p = subprocess.run([BASH, str(HOOKS / "protect-bash.sh")], input=payload,
                           capture_output=True, text=True, env=self.env, timeout=120)
        if not p.stdout.strip():
            return "allow", ""
        h = json.loads(p.stdout)["hookSpecificOutput"]
        return h.get("permissionDecision", "advise"), h.get("permissionDecisionReason", "")

    def test_empty_with_unresolved_parts_refuses_with_a_real_reason(self):
        for cmd in ("iex $cmd", "Remove-Item @args"):
            v, r = self.reason(cmd)
            self.assertEqual(v, "deny", cmd)
            self.assertNotIn("__X4_COMMAND__", r, cmd)
            self.assertNotIn("carrier_untranslated" + chr(9), r, cmd)
            self.assertIn("scope could not be completely evaluated", r, cmd)

    def test_TWIN_empty_with_nothing_unresolved_allows(self):
        for cmd in ("# only a comment", "$x = 1"):
            self.assertEqual(self.reason(cmd)[0], "allow", cmd)


class TestEmptyCommandSplit(unittest.TestCase):
    """Backend-only sentinel seam: the stub provides CLI TSV, not the normal facts() API.
    Normal full-dispatch refusal and healthy controls remain in TestPSEmptyTranslation.
    The same defect at the seam, with the analyser STUBBED so the fact stream is exact.
    hook_facts ends its output with `<NL>__X4_COMMAND__<NL><command>`; for an empty command
    `$(...)` strips the final newline, the split fails, and COMMAND became the fact dump --
    every rule that quotes the command then quoted the dump. Runnable without PowerShell."""

    def run_stub(self, facts):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="split_"))
        try:
            for source in (REPO / "tools/x4_harness").iterdir():
                if source.is_file():
                    shutil.copy2(source, tmp / source.name)
            lines = "".join("%s%s%s%s" % (k, chr(9), v, chr(10)) for k, v in facts.items())
            (tmp / "hook_facts.py").write_text(
                "import sys\nsys.stdin.read()\nsys.stdout.buffer.write(%r.encode())\n"
                % (lines + "__X4_COMMAND__" + chr(10)), encoding="utf-8")
            env = _owned_env(tmp, X4_CONFIG=str(tmp / "none.env"), X4_TOOLKIT=str(tmp))
            p = subprocess.run([BASH, str(tmp / "protect-bash.sh"), "--harness-policy-backend"],
                               input='{"tool_name":"PowerShell","tool_input":{"command":"x"}}',
                               capture_output=True, text=True, env=env, timeout=60)
            if not p.stdout.strip():
                return "allow", ""
            h = json.loads(p.stdout)["hookSpecificOutput"]
            return h.get("permissionDecision", "advise"), h.get("permissionDecisionReason", "")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_empty_command_with_an_unresolved_part_asks_without_the_dump(self):
        v, r = self.run_stub({"carrier_untranslated": 1, "from_powershell": 1,
                              "rm_in_x4_dir": 1})
        self.assertEqual(v, "ask")
        self.assertNotIn("carrier_untranslated" + chr(9), r)
        self.assertNotIn("__X4_COMMAND__", r)

    def test_TWIN_empty_command_with_nothing_unresolved_allows(self):
        self.assertEqual(self.run_stub({"carrier_untranslated": 0, "from_powershell": 1})[0],
                         "allow")


# ------------------------------------------------------------------------------------------
# v3.3.0 release review (hooks lane). Every finding was reproduced E2E on fake roots before
# its fix; each class names the finding it pins. The hook only DECIDES; nothing here runs.
class _RR(_PSE2E):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Rf = str(cls.ref).replace(BS, "/")
        cls.Gf = str(cls.game).replace(BS, "/")
        cls.Sf = str(cls.prof / "save").replace(BS, "/")

    def expect(self, cases, tool="PowerShell"):
        """Every case is run and every mismatch reported, not just the first."""
        bad = []
        for want, command in cases:
            got, reason = self.verdict_with(command, tool)
            if want == "scope-refusal":
                if got != "deny" or "scope could not be completely evaluated" not in reason:
                    bad.append("%s (want unresolved-scope refusal): %s" % (got, command))
                continue
            if got != want:
                bad.append("%s (want %s): %s" % (got, want, command))
            elif want == "deny" and engine_failure(reason):
                # T2: a deny produced by an engine failure is not the policy's deny.
                bad.append("deny by ENGINE FAILURE (%s): %s" % (reason[:80], command))
        self.assertEqual(bad, [])

    def verdict_with(self, command, tool="PowerShell", **extra):
        """`extra` goes at the payload's TOP level, where Claude Code puts `cwd`."""
        payload = json.dumps({"tool_name": tool, "tool_input": {"command": command}, **extra})
        p = subprocess.run([BASH, str(HOOKS / "protect-bash.sh")], input=payload,
                           capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        if not p.stdout.strip():
            return "allow", ""
        h = json.loads(p.stdout)["hookSpecificOutput"]
        return (h.get("permissionDecision", "advise"),
                h.get("permissionDecisionReason") or h.get("additionalContext") or "")


class TestRR1PipelineAndVariableTargets(_RR):
    """CRITICAL: a delete target reaching Remove-Item through the PIPELINE, or through a
    variable holding command output, became `"${PS_PIPELINE_INPUT}"` / `"${t}"` with no
    unknown entry -- an unresolved operand naming no root, so ALLOW. The translator's own
    contract says an unresolvable write target ASKS; a resolvable one must deny exactly
    like the literal form."""

    def test_resolvable_pipeline_and_variable_targets_deny_like_the_literal(self):
        R, G = self.R, self.G
        self.expect([
            ("deny", "'" + R + "' | Remove-Item -Recurse -Force"),
            ("deny", "$x = '" + R + "'; $x | Remove-Item -Recurse -Force"),
            ("deny", "Write-Output '" + R + "' | Remove-Item -Recurse -Force"),
            ("deny", "'" + G + "' | ForEach-Object { Remove-Item $_ -Recurse -Force }"),
            ("deny", "$t = Join-Path '" + R + "' 'libraries'; Remove-Item $t -Recurse -Force"),
            ("deny", "$t = Join-Path $env:X4_REFERENCE 'libraries'; Remove-Item $t -Recurse"),
            ("deny", "$d = Get-Item '" + R + "'; Remove-Item $d -Recurse -Force"),
            ("deny", "$d = Get-Item '" + R + "'; $d | Remove-Item -Recurse -Force"),
            ("deny", "Join-Path '" + R + "' 'libraries' | Remove-Item -Recurse"),
            ("deny", "$t = Resolve-Path '" + G + "'; Remove-Item $t -Recurse -Force"),
            ("deny", "$t = Join-Path '" + R + "' 'a.xml'; Set-Content -Path $t -Value x"),
            ("deny", "$d = '" + R + "'; Copy-Item .\\a.xml -Destination $d"),
        ])

    def test_whole_save_folder_refuses_and_unqualified_children_still_ask(self):
        S = self.S
        self.expect([
            ("deny", "$t = Resolve-Path '" + S + "'; Remove-Item $t -Recurse"),
            ("ask", "$items = Get-ChildItem '" + S + "'; $items | Remove-Item"),
        ])

    def test_an_unresolvable_DELETE_target_refuses(self):
        self.expect([
            ("scope-refusal", "$t = Get-Target; Remove-Item $t -Recurse"),
            ("scope-refusal", "Get-Targets | Remove-Item -Recurse"),
            ("scope-refusal", "$t | Remove-Item"),
            ("scope-refusal", "Remove-Item (Get-Target) -Recurse"),
            ("scope-refusal", "Get-Targets | Move-Item -Destination .\\x"),
            ("scope-refusal", "Rename-Item $t -NewName x"),
        ])

    def test_an_unresolvable_WRITE_target_follows_the_bash_write_convention(self):
        """Scoped by MEASUREMENT: failing closed on write targets too added 28 asks over
        1,524 historical PowerShell commands, all routine. A write target is judged by its
        text, as `cp x "$T"` is in Bash -- it still denies when that text names a root."""
        self.expect([
            ("allow", "Copy-Item .\\a -Destination $d"),
            ("allow", "Set-Content -Path $p -Value x"),
            ("deny", "Set-Content -Path \"" + self.R + "\\$p\" -Value x"),
            ("deny", "Copy-Item .\\a -Destination (Join-Path '" + self.R + "' $n)"),
        ])

    def test_TWIN_resolvable_harmless_targets_allow(self):
        self.expect([
            ("allow", "$t = Join-Path '.' 'build'; Remove-Item $t -Recurse"),
            ("allow", "'.\\build' | Remove-Item -Recurse"),
            ("allow", "$x = '.\\build'; $x | Remove-Item -Recurse"),
            ("allow", "Get-ChildItem . -Filter *.pyc | Remove-Item"),
            ("allow", "Get-ChildItem .\\build | Remove-Item -Recurse"),
            ("allow", "$d = Get-Item .\\build; Remove-Item $d -Recurse"),
            # An ENVIRONMENT variable is judged as `$X` is in Bash: a root variable
            # names its root (above), any other is an unknown operand -- not a new ask.
            ("allow", "Remove-Item \"$env:TEMP\\x4build\" -Recurse"),
            ("allow", "Get-Process | Stop-Process -WhatIf"),
            ("allow", "Get-Content a.txt | Set-Content .\\b.txt"),
            # a non-filesystem provider path is not a file (replay FP, this lane)
            ("allow", "foreach ($v in 'X4_GAME','X4_TOOLKIT') { Remove-Item (\"Env:\" + $v) }"),
            ("allow", "Remove-Item Env:X4_REFERENCE"),
            # a variable assigned twice is the union of both values (replay FP, this lane)
            ("allow", "$s = Join-Path '.' 'a'; Move-Item $s .\\x; $s = Join-Path '.' 'b'; Move-Item $s .\\y"),
            ("allow", "'x' | Out-File .\\o.txt"),
        ])


class TestRR2PowerShellHostForms(_RR):
    """IMPORTANT: a PowerShell host taking its program from -CommandWithArgs/-cwa, from
    `-File -`, or from stdin passed silently: the payload reached no rule."""

    def test_host_payload_forms_are_translated(self):
        rm = "Remove-Item -Recurse -Force '" + self.G + "'"
        self.expect([
            ("deny", 'pwsh -NoProfile -CommandWithArgs "' + rm + '"'),
            ("deny", 'pwsh -cwa "' + rm + '"'),
            ("deny", 'echo "' + rm + '" | pwsh -NoProfile -File -'),
            ("deny", 'echo "' + rm + '" | pwsh -NoProfile'),
            ("deny", 'echo "' + rm + '" | powershell -NoProfile'),
            ("deny", 'echo "' + rm + '" | pwsh -NoProfile -Command -'),
            ("deny", "pwsh -NoProfile -Command - <<< \"" + rm + "\""),
        ], tool="Bash")

    def test_an_unreadable_stdin_program_refuses(self):
        self.expect([
            ("scope-refusal", "cat x.ps1 | pwsh -NoProfile"),
            ("scope-refusal", "git show HEAD:x.ps1 | pwsh -NoProfile -Command -"),
            ("scope-refusal", "pwsh -NoProfile -File - < x.ps1"),
        ], tool="Bash")

    def test_TWIN_hosts_with_their_own_program(self):
        self.expect([
            ("allow", "pwsh -NoProfile -File script.ps1"),
            ("allow", 'echo hi | pwsh -NoProfile -Command "Get-Date"'),
            ("allow", 'echo "Get-Date" | pwsh -NoProfile -Command -'),
            ("allow", 'pwsh -NoProfile -cwa "Get-Date"'),
            ("allow", "pwsh -NoProfile -Version"),
        ], tool="Bash")


class TestRR3CmdCarrier(_RR):
    """IMPORTANT: the cmd.exe carrier lost the directory of `cd /d X` (so a relative delete
    escaped), did not know `/R` (a synonym of /C), and read a caret-escaped verb (`r^d`)
    as an unknown command."""

    def test_cd_d_R_and_caret(self):
        G, Gf = self.G, self.Gf
        self.expect([
            ("deny", 'cmd //r rd /s /q "' + Gf + '"'),
            # cmd splits an UNQUOTED spaced path into two operands, so the game root is
            # quoted inside the payload; the reference root has no space.
            ("deny", 'cmd //c "r^d /s /q \\"' + G + '\\""'),
            ("deny", 'cmd //c "r^d /s /q ' + self.R + '"'),
            # coordinator verification: the game root (it has a SPACE) left unquoted
            # inside the payload -- cmd splits it, the guard still reads the root
            ("deny", 'cmd //c "r^d /s /q ' + G + '"'),
            ("deny", 'cmd //c "rd /s /q ' + G + '"'),
            ("deny", 'cmd //c "r^d /s /q ' + G + '\\extensions"'),
            ("deny", 'cmd //c "^r^m^d^i^r /s /q ' + self.R + '"'),
            ("deny", 'cmd //c "cd /d ' + G + ' && rd /s /q extensions"'),
            ("deny", 'cmd //c "pushd ' + G + ' && rd /s /q extensions"'),
        ], tool="Bash")
        self.expect([
            ("deny", "cmd /c 'cd /d \"" + G + "\" && rd /s /q extensions'"),
            ("deny", 'cmd /c "cd /d ' + G + ' && rd /s /q extensions"'),
            ("deny", "cmd /r rd /s /q '" + G + "'"),
        ])

    def test_TWIN_cmd_carriers_that_touch_nothing_protected(self):
        self.expect([
            ("allow", 'cmd //c "cd /d C:\\work\\x && rd /s /q build"'),
            ("allow", "cmd //c echo a^&b"),
            ("allow", 'cmd //r "dir /b"'),
        ], tool="Bash")


class TestF10DynamicProgramWordE2E(_RR):
    """F10 (v4.0), through the REAL hook: an unidentifiable command name (variable,
    substitution, `%R%`/`!R!`, `& $R`) gets at least the plain command's verdict, or an ask
    when the name itself may be the delete. MEASURED on master: every non-twin row allowed."""

    def test_bash_and_cmd(self):
        R, Rf, Gf = self.R, self.Rf, self.Gf
        self.expect([
            ("deny", "$R rm -rf '" + Rf + "'"),
            ("deny", '"$R" rm -rf \'' + Rf + "'"),
            ("deny", "$R 'rm -rf \"" + Rf + "\"'"),
            ("deny", "R=eval; $R 'rm -rf \"" + Gf + "\"'"),
            ("deny", "cd '" + Gf + "' && $R rm -rf extensions"),
            ("deny", "$R git add -A"),
            ("deny", "$R add -A"),
            ("ask", "$R -rf '" + Gf + "'"),
            ("deny", 'cmd //c "%R% del ' + Rf + '/a.xml"'),
            ("deny", 'cmd //v:on //c "!R! del ' + Rf + '/a.xml"'),
            ("deny", 'cmd //c "%R%del ' + Rf + '/a.xml"'),
        ], tool="Bash")

    def test_powershell(self):
        R, G = self.R, self.G
        self.expect([
            ("deny", "& $R Remove-Item '" + R + "'"),
            ("deny", ". $R Remove-Item '" + R + "'"),
            ("deny", "& $R \"Remove-Item '" + R + "'\""),
            ("deny", "& \"$($R)-Item\" '" + G + "'"),
            ("deny", "& $R git add -A"),
            ("deny", "cmd /c \"%R% del " + R + "\\a.xml\""),
        ])

    def test_TWIN_unknown_names_that_touch_nothing_protected(self):
        self.expect([
            ("allow", "$R --version"),
            ("allow", '"$PY" -c "import os; print(os.getcwd())"'),
            ("allow", 'cmd //c "%R% --version"'),
        ], tool="Bash")
        self.expect([
            ("allow", "& $exe --version"),
            # pwsh 7 reads `pwsh Remove-Item` as -File: it runs nothing (the sibling's twin)
            ("allow", "& pwsh Remove-Item '" + self.R + "'"),
        ])


class TestRR4DotNetWritersAndScriptBlocks(_RR):
    """IMPORTANT: .NET file writers and code-runners were unmodelled, so they allowed."""

    def test_writers_deny_on_a_protected_path(self):
        f = "'" + self.R + BS + "libraries" + BS + "w.xml'"
        self.expect([
            ("deny", "$w = [IO.StreamWriter]::new(" + f + "); $w.Write(1); $w.Close()"),
            ("deny", "$w = New-Object IO.StreamWriter " + f + "; $w.Close()"),
            ("deny", "$w = New-Object -TypeName System.IO.StreamWriter -ArgumentList " + f),
            ("deny", "[IO.File]::Open(" + f + ", 'Truncate').Close()"),
            ("deny", "[System.IO.FileStream]::new(" + f + ", 'Create')"),
        ])

    def test_scriptblock_text_is_translated_like_iex(self):
        R, G = self.R, self.G
        self.expect([
            ("deny", "[scriptblock]::Create('Remove-Item -Recurse -Force ''" + R + "''').Invoke()"),
            ("deny", "& ([scriptblock]::Create('Remove-Item -Recurse -Force ''" + G + "'''))"),
            ("deny", "$ExecutionContext.InvokeCommand.InvokeScript('Remove-Item -Recurse -Force ''"
             + G + "''')"),
            ("deny", "$ExecutionContext.InvokeCommand.NewScriptBlock('Remove-Item -Recurse ''"
             + G + "''')"),
        ])

    def test_unreadable_text_or_target_refuses(self):
        self.expect([
            ("scope-refusal", "[scriptblock]::Create($s).Invoke()"),
            ("scope-refusal", "$ExecutionContext.InvokeCommand.InvokeScript($s)"),
            ("scope-refusal", "[System.IO.Compression.ZipFile]::ExtractToDirectory('a.zip', '" + self.R + "')"),
        ])

    def test_TWIN_readers_and_harmless_calls(self):
        R = self.R
        self.expect([
            ("allow", "[IO.File]::Open('" + R + BS + "a.xml', 'Open', 'Read').Close()"),
            ("allow", "$r = [IO.StreamReader]::new('" + R + BS + "a.xml'); $r.ReadToEnd()"),
            ("allow", "[IO.Path]::Combine('" + R + "', 'a')"),
            ("allow", "[scriptblock]::Create('Get-Date').Invoke()"),
            ("allow", "$w = [IO.StreamWriter]::new('.\\out.txt'); $w.Close()"),
            ("allow", "$w = [IO.StreamWriter]::new($p)"),
            ("allow", "$s = [IO.File]::Open('.\\o', 'Create'); $w = New-Object IO.StreamWriter($s, $e)"),
            ("allow", "[System.IO.Compression.ZipFile]::ExtractToDirectory('a.zip', '.\\out')"),
        ])


class TestRR5ChildrenOfARootDenyLikeTheGlob(_RR):
    """MINOR: `gci <G> | Remove-Item` became `rm -rf <G>/${PS_CHILD}` -> ASK, while Bash
    `rm -rf <G>/*` hard-DENIES through glob_covers. The same delete, two verdicts."""

    def test_children_of_the_game_root_deny(self):
        G = self.G
        self.expect([
            ("deny", "Get-ChildItem '" + G + "' | Remove-Item -Recurse -Force"),
            ("deny", "gci '" + G + BS + "extensions' | Remove-Item -Recurse -Force"),
            ("deny", "gci '" + G + "' | % { $_.Delete($true) }"),
        ])

    def test_TWIN_children_inside_one_mod_advise_not_deny(self):
        # ask -> advise 2026-10-02 (user: X4-folder deletes are advisories; 0 of 14 prompts refused).
        # The twin still holds its point: one mod's children never reach the root's HARD deny.
        self.expect([("advise", "gci '" + self.G + BS + "extensions" + BS + "amod' | Remove-Item -Recurse")])


class TestRR6BarePythonScope(_RR):
    """MINOR: the bare-python deny fired on `python -m pytest` in ANY directory, and
    `python3.10`/`python3.12` walked past it."""

    def test_versioned_interpreters_are_bare_too(self):
        self.expect([
            ("deny", "cd tools/x4validate && python3.10 -m pytest -q"),
            ("deny", "python3.12 tools/x4validate/gates/x.py"),
            ("deny", "cd tools/x4validate && python -m pytest -q"),
        ], tool="Bash")

    def test_the_session_cwd_counts_as_the_shell_directory(self):
        cwd = str(self.tk / "tools" / "x4validate")
        self.assertEqual(self.verdict_with("python -m pytest -q", tool="Bash", cwd=cwd)[0], "deny")

    def test_TWIN_pytest_elsewhere_is_not_toolkit_code(self):
        self.expect([
            ("allow", "cd /c/work/myproject && python -m pytest -q"),
            ("allow", "python -m pytest tests/"),
            ("allow", "python3.10 -c 'print(1)'"),
        ], tool="Bash")
        self.assertEqual(self.verdict_with("python -m pytest -q", tool="Bash",
                                           cwd="C:/work/other")[0], "allow")


class TestRR7UnparseablePowerShellDenies(_RR):
    """MINOR: a PowerShell command that does not PARSE was an ASK -- the user was
    prompted for Claude's own syntax error. Hygiene is a DENY with an actionable reason."""

    def test_a_syntax_error_denies_with_a_reason(self):
        v, r = self.verdict_with("if ($x) { Write-Host 'ok' ")
        self.assertEqual(v, "deny")
        self.assertIn("does not parse", r)

    def test_TWIN_no_powershell_to_parse_with_refuses(self):
        old = self.env
        self.env = dict(old, X4_PWSH=str(self.tmp / "no-such-pwsh.exe"))
        try:
            self.assert_scope_refusal("Get-Date")
        finally:
            self.env = old


class TestHygieneDeniesInBash(_RR):
    """2026-10-02, user: "what's the point of an ask hook on me if all I'm ever going to do
    is hit approve". A Bash command that does not PARSE, or nests past the expansion bound,
    is Claude's own hygiene -- the same call TestRR7 made for PowerShell. DENY with a reason
    Claude can act on; never a prompt. (bash -n rejecting it means bash would not run it.)"""

    def _deep(self):
        def build(depth, n, tag="q"):
            if depth == 0:
                return "echo " + tag
            return " ".join("$(" + build(depth - 1, n, tag + chr(97 + i)) + ")" for i in range(n))
        return "echo " + build(8, 3)

    def test_an_unparseable_bash_command_denies_with_a_reason(self):
        v, r = self.verdict_with("echo 'unterminated", tool="Bash")
        self.assertEqual(v, "deny")
        self.assertIn("does not PARSE", r)

    def test_nesting_past_the_bound_denies_with_a_reason(self):
        v, r = self.verdict_with(self._deep(), tool="Bash")
        self.assertEqual(v, "deny")
        self.assertIn("stopped expanding", r)

    def test_TWIN_a_parseable_shallow_command_is_still_allowed(self):
        self.assertEqual(self.verdict_with("echo 'balanced'", tool="Bash")[0], "allow")

    def test_TWIN_under_X4_GUARD_CHECK_both_still_signal_checked_nothing(self):
        """x4guard reads exit 2 as "could not evaluate" (inert). The deny must not hide it."""
        env = dict(self.env, X4_GUARD_CHECK="1")
        for cmd in ("echo 'unterminated", self._deep()):
            payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
            p = subprocess.run([BASH, str(HOOKS / "protect-bash.sh")], input=payload,
                               capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(p.returncode, 2, cmd[:60])


class TestRRPreArcBash(_RR):
    """Reviewer's PRE-ARC notes: the same fail-closed principle in the Bash half. A delete
    or write target that is resolvable DENIES like the literal form; one that is not, but
    sits against a protected root, ASKS."""

    def test_xargs_for_and_realpath(self):
        Rf, Gf = self.Rf, self.Gf
        self.expect([
            ("deny", "echo '" + Rf + "' | xargs rm -rf"),
            ("deny", "printf '%s' '" + Gf + "' | xargs -0 rm -rf"),
            ("advise", "find '" + Gf + "/extensions' -name '*.bak' | xargs rm -f"),
            ("deny", "for f in '" + Rf + "'/*; do rm -rf \"$f\"; done"),
            ("deny", "for d in build '" + Gf + "'; do rm -rf \"$d\"; done"),
            ("deny", "t=$(realpath -m '" + Rf + "/libraries'); rm -rf \"$t\""),
            ("deny", "t=$(cygpath -u '" + Gf + "'); rm -rf \"$t\""),
        ], tool="Bash")

    def test_rsync_delete_ln_touch_chmod_robocopy(self):
        Rf, Gf, G = self.Rf, self.Gf, self.G
        self.expect([
            ("deny", "rsync -a --delete empty/ '" + Gf + "/'"),
            ("advise", "rsync -a --delete ./mod/ '" + Gf + "/extensions/amod/'"),
            ("deny", "ln -sf /dev/null '" + Rf + "/libraries/w.xml'"),
            ("deny", "touch '" + Rf + "/libraries/w.xml'"),
            ("deny", "chmod 000 '" + Rf + "/libraries'"),
            ("deny", "robocopy C:/empty '" + Gf + "' //MIR"),
            ("advise", "robocopy ./mod '" + Gf + "/extensions/amod' /MIR"),
        ], tool="Bash")
        self.expect([
            ("deny", "robocopy C:\\empty '" + G + "' /MIR"),
            ("ask", "foreach ($f in Get-ChildItem '" + self.S + "') { Remove-Item $f.FullName }"),
            ("deny", "foreach ($f in gci '" + self.R + "') { Remove-Item $f.FullName -Recurse }"),
        ])

    def test_TWIN_ordinary_work_is_untouched(self):
        Rf = self.Rf
        self.expect([
            ("allow", "find . -name '*.pyc' | xargs rm -f"),
            ("allow", "echo a.txt b.txt | xargs rm -f"),
            ("allow", "for f in *.tmp; do rm -f \"$f\"; done"),
            ("allow", "t=$(realpath -m ./build); rm -rf \"$t\""),
            ("allow", "t=$(mktemp -d); rm -rf \"$t\""),
            ("allow", "rsync -a --delete ./a/ ./b/"),
            ("allow", "touch ./x.txt && chmod +x ./s.sh"),
            ("allow", "ln -s '" + Rf + "/libraries' ./lib"),
            ("allow", "robocopy ./a ./b //MIR"),
            ("advise", "rsync -a ./mod/ '" + self.Gf + "/extensions/amod/'"),
        ], tool="Bash")
        self.expect([("allow", "foreach ($f in gci .\\build) { Remove-Item $f.FullName }")])


class TestLaneFRelativePathsUseThePayloadCwd(_RR):
    """Lane F (2026-10-02): a relative operand is judged where the shell STARTS -- the
    payload's top-level `cwd`. MEASURED on the deployed guard with cwd = the folder holding
    reference/: rows 1-2 were ALLOW, rows 3-4 deny. Here the fixture's tmp folder holds
    reference/, as Desktop/Modding/X4 does on the reference machine."""

    def _v(self, command, cwd, tool="Bash"):
        extra = {} if cwd is None else {"cwd": cwd}
        return self.verdict_with(command, tool, **extra)[0]

    def test_the_four_row_table(self):
        mod = self.tmp.as_posix()
        rel = "reference/libraries/__p.xml"
        rows = [
            ("deny", "r" + "m -f " + rel),
            ("deny", "echo x > " + rel),
            ("deny", "r" + "m -f " + mod + "/" + rel),
            ("deny", "cd " + mod + " && r" + "m -f " + rel),
        ]
        bad = [(c, self._v(c, mod)) for want, c in rows if self._v(c, mod) != want]
        self.assertEqual(bad, [])
        # Windows-spelled cwd, as Claude Code sends it on Windows.
        self.assertEqual(self._v(rows[0][1], str(self.tmp).replace("/", BS)), "deny")

    def test_powershell_relative_paths_resolve_too(self):
        mod = str(self.tmp)
        self.assertEqual(self._v("Remove-Item reference" + BS + "libraries" + BS + "__p.xml", mod,
                                 tool="PowerShell"), "deny")
        self.assertEqual(self._v("Set-Content -Path reference" + BS + "libraries" + BS + "__p.xml"
                                 " -Value x", mod, tool="PowerShell"), "deny")

    def test_TWIN_powershell_from_an_unrelated_cwd_allows(self):
        other = self.tmp / "elsewhere"
        other.mkdir(exist_ok=True)
        self.assertEqual(self._v("Remove-Item reference" + BS + "libraries" + BS + "__p.xml",
                                 str(other), tool="PowerShell"), "allow")

    def test_TWINS_unrelated_missing_and_garbage_cwd_and_a_cd_that_wins(self):
        rel_del = "r" + "m -f reference/libraries/__p.xml"
        other = (self.tmp / "elsewhere").as_posix()
        self.assertEqual(self._v(rel_del, other), "allow")
        self.assertEqual(self._v(rel_del, None), "allow")
        self.assertEqual(self._v(rel_del, "Modding/X4"), "allow")
        self.assertEqual(self._v(rel_del, "::garbage::"), "allow")
        self.assertEqual(self._v("cd " + other + " && " + rel_del, self.tmp.as_posix()), "allow")


class TestLaneIConfigLocation(unittest.TestCase):
    """Plan 3 lane I: the guards read <tk>/x4-paths.env, still read the 3.x copy, keep the
    default reference HARD BLOCK with no config, and SAY so at session start."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="lanei_"))
        self.tk = self.tmp / "tk"
        (self.tk / "reference" / "libraries").mkdir(parents=True)
        (self.tmp / "elsewhere" / "libraries").mkdir(parents=True)
        self.env = _owned_env(self.tmp, X4_TOOLKIT=str(self.tk))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cfg(self, rel):
        p = self.tk / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('X4_REFERENCE="%s"\n' % (self.tmp / "elsewhere").as_posix(),
                     encoding="utf-8", newline="\n")

    def edit(self, path):
        payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": path.as_posix()}})
        p = subprocess.run([BASH, str(HOOKS / "protect-files.sh")], input=payload,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=self.env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-300:])
        if not p.stdout.strip():
            return "allow"
        return json.loads(p.stdout)["hookSpecificOutput"].get("permissionDecision", "advise")

    def banner(self):
        p = subprocess.run([BASH, str(HOOKS / "session-canary.sh")], input="{}",
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=self.env, timeout=60)
        return [l for l in p.stdout.splitlines() if l.startswith("[x4 config]")]

    def test_no_config_still_HARD_BLOCKS_the_default_reference(self):
        self.assertEqual(self.edit(self.tk / "reference" / "libraries" / "w.xml"), "deny")

    def test_the_NEW_file_is_read_by_the_hooks(self):
        self.cfg("x4-paths.env")
        self.assertEqual(self.edit(self.tmp / "elsewhere" / "libraries" / "w.xml"), "deny")

    def test_TWIN_the_LEGACY_file_is_still_read_by_the_hooks(self):
        self.cfg(".claude/x4-paths.env")
        self.assertEqual(self.edit(self.tmp / "elsewhere" / "libraries" / "w.xml"), "deny")

    def test_banner_NAMES_the_gap_when_nothing_is_configured(self):
        lines = self.banner()
        self.assertTrue(lines and "NO PATH CONFIG" in lines[0], lines)
        joined = " ".join(lines)
        self.assertIn((self.tk / "x4-paths.env").as_posix().lower(), joined.replace("\\", "/").lower())

    def test_TWIN_no_banner_when_the_reference_is_EXPORTED(self):
        self.env["X4_REFERENCE"] = str(self.tmp / "elsewhere")
        self.assertEqual(self.banner(), [])

    def test_TWIN_no_banner_when_the_NEW_file_exists(self):
        self.cfg("x4-paths.env")
        self.assertEqual(self.banner(), [])

    def test_banner_says_DEPRECATED_for_the_legacy_location(self):
        self.cfg(".claude/x4-paths.env")
        lines = self.banner()
        self.assertTrue(lines and "DEPRECATED" in lines[0] and "x4config.py migrate" in lines[0], lines)

    def test_banner_says_TWO_when_both_exist_and_prints_no_VALUE(self):
        self.cfg("x4-paths.env"); self.cfg(".claude/x4-paths.env")
        lines = self.banner()
        self.assertTrue(lines and "TWO path configs" in lines[0], lines)
        self.assertNotIn("elsewhere", " ".join(lines))


@unittest.skipUnless(BASH and shutil.which("git"), "bash or git unavailable; the session hook is not exercised")
class TestF11UncommittedKnowledgebase(unittest.TestCase):
    """F11 (2026-10-20): Codex appended ~1,767 lines to a game root's KNOWLEDGEBASE.md over 2.5
    weeks and never committed them. The session hook now names an uncommitted knowledgebase in
    the hook's own root. One twin per clause; the fixture repo pins core.autocrlf=false so the
    line-ending churn case is real (MEASURED: plain --numstat counted 103 added for 3)."""

    KB = "KNOWLEDGEBASE.md"

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="f11kb_"))
        self.root = self.tmp / "root"
        shutil.copytree(HOOKS, self.root / ".claude" / "hooks",
                        ignore=shutil.ignore_patterns("__pycache__", "test*"))
        self.base = b"".join(b"fact %d\n" % i for i in range(100))

    def tearDown(self):
        # Same-run scratch. git writes its objects READ-ONLY, and on Windows a bare
        # rmtree(ignore_errors=True) silently left every .git/objects file behind --
        # MEASURED 2026-10-20: 188 f11kb_* dirs in one temp folder, all 69 files sampled
        # read-only objects. Clear the bit and retry the one call that failed.
        def _writable(func, path, _exc):
            os.chmod(path, 0o700)
            func(path)
        kw = {"onexc": _writable} if sys.version_info >= (3, 12) else {"onerror": _writable}
        try:
            shutil.rmtree(self.tmp, **kw)
        except OSError:
            pass   # left for x4housekeep's temp sweep (f11kb_ is one of its prefixes)

    def git(self, *a):
        r = subprocess.run(["git", "-C", str(self.root), *a], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def repo(self, files=None):
        self.git("init", "-q")
        for k, v in (("user.email", "t@example.invalid"), ("user.name", "t"),
                     ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
            self.git("config", k, v)
        for name, data in (files or {self.KB: self.base}).items():
            (self.root / name).write_bytes(data)
            self.git("add", "--", name)
        self.git("commit", "-q", "-m", "c")

    def run_hook(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("X4_") and k not in ("CLAUDE_PROJECT_DIR", "HOOK_DIR", "PYTHONPATH")}
        env.update(X4_TOOLKIT=str(self.tmp / "no-toolkit"), X4_PYTHON=sys.executable,
                   X4_CONFIG=str(self.tmp / "none.env"), HOME=str(self.tmp / "home"),
                   X4_HOUSEKEEP_SWEEP="off", **extra)
        p = subprocess.run([BASH, str(self.root / ".claude" / "hooks" / "session-canary.sh")],
                           input="{}", capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        return [ln for ln in p.stdout.splitlines() if ln.startswith("[x4 kb]")], p.stdout

    def test_uncommitted_additions_are_named_with_the_count_and_the_directive(self):
        self.repo()
        (self.root / self.KB).write_bytes(self.base + b"new a\nnew b\nnew c\n")
        lines, _ = self.run_hook()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("KNOWLEDGEBASE.md has 3 uncommitted added line(s)", lines[0])
        self.assertIn("Commit it alone (explicit path)", lines[0])
        self.assertRegex(lines[0], r"last commit of it: \d{4}-\d\d-\d\d")

    def test_TWIN_a_clean_tree_prints_nothing(self):
        self.repo()
        self.assertEqual(self.run_hook()[0], [])

    def test_line_ending_churn_is_not_counted(self):
        self.repo()
        (self.root / self.KB).write_bytes((self.base + b"x\ny\nz\n").replace(b"\n", b"\r\n"))
        lines, _ = self.run_hook()
        self.assertTrue(lines and "has 3 uncommitted added" in lines[0], lines)

    def test_TWIN_line_ending_churn_ALONE_prints_nothing(self):
        self.repo()
        (self.root / self.KB).write_bytes(self.base.replace(b"\n", b"\r\n"))
        self.assertEqual(self.run_hook()[0], [])

    def test_STAGED_but_uncommitted_additions_are_counted(self):
        self.repo()
        (self.root / self.KB).write_bytes(self.base + b"staged\n")
        self.git("add", "--", self.KB)
        lines, _ = self.run_hook()
        self.assertTrue(lines and "has 1 uncommitted added" in lines[0], lines)

    def test_the_LOCAL_notes_file_is_checked_too(self):
        self.repo({self.KB: self.base, "KNOWLEDGEBASE.local.md": b"## Notes\n"})
        (self.root / "KNOWLEDGEBASE.local.md").write_bytes(b"## Notes\nmine\n")
        lines, _ = self.run_hook()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("KNOWLEDGEBASE.local.md has 1 uncommitted", lines[0])

    def test_an_UNTRACKED_knowledgebase_is_named_as_never_committed(self):
        self.repo({"other.txt": b"x\n"})
        (self.root / self.KB).write_bytes(self.base)
        lines, _ = self.run_hook()
        self.assertTrue(lines and "untracked" in lines[0] and self.KB in lines[0], lines)

    def test_TWIN_an_IGNORED_knowledgebase_is_the_users_choice_and_silent(self):
        self.repo({".gitignore": b"/KNOWLEDGEBASE.local.md\n"})
        (self.root / "KNOWLEDGEBASE.local.md").write_bytes(b"## Notes\nmine\n")
        self.assertEqual(self.run_hook()[0], [])

    def test_TWIN_not_a_git_repo_prints_nothing(self):
        (self.root / self.KB).write_bytes(self.base + b"more\n")
        self.assertEqual(self.run_hook(GIT_CEILING_DIRECTORIES=str(self.tmp))[0], [])

    def test_a_repo_git_REFUSES_is_said_not_skipped(self):
        """Review LOW-1: a failed rev-parse other than "not a git repository" (dubious ownership,
        a broken config) read as "not a repo" and the check skipped silently. A malformed
        .git/config is the portable stand-in (MEASURED: rc 128, "bad config line")."""
        self.repo()
        (self.root / self.KB).write_bytes(self.base + b"more\n")
        (self.root / ".git" / "config").write_bytes(b"[core\n")
        lines, _ = self.run_hook()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("could not check the knowledgebase: git refused this folder (", lines[0])
        self.assertLess(len(lines[0]), 260, lines[0])

    def test_the_line_follows_GUARDS_OFF_and_precedes_the_preferences(self):
        """#38: the preview keeps the HEAD. GUARDS OFF stays first (K7); the directive comes
        before the up-to-4 KiB preferences block."""
        self.repo({self.KB: self.base, "KNOWLEDGEBASE.local.md": b"## My preferences\nKeep it.\n"})
        (self.root / self.KB).write_bytes(self.base + b"more\n")
        _, out = self.run_hook(X4_GUARD="off")
        self.assertTrue(out.lstrip().startswith("[x4 guards] *** GUARDS OFF"), out[:300])
        # The preferences block, injected or (in this bare fixture) its NOT INJECTED line.
        pref = max(out.find("Keep it."), out.find("PREFERENCES"))
        kb = out.find("[x4 kb]")
        self.assertTrue(0 < kb < pref, (kb, pref, out[:600]))

    def test_no_file_content_is_printed(self):
        self.repo()
        (self.root / self.KB).write_bytes(self.base + b"SECRET-CONTENT-LINE\n")
        _, out = self.run_hook()
        self.assertNotIn("SECRET-CONTENT-LINE", out)


if __name__ == "__main__":
    unittest.main()
