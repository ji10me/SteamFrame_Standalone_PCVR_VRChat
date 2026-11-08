// Read gaze availability as a background client. No new scene/session, frames,
// action manifests, overlays, shared-memory writes or raw gaze output.
#include <openvr.h>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <thread>

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    char *end = nullptr;
    const auto expected = std::strtoul(argv[1], &end, 10);
    if (!end || *end || !expected || expected > 0x7fffffffUL) return 2;
    const auto seconds = std::strtol(argv[2], &end, 10);
    if (!end || *end || seconds < 1 || seconds > 15) return 2;
    vr::EVRInitError error = vr::VRInitError_None;
    auto *system = vr::VR_Init(&error, vr::VRApplication_Background);
    if (error != vr::VRInitError_None || !system) {
        std::printf("{\"init_error\":%d}\n", int(error));
        return 3;
    }
    auto *compositor = vr::VRCompositor();
    unsigned samples = 0, valid = 0, changed = 0;
    vr::HmdVector2_t previous[2]{};
    bool have_previous = false;
    bool same_scene = compositor != nullptr;
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(seconds);
    while (same_scene && std::chrono::steady_clock::now() < deadline) {
        same_scene = compositor->GetCurrentSceneFocusProcess() == expected;
        if (!same_scene) break;
        vr::HmdVector2_t left{}, right{};
        bool ok = system->GetEyeTrackedFoveationCenter(&left, &right);
        for (const auto &eye : {left, right})
            for (const float v : eye.v)
                ok = ok && std::isfinite(v) && v >= -1 && v <= 1;
        ++samples;
        valid += ok;
        if (ok) {
            bool moved = false;
            const vr::HmdVector2_t current[2] = {left, right};
            for (unsigned eye = 0; eye < 2; ++eye)
                for (unsigned axis = 0; axis < 2; ++axis)
                    moved |= std::fabs(current[eye].v[axis] - previous[eye].v[axis]) > .0001f;
            changed += have_previous && moved;
            previous[0] = left;
            previous[1] = right;
            have_previous = true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    same_scene = same_scene && compositor->GetCurrentSceneFocusProcess() == expected;
    std::printf("{\"system_interface\":\"%s\",\"samples\":%u,\"valid_samples\":%u,\"changed_samples\":%u,"
                "\"scene_unchanged\":%s,\"raw_gaze_saved\":false}\n",
                vr::IVRSystem_Version, samples, valid, changed, same_scene ? "true" : "false");
    vr::VR_Shutdown();
    return same_scene ? 0 : 4;
}
