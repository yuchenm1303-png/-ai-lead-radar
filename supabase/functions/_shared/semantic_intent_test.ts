import { assessText, type PolicyAssessment } from "./lead_policy.ts";
import {
  buildSemanticRequest,
  decideSemantic,
  hardGuardrail,
  type IntelligenceSettings,
  type SemanticAssessment,
} from "./semantic_intent.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

const settings: IntelligenceSettings = {
  enabled: true,
  mode: "enforce",
  provider: "openai",
  model: "gpt-5.4-nano",
  buyer_threshold: 72,
  min_confidence: 65,
  reject_confidence: 75,
  max_items_per_batch: 20,
};

Deno.test("hard guardrail rejects only high-confidence negatives and routes ambiguous or unseen-topic candidates to semantics", () => {
  const provider = assessText("小程序接定制开发", "全行业都能做，需要的可以说一下你的需求，小程序商城网站开发都可以");
  const tutorial = assessText("老师傅私藏4个维修宝藏网站", "维修界百科全书，教程超详细，建议收藏");
  const realBuyer = assessText("寻找杭州本地小程序开发的公司或者个人", "因公司数字化升级需求，长期寻找杭州小程序定制开发正规技术公司");
  const noisyHelp = assessText("关西学院大学院出愿求助！！！", "学校网站填写出愿信息时不知道高校代码怎么填");
  const unseenTopic = assessText("找人用uniapp做个预约功能", "需要登录、日历和支付，预算可以沟通");
  const uncertainProvider: PolicyAssessment = { ...provider, confidence: 72 };

  assert(hardGuardrail(provider).action === "reject", `provider role=${provider.actor_role} confidence=${provider.confidence}`);
  assert(hardGuardrail(tutorial).action === "reject", `tutorial role=${tutorial.actor_role} confidence=${tutorial.confidence}`);
  assert(hardGuardrail(uncertainProvider).action === "semantic", "low-confidence provider rule must not bypass semantic review");
  assert(hardGuardrail(realBuyer).action === "semantic", `buyer role=${realBuyer.actor_role}`);
  assert(hardGuardrail(noisyHelp).action === "semantic", `noisy help should be semantic, role=${noisyHelp.actor_role}`);
  assert(unseenTopic.topic_hits.length === 0, "fixture must remain outside the keyword topic dictionary");
  assert(hardGuardrail(unseenTopic).action === "semantic", "unknown topic must not be rejected before semantic scope classification");
});

Deno.test("semantic decision requires transaction direction, probability, and confidence", () => {
  const buyer: SemanticAssessment = {
    id: "buyer",
    actor_role: "buyer",
    transaction_direction: "buy",
    buyer_probability: 97,
    confidence: 95,
    project_specificity: 88,
    reason: "company is seeking an external developer",
    evidence: ["寻找开发公司", "公司数字化升级"],
  };
  const provider: SemanticAssessment = {
    ...buyer,
    id: "provider",
    actor_role: "provider",
    transaction_direction: "sell",
    buyer_probability: 2,
    confidence: 97,
    reason: "author advertises development services",
  };
  const ambiguous: SemanticAssessment = {
    ...buyer,
    id: "ambiguous",
    buyer_probability: 61,
    confidence: 70,
    project_specificity: 40,
  };

  assert(decideSemantic(buyer, settings) === "accept", "real buyer should pass");
  assert(decideSemantic(provider, settings) === "reject", "provider should reject");
  assert(decideSemantic(ambiguous, settings) === "uncertain", "ambiguous content should remain review-only");
});

Deno.test("semantic request is batched and uses strict structured output", () => {
  const request = buildSemanticRequest([
    { id: "1", title: "寻找杭州本地小程序开发的公司或者个人", excerpt: "公司数字化升级需求", author_name: "小红薯" },
    { id: "2", title: "小程序接定制开发", excerpt: "全行业都能做", author_name: "开发工作室" },
  ], "gpt-5.4-nano") as any;
  assert(request.model === "gpt-5.4-nano", "model mismatch");
  assert(request.text?.format?.type === "json_schema", "structured output must use json_schema");
  assert(request.text?.format?.strict === true, "structured output must be strict");
  const input = JSON.parse(request.input);
  assert(input.items.length === 2, "batch should contain both candidates");
});
