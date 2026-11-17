"""Settings and small helpers shared by the scan, approach and session code."""

NEUTRAL_PITCH = -15
PITCH_POSITIONS = (-22, -8)
YAW_POSITIONS = (-18, 18)
MOVE_DURATION = 2.5
FACE_ACTIVE_WINDOW = 8.0
IDLE_IMAGE = "e_defaultcontent.jpg"
FACE_IMAGE = "e_joy.jpg"


def get_property(data, name, default=None):
    if not isinstance(data, dict):
        return default
    wanted = name.casefold()
    return next((value for key, value in data.items() if key.casefold() == wanted), default)


def center_head(misty):
    return misty.move_head(pitch=NEUTRAL_PITCH, roll=0, yaw=0, duration=1.0, units="degrees")


def best_effort(what: str, call) -> None:
    try:
        call()
    except Exception as error:
        print(f"Could not {what}: {error}")
