// Unprivileged finite OpenVR Background -> GAZ2 publisher. No scene/session
// creation, frames, overlays, game writes or raw gaze logging.
#include <openvr.h>
#include "frame_gaze_projection.h"
#include "frame_gaze_poll.h"
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <cerrno>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

static volatile std::sig_atomic_t stopping = 0;
static void stop(int) { stopping = 1; }

static std::string read(const std::string &path) {
    std::ifstream file(path, std::ios::binary);
    if (!file) throw std::runtime_error("game identity unavailable");
    return std::string(std::istreambuf_iterator<char>(file), {});
}

static std::string identity(unsigned long pid) {
    const auto root = "/proc/" + std::to_string(pid);
    struct stat metadata{};
    if (stat(root.c_str(), &metadata) || metadata.st_uid != getuid() || read(root + "/comm") != "VRChat.exe\n")
        throw std::runtime_error("game identity changed");
    std::istringstream environment(read(root + "/environ"));
    std::string entry;
    bool correct_app = false;
    while (std::getline(environment, entry, '\0')) correct_app |= entry == "SteamAppId=438100";
    if (!correct_app) throw std::runtime_error("not the VRChat launch");
    const auto proc_stat = read(root + "/stat");
    const auto end = proc_stat.rfind(')');
    if (end == std::string::npos) throw std::runtime_error("invalid process identity");
    std::istringstream fields(proc_stat.substr(end + 1));
    for (unsigned i = 0; i < 20; ++i)
        if (!(fields >> entry)) throw std::runtime_error("invalid process identity");
    return entry;
}

class Output {
    int fd = -1;
    gaze_bridge::SharedGaze *memory = nullptr;
public:
    Output() {
        const char *home = std::getenv("HOME");
        if (!home || *home != '/') throw std::runtime_error("absolute HOME required");
        const std::string directory = std::string(home) + "/.local/share/frame-pcvr/gaze";
        if (mkdir(directory.c_str(), 0700) && errno != EEXIST)
            throw std::runtime_error("cannot create private gaze directory");
        struct stat metadata{};
        if (lstat(directory.c_str(), &metadata) || !S_ISDIR(metadata.st_mode) ||
            metadata.st_uid != getuid() || (metadata.st_mode & 0777) != 0700)
            throw std::runtime_error("gaze directory must be private and owned by this user");
        const auto path = directory + "/frame-gaze-v1.bin";
        bool created = true;
        fd = open(path.c_str(), O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (fd < 0 && errno == EEXIST) {
            created = false;
            fd = open(path.c_str(), O_RDWR | O_NOFOLLOW | O_CLOEXEC);
        }
        try {
            if (fd < 0 || fstat(fd, &metadata) || !S_ISREG(metadata.st_mode) ||
                metadata.st_uid != getuid() || metadata.st_nlink != 1 || (metadata.st_mode & 0777) != 0600)
                throw std::runtime_error("untrusted gaze file");
            if (flock(fd, LOCK_EX | LOCK_NB)) throw std::runtime_error("another gaze writer is active");
            if (created && ftruncate(fd, sizeof(gaze_bridge::SharedGaze)))
                throw std::runtime_error("cannot size gaze file");
            if (fstat(fd, &metadata) || metadata.st_size != static_cast<off_t>(sizeof(gaze_bridge::SharedGaze)))
                throw std::runtime_error("unexpected gaze file size; existing file was retained");
            void *address = mmap(nullptr, sizeof(gaze_bridge::SharedGaze), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
            if (address == MAP_FAILED) throw std::runtime_error("cannot map gaze file");
            memory = static_cast<gaze_bridge::SharedGaze *>(address);
            publish({});
        } catch (...) {
            if (fd >= 0) close(fd);
            fd = -1;
            throw;
        }
    }
    Output(const Output &) = delete;
    Output &operator=(const Output &) = delete;
    void publish(const gaze_bridge::Sample &sample) { gaze_bridge::publish(memory, sample); }
    ~Output() {
        if (memory) {
            publish({}); // Invalidate and erase last gaze values; preserve inode for mapped readers.
            munmap(memory, sizeof(gaze_bridge::SharedGaze));
        }
        if (fd >= 0) close(fd);
    }
};

int main(int argc, char **argv) {
    if (argc < 3 || geteuid() == 0 || geteuid() != getuid()) return 2;
    bool flip_y = false, calibration_set = false;
    frame_gaze::Calibration calibration;
    for (int i = 3; i < argc; ++i) {
        const std::string option(argv[i]);
        if (option == "--flip-y" && !flip_y) { flip_y = true; continue; }
        if (option != "--calibration" || calibration_set || argc - i < 5) return 2;
        float *values[] = {&calibration.gain_x, &calibration.gain_y, &calibration.offset_x, &calibration.offset_y};
        for (auto *value : values) {
            char *last = nullptr;
            const char *input = argv[++i];
            *value = std::strtof(input, &last);
            if (last == input || *last) return 2;
        }
        if (!calibration.valid()) return 2;
        calibration_set = true;
    }
    char *end = nullptr;
    const auto pid = std::strtoul(argv[1], &end, 10);
    if (!end || *end || !pid || pid > 0x7fffffffUL) return 2;
    const auto seconds = std::strtol(argv[2], &end, 10);
    if (!end || *end || seconds < 1 || seconds > 1800) return 2;
    bool initialized = false;
    int status = 1;
    try {
        const auto started = identity(pid);
        vr::EVRInitError error = vr::VRInitError_None;
        auto *system = vr::VR_Init(&error, vr::VRApplication_Background);
        initialized = error == vr::VRInitError_None;
        if (!initialized || !system) throw std::runtime_error("OpenVR Background initialization failed");
        auto *compositor = vr::VRCompositor();
        if (!compositor || compositor->GetCurrentSceneFocusProcess() != pid)
            throw std::runtime_error("VRChat does not own the scene");
        for (const int sig : {SIGTERM, SIGINT, SIGHUP}) std::signal(sig, stop);
        Output output;
        unsigned samples = 0, valid = 0;
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(seconds);
        auto next_identity = std::chrono::steady_clock::now();
        frame_gaze::ScenePoll scene;
        while (!stopping && std::chrono::steady_clock::now() < deadline) {
            if (std::chrono::steady_clock::now() >= next_identity) {
                if (identity(pid) != started) throw std::runtime_error("VRChat process replaced");
                next_identity = std::chrono::steady_clock::now() + std::chrono::seconds(1);
            }
            if (scene.due(std::chrono::steady_clock::now())) {
                if (compositor->GetCurrentSceneFocusProcess() != pid)
                    throw std::runtime_error("VRChat lost scene focus");
                const bool ready = !compositor->IsCurrentSceneFocusAppLoading() && compositor->GetCurrentGridAlpha() < .01f;
                scene.update(std::chrono::steady_clock::now(), ready);
            }
            vr::HmdVector2_t left{}, right{};
            const bool tracked = scene.ready() && system->GetEyeTrackedFoveationCenter(&left, &right);
            const auto sample = frame_gaze::calibrated(
                frame_gaze::from_ndc(tracked, left.v, right.v, flip_y), calibration);
            output.publish(sample);
            ++samples;
            valid += sample.valid;
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        std::printf("{\"samples\":%u,\"valid_samples\":%u,\"normal_shutdown_invalidates\":true,"
                    "\"raw_gaze_logged\":false,\"flip_y\":%s,"
                    "\"scene_poll_ms\":100,\"eye_sleep_ms\":10,"
                    "\"calibration\":[%.4f,%.4f,%.4f,%.4f]}\n", samples, valid, flip_y ? "true" : "false",
                    calibration.gain_x, calibration.gain_y, calibration.offset_x, calibration.offset_y);
        status = 0;
    } catch (const std::exception &error) {
        std::fprintf(stderr, "%s\n", error.what());
    }
    if (initialized) vr::VR_Shutdown();
    return status;
}
