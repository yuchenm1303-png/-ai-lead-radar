const ALLOWED = new Set(["https://smirel.com", "http://localhost:3000"]);
const SB_URL = Deno.env.get("SUPABASE_URL") || "";
const LEGACY_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
let SECRET_KEY = LEGACY_KEY;
try {
  const keys = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}");
  SECRET_KEY = keys.default || SECRET_KEY;
} catch {}

function cors(origin = "") {
  return {
    "Access-Control-Allow-Origin": ALLOWED.has(origin) ? origin : "https://smirel.com",
    "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
    "Vary": "Origin",
  };
}

function json(data: unknown, status = 200, origin = "") {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...cors(origin) },
  });
}

function restHeaders(extra: Record<string, string> = {}) {
  const headers: Record<string, string> = {
    apikey: SECRET_KEY,
    "Content-Type": "application/json",
    ...extra,
  };
  if (SECRET_KEY.startsWith("ey")) headers.Authorization = `Bearer ${SECRET_KEY}`;
  return headers;
}

async function rest(path: string, init: RequestInit = {}) {
  if (!SB_URL || !SECRET_KEY) throw new Error("Supabase server credentials are unavailable");
  const response = await fetch(`${SB_URL}/rest/v1/${path}`, {
    ...init,
    headers: { ...restHeaders(), ...((init.headers as Record<string, string>) || {}) },
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`db ${response.status}: ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : null;
}

function out(row: any) {
  return {
    id: row.id,
    source: row.source,
    external_id: row.source_id,
    title: row.title,
    excerpt: row.excerpt,
    category: row.need_type,
    score: row.ai_score,
    is_lead: row.is_lead,
    intent_score: row.intent_score,
    fit_score: row.fit_score,
    freshness_score: row.freshness_score,
    actionability_score: row.actionability_score ?? 0,
    actor_role: row.actor_role ?? "unknown",
    buying_stage: row.buying_stage ?? "none",
    policy_version: row.policy_version ?? "legacy",
    urgency: row.urgency,
    confidence: row.confidence,
    priority: row.priority,
    published_at: row.published_at,
    discovered_at: row.discovered_at,
    budget: row.budget_text,
    reason: row.reason,
    status: row.status,
    url: row.url,
    signals: row.signals,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

async function delegateIngest(items: unknown[]) {
  if (!SB_URL || !SECRET_KEY) throw new Error("Supabase server credentials are unavailable");
  const response = await fetch(`${SB_URL}/functions/v1/lead-radar-ingest/api/v1/ingest`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      apikey: SECRET_KEY,
      Authorization: `Bearer ${SECRET_KEY}`,
    },
    body: JSON.stringify({ items, query_context: { query_key: "manual", query_text: "manual", intent_family: "manual", topic_family: "manual" } }),
    signal: AbortSignal.timeout(30000),
  });
  const text = await response.text();
  let payload: any = {};
  try { payload = text ? JSON.parse(text) : {}; }
  catch { payload = { detail: text.slice(0, 300) }; }
  if (!response.ok) throw new Error(`lead-radar-ingest ${response.status}: ${String(payload.detail || text).slice(0, 300)}`);
  return payload;
}

Deno.serve(async (req: Request) => {
  const origin = req.headers.get("origin") || "";
  const method = req.method.toUpperCase();
  if (method === "OPTIONS") return new Response(null, { status: 204, headers: cors(origin) });
  if (origin && !ALLOWED.has(origin)) return json({ detail: "Origin not allowed" }, 403, origin);

  try {
    const url = new URL(req.url);
    const path = url.pathname.replace(/^.*\/lead-radar-api/, "") || "/";

    if (method === "GET" && path === "/health") {
      return json({
        ok: true,
        service: "lead-radar-api",
        version: "1.0-gateway",
        ingest_service: "lead-radar-ingest",
        classifier: "canonical-policy",
        timestamp: new Date().toISOString(),
        ai_provider: "rules",
      }, 200, origin);
    }

    if (method === "GET" && path === "/api/v1/leads") {
      const min = Math.max(0, Math.min(100, Number(url.searchParams.get("min_score") || 0)));
      const status = url.searchParams.get("status");
      const limit = Math.max(1, Math.min(500, Number(url.searchParams.get("limit") || 100)));
      let query = `lead_radar_leads?select=*&is_lead=eq.true&ai_score=gte.${min}&order=ai_score.desc,published_at.desc&limit=${limit}`;
      if (status) query += `&status=eq.${encodeURIComponent(status)}`;
      const rows = await rest(query);
      return json((Array.isArray(rows) ? rows : []).map(out), 200, origin);
    }

    const statusMatch = path.match(/^\/api\/v1\/leads\/(\d+)\/status$/);
    if (method === "PATCH" && statusMatch) {
      if (!ALLOWED.has(origin)) return json({ detail: "Write origin required" }, 403, origin);
      const body = await req.json();
      if (!["new", "saved", "contacted", "ignored"].includes(body?.status)) return json({ detail: "Invalid status" }, 422, origin);
      const rows = await rest(`lead_radar_leads?id=eq.${statusMatch[1]}&select=*`, {
        method: "PATCH",
        headers: { Prefer: "return=representation" },
        body: JSON.stringify({ status: body.status, updated_at: new Date().toISOString() }),
      });
      if (!rows?.length) return json({ detail: "Lead not found" }, 404, origin);
      return json(out(rows[0]), 200, origin);
    }

    if (method === "POST" && path === "/api/v1/ingest/manual") {
      if (!ALLOWED.has(origin)) return json({ detail: "Write origin required" }, 403, origin);
      const body = await req.json();
      const items = Array.isArray(body?.items) ? body.items.slice(0, 100) : [];
      if (!items.length) return json({ detail: "items required" }, 422, origin);
      return json(await delegateIngest(items), 200, origin);
    }

    if (method === "GET" && path === "/api/v1/monitor/status") {
      const runs = await rest("lead_radar_scan_runs?select=*&order=started_at.desc&limit=1");
      return json({
        running: false,
        mode: "policy-driven-production",
        platforms: ["justone-xiaohongshu-v4", "manual", "browser-helper"],
        last_scan_at: runs?.[0]?.finished_at || null,
        ai_provider: "rules",
        notification_enabled: Boolean(Deno.env.get("FEISHU_WEBHOOK_URL") || ""),
        ingest_service: "lead-radar-ingest",
        note: "All candidate ingestion paths delegate to the canonical policy service. Source collection and classification are separate components.",
      }, 200, origin);
    }

    if (method === "POST" && path === "/api/v1/monitor/scan") {
      if (!ALLOWED.has(origin)) return json({ detail: "Write origin required" }, 403, origin);
      return json({
        ok: true,
        deprecated: true,
        scanned: 0,
        stored: 0,
        filtered: 0,
        high_intent: 0,
        note: "Use lead-radar-scan /api/v1/request for controlled provider scanning.",
      }, 200, origin);
    }

    return json({ detail: "Not found" }, 404, origin);
  } catch (error) {
    console.error(error);
    return json({ detail: "Internal API error" }, 500, origin);
  }
});
