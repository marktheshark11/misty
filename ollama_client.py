import base64

import requests

from settings import OLLAMA_HOST, OLLAMA_MODEL


DESCRIPTION_PROMPT = "Describe the main subject of this image in one short, direct sentence."
PERSON_PROMPT = (
    "Look at the person in this image. Find one specific, clearly visible detail about their "
    "appearance, clothing, or accessories, such as 'gray shirt', 'curly hair', 'red glasses', "
    "or 'blue striped scarf'. "
    "Return only the detail in 2 to 6 words, with no sentence or explanation. Do not guess. "
    "If there is no person or no specific detail is visible, return exactly NONE."
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
    """Return a specific visible clothing/accessory detail, or None if unclear."""
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
        "none",
        "unknown",
        "not visible",
        "no clear detail",
        "i can't see a clear clothing detail",
    }:
        return None
    # Guard against a model ignoring the requested short-detail format.
    if len(detail.split()) > 10 or "can't see" in detail.casefold() or "cannot see" in detail.casefold():
        return None
    return detail.rstrip(".!?")
