const TIME_ZONE = "Asia/Kolkata";

const numberFormatter = new Intl.NumberFormat("en-IN");
const compactFormatter = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });

export function formatNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : numberFormatter.format(value);
}

export function formatCompact(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : compactFormatter.format(value);
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** exponent).toFixed(exponent === 0 ? 0 : 1)} ${units[exponent]}`;
}

function parse(value: string | null | undefined): Date | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? null : parsed;
}

/** A calendar date in IST, e.g. "25 Sept 2026". Date-only values are shown as given. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [year, month, day] = value.split("-").map(Number);
    return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" })
      .format(new Date(Date.UTC(year, month - 1, day)));
  }
  const parsed = parse(value);
  if (!parsed) return value.slice(0, 10);
  return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: TIME_ZONE })
    .format(parsed);
}

/** Date and time in IST with the zone name, e.g. "25 Sept, 10:30 am IST". */
export function formatTime(value: string | null | undefined, fallback = "Never"): string {
  const parsed = parse(value);
  if (!parsed) return value ? value : fallback;
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: TIME_ZONE, timeZoneName: "short",
  }).format(parsed);
}

export function formatDuration(seconds: number): string {
  const value = Math.max(Math.round(seconds), 0);
  if (value < 90) return `${value}s`;
  if (value < 5_400) return `${Math.round(value / 60)} min`;
  if (value < 172_800) return `${Math.round(value / 3_600)} h`;
  return `${Math.round(value / 86_400)} days`;
}

/** "3 min ago" relative to ``now`` (defaults to the current time). */
export function formatRelative(value: string | null | undefined, now: number = Date.now()): string {
  const parsed = parse(value);
  if (!parsed) return "never";
  const seconds = (now - parsed.valueOf()) / 1000;
  if (seconds < 0) return `in ${formatDuration(-seconds)}`;
  if (seconds < 45) return "just now";
  return `${formatDuration(seconds)} ago`;
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(digits)}%`;
}

export function formatRange(first: string | null | undefined, last: string | null | undefined): string {
  if (!first && !last) return "No data yet";
  if (first && last && formatDate(first) === formatDate(last)) return formatDate(first);
  return `${formatDate(first)} → ${formatDate(last)}`;
}
