import { useRef, useState, type MouseEvent } from "react";

import { useWidth } from "./useWidth";

import { direction } from "../market";

export interface TimePoint { t: string; value: number }

/**
 * One series over time, plotted by index so closed sessions leave no gaps. Colour follows
 * the net change over the window; a dashed reference (e.g. previous close) is optional.
 */
export function AreaChart({ points, label, format, formatTime, reference, referenceLabel, height = 220 }: {
  points: TimePoint[];
  label: string;
  format: (value: number) => string;
  formatTime: (t: string) => string;
  reference?: number | null;
  referenceLabel?: string;
  height?: number;
}) {
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [box, measured] = useWidth<HTMLDivElement>(900);
  if (points.length < 2) return <div className="t-chart t-chart--empty" style={{ height }}>Not enough history to chart yet</div>;
  const width = Math.max(measured, 240);
  const pad = { top: 12, right: 64, bottom: 22, left: 8 };
  const values = points.map((point) => point.value);
  if (reference !== null && reference !== undefined) values.push(reference);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = high - low || Math.abs(high) * 0.01 || 1;
  const lo = low - span * 0.06;
  const hi = high + span * 0.06;
  const x = (index: number) => pad.left + (index / (points.length - 1)) * (width - pad.left - pad.right);
  const y = (value: number) => pad.top + (1 - (value - lo) / (hi - lo)) * (height - pad.top - pad.bottom);
  const line = points.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(point.value).toFixed(1)}`).join("");
  const area = `${line}L${x(points.length - 1).toFixed(1)},${height - pad.bottom}L${x(0).toFixed(1)},${height - pad.bottom}Z`;
  const tone = direction(points[points.length - 1].value - (reference ?? points[0].value));
  const ticks = [hi - (hi - lo) * 0.1, (hi + lo) / 2, lo + (hi - lo) * 0.1];
  const last = points[points.length - 1];

  function onMove(event: MouseEvent<SVGSVGElement>) {
    const box = ref.current?.getBoundingClientRect();
    if (!box || !box.width) return;
    const ratio = ((event.clientX - box.left) / box.width * width - pad.left) / (width - pad.left - pad.right);
    setHover(Math.max(0, Math.min(points.length - 1, Math.round(ratio * (points.length - 1)))));
  }

  const focus = hover === null ? null : points[hover];
  return (
    <div className={`t-chart t-chart--${tone}`} ref={box}>
      <svg ref={ref} viewBox={`0 0 ${width} ${height}`} role="img"
        aria-label={`${label}: ${points.length} points from ${formatTime(points[0].t)} to ${formatTime(last.t)}, last ${format(last.value)}`}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {ticks.map((tick) => (
          <g key={tick}>
            <line className="t-chart__grid" x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} />
            {Math.abs(y(tick) - y(last.value)) > 14 && <text className="t-chart__axis" x={width - pad.right + 6} y={y(tick) + 3}>{format(tick)}</text>}
          </g>
        ))}
        {reference !== null && reference !== undefined && (
          <line className="t-chart__ref" x1={pad.left} x2={width - pad.right} y1={y(reference)} y2={y(reference)}>
            <title>{referenceLabel ?? "Reference"} {format(reference)}</title>
          </line>
        )}
        <path className="t-chart__area" d={area} />
        <path className="t-chart__line" d={line} />
        <rect className="t-chart__last" x={width - pad.right + 2} y={y(last.value) - 8} width={pad.right - 4} height={16} rx="1" />
        <text className="t-chart__last-text" x={width - pad.right + 6} y={y(last.value) + 3}>{format(last.value)}</text>
        <text className="t-chart__axis" x={pad.left} y={height - 6}>{formatTime(points[0].t)}</text>
        <text className="t-chart__axis" x={width - pad.right} y={height - 6} textAnchor="end">{formatTime(last.t)}</text>
        {focus && hover !== null && (
          <g>
            <line className="t-chart__cross" x1={x(hover)} x2={x(hover)} y1={pad.top} y2={height - pad.bottom} />
            <line className="t-chart__cross" x1={pad.left} x2={width - pad.right} y1={y(focus.value)} y2={y(focus.value)} />
            <circle className="t-chart__dot" cx={x(hover)} cy={y(focus.value)} r="3.5" />
          </g>
        )}
      </svg>
      {focus && (
        <div className="t-chart__tip" role="status">
          <span>{formatTime(focus.t)}</span>
          <b>{format(focus.value)}</b>
        </div>
      )}
    </div>
  );
}
