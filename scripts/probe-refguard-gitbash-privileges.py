"""Scratch-only diagnostic: unchanged ACLs, Git Bash, and child token privileges.

Never imported by the product. Privileges are removed only in a disposable child
process; the caller and pytest retain their tokens. No environment values or
identities are emitted. Results are measurements, not an acceptance waiver.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parent.parent
NAMES = ("SeBackupPrivilege", "SeRestorePrivilege")
COMMANDS = {
    "bash_redirect": 'echo x > "$T/libraries/wares.xml"',
    "rm_f": 'rm -f "$T/libraries/wares.xml"',
    "rm_rf_tree": 'rm -rf "$T/libraries"',
}


def child(root, name, remove):
    root = Path(root).resolve()
    temp = Path(tempfile.gettempdir()).resolve()
    assert root != temp and root.is_relative_to(temp)
    assert name in COMMANDS
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    k.GetCurrentProcess.restype = w.HANDLE
    k.CloseHandle.argtypes = [w.HANDLE]
    a.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    a.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]

    class LUID(ctypes.Structure):
        _fields_ = [("low", w.DWORD), ("high", w.LONG)]

    class ENTRY(ctypes.Structure):
        _fields_ = [("luid", LUID), ("attributes", w.DWORD)]

    class PRIVS(ctypes.Structure):
        _fields_ = [("count", w.DWORD), ("entries", ENTRY * 1)]

    a.LookupPrivilegeValueW.argtypes = [w.LPCWSTR, w.LPCWSTR, ctypes.POINTER(LUID)]
    a.AdjustTokenPrivileges.argtypes = [w.HANDLE, w.BOOL, ctypes.POINTER(PRIVS), w.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    token = w.HANDLE()
    assert a.OpenProcessToken(k.GetCurrentProcess(), 0x20 | 0x08, ctypes.byref(token)), ctypes.get_last_error()
    luids = {}
    for privilege in NAMES:
        value = LUID()
        assert a.LookupPrivilegeValueW(None, privilege, ctypes.byref(value))
        luids[privilege] = value

    def snapshot():
        size = w.DWORD()
        a.GetTokenInformation(token, 3, None, 0, ctypes.byref(size))
        assert size.value
        buf = ctypes.create_string_buffer(size.value)
        assert a.GetTokenInformation(token, 3, buf, size, ctypes.byref(size))
        count = w.DWORD.from_buffer(buf).value
        entries = (ENTRY * count).from_buffer(buf, PRIVS.entries.offset)
        found = {(e.luid.low, e.luid.high): e.attributes for e in entries}
        return {n: {"present": (v.low, v.high) in found,
                    "enabled": bool(found.get((v.low, v.high), 0) & 2)}
                for n, v in luids.items()}

    try:
        before = snapshot()
        if remove:
            for n, v in luids.items():
                if before[n]["present"]:
                    request = PRIVS(1, (ENTRY(v, 4),))
                    ctypes.set_last_error(0)
                    assert a.AdjustTokenPrivileges(token, False, ctypes.byref(request), 0, None, None)
                    assert ctypes.get_last_error() == 0
        after = snapshot()
        if remove:
            assert not any(v["present"] for v in after.values()), after
    finally:
        k.CloseHandle(token)
    sys.path.insert(0, str(REPO / "scripts"))
    import gitbash
    bash = gitbash.find_bash()
    assert bash, "Git Bash required; no skip"
    result = subprocess.run([bash, "-c", COMMANDS[name]],
                            env={**os.environ, "T": str(root)}, capture_output=True)
    print(json.dumps({"token_before": before, "token_after": after,
                      "primitive_rc": result.returncode}))


def snapshot(root):
    return {str(p.relative_to(root)): ("d" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(root.rglob("*"))}


def parent():
    assert os.name == "nt", "Windows diagnostic only"
    sys.path[:0] = [str(REPO / "scripts"), str(REPO / "tools/x4validate"),
                   str(REPO / "tools/x4validate/tests")]
    for name in list(os.environ):
        if name.startswith("X4_"):
            os.environ.pop(name)
    import x4refguard as guard
    from x4validate import _paths
    from refguard_owner import own_or_skip
    rows = []
    with tempfile.TemporaryDirectory(prefix="x4-acl-privilege-probe-") as scratch:
        scratch = Path(scratch).resolve()
        assert scratch.is_relative_to(Path(tempfile.gettempdir()).resolve())
        os.environ[guard.SANDBOX_ENV] = str(scratch)
        kit = scratch / "kit"
        kit.mkdir()
        _paths._SELF = kit
        _paths._EXPLICIT = None
        _paths._NOTICED = set()
        for name in COMMANDS:
            for remove in (False, True):
                for protected in (False, True):
                    root = scratch / (name + "-" + str(int(remove)) + "-" + str(int(protected)))
                    (root / "libraries").mkdir(parents=True)
                    (root / "libraries/wares.xml").write_text("<wares/>", encoding="utf-8")
                    (root / "01.cat").write_text("fixture", encoding="utf-8")
                    (root / ".unpacked-and-locked").write_text("fixture", encoding="utf-8")
                    own_or_skip(root, guard)
                    os.environ["X4_REFERENCE"] = str(root)
                    _paths.reload()
                    try:
                        if protected:
                            assert guard.main(["apply", "--yes"]) == 0
                            assert guard.report(full=True)["state"] == "protected"
                        before = snapshot(root)
                        run = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                              "--child", str(root), name, str(int(remove))],
                                             capture_output=True, text=True, check=True)
                        data = json.loads(run.stdout)
                        changed = before != snapshot(root)
                        rows.append({"primitive": name, "remove_privileges": remove,
                                     "protected": protected, "changed": changed, **data})
                        if not protected:
                            assert changed, "broken unprotected control"
                    finally:
                        guard._mutate_run(["icacls", root, "/reset", "/T", "/C", "/Q"], root)
        print("ACL_PRIVILEGE_MEASUREMENT=" + json.dumps(rows, sort_keys=True))
        assert len(rows) == 12


if __name__ == "__main__":
    if sys.argv[1:2] == ["--child"]:
        child(sys.argv[2], sys.argv[3], bool(int(sys.argv[4])))
    else:
        parent()
