import os


MISTY_IP = os.getenv("MISTY_IP", "192.168.1.237")
if not MISTY_IP:
    raise RuntimeError("Set the MISTY_IP environment variable to Misty's network address.")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5vl:3b")

# PC-side face tracking. Misty streams video; this PC does the vision.
STREAM_PORT = int(os.getenv("MISTY_STREAM_PORT", "1936"))
STREAM_WIDTH = int(os.getenv("MISTY_STREAM_WIDTH", "640"))
STREAM_HEIGHT = int(os.getenv("MISTY_STREAM_HEIGHT", "480"))
# Misty's docs: the frame rate must be "greater than 1 and less than 30".
STREAM_FPS = int(os.getenv("MISTY_STREAM_FPS", "24"))
STREAM_BITRATE = int(os.getenv("MISTY_STREAM_BITRATE", "1500000"))
# "tcp" or "udp": which RTSP transport to try first (the other is the fallback).
STREAM_TRANSPORT = os.getenv("MISTY_STREAM_TRANSPORT", "tcp").lower()
if STREAM_TRANSPORT not in ("tcp", "udp"):
    raise RuntimeError("MISTY_STREAM_TRANSPORT must be tcp or udp.")
# Degrees to turn each frame clockwise so people are upright: 0, 90, 180, 270, or
# "auto" to work it out from the first faces seen. Misty's docs say the stream arrives
# rotated 90 degrees counterclockwise, which would make this 90.
STREAM_ROTATE = os.getenv("MISTY_STREAM_ROTATE", "auto").lower()
if STREAM_ROTATE not in ("auto", "0", "90", "180", "270"):
    raise RuntimeError("MISTY_STREAM_ROTATE must be auto, 0, 90, 180 or 270.")
# Field of view across the longer side of the streamed image, in degrees. Sets how face
# positions convert to angles and how face size converts to distance.
CAMERA_FOV = float(os.getenv("MISTY_CAMERA_FOV", "70"))
