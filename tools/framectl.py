#!/usr/bin/env python3
"""Read-only Steam Frame inventory. Python 3.9+, standard library only.

collect prints JSON to stdout; it never executes game, FEX or runtime binaries.
check evaluates prerequisites, not EAC authentication or VR functionality.
"""
import argparse
import datetime as dt
import functools
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys

SCHEMA = 1
COLLECTOR_VERSION = "0.2.0"
FEX_COMMIT = "6effd2384085818345ec32e6abb4181c7135a9a7"
FEX_BINS = ("FEX", "FEXBash", "FEXGetConfig", "FEXOfflineCompiler",
            "FEXServer", "FEXServerManager", "FEXpidof")
PROCESS_ENV = ("SteamAppId", "SteamGameId", "STEAM_COMPAT_TOOL_PATHS",
               "STEAM_COMPAT_DATA_PATH", "STEAM_COMPAT_CLIENT_INSTALL_PATH",
               "XR_RUNTIME_JSON", "VR_OVERRIDE", "PROTON_EAC_RUNTIME",
               "FEX_APP_CONFIG", "FEX_APP_CONFIG_LOCATION", "FEX_ROOTFS", "FEX_THUNKHOSTLIBS", "FEX_THUNKGUESTLIBS",
               "STEAM_COMPAT_GRAPHICS_PROVIDER", "STEAM_COMPAT_FEX_CONFIG", "STEAM_FEX_TSOENABLED",
               "STEAM_FEX_MULTIBLOCK", "STEAM_FEX_LOG")


def sha256(path):
    path = Path(path)
    stat = path.stat()
    return _sha256_cached(path, stat.st_size, stat.st_mtime_ns, stat.st_ino)


@functools.lru_cache(maxsize=2048)
def _sha256_cached(path, size, mtime_ns, inode):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def binary_arch(path):
    with path.open("rb") as f:
        head = f.read(64)
        if head[:4] == b"\x7fELF" and len(head) >= 20:
            endian = {1: "<", 2: ">"}.get(head[5])
            if endian:
                machine = struct.unpack(endian + "H", head[18:20])[0]
                return {183: "ELF-aarch64", 62: "ELF-x86_64", 3: "ELF-x86"}.get(machine, f"ELF-{machine}")
        if head[:2] == b"MZ" and len(head) >= 64:
            f.seek(struct.unpack("<I", head[60:64])[0])
            pe = f.read(6)
            if pe[:4] == b"PE\0\0" and len(pe) == 6:
                machine = struct.unpack("<H", pe[4:])[0]
                return {0x8664: "PE-x86_64", 0x14c: "PE-x86", 0xaa64: "PE-aarch64"}.get(machine, f"PE-{machine}")
    return "other"


def file_info(path):
    p = Path(path)
    result = {"path": str(p), "resolved": str(p.resolve())}
    try:
        result.update(size=p.stat().st_size, sha256=sha256(p), arch=binary_arch(p))
    except OSError as e:
        result["error"] = type(e).__name__
    return result


def read_vdf(path):
    """Parse text KeyValues without loading Steam credentials into the report."""
    data = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    tokens = []
    lexer = re.compile(r'\s+|//[^\n]*|"((?:\\.|[^"\\])*)"|([{}])|([^\s"{}]+)')
    offset = 0
    while offset < len(data):
        m = lexer.match(data, offset)
        if not m:
            raise ValueError("invalid KeyValues syntax")
        offset = m.end()
        if m.group(1) is not None:
            tokens.append(re.sub(r'\\(["\\])', r'\1', m.group(1)))
        elif m.group(2) or m.group(3):
            tokens.append(m.group(2) or m.group(3))
    pos = 0

    def block(nested=False):
        nonlocal pos
        result = {}
        while pos < len(tokens):
            key = tokens[pos]
            pos += 1
            if key == "}":
                if not nested:
                    raise ValueError("unexpected closing brace")
                return result
            if key == "{" or pos >= len(tokens):
                raise ValueError("missing KeyValues value")
            value = tokens[pos]
            pos += 1
            if value == "}":
                raise ValueError("missing KeyValues value")
            result[key] = block(True) if value == "{" else value
        if nested:
            raise ValueError("unclosed KeyValues block")
        return result
    return block()


def at(data, *keys):
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = next((v for k, v in data.items() if k.lower() == key.lower()), None)
    return data


def json_read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def runtime_info(path):
    path = Path(path)
    info = file_info(path)
    data = json_read(path)
    library = at(data, "runtime", "library_path") or at(data, "ICD", "library_path")
    if library:
        declared = library
        library = Path(library)
        if library.is_absolute():
            info["library"] = file_info(library)
        elif "/" in declared:
            # OpenXR resolves a relative library against the real manifest, not
            # the active_runtime.json symlink. See Khronos manifest_file.cpp.
            info["library"] = file_info(path.resolve().parent / library)
        else:
            info["library_resolution"] = "dynamic-linker-soname; not resolved by inventory"
        info["library_path_declared"] = declared
    return info


def tool_info(root):
    files = [root / "usr/bin" / b for b in FEX_BINS]
    files += [root / b for b in ("FEXCompatTool", "VERSIONS.txt", "ConfigTemplate.json", "version", "proton")]
    thunks = []
    for base in (root / "usr/lib/aarch64-linux-gnu/fex-emu/HostThunks",
                 root / "usr/share/fex-emu/GuestThunks"):
        if base.is_dir():
            thunks.extend(p for p in base.rglob("*") if p.is_file())
    version = {}
    for name in ("VERSIONS.txt", "version"):
        try:
            version[name] = (root / name).read_text()[:4096]
        except (OSError, UnicodeError):
            pass
    return {"path": str(root), "version": version, "files": [file_info(p) for p in files if p.is_file()],
            "thunks": [file_info(p) for p in sorted(thunks)[:512]],
            "thunks_truncated": len(thunks) > 512,
            "config_template": json_read(root / "ConfigTemplate.json")}


def launch_summary(raw):
    # Do not emit free-form launch options: arbitrary options may contain secrets.
    return {"sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "empty": not bool(raw.strip()),
            "has_command_placeholder": "%command%" in raw,
            "desktop_mode": "--no-vr" in raw.split(),
            "environment_keys": sorted(set(re.findall(r'\b([A-Z][A-Z0-9_]+)=', raw)))}


def steam_inventory(home):
    roots = []
    for p in (home / ".local/share/Steam", home / ".steam/steam", home / ".steam/root"):
        if p.is_dir() and p.resolve() not in roots:
            roots.append(p.resolve())
    libraries = list(roots)
    errors = []
    for root in roots:
        try:
            folders = at(read_vdf(root / "steamapps/libraryfolders.vdf"), "libraryfolders") or {}
            for key, value in folders.items():
                path = at(value, "path") if isinstance(value, dict) else value if key.isdigit() else None
                if path and Path(path).resolve() not in libraries:
                    libraries.append(Path(path).resolve())
        except (OSError, ValueError) as e:
            errors.append({"file": str(root / "steamapps/libraryfolders.vdf"), "error": type(e).__name__})
    apps, library_status, unregistered = [], [], []
    for library in libraries:
        manifests = sorted((library / "steamapps").glob("appmanifest_*.acf"))
        library_status.append({"path": str(library), "steamapps_exists": (library / "steamapps").is_dir(),
                               "manifest_count": len(manifests)})
        for candidate in ("FEX-Emu", "VRChat", "Proton EasyAntiCheat Runtime", "Proton - Experimental"):
            directory = library / "steamapps/common" / candidate
            if directory.is_dir():
                unregistered.append({"name": candidate, "tool": tool_info(directory)})
        for manifest in manifests:
            try:
                state = at(read_vdf(manifest), "AppState") or {}
                name = at(state, "name") or ""
                appid = at(state, "appid")
                if appid not in ("438100", "3127680", "1826330", "250820") and "proton" not in name.lower():
                    continue
                install = at(state, "installdir")
                # Manifests are data, not permission to traverse arbitrary paths.
                if not install or Path(install).name != install or install in (".", ".."):
                    raise ValueError("invalid install directory")
                directory = library / "steamapps/common" / install
                apps.append({"appid": appid, "name": name, "buildid": at(state, "buildid"),
                             "state_flags": at(state, "StateFlags"), "manifest": file_info(manifest),
                             "directory": str(directory), "tool": tool_info(directory),
                             "game_files": [file_info(directory / f) for f in
                                            ("VRChat.exe", "launch.exe", "start_protected_game.exe", "VRChat.apk",
                                             "VRChat_Data/Plugins/x86_64/openvr_api.dll",
                                             "VRChat_Data/Plugins/x86_64/openxr_loader.dll",
                                             "VRChat_Data/Plugins/x86_64/UnityOpenXR.dll")
                                            if (directory / f).is_file()] if appid == "438100" else []})
            except (OSError, ValueError) as e:
                errors.append({"file": str(manifest), "error": type(e).__name__})
    mappings, launch = [], []
    for root in roots:
        try:
            config = read_vdf(root / "config/config.vdf")
            mapping = at(config, "InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping") or {}
            mappings.append({"root": str(root), "mapping": {
                key: {k: v for k, v in value.items() if k.lower() in ("name", "config", "priority")}
                for key, value in mapping.items() if key in ("0", "438100") and isinstance(value, dict)}})
        except (OSError, ValueError) as e:
            errors.append({"file": str(root / "config/config.vdf"), "error": type(e).__name__})
        for path in sorted((root / "userdata").glob("*/config/localconfig.vdf")):
            try:
                raw = at(read_vdf(path), "UserLocalConfigStore", "Software", "Valve", "Steam", "apps", "438100", "LaunchOptions")
                if isinstance(raw, str):
                    launch.append(launch_summary(raw))
            except (OSError, ValueError):
                pass
    return {"roots": [str(p) for p in roots], "libraries": [str(p) for p in libraries],
            "library_status": library_status, "common_candidates": unregistered,
            "apps": apps, "compat_mapping": mappings, "launch_options": launch, "errors": errors}


def process_inventory(proc=Path("/proc")):
    result = []
    names = re.compile(r'fex|wine|vrchat|vrserver|vrcompositor|xrservice|wivrn|steamlink|proton|pressure-vessel', re.I)
    for entry in proc.glob("[0-9]*"):
        try:
            comm = (entry / "comm").read_text().strip()
            if not names.search(comm):
                continue
            item = {"pid": int(entry.name), "name": comm}
            try:
                item["exe"] = str((entry / "exe").resolve(strict=True))
            except OSError:
                pass
            try:
                env = (entry / "environ").read_bytes().split(b"\0")
                item["environment"] = {k.decode(): v.decode(errors="replace") for pair in env
                                       for k, sep, v in [pair.partition(b"=")]
                                       if sep and k.decode(errors="replace") in PROCESS_ENV}
                configs = {}
                for key in ("FEX_APP_CONFIG", "FEX_APP_CONFIG_LOCATION"):
                    value = item["environment"].get(key)
                    if value:
                        p = Path(value)
                        if p.is_dir():
                            p = p / "Config.json"
                        data = json_read(p)
                        config = at(data, "Config") or {}
                        configs[key] = {"path": str(p), "Config": {k: v for k, v in config.items()
                            if k in ("RootFS", "ThunkHostLibs", "ThunkGuestLibs", "TSOEnabled", "Multiblock", "DiskCache")},
                            "ThunksDB": at(data, "ThunksDB")}
                item["fex_configs"] = configs
                maps = (entry / "maps").read_text().splitlines()
                paths = sorted({line.split(None, 5)[5] for line in maps if len(line.split(None, 5)) == 6
                                and re.search(r'vulkan|dxvk|openvr|openxr|vrclient|fex|wine', line, re.I)
                                and line.split(None, 5)[5].startswith("/")})
                item["relevant_libraries"] = [file_info(Path(p)) for p in paths[:80]]
            except OSError as e:
                item["detail_error"] = type(e).__name__
            result.append(item)
        except OSError:
            continue
    return result


def collect(home=None):
    home = Path(home) if home else Path.home()
    system = {"os": platform.system(), "machine": platform.machine(), "kernel": platform.release()}
    try:
        system["os_release"] = {k: v.strip('"') for line in Path("/etc/os-release").read_text().splitlines()
                                for k, sep, v in [line.partition("=")] if sep and k in
                                ("NAME", "ID", "VERSION_ID", "BUILD_ID", "VARIANT_ID")}
        system["memory"] = {k: v.strip() for line in Path("/proc/meminfo").read_text().splitlines()
                            for k, sep, v in [line.partition(":")] if sep and k in ("MemTotal", "MemAvailable", "SwapTotal")}
        system["cpu"] = sorted({line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                                if re.match(r'^(CPU implementer|CPU architecture|CPU part|model name)\s*:', line)})
    except OSError:
        pass
    stat = os.statvfs(home)
    system["home_free_bytes"] = stat.f_bavail * stat.f_frsize
    steam = steam_inventory(home)
    configs = [Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config"))), Path("/etc")]
    configs += [Path(p) for p in os.environ.get("XDG_CONFIG_DIRS", "/etc/xdg").split(":") if p]
    runtimes = []
    for root in configs:
        for arch in ("", ".x86_64", ".aarch64", ".i686"):
            p = root / f"openxr/1/active_runtime{arch}.json"
            if p.exists() or p.is_symlink():
                runtimes.append(runtime_info(p))
    for name in ("XR_RUNTIME_JSON",):
        if os.environ.get(name):
            runtimes.append(runtime_info(Path(os.environ[name])))
    icds = [runtime_info(p) for root in (Path("/etc/vulkan/icd.d"), Path("/usr/share/vulkan/icd.d"),
                                       home / ".local/share/vulkan/icd.d") for p in sorted(root.glob("*.json"))]
    openvr = []
    for p in (home / ".config/openvr/openvrpaths.vrpath",):
        data = json_read(p)
        if data:
            openvr.append({"path": str(p), "runtime": at(data, "runtime")})
    versions = []
    for p in (Path("/opt/steamvr/bin/version.txt"), Path("/opt/steamvr/steamxr_linuxarm64.json")):
        if p.is_file():
            info = file_info(p)
            if p.name == "version.txt":
                info["version_text"] = p.read_text(errors="replace")[:128]
            versions.append(info)
    steam["client_packages"] = [file_info(p) for root in steam["roots"]
                                for p in sorted((Path(root) / "package").glob("*.installed"))[:30]]
    return {"schema": SCHEMA, "collector_version": COLLECTOR_VERSION,
            "collected_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "system": system, "steam": steam,
            "vr": {"openxr": runtimes, "openvr": openvr, "native_files": versions},
            "vulkan_icds": icds, "processes": process_inventory(),
            "toolchain": {name: command_version(name) for name in
                          ("clang", "cmake", "ninja", "ld.lld", "git", "python3")},
            "limits": ["M0 inventory only; EAC, login, world entry and stereo rendering remain unverified.",
                       "No full command lines, credentials, game logs or Steam account identifiers collected.",
                       "A runtime manifest or thunk file does not prove it is used by the game."]}


def command_version(name):
    try:
        r = subprocess.run([name, "--version"], capture_output=True, text=True, timeout=10)
        return {"returncode": r.returncode, "version": (r.stdout or r.stderr).splitlines()[0]}
    except (OSError, subprocess.TimeoutExpired, IndexError) as e:
        return {"error": type(e).__name__}


def check(data):
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("unsupported inventory schema")
    blockers, pending = [], []
    if data["system"].get("os") != "Linux" or data["system"].get("machine") not in ("aarch64", "arm64"):
        blockers.append("FrameのARM64 Linux環境で採取してください。Macの採取結果は実機判定に使えません。")
    apps = data["steam"]["apps"]
    for appid, title in (("438100", "VRChat"), ("3127680", "FEX-Emu"), ("1826330", "Proton EasyAntiCheat Runtime")):
        found = [a for a in apps if a["appid"] == appid]
        if not found:
            blockers.append(f"{title} のインストールを確認できません。")
        for a in found:
            if a.get("state_flags") != "4":
                pending.append(f"{title}: Steamのインストール状態を確認 (StateFlags={a.get('state_flags')})。")
    games = [a for a in apps if a["appid"] == "438100"]
    if games and not any(f.get("arch") == "PE-x86_64" and Path(f["path"]).name == "VRChat.exe"
                         for a in games for f in a["game_files"]):
        blockers.append("Windows x86-64版 VRChat.exe がありません。Steamで互換ツールを指定して配布内容を確認してください。")
    if not any("proton" in a["name"].lower() for a in apps):
        pending.append("Protonのインストール先を確認できません。外部互換ツールの場合は個別調査が必要です。")
    fex = [a for a in apps if a["appid"] == "3127680"]
    if fex and not any(a["tool"]["thunks"] for a in fex):
        pending.append("FEXの既定位置にthunkがありません。標準の配置・起動時の参照先を調べてください。")
    if data["steam"].get("common_candidates"):
        pending.append("common配下にも候補ディレクトリを検出。マニフェストと照合し、記録不足だけでアンインストール済みと断定しないでください。")
    if not data["vr"]["openxr"] and not data["vr"]["openvr"]:
        pending.append("標準位置のVRランタイム設定がありません。実際の起動環境を調べてください。")
    if not data["vulkan_icds"]:
        pending.append("標準位置のVulkan ICDがありません。コンテナー内の配置を調べてください。")
    streaming = [p["name"] for p in data["processes"] if re.search(r'wivrn|steamlink', p["name"], re.I)]
    if streaming:
        pending.append("配信プロセスを検出: " + ", ".join(streaming) + "。ローカル実行試験時の有無を記録してください。")
    pending += ["Steam UIでVRChatに選択されたProtonと起動オプションを確認する。",
                "標準構成の正規Steam起動でEAC・ログイン・入室・10分滞在を確認する (M1)。",
                "x86 VR接続、両眼・頭・両手・音声、30分継続は未検証 (M2〜M4)。"]
    return {"status": "prerequisites_missing" if blockers else "inventory_ready_for_review",
            "blockers": blockers, "pending": pending, "milestones_passed": []}


def audit_stage(root):
    root = Path(root)
    required = [root / "usr/bin" / b for b in FEX_BINS]
    required += [root / b for b in ("FEXCompatTool", "VERSIONS.txt", "ConfigTemplate.json")]
    files = [file_info(p) for p in required]
    errors = [f["path"] + ": missing/empty" for f in files if "error" in f or not f.get("size")]
    for f in files:
        if Path(f["path"]).name in FEX_BINS + ("FEXCompatTool",) and f.get("arch") != "ELF-aarch64":
            errors.append(f["path"] + ": expected ELF-aarch64")
    meta = json_read(root / "frame-build.json")
    if not isinstance(meta, dict):
        meta = None
    if not meta or meta.get("commit") != FEX_COMMIT:
        errors.append("frame-build.json: pinned source provenance missing/mismatched")
    for f in files:
        if meta and meta.get("artifacts", {}).get(str(Path(f["path"]).relative_to(root))) != f.get("sha256"):
            errors.append(f["path"] + ": artifact hash does not match build record")
    return {"files": files, "provenance": meta, "errors": errors,
            "installable": False, "reason": "Frame標準thunk・ABI・共有FEXの選択境界は実機で確認が必要。自動インストールは実装していません。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect", help="Frame上で実行。採取JSONをstdoutに出力")
    p = sub.add_parser("check", help="採取JSONの前提条件と未検証事項を表示")
    p.add_argument("report", type=Path)
    p = sub.add_parser("audit-stage", help="ステージ成果物を実行せず検査")
    p.add_argument("stage", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "collect":
            result = collect()
        elif args.command == "check":
            result = check(json.loads(args.report.read_text()))
        else:
            result = audit_stage(args.stage)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 2 if result.get("blockers") or result.get("errors") else 0
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"framectl: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
