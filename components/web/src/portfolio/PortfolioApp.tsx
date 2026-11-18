import { Portfolio } from "./Portfolio";
import { Performance } from "./Performance";
import { PortfolioGroups } from "./PortfolioBook";
import { PortfolioShell } from "./PortfolioShell";
import { Risk } from "./Risk";
import { TradesPage } from "./TradeHistory";

function PortfolioPage({ path }: { path: string }) {
  if (path === "/portfolio/risk") return <Risk />;
  if (path === "/portfolio/performance") return <Performance />;
  if (path === "/portfolio/trades") return <TradesPage />;
  if (path === "/portfolio/themes") return <PortfolioGroups kind="themes" />;
  if (path === "/portfolio/strategies") return <PortfolioGroups kind="strategies" />;
  if (path === "/portfolio/trade-names") return <PortfolioGroups kind="trade-names" />;
  return <Portfolio />;
}

/** The portfolio app: its own shell and theme, loaded as a separate chunk from the terminal. */
export function PortfolioApp({ path }: { path: string }) {
  return <PortfolioShell><PortfolioPage path={path} /></PortfolioShell>;
}
