"""Diagnose why Misty's head doesn't follow: run this once and paste the output.

Tests, in order (about 30 seconds; watch Misty's head):
  1. Are head-position (ActuatorPosition) events arriving?
  2. Does the SDK's own move_head (lowercase keys, duration) move the head?
  3. Lowercase keys with velocity (what face tracking sends now).
  4. Capitalized keys with velocity (what face tracking sent before).
  5. Rapid retargeting at ~12 commands/second (how face tracking drives the head).
  6. The same rapid retargeting at ~3 commands/second.
  7. Optional (--with-stream): repeat test 3 while Misty's video stream is running.
"""

from __future__ import annotations

import argparse
import threading
import time

import requests
from mistyPy.Events import Events
from mistyPy.Robot import Robot

from settings import MISTY_IP, STREAM_BITRATE, STREAM_FPS, STREAM_HEIGHT, STREAM_PORT, STREAM_WIDTH

NEUTRAL_PITCH = -15
lock = threading.Lock()
latest = {}      # axis -> (time, value)
counts = {}      # sensorId -> number of messages
first_message = []


def _get(data, name):
    if not isinstance(data, dict):
        return None
    wanted = name.casefold()
    return next((v for k, v in data.items() if k.casefold() == wanted), None)


def on_actuator(event):
    message = _get(event, "message")
    if not first_message:
        first_message.append(event)
    sensor = str(_get(message, "sensorId") or _get(message, "sensorName") or "?")
    value = _get(message, "value")
    with lock:
        counts[sensor] = counts.get(sensor, 0) + 1
        axis = {"ahy": "yaw", "actuator_headyaw": "yaw",
                "ahp": "pitch", "actuator_headpitch": "pitch"}.get(sensor.casefold())
        if axis and isinstance(value, (int, float)):
            latest[axis] = (time.monotonic(), float(value))


def head_now():
    with lock:
        yaw = latest.get("yaw")
        pitch = latest.get("pitch")
    if not yaw or time.monotonic() - yaw[0] > 1.0:
        return None
    return round(yaw[1], 1), round(pitch[1], 1) if pitch else None


def post(session, body):
    try:
        response = session.post(f"http://{MISTY_IP}/api/head", json=body, timeout=2.0)
        return f"HTTP {response.status_code} {response.text[:160]}"
    except requests.RequestException as error:
        return f"ERROR {error}"


def report(name, before, target_yaw, reply=None):
    after = head_now()
    if reply:
        print(f"    Misty replied: {reply}")
    if before is None or after is None:
        verdict = "no head-position data; did the head turn? (note it)"
    else:
        moved = abs(after[0] - before[0])
        reached = abs(after[0] - target_yaw) < 4
        verdict = ("REACHED target" if reached else
                   f"moved {moved:.1f} deg but did NOT reach {target_yaw}" if moved > 2 else
                   "DID NOT MOVE")
    print(f"    head yaw before {before}, after {after}  ->  {verdict}")


def neutral(misty):
    misty.move_head(pitch=NEUTRAL_PITCH, roll=0, yaw=0, duration=1.0, units="degrees")
    time.sleep(1.8)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--with-stream", action="store_true",
                        help="also test head commands while Misty's video stream is running")
    args = parser.parse_args()

    misty = Robot(MISTY_IP)
    session = requests.Session()
    # Make sure nothing else is holding the head.
    for call in (misty.stop_action, misty.stop_face_detection):
        try:
            call()
        except Exception:
            pass

    print("1. Subscribing to head-position events (unfiltered)...")
    misty.register_event(Events.ActuatorPosition, "HeadCheckPose", debounce=100,
                         keep_alive=True, callback_function=on_actuator)
    time.sleep(2.0)
    with lock:
        print(f"    messages per sensor in 2 s: {counts or 'NONE'}")
    if first_message:
        print(f"    example message: {str(first_message[0])[:300]}")
    print(f"    current head (yaw, pitch): {head_now()}")

    try:
        print("2. SDK move_head, lowercase keys, duration 1.5 s -> yaw +30")
        neutral(misty)
        before = head_now()
        r = misty.move_head(pitch=NEUTRAL_PITCH, roll=0, yaw=30, duration=1.5, units="degrees")
        time.sleep(2.2)
        report("sdk", before, 30, f"HTTP {r.status_code} {r.text[:160]}")

        print("3. Lowercase keys, velocity 60 -> yaw -30")
        before = head_now()
        reply = post(session, {"pitch": NEUTRAL_PITCH, "roll": 0, "yaw": -30,
                               "velocity": 60, "units": "degrees"})
        time.sleep(2.5)
        report("lower-velocity", before, -30, reply)

        print("4. Capitalized keys, velocity 60 -> yaw +30")
        before = head_now()
        reply = post(session, {"Pitch": NEUTRAL_PITCH, "Roll": 0, "Yaw": 30,
                               "Velocity": 60, "Units": "degrees"})
        time.sleep(2.5)
        report("upper-velocity", before, 30, reply)

        for rate in (12, 3):
            print(f"5/6. Rapid retargeting at {rate} commands/s: yaw +30 -> -30 over 3 s")
            neutral(misty)
            post(session, {"pitch": NEUTRAL_PITCH, "roll": 0, "yaw": 30,
                           "velocity": 60, "units": "degrees"})
            time.sleep(2.0)
            before = head_now()
            start = time.monotonic()
            replies = set()
            while time.monotonic() - start < 3.0:
                yaw = 30 - 60 * (time.monotonic() - start) / 3.0
                replies.add(post(session, {"pitch": NEUTRAL_PITCH, "roll": 0, "yaw": yaw,
                                           "velocity": 60, "units": "degrees"})[:40])
                time.sleep(1.0 / rate)
            time.sleep(1.0)
            print(f"    distinct replies: {replies}")
            report(f"rapid-{rate}", before, -30)

        if args.with_stream:
            from misty_stream import start_misty_stream, stop_misty_stream
            print("7. Starting video stream, then lowercase velocity -> yaw +30")
            neutral(misty)
            start_misty_stream(misty, STREAM_PORT, STREAM_WIDTH, STREAM_HEIGHT,
                               STREAM_FPS, STREAM_BITRATE)
            time.sleep(3.0)
            try:
                before = head_now()
                reply = post(session, {"pitch": NEUTRAL_PITCH, "roll": 0, "yaw": 30,
                                       "velocity": 60, "units": "degrees"})
                time.sleep(2.5)
                report("streaming", before, 30, reply)
            finally:
                stop_misty_stream(misty)
    finally:
        neutral(misty)
        misty.unregister_all_events()
    print("Done. Please paste everything above.")


if __name__ == "__main__":
    main()
