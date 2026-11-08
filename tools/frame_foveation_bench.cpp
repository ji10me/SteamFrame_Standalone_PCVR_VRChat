// Finite CPU-only benchmark. Does not connect to a VR runtime or alter clocks.
#include "frame_foveation_map.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#ifdef FRAME_DFR_COMPARE_LEGACY
#include "dxvk_gaze_map.h"
#endif

static volatile uint64_t checksum = 0;

int main(int argc, char **argv) {
  unsigned iterations = 64;
  if (argc > 2) return 2;
  if (argc == 2) {
    char *end = nullptr;
    const auto n = std::strtoul(argv[1], &end, 10);
    if (*end || end == argv[1] || n < 1 || n > 256) return 2;
    iterations = unsigned(n);
  }
  for (auto size : {1440u, 1728u}) for (auto layers : {1u, 2u}) {
    frame_dfr::Layout layout{layers == 1 ? size*2 : size, size, layers, 8, 8};
    frame_dfr::Focus focus;
    focus.u = {.5f,.5f}; focus.v = {.5f,.5f};
    focus.inner_percent = 70; focus.outer_percent = 85;
    std::vector<uint8_t> map;
#ifdef FRAME_DFR_COMPARE_LEGACY
    dxvk::GazeMapRequest old;
    old.shape = {layout.width, layout.height, layers, 97, 2};
    old.texel = {8,8}; old.fullArea = 70; old.midArea = 85;
    dxvk::GazeMap legacy;
#endif
    auto vary = [&](unsigned i) {
      focus.u = {.3f + float(i%31)*.01f, .4f + float(i%19)*.01f};
      focus.v = {.5f,.5f};
#ifdef FRAME_DFR_COMPARE_LEGACY
      old.fx = focus.u; old.fy = focus.v;
#endif
    };
    for (unsigned i=0;i<8;++i) {
      vary(i);
      if (!frame_dfr::build_map(layout, focus, map)) return 3;
#ifdef FRAME_DFR_COMPARE_LEGACY
      dxvk::generateGazeMap(old, legacy);
      if (map != legacy.data) return 4;
#endif
    }
    auto run = [&](bool use_new) {
      auto start = std::chrono::steady_clock::now();
      for (unsigned i=0;i<iterations;++i) {
        vary(i);
#ifdef FRAME_DFR_COMPARE_LEGACY
        if (!use_new) {
          dxvk::generateGazeMap(old, legacy);
          checksum += legacy.data[(i*97)%legacy.data.size()];
          continue;
        }
#else
        (void)use_new;
#endif
        if (!frame_dfr::build_map(layout, focus, map)) std::abort();
        checksum += map[(i*97)%map.size()];
      }
      return std::chrono::duration<double,std::micro>(
          std::chrono::steady_clock::now()-start).count()/iterations;
    };
    for (unsigned repeat=0;repeat<5;++repeat) {
      double current = 0, previous = 0;
      if (repeat%2) current=run(true);
#ifdef FRAME_DFR_COMPARE_LEGACY
      previous=run(false);
#endif
      if (!(repeat%2)) current=run(true);
      std::printf("{\"eye_size\":%u,\"layers\":%u,\"repeat\":%u,\"iterations\":%u,\"new_us_per_map\":%.3f,\"legacy_us_per_map\":%.3f}\n",
          size,layers,repeat,iterations,current,previous);
    }
  }
}
