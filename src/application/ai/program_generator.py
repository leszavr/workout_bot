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

Перебор моделей ведёт генератор, а не Gateway. Транспортный успех не равен
пригодному ответу: модель может ответить `200 OK` и при этом выдумать
`external_id`. Раньше цепочку кандидатов перебирал только `AIGateway.generate`
и только по `AIError`, поэтому все repair-попытки уходили в ту же модель, а
настроенные резервные не использовались никогда. Теперь порядок такой:

    модель 1 → невалидный ответ → repair → невалидный
        → модель 2 → ... → модель N → отказ (дальше решает оркестратор)

Repair получает контекст, достаточный для исправления: исходные правила и
схему (system-промпт), собственный предыдущий ответ, ошибки валидации и
перечень допустимых `external_id`. Пустой repair-запрос «исправь эти ошибки»
без документа и схемы модель не могла выполнить в принципе.

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
from dataclasses import asdict, dataclass
from typing import Awaitable, Callable

from src.application.ai.program_context import (
    AIProgramGenerationContext,
    build_generation_context,
)
from src.application.programs.validator import ProgramValidator
from src.domain.ai.enums import AIResponseFormat, AITaskType
from src.domain.ai.errors import AIConfigurationError, AIError
from src.domain.ai.gateway import AIMessage, AIRequest, AIResponse
from src.domain.enums import GenerationSource, ProgramStatus
from src.domain.generation import safe_error_message
from src.domain.pools import SafeExercisePool
from src.domain.profile import FitnessProfile
from src.domain.program import WorkoutProgram
from src.errors import ProgramGenerationError

logger = logging.getLogger(__name__)

# Версия генератора для метаданных
AI_GENERATOR_VERSION = "ai-1.0.0"

# Максимальное количество repair-попыток на ОДНУ модель (не бесконечный цикл!).
# Исчерпав их, генератор переходит к следующей модели цепочки, а не сдаётся.
MAX_REPAIR_ATTEMPTS = 2

# Предельное время всей генерации, включая повторы внутри адаптера, перебор
# моделей и repair-запросы. Без общего бюджета таймауты перемножаются
# (модели × попытки × таймаут) и запрос «висит» минутами: администратор в
# интерфейсе видит зависание вместо понятного отказа.
DEFAULT_TOTAL_BUDGET_SECONDS = 240

# Предыдущий ответ модели передаётся в repair целиком: без документа править
# нечего. Ограничение защищает от вырожденного случая, когда модель вместо
# программы вернула поток текста.
MAX_PREVIOUS_RESPONSE_CHARS = 20_000

# Исходы попытки одной модели. Значения попадают в журнал администратора,
# поэтому переименование — breaking change, а не косметика.
ATTEMPT_SUCCESS = "success"
ATTEMPT_INVALID_OUTPUT = "invalid_output"
ATTEMPT_PROVIDER_ERROR = "provider_error"
ATTEMPT_BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True)
class ModelAttempt:
    """Что произошло с одной моделью цепочки.

    Отвечает на эксплуатационные вопросы: какая модель вызывалась, прошёл ли
    первый ответ, запрашивалось ли исправление, почему модель была оставлена и
    выбиралась ли следующая. Персональных данных, промпта и ответа модели здесь
    нет — только технические поля.
    """

    priority: int
    is_primary: bool
    provider: str
    model_id: str
    model_pk: int | None
    initial_valid: bool
    repair_attempts: int
    outcome: str
    error_type: str | None = None
    detail: str | None = None


class _CandidateFailed(Exception):
    """Модель не дала пригодного результата.

    `fatal=True` означает, что перебор прекращается совсем (например, вышел
    общий бюджет времени): следующая модель заведомо не успеет ответить.
    """

    def __init__(
        self, attempt: ModelAttempt, error: Exception, *, fatal: bool = False
    ) -> None:
        super().__init__(str(error))
        self.attempt = attempt
        self.error = error
        self.fatal = fatal


class PromptLoader:
    """Загрузка инструкции из базы — единственного источника промптов.

    Файлового fallback нет намеренно. Пока текст жил и в базе, и в образе,
    источник истины был неопределён: файловую инструкцию нельзя было прочитать
    в админке, изменить или заменить, а `prompt_version = NULL` молча означал
    «взять файл». Базовая инструкция перенесена в базу миграцией `0009` и стала
    обычной версией, поэтому читать откуда-то ещё больше не нужно.

    Версию выбирает конфигурация задачи (`ai_task_configs.prompt_version`), и
    разрешение этой ссылки живёт здесь: «какую инструкцию использует задача» —
    часть вопроса «откуда берётся промпт», а не отдельная ответственность
    генератора. Отсутствие версии — ошибка конфигурации, а не повод молча взять
    другой текст: подмена инструкции незаметно меняет поведение генерации.
    """

    def __init__(self, prompt_repository, task_repository=None):
        self._repo = prompt_repository
        self._tasks = task_repository

    async def load(
        self, task_type: AITaskType, version: int | None = None
    ) -> tuple[str, str, int]:
        """Возвращает (system_prompt, user_template, version).

        `version=None` означает «взять версию из настроек задачи», а не
        «взять любую»: явный аргумент нужен только там, где версия уже известна.
        """
        if version is None:
            version = await self._task_prompt_version(task_type)
        if version is None:
            raise AIConfigurationError(
                f"Для задачи «{task_type.value}» не выбрана инструкция. "
                "Выберите версию инструкции в настройках задачи."
            )

        template = await self._repo.get(task_type, version)
        if template is None:
            raise AIConfigurationError(
                f"Инструкция №{version} для задачи «{task_type.value}» не найдена. "
                "Выберите существующую версию в настройках задачи."
            )
        if not template.enabled:
            raise AIConfigurationError(
                f"Инструкция №{version} выключена. Включите её или выберите "
                "другую версию в настройках задачи."
            )
        return template.system_prompt, template.user_template, template.version

    async def _task_prompt_version(self, task_type: AITaskType) -> int | None:
        if self._tasks is None:
            return None
        config = await self._tasks.get(task_type)
        return config.prompt_version if config else None


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


def _used_exercise_ids(content: str) -> list[str]:
    """`external_id`, которые модель употребила в ответе.

    Ответ может быть невалидным JSON, поэтому идентификаторы вынимаются
    регулярным выражением: repair-запрос должен называть выдуманные
    идентификаторы даже тогда, когда структура ответа сломана.
    """
    return re.findall(r'"exercise_external_id"\s*:\s*"([^"]{1,200})"', content)


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
        attempt_recorder: Callable[[list[ModelAttempt]], Awaitable[None]] | None = None,
    ) -> None:
        self._gateway = gateway
        self._prompts = prompt_loader
        self._validator = validator or ProgramValidator()
        self._max_repair_attempts = max_repair_attempts
        self._total_budget_seconds = total_budget_seconds
        self._attempt_recorder = attempt_recorder

    async def generate(
        self,
        profile: FitnessProfile,
        pool: SafeExercisePool,
        prompt_version: int | None = None,
    ) -> WorkoutProgram:
        """Генерирует программу: цепочка моделей × (ответ + repair) + валидация."""
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

        # 4. Готовим цепочку кандидатов задачи (primary → fallback)
        request = AIRequest(
            task_type=AITaskType.WORKOUT_GENERATION,
            messages=[
                AIMessage(role="system", content=system_prompt),
                AIMessage(role="user", content=user_prompt),
            ],
            response_format=AIResponseFormat.JSON,
            profile_id=profile.profile_id,
        )
        chain = await self._gateway.prepare(request)

        # Модель видела ровно эти упражнения (контекст ограничен), поэтому
        # repair предлагает выбирать из того же набора, а не из всего пула.
        allowed_prompt_ids = [e.external_id for e in context.safe_pool]

        attempts: list[ModelAttempt] = []
        last_error: Exception | None = None

        for candidate in chain.candidates:
            if attempts and time.monotonic() >= deadline:
                # Следующая модель заведомо не успеет: честнее отказать сразу.
                last_error = ProgramGenerationError(
                    "Отведённое время на генерацию через ИИ исчерпано до перехода "
                    "к следующей модели"
                )
                attempts.append(
                    self._attempt(
                        candidate,
                        initial_valid=False,
                        repair_attempts=0,
                        outcome=ATTEMPT_BUDGET_EXHAUSTED,
                        error=last_error,
                    )
                )
                break

            try:
                program, response, attempt = await self._run_candidate(
                    candidate,
                    chain,
                    request,
                    system_prompt=system_prompt,
                    pool=pool,
                    profile=profile,
                    allowed_prompt_ids=allowed_prompt_ids,
                    deadline=deadline,
                )
            except _CandidateFailed as failed:
                attempts.append(failed.attempt)
                last_error = failed.error
                logger.warning(
                    "event=ai_model_abandoned",
                    extra={
                        "model_pk": failed.attempt.model_pk,
                        "priority": failed.attempt.priority,
                        "outcome": failed.attempt.outcome,
                        "repair_attempts": failed.attempt.repair_attempts,
                        "error_type": failed.attempt.error_type,
                    },
                )
                if failed.fatal:
                    break
                continue

            attempts.append(attempt)
            await self._record_attempts(attempts)

            # 5. Заполняем AI-метаданные
            program.generation.source = GenerationSource.AI
            program.generation.generator_version = AI_GENERATOR_VERSION
            program.generation.provider = response.provider
            program.generation.model = response.model
            program.generation.prompt_version = actual_version
            program.generation.safe_pool_size = len(pool.allowed)
            logger.info(
                "event=ai_generation_succeeded",
                extra={
                    "model_pk": attempt.model_pk,
                    "priority": attempt.priority,
                    "initial_valid": attempt.initial_valid,
                    "repair_attempts": attempt.repair_attempts,
                    "models_tried": len(attempts),
                },
            )
            return program

        await self._record_attempts(attempts)
        logger.error(
            "event=ai_generation_exhausted",
            extra={
                "models_tried": len(attempts),
                "outcomes": [a.outcome for a in attempts],
            },
        )
        if last_error is not None:
            # Транспортный отказ (`AIError`) сохраняем как есть: по его типу
            # оркестратор различает таймаут, лимит и сбой соединения.
            raise last_error
        raise ProgramGenerationError("AI-генерация не удалась: нет кандидатов")

    # --- Одна модель цепочки ------------------------------------------------------

    async def _run_candidate(
        self,
        candidate,
        chain,
        request: AIRequest,
        *,
        system_prompt: str,
        pool: SafeExercisePool,
        profile: FitnessProfile,
        allowed_prompt_ids: list[str],
        deadline: float,
    ) -> tuple[WorkoutProgram, AIResponse, ModelAttempt]:
        """Ответ модели + repair-попытки. Отказ — это `_CandidateFailed`."""
        repair_attempts = 0

        try:
            response = await self._gateway.generate_once(candidate, request, chain)
        except AIError as exc:
            raise _CandidateFailed(
                self._attempt(
                    candidate,
                    initial_valid=False,
                    repair_attempts=0,
                    outcome=ATTEMPT_PROVIDER_ERROR,
                    error=exc,
                ),
                exc,
            ) from exc

        for attempt_index in range(self._max_repair_attempts + 1):
            try:
                program = self._parse_and_validate(response, pool, profile)
            except ProgramGenerationError as exc:
                if attempt_index >= self._max_repair_attempts:
                    raise _CandidateFailed(
                        self._attempt(
                            candidate,
                            initial_valid=False,
                            repair_attempts=repair_attempts,
                            outcome=ATTEMPT_INVALID_OUTPUT,
                            error=exc,
                        ),
                        exc,
                    ) from exc

                # Исправлять ответ имеет смысл только если на это осталось
                # время: иначе администратор ждёт заведомо безнадёжный запрос.
                if time.monotonic() >= deadline:
                    budget_error = ProgramGenerationError(
                        f"Отведённое время на генерацию через ИИ исчерпано. "
                        f"Последняя ошибка: {exc}"
                    )
                    raise _CandidateFailed(
                        self._attempt(
                            candidate,
                            initial_valid=False,
                            repair_attempts=repair_attempts,
                            outcome=ATTEMPT_BUDGET_EXHAUSTED,
                            error=budget_error,
                        ),
                        budget_error,
                        fatal=True,
                    ) from exc

                repair_attempts += 1
                logger.warning(
                    "event=ai_output_rejected",
                    extra={
                        "model_pk": candidate.model.id,
                        "priority": candidate.priority,
                        "repair_attempt": repair_attempts,
                        "max_repair_attempts": self._max_repair_attempts,
                    },
                )
                repair_request = self._repair_request(
                    original=request,
                    system_prompt=system_prompt,
                    previous_content=response.content,
                    error_message=str(exc),
                    allowed_prompt_ids=allowed_prompt_ids,
                )
                try:
                    response = await self._gateway.generate_once(
                        candidate, repair_request, chain
                    )
                except AIError as provider_exc:
                    raise _CandidateFailed(
                        self._attempt(
                            candidate,
                            initial_valid=False,
                            repair_attempts=repair_attempts,
                            outcome=ATTEMPT_PROVIDER_ERROR,
                            error=provider_exc,
                        ),
                        provider_exc,
                    ) from provider_exc
                continue

            return (
                program,
                response,
                self._attempt(
                    candidate,
                    initial_valid=attempt_index == 0,
                    repair_attempts=repair_attempts,
                    outcome=ATTEMPT_SUCCESS,
                    error=None,
                ),
            )

        # Недостижимо: цикл либо возвращает результат, либо бросает.
        raise ProgramGenerationError("AI-генерация не удалась")

    def _parse_and_validate(
        self,
        response: AIResponse,
        pool: SafeExercisePool,
        profile: FitnessProfile,
    ) -> WorkoutProgram:
        """Парсинг JSON + строгая валидация одного ответа модели.

        Валидатор не ослабляется ради прохождения AI-вывода: выдуманный
        `external_id` остаётся ошибкой, и именно она уходит в repair-запрос.
        """
        allowed_sources = pool.allowed_sources()
        payload = AIOutputParser.extract_json(response.content)

        # Добавляем обязательные поля, которые AI не должен генерировать
        payload.setdefault("profile_id", profile.profile_id or "")
        payload.setdefault("schema_version", "1.0")
        payload.setdefault("version", 1)
        payload.setdefault("status", ProgramStatus.GENERATED.value)
        _apply_catalog_sources(payload, allowed_sources)

        program, schema_result = self._validator.validate_schema(payload)
        if program is None:
            errors = "; ".join(i.message for i in schema_result.issues)
            raise ProgramGenerationError(f"Schema validation failed: {errors}")

        # AI может использовать только safe pool: каталог для него — этот пул.
        validation_result = self._validator.validate(
            program, pool, profile, pool.allowed_ids(), allowed_sources
        )
        if validation_result.valid:
            return program

        errors = "; ".join(
            f"{i.code}: {i.message}" for i in validation_result.issues
        )
        raise ProgramGenerationError(f"Program validation failed: {errors}")

    # --- Repair -------------------------------------------------------------------

    def _repair_request(
        self,
        *,
        original: AIRequest,
        system_prompt: str,
        previous_content: str,
        error_message: str,
        allowed_prompt_ids: list[str],
    ) -> AIRequest:
        """Repair-запрос с минимальным достаточным контекстом.

        Достаточный контекст — это правила и схема (исходный system-промпт),
        собственный предыдущий ответ модели, ошибки валидации и перечень
        допустимых `external_id`. Полный исходный user-промпт не дублируется:
        он в разы больше, а для точечного исправления не нужен — программа уже
        составлена, менять надо только то, что не прошло проверку.
        """
        previous = previous_content.strip()[:MAX_PREVIOUS_RESPONSE_CHARS]
        invented = [
            external_id
            for external_id in dict.fromkeys(_used_exercise_ids(previous))
            if external_id not in set(allowed_prompt_ids)
        ]
        invented_block = (
            "Идентификаторы, которых нет в разрешённом наборе (заменить обязательно): "
            + ", ".join(invented[:20])
            + "\n\n"
            if invented
            else ""
        )

        instructions = (
            "Твой предыдущий ответ не прошёл проверку.\n\n"
            "Исправь ИМЕННО его: сохрани всё, что прошло проверку, и измени только то, "
            "что вызвало ошибки. Не составляй программу заново.\n\n"
            f"Ошибки проверки:\n{error_message}\n\n"
            f"{invented_block}"
            "Разрешённые exercise_external_id (используй ТОЛЬКО их, придумывать "
            f"новые запрещено):\n{', '.join(allowed_prompt_ids)}\n\n"
            "Верни ТОЛЬКО исправленный JSON по той же схеме, без пояснений и без "
            "markdown."
        )

        return AIRequest(
            task_type=original.task_type,
            messages=[
                # Схема и правила — в исходном system-промпте: без него модель
                # не знает, какую структуру от неё ждут.
                AIMessage(role="system", content=system_prompt),
                AIMessage(role="assistant", content=previous),
                AIMessage(role="user", content=instructions),
            ],
            response_format=AIResponseFormat.JSON,
            profile_id=original.profile_id,
        )

    # --- Телеметрия ----------------------------------------------------------------

    @staticmethod
    def _attempt(
        candidate,
        *,
        initial_valid: bool,
        repair_attempts: int,
        outcome: str,
        error: Exception | None,
    ) -> ModelAttempt:
        return ModelAttempt(
            priority=candidate.priority,
            is_primary=candidate.is_primary,
            provider=candidate.provider.slug,
            model_id=candidate.model.model_id,
            model_pk=candidate.model.id,
            initial_valid=initial_valid,
            repair_attempts=repair_attempts,
            outcome=outcome,
            error_type=error.__class__.__name__ if error is not None else None,
            detail=safe_error_message(error)[:300] if error is not None else None,
        )

    async def _record_attempts(self, attempts: list[ModelAttempt]) -> None:
        """Пишет историю попыток в журнал администратора.

        Журнал не должен ломать генерацию: сбой записи только логируется.
        """
        if self._attempt_recorder is None or not attempts:
            return
        try:
            await self._attempt_recorder(list(attempts))
        except Exception:  # noqa: BLE001 — телеметрия не критична для генерации
            logger.exception("Не удалось записать историю попыток AI-моделей")

    @staticmethod
    def attempts_metadata(attempts: list[ModelAttempt]) -> list[dict]:
        """Технические поля попыток для журнала. Без промптов и ответов."""
        return [asdict(a) for a in attempts]

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
