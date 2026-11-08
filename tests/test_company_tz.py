"""Часовой пояс компании: сутки, «сегодня» и отчёт считаются по времени компании.

Компания — диспетчерская в Чикаго, а диспетчер может работать удалённо из
другой страны. Его смена 17:00–02:00 по Чикаго — это ОДИН рабочий день
компании, хотя по его местным часам он перескакивает через полночь.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.config import company_day_bounds, load_company_timezone
from tests.helpers import FakeClock, add_employee

UTC = timezone.utc
CHICAGO = ZoneInfo("America/Chicago")


def utc(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def work(client: TestClient, clock: FakeClock, employee_id: int, start: datetime, end: datetime | None) -> None:
    clock.now = start
    client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Рейс"})
    if end is not None:
        clock.now = end
        client.post(f"/api/employees/{employee_id}/timer/stop")


def report_days(client: TestClient, date_from: str, date_to: str) -> dict:
    return client.get("/api/report", params={"date_from": date_from, "date_to": date_to}).json()


# ---------- Настройка ----------

def test_default_company_timezone_is_chicago(client: TestClient) -> None:
    body = client.get("/api/config").json()

    assert body["company_timezone"] == "America/Chicago"


def test_unknown_timezone_is_a_clear_error() -> None:
    with pytest.raises(ValueError, match="часовой пояс"):
        load_company_timezone("Mars/Olympus")


# ---------- Границы суток ----------

def test_company_day_bounds_in_utc() -> None:
    start, end = company_day_bounds(date(2026, 11, 6), CHICAGO)

    assert (start, end) == (utc(6, 6), utc(7, 6))  # полночь в Чикаго (UTC−6) = 06:00 UTC


def test_daylight_saving_day_is_25_hours() -> None:
    """1 ноября 2026 в Чикаго переводят часы назад — сутки длятся 25 часов."""
    start, end = company_day_bounds(date(2026, 11, 1), CHICAGO)

    assert (end - start).total_seconds() == 25 * 3600


# ---------- «Сегодня» ----------

def test_today_starts_at_company_midnight(client: TestClient, clock: FakeClock) -> None:
    """Вечер в Чикаго — это уже следующий день по UTC, но «сегодня» компании ещё не кончилось."""
    employee_id = add_employee(client)
    work(client, clock, employee_id, utc(6, 23, 30), utc(7, 1))  # 17:30–19:00 по Чикаго, 6 ноября
    clock.now = utc(7, 2)  # 20:00 по Чикаго, всё ещё 6 ноября

    today = client.get("/api/employees").json()[0]["today"]

    assert today["work_seconds"] == 90 * 60  # по полуночи UTC было бы только 60 минут


# ---------- Отчёт по датам компании ----------

def test_report_by_company_dates(client: TestClient, clock: FakeClock) -> None:
    """Смена 21:00–23:00 по Чикаго попадает в свой день, хотя по UTC это уже следующие сутки."""
    employee_id = add_employee(client)
    work(client, clock, employee_id, utc(7, 3), utc(7, 5))  # 6 ноября, 21:00–23:00 по Чикаго
    clock.now = utc(8, 12)

    on_sixth = report_days(client, "2026-11-06", "2026-11-06")
    on_seventh = report_days(client, "2026-11-07", "2026-11-07")

    assert on_sixth["total_seconds"] == 2 * 3600
    assert on_seventh["total_seconds"] == 0


def test_night_shift_stays_one_company_day(client: TestClient, clock: FakeClock) -> None:
    """Смена удалённого диспетчера 17:00–02:00 по Чикаго делится на 6-е и 7-е ноября по часам компании."""
    employee_id = add_employee(client)
    work(client, clock, employee_id, utc(6, 23), utc(7, 8))  # 17:00–02:00 по Чикаго
    clock.now = utc(8, 12)

    body = report_days(client, "2026-11-06", "2026-11-07")

    assert body["total_seconds"] == 9 * 3600
    assert [d["date"] for d in body["days"]] == ["2026-11-06", "2026-11-07"]


def test_report_lists_days_with_utc_bounds(client: TestClient) -> None:
    body = report_days(client, "2026-11-01", "2026-11-01")

    assert body["timezone"] == "America/Chicago"
    assert body["days"] == [{"date": "2026-11-01", "start": "2026-11-01T05:00:00Z", "end": "2026-11-02T06:00:00Z"}]


def test_report_needs_dates_or_moments(client: TestClient) -> None:
    assert client.get("/api/report").status_code == 422


def test_report_rejects_inverted_dates(client: TestClient) -> None:
    assert client.get("/api/report", params={"date_from": "2026-11-07", "date_to": "2026-11-06"}).status_code == 422


def test_report_rejects_mixed_parameters(client: TestClient) -> None:
    params = {"date_from": "2026-11-06", "date_to": "2026-11-06",
              "start": "2026-11-06T00:00:00Z", "end": "2026-11-07T00:00:00Z"}

    assert client.get("/api/report", params=params).status_code == 422


# ---------- Подсказка исправления ----------

def test_suggestion_uses_company_time_by_default(client: TestClient, clock: FakeClock) -> None:
    """Без смещения от страницы «обычное время ухода» считается по часам компании."""
    employee_id = add_employee(client)
    work(client, clock, employee_id, utc(4, 14), utc(4, 23))  # 08:00–17:00 по Чикаго
    clock.now = utc(5, 14)
    client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Забыл"})
    entry_id = client.get("/api/employees").json()[0]["active_entry"]["id"]
    clock.now = utc(6, 3)

    body = client.get(f"/api/entries/{entry_id}/suggested-end").json()

    assert body["suggested_end"] == "2026-11-05T23:00:00Z"  # 17:00 по Чикаго


def test_suggestion_on_clock_change_day(client: TestClient, clock: FakeClock) -> None:
    """В день перевода часов (1 ноября, сутки 25 ч) подсказка — 17:00 «на стене», а не «полночь + 17 ч».

    «Полночь + 17 часов» в этот день дало бы 16:00 — на час раньше обычного ухода.
    """
    employee_id = add_employee(client)
    clock.now = datetime(2026, 10, 30, 13, tzinfo=UTC)  # 08:00 по Чикаго (летнее время, UTC−5)
    client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Рейс"})
    clock.now = datetime(2026, 10, 30, 22, tzinfo=UTC)  # 17:00 по Чикаго
    client.post(f"/api/employees/{employee_id}/timer/stop")
    clock.now = utc(1, 14)  # 1 ноября, 08:00 по Чикаго (уже зимнее время, UTC−6)
    client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Забыл"})
    entry_id = client.get("/api/employees").json()[0]["active_entry"]["id"]
    clock.now = utc(2, 12)

    body = client.get(f"/api/entries/{entry_id}/suggested-end").json()

    assert body["suggested_end"] == "2026-11-01T23:00:00Z"  # 17:00 по Чикаго в зимнее время
    assert body["explanation"].startswith("Обычно уходит в 17:00")


def test_correction_in_company_wall_time(client: TestClient, clock: FakeClock) -> None:
    """Страница присылает время «как на часах компании» — сервер сам переводит в UTC."""
    employee_id = add_employee(client)
    work(client, clock, employee_id, utc(6, 14), None)  # 08:00 по Чикаго
    entry_id = client.get("/api/employees").json()[0]["active_entry"]["id"]
    clock.now = utc(7, 12)

    response = client.post(f"/api/entries/{entry_id}/correct", json={"ended_at_local": "2026-11-06T17:30"})

    assert response.status_code == 200
    assert response.json()["ended_at"] == "2026-11-06T23:30:00Z"  # 17:30 по Чикаго


def test_correction_needs_exactly_one_time(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    work(client, clock, employee_id, utc(6, 14), None)
    entry_id = client.get("/api/employees").json()[0]["active_entry"]["id"]
    clock.now = utc(7, 12)

    both = {"ended_at": "2026-11-06T23:30:00Z", "ended_at_local": "2026-11-06T17:30"}

    assert client.post(f"/api/entries/{entry_id}/correct", json={}).status_code == 422
    assert client.post(f"/api/entries/{entry_id}/correct", json=both).status_code == 422
