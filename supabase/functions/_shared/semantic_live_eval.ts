import goldSet from "./semantic_gold_set.json" with { type: "json" };
import {
  classifySemanticBatch,
  decideSemantic,
  DEFAULT_SEMANTIC_MODEL,
  type IntelligenceSettings,
} from "./semantic_intent.ts";

const settings: IntelligenceSettings = {
  enabled: true,
  mode: "enforce",
  provider: "openai",
  model: (Deno.env.get("LEAD_SEMANTIC_MODEL") || DEFAULT_SEMANTIC_MODEL).trim() || DEFAULT_SEMANTIC_MODEL,
  buyer_threshold: Number(Deno.env.get("LEAD_BUYER_THRESHOLD") || 72),
  min_confidence: Number(Deno.env.get("LEAD_MIN_SEMANTIC_CONFIDENCE") || 65),
  reject_confidence: Number(Deno.env.get("LEAD_REJECT_SEMANTIC_CONFIDENCE") || 75),
  max_items_per_batch: 20,
};

if (!(Deno.env.get("OPENAI_API_KEY") || "").trim()) {
  console.error("OPENAI_API_KEY is required for live semantic evaluation");
  Deno.exit(2);
}

const candidates = (goldSet as any[]).map((sample) => ({
  id: String(sample.id),
  title: String(sample.title || ""),
  excerpt: String(sample.excerpt || ""),
  author_name: "",
}));
const results = await classifySemanticBatch(candidates, settings);
let tp = 0, fp = 0, tn = 0, fn = 0, actorCorrect = 0, directionCorrect = 0;
for (const sample of goldSet as any[]) {
  const semantic = results.get(String(sample.id));
  if (!semantic) {
    console.error(`missing semantic result for ${sample.id}`);
    fn += sample.label === "lead" ? 1 : 0;
    continue;
  }
  const decision = decideSemantic(semantic, settings);
  const predicted = decision === "accept";
  const expected = sample.label === "lead";
  if (predicted && expected) tp += 1;
  else if (predicted && !expected) fp += 1;
  else if (!predicted && expected) fn += 1;
  else tn += 1;
  if (semantic.actor_role === sample.actor_role) actorCorrect += 1;
  if (semantic.transaction_direction === sample.transaction_direction) directionCorrect += 1;
  console.log(JSON.stringify({
    id: sample.id,
    expected: sample.label,
    actor: semantic.actor_role,
    direction: semantic.transaction_direction,
    buyer_probability: semantic.buyer_probability,
    confidence: semantic.confidence,
    decision,
    reason: semantic.reason,
  }));
}
const precision = tp + fp ? tp / (tp + fp) : 1;
const recall = tp + fn ? tp / (tp + fn) : 1;
const f1 = precision + recall ? 2 * precision * recall / (precision + recall) : 0;
const actorAccuracy = actorCorrect / (goldSet as any[]).length;
const directionAccuracy = directionCorrect / (goldSet as any[]).length;
console.log(JSON.stringify({ model: settings.model, tp, fp, tn, fn, precision, recall, f1, actor_accuracy: actorAccuracy, direction_accuracy: directionAccuracy }, null, 2));

if (precision < 0.95 || recall < 0.90 || directionAccuracy < 0.90) Deno.exit(1);
