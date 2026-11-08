import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'frame_process_discovery', Path(__file__).resolve().parents[1] / 'tools/frame_process_discovery.py')
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)


class ProcessDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.expected = '/opt/steamvr/tools/vrlink/bin/linuxarm64/vrlink'

    def process(self, pid, target=None):
        proc = self.root / str(pid)
        proc.mkdir()
        if target is not None:
            (proc / 'exe').symlink_to(target)

    def find(self):
        return discovery.unique_executable_pid(self.expected, self.root)

    def test_empty_or_unreadable_process_is_not_a_match(self):
        self.process(100)
        self.assertIsNone(self.find())

    def test_unique_full_path_match(self):
        self.process(100, self.expected)
        self.process(101, '/other/bin/vrlink')
        self.assertEqual(self.find(), 100)

    def test_duplicate_match_is_ambiguous(self):
        self.process(100, self.expected)
        self.process(101, self.expected)
        self.assertIsNone(self.find())

    def test_same_basename_and_deleted_executable_do_not_match(self):
        self.process(100, '/other/bin/vrlink')
        self.process(101, self.expected + ' (deleted)')
        self.assertIsNone(self.find())

    def test_non_pid_directories_are_ignored(self):
        self.process('self', self.expected)
        self.process('１２３', self.expected)
        self.assertIsNone(self.find())

    def test_process_exit_or_permission_error_during_scan(self):
        self.process(100, self.expected)
        self.process(101, self.expected)
        real_readlink = discovery.os.readlink

        def disappearing(path):
            if str(path).endswith('/100/exe'):
                raise PermissionError('process unavailable')
            return real_readlink(path)

        with patch.object(discovery.os, 'readlink', side_effect=disappearing):
            self.assertEqual(self.find(), 101)


if __name__ == '__main__':
    unittest.main()
