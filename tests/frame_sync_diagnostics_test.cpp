#include "third_party/vrchat-dfr-gaze/frame_sync_diagnostics.h"
#include <atomic>
#include <cassert>
#include <thread>
#include <vector>

using namespace frame_sync;
static std::atomic<uint64_t> clockNs { 1000000 };
static uint64_t clockRead() { return clockNs.load(); }
static uint64_t clockStep() { return clockNs.fetch_add(1000); }
static Shared fresh() {
  Shared value {};
  std::memcpy(value.magic, "FSYNC001", 8);
  value.version = 1; value.byte_size = sizeof(value); value.frequency = 1000000000ULL;
  return value;
}
int main() {
  Shared data = fresh();
  Diagnostics d(&data, clockRead);
  assert(d.begin() == 0 && data.start_ns == 0);
  data.enabled = 1;
  auto start = d.begin();
  d.endAt(Flush, start, start + 2000000);
  d.endAt(Flush, start, start + 6000000);
  assert(data.metrics[Flush].count == 2 && data.metrics[Flush].total_ns == 8000000);
  assert(data.metrics[Flush].maximum_ns == 6000000);
  assert(data.metrics[Flush].over_1ms == 2 && data.metrics[Flush].over_5ms == 1);
  data.enabled = 0;
  d.endAt(Flush, start, start + 7000000);
  assert(data.metrics[Flush].count == 2 && data.dropped == 1);
  data.enabled = 1;
  clockNs = start + WindowNs;
  assert(d.begin() == 0 && data.stopped == 1 && data.enabled == 0);
  data.enabled = 1;
  assert(d.begin() == 0); // A stale trigger cannot rearm a completed run.
  data = fresh(); // A deliberate reset while no observations are in flight.
  data.enabled = 1; clockNs = 1000000;
  assert(d.begin() == 1000000);
  d.endAt(Flush, 1000000, 2000000);
  assert(data.metrics[Flush].count == 1 && data.metrics[Flush].total_ns == 1000000);

  Shared invalid = fresh(); invalid.version = 99; invalid.enabled = 1;
  Diagnostics rejected(&invalid, clockStep);
  uint64_t before = clockNs.load();
  assert(rejected.begin() == 0 && clockNs.load() == before);

  Shared backwards = fresh(); backwards.enabled = 1;
  clockNs = 2000000;
  Diagnostics reversed(&backwards, clockRead);
  assert(reversed.begin() == 2000000);
  clockNs = 1000000;
  assert(reversed.begin() == 0 && backwards.stopped == 1);

  Shared concurrent = fresh(); concurrent.enabled = 1;
  clockNs = 1000000;
  Diagnostics parallel(&concurrent, clockStep);
  std::vector<std::thread> threads;
  for (unsigned i = 0; i < 4; i++) threads.emplace_back([&] {
    for (unsigned j = 0; j < 10000; j++) {
      auto begin = parallel.begin(); assert(begin);
      parallel.end(QueueWait, begin);
    }
  });
  for (auto& thread : threads) thread.join();
  assert(concurrent.metrics[QueueWait].count == 40000);
  assert(concurrent.metrics[QueueWait].total_ns >= 40000000);
  assert(concurrent.metrics[Flush].count == 0 && concurrent.metrics[QueueHeld].count == 0);
  assert(concurrent.dropped == 0 && concurrent.stopped == 0);
}
