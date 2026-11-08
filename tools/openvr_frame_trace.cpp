// Collect timing history as a background client. No Submit/reset/pose output.
#include <openvr.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <thread>
#include <unordered_set>

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    char *end = nullptr;
    const long seconds = std::strtol(argv[1], &end, 10);
    if (!end || *end || seconds < 5 || seconds > 120) return 2;
    const auto pid = std::strtoul(argv[2], &end, 10);
    if (!end || *end || pid == 0 || pid > 0x7fffffffUL) return 2;
    vr::EVRInitError error = vr::VRInitError_None;
    vr::VR_Init(&error, vr::VRApplication_Background);
    if (error != vr::VRInitError_None) return 3;
    auto *compositor = vr::VRCompositor();
    if (!compositor) { vr::VR_Shutdown(); return 4; }
    vr::ETrackedPropertyError property_error = vr::TrackedProp_Success;
    const float frequency = vr::VRSystem()->GetFloatTrackedDeviceProperty(
        vr::k_unTrackedDeviceIndex_Hmd, vr::Prop_DisplayFrequency_Float, &property_error);
    const float safe_frequency = property_error == vr::TrackedProp_Success && std::isfinite(frequency) ? frequency : 0;
    std::printf("{\"type\":\"header\",\"pid\":%lu,\"seconds\":%ld,\"display_hz\":%.3f,\"poll_ms\":250}\n",
                pid, seconds, safe_frequency);
    std::unordered_set<uint32_t> seen;
    const auto start = std::chrono::steady_clock::now();
    bool seeded = false;
    unsigned emitted = 0, invalid = 0, skipped_polls = 0;
    while (std::chrono::steady_clock::now() - start < std::chrono::seconds(seconds)) {
        vr::Compositor_CumulativeStats stats{};
        compositor->GetCumulativeStats(&stats, sizeof(stats));
        if (compositor->GetCurrentSceneFocusProcess() != pid || stats.m_nPid != pid) {
            ++skipped_polls;
        } else {
            vr::Compositor_FrameTiming frames[128]{};
            for (auto &frame : frames) frame.m_nSize = sizeof(frame);
            const uint32_t count = std::min(compositor->GetFrameTimings(frames, 128), uint32_t(128));
            const double poll_seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
            // Observation clocks only: the OpenVR frame reference must be
            // checked against the host clock before correlating OS samples.
            const double poll_monotonic = std::chrono::duration<double>(
                std::chrono::steady_clock::now().time_since_epoch()).count();
            const double poll_epoch = std::chrono::duration<double>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            if (compositor->GetCurrentSceneFocusProcess() == pid) {
                for (uint32_t i = 0; i < count; ++i) {
                    const auto &f = frames[i];
                    if (!f.m_nFrameIndex || !seen.insert(f.m_nFrameIndex).second || !seeded) continue;
                    if (!std::isfinite(f.m_flSystemTimeInSeconds) || !std::isfinite(f.m_flPreSubmitGpuMs)
                        || !std::isfinite(f.m_flPostSubmitGpuMs) || !std::isfinite(f.m_flTotalRenderGpuMs)
                        || !std::isfinite(f.m_flCompositorRenderGpuMs) || !std::isfinite(f.m_flClientFrameIntervalMs)
                        || !std::isfinite(f.m_flSubmitFrameMs) || !std::isfinite(f.m_flNewFrameReadyMs)
                        || !std::isfinite(f.m_flNewPosesReadyMs) || !std::isfinite(f.m_flCompositorRenderCpuMs)
                        || !std::isfinite(f.m_flWaitGetPosesCalledMs) || !std::isfinite(f.m_flPresentCallCpuMs)
                        || !std::isfinite(f.m_flWaitForPresentCpuMs) || !std::isfinite(f.m_flCompositorIdleCpuMs)) {
                        ++invalid; continue;
                    }
                    // app_work_wall_ms is the legacy arithmetic delta. Some
                    // runtimes report poses_ready before WaitGetPoses called;
                    // reject elapsed-time interpretations unless ordered.
                    std::printf("{\"type\":\"frame\",\"poll_s\":%.6f,\"index\":%u,\"system_s\":%.6f,"
                                "\"scene_gpu_ms\":%.4f,\"post_submit_gpu_ms\":%.4f,\"total_gpu_ms\":%.4f,"
                                "\"compositor_gpu_ms\":%.4f,\"compositor_cpu_ms\":%.4f,\"client_interval_ms\":%.4f,"
                                "\"submit_call_ms\":%.4f,\"app_work_wall_ms\":%.4f,"
                                "\"wait_getposes_called_ms\":%.4f,\"poses_ready_ms\":%.4f,\"frame_ready_ms\":%.4f,"
                                "\"pose_call_ordered\":%s,"
                                "\"present_call_ms\":%.4f,\"wait_for_present_ms\":%.4f,\"compositor_idle_cpu_ms\":%.4f,"
                                "\"poll_monotonic_s\":%.6f,\"poll_epoch_s\":%.6f,"
                                "\"presents\":%u,\"dropped\":%u,\"mis_presented\":%u,\"flags\":%u}\n",
                                poll_seconds, f.m_nFrameIndex, f.m_flSystemTimeInSeconds,
                                f.m_flPreSubmitGpuMs, f.m_flPostSubmitGpuMs, f.m_flTotalRenderGpuMs,
                                f.m_flCompositorRenderGpuMs, f.m_flCompositorRenderCpuMs, f.m_flClientFrameIntervalMs,
                                f.m_flSubmitFrameMs, f.m_flNewFrameReadyMs - f.m_flNewPosesReadyMs,
                                f.m_flWaitGetPosesCalledMs, f.m_flNewPosesReadyMs, f.m_flNewFrameReadyMs,
                                f.m_flNewPosesReadyMs >= f.m_flWaitGetPosesCalledMs
                                    && f.m_flNewFrameReadyMs >= f.m_flNewPosesReadyMs ? "true" : "false",
                                f.m_flPresentCallCpuMs, f.m_flWaitForPresentCpuMs, f.m_flCompositorIdleCpuMs,
                                poll_monotonic, poll_epoch,
                                f.m_nNumFramePresents, f.m_nNumDroppedFrames, f.m_nNumMisPresented, f.m_nReprojectionFlags);
                    ++emitted;
                }
                seeded = count > 0;
            }
        }
        std::fflush(stdout);
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
    }
    std::printf("{\"type\":\"summary\",\"frames\":%u,\"invalid\":%u,\"skipped_polls\":%u}\n",
                emitted, invalid, skipped_polls);
    vr::VR_Shutdown();
    return emitted ? 0 : 5;
}
