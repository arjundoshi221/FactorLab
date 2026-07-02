# Earnings Call Transcripts — NLP Sentiment & Keyword Signals

> Status: `[design]` · Last updated: 2026-04-28

## Purpose

Earnings calls contain forward-looking language that financial statements don't. Management tone, hedging language, and specific keyword usage predict future performance. NLP on transcripts extracts:
- **Sentiment shift** — is management more or less optimistic than last quarter?
- **Guidance language** — "confident", "challenging", "headwinds", "tailwinds"
- **Topic frequency** — mentions of "AI", "pricing power", "inventory", "restructuring"
- **Q&A tone** — analyst questions reveal what the market is worried about

---

## Part 1 — Data Sources

### 1A. Finnhub Transcripts (PRIMARY — free tier)

| Field | Detail |
|-------|--------|
| URL | https://finnhub.io/docs/api/earnings-call-transcripts |
| Auth | Free API key (60 req/min) |
| Coverage | US companies, recent quarters |
| Format | JSON — structured by speaker, with participant list |

```
GET /api/v1/stock/transcripts?id=AAPL_2024Q4&token=KEY
GET /api/v1/stock/transcripts/list?symbol=AAPL&token=KEY
```

Returns: `transcript[]` with `speaker`, `speech` (text), `section` (Prepared Remarks / Q&A)

### 1B. SEC EDGAR 8-K Filings (FREE, raw)

| Field | Detail |
|-------|--------|
| URL | EDGAR full-text search |
| Tool | `edgartools` Python library |
| Coverage | All US public companies |
| Lag | Filed within 4 business days of the call |

Some companies file full transcripts as 8-K exhibits (Item 2.02). Not universal — use Finnhub as primary, EDGAR as supplement.

### 1C. Paid Sources (future)

| Source | Cost | Notes |
|--------|------|-------|
| Seeking Alpha | $20/mo | Full transcripts, broad coverage |
| S&P Capital IQ | $$$$$ | Enterprise-grade, structured |
| Refinitiv | $$$$$ | Historical depth |

---

## Part 2 — Key Signals

| Signal | Method | Alpha Thesis |
|--------|--------|--------------|
| **Sentiment delta** | Compare LLM sentiment score Q/Q for same company | Improving tone → positive drift |
| **Hedging language** | Count "uncertain", "challenging", "difficult", "risk" | Increasing hedge words = negative |
| **Confidence language** | Count "confident", "strong", "momentum", "record" | But filter for boilerplate |
| **Guidance specificity** | Specific numbers vs vague ranges | More specific = more confident |
| **Q&A divergence** | Sentiment(prepared remarks) - Sentiment(Q&A) | Large gap = management spinning |
| **Novel topics** | New keywords vs prior 4 quarters | First mention of "restructuring" or "strategic review" |

---

## Part 3 — Schema

### `alt_research.earnings_transcripts`
```sql
transcript_id      text PRIMARY KEY           -- e.g., 'AAPL_2024Q4'
security_id        uuid REFERENCES ref.securities
ticker             text NOT NULL
fiscal_quarter     text NOT NULL              -- '2024Q4'
call_date          date NOT NULL
num_speakers       int
transcript_text    text                       -- full text (for re-processing)
source             text NOT NULL DEFAULT 'finnhub'
fetched_at         timestamptz NOT NULL DEFAULT now()
```

### `alt_research.transcript_signals` (derived)
```sql
transcript_id      text REFERENCES alt_research.earnings_transcripts
signal_type        text NOT NULL              -- 'sentiment', 'hedging', 'confidence', etc.
signal_value       numeric NOT NULL           -- score
section            text                       -- 'prepared', 'qa', 'full'
model_version      text                       -- which NLP model produced this
computed_at        timestamptz NOT NULL DEFAULT now()
PRIMARY KEY (transcript_id, signal_type, section)
```

---

## Part 4 — Implementation Order

1. Finnhub transcript fetcher — free, structured JSON
2. Store raw transcripts in Postgres
3. LLM-based sentiment scoring (Claude Haiku for cost efficiency)
4. Q/Q sentiment delta computation
5. Keyword frequency tracking

---

## Open Questions

- [ ] LLM cost for scoring: Haiku at ~$0.25/1M tokens — estimate cost for full S&P 500 quarterly
- [ ] India earnings calls: mostly in English for large-caps. Finnhub coverage for NSE-listed?
- [ ] Historical depth: how far back does Finnhub free tier go?
- [ ] Pre-built NLP models (FinBERT) vs LLM for sentiment — tradeoff: speed vs accuracy
