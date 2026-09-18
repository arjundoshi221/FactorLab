export function USStatusPill({ status }: { status: string }) {
  const tone = ["complete", "ready", "success"].includes(status) ? "healthy"
    : ["error", "failed", "auth_required", "stale"].includes(status) ? "missing" : "attention";
  return <span className={`status status--${tone}`}>
    <span className="status__dot" aria-hidden="true" />{status.replaceAll("_", " ")}
  </span>;
}
