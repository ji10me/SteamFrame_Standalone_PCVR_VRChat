import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location('memory_monitor', Path(__file__).resolve().parents[1] / 'tools/monitor_vrchat_memory.py')
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


class DrmCountersTest(unittest.TestCase):
    def test_duplicate_fds_count_once_and_kib_are_converted(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / 'fd').mkdir()
            (proc / 'fdinfo').mkdir()
            for descriptor in ('1', '2'):
                (proc / 'fd' / descriptor).symlink_to('/dev/dri/renderD128')
                (proc / 'fdinfo' / descriptor).write_text(
                    'drm-driver:\tmsm\ndrm-client-id:\t7\ndrm-total-memory:\t1024 KiB\n'
                    'drm-shared-memory:\t128 KiB\ndrm-resident-memory:\t512 KiB\n')
            (proc / 'fd' / '3').symlink_to('/dev/snd/controlC0')
            (proc / 'fd' / '4').symlink_to('/dev/dri/renderD128')
            (proc / 'fdinfo' / '4').write_text('drm-client-id:\t8\ndrm-total-memory:\t0\n')
            clients = monitor.drm_memory_clients(proc)
            self.assertEqual(len(clients), 2)
            self.assertEqual(clients['7'], {'total_bytes': 1024 * 1024, 'shared_bytes': 128 * 1024,
                                          'resident_bytes': 512 * 1024})
            self.assertEqual(clients['8']['total_bytes'], 0)


class FreshLogTest(unittest.TestCase):
    def test_cloned_prefix_log_is_ignored_until_current_launch_writes_a_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / 'output_log_old.txt'
            old.write_text('OnPlayerJoined\n' * 41)
            os.utime(old, (100, 100))
            self.assertIsNone(monitor.fresh_log_counts(root, 200))
            current = root / 'output_log_current.txt'
            current.write_text('Successfully joined\nOnPlayerJoined\n' * 2)
            os.utime(current, (201, 201))
            counts = monitor.fresh_log_counts(root, 200)
            self.assertEqual(counts['OnPlayerJoined'], 2)
            self.assertEqual(counts['Successfully joined'], 2)


class MemoryGuardTest(unittest.TestCase):
    def test_sustained_ram_pressure_can_stop_with_plenty_of_swap_but_a_dip_resets(self):
        mib = 1024**2
        critical, reserve = 0, 0
        for available, expected in [(700, None), (700, None), (1100, None),
                                    (700, None), (700, None), (700, 'sustained-low-available-ram')]:
            critical, reserve, reason = monitor.guard_decision(available * mib, 3000 * mib,
                                                              1024, critical, reserve)
            self.assertEqual(reason, expected)
        self.assertEqual(critical, 0)

    def test_original_guard_still_needs_two_critical_samples_and_reserve_is_opt_in(self):
        mib = 1024**2
        critical, reserve = 0, 0
        for _ in range(5):
            critical, reserve, reason = monitor.guard_decision(440 * mib, 2054 * mib,
                                                              None, critical, reserve)
            self.assertIsNone(reason)
        critical, reserve, reason = monitor.guard_decision(440 * mib, 900 * mib, None, critical, reserve)
        self.assertIsNone(reason)
        critical, reserve, reason = monitor.guard_decision(440 * mib, 900 * mib, None, critical, reserve)
        self.assertEqual(reason, 'critical-memory-and-swap')


if __name__ == '__main__':
    unittest.main()
