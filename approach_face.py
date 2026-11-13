"""Turn toward a detected face, keep eye contact, and hold ~1 m from the person.

Body turns to keep the face centered horizontally, the head pitches to keep it centered
vertically, and the distance to the person is guessed from the camera alone (the face
detector's distance estimate, ~1-3 m). The front time-of-flight sensors are switched off
as hazards and not read at all; bump sensors and the downward (cliff) sensors stay on.
Used by main.py; can also be run on its own.
"""

import threading
import time

from mistyPy.Events import Events
from mistyPy.Robot import Robot

from idle_mode import get_property
from settings import MISTY_IP

# --- Calibration knobs (tune on the real robot) ---
STOP_DISTANCE = 1.00   # metres from the camera to the person's face
BACKUP_DISTANCE = 0.85 # closer than this, Misty reverses back toward STOP_DISTANCE
SLOW_DISTANCE = 2.00   # start slowing down inside this range
MAX_ESTIMATE = 3.00    # camera guesses beyond this are treated as "3 m, far away"
# Misty's face events report Distance as a raw number. Calibrate: stand exactly 2 m
# away, read the "raw" value printed in the log, and set this to 2 / raw.
FACE_DISTANCE_SCALE = 0.01  # assumes centimetres
DISTANCE_SMOOTHING = 0.3    # 0-1; lower = steadier but slower to react
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
DISTANCE_LOG_EVERY = 2.0
# Front ToF sensors to switch off as hazards (threshold 0 = disabled).
FRONT_TOF_SENSORS = ("TOF_Left", "TOF_Center", "TOF_Right")
LOOP_PERIOD = 0.15


def estimate_distance(raw, previous=None):
    """Turn the face event's raw Distance into smoothed metres, capped at MAX_ESTIMATE."""
    metres = min(MAX_ESTIMATE, float(raw) * FACE_DISTANCE_SCALE)
    if previous is None:
        return metres
    return previous + DISTANCE_SMOOTHING * (metres - previous)


def drive_command(bearing, distance):
    """Return (linear, angular) drive velocities for a face at `bearing` and `distance` m."""
    angular = max(-MAX_TURN, min(MAX_TURN, BEARING_SIGN * TURN_GAIN * bearing))
    # Front sensors are off, so never drive forward blind: no estimate = turn only.
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
        self.last_distance_log = 0.0
        self.pitch = START_PITCH
        self.driving = False

    def register(self):
        """Switch off the front ToF hazards and subscribe to face events."""
        # ponytail: with front ToFs off Misty won't stop for walls or furniture on its
        # own; only the bump sensors catch a collision. Supervise it.
        self.misty.update_hazard_settings(
            timeOfFlightThresholds=[
                {"sensorName": name, "threshold": 0} for name in FRONT_TOF_SENSORS
            ]
        ).raise_for_status()
        print("Front time-of-flight sensors disabled; using camera distance only.")
        self.misty.register_event(
            Events.FaceRecognition,
            "ApproachFace",
            keep_alive=True,
            # The SDK checks the raw argument count, which includes `self` on
            # bound methods. A one-argument wrapper matches its callback API.
            callback_function=lambda event: self._on_face(event),
        )

    def restore_hazards(self):
        """Put Misty's hazard settings back to factory defaults (they persist otherwise)."""
        try:
            self.misty.update_hazard_settings(revertToDefault=True).raise_for_status()
            print("Hazard settings restored to defaults.")
        except Exception as error:
            print(f"Could not restore hazard settings, re-enable front ToFs manually: {error}")

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
        """Run one control update. Returns True while holding at the stop distance."""
        now = time.monotonic()
        with self.lock:
            face_visible = now - self.face["seen"] <= FACE_LOST_AFTER
            bearing, elevation = self.face["bearing"], self.face["elevation"]
            distance, raw = self.face["distance"], self.face["raw"]
            if not face_visible:
                # Forget the old guess so a new face starts from its own estimate.
                self.face["distance"] = None

        if not face_visible:
            self.halt()
            return False

        if now - self.last_distance_log >= DISTANCE_LOG_EVERY:
            self.last_distance_log = now
            shown = "unknown" if distance is None else f"{distance:.2f} m"
            print(f"Camera distance estimate: {shown} (raw {raw})")

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
        approach.register()  # disables front ToFs; restored in finally
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
        approach.restore_hazards()


def self_check():
    assert drive_command(0, 0.95)[0] == 0, "holds inside the 85-100 cm band"
    assert drive_command(0, 0.70)[0] == -BACKUP_SPEED, "backs up when too close"
    assert drive_command(0, None)[0] == 0, "no camera estimate = never drive forward blind"
    assert drive_command(0, 3.0)[0] == MAX_SPEED, "full speed when far"
    assert MIN_SPEED <= drive_command(0, 1.5)[0] < MAX_SPEED, "slows down when close"
    assert drive_command(20, 2.5)[0] == 0, "turns before driving when face is off-center"
    assert drive_command(100, None)[1] == MAX_TURN * BEARING_SIGN, "turn is clamped"
    assert estimate_distance(500) == MAX_ESTIMATE, "guesses are capped at 3 m"
    assert estimate_distance(150) == 1.5
    assert 2.0 < estimate_distance(100, previous=3.0) < 3.0, "smoothing moves part-way"


if __name__ == "__main__":
    self_check()
    main()
