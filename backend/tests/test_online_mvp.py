from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT_DB = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(ROOT_DB.name, "api.db")
os.environ.pop("DATABASE_URL", None)
os.environ["AI_PROVIDER"] = "rules"
os.environ["SCAN_CONNECTORS"] = "mock"
os.environ["RADAR_WRITE_TOKEN"] = "test-secret"
os.environ.pop("FEISHU_WEBHOOK_URL", None)

from app.connectors.base import RawLead
from app.main import _ingest, app
from app.pipeline import analyze_raw
from app.ai import AIProvider
from app.storage import init_db, list_leads, make_dedupe_key, upsert_lead


class OnlineApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_ctx = TestClient(app)
        cls.client = cls.client_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_ctx.__exit__(None, None, None)

    def test_health_and_alias_fields(self):
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["ok"])
        self.assertEqual(health.json()["database"], "sqlite")

        response = self.client.post(
            "/api/v1/ingest",
            headers={"X-Radar-Token": "test-secret"},
            json={
                "adapter": "manual",
                "items": [
                    {
                        "source": "小红书",
                        "external_id": "api-online-1",
                        "title": "公司找开发做官网，有偿",
                        "excerpt": "需要手机端适配，预算可以聊",
                        "url": "https://example.com/api-online-1",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        lead = response.json()["leads"][0]
        self.assertEqual(lead["ai_score"], lead["score"])
        self.assertEqual(lead["need_type"], lead["category"])
        self.assertEqual(lead["source_id"], lead["external_id"])

    def test_write_token_and_status_update(self):
        denied = self.client.post(
            "/api/v1/ingest",
            json={"adapter": "manual", "items": [{"title": "找人做网站 有偿"}]},
        )
        self.assertEqual(denied.status_code, 401)

        ingest = self.client.post(
            "/api/v1/ingest",
            headers={"X-Radar-Token": "test-secret"},
            json={
                "adapter": "browser-helper",
                "items": [
                    {
                        "source": "小红书",
                        "external_id": "browser-1",
                        "title": "有没有会 Python 的，急，有偿",
                        "excerpt": "Excel 清洗和自动汇总",
                        "url": "https://example.com/browser-1",
                    }
                ],
            },
        )
        self.assertEqual(ingest.status_code, 200)
        lead_id = ingest.json()["leads"][0]["id"]

        patched = self.client.patch(
            f"/api/v1/leads/{lead_id}/status",
            headers={"X-Radar-Token": "test-secret"},
            json={"status": "saved"},
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["status"], "saved")

    def test_scan_is_deduped_and_monitor_state_persists(self):
        first = self.client.post("/api/v1/monitor/scan", headers={"X-Radar-Token": "test-secret"})
        second = self.client.post("/api/v1/monitor/scan", headers={"X-Radar-Token": "test-secret"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertGreaterEqual(first.json()["stored"], 1)
        self.assertEqual(second.json()["created"], 0)

        status = self.client.get("/api/v1/monitor/status")
        self.assertEqual(status.status_code, 200)
        self.assertIsNotNone(status.json()["last_scan_at"])
        self.assertIn("scanned", status.json()["last_scan_counts"])

    def test_successful_notification_is_not_repeated(self):
        raw = RawLead(
            source="小红书",
            external_id="notify-once",
            title="找人做微信小程序，有偿，急",
            excerpt="预算可以聊，尽快开始",
            published_at=datetime.now(timezone.utc),
            url="https://example.com/notify-once",
            budget="可聊",
        )
        with patch("app.main.notify_high_score", return_value=True):
            first = _ingest([raw])
            second = _ingest([raw])
        self.assertEqual(first["notified"], 1)
        self.assertEqual(second["notified"], 0)


class StorageMigrationTests(unittest.TestCase):
    def test_legacy_sqlite_row_gets_dedupe_and_discovered_time(self):
        tmp = tempfile.TemporaryDirectory()
        old_path = os.environ["DATABASE_PATH"]
        try:
            path = os.path.join(tmp.name, "legacy.db")
            os.environ["DATABASE_PATH"] = path
            conn = sqlite3.connect(path)
            conn.execute(
                """
                CREATE TABLE leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    external_id TEXT,
                    title TEXT NOT NULL,
                    excerpt TEXT NOT NULL,
                    category TEXT NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    published_at TEXT NOT NULL,
                    budget TEXT,
                    status TEXT NOT NULL DEFAULT 'new',
                    url TEXT,
                    signals_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source, external_id)
                )
                """
            )
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO leads(source, external_id, title, excerpt, category, score, published_at, budget, status, url, signals_json, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("小红书", "legacy-1", "找人做网站 有偿", "", "网页开发", 80, now, None, "new", "https://example.com/legacy?utm_source=x", "[]", now, now),
            )
            conn.commit()
            conn.close()

            init_db()
            rows = list_leads(include_non_leads=True)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0].dedupe_key)
            self.assertIsNotNone(rows[0].discovered_at)
        finally:
            os.environ["DATABASE_PATH"] = old_path
            tmp.cleanup()

    def test_tracking_params_do_not_change_dedupe_identity(self):
        now = datetime.now(timezone.utc)
        base = RawLead("公开网页", None, "找人做网站 有偿", "预算可聊", now, "https://example.com/post?id=1&utm_source=a", None)
        other = RawLead("公开网页", None, "找人做网站 有偿", "预算可聊", now, "https://example.com/post?utm_medium=b&id=1", None)
        first = analyze_raw(base, AIProvider())
        second = analyze_raw(other, AIProvider())
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(make_dedupe_key(first), make_dedupe_key(second))


if __name__ == "__main__":
    unittest.main()
