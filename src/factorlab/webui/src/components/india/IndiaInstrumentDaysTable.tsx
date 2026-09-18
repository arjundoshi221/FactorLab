import type { IndiaInstrumentDay, IndiaInstrumentRow } from "../../indiaTypes";
import { IndiaCheckPill } from "./IndiaCheckPill";

const numberFormatter = new Intl.NumberFormat("en-IN");

function timeOnly(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Kolkata",
  }).format(new Date(value));
}

export function IndiaInstrumentDaysTable({
  instrument,
  days,
  loading,
  selectedDate,
  onSelectDate,
}: {
  instrument: IndiaInstrumentRow;
  days: IndiaInstrumentDay[];
  loading: boolean;
  selectedDate?: string;
  onSelectDate?: (date: string) => void;
}) {
  return (
    <section className="instrument-detail" aria-labelledby="instrument-history-title">
      <div className="section-heading instrument-detail__heading">
        <div>
          <span className="eyebrow">Exchange calendar</span>
          <h2 id="instrument-history-title">Session health</h2>
          <p>Select a date to load its stored one-minute candles.</p>
        </div>
        <div className="instrument-facts">
          <span>{instrument.unique_series} unique series</span>
          <span>{numberFormatter.format(instrument.data_points)} stored rows</span>
        </div>
      </div>
      {instrument.collection_status === "not_configured" ? (
        <p className="inline-loading instrument-not-configured" role="status">
          This instrument is available in the Upstox reference universe, but it is not configured
          for collection and has no stored candles yet.
        </p>
      ) : loading ? (
        <p className="inline-loading" role="status">Checking exchange sessions…</p>
      ) : (
        <div className="table-wrap">
          <table className="india-days-table">
            <thead>
              <tr>
                <th>Trading date</th>
                <th>Session</th>
                <th className="numeric">Rows</th>
                <th className="numeric">Series</th>
                <th className="numeric">Coverage</th>
                <th>Observed window</th>
                <th className="numeric">Value issues</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {days.map((day) => (
                <tr className={selectedDate === day.trading_date ? "is-selected" : ""} key={day.trading_date}>
                  <td data-label="Trading date">{onSelectDate ? <button className="session-link" type="button" onClick={() => onSelectDate(day.trading_date)}>{day.trading_date}</button> : <code>{day.trading_date}</code>}</td>
                  <td data-label="Session">{day.market_status.replaceAll("_", " ")}</td>
                  <td data-label="Rows" className="numeric">{numberFormatter.format(day.data_points)}</td>
                  <td data-label="Series" className="numeric">{day.unique_series}/{day.expected_series}</td>
                  <td data-label="Coverage" className="numeric">
                    {day.coverage_percent === null ? "—" : `${day.coverage_percent.toFixed(1)}%`}
                    <small>{day.expected_data_points ? `${day.expected_data_points} expected` : "No expectation"}</small>
                  </td>
                  <td data-label="Observed window">
                    {timeOnly(day.first_bar_time)}–{timeOnly(day.last_bar_time)}
                  </td>
                  <td data-label="Value issues" className="numeric">
                    {numberFormatter.format(day.quality_issue_count)}
                    <small>{day.duplicate_versions} replacement versions</small>
                  </td>
                  <td data-label="Result">
                    <IndiaCheckPill status={day.check_status} />
                    <small className="status-reason">{day.check_reason}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {days.length === 0 && <p className="no-results">No sessions are available for this range.</p>}
        </div>
      )}
    </section>
  );
}
