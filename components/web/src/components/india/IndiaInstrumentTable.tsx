import type { IndiaCheckStatus, IndiaInstrumentRow } from "../../indiaTypes";
import { IndiaCheckPill } from "./IndiaCheckPill";
import { IndiaCollectionPill } from "./IndiaCollectionPill";

const numberFormatter = new Intl.NumberFormat("en-IN");

function formatTime(value: string | null): string {
  if (!value) return "Never";
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Kolkata",
  }).format(new Date(value));
}

export function IndiaInstrumentTable({
  instruments,
  statusFilter,
  tradingDate,
}: {
  instruments: IndiaInstrumentRow[];
  statusFilter: "all" | IndiaCheckStatus;
  tradingDate: string;
}) {
  const visible = instruments.filter(
    (instrument) => statusFilter === "all" || instrument.check_status === statusFilter,
  );

  return (
    <div className="table-wrap india-table-wrap">
      <table className="india-instrument-table">
        <thead>
          <tr>
            <th>Instrument</th>
            <th>Market</th>
            <th>Collection</th>
            <th className="numeric">All rows</th>
            <th className="numeric">Days</th>
            <th className="numeric">Selected day</th>
            <th className="numeric">Coverage</th>
            <th>Latest ingest</th>
            <th>Checks</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((instrument) => (
            <tr key={instrument.listing_id}>
              <td data-label="Instrument">
                <a className="instrument-link" href={`/india/instruments/${instrument.listing_id}?date=${tradingDate}`}>
                  <strong>{instrument.symbol}</strong>
                  <small>{instrument.name || "Unnamed instrument"}</small>
                </a>
              </td>
              <td data-label="Market">
                <span>{instrument.exchange_code || "—"}</span>
                <small>{instrument.instrument_type || "—"}</small>
              </td>
              <td data-label="Collection">
                <IndiaCollectionPill status={instrument.collection_status} />
                <small>
                  {instrument.expected_series > 0
                    ? `${instrument.expected_series} expected series`
                    : instrument.data_points > 0
                      ? "Stored data, no longer active"
                      : "Reference metadata only"}
                </small>
              </td>
              <td data-label="All rows" className="numeric">{numberFormatter.format(instrument.data_points)}</td>
              <td data-label="Days" className="numeric">{numberFormatter.format(instrument.trading_days)}</td>
              <td data-label="Selected day" className="numeric">
                {numberFormatter.format(instrument.selected_data_points)}
                <small>{instrument.selected_unique_series} series</small>
              </td>
              <td data-label="Coverage" className="numeric">
                {instrument.coverage_percent === null ? "—" : `${instrument.coverage_percent.toFixed(1)}%`}
                <small>{instrument.expected_data_points ? `${instrument.expected_data_points} expected` : "No expectation"}</small>
              </td>
              <td data-label="Latest ingest">{formatTime(instrument.last_ingested_at)}</td>
              <td data-label="Checks">
                <IndiaCheckPill status={instrument.check_status} />
                <small className="status-reason">{instrument.check_reason}</small>
                <a className="instrument-open" href={`/india/instruments/${instrument.listing_id}?date=${tradingDate}`}>Open instrument →</a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {visible.length === 0 && <p className="no-results">No instruments match this check filter.</p>}
    </div>
  );
}
