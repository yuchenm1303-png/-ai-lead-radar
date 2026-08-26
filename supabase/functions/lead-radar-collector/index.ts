import { POLICY_VERSION } from "../_shared/lead_policy.ts";
import { chooseQueries, RETRIEVAL_VERSION, type QueryMetric } from "../_shared/retrieval_policy.ts";

const SB_URL = Deno.env.get("SUPABASE_URL") || "";
const LEGACY_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
let SECRET_KEY = LEGACY_KEY;
try {
  const keys = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}");
  SECRET_KEY = keys.default || SECRET_KEY;
} catch {}

const GITHUB_ISSUER = "https://token.actions.githubusercontent.com";
const GITHUB_JWKS = "https://token.actions.githubusercontent.com/.well-known/jwks";
const EXPECTED_AUDIENCE = "lead-radar-collector";
const EXPECTED_REPOSITORY = "yuchenm1303-png/-ai-lead-radar";
const EXPECTED_REF = "refs/heads/main";
const EXPECTED_WORKFLOW_REF = `${EXPECTED_REPOSITORY}/.github/workflows/lead-radar-collector.yml@${EXPECTED_REF}`;

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json; charset=utf-8" } });
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

function b64urlBytes(value: string): Uint8Array<ArrayBuffer> {
  let base64 = value.replace(/-/g, "+").replace(/_/g, "/");
  while (base64.length % 4) base64 += "=";
  const binary = atob(base64);
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

function decodeJsonPart(value: string): Record<string, unknown> {
  const parsed = JSON.parse(new TextDecoder().decode(b64urlBytes(value)));
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("invalid JWT object");
  return parsed as Record<string, unknown>;
}

function audienceMatches(aud: unknown) {
  return typeof aud === "string" ? aud === EXPECTED_AUDIENCE : Array.isArray(aud) && aud.some((item) => item === EXPECTED_AUDIENCE);
}

async function verifyGithubOidc(req: Request) {
  const authorization = req.headers.get("authorization") || "";
  const match = authorization.match(/^Bearer\s+(.+)$/i);
  if (!match) throw new Error("missing bearer token");
  const token = match[1].trim();
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("invalid JWT format");
  const header = decodeJsonPart(parts[0]);
  const claims = decodeJsonPart(parts[1]);
  if (header.alg !== "RS256" || typeof header.kid !== "string") throw new Error("unsupported JWT algorithm");

  const jwksResponse = await fetch(GITHUB_JWKS, { headers: { Accept: "application/json" } });
  if (!jwksResponse.ok) throw new Error(`GitHub JWKS ${jwksResponse.status}`);
  const jwks = await jwksResponse.json();
  const jwk = Array.isArray(jwks?.keys) ? jwks.keys.find((key: any) => key?.kid === header.kid) : null;
  if (!jwk) throw new Error("GitHub signing key not found");
  const publicKey = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const verified = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", publicKey, b64urlBytes(parts[2]), new TextEncoder().encode(`${parts[0]}.${parts[1]}`));
  if (!verified) throw new Error("invalid GitHub OIDC signature");

  const now = Math.floor(Date.now() / 1000);
  if (Number(claims.exp || 0) < now - 30) throw new Error("expired GitHub OIDC token");
  if (Number(claims.nbf || 0) > now + 30) throw new Error("GitHub OIDC token is not active yet");
  if (claims.iss !== GITHUB_ISSUER) throw new Error("unexpected GitHub OIDC issuer");
  if (!audienceMatches(claims.aud)) throw new Error("unexpected GitHub OIDC audience");
  if (claims.repository !== EXPECTED_REPOSITORY) throw new Error("unexpected GitHub repository");
  if (claims.ref !== EXPECTED_REF) throw new Error("collector must run from main");
  if (claims.workflow_ref !== EXPECTED_WORKFLOW_REF) throw new Error("unexpected collector workflow");
  return claims;
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

function cleanQuery(item: any) {
  return {
    key: String(item?.key || "").slice(0, 160),
    keyword: String(item?.keyword || "").slice(0, 240),
    category: String(item?.category || "").slice(0, 80),
    lane: String(item?.lane || "precision").slice(0, 40),
    intent_family: String(item?.intent_family || "").slice(0, 80),
    topic_family: String(item?.topic_family || "").slice(0, 80),
    raw_count: Math.max(0, Math.min(1000, Number(item?.raw_count || 0))),
    normalized_count: Math.max(0, Math.min(1000, Number(item?.normalized_count || 0))),
    fresh_count: Math.max(0, Math.min(1000, Number(item?.fresh_count || 0))),
    pages: Math.max(0, Math.min(6, Number(item?.pages || 1))),
    api_calls: Math.max(0, Math.min(12, Number(item?.api_calls || 1))),
    request_ids: Array.isArray(item?.request_ids) ? item.request_ids.slice(0, 6).map((value: unknown) => String(value).slice(0, 160)) : [],
    newest_published_at: item?.newest_published_at || null,
    oldest_published_at: item?.oldest_published_at || null,
    started_at: item?.started_at || null,
  };
}

function cleanQueries(value: unknown) {
  return Array.isArray(value) ? value.slice(0, 6).map(cleanQuery).filter((item) => item.key && item.keyword) : [];
}

async function claimScanRequest(claims: Record<string, unknown>, allowAuto: boolean) {
  const rows = await rest("rpc/lead_radar_claim_scan_work", {
    method: "POST",
    body: JSON.stringify({
      p_run_id: String(claims.run_id || "").slice(0, 120),
      p_allow_auto: Boolean(allowAuto),
    }),
  });
  const row = Array.isArray(rows) ? rows[0] : null;
  if (!row) return null;
  const maxQueries = Math.max(1, Math.min(3, Number(row.max_queries || 1)));
  const providerCallBudget = Math.max(1, Math.min(6, Number(row.provider_call_budget || maxQueries)));
  const metrics = await queryMetrics();
  const queries = chooseQueries({
    override: row.query_override ? String(row.query_override).slice(0, 120) : null,
    count: Math.min(maxQueries, providerCallBudget),
    metrics,
  }).map((spec) => ({
    key: spec.key,
    keyword: spec.keyword,
    category: spec.category,
    lane: spec.lane,
    intent_family: spec.intent_family,
    topic_family: spec.topic_family,
  }));
  return {
    id: Number(row.id),
    query_override: row.query_override ? String(row.query_override).slice(0, 120) : null,
    max_queries: maxQueries,
    requested_at: row.requested_at || null,
    requested_from: String(row.requested_from || "web").slice(0, 40),
    provider_call_budget: providerCallBudget,
    retrieval_version: RETRIEVAL_VERSION,
    queries,
  };
}

async function updateScanRequest(id: number, status: "success" | "failed", result: Record<string, unknown>, errorText: string | null = null) {
  await rest(`lead_radar_scan_requests?id=eq.${id}`, {
    method: "PATCH",
    headers: { Prefer: "return=minimal" },
    body: JSON.stringify({ status, finished_at: new Date().toISOString(), updated_at: new Date().toISOString(), result, error_text: errorText }),
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

async function ingestBatch(connector: string, scanRequestId: number | null, query: ReturnType<typeof cleanQuery>, items: any[]) {
  const response = await fetch(`${SB_URL}/functions/v1/lead-radar-ingest/api/v1/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json", apikey: SECRET_KEY, Authorization: `Bearer ${SECRET_KEY}` },
    body: JSON.stringify({
      items,
      query_context: {
        scan_request_id: scanRequestId,
        provider: connector,
        retrieval_version: RETRIEVAL_VERSION,
        query_key: query.key,
        query_text: query.keyword,
        lane: query.lane,
        intent_family: query.intent_family,
        topic_family: query.topic_family,
        started_at: query.started_at || new Date().toISOString(),
        pages: query.pages,
        api_calls: query.api_calls,
        returned_count: query.raw_count,
        normalized_count: query.normalized_count,
        fresh_count: query.fresh_count,
        newest_published_at: query.newest_published_at,
        oldest_published_at: query.oldest_published_at,
      },
    }),
    signal: AbortSignal.timeout(30000),
  });
  const text = await response.text();
  let payload: any = {};
  try { payload = text ? JSON.parse(text) : {}; } catch { payload = { detail: text.slice(0, 300) }; }
  if (!response.ok) throw new Error(`lead-radar-ingest ${response.status}: ${String(payload.detail || text).slice(0, 300)}`);
  return payload;
}

Deno.serve(async (req) => {
  try {
    const url = new URL(req.url);
    const path = url.pathname.replace(/^.*\/lead-radar-collector/, "") || "/";
    if (req.method === "GET" && path === "/health") {
      return json({ ok: true, service: "lead-radar-collector", auth: "github-actions-oidc", policy_version: POLICY_VERSION, retrieval_version: RETRIEVAL_VERSION });
    }

    if (req.method === "POST" && path === "/api/v1/scan/claim") {
      const claims = await verifyGithubOidc(req);
      const body = await req.json().catch(() => ({}));
      const claimed = await claimScanRequest(claims, Boolean(body?.allow_auto));
      return json(claimed ? { ok: true, claimed: true, request: claimed } : { ok: true, claimed: false });
    }

    if (req.method === "POST" && path === "/api/v1/scan/fail") {
      await verifyGithubOidc(req);
      const body = await req.json();
      const id = Number(body?.scan_request_id || 0);
      if (!Number.isInteger(id) || id <= 0) return json({ detail: "scan_request_id required" }, 422);
      const message = String(body?.error || "collector failed").slice(0, 700);
      await updateScanRequest(id, "failed", { failed_by: "collector", policy_version: POLICY_VERSION, retrieval_version: RETRIEVAL_VERSION }, message);
      return json({ ok: true });
    }

    if (req.method !== "POST" || path !== "/api/v1/ingest/source") return json({ detail: "Not found" }, 404);
    await verifyGithubOidc(req);
    const body = await req.json();
    const connector = String(body?.connector || "justone-xiaohongshu-v4").slice(0, 120);
    const scanRequestId = Number.isInteger(Number(body?.scan_request_id)) && Number(body.scan_request_id) > 0 ? Number(body.scan_request_id) : null;
    const startedAt = Number.isFinite(new Date(body?.started_at || "").getTime()) ? new Date(body.started_at).toISOString() : new Date().toISOString();

    let batches: { query: ReturnType<typeof cleanQuery>; items: any[] }[] = [];
    if (Array.isArray(body?.batches)) {
      batches = body.batches.slice(0, 6).map((batch: any) => ({
        query: cleanQuery(batch?.query || {}),
        items: Array.isArray(batch?.items) ? batch.items.slice(0, 100) : [],
      })).filter((batch: any) => batch.query.key && batch.query.keyword);
    } else {
      const queries = cleanQueries(body?.queries);
      if (queries[0]) batches = [{ query: queries[0], items: Array.isArray(body?.items) ? body.items.slice(0, 100) : [] }];
    }
    if (!batches.length) throw new Error("collector payload contains no query batches");

    let stored = 0, filtered = 0, duplicates = 0, notified = 0, highIntent = 0;
    const leadIds = new Set<number>();
    const queryResults: any[] = [];
    for (const batch of batches) {
      const ingestion = await ingestBatch(connector, scanRequestId, batch.query, batch.items);
      stored += Number(ingestion?.stored || 0);
      filtered += Number(ingestion?.filtered || 0);
      duplicates += Number(ingestion?.duplicates || 0);
      notified += Number(ingestion?.notified || 0);
      for (const leadId of Array.isArray(ingestion?.lead_ids) ? ingestion.lead_ids : []) {
        if (Number.isInteger(Number(leadId)) && Number(leadId) > 0) leadIds.add(Number(leadId));
      }
      highIntent += (Array.isArray(ingestion?.decisions) ? ingestion.decisions : []).filter((item: any) => item?.disposition === "stored" && Number(item?.score || 0) >= 85).length;
      queryResults.push({
        ...batch.query,
        stored: Number(ingestion?.stored || 0),
        filtered: Number(ingestion?.filtered || 0),
        duplicates: Number(ingestion?.duplicates || 0),
      });
    }

    const finishedAt = new Date().toISOString();
    const scanned = batches.reduce((sum, batch) => sum + Number(batch.query.raw_count || 0), 0);
    const fresh = new Set(batches.flatMap((batch) => batch.items.map((item: any) => `${String(item?.source || "")}|${String(item?.external_id || item?.url || "")}`))).size;
    const result = {
      connector,
      policy_version: POLICY_VERSION,
      retrieval_version: RETRIEVAL_VERSION,
      scanned,
      fresh,
      stored,
      filtered,
      duplicates,
      notified,
      high_intent: highIntent,
      lead_ids: [...leadIds],
      queries: queryResults,
      provider_calls: batches.reduce((sum, batch) => sum + Number(batch.query.api_calls || 0), 0),
      last_scan_at: finishedAt,
    };
    await recordRun({
      connector,
      started_at: startedAt,
      finished_at: finishedAt,
      scanned: result.scanned,
      stored: result.stored,
      filtered: result.filtered,
      high_intent: highIntent,
      status: "success",
      error_text: null,
      details: { mode: "retrieval-v2-collector", queries: queryResults, policy_version: POLICY_VERSION, retrieval_version: RETRIEVAL_VERSION, provider_calls: result.provider_calls },
    });
    if (scanRequestId) await updateScanRequest(scanRequestId, "success", result, null);
    return json({ ok: true, ...result, runtime_ms: Date.now() - new Date(startedAt).getTime() });
  } catch (error) {
    return json({ detail: String(error).slice(0, 500), policy_version: POLICY_VERSION, retrieval_version: RETRIEVAL_VERSION }, 500);
  }
});
