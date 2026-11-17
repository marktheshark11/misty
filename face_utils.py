"""Settings and small helpers shared by the scan, approach and session code."""

# Misty's head uses degrees; negative pitch tilts it upward. This is the resting pose.
NEUTRAL_PITCH = -15
# Idle scan positions. Keep the motion small and slow.
PITCH_POSITIONS = (-22, -8)
YAW_POSITIONS = (-18, 18)
MOVE_DURATION = 2.5
# Face events can arrive intermittently; wait through short gaps before resuming the scan.
FACE_ACTIVE_WINDOW = 8.0
IDLE_IMAGE = "e_defaultcontent.jpg"
FACE_IMAGE = "e_joy.jpg"


def get_property(data, name, default=None):
    """Read a key from an event dict regardless of capitalization (Misty's JSON varies)."""
    if not isinstance(data, dict):
        return default
    wanted = name.casefold()
    return next((value for key, value in data.items() if key.casefold() == wanted), default)


def center_head(misty):
    """Send the head to its resting pose over one second; returns Misty's response."""
    return misty.move_head(pitch=NEUTRAL_PITCH, roll=0, yaw=0, duration=1.0, units="degrees")


def best_effort(what: str, call) -> None:
    """Run a cleanup step, reporting a failure instead of letting it stop the others."""
    try:
        call()
    except Exception as error:
        print(f"Could not {what}: {error}")
