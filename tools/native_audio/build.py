"""Explicit local MSVC build only. Never invokes capture or installs a binary."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser()
    for name in ("compiler","vc_include","vc_lib","sdk_root","sdk_version","output"):
        p.add_argument("--"+name.replace("_","-"),required=True)
    a=p.parse_args()
    source=Path(__file__).with_name("process_loopback.cpp").resolve(strict=True)
    compiler=Path(a.compiler).resolve(strict=True)
    root=Path(a.sdk_root).resolve(strict=True)
    output=Path(a.output).resolve(strict=True)
    if not output.is_dir() or output.is_symlink():raise ValueError("existing ordinary build directory required")
    exe=output/"x4-process-audio.exe";obj=output/"process_loopback.obj";receipt=output/"build-receipt.json"
    if any(x.exists() for x in (exe,obj,receipt)):raise ValueError("build outputs already exist; no implicit overwrite")
    includes=[Path(a.vc_include)]+[root/"Include"/a.sdk_version/n for n in ("ucrt","shared","um","winrt")]
    libs=[Path(a.vc_lib),root/"Lib"/a.sdk_version/"ucrt"/"x64",root/"Lib"/a.sdk_version/"um"/"x64"]
    for x in (*includes,*libs):
        if not x.is_dir():raise ValueError("missing exact toolchain directory")
    tools=[compiler,compiler.with_name("link.exe"),compiler.with_name("c1xx.dll"),compiler.with_name("c2.dll")]
    headers=[root/"Include"/a.sdk_version/"um"/n for n in ("audioclient.h","audioclientactivationparams.h","mmdeviceapi.h")]
    inputs=[source,Path(__file__).resolve(),*tools,*headers]
    before={str(x):sha(x) for x in inputs}
    argv=[str(compiler),"/nologo","/std:c++17","/EHsc","/O2","/MT","/W4","/D_WIN32_WINNT=0x0A00",
        "/DNTDDI_VERSION=0x0A00000A",'/DX4_AUDIO_SOURCE_SHA256="'+sha(source)+'"',
        *["/I"+str(x) for x in includes],str(source),"/Fo"+str(obj),"/Fe"+str(exe),
        "/link",*["/LIBPATH:"+str(x) for x in libs],"Mmdevapi.lib","Ole32.lib","uuid.lib"]
    env=dict(os.environ);env["PATH"]=str(compiler.parent)+os.pathsep+env.get("PATH","")
    r=subprocess.run(argv,cwd=output,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                     timeout=120,creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    print(r.stdout.decode("utf-8","replace"))
    if r.returncode:raise SystemExit(r.returncode)
    if {str(x):sha(x) for x in inputs}!=before:raise ValueError("build inputs changed")
    value={"status":"COMPILED_NOT_RUNTIME_QUALIFIED","source_sha256":sha(source),"helper_sha256":sha(exe),
           "observed_inputs":before,"compiler_argv":argv,
           "scope":"Named direct compiler binaries and three audio API headers; not complete transitive SDK closure.",
           "capture_invoked":False}
    with receipt.open("x",encoding="utf-8") as f:json.dump(value,f,indent=2);f.write("\n")
    print(json.dumps(value))
if __name__=="__main__":main()
