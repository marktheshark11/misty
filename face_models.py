"""Download and verify the OpenCV face models used by the PC-side tracker."""

from __future__ import annotations

import hashlib
import os
import urllib.request

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
_ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"

# YuNet: fast face detector (~0.2 MB). SFace: face embedding model for identity (~39 MB).
MODELS = {
    "detector": (
        "face_detection_yunet_2023mar.onnx",
        f"{_ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "recognizer": (
        "face_recognition_sface_2021dec.onnx",
        f"{_ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
}


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_path(key: str) -> str:
    """Return the local path of a model, downloading and verifying it if needed."""
    filename, url, expected = MODELS[key]
    path = os.path.join(MODEL_DIR, filename)
    if os.path.exists(path) and _sha256(path) == expected:
        return path

    os.makedirs(MODEL_DIR, exist_ok=True)
    print(f"Downloading {filename}...")
    tmp_path = path + ".part"
    urllib.request.urlretrieve(url, tmp_path)
    actual = _sha256(tmp_path)
    if actual != expected:
        os.remove(tmp_path)
        raise RuntimeError(f"Checksum mismatch for {filename}: expected {expected}, got {actual}")
    os.replace(tmp_path, path)
    return path
