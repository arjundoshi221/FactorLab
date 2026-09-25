import type { PipelineRun } from "../../dataTypes";
import { formatNumber, formatRelative, formatTime } from "../../shared/format";

export function RunList({ runs, now }: { runs: PipelineRun[]; now: number }) {
  if (!runs.length) return <p className="data-muted">No runs in the last seven days.</p>;
  return (
    <ul className="data-runs">
      {runs.map((run) => (
        <li key={run.run_id}>
          <span className={`data-run-status data-run-status--${run.status}`}>{run.status}</span>
          <div>
            <strong>{run.pipeline}</strong>
            <small title={formatTime(run.started_at)}>
              {formatRelative(run.started_at, now)} · {formatNumber(run.rows_written)} rows
              {run.requested_series > 1 && ` · ${run.successful_series}/${run.requested_series} series`}
            </small>
            {run.error && <small className="data-run-error">{run.error}</small>}
          </div>
        </li>
      ))}
    </ul>
  );
}
