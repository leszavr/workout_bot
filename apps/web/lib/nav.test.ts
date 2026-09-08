// Тесты навигации внутреннего интерфейса.
//
// Проверяется то, что нельзя увидеть на скриншоте, но что напрямую меняет
// поведение: какой пункт меню подсвечен для конкретного адреса и что видит
// наблюдатель. Ошибка подсветки не ломает переходы, но администратор перестаёт
// понимать, где находится, а лишний пункт в меню наблюдателя обещает доступ,
// которого сервер не даст.
//
// Запуск: npm test (node --test, без дополнительных зависимостей).

import test from "node:test";
import assert from "node:assert/strict";

import {
  NAV_GROUPS,
  activeNavItem,
  isNavItemActive,
  navGroupsFor,
} from "./nav.ts";

function itemFor(href: string) {
  for (const group of NAV_GROUPS) {
    const found = group.items.find((item) => item.href === href);
    if (found) return found;
  }
  throw new Error(`нет пункта ${href}`);
}

test("сводка активна только на своём адресе", () => {
  // Раздел «/» — префикс любого пути, поэтому сравнение по префиксу
  // подсвечивало бы сводку на каждой странице сразу.
  const dashboard = itemFor("/");
  assert.equal(isNavItemActive(dashboard, "/"), true);
  assert.equal(isNavItemActive(dashboard, "/profiles"), false);
  assert.equal(isNavItemActive(dashboard, "/exercises/42"), false);
});

test("карточка сущности остаётся в своём разделе", () => {
  const exercises = itemFor("/exercises");
  assert.equal(isNavItemActive(exercises, "/exercises"), true);
  assert.equal(isNavItemActive(exercises, "/exercises/42"), true);
});

test("схожий префикс не считается тем же разделом", () => {
  // «/exercises-archive» начинается на «/exercises», но это другой раздел:
  // сравнение делается по границе сегмента пути, а не по строке.
  const exercises = itemFor("/exercises");
  assert.equal(isNavItemActive(exercises, "/exercises-archive"), false);
});

test("вкладки раздела не выводят из раздела", () => {
  const knowledge = itemFor("/knowledge");
  assert.equal(isNavItemActive(knowledge, "/knowledge/health"), true);
  assert.equal(isNavItemActive(knowledge, "/knowledge/unmapped"), true);

  const ai = itemFor("/ai");
  assert.equal(isNavItemActive(ai, "/ai/prompts"), true);
  assert.equal(isNavItemActive(ai, "/ai/generations/job-1"), true);
});

test("наблюдатель не видит пункты, где сервер откажет", () => {
  const viewerHrefs = navGroupsFor(false).flatMap((group) =>
    group.items.map((item) => item.href),
  );
  const adminHrefs = navGroupsFor(true).flatMap((group) =>
    group.items.map((item) => item.href),
  );

  assert.ok(!viewerHrefs.includes("/users"));
  assert.ok(adminHrefs.includes("/users"));
  // Остальные разделы доступны для чтения: скрывать их значило бы прятать
  // состояние системы от того, кому дали доступ на просмотр.
  assert.ok(viewerHrefs.includes("/infrastructure"));
  assert.ok(viewerHrefs.includes("/ai"));
});

test("пустых групп в меню не остаётся", () => {
  // Группа «Система» без «Пользователей» всё равно содержит другие пункты;
  // проверяем сам инвариант, чтобы заголовок без пунктов не появился при
  // будущем изменении состава.
  for (const group of navGroupsFor(false)) {
    assert.ok(group.items.length > 0, `пустая группа ${group.title}`);
  }
});

test("продуктовые данные и инфраструктура — разные группы", () => {
  // Требование раздела: администратор должен отличать «у пользователя нет
  // программы» от «шлюз не отвечает», поэтому разделы не смешиваются в одной
  // группе меню.
  const groupOf = (href: string) =>
    NAV_GROUPS.find((group) => group.items.some((item) => item.href === href))
      ?.title;

  assert.equal(groupOf("/profiles"), groupOf("/programs"));
  assert.notEqual(groupOf("/profiles"), groupOf("/infrastructure"));
  assert.notEqual(groupOf("/exercises"), groupOf("/infrastructure"));
});

test("заголовок в шапке берётся из раздела адреса", () => {
  assert.equal(activeNavItem("/knowledge/unmapped")?.href, "/knowledge");
  assert.equal(activeNavItem("/ai/logs")?.href, "/ai");
  assert.equal(activeNavItem("/")?.href, "/");
  // Страница вне разделов меню (смена пароля) заголовка раздела не получает:
  // придумывать ей чужой раздел значило бы соврать о расположении.
  assert.equal(activeNavItem("/change-password"), null);
});
