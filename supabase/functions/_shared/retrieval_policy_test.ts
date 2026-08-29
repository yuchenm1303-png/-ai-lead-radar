import {
  buildRetrievalPortfolio,
  chooseQueries,
  shouldFetchNextPage,
  RETRIEVAL_VERSION,
  type QueryMetric,
} from "./retrieval_policy.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function metric(overrides: Partial<QueryMetric> = {}): QueryMetric {
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
    ...overrides,
  };
}

Deno.test("retrieval v3 is archetype driven and never emits bare-topic broad probes", () => {
  const portfolio = buildRetrievalPortfolio();
  const archetypes = new Set(portfolio.map((spec) => spec.intent_family));
  const bareTopics = new Set([
    "小程序", "微信小程序", "网站", "官网", "独立站", "英文官网", "管理系统", "后台系统", "业务系统",
    "AI智能体", "智能体", "AI应用", "自动化", "工作流自动化", "Python脚本", "爬虫", "数据处理",
  ].map((value) => value.toLowerCase()));
  assert(RETRIEVAL_VERSION === "3.0.0", `unexpected retrieval version ${RETRIEVAL_VERSION}`);
  assert(portfolio.length > 100, `portfolio too small: ${portfolio.length}`);
  assert(archetypes.has("vendor_search") && archetypes.has("quote_budget") && archetypes.has("modify_takeover"), `missing archetypes: ${[...archetypes].join(",")}`);
  assert(!portfolio.some((spec) => bareTopics.has(spec.keyword.toLowerCase())), "V3 must not issue bare-topic broad searches");
  assert(portfolio.every((spec) => spec.key.startsWith("v3:")), "V3 probe keys must be versioned");
});

Deno.test("three-query plan is exploit plus explore plus expand", () => {
  const selected = chooseQueries({ now: new Date("2026-08-29T09:00:00Z"), count: 3 });
  assert(selected.length === 3, `expected 3 probes, got ${selected.length}`);
  assert(selected[0].lane === "exploit", `first lane=${selected[0].lane}`);
  assert(selected[1].lane === "explore", `second lane=${selected[1].lane}`);
  assert(selected[2].lane === "expand", `third lane=${selected[2].lane}`);
  assert(new Set(selected.map((spec) => spec.key)).size === 3, "planner repeated a probe");
  assert(selected[1].topic_family !== selected[0].topic_family, "explore should move to a new topic when possible");
  assert(selected[1].intent_family !== selected[0].intent_family, "explore should move to a new buyer-intent archetype when possible");
});

Deno.test("legacy V2 success metrics warm-start the V3 buyer-intent archetype", () => {
  const metrics = {
    "explicit_outsource:mini_program:0": metric({
      runs: 5,
      api_calls: 5,
      returned_count: 100,
      fresh_count: 55,
      qualified_count: 16,
      filtered_count: 10,
      duplicate_count: 3,
      human_positive_count: 3,
      human_negative_count: 0,
      last_run_at: "2026-08-29T02:00:00Z",
    }),
    "discovery:website:1:0": metric({
      runs: 4,
      api_calls: 4,
      returned_count: 80,
      fresh_count: 30,
      qualified_count: 0,
      filtered_count: 30,
      duplicate_count: 0,
      human_positive_count: 0,
      human_negative_count: 3,
      last_run_at: "2026-08-29T02:00:00Z",
    }),
  };
  const selected = chooseQueries({ now: new Date("2026-08-29T09:00:00Z"), count: 1, metrics });
  assert(selected.length === 1, "missing exploit probe");
  assert(selected[0].intent_family === "vendor_search", `unexpected archetype=${selected[0].intent_family}`);
  assert(selected[0].topic_family === "mini_program", `unexpected topic=${selected[0].topic_family}`);
});

Deno.test("recent duplicate saturation suppresses an exhausted V3 probe", () => {
  const portfolio = buildRetrievalPortfolio();
  const target = portfolio.find((spec) => spec.intent_family === "vendor_search" && spec.topic_family === "mini_program");
  assert(target, "target V3 probe missing");
  const selected = chooseQueries({
    now: new Date("2026-08-29T09:00:00Z"),
    count: 3,
    metrics: {
      [target.key]: metric({
        runs: 5,
        api_calls: 5,
        returned_count: 100,
        fresh_count: 45,
        qualified_count: 8,
        filtered_count: 5,
        duplicate_count: 35,
        last_run_at: "2026-08-29T08:50:00Z",
      }),
    },
  });
  assert(!selected.some((spec) => spec.key === target.key), "saturated probe was selected again");
});

Deno.test("expand follows a proven semantic neighborhood without repeating the exact pair", () => {
  const metrics = {
    "paid_request:website:0": metric({
      runs: 4,
      api_calls: 4,
      returned_count: 80,
      fresh_count: 32,
      qualified_count: 10,
      filtered_count: 12,
      duplicate_count: 1,
      human_positive_count: 2,
      last_run_at: "2026-08-29T03:00:00Z",
    }),
  };
  const selected = chooseQueries({ now: new Date("2026-08-29T09:00:00Z"), count: 3, metrics });
  const expand = selected.find((spec) => spec.lane === "expand");
  assert(expand, "expand probe missing");
  assert(
    expand.intent_family === "quote_budget" || expand.topic_family === "website",
    `expand did not stay adjacent to winning pair: ${expand.intent_family}/${expand.topic_family}`,
  );
  assert(!(expand.intent_family === "quote_budget" && expand.topic_family === "website"), "expand repeated the exact winning pair instead of widening it");
});

Deno.test("pagination still respects freshness frontier and provider budget", () => {
  assert(shouldFetchNextPage({
    rawCount: 20,
    oldestPublishedAt: "2026-08-29T01:00:00Z",
    hasMore: true,
    pagesFetched: 1,
    providerCallsUsed: 2,
    providerCallBudget: 3,
    now: new Date("2026-08-29T09:00:00Z"),
  }), "fresh full page should remain eligible for page 2 when budget exists");

  assert(!shouldFetchNextPage({
    rawCount: 20,
    oldestPublishedAt: "2026-08-27T20:00:00Z",
    hasMore: true,
    pagesFetched: 1,
    providerCallsUsed: 2,
    providerCallBudget: 3,
    now: new Date("2026-08-29T09:00:00Z"),
  }), "old page should not paginate");

  assert(!shouldFetchNextPage({
    rawCount: 20,
    oldestPublishedAt: "2026-08-29T01:00:00Z",
    hasMore: true,
    pagesFetched: 1,
    providerCallsUsed: 3,
    providerCallBudget: 3,
    now: new Date("2026-08-29T09:00:00Z"),
  }), "provider budget must stop pagination");
});
