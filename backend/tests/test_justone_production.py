import unittest
from datetime import datetime, timedelta, timezone

from app.connectors.justone import _note_to_raw
from app.query_engine import QUERY_SPECS, choose_queries
from collectors.justone_to_edge import _fresh


class JustOneProductionTests(unittest.TestCase):
    def test_v4_note_shape_normalizes_to_raw_lead(self):
        note = {
            "id": "6a8d6a49000000000400a38e",
            "title": "急！需要icp+edi，小程序，知识付费，在线交易",
            "desc": "需要开发小程序，其他细节可沟通",
            "timestamp": 1787652681,
        }
        raw = _note_to_raw(note)
        self.assertIsNotNone(raw)
        assert raw is not None
        self.assertEqual(raw.source, "小红书")
        self.assertEqual(raw.external_id, note["id"])
        self.assertIn("小程序", raw.title)
        self.assertEqual(raw.excerpt, note["desc"])
        self.assertEqual(raw.url, f"https://www.xiaohongshu.com/explore/{note['id']}")
        self.assertEqual(raw.published_at.tzinfo, timezone.utc)

    def test_v4_millisecond_timestamp_is_supported(self):
        note = {
            "id": "6a8d6c33000000002003cecb",
            "title": "小程序需求",
            "desc": "找人做",
            "timestamp": 1787653171000,
        }
        raw = _note_to_raw(note)
        self.assertIsNotNone(raw)
        assert raw is not None
        self.assertEqual(int(raw.published_at.timestamp()), 1787653171)

    def test_query_engine_returns_unique_weighted_anchors(self):
        now = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)
        chosen = choose_queries(now=now, count=4)
        self.assertEqual(len(chosen), 4)
        self.assertEqual(len({item.key for item in chosen}), 4)
        self.assertTrue(all(item in QUERY_SPECS for item in chosen))

    def test_query_override_uses_one_exact_query(self):
        chosen = choose_queries(override="找人做网站", count=3)
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0].keyword, "找人做网站")

    def test_freshness_filter_rejects_old_and_far_future_posts(self):
        now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
        fresh = _note_to_raw({
            "id": "fresh123456789012345",
            "title": "小程序",
            "desc": "找人做",
            "timestamp": int((now - timedelta(minutes=30)).timestamp()),
        })
        old = _note_to_raw({
            "id": "old12345678901234567",
            "title": "小程序",
            "desc": "找人做",
            "timestamp": int((now - timedelta(days=2)).timestamp()),
        })
        future = _note_to_raw({
            "id": "future1234567890123",
            "title": "小程序",
            "desc": "找人做",
            "timestamp": int((now + timedelta(minutes=20)).timestamp()),
        })
        assert fresh is not None and old is not None and future is not None
        self.assertTrue(_fresh(fresh, now=now, max_age_minutes=1440))
        self.assertFalse(_fresh(old, now=now, max_age_minutes=1440))
        self.assertFalse(_fresh(future, now=now, max_age_minutes=1440))


if __name__ == "__main__":
    unittest.main()
