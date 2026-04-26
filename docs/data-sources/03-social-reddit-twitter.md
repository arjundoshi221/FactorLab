# Social Signals — Reddit & Twitter/X

> Status: `[design]`

## Purpose
Capture retail attention and sentiment signals around tickers, sectors, and themes. Inputs to factors like attention-momentum, sentiment-divergence, and theme-detection. Treat as **noisy alternative data** — useful only after careful normalization and decay handling.

## Sources & access strategy

### Reddit
| Approach | Notes |
|----------|-------|
| **Official PRAW API** (preferred) | OAuth app credentials. Free tier: 60 reqs/min. Sufficient for a curated subreddit list. |
| Pushshift / arctic_shift archives | Fallback for historical bulk; access has been intermittent post-2023 — verify availability before depending on it. |
| Direct HTML scrape | Last resort; brittle and against ToS. Avoid. |

Subreddit universe (initial): `r/wallstreetbets`, `r/stocks`, `r/investing`, `r/SecurityAnalysis`, `r/IndianStockMarket`, `r/ValueInvesting`, `r/Daytrading`. Configurable in `configs/social/reddit_subs.yaml`.

### Twitter / X
| Approach | Notes |
|----------|-------|
| **Official X API v2** | Paid tier basically required for any volume; Basic tier ~$200/mo for 10k tweets/month — quota-tight. |
| `snscrape` / similar | Has been rate-limited and broken on/off since 2023; do **not** depend on it for production. |
| Curated list of accounts via RSS-bridges or Nitter mirrors | Fragile but workable for a small set of high-signal accounts. |
| Third-party aggregators (e.g. **Apify**, **TwitterAPI.io**, **Decodo**) | Cost-effective for moderate volume. Vendor risk. |

Recommend: **start with Reddit only, defer Twitter until a specific factor design demands it.** Twitter access has been unstable enough that building factors on it without a reliable source is wasted effort.

## Auth
```
.env
  REDDIT_CLIENT_ID=
  REDDIT_CLIENT_SECRET=
  REDDIT_USER_AGENT=factorlab/0.1 (by /u/username)
  TWITTER_BEARER_TOKEN=          # if/when subscribed
```

## Rate limits
- Reddit (PRAW): 60 reqs/min sustained
- Twitter Basic: 10k tweets/month, 100 reqs/15-min window

## Schema mapping (canonical)

`alt_social.posts`:
```sql
post_id            uuid PRIMARY KEY DEFAULT gen_random_uuid()
source             text NOT NULL              -- 'reddit', 'twitter'
source_post_id     text NOT NULL              -- platform native id
author_id          text                       -- platform native author id
author_handle      text
subreddit          text                       -- reddit only
event_time         timestamptz NOT NULL       -- when posted
fetched_at         timestamptz NOT NULL       -- our ingest time
title              text                       -- reddit submission title; null for comments
body               text
url                text
score              int                        -- reddit score / twitter likes
num_comments       int
language           text
raw_payload_id     uuid                       -- FK to raw archive
UNIQUE (source, source_post_id)
```

`alt_social.post_tickers` (post → ticker mapping, NER-derived):
```sql
post_id            uuid REFERENCES alt_social.posts
security_id        uuid REFERENCES ref.securities
confidence         numeric(3,2)
extraction_method  text                       -- 'cashtag', 'ner_model', 'manual'
PRIMARY KEY (post_id, security_id)
```

`alt_social.post_sentiment` (computed):
```sql
post_id            uuid REFERENCES alt_social.posts
model              text NOT NULL              -- 'finbert-v1', etc.
sentiment          numeric(4,3)               -- -1..1
confidence         numeric(3,2)
computed_at        timestamptz NOT NULL
PRIMARY KEY (post_id, model)
```

## Pipeline

```
hourly  →  poll subreddit list for new submissions + top-level comments
        →  raw payload to alt_social.raw_reddit
        →  parse → alt_social.posts
        ↓
async   →  ticker extraction (cashtag regex + NER model)
        →  populate alt_social.post_tickers
        ↓
async   →  sentiment scoring (FinBERT or similar)
        →  alt_social.post_sentiment
        ↓
nightly →  derive aggregates: ticker × day × (mention_count, avg_sentiment, score-weighted)
        →  derived.social_features
```

## Storage
- Raw: `alt_social.raw_reddit`, `alt_social.raw_twitter` (jsonb + fetched_at + source_url)
- Posts: `alt_social.posts`
- Mappings: `alt_social.post_tickers`, `alt_social.post_sentiment`
- Aggregates: `derived.social_features` (ticker × day × feature_set)

## Edge cases
- **Spam/bot accounts** — cluster suspicious authors and exclude. Start with a simple "low karma + recent account" filter.
- **Pump-and-dump coordination** — anomalously synchronous posting around a small-cap should be a feature, not filtered out.
- **Cashtag false positives** — `$AAPL` is unambiguous; `$ME` could be a ticker or "me, the author". Confidence scores are not optional.
- **Deleted posts** — Reddit posts can be edited/deleted. Always preserve the version we ingested (raw archive).
- **Time zones** — Reddit timestamps are UTC; Twitter/X timestamps are UTC. Store UTC.
- **Multi-ticker posts** — one post → many securities mapping must be many-to-many.

## Compliance / safety notes
- Respect platform ToS — no scraping behind login walls
- No PII storage beyond public handles
- Don't redistribute raw post content publicly; aggregates only

## Open questions
- [ ] Twitter strategy: pay for Basic, use a third-party aggregator, or skip entirely for v1?
- [ ] Sentiment model: FinBERT (well-known, dated) vs. a fine-tuned recent LLM-based scorer?
- [ ] Ticker NER: regex+gazetteer is enough for cashtags; full NER for "Apple stock looks weak" requires more. Defer until needed?
- [ ] Historical backfill — Pushshift state-of-the-world matters for whether Phase 7a is feasible at scale.
