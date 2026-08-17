"""Safety Framework: нормализация ограничений профиля и правила отбора.

Архитектура:

    Restriction (свободный текст профиля)
        ↓ Normalization (ключевые слова → MovementRestriction)
    Safety Rule (централизованный реестр)
        ↓ Exercise Characteristics (rule mapping из полей каталога)
    Decision: ALLOW / EXCLUDE / WARNING / REQUIRES_REVIEW

Принципы:
- Правила — ТЕХНИЧЕСКИЕ правила отбора движений, а не медицинская диагностика.
  Система не утверждает «упражнение безопасно при заболевании X»; она лишь
  исключает движения того типа, которых профиль просит избегать.
- Никакой логики вида ``if "грыжа" in text`` в качестве основного механизма:
  свободный текст сначала нормализуется в MovementRestriction, и только
  нормализованные ограничения активируют правила.
- При недостатке данных правило возвращает WARNING или REQUIRES_REVIEW,
  а не необоснованный EXCLUDE.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from src.domain.enums import MovementRestriction, SafetyDecision
from src.domain.exercise import Exercise
from src.domain.pools import ExclusionRecord, SafeExercisePool
from src.domain.profile import FitnessProfile
from src.domain.safety import ExerciseCharacteristics

# --- Нормализация свободного текста ограничений -------------------------------

# Ключевые слова (RU/EN, нижний регистр) → нормализованные ограничения.
# Одно слово может активировать несколько ограничений.
RESTRICTION_KEYWORDS: dict[str, tuple[MovementRestriction, ...]] = {
    # Ударные / прыжковые движения
    "прыжк": (MovementRestriction.AVOID_HIGH_IMPACT,),
    "jump": (MovementRestriction.AVOID_HIGH_IMPACT,),
    "ударн": (MovementRestriction.AVOID_HIGH_IMPACT,),
    "impact": (MovementRestriction.AVOID_HIGH_IMPACT,),
    "бег": (MovementRestriction.AVOID_HIGH_IMPACT, MovementRestriction.AVOID_HIGH_INTENSITY_CARDIO),
    # Осевая нагрузка на позвоночник
    "поясниц": (MovementRestriction.AVOID_HEAVY_SPINAL_LOADING,),
    "спин": (MovementRestriction.AVOID_HEAVY_SPINAL_LOADING,),
    "позвоноч": (MovementRestriction.AVOID_HEAVY_SPINAL_LOADING,),
    "spine": (MovementRestriction.AVOID_HEAVY_SPINAL_LOADING,),
    "back": (MovementRestriction.AVOID_HEAVY_SPINAL_LOADING,),
    "грыж": (
        MovementRestriction.AVOID_HEAVY_SPINAL_LOADING,
        MovementRestriction.AVOID_HIGH_INTRA_ABDOMINAL_PRESSURE,
    ),
    "протрузи": (MovementRestriction.AVOID_HEAVY_SPINAL_LOADING,),
    # Нагрузка над головой
    "плеч": (MovementRestriction.AVOID_OVERHEAD_LOADING,),
    "shoulder": (MovementRestriction.AVOID_OVERHEAD_LOADING,),
    "над головой": (MovementRestriction.AVOID_OVERHEAD_LOADING,),
    "overhead": (MovementRestriction.AVOID_OVERHEAD_LOADING,),
    # Глубокое сгибание колена + ударная нагрузка (колени страдают и от прыжков)
    "колен": (
        MovementRestriction.AVOID_DEEP_KNEE_FLEXION,
        MovementRestriction.AVOID_HIGH_IMPACT,
    ),
    "knee": (
        MovementRestriction.AVOID_DEEP_KNEE_FLEXION,
        MovementRestriction.AVOID_HIGH_IMPACT,
    ),
    "присед": (MovementRestriction.AVOID_DEEP_KNEE_FLEXION,),
    # Внутрибрюшное давление / интенсивность
    "давлен": (
        MovementRestriction.AVOID_HIGH_INTRA_ABDOMINAL_PRESSURE,
        MovementRestriction.AVOID_HIGH_INTENSITY_CARDIO,
    ),
    "гипертон": (
        MovementRestriction.AVOID_HIGH_INTRA_ABDOMINAL_PRESSURE,
        MovementRestriction.AVOID_HIGH_INTENSITY_CARDIO,
    ),
    "брюшн": (MovementRestriction.AVOID_HIGH_INTRA_ABDOMINAL_PRESSURE,),
    "pressure": (MovementRestriction.AVOID_HIGH_INTRA_ABDOMINAL_PRESSURE,),
    "серд": (MovementRestriction.AVOID_HIGH_INTENSITY_CARDIO,),
    "интенсив": (MovementRestriction.AVOID_HIGH_INTENSITY_CARDIO,),
}

# Слова-«пустышки», которые пользователь пишет вместо реального ограничения.
NOISE_WORDS = frozenset({"нет", "none", "-", "—", "не знаю", "нет ограничений"})


def normalize_restrictions(profile: FitnessProfile) -> tuple[set[MovementRestriction], list[str]]:
    """Переводит свободный текст ограничений профиля в MovementRestriction.

    Возвращает (набор ограничений, замечания для ручного рассмотрения).
    """
    health = profile.health_and_limitations
    if not health.has_limitations:
        return set(), []

    texts: list[str] = []
    texts.extend(health.categories)
    texts.extend(health.movements_to_avoid)
    if health.doctor_recommendations:
        texts.append(health.doctor_recommendations)

    restrictions: set[MovementRestriction] = set()
    recognized_any = False
    meaningful_text_seen = False
    for text in texts:
        lowered = text.strip().lower()
        if not lowered or lowered in NOISE_WORDS:
            continue
        meaningful_text_seen = True
        matched = False
        for keyword, mapped in RESTRICTION_KEYWORDS.items():
            if keyword in lowered:
                restrictions.update(mapped)
                matched = True
        recognized_any = recognized_any or matched

    notes: list[str] = []
    if meaningful_text_seen and not restrictions and not recognized_any:
        notes.append(
            "Пользователь указал ограничения, но они не распознаны нормализатором — "
            "требуется ручное рассмотрение профиля перед выдачей программы."
        )
    if health.medical_clearance_required:
        notes.append(
            "Пользователь указал рекомендации врача — программу рекомендуется "
            "проверить вручную (REQUIRES_REVIEW)."
        )
    return restrictions, notes


# --- Характеристики упражнений (rule mapping) ---------------------------------

_HIGH_IMPACT_NAME = re.compile(r"\b(jump|hop|sprint|burpee|skip)\b", re.IGNORECASE)
_SPINAL_LOAD_NAME = re.compile(
    r"\b(squat|deadlift|good morning|clean|snatch|jerk)\b", re.IGNORECASE
)
_OVERHEAD_NAME = re.compile(
    r"\b(overhead|shoulder press|military press|push press|jerk|snatch|handstand)\b",
    re.IGNORECASE,
)
_DEEP_KNEE_NAME = re.compile(r"\b(squat|lunge|leg press)\b", re.IGNORECASE)
_HIGH_INTENSITY_CARDIO_NAME = re.compile(
    r"\b(sprint|interval|hiit|running|run)\b", re.IGNORECASE
)
_LOW_INTENSITY_CARDIO_NAME = re.compile(r"\b(walking|walk)\b", re.IGNORECASE)


def derive_characteristics(exercise: Exercise) -> ExerciseCharacteristics:
    """Детерминированно выводит характеристики упражнения из полей каталога."""
    name = exercise.name
    equipment = set(exercise.equipment)
    barbell_like = bool(equipment & {"barbell", "e-z curl bar"})

    is_plyo = exercise.exercise_type == "plyometrics"
    is_cardio = exercise.exercise_type == "cardio"
    is_compound = exercise.mechanic == "compound"

    high_impact = is_plyo or bool(_HIGH_IMPACT_NAME.search(name))
    spinal_load = barbell_like and bool(_SPINAL_LOAD_NAME.search(name))
    overhead = bool(_OVERHEAD_NAME.search(name))
    deep_knee = bool(_DEEP_KNEE_NAME.search(name))
    intra_abdominal = barbell_like and bool(_SPINAL_LOAD_NAME.search(name)) and is_compound
    high_intensity_cardio = (
        is_plyo
        or (
            is_cardio
            and bool(_HIGH_INTENSITY_CARDIO_NAME.search(name))
            and not _LOW_INTENSITY_CARDIO_NAME.search(name)
        )
    )

    # Упражнения с неподтверждённой классификацией (other) считаются
    # неопределёнными: правила понижают EXCLUDE до REQUIRES_REVIEW.
    uncertain = exercise.exercise_type in (None, "other") or equipment == {"other"}

    return ExerciseCharacteristics(
        is_high_impact=high_impact,
        has_spinal_load=spinal_load,
        has_overhead_component=overhead,
        has_deep_knee_flexion=deep_knee,
        raises_intra_abdominal_pressure=intra_abdominal,
        is_high_intensity_cardio=high_intensity_cardio,
        is_compound=is_compound,
        characteristics_uncertain=uncertain,
    )


# --- Реестр safety-правил ------------------------------------------------------


@dataclass(frozen=True)
class SafetyRule:
    """Техническое правило: ограничение → проверка характеристик → решение."""

    rule_id: str
    restriction: MovementRestriction
    description: str
    evaluate: Callable[[ExerciseCharacteristics], SafetyDecision | None]


def _rule_avoid_high_impact(chars: ExerciseCharacteristics) -> SafetyDecision | None:
    if chars.is_high_impact:
        return SafetyDecision.EXCLUDE if not chars.characteristics_uncertain else SafetyDecision.REQUIRES_REVIEW
    return None


def _rule_avoid_spinal_loading(chars: ExerciseCharacteristics) -> SafetyDecision | None:
    if chars.has_spinal_load:
        return SafetyDecision.EXCLUDE
    if chars.raises_intra_abdominal_pressure:
        return SafetyDecision.WARNING
    return None


def _rule_avoid_overhead(chars: ExerciseCharacteristics) -> SafetyDecision | None:
    if chars.has_overhead_component:
        return SafetyDecision.EXCLUDE
    return None


def _rule_avoid_deep_knee(chars: ExerciseCharacteristics) -> SafetyDecision | None:
    if chars.has_deep_knee_flexion:
        return SafetyDecision.EXCLUDE
    return None


def _rule_avoid_intra_abdominal(chars: ExerciseCharacteristics) -> SafetyDecision | None:
    if chars.raises_intra_abdominal_pressure:
        return SafetyDecision.EXCLUDE
    if chars.has_spinal_load:
        return SafetyDecision.WARNING
    return None


def _rule_avoid_high_intensity_cardio(chars: ExerciseCharacteristics) -> SafetyDecision | None:
    if chars.is_high_intensity_cardio:
        return SafetyDecision.EXCLUDE
    if chars.is_high_impact:
        return SafetyDecision.WARNING
    return None


SAFETY_RULES: tuple[SafetyRule, ...] = (
    SafetyRule(
        rule_id="safety.avoid_high_impact",
        restriction=MovementRestriction.AVOID_HIGH_IMPACT,
        description="Исключить ударные/прыжковые движения",
        evaluate=_rule_avoid_high_impact,
    ),
    SafetyRule(
        rule_id="safety.avoid_heavy_spinal_loading",
        restriction=MovementRestriction.AVOID_HEAVY_SPINAL_LOADING,
        description="Исключить тяжёлую осевую нагрузку на позвоночник",
        evaluate=_rule_avoid_spinal_loading,
    ),
    SafetyRule(
        rule_id="safety.avoid_overhead_loading",
        restriction=MovementRestriction.AVOID_OVERHEAD_LOADING,
        description="Исключить нагрузку над головой",
        evaluate=_rule_avoid_overhead,
    ),
    SafetyRule(
        rule_id="safety.avoid_deep_knee_flexion",
        restriction=MovementRestriction.AVOID_DEEP_KNEE_FLEXION,
        description="Исключить глубокое сгибание коленного сустава",
        evaluate=_rule_avoid_deep_knee,
    ),
    SafetyRule(
        rule_id="safety.avoid_high_intra_abdominal_pressure",
        restriction=MovementRestriction.AVOID_HIGH_INTRA_ABDOMINAL_PRESSURE,
        description="Исключить движения с высоким внутрибрюшным давлением",
        evaluate=_rule_avoid_intra_abdominal,
    ),
    SafetyRule(
        rule_id="safety.avoid_high_intensity_cardio",
        restriction=MovementRestriction.AVOID_HIGH_INTENSITY_CARDIO,
        description="Исключить высокоинтенсивное кардио",
        evaluate=_rule_avoid_high_intensity_cardio,
    ),
)


# --- Safety Engine -------------------------------------------------------------


@dataclass(frozen=True)
class _ExerciseOutcome:
    """Результат оценки одного упражнения: решение + запись/предупреждения."""

    decision: SafetyDecision
    record: ExclusionRecord | None = None
    warnings: list[str] = field(default_factory=list)


class SafetyEngine:
    """Применяет нормализованные ограничения к кандидатному пулу."""

    def apply(
        self,
        profile: FitnessProfile,
        candidates: list[Exercise],
    ) -> SafeExercisePool:
        restrictions, review_notes = normalize_restrictions(profile)
        active_rules = [r for r in SAFETY_RULES if r.restriction in restrictions]

        allowed: list[Exercise] = []
        warnings: dict[str, list[str]] = {}
        excluded: list[ExclusionRecord] = []
        requires_review: list[ExclusionRecord] = []

        for exercise in candidates:
            outcome = self._evaluate_exercise(exercise, active_rules)
            if outcome.decision is SafetyDecision.EXCLUDE:
                excluded.append(outcome.record)
            elif outcome.decision is SafetyDecision.REQUIRES_REVIEW:
                requires_review.append(outcome.record)
            else:
                allowed.append(exercise)
                if outcome.warnings:
                    warnings[exercise.external_id] = outcome.warnings

        return SafeExercisePool(
            profile_id=profile.profile_id or "",
            allowed=allowed,
            warnings=warnings,
            excluded=excluded,
            requires_review=requires_review,
            review_notes=review_notes,
            applied_rules=[r.rule_id for r in active_rules],
            active_restrictions=sorted(restrictions, key=lambda r: r.value),
        )

    @staticmethod
    def _evaluate_exercise(
        exercise: Exercise, active_rules: list[SafetyRule]
    ) -> _ExerciseOutcome:
        """Оценивает одно упражнение против активных правил."""
        chars = derive_characteristics(exercise)
        warnings: list[str] = []
        for rule in active_rules:
            decision = rule.evaluate(chars)
            if decision is None:
                continue
            if decision is SafetyDecision.WARNING:
                warnings.append(f"{rule.rule_id}: {rule.description}")
                continue
            reason = (
                f"{rule.rule_id}: {rule.description}"
                if decision is SafetyDecision.EXCLUDE
                else f"{rule.rule_id}: классификация не подтверждена, нужно ручное рассмотрение"
            )
            return _ExerciseOutcome(
                decision=decision,
                record=ExclusionRecord(
                    exercise_external_id=exercise.external_id,
                    exercise_name=exercise.name,
                    reason=reason,
                ),
            )
        return _ExerciseOutcome(decision=SafetyDecision.ALLOW, warnings=warnings)
