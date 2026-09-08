// Тесты сборки строк запроса списков.
//
// Проверяется то, что не видно в интерфейсе, но что напрямую меняет выборку:
// значения «любое»/«все» — отсутствие фильтра, `false` — это фильтр, а не
// «не задан», а несколько значений одного фильтра уходят одним повторяющимся
// параметром.
//
// Запуск: npm test (node --test, без дополнительных зависимостей).

import test from "node:test";
import assert from "node:assert/strict";

import { exercisesQuery, profilesQuery } from "./api.ts";

test("анкеты: false доходит до сервера как фильтр, а не отбрасывается", () => {
  // «без программы» — содержательный фильтр. `if (generated)` выбросил бы его,
  // и запрос вернул бы анкеты с программами вперемешку.
  const qs = profilesQuery({ generated: false });
  assert.match(qs, /generated=false/);
});

test("анкеты: пустой фильтр не порождает мусорных параметров", () => {
  const qs = profilesQuery({});
  assert.doesNotMatch(qs, /generated/);
  assert.doesNotMatch(qs, /delivered/);
  assert.match(qs, /limit=50/);
});

test("упражнения: несколько значений оборудования — повторяющийся параметр", () => {
  // FastAPI собирает повторяющийся параметр в список: «штанга или гантели» —
  // один запрос, а не два.
  const qs = exercisesQuery({ equipment: ["barbell", "dumbbell"] });
  const params = new URLSearchParams(qs);
  assert.deepEqual(params.getAll("equipment"), ["barbell", "dumbbell"]);
});

test("упражнения: «любое» требование не уходит в запрос", () => {
  // «любое» — значение по умолчанию и не фильтрует. Отправить его значило бы
  // наложить фильтр там, где администратор его не задавал.
  const qs = exercisesQuery({ requirement_kind: "any" });
  assert.doesNotMatch(qs, /requirement_kind/);
});

test("упражнения: «все» знание об оборудовании не уходит в запрос", () => {
  const qs = exercisesQuery({ equipment_knowledge: "all" });
  assert.doesNotMatch(qs, /equipment_knowledge/);
});

test("упражнения: допущение об отсутствии передаётся явно", () => {
  // Флаг меняет ответ системы «неизвестно» на «нет», поэтому он обязан быть в
  // адресе, чтобы результат можно было воспроизвести по ссылке.
  const qs = exercisesQuery({ assume_unlisted_unavailable: true });
  assert.match(qs, /assume_unlisted_unavailable=true/);
  const qsDefault = exercisesQuery({ assume_unlisted_unavailable: false });
  assert.doesNotMatch(qsDefault, /assume_unlisted_unavailable/);
});

test("упражнения: сортировка и порядок попадают в адрес", () => {
  const qs = exercisesQuery({
    sort_by: "difficulty",
    order: "desc",
    with_facets: true,
  });
  assert.match(qs, /sort_by=difficulty/);
  assert.match(qs, /order=desc/);
  assert.match(qs, /with_facets=true/);
});