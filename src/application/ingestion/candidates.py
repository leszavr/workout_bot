"""Нормализованный кандидат внешнего источника.

Одна структура для обоих источников. Она не является упражнением: у неё нет ни
canonical идентификатора, ни статуса активности, и в базу упражнений она не
записывается. Это разобранная внешняя запись, готовая к сопоставлению.

Списочные поля — кортежи: кандидат вычисляется один раз и дальше только
читается, а изменяемый список позволил бы сопоставлению незаметно править вход.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from src.application.ingestion.normalization import (
    core_tokens,
    latin_name_key,
    name_key,
    normalize_name,
    semantic_variant_tokens,
)


@dataclass(frozen=True)
class CandidateMedia:
    """Медиа внешней записи.

    ``relative_path`` — путь внутри локальной копии источника, а не URL: runtime
    приложения к внешнему источнику не обращается, файл берётся из копии и
    попадает в объектное хранилище проекта.
    """

    media_type: str
    relative_path: str
    source_media_id: str | None = None
    attribution: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class ExternalExerciseCandidate:
    """Разобранная внешняя запись, пригодная к сопоставлению и оценке."""

    source_key: str
    source_version: str
    source_record_id: str
    raw_name: str
    name: str
    description: str | None = None
    technique: str | None = None
    technique_ru: str | None = None
    equipment_values: tuple[str, ...] = ()
    body_part: str | None = None
    primary_muscle_values: tuple[str, ...] = ()
    secondary_muscle_values: tuple[str, ...] = ()
    media: tuple[CandidateMedia, ...] = ()
    attribution: str | None = None
    # Данные источника, не вошедшие в поля: сохраняются в payload staging-записи,
    # чтобы решение можно было перепроверить без повторного чтения источника.
    extra: dict = field(default_factory=dict)

    @property
    def name_key(self) -> str:
        return name_key(self.name)

    @property
    def latin_key(self) -> str:
        return latin_name_key(self.name)

    @property
    def core(self) -> frozenset[str]:
        return core_tokens(self.name)

    @property
    def variants(self) -> frozenset[str]:
        """Различители способа выполнения без учёта оборудования.

        Полный набор признаков различия строится в сопоставлении
        (``variant_signature``): он требует словаря оборудования, которого у
        кандидата нет и быть не должно.
        """
        return semantic_variant_tokens(self.name)

    def record_hash(self) -> str:
        """Хеш содержимого записи.

        Считается по нормализованным полям, а не по исходной строке источника:
        переформатирование источника не должно выглядеть как изменение данных, а
        изменение техники — должно.
        """
        payload = {
            "name": self.name,
            "description": self.description,
            "technique": self.technique,
            "technique_ru": self.technique_ru,
            "equipment": sorted(self.equipment_values),
            "body_part": self.body_part,
            "primary": sorted(self.primary_muscle_values),
            "secondary": sorted(self.secondary_muscle_values),
            "media": sorted(m.relative_path for m in self.media),
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def as_payload(self) -> dict:
        """Представление для staging-записи."""
        return {
            "name": self.name,
            "raw_name": self.raw_name,
            "description": self.description,
            "technique": self.technique,
            "technique_ru": self.technique_ru,
            "equipment_values": list(self.equipment_values),
            "body_part": self.body_part,
            "primary_muscle_values": list(self.primary_muscle_values),
            "secondary_muscle_values": list(self.secondary_muscle_values),
            "media": [
                {
                    "media_type": m.media_type,
                    "relative_path": m.relative_path,
                    "source_media_id": m.source_media_id,
                    "attribution": m.attribution,
                    "source_url": m.source_url,
                }
                for m in self.media
            ],
            "attribution": self.attribution,
            "extra": self.extra,
        }
