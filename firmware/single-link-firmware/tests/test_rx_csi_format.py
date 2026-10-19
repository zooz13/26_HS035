"""Compile and execute RX firmware's actual parser/Base64/CSV helpers with a host compiler.

Run from repository root: python -m pytest tests/test_rx_csi_format.py -q
Set CSI_HOST_CC to a native GCC/Clang executable if it is not on PATH.
"""
import base64
import csv
import glob
import io

import pytest
import os
from pathlib import Path
import shutil
import subprocess


@pytest.fixture(scope="module")
def native_output(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("rx-csi-native")
    compiler = os.environ.get("CSI_HOST_CC") or shutil.which("clang") or shutil.which("gcc")
    if not compiler and os.name == "nt":
        candidates = glob.glob("C:/Program Files/Microsoft Visual Studio/*/*/VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe")
        compiler = candidates[-1] if candidates else None
    assert compiler, "Native Clang, GCC, or MSVC is required (set CSI_HOST_CC)"
    source = Path(__file__).with_name("rx_csi_format_test.c")
    executable = tmp_path / ("rx_csi_format_test.exe" if os.name == "nt" else "rx_csi_format_test")
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

    return result.stdout.splitlines()


def test_all_base64_lengths_round_trip(native_output):
    rows = [line.split(",", 2) for line in native_output if line.startswith("B64,")]
    assert len(rows) == 768
    for _, length, encoded in rows:
        n = int(length)
        decoded = base64.b64decode(encoded, validate=True)
        assert decoded == bytes((i * 73 + n) & 255 for i in range(n))
        assert len(encoded) == 4 * ((n + 2) // 3)


def row(native_output, prefix):
    line = next(line[len(prefix) + 1:] for line in native_output if line.startswith(prefix + ","))
    return next(csv.reader(io.StringIO(line)))


def test_actual_csv_header_and_raw_fixture(native_output):
    header = row(native_output, "HEADER")
    fixture = row(native_output, "FIXTURE")
    expected = "type,id,mac,rssi,rate,sig_mode,mcs,bandwidth,smoothing,not_sounding,aggregation,stbc,fec_coding,sgi,noise_floor,fft_gain,agc_gain,compensate_gain_x1000000,ampdu_cnt,channel,secondary_channel,local_timestamp,ant,sig_len,rx_format,len,first_word,data_b64".split(",")
    assert header == expected
    assert len(fixture) == len(header) == 28
    values = dict(zip(header, fixture))
    assert values["type"] == "CSI_DATA_B64"
    assert values["id"] == "42"
    assert values["len"] == "5"
    assert values["first_word"] == "1"
    assert values["compensate_gain_x1000000"] == "1000000"
    assert base64.b64decode(values["data_b64"], validate=True) == bytes([0, 1, 127, 128, 255])


def test_metadata_ranges_and_maximum_payload(native_output):
    header = row(native_output, "HEADER")
    extremes = row(native_output, "EXTREMES")
    assert len(extremes) == len(header) == 28
    values = dict(zip(header, extremes))
    assert values["id"] == "4294967295"
    assert values["local_timestamp"] == "2147483648"
    assert values["rssi"] == values["noise_floor"] == values["fft_gain"] == "-128"
    assert values["agc_gain"] == "255"
    assert values["sig_len"] == "65535"
    assert values["compensate_gain_x1000000"] == "-2147483648"
    assert base64.b64decode(values["data_b64"], validate=True) == bytes([0, 1, 127, 128, 255])
    maximum = row(native_output, "MAXIMUM")
    assert len(maximum) == 28
    assert maximum[25] == "768"
    assert base64.b64decode(maximum[27], validate=True) == bytes(768)


def test_native_packet_bounds_capacity_and_commands(native_output):
    # Native assertions cover malformed/truncated action frames, control-vs-data,
    # sequence endianness/wrap, nulls, every encoder capacity, and local commands.
    assert native_output[-1] == "RX CSI format tests passed"


def test_gain_progress_preserves_received_prefix(native_output):
    assert "GAIN_PROGRESS,first100,50-boundary,101-boundary,queue-gaps,passed" in native_output
