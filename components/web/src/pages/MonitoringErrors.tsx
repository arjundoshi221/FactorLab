import { useEffect, useState } from "react";

export interface MonitoringIssue {
  origin: "telemetry" | "pipeline" | "source";
  service_id: string; severity: string; error_type: string; message: string;
  first_seen: string | null; last_seen: string | null; occurrences: number | null;
  pipeline: string | null; source?: string; run_id?: string; instance_id?: string;
  host?: string; failed_series?: number; last_success: string | null;
  recovery: "unconfirmed" | "later_success" | "source_failure" | "source_stale";
}
export interface MonitoringErrorResponse {
  generated_at: string; since: string; issues: MonitoringIssue[];
  has_more: boolean; unavailable: string[];
}
const time = (value: string | null) => value ? new Date(value).toLocaleString() : "Not recorded";
const originNames = {telemetry: "Service telemetry", pipeline: "Ingestion run", source: "Current source status"};
const recoveryNames = {unconfirmed: "Recovery unconfirmed", later_success: "Later successful run", source_failure: "Current source problem", source_stale: "Source status stale"};

export function MonitoringErrors({ refreshKey, services }: { refreshKey: number; services: string[] }) {
  const [hours, setHours] = useState("168");
  const [service, setService] = useState("");
  const [severity, setSeverity] = useState("all");
  const [search, setSearch] = useState("");
  const [snapshot, setSnapshot] = useState<{key: string; data: MonitoringErrorResponse} | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const key = JSON.stringify([hours, service, severity]);
  useEffect(() => {
    let disposed = false;
    let controller: AbortController | null = null;
    const refresh = async () => {
      controller?.abort();
      const request = new AbortController(); controller = request;
      setLoading(true);
      const params = new URLSearchParams({hours, severity, limit: "100"});
      if (service) params.set("service_id", service);
      try {
        const response = await fetch(`/hub/api/v1/monitoring/errors?${params}`, {signal: request.signal});
        if (!response.ok) throw new Error("Unavailable");
        const data = await response.json() as MonitoringErrorResponse;
        if (!Array.isArray(data.issues) || !Array.isArray(data.unavailable)) throw new Error("Invalid response");
        if (!disposed && !request.signal.aborted) { setSnapshot({key, data}); setFailed(false); }
      } catch {
        if (!disposed && !request.signal.aborted) setFailed(true);
      } finally {
        if (!disposed && !request.signal.aborted) setLoading(false);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => { disposed = true; controller?.abort(); window.clearInterval(timer); };
  }, [hours, service, severity, key, refreshKey]);
  const data = snapshot?.key === key ? snapshot.data : null;
  const issues = data?.issues ?? [];
  const visible = issues.filter(issue => `${issue.message} ${issue.error_type} ${issue.pipeline ?? ""} ${issue.service_id}`.toLowerCase().includes(search.toLowerCase()));
  const serviceOptions = [...new Set([...services, ...issues.map(issue => issue.service_id), ...(service ? [service] : [])])].sort();
  return <section className="monitoring-errors" aria-labelledby="monitoring-errors-title">
    <div className="monitoring-section-heading"><div><h2 id="monitoring-errors-title" tabIndex={-1}>Error inbox</h2><p>Collection failures, service errors and warnings. Repeated messages are grouped within the selected window.</p></div><span className="monitoring-event-count">{loading ? "Updating..." : data ? `${issues.length} groups${data.has_more ? "+" : ""}` : "Unavailable"}</span></div>
    <div className="monitoring-filters monitoring-error-filters">
      <label>Error time range<select value={hours} onChange={e => setHours(e.target.value)}><option value="24">Last 24 hours</option><option value="168">Last 7 days</option><option value="720">Last 30 days</option></select></label>
      <label>Error service<select value={service} onChange={e => setService(e.target.value)}><option value="">All services</option>{serviceOptions.map(id => <option key={id} value={id}>{id}</option>)}</select></label>
      <label>Error severity<select value={severity} onChange={e => setSeverity(e.target.value)}><option value="all">Errors and warnings</option><option value="error">Errors (including fatal)</option><option value="fatal">Fatal only</option><option value="warning">Warnings only</option></select></label>
      <label>Search loaded errors<input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Message, type or pipeline" /></label>
    </div>
    {failed && <div className="monitoring-notice monitoring-notice--error" role="status">Error history refresh failed. {data ? `Showing stale snapshot from ${time(data.generated_at)}.` : "Error history is unavailable."}</div>}
    {data && data.unavailable.length > 0 && <div className="monitoring-notice" role="status">Incomplete error history: {data.unavailable.join(", ").replaceAll("_", " ")} unavailable. Missing results do not mean these services are healthy.</div>}
    {data && <div className="monitoring-error-note"><span>Snapshot {time(data.generated_at)} · times in your local timezone</span><span>Current source problems appear even if they started before this window. First seen is the first stored occurrence within the window. Heartbeats do not resolve errors.</span></div>}
    <div className="monitoring-issue-list" aria-busy={loading}>
      {visible.map((issue, index) => <details className={`monitoring-issue monitoring-issue--${issue.severity}`} key={`${key}-${issue.origin}-${issue.service_id}-${issue.error_type}-${index}`}>
        <summary><span className={`monitoring-event-type monitoring-event-type--${issue.severity === "warning" ? "warn" : "err"}`}>{issue.severity}</span><span className="monitoring-issue-heading"><strong>{issue.message}</strong><span>{issue.service_id}{issue.pipeline ? ` / ${issue.pipeline}` : ""} · {originNames[issue.origin]}</span></span><span className="monitoring-issue-last"><strong>{issue.occurrences === null ? "Current status" : `${issue.occurrences.toLocaleString()} occurrences`}</strong><span>{time(issue.last_seen)}</span></span></summary>
        <div className="monitoring-issue-details">
          <span className={`monitoring-chip monitoring-chip--${issue.recovery === "later_success" ? "up" : "late"}`}>{recoveryNames[issue.recovery]}</span>
          <dl><div><dt>First seen in window</dt><dd>{time(issue.first_seen)}</dd></div><div><dt>{issue.origin === "source" ? "Last status report" : "Last occurrence"}</dt><dd>{time(issue.last_seen)}</dd></div><div><dt>Error type</dt><dd>{issue.error_type}</dd></div><div><dt>Service</dt><dd>{issue.service_id}</dd></div>
            {issue.pipeline && <div><dt>Pipeline / source</dt><dd>{issue.pipeline} / {issue.source}</dd></div>}
            {issue.run_id && <div><dt>Latest affected run</dt><dd>{issue.run_id}</dd></div>}
            {issue.failed_series !== undefined && <div><dt>Failed series across these runs</dt><dd>{issue.failed_series.toLocaleString()}</dd></div>}
            {issue.host && <div><dt>Latest host / instance</dt><dd>{issue.host} / {issue.instance_id}</dd></div>}
            {issue.last_success && <div><dt>Latest successful pipeline run in window</dt><dd>{time(issue.last_success)}</dd></div>}
          </dl>
          <pre>{issue.message}</pre>
          <p>{issue.recovery === "later_success" ? "A later run succeeded. This does not prove that every failed symbol or missing record was repaired." : issue.origin === "source" ? "The latest source report needs attention. The process may still be running and sending heartbeats." : "No confirmed recovery is attached to this error. Check subsequent runs and coverage before considering it resolved."}</p>
          <a className="monitoring-link" href={issue.origin === "telemetry" ? "/data/tables/meta.service_events" : issue.origin === "source" ? "/data/tables/meta.source_status" : "/data/pipelines"}>Inspect underlying records ↗</a>
        </div>
      </details>)}
      {!data && loading && <div className="monitoring-empty" role="status">Loading error history...</div>}
      {data && !visible.length && <div className="monitoring-empty">{search ? "No loaded errors match this search." : failed || data.unavailable.length ? "No errors available from the responding sources. History is incomplete." : "No recorded errors match this window and service filter."}</div>}
    </div>
    {data?.has_more && <div className="monitoring-notice" role="status">Showing the latest 100 groups. Narrow the service or time range to inspect older errors; search applies to loaded groups only.</div>}
  </section>;
}
