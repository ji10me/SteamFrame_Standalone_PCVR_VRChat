"""Verify state polling rate and transition behavior independently of OpenVR."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GazePollTest(unittest.TestCase):
    def test_refresh_rate_and_loading_transitions(self):
        compiler = shutil.which('c++')
        if not compiler:
            self.skipTest('C++ compiler unavailable')
        source = r'''
#include "frame_gaze_poll.h"
#include <cassert>
using namespace std::chrono;
using Gate = frame_gaze::ScenePoll;
static Gate::TimePoint at(int ms) { return Gate::TimePoint{} + milliseconds(ms); }
int main() {
    Gate gate;
    assert(!gate.ready() && gate.due(at(0)));
    unsigned checks=0, eyes=0;
    for(int ms=0;ms<1000;ms+=10) {
        if(gate.due(at(ms))) { ++checks;gate.update(at(ms),true); }
        if(gate.ready()) ++eyes;
    }
    assert(checks==10 && eyes==100);
    assert(3*checks+eyes==130); // Steady-state API budget, not measured IPC.
    Gate transitions;
    transitions.update(at(0),true);
    assert(!transitions.due(at(99)) && transitions.due(at(100)));
    transitions.update(at(100),false);
    assert(!transitions.ready()); // Loading suppresses eye queries immediately on observation.
    assert(!transitions.due(at(199)) && transitions.due(at(200)));
    transitions.update(at(200),true);
    assert(transitions.ready());
    transitions.update(at(1500),false);
    assert(!transitions.due(at(1501))); // A delayed poll does not cause a catch-up burst.
    assert(transitions.due(at(1600)));
}
'''
        with tempfile.TemporaryDirectory(prefix='gaze-poll-') as directory:
            cpp = Path(directory)/'test.cpp'
            exe = Path(directory)/'test'
            cpp.write_text(source)
            subprocess.run([compiler, '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                            '-I'+str(ROOT/'tools'), str(cpp), '-o', str(exe)],
                           check=True, capture_output=True, text=True)
            subprocess.run([str(exe)], check=True, timeout=10)


if __name__ == '__main__':
    unittest.main()
