import { useEffect, useState } from "react";

export interface CollectionMembership { resolution: string; source: string; active_series: number; contracts: number }
export interface CollectionPipeline {
  id: string; label: string; schedule: string; scheduled: boolean; per_symbol_runs: boolean;
  declared_universe: string; resolution: string | null; expected_interval_seconds: number | null;
  status: string; status_reason: string; last_status: string | null; last_started_at: string | null; runs_24h: number;
  universe: string | null; universes_7d: string[]; requested_series: number | null;
  config: Record<string, unknown> | null; median_gap_seconds: number | null;
  members: CollectionMembership[];
  flags: ("universe_mismatch" | "several_universes" | "config_unreported")[];
}
export interface CollectionPlanResponse {
  generated_at: string;
  components: { component: string; services: { service: string; pipelines: CollectionPipeline[] }[] }[];
  unavailable: string[];
}
const flagNames = {
  universe_mismatch: "Universe differs from declared",
  several_universes: "Several universes this week",
  config_unreported: "Config not reported yet",
};
const tones: Record<string, string> = { healthy: "up", attention: "late", missing: "down", not_expected: "stopped" };
const observationNames: Record<string, string> = { runs: "latest runs", cadence: "run cadence", membership: "membership" };
function every(seconds: number | null) {
  if (seconds === null) return "—";
  if (seconds < 120) return `${Math.round(seconds)}s`;
  if (seconds < 7200) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}
function value(item: unknown) {
  return Array.isArray(item) ? item.join(", ") : String(item);
}

export function MonitoringCollection({ refreshKey }: { refreshKey: number }) {
  const [data, setData] = useState<CollectionPlanResponse | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let disposed = false;
    let controller: AbortController | null = null;
    const refresh = async () => {
      controller?.abort();
      const request = new AbortController(); controller = request;
      try {
        const response = await fetch("/hub/api/v1/monitoring/collection-plan", { signal: request.signal });
        if (!response.ok) throw new Error("Unavailable");
        const plan = await response.json() as CollectionPlanResponse;
        if (!Array.isArray(plan.components)) throw new Error("Invalid response");
        if (!disposed && !request.signal.aborted) { setData(plan); setFailed(false); }
      } catch {
        if (!disposed && !request.signal.aborted) setFailed(true);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 60000);
    return () => { disposed = true; controller?.abort(); window.clearInterval(timer); };
  }, [refreshKey]);
  return <section className="monitoring-collection" aria-labelledby="monitoring-collection-title">
    <div className="monitoring-section-heading"><div><h2 id="monitoring-collection-title">Collection plan</h2><p>What each ingestion service collects: the universe, resolution and cadence it declares, beside what its latest run reported.</p></div><span className="monitoring-event-count">{data ? `Checked ${new Date(data.generated_at).toLocaleTimeString()}` : failed ? "Unavailable" : "Loading..."}</span></div>
    {failed && <div className="monitoring-notice" role="status">Collection plan refresh failed. {data ? "Showing the last snapshot." : "Try refreshing."}</div>}
    {data && data.unavailable.length > 0 && <div className="monitoring-notice" role="status">Incomplete: {data.unavailable.map(name => observationNames[name] || name).join(", ")} could not be read; those columns are empty, not clear.</div>}
    {data?.components.map(component => <div className="monitoring-collection-component" key={component.component}>
      <h3><code>{component.component}</code></h3>
      <div className="monitoring-event-scroll"><table className="monitoring-events monitoring-collection-table">
        <thead><tr><th scope="col">Service / pipeline</th><th scope="col">Universe</th><th scope="col">Members</th><th scope="col">Resolution</th><th scope="col">Cadence</th><th scope="col">Latest run</th><th scope="col">Reported config</th></tr></thead>
        <tbody>{component.services.flatMap(service => service.pipelines.map(p => <tr key={p.id} id={`collection-${p.id}`}>
          <td><span className="monitoring-event-service">{p.label}</span><code>{service.service} · {p.id}</code></td>
          <td><code>{p.universe ?? (p.declared_universe || "—")}</code>{p.universe && p.declared_universe && p.universe !== p.declared_universe && <small> declared {p.declared_universe}</small>}{p.flags.map(flag => <span className="monitoring-event-error" key={flag}>{flagNames[flag]}</span>)}</td>
          <td>{p.members.length ? p.members.map(m => <span className="monitoring-collection-member" key={`${m.source}-${m.resolution}`}>{m.resolution} {m.active_series.toLocaleString()}{m.contracts ? ` (${m.contracts.toLocaleString()} contracts)` : ""}</span>) : "—"}</td>
          <td>{p.resolution ?? "—"}</td>
          <td>{p.schedule}<small>{p.per_symbol_runs ? `${p.runs_24h.toLocaleString()} symbol runs / 24h` : p.median_gap_seconds !== null ? `observed every ${every(p.median_gap_seconds)} · ${p.runs_24h} runs / 24h` : `${p.runs_24h} runs / 24h`}</small></td>
          <td><span className={`monitoring-chip monitoring-chip--${tones[p.status] ?? "unknown"}`}><i />{p.last_started_at ? new Date(p.last_started_at).toLocaleString() : "No run in 7 days"}</span><small>{p.status_reason}</small></td>
          <td>{p.config && Object.keys(p.config).length ? Object.entries(p.config).map(([key, item]) => <span className="monitoring-event-state" key={key}>{key}: {value(item)}</span>) : "—"}</td>
        </tr>))}</tbody>
      </table></div>
    </div>)}
  </section>;
}
