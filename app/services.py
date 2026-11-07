"""Правила работы приложения: сотрудники, таймер, отчёт.

Здесь нет ничего про HTTP — только данные и правила. Поэтому каждое правило
можно объяснить и проверить отдельно. Текущее время передаётся параметром
(now), а не берётся внутри: так тесты могут «управлять часами».
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Employee, TimeEntry
from app.schemas import EmployeeReport, EntryOut, ReportOut


class EmployeeNotFound(Exception):
    """Сотрудника с таким id нет."""


class TimerAlreadyRunning(Exception):
    """У сотрудника уже идёт таймер — второй запустить нельзя."""


class NoRunningTimer(Exception):
    """Останавливать нечего: таймер не запущен."""


def create_employee(db: Session, name: str) -> Employee:
    """Добавляет сотрудника. Имя уже проверено схемой (не пустое, не длинное)."""
    employee = Employee(name=name)
    db.add(employee)
    db.commit()
    return employee


def list_employees(db: Session) -> list[Employee]:
    """Все сотрудники в порядке добавления."""
    return list(db.scalars(select(Employee).order_by(Employee.id)))


def get_running_entry(db: Session, employee_id: int) -> TimeEntry | None:
    """Идущий таймер сотрудника или None."""
    return db.scalars(
        select(TimeEntry).where(TimeEntry.employee_id == employee_id, TimeEntry.ended_at.is_(None))
    ).first()


def _require_employee(db: Session, employee_id: int) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise EmployeeNotFound(f"Сотрудник {employee_id} не найден")
    return employee


def start_timer(db: Session, employee_id: int, description: str, now: datetime) -> TimeEntry:
    """Запускает таймер. Второй одновременный таймер у сотрудника запрещён.

    Проверка двойная: сначала спрашиваем базу, есть ли идущий таймер, —
    это даёт понятную ошибку. Если два запроса пришли одновременно и оба
    проверку прошли, второй всё равно упрётся в уникальный индекс базы.
    """
    _require_employee(db, employee_id)
    if get_running_entry(db, employee_id) is not None:
        raise TimerAlreadyRunning("Таймер уже запущен — сначала остановите его")
    entry = TimeEntry(employee_id=employee_id, description=description, started_at=now)
    db.add(entry)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise TimerAlreadyRunning("Таймер уже запущен — сначала остановите его") from error
    return entry


def stop_timer(db: Session, employee_id: int, now: datetime) -> TimeEntry:
    """Останавливает идущий таймер сотрудника."""
    _require_employee(db, employee_id)
    entry = get_running_entry(db, employee_id)
    if entry is None:
        raise NoRunningTimer("Таймер не запущен — останавливать нечего")
    # Защита от «конца раньше начала», если часы сервера отвели назад.
    entry.ended_at = max(now, entry.started_at)
    db.commit()
    return entry


def _seconds_between(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds()))


def entry_out(entry: TimeEntry, now: datetime) -> EntryOut:
    """Запись для ответа; длительность идущего таймера — до текущего момента."""
    end = entry.ended_at if entry.ended_at is not None else now
    return EntryOut(
        id=entry.id,
        employee_id=entry.employee_id,
        description=entry.description,
        started_at=entry.started_at,
        ended_at=entry.ended_at,
        is_running=entry.ended_at is None,
        duration_seconds=_seconds_between(entry.started_at, end),
    )


def build_report(db: Session, start: datetime, end: datetime, now: datetime) -> ReportOut:
    """Отчёт за период [start, end): записи и сумма по каждому сотруднику.

    Правила:
    - в отчёт попадает запись, которая хоть частично пересекает период;
    - считается только часть внутри периода (работа 23:00–01:00 в отчёте
      за один день даёт свой час);
    - идущий таймер считается до текущего момента;
    - сотрудники без записей тоже показываются — с нулём.
    """
    entries = db.scalars(
        select(TimeEntry)
        .where(
            TimeEntry.started_at < end,
            (TimeEntry.ended_at.is_(None)) | (TimeEntry.ended_at > start),
        )
        .order_by(TimeEntry.started_at)
    ).all()

    rows: list[EmployeeReport] = []
    for employee in list_employees(db):
        own = [e for e in entries if e.employee_id == employee.id]
        total = 0
        for entry in own:
            entry_end = entry.ended_at if entry.ended_at is not None else now
            total += _seconds_between(max(entry.started_at, start), min(entry_end, end))
        rows.append(EmployeeReport(
            employee_id=employee.id,
            name=employee.name,
            total_seconds=total,
            entries=[entry_out(e, now) for e in own],
        ))

    return ReportOut(start=start, end=end, total_seconds=sum(r.total_seconds for r in rows), employees=rows)
