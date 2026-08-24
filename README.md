# AI Lead Radar

AI 接单雷达：持续发现公开的最新开发需求，使用 AI 进行意向与匹配度筛选，再把高价值机会优先展示给使用者。

## 当前阶段

这是第一版 MVP 骨架，重点先把产品形态与交互跑通：

- 响应式 Web Dashboard（电脑 / iPad / 手机）
- 高意向、新发现、已联系等潜客筛选
- 潜客 AI Score、需求类型、关键信号与状态管理
- 监控策略和通知状态界面
- FastAPI 后端骨架与健康检查
- 当前使用 mock 数据，真实小红书数据连接器尚未启用

## 目录

```text
web/                 Next.js 前端
backend/             FastAPI 后端
.env.example         环境变量模板
```

## 本地运行前端

```bash
cd web
npm install
npm run dev
```

默认地址：`http://localhost:3000`

## 本地运行后端

```bash
cd backend
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

后端健康检查：`http://localhost:8000/health`

## 下一阶段

1. 建立 SQLite / PostgreSQL 数据层与去重逻辑。
2. 接入 AI 分类与评分接口。
3. 设计合规的数据连接器，只处理允许访问的公开内容，不实现绕验证码、绕风控等机制。
4. 接入飞书 / 企业微信 / Web Push 高分机会提醒。
5. 将前端 mock 数据切换到真实 API。

## 产品原则

第一版定位为“公开需求监测 + AI 辅助筛选 + 人工联系”，不做自动私信、批量营销或规避平台技术措施的采集行为。
