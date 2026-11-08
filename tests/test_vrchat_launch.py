import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("vrchat_launch", TOOLS / "vrchat-launch.py")
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.addCleanup(patch.stopall)
        patch.object(Path, "home", return_value=self.home).start()
        patch.dict(os.environ, {"SteamAppId": "438100"}).start()
        self.execute = patch.object(launch.os, "execvp").start()

    def run_helper(self, flags, command):
        with patch.object(sys, "argv", ["vrchat-launch.py"] + flags + ["--"] + command):
            launch.main()

    def make_candidate(self):
        base = self.home / ".local/share/frame-pcvr"
        candidate = base / "candidates/test"
        prefix = base / "c/438100"
        candidate.mkdir(parents=True)
        prefix.mkdir(parents=True)
        standard = self.home / ".local/share/Steam/steamapps/common/FEX-Emu"
        standard.mkdir(parents=True)
        (standard / "fex-compat-tool").write_text("standard")
        (candidate / "usr/bin").mkdir(parents=True)
        (candidate / "usr/bin/FEX").write_text("candidate")
        record = {"state": "smoke-passed", "commit": launch.FEX_COMMIT,
                  "standard_unchanged": True, "compat_data": str(prefix),
                  "standard_hashes": {"fex-compat-tool": launch.sha256(standard / "fex-compat-tool")},
                  "runtime_hashes": {"FEX": launch.sha256(candidate / "usr/bin/FEX")}}
        (candidate / "integration.json").write_text(json.dumps(record))
        return candidate, prefix, standard

    def test_observation_preserves_arguments_without_recording_secrets(self):
        command = ["/example/steam-launch-wrapper", "--", "/example/launch.exe", "TOKEN=PRIVATE_SENTINEL", "literal $(value)"]
        self.run_helper([], command)
        self.execute.assert_called_once_with(command[0], command)
        reports = list(self.home.glob(".local/share/frame-pcvr/runs/launch-boundary/*.json"))
        self.assertEqual(len(reports), 1)
        self.assertNotIn("PRIVATE_SENTINEL", reports[0].read_text())

    def test_other_app_refused_before_writes(self):
        os.environ["SteamAppId"] = "250820"
        with self.assertRaises(RuntimeError):
            self.run_helper([], ["/example/program"])
        self.execute.assert_not_called()
        self.assertFalse((self.home / ".local").exists())

    def test_candidate_rewrites_one_fex_boundary_and_isolates_prefix(self):
        candidate, prefix, standard = self.make_candidate()
        command = ["/example/wrapper", str(standard / "fex-compat-tool"), "waitforexitandrun", "--", "/example/proton", "launch.exe"]
        self.run_helper(["--candidate", str(candidate)], command)
        expected = list(command)
        expected[1] = str(candidate / "fex-compat-tool")
        self.execute.assert_called_once_with(expected[0], expected)
        self.assertEqual(os.environ["STEAM_COMPAT_DATA_PATH"], str(prefix))
        self.assertEqual((standard / "fex-compat-tool").read_text(), "standard")

    def test_private_graphics_trial_keeps_steam_arguments_and_both_shared_tools(self):
        from private_proton import CHECKED_FILES, DLLS
        candidate, prefix, standard = self.make_candidate()
        base = self.home / '.local/share/frame-pcvr'
        parent = self.home / '.local/share/Steam/steamapps/common/Proton - Experimental'
        private = base / 'proton/dfr-test'
        for root in (parent, private):
            for name in CHECKED_FILES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('stock ' + name)
        for name in DLLS:
            (private / name).write_text('trial ' + name)
        record = json.loads((candidate / 'integration.json').read_text())
        record['private_proton'] = {
            'format': 'frame-dfr-private-proton-v1', 'path': str(private), 'parent': str(parent),
            'parent_hashes': {n: launch.sha256(parent / n) for n in CHECKED_FILES},
            'private_hashes': {n: launch.sha256(private / n) for n in CHECKED_FILES}}
        (candidate / 'integration.json').write_text(json.dumps(record))
        command = ['/steam-wrapper', str(standard / 'fex-compat-tool'), '--',
                   str(parent / 'proton'), 'waitforexitandrun', '/game/launch.exe', 'PRIVATE_SENTINEL']
        self.run_helper(['--candidate', str(candidate)], command)
        expected = list(command)
        expected[1] = str(candidate / 'fex-compat-tool')
        expected[3] = str(private / 'proton')
        self.execute.assert_called_once_with(expected[0], expected)
        self.assertEqual(os.environ['STEAM_COMPAT_DATA_PATH'], str(prefix))
        paths = os.environ['STEAM_COMPAT_TOOL_PATHS'].split(':')
        self.assertIn(str(candidate), paths)
        self.assertIn(str(private), paths)
        self.assertEqual((parent / DLLS[0]).read_text(), 'stock ' + DLLS[0])
        reports = list(base.glob('runs/launch-boundary/*.json'))
        self.assertNotIn('PRIVATE_SENTINEL', reports[0].read_text())

    def test_changed_shared_tool_prevents_candidate_launch(self):
        candidate, _, standard = self.make_candidate()
        (standard / "fex-compat-tool").write_text("new Steam update")
        with self.assertRaisesRegex(RuntimeError, "Steam FEX changed"):
            self.run_helper(["--candidate", str(candidate)], [str(standard / "fex-compat-tool")])
        self.execute.assert_not_called()

    def test_missing_or_duplicate_fex_boundary_refused(self):
        candidate, _, standard = self.make_candidate()
        for command in (["/example/proton"], [str(standard / "fex-compat-tool")] * 2):
            with self.assertRaisesRegex(RuntimeError, "exactly one"):
                self.run_helper(["--candidate", str(candidate)], command)
        self.execute.assert_not_called()

    def test_vulkan_trial_uses_private_config_without_cpu_overrides(self):
        candidate, prefix, standard = self.make_candidate()
        config = prefix / "fex-emu/vulkan-app.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"Config": {}, "ThunksDB": {"Vulkan": 1}}))
        record = json.loads((candidate / "integration.json").read_text())
        record["vulkan_trial"] = "native"
        (candidate / "integration.json").write_text(json.dumps(record))
        self.run_helper(["--candidate", str(candidate)], [str(standard / "fex-compat-tool")])
        self.assertNotEqual(os.environ.get("FEX_APP_CONFIG"), str(config))
        self.assertEqual(json.loads(config.read_text())["Config"], {})

    def test_changed_trial_integration_prevents_launch(self):
        candidate, _, standard = self.make_candidate()
        metadata = candidate / "emulator.json"
        metadata.write_text("original integration")
        record = json.loads((candidate / "integration.json").read_text())
        record["extra_hashes"] = {"emulator.json": launch.sha256(metadata)}
        (candidate / "integration.json").write_text(json.dumps(record))
        metadata.write_text("modified integration")
        with self.assertRaisesRegex(RuntimeError, "integration file changed"):
            self.run_helper(["--candidate", str(candidate)], [str(standard / "fex-compat-tool")])
        self.execute.assert_not_called()

    def test_vulkan_trial_rejects_cpu_memory_model_override(self):
        candidate, prefix, standard = self.make_candidate()
        config = prefix / "fex-emu/vulkan-app.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"Config": {"TSOEnabled": "0"}, "ThunksDB": {"Vulkan": 1}}))
        record = json.loads((candidate / "integration.json").read_text())
        record["vulkan_trial"] = "native"
        (candidate / "integration.json").write_text(json.dumps(record))
        with self.assertRaisesRegex(RuntimeError, "preserve CPU"):
            self.run_helper(["--candidate", str(candidate)], [str(standard / "fex-compat-tool")])
        self.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
