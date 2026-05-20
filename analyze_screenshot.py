"""
UX Insight Generator — v1 MVP

Reads a local screenshot, sends it to Claude with a UX-critique prompt,
and prints suggested product improvements as plain text.
"""

import base64
from pathlib import Path

from dotenv import load_dotenv
from anthropic import Anthropic

# --- Config ---
# Hardcoded for v1. We'll make these configurable later.
SCREENSHOT_PATH = "test_screenshot.png"
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 1500  # bigger than hello_claude — we want a real critique

# This prompt is intentionally simple for v1. We will iterate on it
# heavily in the next step once we see how the model behaves with a
# baseline prompt. Don't over-engineer before observing reality.
PROMPT = """You are a senior UX designer reviewing a screenshot of a digital product.

Look carefully at the interface and suggest concrete product improvements.
For each suggestion:
- Describe the issue you observed
- Explain why it matters from a user experience perspective
- Suggest a specific improvement

Focus on the most impactful issues, not nitpicks. Aim for 3-5 suggestions."""


def load_image_as_base64(path: str) -> tuple[str, str]:
    """
    Read an image file and return (media_type, base64_string).

    The API needs both: the media type tells Claude what format the bytes
    are in, and the base64 string is the actual image data as text.
    """
    image_path = Path(path)

    # Figure out the media type from the file extension.
    # Claude accepts: png, jpeg, gif, webp
    suffix = image_path.suffix.lower()
    media_type_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    if suffix not in media_type_map:
        raise ValueError(f"Unsupported image type: {suffix}")
    media_type = media_type_map[suffix]

    # Read the raw bytes and encode them as base64.
    # .read_bytes() returns bytes; base64.b64encode returns bytes;
    # .decode("ascii") turns those bytes into a regular string.
    image_bytes = image_path.read_bytes()
    image_b64 = base64.b64encode(image_bytes).decode("ascii")

    return media_type, image_b64


def analyze_screenshot(image_path: str) -> str:
    """Send the screenshot to Claude and return its critique as a string."""
    load_dotenv()
    client = Anthropic()

    media_type, image_b64 = load_image_as_base64(image_path)

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": PROMPT,
                    },
                ],
            }
        ],
    )

    return response.content[0].text


if __name__ == "__main__":
    print(f"Analyzing {SCREENSHOT_PATH}...\n")
    critique = analyze_screenshot(SCREENSHOT_PATH)
    print(critique)