#!/usr/bin/env python3
"""VRChat-only launch boundary observation or isolated FEX candidate selection."""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import sys
from framectl import FEX_COMMIT, sha256


def main():
    separator = sys.argv.index("--")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eac-env", action="store_true")
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--capture-launch", action="store_true", help="Capture this launch without verbose Proton tracing")
    options = parser.parse_args(sys.argv[1:separator])
    args = sys.argv[separator + 1:]
    if not args:
        raise RuntimeError("Missing Steam command")
    if os.environ.get("SteamAppId") != "438100":
        raise RuntimeError("This helper is restricted to VRChat (438100)")
    selection = None
    if options.candidate:
        candidate = options.candidate.resolve()
        base = Path.home() / ".local/share/frame-pcvr"
        candidate.relative_to((base / "candidates").resolve())
        selection = json.loads((candidate / "integration.json").read_text())
        if selection.get("state") != "smoke-passed" or selection.get("commit") != FEX_COMMIT or not selection.get("standard_unchanged"):
            raise RuntimeError("Candidate did not pass its integration smoke tests")
        standard = Path.home() / ".local/share/Steam/steamapps/common/FEX-Emu"
        for name, expected in selection["standard_hashes"].items():
            if sha256(standard / name) != expected:
                raise RuntimeError("Steam FEX changed since staging; re-evaluate instead of auto-reapplying")
        for name, expected in selection["runtime_hashes"].items():
            if sha256(candidate / "usr/bin" / name) != expected:
                raise RuntimeError("Candidate binary changed since smoke tests")
        for name, expected in selection.get("extra_hashes", {}).items():
            path = (candidate / name).resolve()
            path.relative_to(candidate)
            if sha256(path) != expected:
                raise RuntimeError("Candidate integration file changed since staging")
        if selection.get("private_proton"):
            from private_proton import select_private_proton
            args = select_private_proton(args, os.environ, base, selection["private_proton"])
        matches = [i for i, arg in enumerate(args) if arg == str(standard / "fex-compat-tool")]
        if len(matches) != 1:
            raise RuntimeError("Expected exactly one standard FEX boundary; no nested emulation allowed")
        prefix = Path(selection["compat_data"]).resolve()
        prefix.relative_to((base / "c").resolve())
        args[matches[0]] = str(candidate / "fex-compat-tool")
        os.environ["STEAM_COMPAT_DATA_PATH"] = str(prefix)
        os.environ["FEX_APP_CONFIG_LOCATION"] = str(prefix / "fex-emu") + "/"
        os.environ["FEX_SERVERSOCKETPATH"] = "frame-pcvr-438100-" + selection["commit"][:8]
        if selection.get("vulkan_trial"):
            if selection["vulkan_trial"] not in ("native", "rpo", "foveated"):
                raise RuntimeError("Unknown Vulkan trial profile")
            config = prefix / "fex-emu/vulkan-app.json"
            if not config.is_file():
                raise RuntimeError("Vulkan trial configuration is missing")
            if json.loads(config.read_text()) != {"Config": {}, "ThunksDB": {"Vulkan": 1}}:
                raise RuntimeError("Vulkan trial must preserve CPU configuration defaults")
            # Emulator metadata selects this after entering the container;
            # host setup helpers keep their separate stock-route configuration.
        paths = os.environ.get("STEAM_COMPAT_TOOL_PATHS", "").split(":")
        os.environ["STEAM_COMPAT_TOOL_PATHS"] = ":".join([str(candidate)] + [p for p in paths if p])
    if options.eac_env:
        data = os.environ.get("STEAM_COMPAT_DATA_PATH")
        if not data:
            raise RuntimeError("Missing VRChat compatibility data path")
        eac = Path(data) / "pfx/drive_c/users/steamuser/AppData/Roaming/EasyAntiCheat"
        if not eac.is_dir():
            raise RuntimeError("EAC launcher directory has not been installed")
        os.environ["EAC_LAUNCHERDIR"] = str(eac)
    directory = Path.home() / ".local/share/frame-pcvr/runs/launch-boundary"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Record only executable path boundaries and argument shape. Game arguments
    # may contain private instance data and must not be copied to this report.
    paths = [{"index": i, "path": a} for i, a in enumerate(args)
             if Path(a).name in ("fex-compat-tool", "FEX", "_v2-entry-point", "proton", "launch.exe")]
    report = {"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "appid": "438100", "host": platform.machine(), "argument_count": len(args),
              "boundaries": paths, "compat_data_path": os.environ.get("STEAM_COMPAT_DATA_PATH"),
              "emulator_manifest": os.environ.get("STEAM_COMPAT_EMULATOR"),
              "eac_launcher_dir": os.environ.get("EAC_LAUNCHERDIR"),
              "candidate": str(options.candidate) if options.candidate else None,
              "mode": "candidate" if selection else "standard-eac-environment" if options.eac_env else "observe-only"}
    destination = directory / (datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}.json")
    with destination.open("x") as f:
        os.chmod(destination, 0o600)
        json.dump(report, f, indent=2)
    if options.diagnose or options.capture_launch:
        logdir = Path.home() / ".local/share/frame-pcvr/runs/candidate-diagnostics"
        logdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if options.diagnose:
            os.environ["PROTON_LOG"] = "1"
            os.environ["PROTON_LOG_DIR"] = str(logdir)
        # Only descendants of this VRChat launch inherit these file descriptors.
        fd = os.open(logdir / (destination.stem + ".log"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.close(fd)
    os.execvp(args[0], args)


if __name__ == "__main__":
    main()
