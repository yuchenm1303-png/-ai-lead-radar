import type { ActorRole, PolicyAssessment } from "./lead_policy.ts";

export const INTELLIGENCE_VERSION = "3.4.0";
export type SemanticProvider = "openai" | "minimax";
export const DEFAULT_SEMANTIC_PROVIDER: SemanticProvider = "openai";
export const DEFAULT_SEMANTIC_MODELS: Record<SemanticProvider, string> = {
  openai: "gpt-5.4-nano",
  minimax: "MiniMax-M2.7",
};
export const DEFAULT_SEMANTIC_MODEL = DEFAULT_SEMANTIC_MODELS.openai;

export type TransactionDirection = "buy" | "sell" | "recruit" | "non_transactional" | "unknown";
export type SemanticDecision = "accept" | "reject" | "uncertain";
export type SemanticMode = "off" | "shadow" | "enforce";

export interface IntelligenceSettings {
  enabled: boolean;
  mode: SemanticMode;
  provider: SemanticProvider;
  model: string;
  buyer_threshold: number;
  min_confidence: number;
  reject_confidence: number;
  max_items_per_batch: number;
}

export interface SemanticCandidate {
  id: string;
  title: string;
  excerpt: string;
  author_name?: string | null;
  content_kind?: string | null;
  context_text?: string | null;
  actor_context?: Record<string, unknown> | null;
}

export interface SemanticAssessment {
  id: string;
  actor_role: ActorRole;
  transaction_direction: TransactionDirection;
  buyer_probability: number;
  confidence: number;
  project_specificity: number;
  reason: string;
  evidence: string[];
}

export interface GuardrailResult {
  action: "reject" | "semantic";
  reason: string;
}

const HARD_NEGATIVE_ROLES = new Set<ActorRole>(["provider", "recruiter", "learner", "content"]);
const HARD_GUARDRAIL_CONFIDENCE = 90;

function clamp(value: unknown, min = 0, max = 100, fallback = 0) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.max(min, Math.min(max, Math.round(number)));
}

function serviceKey() {
  const legacy = (Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "").trim();
  try {
    const keys = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}");
    return String(keys.default || legacy || "").trim();
  } catch {
    return legacy;
  }
}

function effectiveProvider(settings: IntelligenceSettings): SemanticProvider {
  if (settings.provider === "minimax" || /^minimax-/i.test(settings.model || "")) return "minimax";
  return "openai";
}

export function defaultSemanticModel(provider: SemanticProvider) {
  return DEFAULT_SEMANTIC_MODELS[provider];
}

export function providerReady(apiKey?: string | null) {
  if (String(apiKey || "").trim()) return true;
  if ((Deno.env.get("OPENAI_API_KEY") || "").trim()) return true;
  if ((Deno.env.get("MINIMAX_API_KEY") || "").trim()) return true;
  return Boolean((Deno.env.get("SUPABASE_URL") || "").trim() && serviceKey());
}

async function resolveSemanticCredential(
  settings: IntelligenceSettings,
  fetchImpl: typeof fetch,
) {
  const provider = effectiveProvider(settings);
  const envKey = provider === "minimax"
    ? (Deno.env.get("MINIMAX_API_KEY") || "").trim()
    : (Deno.env.get("OPENAI_API_KEY") || "").trim();
  if (envKey) return envKey;

  const sbUrl = (Deno.env.get("SUPABASE_URL") || "").trim();
  const secretKey = serviceKey();
  if (!sbUrl || !secretKey) return "";
  try {
    const headers: Record<string, string> = {
      apikey: secretKey,
      "Content-Type": "application/json",
    };
    if (secretKey.startsWith("ey")) headers.Authorization = `Bearer ${secretKey}`;
    const response = await fetchImpl(`${sbUrl}/rest/v1/rpc/lead_radar_get_intelligence_secret`, {
      method: "POST",
      headers,
      body: JSON.stringify({ p_provider: provider }),
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) return "";
    const text = await response.text();
    if (!text) return "";
    const parsed = JSON.parse(text);
    return typeof parsed === "string" ? parsed.trim() : "";
  } catch {
    return "";
  }
}

export function hardGuardrail(assessment: PolicyAssessment): GuardrailResult {
  if (HARD_NEGATIVE_ROLES.has(assessment.actor_role) && assessment.confidence >= HARD_GUARDRAIL_CONFIDENCE) {
    return { action: "reject", reason: `hard_actor:${assessment.actor_role}` };
  }
  return {
    action: "semantic",
    reason: assessment.topic_hits.length ? "semantic_required" : "semantic_scope_check",
  };
}

export function decideSemantic(assessment: SemanticAssessment, settings: IntelligenceSettings): SemanticDecision {
  const buyerProbability = clamp(assessment.buyer_probability);
  const confidence = clamp(assessment.confidence);
  if (
    assessment.actor_role === "buyer" &&
    assessment.transaction_direction === "buy" &&
    buyerProbability >= settings.buyer_threshold &&
    confidence >= settings.min_confidence
  ) return "accept";

  if (
    confidence >= settings.reject_confidence &&
    (HARD_NEGATIVE_ROLES.has(assessment.actor_role) || assessment.transaction_direction !== "buy")
  ) return "reject";

  if (confidence >= settings.reject_confidence && buyerProbability < Math.max(35, settings.buyer_threshold - 25)) {
    return "reject";
  }
  return "uncertain";
}

const RESPONSE_SCHEMA = {
  type: "object",
  properties: {
    items: {
      type: "array",
      items: {
        type: "object",
        properties: {
          id: { type: "string" },
          actor_role: { type: "string", enum: ["buyer", "provider", "recruiter", "learner", "content", "unknown"] },
          transaction_direction: { type: "string", enum: ["buy", "sell", "recruit", "non_transactional", "unknown"] },
          buyer_probability: { type: "integer", minimum: 0, maximum: 100 },
          confidence: { type: "integer", minimum: 0, maximum: 100 },
          project_specificity: { type: "integer", minimum: 0, maximum: 100 },
          reason: { type: "string" },
          evidence: { type: "array", items: { type: "string" } },
        },
        required: ["id", "actor_role", "transaction_direction", "buyer_probability", "confidence", "project_specificity", "reason", "evidence"],
        additionalProperties: false,
      },
    },
  },
  required: ["items"],
  additionalProperties: false,
} as const;

const SYSTEM_INSTRUCTIONS = `You are the buyer-intent classifier for a software-development lead radar.
Classify the AUTHOR'S actor role and transaction direction from the overall meaning, not by keyword overlap.
Actor role and transaction direction are separate dimensions.
For content_kind=comment, classify the COMMENT AUTHOR. context_text describes the parent post and is context only; never inherit the parent author's provider/buyer role.
actor_context is historical evidence about the same author from earlier observed posts/comments. Treat it as a PRIOR only: repeated provider/sell history is strong evidence against reading vague current text as buyer intent, but explicit current buy/sell intent always overrides history.
A buyer is an author who wants to commission, outsource, or hire a vendor/freelancer to build or modify software, a website, mini-program, app, automation, AI system, script, data solution, or a closely related custom digital product for the author's own real need. Direction=buy.
A provider advertises development services, solicits clients, shows cases, gives vendor-marketing advice, or says they can build software for others. Direction=sell.
Recruitment for employees/interns is recruiter. Direction=recruit.
Learning, tutorials, resource recommendations, commentary, entertainment, and ordinary troubleshooting are non-transactional. Use actor_role learner/content/unknown as appropriate and transaction_direction=non_transactional.
Use direction=unknown only when even the transactional nature is genuinely unclear.
General troubleshooting of an existing third-party website/app is not a software-development purchase unless the author is explicitly seeking custom development help.
Do not call something a buyer merely because it contains phrases such as 找开发, 求助, 小程序开发, 网站, 外包, or 报价. Determine who is buying from whom.
The buyer_probability field means the probability that this post represents demand for CUSTOM SOFTWARE/DEVELOPMENT SERVICES, not merely that the author is buying any product or service.
Examples:
- 寻找杭州本地小程序开发的公司或者个人；公司有数字化升级需求 => buyer / buy.
- 小程序接定制开发；全行业都能做；需要的可以说需求 => provider / sell.
- 求助：学校网站怎么填写；某网站打不开；网页游戏求助 => unknown/content / non_transactional, not buyer.
Return one result for every input id. Keep reasons short and evidence grounded in the text. Evidence should contain at most four short phrases.`;

function semanticInput(candidates: SemanticCandidate[]) {
  return candidates.map((item) => ({
    id: item.id,
    title: item.title.slice(0, 240),
    excerpt: item.excerpt.slice(0, 1600),
    author_name: String(item.author_name || "").slice(0, 120),
    content_kind: String(item.content_kind || "post").slice(0, 20),
    context_text: String(item.context_text || "").slice(0, 1200),
    actor_context: item.actor_context && typeof item.actor_context === "object" ? item.actor_context : null,
  }));
}

export function buildOpenAISemanticRequest(candidates: SemanticCandidate[], model: string) {
  return {
    model,
    store: false,
    instructions: SYSTEM_INSTRUCTIONS,
    input: JSON.stringify({ items: semanticInput(candidates) }),
    text: {
      format: {
        type: "json_schema",
        name: "lead_buyer_intent_batch",
        strict: true,
        schema: RESPONSE_SCHEMA,
      },
      verbosity: "low",
    },
  };
}

export function buildMiniMaxSemanticRequest(candidates: SemanticCandidate[], model: string) {
  const requiredShape = {
    items: [{
      id: "same input id",
      actor_role: "buyer|provider|recruiter|learner|content|unknown",
      transaction_direction: "buy|sell|recruit|non_transactional|unknown",
      buyer_probability: "integer 0-100",
      confidence: "integer 0-100",
      project_specificity: "integer 0-100",
      reason: "short reason",
      evidence: ["up to four short phrases"],
    }],
  };
  return {
    model,
    temperature: 0.1,
    max_completion_tokens: 6000,
    messages: [
      {
        role: "system",
        content: `${SYSTEM_INSTRUCTIONS}\nReturn ONLY one valid JSON object. No markdown fences and no prose outside JSON.`,
      },
      {
        role: "user",
        content: JSON.stringify({
          task: "Classify every item and return exactly one result per input id.",
          output_shape: requiredShape,
          items: semanticInput(candidates),
        }),
      },
    ],
  };
}

export function buildSemanticRequest(candidates: SemanticCandidate[], model: string) {
  return buildOpenAISemanticRequest(candidates, model);
}

function extractOpenAIOutputText(data: any) {
  if (typeof data?.output_text === "string" && data.output_text.trim()) return data.output_text.trim();
  return (Array.isArray(data?.output) ? data.output : [])
    .filter((item: any) => item?.type === "message")
    .flatMap((item: any) => Array.isArray(item?.content) ? item.content : [])
    .filter((item: any) => item?.type === "output_text" && typeof item?.text === "string")
    .map((item: any) => item.text)
    .join("")
    .trim();
}

function extractMiniMaxOutputText(data: any) {
  const content = data?.choices?.[0]?.message?.content;
  return typeof content === "string" ? content.trim() : "";
}

function stripJsonFence(value: string) {
  const text = value.trim();
  if (!text.startsWith("```")) return text;
  return text.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim();
}

function normalizeAssessment(value: any): SemanticAssessment | null {
  const id = String(value?.id || "").trim();
  const role = String(value?.actor_role || "unknown") as ActorRole;
  const direction = String(value?.transaction_direction || "unknown") as TransactionDirection;
  if (!id || !["buyer", "provider", "recruiter", "learner", "content", "unknown"].includes(role)) return null;
  if (!["buy", "sell", "recruit", "non_transactional", "unknown"].includes(direction)) return null;
  return {
    id,
    actor_role: role,
    transaction_direction: direction,
    buyer_probability: clamp(value?.buyer_probability),
    confidence: clamp(value?.confidence),
    project_specificity: clamp(value?.project_specificity),
    reason: String(value?.reason || "").trim().slice(0, 220),
    evidence: (Array.isArray(value?.evidence) ? value.evidence : []).map(String).map((item: string) => item.trim().slice(0, 100)).filter(Boolean).slice(0, 4),
  };
}

function parseSemanticOutput(output: string) {
  let parsed: any;
  try { parsed = JSON.parse(stripJsonFence(output)); } catch { throw new Error("semantic structured output was invalid JSON"); }
  const result = new Map<string, SemanticAssessment>();
  for (const value of Array.isArray(parsed?.items) ? parsed.items : []) {
    const normalized = normalizeAssessment(value);
    if (normalized) result.set(normalized.id, normalized);
  }
  return result;
}

async function classifyOpenAI(
  candidates: SemanticCandidate[],
  settings: IntelligenceSettings,
  apiKey: string,
  fetchImpl: typeof fetch,
) {
  const response = await fetchImpl("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
    body: JSON.stringify(buildOpenAISemanticRequest(candidates, settings.model || defaultSemanticModel("openai"))),
    signal: AbortSignal.timeout(25000),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`openai semantic provider ${response.status}: ${text.slice(0, 240)}`);
  let data: any;
  try { data = JSON.parse(text); } catch { throw new Error("openai semantic provider returned invalid JSON"); }
  const output = extractOpenAIOutputText(data);
  if (!output) throw new Error("openai semantic provider returned no structured output");
  return parseSemanticOutput(output);
}

async function classifyMiniMax(
  candidates: SemanticCandidate[],
  settings: IntelligenceSettings,
  apiKey: string,
  fetchImpl: typeof fetch,
) {
  const response = await fetchImpl("https://api.minimaxi.com/v1/text/chatcompletion_v2", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(buildMiniMaxSemanticRequest(candidates, settings.model || defaultSemanticModel("minimax"))),
    signal: AbortSignal.timeout(30000),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`minimax semantic provider ${response.status}: ${text.slice(0, 240)}`);
  let data: any;
  try { data = JSON.parse(text); } catch { throw new Error("minimax semantic provider returned invalid JSON"); }
  if (Number(data?.base_resp?.status_code || 0) !== 0) {
    throw new Error(`minimax semantic business error: ${String(data?.base_resp?.status_msg || "unknown").slice(0, 200)}`);
  }
  const output = extractMiniMaxOutputText(data);
  if (!output) throw new Error("minimax semantic provider returned no text output");
  return parseSemanticOutput(output);
}


export function requireCompleteSemanticCoverage(
  candidates: SemanticCandidate[],
  result: Map<string, SemanticAssessment>,
) {
  const missing = candidates
    .map((candidate) => String(candidate.id || "").trim())
    .filter((id) => id && !result.has(id));
  if (missing.length) throw new Error(`semantic provider omitted ${missing.length} item(s): ${missing.slice(0, 4).join(",")}`);
  return result;
}

export async function classifySemanticBatch(
  candidates: SemanticCandidate[],
  settings: IntelligenceSettings,
  apiKey?: string | null,
  fetchImpl: typeof fetch = fetch,
): Promise<Map<string, SemanticAssessment>> {
  if (!settings.enabled || settings.mode === "off" || !candidates.length) {
    return new Map<string, SemanticAssessment>();
  }
  const provider = effectiveProvider(settings);
  const credential = String(apiKey || await resolveSemanticCredential({ ...settings, provider }, fetchImpl) || "").trim();
  if (!credential) return new Map<string, SemanticAssessment>();
  const result = provider === "minimax"
    ? await classifyMiniMax(candidates, { ...settings, provider }, credential, fetchImpl)
    : await classifyOpenAI(candidates, { ...settings, provider }, credential, fetchImpl);
  return requireCompleteSemanticCoverage(candidates, result);
}
