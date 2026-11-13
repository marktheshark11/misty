import os


MISTY_IP = os.getenv("MISTY_IP", "192.168.1.237")
if not MISTY_IP:
    raise RuntimeError("Set the MISTY_IP environment variable to Misty's network address.")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5vl:3b")
CAPTURE_FILE = "misty_capture.jpg"

# PC-side face tracking (follow_face.py). Misty streams video; this PC does the vision.
STREAM_PORT = int(os.getenv("MISTY_STREAM_PORT", "1936"))
STREAM_WIDTH = int(os.getenv("MISTY_STREAM_WIDTH", "640"))
STREAM_HEIGHT = int(os.getenv("MISTY_STREAM_HEIGHT", "480"))
STREAM_FPS = int(os.getenv("MISTY_STREAM_FPS", "30"))
STREAM_BITRATE = int(os.getenv("MISTY_STREAM_BITRATE", "1500000"))
# Seconds between Misty capturing a frame and this PC decoding it. Used to look up
# where the head was pointing when the frame was taken. Typical Wi-Fi: 0.15-0.35.
STREAM_LATENCY = float(os.getenv("MISTY_STREAM_LATENCY", "0.25"))
# Horizontal field of view of the streamed image, in degrees.
CAMERA_HFOV = float(os.getenv("MISTY_CAMERA_HFOV", "70"))
