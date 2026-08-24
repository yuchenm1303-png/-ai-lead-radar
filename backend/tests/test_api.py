from __future__ import annotations

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.settings import get_settings


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["DATABASE_PATH"] = os.path.join(cls.tmp.name, "api.db")
        os.environ["AI_PROVIDER"] = "heuristic"
        os.environ["SCAN_CONNECTORS"] = "mock"
        get_settings.cache_clear()
        from app.main import app
        cls.client_ctx = TestClient(app)
        cls.client = cls.client_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_ctx.__exit__(None, None, None)
        cls.tmp.cleanup()
        get_settings.cache_clear()

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_ingest_list_status(self):
        response = self.client.post("/api/v1/ingest", json={
            "adapter": "manual",
            "items": [{
                "source": "小红书",
                "source_id": "api-1",
                "title": "公司想找人做一个管理系统，有偿",
                "content": "需要后台、权限和报表，预算可以聊",
                "url": "https://example.com/api-1"
            }]
        })
        self.assertEqual(response.status_code, 200)
        lead = response.json()["leads"][0]
        listed = self.client.get("/api/v1/leads?min_score=0").json()
        self.assertTrue(any(item["id"] == lead["id"] for item in listed))
        patched = self.client.patch(f"/api/v1/leads/{lead['id']}/status", json={"status": "contacted"})
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["status"], "contacted")

    def test_scan(self):
        response = self.client.post("/api/v1/monitor/scan")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.json()["scanned"], 1)


if __name__ == "__main__":
    unittest.main()
