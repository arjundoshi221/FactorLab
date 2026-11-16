import { describe, expect, it } from "vitest";

import { attributeOf, parseGroupBy, pivotRows, pivotTree, sumDecimals, treeKeys, UNCLASSIFIED } from "./pivot";

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

describe("pivotTree", () => {
  const rows = [
    { s: "AAPL", sector: "Tech", theme: "AI" }, { s: "MSFT", sector: "Tech", theme: "Cloud" },
    { s: "XOM", sector: "Energy", theme: "AI" }, { s: "ZZZ", sector: null, theme: "AI" },
  ];
  const level = (id: "sector" | "theme") => ({ id, label: id, valueOf: (row: typeof rows[number]) => row[id] === null ? UNCLASSIFIED : { id: row[id], label: row[id] as string } });

  it("nests any number of levels, each node aggregating its rows", () => {
    const tree = pivotTree(rows, [level("theme"), level("sector")], items => items.length);
    expect(tree.map(node => [node.value.label, node.totals, node.depth, node.children.map(child => [child.value.label, child.totals, child.depth])])).toEqual([
      ["AI", 3, 0, [["Energy", 1, 1], ["Tech", 1, 1], ["Unclassified", 1, 1]]],
      ["Cloud", 1, 0, [["Tech", 1, 1]]],
    ]);
    expect(tree[0].children[0].children).toEqual([]);
    expect(new Set(treeKeys(tree)).size).toBe(6);
  });
});

describe("parseGroupBy", () => {
  const parse = (query: string, defaults = ["theme"]) => parseGroupBy(new URLSearchParams(query), defaults);
  it("reads group= and maps the older pivot links", () => {
    expect(parse("")).toEqual(["theme"]);
    expect(parse("", ["theme", "sector"])).toEqual(["theme", "sector"]);
    expect(parse("group=sector,theme,sector")).toEqual(["sector", "theme"]);
    expect(parse("pivot=sector")).toEqual(["theme", "sector"]);
    expect(parse("pivot=sector&rows=attr")).toEqual(["sector", "theme"]);
  });
});
