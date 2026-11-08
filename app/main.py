"""Веб-приложение: адреса API и страница интерфейса.

Маршруты здесь тонкие: принять запрос → вызвать правило из services.py →
вернуть ответ. Ошибки правил превращаются в понятные HTTP-ответы:
404 — нет такого сотрудника, 409 — действие противоречит состоянию.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime
from sqlalchemy.orm import Session

from app import services
from app.config import COMPANY_TIMEZONE_NAME, COMPANY_TZ, company_day_bounds, company_today
from app.db import engine, get_db, init_db
from app.schemas import (
    ConfigOut,
    DaySummary,
    EmployeeCreate,
    EmployeeOut,
    EntryCorrect,
    EntryOut,
    ReportOut,
    SuggestedEnd,
    TimerExtend,
    TimerStart,
    TimerSwitch,
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """При старте создаём таблицы, если их ещё нет."""
    init_db(engine)
    yield


app = FastAPI(title="Учёт рабочего времени", lifespan=lifespan)


def get_now() -> datetime:
    """Текущее время в UTC. Вынесено в зависимость, чтобы тесты могли его подменить."""
    return datetime.now(timezone.utc)


def get_checked_db(db: Session = Depends(get_db), now: datetime = Depends(get_now)) -> Session:
    """Сессия базы, в которой просроченные таймеры уже закрыты.

    Каждый запрос сначала закрывает таймеры, перешедшие свой предел
    (12 ч или продлённый), — поэтому все ответы видят актуальное состояние.
    """
    services.auto_stop_overdue(db, now)
    return db


@app.exception_handler(services.EmployeeNotFound)
@app.exception_handler(services.EntryNotFound)
async def _not_found(_request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(error)})


@app.exception_handler(services.LimitTooHigh)
@app.exception_handler(services.InvalidCorrection)
async def _unprocessable(_request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(error)})


@app.exception_handler(services.MissingDescription)
async def _missing_description(_request: Request, error: services.MissingDescription) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(error)})


@app.exception_handler(services.TimerAlreadyRunning)
@app.exception_handler(services.NoRunningTimer)
async def _conflict(_request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(error)})


@app.get("/api/config", response_model=ConfigOut)
def config() -> ConfigOut:
    """Настройки для страницы: пояс компании, в котором показывать время."""
    return ConfigOut(company_timezone=COMPANY_TIMEZONE_NAME)


@app.post("/api/employees", status_code=201, response_model=EmployeeOut)
def add_employee(body: EmployeeCreate, db: Session = Depends(get_checked_db)) -> EmployeeOut:
    """Добавить сотрудника."""
    employee = services.create_employee(db, body.name)
    empty_day = DaySummary(work_seconds=0, break_seconds=0, lunch_seconds=0)
    return EmployeeOut(id=employee.id, name=employee.name, active_entry=None, today=empty_day)


@app.get("/api/employees", response_model=list[EmployeeOut])
def employees(
    since: AwareDatetime | None = Query(
        default=None, description="Начало «сегодня», с часовым поясом; по умолчанию — полночь компании"
    ),
    db: Session = Depends(get_checked_db),
    now: datetime = Depends(get_now),
) -> list[EmployeeOut]:
    """Список сотрудников: идущий таймер и итоги дня («отработано сегодня»).

    «Сегодня» — сутки компании (по умолчанию Чикаго), а не сервера и не
    сотрудника: удалённый диспетчер работает по американским часам.
    """
    day_start = since if since is not None else company_day_bounds(company_today(now))[0]
    summaries = services.day_summaries(db, day_start, now)
    result = []
    for employee in services.list_employees(db):
        running = services.get_running_entry(db, employee.id)
        result.append(EmployeeOut(
            id=employee.id,
            name=employee.name,
            active_entry=services.entry_out(running, now) if running else None,
            today=summaries[employee.id],
        ))
    return result


@app.post("/api/employees/{employee_id}/timer/start", status_code=201, response_model=EntryOut)
def start_timer(
    employee_id: int, body: TimerStart, db: Session = Depends(get_checked_db), now: datetime = Depends(get_now)
) -> EntryOut:
    """Запустить таймер: работа с задачей, перерыв или обед."""
    entry = services.start_timer(db, employee_id, body.description, now, kind=body.kind)
    return services.entry_out(entry, now)


@app.post("/api/employees/{employee_id}/timer/switch", status_code=201, response_model=EntryOut)
def switch_timer(
    employee_id: int, body: TimerSwitch, db: Session = Depends(get_checked_db), now: datetime = Depends(get_now)
) -> EntryOut:
    """Перерыв, обед или «вернуться к работе» — одним нажатием, без разрыва во времени."""
    entry = services.switch_timer(db, employee_id, body.kind, body.description, now)
    return services.entry_out(entry, now)


@app.post("/api/employees/{employee_id}/timer/stop", response_model=EntryOut)
def stop_timer(employee_id: int, db: Session = Depends(get_checked_db), now: datetime = Depends(get_now)) -> EntryOut:
    """Остановить идущий таймер."""
    entry = services.stop_timer(db, employee_id, now)
    return services.entry_out(entry, now)


@app.post("/api/entries/{entry_id}/extend", response_model=EntryOut)
def extend_entry(
    entry_id: int, body: TimerExtend, db: Session = Depends(get_checked_db), now: datetime = Depends(get_now)
) -> EntryOut:
    """Продлить рабочий день идущей записи — с подтверждением руководителя."""
    entry = services.extend_limit(db, entry_id, body.approved_by, body.hours)
    return services.entry_out(entry, now)


@app.get("/api/entries/{entry_id}/suggested-end", response_model=SuggestedEnd)
def suggested_end(
    entry_id: int,
    utc_offset_minutes: int | None = Query(default=None, ge=-14 * 60, le=14 * 60,
                                           description="Смещение от UTC в минутах; по умолчанию — пояс компании"),
    db: Session = Depends(get_checked_db),
    now: datetime = Depends(get_now),
) -> SuggestedEnd:
    """Подсказка для исправления: обычное время ухода за последние 7 дней."""
    local = COMPANY_TZ if utc_offset_minutes is None else timezone(timedelta(minutes=utc_offset_minutes))
    return services.suggest_end(db, entry_id, local, now)


@app.post("/api/entries/{entry_id}/correct", response_model=EntryOut)
def correct_entry(
    entry_id: int, body: EntryCorrect, db: Session = Depends(get_checked_db), now: datetime = Depends(get_now)
) -> EntryOut:
    """Исправить время окончания записи — с пометкой «исправлено» в отчёте."""
    # Время «по часам компании» привязываем к её поясу. В час перевода часов
    # назад (01:00–02:00 бывает дважды) берётся первое из двух — так делает Python по умолчанию.
    ended_at = body.ended_at if body.ended_at is not None else body.ended_at_local.replace(tzinfo=COMPANY_TZ)
    entry = services.correct_end(db, entry_id, ended_at, body.note, now)
    return services.entry_out(entry, now)


@app.get("/api/report", response_model=ReportOut)
def report(
    date_from: date | None = Query(default=None, description="Первый день периода (по часам компании)"),
    date_to: date | None = Query(default=None, description="Последний день периода, включительно"),
    start: AwareDatetime | None = Query(default=None, description="Или: начало периода, с часовым поясом"),
    end: AwareDatetime | None = Query(default=None, description="Или: конец периода (не включая)"),
    db: Session = Depends(get_checked_db),
    now: datetime = Depends(get_now),
) -> ReportOut:
    """Записи и общее время каждого сотрудника за период.

    Обычно период задают датами компании (date_from, date_to) — так делает
    страница. Точные моменты (start, end) оставлены для интеграций.
    Нужна ровно одна пара.
    """
    by_dates = date_from is not None or date_to is not None
    by_moments = start is not None or end is not None
    if by_dates == by_moments:
        raise HTTPException(status_code=422, detail="Укажите период: date_from и date_to (или start и end)")
    if by_dates:
        if date_from is None or date_to is None:
            raise HTTPException(status_code=422, detail="Нужны обе даты: date_from и date_to")
        if date_to < date_from:
            raise HTTPException(status_code=422, detail="Дата «по» не может быть раньше даты «с»")
        start = company_day_bounds(date_from)[0]
        end = company_day_bounds(date_to)[1]
    elif start is None or end is None:
        raise HTTPException(status_code=422, detail="Нужны оба момента: start и end")
    if end <= start:
        raise HTTPException(status_code=422, detail="Конец периода должен быть позже начала")
    return services.build_report(db, start, end, now)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Страница интерфейса."""
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
