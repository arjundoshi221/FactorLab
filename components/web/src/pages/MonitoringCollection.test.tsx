import { render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MonitoringCollection, type CollectionPipeline, type CollectionPlanResponse } from "./MonitoringCollection";

afterEach(() => vi.unstubAllGlobals());

const pipeline = (overrides: Partial<CollectionPipeline>): CollectionPipeline => ({
  id: "us_live", label: "US live bars", schedule: "A sweep every 5 minutes during the US regular session.",
  scheduled: true, per_symbol_runs: true, declared_universe: "us_liquid_250", resolution: "1min",
  expected_interval_seconds: 900, status: "healthy", status_reason: "Runs are arriving on schedule.",
  last_status: "success", last_started_at: "2026-10-30T14:55:00Z", runs_24h: 19500,
  universe: "us_liquid_250", universes_7d: ["us_liquid_250"], requested_series: 2,
  config: { minute_tier_size: 250, live_interval_s: 300 }, median_gap_seconds: null,
  members: [{ resolution: "1min", source: "schwab", active_series: 250, contracts: 0 }], flags: [],
  ...overrides,
});

const plan: CollectionPlanResponse = {
  generated_at: "2026-10-30T15:00:00Z", unavailable: [],
  components: [
    { component: "ingest-us", services: [{ service: "ingest-us", pipelines: [
      pipeline({}),
      pipeline({ id: "us_recovery_1min", label: "US minute history", universe: "pilot", config: null, flags: ["universe_mismatch", "config_unreported"], members: [] }),
    ] }] },
    { component: "ingest-us-futures", services: [{ service: "futures-us", pipelines: [
      pipeline({ id: "us_futures_quotes", label: "US futures quotes", per_symbol_runs: false, universe: "us_futures", declared_universe: "us_futures", resolution: null, median_gap_seconds: 60, runs_24h: 1380, config: { roots: ["/ES", "/CL"], interval_s: 60 }, members: [] }),
    ] }] },
  ],
};

it("shows each service's universe, members, cadence and reported config", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => plan })));
  render(<MonitoringCollection refreshKey={0} />);
  const live = within((await screen.findByText("US live bars")).closest("tr")!);
  expect(live.getByText("us_liquid_250")).toBeInTheDocument();
  expect(live.getByText("1min 250")).toBeInTheDocument();
  expect(live.getByText("minute_tier_size: 250")).toBeInTheDocument();
  expect(live.getByText("19,500 symbol runs / 24h")).toBeInTheDocument();
  const futures = within(screen.getByText("US futures quotes").closest("tr")!);
  expect(futures.getByText("roots: /ES, /CL")).toBeInTheDocument();
  expect(futures.getByText("observed every 60s · 1380 runs / 24h")).toBeInTheDocument();
});

it("flags drift from the declared plan", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => plan })));
  render(<MonitoringCollection refreshKey={0} />);
  const minute = within((await screen.findByText("US minute history")).closest("tr")!);
  expect(minute.getByText("pilot")).toBeInTheDocument();
  expect(minute.getByText("declared us_liquid_250", { exact: false })).toBeInTheDocument();
  expect(minute.getByText("Universe differs from declared")).toBeInTheDocument();
  expect(minute.getByText("Config not reported yet")).toBeInTheDocument();
});

it("says which observations are missing instead of showing a clean plan", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ ...plan, unavailable: ["membership"] }) })));
  render(<MonitoringCollection refreshKey={0} />);
  expect(await screen.findByText(/Incomplete: membership could not be read/)).toBeInTheDocument();
});

it("reports a failed load", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, json: async () => ({}) })));
  render(<MonitoringCollection refreshKey={0} />);
  expect(await screen.findByText(/Collection plan refresh failed/)).toBeInTheDocument();
});
