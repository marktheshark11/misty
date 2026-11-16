"""Face data for the approach from this PC's face tracker instead of Misty's on-board one.

RemoteFaceApproach is a drop-in replacement for FaceApproach: the turning, driving,
head-pitch and stopping logic is inherited unchanged. Only where the face's bearing,
elevation and distance come from differs: Misty streams her camera to this PC, which
detects faces and keeps a lock on one person (see face_session.py).
"""

import time

from approach_face import DISTANCE_SMOOTHING, MAX_ESTIMATE, FaceApproach
from face_session import FaceSession
from misty_camera import restore_face, show_camera_face
from person_photo import save_tracked_photo

# Misty's on-board face events report bearing and elevation as roughly -13..13 from one
# edge of the picture to the other, and the approach gains are tuned for that. The PC
# tracker measures real degrees (about -35..35), so they are scaled down to match.
ANGLE_SCALE = 13.0 / 35.0
# Typical width of a detected face box, in metres. Distance is estimated from how wide
# the face appears; if the printed distance reads too far, lower this, and vice versa.
FACE_WIDTH_M = 0.15
# Misty's own camera takes about this long to return a picture, during which the
# on-board version shows the camera face. A frame from the stream is instant, so the
# camera face is held this long to look the same.
PHOTO_FACE_TIME = 1.0


class RemoteFaceApproach(FaceApproach):
    def __init__(self, misty, preview: bool = False):
        super().__init__(misty)
        # follow=False: the session only watches. The head and wheels are driven by the
        # inherited approach logic, exactly as with on-board detection.
        self.session = FaceSession(misty, preview=preview, follow=False)
        self._started = False

    def start(self) -> None:
        self._started = True
        self.session.__enter__()

    def stop(self) -> None:
        if self._started:
            self._started = False
            self.session.close()

    def wait(self, seconds: float) -> None:
        """Pause the main loop while processing the camera frames that arrive meanwhile."""
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._on_result(self.session.step(timeout=remaining))

    def take_picture(self) -> bytes:
        """Return a JPEG of the tracked person, cut out of the stream.

        Raises RuntimeError, without taking a photo, if nobody is being tracked.

        The person is marked with a rectangle and the picture is cropped to it, so the
        compliment is about them even when other people are in view. Both the marked
        picture and the crop are saved in the recent_images folder (see person_photo.py).
        """
        # No tracked person means no photo: a picture of the whole scene would bring back
        # the problem of the model not knowing who to compliment. Raising here makes the
        # greeting report the failure and the main loop return to scanning.
        view = self.session.tracked_view()
        if view is None:
            raise RuntimeError("nobody is being tracked, so no photo was taken")
        # Misty's camera is busy streaming, so the photo comes from the stream. The face
        # changes are the same ones misty_camera.take_picture shows.
        show_camera_face(self.misty)
        print("Taking picture...")
        photo = save_tracked_photo(*view)
        print("Saved the tracked person's picture in recent_images.")
        time.sleep(PHOTO_FACE_TIME)
        restore_face(self.misty)
        return photo

    def _on_result(self, result) -> None:
        if result is None or result.target is None:
            return  # No frame, or the person wasn't actually seen in this one.
        frame = self.session._frame
        metres = min(MAX_ESTIMATE, FACE_WIDTH_M * self.session.tracker.focal_px(frame.shape)
                     / max(1.0, result.target.width_px))
        now = time.monotonic()
        with self.lock:
            face = self.face
            face["bearing"] = result.yaw * ANGLE_SCALE
            face["elevation"] = result.pitch * ANGLE_SCALE
            face["seen"] = now
            previous = face["distance"]
            face["distance"] = (metres if previous is None
                                else previous + DISTANCE_SMOOTHING * (metres - previous))
            face["raw_distance"] = round(metres, 2)
            face["distance_seen"] = now
