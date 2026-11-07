"""Что приложение принимает и что отдаёт.

Pydantic проверяет входящие данные до того, как они попадут в логику:
пустое имя или слишком длинное описание отклоняются ответом 422.
"""
from datetime import datetime
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, StringConstraints

from app.models import DESCRIPTION_MAX_LENGTH, NAME_MAX_LENGTH

# Строка без пробелов по краям, не пустая и не длиннее лимита базы.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX_LENGTH)]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=DESCRIPTION_MAX_LENGTH)
]


class EmployeeCreate(BaseModel):
    """Тело запроса «добавить сотрудника»."""

    name: Name


class TimerStart(BaseModel):
    """Тело запроса «запустить таймер»."""

    description: Description


class EntryOut(BaseModel):
    """Одна запись времени в ответе."""

    id: int
    employee_id: int
    description: str
    started_at: AwareDatetime
    ended_at: AwareDatetime | None
    is_running: bool
    duration_seconds: int  # для идущего таймера — до текущего момента


class EmployeeOut(BaseModel):
    """Сотрудник и его идущий таймер, если есть."""

    id: int
    name: str
    active_entry: EntryOut | None


class EmployeeReport(BaseModel):
    """Строка отчёта: сотрудник, его записи за период и сумма."""

    employee_id: int
    name: str
    total_seconds: int
    entries: list[EntryOut]


class ReportOut(BaseModel):
    """Отчёт за период [start, end)."""

    start: datetime
    end: datetime
    total_seconds: int
    employees: list[EmployeeReport]
