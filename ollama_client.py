import base64

import requests

from settings import OLLAMA_HOST, OLLAMA_MODEL


DESCRIPTION_PROMPT = "Describe the main subject of this image in one short, direct sentence."


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
