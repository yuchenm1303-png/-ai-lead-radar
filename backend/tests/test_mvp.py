import os, sys, tempfile, unittest
from datetime import datetime, timezone, timedelta
ROOT=os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0,ROOT)
_tmp=tempfile.TemporaryDirectory(); os.environ['DATABASE_PATH']=os.path.join(_tmp.name,'test.db')
from app.ai import AIProvider
from app.connectors.base import RawLead
from app.pipeline import analyze_raw
from app.scoring import prefilter_text, score_text
from app.storage import init_db, list_leads, upsert_lead, update_lead_status

class MVPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): init_db()
    def test_real_need_passes(self):
        r=score_text('学校比赛需要做个网站，有偿','预算可以聊',datetime.now(timezone.utc))
        self.assertTrue(r.is_lead); self.assertGreaterEqual(r.score,65)
    def test_learning_filtered(self):
        self.assertFalse(prefilter_text('Python 怎么学','求课程推荐').passed)
    def test_pipeline_and_dedupe(self):
        raw=RawLead('小红书','x1','有没有人会做微信小程序，有偿','预约系统，急',datetime.now(timezone.utc),'https://example.com/1','可聊')
        p=analyze_raw(raw,AIProvider()); self.assertIsNotNone(p)
        a=upsert_lead(p); b=upsert_lead(p); self.assertEqual(a.id,b.id)
        self.assertEqual(len([x for x in list_leads() if x.external_id=='x1']),1)
    def test_status_persists_on_upsert(self):
        raw=RawLead('小红书','x2','公司找开发做官网','有偿，尽快',datetime.now(timezone.utc),'https://example.com/2','预算待聊')
        p=analyze_raw(raw,AIProvider()); lead=upsert_lead(p); update_lead_status(lead.id,'saved'); refreshed=upsert_lead(p); self.assertEqual(refreshed.status,'saved')
    def test_freshness_drops(self):
        now=datetime.now(timezone.utc)
        fresh=score_text('找人做网站 有偿','',now).freshness_score
        old=score_text('找人做网站 有偿','',now-timedelta(days=10)).freshness_score
        self.assertGreater(fresh,old)

if __name__=='__main__': unittest.main()
