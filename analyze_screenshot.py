"""
UX Insight Generator — v3 MVP

Reads a local screenshot, sends it to Claude with a UX-critique prompt,
and prints suggested product improvements as plain text.

v3 changes vs v2:
- Split single "Confidence" rating into "Observation confidence" and
  "Judgment confidence" to separate "did I see it correctly?" from
  "is this really a problem?"
- Added a "Calibrating your fixes" section that ties fix specificity
  to confidence levels (no pixel values without HIGH/HIGH).
- Added explicit instruction to suppress weak findings rather than
  padding severity to justify their inclusion.
"""

import base64
from pathlib import Path
import argparse

from dotenv import load_dotenv
from anthropic import Anthropic

# --- Config ---
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 1500

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
   - Rate observation confidence: HIGH (you can clearly see the element and what it's doing), MEDIUM (you can see it but its function or state is partly inferred), LOW (you're partly guessing at what the element is or does)
   - Rate judgment confidence: assuming your observation is correct, HIGH (this is clearly a problem for likely users), MEDIUM (this is probably a problem but depends on context, intent, or data you can't see), LOW (this might be intentional or a reasonable tradeoff)
   - Explain why it matters for *this product's likely users*, not abstract UX principles
   - Suggest a concrete fix (calibrated per the rules below)

## Rules
- Prefer fewer, stronger findings over more, weaker ones. A short critique of 3 strong findings is more valuable than 6 findings padded with weak ones.
- If both observation confidence and judgment confidence are LOW, omit the finding — unless the potential issue would be severe enough that flagging it is worth the uncertainty.
- If you find yourself inflating severity to justify including a finding, that's a signal to cut it instead.
- Stay within "what could this screen do differently." Omit findings that are really about user behavior, platform policy, posting norms, or organizational strategy.
- If a suggestion depends on knowing the target audience or business goals, say so explicitly.
- Do NOT apply corporate-design defaults (e.g., "use a professional headshot") without justifying why they apply HERE.
- Do NOT critique things you cannot verify from the screenshot alone (e.g., page load speed, accessibility of interactions you can't test).

## Calibrating your suggested fixes
Match the specificity of your fix to your confidence:

- **Both confidences HIGH:** You may suggest specific changes (concrete labels, specific layout changes, specific patterns to adopt).
- **Either confidence is MEDIUM:** Keep the fix directional. Say "increase spacing to improve hierarchy" or "clarify the label to indicate the action," not "16-24px spacing" or specific copy. Do not invent precise numbers, exact strings, or design tokens.
- **Either confidence is LOW:** Frame the fix as a question to investigate, not a prescription. "Worth testing whether users understand X" is appropriate; "Change X to Y" is not.

Specific pixel values, exact percentages, or precise copy require HIGH/HIGH. If you find yourself writing "minimum 16-24px" or "exactly 8px gap" or "change copy to '...'," check whether you actually have evidence for that specificity — you almost certainly don't from a single screenshot.

## Output format
**What I'm looking at:** [1-2 sentences identifying the product and likely user]

**What's working:** [1-2 bullet points]

**Issues (ordered by severity):**

For each issue, use this format:
### [Issue title]
- **Severity:** HIGH / MEDIUM / LOW
- **Observation confidence:** HIGH / MEDIUM / LOW
- **Judgment confidence:** HIGH / MEDIUM / LOW
- **What I see:** [specific observation anchored to the screenshot]
- **Why it matters:** [user-impact reasoning, specific to this product]
- **Suggested fix:** [concrete change, calibrated to your confidence per the rules above]
- **Caveat (if any):** [what depends on context I can't see]
"""


def load_image_as_base64(path: str) -> tuple[str, str]:
    """
    Read an image file and return (media_type, base64_string).

    The API needs both: the media type tells Claude what format the bytes
    are in, and the base64 string is the actual image data as text.
    """
    image_path = Path(path)

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
    parser = argparse.ArgumentParser(
        description="Analyze a screenshot and suggest UX improvements."
    )
    parser.add_argument(
        "image_path",
        help="Path to the screenshot image (PNG, JPG, GIF, or WEBP)",
    )
    args = parser.parse_args()

    print(f"Analyzing {args.image_path}...\n")
    critique = analyze_screenshot(args.image_path)
    print(critique)