"""
UX Insight Generator — v4 (structured JSON output)

Reads a local screenshot, sends it to Claude with a UX-critique prompt,
and writes structured findings to a JSON file in runs/.

v4 changes vs v3:
- Switched from prose output to structured JSON via tool use.
- Removed the "Output format" section from the prompt; the tool schema
  now defines the output shape.
- Output is written to runs/<image_stem>_v4.json instead of stdout.
"""

import base64
import json
from datetime import date
from pathlib import Path
import argparse

from dotenv import load_dotenv
from anthropic import Anthropic

# --- Config ---
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 1500
OUTPUT_DIR = Path("runs/v4")

# --- Tool definition ---
# This is the structured-output schema. Claude is forced to call this
# "tool" with arguments matching the schema, which gives us reliable
# JSON without parsing prose.
TOOL = {
    "name": "report_ux_findings",
    "description": (
        "Report the structured results of a UX review of a single screenshot. "
        "Call this exactly once with all findings."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "what_im_looking_at": {
                "type": "string",
                "description": (
                    "1-2 sentences identifying what the product appears to be "
                    "and who its likely user is."
                ),
            },
            "whats_working": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1-2 things the screen is doing well.",
            },
            "findings": {
                "type": "array",
                "description": (
                    "UX issues, ordered by severity (highest first). "
                    "Prefer fewer, stronger findings over more, weaker ones."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Short label for the issue.",
                        },
                        "theme": {
                            "type": "string",
                            "enum": [
                                "trust_and_credibility",
                                "information_hierarchy",
                                "navigation_and_wayfinding",
                                "content_clarity",
                                "interaction_design",
                                "accessibility",
                                "other",
                            ],
                            "description": (
                                "Broad category of the issue. See the prompt "
                                "for definitions. Use 'other' only when no "
                                "category fits."
                            ),
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "observation_confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "judgment_confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "what_i_see": {
                            "type": "string",
                            "description": (
                                "Specific observation anchored to the screenshot."
                            ),
                        },
                        "why_it_matters": {
                            "type": "string",
                            "description": (
                                "User-impact reasoning, specific to this product."
                            ),
                        },
                        "suggested_fix": {
                            "type": "string",
                            "description": (
                                "Concrete change, calibrated to confidence per "
                                "the rules in the prompt."
                            ),
                        },
                        "caveat": {
                            "type": ["string", "null"],
                            "description": (
                                "Optional: what depends on context that can't "
                                "be seen. Use null if no caveat applies."
                            ),
                        },
                    },
                    "required": [
                        "title",
                        "theme",
                        "severity",
                        "observation_confidence",
                        "judgment_confidence",
                        "what_i_see",
                        "why_it_matters",
                        "suggested_fix",
                        "caveat",
                    ],
                },
            },
        },
        "required": ["what_im_looking_at", "whats_working", "findings"],
    },
}

PROMPT = """## Context
Today's date is {today}. Use this when interpreting any dates visible in the screenshot — your training data is not a reliable source of "what is current."

You are a senior product designer doing a focused UX review of a single screenshot from a digital product. You have 15+ years of experience and have shipped consumer and developer-tool products. You care about evidence-based critique, not generic best practices.

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

## Theme taxonomy
Tag each finding with one theme. Pick the *best* fit; don't invent overlaps. Use `other` only when none of these clearly apply.

- **trust_and_credibility:** anything that makes the user question the legitimacy, accuracy, or honesty of what they're seeing (data errors, dark patterns, missing context that would build confidence).
- **information_hierarchy:** what's emphasized vs. de-emphasized, redundant content, competing focal points, scan-ability of layout.
- **navigation_and_wayfinding:** how users move between states or sections, discoverability of actions, understanding "where am I / what can I do here."
- **content_clarity:** wording, labels, descriptions, and how well text communicates meaning. (Distinct from hierarchy — this is about the words themselves, not their placement.)
- **interaction_design:** behavior of controls, affordances, feedback, friction in actions the user is trying to take.
- **accessibility:** issues affecting users with disabilities (color contrast, color-alone signaling, keyboard nav, screen reader cues you can see evidence of).
- **other:** none of the above clearly applies. Use sparingly.

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

## Output
Call the `report_ux_findings` tool exactly once with your analysis. Order findings by severity (highest first). Use null for `caveat` when no caveat applies.
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


def analyze_screenshot(image_path: str) -> dict:
    """Send the screenshot to Claude and return parsed structured findings."""
    # override=True so a .env value wins over an empty/stale shell var.
    # (python-dotenv's default is the opposite, which silently breaks dev
    #  when something has already exported ANTHROPIC_API_KEY="".)
    load_dotenv(override=True)
    client = Anthropic()

    media_type, image_b64 = load_image_as_base64(image_path)

    # Ground the model with today's date — fixes "this date is in the future"
    # hallucinations on screenshots that contain dates near the training cutoff.
    prompt_text = PROMPT.format(today=date.today().isoformat())

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "report_ux_findings"},
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
                        "text": prompt_text,
                    },
                ],
            }
        ],
    )

    # Find the tool_use block. With tool_choice forcing our tool, there
    # should be exactly one. Hard-fail if not — that's our parsing policy.
    for block in response.content:
        if block.type == "tool_use" and block.name == "report_ux_findings":
            return block.input

    raise RuntimeError(
        f"Expected a tool_use block for 'report_ux_findings', got: "
        f"{[b.type for b in response.content]}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Analyze a screenshot and write UX findings to JSON."
    )
    parser.add_argument(
        "image_path",
        help="Path to the screenshot image (PNG, JPG, GIF, or WEBP)",
    )
    args = parser.parse_args()

    print(f"Analyzing {args.image_path}...")
    findings = analyze_screenshot(args.image_path)

    OUTPUT_DIR.mkdir(exist_ok=True)
    image_stem = Path(args.image_path).stem
    output_path = OUTPUT_DIR / f"{image_stem}.json"
    output_path.write_text(
        json.dumps(findings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Wrote {output_path}")