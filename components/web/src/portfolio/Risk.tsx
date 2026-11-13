import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { errorMessage, useRemote } from "../shared/api";
import { formatTime } from "../shared/format";
import {
  DEFAULT_CASH_LIKE, DEFAULT_LIMITS, LIMIT_SPECS, computeRisk, contributionToReach, deployableCapacity, explainMetric, hasReconciliationGap,
  overallStatus, statusOf, stressTests, type CalcTerm, type Calculation, type Limit, type LimitKey, type LimitSpec, type Limits, type RiskState, type Status,
} from "./riskModel";
import { BENCHMARK, useBetas, type BetaResult } from "./betas";
import type { AccountSummary, BrokerAccount, ItemsPage, Profile, SnapshotPage } from "./types";
import { EmptyState, PortfolioHeader, ProfileSelect, setPortfolioQuery } from "./PortfolioUI";
import "./Portfolio.css";
import "./Risk.css";

const API = "/hub/api/v1/portfolio";

/* ---------- formatting ---------- */

function money(value: number | null, currency?: string, compact = false): string {
  if (value === null || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  const text = compact && abs >= 1000
    ? new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: abs >= 100_000 ? 0 : 1 }).format(abs)
    : new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(abs);
  return `${value < 0 ? "−" : ""}${currency ? `${currency} ` : ""}${text}`;
}
const times = (value: number | null, digits = 2) => (value === null || !Number.isFinite(value) ? "—" : `${value.toFixed(digits)}×`);
const pct = (value: number | null, digits = 1) => (value === null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(digits)}%`);
const signedPct = (value: number) => `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value * 100).toFixed(1)}%`;
const signedMoney = (value: number | null, currency?: string) => (value === null || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${money(value, currency)}`);
const pnlTone = (value: number | null) => (value === null || value === 0 ? "" : value < 0 ? "is-negative" : "is-positive");
/** Two decimals, for showing the arithmetic behind a metric. */
function exact(value: number | null, currency?: string): string {
  if (value === null || !Number.isFinite(value)) return "—";
  const text = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Math.abs(value));
  return `${value < 0 ? "−" : ""}${currency ? `${currency} ` : ""}${text}`;
}
const formatLimit = (spec: LimitSpec, value: number | null) => (spec.unit === "x" ? times(value) : pct(value));
const comparator = (spec: LimitSpec) => (spec.direction === "max" ? "≤" : "≥");

const STATUS_LABEL: Record<Status, string> = { green: "Within target", amber: "Above target", red: "Hard limit breached", unknown: "Unverified" };
const VERDICT_LABEL: Record<Status, string> = { green: "Green", amber: "Amber", red: "Red", unknown: "Unverified" };

function StatusIcon({ status }: { status: Status }) {
  const glyph = { green: "✓", amber: "!", red: "×", unknown: "?" }[status];
  return <span className={`pf-status-icon pf-status-icon--${status}`} aria-hidden="true">{glyph}</span>;
}

function StatusTag({ status, children }: { status: Status; children?: ReactNode }) {
  return <span className={`pf-status pf-status--${status}`}><StatusIcon status={status} />{children ?? STATUS_LABEL[status]}</span>;
}

/* ---------- settings (per account, this browser) ---------- */

interface RiskSettings { limits: Limits; cashLike: string[] }
const settingsKey = (account: BrokerAccount) => `factorlab.portfolio.risk.v1:${account.broker_code}:${account.account_id}`;

function loadSettings(account: BrokerAccount): RiskSettings {
  try {
    const raw = window.localStorage.getItem(settingsKey(account));
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<RiskSettings>;
      const limits = { ...DEFAULT_LIMITS };
      for (const spec of LIMIT_SPECS) {
        const saved = parsed.limits?.[spec.key];
        if (saved && Number.isFinite(saved.target) && Number.isFinite(saved.hard)) limits[spec.key] = { target: saved.target, hard: saved.hard };
      }
      return { limits, cashLike: Array.isArray(parsed.cashLike) ? parsed.cashLike.map(String) : DEFAULT_CASH_LIKE };
    }
  } catch { /* storage blocked or corrupt: use defaults */ }
  return { limits: DEFAULT_LIMITS, cashLike: DEFAULT_CASH_LIKE };
}

function saveSettings(account: BrokerAccount, settings: RiskSettings | null) {
  try {
    if (settings) window.localStorage.setItem(settingsKey(account), JSON.stringify(settings));
    else window.localStorage.removeItem(settingsKey(account));
  } catch { /* not persisted */ }
}

/* ---------- composition donut ---------- */

interface Slice { key: string; label: string; value: number; tone: string; note?: string }

function Donut({ slices, total, center, sub }: { slices: Slice[]; total: number; center: string; sub: string }) {
  const [hover, setHover] = useState<string | null>(null);
  const size = 168, stroke = 26, radius = (size - stroke) / 2, circumference = 2 * Math.PI * radius;
  const gap = slices.length > 1 ? 2 : 0;
  let offset = 0;
  const active = slices.find(slice => slice.key === hover);
  return <div className="pf-donut">
    <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} role="img" aria-label={`Composition: ${slices.map(slice => `${slice.label} ${pct(slice.value / total)}`).join(", ")}`}>
      <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--raised)" strokeWidth={stroke} />
      {total > 0 && slices.map(slice => {
        const length = (slice.value / total) * circumference;
        const dash = Math.max(0, length - gap);
        const element = <circle key={slice.key} cx={size / 2} cy={size / 2} r={radius} fill="none" stroke={slice.tone} strokeWidth={hover === slice.key ? stroke + 4 : stroke}
          strokeDasharray={`${dash} ${circumference - dash}`} strokeDashoffset={-offset} transform={`rotate(-90 ${size / 2} ${size / 2})`}
          onMouseEnter={() => setHover(slice.key)} onMouseLeave={() => setHover(null)}><title>{`${slice.label}: ${pct(slice.value / total)}`}</title></circle>;
        offset += length;
        return element;
      })}
      <text x="50%" y="47%" textAnchor="middle" className="pf-donut__value">{active ? pct(active.value / total) : center}</text>
      <text x="50%" y="60%" textAnchor="middle" className="pf-donut__sub">{active ? active.label : sub}</text>
    </svg>
  </div>;
}

/* ---------- calculation breakdown (i) ---------- */

function useCalc() {
  const [open, setOpen] = useState<LimitKey | null>(null);
  return { open, toggle: (key: LimitKey) => setOpen(current => (current === key ? null : key)), close: () => setOpen(null) };
}

function InfoButton({ spec, open, onToggle }: { spec: LimitSpec; open: boolean; onToggle: () => void }) {
  return <button type="button" className={`pf-info${open ? " is-open" : ""}`} aria-label={`How ${spec.label.toLowerCase()} is calculated`} aria-expanded={open} onClick={onToggle}>
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" strokeWidth="1.4" /><circle cx="8" cy="4.8" r="1" fill="currentColor" /><path d="M8 7.2v4.6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
  </button>;
}

function termInputs(term: CalcTerm, base: string): string {
  const parts: string[] = [];
  if (term.native) parts.push(`${exact(term.native.amount, term.native.currency)} × ${term.native.fx === null ? "no FX rate" : `${term.native.fx.toFixed(6)} ${term.native.currency}→${base}`}`);
  if (term.beta) parts.push(`${term.native ? "" : `${exact(term.beta.exposure, base)} `}× β ${term.beta.value.toFixed(4)} (${term.beta.source})`);
  return parts.join(" ");
}

function CalcPanel({ calc, base, onClose }: { calc: Calculation; base: string; onClose: () => void }) {
  const spec = LIMIT_SPECS.find(item => item.key === calc.key)!;
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.focus({ preventScroll: true }); ref.current?.scrollIntoView?.({ block: "nearest" }); }, [calc.key]);
  const { numerator, denominator, result } = calc;
  const resultText = result === null ? "unavailable" : spec.unit === "x" ? `${result.toFixed(4)}×` : `${(result * 100).toFixed(2)}%`;
  return <div ref={ref} tabIndex={-1} className="pf-calc" role="region" aria-label={`${calc.label} calculation`} onKeyDown={event => { if (event.key === "Escape") onClose(); }}>
    <header className="pf-calc__head">
      <div><h3>How {calc.label.toLowerCase()} is calculated</h3><code>{calc.formula}</code></div>
      <button type="button" className="pf-btn pf-btn--ghost" aria-label="Close calculation" onClick={onClose}>×</button>
    </header>
    <div className="pf-calc__body">
      <section>
        <h4>{numerator.label}<small>{numerator.source}</small></h4>
        {numerator.terms.length > 0 ? <div className="pf-table-scroll pf-calc__scroll"><table className="pf-calc__terms" aria-label={`${numerator.label} inputs`}>
          <thead><tr><th scope="col">Item</th><th scope="col">Inputs</th><th scope="col" className="num">{base}</th></tr></thead>
          <tbody>{numerator.terms.map((term, index) => <tr key={`${term.label}-${index}`}>
            <th scope="row">{term.label}{term.note && <small>{term.note}</small>}</th>
            <td className="pf-calc__inputs">{termInputs(term, base)}</td>
            <td className="num">{exact(term.value)}</td>
          </tr>)}</tbody>
          <tfoot><tr><th scope="row" colSpan={2}>{numerator.transform === "abs" ? `|Σ| = ${numerator.label}` : `Σ = ${numerator.label}`}</th><td className="num">{exact(numerator.value)}</td></tr></tfoot>
        </table></div> : <p className="pf-calc__value">{exact(numerator.value, base)}</p>}
      </section>
      <section>
        <h4>{denominator.label}<small>{denominator.source}</small></h4>
        <p className="pf-calc__value">{exact(denominator.value, base)}</p>
      </section>
    </div>
    <p className="pf-calc__result"><span>{exact(numerator.value)} ÷ {exact(denominator.value)}</span> = <b>{resultText}</b>
      {result !== null && <small> shown as {formatLimit(spec, result)}</small>}</p>
    {calc.notes.map(note => <p key={note} className="pf-calc__note">{note}</p>)}
  </div>;
}

/* ---------- panels ---------- */

function Card({ title, aside, children, className = "" }: { title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`pf-card ${className}`.trim()} aria-label={title}>
    <header className="pf-card__head"><h2>{title}</h2>{aside && <div className="pf-card__aside">{aside}</div>}</header>
    {children}
  </section>;
}

function Snapshot({ state }: { state: RiskState }) {
  const base = state.base;
  return <Card title="Account snapshot">
    <dl className="pf-ledger">
      <div className="is-strong"><dt>Net liquidation value</dt><dd>{money(state.nav, base)}</dd></div>
      <div><dt>Gross position value</dt><dd>{money(state.gross, base)}</dd></div>
      <div><dt>Net cash</dt><dd className={state.totalCash !== null && state.totalCash < 0 ? "is-negative" : ""}>{money(state.totalCash, base)}</dd></div>
      {state.cash.map(line => <div key={line.currency} className="is-sub"><dt>{line.currency} cash{line.native < 0 ? " (debit)" : ""}</dt>
        <dd className={line.native < 0 ? "is-negative" : ""}>{money(line.native, line.currency)}</dd></div>)}
      <div><dt>Excess liquidity</dt><dd>{money(state.excess, base)}</dd></div>
      <div className="is-pnl"><dt>Unrealized PnL<small>{state.unrealizedSource === "broker" ? "Broker account-wide, open positions" : state.unrealizedSource === "positions" ? "Σ open positions" : "Not reported"}</small></dt>
        <dd className={pnlTone(state.unrealized)}>{signedMoney(state.unrealized, base)}</dd></div>
      <div className="is-pnl"><dt>Realized PnL · YTD<small>Σ instruments still held; closed-out names are on Themes</small></dt>
        <dd className={pnlTone(state.realized)}>{signedMoney(state.realized, base)}</dd></div>
    </dl>
  </Card>;
}

function KeyMetrics({ state, limits }: { state: RiskState; limits: Limits }) {
  const keys: LimitKey[] = ["grossLeverage", "equityLeverage", "initUtil", "maintUtil", "excessLiquidity"];
  const calc = useCalc();
  return <Card title="Key risk metrics" className="pf-card--metrics">
    <div className="pf-tiles">{keys.map(key => {
      const spec = LIMIT_SPECS.find(item => item.key === key)!;
      const value = state.metrics[key];
      const status = statusOf(value, limits[key], spec.direction);
      return <div key={key} className={`pf-tile pf-tile--${status}`}>
        <span className="pf-tile__label">{spec.label}<InfoButton spec={spec} open={calc.open === key} onToggle={() => calc.toggle(key)} /></span>
        <strong className="pf-tile__value">{formatLimit(spec, value)}</strong>
        <span className="pf-tile__foot"><StatusIcon status={status} />Target {comparator(spec)} {formatLimit(spec, limits[key].target)}</span>
        {key === "excessLiquidity" && state.excess !== null && <span className="pf-tile__note">{money(state.excess, state.base, true)}</span>}
      </div>;
    })}</div>
    {calc.open && <CalcPanel calc={explainMetric(state, calc.open)} base={state.base} onClose={calc.close} />}
  </Card>;
}

function Composition({ state }: { state: RiskState }) {
  const priced = state.holdings.filter(holding => holding.value !== null && holding.value !== 0);
  const top = priced.slice(0, 6);
  const rest = priced.slice(6).reduce((sum, holding) => sum + Math.abs(holding.value as number), 0);
  const slices: Slice[] = top.map((holding, index) => ({ key: holding.symbol + index, label: holding.symbol, value: Math.abs(holding.value as number), tone: `var(--series-${index + 1})` }));
  if (rest > 0) slices.push({ key: "other", label: "Other positions", value: rest, tone: "var(--series-7)" });
  if (state.unreconciled !== null && state.unreconciled > 0 && hasReconciliationGap(state)) slices.push({ key: "gap", label: "Unreconciled", value: state.unreconciled, tone: "var(--faint)", note: "Broker gross not explained by listed positions" });
  const total = slices.reduce((sum, slice) => sum + slice.value, 0);
  return <Card title="Composition" aside={<span className="pf-muted">{money(total, state.base, true)}</span>}>
    {total > 0 ? <div className="pf-composition">
      <Donut slices={slices} total={total} center={money(total, undefined, true)} sub={state.metrics.grossLeverage !== null ? `${times(state.metrics.grossLeverage)} NAV` : state.base} />
      <ul className="pf-legend">{slices.map(slice => <li key={slice.key} title={slice.note}>
        <i style={{ background: slice.tone }} aria-hidden="true" /><span>{slice.label}</span>
        <b>{money(slice.value, undefined, true)}</b><em>{pct(slice.value / total)}</em>
      </li>)}</ul>
    </div> : <p className="pf-muted pf-pad">No priced positions in the latest snapshot.</p>}
  </Card>;
}

function LimitBar({ spec, value, limit }: { spec: LimitSpec; value: number | null; limit: Limit }) {
  if (value === null) return <span className="pf-meter pf-meter--empty" />;
  const scale = Math.max(limit.hard, limit.target, value) * (spec.direction === "max" ? 1.15 : 1.6) || 1;
  const at = (v: number) => `${Math.min(100, (v / scale) * 100)}%`;
  const status = statusOf(value, limit, spec.direction);
  return <span className="pf-meter" aria-hidden="true">
    <span className={`pf-meter__fill pf-meter__fill--${status}`} style={{ width: at(value) }} />
    <span className="pf-meter__tick pf-meter__tick--target" style={{ left: at(limit.target) }} />
    <span className="pf-meter__tick pf-meter__tick--hard" style={{ left: at(limit.hard) }} />
  </span>;
}

function LimitsTable({ state, settings, onSave, onReset }: {
  state: RiskState; settings: RiskSettings; onSave: (settings: RiskSettings) => void; onReset: () => void;
}) {
  const [draft, setDraft] = useState<Record<LimitKey, { target: string; hard: string }> | null>(null);
  const [cashDraft, setCashDraft] = useState("");
  const calc = useCalc();
  const display = (spec: LimitSpec, value: number) => String(spec.unit === "pct" ? +(value * 100).toFixed(4) : value);
  function edit() {
    setDraft(Object.fromEntries(LIMIT_SPECS.map(spec => [spec.key, { target: display(spec, settings.limits[spec.key].target), hard: display(spec, settings.limits[spec.key].hard) }])) as Record<LimitKey, { target: string; hard: string }>);
    setCashDraft(settings.cashLike.join(", "));
  }
  const parsed = draft && Object.fromEntries(LIMIT_SPECS.map(spec => {
    const read = (text: string) => { const value = Number(text); return Number.isFinite(value) && text.trim() !== "" && value >= 0 ? (spec.unit === "pct" ? value / 100 : value) : null; };
    return [spec.key, { target: read(draft[spec.key].target), hard: read(draft[spec.key].hard) }];
  })) as Record<LimitKey, { target: number | null; hard: number | null }> | null;
  const problem = parsed && LIMIT_SPECS.find(spec => {
    const { target, hard } = parsed[spec.key];
    return target === null || hard === null || (spec.direction === "max" ? target > hard : target < hard);
  });
  function save() {
    if (!parsed || problem) return;
    const limits = Object.fromEntries(LIMIT_SPECS.map(spec => [spec.key, parsed[spec.key] as Limit])) as Limits;
    onSave({ limits, cashLike: cashDraft.split(/[\s,]+/).map(symbol => symbol.trim().toUpperCase()).filter(Boolean) });
    setDraft(null);
  }
  const editing = draft !== null;
  return <Card title="Risk limits & operating envelope" className="pf-card--limits" aside={editing
    ? <><button type="button" className="pf-btn pf-btn--ghost" onClick={() => { onReset(); setDraft(null); }}>Reset to defaults</button><button type="button" className="pf-btn pf-btn--ghost" onClick={() => setDraft(null)}>Cancel</button><button type="button" className="pf-btn pf-btn--primary" disabled={Boolean(problem)} onClick={save}>Save limits</button></>
    : <button type="button" className="pf-btn" onClick={edit}>Edit limits</button>}>
    <div className="pf-table-scroll"><table className="pf-table pf-limits" aria-label="Risk limits">
      <thead><tr><th scope="col">Metric</th><th scope="col" className="num">Current</th><th scope="col" className="pf-limits__bar-col"><span className="pf-sr">Usage</span></th><th scope="col" className="num pf-col-target">Target</th><th scope="col" className="num pf-col-hard">Hard limit</th><th scope="col">Status</th></tr></thead>
      <tbody>{LIMIT_SPECS.map(spec => {
        const value = state.metrics[spec.key];
        const limit = settings.limits[spec.key];
        const status = statusOf(value, limit, spec.direction);
        const input = (field: "target" | "hard") => <label className="pf-limit-input"><span>{comparator(spec)}</span>
          <input type="number" min="0" step={spec.unit === "pct" ? 1 : 0.05} inputMode="decimal" aria-label={`${spec.label} ${field === "target" ? "operating target" : "hard limit"}`}
            value={draft![spec.key][field]} onChange={event => setDraft({ ...draft!, [spec.key]: { ...draft![spec.key], [field]: event.target.value } })} />
          <span>{spec.unit === "pct" ? "%" : "×"}</span></label>;
        return <tr key={spec.key} className={`is-${status}`}>
          <th scope="row"><span className="pf-limits__name">{spec.label}<InfoButton spec={spec} open={calc.open === spec.key} onToggle={() => calc.toggle(spec.key)} /></span><small>{spec.help}</small></th>
          <td className={`num pf-limits__current pf-ink--${status}`}>{formatLimit(spec, value)}</td>
          <td className="pf-limits__bar-col"><LimitBar spec={spec} value={value} limit={limit} /></td>
          <td className="num pf-col-target">{editing ? input("target") : `${comparator(spec)} ${formatLimit(spec, limit.target)}`}</td>
          <td className="num pf-col-hard">{editing ? input("hard") : `${comparator(spec)} ${formatLimit(spec, limit.hard)}`}</td>
          <td><StatusTag status={status} /></td>
        </tr>;
      })}</tbody>
    </table></div>
    {calc.open && <CalcPanel calc={explainMetric(state, calc.open)} base={state.base} onClose={calc.close} />}
    {editing && <div className="pf-limits__cash"><label><span>Cash-equivalent symbols</span>
      <input value={cashDraft} onChange={event => setCashDraft(event.target.value)} placeholder="BOXX, SGOV" aria-label="Cash-equivalent symbols" /></label>
      <p className="pf-muted">Excluded from equity leverage, single-name concentration and stress shocks.</p></div>}
    {problem && <p className="pf-inline-error" role="alert">{problem.label}: enter non-negative numbers; the target must be {problem.direction === "max" ? "at or below" : "at or above"} the hard limit.</p>}
    <p className="pf-foot-note">Proposed governance limits, not statistically calibrated thresholds. Saved for this account in this browser. Bars show usage with ticks at the target (thin) and hard limit (thick).</p>
  </Card>;
}

function Concentration({ state, limits }: { state: RiskState; limits: Limits }) {
  const nav = state.nav;
  return <Card title="Positions & concentration" aside={<span className="pf-muted">{state.base} · % of net liquidation</span>}>
    {state.holdings.length ? <div className="pf-table-scroll"><table className="pf-table" aria-label="Positions and concentration">
      <thead><tr><th scope="col">Position</th><th scope="col" className="num">Value ({state.base})</th><th scope="col" className="num">% NAV</th><th scope="col" className="num">Beta</th><th scope="col" className="num">Unrealized</th><th scope="col" className="num">Realized YTD</th><th scope="col" className="num">Limit</th><th scope="col">Status</th></tr></thead>
      <tbody>{state.holdings.map((holding, index) => {
        const share = holding.value === null || nav === null || nav <= 0 ? null : Math.abs(holding.value) / nav;
        const limit = holding.cashLike ? limits.cashLike : limits.singleName;
        const status = statusOf(share, limit, "max");
        return <tr key={`${holding.symbol}-${index}`}>
          <th scope="row"><span className="pf-limits__name">{holding.symbol}</span><small>{holding.cashLike ? "Cash equivalent" : holding.currency}{holding.currency && holding.currency !== state.base && holding.native !== null ? ` · ${money(holding.native, holding.currency, true)}` : ""}</small></th>
          <td className="num">{money(holding.value)}</td>
          <td className={`num pf-ink--${status}`}>{pct(share)}</td>
          <td className="num">{holding.beta === null ? "…" : holding.beta.toFixed(2)}{holding.betaSource === "assumed" && <small>assumed</small>}</td>
          <td className={`num ${pnlTone(holding.unrealized)}`}>{signedMoney(holding.unrealized)}</td>
          <td className={`num ${pnlTone(holding.realized)}`}>{signedMoney(holding.realized)}</td>
          <td className="num pf-muted">≤ {holding.cashLike ? times(limit.target) : pct(limit.target, 0)}</td>
          <td><StatusTag status={status}>{status === "red" ? "Trim" : status === "amber" ? "Above target" : status === "unknown" ? "No FX rate" : "OK"}</StatusTag></td>
        </tr>;
      })}</tbody>
    </table></div> : <p className="pf-muted pf-pad">No open positions in the latest snapshot.</p>}
  </Card>;
}

const LEVERAGE_KEYS: LimitKey[] = ["grossLeverage", "equityLeverage", "netLeverage", "betaLeverage", "borrowing"];

function Leverage({ state, limits, beta }: { state: RiskState; limits: Limits; beta: BetaResult }) {
  const base = state.base;
  const parts = [
    { label: "Cash equivalents", value: Math.abs(state.cashLikeValue), tone: "var(--series-1)" },
    { label: "Equities", value: state.equityExposure, tone: "var(--series-2)" },
    ...(hasReconciliationGap(state) && state.unreconciled !== null && state.unreconciled > 0 ? [{ label: "Unreconciled", value: state.unreconciled, tone: "var(--faint)" }] : []),
  ].filter(part => part.value > 0);
  const total = parts.reduce((sum, part) => sum + part.value, 0);
  const measured = state.holdings.filter(holding => holding.betaSource === "measured");
  const calc = useCalc();
  return <Card title="Leverage" className="pf-card--leverage" aside={<span className="pf-muted">× net liquidation ({money(state.nav, base, true)})</span>}>
    <div className="pf-leverage">
      <ul className="pf-leverage__ladder">{LEVERAGE_KEYS.map(key => {
        const spec = LIMIT_SPECS.find(item => item.key === key)!;
        const value = state.metrics[key];
        const status = statusOf(value, limits[key], spec.direction);
        const loading = key === "betaLeverage" && beta.betas === null;
        return <li key={key}>
          <div className="pf-leverage__head"><span className="pf-leverage__label">{spec.label}<InfoButton spec={spec} open={calc.open === key} onToggle={() => calc.toggle(key)} /></span><strong className={`pf-ink--${loading ? "unknown" : status}`}>{loading ? "…" : times(value)}</strong></div>
          <LimitBar spec={spec} value={loading ? null : value} limit={limits[key]} />
          <div className="pf-leverage__foot"><small>{spec.help}</small><small>Target {times(limits[key].target, 2)} · hard {times(limits[key].hard, 2)}</small></div>
        </li>;
      })}</ul>
      {calc.open && <div className="pf-leverage__calc"><CalcPanel calc={explainMetric(state, calc.open)} base={state.base} onClose={calc.close} /></div>}
      <div className="pf-leverage__side"><section>
        <h3>What makes up gross exposure</h3>
        {total > 0 ? <>
          <div className="pf-stackbar" role="img" aria-label={parts.map(part => `${part.label} ${money(part.value, base)}`).join(", ")}>
            {parts.map(part => <span key={part.label} style={{ width: `${(part.value / total) * 100}%`, background: part.tone }} title={`${part.label}: ${money(part.value, base)}`} />)}
          </div>
          <ul className="pf-legend">{parts.map(part => <li key={part.label}><i style={{ background: part.tone }} aria-hidden="true" /><span>{part.label}</span><b>{money(part.value, undefined, true)}</b><em>{times(state.nav ? part.value / state.nav : null)}</em></li>)}</ul>
        </> : <p className="pf-muted">No priced positions.</p>}
      </section><section>
        <h3>Betas</h3>
        <p className="pf-muted">{beta.betas === null ? `Measuring 1-year daily betas against ${BENCHMARK}…`
          : beta.problem ?? `1-year daily betas against ${BENCHMARK}${measured.length ? `, measured for ${measured.map(holding => `${holding.symbol} ${holding.beta?.toFixed(2)}`).join(", ")}` : ""}.`}
          {beta.betas !== null && state.assumedBetas.length > 0 && !beta.problem && ` No usable price history for ${state.assumedBetas.join(", ")}; assumed 1.0.`}
          {" "}Cash equivalents count as 0.</p>
      </section></div>
    </div>
  </Card>;
}

function CapacityCard({ state, limits }: { state: RiskState; limits: Limits }) {
  const capacity = deployableCapacity(state, limits);
  const binding = capacity.binding;
  const over = binding && binding.amount !== null && binding.amount < 0;
  const toTarget = contributionToReach(state, limits.grossLeverage.target);
  return <Card title="Deployable capacity" className="pf-card--capacity">
    <div className={`pf-capacity ${over ? "is-over" : ""}`}>
      <span className="pf-capacity__label">Additional investment permitted</span>
      <strong>{capacity.amount === null ? "—" : money(capacity.amount, state.base)}</strong>
      <span className="pf-capacity__binding">{binding ? <>Binding constraint: <b>{binding.label}</b>{over && <> · over target by {money(-(binding.amount as number), state.base)}</>}</> : "Not enough data to size capacity"}</span>
    </div>
    <ul className="pf-headroom">{capacity.constraints.map(item => <li key={item.key} className={item === binding ? "is-binding" : ""}>
      <span>{item.label}<small>{item.basis}</small></span>
      <b className={item.amount !== null && item.amount < 0 ? "is-negative" : ""}>{item.amount === null ? "—" : money(item.amount, undefined, true)}</b>
    </li>)}</ul>
    {toTarget !== null && toTarget > 0 && <p className="pf-foot-note">Cash needed to return to {times(limits.grossLeverage.target, 1)} gross with exposure unchanged: <b>{money(toTarget, state.base)}</b>.</p>}
    <p className="pf-foot-note">Against operating targets; purchases assumed borrowed, margin at the account's current average rate. Single-name limits apply per position.</p>
  </Card>;
}

function OperatingRange({ state, limits }: { state: RiskState; limits: Limits }) {
  const nav = state.nav;
  const exposure = state.gross;
  if (nav === null || nav <= 0) return null;
  const target = limits.grossLeverage.target * nav, hard = limits.grossLeverage.hard * nav;
  const scale = Math.max(hard, exposure ?? 0) * 1.1;
  const at = (value: number) => `${Math.min(100, (value / scale) * 100)}%`;
  return <Card title="Operating range" aside={<span className="pf-muted">Gross exposure</span>}>
    <div className="pf-range">
      <div className="pf-range__box pf-range__box--good"><span>Operating</span><strong>≤ {money(target, state.base, true)}</strong><small>{times(limits.grossLeverage.target, 1)} NAV</small></div>
      <div className="pf-range__box pf-range__box--bad"><span>Hard cap</span><strong>≤ {money(hard, state.base, true)}</strong><small>{times(limits.grossLeverage.hard, 1)} NAV</small></div>
    </div>
    <div className="pf-range__track" role="img" aria-label={`Current gross ${money(exposure, state.base)} against operating ${money(target, state.base)} and hard cap ${money(hard, state.base)}`}>
      <span className="pf-range__zone pf-range__zone--good" style={{ width: at(target) }} />
      <span className="pf-range__zone pf-range__zone--warn" style={{ left: at(target), width: `calc(${at(hard)} - ${at(target)})` }} />
      {exposure !== null && <span className="pf-range__marker" style={{ left: at(exposure) }}><b>Now {money(exposure, undefined, true)}</b></span>}
    </div>
  </Card>;
}

const STRESS_LABEL = { cushion: ["green", "Within cushion"], pressure: ["amber", "Near margin pressure"], breach: ["red", "Breaches hard limit"], call: ["red", "Margin call / forced deleveraging"] } as const;

function StressTable({ state, limits }: { state: RiskState; limits: Limits }) {
  const rows = stressTests(state, limits);
  return <Card title="Stress tests" aside={<span className="pf-muted">Instantaneous, approximate</span>} className="pf-card--stress">
    {rows.length ? <div className="pf-table-scroll"><table className="pf-table" aria-label="Stress tests">
      <thead><tr><th scope="col">Scenario</th><th scope="col" className="num">NAV impact</th><th scope="col" className="num">Gross after</th><th scope="col" className="num">Excess liq. after</th><th scope="col">Result</th></tr></thead>
      <tbody>{rows.map(row => {
        const [status, label] = STRESS_LABEL[row.result];
        return <tr key={row.id}>
          <th scope="row"><span className="pf-limits__name">{row.label}</span>{row.firstBreach && <small>First breach: {row.firstBreach}</small>}</th>
          <td className="num"><span className="is-negative">{signedPct(row.navImpactPct)}</span><small>{money(row.navImpact, undefined, true)}</small></td>
          <td className="num">{times(row.grossLeverageAfter)}</td>
          <td className="num">{row.excessAfter === null ? "—" : <>{money(row.excessAfter, undefined, true)}<small>{pct(row.excessRatioAfter)} NAV</small></>}</td>
          <td><StatusTag status={status}>{label}</StatusTag></td>
        </tr>;
      })}</tbody>
    </table></div> : <p className="pf-muted pf-pad">Net liquidation is needed to run stress tests.</p>}
    <p className="pf-foot-note">Market scenarios move each position by its beta × the market move; cash equivalents held flat; maintenance margin scales with each shocked position. Ignores FX, volatility and margin-rate changes.</p>
  </Card>;
}

function Triggers({ state, limits }: { state: RiskState; limits: Limits }) {
  const gross = state.metrics.grossLeverage, excess = state.metrics.excessLiquidity, single = state.metrics.singleName;
  const rules = [
    { status: "green" as Status, when: `Gross ≤ ${times(limits.grossLeverage.target, 1)}`, then: "Maintain; consider selective adds.", active: gross !== null && gross <= limits.grossLeverage.target },
    { status: "amber" as Status, when: `Gross ${times(limits.grossLeverage.target, 1)} – ${times(limits.grossLeverage.hard, 1)}`, then: "Do not add risk; prioritise deleveraging.", active: gross !== null && gross > limits.grossLeverage.target && gross <= limits.grossLeverage.hard },
    { status: "red" as Status, when: `Gross > ${times(limits.grossLeverage.hard, 1)} or excess liquidity < ${pct(limits.excessLiquidity.hard, 0)}`, then: "Delever now: reduce positions and/or repay borrowing.", active: (gross !== null && gross > limits.grossLeverage.hard) || (excess !== null && excess < limits.excessLiquidity.hard) },
    { status: "amber" as Status, when: `any single name > ${pct(limits.singleName.hard, 0)} of NAV`, then: `Trim to ≤ ${pct(limits.singleName.target, 0)}.`, active: single !== null && single > limits.singleName.hard },
  ];
  return <Card title="Actions & triggers">
    <ul className="pf-triggers">{rules.map(rule => <li key={rule.when} className={rule.active ? "is-active" : ""}>
      <StatusIcon status={rule.status} /><div><b>If {rule.when}</b><span>→ {rule.then}</span></div>
      {rule.active && <span className="pf-pill">Now</span>}
    </li>)}</ul>
  </Card>;
}

/* ---------- page ---------- */

function RiskBoard({ account, userId }: { account: BrokerAccount; userId: string }) {
  const base = `${API}/accounts/${encodeURIComponent(account.broker_code)}/${encodeURIComponent(account.account_id)}`;
  const filter = `user_id=${encodeURIComponent(userId)}`;
  const summary = useRemote<AccountSummary>(`${base}/summary?${filter}`, 60_000);
  const positions = useRemote<SnapshotPage>(`${base}/positions?${filter}`, 60_000);
  const [settings, setSettings] = useState<RiskSettings>(() => loadSettings(account));
  const currency = account.base_currency ?? "";
  const balances = summary.data?.balances;
  const listingIds = (positions.data?.items ?? []).map(row => (row.listing_id ? String(row.listing_id) : "")).filter(Boolean);
  const beta = useBetas(listingIds);
  const state = useMemo(() => computeRisk(currency, balances?.items ?? [], positions.data?.items ?? [], settings.cashLike, beta.betas),
    [currency, balances, positions.data, settings.cashLike, beta.betas]);

  if ((summary.loading && !summary.data) || (positions.loading && !positions.data)) return <p className="pf-muted pf-pad" role="status">Loading risk snapshot…</p>;
  if (!summary.data || !positions.data) return <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(summary.error ?? positions.error, "The broker snapshot is unavailable.")}</p>;
  if (!currency) return <EmptyState title="This account has no base currency.">Risk is computed in the account's base currency, so it cannot be shown.</EmptyState>;

  const stale = summary.data.balances.freshness !== "fresh" || positions.data.freshness !== "fresh";
  const verdict = overallStatus(state, settings.limits, stale);
  const snapshotTime = summary.data.balances.snapshot_time;
  const gap = hasReconciliationGap(state);
  return <div className="pf-risk">
    <section className={`pf-verdict pf-verdict--${verdict.status}`} aria-label="Risk status">
      <div className="pf-verdict__main"><span className="pf-verdict__label">Risk status</span><strong><StatusIcon status={verdict.status} />{VERDICT_LABEL[verdict.status]}</strong></div>
      <ul>{verdict.reasons.slice(0, 4).map(reason => <li key={reason}>{reason}</li>)}</ul>
      <span className={`pf-verdict__time${stale ? " is-stale" : ""}`}>{snapshotTime ? `${stale ? "Stale · " : ""}Snapshot ${formatTime(snapshotTime)}` : "No snapshot reported"}{account.simulated ? " · Simulated" : ""}</span>
    </section>
    {Boolean(summary.error || positions.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">Refresh failed. Showing the last successful snapshot.</p>}
    {gap && state.unreconciled !== null && <p className="pf-notice pf-notice--warn" role="status"><b>Unreconciled exposure.</b> The broker reports {money(state.gross, currency)} gross position value, but the listed positions total {money(state.positionsGross, currency)} — a gap of {money(state.unreconciled, currency)}. Position-level figures exclude it until it is explained.</p>}
    {state.unconverted.length > 0 && <p className="pf-notice pf-notice--warn" role="status"><b>Missing FX rates.</b> No {state.unconverted.join(", ")} → {currency} rate in this snapshot; equity leverage and concentration are unavailable.</p>}

    <div className="pf-grid pf-grid--top">
      <Snapshot state={state} />
      <KeyMetrics state={state} limits={settings.limits} />
      <Composition state={state} />
    </div>
    <Leverage state={state} limits={settings.limits} beta={beta} />
    <div className="pf-grid pf-grid--mid">
      <LimitsTable state={state} settings={settings} onSave={next => { setSettings(next); saveSettings(account, next); }}
        onReset={() => { const next = { limits: DEFAULT_LIMITS, cashLike: DEFAULT_CASH_LIKE }; setSettings(next); saveSettings(account, null); }} />
      <div className="pf-stack"><Concentration state={state} limits={settings.limits} /><StressTable state={state} limits={settings.limits} /></div>
    </div>
    <div className="pf-grid pf-grid--bottom">
      <CapacityCard state={state} limits={settings.limits} />
      <div className="pf-stack"><OperatingRange state={state} limits={settings.limits} /><Triggers state={state} limits={settings.limits} /></div>
    </div>
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
      {account ? <RiskBoard key={JSON.stringify([userId, account.broker_code, account.account_id])} account={account} userId={userId} />
        : !remote.error && <EmptyState title="No accounts registered for this profile.">Choose another profile to see its risk.</EmptyState>}
    </>}
  </>;
}

export function Risk() {
  const remote = useRemote<ItemsPage<Profile>>(`${API}/profiles`, 60_000);
  const [userId, setUserId] = useState<string | null>(() => new URLSearchParams(window.location.search).get("user_id"));
  const [account, setAccount] = useState(() => new URLSearchParams(window.location.search).get("account") ?? "");
  const profiles = remote.data?.items ?? [];
  const selected = userId ?? profiles.find(profile => profile.active)?.user_id ?? profiles[0]?.user_id ?? "";
  return <main className="portfolio-page pf-risk-page">
    <PortfolioHeader title="Risk" description="Leverage, margin, liquidity and concentration against your limits — and how much more risk the account can take.">
      <ProfileSelect profiles={profiles} value={selected} disabled={remote.loading && !remote.data} all onChange={value => { setUserId(value); setAccount(""); setPortfolioQuery(value); }} />
    </PortfolioHeader>
    {Boolean(remote.error) && <p className="portfolio-notice portfolio-notice--error" role="alert">{errorMessage(remote.error, "Profiles are unavailable.")}</p>}
    {remote.loading && !remote.data ? <p className="pf-muted pf-pad" role="status">Loading profiles…</p>
      : <AccountPicker key={selected} userId={selected} selected={account} onSelect={value => { setAccount(value); setPortfolioQuery(selected, value); }} />}
  </main>;
}
