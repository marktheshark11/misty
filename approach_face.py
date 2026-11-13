"""Turn toward a detected face, keep eye contact, and hold ~1 m from the person.

Body turns to keep the face centered horizontally, the head pitches to keep it centered
vertically, and the front-center time-of-flight sensor decides when to stop or back up.
Used by main.py; can also be run on its own.
"""

import threading
import time

from mistyPy.EventFilters import EventFilters
from mistyPy.Events import Events
from mistyPy.Robot import Robot

from idle_mode import get_property
from settings import MISTY_IP

# --- Calibration knobs (tune on the real robot) ---
STOP_DISTANCE = 1.00   # metres from the front ToF sensor to the person
BACKUP_DISTANCE = 0.90 # closer than this, Misty reverses back toward STOP_DISTANCE
SLOW_DISTANCE = 1.30   # start slowing down inside this range (~ToF max range)
MAX_SPEED = 25         # drive linear velocity, % of max. Keep low indoors.
MIN_SPEED = 8          # below this Misty's wheels tend to stall
BACKUP_SPEED = 10
TURN_GAIN = 2.5        # angular % per degree of face bearing
MAX_TURN = 30
BEARING_SIGN = 1       # flip to -1 if Misty turns away from the face
PITCH_GAIN = 0.4       # head pitch degrees per degree of face elevation, per update
ELEVATION_SIGN = 1     # flip to -1 if the head tilts away from the face
PITCH_LIMITS = (-40, 25)  # negative pitch = head up
START_PITCH = -15
FACE_LOST_AFTER = 1.0  # seconds without a face event before the wheels stop
TOF_STALE_AFTER = 0.5  # seconds; ToF has no reading when nothing is within ~1.3 m
LOOP_PERIOD = 0.15


def drive_command(bearing, tof_distance):
    """Return (linear, angular) drive velocities for a face at `bearing` and ToF reading."""
    angular = max(-MAX_TURN, min(MAX_TURN, BEARING_SIGN * TURN_GAIN * bearing))
    if tof_distance is not None and tof_distance < BACKUP_DISTANCE:
        return -BACKUP_SPEED, angular
    if tof_distance is not None and tof_distance <= STOP_DISTANCE:
        return 0, angular
    # Don't creep forward while the face is far off to the side; turn first.
    if abs(bearing) > 8:
        return 0, angular
    if tof_distance is None or tof_distance >= SLOW_DISTANCE:
        return MAX_SPEED, angular
    fraction = (tof_distance - STOP_DISTANCE) / (SLOW_DISTANCE - STOP_DISTANCE)
    return max(MIN_SPEED, MAX_SPEED * fraction), angular


class FaceApproach:
    """Face/ToF event state plus one control step. Caller starts face detection."""

    def __init__(self, misty):
        self.misty = misty
        self.lock = threading.Lock()
        self.face = {"bearing": 0.0, "elevation": 0.0, "seen": 0.0}
        self.tof = {"distance": None, "seen": 0.0}
        self.pitch = START_PITCH
        self.driving = False

    def register(self):
        self.misty.register_event(
            Events.FaceRecognition,
            "ApproachFace",
            keep_alive=True,
            # The SDK checks the raw argument count, which includes `self` on
            # bound methods. A one-argument wrapper matches its callback API.
            callback_function=lambda event: self._on_face(event),
        )
        self.misty.register_event(
            Events.TimeOfFlight,
            "ApproachFrontTof",
            condition=[EventFilters.TimeOfFlightPosition.FrontCenter],
            keep_alive=True,
            callback_function=lambda event: self._on_tof(event),
        )

    def _on_face(self, event):
        message = get_property(event, "message", {})
        bearing = get_property(message, "bearing")
        if bearing is None:
            return
        with self.lock:
            self.face["bearing"] = float(bearing)
            self.face["elevation"] = float(get_property(message, "elevation", 0) or 0)
            self.face["seen"] = time.monotonic()

    def _on_tof(self, event):
        message = get_property(event, "message", {})
        # Status 0 is the only fully valid ToF reading.
        if get_property(message, "status") != 0:
            return
        with self.lock:
            self.tof["distance"] = float(get_property(message, "distanceInMeters"))
            self.tof["seen"] = time.monotonic()

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
        """Run one control update. Returns True while holding at the stop distance."""
        now = time.monotonic()
        with self.lock:
            face_visible = now - self.face["seen"] <= FACE_LOST_AFTER
            bearing, elevation = self.face["bearing"], self.face["elevation"]
            fresh = now - self.tof["seen"] <= TOF_STALE_AFTER
            distance = self.tof["distance"] if fresh else None

        if not face_visible:
            self.halt()
            return False

        # Keep eyeline: nudge head pitch toward the face's vertical offset.
        new_pitch = self.pitch + ELEVATION_SIGN * PITCH_GAIN * elevation
        new_pitch = max(PITCH_LIMITS[0], min(PITCH_LIMITS[1], new_pitch))
        if abs(new_pitch - self.pitch) >= 1:
            self.pitch = new_pitch
            self.misty.move_head(pitch=new_pitch, roll=0, yaw=0, duration=0.3, units="degrees")

        linear, angular = drive_command(bearing, distance)
        if linear == 0 and abs(angular) < 3:
            self.halt()
        else:
            self.misty.drive(linearVelocity=linear, angularVelocity=angular)
            self.driving = True
        return distance is not None and BACKUP_DISTANCE <= distance <= STOP_DISTANCE


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
    assert drive_command(0, 0.95)[0] == 0, "holds inside the 90-100 cm band"
    assert drive_command(0, 0.70)[0] == -BACKUP_SPEED, "backs up when too close"
    assert drive_command(0, None)[0] == MAX_SPEED, "no ToF reading = far away"
    assert MIN_SPEED <= drive_command(0, 1.15)[0] < MAX_SPEED, "slows down when close"
    assert drive_command(20, 2.0)[0] == 0, "turns before driving when face is off-center"
    assert drive_command(100, None)[1] == MAX_TURN * BEARING_SIGN, "turn is clamped"


if __name__ == "__main__":
    self_check()
    main()
