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
from backend.capture import capture_url


def analyze_url(url: str) -> tuple[Path, Path]:
    """Capture `url` and run the analyzer on it.

    Returns (screenshot_path, findings_json_path).
    """
    print(f"Capturing {url}...")
    screenshot_path = capture_url(url)
    print(f"  -> {screenshot_path}")

    print(f"Analyzing {screenshot_path}...")
    findings = analyze_screenshot(str(screenshot_path))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    findings_path = OUTPUT_DIR / f"{screenshot_path.stem}.json"
    findings_path.write_text(
        json.dumps(findings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  -> {findings_path}")

    return screenshot_path, findings_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Capture a URL and run a UX analysis on the screenshot."
    )
    parser.add_argument("url", help="URL to analyze")
    args = parser.parse_args()

    analyze_url(args.url)
