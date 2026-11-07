"""Подключение к базе данных SQLite.

Здесь три вещи: как создать подключение (make_engine), как создать таблицы
(init_db) и как выдать сессию на один запрос (get_db).
"""
import os
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event, inspect, text
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
    """Создаёт таблицы, которых ещё нет, и обновляет старые. Данные не трогает."""
    from app import models  # noqa: F401 — импорт регистрирует таблицы в Base

    Base.metadata.create_all(engine)
    _upgrade_schema(engine)


def _upgrade_schema(engine: Engine) -> None:
    """Маленькая миграция: добавляет колонки, появившиеся в новых версиях.

    create_all создаёт только НОВЫЕ таблицы и не меняет существующие.
    Поэтому база, созданная до появления перерывов, осталась бы без колонки
    kind — и приложение упало бы. Здесь мы добавляем её, если её нет.
    Старые записи получают тип «work» (значение по умолчанию).
    Запуск повторно безопасен: колонка уже есть — ничего не делаем.
    """
    columns = {column["name"] for column in inspect(engine).get_columns("time_entries")}
    if "kind" not in columns:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE time_entries ADD COLUMN kind VARCHAR(10) NOT NULL DEFAULT 'work'"
            ))


engine = make_engine(os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Выдаёт сессию на один запрос и гарантированно закрывает её после."""
    with SessionLocal() as session:
        yield session
