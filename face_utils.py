"""Shared face-scan settings and event helpers used by main.py."""

# Misty's head uses degrees. Keep the motion small and slow.
PITCH_POSITIONS = (-22, -8)
YAW_POSITIONS = (-18, 18)
MOVE_DURATION = 2.5
# Face events can arrive intermittently; wait through short gaps before resuming the scan.
FACE_ACTIVE_WINDOW = 8.0
IDLE_IMAGE = "e_defaultcontent.jpg"
FACE_IMAGE = "e_joy.jpg"


def get_property(data, name, default=None):
    if not isinstance(data, dict):
        return default
    wanted = name.casefold()
    return next((value for key, value in data.items() if key.casefold() == wanted), default)


