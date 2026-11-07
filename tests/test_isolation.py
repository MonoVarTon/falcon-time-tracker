"""Тесты не должны касаться рабочей базы приложения."""
from pathlib import Path

from app import db

PROJECT_DIR = Path(__file__).resolve().parent.parent


def test_tests_use_database_outside_project_folder() -> None:
    """В тестах приложение подключено к временному файлу вне папки проекта.

    Раньше здесь была проверка «в папке проекта нет файла базы». Она была
    неверной: при обычном запуске приложение и должно создать timetracker.db
    в папке проекта. Проверка упала на настоящей базе владельца — удаление
    по её «совету» уничтожило бы данные (см. docs/ai-log.md).
    """
    database_file = Path(db.engine.url.database).resolve()

    assert database_file.name != "timetracker.db"
    assert PROJECT_DIR not in database_file.parents
