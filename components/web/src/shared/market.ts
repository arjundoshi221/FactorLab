/** Market-number formatting shared by the terminal shell, cockpit and charts. */

export type Direction = "up" | "down" | "flat";

export function direction(value: number | null | undefined): Direction {
  if (value === null || value === undefined || value === 0 || Number.isNaN(value)) return "flat";
  return value > 0 ? "up" : "down";
}

export const arrow = (value: number | null | undefined) => ({ up: "▲", down: "▼", flat: "■" })[direction(value)];

/** A fraction as a signed percentage: 0.0042 -> "+0.42%". */
export function signedPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${(value * 100).toFixed(digits)}%`;
}

export function signed(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export function price(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** Compact money in a currency's own grouping: 1234567 -> "1.23M". */
export function compactMoney(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(value);
}

/** Parse a decimal string from the API without losing the null / "—" distinction. */
export function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
