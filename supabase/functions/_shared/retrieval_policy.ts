import leadPolicyData from "./lead_policy.json" with { type: "json" };
import retrievalPolicyData from "./retrieval_policy.json" with { type: "json" };

export type RetrievalLane = "precision" | "discovery" | "broad" | "manual";

export interface QuerySpec {
  key: string;
  keyword: string;
  category: string;
  intent_family: string;
  topic_family: string;
  lane: RetrievalLane;
  prior: number;
}

export interface QueryMetric {
  runs: number;
  api_calls: number;
  returned_count: number;
  fresh_count: number;
  qualified_count: number;
  filtered_count: number;
  duplicate_count: number;
  human_positive_count: number;
  human_negative_count: number;
  last_run_at: string | null;
}

const leadPolicy = leadPolicyData as any;
const retrievalPolicy = retrievalPolicyData as any;

function numberValue(value: unknown, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function defaultMetric(): QueryMetric {
  return {
    runs: 0,
    api_calls: 0,
    returned_count: 0,
    fresh_count: 0,
    qualified_count: 0,
    filtered_count: 0,
    duplicate_count: 0,
    human_positive_count: 0,
    human_negative_count: 0,
    last_run_at: null,
  };
}

function laneWeight(lane: RetrievalLane) {
  if (lane === "manual") return 1;
  return numberValue(retrievalPolicy.scheduler?.lane_mix?.[lane], lane === "precision" ? 0.62 : lane === "discovery" ? 0.30 : 0.08);
}

function lanePrior(lane: RetrievalLane) {
  if (lane === "manual") return 1;
  return numberValue(retrievalPolicy.lanes?.[lane]?.prior, 1);
}

function queryTerms(topic: any): string[] {
  const result: string[] = [];
  for (const raw of topic?.query_terms || []) {
    const value = String(raw || "").trim();
    if (value && !result.includes(value)) result.push(value);
  }
  return result;
}

export function buildRetrievalPortfolio(): QuerySpec[] {
  const result: QuerySpec[] = [];
  const seen = new Set<string>();
  const aliasDecay = Math.max(0.1, Math.min(1, numberValue(retrievalPolicy.alias_prior_decay, 0.9)));

  for (const topic of leadPolicy.topics || []) {
    const topicKey = String(topic?.key || "").trim();
    const category = String(topic?.category || "其他开发");
    const topicPrior = numberValue(topic?.prior, 1);
    const terms = queryTerms(topic);
    if (!topicKey || !terms.length) continue;

    for (const family of leadPolicy.intent_families || []) {
      const familyKey = String(family?.key || "").trim();
      const familyPrior = numberValue(family?.prior, 1);
      const templates = (family?.query_templates || []).map(String).map((item: string) => item.trim()).filter(Boolean);
      if (!familyKey || !templates.length) continue;
      terms.forEach((term, termIndex) => {
        templates.forEach((template: string, templateIndex: number) => {
          const keyword = template.replaceAll("{topic}", term).trim();
          const signature = `precision|${keyword.toLowerCase()}`;
          if (!keyword || seen.has(signature)) return;
          seen.add(signature);
          const key = termIndex === 0
            ? `${familyKey}:${topicKey}:${templateIndex}`
            : `${familyKey}:${topicKey}:${templateIndex}:alias${termIndex}`;
          result.push({
            key,
            keyword,
            category,
            intent_family: familyKey,
            topic_family: topicKey,
            lane: "precision",
            prior: familyPrior * topicPrior * lanePrior("precision") * Math.pow(aliasDecay, termIndex),
          });
        });
      });
    }

    const discoveryTemplates = (retrievalPolicy.lanes?.discovery?.templates || []).map(String).filter(Boolean);
    terms.forEach((term, termIndex) => {
      discoveryTemplates.forEach((template: string, templateIndex: number) => {
        const keyword = template.replaceAll("{topic}", term).trim();
        const signature = `discovery|${keyword.toLowerCase()}`;
        if (!keyword || seen.has(signature)) return;
        seen.add(signature);
        result.push({
          key: `discovery:${topicKey}:${templateIndex}:${termIndex}`,
          keyword,
          category,
          intent_family: "discovery",
          topic_family: topicKey,
          lane: "discovery",
          prior: topicPrior * lanePrior("discovery") * Math.pow(aliasDecay, termIndex),
        });
      });
    });

    const broadTemplates = (retrievalPolicy.lanes?.broad?.templates || ["{topic}"]).map(String).filter(Boolean);
    terms.forEach((term, termIndex) => {
      broadTemplates.forEach((template: string, templateIndex: number) => {
        const keyword = template.replaceAll("{topic}", term).trim();
        const signature = `broad|${keyword.toLowerCase()}`;
        if (!keyword || seen.has(signature)) return;
        seen.add(signature);
        result.push({
          key: `broad:${topicKey}:${templateIndex}:${termIndex}`,
          keyword,
          category,
          intent_family: "discovery",
          topic_family: topicKey,
          lane: "broad",
          prior: topicPrior * lanePrior("broad") * Math.pow(aliasDecay, termIndex),
        });
      });
    });
  }

  return result;
}

export function queryScore(spec: QuerySpec, metricInput: QueryMetric | undefined, totalRuns: number, now = new Date()) {
  const metric = metricInput || defaultMetric();
  const runs = Math.max(0, numberValue(metric.runs));
  const apiCalls = Math.max(runs, numberValue(metric.api_calls, runs));
  const fresh = Math.max(0, numberValue(metric.fresh_count));
  const duplicates = Math.max(0, numberValue(metric.duplicate_count));
  const newUnique = Math.max(0, fresh - duplicates);
  const qualified = Math.max(0, numberValue(metric.qualified_count));
  const humanPositive = Math.max(0, numberValue(metric.human_positive_count));
  const humanNegative = Math.max(0, numberValue(metric.human_negative_count));

  const precision = (qualified + 0.5) / (newUnique + 2.0);
  const uniqueRate = (newUnique + 1.0) / (fresh + 2.0);
  const duplicateRate = (duplicates + 0.25) / (fresh + 1.0);
  const humanPrecision = (humanPositive + 1.0) / (humanPositive + humanNegative + 2.0);
  const yieldPerCall = (qualified + 0.5) / (apiCalls + 1.5);
  const yieldSignal = Math.tanh(yieldPerCall / 2.5);
  const exploration = numberValue(retrievalPolicy.scheduler?.exploration, 0.42) * Math.sqrt(Math.log(Math.max(0, totalRuns) + 2.0) / (runs + 1.0));

  const cooldownMinutes = Math.max(1, numberValue(retrievalPolicy.scheduler?.query_cooldown_minutes, 120));
  const saturationCooldown = Math.max(cooldownMinutes, numberValue(retrievalPolicy.scheduler?.saturation_cooldown_minutes, 360));
  const saturationThreshold = Math.max(0, Math.min(1, numberValue(retrievalPolicy.scheduler?.duplicate_saturation_threshold, 0.65)));
  let minutesSinceLast = Number.POSITIVE_INFINITY;
  if (metric.last_run_at) {
    const last = new Date(metric.last_run_at).getTime();
    if (Number.isFinite(last)) minutesSinceLast = Math.max(0, (now.getTime() - last) / 60000);
  }

  let cooldownFactor = 1;
  if (minutesSinceLast < cooldownMinutes) {
    cooldownFactor = 0.06 + 0.24 * (minutesSinceLast / cooldownMinutes);
  } else if (Number.isFinite(minutesSinceLast)) {
    cooldownFactor = 1 + Math.min(0.22, (minutesSinceLast - cooldownMinutes) / Math.max(cooldownMinutes * 8, 1));
  }

  let saturationFactor = 1;
  if (duplicateRate >= saturationThreshold && minutesSinceLast < saturationCooldown) {
    saturationFactor = 0.16;
  } else {
    saturationFactor = Math.max(0.35, 1 - Math.min(0.65, duplicateRate * 0.72));
  }

  const quality = 0.30 * precision + 0.23 * uniqueRate + 0.17 * humanPrecision + 0.15 * yieldSignal + 0.15 * exploration;
  return spec.prior * (0.38 + quality) * (0.65 + laneWeight(spec.lane)) * cooldownFactor * saturationFactor;
}

function preferredLane(now: Date): RetrievalLane {
  const interval = Math.max(1, numberValue(retrievalPolicy.scheduler?.interval_minutes, 15));
  const bucket = Math.floor(now.getTime() / (interval * 60000));
  const mix = retrievalPolicy.scheduler?.lane_mix || {};
  const cycle: RetrievalLane[] = [];
  const add = (lane: RetrievalLane, weight: unknown) => {
    const count = Math.max(1, Math.round(numberValue(weight, 0.1) * 10));
    for (let i = 0; i < count; i += 1) cycle.push(lane);
  };
  add("precision", mix.precision ?? 0.62);
  add("discovery", mix.discovery ?? 0.30);
  add("broad", mix.broad ?? 0.08);
  return cycle[bucket % cycle.length] || "precision";
}

export function chooseQueries(options: {
  now?: Date;
  count?: number;
  metrics?: Record<string, QueryMetric>;
  override?: string | null;
} = {}): QuerySpec[] {
  const override = options.override?.trim();
  if (override) {
    return [{ key: "manual", keyword: override, category: "manual", intent_family: "manual", topic_family: "manual", lane: "manual", prior: 1 }];
  }

  const count = Math.max(0, Math.floor(numberValue(options.count, 1)));
  if (!count) return [];
  const portfolio = buildRetrievalPortfolio();
  if (!portfolio.length) return [];
  const metrics = options.metrics || {};
  const now = options.now || new Date();
  const totalRuns = Object.values(metrics).reduce((sum, item) => sum + Math.max(0, numberValue(item?.runs)), 0);
  const ranked = [...portfolio].sort((a, b) => queryScore(b, metrics[b.key], totalRuns, now) - queryScore(a, metrics[a.key], totalRuns, now));
  const selected: QuerySpec[] = [];
  const usedTopics = new Set<string>();
  const usedKeys = new Set<string>();

  const pickFrom = (lane: RetrievalLane | null, preferNewTopic = true) => {
    const candidates = ranked.filter((spec) => !usedKeys.has(spec.key) && (!lane || spec.lane === lane));
    const chosen = (preferNewTopic ? candidates.find((spec) => !usedTopics.has(spec.topic_family)) : null) || candidates[0] || null;
    if (!chosen) return false;
    selected.push(chosen);
    usedKeys.add(chosen.key);
    usedTopics.add(chosen.topic_family);
    return true;
  };

  if (count === 1) {
    if (!pickFrom(preferredLane(now), false)) pickFrom(null, false);
    return selected;
  }

  const laneOrder: RetrievalLane[] = ["precision", "discovery", "broad"];
  for (const lane of laneOrder) {
    if (selected.length >= count) break;
    pickFrom(lane, true);
  }
  while (selected.length < Math.min(count, ranked.length)) {
    if (!pickFrom(null, true) && !pickFrom(null, false)) break;
  }
  return selected;
}

export function shouldFetchNextPage(options: {
  rawCount: number;
  oldestPublishedAt: string | null;
  hasMore: boolean | null;
  pagesFetched: number;
  providerCallsUsed: number;
  providerCallBudget: number;
  now?: Date;
}) {
  const scheduler = retrievalPolicy.scheduler || {};
  const maxPages = Math.max(1, Math.floor(numberValue(scheduler.max_pages_per_query, 2)));
  if (options.pagesFetched >= maxPages) return false;
  if (options.providerCallsUsed >= options.providerCallBudget) return false;
  if (options.hasMore === false) return false;
  if (Math.max(0, options.rawCount) < Math.max(1, numberValue(scheduler.min_page_fill_for_pagination, 16))) return false;
  if (!options.oldestPublishedAt) return false;
  const oldest = new Date(options.oldestPublishedAt).getTime();
  if (!Number.isFinite(oldest)) return false;
  const now = options.now || new Date();
  const ageMinutes = Math.max(0, (now.getTime() - oldest) / 60000);
  const frontier = Math.max(30, numberValue(retrievalPolicy.freshness_minutes, 1440)) + Math.max(0, numberValue(scheduler.page_frontier_margin_minutes, 60));
  return ageMinutes <= frontier;
}

export function retrievalLimits() {
  const scheduler = retrievalPolicy.scheduler || {};
  return {
    freshness_minutes: Math.max(30, numberValue(retrievalPolicy.freshness_minutes, 1440)),
    max_queries_web: Math.max(1, Math.floor(numberValue(scheduler.max_queries_web, 2))),
    max_queries_auto: Math.max(1, Math.floor(numberValue(scheduler.max_queries_auto, 1))),
    max_provider_calls_web: Math.max(1, Math.floor(numberValue(scheduler.max_provider_calls_web, 3))),
    max_provider_calls_auto: Math.max(1, Math.floor(numberValue(scheduler.max_provider_calls_auto, 1))),
  };
}

export const RETRIEVAL_VERSION = String(retrievalPolicy.version || "unknown");
