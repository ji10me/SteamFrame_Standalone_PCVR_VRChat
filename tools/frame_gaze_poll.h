// Scene readiness is slow-changing; eye samples retain their 10ms loop.
#pragma once
#include <chrono>

namespace frame_gaze {
class ScenePoll {
public:
    using Clock = std::chrono::steady_clock;
    using TimePoint = Clock::time_point;
    static constexpr std::chrono::milliseconds Interval{100};

    bool due(TimePoint now) const { return !checked_ || now >= next_; }
    void update(TimePoint now, bool ready) {
        ready_ = ready;
        checked_ = true;
        next_ = now + Interval;
    }
    bool ready() const { return ready_; }
private:
    TimePoint next_{};
    bool checked_ = false, ready_ = false;
};
} // namespace frame_gaze
