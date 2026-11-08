"""«Требуют внимания»: сотрудники с забытым или остановившимся сам таймером.

При сотне сотрудников руководитель не должен искать их по списку —
сервер помечает таких людей, страница поднимает их наверх.
"""
from datetime import timedelta

from fastapi.testclient import TestClient

from tests.helpers import FakeClock, add_employee


def employee(client: TestClient, employee_id: int) -> dict:
    return next(e for e in client.get("/api/employees").json() if e["id"] == employee_id)


def test_ordinary_employee_needs_no_attention(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Рейс"})
    clock.advance(hours=3)

    body = employee(client, employee_id)

    assert body["needs_attention"] is False
    assert body["review_entry"] is None


def test_timer_running_too_long_needs_attention(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Рейс"})
    clock.advance(hours=10, minutes=30)

    assert employee(client, employee_id)["needs_attention"] is True


def test_auto_stopped_entry_needs_attention_until_corrected(client: TestClient, clock: FakeClock) -> None:
    """Таймер остановился сам — сотрудник «не на таймере», но требует внимания, пока время не уточнят."""
    employee_id = add_employee(client)
    entry_id = client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Рейс"}).json()["id"]
    clock.advance(hours=20)

    before = employee(client, employee_id)
    client.post(f"/api/entries/{entry_id}/correct",
                json={"ended_at": (clock.now - timedelta(hours=11)).isoformat()})
    after = employee(client, employee_id)

    assert before["active_entry"] is None
    assert before["needs_attention"] is True
    assert before["review_entry"]["id"] == entry_id
    assert after["needs_attention"] is False
    assert after["review_entry"] is None


def test_extended_timer_needs_no_attention(client: TestClient, clock: FakeClock) -> None:
    """Продлено руководителем — это не забытый таймер."""
    employee_id = add_employee(client)
    entry_id = client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Рейс"}).json()["id"]
    clock.advance(hours=10)
    client.post(f"/api/entries/{entry_id}/extend", json={"approved_by": "Иван Петрович", "hours": 4})

    assert employee(client, employee_id)["needs_attention"] is False


def test_new_employee_needs_no_attention(client: TestClient) -> None:
    response = client.post("/api/employees", json={"name": "Новый Сотрудник"})

    assert response.json()["needs_attention"] is False
