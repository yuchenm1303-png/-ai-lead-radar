from __future__ import annotations

import unittest

from benchmarks.source_benchmark import extract_candidates


class JustOneUnifiedSearchParserTests(unittest.TestCase):
    def test_extracts_justone_cross_search_shape(self) -> None:
        payload = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "author": "demo",
                        "content": "寻找开发团队做一个网站",
                        "createTime": 1787535613083,
                        "sourceName": "小红书",
                        "title": "寻找开发团队",
                        "url": "https://www.xiaohongshu.com/explore/68ba0fd000000002b025fa1",
                    }
                ],
                "nextCursor": "",
                "totalNumber": 1,
            },
        }

        raw_count, candidates = extract_candidates(payload)

        self.assertEqual(raw_count, 1)
        self.assertEqual(len(candidates), 1)
        item = candidates[0]
        self.assertEqual(item.external_id, "68ba0fd000000002b025fa1")
        self.assertEqual(item.title, "寻找开发团队")
        self.assertEqual(item.excerpt, "寻找开发团队做一个网站")
        self.assertEqual(item.url, "https://www.xiaohongshu.com/explore/68ba0fd000000002b025fa1")
        self.assertIsNotNone(item.published_at.tzinfo)

    def test_uses_stable_url_id_when_explore_id_missing(self) -> None:
        payload = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "content": "网页开发有偿",
                        "createTime": 1787535613083,
                        "sourceName": "小红书",
                        "title": "网页开发有偿",
                        "url": "https://www.xiaohongshu.com/search_result?keyword=test",
                    }
                ]
            },
        }

        raw_count, candidates = extract_candidates(payload)

        self.assertEqual(raw_count, 1)
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0].external_id.startswith("url-"))
        self.assertGreater(len(candidates[0].external_id), 10)


if __name__ == "__main__":
    unittest.main()
