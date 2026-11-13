"""Turn toward a detected face, keep eye contact, and hold ~2 m from the person.

Body turns to keep the face centered horizontally, the head pitches to keep it centered
vertically, and the distance to the person is guessed from the camera alone (the face
detector's distance estimate, ~1-3 m). The front time-of-flight sensors are used only as
a collision stop: if something is right in front, Misty stops and looks up in case it is
the person. If it sees no face it backs away and forgets the face so the caller searches
again. Used by main.py; can also be run on its own.
"""

import threading
import time

from mistyPy.EventFilters import EventFilters
from mistyPy.Events import Events
from mistyPy.Robot import Robot

from idle_mode import get_property
from settings import MISTY_IP

# --- Calibration knobs (tune on the real robot) ---
STOP_DISTANCE = 2.00   # metres from the camera to the person's face
BACKUP_DISTANCE = 1.85 # closer than this, Misty reverses back toward STOP_DISTANCE
SLOW_DISTANCE = 2.70   # start slowing down inside this range
MAX_ESTIMATE = 3.00    # camera guesses beyond this are treated as "3 m, far away"
# Misty's face events report Distance as a raw number. Calibrate: stand exactly 2 m
# away, read the "raw" value printed in the log, and set this to 2 / raw.
FACE_DISTANCE_SCALE = 0.01  # assumes centimetres
DISTANCE_SMOOTHING = 0.3    # 0-1; lower = steadier but slower to react
MAX_SPEED = 25         # drive linear velocity, % of max. Keep low indoors.
MIN_SPEED = 15         # slowest approach speed; at 8 Misty stalled short of the target
BACKUP_SPEED = 15      # at 8 Misty stalled, so keep this well above that
TURN_GAIN = 2.5        # angular % per degree of face bearing
MAX_TURN = 30
BEARING_SIGN = 1       # flip to -1 if Misty turns away from the face
PITCH_GAIN = 0.4       # head pitch degrees per degree of face elevation, per update
ELEVATION_SIGN = 1     # flip to -1 if the head tilts away from the face
PITCH_LIMITS = (-40, 25)  # negative pitch = head up
START_PITCH = -15
FACE_LOST_AFTER = 1.0  # seconds without a face event before the wheels stop
DISTANCE_LOG_EVERY = 2.0
# --- Collision stop (front time-of-flight sensors; not used for the 2 m goal) ---
OBSTACLE_DISTANCE = 0.25  # metres; just outside Misty's own built-in hazard stop
OBSTACLE_STALE_AFTER = 0.5
LOOK_UP_TIME = 2.0        # seconds to look up for a face before deciding it's an object
BACK_AWAY_TIME = 1.5      # seconds of reversing at BACKUP_SPEED when it is an object
LOOP_PERIOD = 0.15


def estimate_distance(raw, previous=None):
    """Turn the face event's raw Distance into smoothed metres, capped at MAX_ESTIMATE."""
    metres = min(MAX_ESTIMATE, float(raw) * FACE_DISTANCE_SCALE)
    if previous is None:
        return metres
    return previous + DISTANCE_SMOOTHING * (metres - previous)


def too_close(obstacle):
    """True when a fresh front ToF reading says something is right in front."""
    return obstacle is not None and obstacle <= OBSTACLE_DISTANCE


def drive_command(bearing, distance):
    """Return (linear, angular) drive velocities for a face at `bearing` and `distance` m."""
    angular = max(-MAX_TURN, min(MAX_TURN, BEARING_SIGN * TURN_GAIN * bearing))
    # Never drive forward blind: no camera estimate = turn only.
    if distance is None:
        return 0, angular
    if distance < BACKUP_DISTANCE:
        return -BACKUP_SPEED, angular
    if distance <= STOP_DISTANCE:
        return 0, angular
    # Don't creep forward while the face is far off to the side; turn first.
    if abs(bearing) > 8:
        return 0, angular
    if distance >= SLOW_DISTANCE:
        return MAX_SPEED, angular
    fraction = (distance - STOP_DISTANCE) / (SLOW_DISTANCE - STOP_DISTANCE)
    return max(MIN_SPEED, MAX_SPEED * fraction), angular


class FaceApproach:
    """Face event state plus one control step. Caller starts face detection."""

    def __init__(self, misty):
        self.misty = misty
        self.lock = threading.Lock()
        self.face = {"bearing": 0.0, "elevation": 0.0, "distance": None, "raw": None, "seen": 0.0}
        self.tof = {}  # front sensor position -> (metres, time seen)
        self.last_distance_log = 0.0
        self.pitch = START_PITCH
        self.driving = False
        self.person_blocking = False

    def register(self):
        """Make sure the front sensors are on, then subscribe to face and ToF events."""
        # Earlier versions switched the front ToF hazards off, and that setting persists.
        self.misty.update_hazard_settings(revertToDefault=True).raise_for_status()
        positions = EventFilters.TimeOfFlightPosition
        for name, position in (
            ("Left", positions.FrontLeft),
            ("Center", positions.FrontCenter),
            ("Right", positions.FrontRight),
        ):
            self.misty.register_event(
                Events.TimeOfFlight,
                f"ApproachTof{name}",
                condition=[position],
                debounce=100,
                keep_alive=True,
                callback_function=self._tof_callback(name),
            )
        self.misty.register_event(
            Events.FaceRecognition,
            "ApproachFace",
            keep_alive=True,
            # The SDK checks the raw argument count, which includes `self` on
            # bound methods. A one-argument wrapper matches its callback API.
            callback_function=lambda event: self._on_face(event),
        )

    def _tof_callback(self, name):
        # The SDK only accepts callbacks that take exactly one argument.
        return lambda event: self._on_tof(name, event)

    def _on_tof(self, name, event):
        message = get_property(event, "message", {})
        # Status 0 is the only fully valid ToF reading.
        if get_property(message, "status") != 0:
            return
        metres = get_property(message, "distanceInMeters")
        if metres is not None:
            with self.lock:
                self.tof[name] = (float(metres), time.monotonic())

    def _front_obstacle(self, now):
        """Nearest fresh front ToF reading in metres, or None when nothing is in range."""
        with self.lock:
            fresh = [d for d, seen in self.tof.values() if now - seen <= OBSTACLE_STALE_AFTER]
        return min(fresh, default=None)

    def _look_up_for_face(self):
        """Tilt the head right up; True if a face shows up (the person is the obstacle)."""
        print("Something is right in front; looking up to check whether it is the person.")
        started = time.monotonic()
        self.pitch = PITCH_LIMITS[0]
        self.misty.move_head(pitch=self.pitch, roll=0, yaw=0, duration=0.5, units="degrees")
        while time.monotonic() - started < LOOK_UP_TIME:
            with self.lock:
                # Only count faces seen after the head has finished moving.
                if self.face["seen"] > started + 0.5:
                    return True
            time.sleep(0.1)
        return False

    def _back_away(self):
        """Reverse from an object and forget the face so the caller searches again."""
        print("No face up there; it is an object. Backing away to search again.")
        self.misty.drive(linearVelocity=-BACKUP_SPEED, angularVelocity=0)
        time.sleep(BACK_AWAY_TIME)
        self.misty.stop()
        self.driving = False
        with self.lock:
            self.face["seen"] = 0.0
            self.face["distance"] = None

    def _on_face(self, event):
        message = get_property(event, "message", {})
        bearing = get_property(message, "bearing")
        if bearing is None:
            return
        with self.lock:
            self.face["bearing"] = float(bearing)
            self.face["elevation"] = float(get_property(message, "elevation", 0) or 0)
            raw = get_property(message, "distance")
            if raw:
                self.face["raw"] = raw
                self.face["distance"] = estimate_distance(raw, self.face["distance"])
            self.face["seen"] = time.monotonic()

    def seconds_since_face(self):
        with self.lock:
            return time.monotonic() - self.face["seen"]

    def face_forward(self, pitch):
        """Center the head yaw so body turning lines up with the camera."""
        # ponytail: snapping yaw to 0 can push a face at the frame edge out of view;
        # compensate with head-yaw + bearing if that happens a lot.
        self.pitch = pitch
        self.misty.move_head(pitch=pitch, roll=0, yaw=0, duration=0.5, units="degrees")

    def halt(self):
        if self.driving:
            self.misty.stop()
            self.driving = False

    def step(self):
        """Run one control update. Returns True while holding in front of the person.

        Blocks for a few seconds when the collision stop triggers (look up, back away).
        """
        now = time.monotonic()
        with self.lock:
            face_visible = now - self.face["seen"] <= FACE_LOST_AFTER
            bearing, elevation = self.face["bearing"], self.face["elevation"]
            distance, raw = self.face["distance"], self.face["raw"]
            if not face_visible:
                # Forget the old guess so a new face starts from its own estimate.
                self.face["distance"] = None

        linear, angular = drive_command(bearing, distance) if face_visible else (0, 0)
        close = too_close(self._front_obstacle(now))
        if not close:
            self.person_blocking = False
        # Collision stop: about to drive into something, or lost the face right at it.
        elif not self.person_blocking and (linear > 0 or not face_visible):
            self.halt()
            if self._look_up_for_face():
                print("Found a face; the person is what is in front. Holding here.")
                self.person_blocking = True
                return True
            self._back_away()
            return False

        if not face_visible:
            self.halt()
            return False
        if close and linear > 0:
            linear = 0  # the person is right in front: don't push into them

        # Keep eyeline: nudge head pitch toward the face's vertical offset.
        new_pitch = self.pitch + ELEVATION_SIGN * PITCH_GAIN * elevation
        new_pitch = max(PITCH_LIMITS[0], min(PITCH_LIMITS[1], new_pitch))
        if abs(new_pitch - self.pitch) >= 1:
            self.pitch = new_pitch
            self.misty.move_head(pitch=new_pitch, roll=0, yaw=0, duration=0.3, units="degrees")

        if now - self.last_distance_log >= DISTANCE_LOG_EVERY:
            self.last_distance_log = now
            shown = "unknown" if distance is None else f"{distance:.2f} m"
            print(
                f"Camera distance estimate: {shown} (raw {raw}), bearing {bearing:.0f}°, "
                f"drive linear {linear:.0f} angular {angular:.0f}"
            )
        if linear == 0 and abs(angular) < 3:
            self.halt()
        else:
            self.misty.drive(linearVelocity=linear, angularVelocity=angular)
            self.driving = True
        at_goal = distance is not None and BACKUP_DISTANCE <= distance <= STOP_DISTANCE
        return at_goal or (close and self.person_blocking)


def main():
    misty = Robot(MISTY_IP)
    approach = FaceApproach(misty)
    detection_started = False
    try:
        approach.face_forward(START_PITCH)
        approach.register()
        time.sleep(0.5)
        misty.start_face_detection().raise_for_status()
        detection_started = True
        print("Looking for a face. Press Ctrl+C to stop.")
        while True:
            approach.step()
            time.sleep(LOOP_PERIOD)
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        try:
            misty.stop()
        except Exception as error:
            print(f"Could not stop driving: {error}")
        if detection_started:
            try:
                misty.stop_face_detection()
            except Exception as error:
                print(f"Could not stop face detection: {error}")
        misty.unregister_all_events()


def self_check():
    assert drive_command(0, 1.95)[0] == 0, "holds inside the 1.85-2 m band"
    assert drive_command(0, 1.50)[0] == -BACKUP_SPEED, "backs up when too close"
    assert drive_command(0, None)[0] == 0, "no camera estimate = never drive forward blind"
    assert drive_command(0, 3.0)[0] == MAX_SPEED, "full speed when far"
    assert MIN_SPEED <= drive_command(0, 2.4)[0] < MAX_SPEED, "slows down when close"
    assert drive_command(20, 2.9)[0] == 0, "turns before driving when face is off-center"
    assert drive_command(100, None)[1] == MAX_TURN * BEARING_SIGN, "turn is clamped"
    assert too_close(0.20) and not too_close(1.0), "collision stop only right in front"
    assert not too_close(None), "no ToF reading = nothing in front"
    assert estimate_distance(500) == MAX_ESTIMATE, "guesses are capped at 3 m"
    assert estimate_distance(150) == 1.5
    assert 2.0 < estimate_distance(100, previous=3.0) < 3.0, "smoothing moves part-way"


if __name__ == "__main__":
    self_check()
    main()
