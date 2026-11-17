"""Ask a local Ollama vision model about a picture."""

import base64

import requests

from settings import OLLAMA_HOST, OLLAMA_MODEL


PERSON_PROMPT = (
    "Look at the person's clothing. Return only its clearly visible color and one piece of clothing, "
    "for example: 'blue shirt' or 'black jacket'. Do not mention anything else. "
    "Do not guess the color or clothing. If either is not clearly visible, return exactly: clothes."
)


def _ask_about_image(prompt: str, image_bytes: bytes, temperature: float, max_tokens: int) -> str:
    """Send one picture and a question to the Ollama vision model; return its reply."""
    print(f"Sending {len(image_bytes)} bytes directly to Ollama ({OLLAMA_MODEL})...")
    response = requests.post(
        f"{OLLAMA_HOST}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [base64.b64encode(image_bytes).decode("ascii")],
                }
            ],
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": temperature, "num_predict": max_tokens},
        },
        timeout=180,
    )
    response.raise_for_status()
    return response.json().get("message", {}).get("content", "").strip()


def describe_person(image_bytes: bytes):
    """Return a visible clothing color and item, defaulting to 'clothes' if unclear."""
    reply = _ask_about_image(PERSON_PROMPT, image_bytes, temperature=0.2, max_tokens=30)
    detail = reply.strip('"').strip()
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
