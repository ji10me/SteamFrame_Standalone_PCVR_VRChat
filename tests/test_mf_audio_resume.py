"""Fault tests against the actual patched Wine SAR rate callback.

Supply FRAME_WINE_SAR_SOURCE or use the private pinned research download.
The normal project test run skips when that upstream source is absent.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = '719bb8030ee6b5a784cecfc4ec0001bb8bf25acc2cdf07c8b9fc5fc1616ae37a'


class AudioResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(os.environ.get('FRAME_WINE_SAR_SOURCE', ROOT/
                      'local/media-audio-recovery-20261107/wine-mf-source/dlls/mf/sar.c'))
        if not source.is_file() or not shutil.which('cc') or not shutil.which('patch'):
            raise unittest.SkipTest('pinned Wine SAR source, cc and patch required')
        if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA:
            raise RuntimeError('unexpected upstream SAR source')
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        target = root/'dlls/mf/sar.c'
        target.parent.mkdir(parents=True)
        target.write_bytes(source.read_bytes())
        if os.environ.get('FRAME_WINE_TEST_BASELINE') != '1':
            subprocess.run(['patch', '--batch', '-p1', '-i', str(ROOT/
                           'patches/wine-mf-audio-rate-resume/rate-resume.patch')],
                           cwd=root, capture_output=True, text=True, check=True)
        text = target.read_text()
        start = text.index('static HRESULT WINAPI audio_renderer_clock_sink_OnClockSetRate(')
        callback = text[start:text.index('\n}', start)+2]
        harness = r'''
#include <assert.h>
#include <string.h>
typedef int HRESULT;
typedef long long MFTIME;
typedef struct { int unused; } IMFClockStateSink;
struct audio_renderer { IMFClockStateSink iface; int cs, flags, clock_state;
    float rate; void *audio_client, *stream_event_queue; };
enum { S_OK=0, E_FAIL=-1, AUDCLNT_E_NOT_STOPPED=-2, MF_E_UNSUPPORTED_RATE=-3,
       MFCLOCK_STATE_RUNNING=1, MFCLOCK_STATE_PAUSED=3, SAR_PREROLLED=2 };
#define SUCCEEDED(x) ((x)>=0)
#define WINAPI
static int starts, start_result, prerolls, requests, events, lock_depth;
static struct audio_renderer *impl_from_IMFClockStateSink(IMFClockStateSink *p) {
    return (struct audio_renderer *)p;
}
#define TRACE(...) ((void)systime)
#define WARN(...) ((void)0)
#define EnterCriticalSection(x) ((void)(x),++lock_depth)
#define LeaveCriticalSection(x) ((void)(x),--lock_depth)
static __attribute__((unused)) HRESULT IAudioClient_Start(void *p) { assert(p); ++starts; return start_result; }
static __attribute__((unused)) void audio_renderer_preroll(struct audio_renderer *p) {
    ++prerolls;
    if (!(p->flags&SAR_PREROLLED)) { p->flags|=SAR_PREROLLED; requests+=2; }
}
#define IMFMediaEventQueue_QueueEventParamVar(...) ((void)++events)
'''+callback+r'''
int main(int argc, char **argv) {
    assert(argc==2);
    struct audio_renderer r={.clock_state=MFCLOCK_STATE_RUNNING,.rate=0,.audio_client=(void *)1};
    if (!strcmp(argv[1],"resume")) {
        assert(audio_renderer_clock_sink_OnClockSetRate(&r.iface,0,1)==S_OK);
        assert(r.rate==1 && starts==1 && prerolls==1 && requests==2 && events==1);
    } else if (!strcmp(argv[1],"failure-retry")) {
        start_result=E_FAIL;
        assert(audio_renderer_clock_sink_OnClockSetRate(&r.iface,0,1)==E_FAIL);
        assert(r.rate==0 && starts==1 && requests==0 && events==0);
        start_result=S_OK;
        assert(audio_renderer_clock_sink_OnClockSetRate(&r.iface,0,1)==S_OK);
        assert(r.rate==1 && starts==2 && requests==2 && events==1);
    } else if (!strcmp(argv[1],"already-started")) {
        r.flags=SAR_PREROLLED; start_result=AUDCLNT_E_NOT_STOPPED;
        assert(audio_renderer_clock_sink_OnClockSetRate(&r.iface,0,1)==S_OK);
        assert(starts==1 && prerolls==1 && requests==0 && events==1);
    } else if (!strcmp(argv[1],"paused")) {
        r.clock_state=MFCLOCK_STATE_PAUSED;
        assert(audio_renderer_clock_sink_OnClockSetRate(&r.iface,0,1)==S_OK);
        assert(r.rate==1 && starts==0 && requests==0 && events==1);
    } else if (!strcmp(argv[1],"unchanged")) {
        r.rate=1;
        assert(audio_renderer_clock_sink_OnClockSetRate(&r.iface,0,1)==S_OK);
        assert(starts==0 && prerolls==0 && events==1);
    } else if (!strcmp(argv[1],"rate-zero")) {
        r.rate=1;
        assert(audio_renderer_clock_sink_OnClockSetRate(&r.iface,0,0)==S_OK);
        assert(r.rate==0 && starts==0 && prerolls==0 && events==1);
    } else if (!strcmp(argv[1],"unsupported")) {
        assert(audio_renderer_clock_sink_OnClockSetRate(&r.iface,0,2)==MF_E_UNSUPPORTED_RATE);
        assert(r.rate==0 && starts==0 && requests==0 && events==0);
    } else return 2;
    assert(lock_depth==0);
    return 0;
}
'''
        c = root/'test.c'; c.write_text(harness)
        cls.exe = root/'test'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(c),
                        '-o', str(cls.exe)], capture_output=True, text=True, check=True)

    def case(self, name):
        result = subprocess.run([str(self.exe), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_resume_starts_client_and_preroll(self):
        self.case('resume')

    def test_failed_start_is_retryable(self):
        self.case('failure-retry')

    def test_already_started_is_benign_without_duplicate_requests(self):
        self.case('already-started')

    def test_paused_clock_waits_for_clock_restart(self):
        self.case('paused')

    def test_unchanged_rate_preserves_behavior(self):
        self.case('unchanged')

    def test_rate_zero_preserves_behavior(self):
        self.case('rate-zero')

    def test_unsupported_rate_preserves_state(self):
        self.case('unsupported')


if __name__ == '__main__':
    unittest.main()
