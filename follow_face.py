"""Follow one person's face with Misty, using vision on this PC instead of on the robot.

Misty streams its camera over RTSP; this PC detects faces (YuNet), keeps a lock on one
person by appearance (SFace), and drives Misty's head. While a face is tracked, it
periodically asks local Ollama to describe the scene and has Misty say it.

Run with --preview to see what the tracker sees.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import time

import cv2

try:  # Errors only; hides harmless DNN backend warnings.
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except AttributeError:
    pass
from mistyPy.Robot import Robot

from face_models import model_path
from face_tracker import COASTING, SEARCHING, TRACKING, FaceTracker, TrackerConfig
from head_controller import HeadController, HeadPose
from misty_stream import (LatestFrameReader, port_is_open, start_misty_stream,
                          stop_misty_stream)
from ollama_client import describe_image
from settings import (CAMERA_HFOV, MISTY_IP, STREAM_BITRATE, STREAM_FPS, STREAM_HEIGHT,
                      STREAM_LATENCY, STREAM_PORT, STREAM_WIDTH)

NEUTRAL_PITCH = -15  # Negative pitch tilts Misty's head upward.
DESCRIPTION_INTERVAL = 10
NO_FACE_IMAGE = "e_defaultcontent.jpg"
FACE_SEEN_IMAGE = "e_joy.jpg"
STATE_COLORS = {TRACKING: (0, 200, 0), COASTING: (0, 200, 255)}


def draw_preview(frame, result, fps, proc_ms):
    for face in result.faces:
        x, y, w, h = face.box
        is_target = face is result.target
        color = STATE_COLORS.get(result.state, (0, 0, 255)) if is_target else (160, 160, 160)
        cv2.rectangle(frame, (x, y), (x + w, y + h), color, 3 if is_target else 1)
        if face.similarity is not None:
            cv2.putText(frame, f"{face.similarity:.2f}", (x, y - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    cv2.putText(frame, f"{result.state}  {fps:.0f} fps  {proc_ms:.0f} ms", (8, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    cv2.imshow("Misty face tracking", frame)
    return cv2.waitKey(1) & 0xFF


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preview", action="store_true", help="show the tracker's view")
    parser.add_argument("--no-describe", action="store_true", help="skip Ollama descriptions")
    args = parser.parse_args()

    tracker = FaceTracker(model_path("detector"), model_path("recognizer"),
                          TrackerConfig(hfov_deg=CAMERA_HFOV))

    misty = Robot(MISTY_IP)
    commands = concurrent.futures.ThreadPoolExecutor(max_workers=1)  # Eyes etc.
    describer = concurrent.futures.ThreadPoolExecutor(max_workers=1)  # Ollama + speech
    reader = head = None
    eyes_showing_face = None
    description_job = None
    last_description_time = 0.0

    def set_eyes(face_seen):
        nonlocal eyes_showing_face
        if eyes_showing_face == face_seen:
            return
        eyes_showing_face = face_seen
        commands.submit(misty.display_image, FACE_SEEN_IMAGE if face_seen else NO_FACE_IMAGE)

    def describe_and_speak(jpeg_bytes):
        description = describe_image(jpeg_bytes)
        if tracker.state in (TRACKING, COASTING):
            print(f"=== DESCRIPTION ===\n{description}")
            misty.speak(f"I see: {description}", flush=True, language="en-US").raise_for_status()
        else:
            print("Face was lost before the description was ready; skipping speech.")

    try:
        set_eyes(False)
        misty.move_head(pitch=NEUTRAL_PITCH, roll=0, yaw=0, duration=1.0,
                        units="degrees").raise_for_status()

        print("Starting Misty's video stream (on-board vision is paused while streaming)...")
        start_misty_stream(misty, STREAM_PORT, STREAM_WIDTH, STREAM_HEIGHT,
                           STREAM_FPS, STREAM_BITRATE)
        # Misty's RTSP server takes a moment to come up after the start command.
        port_deadline = time.monotonic() + 15
        while not port_is_open(MISTY_IP, STREAM_PORT):
            if time.monotonic() > port_deadline:
                raise RuntimeError(
                    f"Misty accepted the stream command, but nothing is listening on "
                    f"{MISTY_IP}:{STREAM_PORT}. Try restarting Misty, or set MISTY_STREAM_PORT "
                    f"to another port (e.g. 1935).")
            time.sleep(0.5)
        reader = LatestFrameReader(f"rtsp://{MISTY_IP}:{STREAM_PORT}")

        pose = HeadPose(misty, start_yaw=0.0, start_pitch=NEUTRAL_PITCH)
        head = HeadController(MISTY_IP, pose, 0.0, NEUTRAL_PITCH, lead_time=STREAM_LATENCY)

        print("Waiting for video...")
        seq, deadline = 0, time.monotonic() + 75  # Allows a full TCP and a UDP attempt.
        while reader.wait_for_frame(seq, timeout=1.0) is None:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"Port {STREAM_PORT} on Misty is open, but no video arrived over TCP or UDP. "
                    f"If you're on Windows, allow Python through the firewall for UDP.")
        print("Tracking. Press Ctrl+C to stop" + (" (or q in the preview)." if args.preview else "."))

        stats_time, stats_frames, stats_proc = time.monotonic(), 0, 0.0
        fps = proc_ms = 0.0
        while True:
            item = reader.wait_for_frame(seq, timeout=2.0)
            if item is None:
                head.clear_target()
                continue
            frame, received, seq = item
            started = time.monotonic()

            captured = received - STREAM_LATENCY
            head_yaw, head_pitch = pose.at(captured)
            result = tracker.update(frame, head_yaw, head_pitch, t=captured)

            if result.state == SEARCHING:
                head.clear_target()
            else:
                head.set_target(result.yaw, result.pitch, result.vel_yaw, result.vel_pitch)
            set_eyes(result.state in (TRACKING, COASTING))

            now = time.monotonic()
            if (not args.no_describe and result.state == TRACKING
                    and now - last_description_time >= DESCRIPTION_INTERVAL
                    and (description_job is None or description_job.done())):
                ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    description_job = describer.submit(describe_and_speak, jpeg.tobytes())
                    last_description_time = now
            if description_job is not None and description_job.done():
                if description_job.exception():
                    print(f"Could not describe or speak the image: {description_job.exception()}")
                description_job = None

            stats_frames += 1
            stats_proc += now - started
            if now - stats_time >= 5.0:
                fps = stats_frames / (now - stats_time)
                proc_ms = 1000 * stats_proc / stats_frames
                source = "measured" if pose.measured_is_live() else "commanded"
                print(f"[{fps:.1f} fps, {proc_ms:.1f} ms/frame, {result.state}, head pose: {source}]")
                stats_time, stats_frames, stats_proc = now, 0, 0.0

            if args.preview and draw_preview(frame, result, fps, proc_ms) == ord("q"):
                break
    except KeyboardInterrupt:
        print("Stopping face tracking...")
    finally:
        if head is not None:
            head.close()
        if reader is not None:
            reader.close()
        stop_misty_stream(misty)
        misty.unregister_all_events()
        commands.shutdown(wait=True)
        describer.shutdown(wait=False, cancel_futures=True)
        for call, what in (
            (lambda: misty.move_head(pitch=NEUTRAL_PITCH, roll=0, yaw=0, duration=1.0,
                                     units="degrees"), "return the head to neutral"),
            (lambda: misty.display_image(NO_FACE_IMAGE), "restore idle eyes"),
        ):
            try:
                call().raise_for_status()
            except Exception as error:
                print(f"Could not {what}: {error}")
        if args.preview:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
