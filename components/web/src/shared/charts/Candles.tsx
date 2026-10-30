import { useRef, useState, type MouseEvent } from "react";

import { useWidth } from "./useWidth";

export interface Bar { t: string; open: number; high: number; low: number; close: number; volume: number | null }

/** OHLC candles with a volume pane and crosshair. Up candles are hollow, down candles filled. */
export function Candles({ bars, label, format, formatTime, height = 320 }: {
  bars: Bar[]; label: string; format: (value: number) => string; formatTime: (t: string) => string; height?: number;
}) {
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [box, measured] = useWidth<HTMLDivElement>(960);
  if (!bars.length) return null;
  const width = Math.max(measured, 240);
  const pad = { top: 14, right: 70, bottom: 22, left: 8 };
  const volumes = bars.map((bar) => bar.volume ?? 0);
  const hasVolume = volumes.some((value) => value > 0);
  const volumeHeight = hasVolume ? Math.round((height - pad.top - pad.bottom) * 0.2) : 0;
  const priceBottom = height - pad.bottom - volumeHeight - (hasVolume ? 6 : 0);
  const high = Math.max(...bars.map((bar) => bar.high));
  const low = Math.min(...bars.map((bar) => bar.low));
  const span = Math.max(high - low, Math.abs(high) * 0.001, 0.01);
  const y = (value: number) => pad.top + ((high - value) / span) * (priceBottom - pad.top);
  const plotWidth = width - pad.left - pad.right;
  const step = plotWidth / bars.length;
  const body = Math.max(1, Math.min(9, step * 0.64));
  const maxVolume = Math.max(...volumes, 1);
  const ticks = [high, high - span / 2, low];
  const last = bars[bars.length - 1];

  function onMove(event: MouseEvent<SVGSVGElement>) {
    const box = ref.current?.getBoundingClientRect();
    if (!box || !box.width) return;
    const offset = (event.clientX - box.left) / box.width * width - pad.left;
    setHover(Math.max(0, Math.min(bars.length - 1, Math.floor(offset / step))));
  }

  const focus = hover === null ? null : bars[hover];
  return (
    <div className="t-chart t-candles" ref={box}>
      <svg ref={ref} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {ticks.map((tick) => (
          <g key={tick}>
            <line className="t-chart__grid chart-gridline" x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} />
            {Math.abs(y(tick) - y(last.close)) > 14 && <text className="t-chart__axis chart-axis-label" x={width - pad.right + 6} y={y(tick) + 3}>{format(tick)}</text>}
          </g>
        ))}
        {bars.map((bar, index) => {
          const x = pad.left + (index + 0.5) * step;
          const rising = bar.close >= bar.open;
          const top = Math.min(y(bar.open), y(bar.close));
          const size = Math.max(1, Math.abs(y(bar.close) - y(bar.open)));
          return (
            <g key={`${bar.t}-${index}`} className={rising ? "candle candle--up" : "candle candle--down"}>
              <title>{`${formatTime(bar.t)} · O ${format(bar.open)} H ${format(bar.high)} L ${format(bar.low)} C ${format(bar.close)}${bar.volume !== null ? ` · V ${bar.volume.toLocaleString()}` : ""}`}</title>
              <line x1={x} x2={x} y1={y(bar.high)} y2={y(bar.low)} />
              <rect x={x - body / 2} y={top} width={body} height={size} />
              {hasVolume && bar.volume !== null && (
                <rect className="t-candles__vol" x={x - body / 2} width={body}
                  y={height - pad.bottom - (bar.volume / maxVolume) * volumeHeight} height={(bar.volume / maxVolume) * volumeHeight} />
              )}
            </g>
          );
        })}
        <rect className="t-chart__last" x={width - pad.right + 2} y={y(last.close) - 8} width={pad.right - 4} height={16} rx="1" />
        <text className="t-chart__last-text" x={width - pad.right + 6} y={y(last.close) + 3}>{format(last.close)}</text>
        <text className="t-chart__axis chart-axis-label" x={pad.left} y={height - 6}>{formatTime(bars[0].t)}</text>
        <text className="t-chart__axis chart-axis-label" x={width - pad.right} y={height - 6} textAnchor="end">{formatTime(last.t)}</text>
        {focus && hover !== null && (
          <line className="t-chart__cross" x1={pad.left + (hover + 0.5) * step} x2={pad.left + (hover + 0.5) * step} y1={pad.top} y2={height - pad.bottom} />
        )}
      </svg>
      {focus && (
        <div className="t-chart__tip t-chart__tip--ohlc" role="status">
          <span>{formatTime(focus.t)}</span>
          <span>O <b>{format(focus.open)}</b></span><span>H <b>{format(focus.high)}</b></span>
          <span>L <b>{format(focus.low)}</b></span><span>C <b>{format(focus.close)}</b></span>
          {focus.volume !== null && <span>V <b>{focus.volume.toLocaleString()}</b></span>}
        </div>
      )}
    </div>
  );
}
