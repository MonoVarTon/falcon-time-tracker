"""Общие заготовки для тестов.

Каждый тест получает собственный временный файл базы: тесты не мешают
друг другу и не трогают рабочую базу приложения.
"""
import os
import tempfile
from collections.abc import Iterator

# Тесты никогда не трогают рабочую базу. app.db читает адрес базы при импорте,
# поэтому подставляем временный файл ДО импорта приложения (строки ниже).
# Ставим принудительно: даже если у разработчика задан свой DATABASE_URL.
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='ftt-tests-')}/app.db"

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.db import init_db, make_engine


@pytest.fixture
def db_url(tmp_path) -> str:
    """Адрес временной базы SQLite в папке, которую pytest удалит сам."""
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def engine(db_url: str) -> Iterator[Engine]:
    """Подключение к временной базе с уже созданными таблицами."""
    eng = make_engine(db_url)
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Сессия для работы с временной базой; закрывается после теста."""
    with Session(engine) as s:
        yield s
