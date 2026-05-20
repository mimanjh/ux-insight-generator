"""
Capture a screenshot of a URL using Playwright.

This is the v2 input pipeline: instead of feeding hand-curated screenshots
to the analyzer, we render the page in a headless browser and screenshot
it ourselves.

Design notes:
- We use page.goto(wait_until="load") to wait for the initial load event,
  then separately wait for networkidle with a forgiving timeout. If
  networkidle times out (some sites have endless analytics beacons), we
  screenshot anyway — the page is usually visually complete by then.
- Default viewport is 1440x900 (desktop above-the-fold). Full-page
  screenshots are not implemented — they produce very tall images that
  compress vision detail and may exceed model input limits.
- Filenames are derived from a sanitized URL. Re-running the same URL
  overwrites the previous capture.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

SCREENSHOT_DIR = Path("screenshots")

# Default viewport — desktop above-the-fold.
DEFAULT_VIEWPORT = (1440, 900)

# How long to wait for the initial load event before giving up entirely.
LOAD_TIMEOUT_MS = 20_000

# How long to wait for networkidle after load. Many sites never reach
# networkidle (websockets, analytics) — we accept that and screenshot
# anyway when this times out.
NETWORK_IDLE_TIMEOUT_MS = 8_000


def url_to_filename(url: str) -> str:
    """Turn a URL into a safe filename stem.

    Example: https://www.amazon.com/dp/B07XYZ?ref=foo
          -> www_amazon_com_dp_B07XYZ
    """
    parsed = urlparse(url)
    raw = f"{parsed.netloc}{parsed.path}"
    # Replace anything that isn't a safe filename character with underscore.
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("_")
    # Truncate to keep filesystem happy.
    return sanitized[:120] or "capture"


def capture_url(
    url: str,
    output_path: Path | None = None,
    viewport: tuple[int, int] = DEFAULT_VIEWPORT,
) -> Path:
    """Render `url` in headless Chromium and save a viewport screenshot.

    Returns the path the screenshot was written to.
    """
    if output_path is None:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = SCREENSHOT_DIR / f"{url_to_filename(url)}.png"

    width, height = viewport

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": width, "height": height},
        )
        page = context.new_page()

        page.goto(url, wait_until="load", timeout=LOAD_TIMEOUT_MS)

        # Best-effort wait for the page to actually settle. Many sites
        # never reach networkidle; that's fine, we screenshot anyway.
        try:
            page.wait_for_load_state(
                "networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS
            )
        except PlaywrightTimeout:
            print(
                f"  (networkidle didn't fire within "
                f"{NETWORK_IDLE_TIMEOUT_MS}ms — screenshotting anyway)"
            )

        page.screenshot(path=str(output_path), full_page=False)
        browser.close()

    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Capture a screenshot of a URL using headless Chromium."
    )
    parser.add_argument("url", help="URL to capture")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path (default: screenshots/<sanitized-url>.png)",
    )
    args = parser.parse_args()

    print(f"Capturing {args.url}...")
    path = capture_url(args.url, output_path=args.output)
    print(f"Wrote {path}")
