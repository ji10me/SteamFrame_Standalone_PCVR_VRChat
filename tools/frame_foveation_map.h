// Frame-specific CPU shading-rate map. Project license: ../LICENSE.md.
// Written after reviewing the previous implementation; not a clean-room claim.
#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <vector>

namespace frame_dfr {
struct Layout {
  uint32_t width, height, layers, tile_width, tile_height;
};
struct Focus {
  std::array<float, 2> u, v;
  float inner_radius = .20f, outer_radius = .40f;
  float inner_percent = -1.f, outer_percent = -1.f;
  uint32_t peripheral_rate = 5;
};

namespace detail {
// Analytic boundary first, exact float distance only at its neighbours.
// Bounded correction + binary fallback preserves the strict circle test even
// at rounding boundaries without evaluating hypot at every tile/search step.
template<class Inside>
inline std::array<uint32_t, 2> span(uint32_t first, uint32_t last,
    float center, float shift, float dy, float radius, float scale,
    uint32_t tile_width, Inside inside) {
  if (first == last || radius <= 0) return {first, first};
  auto index = [&](double x) {
    return uint32_t(std::clamp(x, double(first), double(last - 1)));
  };
  uint32_t pivot = index(std::floor((double(center) + shift) / tile_width));
  if (!inside(pivot)) {
    if (pivot > first && inside(pivot - 1)) --pivot;
    else if (pivot + 1 < last && inside(pivot + 1)) ++pivot;
    else return {first, first};
  }
  if (inside(first) && inside(last - 1)) return {first, last};
  const double r = double(radius) * scale;
  const double half = std::sqrt(std::max(0., r * r - double(dy) * dy));
  uint32_t left = uint32_t(std::clamp(
      std::floor((double(center) + shift - half) / tile_width - .5) + 1.,
      double(first), double(pivot)));
  uint32_t right = uint32_t(std::clamp(
      std::ceil((double(center) + shift + half) / tile_width - .5),
      double(pivot + 1), double(last)));
  unsigned corrections = 0;
  while (left > first && inside(left - 1) && corrections++ < 4) --left;
  while (left < pivot && !inside(left) && corrections++ < 4) ++left;
  if (!inside(left) || (left > first && inside(left - 1))) {
    uint32_t lo = first, hi = pivot;
    while (lo < hi) {
      uint32_t m = lo + (hi - lo) / 2;
      if (inside(m)) hi = m; else lo = m + 1;
    }
    left = lo;
  }
  corrections = 0;
  while (right < last && inside(right) && corrections++ < 4) ++right;
  while (right > pivot + 1 && !inside(right - 1) && corrections++ < 4) --right;
  if (!inside(right - 1) || (right < last && inside(right))) {
    uint32_t lo = pivot + 1, hi = last;
    while (lo < hi) {
      uint32_t m = lo + (hi - lo) / 2;
      if (inside(m)) lo = m + 1; else hi = m;
    }
    right = lo;
  }
  return {left, right};
}
}

// One layer means two eyes side-by-side, two layers means one eye per layer.
// The caller owns the vector; its allocation is reused. Invalid requests
// return false and an empty map, allowing the renderer to use full-rate shading.
inline bool build_map(const Layout &layout, const Focus &focus,
                      std::vector<uint8_t> &bytes) {
  auto invalid = [&] { bytes.clear(); return false; };
  if (!layout.width || !layout.height || !layout.tile_width || !layout.tile_height
      || layout.width > 1048576 || layout.height > 1048576
      || layout.tile_width > 1048576 || layout.tile_height > 1048576
      || (layout.layers != 1 && layout.layers != 2)
      || focus.peripheral_rate > 255
      || !std::isfinite(focus.inner_radius) || !std::isfinite(focus.outer_radius)
      || !std::isfinite(focus.inner_percent) || !std::isfinite(focus.outer_percent))
    return invalid();
  for (unsigned eye = 0; eye < 2; ++eye)
    if (!std::isfinite(focus.u[eye]) || !std::isfinite(focus.v[eye])) return invalid();
  const uint32_t columns = (layout.width + layout.tile_width - 1) / layout.tile_width;
  const uint32_t rows = (layout.height + layout.tile_height - 1) / layout.tile_height;
  const uint64_t tiles = uint64_t(columns) * rows;
  if (tiles > 1048576) return invalid();
  const size_t stride = (size_t(tiles) + 3) & ~size_t(3);
  bytes.resize(stride * layout.layers);
  const bool stereo = layout.layers == 1;
  const float eye_width = stereo ? float(layout.width) * .5f : float(layout.width);
  const float scale = std::min(eye_width, float(layout.height));
  auto radius = [&](float percent, float normalized) {
    if (percent < 0) return normalized;
    const float fraction = std::clamp(percent, 0.f, 100.f) * .01f;
    return fraction >= 1.f ? 10.f
        : std::sqrt(fraction * eye_width * float(layout.height) / 3.14159265f) / scale;
  };
  const float inner = radius(focus.inner_percent, focus.inner_radius);
  const float outer = radius(focus.outer_percent, focus.outer_radius);
  const uint8_t peripheral = uint8_t(focus.peripheral_rate);
  const uint8_t middle = uint8_t(std::min(5u, focus.peripheral_rate));
  auto x = [&](uint32_t column) { return (float(column) + .5f) * float(layout.tile_width); };
  uint32_t split = columns;
  if (stereo) {
    split = uint32_t(std::clamp(std::ceil(double(eye_width) / layout.tile_width - .5), 0., double(columns)));
    while (split && x(split - 1) >= eye_width) --split;
    while (split < columns && x(split) < eye_width) ++split;
  }
  for (uint32_t layer = 0; layer < layout.layers; ++layer) {
    auto base = bytes.begin() + size_t(layer) * stride;
    std::fill(base, base + tiles, peripheral);
    std::fill(base + tiles, base + stride, uint8_t(0));
    if (!peripheral) continue;
    for (uint32_t eye = stereo ? 0 : layer; eye < (stereo ? 2 : layer + 1); ++eye) {
      const uint32_t first = stereo && eye ? split : 0;
      const uint32_t last = stereo && !eye ? split : columns;
      const float shift = stereo ? float(eye) * eye_width : 0.f;
      const float cx = focus.u[eye] * eye_width;
      const float cy = focus.v[eye] * float(layout.height);
      for (uint32_t row = 0; row < rows; ++row) {
        const float dy = (float(row) + .5f) * float(layout.tile_height) - cy;
        auto paint = [&](float r, uint8_t rate) {
          auto inside = [&](uint32_t column) {
            const float px = x(column);
            const float local = stereo ? px - float(eye) * eye_width : px;
            const float py = (float(row) + .5f) * float(layout.tile_height);
            return std::hypot(local - focus.u[eye] * eye_width,
                py - focus.v[eye] * float(layout.height)) / scale < r;
          };
          auto edges = detail::span(first, last, cx, shift, dy, r, scale, layout.tile_width, inside);
          std::fill(base + size_t(row) * columns + edges[0],
                    base + size_t(row) * columns + edges[1], rate);
        };
        if (middle != peripheral) paint(outer, middle);
        paint(inner, 0);
      }
    }
  }
  return true;
}
}
