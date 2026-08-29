import json
import unittest

from app.domain import ActorRole
from app.policy import POLICY_PATH, assess_text, evaluate_gold_set
from app.query_engine import QUERY_SPECS

GOLD_PATH = POLICY_PATH.with_name("gold_set.json")


class PolicyGoldSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = json.loads(GOLD_PATH.read_text(encoding="utf-8"))

    def test_gold_set_quality_gate(self):
        metrics = evaluate_gold_set(self.samples)
        mistakes = []
        for sample in self.samples:
            assessment = assess_text(sample.get("title", ""), sample.get("excerpt", ""))
            expected_lead = sample.get("label") == "lead"
            expected_role = sample.get("actor_role")
            if assessment.is_lead != expected_lead or assessment.actor_role.value != expected_role:
                mistakes.append({
                    "id": sample.get("id"),
                    "expected_label": sample.get("label"),
                    "actual_lead": assessment.is_lead,
                    "expected_role": expected_role,
                    "actual_role": assessment.actor_role.value,
                    "reason_codes": assessment.reason_codes,
                })
        message = {"metrics": metrics, "mistakes": mistakes}
        self.assertGreaterEqual(metrics["precision"], 0.95, message)
        self.assertGreaterEqual(metrics["recall"], 0.95, message)
        self.assertGreaterEqual(metrics["f1"], 0.95, message)
        self.assertGreaterEqual(metrics["actor_accuracy"], 0.80, message)

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

    def test_retrieval_portfolio_is_separate_intent_driven_candidate_acquisition(self):
        archetypes = {spec.intent_family for spec in QUERY_SPECS}
        keywords = {spec.keyword.lower() for spec in QUERY_SPECS}
        bare_topics = {"网站", "官网", "小程序", "微信小程序", "python脚本", "爬虫", "自动化"}
        self.assertGreater(len(QUERY_SPECS), 100)
        self.assertTrue({"vendor_search", "quote_budget", "modify_takeover", "business_need"}.issubset(archetypes))
        self.assertTrue(any("微信小程序" in keyword for keyword in keywords))
        self.assertTrue(any("官网" in keyword for keyword in keywords))
        self.assertTrue(any("爬虫" in keyword or "python脚本" in keyword for keyword in keywords))
        self.assertFalse(any(keyword in bare_topics for keyword in keywords))
        self.assertTrue(all(spec.key.startswith("v3:") for spec in QUERY_SPECS))


if __name__ == "__main__":
    unittest.main()
