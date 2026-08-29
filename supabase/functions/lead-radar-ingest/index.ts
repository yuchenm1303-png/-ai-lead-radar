import { assessText, POLICY_VERSION, type PolicyAssessment } from "../_shared/lead_policy.ts";
import { RETRIEVAL_VERSION } from "../_shared/retrieval_policy.ts";
import {
  classifySemanticBatch,
  decideSemantic,
  hardGuardrail,
  providerReady,
  defaultSemanticModel,
  DEFAULT_SEMANTIC_MODEL,
  INTELLIGENCE_VERSION,
  type IntelligenceSettings,
  type SemanticAssessment,
  type SemanticDecision,
  type SemanticProvider,
} from "../_shared/semantic_intent.ts";

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

function firstText(object: any, keys: string[]) {
  if (!object || typeof object !== "object") return "";
  for (const key of keys) {
    const value = object[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function normalizeItem(input: any) {
  const title = String(input?.title || "").trim().slice(0, 240);
  if (!title) return null;
  const published = new Date(input?.published_at || Date.now());
  const author = input?.author && typeof input.author === "object" ? input.author : {};
  const authorId = String(input?.author_id || firstText(author, ["id", "user_id", "userId", "userid"]) || "").trim().slice(0, 160) || null;
  const authorName = String(input?.author_name || firstText(author, ["nickname", "nick_name", "name", "user_name", "userName"]) || "").trim().slice(0, 120) || null;
  return {
    source: String(input?.source || "manual").trim().slice(0, 40) || "manual",
    external_id: input?.external_id ? String(input.external_id).trim().slice(0, 160) : null,
    title,
    excerpt: String(input?.excerpt || "").trim().slice(0, 1600),
    url: safeUrl(input?.url),
    published_at: Number.isFinite(published.getTime()) ? published.toISOString() : new Date().toISOString(),
    budget: input?.budget ? String(input.budget).trim().slice(0, 100) : null,
    author_id: authorId,
    author_name: authorName,
    content_kind: String(input?.content_kind || "post").trim().slice(0, 20) || "post",
    context_text: String(input?.context_text || "").trim().slice(0, 1200),
    parent_source_id: input?.parent_source_id ? String(input.parent_source_id).trim().slice(0, 160) : null,
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

async function semanticContentHash(item: any) {
  return await sha256([
    item.source,
    item.title,
    item.excerpt,
    item.author_id || "",
    item.author_name || "",
    item.content_kind || "post",
    item.context_text || "",
  ].join("\n"));
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
      metadata: {
        published_at: item.published_at,
        url: item.url,
        author_id: item.author_id,
        author_name: item.author_name,
        policy_version: POLICY_VERSION,
        intelligence_version: INTELLIGENCE_VERSION,
      },
    }),
  });
  return rows?.[0] || null;
}

async function updateSeen(
  id: number,
  disposition: string,
  leadId: number | null,
  assessment: PolicyAssessment | null,
  semantic: SemanticAssessment | null = null,
  semanticDecision: SemanticDecision | null = null,
) {
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
        intelligence_version: INTELLIGENCE_VERSION,
        semantic_actor_role: semantic?.actor_role || null,
        transaction_direction: semantic?.transaction_direction || null,
        buyer_probability: semantic?.buyer_probability ?? null,
        semantic_confidence: semantic?.confidence ?? null,
        semantic_decision: semanticDecision,
      } : undefined,
    }),
  });
}

function clampInt(value: unknown, fallback: number, min = 0, max = 100) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(min, Math.min(max, Math.round(number))) : fallback;
}

function semanticProvider(value: unknown): SemanticProvider {
  return String(value || "").toLowerCase() === "minimax" ? "minimax" : "openai";
}

async function intelligenceSettings(): Promise<IntelligenceSettings> {
  const defaults: IntelligenceSettings = {
    enabled: false,
    mode: "shadow",
    provider: "openai",
    model: (Deno.env.get("LEAD_SEMANTIC_MODEL") || DEFAULT_SEMANTIC_MODEL).trim() || DEFAULT_SEMANTIC_MODEL,
    buyer_threshold: 72,
    min_confidence: 65,
    reject_confidence: 75,
    max_items_per_batch: 20,
  };
  try {
    const rows = await rest("lead_radar_intelligence_settings?id=eq.1&select=semantic_enabled,semantic_mode,provider,model,buyer_threshold,min_confidence,reject_confidence,max_items_per_batch&limit=1");
    const row = Array.isArray(rows) ? rows[0] : null;
    if (!row) return defaults;
    const mode = ["off", "shadow", "enforce"].includes(String(row.semantic_mode)) ? String(row.semantic_mode) as IntelligenceSettings["mode"] : defaults.mode;
    const provider = semanticProvider(row.provider);
    const providerModel = defaultSemanticModel(provider);
    return {
      enabled: Boolean(row.semantic_enabled),
      mode,
      provider,
      model: String(row.model || providerModel).trim().slice(0, 120) || providerModel,
      buyer_threshold: clampInt(row.buyer_threshold, defaults.buyer_threshold),
      min_confidence: clampInt(row.min_confidence, defaults.min_confidence),
      reject_confidence: clampInt(row.reject_confidence, defaults.reject_confidence),
      max_items_per_batch: clampInt(row.max_items_per_batch, defaults.max_items_per_batch, 1, 40),
    };
  } catch (error) {
    console.warn("intelligence settings unavailable", String(error));
    return defaults;
  }
}

function semanticFromRow(row: any): SemanticAssessment | null {
  if (!row) return null;
  const id = String(row.source_id || row.content_hash || "").trim();
  if (!id) return null;
  return {
    id,
    actor_role: String(row.actor_role || "unknown") as SemanticAssessment["actor_role"],
    transaction_direction: String(row.transaction_direction || "unknown") as SemanticAssessment["transaction_direction"],
    buyer_probability: clampInt(row.buyer_probability, 0),
    confidence: clampInt(row.confidence, 0),
    project_specificity: clampInt(row.project_specificity, 0),
    reason: String(row.reason || "").slice(0, 220),
    evidence: (Array.isArray(row.evidence) ? row.evidence : []).map(String).slice(0, 4),
  };
}

async function cachedSemantic(item: any, contentHash: string, settings: IntelligenceSettings) {
  try {
    const rows = await rest(`lead_radar_semantic_assessments?content_hash=eq.${contentHash}&intelligence_version=eq.${encodeURIComponent(INTELLIGENCE_VERSION)}&provider=eq.${encodeURIComponent(settings.provider)}&model=eq.${encodeURIComponent(settings.model)}&select=source_id,content_hash,actor_role,transaction_direction,buyer_probability,confidence,project_specificity,reason,evidence&limit=1`);
    const row = Array.isArray(rows) ? rows[0] : null;
    const assessment = semanticFromRow(row);
    if (!assessment) return null;
    return { ...assessment, id: String(item.external_id || contentHash) };
  } catch {
    return null;
  }
}

async function persistSemantic(
  item: any,
  contentHash: string,
  settings: IntelligenceSettings,
  semantic: SemanticAssessment,
  decision: SemanticDecision,
) {
  try {
    await rest("lead_radar_semantic_assessments?on_conflict=content_hash,intelligence_version,provider,model", {
      method: "POST",
      headers: { Prefer: "resolution=merge-duplicates,return=minimal" },
      body: JSON.stringify({
        source: item.source,
        source_id: item.external_id,
        content_hash: contentHash,
        intelligence_version: INTELLIGENCE_VERSION,
        policy_version: POLICY_VERSION,
        provider: settings.provider,
        model: settings.model,
        actor_role: semantic.actor_role,
        transaction_direction: semantic.transaction_direction,
        buyer_probability: semantic.buyer_probability,
        confidence: semantic.confidence,
        project_specificity: semantic.project_specificity,
        semantic_decision: decision,
        reason: semantic.reason,
        evidence: semantic.evidence,
      }),
    });
  } catch (error) {
    console.warn("semantic assessment persist failed", String(error));
  }
}

function mergeAcceptedSemantic(assessment: PolicyAssessment, semantic: SemanticAssessment): PolicyAssessment {
  const stage = assessment.buying_stage === "none" ? "considering" : assessment.buying_stage;
  const intentFloor = semantic.buyer_probability >= 90 ? 88 : semantic.buyer_probability >= 80 ? 82 : 72;
  const actionabilityFloor = semantic.project_specificity >= 75 ? 86 : semantic.project_specificity >= 50 ? 78 : 72;
  return {
    ...assessment,
    actor_role: "buyer",
    buying_stage: stage,
    is_lead: true,
    intent_score: Math.max(assessment.intent_score, intentFloor),
    actionability_score: Math.max(assessment.actionability_score, actionabilityFloor),
    confidence: Math.max(assessment.confidence, semantic.confidence),
    reason_codes: [...new Set([
      ...assessment.reason_codes,
      "semantic:buyer",
      "semantic:direction_buy",
      `semantic:buyer_probability_${semantic.buyer_probability}`,
    ])].slice(0, 16),
    evidence: [...new Set([...assessment.evidence, ...semantic.evidence])].slice(0, 8),
  };
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

function finalScore(item: any, assessment: PolicyAssessment, semantic: SemanticAssessment | null = null) {
  const freshness = freshnessScore(item.published_at);
  let intent = assessment.intent_score;
  let actionability = assessment.actionability_score;
  if (item.budget && assessment.actor_role === "buyer") {
    intent = Math.max(intent, 82);
    actionability = Math.max(actionability, 88);
  }
  if (semantic?.actor_role === "buyer" && semantic.transaction_direction === "buy") {
    actionability = Math.max(actionability, semantic.project_specificity);
  }
  const score = Math.max(0, Math.min(100, Math.round(intent * 0.40 + assessment.fit_score * 0.20 + freshness * 0.20 + actionability * 0.20)));
  return { score, freshness, intent, actionability };
}

async function insertLead(item: any, assessment: PolicyAssessment, dedupeKey: string, semantic: SemanticAssessment | null = null, settings: IntelligenceSettings | null = null) {
  const scored = finalScore(item, assessment, semantic);
  const urgencyValue = urgency(`${item.title} ${item.excerpt}`);
  const priority = scored.score >= 85 ? "high" : scored.score >= 70 ? "medium" : "low";
  const reason = [
    `角色=${assessment.actor_role}`,
    `阶段=${assessment.buying_stage}`,
    `类型=${assessment.category}`,
    semantic ? `语义=${semantic.actor_role}/${semantic.transaction_direction}/${semantic.buyer_probability}` : null,
    `policy=${assessment.policy_version}`,
    `intelligence=${INTELLIGENCE_VERSION}`,
  ].filter(Boolean).join("；");
  const signals = [...new Set([
    ...assessment.reason_codes,
    ...(semantic ? [`semantic:${semantic.actor_role}`, `direction:${semantic.transaction_direction}`] : []),
  ])].slice(0, 16);
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
      confidence: semantic ? Math.max(assessment.confidence, semantic.confidence) : assessment.confidence,
      priority,
      budget_text: item.budget,
      reason,
      signals,
      actor_role: assessment.actor_role,
      buying_stage: assessment.buying_stage,
      actionability_score: scored.actionability,
      policy_version: assessment.policy_version,
      dedupe_key: dedupeKey,
      author_id: item.author_id,
      author_name: item.author_name,
      semantic_actor_role: semantic?.actor_role || null,
      transaction_direction: semantic?.transaction_direction || null,
      buyer_probability: semantic?.buyer_probability ?? null,
      semantic_confidence: semantic?.confidence ?? null,
      project_specificity: semantic?.project_specificity ?? null,
      intelligence_version: INTELLIGENCE_VERSION,
      semantic_model: semantic ? settings?.model || DEFAULT_SEMANTIC_MODEL : null,
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

async function recordQueryRun(context: any, items: any[], decisions: any[], counts: any) {
  if (!context?.query_key || context.query_key === "manual") return;
  try {
    const rows = await rest("lead_radar_query_runs?select=id", {
      method: "POST",
      headers: { Prefer: "return=representation" },
      body: JSON.stringify({
        scan_request_id: Number.isInteger(Number(context.scan_request_id)) && Number(context.scan_request_id) > 0 ? Number(context.scan_request_id) : null,
        provider: String(context.provider || "justone-xiaohongshu-v4").slice(0, 120),
        retrieval_version: String(context.retrieval_version || RETRIEVAL_VERSION).slice(0, 40),
        query_key: String(context.query_key).slice(0, 160),
        query_text: String(context.query_text || "").slice(0, 240),
        lane: String(context.lane || "precision").slice(0, 40),
        intent_family: String(context.intent_family || "").slice(0, 80),
        topic_family: String(context.topic_family || "").slice(0, 80),
        started_at: context.started_at || new Date().toISOString(),
        finished_at: new Date().toISOString(),
        pages: Math.max(0, Number(context.pages || 1)),
        api_calls: Math.max(0, Number(context.api_calls || 1)),
        returned_count: Math.max(0, Number(context.returned_count || 0)),
        normalized_count: Math.max(0, Number(context.normalized_count || items.length)),
        fresh_count: Math.max(0, Number(context.fresh_count || 0)),
        qualified_count: Math.max(0, Number(counts.stored || 0)),
        filtered_count: Math.max(0, Number(counts.filtered || 0)),
        duplicate_count: Math.max(0, Number(counts.duplicates || 0)),
        newest_published_at: context.newest_published_at || null,
        oldest_published_at: context.oldest_published_at || null,
      }),
    });
    const queryRunId = Number(rows?.[0]?.id || 0);
    if (!queryRunId) return;

    const decisionById = new Map<string, any>();
    for (const decision of decisions) {
      const id = String(decision?.external_id || "");
      if (id) decisionById.set(id, decision);
    }
    const observations = items
      .filter((item) => item?.external_id)
      .map((item) => {
        const decision = decisionById.get(String(item.external_id)) || {};
        return {
          query_run_id: queryRunId,
          source: String(item.source || "manual").slice(0, 40),
          source_id: String(item.external_id).slice(0, 160),
          lead_id: Number.isInteger(Number(decision.lead_id)) && Number(decision.lead_id) > 0 ? Number(decision.lead_id) : null,
          disposition: String(decision.disposition || "unknown").slice(0, 40),
          score: Number.isFinite(Number(decision.score)) ? Number(decision.score) : null,
          published_at: item.published_at || null,
        };
      });
    if (observations.length) {
      await rest("lead_radar_query_observations", {
        method: "POST",
        headers: { Prefer: "return=minimal" },
        body: JSON.stringify(observations),
      });
    }
  } catch (error) {
    console.warn("query run attribution failed", String(error));
  }
}


function actorPlatformSource(value: unknown) {
  const source = String(value || "unknown").trim().slice(0, 40) || "unknown";
  return source.startsWith("小红书") ? "小红书" : source;
}

function actorMemoryKey(item: any) {
  const authorId = String(item?.author_id || "").trim();
  return authorId ? `${actorPlatformSource(item?.source)}|${authorId}` : "";
}

function policyDirection(assessment: PolicyAssessment) {
  if (assessment.actor_role === "buyer") return "buy";
  if (assessment.actor_role === "provider") return "sell";
  if (assessment.actor_role === "recruiter") return "recruit";
  if (["learner", "content"].includes(assessment.actor_role)) return "non_transactional";
  return "unknown";
}

function actorContextFromRow(row: any) {
  if (!row) return null;
  return {
    observations: Math.max(0, Number(row.observations || 0)),
    buyer_count: Math.max(0, Number(row.buyer_count || 0)),
    provider_count: Math.max(0, Number(row.provider_count || 0)),
    recruiter_count: Math.max(0, Number(row.recruiter_count || 0)),
    learner_count: Math.max(0, Number(row.learner_count || 0)),
    content_count: Math.max(0, Number(row.content_count || 0)),
    unknown_count: Math.max(0, Number(row.unknown_count || 0)),
    buy_count: Math.max(0, Number(row.buy_count || 0)),
    sell_count: Math.max(0, Number(row.sell_count || 0)),
    recruit_count: Math.max(0, Number(row.recruit_count || 0)),
    non_transactional_count: Math.max(0, Number(row.non_transactional_count || 0)),
    unknown_direction_count: Math.max(0, Number(row.unknown_direction_count || 0)),
    max_buyer_probability: clampInt(row.max_buyer_probability, 0),
    last_role: String(row.last_role || "unknown"),
    last_direction: String(row.last_direction || "unknown"),
    last_confidence: clampInt(row.last_confidence, 0),
  };
}

async function loadActorMemory(items: any[]) {
  const result = new Map<string, Record<string, unknown>>();
  const unique = new Map<string, { source: string; authorId: string }>();
  for (const item of items) {
    const key = actorMemoryKey(item);
    if (!key) continue;
    unique.set(key, { source: actorPlatformSource(item.source), authorId: String(item.author_id) });
  }
  await Promise.all([...unique.entries()].map(async ([key, actor]) => {
    try {
      const rows = await rest(`lead_radar_actor_memory?source=eq.${encodeURIComponent(actor.source)}&author_id=eq.${encodeURIComponent(actor.authorId)}&select=observations,buyer_count,provider_count,recruiter_count,learner_count,content_count,unknown_count,buy_count,sell_count,recruit_count,non_transactional_count,unknown_direction_count,max_buyer_probability,last_role,last_direction,last_confidence&limit=1`);
      const context = actorContextFromRow(Array.isArray(rows) ? rows[0] : null);
      if (context) result.set(key, context);
    } catch (error) {
      console.warn("actor memory lookup failed", String(error));
    }
  }));
  return result;
}

async function recordActorObservation(item: any, assessment: PolicyAssessment, semantic: SemanticAssessment | null) {
  const authorId = String(item?.author_id || "").trim();
  if (!authorId) return;
  const role = semantic?.actor_role || assessment.actor_role || "unknown";
  const direction = semantic?.transaction_direction || policyDirection(assessment);
  const buyerProbability = semantic?.buyer_probability ?? (role === "buyer" ? Math.max(55, assessment.intent_score) : 0);
  const confidence = semantic?.confidence ?? assessment.confidence;
  try {
    await rest("rpc/lead_radar_record_actor_observation", {
      method: "POST",
      headers: { Prefer: "return=minimal" },
      body: JSON.stringify({
        p_source: actorPlatformSource(item.source),
        p_author_id: authorId,
        p_author_name: item.author_name || null,
        p_source_id: item.external_id || null,
        p_actor_role: role,
        p_direction: direction,
        p_buyer_probability: clampInt(buyerProbability, 0),
        p_confidence: clampInt(confidence, 0),
      }),
    });
  } catch (error) {
    console.warn("actor memory record failed", String(error));
  }
}

async function ingest(payload: any) {
  const rawItems = Array.isArray(payload?.items) ? payload.items : [];
  const items = rawItems.map(normalizeItem).filter(Boolean).slice(0, 100);
  const settings = await intelligenceSettings();
  const semanticActive = settings.enabled && settings.mode !== "off" && providerReady();
  const decisions: any[] = [];
  let stored = 0, filtered = 0, duplicates = 0, notified = 0;
  let semanticCandidates = 0, semanticCalls = 0, semanticCached = 0, semanticAccepted = 0, semanticRejected = 0, semanticUncertain = 0;
  const leadIds: number[] = [];
  const pending: any[] = [];

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
    const assessment = assessText(item.title, item.excerpt);
    const guardrail = hardGuardrail(assessment);
    if (guardrail.action === "reject") {
      filtered += 1;
      await updateSeen(Number(seen.id), "filtered", null, assessment);
      await recordActorObservation(item, assessment, null);
      decisions.push({
        external_id: item.external_id,
        disposition: "filtered",
        lead_id: null,
        assessment,
        intelligence: { route: "hard_guardrail", reason: guardrail.reason },
      });
      continue;
    }
    pending.push({ item, seen, dedupeKey, assessment, contentHash: await semanticContentHash(item) });
  }

  const actorMemory = await loadActorMemory(pending.map((entry) => entry.item));
  const semanticById = new Map<string, SemanticAssessment>();
  const cachedSemanticIds = new Set<string>();
  if (semanticActive && pending.length) {
    semanticCandidates = pending.length;
    const uncached: any[] = [];
    for (const entry of pending) {
      const key = String(entry.item.external_id || entry.contentHash);
      const cached = await cachedSemantic(entry.item, entry.contentHash, settings);
      if (cached) {
        semanticCached += 1;
        cachedSemanticIds.add(key);
        semanticById.set(key, cached);
      } else uncached.push(entry);
    }

    for (let offset = 0; offset < uncached.length; offset += settings.max_items_per_batch) {
      const chunk = uncached.slice(offset, offset + settings.max_items_per_batch);
      try {
        semanticCalls += 1;
        const classified = await classifySemanticBatch(chunk.map((entry) => ({
          id: String(entry.item.external_id || entry.contentHash),
          title: entry.item.title,
          excerpt: entry.item.excerpt,
          author_name: entry.item.author_name,
          content_kind: entry.item.content_kind,
          context_text: entry.item.context_text,
          actor_context: actorMemory.get(actorMemoryKey(entry.item)) || null,
        })), settings);
        for (const [id, semantic] of classified.entries()) semanticById.set(id, semantic);
      } catch (error) {
        console.warn("semantic classification failed; falling back to policy", String(error));
      }
    }
  }

  for (const entry of pending) {
    const { item, seen, dedupeKey } = entry;
    const semanticKey = String(item.external_id || entry.contentHash);
    let assessment: PolicyAssessment = entry.assessment;
    const semantic = semanticById.get(semanticKey) || null;
    const semanticDecision: SemanticDecision | null = semantic ? decideSemantic(semantic, settings) : null;

    if (semanticDecision === "accept") semanticAccepted += 1;
    else if (semanticDecision === "reject") semanticRejected += 1;
    else if (semanticDecision === "uncertain") semanticUncertain += 1;

    if (semantic && !cachedSemanticIds.has(semanticKey)) {
      await persistSemantic(item, entry.contentHash, settings, semantic, semanticDecision || "uncertain");
    }
    if (semantic) await recordActorObservation(item, assessment, semantic);

    if (settings.enabled && settings.mode === "enforce" && !semantic) {
      filtered += 1;
      await updateSeen(Number(seen.id), "error", null, assessment, null, null);
      decisions.push({
        external_id: item.external_id,
        disposition: "filtered",
        lead_id: null,
        assessment,
        semantic: null,
        semantic_decision: null,
        intelligence: { route: semanticActive ? "semantic_missing_retryable" : "semantic_unavailable_retryable" },
      });
      continue;
    }

    let shouldStore = assessment.is_lead;
    let dispositionReason = "policy";
    if (settings.enabled && settings.mode === "enforce") {
      shouldStore = semanticDecision === "accept";
      dispositionReason = `semantic:${semanticDecision || "missing"}`;
      if (semanticDecision === "accept" && semantic) assessment = mergeAcceptedSemantic(assessment, semantic);
    } else if (settings.enabled && settings.mode === "shadow" && semantic) {
      dispositionReason = `semantic_shadow:${semanticDecision}`;
    } else if (settings.enabled && !semanticActive) {
      dispositionReason = "semantic_unavailable_policy_fallback";
    }

    if (!shouldStore) {
      filtered += 1;
      await updateSeen(Number(seen.id), "filtered", null, assessment, semantic, semanticDecision);
      decisions.push({
        external_id: item.external_id,
        disposition: "filtered",
        lead_id: null,
        assessment,
        semantic,
        semantic_decision: semanticDecision,
        intelligence: { route: dispositionReason },
      });
      continue;
    }

    try {
      const lead = await insertLead(item, assessment, dedupeKey, semantic, settings);
      if (!lead?.id) throw new Error("lead insert returned no id");
      stored += 1;
      leadIds.push(Number(lead.id));
      await updateSeen(Number(seen.id), "stored", Number(lead.id), assessment, semantic, semanticDecision);
      if (await notifyLead(lead)) notified += 1;
      decisions.push({
        external_id: item.external_id,
        disposition: "stored",
        lead_id: Number(lead.id),
        assessment,
        semantic,
        semantic_decision: semanticDecision,
        intelligence: { route: dispositionReason },
        score: Number(lead.ai_score || 0),
      });
    } catch (error) {
      await updateSeen(Number(seen.id), "error", null, assessment, semantic, semanticDecision);
      decisions.push({ external_id: item.external_id, disposition: "error", lead_id: null, error: String(error).slice(0, 300) });
    }
  }

  const counts = {
    received: items.length,
    stored,
    filtered,
    duplicates,
    notified,
    lead_ids: leadIds,
    semantic_candidates: semanticCandidates,
    semantic_calls: semanticCalls,
    semantic_cached: semanticCached,
    semantic_accepted: semanticAccepted,
    semantic_rejected: semanticRejected,
    semantic_uncertain: semanticUncertain,
  };
  await recordQueryMetric(payload?.query_context || null, counts);
  await recordQueryRun(payload?.query_context || null, items, decisions, counts);
  return {
    ok: true,
    policy_version: POLICY_VERSION,
    retrieval_version: RETRIEVAL_VERSION,
    intelligence_version: INTELLIGENCE_VERSION,
    semantic: {
      enabled: settings.enabled,
      mode: settings.mode,
      provider: settings.provider,
      provider_ready: providerReady(),
      model: settings.model,
    },
    ...counts,
    decisions,
  };
}

Deno.serve(async (request) => {
  if (request.method === "OPTIONS") return new Response(null, { status: 204 });
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname.endsWith("/health")) {
    const settings = await intelligenceSettings();
    return json({
      ok: true,
      service: "lead-radar-ingest",
      policy_version: POLICY_VERSION,
      retrieval_version: RETRIEVAL_VERSION,
      intelligence_version: INTELLIGENCE_VERSION,
      semantic: {
        enabled: settings.enabled,
        mode: settings.mode,
        provider: settings.provider,
        provider_ready: providerReady(),
        model: settings.model,
      },
      auth: "server-to-server",
    });
  }
  if (!authorized(request)) return json({ detail: "Unauthorized" }, 401);
  if (request.method !== "POST" || !url.pathname.endsWith("/api/v1/ingest")) return json({ detail: "Not found" }, 404);
  try {
    return json(await ingest(await request.json()));
  } catch (error) {
    return json({ detail: String(error).slice(0, 500), policy_version: POLICY_VERSION, retrieval_version: RETRIEVAL_VERSION, intelligence_version: INTELLIGENCE_VERSION }, 500);
  }
});