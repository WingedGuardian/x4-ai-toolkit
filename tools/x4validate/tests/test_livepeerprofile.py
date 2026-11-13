"""Native Windows argv parsing plus synthetic process readers; no game is opened."""
import os
from pathlib import Path
import pytest
from x4validate._livepeerprofile import profile_from_argv, windows_argv, ProfilePeer
from x4validate._livesession import SessionUnavailable


@pytest.mark.parametrize("argv",[
    [],["X4.exe"],["X4.exe","-personalfolderid"],
    ["X4.exe","-personalfolderid","other"],
    ["X4.exe","-personalfolderid123"],
    ["X4.exe","-personalfolder","C:/somewhere"],
    ["X4.exe","-personalfolderid","123","-personalfolderid","123"],
    ["X4.exe","-personalfolderid","123","-personalfolder","x"],
    ["X4.exe","-personalfolderid","123x"],["X4.exe","-personalfolderid",True],
])
def test_ambiguous_or_missing_profile_refuses(argv):
    with pytest.raises(SessionUnavailable): profile_from_argv(argv)


def test_exact_profile_readback():
    assert profile_from_argv(["X4.exe","-personalfolderid","123","-debug","all"])=="123"


@pytest.mark.skipif(os.name!="nt",reason="native Windows command-line API unavailable")
def test_actual_native_windows_argv_parser():
    argv=windows_argv('"C:\\Games With Spaces\\X4.exe" -personalfolderid 123 -logfile "debug log.txt"')
    assert argv[0]=="C:\\Games With Spaces\\X4.exe"
    assert argv[-1]=="debug log.txt" and profile_from_argv(argv)=="123"


def setup(tmp_path, **kwargs):
    image=tmp_path/"X4.exe";image.write_bytes(b"synthetic executable placeholder")
    peer=dict(pid=17,creation_filetime="123456789",image=str(image))
    commands=[]
    def command(pid):
        commands.append(pid);return "opaque command consumed by injected argv parser"
    reader=kwargs.pop("peer_reader",lambda _:dict(peer))
    obj=ProfilePeer(123,image,"123",peer_reader=reader,command_reader=command,
                    argv_reader=kwargs.pop("argv_reader",lambda _:["X4.exe","-personalfolderid","123"]))
    return obj,peer,commands


def test_same_connected_peer_profile_checked_once(tmp_path):
    obj,peer,commands=setup(tmp_path)
    assert obj()==peer and obj()==peer and commands==[17]


def test_wrong_profile_refuses(tmp_path):
    obj,peer,commands=setup(tmp_path,argv_reader=lambda _:["X4.exe","-personalfolderid","124"])
    with pytest.raises(SessionUnavailable): obj()
    assert obj.qualified is None


def test_changed_process_between_profile_reads_refuses(tmp_path):
    obj,peer,commands=setup(tmp_path)
    reads=iter([peer,dict(peer,creation_filetime="987654321")])
    obj.peer_reader=lambda _:next(reads)
    with pytest.raises(SessionUnavailable): obj()
    assert obj.qualified is None


def test_wrong_image_refuses_before_command_query(tmp_path):
    obj,peer,commands=setup(tmp_path)
    other=tmp_path/"Other.exe";other.write_bytes(b"another")
    obj.peer_reader=lambda _:dict(peer,image=str(other))
    with pytest.raises(SessionUnavailable): obj()
    assert commands==[]


def test_changed_qualified_process_refuses(tmp_path):
    obj,peer,commands=setup(tmp_path);obj()
    obj.peer_reader=lambda _:dict(peer,pid=18)
    with pytest.raises(SessionUnavailable): obj()
    assert commands==[17]

