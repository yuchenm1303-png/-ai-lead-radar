# AI Lead Radar

AI 接单雷达：从公开或用户授权的数据来源获取最新开发需求，经过低成本预筛、可替换 AI 分类、评分、去重和持久化后，将值得人工联系的机会优先展示并通知。

## 当前 MVP 能力

- FastAPI API：健康检查、Lead 列表、状态更新、手动导入、扫描、监控状态
- SQLite 持久化与增量 schema 初始化；基于 dedupe key 的去重，重复抓取不会覆盖人工状态
- cheap prefilter → AI Provider（可选）→ 综合评分 → store 流水线
- AI Provider 可替换；默认规则模式零成本运行，配置环境变量后可启用 OpenAI Structured Outputs
- 安全数据入口：Manual Import / 浏览器辅助导入可直接调用手动 ingest API
- Mock 小红书 Connector 仅用于端到端验证；未实现验证码/风控绕过、私有签名破解或高频抓取
- 飞书 Webhook 高分提醒（可选）
- Next.js 历史 Dashboard 保留；生产展示页位于 `smirel.com/radar/`

## API

- `GET /health`
- `GET /api/v1/leads?min_score=0&status=new&limit=100`
- `PATCH /api/v1/leads/{id}/status`
- `POST /api/v1/ingest/manual`
- `GET /api/v1/monitor/status`
- `POST /api/v1/monitor/scan`

手动导入接受最多 200 条用户已经能够合法看到/导出的内容，每条只需来源、标题、短摘要、发布时间、原帖链接和可选预算信息。内容会先经过 prefilter，再进入 AI/规则判断与评分，非真实需求不会入库。

## 本地运行

```bash
cd backend
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

测试：

```bash
cd backend
python -m unittest discover -s tests -v
```

## 配置

复制 `.env.example` 到本地 `.env`。任何 API Key、Webhook 或 token 都只能放环境变量，禁止提交仓库。

默认 `AI_PROVIDER=rules`。若启用 OpenAI，设置 `AI_PROVIDER=openai`、`OPENAI_API_KEY` 和 `OPENAI_MODEL`。未配置或 AI 请求失败时，流水线会安全回退到规则评分，不阻塞数据处理。

## 数据与合规边界

只保存完成判断与人工跟进所需的最小信息：来源、标题、短摘要、发布时间/发现时间、原帖 URL、评分、判断理由、信号和人工状态。不做自动私信、评论、关注、批量营销，也不实现绕 CAPTCHA、绕登录保护、设备指纹伪造、私有 API 签名破解等机制。
