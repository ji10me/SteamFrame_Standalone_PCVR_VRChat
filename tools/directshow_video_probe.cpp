// DirectShow fixture probe with its renderer window kept hidden. No game attachment or VR session.
// Usage: directshow_video_probe.exe LOCAL_MP4 EXPECTED_DURATION_100NS
#include <windows.h>
#include <dshow.h>
#include <cstdio>
#include <cstdlib>
#include <cwchar>

template<typename T> struct Com {
    T *p=nullptr;
    ~Com() { if (p) p->Release(); }
    T **out() { return &p; }
    T *operator->() const { return p; }
};
static bool ok(const char *name,HRESULT hr) {
    if (SUCCEEDED(hr)) return true;
    std::fprintf(stderr,"DS_ERROR %s 0x%08lx\n",name,static_cast<unsigned long>(hr));return false;
}
static void stage(const char *name) { std::printf("STAGE=%s\n",name);std::fflush(stdout); }
static HRESULT pin(IBaseFilter *filter,PIN_DIRECTION direction,IPin **result) {
    Com<IEnumPins> pins;
    HRESULT hr=filter->EnumPins(pins.out());if (FAILED(hr)) return hr;
    IPin *candidate=nullptr;
    while (pins->Next(1,&candidate,nullptr)==S_OK) {
        PIN_DIRECTION found;
        hr=candidate->QueryDirection(&found);
        if (SUCCEEDED(hr) && found==direction) { *result=candidate;return S_OK; }
        candidate->Release();
    }
    return E_FAIL;
}
static int run(const wchar_t *file,LONGLONG expected,unsigned cycle) {
    Com<IGraphBuilder> graph;Com<IBaseFilter> sink,source;
    Com<IPin> sink_in,source_out;Com<IVideoWindow> window;
    Com<IMediaControl> control;Com<IMediaEvent> events;Com<IMediaSeeking> seeking;
    stage("CREATE_GRAPH");
    if (!ok("GRAPH",CoCreateInstance(CLSID_FilterGraph,nullptr,CLSCTX_INPROC_SERVER,IID_IGraphBuilder,reinterpret_cast<void **>(graph.out()))) ||
        !ok("SINK",CoCreateInstance(CLSID_VideoRenderer,nullptr,CLSCTX_INPROC_SERVER,IID_IBaseFilter,reinterpret_cast<void **>(sink.out())))) return 5;
    // A classic video renderer requires raw video, so Intelligent Connect must
    // insert real demuxing/decoding. Keep its own window hidden throughout.
    if (!ok("WINDOW",sink->QueryInterface(IID_IVideoWindow,reinterpret_cast<void **>(window.out()))) ||
        !ok("ADD_SINK",graph->AddFilter(sink.p,L"Hidden synthetic video output")) ||
        !ok("SINK_PIN",pin(sink.p,PINDIR_INPUT,sink_in.out()))) return 5;
    stage("ADD_SOURCE");
    if (!ok("SOURCE",graph->AddSourceFilter(file,L"Synthetic local fixture",source.out())) ||
        !ok("SOURCE_PIN",pin(source.p,PINDIR_OUTPUT,source_out.out()))) return 5;
    stage("CONNECT_DECODING");
    if (!ok("CONNECT",graph->Connect(source_out.p,sink_in.p))) return 5;
    if (!ok("HIDE_AUTO",window->put_AutoShow(OAFALSE)) || !ok("HIDE",window->put_Visible(OAFALSE))) return 5;
    if (!ok("CONTROL",graph->QueryInterface(IID_IMediaControl,reinterpret_cast<void **>(control.out()))) ||
        !ok("EVENTS",graph->QueryInterface(IID_IMediaEvent,reinterpret_cast<void **>(events.out()))) ||
        !ok("SEEKING",graph->QueryInterface(IID_IMediaSeeking,reinterpret_cast<void **>(seeking.out())))) return 5;
    int result=0;
    stage("RUN");
    if (!ok("RUN",control->Run())) return 5;
    long code=0;HRESULT hr=events->WaitForCompletion(7000,&code);
    LONGLONG position=0;seeking->GetCurrentPosition(&position);
    std::printf("CYCLE=%u EVENT=%ld POSITION=%lld EXPECTED=%lld\n",cycle,code,position,expected);std::fflush(stdout);
    if (!ok("COMPLETE",hr) || code!=EC_COMPLETE || position<expected-1000000 || position>expected+1000000) result=5;
    stage("STOP");if (!ok("STOP",control->Stop())) result=5;
    stage("RELEASE_GRAPH");return result;
}
int wmain(int argc,wchar_t **argv) {
    if (argc!=3) return 2;
    wchar_t *end=nullptr;LONGLONG expected=std::wcstoll(argv[2],&end,10);
    if (!end || *end || expected<=0 || expected>60LL*10000000) return 2;
    if (!ok("COM",CoInitializeEx(nullptr,COINIT_MULTITHREADED))) return 3;
    int result=0;
    for (unsigned cycle=0;cycle<3;cycle++) {
        result=run(argv[1],expected,cycle);if (result) break;
    }
    CoUninitialize();stage("DONE");return result;
}
