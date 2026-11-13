"""Idle-scan for a face, walk up to it, and speak a brief clothing compliment."""

import threading
import time

from mistyPy.Robot import Robot

from approach_face import LOOP_PERIOD, FaceApproach
from idle_mode import (
    FACE_ACTIVE_WINDOW,
    FACE_IMAGE,
    IDLE_IMAGE,
    MOVE_DURATION,
    PITCH_POSITIONS,
    YAW_POSITIONS,
)
from misty_camera import take_picture
from ollama_client import describe_person
from settings import MISTY_IP

NEUTRAL_PITCH = -15


def main() -> None:
    print("Connecting to Misty...")
    misty = Robot(MISTY_IP)
    approach = FaceApproach(misty)
    eyes_showing_face = [False]
    detection_started = False
    tracking_started = False
    description_attempted = False

    def face_seen_recently() -> bool:
        return approach.seconds_since_face() <= FACE_ACTIVE_WINDOW

    def set_face_eyes(face_seen: bool) -> None:
        if eyes_showing_face[0] == face_seen:
            return
        image = FACE_IMAGE if face_seen else IDLE_IMAGE
        misty.display_image(image).raise_for_status()
        eyes_showing_face[0] = face_seen

    def compliment() -> None:
        # Runs in a thread so Misty keeps holding distance while Ollama thinks.
        try:
            print("Taking a photo and asking Ollama for a clothing detail...")
            detail = describe_person(take_picture(misty))
            if not detail:
                print("Ollama found no specific visible detail to mention.")
            elif face_seen_recently():
                phrase = f"I like your {detail}."
                print(f"Misty says: {phrase}")
                misty.speak(phrase, flush=True, language="en-US").raise_for_status()
        except Exception as error:
            print(f"Could not describe or speak the person: {error}")

    pitch_index = 0
    yaw_index = 0
    try:
        misty.display_image(IDLE_IMAGE).raise_for_status()
        approach.register()
        time.sleep(0.5)
        misty.start_face_detection().raise_for_status()
        detection_started = True

        print("Idle scan is running. Press Ctrl+C to stop.")
        while True:
            if face_seen_recently():
                set_face_eyes(True)
                if not tracking_started:
                    print("Face detected; greeting and approaching.")
                    approach.face_forward(PITCH_POSITIONS[pitch_index])
                    misty.speak("Hey!", flush=True, language="en-US")
                    tracking_started = True

                arrived = approach.step()
                if arrived and not description_attempted:
                    description_attempted = True
                    print("Arrived in front of the person.")
                    threading.Thread(target=compliment, daemon=True).start()
                time.sleep(LOOP_PERIOD)
                continue

            if tracking_started:
                print("Face no longer detected; returning to idle scan.")
                approach.halt()
                set_face_eyes(False)
                misty.move_head(
                    pitch=NEUTRAL_PITCH,
                    roll=0,
                    yaw=0,
                    duration=1.0,
                    units="degrees",
                ).raise_for_status()
                tracking_started = False
                description_attempted = False
                time.sleep(1.0)
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
                continue  # keep pitch_index so face_forward uses the pitch that found the face
            pitch_index = (pitch_index + 1) % len(PITCH_POSITIONS)
            if pitch_index == 0:
                yaw_index = (yaw_index + 1) % len(YAW_POSITIONS)
    except KeyboardInterrupt:
        print("Stopping Misty...")
    finally:
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
