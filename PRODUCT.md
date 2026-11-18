# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Owner-operators: one or a few people running their own trading book. In a single tool they read the market each day, check their portfolio's risk and tagged PnL, and keep the data pipelines that feed it healthy. They know the domain well (futures roots, GICS sectors, FIFO PnL, IBKR Flex, ClickHouse) and use the hub as a working terminal, not a showcase. Other people with access are testers who share the same view; there is no separate audience.

## Product Purpose

FactorLab is a private, multi-source market research platform. It collects market, broker, index and political-disclosure data (Upstox, Schwab/EODHD, iShares, IBKR Gateway and Flex, House Clerk/Congress/EDGAR). It stores raw payloads and curated point-in-time facts in ClickHouse and serves them through the hub at `arjundoshi221.com`. The hub works when an operator can make a trading or risk decision from it and trust every number they act on.

## Positioning

- **Your own book next to the market.** The IBKR account (positions, balances, Flex history), PnL tagged by theme, strategy and trade name, and the risk view sit in the same product as market, futures, index, sector and Congress-trade data. Book and market are joined at the instrument level, not linked from separate tools.
- **Point-in-time truth.** Every fact records both when it happened and when it was known. Raw responses are kept permanently and can be replayed, and curated rows trace back to their raw row and ingestion run.

## Operating Context

- Desktop-first daily use behind Cloudflare Access. Responsive layouts exist for phones, where the matrix and raw grids stay scrolling tables.
- Areas: Home (cockpit), Data (markets, catalog, schema, market data, political), Portfolio (overview, risk, trades, themes, strategies, trade names) and Ops (monitoring, pipelines, Docker images, roadmap).
- Keyboard use is expected: Ctrl+K and `/` open the command bar, and `g` then a letter jumps between areas.
- Live panels poll every 15–60 s. A failed refresh keeps the last snapshot and marks it stale.
- Times are shown per exchange and market (exchange clocks; IST for ops metadata; New York time for YTD boundaries).

## Capabilities and Constraints

- **Unavailable is never guessed.** Missing, stale, unconverted or unreconciled values show as unavailable (an em dash, "?" or a named state), never as 0, a partial sum or a healthy status. A risk verdict is never green on stale or incomplete data. A currency without an FX rate is flagged and excluded, never converted at a guessed rate.
- **Explainable figures.** Risk metrics open their exact calculation (formula, terms, FX rates, betas, denominator, unrounded result). PnL reconciles against IBKR's own figures, and mismatches are listed.
- **Pipelines are visible.** Collection health, coverage, recovery evidence and provenance are product features (Monitoring, Data matrix, Instrument explorer).
- **Read-only broker mirror.** The hub reads IBKR data and does not place orders.
- **Technical.** React + Vite SPA in `components/web` served as static files by nginx. All data comes from `/hub/api/v1/*`. The CSP is `default-src 'self'`, so there are no third-party scripts, fonts or API origins unless both nginx blocks change. There is no in-app auth; Cloudflare Access is the only login. UI colours and spacing come from tokens in `src/theme/tokens.css`.
- **Terms.** Listing vs contract; underlying; collection health vs heartbeat liveness; recovery evidence; instrument coverage; investigation candidates; base currency; operating targets vs hard limits. Definitions are in `components/web/CONTEXT.md` and `docs/features/`.

## Brand Commitments

- Name: FactorLab. The UI is called "the hub", and its main shell is "the terminal".
- Voice: precise, literal and domain-native. Labels name the exact quantity, state or source. There is no marketing tone inside the product.

## Evidence on Hand

- Real production data from the providers above, plus feature specs in `docs/features/` (F-036 to F-048).
- There are no customers, testimonials, performance claims or public pricing. Future work must not invent them.

## Product Principles

1. Trust over completeness: show less rather than show something wrong, and say why something is missing.
2. Every number can be traced to its source, its time and the calculation behind it.
3. Book and market are one context: from any position you can reach the instrument's market data, and back.
4. Built for the operator's daily loop (morning read, risk check, pipeline triage), with fast keyboard movement and dense, scannable views.
5. Data stays honest when it ages: stale data is marked stale, and failed refreshes never blank the screen.
