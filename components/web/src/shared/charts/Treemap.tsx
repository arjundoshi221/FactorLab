import type { ReactNode } from "react";

export interface TreemapItem {
  key: string;
  label: string;
  /** Area: must be positive (absolute market value, count, weight…). */
  size: number;
  /** Colour value: a signed change mapped onto the diverging up/down scale. */
  change?: number | null;
  /** Scale for |change| at full saturation (e.g. 0.03 for ±3%). */
  scale?: number;
  sublabel?: string;
  title?: string;
  href?: string;
  onSelect?: () => void;
  selected?: boolean;
}

export interface Rect { x: number; y: number; w: number; h: number }

function worst(row: number[], side: number): number {
  const sum = row.reduce((total, value) => total + value, 0);
  const max = Math.max(...row);
  const min = Math.min(...row);
  return Math.max((side * side * max) / (sum * sum), (sum * sum) / (side * side * min));
}

/** Squarified treemap layout (Bruls et al.); returns one rect per area in input order of the sorted list. */
export function squarify(areas: number[], box: Rect): Rect[] {
  const total = areas.reduce((sum, value) => sum + value, 0);
  if (!areas.length || total <= 0) return [];
  const scale = (box.w * box.h) / total;
  const scaled = areas.map((value) => value * scale);
  const rects: Rect[] = [];
  let free = { ...box };
  let index = 0;
  while (index < scaled.length) {
    const side = Math.min(free.w, free.h);
    const row = [scaled[index]];
    index += 1;
    while (index < scaled.length && worst([...row, scaled[index]], side) <= worst(row, side)) {
      row.push(scaled[index]);
      index += 1;
    }
    const rowSum = row.reduce((sum, value) => sum + value, 0);
    if (free.w >= free.h) {
      const width = rowSum / free.h;
      let y = free.y;
      for (const value of row) {
        const height = value / width;
        rects.push({ x: free.x, y, w: width, h: height });
        y += height;
      }
      free = { x: free.x + width, y: free.y, w: free.w - width, h: free.h };
    } else {
      const height = rowSum / free.w;
      let x = free.x;
      for (const value of row) {
        const width = value / height;
        rects.push({ x, y: free.y, w: width, h: height });
        x += width;
      }
      free = { x: free.x, y: free.y + height, w: free.w, h: free.h - height };
    }
  }
  return rects;
}

/** Fill for a signed change on the diverging up/down scale; neutral when unknown. */
export function heatFill(change: number | null | undefined, scale = 0.03): string {
  if (change === null || change === undefined || Number.isNaN(change)) return "var(--raised)";
  const t = Math.min(Math.abs(change) / scale, 1);
  const pole = change >= 0 ? "var(--up)" : "var(--down)";
  return `color-mix(in srgb, ${pole} ${Math.round(12 + t * 50)}%, var(--panel))`;
}

/** Area-sized tiles (HTML, so labels wrap and stay text). Tiles too small for a label keep a tooltip. */
export function Treemap({ items, label, height = 260, empty }: { items: TreemapItem[]; label: string; height?: number; empty?: ReactNode }) {
  const sorted = items.filter((item) => item.size > 0).sort((a, b) => b.size - a.size);
  if (!sorted.length) return <div className="t-treemap t-treemap--empty">{empty ?? "No data"}</div>;
  const rects = squarify(sorted.map((item) => item.size), { x: 0, y: 0, w: 100, h: 100 });
  return (
    <div className="t-treemap" style={{ height }} role="group" aria-label={label}>
      {sorted.map((item, index) => {
        const rect = rects[index];
        const small = rect.w * rect.h < 60;
        const style = {
          left: `${rect.x}%`, top: `${rect.y}%`, width: `${rect.w}%`, height: `${rect.h}%`,
          background: heatFill(item.change, item.scale),
        };
        const body = <>
          {!small && <strong>{item.label}</strong>}
          {!small && item.sublabel && <small>{item.sublabel}</small>}
        </>;
        const common = { className: `t-treemap__tile${item.selected ? " is-selected" : ""}`, style, title: item.title ?? item.label };
        if (item.href) return <a key={item.key} {...common} href={item.href} aria-label={item.title ?? item.label}>{body}</a>;
        if (item.onSelect) {
          return <button key={item.key} type="button" {...common} aria-pressed={item.selected} aria-label={item.title ?? item.label} onClick={item.onSelect}>{body}</button>;
        }
        return <div key={item.key} {...common} role="img" aria-label={item.title ?? item.label}>{body}</div>;
      })}
    </div>
  );
}
