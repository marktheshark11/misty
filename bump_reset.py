"""Detect a simultaneous press of Misty's two front bump sensors."""

import threading

from mistyPy.Events import Events

from face_utils import get_property


class BumpReset:
    """Request one reset while both front bump sensors are held together."""

    SENSOR_IDS = {"bfl": "left", "bfr": "right"}
    SENSOR_NAMES = {
        "bump_frontleft": "left",
        "bump_frontright": "right",
    }

    def __init__(self, misty):
        self.misty = misty
        self.requested = threading.Event()
        self._lock = threading.Lock()
        self._pressed = {"left": False, "right": False}
        self._latched = False

    def register(self) -> None:
        self.misty.register_event(
            Events.BumpSensor,
            "BumpReset",
            keep_alive=True,
            callback_function=lambda event: self._on_bump_event(event),
        )

    def _on_bump_event(self, event) -> None:
        message = get_property(event, "message", {})
        sensor_id = str(get_property(message, "sensorId", "")).casefold()
        sensor_name = str(get_property(message, "sensorName", "")).casefold()
        side = self.SENSOR_IDS.get(sensor_id) or self.SENSOR_NAMES.get(sensor_name)
        if side is None:
            return

        pressed = bool(get_property(message, "isContacted", False))
        with self._lock:
            self._pressed[side] = pressed
            both_pressed = all(self._pressed.values())
            if both_pressed and not self._latched:
                self._latched = True
                self.requested.set()
            elif not both_pressed:
                self._latched = False