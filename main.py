"""Run Misty's face scan, approach, greeting, and petting interaction."""

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


class State(Enum):
    SCANNING = auto()
    CAPTURING_PHOTO = auto()
    APPROACHING = auto()
    ARRIVED = auto()
    WAITING_FOR_PET = auto()
    PETTED = auto()


def main() -> None:
    print("Connecting to Misty...")
    misty = Robot(MISTY_IP)
    approach = FaceApproach(misty)
    greeting = Greeting(misty)
    bump_reset = BumpReset(misty)
    state = State.SCANNING
    eyes_showing_face = False
    detection_started = False
    greeting_started_for_face = False
    returning_to_scan = False
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

    def reset_to_scan() -> None:
        nonlocal eyes_showing_face, greeting_started_for_face
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
            while not greeting.done.wait(0.1):
                pass
        reset_to_scan()
        returning_to_scan = False
        state = State.SCANNING

    try:
        misty.display_image(IDLE_IMAGE).raise_for_status()
        approach.register()
        greeting.register()
        bump_reset.register()
        time.sleep(0.5)
        misty.start_face_detection().raise_for_status()
        detection_started = True

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
                time.sleep(0.5)
                continue

            face_visible = face_seen_recently()

            if state is State.SCANNING:
                if face_visible:
                    print("Face detected; taking a picture before tracking and approaching.")
                    set_face_eyes(True)
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
                    time.sleep(0.1)
                if face_seen_recently():
                    continue  # Keep this pitch; it found the face.
                pitch_index = (pitch_index + 1) % len(PITCH_POSITIONS)
                if pitch_index == 0:
                    yaw_index = (yaw_index + 1) % len(YAW_POSITIONS)
                continue

            if state not in {State.SCANNING, State.PETTED} and not face_visible:
                approach.halt()
                greeting.stop()
                if not returning_to_scan:
                    print("Face no longer detected; finishing the current utterance before scanning again.")
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
                    reset_to_scan()
                    state = State.SCANNING
                else:
                    time.sleep(0.1)
                continue

            if returning_to_scan:
                if greeting.done.is_set():
                    returning_to_scan = False
                    reset_to_scan()
                    state = State.SCANNING
                else:
                    time.sleep(0.1)
                continue

            if state is State.CAPTURING_PHOTO:
                if greeting.photo_capture_done.is_set():
                    if greeting.photo_captured.is_set():
                        print("Photo captured; tracking and approaching the person.")
                        approach.face_forward(PITCH_POSITIONS[pitch_index])
                        state = State.APPROACHING
                    else:
                        reset_to_scan()
                        state = State.SCANNING
                else:
                    time.sleep(0.1)
                continue

            if state is State.APPROACHING:
                set_face_eyes(True)
                if approach.step():
                    print("Arrived in front of the person; holding position for the compliment.")
                    approach.halt()
                    greeting.start_arrival_interaction()
                    state = State.ARRIVED
                time.sleep(LOOP_PERIOD)
                continue

            if state is State.ARRIVED:
                if greeting.compliment_done.is_set():
                    state = State.WAITING_FOR_PET
                elif greeting.done.is_set():
                    print("The compliment did not finish; returning to idle scan.")
                    reset_to_scan()
                    state = State.SCANNING
                time.sleep(LOOP_PERIOD)
                continue

            if state is State.WAITING_FOR_PET and greeting.done.is_set():
                if greeting.petted.is_set():
                    enter_petted()
                else:
                    reset_to_scan()
                    state = State.SCANNING
                continue

            if state is State.WAITING_FOR_PET:
                time.sleep(LOOP_PERIOD)
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
                misty.stop_face_detection().raise_for_status()
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


if __name__ == "__main__":
    main()
