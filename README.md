# AI Lead Radar

AI 接单雷达：持续发现公开或用户授权导入的最新开发需求，先做低成本预筛，再使用可替换的分类器判断真实购买/委托意向，计算 AI Score，并把高价值机会优先展示给使用者。

## 当前 MVP 能力

- FastAPI API：健康检查、潜客列表/详情、状态更新、手动导入、Browser Helper 导入、扫描状态与扫描入口。
- SQLite 持久化：自动初始化/兼容旧表结构、稳定去重键、状态保留、扫描状态持久化。
- Pipeline：prefilter → classifier → freshness/fit/intent scoring → dedupe/store → high-score notification。
- AI Provider：`heuristic` 默认可离线运行；配置环境变量后可切换 `openai`，失败时自动回退规则分类。
- SourceAdapter：保留 Mock Connector；真实小红书第一阶段使用 Manual Import / Browser Helper，不实现绕 CAPTCHA、风控、签名或登录保护。
- 通知：支持飞书 Webhook，高分新 Lead 只在首次入库时提醒。
- Next.js 前端骨架仍保留；正式线上产品 UI 由 `Chen-s-Homepage/radar/` 承载并逐步接入本 API。

## 本地运行

后端：

```bash
cd backend
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

前端：

```bash
cd web
npm install
npm run dev
```

## 关键 API

- `GET /health`
- `GET /api/v1/leads`
- `GET /api/v1/leads/{id}`
- `PATCH /api/v1/leads/{id}/status`
- `POST /api/v1/ingest`
- `GET /api/v1/monitor/status`
- `POST /api/v1/monitor/scan`

`POST /api/v1/ingest` 接受 `manual` 或 `browser-helper` 适配器的数据。自动平台采集器只有在明确配置、合法且符合平台规则时才启用。

## AI 与配置

复制 `.env.example` 为本地 `.env`。API Key、Webhook、Token 不得提交 GitHub。

- `AI_PROVIDER=heuristic`：零外部依赖的默认分类器。
- `AI_PROVIDER=openai` + `OPENAI_API_KEY` + `OPENAI_MODEL`：使用 OpenAI Responses API 结构化输出；请求失败自动回退 heuristic。
- `NOTIFY_MIN_SCORE=85` + `FEISHU_WEBHOOK_URL`：启用高分机会提醒。
- `RADAR_WRITE_TOKEN`：可选写接口保护。为空时适合本地/受控 MVP；生产可开启。

## 测试

```bash
cd backend
python -m unittest discover -s tests -v
```

CI 同时执行 Next.js build、Python compile 和后端单元/API 测试。

## 产品边界

定位始终是“公开/授权需求监测 + AI 辅助筛选 + 人工联系”。不做自动私信、自动评论、批量营销，不绕验证码、登录保护、风控、私有 API 签名或设备指纹，也不批量收集手机号/微信号等个人资料。
