# Lead Discovery Architecture

## Objective

Lead Radar is an intent-detection system, not a keyword monitor. The target event is:

> A real demand-side actor publicly signals an actionable need for external software development work.

Mentions of websites, mini-programs, AI, Python, or automation are not leads by themselves.

## Architectural boundaries

```text
Public/authorized source
        |
        v
lead-radar-scan                  Data-source orchestration
  - rate limits
  - query selection
  - provider call
  - normalization
  - post preview snapshot
        |
        v
lead-radar-ingest                Canonical decision boundary
  - seen/dedupe ledger
  - actor-role classification
  - buying-stage classification
  - fit / intent / actionability scoring
  - persist qualified lead
  - record query yield
        |
        +--> lead_radar_seen_items
        +--> lead_radar_leads
        +--> lead_radar_query_metrics
        +--> notifications

lead-radar-api                   Compatibility/read API
  - list leads
  - status workflow
  - existing frontend compatibility

lead-radar-collector             GitHub Actions fallback
  - GitHub OIDC verification
  - queue claim/fail
  - forwards candidates to the SAME lead-radar-ingest service
```

The direct web scan and GitHub fallback are intentionally converged on the same ingest boundary. No acquisition path owns its own lead-definition rules.

## Single source of truth

`supabase/functions/_shared/lead_policy.json` is the canonical policy.

It defines:

- topic taxonomy;
- intent families;
- actor-role exclusion rules;
- buyer patterns;
- scoring thresholds and weights;
- query templates and priors.

Both Python and Deno runtimes consume the same JSON. Business policy must not be duplicated in source-specific connectors or UI code.

## Classification model

Classification is hierarchical:

1. **Actor role** — buyer, provider, recruiter, learner, content creator, unknown.
2. **Buying stage** — explicit, paid, considering, problem, none.
3. **Topic fit** — which service family matches our capabilities.
4. **Actionability** — can a reasonable next commercial action be taken?
5. **Freshness** — how recently the signal was published.

A high topic fit cannot compensate for the wrong actor role. A provider advertising website-development services is not a lead even when the service fit is 100%.

## Query portfolio

Queries are generated from:

`Intent Family × Topic Family`

Examples:

- `找人做网站`
- `小程序 找开发`
- `AI智能体 外包`
- `管理系统 有偿`
- `网站 二开`

Bare production topics such as `网站` or `小程序` are prohibited by CI because they waste limited result slots on tutorials, recommendations, and provider advertising.

Each query has a stable `query_key`, intent family, topic family, and prior.

## Adaptive query selection

`lead_radar_query_metrics` records cumulative:

- runs;
- returned_count;
- fresh_count;
- qualified_count;
- filtered_count;
- duplicate_count.

When history exists, the query selector uses an exploration/exploitation score derived from qualified yield and a prior. This allows productive queries to receive more traffic without permanently starving unexplored queries.

The useful optimization target is **qualified unique leads per provider call**, not raw result count.

## Evaluation contract

`supabase/functions/_shared/gold_set.json` contains reviewed positive and negative examples.

Every PR that changes lead policy must pass the same Gold Set in both Python and Deno.

Current CI gates:

- precision >= 0.95;
- recall >= 0.95;
- F1 >= 0.95;
- actor-role accuracy >= 0.80;
- no bare-topic production queries;
- strict Deno type-check for policy, ingest, scan, and collector.

New real false positives and false negatives should be added to the Gold Set before or with a policy fix.

## Feedback loop

`lead_radar_feedback` is the persistence boundary for future human labels:

- `lead`
- `maybe`
- `not_lead`

Optional reason codes should distinguish provider promotion, recruiting, tutorials/resources, ordinary discussion, and other failure modes.

Human feedback should update the Gold Set and eventually query-yield priors. It must not silently mutate production policy without a reviewed code change and regression test.

## Cost controls

- Provider calls are rate limited.
- An empty GitHub queue makes zero provider calls.
- `lead_radar_seen_items` deduplicates candidates before repeated classification cost.
- Query selection optimizes qualified yield instead of result volume.
- Provider/content/recruiter/learner signals are rejected before any optional expensive semantic escalation.

## Rollout strategy

This refactor uses a strangler migration:

1. Keep `lead-radar-api` for read/status compatibility.
2. Introduce `lead-radar-ingest` as the new canonical write/decision boundary.
3. Route Direct Edge scans through ingest.
4. Route GitHub fallback through the same ingest.
5. Observe production query metrics and reviewed feedback.
6. Remove legacy classification/write code only after the new boundary is stable.

This avoids a high-risk big-bang rewrite while eliminating new policy divergence.

## Rollback

Database changes are additive. Existing lead/read APIs remain compatible.

If the new acquisition path needs to be rolled back:

- redeploy the previous `lead-radar-scan` and `lead-radar-collector` versions;
- leave additive columns/tables in place;
- do not drop query metrics or feedback during an emergency rollback.

No rollback requires deleting existing Lead Radar data.
