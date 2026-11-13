import { describe, expect, it } from "vitest";

import { attributeOf, pivotRows, sumDecimals, UNCLASSIFIED } from "./pivot";

describe("sumDecimals", () => {
  it("adds exactly across scales and subtracts signed terms", () => {
    expect(sumDecimals(["0.1", "0.2"])).toBe("0.3");
    expect(sumDecimals(["123456789012345678.000001", "1"])).toBe("123456789012345679.000001");
    expect(sumDecimals(["10", ["2.5", -1]])).toBe("7.5");
    expect(sumDecimals(["1", ["3", -1]])).toBe("-2");
    expect(sumDecimals([])).toBe("0");
  });

  it("reads Python exponent notation", () => {
    expect(sumDecimals(["0E-12", "1.5E+3"])).toBe("1500");
    expect(sumDecimals(["-0E-6"])).toBe("0");
  });

  it("is unavailable when any term is", () => {
    expect(sumDecimals(["1", null])).toBeNull();
    expect(sumDecimals(["1", ""])).toBeNull();
    expect(sumDecimals(["abc"])).toBeNull();
  });
});

describe("pivotRows", () => {
  const tech = { id: "45", label: "Information Technology" };
  const energy = { id: "10", label: "Energy" };
  const rows = [
    { s: "AAPL", attributes: { sector: tech } },
    { s: "ZZZ" },
    { s: "XOM", attributes: { sector: energy } },
    { s: "MSFT", attributes: { sector: tech } },
  ];

  it("groups in the dimension's order with Unclassified last", () => {
    const buckets = pivotRows(rows, row => attributeOf(row, "sector"), [energy, tech, UNCLASSIFIED]);
    expect(buckets.map(b => [b.value.label, b.rows.map(r => r.s)])).toEqual([
      ["Energy", ["XOM"]], ["Information Technology", ["AAPL", "MSFT"]], ["Unclassified", ["ZZZ"]],
    ]);
  });

  it("falls back to label order for buckets the order does not name", () => {
    const buckets = pivotRows(rows, row => attributeOf(row, "sector"));
    expect(buckets.map(b => b.value.label)).toEqual(["Energy", "Information Technology", "Unclassified"]);
  });
});
