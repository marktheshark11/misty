"""Stream Misty's camera to this PC over RTSP with minimal latency."""

from __future__ import annotations

import socket
import threading
import time

import av

# Low-latency RTSP: no input buffering, no reordering delay. The probe limits keep
# FFmpeg's stream analysis (5 s by default) from eating the open timeout.
RTSP_OPTIONS = {
    "fflags": "nobuffer",
    "flags": "low_delay",
    "max_delay": "0",
    "reorder_queue_size": "0",
    "analyzeduration": "2000000",
    "probesize": "1000000",
}
# TCP avoids smeared frames from lost packets on Wi-Fi; UDP is tried as a fallback in
# case Misty's RTSP server or a firewall doesn't cooperate with TCP.
RTSP_TRANSPORTS = ("tcp", "udp")
OPEN_TIMEOUT = 20.0  # Misty's encoder can take several seconds to emit its first keyframe.
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
    """Switch Misty from on-board vision to RTSP streaming (Misty hosts the server)."""
    _check(misty.enable_av_streaming_service(), "Enabling the AV streaming service")
    try:
        misty.stop_av_streaming()  # Clear a stream left running by a crashed earlier run.
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
        # Misty's docs disagree on whether 30 fps is allowed; fall back to a safe rate.
        print(f"Stream start at {fps} fps failed ({error}); retrying at 24 fps.")
        _check(
            misty.start_av_streaming(
                url=f"rtspd:{port}", width=width, height=height,
                frameRate=24, videoBitRate=bitrate,
            ),
            "Starting the AV stream",
        )


def stop_misty_stream(misty) -> None:
    """Stop streaming and give the camera back to Misty (needed for TakePicture etc.)."""
    for call, what in (
        (misty.stop_av_streaming, "stop AV streaming"),
        (misty.enable_camera_service, "re-enable the camera service"),
    ):
        try:
            call().raise_for_status()
        except Exception as error:
            print(f"Could not {what}: {error}")


class LatestFrameReader:
    """Reads the RTSP stream continuously on a thread and keeps only the newest frame.

    Draining the stream as fast as it arrives is what keeps latency low: the consumer
    always gets the freshest frame, never a stale one queued in a buffer.
    """

    def __init__(self, url: str):
        self.url = url
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
            transport = RTSP_TRANSPORTS[attempt % len(RTSP_TRANSPORTS)]
            attempt += 1
            options = dict(RTSP_OPTIONS, rtsp_transport=transport) if is_rtsp else {}
            if is_rtsp:
                print(f"Connecting to {self.url} over {transport.upper()}...")
            try:
                with av.open(self.url, options=options,
                             timeout=(OPEN_TIMEOUT, READ_TIMEOUT)) as container:
                    stream = container.streams.video[0]
                    # Frame threading buffers several frames inside the decoder; slice
                    # threading doesn't. That alone can save 100+ ms at 30 fps.
                    stream.codec_context.thread_type = "SLICE"
                    backoff = 0.5
                    attempt -= 1  # Keep using the transport that worked.
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

    def latest(self):
        with self._cond:
            return None if self._frame is None else self._frame.copy()

    def close(self) -> None:
        self._running = False
        self._thread.join(timeout=2.0)
