import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Indexes } from "./Indexes";

const returns = (v: number) => ({ "1d": v, "1w": v * 2, "1m": v * 3, "3m": null, ytd: v * 10, "1y": v * 12 });
const overview = {
  tracked: [{ code: "$SPX", name: "S&P 500", group: "index", value: 6012.5, change: 0.0042, trade_date: "2026-10-21", complete: true }],
  constructed: [], base_date: "2016-01-04", universe: "r3k_iwv",
  sectors: [{ sector_id: "45", name: "Information Technology", cw_etf: "XLK", ew_etf: "RSPT" }],
};
const sector = (id: string, name: string, cw: string, ew: string, v: number) => ({
  sector_id: id, name, code: `us_gics_${id}_cw`, level: 1500, complete: true, cw_etf: cw, ew_etf: ew,
  returns: { ew: returns(v / 2), cw: returns(v), cw_etf: returns(v), ew_etf: returns(v / 2) },
  movers: { top: [{ symbol: "NVDA", ret_1d: 0.03, contribution: 0.004 }], bottom: [] },
});
const performance = {
  as_of: "2026-10-21", weighting: "cw", periods: ["1d", "1w", "1m", "3m", "ytd", "1y"],
  sectors: [sector("45", "Information Technology", "XLK", "RSPT", 0.01), sector("10", "Energy", "XLE", "RSPG", -0.02)],
  benchmarks: { composite_ew: returns(0.001), composite_cw: returns(0.002), $SPX: returns(0.003), RSP: returns(0.001) },
};
const points = (base: number) => [{ trade_date: "2026-10-20", value: base, complete: true }, { trade_date: "2026-10-21", value: base * 1.1, complete: true }];
const series = { items: [
  { code: "us_gics_45_ew", name: "IT EW", kind: "constructed", points: points(1000) },
  { code: "us_gics_45_cw", name: "IT CW", kind: "constructed", points: points(1200) },
  { code: "XLK", name: "XLK", kind: "tracked", points: points(250) },
] };
const constituents = { code: "us_gics_45_cw", trade_date: "2026-10-21", total: 329,
  items: [{ listing_id: "a", symbol: "NVDA", name: "NVIDIA", weight: 0.21, ret_1d: 0.03, contribution: 0.0063 }] };

afterEach(() => vi.unstubAllGlobals());

it("shows tracked indexes, sector returns, comparison chart and constituents", async () => {
  const fetchMock = vi.fn(async (url: string) => ({ ok: true, json: async () =>
    url.includes("/sectors/performance") ? { ...performance, weighting: url.includes("weighting=ew") ? "ew" : "cw" }
      : url.includes("/indexes/series") ? series : url.includes("/constituents") ? constituents : overview }));
  vi.stubGlobal("fetch", fetchMock);
  render(<Indexes />);
  expect(await screen.findByText("6,012.50")).toBeInTheDocument();
  expect(screen.getByText("▲ +0.42%")).toBeInTheDocument();
  const table = await screen.findByRole("table", { name: "Sector returns" });
  expect(within(table).getByRole("button", { name: "Energy" })).toBeInTheDocument();
  expect(within(table).getAllByText("+10.0%").length).toBeGreaterThan(0);
  expect(await screen.findByRole("img", { name: "Information Technology index comparison" })).toBeInTheDocument();
  expect(await screen.findByText("329 constituents · 2026-10-21", { exact: false })).toBeInTheDocument();
  expect(screen.getAllByText("NVIDIA").length).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "Equal weight" }));
  await vi.waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("weighting=ew"))).toBe(true));
});

it("explains an empty index store and surfaces errors", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("/indexes") && !url.includes("series")
    ? { ok: true, json: async () => url.includes("performance") ? { ...performance, as_of: null, sectors: [] } : overview }
    : { ok: true, json: async () => ({ ...performance, as_of: null, sectors: [] }) }));
  render(<Indexes />);
  expect(await screen.findByText(/No index levels yet/)).toBeInTheDocument();
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 503 })));
  render(<Indexes />);
  expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent("503");
});
