import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MonitoringErrors, type MonitoringErrorResponse } from "./MonitoringErrors";

afterEach(() => vi.unstubAllGlobals());

const history: MonitoringErrorResponse = {
  generated_at: "2026-10-15T12:00:00Z", since: "2026-10-08T12:00:00Z", has_more: false, unavailable: [],
  issues: [{origin: "pipeline", service_id: "universe-us", severity: "error", error_type: "failed",
    message: "Universe membership validation failed", first_seen: "2026-10-12T11:00:00Z", last_seen: "2026-10-15T11:00:00Z",
    occurrences: 200, pipeline: "us_universe_sync", source: "github_csv", run_id: "failed-run-id", failed_series: 200,
    last_success: null, recovery: "unconfirmed"}],
};

it("shows repeated collection errors, details and server-side filters", async () => {
  const fetchMock = vi.fn(async () => ({ok: true, json: async () => history}));
  vi.stubGlobal("fetch", fetchMock);
  render(<MonitoringErrors refreshKey={0} services={["universe-us"]} />);
  expect(await screen.findByText("200 occurrences")).toBeInTheDocument();
  fireEvent.click(screen.getByText("Universe membership validation failed", {selector: "strong"}));
  expect(screen.getByText("failed-run-id")).toBeInTheDocument();
  expect(screen.getByText("Recovery unconfirmed")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Error time range"), {target: {value: "720"}});
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("hours=720"), expect.anything()));
  fireEvent.change(screen.getByLabelText("Error service"), {target: {value: "universe-us"}});
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("service_id=universe-us"), expect.anything()));
  fireEvent.change(screen.getByLabelText("Error severity"), {target: {value: "fatal"}});
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("severity=fatal"), expect.anything()));
});

it("preserves stale errors on failure and never calls incomplete history clear", async () => {
  let failed = false;
  vi.stubGlobal("fetch", vi.fn(async () => {
    if (failed) throw new Error("Offline");
    return {ok: true, json: async () => ({...history, unavailable: ["telemetry"], has_more: true})};
  }));
  const {rerender} = render(<MonitoringErrors refreshKey={0} services={[]} />);
  expect(await screen.findByText(/Incomplete error history/)).toBeInTheDocument();
  expect(screen.getByText(/Showing the latest 100 groups/)).toBeInTheDocument();
  failed = true;
  rerender(<MonitoringErrors refreshKey={1} services={[]} />);
  expect(await screen.findByText(/Showing stale snapshot/)).toBeInTheDocument();
  expect(screen.getByText("200 occurrences")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Search loaded errors"), {target: {value: "different"}});
  expect(screen.getByText("No loaded errors match this search.")).toBeInTheDocument();
  expect(screen.queryByText("No recorded errors match this window and service filter.")).not.toBeInTheDocument();
});

it("does not show the previous service's errors while a new filter loads", async () => {
  let resolve!: (value: unknown) => void;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ok: true,
    json: () => url.includes("service_id=api") ? new Promise(done => {resolve = done;}) : Promise.resolve(history),
  })));
  render(<MonitoringErrors refreshKey={0} services={["api", "universe-us"]} />);
  expect(await screen.findByText("200 occurrences")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Error service"), {target: {value: "api"}});
  expect(screen.queryByText("200 occurrences")).not.toBeInTheDocument();
  expect(screen.getByText("Loading error history...")).toBeInTheDocument();
  await waitFor(() => expect(resolve).toBeDefined());
  resolve({...history, issues: [], unavailable: ["pipeline_runs"]});
  expect(await screen.findByText("No errors available from the responding sources. History is incomplete.")).toBeInTheDocument();
});

it("shows later successes as evidence without claiming every missing record is repaired", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ok: true, json: async () => ({...history, issues: [{...history.issues[0],
    recovery: "later_success", last_success: "2026-10-15T11:30:00Z"}]})})));
  render(<MonitoringErrors refreshKey={0} services={[]} />);
  fireEvent.click(await screen.findByText("Universe membership validation failed", {selector: "strong"}));
  expect(screen.getByText("Later successful run")).toBeInTheDocument();
  expect(screen.getByText(/does not prove that every failed symbol/)).toBeInTheDocument();
  const region = screen.getByRole("region", {name: "Error inbox"});
  expect(within(region).getByText("200 occurrences")).toBeInTheDocument();
});
