"""Наполнение словаря оборудования: возможности, оборудование, синонимы

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-05

0014 создал схему, эта миграция наполняет контролируемый словарь. Разделение
намеренное: схема и данные откатываются независимо, и повторное наполнение не
требует пересоздания таблиц.

Почему словарь заводится миграцией, а не скриптом. Compatibility engine без
словаря не работает вовсе: сопоставить `barbell` из каталога с canonical ID
невозможно, если canonical ID не существует. Словарь — это не прикладные данные
вроде каталога упражнений, который может отсутствовать в тестовом окружении, а
предпосылка работоспособности кода. Поэтому он поставляется вместе со схемой и
присутствует в каждом окружении без отдельного шага развёртывания.

Почему словарь при этом не в Python-коде. После этой миграции ни фильтр, ни
генератор, ни API не содержат перечисления оборудования: добавление
`Hammer Strength Chest Press` — это строка в `equipment_items` плюс строки в
`equipment_aliases`, а не правка `if/elif`. Единственное место, где перечисление
существует в виде литерала, — эта миграция, и она описывает начальное состояние,
а не поведение системы.

Синонимы делятся на два режима сопоставления:

- `exact` — полное совпадение нормализованного значения. Так приходят значения
  источника каталога (`body only`, `e-z curl bar`) и короткие слова, которые
  внутри других слов дают ложные срабатывания (`floor`, `мат`);
- `stem` — совпадение как подстроки, для свободного текста анкеты: человек
  пишет «две гантели по 16 кг», и словарю нужна основа «гантел».

Неоднозначные синонимы разрешены и оставлены сознательно: «мяч» законно
указывает и на медицинский мяч, и на фитбол. Сопоставление обязано вернуть оба
варианта как неподтверждённые, а не выбрать первый молча.

Все 12 значений `equipment` действующего каталога, кроме `other`, получают
ровно один canonical ID через `exact`-синоним. `other` синонима не получает
намеренно: это не оборудование, а отсутствие сведений о нём, и импорт (0016)
записывает такие упражнения в `unmapped_equipment_values` с причиной
`ambiguous`.

Идемпотентность: все вставки идут через `ON CONFLICT DO NOTHING`, повторный
`upgrade` ничего не дублирует и не перезаписывает правки администратора.

Downgrade удаляет только то, что создала эта миграция, и только если на строку
никто не ссылается: оборудование, добавленное администратором, и связанные с ним
требования сохраняются. Требования и альтернативы, выведенные из каталога
(источники `catalog_import`, `name_inference`, `derived`), удаляются: без словаря
они ссылались бы в пустоту.
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

SEED_SOURCE = "seed"

# (capability_id, name, name_ru, description)
CAPABILITIES: tuple[tuple[str, str, str, str | None], ...] = (
    ("free_weight", "Free weight", "Свободный вес",
     "Вес не закреплён, траектория задаётся человеком"),
    ("fixed_path", "Fixed movement path", "Фиксированная траектория",
     "Траектория задана конструкцией"),
    ("plate_loaded", "Plate loaded", "Загрузка дисками", None),
    ("adjustable_load", "Adjustable load", "Регулируемый вес", None),
    ("adjustable_resistance", "Adjustable resistance", "Регулируемое сопротивление",
     "Вес меняется без смены снаряда (стек, фиксатор)"),
    ("variable_resistance", "Variable resistance", "Переменное сопротивление",
     "Сопротивление растёт с растяжением (резина)"),
    ("adjustable_height", "Adjustable height", "Регулируемая высота", None),
    ("adjustable_angle", "Adjustable angle", "Регулируемый угол наклона", None),
    ("flat_support", "Flat support", "Горизонтальная опора", None),
    ("incline_support", "Incline support", "Наклонная опора вверх", None),
    ("decline_support", "Decline support", "Наклонная опора вниз", None),
    ("back_support", "Back support", "Опора для спины", None),
    ("unstable_support", "Unstable support", "Неустойчивая опора",
     "Опора требует активной стабилизации"),
    ("overhead_capable", "Overhead loading", "Работа с весом над головой", None),
    ("hanging_support", "Hanging support", "Опора для виса", None),
    ("suspension_support", "Suspension support", "Подвесная опора", None),
    ("unilateral_capable", "Unilateral work", "Односторонняя работа", None),
    ("rope_attachment", "Rope attachment", "Рукоять-верёвка", None),
    ("bar_attachment", "Bar attachment", "Рукоять-гриф", None),
    ("single_handle", "Single handle", "Одиночная рукоять", None),
    ("safety_catch", "Safety catches", "Страховочные упоры", None),
    ("continuous_cardio", "Continuous cardio", "Непрерывное кардио", None),
    ("ground_support", "Ground support", "Опора на пол", None),
    ("elevated_platform", "Elevated platform", "Возвышение", None),
    ("rotational_capable", "Rotational loading", "Вращательная нагрузка", None),
    ("grip_intensive", "Grip intensive", "Высокая нагрузка на хват", None),
)

# (equipment_id, name, name_ru, category, capabilities, aliases)
# alias: (значение, режим сопоставления)
EQUIPMENT: tuple[
    tuple[str, str, str, str, tuple[str, ...], tuple[tuple[str, str], ...]], ...
] = (
    # --- Свободный вес и грифы ---
    ("barbell", "Barbell", "Штанга", "free_weight",
     ("free_weight", "plate_loaded", "adjustable_load", "overhead_capable"),
     (("barbell", "exact"), ("olympic barbell", "exact"), ("штанг", "stem"))),
    ("ez_curl_bar", "EZ curl bar", "Изогнутый гриф", "free_weight",
     ("free_weight", "plate_loaded", "adjustable_load"),
     (("e-z curl bar", "exact"), ("ez bar", "exact"), ("изогнутый гриф", "stem"))),
    ("trap_bar", "Trap bar", "Трэп-гриф", "free_weight",
     ("free_weight", "plate_loaded", "adjustable_load"),
     (("trap bar", "exact"), ("трэп-гриф", "stem"))),
    ("axle_bar", "Axle bar", "Толстый гриф", "free_weight",
     ("free_weight", "plate_loaded", "grip_intensive"),
     (("axle", "exact"), ("толстый гриф", "stem"))),
    ("log_bar", "Log bar", "Бревно", "strongman",
     ("free_weight", "overhead_capable", "grip_intensive"),
     (("log", "exact"), ("бревно", "stem"))),
    ("dumbbell", "Dumbbell", "Гантели", "free_weight",
     ("free_weight", "unilateral_capable", "overhead_capable", "adjustable_load"),
     (("dumbbell", "exact"), ("dumbbells", "exact"), ("гантел", "stem"))),
    ("kettlebell", "Kettlebell", "Гиря", "free_weight",
     ("free_weight", "unilateral_capable", "overhead_capable", "grip_intensive"),
     (("kettlebells", "exact"), ("kettlebell", "exact"), ("гир", "stem"))),
    ("weight_plate", "Weight plate", "Диск (блин)", "free_weight",
     ("free_weight", "grip_intensive"),
     (("plate", "exact"), ("блин", "stem"), ("диск", "stem"))),
    ("medicine_ball", "Medicine ball", "Медицинский мяч", "ball",
     ("free_weight", "rotational_capable"),
     (("medicine ball", "exact"), ("медбол", "stem"), ("мяч", "stem"))),
    ("exercise_ball", "Exercise ball", "Фитбол", "ball",
     ("unstable_support",),
     (("exercise ball", "exact"), ("фитбол", "stem"), ("мяч", "stem"))),
    ("sandbag", "Sandbag", "Мешок с песком", "strongman",
     ("free_weight", "grip_intensive"),
     (("sandbag", "exact"), ("мешок с песком", "stem"))),
    ("atlas_stone", "Atlas stone", "Камень атласа", "strongman",
     ("free_weight", "grip_intensive"),
     (("atlas stone", "exact"), ("камень атласа", "stem"))),
    ("keg", "Keg", "Кег", "strongman",
     ("free_weight", "grip_intensive"), (("keg", "exact"), ("кег", "stem"))),
    ("sledgehammer", "Sledgehammer", "Кувалда", "strongman",
     ("free_weight", "rotational_capable"),
     (("sledgehammer", "exact"), ("кувалда", "stem"))),
    ("lifting_chains", "Lifting chains", "Цепи", "accessory",
     ("free_weight", "variable_resistance"),
     (("chains", "exact"), ("цепи", "stem"))),
    ("farmers_walk_handles", "Farmer's walk handles", "Рукояти фермера", "strongman",
     ("free_weight", "grip_intensive"),
     (("farmers walk handles", "exact"), ("рукояти фермера", "stem"))),
    ("yoke", "Yoke", "Ярмо", "strongman",
     ("free_weight",), (("yoke", "exact"), ("ярмо", "stem"))),
    ("lifting_straps", "Lifting straps", "Лямки", "accessory",
     (), (("straps", "exact"), ("лямки", "stem"))),

    # --- Скамьи и опоры ---
    ("flat_bench", "Flat bench", "Горизонтальная скамья", "bench",
     ("flat_support",), (("flat bench", "exact"), ("горизонтальная скамья", "stem"))),
    ("incline_bench", "Incline bench", "Наклонная скамья", "bench",
     ("incline_support", "back_support"),
     (("incline bench", "exact"), ("наклонная скамья", "stem"))),
    ("decline_bench", "Decline bench", "Скамья с отрицательным наклоном", "bench",
     ("decline_support",),
     (("decline bench", "exact"), ("скамья с отрицательным наклоном", "stem"))),
    ("adjustable_bench", "Adjustable bench", "Регулируемая скамья", "bench",
     ("flat_support", "incline_support", "decline_support", "adjustable_angle",
      "back_support"),
     (("adjustable bench", "exact"), ("регулируемая скамья", "stem"),
      ("скамья", "stem"), ("bench", "exact"))),
    ("hyperextension_bench", "Hyperextension bench", "Скамья для гиперэкстензии",
     "bench", ("back_support",),
     (("hyperextension bench", "exact"), ("roman chair", "exact"),
      ("гиперэкстензи", "stem"))),
    ("glute_ham_developer", "Glute-ham developer", "ГХР-станция", "bench",
     ("back_support",), (("glute ham raise", "exact"), ("гхр", "stem"))),
    ("preacher_bench", "Preacher bench", "Скамья Скотта", "bench",
     ("incline_support",), (("preacher bench", "exact"), ("скамья скотта", "stem"))),

    # --- Рамы, стойки, турники ---
    ("power_rack", "Power rack", "Силовая рама", "rack",
     ("safety_catch", "adjustable_height", "hanging_support"),
     (("power rack", "exact"), ("силовая рама", "stem"))),
    ("squat_rack", "Squat rack", "Стойки для приседа", "rack",
     ("safety_catch", "adjustable_height"),
     (("squat rack", "exact"), ("стойки для приседа", "stem"))),
    ("smith_machine", "Smith machine", "Машина Смита", "machine",
     ("fixed_path", "plate_loaded", "safety_catch"),
     (("smith machine", "exact"), ("машина смита", "stem"), ("смит", "stem"))),
    ("pull_up_bar", "Pull-up bar", "Турник", "bodyweight_support",
     ("hanging_support",),
     (("pull-up bar", "exact"), ("chin-up bar", "exact"), ("турник", "stem"),
      ("перекладин", "stem"))),
    ("parallel_bars", "Parallel bars", "Брусья", "bodyweight_support",
     ("hanging_support",), (("parallel bars", "exact"), ("брусья", "stem"))),
    ("dip_station", "Dip station", "Станция для отжиманий на брусьях",
     "bodyweight_support", ("hanging_support",),
     (("dip station", "exact"), ("dip bar", "exact"))),
    ("gymnastic_rings", "Gymnastic rings", "Гимнастические кольца",
     "bodyweight_support", ("hanging_support", "suspension_support",
                            "unstable_support"),
     (("rings", "exact"), ("кольца", "stem"))),
    ("suspension_trainer", "Suspension trainer", "Петли TRX", "bodyweight_support",
     ("suspension_support", "unstable_support", "adjustable_height"),
     (("trx", "exact"), ("suspension trainer", "exact"), ("suspended", "stem"),
      ("петл", "stem"))),

    # --- Блоки и тренажёры ---
    ("cable_machine", "Cable machine", "Блочный тренажёр", "cable",
     ("adjustable_resistance", "adjustable_height", "rope_attachment",
      "bar_attachment", "single_handle", "unilateral_capable"),
     (("cable", "exact"), ("cable machine", "exact"), ("блок", "stem"),
      ("трос", "stem"), ("кроссовер", "stem"))),
    ("lat_pulldown", "Lat pulldown machine", "Тяга верхнего блока", "cable",
     ("adjustable_resistance", "bar_attachment", "fixed_path", "back_support"),
     (("lat pulldown", "exact"), ("верхний блок", "stem"))),
    ("seated_row_machine", "Seated row machine", "Тяга нижнего блока", "cable",
     ("adjustable_resistance", "bar_attachment", "back_support"),
     (("seated row machine", "exact"), ("нижний блок", "stem"))),
    ("resistance_machine", "Resistance machine", "Силовой тренажёр", "machine",
     ("fixed_path", "adjustable_resistance"),
     (("machine", "exact"), ("тренажер", "stem"), ("тренажёр", "stem"),
      ("машин", "stem"))),
    ("chest_press_machine", "Chest press machine", "Тренажёр жима от груди",
     "machine", ("fixed_path", "adjustable_resistance", "back_support"),
     (("chest press machine", "exact"), ("жим от груди", "stem"))),
    ("shoulder_press_machine", "Shoulder press machine", "Тренажёр жима над головой",
     "machine",
     ("fixed_path", "adjustable_resistance", "back_support", "overhead_capable"),
     (("shoulder press machine", "exact"),)),
    ("pec_deck", "Pec deck", "Тренажёр «бабочка»", "machine",
     ("fixed_path", "adjustable_resistance", "back_support"),
     (("pec deck", "exact"), ("бабочка", "stem"))),
    ("leg_press", "Leg press", "Жим ногами", "machine",
     ("fixed_path", "adjustable_resistance", "plate_loaded", "back_support"),
     (("leg press", "exact"), ("жим ногами", "stem"))),
    ("hack_squat", "Hack squat machine", "Гакк-машина", "machine",
     ("fixed_path", "plate_loaded", "back_support"),
     (("hack squat", "exact"), ("гакк", "stem"))),
    ("leg_extension", "Leg extension machine", "Разгибание ног", "machine",
     ("fixed_path", "adjustable_resistance", "back_support"),
     (("leg extension", "exact"), ("разгибание ног", "stem"))),
    ("leg_curl", "Leg curl machine", "Сгибание ног", "machine",
     ("fixed_path", "adjustable_resistance"),
     (("leg curl", "exact"), ("сгибание ног", "stem"))),
    ("hip_abduction_machine", "Hip abduction machine", "Разведение ног", "machine",
     ("fixed_path", "adjustable_resistance", "back_support"),
     (("hip abduction machine", "exact"), ("разведение ног", "stem"))),
    ("hip_adduction_machine", "Hip adduction machine", "Сведение ног", "machine",
     ("fixed_path", "adjustable_resistance", "back_support"),
     (("hip adduction machine", "exact"), ("сведение ног", "stem"))),
    ("calf_machine", "Calf machine", "Тренажёр для икр", "machine",
     ("fixed_path", "adjustable_resistance"),
     (("calf machine", "exact"), ("тренажер для икр", "stem"))),

    # --- Кардио ---
    ("cardio_machine", "Cardio machine", "Кардиотренажёр", "cardio",
     ("continuous_cardio",),
     (("cardio machine", "exact"), ("кардиотренажер", "stem"),
      ("кардиотренажёр", "stem"))),
    ("treadmill", "Treadmill", "Беговая дорожка", "cardio",
     ("continuous_cardio",), (("treadmill", "exact"), ("дорожк", "stem"))),
    ("stationary_bike", "Stationary bike", "Велотренажёр", "cardio",
     ("continuous_cardio",),
     (("stationary bike", "exact"), ("велотренажер", "stem"),
      ("велотренажёр", "stem"))),
    ("elliptical", "Elliptical trainer", "Эллиптический тренажёр", "cardio",
     ("continuous_cardio",), (("elliptical", "exact"), ("эллипс", "stem"))),
    ("rowing_machine", "Rowing machine", "Гребной тренажёр", "cardio",
     ("continuous_cardio", "adjustable_resistance"),
     (("rowing machine", "exact"), ("гребн", "stem"))),
    ("stair_climber", "Stair climber", "Степпер", "cardio",
     ("continuous_cardio",), (("stair climber", "exact"), ("степпер", "stem"))),
    ("jump_rope", "Jump rope", "Скакалка", "cardio",
     ("continuous_cardio",), (("jump rope", "exact"), ("скакалк", "stem"))),

    # --- Резина, аксессуары, опоры ---
    ("resistance_band", "Resistance band", "Резиновая лента", "band",
     ("variable_resistance", "adjustable_resistance"),
     (("bands", "exact"), ("band", "exact"), ("резин", "stem"), ("лент", "stem"),
      ("эспандер", "stem"))),
    ("ab_wheel", "Ab wheel", "Ролик для пресса", "accessory",
     (), (("ab roller", "exact"), ("ролик для пресса", "stem"))),
    ("foam_roller", "Foam roller", "Массажный валик", "recovery",
     (), (("foam roll", "exact"), ("foam roller", "exact"), ("валик", "stem"))),
    ("exercise_mat", "Exercise mat", "Коврик", "support",
     ("ground_support",), (("mat", "exact"), ("коврик", "stem"))),
    ("floor", "Floor", "Пол", "support",
     ("ground_support",), (("floor", "exact"), ("пол", "exact"))),
    ("wall", "Wall", "Стена", "support",
     (), (("wall", "exact"), ("стена", "exact"))),
    ("chair", "Chair", "Стул", "support",
     ("elevated_platform",), (("chair", "exact"), ("стул", "stem"))),
    ("plyo_box", "Plyo box", "Плиометрическая тумба", "support",
     ("elevated_platform",), (("box", "exact"), ("тумб", "stem"))),
    ("step_platform", "Step platform", "Степ-платформа", "support",
     ("elevated_platform",),
     (("step platform", "exact"), ("степ-платформ", "stem"))),
    ("training_cone", "Training cone", "Конус", "accessory",
     (), (("cone", "exact"), ("конус", "stem"))),
    ("hurdle", "Hurdle", "Барьер", "accessory",
     (), (("hurdle", "exact"), ("барьер", "stem"))),
    ("balance_board", "Balance board", "Балансировочная платформа", "accessory",
     ("unstable_support",),
     (("balance board", "exact"), ("балансировочн", "stem"))),
    ("battle_ropes", "Battle ropes", "Канаты", "accessory",
     ("continuous_cardio",), (("battling ropes", "exact"), ("канаты", "stem"))),
    ("climbing_rope", "Climbing rope", "Канат для лазания", "accessory",
     ("hanging_support", "grip_intensive"), (("climbing rope", "exact"),)),
    ("wrist_roller", "Wrist roller", "Ролик для кистей", "accessory",
     ("grip_intensive",), (("wrist roller", "exact"),)),
    ("head_harness", "Head harness", "Головной ремень", "accessory",
     (), (("head harness", "exact"),)),
    ("heavy_bag", "Heavy bag", "Боксёрский мешок", "accessory",
     (), (("heavy bag", "exact"), ("боксерский мешок", "stem"))),
    ("weight_sled", "Weight sled", "Сани", "strongman",
     ("plate_loaded", "grip_intensive"),
     (("sled", "exact"), ("prowler", "exact"), ("сани", "stem"))),
    ("tire", "Tire", "Покрышка", "strongman",
     (), (("tire", "exact"), ("покрышк", "stem"))),
    ("bodyweight", "Bodyweight", "Собственный вес", "bodyweight",
     ("ground_support",),
     (("body only", "exact"), ("bodyweight", "exact"),
      ("собственный вес", "stem"))),
)

# Отношение «частный случай родового»: частное закрывает требование родового,
# обратное неверно. Заведено ровно там, где его требует источник каталога:
# значения `machine` (67 упражнений), `cable` (81) и кардио — родовые слова, и без
# связи человек с жимом ногами получал бы «не подходит» на упражнение «жим
# ногами».
#
# Взаимозаменяемость скамей здесь НЕ выражается: регулируемая скамья не является
# частным случаем горизонтальной, и их отношение уже описано возможностями
# (`flat_support`, `incline_support`). Смешивать два механизма значило бы иметь
# два ответа на один вопрос.
SPECIALIZATIONS: dict[str, str] = {
    # Силовые тренажёры → родовой «силовой тренажёр» (`machine` в каталоге).
    "chest_press_machine": "resistance_machine",
    "shoulder_press_machine": "resistance_machine",
    "pec_deck": "resistance_machine",
    "leg_press": "resistance_machine",
    "hack_squat": "resistance_machine",
    "leg_extension": "resistance_machine",
    "leg_curl": "resistance_machine",
    "calf_machine": "resistance_machine",
    "hip_abduction_machine": "resistance_machine",
    "hip_adduction_machine": "resistance_machine",
    "smith_machine": "resistance_machine",
    # Блочные станции → родовой «блочный тренажёр» (`cable` в каталоге).
    "lat_pulldown": "cable_machine",
    "seated_row_machine": "cable_machine",
    # Кардио → родовой «кардиотренажёр».
    "treadmill": "cardio_machine",
    "stationary_bike": "cardio_machine",
    "elliptical": "cardio_machine",
    "rowing_machine": "cardio_machine",
    "stair_climber": "cardio_machine",
}

# Источники записей, выведенных из каталога: при downgrade они удаляются, потому
# что без словаря ссылаются в пустоту.
DERIVED_SOURCES = ("catalog_import", "name_inference", "derived")


def upgrade() -> None:
    bind = op.get_bind()

    for capability_id, name, name_ru, description in CAPABILITIES:
        bind.execute(
            sa.text(
                "INSERT INTO equipment_capabilities "
                "(capability_id, name, name_ru, description, is_active) "
                "VALUES (:id, :name, :name_ru, :description, true) "
                "ON CONFLICT (capability_id) DO NOTHING"
            ),
            {
                "id": capability_id,
                "name": name,
                "name_ru": name_ru,
                "description": description,
            },
        )

    for equipment_id, name, name_ru, category, capabilities, aliases in EQUIPMENT:
        bind.execute(
            sa.text(
                "INSERT INTO equipment_items "
                "(equipment_id, name, name_ru, category, source, is_active) "
                "VALUES (:id, :name, :name_ru, :category, :source, true) "
                "ON CONFLICT (equipment_id) DO NOTHING"
            ),
            {
                "id": equipment_id,
                "name": name,
                "name_ru": name_ru,
                "category": category,
                "source": SEED_SOURCE,
            },
        )
        for capability_id in capabilities:
            bind.execute(
                sa.text(
                    "INSERT INTO equipment_item_capabilities "
                    "(equipment_id, capability_id) VALUES (:equipment_id, :capability_id) "
                    "ON CONFLICT (equipment_id, capability_id) DO NOTHING"
                ),
                {"equipment_id": equipment_id, "capability_id": capability_id},
            )
        for alias, match_mode in aliases:
            bind.execute(
                sa.text(
                    "INSERT INTO equipment_aliases "
                    "(equipment_id, alias, match_mode, source) "
                    "VALUES (:equipment_id, :alias, :match_mode, :source) "
                    "ON CONFLICT (alias, equipment_id) DO NOTHING"
                ),
                {
                    "equipment_id": equipment_id,
                    "alias": alias,
                    "match_mode": match_mode,
                    "source": SEED_SOURCE,
                },
            )

    # Специализации проставляются после вставки всех записей: родовая запись
    # может идти в списке позже частной, а FK ссылается на существующую строку.
    for special, generic in SPECIALIZATIONS.items():
        bind.execute(
            sa.text(
                "UPDATE equipment_items SET specializes = :generic "
                "WHERE equipment_id = :special AND specializes IS NULL"
            ),
            {"special": special, "generic": generic},
        )


def downgrade() -> None:
    bind = op.get_bind()

    seeded_items = [row[0] for row in EQUIPMENT]
    seeded_capabilities = [row[0] for row in CAPABILITIES]

    # Выведенное знание уходит первым: оно существует только вместе со словарём.
    bind.execute(
        sa.text("DELETE FROM exercise_alternatives WHERE source = ANY(:sources)"),
        {"sources": list(DERIVED_SOURCES)},
    )
    bind.execute(
        sa.text(
            "DELETE FROM exercise_equipment_requirements WHERE source = ANY(:sources)"
        ),
        {"sources": list(DERIVED_SOURCES)},
    )
    bind.execute(sa.text("DELETE FROM unmapped_equipment_values"))

    # Ссылки специализации снимаются до удаления записей: иначе частная запись
    # держала бы родовую внешним ключом. Два разных параметра вместо одного
    # повторённого: повторное использование именованного параметра в одном
    # выражении зависит от драйвера, а миграция обязана работать одинаково.
    bind.execute(
        sa.text(
            "UPDATE equipment_items SET specializes = NULL "
            "WHERE equipment_id = ANY(:targets) OR specializes = ANY(:parents)"
        ),
        {"targets": seeded_items, "parents": seeded_items},
    )

    # Синонимы и связи снимаются целиком по seed-строкам: они принадлежат словарю.
    bind.execute(
        sa.text(
            "DELETE FROM equipment_aliases "
            "WHERE source = :source AND equipment_id = ANY(:items)"
        ),
        {"source": SEED_SOURCE, "items": seeded_items},
    )
    bind.execute(
        sa.text(
            "DELETE FROM equipment_item_capabilities WHERE equipment_id = ANY(:items)"
        ),
        {"items": seeded_items},
    )

    # Оборудование удаляется только при отсутствии оставшихся ссылок: требование
    # или профиль, заведённые администратором, важнее полноты откатa.
    bind.execute(
        sa.text(
            "DELETE FROM equipment_items i "
            "WHERE i.source = :source AND i.equipment_id = ANY(:items) "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM exercise_equipment_requirements r"
            "  WHERE r.equipment_id = i.equipment_id"
            ") "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM equipment_profile_items p"
            "  WHERE p.equipment_id = i.equipment_id"
            ")"
        ),
        {"source": SEED_SOURCE, "items": seeded_items},
    )
    bind.execute(
        sa.text(
            "DELETE FROM equipment_capabilities c "
            "WHERE c.capability_id = ANY(:capabilities) "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM equipment_item_capabilities l"
            "  WHERE l.capability_id = c.capability_id"
            ") "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM exercise_equipment_requirements r"
            "  WHERE r.capability_id = c.capability_id"
            ")"
        ),
        {"capabilities": seeded_capabilities},
    )
