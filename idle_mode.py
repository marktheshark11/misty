"""Gently scan for a face, then greet and follow it (vision runs on this PC)."""

from __future__ import annotations

import argparse
import concurrent.futures
import time

from mistyPy.Robot import Robot

from face_session import NEUTRAL_PITCH, FaceSession, StopRequested, is_following
from face_tracker import SEARCHING
from settings import MISTY_IP

# Misty's head uses degrees. Keep the motion small and slow.
PITCH_POSITIONS = (-22, -8)
YAW_POSITIONS = (-18, 18)
MOVE_DURATION = 2.5
# After a person leaves, look straight ahead this long before scanning again.
RETURN_PAUSE = 1.0


class IdleScanner:
    """Alternates looking up/down, then a little left/right, one move at a time."""

    def __init__(self, session: FaceSession):
        self.session = session
        self.pitch_index = 0
        self.yaw_index = 0
        self.next_move = 0.0

    def update(self) -> None:
        now = time.monotonic()
        if now < self.next_move:
            return
        self.session.move_head(YAW_POSITIONS[self.yaw_index],
                               PITCH_POSITIONS[self.pitch_index], MOVE_DURATION)
        self.next_move = now + MOVE_DURATION
        self.pitch_index = (self.pitch_index + 1) % len(PITCH_POSITIONS)
        if self.pitch_index == 0:
            self.yaw_index = (self.yaw_index + 1) % len(YAW_POSITIONS)

    def pause(self, seconds: float) -> None:
        self.next_move = time.monotonic() + seconds


def run_scan_and_engage(session: FaceSession, on_new_person) -> None:
    """Scan until a face is locked, follow it, and call on_new_person() once per person.

    on_new_person runs on a background thread so speech or Ollama never stalls tracking.
    The person counts as gone once the tracker gives up on them (after its re-acquire
    window), at which point the head returns to neutral and scanning resumes.
    """
    scanner = IdleScanner(session)
    worker = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    job = None
    engaged = False
    print("Idle scan is running. Press Ctrl+C to stop.")
    try:
        while True:
            result = session.step()
            if job is not None and job.done():
                if job.exception():
                    print(f"Could not greet the person: {job.exception()}")
                job = None
            if result is None:
                continue
            if result.state == SEARCHING:
                if engaged:
                    engaged = False
                    print("Face no longer detected; returning to idle scan.")
                    session.move_head(0, NEUTRAL_PITCH, RETURN_PAUSE)
                    scanner.pause(RETURN_PAUSE)
                scanner.update()
            elif not engaged and is_following(result):
                engaged = True
                print("Face detected; following.")
                if job is None:
                    job = worker.submit(on_new_person)
    finally:
        worker.shutdown(wait=False, cancel_futures=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="show the tracker's view")
    args = parser.parse_args()

    misty = Robot(MISTY_IP)

    def greet():
        misty.speak("Hello", flush=True, language="en-US").raise_for_status()

    try:
        with FaceSession(misty, preview=args.preview) as session:
            run_scan_and_engage(session, greet)
    except (KeyboardInterrupt, StopRequested):
        print("Stopping idle mode...")


if __name__ == "__main__":
    main()
