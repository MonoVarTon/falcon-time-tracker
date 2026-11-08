"""Правила работы приложения: сотрудники, таймер, отчёт.

Здесь нет ничего про HTTP — только данные и правила. Поэтому каждое правило
можно объяснить и проверить отдельно. Текущее время передаётся параметром
(now), а не берётся внутри: так тесты могут «управлять часами».
"""
from datetime import datetime, time, timedelta, timezone, tzinfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import COMPANY_TIMEZONE_NAME, COMPANY_TZ, company_day_bounds
from app.models import KIND_BREAK, KIND_LUNCH, KIND_WORK, MAX_LIMIT_HOURS, Employee, TimeEntry
from app.schemas import DaySummary, EmployeeReport, EntryOut, ReportDay, ReportOut, SuggestedEnd


class EmployeeNotFound(Exception):
    """Сотрудника с таким id нет."""


class TimerAlreadyRunning(Exception):
    """У сотрудника уже идёт таймер — второй запустить нельзя."""


class NoRunningTimer(Exception):
    """Останавливать нечего: таймер не запущен."""


class MissingDescription(Exception):
    """Вернуться к работе нельзя: нет ни новой задачи, ни прошлой."""


class EntryNotFound(Exception):
    """Записи с таким id нет."""


class LimitTooHigh(Exception):
    """Продление сверх суток."""


class InvalidCorrection(Exception):
    """Исправленное время невозможно: раньше начала, в будущем или налезает на следующую запись."""


# За сколько прошлых дней считаем обычное время ухода.
SUGGESTION_DAYS = 7
DEFAULT_CORRECTION_NOTE = "Время окончания исправлено вручную"


# Таймер, идущий дольше этого, скорее всего забыли выключить.
LONG_RUNNING_HOURS = 10

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


def auto_stop_overdue(db: Session, now: datetime) -> int:
    """Закрывает идущие записи, перешедшие свой предел (по умолчанию 12 ч).

    Закрываем ровно на отметке «начало + предел», а не «сейчас»: если в
    приложение заглянули через 30 часов, в отчёт всё равно попадёт 12.
    Вызывается перед каждым запросом — отдельная фоновая программа не нужна.
    Возвращает, сколько записей закрыто.
    """
    running = db.scalars(select(TimeEntry).where(TimeEntry.ended_at.is_(None))).all()
    stopped = 0
    for entry in running:
        limit_end = entry.started_at + timedelta(hours=entry.limit_hours)
        if now >= limit_end:
            entry.ended_at = limit_end
            entry.auto_stopped = True
            stopped += 1
    if stopped:
        db.commit()
    return stopped


def extend_limit(db: Session, entry_id: int, approved_by: str, hours: int) -> TimeEntry:
    """Продлевает рабочий день идущей записи по подтверждению руководителя.

    Входа по паролю в приложении нет (задание его не требует), поэтому
    подтверждение — это записанное имя руководителя, а не проверка роли.
    """
    entry = _get_entry(db, entry_id)
    if entry.ended_at is not None:
        raise NoRunningTimer("Запись уже закрыта — продлевать нечего")
    if entry.limit_hours + hours > MAX_LIMIT_HOURS:
        raise LimitTooHigh(f"Рабочий день нельзя продлить больше чем до {MAX_LIMIT_HOURS} часов")
    entry.limit_hours += hours
    entry.approved_by = approved_by
    db.commit()
    return entry


def _get_entry(db: Session, entry_id: int) -> TimeEntry:
    entry = db.get(TimeEntry, entry_id)
    if entry is None:
        raise EntryNotFound(f"Запись {entry_id} не найдена")
    return entry


def _days_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "дню"
    return "дням"


def suggest_end(db: Session, entry_id: int, local: tzinfo, now: datetime) -> SuggestedEnd:
    """Подсказка для исправления: среднее время ухода сотрудника за прошлые 7 дней.

    «Уход» — конец последней записи дня. Сутки считаются в поясе local
    (по умолчанию — пояс компании). В среднее НЕ идут
    автоостановленные и уже исправленные записи: это догадки, а не факты —
    иначе подсказка начала бы подтверждать сама себя.
    """
    entry = _get_entry(db, entry_id)
    entry_day = entry.started_at.astimezone(local).date()
    day_start, _ = company_day_bounds(entry_day, local)
    history_start, _ = company_day_bounds(entry_day - timedelta(days=SUGGESTION_DAYS), local)
    history = db.scalars(
        select(TimeEntry).where(
            TimeEntry.employee_id == entry.employee_id,
            TimeEntry.ended_at.is_not(None),
            TimeEntry.auto_stopped.is_(False),
            TimeEntry.corrected.is_(False),
            TimeEntry.ended_at >= history_start,
            TimeEntry.ended_at < day_start,
        )
    ).all()

    last_leave: dict = {}  # местная дата → время последнего окончания в этот день
    for past in history:
        ended = past.ended_at.astimezone(local)
        if ended.date() not in last_leave or ended > last_leave[ended.date()]:
            last_leave[ended.date()] = ended
    days = len(last_leave)
    if not days:
        return SuggestedEnd(suggested_end=None, based_on_days=0,
                            explanation="Нет истории за последние 7 дней — укажите время вручную")

    minutes = [t.hour * 60 + t.minute + t.second / 60 for t in last_leave.values()]
    average = round(sum(minutes) / days)
    # Время «на стене» в тот день, а не «полночь + N минут»: в день перевода
    # часов это разные моменты.
    suggested = datetime.combine(entry_day, time(average // 60, average % 60), tzinfo=local).astimezone(timezone.utc)
    usual = f"Обычно уходит в {suggested.astimezone(local):%H:%M} — среднее по {days} {_days_word(days)}"
    if suggested <= entry.started_at:
        return SuggestedEnd(suggested_end=None, based_on_days=days,
                            explanation=f"{usual}, но эта запись началась позже — укажите время вручную")
    if suggested > now:
        return SuggestedEnd(suggested_end=None, based_on_days=days,
                            explanation=f"{usual}, но это время ещё не наступило — укажите вручную")
    return SuggestedEnd(suggested_end=suggested, based_on_days=days, explanation=usual)


def correct_end(db: Session, entry_id: int, ended_at: datetime, note: str | None, now: datetime) -> TimeEntry:
    """Ставит время окончания задним числом и помечает запись «исправлено».

    Исправленное честно видно в отчёте: начальник отличает точные часы от
    восстановленных. Нельзя: раньше начала, в будущем, поверх следующей записи.
    """
    entry = _get_entry(db, entry_id)
    if ended_at <= entry.started_at:
        raise InvalidCorrection("Окончание должно быть позже начала записи")
    if ended_at > now:
        raise InvalidCorrection("Это время ещё не наступило")
    following = db.scalars(
        select(TimeEntry)
        .where(TimeEntry.employee_id == entry.employee_id, TimeEntry.started_at > entry.started_at)
        .order_by(TimeEntry.started_at)
    ).first()
    if following is not None and ended_at > following.started_at:
        raise InvalidCorrection("Выбранное время пересекается со следующей записью этого сотрудника")
    # Храним и отдаём в UTC, в каком бы поясе ни пришло время, — единый формат ответа.
    entry.ended_at = ended_at.astimezone(timezone.utc)
    entry.corrected = True
    entry.correction_note = note or DEFAULT_CORRECTION_NOTE
    db.commit()
    return entry


def _seconds_between(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds()))


def entry_out(entry: TimeEntry, now: datetime) -> EntryOut:
    """Запись для ответа; длительность идущего таймера — до текущего момента.

    is_too_long — только для идущей и не продлённой записи: закрытую уже
    не «забыли», а продлённую подтвердил руководитель.
    """
    running = entry.ended_at is None
    end = entry.ended_at if not running else now
    duration = _seconds_between(entry.started_at, end)
    return EntryOut(
        id=entry.id,
        employee_id=entry.employee_id,
        description=entry.description,
        started_at=entry.started_at,
        ended_at=entry.ended_at,
        kind=entry.kind,
        is_running=entry.ended_at is None,
        duration_seconds=duration,
        is_too_long=running and entry.approved_by is None and duration >= LONG_RUNNING_HOURS * 3600,
        limit_hours=entry.limit_hours,
        stops_at=entry.started_at + timedelta(hours=entry.limit_hours) if running else None,
        approved_by=entry.approved_by,
        auto_stopped=entry.auto_stopped,
        corrected=entry.corrected,
        correction_note=entry.correction_note,
        needs_review=entry.auto_stopped and not entry.corrected,
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

    report_days = []
    day = start.astimezone(COMPANY_TZ).date()
    last_day = (end - timedelta(microseconds=1)).astimezone(COMPANY_TZ).date()
    while day <= last_day:
        day_start, day_end = company_day_bounds(day)
        report_days.append(ReportDay(date=day, start=day_start, end=day_end))
        day += timedelta(days=1)

    return ReportOut(
        start=start,
        end=end,
        timezone=COMPANY_TIMEZONE_NAME,
        days=report_days,
        total_seconds=sum(r.total_seconds for r in rows),
        break_seconds=sum(r.break_seconds for r in rows),
        lunch_seconds=sum(r.lunch_seconds for r in rows),
        employees=rows,
    )
