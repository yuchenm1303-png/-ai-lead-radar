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


# Retrieval V4 source portfolio. Query keys stay v3:* because lexical retrieval itself is unchanged.
policy_path = Path("supabase/functions/_shared/retrieval_policy.json")
policy = json.loads(policy_path.read_text(encoding="utf-8"))
policy["version"] = "4.0.0"
policy["source_portfolio"] = {
    "manual_conversation_calls": 1,
    "auto_conversation_calls": 0,
    "min_manual_provider_budget": 3,
    "anchor_cooldown_minutes": 360,
    "anchor_max_age_minutes": 20160,
    "min_comments": 1,
    "max_comment_candidates": 20,
    "preferred_roles": ["provider", "content"],
}
policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

path = "supabase/functions/_shared/retrieval_policy.ts"
text = read(path)
text = replace_once(
    text,
    'export type RetrievalLane = "exploit" | "explore" | "expand" | "manual";',
    'export type RetrievalLane = "exploit" | "explore" | "expand" | "conversation" | "manual";',
    "retrieval lane",
)
marker = 'export const RETRIEVAL_VERSION = String(retrievalPolicy.version || "unknown");'
source_helper = '''export function sourcePortfolioPolicy() {
  const source = retrievalPolicy.source_portfolio || {};
  const preferredRoles = uniqueStrings(source.preferred_roles);
  return {
    manual_conversation_calls: Math.max(0, Math.floor(numberValue(source.manual_conversation_calls, 1))),
    auto_conversation_calls: Math.max(0, Math.floor(numberValue(source.auto_conversation_calls, 0))),
    min_manual_provider_budget: Math.max(1, Math.floor(numberValue(source.min_manual_provider_budget, 3))),
    anchor_cooldown_minutes: Math.max(30, Math.floor(numberValue(source.anchor_cooldown_minutes, 360))),
    anchor_max_age_minutes: Math.max(60, Math.floor(numberValue(source.anchor_max_age_minutes, 20160))),
    min_comments: Math.max(1, Math.floor(numberValue(source.min_comments, 1))),
    max_comment_candidates: Math.max(1, Math.min(40, Math.floor(numberValue(source.max_comment_candidates, 20)))),
    preferred_roles: preferredRoles.length ? preferredRoles : ["provider", "content"],
  };
}

'''
text = replace_once(text, marker, source_helper + marker, "source portfolio helper")
write(path, text)

path = "supabase/functions/_shared/retrieval_policy_test.ts"
text = read(path)
text = replace_once(text, "  RETRIEVAL_VERSION,\n", "  RETRIEVAL_VERSION,\n  sourcePortfolioPolicy,\n", "retrieval test import")
text = replace_once(
    text,
    'assert(RETRIEVAL_VERSION === "3.0.0", `unexpected retrieval version ${RETRIEVAL_VERSION}`);',
    'assert(RETRIEVAL_VERSION === "4.0.0", `unexpected retrieval version ${RETRIEVAL_VERSION}`);',
    "deno retrieval version",
)
text += '''\nDeno.test("retrieval v4 source portfolio reserves conversation only for manual breadth", () => {
  const source = sourcePortfolioPolicy();
  assert(source.manual_conversation_calls === 1, `manual conversation calls=${source.manual_conversation_calls}`);
  assert(source.auto_conversation_calls === 0, `auto conversation calls=${source.auto_conversation_calls}`);
  assert(source.min_manual_provider_budget === 3, `minimum budget=${source.min_manual_provider_budget}`);
  assert(source.preferred_roles.includes("provider"), "provider anchors must be enabled");
});\n'''
write(path, text)

path = "backend/tests/test_retrieval_v3.py"
text = read(path)
text = replace_once(text, 'self.assertEqual(retrieval_version(), "3.0.0")', 'self.assertEqual(retrieval_version(), "4.0.0")', "python retrieval version")
write(path, text)

# Semantic classifier receives parent context separately and historical actor evidence as a prior.
path = "supabase/functions/_shared/semantic_intent.ts"
text = read(path)
text = replace_once(text, 'export const INTELLIGENCE_VERSION = "3.2.0";', 'export const INTELLIGENCE_VERSION = "3.3.0";', "intelligence version")
text = replace_once(
    text,
    '''export interface SemanticCandidate {
  id: string;
  title: string;
  excerpt: string;
  author_name?: string | null;
}''',
    '''export interface SemanticCandidate {
  id: string;
  title: string;
  excerpt: string;
  author_name?: string | null;
  content_kind?: string | null;
  context_text?: string | null;
  actor_context?: Record<string, unknown> | null;
}''',
    "semantic candidate shape",
)
text = replace_once(
    text,
    "Classify the AUTHOR'S actor role and transaction direction from the overall meaning, not by keyword overlap.\nActor role and transaction direction are separate dimensions.",
    "Classify the AUTHOR'S actor role and transaction direction from the overall meaning, not by keyword overlap.\nActor role and transaction direction are separate dimensions.\nFor content_kind=comment, classify the COMMENT AUTHOR. context_text describes the parent post and is context only; never inherit the parent author's provider/buyer role.\nactor_context is historical evidence about the same author from earlier observed posts/comments. Treat it as a PRIOR only: repeated provider/sell history is strong evidence against reading vague current text as buyer intent, but explicit current buy/sell intent always overrides history.",
    "semantic instructions",
)
text = replace_once(
    text,
    '''    excerpt: item.excerpt.slice(0, 1600),
    author_name: String(item.author_name || "").slice(0, 120),''',
    '''    excerpt: item.excerpt.slice(0, 1600),
    author_name: String(item.author_name || "").slice(0, 120),
    content_kind: String(item.content_kind || "post").slice(0, 20),
    context_text: String(item.context_text || "").slice(0, 1200),
    actor_context: item.actor_context && typeof item.actor_context === "object" ? item.actor_context : null,''',
    "semantic input fields",
)
write(path, text)

path = "supabase/functions/_shared/semantic_intent_test.ts"
text = read(path)
text += '''\nDeno.test("semantic request carries comment parent context and actor memory only as explicit context", () => {
  const request = buildSemanticRequest([{
    id: "comment-1",
    title: "评论需求 · 做一个宠物店小程序大概要多少",
    excerpt: "目前只有一个想法，没有什么思路\\n做一个宠物店小程序，大概要多少？",
    author_name: "Luv.",
    content_kind: "comment",
    context_text: "关联帖子标题：小程序开发服务",
    actor_context: { observations: 3, provider_count: 0, buyer_count: 1, last_role: "buyer" },
  }], "gpt-5.4-nano") as any;
  const input = JSON.parse(request.input);
  assert(input.items[0].content_kind === "comment", "content kind missing");
  assert(input.items[0].context_text.includes("关联帖子标题"), "parent context missing");
  assert(input.items[0].actor_context.observations === 3, "actor memory missing");
  assert(String(request.instructions).includes("COMMENT AUTHOR"), "comment-author instruction missing");
  assert(String(request.instructions).includes("PRIOR only"), "actor-prior instruction missing");
});\n'''
write(path, text)

# Ingest: preserve comment context, read actor prior without provider calls, then atomically learn each new actor observation.
path = "supabase/functions/lead-radar-ingest/index.ts"
text = read(path)
text = replace_once(
    text,
    '''    author_id: authorId,
    author_name: authorName,
  };''',
    '''    author_id: authorId,
    author_name: authorName,
    content_kind: String(input?.content_kind || "post").trim().slice(0, 20) || "post",
    context_text: String(input?.context_text || "").trim().slice(0, 1200),
    parent_source_id: input?.parent_source_id ? String(input.parent_source_id).trim().slice(0, 160) : null,
  };''',
    "normalize comment context",
)
text = replace_once(
    text,
    '''    item.author_id || "",
    item.author_name || "",
  ].join("\\n"));''',
    '''    item.author_id || "",
    item.author_name || "",
    item.content_kind || "post",
    item.context_text || "",
  ].join("\\n"));''',
    "semantic hash context",
)
ingest_helpers = r'''
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

'''
text = replace_once(text, "async function ingest(payload: any) {", ingest_helpers + "async function ingest(payload: any) {", "actor helpers")
text = replace_once(
    text,
    '''      await updateSeen(Number(seen.id), "filtered", null, assessment);
      decisions.push({''',
    '''      await updateSeen(Number(seen.id), "filtered", null, assessment);
      await recordActorObservation(item, assessment, null);
      decisions.push({''',
    "hard guardrail actor observation",
)
text = replace_once(
    text,
    '''  const semanticById = new Map<string, SemanticAssessment>();
  const cachedSemanticIds = new Set<string>();''',
    '''  const actorMemory = await loadActorMemory(pending.map((entry) => entry.item));
  const semanticById = new Map<string, SemanticAssessment>();
  const cachedSemanticIds = new Set<string>();''',
    "load actor memory",
)
text = replace_once(
    text,
    '''          excerpt: entry.item.excerpt,
          author_name: entry.item.author_name,
        })), settings);''',
    '''          excerpt: entry.item.excerpt,
          author_name: entry.item.author_name,
          content_kind: entry.item.content_kind,
          context_text: entry.item.context_text,
          actor_context: actorMemory.get(actorMemoryKey(entry.item)) || null,
        })), settings);''',
    "semantic actor context",
)
text = replace_once(
    text,
    '''    if (semantic && !cachedSemanticIds.has(semanticKey)) {
      await persistSemantic(item, entry.contentHash, settings, semantic, semanticDecision || "uncertain");
    }

    let shouldStore = assessment.is_lead;''',
    '''    if (semantic && !cachedSemanticIds.has(semanticKey)) {
      await persistSemantic(item, entry.contentHash, settings, semantic, semanticDecision || "uncertain");
    }
    await recordActorObservation(item, assessment, semantic);

    let shouldStore = assessment.is_lead;''',
    "pending actor observation",
)
write(path, text)

# Scan: two lexical search calls + one selective conversation call for manual scans; auto remains search-only.
path = "supabase/functions/lead-radar-scan/index.ts"
text = read(path)
text = replace_once(
    text,
    '''  chooseQueries,
  retrievalLimits,
  shouldFetchNextPage,
  RETRIEVAL_VERSION,''',
    '''  chooseQueries,
  retrievalLimits,
  shouldFetchNextPage,
  sourcePortfolioPolicy,
  RETRIEVAL_VERSION,''',
    "scan retrieval import",
)
text = replace_once(
    text,
    '''} from "../_shared/retrieval_policy.ts";

const ALLOWED''',
    '''} from "../_shared/retrieval_policy.ts";
import {
  allocateProviderCalls,
  conversationCooldownIds,
  groupConversationComments,
  selectConversationAnchor,
  type ConversationAnchor,
} from "../_shared/conversation_retrieval.ts";

const ALLOWED''',
    "conversation import",
)
text = replace_once(
    text,
    '''const JUSTONE_ENDPOINT = (Deno.env.get("JUSTONE_API_ENDPOINT") || "https://api.justoneapi.com/api/xiaohongshu/search-note/v4").trim();''',
    '''const JUSTONE_ENDPOINT = (Deno.env.get("JUSTONE_API_ENDPOINT") || "https://api.justoneapi.com/api/xiaohongshu/search-note/v4").trim();
const JUSTONE_COMMENTS_ENDPOINT = "https://api.justoneapi.com/api/xiaohongshu/get-note-comment/v2";''',
    "comments endpoint",
)
text = replace_once(text, "const LIMITS = retrievalLimits();", "const LIMITS = retrievalLimits();\nconst SOURCE_POLICY = sourcePortfolioPolicy();", "source policy constant")

scan_helpers = r'''
async function fetchConversationOnce(anchor: ConversationAnchor) {
  if (!JUSTONE_TOKEN) throw new Error("JUSTONE_API_TOKEN is not configured");
  const url = new URL(JUSTONE_COMMENTS_ENDPOINT);
  url.searchParams.set("token", JUSTONE_TOKEN);
  url.searchParams.set("noteId", String(anchor.id));
  url.searchParams.set("sort", "latest");
  const response = await fetch(url, {
    headers: { Accept: "application/json", "User-Agent": "AI-Lead-Radar/4.0" },
    signal: AbortSignal.timeout(20_000),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`Just One comments HTTP ${response.status}: ${text.slice(0, 200)}`);
  let payload: any;
  try { payload = JSON.parse(text); } catch { throw new Error("Just One comments returned invalid JSON"); }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("Just One comments returned a non-object response");
  if (payload.code !== 0) throw new Error(`Just One comments business code ${String(payload.code)}: ${String(payload.message || payload.msg || "business error").slice(0, 180)}`);
  const grouped = groupConversationComments(payload, anchor, {
    freshnessMinutes: LIMITS.freshness_minutes,
    maxCandidates: SOURCE_POLICY.max_comment_candidates,
  });
  return { ...grouped, request_id: payload.requestId ? String(payload.requestId).slice(0, 160) : null };
}

function conversationSpec(anchor: ConversationAnchor): QuerySpec {
  return {
    key: `v4:conversation:${String(anchor.id).slice(0, 100)}`,
    keyword: `评论区 · ${String(anchor.title || anchor.id).slice(0, 180)}`,
    category: "评论需求",
    intent_family: "conversation_intent",
    topic_family: "observed_provider_thread",
    lane: "conversation",
    prior: 1,
  };
}

function historicalPosts(rows: any[]) {
  const posts: any[] = [];
  for (const row of Array.isArray(rows) ? rows : []) {
    if (String(row?.status || "") !== "success") continue;
    for (const post of Array.isArray(row?.result?.posts) ? row.result.posts : []) posts.push(post);
  }
  return posts;
}

function mergeAnchorCandidates(historical: any[], current: any[]) {
  const byId = new Map<string, any>();
  for (const post of historical) {
    const id = String(post?.id || "");
    if (id) byId.set(id, post);
  }
  for (const post of current) {
    const id = String(post?.id || "");
    if (!id) continue;
    const previous = byId.get(id) || {};
    byId.set(id, {
      ...previous,
      ...post,
      semantic: post.semantic || previous.semantic || null,
      assessment: post.assessment || previous.assessment || null,
    });
  }
  return [...byId.values()];
}

function conversationSource(spec: QuerySpec, result: Awaited<ReturnType<typeof fetchConversationOnce>>) {
  const source = newQuerySource(spec);
  source.pages = 1;
  source.api_calls = 1;
  source.raw_count = result.raw_count;
  source.normalized_count = result.normalized_count;
  source.last_page_raw_count = result.raw_count;
  source.has_more = false;
  if (result.request_id) source.request_ids.push(result.request_id);
  const dates: string[] = [];
  for (const entry of result.entries) {
    source.entries.set(entry.id, { item: entry.item, preview: entry.preview });
    const published = String(entry.item.published_at || "");
    if (published) dates.push(published);
  }
  if (dates.length) {
    dates.sort();
    source.oldest_published_at = dates[0];
    source.newest_published_at = dates[dates.length - 1];
    source.last_page_oldest_published_at = source.oldest_published_at;
  }
  return source;
}

async function recordFailedProviderCall(spec: QuerySpec, scanRequestId: number, startedAt: string) {
  try {
    await rest("lead_radar_query_runs", {
      method: "POST",
      headers: { Prefer: "return=minimal" },
      body: JSON.stringify({
        scan_request_id: scanRequestId,
        provider: "justone-xiaohongshu-comments-v2",
        retrieval_version: RETRIEVAL_VERSION,
        query_key: spec.key,
        query_text: spec.keyword,
        lane: spec.lane,
        intent_family: spec.intent_family,
        topic_family: spec.topic_family,
        started_at: startedAt,
        finished_at: new Date().toISOString(),
        pages: 1,
        api_calls: 1,
        returned_count: 0,
        normalized_count: 0,
        fresh_count: 0,
        qualified_count: 0,
        filtered_count: 0,
        duplicate_count: 0,
      }),
    });
  } catch (error) {
    console.warn("failed provider call accounting failed", String(error));
  }
}

'''
text = replace_once(text, "async function ingestItems(items: any[], spec: QuerySpec, source: ReturnType<typeof newQuerySource>, scanRequestId: number) {", scan_helpers + "async function ingestItems(items: any[], spec: QuerySpec, source: ReturnType<typeof newQuerySource>, scanRequestId: number) {", "scan helpers")
text = replace_once(text, '        provider: "justone-xiaohongshu-v4",', '        provider: spec.lane === "conversation" ? "justone-xiaohongshu-comments-v2" : "justone-xiaohongshu-v4",', "ingest provider attribution")

old_plan = '''    const requestedFrom = String(requestRow?.requested_from || "web");
    const perScanBudget = requestedFrom === "auto" ? settings.auto_provider_calls_per_scan : settings.manual_provider_calls_per_scan;
    const providerCallBudget = Math.max(1, Math.min(perScanBudget, remainingHourlyCalls));
    const desiredQueries = Math.max(1, Math.min(3, Number(requestRow?.max_queries || (requestedFrom === "auto" ? settings.auto_queries_per_scan : settings.manual_queries_per_scan))));
    const metrics = await queryMetrics();
    const specs = chooseQueries({
      override: String(requestRow?.query_override || "").trim() || null,
      metrics,
      count: Math.min(desiredQueries, providerCallBudget),
    });
    if (!specs.length) throw new Error("retrieval plan is empty");

    const retrieval = await executeRetrievalPlan(specs, providerCallBudget);'''
new_plan = '''    const requestedFrom = String(requestRow?.requested_from || "web");
    const perScanBudget = requestedFrom === "auto" ? settings.auto_provider_calls_per_scan : settings.manual_provider_calls_per_scan;
    const providerCallBudget = Math.max(1, Math.min(perScanBudget, remainingHourlyCalls));
    const queryOverride = String(requestRow?.query_override || "").trim();
    const allocation = allocateProviderCalls(requestedFrom, providerCallBudget, Boolean(queryOverride), SOURCE_POLICY);
    const desiredQueries = Math.max(1, Math.min(3, Number(requestRow?.max_queries || (requestedFrom === "auto" ? settings.auto_queries_per_scan : settings.manual_queries_per_scan))));
    const metrics = await queryMetrics();
    const specs = chooseQueries({
      override: queryOverride || null,
      metrics,
      count: Math.min(desiredQueries, allocation.search_calls),
    });
    if (allocation.search_calls > 0 && !specs.length) throw new Error("retrieval plan is empty");

    const retrieval = await executeRetrievalPlan(specs, allocation.search_calls);'''
text = replace_once(text, old_plan, new_plan, "scan budget allocation")
text = replace_once(
    text,
    "    let totalStored = 0, totalFiltered = 0, totalDuplicates = 0, totalNotified = 0, totalScanned = 0;",
    '''    let totalStored = 0, totalFiltered = 0, totalDuplicates = 0, totalNotified = 0, totalScanned = 0;
    let conversationCallsUsed = 0;
    let conversation: any = {
      enabled: allocation.conversation_calls > 0,
      attempted: false,
      attempted_at: null,
      anchor_id: null,
      anchor_title: null,
      raw_comments: 0,
      candidates: 0,
      stored: 0,
      filtered: 0,
      duplicates: 0,
      error: null,
      reason: allocation.conversation_calls > 0 ? "awaiting_anchor" : (requestedFrom === "auto" ? "auto_search_only" : queryOverride ? "manual_override_search_only" : "budget_not_reserved"),
    };''',
    "conversation summary init",
)

conversation_execution = r'''

    if (allocation.conversation_calls > 0) {
      const recentRows = await latestRequests(50);
      const currentPosts = [...postMap.values()];
      const cooldownIds = conversationCooldownIds(Array.isArray(recentRows) ? recentRows : [], new Date(), SOURCE_POLICY.anchor_cooldown_minutes);
      const candidates = mergeAnchorCandidates(historicalPosts(Array.isArray(recentRows) ? recentRows : []), currentPosts);
      const anchor = selectConversationAnchor(candidates, {
        cooldownIds,
        maxAgeMinutes: SOURCE_POLICY.anchor_max_age_minutes,
        minComments: SOURCE_POLICY.min_comments,
        preferredRoles: SOURCE_POLICY.preferred_roles,
      });
      if (!anchor) {
        conversation.reason = "no_eligible_provider_or_content_anchor";
      } else {
        const spec = conversationSpec(anchor);
        const attemptedAt = new Date().toISOString();
        conversation = {
          ...conversation,
          attempted: true,
          attempted_at: attemptedAt,
          anchor_id: anchor.id,
          anchor_title: anchor.title || null,
          reason: "provider_or_content_comment_mining",
        };
        conversationCallsUsed = 1;
        try {
          const commentResult = await fetchConversationOnce(anchor);
          const source = conversationSource(spec, commentResult);
          const values = [...source.entries.values()];
          const ingest = await ingestItems(values.map((entry) => entry.item), spec, source, id);
          const commentPosts = decoratePosts(values.map((entry) => entry.preview), ingest.decisions || []);
          for (const post of commentPosts) {
            const key = String(post.id || "");
            if (!key) continue;
            const previous = postMap.get(key);
            if (!previous || postRank(post) > postRank(previous)) postMap.set(key, post);
          }
          for (const leadId of Array.isArray(ingest.lead_ids) ? ingest.lead_ids : []) {
            if (Number.isInteger(Number(leadId)) && Number(leadId) > 0) leadIds.add(Number(leadId));
          }
          totalStored += Number(ingest.stored || 0);
          totalFiltered += Number(ingest.filtered || 0);
          totalDuplicates += Number(ingest.duplicates || 0);
          totalNotified += Number(ingest.notified || 0);
          totalScanned += source.raw_count;
          queryResults.push({
            key: spec.key,
            keyword: spec.keyword,
            lane: spec.lane,
            intent_family: spec.intent_family,
            topic_family: spec.topic_family,
            pages: source.pages,
            api_calls: source.api_calls,
            raw_count: source.raw_count,
            normalized_count: source.normalized_count,
            fresh_count: source.entries.size,
            stored: Number(ingest.stored || 0),
            filtered: Number(ingest.filtered || 0),
            duplicates: Number(ingest.duplicates || 0),
            semantic_calls: Number(ingest.semantic_calls || 0),
            semantic_cached: Number(ingest.semantic_cached || 0),
            request_ids: source.request_ids,
            newest_published_at: source.newest_published_at,
            oldest_published_at: source.oldest_published_at,
          });
          conversation = {
            ...conversation,
            raw_comments: source.raw_count,
            candidates: source.entries.size,
            stored: Number(ingest.stored || 0),
            filtered: Number(ingest.filtered || 0),
            duplicates: Number(ingest.duplicates || 0),
          };
        } catch (error) {
          await recordFailedProviderCall(spec, id, attemptedAt);
          conversation.error = String(error).slice(0, 300);
          conversation.reason = "conversation_provider_error";
        }
      }
    }
'''
text = replace_once(
    text,
    "    const posts = [...postMap.values()].sort((a, b) => new Date(b.published_at || 0).getTime() - new Date(a.published_at || 0).getTime());",
    conversation_execution + "\n    const posts = [...postMap.values()].sort((a, b) => new Date(b.published_at || 0).getTime() - new Date(a.published_at || 0).getTime());",
    "conversation execution",
)
text = replace_once(
    text,
    '''      provider_calls: retrieval.providerCallsUsed,
      provider_call_budget: providerCallBudget,''',
    '''      provider_calls: retrieval.providerCallsUsed + conversationCallsUsed,
      provider_call_budget: providerCallBudget,
      provider_call_allocation: allocation,
      conversation,''',
    "result provider calls",
)
text = text.replace('mode: "retrieval-v2-background"', 'mode: "retrieval-v4-background"')
write(path, text)

# CI checks the new module too.
path = ".github/workflows/ci.yml"
text = read(path)
text = replace_once(text, "      - run: deno check supabase/functions/_shared/semantic_intent.ts\n", "      - run: deno check supabase/functions/_shared/semantic_intent.ts\n      - run: deno check supabase/functions/_shared/conversation_retrieval.ts\n", "ci conversation check")
text = replace_once(text, "      - run: deno test supabase/functions/_shared/semantic_intent_test.ts\n", "      - run: deno test supabase/functions/_shared/semantic_intent_test.ts\n      - run: deno test supabase/functions/_shared/conversation_retrieval_test.ts\n", "ci conversation test")
write(path, text)

# One-shot patch infrastructure must not appear in the PR.
for temporary in [Path(".github/workflows/v4-production-patcher.yml"), Path("scripts/v4_production_patch.py")]:
    if temporary.exists():
        temporary.unlink()
