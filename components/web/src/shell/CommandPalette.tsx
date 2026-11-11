import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import type { InstrumentPage } from "../instrumentTypes";
import { fetchJson } from "../shared/api";
import { tickerHref } from "../shared/urlState";
import { allItems, sections } from "./nav";

interface Entry { key: string; code: string; label: string; detail: string; href: string }

const RECENT_KEY = "factorlab.command.recent";

function readRecent(): Entry[] {
  try {
    const stored = JSON.parse(window.localStorage.getItem(RECENT_KEY) ?? "[]") as Entry[];
    return Array.isArray(stored) ? stored.slice(0, 5) : [];
  } catch {
    return [];
  }
}

function remember(entry: Entry) {
  try {
    const next = [entry, ...readRecent().filter((item) => item.key !== entry.key)].slice(0, 5);
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    // Storage can be unavailable (private windows); recents are a convenience only.
  }
}

const pages: Entry[] = allItems.map((item) => {
  const section = sections.find((candidate) => candidate.items.includes(item));
  return { key: `page:${item.href}`, code: item.code, label: item.label, detail: `${section?.label ?? "Home"} ${item.keywords ?? ""}`.trim(), href: item.href };
});

export function matchPages(query: string): Entry[] {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return pages;
  return pages
    .map((entry) => {
      const haystack = `${entry.code} ${entry.label} ${entry.detail}`.toLowerCase();
      if (!words.every((word) => haystack.includes(word))) return null;
      const score = entry.code.toLowerCase() === words[0] ? 0 : entry.label.toLowerCase().startsWith(words[0]) ? 1 : 2;
      return { entry, score };
    })
    .filter((item): item is { entry: Entry; score: number } => item !== null)
    .sort((a, b) => a.score - b.score)
    .map((item) => item.entry);
}

/** Ctrl+K / "/" command bar: jump to any page, or look up a ticker. */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [tickers, setTickers] = useState<Entry[]>([]);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    setTickers([]);
    window.setTimeout(() => input.current?.focus(), 0);
  }, [open]);

  useEffect(() => {
    const term = query.trim();
    if (!open || term.length < 2) {
      setTickers([]);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      fetchJson<InstrumentPage>(`/hub/api/v1/market-data/instruments?${new URLSearchParams({ search: term, kind: "listing" })}`, { signal: controller.signal })
        .then((page) => setTickers(page.items.slice(0, 8).map((item) => ({
          key: `inst:${item.kind}/${item.id}`, code: item.symbol, label: item.name || item.symbol,
          detail: [item.country, item.exchange, item.instrument_type].filter(Boolean).join(" · "),
          href: tickerHref(item.id),
        }))))
        .catch(() => setTickers([]));
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [open, query]);

  const results = useMemo(() => {
    const matched = matchPages(query);
    const recent = query.trim() ? [] : readRecent();
    const seen = new Set(recent.map((entry) => entry.key));
    return [...recent, ...matched.filter((entry) => !seen.has(entry.key)), ...tickers];
  }, [query, tickers]);

  useEffect(() => setActive((index) => Math.min(index, Math.max(results.length - 1, 0))), [results.length]);

  if (!open) return null;

  function go(entry: Entry) {
    remember(entry);
    onClose();
    window.location.assign(entry.href);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") { event.preventDefault(); setActive((index) => Math.min(index + 1, results.length - 1)); }
    else if (event.key === "ArrowUp") { event.preventDefault(); setActive((index) => Math.max(index - 1, 0)); }
    else if (event.key === "Enter" && results[active]) { event.preventDefault(); go(results[active]); }
    else if (event.key === "Escape") { event.preventDefault(); onClose(); }
  }

  return (
    <div className="term-palette" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="term-palette__box" role="dialog" aria-modal="true" aria-label="Command bar">
        <div className="term-palette__input">
          <span aria-hidden="true">&gt;</span>
          <input
            ref={input}
            value={query}
            onChange={(event) => { setQuery(event.target.value); setActive(0); }}
            onKeyDown={onKeyDown}
            placeholder="Go to page or ticker (e.g. FUT, PORT, AAPL)"
            aria-label="Command"
            aria-controls="term-palette-results"
            aria-activedescendant={results[active] ? `term-palette-${active}` : undefined}
            role="combobox"
            aria-expanded="true"
          />
          <kbd>Esc</kbd>
        </div>
        <ul id="term-palette-results" role="listbox" aria-label="Results">
          {results.map((entry, index) => (
            <li key={entry.key} id={`term-palette-${index}`} role="option" aria-selected={index === active}
              className={index === active ? "is-active" : ""} onMouseEnter={() => setActive(index)} onClick={() => go(entry)}>
              <code>{entry.code}</code>
              <span>{entry.label}</span>
              <small>{entry.key.startsWith("inst:") ? entry.detail : entry.detail.split(" ")[0]}</small>
            </li>
          ))}
          {results.length === 0 && <li className="term-palette__empty">No matches</li>}
        </ul>
      </div>
    </div>
  );
}
