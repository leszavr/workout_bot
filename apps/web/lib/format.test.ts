// Тесты форматирования и сборки запросов админ-интерфейса.
//
// Проверяется то, что легко сломать незаметно и что напрямую меняет смысл
// показанного: null-значение показывается как «нет данных», а не как ноль, и
// фильтр `false` доходит до сервера, а не отбрасывается как «не задан».
//
// Запуск: npm test (node --test, без дополнительных зависимостей).

import test from "node:test";
import assert from "node:assert/strict";

import { analyticsQuery, ingestionRecordsQuery } from "./api.ts";
import {
  INGESTION_DECISION_LABELS,
  count,
  dateTime,
  duration,
  generationStatusTone,
  ingestionDecisionTone,
  ingestionReasonLabel,
  metricLabel,
  percent,
  qualityStatusTone,
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

// --- Внешние источники знаний об упражнениях ---------------------------------

test("запрос записей: многозначные фильтры повторяют параметр", () => {
  // Сервер читает несколько значений одного параметра. Если склеить их запятой,
  // фильтр молча перестанет работать: сервер получит одно значение
  // «new_relevant,enrichable», не найдёт такого решения и вернёт пустой список.
  const query = ingestionRecordsQuery({
    decision: ["new_relevant", "enrichable"],
    source: ["a/b", "c/d"],
  });
  assert.ok(query.includes("decision=new_relevant"));
  assert.ok(query.includes("decision=enrichable"));
  assert.ok(query.includes("source=a%2Fb"));
  assert.ok(query.includes("source=c%2Fd"));
});

test("запрос записей: нулевая уверенность — это граница, а не пустое значение", () => {
  const query = ingestionRecordsQuery({ min_confidence: 0 });
  assert.ok(query.includes("min_confidence=0"));
});

test("запрос записей: пагинация задаётся по умолчанию", () => {
  const query = ingestionRecordsQuery();
  assert.ok(query.includes("limit=50"));
  assert.ok(query.includes("offset=0"));
});

test("подписи решений: код без перевода не показывается пользователю", () => {
  // В интерфейсе не должно быть английских кодов: каждое решение и каждая
  // причина обязаны иметь русскую подпись.
  for (const decision of [
    "existing",
    "enrichable",
    "new_relevant",
    "duplicate_variant",
    "low_quality",
    "questionable",
    "unknown",
  ]) {
    assert.ok(INGESTION_DECISION_LABELS[decision], decision);
  }
  for (const reason of [
    "existing_source_link",
    "normalized_name_match",
    "variant_tokens_differ",
    "equipment_differs",
    "target_in_secondary_muscles",
    "technique_missing",
    "filled_missing_value",
  ]) {
    assert.notEqual(ingestionReasonLabel(reason), reason, reason);
  }
});

test("неизвестная причина показывается как есть, а не теряется", () => {
  // Отчёт может добавить причину раньше, чем интерфейс: показать код лучше, чем
  // показать пустое место.
  assert.equal(ingestionReasonLabel("brand_new_reason"), "brand_new_reason");
});

test("тон решения: новое упражнение и низкое качество различаются", () => {
  assert.equal(ingestionDecisionTone("new_relevant"), "ok");
  assert.equal(ingestionDecisionTone("low_quality"), "bad");
  // «Требует проверки» — предупреждение, а не отказ: запись может быть верной.
  assert.equal(ingestionDecisionTone("questionable"), "warn");
  assert.equal(ingestionDecisionTone("unknown"), "warn");
});

test("тон качества: на проверку — предупреждение, непригодно — отказ", () => {
  assert.equal(qualityStatusTone("ready"), "ok");
  assert.equal(qualityStatusTone("review"), "warn");
  assert.equal(qualityStatusTone("reject"), "bad");
});
