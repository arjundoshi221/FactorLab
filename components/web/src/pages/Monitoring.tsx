import { useEffect, useState } from "react";

interface Service {
  service_id: string;
  status: string;
  registered: boolean;
  last_seen_age_s: number | null;
  uptime_s: number | null;
  restarts_24h?: number;
}
interface Event {
  received_at: string;
  service_id: string;
  type: string;
  state: string;
  severity: string;
  error_type: string;
  message: string;
}
const colors: Record<string, string> = { up: "#26734d", late: "#a66b00", down: "#b83232", stopped: "#64748b", unknown: "#64748b" };
function age(seconds: number | null) {
  if (seconds === null) return "Never";
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h`;
}

export function Monitoring() {
  const [services, setServices] = useState<Service[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [service, setService] = useState("");
  const [type, setType] = useState("");
  const [since, setSince] = useState("");
  useEffect(() => {
    let disposed = false;
    let controller: AbortController | null = null;
    const refresh = async () => {
      controller?.abort();
      controller = new AbortController();
      const params = new URLSearchParams({ limit: "100" });
      if (service) params.set("service_id", service);
      if (type) params.set("type", type);
      if (since) params.set("since", new Date(since).toISOString());
      try {
        const responses = await Promise.all([
          fetch("/hub/api/v1/monitoring/services", { signal: controller.signal }),
          fetch(`/hub/api/v1/monitoring/events?${params}`, { signal: controller.signal }),
        ]);
        if (responses.some(r => !r.ok)) throw new Error("Monitoring data is unavailable");
        const [status, history] = await Promise.all(responses.map(r => r.json()));
        if (!disposed) {
          setServices(status.services); setOffline(status.monitor_offline);
          setEvents(history.events); setError("");
        }
      } catch (err) {
        if (!disposed && (err as Error).name !== "AbortError") setError("Monitoring data is unavailable. Showing the last snapshot.");
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => { disposed = true; controller?.abort(); window.clearInterval(timer); };
  }, [service, type, since]);
  return <main className="dashboard">
    <h1>Service monitoring</h1>
    {offline && <p role="alert">Monitor offline — service status may be stale.</p>}
    {error && <p role="alert">{error}</p>}
    <section aria-label="Service status" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 16 }}>
      {services.map(s => <article key={s.service_id} style={{ border: `2px solid ${colors[s.status] || colors.unknown}`, borderRadius: 8, padding: 16 }}>
        <h2>{s.service_id}</h2><strong>{s.status}</strong>{!s.registered && <span> · Unregistered</span>}
        <p>Last seen: {age(s.last_seen_age_s)}{s.last_seen_age_s !== null ? " ago" : ""}</p>
        <p>Uptime: {age(s.uptime_s)} · Restarts (24h): {s.restarts_24h ?? 0}</p>
      </article>)}
    </section>
    <h2>Events</h2>
    <label>Service <select value={service} onChange={e => setService(e.target.value)}><option value="">All services</option>{services.map(s => <option key={s.service_id}>{s.service_id}</option>)}</select></label>{" "}
    <label>Type <select value={type} onChange={e => setType(e.target.value)}><option value="">All types</option>{["hb", "warn", "err", "state", "restart"].map(t => <option key={t}>{t}</option>)}</select></label>{" "}
    <label>Since <input type="datetime-local" value={since} onChange={e => setSince(e.target.value)} /></label>
    <table><thead><tr>{["Received", "Service", "Type", "State / severity", "Error", "Message"].map(h => <th key={h}>{h}</th>)}</tr></thead>
      <tbody>{events.map((e, i) => <tr key={`${e.received_at}-${i}`}><td>{new Date(e.received_at).toLocaleString()}</td><td>{e.service_id}</td><td>{e.type}</td><td>{e.state || e.severity}</td><td>{e.error_type}</td><td>{e.message}</td></tr>)}</tbody>
    </table>
    {!events.length && <p>No events match these filters.</p>}
  </main>;
}
