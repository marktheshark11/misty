"""Local control panel backend for Misty. Run: python server.py [--port 8000]"""
import ast
import collections
import json
import os
import re
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from settings import MISTY_IP

ROOT = Path(__file__).resolve().parent
UI = ROOT / "ui"
DATA = Path(os.getenv("MISTY_UI_DATA_DIR") or ROOT)
BEHAVIOURS = ["main", "idle_mode", "follow_face", "approach_face"]
SCRIPTS = {}  # test-only override: name -> script path
if os.getenv("MISTY_UI_TEST_BEHAVIOUR"):
    BEHAVIOURS.append("test")
    SCRIPTS["test"] = os.environ["MISTY_UI_TEST_BEHAVIOUR"]
MAX_BODY = 1_000_000
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
         ".png": "image/png", ".jpg": "image/jpeg"}
LOCAL = ("localhost", "127.0.0.1")

lock = threading.Lock()
proc = None  # (name, Popen)
log = collections.deque(maxlen=2000)  # (index, line)
counter = 0


class HttpError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def read_pump(p):
    global counter
    for line in p.stdout:
        with lock:
            log.append((counter, line.rstrip("\n")))
            counter += 1


def running():
    global proc
    if proc and proc[1].poll() is not None:
        proc = None
    return proc[0] if proc else None


def knobs_defaults():
    """Parse `NAME = literal  # comment` lines from approach_face.py's knob block."""
    src = (ROOT / "approach_face.py").read_text()
    block = src.split("# --- Calibration knobs", 1)[1].split("\ndef ", 1)[0]
    out = []
    for line in block.splitlines():
        m = re.match(r"([A-Z][A-Z0-9_]*) = (.*?)(?:\s+# (.*))?$", line)
        if m:
            v = ast.literal_eval(m[2])
            out.append((m[1], list(v) if isinstance(v, tuple) else v, m[3] or ""))
    return out


def load_json(name, default):
    try:
        return json.loads((DATA / name).read_text())
    except (OSError, ValueError):
        return default


def save_json(name, obj):
    tmp = DATA / (name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, DATA / name)


def knobs_response():
    over = load_json("knobs.json", {})
    return {"knobs": [{"name": n, "default": d, "value": over.get(n, d), "help": h}
                      for n, d, h in knobs_defaults()]}


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def stop_behaviour():
    global proc
    with lock:
        name = running()
        p = proc[1] if name else None
    if p:
        if os.name == "nt":
            p.terminate()
        else:
            p.send_signal(signal.SIGINT)
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
    # Make the robot safe regardless; each call is independent.
    # The avstreaming/camera calls give Misty its camera back if a laptop-faces run was killed.
    for path, body in [("drive/stop", None), ("faces/detection/stop", None), ("avstreaming/stop", None),
                       ("services/avstreaming/disable", None), ("services/camera/enable", None),
                       ("head", {"pitch": -15, "roll": 0, "yaw": 0, "velocity": 40, "units": "degrees"})]:
        try:
            requests.post(f"http://{MISTY_IP}/api/{path}", json=body, timeout=2)
        except requests.RequestException:
            pass


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, ctype="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise HttpError(413, "body too large")
        return self.rfile.read(n)

    def json_body(self):
        try:
            return json.loads(self.body() or b"{}")
        except ValueError:
            raise HttpError(400, "invalid JSON")

    def guard(self, path):
        """Block other web pages / DNS rebinding from driving the robot."""
        origin = self.headers.get("Origin")
        host = urlparse("//" + (self.headers.get("Host") or "")).hostname
        if host not in LOCAL or (origin is not None and urlparse(origin).hostname not in LOCAL):
            raise HttpError(403, "forbidden")

    def handle_any(self):
        url = urlparse(self.path)
        path = url.path
        try:
            self.guard(path)
            if path.startswith("/misty/"):
                return self.proxy(path[len("/misty/"):], url.query)
            if self.command == "GET" and (path == "/" or path.startswith("/ui/")):
                return self.static("index.html" if path == "/" else path[len("/ui/"):])
            route = getattr(self, "api_" + self.command.lower() + "_" + path.strip("/").replace("/", "_"), None)
            if not route:
                raise HttpError(404, "not found")
            self.send(200, route(parse_qs(url.query)))
        except (BrokenPipeError, ConnectionResetError):
            pass  # browser gave up on the request
        except HttpError as e:
            self.reply_error(e.status, e.message)
        except Exception as e:  # unexpected: report as JSON, keep serving
            self.reply_error(500, str(e))

    def reply_error(self, status, message):
        try:
            self.send(status, {"error": message})
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = do_POST = do_PUT = do_DELETE = handle_any

    def static(self, rel):
        f = (UI / rel).resolve()
        if not f.is_relative_to(UI.resolve()) or not f.is_file():
            raise HttpError(404, "not found")
        self.send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))

    def proxy(self, path, query):
        data = self.body()
        headers = {"Content-Type": self.headers["Content-Type"]} if self.headers.get("Content-Type") else {}
        try:
            r = requests.request(self.command, f"http://{MISTY_IP}/api/{path}" + (f"?{query}" if query else ""),
                                 data=data or None, headers=headers, timeout=(2, 10))  # (connect, read)
        except requests.RequestException as e:
            raise HttpError(502, str(e))
        self.send(r.status_code, r.content, r.headers.get("Content-Type", "application/octet-stream"))

    def api_get_api_state(self, q):
        since = int(q.get("since", ["0"])[0] or 0)
        with lock:
            return {"misty_ip": MISTY_IP, "behaviour": running(), "behaviours": BEHAVIOURS,
                    "log": [l for i, l in log if i >= since], "next": counter}

    def api_post_api_behaviour_start(self, q):
        global proc
        name = self.json_body().get("name")
        if name not in BEHAVIOURS:
            raise HttpError(400, "unknown behaviour")
        with lock:
            if running():
                raise HttpError(409, f"{running()} is already running")
            script = SCRIPTS.get(name, f"{name}.py")
            p = subprocess.Popen([sys.executable, "-u", script], cwd=ROOT, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            proc = (name, p)
        threading.Thread(target=read_pump, args=(p,), daemon=True).start()
        return {"ok": True}

    def api_post_api_behaviour_stop(self, q):
        stop_behaviour()
        return {"ok": True}

    def api_get_api_knobs(self, q):
        return knobs_response()

    def api_put_api_knobs(self, q):
        values = self.json_body().get("values")
        if not isinstance(values, dict):
            raise HttpError(400, "values must be an object")
        defaults = {n: d for n, d, _ in knobs_defaults()}
        for n, v in values.items():
            if n not in defaults:
                raise HttpError(400, f"unknown knob {n}")
            d = defaults[n]
            ok = (isinstance(v, list) and len(v) == len(d) and all(map(is_num, v))) if isinstance(d, list) else is_num(v)
            if not ok:
                raise HttpError(400, f"invalid value for {n}")
        save_json("knobs.json", values)
        return knobs_response()

    def api_get_api_config(self, q):
        return load_json("ui_config.json", {})

    def api_put_api_config(self, q):
        cfg = self.json_body()
        if not isinstance(cfg, dict):
            raise HttpError(400, "config must be an object")
        save_json("ui_config.json", cfg)
        return {"ok": True}

    def api_post_api_describe(self, q):
        try:
            from mistyPy.Robot import Robot
            import misty_camera
            import ollama_client
            return {"text": ollama_client.describe_image(misty_camera.take_picture(Robot(MISTY_IP)))}
        except Exception as e:
            raise HttpError(502, str(e))

    def log_message(self, *args):
        pass

    def log_error(self, fmt, *args):
        print("error:", fmt % args, file=sys.stderr)


if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8000
    print(f"Control panel on http://127.0.0.1:{port} (Misty at {MISTY_IP})", flush=True)
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        stop_behaviour()
