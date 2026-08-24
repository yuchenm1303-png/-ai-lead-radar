from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from app.connectors.base import RawLead
from app.pipeline import process_items
from app.prefilter import prefilter_text
from app.settings import get_settings
from app.storage import init_db, list_leads, update_lead_status


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DATABASE_PATH"] = os.path.join(self.tmp.name, "test.db")
        os.environ["AI_PROVIDER"] = "heuristic"
        os.environ["SCAN_CONNECTORS"] = "mock"
        os.environ.pop("FEISHU_WEBHOOK_URL", None)
        get_settings.cache_clear()
        init_db()

    def tearDown(self):
        self.tmp.cleanup()
        get_settings.cache_clear()

    def test_prefilter_rejects_learning_content(self):
        result = prefilter_text("有没有人推荐学习微信小程序的课程", "零基础怎么学")
        self.assertFalse(result.accepted)

    def test_pipeline_keeps_real_request_and_filters_tutorial(self):
        now = datetime.now(timezone.utc)
        result = process_items([
            RawLead("小红书", "a", "学校比赛需要做个网站，有偿", "需要展示和报名页面", now, "https://example.com/a", "可沟通"),
            RawLead("小红书", "b", "Python 怎么学", "求课程推荐", now, "https://example.com/b", None),
        ])
        self.assertEqual(result.received, 2)
        self.assertEqual(result.stored, 1)
        self.assertEqual(result.filtered_out, 1)
        self.assertTrue(result.leads[0].is_lead)
        self.assertGreaterEqual(result.leads[0].score, 70)

    def test_dedupe_preserves_status(self):
        now = datetime.now(timezone.utc)
        item = RawLead("小红书", "same", "有没有会 Python 的，急", "有偿处理 Excel", now, "https://example.com/same", "500")
        first = process_items([item])
        lead_id = first.leads[0].id
        update_lead_status(lead_id, "saved")
        second = process_items([item])
        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, 1)
        leads = list_leads(is_lead=None)
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].status, "saved")

    def test_freshness_affects_score(self):
        now = datetime.now(timezone.utc)
        fresh = process_items([RawLead("小红书", "fresh", "找人做微信小程序，有偿", "预算可聊", now, None, "预算可聊")]).leads[0]
        old = process_items([RawLead("小红书", "old", "找人做微信小程序，有偿", "预算可聊", now - timedelta(days=10), None, "预算可聊")]).leads[0]
        self.assertGreater(fresh.score, old.score)


if __name__ == "__main__":
    unittest.main()
