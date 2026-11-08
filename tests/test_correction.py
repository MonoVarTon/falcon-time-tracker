"""Исправление времени окончания с подсказкой «обычно уходит в …»."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from tests.helpers import FakeClock, add_employee, period

UTC = timezone.utc


def day_at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def worked(client: TestClient, clock: FakeClock, employee_id: int, start: datetime, end: datetime) -> int:
    """Запись работы с start по end (часы двигаем сами)."""
    clock.now = start
    entry_id = client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Смена"}).json()["id"]
    clock.now = end
    client.post(f"/api/employees/{employee_id}/timer/stop")
    return entry_id


def start_forgotten(client: TestClient, clock: FakeClock, employee_id: int, start: datetime) -> int:
    clock.now = start
    return client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Забыл"}).json()["id"]


def suggestion(client: TestClient, entry_id: int, offset: int = 0) -> dict:
    return client.get(f"/api/entries/{entry_id}/suggested-end", params={"utc_offset_minutes": offset}).json()


def correct(client: TestClient, entry_id: int, ended_at: datetime, **extra):
    return client.post(f"/api/entries/{entry_id}/correct", json={"ended_at": ended_at.isoformat(), **extra})


# ---------- Подсказка ----------

def test_suggestion_is_average_leave_time_of_last_days(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    for day, minute in ((2, 0), (3, 10), (4, 20)):
        worked(client, clock, employee_id, day_at(day, 9), day_at(day, 18, minute))
    forgotten = start_forgotten(client, clock, employee_id, day_at(5, 9))
    clock.now = day_at(5, 23)

    body = suggestion(client, forgotten)

    assert body["suggested_end"] == "2026-11-05T18:10:00Z"  # среднее 18:00, 18:10, 18:20
    assert body["based_on_days"] == 3


def test_only_last_leave_of_each_day_counts(client: TestClient, clock: FakeClock) -> None:
    """Уход — это конец ПОСЛЕДНЕЙ записи дня, а не обеда посередине."""
    employee_id = add_employee(client)
    worked(client, clock, employee_id, day_at(2, 9), day_at(2, 13))
    worked(client, clock, employee_id, day_at(2, 14), day_at(2, 18))
    forgotten = start_forgotten(client, clock, employee_id, day_at(3, 9))
    clock.now = day_at(3, 22)

    assert suggestion(client, forgotten)["suggested_end"] == "2026-11-03T18:00:00Z"


def test_old_auto_stopped_and_corrected_days_are_ignored(client: TestClient, clock: FakeClock) -> None:
    """В среднее не идут дни старше недели, автоостановленные и уже исправленные (это догадки, а не факты)."""
    employee_id = add_employee(client)
    worked(client, clock, employee_id, day_at(1, 9) - timedelta(days=10), day_at(1, 23) - timedelta(days=10))  # старше 7 дней
    auto = start_forgotten(client, clock, employee_id, day_at(2, 9))  # остановится сам в 21:00
    clock.now = day_at(3, 8)
    client.get("/api/employees")
    fixed = worked(client, clock, employee_id, day_at(3, 9), day_at(3, 20))
    correct(client, fixed, day_at(3, 19))  # исправленная — не факт
    worked(client, clock, employee_id, day_at(4, 9), day_at(4, 17, 30))  # единственный надёжный день
    forgotten = start_forgotten(client, clock, employee_id, day_at(5, 9))
    clock.now = day_at(5, 22)

    body = suggestion(client, forgotten)

    assert auto  # запись создана
    assert body["suggested_end"] == "2026-11-05T17:30:00Z"
    assert body["based_on_days"] == 1


def test_no_history_means_no_suggestion(client: TestClient, clock: FakeClock) -> None:
    forgotten = start_forgotten(client, clock, add_employee(client), day_at(5, 9))
    clock.now = day_at(5, 20)

    body = suggestion(client, forgotten)

    assert body["suggested_end"] is None
    assert "истории" in body["explanation"]


def test_no_suggestion_if_usual_leave_is_before_start(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    worked(client, clock, employee_id, day_at(2, 9), day_at(2, 18))
    forgotten = start_forgotten(client, clock, employee_id, day_at(3, 19))  # пришёл в ночную смену
    clock.now = day_at(4, 6)

    assert suggestion(client, forgotten)["suggested_end"] is None


def test_suggestion_respects_users_timezone(client: TestClient, clock: FakeClock) -> None:
    """Сутки считаются по местному времени пользователя: Минск, UTC+3."""
    employee_id = add_employee(client)
    worked(client, clock, employee_id, day_at(2, 6), day_at(2, 15))  # 9:00–18:00 по Минску
    forgotten = start_forgotten(client, clock, employee_id, day_at(3, 6))
    clock.now = day_at(3, 20)

    body = suggestion(client, forgotten, offset=180)

    assert body["suggested_end"] == "2026-11-03T15:00:00Z"
    # Момент совпадает и при поясе UTC (15:00 UTC = 18:00 Минск), поэтому одного его мало:
    # пояс виден только в объяснении. Без этой строки проверка проходила с игнорированием
    # пояса (нашёл Gemini при ревью).
    assert body["explanation"].startswith("Обычно уходит в 18:00")


# ---------- Исправление ----------

def test_correct_auto_stopped_entry(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    entry_id = start_forgotten(client, clock, employee_id, day_at(5, 9))
    clock.now = day_at(6, 9)  # давно остановился сам на 21:00

    response = correct(client, entry_id, day_at(5, 18), note="по среднему времени ухода")

    assert response.status_code == 200
    body = response.json()
    assert body["ended_at"] == "2026-11-05T18:00:00Z"
    assert (body["corrected"], body["needs_review"]) == (True, False)
    assert body["correction_note"] == "по среднему времени ухода"
    report = client.get("/api/report", params=period(day_at(5, 0), day_at(6, 0))).json()
    assert report["total_seconds"] == 9 * 3600  # было бы 12 ч


def test_correcting_running_forgotten_timer_closes_it(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    entry_id = start_forgotten(client, clock, employee_id, day_at(5, 9))
    clock.now = day_at(5, 20)

    correct(client, entry_id, day_at(5, 18))
    again = client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Новая"})

    assert again.status_code == 201
    entry = client.get("/api/report", params=period(day_at(5, 0), day_at(6, 0))).json()["employees"][0]["entries"][0]
    assert entry["correction_note"] == "Время окончания исправлено вручную"


def test_correction_cannot_be_in_future(client: TestClient, clock: FakeClock) -> None:
    entry_id = start_forgotten(client, clock, add_employee(client), day_at(5, 9))
    clock.now = day_at(5, 12)

    assert correct(client, entry_id, day_at(5, 18)).status_code == 422


def test_correction_cannot_be_before_start(client: TestClient, clock: FakeClock) -> None:
    entry_id = start_forgotten(client, clock, add_employee(client), day_at(5, 9))
    clock.now = day_at(5, 20)

    assert correct(client, entry_id, day_at(5, 8)).status_code == 422


def test_correction_cannot_overlap_next_entry(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    first = worked(client, clock, employee_id, day_at(5, 9), day_at(5, 12))
    worked(client, clock, employee_id, day_at(5, 13), day_at(5, 17))

    response = correct(client, first, day_at(5, 14))

    assert response.status_code == 422
    assert "следующей записью" in response.json()["detail"]


def test_correcting_unknown_entry_is_404(client: TestClient) -> None:
    response = correct(client, 999, day_at(5, 18))

    assert response.status_code == 404
    assert "не найдена" in response.json()["detail"]
