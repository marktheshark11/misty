"""Run Misty's face scan, approach, greeting, and petting interaction.

This file uses Misty's on-board face detection. main_remote.py runs the same
interaction with face detection on this PC instead; both share run() below.
"""

import time
from enum import Enum, auto

from mistyPy.Robot import Robot

from approach_face import LOOP_PERIOD, FaceApproach
from bump_reset import BumpReset
from face_utils import (
    FACE_ACTIVE_WINDOW,
    FACE_IMAGE,
    IDLE_IMAGE,
    MOVE_DURATION,
    PITCH_POSITIONS,
    YAW_POSITIONS,
)
from greeting import Greeting
from settings import MISTY_IP

NEUTRAL_PITCH = -15
PETTED_IMAGE = "e_love.jpg"
RETRY_DELAY = 10.0
MISSING_DISTANCE_TIMEOUT = 10.0
FACE_LOST_PHRASE = "Oh, where did you go?"


class State(Enum):
    SCANNING = auto()
    CAPTURING_PHOTO = auto()
    APPROACHING = auto()
    ARRIVED = auto()
    WAITING_FOR_PET = auto()
    PETTED = auto()


def run(misty, approach) -> None:
    """Run the interaction. `approach` supplies the face data: FaceApproach for Misty's
    on-board detection, RemoteFaceApproach for detection on this PC."""
    greeting = Greeting(misty, take_photo=approach.take_picture)
    bump_reset = BumpReset(misty)
    state = State.SCANNING
    eyes_showing_face = False
    detection_started = False
    greeting_started_for_face = False
    returning_to_scan = False
    next_attempt_at = 0.0
    missing_distance_since = None
    pitch_index = 0
    yaw_index = 0

    def face_seen_recently() -> bool:
        return approach.seconds_since_face() <= FACE_ACTIVE_WINDOW

    def set_face_eyes(face_seen: bool) -> None:
        nonlocal eyes_showing_face
        if eyes_showing_face == face_seen:
            return
        image = FACE_IMAGE if face_seen else IDLE_IMAGE
        misty.display_image(image).raise_for_status()
        eyes_showing_face = face_seen

    def reset_to_scan(retry_delay: float = 0.0) -> None:
        nonlocal greeting_started_for_face, next_attempt_at
        approach.halt()
        set_face_eyes(False)
        misty.move_head(
            pitch=NEUTRAL_PITCH,
            roll=0,
            yaw=0,
            duration=1.0,
            units="degrees",
        ).raise_for_status()
        greeting_started_for_face = False
        next_attempt_at = time.monotonic() + retry_delay

    def say_face_lost() -> None:
        # Called once the interrupted greeting has gone quiet, so it isn't cut off.
        print(f"Misty says: {FACE_LOST_PHRASE}")
        try:
            misty.speak(FACE_LOST_PHRASE, flush=True, language="en-US").raise_for_status()
        except Exception as error:
            print(f"Could not say the face-lost line: {error}")

    def enter_petted() -> None:
        nonlocal state
        approach.halt()
        misty.display_image(PETTED_IMAGE).raise_for_status()
        state = State.PETTED

    def reset_to_idle() -> None:
        nonlocal state, returning_to_scan
        print("Both front bump sensors pressed; resetting to idle scan.")
        greeting.stop()
        approach.halt()
        if greeting_started_for_face:
            while not greeting.done.is_set():
                approach.wait(0.1)
        reset_to_scan()
        returning_to_scan = False
        state = State.SCANNING

    try:
        misty.display_image(IDLE_IMAGE).raise_for_status()
        greeting.register()
        bump_reset.register()
        detection_started = True  # Set first so a failed start is still cleaned up.
        approach.start()

        print("Idle scan is running. Press Ctrl+C to stop.")
        while True:
            if bump_reset.requested.is_set():
                bump_reset.requested.clear()
                reset_to_idle()
                continue

            if (
                state is State.WAITING_FOR_PET
                and greeting.petted.is_set()
                and greeting.done.is_set()
            ):
                enter_petted()
                continue

            if state is State.PETTED:
                approach.wait(0.5)
                continue

            face_visible = face_seen_recently()

            if state is State.SCANNING:
                if face_visible:
                    set_face_eyes(True)
                    if time.monotonic() < next_attempt_at:
                        approach.wait(LOOP_PERIOD)
                        continue
                    print("Face detected; taking a picture before tracking and approaching.")
                    greeting.start()
                    greeting_started_for_face = True
                    state = State.CAPTURING_PHOTO
                    continue

                set_face_eyes(False)
                pitch = PITCH_POSITIONS[pitch_index]
                yaw = YAW_POSITIONS[yaw_index]
                misty.move_head(
                    pitch=pitch,
                    roll=0,
                    yaw=yaw,
                    duration=MOVE_DURATION,
                    units="degrees",
                ).raise_for_status()
                deadline = time.monotonic() + MOVE_DURATION
                while time.monotonic() < deadline and not face_seen_recently():
                    approach.wait(0.1)
                if face_seen_recently():
                    continue  # Keep this pitch; it found the face.
                pitch_index = (pitch_index + 1) % len(PITCH_POSITIONS)
                if pitch_index == 0:
                    yaw_index = (yaw_index + 1) % len(YAW_POSITIONS)
                continue

            if state not in {State.SCANNING, State.PETTED} and not face_visible:
                approach.halt()
                if not returning_to_scan:
                    greeting.stop()
                    print("Face no longer detected; returning to idle scan.")
                    set_face_eyes(False)
                    misty.move_head(
                        pitch=NEUTRAL_PITCH,
                        roll=0,
                        yaw=0,
                        duration=1.0,
                        units="degrees",
                    ).raise_for_status()
                    returning_to_scan = True
                if greeting.done.is_set():
                    returning_to_scan = False
                    say_face_lost()
                    reset_to_scan()
                    state = State.SCANNING
                else:
                    approach.wait(0.1)
                continue

            if returning_to_scan:
                if greeting.done.is_set():
                    returning_to_scan = False
                    say_face_lost()
                    reset_to_scan()
                    state = State.SCANNING
                else:
                    approach.wait(0.1)
                continue

            if state is State.CAPTURING_PHOTO:
                if greeting.photo_capture_done.is_set():
                    if greeting.photo_captured.is_set():
                        print("Photo captured; tracking and approaching the person.")
                        approach.face_forward(PITCH_POSITIONS[pitch_index])
                        missing_distance_since = None
                        state = State.APPROACHING
                    else:
                        reset_to_scan(RETRY_DELAY)
                        state = State.SCANNING
                else:
                    approach.wait(0.1)
                continue

            if state is State.APPROACHING:
                set_face_eyes(True)
                if approach.step():
                    print("Arrived in front of the person; holding position for the compliment.")
                    approach.halt()
                    greeting.start_arrival_interaction()
                    state = State.ARRIVED
                elif approach.has_fresh_distance():
                    missing_distance_since = None
                else:
                    if missing_distance_since is None:
                        missing_distance_since = time.monotonic()
                    elif time.monotonic() - missing_distance_since >= MISSING_DISTANCE_TIMEOUT:
                        print("No usable face distance; returning to idle scan.")
                        greeting.stop()
                        approach.halt()
                        reset_to_scan(RETRY_DELAY)
                        state = State.SCANNING
                approach.wait(LOOP_PERIOD)
                continue

            if state is State.ARRIVED:
                if greeting.compliment_done.is_set():
                    state = State.WAITING_FOR_PET
                elif greeting.done.is_set():
                    print("The compliment did not finish; returning to idle scan.")
                    reset_to_scan(RETRY_DELAY)
                    state = State.SCANNING
                approach.wait(LOOP_PERIOD)
                continue

            if state is State.WAITING_FOR_PET and greeting.done.is_set():
                if greeting.petted.is_set():
                    enter_petted()
                else:
                    reset_to_scan(RETRY_DELAY)
                    state = State.SCANNING
                continue

            if state is State.WAITING_FOR_PET:
                approach.wait(LOOP_PERIOD)
                continue

    except KeyboardInterrupt:
        print("Stopping Misty...")
    finally:
        greeting.stop()
        try:
            misty.stop()
        except Exception as error:
            print(f"Could not stop driving: {error}")
        if detection_started:
            try:
                approach.stop()
            except Exception as error:
                print(f"Could not stop face detection cleanly: {error}")
        misty.unregister_all_events()
        try:
            misty.move_head(
                pitch=NEUTRAL_PITCH,
                roll=0,
                yaw=0,
                duration=1.0,
                units="degrees",
            ).raise_for_status()
        except Exception as error:
            print(f"Could not return Misty's head to neutral: {error}")
        try:
            misty.display_image(IDLE_IMAGE).raise_for_status()
        except Exception as error:
            print(f"Could not restore idle eyes: {error}")


def main() -> None:
    print("Connecting to Misty...")
    misty = Robot(MISTY_IP)
    run(misty, FaceApproach(misty))


if __name__ == "__main__":
    main()
