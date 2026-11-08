import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import record_vr_performance as perf


class NativeRecordingTest(unittest.TestCase):
    def test_discovers_only_the_recording_ui_import_and_sets_one_boolean(self):
        source = ',862:((je,te,t)=>{var p=t(92102);function Q(){p.p.SteamVR.SetHMDSettings({record_vr_stats:M})}})'
        self.assertEqual(perf.controller_module(source), 92102)
        with self.assertRaises(RuntimeError):
            perf.controller_module(source.replace('record_vr_stats', 'record_tracking'))
        calls = []
        perf.set_recording(92102, True, rpc=lambda expression: calls.append(expression) or {'record_request': True})
        self.assertIn('SetHMDSettings({record_vr_stats:true})', calls[0])
        for forbidden in ('record_tracking:', 'perf_graph_in_hmd:', 'supersample', 'motion_smoothing:'):
            self.assertNotIn(forbidden, calls[0])

    def test_exception_or_lost_start_response_still_requests_stop(self):
        for failure in ('identity', 'start'):
            calls = []
            identities = [0]
            def identity():
                identities[0] += 1
                if failure == 'identity' and identities[0] == 3:
                    raise RuntimeError('launch changed')
            def toggle(mid, enabled):
                calls.append(enabled)
                if enabled and failure == 'start':
                    raise RuntimeError('start response lost')
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError):
                    perf.recording_window(1, 5, identity, toggle=toggle)
            self.assertEqual(calls, [False, True, False])
        calls = []
        def wrong():
            raise RuntimeError('wrong launch')
        with self.assertRaises(RuntimeError):
            perf.recording_window(1, 5, wrong, toggle=lambda mid, value: calls.append(value))
        self.assertEqual(calls, [])

    def test_native_parser_filters_every_dashboard_transition_and_rejects_other_apps(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'capture.csv'
            header = ', '.join(perf.FIELDS)
            def row(ts, dashboard, fps):
                return f'{ts}, {dashboard}, {fps}, 40, 29, 40, 903, 20, 72, 36, 1728, 1728, \n'
            text = ('appId, steam.app.438100\nappName, private-example-not-exported\n' + header + '\n'
                    + row(1000, 1, 5) + row(2000, 0, 30) + row(3000, 0, 32) + row(4000, 0, 34)
                    + row(5000, 1, 4) + row(6000, 0, 20) + row(7000, 0, 22) + row(8000, 0, 24))
            path.write_text(text)
            result = perf.read_recording(path)
            self.assertEqual(result['dashboard_closed']['samples'], 6)
            self.assertEqual(result['settled_dashboard_closed']['samples'], 2)
            self.assertEqual(result['settled_dashboard_closed']['fps_time_weighted'], 29)
            self.assertEqual(result['settled_dashboard_closed']['cpu_interval_peak_max_ms'], 40)
            self.assertNotIn('private-example', json.dumps(result))
            path.write_text(text.replace('steam.app.438100', 'steam.app.123'))
            with self.assertRaisesRegex(ValueError, 'another application'):
                perf.read_recording(path)
            path.write_text(text.replace('40, 29', 'nan, 29', 1))
            with self.assertRaisesRegex(ValueError, 'unknown interval'):
                perf.read_recording(path)

    def test_duration_is_bounded_and_recording_stops_after_success(self):
        calls, current = [], [0.0]
        def sleep(seconds):
            current[0] += seconds
        with contextlib.redirect_stdout(io.StringIO()):
            perf.recording_window(1, 5, lambda: None, toggle=lambda mid, on: calls.append(on),
                                  sleep=sleep, clock=lambda: current[0])
        self.assertEqual(calls, [False, True, False])
        with self.assertRaises(ValueError):
            perf.recording_window(1, 3600, lambda: None, toggle=lambda mid, on: calls.append(on))

    def test_return_to_game_only_uses_the_dashboard_navigation_action(self):
        requests = []
        perf.return_to_game(rpc=lambda expression: requests.append(expression) or {'return_to_game_requested': True})
        self.assertIn('SteamClient.OpenVR.VROverlay.HideDashboard()', requests[0])
        self.assertNotIn('SetHMDSettings', requests[0])
        self.assertNotIn('TerminateApp', requests[0])

    def test_loading_or_empty_trace_never_counts_as_a_valid_benchmark(self):
        valid = {'pid': 123, 'scene_pid': 123, 'presents': 10, 'timing_available': True,
                 'scene_loading': False, 'grid_alpha': 0, 'timing': {'frame_index': 200}}
        self.assertTrue(perf.visible_frames_ready(valid, 123))
        for change in ({'presents': 0}, {'scene_loading': True}, {'grid_alpha': 1},
                       {'scene_pid': 0}, {'pid': 456}, {'timing_available': False}):
            self.assertFalse(perf.visible_frames_ready(dict(valid, **change), 123))
        with self.assertRaisesRegex(RuntimeError, 'zero performance'):
            perf.require_samples({'all': {'samples': 0}})
        with self.assertRaisesRegex(RuntimeError, 'dashboard-closed'):
            perf.require_samples({'all': {'samples': 60}, 'dashboard_closed': {'samples': 0}}, True)
        perf.require_samples({'all': {'samples': 60}, 'dashboard_closed': {'samples': 58}}, True)


if __name__ == '__main__':
    unittest.main()
