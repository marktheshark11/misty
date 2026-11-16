import base64
import time

CAMERA_IMAGE = "e_SystemCamera.jpg"
AFTER_PHOTO_IMAGE = "e_DefaultContent.jpg"


def show_camera_face(misty) -> None:
    """Show the camera face while a picture is being taken."""
    misty.display_image(fileName=CAMERA_IMAGE)


def restore_face(misty) -> None:
    """Put the normal face back once the picture is taken."""
    time.sleep(0.2)
    misty.display_image(fileName=AFTER_PHOTO_IMAGE)


def take_picture(misty) -> bytes:
    """Capture an image and return the decoded JPEG bytes."""
    show_camera_face(misty)
    print("Taking picture...")
    response = misty.take_picture(
        base64=True,
        fileName="vision.jpg",
        width=640,
        height=480,
        displayOnScreen=False,
        overwriteExisting=True,
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
    restore_face(misty)
    return base64.b64decode(image_base64)
