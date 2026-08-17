"""Exercise Filtering Engine: детерминированный отбор упражнений под профиль.

Результат — ExerciseCandidatePool с объяснением каждого включения/исключения.

Правила фильтрации (все детерминированные):
1. Оборудование: упражнение требует только доступное оборудование.
   - зал без явно указанного списка → предполагается полный набор зала;
   - дом → парсинг свободного текста описания оборудования в теги каталога;
   - "body only" доступно всегда.
2. Уровень подготовки: отображение опыта профиля на допустимые difficulty.
3. Явные исключения пользователя (exercise_preferences.excluded_exercises).
4. CardioPreference.EXCLUDE → кардио исключается.

Цель пользователя НЕ исключает упражнения — она используется генератором
для ранжирования (см. generator.py).
"""
from __future__ import annotations

import asyncio

from src.domain.enums import (
    CardioPreference,
    ExperienceLevel,
    TrainingLocationType,
)
from src.domain.exercise import Exercise
from src.domain.pools import ExclusionRecord, ExerciseCandidatePool
from src.domain.profile import FitnessProfile

# Теги оборудования каталога (leszavr/workout).
EQ_BANDS = "bands"
EQ_BARBELL = "barbell"
EQ_BODY_ONLY = "body only"
EQ_CABLE = "cable"
EQ_DUMBBELL = "dumbbell"
EQ_EZ_CURL_BAR = "e-z curl bar"
EQ_EXERCISE_BALL = "exercise ball"
EQ_FOAM_ROLL = "foam roll"
EQ_KETTLEBELLS = "kettlebells"
EQ_MACHINE = "machine"
EQ_MEDICINE_BALL = "medicine ball"
EQ_OTHER = "other"

CATALOG_EQUIPMENT = frozenset(
    {
        EQ_BANDS, EQ_BARBELL, EQ_BODY_ONLY, EQ_CABLE, EQ_DUMBBELL,
        EQ_EZ_CURL_BAR, EQ_EXERCISE_BALL, EQ_FOAM_ROLL, EQ_KETTLEBELLS,
        EQ_MACHINE, EQ_MEDICINE_BALL, EQ_OTHER,
    }
)

# Полный набор оборудования типового тренажёрного зала.
GYM_EQUIPMENT = frozenset(CATALOG_EQUIPMENT)

# Дома всегда доступен минимум собственный вес.
HOME_BASELINE = frozenset({EQ_BODY_ONLY})

# Нормализация свободного текста (RU/EN) → теги оборудования каталога.
# Ключ — подстрока в нижнем регистре; значение — теги, которые она даёт.
EQUIPMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "гантел": (EQ_DUMBBELL,),
    "dumbbell": (EQ_DUMBBELL,),
    "штанг": (EQ_BARBELL,),
    "barbell": (EQ_BARBELL,),
    "гир": (EQ_KETTLEBELLS,),
    "kettlebell": (EQ_KETTLEBELLS,),
    "резин": (EQ_BANDS,),
    "петл": (EQ_BANDS,),
    "лент": (EQ_BANDS,),
    "эспандер": (EQ_BANDS,),
    "band": (EQ_BANDS,),
    "блок": (EQ_CABLE,),
    "cable": (EQ_CABLE,),
    "трос": (EQ_CABLE,),
    "тренажер": (EQ_MACHINE,),
    "тренажёр": (EQ_MACHINE,),
    "машин": (EQ_MACHINE,),
    "machine": (EQ_MACHINE,),
    "дорожк": (EQ_MACHINE,),
    "treadmill": (EQ_MACHINE,),
    "велотренажер": (EQ_MACHINE,),
    "велотренажёр": (EQ_MACHINE,),
    "мяч": (EQ_MEDICINE_BALL, EQ_EXERCISE_BALL),
    "ball": (EQ_MEDICINE_BALL, EQ_EXERCISE_BALL),
    "фитбол": (EQ_EXERCISE_BALL,),
    "ролл": (EQ_FOAM_ROLL,),
    "валик": (EQ_FOAM_ROLL,),
    "foam": (EQ_FOAM_ROLL,),
    "e-z": (EQ_EZ_CURL_BAR,),
    "изогнутый гриф": (EQ_EZ_CURL_BAR,),
    "турник": (EQ_BODY_ONLY,),
    "брусья": (EQ_BODY_ONLY,),
    "перекладин": (EQ_BODY_ONLY,),
    "pull-up": (EQ_BODY_ONLY,),
    "скамья": (EQ_MACHINE,),
    "bench": (EQ_MACHINE,),
}

# Отображение опыта профиля → допустимые difficulty каталога.
# Незаполненный опыт трактуется консервативно (beginner + intermediate).
EXPERIENCE_TO_DIFFICULTY: dict[ExperienceLevel | None, frozenset[str]] = {
    ExperienceLevel.NEVER: frozenset({"beginner"}),
    ExperienceLevel.LONG_BREAK: frozenset({"beginner"}),
    ExperienceLevel.UNDER_3_MONTHS: frozenset({"beginner", "intermediate"}),
    ExperienceLevel.THREE_TWELVE_MONTHS: frozenset({"beginner", "intermediate", "expert"}),
    ExperienceLevel.OVER_1_YEAR: frozenset({"beginner", "intermediate", "expert"}),
    None: frozenset({"beginner", "intermediate"}),
}


def normalize_equipment_text(text: str | None) -> set[str]:
    """Переводит свободный текст описания оборудования в теги каталога."""
    if not text:
        return set()
    lowered = text.lower()
    tags: set[str] = set()
    for alias, mapped in EQUIPMENT_ALIASES.items():
        if alias in lowered:
            tags.update(mapped)
    return tags


def resolve_available_equipment(profile: FitnessProfile) -> set[str]:
    """Определяет доступные теги оборудования по профилю."""
    location = profile.training_location
    available: set[str] = set(HOME_BASELINE)

    listed = normalize_equipment_text(", ".join(location.available_equipment))
    listed |= normalize_equipment_text(location.custom_equipment_description)

    if location.primary_location is TrainingLocationType.GYM:
        # Зал: список указан → доверяем ему; пуст → предполагаем полный зал.
        available |= listed if listed else set(GYM_EQUIPMENT)
    elif location.primary_location is TrainingLocationType.BOTH:
        available |= set(GYM_EQUIPMENT) if not listed else listed
    else:
        # Дом (или не указано): только явно перечисленное + body only.
        available |= listed
    return available


class ExerciseFilter:
    """Детерминированный отбор кандидатов из каталога под профиль."""

    async def select_candidates(
        self,
        profile: FitnessProfile,
        exercises: list[Exercise],
    ) -> ExerciseCandidatePool:
        # Интерфейс асинхронный (в будущем фильтр может выполнять I/O);
        # детерминированная реализация уступает управление event loop.
        await asyncio.sleep(0)
        available_equipment = resolve_available_equipment(profile)
        allowed_difficulty = EXPERIENCE_TO_DIFFICULTY.get(
            profile.training_background.experience_level
        )
        excluded_names = {
            name.strip().lower()
            for name in profile.exercise_preferences.excluded_exercises
            if name.strip()
        }
        cardio_excluded = (
            profile.lifestyle.cardio_preference is CardioPreference.EXCLUDE
        )

        included: list[Exercise] = []
        excluded: list[ExclusionRecord] = []

        for exercise in sorted(exercises, key=lambda e: e.name):
            if not exercise.is_active:
                excluded.append(self._record(exercise, "упражнение деактивировано"))
                continue
            if cardio_excluded and exercise.exercise_type == "cardio":
                excluded.append(
                    self._record(exercise, "кардио исключено по предпочтению пользователя")
                )
                continue
            reason = self._check_equipment(exercise, available_equipment)
            if reason:
                excluded.append(self._record(exercise, reason))
                continue
            reason = self._check_difficulty(exercise, allowed_difficulty)
            if reason:
                excluded.append(self._record(exercise, reason))
                continue
            if self._is_user_excluded(exercise, excluded_names):
                excluded.append(
                    self._record(exercise, "в списке исключений пользователя")
                )
                continue
            included.append(exercise)

        return ExerciseCandidatePool(
            profile_id=profile.profile_id or "",
            total_exercises=len(exercises),
            included=included,
            excluded=excluded,
        )

    @staticmethod
    def _record(exercise: Exercise, reason: str) -> ExclusionRecord:
        return ExclusionRecord(
            exercise_external_id=exercise.external_id,
            exercise_name=exercise.name,
            reason=reason,
        )

    @staticmethod
    def _check_equipment(exercise: Exercise, available: set[str]) -> str | None:
        required = set(exercise.equipment)
        if not required:
            return None
        if required & available:
            return None
        if required == {EQ_OTHER}:
            return "требуемое оборудование не подтверждено профилем (other)"
        missing = ", ".join(sorted(required - available))
        return f"нет оборудования: {missing}"

    @staticmethod
    def _check_difficulty(
        exercise: Exercise, allowed: frozenset[str] | None
    ) -> str | None:
        if exercise.difficulty is None or allowed is None:
            return None
        if exercise.difficulty in allowed:
            return None
        return f"сложность '{exercise.difficulty}' выше допустимой для уровня подготовки"

    @staticmethod
    def _is_user_excluded(exercise: Exercise, excluded_names: set[str]) -> bool:
        if not excluded_names:
            return False
        names = {exercise.name.lower()}
        if exercise.name_ru:
            names.add(exercise.name_ru.lower())
        names.update(alias.lower() for alias in exercise.aliases)
        return bool(names & excluded_names)
