"""Stream Misty's camera to this PC and keep a lock on one face in it.

Misty streams its camera over RTSP; this PC detects faces (YuNet) and keeps a lock on
one person by appearance (SFace).
"""

from __future__ import annotations

import time

import cv2

try:
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except AttributeError:
    pass

from face_models import model_path
from face_tracker import COASTING, SEARCHING, TRACKING, FaceTracker, TrackerConfig, TrackResult
from face_utils import IDLE_IMAGE, best_effort, center_head
from misty_stream import (LatestFrameReader, port_is_open, start_misty_stream,
                          stop_misty_stream)
from settings import (CAMERA_FOV, MISTY_IP, STREAM_BITRATE, STREAM_FPS, STREAM_HEIGHT,
                      STREAM_PORT, STREAM_ROTATE, STREAM_TRANSPORT, STREAM_WIDTH)

_STATE_COLORS = {TRACKING: (0, 200, 0), COASTING: (0, 200, 255)}
_ROTATIONS = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
              270: cv2.ROTATE_90_COUNTERCLOCKWISE}
_ORIENTATION_VOTES = 5


class StopRequested(Exception):
    pass


class FaceSession:
    def __init__(self, misty, preview: bool = False):
        self.misty = misty
        self.preview = preview
        self.tracker = FaceTracker(model_path("detector"), model_path("recognizer"),
                                   TrackerConfig(hfov_deg=CAMERA_FOV))
        self.result = None
        self._reader = None
        self._rotation = None if STREAM_ROTATE == "auto" else int(STREAM_ROTATE)
        self._votes = dict.fromkeys(_ROTATIONS, 0)
        self._frame = None
        self._tracked_view = None
        self._seq = 0
        self._stats = [time.monotonic(), 0, 0.0]  # Since when, frames, processing seconds.
        self._fps = self._proc_ms = 0.0

    # ----- lifecycle ---------------------------------------------------------------------
    def start(self) -> None:
        try:
            self._start()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> "FaceSession":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _start(self) -> None:
        misty = self.misty
        center_head(misty).raise_for_status()

        print("Starting Misty's video stream (on-board vision is paused while streaming)...")
        start_misty_stream(misty, STREAM_PORT, STREAM_WIDTH, STREAM_HEIGHT,
                           STREAM_FPS, STREAM_BITRATE)
        port_deadline = time.monotonic() + 15
        while not port_is_open(MISTY_IP, STREAM_PORT):
            if time.monotonic() > port_deadline:
                raise RuntimeError(
                    f"Misty accepted the stream command, but nothing is listening on "
                    f"{MISTY_IP}:{STREAM_PORT}. Try restarting Misty, or set MISTY_STREAM_PORT "
                    f"to another port (e.g. 1935).")
            time.sleep(0.5)
        self._reader = LatestFrameReader(f"rtsp://{MISTY_IP}:{STREAM_PORT}", STREAM_TRANSPORT)

        print("Waiting for video...")
        deadline = time.monotonic() + 75  # Allows a full TCP and a UDP attempt.
        while self._reader.wait_for_frame(0, timeout=1.0) is None:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"Port {STREAM_PORT} on Misty is open, but no video arrived over TCP or UDP. "
                    f"If you're on Windows, allow Python through the firewall for UDP.")
        print("Video is running." + (" Press q in the preview window to stop." if self.preview else ""))

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
        stop_misty_stream(self.misty)
        best_effort(
            "return the head to neutral",
            lambda: center_head(self.misty).raise_for_status(),
        )
        best_effort(
            "restore idle eyes",
            lambda: self.misty.display_image(IDLE_IMAGE).raise_for_status(),
        )
        if self.preview:
            cv2.destroyAllWindows()

    # ----- per-frame work ----------------------------------------------------------------
    def step(self, timeout: float = 2.0):
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
            result = self.tracker.update(frame, 0.0, 0.0, t=received)
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
        """Work out which way up the stream is."""
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

    @property
    def frame(self):
        return self._frame

    def tracked_view(self):
        return self._tracked_view

    # ----- diagnostics -------------------------------------------------------------------
    def _update_stats(self, result, processing: float) -> None:
        stats = self._stats
        stats[1] += 1
        stats[2] += processing
        now = time.monotonic()
        if now - stats[0] >= 5.0:
            self._fps = stats[1] / (now - stats[0])
            self._proc_ms = 1000 * stats[2] / stats[1]
            face = (f"face off-centre by yaw {result.yaw:+.0f} pitch {result.pitch:+.0f}"
                    if result.yaw is not None else "no face")
            print(f"[{self._fps:.1f} fps, {self._proc_ms:.1f} ms/frame, {result.state}, {face}]")
            self._stats = [now, 0, 0.0]

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
