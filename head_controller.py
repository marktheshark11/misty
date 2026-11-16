"""Image-error feedback control of Misty's head.

This is the design used by the well-known pan/tilt face trackers (PyImageSearch's
pan-tilt tracker, Pollen Robotics' reachy-face-tracking): the only input is how far the
face is from the centre of the picture, and each new frame nudges the head a fraction of
that error towards it. Nothing here needs to know the stream latency, the exact field of
view or where the head really is: errors in any of those only change how quickly the
head converges, never where it ends up. Latency limits how high the gain can go; that is
the single tuning knob.
"""

from __future__ import annotations

import threading
import time

import requests

YAW_LIMITS = (-81.0, 81.0)    # + is left
PITCH_LIMITS = (-40.0, 26.0)  # - is up


def _clamp(value: float, limits) -> float:
    return max(limits[0], min(limits[1], value))


class HeadController:
    """Keeps an aim angle, nudged by track(), and sends it to Misty from its own thread."""

    def __init__(self, misty_ip: str, start_yaw: float, start_pitch: float,
                 gain: float = 1.5, velocity: float = 60.0, rate_hz: float = 10.0,
                 deadband_deg: float = 2.0, max_rate_dps: float = 40.0,
                 min_step_deg: float = 0.8):
        self._url = f"http://{misty_ip}/api/head"
        self._session = requests.Session()
        self._gain = gain                # Fraction of the error corrected per second.
        self._velocity = velocity        # Misty head velocity (percent) while following.
        self._period = 1.0 / rate_hz
        self._deadband = deadband_deg    # Errors smaller than this are left alone (no jitter).
        self._max_rate = max_rate_dps    # Aim never moves faster than the head can follow.
        self._min_step = min_step_deg    # Don't send commands for smaller aim changes.
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()  # One HTTP session, used from two threads.
        self._aim = (start_yaw, start_pitch)
        self._sent = (start_yaw, start_pitch)
        # Where the head is believed to be: a linear ramp (t0, from, t1, to). Only used to
        # pick the starting aim when a face is found in the middle of an idle-scan move.
        now = time.monotonic()
        self._ramp = (now, self._aim, now, self._aim)
        self._following = False
        self.sent = 0
        self.failed = 0
        self._last_error = None
        self._running = True
        self._thread = threading.Thread(target=self._run, name="head-control", daemon=True)
        self._thread.start()

    # ----- following ---------------------------------------------------------------------
    def track(self, error_yaw: float, error_pitch: float, dt: float) -> None:
        """Nudge the aim towards a face seen `error` degrees from the image centre.

        Call once per frame in which the face was actually detected, with dt = seconds
        since the previous such frame. Never call it with a stale or predicted error:
        integrating an old error is what makes a head run away.
        """
        dt = max(0.0, min(dt, 0.1))
        limit = self._max_rate * dt
        with self._lock:
            if not self._following:
                self._aim = self._estimate(time.monotonic())
                self._following = True
            steps = []
            for error in (error_yaw, error_pitch):
                if abs(error) < self._deadband:
                    steps.append(0.0)
                else:
                    steps.append(max(-limit, min(limit, self._gain * dt * error)))
            self._aim = (_clamp(self._aim[0] + steps[0], YAW_LIMITS),
                         _clamp(self._aim[1] + steps[1], PITCH_LIMITS))

    def release(self) -> None:
        """Stop following (the aim is re-seeded from the head position on the next track)."""
        with self._lock:
            self._following = False

    @property
    def aim(self):
        with self._lock:
            return self._aim

    # ----- scripted moves ----------------------------------------------------------------
    def move_to(self, yaw: float, pitch: float, duration: float) -> None:
        """Stop following and move the head over `duration` seconds (e.g. idle scanning)."""
        self.release()
        with self._send_lock:
            self._post(yaw, pitch, duration=duration)

    def _estimate(self, now: float):
        t0, start, t1, end = self._ramp
        if now >= t1 or t1 <= t0:
            return end
        f = max(0.0, (now - t0) / (t1 - t0))
        return (start[0] + (end[0] - start[0]) * f, start[1] + (end[1] - start[1]) * f)

    # ----- sending -----------------------------------------------------------------------
    def _post(self, yaw: float, pitch: float, duration=None) -> None:
        yaw, pitch = _clamp(yaw, YAW_LIMITS), _clamp(pitch, PITCH_LIMITS)
        body = {"pitch": pitch, "roll": 0, "yaw": yaw, "units": "degrees"}
        body.update({"velocity": self._velocity} if duration is None else {"duration": duration})
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
        now = time.monotonic()
        with self._lock:
            start = self._estimate(now)
            self._sent = (yaw, pitch)
            self._ramp = ((now, start, now + duration, (yaw, pitch)) if duration
                          else (now, (yaw, pitch), now, (yaw, pitch)))

    def _run(self) -> None:
        while self._running:
            started = time.monotonic()
            with self._lock:
                aim = self._aim if self._following else None
                sent = self._sent
            if aim is not None and max(abs(aim[0] - sent[0]),
                                       abs(aim[1] - sent[1])) >= self._min_step:
                with self._send_lock:
                    with self._lock:
                        still_following = self._following
                    if still_following:  # A move_to() may have taken over meanwhile.
                        self._post(*aim)
            time.sleep(max(0.0, self._period - (time.monotonic() - started)))

    def close(self) -> None:
        self._running = False
        self._thread.join(timeout=2.0)
