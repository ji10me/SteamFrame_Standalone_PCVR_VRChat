"""Exercise actual C++ NDC conversion and its published GAZ2 wire packet."""
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Projection(unittest.TestCase):
    def test_projection_rejects_invalid_eyes_and_publishes_the_expected_wire_layout(self):
        compiler = shutil.which('clang++') or shutil.which('c++')
        self.assertIsNotNone(compiler, 'A C++ compiler is required for the gaze bridge test')
        source = r'''
#include "frame_gaze_projection.h"
#include <cassert>
#include <cstdio>
#include <limits>
int main() {
    float l[2] = {-1, -1}, r[2] = {1, 1};
    auto sample = frame_gaze::from_ndc(true, l, r);
    assert(sample.valid && sample.u[0] == 0 && sample.v[0] == 1);
    assert(sample.u[1] == 1 && sample.v[1] == 0);
    const auto flipped = frame_gaze::from_ndc(true, l, r, true);
    assert(flipped.valid && flipped.u[0] == 0 && flipped.u[1] == 1);
    assert(flipped.v[0] == 0 && flipped.v[1] == 1);
    l[0] = l[1] = r[0] = r[1] = 0;
    sample = frame_gaze::from_ndc(true, l, r);
    assert(sample.valid && sample.u[0] == .5f && sample.v[1] == .5f);
    frame_gaze::Calibration correction{1.1f, 1.1f, .015f, 0};
    auto adjusted = frame_gaze::calibrated(sample, correction);
    assert(adjusted.valid && std::abs(adjusted.u[0] - .515f) < .00001f && adjusted.v[0] == .5f);
    sample.u[0] = 0; sample.u[1] = 1; sample.v[0] = .25f; sample.v[1] = .75f;
    adjusted = frame_gaze::calibrated(sample, correction);
    assert(adjusted.u[0] == 0 && adjusted.u[1] == 1);
    assert(std::abs(adjusted.v[0] - .225f) < .00001f && std::abs(adjusted.v[1] - .775f) < .00001f);
    assert(!frame_gaze::calibrated({}, correction).valid);
    correction.gain_x = std::numeric_limits<float>::quiet_NaN();
    adjusted = frame_gaze::calibrated(sample, correction);
    assert(!adjusted.valid && adjusted.u[0] == .5f && adjusted.v[1] == .5f);
    sample = frame_gaze::from_ndc(true, l, r);
    assert(!frame_gaze::from_ndc(false, l, r).valid);
    for (float bad : {1.01f, -1.01f, std::numeric_limits<float>::infinity(),
                      std::numeric_limits<float>::quiet_NaN()}) {
        r[1] = bad;
        const auto rejected = frame_gaze::from_ndc(true, l, r);
        assert(!rejected.valid && rejected.u[0] == .5f && rejected.v[1] == .5f);
        const auto rejected_flipped = frame_gaze::from_ndc(true, l, r, true);
        assert(!rejected_flipped.valid && rejected_flipped.v[0] == .5f && rejected_flipped.v[1] == .5f);
    }
    r[1] = 0;
    gaze_bridge::SharedGaze packet{};
    gaze_bridge::publish(&packet, frame_gaze::from_ndc(true, l, r));
    assert(std::fwrite(&packet, 1, sizeof(packet), stdout) == 64);
    // Normal shutdown must erase the last eye values, not only its valid flag.
    gaze_bridge::publish(&packet, {});
    assert(std::fwrite(&packet, 1, sizeof(packet), stdout) == 64);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            cpp = Path(directory) / 'projection.cpp'
            executable = Path(directory) / 'projection'
            cpp.write_text(source)
            subprocess.run([compiler, '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                            '-I' + str(ROOT / 'tools'), '-I' + str(ROOT / 'third_party/vrchat-dfr-gaze'),
                            str(cpp), '-o', str(executable)], check=True, capture_output=True)
            packets = subprocess.check_output([str(executable)])
        self.assertEqual(len(packets), 128)
        live, stopped = (struct.unpack_from('<16I', packets, offset) for offset in (0, 64))
        self.assertEqual(live[:5], (0x325A4147, 1, 64, 2, 1))
        self.assertEqual(stopped[:5], (0x325A4147, 1, 64, 4, 0))
        self.assertEqual(stopped[5:9], (0x3F000000,) * 4)
        self.assertEqual(stopped[9:], (0,) * 7)


if __name__ == '__main__':
    unittest.main()
