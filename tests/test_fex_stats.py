from pathlib import Path
import json
import os
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import sample_fex_stats as stats


def buffer():
    data = bytearray(4096)
    struct.pack_into('<BBH', data, 0, 2, 1, 128)
    struct.pack_into('<II', data, 52, 64, len(data))
    struct.pack_into('<II15Q', data, 64, 192, 101, *range(15))
    struct.pack_into('<II15Q', data, 192, 0, 102, *range(15))
    return data


class FexStatsTest(unittest.TestCase):
    def test_reader_requires_the_published_inode_mapped_by_this_process(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = Path(folder) / '101'
            shm = proc / 'root/dev/shm'
            shm.mkdir(parents=True)
            path = shm / 'fex-101-stats'
            path.write_bytes(buffer())
            path.chmod(0o600)
            st = path.stat()
            (proc / 'maps').write_text(f'1000-2000 rw-s 0 {os.major(st.st_dev):x}:{os.minor(st.st_dev):x} '
                                      f'{st.st_ino} /dev/shm/fex-101-stats\n')
            fd = stats.open_stats(proc)
            try:
                self.assertEqual(stats.parse_stats(os.pread(fd, 4096, 0))[101]['slot'], 64)
            finally:
                os.close(fd)
            (proc / 'maps').write_text('')
            with self.assertRaises(RuntimeError):
                stats.open_stats(proc)
            path.unlink()
            path.symlink_to('/dev/null')
            with self.assertRaises(OSError):
                stats.open_stats(proc)

    def test_unavailable_is_preserved_and_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'report.json'
            args = ['sample', '--pid', '101', '--prefix', '/private/p', '--seconds', '5', '--output', str(output)]
            with patch.object(sys, 'argv', args), patch.object(stats, 'identity', return_value=1), \
                    patch.object(stats, 'open_stats', return_value=None):
                stats.main()
                result = json.loads(output.read_text())
                self.assertFalse(result['available'])
                self.assertEqual(result['samples'], [])
                with self.assertRaises(FileExistsError):
                    stats.main()

    def test_published_fields_and_retired_slots(self):
        data = buffer()
        rows = stats.parse_stats(data)
        self.assertEqual(rows[101]['counters']['sigbus_count'], 2)
        self.assertEqual(rows[102]['counters']['invalidation_count'], 12)
        self.assertEqual(rows[102]['counters']['callret_flush_count'], 14)
        struct.pack_into('<I', data, 68, 0)
        self.assertEqual(set(stats.parse_stats(data)), {102})

    def test_invalid_or_racing_topology_is_rejected(self):
        for offset, value in [(64, 64), (64, 80), (64, 4096), (196, 101), (52, 80), (56, 8192)]:
            with self.subTest(offset=offset, value=value):
                data = buffer()
                struct.pack_into('<I', data, offset, value)
                with self.assertRaises(ValueError):
                    stats.parse_stats(data)
        for data in [b'', b'0' * (stats.LIMIT + 1), bytes(4096)]:
            with self.assertRaises(ValueError):
                stats.parse_stats(data)

    def test_slot_reuse_thread_reuse_and_regression_are_excluded(self):
        old = stats.parse_stats(buffer())
        for row in old.values():
            row['start'] = 100
        new = {tid: {**row, 'counters': {k: v + 7 for k, v in row['counters'].items()}}
               for tid, row in old.items()}
        summary, _ = stats.difference(old, new)
        self.assertEqual(summary['totals']['jit_count'], 14)
        for field, value in [('slot', 320), ('start', 101)]:
            changed = {**new, 101: {**new[101], field: value}}
            summary, _ = stats.difference(old, changed)
            self.assertEqual(summary['matched_threads'], 1)
        new[101]['counters']['jit_count'] = 0
        self.assertEqual(stats.difference(old, new)[0]['matched_threads'], 1)
        self.assertIsNone(stats.difference({}, new)[0]['totals'])


if __name__ == '__main__':
    unittest.main()
