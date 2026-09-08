// Тесты группировки требований к оборудованию.
//
// Проверяется смысл, а не разметка: от группировки зависит, прочитает
// администратор «без этого упражнение не выполнить» или «достаточно любого из
// вариантов». Ошибка здесь на экране незаметна — список выглядит правдоподобно
// в любом случае.
//
// Запуск: npm test (node --test, без дополнительных зависимостей).

import test from "node:test";
import assert from "node:assert/strict";

import type { ExerciseRequirement, RequirementKind } from "./api.ts";
import { groupRequirements, requirementName } from "./requirements.ts";

interface RequirementFields {
  requirement: RequirementKind;
  equipment_id?: string | null;
  capability_id?: string | null;
  alternative_group?: number | null;
}

/** Требование с заполненными обязательными полями ответа сервера. */
function requirement(fields: RequirementFields): ExerciseRequirement {
  return {
    equipment_id: fields.equipment_id ?? null,
    capability_id: fields.capability_id ?? null,
    requirement: fields.requirement,
    alternative_group: fields.alternative_group ?? null,
    confidence: "confirmed",
    source: "source_data",
    notes: null,
  };
}

test("обязательное, желательное и «одно из» разделены", () => {
  const grouped = groupRequirements([
    requirement({ requirement: "required", equipment_id: "barbell" }),
    requirement({ requirement: "optional", equipment_id: "belt" }),
    requirement({
      requirement: "alternative",
      equipment_id: "bench",
      alternative_group: 1,
    }),
  ]);

  assert.equal(grouped.required.length, 1);
  assert.equal(grouped.optional.length, 1);
  assert.equal(grouped.alternatives.length, 1);
  assert.equal(grouped.alternatives[0].variants.length, 1);
});

test("варианты одной группы держатся вместе", () => {
  // Три отдельные строки не сообщают, что достаточно любого варианта, поэтому
  // группа обязана остаться одним элементом.
  const grouped = groupRequirements([
    requirement({
      requirement: "alternative",
      equipment_id: "dumbbell",
      alternative_group: 2,
    }),
    requirement({
      requirement: "alternative",
      equipment_id: "kettlebell",
      alternative_group: 2,
    }),
    requirement({
      requirement: "alternative",
      equipment_id: "barbell",
      alternative_group: 5,
    }),
  ]);

  assert.equal(grouped.alternatives.length, 2);
  // Группы упорядочены по номеру: иначе порядок зависел бы от порядка выдачи
  // базы и менялся между открытиями страницы.
  assert.deepEqual(
    grouped.alternatives.map((entry) => entry.group),
    [2, 5],
  );
  assert.deepEqual(
    grouped.alternatives[0].variants.map((item) => item.equipment_id),
    ["dumbbell", "kettlebell"],
  );
});

test("«одно из» без группы считается обязательным", () => {
  // Вариант, не связанный с другими, выбора не даёт: показывать его как «одно
  // из» значило бы обещать альтернативу, которой нет.
  const grouped = groupRequirements([
    requirement({ requirement: "alternative", equipment_id: "rings" }),
  ]);

  assert.equal(grouped.alternatives.length, 0);
  assert.equal(grouped.required.length, 1);
  assert.equal(grouped.required[0].equipment_id, "rings");
});

test("пустой список требований не превращается в «оборудование не нужно»", () => {
  // Отсутствие требований — «неизвестно». Функция обязана вернуть пустые
  // группы, а страница на них показывает предупреждение, а не пустой список.
  const grouped = groupRequirements([]);
  assert.deepEqual(grouped, { required: [], optional: [], alternatives: [] });
});

test("название берётся из заполненного поля требования", () => {
  const byEquipment = requirement({
    requirement: "required",
    equipment_id: "leg_press",
  });
  const byCapability = requirement({
    requirement: "required",
    capability_id: "horizontal_push",
  });

  const equipmentName = (id: string) => `оборудование ${id}`;
  const capabilityName = (id: string) => `возможность ${id}`;

  assert.equal(
    requirementName(byEquipment, equipmentName, capabilityName),
    "оборудование leg_press",
  );
  assert.equal(
    requirementName(byCapability, equipmentName, capabilityName),
    "возможность horizontal_push",
  );
});

test("требование без ссылки не выглядит как отсутствие требования", () => {
  // Пустая строка в списке читалась бы как «ничего не требуется»; это другое
  // утверждение, поэтому подпись явная.
  const broken = requirement({ requirement: "required" });
  assert.equal(
    requirementName(
      broken,
      (id) => id,
      (id) => id,
    ),
    "требование без ссылки",
  );
});
