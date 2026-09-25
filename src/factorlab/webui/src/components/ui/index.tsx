import { useId, type KeyboardEvent, type ReactNode } from "react";

import { formatCompact, formatDate } from "../../shared/format";

export type HubStatus = "healthy" | "attention" | "missing" | "not_expected" | "not_configured" | "unknown";

export const statusLabels: Record<HubStatus, string> = {
  healthy: "Healthy",
  attention: "Needs attention",
  missing: "Missing",
  not_expected: "No daily rule",
  not_configured: "No producer yet",
  unknown: "Unknown",
};

export function StatusPill({ status, label }: { status: HubStatus; label?: string }) {
  return (
    <span className={`status status--${status}`}>
      <span className="status__dot" aria-hidden="true" />
      {label ?? statusLabels[status]}
    </span>
  );
}

export function MetricCard({ label, value, detail }: { label: string; value: ReactNode; detail?: ReactNode }) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </article>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="data-empty">
      <strong>{title}</strong>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function ErrorBanner({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  return (
    <div className="notice notice--error data-error" role="alert">
      <span>{children}</span>
      {onRetry && <button type="button" className="button-secondary" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="data-skeleton" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => <span key={index} />)}
    </div>
  );
}

export interface TabItem<T extends string> { id: T; label: string; hint?: string; disabled?: boolean }

/** ARIA tablist with arrow-key navigation. */
export function Tabs<T extends string>({ tabs, active, onChange, label }: {
  tabs: TabItem<T>[]; active: T; onChange: (tab: T) => void; label: string;
}) {
  const prefix = useId();
  const enabled = tabs.filter((tab) => !tab.disabled);
  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const index = enabled.findIndex((tab) => tab.id === active);
    const next = enabled[(index + (event.key === "ArrowRight" ? 1 : enabled.length - 1)) % enabled.length];
    onChange(next.id);
    document.getElementById(`${prefix}-${next.id}`)?.focus();
  }
  return (
    <div className="data-tabs" role="tablist" aria-label={label}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          id={`${prefix}-${tab.id}`}
          type="button"
          role="tab"
          aria-selected={tab.id === active}
          tabIndex={tab.id === active ? 0 : -1}
          disabled={tab.disabled}
          className={tab.id === active ? "is-active" : ""}
          onClick={() => onChange(tab.id)}
          onKeyDown={onKeyDown}
        >
          {tab.label}
          {tab.hint && <small>{tab.hint}</small>}
        </button>
      ))}
    </div>
  );
}

export interface MarketOption { code: string; label: string; detail?: string }

/** All / India / US switch; an empty code means every market. */
export function MarketSwitch({ options, value, onChange, label = "Market" }: {
  options: MarketOption[]; value: string; onChange: (code: string) => void; label?: string;
}) {
  return (
    <div className="market-switch" role="group" aria-label={label}>
      <span className="market-switch__label">{label}</span>
      {[{ code: "", label: "All markets" }, ...options].map((option) => (
        <button key={option.code || "all"} type="button" aria-pressed={value === option.code}
          className={value === option.code ? "is-active" : ""} onClick={() => onChange(option.code)}>
          {option.label}
          {"detail" in option && option.detail && <small>{option.detail}</small>}
        </button>
      ))}
    </div>
  );
}

export interface Bar { label: string; value: number }

/** Accessible bar chart: bars for the eye, a caption and titles for exact values. */
export function BarChart({ bars, caption, unit = "rows" }: { bars: Bar[]; caption: string; unit?: string }) {
  const max = Math.max(...bars.map((bar) => bar.value), 1);
  const total = bars.reduce((sum, bar) => sum + bar.value, 0);
  const step = Math.max(1, Math.ceil(bars.length / 8));
  return (
    <figure className="bar-chart">
      <figcaption>
        <span>{caption}</span>
        <small>{formatCompact(total)} {unit} total · peak {formatCompact(max)}</small>
      </figcaption>
      <div className="bar-chart__plot" role="img" aria-label={`${caption}: ${bars.length} periods, ${total} ${unit} in total`}>
        {bars.map((bar, index) => (
          <div className="bar-chart__column" key={bar.label} title={`${formatDate(bar.label)}: ${bar.value.toLocaleString("en-IN")} ${unit}`}>
            <span className="bar-chart__bar" style={{ height: `${Math.max((bar.value / max) * 100, bar.value ? 2 : 0)}%` }} />
            <time>{index % step === 0 ? formatDate(bar.label).replace(/ \d{4}$/, "") : ""}</time>
          </div>
        ))}
      </div>
    </figure>
  );
}
