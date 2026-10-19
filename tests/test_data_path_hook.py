"""The tracked settings template is allowed without permitting runtime secrets."""

import pytest
from tools.hooks.check_no_data_paths import is_banned


@pytest.mark.parametrize(
    "path", [".env", ".env.production", ".env.example.local", "data/raw.bin", "logs\\run.log"]
)
def test_runtime_paths_are_blocked(path: str) -> None:
    assert is_banned(path)


def test_generated_settings_template_is_allowed() -> None:
    assert not is_banned(".env.example")
