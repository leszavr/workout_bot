// Тесты форматирования и сборки запросов админ-интерфейса.
//
// Проверяется то, что легко сломать незаметно и что напрямую меняет смысл
// показанного: null-значение показывается как «нет данных», а не как ноль, и
// фильтр `false` доходит до сервера, а не отбрасывается как «не задан».
//
// Запуск: npm test (node --test, без дополнительных зависимостей).

import test from "node:test";
import assert from "node:assert/strict";

import { analyticsQuery } from "./api.ts";
import {
  count,
  dateTime,
  duration,
  generationStatusTone,
  metricLabel,
  percent,
  validationStateLabel,
} from "./labels.ts";

test("процент: null — это «нет данных», а не ноль", () => {
  // Ноль означал бы измеренный результат: «отказов не было». null означает,
  // что делить не на что — генераций не было вовсе.
  assert.equal(percent(null), "—");
  assert.equal(percent(undefined), "—");
  assert.equal(percent(0), "0%");
});

test("процент: дробная часть не показывается, если она нулевая", () => {
  assert.equal(percent(66.7), "66.7%");
  assert.equal(percent(100), "100%");
});

test("число: null не превращается в ноль", () => {
  assert.equal(count(null), "—");
  assert.equal(count(0), "0");
});

test("длительность: единицы меняются по величине", () => {
  assert.equal(duration(null), "—");
  assert.equal(duration(120), "120 мс");
  assert.equal(duration(1500), "1.5 с");
  assert.equal(duration(45_000), "45 с");
  assert.equal(duration(90_000), "1 мин 30 с");
  assert.equal(duration(120_000), "2 мин");
});

test("дата: пустое значение не показывается как Invalid Date", () => {
  assert.equal(dateTime(null), "—");
  assert.equal(dateTime(""), "—");
  // Нераспознанная строка возвращается как есть: терять данные хуже, чем
  // показать их в исходном виде.
  assert.equal(dateTime("не дата"), "не дата");
});

test("тон статуса генерации отражает итог", () => {
  assert.equal(generationStatusTone("succeeded"), "ok");
  assert.equal(generationStatusTone("failed"), "bad");
  assert.equal(generationStatusTone("running"), "warn");
  assert.equal(generationStatusTone("что-то новое"), "neutral");
});

test("подписи: незнакомое значение возвращается как есть", () => {
  assert.equal(metricLabel("success_rate"), "Доля успешных генераций");
  assert.equal(metricLabel("unknown_metric"), "unknown_metric");
  assert.equal(validationStateLabel("repaired"), "принято после исправления");
  assert.equal(validationStateLabel("нечто"), "нечто");
});

test("запрос аналитики: пустой фильтр не добавляет query-строку", () => {
  assert.equal(analyticsQuery(), "");
  assert.equal(analyticsQuery({}), "");
});

test("запрос аналитики: false — это фильтр, а не отсутствие фильтра", () => {
  // «Без подмены генератора» — осмысленное условие. Если отбрасывать false
  // вместе с undefined, фильтр молча перестаёт работать.
  assert.equal(analyticsQuery({ fallback: false }), "?fallback=false");
  assert.equal(analyticsQuery({ fallback: true }), "?fallback=true");
});

test("запрос аналитики: пустые строки и undefined не отправляются", () => {
  assert.equal(analyticsQuery({ model: "", provider: undefined }), "");
});

test("запрос аналитики: фильтры и дополнительные параметры объединяются", () => {
  const query = analyticsQuery(
    { model: "claude", validation: "repaired" },
    { limit: 25, offset: 50 },
  );
  const params = new URLSearchParams(query.slice(1));
  assert.equal(params.get("model"), "claude");
  assert.equal(params.get("validation"), "repaired");
  assert.equal(params.get("limit"), "25");
  assert.equal(params.get("offset"), "50");
});

test("запрос аналитики: числовая версия инструкции передаётся числом", () => {
  const params = new URLSearchParams(analyticsQuery({ prompt_version: 2 }).slice(1));
  assert.equal(params.get("prompt_version"), "2");
});
