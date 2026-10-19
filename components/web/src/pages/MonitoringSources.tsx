import type { PipelinesResponse, SourceHealth } from "../dataTypes";

export function sourceService(country: string, source: string) {
  if (source === "ibkr") return "ibkr-snapshot";
  if (country === "US") return source === "universe" ? "universe-us" : "ingest-us";
  if (country === "IN") return "ingest-india";
  return source;
}

const SEVERE = ["error", "failed", "auth_required", "incomplete"];

// The API judges sources against their own schedules; the fixed 10-minute rule is a fallback
// for responses from an API that predates those fields.
function judge(source: SourceHealth, generatedAt: string) {
  if (source.problem !== undefined) {
    return { problem: source.problem, stale: Boolean(source.stale), severe: Boolean(source.severe), service: source.service_id || sourceService(source.country_code, source.source) };
  }
  const age = source.checked_at ? (Date.parse(generatedAt) - Date.parse(source.checked_at)) / 1000 : Infinity;
  return { problem: source.status !== "ready" || age > 600, stale: age > 600, severe: SEVERE.includes(source.status), service: sourceService(source.country_code, source.source) };
}

export function sourceProblems(data: PipelinesResponse | null) {
  if (!data) return [];
  return data.sources.flatMap(source => {
    const { problem, ...state } = judge(source, data.generated_at);
    return problem ? [{ ...source, ...state }] : [];
  });
}

export function SourceAlerts({ data, stale }: { data: PipelinesResponse | null; stale: boolean }) {
  const problems = sourceProblems(data);
  if (!problems.length) return null;
  return <section className="monitoring-source-alerts" aria-label="Collection failures">
    {problems.map(source => <div className={`monitoring-notice ${source.severe ? "monitoring-notice--error" : ""}`} role="alert" key={`${source.country_code}-${source.source}`}>
      <div className="monitoring-source-alert-heading"><strong>{source.country_code} / {source.source}: {stale ? "stale snapshot" : source.stale ? "source reporting overdue" : source.status.replaceAll("_", " ")}</strong><a href="#monitoring-errors-title">Inspect errors ↓</a></div>
      <p>{source.detail || "No source status detail was recorded."}</p>
      <small>{source.checked_at ? `Last source report: ${new Date(source.checked_at).toLocaleString()}` : "No source report timestamp"} · Process heartbeats do not confirm successful collection.</small>
    </div>)}
  </section>;
}
