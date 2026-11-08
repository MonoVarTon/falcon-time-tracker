"""Настройки компании: часовой пояс, по которому считаются рабочие сутки.

Клиенты Falcon AI — диспетчерские США, а сотрудники бывают удалёнными
из других стран. Поэтому «сегодня», отчёт за дату и лента дня считаются
по часам КОМПАНИИ, а не по часам компьютера сотрудника или сервера.

Пояс задаётся переменной окружения COMPANY_TIMEZONE (имя из базы IANA,
например America/New_York). По умолчанию — America/Chicago.
"""
import os
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_COMPANY_TIMEZONE = "America/Chicago"


def load_company_timezone(name: str) -> ZoneInfo:
    """Загружает пояс по имени; неизвестное имя — понятная ошибка при старте.

    На Windows база поясов ставится пакетом tzdata (он есть в requirements.txt).
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(
            f"Неизвестный часовой пояс компании: «{name}». Пример правильного: America/Chicago"
        ) from error


COMPANY_TIMEZONE_NAME = os.getenv("COMPANY_TIMEZONE", DEFAULT_COMPANY_TIMEZONE)
COMPANY_TZ = load_company_timezone(COMPANY_TIMEZONE_NAME)


def company_day_bounds(day: date, tz: tzinfo = COMPANY_TZ) -> tuple[datetime, datetime]:
    """Начало и конец суток компании в UTC: [полночь дня, полночь следующего дня).

    Переводим в UTC сразу. Сравнивать даты с одним и тем же объектом пояса
    опасно: Python сравнивает их по «часам на стене», без учёта перевода часов.
    В день перевода часов сутки длятся 23 или 25 часов — так и должно быть.
    """
    start = datetime.combine(day, time(0), tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def company_today(now: datetime, tz: tzinfo = COMPANY_TZ) -> date:
    """Какая сейчас дата у компании."""
    return now.astimezone(tz).date()
