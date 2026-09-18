"""Cross-country, cross-domain shared plumbing.

Submodules:
    paths      — REPO_ROOT and env-configurable RAW/STATE/LOG/TOKEN roots
    runtime/   — (Phase 3) logging, signals, dedup, market windows, lock, supervised
    notify/    — (Phase 4) Outlook + SMTP + webhook + JSONL backends
    ingest/    — (Phase 5) HTTPClient + State + Backfiller protocol
    storage/   — Postgres engine + write helpers (Phase 6 migration target)
    config/    — country / universe / source YAML loaders (Phase 6 migration target)
"""
