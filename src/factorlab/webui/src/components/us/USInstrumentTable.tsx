import { Fragment } from "react";

import type { USInstrument } from "../../usTypes";
import { USStatusPill } from "./USStatusPill";

function when(value: string | null | undefined) {
  if (!value) return "Not collected";
  return new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeZone: "America/New_York" }).format(new Date(value));
}
export function USInstrumentTable({ items, selected, onSelect }: {
  items: USInstrument[]; selected: USInstrument | null; onSelect: (item: USInstrument) => void;
}) {
  return <div className="table-scroll"><table><thead><tr><th>Instrument</th><th>Minute collection</th>
    <th>Minute history</th><th>Daily collection</th><th>Daily history</th></tr></thead>
    <tbody>{items.map(item => <tr key={item.instrument_id}
      className={selected?.instrument_id === item.instrument_id ? "us-selected" : ""}>
      <td><button className="us-symbol" onClick={() => onSelect(item)}>{item.symbol}</button>
        <small className="us-subtitle">{item.name} · {item.exchange_code}</small></td>
      {item.series.map(series => <Fragment key={series.resolution}><td><USStatusPill status={series.status} />
        <small className="us-subtitle">{series.source} · {series.actual} / {series.expected} bars
          {series.error && ` · ${series.error}`}</small></td>
        <td>{when(series.available_from)}
          <small className="us-subtitle">through {when(series.last_bar)}</small></td></Fragment>)}
    </tr>)}</tbody></table></div>;
}
