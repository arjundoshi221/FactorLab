/**
 * Sector (or any dimension) pivot for the Trades page's two views. The API labels every
 * filtered row and returns per-bucket figures (`dims=`), so buckets cover full history, not
 * just the loaded page; choosing a bucket filters the list on the server (`bucket=`).
 * URL: `pivot=<dimension>` and `bucket=<id>` (`bucket=none` for Unclassified).
 */
import type { ReactNode } from "react";

import { useUrlParams } from "../shared/urlState";
import { useDimensions } from "./pivot";
import { PivotControl } from "./PortfolioUI";
import type { AttrValue, Attributed, Dimension } from "./types";

export interface TradePivot {
  dimensions: Dimension[];
  /** The chosen dimension, once `/portfolio/dimensions` lists it. */
  dimension: Dimension | undefined;
  /** The chosen bucket id (null = Unclassified), or undefined for every bucket. */
  bucket: string | null | undefined;
  /** Query parameters for the API: `dims` and `bucket`. */
  query: Record<string, string>;
  setPivot: (dimension: string) => void;
  setBucket: (bucket: string | null | undefined) => void;
}

export function useTradePivot(): TradePivot {
  const [params, update] = useUrlParams();
  const dimensions = useDimensions();
  const dimension = dimensions.find(item => item.id === params.get("pivot"));
  const raw = params.get("bucket");
  const bucket = !dimension || raw === null ? undefined : raw === "none" ? null : raw;
  const query: Record<string, string> = dimension ? { dims: dimension.id } : {};
  if (dimension && bucket !== undefined) query.bucket = `${dimension.id}:${bucket ?? ""}`;
  return {
    dimensions, dimension, bucket, query,
    setPivot: id => update(next => { if (id) next.set("pivot", id); else next.delete("pivot"); next.delete("bucket"); next.delete("offset"); }, true),
    setBucket: id => update(next => { if (id === undefined) next.delete("bucket"); else next.set("bucket", id ?? "none"); next.delete("offset"); }, true),
  };
}

/** The "Pivot by" select for the Trades filter bar. */
export function TradePivotControl({ pivot }: { pivot: TradePivot }) {
  return <PivotControl dimensions={pivot.dimensions} value={pivot.dimension?.id ?? ""} onChange={pivot.setPivot} />;
}

/** The row's bucket label for the chosen dimension, shown under the ticker. */
export function BucketLabel({ row, dimension }: { row: Attributed; dimension: Dimension | undefined }) {
  const value = dimension ? row.attributes?.[dimension.id] : undefined;
  return value ? <small className="trade-bucket-label">{value.label}</small> : null;
}

/**
 * One row per bucket; selecting a row filters the list to it, selecting it again clears it.
 * `cells` renders the bucket's figures; `headers` names them.
 */
export function BucketTable<B extends AttrValue>({ pivot, buckets, failed, headers, cells, caption }: {
  pivot: TradePivot; buckets: B[] | undefined; failed: boolean; headers: string[]; cells: (bucket: B) => ReactNode; caption: string;
}) {
  const dimension = pivot.dimension;
  if (!dimension) return null;
  const name = dimension.label.toLowerCase();
  if (failed) return <p className="portfolio-notice portfolio-notice--error" role="alert">{dimension.label} classification is unavailable right now, so the list is not filtered by {name}.</p>;
  if (!buckets) return null;
  const active = buckets.find(bucket => bucket.id === pivot.bucket);
  return <section className="trade-pivot" aria-label={`${caption} by ${name}`}>
    <div className="trade-pivot__head"><h3>{caption} by {name}</h3>
      {pivot.bucket !== undefined && <button type="button" className="portfolio-button portfolio-button--ghost" onClick={() => pivot.setBucket(undefined)}>Show every {name}</button>}</div>
    <div className="portfolio-table-scroll" role="region" aria-label={`${caption} by ${name} table`} tabIndex={0}><table aria-label={`${caption} by ${name}`}>
      <thead><tr><th scope="col">{dimension.label}</th>{headers.map(header => <th key={header} scope="col" className="portfolio-numeric">{header}</th>)}</tr></thead>
      <tbody>{buckets.map(bucket => {
        const selected = bucket.id === pivot.bucket;
        return <tr key={bucket.id ?? ""} className={selected ? "is-selected" : ""}>
          <th scope="row"><button type="button" className="trade-pivot__pick" aria-pressed={selected} onClick={() => pivot.setBucket(selected ? undefined : bucket.id)}>{bucket.label}</button></th>
          {cells(bucket)}
        </tr>;
      })}</tbody>
    </table></div>
    {pivot.bucket !== undefined && !active && <p className="portfolio-footnote">No rows in the chosen {name} match the other filters.</p>}
  </section>;
}
