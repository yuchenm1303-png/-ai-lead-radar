const ALLOWED = new Set(["https://smirel.com", "http://localhost:3000"]);
const SB_URL = Deno.env.get("SUPABASE_URL") || "";
const LEGACY_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
let SECRET_KEY = LEGACY_KEY;
try {
  const keys = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}");
  SECRET_KEY = keys.default || SECRET_KEY;
} catch {}
const OPENAI_KEY = Deno.env.get("OPENAI_API_KEY") || "";
const OPENAI_MODEL = Deno.env.get("OPENAI_MODEL") || "";
const FEISHU_WEBHOOK_URL = Deno.env.get("FEISHU_WEBHOOK_URL") || "";
const NOTIFY_MIN_SCORE = Math.max(0, Math.min(100, Number(Deno.env.get("NOTIFY_MIN_SCORE") || 85)));

const SERVICES = {
  "AI / 自动化": ["ai", "智能体", "大模型", "自动化", "软件开发"],
  "微信小程序": ["小程序", "微信小程序", "预约小程序", "商城小程序"],
  "独立站": ["独立站", "shopify", "跨境网站", "英文官网"],
  "网页开发": ["网页", "网站", "官网", "前端", "后端", "管理系统", "h5"],
  "Python / 数据": ["python", "excel", "数据处理", "爬虫", "脚本"]
};
const INTENT = {
  "有偿": 24, "预算": 18, "多少钱": 18, "报价": 18, "找人": 22, "寻找": 26, "寻求": 24,
  "求开发": 26, "找开发": 26, "开发团队": 26, "开发个人": 22, "帮忙做": 22, "需要开发": 24,
  "需要做": 16, "急": 16, "急需": 20, "外包": 24, "可以聊": 8, "有没有人会": 14, "求助": 12,
  "找谁": 15, "想做": 16, "想搞": 16, "帮忙写": 20, "有没有会": 14, "谁能做": 24, "谁会做": 22,
  "有人能做": 22, "有人接": 20, "能接吗": 20, "接单吗": 18
};
const BUYER_ACTIONS = [
  "有偿", "找人", "寻找", "寻求", "求开发", "找开发", "开发团队", "开发个人", "帮忙做", "需要开发",
  "需要做", "急需", "外包", "有没有人会", "求助", "找谁", "想做", "想搞", "帮忙写", "有没有会",
  "谁能做", "谁会做", "有人能做", "有人接", "能接吗", "接单吗"
];
const PAYMENT = ["有偿", "预算", "多少钱", "报价"];
const NEGATIVE = ["教程", "怎么学", "学习路线", "推荐课程", "课程推荐", "想学", "零基础", "入门", "找工作", "面试", "源码分享", "难吗"];
const SERVICE_OBJECT = String.raw`(?:小程序|微信小程序|网站|网页|官网|管理系统|系统|ai智能体|智能体|自动化|软件开发|独立站|h5|python|爬虫|脚本)`;
const DIRECT_BUYER_PATTERNS = [
  /(?:寻找|寻求|找|求).{0,12}(?:开发|程序员|开发者|技术团队|开发团队)/,
  /(?:有没有|有没).{0,10}(?:会|能).{0,8}(?:做|开发|写|搭建)/,
  new RegExp(`(?:需要|想要|准备|打算|想).{0,10}(?:做|开发|搭建|制作|写).{0,16}${SERVICE_OBJECT}`),
  new RegExp(`(?:急需|需要|求|想要|有偿|外包).{0,24}${SERVICE_OBJECT}`),
  new RegExp(`${SERVICE_OBJECT}.{0,20}(?:有偿|外包|找人|求助|急需|谁会|谁能|有人接|接单吗|能接吗)`),
  new RegExp(`(?:预算|报价).{0,12}(?:\\d+|[一二三四五六七八九十百千万]+|可聊|面议).{0,24}${SERVICE_OBJECT}`),
  new RegExp(`${SERVICE_OBJECT}.{0,24}(?:预算|报价).{0,12}(?:\\d+|[一二三四五六七八九十百千万]+|可聊|面议)`),
  new RegExp(`(?:多少钱|怎么报价|报价多少).{0,20}${SERVICE_OBJECT}`)
];
const HARD_NEGATIVE_PATTERNS = [
  ["招聘/实习", /(?:找实习|实习生|招聘|诚聘|急招|校招|社招|投简历|简历投递|开发岗|岗位职责|薪资待遇)/],
  ["内容推荐", /(?:网站推荐|学习网站|免费学习资源|建议收藏|推荐网站|必备.{0,8}网站|年度.{0,8}(?:最伟大|发现|发明)|本年度.{0,8}(?:最伟大|发现|发明)|用了几个)/],
  ["服务商接单", /(?:^|[\s#｜|])(?:ui\s*)?接单[｜|:：]|^承接.{0,20}(?:网站|网页|小程序|开发|设计)|接单案例/]
];

function cors(origin = "") {
  return {
    "Access-Control-Allow-Origin": ALLOWED.has(origin) ? origin : "https://smirel.com",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
    "Vary": "Origin"
  };
}
function json(data, status = 200, origin = "") {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json; charset=utf-8", ...cors(origin) } });
}
function restHeaders(extra = {}) {
  const headers = { apikey: SECRET_KEY, "Content-Type": "application/json", ...extra };
  if (SECRET_KEY.startsWith("ey")) headers.Authorization = `Bearer ${SECRET_KEY}`;
  return headers;
}
async function rest(path, init = {}) {
  if (!SB_URL || !SECRET_KEY) throw new Error("Supabase server credentials are unavailable");
  const response = await fetch(`${SB_URL}/rest/v1/${path}`, { ...init, headers: { ...restHeaders(), ...(init.headers || {}) } });
  const text = await response.text();
  if (!response.ok) throw new Error(`db ${response.status}: ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : null;
}
async function sha256(value) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)].map((x) => x.toString(16).padStart(2, "0")).join("");
}
function candidateIdentity(item) {
  if (item.external_id) return `${item.source}|id:${item.external_id}`;
  return `${item.source}|url:${item.url || ""}|title:${item.title.toLowerCase()}`;
}
async function findSeen(item, dedupeKey) {
  if (item.external_id) {
    return await rest(`lead_radar_seen_items?source=eq.${encodeURIComponent(item.source)}&source_id=eq.${encodeURIComponent(item.external_id)}&select=id,disposition,lead_id&limit=1`);
  }
  return await rest(`lead_radar_seen_items?dedupe_key=eq.${dedupeKey}&select=id,disposition,lead_id&limit=1`);
}
async function touchSeen(id) {
  await rest(`lead_radar_seen_items?id=eq.${id}`, {
    method: "PATCH", headers: { Prefer: "return=minimal" }, body: JSON.stringify({ last_seen_at: new Date().toISOString() })
  });
}
async function createSeen(item, dedupeKey) {
  const rows = await rest("lead_radar_seen_items?select=*", {
    method: "POST",
    headers: { Prefer: "return=representation" },
    body: JSON.stringify({ source: item.source, source_id: item.external_id, dedupe_key: dedupeKey, disposition: "seen", metadata: { published_at: item.published_at, url: item.url } })
  });
  return rows?.[0] || null;
}
async function updateSeen(id, disposition, leadId = null) {
  await rest(`lead_radar_seen_items?id=eq.${id}`, {
    method: "PATCH", headers: { Prefer: "return=minimal" }, body: JSON.stringify({ disposition, lead_id: leadId, last_seen_at: new Date().toISOString() })
  });
}
function safeUrl(value) {
  if (!value) return null;
  try { const url = new URL(String(value)); return ["http:", "https:"].includes(url.protocol) ? url.href : null; }
  catch { return null; }
}
function normalizedItem(input) {
  const title = String(input?.title || "").trim().slice(0, 240);
  if (!title) return null;
  const published = new Date(input?.published_at || Date.now());
  return {
    source: String(input?.source || "manual").trim().slice(0, 40) || "manual",
    external_id: input?.external_id ? String(input.external_id).slice(0, 160) : null,
    title,
    excerpt: String(input?.excerpt || "").trim().slice(0, 1600),
    url: safeUrl(input?.url),
    published_at: Number.isFinite(published.getTime()) ? published.toISOString() : new Date().toISOString(),
    budget: input?.budget ? String(input.budget).trim().slice(0, 100) : null
  };
}
function freshnessScore(publishedAt) {
  const hours = Math.max(0, (Date.now() - new Date(publishedAt).getTime()) / 36e5);
  if (hours <= 0.5) return 100;
  if (hours <= 2) return 94;
  if (hours <= 6) return 86;
  if (hours <= 24) return 72;
  if (hours <= 72) return 52;
  if (hours <= 168) return 35;
  return 15;
}
function prefilter(item) {
  const text = `${item.title} ${item.excerpt}`.toLowerCase();
  const serviceHits = Object.entries(SERVICES).filter(([, words]) => words.some((word) => text.includes(word))).map(([name]) => name);
  const intentHits = Object.keys(INTENT).filter((word) => text.includes(word));
  const softNegativeHits = NEGATIVE.filter((word) => text.includes(word));
  if (/学习.{0,12}(?:课程|教程|路线|开发|编程|python|前端|小程序|ai)/.test(text) && !softNegativeHits.includes("学习咨询")) softNegativeHits.push("学习咨询");
  const hardNegativeHits = HARD_NEGATIVE_PATTERNS.filter(([, pattern]) => pattern.test(text)).map(([label]) => label);
  const negativeHits = [...softNegativeHits, ...hardNegativeHits];
  const directBuyer = DIRECT_BUYER_PATTERNS.some((pattern) => pattern.test(text));
  const paid = PAYMENT.some((word) => text.includes(word));
  const strongAction = BUYER_ACTIONS.some((word) => text.includes(word));
  const blocked = Boolean(hardNegativeHits.length) || Boolean(softNegativeHits.length && !(paid && directBuyer));
  const passed = Boolean(serviceHits.length && (directBuyer || strongAction)) && !blocked;
  return { passed, text, serviceHits, intentHits, negativeHits, directBuyer };
}
function ruleClassification(item, pf) {
  let intent = Math.min(100, pf.intentHits.reduce((sum, word) => sum + INTENT[word], 0));
  if (pf.directBuyer) intent = Math.max(intent, 82);
  if (PAYMENT.some((word) => pf.text.includes(word))) intent = Math.min(100, intent + 12);
  if (pf.negativeHits.length && !pf.passed) intent = Math.max(0, intent - 55);
  const fit = pf.serviceHits.length ? 92 : 35;
  const freshness = freshnessScore(item.published_at);
  const urgency = ["急", "今天", "尽快", "马上"].some((word) => pf.text.includes(word)) ? "high" : (["近期", "最近"].some((word) => pf.text.includes(word)) ? "medium" : "low");
  const budgetSignal = item.budget || PAYMENT.some((word) => pf.text.includes(word)) ? 100 : (pf.directBuyer ? 75 : 35);
  let score = Math.round(intent * 0.40 + freshness * 0.30 + fit * 0.20 + budgetSignal * 0.10 + (urgency === "high" ? 10 : urgency === "medium" ? 5 : 0));
  const isLead = pf.passed && score >= 45;
  if (!isLead) score = Math.min(score, 39);
  return {
    need_type: pf.serviceHits[0] || "其他开发",
    is_lead: isLead,
    intent_score: intent,
    fit_score: fit,
    freshness_score: freshness,
    ai_score: Math.max(0, Math.min(100, score)),
    urgency,
    confidence: Math.max(0, Math.min(99, 58 + pf.serviceHits.length * 9 + pf.intentHits.length * 6 + (pf.directBuyer ? 12 : 0) - pf.negativeHits.length * 12)),
    priority: score >= 85 && isLead ? "high" : score >= 65 && isLead ? "medium" : "low",
    budget_text: item.budget,
    reason: [
      pf.serviceHits.length ? `识别为${pf.serviceHits[0]}` : "",
      pf.directBuyer ? "存在直接寻找开发方的表达" : "",
      pf.intentHits.length ? `需求意向信号：${pf.intentHits.slice(0, 4).join("、")}` : "",
      pf.negativeHits.length ? `负向信号：${pf.negativeHits.slice(0, 3).join("、")}` : ""
    ].filter(Boolean).join("；") || "信号不足",
    signals: [...pf.serviceHits, ...(pf.directBuyer ? ["明确找开发方"] : []), ...pf.intentHits, ...pf.negativeHits.map((x) => `排除:${x}`)].slice(0, 8)
  };
}
async function semanticClassification(item, base) {
  if (!OPENAI_KEY || !OPENAI_MODEL) return base.is_lead ? base : null;
  const schema = {
    type: "object",
    properties: {
      is_lead: { type: "boolean" }, need_type: { type: "string" }, intent_score: { type: "integer", minimum: 0, maximum: 100 },
      fit_score: { type: "integer", minimum: 0, maximum: 100 }, urgency: { type: "string", enum: ["low", "medium", "high"] },
      budget_text: { type: ["string", "null"] }, reason: { type: "string" }, confidence: { type: "integer", minimum: 0, maximum: 100 },
      signals: { type: "array", items: { type: "string" }, maxItems: 8 }
    },
    required: ["is_lead", "need_type", "intent_score", "fit_score", "urgency", "budget_text", "reason", "confidence", "signals"],
    additionalProperties: false
  };
  const payload = {
    model: OPENAI_MODEL,
    store: false,
    instructions: "你是开发外包需求分类器。高精度优先。只有明确寻找开发方、要求实现产品、询价或外包的帖子才是 Lead。招聘/实习、教程/学习资源、网站推荐/收藏清单、服务商自我接单宣传、泛讨论必须判为 false。像‘寻找AI智能体开发团队或个人’、‘找人做网站’、‘急！需要icp+edi，小程序，知识付费，在线交易’属于强购买意图。不要提取或推断个人敏感信息。",
    input: [{ role: "user", content: [{ type: "input_text", text: `请输出符合 schema 的 JSON。\n标题：${item.title}\n正文摘要：${item.excerpt}\n已知预算：${item.budget || "未公开"}` }] }],
    text: { format: { type: "json_schema", name: "lead_classification", strict: true, schema } }
  };
  try {
    const response = await fetch("https://api.openai.com/v1/responses", { method: "POST", headers: { Authorization: `Bearer ${OPENAI_KEY}`, "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!response.ok) throw new Error(`OpenAI ${response.status}`);
    const data = await response.json();
    const outputText = data.output_text || (data.output || []).flatMap((part) => part.content || []).filter((part) => part.type === "output_text").map((part) => part.text || "").join("");
    if (!outputText) throw new Error("OpenAI returned no structured text");
    const ai = JSON.parse(outputText);
    if (!ai.is_lead) return null;
    const freshness = base.freshness_score;
    const budgetText = ai.budget_text || item.budget || null;
    const budgetSignal = budgetText ? 100 : (base.signals?.includes("明确找开发方") ? 75 : 35);
    const score = Math.max(0, Math.min(100, Math.round(ai.intent_score * 0.40 + freshness * 0.30 + ai.fit_score * 0.20 + budgetSignal * 0.10 + (ai.urgency === "high" ? 10 : ai.urgency === "medium" ? 5 : 0))));
    return {
      need_type: String(ai.need_type || base.need_type).slice(0, 80), is_lead: true, intent_score: Number(ai.intent_score), fit_score: Number(ai.fit_score), freshness_score: freshness,
      ai_score: score, urgency: ai.urgency, confidence: Number(ai.confidence), priority: score >= 85 ? "high" : score >= 65 ? "medium" : "low",
      budget_text: budgetText, reason: String(ai.reason || base.reason).slice(0, 700),
      signals: [...new Set([...(base.signals || []), ...(Array.isArray(ai.signals) ? ai.signals : [])])].slice(0, 8)
    };
  } catch (error) {
    console.warn("semantic classifier fallback", String(error));
    return base.is_lead ? base : null;
  }
}
async function analyze(item) {
  const pf = prefilter(item);
  if (!pf.passed) return null;
  return semanticClassification(item, ruleClassification(item, pf));
}
function out(row) {
  return { id: row.id, source: row.source, external_id: row.source_id, title: row.title, excerpt: row.excerpt, category: row.need_type, score: row.ai_score, is_lead: row.is_lead, intent_score: row.intent_score, fit_score: row.fit_score, freshness_score: row.freshness_score, urgency: row.urgency, confidence: row.confidence, priority: row.priority, published_at: row.published_at, discovered_at: row.discovered_at, budget: row.budget_text, reason: row.reason, status: row.status, url: row.url, signals: row.signals, created_at: row.created_at, updated_at: row.updated_at };
}
async function notifyHighLead(row) {
  if (!FEISHU_WEBHOOK_URL || Number(row.ai_score || 0) < NOTIFY_MIN_SCORE) return false;
  const text = `【AI Lead Radar】\n来源：${row.source}\n时间：${row.published_at}\n类型：${row.need_type}\nAI Score：${row.ai_score}\n需求：${row.title}\n链接：${row.url || "未提供"}`;
  try {
    const response = await fetch(FEISHU_WEBHOOK_URL, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ msg_type: "text", content: { text } }) });
    return response.ok;
  } catch (error) {
    console.warn("notification failed", String(error));
    return false;
  }
}

Deno.serve(async (req) => {
  const origin = req.headers.get("origin") || "";
  const method = req.method.toUpperCase();
  if (method === "OPTIONS") return new Response(null, { status: 204, headers: cors(origin) });
  if (origin && !ALLOWED.has(origin)) return json({ detail: "Origin not allowed" }, 403, origin);
  try {
    const url = new URL(req.url);
    const path = url.pathname.replace(/^.*\/lead-radar-api/, "") || "/";
    if (method === "GET" && path === "/health") {
      return json({ ok: true, service: "lead-radar-api", version: "0.8-edge", rules_version: "precision-v2", timestamp: new Date().toISOString(), ai_provider: OPENAI_KEY && OPENAI_MODEL ? "openai" : "rules" }, 200, origin);
    }
    if (method === "GET" && path === "/api/v1/leads") {
      const min = Math.max(0, Math.min(100, Number(url.searchParams.get("min_score") || 0)));
      const status = url.searchParams.get("status");
      const limit = Math.max(1, Math.min(500, Number(url.searchParams.get("limit") || 100)));
      let query = `lead_radar_leads?select=*&is_lead=eq.true&ai_score=gte.${min}&order=ai_score.desc,published_at.desc&limit=${limit}`;
      if (status) query += `&status=eq.${encodeURIComponent(status)}`;
      return json((await rest(query)).map(out), 200, origin);
    }
    const statusMatch = path.match(/^\/api\/v1\/leads\/(\d+)\/status$/);
    if (method === "PATCH" && statusMatch) {
      if (!ALLOWED.has(origin)) return json({ detail: "Write origin required" }, 403, origin);
      const body = await req.json();
      if (!["new", "saved", "contacted", "ignored"].includes(body.status)) return json({ detail: "Invalid status" }, 422, origin);
      const rows = await rest(`lead_radar_leads?id=eq.${statusMatch[1]}&select=*`, { method: "PATCH", headers: { Prefer: "return=representation" }, body: JSON.stringify({ status: body.status, updated_at: new Date().toISOString() }) });
      if (!rows?.length) return json({ detail: "Lead not found" }, 404, origin);
      return json(out(rows[0]), 200, origin);
    }
    if (method === "POST" && path === "/api/v1/ingest/manual") {
      if (!ALLOWED.has(origin)) return json({ detail: "Write origin required" }, 403, origin);
      const body = await req.json();
      const rawItems = Array.isArray(body.items) ? body.items.slice(0, 200) : [];
      if (!rawItems.length) return json({ detail: "items required" }, 422, origin);
      let stored = 0, filtered = 0, duplicates = 0, notified = 0;
      const leadIds = [];
      for (const raw of rawItems) {
        const item = normalizedItem(raw);
        if (!item) { filtered += 1; continue; }
        const dedupeKey = await sha256(candidateIdentity(item));
        let seenRows = await findSeen(item, dedupeKey);
        let seen = seenRows?.[0] || null;
        if (seen && seen.disposition !== "error") {
          duplicates += 1;
          await touchSeen(seen.id);
          if (seen.lead_id) leadIds.push(seen.lead_id);
          continue;
        }
        const existing = await rest(item.external_id
          ? `lead_radar_leads?source=eq.${encodeURIComponent(item.source)}&source_id=eq.${encodeURIComponent(item.external_id)}&select=id,status&limit=1`
          : `lead_radar_leads?dedupe_key=eq.${dedupeKey}&select=id,status&limit=1`);
        if (existing?.length) {
          if (!seen) seen = await createSeen(item, dedupeKey);
          if (seen) await updateSeen(seen.id, "stored", existing[0].id);
          duplicates += 1;
          leadIds.push(existing[0].id);
          continue;
        }
        if (!seen) seen = await createSeen(item, dedupeKey);
        else await updateSeen(seen.id, "seen", null);
        if (!seen) throw new Error("Failed to create candidate seen record");
        try {
          const analysis = await analyze(item);
          if (!analysis) {
            filtered += 1;
            await updateSeen(seen.id, "filtered", null);
            continue;
          }
          const payload = { source: item.source, source_id: item.external_id, title: item.title, excerpt: item.excerpt, url: item.url, published_at: item.published_at, discovered_at: new Date().toISOString(), dedupe_key: dedupeKey, ...analysis, updated_at: new Date().toISOString() };
          const rows = await rest("lead_radar_leads?select=*", { method: "POST", headers: { Prefer: "return=representation" }, body: JSON.stringify(payload) });
          if (rows?.[0]) {
            stored += 1;
            leadIds.push(rows[0].id);
            await updateSeen(seen.id, "stored", rows[0].id);
            if (await notifyHighLead(rows[0])) notified += 1;
          } else {
            await updateSeen(seen.id, "error", null);
          }
        } catch (error) {
          await updateSeen(seen.id, "error", null);
          throw error;
        }
      }
      return json({ ok: true, received: rawItems.length, stored, filtered, duplicates, notified, lead_ids: leadIds }, 200, origin);
    }
    if (method === "GET" && path === "/api/v1/monitor/status") {
      const runs = await rest("lead_radar_scan_runs?select=*&order=started_at.desc&limit=1");
      return json({ running: false, mode: "production-source-collector", platforms: ["justone-xiaohongshu-v4", "manual", "browser-helper"], last_scan_at: runs?.[0]?.finished_at || null, ai_provider: OPENAI_KEY && OPENAI_MODEL ? "openai" : "rules", notification_enabled: Boolean(FEISHU_WEBHOOK_URL), note: "Xiaohongshu source collection is authenticated with GitHub Actions OIDC; every candidate is deduped before semantic AI and hard-negative precision filters remove recruiting/content/self-promo noise." }, 200, origin);
    }
    if (method === "POST" && path === "/api/v1/monitor/scan") {
      if (!ALLOWED.has(origin)) return json({ detail: "Write origin required" }, 403, origin);
      const now = new Date().toISOString();
      return json({ ok: true, connector: "justone-xiaohongshu-v4", scanned: 0, stored: 0, filtered: 0, high_intent: 0, last_scan_at: now, note: "Production source collection is schedule-driven to control provider quota; use the collector workflow for a controlled scan." }, 200, origin);
    }
    return json({ detail: "Not found" }, 404, origin);
  } catch (error) {
    console.error(error);
    return json({ detail: "Internal API error" }, 500, origin);
  }
});
