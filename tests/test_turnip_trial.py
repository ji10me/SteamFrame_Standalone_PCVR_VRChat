import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import stage_turnip_trial as trial


class TurnipTrialTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        home_patch = patch.object(Path, 'home', return_value=self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.base = self.home / '.local/share/frame-pcvr'
        self.source = self.base / 'candidates/source'
        self.source.mkdir(parents=True)
        self.driver = self.home / 'driver.so'
        self.driver.write_bytes(b'fixture-native-driver')
        driver_patch = patch.object(trial, 'NATIVE_DRIVER', self.driver)
        driver_patch.start()
        self.addCleanup(driver_patch.stop)
        self.manifest = {'emulator_v0': {'environment': {}, 'container_environment': {
            'DXVK_CONFIG': 'dxvk.trackPipelineLifetime = True; dxgi.maxDeviceMemory = 2048',
            'PROTON_USE_XALIA': '0', 'VK_INSTANCE_LAYERS': '', 'FDM_DEBUG': '', 'RPO_DEBUG': ''}}}
        (self.source / 'emulator.json').write_text(json.dumps(self.manifest))
        (self.source / 'integration.json').write_text(json.dumps({
            'state': 'smoke-passed', 'commit': trial.FEX_COMMIT,
            'vulkan_trial': 'native', 'multiview_trial': 'none'}))
        self.proof = self.base / 'runs/image/results.json'
        self.proof.parent.mkdir(parents=True)
        self.data = {'candidate': str(self.source), 'mode': 'route', 'probe': 'image',
                     'turnip_autotune_algo': 'profiled',
                     'candidate_integration_sha256': trial.sha256(self.source / 'integration.json'),
                     'native_driver_sha256': trial.sha256(self.driver),
                     'cases': [{'name': name, 'returncode': 0, 'timed_out': False,
                                'log_tail': 'image=2880x1440x1 mismatched_pixels=0 passed\n'
                                            'image=1440x1440x2 mismatched_pixels=0 passed'}
                               for name in ('native', 'guest-thunk-0', 'guest-thunk-1')]}

    def test_failed_stale_or_ignored_probe_refused_before_prefix_copy(self):
        for change in ('candidate', 'driver', 'integration', 'missing-case', 'failed-case', 'ignored', 'mismatch', 'no-tuning'):
            data = copy.deepcopy(self.data)
            if change == 'candidate': data['candidate'] = 'another'
            elif change == 'driver': data['native_driver_sha256'] = 'changed'
            elif change == 'integration': data['candidate_integration_sha256'] = 'changed'
            elif change == 'missing-case': data['cases'].pop()
            elif change == 'failed-case': data['cases'][0]['returncode'] = 1
            elif change == 'ignored': data['cases'][1]['log_tail'] += '\nUnknown TU_AUTOTUNE_ALGO'
            elif change == 'mismatch': data['cases'][2]['log_tail'] = 'image=2880x1440x1 mismatched_pixels=4 '
            elif change == 'no-tuning': data['turnip_autotune_algo'] = None
            self.proof.write_text(json.dumps(data))
            with patch.object(trial, 'stage_vulkan') as clone:
                with self.assertRaises(RuntimeError):
                    trial.main(self.source, self.proof)
                clone.assert_not_called()
        self.proof.write_text(json.dumps(self.data))
        manifest = copy.deepcopy(self.manifest)
        manifest['emulator_v0']['environment']['TU_DEBUG'] = 'gmem'
        (self.source / 'emulator.json').write_text(json.dumps(manifest))
        with patch.object(trial, 'stage_vulkan') as clone:
            with self.assertRaisesRegex(RuntimeError, 'Existing Turnip'):
                trial.main(self.source, self.proof)
            clone.assert_not_called()

    def test_only_private_gpu_policy_changes_after_valid_proof(self):
        self.proof.write_text(json.dumps(self.data))
        candidate, prefix = self.base / 'candidates/profiled', self.base / 'c/profiled'
        candidate.mkdir()
        prefix.mkdir(parents=True)
        (candidate / 'emulator.json').write_text(json.dumps(self.manifest))
        (candidate / 'integration.json').write_text(json.dumps({'extra_hashes': {}}))
        original = (self.source / 'emulator.json').read_bytes()
        with patch.object(trial, 'stage_vulkan', return_value={'candidate': str(candidate), 'prefix': str(prefix)}), \
                contextlib.redirect_stdout(io.StringIO()):
            trial.main(self.source, self.proof)
        expected = copy.deepcopy(self.manifest)
        expected['emulator_v0']['container_environment']['TU_AUTOTUNE_ALGO'] = 'profiled'
        self.assertEqual(json.loads((candidate / 'emulator.json').read_text()), expected)
        self.assertEqual((self.source / 'emulator.json').read_bytes(), original)
        record = json.loads((candidate / 'integration.json').read_text())
        self.assertEqual(record['extra_hashes']['emulator.json'], trial.sha256(candidate / 'emulator.json'))
        self.assertFalse(record['turnip_trial']['render_resolution_changed'])
        self.assertFalse(record['turnip_trial']['benefit_verified'])
        self.assertFalse(record['turnip_trial']['display_verified'])


if __name__ == '__main__':
    unittest.main()
