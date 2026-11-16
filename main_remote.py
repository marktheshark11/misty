"""Run the same interaction as main.py, with face detection on this PC.

Misty streams her camera to this PC, which finds and tracks the face; everything else
(scan, photo, approach, greeting, compliment, petting) is main.py's own code.
Run with --preview to see what the tracker sees.
"""

import argparse

from mistyPy.Robot import Robot

from face_session import StopRequested
from main import run
from remote_approach import RemoteFaceApproach
from settings import MISTY_IP


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preview", action="store_true", help="show the tracker's view")
    args = parser.parse_args()

    print("Connecting to Misty...")
    misty = Robot(MISTY_IP)
    try:
        run(misty, RemoteFaceApproach(misty, preview=args.preview))
    except StopRequested:
        print("Stopped from the preview window.")


if __name__ == "__main__":
    main()
