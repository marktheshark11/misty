"""Head pose tracking and smooth, rate-limited head control for Misty."""

from __future__ import annotations

import bisect
import collections
import threading
import time

import requests

from mistyPy.Events import Events

YAW_LIMITS = (-81.0, 81.0)    # + is left
PITCH_LIMITS = (-40.0, 26.0)  # - is up
_SENSORS = {"ahy": "yaw", "actuator_headyaw": "yaw",
            "ahp": "pitch", "actuator_headpitch": "pitch"}


def _get(data, name: str):
    """Read a key regardless of capitalization (Misty's event JSON varies)."""
    if not isinstance(data, dict):
        return None
    wanted = name.casefold()
    return next((value for key, value in data.items() if key.casefold() == wanted), None)


def _clamp(value: float, limits) -> float:
    return max(limits[0], min(limits[1], value))


class _History:
    """Timestamped scalar samples with interpolated lookup."""

    def __init__(self, maxlen: int = 400):
        self.times = collections.deque(maxlen=maxlen)
        self.values = collections.deque(maxlen=maxlen)

    def add(self, t: float, value: float) -> None:
        # A new command supersedes any planned (future) samples from an earlier one.
        while self.times and self.times[-1] >= t:
            self.times.pop()
            self.values.pop()
        self.times.append(t)
        self.values.append(value)

    def at(self, t: float):
        if not self.times:
            return None
        times = list(self.times)
        i = bisect.bisect_left(times, t)
        if i <= 0:
            return self.values[0]
        if i >= len(times):
            return self.values[-1]
        t0, t1 = times[i - 1], times[i]
        v0, v1 = self.values[i - 1], self.values[i]
        return v0 + (v1 - v0) * (t - t0) / (t1 - t0) if t1 > t0 else v1

    def last_time(self) -> float:
        return self.times[-1] if self.times else 0.0


class HeadPose:
    """Knows where Misty's head was pointing at any recent moment.

    Uses Misty's ActuatorPosition events when they arrive. Otherwise it falls back to a
    model of the head moving toward each commanded angle at a limited speed. The model
    deliberately errs on the slow side: if it assumed the head arrives instantly, a face
    would look further off-centre than it is while the head is still turning, and the
    head would run away past the face.

    Looking the pose up at the *frame's capture time* (not "now") is what lets the
    tracker work in world angles despite stream latency.
    """

    def __init__(self, misty, start_yaw: float, start_pitch: float):
        self._lock = threading.Lock()
        self._measured = {"yaw": _History(), "pitch": _History()}
        self._commanded = {"yaw": _History(), "pitch": _History()}
        self._reported_live = False
        now = time.monotonic()
        self.record_command(now, start_yaw, start_pitch)
        # One unfiltered subscription: the SDK's SensorId filter may not match Misty's
        # message casing, which silently drops every head-position event.
        misty.register_event(
            Events.ActuatorPosition,
            "FaceTrackHeadPose",
            debounce=33,
            keep_alive=True,
            callback_function=self._on_actuator,
        )

    def _on_actuator(self, event) -> None:
        message = _get(event, "message")
        sensor = str(_get(message, "sensorId") or _get(message, "sensorName") or "").casefold()
        axis = _SENSORS.get(sensor)
        value = _get(message, "value")
        if axis and isinstance(value, (int, float)):
            with self._lock:
                self._measured[axis].add(time.monotonic(), float(value))
            if not self._reported_live:
                self._reported_live = True
                print("Receiving Misty's head position.")

    def estimate(self, t: float):
        """Modelled head position from commands only, ignoring measurements."""
        with self._lock:
            return self._commanded["yaw"].at(t), self._commanded["pitch"].at(t)

    def record_command(self, t: float, yaw: float, pitch: float, duration: float = 0.0) -> None:
        """Record a command; with a duration, the head is assumed to ramp there linearly."""
        with self._lock:
            for axis, value in (("yaw", yaw), ("pitch", pitch)):
                history = self._commanded[axis]
                if duration > 0:
                    start = history.at(t)
                    if start is not None:
                        history.add(t, start)
                history.add(t + duration, value)

    def measured_is_live(self) -> bool:
        with self._lock:
            newest = min(self._measured["yaw"].last_time(), self._measured["pitch"].last_time())
        return time.monotonic() - newest < 0.5

    def uncertainty(self, t: float) -> float:
        """Degrees the pose at time t may be off: 0 when measured, else the distance the
        head was still believed to be travelling (its true position is somewhere along it)."""
        if self.measured_is_live():
            return 0.0
        with self._lock:
            return max(abs(h.values[-1] - h.at(t)) for h in self._commanded.values())

    def at(self, t: float):
        """Return (yaw, pitch) in degrees at monotonic time t."""
        source = self._measured if self.measured_is_live() else self._commanded
        with self._lock:
            return source["yaw"].at(t), source["pitch"].at(t)


class HeadController:
    """Drives the head toward the tracker's target from its own thread.

    Commands are absolute angles in world space, so re-sending the same target after a
    delayed frame never double-counts motion (the usual cause of oscillation over Wi-Fi).
    """

    def __init__(self, misty_ip: str, pose: HeadPose, start_yaw: float, start_pitch: float,
                 lead_time: float, max_speed_dps: float = 30.0, rate_hz: float = 12.0,
                 deadband_deg: float = 1.2):
        self._url = f"http://{misty_ip}/api/head"
        self._session = requests.Session()
        self._pose = pose
        self._lead = lead_time
        self._period = 1.0 / rate_hz
        self._deadband = deadband_deg
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()  # One HTTP session, used from two threads.
        self._target = None
        self._commanded = (start_yaw, start_pitch)
        self._last_send = 0.0
        self._max_speed = max_speed_dps  # Head speed at Velocity=100, for the pose model.
        self.sent = 0
        self.failed = 0
        self._last_error = None
        self._running = True
        self._thread = threading.Thread(target=self._run, name="head-control", daemon=True)
        self._thread.start()

    def set_target(self, yaw: float, pitch: float, vel_yaw: float = 0.0, vel_pitch: float = 0.0):
        with self._lock:
            self._target = (yaw, pitch, vel_yaw, vel_pitch)

    def clear_target(self) -> None:
        with self._lock:
            self._target = None

    def move_to(self, yaw: float, pitch: float, duration: float) -> None:
        """Stop following and move the head over `duration` seconds (e.g. idle scanning)."""
        self.clear_target()
        with self._send_lock:
            self._post(yaw, pitch, duration=duration)

    def send(self, yaw: float, pitch: float, velocity: float) -> None:
        with self._send_lock:
            self._post(yaw, pitch, velocity=velocity)

    def _post(self, yaw: float, pitch: float, velocity=None, duration=None) -> None:
        yaw, pitch = _clamp(yaw, YAW_LIMITS), _clamp(pitch, PITCH_LIMITS)
        now = time.monotonic()
        if duration is None:
            # Estimate how long the head takes to get there at this velocity.
            est_yaw, est_pitch = self._pose.estimate(now)
            distance = max(abs(yaw - est_yaw), abs(pitch - est_pitch))
            ramp = distance / max(1.0, self._max_speed * velocity / 100.0)
        else:
            ramp = duration
        body = {"pitch": pitch, "roll": 0, "yaw": yaw, "units": "degrees"}
        body.update({"velocity": velocity} if duration is None else {"duration": duration})
        try:
            response = self._session.post(self._url, json=body, timeout=1.0)
            response.raise_for_status()
            status = response.json().get("status", "Success")
            if str(status).lower() != "success":
                raise RuntimeError(response.text[:200])
        except (requests.RequestException, ValueError, RuntimeError) as error:
            self.failed += 1
            if str(error) != self._last_error:  # Don't flood the console with repeats.
                self._last_error = str(error)
                print(f"Head command failed: {error}")
            return
        self.sent += 1
        self._commanded = (yaw, pitch)
        self._last_send = now
        self._pose.record_command(now, yaw, pitch, ramp)

    @property
    def commanded(self):
        return self._commanded

    def _run(self) -> None:
        while self._running:
            started = time.monotonic()
            with self._lock:
                target = self._target
            if target is not None:
                yaw, pitch, vy, vp = target
                # Lead the target slightly so a moving face isn't chased from behind
                # (capped so a bad velocity estimate can't fling the head away).
                aim_yaw = _clamp(yaw + max(-10.0, min(10.0, vy * self._lead)), YAW_LIMITS)
                aim_pitch = _clamp(pitch + max(-6.0, min(6.0, vp * self._lead)), PITCH_LIMITS)
                change = max(abs(aim_yaw - self._commanded[0]), abs(aim_pitch - self._commanded[1]))
                if change > self._deadband:
                    # Speed comes from how far the head *is* from the aim (not from the last
                    # command, which a moving face only nudges), plus the face's own speed:
                    # get there in ~0.3 s. Small corrections stay gentle to avoid jitter.
                    head_yaw, head_pitch = self._pose.at(time.monotonic())
                    distance = max(abs(aim_yaw - head_yaw), abs(aim_pitch - head_pitch))
                    needed = distance / 0.3 + max(abs(vy), abs(vp))
                    velocity = max(15.0, min(100.0, 100.0 * needed / self._max_speed))
                    with self._send_lock:
                        with self._lock:
                            still_following = self._target is not None
                        if still_following:  # A move_to() may have taken over meanwhile.
                            self._post(aim_yaw, aim_pitch, velocity=velocity)
            time.sleep(max(0.0, self._period - (time.monotonic() - started)))

    def close(self) -> None:
        self._running = False
        self._thread.join(timeout=2.0)
