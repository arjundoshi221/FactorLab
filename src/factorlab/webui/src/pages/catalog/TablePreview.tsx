import { useMemo, useState, type FormEvent } from "react";

import { EmptyState, ErrorBanner } from "../../components/ui";
import type { Cell, CatalogColumn, CatalogTableDetail, RowsPage } from "../../dataTypes";
import { errorMessage, useRemote } from "../../shared/api";
import { formatNumber, formatTime } from "../../shared/format";
import { useUrlParams } from "../../shared/urlState";

const OPERATOR_LABELS: Record<string, string> = {
  eq: "is", neq: "is not", contains: "contains", prefix: "starts with", in: "is one of",
  gt: "is greater than", gte: "is at least", lt: "is less than", lte: "is at most",
  null: "is empty", notnull: "is not empty",
};
const TIME_OPERATOR_LABELS: Record<string, string> = { gte: "is on or after", lt: "is before", eq: "is" };
const PAGE_SIZES = [25, 50, 100, 200];
const DAY = 86_400_000;

interface ActiveFilter { column: string; operator: string; value: string }

function operatorLabel(column: CatalogColumn | undefined, operator: string): string {
  if (column && (column.type_class === "datetime" || column.type_class === "date")) {
    return TIME_OPERATOR_LABELS[operator] ?? OPERATOR_LABELS[operator] ?? operator;
  }
  return OPERATOR_LABELS[operator] ?? operator;
}

function readFilters(params: URLSearchParams): ActiveFilter[] {
  const filters: ActiveFilter[] = [];
  params.forEach((raw, key) => {
    if (!key.startsWith("f.")) return;
    const split = raw.indexOf(":");
    filters.push({
      column: key.slice(2),
      operator: split < 0 ? raw : raw.slice(0, split),
      value: split < 0 ? "" : raw.slice(split + 1),
    });
  });
  return filters;
}

function dayOnly(value: string | null | undefined): string {
  return value ? value.slice(0, 10) : "";
}

/** The inclusive last day of an exclusive window end. */
function lastDay(value: string | null | undefined): string {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? "" : new Date(parsed.valueOf() - 1).toISOString().slice(0, 10);
}

function renderCell(value: Cell, column: CatalogColumn | undefined) {
  if (value === null) return <span className="data-cell--null">empty</span>;
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return <span className="data-cell--number">{formatNumber(value)}</span>;
  if (column?.type_class === "datetime") return <span title={formatTime(value)}>{value.replace("T", " ").replace("+00:00", "Z")}</span>;
  if (column && ["integer", "number", "decimal"].includes(column.type_class)) {
    return <span className="data-cell--number">{value}</span>;
  }
  return <span title={value.length > 60 ? value : undefined}>{value}</span>;
}

function FilterBuilder({ columns, onAdd }: { columns: CatalogColumn[]; onAdd: (filter: ActiveFilter) => void }) {
  const filterable = columns.filter((column) => column.operators.length > 0);
  const [columnName, setColumnName] = useState(filterable[0]?.name ?? "");
  const column = filterable.find((item) => item.name === columnName);
  const [operator, setOperator] = useState(column?.operators.find((item) => item === "eq") ?? column?.operators[0] ?? "");
  const [value, setValue] = useState("");
  const needsValue = operator !== "null" && operator !== "notnull";

  function chooseColumn(name: string) {
    setColumnName(name);
    const next = filterable.find((item) => item.name === name);
    const operators = next?.operators ?? [];
    setOperator(operators.includes("eq") ? "eq" : operators.includes("gte") ? "gte" : operators[0] ?? "");
    setValue("");
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!column || !operator || (needsValue && !value.trim())) return;
    const normalized = column.type_class === "datetime" && value.length === 16 ? `${value}:00Z` : value.trim();
    onAdd({ column: column.name, operator, value: needsValue ? normalized : "" });
    setValue("");
  }

  if (!filterable.length) return null;
  const inputType = column?.type_class === "date" ? "date"
    : column?.type_class === "datetime" ? "datetime-local"
    : ["integer", "number", "decimal"].includes(column?.type_class ?? "") ? "number" : "text";
  return (
    <form className="data-filter-builder" onSubmit={submit} aria-label="Add a filter">
      <label>
        <span>Column</span>
        <select value={columnName} onChange={(event) => chooseColumn(event.target.value)}>
          {filterable.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
        </select>
      </label>
      <label>
        <span>Condition</span>
        <select value={operator} onChange={(event) => setOperator(event.target.value)}>
          {(column?.operators ?? []).map((item) => <option key={item} value={item}>{operatorLabel(column, item)}</option>)}
        </select>
      </label>
      {needsValue && (
        <label className="data-filter-builder__value">
          <span>Value{column?.type_class === "datetime" ? " (UTC)" : ""}</span>
          {column?.type_class === "bool" ? (
            <select value={value} onChange={(event) => setValue(event.target.value)}>
              <option value="">Choose…</option><option value="true">true</option><option value="false">false</option>
            </select>
          ) : (
            <input
              type={inputType}
              step={inputType === "number" ? "any" : undefined}
              value={value}
              placeholder={operator === "in" ? "Comma-separated values" : "Value"}
              onChange={(event) => setValue(event.target.value)}
            />
          )}
        </label>
      )}
      <button type="submit" disabled={needsValue && !value.trim()}>Add filter</button>
    </form>
  );
}

export function TablePreview({ detail }: { detail: CatalogTableDetail }) {
  const [params, setParams] = useUrlParams();
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const columns = useMemo(() => detail.column_details.filter((column) => !column.hidden), [detail]);
  const byName = useMemo(() => new Map(columns.map((column) => [column.name, column])), [columns]);
  const filters = readFilters(params);
  const limit = Number(params.get("limit")) || 50;
  const page = Math.max(Number(params.get("page")) || 1, 1);
  const sort = params.get("sort") ?? detail.preview.time_column ?? "";
  const direction = params.get("dir") === "asc" ? "asc" : "desc";
  const start = params.get("start") ?? "";
  const end = params.get("end") ?? "";

  const query = new URLSearchParams();
  filters.forEach((item) => query.append(`f.${item.column}`, item.operator + (item.value ? `:${item.value}` : "")));
  if (params.get("sort")) query.set("sort", sort);
  if (params.get("dir")) query.set("dir", direction);
  if (start) query.set("start", start);
  if (end) query.set("end", end);
  const csvHref = `/hub/api/v1/catalog/tables/${detail.name}/rows.csv${query.toString() ? `?${query}` : ""}`;
  query.set("limit", String(limit));
  query.set("offset", String((page - 1) * limit));
  const rows = useRemote<RowsPage>(detail.preview.enabled ? `/hub/api/v1/catalog/tables/${detail.name}/rows?${query}` : null);

  function update(change: (next: URLSearchParams) => void) {
    setParams((next) => { change(next); next.delete("page"); });
  }

  function writeFilters(next: URLSearchParams, list: ActiveFilter[]) {
    [...next.keys()].filter((key) => key.startsWith("f.")).forEach((key) => next.delete(key));
    list.forEach((item) => next.append(`f.${item.column}`, item.operator + (item.value ? `:${item.value}` : "")));
  }

  function setWindow(days: number | null) {
    update((next) => {
      if (days === null || !detail.preview.default_end) {
        next.delete("start");
        next.delete("end");
        return;
      }
      const anchor = new Date(detail.preview.default_end).valueOf();
      next.set("end", new Date(anchor).toISOString());
      next.set("start", new Date(anchor - days * DAY).toISOString());
    });
  }

  function setDay(key: "start" | "end", value: string) {
    update((next) => {
      if (!value) {
        next.delete(key);
        return;
      }
      const midnight = new Date(`${value}T00:00:00Z`).valueOf();
      // The "To" day is inclusive, so the exclusive window end is the following midnight.
      next.set(key, new Date(key === "end" ? midnight + DAY : midnight).toISOString());
    });
  }

  function toggleSort(column: string) {
    update((next) => {
      if (sort === column) next.set("dir", direction === "desc" ? "asc" : "desc");
      else {
        next.set("sort", column);
        next.set("dir", "desc");
      }
    });
  }

  if (!detail.preview.enabled) {
    return <EmptyState title="Row previews are off for this table.">{detail.preview.reason}</EmptyState>;
  }

  const data = rows.data;
  const shown = data ? data.columns.filter((column) => !hidden.has(column)) : [];
  const indexes = data ? shown.map((column) => data.columns.indexOf(column)) : [];
  const firstRow = data ? data.offset + 1 : 0;
  const lastRow = data ? data.offset + data.rows.length : 0;
  const windowDays = detail.preview.max_window_days;
  const presets = [1, 7, 31, 90, 365].filter((days) => days <= windowDays);

  return (
    <section className="data-preview" aria-label="Row preview">
      <div className="data-preview__controls">
        {detail.preview.windowed && (
          <fieldset className="data-window">
            <legend>Time window on <code>{detail.preview.time_column}</code></legend>
            <div className="segmented">
              <button type="button" className={!start && !end ? "is-active" : ""} aria-pressed={!start && !end} onClick={() => setWindow(null)}>Latest</button>
              {presets.map((days) => (
                <button key={days} type="button" onClick={() => setWindow(days)}>{days === 1 ? "1 day" : `${days} days`}</button>
              ))}
            </div>
            <label><span>From (UTC)</span><input type="date" value={dayOnly(start || data?.window_start)} onChange={(event) => setDay("start", event.target.value)} /></label>
            <label><span>To (UTC)</span><input type="date" value={lastDay(end || data?.window_end)} onChange={(event) => setDay("end", event.target.value)} /></label>
            <small>Large tables are previewed one window at a time, up to {windowDays} days.</small>
          </fieldset>
        )}
        <FilterBuilder columns={columns} onAdd={(filter) => update((next) => writeFilters(next, [...filters, filter]))} />
      </div>

      {filters.length > 0 && (
        <ul className="data-active-filters" aria-label="Active filters">
          {filters.map((item, index) => (
            <li key={`${item.column}-${index}`}>
              <code>{item.column}</code> {operatorLabel(byName.get(item.column), item.operator)} {item.value && <strong>{item.value}</strong>}
              <button type="button" aria-label={`Remove filter on ${item.column}`}
                onClick={() => update((next) => writeFilters(next, filters.filter((_, position) => position !== index)))}>×</button>
            </li>
          ))}
          <li><button type="button" className="data-link-button" onClick={() => update((next) => writeFilters(next, []))}>Clear all</button></li>
        </ul>
      )}

      <div className="data-preview__bar">
        <span>
          {data ? (data.rows.length ? `Rows ${formatNumber(firstRow)}–${formatNumber(lastRow)}` : "No rows") : "Loading rows…"}
          {data?.window_start && ` · ${data.window_start.slice(0, 16).replace("T", " ")} → ${data.window_end?.slice(0, 16).replace("T", " ")} UTC`}
        </span>
        <div className="data-preview__actions">
          <details className="data-columns-menu">
            <summary>Columns ({shown.length}/{data?.columns.length ?? columns.length})</summary>
            <div>
              {(data?.columns ?? columns.map((column) => column.name)).map((column) => (
                <label key={column}>
                  <input type="checkbox" checked={!hidden.has(column)} onChange={() => setHidden((current) => {
                    const next = new Set(current);
                    if (next.has(column)) next.delete(column); else next.add(column);
                    return next;
                  })} />
                  {column}
                </label>
              ))}
            </div>
          </details>
          <label className="data-page-size">
            <span className="sr-only">Rows per page</span>
            <select value={limit} onChange={(event) => update((next) => next.set("limit", event.target.value))}>
              {PAGE_SIZES.map((size) => <option key={size} value={size}>{size} per page</option>)}
            </select>
          </label>
          {detail.preview.csv_enabled && (
            <a className="button-link" href={csvHref} download>Download CSV <small>up to {formatNumber(detail.preview.max_csv_rows)} rows</small></a>
          )}
        </div>
      </div>

      {data?.notes.map((note) => <p className="data-note" key={note}>{note}</p>)}
      {rows.error ? <ErrorBanner onRetry={rows.refresh}>{errorMessage(rows.error, "Rows could not be loaded.")}</ErrorBanner> : null}

      {data && data.rows.length > 0 && (
        <div className={`data-grid-wrap${rows.loading ? " is-loading" : ""}`}>
          <table className="data-grid">
            <thead>
              <tr>
                {shown.map((column) => (
                  <th key={column} aria-sort={sort === column ? (direction === "asc" ? "ascending" : "descending") : "none"}>
                    <button type="button" onClick={() => toggleSort(column)} title={byName.get(column)?.description ?? byName.get(column)?.type}>
                      {column}{sort === column ? (direction === "asc" ? " ↑" : " ↓") : ""}
                      <small>{byName.get(column)?.friendly_type}</small>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {indexes.map((index, position) => (
                    <td key={shown[position]}>{renderCell(row[index], byName.get(shown[position]))}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && data.rows.length === 0 && !rows.error && (
        <EmptyState
          title="No rows match this view."
          action={detail.preview.windowed ? <button type="button" className="button-secondary" onClick={() => setWindow(Math.min(31, windowDays))}>Widen to {Math.min(31, windowDays)} days</button> : undefined}
        >
          {filters.length ? "Try removing a filter." : detail.preview.windowed ? "No rows were stored in this time window." : "This table is empty."}
        </EmptyState>
      )}

      {data && (data.offset > 0 || data.has_more) && (
        <nav className="pagination" aria-label="Pages">
          <button type="button" className="button-secondary" disabled={page <= 1}
            onClick={() => setParams((next) => next.set("page", String(page - 1)))}>Previous</button>
          <span>Page {page}</span>
          <button type="button" className="button-secondary" disabled={!data.has_more}
            onClick={() => setParams((next) => next.set("page", String(page + 1)))}>Next</button>
        </nav>
      )}
      {data && data.truncated_cells > 0 && <p className="data-note">{data.truncated_cells} long values are shortened; download the CSV for longer text.</p>}
    </section>
  );
}
