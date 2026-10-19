import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Monitoring } from "./Monitoring";

afterEach(() => vi.unstubAllGlobals());

it("shows stale watcher, status, error history and filters", async () => {
  const fetchMock = vi.fn(async (url: string) => ({ ok: true, json: async () => url.includes("/daily") ? {generated_at: "2026-09-30T00:00:00Z", days: []} : url.includes("/coverage") ? {generated_at: "2026-09-30T00:00:00Z", feeds: []} : url.includes("/pipelines") ? {generated_at: "2026-09-30T00:00:00Z", pipelines: [], sources: []} : url.includes("/services") ? {
    monitor_offline: true, services: [{service_id: "ingest-us", status: "down", registered: true, last_seen_age_s: 90, uptime_s: 3600, restarts_24h: 2}],
  } : {events: [{received_at: "2026-09-30T00:00:00Z", service_id: "ingest-us", type: "err", severity: "error", error_type: "auth", message: "Token rejected"}]} }));
  vi.stubGlobal("fetch", fetchMock);
  render(<Monitoring />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Monitor offline");
  expect(screen.getByText("Stale · No heartbeat")).toBeInTheDocument();
  expect(screen.getByText("Token rejected")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Type"), {target: {value: "err"}});
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("type=err"), expect.anything()));
});

it("explains a scheduled job awaiting its first run and filters from its card", async () => {
  const fetchMock = vi.fn(async (url: string) => ({ ok: true, json: async () => url.includes("/daily") ? {generated_at: "2026-09-30T00:00:00Z", days: []} : url.includes("/coverage") ? {generated_at: "2026-09-30T00:00:00Z", feeds: []} : url.includes("/pipelines") ? {generated_at: "2026-09-30T00:00:00Z", pipelines: [], sources: []} : url.includes("/services") ? {
    monitor_offline: false, services: [
      {service_id: "ingest-political", status: "unknown", registered: true, last_seen_age_s: null, uptime_s: null, monitor: {max_silence_s: 93600}},
      {service_id: "ingest-us", status: "down", registered: true, last_seen_age_s: 120, uptime_s: 3600},
    ],
  } : {events: []} }));
  vi.stubGlobal("fetch", fetchMock);
  render(<Monitoring />);
  const card = await screen.findByRole("button", {name: "View events for ingest-political"});
  expect(within(card).getByText("Scheduled job")).toBeInTheDocument();
  expect(within(card).getByText("Not yet reported")).toBeInTheDocument();
  expect(within(card).getByText("Waiting for this job’s first monitored run.")).toBeInTheDocument();
  expect(screen.queryByText(/Never/)).not.toBeInTheDocument();
  expect(screen.getAllByRole("button", {name: /View events/})[0]).toHaveAccessibleName("View events for ingest-us");
  fireEvent.click(card);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("service_id=ingest-political"), expect.anything()));
  expect(card).toHaveAttribute("aria-pressed", "true");
  fireEvent.change(screen.getByLabelText("Time range"), {target: {value: "24"}});
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("since="), expect.anything()));
  fireEvent.click(screen.getByRole("button", {name: "Clear filters"}));
  await waitFor(() => expect(screen.getByLabelText("Service")).toHaveValue(""));
  expect(screen.getByLabelText("Time range")).toHaveValue("");
});

it("keeps the last snapshot on refresh failure and recovers on retry", async () => {
  let failed = false;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (failed) throw new Error("Network offline");
    return {ok: true, json: async () => url.includes("/daily") ? {generated_at: "2026-09-30T00:00:00Z", days: []} : url.includes("/coverage") ? {generated_at: "2026-09-30T00:00:00Z", feeds: []} : url.includes("/pipelines") ? {generated_at: "2026-09-30T00:00:00Z", pipelines: [], sources: []} : url.includes("/services") ? {
      monitor_offline: false, services: [{service_id: "api", status: "up", registered: true, last_seen_age_s: 2, uptime_s: 60}],
    } : {events: [{received_at: "2026-09-30T00:00:00Z", service_id: "api", type: "err", message: "Retained error"}]}};
  }));
  render(<Monitoring />);
  expect(await screen.findByText("Retained error")).toBeInTheDocument();
  failed = true;
  fireEvent.click(screen.getByRole("button", {name: "Refresh now"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("Showing the last successful snapshot");
  expect(screen.getByText("Retained error")).toBeInTheDocument();
  failed = false;
  fireEvent.click(screen.getByRole("button", {name: "Refresh now"}));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
});

it("distinguishes loading from an empty event history", async () => {
  let resolveStatus!: (value: unknown) => void;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ok: true, json: () => url.includes("/daily") ? Promise.resolve({generated_at: "2026-09-30T00:00:00Z", days: []}) : url.includes("/coverage") ? Promise.resolve({generated_at: "2026-09-30T00:00:00Z", feeds: []}) : url.includes("/pipelines") ? Promise.resolve({generated_at: "2026-09-30T00:00:00Z", pipelines: [], sources: []}) : url.includes("/services")
    ? new Promise(resolve => { resolveStatus = resolve; }) : Promise.resolve({events: []})})));
  render(<Monitoring />);
  expect(screen.getByText("Loading service health...")).toBeInTheDocument();
  expect(screen.queryByText("No events reported yet")).not.toBeInTheDocument();
  await waitFor(() => expect(resolveStatus).toBeDefined());
  resolveStatus({monitor_offline: false, services: []});
  expect(await screen.findByText("No events reported yet")).toBeInTheDocument();
});
