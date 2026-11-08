"""Проверки приложения через его API — так же, как им пользуется страница.

Время в тестах управляемое (FakeClock): мы сами говорим, который час,
и можем «перемотать» его. Так длительность проверяется точно, без sleep().
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import init_db, make_engine
from app.main import app
from tests.helpers import START, FakeClock, add_employee, make_client, period

# ---------- Сотрудники ----------

def test_add_employee_and_see_it_in_list(client: TestClient) -> None:
    response = client.post("/api/employees", json={"name": "  Анна Пример  "})

    assert response.status_code == 201
    assert response.json()["name"] == "Анна Пример"  # пробелы по краям убраны
    names = [e["name"] for e in client.get("/api/employees").json()]
    assert names == ["Анна Пример"]


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_employee_name_must_be_meaningful(client: TestClient, name: str) -> None:
    response = client.post("/api/employees", json={"name": name})

    assert response.status_code == 422


# ---------- Таймер ----------

def test_start_and_stop_timer_records_duration(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)

    started = client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Отчёт"})
    clock.advance(minutes=90)
    stopped = client.post(f"/api/employees/{employee_id}/timer/stop")

    assert started.status_code == 201
    assert started.json()["is_running"] is True
    assert stopped.status_code == 200
    assert stopped.json()["is_running"] is False
    assert stopped.json()["duration_seconds"] == 90 * 60


def test_second_start_is_rejected(client: TestClient) -> None:
    """Обязательная проверка из задания: повторный запуск таймера."""
    employee_id = add_employee(client)
    client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Первая"})

    second = client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Вторая"})

    assert second.status_code == 409
    assert "уже запущен" in second.json()["detail"]
    # И главное — вторая запись не появилась, первая продолжает идти.
    employee = client.get("/api/employees").json()[0]
    assert employee["active_entry"]["description"] == "Первая"


def test_timer_can_be_started_again_after_stop(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Первая"})
    clock.advance(minutes=10)
    client.post(f"/api/employees/{employee_id}/timer/stop")

    again = client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Вторая"})

    assert again.status_code == 201


def test_stop_without_running_timer_is_rejected(client: TestClient) -> None:
    employee_id = add_employee(client)

    response = client.post(f"/api/employees/{employee_id}/timer/stop")

    assert response.status_code == 409


def test_timer_for_unknown_employee_is_404(client: TestClient) -> None:
    response = client.post("/api/employees/999/timer/start", json={"description": "Задача"})

    assert response.status_code == 404


@pytest.mark.parametrize("description", ["", "   ", "x" * 501])
def test_timer_needs_task_description(client: TestClient, description: str) -> None:
    employee_id = add_employee(client)

    response = client.post(f"/api/employees/{employee_id}/timer/start", json={"description": description})

    assert response.status_code == 422


# ---------- Отчёт ----------

def test_empty_report(client: TestClient) -> None:
    """Обязательная проверка из задания: пустой отчёт — не ошибка, а нули."""
    add_employee(client, "Иван Тестов")

    response = client.get("/api/report", params=period(START, START + timedelta(days=1)))

    assert response.status_code == 200
    body = response.json()
    assert body["total_seconds"] == 0
    assert body["employees"][0]["total_seconds"] == 0
    assert body["employees"][0]["entries"] == []


def test_empty_report_without_employees(client: TestClient) -> None:
    response = client.get("/api/report", params=period(START, START + timedelta(days=1)))

    assert response.status_code == 200
    body = response.json()
    # Сутки компании (Чикаго), которые задевает период 09:00–09:00 UTC: 7 и 8 ноября.
    assert [d["date"] for d in body.pop("days")] == ["2026-11-07", "2026-11-08"]
    assert body.pop("timezone") == "America/Chicago"
    assert body.pop("now") == "2026-11-07T09:00:00Z"  # часы теста
    assert body == {
        "start": "2026-11-07T09:00:00Z",
        "end": "2026-11-08T09:00:00Z",
        "total_seconds": 0,
        "break_seconds": 0,
        "lunch_seconds": 0,
        "employees": [],
    }


def test_report_sums_time_per_employee(client: TestClient, clock: FakeClock) -> None:
    anna = add_employee(client, "Анна Пример")
    petr = add_employee(client, "Пётр Образец")
    for minutes in (30, 45):
        client.post(f"/api/employees/{anna}/timer/start", json={"description": "Дизайн"})
        clock.advance(minutes=minutes)
        client.post(f"/api/employees/{anna}/timer/stop")
    client.post(f"/api/employees/{petr}/timer/start", json={"description": "Код"})
    clock.advance(hours=2)
    client.post(f"/api/employees/{petr}/timer/stop")

    body = client.get("/api/report", params=period(START, START + timedelta(days=1))).json()

    totals = {e["name"]: e["total_seconds"] for e in body["employees"]}
    assert totals == {"Анна Пример": 75 * 60, "Пётр Образец": 120 * 60}
    assert body["total_seconds"] == 195 * 60
    assert len(body["employees"][0]["entries"]) == 2


def test_report_ignores_entries_outside_period(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Вчера"})
    clock.advance(hours=1)
    client.post(f"/api/employees/{employee_id}/timer/stop")

    next_day = START + timedelta(days=1)
    body = client.get("/api/report", params=period(next_day, next_day + timedelta(days=1))).json()

    assert body["total_seconds"] == 0


def test_report_counts_only_part_inside_period(client: TestClient, clock: FakeClock) -> None:
    """Работа 23:00–01:00 в отчёте за один день даёт только свой час."""
    employee_id = add_employee(client)
    clock.now = datetime(2026, 11, 7, 23, 0, tzinfo=timezone.utc)
    client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Ночная"})
    clock.advance(hours=2)
    client.post(f"/api/employees/{employee_id}/timer/stop")

    day_start = datetime(2026, 11, 7, tzinfo=timezone.utc)
    body = client.get("/api/report", params=period(day_start, day_start + timedelta(days=1))).json()

    assert body["total_seconds"] == 3600


def test_report_counts_running_timer_up_to_now(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Идёт"})
    clock.advance(minutes=20)

    body = client.get("/api/report", params=period(START, START + timedelta(days=1))).json()

    entry = body["employees"][0]["entries"][0]
    assert entry["is_running"] is True
    assert body["total_seconds"] == 20 * 60


def test_report_rejects_inverted_period(client: TestClient) -> None:
    response = client.get("/api/report", params=period(START, START - timedelta(hours=1)))

    assert response.status_code == 422


def test_report_rejects_period_without_timezone(client: TestClient) -> None:
    response = client.get("/api/report", params={"start": "2026-11-07T00:00:00", "end": "2026-11-08T00:00:00"})

    assert response.status_code == 422


# ---------- Перезапуск ----------

def test_data_survives_app_restart(db_url: str, clock: FakeClock) -> None:
    """Сотрудник и запись остаются после перезапуска: новое подключение к тому же файлу."""
    first = make_engine(db_url)
    init_db(first)
    with make_client(first, clock) as c:
        employee_id = add_employee(c)
        c.post(f"/api/employees/{employee_id}/timer/start", json={"description": "До перезапуска"})
    app.dependency_overrides.clear()
    first.dispose()

    second = make_engine(db_url)
    with make_client(second, clock) as c:
        employees = c.get("/api/employees").json()
    app.dependency_overrides.clear()
    second.dispose()

    assert employees[0]["active_entry"]["description"] == "До перезапуска"


# ---------- Страница ----------

def test_page_is_served_and_script_is_not_cut(client: TestClient) -> None:
    """Страница открывается, и её скрипт не обрывается раньше времени.

    Была ошибка: слово «</script>» в комментарии внутри скрипта браузер
    принимал за конец скрипта — весь код ниже не выполнялся, страница молчала.
    """
    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    script = html.split("<script>", 1)[1]
    closing = "</" + "script>"
    # Закрывающий тег должен встречаться только один раз — в самом конце скрипта,
    # и уже после последней функции страницы.
    assert script.count(closing) == 1
    assert script.index("\nstart();") < script.index(closing)  # последняя строка — запуск страницы
