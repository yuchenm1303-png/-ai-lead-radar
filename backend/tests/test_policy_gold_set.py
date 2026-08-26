import json
import unittest
from pathlib import Path

from app.domain import ActorRole
from app.policy import POLICY_PATH, assess_text, evaluate_gold_set, load_policy
from app.query_engine import QUERY_SPECS

GOLD_PATH = POLICY_PATH.with_name("gold_set.json")


class PolicyGoldSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = json.loads(GOLD_PATH.read_text(encoding="utf-8"))

    def test_gold_set_quality_gate(self):
        metrics = evaluate_gold_set(self.samples)
        self.assertGreaterEqual(metrics["precision"], 0.95, metrics)
        self.assertGreaterEqual(metrics["recall"], 0.95, metrics)
        self.assertGreaterEqual(metrics["f1"], 0.95, metrics)
        self.assertGreaterEqual(metrics["actor_accuracy"], 0.80, metrics)

    def test_service_provider_is_not_buyer(self):
        assessment = assess_text(
            "外贸独立站建设英文官网开发多语言网站",
            "提供定制开发服务，成品交付，售后无忧",
        )
        self.assertFalse(assessment.is_lead)
        self.assertEqual(assessment.actor_role, ActorRole.PROVIDER)

    def test_explicit_team_search_is_high_intent(self):
        assessment = assess_text(
            "寻找AI智能体开发团队或个人，要求产品做成网页端登录使用",
            "具体功能需要进一步沟通",
        )
        self.assertTrue(assessment.is_lead)
        self.assertEqual(assessment.actor_role, ActorRole.BUYER)
        self.assertGreaterEqual(assessment.intent_score, 85)

    def test_query_portfolio_has_no_bare_topic_queries(self):
        policy = load_policy()
        bare_terms = {
            str(term).strip().lower()
            for topic in policy.get("topics", [])
            for term in topic.get("query_terms", [])
            if str(term).strip()
        }
        self.assertGreater(len(QUERY_SPECS), 10)
        for spec in QUERY_SPECS:
            self.assertNotIn(spec.keyword.strip().lower(), bare_terms, spec)
            self.assertNotEqual(spec.intent_family, "")
            self.assertNotEqual(spec.topic_family, "")


if __name__ == "__main__":
    unittest.main()
