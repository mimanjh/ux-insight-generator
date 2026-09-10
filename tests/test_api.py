"""Offline API checks: python -m unittest discover -s tests."""
import base64
import copy
import asyncio
import threading
import unittest
from unittest.mock import MagicMock, patch
import os

from fastapi.testclient import TestClient
import httpx
import redis as redis_library

with patch("redis.from_url"):
    from backend import main

API_KEY = "sk-ant-test-key-not-real-1234567890"
SECOND_KEY = "sk-ant-second-test-key-9876543210"

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
REPORT = {"what_im_looking_at": "A sample checkout", "whats_working": ["Clear heading"], "findings": [{"title": "Clarify checkout", "theme": "content_clarity", "severity": "high", "observation_confidence": "high", "judgment_confidence": "medium", "what_i_see": "An ambiguous button", "why_it_matters": "The next step is unclear", "suggested_fix": "Describe the next step", "caveat": None, "citation": None, "citation_status": "no_match"}]}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.cache = {}
        redis = MagicMock()
        redis.get.side_effect = self.cache.get
        redis.setex.side_effect = lambda key, ttl, value: self.cache.__setitem__(key, value)
        locks = {}
        redis.lock.side_effect = lambda key, **kwargs: locks.setdefault(key, threading.Lock())
        self.patches = [patch.object(main, "r", redis), patch.object(main, "capture_url", return_value=(PNG, "image/png")), patch.object(main, "analyze_screenshot", side_effect=lambda *a, **kw: copy.deepcopy(REPORT)), patch.object(main, "ground_findings", side_effect=lambda report, **kw: report)]
        self.redis, self.capture, self.model, _ = [p.start() for p in self.patches]
        self.redis.eval.return_value = 1
        for p in self.patches:
            self.addCleanup(p.stop)
        self.client = TestClient(main.app, headers={"Authorization": f"Bearer {API_KEY}"})

    def test_preview_matches_input_and_every_request_is_fresh(self):
        for path, kwargs in [("/api/analyze", {"json": {"url": "https://example.com"}}), ("/api/analyze-image", {"files": {"file": ("screen.png", PNG, "image/png")}})]:
            first = self.client.post(path, **kwargs)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(base64.b64decode(first.json()["screenshot"].split(",")[1]), PNG)
            calls = self.model.call_count
            second = self.client.post(path, headers={"Authorization": f"Bearer {SECOND_KEY}"}, **kwargs)
            self.assertEqual(second.status_code, 200)
            self.assertEqual(self.model.call_count, calls + 1)
            self.assertEqual(self.model.call_args.kwargs["api_key"], SECOND_KEY)
            self.assertEqual(main.ground_findings.call_args.kwargs["api_key"], SECOND_KEY)
            self.assertNotIn(API_KEY, first.text)
            self.assertNotIn(SECOND_KEY, second.text)
            self.assertNotIn(SECOND_KEY, str(self.redis.mock_calls))
        self.redis.get.assert_not_called()
        self.redis.setex.assert_not_called()

    def test_capture_failure_does_not_call_model(self):
        self.capture.side_effect = main.CaptureFailed("Blocked")
        response = self.client.post("/api/analyze", json={"url": "https://example.com"})
        self.assertEqual(response.status_code, 422)
        self.model.assert_not_called()

    def test_context_is_forwarded_for_both_inputs(self):
        for path, kwargs in [("/api/analyze", {"json": {"url": "https://example.com"}}), ("/api/analyze-image", {"files": {"file": ("screen.png", PNG, "image/png")}})]:
            for context in ["", "Shoppers checking out"]:
                args = copy.deepcopy(kwargs)
                if "json" in args:
                    args["json"]["context"] = context
                else:
                    args["data"] = {"context": context}
                response = self.client.post(path, **args)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.model.call_args.kwargs["context"], context)
                self.assertEqual(response.json()["context"], context)
        self.assertEqual(self.client.post("/api/analyze", json={"url": "https://example.com", "context": "x" * 1001}).status_code, 422)
        self.assertEqual(self.client.post("/api/analyze-image", files={"file": ("screen.png", PNG, "image/png")}, data={"context": "x" * 1001}).status_code, 422)

    def test_mobile_capture_options(self):
        for device in ["desktop", "mobile"]:
            response = self.client.post("/api/analyze", json={"url": "https://example.com", "device": device})
            self.assertEqual(response.json()["device"], device)
            self.assertEqual(self.capture.call_args.kwargs["mobile"], device == "mobile")
            self.assertEqual(self.capture.call_args.kwargs["viewport"], (390, 844) if device == "mobile" else (1440, 900))
        self.assertEqual(self.client.post("/api/analyze", json={"url": "https://example.com", "device": "invalid"}).status_code, 422)

    def test_invalid_model_response_is_sanitized(self):
        self.model.side_effect = lambda *a, **kw: {"findings": "invalid"}
        response = self.client.post("/api/analyze", json={"url": "https://example.com"})
        self.assertEqual(response.status_code, 502)
        self.assertFalse(self.cache)
        self.model.side_effect = RuntimeError(API_KEY)
        with self.assertLogs("uvicorn.error", level="WARNING") as logs:
            response = self.client.post("/api/analyze", json={"url": "https://example.com"})
        self.assertNotIn(API_KEY, response.text + str(logs.output))

    def test_redis_timeout_fails_before_paid_work(self):
        self.redis.eval.side_effect = redis_library.TimeoutError("timeout")
        self.assertEqual(self.client.post("/api/analyze", json={"url": "https://example.com"}).status_code, 503)
        self.model.assert_not_called()

    def test_slow_upload_keeps_health_responsive_and_blocks_duplicates(self):
        entered, release = threading.Event(), threading.Event()
        def slow_model(*args, **kwargs):
            entered.set()
            release.wait(5)
            return copy.deepcopy(REPORT)
        self.model.side_effect = slow_model
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test", headers={"Authorization": f"Bearer {API_KEY}"}) as client:
                upload = {"files": {"file": ("screen.png", PNG, "image/png")}}
                task = asyncio.create_task(client.post("/api/analyze-image", **upload))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    self.assertEqual((await asyncio.wait_for(client.get("/api/health"), 1)).status_code, 200)
                    self.assertEqual((await asyncio.wait_for(client.post("/api/analyze-image", **upload), 1)).status_code, 409)
                    self.assertEqual(self.model.call_count, 1)
                finally:
                    release.set()
                    self.assertEqual((await task).status_code, 200)
                self.assertEqual((await client.post("/api/analyze-image", **upload)).status_code, 200)
                self.assertEqual(self.model.call_count, 2)
        asyncio.run(scenario())

    def test_access_and_rate_limits_fail_before_model_calls(self):
        for path, kwargs in [("/api/analyze", {"json": {"url": "https://example.com"}}), ("/api/analyze-image", {"files": {"file": ("screen.png", PNG, "image/png")}})]:
            self.assertEqual(self.client.post(path, headers={"Authorization": "Bearer wrong"}, **kwargs).status_code, 401)
            self.assertEqual(self.client.post(path, headers={"Authorization": ""}, **kwargs).status_code, 401)
            self.redis.eval.return_value = main.HOURLY_LIMIT + 1
            limited = self.client.post(path, **kwargs)
            self.assertEqual(limited.status_code, 429)
            self.assertIn("Retry-After", limited.headers)
            self.redis.eval.return_value = 1
        self.model.assert_not_called()
        self.assertEqual(self.client.post("/api/analyze-image", content=b"x" * (main.MAX_UPLOAD_BYTES + 65537)).status_code, 413)
        self.assertEqual(self.client.get("/api/health", headers={"Authorization": ""}).status_code, 200)

    def test_full_capacity_rejects_new_work_and_recovers(self):
        slots = [self.redis.lock(f"{main.REDIS_KEY_PREFIX}analysis-slot:{index}") for index in range(2)]
        for slot in slots:
            slot.acquire()
        try:
            self.assertEqual(self.client.post("/api/analyze", json={"url": "https://example.com"}).status_code, 429)
            self.model.assert_not_called()
        finally:
            for slot in slots:
                slot.release()
        self.assertEqual(self.client.post("/api/analyze", json={"url": "https://example.com"}).status_code, 200)

    def test_no_owner_key_fallback_and_no_secret_in_errors(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "owner-key", "ANALYSIS_ACCESS_KEY": "legacy-code"}):
            missing = self.client.post("/api/analyze", json={"url": "https://example.com"}, headers={"Authorization": ""})
            self.assertEqual(missing.status_code, 401)
            self.capture.assert_not_called()
        self.model.side_effect = main.AuthenticationError(API_KEY, response=httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")), body={"key": API_KEY})
        response = self.client.post("/api/analyze", json={"url": "https://example.com"})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(API_KEY, response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_custom_headers_fail_closed_before_capture(self):
        with patch.dict(os.environ, {"ANTHROPIC_CUSTOM_HEADERS": "x-api-key: owner-key"}):
            response = self.client.post("/api/analyze", json={"url": "https://example.com"})
            self.assertEqual(response.status_code, 503)
            self.capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
