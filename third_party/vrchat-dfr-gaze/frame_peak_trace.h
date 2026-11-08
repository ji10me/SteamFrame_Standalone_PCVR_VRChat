// Optional private DXVK diagnostics. SPDX-License-Identifier: Zlib
// Fixed append-only buffer: no addresses, textures, gaze or game data.
#pragma once
#include <chrono>
#include <cstdint>
#include <cstring>
#ifdef _WIN32
#include <windows.h>
#endif

namespace frame_peak {
constexpr uint32_t Capacity = 262144;
constexpr uint64_t WindowNs = 120000000000ULL;
enum Kind : uint32_t {
  Flush=1, InteropLock=2, InteropHeld=3, CsSync=4,
  SubmissionSync=5, SubmissionDrain=6, DeviceQueueLock=7,
  FrameLatency=8, Present=9, GpuCompletion=10, QueueSubmit=11,
  SubmitThreadLock=12
};
struct alignas(64) Header {
  char magic[8];
  uint32_t version, bytes, enabled, stopped, capacity, reserved;
  uint64_t tickets, dropped, start_ns, stop_ns, writer_seen, next_thread;
  uint64_t request, acknowledged, clock_ns, frequency, window_ns;
  uint32_t calibration_lock, padding;
};
struct alignas(64) Event {
  uint64_t committed, begin_ns, end_ns, reference;
  uint32_t thread_cookie, kind;
  uint64_t reserved[3];
};
static_assert(sizeof(Header)==128 && sizeof(Event)==64);
static_assert(offsetof(Header, tickets)==32 && offsetof(Header, request)==80);
static_assert(offsetof(Header, calibration_lock)==120);
constexpr uint32_t FileBytes = sizeof(Header)+Capacity*sizeof(Event);

template<typename T> inline T load(const T *p) { return __atomic_load_n(p,__ATOMIC_ACQUIRE); }
class Trace {
public:
  using Clock=uint64_t(*)();
  Trace() {
#ifdef _WIN32
    char path[4096];
    DWORD n=GetEnvironmentVariableA("DXVK_FRAME_PEAK_FILE",path,sizeof(path));
    if(!n || n>=sizeof(path)) return;
    m_file=CreateFileA(path,GENERIC_READ|GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE,
      nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
    if(m_file==INVALID_HANDLE_VALUE) return;
    LARGE_INTEGER size;
    if(!GetFileSizeEx(m_file,&size) || size.QuadPart!=FileBytes) return;
    m_mapping=CreateFileMappingA(m_file,nullptr,PAGE_READWRITE,0,0,nullptr);
    if(!m_mapping) return;
    m_view=MapViewOfFile(m_mapping,FILE_MAP_READ|FILE_MAP_WRITE,0,0,FileBytes);
    auto *h=static_cast<Header*>(m_view);
    if(valid(h) && h->capacity==Capacity) {
      m_header=h; m_events=reinterpret_cast<Event*>(h+1);
      __atomic_fetch_add(&h->writer_seen,uint64_t(1),__ATOMIC_RELAXED);
    }
#endif
  }
  Trace(Header *h, Event *events, Clock clock):m_clock(clock) {
    if(valid(h) && events) { m_header=h; m_events=events; }
  }
  Trace(const Trace&)=delete;
  Trace& operator=(const Trace&)=delete;
  ~Trace() {
#ifdef _WIN32
    if(m_view) UnmapViewOfFile(m_view);
    if(m_mapping) CloseHandle(m_mapping);
    if(m_file!=INVALID_HANDLE_VALUE) CloseHandle(m_file);
#endif
  }
  uint64_t begin() {
    if(!m_header || !load(&m_header->enabled) || load(&m_header->stopped)) return 0;
    uint64_t start=load(&m_header->start_ns);
    uint64_t now=m_clock();
    if(!start) {
      uint64_t zero=0;
      if(__atomic_compare_exchange_n(&m_header->start_ns,&zero,now,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE)) start=now;
      else {
        start=zero;
        // Another writer may initialize after our timestamp was sampled.
        // Refresh this stale first sample instead of reporting clock reversal.
        if(now<start) now=m_clock();
      }
    }
    if(now<start || now-start>=m_header->window_ns) { stop(now); return 0; }
    calibrate(now);
    return now;
  }
  uint64_t timestamp() { return m_clock(); }
  void end(Kind kind,uint64_t start,uint64_t reference=0) {
    if(start) endAt(kind,start,m_clock(),reference);
  }
  void endAt(Kind kind,uint64_t start,uint64_t now,uint64_t reference=0) {
    if(!start || !m_header) return;
    uint64_t origin=load(&m_header->start_ns);
    if(!load(&m_header->enabled) || load(&m_header->stopped) || now<start || now<origin
       || now-origin>=m_header->window_ns || kind<Flush || kind>SubmitThreadLock) {
      __atomic_fetch_add(&m_header->dropped,uint64_t(1),__ATOMIC_RELAXED);
      if(now<origin || (now>=origin && now-origin>=m_header->window_ns)) stop(now);
      return;
    }
    uint64_t ticket=__atomic_fetch_add(&m_header->tickets,uint64_t(1),__ATOMIC_RELAXED);
    if(ticket>=m_header->capacity) {
      __atomic_fetch_add(&m_header->dropped,uint64_t(1),__ATOMIC_RELAXED);stop(now);return;
    }
    // Cookies identify diagnostic writers, not Linux or Windows thread IDs.
    static thread_local uint32_t cookie=0;
    if(!cookie) cookie=uint32_t(__atomic_add_fetch(&m_header->next_thread,uint64_t(1),__ATOMIC_RELAXED));
    Event &e=m_events[ticket];
    e.begin_ns=start;e.end_ns=now;e.reference=reference;e.thread_cookie=cookie;e.kind=kind;
    __atomic_store_n(&e.committed,ticket+1,__ATOMIC_RELEASE);
  }
private:
  static bool valid(const Header *h) {
    return h && !std::memcmp(h->magic,"FPEAK001",8) && h->version==1
      && h->capacity>0 && h->capacity<=Capacity
      && h->bytes==sizeof(Header)+uint64_t(h->capacity)*sizeof(Event)
      && h->frequency==1000000000ULL && h->window_ns>0 && h->window_ns<=WindowNs;
  }
  void calibrate(uint64_t) {
    uint64_t request=load(&m_header->request);
    if(request==load(&m_header->acknowledged)) return;
    uint32_t zero=0;
    if(!__atomic_compare_exchange_n(&m_header->calibration_lock,&zero,uint32_t(1),false,__ATOMIC_ACQUIRE,__ATOMIC_RELAXED)) return;
    request=load(&m_header->request);
    if(request!=load(&m_header->acknowledged)) {
      __atomic_store_n(&m_header->clock_ns,m_clock(),__ATOMIC_RELAXED);
      __atomic_store_n(&m_header->acknowledged,request,__ATOMIC_RELEASE);
    }
    __atomic_store_n(&m_header->calibration_lock,uint32_t(0),__ATOMIC_RELEASE);
  }
  void stop(uint64_t now) {
    uint64_t zero=0;
    __atomic_compare_exchange_n(&m_header->stop_ns,&zero,now,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE);
    __atomic_store_n(&m_header->stopped,uint32_t(1),__ATOMIC_RELEASE);
    __atomic_store_n(&m_header->enabled,uint32_t(0),__ATOMIC_RELEASE);
  }
  static uint64_t nowNs() {
    return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count());
  }
  Header *m_header=nullptr;
  Event *m_events=nullptr;
  Clock m_clock=nowNs;
#ifdef _WIN32
  HANDLE m_file=INVALID_HANDLE_VALUE,m_mapping=nullptr;
  void *m_view=nullptr;
#endif
};
inline Trace& trace() { static Trace instance; return instance; }
struct Scope {
  Kind kind;
  uint64_t reference,start;
  Scope(Kind k,uint64_t ref=0):kind(k),reference(ref),start(trace().begin()) { }
  ~Scope() { trace().end(kind,start,reference); }
};
} // namespace frame_peak
