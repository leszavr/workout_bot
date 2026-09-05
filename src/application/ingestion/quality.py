"""Оценка качества внешней записи и решение о её судьбе.

Оценка нужна не ради числа, а ради ответа на конкретный вопрос: можно ли
добавить эту запись в canonical каталог без участия человека. Поэтому итог —
не только `quality_score`, но и `quality_status` с перечислением причин: число
без причин нельзя ни проверить, ни исправить.

Обязательный минимум зафиксирован требованием этапа прямо: запись без описания и
техники полноценным кандидатом не является. Это выражено не весом, а условием:
отсутствие техники даёт `REJECT` независимо от того, насколько хороши остальные
поля. Иначе запись с полным набором мышц и media, но без техники, набрала бы
проходной балл и попала бы в каталог — то есть в программу пользователя попало бы
упражнение без объяснения, как его выполнять.

Веса распределены по практической ценности поля для программы:

- техника (обязательна) — без неё упражнение нельзя показать пользователю;
- русская техника — программа отдаётся на русском, и её отсутствие означает, что
  упражнение придётся переводить вручную;
- оборудование, приведённое к canonical словарю — без него упражнение не
  участвует в проверке совместимости и будет отфильтровано у любого пользователя;
- целевая мышца — без неё генератор не может отнести упражнение к роли движения;
- дополнительные мышцы, media, описание — уточняют, но не блокируют.

Порог `READY` = 0.70 подобран так, чтобы запись с техникой на двух языках,
известным оборудованием и целевой мышцей проходила, а запись только с техникой —
нет. Порог `REJECT` = 0.40 отделяет записи, у которых кроме названия и техники
почти ничего нет.

`QUESTIONABLE` — отдельное решение, а не низкое качество. Оно ставится, когда
данные внутренне противоречивы или подозрительны: mojibake в названии, техника
короче двух шагов, оборудование, которое источник называет родовым словом при
названии, указывающем на конкретный снаряд. Такая запись может быть верной, но
проверять её должен человек.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.application.ingestion.candidates import ExternalExerciseCandidate
from src.application.ingestion.muscles import map_muscles
from src.domain.ingestion import QualityStatus

# --- Пороги --------------------------------------------------------------------

READY_THRESHOLD = 0.70
REJECT_THRESHOLD = 0.40

# --- Веса ----------------------------------------------------------------------

WEIGHT_TECHNIQUE = 0.30
WEIGHT_TECHNIQUE_RU = 0.20
WEIGHT_EQUIPMENT = 0.18
WEIGHT_PRIMARY_MUSCLE = 0.14
WEIGHT_SECONDARY_MUSCLE = 0.06
WEIGHT_MEDIA = 0.08
WEIGHT_DESCRIPTION = 0.04

# Минимальное число шагов техники. Один шаг — это не техника, а фраза: источник
# с одним шагом встречается там, где инструкция потерялась при выгрузке.
MIN_TECHNIQUE_STEPS = 2

# Минимальная длина текста техники. Значение выбрано по фактическим данным
# источника: самая короткая полная инструкция там — около 120 символов.
MIN_TECHNIQUE_LENGTH = 60

REASON_NO_TECHNIQUE = "technique_missing"
REASON_TECHNIQUE_TOO_SHORT = "technique_too_short"
REASON_TECHNIQUE = "technique_present"
REASON_TECHNIQUE_RU = "technique_ru_present"
REASON_NO_TECHNIQUE_RU = "technique_ru_missing"
REASON_EQUIPMENT = "equipment_mapped"
REASON_EQUIPMENT_UNMAPPED = "equipment_unmapped"
REASON_EQUIPMENT_MISSING = "equipment_missing"
REASON_PRIMARY_MUSCLE = "target_muscle_mapped"
REASON_PRIMARY_MUSCLE_MISSING = "target_muscle_unmapped"
REASON_SECONDARY_MUSCLE = "secondary_muscles_mapped"
REASON_MEDIA = "media_present"
REASON_NO_MEDIA = "media_missing"
REASON_DESCRIPTION = "description_present"
REASON_MOJIBAKE = "name_encoding_broken"
REASON_NAME_TOO_SHORT = "name_too_short"
REASON_AMBIGUOUS_MUSCLE = "muscle_terms_ambiguous"

# Символы испорченной кодировки. Присутствие в названии не делает запись
# негодной, но требует проверки человеком: имя попадёт в программу.
MOJIBAKE_MARKERS = ("в°", "\ufffd", "Ð", "Ã")


@dataclass
class QualityAssessment:
    """Оценка одной внешней записи."""

    score: float = 0.0
    status: QualityStatus = QualityStatus.REVIEW
    reasons: list[str] = field(default_factory=list)
    questionable: bool = False
    # Оборудование, приведённое к canonical словарю: считается здесь, потому что
    # оценка всё равно должна его знать, а повторное приведение стоило бы второго
    # обхода словаря.
    equipment_ids: frozenset[str] = frozenset()
    unmapped_equipment: tuple[str, ...] = ()
    primary_muscles: tuple[str, ...] = ()
    secondary_muscles: tuple[str, ...] = ()
    ambiguous_muscles: tuple[str, ...] = ()
    unmapped_muscles: tuple[str, ...] = ()

    @property
    def has_technique(self) -> bool:
        return REASON_TECHNIQUE in self.reasons


def _technique_steps(technique: str | None) -> int:
    if not technique:
        return 0
    return len([line for line in technique.split("\n") if line.strip()])


class QualityScorer:
    """Считает качество внешней записи и приводит её оборудование к словарю.

    ``equipment_resolver`` возвращает пару «canonical ID» и «непонятые значения».
    Передаётся снаружи по той же причине, что и в сопоставлении: словарь
    оборудования живёт в базе знаний, и второй словарь здесь заводить нельзя.
    """

    def __init__(self, equipment_resolver) -> None:
        self._resolve_equipment = equipment_resolver

    def assess(self, candidate: ExternalExerciseCandidate) -> QualityAssessment:
        result = QualityAssessment()

        equipment_ids, unmapped = self._resolve_equipment(
            list(candidate.equipment_values)
        )
        result.equipment_ids = frozenset(equipment_ids)
        result.unmapped_equipment = tuple(unmapped)

        primary = map_muscles(list(candidate.primary_muscle_values))
        secondary = map_muscles(list(candidate.secondary_muscle_values))
        result.primary_muscles = tuple(primary.canonical)
        result.secondary_muscles = tuple(secondary.canonical)
        result.ambiguous_muscles = tuple(
            sorted(set(primary.ambiguous) | set(secondary.ambiguous))
        )
        result.unmapped_muscles = tuple(
            sorted(set(primary.unmapped) | set(secondary.unmapped))
        )

        score = 0.0

        steps = _technique_steps(candidate.technique)
        technique_length = len(candidate.technique or "")
        if steps == 0:
            result.reasons.append(REASON_NO_TECHNIQUE)
        elif steps < MIN_TECHNIQUE_STEPS or technique_length < MIN_TECHNIQUE_LENGTH:
            # Техника есть, но выглядит обрезанной: балл начисляется частично, а
            # запись помечается требующей проверки.
            score += WEIGHT_TECHNIQUE / 2
            result.reasons.append(REASON_TECHNIQUE_TOO_SHORT)
            result.questionable = True
        else:
            score += WEIGHT_TECHNIQUE
            result.reasons.append(REASON_TECHNIQUE)

        if candidate.technique_ru and _technique_steps(candidate.technique_ru) >= 1:
            score += WEIGHT_TECHNIQUE_RU
            result.reasons.append(REASON_TECHNIQUE_RU)
        else:
            result.reasons.append(REASON_NO_TECHNIQUE_RU)

        if result.equipment_ids:
            score += WEIGHT_EQUIPMENT
            result.reasons.append(REASON_EQUIPMENT)
        elif candidate.equipment_values:
            result.reasons.append(REASON_EQUIPMENT_UNMAPPED)
        else:
            result.reasons.append(REASON_EQUIPMENT_MISSING)

        if result.primary_muscles:
            score += WEIGHT_PRIMARY_MUSCLE
            result.reasons.append(REASON_PRIMARY_MUSCLE)
        else:
            result.reasons.append(REASON_PRIMARY_MUSCLE_MISSING)

        if result.secondary_muscles:
            score += WEIGHT_SECONDARY_MUSCLE
            result.reasons.append(REASON_SECONDARY_MUSCLE)

        if candidate.media:
            score += WEIGHT_MEDIA
            result.reasons.append(REASON_MEDIA)
        else:
            result.reasons.append(REASON_NO_MEDIA)

        if candidate.description:
            score += WEIGHT_DESCRIPTION
            result.reasons.append(REASON_DESCRIPTION)

        if any(marker in candidate.raw_name for marker in MOJIBAKE_MARKERS):
            result.questionable = True
            result.reasons.append(REASON_MOJIBAKE)

        if len(candidate.name.strip()) < 4:
            result.questionable = True
            result.reasons.append(REASON_NAME_TOO_SHORT)

        if result.ambiguous_muscles:
            result.reasons.append(REASON_AMBIGUOUS_MUSCLE)

        result.score = round(min(score, 1.0), 4)

        # Отсутствие техники — не низкий балл, а отказ: без техники упражнение
        # нельзя показать пользователю, сколько бы полей ни было заполнено.
        if steps == 0:
            result.status = QualityStatus.REJECT
        elif result.score >= READY_THRESHOLD and not result.questionable:
            result.status = QualityStatus.READY
        elif result.score < REJECT_THRESHOLD:
            result.status = QualityStatus.REJECT
        else:
            result.status = QualityStatus.REVIEW
        return result
