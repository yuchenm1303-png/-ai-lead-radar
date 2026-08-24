from datetime import datetime, timezone

from .base import RawLead


class MockConnector:
    name = "mock-xiaohongshu"

    def fetch_latest(self) -> list[RawLead]:
        now = datetime.now(timezone.utc)
        return [
            RawLead(
                source="小红书",
                external_id="mock-xhs-001",
                title="有没有会做微信小程序的，有偿",
                excerpt="想做一个预约和会员积分功能，最好能尽快沟通。",
                published_at=now,
                budget="预算可聊",
            ),
            RawLead(
                source="小红书",
                external_id="mock-xhs-002",
                title="公司想做英文官网，求靠谱开发",
                excerpt="展示产品和询盘为主，需要手机端适配，项目可以聊报价。",
                published_at=now,
                budget="询价中",
            ),
            RawLead(
                source="小红书",
                external_id="mock-xhs-003",
                title="前端新手应该怎么学？求课程推荐",
                excerpt="最近准备学习 HTML CSS JavaScript，有没有适合零基础的教程。",
                published_at=now,
            ),
        ]
