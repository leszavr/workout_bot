"""Характеристики упражнений для Safety Framework.

Характеристики выводятся детерминированно из полей каталога
(rule mapping: category + equipment + movement pattern → признаки).
Классификация консервативная: при недостатке данных признак считается
отсутствующим, а safety-правило при неуверенности возвращает WARNING
или REQUIRES_REVIEW вместо необоснованного EXCLUDE.

Важно: это технические признаки отбора, а не медицинская оценка.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ExerciseCharacteristics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_high_impact: bool = False
    has_spinal_load: bool = False
    has_overhead_component: bool = False
    has_deep_knee_flexion: bool = False
    raises_intra_abdominal_pressure: bool = False
    is_high_intensity_cardio: bool = False
    is_compound: bool = False
    characteristics_uncertain: bool = False
