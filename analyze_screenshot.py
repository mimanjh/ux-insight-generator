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
PROMPT = """You are a senior product designer doing a focused UX review of a single screenshot from a digital product. You have 15+ years of experience and have shipped consumer and developer-tool products. You care about evidence-based critique, not generic best practices.

## What you're looking at
This is ONE screenshot — likely above-the-fold or a single screen. You cannot see:
- Other pages or scroll positions
- Interactive states (hover, focus, loading)
- The target audience or business goals
- What this product is competing against

Acknowledge these limits when relevant. Do NOT invent context to fill the gap.

## How to analyze
1. First, identify what the product appears to be and who its likely user is. State this briefly so I can correct you if you're wrong.
2. Note 1-2 things that are working well. A balanced review is more useful than pure criticism.
3. Identify UX issues. For each issue:
   - Anchor it to something specific you can actually see in the screenshot
   - Rate severity: HIGH (blocks/frustrates core user task), MEDIUM (causes friction or confusion), LOW (polish/nitpick)
   - Rate your confidence: HIGH (clearly visible issue), MEDIUM (likely issue but depends on context), LOW (speculation)
   - Explain why it matters for *this product's likely users*, not abstract UX principles
   - Suggest a concrete fix

## Rules
- Skip an issue rather than padding the list. 2 sharp observations beat 5 generic ones.
- If a suggestion depends on knowing the target audience or business goals, say so explicitly.
- Do NOT apply corporate-design defaults (e.g., "use a professional headshot") without justifying why they apply HERE.
- Do NOT critique things you cannot verify from the screenshot alone (e.g., page load speed, accessibility of interactions you can't test).

## Output format
**What I'm looking at:** [1-2 sentences identifying the product and likely user]

**What's working:** [1-2 bullet points]

**Issues (ordered by severity):**

For each issue, use this format:
### [Issue title]
- **Severity:** HIGH / MEDIUM / LOW
- **Confidence:** HIGH / MEDIUM / LOW
- **What I see:** [specific observation anchored to the screenshot]
- **Why it matters:** [user-impact reasoning, specific to this product]
- **Suggested fix:** [concrete change]
- **Caveat (if any):** [what depends on context I can't see]
"""


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