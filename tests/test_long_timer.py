"""«Забытый таймер»: идущая запись дольше порога помечается предупреждением."""
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee
from app.seed import seed
from app.services import LONG_RUNNING_HOURS, get_running_entry
from tests.helpers import FakeClock, add_employee
from tests.test_seed import NOW


def active(client: TestClient) -> dict:
    return client.get("/api/employees").json()[0]["active_entry"]


def test_threshold_is_ten_hours() -> None:
    assert LONG_RUNNING_HOURS == 10


def test_normal_working_day_is_not_flagged(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Смена"})
    clock.advance(hours=9, minutes=59)

    assert active(client)["is_too_long"] is False


def test_timer_running_over_threshold_is_flagged(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Забыл выключить"})
    clock.advance(hours=10)

    assert active(client)["is_too_long"] is True


def test_finished_long_entry_is_not_flagged(client: TestClient, clock: FakeClock) -> None:
    """Предупреждение — только для идущего таймера: закрытую запись уже не «забыли»."""
    employee_id = add_employee(client)
    client.post(f"/api/employees/{employee_id}/timer/start", json={"description": "Аврал"})
    clock.advance(hours=11)
    stopped = client.post(f"/api/employees/{employee_id}/timer/stop").json()

    assert stopped["is_too_long"] is False


def test_long_break_is_flagged_too(client: TestClient, clock: FakeClock) -> None:
    employee_id = add_employee(client)
    client.post(f"/api/employees/{employee_id}/timer/start", json={"kind": "lunch"})
    clock.advance(hours=11)  # на 12 ч таймер уже остановится сам (tests/test_autostop.py)

    assert active(client)["is_too_long"] is True


def test_demo_has_forgotten_timer(session: Session) -> None:
    """В демо-данных Глеб вчера забыл нажать «Закончить» — предупреждение видно сразу."""
    seed(session, NOW)
    gleb = session.scalars(select(Employee).where(Employee.name == "Глеб Сорокин")).one()

    entry = get_running_entry(session, gleb.id)

    assert entry is not None
    assert NOW - entry.started_at > timedelta(hours=LONG_RUNNING_HOURS)
