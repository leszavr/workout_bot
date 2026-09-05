"""Entity resolution: сопоставление внешней записи с canonical упражнением.

Модуль отвечает на вопрос «есть ли это упражнение у нас уже» и обязан различать
три исхода, а не два: то же упражнение, другой вариант того же движения, другое
упражнение. Без среднего исхода дедупликация либо теряет упражнения (объявляя
вариант дублем), либо плодит их (объявляя вариант новым).

Признаки названия разложены на три независимых набора, и это главное решение
модуля:

1. **ядро движения** (`core_tokens`) — что за движение, без различителей и без
   названий мышц. `Dumbbell Biceps Curl` и `Dumbbell Curl` дают одно ядро:
   мышца записана полем, и повторять её в названии нечем;
2. **содержательные различители** (`semantic_variant_tokens`) — хват, стойка,
   угол, односторонность, амплитуда. Их расхождение означает другое упражнение:
   `Bench Press` и `Decline Bench Press` — не одно и то же;
3. **снаряд** — сравнивается не по слову в названии, а по canonical ID словаря
   оборудования. `Bench Press - With Bands` и `Band Bench Press` называют
   одинаковый снаряд разными словами, и различие названия здесь ничего не
   значит; различие canonical оборудования — значит.

**Тождество решается предикатом, а не порогом.** Запись считается тем же
упражнением, когда совпало ядро, совпали содержательные различители и не
противоречат оборудование и целевая мышца. Порог здесь был бы неверным
инструментом: он позволил бы объявить тождеством запись, у которой различитель
разошёлся, но «набралось достаточно баллов» на совпадении мышц.

**Оценка уверенности (`confidence`) считается всегда** и служит двум задачам:
выбрать лучшего кандидата среди нескольких и показать администратору, насколько
близка запись, которую система решением не объединила. Веса и границы:

- содержательные различители: 0.40;
- оборудование: 0.30 при совпадении известных наборов, 0.25 если неизвестно
  обеим сторонам, 0.15 если известно одной, 0 при расхождении;
- целевая мышца: 0.22 при совпадении, пропорционально при частичном, 0.10 если
  известна одной стороне, 0 при расхождении;
- дополнительные мышцы: до 0.05;
- совпадение `force` + `mechanic`: 0.03.

`RELATED_THRESHOLD = 0.55` — граница, ниже которой близость не записывается
вовсе: связь с упражнением, у которого совпало только ядро, знанием не является.
`MATCH_CONFIDENCE_FLOOR = 0.55` — тождество, набравшее меньше, отправляется на
проверку человеком вместо автоматического слияния.

Почему нельзя обойтись похожестью названий. Требование этапа названо прямо:

    Barbell Bench Press
    Bench Press - Barbell
    Barbell Bench Press - Medium Grip

Первые два — одно упражнение: ключ сопоставления снимает порядок слов, ядро и
различители совпадают. Третье — другое: у него есть `medium grip`, которого нет у
первых двух, и это различие тренировочное, а не орфографическое. Такая запись
получает решение «отдельное упражнение», а найденный близкий canonical остаётся в
staging как кандидат на объединение с уверенностью и причинами — то есть решение
человека возможно, но система его не выдумывает.

Обратный случай тоже назван прямо:

    Dumbbell Biceps Curl
    DB Biceps Curl
    Dumbbell Curl

Все три дают одно ядро (`curl`), один снаряд (`dumbbell`) и пустой набор
содержательных различителей: `DB` раскрывается в `dumbbell`, `biceps` — название
мышцы. Это одно упражнение.

AI в сопоставлении не участвует. Это не осторожность, а требование
воспроизводимости: результат импорта обязан совпадать при повторном запуске на
тех же данных, а вызов модели этого не гарантирует.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.application.ingestion.candidates import ExternalExerciseCandidate
from src.application.ingestion.muscles import map_muscles
from src.application.ingestion.normalization import (
    core_tokens,
    latin_name_key,
    name_key,
    naming_equipment_phrases,
    semantic_variant_tokens,
)
from src.domain.exercise import Exercise

# --- Пороги и веса -------------------------------------------------------------

# Ниже этого значения близость не записывается: связь с упражнением, у которого
# совпало только ядро, знанием не является.
RELATED_THRESHOLD = 0.55

# Тождество, набравшее меньше этого значения, не применяется автоматически:
# совпадение, опирающееся только на неизвестность с обеих сторон, должен
# подтвердить человек.
MATCH_CONFIDENCE_FLOOR = 0.55

# Сохранено как публичная граница отчёта: уверенность выше означает, что
# совпадение подтверждено не только названием, но и оборудованием с мышцей.
MATCH_THRESHOLD = 0.86

WEIGHT_VARIANTS = 0.40
WEIGHT_EQUIPMENT_MATCH = 0.30
WEIGHT_EQUIPMENT_BOTH_UNKNOWN = 0.25
WEIGHT_EQUIPMENT_ONE_UNKNOWN = 0.15
WEIGHT_PRIMARY_MUSCLE = 0.22
WEIGHT_PRIMARY_ONE_UNKNOWN = 0.10
WEIGHT_SECONDARY_MUSCLE = 0.05
WEIGHT_MECHANICS = 0.03

REASON_SOURCE_LINK = "existing_source_link"
REASON_EXACT_KEY = "normalized_name_match"
REASON_ALIAS = "alias_match"
REASON_TRANSLITERATION = "transliteration_match"
REASON_CORE = "movement_core_match"
REASON_VARIANTS = "variant_tokens_match"
REASON_VARIANT_MISMATCH = "variant_tokens_differ"
REASON_EQUIPMENT = "equipment_match"
REASON_EQUIPMENT_MISMATCH = "equipment_differs"
REASON_EQUIPMENT_UNKNOWN = "equipment_unknown"
REASON_PRIMARY = "target_match"
REASON_PRIMARY_SECONDARY = "target_in_secondary_muscles"
REASON_PRIMARY_MISMATCH = "target_differs"
REASON_PRIMARY_UNKNOWN = "target_unknown"
REASON_SECONDARY = "secondary_muscles_match"
REASON_MECHANICS = "force_mechanic_match"


@dataclass(frozen=True)
class CanonicalFeatures:
    """Признаки canonical упражнения, участвующие в сопоставлении.

    Считаются один раз на весь каталог: сопоставление обходит 873 упражнения на
    каждую из внешних записей, и пересчёт токенов внутри цикла превратил бы
    линейную работу в квадратичную по строкам.
    """

    external_id: str
    source: str
    name: str
    keys: frozenset[str]
    core: frozenset[str]
    variants: frozenset[str]
    equipment_ids: frozenset[str]
    primary_muscles: frozenset[str]
    secondary_muscles: frozenset[str]
    force: str | None
    mechanic: str | None
    has_technique: bool
    has_technique_ru: bool
    has_description: bool


@dataclass(frozen=True)
class EquipmentContext:
    """Что известно об оборудовании записи и откуда.

    ``declared`` — canonical ID, полученные из поля `equipment`. ``implied`` —
    ID, которые следуют только из названия. ``extra`` — оборудование, названное
    **в дополнение** к объявленному.

    Различие между `implied` и `extra` — не оттенок. `Smith Machine Bench Press`
    объявляет полем родовое `machine`, а названием — `smith machine`: это одно и
    то же оборудование, названное точнее, и различителем упражнения оно не
    является. `Cable Incline Fly (on stability ball)` объявляет блок, а названием
    добавляет фитбол: это второе оборудование, и оно упражнение различает.
    Отличить один случай от другого можно только через отношение
    «частное — родовое» словаря оборудования, поэтому оно передаётся сюда
    предикатом.
    """

    declared: frozenset[str] = frozenset()
    implied: frozenset[str] = frozenset()
    extra: frozenset[str] = frozenset()

    @property
    def effective(self) -> frozenset[str]:
        """Оборудование записи: объявленное, а при отсутствии — из названия.

        Запись без объявленного оборудования (строка датасета программ
        `Bench Press (Barbell)`) не является записью с неизвестным снарядом:
        название — единственное её утверждение о снаряде.
        """
        return self.declared or self.implied

    @property
    def declared_known(self) -> bool:
        return bool(self.declared)


def build_equipment_context(
    *,
    name: str,
    declared_values: list[str],
    resolve_values,
    resolve_phrases,
    related_equipment=None,
) -> EquipmentContext:
    """Разбирает оборудование записи по полю и по названию.

    ``resolve_phrases`` разрешает фразы названия через словарь оборудования, а не
    через список в коде: добавление снаряда остаётся строкой в
    `equipment_aliases`, как и предусмотрено прошлым этапом.

    ``related_equipment`` возвращает оборудование, связанное с данным отношением
    «частное — родовое» (включая само значение). Без него точнее названный
    снаряд считался бы вторым снарядом.
    """
    declared = frozenset(resolve_values(declared_values))
    implied = frozenset(resolve_phrases(naming_equipment_phrases(name)))
    if not declared:
        return EquipmentContext(declared=declared, implied=implied)

    related = related_equipment or (lambda value: {value})
    covered: set[str] = set()
    for value in declared:
        covered |= related(value)
    return EquipmentContext(
        declared=declared, implied=implied, extra=frozenset(implied - covered)
    )


def variant_signature(name: str, context: EquipmentContext) -> frozenset[str]:
    """Признаки, различающие упражнения: способ выполнения плюс лишний снаряд.

    Слова-снаряды в различители не входят: `lever` и `machine`, `band` и `bands` —
    разные слова для одного и того же, и различие названия не является различием
    упражнения. Вместо слова используется canonical ID, и только тот, который
    запись добавляет к объявленному оборудованию.
    """
    extra = frozenset(f"equipment:{value}" for value in context.extra)
    return semantic_variant_tokens(name) | extra


@dataclass
class MatchResult:
    """Найденное соответствие внешней записи canonical упражнению.

    ``identical`` — решение о тождестве, принятое предикатом: совпало движение,
    совпали содержательные различители, оборудование и целевая мышца не
    противоречат. ``confidence`` — насколько это решение подтверждено данными, а
    не только названием.

    Разделение обязательно. Запись, у которой совпало всё, кроме различителя
    (`medium grip`), может набрать высокую оценку по мышцам и оборудованию, но
    тождеством не является; запись, у которой совпало всё, а оборудование
    неизвестно обеим сторонам, тождеством является, но подтверждена слабо и
    требует проверки человеком.
    """

    external_id: str | None = None
    source: str | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    deterministic: bool = False
    identical: bool = False
    # Совпало движение, но различители разошлись: это связанное упражнение, а не
    # то же самое.
    variant_of: str | None = None

    @property
    def matched(self) -> bool:
        """Можно ли считать записи одним упражнением без участия человека."""
        return (
            self.external_id is not None
            and self.identical
            and self.confidence >= MATCH_CONFIDENCE_FLOOR
        )

    @property
    def related(self) -> bool:
        return self.external_id is not None and self.confidence >= RELATED_THRESHOLD


def build_canonical_features(
    exercises: list[Exercise], resolve_values, resolve_phrases, related_equipment=None
) -> list[CanonicalFeatures]:
    """Готовит признаки canonical каталога.

    ``resolve_values`` приводит формулировки поля `equipment` к canonical ID,
    ``resolve_phrases`` — фразы названия, ``related_equipment`` возвращает
    оборудование, связанное отношением «частное — родовое». Все три передаются
    снаружи, потому что словарь оборудования живёт в базе знаний, и сопоставление
    не должно ни знать про PostgreSQL, ни заводить второй словарь.
    """
    features: list[CanonicalFeatures] = []
    for exercise in exercises:
        keys = {name_key(exercise.name)}
        if exercise.name_ru:
            keys.add(latin_name_key(exercise.name_ru))
            keys.add(name_key(exercise.name_ru))
        for alias in exercise.aliases:
            keys.add(name_key(alias))
            keys.add(latin_name_key(alias))
        keys.discard("")

        context = build_equipment_context(
            name=exercise.name,
            declared_values=list(exercise.equipment),
            resolve_values=resolve_values,
            resolve_phrases=resolve_phrases,
            related_equipment=related_equipment,
        )
        features.append(
            CanonicalFeatures(
                external_id=exercise.external_id,
                source=exercise.source,
                name=exercise.name,
                keys=frozenset(keys),
                core=core_tokens(exercise.name),
                variants=variant_signature(exercise.name, context),
                equipment_ids=context.effective,
                primary_muscles=frozenset(exercise.primary_muscles),
                secondary_muscles=frozenset(exercise.secondary_muscles),
                force=exercise.force,
                mechanic=exercise.mechanic,
                has_technique=bool(exercise.technique),
                has_technique_ru=bool(exercise.technique_ru),
                has_description=bool(exercise.description),
            )
        )
    return features


def _overlap(left: frozenset[str], right: frozenset[str]) -> float:
    """Доля совпадения двух наборов (Jaccard).

    Пустые наборы считаются совпавшими: отсутствие различителей у обеих записей —
    это совпадение, а не отсутствие информации.
    """
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


class ExerciseMatcher:
    """Сопоставляет внешние записи с canonical каталогом.

    Два индекса строятся один раз: по ключу сопоставления и по токенам ядра
    движения. Без них каждая внешняя запись обходила бы каталог целиком, и на
    тысячах записей это миллионы сравнений вместо обращения к словарю.
    """

    def __init__(
        self,
        features: list[CanonicalFeatures],
        *,
        source_links: dict[tuple[str, str], tuple[str, str]] | None = None,
    ) -> None:
        self._features = features
        self._by_id: dict[str, CanonicalFeatures] = {}
        self._by_key: dict[str, list[CanonicalFeatures]] = {}
        self._by_core: dict[frozenset[str], list[CanonicalFeatures]] = {}
        for item in features:
            self._by_id[item.external_id] = item
            for key in item.keys:
                self._by_key.setdefault(key, []).append(item)
            if item.core:
                self._by_core.setdefault(item.core, []).append(item)
        # (source_key, source_record_id) -> (external_id, source)
        self._source_links = source_links or {}

    # --- Публичный интерфейс -------------------------------------------------

    def match(
        self,
        candidate: ExternalExerciseCandidate,
        *,
        equipment: EquipmentContext,
    ) -> MatchResult:
        """Ищет canonical упражнение для внешней записи."""
        link = self._source_links.get(
            (candidate.source_key, candidate.source_record_id)
        )
        if link is not None:
            # Связь, записанная предыдущим импортом, — факт, а не оценка. Это
            # единственный механизм, который делает повторный импорт
            # идемпотентным по определению, а не по совпадению правил.
            return MatchResult(
                external_id=link[0],
                source=link[1],
                confidence=1.0,
                reasons=[REASON_SOURCE_LINK],
                deterministic=True,
                identical=True,
            )

        primary_map = map_muscles(list(candidate.primary_muscle_values))
        secondary_map = map_muscles(list(candidate.secondary_muscle_values))
        candidate_primary = frozenset(primary_map.canonical)
        candidate_secondary = frozenset(secondary_map.canonical)
        candidate_variants = variant_signature(candidate.name, equipment)

        best = MatchResult()
        for item, key_reason in self._candidates(candidate):
            result = self._evaluate(
                candidate,
                item,
                key_reason=key_reason,
                candidate_variants=candidate_variants,
                candidate_equipment_ids=equipment.effective,
                candidate_primary=candidate_primary,
                candidate_secondary=candidate_secondary,
            )
            if result is None:
                continue
            if _better(result, best):
                best = result
        return best

    # --- Отбор кандидатов ----------------------------------------------------

    def _candidates(self, candidate: ExternalExerciseCandidate):
        """Canonical записи, которые вообще могут оказаться тем же упражнением.

        Ядро движения обязано совпасть полностью: оно уже очищено и от
        различителей, и от названий мышц, поэтому расхождение в нём означает
        другое движение. Отсюда и способ отбора — точное совпадение набора ядра,
        а не перебор по отдельным токенам.

        Дополнительно берутся записи, найденные по ключу названия: у них ядро
        совпадает по построению, но индекс ключей находит и совпадения через
        синонимы и транслитерацию русского названия.
        """
        found: dict[str, tuple[CanonicalFeatures, str | None]] = {}
        for key, reason in (
            (candidate.name_key, REASON_EXACT_KEY),
            (candidate.latin_key, REASON_TRANSLITERATION),
        ):
            if not key:
                continue
            for item in self._by_key.get(key, ()):
                existing = found.get(item.external_id)
                if existing is None or existing[1] is None:
                    found[item.external_id] = (item, reason)
        if candidate.core:
            for item in self._by_core.get(candidate.core, ()):
                found.setdefault(item.external_id, (item, None))
        return list(found.values())

    # --- Оценка одной пары ---------------------------------------------------

    def _evaluate(
        self,
        candidate: ExternalExerciseCandidate,
        item: CanonicalFeatures,
        *,
        key_reason: str | None,
        candidate_variants: frozenset[str],
        candidate_equipment_ids: frozenset[str],
        candidate_primary: frozenset[str],
        candidate_secondary: frozenset[str],
    ) -> MatchResult | None:
        core_matches = bool(candidate.core) and candidate.core == item.core
        key_matches = key_reason is not None
        if not core_matches and not key_matches:
            return None

        reasons: list[str] = []
        if key_matches:
            reasons.append(key_reason)  # type: ignore[arg-type]
            if key_reason == REASON_TRANSLITERATION:
                reasons.append(REASON_ALIAS)
        if core_matches:
            reasons.append(REASON_CORE)

        variants_match = candidate_variants == item.variants
        reasons.append(
            REASON_VARIANTS if variants_match else REASON_VARIANT_MISMATCH
        )
        confidence = WEIGHT_VARIANTS if variants_match else 0.0

        equipment_known_both = bool(candidate_equipment_ids) and bool(item.equipment_ids)
        equipment_match = True
        equipment_confirmed = False
        if equipment_known_both:
            if candidate_equipment_ids == item.equipment_ids:
                confidence += WEIGHT_EQUIPMENT_MATCH
                equipment_confirmed = True
                reasons.append(REASON_EQUIPMENT)
            else:
                equipment_match = False
                reasons.append(REASON_EQUIPMENT_MISMATCH)
        elif not candidate_equipment_ids and not item.equipment_ids:
            # Неизвестно обеим сторонам. Это не совпадение и не расхождение:
            # признак даёт меньше, чем подтверждённое совпадение, но решение о
            # тождестве не блокирует.
            confidence += WEIGHT_EQUIPMENT_BOTH_UNKNOWN
            reasons.append(REASON_EQUIPMENT_UNKNOWN)
        else:
            confidence += WEIGHT_EQUIPMENT_ONE_UNKNOWN
            reasons.append(REASON_EQUIPMENT_UNKNOWN)

        primary_match = True
        primary_confirmed = False
        if candidate_primary and item.primary_muscles:
            # Совпадение целевой мышцы проверяется не «primary против primary».
            # Источники расходятся в том, какую из задействованных мышц назвать
            # главной: для приседа внешний каталог называет целевой `glutes`, а
            # действующий — `quadriceps`, при этом `glutes` у него в
            # дополнительных, а `quadriceps` у внешнего — тоже в дополнительных.
            # Набор задействованных мышц совпадает, различается только выбор
            # главной, и трактовать это как разные упражнения значило бы
            # разводить присед с приседом.
            #
            # Поэтому «мышцы не противоречат» означает: целевая мышца одной
            # стороны есть среди задействованных мышц другой. Полное совпадение
            # главных мышц даёт полный вес, совпадение через дополнительные —
            # половину: это слабее, и цифра обязана это показывать.
            canonical_all = item.primary_muscles | item.secondary_muscles
            candidate_all = candidate_primary | candidate_secondary
            primary_overlap = _overlap(candidate_primary, item.primary_muscles)
            if primary_overlap > 0:
                confidence += WEIGHT_PRIMARY_MUSCLE * primary_overlap
                primary_confirmed = True
                reasons.append(REASON_PRIMARY)
            elif (candidate_primary & canonical_all) or (
                item.primary_muscles & candidate_all
            ):
                confidence += WEIGHT_PRIMARY_MUSCLE / 2
                primary_confirmed = True
                reasons.append(REASON_PRIMARY_SECONDARY)
            else:
                primary_match = False
                reasons.append(REASON_PRIMARY_MISMATCH)
        else:
            confidence += WEIGHT_PRIMARY_ONE_UNKNOWN
            reasons.append(REASON_PRIMARY_UNKNOWN)

        if candidate_secondary and item.secondary_muscles:
            secondary_overlap = _overlap(candidate_secondary, item.secondary_muscles)
            confidence += WEIGHT_SECONDARY_MUSCLE * secondary_overlap
            if secondary_overlap > 0.5:
                reasons.append(REASON_SECONDARY)

        # Тип усилия и механика есть только у canonical записи: источник их не
        # сообщает. Признак начисляется лишь тогда, когда обе стороны его знают, —
        # иначе это была бы премия за отсутствие данных.
        candidate_force = candidate.extra.get("force")
        candidate_mechanic = candidate.extra.get("mechanic")
        if (
            item.force is not None
            and item.mechanic is not None
            and candidate_force
            and candidate_mechanic
            and candidate_force == item.force
            and candidate_mechanic == item.mechanic
        ):
            confidence += WEIGHT_MECHANICS
            reasons.append(REASON_MECHANICS)

        identical = (
            (core_matches or key_matches)
            and variants_match
            and equipment_match
            # Расхождение целевой мышцы блокирует тождество только тогда, когда
            # совпадение опирается на ядро движения, а не на само название.
            # Полное совпадение нормализованного названия при совпадающих
            # различителях и оборудовании — прямое свидетельство тождества:
            # `Kettlebell Turkish Get-Up (Squat style)` называется одинаково в
            # обоих источниках, и то, что один назвал целевой мышцей плечи, а
            # другой ягодицы, — расхождение данных о мышцах, а не другое
            # упражнение. Такое расхождение видно в причинах и попадает в отчёт.
            #
            # При совпадении только по ядру правило обратное: ядро — сокращённая
            # подпись, и без согласия по мышцам оно объединило бы `Seated Dumbbell
            # Press` с `Seated Triceps Press`.
            and (primary_match or key_matches)
        )

        # Совпадение только по ядру требует подтверждения хотя бы одним фактом о
        # содержании. Ядро — короткая подпись: у `roller back stretch` и
        # `back pec stretch` оно одинаково (`back`, `stretch`), различителей нет
        # ни у одной, и без этого условия растяжка на валике объявлялась бы тем же
        # упражнением, что растяжка груди у стены. Подтверждением считается
        # совпадение оборудования либо мышц; неизвестность подтверждением не
        # является.
        if identical and not key_matches and not (
            equipment_confirmed or primary_confirmed
        ):
            identical = False

        return MatchResult(
            external_id=item.external_id,
            source=item.source,
            confidence=round(min(confidence, 1.0), 4),
            reasons=reasons,
            deterministic=key_matches and identical,
            identical=identical,
            variant_of=None if variants_match else item.external_id,
        )

    # --- Доступ к признакам --------------------------------------------------

    def features_of(self, external_id: str) -> CanonicalFeatures | None:
        return self._by_id.get(external_id)


def _better(left: MatchResult, right: MatchResult) -> bool:
    """Сравнение кандидатов: тождество сильнее любой оценки.

    Без этого правила запись, набравшая больше баллов на совпадении мышц, но
    различающаяся хватом, вытеснила бы точное совпадение.
    """
    if left.identical != right.identical:
        return left.identical
    return left.confidence > right.confidence
