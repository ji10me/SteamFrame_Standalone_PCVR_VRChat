"""Regression, boundary and invalid-request checks for the Frame map kernel."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FrameFoveationMap(unittest.TestCase):
    def test_map_compatibility_and_memory_safety(self):
        compiler = shutil.which('clang++') or shutil.which('c++')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'map-test'
            subprocess.run([
                compiler, '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                '-I' + str(ROOT / 'tools'),
                '-I' + str(ROOT / 'third_party/vrchat-dfr-gaze'),
                str(ROOT / 'tests/frame_foveation_map_test.cpp'), '-o', str(binary),
            ], check=True, capture_output=True, text=True)
            run = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_dxvk_adapter_keeps_fields_and_invalid_fallback(self):
        compiler = shutil.which('clang++') or shutil.which('c++')
        self.assertIsNotNone(compiler)
        vendor = ROOT / 'third_party/vrchat-dfr-gaze'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            headers = root / 'src/dxvk'
            headers.mkdir(parents=True)
            shutil.copy2(vendor / 'gaze_map_upstream.h', headers / 'dxvk_gaze_map.h')
            shutil.copy2(ROOT / 'tools/frame_foveation_map.h', headers)
            subprocess.run(['git', 'apply', str(vendor / 'dxvk-frame-map-kernel.patch')],
                           cwd=root, check=True, capture_output=True)
            source = root / 'adapter.cpp'
            source.write_text(r'''
#include "dxvk_gaze_map.h"
#include <cassert>
int main() {
  for (auto layers : {1u,2u}) {
    dxvk::GazeMapRequest r;
    r.shape = {layers == 1 ? 2880u : 1440u,1440,layers,97,2};
    r.texel = {8,16}; r.fx = {.2f,.8f}; r.fy = {.3f,.7f};
    r.r1 = .15f; r.r2 = .6f; r.fullArea = 15; r.midArea = 70; r.coarse = 10;
    dxvk::GazeMap actual;
    dxvk::generateGazeMap(r,actual);
    frame_dfr::Focus f;
    f.u = r.fx; f.v = r.fy; f.inner_radius = .15f; f.outer_radius = .6f;
    f.inner_percent = 15; f.outer_percent = 70; f.peripheral_rate = 10;
    std::vector<uint8_t> expected;
    assert(frame_dfr::build_map({r.shape[0],1440,layers,8,16},f,expected));
    assert(actual.valid && actual.data == expected && actual.request.sameMap(r));
    r.texel[0] = 0;
    dxvk::generateGazeMap(r,actual);
    assert(!actual.valid && actual.data.empty());
  }
}
''')
            binary = root / 'adapter'
            subprocess.run([compiler, '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                            '-I' + str(headers), str(source), '-o', str(binary)],
                           check=True, capture_output=True, text=True)
            run = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
