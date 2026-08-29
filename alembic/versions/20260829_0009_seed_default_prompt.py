"""Инструкция для ИИ переносится в базу: единственный источник промптов

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-29

Пока инструкция существовала в двух местах — в `prompt_templates` и в файлах
образа (`prompts/program_generator/v1`) — источник истины был неопределён:
файловый промпт нельзя было ни прочитать в админке, ни изменить, ни удалить, а
`prompt_version = NULL` молча означал «взять файл». Из-за этого базовая
инструкция была недоступна как референс для новых версий и не могла быть
заменена, даже если созданная позже оказывалась лучше.

Миграция переносит текст файлового промпта в базу как обычную версию — без
флага «системная» и без защиты от изменения. С этого момента базовая инструкция
ничем не отличается от созданных вручную: её можно править, копировать и
удалять (пока она не выбрана в настройках задачи).

Backfill: задачи с `prompt_version = NULL` начинают ссылаться на созданную
версию. Иначе после удаления файлов у них не осталось бы инструкции вовсе.

Идемпотентность: если у задачи уже есть версии в базе, текст добавляется
следующим свободным номером и ничего не перезаписывает.

Downgrade возвращает `prompt_version = NULL` и удаляет добавленную версию.
Правки, внесённые администратором в эту версию, при откате теряются: сохранять
их некуда — до 0009 инструкции в базе не было ни одной.
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TASK_TYPE = "workout_generation"

# Имя-маркер: по нему downgrade находит именно добавленную здесь версию и не
# трогает инструкции, созданные администратором.
SEED_NAME = "Базовая инструкция"

# Текст перенесён из prompts/program_generator/v1 дословно. Миграция обязана
# быть самодостаточной: файлы удаляются в этом же изменении, и читать их из
# миграции было бы нельзя — при повторном применении на другой копии кода
# результат зависел бы от содержимого рабочего каталога.
SEED_SYSTEM_PROMPT = """Ты — эксперт по составлению персональных программ тренировок.

Твоя задача: создать структурированную программу тренировок на основе предоставленных данных о клиенте и доступных упражнений.

КРИТИЧЕСКИЕ ПРАВИЛА:
1. Используй ТОЛЬКО упражнения из предоставленного списка safe_pool. Каждое упражнение имеет уникальный external_id.
2. НЕ придумывай упражнения, которых нет в списке. external_id копируй из списка символ в символ: не переводи, не сокращай, не дополняй пояснениями вроде «(Generic)». Если подходящего упражнения в списке нет, возьми ближайшее из списка — выдуманный external_id делает всю программу недействительной.
3. НЕ используй упражнения, помеченные предупреждениями (pool_warnings), без необходимости.
4. Учитывай ограничения движений (movement_restrictions) — не назначай упражнения, нарушающие эти ограничения.
5. Программа должна быть безопасной и соответствовать уровню подготовки клиента.

ФОРМАТ ОТВЕТА:
Верни ТОЛЬКО валидный JSON без дополнительных пояснений. Структура:

{
  "title": "Название программы (до 200 символов)",
  "description": "Краткое описание программы (до 1000 символов)",
  "duration_weeks": число от 1 до 52,
  "training_days_per_week": число от 1 до 7,
  "training_days": [
    {
      "day_number": 1,
      "title": "Название дня",
      "focus": "Фокус тренировки (например: legs, push, pull, full_body)",
      "exercises": [
        {
          "exercise_external_id": "ID упражнения из safe_pool",
          "order": 1,
          "sets": число от 1 до 10,
          "repetitions_min": число от 1 до 200,
          "repetitions_max": число от 1 до 200,
          "rest_seconds": число от 0 до 600,
          "intensity": "опционально, например RPE 7",
          "notes": "опционально, примечания"
        }
      ]
    }
  ],
  "progression": {
    "description": "Описание прогрессии нагрузки",
    "weekly_increase_percent": число от 0 до 20
  },
  "safety_notes": ["список важных замечаний по безопасности"]
}

ТРЕБОВАНИЯ К ПРОГРАММЕ:
- Количество training_days должно равняться training_days_per_week
- day_number должен быть последовательным: 1, 2, 3...
- В каждом дне от 1 до 15 упражнений
- repetitions_max >= repetitions_min
- Для новичков: 2-3 подхода, 10-15 повторений, больше отдыха
- Для опытных: 3-5 подходов, диапазон повторений зависит от цели
- Не дублируй упражнения внутри одного дня
- Не добавляй поле exercise_source: источник упражнения известен системе и
  подставляется автоматически по external_id"""

SEED_USER_TEMPLATE = """Создай программу тренировок на основе следующих данных:

## Данные клиента
- Возраст: {age_years}
- Пол: {sex}
- Рост: {height_cm} см
- Вес: {weight_kg} кг
- Основная цель: {primary_goal}
- Желаемый результат: {desired_result}
- Уровень опыта: {experience_level}
- Тренировок в неделю: {sessions_per_week}
- Длительность тренировки: {session_duration_minutes} минут
- Предпочитаемые дни: {preferred_days}
- Место тренировок: {training_location}
- Доступное оборудование: {available_equipment}
- Любимые упражнения: {preferred_exercises}
- Нелюбимые упражнения: {disliked_exercises}
- Отношение к кардио: {cardio_preference}

## Ограничения движений
{movement_restrictions}

## Доступные упражнения (safe_pool)
Используй ТОЛЬКО эти упражнения (external_id):

{safe_pool_exercises}

## Предупреждения по упражнениям
{pool_warnings}

Создай программу на {sessions_per_week} тренировок в неделю. Верни только JSON."""


def upgrade() -> None:
    bind = op.get_bind()

    next_version = (
        bind.execute(
            sa.text(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM prompt_templates "
                "WHERE task_type = :task"
            ),
            {"task": TASK_TYPE},
        ).scalar_one()
    )

    bind.execute(
        sa.text(
            "INSERT INTO prompt_templates "
            "(task_type, version, name, system_prompt, user_template, enabled) "
            "VALUES (:task, :version, :name, :system_prompt, :user_template, true)"
        ),
        {
            "task": TASK_TYPE,
            "version": next_version,
            "name": SEED_NAME,
            "system_prompt": SEED_SYSTEM_PROMPT,
            "user_template": SEED_USER_TEMPLATE,
        },
    )

    # Задача без выбранной версии раньше работала на файловом промпте. Файлов
    # больше нет, поэтому ссылка ставится явно.
    bind.execute(
        sa.text(
            "UPDATE ai_task_configs SET prompt_version = :version "
            "WHERE task_type = :task AND prompt_version IS NULL"
        ),
        {"task": TASK_TYPE, "version": next_version},
    )


def downgrade() -> None:
    bind = op.get_bind()

    versions = [
        row[0]
        for row in bind.execute(
            sa.text(
                "SELECT version FROM prompt_templates "
                "WHERE task_type = :task AND name = :name"
            ),
            {"task": TASK_TYPE, "name": SEED_NAME},
        )
    ]
    if not versions:
        return

    # Ссылку снимаем раньше удаления: иначе задача осталась бы с номером
    # инструкции, которой уже нет.
    bind.execute(
        sa.text(
            "UPDATE ai_task_configs SET prompt_version = NULL "
            "WHERE task_type = :task AND prompt_version = ANY(:versions)"
        ),
        {"task": TASK_TYPE, "versions": versions},
    )
    bind.execute(
        sa.text(
            "DELETE FROM prompt_templates WHERE task_type = :task AND name = :name"
        ),
        {"task": TASK_TYPE, "name": SEED_NAME},
    )
