"""Save a picture of the tracked person for the compliment.

Only the face is detected, so the person's outline is estimated from the face box:
a few face-widths wide and several face-heights tall, cut off at the picture's edges.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import cv2

if TYPE_CHECKING:
    import numpy as np

RECENT_DIR = Path(__file__).resolve().parent / "recent_images"
FULL_IMAGE = "full.jpg"
PERSON_IMAGE = "person.jpg"
PERSON_WIDTH = 3.5
ABOVE_FACE = 0.75
BELOW_FACE = 7.0

BOX_COLOR = (0, 200, 0)
BOX_THICKNESS = 3
JPEG_QUALITY = 85

Box = tuple[int, int, int, int]


def person_box(face_box: tuple[int, int, int, int], frame_shape: tuple[int, ...]) -> Box:
    x, y, w, h = face_box
    frame_h, frame_w = frame_shape[:2]
    centre = x + w / 2
    left = max(0, round(centre - PERSON_WIDTH * w / 2))
    right = min(frame_w, round(centre + PERSON_WIDTH * w / 2))
    top = max(0, round(y - ABOVE_FACE * h))
    bottom = min(frame_h, round(y + BELOW_FACE * h))
    return left, top, right, bottom


def _write_jpeg(path: Path, image: np.ndarray) -> bytes:
    ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise RuntimeError(f"Could not encode {path.name} as JPEG")
    encoded = data.tobytes()
    temporary = path.with_suffix(".part")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)
    return encoded


def save_tracked_photo(frame: np.ndarray, face_box: tuple[int, int, int, int]) -> bytes:
    left, top, right, bottom = person_box(face_box, frame.shape)
    if right - left < 2 * BOX_THICKNESS or bottom - top < 2 * BOX_THICKNESS:
        raise RuntimeError(f"The tracked person's box is empty: {(left, top, right, bottom)}")

    marked = frame.copy()
    inset = BOX_THICKNESS // 2
    cv2.rectangle(
        marked,
        (left + inset, top + inset),
        (right - 1 - inset, bottom - 1 - inset),
        BOX_COLOR,
        BOX_THICKNESS,
    )

    RECENT_DIR.mkdir(exist_ok=True)
    _write_jpeg(RECENT_DIR / FULL_IMAGE, marked)
    return _write_jpeg(RECENT_DIR / PERSON_IMAGE, marked[top:bottom, left:right])
