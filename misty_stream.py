"""Stream Misty's camera to this PC over RTSP with minimal latency."""

from __future__ import annotations

import socket
import threading
import time

import av

from face_utils import best_effort

RTSP_OPTIONS = {
    "fflags": "nobuffer",
    "flags": "low_delay",
    "max_delay": "0",
    "reorder_queue_size": "0",
    "analyzeduration": "2000000",
    "probesize": "1000000",
}

RTSP_TRANSPORTS = ("tcp", "udp")
OPEN_TIMEOUT = 20.0
READ_TIMEOUT = 5.0


def port_is_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _check(response, what: str) -> None:
    response.raise_for_status()
    try:
        status = response.json().get("status")
    except ValueError:
        return
    if status and status.lower() != "success":
        raise RuntimeError(f"{what} failed: {response.text}")


def start_misty_stream(misty, port: int, width: int, height: int, fps: int, bitrate: int) -> None:
    _check(misty.enable_av_streaming_service(), "Enabling the AV streaming service")
    try:
        misty.stop_av_streaming()
    except Exception:
        pass
    try:
        _check(
            misty.start_av_streaming(
                url=f"rtspd:{port}", width=width, height=height,
                frameRate=fps, videoBitRate=bitrate,
            ),
            "Starting the AV stream",
        )
    except Exception as error:
        print(f"Stream start at {fps} fps failed ({error}); retrying at 24 fps.")
        _check(
            misty.start_av_streaming(
                url=f"rtspd:{port}", width=width, height=height,
                frameRate=24, videoBitRate=bitrate,
            ),
            "Starting the AV stream",
        )


def stop_misty_stream(misty) -> None:
    best_effort("stop AV streaming", lambda: misty.stop_av_streaming().raise_for_status())
    best_effort(
        "re-enable the camera service",
        lambda: misty.enable_camera_service().raise_for_status(),
    )


class LatestFrameReader:

    def __init__(self, url: str, transport: str = "tcp"):
        self.url = url
        self._transports = (transport,) + tuple(t for t in RTSP_TRANSPORTS if t != transport)
        self._cond = threading.Condition()
        self._frame = None
        self._time = 0.0
        self._seq = 0
        self._running = True
        self.frames_received = 0
        self._thread = threading.Thread(target=self._run, name="rtsp-reader", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        backoff = 0.5
        attempt = 0
        while self._running:
            is_rtsp = self.url.startswith("rtsp://")
            transport = self._transports[attempt % len(self._transports)]
            attempt += 1
            options = dict(RTSP_OPTIONS, rtsp_transport=transport) if is_rtsp else {}
            if is_rtsp:
                print(f"Connecting to {self.url} over {transport.upper()}...")
            try:
                with av.open(self.url, options=options,
                             timeout=(OPEN_TIMEOUT, READ_TIMEOUT)) as container:
                    stream = container.streams.video[0]
                    stream.codec_context.thread_type = "SLICE"
                    backoff = 0.5
                    attempt -= 1
                    if is_rtsp:
                        print(f"Video connected over {transport.upper()}.")
                    for frame in container.decode(stream):
                        if not self._running:
                            return
                        image = frame.to_ndarray(format="bgr24")
                        now = time.monotonic()
                        with self._cond:
                            self._frame, self._time = image, now
                            self._seq += 1
                            self.frames_received += 1
                            self._cond.notify_all()
                if self._running:
                    print("Video stream ended; reconnecting...")
            except (av.error.FFmpegError, OSError) as error:
                if self._running:
                    print(f"Video stream unavailable over {transport.upper()} ({error}); retrying...")
            time.sleep(backoff)
            backoff = min(backoff * 2, 3.0)

    def wait_for_frame(self, after_seq: int, timeout: float = 1.0):
        """Return (frame, receive_time, seq) newer than after_seq, or None on timeout."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._seq > after_seq, timeout=timeout):
                return None
            return self._frame, self._time, self._seq

    def close(self) -> None:
        self._running = False
        self._thread.join(timeout=2.0)
