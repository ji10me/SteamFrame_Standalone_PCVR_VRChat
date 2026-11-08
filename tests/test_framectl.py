import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("framectl", ROOT / "tools/framectl.py")
framectl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(framectl)


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())


def elf(machine=183):
    data = bytearray(64)
    data[:6] = b"\x7fELF\x02\x01"
    data[18:20] = struct.pack("<H", machine)
    return bytes(data)


def pe():
    data = bytearray(70)
    data[:2] = b"MZ"
    data[60:64] = struct.pack("<I", 64)
    data[64:] = b"PE\0\0\x64\x86"
    return bytes(data)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.steam = self.home / ".local/share/Steam"
        self.external = self.base / "External Library"
        write(self.steam / "steamapps/libraryfolders.vdf",
              '"libraryfolders" { "0" { "path" "' + str(self.steam) + '" } '
              '"1" { "path" "' + str(self.external) + '" } }')
        for appid, name, install, library in (
            ("438100", "VRChat", "VRChat", self.external),
            ("3127680", "FEX-Emu", "FEX-Emu", self.steam),
            ("1826330", "Proton EasyAntiCheat Runtime", "Proton EasyAntiCheat Runtime", self.steam),
            ("1493710", "Proton Experimental", "Proton - Experimental", self.steam),
        ):
            write(library / f"steamapps/appmanifest_{appid}.acf",
                  f'"AppState" {{ "appid" "{appid}" "name" "{name}" '
                  f'"installdir" "{install}" "buildid" "123" "StateFlags" "4" }}')
        write(self.external / "steamapps/common/VRChat/VRChat.exe", pe())
        write(self.steam / "config/config.vdf",
              '"InstallConfigStore" { "Software" { "Valve" { "Steam" {'
              '"password" "CREDENTIAL_SENTINEL" '
              '"CompatToolMapping" { "438100" { "name" "proton_experimental" "config" "" } } } } } }')
        write(self.steam / "userdata/123456/config/localconfig.vdf",
              '"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" { "438100" {'
              '"LaunchOptions" "TOKEN=CREDENTIAL_SENTINEL %command% --no-vr" } } } } } }')

    def report(self):
        return {"schema": 1, "system": {"os": "Linux", "machine": "aarch64"},
                "steam": framectl.steam_inventory(self.home),
                "vr": {"openxr": [], "openvr": []}, "vulkan_icds": [], "processes": []}

    def test_secondary_library_windows_and_private_fields(self):
        report = self.report()
        checked = framectl.check(report)
        self.assertEqual(checked["blockers"], [])
        self.assertEqual(checked["milestones_passed"], [])
        self.assertNotIn("CREDENTIAL_SENTINEL", json.dumps(report))
        self.assertNotIn("123456", json.dumps(report))
        game = next(a for a in report["steam"]["apps"] if a["appid"] == "438100")
        self.assertEqual(game["game_files"][0]["arch"], "PE-x86_64")
        self.assertTrue(report["steam"]["launch_options"][0]["desktop_mode"])

    def test_android_and_mac_are_not_success(self):
        (self.external / "steamapps/common/VRChat/VRChat.exe").unlink()
        write(self.external / "steamapps/common/VRChat/VRChat.apk", b"PK\0\0")
        report = self.report()
        report["system"] = {"os": "Darwin", "machine": "arm64"}
        self.assertEqual(len(framectl.check(report)["blockers"]), 2)

    def test_missing_runtime_and_partial_download_remain_pending(self):
        (self.steam / "steamapps/appmanifest_1826330.acf").unlink()
        report = self.report()
        next(a for a in report["steam"]["apps"] if a["appid"] == "438100")["state_flags"] = "1026"
        checked = framectl.check(report)
        self.assertTrue(any("EasyAntiCheat" in s for s in checked["blockers"]))
        self.assertTrue(any("StateFlags=1026" in s for s in checked["pending"]))

    def test_manifest_cannot_traverse(self):
        write(self.external / "steamapps/appmanifest_438100.acf",
              '"AppState" { "appid" "438100" "name" "VRChat" "installdir" "../../elsewhere" }')
        report = self.report()
        self.assertTrue(report["steam"]["errors"])
        self.assertFalse(any(a["appid"] == "438100" for a in report["steam"]["apps"]))

    def test_runtime_manifest_is_not_cross_isa_success(self):
        write(self.base / "native/libxr.so", elf())
        manifest = self.base / "native/runtime.json"
        write(manifest, json.dumps({"runtime": {"library_path": "./libxr.so"}}))
        info = framectl.runtime_info(manifest)
        self.assertEqual(info["library"]["arch"], "ELF-aarch64")
        report = self.report()
        report["vr"]["openxr"] = [info]
        self.assertEqual(framectl.check(report)["milestones_passed"], [])

    def test_symlinked_runtime_resolves_real_manifest_directory(self):
        write(self.base / "opt/steamvr/bin/linuxarm64/vrclient.so", elf())
        manifest = self.base / "opt/steamvr/runtime.json"
        write(manifest, json.dumps({"runtime": {"library_path": "bin/linuxarm64/vrclient.so"}}))
        active = self.home / ".config/openxr/1/active_runtime.json"
        active.parent.mkdir(parents=True)
        active.symlink_to(manifest)
        info = framectl.runtime_info(active)
        self.assertEqual(info["library"]["arch"], "ELF-aarch64")
        self.assertEqual(info["library"]["path"], str(manifest.resolve().parent / "bin/linuxarm64/vrclient.so"))

    def test_soname_does_not_report_missing_relative_library(self):
        manifest = self.base / "runtime.json"
        write(manifest, json.dumps({"runtime": {"library_path": "libxr.so"}}))
        info = framectl.runtime_info(manifest)
        self.assertNotIn("library", info)
        self.assertIn("dynamic-linker", info["library_resolution"])

    def test_processes_do_not_copy_cmdline_or_unknown_env(self):
        p = self.base / "proc/42"
        write(p / "comm", "FEXServer\n")
        write(p / "environ", b"SECRET=CREDENTIAL_SENTINEL\0SteamAppId=438100\0")
        write(p / "cmdline", b"--token=CREDENTIAL_SENTINEL\0")
        write(p / "maps", "")
        result = framectl.process_inventory(self.base / "proc")
        self.assertEqual(result[0]["environment"], {"SteamAppId": "438100"})
        self.assertNotIn("CREDENTIAL_SENTINEL", json.dumps(result))

    def test_vdf_comments_escaping_and_truncation(self):
        p = self.base / "test.vdf"
        write(p, '// comment\n"a" { "b" "a\\\"b" "p" "C:\\games" }')
        self.assertEqual(framectl.read_vdf(p)["a"], {"b": 'a"b', "p": "C:\\games"})
        write(p, '"a" { "b" "x"')
        with self.assertRaises(ValueError):
            framectl.read_vdf(p)

    def test_incomplete_stage_and_wrong_isa_rejected(self):
        stage = self.base / "stage"
        self.assertTrue(framectl.audit_stage(stage)["errors"])
        required = [stage / "usr/bin" / b for b in framectl.FEX_BINS]
        required += [stage / "FEXCompatTool", stage / "VERSIONS.txt", stage / "ConfigTemplate.json"]
        for path in required:
            write(path, elf(62) if path.name in framectl.FEX_BINS else b"metadata")
        write(stage / "frame-build.json", json.dumps({"commit": framectl.FEX_COMMIT, "artifacts": {
            str(p.relative_to(stage)): framectl.sha256(p) for p in required}}))
        result = framectl.audit_stage(stage)
        self.assertTrue(any("expected ELF-aarch64" in e for e in result["errors"]))
        self.assertFalse(result["installable"])

    def test_stage_hash_change_detected_after_initial_audit(self):
        stage = self.base / "stage"
        required = [stage / "usr/bin" / b for b in framectl.FEX_BINS]
        required += [stage / "FEXCompatTool", stage / "VERSIONS.txt", stage / "ConfigTemplate.json"]
        for path in required:
            write(path, elf() if path.name in framectl.FEX_BINS + ("FEXCompatTool",) else b"metadata")
        write(stage / "frame-build.json", json.dumps({"commit": framectl.FEX_COMMIT, "artifacts": {
            str(p.relative_to(stage)): framectl.sha256(p) for p in required}}))
        self.assertEqual(framectl.audit_stage(stage)["errors"], [])
        write(required[0], elf() + b"changed")
        self.assertTrue(any("hash does not match" in e for e in framectl.audit_stage(stage)["errors"]))

    def test_collector_wrapper_keeps_failed_ssh_out_of_inventory(self):
        project = self.base / "project"
        (project / "scripts").mkdir(parents=True)
        (project / "tools").mkdir()
        shutil.copy(ROOT / "scripts/collect-frame.sh", project / "scripts")
        shutil.copy(ROOT / "tools/framectl.py", project / "tools")
        ssh = self.base / "bin/ssh"
        write(ssh, '#!/bin/sh\necho "permission denied" >&2\nexit 255\n')
        ssh.chmod(0o755)
        env = dict(os.environ, PATH=str(ssh.parent) + os.pathsep + os.environ["PATH"])
        r = subprocess.run(["sh", str(project / "scripts/collect-frame.sh"), "steamos@frame.invalid"], env=env, capture_output=True)
        self.assertEqual(r.returncode, 255)
        self.assertFalse(list(project.glob("local/runs/*/inventory.json")))
        # Successful transport persists valid JSON and prints prerequisite review.
        report = self.report()
        write(ssh, '#!/bin/sh\ncat >/dev/null\ncat <<\'REPORT\'\n' + json.dumps(report) + '\nREPORT\n')
        r = subprocess.run(["sh", str(project / "scripts/collect-frame.sh"), "steamos@frame.invalid"], env=env, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        saved = list(project.glob("local/runs/*/inventory.json"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(json.loads(saved[0].read_text()), report)
        self.assertEqual(saved[0].stat().st_mode & 0o777, 0o600)

    def test_collector_requires_explicit_target_without_starting_ssh(self):
        project = self.base / "no-default-project"
        (project / "scripts").mkdir(parents=True)
        shutil.copy(ROOT / "scripts/collect-frame.sh", project / "scripts")
        marker = self.base / "ssh-started"
        ssh = self.base / "no-default-bin/ssh"
        write(ssh, '#!/bin/sh\ntouch "' + str(marker) + '"\nexit 99\n')
        ssh.chmod(0o755)
        env = dict(os.environ, PATH=str(ssh.parent) + os.pathsep + os.environ["PATH"])
        r = subprocess.run(["sh", str(project / "scripts/collect-frame.sh")], env=env, capture_output=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"FRAME_ADDRESS", r.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse((project / "local").exists())

    def test_build_refuses_non_linux_host_before_writes(self):
        if os.uname().sysname != "Linux":
            r = subprocess.run(["sh", str(ROOT / "scripts/build-fex-stage.sh")], capture_output=True)
            self.assertEqual(r.returncode, 2)
            self.assertIn(b"ARM64 Linux", r.stderr)


if __name__ == "__main__":
    unittest.main()
