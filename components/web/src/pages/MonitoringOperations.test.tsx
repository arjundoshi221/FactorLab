import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DailyStrip, MonitoringOperations } from "./MonitoringOperations";
import { Monitoring } from "./Monitoring";

afterEach(() => vi.unstubAllGlobals());

it("flags a failed universe source even when no failed run was recorded", () => {
  render(<MonitoringOperations coverage={{data: {generated_at: "2026-10-15T12:00:00Z", feeds: []}, error: false}}
    pipelines={{data: {generated_at: "2026-10-15T12:00:00Z", pipelines: [], sources: [{
      country_code: "US", source: "universe", status: "error", detail: "Universe membership validation failed",
      checked_at: "2026-10-15T11:59:00Z",
    }]}, error: false}} />);
  expect(screen.getByText("Universe membership validation failed")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("universe");
});

it("does not label universe collection healthy just because the process reports on time", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ok: true, json: async () =>
    url.includes("/services") ? {monitor_offline: false, services: [{service_id: "universe-us", status: "up",
      registered: true, last_seen_age_s: 1, uptime_s: 86400, err_count_1h: 0, warn_count_1h: 0}]}
    : url.includes("/pipelines") ? {generated_at: "2026-10-15T12:00:00Z", pipelines: [], sources: [{
      country_code: "US", source: "universe", status: "error", detail: "Universe membership validation failed", checked_at: "2026-10-15T11:59:00Z"}]}
    : url.includes("/coverage") ? {generated_at: "2026-10-15T12:00:00Z", feeds: []}
    : url.includes("/daily") ? {generated_at: "2026-10-15T12:00:00Z", days: []}
    : url.includes("/errors") ? {generated_at: "2026-10-15T12:00:00Z", since: "2026-10-08T12:00:00Z", issues: [], unavailable: [], has_more: false}
    : {events: []},
  })));
  render(<Monitoring />);
  const card = await screen.findByRole("button", {name: "View events for universe-us"});
  expect(within(card).getByText("Collection needs attention")).toBeInTheDocument();
  expect(within(card).queryByText("Healthy")).not.toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("Universe membership validation failed");
  fireEvent.click(screen.getByLabelText("Needs attention only"));
  expect(card).toBeInTheDocument();
});

it("distinguishes no telemetry from a reported day without errors", () => {
  render(<DailyStrip generatedAt="2026-10-15T12:00:00Z" reports={[
    {service_id: "api", day: "2026-10-15", reports: 10, errors: 3, warnings: 0, restarts: 1},
    {service_id: "api", day: "2026-10-14", reports: 20, errors: 0, warnings: 0, restarts: 0},
  ]} />);
  expect(screen.getByLabelText("2026-10-13: No reports stored")).toHaveClass("monitoring-day--unknown");
  expect(screen.getByLabelText(/2026-10-14: 20 reports/)).toHaveClass("monitoring-day--up");
  expect(screen.getByLabelText(/2026-10-15: 10 reports, 3 errors/)).toHaveClass("monitoring-day--down");
});

it("shows coverage gaps and pipeline failures while keeping service health independent", async () => {
  let coverageFailed = false;
  const fetchMock = vi.fn(async (url: string) => {
    if (url.includes("/coverage") && coverageFailed) throw new Error("Offline");
    const data = url.includes("/services") ? { monitor_offline: false, services: [
      {service_id: "api", status: "up", registered: true, last_seen_age_s: 1, uptime_s: 30, err_count_1h: 2, warn_count_1h: 0},
      {service_id: "monitor", status: "up", registered: true, last_seen_age_s: 1, uptime_s: 30, err_count_1h: 0, warn_count_1h: 0},
    ]} : url.includes("/daily") ? {generated_at: "2026-10-15T12:00:00Z", days: []}
      : url.includes("/coverage") ? {generated_at: "2026-10-15T12:00:00Z", feeds: [{
        country_code: "US", source: "schwab", resolution: "1min", trade_date: "2026-10-14", checked_series: 10,
        expected: 100, actual: 80, missing: 20, affected_series: 2, checked_at: "2026-10-15T11:00:00Z", active_series: 12, unreported_series: 2,
      }]}
      : url.includes("/pipelines") ? {generated_at: "2026-10-15T12:00:00Z", pipelines: [{
        id: "us_live", label: "US live bars", market: "US", source: "Schwab", status: "attention", status_reason: "Last run failed",
        schedule: "Every 5 minutes", runs_24h: 10, problem_24h: 2, rows_24h: 80, days: [], recent_problems: [{
          run_id: "one", status: "failed", started_at: "2026-10-15T10:00:00Z", error: "Provider rejected request", failed_series: 2, requested_series: 10,
        }],
      }], sources: []} : {events: []};
    return {ok: true, json: async () => data};
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<Monitoring />);
  expect(await screen.findByText("80.0%")).toBeInTheDocument();
  expect(screen.getByText("20 missing bars")).toBeInTheDocument();
  expect(screen.getByText("Session 2026-10-14")).toBeInTheDocument();
  expect(screen.getByText("2 of 12 active series have no check for this session.")).toBeInTheDocument();
  fireEvent.click(screen.getByText("1 recent problem runs"));
  expect(screen.getByText("Provider rejected request")).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("Needs attention only"));
  expect(screen.queryByRole("button", {name: "View events for monitor"})).not.toBeInTheDocument();
  expect(within(screen.getByRole("button", {name: "View events for api"})).getByText("Reporting · recent errors")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Find a service"), {target: {value: "absent"}});
  expect(screen.getByText("No services match these controls.")).toBeInTheDocument();
  coverageFailed = true;
  fireEvent.click(screen.getByRole("button", {name: "Refresh now"}));
  expect(await screen.findByText("Stale coverage")).toBeInTheDocument();
  expect(screen.getByText("80.0%")).toBeInTheDocument();
  expect(screen.getByText("Monitor connected")).toBeInTheDocument();
  coverageFailed = false;
  await waitFor(() => expect(screen.getByRole("button", {name: "Refresh now"})).toBeEnabled());
  fireEvent.click(screen.getByRole("button", {name: "Refresh now"}));
  await waitFor(() => expect(screen.queryByText("Stale coverage")).not.toBeInTheDocument());
});
