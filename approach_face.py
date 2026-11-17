"""Turn toward a face and use its camera distance estimate to approach."""

import threading
import time

from mistyPy.Events import Events

from face_utils import FACE_ACTIVE_WINDOW, NEUTRAL_PITCH, get_property
from misty_camera import take_picture

# --- Calibration and movement settings ---
BACKUP_DISTANCE = 1.00
STOP_DISTANCE = 1.50
SLOW_DISTANCE = 2.00
MAX_ESTIMATE = 5.00
FACE_DISTANCE_SCALE = 0.01
DISTANCE_SMOOTHING = 0.3
DISTANCE_STALE_AFTER = 1.0
MAX_SPEED = 25
MIN_SPEED = 15
BACKUP_SPEED = 10
TURN_GAIN = 2.5
MAX_TURN = 30
BEARING_SIGN = 1         # flip to -1 if Misty turns away from the face
PITCH_GAIN = 0.4
ELEVATION_SIGN = 1       # flip to -1 if the head tilts away from the face
PITCH_LIMITS = (-40, 25)
FACE_LOST_AFTER = 1.0   # stop the wheels after this long without a face event
DISTANCE_LOG_EVERY = 2.0
LOOP_PERIOD = 0.15


def smooth_distance(metres, previous=None):
    """Cap a distance estimate and blend it into the previous one, if there is one."""
    metres = min(MAX_ESTIMATE, metres)
    if previous is None:
        return metres
    return previous + DISTANCE_SMOOTHING * (metres - previous)


def drive_command(bearing, distance):
    """Return linear and angular drive velocities for a face bearing and range."""
    angular = max(-MAX_TURN, min(MAX_TURN, BEARING_SIGN * TURN_GAIN * bearing))
    if distance is None:
        return 0, angular  # turn to face the person, but never drive forward blind
    if distance < BACKUP_DISTANCE:
        return -BACKUP_SPEED, angular
    if distance <= STOP_DISTANCE:
        return 0, angular
    if abs(bearing) > 8:
        return 0, angular  # turn first when the face is far off-center
    if distance >= SLOW_DISTANCE:
        return MAX_SPEED, angular
    fraction = (distance - STOP_DISTANCE) / (SLOW_DISTANCE - STOP_DISTANCE)
    return max(MIN_SPEED, MAX_SPEED * fraction), angular


_NO_READING = object()  # Marks "this sighting had no distance", as opposed to None.


class FaceApproach:
    """Face event state plus one camera-distance control step."""

    def __init__(self, misty):
        self.misty = misty
        self.lock = threading.Lock()
        self.face = {
            "bearing": 0.0,
            "elevation": 0.0,
            "distance": None,
            "raw_distance": None,
            "distance_seen": 0.0,
            "seen": 0.0,
        }
        self.pitch = NEUTRAL_PITCH
        self.driving = False
        self.last_distance_log = 0.0

    def start(self) -> None:
        self.register()
        time.sleep(0.5)
        self.misty.start_face_detection().raise_for_status()

    def stop(self) -> None:
        self.misty.stop_face_detection().raise_for_status()

    def wait(self, seconds: float) -> None:
        """Pause the main loop; face events keep arriving in the background."""
        time.sleep(seconds)

    def take_picture(self) -> bytes:
        return take_picture(self.misty, face_visible=self.face_seen_recently)

    def register(self) -> None:
        self.misty.register_event(
            Events.FaceRecognition,
            "ApproachFace",
            keep_alive=True,
            callback_function=lambda event: self._on_face(event),
        )

    def _on_face(self, event) -> None:
        message = get_property(event, "message", {})
        bearing = get_property(message, "bearing")
        if bearing is None:
            return

        elevation = float(get_property(message, "elevation", 0) or 0)
        raw_distance = get_property(message, "distance")
        if raw_distance is None:
            self._record_face(float(bearing), elevation)
            return
        try:
            raw_distance = float(raw_distance)
        except (TypeError, ValueError):
            raw_distance = None
        if raw_distance is not None and raw_distance > 0:
            metres = raw_distance * FACE_DISTANCE_SCALE
        else:
            metres = None
        self._record_face(float(bearing), elevation, metres, raw_distance)

    def _record_face(self, bearing, elevation, metres=_NO_READING, raw_distance=None) -> None:
        """Store one sighting of the face; this is the only place self.face is filled in.

        metres is the measured distance: a number is blended into the running estimate,
        None clears the estimate (an unusable reading), and leaving it out keeps the
        estimate as it is (the sighting carried no distance at all).
        """
        now = time.monotonic()
        with self.lock:
            face = self.face
            face["bearing"] = bearing
            face["elevation"] = elevation
            face["seen"] = now
            if metres is _NO_READING:
                return
            face["raw_distance"] = raw_distance
            if metres is None:
                face["distance"] = None
                face["distance_seen"] = 0.0
            else:
                face["distance"] = smooth_distance(metres, face["distance"])
                face["distance_seen"] = now

    def seconds_since_face(self) -> float:
        with self.lock:
            return time.monotonic() - self.face["seen"]

    def face_seen_recently(self) -> bool:
        """True while the face counts as present (short gaps between sightings allowed)."""
        return self.seconds_since_face() <= FACE_ACTIVE_WINDOW

    def has_fresh_distance(self) -> bool:
        with self.lock:
            return (
                self.face["distance"] is not None
                and time.monotonic() - self.face["distance_seen"] <= DISTANCE_STALE_AFTER
            )

    def face_forward(self, pitch: float) -> None:
        """Set the head pitch and center its yaw before the body turns."""
        self.pitch = pitch
        self.misty.move_head(pitch=pitch, roll=0, yaw=0, duration=0.5, units="degrees")

    def halt(self) -> None:
        if self.driving:
            self.misty.stop()
            self.driving = False

    def step(self) -> bool:
        """Update tracking and return True inside the 1-1.5 m stopping range."""
        now = time.monotonic()
        with self.lock:
            face_visible = now - self.face["seen"] <= FACE_LOST_AFTER
            bearing = self.face["bearing"]
            elevation = self.face["elevation"]
            distance_is_fresh = now - self.face["distance_seen"] <= DISTANCE_STALE_AFTER
            distance = self.face["distance"] if distance_is_fresh else None
            raw_distance = self.face["raw_distance"]
            if not face_visible:
                self.face["distance"] = None
                self.face["distance_seen"] = 0.0

        if not face_visible:
            self.halt()
            return False

        new_pitch = self.pitch + ELEVATION_SIGN * PITCH_GAIN * elevation
        new_pitch = max(PITCH_LIMITS[0], min(PITCH_LIMITS[1], new_pitch))
        if abs(new_pitch - self.pitch) >= 1:
            self.pitch = new_pitch
            self.misty.move_head(pitch=new_pitch, roll=0, yaw=0, duration=0.3, units="degrees")

        linear, angular = drive_command(bearing, distance)
        if now - self.last_distance_log >= DISTANCE_LOG_EVERY:
            self.last_distance_log = now
            shown = "unknown" if distance is None else f"{distance:.2f} m"
            print(
                f"Camera distance estimate: {shown} (raw {raw_distance}), "
                f"bearing {bearing:.0f}°, drive linear {linear:.0f} angular {angular:.0f}"
            )

        if linear == 0 and abs(angular) < 3:
            self.halt()
        else:
            self.misty.drive(linearVelocity=linear, angularVelocity=angular)
            self.driving = True

        return distance is not None and BACKUP_DISTANCE <= distance <= STOP_DISTANCE
