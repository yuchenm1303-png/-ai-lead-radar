export interface SourcePortfolioPolicy {
  manual_conversation_calls: number;
  auto_conversation_calls: number;
  min_manual_provider_budget: number;
  anchor_cooldown_minutes: number;
  anchor_max_age_minutes: number;
  min_comments: number;
  max_comment_candidates: number;
  preferred_roles: string[];
}

export interface ProviderCallAllocation {
  search_calls: number;
  conversation_calls: number;
}

export interface ConversationAnchor {
  id: string;
  source?: string;
  title?: string;
  body?: string;
  published_at?: string | null;
  url?: string | null;
  author?: { id?: string | null; nickname?: string | null; avatar?: string | null } | null;
  images?: string[];
  metrics?: { comments?: number | null; likes?: number | null; collects?: number | null; shares?: number | null } | null;
  decision?: string | null;
  assessment?: { actor_role?: string | null; confidence?: number | null } | null;
  semantic?: {
    actor_role?: string | null;
    transaction_direction?: string | null;
    confidence?: number | null;
    buyer_probability?: number | null;
  } | null;
}

export interface ConversationEntry {
  id: string;
  item: Record<string, unknown>;
  preview: Record<string, unknown>;
}

function clampInt(value: unknown, fallback: number, min = 0, max = 100000) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(min, Math.min(max, Math.round(number))) : fallback;
}

export function allocateProviderCalls(
  requestedFrom: string,
  providerCallBudget: number,
  hasManualOverride: boolean,
  policy: SourcePortfolioPolicy,
): ProviderCallAllocation {
  const budget = Math.max(0, Math.floor(Number(providerCallBudget) || 0));
  if (!budget) return { search_calls: 0, conversation_calls: 0 };
  if (hasManualOverride) return { search_calls: budget, conversation_calls: 0 };

  const requested = requestedFrom === "auto"
    ? clampInt(policy.auto_conversation_calls, 0, 0, budget)
    : budget >= clampInt(policy.min_manual_provider_budget, 3, 1, 100)
    ? clampInt(policy.manual_conversation_calls, 1, 0, budget)
    : 0;
  const conversationCalls = Math.min(budget, requested);
  return { search_calls: budget - conversationCalls, conversation_calls: conversationCalls };
}

function roleOf(post: ConversationAnchor) {
  return String(post.semantic?.actor_role || post.assessment?.actor_role || "unknown").toLowerCase();
}

function directionOf(post: ConversationAnchor) {
  return String(post.semantic?.transaction_direction || "unknown").toLowerCase();
}

function confidenceOf(post: ConversationAnchor) {
  return Math.max(Number(post.semantic?.confidence || 0), Number(post.assessment?.confidence || 0));
}

function publishedAgeMinutes(post: ConversationAnchor, now: Date) {
  const timestamp = new Date(post.published_at || 0).getTime();
  return Number.isFinite(timestamp) ? (now.getTime() - timestamp) / 60000 : Number.POSITIVE_INFINITY;
}

export function conversationCooldownIds(rows: any[], now: Date, cooldownMinutes: number) {
  const cutoff = now.getTime() - Math.max(1, cooldownMinutes) * 60000;
  const result = new Set<string>();
  for (const row of Array.isArray(rows) ? rows : []) {
    const conversation = row?.result?.conversation;
    const anchorId = String(conversation?.anchor_id || "").trim();
    if (!anchorId) continue;
    const attempted = new Date(conversation?.attempted_at || row?.finished_at || row?.requested_at || 0).getTime();
    if (Number.isFinite(attempted) && attempted >= cutoff) result.add(anchorId);
  }
  return result;
}

export function selectConversationAnchor(
  candidates: ConversationAnchor[],
  options: {
    now?: Date;
    cooldownIds?: Set<string>;
    maxAgeMinutes: number;
    minComments: number;
    preferredRoles: string[];
  },
): ConversationAnchor | null {
  const now = options.now || new Date();
  const cooldown = options.cooldownIds || new Set<string>();
  const roles = new Set((options.preferredRoles || ["provider", "content"]).map((value) => String(value).toLowerCase()));
  const eligible = (Array.isArray(candidates) ? candidates : []).filter((post) => {
    const id = String(post?.id || "").trim();
    if (!id || cooldown.has(id)) return false;
    const comments = Math.max(0, Number(post?.metrics?.comments || 0));
    if (comments < Math.max(1, options.minComments)) return false;
    const age = publishedAgeMinutes(post, now);
    if (!Number.isFinite(age) || age < -5 || age > Math.max(1, options.maxAgeMinutes)) return false;
    const role = roleOf(post);
    if (!roles.has(role)) return false;
    if (role === "provider" && post.semantic && directionOf(post) !== "sell") return false;
    return true;
  });

  eligible.sort((a, b) => {
    const roleA = roleOf(a) === "provider" ? 2 : 1;
    const roleB = roleOf(b) === "provider" ? 2 : 1;
    const commentsA = Math.min(1000, Math.max(0, Number(a.metrics?.comments || 0)));
    const commentsB = Math.min(1000, Math.max(0, Number(b.metrics?.comments || 0)));
    const confidenceA = confidenceOf(a);
    const confidenceB = confidenceOf(b);
    const ageA = publishedAgeMinutes(a, now);
    const ageB = publishedAgeMinutes(b, now);
    const scoreA = roleA * 100 + Math.log1p(commentsA) * 18 + confidenceA * 0.35 - Math.max(0, ageA) / 1440;
    const scoreB = roleB * 100 + Math.log1p(commentsB) * 18 + confidenceB * 0.35 - Math.max(0, ageB) / 1440;
    return scoreB - scoreA;
  });
  return eligible[0] || null;
}

function walkObjects(value: unknown): any[] {
  const result: any[] = [];
  const visit = (node: unknown, depth: number) => {
    if (depth > 8 || node === null || node === undefined) return;
    if (Array.isArray(node)) {
      for (const item of node) visit(item, depth + 1);
      return;
    }
    if (typeof node !== "object") return;
    const object = node as Record<string, unknown>;
    result.push(object);
    for (const child of Object.values(object)) visit(child, depth + 1);
  };
  visit(value, 0);
  return result;
}

function textValue(node: any, keys: string[]) {
  for (const key of keys) {
    const value = node?.[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function commentAuthor(node: any) {
  for (const key of ["user", "author", "user_info", "userInfo", "note_user"]) {
    const user = node?.[key];
    if (!user || typeof user !== "object" || Array.isArray(user)) continue;
    const id = textValue(user, ["id", "user_id", "userId", "userid", "red_id"]);
    const nickname = textValue(user, ["nickname", "nick_name", "name", "user_name", "userName"]);
    const avatar = textValue(user, ["avatar", "avatar_url", "avatarUrl", "image"]);
    if (id || nickname) return { id, nickname, avatar };
  }
  return { id: "", nickname: "", avatar: "" };
}

export function parseConversationTimestamp(value: unknown): Date | null {
  if (value === null || value === undefined || typeof value === "boolean") return null;
  if (typeof value === "number" || (typeof value === "string" && /^\d+(?:\.\d+)?$/.test(value.trim()))) {
    let number = Number(value);
    if (!Number.isFinite(number)) return null;
    if (number > 10_000_000_000) number /= 1000;
    if (number < 1_000_000_000 || number > 4_000_000_000) return null;
    const parsed = new Date(number * 1000);
    return Number.isFinite(parsed.getTime()) ? parsed : null;
  }
  if (typeof value === "string") {
    const parsed = new Date(value.trim());
    return Number.isFinite(parsed.getTime()) ? parsed : null;
  }
  return null;
}

function commentTimestamp(node: any) {
  for (const key of ["create_time", "createTime", "created_at", "createdAt", "timestamp", "time", "publish_time", "published_at"]) {
    const parsed = parseConversationTimestamp(node?.[key]);
    if (parsed) return parsed;
  }
  return null;
}

function meaningfulText(value: string) {
  const normalized = value
    .replace(/\[[^\]]+R\]/g, " ")
    .replace(/#?[^#\s]{1,40}\[搜索高亮\]#/g, " ")
    .replace(/[\s\p{P}\p{S}]+/gu, "")
    .trim();
  return normalized.length >= 3;
}

export function groupConversationComments(
  payload: unknown,
  anchor: ConversationAnchor,
  options: { now?: Date; freshnessMinutes: number; maxCandidates: number },
) {
  const now = options.now || new Date();
  const maxAge = Math.max(1, options.freshnessMinutes);
  const anchorAuthorId = String(anchor.author?.id || "").trim();
  const grouped = new Map<string, {
    author: { id: string; nickname: string; avatar: string };
    comments: { id: string; text: string; published: Date; likes: number | null }[];
  }>();
  let rawCount = 0;

  for (const node of walkObjects((payload as any)?.data ?? payload)) {
    const text = textValue(node, ["content", "text", "comment", "comment_content", "commentContent"]);
    if (!text) continue;
    const commentId = textValue(node, ["comment_id", "commentId", "id", "comment_id_str"]);
    const author = commentAuthor(node);
    const commentish = Boolean(commentId || author.id || author.nickname || ["sub_comments", "subComments", "reply_count", "replyCount"].some((key) => key in node));
    if (!commentish) continue;
    rawCount += 1;
    if (!meaningfulText(text)) continue;
    if (anchorAuthorId && author.id && author.id === anchorAuthorId) continue;
    const published = commentTimestamp(node);
    if (!published) continue;
    const ageMinutes = (now.getTime() - published.getTime()) / 60000;
    if (!Number.isFinite(ageMinutes) || ageMinutes < -5 || ageMinutes > maxAge) continue;
    const authorKey = author.id || author.nickname || `comment:${commentId}`;
    const group = grouped.get(authorKey) || { author, comments: [] };
    if (!group.comments.some((item) => item.id && item.id === commentId)) {
      group.comments.push({
        id: commentId || `${authorKey}:${published.getTime()}`,
        text: text.slice(0, 1000),
        published,
        likes: Number.isFinite(Number(node?.like_count ?? node?.liked_count)) ? Math.max(0, Number(node?.like_count ?? node?.liked_count)) : null,
      });
    }
    grouped.set(authorKey, group);
  }

  const entries: ConversationEntry[] = [];
  for (const group of grouped.values()) {
    group.comments.sort((a, b) => a.published.getTime() - b.published.getTime());
    const latest = group.comments[group.comments.length - 1];
    if (!latest) continue;
    const combined = group.comments.map((comment) => comment.text).join("\n").slice(0, 1600);
    const externalId = `comment:${String(anchor.id).slice(0, 80)}:${latest.id.slice(0, 80)}`;
    const context = [
      `关联帖子标题：${String(anchor.title || "").slice(0, 240)}`,
      String(anchor.body || "").trim() ? `关联帖子正文：${String(anchor.body || "").slice(0, 900)}` : "",
    ].filter(Boolean).join("\n");
    const title = `评论需求 · ${combined.replace(/\s+/g, " ").slice(0, 100)}`;
    const parentUrl = String(anchor.url || `https://www.xiaohongshu.com/explore/${anchor.id}`);
    const item = {
      source: "小红书评论",
      external_id: externalId,
      title,
      excerpt: combined,
      published_at: latest.published.toISOString(),
      url: parentUrl,
      budget: null,
      author_id: group.author.id || null,
      author_name: group.author.nickname || null,
      content_kind: "comment",
      context_text: context,
      parent_source_id: anchor.id,
    };
    const preview = {
      id: externalId,
      source: "小红书评论",
      title,
      body: combined,
      published_at: latest.published.toISOString(),
      url: parentUrl,
      author: group.author.id || group.author.nickname || group.author.avatar
        ? { id: group.author.id || null, nickname: group.author.nickname || "", avatar: group.author.avatar || null }
        : null,
      images: Array.isArray(anchor.images) ? anchor.images.slice(0, 9) : [],
      metrics: { likes: latest.likes, comments: group.comments.length, collects: null, shares: null },
      tags: ["评论需求"],
      parent_note: { id: anchor.id, title: anchor.title || "" },
      discovery: { generator: "conversation_intent", anchor_id: anchor.id },
    };
    entries.push({ id: externalId, item, preview });
  }

  entries.sort((a, b) => new Date(String(b.item.published_at)).getTime() - new Date(String(a.item.published_at)).getTime());
  return {
    raw_count: rawCount,
    normalized_count: entries.length,
    entries: entries.slice(0, Math.max(1, options.maxCandidates)),
  };
}
