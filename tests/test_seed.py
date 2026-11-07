"""Демо-данные: вымышленные сотрудники и неделя работы одной командой."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import KIND_BREAK, KIND_LUNCH, KIND_WORK, Employee, TimeEntry
from app.seed import DEMO_NAMES, SeedRefused, seed
from app.services import build_report

# Среда, 11 ноября 2026, 12:30 по Минску — середина рабочего дня.
MINSK = timezone(timedelta(hours=3))
NOW = datetime(2026, 11, 11, 12, 30, tzinfo=MINSK)


def test_seed_fills_empty_database(session: Session) -> None:
    created = seed(session, NOW)

    names = session.scalars(select(Employee.name).order_by(Employee.id)).all()
    kinds = set(session.scalars(select(TimeEntry.kind)))
    assert names == list(DEMO_NAMES)
    assert created == len(DEMO_NAMES)
    assert kinds == {KIND_WORK, KIND_BREAK, KIND_LUNCH}


def test_seed_refuses_database_with_people(session: Session) -> None:
    """Демо не смешивается с настоящими данными: в непустую базу — отказ."""
    session.add(Employee(name="Настоящий сотрудник"))
    session.commit()

    with pytest.raises(SeedRefused):
        seed(session, NOW)

    assert session.scalar(select(func.count()).select_from(Employee)) == 1


def test_seed_can_append_when_asked(session: Session) -> None:
    session.add(Employee(name="Настоящий сотрудник"))
    session.commit()

    seed(session, NOW, append=True)

    assert session.scalar(select(func.count()).select_from(Employee)) == 1 + len(DEMO_NAMES)


def test_seed_is_repeatable(engine, db_url: str) -> None:
    """Одинаковое «сейчас» — одинаковые данные: демо предсказуемо."""
    from app.db import init_db, make_engine

    other = make_engine(db_url.replace("test.db", "other.db"))
    init_db(other)
    with Session(engine) as a, Session(other) as b:
        seed(a, NOW)
        seed(b, NOW)
        rows_a = a.execute(select(TimeEntry.description, TimeEntry.started_at).order_by(TimeEntry.id)).all()
        rows_b = b.execute(select(TimeEntry.description, TimeEntry.started_at).order_by(TimeEntry.id)).all()
    other.dispose()
    assert rows_a == rows_b


def test_seeded_week_looks_like_real_work(session: Session) -> None:
    """За прошлые будни у каждого — от 6 до 9 часов работы в день, обед есть каждый день."""
    seed(session, NOW)
    monday = datetime(2026, 11, 9, tzinfo=MINSK)  # понедельник этой недели
    report = build_report(session, monday, monday + timedelta(days=2), NOW)  # пн и вт — полные дни

    for row in report.employees:
        assert 2 * 6 * 3600 <= row.total_seconds <= 2 * 9 * 3600, row.name
        assert row.lunch_seconds >= 2 * 30 * 60, row.name


def test_today_shows_live_picture(session: Session) -> None:
    """Сегодня кто-то работает, кто-то на перерыве — оценщик сразу видит живые часы."""
    seed(session, NOW)

    running_kinds = session.scalars(select(TimeEntry.kind).where(TimeEntry.ended_at.is_(None))).all()

    assert KIND_WORK in running_kinds
    assert set(running_kinds) & {KIND_BREAK, KIND_LUNCH}
    assert all(e <= NOW for e in session.scalars(select(TimeEntry.started_at)))  # ничего «из будущего»
