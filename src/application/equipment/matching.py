"""Сопоставление формулировок оборудования с canonical ID словаря.

Здесь закрывается главный дефект прежней модели: словарь синонимов жил в
Python-коде (`EQUIPMENT_ALIASES` в фильтре), и добавление тренажёра требовало
правки кода. Теперь синонимы — данные (`equipment_aliases`), а этот модуль лишь
применяет их.

Два входа с разными правилами:

1. Значения источника каталога (`barbell`, `body only`, `e-z curl bar`) —
   сопоставление по полному совпадению. Каждое такое значение либо имеет ровно
   один canonical ID, либо не имеет ни одного; угадывать здесь нечего.
2. Свободный текст анкеты («две гантели по 16 кг и резина») — сопоставление по
   основам слов. Здесь возможны и множественные совпадения, и неоднозначность
   («мяч» — медбол или фитбол), и она возвращается как факт, а не разрешается
   выбором первого варианта.

Результат сопоставления всегда содержит не только найденное, но и не найденное:
без этого пробел в данных невидим, а «оборудование не распознано» неотличимо от
«оборудования нет».
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.infrastructure.persistence.postgres.equipment_repository import (
    EquipmentIndex,
    normalize_alias,
)


@dataclass(frozen=True)
class AliasMatch:
    """Результат сопоставления одного значения.

    ``ambiguous`` означает, что синоним указывает на несколько единиц
    оборудования. Такое совпадение нельзя использовать как подтверждённый факт:
    «мяч» законно означает и медбол, и фитбол.
    """

    raw_value: str
    equipment_ids: tuple[str, ...] = ()

    @property
    def matched(self) -> bool:
        return bool(self.equipment_ids)

    @property
    def ambiguous(self) -> bool:
        return len(self.equipment_ids) > 1

    @property
    def single(self) -> str | None:
        return self.equipment_ids[0] if len(self.equipment_ids) == 1 else None


@dataclass
class TextMatchResult:
    """Разбор свободного текста: что нашлось однозначно, что нет."""

    confident: set[str] = field(default_factory=set)
    ambiguous: dict[str, tuple[str, ...]] = field(default_factory=dict)
    matched_aliases: set[str] = field(default_factory=set)

    @property
    def any_match(self) -> bool:
        return bool(self.confident or self.ambiguous)


class EquipmentMatcher:
    """Применяет словарь синонимов к значениям каталога и свободному тексту."""

    def __init__(self, index: EquipmentIndex) -> None:
        self._index = index

    def match_catalog_value(self, raw_value: str) -> AliasMatch:
        """Сопоставляет значение источника каталога по полному совпадению.

        Только exact-синонимы: значения каталога — контролируемый набор, и
        сопоставление по основе слова здесь дало бы ложные срабатывания
        (`bands` внутри `resistance bands` — тот же смысл, но `ball` внутри
        `barbell` — уже нет).
        """
        normalized = normalize_alias(raw_value)
        if not normalized:
            return AliasMatch(raw_value=raw_value)
        found = self._index.exact_aliases.get(normalized, set())
        if not found and normalized in self._index.items:
            # Значение уже является canonical ID: так приходят данные из админки
            # и из повторного импорта уже нормализованных значений.
            found = {normalized}
        return AliasMatch(raw_value=raw_value, equipment_ids=tuple(sorted(found)))

    def match_text(self, text: str | None) -> TextMatchResult:
        """Разбирает свободный текст: и полные совпадения, и основы слов."""
        result = TextMatchResult()
        normalized = normalize_alias(text or "")
        if not normalized:
            return result

        exact = self._index.exact_aliases.get(normalized)
        if exact:
            self._record(result, normalized, exact)

        for alias, equipment_ids in self._index.stem_aliases.items():
            if alias in normalized:
                self._record(result, alias, equipment_ids)

        # Полные синонимы внутри фразы: «есть штанга и flat bench» содержит
        # значение каталога целиком, и терять его из-за режима exact нельзя.
        for alias, equipment_ids in self._index.exact_aliases.items():
            if len(alias) < 4 or alias == normalized:
                continue
            if _contains_word(normalized, alias):
                self._record(result, alias, equipment_ids)
        return result

    def match_values(self, values: list[str]) -> TextMatchResult:
        """Разбирает список формулировок как одно описание оборудования."""
        combined = TextMatchResult()
        for value in values:
            partial = self.match_text(value)
            combined.confident |= partial.confident
            combined.ambiguous.update(partial.ambiguous)
            combined.matched_aliases |= partial.matched_aliases
        # Синоним мог быть однозначным в одной фразе и неоднозначным в другой:
        # однозначное совпадение сильнее.
        for alias in list(combined.ambiguous):
            if set(combined.ambiguous[alias]) & combined.confident:
                combined.ambiguous.pop(alias)
        return combined

    @staticmethod
    def _record(
        result: TextMatchResult, alias: str, equipment_ids: set[str] | tuple[str, ...]
    ) -> None:
        ids = tuple(sorted(equipment_ids))
        result.matched_aliases.add(alias)
        if len(ids) == 1:
            result.confident.add(ids[0])
        else:
            result.ambiguous[alias] = ids


def _contains_word(haystack: str, needle: str) -> bool:
    """Проверяет вхождение синонима как отдельного слова или фразы.

    Без проверки границ `ball` нашёлся бы внутри `barbell`, и штанга получила бы
    требование медицинского мяча.

    Английское множественное число допускается отдельно: `Atlas_Stones` и
    `Battling_Ropes` — те же снаряды, что `atlas stone` и `battling ropes`, а
    ослаблять правую границу целиком нельзя: тогда `plate` совпал бы с
    `platform`.
    """
    candidates = [needle] if needle.endswith("s") else [needle, needle + "s"]
    return any(_contains_exact_word(haystack, value) for value in candidates)


def _contains_exact_word(haystack: str, needle: str) -> bool:
    start = 0
    while True:
        position = haystack.find(needle, start)
        if position < 0:
            return False
        before_ok = position == 0 or not haystack[position - 1].isalnum()
        end = position + len(needle)
        after_ok = end == len(haystack) or not haystack[end].isalnum()
        if before_ok and after_ok:
            return True
        start = position + 1
