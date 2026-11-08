/* Standalone synchronous H.264 MFT probe using synthetic packet fixtures only.
 * Usage: mf_transform_probe.exe FIXTURE EXPECTED_FRAMES
 * Fixture: LE DWORD magic 0x36324846, width, height, packet count;
 * then repeated LE DWORD byte count + Annex-B access unit. No game attachment.
 * An external process/resource deadline is required.
 */
#define COBJMACROS
#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mftransform.h>
#include <mferror.h>
#include <wmcodecdsp.h>
#include <stdio.h>
#include <stdlib.h>

static HRESULT checked(const char *name,HRESULT hr) {
    if (FAILED(hr)) fprintf(stderr,"MF_ERROR %s 0x%08lx\n",name,(unsigned long)hr);
    return hr;
}
static void stage(const char *name) { printf("STAGE=%s\n",name);fflush(stdout); }
static HRESULT output_type(IMFTransform *transform) {
    for (DWORD i=0;i<64;i++) {
        IMFMediaType *type=NULL;GUID subtype;
        HRESULT hr=IMFTransform_GetOutputAvailableType(transform,0,i,&type);
        if (FAILED(hr)) return hr;
        hr=IMFMediaType_GetGUID(type,&MF_MT_SUBTYPE,&subtype);
        if (SUCCEEDED(hr) && IsEqualGUID(&subtype,&MFVideoFormat_NV12)) {
            hr=IMFTransform_SetOutputType(transform,0,type,0);
            IMFMediaType_Release(type);return hr;
        }
        IMFMediaType_Release(type);
    }
    return E_FAIL;
}
static HRESULT make_sample(DWORD size,IMFSample **sample,IMFMediaBuffer **buffer) {
    HRESULT hr=MFCreateSample(sample);
    if (FAILED(hr)) return hr;
    hr=MFCreateMemoryBuffer(size,buffer);
    if (SUCCEEDED(hr)) hr=IMFSample_AddBuffer(*sample,*buffer);
    return hr;
}
static HRESULT pull(IMFTransform *transform,unsigned *frames,DWORD *checksum) {
    for (unsigned attempt=0;attempt<2048;attempt++) {
        MFT_OUTPUT_STREAM_INFO info;MFT_OUTPUT_DATA_BUFFER output={0};
        IMFSample *allocated=NULL;IMFMediaBuffer *buffer=NULL;DWORD status=0;
        HRESULT hr=IMFTransform_GetOutputStreamInfo(transform,0,&info);
        if (FAILED(hr)) return hr;
        if (!(info.dwFlags&MFT_OUTPUT_STREAM_PROVIDES_SAMPLES)) {
            if (!info.cbSize || info.cbSize>32*1024*1024) return E_FAIL;
            hr=make_sample(info.cbSize,&allocated,&buffer);
            if (buffer) IMFMediaBuffer_Release(buffer);
            if (FAILED(hr)) { if (allocated) IMFSample_Release(allocated);return hr; }
            output.pSample=allocated;
        }
        hr=IMFTransform_ProcessOutput(transform,0,1,&output,&status);
        if (SUCCEEDED(hr) && output.pSample && !(output.dwStatus&MFT_OUTPUT_DATA_BUFFER_NO_SAMPLE)) {
            BYTE *data=NULL;DWORD size=0;buffer=NULL;
            hr=IMFSample_ConvertToContiguousBuffer(output.pSample,&buffer);
            if (SUCCEEDED(hr)) {
                hr=IMFMediaBuffer_Lock(buffer,&data,NULL,&size);
                if (SUCCEEDED(hr)) {
                    if (!size) hr=E_FAIL;
                    else { for (DWORD i=0;i<size;i+=4096) *checksum=(*checksum^data[i])*16777619u; }
                    IMFMediaBuffer_Unlock(buffer);
                }
                IMFMediaBuffer_Release(buffer);
            }
            if (SUCCEEDED(hr)) (*frames)++;
        }
        if (output.pEvents) IMFCollection_Release(output.pEvents);
        if (output.pSample && output.pSample!=allocated) IMFSample_Release(output.pSample);
        if (allocated) IMFSample_Release(allocated);
        if (hr==MF_E_TRANSFORM_NEED_MORE_INPUT) return S_OK;
        if (hr==MF_E_TRANSFORM_STREAM_CHANGE) { hr=output_type(transform);if (SUCCEEDED(hr)) continue; }
        if (FAILED(hr)) return hr;
    }
    return E_FAIL;
}
int wmain(int argc,WCHAR **argv) {
    if (argc!=3) return 2;
    WCHAR *end=NULL;unsigned long expected=wcstoul(argv[2],&end,10);
    if (!end || *end || !expected || expected>2000) return 2;
    HANDLE file=CreateFileW(argv[1],GENERIC_READ,FILE_SHARE_READ,NULL,OPEN_EXISTING,0,NULL);
    if (file==INVALID_HANDLE_VALUE) return 3;
    DWORD header[4],read=0;
    if (!ReadFile(file,header,sizeof(header),&read,NULL) || read!=sizeof(header) || header[0]!=0x36324846 ||
        !header[1] || header[1]>3840 || !header[2] || header[2]>2160 || header[3]!=expected) { CloseHandle(file);return 3; }
    HRESULT hr=CoInitializeEx(NULL,COINIT_MULTITHREADED);
    if (FAILED(checked("COM",hr))) { CloseHandle(file);return 4; }
    hr=MFStartup(MF_VERSION,MFSTARTUP_FULL);
    if (FAILED(checked("STARTUP",hr))) { CoUninitialize();CloseHandle(file);return 4; }
    IMFTransform *transform=NULL;IMFMediaType *input=NULL;int result=5;
    stage("CREATE_TRANSFORM");
    if (FAILED(checked("CREATE",CoCreateInstance(&CLSID_MSH264DecoderMFT,NULL,CLSCTX_INPROC_SERVER,
            &IID_IMFTransform,(void **)&transform)))) goto done;
    if (FAILED(MFCreateMediaType(&input))) goto done;
    if (FAILED(IMFMediaType_SetGUID(input,&MF_MT_MAJOR_TYPE,&MFMediaType_Video)) ||
        FAILED(IMFMediaType_SetGUID(input,&MF_MT_SUBTYPE,&MFVideoFormat_H264)) ||
        FAILED(IMFMediaType_SetUINT64(input,&MF_MT_FRAME_SIZE,((UINT64)header[1]<<32)|header[2])) ||
        FAILED(IMFMediaType_SetUINT64(input,&MF_MT_FRAME_RATE,((UINT64)30<<32)|1)) ||
        FAILED(IMFMediaType_SetUINT32(input,&MF_MT_INTERLACE_MODE,MFVideoInterlace_Progressive))) goto done;
    if (FAILED(checked("INPUT_TYPE",IMFTransform_SetInputType(transform,0,input,0)))) goto done;
    if (FAILED(checked("OUTPUT_TYPE",output_type(transform)))) goto done;
    stage("TYPE_SET");
    if (FAILED(checked("BEGIN",IMFTransform_ProcessMessage(transform,MFT_MESSAGE_NOTIFY_BEGIN_STREAMING,0)))) goto done;
    for (unsigned cycle=0;cycle<3;cycle++) {
        unsigned frames=0;DWORD checksum=2166136261u;
        LARGE_INTEGER position;position.QuadPart=sizeof(header);
        if (!SetFilePointerEx(file,position,NULL,FILE_BEGIN)) goto done;
        if (cycle) {
            stage("FLUSH");
            if (FAILED(checked("FLUSH",IMFTransform_ProcessMessage(transform,MFT_MESSAGE_COMMAND_FLUSH,0)))) goto done;
        }
        if (FAILED(checked("START",IMFTransform_ProcessMessage(transform,MFT_MESSAGE_NOTIFY_START_OF_STREAM,0)))) goto done;
        stage("INPUT_PACKETS");
        for (DWORD i=0;i<header[3];i++) {
            DWORD length=0;IMFSample *sample=NULL;IMFMediaBuffer *buffer=NULL;BYTE *bytes=NULL;
            if (!ReadFile(file,&length,sizeof(length),&read,NULL) || read!=sizeof(length) || !length || length>4*1024*1024) goto done;
            hr=make_sample(length,&sample,&buffer);
            if (SUCCEEDED(hr)) {
                hr=IMFMediaBuffer_Lock(buffer,&bytes,NULL,NULL);
                if (SUCCEEDED(hr)) {
                    if (!ReadFile(file,bytes,length,&read,NULL) || read!=length) hr=E_FAIL;
                    IMFMediaBuffer_Unlock(buffer);
                }
                if (SUCCEEDED(hr)) hr=IMFMediaBuffer_SetCurrentLength(buffer,length);
                if (SUCCEEDED(hr)) hr=IMFSample_SetSampleTime(sample,(LONGLONG)i*10000000/30);
                if (SUCCEEDED(hr)) hr=IMFSample_SetSampleDuration(sample,10000000/30);
                if (SUCCEEDED(hr)) {
                    hr=IMFTransform_ProcessInput(transform,0,sample,0);
                    if (hr==MF_E_NOTACCEPTING) {
                        hr=pull(transform,&frames,&checksum);
                        if (SUCCEEDED(hr)) hr=IMFTransform_ProcessInput(transform,0,sample,0);
                    }
                }
            }
            if (buffer) IMFMediaBuffer_Release(buffer);
            if (sample) IMFSample_Release(sample);
            if (FAILED(checked("INPUT",hr))) goto done;
            if (FAILED(checked("OUTPUT",pull(transform,&frames,&checksum)))) goto done;
        }
        stage("DRAIN");
        if (FAILED(checked("END",IMFTransform_ProcessMessage(transform,MFT_MESSAGE_NOTIFY_END_OF_STREAM,0))) ||
            FAILED(checked("DRAIN",IMFTransform_ProcessMessage(transform,MFT_MESSAGE_COMMAND_DRAIN,0))) ||
            FAILED(checked("OUTPUT_DRAIN",pull(transform,&frames,&checksum)))) goto done;
        printf("CYCLE=%u FRAMES=%u EXPECTED=%lu\nPIXEL_CHECKSUM=%08lx\n",cycle,frames,expected,(unsigned long)checksum);fflush(stdout);
        if (frames!=expected) goto done;
    }
    result=0;
done:
    if (input) IMFMediaType_Release(input);
    stage("RELEASE_TRANSFORM");
    if (transform) IMFTransform_Release(transform);
    MFShutdown();CoUninitialize();CloseHandle(file);stage("DONE");return result;
}
