"""«Отработано сегодня»: сотрудник видит сумму работы за день, а не только текущий отрезок."""
from datetime import timedelta

from fastapi.testclient import TestClient

from tests.helpers import START, FakeClock, add_employee


def start(client: TestClient, employee_id: int, **body) -> None:
    client.post(f"/api/employees/{employee_id}/timer/start", json=body)


def switch(client: TestClient, employee_id: int, kind: str) -> None:
    client.post(f"/api/employees/{employee_id}/timer/switch", json={"kind": kind})


def today(client: TestClient, since=START) -> dict:
    """Сводка за день первого сотрудника; since — местная полночь, которую шлёт страница."""
    employees = client.get("/api/employees", params={"since": since.isoformat()}).json()
    return employees[0]["today"]


def test_worked_today_continues_after_break(client: TestClient, clock: FakeClock) -> None:
    """60 мин работы, 15 мин перерыва, ещё 30 мин работы — отработано 90 мин, не 30."""
    employee_id = add_employee(client)
    start(client, employee_id, description="Отчёт")
    clock.advance(minutes=60)
    switch(client, employee_id, "break")
    clock.advance(minutes=15)
    switch(client, employee_id, "work")
    clock.advance(minutes=30)

    summary = today(client)

    assert summary["work_seconds"] == 90 * 60
    assert summary["break_seconds"] == 15 * 60


def test_worked_today_freezes_during_lunch(client: TestClient, clock: FakeClock) -> None:
    """Пока идёт обед, рабочее время стоит на месте, а время обеда растёт."""
    employee_id = add_employee(client)
    start(client, employee_id, description="Склад")
    clock.advance(hours=2)
    switch(client, employee_id, "lunch")
    clock.advance(minutes=10)
    first = today(client)
    clock.advance(minutes=20)
    second = today(client)

    assert first["work_seconds"] == second["work_seconds"] == 2 * 3600
    assert (first["lunch_seconds"], second["lunch_seconds"]) == (10 * 60, 30 * 60)


def test_yesterday_is_not_counted_today(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    start(client, employee_id, description="Вчера")
    clock.advance(hours=1)
    client.post(f"/api/employees/{employee_id}/timer/stop")
    clock.advance(days=1)

    summary = today(client, since=START + timedelta(days=1))

    assert summary == {"work_seconds": 0, "break_seconds": 0, "lunch_seconds": 0}


def test_list_works_without_since(client: TestClient) -> None:
    """Без параметра since «сегодня» считается от полуночи компании — список работает."""
    add_employee(client)

    response = client.get("/api/employees")

    assert response.status_code == 200
    assert response.json()[0]["today"]["work_seconds"] == 0


def test_since_without_timezone_is_rejected(client: TestClient) -> None:
    add_employee(client)

    response = client.get("/api/employees", params={"since": "2026-11-07T00:00:00"})

    assert response.status_code == 422
