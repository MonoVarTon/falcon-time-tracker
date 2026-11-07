"""Что приложение принимает и что отдаёт.

Pydantic проверяет входящие данные до того, как они попадут в логику:
пустое имя, слишком длинное описание или неизвестный тип записи
отклоняются ответом 422.
"""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints, model_validator

from app.models import DESCRIPTION_MAX_LENGTH, NAME_MAX_LENGTH, NOTE_MAX_LENGTH

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


class TimerExtend(BaseModel):
    """Продление рабочего дня: кто из руководителей подтвердил и на сколько часов."""

    approved_by: Name
    hours: int = Field(ge=1, le=12)


Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NOTE_MAX_LENGTH)]


class EntryCorrect(BaseModel):
    """Исправление времени окончания записи задним числом."""

    ended_at: AwareDatetime
    note: Note | None = None  # почему исправили; без причины — стандартная пометка


class SuggestedEnd(BaseModel):
    """Подсказка для исправления: обычное время ухода сотрудника."""

    suggested_end: AwareDatetime | None  # None — подсказать нечего
    based_on_days: int  # по скольким дням посчитано среднее
    explanation: str  # человеческое объяснение для страницы


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
    is_too_long: bool  # идёт дольше порога и не продлён — похоже, забыли нажать «Закончить»
    limit_hours: int  # через столько часов от начала запись закроется сама
    stops_at: AwareDatetime | None  # когда закроется сама (только для идущей)
    approved_by: str | None  # руководитель, продливший рабочий день
    auto_stopped: bool  # закрыта автоматически
    corrected: bool  # время окончания исправлено вручную
    correction_note: str | None
    needs_review: bool  # закрыта автоматически и ещё не исправлена — время надо уточнить


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
