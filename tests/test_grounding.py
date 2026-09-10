import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.ground_findings import ground_findings
from tests.test_api import API_KEY, REPORT


class GroundingTests(unittest.TestCase):
    def test_source_status_distinguishes_decline_failure_and_invalid_output(self):
        article = {"id": "a", "title": "Clear labels", "url": "https://www.nngroup.com/articles/", "summary": "Use clear labels"}
        with patch("backend.ground_findings.retrieve_batch", return_value=[[article]]) as retrieve, patch("backend.ground_findings.Anthropic") as client:
            client.return_value.__enter__.return_value = client.return_value
            for article_id, expected in [("a", "matched"), (None, "no_match"), ("invented", "unavailable")]:
                client.return_value.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="attach_citations", input={"citations": [{"finding_index": 0, "article_id": article_id, "relevance_note": None}]})])
                finding = ground_findings(copy.deepcopy(REPORT), api_key=API_KEY)["findings"][0]
                self.assertEqual(client.call_args.kwargs["api_key"], API_KEY)
                self.assertEqual(client.call_args.kwargs["base_url"], "https://api.anthropic.com")
                self.assertEqual(finding["citation_status"], expected)
                self.assertEqual(finding["citation"] is not None, expected == "matched")
            for invalid in [{"citations": "bad"}, {"citations": []}, {"citations": [{"finding_index": True, "article_id": None, "relevance_note": None}]}]:
                client.return_value.messages.create.return_value.content[0].input = invalid
                self.assertEqual(ground_findings(copy.deepcopy(REPORT), api_key=API_KEY)["findings"][0]["citation_status"], "unavailable")
            retrieve.side_effect = RuntimeError("Provider unavailable")
            self.assertEqual(ground_findings(copy.deepcopy(REPORT), api_key=API_KEY)["findings"][0]["citation_status"], "unavailable")
