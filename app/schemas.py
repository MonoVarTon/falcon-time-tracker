"""Что приложение принимает и что отдаёт.

Pydantic проверяет входящие данные до того, как они попадут в логику:
пустое имя, слишком длинное описание или неизвестный тип записи
отклоняются ответом 422.
"""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, StringConstraints, model_validator

from app.models import DESCRIPTION_MAX_LENGTH, NAME_MAX_LENGTH

# Строка без пробелов по краям, не пустая и не длиннее лимита базы.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX_LENGTH)]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=DESCRIPTION_MAX_LENGTH)
]
Kind = Literal["work", "break", "lunch"]


class EmployeeCreate(BaseModel):
    """Тело запроса «добавить сотрудника»."""

    name: Name


class TimerSwitch(BaseModel):
    """Тело запроса «переключиться»: перерыв, обед или снова работа.

    Описание необязательно: у перерыва и обеда оно подставится само,
    а при возврате к работе продолжится последняя задача.
    """

    kind: Kind = "work"
    description: Description | None = None


class TimerStart(TimerSwitch):
    """Тело запроса «запустить таймер». Для работы описание задачи обязательно."""

    @model_validator(mode="after")
    def _work_needs_description(self) -> "TimerStart":
        if self.kind == "work" and self.description is None:
            raise ValueError("Для работы укажите задачу")
        return self


class EntryOut(BaseModel):
    """Одна запись времени в ответе."""

    id: int
    employee_id: int
    kind: Kind
    description: str
    started_at: AwareDatetime
    ended_at: AwareDatetime | None
    is_running: bool
    duration_seconds: int  # для идущего таймера — до текущего момента


class DaySummary(BaseModel):
    """Итоги дня сотрудника: сколько работы, перерывов и обеда с начала суток."""

    work_seconds: int
    break_seconds: int
    lunch_seconds: int


class EmployeeOut(BaseModel):
    """Сотрудник, его идущий таймер (если есть) и итоги дня."""

    id: int
    name: str
    active_entry: EntryOut | None
    today: DaySummary


class EmployeeReport(BaseModel):
    """Строка отчёта: сотрудник, его записи за период и суммы."""

    employee_id: int
    name: str
    total_seconds: int  # рабочее время — без перерывов и обеда
    break_seconds: int
    lunch_seconds: int
    entries: list[EntryOut]


class ReportOut(BaseModel):
    """Отчёт за период [start, end)."""

    start: datetime
    end: datetime
    total_seconds: int  # рабочее время всех сотрудников
    break_seconds: int
    lunch_seconds: int
    employees: list[EmployeeReport]
