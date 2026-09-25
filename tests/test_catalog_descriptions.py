import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def generator():
    path = ROOT / "scripts" / "generate_catalog_descriptions.py"
    spec = importlib.util.spec_from_file_location("generate_catalog_descriptions", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checked_in_descriptions_match_the_generator():
    module = generator()
    assert module.OUTPUT.read_text(encoding="utf-8") == module.render(), (
        "Run python scripts/generate_catalog_descriptions.py"
    )


def test_every_v2_table_has_a_curated_summary():
    sql = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "sql/clickhouse/v2").glob("wave_*.sql"))
    defined = set(re.findall(r"CREATE (?:TABLE|VIEW) IF NOT EXISTS ([a-z_]+\.[a-z0-9_]+)", sql))
    curated = json.loads((ROOT / "src/factorlab/api/catalog_curated.json").read_text(encoding="utf-8"))
    missing = sorted(defined - set(curated["tables"]))
    assert not missing, f"Add plain-language entries to catalog_curated.json: {missing}"
    assert {name.split(".", 1)[0] for name in defined} <= set(curated["namespaces"])


def test_column_comments_are_parsed_without_revision_noise():
    module = generator()
    columns = module.column_descriptions(
        "CREATE TABLE IF NOT EXISTS t.x (\n"
        "    a     UInt8,        -- first line\n"
        "                        --   continued\n"
        "    -- section --\n"
        "    b     String,       -- ISO code; added rev 11 per F16 — default\n"
        "    -- stray note\n"
        "    --   not a continuation\n"
        ") ENGINE = MergeTree ORDER BY a;\n"
    )
    assert columns["t.x"] == {"a": "first line continued", "b": "ISO code; default"}
