/*
 * Лента дня: раскладывает записи времени по местным суткам и часам.
 *
 * Чистая функция без работы со страницей — поэтому её можно проверить
 * тестами (tests/js/timeline.test.mjs), как серверную логику.
 * На странице результат рисуется полосками в отчёте.
 */
"use strict";

const HOUR_MS = 3600 * 1000;
const DEFAULT_WINDOW = [8, 18];   // шкала по умолчанию: 8:00–18:00
const MAX_DAYS = 14;              // больше двух недель лент не рисуем — нечитаемо

function startOfLocalDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function nextLocalDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + 1);
}

/**
 * entries — записи из отчёта; periodStart / periodEnd — местные полночи (конец не включается);
 * now — текущий момент (идущая запись заканчивается «сейчас»).
 * Возвращает { days: [{ date, segments: [{ kind, description, start, end, from, to }] }],
 *              windowStart, windowEnd, tooLong }, где from/to — часы от начала суток (дробные).
 */
function buildLanes(entries, periodStart, periodEnd, now) {
  const days = [];
  for (let day = startOfLocalDay(periodStart); day < periodEnd; day = nextLocalDay(day)) {
    days.push({ date: day, segments: [] });
  }
  if (days.length > MAX_DAYS) {
    return { days: [], windowStart: DEFAULT_WINDOW[0], windowEnd: DEFAULT_WINDOW[1], tooLong: true };
  }

  let earliest = Infinity;
  let latest = -Infinity;
  for (const entry of entries) {
    const entryStart = new Date(entry.started_at);
    const entryEnd = entry.ended_at ? new Date(entry.ended_at) : now;
    for (const lane of days) {
      const dayEnd = nextLocalDay(lane.date);
      // Только часть записи внутри этих суток: работа 23:00–01:00 делится на два дня.
      const start = new Date(Math.max(entryStart, lane.date));
      const end = new Date(Math.min(entryEnd, dayEnd, now));
      if (end <= start) continue;
      const from = (start - lane.date) / HOUR_MS;
      const to = (end - lane.date) / HOUR_MS;
      lane.segments.push({ kind: entry.kind, description: entry.description, start, end, from, to });
      earliest = Math.min(earliest, from);
      latest = Math.max(latest, to);
    }
  }
  // Шкала: не уже 8:00–18:00, и расширяется, если кто-то пришёл раньше или ушёл позже.
  const windowStart = Math.min(DEFAULT_WINDOW[0], Math.floor(earliest));
  const windowEnd = Math.max(DEFAULT_WINDOW[1], Math.ceil(latest));
  return { days, windowStart, windowEnd, tooLong: false };
}

/**
 * Дни, в которые хоть у кого-то есть записи (метки времени местных полуночей).
 * Пустые дни — выходные, дни до начала учёта — в ленте не рисуем: они только
 * растягивают отчёт. Если записей нет вовсе, оставляем первый день периода,
 * чтобы было видно пустую шкалу, а не пропавшую ленту.
 */
function activeDays(lanes) {
  const active = lanes.days.filter((day) => day.segments.length).map((day) => day.date.getTime());
  if (!active.length && lanes.days.length) active.push(lanes.days[0].date.getTime());
  return new Set(active);
}

// Для тестов в Node.js; в браузере функции просто остаются глобальными.
if (typeof module !== "undefined") module.exports = { buildLanes, activeDays, MAX_DAYS };
