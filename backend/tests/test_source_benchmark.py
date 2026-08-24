import json
import unittest
from datetime import timezone

from benchmarks.source_benchmark import ProbeResult, _parse_datetime, extract_candidates, summarize
from benchmarks.tikhub_account_status import _safe_error_payload


class SourceBenchmarkTests(unittest.TestCase):
    def test_parse_datetime_supports_seconds_milliseconds_and_iso(self):
        seconds = _parse_datetime(1787560000)
        millis = _parse_datetime(1787560000000)
        iso = _parse_datetime("2026-08-24T08:26:40Z")
        self.assertEqual(seconds, millis)
        self.assertEqual(seconds, iso)
        self.assertEqual(seconds.tzinfo, timezone.utc)

    def test_extract_candidates_keeps_notes_and_deduplicates(self):
        payload = {
            "code": 200,
            "data": {
                "items": [
                    {
                        "note_id": "abc123456789012345",
                        "display_title": "寻找 AI 智能体开发团队或个人",
                        "desc": "希望产品做成网页端登录使用",
                        "create_time": 1787560000,
                        "share_url": "https://www.xiaohongshu.com/explore/abc123456789012345",
                    },
                    {
                        "note_id": "abc123456789012345",
                        "display_title": "duplicate",
                        "desc": "duplicate",
                        "create_time": 1787560000,
                    },
                    {"id": "author-1", "name": "作者名字"},
                ]
            },
        }
        raw_count, candidates = extract_candidates(payload)
        self.assertEqual(raw_count, 2)
        self.assertEqual(len(candidates), 1)
        self.assertIn("AI 智能体", candidates[0].title)
        self.assertTrue(candidates[0].url.startswith("https://www.xiaohongshu.com/"))

    def test_extract_requires_publish_time_for_freshness_integrity(self):
        payload = {
            "items": [
                {
                    "note_id": "abc123456789012345",
                    "display_title": "找人做网站",
                    "desc": "有偿",
                }
            ]
        }
        raw_count, candidates = extract_candidates(payload)
        self.assertEqual(raw_count, 1)
        self.assertEqual(candidates, [])

    def test_summary_aggregates_provider_metrics(self):
        rows = [
            ProbeResult("demo", "a", True, 100, 2, 2, 2, 5.0, 1, 2, 2),
            ProbeResult("demo", "b", True, 300, 1, 1, 0, 15.0, 1, 1, 1),
            ProbeResult("demo", "c", False, 500, 0, 0, 0, None, 0, 0, 0, "timeout"),
        ]
        data = summarize(rows)[0]
        self.assertEqual(data["queries"], 3)
        self.assertEqual(data["success_rate"], 0.667)
        self.assertEqual(data["median_latency_ms"], 200)
        self.assertEqual(data["median_newest_age_minutes"], 10.0)
        self.assertEqual(data["url_coverage_rate"], 0.667)

    def test_tikhub_error_payload_keeps_nested_reason_and_redacts_sensitive_values(self):
        raw = json.dumps(
            {
                "detail": {
                    "message": "API token demo-secret has no route access",
                    "required_scope": "/api/v1/tikhub/user/",
                    "email": "person@example.com",
                }
            }
        ).encode("utf-8")
        payload = _safe_error_payload(raw, 403, "demo-secret")
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(payload["http_status"], 403)
        self.assertIn("required_scope", rendered)
        self.assertNotIn("demo-secret", rendered)
        self.assertNotIn("person@example.com", rendered)


if __name__ == "__main__":
    unittest.main()
