const ALLOWED = new Set(["https://smirel.com", "http://localhost:3000"]);
const SB_URL = Deno.env.get("SUPABASE_URL") || "";
const LEGACY_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
let SECRET_KEY = LEGACY_KEY;
try {
  const keys = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}");
  SECRET_KEY = keys.default || SECRET_KEY;
} catch {}

const MAX_REQUESTS_PER_HOUR = 3;
const MIN_REQUEST_GAP_MS = 5 * 60 * 1000;

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
  return await rest(`lead_radar_scan_requests?select=id,status,requested_at,started_at,finished_at,result,error_text&order=requested_at.desc&limit=${limit}`);
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
    version: "0.2-queue-recovery",
    mode: "web-queued-github-worker",
    platform: "小红书 · Just One V4",
    worker_interval_minutes: 5,
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
        return json({ ok: true, accepted: true, existing: true, request: publicRequest(active), eta_minutes: 5 }, 200, origin);
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
        rows = await rest("lead_radar_scan_requests?select=*", {
          method: "POST",
          headers: { Prefer: "return=representation" },
          body: JSON.stringify({
            status: "queued",
            requested_from: "web",
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
        if (raced) return json({ ok: true, accepted: true, existing: true, request: publicRequest(raced), eta_minutes: 5 }, 200, origin);
        throw error;
      }

      const created = Array.isArray(rows) ? rows[0] : null;
      return json({ ok: true, accepted: true, existing: false, request: publicRequest(created), eta_minutes: 5 }, 202, origin);
    }

    return json({ detail: "Not found" }, 404, origin);
  } catch (error) {
    console.error(String(error));
    return json({ detail: "Scan queue API error" }, 500, origin);
  }
});
