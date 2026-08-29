import retrievalPolicyData from "./retrieval_policy.json" with { type: "json" };

export type RetrievalLane = "exploit" | "explore" | "expand" | "manual";

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

type PairKey = { archetype: string; topic: string };

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

function cloneMetric(metric: QueryMetric): QueryMetric {
  return { ...metric };
}

function addMetric(target: QueryMetric, source: QueryMetric, weight = 1) {
  target.runs += source.runs * weight;
  target.api_calls += source.api_calls * weight;
  target.returned_count += source.returned_count * weight;
  target.fresh_count += source.fresh_count * weight;
  target.qualified_count += source.qualified_count * weight;
  target.filtered_count += source.filtered_count * weight;
  target.duplicate_count += source.duplicate_count * weight;
  target.human_positive_count += source.human_positive_count * weight;
  target.human_negative_count += source.human_negative_count * weight;
  return target;
}

function topicConfigEntries() {
  return Object.entries(retrievalPolicy.topics || {}) as [string, any][];
}

function archetypeConfigEntries() {
  return Object.entries(retrievalPolicy.archetypes || {}) as [string, any][];
}

function uniqueStrings(value: unknown): string[] {
  const result: string[] = [];
  for (const raw of Array.isArray(value) ? value : []) {
    const text = String(raw || "").trim();
    if (text && !result.includes(text)) result.push(text);
  }
  return result;
}

export function buildRetrievalPortfolio(): QuerySpec[] {
  const result: QuerySpec[] = [];
  const seenKeywords = new Set<string>();
  const aliasDecay = Math.max(0.1, Math.min(1, numberValue(retrievalPolicy.alias_prior_decay, 0.92)));

  for (const [archetypeKey, archetype] of archetypeConfigEntries()) {
    const templates = uniqueStrings(archetype?.templates);
    const archetypePrior = Math.max(0.05, numberValue(archetype?.prior, 1));
    if (!archetypeKey || !templates.length) continue;

    for (const [topicKey, topic] of topicConfigEntries()) {
      const terms = uniqueStrings(topic?.terms);
      const category = String(topic?.category || "其他开发");
      const topicPrior = Math.max(0.05, numberValue(topic?.prior, 1));
      if (!topicKey || !terms.length) continue;

      terms.forEach((term, termIndex) => {
        templates.forEach((template, templateIndex) => {
          const keyword = template.replaceAll("{topic}", term).replace(/\s+/g, " ").trim();
          const signature = keyword.toLowerCase();
          if (!keyword || seenKeywords.has(signature)) return;
          seenKeywords.add(signature);
          result.push({
            key: `v3:${archetypeKey}:${topicKey}:${templateIndex}:${termIndex}`,
            keyword,
            category,
            intent_family: archetypeKey,
            topic_family: topicKey,
            lane: "explore",
            prior: archetypePrior * topicPrior * Math.pow(aliasDecay, termIndex),
          });
        });
      });
    }
  }

  return result;
}

function pairFromKey(key: string): PairKey | null {
  const parts = String(key || "").split(":");
  if (parts[0] === "v3" && parts[1] && parts[2]) {
    return { archetype: parts[1], topic: parts[2] };
  }
  const mapped = String(retrievalPolicy.legacy_archetype_map?.[parts[0]] || "");
  if (mapped && parts[1]) return { archetype: mapped, topic: parts[1] };
  return null;
}

function relatedMetric(spec: QuerySpec, metrics: Record<string, QueryMetric>) {
  const related = defaultMetric();
  for (const [key, metric] of Object.entries(metrics)) {
    if (key === spec.key) continue;
    const pair = pairFromKey(key);
    if (pair?.archetype === spec.intent_family && pair.topic === spec.topic_family) addMetric(related, metric);
  }
  return related;
}

function effectiveMetric(spec: QuerySpec, metrics: Record<string, QueryMetric>) {
  const exact = cloneMetric(metrics[spec.key] || defaultMetric());
  const groupWeight = Math.max(0, Math.min(1, numberValue(retrievalPolicy.scheduler?.group_history_weight, 0.35)));
  return addMetric(exact, relatedMetric(spec, metrics), groupWeight);
}

function metricSignals(metric: QueryMetric) {
  const apiCalls = Math.max(0, numberValue(metric.api_calls, metric.runs));
  const fresh = Math.max(0, numberValue(metric.fresh_count));
  const duplicates = Math.max(0, numberValue(metric.duplicate_count));
  const newUnique = Math.max(0, fresh - duplicates);
  const qualified = Math.max(0, numberValue(metric.qualified_count));
  const filtered = Math.max(0, numberValue(metric.filtered_count));
  const humanPositive = Math.max(0, numberValue(metric.human_positive_count));
  const humanNegative = Math.max(0, numberValue(metric.human_negative_count));

  const buyerEvidence = qualified + humanPositive * 1.75;
  const precision = (buyerEvidence + 0.75) / (newUnique + humanPositive + humanNegative + 3.0);
  const buyerYieldPerCall = (buyerEvidence + 0.5) / (apiCalls + 1.5);
  const humanPrecision = (humanPositive + 1.0) / (humanPositive + humanNegative + 2.0);
  const freshPerCall = fresh / Math.max(1, apiCalls);
  const freshnessSignal = Math.tanh(freshPerCall / 10.0);
  const duplicateRate = (duplicates + 0.25) / (fresh + 1.0);
  const noiseRate = (filtered + humanNegative) / (fresh + humanPositive + humanNegative + 2.0);
  const providerNoisePenalty = Math.max(0, numberValue(retrievalPolicy.scheduler?.provider_noise_penalty, 0.22));
  const duplicatePenalty = Math.max(0, numberValue(retrievalPolicy.scheduler?.duplicate_penalty, 0.28));
  const quality =
    0.36 * precision +
    0.24 * Math.tanh(buyerYieldPerCall / 1.5) +
    0.18 * humanPrecision +
    0.22 * freshnessSignal -
    providerNoisePenalty * Math.min(1, noiseRate) -
    duplicatePenalty * Math.min(1, duplicateRate);

  return {
    apiCalls,
    fresh,
    duplicates,
    newUnique,
    qualified,
    filtered,
    humanPositive,
    humanNegative,
    buyerEvidence,
    precision,
    buyerYieldPerCall,
    humanPrecision,
    freshnessSignal,
    duplicateRate,
    noiseRate,
    quality,
  };
}

function minutesSince(value: string | null, now: Date) {
  if (!value) return Number.POSITIVE_INFINITY;
  const timestamp = new Date(value).getTime();
  return Number.isFinite(timestamp) ? Math.max(0, (now.getTime() - timestamp) / 60000) : Number.POSITIVE_INFINITY;
}

function cooldownFactor(metric: QueryMetric, now: Date) {
  const cooldown = Math.max(1, numberValue(retrievalPolicy.scheduler?.query_cooldown_minutes, 120));
  const elapsed = minutesSince(metric.last_run_at, now);
  if (!Number.isFinite(elapsed)) return 1;
  if (elapsed < cooldown) return 0.05 + 0.25 * (elapsed / cooldown);
  return 1 + Math.min(0.18, (elapsed - cooldown) / Math.max(cooldown * 10, 1));
}

function saturationFactor(metric: QueryMetric, now: Date) {
  const signals = metricSignals(metric);
  const threshold = Math.max(0, Math.min(1, numberValue(retrievalPolicy.scheduler?.duplicate_saturation_threshold, 0.65)));
  const cooldown = Math.max(1, numberValue(retrievalPolicy.scheduler?.saturation_cooldown_minutes, 360));
  if (signals.duplicateRate >= threshold && minutesSince(metric.last_run_at, now) < cooldown) return 0.14;
  return Math.max(0.34, 1 - Math.min(0.66, signals.duplicateRate * 0.72));
}

export function queryScore(spec: QuerySpec, metricInput: QueryMetric | undefined, totalRuns: number, now = new Date()) {
  const metric = metricInput || defaultMetric();
  const signals = metricSignals(metric);
  const exploration = Math.max(0, numberValue(retrievalPolicy.scheduler?.exploration, 0.55)) *
    Math.sqrt(Math.log(Math.max(0, totalRuns) + 2.0) / (Math.max(0, metric.runs) + 1.0));
  return spec.prior * (0.72 + signals.quality + 0.12 * exploration) * cooldownFactor(metric, now) * saturationFactor(metric, now);
}

function exploitScore(spec: QuerySpec, metrics: Record<string, QueryMetric>, totalRuns: number, now: Date) {
  const exact = metrics[spec.key] || defaultMetric();
  const effective = effectiveMetric(spec, metrics);
  const signals = metricSignals(effective);
  const exploration = Math.max(0, numberValue(retrievalPolicy.scheduler?.exploration, 0.55)) *
    Math.sqrt(Math.log(Math.max(0, totalRuns) + 2.0) / (Math.max(0, exact.runs) + 1.0));
  return spec.prior * (0.82 + signals.quality + 0.08 * exploration) * cooldownFactor(exact, now) * saturationFactor(exact, now);
}

function exploreScore(spec: QuerySpec, metrics: Record<string, QueryMetric>, totalRuns: number, now: Date) {
  const exact = metrics[spec.key] || defaultMetric();
  const effective = effectiveMetric(spec, metrics);
  const quality = Math.max(-0.4, metricSignals(effective).quality);
  const uncertainty = Math.sqrt(Math.log(Math.max(0, totalRuns) + 3.0) / (Math.max(0, exact.runs) + 1.0));
  const exploration = Math.max(0.05, numberValue(retrievalPolicy.scheduler?.exploration, 0.55));
  return spec.prior * (0.68 + exploration * uncertainty + 0.18 * Math.max(0, quality)) * cooldownFactor(exact, now);
}

function aggregatePairMetric(pair: PairKey, metrics: Record<string, QueryMetric>) {
  const aggregate = defaultMetric();
  for (const [key, metric] of Object.entries(metrics)) {
    const meta = pairFromKey(key);
    if (meta?.archetype === pair.archetype && meta.topic === pair.topic) addMetric(aggregate, metric);
  }
  return aggregate;
}

function winningPair(portfolio: QuerySpec[], metrics: Record<string, QueryMetric>): PairKey | null {
  const seen = new Set<string>();
  let best: { pair: PairKey; score: number } | null = null;
  for (const spec of portfolio) {
    const signature = `${spec.intent_family}|${spec.topic_family}`;
    if (seen.has(signature)) continue;
    seen.add(signature);
    const pair = { archetype: spec.intent_family, topic: spec.topic_family };
    const metric = aggregatePairMetric(pair, metrics);
    const signals = metricSignals(metric);
    if (signals.qualified + signals.humanPositive <= 0) continue;
    const score = signals.quality + 0.08 * Math.log1p(signals.qualified + 2 * signals.humanPositive);
    if (!best || score > best.score) best = { pair, score };
  }
  return best?.pair || null;
}

function expansionScore(spec: QuerySpec, winner: PairKey | null, metrics: Record<string, QueryMetric>, totalRuns: number, now: Date) {
  if (!winner) return exploreScore(spec, metrics, totalRuns, now);
  const exact = metrics[spec.key] || defaultMetric();
  const sameArchetype = spec.intent_family === winner.archetype;
  const sameTopic = spec.topic_family === winner.topic;
  let adjacency = 0.20;
  if (sameArchetype && sameTopic) adjacency = 0.62;
  else if (sameArchetype) adjacency = 1.00;
  else if (sameTopic) adjacency = 0.88;
  const novelty = 1 / Math.sqrt(Math.max(0, exact.runs) + 1.0);
  const quality = Math.max(0, metricSignals(effectiveMetric(spec, metrics)).quality);
  return spec.prior * (0.55 + adjacency + 0.48 * novelty + 0.12 * quality) * cooldownFactor(exact, now);
}

function chooseCandidate(
  ranked: QuerySpec[],
  usedKeys: Set<string>,
  predicate: (spec: QuerySpec) => boolean,
) {
  return ranked.find((spec) => !usedKeys.has(spec.key) && predicate(spec)) || null;
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
  const totalRuns = Object.values(metrics).reduce((sum, metric) => sum + Math.max(0, numberValue(metric?.runs)), 0);
  const usedKeys = new Set<string>();
  const selected: QuerySpec[] = [];

  const add = (spec: QuerySpec | null, lane: RetrievalLane) => {
    if (!spec || usedKeys.has(spec.key)) return false;
    usedKeys.add(spec.key);
    selected.push({ ...spec, lane });
    return true;
  };

  const exploitRanked = [...portfolio].sort((a, b) => exploitScore(b, metrics, totalRuns, now) - exploitScore(a, metrics, totalRuns, now));
  add(exploitRanked[0] || null, "exploit");
  if (selected.length >= count) return selected;

  const first = selected[0];
  const exploreRanked = [...portfolio].sort((a, b) => exploreScore(b, metrics, totalRuns, now) - exploreScore(a, metrics, totalRuns, now));
  const explore =
    chooseCandidate(exploreRanked, usedKeys, (spec) => spec.topic_family !== first.topic_family && spec.intent_family !== first.intent_family) ||
    chooseCandidate(exploreRanked, usedKeys, (spec) => spec.topic_family !== first.topic_family) ||
    chooseCandidate(exploreRanked, usedKeys, () => true);
  add(explore, "explore");
  if (selected.length >= count) return selected;

  const winner = winningPair(portfolio, metrics);
  const expandRanked = [...portfolio].sort((a, b) => expansionScore(b, winner, metrics, totalRuns, now) - expansionScore(a, winner, metrics, totalRuns, now));
  const expand = winner
    ? chooseCandidate(expandRanked, usedKeys, (spec) =>
      (spec.intent_family === winner.archetype || spec.topic_family === winner.topic) &&
      !(spec.intent_family === winner.archetype && spec.topic_family === winner.topic)) ||
      chooseCandidate(expandRanked, usedKeys, () => true)
    : chooseCandidate(expandRanked, usedKeys, (spec) => !selected.some((item) => item.topic_family === spec.topic_family)) ||
      chooseCandidate(expandRanked, usedKeys, () => true);
  add(expand, "expand");

  while (selected.length < Math.min(count, portfolio.length)) {
    if (!add(chooseCandidate(exploreRanked, usedKeys, () => true), "explore")) break;
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
    max_queries_web: Math.max(1, Math.floor(numberValue(scheduler.max_queries_web, 3))),
    max_queries_auto: Math.max(1, Math.floor(numberValue(scheduler.max_queries_auto, 1))),
    max_provider_calls_web: Math.max(1, Math.floor(numberValue(scheduler.max_provider_calls_web, 3))),
    max_provider_calls_auto: Math.max(1, Math.floor(numberValue(scheduler.max_provider_calls_auto, 1))),
  };
}

export const RETRIEVAL_VERSION = String(retrievalPolicy.version || "unknown");
