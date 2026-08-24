# Xiaohongshu Source Provider Benchmark

This benchmark exists to answer one product question before we commit to any data vendor:

> How quickly and reliably can a provider surface newly published Xiaohongshu notes for our development-demand search queries?

The goal is not maximum volume. AI Lead Radar values fresh, actionable demand, so the primary benchmark is **freshness + reliability + usable original-post links**.

## First-round providers

### 1. TikHub — primary candidate

- Search endpoint: `/api/v1/xiaohongshu/app_v2/search_notes`
- Supports `sort_type=time_descending`
- Supports `time_filter=一天内`
- Bearer-token authentication
- Public pricing page states roughly `$0.001–0.01/request`, with free signup credits
- App V2 is the provider's current recommended Xiaohongshu series

Why it is first: it exposes exactly the sort/time controls Lead Radar needs, and pricing/testing entry is clear.

### 2. Rnote — primary candidate

- Search endpoint: `/api/v2/crawler/search/notes`
- Supports `sort=time_descending`
- Header authentication via `X-API-Key`
- Provider states that only successful requests are billed and new accounts receive trial balance
- Public site advertises pricing from about `$0.01/request`

Why it is first: focused Xiaohongshu product, simple REST surface, explicit latest sort.

Important: statements about upstream/source behavior are vendor claims and must be independently validated before commercial reliance.

### 3. Just One API — primary candidate

- Search endpoint: `/api/xiaohongshu/search-note/v4`
- V4 supports `sortType=time_descending`
- Supports `timeFilter=ONE_DAY`
- Free test calls are advertised; endpoint pricing is visible after login
- Terms explicitly state that third-party platform behavior can change and downstream compliance remains the customer's responsibility

Why it is first: strong query controls and a broader cross-platform API surface that may later support the "all-web" version of Lead Radar.

## Second-round providers

### SpiderHubs

Worth testing after the first three. It advertises RedNote/Xiaohongshu APIs, automated monitoring, free starting credits, and other social platforms. Public documentation surfaced clear RedNote coverage but did not surface a keyword-search endpoint as directly as the top three during this research pass.

### Apify community RedNote actors

Useful as a fallback/independent comparison. Multiple community Actors expose Xiaohongshu keyword search and scheduled runs via the Apify API. Some search modes are login-gated, and actor adoption/ratings vary, so this is not the first production choice.

### QianGua / NewRank commercial monitoring products

These products validate the market need for ongoing Xiaohongshu monitoring, but a self-service public note-search API was not confirmed in this pass. They remain enterprise/data-feed candidates if commercial API or webhook access is available by contract.

## Default demand query set

The script runs 20 buyer-intent queries by default:

- 找人做小程序
- 有没有会做小程序的
- 微信小程序 有偿
- 小程序 外包
- 网站开发 有偿
- 找人做网站
- 想做一个网站
- 寻找开发团队
- AI智能体 开发团队
- AI智能体 外包
- 网页开发 找人
- 管理系统 开发
- 独立站 开发
- Python 有偿
- Python 急
- 数据处理 有偿
- 自动化 开发
- 软件开发 外包
- H5 开发
- 预约系统 开发

This list is intentionally buyer-intent oriented rather than generic technical keywords.

## Metrics

Each provider/query pair records:

- request success/failure
- request latency
- number of note-like objects returned
- number of candidates with usable ID + content + publish time
- original-post URL coverage
- age of the newest result
- count published within 30 minutes
- count published within 2 hours
- count published within 24 hours

The report then summarizes success rate, median latency, median newest-result age, fresh-result counts, and URL coverage per provider.

For our use case, ranking should prioritize roughly:

1. **Median newest-result age**
2. **Success rate**
3. **Within-30-minute / within-2-hour result count**
4. **Correct publish timestamp and original URL coverage**
5. Latency
6. Cost

A provider returning thousands of old notes is worse than a smaller provider that consistently sees a 5-minute-old demand post.

## Credentials

Never commit provider keys. Configure only through environment variables:

```bash
TIKHUB_API_KEY=
RNOTE_API_KEY=
JUSTONE_API_TOKEN=
```

## Run

From the repository root:

```bash
python -m backend.benchmarks.source_benchmark --provider tikhub --keyword "寻找开发团队"
```

Run all configured first-round providers against the full 20-query set:

```bash
python -m backend.benchmarks.source_benchmark
```

Outputs are written to `backend/benchmarks/output/` as CSV and JSON. The output directory is gitignored because real search results should not be committed by default.

## Decision gate

Do not wire any provider into production `scan` until we have at least one real benchmark run. A provider should only graduate to a production connector if:

- latest sorting is actually latest in returned data;
- publish timestamps are reliable enough for freshness scoring;
- original note IDs/links are usable;
- repeated queries do not have unacceptable failure/timeout rates;
- commercial terms and our intended public-data use are acceptable;
- expected daily cost is reasonable at the target polling interval.

Browser Helper remains a fallback and validation tool even after a provider is selected.
