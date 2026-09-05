"""Импорт знания об оборудовании из существующего каталога упражнений.

Модуль решает задачу пункта «Import / migration существующих данных»: перевести
значения `exercises.equipment` (свободный формат источника) в нормализованные
требования, ничего не потеряв молча.

Старое поле не удаляется и не изменяется. Оно остаётся входом для повторного
импорта: сопоставление можно пересчитать после пополнения словаря, и результат
воспроизводим.

Три уровня знания, и они не смешиваются:

1. CONFIRMED — значение источника имеет ровно один canonical ID. Факт взят из
   данных каталога, а не выведен.
2. INFERRED — требование выведено правилом (по названию упражнения или по типу
   нагрузки). Такой факт помечен и виден администратору как требующий проверки.
3. Ничего — требования не установлены. Совместимость вернёт UNKNOWN. Это честный
   ответ: 122 упражнения каталога имеют значение `other`, которое означает «нужно
   оборудование, но какое — не сказано», и превращать его в «оборудование не
   нужно» значит утверждать неверное.

Почему выводом по названию нельзя ограничиться. Название `Scapular_Pull-Up` не
содержит слова «турник», хотя турник обязателен. Поэтому вывод не применяется к
силовым категориям, где пропущенный снаряд опаснее пробела в данных: упражнение
остаётся UNKNOWN и попадает в отчёт, а не получает выдуманное «собственный вес».
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.application.equipment.matching import EquipmentMatcher
from src.domain.equipment import (
    EquipmentRequirement,
    ExerciseEquipmentRequirement,
    KnowledgeConfidence,
    KnowledgeSource,
    UnmappedEquipmentValue,
    UnmappedReason,
)
from src.domain.exercise import Exercise
from src.infrastructure.persistence.postgres.equipment_repository import EquipmentIndex

# Значение источника, означающее «оборудование нужно, но какое — не указано».
AMBIGUOUS_CATALOG_VALUE = "other"

BODYWEIGHT_ID = "bodyweight"

# Категории, где отсутствие значения оборудования нельзя трактовать как
# «оборудование не нужно»: пропущенная штанга или турник в силовом упражнении
# опаснее, чем незакрытый пробел в данных.
STRENGTH_TYPES = frozenset(
    {"strength", "powerlifting", "olympic weightlifting", "strongman"}
)

RULE_CATALOG_VALUE = "rule=catalog_value"
RULE_NAME_MATCH = "rule=name_match"
RULE_BODYWEIGHT_BY_TYPE = "rule=bodyweight_by_type"


@dataclass
class ImportReport:
    """Отчёт сопоставления. Без него импорт нельзя ни проверить, ни принять."""

    exercises_total: int = 0
    mapped_exercises: int = 0
    inferred_exercises: int = 0
    unknown_exercises: int = 0
    requirements_confirmed: int = 0
    requirements_inferred: int = 0
    ambiguous_values: int = 0
    unmapped_values: int = 0
    # raw_value -> сколько упражнений его использует.
    value_counts: dict[str, int] = field(default_factory=dict)
    mapped_values: dict[str, str] = field(default_factory=dict)
    ambiguous_details: dict[str, tuple[str, ...]] = field(default_factory=dict)
    unmapped_details: dict[str, int] = field(default_factory=dict)
    duplicates_skipped: int = 0

    def as_dict(self) -> dict:
        return {
            "exercises_total": self.exercises_total,
            "mapped_exercises": self.mapped_exercises,
            "inferred_exercises": self.inferred_exercises,
            "unknown_exercises": self.unknown_exercises,
            "requirements_confirmed": self.requirements_confirmed,
            "requirements_inferred": self.requirements_inferred,
            "ambiguous_values": self.ambiguous_values,
            "unmapped_values": self.unmapped_values,
            "value_counts": dict(sorted(self.value_counts.items())),
            "mapped_values": dict(sorted(self.mapped_values.items())),
            "ambiguous_details": {
                key: list(value) for key, value in sorted(self.ambiguous_details.items())
            },
            "unmapped_details": dict(sorted(self.unmapped_details.items())),
            "duplicates_skipped": self.duplicates_skipped,
        }


@dataclass
class ImportPlan:
    """Что будет записано в базу знаний. Отделено от записи ради проверки."""

    requirements: list[ExerciseEquipmentRequirement] = field(default_factory=list)
    unmapped: list[UnmappedEquipmentValue] = field(default_factory=list)
    report: ImportReport = field(default_factory=ImportReport)


class EquipmentKnowledgeImporter:
    """Строит требования к оборудованию из значений каталога."""

    def __init__(self, index: EquipmentIndex) -> None:
        self._index = index
        self._matcher = EquipmentMatcher(index)

    def build_plan(self, exercises: list[Exercise]) -> ImportPlan:
        plan = ImportPlan()
        report = plan.report
        report.exercises_total = len(exercises)

        for exercise in exercises:
            confirmed, ambiguous, unmapped = self._map_catalog_values(exercise, plan)
            if confirmed:
                report.mapped_exercises += 1
                continue
            # Значение источника не дало canonical ID: пробуем вывод по названию.
            inferred = self._infer_from_name(exercise, plan)
            if inferred:
                report.inferred_exercises += 1
                continue
            if not ambiguous and not unmapped and self._can_assume_bodyweight(exercise):
                # Источник не указал оборудование, и категория допускает
                # трактовку «снаряд не нужен». Факт помечен как выведенный.
                plan.requirements.append(
                    ExerciseEquipmentRequirement(
                        exercise_external_id=exercise.external_id,
                        exercise_source=exercise.source,
                        equipment_id=BODYWEIGHT_ID,
                        requirement=EquipmentRequirement.REQUIRED,
                        confidence=KnowledgeConfidence.INFERRED,
                        source=KnowledgeSource.NAME_INFERENCE,
                        notes=RULE_BODYWEIGHT_BY_TYPE,
                    )
                )
                report.requirements_inferred += 1
                report.inferred_exercises += 1
                continue
            report.unknown_exercises += 1

        return plan

    # --- Значения каталога ----------------------------------------------------

    def _map_catalog_values(
        self, exercise: Exercise, plan: ImportPlan
    ) -> tuple[bool, bool, bool]:
        """Сопоставляет значения `equipment` упражнения со словарём."""
        report = plan.report
        has_confirmed = False
        has_ambiguous = False
        has_unmapped = False
        seen: set[str] = set()

        for raw_value in exercise.equipment:
            normalized = raw_value.strip()
            if not normalized:
                continue
            report.value_counts[normalized] = report.value_counts.get(normalized, 0) + 1

            match = self._matcher.match_catalog_value(normalized)
            single = match.single
            if single is not None:
                if single in seen:
                    report.duplicates_skipped += 1
                    continue
                seen.add(single)
                plan.requirements.append(
                    ExerciseEquipmentRequirement(
                        exercise_external_id=exercise.external_id,
                        exercise_source=exercise.source,
                        equipment_id=single,
                        requirement=EquipmentRequirement.REQUIRED,
                        confidence=KnowledgeConfidence.CONFIRMED,
                        source=KnowledgeSource.CATALOG_IMPORT,
                        notes=f"{RULE_CATALOG_VALUE}:{normalized}",
                    )
                )
                report.requirements_confirmed += 1
                report.mapped_values[normalized] = single
                has_confirmed = True
                continue

            if match.ambiguous:
                # Значение указывает на несколько единиц оборудования: это
                # законное «одно из», а не ошибка. Записывается группой
                # ALTERNATIVE и одновременно помечается как требующее уточнения.
                has_ambiguous = True
                report.ambiguous_values += 1
                report.ambiguous_details[normalized] = match.equipment_ids
                group = len(
                    {
                        r.alternative_group
                        for r in plan.requirements
                        if r.exercise_external_id == exercise.external_id
                        and r.alternative_group is not None
                    }
                ) + 1
                for equipment_id in match.equipment_ids:
                    plan.requirements.append(
                        ExerciseEquipmentRequirement(
                            exercise_external_id=exercise.external_id,
                            exercise_source=exercise.source,
                            equipment_id=equipment_id,
                            requirement=EquipmentRequirement.ALTERNATIVE,
                            alternative_group=group,
                            confidence=KnowledgeConfidence.INFERRED,
                            source=KnowledgeSource.CATALOG_IMPORT,
                            notes=f"{RULE_CATALOG_VALUE}:{normalized}",
                        )
                    )
                    report.requirements_inferred += 1
                plan.unmapped.append(
                    UnmappedEquipmentValue(
                        exercise_external_id=exercise.external_id,
                        exercise_source=exercise.source,
                        raw_value=normalized,
                        reason=UnmappedReason.AMBIGUOUS,
                        notes="значение указывает на несколько единиц оборудования",
                    )
                )
                continue

            has_unmapped = True
            report.unmapped_values += 1
            report.unmapped_details[normalized] = (
                report.unmapped_details.get(normalized, 0) + 1
            )
            reason = (
                UnmappedReason.AMBIGUOUS
                if normalized.lower() == AMBIGUOUS_CATALOG_VALUE
                else UnmappedReason.UNMAPPED
            )
            plan.unmapped.append(
                UnmappedEquipmentValue(
                    exercise_external_id=exercise.external_id,
                    exercise_source=exercise.source,
                    raw_value=normalized,
                    reason=reason,
                    notes=(
                        "источник не уточняет оборудование"
                        if reason is UnmappedReason.AMBIGUOUS
                        else "значение отсутствует в словаре"
                    ),
                )
            )
        return has_confirmed, has_ambiguous, has_unmapped

    # --- Вывод по названию ----------------------------------------------------

    def _infer_from_name(self, exercise: Exercise, plan: ImportPlan) -> bool:
        """Выводит оборудование из названия упражнения.

        Работает только по однозначным совпадениям. Неоднозначное совпадение
        («мяч») здесь не используется: вывод из названия и без того слабее
        значения источника, и добавлять к нему угадывание нельзя.
        """
        candidates = [exercise.name, exercise.name_ru or "", *exercise.aliases]
        match = self._matcher.match_values([c for c in candidates if c])
        if not match.confident:
            return False

        report = plan.report
        for equipment_id in sorted(match.confident):
            plan.requirements.append(
                ExerciseEquipmentRequirement(
                    exercise_external_id=exercise.external_id,
                    exercise_source=exercise.source,
                    equipment_id=equipment_id,
                    requirement=EquipmentRequirement.REQUIRED,
                    confidence=KnowledgeConfidence.INFERRED,
                    source=KnowledgeSource.NAME_INFERENCE,
                    notes=RULE_NAME_MATCH,
                )
            )
            report.requirements_inferred += 1
        return True

    @staticmethod
    def _can_assume_bodyweight(exercise: Exercise) -> bool:
        return (exercise.exercise_type or "") not in STRENGTH_TYPES
