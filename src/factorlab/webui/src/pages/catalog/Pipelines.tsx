import { useState } from "react";

import { ErrorBanner, Skeleton, StatusPill, type HubStatus } from "../../components/ui";
import { tableHref, type PipelineCard, type PipelineRunsPage, type PipelinesResponse } from "../../dataTypes";
import { errorMessage, useRemote } from "../../shared/api";
import { formatCompact, formatDate, formatRelative, formatTime } from "../../shared/format";
import { RunList } from "./RunList";

const GROUPS: { status: HubStatus[]; label: string }[] = [
  { status: ["attention", "missing"], label: "need attention" },
  { status: ["healthy"], label: "healthy" },
  { status: ["not_expected", "not_configured", "unknown"], label: "idle or on demand" },
];

function successRate(success: number, total: number): string {
  return total ? `${Math.round((success * 100) / total)}%` : "—";
}

function WeekStrip({ pipeline, now }: { pipeline: PipelineCard; now: number }) {
  const days = Array.from({ length: 7 }, (_, index) => {
    const day = new Date(now - (6 - index) * 86_400_000).toISOString().slice(0, 10);
    return pipeline.days.find((item) => item.day === day) ?? { day, success: 0, problem: 0, total: 0 };
  });
  return (
    <ol className="data-week" aria-label="Runs per day over the last 7 days">
      {days.map((day) => {
        const tone = !day.total ? "none" : day.problem === 0 ? "good" : day.problem / day.total > 0.2 ? "bad" : "mixed";
        return (
          <li key={day.day} className={`data-week__day data-week__day--${tone}`}
            title={`${formatDate(day.day)}: ${day.total} runs, ${day.success} succeeded, ${day.problem} failed or incomplete`}>
            <span className="sr-only">{formatDate(day.day)}: {day.total} runs, {day.problem} with problems</span>
          </li>
        );
      })}
    </ol>
  );
}

function RecentRuns({ pipeline, now }: { pipeline: PipelineCard; now: number }) {
  const runs = useRemote<PipelineRunsPage>(`/hub/api/v1/catalog/pipelines/${pipeline.id}/runs?limit=15`);
  if (runs.error) return <ErrorBanner onRetry={runs.refresh}>{errorMessage(runs.error, "Runs could not be loaded.")}</ErrorBanner>;
  if (!runs.data) return <Skeleton rows={3} />;
  return <RunList runs={runs.data.items} now={now} />;
}

function Card({ pipeline, now }: { pipeline: PipelineCard; now: number }) {
  const [showRuns, setShowRuns] = useState(false);
  return (
    <article className={`data-pipeline data-pipeline--${pipeline.status}`} id={pipeline.id}>
      <header>
        <div>
          <span className="eyebrow">{pipeline.market} · {pipeline.source}</span>
          <h3>{pipeline.label}</h3>
          <code>{pipeline.id}</code>
        </div>
        <StatusPill status={pipeline.status} />
      </header>
      <p>{pipeline.description}</p>
      <p className="data-pipeline__reason">{pipeline.status_reason}</p>
      <dl className="data-pipeline__facts">
        <div><dt>Schedule</dt><dd>{pipeline.schedule}</dd></div>
        <div><dt>Last run</dt><dd title={formatTime(pipeline.last_started_at)}>{pipeline.last_started_at ? `${formatRelative(pipeline.last_started_at, now)} · ${pipeline.last_status}` : "None in 7 days"}</dd></div>
        <div><dt>Last 24 hours</dt><dd>{pipeline.runs_24h ? `${formatCompact(pipeline.runs_24h)} runs · ${successRate(pipeline.success_24h, pipeline.runs_24h)} succeeded` : "No runs"}</dd></div>
        <div><dt>Rows written today</dt><dd>{formatCompact(pipeline.rows_24h)}</dd></div>
      </dl>
      <div className="data-pipeline__week">
        <span>Last 7 days · {successRate(pipeline.success_7d, pipeline.runs_7d)} of {formatCompact(pipeline.runs_7d)} runs succeeded</span>
        <WeekStrip pipeline={pipeline} now={now} />
      </div>
      {pipeline.recent_problems.length > 0 && (
        <details className="data-pipeline__problems">
          <summary>{pipeline.recent_problems.length} recent problem{pipeline.recent_problems.length > 1 ? "s" : ""}</summary>
          <RunList runs={pipeline.recent_problems} now={now} />
        </details>
      )}
      <div className="data-pipeline__tables">
        <span>Writes to</span>
        {pipeline.tables_written.filter((table) => table !== "meta.ingestion_runs").map((table) => (
          <a key={table} href={tableHref(table)}><code>{table}</code></a>
        ))}
      </div>
      {pipeline.per_symbol_runs && <p className="data-note">Runs are recorded once per symbol, so counts are high by design.</p>}
      <button type="button" className="button-secondary" aria-expanded={showRuns} onClick={() => setShowRuns((value) => !value)}>
        {showRuns ? "Hide recent runs" : "Show recent runs"}
      </button>
      {showRuns && <RecentRuns pipeline={pipeline} now={now} />}
    </article>
  );
}

export function Pipelines() {
  const remote = useRemote<PipelinesResponse>("/hub/api/v1/catalog/pipelines", 60_000);
  const data = remote.data;
  if (remote.loading && !data) return <main className="data-page"><Skeleton rows={6} /></main>;
  if (!data) {
    return (
      <main className="data-page">
        <section className="data-hero"><span className="eyebrow">Pipelines</span><h1>Pipeline status could not load.</h1></section>
        <ErrorBanner onRetry={remote.refresh}>{errorMessage(remote.error)}</ErrorBanner>
      </main>
    );
  }
  const now = new Date(data.generated_at).valueOf();
  const order: HubStatus[] = ["attention", "missing", "healthy", "not_expected", "unknown", "not_configured"];
  const pipelines = [...data.pipelines].sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
  return (
    <main className="data-page">
      <section className="data-hero data-hero--compact">
        <span className="eyebrow">Pipelines</span>
        <h1>How collection is running.</h1>
        <p>Every job that brings data into FactorLab, judged against its own schedule: market-hours collectors are only expected to run while their market is open.</p>
        <div className="data-hero__facts">
          {GROUPS.map((group) => {
            const count = data.pipelines.filter((item) => group.status.includes(item.status)).length;
            return <span key={group.label} className={group.status.includes("attention") && count ? "is-warning" : ""}><strong>{count}</strong> {group.label}</span>;
          })}
          <span>Updated {formatRelative(data.generated_at)}</span>
        </div>
      </section>
      {remote.error ? <ErrorBanner onRetry={remote.refresh}>{errorMessage(remote.error)} Showing the last loaded status.</ErrorBanner> : null}
      <section className="data-pipeline-grid" aria-label="Pipelines">
        {pipelines.map((pipeline) => <Card key={pipeline.id} pipeline={pipeline} now={now} />)}
      </section>
      <section className="inventory" aria-labelledby="sources-title">
        <div className="section-heading">
          <div><span className="eyebrow">Sources</span><h2 id="sources-title">What each data source last reported</h2></div>
          <span>{data.sources.length} sources</span>
        </div>
        {data.sources.length ? (
          <div className="table-wrap">
            <table className="data-sources-table">
              <thead><tr><th>Source</th><th>Country</th><th>Status</th><th>Detail</th><th>Checked</th></tr></thead>
              <tbody>
                {data.sources.map((source) => (
                  <tr key={`${source.country_code}-${source.source}`}>
                    <td data-label="Source"><code>{source.source}</code></td>
                    <td data-label="Country">{source.country_code}</td>
                    <td data-label="Status">
                      <StatusPill status={source.status === "ready" ? "healthy" : ["error", "auth_required", "stopped"].includes(source.status) ? "attention" : "not_expected"} label={source.status.replaceAll("_", " ")} />
                    </td>
                    <td data-label="Detail">{source.detail || "—"}</td>
                    <td data-label="Checked" title={formatTime(source.checked_at)}>{formatRelative(source.checked_at, now)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="data-muted">No source has reported a status yet.</p>}
      </section>
      <footer>FactorLab pipelines · Refreshes every minute · Times in IST</footer>
    </main>
  );
}
