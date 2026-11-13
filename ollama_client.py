import base64

import requests

from settings import OLLAMA_HOST, OLLAMA_MODEL


DESCRIPTION_PROMPT = "Describe the main subject of this image in one short, direct sentence."
PERSON_PROMPT = (
    "Look at the person's clothing. Return only its clearly visible color and one piece of clothing, "
    "for example: 'blue shirt' or 'black jacket'. Do not mention anything else. "
    "Do not guess the color or clothing. If either is not clearly visible, return exactly: clothes."
)


def describe_image(image_bytes: bytes) -> str:
    """Return a short image description from Ollama."""
    print(f"Sending {len(image_bytes)} bytes directly to Ollama ({OLLAMA_MODEL})...")
    response = requests.post(
        f"{OLLAMA_HOST}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": DESCRIPTION_PROMPT,
                    "images": [base64.b64encode(image_bytes).decode("ascii")],
                }
            ],
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_predict": 80},
        },
        timeout=180,
    )
    response.raise_for_status()

    description = response.json().get("message", {}).get("content", "").strip()
    if not description:
        raise RuntimeError("Ollama returned an empty description")
    return description


def describe_person(image_bytes: bytes):
    """Return a visible clothing color and item, defaulting to 'clothes' if unclear."""
    print(f"Sending {len(image_bytes)} bytes directly to Ollama ({OLLAMA_MODEL})...")
    response = requests.post(
        f"{OLLAMA_HOST}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": PERSON_PROMPT,
                    "images": [base64.b64encode(image_bytes).decode("ascii")],
                }
            ],
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": 0.2, "num_predict": 30},
        },
        timeout=180,
    )
    response.raise_for_status()

    detail = response.json().get("message", {}).get("content", "").strip().strip('"').strip()
    if not detail or detail.casefold().strip(" .!\n") in {
        "clothes",
        "none",
        "unknown",
        "not visible",
        "no clear detail",
        "i can't see a clear clothing detail",
    }:
        return "clothes"
    # Guard against a model returning a sentence instead of a color and garment.
    if len(detail.split()) > 5 or any(char in detail for char in ".!?\n"):
        return "clothes"
    return detail.rstrip(".!?")
