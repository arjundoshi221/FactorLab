"""Create the initial ClickHouse raw, reference, and market tables."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from factorlab.storage.clickhouse import ClickHouseStorage  # noqa: E402


def main() -> None:
    storage = ClickHouseStorage.from_environment()
    ddl_dir = PROJECT_ROOT / "sql" / "clickhouse"
    for ddl_path in sorted(ddl_dir.glob("*.sql")):
        ddl = ddl_path.read_text(encoding="utf-8")
        for statement in ddl.split(";"):
            statement = "\n".join(
                line
                for line in statement.splitlines()
                if not line.strip().startswith("--")
            ).strip()
            if statement:
                storage.client.command(statement)
        print(f"Applied {ddl_path.name}")
    storage.seed_india_reference_data()
    print("ClickHouse schema foundation is ready.")


if __name__ == "__main__":
    main()
