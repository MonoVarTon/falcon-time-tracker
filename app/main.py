"""Веб-приложение: адреса API и страница интерфейса.

Маршруты здесь тонкие: принять запрос → вызвать правило из services.py →
вернуть ответ. Ошибки правил превращаются в понятные HTTP-ответы:
404 — нет такого сотрудника, 409 — действие противоречит состоянию.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime
from sqlalchemy.orm import Session

from app import services
from app.db import engine, get_db, init_db
from app.schemas import EmployeeCreate, EmployeeOut, EntryOut, ReportOut, TimerStart

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


@app.exception_handler(services.EmployeeNotFound)
async def _not_found(_request: Request, error: services.EmployeeNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(error)})


@app.exception_handler(services.TimerAlreadyRunning)
@app.exception_handler(services.NoRunningTimer)
async def _conflict(_request: Request, error: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(error)})


@app.post("/api/employees", status_code=201, response_model=EmployeeOut)
def add_employee(body: EmployeeCreate, db: Session = Depends(get_db)) -> EmployeeOut:
    """Добавить сотрудника."""
    employee = services.create_employee(db, body.name)
    return EmployeeOut(id=employee.id, name=employee.name, active_entry=None)


@app.get("/api/employees", response_model=list[EmployeeOut])
def employees(db: Session = Depends(get_db), now: datetime = Depends(get_now)) -> list[EmployeeOut]:
    """Список сотрудников с их идущими таймерами."""
    result = []
    for employee in services.list_employees(db):
        running = services.get_running_entry(db, employee.id)
        result.append(EmployeeOut(
            id=employee.id,
            name=employee.name,
            active_entry=services.entry_out(running, now) if running else None,
        ))
    return result


@app.post("/api/employees/{employee_id}/timer/start", status_code=201, response_model=EntryOut)
def start_timer(
    employee_id: int, body: TimerStart, db: Session = Depends(get_db), now: datetime = Depends(get_now)
) -> EntryOut:
    """Запустить таймер с описанием задачи."""
    entry = services.start_timer(db, employee_id, body.description, now)
    return services.entry_out(entry, now)


@app.post("/api/employees/{employee_id}/timer/stop", response_model=EntryOut)
def stop_timer(employee_id: int, db: Session = Depends(get_db), now: datetime = Depends(get_now)) -> EntryOut:
    """Остановить идущий таймер."""
    entry = services.stop_timer(db, employee_id, now)
    return services.entry_out(entry, now)


@app.get("/api/report", response_model=ReportOut)
def report(
    start: AwareDatetime = Query(description="Начало периода, с часовым поясом"),
    end: AwareDatetime = Query(description="Конец периода (не включая), с часовым поясом"),
    db: Session = Depends(get_db),
    now: datetime = Depends(get_now),
) -> ReportOut:
    """Записи и общее время каждого сотрудника за период."""
    if end <= start:
        raise HTTPException(status_code=422, detail="Конец периода должен быть позже начала")
    return services.build_report(db, start, end, now)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Страница интерфейса."""
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
