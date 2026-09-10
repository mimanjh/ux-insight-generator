import copy
import unittest
import os
import httpx
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError
from backend.analyze_screenshot import analyze_screenshot
from backend.ground_findings import ground_findings
from tests.test_api import API_KEY, PNG, REPORT


class ModelTests(unittest.TestCase):
    def test_real_sdk_uses_only_supplied_key_and_fixed_destination(self):
        requests = []
        original_client = httpx.Client
        def handler(request):
            requests.append(request)
            is_grounding = b"attach_citations" in request.content
            value = {"citations": [{"finding_index": 0, "article_id": "a", "relevance_note": None}]} if is_grounding else copy.deepcopy(REPORT)
            return httpx.Response(200, json={"id": "msg_test", "type": "message", "role": "assistant", "model": "claude-sonnet-4-5", "content": [{"type": "tool_use", "id": "tool_test", "name": "attach_citations" if is_grounding else "report_ux_findings", "input": value}], "stop_reason": "tool_use", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})
        class TransportClient(original_client):
            def __init__(client_self, *args, **kwargs):
                self.assertFalse(kwargs["trust_env"])
                self.assertFalse(kwargs["follow_redirects"])
                super().__init__(*args, **kwargs, transport=httpx.MockTransport(handler))
        article = {"id": "a", "title": "Clear labels", "url": "https://www.nngroup.com/articles/", "summary": "Use clear labels"}
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "owner-key", "ANTHROPIC_BASE_URL": "https://untrusted.invalid", "HTTPS_PROXY": "http://untrusted.invalid"}), patch("httpx.Client", TransportClient), patch("backend.ground_findings.retrieve_batch", return_value=[[article]]):
            report = analyze_screenshot(PNG, "image/png", api_key=API_KEY)
            report = ground_findings(report, api_key=API_KEY)
            self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "owner-key")
        self.assertEqual(len(requests), 2)
        for request in requests:
            self.assertEqual(str(request.url), "https://api.anthropic.com/v1/messages")
            self.assertEqual(request.headers["x-api-key"], API_KEY)
            self.assertNotIn(API_KEY.encode(), request.content)
        self.assertNotIn(API_KEY, str(report))

    def test_context_reaches_prompt_and_output_is_validated(self):
        reply = SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(type="tool_use", name="report_ux_findings", input=copy.deepcopy(REPORT))])
        with patch("backend.analyze_screenshot.Anthropic") as client:
            client.return_value.__enter__.return_value = client.return_value
            client.return_value.messages.create.return_value = reply
            result = analyze_screenshot(PNG, "image/png", context="New shoppers", api_key=API_KEY)
            self.assertEqual(client.call_args.kwargs["api_key"], API_KEY)
            self.assertEqual(client.call_args.kwargs["base_url"], "https://api.anthropic.com")
            client.return_value.__exit__.assert_called_once()
            self.assertEqual(result["findings"][0]["severity"], "high")
            prompt = client.return_value.messages.create.call_args.kwargs["messages"][0]["content"][1]["text"]
            self.assertIn("New shoppers", prompt)
            reply.content[0].input["findings"][0]["severity"] = "invented"
            with self.assertRaises(ValidationError):
                analyze_screenshot(PNG, "image/png", api_key=API_KEY)
            reply.stop_reason = "max_tokens"
            with self.assertRaises(RuntimeError):
                analyze_screenshot(PNG, "image/png", api_key=API_KEY)
