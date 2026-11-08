import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import snapshot_vrchat_memory as memory


class LaunchSnapshotTest(unittest.TestCase):
    def test_counts_only_the_selected_launch_and_rejects_a_stale_main_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = '/private/prefix'
            for pid, app, path in [('10', '438100', prefix), ('11', '438100', prefix),
                                   ('12', '438100', '/another/prefix'), ('13', '123', prefix)]:
                proc = root / pid
                proc.mkdir()
                (proc / 'environ').write_bytes(f'SteamAppId={app}\0STEAM_COMPAT_DATA_PATH={path}\0'.encode())
                (proc / 'comm').write_text('VRChat.exe\n')
                (proc / 'smaps').write_text(
                    '1000-2000 rw-p 00000000 00:00 0 [anon:FEXAllocator]\nPss: 8 kB\nSwapPss: 4 kB\n'
                    '2000-3000 r--p 00000000 00:00 0 /private/secret/world.bin\nPss: 2 kB\nSwapPss: 0 kB\n')
            (root / 'meminfo').write_text('MemAvailable: 123 kB\nSwapFree: 45 kB\n')
            zram = root / 'mm_stat'
            zram.write_text('1000 500 600 0 700\n')
            report = memory.snapshot(root, prefix, 10, zram)
            self.assertEqual([row['pid'] for row in report['processes']], [10, 11])
            self.assertEqual(report['launch_pss_kib'], 20)
            self.assertEqual(report['launch_swap_pss_kib'], 8)
            self.assertEqual(report['system_bytes']['MemAvailable'], 123 * 1024)
            self.assertEqual(report['zram']['physical_bytes'], 600)
            self.assertNotIn('secret', json.dumps(report))
            with self.assertRaisesRegex(RuntimeError, 'Main PID'):
                memory.snapshot(root, prefix, 12, zram)


if __name__ == '__main__':
    unittest.main()
