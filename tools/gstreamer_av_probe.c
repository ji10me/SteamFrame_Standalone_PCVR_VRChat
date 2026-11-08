/* Standalone H.264/AAC fixture probe. No audio output, network, or game hooks.
 * Usage: gstreamer-av-probe TRUSTED_V4L2_PLUGIN LOCAL_MP4 EXPECTED_VIDEO_FRAMES
 * Use a 30fps, 48kHz stereo fixture. Decode to fakesinks, counting both streams
 * across EOS and two flush/seeks; require at least 95% of expected PCM bytes.
 * A caller must impose an external process/resource deadline as well.
 */
#include <gst/gst.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct Counts {
  GMutex lock;
  guint video_frames, audio_buffers;
  guint64 audio_bytes;
};
struct Links { GstElement *video, *audio; gint video_linked, audio_linked; };

static void video_buffer(GstElement *element, GstBuffer *buffer, gpointer opaque) {
  (void)element; (void)buffer;
  struct Counts *counts = opaque;
  g_mutex_lock(&counts->lock);
  ++counts->video_frames;
  g_mutex_unlock(&counts->lock);
}
static void audio_buffer(GstElement *element, GstBuffer *buffer, gpointer opaque) {
  (void)element;
  struct Counts *counts = opaque;
  g_mutex_lock(&counts->lock);
  ++counts->audio_buffers;
  counts->audio_bytes += gst_buffer_get_size(buffer);
  g_mutex_unlock(&counts->lock);
}
static void pad_added(GstElement *demuxer, GstPad *pad, gpointer opaque) {
  (void)demuxer;
  struct Links *links = opaque;
  GstCaps *caps = gst_pad_get_current_caps(pad);
  if (!caps) caps = gst_pad_query_caps(pad, NULL);
  if (!caps || gst_caps_is_empty(caps) || gst_caps_is_any(caps)) {
    if (caps) gst_caps_unref(caps);
    return;
  }
  const gchar *type = gst_structure_get_name(gst_caps_get_structure(caps, 0));
  gboolean video = !strcmp(type, "video/x-h264");
  gboolean audio = !strcmp(type, "audio/mpeg");
  if (video || audio) {
    GstPad *sink = gst_element_get_static_pad(video ? links->video : links->audio, "sink");
    if (!gst_pad_is_linked(sink)) {
      GstPadLinkReturn result = gst_pad_link(pad, sink);
      if (result == GST_PAD_LINK_OK) {
        if (video) g_atomic_int_set(&links->video_linked, TRUE);
        else g_atomic_int_set(&links->audio_linked, TRUE);
      }
      printf("PAD=%s RESULT=%d\n", video ? "video" : "audio", result);
      fflush(stdout);
    }
    gst_object_unref(sink);
  }
  gst_caps_unref(caps);
}

int main(int argc, char **argv) {
  if (argc != 4) return 2;
  char *end = NULL;
  unsigned long expected = strtoul(argv[3], &end, 10);
  if (!*argv[3] || !end || *end || expected < 1 || expected > 2000) return 2;
  GError *error = NULL;
  if (!gst_init_check(NULL, NULL, &error)) {
    g_clear_error(&error);
    return 3;
  }
  GstPlugin *plugin = gst_plugin_load_file(argv[1], &error);
  if (!plugin) { g_clear_error(&error); return 4; }
  GstElement *pipeline = gst_pipeline_new(NULL);
  GstElement *source = gst_element_factory_make("filesrc", NULL);
  GstElement *demux = gst_element_factory_make("qtdemux", NULL);
  GstElement *vq = gst_element_factory_make("queue", NULL);
  GstElement *vp = gst_element_factory_make("h264parse", NULL);
  GstElement *vd = gst_element_factory_make("v4l2h264dec", NULL);
  GstElement *vi = gst_element_factory_make("identity", NULL);
  GstElement *vs = gst_element_factory_make("fakesink", NULL);
  GstElement *aq = gst_element_factory_make("queue", NULL);
  GstElement *ap = gst_element_factory_make("aacparse", NULL);
  GstElement *ad = gst_element_factory_make("avdec_aac", NULL);
  GstElement *ac = gst_element_factory_make("audioconvert", NULL);
  GstElement *af = gst_element_factory_make("capsfilter", NULL);
  GstElement *ai = gst_element_factory_make("identity", NULL);
  GstElement *as = gst_element_factory_make("fakesink", NULL);
  if (!pipeline || !source || !demux || !vq || !vp || !vd || !vi || !vs || !aq || !ap || !ad || !ac || !af || !ai || !as) {
    fprintf(stderr, "Required fixture decoder element unavailable\n");
    return 5;
  }
  g_object_set(source, "location", argv[2], NULL);
  g_object_set(vs, "sync", FALSE, NULL);
  g_object_set(as, "sync", FALSE, NULL);
  g_object_set(vi, "signal-handoffs", TRUE, NULL);
  g_object_set(ai, "signal-handoffs", TRUE, NULL);
  GstCaps *audio_caps = gst_caps_new_simple("audio/x-raw", "format", G_TYPE_STRING, "S16LE",
      "layout", G_TYPE_STRING, "interleaved", "rate", G_TYPE_INT, 48000, "channels", G_TYPE_INT, 2, NULL);
  g_object_set(af, "caps", audio_caps, NULL);
  gst_caps_unref(audio_caps);
  gst_bin_add_many(GST_BIN(pipeline), source, demux, vq, vp, vd, vi, vs, aq, ap, ad, ac, af, ai, as, NULL);
  if (!gst_element_link(source, demux) || !gst_element_link_many(vq, vp, vd, vi, vs, NULL) ||
      !gst_element_link_many(aq, ap, ad, ac, af, ai, as, NULL)) return 6;
  struct Counts counts = {0};
  g_mutex_init(&counts.lock);
  struct Links links = {.video = vq, .audio = aq};
  g_signal_connect(demux, "pad-added", G_CALLBACK(pad_added), &links);
  g_signal_connect(vi, "handoff", G_CALLBACK(video_buffer), &counts);
  g_signal_connect(ai, "handoff", G_CALLBACK(audio_buffer), &counts);
  GstBus *bus = gst_element_get_bus(pipeline);
  int result = 0;
  if (gst_element_set_state(pipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE) result = 7;
  for (unsigned cycle = 0; !result && cycle < 3; ++cycle) {
    if (cycle) {
      g_mutex_lock(&counts.lock);
      counts.video_frames = counts.audio_buffers = 0;
      counts.audio_bytes = 0;
      g_mutex_unlock(&counts.lock);
      printf("SEEK_CYCLE=%u\n", cycle); fflush(stdout);
      if (!gst_element_seek_simple(pipeline, GST_FORMAT_TIME, GST_SEEK_FLAG_FLUSH | GST_SEEK_FLAG_KEY_UNIT, 0)) {
        result = 8; break;
      }
    }
    GstMessage *message = gst_bus_timed_pop_filtered(bus, 5 * GST_SECOND, GST_MESSAGE_EOS | GST_MESSAGE_ERROR);
    gboolean eos = message && GST_MESSAGE_TYPE(message) == GST_MESSAGE_EOS;
    if (message && GST_MESSAGE_TYPE(message) == GST_MESSAGE_ERROR) {
      GError *failure = NULL;
      gst_message_parse_error(message, &failure, NULL);
      fprintf(stderr, "FIXTURE_ERROR_DOMAIN=%u CODE=%d\n", failure ? failure->domain : 0, failure ? failure->code : 0);
      g_clear_error(&failure);
    }
    g_mutex_lock(&counts.lock);
    printf("CYCLE=%u VIDEO_FRAMES=%u AUDIO_BUFFERS=%u AUDIO_BYTES=%" G_GUINT64_FORMAT " EOS=%d\n",
           cycle, counts.video_frames, counts.audio_buffers, counts.audio_bytes, eos);
    guint64 minimum_audio_bytes = (guint64)expected * 6400 * 95 / 100;
    if (!eos || counts.video_frames != expected || !counts.audio_buffers || counts.audio_bytes < minimum_audio_bytes ||
        !g_atomic_int_get(&links.video_linked) || !g_atomic_int_get(&links.audio_linked)) result = 9;
    g_mutex_unlock(&counts.lock);
    fflush(stdout);
    if (message) gst_message_unref(message);
  }
  gst_element_set_state(pipeline, GST_STATE_NULL);
  gst_object_unref(bus);
  gst_object_unref(pipeline);
  gst_object_unref(plugin);
  g_mutex_clear(&counts.lock);
  return result;
}
