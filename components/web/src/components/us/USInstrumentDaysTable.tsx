import type { USDay, USResolution } from "../../usTypes";

export function USInstrumentDaysTable({ days, resolution }: { days: USDay[]; resolution: USResolution }) {
  return <><h3>Recent session checks</h3><div className="table-scroll"><table><thead><tr>
    <th>Session</th><th>Resolution</th><th>Collected</th><th>Expected</th><th>Missing</th>
  </tr></thead><tbody>{days.filter(day => day.resolution === resolution).map(day =>
    <tr key={`${day.trade_date}-${day.resolution}`}><td>{day.trade_date}</td><td>{day.resolution}</td>
      <td>{day.actual}</td><td>{day.expected}</td><td>{day.missing}</td></tr>)}</tbody></table></div></>;
}
