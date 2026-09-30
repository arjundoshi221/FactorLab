import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Monitoring } from "./Monitoring";

afterEach(() => vi.unstubAllGlobals());

it("shows stale watcher, status, error history and filters", async () => {
  const fetchMock = vi.fn(async (url: string) => ({ ok: true, json: async () => url.includes("/services") ? {
    monitor_offline: true, services: [{service_id: "ingest-us", status: "down", registered: true, last_seen_age_s: 90, uptime_s: 3600, restarts_24h: 2}],
  } : {events: [{received_at: "2026-09-30T00:00:00Z", service_id: "ingest-us", type: "err", severity: "error", error_type: "auth", message: "Token rejected"}]} }));
  vi.stubGlobal("fetch", fetchMock);
  render(<Monitoring />);
  expect(await screen.findByText(/Monitor offline/)).toBeInTheDocument();
  expect(screen.getByText("down")).toBeInTheDocument();
  expect(screen.getByText("Token rejected")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Type"), {target: {value: "err"}});
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("type=err"), expect.anything()));
});
