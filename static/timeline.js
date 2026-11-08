/*
 * Лента дня: раскладывает записи времени по суткам компании.
 *
 * Границы суток присылает сервер (report.days: дата + начало и конец в UTC) —
 * сутки считаются по часам компании, а не по часам компьютера, на котором
 * открыта страница. В день перевода часов сутки длятся 23 или 25 часов.
 *
 * Чистые функции без работы со страницей — проверяются тестами
 * (tests/js/timeline.test.mjs), как серверная логика.
 */
"use strict";

const HOUR_MS = 3600 * 1000;
const DEFAULT_WINDOW = [8, 18];   // шкала по умолчанию: 8:00–18:00
const MAX_DAYS = 14;              // больше двух недель лент не рисуем — нечитаемо

/**
 * entries — записи из отчёта; days — сутки из отчёта [{ date, start, end }];
 * now — текущий момент (идущая запись заканчивается «сейчас»).
 * Возвращает { days: [{ date, start, end, segments: [{ kind, description, start, end, from, to }] }],
 *              windowStart, windowEnd, tooLong }, где from/to — часы от начала суток (дробные).
 */
function buildLanes(entries, days, now) {
  if (days.length > MAX_DAYS) {
    return { days: [], windowStart: DEFAULT_WINDOW[0], windowEnd: DEFAULT_WINDOW[1], tooLong: true };
  }
  const lanes = days.map((d) => ({ date: d.date, start: new Date(d.start), end: new Date(d.end), segments: [] }));

  let earliest = Infinity;
  let latest = -Infinity;
  for (const entry of entries) {
    const entryStart = new Date(entry.started_at);
    const entryEnd = entry.ended_at ? new Date(entry.ended_at) : now;
    for (const lane of lanes) {
      // Только часть записи внутри этих суток: ночная смена делится на два дня.
      const start = new Date(Math.max(entryStart, lane.start));
      const end = new Date(Math.min(entryEnd, lane.end, now));
      if (end <= start) continue;
      const from = (start - lane.start) / HOUR_MS;
      const to = (end - lane.start) / HOUR_MS;
      lane.segments.push({ kind: entry.kind, description: entry.description, start, end, from, to });
      earliest = Math.min(earliest, from);
      latest = Math.max(latest, to);
    }
  }
  // Шкала: не уже 8:00–18:00, и расширяется, если кто-то пришёл раньше или ушёл позже.
  const windowStart = Math.min(DEFAULT_WINDOW[0], Math.floor(earliest));
  const windowEnd = Math.max(DEFAULT_WINDOW[1], Math.ceil(latest));
  return { days: lanes, windowStart, windowEnd, tooLong: false };
}

/**
 * Даты суток, в которые хоть у кого-то есть записи. Пустые дни (выходные,
 * дни до начала учёта) не рисуем — они только растягивают отчёт. Если
 * записей нет вовсе, оставляем первый день, чтобы была видна пустая шкала.
 */
function activeDays(lanes) {
  const active = lanes.days.filter((day) => day.segments.length).map((day) => day.date);
  if (!active.length && lanes.days.length) active.push(lanes.days[0].date);
  return new Set(active);
}

// Для тестов в Node.js; в браузере функции просто остаются глобальными.
if (typeof module !== "undefined") module.exports = { buildLanes, activeDays, MAX_DAYS };
