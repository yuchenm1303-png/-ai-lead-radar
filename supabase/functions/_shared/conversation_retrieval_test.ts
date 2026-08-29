import {
  allocateProviderCalls,
  conversationCooldownIds,
  groupConversationComments,
  selectConversationAnchor,
  type SourcePortfolioPolicy,
} from "./conversation_retrieval.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

const policy: SourcePortfolioPolicy = {
  manual_conversation_calls: 1,
  auto_conversation_calls: 0,
  min_manual_provider_budget: 3,
  anchor_cooldown_minutes: 360,
  anchor_max_age_minutes: 20160,
  min_comments: 1,
  max_comment_candidates: 20,
  preferred_roles: ["provider", "content"],
};

Deno.test("manual three-call scan reserves one conversation call while auto and override do not", () => {
  const manual = allocateProviderCalls("web", 3, false, policy);
  assert(manual.search_calls === 2 && manual.conversation_calls === 1, JSON.stringify(manual));
  const auto = allocateProviderCalls("auto", 1, false, policy);
  assert(auto.search_calls === 1 && auto.conversation_calls === 0, JSON.stringify(auto));
  const override = allocateProviderCalls("web", 3, true, policy);
  assert(override.search_calls === 3 && override.conversation_calls === 0, JSON.stringify(override));
  const small = allocateProviderCalls("web", 2, false, policy);
  assert(small.search_calls === 2 && small.conversation_calls === 0, JSON.stringify(small));
  const noAnchor = allocateProviderCalls("web", 3, false, policy, false);
  assert(noAnchor.search_calls === 3 && noAnchor.conversation_calls === 0, JSON.stringify(noAnchor));
});

Deno.test("conversation anchor prefers provider/content posts with comments and respects cooldown", () => {
  const now = new Date("2026-08-29T10:00:00Z");
  const candidates: any[] = [
    {
      id: "buyer",
      title: "公司想做网站",
      published_at: "2026-08-29T09:00:00Z",
      metrics: { comments: 30 },
      semantic: { actor_role: "buyer", transaction_direction: "buy", confidence: 98 },
    },
    {
      id: "provider",
      title: "小程序开发服务",
      published_at: "2026-08-29T09:20:00Z",
      metrics: { comments: 5 },
      semantic: { actor_role: "provider", transaction_direction: "sell", confidence: 95 },
    },
    {
      id: "content",
      title: "开发避坑指南",
      published_at: "2026-08-29T09:40:00Z",
      metrics: { comments: 12 },
      semantic: { actor_role: "content", transaction_direction: "non_transactional", confidence: 90 },
    },
  ];
  const anchor = selectConversationAnchor(candidates, {
    now,
    cooldownIds: new Set(),
    maxAgeMinutes: policy.anchor_max_age_minutes,
    minComments: 1,
    preferredRoles: policy.preferred_roles,
  });
  assert(anchor?.id === "provider", `unexpected anchor=${anchor?.id}`);

  const cooled = selectConversationAnchor(candidates, {
    now,
    cooldownIds: new Set(["provider"]),
    maxAgeMinutes: policy.anchor_max_age_minutes,
    minComments: 1,
    preferredRoles: policy.preferred_roles,
  });
  assert(cooled?.id === "content", `cooldown fallback=${cooled?.id}`);
});

Deno.test("cooldown ids are reconstructed from recent scan results", () => {
  const now = new Date("2026-08-29T10:00:00Z");
  const ids = conversationCooldownIds([
    { finished_at: "2026-08-29T09:00:00Z", result: { conversation: { anchor_id: "fresh", attempted_at: "2026-08-29T09:00:00Z" } } },
    { finished_at: "2026-08-29T01:00:00Z", result: { conversation: { anchor_id: "old", attempted_at: "2026-08-29T01:00:00Z" } } },
  ], now, 360);
  assert(ids.has("fresh"), "recent anchor missing");
  assert(!ids.has("old"), "expired anchor must leave cooldown");
});

Deno.test("comments are grouped by commenter, parent author replies are skipped, and missing timestamps are not faked", () => {
  const now = new Date("2026-08-29T10:00:00Z");
  const payload = {
    code: 0,
    data: {
      comments: [
        {
          id: "c1",
          content: "目前只有一个想法，没有什么思路",
          create_time: Math.floor(new Date("2026-08-29T09:40:00Z").getTime() / 1000),
          user: { id: "buyer-1", nickname: "Luv." },
        },
        {
          id: "c2",
          content: "做一个宠物店小程序，大概要多少？",
          create_time: Math.floor(new Date("2026-08-29T09:45:00Z").getTime() / 1000),
          user: { id: "buyer-1", nickname: "Luv." },
        },
        {
          id: "c3",
          content: "私信您了",
          create_time: Math.floor(new Date("2026-08-29T09:46:00Z").getTime() / 1000),
          user: { id: "provider-anchor", nickname: "开发咨询" },
        },
        {
          id: "c4",
          content: "多少钱",
          user: { id: "no-time", nickname: "无时间" },
        },
        {
          id: "c5",
          content: "[点赞R]",
          create_time: Math.floor(new Date("2026-08-29T09:47:00Z").getTime() / 1000),
          user: { id: "reaction", nickname: "点赞" },
        },
      ],
    },
  };
  const grouped = groupConversationComments(payload, {
    id: "provider-note",
    title: "小程序开发服务",
    body: "需要的可以咨询",
    url: "https://www.xiaohongshu.com/explore/provider-note",
    author: { id: "provider-anchor", nickname: "开发咨询" },
    images: ["https://example.com/cover.jpg"],
  }, { now, freshnessMinutes: 1440, maxCandidates: 20 });

  assert(grouped.raw_count === 5, `raw=${grouped.raw_count}`);
  assert(grouped.entries.length === 1, `entries=${grouped.entries.length}`);
  const entry: any = grouped.entries[0];
  assert(entry.item.author_id === "buyer-1", `author=${entry.item.author_id}`);
  assert(String(entry.item.excerpt).includes("目前只有一个想法"), "first buyer comment missing");
  assert(String(entry.item.excerpt).includes("宠物店小程序"), "second buyer comment missing");
  assert(entry.item.content_kind === "comment", "comment kind missing");
  assert(String(entry.item.context_text).includes("关联帖子标题"), "parent context missing");
  assert(String(entry.item.external_id).endsWith(":c2"), `stable id=${entry.item.external_id}`);
});

Deno.test("conversation anchor rejects contradictory historical provider snapshots", () => {
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
});
