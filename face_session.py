"""Shared PC-side face-following session used by main.py, idle_mode.py and follow_face.py.

Misty streams its camera over RTSP; this PC detects faces (YuNet), keeps a lock on one
person by appearance (SFace), and nudges Misty's head so that face moves to the centre
of the picture (plain image-error feedback, see head_controller.py). While a session is
open, Misty's own camera features (TakePicture, on-board face detection) are
unavailable, so use `jpeg()` for pictures instead.

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
from face_tracker import COASTING, SEARCHING, TRACKING, FaceTracker, TrackerConfig, TrackResult
from head_controller import HeadController
from misty_stream import (LatestFrameReader, port_is_open, start_misty_stream,
                          stop_misty_stream)
from settings import (CAMERA_FOV, HEAD_VELOCITY, MISTY_IP, STREAM_BITRATE, STREAM_FPS, STREAM_HEIGHT,
                      STREAM_PORT, STREAM_ROTATE, STREAM_TRANSPORT, STREAM_WIDTH, TRACK_GAIN)

NEUTRAL_PITCH = -15  # Negative pitch tilts Misty's head upward.
IDLE_IMAGE = "e_defaultcontent.jpg"
FACE_IMAGE = "e_joy.jpg"
_STATE_COLORS = {TRACKING: (0, 200, 0), COASTING: (0, 200, 255)}
# Clockwise rotation (degrees) -> OpenCV rotate code.
_ROTATIONS = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
              270: cv2.ROTATE_90_COUNTERCLOCKWISE}
_ORIENTATION_VOTES = 5


class StopRequested(Exception):
    """Raised by step() when the user presses q in the preview window."""


def is_following(result) -> bool:
    """True while a face is locked, including brief gaps the tracker is coasting through."""
    return result is not None and result.state in (TRACKING, COASTING)


class FaceSession:
    def __init__(self, misty, preview: bool = False, follow: bool = True):
        """follow=False only detects and locks onto a face: the head and eyes are left
        to the caller (main_remote.py drives them the same way as the on-board version)."""
        self.misty = misty
        self.preview = preview
        self.follow = follow
        self.tracker = FaceTracker(model_path("detector"), model_path("recognizer"),
                                   TrackerConfig(hfov_deg=CAMERA_FOV))
        self.result = None
        self._commands = concurrent.futures.ThreadPoolExecutor(max_workers=1)  # Eyes.
        self._reader = self._head = None
        # Clockwise degrees to turn each frame upright; None until worked out ("auto").
        self._rotation = None if STREAM_ROTATE == "auto" else int(STREAM_ROTATE)
        self._votes = dict.fromkeys(_ROTATIONS, 0)
        self._last_tracked = 0.0
        self._eyes_show_face = None
        self._frame = None
        # The newest frame in which the tracked person was actually seen, with their
        # face box; None while nobody is being tracked.
        self._tracked_view = None
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
        self._reader = LatestFrameReader(f"rtsp://{MISTY_IP}:{STREAM_PORT}", STREAM_TRANSPORT)
        self._head = HeadController(MISTY_IP, 0.0, NEUTRAL_PITCH, gain=TRACK_GAIN,
                                    velocity=HEAD_VELOCITY)

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
            return None
        frame, received, self._seq = item
        started = time.monotonic()

        if self._rotation is None:
            result = self._find_orientation(frame)
        else:
            code = _ROTATIONS[self._rotation]
            if code is not None:
                frame = cv2.rotate(frame, code)
            # Head angles are passed as 0, so the result is the face's angle from the
            # centre of the picture: exactly the error the head has to remove.
            result = self.tracker.update(frame, 0.0, 0.0, t=received)
        if not self.follow:
            pass
        elif result.state == SEARCHING:
            self._head.release()
        elif result.target is not None:
            # Only frames where the face was really seen move the head; while coasting
            # or waiting for the person to reappear, the head simply holds still.
            self._head.track(result.yaw, result.pitch, received - self._last_tracked)
            self._last_tracked = received
        if self.follow:
            self.set_eyes(is_following(result))
        self._frame, self.result = frame, result
        if result.target is not None:
            self._tracked_view = (frame, result.target.box)
        elif result.state == SEARCHING:
            self._tracked_view = None

        self._update_stats(result, time.monotonic() - started)
        if self.preview and self._draw_preview(frame, result) == ord("q"):
            raise StopRequested()
        return result

    def _find_orientation(self, frame) -> TrackResult:
        """Work out which way up the stream is: Misty's docs say it arrives rotated 90
        degrees counterclockwise, but that is cheap to check rather than assume. The
        face detector only fires confidently on upright faces, so the rotation that
        keeps giving the best face wins."""
        best_score, best_rotation = 0.0, None
        for rotation, code in _ROTATIONS.items():
            turned = frame if code is None else cv2.rotate(frame, code)
            for face in self.tracker.detect(turned, 0.0, 0.0):
                if face.row[14] > best_score:
                    best_score, best_rotation = float(face.row[14]), rotation
        if best_rotation is not None:
            self._votes[best_rotation] += 1
            if self._votes[best_rotation] >= _ORIENTATION_VOTES:
                self._rotation = best_rotation
                print(f"Stream orientation: rotating frames {best_rotation} degrees clockwise "
                      f"(set MISTY_STREAM_ROTATE={best_rotation} to skip this check).")
        return TrackResult(SEARCHING)

    def tracked_view(self):
        """The newest frame showing the tracked person, and their face box.

        Returns (frame, (x, y, width, height)), or None while nobody is being tracked.
        """
        return self._tracked_view

    def jpeg(self, quality: int = 85) -> bytes:
        """The most recently processed frame as JPEG (replaces take_picture while streaming)."""
        if self._frame is None:
            raise RuntimeError("No camera frame has arrived yet")
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
            aim_yaw, aim_pitch = self._head.aim
            sent, failed = self._head.sent - stats[3], self._head.failed - stats[4]
            face = (f"face off-centre by yaw {result.yaw:+.0f} pitch {result.pitch:+.0f}"
                    if result.yaw is not None else "no face")
            head = (f"; head aim yaw {aim_yaw:+.0f} pitch {aim_pitch:+.0f}; {sent} head cmds"
                    + (f", {failed} FAILED" if failed else "")) if self.follow else ""
            print(f"[{self._fps:.1f} fps, {self._proc_ms:.1f} ms/frame, {result.state}, {face}{head}]")
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
        h, w = frame.shape[:2]
        cv2.drawMarker(frame, (w // 2, h // 2), (255, 255, 255), cv2.MARKER_CROSS, 16, 1)
        cv2.putText(frame, f"{result.state}  {self._fps:.0f} fps  {self._proc_ms:.0f} ms", (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.imshow("Misty face tracking", frame)
        return cv2.waitKey(1) & 0xFF
