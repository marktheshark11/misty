"""Follow one person's face and periodically describe the scene (vision runs on this PC).

While a face is tracked, this asks local Ollama to describe the camera image about every
10 seconds and has Misty say it. Run with --preview to see what the tracker sees.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import time

from mistyPy.Robot import Robot

from face_session import FaceSession, StopRequested, is_following
from face_tracker import TRACKING
from ollama_client import describe_image
from settings import MISTY_IP

DESCRIPTION_INTERVAL = 10


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preview", action="store_true", help="show the tracker's view")
    parser.add_argument("--no-describe", action="store_true", help="skip Ollama descriptions")
    args = parser.parse_args()

    misty = Robot(MISTY_IP)
    describer = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    job = None
    last_description_time = 0.0

    try:
        with FaceSession(misty, preview=args.preview) as session:

            def describe_and_speak(jpeg_bytes):
                description = describe_image(jpeg_bytes)
                if is_following(session.result):
                    print(f"=== DESCRIPTION ===\n{description}")
                    misty.speak(f"I see: {description}", flush=True,
                                language="en-US").raise_for_status()
                else:
                    print("Face was lost before the description was ready; skipping speech.")

            print("Tracking. Press Ctrl+C to stop.")
            while True:
                result = session.step()
                if job is not None and job.done():
                    if job.exception():
                        print(f"Could not describe or speak the image: {job.exception()}")
                    job = None
                now = time.monotonic()
                if (not args.no_describe and result is not None and result.state == TRACKING
                        and job is None and now - last_description_time >= DESCRIPTION_INTERVAL):
                    job = describer.submit(describe_and_speak, session.jpeg())
                    last_description_time = now
    except (KeyboardInterrupt, StopRequested):
        print("Stopping face tracking...")
    finally:
        describer.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
