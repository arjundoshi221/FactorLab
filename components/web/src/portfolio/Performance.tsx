import { useState, type ReactNode } from "react";

import { errorMessage, useRemote } from "../shared/api";
import { LineChart, SignedBars, type LineSeries, type SignedBar } from "../shared/charts";
import { useUrlParams } from "../shared/urlState";
import { EmptyState, PortfolioHeader, ProfileSelect, setPortfolioQuery } from "./PortfolioUI";
import type { BrokerAccount, ItemsPage, PerformanceReport, Profile, RiskStats } from "./types";
import "./Portfolio.css";
import "./Risk.css";
import "./Performance.css";

const API = "/hub/api/v1/portfolio";

/* ---------- formatting ---------- */

function money(value: number | null | undefined, currency?: string | null, compact = false): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  const text = compact && abs >= 1000
    ? new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(abs)
    : new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(abs);
  return `${value < 0 ? "−" : ""}${currency ? `${currency} ` : ""}${text}`;
}
const signedMoney = (value: number | null | undefined, currency?: string | null) =>
  value === null || value === undefined || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${money(value, currency)}`;
function pct(value: number | null | undefined, digits = 2, signed = false): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const text = `${Math.abs(value * 100).toFixed(digits)}%`;
  return value < 0 ? `−${text}` : signed && value > 0 ? `+${text}` : text;
}
const tone = (value: number | null | undefined) => (!value ? "" : value < 0 ? "is-negative" : "is-positive");
const shortDate = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "2-digit" });
const monthLabel = (month: string) => new Date(`${month}-01T00:00:00`).toLocaleDateString("en-GB", { month: "short", year: "numeric" });

/* ---------- periods ---------- */

export type Period = "ytd" | "1y" | "all";
const PERIODS: { id: Period; label: string }[] = [{ id: "ytd", label: "YTD" }, { id: "1y", label: "1 year" }, { id: "all", label: "Since start" }];

/** The `since=` date for a period, in New York terms (the API's trading dates). */
export function sinceFor(period: Period, today = new Date()): string {
  const ny = new Date(today.toLocaleString("en-US", { timeZone: "America/New_York" }));
  if (period === "ytd") return `${ny.getFullYear()}-01-01`;
  if (period === "all") return "2000-01-01";
  const back = new Date(ny);
  back.setFullYear(ny.getFullYear() - 1);
  return `${back.getFullYear()}-${String(back.getMonth() + 1).padStart(2, "0")}-${String(back.getDate()).padStart(2, "0")}`;
}

export const performanceUrl = (account: BrokerAccount, userId: string, since: string) =>
  `${API}/accounts/${encodeURIComponent(account.broker_code)}/${encodeURIComponent(account.account_id)}/performance?user_id=${encodeURIComponent(userId)}&since=${since}`;

/* ---------- panels ---------- */

function Card({ title, aside, children, className = "" }: { title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`pf-card ${className}`.trim()} aria-label={title}>
    <header className="pf-card__head"><h2>{title}</h2>{aside && <div className="pf-card__aside">{aside}</div>}</header>
    {children}
  </section>;
}

function Tile({ label, value, foot, className = "" }: { label: string; value: ReactNode; foot?: ReactNode; className?: string }) {
  return <div className={`pf-tile ${className}`.trim()}>
    <span className="pf-tile__label">{label}</span>
    <strong className="pf-tile__value">{value}</strong>
    {foot && <span className="pf-tile__foot">{foot}</span>}
  </div>;
}

function Summary({ report }: { report: PerformanceReport }) {
  const ccy = report.currency;
  const reconciled = report.ibkr_twr !== null && report.twr !== null && Math.abs(report.ibkr_twr - report.twr) < 0.0005;
  return <Card title="Summary" className="pf-card--metrics" aside={<span className="pf-muted">{report.start ? shortDate(report.start) : "Start"} → {report.end ? shortDate(report.end) : "—"} · {ccy}</span>}>
    <div className="pf-tiles pf-perf-tiles">
      <Tile label="Net liquidation" value={money(report.closing_nav, null)} foot={`Opening ${money(report.opening_nav, null)}`} />
      <Tile label="Net profit" value={<span className={tone(report.net_profit)}>{signedMoney(report.net_profit)}</span>} foot={`After ${money(report.net_contributions, null)} net deposits`} />
      <Tile label="Time-weighted return" value={<span className={tone(report.twr)}>{pct(report.twr, 2, true)}</span>}
        foot={report.ibkr_twr === null ? "IBKR figure not available" : reconciled ? "Matches IBKR" : `IBKR ${pct(report.ibkr_twr, 2, true)}`} />
      <Tile label="Money-weighted (XIRR)" value={<span className={tone(report.xirr)}>{pct(report.xirr, 2, true)}</span>} foot="Annualised" />
      <Tile label="Return on capital" value={<span className={tone(report.return_on_capital)}>{pct(report.return_on_capital, 2, true)}</span>} foot="Profit ÷ (opening NAV + deposits)" />
      <Tile label="Financing cost" value={<span className={tone(report.financing_cost)}>{signedMoney(report.financing_cost)}</span>} foot="Interest + accrual change" />
    </div>
  </Card>;
}

function NavChart({ report }: { report: PerformanceReport }) {
  const series: LineSeries[] = [
    { key: "nav", label: "Net liquidation", color: "var(--series-1)", points: report.nav.map(point => ({ t: point.date, value: point.nav })) },
    { key: "capital", label: "Capital contributed", color: "var(--series-4)", dashed: true, points: report.nav.map(point => ({ t: point.date, value: point.contributions })) },
  ];
  return <Card title="Net liquidation vs capital contributed" aside={<span className="pf-muted">{report.currency}</span>}>
    <div className="pf-pad"><LineChart series={series} label="Net liquidation and capital contributed" format={value => money(value, null, true)} formatTime={shortDate} /></div>
    <p className="pf-foot-note">The gap between the lines is cumulative investment profit. Capital contributed is the opening NAV plus net deposits.</p>
  </Card>;
}

const BENCH_COLORS = ["var(--series-2)", "var(--series-3)", "var(--series-5)", "var(--series-6)"];

function ReturnsChart({ report }: { report: PerformanceReport }) {
  const series: LineSeries[] = [
    { key: "portfolio", label: "Portfolio (TWR)", color: "var(--series-1)", points: report.nav.map(point => ({ t: point.date, value: point.twr })) },
    ...report.benchmarks.map((bench, index) => ({
      key: bench.code, label: bench.label, color: BENCH_COLORS[index % BENCH_COLORS.length], dashed: bench.code === "risk_matched",
      points: bench.points.map(point => ({ t: point.date, value: point.value })),
    })),
  ];
  return <Card title="Cumulative return vs benchmarks" aside={<span className="pf-muted">In {report.currency}</span>}>
    <div className="pf-pad"><LineChart series={series} label="Cumulative return against benchmarks" format={value => pct(value, 1)} formatTime={shortDate} zero /></div>
    {report.benchmarks.length > 0 ? <div className="pf-table-scroll"><table className="pf-table" aria-label="Benchmarks">
      <thead><tr><th scope="col">Benchmark</th><th scope="col" className="num">Return</th><th scope="col" className="num">Portfolio vs it</th></tr></thead>
      <tbody>{report.benchmarks.map(bench => <tr key={bench.code}>
        <th scope="row"><span className="pf-limits__name">{bench.label}</span><small>{bench.note}</small></th>
        <td className={`num ${tone(bench.twr)}`}>{pct(bench.twr, 2, true)}</td>
        <td className={`num ${tone(bench.excess)}`}>{pct(bench.excess, 2, true)}</td>
      </tr>)}</tbody>
    </table></div> : <p className="pf-muted pf-pad">No benchmark prices for this period.</p>}
    <p className="pf-foot-note">Benchmarks are converted to {report.currency} at IBKR's daily rates and compared on the same dates. They are price series, so they exclude dividends. The beta-scaled line is the benchmark at the account's measured risk.</p>
  </Card>;
}

function Attribution({ report }: { report: PerformanceReport }) {
  const lines = report.attribution.filter(line => line.key !== "net_profit");
  const net = report.attribution.find(line => line.key === "net_profit");
  const bars: SignedBar[] = lines.map(line => ({ key: line.key, label: line.label, value: line.amount, display: signedMoney(line.amount), title: line.note || undefined }));
  return <Card title="Where the profit came from" aside={<span className="pf-muted">{report.attribution_source === "ibkr" ? "IBKR change in NAV" : "Cash records"} · {report.currency}</span>}>
    {lines.length ? <div className="pf-pad"><SignedBars bars={bars} label="Profit attribution" /></div> : <p className="pf-muted pf-pad">No attribution for this period.</p>}
    {net && <dl className="pf-ledger"><div className="is-strong"><dt>Net profit</dt><dd className={tone(net.amount)}>{signedMoney(net.amount, report.currency)}</dd></div></dl>}
    <p className="pf-foot-note">{report.attribution_source === "ibkr"
      ? "IBKR's own daily change in NAV, summed. Investment gains are mark-to-market on positions; interest and accrual changes together are the financing cost."
      : "From cash records: investment gains are what remains after dividends, interest, commissions, fees and taxes, so they also hold FX translation and accrual changes."}</p>
  </Card>;
}

function Monthly({ report }: { report: PerformanceReport }) {
  return <Card title="Monthly performance" aside={<span className="pf-muted">{report.currency}</span>}>
    <div className="pf-table-scroll"><table className="pf-table" aria-label="Monthly performance">
      <thead><tr><th scope="col">Month</th><th scope="col" className="num">Start NAV</th><th scope="col" className="num">Deposits</th><th scope="col" className="num">Profit</th><th scope="col" className="num">End NAV</th><th scope="col" className="num">TWR</th></tr></thead>
      <tbody>{[...report.monthly].reverse().map(row => <tr key={row.month}>
        <th scope="row">{monthLabel(row.month)}</th>
        <td className="num">{money(row.start_nav, null)}</td>
        <td className="num">{row.flows ? signedMoney(row.flows) : "—"}</td>
        <td className={`num ${tone(row.profit)}`}>{signedMoney(row.profit)}</td>
        <td className="num">{money(row.end_nav, null)}</td>
        <td className={`num ${tone(row.twr)}`}>{pct(row.twr, 2, true)}</td>
      </tr>)}</tbody>
    </table></div>
  </Card>;
}

/** Volatility, tail risk, drawdown and beta over the report's daily time-weighted returns. */
export function RiskStatsCard({ stats, currency, title = "Statistical risk", aside }: { stats: RiskStats | null; currency: string | null; title?: string; aside?: ReactNode }) {
  if (!stats) return <Card title={title} aside={aside}><p className="pf-muted pf-pad">No daily returns for this period.</p></Card>;
  return <Card title={title} aside={aside ?? <span className="pf-muted">{stats.observations} daily returns</span>}>
    <div className="pf-tiles pf-perf-tiles">
      <Tile label="Volatility (annualised)" value={pct(stats.annual_vol, 1)} foot={`Daily ${pct(stats.daily_vol, 2)}`} />
      <Tile label="1-day VaR 95%" value={pct(stats.var95, 2)} foot={stats.var95_amount === null ? "Needs 20+ days" : money(stats.var95_amount, currency)} />
      <Tile label="Expected shortfall 95%" value={pct(stats.es95, 2)} foot={stats.es95_amount === null ? "Needs 20+ days" : money(stats.es95_amount, currency)} />
      <Tile label="Maximum drawdown" value={<span className={tone(stats.max_drawdown)}>{pct(stats.max_drawdown, 1)}</span>}
        foot={stats.drawdown_peak && stats.drawdown_trough ? `${shortDate(stats.drawdown_peak)} → ${shortDate(stats.drawdown_trough)}` : "No drawdown"} />
      <Tile label="Current drawdown" value={<span className={tone(stats.current_drawdown)}>{pct(stats.current_drawdown, 1)}</span>} foot="Below the previous peak" />
      <Tile label="Beta" value={stats.beta === null ? "—" : stats.beta.toFixed(2)} foot={stats.benchmark ? `vs ${stats.benchmark}${stats.correlation === null ? "" : `, ρ ${stats.correlation.toFixed(2)}`}` : "Needs 20+ days of benchmark"} />
    </div>
    <p className="pf-foot-note">From daily time-weighted returns, so deposits never read as gains. VaR is the 5th-percentile daily loss (historical); expected shortfall is the average loss beyond it, both at today's NAV.{stats.enough_history ? "" : " Fewer than 252 days of history: treat the tail figures as rough."}</p>
  </Card>;
}

/* ---------- page ---------- */

function PerformanceBoard({ account, userId }: { account: BrokerAccount; userId: string }) {
  const [params, update] = useUrlParams();
  const period = (PERIODS.find(item => item.id === params.get("period"))?.id ?? "ytd") as Period;
  const remote = useRemote<PerformanceReport>(performanceUrl(account, userId, sinceFor(period)), 300_000);
  const report = remote.data;
  const chips = <div className="pf-perf-periods" role="group" aria-label="Period">{PERIODS.map(item =>
    <button key={item.id} type="button" className={`pf-btn${item.id === period ? " pf-btn--primary" : ""}`} aria-pressed={item.id === period}
      onClick={() => update(next => { if (item.id === "ytd") next.delete("period"); else next.set("period", item.id); })}>{item.label}</button>)}</div>;
  if (remote.loading && !report) return <>{chips}<p className="pf-muted pf-pad" role="status">Loading performance…</p></>;
  if (!report) return <>{chips}<p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(remote.error, "Performance is unavailable.")}</p></>;
  return <div className="pf-risk pf-perf">
    {chips}
    {Boolean(remote.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">Refresh failed. Showing the last successful report.</p>}
    {report.warnings.map(warning => <p key={warning} className="pf-notice pf-notice--warn" role="status">{warning}</p>)}
    {report.net_profit === null ? <EmptyState title="No performance for this period.">There is no net liquidation history yet.</EmptyState> : <>
      <Summary report={report} />
      <div className="pf-grid pf-grid--mid">
        <NavChart report={report} />
        <ReturnsChart report={report} />
      </div>
      <div className="pf-grid pf-grid--mid">
        <Attribution report={report} />
        <RiskStatsCard stats={report.risk} currency={report.currency} />
      </div>
      <Monthly report={report} />
      <details className="pf-fold"><summary><h2>How these figures are calculated</h2></summary>
        <div className="pf-fold__body"><ul className="pf-perf-basis">{report.basis.map(line => <li key={line}>{line}</li>)}</ul></div></details>
    </>}
  </div>;
}

function AccountPicker({ userId, selected, onSelect }: { userId: string; selected: string; onSelect: (value: string) => void }) {
  const remote = useRemote<ItemsPage<BrokerAccount>>(`${API}/accounts?user_id=${encodeURIComponent(userId)}`, 60_000);
  const accounts = remote.data?.items ?? [];
  const account = accounts.find(item => JSON.stringify([item.broker_code, item.account_id]) === selected) ?? accounts[0];
  return <>
    {Boolean(remote.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(remote.error, "Account list is unavailable.")}</p>}
    {remote.loading && !remote.data ? <p className="pf-muted pf-pad" role="status">Loading accounts…</p> : <>
      {accounts.length > 1 && <label className="portfolio-selector pf-account-select"><span>Broker account</span>
        <select value={account ? JSON.stringify([account.broker_code, account.account_id]) : ""} onChange={event => onSelect(event.target.value)}>
          {accounts.map(item => <option key={JSON.stringify([item.broker_code, item.account_id])} value={JSON.stringify([item.broker_code, item.account_id])}>{item.broker_code.toUpperCase()} · {item.account_id}</option>)}
        </select></label>}
      {account ? <PerformanceBoard key={JSON.stringify([userId, account.broker_code, account.account_id])} account={account} userId={userId} />
        : !remote.error && <EmptyState title="No accounts registered for this profile.">Choose another profile to see its performance.</EmptyState>}
    </>}
  </>;
}

export function Performance() {
  const remote = useRemote<ItemsPage<Profile>>(`${API}/profiles`, 60_000);
  const [userId, setUserId] = useState<string | null>(() => new URLSearchParams(window.location.search).get("user_id"));
  const [account, setAccount] = useState(() => new URLSearchParams(window.location.search).get("account") ?? "");
  const profiles = remote.data?.items ?? [];
  const selected = userId ?? profiles.find(profile => profile.active)?.user_id ?? profiles[0]?.user_id ?? "";
  return <main className="portfolio-page pf-risk-page">
    <PortfolioHeader title="Performance" description="How much the account is making after every cost: net profit, time- and money-weighted returns, where the profit came from, and how it compares with the market.">
      <ProfileSelect profiles={profiles} value={selected} disabled={remote.loading && !remote.data} all onChange={value => { setUserId(value); setAccount(""); setPortfolioQuery(value); }} />
    </PortfolioHeader>
    {Boolean(remote.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(remote.error, "Profiles are unavailable.")}</p>}
    {remote.loading && !remote.data ? <p className="pf-muted pf-pad" role="status">Loading profiles…</p>
      : <AccountPicker key={selected} userId={selected} selected={account} onSelect={value => { setAccount(value); setPortfolioQuery(selected, value); }} />}
  </main>;
}
