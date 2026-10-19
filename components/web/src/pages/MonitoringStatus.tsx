import { useEffect, useRef } from "react";
import { useRemote } from "../shared/api";

export type CheckSeverity = "critical" | "warning" | "info" | "ok" | "unknown";
export interface StatusCheck {
  id: string; area: string; severity: CheckSeverity; title: string; detail: string;
  count: number | null; observed_at: string | null; href: string; link_label: string;
}
export interface StatusSummary {
  generated_at: string; verdict: "ok" | "attention" | "critical" | "unknown";
  counts: Record<CheckSeverity, number>; checks: StatusCheck[];
}

const areas: Record<string, string> = {
  services: "Service", sources: "Source", pipelines: "Ingestion", coverage: "Completeness",
  broker: "Broker", tickers: "Tickers", tagging: "Trade tags",
};
const severityLabel: Record<CheckSeverity, string> = { critical: "Critical", warning: "Warning", unknown: "Not checked", info: "Note", ok: "OK" };
const tone: Record<CheckSeverity, string> = { critical: "down", warning: "late", unknown: "unknown", info: "unknown", ok: "up" };
const plural = (count: number, word: string) => `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
const when = (value: string) => new Date(value).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

function isSummary(value: unknown): value is StatusSummary {
  return Boolean(value && typeof value === "object" && Array.isArray((value as StatusSummary).checks));
}

function CheckRow({ check }: { check: StatusCheck }) {
  return <li className={`monitoring-status-row monitoring-status-row--${check.severity}`}>
    <span className={`monitoring-chip monitoring-chip--${tone[check.severity]}`}><i />{severityLabel[check.severity]}</span>
    <div className="monitoring-status-text">
      <strong>{check.title}</strong>
      <span>{areas[check.area] ?? check.area}{check.observed_at ? ` · last seen ${when(check.observed_at)}` : ""}{check.detail ? ` · ${check.detail}` : ""}</span>
    </div>
    <a className="monitoring-link" href={check.href}>{check.link_label} →</a>
  </li>;
}

/** The answer to "is everything working": one verdict, then each problem with a link to its evidence. */
export function MonitoringStatus({ refreshKey = 0 }: { refreshKey?: number }) {
  const remote = useRemote<StatusSummary>("/hub/api/v1/monitoring/summary", 30000);
  const { refresh } = remote;
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    refresh();
  }, [refreshKey, refresh]);
  const data = isSummary(remote.data) ? remote.data : null;
  const problems = data?.checks.filter(c => ["critical", "warning", "unknown"].includes(c.severity)) ?? [];
  const notes = data?.checks.filter(c => c.severity === "info") ?? [];
  const passing = data?.checks.filter(c => c.severity === "ok") ?? [];
  const attention = problems.filter(c => c.severity !== "unknown").length;
  const verdict = !data ? (remote.loading ? "loading" : "unavailable") : data.verdict;
  const headline = {
    loading: "Checking every system…",
    unavailable: "System status unavailable",
    ok: "All systems working",
    attention: `${plural(attention, "thing")} need${attention === 1 ? "s" : ""} attention`,
    critical: `${plural(attention, "thing")} need${attention === 1 ? "s" : ""} attention`,
    unknown: "Some checks could not run",
  }[verdict];
  const verdictTone = { loading: "unknown", unavailable: "unknown", ok: "up", attention: "late", critical: "down", unknown: "unknown" }[verdict];
  const passingCount = passing.reduce((sum, c) => sum + (c.count ?? 1), 0);
  return <section className={`monitoring-status monitoring-status--${verdictTone}`} id="monitoring-status" aria-labelledby="monitoring-status-title">
    <div className="monitoring-status-verdict" role="status">
      <span className="monitoring-status-mark" aria-hidden="true" />
      <div>
        <h2 id="monitoring-status-title">{headline}</h2>
        <p>{data
          ? [data.counts.critical ? plural(data.counts.critical, "critical issue") : "", data.counts.warning ? plural(data.counts.warning, "warning") : "",
            data.counts.unknown ? `${plural(data.counts.unknown, "area")} not checked` : "", `${passingCount.toLocaleString()} checks passing`].filter(Boolean).join(" · ")
          : remote.error ? "The status summary could not be loaded. The sections below still show their own data." : "Services, sources, ingestion, data completeness and broker snapshots."}</p>
      </div>
      <span className="monitoring-status-time">{data ? `Checked ${when(data.generated_at)}` : ""}</span>
    </div>
    {Boolean(remote.error) && data && <p className="monitoring-status-stale">Refresh failed · showing the summary from {when(data.generated_at)}.</p>}
    {problems.length > 0 && <ul className="monitoring-status-list" aria-label="Needs attention">{problems.map(c => <CheckRow key={c.id} check={c} />)}</ul>}
    {notes.length > 0 && <details className="monitoring-status-more"><summary>{plural(notes.length, "note")} · not failures</summary>
      <ul className="monitoring-status-list">{notes.map(c => <CheckRow key={c.id} check={c} />)}</ul></details>}
    {passing.length > 0 && <details className="monitoring-status-more"><summary>{passingCount.toLocaleString()} checks passing</summary>
      <ul className="monitoring-status-list">{passing.map(c => <CheckRow key={c.id} check={c} />)}</ul></details>}
  </section>;
}
