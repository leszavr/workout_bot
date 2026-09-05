"""Приведение внешних обозначений мышц к canonical словарю каталога.

Canonical словарь мышц задан действующим каталогом (`exercises.primary_muscles`
и `secondary_muscles`) и состоит из 17 значений. Он не расширяется этим этапом:
добавление новой мышцы меняет поведение генератора, который группирует
упражнения по мышцам (`src/application/programs/generator.py`), и делать это
внутри импорта данных нельзя.

Отношение внешнего значения к canonical выражено явно, а не сведено к «нашлось /
не нашлось». Разница между четырьмя случаями существенна:

- ``EXACT`` — то же понятие другими словами: ``abs`` и ``abdominals``,
  ``pectorals`` и ``chest``. Такое значение подставляется как факт.
- ``BROADER`` — внешнее значение является частью canonical группы: ``obliques``
  входят в ``abdominals``, ``soleus`` — в ``calves``. Значение подставляется, но
  помечено как выведенное: это не тождество, а поглощение более общим термином.
- ``AMBIGUOUS`` — внешнее значение указывает на несколько canonical групп:
  ``upper back`` — это и ``middle back``, и ``traps``; ``core`` — и
  ``abdominals``, и ``lower back``. Автоматически такое значение не
  подставляется: выбор одного варианта был бы решением, которого никто не
  принимал.
- ``UNMAPPED`` — canonical эквивалента нет: ``serratus anterior``, ``feet``,
  ``cardiovascular system``. Значение сохраняется в отчёте как незакрытый пробел
  словаря, а не подменяется похожим.

Соблазн свести всё к EXACT есть: тогда мышцы заполнялись бы у всех записей. Но
``upper back → middle back`` — утверждение об анатомии, которое источник не
делал, и предъявлять его как факт нельзя. Требование этапа сформулировано прямо:
неоднозначные значения идут в review, а не в базу.
"""
from __future__ import annotations

from enum import StrEnum

# Canonical словарь мышц действующего каталога. Порядок не важен, состав —
# важен: любое значение вне этого набора в canonical запись не попадает.
CANONICAL_MUSCLES = frozenset(
    {
        "abdominals",
        "abductors",
        "adductors",
        "biceps",
        "calves",
        "chest",
        "forearms",
        "glutes",
        "hamstrings",
        "lats",
        "lower back",
        "middle back",
        "neck",
        "quadriceps",
        "shoulders",
        "traps",
        "triceps",
    }
)


class MuscleRelation(StrEnum):
    """Отношение внешнего обозначения мышцы к canonical словарю."""

    EXACT = "exact"
    BROADER = "broader"
    AMBIGUOUS = "ambiguous"
    UNMAPPED = "unmapped"


# Внешнее значение -> одно canonical значение. Тождество разными словами.
EXACT_MAP: dict[str, str] = {
    "abdominals": "abdominals",
    "abs": "abdominals",
    "abductors": "abductors",
    "adductors": "adductors",
    "inner thighs": "adductors",
    "biceps": "biceps",
    "bicep": "biceps",
    "calves": "calves",
    "calf": "calves",
    "chest": "chest",
    "pectorals": "chest",
    "pectoralis major": "chest",
    "pecs": "chest",
    "forearms": "forearms",
    "forearm": "forearms",
    "glutes": "glutes",
    "gluteus maximus": "glutes",
    "hamstrings": "hamstrings",
    "lats": "lats",
    "latissimus dorsi": "lats",
    "lower back": "lower back",
    "erector spinae": "lower back",
    "middle back": "middle back",
    "neck": "neck",
    "quadriceps": "quadriceps",
    "quads": "quadriceps",
    "shoulders": "shoulders",
    "delts": "shoulders",
    "deltoids": "shoulders",
    "traps": "traps",
    "trapezius": "traps",
    "triceps": "triceps",
    "tricep": "triceps",
}

# Внешнее значение -> canonical группа, в которую оно входит. Подставляется, но
# помечается как выведенное: это поглощение, а не тождество.
BROADER_MAP: dict[str, str] = {
    "obliques": "abdominals",
    "lower abs": "abdominals",
    "upper abs": "abdominals",
    "transverse abdominis": "abdominals",
    "rectus abdominis": "abdominals",
    "serratus": "abdominals",
    "rhomboids": "middle back",
    "teres major": "middle back",
    "upper chest": "chest",
    "lower chest": "chest",
    "rear deltoids": "shoulders",
    "front deltoids": "shoulders",
    "side deltoids": "shoulders",
    "rotator cuff": "shoulders",
    "brachialis": "biceps",
    "brachioradialis": "forearms",
    "wrist flexors": "forearms",
    "wrist extensors": "forearms",
    "wrists": "forearms",
    "hands": "forearms",
    "grip muscles": "forearms",
    "soleus": "calves",
    "gastrocnemius": "calves",
    "groin": "adductors",
    "sternocleidomastoid": "neck",
    "levator scapulae": "neck",
    "gluteus medius": "glutes",
    "gluteus minimus": "glutes",
}

# Внешнее значение -> несколько canonical кандидатов. Не подставляется.
AMBIGUOUS_MAP: dict[str, tuple[str, ...]] = {
    "back": ("lats", "middle back", "lower back"),
    "upper back": ("middle back", "traps"),
    "core": ("abdominals", "lower back"),
    "spine": ("lower back", "middle back"),
    "hip flexors": ("quadriceps", "abdominals"),
    "hips": ("glutes", "quadriceps"),
    "arms": ("biceps", "triceps", "forearms"),
    "legs": ("quadriceps", "hamstrings", "glutes", "calves"),
    "shoulder girdle": ("shoulders", "traps"),
}

# Значения, для которых canonical эквивалента нет. Перечислены явно, чтобы
# отличить «словарь не знает» от «мы решили не переносить».
KNOWN_UNMAPPED = frozenset(
    {
        "cardiovascular system",
        "serratus anterior",
        "feet",
        "ankles",
        "ankle stabilizers",
        "shins",
        "tibialis anterior",
        "diaphragm",
        "pelvic floor",
        "jaw",
        "eyes",
    }
)


def normalize_muscle_term(value: str) -> str:
    """Приводит обозначение мышцы к сопоставимому виду."""
    return " ".join(value.strip().lower().replace("ё", "е").replace("-", " ").split())


class MuscleResolution:
    """Результат приведения одного внешнего обозначения."""

    __slots__ = ("raw_value", "relation", "canonical", "candidates")

    def __init__(
        self,
        raw_value: str,
        relation: MuscleRelation,
        canonical: str | None = None,
        candidates: tuple[str, ...] = (),
    ) -> None:
        self.raw_value = raw_value
        self.relation = relation
        self.canonical = canonical
        self.candidates = candidates

    @property
    def usable(self) -> bool:
        """Можно ли подставить значение в canonical запись без решения человека."""
        return self.relation in (MuscleRelation.EXACT, MuscleRelation.BROADER)

    def __repr__(self) -> str:  # pragma: no cover - диагностика
        return (
            f"MuscleResolution({self.raw_value!r}, {self.relation.value}, "
            f"{self.canonical!r}, {self.candidates!r})"
        )


def resolve_muscle(value: str) -> MuscleResolution:
    """Приводит внешнее обозначение мышцы к canonical словарю."""
    normalized = normalize_muscle_term(value)
    if not normalized:
        return MuscleResolution(value, MuscleRelation.UNMAPPED)
    if normalized in EXACT_MAP:
        return MuscleResolution(value, MuscleRelation.EXACT, EXACT_MAP[normalized])
    if normalized in BROADER_MAP:
        return MuscleResolution(value, MuscleRelation.BROADER, BROADER_MAP[normalized])
    if normalized in AMBIGUOUS_MAP:
        return MuscleResolution(
            value, MuscleRelation.AMBIGUOUS, None, AMBIGUOUS_MAP[normalized]
        )
    return MuscleResolution(value, MuscleRelation.UNMAPPED)


class MuscleMappingResult:
    """Итог приведения набора обозначений: перенесённое и не перенесённое."""

    __slots__ = ("canonical", "inferred", "ambiguous", "unmapped")

    def __init__(self) -> None:
        self.canonical: list[str] = []
        self.inferred: list[str] = []
        self.ambiguous: dict[str, tuple[str, ...]] = {}
        self.unmapped: list[str] = []

    @property
    def has_gaps(self) -> bool:
        return bool(self.ambiguous or self.unmapped)


def map_muscles(values: list[str]) -> MuscleMappingResult:
    """Приводит список внешних обозначений к canonical словарю.

    Порядок сохраняется, дубликаты снимаются: у источника ``chest`` и
    ``pectorals`` встречаются вместе, и canonical запись не должна получить
    ``chest`` дважды.
    """
    result = MuscleMappingResult()
    seen: set[str] = set()
    for value in values:
        resolution = resolve_muscle(value)
        if resolution.relation is MuscleRelation.AMBIGUOUS:
            result.ambiguous[normalize_muscle_term(value)] = resolution.candidates
            continue
        if resolution.relation is MuscleRelation.UNMAPPED:
            result.unmapped.append(normalize_muscle_term(value))
            continue
        canonical = resolution.canonical or ""
        if canonical in seen:
            continue
        seen.add(canonical)
        result.canonical.append(canonical)
        if resolution.relation is MuscleRelation.BROADER:
            result.inferred.append(canonical)
    return result
