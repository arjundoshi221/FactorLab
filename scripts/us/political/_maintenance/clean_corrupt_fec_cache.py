"""Find truncated/corrupt JSON files under data/political/raw/fec/.

Run with --dry-run (default) first to see what would be removed; pass
--delete to actually unlink. Crash recovery: when the machine dies mid-
fetch, the file gets a partial write and json.loads chokes with weird
encoding errors (e.g. 'utf-32-be' codec). Re-running the backfill replays
these as cache hits and fails the same way until the bad files are gone.

Uses a process pool because the bottleneck is per-file json.loads on
~33K files. On a typical OneDrive-backed disk a single-process scan
takes ~10 min; with 8 workers it's <2 min.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from multiprocessing import Pool, cpu_count
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from factorlab.shared.paths import raw_dir

ROOT = raw_dir("fec")


def _check_file(path: Path) -> tuple[Path, str | None]:
    """Worker: returns (path, error_message_or_None)."""
    try:
        json.loads(path.read_bytes())
        return path, None
    except (json.JSONDecodeError, UnicodeDecodeError, UnicodeError) as e:
        return path, str(e)[:80]
    except OSError as e:
        # File missing / locked / etc. — surface the error but don't crash
        return path, f"OSError: {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete", action="store_true",
                    help="actually unlink corrupt files (default: dry-run)")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--workers", type=int, default=0,
                    help="worker process count (default: cpu_count)")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"no such dir: {root}", file=sys.stderr)
        return 2

    print(f"counting files under {root}...", flush=True)
    all_files = list(root.rglob("*.json"))
    total = len(all_files)
    print(f"  {total} files", flush=True)
    if total == 0:
        return 0

    workers = args.workers or max(2, cpu_count() - 1)
    print(f"scanning with {workers} worker processes...", flush=True)

    corrupt: list[tuple[Path, str]] = []
    PROGRESS_EVERY = 1000
    chunksize = max(50, total // (workers * 8))

    with Pool(processes=workers) as pool:
        for i, (path, err) in enumerate(
            pool.imap_unordered(_check_file, all_files, chunksize=chunksize),
            start=1,
        ):
            if err is not None:
                corrupt.append((path, err))
            if i % PROGRESS_EVERY == 0 or i == total:
                pct = i * 100 // total
                print(f"  scanned {i}/{total} ({pct}%) — {len(corrupt)} corrupt so far",
                      flush=True)

    print(f"\nscanned {total} files, {len(corrupt)} corrupt")
    for f, err in corrupt[:30]:
        print(f"  {f.relative_to(root)}  --  {err}")
    if len(corrupt) > 30:
        print(f"  ... +{len(corrupt) - 30} more")

    if args.delete and corrupt:
        deleted = 0
        for f, _ in corrupt:
            try:
                f.unlink()
                deleted += 1
            except OSError as e:
                print(f"  unlink failed: {f.name}  --  {e}")
        print(f"deleted {deleted}/{len(corrupt)} files")
    elif corrupt:
        print("\n(dry-run — pass --delete to remove)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
