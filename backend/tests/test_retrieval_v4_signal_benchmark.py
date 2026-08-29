from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from benchmarks.retrieval_v4_signal_benchmark import (
    SignalProbe,
    build_default_plan,
    build_request_url,
    extract_actor_notes,
    extract_comments,
    extract_suggestions,
    keyword_suggestion_probe,
    validate_plan_budget,
)


class RetrievalV4SignalBenchmarkTests(unittest.TestCase):
    def test_default_plan_is_three_controlled_calls(self):
        plan = build_default_plan()
        self.assertEqual(len(plan), 3)
        self.assertEqual(
            [probe.generator for probe in plan],
            ["conversation_negative_control", "conversation_buyer_discovery", "actor_expansion"],
        )
        self.assertEqual(plan[0].sort, "latest")
        self.assertEqual(plan[1].sort, "latest")

    def test_budget_refuses_plan_expansion(self):
        plan = build_default_plan()
        validate_plan_budget(plan, 3)
        with self.assertRaises(ValueError):
            validate_plan_budget(plan, 2)

    def test_comment_request_uses_note_id_and_latest(self):
        probe = SignalProbe(
            key="comments",
            generator="conversation_buyer_discovery",
            endpoint="/api/xiaohongshu/get-note-comment/v2",
            note_id="abc123",
            sort="latest",
        )
        query = parse_qs(urlparse(build_request_url(probe, "secret")).query)
        self.assertEqual(query["noteId"], ["abc123"])
        self.assertEqual(query["sort"], ["latest"])
        self.assertEqual(query["token"], ["secret"])

    def test_actor_request_uses_user_id(self):
        probe = SignalProbe(
            key="actor",
            generator="actor_expansion",
            endpoint="/api/xiaohongshu/get-user-note-list/v4",
            user_id="user-1",
        )
        query = parse_qs(urlparse(build_request_url(probe, "secret")).query)
        self.assertEqual(query["userId"], ["user-1"])
        self.assertNotIn("keyword", query)

    def test_keyword_suggestion_capability_is_available_but_not_default(self):
        plan = build_default_plan()
        suggestion = keyword_suggestion_probe("网页插件")
        self.assertNotIn(suggestion, plan)
        query = parse_qs(urlparse(build_request_url(suggestion, "secret")).query)
        self.assertEqual(query["keyword"], ["网页插件"])

    def test_extract_comments_preserves_author_and_text(self):
        payload = {
            "code": 0,
            "data": {
                "comments": [
                    {
                        "id": "c1",
                        "content": "多少钱？我公司也想做一个",
                        "user": {"id": "u1", "nickname": "甲方A"},
                        "like_count": 3,
                    },
                    {
                        "id": "c2",
                        "content": "我们专业承接开发",
                        "user": {"id": "u2", "nickname": "服务商B"},
                    },
                ]
            },
        }
        raw, comments = extract_comments(payload)
        self.assertEqual(raw, 2)
        self.assertEqual(len(comments), 2)
        self.assertEqual(comments[0]["author_id"], "u1")
        self.assertIn("公司也想做", comments[0]["text"])

    def test_extract_actor_notes_ignores_nested_user_objects(self):
        payload = {
            "code": 0,
            "data": {
                "notes": [
                    {"id": "n1", "title": "承接网站开发", "desc": "专业开发团队", "timestamp": 1},
                    {"id": "n2", "display_title": "开发案例", "desc": "小程序案例", "note_type": "normal"},
                ],
                "user": {"id": "u1", "name": "某开发公司"},
            },
        }
        raw, notes = extract_actor_notes(payload)
        self.assertEqual(raw, 2)
        self.assertEqual([item["note_id"] for item in notes], ["n1", "n2"])

    def test_extract_suggestions_deduplicates(self):
        payload = {
            "code": 0,
            "data": {"items": [{"keyword": "网页自动化"}, {"keyword": "网页自动化"}, {"query": "浏览器自动化"}]},
        }
        raw, suggestions = extract_suggestions(payload)
        self.assertEqual(raw, 3)
        self.assertEqual([item["keyword"] for item in suggestions], ["网页自动化", "浏览器自动化"])


if __name__ == "__main__":
    unittest.main()
