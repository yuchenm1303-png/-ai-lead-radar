import { POLICY_VERSION } from "../_shared/lead_policy.ts";

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

function cleanQueries(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 6).map((item) => ({
    key: String(item?.key || "").slice(0, 160),
    keyword: String(item?.keyword || "").slice(0, 240),
    category: String(item?.category || "").slice(0, 80),
    intent_family: String(item?.intent_family || "").slice(0, 80),
    topic_family: String(item?.topic_family || "").slice(0, 80),
    raw_count: Math.max(0, Math.min(500, Number(item?.raw_count || 0))),
    normalized_count: Math.max(0, Math.min(500, Number(item?.normalized_count || 0))),
    fresh_count: Math.max(0, Math.min(500, Number(item?.fresh_count || 0))),
    request_id: item?.request_id ? String(item.request_id).slice(0, 160) : null,
  }));
}

async function claimScanRequest(claims: Record<string, unknown>) {
  const rows = await rest("rpc/lead_radar_claim_scan_request", {
    method: "POST",
    body: JSON.stringify({ p_run_id: String(claims.run_id || "").slice(0, 120) }),
  });
  const row = Array.isArray(rows) ? rows[0] : null;
  if (!row) return null;
  return {
    id: Number(row.id),
    query_override: row.query_override ? String(row.query_override).slice(0, 120) : null,
    max_queries: Math.max(1, Math.min(3, Number(row.max_queries || 1))),
    requested_at: row.requested_at || null,
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

async function ingest(items: any[], queries: ReturnType<typeof cleanQueries>) {
  const first = queries[0] || null;
  const response = await fetch(`${SB_URL}/functions/v1/lead-radar-ingest/api/v1/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json", apikey: SECRET_KEY, Authorization: `Bearer ${SECRET_KEY}` },
    body: JSON.stringify({
      items,
      query_context: first ? {
        query_key: first.key,
        query_text: first.keyword,
        intent_family: first.intent_family,
        topic_family: first.topic_family,
        returned_count: first.raw_count,
        fresh_count: first.fresh_count,
      } : null,
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
      return json({ ok: true, service: "lead-radar-collector", auth: "github-actions-oidc", policy_version: POLICY_VERSION });
    }

    if (req.method === "POST" && path === "/api/v1/scan/claim") {
      const claims = await verifyGithubOidc(req);
      const claimed = await claimScanRequest(claims);
      return json(claimed ? { ok: true, claimed: true, request: claimed } : { ok: true, claimed: false });
    }

    if (req.method === "POST" && path === "/api/v1/scan/fail") {
      await verifyGithubOidc(req);
      const body = await req.json();
      const id = Number(body?.scan_request_id || 0);
      if (!Number.isInteger(id) || id <= 0) return json({ detail: "scan_request_id required" }, 422);
      const message = String(body?.error || "collector failed").slice(0, 700);
      await updateScanRequest(id, "failed", { failed_by: "collector", policy_version: POLICY_VERSION }, message);
      return json({ ok: true });
    }

    if (req.method !== "POST" || path !== "/api/v1/ingest/source") return json({ detail: "Not found" }, 404);
    await verifyGithubOidc(req);
    const body = await req.json();
    const connector = String(body?.connector || "justone-xiaohongshu-v4").slice(0, 120);
    const scanRequestId = Number.isInteger(Number(body?.scan_request_id)) && Number(body.scan_request_id) > 0 ? Number(body.scan_request_id) : null;
    const startedAt = Number.isFinite(new Date(body?.started_at || "").getTime()) ? new Date(body.started_at).toISOString() : new Date().toISOString();
    const scanned = Math.max(0, Math.min(2000, Number(body?.scanned || 0)));
    const queries = cleanQueries(body?.queries);
    const items = Array.isArray(body?.items) ? body.items.slice(0, 200) : [];
    const ingestion = await ingest(items, queries);
    const highIntent = (Array.isArray(ingestion?.decisions) ? ingestion.decisions : []).filter((item: any) => item?.disposition === "stored" && Number(item?.score || 0) >= 85).length;
    const finishedAt = new Date().toISOString();
    const result = {
      connector,
      policy_version: ingestion?.policy_version || POLICY_VERSION,
      scanned: scanned || items.length,
      stored: Number(ingestion?.stored || 0),
      filtered: Number(ingestion?.filtered || 0),
      duplicates: Number(ingestion?.duplicates || 0),
      notified: Number(ingestion?.notified || 0),
      high_intent: highIntent,
      lead_ids: Array.isArray(ingestion?.lead_ids) ? ingestion.lead_ids : [],
      queries,
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
      details: { queries, policy_version: result.policy_version, received: Number(ingestion?.received || items.length) },
    });
    if (scanRequestId) await updateScanRequest(scanRequestId, "success", result, null);
    return json({ ok: true, ...result, runtime_ms: Date.now() - new Date(startedAt).getTime() });
  } catch (error) {
    return json({ detail: String(error).slice(0, 500), policy_version: POLICY_VERSION }, 500);
  }
});
