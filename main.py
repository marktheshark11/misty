from mistyPy.Robot import Robot

from misty_camera import take_picture
from ollama_client import describe_image
from settings import CAPTURE_FILE, MISTY_IP


def main() -> None:
    print("Connecting to Misty...")
    misty = Robot(MISTY_IP)

    image_bytes = take_picture(misty)
    print(f"Received image from Misty: {len(image_bytes)} bytes")

    with open(CAPTURE_FILE, "wb") as image_file:
        image_file.write(image_bytes)
    print(f"Saved image as {CAPTURE_FILE}")

    description = describe_image(image_bytes)
    print("\n=== DESCRIPTION ===")
    print(description)

    print("\nMisty is speaking the result...")
    speech_response = misty.speak(
        f"I see: {description}",
        flush=True,
        language="en-US",
    )
    print("Misty speech status:", speech_response.status_code)
    speech_response.raise_for_status()


if __name__ == "__main__":
    main()
