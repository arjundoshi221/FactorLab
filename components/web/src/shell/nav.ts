/** The single route table: drives the header tabs, sub-tabs and the command bar. */

export type SectionId = "data" | "portfolio" | "ops";

export interface NavItem {
  href: string;
  label: string;
  /** Short terminal mnemonic shown in the command bar. */
  code: string;
  keywords?: string;
  active: (path: string, search: URLSearchParams) => boolean;
}

export interface NavSection { id: SectionId; label: string; items: NavItem[] }

const view = (search: URLSearchParams) => search.get("view") ?? "";

export const COCKPIT: NavItem = { href: "/", label: "Cockpit", code: "HOME", keywords: "home overview markets", active: (path) => path === "/" };

export const sections: NavSection[] = [
  {
    id: "data",
    label: "Data",
    items: [
      { href: "/market-data", label: "Markets", code: "MKT", keywords: "tickers instruments explorer",
        active: (path, search) => path === "/market-data" && !["indexes", "futures", "matrix", "collection"].includes(view(search)) },
      { href: "/market-data?view=indexes", label: "Indexes", code: "INDX", keywords: "sectors heatmap spx",
        active: (path, search) => path === "/market-data" && view(search) === "indexes" },
      { href: "/market-data?view=futures", label: "Futures", code: "FUT", keywords: "curve contango term structure",
        active: (path, search) => path === "/market-data" && view(search) === "futures" },
      { href: "/market-data?view=matrix", label: "Matrix", code: "MTX", keywords: "data matrix coverage ticker",
        active: (path, search) => path === "/market-data" && view(search) === "matrix" },
      { href: "/india", label: "India", code: "IN", keywords: "nse bse one-minute candles", active: (path) => path.startsWith("/india") },
      { href: "/us", label: "US", code: "US", keywords: "equities schwab", active: (path) => path === "/us" },
      { href: "/political", label: "Political", code: "POL", keywords: "congress stock act disclosures", active: (path) => path === "/political" },
      { href: "/data", label: "Catalog", code: "CAT", keywords: "tables clickhouse search columns",
        active: (path) => path === "/data" || path.startsWith("/data/tables") },
      { href: "/schema", label: "Schema", code: "SCH", keywords: "graph relationships map", active: (path) => path.startsWith("/schema") },
    ],
  },
  {
    id: "portfolio",
    label: "Portfolio",
    items: [
      { href: "/portfolio", label: "Book", code: "PORT", keywords: "accounts positions balances orders", active: (path) => path === "/portfolio" },
      { href: "/portfolio/trades", label: "Trades", code: "TRD", keywords: "ledger executions tagging", active: (path) => path === "/portfolio/trades" },
      { href: "/portfolio/themes", label: "Themes", code: "THM", keywords: "pnl", active: (path) => path === "/portfolio/themes" },
      { href: "/portfolio/strategies", label: "Strategies", code: "STRT", keywords: "pnl", active: (path) => path === "/portfolio/strategies" },
      { href: "/portfolio/trade-names", label: "Trade names", code: "TNM", keywords: "pnl", active: (path) => path === "/portfolio/trade-names" },
    ],
  },
  {
    id: "ops",
    label: "Ops",
    items: [
      { href: "/ops", label: "Overview", code: "OPS", keywords: "tables inventory dashboard health", active: (path) => path === "/ops" },
      { href: "/monitoring", label: "Monitoring", code: "MON", keywords: "services heartbeats errors", active: (path) => path === "/monitoring" },
      { href: "/data/pipelines", label: "Pipelines", code: "PIPE", keywords: "ingestion runs sources", active: (path) => path === "/data/pipelines" },
      { href: "/market-data?view=collection", label: "Coverage", code: "COV", keywords: "collection recovery universe",
        active: (path, search) => path === "/market-data" && view(search) === "collection" },
      { href: "/docker-images", label: "Images", code: "IMG", keywords: "docker release containers", active: (path) => path === "/docker-images" },
      { href: "/roadmap", label: "Roadmap", code: "MAP", keywords: "releases plan", active: (path) => path === "/roadmap" },
    ],
  },
];

export const allItems: NavItem[] = [COCKPIT, ...sections.flatMap((section) => section.items)];

export function locate(path: string, search: URLSearchParams): { section: NavSection | null; item: NavItem | null } {
  for (const section of sections) {
    const item = section.items.find((entry) => entry.active(path, search));
    if (item) return { section, item };
  }
  return { section: null, item: COCKPIT.active(path, search) ? COCKPIT : null };
}
