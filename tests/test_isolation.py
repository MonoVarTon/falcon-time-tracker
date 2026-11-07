"""Тесты не должны касаться рабочей базы приложения."""
from pathlib import Path

from app import db

PROJECT_DIR = Path(__file__).resolve().parent.parent


def test_tests_never_use_working_database() -> None:
    """Подключение приложения в тестах указывает на временный файл, а не на timetracker.db."""
    assert "timetracker.db" not in str(db.engine.url)


def test_working_database_file_is_not_created_by_tests() -> None:
    """Файл рабочей базы в папке проекта не появляется от запуска тестов."""
    assert not (PROJECT_DIR / "timetracker.db").exists()
