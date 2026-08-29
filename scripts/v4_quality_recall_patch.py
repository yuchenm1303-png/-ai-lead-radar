from pathlib import Path
import json


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)


# Retrieval version + source policy stays at the same provider-call ceiling.
policy_path = Path("supabase/functions/_shared/retrieval_policy.json")
policy = json.loads(policy_path.read_text(encoding="utf-8"))
policy["version"] = "4.1.0"
policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

for path in ["supabase/functions/_shared/retrieval_policy_test.ts", "backend/tests/test_retrieval_v3.py"]:
    text = read(path)
    text = text.replace('"4.0.0"', '"4.1.0"')
    write(path, text)

# Conversation acquisition: reserve the comment call only when a verified anchor exists.
path = "supabase/functions/_shared/conversation_retrieval.ts"
text = read(path)
text = replace_once(
    text,
    '''  assessment?: { actor_role?: string | null; confidence?: number | null } | null;''',
    '''  assessment?: {
    actor_role?: string | null;
    confidence?: number | null;
    buying_stage?: string | null;
    reason_codes?: string[] | null;
    negative_hits?: string[] | null;
  } | null;''',
    "anchor assessment fields",
)
text = replace_once(
    text,
    '''  policy: SourcePortfolioPolicy,
): ProviderCallAllocation {''',
    '''  policy: SourcePortfolioPolicy,
  conversationAvailable = true,
): ProviderCallAllocation {''',
    "allocation signature",
)
text = replace_once(
    text,
    '''  const requested = requestedFrom === "auto"
    ? clampInt(policy.auto_conversation_calls, 0, 0, budget)
    : budget >= clampInt(policy.min_manual_provider_budget, 3, 1, 100)
    ? clampInt(policy.manual_conversation_calls, 1, 0, budget)
    : 0;''',
    '''  const requested = !conversationAvailable
    ? 0
    : requestedFrom === "auto"
    ? clampInt(policy.auto_conversation_calls, 0, 0, budget)
    : budget >= clampInt(policy.min_manual_provider_budget, 3, 1, 100)
    ? clampInt(policy.manual_conversation_calls, 1, 0, budget)
    : 0;''',
    "allocation availability",
)
verify_helpers = '''\nfunction assessmentReasonCodes(post: ConversationAnchor) {
  return new Set((Array.isArray(post.assessment?.reason_codes) ? post.assessment?.reason_codes : []).map((value) => String(value)));
}

function verifiedConversationAnchor(post: ConversationAnchor, roles: Set<string>) {
  const semanticRole = String(post.semantic?.actor_role || "").toLowerCase();
  if (post.semantic) {
    if (!roles.has(semanticRole) || Number(post.semantic.confidence || 0) < 75) return false;
    const direction = directionOf(post);
    if (semanticRole === "provider") return direction === "sell";
    if (semanticRole === "content") return direction === "non_transactional" || direction === "unknown";
    return false;
  }

  const policyRole = String(post.assessment?.actor_role || "").toLowerCase();
  if (!roles.has(policyRole)) return false;
  if (String(post.decision || "") !== "filtered") return false;
  if (Number(post.assessment?.confidence || 0) < 88) return false;
  const reasons = assessmentReasonCodes(post);
  const contradictoryBuyer = reasons.has("actor:buyer") || reasons.has("intent:direct_buyer") || reasons.has("intent:explicit_search_language");
  if (contradictoryBuyer || String(post.assessment?.buying_stage || "") === "explicit") return false;
  if (!reasons.has(`actor:${policyRole}`)) return false;
  return true;
}
'''
text = replace_once(text, "function publishedAgeMinutes(post: ConversationAnchor, now: Date) {", verify_helpers + "\nfunction publishedAgeMinutes(post: ConversationAnchor, now: Date) {", "verified anchor helpers")
text = replace_once(
    text,
    '''    const role = roleOf(post);
    if (!roles.has(role)) return false;
    if (role === "provider" && post.semantic && directionOf(post) !== "sell") return false;
    return true;''',
    '''    if (!verifiedConversationAnchor(post, roles)) return false;
    return true;''',
    "anchor eligibility",
)
write(path, text)

path = "supabase/functions/_shared/conversation_retrieval_test.ts"
text = read(path)
text = replace_once(
    text,
    '''  const small = allocateProviderCalls("web", 2, false, policy);
  assert(small.search_calls === 2 && small.conversation_calls === 0, JSON.stringify(small));''',
    '''  const small = allocateProviderCalls("web", 2, false, policy);
  assert(small.search_calls === 2 && small.conversation_calls === 0, JSON.stringify(small));
  const noAnchor = allocateProviderCalls("web", 3, false, policy, false);
  assert(noAnchor.search_calls === 3 && noAnchor.conversation_calls === 0, JSON.stringify(noAnchor));''',
    "no anchor allocation test",
)
text += '''\nDeno.test("conversation anchor rejects contradictory historical provider snapshots", () => {
  const now = new Date("2026-08-29T12:00:00Z");
  const contradictory: any = {
    id: "old-misclassified-buyer",
    title: "寻找杭州本地小程序开发的公司或者个人",
    decision: "filtered",
    published_at: "2026-08-28T10:00:00Z",
    metrics: { comments: 50 },
    assessment: {
      actor_role: "provider",
      confidence: 90,
      buying_stage: "explicit",
      reason_codes: ["actor:provider", "intent:direct_buyer", "intent:explicit_search_language"],
      negative_hits: ["服务商自推广"],
    },
  };
  const verifiedProvider: any = {
    id: "verified-provider",
    title: "专业承接小程序开发",
    decision: "filtered",
    published_at: "2026-08-29T10:00:00Z",
    metrics: { comments: 3 },
    assessment: {
      actor_role: "provider",
      confidence: 95,
      buying_stage: "none",
      reason_codes: ["actor:provider", "exclude:服务商自推广"],
      negative_hits: ["服务商自推广"],
    },
  };
  const anchor = selectConversationAnchor([contradictory, verifiedProvider], {
    now,
    cooldownIds: new Set(),
    maxAgeMinutes: policy.anchor_max_age_minutes,
    minComments: 1,
    preferredRoles: policy.preferred_roles,
  });
  assert(anchor?.id === "verified-provider", `unexpected anchor=${anchor?.id}`);
});\n'''
write(path, text)

# Semantic intelligence: enforce complete batch coverage; never silently omit an item.
path = "supabase/functions/_shared/semantic_intent.ts"
text = read(path)
text = replace_once(text, 'export const INTELLIGENCE_VERSION = "3.3.0";', 'export const INTELLIGENCE_VERSION = "3.4.0";', "intelligence version")
coverage_helper = '''\nexport function requireCompleteSemanticCoverage(
  candidates: SemanticCandidate[],
  result: Map<string, SemanticAssessment>,
) {
  const missing = candidates
    .map((candidate) => String(candidate.id || "").trim())
    .filter((id) => id && !result.has(id));
  if (missing.length) throw new Error(`semantic provider omitted ${missing.length} item(s): ${missing.slice(0, 4).join(",")}`);
  return result;
}
'''
text = replace_once(text, "export async function classifySemanticBatch(", coverage_helper + "\nexport async function classifySemanticBatch(", "semantic coverage helper")
text = replace_once(
    text,
    '''  if (!credential) return new Map<string, SemanticAssessment>();
  if (provider === "minimax") return await classifyMiniMax(candidates, { ...settings, provider }, credential, fetchImpl);
  return await classifyOpenAI(candidates, { ...settings, provider }, credential, fetchImpl);''',
    '''  if (!credential) return new Map<string, SemanticAssessment>();
  const result = provider === "minimax"
    ? await classifyMiniMax(candidates, { ...settings, provider }, credential, fetchImpl)
    : await classifyOpenAI(candidates, { ...settings, provider }, credential, fetchImpl);
  return requireCompleteSemanticCoverage(candidates, result);''',
    "semantic complete coverage",
)
write(path, text)

path = "supabase/functions/_shared/semantic_intent_test.ts"
text = read(path)
text = replace_once(text, "  hardGuardrail,\n", "  hardGuardrail,\n  requireCompleteSemanticCoverage,\n", "semantic test import")
text += '''\nDeno.test("semantic coverage must include every candidate before enforce can trust the batch", () => {
  const candidates = [
    { id: "a", title: "甲", excerpt: "需求一" },
    { id: "b", title: "乙", excerpt: "需求二" },
  ];
  const result = new Map<string, SemanticAssessment>([["a", {
    id: "a",
    actor_role: "buyer",
    transaction_direction: "buy",
    buyer_probability: 95,
    confidence: 95,
    project_specificity: 80,
    reason: "buyer",
    evidence: ["需求一"],
  }]]);
  let threw = false;
  try { requireCompleteSemanticCoverage(candidates, result); } catch { threw = true; }
  assert(threw, "partial semantic batch must fail instead of silently falling back to rules");
});\n'''
write(path, text)

# Ingest: semantic enforce is fail-closed but retryable when the provider fails/omits a result.
path = "supabase/functions/lead-radar-ingest/index.ts"
text = read(path)
text = replace_once(
    text,
    '''    await recordActorObservation(item, assessment, semantic);

    let shouldStore = assessment.is_lead;
    let dispositionReason = "policy";
    if (settings.enabled && settings.mode === "enforce" && semantic) {
      shouldStore = semanticDecision === "accept";
      dispositionReason = `semantic:${semanticDecision}`;
      if (semanticDecision === "accept") assessment = mergeAcceptedSemantic(assessment, semantic);
    } else if (settings.enabled && settings.mode === "shadow" && semantic) {
      dispositionReason = `semantic_shadow:${semanticDecision}`;
    } else if (settings.enabled && !semanticActive) {
      dispositionReason = "semantic_unavailable_policy_fallback";
    }

    if (!shouldStore) {''',
    '''    if (semantic) await recordActorObservation(item, assessment, semantic);

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

    if (!shouldStore) {''',
    "fail closed enforce",
)
write(path, text)

# Scan: provider-side one-day filter + only reserve conversation when a verified historical anchor exists.
path = "supabase/functions/lead-radar-scan/index.ts"
text = read(path)
text = replace_once(text, 'url.searchParams.set("timeFilter", "ALL");', 'url.searchParams.set("timeFilter", "ONE_DAY");', "one day provider filter")
old = '''    const queryOverride = String(requestRow?.query_override || "").trim();
    const allocation = allocateProviderCalls(requestedFrom, providerCallBudget, Boolean(queryOverride), SOURCE_POLICY);
    const desiredQueries = Math.max(1, Math.min(3, Number(requestRow?.max_queries || (requestedFrom === "auto" ? settings.auto_queries_per_scan : settings.manual_queries_per_scan))));'''
new = '''    const queryOverride = String(requestRow?.query_override || "").trim();
    const recentRowsForConversation = requestedFrom === "auto" || queryOverride ? [] : await latestRequests(50);
    const preselectedConversationAnchor = selectConversationAnchor(
      historicalPosts(Array.isArray(recentRowsForConversation) ? recentRowsForConversation : []),
      {
        cooldownIds: conversationCooldownIds(Array.isArray(recentRowsForConversation) ? recentRowsForConversation : [], new Date(), SOURCE_POLICY.anchor_cooldown_minutes),
        maxAgeMinutes: SOURCE_POLICY.anchor_max_age_minutes,
        minComments: SOURCE_POLICY.min_comments,
        preferredRoles: SOURCE_POLICY.preferred_roles,
      },
    );
    const allocation = allocateProviderCalls(requestedFrom, providerCallBudget, Boolean(queryOverride), SOURCE_POLICY, Boolean(preselectedConversationAnchor));
    const desiredQueries = Math.max(1, Math.min(3, Number(requestRow?.max_queries || (requestedFrom === "auto" ? settings.auto_queries_per_scan : settings.manual_queries_per_scan))));'''
text = replace_once(text, old, new, "preselect verified conversation anchor")
old = '''      const recentRows = await latestRequests(50);
      const currentPosts = [...postMap.values()];
      const cooldownIds = conversationCooldownIds(Array.isArray(recentRows) ? recentRows : [], new Date(), SOURCE_POLICY.anchor_cooldown_minutes);
      const candidates = mergeAnchorCandidates(historicalPosts(Array.isArray(recentRows) ? recentRows : []), currentPosts);
      const anchor = selectConversationAnchor(candidates, {
        cooldownIds,
        maxAgeMinutes: SOURCE_POLICY.anchor_max_age_minutes,
        minComments: SOURCE_POLICY.min_comments,
        preferredRoles: SOURCE_POLICY.preferred_roles,
      });'''
new = '''      const anchor = preselectedConversationAnchor;'''
text = replace_once(text, old, new, "use preselected verified anchor")
write(path, text)

# Remove now-unused merge helper so deno lint/type surface stays clean and intent is explicit.
text = read(path)
start = text.find("function mergeAnchorCandidates(")
if start >= 0:
    end = text.find("\nfunction conversationSource(", start)
    if end < 0:
        raise SystemExit("missing mergeAnchorCandidates end")
    text = text[:start] + text[end + 1:]
write(path, text)

print("V4 quality/recall patch applied")
