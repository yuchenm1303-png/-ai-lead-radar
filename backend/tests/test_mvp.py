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
    def test_xhs_direct_team_request_is_high_priority(self):
        r=score_text(
            '寻找ai智能体开发团队或个人，要求产品做成网页端登录使用',
            '具体功能：围绕社交媒体平台寻找潜在客户，给我们推送，其他细节需要进一步沟通',
            datetime.now(timezone.utc),
        )
        self.assertTrue(r.is_lead)
        self.assertGreaterEqual(r.score,85)
        self.assertEqual(r.priority,'high')
        self.assertIn('明确找开发方',r.signals)
    def test_terse_xhs_need_without_development_verb_reaches_classifier(self):
        r=score_text(
            '急！需要icp+edi，小程序，知识付费，在线交易',
            '其他细节可以沟通',
            datetime.now(timezone.utc),
        )
        self.assertTrue(r.is_lead)
        self.assertTrue(prefilter_text('急！需要icp+edi，小程序，知识付费，在线交易').direct_buyer)
    def test_learning_request_is_filtered_even_with_search_wording(self):
        r=score_text('寻找适合学习AI开发的课程','推荐一下',datetime.now(timezone.utc))
        self.assertFalse(r.is_lead)
    def test_course_product_request_is_not_mistaken_for_learning(self):
        r=score_text('找人开发在线课程网站','需要后台、登录和内容管理',datetime.now(timezone.utc))
        self.assertTrue(r.is_lead)
    def test_learning_filtered(self):
        self.assertFalse(prefilter_text('Python 怎么学','求课程推荐').passed)
    def test_recruiting_is_not_a_lead(self):
        r=score_text('海大有同学找实习吗？网站开发实习生','公司在学校旁边，主要工作是网站开发',datetime.now(timezone.utc))
        self.assertFalse(r.is_lead)
        self.assertIn('排除:招聘/实习', r.signals)
    def test_content_recommendation_is_not_a_lead(self):
        samples = [
            ('这个网站我愿意称之为本年度最伟大的发明！！','#代码 #python #程序员 #免费学习资源网站 #网站推荐'),
            ('我愿称这个网站为年度最伟大的发现！！！','#python #编程 #学习网站 #程序员'),
            ('不体面，但很挣的5个网站','#副业赚零花钱 #学习网站 #项目'),
            ('📚交易者必备｜8个实用参考网站建议收藏','炒股必看八大网站，你用了几个'),
        ]
        for title, excerpt in samples:
            with self.subTest(title=title):
                self.assertFalse(score_text(title, excerpt, datetime.now(timezone.utc)).is_lead)
    def test_budget_article_is_not_a_buyer_request(self):
        r=score_text(
            '很多老板以为，网站做完上线就一劳永逸了。真不是。',
            '最近帮朋友梳理他们公司官网的年度预算，顺手把网站维护这笔账拆开看了',
            datetime.now(timezone.utc),
        )
        self.assertFalse(r.is_lead)
    def test_service_provider_self_promo_is_not_a_lead(self):
        r=score_text('UI 接单｜中医门诊预约挂号小程序','可承接UI设计和小程序页面',datetime.now(timezone.utc))
        self.assertFalse(r.is_lead)
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
