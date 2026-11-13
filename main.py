"""Idle-scan for a face, follow it, and speak a brief clothing compliment."""

import threading
import time
import uuid

from mistyPy.Events import Events
from mistyPy.Robot import Robot

from idle_mode import (
    FACE_ACTIVE_WINDOW,
    FACE_IMAGE,
    IDLE_IMAGE,
    MOVE_DURATION,
    PITCH_POSITIONS,
    YAW_POSITIONS,
    get_property,
)
from misty_camera import take_picture
from ollama_client import describe_person
from settings import MISTY_IP

NEUTRAL_PITCH = -15


def main() -> None:
    print("Connecting to Misty...")
    misty = Robot(MISTY_IP)
    suffix = uuid.uuid4().hex[:8]
    follow_action = f"main_face_follow_{suffix}"
    stop_action = f"main_face_stop_{suffix}"
    state_lock = threading.Lock()
    last_face_seen = [0.0]
    eyes_showing_face = [False]
    detection_started = False
    tracking_started = False
    description_attempted = False

    def on_face_event(event):
        message = get_property(event, "message", {})
        if get_property(message, "bearing") is not None:
            with state_lock:
                last_face_seen[0] = time.monotonic()

    def face_seen_recently() -> bool:
        with state_lock:
            return time.monotonic() - last_face_seen[0] <= FACE_ACTIVE_WINDOW

    def set_face_eyes(face_seen: bool) -> None:
        if eyes_showing_face[0] == face_seen:
            return
        image = FACE_IMAGE if face_seen else IDLE_IMAGE
        misty.display_image(image).raise_for_status()
        eyes_showing_face[0] = face_seen

    pitch_index = 0
    yaw_index = 0
    try:
        misty.display_image(IDLE_IMAGE).raise_for_status()
        misty.register_event(
            Events.FaceRecognition,
            "MainFacePresence",
            keep_alive=True,
            callback_function=on_face_event,
        )
        time.sleep(0.5)
        misty.start_face_detection().raise_for_status()
        detection_started = True
        misty.create_action(name=follow_action, script="FOLLOW-FACE;").raise_for_status()
        misty.create_action(name=stop_action, script="STOP-FOLLOW;").raise_for_status()

        print("Idle scan is running. Press Ctrl+C to stop.")
        while True:
            if face_seen_recently():
                set_face_eyes(True)
                if not tracking_started:
                    print("Face detected; starting face tracking.")
                    misty.start_action(name=follow_action, useVisionData=True).raise_for_status()
                    tracking_started = True

                if not description_attempted:
                    description_attempted = True
                    try:
                        print("Greeting the person.")
                        misty.speak("Hey!", flush=True, language="en-US").raise_for_status()
                        print("Taking a photo and asking Ollama for a clothing detail...")
                        image_bytes = take_picture(misty)
                        detail = describe_person(image_bytes)
                        if detail:
                            phrase = f"I like your {detail}."
                            print(f"Misty says: {phrase}")
                            misty.speak(phrase, flush=True, language="en-US").raise_for_status()
                        else:
                            print("Ollama found no specific visible detail to mention.")
                    except Exception as error:
                        print(f"Could not describe or speak the person: {error}")
                time.sleep(0.2)
                continue

            if tracking_started:
                print("Face no longer detected; returning to idle scan.")
                set_face_eyes(False)
                misty.stop_action().raise_for_status()
                misty.start_action(name=stop_action, useVisionData=True).raise_for_status()
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
            pitch_index = (pitch_index + 1) % len(PITCH_POSITIONS)
            if pitch_index == 0:
                yaw_index = (yaw_index + 1) % len(YAW_POSITIONS)
    except KeyboardInterrupt:
        print("Stopping Misty...")
    finally:
        if tracking_started:
            try:
                misty.stop_action().raise_for_status()
                misty.start_action(name=stop_action, useVisionData=True).raise_for_status()
            except Exception as error:
                print(f"Could not stop face tracking cleanly: {error}")
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
        for action_name in (follow_action, stop_action):
            try:
                misty.delete_action(name=action_name).raise_for_status()
            except Exception:
                pass


if __name__ == "__main__":
    main()
