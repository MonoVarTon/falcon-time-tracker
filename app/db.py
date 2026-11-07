"""Подключение к базе данных SQLite.

Здесь три вещи: как создать подключение (make_engine), как создать таблицы
(init_db) и как выдать сессию на один запрос (get_db).
"""
import os
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Файл базы по умолчанию лежит рядом с проектом. Адрес можно переопределить
# переменной окружения DATABASE_URL — так тесты подставляют временную базу.
DEFAULT_DATABASE_URL = "sqlite:///./timetracker.db"


class Base(DeclarativeBase):
    """Общий родитель всех таблиц: по нему SQLAlchemy знает, что создавать."""


def make_engine(url: str) -> Engine:
    """Создаёт подключение к базе и включает проверку внешних ключей.

    SQLite по умолчанию НЕ проверяет внешние ключи — запись со ссылкой
    на несуществующего сотрудника прошла бы молча. Поэтому на каждом новом
    соединении включаем PRAGMA foreign_keys.
    """
    engine = create_engine(
        url,
        # FastAPI может обработать запрос в другом потоке, чем тот,
        # где открыто соединение; без этого SQLite выдаёт ошибку.
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_db(engine: Engine) -> None:
    """Создаёт таблицы, которых ещё нет. Существующие данные не трогает."""
    from app import models  # noqa: F401 — импорт регистрирует таблицы в Base

    Base.metadata.create_all(engine)


engine = make_engine(os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Выдаёт сессию на один запрос и гарантированно закрывает её после."""
    with SessionLocal() as session:
        yield session
