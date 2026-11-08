#pragma once
#include <gaze_shm.h>
#include <algorithm>

namespace frame_gaze {
struct Calibration {
    float gain_x = 1, gain_y = 1, offset_x = 0, offset_y = 0;
    bool valid() const {
        return std::isfinite(gain_x) && std::isfinite(gain_y) &&
            std::isfinite(offset_x) && std::isfinite(offset_y) &&
            gain_x >= .5f && gain_x <= 1.5f && gain_y >= .5f && gain_y <= 1.5f &&
            std::abs(offset_x) <= .1f && std::abs(offset_y) <= .1f;
    }
};

inline gaze_bridge::Sample calibrated(gaze_bridge::Sample sample, const Calibration &calibration) {
    if (!sample.valid || !calibration.valid()) return {};
    for (unsigned eye = 0; eye < 2; ++eye) {
        sample.u[eye] = std::clamp(.5f + (sample.u[eye] - .5f) * calibration.gain_x + calibration.offset_x, 0.f, 1.f);
        sample.v[eye] = std::clamp(.5f + (sample.v[eye] - .5f) * calibration.gain_y + calibration.offset_y, 0.f, 1.f);
    }
    return sample;
}

// OpenVR supplies NDC, with Y upwards. The GAZ2 wire format uses top-left UV.
// Invalid or out-of-view samples publish a neutral invalid packet, never stale
// gaze. flip_y adapts to inverted Unity eye textures without a game restart;
// it must not also be applied by the DXVK reader.
inline gaze_bridge::Sample from_ndc(bool valid, const float left[2], const float right[2], bool flip_y = false) {
    gaze_bridge::Sample result;
    if (!valid) return result;
    const float *eyes[2] = {left, right};
    for (unsigned eye = 0; eye < 2; ++eye)
        for (unsigned axis = 0; axis < 2; ++axis)
            if (!std::isfinite(eyes[eye][axis]) || eyes[eye][axis] < -1 || eyes[eye][axis] > 1)
                return result;
    for (unsigned eye = 0; eye < 2; ++eye) {
        result.u[eye] = (eyes[eye][0] + 1.f) * .5f;
        result.v[eye] = (1.f + (flip_y ? eyes[eye][1] : -eyes[eye][1])) * .5f;
    }
    result.valid = true;
    return result;
}
}
