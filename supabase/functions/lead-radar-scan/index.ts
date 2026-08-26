const ALLOWED = new Set(["https://smirel.com", "http://localhost:3000"]);
const SB_URL = Deno.env.get("SUPABASE_URL") || "";
const LEGACY_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
let SECRET_KEY = LEGACY_KEY;
try {
  const keys = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}");
  SECRET_KEY = keys.default || SECRET_KEY;
} catch {}

const JUSTONE_TOKEN = (Deno.env.get("JUSTONE_API_TOKEN") || "").trim();
const JUSTONE_ENDPOINT = (Deno.env.get("JUSTONE_API_ENDPOINT") || "https://api.justoneapi.com/api/xiaohongshu/search-note/v4").trim();
const MAX_REQUESTS_PER_HOUR = 3;
const MIN_REQUEST_GAP_MS = 5 * 60 * 1000;
const MAX_AGE_MINUTES = 24 * 60;
const MAX_PREVIEW_BODY_CHARS = 12000;
const MAX_PREVIEW_IMAGES = 9;
const QUERY_ROTATION = ["小程序", "小程序", "小程序", "网站", "网站", "管理系统", "AI智能体", "软件开发", "自动化"];

function cors(origin = "") {
  return {
    "Access-Control-Allow-Origin": ALLOWED.has(origin) ? origin : "https://smirel.com",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
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
  const headers: Record<string, string> = { apikey: SECRET_KEY, "Content-Type": "application/json", ...extra };
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

function publicRequest(row: any) {
  if (!row) return null;
  return {
    id: Number(row.id),
    status: String(row.status || "queued"),
    requested_at: row.requested_at || null,
    started_at: row.started_at || null,
    finished_at: row.finished_at || null,
    result: row.result && typeof row.result === "object" ? row.result : {},
    error: row.status === "failed" ? String(row.error_text || "扫描失败") : null,
  };
}

async function latestRequests(limit = 10) {
  return await rest(`lead_radar_scan_requests?select=id,status,requested_at,started_at,finished_at,query_override,max_queries,provider,result,error_text&order=requested_at.desc&limit=${limit}`);
}

function requestWindow(requests: any[]) {
  const now = Date.now();
  const recent = requests.filter((row: any) => {
    const ts = new Date(row?.requested_at || 0).getTime();
    return Number.isFinite(ts) && ts >= now - 60 * 60 * 1000 && String(row?.status || "") !== "cancelled";
  });
  const latest = recent[0] || null;
  const latestAt = latest ? new Date(latest.requested_at || 0).getTime() : 0;
  const cooldownMs = latestAt ? Math.max(0, MIN_REQUEST_GAP_MS - (now - latestAt)) : 0;
  return {
    recent,
    recentCount: recent.length,
    cooldownSeconds: Math.ceil(cooldownMs / 1000),
    nextAvailableAt: cooldownMs > 0 ? new Date(now + cooldownMs).toISOString() : null,
  };
}

function chooseKeyword(now = new Date()) {
  const bucket = Math.floor(now.getTime() / (15 * 60 * 1000));
  return QUERY_ROTATION[bucket % QUERY_ROTATION.length];
}

function parseTimestamp(value: unknown): Date | null {
  if (value === null || value === undefined || typeof value === "boolean") return null;
  let number: number | null = null;
  if (typeof value === "number") number = value;
  else if (typeof value === "string") {
    const text = value.trim();
    if (!text) return null;
    const numeric = Number(text);
    if (Number.isFinite(numeric)) number = numeric;
    else {
      const parsed = new Date(text);
      return Number.isFinite(parsed.getTime()) ? parsed : null;
    }
  }
  if (number === null || !Number.isFinite(number)) return null;
  if (number > 1e12) number /= 1000;
  if (number < 1_000_000_000 || number > 4_000_000_000) return null;
  const parsed = new Date(number * 1000);
  return Number.isFinite(parsed.getTime()) ? parsed : null;
}

function safeHttpUrl(value: unknown): string | null {
  if (!value) return null;
  try {
    const url = new URL(String(value));
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function firstText(object: any, keys: string[]): string {
  if (!object || typeof object !== "object") return "";
  for (const key of keys) {
    const value = object[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function numericMetric(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return Math.max(0, Math.round(value));
  if (typeof value === "string") {
    const parsed = Number(value.replace(/,/g, "").trim());
    return Number.isFinite(parsed) ? Math.max(0, Math.round(parsed)) : null;
  }
  return null;
}

function collectImageUrls(value: unknown, output = new Set<string>(), depth = 0): string[] {
  if (output.size >= MAX_PREVIEW_IMAGES || depth > 4 || value === null || value === undefined) return [...output];
  if (typeof value === "string") {
    const url = safeHttpUrl(value);
    if (url) output.add(url);
    return [...output];
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      collectImageUrls(item, output, depth + 1);
      if (output.size >= MAX_PREVIEW_IMAGES) break;
    }
    return [...output];
  }
  if (typeof value === "object") {
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (!/(url|image|original|default|preview|large|small)/i.test(key)) continue;
      collectImageUrls(item, output, depth + 1);
      if (output.size >= MAX_PREVIEW_IMAGES) break;
    }
  }
  return [...output];
}

function extractTags(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const result: string[] = [];
  for (const item of value) {
    const text = typeof item === "string" ? item.trim() : firstText(item, ["name", "title", "tag_name", "tagName"]);
    if (text && !result.includes(text)) result.push(text.slice(0, 80));
    if (result.length >= 12) break;
  }
  return result;
}

function buildPreview(note: any, id: string, title: string, body: string, published: Date) {
  const user = note?.user && typeof note.user === "object" ? note.user : {};
  const nickname = firstText(user, ["nickname", "nick_name", "name", "user_name", "userName"]);
  const avatar = safeHttpUrl(firstText(user, ["avatar", "avatar_url", "avatarUrl", "image"]));
  return {
    id,
    source: "小红书",
    title: (title || body.slice(0, 120)).slice(0, 240),
    body: body.slice(0, MAX_PREVIEW_BODY_CHARS),
    published_at: published.toISOString(),
    url: `https://www.xiaohongshu.com/explore/${id}`,
    author: nickname || avatar ? { nickname: nickname.slice(0, 100), avatar } : null,
    images: collectImageUrls(note?.images_list ?? note?.images ?? note?.image_list).slice(0, MAX_PREVIEW_IMAGES),
    metrics: {
      likes: numericMetric(note?.liked_count ?? note?.likes_count ?? note?.like_count),
      comments: numericMetric(note?.comments_count ?? note?.comment_count),
      collects: numericMetric(note?.collected_count ?? note?.collect_count),
      shares: numericMetric(note?.shared_count ?? note?.share_count),
    },
    tags: extractTags(note?.tags),
  };
}

async function fetchJustOne(keyword: string) {
  if (!JUSTONE_TOKEN) throw new Error("JUSTONE_API_TOKEN is not configured");
  const url = new URL(JUSTONE_ENDPOINT);
  url.searchParams.set("token", JUSTONE_TOKEN);
  url.searchParams.set("keyword", keyword);
  url.searchParams.set("page", "1");
  url.searchParams.set("sortType", "time_descending");
  url.searchParams.set("noteType", "ALL");
  url.searchParams.set("timeFilter", "ALL");

  const response = await fetch(url, {
    headers: { Accept: "application/json", "User-Agent": "AI-Lead-Radar/1.0" },
    signal: AbortSignal.timeout(20000),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`Just One HTTP ${response.status}: ${text.slice(0, 200)}`);
  let payload: any;
  try { payload = JSON.parse(text); }
  catch { throw new Error("Just One returned invalid JSON"); }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("Just One returned a non-object response");
  if (payload.code !== 0) throw new Error(`Just One business code ${String(payload.code)}: ${String(payload.message || payload.msg || "business error").slice(0, 180)}`);

  const notes = Array.isArray(payload?.data?.notes) ? payload.data.notes : [];
  const now = Date.now();
  const unique = new Map<string, { item: any; preview: any }>();
  for (const note of notes) {
    if (!note || typeof note !== "object" || Array.isArray(note)) continue;
    const id = String(note.id || "").trim();
    const title = String(note.title || "").trim();
    const body = String(note.desc || "").trim();
    const published = parseTimestamp(note.timestamp);
    if (!id || !published || (!title && !body)) continue;
    const ageMinutes = (now - published.getTime()) / 60000;
    if (ageMinutes < -5 || ageMinutes > MAX_AGE_MINUTES || unique.has(id)) continue;
    const preview = buildPreview(note, id, title, body, published);
    unique.set(id, {
      item: {
        source: "小红书",
        external_id: id.slice(0, 160),
        title: preview.title,
        excerpt: body.slice(0, 1600),
        published_at: published.toISOString(),
        url: preview.url,
        budget: null,
      },
      preview,
    });
  }
  return {
    keyword,
    raw_count: notes.length,
    fresh_count: unique.size,
    request_id: payload.requestId ? String(payload.requestId).slice(0, 160) : null,
    items: [...unique.values()].map((entry) => entry.item),
    previews: [...unique.values()].map((entry) => entry.preview),
  };
}

async function ingestItems(items: any[]) {
  if (!items.length) return { received: 0, stored: 0, filtered: 0, duplicates: 0, notified: 0, lead_ids: [] };
  const response = await fetch(`${SB_URL}/functions/v1/lead-radar-api/api/v1/ingest/manual`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json", Origin: "https://smirel.com" },
    body: JSON.stringify({ items }),
    signal: AbortSignal.timeout(30000),
  });
  const text = await response.text();
  let payload: any = {};
  try { payload = text ? JSON.parse(text) : {}; }
  catch { payload = { detail: text.slice(0, 200) }; }
  if (!response.ok) throw new Error(`lead-radar-api ${response.status}: ${String(payload.detail || text).slice(0, 220)}`);
  return payload;
}

async function candidateDecisions(items: any[]) {
  const ids = items.map((item) => String(item?.external_id || "").trim()).filter(Boolean).slice(0, 100);
  if (!ids.length) return new Map<string, { disposition: string; lead_id: number | null }>();
  const rows = await rest(`lead_radar_seen_items?source=eq.${encodeURIComponent("小红书")}&source_id=in.(${ids.join(",")})&select=source_id,disposition,lead_id`);
  const result = new Map<string, { disposition: string; lead_id: number | null }>();
  for (const row of Array.isArray(rows) ? rows : []) {
    const sourceId = String(row?.source_id || "");
    if (!sourceId) continue;
    result.set(sourceId, {
      disposition: String(row?.disposition || "seen"),
      lead_id: Number.isInteger(Number(row?.lead_id)) ? Number(row.lead_id) : null,
    });
  }
  return result;
}

async function highIntentCount(leadIds: number[]) {
  if (!leadIds.length) return 0;
  const rows = await rest(`lead_radar_leads?id=in.(${leadIds.join(",")})&select=id,ai_score`);
  return Array.isArray(rows) ? rows.filter((row: any) => Number(row?.ai_score || 0) >= 80).length : 0;
}

async function updateRequest(id: number, values: Record<string, unknown>) {
  await rest(`lead_radar_scan_requests?id=eq.${id}`, {
    method: "PATCH",
    headers: { Prefer: "return=minimal" },
    body: JSON.stringify({ ...values, updated_at: new Date().toISOString() }),
  });
}

async function recordRun(payload: Record<string, unknown>) {
  try {
    await rest("lead_radar_scan_runs?select=*", {
      method: "POST",
      headers: { Prefer: "return=minimal" },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    console.warn("scan audit insert failed", String(error));
  }
}

async function executeDirectScan(requestRow: any) {
  const id = Number(requestRow?.id || 0);
  if (!Number.isInteger(id) || id <= 0) throw new Error("invalid scan request");
  const startedAt = new Date().toISOString();
  await updateRequest(id, { status: "running", started_at: startedAt, error_text: null });
  const keyword = String(requestRow?.query_override || "").trim() || chooseKeyword(new Date());
  try {
    const source = await fetchJustOne(keyword);
    const ingest = await ingestItems(source.items);
    const decisions = await candidateDecisions(source.items);
    const leadIds = Array.isArray(ingest?.lead_ids)
      ? ingest.lead_ids.map((value: unknown) => Number(value)).filter((value: number) => Number.isInteger(value) && value > 0).slice(0, 200)
      : [];
    const highIntent = await highIntentCount(leadIds);
    const posts = source.previews.map((preview: any) => {
      const decision = decisions.get(String(preview.id)) || { disposition: "unknown", lead_id: null };
      return { ...preview, decision: decision.disposition, lead_id: decision.lead_id };
    });
    const finishedAt = new Date().toISOString();
    const result = {
      connector: "justone-xiaohongshu-v4",
      keyword,
      scanned: source.raw_count,
      fresh: source.fresh_count,
      stored: Number(ingest?.stored || 0),
      filtered: Number(ingest?.filtered || 0),
      duplicates: Number(ingest?.duplicates || 0),
      notified: Number(ingest?.notified || 0),
      high_intent: highIntent,
      lead_ids: leadIds,
      request_id: source.request_id,
      posts,
      last_scan_at: finishedAt,
    };
    await updateRequest(id, { status: "success", finished_at: finishedAt, result, error_text: null });
    await recordRun({
      connector: "justone-xiaohongshu-v4",
      started_at: startedAt,
      finished_at: finishedAt,
      scanned: source.raw_count,
      stored: result.stored,
      filtered: result.filtered,
      high_intent: highIntent,
      status: "success",
      error_text: null,
      details: {
        mode: "direct-edge",
        scan_request_id: id,
        keyword,
        fresh: source.fresh_count,
        preview_count: posts.length,
        duplicates: result.duplicates,
        notified: result.notified,
        provider_request_id: source.request_id,
      },
    });
    return result;
  } catch (error) {
    const message = String(error instanceof Error ? error.message : error).slice(0, 600);
    const finishedAt = new Date().toISOString();
    await updateRequest(id, { status: "failed", finished_at: finishedAt, result: { mode: "direct-edge", keyword }, error_text: message });
    await recordRun({
      connector: "justone-xiaohongshu-v4",
      started_at: startedAt,
      finished_at: finishedAt,
      scanned: 0,
      stored: 0,
      filtered: 0,
      high_intent: 0,
      status: "failed",
      error_text: message,
      details: { mode: "direct-edge", scan_request_id: id, keyword },
    });
    throw new Error(message);
  }
}

async function statusPayload() {
  const [runs, requestRows] = await Promise.all([
    rest("lead_radar_scan_runs?select=id,connector,started_at,finished_at,scanned,stored,filtered,high_intent,status,error_text,details&order=started_at.desc&limit=1"),
    latestRequests(20),
  ]);
  const requests = Array.isArray(requestRows) ? requestRows : [];
  const latestRun = Array.isArray(runs) ? runs[0] : null;
  const latestRequest = requests[0] || null;
  const active = requests.find((row: any) => ["queued", "running"].includes(String(row?.status || ""))) || null;
  const windowState = requestWindow(requests);
  const queueAvailable = !active && windowState.recentCount < MAX_REQUESTS_PER_HOUR && windowState.cooldownSeconds === 0;
  return {
    ok: true,
    service: "lead-radar-scan",
    version: "0.4-post-preview",
    mode: JUSTONE_TOKEN ? "direct-edge" : "web-queued-github-worker",
    direct_ready: Boolean(JUSTONE_TOKEN),
    platform: "小红书 · Just One V4",
    worker_interval_minutes: JUSTONE_TOKEN ? 0 : 5,
    running: String(active?.status || "") === "running",
    queued: String(active?.status || "") === "queued",
    active_request: publicRequest(active),
    latest_request: publicRequest(latestRequest),
    last_scan_at: latestRun?.finished_at || null,
    last_scan: latestRun ? {
      status: latestRun.status,
      scanned: Number(latestRun.scanned || 0),
      stored: Number(latestRun.stored || 0),
      filtered: Number(latestRun.filtered || 0),
      high_intent: Number(latestRun.high_intent || 0),
      connector: latestRun.connector || "justone-xiaohongshu-v4",
    } : null,
    queue_available: queueAvailable,
    cooldown_seconds: windowState.cooldownSeconds,
    next_available_at: windowState.nextAvailableAt,
    requests_last_hour: windowState.recentCount,
    request_limit_per_hour: MAX_REQUESTS_PER_HOUR,
  };
}

Deno.serve(async (req: Request) => {
  const origin = req.headers.get("origin") || "";
  const method = req.method.toUpperCase();
  if (method === "OPTIONS") return new Response(null, { status: 204, headers: cors(origin) });
  if (origin && !ALLOWED.has(origin)) return json({ detail: "Origin not allowed" }, 403, origin);

  try {
    const url = new URL(req.url);
    const path = url.pathname.replace(/^.*\/lead-radar-scan/, "") || "/";

    if (method === "GET" && (path === "/health" || path === "/api/v1/status")) {
      return json(await statusPayload(), 200, origin);
    }

    if (method === "POST" && path === "/api/v1/request") {
      if (!ALLOWED.has(origin)) return json({ detail: "Write origin required" }, 403, origin);
      const requestRows = await latestRequests(20);
      const requests = Array.isArray(requestRows) ? requestRows : [];
      const active = requests.find((row: any) => ["queued", "running"].includes(String(row?.status || ""))) || null;
      if (active) {
        if (JUSTONE_TOKEN && String(active.status) === "queued") {
          try {
            const result = await executeDirectScan(active);
            const refreshed = (await latestRequests(1))?.[0] || active;
            return json({ ok: true, accepted: true, existing: true, direct: true, request: publicRequest(refreshed), result }, 200, origin);
          } catch (error) {
            return json({ detail: String(error instanceof Error ? error.message : error).slice(0, 240) }, 502, origin);
          }
        }
        return json({ ok: true, accepted: true, existing: true, direct: false, request: publicRequest(active), eta_minutes: JUSTONE_TOKEN ? 0 : 5 }, 200, origin);
      }

      const windowState = requestWindow(requests);
      if (windowState.recentCount >= MAX_REQUESTS_PER_HOUR) {
        return json({ detail: "扫描额度保护已触发：每小时最多发起 3 次扫描。", retry_after_seconds: 1200 }, 429, origin);
      }
      if (windowState.cooldownSeconds > 0) {
        return json({ detail: "刚刚已经发起过扫描，请稍后再试。", retry_after_seconds: windowState.cooldownSeconds }, 429, origin);
      }

      let rows;
      try {
        const now = new Date().toISOString();
        rows = await rest("lead_radar_scan_requests?select=*", {
          method: "POST",
          headers: { Prefer: "return=representation" },
          body: JSON.stringify({
            status: JUSTONE_TOKEN ? "running" : "queued",
            started_at: JUSTONE_TOKEN ? now : null,
            requested_from: JUSTONE_TOKEN ? "web-direct" : "web",
            query_override: null,
            max_queries: 1,
            provider: "justone-xiaohongshu-v4",
            result: {},
          }),
        });
      } catch (error) {
        const refreshedRows = await latestRequests(5);
        const refreshed = Array.isArray(refreshedRows) ? refreshedRows : [];
        const raced = refreshed.find((row: any) => ["queued", "running"].includes(String(row?.status || ""))) || null;
        if (raced) return json({ ok: true, accepted: true, existing: true, request: publicRequest(raced), eta_minutes: JUSTONE_TOKEN ? 0 : 5 }, 200, origin);
        throw error;
      }

      const created = Array.isArray(rows) ? rows[0] : null;
      if (JUSTONE_TOKEN && created) {
        try {
          const result = await executeDirectScan(created);
          const refreshed = (await latestRequests(1))?.[0] || created;
          return json({ ok: true, accepted: true, existing: false, direct: true, request: publicRequest(refreshed), result }, 200, origin);
        } catch (error) {
          return json({ detail: String(error instanceof Error ? error.message : error).slice(0, 240) }, 502, origin);
        }
      }
      return json({ ok: true, accepted: true, existing: false, direct: false, request: publicRequest(created), eta_minutes: 5 }, 202, origin);
    }

    return json({ detail: "Not found" }, 404, origin);
  } catch (error) {
    console.error(String(error));
    return json({ detail: "Scan API error" }, 500, origin);
  }
});
