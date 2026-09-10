"""Headless UI + API smoke, with paid services replaced. Build frontend first.

Run: python -m tests.browser_smoke
"""
import socket
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright, expect
from tests.test_api import ApiTests, main


def run():
    fixture = ApiTests()
    fixture.setUp()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}")
            # Capture a real rendered page and use its exact bytes through the API.
            png = page.screenshot()
            fixture.capture.return_value = (png, "image/png")
            page.locator('input[type=url]').fill("https://example.com")
            page.get_by_role("button", name="Analyze URL", exact=True).click()
            preview = page.get_by_alt_text("Screenshot used for this UX review")
            expect(preview).to_be_visible()
            assert preview.evaluate("img => img.complete && img.naturalWidth > 0")
            page.get_by_role("button", name="Analyze URL", exact=True).click()
            expect(page.get_by_text("Served from cache", exact=True)).to_be_visible()
            calls = fixture.capture.call_count
            page.get_by_role("button", name="Analyze again", exact=True).click()
            expect(page.get_by_text("Served from cache", exact=True)).to_have_count(0)
            expect(preview).to_be_visible()
            assert fixture.capture.call_count == calls + 1
            expect(page.locator("time")).to_be_visible()
            page.locator('input[type=file]').set_input_files({"name": "screen.png", "mimeType": "image/png", "buffer": png})
            page.get_by_role("button", name="Analyze image", exact=True).click()
            expect(page.get_by_role("heading", name="Clarify checkout", exact=True)).to_be_visible()
            assert preview.evaluate("img => img.complete && img.naturalWidth > 0")
            Path("screenshots").mkdir(exist_ok=True)
            page.screenshot(path="screenshots/review-desktop.png", full_page=True)
            assert not errors, errors
            browser.close()
        print("PASS: headless URL, cached URL, upload and decoded screenshot preview")
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        fixture.doCleanups()


if __name__ == "__main__":
    run()
