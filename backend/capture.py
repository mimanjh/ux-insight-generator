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
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

# Default viewport — desktop above-the-fold.
DEFAULT_VIEWPORT = (1440, 900)

# How long to wait for the initial load event before giving up entirely.
LOAD_TIMEOUT_MS = 20_000

# How long to wait for networkidle after load. Many sites never reach
# networkidle (websockets, analytics) — we accept that and screenshot
# anyway when this times out.
NETWORK_IDLE_TIMEOUT_MS = 8_000

# Page-title fragments that strongly suggest we landed on a bot-challenge
# or access-denied page rather than the content the user wanted. Substring
# match, case-insensitive. False positives are possible but rare — these
# phrases almost never appear in real page titles. Add patterns here as
# we encounter new bot-block variants.
CHALLENGE_TITLE_PATTERNS = (
    "just a moment",                  # Cloudflare interstitial
    "attention required",             # Cloudflare block
    "verify you are human",
    "verifying you are human",
    "captcha",
    "please complete the security",
    "access denied",
    "are you a robot",
)

# Stealth knobs. Playwright's defaults are unusually detectable — sites
# can sniff the TLS fingerprint, the navigator.webdriver flag, and the
# user-agent string ("HeadlessChrome") and refuse to serve. None of this
# beats sophisticated bot detection (LinkedIn, banks), but it dramatically
# improves the pass rate on mid-tier protection.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",  # hides navigator.webdriver
]
# Removes the navigator.webdriver=true that the disable-blink flag misses
# on some Playwright builds. Belt-and-suspenders.
WEBDRIVER_HIDE_SCRIPT = (
    "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
)


class CaptureFailed(Exception):
    """Capture finished mechanically but the result is unlikely to be a
    useful screenshot of what the user intended.

    Examples: HTTP 4xx/5xx, bot-challenge / captcha pages, access-denied
    walls. The caller should surface this to the user as "try uploading
    instead" rather than spending an Anthropic call on a useless screenshot.

    Transient errors (DNS, connection refused, timeout) are also wrapped
    so the backend has a single exception type to handle for "this URL
    won't work."
    """

    def __init__(self, reason: str, http_status: int | None = None) -> None:
        self.reason = reason
        self.http_status = http_status
        super().__init__(reason)


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
    viewport: tuple[int, int] = DEFAULT_VIEWPORT,
) -> tuple[bytes, str]:
    """Render `url` in headless Chromium and return (png_bytes, media_type).

    Pure function: no disk I/O. Callers that want the bytes on disk (the
    CLI, tests) write them themselves. Always returns PNG bytes.
    """
    width, height = viewport

    with sync_playwright() as p:
        # channel="chromium" forces the full Chrome-for-Testing build
        # rather than the headless-shell, which has a more recognizable
        # bot fingerprint.
        browser = p.chromium.launch(channel="chromium", args=LAUNCH_ARGS)
        try:
            context = browser.new_context(
                viewport={"width": width, "height": height},
                user_agent=USER_AGENT,
                locale="en-US",
                timezone_id="America/New_York",
            )
            context.add_init_script(WEBDRIVER_HIDE_SCRIPT)
            page = context.new_page()

            # Navigation — wrapped to convert transient errors into a
            # CaptureFailed so the backend has a single exception to handle.
            try:
                response = page.goto(
                    url, wait_until="load", timeout=LOAD_TIMEOUT_MS
                )
            except PlaywrightTimeout:
                raise CaptureFailed("Page load timed out.")
            except Exception as e:
                # DNS errors, connection refused, etc.
                raise CaptureFailed(f"Could not reach the URL: {e}")

            # Fail-fast on HTTP error status. The cheapest signal we have
            # that the capture won't be useful. Some sites return error
            # pages with HTTP 200 — we can't catch those here, only the
            # honest ones.
            if response is not None and response.status >= 400:
                raise CaptureFailed(
                    f"Server returned HTTP {response.status}.",
                    http_status=response.status,
                )

            # Best-effort wait for the page to actually settle. Many sites
            # never reach networkidle; that's fine, we screenshot anyway.
            try:
                page.wait_for_load_state(
                    "networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS
                )
            except PlaywrightTimeout:
                logger.info(
                    "networkidle did not fire within %dms for %s; "
                    "screenshotting anyway",
                    NETWORK_IDLE_TIMEOUT_MS,
                    url,
                )

            # Heuristic: page-title check for known bot-challenge / access
            # pages. Cheap, catches the most common silent-success cases
            # where Playwright thinks it succeeded but the screenshot is
            # just a "verify you're human" prompt.
            title_lower = (page.title() or "").lower()
            for pattern in CHALLENGE_TITLE_PATTERNS:
                if pattern in title_lower:
                    raise CaptureFailed(
                        f"Site appears to be showing a bot-challenge or "
                        f"access page (page title contains '{pattern}')."
                    )

            # No path argument -> Playwright returns bytes instead of writing.
            image_bytes = page.screenshot(full_page=False)
        finally:
            browser.close()

    return image_bytes, "image/png"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Capture a screenshot of a URL using headless Chromium."
    )
    parser.add_argument("url", help="URL to capture")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Where to write the PNG (path is created if it doesn't exist).",
    )
    args = parser.parse_args()

    print(f"Capturing {args.url}...")
    image_bytes, _media_type = capture_url(args.url)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(image_bytes)
    print(f"Wrote {args.output}")
