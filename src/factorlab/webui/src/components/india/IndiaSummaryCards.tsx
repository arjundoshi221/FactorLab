import type { IndiaDashboardData, IndiaInstrumentRow } from "../../indiaTypes";

const numberFormatter = new Intl.NumberFormat("en-IN");

function SummaryCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

export function IndiaSummaryCards({
  dashboard,
  instruments,
}: {
  dashboard: IndiaDashboardData;
  instruments: IndiaInstrumentRow[];
}) {
  const reviewCount = instruments.filter(
    (item) => item.check_status === "attention" || item.check_status === "missing",
  ).length;
  return (
    <section className="metric-grid" aria-label="India market totals">
      <SummaryCard
        label="Reference instruments"
        value={numberFormatter.format(dashboard.reference_instruments)}
        detail="Latest synchronized India universe"
      />
      <SummaryCard
        label="Expected series"
        value={numberFormatter.format(dashboard.expected_series)}
        detail={`${numberFormatter.format(dashboard.series_with_data)} series observed`}
      />
      <SummaryCard
        label="Selected-day coverage"
        value={`${dashboard.coverage_percent.toFixed(1)}%`}
        detail={`${numberFormatter.format(dashboard.data_points)} / ${numberFormatter.format(dashboard.expected_data_points)} points`}
      />
      <SummaryCard
        label="Checks to review"
        value={numberFormatter.format(reviewCount)}
        detail={`${dashboard.anomaly_count} detected anomaly finding(s)`}
      />
    </section>
  );
}
