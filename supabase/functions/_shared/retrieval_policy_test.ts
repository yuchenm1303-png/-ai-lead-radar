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

Deno.test("retrieval v2 expands aliases across three lanes", () => {
  const portfolio = buildRetrievalPortfolio();
  const lanes = new Set(portfolio.map((spec) => spec.lane));
  const keywords = portfolio.map((spec) => spec.keyword.toLowerCase());
  assert(RETRIEVAL_VERSION === "2.0.0", `unexpected retrieval version ${RETRIEVAL_VERSION}`);
  assert(portfolio.length > 50, `portfolio too small: ${portfolio.length}`);
  assert(lanes.has("precision") && lanes.has("discovery") && lanes.has("broad"), `missing lanes: ${[...lanes].join(",")}`);
  assert(keywords.some((value) => value.includes("微信小程序")), "微信小程序 alias missing");
  assert(keywords.some((value) => value.includes("官网")), "官网 alias missing");
  assert(keywords.some((value) => value.includes("脚本")), "脚本 alias missing");
});

Deno.test("two-query plan diversifies lane and topic", () => {
  const selected = chooseQueries({ now: new Date("2026-08-26T14:00:00Z"), count: 2 });
  assert(selected.length === 2, `expected 2 queries, got ${selected.length}`);
  assert(new Set(selected.map((spec) => spec.lane)).size === 2, "lanes were not diversified");
  assert(new Set(selected.map((spec) => spec.topic_family)).size === 2, "topics were not diversified");
  assert(selected.some((spec) => spec.lane === "precision"), "precision lane missing");
  assert(selected.some((spec) => spec.lane === "discovery"), "discovery lane missing");
});

Deno.test("recent duplicate saturation suppresses exhausted query", () => {
  const portfolio = buildRetrievalPortfolio();
  const target = portfolio.find((spec) => spec.key === "explicit_outsource:mini_program:0");
  assert(target, "canonical mini program query missing");
  const metric: QueryMetric = {
    runs: 4,
    api_calls: 4,
    returned_count: 80,
    fresh_count: 40,
    qualified_count: 8,
    filtered_count: 4,
    duplicate_count: 28,
    human_positive_count: 0,
    human_negative_count: 0,
    last_run_at: "2026-08-26T13:50:00Z",
  };
  const selected = chooseQueries({
    now: new Date("2026-08-26T14:00:00Z"),
    count: 2,
    metrics: { [target.key]: metric },
  });
  assert(!selected.some((spec) => spec.key === target.key), "saturated query was selected again");
});

Deno.test("pagination follows freshness frontier and provider budget", () => {
  assert(shouldFetchNextPage({
    rawCount: 20,
    oldestPublishedAt: "2026-08-26T02:00:00Z",
    hasMore: true,
    pagesFetched: 1,
    providerCallsUsed: 2,
    providerCallBudget: 3,
    now: new Date("2026-08-26T14:00:00Z"),
  }), "fresh full page should be eligible for page 2");

  assert(!shouldFetchNextPage({
    rawCount: 20,
    oldestPublishedAt: "2026-08-25T10:00:00Z",
    hasMore: true,
    pagesFetched: 1,
    providerCallsUsed: 2,
    providerCallBudget: 3,
    now: new Date("2026-08-26T14:00:00Z"),
  }), "page beyond freshness frontier should not paginate");

  assert(!shouldFetchNextPage({
    rawCount: 20,
    oldestPublishedAt: "2026-08-26T02:00:00Z",
    hasMore: true,
    pagesFetched: 1,
    providerCallsUsed: 3,
    providerCallBudget: 3,
    now: new Date("2026-08-26T14:00:00Z"),
  }), "provider budget must stop pagination");
});
