"""Offline API checks: python -m unittest discover -s tests."""
import base64
import copy
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("redis.from_url"):
    from backend import main

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
REPORT = {"what_im_looking_at": "A sample checkout", "whats_working": ["Clear heading"], "findings": [{"title": "Clarify checkout", "theme": "content_clarity", "severity": "high", "observation_confidence": "high", "judgment_confidence": "medium", "what_i_see": "An ambiguous button", "why_it_matters": "The next step is unclear", "suggested_fix": "Describe the next step", "caveat": None, "citation": None}]}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.cache = {}
        redis = MagicMock()
        redis.get.side_effect = self.cache.get
        redis.setex.side_effect = lambda key, ttl, value: self.cache.__setitem__(key, value)
        self.patches = [patch.object(main, "r", redis), patch.object(main, "capture_url", return_value=(PNG, "image/png")), patch.object(main, "analyze_screenshot", side_effect=lambda *a, **kw: copy.deepcopy(REPORT)), patch.object(main, "ground_findings", side_effect=lambda report: report)]
        self.redis, self.capture, self.model, _ = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.client = TestClient(main.app)

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


if __name__ == "__main__":
    unittest.main()
