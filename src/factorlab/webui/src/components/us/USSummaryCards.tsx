import type { USDashboard, USResolution } from "../../usTypes";
import { USStatusPill } from "./USStatusPill";

export function USSummaryCards({ dashboard }: { dashboard: USDashboard }) {
  return <>
    <section className="metric-grid" aria-label="US collection summary">
      <article className="metric-card"><span>US references</span>
        <strong>{dashboard.universe.active.toLocaleString()} stocks</strong>
        <small>{dashboard.universe.daily_configured.toLocaleString()} daily configured</small></article>
      <article className="metric-card"><span>Liquid minute tier</span>
        <strong>{dashboard.universe.minute_configured.toLocaleString()} / 250</strong>
        <small>{dashboard.universe.minute_with_data.toLocaleString()} with minute data</small></article>
      <article className="metric-card"><span>Daily history</span>
        <strong>{dashboard.universe.daily_with_data.toLocaleString()}</strong>
        <small>{dashboard.universe.no_daily_data.toLocaleString()} awaiting data</small></article>
      <article className="metric-card"><span>Selected session</span>
        <strong>{dashboard.trading_date}</strong><small>Market {dashboard.market_status}</small></article>
      {(["1min", "daily"] as USResolution[]).map(resolution => <article className="metric-card" key={resolution}>
        <span>{resolution === "1min" ? "Minute" : "Daily"} coverage</span>
        <strong>{dashboard.resolutions[resolution].coverage_percent === null ? "Not expected"
          : `${dashboard.resolutions[resolution].coverage_percent}%`}</strong>
        <small>{dashboard.resolutions[resolution].actual.toLocaleString()} / {dashboard.resolutions[resolution].expected.toLocaleString()} bars</small>
      </article>)}
    </section>
    <section className="source-health" aria-label="US data sources">
      {dashboard.sources.map(source => <article className="metric-card" key={source.source}>
        <span>{source.source.toUpperCase()}</span><USStatusPill status={source.status} />
        <small>{source.detail}</small>
      </article>)}
    </section>
  </>;
}
