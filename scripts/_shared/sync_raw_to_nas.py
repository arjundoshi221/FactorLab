"""One-way sync of raw vendor data from <repo>/data/ to E:\\NAS\\factorlab\\raw\\.

**Operational model:** the repo is the canonical / primary location for raw
data. NAS is an **additive-only backup mirror**. Code reads from
``factorlab.shared.paths.raw_dir(source)`` which defaults to ``<repo>/data``.
``FACTORLAB_RAW_ROOT`` does NOT flip to NAS — the data stays in the repo so
that if NAS is unavailable the system keeps working.

Subsequent runs are incremental: a file is copied only if it doesn't exist at
the destination, or its source ``mtime`` is newer than the destination's
(with 1-second tolerance for FAT-style filesystems).

By default the sync is **additive-only**: files deleted from the source are
NOT removed from the destination. This is intentional — a backup that
mirrors deletions can lose data when the user accidentally clears the source.
Pass ``--mirror`` to enable destructive sync (deletions on source propagate
to dest); not recommended for routine backup runs.

Three modes::

    --dry-run    (default) scan + tabulate; write nothing
    --sync       perform the sync
    --verify     sha256 a 1% random sample on both sides; non-zero on mismatch

Source/dest mapping is hardcoded::

    data/political/raw/      ↔ <DEST>/political/raw/
    data/eodhd/              ↔ <DEST>/eodhd/
    data/in/live/            ↔ <DEST>/in/live/
    data/upstox/instruments/ ↔ <DEST>/upstox/instruments/

Destination resolution order:
    1. --dest CLI arg
    2. FACTORLAB_NAS_BACKUP_ROOT env var
    3. error (force the operator to be explicit on first run)

Note we use a dedicated ``FACTORLAB_NAS_BACKUP_ROOT`` env, NOT
``FACTORLAB_RAW_ROOT``. The latter is what the application code reads when
it looks up raw paths; flipping it would relocate the working data set,
which is precisely what we are NOT doing here.

Examples::

    python scripts/_shared/sync_raw_to_nas.py --dry-run
    python scripts/_shared/sync_raw_to_nas.py --dest E:/NAS/factorlab/raw --sync
    python scripts/_shared/sync_raw_to_nas.py --dest E:/NAS/factorlab/raw --verify
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import random
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Make ``factorlab`` importable when run as a script.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.shared.paths import REPO_ROOT  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sync_raw_to_nas")


# (source_relative_to_repo, dest_relative_to_dest_root)
SOURCES: list[tuple[str, str]] = [
    ("data/political/raw",      "political/raw"),
    ("data/eodhd",              "eodhd"),
    ("data/in/live",            "in/live"),
    ("data/upstox/instruments", "upstox/instruments"),
]

# Tolerate 1-second mtime drift between filesystems (FAT precision, network
# share clock skew).
MTIME_TOLERANCE_SEC = 1.0


# ── Helpers ─────────────────────────────────────────────────────────────────


@dataclass
class SyncStats:
    name: str
    files_in_source: int = 0
    files_already_current: int = 0
    files_to_copy: int = 0
    files_copied: int = 0
    bytes_to_copy: int = 0
    bytes_copied: int = 0
    files_deleted_from_dest: int = 0


def _human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _needs_copy(src: Path, dst: Path) -> bool:
    """True if dst is missing or older than src (with tolerance)."""
    if not dst.exists():
        return True
    try:
        return src.stat().st_mtime > dst.stat().st_mtime + MTIME_TOLERANCE_SEC
    except OSError:
        return True


def _sync_one(
    src_root: Path,
    dst_root: Path,
    name: str,
    *,
    apply_: bool,
    mirror: bool,
) -> SyncStats:
    """Walk *src_root*. For each file: copy to *dst_root* iff needed.

    If *apply_* is False, only counts; nothing is written.
    If *mirror* is True, files present at dest but absent from src are deleted.
    """
    st = SyncStats(name=name)
    if not src_root.exists():
        log.info("[%s] source missing (skip): %s", name, src_root)
        return st

    log.info("[%s] scanning %s ...", name, src_root)
    seen_rel: set[Path] = set()
    for src in src_root.rglob("*"):
        if not src.is_file():
            continue
        st.files_in_source += 1
        rel = src.relative_to(src_root)
        seen_rel.add(rel)
        dst = dst_root / rel
        if not _needs_copy(src, dst):
            st.files_already_current += 1
            continue
        try:
            size = src.stat().st_size
        except OSError as e:
            log.warning("[%s] stat failed for %s: %s", name, src, e)
            continue
        st.files_to_copy += 1
        st.bytes_to_copy += size
        if apply_:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                st.files_copied += 1
                st.bytes_copied += size
                if st.files_copied % 5000 == 0:
                    log.info("[%s] copied %d files so far (%s)",
                             name, st.files_copied,
                             _human_bytes(st.bytes_copied))
            except OSError as e:
                log.error("[%s] copy failed %s → %s: %s", name, src, dst, e)
        if st.files_in_source % 25000 == 0:
            log.info("[%s] scanned %d files so far", name, st.files_in_source)

    if mirror and dst_root.exists():
        log.warning("[%s] --mirror enabled; pruning files absent from source", name)
        for dst in dst_root.rglob("*"):
            if not dst.is_file():
                continue
            rel = dst.relative_to(dst_root)
            if rel in seen_rel:
                continue
            log.warning("[%s] mirror-delete: %s", name, rel)
            if apply_:
                try:
                    dst.unlink()
                    st.files_deleted_from_dest += 1
                except OSError as e:
                    log.error("[%s] delete failed %s: %s", name, dst, e)

    log.info(
        "[%s] done — source=%d, current=%d, %s=%d (%s)",
        name,
        st.files_in_source,
        st.files_already_current,
        "copied" if apply_ else "would copy",
        st.files_copied if apply_ else st.files_to_copy,
        _human_bytes(st.bytes_copied if apply_ else st.bytes_to_copy),
    )
    return st


# ── Modes ───────────────────────────────────────────────────────────────────


def cmd_dry_run(dest_root: Path, mirror: bool) -> int:
    log.info("DRY-RUN -- no writes; reporting what --sync would do")
    log.info("dest:   %s", dest_root)
    log.info("mirror: %s", mirror)
    stats: list[SyncStats] = []
    for src_rel, dst_rel in SOURCES:
        src = REPO_ROOT / src_rel
        dst = dest_root / dst_rel
        stats.append(_sync_one(src, dst, src_rel, apply_=False, mirror=mirror))

    grand_files = sum(s.files_to_copy for s in stats)
    grand_bytes = sum(s.bytes_to_copy for s in stats)

    log.info("")
    log.info("=" * 78)
    log.info(f"{'source':<32}{'source files':>14}{'current':>10}"
             f"{'to copy':>10}{'size to copy':>12}")
    log.info("-" * 78)
    for s in stats:
        log.info(
            f"{s.name:<32}{s.files_in_source:>14}{s.files_already_current:>10}"
            f"{s.files_to_copy:>10}{_human_bytes(s.bytes_to_copy):>12}"
        )
    log.info("-" * 78)
    log.info(f"{'TOTAL':<32}{'':>14}{'':>10}{grand_files:>10}"
             f"{_human_bytes(grand_bytes):>12}")
    log.info("=" * 78)
    if grand_files == 0:
        log.info("Nothing to do — destination is already up to date.")
    else:
        log.info("Rerun with --sync to copy.")
    return 0


def cmd_sync(dest_root: Path, mirror: bool) -> int:
    log.info("SYNC -- copying changes to %s", dest_root)
    if not dest_root.exists():
        log.info("creating destination root: %s", dest_root)
        dest_root.mkdir(parents=True, exist_ok=True)

    stats: list[SyncStats] = []
    for src_rel, dst_rel in SOURCES:
        src = REPO_ROOT / src_rel
        dst = dest_root / dst_rel
        stats.append(_sync_one(src, dst, src_rel, apply_=True, mirror=mirror))

    grand_copied = sum(s.files_copied for s in stats)
    grand_bytes = sum(s.bytes_copied for s in stats)
    grand_deleted = sum(s.files_deleted_from_dest for s in stats)

    # Write a small status file at the dest so an operator landing here later
    # knows what this directory is.
    status_path = dest_root / "_LAST_SYNC.txt"
    try:
        status_path.write_text(
            f"FactorLab raw-data backup mirror.\n"
            f"Primary location: <repo>/data/  (this is a BACKUP, not the canonical store).\n"
            f"Last sync (UTC):  {datetime.now(timezone.utc).isoformat()}\n"
            f"Last sync copied: {grand_copied} files, {_human_bytes(grand_bytes)}\n"
            f"Mirror mode:      {mirror}\n\n"
            f"Re-sync with: python scripts/_shared/sync_raw_to_nas.py "
            f"--dest {dest_root.as_posix()} --sync\n",
            encoding="utf-8",
        )
    except OSError as e:
        log.warning("could not write %s: %s", status_path, e)

    log.info("TOTAL copied: %d files, %s", grand_copied, _human_bytes(grand_bytes))
    if grand_deleted:
        log.warning("TOTAL deleted from dest (--mirror): %d", grand_deleted)
    log.info("Next: run --verify to sample-check sha256 on both sides.")
    return 0


def cmd_verify(dest_root: Path, sample_pct: float = 0.01) -> int:
    log.info("VERIFY -- sha256 a %.1f%% random sample", sample_pct * 100)
    failures: list[tuple[str, Path, str, str]] = []
    checked = 0
    for src_rel, dst_rel in SOURCES:
        src = REPO_ROOT / src_rel
        dst = dest_root / dst_rel
        if not src.exists():
            log.info("[%s] source missing — skip", src_rel)
            continue
        if not dst.exists():
            log.error("[%s] destination missing: %s", src_rel, dst)
            return 2
        files = [p for p in src.rglob("*") if p.is_file()]
        if not files:
            log.info("[%s] no files to verify", src_rel)
            continue
        n_sample = max(1, int(len(files) * sample_pct))
        sample = random.sample(files, min(n_sample, len(files)))
        log.info("[%s] sampling %d / %d files", src_rel, len(sample), len(files))
        for src_file in sample:
            rel = src_file.relative_to(src)
            dst_file = dst / rel
            if not dst_file.exists():
                failures.append((src_rel, rel, "missing on dest", ""))
                continue
            src_hash = _sha256(src_file)
            dst_hash = _sha256(dst_file)
            checked += 1
            if src_hash != dst_hash:
                failures.append((src_rel, rel, src_hash, dst_hash))
                log.error("[%s] hash mismatch: %s", src_rel, rel)

    log.info("verified %d files", checked)
    if failures:
        log.error("VERIFY FAILED -- %d mismatches", len(failures))
        for name, rel, a, b in failures[:20]:
            log.error("  [%s] %s  src=%s dst=%s", name, rel, a[:12], b[:12])
        return 2
    log.info("VERIFY OK -- sample matches on both sides")
    return 0


# ── Entrypoint ──────────────────────────────────────────────────────────────


def _resolve_dest(args: argparse.Namespace) -> Path | None:
    if args.dest:
        return Path(args.dest).resolve()
    env = os.environ.get("FACTORLAB_NAS_BACKUP_ROOT")
    if env:
        return Path(env).resolve()
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dest",
        help="destination root (overrides FACTORLAB_NAS_BACKUP_ROOT env var)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="(default) scan and report; write nothing")
    mode.add_argument("--sync", dest="sync_", action="store_true",
                      help="copy new / changed files from source to destination")
    mode.add_argument("--verify", action="store_true",
                      help="sha256 a 1%% random sample on both sides")
    parser.add_argument(
        "--mirror",
        action="store_true",
        help="also DELETE files at destination that are no longer in source "
             "(destructive; off by default for backup-safety)",
    )
    args = parser.parse_args()

    dest = _resolve_dest(args)
    if dest is None:
        log.error("destination required — pass --dest E:/NAS/factorlab/raw "
                  "or set FACTORLAB_NAS_BACKUP_ROOT in your environment")
        return 1

    if args.sync_:
        return cmd_sync(dest, mirror=args.mirror)
    if args.verify:
        return cmd_verify(dest)
    return cmd_dry_run(dest, mirror=args.mirror)


if __name__ == "__main__":
    sys.exit(main())
