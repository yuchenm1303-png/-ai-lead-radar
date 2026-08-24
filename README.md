# AI Lead Radar

AI 接单雷达：从公开或用户授权的数据来源获取最新开发需求，经过低成本预筛、可替换 AI 分类、评分、去重和持久化后，将值得人工联系的机会优先展示并通知。

## 当前线上 MVP

生产展示页：`https://smirel.com/radar/`

当前线上数据链路：

`公开 / 用户可正常查看的内容 → Manual / Browser-assisted Import → cheap prefilter → 可选语义 AI → AI Score → dedupe → Supabase Postgres → Radar 页面 → 人工状态管理`

已经上线：

- `smirel.com/radar/` 已从静态 Demo 数据切换到真实 Lead API
- Supabase Postgres 持久化，Radar 数据使用独立的 `lead_radar_*` 表
- Lead 列表、搜索、筛选、AI Score、AI 判断理由、发布时间 / 发现时间、预算、原帖入口
- 收藏、已联系、忽略状态直接写回数据库
- Manual Import 安全入口，支持用户主动导入已经能正常看到的公开需求
- cheap prefilter → 可替换 AI Provider → 综合评分 → store
- 生产 Edge API 在存在 `OPENAI_API_KEY + OPENAI_MODEL` 时启用 OpenAI Structured Outputs；未配置或调用失败时自动回退 rules
- 高分飞书通知代码已接入；存在 `FEISHU_WEBHOOK_URL` 时启用
- 去重在语义 AI 调用前完成，重复内容不重复入库，也不会重复消耗模型请求
- 输入长度限制、URL `http/https` 白名单、前端 HTML 转义，避免公开内容成为 XSS 输入
- Radar 表启用 RLS，并撤销 `anon` / `authenticated` 的直接表权限；浏览器只通过 Edge API 访问

当前没有启用自动小红书反爬。`立即扫描` 只检查当前安全数据源并记录扫描状态；真实小红书内容第一版通过用户主动导入 / 浏览器辅助流程进入系统。不会实现绕 CAPTCHA、绕登录、设备指纹伪造、私有接口签名破解或高频撞接口。

## 仓库结构

```text
backend/                                  FastAPI 本地 / 可独立部署后端
web/                                      Next.js 历史 Dashboard
supabase/migrations/                      生产数据库 migration
supabase/functions/lead-radar-api/        生产 Edge API
```

生产静态 UI 位于 `yuchenm1303-png/Chen-s-Homepage/radar/`，由 GitHub Pages 承载。

## API

生产 API 路径：

- `GET /health`
- `GET /api/v1/leads?min_score=0&status=new&limit=100`
- `PATCH /api/v1/leads/{id}/status`
- `POST /api/v1/ingest/manual`
- `GET /api/v1/monitor/status`
- `POST /api/v1/monitor/scan`

手动导入接受最多 200 条用户已经能够合法看到 / 导出的内容，每条只需来源、标题、短摘要、发布时间、原帖链接和可选预算信息。内容会先经过 prefilter，再进入语义 AI（若配置）或规则判断与评分，非真实需求不会入库。

## FastAPI 本地运行

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

任何 API Key、Webhook 或 token 都只能放环境变量，禁止提交仓库。

本地 FastAPI 默认 `AI_PROVIDER=rules`。若启用 OpenAI，设置：

- `AI_PROVIDER=openai`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

生产 Edge Function 使用：

- `OPENAI_API_KEY` + `OPENAI_MODEL`：启用语义分类；缺失时使用 rules
- `FEISHU_WEBHOOK_URL`：启用高分飞书通知
- `NOTIFY_MIN_SCORE`：通知阈值，默认 85

## 数据与合规边界

只保存完成判断与人工跟进所需的最小信息：来源、标题、短摘要、发布时间 / 发现时间、原帖 URL、评分、判断理由、信号和人工状态。不需要就不保存用户详细个人资料。

不做自动私信、评论、关注、批量营销，也不实现绕 CAPTCHA、绕登录保护、设备指纹伪造、私有 API 签名破解等机制。
