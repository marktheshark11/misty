"""Stub Misty robot for testing the control panel without hardware."""
import base64
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

JPEG_B64 = "/9j/4AAQSkZJRgABAQAASABIAAD/4QBMRXhpZgAATU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQAAAABAAAACKADAAQAAAABAAAACAAAAAD/7QA4UGhvdG9zaG9wIDMuMAA4QklNBAQAAAAAAAA4QklNBCUAAAAAABDUHYzZjwCyBOmACZjs+EJ+/8AAEQgACAAIAwEiAAIRAQMRAf/EAB8AAAEFAQEBAQEBAAAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIBAwMCBAMFBQQEAAABfQECAwAEEQUSITFBBhNRYQcicRQygZGhCCNCscEVUtHwJDNicoIJChYXGBkaJSYnKCkqNDU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6g4SFhoeIiYqSk5SVlpeYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk5ebn6Onq8fLz9PX29/j5+v/EAB8BAAMBAQEBAQEBAQEAAAAAAAABAgMEBQYHCAkKC//EALURAAIBAgQEAwQHBQQEAAECdwABAgMRBAUhMQYSQVEHYXETIjKBCBRCkaGxwQkjM1LwFWJy0QoWJDThJfEXGBkaJicoKSo1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2RlZmdoaWpzdHV2d3h5eoKDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uLj5OXm5+jp6vLz9PX29/j5+v/bAEMAAgICAgICAwICAwUDAwMFBgUFBQUGCAYGBgYGCAoICAgICAgKCgoKCgoKCgwMDAwMDA4ODg4ODw8PDw8PDw8PD//bAEMBAgICBAQEBwQEBxALCQsQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEP/dAAQAAf/aAAwDAQACEQMRAD8AKKKKAP/Z"
requests_seen = []

ROUTES = {
    "/api/images/list": {"result": [{"name": "e_defaultcontent.jpg"}, {"name": "e_joy.jpg"}, {"name": "e_Sleeping.jpg"}]},
    "/api/audio/list": {"result": [{"name": "s_Joy.wav"}, {"name": "s_Awe.wav"}]},
    "/api/battery": {"result": {"chargePercent": 0.76, "isCharging": False, "voltage": 8.1}},
}


class Handler(BaseHTTPRequestHandler):
    def reply(self, body, ctype="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self):
        url = urlparse(self.path)
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        if not url.path.startswith("/api/"):
            return self.reply({"error": "not found"})
        if url.path == "/api/requests":
            return self.reply(requests_seen)
        requests_seen.append({"method": self.command, "path": self.path, "body": body})
        print(self.command, self.path, body, flush=True)
        if self.command == "GET" and url.path == "/api/cameras/rgb":
            if parse_qs(url.query).get("base64") == ["true"]:
                return self.reply({"result": {"base64": JPEG_B64, "contentType": "image/jpeg"}})
            return self.reply(base64.b64decode(JPEG_B64), "image/jpeg")
        if self.command == "GET" and url.path in ROUTES:
            return self.reply(ROUTES[url.path])
        self.reply({"result": True, "status": "Success"})

    do_GET = do_POST = do_PUT = do_DELETE = handle_any

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8001
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
