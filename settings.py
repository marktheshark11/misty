import os


MISTY_IP = os.getenv("MISTY_IP", "192.168.1.237")
if not MISTY_IP:
    raise RuntimeError("Set the MISTY_IP environment variable to Misty's network address.")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5vl:3b")
CAPTURE_FILE = "misty_capture.jpg"
