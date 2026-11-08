"""Prove the row-span optimization preserves the upstream map byte for byte."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'third_party/vrchat-dfr-gaze'


class MapOptimization(unittest.TestCase):
    def test_discrete_output_matches_upstream_for_stereo_edges_and_random_gaze(self):
        compiler = shutil.which('clang++') or shutil.which('c++')
        self.assertIsNotNone(compiler)
        source = r'''
#include <cassert>
#include <cmath>
#include <cstring>
#include <limits>
#include <random>
#define dxvk reference_dxvk
#include "upstream.h"
#undef dxvk
#include "src/dxvk/dxvk_gaze_map.h"

static void check(const dxvk::GazeMapRequest &r) {
    reference_dxvk::GazeMapRequest ref;
    ref.shape=r.shape; ref.texel=r.texel; ref.fx=r.fx; ref.fy=r.fy;
    ref.center=r.center; ref.dotCenter=r.dotCenter; ref.flags=r.flags;
    ref.coarse=r.coarse; ref.r1=r.r1; ref.r2=r.r2;
    ref.fullArea=r.fullArea; ref.midArea=r.midArea;
    reference_dxvk::GazeMap expected;
    dxvk::GazeMap actual;
    reference_dxvk::generateGazeMap(ref, expected);
    dxvk::generateGazeMap(r, actual);
    assert(actual.valid == expected.valid && actual.data == expected.data);
    std::fill(actual.data.begin(),actual.data.end(),255);
    dxvk::generateGazeMap(r,actual);
    assert(actual.data == expected.data); // Reused storage and padding too.
}
int main() {
    std::mt19937 random(64892);
    std::uniform_real_distribution<float> gaze(-.2f,1.2f);
    for (unsigned i=0;i<1800;i++) {
        dxvk::GazeMapRequest r;
        r.shape={1+random()%240,1+random()%180,1+random()%2,97,1};
        r.texel={1+random()%16,1+random()%16};
        r.fx={gaze(random),gaze(random)};r.fy={gaze(random),gaze(random)};
        r.coarse=random()%12;r.r1=gaze(random);r.r2=gaze(random);
        if(i%3) {r.fullArea=float(random()%101);r.midArea=float(random()%101);}
        check(r);
    }
    for (auto layers:{1u,2u}) for(auto coarse:{0u,1u,5u,6u,9u,10u})
        for(float x:{0.f,.5f,1.f}) for(float y:{0.f,.5f,1.f}) {
            dxvk::GazeMapRequest r;
            r.shape={layers==1?2880u:1440u,1440,layers,97,2};r.texel={8,8};
            r.fx={x,1-x};r.fy={y,1-y};r.fullArea=70;r.midArea=85;r.coarse=coarse;
            check(r);
        }
    dxvk::GazeMapRequest r;
    r.shape={160,80,1,97,1};r.texel={8,8};r.fx={.5f,.5f};r.fy={.5f,.5f};
    const float boundary=std::hypot(4.f-40.f,4.f-40.f)/80.f;
    for(float radius:{boundary,std::nextafter(boundary,0.f),std::nextafter(boundary,1.f),-1.f,0.f}) {
        r.r1=radius;r.r2=radius;check(r);
    }
    r.r1=std::numeric_limits<float>::quiet_NaN();check(r);
    r.r1=.2f;r.fx[0]=std::numeric_limits<float>::infinity();check(r);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'src/dxvk/dxvk_gaze_map.h'
            target.parent.mkdir(parents=True)
            shutil.copy2(VENDOR / 'gaze_map_upstream.h', target)
            shutil.copy2(VENDOR / 'gaze_map_upstream.h', root / 'upstream.h')
            subprocess.run(['git', 'apply', str(VENDOR / 'dxvk-gaze-map-row-spans.patch')],
                           cwd=root, check=True, capture_output=True)
            (root / 'compare.cpp').write_text(source)
            subprocess.run([compiler, '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread',
                            '-I'+str(root), str(root/'compare.cpp'), '-o', str(root/'compare')],
                           check=True, capture_output=True)
            subprocess.run([str(root/'compare')], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
