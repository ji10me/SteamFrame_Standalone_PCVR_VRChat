#include "third_party/vrchat-dfr-gaze/frame_peak_trace.h"
#include <atomic>
#include <cassert>
#include <thread>
#include <vector>

using namespace frame_peak;
static std::atomic<uint64_t> clockValue{1000000};
static uint64_t clockRead() { return clockValue.fetch_add(100); }
static std::atomic<unsigned> raceCalls{0};
static std::atomic<bool> earlyClockEntered{false}, releaseEarlyClock{false};
static uint64_t racingClock() {
  unsigned n=raceCalls.fetch_add(1);
  if(!n) {
    earlyClockEntered=true;
    while(!releaseEarlyClock.load()) std::this_thread::yield();
    return 1000000;
  }
  return 2000000+uint64_t(n-1)*1000000;
}
static Header fresh(uint32_t capacity) {
  Header h{};std::memcpy(h.magic,"FPEAK001",8);h.version=1;h.capacity=capacity;
  h.bytes=sizeof(Header)+capacity*sizeof(Event);h.frequency=1000000000;h.window_ns=WindowNs;return h;
}
int main() {
  Header h=fresh(50000);std::vector<Event> e(h.capacity);Trace t(&h,e.data(),clockRead);
  auto before=clockValue.load();assert(!t.begin() && clockValue.load()==before);
  h.enabled=1;h.request=1;auto start=t.begin();assert(start && h.acknowledged==1 && h.clock_ns>=start);
  t.endAt(Flush,start,start+5000000,7);
  assert(e[0].committed==1 && e[0].end_ns-e[0].begin_ns==5000000 && e[0].reference==7);
  std::vector<std::thread> threads;
  for(int i=0;i<4;i++) threads.emplace_back([&]{for(int n=0;n<10000;n++){auto a=t.begin();assert(a);t.end(CsSync,a);}});
  for(auto &thread:threads) thread.join();
  assert(h.tickets==40001 && h.dropped==0);
  for(uint64_t i=0;i<h.tickets;i++) assert(e[i].committed==i+1 && e[i].end_ns>=e[i].begin_ns);
  clockValue=h.start_ns+WindowNs;assert(!t.begin() && h.stopped && !h.enabled);
  h.enabled=1;assert(!t.begin()); // A completed buffer cannot be rearmed.
  Header small=fresh(1);Event one{};Trace overflow(&small,&one,clockRead);small.enabled=1;
  overflow.end(Flush,overflow.begin());overflow.end(Flush,overflow.begin());
  assert(small.stopped && small.dropped==1 && one.committed==1);
  Header invalid=fresh(1);invalid.version=99;invalid.enabled=1;Trace rejected(&invalid,&one,clockRead);
  before=clockValue.load();assert(!rejected.begin() && clockValue.load()==before);
  Header backwards=fresh(1);backwards.enabled=1;Trace reversed(&backwards,&one,clockRead);
  start=reversed.begin();reversed.endAt(Flush,start,start-1);assert(backwards.dropped==1 && backwards.stopped);
  Header race=fresh(1);race.enabled=1;Event raceEvent{};Trace concurrentStart(&race,&raceEvent,racingClock);
  uint64_t first=0;
  std::thread early([&]{first=concurrentStart.begin();});
  while(!earlyClockEntered.load()) std::this_thread::yield();
  uint64_t second=concurrentStart.begin();
  releaseEarlyClock=true;early.join();
  assert(first && second && first>=race.start_ns && second>=race.start_ns);
  assert(race.enabled && !race.stopped && race.start_ns==2000000);
}
