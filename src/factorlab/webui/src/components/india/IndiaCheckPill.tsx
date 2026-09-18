import type { IndiaCheckStatus } from "../../indiaTypes";

const labels: Record<IndiaCheckStatus, string> = {
  healthy: "Passed",
  attention: "Review",
  missing: "Missing",
  not_expected: "Not expected",
};

export function IndiaCheckPill({ status }: { status: IndiaCheckStatus }) {
  return (
    <span className={`status status--${status}`}>
      <span className="status__dot" aria-hidden="true" />
      {labels[status]}
    </span>
  );
}
