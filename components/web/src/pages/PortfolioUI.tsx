import { useEffect, useRef, type ReactNode } from "react";

import type { Profile } from "../portfolioTypes";

/** Refresh mounted panels without throwing away their last result or unsaved forms. */
export function usePortfolioRefresh(revision: number, ...refreshers: Array<() => void>) {
  const previous = useRef(revision);
  useEffect(() => {
    if (previous.current !== revision) {
      previous.current = revision;
      refreshers.forEach(refresh => refresh());
    }
  }, [revision, ...refreshers]);
}

/** Format decimal strings without losing precision in large broker values. */
export function portfolioNumber(value: string | number | null | undefined, digits = 2, trim = false): string {
  if (value === null || value === undefined || value === "") return "—";
  const text = String(value);
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(text);
  if (!match) return text;
  const fraction = match[3] ?? "";
  const scale = 10n ** BigInt(digits);
  let amount = BigInt(match[2]) * scale + BigInt(fraction.slice(0, digits).padEnd(digits, "0") || "0");
  if (Number(fraction[digits] ?? 0) >= 5) amount += 1n;
  let tail = digits ? (amount % scale).toString().padStart(digits, "0") : "";
  if (trim) tail = tail.replace(/0+$/, "");
  return `${match[1] && amount ? "−" : ""}${(amount / scale).toLocaleString("en-US")}${tail ? `.${tail}` : ""}`;
}

export function Amount({ value, signed = false, quantity = false }: {
  value: string | number | null | undefined; signed?: boolean; quantity?: boolean;
}) {
  const numeric = value === null || value === undefined || value === "" ? NaN : Number(value);
  const tone = signed && Number.isFinite(numeric) ? numeric > 0 ? "positive" : numeric < 0 ? "negative" : "neutral" : "neutral";
  return <span className={`portfolio-amount portfolio-amount--${tone}`} title={value == null ? "Unavailable" : String(value)}>
    {portfolioNumber(value, quantity ? 6 : 2, quantity)}
  </span>;
}

export function PortfolioIcon({ name }: { name: "refresh" | "plus" | "search" | "arrow" | "chevron" }) {
  const paths = {
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3A7 7 0 0 0 18 17" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4.5 4.5" /></>,
    arrow: <path d="M6 18 18 6M6 6h12v12" />,
    chevron: <path d="m9 5 7 7-7 7" />,
  };
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="portfolio-empty"><span className="portfolio-empty__mark" aria-hidden="true">↗</span><strong>{title}</strong>{children && <p>{children}</p>}</div>;
}

export function ProfileSelect({ profiles, value, onChange, all = false, disabled = false }: {
  profiles: Profile[]; value: string; onChange: (value: string) => void; all?: boolean; disabled?: boolean;
}) {
  return <label className="portfolio-selector portfolio-profile"><span>Profile</span><select value={value} onChange={event => onChange(event.target.value)} disabled={disabled}>
    {all && <><option value="">All profiles</option><option value="unassigned">Unassigned accounts</option></>}
    {!all && profiles.length === 0 && <option value="">No profiles</option>}
    {profiles.map(profile => <option key={profile.user_id} value={profile.user_id}>{profile.display_name}{!profile.active ? " (inactive)" : ""}</option>)}
  </select></label>;
}

export function setPortfolioQuery(userId: string, account?: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("user_id", userId);
  if (account) url.searchParams.set("account", account);
  else url.searchParams.delete("account");
  window.history.replaceState(null, "", url);
}

export function PortfolioHeader({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <header className="portfolio-header"><div><span className="portfolio-eyebrow">Portfolio workspace</span><h1>{title}</h1><p>{description}</p></div>{children}</header>;
}
