# FactorLab

Global fundamental equity research platform. Systematic approach to cross-geography
equity analysis, sector-driven capital allocation, and durable wealth compounding.

## Quick Start

```bash
# 1. Install
pip install -e ".[dev]"

# 2. Configure API key
cp .env.example .env
# Edit .env with your EODHD API key (or leave as "demo" for testing)

# 3. Fetch price data (demo tickers: AAPL.US, TSLA.US, AMZN.US, VTI.US)
python scripts/fetch_prices.py --universe demo

# 4. Fetch fundamentals (requires paid EODHD plan)
python scripts/fetch_fundamentals.py --tickers AAPL.US

# 5. Compute derived metrics
python scripts/compute_metrics.py --tickers AAPL.US

# 6. Run tests
pytest
```

## Project Structure

```
src/factorlab/
  config/       Settings (.env secrets + YAML config) and logging
  models/       Pydantic data models (PriceBar, CompanyFundamentals, etc.)
  api/          Market data providers (EODHD client, abstract base class)
  storage/      Local file storage (Parquet + JSON)
  compute/      Derived metric computation (ROIC, FCF, margins, earnings quality)
  utils/        Rate limiter, helpers

scripts/        CLI scripts for data fetching and metric computation
tests/          Test suite with real data fixtures
configs/        Non-secret application settings
data/           Local data store (gitignored)
concepts/       Research framework reference document
```

## Data Sources

| Region | Provider | Status |
|--------|----------|--------|
| US     | EODHD    | Active |
| Europe | TBD      | Planned |
| India  | TBD      | Planned |
| APAC   | TBD      | Planned |

## Architecture

- **Region-based API pattern**: Each region implements `MarketDataProvider` ABC
- **Typed data models**: Pydantic models for price bars, financial statements, derived metrics
- **Parquet storage**: Compressed, typed columnar storage for all tabular data
- **Pure compute functions**: No side effects, trivially testable metric computation
- **Secrets isolation**: API keys in `.env`, settings in YAML
