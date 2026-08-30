"""Восемь сценариев пользователя: разные цели, ограничения и оборудование.

Набор подобран так, чтобы каждая анкета проверяла свою группу правил, а не была
вариацией одной и той же. Ключевое требование — проверить, соблюдается ли прямой
отказ пользователя от упражнений, поэтому нежелательные упражнения есть почти в
каждом сценарии и сформулированы так, как их пишет человек: «выпады», а не
«Barbell Lunge».

`expectations` описывает не желаемую программу, а проверяемые условия. Их
проверяет `quality.py`; сам сценарий ничего не утверждает.
"""
from __future__ import annotations

from scripts.qa_harness.user_simulator import ScriptedUser

# Общая часть: вопросы, которые не участвуют в различении сценариев. Собраны в
# одном месте, чтобы в самих сценариях было видно только то, чем они отличаются.
_BASE_ANSWERS: dict[str, object] = {
    "q03_sex": "sex_male",
    "q10_timeframe": "timeframe_3_6_months",
    "q23_preferred_time": "time_pref_evening",
    "q28_medical_clearance": "med_clear_no_recommendations",
}


def _user(
    *,
    name: str,
    user_id: int,
    answers: dict[str, object],
    expectations: dict[str, object],
) -> ScriptedUser:
    return ScriptedUser(
        name=name,
        telegram_user_id=user_id,
        answers={**_BASE_ANSWERS, **answers},
        expectations=expectations,
    )


SCENARIOS: list[ScriptedUser] = [
    # 1. Базовый зал: полный набор оборудования, ограничений нет, отказ от двух
    #    групп упражнений. Проверяет, что отказ соблюдается в самых свободных
    #    условиях — когда пул максимально велик и «пойти навстречу» проще всего.
    _user(
        name="Зал без ограничений",
        user_id=990101,
        answers={
            "q01_name": "Андрей",
            "q02_age": "31",
            "q04_height": "182",
            "q05_weight": "84",
            "q07_primary_goal": "goal_muscle_gain",
            "q09_desired_result": "Набрать 5 кг мышечной массы",
            "q11_experience": "exp_over_1_year",
            "q16_location": "loc_gym",
            "q20_sessions_per_week": "sessions_4",
            "q21_preferred_days": ["mon", "tue", "thu", "fri"],
            "q22_session_duration": "duration_75",
            "q24_has_limitations": "limit_no",
            "q27_movements_to_avoid": "нет",
            "q30_disliked_exercises": "выпады, бег",
            "q33_cardio_preference": "cardio_okay",
        },
        expectations={
            "sessions_per_week": 4,
            "unwanted": ["выпады", "бег"],
            "equipment_profile": "gym_full",
        },
    ),
    # 2. Дом, только собственный вес. Проверяет оборудование: программа не должна
    #    содержать штангу и тренажёры, которых у человека нет.
    _user(
        name="Дом, только своё тело",
        user_id=990102,
        answers={
            "q01_name": "Мария",
            "q02_age": "27",
            "q03_sex": "sex_female",
            "q04_height": "165",
            "q05_weight": "58",
            "q07_primary_goal": "goal_health_fitness",
            "q09_desired_result": "Привести себя в форму",
            "q11_experience": "exp_never",
            "q16_location": "loc_home",
            "q18b_home_equipment": "коврик",
            "q20_sessions_per_week": "sessions_3",
            "q21_preferred_days": ["mon", "wed", "fri"],
            "q22_session_duration": "duration_45",
            "q24_has_limitations": "limit_no",
            "q27_movements_to_avoid": "нет",
            "q30_disliked_exercises": "прыжки",
            "q33_cardio_preference": "cardio_okay",
        },
        expectations={
            "sessions_per_week": 3,
            "unwanted": ["прыжки"],
            "equipment_profile": "bodyweight_only",
            "experience": "never",
        },
    ),
    # 3. Ограничение по спине. Проверяет safety-контур: осевая нагрузка должна
    #    уйти из программы, а не остаться с пометкой.
    _user(
        name="Ограничение по спине",
        user_id=990103,
        answers={
            "q01_name": "Сергей",
            "q02_age": "45",
            "q04_height": "178",
            "q05_weight": "92",
            "q07_primary_goal": "goal_weight_loss",
            "q09_desired_result": "Сбросить вес и не навредить спине",
            "q11_experience": "exp_long_break",
            "q16_location": "loc_gym",
            "q20_sessions_per_week": "sessions_3",
            "q21_preferred_days": ["tue", "thu", "sat"],
            "q22_session_duration": "duration_60",
            "q24_has_limitations": "limit_yes",
            "q25_limitation_categories": "проблемы с позвоночником, грыжа",
            "q27_movements_to_avoid": "осевая нагрузка на позвоночник",
            "q30_disliked_exercises": "становая тяга",
            "q33_cardio_preference": "cardio_walking_only",
        },
        expectations={
            "sessions_per_week": 3,
            "unwanted": ["становая тяга"],
            "restrictions_expected": True,
        },
    ),
    # 4. Ограничение по колену + отказ от нескольких групп. Самый узкий пул:
    #    проверяет, что при сильном сужении система не начинает «добирать»
    #    запрещённое, а честно отказывает.
    _user(
        name="Колено, узкий пул",
        user_id=990104,
        answers={
            "q01_name": "Ольга",
            "q02_age": "52",
            "q03_sex": "sex_female",
            "q04_height": "160",
            "q05_weight": "70",
            "q07_primary_goal": "goal_health_fitness",
            "q09_desired_result": "Укрепить мышцы без нагрузки на колени",
            "q11_experience": "exp_under_3_months",
            "q16_location": "loc_home",
            "q18b_home_equipment": "гантели, резинки",
            "q20_sessions_per_week": "sessions_2",
            "q21_preferred_days": ["wed", "sun"],
            "q22_session_duration": "duration_45",
            "q24_has_limitations": "limit_yes",
            "q25_limitation_categories": "боль в колене",
            "q27_movements_to_avoid": "ударные нагрузки, прыжки, глубокие приседания",
            "q30_disliked_exercises": "приседания, выпады, прыжки",
            "q33_cardio_preference": "cardio_dislike",
        },
        expectations={
            "sessions_per_week": 2,
            "unwanted": ["приседания", "выпады", "прыжки"],
            "restrictions_expected": True,
            "equipment_profile": "home_dumbbells_bands",
        },
    ),
    # 5. Кардио исключено полностью. Отдельный сценарий, потому что это не
    #    «нежелательное упражнение», а собственное правило фильтра.
    _user(
        name="Кардио исключено",
        user_id=990105,
        answers={
            "q01_name": "Дмитрий",
            "q02_age": "35",
            "q04_height": "175",
            "q05_weight": "78",
            "q07_primary_goal": "goal_strength",
            "q09_desired_result": "Увеличить силовые показатели",
            "q11_experience": "exp_over_1_year",
            "q16_location": "loc_gym",
            "q20_sessions_per_week": "sessions_4",
            "q21_preferred_days": ["mon", "tue", "thu", "sat"],
            "q22_session_duration": "duration_90",
            "q24_has_limitations": "limit_no",
            "q27_movements_to_avoid": "нет",
            "q30_disliked_exercises": "нет",
            "q33_cardio_preference": "cardio_exclude",
        },
        expectations={
            "sessions_per_week": 4,
            "cardio_excluded": True,
            # «Нет» в ответе про нежелательные упражнения не должно ничего
            # исключать: проверяется, что слово-пустышка не считается запросом.
            "unwanted": [],
        },
    ),
    # 6. Новичок, минимум занятий. Проверяет соответствие сложности опыту.
    _user(
        name="Новичок, два занятия",
        user_id=990106,
        answers={
            "q01_name": "Иван",
            "q02_age": "23",
            "q04_height": "185",
            "q05_weight": "70",
            "q07_primary_goal": "goal_muscle_gain",
            "q09_desired_result": "Начать тренироваться и не бросить",
            "q11_experience": "exp_never",
            "q16_location": "loc_gym",
            "q20_sessions_per_week": "sessions_2",
            "q21_preferred_days": ["tue", "fri"],
            "q22_session_duration": "duration_45",
            "q24_has_limitations": "limit_no",
            "q27_movements_to_avoid": "нет",
            "q30_disliked_exercises": "берпи",
            "q33_cardio_preference": "cardio_love",
        },
        expectations={
            "sessions_per_week": 2,
            "experience": "never",
            # «Берпи» в каталоге нет: проверяется, что ненайденный запрос не
            # ломает генерацию и не исключает лишнего.
            "unwanted": ["берпи"],
        },
    ),
    # 7. Максимум занятий, смешанное место. Проверяет верхнюю границу числа дней.
    _user(
        name="Шесть занятий, дом и зал",
        user_id=990107,
        answers={
            "q01_name": "Павел",
            "q02_age": "29",
            "q04_height": "180",
            "q05_weight": "80",
            "q07_primary_goal": "goal_endurance",
            "q09_desired_result": "Подготовиться к полумарафону",
            "q11_experience": "exp_over_1_year",
            "q16_location": "loc_both",
            "q20_sessions_per_week": "sessions_6",
            "q21_preferred_days": ["mon", "tue", "wed", "thu", "fri", "sat"],
            "q22_session_duration": "duration_60",
            "q24_has_limitations": "limit_no",
            "q27_movements_to_avoid": "нет",
            "q30_disliked_exercises": "жим лёжа",
            "q33_cardio_preference": "cardio_love",
        },
        expectations={
            "sessions_per_week": 6,
            "unwanted": ["жим лёжа"],
        },
    ),
    # 8. Медицинские рекомендации + возврат к тренировкам. Проверяет, что
    #    рекомендации врача попадают в замечания пула, а не игнорируются.
    _user(
        name="Рекомендации врача",
        user_id=990108,
        answers={
            "q01_name": "Наталья",
            "q02_age": "48",
            "q03_sex": "sex_female",
            "q04_height": "168",
            "q05_weight": "75",
            "q07_primary_goal": "goal_return_to_training",
            "q09_desired_result": "Вернуться к тренировкам после перерыва",
            "q11_experience": "exp_long_break",
            "q16_location": "loc_gym",
            "q20_sessions_per_week": "sessions_3",
            "q21_preferred_days": ["mon", "wed", "sat"],
            "q22_session_duration": "duration_60",
            "q24_has_limitations": "limit_yes",
            "q25_limitation_categories": "повышенное давление",
            "q27_movements_to_avoid": "упражнения с задержкой дыхания",
            "q28_medical_clearance": "med_clear_restricted",
            "q28_doctor_recommendations": "избегать высокоинтенсивных нагрузок",
            "q30_disliked_exercises": "планка",
            "q33_cardio_preference": "cardio_walking_only",
        },
        expectations={
            "sessions_per_week": 3,
            "unwanted": ["планка"],
            "restrictions_expected": True,
            "review_notes_expected": True,
        },
    ),
]

# --- Дополнительные сценарии для наполнения аналитики ---------------------------
#
# Первые восемь сценариев отвечают на вопрос «соблюдаются ли требования
# пользователя». Эти двенадцать добавлены под другую задачу: наполнить разрезы
# аналитического дашборда. Дашборд показывает не только успехи, но и распределение
# по моделям, версиям инструкций, состояниям валидации и причинам fallback — на
# однотипных генерациях эти разрезы остаются пустыми, и проверить их нечем.
#
# Профили различаются по цели, полу, возрасту, месту занятий, числу дней и
# длительности: аналитика группирует генерации по параметрам программы, и выборка
# из клонов одного профиля дала бы формально заполненный, но бессодержательный
# дашборд.

EXTRA_SCENARIOS: list[ScriptedUser] = [
    _user(
        name="Женщина, сила в зале",
        user_id=990109,
        answers={
            "q01_name": "Екатерина",
            "q02_age": "34",
            "q03_sex": "sex_female",
            "q04_height": "170",
            "q05_weight": "63",
            "q07_primary_goal": "goal_strength",
            "q09_desired_result": "Подтягиваться 10 раз",
            "q11_experience": "exp_3_12_months",
            "q16_location": "loc_gym",
            "q20_sessions_per_week": "sessions_3",
            "q21_preferred_days": ["mon", "wed", "fri"],
            "q22_session_duration": "duration_60",
            "q24_has_limitations": "limit_no",
            "q27_movements_to_avoid": "нет",
            "q30_disliked_exercises": "нет",
            "q33_cardio_preference": "cardio_okay",
        },
        expectations={"sessions_per_week": 3},
    ),
    _user(
        name="Похудение, зал, 5 дней",
        user_id=990110,
        answers={
            "q01_name": "Виктор",
            "q02_age": "41",
            "q04_height": "176",
            "q05_weight": "98",
            "q07_primary_goal": "goal_weight_loss",
            "q09_desired_result": "Сбросить 12 кг",
            "q11_experience": "exp_under_3_months",
            "q16_location": "loc_gym",
            "q20_sessions_per_week": "sessions_5",
            "q21_preferred_days": ["mon", "tue", "wed", "thu", "fri"],
            "q22_session_duration": "duration_60",
            "q24_has_limitations": "limit_no",
            "q27_movements_to_avoid": "нет",
            "q30_disliked_exercises": "прыжки, берпи",
            "q33_cardio_preference": "cardio_love",
        },
        expectations={"sessions_per_week": 5, "unwanted": ["прыжки", "берпи"]},
    ),
    _user(
        name="Дом, гантели, масса",
        user_id=990111,
        answers={
            "q01_name": "Артём",
            "q02_age": "26",
            "q04_height": "179",
            "q05_weight": "68",
            "q07_primary_goal": "goal_muscle_gain",
            "q09_desired_result": "Набрать массу дома",
            "q11_experience": "exp_3_12_months",
            "q16_location": "loc_home",
            "q18b_home_equipment": "разборные гантели, турник, скамья",
            "q20_sessions_per_week": "sessions_4",
            "q21_preferred_days": ["mon", "tue", "thu", "sat"],
            "q22_session_duration": "duration_75",
            "q24_has_limitations": "limit_no",
            "q27_movements_to_avoid": "нет",
            "q30_disliked_exercises": "нет",
            "q33_cardio_preference": "cardio_dislike",
        },
        expectations={"sessions_per_week": 4, "equipment_profile": "home_dumbbells"},
    ),
    _user(
        name="Старший возраст, здоровье",
        user_id=990112,
        answers={
            "q01_name": "Людмила",
            "q02_age": "61",
            "q03_sex": "sex_female",
            "q04_height": "162",
            "q05_weight": "72",
            "q07_primary_goal": "goal_health_fitness",
            "q09_desired_result": "Сохранить подвижность",
            "q11_experience": "exp_never",
            "q16_location": "loc_home",
            "q18b_home_equipment": "коврик, резинки",
            "q20_sessions_per_week": "sessions_3",
            "q21_preferred_days": ["tue", "thu", "sun"],
            "q22_session_duration": "duration_45",
            "q24_has_limitations": "limit_yes",
            "q25_limitation_categories": "гипертония",
            "q27_movements_to_avoid": "резкие наклоны, задержка дыхания",
            "q28_medical_clearance": "med_clear_restricted",
            "q28_doctor_recommendations": "без высокоинтенсивных нагрузок",
            "q30_disliked_exercises": "нет",
            "q33_cardio_preference": "cardio_walking_only",
        },
        expectations={"sessions_per_week": 3, "restrictions_expected": True},
    ),
]

EXTRA_SCENARIOS += [
    _user(
        name="Выносливость, велосипед",
        user_id=990113,
        answers={
            "q01_name": "Роман",
            "q02_age": "33",
            "q04_height": "181",
            "q05_weight": "74",
            "q07_primary_goal": "goal_endurance",
            "q09_desired_result": "Проехать 100 км",
            "q11_experience": "exp_over_1_year",
            "q16_location": "loc_both",
            "q20_sessions_per_week": "sessions_5",
            "q21_preferred_days": ["mon", "tue", "thu", "fri", "sun"],
            "q22_session_duration": "duration_90",
            "q24_has_limitations": "limit_no",
            "q27_movements_to_avoid": "нет",
            "q30_disliked_exercises": "жим лёжа",
            "q33_cardio_preference": "cardio_love",
        },
        expectations={"sessions_per_week": 5, "unwanted": ["жим лёжа"]},
    ),
    _user(
        name="Возврат после травмы плеча",
        user_id=990114,
        answers={
            "q01_name": "Денис",
            "q02_age": "38",
            "q04_height": "184",
            "q05_weight": "88",
            "q07_primary_goal": "goal_return_to_training",
            "q09_desired_result": "Вернуться в форму без боли в плече",
            "q11_experience": "exp_long_break",
            "q16_location": "loc_gym",
            "q20_sessions_per_week": "sessions_3",
            "q21_preferred_days": ["mon", "wed", "fri"],
            "q22_session_duration": "duration_60",
            "q24_has_limitations": "limit_yes",
            "q25_limitation_categories": "травма плеча",
            "q27_movements_to_avoid": "жимы над головой",
            "q30_disliked_exercises": "подтягивания",
            "q33_cardio_preference": "cardio_okay",
        },
        expectations={
            "sessions_per_week": 3,
            "unwanted": ["подтягивания"],
            "restrictions_expected": True,
        },
    ),
    _user(
        name="Минимум времени, два дня",
        user_id=990115,
        answers={
            "q01_name": "Игорь",
            "q02_age": "45",
            "q04_height": "173",
            "q05_weight": "81",
            "q07_primary_goal": "goal_health_fitness",
            "q09_desired_result": "Держать себя в форме при плотном графике",
            "q11_experience": "exp_under_3_months",
            "q16_location": "loc_gym",
            "q20_sessions_per_week": "sessions_2",
            "q21_preferred_days": ["wed", "sat"],
            "q22_session_duration": "duration_45",
            "q24_has_limitations": "limit_no",
            "q27_movements_to_avoid": "нет",
            "q30_disliked_exercises": "нет",
            "q33_cardio_preference": "cardio_okay",
            "q35_schedule_constraints": "только раннее утро",
        },
        expectations={"sessions_per_week": 2},
    ),
    _user(
        name="Семь дней, максимум",
        user_id=990116,
        answers={
            "q01_name": "Максим",
            "q02_age": "24",
            "q04_height": "187",
            "q05_weight": "79",
            "q07_primary_goal": "goal_muscle_gain",
            "q09_desired_result": "Максимальный набор массы",
            "q11_experience": "exp_over_1_year",
            "q16_location": "loc_gym",
            "q20_sessions_per_week": "sessions_6",
            "q21_preferred_days": ["mon", "tue", "wed", "thu", "fri", "sat"],
            "q22_session_duration": "duration_120",
            "q24_has_limitations": "limit_no",
            "q27_movements_to_avoid": "нет",
            "q30_disliked_exercises": "нет",
            "q33_cardio_preference": "cardio_dislike",
        },
        # Шесть дней — проверка того же дефекта, что нашёл прошлый прогон:
        # алгоритм обрезал число дней до пяти.
        expectations={"sessions_per_week": 6},
    ),
]

EXTRA_SCENARIOS += [
    _user(
        name="Кардио исключено, дом",
        user_id=990117,
        answers={
            "q01_name": "Светлана",
            "q02_age": "29",
            "q03_sex": "sex_female",
            "q04_height": "167",
            "q05_weight": "55",
            "q07_primary_goal": "goal_muscle_gain",
            "q09_desired_result": "Округлить формы",
            "q11_experience": "exp_3_12_months",
            "q16_location": "loc_home",
            "q18b_home_equipment": "гантели 8 кг, резинки, фитбол",
            "q20_sessions_per_week": "sessions_3",
            "q21_preferred_days": ["mon", "wed", "sat"],
            "q22_session_duration": "duration_60",
            "q24_has_limitations": "limit_no",
            "q27_movements_to_avoid": "нет",
            "q30_disliked_exercises": "нет",
            "q33_cardio_preference": "cardio_exclude",
        },
        expectations={"sessions_per_week": 3, "cardio_excluded": True},
    ),
    _user(
        name="Ограничение по запястьям",
        user_id=990118,
        answers={
            "q01_name": "Павел",
            "q02_age": "36",
            "q04_height": "175",
            "q05_weight": "83",
            "q07_primary_goal": "goal_strength",
            "q09_desired_result": "Стать сильнее без боли в кистях",
            "q11_experience": "exp_3_12_months",
            "q16_location": "loc_gym",
            "q20_sessions_per_week": "sessions_4",
            "q21_preferred_days": ["tue", "wed", "fri", "sun"],
            "q22_session_duration": "duration_75",
            "q24_has_limitations": "limit_yes",
            "q25_limitation_categories": "боль в запястьях",
            "q27_movements_to_avoid": "упор на кисти",
            "q30_disliked_exercises": "отжимания",
            "q33_cardio_preference": "cardio_okay",
        },
        expectations={
            "sessions_per_week": 4,
            "unwanted": ["отжимания"],
            "restrictions_expected": True,
        },
    ),
    _user(
        name="Новичок без времени в анкете",
        user_id=990119,
        answers={
            "q01_name": "Алина",
            "q02_age": "22",
            "q03_sex": "sex_female",
            "q04_height": "164",
            "q05_weight": "51",
            "q07_primary_goal": "goal_health_fitness",
            "q09_desired_result": "Начать заниматься регулярно",
            "q11_experience": "exp_never",
            "q16_location": "loc_home",
            "q18b_home_equipment": "коврик",
            "q20_sessions_per_week": "sessions_3",
            "q21_preferred_days": ["mon", "wed", "fri"],
            # Длительность не указана: проверяет значение по умолчанию (60 минут).
            "q24_has_limitations": "limit_no",
            "q27_movements_to_avoid": "нет",
            "q30_disliked_exercises": "нет",
            "q33_cardio_preference": "cardio_okay",
        },
        expectations={"sessions_per_week": 3, "duration_default": True},
    ),
    _user(
        name="Смешанное место, похудение",
        user_id=990120,
        answers={
            "q01_name": "Олег",
            "q02_age": "50",
            "q04_height": "178",
            "q05_weight": "104",
            "q07_primary_goal": "goal_weight_loss",
            "q09_desired_result": "Снизить вес и давление",
            "q11_experience": "exp_long_break",
            "q16_location": "loc_both",
            "q18b_home_equipment": "беговая дорожка",
            "q20_sessions_per_week": "sessions_4",
            "q21_preferred_days": ["mon", "tue", "thu", "sat"],
            "q22_session_duration": "duration_60",
            "q24_has_limitations": "limit_yes",
            "q25_limitation_categories": "повышенное давление, лишний вес",
            "q27_movements_to_avoid": "прыжки, ударные нагрузки",
            "q30_disliked_exercises": "бег",
            "q33_cardio_preference": "cardio_walking_only",
        },
        expectations={
            "sessions_per_week": 4,
            "unwanted": ["бег"],
            "restrictions_expected": True,
        },
    ),
]


# Диапазон telegram_user_id тестовых пользователей: по нему очищаются данные
# прогона, не затрагивая реальных пользователей.
QA_USER_ID_MIN = 990100
QA_USER_ID_MAX = 990199
