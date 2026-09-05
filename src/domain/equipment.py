"""Доменные модели Gym Knowledge Base: оборудование, требования, альтернативы.

Оборудование перестаёт быть свободной строкой. Знание разложено на четыре
независимых уровня, и каждый отвечает на свой вопрос:

1. ``EquipmentCapability`` — что объект умеет делать (наклонная опора,
   регулируемое сопротивление). Два тренажёра разных производителей называются
   по-разному, но функционально совпадают, и упражнению нужна именно
   возможность, а не бренд.
2. ``EquipmentItem`` — что это за объект (штанга, блочный тренажёр). Канонический
   идентификатор — стабильный строковый ``equipment_id``, а не название: название
   меняется и локализуется, ссылки — нет.
3. ``ExerciseEquipmentRequirement`` — что нужно упражнению, с различением
   «без этого нельзя» / «желательно» / «одно из».
4. ``EquipmentProfileItem`` — что фактически есть у пользователя или зала.

Совместимость вычисляется из этих фактов детерминированно (см.
``src/application/equipment/compatibility.py``). AI в вычислении не участвует и
не создаёт идентификаторы: любой ``equipment_id`` и ``capability_id`` обязан
существовать в базе.

Ключевое различие модели — ``UNKNOWN`` против ``INCOMPATIBLE``. Отсутствие знания
о требованиях упражнения и отсутствие ответа пользователя про тренажёр — это не
факт отсутствия оборудования. Поэтому у наличия оборудования три состояния
(``EquipmentAvailability``), у знания — три уровня доверия
(``KnowledgeConfidence``), а у результата проверки — три статуса
(``EquipmentCompatibilityStatus``).
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Идентификаторы словарей: латиница нижнего регистра, цифры и подчёркивание.
# Ограничение выражено паттерном, а не соглашением: `Cable Machine`,
# `cable-machine` и `cable_machine` иначе стали бы тремя разными ключами.
ID_PATTERN = r"^[a-z][a-z0-9_]*$"
MAX_ID_LENGTH = 64
MAX_NAME_LENGTH = 120
MAX_NOTE_LENGTH = 300


class EquipmentRequirement(StrEnum):
    """Характер потребности упражнения в оборудовании.

    ALTERNATIVE отличается от OPTIONAL: подъём на бицепс возможен с гантелями,
    штангой или в блоке — что-то из этого нужно обязательно, но не всё сразу.
    Такие строки объединяются номером группы ``alternative_group``.
    """

    REQUIRED = "required"
    OPTIONAL = "optional"
    ALTERNATIVE = "alternative"


class EquipmentAvailability(StrEnum):
    """Наличие оборудования в профиле.

    UNKNOWN существует отдельно от UNAVAILABLE: пользователь, который не ответил
    про наклонную скамью, не сообщил, что её нет.
    """

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class KnowledgeConfidence(StrEnum):
    """Насколько надёжен факт в базе знаний.

    CONFIRMED — из источника каталога или подтверждено человеком.
    INFERRED — выведено правилом сопоставления, требует проверки.
    UNKNOWN — факт отсутствует.
    """

    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class KnowledgeSource(StrEnum):
    """Происхождение записи базы знаний."""

    SEED = "seed"
    CATALOG_IMPORT = "catalog_import"
    NAME_INFERENCE = "name_inference"
    ADMIN = "admin"
    QUESTIONNAIRE = "questionnaire"
    PHOTO = "photo"
    DERIVED = "derived"


class UnmappedReason(StrEnum):
    """Почему значение оборудования из каталога не получило canonical ID.

    AMBIGUOUS и UNMAPPED различаются: `other` в источнике означает «оборудование
    есть, но какое — не сказано», а неизвестная строка означает «такого значения
    словарь не знает». Первое требует уточнения данных, второе — расширения
    словаря.
    """

    AMBIGUOUS = "ambiguous"
    UNMAPPED = "unmapped"


class EquipmentCompatibilityStatus(StrEnum):
    """Результат детерминированной проверки упражнения на оборудование."""

    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


class CompatibilityReason(StrEnum):
    """Причина решения. Ответ обязан быть объяснимым, а не одним словом."""

    NO_EQUIPMENT_NEEDED = "no_equipment_needed"
    ALL_REQUIRED_AVAILABLE = "all_required_available"
    ALTERNATIVE_EQUIPMENT_AVAILABLE = "alternative_equipment_available"
    SPECIALIZED_EQUIPMENT_AVAILABLE = "specialized_equipment_available"
    REQUIRED_EQUIPMENT_MISSING = "required_equipment_missing"
    NO_ALTERNATIVE_AVAILABLE = "no_alternative_available"
    REQUIREMENTS_UNKNOWN = "requirements_unknown"
    AVAILABILITY_UNKNOWN = "availability_unknown"


class SubstitutionType(StrEnum):
    """Насколько альтернатива заменяет исходное упражнение.

    Различение обязательно: «похожее движение» нельзя предъявлять как полную
    замену, иначе замена жима штанги разведением рук выглядит равноценной.
    """

    EXACT = "exact"
    SIMILAR = "similar"
    PARTIAL = "partial"


class EquipmentOwnerType(StrEnum):
    """Кому принадлежит профиль оборудования.

    Профиль не привязан жёстко к пользователю: один и тот же зал описывается
    один раз, а временный профиль («в отпуске, только резина») не должен
    затирать основной.
    """

    USER = "user"
    GYM = "gym"
    TEMPORARY = "temporary"


class AliasMatchMode(StrEnum):
    """Как синоним сопоставляется со входным текстом.

    EXACT — только полное совпадение нормализованного значения. Так приходят
    значения каталога (`body only`, `e-z curl bar`) и короткие слова, которые
    внутри других слов дают ложные срабатывания.

    STEM — совпадение как подстроки. Нужно для свободного текста анкеты: человек
    пишет «две гантели по 16 кг», и словарю нужна основа «гантел».
    """

    EXACT = "exact"
    STEM = "stem"


class EquipmentCapability(BaseModel):
    """Функциональная возможность оборудования."""

    model_config = ConfigDict(extra="forbid")

    capability_id: str = Field(pattern=ID_PATTERN, max_length=MAX_ID_LENGTH)
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    name_ru: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)
    is_active: bool = True


class EquipmentAlias(BaseModel):
    """Синоним оборудования: значение источника или формулировка пользователя."""

    model_config = ConfigDict(extra="forbid")

    alias: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    match_mode: AliasMatchMode = AliasMatchMode.EXACT
    source: KnowledgeSource = KnowledgeSource.SEED


class EquipmentItem(BaseModel):
    """Единица контролируемого словаря оборудования.

    ``capabilities`` — список ID возможностей. Проверка существования делается
    сервисным слоем по таблице возможностей, а не доверием к вводу.

    ``specializes`` — родовое оборудование, частным случаем которого является
    запись: ``leg_press`` специализирует ``resistance_machine``. Это отношение
    нужно, потому что источник каталога говорит родовыми словами: у 67
    упражнений оборудование указано как ``machine``, и без явной связи человек с
    жимом ногами получал бы «не подходит» на упражнение «жим ногами». Отношение
    выражено данными и направлено в одну сторону: частное закрывает требование
    родового, обратное неверно.

    ``protected_namespaces`` снят: ``model_name`` — это модель тренажёра
    («Hammer Strength Chest Press»), а не поле Pydantic, и переименовывать
    предметное понятие ради служебного префикса не требуется.
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    equipment_id: str = Field(pattern=ID_PATTERN, max_length=MAX_ID_LENGTH)
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    name_ru: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    category: str = Field(pattern=ID_PATTERN, max_length=MAX_ID_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)
    capabilities: list[str] = Field(default_factory=list)
    aliases: list[EquipmentAlias] = Field(default_factory=list)
    specializes: str | None = Field(
        default=None,
        max_length=MAX_ID_LENGTH,
        description="Родовое оборудование, частным случаем которого является запись",
    )
    manufacturer: str | None = Field(default=None, max_length=MAX_NAME_LENGTH)
    model_name: str | None = Field(default=None, max_length=MAX_NAME_LENGTH)
    attributes: dict = Field(
        default_factory=dict,
        description="Дополнительные признаки конкретного экземпляра (без секретов)",
    )
    source: KnowledgeSource = KnowledgeSource.SEED
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExerciseEquipmentRequirement(BaseModel):
    """Потребность упражнения в оборудовании или в его возможности.

    Ровно одно из ``equipment_id`` / ``capability_id`` заполнено: требование
    «нужен блочный тренажёр» и требование «нужно регулируемое сопротивление» —
    разные утверждения, и смешивать их в одном поле значит потерять смысл.
    """

    model_config = ConfigDict(extra="forbid")

    exercise_external_id: str = Field(min_length=1, max_length=128)
    exercise_source: str = Field(default="leszavr/workout", max_length=64)
    equipment_id: str | None = Field(default=None, max_length=MAX_ID_LENGTH)
    capability_id: str | None = Field(default=None, max_length=MAX_ID_LENGTH)
    requirement: EquipmentRequirement = EquipmentRequirement.REQUIRED
    alternative_group: int | None = Field(default=None, ge=1)
    confidence: KnowledgeConfidence = KnowledgeConfidence.CONFIRMED
    source: KnowledgeSource = KnowledgeSource.CATALOG_IMPORT
    notes: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)

    @model_validator(mode="after")
    def _check_target(self) -> ExerciseEquipmentRequirement:
        if bool(self.equipment_id) == bool(self.capability_id):
            raise ValueError(
                "требование указывает либо equipment_id, либо capability_id"
            )
        if (
            self.requirement is EquipmentRequirement.ALTERNATIVE
            and self.alternative_group is None
        ):
            # Без группы «одно из» нечитаемо: три независимые ALTERNATIVE-строки
            # неотличимы от одной группы из трёх вариантов.
            raise ValueError("ALTERNATIVE требует alternative_group")
        return self


class UnmappedEquipmentValue(BaseModel):
    """Значение оборудования источника, не получившее canonical ID.

    Существует, чтобы миграция не теряла информацию молча: строка `other`
    остаётся видимой как незакрытый пробел данных, а не исчезает.
    """

    model_config = ConfigDict(extra="forbid")

    exercise_external_id: str = Field(min_length=1, max_length=128)
    exercise_source: str = Field(default="leszavr/workout", max_length=64)
    raw_value: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    reason: UnmappedReason = UnmappedReason.UNMAPPED
    notes: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)


class ExerciseAlternative(BaseModel):
    """Альтернативное упражнение с явным типом замены и обоснованием."""

    model_config = ConfigDict(extra="forbid")

    exercise_external_id: str = Field(min_length=1, max_length=128)
    exercise_source: str = Field(default="leszavr/workout", max_length=64)
    alternative_external_id: str = Field(min_length=1, max_length=128)
    alternative_source: str = Field(default="leszavr/workout", max_length=64)
    substitution: SubstitutionType = SubstitutionType.SIMILAR
    score: float = Field(ge=0.0, le=1.0)
    rationale: dict = Field(
        default_factory=dict,
        description="Совпавшие и разошедшиеся признаки: почему это замена",
    )
    source: KnowledgeSource = KnowledgeSource.DERIVED
    notes: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)


class EquipmentProfileItem(BaseModel):
    """Одна позиция оборудования в профиле пользователя или зала.

    ``extra_capabilities`` дополняет возможности самого типа оборудования: у
    скамьи в конкретном зале может быть регулировка, которой у базового типа
    нет. Убирать возможности здесь нельзя — для этого выбирается более точный
    тип (``flat_bench`` вместо ``adjustable_bench``).

    ``source_ref`` хранит ссылку на источник факта — например, ключ фотографии в
    объектном хранилище. Так путь «фото → кандидат → подтверждение человеком»
    выражается состоянием записи, а не отдельной подсистемой.
    """

    model_config = ConfigDict(extra="forbid")

    equipment_id: str = Field(max_length=MAX_ID_LENGTH)
    quantity: int | None = Field(default=None, ge=0)
    availability: EquipmentAvailability = EquipmentAvailability.AVAILABLE
    confidence: KnowledgeConfidence = KnowledgeConfidence.CONFIRMED
    extra_capabilities: list[str] = Field(default_factory=list)
    source: KnowledgeSource = KnowledgeSource.ADMIN
    source_ref: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)


class EquipmentProfile(BaseModel):
    """Описание фактически доступного оборудования.

    ``assume_unlisted_unavailable`` отвечает на вопрос, что значит отсутствие
    позиции в профиле. Для домашнего профиля, где человек перечислил всё, что у
    него есть, это «нет». Для зала, про который известно только название, это
    «неизвестно», и придумывать отсутствие тренажёра нельзя.
    """

    model_config = ConfigDict(extra="forbid")

    profile_key: str = Field(min_length=1, max_length=64)
    owner_type: EquipmentOwnerType = EquipmentOwnerType.USER
    owner_ref: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    items: list[EquipmentProfileItem] = Field(default_factory=list)
    assume_unlisted_unavailable: bool = False
    source: KnowledgeSource = KnowledgeSource.ADMIN
    notes: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RequirementCheck(BaseModel):
    """Разбор одного требования упражнения при проверке совместимости."""

    model_config = ConfigDict(extra="forbid")

    requirement: EquipmentRequirement
    alternative_group: int | None = None
    equipment_id: str | None = None
    capability_id: str | None = None
    availability: EquipmentAvailability
    satisfied_by: str | None = Field(
        default=None, description="equipment_id, которым закрыто требование"
    )


class CompatibilityResult(BaseModel):
    """Результат детерминированной проверки «упражнение × оборудование».

    Возвращается не только статус: без списка недостающего оборудования
    администратор не может ни проверить решение, ни исправить данные.
    """

    model_config = ConfigDict(extra="forbid")

    exercise_external_id: str
    exercise_source: str = "leszavr/workout"
    status: EquipmentCompatibilityStatus
    reason: CompatibilityReason
    missing: list[str] = Field(default_factory=list)
    matched: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)
    checks: list[RequirementCheck] = Field(default_factory=list)


class EquipmentUsage(BaseModel):
    """Оборудование словаря вместе с числом связанных упражнений."""

    model_config = ConfigDict(extra="forbid")

    item: EquipmentItem
    exercise_count: int = Field(ge=0)


class KnowledgeBaseHealth(BaseModel):
    """Диагностика полноты и целостности базы знаний.

    Все числа считаются запросами к базе. Захардкоженная метрика показывала бы
    состояние на момент написания кода, а не текущее.
    """

    model_config = ConfigDict(extra="forbid")

    exercises_total: int = Field(ge=0)
    exercises_active: int = Field(ge=0)
    equipment_known: int = Field(ge=0)
    equipment_unknown: int = Field(ge=0)
    equipment_confirmed: int = Field(ge=0)
    equipment_inferred: int = Field(ge=0)
    exercises_with_alternatives: int = Field(ge=0)
    equipment_items_total: int = Field(ge=0)
    equipment_items_active: int = Field(ge=0)
    equipment_items_unused: int = Field(ge=0)
    capabilities_total: int = Field(ge=0)
    aliases_total: int = Field(ge=0)
    requirements_total: int = Field(ge=0)
    alternatives_total: int = Field(ge=0)
    unmapped_values: int = Field(ge=0)
    unmapped_exercises: int = Field(ge=0)
    orphan_equipment_references: int = Field(ge=0)
    invalid_capability_references: int = Field(ge=0)
    impossible_requirement_combinations: int = Field(ge=0)
    duplicate_requirements: int = Field(ge=0)

    @property
    def equipment_known_ratio(self) -> float:
        if not self.exercises_total:
            return 0.0
        return self.equipment_known / self.exercises_total
