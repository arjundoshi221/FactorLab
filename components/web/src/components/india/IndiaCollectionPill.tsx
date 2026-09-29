import type { IndiaCollectionStatus } from "../../indiaTypes";

const labels: Record<IndiaCollectionStatus, string> = {
  collecting: "Active collection",
  historical: "Historical only",
  not_configured: "Not configured",
};

export function IndiaCollectionPill({ status }: { status: IndiaCollectionStatus }) {
  return <span className={`collection-pill collection-pill--${status}`}>{labels[status]}</span>;
}
