"""Проверки хранилища: таблицы, время, ограничения базы."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.db import init_db, make_engine
from app.models import Employee, TimeEntry


def test_employee_survives_restart(db_url: str) -> None:
    """Сотрудник, записанный в базу, виден после «перезапуска».

    Перезапуск моделируем честно: первое подключение закрываем полностью,
    затем открываем новое к тому же файлу.
    """
    first = make_engine(db_url)
    init_db(first)
    with Session(first) as s:
        s.add(Employee(name="Иван Тестов"))
        s.commit()
    first.dispose()

    second = make_engine(db_url)
    with Session(second) as s:
        names = s.scalars(select(Employee.name)).all()
    second.dispose()

    assert names == ["Иван Тестов"]


def test_time_is_read_back_in_utc_with_timezone(session: Session) -> None:
    """Время из базы возвращается с часовым поясом UTC и без сдвига.

    Время в другом поясе (Минск, UTC+3) должно сохраниться как тот же момент.
    """
    employee = Employee(name="Анна Пример")
    session.add(employee)
    session.flush()
    minsk = timezone(timedelta(hours=3))
    started = datetime(2026, 11, 7, 12, 0, tzinfo=minsk)
    session.add(TimeEntry(employee_id=employee.id, description="Отчёт", started_at=started))
    session.commit()
    session.expire_all()  # заставляем перечитать из базы, а не из памяти

    entry = session.scalars(select(TimeEntry)).one()

    assert entry.started_at.tzinfo is not None
    assert entry.started_at.utcoffset() == timedelta(0)
    assert entry.started_at == started  # тот же момент: 12:00 по Минску = 09:00 UTC
    assert entry.ended_at is None  # таймер ещё идёт


def test_database_forbids_second_active_timer(session: Session) -> None:
    """База не даёт сотруднику иметь два незавершённых таймера одновременно."""
    employee = Employee(name="Пётр Образец")
    session.add(employee)
    session.flush()
    now = datetime.now(timezone.utc)
    session.add(TimeEntry(employee_id=employee.id, description="Первая", started_at=now))
    session.flush()
    session.add(TimeEntry(employee_id=employee.id, description="Вторая", started_at=now))

    with pytest.raises(IntegrityError):
        session.flush()


def test_finished_entries_do_not_block_each_other(session: Session) -> None:
    """Завершённых записей у сотрудника может быть сколько угодно."""
    employee = Employee(name="Ольга Проба")
    session.add(employee)
    session.flush()
    start = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)
    for hour in range(3):
        session.add(TimeEntry(
            employee_id=employee.id,
            description=f"Задача {hour}",
            started_at=start + timedelta(hours=hour),
            ended_at=start + timedelta(hours=hour, minutes=30),
        ))
    session.commit()

    assert len(session.scalars(select(TimeEntry)).all()) == 3


def test_entry_requires_existing_employee(session: Session) -> None:
    """Нельзя записать время сотруднику, которого нет в базе."""
    session.add(TimeEntry(
        employee_id=999,
        description="Сирота",
        started_at=datetime.now(timezone.utc),
    ))

    with pytest.raises(IntegrityError):
        session.flush()


def test_time_without_timezone_is_rejected(session: Session) -> None:
    """Время без часового пояса не записывается: оно неоднозначно."""
    employee = Employee(name="Сергей Наивный")
    session.add(employee)
    session.flush()
    session.add(TimeEntry(
        employee_id=employee.id,
        description="Без пояса",
        started_at=datetime(2026, 11, 7, 12, 0),  # нет tzinfo
    ))

    # SQLAlchemy заворачивает наш ValueError в StatementError — проверяем оба слоя.
    with pytest.raises(StatementError, match="без часового пояса") as caught:
        session.flush()
    assert isinstance(caught.value.orig, ValueError)
