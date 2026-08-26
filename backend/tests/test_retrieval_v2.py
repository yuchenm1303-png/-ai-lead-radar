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


class RetrievalV2Tests(unittest.TestCase):
    def test_portfolio_uses_alias_terms_and_three_lanes(self):
        lanes = {spec.lane for spec in QUERY_SPECS}
        keywords = {spec.keyword.lower() for spec in QUERY_SPECS}
        self.assertEqual(retrieval_version(), "2.0.0")
        self.assertTrue({"precision", "discovery", "broad"}.issubset(lanes))
        self.assertTrue(any("微信小程序" in value for value in keywords))
        self.assertTrue(any("官网" in value for value in keywords))
        self.assertTrue(any("脚本" in value for value in keywords))

    def test_two_query_plan_diversifies_lane_and_topic(self):
        now = datetime(2026, 8, 26, 14, 0, tzinfo=timezone.utc)
        selected = choose_queries(now=now, count=2)
        self.assertEqual(len(selected), 2)
        self.assertEqual({spec.lane for spec in selected}, {"precision", "discovery"})
        self.assertEqual(len({spec.topic_family for spec in selected}), 2)

    def test_recent_duplicate_saturation_pushes_query_out_of_plan(self):
        now = datetime(2026, 8, 26, 14, 0, tzinfo=timezone.utc)
        target = next(spec for spec in QUERY_SPECS if spec.key == "explicit_outsource:mini_program:0")
        performance = {
            target.key: QueryPerformance(
                runs=4,
                api_calls=4,
                returned_count=80,
                fresh_count=40,
                qualified_count=8,
                filtered_count=4,
                duplicate_count=28,
                last_run_at=now - timedelta(minutes=10),
            )
        }
        selected = choose_queries(now=now, count=2, performance=performance)
        self.assertNotIn(target.key, {spec.key for spec in selected})

    def test_provider_budget_is_breadth_first_before_pagination(self):
        now = datetime(2026, 8, 26, 14, 0, tzinfo=timezone.utc)
        specs = choose_queries(now=now, count=2)
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
        self.assertEqual(connector.calls[0], (specs[0].keyword, 1))
        self.assertEqual(connector.calls[1], (specs[1].keyword, 1))
        self.assertEqual(connector.calls[2][1], 2)
        self.assertEqual(len(states), 2)
        self.assertEqual(sum(state["api_calls"] for state in states), 3)

    def test_pagination_stops_after_freshness_frontier(self):
        now = datetime(2026, 8, 26, 14, 0, tzinfo=timezone.utc)
        state = {
            "pages": 1,
            "has_more": True,
            "last_page_raw_count": 20,
            "last_page_oldest_published_at": (now - timedelta(hours=27)).isoformat(),
        }
        self.assertFalse(
            worker._should_fetch_next_page(
                state,
                now=now,
                max_age_minutes=1440,
                provider_calls_used=1,
                provider_call_budget=3,
            )
        )


if __name__ == "__main__":
    unittest.main()
