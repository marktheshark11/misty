"""Face data for the approach from this PC's face tracker instead of Misty's on-board one.

RemoteFaceApproach is a drop-in replacement for FaceApproach: the turning, driving,
head-pitch and stopping logic is inherited unchanged. Only where the face's bearing,
elevation and distance come from differs: Misty streams her camera to this PC, which
detects faces and keeps a lock on one person (see face_session.py).
"""

import time

from approach_face import MAX_ESTIMATE, FaceApproach
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
        self.session = FaceSession(misty, preview=preview)
        self._started = False

    def start(self) -> None:
        self._started = True
        self.session.start()

    def stop(self) -> None:
        if self._started:
            self._started = False
            self.session.close()

    def wait(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._on_result(self.session.step(timeout=remaining))

    def take_picture(self) -> bytes:
        """Return a JPEG of the tracked person, cut out of the stream.

        Raises RuntimeError, without taking a photo, if nobody is being tracked.
        """
        view = self.session.tracked_view()
        if view is None:
            raise RuntimeError("nobody is being tracked, so no photo was taken")
        show_camera_face(self.misty)
        try:
            print("Taking picture...")
            photo = save_tracked_photo(*view)
            print("Saved the tracked person's picture in recent_images.")
            time.sleep(PHOTO_FACE_TIME)
            return photo
        finally:
            restore_face(self.misty, self.face_seen_recently())

    def _on_result(self, result) -> None:
        if result is None or result.target is None:
            return
        focal_px = self.session.tracker.focal_px(self.session.frame.shape)
        metres = min(MAX_ESTIMATE, FACE_WIDTH_M * focal_px / max(1.0, result.target.width_px))
        self._record_face(
            result.yaw * ANGLE_SCALE,
            result.pitch * ANGLE_SCALE,
            metres,
            raw_distance=round(metres, 2),
        )
