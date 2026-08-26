import { chooseQuery, POLICY_VERSION, type QueryMetric, type QuerySpec } from "../_shared/lead_policy.ts";

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

function cors(origin = "") {
  return {
    "Access-Control-Allow-Origin": ALLOWED.has(origin) ? origin : "https://smirel.com",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
    "Vary": "Origin",
  };
}

function json(data: unknown, status = 200, origin = "") {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json; charset=utf-8", ...cors(origin) } });
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
    recentCount: recent.length,
    cooldownSeconds: Math.ceil(cooldownMs / 1000),
    nextAvailableAt: cooldownMs > 0 ? new Date(now + cooldownMs).toISOString() : null,
  };
}

async function queryMetrics(): Promise<Record<string, QueryMetric>> {
  try {
    const rows = await rest("lead_radar_query_metrics?select=query_key,runs,fresh_count,qualified_count&limit=500");
    const result: Record<string, QueryMetric> = {};
    for (const row of Array.isArray(rows) ? rows : []) {
      const key = String(row?.query_key || "");
      if (!key) continue;
      result[key] = {
        runs: Number(row?.runs || 0),
        fresh_count: Number(row?.fresh_count || 0),
        qualified_count: Number(row?.qualified_count || 0),
      };
    }
    return result;
  } catch {
    return {};
  }
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

function firstText(object: any, keys: string[]) {
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

async function fetchJustOne(spec: QuerySpec) {
  if (!JUSTONE_TOKEN) throw new Error("JUSTONE_API_TOKEN is not configured");
  const url = new URL(JUSTONE_ENDPOINT);
  url.searchParams.set("token", JUSTONE_TOKEN);
  url.searchParams.set("keyword", spec.keyword);
  url.searchParams.set("page", "1");
  url.searchParams.set("sortType", "time_descending");
  url.searchParams.set("noteType", "ALL");
  url.searchParams.set("timeFilter", "ALL");
  const response = await fetch(url, {
    headers: { Accept: "application/json", "User-Agent": "AI-Lead-Radar/2.0" },
    signal: AbortSignal.timeout(20000),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`Just One HTTP ${response.status}: ${text.slice(0, 200)}`);
  let payload: any;
  try { payload = JSON.parse(text); } catch { throw new Error("Just One returned invalid JSON"); }
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
    raw_count: notes.length,
    fresh_count: unique.size,
    request_id: payload.requestId ? String(payload.requestId).slice(0, 160) : null,
    items: [...unique.values()].map((entry) => entry.item),
    previews: [...unique.values()].map((entry) => entry.preview),
  };
}

async function ingestItems(items: any[], spec: QuerySpec, source: any) {
  if (!items.length) {
    return { ok: true, received: 0, stored: 0, filtered: 0, duplicates: 0, notified: 0, lead_ids: [], decisions: [], policy_version: POLICY_VERSION };
  }
  const response = await fetch(`${SB_URL}/functions/v1/lead-radar-ingest/api/v1/ingest`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      apikey: SECRET_KEY,
      Authorization: `Bearer ${SECRET_KEY}`,
    },
    body: JSON.stringify({
      items,
      query_context: {
        query_key: spec.key,
        query_text: spec.keyword,
        intent_family: spec.intent_family,
        topic_family: spec.topic_family,
        returned_count: source.raw_count,
        fresh_count: source.fresh_count,
      },
    }),
    signal: AbortSignal.timeout(30000),
  });
  const text = await response.text();
  let payload: any = {};
  try { payload = text ? JSON.parse(text) : {}; } catch { payload = { detail: text.slice(0, 200) }; }
  if (!response.ok) throw new Error(`lead-radar-ingest ${response.status}: ${String(payload.detail || text).slice(0, 220)}`);
  return payload;
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

function decoratePosts(previews: any[], decisions: any[]) {
  const byId = new Map<string, any>();
  for (const decision of Array.isArray(decisions) ? decisions : []) {
    const id = String(decision?.external_id || "");
    if (id) byId.set(id, decision);
  }
  return previews.map((preview) => {
    const decision = byId.get(String(preview.id)) || null;
    const disposition = String(decision?.disposition || "unknown");
    return {
      ...preview,
      decision: disposition === "duplicate" ? "seen" : disposition,
      lead_id: decision?.lead_id || null,
      score: decision?.score ?? null,
      assessment: decision?.assessment || null,
    };
  });
}

async function executeDirectScan(requestRow: any) {
  const id = Number(requestRow?.id || 0);
  if (!Number.isInteger(id) || id <= 0) throw new Error("invalid scan request");
  const startedAt = new Date().toISOString();
  await updateRequest(id, { status: "running", started_at: startedAt, error_text: null });
  const metrics = await queryMetrics();
  const spec = chooseQuery({ override: String(requestRow?.query_override || "").trim() || null, metrics });
  try {
    const source = await fetchJustOne(spec);
    const ingest = await ingestItems(source.items, spec, source);
    const posts = decoratePosts(source.previews, ingest.decisions || []);
    const highIntent = (Array.isArray(ingest.decisions) ? ingest.decisions : []).filter((item: any) => item?.disposition === "stored" && Number(item?.score || 0) >= 85).length;
    const result = {
      provider: "justone-xiaohongshu-v4",
      policy_version: ingest.policy_version || POLICY_VERSION,
      query: {
        key: spec.key,
        keyword: spec.keyword,
        intent_family: spec.intent_family,
        topic_family: spec.topic_family,
      },
      request_id: source.request_id,
      scanned: source.raw_count,
      fresh: source.fresh_count,
      stored: Number(ingest.stored || 0),
      filtered: Number(ingest.filtered || 0),
      duplicates: Number(ingest.duplicates || 0),
      notified: Number(ingest.notified || 0),
      high_intent: highIntent,
      lead_ids: Array.isArray(ingest.lead_ids) ? ingest.lead_ids : [],
      posts,
    };
    const finishedAt = new Date().toISOString();
    await updateRequest(id, { status: "success", finished_at: finishedAt, result, error_text: null });
    await recordRun({
      connector: "justone-xiaohongshu-v4",
      started_at: startedAt,
      finished_at: finishedAt,
      scanned: result.scanned,
      stored: result.stored,
      filtered: result.filtered,
      high_intent: highIntent,
      status: "success",
      details: { query: result.query, policy_version: result.policy_version, request_id: source.request_id },
    });
    return { id, status: "success", requested_at: requestRow.requested_at, started_at: startedAt, finished_at: finishedAt, result, error_text: null };
  } catch (error) {
    const finishedAt = new Date().toISOString();
    const message = String(error).slice(0, 700);
    await updateRequest(id, { status: "failed", finished_at: finishedAt, error_text: message, result: { policy_version: POLICY_VERSION } });
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
      details: { policy_version: POLICY_VERSION },
    });
    throw error;
  }
}

async function statusPayload() {
  const requests = await latestRequests(10);
  const rows = Array.isArray(requests) ? requests : [];
  const active = rows.find((row: any) => ["queued", "running"].includes(String(row?.status || ""))) || null;
  const latest = rows[0] || null;
  const latestSuccess = rows.find((row: any) => String(row?.status || "") === "success") || null;
  const window = requestWindow(rows);
  return {
    ok: true,
    platform: "小红书 · Just One V4",
    provider: "justone-xiaohongshu-v4",
    policy_version: POLICY_VERSION,
    direct_ready: Boolean(JUSTONE_TOKEN),
    running: String(active?.status || "") === "running",
    queued: String(active?.status || "") === "queued",
    active_request: publicRequest(active),
    latest_request: publicRequest(latest),
    last_scan_at: latestSuccess?.finished_at || null,
    recent_count: window.recentCount,
    cooldown_seconds: window.cooldownSeconds,
    next_available_at: window.nextAvailableAt,
    queue_available: !active && window.recentCount < MAX_REQUESTS_PER_HOUR && window.cooldownSeconds <= 0,
  };
}

async function createScanRequest() {
  const rows = await rest("lead_radar_scan_requests?select=*", {
    method: "POST",
    headers: { Prefer: "return=representation" },
    body: JSON.stringify({ requested_from: "web", max_queries: 1, provider: "justone-xiaohongshu-v4", status: "queued" }),
  });
  return rows?.[0] || null;
}

Deno.serve(async (request) => {
  const origin = request.headers.get("origin") || "";
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(origin) });
  const url = new URL(request.url);

  if (request.method === "GET" && url.pathname.endsWith("/api/v1/status")) {
    try { return json(await statusPayload(), 200, origin); }
    catch (error) { return json({ detail: String(error).slice(0, 400), policy_version: POLICY_VERSION }, 500, origin); }
  }

  if (request.method === "POST" && url.pathname.endsWith("/api/v1/request")) {
    if (!ALLOWED.has(origin)) return json({ detail: "Origin not allowed" }, 403, origin);
    try {
      const requests = await latestRequests(10);
      const rows = Array.isArray(requests) ? requests : [];
      const active = rows.find((row: any) => ["queued", "running"].includes(String(row?.status || ""))) || null;
      if (active) {
        if (String(active.status) === "queued" && JUSTONE_TOKEN) {
          const completed = await executeDirectScan(active);
          return json({ ok: true, direct: true, existing: true, request: publicRequest(completed) }, 200, origin);
        }
        return json({ ok: true, direct: false, existing: true, request: publicRequest(active) }, 200, origin);
      }

      const window = requestWindow(rows);
      if (window.recentCount >= MAX_REQUESTS_PER_HOUR || window.cooldownSeconds > 0) {
        return json({ detail: "Scan rate limit active", retry_after_seconds: Math.max(1, window.cooldownSeconds), next_available_at: window.nextAvailableAt }, 429, origin);
      }

      const created = await createScanRequest();
      if (!created?.id) throw new Error("failed to create scan request");
      if (!JUSTONE_TOKEN) return json({ ok: true, direct: false, existing: false, request: publicRequest(created) }, 202, origin);
      const completed = await executeDirectScan(created);
      return json({ ok: true, direct: true, existing: false, request: publicRequest(completed) }, 200, origin);
    } catch (error) {
      return json({ detail: String(error).slice(0, 500), policy_version: POLICY_VERSION }, 500, origin);
    }
  }

  return json({ detail: "Not found" }, 404, origin);
});
