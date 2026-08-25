from __future__ import annotations

from benchmarks.source_benchmark import extract_candidates


def test_extracts_justone_cross_search_shape() -> None:
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

    assert raw_count == 1
    assert len(candidates) == 1
    item = candidates[0]
    assert item.external_id == "68ba0fd000000002b025fa1"
    assert item.title == "寻找开发团队"
    assert item.excerpt == "寻找开发团队做一个网站"
    assert item.url == "https://www.xiaohongshu.com/explore/68ba0fd000000002b025fa1"
    assert item.published_at.tzinfo is not None


def test_uses_stable_url_id_when_explore_id_missing() -> None:
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

    assert raw_count == 1
    assert len(candidates) == 1
    assert candidates[0].external_id.startswith("url-")
    assert len(candidates[0].external_id) > 10
