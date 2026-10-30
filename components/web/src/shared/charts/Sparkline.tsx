import { direction } from "../market";

/** A word-sized trend line: colour follows the net change, the end point is marked. */
export function Sparkline({ values, width = 96, height = 24, label, tone }: {
  values: number[]; width?: number; height?: number; label: string; tone?: "up" | "down" | "flat" | "accent";
}) {
  const points = values.filter((value) => Number.isFinite(value));
  if (points.length < 2) return <span className="t-spark t-spark--empty" aria-label={`${label}: not enough history`} />;
  const low = Math.min(...points);
  const high = Math.max(...points);
  const span = high - low || 1;
  const pad = 2;
  const x = (index: number) => pad + (index / (points.length - 1)) * (width - pad * 2);
  const y = (value: number) => pad + (1 - (value - low) / span) * (height - pad * 2);
  const path = points.map((value, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(value).toFixed(1)}`).join("");
  const color = tone ?? direction(points[points.length - 1] - points[0]);
  const area = `${path}L${x(points.length - 1).toFixed(1)},${height}L${x(0).toFixed(1)},${height}Z`;
  return (
    <svg className={`t-spark t-spark--${color}`} width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img"
      aria-label={`${label}: ${points.length} points, from ${points[0].toLocaleString()} to ${points[points.length - 1].toLocaleString()}`}>
      <path className="t-spark__area" d={area} />
      <path className="t-spark__line" d={path} />
      <circle className="t-spark__end" cx={x(points.length - 1)} cy={y(points[points.length - 1])} r="2" />
    </svg>
  );
}
