import { useEffect, useState } from "react";
import "./Monitoring.css";
import { DailyStrip, MonitoringOperations, useMonitoringOperations } from "./MonitoringOperations";
import { SourceAlerts, sourceProblems } from "./MonitoringSources";
import { MonitoringErrors } from "./MonitoringErrors";

interface Service {
  service_id: string; status: string; registered: boolean;
  last_seen_age_s: number | null; uptime_s: number | null; restarts_24h?: number;
  last_error_message?: string; last_error_at?: string; warn_count_1h?: number; err_count_1h?: number;
  packet_loss?: number; monitor?: { max_silence_s?: number; interval_s?: number };
}
interface Event {
  received_at: string; service_id: string; type: string;
  state?: string; severity?: string; error_type?: string; message?: string;
}
const names: Record<string, string> = {
  "ingest-india": "India markets", "ingest-us": "US markets", "ingest-political": "Political disclosures",
  "ingest-broker": "Broker ingestion", "secrets-agent": "Secrets agent", monitor: "Service monitor", api: "Hub API",
  "ibkr-snapshot": "IBKR snapshots", "universe-us": "US universe",
};
const statuses: Record<string, string> = { up: "Healthy", late: "Heartbeat delayed", down: "No heartbeat", stopped: "Stopped", unknown: "Awaiting first report" };
const types: Record<string, string> = { hb: "Heartbeat", warn: "Warning", err: "Error", state: "State change", restart: "Restart" };
function duration(seconds: number | null) {
  if (seconds === null) return "\u2014";
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor(seconds % 86400 / 3600)}h`;
}
function clock(value: string | Date) {
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function Monitoring() {
  const [services, setServices] = useState<Service[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [updated, setUpdated] = useState<Date | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);
  const [service, setService] = useState("");
  const [type, setType] = useState("");
  const [windowHours, setWindowHours] = useState("");
  const [historyFilter, setHistoryFilter] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [attentionOnly, setAttentionOnly] = useState(false);
  const operations = useMonitoringOperations(refreshKey);
  const sourceFailures = sourceProblems(operations.pipelines.data);
  const sourceFailure = (s: Service) => sourceFailures.find(source => source.service === s.service_id);
  const needsAttention = (s: Service) => ["down", "late", "unknown"].includes(s.status) || !s.registered || Boolean(s.err_count_1h || s.warn_count_1h || sourceFailure(s));
  const serviceTone = (s: Service) => {
    if (error || offline) return "unknown";
    const source = sourceFailure(s);
    if (source) {
      if (operations.pipelines.error || source.stale) return "unknown";
      return source.severe ? "down" : "late";
    }
    if (s.status === "up" && s.err_count_1h) return "down";
    if (s.status === "up" && s.warn_count_1h) return "late";
    return s.status;
  };
  const serviceLabel = (s: Service) => {
    if (error || offline) return `Stale · ${statuses[s.status] || s.status}`;
    const source = sourceFailure(s);
    if (source) return operations.pipelines.error || source.stale ? "Collection status stale" : "Collection needs attention";
    if (s.status === "up" && s.err_count_1h) return "Reporting · recent errors";
    if (s.status === "up" && s.warn_count_1h) return "Reporting · recent warnings";
    return statuses[s.status] || s.status;
  };
  const filterKey = JSON.stringify([service, type, windowHours]);
  useEffect(() => {
    let disposed = false;
    let controller: AbortController | null = null;
    const refresh = async () => {
      controller?.abort();
      const request = new AbortController();
      controller = request;
      setLoading(true);
      const params = new URLSearchParams({ limit: "100" });
      if (service) params.set("service_id", service);
      if (type) params.set("type", type);
      if (windowHours) params.set("since", new Date(Date.now() - Number(windowHours) * 3600000).toISOString());
      try {
        const responses = await Promise.all([
          fetch("/hub/api/v1/monitoring/services", { signal: request.signal }),
          fetch(`/hub/api/v1/monitoring/events?${params}`, { signal: request.signal }),
        ]);
        if (responses.some(r => !r.ok)) throw new Error("Monitoring data is unavailable");
        const [status, history] = await Promise.all(responses.map(r => r.json()));
        if (!disposed && !request.signal.aborted) {
          setServices(status.services); setOffline(status.monitor_offline);
          setEvents(history.events); setHistoryFilter(filterKey); setError(""); setUpdated(new Date());
        }
      } catch (err) {
        if (!disposed && !request.signal.aborted && (err as Error).name !== "AbortError") setError("Unable to refresh monitoring data.");
      } finally {
        if (!disposed && !request.signal.aborted) setLoading(false);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => { disposed = true; controller?.abort(); window.clearInterval(timer); };
  }, [service, type, windowHours, refreshKey, filterKey]);
  const ordered = [...services].filter(s => (!attentionOnly || needsAttention(s)) && `${s.service_id} ${names[s.service_id] || ""}`.toLowerCase().includes(search.toLowerCase())).sort((a, b) => {
    const rank: Record<string, number> = { down: 0, late: 1, unknown: 2, up: 3, stopped: 4 };
    return Number(needsAttention(b)) - Number(needsAttention(a)) || (rank[a.status] ?? 2) - (rank[b.status] ?? 2) || a.service_id.localeCompare(b.service_id);
  });
  const filtered = Boolean(service || type || windowHours);
  const visibleEvents = historyFilter === filterKey ? events : [];
  return <main className="monitoring">
    <header className="monitoring-hero">
      <div><span className="eyebrow">Operations / Control room</span><h1>Service monitoring</h1><p>Live health, daily ingestion and data gaps. One operational view.</p></div>
      <div className="monitoring-refresh">
        <span className={`monitoring-chip monitoring-chip--${!updated ? "unknown" : error || offline ? "late" : "up"}`}><i />{error ? "Connection lost" : !updated ? "Connecting" : offline ? "Monitor offline" : "Monitor connected"}</span>
        <span>{updated ? `Checked ${clock(updated)}` : "Waiting for monitoring data"} &middot; every 15s</span>
        <button type="button" disabled={loading} onClick={() => setRefreshKey(k => k + 1)}>{loading ? "Refreshing..." : "Refresh now"}</button>
      </div>
    </header>
    {offline && <div className="monitoring-notice" role="alert"><strong>Monitor offline.</strong> Heartbeat tracking is interrupted. Service statuses may be stale.</div>}
    {error && <div className="monitoring-notice monitoring-notice--error" role="alert"><strong>{error}</strong> {updated ? "Showing the last successful snapshot." : "Try refreshing to reconnect."}</div>}
    <SourceAlerts data={operations.pipelines.data} stale={operations.pipelines.error} />
    <nav className="monitoring-section-nav" aria-label="Monitoring sections"><a href="#monitoring-services-title">Service health</a><a href="#monitoring-errors-title">Error inbox</a><a href="#monitoring-data-title">Data completeness</a><a href="#monitoring-pipelines-title">Ingestion</a></nav>
    <section className="monitoring-summary" aria-label="Health overview">
      {[
        ["Healthy", services.filter(s => s.status === "up" && !needsAttention(s)).length, "up", error || offline ? "Stale snapshot · verify connection" : "On time, no errors or warnings / 1h"],
        ["Needs attention", services.filter(needsAttention).length, "down", "Heartbeat, telemetry or recent error issues"],
        ["Awaiting report", services.filter(s => s.status === "unknown").length, "unknown", "No telemetry received yet"],
        ["Stopped", services.filter(s => s.status === "stopped").length, "stopped", "Reported a deliberate stop"],
      ].map(([label, count, tone, description]) => <div className={`monitoring-summary-item monitoring-summary-item--${tone}`} key={label}><span>{label}</span><strong>{updated ? count : "\u2014"}</strong><small>{description}</small></div>)}
    </section>
    <div className="monitoring-daily-overview">
      <div><span>Errors today · UTC</span><strong>{operations.daily.data?.days ? operations.daily.data.days.filter(d => d.day === operations.daily.data!.generated_at.slice(0, 10)).reduce((sum, d) => sum + d.errors, 0).toLocaleString() : "—"}</strong><small>{operations.daily.error ? "Daily telemetry unavailable or stale" : "Full stored event counts"}</small></div>
      <div><span>Pipeline problems / 24h</span><strong>{operations.pipelines.data?.pipelines ? operations.pipelines.data.pipelines.reduce((sum, p) => sum + p.problem_24h, 0).toLocaleString() : "—"}</strong><small>{operations.pipelines.error ? "Ingestion snapshot unavailable or stale" : "Failed and partial runs"}</small></div>
      <div><span>Recorded missing bars</span><strong>{operations.coverage.data?.feeds?.length ? operations.coverage.data.feeds.reduce((sum, f) => sum + f.missing, 0).toLocaleString() : "—"}</strong><small>{operations.coverage.error ? "Coverage snapshot unavailable or stale" : operations.coverage.data?.feeds?.length ? `${operations.coverage.data.feeds.reduce((sum, f) => sum + (f.unreported_series ?? 0), 0).toLocaleString()} active series without a session check` : "Latest checked sessions · recorded series only"}</small></div>
    </div>
    <section aria-labelledby="monitoring-services-title">
      <div className="monitoring-section-heading"><div><h2 id="monitoring-services-title">Services <span>{services.length}</span></h2><p>Services needing attention appear first. Select a card to view its events.</p></div>{service && <button className="monitoring-quiet-button" onClick={() => setService("")}>Show all services</button>}</div>
      <div className="monitoring-grid-tools"><label>Find a service<input type="search" placeholder="Search name or service ID" value={search} onChange={e => setSearch(e.target.value)} /></label><label className="monitoring-toggle"><input type="checkbox" checked={attentionOnly} onChange={e => setAttentionOnly(e.target.checked)} /> Needs attention only</label><span>7-day strips: green = reports without errors or down transitions; amber = warning / restart; red = errors / down transitions; grey = no reports. These show activity, not uptime.</span></div>
      {operations.daily.error && <div className="monitoring-notice" role="status">Daily telemetry refresh failed. {operations.daily.data ? "The daily strips show the last snapshot." : "Daily history is unavailable."}</div>}
      {!updated && loading && <div className="monitoring-empty" role="status">Loading service health...</div>}
      {updated && !services.length && <div className="monitoring-empty">No services have been registered yet.</div>}
      <div className="monitoring-services">
        {ordered.map(s => <button type="button" className={`monitoring-service monitoring-service--${serviceTone(s)} ${service === s.service_id ? "is-selected" : ""}`} key={s.service_id} aria-pressed={service === s.service_id} aria-label={`View events for ${s.service_id}`} onClick={() => setService(service === s.service_id ? "" : s.service_id)}>
          <div className="monitoring-service-top"><span className={`monitoring-chip monitoring-chip--${serviceTone(s)}`}><i />{serviceLabel(s)}</span><span className="monitoring-service-kind">{s.monitor?.max_silence_s ? "Scheduled job" : "Service"}</span></div>
          <h3>{names[s.service_id] || s.service_id.replaceAll("-", " ")}</h3><code>{s.service_id}</code>
          {sourceFailure(s) && <p className="monitoring-service-error">{sourceFailure(s)?.detail || "Collection has not reported a healthy source status."}</p>}
          <dl><div><dt>Last report</dt><dd>{s.last_seen_age_s === null ? "Not yet reported" : `${duration(s.last_seen_age_s)} ago`}</dd></div><div><dt>Instance uptime</dt><dd>{duration(s.uptime_s)}</dd></div><div><dt>Restarts / 24h</dt><dd>{s.restarts_24h ?? 0}</dd></div></dl>
          <div className="monitoring-live-metrics"><span className={s.err_count_1h ? "monitoring-danger" : ""}><strong>{s.err_count_1h ?? "—"}</strong> errors / 1h</span><span><strong>{s.warn_count_1h ?? "—"}</strong> warnings / 1h</span><span><strong>{s.packet_loss ?? "—"}</strong> packet gaps</span></div>
          {operations.daily.data?.days && <DailyStrip reports={operations.daily.data.days.filter(d => d.service_id === s.service_id)} generatedAt={operations.daily.data.generated_at} stale={operations.daily.error} />}
          <p className="monitoring-session">{s.monitor?.max_silence_s ? `Run deadline: ${duration(s.monitor.max_silence_s)}` : s.monitor?.interval_s ? `Heartbeat every ${duration(s.monitor.interval_s)} · down after ${duration(s.monitor.interval_s * 3)}` : "No configured heartbeat interval"}</p>
          <div className="monitoring-service-note">{!s.registered ? "Unregistered sender; check service configuration" : s.status === "unknown" ? s.monitor?.max_silence_s ? "Waiting for this job\u2019s first monitored run." : "Waiting for the service\u2019s first heartbeat." : s.status === "stopped" ? "The service reported that it stopped." : s.status === "down" ? "Heartbeat deadline exceeded. Check the service." : s.status === "late" ? "The latest heartbeat is overdue." : "Heartbeats are arriving within the expected interval."}</div>
          {s.last_error_message && <p className="monitoring-service-error" title={s.last_error_message}>Last error{s.last_error_at ? ` · ${new Date(s.last_error_at).toLocaleString()}` : ""}: {s.last_error_message}</p>}
        </button>)}
      </div>
      {updated && services.length > 0 && !ordered.length && <div className="monitoring-empty">No services match these controls.</div>}
    </section>
    <MonitoringErrors refreshKey={refreshKey} services={services.map(s => s.service_id)} />
    <MonitoringOperations coverage={operations.coverage} pipelines={operations.pipelines} showSourceAlerts={false} />
    <section className="monitoring-history" aria-labelledby="monitoring-events-title">
      <div className="monitoring-section-heading"><div><h2 id="monitoring-events-title">Event activity</h2><p>Latest 100 matching events &middot; times in your local timezone.</p></div><span className="monitoring-event-count" role="status">{loading ? "Updating..." : `${visibleEvents.length} events`}</span></div>
      <div className="monitoring-filters">
        <label>Service<select value={service} onChange={e => setService(e.target.value)}><option value="">All services</option>{services.map(s => <option key={s.service_id} value={s.service_id}>{names[s.service_id] || s.service_id}</option>)}</select></label>
        <label>Type<select value={type} onChange={e => setType(e.target.value)}><option value="">All event types</option>{Object.entries(types).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Time range<select value={windowHours} onChange={e => setWindowHours(e.target.value)}><option value="">Any time</option><option value="1">Last hour</option><option value="24">Last 24 hours</option><option value="168">Last 7 days</option></select></label>
        {filtered && <button className="monitoring-quiet-button" onClick={() => { setService(""); setType(""); setWindowHours(""); }}>Clear filters</button>}
      </div>
      <div className="monitoring-event-scroll" aria-busy={loading}>
        <table className="monitoring-events"><thead><tr><th scope="col">Time</th><th scope="col">Service</th><th scope="col">Event</th><th scope="col">Details</th></tr></thead><tbody>
          {visibleEvents.map((e, i) => <tr key={`${e.received_at}-${e.service_id}-${i}`}><td><time dateTime={e.received_at} title={new Date(e.received_at).toLocaleString()}>{clock(e.received_at)}<small>{new Date(e.received_at).toLocaleDateString([], { month: "short", day: "numeric" })}</small></time></td><td><span className="monitoring-event-service">{names[e.service_id] || e.service_id}</span><code>{e.service_id}</code></td><td><span className={`monitoring-event-type monitoring-event-type--${e.type}`}>{types[e.type] || e.type}</span></td><td>{e.state && <span className="monitoring-event-state">{e.state}</span>}{e.error_type && <span className="monitoring-event-error">{e.error_type}</span>}<span className="monitoring-event-message">{e.message || (e.type === "hb" ? "Heartbeat received" : e.severity || "\u2014")}</span></td></tr>)}
        </tbody></table>
        {loading && historyFilter !== filterKey ? <div className="monitoring-empty" role="status">Loading events...</div> : !visibleEvents.length && <div className="monitoring-empty"><strong>{error ? "Event data unavailable" : filtered ? "No events match these filters" : "No events reported yet"}</strong><p>{error ? "Refresh to try again." : filtered ? "Choose another service or a wider time range." : "Activity will appear when services send their first reports."}</p></div>}
      </div>
    </section>
  </main>;
}
