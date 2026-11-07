"""Таблицы базы данных: сотрудники и записи рабочего времени."""
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.db import Base

NAME_MAX_LENGTH = 100
DESCRIPTION_MAX_LENGTH = 500

# Тип записи: работа над задачей, перерыв или обед.
KIND_WORK = "work"
KIND_BREAK = "break"
KIND_LUNCH = "lunch"
ENTRY_KINDS = (KIND_WORK, KIND_BREAK, KIND_LUNCH)


class UTCDateTime(TypeDecorator[datetime]):
    """Время, которое всегда хранится и читается в UTC.

    Зачем: SQLite не умеет хранить часовой пояс. Обычный DateTime(timezone=True)
    сохраняет «12:00+03:00» как «12:00» — без пояса и без перевода в UTC,
    и отчёт молча съезжает на разницу поясов (найдено тестом, см. docs/ai-log.md).
    Поэтому при записи переводим в UTC, при чтении возвращаем метку UTC.
    """

    impl = DateTime
    cache_ok = True  # тип без настроек — SQLAlchemy может кэшировать запросы с ним

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Перед записью: переводим в UTC и убираем метку пояса (SQLite её не хранит)."""
        if value is None:
            return None
        if value.tzinfo is None:
            # Время без пояса неоднозначно: непонятно, чьи это «12:00».
            # Лучше сразу упасть, чем тихо записать неверный момент.
            raise ValueError("Время без часового пояса: передайте aware-datetime")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """После чтения: возвращаем метку UTC, которую убрали при записи."""
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class Employee(Base):
    """Сотрудник. Имена не уникальны: в компании могут быть тёзки."""

    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))


class TimeEntry(Base):
    """Одна запись рабочего времени.

    Пока ended_at пустой (None) — таймер идёт. Остановка таймера —
    это запись времени в ended_at. Перерыв и обед — тоже записи,
    со своим типом (kind); правило «один идущий таймер» действует на все.
    """

    __tablename__ = "time_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    description: Mapped[str] = mapped_column(String(DESCRIPTION_MAX_LENGTH))
    # server_default нужен для обновления старой базы: существующие записи станут «работой».
    kind: Mapped[str] = mapped_column(String(10), default=KIND_WORK, server_default=KIND_WORK)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)

    __table_args__ = (
        CheckConstraint(f"kind IN {ENTRY_KINDS}", name="ck_time_entries_kind"),
        # Не больше одного активного таймера на сотрудника — на уровне самой базы.
        # Индекс уникален только среди строк, где ended_at пустой.
        Index(
            "uq_one_active_timer_per_employee",
            "employee_id",
            unique=True,
            sqlite_where=ended_at.is_(None),
        ),
    )
