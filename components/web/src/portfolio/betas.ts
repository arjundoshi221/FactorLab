import { useEffect, useState } from "react";

import type { InstrumentPage, PriceHistory } from "../instrumentTypes";
import { fetchJson } from "../shared/api";
import { estimateBeta, type BetaMap } from "./riskModel";

const MARKET = "/hub/api/v1/market-data";
/** Benchmark for beta: the S&P 500 ETF, looked up by symbol among US listings. */
export const BENCHMARK = "SPY";

export interface BetaDetail { beta: number; points: number }
export interface BetaResult {
  /** Measured betas by listing ID; null while loading. */
  betas: BetaMap | null;
  details: Record<string, BetaDetail>;
  /** Why betas could not be measured at all (e.g. no benchmark history); per-position gaps fall back to 1.0. */
  problem: string | null;
}

const cache = new Map<string, BetaResult>();

async function measure(listingIds: string[]): Promise<BetaResult> {
  const found = await fetchJson<InstrumentPage>(`${MARKET}/instruments?${new URLSearchParams({ search: BENCHMARK, kind: "listing", country: "US", limit: "20" })}`);
  const benchmark = found.items.find(item => item.symbol.toUpperCase() === BENCHMARK && item.active) ?? found.items.find(item => item.symbol.toUpperCase() === BENCHMARK);
  if (!benchmark) return { betas: {}, details: {}, problem: `${BENCHMARK} is not in the instrument reference, so betas are assumed 1.0.` };
  const prices = (id: string) => fetchJson<PriceHistory>(`${MARKET}/listings/${encodeURIComponent(id)}/prices?range=1y`);
  const [bench, ...assets] = await Promise.allSettled([prices(benchmark.listing_id || benchmark.id), ...listingIds.map(prices)]);
  if (bench.status !== "fulfilled" || bench.value.bars.length < 60) return { betas: {}, details: {}, problem: `Not enough ${BENCHMARK} price history to measure betas; they are assumed 1.0.` };
  const betas: BetaMap = {};
  const details: Record<string, BetaDetail> = {};
  assets.forEach((result, index) => {
    if (result.status !== "fulfilled") return;
    const estimate = estimateBeta(result.value.bars, bench.value.bars);
    if (estimate) { betas[listingIds[index]] = estimate.beta; details[listingIds[index]] = estimate; }
  });
  return { betas, details, problem: null };
}

/** One-year daily betas to the benchmark for the given listings; cached per set of listings. */
export function useBetas(listingIds: string[]): BetaResult {
  const key = [...new Set(listingIds)].sort().join(",");
  const [result, setResult] = useState<BetaResult>(() => cache.get(key) ?? { betas: key ? null : {}, details: {}, problem: null });
  useEffect(() => {
    const cached = cache.get(key);
    if (cached || !key) { setResult(cached ?? { betas: {}, details: {}, problem: null }); return; }
    let live = true;
    setResult({ betas: null, details: {}, problem: null });
    measure(key.split(",")).then(
      value => { cache.set(key, value); if (live) setResult(value); },
      () => { if (live) setResult({ betas: {}, details: {}, problem: "Price history is unavailable, so betas are assumed 1.0." }); },
    );
    return () => { live = false; };
  }, [key]);
  return result;
}
