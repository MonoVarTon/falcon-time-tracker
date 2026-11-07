"""Правила работы приложения: сотрудники, таймер, отчёт.

Здесь нет ничего про HTTP — только данные и правила. Поэтому каждое правило
можно объяснить и проверить отдельно. Текущее время передаётся параметром
(now), а не берётся внутри: так тесты могут «управлять часами».
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import KIND_BREAK, KIND_LUNCH, KIND_WORK, Employee, TimeEntry
from app.schemas import DaySummary, EmployeeReport, EntryOut, ReportOut


class EmployeeNotFound(Exception):
    """Сотрудника с таким id нет."""


class TimerAlreadyRunning(Exception):
    """У сотрудника уже идёт таймер — второй запустить нельзя."""


class NoRunningTimer(Exception):
    """Останавливать нечего: таймер не запущен."""


class MissingDescription(Exception):
    """Вернуться к работе нельзя: нет ни новой задачи, ни прошлой."""


# Описания по умолчанию: у перерыва и обеда задачу вписывать не нужно.
DEFAULT_DESCRIPTIONS = {KIND_BREAK: "Перерыв", KIND_LUNCH: "Обед"}


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


def _description_for(db: Session, employee_id: int, kind: str, description: str | None) -> str:
    """Описание записи: введённое, стандартное для перерыва/обеда или прошлая задача."""
    if description is not None:
        return description
    if kind in DEFAULT_DESCRIPTIONS:
        return DEFAULT_DESCRIPTIONS[kind]
    last_work = db.scalars(
        select(TimeEntry)
        .where(TimeEntry.employee_id == employee_id, TimeEntry.kind == KIND_WORK)
        .order_by(TimeEntry.started_at.desc())
    ).first()
    if last_work is None:
        raise MissingDescription("Укажите задачу: прошлой работы, чтобы её продолжить, нет")
    return last_work.description


def _save_new_entry(db: Session, entry: TimeEntry) -> TimeEntry:
    """Сохраняет новую запись; гонку двух одновременных запросов ловит индекс базы."""
    db.add(entry)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise TimerAlreadyRunning("Таймер уже запущен — сначала остановите его") from error
    return entry


def start_timer(
    db: Session, employee_id: int, description: str | None, now: datetime, kind: str = KIND_WORK
) -> TimeEntry:
    """Запускает таймер. Второй одновременный таймер у сотрудника запрещён.

    Проверка двойная: сначала спрашиваем базу, есть ли идущий таймер, —
    это даёт понятную ошибку. Если два запроса пришли одновременно и оба
    проверку прошли, второй всё равно упрётся в уникальный индекс базы.
    """
    _require_employee(db, employee_id)
    if get_running_entry(db, employee_id) is not None:
        raise TimerAlreadyRunning("Таймер уже запущен — сначала остановите его")
    text = _description_for(db, employee_id, kind, description)
    return _save_new_entry(db, TimeEntry(employee_id=employee_id, kind=kind, description=text, started_at=now))


def switch_timer(
    db: Session, employee_id: int, kind: str, description: str | None, now: datetime
) -> TimeEntry:
    """Одним действием закрывает текущую запись и открывает новую.

    Так работают кнопки «Перерыв», «Обед» и «Вернуться к работе»: между
    записями нет ни секунды разрыва, и сотруднику не нужно жать два раза.
    Если ничего не идёт — просто запускает новую запись.
    """
    _require_employee(db, employee_id)
    text = _description_for(db, employee_id, kind, description)
    running = get_running_entry(db, employee_id)
    if running is not None:
        running.ended_at = max(now, running.started_at)
        # Закрытие записываем в базу явно и до новой записи. SQLAlchemy и сам
        # пишет UPDATE раньше INSERT (проверено логом запросов), но мы не хотим
        # зависеть от его внутреннего порядка: иначе уникальный индекс
        # «один идущий таймер» увидел бы две открытые записи сразу.
        db.flush()
    return _save_new_entry(db, TimeEntry(employee_id=employee_id, kind=kind, description=text, started_at=now))


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
        kind=entry.kind,
        is_running=entry.ended_at is None,
        duration_seconds=_seconds_between(entry.started_at, end),
    )


def _entries_overlapping(db: Session, start: datetime, end: datetime) -> list[TimeEntry]:
    """Записи, которые хоть частично попадают в отрезок [start, end)."""
    return list(db.scalars(
        select(TimeEntry)
        .where(
            TimeEntry.started_at < end,
            (TimeEntry.ended_at.is_(None)) | (TimeEntry.ended_at > start),
        )
        .order_by(TimeEntry.started_at)
    ))


def _seconds_by_kind(entries: list[TimeEntry], start: datetime, end: datetime, now: datetime) -> dict[str, int]:
    """Сколько секунд работы, перерывов и обеда внутри отрезка [start, end).

    Единое правило подсчёта для отчёта и для «отработано сегодня»:
    считается только часть записи внутри отрезка, идущая запись — до now.
    """
    totals = {KIND_WORK: 0, KIND_BREAK: 0, KIND_LUNCH: 0}
    for entry in entries:
        entry_end = entry.ended_at if entry.ended_at is not None else now
        totals[entry.kind] += _seconds_between(max(entry.started_at, start), min(entry_end, end))
    return totals


def day_summaries(db: Session, since: datetime, now: datetime) -> dict[int, DaySummary]:
    """Итоги дня каждого сотрудника: с начала суток (since) до текущего момента.

    Нужны, чтобы на экране было видно «отработано сегодня» целиком,
    а не только текущий отрезок, который начинается заново после обеда.
    """
    entries = _entries_overlapping(db, since, now)
    result = {}
    for employee in list_employees(db):
        own = [e for e in entries if e.employee_id == employee.id]
        totals = _seconds_by_kind(own, since, now, now)
        result[employee.id] = DaySummary(
            work_seconds=totals[KIND_WORK],
            break_seconds=totals[KIND_BREAK],
            lunch_seconds=totals[KIND_LUNCH],
        )
    return result


def build_report(db: Session, start: datetime, end: datetime, now: datetime) -> ReportOut:
    """Отчёт за период [start, end): записи и сумма по каждому сотруднику.

    Правила:
    - в отчёт попадает запись, которая хоть частично пересекает период;
    - считается только часть внутри периода (работа 23:00–01:00 в отчёте
      за один день даёт свой час);
    - идущий таймер считается до текущего момента;
    - работа, перерывы и обед считаются отдельно: total_seconds — только работа;
    - сотрудники без записей тоже показываются — с нулём.
    """
    entries = _entries_overlapping(db, start, end)

    rows: list[EmployeeReport] = []
    for employee in list_employees(db):
        own = [e for e in entries if e.employee_id == employee.id]
        by_kind = _seconds_by_kind(own, start, end, now)
        rows.append(EmployeeReport(
            employee_id=employee.id,
            name=employee.name,
            total_seconds=by_kind[KIND_WORK],
            break_seconds=by_kind[KIND_BREAK],
            lunch_seconds=by_kind[KIND_LUNCH],
            entries=[entry_out(e, now) for e in own],
        ))

    return ReportOut(
        start=start,
        end=end,
        total_seconds=sum(r.total_seconds for r in rows),
        break_seconds=sum(r.break_seconds for r in rows),
        lunch_seconds=sum(r.lunch_seconds for r in rows),
        employees=rows,
    )
