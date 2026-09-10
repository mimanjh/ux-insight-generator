import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError
from backend.analyze_screenshot import analyze_screenshot
from tests.test_api import PNG, REPORT


class ModelTests(unittest.TestCase):
    def test_context_reaches_prompt_and_output_is_validated(self):
        reply = SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(type="tool_use", name="report_ux_findings", input=copy.deepcopy(REPORT))])
        with patch("backend.analyze_screenshot.Anthropic") as client:
            client.return_value.messages.create.return_value = reply
            result = analyze_screenshot(PNG, "image/png", context="New shoppers")
            self.assertEqual(result["findings"][0]["severity"], "high")
            prompt = client.return_value.messages.create.call_args.kwargs["messages"][0]["content"][1]["text"]
            self.assertIn("New shoppers", prompt)
            reply.content[0].input["findings"][0]["severity"] = "invented"
            with self.assertRaises(ValidationError):
                analyze_screenshot(PNG, "image/png")
            reply.stop_reason = "max_tokens"
            with self.assertRaises(RuntimeError):
                analyze_screenshot(PNG, "image/png")
