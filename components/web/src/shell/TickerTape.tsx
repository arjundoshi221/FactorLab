import type { FuturesResponse } from "../futuresTypes";
import type { IndexOverview } from "../indexTypes";
import { useRemote } from "../shared/api";
import { direction, price, signedPct } from "../shared/market";

interface Quote { key: string; symbol: string; last: string; change: string; dir: "up" | "down" | "flat"; href: string; stale?: boolean }

export function tapeQuotes(indexes: IndexOverview | null, futures: FuturesResponse | null): Quote[] {
  const quotes: Quote[] = [];
  for (const item of Array.isArray(indexes?.tracked) ? indexes.tracked : []) {
    if (typeof item.value !== "number") continue;
    quotes.push({ key: `i-${item.code}`, symbol: item.code, last: price(item.value), change: signedPct(item.change), dir: direction(item.change), href: "/market-data?view=indexes" });
  }
  for (const root of Array.isArray(futures?.items) ? futures.items : []) {
    const front = Array.isArray(root.contracts) ? root.contracts.find((contract) => typeof contract.last === "number") : undefined;
    if (!front) continue;
    quotes.push({
      key: `f-${front.contract_id}`, symbol: root.root, last: price(front.last), change: signedPct(front.change_pct),
      dir: direction(front.change_pct), stale: front.stale,
      href: `/market-data?view=futures&group=${root.group}&root=${encodeURIComponent(root.root)}`,
    });
  }
  return quotes;
}

const ARROWS = { up: "▲", down: "▼", flat: "■" } as const;

function Item({ quote, copy = false }: { quote: Quote; copy?: boolean }) {
  return (
    <a className={`term-tape__item t-num--${quote.dir}${quote.stale ? " is-stale" : ""}`} href={quote.href} tabIndex={copy ? -1 : undefined}>
      <b>{quote.symbol}</b>
      <span>{quote.last}</span>
      <em>{ARROWS[quote.dir]} {quote.change}</em>
    </a>
  );
}

/** Scrolling index and front-month futures quotes; pauses on hover and under reduced motion. */
export function TickerTape() {
  const indexes = useRemote<IndexOverview>("/hub/api/v1/us/indexes", 60_000);
  const futures = useRemote<FuturesResponse>("/hub/api/v1/us/futures", 60_000);
  const quotes = tapeQuotes(indexes.data, futures.data);
  return (
    <div className="term-tape" aria-label="Market tape">
      {quotes.length === 0 ? (
        <span className="term-tape__empty">{indexes.loading || futures.loading ? "Connecting to quotes…" : "No quotes recorded yet"}</span>
      ) : (
        <div className="term-tape__track" style={{ animationDuration: `${Math.max(quotes.length * 4, 30)}s` }}>
          <div className="term-tape__run">{quotes.map((quote) => <Item key={quote.key} quote={quote} />)}</div>
          <div className="term-tape__run" aria-hidden="true">{quotes.map((quote) => <Item key={quote.key} quote={quote} copy />)}</div>
        </div>
      )}
    </div>
  );
}
