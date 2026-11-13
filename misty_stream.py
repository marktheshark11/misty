"""Stream Misty's camera to this PC over RTSP with minimal latency."""

from __future__ import annotations

import threading
import time

import av

# Low-latency RTSP: TCP (no smeared frames from lost UDP packets on Wi-Fi), no input
# buffering, no reordering delay.
RTSP_OPTIONS = {
    "rtsp_transport": "tcp",
    "fflags": "nobuffer",
    "flags": "low_delay",
    "max_delay": "0",
    "reorder_queue_size": "0",
}


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
        while self._running:
            try:
                options = RTSP_OPTIONS if self.url.startswith("rtsp://") else {}
                with av.open(self.url, options=options, timeout=5.0) as container:
                    stream = container.streams.video[0]
                    # Frame threading buffers several frames inside the decoder; slice
                    # threading doesn't. That alone can save 100+ ms at 30 fps.
                    stream.codec_context.thread_type = "SLICE"
                    backoff = 0.5
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
                    print(f"Video stream unavailable ({error}); retrying...")
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
