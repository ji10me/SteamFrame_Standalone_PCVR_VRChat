/* Independent Wine/MF audio-renderer recovery probe, using synthetic silence.
 * No game hooks, network, capture, or participant audio. Use a private Prefix
 * and an external timeout. Modes: normal, rate-resume, starve, activate.
 */
#define COBJMACROS
#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mferror.h>
#include <stdio.h>
#include <string.h>
#ifndef PRESENTATION_CURRENT_POSITION
#define PRESENTATION_CURRENT_POSITION ((LONGLONG)0x7fffffffffffffffLL)
#endif

static int error(const char *stage, HRESULT hr)
{
    if (SUCCEEDED(hr)) return 0;
    fprintf(stderr, "MF_ERROR=%s HR=%08lx\n", stage, (unsigned long)hr);
    return 1;
}

static HRESULT requests(IMFStreamSink *stream, unsigned *count)
{
    *count = 0;
    for (unsigned i = 0; i < 100; ++i) {
        IMFMediaEvent *event = NULL;
        MediaEventType kind;
        HRESULT hr = IMFStreamSink_GetEvent(stream, MF_EVENT_FLAG_NO_WAIT, &event);
        if (hr == MF_E_NO_EVENTS_AVAILABLE) return S_OK;
        if (FAILED(hr)) return hr;
        hr = IMFMediaEvent_GetType(event, &kind);
        IMFMediaEvent_Release(event);
        if (FAILED(hr)) return hr;
        if (kind == MEStreamSinkRequestSample) ++*count;
    }
    return E_UNEXPECTED;
}

static HRESULT silence(IMFStreamSink *stream, LONGLONG pts)
{
    IMFSample *sample = NULL;
    IMFMediaBuffer *buffer = NULL;
    BYTE *bytes = NULL;
    const DWORD size = 7680; /* 20 ms, stereo 48kHz float32. */
    HRESULT hr = MFCreateSample(&sample);
    if (FAILED(hr)) return hr;
    if (FAILED(hr = MFCreateMemoryBuffer(size, &buffer))) goto done;
    if (FAILED(hr = IMFMediaBuffer_Lock(buffer, &bytes, NULL, NULL))) goto done;
    memset(bytes, 0, size);
    hr = IMFMediaBuffer_Unlock(buffer);
    if (FAILED(hr)) goto done;
    if (FAILED(hr = IMFMediaBuffer_SetCurrentLength(buffer, size))) goto done;
    if (FAILED(hr = IMFSample_AddBuffer(sample, buffer))) goto done;
    if (FAILED(hr = IMFSample_SetSampleTime(sample, pts))) goto done;
    if (FAILED(hr = IMFSample_SetSampleDuration(sample, 200000))) goto done;
    hr = IMFStreamSink_ProcessSample(stream, sample);
done:
    if (buffer) IMFMediaBuffer_Release(buffer);
    IMFSample_Release(sample);
    return hr;
}

int main(int argc, char **argv)
{
    if (argc != 2 || (strcmp(argv[1], "normal") && strcmp(argv[1], "rate-resume") && strcmp(argv[1], "starve") && strcmp(argv[1], "activate"))) return 2;
    if (error("COM", CoInitializeEx(NULL, COINIT_MULTITHREADED))) return 3;
    if (error("STARTUP", MFStartup(MF_VERSION, MFSTARTUP_FULL))) { CoUninitialize(); return 4; }
    IMFMediaSink *sink = NULL;
    IMFStreamSink *stream = NULL;
    IMFMediaTypeHandler *handler = NULL;
    IMFMediaType *type = NULL;
    IMFClockStateSink *clock = NULL;
    IMFActivate *activation = NULL;
    IMFSimpleAudioVolume *volume = NULL;
    IMFAudioStreamVolume *channels = NULL;
    int result = 5;
    unsigned before = 0, after = 0;
    if (!strcmp(argv[1], "activate")) {
        if (error("CREATE_ACTIVATION", MFCreateAudioRendererActivate(&activation))) goto done;
        if (error("ACTIVATE", IMFActivate_ActivateObject(activation, &IID_IMFMediaSink, (void **)&sink))) goto done;
    } else if (error("CREATE", MFCreateAudioRenderer(NULL, &sink))) goto done;
    if (error("STREAM", IMFMediaSink_GetStreamSinkByIndex(sink, 0, &stream))) goto done;
    if (error("HANDLER", IMFStreamSink_GetMediaTypeHandler(stream, &handler))) goto done;
    if (error("TYPE", MFCreateMediaType(&type))) goto done;
    if (error("MAJOR", IMFMediaType_SetGUID(type, &MF_MT_MAJOR_TYPE, &MFMediaType_Audio))) goto done;
    if (error("SUBTYPE", IMFMediaType_SetGUID(type, &MF_MT_SUBTYPE, &MFAudioFormat_Float))) goto done;
    if (error("CHANNELS", IMFMediaType_SetUINT32(type, &MF_MT_AUDIO_NUM_CHANNELS, 2))) goto done;
    if (error("RATE", IMFMediaType_SetUINT32(type, &MF_MT_AUDIO_SAMPLES_PER_SECOND, 48000))) goto done;
    if (error("BITS", IMFMediaType_SetUINT32(type, &MF_MT_AUDIO_BITS_PER_SAMPLE, 32))) goto done;
    if (error("BLOCK", IMFMediaType_SetUINT32(type, &MF_MT_AUDIO_BLOCK_ALIGNMENT, 8))) goto done;
    if (error("BPS", IMFMediaType_SetUINT32(type, &MF_MT_AUDIO_AVG_BYTES_PER_SECOND, 384000))) goto done;
    if (error("CLOCK", IMFMediaSink_QueryInterface(sink, &IID_IMFClockStateSink, (void **)&clock))) goto done;
    if (!strcmp(argv[1], "rate-resume")) {
        if (error("RATE_ZERO", IMFClockStateSink_OnClockSetRate(clock, 0, 0.0f))) goto done;
    }
    /* Set rate before the media type: setting the type may preroll already. */
    if (error("SET_TYPE", IMFMediaTypeHandler_SetCurrentMediaType(handler, type))) goto done;
    if (error("VOLUME_SERVICE", MFGetService((IUnknown *)sink, &MR_POLICY_VOLUME_SERVICE,
            &IID_IMFSimpleAudioVolume, (void **)&volume))) goto done;
    if (error("STREAM_VOLUME_SERVICE", MFGetService((IUnknown *)sink, &MR_STREAM_VOLUME_SERVICE,
            &IID_IMFAudioStreamVolume, (void **)&channels))) goto done;
    float level = -1.0f;
    UINT32 channel_count = 0;
    if (error("VOLUME_READ", IMFSimpleAudioVolume_GetMasterVolume(volume, &level))) goto done;
    if (error("CHANNEL_COUNT", IMFAudioStreamVolume_GetChannelCount(channels, &channel_count))) goto done;
    if (level < 0.0f || level > 1.0f || channel_count != 2) goto done;
    if (error("CLOCK_START", IMFClockStateSink_OnClockStart(clock, 0, PRESENTATION_CURRENT_POSITION))) goto done;
    Sleep(150);
    if (error("INITIAL_EVENTS", requests(stream, &before))) goto done;
    if (!strcmp(argv[1], "rate-resume")) {
        if (error("RATE_ONE", IMFClockStateSink_OnClockSetRate(clock, 0, 1.0f))) goto done;
        Sleep(300);
        if (error("RESUME_EVENTS", requests(stream, &after))) goto done;
        result = after >= 2 ? 0 : 10;
    } else if (!strcmp(argv[1], "starve")) {
        if (before < 2) { result = 11; goto report; }
        if (error("SILENCE_1", silence(stream, 0)) || error("SILENCE_2", silence(stream, 200000))) goto done;
        Sleep(1800); /* Deliberately stop delivering samples, without memory load. */
        unsigned pending = 0;
        if (error("STARVE_EVENTS", requests(stream, &pending))) goto done;
        if (error("SILENCE_RESUME", silence(stream, 18000000))) goto done;
        Sleep(300);
        if (error("RECOVERY_EVENTS", requests(stream, &after))) goto done;
        printf("PENDING_REQUESTS=%u\n", pending);
        result = after > 0 ? 0 : 12;
    } else {
        result = before >= 2 ? 0 : 11;
    }
report:
    printf("MODE=%s INITIAL_REQUESTS=%u RESUMED_REQUESTS=%u RESULT=%d\n", argv[1], before, after, result);
    fflush(stdout);
done:
    if (clock) { IMFClockStateSink_OnClockStop(clock, 0); IMFClockStateSink_Release(clock); }
    if (volume) IMFSimpleAudioVolume_Release(volume);
    if (channels) IMFAudioStreamVolume_Release(channels);
    if (sink) IMFMediaSink_Shutdown(sink);
    if (type) IMFMediaType_Release(type);
    if (handler) IMFMediaTypeHandler_Release(handler);
    if (stream) IMFStreamSink_Release(stream);
    if (sink) IMFMediaSink_Release(sink);
    if (activation) { IMFActivate_ShutdownObject(activation); IMFActivate_Release(activation); }
    MFShutdown();
    CoUninitialize();
    return result;
}
