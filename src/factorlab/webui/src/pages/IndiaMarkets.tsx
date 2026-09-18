import { FormEvent, useEffect, useMemo, useState } from "react";

import { IndiaCheckPill } from "../components/india/IndiaCheckPill";
import { IndiaInstrumentTable } from "../components/india/IndiaInstrumentTable";
import { IndiaSummaryCards } from "../components/india/IndiaSummaryCards";
import { IndiaUniverseTabs } from "../components/india/IndiaUniverseTabs";
import type {
  IndiaCheckStatus,
  IndiaCollectionScope,
  IndiaDashboardData,
  IndiaInstrumentPage,
} from "../indiaTypes";

const PAGE_SIZE = 100;

function indiaToday(): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Kolkata",
  }).formatToParts(new Date());
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${value.year}-${value.month}-${value.day}`;
}

function formatTime(value: string | null): string {
  if (!value) return "Never";
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Kolkata",
    timeZoneName: "short",
  }).format(new Date(value));
}

async function fetchJson<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`India Markets request failed (${response.status})`);
  return (await response.json()) as T;
}

function dashboardStatus(dashboard: IndiaDashboardData): IndiaCheckStatus {
  if (dashboard.market_status === "pre_open" || dashboard.market_status === "non_trading_day") {
    return "not_expected";
  }
  if (dashboard.expected_data_points > 0 && dashboard.data_points === 0) return "missing";
  if (dashboard.anomaly_count > 0 || dashboard.coverage_percent < 99) return "attention";
  return "healthy";
}

export function IndiaMarkets() {
  const [selectedDate, setSelectedDate] = useState(indiaToday);
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [collectionScope, setCollectionScope] = useState<IndiaCollectionScope>("all");
  const [statusFilter, setStatusFilter] = useState<"all" | IndiaCheckStatus>("all");
  const [offset, setOffset] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);
  const [dashboard, setDashboard] = useState<IndiaDashboardData | null>(null);
  const [instrumentPage, setInstrumentPage] = useState<IndiaInstrumentPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({
      trading_date: selectedDate,
      scope: collectionScope,
      limit: String(PAGE_SIZE),
      offset: String(offset),
    });
    if (search) params.set("search", search);
    setLoading(true);
    void Promise.all([
      fetchJson<IndiaDashboardData>(
        `/hub/api/v1/india/dashboard?trading_date=${selectedDate}`,
        controller.signal,
      ),
      fetchJson<IndiaInstrumentPage>(
        `/hub/api/v1/india/instruments?${params.toString()}`,
        controller.signal,
      ),
    ])
      .then(([nextDashboard, nextInstruments]) => {
        setDashboard(nextDashboard);
        setInstrumentPage(nextInstruments);
        setError(null);
      })
      .catch((requestError: Error) => {
        if (requestError.name !== "AbortError") {
          setError("India market checks are temporarily unavailable. The last successful result remains visible.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [selectedDate, collectionScope, search, offset, refreshKey]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      if (!document.hidden && selectedDate === indiaToday()) setRefreshKey((value) => value + 1);
    }, 60_000);
    return () => window.clearInterval(interval);
  }, [selectedDate]);

  const checkCounts = useMemo(() => {
    const counts: Record<IndiaCheckStatus, number> = {
      healthy: 0,
      attention: 0,
      missing: 0,
      not_expected: 0,
    };
    for (const item of instrumentPage?.items ?? []) counts[item.check_status] += 1;
    return counts;
  }, [instrumentPage]);

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    setOffset(0);
    setSearch(searchDraft.trim());
  }

  if (loading && !dashboard) {
    return (
      <main className="loading-state" aria-live="polite">
        <span className="loading-state__pulse" />
        <p>Checking India market data…</p>
      </main>
    );
  }

  if (!dashboard || !instrumentPage) {
    return (
      <main className="empty-state">
        <span className="eyebrow">India Markets unavailable</span>
        <h1>The market explorer could not load.</h1>
        <p>{error}</p>
        <button type="button" onClick={() => setRefreshKey((value) => value + 1)}>Try again</button>
      </main>
    );
  }

  const overallStatus = dashboardStatus(dashboard);
  return (
    <main>
      <section className="hero india-hero">
        <div>
          <span className="eyebrow">India markets · one-minute data</span>
          <h1>Inspect every instrument. Verify every session.</h1>
          <p>Unique instruments, expected coverage, value integrity, and session-by-session history from ClickHouse.</p>
        </div>
        <div className="india-date-control">
          <label htmlFor="india-trading-date">Trading date</label>
          <input
            id="india-trading-date"
            type="date"
            max={indiaToday()}
            value={selectedDate}
            onChange={(event) => {
              setSelectedDate(event.target.value);
              setOffset(0);
            }}
          />
          <button type="button" onClick={() => setRefreshKey((value) => value + 1)} disabled={loading}>
            {loading ? "Checking…" : "Run checks"}
          </button>
        </div>
      </section>

      {error && <div className="notice notice--error" role="status">{error}</div>}
      <IndiaSummaryCards dashboard={dashboard} instruments={instrumentPage.items} />

      <section className="india-check-banner" aria-label="Selected day health">
        <div>
          <span className="eyebrow">Selected-day verdict</span>
          <h2>{selectedDate} · {dashboard.market_status.replaceAll("_", " ")}</h2>
          <p>
            {overallStatus === "healthy"
              ? "Expected coverage and automated anomaly checks passed."
              : `${dashboard.anomaly_count} anomaly finding(s); inspect instruments marked Review or Missing.`}
          </p>
        </div>
        <div className="india-check-banner__meta">
          <IndiaCheckPill status={overallStatus} />
          <span>Latest ingest {formatTime(dashboard.last_ingested_at)}</span>
          <span>{dashboard.backfilled_on_date.toLocaleString("en-IN")} rows backfilled on this date</span>
        </div>
      </section>

      <section className="inventory india-inventory" aria-labelledby="india-instruments-title">
        <div className="section-heading">
          <div>
            <span className="eyebrow">Instrument inventory</span>
            <h2 id="india-instruments-title">India instrument universe</h2>
          </div>
          <span>{instrumentPage.total.toLocaleString("en-IN")} results in this view</span>
        </div>
        <IndiaUniverseTabs
          page={instrumentPage}
          selected={collectionScope}
          onSelect={(scope) => {
            setCollectionScope(scope);
            setStatusFilter("all");
            setOffset(0);
          }}
        />
        <div className="india-tools">
          <form onSubmit={submitSearch} role="search">
            <label>
              <span className="sr-only">Search India instruments</span>
              <input
                value={searchDraft}
                onChange={(event) => setSearchDraft(event.target.value)}
                placeholder="Search symbol, company, or instrument key"
              />
            </label>
            <button type="submit">Search</button>
            {search && (
              <button
                className="button-secondary"
                type="button"
                onClick={() => {
                  setSearchDraft("");
                  setSearch("");
                  setOffset(0);
                }}
              >
                Clear
              </button>
            )}
          </form>
          <label className="status-filter">
            <span>Check result</span>
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as "all" | IndiaCheckStatus)}
            >
              <option value="all">All ({instrumentPage.items.length})</option>
              <option value="healthy">Passed ({checkCounts.healthy})</option>
              <option value="attention">Review ({checkCounts.attention})</option>
              <option value="missing">Missing ({checkCounts.missing})</option>
              <option value="not_expected">Not expected ({checkCounts.not_expected})</option>
            </select>
          </label>
        </div>

        <IndiaInstrumentTable
          instruments={instrumentPage.items}
          statusFilter={statusFilter}
          tradingDate={selectedDate}
        />
        <div className="pagination" aria-label="Instrument pages">
          <button
            className="button-secondary"
            type="button"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(offset - PAGE_SIZE, 0))}
          >
            Previous
          </button>
          <span>
            {instrumentPage.total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE_SIZE, instrumentPage.total)} of {instrumentPage.total}
          </span>
          <button
            className="button-secondary"
            type="button"
            disabled={offset + PAGE_SIZE >= instrumentPage.total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Next
          </button>
        </div>
      </section>

      <footer>FactorLab · India Markets · Exchange sessions use Asia/Kolkata and the XBOM calendar</footer>
    </main>
  );
}
