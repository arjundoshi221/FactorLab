import { useRef, useState, type MouseEvent } from "react";

import { useWidth } from "./useWidth";

export interface LineSeries {
  key: string;
  label: string;
  /** Values by date (ISO `YYYY-MM-DD`); dates missing from a series leave a gap in it. */
  points: { t: string; value: number }[];
  /** A CSS colour, normally a token such as `var(--series-1)`. */
  color: string;
  dashed?: boolean;
}

/**
 * Several series over shared dates, plotted by date index so closed sessions leave no gaps.
 * A legend names every series; hovering shows each value on that date. An optional zero
 * line marks the baseline of cumulative returns.
 */
export function LineChart({ series, label, format, formatTime, zero = false, height = 240 }: {
  series: LineSeries[];
  label: string;
  format: (value: number) => string;
  formatTime: (t: string) => string;
  zero?: boolean;
  height?: number;
}) {
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [box, measured] = useWidth<HTMLDivElement>(900);
  const lines = series.filter((line) => line.points.length > 1);
  const dates = Array.from(new Set(lines.flatMap((line) => line.points.map((point) => point.t)))).sort();
  if (!lines.length || dates.length < 2) return <div className="t-chart t-chart--empty" style={{ height }}>Not enough history to chart yet</div>;
  const width = Math.max(measured, 240);
  const pad = { top: 12, right: 64, bottom: 22, left: 8 };
  const index = new Map(dates.map((date, position) => [date, position]));
  const values = lines.flatMap((line) => line.points.map((point) => point.value));
  if (zero) values.push(0);
  const low = Math.min(...values), high = Math.max(...values);
  const span = high - low || Math.abs(high) * 0.01 || 1;
  const lo = low - span * 0.06, hi = high + span * 0.06;
  const x = (position: number) => pad.left + (position / (dates.length - 1)) * (width - pad.left - pad.right);
  const y = (value: number) => pad.top + (1 - (value - lo) / (hi - lo)) * (height - pad.top - pad.bottom);
  const ticks = [hi - (hi - lo) * 0.1, (hi + lo) / 2, lo + (hi - lo) * 0.1];
  const path = (line: LineSeries) => line.points
    .map((point, position) => `${position ? "L" : "M"}${x(index.get(point.t) ?? 0).toFixed(1)},${y(point.value).toFixed(1)}`).join("");

  function onMove(event: MouseEvent<SVGSVGElement>) {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect || !rect.width) return;
    const ratio = ((event.clientX - rect.left) / rect.width * width - pad.left) / (width - pad.left - pad.right);
    setHover(Math.max(0, Math.min(dates.length - 1, Math.round(ratio * (dates.length - 1)))));
  }

  const focus = hover === null ? null : dates[hover];
  const valueAt = (line: LineSeries, date: string) => line.points.find((point) => point.t === date)?.value;
  return (
    <div className="t-chart t-chart--lines" ref={box}>
      <ul className="t-chart__legend">{lines.map((line) => (
        <li key={line.key}>
          <svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" style={{ stroke: line.color }} strokeWidth="2" strokeDasharray={line.dashed ? "5 3" : undefined} /></svg>
          <span>{line.label}</span>
          <b>{format(focus ? valueAt(line, focus) ?? NaN : line.points[line.points.length - 1].value)}</b>
        </li>
      ))}</ul>
      <svg ref={ref} viewBox={`0 0 ${width} ${height}`} role="img"
        aria-label={`${label}: ${lines.map((line) => `${line.label} ${format(line.points[line.points.length - 1].value)}`).join(", ")} on ${formatTime(dates[dates.length - 1])}`}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {ticks.map((tick) => (
          <g key={tick}>
            <line className="t-chart__grid" x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} />
            <text className="t-chart__axis" x={width - pad.right + 6} y={y(tick) + 3}>{format(tick)}</text>
          </g>
        ))}
        {zero && <line className="t-chart__ref" x1={pad.left} x2={width - pad.right} y1={y(0)} y2={y(0)} />}
        {lines.map((line) => (
          <path key={line.key} d={path(line)} fill="none" style={{ stroke: line.color }} strokeWidth="2" strokeLinejoin="round"
            strokeDasharray={line.dashed ? "6 4" : undefined} />
        ))}
        <text className="t-chart__axis" x={pad.left} y={height - 6}>{formatTime(dates[0])}</text>
        <text className="t-chart__axis" x={width - pad.right} y={height - 6} textAnchor="end">{formatTime(dates[dates.length - 1])}</text>
        {focus && hover !== null && (
          <g>
            <line className="t-chart__cross" x1={x(hover)} x2={x(hover)} y1={pad.top} y2={height - pad.bottom} />
            {lines.map((line) => {
              const value = valueAt(line, focus);
              return value === undefined ? null : <circle key={line.key} cx={x(hover)} cy={y(value)} r="3.5" style={{ fill: line.color }} />;
            })}
            <text className="t-chart__axis" x={x(hover)} y={pad.top - 2} textAnchor="middle">{formatTime(focus)}</text>
          </g>
        )}
      </svg>
    </div>
  );
}
