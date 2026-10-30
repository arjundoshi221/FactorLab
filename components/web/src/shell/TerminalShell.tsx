import { useEffect, useRef, useState, type ReactNode } from "react";

import type { StatusSummary } from "../pages/MonitoringStatus";
import { useRemote } from "../shared/api";
import { WEB_VERSION } from "../shared/build";
import { CommandPalette } from "./CommandPalette";
import { useLocation } from "./location";
import { MarketClocks } from "./MarketClocks";
import { COCKPIT, locate, sections, type SectionId } from "./nav";
import { TickerTape } from "./TickerTape";

const verdictLabel: Record<StatusSummary["verdict"], string> = {
  ok: "All systems working", attention: "Needs attention", critical: "Critical problem", unknown: "Status unknown",
};

function HealthLight() {
  const remote = useRemote<StatusSummary>("/hub/api/v1/monitoring/summary", 30_000);
  // The shell renders on every page; ignore any payload that is not a status summary.
  const data = remote.data && remote.data.counts && remote.data.verdict in verdictLabel ? remote.data : null;
  const error = remote.error;
  const verdict = data?.verdict ?? "unknown";
  const problems = data ? data.counts.critical + data.counts.warning : 0;
  const label = error && !data ? "Monitor unreachable" : verdictLabel[verdict];
  return (
    <a className={`term-health term-health--${error && !data ? "unknown" : verdict}`} href="/monitoring" title={label} aria-label={`System status: ${label}`}>
      <i aria-hidden="true" />
      <span>SYS</span>
      {problems > 0 && <b>{problems}</b>}
    </a>
  );
}

const GO_KEYS: Record<string, string> = { h: "/", d: "/market-data", p: "/portfolio", o: "/ops", m: "/monitoring", t: "/portfolio/trades" };

function isTyping(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  return Boolean(element && (element.isContentEditable || ["INPUT", "SELECT", "TEXTAREA"].includes(element.tagName)));
}

/** Terminal chrome: section bar, sub-tabs, quote tape, status line and the command bar. */
export function TerminalShell({ children }: { children: ReactNode }) {
  const { path, search } = useLocation();
  const { section, item } = locate(path, search);
  const [palette, setPalette] = useState(false);
  const pendingGo = useRef(0);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette((open) => !open);
        return;
      }
      if (isTyping(event.target) || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === "/") {
        event.preventDefault();
        setPalette(true);
      } else if (event.key === "g") {
        pendingGo.current = Date.now();
      } else if (Date.now() - pendingGo.current < 900 && GO_KEYS[event.key]) {
        pendingGo.current = 0;
        window.location.assign(GO_KEYS[event.key]);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const activeSection: SectionId | null = section?.id ?? null;
  return (
    <div className="term">
      <header className="term-bar">
        <a className="term-brand" href="/" aria-label="FactorLab home">
          <span className="term-brand__mark" aria-hidden="true">FL</span>
          <strong>FactorLab</strong>
        </a>
        <nav className="term-sections" aria-label="Primary navigation">
          <a href="/" className={item === COCKPIT ? "active" : ""} aria-current={item === COCKPIT ? "page" : undefined}>Home</a>
          {sections.map((entry) => (
            <a key={entry.id} href={entry.items[0].href} className={activeSection === entry.id ? "active" : ""}
              aria-current={activeSection === entry.id ? "true" : undefined}>{entry.label}</a>
          ))}
        </nav>
        <button type="button" className="term-command" onClick={() => setPalette(true)} aria-label="Open command bar" aria-keyshortcuts="Control+K">
          <span aria-hidden="true">&gt;</span>
          <span className="term-command__hint">Go to page or ticker</span>
          <kbd>Ctrl K</kbd>
        </button>
        <MarketClocks />
        <HealthLight />
      </header>
      <nav className="term-subnav" aria-label="Section navigation">
        {(section?.items ?? []).map((entry) => (
          <a key={entry.href} href={entry.href} className={entry === item ? "active" : ""} aria-current={entry === item ? "page" : undefined}>
            <code aria-hidden="true">{entry.code}</code>{entry.label}
          </a>
        ))}
        {!section && <span className="term-subnav__home"><code>HOME</code>Cross-asset cockpit</span>}
      </nav>
      <TickerTape />
      <div className="term-body">{children}</div>
      <footer className="term-status" aria-label="Status line">
        <span><b>FACTORLAB</b> Private system</span>
        <span>{item ? `${item.code} · ${item.label}` : path}</span>
        <span className="term-status__spacer" />
        <span>Asia/Kolkata</span>
        <span>Web {WEB_VERSION ? `v${WEB_VERSION}` : "dev"}</span>
        <span><kbd>Ctrl K</kbd> command · <kbd>g</kbd> then <kbd>h</kbd>/<kbd>d</kbd>/<kbd>p</kbd>/<kbd>o</kbd></span>
      </footer>
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
    </div>
  );
}
