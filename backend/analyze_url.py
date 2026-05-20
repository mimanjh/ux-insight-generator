"""
End-to-end pipeline: URL -> screenshot -> UX analysis -> JSON.

This is the v2 product interface. Composes capture.py (Playwright)
and analyze_screenshot.py (Claude tool use) — no new logic of its own.

Usage:
    python -m backend.analyze_url https://www.amazon.com/dp/B07XYZ
"""

import argparse
import json
from pathlib import Path

from backend.analyze_screenshot import analyze_screenshot, OUTPUT_DIR
from backend.capture import capture_url, url_to_filename


def analyze_url(url: str) -> Path:
    """Capture `url` and run the analyzer on it.

    CLI convenience wrapper. Writes findings JSON to disk; does NOT save
    the intermediate screenshot. If you want the screenshot, run
    `python -m backend.capture <url> --output path.png` separately.

    Returns the path to the findings JSON.
    """
    print(f"Capturing {url}...")
    image_bytes, media_type = capture_url(url)

    print("Analyzing...")
    findings = analyze_screenshot(image_bytes, media_type)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    findings_path = OUTPUT_DIR / f"{url_to_filename(url)}.json"
    findings_path.write_text(
        json.dumps(findings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {findings_path}")

    return findings_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Capture a URL and run a UX analysis on the screenshot."
    )
    parser.add_argument("url", help="URL to analyze")
    args = parser.parse_args()

    analyze_url(args.url)
