import { direction } from "../market";

export interface SignedBar { key: string; label: string; value: number | null; display: string; title?: string }

/** Horizontal bars diverging from a zero line (PnL, contribution). Labels and values stay text. */
export function SignedBars({ bars, label }: { bars: SignedBar[]; label: string }) {
  const max = Math.max(...bars.map((bar) => Math.abs(bar.value ?? 0)), 1e-9);
  return (
    <ul className="t-sbars" aria-label={label}>
      {bars.map((bar) => {
        const share = bar.value === null ? 0 : (Math.abs(bar.value) / max) * 50;
        const tone = direction(bar.value);
        return (
          <li key={bar.key} title={bar.title ?? `${bar.label}: ${bar.display}`}>
            <span className="t-sbars__label">{bar.label}</span>
            <span className="t-sbars__track" aria-hidden="true">
              <i className={`t-sbars__bar t-sbars__bar--${tone}`}
                style={tone === "down" ? { right: "50%", width: `${share}%` } : { left: "50%", width: `${share}%` }} />
            </span>
            <span className={`t-sbars__value t-num--${tone}`}>{bar.display}</span>
          </li>
        );
      })}
    </ul>
  );
}
