"""Автоостановка через 12 часов и продление рабочего дня руководителем."""
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import init_db, make_engine
from app.models import TimeEntry
from tests.helpers import START, FakeClock, add_employee, period


def start(client: TestClient, employee_id: int) -> int:
    response = client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Смена"})
    return response.json()["id"]


def active(client: TestClient) -> dict | None:
    return client.get("/api/employees").json()[0]["active_entry"]


def entries(client: TestClient) -> list[dict]:
    body = client.get("/api/report", params=period(START, START + timedelta(days=2))).json()
    return body["employees"][0]["entries"]


def extend(client: TestClient, entry_id: int, **body):
    return client.post(f"/api/entries/{entry_id}/extend", json=body)


# ---------- Автоостановка ----------

def test_timer_keeps_running_just_before_limit(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    start(client, employee_id)
    clock.advance(hours=11, minutes=59)

    assert active(client) is not None


def test_timer_stops_itself_after_twelve_hours(client: TestClient, clock: FakeClock) -> None:
    """Забытый таймер закрывается ровно на «начало + 12 ч», а не тогда, когда кто-то заглянул."""
    employee_id = add_employee(client)
    start(client, employee_id)
    clock.advance(hours=30)  # заглянули только через 30 часов

    assert active(client) is None
    entry = entries(client)[0]
    assert entry["duration_seconds"] == 12 * 3600
    assert entry["auto_stopped"] is True
    assert entry["needs_review"] is True


def test_new_timer_can_start_after_auto_stop(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    start(client, employee_id)
    clock.advance(hours=13)

    response = client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Новый день"})

    assert response.status_code == 201


def test_running_timer_shows_when_it_will_stop(client: TestClient) -> None:
    employee_id = add_employee(client)
    start(client, employee_id)

    entry = active(client)

    assert entry["limit_hours"] == 12
    assert entry["stops_at"] == (START + timedelta(hours=12)).isoformat().replace("+00:00", "Z")


# ---------- Продление руководителем ----------

def test_manager_extends_working_day(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    entry_id = start(client, employee_id)
    clock.advance(hours=10)

    response = extend(client, entry_id, approved_by="Иван Петрович", hours=4)
    clock.advance(hours=3)  # 13 ч — без продления уже остановился бы

    assert response.status_code == 200
    still = active(client)
    assert still is not None
    assert still["limit_hours"] == 16
    assert still["approved_by"] == "Иван Петрович"
    assert still["is_too_long"] is False  # продлено — красное предупреждение не нужно


def test_extended_timer_still_stops_at_new_limit(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    entry_id = start(client, employee_id)
    clock.advance(hours=10)
    extend(client, entry_id, approved_by="Иван Петрович", hours=2)
    clock.advance(hours=5)

    assert active(client) is None
    assert entries(client)[0]["duration_seconds"] == 14 * 3600


def test_extension_needs_manager_name(client: TestClient) -> None:
    entry_id = start(client, add_employee(client))

    assert extend(client, entry_id, approved_by="  ", hours=2).status_code == 422


def test_working_day_cannot_exceed_24_hours(client: TestClient) -> None:
    entry_id = start(client, add_employee(client))
    extend(client, entry_id, approved_by="Иван Петрович", hours=12)

    response = extend(client, entry_id, approved_by="Иван Петрович", hours=1)

    assert response.status_code == 422


def test_cannot_extend_finished_entry(client: TestClient) -> None:
    employee_id = add_employee(client)
    entry_id = start(client, employee_id)
    client.post(f"/api/employees/{employee_id}/timer/stop")

    assert extend(client, entry_id, approved_by="Иван Петрович", hours=2).status_code == 409


def test_cannot_extend_unknown_entry(client: TestClient) -> None:
    response = extend(client, 999, approved_by="Иван Петрович", hours=2)

    # Проверяем и текст: 404 «адреса нет» не должен сойти за 404 «записи нет».
    assert response.status_code == 404
    assert "не найдена" in response.json()["detail"]


# ---------- Обновление базы ----------

def test_database_from_previous_version_gets_new_columns(db_url: str) -> None:
    """База предыдущей версии (уже с kind, но без новых полей) обновляется без потерь."""
    engine = make_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE employees (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL)"))
        conn.execute(text("""CREATE TABLE time_entries (id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL,
            description VARCHAR(500) NOT NULL, started_at DATETIME NOT NULL, ended_at DATETIME,
            kind VARCHAR(10) NOT NULL DEFAULT 'work')"""))
        conn.execute(text("INSERT INTO employees VALUES (1, 'анна малага')"))
        conn.execute(text("INSERT INTO time_entries VALUES (1, 1, 'яся', '2026-11-07 17:20:19', NULL, 'work')"))

    init_db(engine)

    with Session(engine) as s:
        entry = s.scalars(select(TimeEntry)).one()
    engine.dispose()
    assert (entry.description, entry.limit_hours, entry.auto_stopped, entry.corrected) == ("яся", 12, False, False)
