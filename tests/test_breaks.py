"""Перерывы и обед: тип записи, переключение одним действием, обновление старой базы."""
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import init_db, make_engine
from app.models import TimeEntry
from tests.helpers import START, FakeClock, add_employee, period


def start(client: TestClient, employee_id: int, **body):
    return client.post(f"/api/employees/{employee_id}/timer/start", json=body)


def switch(client: TestClient, employee_id: int, **body):
    return client.post(f"/api/employees/{employee_id}/timer/switch", json=body)


def report(client: TestClient) -> dict:
    return client.get("/api/report", params=period(START, START + timedelta(days=1))).json()


# ---------- Обновление старой базы ----------

OLD_SCHEMA = [
    "CREATE TABLE employees (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL)",
    """CREATE TABLE time_entries (
        id INTEGER PRIMARY KEY,
        employee_id INTEGER NOT NULL REFERENCES employees(id),
        description VARCHAR(500) NOT NULL,
        started_at DATETIME NOT NULL,
        ended_at DATETIME)""",
    "CREATE UNIQUE INDEX uq_one_active_timer_per_employee ON time_entries (employee_id) WHERE ended_at IS NULL",
    "INSERT INTO employees (id, name) VALUES (1, 'анна малага')",
    "INSERT INTO time_entries VALUES (1, 1, 'кладовщик', '2026-11-07 16:39:00', NULL)",
]


def test_old_database_is_upgraded_without_losing_data(db_url: str) -> None:
    """База, созданная до появления перерывов, получает колонку kind; данные на месте.

    Ровно такая база сейчас лежит у владельца на Windows.
    """
    engine = make_engine(db_url)
    with engine.begin() as conn:
        for statement in OLD_SCHEMA:
            conn.execute(text(statement))

    init_db(engine)
    init_db(engine)  # повторный запуск ничего не ломает

    with Session(engine) as s:
        entry = s.scalars(select(TimeEntry)).one()
    engine.dispose()
    assert entry.description == "кладовщик"
    assert entry.kind == "work"
    assert entry.ended_at is None


# ---------- Перерывы и обед ----------

def test_break_does_not_count_as_work(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    start(client, employee_id, description="Отчёт")
    clock.advance(minutes=60)
    switch(client, employee_id, kind="break")
    clock.advance(minutes=15)
    switch(client, employee_id, kind="work")
    clock.advance(minutes=30)
    client.post(f"/api/employees/{employee_id}/timer/stop")

    body = report(client)

    row = body["employees"][0]
    assert row["total_seconds"] == 90 * 60  # работа: 60 + 30
    assert row["break_seconds"] == 15 * 60
    assert row["lunch_seconds"] == 0
    assert body["break_seconds"] == 15 * 60


def test_lunch_is_counted_separately(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    start(client, employee_id, description="Склад")
    clock.advance(hours=3)
    switch(client, employee_id, kind="lunch")
    clock.advance(minutes=45)
    client.post(f"/api/employees/{employee_id}/timer/stop")

    row = report(client)["employees"][0]

    assert row["total_seconds"] == 3 * 3600
    assert row["lunch_seconds"] == 45 * 60


def test_switch_closes_previous_entry_without_gap(client: TestClient, clock: FakeClock) -> None:
    """Переключение — одно действие: работа закрывается ровно в момент начала перерыва."""
    employee_id = add_employee(client)
    start(client, employee_id, description="Отчёт")
    clock.advance(minutes=50)

    response = switch(client, employee_id, kind="break")

    assert response.status_code == 201
    assert response.json()["kind"] == "break"
    assert response.json()["description"] == "Перерыв"
    entries = report(client)["employees"][0]["entries"]
    assert entries[0]["ended_at"] == entries[1]["started_at"]
    assert [e["is_running"] for e in entries] == [False, True]


def test_back_to_work_continues_last_task(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    start(client, employee_id, description="Инвентаризация")
    clock.advance(minutes=20)
    switch(client, employee_id, kind="lunch")
    clock.advance(minutes=30)

    response = switch(client, employee_id, kind="work")

    assert response.json()["description"] == "Инвентаризация"


def test_back_to_work_without_any_task_needs_description(client: TestClient) -> None:
    employee_id = add_employee(client)
    start(client, employee_id, kind="break")

    response = switch(client, employee_id, kind="work")

    assert response.status_code == 422
    assert "задачу" in response.json()["detail"]


def test_switch_when_nothing_is_running_just_starts(client: TestClient) -> None:
    employee_id = add_employee(client)

    response = switch(client, employee_id, kind="work", description="Утро")

    assert response.status_code == 201
    assert response.json()["is_running"] is True


def test_break_needs_no_description(client: TestClient) -> None:
    employee_id = add_employee(client)

    response = start(client, employee_id, kind="lunch")

    assert response.status_code == 201
    assert response.json()["description"] == "Обед"


def test_work_still_needs_description(client: TestClient) -> None:
    employee_id = add_employee(client)

    response = start(client, employee_id, kind="work")

    assert response.status_code == 422


def test_unknown_kind_is_rejected(client: TestClient) -> None:
    employee_id = add_employee(client)

    response = start(client, employee_id, kind="перекур", description="x")

    assert response.status_code == 422


def test_plain_start_during_break_is_still_rejected(client: TestClient) -> None:
    """Правило «один таймер» действует и для перерывов: старт поверх перерыва — 409."""
    employee_id = add_employee(client)
    start(client, employee_id, kind="break")

    response = start(client, employee_id, description="Работа")

    assert response.status_code == 409
