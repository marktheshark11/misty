"""Face detection on this computer from Misty's camera stream (detection only, no identity).

Used by approach_face.py / main.py when FACE_SOURCE=laptop (the default). It sends
face events shaped like Misty's own FaceRecognition events (bearing, elevation,
distance), so the behaviours work the same with either source.

Live viewer on its own:
    python stream_faces.py            # Misty at MISTY_IP
    python stream_faces.py --webcam   # this computer's camera, no robot needed
Press q or Esc to quit. Misty's normal camera service is turned back on when it stops.
"""
import argparse
import math
import os
import threading
import time
import urllib.request
from pathlib import Path

import cv2

MODEL_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
MODEL_PATH = Path(__file__).with_name("face_detection_yunet_2023mar.onnx")

# Tuning knobs
STREAM_PORT = 1936
WIDTH, HEIGHT, FPS = 640, 480, 30
SCORE_THRESHOLD = 0.7  # lower finds more faces (and more false ones)
HOLD_SECONDS = 0.3     # viewer only: keep the last box this long when one frame misses
ROTATE = cv2.ROTATE_90_CLOCKWISE  # Misty's stream arrives sideways; None if yours is upright
CAMERA_HFOV = 70.0     # degrees, field of view across the camera's long side
FACE_WIDTH_CM = 15.0   # real width of a detected face box; scales the distance guess
MAX_JUMP = 1.5         # a face counts as the same one if it moved < this many face widths per frame
LOCK_TIMEOUT = 0.7     # seconds the followed face can be missing before another face is picked

# Read RTSP over TCP: UDP packets get dropped on busy Wi-Fi and smear the picture.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")


class LatestFrame:
    """Reads frames on a thread and keeps only the newest, so the view never lags behind."""

    def __init__(self, source):
        self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open video source {source!r}")
        self.frame = None
        self.running = True
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        # ponytail: no reconnect; if the stream drops, frames stop, no face events
        # arrive and the behaviours stop driving. Add a reopen here if Wi-Fi is flaky.
        while self.running:
            ok, frame = self.cap.read()
            if ok:
                self.frame = frame
            else:
                time.sleep(0.05)

    def close(self):
        self.running = False
        self.cap.release()


def load_detector():
    if not MODEL_PATH.exists():
        print("Downloading the YuNet face model (~230 KB)...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    return cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (WIDTH, HEIGHT), SCORE_THRESHOLD)


def detect(detector, frame):
    """Return YuNet rows (x, y, w, h, ..., score) for the faces in `frame`."""
    h, w = frame.shape[:2]
    detector.setInputSize((w, h))
    faces = detector.detect(frame)[1]
    return [] if faces is None else list(faces)


class FaceLock:
    """Keeps following one face: the one nearest where the followed face was last seen.

    A new face (the biggest) is picked only after the followed one has been missing for
    LOCK_TIMEOUT, so a second person walking past or getting closer doesn't steal focus.
    """

    def __init__(self):
        self.box = None
        self.seen = 0.0

    def pick(self, faces, now):
        """Return the followed face's row from this frame's faces, or None."""
        if self.box is not None and now - self.seen <= LOCK_TIMEOUT:
            x, y, w, h = self.box
            cx, cy = x + w / 2, y + h / 2

            def jump(f):
                return math.hypot(f[0] + f[2] / 2 - cx, f[1] + f[3] / 2 - cy) / max(w, 1)

            near = [f for f in faces if jump(f) < MAX_JUMP]
            if not near:
                return None  # followed face missed this frame: wait for it, don't switch
            chosen = min(near, key=jump)
        elif faces:
            chosen = max(faces, key=lambda f: f[2] * f[3])
        else:
            return None
        self.box, self.seen = tuple(map(float, chosen[:4])), now
        return chosen


def face_event(box, frame_w, frame_h):
    """Turn a face box into a Misty-style FaceRecognition event.

    bearing: degrees, positive = face left of centre (positive angular velocity turns left).
    elevation: degrees, positive = face below centre (approach_face raises pitch = head down).
    distance: centimetres, like Misty's raw Distance (approach_face scales it by 0.01).
    """
    x, y, w, h = map(float, box)
    # Same lens whichever way the picture is turned, so measure from the long side.
    focal = (max(frame_w, frame_h) / 2) / math.tan(math.radians(CAMERA_HFOV) / 2)
    cx, cy = x + w / 2, y + h / 2
    return {"message": {
        "bearing": math.degrees(math.atan((frame_w / 2 - cx) / focal)),
        "elevation": math.degrees(math.atan((cy - frame_h / 2) / focal)),
        "distance": FACE_WIDTH_CM * focal / max(w, 1),
    }}


def start_misty_stream(misty):
    """Hand Misty's camera to the AV streaming service and serve it as RTSP."""
    misty.stop_av_streaming()  # in case an earlier run left it on
    misty.disable_camera_service()
    misty.enable_av_streaming_service()
    r = misty.start_av_streaming(url=f"rtspd:{STREAM_PORT}", width=WIDTH, height=HEIGHT, frameRate=FPS)
    print("Start AV streaming:", r.status_code, r.text[:200])
    r.raise_for_status()


def stop_misty_stream(misty):
    """Give the camera back to Misty so the dashboard and on-robot face events work again."""
    for call in (misty.stop_av_streaming, misty.disable_av_streaming_service, misty.enable_camera_service):
        try:
            call()
        except Exception as e:
            print(f"{call.__name__} failed: {e}")


def open_stream(url, attempts=10):
    """Misty takes a few seconds to start serving, so retry the connection."""
    for i in range(attempts):
        try:
            return LatestFrame(url)
        except RuntimeError:
            print(f"Waiting for {url} ({i + 1}/{attempts})...")
            time.sleep(1)
    raise RuntimeError(f"Misty's stream never came up at {url}")


def open_misty(misty):
    from settings import MISTY_IP

    start_misty_stream(misty)
    try:
        return open_stream(f"rtsp://{MISTY_IP.split(':')[0]}:{STREAM_PORT}")
    except Exception:
        stop_misty_stream(misty)
        raise


class LaptopFaces:
    """Streams Misty's camera and calls `on_face(event)` for the followed face in each new frame."""

    def __init__(self, misty, on_face):
        self.misty, self.on_face = misty, on_face
        self.stream = None
        self.running = False

    def start(self):
        detector = load_detector()
        self.stream = open_misty(self.misty)
        self.running = True
        threading.Thread(target=self._loop, args=(detector,), daemon=True).start()
        print("Laptop face detection running on Misty's stream.")

    def _loop(self, detector):
        last = None
        lock = FaceLock()
        while self.running:
            frame = self.stream.frame
            # Only new frames: a frozen stream must stop the events, not repeat the last face.
            if frame is None or frame is last:
                time.sleep(0.005)
                continue
            last = frame
            if ROTATE is not None:
                frame = cv2.rotate(frame, ROTATE)
            target = lock.pick(detect(detector, frame), time.monotonic())
            if target is not None:
                self.on_face(face_event(target[:4], frame.shape[1], frame.shape[0]))

    def photo(self):
        """Latest frame as JPEG bytes (Misty's own take_picture is off while streaming)."""
        deadline = time.monotonic() + 5
        while self.stream.frame is None and time.monotonic() < deadline:
            time.sleep(0.05)
        frame = self.stream.frame
        if frame is None:
            raise RuntimeError("No frame from Misty's stream yet")
        if ROTATE is not None:
            frame = cv2.rotate(frame, ROTATE)
        return cv2.imencode(".jpg", frame)[1].tobytes()

    def stop(self):
        self.running = False
        if self.stream:
            self.stream.close()
        stop_misty_stream(self.misty)


def view(webcam):
    detector = load_detector()
    misty = None
    if webcam:
        stream = LatestFrame(0)
    else:
        from mistyPy.Robot import Robot
        from settings import MISTY_IP

        misty = Robot(MISTY_IP)
        stream = open_misty(misty)

    lock = FaceLock()
    last_faces, target, last_seen, last_frame_time = [], None, 0.0, time.time()
    try:
        while True:
            frame = stream.frame
            if frame is None:
                if cv2.waitKey(10) in (ord("q"), 27):
                    break
                continue
            if ROTATE is not None:
                frame = cv2.rotate(frame, ROTATE)

            faces = detect(detector, frame)
            now = time.time()
            picked = lock.pick(faces, time.monotonic())
            if faces:
                last_faces, target, last_seen = faces, picked, now
            elif now - last_seen > HOLD_SECONDS:
                last_faces, target = [], None

            # Red = the face Misty follows, grey = other faces it ignores.
            for f in last_faces:
                x, y, bw, bh = map(int, f[:4])
                followed = target is not None and f is target
                color = (0, 0, 255) if followed else (160, 160, 160)
                cv2.rectangle(frame, (x, y), (x + bw, y + bh), color, 2)
                if followed:
                    m = face_event(f[:4], frame.shape[1], frame.shape[0])["message"]
                    cv2.putText(frame, f"{f[14]:.2f}  {m['distance'] / 100:.1f} m  {m['bearing']:+.0f} deg",
                                (x, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

            fps = 1 / max(now - last_frame_time, 1e-6)
            last_frame_time = now
            cv2.putText(frame, f"{fps:.0f} fps  faces: {len(last_faces)}", (10, 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            cv2.imshow("Misty faces", frame)
            if cv2.waitKey(1) in (ord("q"), 27):
                break
    except KeyboardInterrupt:
        pass
    finally:
        stream.close()
        cv2.destroyAllWindows()
        if misty is not None:
            stop_misty_stream(misty)


def self_check():
    centre = face_event((300, 220, 40, 40), 640, 480)["message"]
    assert abs(centre["bearing"]) < 0.01 and abs(centre["elevation"]) < 0.01, "centred face = 0, 0"
    left = face_event((0, 220, 40, 40), 640, 480)["message"]
    assert left["bearing"] > 0, "face on the left of the picture = positive bearing"
    low = face_event((300, 400, 40, 40), 640, 480)["message"]
    assert low["elevation"] > 0, "face low in the picture = positive elevation"
    near, far = (face_event((300, 220, w, w), 640, 480)["message"]["distance"] for w in (200, 50))
    assert near < far, "bigger face = closer"
    landscape = face_event((0, 220, 40, 40), 640, 480)["message"]["bearing"]
    portrait = face_event((0, 300, 40, 40), 480, 640)["message"]["bearing"]
    assert portrait < landscape, "portrait picture is narrower, so its edge is a smaller angle"

    lock = FaceLock()
    a, b = (100, 100, 50, 50), (400, 100, 80, 80)  # b is bigger
    assert lock.pick([a, b], 0.0) == b, "no lock yet: pick the biggest face"
    moved = (420, 110, 80, 80)
    assert lock.pick([a, moved], 0.1) == moved, "follows the same face as it moves"
    big_stranger = (100, 100, 200, 200)
    assert lock.pick([big_stranger, (430, 110, 80, 80)], 0.2)[0] == 430, "a bigger face doesn't steal focus"
    assert lock.pick([big_stranger], 0.3) is None, "followed face missing: wait, don't switch"
    assert lock.pick([big_stranger], 0.3 + LOCK_TIMEOUT + 0.1) == big_stranger, "switches after LOCK_TIMEOUT"
    assert FaceLock().pick([], 0.0) is None


if __name__ == "__main__":
    self_check()
    parser = argparse.ArgumentParser()
    parser.add_argument("--webcam", action="store_true", help="use this computer's camera instead of Misty")
    view(parser.parse_args().webcam)
