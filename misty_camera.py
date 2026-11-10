import base64


def take_picture(misty) -> bytes:
    """Capture an image and return the decoded JPEG bytes."""
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

    return base64.b64decode(image_base64)
