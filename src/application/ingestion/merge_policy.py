"""Правила merge: что берётся из внешней записи в canonical упражнение.

Модуль отвечает на два вопроса и держит их раздельно.

**Первый: чем является внешняя запись** — `IngestionDecision`. Решение
принимается по совокупности найденного соответствия и качества записи, и порядок
проверок не случаен:

1. качество `REJECT` даёт `LOW_QUALITY` независимо от совпадения: запись без
   техники нельзя ни импортировать, ни использовать для обогащения, потому что
   обогащать нечем;
2. запись, помеченная сомнительной (`questionable`), даёт `QUESTIONABLE` — это
   не низкое качество, а требование проверки человеком;
3. подтверждённое тождество (`match.matched`) даёт `EXISTING`, а если внешняя
   запись содержит поля, которых у canonical нет, — `ENRICHABLE`;
4. тождество, не подтверждённое данными (совпадение опирается на неизвестность
   оборудования или мышцы с обеих сторон), даёт `UNKNOWN`: объединять нельзя,
   потому что это молча уничтожило бы одну запись, и создавать нельзя, потому что
   она вероятный дубль. Решение остаётся человеку;
5. совпадение движения при расхождении содержательных различителей даёт
   `NEW_RELEVANT` — это отдельное упражнение, и найденный близкий canonical
   остаётся в staging как кандидат на объединение. Требование этапа
   сформулировано прямо: если различие существенно для тренировочного назначения,
   это отдельное упражнение;
6. отсутствие соответствия при качестве `READY` даёт `NEW_RELEVANT`;
7. всё остальное — `UNKNOWN`.

`DUPLICATE_VARIANT` в этом решении не выдаётся сопоставлением: его выставляет
сервис ingestion для дублей **внутри одного источника** — записей, различающихся
только пометкой съёмки (`(male)`, `v. 2`). Такая пара действительно описывает
одно упражнение, и вторая запись caталог не пополняет.

**Второй: какие поля берутся** — `MergePlan`. Политика по полям задана таблицей
требования этапа и здесь выражена кодом:

- canonical идентификатор не заменяется никогда;
- название canonical сохраняется; внешнее название добавляется в `aliases`, если
  его там нет — это единственный способ не потерять его и не подменить canonical;
- техника берётся у внешнего источника, только если у canonical её нет либо
  внешняя содержит больше шагов; равный по объёму текст не заменяется, потому что
  «другой формулировкой» не значит «лучше»;
- русская техника — то же правило; для canonical каталога это главный выигрыш от
  источника A только там, где русской техники нет;
- мышцы дополняются, но не заменяются: canonical значение получено из источника,
  которому доверяет генератор, а внешнее приведено словарём и может быть
  выведенным (`BROADER`);
- оборудование не переписывается в поле `exercises.equipment`: оно остаётся
  входом действующего фильтра, а нормализованное знание пишется в
  `exercise_equipment_requirements` через существующую Equipment Intelligence;
- media добавляется, если её нет; существующая не удаляется;
- provenance записывается всегда — и для добавленного поля, и для нового
  упражнения.

Слепой overwrite не делается ни для одного поля. Это не осторожность: canonical
каталог билингвален и вычитан, а внешний источник содержит и mojibake, и
инструкции, переведённые машинно. Заменять проверенное непроверенным на основании
того, что оно новее, значило бы ухудшать базу, объявляя это обогащением.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.application.ingestion.candidates import ExternalExerciseCandidate
from src.application.ingestion.matching import (
    MATCH_CONFIDENCE_FLOOR,
    MATCH_THRESHOLD,
    RELATED_THRESHOLD,
    MatchResult,
)
from src.application.ingestion.quality import QualityAssessment
from src.domain.ingestion import IngestionDecision, QualityStatus

# Поля canonical упражнения, которые может заполнить внешний источник.
FIELD_TECHNIQUE = "technique"
FIELD_TECHNIQUE_RU = "technique_ru"
FIELD_DESCRIPTION = "description"
FIELD_ALIASES = "aliases"
FIELD_PRIMARY_MUSCLES = "primary_muscles"
FIELD_SECONDARY_MUSCLES = "secondary_muscles"
FIELD_MEDIA = "media"
FIELD_NAME = "name"
FIELD_NAME_RU = "name_ru"
FIELD_EQUIPMENT_REQUIREMENTS = "equipment_requirements"

REASON_FILLED_EMPTY = "filled_missing_value"
REASON_MORE_COMPLETE = "more_complete_than_canonical"
REASON_ORIGIN = "new_exercise_from_source"
REASON_ALIAS_ADDED = "external_name_kept_as_alias"


def _steps(text: str | None) -> int:
    if not text:
        return 0
    return len([line for line in text.split("\n") if line.strip()])


@dataclass
class MergePlan:
    """Какие поля canonical упражнения меняет внешняя запись."""

    fields: dict[str, object] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    # Медиа, которую нужно загрузить: пути внутри локальной копии источника.
    media: list = field(default_factory=list)

    @property
    def changes_anything(self) -> bool:
        return bool(self.fields or self.media)

    def add(self, name: str, value: object, reason: str) -> None:
        self.fields[name] = value
        self.reasons[name] = reason


def build_enrichment_plan(
    candidate: ExternalExerciseCandidate,
    quality: QualityAssessment,
    *,
    canonical_name: str,
    canonical_technique: str | None,
    canonical_technique_ru: str | None,
    canonical_description: str | None,
    canonical_aliases: list[str],
    canonical_primary: list[str],
    canonical_secondary: list[str],
    canonical_has_media: bool,
) -> MergePlan:
    """Строит план обогащения существующего canonical упражнения."""
    plan = MergePlan()

    if candidate.technique and _steps(candidate.technique) > _steps(canonical_technique):
        plan.add(
            FIELD_TECHNIQUE,
            candidate.technique,
            REASON_FILLED_EMPTY if not canonical_technique else REASON_MORE_COMPLETE,
        )

    if candidate.technique_ru and _steps(candidate.technique_ru) > _steps(
        canonical_technique_ru
    ):
        plan.add(
            FIELD_TECHNIQUE_RU,
            candidate.technique_ru,
            REASON_FILLED_EMPTY
            if not canonical_technique_ru
            else REASON_MORE_COMPLETE,
        )

    if candidate.description and not canonical_description:
        plan.add(FIELD_DESCRIPTION, candidate.description, REASON_FILLED_EMPTY)

    # Внешнее название сохраняется синонимом: без этого связь «искали по этому
    # названию — нашли это упражнение» существует только в staging, и поиск в
    # каталоге по названию источника ничего не находит.
    #
    # В план кладётся одно добавляемое название, а не готовый список синонимов.
    # Разница существенна: одно canonical упражнение обогащают несколько внешних
    # записей (например, `barbell upright row` и `barbell upright row v. 2`), и
    # готовый список, посчитанный от одного и того же исходного состояния,
    # затирал бы синоним, добавленный предыдущей записью. Список собирается при
    # применении, поверх фактического состояния записи.
    #
    # Название, совпадающее с canonical, синонимом не добавляется: синоним,
    # равный названию, ничего не добавляет к поиску, зато при повторном импорте
    # выглядел бы как изменение — то есть импорт перестал бы быть идемпотентным
    # по полям.
    known_names = {a.strip().lower() for a in canonical_aliases}
    known_names.add(canonical_name.strip().lower())
    if candidate.name and candidate.name.strip().lower() not in known_names:
        plan.add(FIELD_ALIASES, candidate.name, REASON_ALIAS_ADDED)

    new_primary = [m for m in quality.primary_muscles if m not in canonical_primary]
    if new_primary and not canonical_primary:
        # Мышцы дополняются только там, где canonical их не знает. Дополнять
        # непустой список внешними значениями нельзя: генератор относит
        # упражнение к роли по основным мышцам, и добавленная мышца изменила бы
        # роль упражнения на основании выведенного словарём значения.
        plan.add(FIELD_PRIMARY_MUSCLES, quality.primary_muscles, REASON_FILLED_EMPTY)

    new_secondary = [
        m
        for m in quality.secondary_muscles
        if m not in canonical_secondary and m not in canonical_primary
    ]
    if new_secondary and not canonical_secondary:
        plan.add(
            FIELD_SECONDARY_MUSCLES, quality.secondary_muscles, REASON_FILLED_EMPTY
        )

    if candidate.media and not canonical_has_media:
        plan.media = list(candidate.media)
        plan.reasons[FIELD_MEDIA] = REASON_FILLED_EMPTY

    return plan


def build_twin_enrichment_plan(
    candidate: ExternalExerciseCandidate,
    twin: ExternalExerciseCandidate,
    quality: QualityAssessment,
    twin_quality: QualityAssessment,
) -> MergePlan:
    """План обогащения упражнения, созданного «двойником» из того же источника.

    Существует ради сходимости за один прогон. Дубль внутри источника
    (`barbell wrist curl` и `barbell wrist curl v. 2`) не создаёт упражнение, но
    его данные не всегда беднее: у второй записи может быть более полная техника,
    другое название и больше сопоставленных мышц. Если их не взять сразу, они
    будут взяты на втором прогоне — и импорт перестанет быть идемпотентным по
    данным, хотя по числу упражнений останется.

    Сравнение идёт с записью-двойником, а не с canonical строкой: на момент
    построения плана canonical запись ещё не создана, и читать её нечем.
    """
    plan = MergePlan()

    if candidate.technique and _steps(candidate.technique) > _steps(twin.technique):
        plan.add(FIELD_TECHNIQUE, candidate.technique, REASON_MORE_COMPLETE)
    if candidate.technique_ru and _steps(candidate.technique_ru) > _steps(
        twin.technique_ru
    ):
        plan.add(FIELD_TECHNIQUE_RU, candidate.technique_ru, REASON_MORE_COMPLETE)
    if candidate.name.strip().lower() != twin.name.strip().lower():
        plan.add(FIELD_ALIASES, candidate.name, REASON_ALIAS_ADDED)
    # Мышцы дополняются только там, где двойник не дал ни одной: правило то же,
    # что для существующего упражнения, — заполнить пробел можно, заменить
    # заполненное нельзя.
    if quality.primary_muscles and not twin_quality.primary_muscles:
        plan.add(FIELD_PRIMARY_MUSCLES, quality.primary_muscles, REASON_FILLED_EMPTY)
    if quality.secondary_muscles and not twin_quality.secondary_muscles:
        plan.add(
            FIELD_SECONDARY_MUSCLES, quality.secondary_muscles, REASON_FILLED_EMPTY
        )
    return plan


def refine_plan_against_current(plan: MergePlan, exercise) -> MergePlan:
    """Убирает из плана поля, которые уже не являются улучшением.

    План строится от снимка каталога, а применяется последовательно, и одно
    canonical упражнение обогащают несколько внешних записей. Без повторной
    проверки порядок применения решал бы результат: запись с двумя шагами
    техники, применённая после записи с пятью, затирала бы более полную версию —
    и следующий прогон снова «улучшал» бы её, то есть импорт никогда не сходился
    бы.

    Проверка идёт против фактического состояния записи, поэтому результат не
    зависит ни от порядка записей в источнике, ни от числа прогонов.
    """
    refined = MergePlan()
    for field_name, value in plan.fields.items():
        reason = plan.reasons.get(field_name, "")
        if field_name == FIELD_TECHNIQUE:
            if _steps(str(value)) <= _steps(exercise.technique):
                continue
        elif field_name == FIELD_TECHNIQUE_RU:
            if _steps(str(value)) <= _steps(exercise.technique_ru):
                continue
        elif field_name == FIELD_DESCRIPTION:
            if exercise.description:
                continue
        elif field_name == FIELD_ALIASES:
            known = {a.strip().lower() for a in exercise.aliases}
            known.add(exercise.name.strip().lower())
            if str(value).strip().lower() in known:
                continue
        elif field_name == FIELD_PRIMARY_MUSCLES:
            if exercise.primary_muscles:
                continue
        elif field_name == FIELD_SECONDARY_MUSCLES:
            if exercise.secondary_muscles:
                continue
        refined.add(field_name, value, reason)
    refined.media = plan.media
    if FIELD_MEDIA in plan.reasons:
        refined.reasons[FIELD_MEDIA] = plan.reasons[FIELD_MEDIA]
    return refined


def decide(
    candidate: ExternalExerciseCandidate,
    match: MatchResult,
    quality: QualityAssessment,
    *,
    enrichment: MergePlan | None,
) -> tuple[IngestionDecision, str]:
    """Определяет судьбу внешней записи и причину решения."""
    if quality.status is QualityStatus.REJECT:
        return (
            IngestionDecision.LOW_QUALITY,
            "качество ниже порога: " + ", ".join(quality.reasons[:4]),
        )

    if quality.questionable:
        return (
            IngestionDecision.QUESTIONABLE,
            "запись требует проверки человеком: " + ", ".join(quality.reasons[:4]),
        )

    if match.matched:
        if enrichment is not None and enrichment.changes_anything:
            changed = sorted(enrichment.fields)
            if enrichment.media:
                changed.append("media")
            return (
                IngestionDecision.ENRICHABLE,
                "упражнение существует, внешние данные полнее по полям: "
                + ", ".join(changed),
            )
        return (
            IngestionDecision.EXISTING,
            "упражнение существует, внешние данные не добавляют полей",
        )

    if match.identical and match.external_id is not None:
        # Тождество по признакам есть, но подтверждено слабо: совпадение
        # опирается на неизвестность оборудования или мышцы с обеих сторон.
        # Автоматически объединять нельзя — это молча уничтожило бы одну из
        # записей, — и создавать вторую тоже нельзя: она вероятный дубль.
        return (
            IngestionDecision.UNKNOWN,
            f"вероятное совпадение с {match.external_id} "
            f"(уверенность {match.confidence:.2f}) не подтверждено данными: "
            "решение за человеком",
        )

    if quality.status is QualityStatus.READY:
        if match.variant_of is not None:
            # Совпало движение, но различаются признаки выполнения — хват, угол,
            # стойка, амплитуда. Требование этапа на этот счёт однозначно: если
            # различие существенно для тренировочного назначения, это отдельное
            # упражнение. Наклонный жим узким хватом не является записью того же
            # упражнения, что жим узким хватом лёжа, и объявлять его дублем
            # значило бы потерять упражнение, назвав потерю дедупликацией.
            return (
                IngestionDecision.NEW_RELEVANT,
                f"отдельное упражнение: движение совпадает с {match.variant_of}, "
                "но различаются признаки выполнения "
                f"(уверенность {match.confidence:.2f})",
            )
        return (
            IngestionDecision.NEW_RELEVANT,
            "соответствия в каталоге нет, запись удовлетворяет минимальным "
            "требованиям",
        )

    return (
        IngestionDecision.UNKNOWN,
        "данных недостаточно для уверенного решения: "
        f"качество {quality.status.value}, уверенность сопоставления "
        f"{match.confidence:.2f}",
    )


# Пороговые значения выносятся в отчёт: без них числа решения непроверяемы.
DECISION_THRESHOLDS = {
    "match_confidence_floor": MATCH_CONFIDENCE_FLOOR,
    "related_threshold": RELATED_THRESHOLD,
    "match_threshold_reported": MATCH_THRESHOLD,
}
