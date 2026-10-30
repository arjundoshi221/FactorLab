/** A coverage-style arc meter for a single 0–100 reading; the number stays the primary text. */
export function Gauge({ value, label, target, size = 92 }: { value: number | null; label: string; target?: number; size?: number }) {
  const radius = 38;
  const length = Math.PI * radius;
  const clamped = value === null ? 0 : Math.max(0, Math.min(100, value));
  const tone = value === null ? "unknown" : target !== undefined && value < target ? "warn" : "good";
  const angle = target === undefined ? null : Math.PI * (1 - target / 100);
  return (
    <figure className={`t-gauge t-gauge--${tone}`} style={{ width: size }}>
      <svg viewBox="0 0 100 58" role="img" aria-label={`${label}: ${value === null ? "no reading" : `${value.toFixed(1)}%`}${target !== undefined ? `, target ${target}%` : ""}`}>
        <path className="t-gauge__track" d="M12,50 A38,38 0 0 1 88,50" pathLength={length} />
        <path className="t-gauge__value" d="M12,50 A38,38 0 0 1 88,50" strokeDasharray={`${(clamped / 100) * length} ${length}`} pathLength={length} />
        {angle !== null && <line className="t-gauge__target" x1={50 + Math.cos(angle) * 31} y1={50 - Math.sin(angle) * 31} x2={50 + Math.cos(angle) * 45} y2={50 - Math.sin(angle) * 45} />}
        <text x="50" y="47" textAnchor="middle">{value === null ? "—" : `${value.toFixed(1)}%`}</text>
      </svg>
      <figcaption>{label}</figcaption>
    </figure>
  );
}
