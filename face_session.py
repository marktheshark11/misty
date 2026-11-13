"""Shared PC-side face-following session used by main.py, idle_mode.py and follow_face.py.

Misty streams its camera over RTSP; this PC detects faces (YuNet), keeps a lock on one
person by appearance (SFace), and drives Misty's head. While a session is open, Misty's
own camera features (TakePicture, on-board face detection) are unavailable, so use
`jpeg()` for pictures instead.

Typical use:

    with FaceSession(misty, preview=True) as session:
        while True:
            result = session.step()   # Processes one frame; head and eyes follow by themselves.
            if result and result.state == TRACKING: ...
"""

from __future__ import annotations

import concurrent.futures
import time

import cv2

try:  # Errors only; hides harmless DNN backend warnings.
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except AttributeError:
    pass

from face_models import model_path
from face_tracker import COASTING, SEARCHING, TRACKING, FaceTracker, TrackerConfig
from head_controller import HeadController, HeadPose
from misty_stream import (LatestFrameReader, port_is_open, start_misty_stream,
                          stop_misty_stream)
from settings import (CAMERA_HFOV, HEAD_MAX_SPEED, MISTY_IP, STREAM_BITRATE, STREAM_FPS, STREAM_HEIGHT,
                      STREAM_LATENCY, STREAM_PORT, STREAM_WIDTH)

NEUTRAL_PITCH = -15  # Negative pitch tilts Misty's head upward.
IDLE_IMAGE = "e_defaultcontent.jpg"
FACE_IMAGE = "e_joy.jpg"
_STATE_COLORS = {TRACKING: (0, 200, 0), COASTING: (0, 200, 255)}


class StopRequested(Exception):
    """Raised by step() when the user presses q in the preview window."""


def is_following(result) -> bool:
    """True while a face is locked, including brief gaps the tracker is coasting through."""
    return result is not None and result.state in (TRACKING, COASTING)


class FaceSession:
    def __init__(self, misty, preview: bool = False):
        self.misty = misty
        self.preview = preview
        self.tracker = FaceTracker(model_path("detector"), model_path("recognizer"),
                                   TrackerConfig(hfov_deg=CAMERA_HFOV))
        self.result = None
        self._commands = concurrent.futures.ThreadPoolExecutor(max_workers=1)  # Eyes.
        self._reader = self._head = self._pose = None
        self._eyes_show_face = None
        self._frame = None
        self._seq = 0
        self._stats = [time.monotonic(), 0, 0.0, 0, 0]
        self._fps = self._proc_ms = 0.0

    # ----- lifecycle ---------------------------------------------------------------------
    def __enter__(self) -> "FaceSession":
        try:
            self._start()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _start(self) -> None:
        misty = self.misty
        self.set_eyes(False)
        misty.move_head(pitch=NEUTRAL_PITCH, roll=0, yaw=0, duration=1.0,
                        units="degrees").raise_for_status()

        print("Starting Misty's video stream (on-board vision is paused while streaming)...")
        start_misty_stream(misty, STREAM_PORT, STREAM_WIDTH, STREAM_HEIGHT,
                           STREAM_FPS, STREAM_BITRATE)
        # Misty's RTSP server takes a moment to come up after the start command.
        port_deadline = time.monotonic() + 15
        while not port_is_open(MISTY_IP, STREAM_PORT):
            if time.monotonic() > port_deadline:
                raise RuntimeError(
                    f"Misty accepted the stream command, but nothing is listening on "
                    f"{MISTY_IP}:{STREAM_PORT}. Try restarting Misty, or set MISTY_STREAM_PORT "
                    f"to another port (e.g. 1935).")
            time.sleep(0.5)
        self._reader = LatestFrameReader(f"rtsp://{MISTY_IP}:{STREAM_PORT}")

        self._pose = HeadPose(misty, start_yaw=0.0, start_pitch=NEUTRAL_PITCH)
        self._head = HeadController(MISTY_IP, self._pose, 0.0, NEUTRAL_PITCH,
                                    lead_time=STREAM_LATENCY, max_speed_dps=HEAD_MAX_SPEED)

        print("Waiting for video...")
        deadline = time.monotonic() + 75  # Allows a full TCP and a UDP attempt.
        while self._reader.wait_for_frame(0, timeout=1.0) is None:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"Port {STREAM_PORT} on Misty is open, but no video arrived over TCP or UDP. "
                    f"If you're on Windows, allow Python through the firewall for UDP.")
        print("Video is running." + (" Press q in the preview window to stop." if self.preview else ""))

    def close(self) -> None:
        if self._head is not None:
            self._head.close()
        if self._reader is not None:
            self._reader.close()
        stop_misty_stream(self.misty)
        self.misty.unregister_all_events()
        self._commands.shutdown(wait=True)
        for call, what in (
            (lambda: self.misty.move_head(pitch=NEUTRAL_PITCH, roll=0, yaw=0, duration=1.0,
                                          units="degrees"), "return the head to neutral"),
            (lambda: self.misty.display_image(IDLE_IMAGE), "restore idle eyes"),
        ):
            try:
                call().raise_for_status()
            except Exception as error:
                print(f"Could not {what}: {error}")
        if self.preview:
            cv2.destroyAllWindows()

    # ----- per-frame work ----------------------------------------------------------------
    def step(self, timeout: float = 2.0):
        """Process the newest frame. Returns the TrackResult, or None if no frame arrived."""
        item = self._reader.wait_for_frame(self._seq, timeout=timeout)
        if item is None:
            self._head.clear_target()
            return None
        frame, received, self._seq = item
        started = time.monotonic()

        captured = received - STREAM_LATENCY
        head_yaw, head_pitch = self._pose.at(captured)
        result = self.tracker.update(frame, head_yaw, head_pitch, t=captured,
                                     pose_uncertainty=self._pose.uncertainty(captured))
        if result.state == SEARCHING:
            self._head.clear_target()
        else:
            self._head.set_target(result.yaw, result.pitch, result.vel_yaw, result.vel_pitch)
        self.set_eyes(is_following(result))
        self._frame, self.result = frame, result

        self._update_stats(result, time.monotonic() - started)
        if self.preview and self._draw_preview(frame, result) == ord("q"):
            raise StopRequested()
        return result

    def jpeg(self, quality: int = 85) -> bytes:
        """The most recently processed frame as JPEG (replaces take_picture while streaming)."""
        ok, data = cv2.imencode(".jpg", self._frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError("Could not encode the camera frame as JPEG")
        return data.tobytes()

    def move_head(self, yaw: float, pitch: float, duration: float) -> None:
        """Move the head while not following (e.g. idle scanning or returning to neutral)."""
        self._head.move_to(yaw, pitch, duration)

    def set_eyes(self, face_seen: bool) -> None:
        if self._eyes_show_face == face_seen:
            return
        self._eyes_show_face = face_seen
        self._commands.submit(self.misty.display_image, FACE_IMAGE if face_seen else IDLE_IMAGE)

    # ----- diagnostics -------------------------------------------------------------------
    def _update_stats(self, result, processing: float) -> None:
        stats = self._stats
        stats[1] += 1
        stats[2] += processing
        now = time.monotonic()
        if now - stats[0] >= 5.0:
            self._fps = stats[1] / (now - stats[0])
            self._proc_ms = 1000 * stats[2] / stats[1]
            source = "measured" if self._pose.measured_is_live() else "estimated"
            head_yaw, head_pitch = self._pose.at(now)
            sent, failed = self._head.sent - stats[3], self._head.failed - stats[4]
            face = (f"face at yaw {result.yaw:+.0f} pitch {result.pitch:+.0f}"
                    if result.yaw is not None else "no face")
            print(f"[{self._fps:.1f} fps, {self._proc_ms:.1f} ms/frame, {result.state}, {face}; "
                  f"head ({source}) yaw {head_yaw:+.0f} pitch {head_pitch:+.0f}; "
                  f"{sent} head cmds" + (f", {failed} FAILED" if failed else "") + "]")
            self._stats = [now, 0, 0.0, self._head.sent, self._head.failed]

    def _draw_preview(self, frame, result) -> int:
        frame = frame.copy()
        for face in result.faces:
            x, y, w, h = face.box
            is_target = face is result.target
            color = _STATE_COLORS.get(result.state, (0, 0, 255)) if is_target else (160, 160, 160)
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 3 if is_target else 1)
            if face.similarity is not None:
                cv2.putText(frame, f"{face.similarity:.2f}", (x, y - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        cv2.putText(frame, f"{result.state}  {self._fps:.0f} fps  {self._proc_ms:.0f} ms", (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.imshow("Misty face tracking", frame)
        return cv2.waitKey(1) & 0xFF
