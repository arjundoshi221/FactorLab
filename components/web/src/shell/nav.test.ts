import { describe, expect, it } from "vitest";

import { matchPages } from "./CommandPalette";
import { allItems, locate, sections } from "./nav";

const at = (href: string) => {
  const url = new URL(href, "http://hub.test");
  return locate(url.pathname, url.searchParams);
};

describe("terminal navigation", () => {
  it("has exactly the Data, Portfolio and Ops headers", () => {
    expect(sections.map((section) => section.label)).toEqual(["Data", "Portfolio", "Ops"]);
  });

  it.each([
    ["/", null, "Cockpit"],
    ["/market-data", "data", "Markets"],
    ["/market-data?view=sectors", "data", "Markets"],
    ["/market-data?view=indexes", "data", "Indexes"],
    ["/market-data?view=futures&root=/ES", "data", "Futures"],
    ["/market-data?view=matrix", "data", "Matrix"],
    ["/market-data?view=collection", "ops", "Coverage"],
    ["/india/instruments/9b0b5a8e-1111-4222-8333-444455556666", "data", "India"],
    ["/data", "data", "Catalog"],
    ["/data/tables/market.bars", "data", "Catalog"],
    ["/schema/v2", "data", "Schema"],
    ["/portfolio", "portfolio", "Book"],
    ["/portfolio/trades", "portfolio", "Trades"],
    ["/portfolio/trade-names", "portfolio", "Trade names"],
    ["/ops", "ops", "Overview"],
    ["/data/pipelines", "ops", "Pipelines"],
    ["/monitoring", "ops", "Monitoring"],
    ["/docker-images", "ops", "Images"],
    ["/roadmap", "ops", "Roadmap"],
  ])("places %s under %s / %s", (href, section, label) => {
    const found = at(href);
    expect(found.section?.id ?? null).toBe(section);
    expect(found.item?.label).toBe(label);
  });

  it("gives every page a unique mnemonic", () => {
    const codes = allItems.map((item) => item.code);
    expect(new Set(codes).size).toBe(codes.length);
  });

  it("matches commands by mnemonic first, then label and keywords", () => {
    expect(matchPages("fut")[0].href).toBe("/market-data?view=futures");
    expect(matchPages("ledger")[0].href).toBe("/portfolio/trades");
    expect(matchPages("docker").map((entry) => entry.href)).toContain("/docker-images");
    expect(matchPages("zzzz")).toEqual([]);
  });
});
