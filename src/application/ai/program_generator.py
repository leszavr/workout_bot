"""AIProgramGenerator: генерация программ тренировок через AI Gateway.

Реализует контракт ProgramGenerator (тот же интерфейс, что и
DeterministicProgramGenerator), поэтому замена генератора не требует
изменений pipeline, API, репозитория или валидаторов.

Pipeline:
    SafeExercisePool + AIProgramGenerationContext + Prompt
        → AIGateway → Raw AI Response
        → JSON extraction → Pydantic validation
        → ProgramValidator (schema + safe pool)
        → WorkoutProgram

Безопасность:
- AI получает только минимизированный DTO (без Telegram ID, имени и т.д.)
- AI получает только SafeExercisePool (не весь каталог)
- Результат проходит повторную валидацию существующими валидаторами
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from src.application.ai.program_context import (
    AIProgramGenerationContext,
    build_generation_context,
)
from src.application.programs.validator import ProgramValidator
from src.domain.ai.enums import AIResponseFormat, AITaskType
from src.domain.ai.errors import AIConfigurationError, AIError
from src.domain.ai.gateway import AIMessage, AIRequest, AIResponse
from src.domain.enums import GenerationSource, ProgramStatus
from src.domain.pools import SafeExercisePool
from src.domain.profile import FitnessProfile
from src.domain.program import WorkoutProgram
from src.errors import ProgramGenerationError

logger = logging.getLogger(__name__)

# Версия генератора для метаданных
AI_GENERATOR_VERSION = "ai-1.0.0"

# Максимальное количество repair-попыток (не бесконечный цикл!)
MAX_REPAIR_ATTEMPTS = 2

# Предельное время всей генерации, включая повторы внутри адаптера, перебор
# моделей и repair-запросы. Без общего бюджета таймауты перемножаются
# (попытки × таймаут × repair) и запрос «висит» минутами: администратор в
# интерфейсе видит зависание вместо понятного отказа.
DEFAULT_TOTAL_BUDGET_SECONDS = 240

# Путь к файлам промптов (fallback, если в БД нет)
PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "prompts" / "program_generator"


class PromptLoader:
    """Загрузка промптов из файлов или БД."""

    def __init__(self, prompt_repository=None):
        self._repo = prompt_repository

    async def load(
        self, task_type: AITaskType, version: int | None = None
    ) -> tuple[str, str, int]:
        """Возвращает (system_prompt, user_template, version)."""
        # Сначала пробуем БД
        if self._repo is not None:
            template = await self._repo.get(task_type, version)
            if template is not None and template.enabled:
                return template.system_prompt, template.user_template, template.version

        # Fallback на файлы
        prompt_dir = PROMPTS_DIR / f"v{version or 1}"
        system_file = prompt_dir / "system.txt"
        user_file = prompt_dir / "user_template.txt"

        if not system_file.exists() or not user_file.exists():
            raise AIConfigurationError(
                f"Промпты для задачи {task_type.value} v{version or 1} не найдены"
            )

        return (
            system_file.read_text(encoding="utf-8"),
            user_file.read_text(encoding="utf-8"),
            version or 1,
        )


class AIOutputParser:
    """Парсинг JSON из AI-ответа (безопасный, без eval)."""

    @staticmethod
    def extract_json(content: str) -> dict:
        """Извлекает JSON из ответа AI.

        Поддерживает:
        - Чистый JSON
        - JSON в markdown code block
        - JSON с текстом до/после
        """
        content = content.strip()

        # Пробуем распарсить напрямую
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Ищем JSON в markdown code block
        code_block_match = re.search(
            r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL
        )
        if code_block_match:
            try:
                return json.loads(code_block_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Ищем первый { и последний }
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                pass

        raise ProgramGenerationError("Не удалось извлечь JSON из ответа AI")


def _apply_catalog_sources(payload: dict, catalog_sources: dict[str, str]) -> None:
    """Подставляет `exercise_source` из каталога, не доверяя его модели.

    Ссылка на упражнение канонична как пара `external_id` + `source`. Модель
    источник не выбирает — она берёт упражнения из safe pool, где source уже
    известен, — но воспроизводит поле по примеру из промпта и искажает его
    (наблюдалось `workout` вместо `leszavr/workout`). Такая запись проходит
    схему, сохраняется, а потом каталог по ней не находится: пользователь
    получает программу без названий, техники и предупреждений.

    Чужой source не исправляется молча: неизвестный `external_id` остаётся как
    есть, и его поймает проверка каталога, а не эта подстановка.
    """
    days = payload.get("training_days")
    if not isinstance(days, list):
        return
    for day in days:
        if not isinstance(day, dict):
            continue
        exercises = day.get("exercises")
        if not isinstance(exercises, list):
            continue
        for exercise in exercises:
            if not isinstance(exercise, dict):
                continue
            source = catalog_sources.get(exercise.get("exercise_external_id"))
            if source is not None:
                exercise["exercise_source"] = source


class AIProgramGenerator:
    """Генератор программ через AI. Реализует контракт ProgramGenerator."""

    def __init__(
        self,
        *,
        gateway,  # AIGateway (тип не импортируем, чтобы не создавать цикл)
        prompt_loader: PromptLoader,
        validator: ProgramValidator | None = None,
        max_repair_attempts: int = MAX_REPAIR_ATTEMPTS,
        total_budget_seconds: int = DEFAULT_TOTAL_BUDGET_SECONDS,
    ) -> None:
        self._gateway = gateway
        self._prompts = prompt_loader
        self._validator = validator or ProgramValidator()
        self._max_repair_attempts = max_repair_attempts
        self._total_budget_seconds = total_budget_seconds

    async def generate(
        self,
        profile: FitnessProfile,
        pool: SafeExercisePool,
        prompt_version: int | None = None,
    ) -> WorkoutProgram:
        """Генерирует программу через AI с валидацией и repair."""
        deadline = time.monotonic() + self._total_budget_seconds

        # 1. Создаём минимизированный контекст (без персональных данных)
        context = build_generation_context(profile, pool)

        if context.safe_pool_size < 4:
            raise ProgramGenerationError(
                f"Безопасный пул слишком мал: {context.safe_pool_size} упражнений. "
                "AI-генерация невозможна."
            )

        # 2. Загружаем промпты
        system_prompt, user_template, actual_version = await self._prompts.load(
            AITaskType.WORKOUT_GENERATION, prompt_version
        )

        # 3. Формируем user prompt
        user_prompt = self._render_user_prompt(user_template, context)

        # 4. Вызываем AI Gateway
        request = AIRequest(
            task_type=AITaskType.WORKOUT_GENERATION,
            messages=[
                AIMessage(role="system", content=system_prompt),
                AIMessage(role="user", content=user_prompt),
            ],
            response_format=AIResponseFormat.JSON,
            profile_id=profile.profile_id,
        )

        response = await self._gateway.generate(request)

        # 5. Парсим и валидируем
        program = await self._parse_and_validate(
            response, context, pool, profile, actual_version, deadline
        )

        # 6. Заполняем AI-метаданные
        program.generation.source = GenerationSource.AI
        program.generation.generator_version = AI_GENERATOR_VERSION
        program.generation.provider = response.provider
        program.generation.model = response.model
        program.generation.prompt_version = actual_version
        program.generation.safe_pool_size = len(pool.allowed)

        return program

    async def _parse_and_validate(
        self,
        response: AIResponse,
        context: AIProgramGenerationContext,
        pool: SafeExercisePool,
        profile: FitnessProfile,
        prompt_version: int,
        deadline: float | None = None,
    ) -> WorkoutProgram:
        """Парсинг JSON + валидация + repair при ошибках."""
        parser = AIOutputParser()
        allowed_ids = pool.allowed_ids()
        allowed_sources = pool.allowed_sources()

        for attempt in range(self._max_repair_attempts + 1):
            try:
                # Парсим JSON
                payload = parser.extract_json(response.content)

                # Добавляем обязательные поля, которые AI не должен генерировать
                payload.setdefault("profile_id", profile.profile_id or "")
                payload.setdefault("schema_version", "1.0")
                payload.setdefault("version", 1)
                payload.setdefault("status", ProgramStatus.GENERATED.value)
                _apply_catalog_sources(payload, allowed_sources)

                # Валидируем схему
                program, schema_result = self._validator.validate_schema(payload)
                if program is None:
                    errors = "; ".join(i.message for i in schema_result.issues)
                    raise ProgramGenerationError(f"Schema validation failed: {errors}")

                # Валидируем против safe pool и каталога
                catalog_ids = allowed_ids  # AI может использовать только safe pool
                validation_result = self._validator.validate(
                    program, pool, profile, catalog_ids, allowed_sources
                )

                if validation_result.valid:
                    return program

                # Ошибки валидации
                errors = "; ".join(
                    f"{i.code}: {i.message}" for i in validation_result.issues
                )
                raise ProgramGenerationError(f"Program validation failed: {errors}")

            except ProgramGenerationError as exc:
                if attempt >= self._max_repair_attempts:
                    logger.error(
                        "AI-генерация не удалась после %d попыток: %s",
                        attempt + 1,
                        str(exc),
                    )
                    raise

                # Исправлять ответ имеет смысл только если на это осталось
                # время: иначе администратор ждёт заведомо безнадёжный запрос.
                if deadline is not None and time.monotonic() >= deadline:
                    logger.error(
                        "Время на AI-генерацию исчерпано, исправление не запрашивается: %s",
                        str(exc),
                    )
                    raise ProgramGenerationError(
                        f"Отведённое время на генерацию через ИИ исчерпано. "
                        f"Последняя ошибка: {exc}"
                    ) from exc

                # Repair attempt
                logger.warning(
                    "AI-вывод невалиден (попытка %d/%d), запрашиваем исправление: %s",
                    attempt + 1,
                    self._max_repair_attempts + 1,
                    str(exc),
                )
                response = await self._repair_request(response, str(exc), prompt_version)

        # Недостижимо, но для type checker
        raise ProgramGenerationError("AI-генерация не удалась")

    async def _repair_request(
        self, original_response: AIResponse, error_message: str, prompt_version: int
    ) -> AIResponse:
        """Запрос исправления с минимальным контекстом (не весь исходный промпт)."""
        repair_prompt = (
            "Your previous output did not satisfy the required schema or validation rules.\n\n"
            f"Validation errors:\n{error_message}\n\n"
            "Return ONLY the corrected JSON. No explanations, no markdown."
        )

        request = AIRequest(
            task_type=AITaskType.WORKOUT_GENERATION,
            messages=[
                AIMessage(role="user", content=repair_prompt),
            ],
            response_format=AIResponseFormat.JSON,
        )

        return await self._gateway.generate(request)

    @staticmethod
    def _render_user_prompt(
        template: str, context: AIProgramGenerationContext
    ) -> str:
        """Подставляет данные контекста в шаблон промпта."""
        # Формируем список упражнений
        exercises_lines = []
        for ex in context.safe_pool:
            muscles = ", ".join(ex.primary_muscles) if ex.primary_muscles else "N/A"
            equipment = ", ".join(ex.equipment) if ex.equipment else "bodyweight"
            name = ex.name_ru or ex.name
            exercises_lines.append(
                f"- {ex.external_id}: {name} (muscles: {muscles}, equipment: {equipment}, "
                f"type: {ex.exercise_type or 'N/A'}, difficulty: {ex.difficulty or 'N/A'})"
            )

        # Формируем предупреждения
        warnings_lines = []
        for ex_id, warns in context.pool_warnings.items():
            warnings_lines.append(f"- {ex_id}: {'; '.join(warns)}")

        # Ограничения
        restrictions = (
            ", ".join(r.value for r in context.movement_restrictions)
            if context.movement_restrictions
            else "нет"
        )

        return template.format(
            age_years=context.age_years or "не указан",
            sex=context.sex.value if context.sex else "не указан",
            height_cm=context.height_cm or "не указан",
            weight_kg=context.weight_kg or "не указан",
            primary_goal=context.primary_goal.value if context.primary_goal else "не указана",
            desired_result=context.desired_result or "не указан",
            experience_level=context.experience_level.value if context.experience_level else "не указан",
            sessions_per_week=context.sessions_per_week,
            session_duration_minutes=context.session_duration_minutes or "не указана",
            preferred_days=", ".join(context.preferred_days) or "любые",
            training_location=context.training_location.value if context.training_location else "не указано",
            available_equipment=", ".join(context.available_equipment) or "не указано",
            preferred_exercises=", ".join(context.preferred_exercises) or "нет",
            disliked_exercises=", ".join(context.disliked_exercises) or "нет",
            cardio_preference=context.cardio_preference.value if context.cardio_preference else "не указано",
            movement_restrictions=restrictions,
            safe_pool_exercises="\n".join(exercises_lines),
            pool_warnings="\n".join(warnings_lines) or "нет",
        )