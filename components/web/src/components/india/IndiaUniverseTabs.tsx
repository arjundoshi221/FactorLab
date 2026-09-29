import type { IndiaCollectionScope, IndiaInstrumentPage } from "../../indiaTypes";

const numberFormatter = new Intl.NumberFormat("en-IN");

export function IndiaUniverseTabs({
  page,
  selected,
  onSelect,
}: {
  page: IndiaInstrumentPage;
  selected: IndiaCollectionScope;
  onSelect: (scope: IndiaCollectionScope) => void;
}) {
  const tabs: Array<{ scope: IndiaCollectionScope; label: string; count: number }> = [
    { scope: "all", label: "All instruments", count: page.reference_total },
    { scope: "collecting", label: "Active collection", count: page.collecting_total },
    { scope: "historical", label: "Historical only", count: page.historical_total },
    { scope: "not_configured", label: "Not configured", count: page.not_configured_total },
  ];

  return (
    <div className="universe-tabs" aria-label="Instrument collection views">
      {tabs.map((tab) => (
        <button
          className={selected === tab.scope ? "is-active" : ""}
          type="button"
          key={tab.scope}
          aria-pressed={selected === tab.scope}
          onClick={() => onSelect(tab.scope)}
        >
          <span>{tab.label}</span>
          <strong>{numberFormatter.format(tab.count)}</strong>
        </button>
      ))}
    </div>
  );
}
