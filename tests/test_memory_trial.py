import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import stage_memory_trial as trial


class MemoryTrialFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        home = Path(self.temp.name).resolve()
        self.patcher = patch.object(Path, 'home', return_value=home)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.base = home / '.local/share/frame-pcvr'
        self.source = self.base / 'candidates/source'
        self.source.mkdir(parents=True)
        self.baseline = self.base / 'runs/baseline.json'
        self.baseline.parent.mkdir()
        self.config = 'dxvk.trackPipelineLifetime = True; dxvk.numCompilerThreads = 2'
        self.data = {'appid': '438100', 'candidate': str(self.source),
                     'prefix': str(self.base / 'c/source'), 'DXVK_CONFIG': self.config,
                     'dxgi_memory_reporting_supported': True, 'reported_device_memory_MiB': 8192}

    def save(self):
        self.baseline.write_text(json.dumps(self.data))


class ReportedVramTrialTest(MemoryTrialFixture):
    def test_missing_evidence_and_existing_override_refuse_before_prefix_copy(self):
        for change in ({'dxgi_memory_reporting_supported': False},
                       {'reported_device_memory_MiB': None},
                       {'reported_device_memory_MiB': 4096},
                       {'reported_device_memory_MiB': 4064},
                       {'DXVK_CONFIG': self.config + '\ndxgi.maxDeviceMemory = 2048'}):
            with self.subTest(change=change):
                data = dict(self.data)
                self.data.update(change)
                self.save()
                with patch.object(trial, 'stage_vulkan') as clone:
                    with self.assertRaises(RuntimeError):
                        trial.main(self.source, self.baseline, 'dxgi-report-4096')
                    clone.assert_not_called()
                self.data = data

    def test_private_trial_preserves_existing_tuning_and_marks_unproven_texture_effect(self):
        self.data['reported_device_memory_MiB'] = 4064
        self.save()
        candidate = self.base / 'candidates/trial'
        prefix = self.base / 'c/trial'
        candidate.mkdir()
        prefix.mkdir(parents=True)
        manifest = {'emulator_v0': {'container_environment': {'PROTON_USE_XALIA': '0', 'VK_INSTANCE_LAYERS': '',
                                                             'DXVK_CONFIG': self.config}}}
        (candidate / 'emulator.json').write_text(json.dumps(manifest))
        (candidate / 'integration.json').write_text(json.dumps({'extra_hashes': {}}))
        original = json.dumps(manifest)
        (self.source / 'emulator.json').write_text(original)
        staged = {'candidate': str(candidate), 'prefix': str(prefix)}
        with patch.object(trial, 'stage_vulkan', return_value=staged), contextlib.redirect_stdout(io.StringIO()):
            trial.main(self.source, self.baseline, 'dxgi-report-2048')
        env = json.loads((candidate / 'emulator.json').read_text())['emulator_v0']['container_environment']
        self.assertEqual(env['DXVK_CONFIG'], self.config + '; dxgi.maxDeviceMemory = 2048')
        self.assertEqual(env['PROTON_USE_XALIA'], '0')
        self.assertEqual((self.source / 'emulator.json').read_text(), original)
        record = json.loads((candidate / 'integration.json').read_text())
        self.assertEqual(record['extra_hashes']['emulator.json'], trial.sha256(candidate / 'emulator.json'))
        self.assertFalse(record['memory_trial']['hard_allocation_cap'])
        self.assertFalse(record['memory_trial']['application_streaming_budget_verified'])
        self.assertFalse(record['memory_trial']['texture_residency_reduction_verified'])
        self.assertTrue(record['memory_trial']['render_quality_may_change'])


class DiskCacheTrialTest(MemoryTrialFixture):
    def setup_cache_files(self, candidate, prefix, enabled='1'):
        candidate.mkdir(parents=True, exist_ok=True)
        (prefix / 'fex-emu').mkdir(parents=True, exist_ok=True)
        template = {'Config': {'DiskCache': enabled, 'X87ReducedPrecision': '1', 'ProfileStats': '1'}}
        active = {'Config': dict(template['Config'], RootFS='/unchanged/rootfs')}
        (candidate / 'ConfigTemplate.json').write_text(json.dumps(template))
        (prefix / 'fex-emu/Config.json').write_text(json.dumps(active))
        manifest = {'emulator_v0': {'container_environment': {'DXVK_CONFIG': self.config,
                                                             'PROTON_USE_XALIA': '0',
                                                             'VK_INSTANCE_LAYERS': ''}}}
        (candidate / 'emulator.json').write_text(json.dumps(manifest))
        return template, active

    def test_cache_override_or_disabled_source_refuses_before_copy(self):
        prefix = Path(self.data['prefix'])
        self.setup_cache_files(self.source, prefix)
        for override in ('template-disabled', 'manifest-override', 'measured-override'):
            self.setup_cache_files(self.source, prefix)
            self.data.pop('FEX_DISKCACHE', None)
            if override == 'template-disabled':
                self.setup_cache_files(self.source, prefix, enabled='0')
            elif override == 'manifest-override':
                p = self.source / 'emulator.json'
                m = json.loads(p.read_text())
                m['emulator_v0']['container_environment']['FEX_DISKCACHE'] = '1'
                p.write_text(json.dumps(m))
            else:
                self.data['FEX_DISKCACHE'] = '1'
            self.save()
            with patch.object(trial, 'stage_vulkan') as clone:
                with self.assertRaises(RuntimeError):
                    trial.main(self.source, self.baseline, 'no-disk-cache')
                clone.assert_not_called()

    def test_only_private_cache_toggle_changes_and_template_hash_is_guarded(self):
        source_prefix = Path(self.data['prefix'])
        self.setup_cache_files(self.source, source_prefix)
        original_template = (self.source / 'ConfigTemplate.json').read_text()
        self.save()
        candidate, prefix = self.base / 'candidates/cache-off', self.base / 'c/cache-off'
        template, active = self.setup_cache_files(candidate, prefix)
        (candidate / 'integration.json').write_text(json.dumps({'extra_hashes': {}}))
        with patch.object(trial, 'stage_vulkan', return_value={'candidate': str(candidate), 'prefix': str(prefix)}), \
                contextlib.redirect_stdout(io.StringIO()):
            trial.main(self.source, self.baseline, 'no-disk-cache')
        template['Config']['DiskCache'] = '0'
        active['Config']['DiskCache'] = '0'
        self.assertEqual(json.loads((candidate / 'ConfigTemplate.json').read_text()), template)
        self.assertEqual(json.loads((prefix / 'fex-emu/Config.json').read_text()), active)
        self.assertEqual((self.source / 'ConfigTemplate.json').read_text(), original_template)
        self.assertEqual(json.loads((source_prefix / 'fex-emu/Config.json').read_text())['Config']['DiskCache'], '1')
        env = json.loads((candidate / 'emulator.json').read_text())['emulator_v0']['container_environment']
        self.assertEqual(env['DXVK_CONFIG'], self.config)
        self.assertEqual(env['PROTON_USE_XALIA'], '0')
        record = json.loads((candidate / 'integration.json').read_text())
        self.assertEqual(record['extra_hashes']['ConfigTemplate.json'], trial.sha256(candidate / 'ConfigTemplate.json'))
        self.assertFalse(record['memory_trial']['render_quality_changed'])
        self.assertFalse(record['memory_trial']['game_memory_reduction_verified'])
        self.assertFalse(record['memory_trial']['cpu_compatibility_options_changed'])


class ProfileStatsTrialTest(MemoryTrialFixture):
    def setup_stats_files(self, candidate, prefix):
        candidate.mkdir(parents=True, exist_ok=True)
        (prefix / 'fex-emu').mkdir(parents=True, exist_ok=True)
        template = {'Config': {'ProfileStats': '1', 'DiskCache': '0', 'X87ReducedPrecision': '1'}}
        active = {'Config': dict(template['Config'], RootFS='/unchanged/rootfs')}
        (candidate / 'ConfigTemplate.json').write_text(json.dumps(template))
        (prefix / 'fex-emu/Config.json').write_text(json.dumps(active))
        manifest = {'emulator_v0': {'environment': {}, 'container_environment': {
            'DXVK_CONFIG': self.config, 'PROTON_USE_XALIA': '0', 'VK_INSTANCE_LAYERS': ''}}}
        (candidate / 'emulator.json').write_text(json.dumps(manifest))
        return template, active

    def test_disabled_or_overridden_stats_refused_before_clone(self):
        prefix = Path(self.data['prefix'])
        for change in ('disabled-template', 'disabled-prefix', 'environment', 'container_environment', 'measured'):
            self.setup_stats_files(self.source, prefix)
            self.data.pop('FEX_PROFILESTATS', None)
            if change.startswith('disabled-'):
                path = self.source / 'ConfigTemplate.json' if change.endswith('template') else prefix / 'fex-emu/Config.json'
                config = json.loads(path.read_text())
                config['Config']['ProfileStats'] = '0'
                path.write_text(json.dumps(config))
            elif change == 'measured':
                self.data['FEX_PROFILESTATS'] = '1'
            else:
                path = self.source / 'emulator.json'
                manifest = json.loads(path.read_text())
                manifest['emulator_v0'][change]['FEX_PROFILESTATS'] = '1'
                path.write_text(json.dumps(manifest))
            self.save()
            with patch.object(trial, 'stage_vulkan') as clone:
                with self.assertRaises(RuntimeError):
                    trial.main(self.source, self.baseline, 'no-profile-stats')
                clone.assert_not_called()

    def test_only_private_diagnostics_change_preserves_stable_tuning(self):
        source_prefix = Path(self.data['prefix'])
        self.setup_stats_files(self.source, source_prefix)
        original = (self.source / 'ConfigTemplate.json').read_bytes()
        self.save()
        candidate, prefix = self.base / 'candidates/stats-off', self.base / 'c/stats-off'
        template, active = self.setup_stats_files(candidate, prefix)
        (candidate / 'integration.json').write_text(json.dumps({'extra_hashes': {}}))
        with patch.object(trial, 'stage_vulkan', return_value={'candidate': str(candidate), 'prefix': str(prefix)}), \
                contextlib.redirect_stdout(io.StringIO()):
            trial.main(self.source, self.baseline, 'no-profile-stats')
        template['Config']['ProfileStats'] = active['Config']['ProfileStats'] = '0'
        self.assertEqual(json.loads((candidate / 'ConfigTemplate.json').read_text()), template)
        self.assertEqual(json.loads((prefix / 'fex-emu/Config.json').read_text()), active)
        self.assertEqual((self.source / 'ConfigTemplate.json').read_bytes(), original)
        self.assertEqual(json.loads((source_prefix / 'fex-emu/Config.json').read_text())['Config']['ProfileStats'], '1')
        env = json.loads((candidate / 'emulator.json').read_text())['emulator_v0']['container_environment']
        self.assertEqual(env['DXVK_CONFIG'], self.config)
        self.assertEqual(env['PROTON_USE_XALIA'], '0')
        self.assertEqual(env['VK_INSTANCE_LAYERS'], '')
        record = json.loads((candidate / 'integration.json').read_text())
        self.assertEqual(record['extra_hashes']['ConfigTemplate.json'], trial.sha256(candidate / 'ConfigTemplate.json'))
        for flag in ('benefit_verified', 'cpu_compatibility_options_changed', 'disk_cache_setting_changed',
                     'render_quality_changed', 'game_memory_reduction_verified', 'game_cpu_reduction_verified'):
            self.assertFalse(record['memory_trial'][flag])


if __name__ == '__main__':
    unittest.main()
