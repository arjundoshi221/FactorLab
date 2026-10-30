import { lazy, Suspense } from "react";

import { Cockpit } from "./pages/Cockpit";
import { DockerImages } from "./pages/DockerImages";
import { IndiaInstrumentDetail } from "./pages/IndiaInstrumentDetail";
import { IndiaMarkets } from "./pages/IndiaMarkets";
import { MarketData } from "./pages/InstrumentExplorer";
import { Monitoring } from "./pages/Monitoring";
import { OpsOverview } from "./pages/OpsOverview";
import { PoliticalData } from "./pages/PoliticalData";
import { Portfolio } from "./pages/Portfolio";
import { PortfolioGroups } from "./pages/PortfolioBook";
import { Roadmap } from "./pages/Roadmap";
import { TradeLedger } from "./pages/TradeLedger";
import { USMarkets } from "./pages/USMarkets";
import { DataHome } from "./pages/catalog/DataHome";
import { Pipelines } from "./pages/catalog/Pipelines";
import { TerminalShell } from "./shell/TerminalShell";

const SchemaExplorer = lazy(async () => {
  const module = await import("./pages/schema/SchemaExplorer");
  return { default: module.SchemaExplorer };
});

const TableDetail = lazy(async () => {
  const module = await import("./pages/catalog/TableDetail");
  return { default: module.TableDetail };
});

function Page({ path }: { path: string }) {
  const indiaInstrumentMatch = path.match(/^\/india\/instruments\/([0-9a-f-]+)$/i);
  const tableMatch = path.match(/^\/data\/tables\/([a-z_]+\.[a-z0-9_]+)$/);
  if (path === "/") return <Cockpit />;
  if (path === "/ops") return <OpsOverview />;
  if (path === "/portfolio") return <Portfolio />;
  if (path === "/portfolio/trades") return <TradeLedger />;
  if (path === "/portfolio/themes") return <PortfolioGroups kind="themes" />;
  if (path === "/portfolio/strategies") return <PortfolioGroups kind="strategies" />;
  if (path === "/portfolio/trade-names") return <PortfolioGroups kind="trade-names" />;
  if (path === "/data") return <DataHome />;
  if (path === "/data/pipelines") return <Pipelines />;
  if (tableMatch) {
    return (
      <Suspense fallback={<main className="loading-state">Loading table…</main>}>
        <TableDetail key={tableMatch[1]} name={tableMatch[1]} />
      </Suspense>
    );
  }
  if (path === "/schema" || path === "/schema/v2") {
    return (
      <Suspense fallback={<main className="loading-state">Loading the schema explorer…</main>}>
        <SchemaExplorer />
      </Suspense>
    );
  }
  if (path === "/market-data") return <MarketData />;
  if (path === "/monitoring") return <Monitoring />;
  if (path === "/roadmap") return <Roadmap />;
  if (path === "/docker-images") return <DockerImages />;
  if (indiaInstrumentMatch) return <IndiaInstrumentDetail listingId={indiaInstrumentMatch[1]} />;
  if (path === "/india") return <IndiaMarkets />;
  if (path === "/us") return <USMarkets />;
  if (path === "/political") return <PoliticalData />;
  return <Cockpit />;
}

export function App() {
  // Indexes and Futures live in Market Data; keep direct links working.
  if (window.location.pathname === "/indexes") window.history.replaceState(null, "", "/market-data?view=indexes");
  if (window.location.pathname === "/futures") window.history.replaceState(null, "", "/market-data?view=futures");
  return (
    <TerminalShell>
      <Page path={window.location.pathname} />
    </TerminalShell>
  );
}
