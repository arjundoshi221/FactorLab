# Research Ideation — arxiv Quant Papers

> Status: `[design]`

## Purpose
Continuously surface new research relevant to factor design, portfolio construction, and ML-for-finance. The output is a **searchable, ranked, deduplicated index** of papers + structured summaries — fuel for ideation, not signal generation.

This is an **assistant-augmented** stream: papers get ingested, embedded, summarized, and tagged. The endpoint is a researcher (you) browsing, not a backtest consuming.

## Source
arxiv.org via two interfaces:
- **OAI-PMH** (`http://export.arxiv.org/oai2`) — bulk metadata harvesting, structured XML, no rate limits if respectful (8s between requests)
- **arxiv API** (`http://export.arxiv.org/api/query`) — search-oriented, more flexible, rate-limited (3s between requests recommended)

PDF download: papers are at `https://arxiv.org/pdf/<id>.pdf`. Use mirrors politely or the export.arxiv.org domain.

## Categories of interest
| Category | Description |
|----------|-------------|
| `q-fin.PM` | Portfolio management |
| `q-fin.ST` | Statistical finance |
| `q-fin.CP` | Computational finance |
| `q-fin.TR` | Trading & market microstructure |
| `q-fin.RM` | Risk management |
| `q-fin.MF` | Mathematical finance |
| `q-fin.PR` | Pricing of securities |
| `q-fin.GN` | General finance |
| `cs.LG` | Machine learning (filtered for finance keywords) |
| `stat.ML` | ML/stats (filtered) |
| `econ.EM` | Econometrics |

`cs.LG` and `stat.ML` are huge; filter by keyword whitelist (`portfolio`, `factor`, `volatility`, `regime`, `signal`, `alpha`, `pricing`, `risk premium`, ...).

## Auth
None required. Be polite: identify yourself in User-Agent (`factorlab-research-bot/0.1; mailto:<your-email>`).

## Rate limits
- OAI-PMH: 8 seconds between requests (recommended; overrun → 503)
- arxiv API: 3 seconds between requests
- PDF mirror: be conservative; don't hammer

A daily batch of ~50–200 new papers across q-fin + filtered cs.LG is comfortably within these limits.

## Schema mapping (canonical)

`alt_research.papers`:
```sql
paper_id           uuid PRIMARY KEY DEFAULT gen_random_uuid()
arxiv_id           text NOT NULL UNIQUE        -- '2604.12345'
version            int NOT NULL DEFAULT 1
title              text NOT NULL
abstract           text NOT NULL
authors            text[] NOT NULL
primary_category   text NOT NULL
categories         text[] NOT NULL
submitted_at       timestamptz NOT NULL
updated_at         timestamptz NOT NULL
pdf_url            text NOT NULL
doi                text
journal_ref        text
fetched_at         timestamptz NOT NULL
```

`alt_research.paper_full_text`:
```sql
paper_id           uuid REFERENCES alt_research.papers PRIMARY KEY
extracted_text     text                        -- full PDF text
section_headers    text[]                      -- extracted headings
extraction_method  text                        -- 'pdfplumber', 'grobid', etc.
extracted_at       timestamptz NOT NULL
```

`alt_research.paper_embeddings`:
```sql
paper_id           uuid REFERENCES alt_research.papers
model              text NOT NULL               -- 'voyage-3', 'text-embedding-3-large', etc.
embedding          vector(1536)                -- pgvector extension
computed_at        timestamptz NOT NULL
PRIMARY KEY (paper_id, model)
```

`alt_research.paper_summaries`:
```sql
paper_id           uuid REFERENCES alt_research.papers
model              text                        -- LLM used
summary_type       text                        -- 'tldr', 'methods', 'results', 'limitations'
content            text NOT NULL
generated_at       timestamptz NOT NULL
PRIMARY KEY (paper_id, model, summary_type)
```

`alt_research.paper_tags`:
```sql
paper_id           uuid REFERENCES alt_research.papers
tag                text                        -- 'momentum', 'low-vol', 'transformer', 'reinforcement-learning'
confidence         numeric(3,2)
PRIMARY KEY (paper_id, tag)
```

## Pipeline

```
daily 02:00 UTC
        │
        ▼
OAI-PMH harvest: list papers added/updated since last run for q-fin.* and cs.LG
        │
        ▼
filter cs.LG by keyword whitelist
        │
        ▼
dedupe vs existing papers (by arxiv_id + version)
        │
        ▼
write metadata → alt_research.papers (raw payload archived)
        │
        ▼
async download PDFs → S3 / local pdf_store/  (gitignored)
        │
        ▼
extract text (pdfplumber + grobid for structure) → alt_research.paper_full_text
        │
        ▼
embed (voyage-3 or OpenAI) → alt_research.paper_embeddings
        │
        ▼
LLM summarize (TL;DR + methods + results) → alt_research.paper_summaries
        │
        ▼
LLM tag (factor type, asset class, methodology) → alt_research.paper_tags
        │
        ▼
weekly digest: top-N novel papers ranked by similarity to active research interests
```

## Storage
- Raw: `alt_research.raw_arxiv_oai` (XML payloads), PDFs in object storage / local `data/pdfs/`
- Fact: `alt_research.papers`, `alt_research.paper_full_text`, `alt_research.paper_embeddings`, `alt_research.paper_summaries`, `alt_research.paper_tags`
- Index: `pgvector` HNSW index on `paper_embeddings` for similarity search

## Querying patterns
1. **Topic search**: "show me papers on cross-sectional momentum in emerging markets" → embed query → top-K cosine similarity in pgvector
2. **Author follow**: "everything by [author]" → SQL on `authors` array
3. **Recency**: "this week's most cited or downloaded q-fin papers" → join with citation source if added
4. **Cross-source**: "papers that match factors I'm currently researching" → embed your factor research notes, similarity-search against papers

## Edge cases
- **Versioning** — papers get re-uploaded (`v1` → `v2`). Track all versions; the latest summary may be stale.
- **PDF extraction quality** — math-heavy papers extract poorly with pdfplumber. Consider grobid or Mathpix for v2.
- **Cross-listing** — a paper in `q-fin.PM` may also be in `cs.LG`; dedupe by `arxiv_id`.
- **Withdrawals** — papers can be withdrawn. Mark `status='withdrawn'` rather than deleting.
- **Embedding model drift** — re-embedding the entire corpus is expensive. Tag the model version per row; only re-embed when changing model. Keep old embeddings until cutover.
- **LLM summary cost** — at $0.01–0.10 per paper, 10k papers/year is manageable; full-corpus retag on prompt change is not. Be deliberate about re-runs.

## Beyond arxiv (future)
| Source | Notes |
|--------|-------|
| **SSRN** (Social Science Research Network) | Massive finance research repository; harder to scrape, no clean API |
| **NBER working papers** | Macro / empirical finance; RSS feed available |
| **Federal Reserve / BIS / IMF** | Working papers; per-org RSS or scraping |
| **Conference proceedings** (NeurIPS, ICML for ML; AFA, EFA for finance) | Annual batches |

Add as separate sources, same schema pattern. arxiv first — it covers most of cs.LG and a meaningful chunk of q-fin.

## Open questions
- [ ] Embedding provider choice: OpenAI `text-embedding-3-large`, Voyage `voyage-3`, or a local model? (Latency and cost tradeoffs.)
- [ ] LLM for summaries: Claude / GPT / open-weight? (Quality vs. cost; you presumably already have API access.)
- [ ] Full-text storage size — at ~50KB/paper × 50k papers = 2.5GB. Postgres handles fine; just mind backups.
- [ ] Citation graph: do we want to ingest citations (semantic scholar API) for influence ranking?
- [ ] Should this be query-time (LLM-over-the-corpus) or pre-computed summaries? (Likely both: pre-compute TL;DR, on-demand deep summaries.)
