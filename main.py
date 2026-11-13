"""Idle-scan for a face, follow it, and speak a brief clothing compliment."""

from __future__ import annotations

import argparse

from mistyPy.Robot import Robot

from face_session import FaceSession, StopRequested
from face_tracker import SEARCHING
from idle_mode import run_scan_and_engage
from ollama_client import describe_person
from settings import CAPTURE_FILE, MISTY_IP


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="show the tracker's view")
    args = parser.parse_args()

    print("Connecting to Misty...")
    misty = Robot(MISTY_IP)

    try:
        with FaceSession(misty, preview=args.preview) as session:

            def compliment():
                print("Greeting the person.")
                misty.speak("Hey!", flush=True, language="en-US").raise_for_status()
                print("Asking Ollama for a clothing detail...")
                # Misty's camera is busy streaming, so the photo comes from the stream.
                image_bytes = session.jpeg()
                with open(CAPTURE_FILE, "wb") as image_file:
                    image_file.write(image_bytes)
                detail = describe_person(image_bytes)
                if not detail:
                    print("Ollama found no specific visible detail to mention.")
                elif session.result is None or session.result.state == SEARCHING:
                    print(f"Person left before the compliment was ready ({detail}).")
                else:
                    phrase = f"I like your {detail}."
                    print(f"Misty says: {phrase}")
                    misty.speak(phrase, flush=True, language="en-US").raise_for_status()

            run_scan_and_engage(session, compliment)
    except (KeyboardInterrupt, StopRequested):
        print("Stopping Misty...")


if __name__ == "__main__":
    main()
