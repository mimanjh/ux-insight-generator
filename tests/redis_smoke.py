"""Real Redis check, explicitly restricted to loopback: python -m tests.redis_smoke redis://127.0.0.1:6379/15."""
import ipaddress
import sys
import uuid
from urllib.parse import urlsplit
from unittest.mock import patch

import redis
from tests.test_api import ApiTests, main


def run(url):
    target = urlsplit(url)
    if target.scheme != "redis" or not ipaddress.ip_address(target.hostname).is_loopback:
        raise ValueError("Use an explicit loopback Redis test instance")
    client = redis.from_url(url, decode_responses=True, socket_timeout=3)
    client.ping()
    prefix = f"ux-test:{uuid.uuid4().hex}:"
    try:
        for method in ["test_preview_matches_model_input_and_survives_cache", "test_refresh_replaces_cache_only_after_success", "test_slow_upload_keeps_health_responsive_and_blocks_duplicates"]:
            fixture = ApiTests()
            fixture.setUp()
            try:
                with patch.object(main, "r", client), patch.object(main, "REDIS_KEY_PREFIX", prefix + method + ":"):
                    getattr(fixture, method)()
            finally:
                fixture.doCleanups()
        fixture = ApiTests()
        fixture.setUp()
        try:
            with patch.object(main, "r", client), patch.object(main, "REDIS_KEY_PREFIX", prefix + "rate:"), patch.object(main, "HOURLY_LIMIT", 1):
                assert fixture.client.post("/api/analyze", json={"url": "https://example.com"}).status_code == 200
                assert fixture.client.post("/api/analyze", json={"url": "https://example.com"}).status_code == 429
                assert fixture.model.call_count == 1
        finally:
            fixture.doCleanups()
        keys = list(client.scan_iter(prefix + "*"))
        assert keys and all(client.ttl(key) > 0 for key in keys)
        assert not any(key.endswith(":lock") or "analysis-slot:" in key for key in keys)
        print("PASS: real Redis cache, TTLs, lock contention/release and rate limit")
    finally:
        for key in client.scan_iter(prefix + "*"):
            client.delete(key)
        client.close()


if __name__ == "__main__":
    run(sys.argv[1])
