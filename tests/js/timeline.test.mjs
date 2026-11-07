// Проверки ленты дня. Запуск: node --test tests/js/timeline.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { buildLanes, activeDays, MAX_DAYS } = require("../../static/timeline.js");

// Все даты — местное время: лента считает сутки по местным часам.
const at = (d, h, m = 0) => new Date(2026, 10, d, h, m).toISOString();
const day = (d) => new Date(2026, 10, d);
const NOW = new Date(2026, 10, 12, 12, 0);

test("запись внутри дня — один отрезок на своих часах", () => {
  const lanes = buildLanes([{ kind: "work", description: "Отчёт", started_at: at(10, 9), ended_at: at(10, 12, 30) }],
                           day(10), day(11), NOW);
  assert.equal(lanes.days.length, 1);
  assert.deepEqual(lanes.days[0].segments.map((s) => [s.kind, s.from, s.to]), [["work", 9, 12.5]]);
});

test("ночная работа 23:00–01:00 делится между двумя днями", () => {
  const lanes = buildLanes([{ kind: "work", description: "Ночь", started_at: at(10, 23), ended_at: at(11, 1) }],
                           day(10), day(12), NOW);
  assert.deepEqual(lanes.days.map((d) => d.segments.map((s) => [s.from, s.to])), [[[23, 24]], [[0, 1]]]);
  assert.equal(lanes.windowStart, 0);
  assert.equal(lanes.windowEnd, 24);
});

test("идущая запись заканчивается «сейчас», а не в конце суток", () => {
  const lanes = buildLanes([{ kind: "lunch", description: "Обед", started_at: at(12, 11, 30), ended_at: null }],
                           day(12), day(13), NOW);
  assert.deepEqual(lanes.days[0].segments.map((s) => [s.kind, s.from, s.to]), [["lunch", 11.5, 12]]);
});

test("записи вне периода не попадают в ленту", () => {
  const lanes = buildLanes([{ kind: "work", description: "Вчера", started_at: at(9, 9), ended_at: at(9, 17) }],
                           day(10), day(11), NOW);
  assert.equal(lanes.days[0].segments.length, 0);
});

test("пустой период — шкала по умолчанию 8:00–18:00", () => {
  const lanes = buildLanes([], day(10), day(11), NOW);
  assert.deepEqual([lanes.windowStart, lanes.windowEnd], [8, 18]);
});

test("ранний приход расширяет шкалу", () => {
  const lanes = buildLanes([{ kind: "work", description: "Рано", started_at: at(10, 6, 40), ended_at: at(10, 9) }],
                           day(10), day(11), NOW);
  assert.equal(lanes.windowStart, 6);
});

test("слишком длинный период — ленты не строятся", () => {
  const lanes = buildLanes([], day(1), new Date(2026, 10, 1 + MAX_DAYS + 1), NOW);
  assert.equal(lanes.tooLong, true);
  assert.equal(lanes.days.length, 0);
});

test("пустые дни пропускаются, дни с записями остаются", () => {
  const lanes = buildLanes([{ kind: "work", description: "Пн", started_at: at(9, 9), ended_at: at(9, 17) },
                            { kind: "work", description: "Ср", started_at: at(11, 9), ended_at: at(11, 17) }],
                           day(7), day(12), NOW);  // сб 7 — ср 11
  assert.deepEqual([...activeDays(lanes)], [day(9).getTime(), day(11).getTime()]);
});

test("если записей нет совсем — остаётся одна пустая полоса", () => {
  const lanes = buildLanes([], day(7), day(9), NOW);
  assert.deepEqual([...activeDays(lanes)], [day(7).getTime()]);
});
