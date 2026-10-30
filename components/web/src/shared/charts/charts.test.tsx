import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AreaChart, heatFill, SignedBars, Sparkline, squarify, Treemap } from ".";

describe("chart kit", () => {
  it("squarify fills the box exactly and keeps every area proportional", () => {
    const areas = [6, 6, 4, 3, 2, 2, 1];
    const rects = squarify(areas, { x: 0, y: 0, w: 60, h: 40 });
    expect(rects).toHaveLength(areas.length);
    const total = rects.reduce((sum, rect) => sum + rect.w * rect.h, 0);
    expect(total).toBeCloseTo(60 * 40, 6);
    rects.forEach((rect, index) => expect(rect.w * rect.h).toBeCloseTo((areas[index] / 24) * 2400, 6));
    rects.forEach((rect) => {
      expect(rect.x + rect.w).toBeLessThanOrEqual(60 + 1e-9);
      expect(rect.y + rect.h).toBeLessThanOrEqual(40 + 1e-9);
    });
  });

  it("maps signed changes onto the up/down poles and leaves unknowns neutral", () => {
    expect(heatFill(0.05, 0.03)).toContain("var(--up) 62%");
    expect(heatFill(-0.01, 0.03)).toContain("var(--down)");
    expect(heatFill(null)).toBe("var(--raised)");
  });

  it("treemap tiles are labelled and selectable", () => {
    let picked = "";
    render(<Treemap label="Holdings" items={[
      { key: "a", label: "AAPL", size: 3, change: 0.02, onSelect: () => { picked = "a"; } },
      { key: "b", label: "MSFT", size: 1, change: -0.01, title: "MSFT −1%" },
      { key: "z", label: "ZERO", size: 0 },
    ]} />);
    expect(screen.getByRole("group", { name: "Holdings" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "AAPL" }));
    expect(picked).toBe("a");
    expect(screen.getByRole("img", { name: "MSFT −1%" })).toBeInTheDocument();
    expect(screen.queryByText("ZERO")).not.toBeInTheDocument();
  });

  it("sparkline and area chart describe their data for assistive tech", () => {
    render(<>
      <Sparkline values={[1, 2, 3]} label="Trend" />
      <Sparkline values={[1]} label="Thin" />
      <AreaChart points={[{ t: "2026-10-01", value: 10 }, { t: "2026-10-02", value: 12 }]} label="Equity" format={(v) => v.toFixed(0)} formatTime={(t) => t} />
    </>);
    expect(screen.getByRole("img", { name: /Trend: 3 points, from 1 to 3/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Thin: not enough history")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Equity: 2 points from 2026-10-01 to 2026-10-02, last 12/ })).toBeInTheDocument();
  });

  it("signed bars keep values as text with direction classes", () => {
    render(<SignedBars label="Movers" bars={[{ key: "a", label: "AAA", value: 0.02, display: "+2.00%" }, { key: "b", label: "BBB", value: -0.01, display: "-1.00%" }]} />);
    expect(screen.getByText("+2.00%")).toHaveClass("t-num--up");
    expect(screen.getByText("-1.00%")).toHaveClass("t-num--down");
  });
});
