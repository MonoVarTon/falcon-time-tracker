"""Демо-данные: пять вымышленных сотрудников и неделя рабочих дней.

Запуск:  python -m app.seed            — заполнить пустую базу
         python -m app.seed --append   — добавить к уже существующим

Правило безопасности: в базу, где уже есть сотрудники, без флага --append
ничего не пишем. Демо никогда не смешивается с настоящими данными молча.

Все имена и задачи выдуманы. Случайность «заморожена» зерном SEED:
при одинаковом «сейчас» данные получаются одинаковыми.
"""
import argparse
import random
from datetime import date, datetime, time, timedelta, tzinfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal, engine, init_db
from app.models import KIND_BREAK, KIND_LUNCH, KIND_WORK, Employee, TimeEntry
from app.services import DEFAULT_DESCRIPTIONS

SEED = 42
PAST_WORKDAYS = 5
DAY_START = time(9, 0)

DEMO_NAMES = ("Алина Морозова", "Борис Ткаченко", "Вера Лесная", "Глеб Сорокин", "Дина Ракитина")
DEMO_TASKS = (
    ("Макет главной страницы", "Правки по баннерам", "Созвон с заказчиком"),
    ("Приёмка товара", "Инвентаризация склада", "Сборка заказов"),
    ("Сверка счетов", "Закрытие месяца", "Ответы поставщикам"),
    ("Исправление ошибок в API", "Ревью кода", "Настройка сервера"),
    ("Обзвон клиентов", "Подготовка договора", "Отчёт по продажам"),
)

# Рабочий день — чередование отрезков: (тип, минимум минут, максимум минут).
DAY_PATTERN = (
    (KIND_WORK, 100, 130),
    (KIND_BREAK, 10, 15),
    (KIND_WORK, 80, 110),
    (KIND_LUNCH, 40, 60),
    (KIND_WORK, 110, 140),
    (KIND_BREAK, 10, 15),
    (KIND_WORK, 80, 110),
)


class SeedRefused(Exception):
    """В базе уже есть сотрудники, а флаг --append не указан."""


def _past_workdays(today: date, count: int) -> list[date]:
    """Последние count будних дней до сегодняшнего (от старого к новому)."""
    days: list[date] = []
    day = today
    while len(days) < count:
        day -= timedelta(days=1)
        if day.weekday() < 5:  # 0–4 — понедельник…пятница
            days.append(day)
    return sorted(days)


def _entry(employee: Employee, kind: str, task: str, start: datetime, end: datetime | None) -> TimeEntry:
    description = task if kind == KIND_WORK else DEFAULT_DESCRIPTIONS[kind]
    return TimeEntry(employee_id=employee.id, kind=kind, description=description, started_at=start, ended_at=end)


def _full_day(rng: random.Random, employee: Employee, tasks: tuple[str, ...], day: date, tz: tzinfo) -> list[TimeEntry]:
    """Один полный рабочий день: начало около 9:00, отрезки по DAY_PATTERN."""
    moment = datetime.combine(day, DAY_START, tzinfo=tz) + timedelta(minutes=rng.randint(-15, 20))
    entries = []
    for kind, low, high in DAY_PATTERN:
        end = moment + timedelta(minutes=rng.randint(low, high))
        entries.append(_entry(employee, kind, rng.choice(tasks), moment, end))
        moment = end
    return entries


def _today(employees: list[Employee], now: datetime) -> list[TimeEntry]:
    """Сегодня — живая картина относительно «сейчас», без записей из будущего.

    Кто-то работает, кто-то на обеде или перерыве, кто-то уже ушёл.
    """
    def ago(minutes: int) -> datetime:
        return now - timedelta(minutes=minutes)

    a, b, c, d, e = employees
    t = DEMO_TASKS
    # Глеб сегодня ещё не пришёл: вчера он забыл нажать «Закончить» (см. seed()).
    return [
        # Алина: утро с перерывом, сейчас снова работает
        _entry(a, KIND_WORK, t[0][0], ago(180), ago(100)),
        _entry(a, KIND_BREAK, "", ago(100), ago(88)),
        _entry(a, KIND_WORK, t[0][1], ago(88), None),
        # Борис: поработал и ушёл на обед
        _entry(b, KIND_WORK, t[1][0], ago(160), ago(20)),
        _entry(b, KIND_LUNCH, "", ago(20), None),
        # Вера: пришла позже, работает
        _entry(c, KIND_WORK, t[2][0], ago(75), None),
        # Дина: на перерыве
        _entry(e, KIND_WORK, t[4][0], ago(120), ago(8)),
        _entry(e, KIND_BREAK, "", ago(8), None),
    ]


def seed(db: Session, now: datetime, append: bool = False) -> int:
    """Добавляет демо-сотрудников и их записи. Возвращает число добавленных сотрудников."""
    if not append and db.scalar(select(func.count()).select_from(Employee)):
        raise SeedRefused(
            "В базе уже есть сотрудники — демо-данные не добавлены, чтобы не смешать их с настоящими. "
            "Добавить всё равно: python -m app.seed --append"
        )
    rng = random.Random(SEED)
    employees = [Employee(name=name) for name in DEMO_NAMES]
    db.add_all(employees)
    db.flush()  # нужны id сотрудников для записей

    last_day: list[TimeEntry] = []
    for day in _past_workdays(now.date(), PAST_WORKDAYS):
        for employee, tasks in zip(employees, DEMO_TASKS):
            entries = _full_day(rng, employee, tasks, day, now.tzinfo)
            db.add_all(entries)
            if employee is employees[3]:
                last_day = entries
    # «Забытый таймер»: в последний рабочий день Глеб не нажал «Закончить» —
    # его последняя запись так и идёт. Так в демо сразу видно предупреждение.
    last_day[-1].ended_at = None
    db.add_all(_today(employees, now))
    db.commit()
    return len(employees)


def main(argv: list[str] | None = None) -> int:
    """Точка входа командной строки: python -m app.seed [--append]."""
    parser = argparse.ArgumentParser(description="Заполнить базу вымышленными демо-данными.")
    parser.add_argument("--append", action="store_true", help="добавить, даже если в базе уже есть сотрудники")
    args = parser.parse_args(argv)

    init_db(engine)
    with SessionLocal() as db:
        try:
            count = seed(db, datetime.now().astimezone(), append=args.append)
        except SeedRefused as refusal:
            print(refusal)
            return 1
    print(f"Готово: {count} вымышленных сотрудников и неделя их рабочих дней. База: {engine.url.database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
