"""Gently scan for a face, then greet and follow it."""

import threading
import time
import uuid

from mistyPy.Events import Events
from mistyPy.Robot import Robot

from settings import MISTY_IP

# Misty's head uses degrees. Keep the motion small and slow.
PITCH_POSITIONS = (-22, -8)
YAW_POSITIONS = (-18, 18)
MOVE_DURATION = 2.5
# Face events can arrive intermittently; wait through short gaps before resuming the scan.
FACE_ACTIVE_WINDOW = 8.0
IDLE_IMAGE = "e_defaultcontent.jpg"
FACE_IMAGE = "e_joy.jpg"


def get_property(data, name, default=None):
    if not isinstance(data, dict):
        return default
    wanted = name.casefold()
    return next((value for key, value in data.items() if key.casefold() == wanted), default)


def main():
    misty = Robot(MISTY_IP)
    suffix = uuid.uuid4().hex[:8]
    follow_action = f"idle_face_follow_{suffix}"
    stop_action = f"idle_face_stop_{suffix}"
    state_lock = threading.Lock()
    last_face_seen = [0.0]
    greeted_current_face = [False]
    eyes_showing_face = [False]
    detection_started = False
    tracking_started = False

    def on_face_event(event):
        message = get_property(event, "message", {})
        # FaceRecognition events include a bearing when a face is present.
        if get_property(message, "bearing") is not None:
            with state_lock:
                last_face_seen[0] = time.monotonic()

    def face_seen_recently():
        with state_lock:
            return time.monotonic() - last_face_seen[0] <= FACE_ACTIVE_WINDOW

    def set_face_eyes(face_seen):
        if eyes_showing_face[0] == face_seen:
            return
        image = FACE_IMAGE if face_seen else IDLE_IMAGE
        misty.display_image(image).raise_for_status()
        eyes_showing_face[0] = face_seen

    try:
        misty.display_image(IDLE_IMAGE).raise_for_status()
        misty.register_event(
            Events.FaceRecognition,
            "IdleModeFacePresence",
            keep_alive=True,
            callback_function=on_face_event,
        )
        time.sleep(0.5)
        misty.start_face_detection().raise_for_status()
        detection_started = True
        misty.create_action(name=follow_action, script="FOLLOW-FACE;").raise_for_status()
        misty.create_action(name=stop_action, script="STOP-FOLLOW;").raise_for_status()

        print("Idle mode is running. Press Ctrl+C to stop.")
        pitch_index = 0
        yaw_index = 0
        while True:
            if face_seen_recently():
                set_face_eyes(True)
                if not tracking_started:
                    misty.start_action(name=follow_action, useVisionData=True).raise_for_status()
                    tracking_started = True
                if not greeted_current_face[0]:
                    misty.speak("Hello", flush=True, language="en-US").raise_for_status()
                    greeted_current_face[0] = True
                time.sleep(0.2)
                continue

            if tracking_started:
                set_face_eyes(False)
                misty.stop_action().raise_for_status()
                misty.start_action(name=stop_action, useVisionData=True).raise_for_status()
                misty.move_head(pitch=-15, roll=0, yaw=0, duration=1.0, units="degrees").raise_for_status()
                tracking_started = False
                greeted_current_face[0] = False
                time.sleep(1.0)
                continue

            set_face_eyes(False)

            # Alternate looking up/down and then a little left/right.
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
        print("Stopping idle mode...")
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
            misty.move_head(pitch=-15, roll=0, yaw=0, duration=1.0, units="degrees").raise_for_status()
        except Exception as error:
            print(f"Could not return Misty's head to neutral: {error}")
        for action_name in (follow_action, stop_action):
            try:
                misty.delete_action(name=action_name).raise_for_status()
            except Exception:
                pass


if __name__ == "__main__":
    main()
