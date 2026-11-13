"""Bind automation to the actual connected executable and explicit X4 profile.

Windows-only production readers. No process launching, settings edits or command
line disclosure. The profile flag is corroborating launch context, not proof of
the effective mod set, which still requires installed and live validation.
"""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
from ._livesession import SessionUnavailable, connected_pipe_peer


def profile_from_argv(argv):
    if not isinstance(argv, list) or not argv or any(not isinstance(x,str) for x in argv):
        raise SessionUnavailable("invalid process arguments")
    flags=[i for i,x in enumerate(argv) if x.casefold().startswith("-personalfolder")]
    if (len(flags)!=1 or argv[flags[0]].casefold()!="-personalfolderid"
            or flags[0]+1>=len(argv) or not re.fullmatch("[0-9]{1,20}",argv[flags[0]+1])):
        raise SessionUnavailable("automation requires one explicit -personalfolderid argument")
    return argv[flags[0]+1]


def windows_argv(command):
    if os.name!="nt":
        raise SessionUnavailable("native argv parsing is Windows-only")
    from ctypes import wintypes as W
    if not isinstance(command,str) or not command or len(command)>32768 or "\0" in command:
        raise SessionUnavailable("process command line unavailable")
    shell=ctypes.WinDLL("shell32",use_last_error=True)
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    shell.CommandLineToArgvW.argtypes=[W.LPCWSTR,ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype=ctypes.POINTER(W.LPWSTR)
    kernel.LocalFree.argtypes=[W.HLOCAL];kernel.LocalFree.restype=W.HLOCAL
    count=ctypes.c_int()
    result=shell.CommandLineToArgvW(command,ctypes.byref(count))
    if not result:
        raise SessionUnavailable("cannot parse process command line")
    try:
        if not 0<count.value<=4096:
            raise SessionUnavailable("process argument count refused")
        return [result[i] for i in range(count.value)]
    finally:
        kernel.LocalFree(result)


def process_command_line(pid):
    if os.name!="nt" or type(pid) is not int or not 0<pid<2**32:
        raise SessionUnavailable("process command query requires a Windows PID")
    # PID is an integer, never interpolated caller text. Return only the requested
    # process command line; do not enumerate/log other users' command lines.
    command=("[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').CommandLine | ConvertTo-Json -Compress")
    try:
        result=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-Command",command],
            capture_output=True,text=True,encoding="utf-8",errors="strict",timeout=8,check=True,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        if len(result.stdout)>131072:
            raise ValueError("oversize command response")
        value=json.loads(result.stdout)
        if not isinstance(value,str) or not value or len(value)>32768:
            raise ValueError("empty or oversized command response")
        return value
    except Exception as exc:
        raise SessionUnavailable("cannot verify connected process profile") from exc


class ProfilePeer:
    def __init__(self, handle, expected_image, profile_id, *,
                 peer_reader=connected_pipe_peer, command_reader=process_command_line,
                 argv_reader=windows_argv):
        if not isinstance(profile_id,str) or not re.fullmatch("[0-9]{1,20}",profile_id):
            raise SessionUnavailable("explicit numeric profile id required")
        self.handle,self.profile_id=handle,profile_id
        self.expected=Path(expected_image).resolve(strict=True)
        self.peer_reader,self.command_reader,self.argv_reader=peer_reader,command_reader,argv_reader
        self.qualified=None

    def __call__(self):
        before=self.peer_reader(self.handle)
        try:
            actual=Path(before["image"]).resolve(strict=True)
            if not os.path.samefile(actual,self.expected):
                raise SessionUnavailable("connected executable differs from approved game path")
            if self.qualified is None:
                args=self.argv_reader(self.command_reader(before["pid"]))
                if profile_from_argv(args)!=self.profile_id:
                    raise SessionUnavailable("connected game uses another profile")
                after=self.peer_reader(self.handle)
                if before!=after:
                    raise SessionUnavailable("process identity changed during profile query")
                self.qualified=dict(before)
            elif before!=self.qualified:
                raise SessionUnavailable("connected process changed")
            return dict(before)
        except SessionUnavailable:
            raise
        except Exception as exc:
            raise SessionUnavailable("connected executable/profile unqualified") from exc

