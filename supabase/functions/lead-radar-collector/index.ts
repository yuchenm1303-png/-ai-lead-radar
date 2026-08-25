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
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
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

function b64urlBytes(value: string): Uint8Array<ArrayBuffer> {
  let base64 = value.replace(/-/g, "+").replace(/_/g, "/");
  while (base64.length % 4) base64 += "=";
  const binary = atob(base64);
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function decodeJsonPart(value: string): Record<string, unknown> {
  const bytes = b64urlBytes(value);
  const text = new TextDecoder().decode(bytes);
  const parsed = JSON.parse(text);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("invalid JWT object");
  return parsed as Record<string, unknown>;
}

function audienceMatches(aud: unknown): boolean {
  if (typeof aud === "string") return aud === EXPECTED_AUDIENCE;
  return Array.isArray(aud) && aud.some((item) => item === EXPECTED_AUDIENCE);
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

  const publicKey = await crypto.subtle.importKey(
    "jwk",
    jwk,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const verified = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    publicKey,
    b64urlBytes(parts[2]),
    new TextEncoder().encode(`${parts[0]}.${parts[1]}`),
  );
  if (!verified) throw new Error("invalid GitHub OIDC signature");

  const now = Math.floor(Date.now() / 1000);
  const exp = Number(claims.exp || 0);
  const nbf = Number(claims.nbf || 0);
  if (!exp || exp < now - 30) throw new Error("expired GitHub OIDC token");
  if (nbf && nbf > now + 30) throw new Error("GitHub OIDC token is not active yet");
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
    key: String(item?.key || "").slice(0, 80),
    keyword: String(item?.keyword || "").slice(0, 120),
    category: String(item?.category || "").slice(0, 80),
    raw_count: Math.max(0, Math.min(500, Number(item?.raw_count || 0))),
    normalized_count: Math.max(0, Math.min(500, Number(item?.normalized_count || 0))),
    fresh_count: Math.max(0, Math.min(500, Number(item?.fresh_count || 0))),
    request_id: item?.request_id ? String(item.request_id).slice(0, 160) : null,
  }));
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

Deno.serve(async (req: Request) => {
  const startedWall = Date.now();
  let auditStarted = new Date().toISOString();
  let connector = "justone-xiaohongshu-v4";
  let scanned = 0;
  let queries: ReturnType<typeof cleanQueries> = [];

  try {
    const url = new URL(req.url);
    const path = url.pathname.replace(/^.*\/lead-radar-collector/, "") || "/";
    if (req.method === "GET" && path === "/health") {
      return json({ ok: true, service: "lead-radar-collector", auth: "github-actions-oidc" });
    }
    if (req.method !== "POST" || path !== "/api/v1/ingest/source") return json({ detail: "Not found" }, 404);

    const claims = await verifyGithubOidc(req);
    const body = await req.json();
    connector = String(body?.connector || connector).slice(0, 120);
    auditStarted = Number.isFinite(new Date(body?.started_at || "").getTime())
      ? new Date(body.started_at).toISOString()
      : new Date().toISOString();
    scanned = Math.max(0, Math.min(2000, Number(body?.scanned || 0)));
    queries = cleanQueries(body?.queries);
    const items = Array.isArray(body?.items) ? body.items.slice(0, 200) : [];

    let ingest: any = { received: 0, stored: 0, filtered: 0, duplicates: 0, notified: 0, lead_ids: [] };
    if (items.length) {
      const ingestResponse = await fetch(`${SB_URL}/functions/v1/lead-radar-api/api/v1/ingest/manual`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          Origin: "https://smirel.com",
        },
        body: JSON.stringify({ items }),
      });
      const ingestText = await ingestResponse.text();
      try { ingest = ingestText ? JSON.parse(ingestText) : {}; }
      catch { ingest = { detail: ingestText.slice(0, 300) }; }
      if (!ingestResponse.ok) throw new Error(`lead-radar-api ${ingestResponse.status}: ${ingestText.slice(0, 300)}`);
    }

    const leadIds = Array.isArray(ingest?.lead_ids)
      ? ingest.lead_ids.filter((id: unknown) => Number.isInteger(Number(id))).map((id: unknown) => Number(id)).slice(0, 200)
      : [];
    let highIntent = 0;
    if (leadIds.length) {
      const rows = await rest(`lead_radar_leads?id=in.(${leadIds.join(",")})&select=id,ai_score`);
      if (Array.isArray(rows)) highIntent = rows.filter((row: any) => Number(row?.ai_score || 0) >= 80).length;
    }

    const finished = new Date().toISOString();
    await recordRun({
      connector,
      started_at: auditStarted,
      finished_at: finished,
      scanned: scanned || items.length,
      stored: Number(ingest?.stored || 0),
      filtered: Number(ingest?.filtered || 0),
      high_intent: highIntent,
      status: "success",
      error_text: null,
      details: {
        queries,
        received: Number(ingest?.received || items.length),
        duplicates: Number(ingest?.duplicates || 0),
        notified: Number(ingest?.notified || 0),
        repository: claims.repository,
        workflow_ref: claims.workflow_ref,
        run_id: claims.run_id || null,
        duration_ms: Date.now() - startedWall,
      },
    });

    return json({
      ok: true,
      connector,
      scanned: scanned || items.length,
      stored: Number(ingest?.stored || 0),
      filtered: Number(ingest?.filtered || 0),
      duplicates: Number(ingest?.duplicates || 0),
      notified: Number(ingest?.notified || 0),
      high_intent: highIntent,
      lead_ids: leadIds,
      last_scan_at: finished,
    });
  } catch (error) {
    const message = String(error).slice(0, 700);
    console.error(message);
    await recordRun({
      connector,
      started_at: auditStarted,
      finished_at: new Date().toISOString(),
      scanned,
      stored: 0,
      filtered: 0,
      high_intent: 0,
      status: "failed",
      error_text: message,
      details: { queries, duration_ms: Date.now() - startedWall },
    });
    const authFailure = /OIDC|bearer|JWT|repository|workflow|audience|issuer|signature|expired|main/i.test(message);
    return json({ detail: authFailure ? "Collector authentication failed" : "Collector ingest failed" }, authFailure ? 401 : 500);
  }
});
