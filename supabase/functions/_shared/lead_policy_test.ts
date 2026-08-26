import goldSet from "./gold_set.json" with { type: "json" };
import { assessText, buildQueryPortfolio } from "./lead_policy.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

Deno.test("lead policy passes shared gold set quality gate", () => {
  let tp = 0, fp = 0, tn = 0, fn = 0, actorCorrect = 0;
  for (const sample of goldSet as any[]) {
    const assessment = assessText(String(sample.title || ""), String(sample.excerpt || ""));
    const expected = sample.label === "lead";
    if (assessment.is_lead && expected) tp += 1;
    else if (assessment.is_lead && !expected) fp += 1;
    else if (!assessment.is_lead && expected) fn += 1;
    else tn += 1;
    if (assessment.actor_role === sample.actor_role) actorCorrect += 1;
  }
  const precision = tp + fp ? tp / (tp + fp) : 1;
  const recall = tp + fn ? tp / (tp + fn) : 1;
  const f1 = precision + recall ? 2 * precision * recall / (precision + recall) : 0;
  const actorAccuracy = actorCorrect / goldSet.length;
  assert(precision >= 0.95, `precision=${precision} tp=${tp} fp=${fp}`);
  assert(recall >= 0.95, `recall=${recall} tp=${tp} fn=${fn}`);
  assert(f1 >= 0.95, `f1=${f1}`);
  assert(actorAccuracy >= 0.80, `actor_accuracy=${actorAccuracy}`);
});

Deno.test("query portfolio never contains bare topic queries", () => {
  const portfolio = buildQueryPortfolio();
  assert(portfolio.length > 10, "query portfolio is unexpectedly small");
  for (const query of portfolio) {
    assert(query.intent_family !== "", `missing intent family for ${query.key}`);
    assert(query.topic_family !== "", `missing topic family for ${query.key}`);
    assert(query.keyword.trim().split(/\s+/).length >= 1, `empty query ${query.key}`);
    assert(!["网站", "小程序", "管理系统", "AI智能体", "自动化", "Python", "脚本"].includes(query.keyword.trim()), `bare topic query leaked: ${query.keyword}`);
  }
});
