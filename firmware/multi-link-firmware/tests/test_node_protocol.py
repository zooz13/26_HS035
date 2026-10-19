"""Compile and execute firmware's pure C protocol tests with a host compiler.

Run from repository root: python -m pytest tests/test_node_protocol.py -q
Set CSI_HOST_CC to a native GCC/Clang executable if it is not on PATH.
"""
import glob
import os
from pathlib import Path
import shutil
import subprocess


def test_native_node_protocol(tmp_path):
    compiler = os.environ.get("CSI_HOST_CC") or shutil.which("clang") or shutil.which("gcc")
    if not compiler and os.name == "nt":
        candidates = glob.glob("C:/Program Files/Microsoft Visual Studio/*/*/VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe")
        compiler = candidates[-1] if candidates else None
    assert compiler, "Native Clang, GCC, or MSVC is required (set CSI_HOST_CC)"
    source = Path(__file__).with_name("node_protocol_test.c")
    executable = tmp_path / ("node_protocol_test.exe" if os.name == "nt" else "node_protocol_test")
    command = [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O1"]
    environment = os.environ.copy()
    if os.name == "nt":
        if "clang" in Path(compiler).name:
            command += ["--target=x86_64-pc-windows-msvc"]
        # Espressif's Clang does not run vcvarsall automatically.
        msvc = sorted(glob.glob("C:/Program Files/Microsoft Visual Studio/*/*/VC/Tools/MSVC/*"))
        sdk = sorted(glob.glob("C:/Program Files (x86)/Windows Kits/10/Include/*"))
        if msvc and sdk:
            vc = Path(msvc[-1])
            kits = Path(sdk[-1])
            environment["PATH"] = str(vc / "bin/Hostx64/x64") + os.pathsep + environment["PATH"]
            environment["INCLUDE"] = os.pathsep.join(str(p) for p in [vc / "include", kits / "ucrt", kits / "shared", kits / "um"])
            environment["LIB"] = os.pathsep.join(str(p) for p in [vc / "lib/x64", kits.parent.parent / "Lib" / kits.name / "ucrt/x64", kits.parent.parent / "Lib" / kits.name / "um/x64"])
            environment["VCToolsInstallDir"] = str(vc) + os.sep
    if Path(compiler).name.lower() == "cl.exe":
        command = [compiler, "/nologo", "/std:c11", "/W4", "/WX", "/O1", str(source),
                   "/Fe:" + str(executable), "/Fo:" + str(tmp_path / "test.obj")]
    else:
        command += [str(source), "-o", str(executable)]
    compiled = subprocess.run(command, capture_output=True, text=True, env=environment)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run([str(executable)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "passed" in result.stdout
