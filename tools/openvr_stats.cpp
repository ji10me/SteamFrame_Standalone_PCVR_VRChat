// Read the active scene application's counters as a background OpenVR client.
// Never submit frames, change the scene application, or reset counters.
#include <openvr.h>
#include <cmath>
#include <cstdio>
#include <cstring>

int main() {
    vr::EVRInitError error = vr::VRInitError_None;
    vr::VR_Init(&error, vr::VRApplication_Background);
    if (error != vr::VRInitError_None) {
        std::fprintf(stderr, "OpenVR initialization failed: %d\n", int(error));
        return 1;
    }
    auto *compositor = vr::VRCompositor();
    if (!compositor) {
        vr::VR_Shutdown();
        return 2;
    }
    vr::Compositor_CumulativeStats stats{};
    compositor->GetCumulativeStats(&stats, sizeof(stats));
    vr::TrackedDevicePose_t poses[vr::k_unMaxTrackedDeviceCount]{};
    auto *system = vr::VRSystem();
    system->GetDeviceToAbsoluteTrackingPose(vr::TrackingUniverseStanding, 0, poses, vr::k_unMaxTrackedDeviceCount);
    const auto &hmd = poses[vr::k_unTrackedDeviceIndex_Hmd];
    const auto fade = compositor->GetCurrentFadeColor(false);
    vr::VRControllerState_t hmd_state{};
    const bool proximity_available = system->GetControllerState(vr::k_unTrackedDeviceIndex_Hmd, &hmd_state, sizeof(hmd_state));
    const bool has_proximity = system->GetBoolTrackedDeviceProperty(vr::k_unTrackedDeviceIndex_Hmd, vr::Prop_ContainsProximitySensor_Bool);
    auto *overlay = vr::VROverlay();
    unsigned connected_controllers = 0, tracked_controllers = 0;
    for (unsigned i = 1; i < vr::k_unMaxTrackedDeviceCount; ++i) {
        if (!poses[i].bDeviceIsConnected || system->GetTrackedDeviceClass(i) != vr::TrackedDeviceClass_Controller) continue;
        ++connected_controllers;
        if (poses[i].bPoseIsValid && poses[i].eTrackingResult == vr::TrackingResult_Running_OK) ++tracked_controllers;
    }
    auto *chaperone = vr::VRChaperone();
    const int calibration_state = chaperone ? int(chaperone->GetCalibrationState()) : -1;
    const auto scene_pid = compositor->GetCurrentSceneFocusProcess();
    auto *applications = vr::VRApplications();
    char application_key[vr::k_unMaxApplicationKeyLength]{};
    const bool application_key_available = applications && scene_pid
        && applications->GetApplicationKeyByProcessId(scene_pid, application_key, sizeof(application_key)) == vr::VRApplicationError_None;
    vr::Compositor_FrameTiming timing{};
    timing.m_nSize = sizeof(timing);
    const bool timing_available = compositor->GetFrameTiming(&timing)
        && timing.m_nFrameIndex != 0 && timing.m_flClientFrameIntervalMs > 0
        && std::isfinite(timing.m_flClientFrameIntervalMs)
        && std::isfinite(timing.m_flPreSubmitGpuMs)
        && std::isfinite(timing.m_flCompositorRenderGpuMs)
        && std::isfinite(timing.m_flSubmitFrameMs);
    vr::ETrackedPropertyError frequency_error = vr::TrackedProp_Success;
    const float frequency = system->GetFloatTrackedDeviceProperty(
        vr::k_unTrackedDeviceIndex_Hmd, vr::Prop_DisplayFrequency_Float, &frequency_error);
    const float safe_frequency = frequency_error == vr::TrackedProp_Success && std::isfinite(frequency) ? frequency : 0;
    std::printf("{\"pid\":%u,\"presents\":%u,\"startup\":%u,"
                "\"loading\":%u,\"timed_out\":%u,\"reprojected\":%u,\"dropped\":%u,"
                "\"scene_pid\":%u,\"hmd_connected\":%s,\"hmd_pose_valid\":%s,"
                "\"chaperone_calibration_state\":%d,\"scene_app_key_available\":%s,"
                "\"scene_app_is_vrchat\":%s,\"scene_app_is_generated\":%s,"
                "\"connected_controllers\":%u,\"tracked_controllers\":%u,"
                "\"hmd_tracking_result\":%d,\"hmd_activity\":%d,"
                "\"grid_alpha\":%.4f,\"fade_alpha\":%.4f,\"scene_loading\":%s,"
                "\"has_proximity_sensor\":%s,\"proximity_state_available\":%s,"
                "\"proximity_pressed\":%s,\"dashboard_visible\":%s,"
                "\"display_frequency_hz\":%.3f,\"timing_available\":%s,\"timing\":",
                stats.m_nPid, stats.m_nNumFramePresents, stats.m_nNumFramePresentsOnStartup,
                stats.m_nNumFramePresentsLoading, stats.m_nNumFramePresentsTimedOut,
                stats.m_nNumReprojectedFrames, stats.m_nNumDroppedFrames,
                scene_pid, hmd.bDeviceIsConnected ? "true" : "false", hmd.bPoseIsValid ? "true" : "false",
                calibration_state, application_key_available ? "true" : "false",
                application_key_available && std::strcmp(application_key, "steam.app.438100") == 0 ? "true" : "false",
                application_key_available && std::strncmp(application_key, "system.generated.", 17) == 0 ? "true" : "false",
                connected_controllers, tracked_controllers,
                int(hmd.eTrackingResult),
                int(system->GetTrackedDeviceActivityLevel(vr::k_unTrackedDeviceIndex_Hmd)),
                compositor->GetCurrentGridAlpha(), fade.a,
                compositor->IsCurrentSceneFocusAppLoading() ? "true" : "false",
                has_proximity ? "true" : "false", proximity_available ? "true" : "false",
                (hmd_state.ulButtonPressed & vr::ButtonMaskFromId(vr::k_EButton_ProximitySensor)) ? "true" : "false",
                overlay && overlay->IsDashboardVisible() ? "true" : "false",
                safe_frequency, timing_available ? "true" : "false");
    if (timing_available) {
        // Client interval includes pacing/waits; it is not CPU execution time.
        // GPU times can include other processes and must not be added as exclusive work.
        std::printf("{\"frame_index\":%u,\"client_interval_ms\":%.4f,\"scene_gpu_ms\":%.4f,"
                    "\"compositor_gpu_ms\":%.4f,\"submit_call_ms\":%.4f,\"reprojection_flags\":%u}",
                    timing.m_nFrameIndex, timing.m_flClientFrameIntervalMs, timing.m_flPreSubmitGpuMs,
                    timing.m_flCompositorRenderGpuMs, timing.m_flSubmitFrameMs, timing.m_nReprojectionFlags);
    } else {
        std::printf("null");
    }
    std::printf("}\n");
    vr::VR_Shutdown();
    return 0;
}
