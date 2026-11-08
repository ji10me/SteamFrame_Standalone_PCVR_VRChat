/* Bounded standalone GStreamer decoder probe for synthetic video fixtures.
 * Usage: gstreamer_hw_probe TRUSTED_PLUGIN VIDEO hardware|software|auto
 * Requires GStreamer development headers/libraries. Never attaches to a game.
 * Reports the selected decoder and actual output frame count; EOS alone is
 * insufficient to establish correct decoding. Modes hardware/software use H.264.
 */
#include <gst/gst.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/ioctl.h>
#include <linux/videodev2.h>

static GstElement *hardware_decoder;

static void count_frame(GstElement *identity, GstBuffer *buffer, gpointer data) {
    (void)identity; (void)buffer;
    g_atomic_int_inc((gint *)data);
}
static void child_added(GstBin *parent, GstBin *bin, GstElement *element, gpointer unused) {
    (void)parent; (void)bin; (void)unused;
    GstElementFactory *factory = gst_element_get_factory(element);
    if (!factory) return;
    const gchar *kind = gst_element_factory_get_klass(factory);
    if (kind && strstr(kind, "Decoder")) {
        printf("DECODER=%s\n", gst_plugin_feature_get_name(GST_PLUGIN_FEATURE(factory)));
        fflush(stdout);
    }
    if (!strcmp(gst_plugin_feature_get_name(GST_PLUGIN_FEATURE(factory)),"v4l2h264dec")) {
        if (hardware_decoder) gst_object_unref(hardware_decoder);
        hardware_decoder=gst_object_ref(element);
    }
}
static void decoded_pad(GstElement *decoder, GstPad *pad, gpointer target) {
    (void)decoder;
    GstPad *sink = gst_element_get_static_pad(GST_ELEMENT(target),"sink");
    if (!gst_pad_is_linked(sink)) {
        GstPadLinkReturn result = gst_pad_link(pad,sink);
        printf("DECODED_PAD_LINK=%d\n",result);fflush(stdout);
    }
    gst_object_unref(sink);
}
int main(int argc, char **argv) {
    GError *error = NULL;
    const char *format = getenv("GST_PROBE_FORMAT");
    if (format && strcmp(format,"BGRx") && strcmp(format,"BGRA") && strcmp(format,"NV12")) return 2;
    const char *cycle_env = getenv("GST_PROBE_CYCLES");
    unsigned cycles = 1;
    if (cycle_env) {
        char *end = NULL;
        unsigned long value = strtoul(cycle_env, &end, 10);
        if (!*cycle_env || *end || value < 1 || value > 3) return 2;
        cycles = (unsigned)value;
    }
    if (argc != 4 || (strcmp(argv[3],"auto") && strcmp(argv[3],"software") && strcmp(argv[3],"hardware"))) return 2;
    if (!gst_init_check(NULL, NULL, &error)) {
        fprintf(stderr, "GST_INIT: %s\n", error ? error->message : "failed"); return 3;
    }
    printf("GST_VERSION=%s\n", gst_version_string()); fflush(stdout);
    GstPlugin *plugin = gst_plugin_load_file(argv[1], &error);
    if (!plugin) { fprintf(stderr, "PLUGIN_LOAD: %s\n", error ? error->message : "failed"); return 4; }
    const gchar *decoder_name = !strcmp(argv[3],"auto") ? "decodebin" : !strcmp(argv[3],"software") ? "avdec_h264" : "v4l2h264dec";
    GstElement *pipeline = gst_pipeline_new(NULL);
    g_signal_connect(pipeline,"deep-element-added",G_CALLBACK(child_added),NULL);
    GstElement *source = gst_element_factory_make("filesrc", NULL);
    GstElement *parser = gst_element_factory_make("h264parse", NULL);
    GstElement *decoder = gst_element_factory_make(decoder_name, NULL);
    GstElement *counter = gst_element_factory_make("identity", "frames");
    GstElement *sink = gst_element_factory_make("fakesink", NULL);
    GstElement *convert = format ? gst_element_factory_make("videoconvert", NULL) : NULL;
    GstElement *filter = format ? gst_element_factory_make("capsfilter", NULL) : NULL;
    if (!source || !parser || !decoder || !counter || !sink) return 5;
    if (format && (!convert || !filter)) return 5;
    g_object_set(source,"location",argv[2],NULL);
    g_object_set(counter,"signal-handoffs",TRUE,NULL);
    g_object_set(sink,"sync",FALSE,NULL);
    gst_bin_add_many(GST_BIN(pipeline),source,parser,decoder,counter,sink,NULL);
    GstElement *target = counter;
    if (format) {
        GstCaps *caps=gst_caps_new_simple("video/x-raw","format",G_TYPE_STRING,format,NULL);
        g_object_set(filter,"caps",caps,NULL);gst_caps_unref(caps);
        gst_bin_add_many(GST_BIN(pipeline),convert,filter,NULL);
        if (!gst_element_link_many(convert,filter,counter,NULL)) return 6;
        target=convert;
        printf("OUTPUT_FORMAT=%s\n",format);fflush(stdout);
    }
    if (!strcmp(argv[3],"auto")) {
        if (!gst_element_link(source,decoder)) return 6;
    } else if (!gst_element_link_many(source,parser,decoder,NULL)) return 6;
    if (!gst_element_link(counter,sink)) return 6;
    if (!strcmp(argv[3],"auto")) g_signal_connect(decoder,"pad-added",G_CALLBACK(decoded_pad),target);
    else if (!gst_element_link(decoder,target)) return 7;
    gint frames = 0;
    g_signal_connect(counter,"handoff",G_CALLBACK(count_frame),&frames);
    GstBus *bus = gst_element_get_bus(pipeline);
    int result = 9;
    if (gst_element_set_state(pipeline,GST_STATE_PLAYING) != GST_STATE_CHANGE_FAILURE) {
      for (unsigned cycle = 0; cycle < cycles; cycle++) {
        if (cycle) {
            const char *restart=getenv("GST_PROBE_RESTART_DECODER");
            if (restart && !strcmp(restart,"1")) {
                int fd=-1;
                struct v4l2_decoder_cmd command={.cmd=V4L2_DEC_CMD_START};
                if (!hardware_decoder || !g_object_class_find_property(
                        G_OBJECT_GET_CLASS(hardware_decoder),"device-fd")) { result=11;break; }
                g_object_get(hardware_decoder,"device-fd",&fd,NULL);
                int status=fd>=0 ? ioctl(fd,VIDIOC_DECODER_CMD,&command) : -1;
                printf("DECODER_START=%d ERRNO=%d\n",status,status<0 ? errno : 0);fflush(stdout);
                if (status<0) { result=11;break; }
            }
            g_atomic_int_set(&frames,0);
            puts("STAGE=SEEK");fflush(stdout);
            if (!gst_element_seek_simple(pipeline,GST_FORMAT_TIME,
                    GST_SEEK_FLAG_FLUSH|GST_SEEK_FLAG_KEY_UNIT,0)) { result=10;break; }
        }
        GstMessage *message = gst_bus_timed_pop_filtered(bus,5 * GST_SECOND,GST_MESSAGE_EOS|GST_MESSAGE_ERROR);
        if (message && GST_MESSAGE_TYPE(message)==GST_MESSAGE_EOS) { puts("DECODE_EOS"); result=0; }
        else if (message && GST_MESSAGE_TYPE(message)==GST_MESSAGE_ERROR) {
            gchar *debug=NULL;gst_message_parse_error(message,&error,&debug);
            fprintf(stderr,"DECODE_ERROR: %s\nDETAIL: %s\n",error ? error->message : "failed",debug ? debug : "none");
            g_free(debug);g_clear_error(&error);
            result=9;
        }
        else result=9;
        printf("CYCLE=%u FRAMES=%d\n",cycle,g_atomic_int_get(&frames));fflush(stdout);
        if (message) gst_message_unref(message);
        if (result) break;
      }
    }
    gst_element_set_state(pipeline,GST_STATE_NULL);
    printf("DECODED_FRAMES=%d\n",g_atomic_int_get(&frames));
    gst_object_unref(bus);gst_object_unref(pipeline);gst_object_unref(plugin);
    if (hardware_decoder) gst_object_unref(hardware_decoder);
    return result;
}
