"""Compile lifecycle callbacks from the patched, hash-pinned GStreamer source.

Set FRAME_GST_SOURCE_ARCHIVE to the upstream gst-plugins-good-1.22.0.tar.xz.
No decoder device, game process, or audio output is used by these fault tests.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SHA = '582e617271e7f314d1a2211e3e3856ae2e4303c8c0d6114e9c4a5ea5719294b0'


def function(source, signature):
    start = source.index(signature)
    end = source.index('\n}', start) + 2
    return source[start:end]


class IrisLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        archive = Path(os.environ.get('FRAME_GST_SOURCE_ARCHIVE',
                       ROOT/'local/video-hw-study/gst-plugins-good-1.22.0.tar.xz'))
        if not archive.is_file() or not shutil.which('cc') or not shutil.which('patch'):
            raise unittest.SkipTest('pinned GStreamer archive, cc and patch required')
        if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA:
            raise RuntimeError('untrusted GStreamer source archive')
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        with tarfile.open(archive) as t:
            for name in ['gstv4l2.c', 'gstv4l2videodec.c', 'gstv4l2videodec.h',
                         'gstv4l2object.c', 'gstv4l2object.h']:
                dest = work/'sys/v4l2'/name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(t.extractfile('gst-plugins-good-1.22.0/sys/v4l2/'+name).read())
        patches = ['frame-iris-compat.patch']
        # Explicit opt-in to demonstrate failure against the previous implementation.
        if os.environ.get('FRAME_GST_TEST_BASELINE') != '1':
            patches.append('lifecycle-errors.patch')
        for name in patches:
            subprocess.run(['patch', '--batch', '-p1', '-i', str(
                ROOT/'patches/gstreamer-iris-drain-restart'/name)], cwd=work,
                check=True, capture_output=True, text=True)
        source = (work/'sys/v4l2/gstv4l2videodec.c').read_text()
        flush = function(source, 'static gboolean\ngst_v4l2_video_dec_flush (')
        drain = function(source, 'static GstFlowReturn\ngst_v4l2_video_dec_drain (')
        set_format = function(source, 'static gboolean\ngst_v4l2_video_dec_set_format (')
        no_format = set_format[set_format.index('no_raw_format:')+len('no_raw_format:'):]
        harness = r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdlib.h>
#include <string.h>
typedef int gboolean;
typedef int GstFlowReturn;
typedef char gchar;
enum { FALSE=0, TRUE=1, GST_FLOW_OK=0, GST_FLOW_FLUSHING=-2,
       GST_FLOW_EOS=-3, GST_FLOW_NOT_NEGOTIATED=-4, GST_FLOW_ERROR=-5,
       GST_TASK_STARTED=1, V4L2_DEC_CMD_START=0 };
typedef struct { struct { char card[32], driver[32]; } vcap; } GstV4l2Object;
typedef struct { void *srcpad; } GstVideoDecoder;
typedef struct { GstVideoDecoder parent; GstV4l2Object *v4l2output, *v4l2capture;
                 int active, frame_restart_after_drain, output_flow; } GstV4l2VideoDec;
static int restart_ok=1, starts, unlock_stops, pool_flushes, posted_errors;
static int finish_result=GST_FLOW_OK;
#define GST_V4L2_VIDEO_DEC(x) ((GstV4l2VideoDec *)(x))
#define GST_DEBUG_OBJECT(x,...) ((void)(x))
#define GST_INFO_OBJECT(x,...) ((void)(x))
#define GST_ELEMENT_ERROR(...) (++posted_errors)
#define GST_VIDEO_DECODER_STREAM_UNLOCK(x) ((void)(x))
#define GST_VIDEO_DECODER_STREAM_LOCK(x) ((void)(x))
#define G_UNLIKELY(x) (x)
#define g_atomic_int_get(x) (*(x))
#define g_strcmp0 strcmp
#define g_getenv getenv
static int gst_pad_get_task_state(void *p) { (void)p; return 0; }
static void gst_pad_stop_task(void *p) { (void)p; }
static void gst_v4l2_object_unlock(GstV4l2Object *p) { (void)p; }
static void gst_v4l2_object_unlock_stop(GstV4l2Object *p) { (void)p; ++unlock_stops; }
static void gst_v4l2_buffer_pool_flush(GstV4l2Object *p) { (void)p; ++pool_flushes; }
static int gst_v4l2_decoder_cmd(GstV4l2Object *p, int cmd, int flags) {
  (void)p; (void)flags; assert(cmd==V4L2_DEC_CMD_START); ++starts; return restart_ok;
}
static GstFlowReturn gst_v4l2_video_dec_finish(GstVideoDecoder *p) {
  (void)p; return finish_result;
}
'''+flush+'\n'+drain+'\nstatic gboolean no_raw_format_result(void) {\n'+no_format+r'''
int main(int argc, char **argv) {
  assert(argc==2);
  GstV4l2Object out={.vcap={"iris_decoder","iris_driver"}}, capture={0};
  GstV4l2VideoDec self={.v4l2output=&out,.v4l2capture=&capture,.active=1,
                        .frame_restart_after_drain=1};
  GstVideoDecoder *decoder=(GstVideoDecoder *)&self;
  setenv("FRAME_V4L2_IRIS_RESTART","1",1);
  if (!strcmp(argv[1],"restart-failure")) {
    restart_ok=0;
    assert(gst_v4l2_video_dec_drain(decoder)==GST_FLOW_ERROR);
    assert(starts==1 && posted_errors==1 && unlock_stops==2 && pool_flushes==0);
    assert(self.frame_restart_after_drain && self.output_flow==GST_FLOW_ERROR);
    restart_ok=1;
    assert(gst_v4l2_video_dec_drain(decoder)==GST_FLOW_OK);
    assert(starts==2 && pool_flushes==2 && unlock_stops==4);
    assert(!self.frame_restart_after_drain && self.output_flow==GST_FLOW_OK);
  } else if (!strcmp(argv[1],"finish-error")) {
    finish_result=GST_FLOW_ERROR;
    assert(gst_v4l2_video_dec_drain(decoder)==GST_FLOW_ERROR);
    assert(starts==0 && pool_flushes==0);
  } else if (!strcmp(argv[1],"negotiation-error")) {
    finish_result=GST_FLOW_NOT_NEGOTIATED;
    assert(gst_v4l2_video_dec_drain(decoder)==GST_FLOW_NOT_NEGOTIATED);
    assert(starts==0 && pool_flushes==0);
  } else if (!strcmp(argv[1],"normal-drain")) {
    int normal[]={GST_FLOW_OK,GST_FLOW_EOS,GST_FLOW_FLUSHING};
    for (unsigned i=0;i<sizeof(normal)/sizeof(*normal);++i) {
      finish_result=normal[i]; self.frame_restart_after_drain=1;
      assert(gst_v4l2_video_dec_drain(decoder)==GST_FLOW_OK);
    }
    assert(starts==3 && pool_flushes==6);
  } else if (!strcmp(argv[1],"other-driver")) {
    strcpy(out.vcap.driver,"other");
    assert(gst_v4l2_video_dec_drain(decoder)==GST_FLOW_OK);
    assert(starts==0 && pool_flushes==2);
  } else if (!strcmp(argv[1],"unsupported-format")) {
    assert(no_raw_format_result()==FALSE);
  } else return 2;
  return 0;
}
'''
        c = work/'test.c'; c.write_text(harness)
        cls.exe = work/'test'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        str(c), '-o', str(cls.exe)], check=True, capture_output=True, text=True)

    def check_case(self, case):
        result = subprocess.run([str(self.exe), case], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_restart_failure_cleanup_and_explicit_retry(self):
        self.check_case('restart-failure')

    def test_finish_error_propagated(self):
        self.check_case('finish-error')

    def test_negotiation_error_propagated(self):
        self.check_case('negotiation-error')

    def test_normal_eos_and_flushing_remain_successful(self):
        self.check_case('normal-drain')

    def test_other_driver_not_restarted(self):
        self.check_case('other-driver')

    def test_unsupported_format_returns_boolean_false(self):
        self.check_case('unsupported-format')


if __name__ == '__main__':
    unittest.main()
