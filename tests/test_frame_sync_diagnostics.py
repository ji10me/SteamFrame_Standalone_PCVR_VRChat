"""Compile/run the independent diagnostic counter and timeout contract."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FrameSyncDiagnosticsTest(unittest.TestCase):
    def test_bounded_concurrent_counters(self):
        compiler = shutil.which('c++')
        if not compiler:
            self.skipTest('C++ compiler unavailable')
        with tempfile.TemporaryDirectory(prefix='frame-sync-test-') as directory:
            binary = Path(directory) / 'diagnostics-test'
            subprocess.run([compiler, '-std=c++17', '-O2', '-pthread', '-Wall', '-Wextra', '-Werror',
                            '-I', str(ROOT), str(ROOT / 'tests/frame_sync_diagnostics_test.cpp'),
                            '-o', str(binary)], check=True, capture_output=True, text=True)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=15)


if __name__ == '__main__':
    unittest.main()
