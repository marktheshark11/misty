"""Idle-scan for a face, walk up to it, and speak a brief clothing compliment."""

import threading
import time
import json
from pathlib import Path

from mistyPy.Events import Events
from mistyPy.Robot import Robot

from approach_face import LOOP_PERIOD, FaceApproach
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
NAMES_FILE = Path(__file__).with_name("known_names.json")


def load_names():
    try:
        data = json.loads(NAMES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> None:
    print("Connecting to Misty...")
    misty = Robot(MISTY_IP)
    approach = FaceApproach(misty)
    eyes_showing_face = [False]
    head_petted = threading.Event()
    shutting_down = threading.Event()
    speech_event_index = [0]
    detection_started = False
    tracking_started = False
    description_attempted = False
    known_names = load_names()

    def speak_and_wait(text: str) -> None:
        """Speak one line and wait for Misty's playback-complete event."""
        speech_event_index[0] += 1
        event_name = f"MainSpeechComplete{speech_event_index[0]}"
        completed = threading.Event()
        subscription = misty.register_event(
            Events.TextToSpeechComplete,
            event_name,
            keep_alive=False,
            callback_function=lambda event: completed.set(),
        )

        # Wait for the WebSocket subscription to be established before speaking,
        # so a short utterance cannot finish before its completion event is heard.
        deadline = time.monotonic() + 5
        while getattr(subscription, "initial_flag", False) and time.monotonic() < deadline:
            time.sleep(0.05)

        try:
            misty.speak(
                text,
                flush=True,
                utteranceId=event_name,
                language="en-US",
            ).raise_for_status()
            if not completed.wait(30):
                raise TimeoutError(f"Timed out waiting for Misty to finish saying: {text}")
        finally:
            if not completed.is_set():
                misty.unregister_event(event_name)

    def on_touch_event(event) -> None:
        message = get_property(event, "message", {})
        position = str(get_property(message, "sensorPosition", "")).casefold()
        is_head = position in {"headfront", "headback", "headleft", "headright", "scruff"}
        if is_head and get_property(message, "isContacted", False):
            head_petted.set()

    def ask_to_be_petted() -> None:
        try:
            head_petted.clear()
            while not head_petted.is_set() and not shutting_down.is_set():
                started = time.monotonic()
                speak_and_wait("Would you like to pet my head?")
                wait_time = max(0, 3.0 - (time.monotonic() - started))
                if head_petted.wait(wait_time):
                    break
            if head_petted.is_set() and not shutting_down.is_set():
                speak_and_wait("Thank you!")
        except Exception as error:
            print(f"Could not ask to be petted: {error}")

    def face_seen_recently() -> bool:
        return approach.seconds_since_face() <= FACE_ACTIVE_WINDOW

    def set_face_eyes(face_seen: bool) -> None:
        if eyes_showing_face[0] == face_seen:
            return
        image = FACE_IMAGE if face_seen else IDLE_IMAGE
        misty.display_image(image).raise_for_status()
        eyes_showing_face[0] = face_seen

    def wave() -> None:
        # A short, gentle right-arm wave while Misty remains stopped.
        for position in (55, 35, 55, 35, 55, 0):
            misty.move_arm("right", position, 35, units="degrees").raise_for_status()

    def greet_and_compliment() -> None:
        # Runs in a thread so Misty keeps holding distance during capture and speech.
        try:
            print("Taking a picture, waving, and greeting the person...")
            photo = take_picture(misty)
            wave()
            speak_and_wait("Hi, I'm Misty!")
            name = "human"
            detail = describe_person(photo)
            if not detail:
                print("Ollama found no specific visible detail to mention.")
            else:
                phrase = f"Nice to meet you, {name}. I like your {detail}." if name else f"I like your {detail}."
                print(f"Misty says: {phrase}")
                speak_and_wait(phrase)
            ask_to_be_petted()
        except Exception as error:
            print(f"Could not complete the greeting: {error}")

    pitch_index = 0
    yaw_index = 0
    try:
        misty.display_image(IDLE_IMAGE).raise_for_status()
        approach.register()
        misty.register_event(
            Events.TouchSensor,
            "MainHeadPetting",
            keep_alive=True,
            callback_function=on_touch_event,
        )
        time.sleep(0.5)
        misty.start_face_detection().raise_for_status()
        detection_started = True

        print("Idle scan is running. Press Ctrl+C to stop.")
        while True:
            if face_seen_recently():
                set_face_eyes(True)
                if not tracking_started:
                    print("Face detected; approaching.")
                    approach.face_forward(PITCH_POSITIONS[pitch_index])
                    tracking_started = True

                arrived = approach.step()
                if arrived and not description_attempted:
                    description_attempted = True
                    print("Arrived in front of the person.")
                    threading.Thread(target=greet_and_compliment, daemon=True).start()
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
        shutting_down.set()
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
        approach.restore_hazards()
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
