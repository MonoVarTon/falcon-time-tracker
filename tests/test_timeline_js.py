"""Запускает проверки ленты дня (JavaScript) вместе с остальными тестами.

Если Node.js не установлен — проверка пропускается, а не падает:
для работы приложения Node.js не нужен, только для этих тестов.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js не установлен")
def test_timeline_logic_in_javascript() -> None:
    result = subprocess.run(
        ["node", "--test", "tests/js/timeline.test.mjs"],
        cwd=PROJECT_DIR, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
