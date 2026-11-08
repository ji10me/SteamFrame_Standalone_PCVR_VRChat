from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import sample_vrchat_threads as sample


def stat(ticks=100, start=20):
    fields = ['0'] * 40
    fields[0] = 'S'
    fields[11], fields[12], fields[19] = str(ticks), '10', str(start)
    return '123 (name with ) spaces) ' + ' '.join(fields)


class ThreadSamplingTest(unittest.TestCase):
    def test_delta_handles_thread_reuse_and_normalizes_to_one_core(self):
        old = {1: {'ticks': 100, 'start_ticks': 9}, 2: {'ticks': 100, 'start_ticks': 7}}
        new = {1: {'ticks': 200, 'start_ticks': 9, 'role': 'main'},
               2: {'ticks': 200, 'start_ticks': 8, 'role': 'other'},
               3: {'ticks': 500, 'start_ticks': 4, 'role': 'other'}}
        self.assertEqual(sample.delta(old, new, 2, 100), [{'tid': 1, 'role': 'main', 'cpu_percent': 50.0}])
        self.assertEqual(sample.task_counters(stat()), {'ticks': 110, 'start_ticks': 20})

    def test_scoping_rejects_other_launch_and_recycled_main_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = root / '123'
            task = process / 'task/123'
            task.mkdir(parents=True)
            (process / 'environ').write_bytes(b'SteamAppId=438100\0STEAM_COMPAT_DATA_PATH=/private/prefix\0')
            (process / 'stat').write_text(stat())
            (task / 'stat').write_text(stat())
            (task / 'comm').write_text('private-avatar-or-user-name')
            started, rows = sample.read_threads(root, 123, '/private/prefix')
            self.assertEqual(started, 20)
            self.assertEqual(rows[123]['role'], 'main')
            self.assertEqual(sample.role('private-avatar-or-user-name'), 'other')
            self.assertNotIn('private-avatar', json.dumps(rows))
            with self.assertRaises(RuntimeError):
                sample.read_threads(root, 123, '/other/prefix')
            with self.assertRaises(RuntimeError):
                sample.read_threads(root, 123, '/private/prefix', start_ticks=21)


if __name__ == '__main__':
    unittest.main()
