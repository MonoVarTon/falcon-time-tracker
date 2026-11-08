"""Находки независимого ревью (Gemini 3.8 Flash), подтверждённые проверкой.

Каждая проверка написана ДО исправления и падала на старом коде.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.models import Employee, TimeEntry
from app.services import LimitTooHigh, extend_limit
from tests.helpers import FakeClock, add_employee

UTC = timezone.utc


def at(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, second, tzinfo=UTC)


def worked(client: TestClient, clock: FakeClock, employee_id: int, start: datetime, end: datetime) -> None:
    clock.now = start
    client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Смена"})
    clock.now = end
    client.post(f"/api/employees/{employee_id}/timer/stop")


def forgotten(client: TestClient, clock: FakeClock, employee_id: int, start: datetime) -> int:
    clock.now = start
    return client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Забыл"}).json()["id"]


def suggestion(client: TestClient, entry_id: int):
    """Подсказка по UTC (смещение 0) — чтобы числа в проверках читались без пересчёта поясов."""
    return client.get(f"/api/entries/{entry_id}/suggested-end", params={"utc_offset_minutes": 0})


# ---------- №1: уход около полуночи ----------

def test_leave_just_before_midnight_does_not_crash(client: TestClient, clock: FakeClock) -> None:
    """Уход в 23:59:45 округляется до 24:00 — раньше это была ошибка 500 (time(24, 0) не бывает)."""
    employee_id = add_employee(client)
    # Смена короче 12 ч: иначе таймер остановится сам и смена не пойдёт в среднее.
    worked(client, clock, employee_id, at(2, 12), at(2, 23, 59, 45))
    entry_id = forgotten(client, clock, employee_id, at(3, 9))
    clock.now = at(4, 12)

    response = suggestion(client, entry_id)

    assert response.status_code == 200
    assert response.json()["suggested_end"] == "2026-11-04T00:00:00Z"  # полночь следующего дня


# ---------- №2: ночные смены ----------

def test_night_shift_gets_a_suggestion(client: TestClient, clock: FakeClock) -> None:
    """Смена 17:00–02:00: обычный уход — 02:00 СЛЕДУЮЩЕГО дня, а не «укажите вручную»."""
    employee_id = add_employee(client)
    worked(client, clock, employee_id, at(2, 17), at(3, 2))
    entry_id = forgotten(client, clock, employee_id, at(3, 17))
    clock.now = at(4, 12)

    body = suggestion(client, entry_id).json()

    assert body["suggested_end"] == "2026-11-04T02:00:00Z"
    assert body["based_on_days"] == 1


def test_mixed_day_and_night_shifts_average_by_shift_start(client: TestClient, clock: FakeClock) -> None:
    """Уход считается от дня начала смены: 22:00 и 02:00 следующего дня — в среднем 00:00, а не 12:00."""
    employee_id = add_employee(client)
    worked(client, clock, employee_id, at(2, 14), at(2, 22))
    worked(client, clock, employee_id, at(3, 18), at(4, 2))
    entry_id = forgotten(client, clock, employee_id, at(4, 16))
    clock.now = at(5, 12)

    assert suggestion(client, entry_id).json()["suggested_end"] == "2026-11-05T00:00:00Z"


# ---------- №5: потерянное продление ----------

def test_second_extension_based_on_stale_data_is_rejected(engine: Engine) -> None:
    """Два руководителя продлевают одновременно: второй не должен молча затереть первого.

    Сессия A прочитала запись (лимит 18 ч), пока сессия B продлила её до 24 ч.
    Продление из A на +6 ч должно получить отказ, а не перезаписать лимит.
    """
    with Session(engine) as setup:
        employee = Employee(name="Диспетчер")
        setup.add(employee)
        setup.flush()
        entry = TimeEntry(employee_id=employee.id, description="Рейс", started_at=at(7, 9), limit_hours=18)
        setup.add(entry)
        setup.commit()
        entry_id = entry.id

    with Session(engine) as a, Session(engine) as b:
        # Ссылку на запись держим в переменной: иначе Python сразу выбросит объект
        # из памяти сессии, A перечитает свежие данные — и проверка пройдёт случайно
        # (так и было в первой версии этой проверки).
        stale = a.get(TimeEntry, entry_id)
        assert stale.limit_hours == 18  # A видит лимит 18
        extend_limit(b, entry_id, "Руководитель Б", 6)  # B: 18 → 24

        with pytest.raises(LimitTooHigh):
            extend_limit(a, entry_id, "Руководитель А", 6)

    with Session(engine) as check:
        final = check.get(TimeEntry, entry_id)
        assert (final.limit_hours, final.approved_by) == (24, "Руководитель Б")


# ---------- №6: огромный период отчёта ----------

def test_report_period_is_limited(client: TestClient) -> None:
    response = client.get("/api/report", params={"date_from": "1970-01-01", "date_to": "2099-12-31"})

    assert response.status_code == 422
    assert "366" in response.json()["detail"]


def test_report_period_limited_for_moments_too(client: TestClient) -> None:
    params = {"start": "2000-01-01T00:00:00Z", "end": "2026-01-01T00:00:00Z"}

    assert client.get("/api/report", params=params).status_code == 422


def test_report_at_end_of_calendar_does_not_crash(client: TestClient) -> None:
    """31.12.9999 — следующего дня не существует; должно быть понятное 422, а не 500."""
    response = client.get("/api/report", params={"date_from": "9999-12-31", "date_to": "9999-12-31"})

    assert response.status_code == 422


def test_report_for_a_full_year_is_allowed(client: TestClient) -> None:
    response = client.get("/api/report", params={"date_from": "2026-01-01", "date_to": "2026-12-31"})

    assert response.status_code == 200


# ---------- №8: «сейчас» от сервера ----------

def test_report_carries_server_now(client: TestClient, clock: FakeClock) -> None:
    """Отметка «сейчас» на ленте должна строиться по часам сервера, а не компьютера."""
    clock.now = at(7, 15, 30)

    body = client.get("/api/report", params={"date_from": "2026-11-07", "date_to": "2026-11-07"}).json()

    assert body["now"] == "2026-11-07T15:30:00Z"


def test_night_shift_with_break_after_midnight_is_one_shift(client: TestClient, clock: FakeClock) -> None:
    """Перерыв в 00:15 не делит ночную смену на два «дня»: уход — 02:00 после смены, начатой 2-го."""
    employee_id = add_employee(client)
    clock.now = at(2, 17)
    client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Смена"})
    clock.now = at(3, 0, 15)
    client.post(f"/api/employees/{employee_id}/timer/switch", json={"kind": "break"})
    clock.now = at(3, 0, 30)
    client.post(f"/api/employees/{employee_id}/timer/switch", json={"kind": "work"})
    clock.now = at(3, 2)
    client.post(f"/api/employees/{employee_id}/timer/stop")
    entry_id = forgotten(client, clock, employee_id, at(3, 17))
    clock.now = at(4, 12)

    body = suggestion(client, entry_id).json()

    assert body["suggested_end"] == "2026-11-04T02:00:00Z"
    assert body["based_on_days"] == 1
