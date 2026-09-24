import { useEffect, useState } from "react";

import type { USDashboard, USInstrument, USPage, USRun } from "../../usTypes";
import { USStatusPill } from "./USStatusPill";
import { USSummaryCards } from "./USSummaryCards";

async function read<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`US dashboard request failed (${response.status})`);
  return response.json() as Promise<T>;
}

function seriesStatus(instrument: USInstrument, resolution: "daily" | "1min") {
  return instrument.series.find((series) => series.resolution === resolution)?.status ?? "not_configured";
}

function formatTime(value: string | null) {
  if (!value) return "In progress";
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    timeZone: "Asia/Kolkata", timeZoneName: "short",
  }).format(new Date(value));
}

export function USDashboardPanel({ refreshKey }: { refreshKey: string }) {
  const [dashboard, setDashboard] = useState<USDashboard | null>(null);
  const [instruments, setInstruments] = useState<USInstrument[]>([]);
  const [runs, setRuns] = useState<USRun[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      read<USDashboard>("/hub/api/v1/us/dashboard", controller.signal),
      read<USPage<USInstrument>>("/hub/api/v1/us/instruments?scope=all&limit=8", controller.signal),
      read<USPage<USRun>>("/hub/api/v1/us/ingestion/runs?limit=5", controller.signal),
    ]).then(([summary, universe, activity]) => {
      setDashboard(summary);
      setInstruments(universe.items);
      setRuns(activity.items);
      setError("");
    }).catch((reason: Error) => {
      if (!controller.signal.aborted) setError(reason.message);
    });
    return () => controller.abort();
  }, [refreshKey]);

  return <section className="us-dashboard-panel" aria-labelledby="us-dashboard-title">
    <div className="section-heading us-dashboard-heading">
      <div><span className="eyebrow">US market data</span><h2 id="us-dashboard-title">Universe and collection</h2></div>
      <a className="button-link" href="/us">Explore all US data</a>
    </div>
    {error && !dashboard && <div className="notice notice--error" role="alert">{error}</div>}
    {!dashboard && !error && <div className="inline-loading" role="status">Loading US collection data…</div>}
    {dashboard && <>
      {dashboard.source.status !== "ready" && <div className="notice" role="status">
        <USStatusPill status={dashboard.source.status} /> <span>{dashboard.source.detail}</span>
      </div>}
      <USSummaryCards dashboard={dashboard} />
      <div className="us-dashboard-columns">
        <article className="us-dashboard-block">
          <div className="us-dashboard-block__heading"><div><span className="eyebrow">Universe sample</span>
            <h3>Listed instruments</h3></div><span>{dashboard.universe.active.toLocaleString()} total</span></div>
          {instruments.length ? <div className="table-wrap"><table className="us-dashboard-table">
            <thead><tr><th>Instrument</th><th>Venue</th><th>Daily</th><th>Minute</th></tr></thead>
            <tbody>{instruments.map((instrument) => <tr key={instrument.instrument_id}>
              <td data-label="Instrument"><a className="us-symbol" href={`/us?symbol=${encodeURIComponent(instrument.symbol)}`}>{instrument.symbol}</a>
                <small>{instrument.name}</small></td><td data-label="Venue">{instrument.exchange_code}</td>
              <td data-label="Daily"><USStatusPill status={seriesStatus(instrument, "daily")} /></td>
              <td data-label="Minute"><USStatusPill status={seriesStatus(instrument, "1min")} /></td>
            </tr>)}</tbody>
          </table></div> : <p className="us-dashboard-empty">The resolver has not published the configured US universe yet.</p>}
        </article>
        <article className="us-dashboard-block">
          <div className="us-dashboard-block__heading"><div><span className="eyebrow">Pipeline activity</span>
            <h3>Recent US runs</h3></div><span>{dashboard.trading_date}</span></div>
          {runs.length ? <ol className="us-run-list">{runs.map((run) => <li key={run.run_id}>
            <div><strong>{run.pipeline.replaceAll("_", " ")}</strong><small>{run.source.toUpperCase()} · {formatTime(run.completed_at ?? run.started_at)}</small></div>
            <div><USStatusPill status={run.status} /><small>{run.rows_written.toLocaleString()} rows</small></div>
          </li>)}</ol> : <p className="us-dashboard-empty">No US ingestion runs have been recorded.</p>}
        </article>
      </div>
    </>}
  </section>;
}
