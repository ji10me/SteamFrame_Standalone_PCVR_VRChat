#!/usr/bin/env python3
"""Build an isolated integration candidate, leaving Steam's tool/prefix intact."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from framectl import FEX_BINS, audit_stage, sha256


def main(stage):
    if platform.system() != "Linux" or platform.machine() not in ("aarch64", "arm64"):
        raise RuntimeError("Run on the ARM64 Frame")
    stage = Path(stage).resolve()
    audit = audit_stage(stage)
    if audit["errors"]:
        raise RuntimeError(str(audit["errors"]))
    home = Path.home()
    base = home / ".local/share/frame-pcvr"
    standard = home / ".local/share/Steam/steamapps/common/FEX-Emu"
    standard_prefix = home / ".local/share/Steam/steamapps/compatdata/438100"
    prefix = base / "c" / ("438100-" + audit["provenance"]["commit"][:8])
    if prefix.exists():
        raise RuntimeError("Candidate prefix already exists; review it instead of overwriting")
    # A live prefix must not be copied. Only this title is relevant to the guard.
    for p in Path("/proc").glob("[0-9]*"):
        try:
            env = dict(x.split(b"=", 1) for x in (p / "environ").read_bytes().split(b"\0") if b"=" in x)
            if env.get(b"SteamAppId") == b"438100":
                raise RuntimeError(f"VRChat process still active: {p.name}; close this title first")
        except OSError:
            pass
    files = [p for p in standard.rglob("*") if p.is_file() and "rootfs" not in p.relative_to(standard).parts
             and "__pycache__" not in p.parts]
    original_hashes = {str(p.relative_to(standard)): sha256(p) for p in files}
    candidates = base / "candidates"
    candidates.mkdir(parents=True, exist_ok=True, mode=0o700)
    candidate = Path(tempfile.mkdtemp(prefix=audit["provenance"]["tag"] + "-", dir=candidates))
    for name in ("emulator.json", "fex-compat-tool", "unshare.py", "toolmanifest.vdf"):
        shutil.copy2(standard / name, candidate / name)
    for name in ("usr/lib", "usr/share"):
        if (standard / name).exists():
            shutil.copytree(standard / name, candidate / name, symlinks=True)
    (candidate / "usr/bin").mkdir(parents=True)
    for name in FEX_BINS:
        shutil.copy2(stage / "usr/bin" / name, candidate / "usr/bin" / name)
    for name in ("ConfigTemplate.json", "FEXCompatTool", "VERSIONS.txt"):
        shutil.copy2(stage / name, candidate / name)
    # The stock wrapper prints all environment variables into /tmp. Remove that
    # diagnostic from our private copy; leave the standard wrapper unchanged.
    wrapper = candidate / "fex-compat-tool"
    text = wrapper.read_text()
    line = "    subprocess.call(['/usr/bin/printenv'])"
    if text.count(line) != 1:
        raise RuntimeError("Standard wrapper changed: inspect before deriving a candidate")
    wrapper.write_text(text.replace(line, "    # Whole-environment logging omitted in the isolated candidate."))
    wrapper.chmod(0o700)
    prefix.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    prefix.mkdir(mode=0o700)
    subprocess.run(["cp", "-a", "--reflink=auto", str(standard_prefix / "pfx"), str(prefix / "pfx")], check=True)
    for name in ("version", "tracked_files", "config_info"):
        if (standard_prefix / name).is_file():
            shutil.copy2(standard_prefix / name, prefix / name)
    configdir = prefix / "fex-emu"
    configdir.mkdir(mode=0o700)
    rootfs = Path("/usr/share/guestos/fex-mesa")
    config = (candidate / "ConfigTemplate.json").read_text().replace("@FEX_COMPAT_TOOL@", str(candidate)).replace("@FEX_ROOTFS_PATH@", str(rootfs))
    (configdir / "Config.json").write_text(config)
    appconfig = configdir / "smoke.json"
    appconfig.write_text(json.dumps({"Config": {}, "ThunksDB": {"Vulkan": "1"}}))
    env = dict(os.environ, SteamAppId="438100", STEAM_COMPAT_DATA_PATH=str(prefix), FEX_PORTABLE="1",
               FEX_ROOTFS=str(rootfs), FEX_APP_CONFIG_LOCATION=str(configdir) + "/",
               FEX_APP_CONFIG=str(appconfig), FEX_SERVERSOCKETPATH="frame-pcvr-438100-" + audit["provenance"]["commit"][:8])
    (candidate / "integration.json").write_text(json.dumps({"state": "staging", "standard_hashes": original_hashes}))
    fex = str(candidate / "usr/bin/FEX")
    smoke = subprocess.run([fex, "/usr/bin/true"], env=env, capture_output=True, text=True, timeout=40)
    (candidate / "smoke-runtime.log").write_text(smoke.stdout + smoke.stderr)
    if smoke.returncode:
        raise RuntimeError(f"Candidate runtime smoke failed ({smoke.returncode}); see {candidate}")
    # Use runtime's x86 CRT/libc explicitly; no cross development packages needed.
    probe = candidate / "vulkan-smoke-x86_64"
    lib = rootfs / "usr/lib"
    compile_cmd = ["clang", "--target=x86_64-linux-gnu", "--sysroot=" + str(rootfs), "-fuse-ld=lld",
                   "-fPIE", "-pie", "-nostdlib", str(lib / "Scrt1.o"), str(lib / "crti.o"),
                   str(Path(__file__).with_name("vulkan_smoke.c")), "-L" + str(lib), "-lc",
                   str(lib / "crtn.o"), "-Wl,--dynamic-linker=/lib64/ld-linux-x86-64.so.2", "-o", str(probe)]
    build = subprocess.run(compile_cmd, capture_output=True, text=True)
    (candidate / "smoke-compile.log").write_text(build.stdout + build.stderr)
    if build.returncode:
        raise RuntimeError(f"Vulkan probe compilation failed; see {candidate}/smoke-compile.log")
    vulkan = subprocess.run([fex, str(probe)], env=env, capture_output=True, text=True, timeout=40)
    (candidate / "smoke-vulkan.log").write_text(vulkan.stdout + vulkan.stderr)
    unchanged = all(sha256(standard / name) == value for name, value in original_hashes.items())
    record = {"state": "smoke-passed" if vulkan.returncode == 0 else "vulkan-smoke-failed",
              "commit": audit["provenance"]["commit"], "candidate": str(candidate), "compat_data": str(prefix),
              "runtime_returncode": smoke.returncode, "vulkan_returncode": vulkan.returncode,
              "standard_unchanged": unchanged, "standard_hashes": original_hashes,
              "runtime_hashes": {name: sha256(candidate / "usr/bin" / name) for name in FEX_BINS}}
    (candidate / "integration.json").write_text(json.dumps(record, indent=2))
    print(json.dumps({k: v for k, v in record.items() if k not in ("standard_hashes", "runtime_hashes")}, indent=2))
    print(vulkan.stdout + vulkan.stderr)
    if vulkan.returncode or not unchanged:
        raise RuntimeError("Candidate not ready for game launch; inspect integration record")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage")
    args = parser.parse_args()
    try:
        main(args.stage)
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
