import { POLICY_VERSION } from "../_shared/lead_policy.ts";
import {
  chooseQueries,
  retrievalLimits,
  shouldFetchNextPage,
  RETRIEVAL_VERSION,
  type QueryMetric,
  type QuerySpec,
} from "../_shared/retrieval_policy.ts";

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
const MAX_PREVIEW_BODY_CHARS = 12000;
const MAX_PREVIEW_IMAGES = 9;
const LIMITS = retrievalLimits();

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
  return await rest(`lead_radar_scan_requests?select=id,status,requested_at,started_at,finished_at,query_override,max_queries,requested_from,provider,result,error_text&order=requested_at.desc&limit=${limit}`);
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

function metricFromRow(row: any): QueryMetric {
  return {
    runs: Number(row?.runs || 0),
    api_calls: Number(row?.api_calls || row?.runs || 0),
    returned_count: Number(row?.returned_count || 0),
    fresh_count: Number(row?.fresh_count || 0),
    qualified_count: Number(row?.qualified_count || 0),
    filtered_count: Number(row?.filtered_count || 0),
    duplicate_count: Number(row?.duplicate_count || 0),
    human_positive_count: Number(row?.human_positive_count || 0),
    human_negative_count: Number(row?.human_negative_count || 0),
    last_run_at: row?.last_run_at || null,
  };
}

async function queryMetrics(): Promise<Record<string, QueryMetric>> {
  const result: Record<string, QueryMetric> = {};
  try {
    const rows = await rest("lead_radar_query_scheduler_stats?select=query_key,runs,api_calls,returned_count,fresh_count,qualified_count,filtered_count,duplicate_count,human_positive_count,human_negative_count,last_run_at&limit=1000");
    for (const row of Array.isArray(rows) ? rows : []) {
      const key = String(row?.query_key || "");
      if (key) result[key] = metricFromRow(row);
    }
  } catch (error) {
    console.warn("retrieval scheduler stats unavailable", String(error));
  }
  try {
    const rows = await rest("lead_radar_query_metrics?select=query_key,runs,returned_count,fresh_count,qualified_count,filtered_count,duplicate_count,last_run_at&limit=1000");
    for (const row of Array.isArray(rows) ? rows : []) {
      const key = String(row?.query_key || "");
      if (key && !result[key]) result[key] = metricFromRow(row);
    }
  } catch {}
  return result;
}

async function retrievalSettings() {
  const defaults = {
    auto_enabled: false,
    auto_interval_minutes: 60,
    auto_queries_per_scan: LIMITS.max_queries_auto,
    auto_provider_calls_per_scan: LIMITS.max_provider_calls_auto,
    manual_queries_per_scan: LIMITS.max_queries_web,
    manual_provider_calls_per_scan: LIMITS.max_provider_calls_web,
    provider_calls_per_hour_cap: 6,
  };
  try {
    const rows = await rest("lead_radar_retrieval_settings?id=eq.1&select=auto_enabled,auto_interval_minutes,auto_queries_per_scan,auto_provider_calls_per_scan,manual_queries_per_scan,manual_provider_calls_per_scan,provider_calls_per_hour_cap&limit=1");
    const row = Array.isArray(rows) ? rows[0] : null;
    if (!row) return defaults;
    return {
      auto_enabled: Boolean(row.auto_enabled),
      auto_interval_minutes: Math.max(15, Number(row.auto_interval_minutes || defaults.auto_interval_minutes)),
      auto_queries_per_scan: Math.max(1, Math.min(3, Number(row.auto_queries_per_scan || defaults.auto_queries_per_scan))),
      auto_provider_calls_per_scan: Math.max(1, Math.min(6, Number(row.auto_provider_calls_per_scan || defaults.auto_provider_calls_per_scan))),
      manual_queries_per_scan: Math.max(1, Math.min(3, Number(row.manual_queries_per_scan || defaults.manual_queries_per_scan))),
      manual_provider_calls_per_scan: Math.max(1, Math.min(6, Number(row.manual_provider_calls_per_scan || defaults.manual_provider_calls_per_scan))),
      provider_calls_per_hour_cap: Math.max(1, Math.min(30, Number(row.provider_calls_per_hour_cap || defaults.provider_calls_per_hour_cap))),
    };
  } catch {
    return defaults;
  }
}

async function providerCallsLastHour() {
  try {
    const since = new Date(Date.now() - 60 * 60 * 1000).toISOString();
    const rows = await rest(`lead_radar_query_runs?select=api_calls&started_at=gte.${encodeURIComponent(since)}&limit=1000`);
    return (Array.isArray(rows) ? rows : []).reduce((sum: number, row: any) => sum + Math.max(0, Number(row?.api_calls || 0)), 0);
  } catch {
    return 0;
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

function parseNote(note: any, now = Date.now()) {
  if (!note || typeof note !== "object" || Array.isArray(note)) return null;
  const id = String(note.id || "").trim();
  const title = String(note.title || "").trim();
  const body = String(note.desc || "").trim();
  const published = parseTimestamp(note.timestamp);
  if (!id || !published || (!title && !body)) return null;
  const preview = buildPreview(note, id, title, body, published);
  const ageMinutes = (now - published.getTime()) / 60000;
  return {
    id,
    published,
    fresh: ageMinutes >= -5 && ageMinutes <= LIMITS.freshness_minutes,
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
  };
}

async function fetchJustOnePage(spec: QuerySpec, page: number) {
  if (!JUSTONE_TOKEN) throw new Error("JUSTONE_API_TOKEN is not configured");
  const url = new URL(JUSTONE_ENDPOINT);
  url.searchParams.set("token", JUSTONE_TOKEN);
  url.searchParams.set("keyword", spec.keyword);
  url.searchParams.set("page", String(Math.max(1, page)));
  url.searchParams.set("sortType", "time_descending");
  url.searchParams.set("noteType", "ALL");
  url.searchParams.set("timeFilter", "ALL");
  const response = await fetch(url, {
    headers: { Accept: "application/json", "User-Agent": "AI-Lead-Radar/3.0" },
    signal: AbortSignal.timeout(20000),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`Just One HTTP ${response.status}: ${text.slice(0, 200)}`);
  let payload: any;
  try { payload = JSON.parse(text); } catch { throw new Error("Just One returned invalid JSON"); }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("Just One returned a non-object response");
  if (payload.code !== 0) throw new Error(`Just One business code ${String(payload.code)}: ${String(payload.message || payload.msg || "business error").slice(0, 180)}`);

  const data = payload?.data && typeof payload.data === "object" ? payload.data : {};
  const notes = Array.isArray(data?.notes) ? data.notes : [];
  const now = Date.now();
  const parsed = notes.map((note: any) => parseNote(note, now)).filter(Boolean) as any[];
  const dates = parsed.map((entry) => entry.published.getTime()).filter(Number.isFinite);
  const hasMoreRaw = data?.has_more ?? data?.hasMore;
  return {
    page,
    raw_count: notes.length,
    normalized_count: parsed.length,
    request_id: payload.requestId ? String(payload.requestId).slice(0, 160) : null,
    has_more: typeof hasMoreRaw === "boolean" ? hasMoreRaw : null,
    newest_published_at: dates.length ? new Date(Math.max(...dates)).toISOString() : null,
    oldest_published_at: dates.length ? new Date(Math.min(...dates)).toISOString() : null,
    entries: parsed.filter((entry) => entry.fresh),
  };
}

function newQuerySource(spec: QuerySpec) {
  return {
    spec,
    started_at: new Date().toISOString(),
    raw_count: 0,
    normalized_count: 0,
    pages: 0,
    api_calls: 0,
    request_ids: [] as string[],
    has_more: null as boolean | null,
    newest_published_at: null as string | null,
    oldest_published_at: null as string | null,
    last_page_raw_count: 0,
    last_page_oldest_published_at: null as string | null,
    entries: new Map<string, { item: any; preview: any }>(),
  };
}

function mergePage(source: ReturnType<typeof newQuerySource>, page: Awaited<ReturnType<typeof fetchJustOnePage>>) {
  source.raw_count += page.raw_count;
  source.normalized_count += page.normalized_count;
  source.pages += 1;
  source.api_calls += 1;
  source.has_more = page.has_more;
  source.last_page_raw_count = page.raw_count;
  source.last_page_oldest_published_at = page.oldest_published_at;
  if (page.request_id) source.request_ids.push(page.request_id);
  if (page.newest_published_at && (!source.newest_published_at || page.newest_published_at > source.newest_published_at)) source.newest_published_at = page.newest_published_at;
  if (page.oldest_published_at && (!source.oldest_published_at || page.oldest_published_at < source.oldest_published_at)) source.oldest_published_at = page.oldest_published_at;
  for (const entry of page.entries) {
    if (!source.entries.has(entry.id)) source.entries.set(entry.id, { item: entry.item, preview: entry.preview });
  }
}

async function executeRetrievalPlan(specs: QuerySpec[], providerCallBudget: number) {
  const sources = specs.map(newQuerySource);
  let providerCallsUsed = 0;

  for (const source of sources) {
    if (providerCallsUsed >= providerCallBudget) break;
    const page = await fetchJustOnePage(source.spec, 1);
    providerCallsUsed += 1;
    mergePage(source, page);
  }

  while (providerCallsUsed < providerCallBudget) {
    const candidates = sources
      .filter((source) => source.pages > 0 && shouldFetchNextPage({
        rawCount: source.last_page_raw_count,
        oldestPublishedAt: source.last_page_oldest_published_at,
        hasMore: source.has_more,
        pagesFetched: source.pages,
        providerCallsUsed,
        providerCallBudget,
      }))
      .sort((a, b) => b.entries.size - a.entries.size || b.raw_count - a.raw_count);
    const source = candidates[0];
    if (!source) break;
    const page = await fetchJustOnePage(source.spec, source.pages + 1);
    providerCallsUsed += 1;
    mergePage(source, page);
  }

  return { sources: sources.filter((source) => source.pages > 0), providerCallsUsed };
}

async function ingestItems(items: any[], spec: QuerySpec, source: ReturnType<typeof newQuerySource>, scanRequestId: number) {
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
        scan_request_id: scanRequestId,
        provider: "justone-xiaohongshu-v4",
        retrieval_version: RETRIEVAL_VERSION,
        query_key: spec.key,
        query_text: spec.keyword,
        lane: spec.lane,
        intent_family: spec.intent_family,
        topic_family: spec.topic_family,
        started_at: source.started_at,
        pages: source.pages,
        api_calls: source.api_calls,
        returned_count: source.raw_count,
        normalized_count: source.normalized_count,
        fresh_count: source.entries.size,
        newest_published_at: source.newest_published_at,
        oldest_published_at: source.oldest_published_at,
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

function postRank(post: any) {
  const decision = String(post?.decision || "unknown");
  if (decision === "stored") return 4;
  if (decision === "filtered") return 3;
  if (decision === "seen") return 2;
  return 1;
}

async function executeDirectScan(requestRow: any) {
  const id = Number(requestRow?.id || 0);
  if (!Number.isInteger(id) || id <= 0) throw new Error("invalid scan request");
  const startedAt = new Date().toISOString();
  await updateRequest(id, { status: "running", started_at: startedAt, error_text: null });

  try {
    const settings = await retrievalSettings();
    const callsLastHour = await providerCallsLastHour();
    const remainingHourlyCalls = Math.max(0, settings.provider_calls_per_hour_cap - callsLastHour);
    if (remainingHourlyCalls <= 0) throw new Error("Provider hourly call budget exhausted");
    const requestedFrom = String(requestRow?.requested_from || "web");
    const perScanBudget = requestedFrom === "auto" ? settings.auto_provider_calls_per_scan : settings.manual_provider_calls_per_scan;
    const providerCallBudget = Math.max(1, Math.min(perScanBudget, remainingHourlyCalls));
    const desiredQueries = Math.max(1, Math.min(3, Number(requestRow?.max_queries || (requestedFrom === "auto" ? settings.auto_queries_per_scan : settings.manual_queries_per_scan))));
    const metrics = await queryMetrics();
    const specs = chooseQueries({
      override: String(requestRow?.query_override || "").trim() || null,
      metrics,
      count: Math.min(desiredQueries, providerCallBudget),
    });
    if (!specs.length) throw new Error("retrieval plan is empty");

    const retrieval = await executeRetrievalPlan(specs, providerCallBudget);
    const postMap = new Map<string, any>();
    const leadIds = new Set<number>();
    const queryResults: any[] = [];
    let totalStored = 0;
    let totalFiltered = 0;
    let totalDuplicates = 0;
    let totalNotified = 0;
    let totalScanned = 0;

    for (const source of retrieval.sources) {
      const items = [...source.entries.values()].map((entry) => entry.item);
      const previews = [...source.entries.values()].map((entry) => entry.preview);
      const ingest = await ingestItems(items, source.spec, source, id);
      const posts = decoratePosts(previews, ingest.decisions || []);
      for (const post of posts) {
        const key = String(post.id || "");
        if (!key) continue;
        const previous = postMap.get(key);
        if (!previous || postRank(post) > postRank(previous)) postMap.set(key, post);
      }
      for (const leadId of Array.isArray(ingest.lead_ids) ? ingest.lead_ids : []) {
        if (Number.isInteger(Number(leadId)) && Number(leadId) > 0) leadIds.add(Number(leadId));
      }
      totalStored += Number(ingest.stored || 0);
      totalFiltered += Number(ingest.filtered || 0);
      totalDuplicates += Number(ingest.duplicates || 0);
      totalNotified += Number(ingest.notified || 0);
      totalScanned += source.raw_count;
      queryResults.push({
        key: source.spec.key,
        keyword: source.spec.keyword,
        lane: source.spec.lane,
        intent_family: source.spec.intent_family,
        topic_family: source.spec.topic_family,
        pages: source.pages,
        api_calls: source.api_calls,
        raw_count: source.raw_count,
        normalized_count: source.normalized_count,
        fresh_count: source.entries.size,
        stored: Number(ingest.stored || 0),
        filtered: Number(ingest.filtered || 0),
        duplicates: Number(ingest.duplicates || 0),
        request_ids: source.request_ids,
        newest_published_at: source.newest_published_at,
        oldest_published_at: source.oldest_published_at,
      });
    }

    const posts = [...postMap.values()].sort((a, b) => new Date(b.published_at || 0).getTime() - new Date(a.published_at || 0).getTime());
    const storedVisible = posts.filter((post) => post.decision === "stored").length;
    const filteredVisible = posts.filter((post) => post.decision === "filtered").length;
    const highIntent = posts.filter((post) => post.decision === "stored" && Number(post.score || 0) >= 85).length;
    const result = {
      provider: "justone-xiaohongshu-v4",
      policy_version: POLICY_VERSION,
      retrieval_version: RETRIEVAL_VERSION,
      query: queryResults[0] ? {
        key: queryResults[0].key,
        keyword: queryResults[0].keyword,
        lane: queryResults[0].lane,
        intent_family: queryResults[0].intent_family,
        topic_family: queryResults[0].topic_family,
      } : null,
      queries: queryResults,
      provider_calls: retrieval.providerCallsUsed,
      provider_call_budget: providerCallBudget,
      scanned: totalScanned,
      fresh: posts.length,
      stored: storedVisible,
      filtered: filteredVisible,
      duplicates: totalDuplicates,
      classified_stored: totalStored,
      classified_filtered: totalFiltered,
      notified: totalNotified,
      high_intent: highIntent,
      lead_ids: [...leadIds],
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
      error_text: null,
      details: {
        mode: "retrieval-v2",
        retrieval_version: RETRIEVAL_VERSION,
        policy_version: POLICY_VERSION,
        provider_calls: result.provider_calls,
        queries: queryResults,
      },
    });
    return { id, status: "success", requested_at: requestRow.requested_at, started_at: startedAt, finished_at: finishedAt, result, error_text: null };
  } catch (error) {
    const finishedAt = new Date().toISOString();
    const message = String(error).slice(0, 700);
    await updateRequest(id, { status: "failed", finished_at: finishedAt, error_text: message, result: { policy_version: POLICY_VERSION, retrieval_version: RETRIEVAL_VERSION } });
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
      details: { policy_version: POLICY_VERSION, retrieval_version: RETRIEVAL_VERSION },
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
  const settings = await retrievalSettings();
  const callsLastHour = await providerCallsLastHour();
  return {
    ok: true,
    platform: "小红书 · Just One V4",
    provider: "justone-xiaohongshu-v4",
    policy_version: POLICY_VERSION,
    retrieval_version: RETRIEVAL_VERSION,
    direct_ready: Boolean(JUSTONE_TOKEN),
    running: String(active?.status || "") === "running",
    queued: String(active?.status || "") === "queued",
    active_request: publicRequest(active),
    latest_request: publicRequest(latest),
    last_scan_at: latestSuccess?.finished_at || null,
    recent_count: window.recentCount,
    cooldown_seconds: window.cooldownSeconds,
    next_available_at: window.nextAvailableAt,
    queue_available: !active && window.recentCount < MAX_REQUESTS_PER_HOUR && window.cooldownSeconds <= 0 && callsLastHour < settings.provider_calls_per_hour_cap,
    retrieval: {
      manual_queries_per_scan: settings.manual_queries_per_scan,
      manual_provider_calls_per_scan: settings.manual_provider_calls_per_scan,
      provider_calls_last_hour: callsLastHour,
      provider_calls_per_hour_cap: settings.provider_calls_per_hour_cap,
      auto_enabled: settings.auto_enabled,
      auto_interval_minutes: settings.auto_interval_minutes,
    },
  };
}

async function createScanRequest(settings: Awaited<ReturnType<typeof retrievalSettings>>) {
  const rows = await rest("lead_radar_scan_requests?select=*", {
    method: "POST",
    headers: { Prefer: "return=representation" },
    body: JSON.stringify({
      requested_from: "web",
      max_queries: settings.manual_queries_per_scan,
      provider: "justone-xiaohongshu-v4",
      status: "queued",
    }),
  });
  return rows?.[0] || null;
}

Deno.serve(async (request) => {
  const origin = request.headers.get("origin") || "";
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(origin) });
  const url = new URL(request.url);

  if (request.method === "GET" && url.pathname.endsWith("/api/v1/status")) {
    try { return json(await statusPayload(), 200, origin); }
    catch (error) { return json({ detail: String(error).slice(0, 400), policy_version: POLICY_VERSION, retrieval_version: RETRIEVAL_VERSION }, 500, origin); }
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

      const settings = await retrievalSettings();
      const callsLastHour = await providerCallsLastHour();
      if (callsLastHour >= settings.provider_calls_per_hour_cap) {
        return json({ detail: "Provider hourly call budget active", provider_calls_last_hour: callsLastHour, provider_calls_per_hour_cap: settings.provider_calls_per_hour_cap }, 429, origin);
      }
      const created = await createScanRequest(settings);
      if (!created?.id) throw new Error("failed to create scan request");
      if (!JUSTONE_TOKEN) return json({ ok: true, direct: false, existing: false, request: publicRequest(created) }, 202, origin);
      const completed = await executeDirectScan(created);
      return json({ ok: true, direct: true, existing: false, request: publicRequest(completed) }, 200, origin);
    } catch (error) {
      return json({ detail: String(error).slice(0, 500), policy_version: POLICY_VERSION, retrieval_version: RETRIEVAL_VERSION }, 500, origin);
    }
  }

  return json({ detail: "Not found" }, 404, origin);
});
