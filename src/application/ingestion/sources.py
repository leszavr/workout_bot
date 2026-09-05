"""Чтение локальных копий внешних источников.

Runtime приложения к внешним источникам не обращается. Оба читателя работают с
локальной копией: каталог `hasaneyldrm/exercises-dataset` — распакованный
репозиторий с `data/exercises.json`, `images/` и `videos/`; датасет Kaggle — два
CSV из архива. Отсюда следует и способ фиксации версии: для GitHub это commit
SHA, для Kaggle — хеш содержимого файла, потому что стабильного номера версии
датасет не публикует.

Читатели не принимают решений о записях. Их результат — нормализованные
кандидаты и версия источника; сопоставление, оценка и merge живут в отдельных
модулях, и смешивать чтение с решением значило бы делать источник неповторяемым:
изменение правила требовало бы перечитывания файлов.
"""
from __future__ import annotations

import ast
import csv
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from src.application.ingestion.candidates import (
    CandidateMedia,
    ExternalExerciseCandidate,
)
from src.application.ingestion.normalization import (
    clean_text,
    normalize_name,
    steps_to_technique,
)
from src.domain.ingestion import (
    ExternalSource,
    ExternalSourceKind,
    ExternalSourceVersion,
)

# --- Источник A: каталог упражнений hasaneyldrm/exercises-dataset --------------

GITHUB_SOURCE_KEY = "hasaneyldrm/exercises-dataset"
GITHUB_SOURCE = ExternalSource(
    source_key=GITHUB_SOURCE_KEY,
    name="hasaneyldrm/exercises-dataset",
    kind=ExternalSourceKind.EXERCISE_CATALOG,
    homepage="https://github.com/hasaneyldrm/exercises-dataset",
    data_license="MIT (данные: названия, категории, оборудование, инструкции)",
    media_license=(
        "© Gym visual — https://gymvisual.com/ (media включено с разрешения "
        "правообладателя, 180x180, атрибуция обязательна)"
    ),
    attribution="© Gym visual — https://gymvisual.com/",
    notes=(
        "Инструкции на 10 языках, включая ru. Media: 180x180 thumbnail (jpg) и "
        "анимация (gif). Права на использование media согласованы владельцем "
        "проекта; атрибуция сохраняется в метаданных каждого ассета."
    ),
)

MEDIA_TYPE_IMAGE = "image"
MEDIA_TYPE_ANIMATION = "animation"


@dataclass
class SourceRead:
    """Результат чтения источника: версия и кандидаты."""

    source: ExternalSource
    version: ExternalSourceVersion
    candidates: list[ExternalExerciseCandidate] = field(default_factory=list)
    # Диагностика чтения: то, что источник не дал, обязано быть видно.
    stats: dict = field(default_factory=dict)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _detect_git_sha(root: Path) -> str | None:
    """Читает commit SHA из локального клона, если он есть.

    Копия может быть развёрнута из архива без `.git` — тогда версия задаётся
    аргументом командной строки, а не выдумывается.
    """
    head = root / ".git" / "HEAD"
    if not head.is_file():
        return None
    try:
        content = head.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if content.startswith("ref:"):
        ref = content.split(" ", 1)[1].strip()
        ref_path = root / ".git" / ref
        if ref_path.is_file():
            try:
                return ref_path.read_text(encoding="utf-8").strip()
            except OSError:
                return None
        return None
    return content or None


def read_github_dataset(
    root: Path, *, version: str | None = None
) -> SourceRead:
    """Читает каталог упражнений источника A из локальной копии."""
    data_path = root / "data" / "exercises.json"
    if not data_path.is_file():
        raise FileNotFoundError(f"Не найден файл источника: {data_path}")

    content_hash = _file_hash(data_path)
    resolved_version = version or _detect_git_sha(root) or f"sha256:{content_hash[:16]}"

    try:
        records = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Не удалось прочитать {data_path}: {exc}") from exc
    if not isinstance(records, list):
        raise ValueError("Ожидался массив записей в data/exercises.json")

    stats = {
        "records_total": len(records),
        "with_ru_instructions": 0,
        "with_en_instructions": 0,
        "with_image": 0,
        "with_animation": 0,
        "missing_media_file": 0,
        "skipped_invalid": 0,
    }

    candidates: list[ExternalExerciseCandidate] = []
    for record in records:
        record_id = str(record.get("id") or "").strip()
        raw_name = str(record.get("name") or "").strip()
        if not record_id or not raw_name:
            stats["skipped_invalid"] += 1
            continue

        instructions = record.get("instructions") or {}
        steps = record.get("instruction_steps") or {}
        en_steps = [str(s) for s in (steps.get("en") or [])]
        ru_steps = [str(s) for s in (steps.get("ru") or [])]
        technique = steps_to_technique(en_steps) or clean_text(
            instructions.get("en")
        ) or None
        technique_ru = steps_to_technique(ru_steps) or clean_text(
            instructions.get("ru")
        ) or None
        if technique:
            stats["with_en_instructions"] += 1
        if technique_ru:
            stats["with_ru_instructions"] += 1

        media: list[CandidateMedia] = []
        attribution = clean_text(record.get("attribution")) or None
        for field_name, media_type in (
            ("image", MEDIA_TYPE_IMAGE),
            ("gif_url", MEDIA_TYPE_ANIMATION),
        ):
            relative = str(record.get(field_name) or "").strip()
            if not relative:
                continue
            if not (root / relative).is_file():
                stats["missing_media_file"] += 1
                continue
            media.append(
                CandidateMedia(
                    media_type=media_type,
                    relative_path=relative,
                    source_media_id=str(record.get("media_id") or "") or None,
                    attribution=attribution,
                    source_url=(
                        "https://github.com/hasaneyldrm/exercises-dataset/blob/main/"
                        f"{relative}"
                    ),
                )
            )
            if media_type == MEDIA_TYPE_IMAGE:
                stats["with_image"] += 1
            else:
                stats["with_animation"] += 1

        equipment = str(record.get("equipment") or "").strip()
        # `muscle_group` источника — синергист, а не целевая мышца. Он идёт в
        # дополнительные: подмена целевой мышцы синергистом изменила бы роль
        # упражнения в программе.
        secondary_values = [
            str(v) for v in (record.get("secondary_muscles") or []) if str(v).strip()
        ]
        muscle_group = str(record.get("muscle_group") or "").strip()
        if muscle_group:
            secondary_values.append(muscle_group)

        target = str(record.get("target") or "").strip()
        body_part = str(record.get("body_part") or record.get("category") or "").strip()

        candidates.append(
            ExternalExerciseCandidate(
                source_key=GITHUB_SOURCE_KEY,
                source_version=resolved_version,
                source_record_id=record_id,
                raw_name=raw_name,
                name=normalize_name(raw_name),
                description=None,
                technique=technique,
                technique_ru=technique_ru,
                equipment_values=(equipment,) if equipment else (),
                body_part=body_part or None,
                primary_muscle_values=(target,) if target else (),
                secondary_muscle_values=tuple(secondary_values),
                media=tuple(media),
                attribution=attribution,
                extra={
                    "category": record.get("category"),
                    "media_id": record.get("media_id"),
                    "created_at": record.get("created_at"),
                    "instruction_languages": sorted(
                        k for k, v in instructions.items() if str(v or "").strip()
                    ),
                },
            )
        )

    return SourceRead(
        source=GITHUB_SOURCE,
        version=ExternalSourceVersion(
            source_key=GITHUB_SOURCE_KEY,
            version=resolved_version,
            content_hash=content_hash,
            retrieved_at=datetime.now(UTC),
            record_count=len(candidates),
            notes="data/exercises.json из локальной копии репозитория",
        ),
        candidates=candidates,
        stats=stats,
    )


# --- Источник B: датасет программ Kaggle ---------------------------------------

KAGGLE_SOURCE_KEY = "kaggle/600k-fitness-exercise-and-workout-program"
KAGGLE_SOURCE = ExternalSource(
    source_key=KAGGLE_SOURCE_KEY,
    name="Kaggle: 600K fitness exercise and workout program dataset",
    kind=ExternalSourceKind.PROGRAM_DATASET,
    homepage=(
        "https://www.kaggle.com/datasets/adnanelouardi/"
        "600k-fitness-exercise-and-workout-program-dataset"
    ),
    data_license="Kaggle dataset (см. страницу датасета)",
    media_license=None,
    attribution="Kaggle: adnanelouardi / boostcamp program dataset",
    notes=(
        "Датасет программ, а не каталог упражнений: техники, описаний и media "
        "нет. Даёт программный контекст — подходы, повторения, интенсивность, "
        "цель и уровень."
    ),
)

# Столбцы источника. Перечислены явно: изменение формата должно приводить к
# явной ошибке чтения, а не к молча пустым наблюдениям.
KAGGLE_DETAILED_COLUMNS = (
    "title",
    "description",
    "level",
    "goal",
    "equipment",
    "program_length",
    "time_per_workout",
    "week",
    "day",
    "number_of_exercises",
    "exercise_name",
    "sets",
    "reps",
    "intensity",
    "created",
    "last_edit",
)

# Сколько значений сохранять в распределениях цели, уровня и контекста
# оборудования. Полное распределение здесь не нужно: наблюдение отвечает на
# вопрос «где это упражнение встречается чаще всего».
TOP_CONTEXT_VALUES = 6


@dataclass
class KaggleAggregate:
    """Агрегат по одному названию упражнения из датасета программ."""

    name: str
    occurrence_count: int = 0
    programs: set[str] = field(default_factory=set)
    sets: list[float] = field(default_factory=list)
    reps: list[float] = field(default_factory=list)
    holds: list[float] = field(default_factory=list)
    intensity: list[float] = field(default_factory=list)
    goals: Counter = field(default_factory=Counter)
    levels: Counter = field(default_factory=Counter)
    equipment_contexts: Counter = field(default_factory=Counter)


def _parse_list_literal(raw: str) -> list[str]:
    """Разбирает поле-список источника (`"['Beginner', 'Novice']"`)."""
    text = (raw or "").strip()
    if not text or text in {"[]", "nan"}:
        return []
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return [text]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def _parse_number(raw: str) -> float | None:
    text = (raw or "").strip()
    if not text or text.lower() == "nan":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _median_int(values: list[float]) -> tuple[float | None, int | None, int | None]:
    if not values:
        return None, None, None
    return (
        round(statistics.median(values), 2),
        int(min(values)),
        int(max(values)),
    )


def read_kaggle_dataset(
    root: Path,
    *,
    version: str | None = None,
    row_limit: int | None = None,
) -> tuple[SourceRead, dict[str, KaggleAggregate]]:
    """Читает датасет программ и агрегирует его по названиям упражнений.

    Возвращает пару: кандидаты (по одному на уникальное название) и агрегаты
    программного контекста. Разделение существенно: кандидат отвечает на вопрос
    «есть ли такое упражнение», агрегат — «как оно используется», и второе
    полезно даже тогда, когда первое отклонено.

    605 тысяч строк никогда не становятся 605 тысячами упражнений: агрегация по
    названию — единственный корректный способ прочитать этот источник, и она
    выполняется здесь, а не в сопоставлении.
    """
    detailed = root / "programs_detailed_boostcamp_kaggle.csv"
    summary = root / "program_summary.csv"
    if not detailed.is_file():
        raise FileNotFoundError(f"Не найден файл источника: {detailed}")

    content_hash = _file_hash(detailed)
    resolved_version = version or f"sha256:{content_hash[:16]}"

    # 605K строк содержат описания программ: поле может превышать стандартный
    # лимит csv-модуля, и без расширения чтение падает на середине файла.
    csv.field_size_limit(min(sys.maxsize, 10 * 1024 * 1024))

    aggregates: dict[str, KaggleAggregate] = {}
    stats = {
        "rows_total": 0,
        "rows_used": 0,
        "rows_without_name": 0,
        "programs_total": 0,
        "unique_exercise_names": 0,
        "reps_as_hold_seconds": 0,
        "summary_programs": 0,
    }
    programs: set[str] = set()

    with detailed.open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in KAGGLE_DETAILED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(
                f"В {detailed.name} отсутствуют столбцы: {', '.join(missing)}"
            )
        for row in reader:
            stats["rows_total"] += 1
            if row_limit is not None and stats["rows_total"] > row_limit:
                break
            raw_name = (row.get("exercise_name") or "").strip()
            if not raw_name or raw_name.lower() == "nan":
                stats["rows_without_name"] += 1
                continue
            stats["rows_used"] += 1

            title = (row.get("title") or "").strip()
            programs.add(title)

            key = normalize_name(raw_name)
            aggregate = aggregates.get(key)
            if aggregate is None:
                aggregate = KaggleAggregate(name=key)
                aggregates[key] = aggregate
            aggregate.occurrence_count += 1
            aggregate.programs.add(title)
            aggregate.goals.update(_parse_list_literal(row.get("goal") or ""))
            aggregate.levels.update(_parse_list_literal(row.get("level") or ""))
            equipment_context = (row.get("equipment") or "").strip()
            if equipment_context:
                aggregate.equipment_contexts[equipment_context] += 1

            sets_value = _parse_number(row.get("sets") or "")
            if sets_value is not None and sets_value > 0:
                aggregate.sets.append(sets_value)
            reps_value = _parse_number(row.get("reps") or "")
            if reps_value is not None:
                if reps_value < 0:
                    # Отрицательные повторения в источнике обозначают удержание
                    # в секундах. Складывать их с повторениями нельзя: это две
                    # разные величины.
                    aggregate.holds.append(abs(reps_value))
                    stats["reps_as_hold_seconds"] += 1
                elif reps_value > 0:
                    aggregate.reps.append(reps_value)
            intensity_value = _parse_number(row.get("intensity") or "")
            if intensity_value is not None and intensity_value > 0:
                aggregate.intensity.append(intensity_value)

    stats["programs_total"] = len(programs)
    stats["unique_exercise_names"] = len(aggregates)

    if summary.is_file():
        with summary.open(newline="", encoding="utf-8", errors="replace") as handle:
            stats["summary_programs"] = sum(1 for _ in csv.DictReader(handle))

    candidates = [
        ExternalExerciseCandidate(
            source_key=KAGGLE_SOURCE_KEY,
            source_version=resolved_version,
            source_record_id=_kaggle_record_id(aggregate.name),
            raw_name=aggregate.name,
            name=aggregate.name,
            description=None,
            technique=None,
            technique_ru=None,
            equipment_values=(),
            body_part=None,
            primary_muscle_values=(),
            secondary_muscle_values=(),
            media=(),
            attribution=KAGGLE_SOURCE.attribution,
            extra={
                "occurrence_count": aggregate.occurrence_count,
                "program_count": len(aggregate.programs),
                "goals": aggregate.goals.most_common(TOP_CONTEXT_VALUES),
                "levels": aggregate.levels.most_common(TOP_CONTEXT_VALUES),
                "equipment_contexts": aggregate.equipment_contexts.most_common(
                    TOP_CONTEXT_VALUES
                ),
            },
        )
        for aggregate in aggregates.values()
    ]

    read = SourceRead(
        source=KAGGLE_SOURCE,
        version=ExternalSourceVersion(
            source_key=KAGGLE_SOURCE_KEY,
            version=resolved_version,
            content_hash=content_hash,
            retrieved_at=datetime.now(UTC),
            record_count=len(candidates),
            notes=(
                f"{stats['rows_used']} строк, {stats['programs_total']} программ, "
                f"{stats['unique_exercise_names']} уникальных названий"
            ),
        ),
        candidates=candidates,
        stats=stats,
    )
    return read, aggregates


def _kaggle_record_id(name: str) -> str:
    """Стабильный идентификатор записи датасета программ.

    Собственных идентификаторов упражнений источник не имеет: единственный ключ —
    название. Идентификатор строится как хеш нормализованного названия, потому
    что название длиннее 128 символов встречается, а ключ ограничен схемой.
    """
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]
    return f"name:{digest}"


def observation_metrics(aggregate: KaggleAggregate) -> dict:
    """Числовые характеристики наблюдения для записи в базу."""
    sets_median, sets_min, sets_max = _median_int(aggregate.sets)
    reps_median, reps_min, reps_max = _median_int(aggregate.reps)
    holds_median, _, _ = _median_int(aggregate.holds)
    intensity_median, _, _ = _median_int(aggregate.intensity)
    return {
        "program_count": len(aggregate.programs),
        "occurrence_count": aggregate.occurrence_count,
        "typical_sets_median": sets_median,
        "typical_sets_min": sets_min,
        "typical_sets_max": sets_max,
        "typical_reps_median": reps_median,
        "typical_reps_min": reps_min,
        "typical_reps_max": reps_max,
        "typical_hold_seconds_median": holds_median,
        "typical_intensity_median": intensity_median,
        "source_goals": dict(aggregate.goals.most_common(TOP_CONTEXT_VALUES)),
        "source_levels": dict(aggregate.levels.most_common(TOP_CONTEXT_VALUES)),
        "source_equipment_contexts": dict(
            aggregate.equipment_contexts.most_common(TOP_CONTEXT_VALUES)
        ),
    }


def merge_aggregates(aggregates: list[KaggleAggregate]) -> KaggleAggregate:
    """Объединяет агрегаты нескольких названий одного упражнения.

    Нужно потому, что разные названия датасета программ сопоставляются с одним
    canonical упражнением: `Bench Press (Barbell)` и `Barbell Bench Press` — одна
    и та же запись каталога. Оставить одно из них значило бы выбросить половину
    наблюдений, а записать оба нельзя: наблюдение уникально по паре «упражнение —
    источник».

    Объединяются исходные наблюдения, а не их медианы: медиана от медиан не
    является медианой, и такое «среднее» было бы числом без смысла. Списки
    подходов и повторений складываются, счётчики целей и уровней суммируются,
    множество программ объединяется — после чего метрики считаются один раз.
    """
    if not aggregates:
        raise ValueError("нечего объединять")
    merged = KaggleAggregate(name=aggregates[0].name)
    for aggregate in aggregates:
        merged.occurrence_count += aggregate.occurrence_count
        merged.programs |= aggregate.programs
        merged.sets.extend(aggregate.sets)
        merged.reps.extend(aggregate.reps)
        merged.holds.extend(aggregate.holds)
        merged.intensity.extend(aggregate.intensity)
        merged.goals.update(aggregate.goals)
        merged.levels.update(aggregate.levels)
        merged.equipment_contexts.update(aggregate.equipment_contexts)
    return merged


def group_by_name(candidates: list[ExternalExerciseCandidate]) -> dict[str, list]:
    """Группирует кандидатов по ключу сопоставления.

    Нужно кросс-источниковому отчёту: пересечение источников считается по ключу,
    а не по названию, иначе `Bench Press (Barbell)` и `Barbell Bench Press`
    выглядели бы разными упражнениями.
    """
    grouped: dict[str, list] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.name_key].append(candidate)
    return dict(grouped)
