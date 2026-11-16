import base64
import time

from face_utils import FACE_IMAGE, IDLE_IMAGE

CAMERA_IMAGE = "e_SystemCamera.jpg"


def show_camera_face(misty) -> None:
    """Show the camera face while a picture is being taken."""
    misty.display_image(fileName=CAMERA_IMAGE)


def restore_face(misty, face_visible: bool) -> None:
    """Restore the eyes that match the current face-detection state."""
    time.sleep(0.2)
    misty.display_image(fileName=FACE_IMAGE if face_visible else IDLE_IMAGE)


def take_picture(misty, face_visible=None) -> bytes:
    """Capture an image and return the decoded JPEG bytes."""
    show_camera_face(misty)
    try:
        print("Taking picture...")
        response = misty.get_request(
            "cameras/rgb",
            json={
                "base64": True,
                "fileName": "vision.jpg",
                "width": 640,
                "height": 480,
                "displayOnScreen": False,
                "overwriteExisting": True,
            },
            timeout=(3, 15),
        )

        print("Misty status:", response.status_code)
        response.raise_for_status()
        data = response.json()

        result = data.get("result", data)
        image_base64 = (
            result.get("base64")
            or result.get("Base64")
            or result.get("image")
            or result.get("Image")
        )
        if not image_base64:
            raise RuntimeError(f"Could not find Base64 image in Misty's response: {data}")

        if image_base64.startswith("data:") and "," in image_base64:
            image_base64 = image_base64.split(",", 1)[1]
        return base64.b64decode(image_base64)
    finally:
        restore_face(misty, bool(face_visible and face_visible()))
