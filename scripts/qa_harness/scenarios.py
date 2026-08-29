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

# Диапазон telegram_user_id тестовых пользователей: по нему очищаются данные
# прогона, не затрагивая реальных пользователей.
QA_USER_ID_MIN = 990100
QA_USER_ID_MAX = 990199
