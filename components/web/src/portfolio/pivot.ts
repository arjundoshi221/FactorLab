/**
 * Generic pivot over rows the API has labelled with `attributes` (`dims=sector`, …).
 * Nothing here knows what a sector is: a new dimension needs only a backend resolver.
 */
import { useRemote } from "../shared/api";
import { useUrlParams } from "../shared/urlState";
import type { AttrValue, Dimension, ItemsPage } from "./types";

export const UNCLASSIFIED: AttrValue = { id: null, label: "Unclassified" };

/** The row's bucket for `dim`; a row the API did not label is Unclassified. */
export function attributeOf(row: { attributes?: Record<string, AttrValue> }, dim: string): AttrValue {
  return row.attributes?.[dim] ?? UNCLASSIFIED;
}

export interface PivotBucket<T> { key: string; value: AttrValue; rows: T[] }

/** Group rows by an attribute, in `order` (the dimension's bucket order), Unclassified last. */
export function pivotRows<T>(rows: T[], valueOf: (row: T) => AttrValue, order: AttrValue[] = []): PivotBucket<T>[] {
  const buckets = new Map<string, PivotBucket<T>>();
  for (const row of rows) {
    const value = valueOf(row);
    const key = value.id ?? "";
    const bucket = buckets.get(key) ?? { key, value, rows: [] };
    bucket.rows.push(row);
    buckets.set(key, bucket);
  }
  const rank = new Map(order.map((value, index) => [value.id ?? "", index]));
  return [...buckets.values()].sort((a, b) => {
    if ((a.value.id === null) !== (b.value.id === null)) return a.value.id === null ? 1 : -1;
    const left = rank.get(a.key) ?? order.length, right = rank.get(b.key) ?? order.length;
    return left - right || a.value.label.localeCompare(b.value.label);
  });
}

interface Scaled { digits: bigint; scale: number }

function parseDecimal(text: string): Scaled | null {
  // Python's Decimal can print an exponent ("0E-12", "1.5E+3").
  const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(text.trim());
  if (!match) return null;
  const fraction = match[3] ?? "";
  let digits = BigInt(`${match[2]}${fraction}`);
  let scale = fraction.length - Number(match[4] ?? 0);
  if (scale < 0) { digits *= 10n ** BigInt(-scale); scale = 0; }
  return { digits: match[1] === "-" ? -digits : digits, scale };
}

function formatScaled({ digits, scale }: Scaled): string {
  const negative = digits < 0n;
  const text = (negative ? -digits : digits).toString().padStart(scale + 1, "0");
  const whole = text.slice(0, text.length - scale), fraction = text.slice(text.length - scale).replace(/0+$/, "");
  const body = fraction ? `${whole}.${fraction}` : whole;
  return negative && body !== "0" ? `-${body}` : body;
}

/**
 * Exact sum of decimal strings, `sign` −1 subtracting a term. Any unavailable term makes
 * the sum unavailable: missing data is never added as zero.
 */
export function sumDecimals(terms: Array<string | null | undefined | [string | null | undefined, 1 | -1]>): string | null {
  const parsed: Scaled[] = [];
  for (const term of terms) {
    const [text, sign] = Array.isArray(term) ? term : [term, 1 as const];
    if (text === null || text === undefined || text === "") return null;
    const value = parseDecimal(text);
    if (!value) return null;
    parsed.push(sign === -1 ? { ...value, digits: -value.digits } : value);
  }
  const scale = Math.max(0, ...parsed.map(value => value.scale));
  const digits = parsed.reduce((total, value) => total + value.digits * 10n ** BigInt(scale - value.scale), 0n);
  return formatScaled({ digits, scale });
}

/** Pivot dimensions the API can label rows with, e.g. Sector. */
export function useDimensions(): Dimension[] {
  return useRemote<ItemsPage<Dimension>>("/hub/api/v1/portfolio/dimensions").data?.items ?? [];
}

export type PivotRows = "group" | "attr";

/** URL-backed pivot state: `pivot=<dimension>` and, on grouped pages, `rows=attr`. */
export function usePivotParams(): { pivot: string; rows: PivotRows; setPivot: (dim: string) => void; setRows: (rows: PivotRows) => void } {
  const [params, update] = useUrlParams();
  return {
    pivot: params.get("pivot") ?? "",
    rows: params.get("rows") === "attr" ? "attr" : "group",
    setPivot: dim => update(next => { if (dim) next.set("pivot", dim); else { next.delete("pivot"); next.delete("rows"); } }),
    setRows: rows => update(next => { if (rows === "attr") next.set("rows", "attr"); else next.delete("rows"); }),
  };
}

/** One grouping level: how a row maps to a bucket, and the buckets' display order. */
export interface Level<T> { id: string; label: string; valueOf: (row: T) => AttrValue; order?: AttrValue[] }

export interface TreeNode<T, A> {
  /** Unique within the tree: the path of bucket keys. */
  key: string; value: AttrValue; depth: number; totals: A; rows: T[];
  /** Next level's buckets; empty at the last level, where `rows` are the leaves. */
  children: TreeNode<T, A>[];
}

/** Group rows level by level (any depth), each node carrying `aggregate` of its rows. */
export function pivotTree<T, A>(rows: T[], levels: Level<T>[], aggregate: (rows: T[]) => A, depth = 0, prefix = ""): TreeNode<T, A>[] {
  const [level, ...rest] = levels;
  if (!level) return [];
  return pivotRows(rows, level.valueOf, level.order).map(bucket => {
    const key = `${prefix}/${level.id}:${bucket.key}`;
    return { key, value: bucket.value, depth, rows: bucket.rows, totals: aggregate(bucket.rows), children: pivotTree(bucket.rows, rest, aggregate, depth + 1, key) };
  });
}

/** Every node key in a tree, for "Expand all". */
export function treeKeys<T, A>(nodes: TreeNode<T, A>[]): string[] {
  return nodes.flatMap(node => [node.key, ...treeKeys(node.children)]);
}

/** Parse `group=` (comma-separated level ids), honouring the older `pivot=` / `rows=attr` links. */
export function parseGroupBy(params: URLSearchParams, defaults: string[]): string[] {
  const group = params.get("group");
  if (group !== null) {
    const ids = [...new Set(group.split(",").map(item => item.trim()).filter(Boolean))];
    if (ids.length) return ids;
  }
  const pivot = params.get("pivot"), first = defaults[0];
  if (!pivot || pivot === first) return defaults;
  return params.get("rows") === "attr" ? [pivot, first] : [first, pivot];
}

/**
 * URL-backed grouping order for tag pages and the risk exposure tree: `group=theme,sector`.
 * Writing it drops the older `pivot` / `rows` parameters.
 */
export function useGroupBy(defaults: string[]): [string[], (levels: string[]) => void] {
  const [params, update] = useUrlParams();
  const levels = parseGroupBy(params, defaults);
  return [levels, next => update(url => {
    url.delete("pivot"); url.delete("rows");
    if (next.join(",") === defaults.join(",")) url.delete("group"); else url.set("group", next.join(","));
  })];
}
