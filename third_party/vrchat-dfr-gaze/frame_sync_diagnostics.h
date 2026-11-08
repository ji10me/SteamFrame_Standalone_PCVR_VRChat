// Diagnostic addition for the private DXVK patch. See LICENSE-DXVK.
// Only elapsed counters are shared. No textures, addresses or game data.
#pragma once
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstring>
#ifdef _WIN32
#include <windows.h>
#endif

namespace frame_sync {
enum Metric : unsigned { Flush = 0, QueueWait = 1, QueueHeld = 2 };
struct Counters {
  uint64_t count, total_ns, maximum_ns, over_1ms, over_5ms;
};
struct alignas(64) Shared {
  char magic[8];
  uint32_t version, byte_size, enabled, stopped;
  uint64_t frequency, start_ns, stop_ns, dropped, writer_seen;
  Counters metrics[3];
  unsigned char padding[72];
};
static_assert(sizeof(Shared) == 256 && offsetof(Shared, metrics) == 64);
static_assert(offsetof(Shared, enabled) == 16 && offsetof(Shared, start_ns) == 32);
constexpr uint64_t WindowNs = 30000000000ULL;

class Diagnostics {
public:
  using Clock = uint64_t (*)();
  Diagnostics() {
#ifdef _WIN32
    char path[4096];
    DWORD length = GetEnvironmentVariableA("DXVK_FRAME_SYNC_FILE", path, sizeof(path));
    if (!length || length >= sizeof(path)) return;
    m_file = CreateFileA(path, GENERIC_READ | GENERIC_WRITE,
      FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (m_file == INVALID_HANDLE_VALUE) return;
    LARGE_INTEGER size;
    if (!GetFileSizeEx(m_file, &size) || size.QuadPart != sizeof(Shared)) return;
    m_mapping = CreateFileMappingA(m_file, nullptr, PAGE_READWRITE, 0, 0, nullptr);
    if (!m_mapping) return;
    m_view = MapViewOfFile(m_mapping, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, sizeof(Shared));
    auto *shared = static_cast<Shared*>(m_view);
    if (valid(shared)) {
      m_shared = shared;
      __atomic_store_n(&shared->writer_seen, uint64_t(1), __ATOMIC_RELEASE);
    }
#endif
  }
  // Caller-owned transport/clock for the standalone concurrency and timeout test.
  Diagnostics(Shared *shared, Clock clock) : m_shared(valid(shared) ? shared : nullptr), m_clock(clock) { }
  Diagnostics(const Diagnostics&) = delete;
  Diagnostics& operator=(const Diagnostics&) = delete;
  ~Diagnostics() {
#ifdef _WIN32
    if (m_view) UnmapViewOfFile(m_view);
    if (m_mapping) CloseHandle(m_mapping);
    if (m_file != INVALID_HANDLE_VALUE) CloseHandle(m_file);
#endif
  }
  uint64_t begin() {
    if (!m_shared || !load(&m_shared->enabled) || load(&m_shared->stopped)) return 0;
    uint64_t now = m_clock();
    uint64_t expected = 0;
    __atomic_compare_exchange_n(&m_shared->start_ns, &expected, now, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
    uint64_t start = load(&m_shared->start_ns);
    if (now < start || now - start >= WindowNs) { stop(now); return 0; }
    return now;
  }
  void end(Metric metric, uint64_t begin_ns) {
    if (begin_ns) endAt(metric, begin_ns, m_clock());
  }
  uint64_t timestamp() { return m_clock(); }
  void endAt(Metric metric, uint64_t begin_ns, uint64_t now) {
    if (!begin_ns || !m_shared || unsigned(metric) >= 3) return;
    uint64_t start = load(&m_shared->start_ns);
    if (!load(&m_shared->enabled) || load(&m_shared->stopped)
        || now < begin_ns || now < start || now - start >= WindowNs) {
      __atomic_fetch_add(&m_shared->dropped, uint64_t(1), __ATOMIC_RELAXED);
      if (now < start || (now >= start && now - start >= WindowNs)) stop(now);
      return;
    }
    uint64_t elapsed = now - begin_ns;
    auto& counters = m_shared->metrics[metric];
    __atomic_fetch_add(&counters.count, uint64_t(1), __ATOMIC_RELAXED);
    __atomic_fetch_add(&counters.total_ns, elapsed, __ATOMIC_RELAXED);
    uint64_t maximum = load(&counters.maximum_ns);
    while (elapsed > maximum && !__atomic_compare_exchange_n(&counters.maximum_ns,
      &maximum, elapsed, true, __ATOMIC_RELAXED, __ATOMIC_RELAXED)) { }
    if (elapsed >= 1000000) __atomic_fetch_add(&counters.over_1ms, uint64_t(1), __ATOMIC_RELAXED);
    if (elapsed >= 5000000) __atomic_fetch_add(&counters.over_5ms, uint64_t(1), __ATOMIC_RELAXED);
  }
private:
  template<typename T> static T load(const T *value) { return __atomic_load_n(value, __ATOMIC_ACQUIRE); }
  static bool valid(const Shared *s) {
    return s && !std::memcmp(s->magic, "FSYNC001", 8) && s->version == 1
      && s->byte_size == sizeof(Shared) && s->frequency == 1000000000ULL;
  }
  static uint64_t nowNs() {
    return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count());
  }
  void stop(uint64_t now) {
    uint64_t expected = 0;
    __atomic_compare_exchange_n(&m_shared->stop_ns, &expected, now, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
    __atomic_store_n(&m_shared->stopped, uint32_t(1), __ATOMIC_RELEASE);
    __atomic_store_n(&m_shared->enabled, uint32_t(0), __ATOMIC_RELEASE);
  }
  Shared *m_shared = nullptr;
  Clock m_clock = nowNs;
#ifdef _WIN32
  HANDLE m_file = INVALID_HANDLE_VALUE, m_mapping = nullptr;
  void *m_view = nullptr;
#endif
};
} // namespace frame_sync
