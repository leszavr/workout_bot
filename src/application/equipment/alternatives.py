"""Вывод альтернативных упражнений из признаков каталога и базы знаний.

Простое «A заменяет B» здесь недостаточно: пользователю нужно знать, насколько
замена равноценна. Поэтому альтернатива вычисляется по совокупности признаков и
получает явный тип замены.

Признаки, участвующие в сравнении, — только те, что фактически существуют в
каталоге и в базе знаний:

- primary_muscles — основные мышцы (обязательное совпадение: без него это не
  замена, а другое упражнение);
- movement pattern — выражен парой `force` + `mechanic` (тяга/жим,
  базовое/изолированное). Отдельного поля паттерна в каталоге нет, и выдумывать
  его на этом этапе не требуется;
- secondary_muscles — дополнительные мышцы;
- difficulty — уровень;
- equipment requirements — набор требуемого оборудования;
- stability requirements — выражены возможностями оборудования
  (`unstable_support`, `fixed_path`): тренажёр с фиксированной траекторией
  требует меньшей стабилизации, чем свободный вес;
- exercise_type — категория нагрузки.

Диапазон движения, положение тела и unilateral/bilateral в каталоге отдельными
полями не представлены. Выводить их из названий значило бы строить догадку и
предъявлять её как факт, поэтому они в оценку не входят, и это ограничение
зафиксировано в отчёте этапа, а не спрятано.

Тип замены назначается по совпадению признаков, а не по величине оценки:

- EXACT — упражнения неотличимы по всем имеющимся признакам: те же основные
  мышцы, тот же паттерн, тот же набор требуемого оборудования и тот же характер
  стабилизации. Требуется равенство именно набора оборудования, а не его
  категории: `barbell` и `dumbbell` обе относятся к `free_weight`, и по категории
  жим гантелей объявлялся бы полной заменой жима штанги — то есть другое
  упражнение выдавалось бы за то же самое;
- SIMILAR — те же основные мышцы и тот же паттерн, но другое оборудование или
  другой характер стабилизации;
- PARTIAL — совпадают основные мышцы, но паттерн или механика отличаются.

EXACT означает «неотличимо по признакам, которые есть в каталоге», а не
«физически идентично». Угол наклона, диапазон движения и положение тела в
каталоге не представлены, поэтому два упражнения, различающиеся только углом,
получают EXACT. Это ограничение данных, и оно зафиксировано в отчёте этапа, а не
скрыто за уверенной формулировкой.

Ниже порога совпадения альтернатива не создаётся вовсе: пустой список честнее
случайного упражнения, выданного как замена.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.equipment import (
    EquipmentRequirement,
    ExerciseAlternative,
    ExerciseEquipmentRequirement,
    KnowledgeSource,
    SubstitutionType,
)
from src.domain.exercise import Exercise
from src.infrastructure.persistence.postgres.equipment_repository import EquipmentIndex

# Возможности, описывающие требование к стабилизации. Свободный вес требует
# активной стабилизации, фиксированная траектория — нет, и подменять одно другим
# без пометки нельзя.
STABILITY_CAPABILITIES = frozenset({"unstable_support", "fixed_path", "free_weight"})

# Сколько альтернатив хранить на упражнение. Ограничение существует, потому что
# внутри группы «жим на грудь со штангой» кандидатов десятки, а пользователю и AI
# нужны лучшие, а не все.
MAX_ALTERNATIVES = 5

# Минимальная оценка. Ниже — не альтернатива: совпали только мышцы, и то
# частично.
MIN_SCORE = 0.5

# Вклад признаков в оценку. Основные мышцы обязательны и потому не оцениваются:
# без них кандидат отбрасывается до подсчёта.
WEIGHT_PATTERN = 0.30
WEIGHT_SECONDARY = 0.15
WEIGHT_DIFFICULTY = 0.15
WEIGHT_TYPE = 0.10
WEIGHT_STABILITY = 0.15
WEIGHT_EQUIPMENT_CLASS = 0.15


@dataclass(frozen=True)
class ExerciseFeatures:
    """Признаки упражнения, участвующие в сравнении.

    ``equipment_known`` отделяет «требований нет» от «требования неизвестны». Без
    этого различения два упражнения без записанных требований выглядели бы как
    имеющие одинаковое оборудование, и оба получали бы тип EXACT — то есть
    неизвестность превращалась бы в утверждение о полной эквивалентности.
    """

    external_id: str
    source: str
    primary_muscles: frozenset[str]
    secondary_muscles: frozenset[str]
    force: str | None
    mechanic: str | None
    difficulty: str | None
    exercise_type: str | None
    equipment: frozenset[str]
    equipment_categories: frozenset[str]
    stability: frozenset[str]
    equipment_known: bool

    @property
    def key(self) -> tuple[str, str]:
        return (self.external_id, self.source)


@dataclass
class AlternativesReport:
    exercises_total: int = 0
    exercises_with_alternatives: int = 0
    alternatives_total: int = 0
    by_substitution: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "exercises_total": self.exercises_total,
            "exercises_with_alternatives": self.exercises_with_alternatives,
            "alternatives_total": self.alternatives_total,
            "by_substitution": dict(sorted(self.by_substitution.items())),
        }


class ExerciseAlternativesBuilder:
    """Вычисляет альтернативы по признакам каталога и требованиям оборудования."""

    def __init__(self, index: EquipmentIndex) -> None:
        self._index = index

    def build(
        self,
        exercises: list[Exercise],
        requirements: dict[tuple[str, str], list[ExerciseEquipmentRequirement]],
    ) -> tuple[list[ExerciseAlternative], AlternativesReport]:
        features = [self._features(e, requirements.get((e.external_id, e.source), []))
                    for e in exercises]
        report = AlternativesReport(exercises_total=len(features))

        # Группировка по основным мышцам: сравнивать «жим лёжа» с «подъёмом на
        # носки» бессмысленно, а полный перебор 873×873 — это 762 тысячи пар.
        by_muscle: dict[str, list[ExerciseFeatures]] = {}
        for item in features:
            for muscle in item.primary_muscles:
                by_muscle.setdefault(muscle, []).append(item)

        result: list[ExerciseAlternative] = []
        for item in features:
            if not item.primary_muscles:
                # Без основных мышц сравнивать нечего: любое совпадение было бы
                # случайным.
                continue
            candidates: dict[tuple[str, str], ExerciseFeatures] = {}
            for muscle in item.primary_muscles:
                for candidate in by_muscle.get(muscle, ()):
                    if candidate.key == item.key:
                        continue
                    candidates[candidate.key] = candidate

            scored: list[tuple[float, SubstitutionType, ExerciseFeatures, dict]] = []
            for candidate in candidates.values():
                if candidate.primary_muscles != item.primary_muscles:
                    # Требование равенства, а не пересечения: упражнение на
                    # грудь и трицепс не заменяет упражнение только на грудь.
                    continue
                score, substitution, rationale = self._compare(item, candidate)
                if score < MIN_SCORE:
                    continue
                scored.append((score, substitution, candidate, rationale))

            scored.sort(
                key=lambda row: (-row[0], _SUBSTITUTION_ORDER[row[1]], row[2].external_id)
            )
            selected = scored[:MAX_ALTERNATIVES]
            if selected:
                report.exercises_with_alternatives += 1
            for score, substitution, candidate, rationale in selected:
                result.append(
                    ExerciseAlternative(
                        exercise_external_id=item.external_id,
                        exercise_source=item.source,
                        alternative_external_id=candidate.external_id,
                        alternative_source=candidate.source,
                        substitution=substitution,
                        score=round(score, 3),
                        rationale=rationale,
                        source=KnowledgeSource.DERIVED,
                    )
                )
                report.by_substitution[substitution.value] = (
                    report.by_substitution.get(substitution.value, 0) + 1
                )
        report.alternatives_total = len(result)
        return result, report

    # --- Признаки -------------------------------------------------------------

    def _features(
        self, exercise: Exercise, requirements: list[ExerciseEquipmentRequirement]
    ) -> ExerciseFeatures:
        equipment = {
            r.equipment_id
            for r in requirements
            if r.equipment_id
            and r.requirement
            in (EquipmentRequirement.REQUIRED, EquipmentRequirement.ALTERNATIVE)
        }
        categories: set[str] = set()
        stability: set[str] = set()
        for equipment_id in equipment:
            item = self._index.items.get(equipment_id)
            if item is None:
                continue
            categories.add(item.category)
            stability |= set(item.capabilities) & STABILITY_CAPABILITIES
        return ExerciseFeatures(
            external_id=exercise.external_id,
            source=exercise.source,
            primary_muscles=frozenset(exercise.primary_muscles),
            secondary_muscles=frozenset(exercise.secondary_muscles),
            force=exercise.force,
            mechanic=exercise.mechanic,
            difficulty=exercise.difficulty,
            exercise_type=exercise.exercise_type,
            equipment=frozenset(equipment),
            equipment_categories=frozenset(categories),
            stability=frozenset(stability),
            equipment_known=bool(requirements),
        )

    # --- Сравнение ------------------------------------------------------------

    @staticmethod
    def _compare(
        left: ExerciseFeatures, right: ExerciseFeatures
    ) -> tuple[float, SubstitutionType, dict]:
        same_pattern = left.force == right.force and left.mechanic == right.mechanic
        equipment_known = left.equipment_known and right.equipment_known
        same_stability = equipment_known and left.stability == right.stability
        same_equipment = equipment_known and left.equipment == right.equipment
        same_categories = (
            equipment_known and left.equipment_categories == right.equipment_categories
        )
        same_difficulty = left.difficulty == right.difficulty
        same_type = left.exercise_type == right.exercise_type

        secondary_overlap = _jaccard(left.secondary_muscles, right.secondary_muscles)

        score = 0.0
        if same_pattern:
            score += WEIGHT_PATTERN
        score += WEIGHT_SECONDARY * secondary_overlap
        if same_difficulty:
            score += WEIGHT_DIFFICULTY
        if same_type:
            score += WEIGHT_TYPE
        if same_stability:
            score += WEIGHT_STABILITY
        if same_categories:
            score += WEIGHT_EQUIPMENT_CLASS

        if not same_pattern:
            substitution = SubstitutionType.PARTIAL
        elif not equipment_known:
            # Требования одного из упражнений неизвестны: утверждать полную
            # эквивалентность нельзя, иначе пробел в данных превращается в факт.
            substitution = SubstitutionType.SIMILAR
        elif same_stability and same_equipment:
            # Равенство набора оборудования, а не категории: `barbell` и
            # `dumbbell` обе относятся к `free_weight`, и по категории жим
            # гантелей объявлялся бы полной заменой жима штанги.
            substitution = SubstitutionType.EXACT
        else:
            substitution = SubstitutionType.SIMILAR

        rationale = {
            "primary_muscles": sorted(left.primary_muscles),
            "same_pattern": same_pattern,
            "force": [left.force, right.force],
            "mechanic": [left.mechanic, right.mechanic],
            "equipment_known": equipment_known,
            "same_stability": same_stability,
            "same_equipment": same_equipment,
            "same_equipment_categories": same_categories,
            "same_difficulty": same_difficulty,
            "same_type": same_type,
            "secondary_overlap": round(secondary_overlap, 3),
            "equipment": sorted(right.equipment),
        }
        return score, substitution, rationale


_SUBSTITUTION_ORDER = {
    SubstitutionType.EXACT: 0,
    SubstitutionType.SIMILAR: 1,
    SubstitutionType.PARTIAL: 2,
}


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)
