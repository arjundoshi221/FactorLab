import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MonitoringStatus, type StatusSummary } from "./MonitoringStatus";

afterEach(() => vi.unstubAllGlobals());

const summary = (overrides: Partial<StatusSummary>): StatusSummary => ({
  generated_at: "2026-10-15T12:00:00Z", verdict: "ok",
  counts: { critical: 0, warning: 0, unknown: 0, info: 0, ok: 0 }, checks: [], ...overrides,
});
const check = (id: string, severity: StatusSummary["checks"][number]["severity"], title: string, count: number | null = null) => ({
  id, area: id.split(":")[0], severity, title, detail: "", count, observed_at: null, href: `/monitoring#${id}`, link_label: "Open details",
});
function serve(body: unknown, ok = true) {
  const fetchMock = vi.fn(async () => ({ ok, status: ok ? 200 : 503, json: async () => body }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

it("says everything works and keeps passing checks collapsed", async () => {
  serve(summary({ counts: { critical: 0, warning: 0, unknown: 0, info: 1, ok: 2 }, checks: [
    check("services:ok", "ok", "7 services reporting on time", 7),
    check("pipelines:ok", "ok", "8 pipelines on schedule", 8),
    check("coverage:IN-upstox-1m", "info", "IN · upstox · 1m: no session coverage checks recorded"),
  ] }));
  render(<MonitoringStatus />);
  const region = await screen.findByRole("region", { name: "All systems working" });
  expect(within(region).getByText("15 checks passing", { selector: "summary" })).toBeInTheDocument();
  expect(within(region).getByText("1 note · not failures")).toBeInTheDocument();
  expect(within(region).queryByRole("list", { name: "Needs attention" })).not.toBeInTheDocument();
});

it("lists problems worst first with links to their evidence", async () => {
  serve(summary({ verdict: "critical", counts: { critical: 1, warning: 1, unknown: 1, info: 0, ok: 0 }, checks: [
    check("services:ingest-us", "critical", "ingest-us is down"),
    check("broker:ibkr:U1", "warning", "IBKR U1: snapshot missed"),
    check("coverage:unavailable", "unknown", "Data completeness could not be checked"),
  ] }));
  render(<MonitoringStatus />);
  const region = await screen.findByRole("region", { name: "2 things need attention" });
  const items = within(within(region).getByRole("list", { name: "Needs attention" })).getAllByRole("listitem");
  expect(items.map(item => item.textContent)).toEqual([
    expect.stringContaining("ingest-us is down"), expect.stringContaining("snapshot missed"), expect.stringContaining("could not be checked"),
  ]);
  expect(within(items[0]).getByRole("link")).toHaveAttribute("href", "/monitoring#services:ingest-us");
  expect(within(region).getByText(/1 critical issue · 1 warning · 1 area not checked/)).toBeInTheDocument();
});

it("never claims success when the summary cannot be read", async () => {
  serve({ detail: "Service monitoring is unavailable" }, false);
  render(<MonitoringStatus />);
  expect(await screen.findByRole("region", { name: "System status unavailable" })).toBeInTheDocument();
  expect(screen.queryByText("All systems working")).not.toBeInTheDocument();
});

it("treats a malformed response as unavailable and refetches on manual refresh", async () => {
  const fetchMock = serve({ events: [] });
  const { rerender } = render(<MonitoringStatus refreshKey={0} />);
  expect(await screen.findByRole("region", { name: "System status unavailable" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  rerender(<MonitoringStatus refreshKey={1} />);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
});
