"""Помощники для тестов: управляемые часы и короткие вызовы API."""
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app, get_now

START = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)


class FakeClock:
    """Часы, которые идут только когда мы их двигаем."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def advance(self, **kwargs: int) -> None:
        self.now += timedelta(**kwargs)


def make_client(engine: Engine, clock: FakeClock) -> TestClient:
    """Приложение, подключённое к временной базе и управляемым часам."""
    def _db() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_now] = lambda: clock.now
    return TestClient(app)


def add_employee(client: TestClient, name: str = "Иван Тестов") -> int:
    response = client.post("/api/employees", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def period(start: datetime, end: datetime) -> dict[str, str]:
    return {"start": start.isoformat(), "end": end.isoformat()}
