from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import launch_vrchat_when_ready as launch


READY = {'hmd_connected': True, 'hmd_pose_valid': True, 'hmd_tracking_result': 200,
         'chaperone_calibration_state': 1, 'tracked_controllers': 2, 'proximity_pressed': False}
SOURCE = ',99:((a,b,t)=>{var v=t(5757);SteamClient.Apps.RunGame(x.GetGameID(),"",-1,source),v.oy.SetRunningApp(x.appid)})'


class LaunchReadinessTest(unittest.TestCase):
    def test_covered_sensor_or_valid_head_pose_does_not_replace_controller_and_room_readiness(self):
        self.assertEqual(launch.readiness_reason(READY), 'ready')
        sleeping = dict(READY, tracked_controllers=0, proximity_pressed=True, hmd_activity=1)
        self.assertEqual(launch.readiness_reason(sleeping), 'controller_tracking_not_ready')
        self.assertEqual(launch.readiness_reason(dict(READY, chaperone_calibration_state=203)), 'playspace_not_calibrated')
        self.assertEqual(launch.readiness_reason(dict(READY, hmd_tracking_result=201)), 'headset_tracking_not_ready')
        old_probe = dict(READY)
        del old_probe['chaperone_calibration_state']
        self.assertEqual(launch.readiness_reason(old_probe), 'tracking_probe_update_required')

    def test_brief_ready_signal_then_tracking_loss_must_settle_again(self):
        now = [0.0]
        def read():
            return dict(READY, tracked_controllers=0) if 2 <= now[0] < 3 else READY
        def sleep(seconds):
            now[0] += seconds
        launch.wait_for_ready(read, timeout=10, clock=lambda: now[0], sleep=sleep)
        self.assertGreaterEqual(now[0], 6)

    def test_already_running_game_is_refused_before_sampling_or_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory) / '123'
            proc.mkdir()
            (proc / 'environ').write_bytes(b'SteamAppId=438100\0')
            read, rpc = Mock(), Mock()
            with self.assertRaisesRegex(RuntimeError, 'already active'):
                launch.launch_when_ready(SOURCE, read, stopped=lambda: launch.require_stopped(Path(directory)), rpc=rpc)
            read.assert_not_called()
            rpc.assert_not_called()

    def test_sleeping_device_times_out_without_any_steam_request(self):
        now = [0.0]
        rpc = Mock()
        def sleep(seconds):
            now[0] += seconds
        with self.assertRaisesRegex(RuntimeError, 'no launch requested'):
            launch.launch_when_ready(SOURCE, lambda: dict(READY, tracked_controllers=0),
                stopped=lambda: None, rpc=rpc, timeout=5, clock=lambda: now[0], sleep=sleep)
        rpc.assert_not_called()

    def test_normal_vr_launch_only_after_tracking_settles(self):
        now = [0.0]
        requests = []
        def sleep(seconds):
            now[0] += seconds
        def rpc(expression):
            self.assertGreaterEqual(now[0], 3)
            requests.append(expression)
            return {'appid': 438100, 'launch_requested': True}
        launch.launch_when_ready(SOURCE, lambda: READY, stopped=lambda: None, rpc=rpc,
            sleep=sleep, clock=lambda: now[0])
        self.assertEqual(len(requests), 1)
        self.assertIn('bIsVRLaunchOption', requests[0])
        self.assertIn('RunGame("438100"', requests[0])
        self.assertIn('store.SetRunningApp(438100)', requests[0])
        for forbidden in ('TerminateApp', 'SetHMDSettings', 'FadeGrid', 'CommitWorkingCopy', 'SetAppLaunchOptions'):
            self.assertNotIn(forbidden, requests[0])
        with self.assertRaises(RuntimeError):
            launch.running_app_module(SOURCE.replace('SetRunningApp', 'AnotherAction'))


if __name__ == '__main__':
    unittest.main()
