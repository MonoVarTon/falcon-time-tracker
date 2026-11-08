// Проверки ленты дня. Запуск: node --test tests/js/timeline.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { buildLanes, activeDays, MAX_DAYS } = require("../../static/timeline.js");

// Сутки компании в Чикаго (зимнее время, UTC−6): полночь = 06:00 UTC.
const iso = (d, h, m = 0) => new Date(Date.UTC(2026, 10, d, h, m)).toISOString();
const chicagoDay = (d) => ({ date: `2026-11-${String(d).padStart(2, "0")}`, start: iso(d, 6), end: iso(d + 1, 6) });
const days = (from, to) => Array.from({ length: to - from + 1 }, (_, i) => chicagoDay(from + i));
const NOW = new Date(iso(12, 18));  // 12 ноября, 12:00 по Чикаго

test("запись внутри дня — один отрезок на часах компании", () => {
  // 09:00–12:30 по Чикаго = 15:00–18:30 UTC
  const lanes = buildLanes([{ kind: "work", description: "Рейс", started_at: iso(10, 15), ended_at: iso(10, 18, 30) }],
                           days(10, 10), NOW);
  assert.deepEqual(lanes.days[0].segments.map((s) => [s.kind, s.from, s.to]), [["work", 9, 12.5]]);
});

test("ночная смена 23:00–01:00 по Чикаго делится между двумя днями компании", () => {
  const lanes = buildLanes([{ kind: "work", description: "Ночь", started_at: iso(11, 5), ended_at: iso(11, 7) }],
                           days(10, 11), NOW);
  assert.deepEqual(lanes.days.map((d) => d.segments.map((s) => [s.from, s.to])), [[[23, 24]], [[0, 1]]]);
  assert.deepEqual([lanes.windowStart, lanes.windowEnd], [0, 24]);
});

test("смена удалённого диспетчера не рвётся по его местной полуночи", () => {
  // 17:00–02:00 по Чикаго; у диспетчера в UTC+3 это 02:00–11:00 следующего дня — но день компании один.
  const lanes = buildLanes([{ kind: "work", description: "Смена", started_at: iso(10, 23), ended_at: iso(11, 2) }],
                           days(10, 10), NOW);
  assert.deepEqual(lanes.days[0].segments.map((s) => [s.from, s.to]), [[17, 20]]);
});

test("сутки перевода часов длиной 25 часов", () => {
  const nov1 = { date: "2026-11-01", start: iso(1, 5), end: iso(2, 6) };  // 1 ноября — 25 часов
  const lanes = buildLanes([{ kind: "work", description: "Поздно", started_at: iso(2, 4), ended_at: iso(2, 6) }],
                           [nov1], NOW);
  assert.deepEqual(lanes.days[0].segments.map((s) => [s.from, s.to]), [[23, 25]]);
  assert.equal(lanes.windowEnd, 25);
});

test("идущая запись заканчивается «сейчас»", () => {
  const lanes = buildLanes([{ kind: "lunch", description: "Обед", started_at: iso(12, 17, 30), ended_at: null }],
                           days(12, 12), NOW);
  assert.deepEqual(lanes.days[0].segments.map((s) => [s.kind, s.from, s.to]), [["lunch", 11.5, 12]]);
});

test("записи вне периода не попадают в ленту", () => {
  const lanes = buildLanes([{ kind: "work", description: "Вчера", started_at: iso(9, 15), ended_at: iso(9, 23) }],
                           days(10, 10), NOW);
  assert.equal(lanes.days[0].segments.length, 0);
});

test("пустой период — шкала по умолчанию 8:00–18:00", () => {
  const lanes = buildLanes([], days(10, 10), NOW);
  assert.deepEqual([lanes.windowStart, lanes.windowEnd], [8, 18]);
});

test("слишком длинный период — ленты не строятся", () => {
  const lanes = buildLanes([], days(1, 1 + MAX_DAYS), NOW);
  assert.equal(lanes.tooLong, true);
});

test("пустые дни пропускаются, дни с записями остаются", () => {
  const lanes = buildLanes([{ kind: "work", description: "Пн", started_at: iso(9, 15), ended_at: iso(9, 23) },
                            { kind: "work", description: "Ср", started_at: iso(11, 15), ended_at: iso(11, 23) }],
                           days(7, 11), NOW);
  assert.deepEqual([...activeDays(lanes)], ["2026-11-09", "2026-11-11"]);
});

test("если записей нет совсем — остаётся одна пустая полоса", () => {
  assert.deepEqual([...activeDays(buildLanes([], days(7, 8), NOW))], ["2026-11-07"]);
});
