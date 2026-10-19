import { useEffect, useState } from "react";
import type { PipelinesResponse } from "../dataTypes";
import { SourceAlerts } from "./MonitoringSources";

export interface DailyReport { service_id: string; day: string; reports: number; errors: number; warnings: number; restarts: number; outages?: number }
interface Daily { generated_at: string; days: DailyReport[] }
interface CoverageFeed { country_code: string; source: string; resolution: string; trade_date: string | null; checked_series: number; expected: number; actual: number; missing: number; affected_series: number; checked_at: string | null; active_series?: number; unreported_series?: number }
interface Coverage { generated_at: string; feeds: CoverageFeed[] }
interface Snapshot<T> { data: T | null; error: boolean }
const empty = { data: null, error: false };

export function useMonitoringOperations(refreshKey: number) {
  const [daily, setDaily] = useState<Snapshot<Daily>>(empty);
  const [coverage, setCoverage] = useState<Snapshot<Coverage>>(empty);
  const [pipelines, setPipelines] = useState<Snapshot<PipelinesResponse>>(empty);
  useEffect(() => {
    let disposed = false;
    let controller: AbortController | null = null;
    const refresh = async () => {
      controller?.abort();
      const request = new AbortController();
      controller = request;
      const read = async <T,>(url: string, set: (update: (previous: Snapshot<T>) => Snapshot<T>) => void) => {
        try {
          const response = await fetch(url, { signal: request.signal });
          if (!response.ok) throw new Error("Unavailable");
          const data = await response.json() as T;
          if (!disposed && !request.signal.aborted) set(() => ({ data, error: false }));
        } catch {
          if (!disposed && !request.signal.aborted) set(previous => ({ ...previous, error: true }));
        }
      };
      await Promise.allSettled([
        read("/hub/api/v1/monitoring/daily", setDaily),
        read("/hub/api/v1/monitoring/coverage", setCoverage),
        read("/hub/api/v1/catalog/pipelines", setPipelines),
      ]);
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => { disposed = true; controller?.abort(); window.clearInterval(timer); };
  }, [refreshKey]);
  return { daily, coverage, pipelines };
}

export function DailyStrip({ reports, generatedAt, stale = false }: { reports: DailyReport[]; generatedAt: string; stale?: boolean }) {
  const end = new Date(generatedAt);
  const days = Array.from({ length: 7 }, (_, index) => {
    const date = new Date(end);
    date.setUTCDate(date.getUTCDate() - 6 + index);
    const day = date.toISOString().slice(0, 10);
    return { day, report: reports.find(item => item.day === day) };
  });
  return <div className={`monitoring-daily ${stale ? "is-stale" : ""}`}>
    <div className="monitoring-daily-label"><span>7-day telemetry · UTC</span><span>{stale ? "Stale snapshot" : "Today →"}</span></div>
    <div className="monitoring-day-strip">{days.map(({ day, report }) => {
      const tone = !report ? "unknown" : report.errors || report.outages ? "down" : report.warnings || report.restarts ? "late" : "up";
      const detail = report ? `${report.reports} reports, ${report.errors} errors, ${report.warnings} warnings, ${report.restarts} restarts, ${report.outages ?? 0} down transitions` : "No reports stored";
      return <span key={day} className={`monitoring-day monitoring-day--${tone}`} tabIndex={0} aria-label={`${day}: ${detail}`} title={`${day}: ${detail}`}><span>{day.slice(8)}</span></span>;
    })}</div>
  </div>;
}

function timestamp(value: string) { return new Date(value).toLocaleString(); }
const number = (value: number) => value.toLocaleString();

export function MonitoringOperations({ coverage, pipelines, showSourceAlerts = true }: Pick<ReturnType<typeof useMonitoringOperations>, "coverage" | "pipelines"> & { showSourceAlerts?: boolean }) {
  const [onlyProblems, setOnlyProblems] = useState(false);
  const cards = pipelines.data?.pipelines ?? [];
  const problem = (card: typeof cards[number]) => card.problem_24h > 0 || ["attention", "missing"].includes(card.status);
  const visible = [...cards].filter(card => !onlyProblems || problem(card)).sort((a, b) => Number(problem(b)) - Number(problem(a)));
  return <>
    {showSourceAlerts && <SourceAlerts data={pipelines.data} stale={pipelines.error} />}
    <section className="monitoring-data" aria-labelledby="monitoring-data-title">
      <div className="monitoring-section-heading"><div><h2 id="monitoring-data-title">Data completeness</h2><p>Latest recorded session for each feed · checks from the last 30 days.</p></div><a className="monitoring-link" href="/us">Explore coverage ↗</a></div>
      {coverage.error && <div className="monitoring-notice" role="status">Coverage refresh failed. {coverage.data ? `Showing snapshot from ${timestamp(coverage.data.generated_at)}.` : "Coverage is unknown."}</div>}
      <div className="monitoring-services">{coverage.data?.feeds.map(feed => {
        const percent = feed.expected > 0 ? Math.max(0, Math.min(100, (feed.expected - feed.missing) / feed.expected * 100)) : null;
        const gaps = feed.missing > 0 || Boolean(feed.unreported_series);
        return <article className={`monitoring-coverage-card ${gaps ? "has-gaps" : ""}`} id={`coverage-${feed.country_code}-${feed.source}-${feed.resolution}`} key={`${feed.country_code}-${feed.source}-${feed.resolution}`}>
          <div className="monitoring-service-top"><span className={`monitoring-chip monitoring-chip--${coverage.error ? "unknown" : gaps ? "down" : percent === null ? "unknown" : "up"}`}><i />{coverage.error ? "Stale coverage" : gaps ? "Coverage gaps" : percent === null ? "No expectation" : "Recorded checks complete"}</span><span className="monitoring-service-kind">{feed.country_code}</span></div>
          <h3>{feed.source} <small>{feed.resolution}</small></h3><p className="monitoring-session">{feed.trade_date ? `Session ${feed.trade_date}` : "No recorded session in the last 30 days"}</p>
          <div className="monitoring-coverage-value"><strong>{percent === null ? "—" : `${percent.toFixed(1)}%`}</strong><span>{number(feed.missing)} missing bars</span></div>
          <progress max={100} value={percent ?? 0} aria-label={`${feed.source} ${feed.resolution} recorded coverage`} />
          <dl><div><dt>Checked series</dt><dd>{number(feed.checked_series)}</dd></div><div><dt>With gaps</dt><dd>{number(feed.affected_series)}</dd></div><div><dt>Expected bars</dt><dd>{number(feed.expected)}</dd></div></dl>
          {feed.active_series !== undefined && <p className={feed.unreported_series ? "monitoring-danger monitoring-session" : "monitoring-session"}>{number(feed.unreported_series ?? 0)} of {number(feed.active_series)} active series have no check for this session.</p>}
          <p className="monitoring-service-note">{feed.checked_at ? `Checked ${timestamp(feed.checked_at)}. ` : "No checks received. "}The percentage covers recorded bars only. Missing entire sessions and exchange calendars are not evaluated here.</p>
          <div className="monitoring-card-links"><a className="monitoring-link" href={`/market-data?view=matrix&attention=bars&country=${feed.country_code}`}>Tickers missing bars →</a><a className="monitoring-link" href={`/data/tables/meta.session_coverage?market=${feed.country_code}`}>Inspect coverage records ↗</a></div>
        </article>;
      })}</div>
      {!coverage.data && !coverage.error && <div className="monitoring-empty" role="status">Loading coverage...</div>}
      {coverage.data && !coverage.data.feeds.length && <div className="monitoring-empty">No coverage checks recorded in the last 30 days. Data completeness is unknown.</div>}
    </section>
    <section className="monitoring-data" aria-labelledby="monitoring-pipelines-title">
      <div className="monitoring-section-heading"><div><h2 id="monitoring-pipelines-title">Daily ingestion</h2><p>Rolling 24-hour runs and rows · schedule-aware pipeline health · cached for up to 60s.</p></div><label className="monitoring-toggle"><input type="checkbox" checked={onlyProblems} onChange={event => setOnlyProblems(event.target.checked)} /> Problems only</label></div>
      {pipelines.error && <div className="monitoring-notice" role="status">Pipeline refresh failed. {pipelines.data ? `Showing snapshot from ${timestamp(pipelines.data.generated_at)}.` : "Ingestion health is unknown."}</div>}
      <div className="monitoring-services">{visible.map(card => <article className={`monitoring-pipeline ${problem(card) ? "has-gaps" : ""}`} id={`pipeline-${card.id}`} key={card.id}>
        <div className="monitoring-service-top"><span className={`monitoring-chip monitoring-chip--${pipelines.error ? "unknown" : problem(card) ? "late" : card.runs_24h ? "up" : "unknown"}`}><i />{pipelines.error ? "Stale snapshot" : card.status.replaceAll("_", " ")}</span><span className="monitoring-service-kind">{card.market} · {card.source}</span></div>
        <h3>{card.label}</h3><p className="monitoring-service-note">{card.status_reason}</p>
        <dl><div><dt>Runs / 24h</dt><dd>{number(card.runs_24h)}</dd></div><div><dt>Problems / 24h</dt><dd className={card.problem_24h ? "monitoring-danger" : ""}>{number(card.problem_24h)}</dd></div><div><dt>Rows / 24h</dt><dd>{number(card.rows_24h)}</dd></div></dl>
        <div className="monitoring-run-history" aria-label="Daily ingestion activity">{Array.from({length: 7}, (_, index) => {
          const date = new Date(pipelines.data!.generated_at); date.setUTCDate(date.getUTCDate() - 6 + index);
          const day = date.toISOString().slice(0, 10); const bucket = card.days.find(item => item.day === day);
          return <span className={`monitoring-day monitoring-day--${!bucket?.total ? "unknown" : bucket.problem ? "down" : "up"}`} key={day} tabIndex={0} aria-label={`${day}: ${bucket?.success ?? 0} successful, ${bucket?.problem ?? 0} problem runs`} title={`${day}: ${bucket?.success ?? 0} successful, ${bucket?.problem ?? 0} problem runs`}>{day.slice(8)}</span>;
        })}</div>
        <p className="monitoring-session">7 calendar days · UTC · blank means no recorded runs</p>
        <p className="monitoring-schedule">{card.schedule}</p>
        {card.recent_problems.length > 0 && <details><summary>{card.recent_problems.length} recent problem runs</summary>{card.recent_problems.map(run => <div className="monitoring-run-problem" key={run.run_id}><strong>{run.status} · {timestamp(run.started_at)}</strong><p>{run.error || "Run did not complete successfully"}</p><span>{run.failed_series} failed series / {run.requested_series} requested</span></div>)}</details>}
        <a className="monitoring-link" href="/data/pipelines">Inspect pipeline runs ↗</a>
      </article>)}</div>
      {!pipelines.data && !pipelines.error && <div className="monitoring-empty" role="status">Loading ingestion health...</div>}
      {pipelines.data && !visible.length && <div className="monitoring-empty">{onlyProblems ? "No pipeline problems in this snapshot." : "No pipelines registered."}</div>}
    </section>
  </>;
}
