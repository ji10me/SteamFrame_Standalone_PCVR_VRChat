import io
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from guard_frame_thermals import ThermalPolicy, StopGuard, next_cap, restore_caps, caller_alive, GPU_TABLE, cap_matches
from measure_frame_thermals import (restore_ui, hmd_settings, set_profile, hardware_profile,
                                    set_hardware_profile, activate_performance, measurement_ready)
import guard_frame_thermals as guard_module


class MeasurementReadiness(unittest.TestCase):
    def stats(self):
        return {'pid': 42, 'scene_pid': 42, 'hmd_connected': True, 'hmd_pose_valid': True,
                'hmd_tracking_result': 200, 'chaperone_calibration_state': 1,
                'tracked_controllers': 0, 'scene_app_is_vrchat': True,
                'presents': 100, 'timing_available': True, 'timing': {'frame_index': 50},
                'scene_loading': False, 'grid_alpha': 0, 'fade_alpha': 0}

    def test_controllerless_mode_is_explicit_and_does_not_change_default(self):
        stats = self.stats()
        self.assertFalse(measurement_ready(stats, 42))
        self.assertTrue(measurement_ready(stats, 42, without_controllers=True))
        stats['tracked_controllers'] = 2
        self.assertTrue(measurement_ready(stats, 42))
        self.assertTrue(measurement_ready(stats, 42, without_controllers=True))

    def test_controllerless_mode_retains_tracking_calibration_and_scene_checks(self):
        for field, bad in (('hmd_connected', False), ('hmd_pose_valid', False),
                           ('hmd_tracking_result', 201), ('chaperone_calibration_state', 2),
                           ('scene_pid', 43), ('scene_app_is_vrchat', False), ('presents', 0),
                           ('timing_available', False), ('scene_loading', True),
                           ('grid_alpha', 1), ('fade_alpha', 1)):
            with self.subTest(field=field):
                stats = self.stats()
                stats[field] = bad
                self.assertFalse(measurement_ready(stats, 42, without_controllers=True))

    def test_controllerless_mode_rejects_missing_or_malformed_probe_data(self):
        for value in (None, -1, False, '0'):
            stats = self.stats()
            stats['tracked_controllers'] = value
            self.assertFalse(measurement_ready(stats, 42, without_controllers=True))
        for key in ('tracked_controllers', 'chaperone_calibration_state', 'timing', 'fade_alpha'):
            stats = self.stats()
            del stats[key]
            self.assertFalse(measurement_ready(stats, 42, without_controllers=True))


class Policy(unittest.TestCase):
    def test_warmth_steps_down_without_changing_mode(self):
        policy = ThermalPolicy()
        self.assertEqual(policy.update(0, 79.9), 'hold')
        self.assertEqual(policy.update(1, 80), 'down')
        self.assertEqual(policy.update(2, 81), 'hold')
        self.assertEqual(policy.update(3, 81), 'down')
        self.assertEqual(policy.update(3.25, 85), 'hold')
        self.assertEqual(policy.update(3.5, 85), 'down')

    def test_88_degrees_ends_measurement_with_margin_to_90(self):
        self.assertEqual(ThermalPolicy().update(0, 88), 'stop')

    def test_recovery_requires_ten_seconds_continuously_cool(self):
        policy = ThermalPolicy()
        self.assertEqual(policy.update(0, 75), 'hold')
        self.assertEqual(policy.update(9, 75), 'hold')
        self.assertEqual(policy.update(10, 77), 'hold')
        self.assertEqual(policy.update(11, 75), 'hold')
        self.assertEqual(policy.update(20, 75), 'hold')
        self.assertEqual(policy.update(21, 75), 'up')

    def test_sensor_faults_are_not_interpreted_as_cooling(self):
        for value in (math.nan, math.inf, 0, -1, 120):
            with self.subTest(value=value), self.assertRaises(StopGuard):
                ThermalPolicy().update(1, value)

    def test_gpu_never_leaves_factory_steps(self):
        current = 903
        steps = []
        for _ in range(20):
            current = next_cap(GPU_TABLE, current, -1)
            steps.append(current)
        self.assertEqual(steps[:7], [834, 770, 720, 680, 629, 578, 500])
        self.assertEqual(steps[-1], 500)
        for _ in range(20):
            current = next_cap(GPU_TABLE, current, 1)
        self.assertEqual(current, 903)

    def test_cpu_uses_supported_step_without_crossing_floor(self):
        table = [2035200, 2131200, 2188800, 2246400, 2323200, 3148800]
        self.assertEqual(next_cap(table, 2323200, -1, cpu=True), 2188800)
        self.assertEqual(next_cap(table, 2131200, -1, cpu=True), 2035200)
        self.assertEqual(next_cap(table, 2035200, -1, cpu=True), 2035200)


class Recovery(unittest.TestCase):
    def test_hardware_only_does_not_request_a_native_render_profile_change(self):
        active, ended = threading.Event(), threading.Event()
        report, calls = {}, []
        def hardware(name):
            calls.append(name)
            active.set()
        with patch('measure_frame_thermals.set_profile'):
            activate_performance(1, report, active, ended,
                                 ui=lambda *args: self.fail('native profile request'),
                                 read=lambda: self.fail('native profile transition poll'),
                                 hardware=hardware, hardware_only=True)
        self.assertEqual(calls, ['Performance'])
        self.assertTrue(report['hardware_only'])
        self.assertTrue(report['hardware_attempted'])
        self.assertTrue(report['mode_attempted'])

    def test_hardware_only_still_checks_guard_before_any_request(self):
        active, ended = threading.Event(), threading.Event()
        ended.set()
        calls = []
        with self.assertRaises(RuntimeError):
            activate_performance(1, {}, active, ended, hardware=lambda *args: calls.append(args),
                                 hardware_only=True)
        self.assertEqual(calls, [])

    def test_lost_hardware_acknowledgement_still_marks_restoration_needed(self):
        active, ended = threading.Event(), threading.Event()
        report = {}
        def hardware(name):
            raise TimeoutError('lost reply')
        with self.assertRaises(TimeoutError):
            activate_performance(1, report, active, ended, hardware=hardware, hardware_only=True)
        self.assertTrue(report['mode_attempted'])
        self.assertTrue(report['hardware_attempted'])

    def test_queued_cpu_limit_is_allowed_to_settle(self):
        now, waits = [0.0], []
        def sleep(seconds):
            waits.append(seconds)
            now[0] += seconds
        def read(path):
            return 3000 if now[0] < .03 else 2800
        self.assertTrue(cap_matches(Path('/cpu'), 2800, 3000, read,
                                   clock=lambda: now[0], sleep=sleep))
        self.assertGreaterEqual(now[0], .03)
        self.assertLessEqual(now[0], .1)
        self.assertTrue(all(value <= .01 for value in waits))

    def test_stuck_cpu_limit_has_a_bounded_failure(self):
        now = [0.0]
        def sleep(seconds):
            now[0] += seconds
        self.assertFalse(cap_matches(Path('/cpu'), 2800, 3000, lambda path: 3000,
                                    clock=lambda: now[0], sleep=sleep))
        self.assertAlmostEqual(now[0], .1)

    def test_unrelated_limit_during_settle_is_not_accepted_or_retried(self):
        with patch('guard_frame_thermals.time.sleep') as sleep:
            self.assertFalse(cap_matches(Path('/cpu'), 2800, 3000, lambda path: 2700))
        sleep.assert_not_called()

    def test_restore_also_waits_for_the_queued_cpu_update(self):
        path = Path('/cpu')
        now, scheduled, writes = [0.0], [False], []
        def read(path):
            return 2035200 if scheduled[0] and now[0] >= .03 else 2800000
        def write(path, value):
            scheduled[0] = True
            writes.append(value)
        def sleep(seconds):
            now[0] += seconds
        with patch('guard_frame_thermals.time.monotonic', side_effect=lambda: now[0]), \
             patch('guard_frame_thermals.time.sleep', side_effect=sleep):
            result = restore_caps({path: 2035200}, {path: {2800000}}, read, write)
        self.assertEqual(writes, [2035200])
        self.assertEqual(result[str(path)], 'restored')

    def test_unacknowledged_upward_request_is_superseded_even_if_readback_is_original(self):
        path = Path('/cpu')
        writes = []
        result = restore_caps({path: 2035200}, {path: {2035200, 2800000}},
                              lambda path: 2035200, lambda path, value: writes.append(value), pending={path})
        self.assertEqual(writes, [2035200])
        self.assertEqual(result[str(path)], 'restored')

    def test_pending_request_does_not_overwrite_an_external_value(self):
        path = Path('/cpu')
        writes = []
        result = restore_caps({path: 2035200}, {path: {2035200, 2800000}},
                              lambda path: 1900000, lambda path, value: writes.append(value), pending={path})
        self.assertEqual(writes, [])
        self.assertEqual(result[str(path)], 'external_change_preserved')

    def test_no_mode_request_after_guard_termination(self):
        ended, active = threading.Event(), threading.Event()
        ended.set()
        calls, report = [], {}
        with self.assertRaises(RuntimeError):
            activate_performance(1, report, active, ended, ui=lambda *args: calls.append('ui'))
        self.assertEqual(calls, [])
        self.assertNotIn('mode_attempted', report)

    def test_guard_exit_during_ui_transition_blocks_hardware_activation(self):
        ended, active = threading.Event(), threading.Event()
        calls, report = [], {}
        def read():
            ended.set()
            return {'profile': 'Performance', 'recording': False}
        with self.assertRaises(RuntimeError):
            activate_performance(1, report, active, ended, ui=lambda *args: calls.append('ui'),
                                 read=read, hardware=lambda *args: calls.append('hardware'))
        self.assertEqual(calls, ['ui'])
        self.assertTrue(report['mode_attempted'])
        self.assertNotIn('hardware_attempted', report)

    def test_guard_exit_during_hardware_request_is_not_followed_by_five_second_wait(self):
        ended = threading.Event()
        class Active:
            def wait(self, seconds):
                raise AssertionError('must not wait after guard exit')
        report = {}
        with self.assertRaisesRegex(RuntimeError, 'ended during hardware'):
            activate_performance(1, report, Active(), ended, ui=lambda *args: None,
                                 read=lambda: {'profile': 'Performance'}, hardware=lambda name: ended.set())
        self.assertTrue(report['hardware_attempted'])

    def test_guard_failure_while_waiting_is_detected_in_a_short_poll(self):
        ended, waits = threading.Event(), []
        class Active:
            def wait(self, seconds):
                waits.append(seconds)
                ended.set()
                return False
        with self.assertRaises(RuntimeError):
            activate_performance(1, {}, Active(), ended, ui=lambda *args: None,
                                 read=lambda: {'profile': 'Performance'}, hardware=lambda name: None)
        self.assertEqual(waits, [.1])

    def test_initial_cached_ui_value_is_allowed_to_propagate(self):
        active, ended = threading.Event(), threading.Event()
        active.set()
        values = iter([{'profile': 'Powersave'}, {'profile': 'Performance'}])
        calls, report = [], {}
        with patch('measure_frame_thermals.time.sleep'):
            activate_performance(1, report, active, ended, ui=lambda *args: calls.append('ui'),
                                 read=lambda: next(values), hardware=lambda *args: calls.append('hardware'))
        self.assertEqual(calls, ['ui', 'hardware'])
        self.assertEqual(report['performance_readback']['profile'], 'Performance')
    def test_only_owned_maximums_are_restored(self):
        cpu, gpu, other = Path('/cpu-max'), Path('/gpu-max'), Path('/changed-max')
        values = {cpu: 2500000, gpu: 629000000, other: 1700000}
        writes = []
        def write(path, value):
            writes.append((path, value))
            values[path] = value
        result = restore_caps({cpu: 2035200, gpu: 629000000, other: 2035200},
                              {cpu: {2500000}, gpu: {834000000}, other: {2500000}},
                              values.__getitem__, write)
        self.assertEqual(writes, [(cpu, 2035200)])
        self.assertEqual(result[str(other)], 'external_change_preserved')

    def test_one_restore_failure_does_not_skip_remaining_caps(self):
        first, second = Path('/one'), Path('/two')
        writes = []
        values = {first: 3, second: 3}
        def write(path, value):
            if path == first:
                raise OSError('lost write')
            values[path] = value
            writes.append(path)
        result = restore_caps({first: 2, second: 2}, {first: {3}, second: {3}}, values.__getitem__, write)
        self.assertEqual(writes, [second])
        self.assertEqual(result[str(first)], 'restore_failed')

    def test_recording_stop_failure_does_not_skip_power_restoration(self):
        calls = []
        def recording(module, enabled):
            raise TimeoutError('lost acknowledgement')
        errors = restore_ui(1, 'Powersave', lambda module, name: calls.append(name), recording)
        self.assertEqual(calls, ['Powersave'])
        self.assertEqual(errors, ['TimeoutError'])

    def test_hardware_restoration_precedes_ui_even_if_its_request_fails(self):
        calls = []
        def hardware():
            calls.append('hardware')
            raise TimeoutError('lost reply')
        errors = restore_ui(1, 'Powersave', lambda *args: calls.append('ui'),
                            lambda *args: calls.append('recording'), hardware=hardware)
        self.assertEqual(calls, ['hardware', 'recording', 'ui'])
        self.assertEqual(errors, ['TimeoutError'])

    def test_stock_manager_profile_is_independent_of_ui_setting(self):
        class Reply:
            stdout = 's "Performance"\n'
        calls = []
        def command(args, **kwargs):
            calls.append(args)
            return Reply()
        self.assertEqual(hardware_profile(command), 'Performance')
        set_hardware_profile('Powersave', command)
        self.assertIn('set-property', calls[-1])
        self.assertEqual(calls[-1][-2:], ['s', 'Powersave'])
        with self.assertRaises(ValueError):
            set_hardware_profile('custom', command)

    def test_cancellation_pipe_accepts_no_commands_and_stops_on_eof(self):
        for value in (b'', b'anything'):
            with patch('guard_frame_thermals.os.read', return_value=value), self.assertRaises(StopGuard):
                caller_alive()
        with patch('guard_frame_thermals.os.read', side_effect=BlockingIOError):
            caller_alive()

    def test_settings_readback_fails_closed(self):
        for value in (None, {}, {'profile': 'unknown', 'recording': False}):
            with self.assertRaises(RuntimeError):
                hmd_settings(lambda expression: value)

    def test_profile_request_contains_only_the_requested_power_setting(self):
        calls = []
        set_profile(42, 'Performance', lambda expression: calls.append(expression) or {'profile_request': 'Performance'})
        self.assertIn('performance_profile_value:"Performance"', calls[0])
        for unrelated in ('resolution_scale', 'record_vr_stats', 'refresh_rate', 'motion_smoothing'):
            self.assertNotIn(unrelated, calls[0])


class GuardLoop(unittest.TestCase):
    def exercise(self, external=False, warm_start=False, disconnect_after_switch=False, cpu_only=False, gpu_only=False, coupled=False, severe=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cpu, gpu = root / 'cpu', root / 'gpu'
            gpu.mkdir()
            for name, value in {'min_freq': '231000000', 'max_freq': '629000000',
                                'governor': 'simple_ondemand',
                                'available_frequencies': ' '.join(str(v * 1000000) for v in GPU_TABLE)}.items():
                (gpu / name).write_text(value)
            for n, maximum in guard_module.CPU_FACTORY.items():
                path = cpu / f'policy{n}'
                path.mkdir(parents=True)
                for name, value in {'scaling_max_freq': '2035200', 'cpuinfo_max_freq': str(maximum),
                                    'scaling_available_frequencies': f'2035200 2150400 {maximum}',
                                    'scaling_governor': 'schedutil', 'scaling_min_freq': '480000'}.items():
                    (path / name).write_text(value)
            now = [0]
            rows = []
            def sleep(seconds):
                if now[0] == 0:
                    for n, maximum in guard_module.CPU_FACTORY.items():
                        (cpu / f'policy{n}/scaling_max_freq').write_text(str(maximum))
                    (gpu / 'max_freq').write_text('903000000')
                now[0] += seconds
                if external and now[0] >= 3:
                    (gpu / 'max_freq').write_text('600000000')
            def temperatures():
                stop_at = 9 if coupled and gpu_only else 5
                peak = 70 if now[0] < .25 or not warm_start and now[0] == .25 else 88 if now[0] >= stop_at else 86 if severe else 85 if coupled and now[0] >= 3 else 82
                return {'cpu-thermal': 70 if gpu_only else peak, 'gpu-thermal': 70 if cpu_only else peak - 1 if not gpu_only else peak}
            def caller():
                if disconnect_after_switch and now[0] > 0:
                    raise StopGuard('caller_stop_or_disconnect')
            with patch.object(guard_module, 'CPU', cpu), patch.object(guard_module, 'GPU', gpu), \
                 patch.object(guard_module, 'identity', return_value=123), \
                 patch.object(guard_module, 'caller_alive', side_effect=caller), patch.object(guard_module, 'emit', side_effect=rows.append), \
                 patch.object(guard_module, 'read_temperatures', side_effect=temperatures), \
                 patch.object(guard_module.time, 'monotonic', side_effect=lambda: now[0]), \
                 patch.object(guard_module.time, 'sleep', side_effect=sleep):
                guard_module.guard(42, 1000, 10)
            self.assertEqual((gpu / 'min_freq').read_text(), '231000000')
            self.assertEqual((gpu / 'governor').read_text(), 'simple_ondemand')
            for n in guard_module.CPU_FACTORY:
                self.assertEqual((cpu / f'policy{n}/scaling_max_freq').read_text(), '2035200')
                self.assertEqual((cpu / f'policy{n}/scaling_governor').read_text(), 'schedutil')
                self.assertEqual((cpu / f'policy{n}/scaling_min_freq').read_text(), '480000')
            if not disconnect_after_switch:
                self.assertTrue(any(row.get('changed_caps', 0) > 0 for row in rows))
            samples = [row for row in rows if row.get('phase') == 'sample']
            if cpu_only and not coupled and not severe:
                self.assertTrue(all(row['gpu_max_MHz'] == 903 for row in samples))
            if gpu_only and not coupled and not severe:
                self.assertTrue(all(row['cpu_max_kHz'] == {f'policy{n}': v for n, v in guard_module.CPU_FACTORY.items()} for row in samples))
            if coupled and cpu_only:
                self.assertTrue(any(row['gpu_max_MHz'] < 903 for row in samples))
            if coupled and gpu_only:
                self.assertTrue(any(row['cpu_max_kHz'] != {f'policy{n}': v for n, v in guard_module.CPU_FACTORY.items()} for row in samples))
            if severe:
                first = next(row for row in samples if row['peak_C'] >= 86)
                self.assertLess(first['gpu_max_MHz'], 903)
                self.assertNotEqual(first['cpu_max_kHz'], {f'policy{n}': v for n, v in guard_module.CPU_FACTORY.items()})
            self.assertEqual((gpu / 'max_freq').read_text(), '600000000' if external else '629000000')
            return rows[-1]

    def test_hot_loop_reduces_maximums_then_restores_at_88(self):
        result = self.exercise()
        self.assertEqual(result['reason'], 'temperature_88C_stop')
        self.assertTrue(all(value in ('restored', 'already_original') for value in result['restoration'].values()))

    def test_external_limit_change_ends_trial_and_is_preserved(self):
        result = self.exercise(external=True)
        self.assertEqual(result['reason'], 'external_limits_change')
        self.assertIn('external_change_preserved', result['restoration'].values())

    def test_warm_profile_transition_enters_gradual_control(self):
        result = self.exercise(warm_start=True)
        self.assertTrue(result['active'])
        self.assertEqual(result['reason'], 'temperature_88C_stop')

    def test_disconnect_after_profile_apply_before_poll_still_restores(self):
        result = self.exercise(disconnect_after_switch=True)
        self.assertFalse(result['active'])
        self.assertEqual(result['reason'], 'caller_stop_or_disconnect')
        self.assertTrue(all(value == 'restored' for value in result['restoration'].values()))

    def test_cpu_hotspots_preserve_a_cool_gpu(self):
        self.exercise(cpu_only=True)

    def test_gpu_hotspots_preserve_cool_cpu_clusters(self):
        self.exercise(gpu_only=True)

    def test_cpu_floor_with_continued_heat_gets_gpu_relief(self):
        self.exercise(cpu_only=True, coupled=True)

    def test_gpu_floor_with_continued_heat_gets_cpu_relief(self):
        self.exercise(gpu_only=True, coupled=True)

    def test_severe_cpu_heat_gets_early_shared_heat_relief(self):
        self.exercise(cpu_only=True, severe=True)

    def test_severe_gpu_heat_gets_early_shared_heat_relief(self):
        self.exercise(gpu_only=True, severe=True)


if __name__ == '__main__':
    unittest.main()
