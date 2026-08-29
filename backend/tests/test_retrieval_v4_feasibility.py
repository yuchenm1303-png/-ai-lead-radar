from __future__ import annotations

import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from benchmarks.retrieval_v4_feasibility import (
    Probe,
    ProbeExecution,
    ProbeResult,
    apply_overlap_metrics,
    build_default_plan,
    build_request_url,
    report,
)
from benchmarks.source_benchmark import Candidate


NOW = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)


def candidate(external_id: str, title: str) -> Candidate:
    return Candidate(
        external_id=external_id,
        title=title,
        excerpt=title,
        published_at=NOW,
        url=f"https://www.xiaohongshu.com/explore/{external_id}",
    )


def result_for(probe: Probe, count: int) -> ProbeResult:
    return ProbeResult(
        key=probe.key,
        generator=probe.generator,
        endpoint=probe.endpoint,
        keyword=probe.keyword,
        ok=True,
        latency_ms=10,
        raw_candidates=count,
        normalized_candidates=count,
        within_30m=count,
        within_2h=count,
        within_24h=count,
        newest_age_minutes=0.0,
        url_coverage=count,
    )


class RetrievalV4FeasibilityTests(unittest.TestCase):
    def test_default_plan_compares_three_distinct_acquisition_modes(self):
        plan = build_default_plan(hours=2, known_keyword="公司需要做网站")

        self.assertEqual(len(plan), 3)
        self.assertEqual([probe.key for probe in plan], ["known_intent", "intent_discovery", "open_recent"])
        self.assertEqual(
            [probe.generator for probe in plan],
            ["known_intent_search", "topic_free_intent_discovery", "open_recent_discovery"],
        )
        self.assertEqual(plan[0].keyword, "公司需要做网站")
        self.assertIsNotNone(plan[1].keyword)
        self.assertIsNone(plan[2].keyword)
        self.assertEqual(plan[1].window_hours, 2)
        self.assertEqual(plan[2].window_hours, 2)

    def test_open_recent_cross_search_omits_keyword_parameter(self):
        probe = Probe(
            key="open_recent",
            generator="open_recent_discovery",
            endpoint="/api/search/v1",
            keyword=None,
            window_hours=1,
        )
        url = build_request_url(probe, "secret", now=NOW)
        query = parse_qs(urlparse(url).query)

        self.assertNotIn("keyword", query)
        self.assertEqual(query["source"], ["XIAOHONGSHU"])
        self.assertIn("start", query)
        self.assertIn("end", query)

    def test_topic_free_intent_cross_search_keeps_boolean_expression(self):
        probe = Probe(
            key="intent_discovery",
            generator="topic_free_intent_discovery",
            endpoint="/api/search/v1",
            keyword='"有报酬" || "项目急需"',
            window_hours=24,
        )
        url = build_request_url(probe, "secret", now=NOW)
        query = parse_qs(urlparse(url).query)

        self.assertEqual(query["keyword"], ['"有报酬" || "项目急需"'])
        self.assertNotIn("网站", query["keyword"][0])
        self.assertNotIn("小程序", query["keyword"][0])

    def test_overlap_metrics_measure_novel_discovery(self):
        known_probe, intent_probe, open_probe = build_default_plan(known_keyword="找人做网站")
        executions = [
            ProbeExecution(known_probe, result_for(known_probe, 2), (candidate("a", "A"), candidate("b", "B"))),
            ProbeExecution(intent_probe, result_for(intent_probe, 2), (candidate("b", "B"), candidate("c", "C"))),
            ProbeExecution(open_probe, result_for(open_probe, 1), (candidate("d", "D"),)),
        ]

        apply_overlap_metrics(executions)

        self.assertEqual(executions[0].result.unique_contribution, 1)
        self.assertEqual(executions[1].result.unique_contribution, 1)
        self.assertEqual(executions[1].result.overlap_with_known, 1)
        self.assertEqual(executions[2].result.unique_contribution, 1)
        self.assertEqual(executions[2].result.overlap_with_known, 0)

        payload = report(executions, plan=[known_probe, intent_probe, open_probe], executed=True)
        self.assertEqual(payload["summary"]["total_unique_candidates"], 4)
        self.assertEqual(payload["summary"]["discovery_unique_candidates"], 3)
        self.assertEqual(payload["summary"]["discovery_novel_vs_known"], 2)
        self.assertEqual(payload["summary"]["discovery_overlap_with_known"], 1)

    def test_dry_report_has_no_results_and_declares_call_ceiling(self):
        plan = build_default_plan(known_keyword="找人做网站")
        payload = report([], plan=plan, executed=False)

        self.assertFalse(payload["executed"])
        self.assertEqual(payload["provider_call_ceiling"], 3)
        self.assertEqual(payload["results"], [])


if __name__ == "__main__":
    unittest.main()
