import unittest
from datetime import datetime, timedelta, timezone

from app.connectors.base import RawLead
from app.query_engine import QUERY_SPECS, QueryPerformance, choose_queries, retrieval_version
from collectors import justone_to_edge as worker


class FakeFetchResult:
    def __init__(self, keyword, page, now):
        self.keyword = keyword
        self.raw_count = 20
        self.has_more = True
        self.request_id = f"req-{keyword}-{page}"
        self.leads = [
            RawLead(
                source="小红书",
                external_id=f"{keyword}-{page}-{index}",
                title=f"{keyword} candidate {index}",
                excerpt="测试",
                published_at=now - timedelta(minutes=page * 20 + index),
                url=f"https://example.com/{keyword}/{page}/{index}",
                budget=None,
            )
            for index in range(20)
        ]


class FakeConnector:
    name = "fake"

    def __init__(self, now):
        self.now = now
        self.calls = []

    def fetch_query(self, keyword, *, timeout=60, page=1):
        self.calls.append((keyword, page))
        return FakeFetchResult(keyword, page, self.now)


class RetrievalV3Tests(unittest.TestCase):
    def test_portfolio_is_intent_driven_and_has_no_bare_topic_probes(self):
        archetypes = {spec.intent_family for spec in QUERY_SPECS}
        bare_topics = {
            "小程序", "微信小程序", "网站", "官网", "独立站", "英文官网", "管理系统", "后台系统", "业务系统",
            "ai智能体", "智能体", "ai应用", "自动化", "工作流自动化", "python脚本", "爬虫", "数据处理",
        }
        self.assertEqual(retrieval_version(), "4.1.0")
        self.assertGreater(len(QUERY_SPECS), 100)
        self.assertTrue({"vendor_search", "quote_budget", "modify_takeover"}.issubset(archetypes))
        self.assertFalse(any(spec.keyword.lower() in bare_topics for spec in QUERY_SPECS))
        self.assertTrue(all(spec.key.startswith("v3:") for spec in QUERY_SPECS))

    def test_three_probe_plan_is_exploit_explore_expand(self):
        now = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)
        selected = choose_queries(now=now, count=3)
        self.assertEqual(len(selected), 3)
        self.assertEqual([spec.lane for spec in selected], ["exploit", "explore", "expand"])
        self.assertEqual(len({spec.key for spec in selected}), 3)
        self.assertNotEqual(selected[1].topic_family, selected[0].topic_family)
        self.assertNotEqual(selected[1].intent_family, selected[0].intent_family)

    def test_legacy_v2_success_warm_starts_v3_archetype(self):
        now = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)
        performance = {
            "explicit_outsource:mini_program:0": QueryPerformance(
                runs=5,
                api_calls=5,
                returned_count=100,
                fresh_count=55,
                qualified_count=16,
                filtered_count=10,
                duplicate_count=3,
                human_positive_count=3,
                human_negative_count=0,
                last_run_at=now - timedelta(hours=7),
            ),
            "discovery:website:1:0": QueryPerformance(
                runs=4,
                api_calls=4,
                returned_count=80,
                fresh_count=30,
                qualified_count=0,
                filtered_count=30,
                duplicate_count=0,
                human_positive_count=0,
                human_negative_count=3,
                last_run_at=now - timedelta(hours=7),
            ),
        }
        selected = choose_queries(now=now, count=1, performance=performance)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].intent_family, "vendor_search")
        self.assertEqual(selected[0].topic_family, "mini_program")

    def test_recent_duplicate_saturation_suppresses_exact_probe(self):
        now = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)
        target = next(
            spec
            for spec in QUERY_SPECS
            if spec.intent_family == "vendor_search" and spec.topic_family == "mini_program"
        )
        performance = {
            target.key: QueryPerformance(
                runs=5,
                api_calls=5,
                returned_count=100,
                fresh_count=45,
                qualified_count=8,
                filtered_count=5,
                duplicate_count=35,
                last_run_at=now - timedelta(minutes=10),
            )
        }
        selected = choose_queries(now=now, count=3, performance=performance)
        self.assertNotIn(target.key, {spec.key for spec in selected})

    def test_three_call_budget_is_spent_on_three_distinct_first_pages(self):
        now = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)
        specs = choose_queries(now=now, count=3)
        connector = FakeConnector(now)
        states, used = worker._execute_plan(
            connector,
            specs,
            now=now,
            max_age_minutes=1440,
            provider_call_budget=3,
            timeout=10,
        )
        self.assertEqual(used, 3)
        self.assertEqual(len(states), 3)
        self.assertEqual([page for _, page in connector.calls], [1, 1, 1])
        self.assertEqual([keyword for keyword, _ in connector.calls], [spec.keyword for spec in specs])
        self.assertEqual(sum(state["api_calls"] for state in states), 3)

    def test_pagination_remains_available_only_when_budget_exceeds_breadth(self):
        now = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)
        specs = choose_queries(now=now, count=2)
        connector = FakeConnector(now)
        _, used = worker._execute_plan(
            connector,
            specs,
            now=now,
            max_age_minutes=1440,
            provider_call_budget=3,
            timeout=10,
        )
        self.assertEqual(used, 3)
        self.assertEqual([page for _, page in connector.calls[:2]], [1, 1])
        self.assertEqual(connector.calls[2][1], 2)


if __name__ == "__main__":
    unittest.main()
