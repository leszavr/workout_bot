// Группировка требований к оборудованию.
//
// Отдельный модуль, а не код внутри страницы: от этой группировки зависит, что
// администратор прочитает как «без этого упражнение не выполнить», а что — как
// «достаточно любого из вариантов». Ошибка здесь не видна на экране (список
// выглядит правдоподобно в любом случае), поэтому логика вынесена туда, где её
// можно проверить тестом.

// Тип импортируется как type: так строка исчезает при компиляции, и модуль
// остаётся исполняемым без разрешения псевдонима путей (нужно для node --test).
import type { ExerciseRequirement } from "@/lib/api";

export interface AlternativeGroup {
  /** Номер группы из данных: группы независимы между собой. */
  group: number;
  variants: ExerciseRequirement[];
}

export interface GroupedRequirements {
  /** Обязательные: их отсутствие даёт «несовместимо». */
  required: ExerciseRequirement[];
  /** Желательные: упражнение выполнимо и без них. */
  optional: ExerciseRequirement[];
  /** «Одно из вариантов»: достаточно любого элемента группы. */
  alternatives: AlternativeGroup[];
}

/**
 * Разложить требования по характеру.
 *
 * `alternative` без номера группы попадает в обязательные: вариант, не
 * связанный с другими, не даёт выбора, и показывать его как «одно из» значило бы
 * обещать альтернативу, которой нет.
 *
 * Группы упорядочены по номеру, а варианты внутри группы сохраняют порядок
 * данных: перестановка меняла бы то, что администратор читает первым.
 */
export function groupRequirements(
  requirements: readonly ExerciseRequirement[],
): GroupedRequirements {
  const required: ExerciseRequirement[] = [];
  const optional: ExerciseRequirement[] = [];
  // Обычный объект, а не Map: цель компиляции — ES5, и перебор Map требовал бы
  // downlevelIteration.
  const groups: Record<number, ExerciseRequirement[]> = {};

  for (const requirement of requirements) {
    if (
      requirement.requirement === "alternative" &&
      requirement.alternative_group !== null
    ) {
      const key = requirement.alternative_group;
      groups[key] = [...(groups[key] ?? []), requirement];
    } else if (requirement.requirement === "optional") {
      optional.push(requirement);
    } else {
      required.push(requirement);
    }
  }

  const alternatives = Object.keys(groups)
    .map(Number)
    .sort((a, b) => a - b)
    .map((group) => ({ group, variants: groups[group] }));

  return { required, optional, alternatives };
}

/**
 * Название требования: конкретное оборудование или возможность.
 *
 * Требование задаётся либо тем, либо другим, поэтому подпись выбирается по
 * заполненному полю, а не по типу требования.
 */
export function requirementName(
  requirement: ExerciseRequirement,
  equipmentName: (id: string) => string,
  capabilityName: (id: string) => string,
): string {
  if (requirement.equipment_id) return equipmentName(requirement.equipment_id);
  if (requirement.capability_id) {
    return capabilityName(requirement.capability_id);
  }
  // Ни того, ни другого быть не должно, но пустая строка выглядела бы как
  // отсутствие требования, а его отсутствие — другое утверждение.
  return "требование без ссылки";
}
