/* Standalone Windows Media Foundation video/flush/seek probe. No game hooks.
 * Usage: mf_video_probe.exe LOCAL_VIDEO EXPECTED_FRAMES [rgb32|d3d11|rgb32-d3d11]
 * Run in an empty, private Wine Prefix with an external timeout.
 */
#define COBJMACROS
#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <d3d11.h>
#include <propidl.h>
#include <stdio.h>
#include <stdlib.h>

static int failed(const char *operation, HRESULT status) {
    if (SUCCEEDED(status)) return 0;
    fprintf(stderr,"MF_ERROR %s 0x%08lx\n",operation,(unsigned long)status);
    return 1;
}
static void stage(const char *name) { printf("STAGE=%s\n",name);fflush(stdout); }

int wmain(int argc, WCHAR **argv) {
    if (argc!=3 && argc!=4) return 2;
    int rgb32=argc==4 && (!wcscmp(argv[3],L"rgb32") || !wcscmp(argv[3],L"rgb32-d3d11"));
    int d3d11=argc==4 && (!wcscmp(argv[3],L"d3d11") || !wcscmp(argv[3],L"rgb32-d3d11"));
    if (argc==4 && !rgb32 && !d3d11) return 2;
    WCHAR *end=NULL;
    unsigned long expected=wcstoul(argv[2],&end,10);
    if (!end || *end || !expected || expected>2000) return 2;
    DWORD file_attributes=GetFileAttributesW(argv[1]);
    printf("FILE_ATTRIBUTES=0x%08lx FILE_ERROR=%lu\n",(unsigned long)file_attributes,
           (unsigned long)(file_attributes==INVALID_FILE_ATTRIBUTES ? GetLastError() : 0));fflush(stdout);
    if (file_attributes==INVALID_FILE_ATTRIBUTES) return 6;
    if (failed("COM",CoInitializeEx(NULL,COINIT_MULTITHREADED))) return 3;
    if (failed("STARTUP",MFStartup(MF_VERSION,MFSTARTUP_FULL))) { CoUninitialize(); return 4; }
    IMFSourceReader *reader=NULL;
    IMFMediaType *type=NULL;
    IMFAttributes *attributes=NULL;
    ID3D11Device *device=NULL;
    IMFDXGIDeviceManager *manager=NULL;
    int result=5;
    if (d3d11) {
        UINT reset_token=0;
        stage("D3D11_DEVICE");
        if (failed("D3D11",D3D11CreateDevice(NULL,D3D_DRIVER_TYPE_HARDWARE,NULL,
                D3D11_CREATE_DEVICE_BGRA_SUPPORT,NULL,0,D3D11_SDK_VERSION,&device,NULL,NULL))) goto done;
        if (failed("DXGI_MANAGER",MFCreateDXGIDeviceManager(&reset_token,&manager))) goto done;
        if (failed("DXGI_DEVICE",IMFDXGIDeviceManager_ResetDevice(manager,(IUnknown *)device,reset_token))) goto done;
    }
    if (rgb32 || d3d11) {
        if (failed("ATTRIBUTES",MFCreateAttributes(&attributes,2))) goto done;
    }
    if (rgb32) {
        if (failed("RGB_PROCESSING",IMFAttributes_SetUINT32(attributes,
                &MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING,TRUE))) goto done;
    }
    if (d3d11 && failed("D3D_MANAGER",IMFAttributes_SetUnknown(attributes,
            &MF_SOURCE_READER_D3D_MANAGER,(IUnknown *)manager))) goto done;
    stage("OPEN_SOURCE");
    if (failed("SOURCE",MFCreateSourceReaderFromURL(argv[1],attributes,&reader))) goto done;
    stage("SOURCE_OPEN");
    if (failed("DESELECT",IMFSourceReader_SetStreamSelection(reader,MF_SOURCE_READER_ALL_STREAMS,FALSE))) goto done;
    if (failed("VIDEO",IMFSourceReader_SetStreamSelection(reader,MF_SOURCE_READER_FIRST_VIDEO_STREAM,TRUE))) goto done;
    if (failed("TYPE",MFCreateMediaType(&type))) goto done;
    if (failed("MAJOR",IMFMediaType_SetGUID(type,&MF_MT_MAJOR_TYPE,&MFMediaType_Video))) goto done;
    if (failed("SUBTYPE",IMFMediaType_SetGUID(type,&MF_MT_SUBTYPE,
            rgb32 ? &MFVideoFormat_RGB32 : &MFVideoFormat_NV12))) goto done;
    if (failed("SET_TYPE",IMFSourceReader_SetCurrentMediaType(reader,MF_SOURCE_READER_FIRST_VIDEO_STREAM,NULL,type))) goto done;
    stage("TYPE_SET");
    result=0;
    for (unsigned cycle=0;cycle<3;cycle++) {
        if (cycle) {
            PROPVARIANT position;
            ZeroMemory(&position,sizeof(position));position.vt=VT_I8;position.hVal.QuadPart=0;
            stage("FLUSH");
            if (failed("FLUSH",IMFSourceReader_Flush(reader,MF_SOURCE_READER_FIRST_VIDEO_STREAM))) { result=5;goto done; }
            stage("SEEK");
            if (failed("SEEK",IMFSourceReader_SetCurrentPosition(reader,&GUID_NULL,&position))) { result=5;goto done; }
        }
        unsigned frames=0;
        DWORD checksum=2166136261u;
        int eos=0;
        stage("READ_LOOP");
        for (unsigned read=0;read<4000;read++) {
            DWORD stream=0,flags=0;
            LONGLONG timestamp=0;
            IMFSample *sample=NULL;
            HRESULT status=IMFSourceReader_ReadSample(reader,MF_SOURCE_READER_FIRST_VIDEO_STREAM,
                0,&stream,&flags,&timestamp,&sample);
            if (sample) {
                IMFMediaBuffer *buffer=NULL;
                BYTE *bytes=NULL;
                DWORD length=0;
                HRESULT content=IMFSample_ConvertToContiguousBuffer(sample,&buffer);
                if (SUCCEEDED(content)) {
                    content=IMFMediaBuffer_Lock(buffer,&bytes,NULL,&length);
                    if (SUCCEEDED(content)) {
                        if (!length) content=E_FAIL;
                        else {
                            for (DWORD offset=0;offset<length;offset+=4096)
                                checksum=(checksum^bytes[offset])*16777619u;
                            checksum=(checksum^bytes[length-1])*16777619u;
                        }
                        IMFMediaBuffer_Unlock(buffer);
                    }
                    IMFMediaBuffer_Release(buffer);
                }
                frames++;IMFSample_Release(sample);
                if (failed("PIXELS",content)) { result=5;goto done; }
            }
            if (failed("READ",status) || (flags&MF_SOURCE_READERF_ERROR)) { result=5;goto done; }
            if (flags&MF_SOURCE_READERF_ENDOFSTREAM) { eos=1;break; }
        }
        printf("CYCLE=%u FRAMES=%u EXPECTED=%lu EOS=%d\n",cycle,frames,expected,eos);fflush(stdout);
        printf("PIXEL_CHECKSUM=%08lx FORMAT=%s\n",(unsigned long)checksum,rgb32 ? "RGB32" : "NV12");fflush(stdout);
        if (!eos || frames!=expected) result=5;
    }
done:
    if (type) IMFMediaType_Release(type);
    if (attributes) IMFAttributes_Release(attributes);
    stage("RELEASE_READER");
    if (reader) IMFSourceReader_Release(reader);
    if (manager) IMFDXGIDeviceManager_Release(manager);
    if (device) ID3D11Device_Release(device);
    stage("MF_SHUTDOWN");
    MFShutdown();CoUninitialize();
    stage("DONE");
    return result;
}
