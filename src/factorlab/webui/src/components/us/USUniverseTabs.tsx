import type { USScope } from "../../usTypes";

const tabs: Array<[USScope, string]> = [
  ["all", "All listed"], ["minute", "Liquid 250"],
  ["daily_only", "Daily only"], ["no_data", "No daily data"],
];
export function USUniverseTabs({ value, onChange }: { value: USScope; onChange: (scope: USScope) => void }) {
  return <div className="universe-tabs" role="tablist" aria-label="US universe scope">
    {tabs.map(([scope, label]) => <button key={scope} role="tab" aria-selected={value === scope}
      className={value === scope ? "is-active" : ""} onClick={() => onChange(scope)}>{label}</button>)}
  </div>;
}
