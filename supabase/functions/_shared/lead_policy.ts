import policyData from "./lead_policy.json" with { type: "json" };

export type ActorRole = "buyer" | "provider" | "recruiter" | "learner" | "content" | "unknown";
export type BuyingStage = "explicit" | "paid" | "considering" | "problem" | "none";

export interface PolicyAssessment {
  policy_version: string;
  actor_role: ActorRole;
  buying_stage: BuyingStage;
  is_lead: boolean;
  category: string;
  topic_hits: string[];
  intent_hits: string[];
  negative_hits: string[];
  intent_score: number;
  fit_score: number;
  actionability_score: number;
  confidence: number;
  reason_codes: string[];
  evidence: string[];
}

export interface QuerySpec {
  key: string;
  keyword: string;
  category: string;
  intent_family: string;
  topic_family: string;
  prior: number;
}

export interface QueryMetric {
  runs: number;
  fresh_count: number;
  qualified_count: number;
}

const policy = policyData as any;
const EXPLICIT_DIRECT_RE = /(?:找人|找开发|求开发|寻找|寻求|外包|谁能做|谁会做|有人接|有人能做)/i;

function escapeRegex(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function normalizedText(title: string, excerpt = "") {
  return `${title || ""} ${excerpt || ""}`.trim().toLowerCase();
}

function matchedTopics(text: string): any[] {
  return (policy.topics || []).filter((topic: any) =>
    (topic.terms || []).some((term: unknown) => text.includes(String(term).toLowerCase()))
  );
}

function matchedIntents(text: string): any[] {
  return (policy.intent_families || []).filter((family: any) =>
    (family.terms || []).some((term: unknown) => text.includes(String(term).toLowerCase()))
  );
}

function topicPattern() {
  const terms = [...new Set<string>((policy.topics || []).flatMap((topic: any) => (topic.terms || []).map((term: unknown) => String(term).toLowerCase())))]
    .sort((a, b) => b.length - a.length);
  return `(?:${terms.map(escapeRegex).join("|")})`;
}

function actorMatch(text: string): { role: ActorRole; labels: string[]; evidence: string[] } {
  for (const rule of policy.actor_rules || []) {
    for (const raw of rule.patterns || []) {
      if (new RegExp(String(raw), "i").test(text)) {
        const role = String(rule.role || "unknown") as ActorRole;
        const label = String(rule.label || role);
        return { role, labels: [label], evidence: [`actor:${label}`] };
      }
    }
  }
  return { role: "unknown", labels: [], evidence: [] };
}

function buyerMatch(text: string) {
  if (!matchedTopics(text).length) return false;
  const topic = topicPattern();
  return (policy.buyer_patterns || []).some((raw: unknown) =>
    new RegExp(String(raw).replaceAll("{topic}", topic), "i").test(text)
  );
}

function buyingStage(intentKeys: Set<string>, directBuyer: boolean, explicitDirect: boolean): BuyingStage {
  if (intentKeys.has("explicit_outsource") || explicitDirect) return "explicit";
  if (intentKeys.has("paid_request") && directBuyer) return "paid";
  if (intentKeys.has("build_intent") || directBuyer) return "considering";
  if (intentKeys.has("problem_help")) return "problem";
  return "none";
}

export function assessText(title: string, excerpt = ""): PolicyAssessment {
  const text = normalizedText(title, excerpt);
  const topics = matchedTopics(text);
  const intents = matchedIntents(text);
  const intentKeys = new Set<string>(intents.map((item: any) => String(item.key)));
  const actor = actorMatch(text);
  const directBuyer = buyerMatch(text);
  const explicitDirect = directBuyer && EXPLICIT_DIRECT_RE.test(text);
  let role: ActorRole = actor.role;
  if (role === "unknown" && directBuyer) role = "buyer";

  let intentScore = Math.min(100, intents.reduce((sum: number, item: any) => sum + Number(item.weight || 0), 0));
  if (directBuyer) {
    if (intentKeys.has("explicit_outsource") || explicitDirect) intentScore = Math.max(intentScore, 88);
    else if (intentKeys.has("paid_request")) intentScore = Math.max(intentScore, 82);
    else intentScore = Math.max(intentScore, 72);
  }

  const fitScore = topics.length ? 92 : 0;
  const stage = buyingStage(intentKeys, directBuyer, explicitDirect);
  const scoring = policy.scoring || {};
  let actionability = Number(scoring.unknown_actionability || 25);
  if (role === "buyer") {
    actionability = stage === "explicit"
      ? Number(scoring.explicit_actionability || 95)
      : ["paid", "considering", "problem"].includes(stage)
        ? Number(scoring.buyer_base_actionability || 78)
        : 65;
  } else if (role !== "unknown") actionability = 5;

  const isLead = role === "buyer" && topics.length > 0 && intentScore >= 55 && actionability >= 70;
  const topicHits: string[] = topics.map((item: any) => String(item.key));
  const intentHits: string[] = intents.map((item: any) => String(item.key));
  const category = topics.length ? String(topics[0].category) : "其他开发";
  const reasonCodes: string[] = [role === "buyer" ? "actor:buyer" : `actor:${role}`];
  if (directBuyer) reasonCodes.push("intent:direct_buyer");
  if (explicitDirect) reasonCodes.push("intent:explicit_search_language");
  reasonCodes.push(...intentHits.map((key: string) => `intent:${key}`));
  reasonCodes.push(...topicHits.map((key: string) => `topic:${key}`));
  reasonCodes.push(...actor.labels.map((label: string) => `exclude:${label}`));

  let confidence = 54 + (role === "buyer" ? 18 : 0) + (directBuyer ? 12 : 0);
  confidence += Math.min(12, intentHits.length * 4) + Math.min(9, topicHits.length * 3);
  if (!(["buyer", "unknown"] as ActorRole[]).includes(role)) confidence = Math.max(confidence, 90);

  return {
    policy_version: String(policy.version || "unknown"),
    actor_role: role,
    buying_stage: stage,
    is_lead: isLead,
    category,
    topic_hits: topicHits,
    intent_hits: intentHits,
    negative_hits: actor.labels,
    intent_score: Math.max(0, Math.min(100, intentScore)),
    fit_score: fitScore,
    actionability_score: Math.max(0, Math.min(100, actionability)),
    confidence: Math.max(0, Math.min(99, confidence)),
    reason_codes: reasonCodes,
    evidence: [...new Set<string>([...(role === "buyer" ? ["明确需求方表达"] : []), ...actor.evidence, ...intentHits.slice(0, 4), ...topicHits.slice(0, 3)])].slice(0, 8),
  };
}

export function buildQueryPortfolio(): QuerySpec[] {
  const result: QuerySpec[] = [];
  const seen = new Set<string>();
  for (const family of policy.intent_families || []) {
    const familyKey = String(family.key || "");
    const templates = (family.query_templates || []).map(String).filter(Boolean);
    if (!familyKey || !templates.length) continue;
    for (const topic of policy.topics || []) {
      const topicKey = String(topic.key || "");
      const topicTerm = String((topic.query_terms || [])[0] || "").trim();
      if (!topicKey || !topicTerm) continue;
      templates.forEach((template: string, index: number) => {
        const keyword = template.replaceAll("{topic}", topicTerm).trim();
        const signature = `${familyKey}|${keyword}`;
        if (!keyword || seen.has(signature)) return;
        seen.add(signature);
        result.push({
          key: `${familyKey}:${topicKey}:${index}`,
          keyword,
          category: String(topic.category || "其他开发"),
          intent_family: familyKey,
          topic_family: topicKey,
          prior: Number(family.prior || 1) * Number(topic.prior || 1),
        });
      });
    }
  }
  return result;
}

export function queryUcbScore(spec: QuerySpec, metric: QueryMetric, totalRuns: number) {
  const exploration = Number(policy.query_policy?.exploration || 0.35);
  const precision = (Number(metric.qualified_count || 0) + 0.5) / (Number(metric.fresh_count || 0) + 2.0);
  const explore = Math.sqrt(Math.log(totalRuns + 2.0) / (Number(metric.runs || 0) + 1.0));
  return spec.prior * (precision + exploration * explore);
}

export function chooseQuery(options: { now?: Date; metrics?: Record<string, QueryMetric>; override?: string | null } = {}): QuerySpec {
  if (options.override?.trim()) {
    return { key: "manual", keyword: options.override.trim(), category: "manual", intent_family: "manual", topic_family: "manual", prior: 1 };
  }
  const portfolio = buildQueryPortfolio();
  if (!portfolio.length) throw new Error("query portfolio is empty");
  const metrics = options.metrics || {};
  if (Object.keys(metrics).length) {
    const totalRuns = Object.values(metrics).reduce((sum, item) => sum + Number(item.runs || 0), 0);
    return [...portfolio].sort((a, b) => queryUcbScore(b, metrics[b.key] || { runs: 0, fresh_count: 0, qualified_count: 0 }, totalRuns) - queryUcbScore(a, metrics[a.key] || { runs: 0, fresh_count: 0, qualified_count: 0 }, totalRuns))[0];
  }
  const intervalMinutes = Number(policy.query_policy?.interval_minutes || 15);
  const bucket = Math.floor((options.now || new Date()).getTime() / (intervalMinutes * 60_000));
  const weighted: QuerySpec[] = [];
  portfolio.forEach((spec) => {
    const weight = Math.max(1, Math.round(spec.prior * 3));
    for (let i = 0; i < weight; i += 1) weighted.push(spec);
  });
  return weighted[bucket % weighted.length];
}

export const POLICY_VERSION = String(policy.version || "unknown");
