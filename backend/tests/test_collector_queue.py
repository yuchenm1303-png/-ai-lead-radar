import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors import justone_to_edge as worker


class CollectorQueueTests(unittest.TestCase):
    @patch.object(worker, "get_github_oidc_token", return_value="oidc-token")
    @patch.object(worker, "post_oidc_json")
    @patch.object(worker, "run")
    def test_empty_queue_never_calls_provider_run(self, run_mock, post_mock, _oidc):
        post_mock.return_value = {"ok": True, "claimed": False}
        result = worker.run_from_queue(timeout=15)
        self.assertTrue(result["queue_empty"])
        self.assertFalse(result["provider_called"])
        run_mock.assert_not_called()
        self.assertEqual(post_mock.call_args.args[2], "/api/v1/scan/claim")

    @patch.object(worker, "get_github_oidc_token", return_value="oidc-token")
    @patch.object(worker, "post_oidc_json")
    @patch.object(worker, "run")
    def test_claimed_queue_forwards_request_to_one_controlled_run(self, run_mock, post_mock, _oidc):
        post_mock.return_value = {
            "ok": True,
            "claimed": True,
            "request": {"id": 42, "query_override": "网站", "max_queries": 1},
        }
        run_mock.return_value = {"ok": True, "candidate_count": 8}
        result = worker.run_from_queue(timeout=15)
        self.assertFalse(result["queue_empty"])
        self.assertTrue(result["provider_called"])
        run_mock.assert_called_once()
        kwargs = run_mock.call_args.kwargs
        self.assertEqual(kwargs["query_override"], "网站")
        self.assertEqual(kwargs["max_queries"], 1)
        self.assertEqual(kwargs["scan_request_id"], 42)
        self.assertEqual(kwargs["oidc_token"], "oidc-token")

    @patch.object(worker, "get_github_oidc_token", return_value="oidc-token")
    @patch.object(worker, "post_oidc_json")
    @patch.object(worker, "run", side_effect=RuntimeError("provider failed"))
    def test_claimed_failure_is_reported_back_to_queue(self, _run_mock, post_mock, _oidc):
        post_mock.side_effect = [
            {"ok": True, "claimed": True, "request": {"id": 7, "query_override": None, "max_queries": 1}},
            {"ok": True},
        ]
        with self.assertRaises(RuntimeError):
            worker.run_from_queue(timeout=15)
        self.assertEqual(post_mock.call_count, 2)
        self.assertEqual(post_mock.call_args_list[1].args[2], "/api/v1/scan/fail")
        self.assertEqual(post_mock.call_args_list[1].args[3]["scan_request_id"], 7)


if __name__ == "__main__":
    unittest.main()
