import { Component, useLayoutEffect, useState, type ReactNode } from "react";

import { WEB_VERSION } from "../shared/build";
import { useLocation } from "../shell/location";
import { sections } from "../shell/nav";
import "./theme.css";

export type PortfolioTheme = "dark" | "light";
const THEME_KEY = "factorlab.portfolio.theme";

function storedTheme(): PortfolioTheme {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    if (value === "light" || value === "dark") return value;
  } catch { /* storage blocked: fall back to the default */ }
  return "dark";
}

const portfolioItems = sections.find(section => section.id === "portfolio")?.items ?? [];
/** Query keys that carry the selected profile and account between portfolio pages. */
const CONTEXT_KEYS = ["user_id", "account"];

function ThemeIcon({ theme }: { theme: PortfolioTheme }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {theme === "dark" ? <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />
      : <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>}
  </svg>;
}

/** Keeps one page's render error from blanking the whole app; the shell and its navigation stay usable. */
class PageErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    if (!this.state.error) return this.props.children;
    return <main className="portfolio-page">
      <div className="portfolio-notice portfolio-notice--error" role="alert">
        <strong>This page stopped rendering.</strong> {this.state.error.message || "An unexpected error occurred."} Other portfolio pages still work; reloading usually clears it.{" "}
        <button type="button" className="portfolio-button portfolio-button--secondary" onClick={() => window.location.reload()}>Reload page</button>
      </div>
    </main>;
  }
}

/** Portfolio app chrome: its own header, tabs and light/dark theme, separate from the terminal shell. */
export function PortfolioShell({ children }: { children: ReactNode }) {
  const { path, search } = useLocation();
  const [theme, setTheme] = useState<PortfolioTheme>(storedTheme);

  // Theme the document too, so overscroll and native controls match the app.
  useLayoutEffect(() => {
    const root = document.documentElement;
    root.dataset.pfTheme = theme;
    return () => { delete root.dataset.pfTheme; };
  }, [theme]);

  function toggle() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    try { window.localStorage.setItem(THEME_KEY, next); } catch { /* not persisted */ }
  }

  const context = new URLSearchParams();
  for (const key of CONTEXT_KEYS) {
    const value = search.get(key);
    if (value) context.set(key, value);
  }
  const query = context.toString();
  const suffix = query ? `?${query}` : "";
  return <div className="pf-app" data-pf-theme={theme}>
    <header className="pf-bar">
      <a className="pf-brand" href={`/portfolio${suffix}`} aria-label="FactorLab portfolio home">
        <span className="pf-brand__mark" aria-hidden="true">FL</span>
        <strong>FactorLab</strong><small>Portfolio</small>
      </a>
      <nav className="pf-nav" aria-label="Portfolio navigation">
        {portfolioItems.map(item => {
          const active = item.active(path, search);
          return <a key={item.href} href={`${item.href}${suffix}`} aria-current={active ? "page" : undefined}>{item.label}</a>;
        })}
      </nav>
      <div className="pf-bar__tools">
        <button type="button" className="pf-chip-button" onClick={toggle} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title="Switch theme">
          <ThemeIcon theme={theme} /><span className="pf-hide-s">{theme === "dark" ? "Dark" : "Light"}</span>
        </button>
        <a className="pf-chip-button" href="/" title="Open the market data terminal">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="14" rx="2" /><path d="m7 9 3 2-3 2M12 14h4" /></svg>
          <span className="pf-hide-s">Terminal</span>
        </a>
      </div>
    </header>
    <div className="pf-body"><PageErrorBoundary key={path}>{children}</PageErrorBoundary></div>
    <footer className="pf-foot"><span>FactorLab · Private portfolio workspace</span><span>Times in Asia/Kolkata</span><span>Web {WEB_VERSION ? `v${WEB_VERSION}` : "dev"}</span></footer>
  </div>;
}
