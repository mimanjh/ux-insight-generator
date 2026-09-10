"""Offline API checks: python -m unittest discover -s tests."""
import base64
import copy
import asyncio
import threading
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import httpx
import redis as redis_library

with patch("redis.from_url"):
    from backend import main

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
        self.patches = [patch.object(main, "r", redis), patch.object(main, "capture_url", return_value=(PNG, "image/png")), patch.object(main, "analyze_screenshot", side_effect=lambda *a, **kw: copy.deepcopy(REPORT)), patch.object(main, "ground_findings", side_effect=lambda report: report)]
        self.redis, self.capture, self.model, _ = [p.start() for p in self.patches]
        self.redis.eval.return_value = 1
        access = patch.object(main, "ACCESS_KEY", "test-access")
        access.start()
        self.addCleanup(access.stop)
        for p in self.patches:
            self.addCleanup(p.stop)
        self.client = TestClient(main.app, headers={"Authorization": "Bearer test-access"})

    def test_preview_matches_model_input_and_survives_cache(self):
        for path, kwargs in [("/api/analyze", {"json": {"url": "https://example.com"}}), ("/api/analyze-image", {"files": {"file": ("screen.png", PNG, "image/png")}})]:
            first = self.client.post(path, **kwargs)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(base64.b64decode(first.json()["screenshot"].split(",")[1]), PNG)
            calls = self.model.call_count
            second = self.client.post(path, **kwargs)
            self.assertTrue(second.json()["cached"])
            self.assertEqual(second.json()["screenshot"], first.json()["screenshot"])
            self.assertEqual(self.model.call_count, calls)

    def test_capture_failure_does_not_call_model(self):
        self.capture.side_effect = main.CaptureFailed("Blocked")
        response = self.client.post("/api/analyze", json={"url": "https://example.com"})
        self.assertEqual(response.status_code, 422)
        self.model.assert_not_called()

    def test_refresh_replaces_cache_only_after_success(self):
        payload = {"url": "https://example.com"}
        first = self.client.post("/api/analyze", json=payload).json()
        self.capture.return_value = (b"new screenshot", "image/png")
        fresh = self.client.post("/api/analyze", json={**payload, "refresh": True}).json()
        self.assertFalse(fresh["cached"])
        self.assertNotEqual(fresh["screenshot"], first["screenshot"])
        self.assertNotEqual(fresh["analyzed_at"], first["analyzed_at"])
        self.capture.side_effect = main.CaptureFailed("Blocked")
        self.assertEqual(self.client.post("/api/analyze", json={**payload, "refresh": True}).status_code, 422)
        cached = self.client.post("/api/analyze", json=payload).json()
        self.assertEqual(cached["screenshot"], fresh["screenshot"])
        self.assertEqual(cached["analyzed_at"], fresh["analyzed_at"])

    def test_context_is_forwarded_and_separates_cache(self):
        for path, kwargs in [("/api/analyze", {"json": {"url": "https://example.com"}}), ("/api/analyze-image", {"files": {"file": ("screen.png", PNG, "image/png")}})]:
            for context in ["", "Shoppers checking out"]:
                args = copy.deepcopy(kwargs)
                if "json" in args:
                    args["json"]["context"] = context
                else:
                    args["data"] = {"context": context}
                response = self.client.post(path, **args)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertFalse(response.json()["cached"])
                self.assertEqual(self.model.call_args.kwargs["context"], context)
                self.assertEqual(response.json()["context"], context)
                self.assertTrue(self.client.post(path, **args).json()["cached"])
        self.assertEqual(self.client.post("/api/analyze", json={"url": "https://example.com", "context": "x" * 1001}).status_code, 422)
        self.assertEqual(self.client.post("/api/analyze-image", files={"file": ("screen.png", PNG, "image/png")}, data={"context": "x" * 1001}).status_code, 422)

    def test_mobile_capture_has_separate_cache(self):
        for device in ["desktop", "mobile"]:
            response = self.client.post("/api/analyze", json={"url": "https://example.com", "device": device})
            self.assertFalse(response.json()["cached"])
            self.assertEqual(response.json()["device"], device)
            self.assertEqual(self.capture.call_args.kwargs["mobile"], device == "mobile")
            self.assertEqual(self.capture.call_args.kwargs["viewport"], (390, 844) if device == "mobile" else (1440, 900))
        self.assertEqual(self.client.post("/api/analyze", json={"url": "https://example.com", "device": "invalid"}).status_code, 422)

    def test_cache_write_failure_preserves_completed_review(self):
        self.redis.setex.side_effect = redis_library.ConnectionError("offline")
        response = self.client.post("/api/analyze", json={"url": "https://example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["cache_saved"])
        self.assertEqual(response.json()["findings"]["what_im_looking_at"], REPORT["what_im_looking_at"])

    def test_invalid_model_response_never_enters_cache(self):
        self.model.side_effect = lambda *a, **kw: {"findings": "invalid"}
        response = self.client.post("/api/analyze", json={"url": "https://example.com"})
        self.assertEqual(response.status_code, 502)
        self.assertFalse(self.cache)

    def test_cache_timeout_fails_before_paid_work(self):
        self.redis.get.side_effect = redis_library.TimeoutError("timeout")
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
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test", headers={"Authorization": "Bearer test-access"}) as client:
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
                self.assertTrue((await client.post("/api/analyze-image", **upload)).json()["cached"])
        asyncio.run(scenario())

    def test_access_and_rate_limits_fail_before_model_calls(self):
        for path, kwargs in [("/api/analyze", {"json": {"url": "https://example.com"}}), ("/api/analyze-image", {"files": {"file": ("screen.png", PNG, "image/png")}})]:
            self.assertEqual(self.client.post(path, headers={"Authorization": "Bearer wrong"}, **kwargs).status_code, 401)
            with patch.object(main, "ACCESS_KEY", ""):
                self.assertEqual(self.client.post(path, **kwargs).status_code, 503)
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


if __name__ == "__main__":
    unittest.main()
