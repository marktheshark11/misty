"""Run Misty's built-in face-tracking behavior."""

import time
import threading
import uuid

from mistyPy.Events import Events
from mistyPy.Robot import Robot

from misty_camera import take_picture
from ollama_client import describe_image
from settings import MISTY_IP

NEUTRAL_PITCH = -15  # Negative pitch tilts Misty's head upward.
DESCRIPTION_INTERVAL = 10
FACE_ACTIVE_WINDOW = 2.0
NO_FACE_IMAGE = "e_defaultcontent.jpg"
FACE_SEEN_IMAGE = "e_joy.jpg"


def get_property(data, name, default=None):
    """Read an event property without depending on JSON key capitalization."""
    if not isinstance(data, dict):
        return default
    wanted = name.casefold()
    return next((value for key, value in data.items() if key.casefold() == wanted), default)


def main():
    misty = Robot(MISTY_IP)
    suffix = uuid.uuid4().hex[:8]
    follow_action = f"face_follow_{suffix}"
    stop_action = f"face_follow_stop_{suffix}"
    face_lock = threading.Lock()
    last_face_seen = [0.0]
    last_face_log = [0.0]
    eyes_showing_face = [False]
    last_description_time = 0.0
    detection_started = False

    try:
        misty.display_image(NO_FACE_IMAGE).raise_for_status()
        print("Centering Misty's head...")
        response = misty.move_head(
            pitch=NEUTRAL_PITCH,
            roll=0,
            yaw=0,
            duration=1.0,
            units="degrees",
        )
        response.raise_for_status()
        time.sleep(1.0)

        def set_face_expression(face_seen):
            with face_lock:
                if eyes_showing_face[0] == face_seen:
                    return
                image = FACE_SEEN_IMAGE if face_seen else NO_FACE_IMAGE
                try:
                    misty.display_image(image).raise_for_status()
                    eyes_showing_face[0] = face_seen
                    print("Showing face-seen eyes." if face_seen else "Showing idle eyes.")
                except Exception as error:
                    print(f"Could not change Misty's eye expression: {error}")

        def on_face_event(event):
            message = get_property(event, "message", {})
            bearing = get_property(message, "bearing")
            if bearing is not None:
                now = time.monotonic()
                with face_lock:
                    last_face_seen[0] = now
                    should_log = now - last_face_log[0] >= 3.0
                    if should_log:
                        last_face_log[0] = now
                if should_log:
                    print(f"Face-detection event received (bearing {bearing}°).")
                set_face_expression(True)

        misty.register_event(
            Events.FaceRecognition,
            "FaceFollowDescriptionPresence",
            keep_alive=True,
            callback_function=on_face_event,
        )
        time.sleep(0.5)
        response = misty.start_face_detection()
        response.raise_for_status()
        detection_started = True

        # Misty's native FOLLOW-FACE behavior handles face tracking and centering.
        response = misty.create_action(name=follow_action, script="FOLLOW-FACE;")
        response.raise_for_status()
        response = misty.create_action(name=stop_action, script="STOP-FOLLOW;")
        response.raise_for_status()

        print("Starting Misty's built-in face tracking.")
        print("Keep your face in front of the camera. Press Ctrl+C to stop.")
        response = misty.start_action(name=follow_action, useVisionData=True)
        response.raise_for_status()

        def face_seen_recently():
            with face_lock:
                return time.monotonic() - last_face_seen[0] <= FACE_ACTIVE_WINDOW

        while True:
            now = time.monotonic()
            currently_tracking = face_seen_recently()
            set_face_expression(currently_tracking)
            if currently_tracking and now - last_description_time >= DESCRIPTION_INTERVAL:
                try:
                    print("Face detected; describing the current camera image...")
                    image_bytes = take_picture(misty)
                    description = describe_image(image_bytes)

                    # Only speak if face tracking is still active after recognition.
                    if face_seen_recently():
                        print("=== DESCRIPTION ===")
                        print(description)
                        speech_response = misty.speak(
                            f"I see: {description}",
                            flush=True,
                            language="en-US",
                        )
                        speech_response.raise_for_status()
                        print("Description spoken; continuing face tracking.")
                    else:
                        print("Face was lost before the description was ready; skipping speech.")
                except Exception as error:
                    print(f"Could not describe or speak the image: {error}")
                finally:
                    last_description_time = time.monotonic()
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("Stopping face tracking...")
    finally:
        try:
            misty.stop_action().raise_for_status()
        except Exception as error:
            print(f"Could not stop the current action: {error}")
        try:
            misty.start_action(name=stop_action, useVisionData=True).raise_for_status()
        except Exception as error:
            print(f"Could not send STOP-FOLLOW: {error}")
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
            print(f"Could not return Misty's head to its upward neutral position: {error}")
        try:
            misty.display_image(NO_FACE_IMAGE).raise_for_status()
        except Exception as error:
            print(f"Could not restore idle eyes: {error}")
        for action_name in (follow_action, stop_action):
            try:
                misty.delete_action(name=action_name).raise_for_status()
            except Exception:
                pass


if __name__ == "__main__":
    main()
