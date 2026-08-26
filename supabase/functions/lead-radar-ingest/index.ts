import { assessText, POLICY_VERSION, type PolicyAssessment } from "../_shared/lead_policy.ts";

const SB_URL = Deno.env.get("SUPABASE_URL") || "";
const LEGACY_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
let SECRET_KEY = LEGACY_KEY;
try {
  const keys = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}");
  SECRET_KEY = keys.default || SECRET_KEY;
} catch {}
const FEISHU_WEBHOOK_URL = (Deno.env.get("FEISHU_WEBHOOK_URL") || "").trim();
const NOTIFY_MIN_SCORE = Math.max(0, Math.min(100, Number(Deno.env.get("NOTIFY_MIN_SCORE") || 85)));

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

function authorized(request: Request) {
  if (!SECRET_KEY) return false;
  const header = request.headers.get("authorization") || "";
  return header === `Bearer ${SECRET_KEY}`;
}

function safeUrl(value: unknown): string | null {
  if (!value) return null;
  try {
    const url = new URL(String(value));
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function normalizeItem(input: any) {
  const title = String(input?.title || "").trim().slice(0, 240);
  if (!title) return null;
  const published = new Date(input?.published_at || Date.now());
  return {
    source: String(input?.source || "manual").trim().slice(0, 40) || "manual",
    external_id: input?.external_id ? String(input.external_id).trim().slice(0, 160) : null,
    title,
    excerpt: String(input?.excerpt || "").trim().slice(0, 1600),
    url: safeUrl(input?.url),
    published_at: Number.isFinite(published.getTime()) ? published.toISOString() : new Date().toISOString(),
    budget: input?.budget ? String(input.budget).trim().slice(0, 100) : null,
  };
}

async function sha256(value: string) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)].map((x) => x.toString(16).padStart(2, "0")).join("");
}

function identity(item: any) {
  if (item.external_id) return `${item.source}|id:${item.external_id}`;
  return `${item.source}|url:${item.url || ""}|title:${item.title.toLowerCase()}`;
}

async function findSeen(item: any, dedupeKey: string) {
  if (item.external_id) {
    return await rest(`lead_radar_seen_items?source=eq.${encodeURIComponent(item.source)}&source_id=eq.${encodeURIComponent(item.external_id)}&select=id,disposition,lead_id&limit=1`);
  }
  return await rest(`lead_radar_seen_items?dedupe_key=eq.${dedupeKey}&select=id,disposition,lead_id&limit=1`);
}

async function createSeen(item: any, dedupeKey: string) {
  const rows = await rest("lead_radar_seen_items?select=*", {
    method: "POST",
    headers: { Prefer: "return=representation" },
    body: JSON.stringify({
      source: item.source,
      source_id: item.external_id,
      dedupe_key: dedupeKey,
      disposition: "seen",
      metadata: { published_at: item.published_at, url: item.url, policy_version: POLICY_VERSION },
    }),
  });
  return rows?.[0] || null;
}

async function updateSeen(id: number, disposition: string, leadId: number | null, assessment: PolicyAssessment | null) {
  await rest(`lead_radar_seen_items?id=eq.${id}`, {
    method: "PATCH",
    headers: { Prefer: "return=minimal" },
    body: JSON.stringify({
      disposition,
      lead_id: leadId,
      last_seen_at: new Date().toISOString(),
      metadata: assessment ? {
        policy_version: assessment.policy_version,
        actor_role: assessment.actor_role,
        buying_stage: assessment.buying_stage,
        reason_codes: assessment.reason_codes,
      } : undefined,
    }),
  });
}

function freshnessScore(publishedAt: string) {
  const hours = Math.max(0, (Date.now() - new Date(publishedAt).getTime()) / 36e5);
  if (hours <= 0.5) return 100;
  if (hours <= 2) return 94;
  if (hours <= 6) return 86;
  if (hours <= 24) return 72;
  if (hours <= 72) return 52;
  if (hours <= 168) return 35;
  return 15;
}

function urgency(text: string) {
  const value = text.toLowerCase();
  if (["急", "急需", "尽快", "马上", "今天", "这两天"].some((word) => value.includes(word))) return "high";
  if (["近期", "最近", "本周", "这周"].some((word) => value.includes(word))) return "medium";
  return "low";
}

function finalScore(item: any, assessment: PolicyAssessment) {
  const freshness = freshnessScore(item.published_at);
  let intent = assessment.intent_score;
  let actionability = assessment.actionability_score;
  if (item.budget && assessment.actor_role === "buyer") {
    intent = Math.max(intent, 82);
    actionability = Math.max(actionability, 88);
  }
  const score = Math.max(0, Math.min(100, Math.round(intent * 0.40 + assessment.fit_score * 0.20 + freshness * 0.20 + actionability * 0.20)));
  return { score, freshness, intent, actionability };
}

async function insertLead(item: any, assessment: PolicyAssessment, dedupeKey: string) {
  const scored = finalScore(item, assessment);
  const urgencyValue = urgency(`${item.title} ${item.excerpt}`);
  const priority = scored.score >= 85 ? "high" : scored.score >= 70 ? "medium" : "low";
  const reason = [
    `角色=${assessment.actor_role}`,
    `阶段=${assessment.buying_stage}`,
    `类型=${assessment.category}`,
    `policy=${assessment.policy_version}`,
  ].join("；");
  const rows = await rest("lead_radar_leads?select=*", {
    method: "POST",
    headers: { Prefer: "return=representation" },
    body: JSON.stringify({
      source: item.source,
      source_id: item.external_id,
      title: item.title,
      excerpt: item.excerpt,
      url: item.url,
      published_at: item.published_at,
      need_type: assessment.category,
      is_lead: true,
      intent_score: scored.intent,
      fit_score: assessment.fit_score,
      freshness_score: scored.freshness,
      ai_score: scored.score,
      urgency: urgencyValue,
      confidence: assessment.confidence,
      priority,
      budget_text: item.budget,
      reason,
      signals: assessment.reason_codes.slice(0, 12),
      actor_role: assessment.actor_role,
      buying_stage: assessment.buying_stage,
      actionability_score: scored.actionability,
      policy_version: assessment.policy_version,
      dedupe_key: dedupeKey,
    }),
  });
  return rows?.[0] || null;
}

async function notifyLead(lead: any) {
  if (!FEISHU_WEBHOOK_URL || Number(lead?.ai_score || 0) < NOTIFY_MIN_SCORE) return false;
  try {
    const response = await fetch(FEISHU_WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        msg_type: "text",
        content: { text: `Lead Radar · ${lead.ai_score}分\n${lead.title}\n${lead.url || ""}` },
      }),
      signal: AbortSignal.timeout(8000),
    });
    return response.ok;
  } catch {
    return false;
  }
}

async function recordQueryMetric(context: any, counts: any) {
  if (!context?.query_key || context.query_key === "manual") return;
  try {
    await rest("rpc/lead_radar_record_query_metric", {
      method: "POST",
      headers: { Prefer: "return=minimal" },
      body: JSON.stringify({
        p_query_key: String(context.query_key).slice(0, 160),
        p_query_text: String(context.query_text || "").slice(0, 240),
        p_intent_family: String(context.intent_family || "").slice(0, 80),
        p_topic_family: String(context.topic_family || "").slice(0, 80),
        p_returned: Number(context.returned_count || 0),
        p_fresh: Number(context.fresh_count || 0),
        p_qualified: Number(counts.stored || 0),
        p_filtered: Number(counts.filtered || 0),
        p_duplicates: Number(counts.duplicates || 0),
      }),
    });
  } catch (error) {
    console.warn("query metric update failed", String(error));
  }
}

async function ingest(payload: any) {
  const rawItems = Array.isArray(payload?.items) ? payload.items : [];
  const items = rawItems.map(normalizeItem).filter(Boolean).slice(0, 100);
  const decisions: any[] = [];
  let stored = 0, filtered = 0, duplicates = 0, notified = 0;
  const leadIds: number[] = [];

  for (const item of items) {
    const dedupeKey = await sha256(identity(item));
    const existing = await findSeen(item, dedupeKey);
    const first = Array.isArray(existing) ? existing[0] : null;
    if (first && String(first.disposition || "") !== "error") {
      duplicates += 1;
      decisions.push({ external_id: item.external_id, disposition: "duplicate", lead_id: first.lead_id || null, assessment: null });
      continue;
    }

    const seen = first || await createSeen(item, dedupeKey);
    if (!seen?.id) throw new Error("failed to create seen item");
    try {
      const assessment = assessText(item.title, item.excerpt);
      if (!assessment.is_lead) {
        filtered += 1;
        await updateSeen(Number(seen.id), "filtered", null, assessment);
        decisions.push({ external_id: item.external_id, disposition: "filtered", lead_id: null, assessment });
        continue;
      }

      const lead = await insertLead(item, assessment, dedupeKey);
      if (!lead?.id) throw new Error("lead insert returned no id");
      stored += 1;
      leadIds.push(Number(lead.id));
      await updateSeen(Number(seen.id), "stored", Number(lead.id), assessment);
      if (await notifyLead(lead)) notified += 1;
      decisions.push({ external_id: item.external_id, disposition: "stored", lead_id: Number(lead.id), assessment, score: Number(lead.ai_score || 0) });
    } catch (error) {
      await updateSeen(Number(seen.id), "error", null, null);
      decisions.push({ external_id: item.external_id, disposition: "error", lead_id: null, error: String(error).slice(0, 300) });
    }
  }

  const counts = { received: items.length, stored, filtered, duplicates, notified, lead_ids: leadIds };
  await recordQueryMetric(payload?.query_context || null, counts);
  return { ok: true, policy_version: POLICY_VERSION, ...counts, decisions };
}

Deno.serve(async (request) => {
  if (request.method === "OPTIONS") return new Response(null, { status: 204 });
  if (!authorized(request)) return json({ detail: "Unauthorized" }, 401);
  const url = new URL(request.url);
  if (request.method !== "POST" || !url.pathname.endsWith("/api/v1/ingest")) return json({ detail: "Not found" }, 404);
  try {
    return json(await ingest(await request.json()));
  } catch (error) {
    return json({ detail: String(error).slice(0, 500), policy_version: POLICY_VERSION }, 500);
  }
});
