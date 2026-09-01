"""Unit-тесты AIProgramGenerator (этап 4).

Тесты используют FakeAIGateway — реальные AI API не вызываются.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.application.ai.program_context import (
    AIProgramGenerationContext,
    ExerciseBrief,
    build_generation_context,
)
from src.application.ai.program_generator import (
    AIOutputParser,
    AIProgramGenerator,
    PromptLoader,
)
from src.application.ai.selection import ModelCandidate
from src.application.programs.validator import ProgramValidator
from src.domain.ai.config import (
    AIEndpoint,
    AIModel,
    AIProvider,
    AITaskConfig,
    PromptTemplate,
)
from src.domain.ai.enums import AITaskType
from src.domain.ai.errors import AIConfigurationError, AIError, AIProviderError
from src.domain.ai.gateway import AIResponse
from src.domain.enums import (
    ExperienceLevel,
    GenerationSource,
    MovementRestriction,
    PrimaryGoal,
    Sex,
)
from src.domain.exercise import Exercise
from src.domain.pools import SafeExercisePool
from src.domain.profile import FitnessProfile
from src.errors import ProgramGenerationError


# --- Фикстуры -------------------------------------------------------------------


def make_exercise(external_id: str, name: str = "Test Exercise") -> Exercise:
    return Exercise(
        external_id=external_id,
        name=name,
        primary_muscles=["chest"],
        equipment=["barbell"],
        exercise_type="strength",
        difficulty="beginner",
    )


def make_safe_pool(count: int = 5) -> SafeExercisePool:
    exercises = [make_exercise(f"ex_{i}", f"Exercise {i}") for i in range(count)]
    return SafeExercisePool(
        profile_id="test-profile",
        allowed=exercises,
        active_restrictions=[MovementRestriction.AVOID_HIGH_IMPACT],
    )


def make_profile() -> FitnessProfile:
    return FitnessProfile(
        profile_id="test-profile",
        client={"age_years": 30, "sex": Sex.MALE, "height_cm": 180, "weight_kg": 80},
        goals={"primary": PrimaryGoal.MUSCLE_GAIN, "desired_result": "Набрать массу"},
        training_background={"experience_level": ExperienceLevel.THREE_TWELVE_MONTHS},
        training_plan_preferences={"sessions_per_week": 3},
    )


def make_valid_program_json(pool: SafeExercisePool) -> str:
    """Валидный JSON программы, использующий упражнения из пула."""
    exercises = pool.allowed[:3]
    program = {
        "title": "AI Test Program",
        "description": "Test description",
        "duration_weeks": 8,
        "training_days_per_week": 1,
        "training_days": [
            {
                "day_number": 1,
                "title": "Full Body",
                "focus": "full_body",
                "exercises": [
                    {
                        "exercise_external_id": ex.external_id,
                        "exercise_source": ex.source,
                        "order": i + 1,
                        "sets": 3,
                        "repetitions_min": 10,
                        "repetitions_max": 12,
                        "rest_seconds": 60,
                    }
                    for i, ex in enumerate(exercises)
                ],
            }
        ],
        "progression": {"description": "Increase weight", "weekly_increase_percent": 2.5},
        "safety_notes": ["Test note"],
    }
    return json.dumps(program)


def make_candidate(priority: int, model_id: str) -> ModelCandidate:
    """Кандидат цепочки задачи (модель + подключение + сервис)."""
    return ModelCandidate(
        model=AIModel(
            id=priority, endpoint_id=10, model_id=model_id, display_name=model_id
        ),
        endpoint=AIEndpoint(
            id=10, provider_id=1, name="E", base_url="https://x.example/v1"
        ),
        provider=AIProvider(id=1, name="P", slug="p1"),
        priority=priority,
        is_primary=priority == 1,
    )


class _PreparedChain:
    """Минимальный аналог PreparedChain: генератору нужны только кандидаты."""

    def __init__(self, candidates: list[ModelCandidate]) -> None:
        self.config = AITaskConfig(
            id=100, task_type=AITaskType.WORKOUT_GENERATION, enabled=True
        )
        self.candidates = candidates
        self.adapter_request = None


class FakeAIGateway:
    """Фейковый AI Gateway для тестов.

    Повторяет двухшаговый контракт реального gateway: цепочку кандидатов
    отдаёт `prepare`, а каждый вызов выполняет `generate_once`. Перебор моделей
    ведёт сам генератор, поэтому сценарии «модель A испорчена, модель B
    отвечает» проверяются без AI-инфраструктуры.
    """

    def __init__(
        self,
        responses: list[str] | None = None,
        *,
        candidates: list[ModelCandidate] | None = None,
        per_model: dict[str, list[str | Exception]] | None = None,
    ):
        self.responses = responses or []
        self.per_model = per_model
        self.calls: list = []
        # (model_id, request) — по ним видно, какой модели что отправляли.
        self.model_calls: list[tuple[str, object]] = []
        self._call_index = 0
        self._candidates = candidates or [make_candidate(1, "test-model")]

    async def prepare(self, request):
        return _PreparedChain(self._candidates)

    async def generate_once(self, candidate, request, chain):
        self.calls.append(request)
        self.model_calls.append((candidate.model.model_id, request))

        if self.per_model is not None:
            queue = self.per_model.get(candidate.model.model_id, [])
            outcome = queue.pop(0) if queue else "{}"
        elif self._call_index < len(self.responses):
            outcome = self.responses[self._call_index]
            self._call_index += 1
        else:
            outcome = "{}"

        if isinstance(outcome, Exception):
            raise outcome
        return AIResponse(
            content=outcome,
            model=candidate.model.model_id,
            provider=candidate.provider.slug,
            endpoint=candidate.endpoint.name,
            input_tokens=100,
            output_tokens=200,
            total_tokens=300,
            latency_ms=500,
        )


class FakePromptLoader:
    """Загрузчик-заглушка: тестам генератора нужен только текст инструкции.

    Сам `PromptLoader` (разрешение версии и отказ, когда инструкции нет)
    проверяется отдельно в `TestPromptLoader` на фейковых репозиториях.
    """

    SYSTEM_PROMPT = "You are a fitness expert. Follow the required JSON schema."

    async def load(self, task_type, version=None):
        return (
            self.SYSTEM_PROMPT,
            "Create program for {sessions_per_week} days. Pool: {safe_pool_exercises}",
            version or 1,
        )


# --- Тесты AIOutputParser -------------------------------------------------------


class TestAIOutputParser:
    def test_extract_json_pure(self):
        """Чистый JSON парсится."""
        parser = AIOutputParser()
        result = parser.extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_extract_json_markdown_block(self):
        """JSON в markdown code block парсится."""
        parser = AIOutputParser()
        content = '```json\n{"key": "value"}\n```'
        result = parser.extract_json(content)
        assert result == {"key": "value"}

    def test_extract_json_with_text(self):
        """JSON с текстом до/после парсится."""
        parser = AIOutputParser()
        content = 'Here is the result: {"key": "value"} Hope this helps!'
        result = parser.extract_json(content)
        assert result == {"key": "value"}

    def test_extract_json_invalid(self):
        """Невалидный JSON бросает ошибку."""
        parser = AIOutputParser()
        with pytest.raises(ProgramGenerationError, match="Не удалось извлечь JSON"):
            parser.extract_json("not json at all")


# --- Тесты build_generation_context ---------------------------------------------


class TestBuildContext:
    def test_excludes_personal_data(self):
        """Контекст не содержит персональных идентификаторов."""
        profile = make_profile()
        profile.source.telegram_username = "secret_user"
        profile.source.bot_user_id = "12345"
        profile.client.name = "Secret Name"

        pool = make_safe_pool()
        context = build_generation_context(profile, pool)

        # Проверяем отсутствие персональных данных
        context_dict = context.model_dump()
        assert "telegram_username" not in str(context_dict)
        assert "bot_user_id" not in str(context_dict)
        assert "Secret Name" not in str(context_dict)
        assert "name" not in context_dict  # имя не передаётся

    def test_includes_safe_pool(self):
        """Контекст содержит упражнения из safe pool."""
        profile = make_profile()
        pool = make_safe_pool(5)
        context = build_generation_context(profile, pool)

        assert context.safe_pool_size == 5
        assert len(context.safe_pool) == 5
        assert context.safe_pool[0].external_id == "ex_0"

    def test_includes_restrictions(self):
        """Контекст содержит ограничения движений."""
        profile = make_profile()
        pool = make_safe_pool()
        context = build_generation_context(profile, pool)

        assert MovementRestriction.AVOID_HIGH_IMPACT in context.movement_restrictions


# --- Тесты AIProgramGenerator ---------------------------------------------------


class TestAIProgramGenerator:
    @pytest.mark.asyncio
    async def test_valid_output(self):
        """Валидный JSON → программа с AI-метаданными."""
        pool = make_safe_pool()
        profile = make_profile()
        valid_json = make_valid_program_json(pool)

        gateway = FakeAIGateway([valid_json])
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
        )

        program = await generator.generate(profile, pool)

        assert program.generation.source == GenerationSource.AI
        assert program.generation.provider == "p1"
        assert program.generation.model == "test-model"
        assert program.generation.prompt_version == 1
        assert program.title == "AI Test Program"

    @pytest.mark.asyncio
    async def test_invalid_json_repair_success(self):
        """Невалидный JSON → repair → успех."""
        pool = make_safe_pool()
        profile = make_profile()
        valid_json = make_valid_program_json(pool)

        # Первый ответ невалидный, второй (repair) валидный
        gateway = FakeAIGateway(["invalid json", valid_json])
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=1,
        )

        program = await generator.generate(profile, pool)
        assert program.title == "AI Test Program"
        assert len(gateway.calls) == 2  # первоначальный + repair

    @pytest.mark.asyncio
    async def test_invalid_json_repair_exhausted(self):
        """Все repair попытки исчерпаны → ошибка."""
        pool = make_safe_pool()
        profile = make_profile()

        gateway = FakeAIGateway(["invalid", "still invalid", "nope"])
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=2,
        )

        with pytest.raises(ProgramGenerationError):
            await generator.generate(profile, pool)

    @pytest.mark.asyncio
    async def test_repair_is_skipped_when_time_budget_exhausted(self):
        """Исчерпанный бюджет времени прекращает попытки исправления.

        Без бюджета таймауты складывались (попытки × таймаут × repair) и запрос
        «висел» минутами вместо понятного отказа.

        Ожидается `AIError`, а не `ProgramGenerationError`: истёкшее время модели
        — транспортный отказ, и вызывающая сторона обрабатывает его тем же
        переходом к следующему кандидату, что обрыв соединения.
        """
        pool = make_safe_pool()
        profile = make_profile()

        gateway = FakeAIGateway(["invalid json", make_valid_program_json(pool)])
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=2,
            total_budget_seconds=0,  # время вышло ещё до первой попытки
        )

        with pytest.raises(AIError, match="время"):
            await generator.generate(profile, pool)
        # Запросов не было вовсе: отказ до обращения к модели.
        assert gateway.calls == []

    @pytest.mark.asyncio
    async def test_exercise_outside_safe_pool_rejected(self):
        """Упражнение вне safe pool → отклонение."""
        pool = make_safe_pool()
        profile = make_profile()

        # Программа с упражнением, которого нет в пуле
        invalid_program = {
            "title": "Bad Program",
            "duration_weeks": 8,
            "training_days_per_week": 1,
            "training_days": [
                {
                    "day_number": 1,
                    "title": "Day 1",
                    "focus": "full_body",
                    "exercises": [
                        {
                            "exercise_external_id": "unknown_exercise",
                            "order": 1,
                            "sets": 3,
                            "repetitions_min": 10,
                            "repetitions_max": 12,
                            "rest_seconds": 60,
                        }
                    ],
                }
            ],
        }

        gateway = FakeAIGateway([json.dumps(invalid_program)])
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=0,
        )

        with pytest.raises(ProgramGenerationError, match="validation failed"):
            await generator.generate(profile, pool)

    @pytest.mark.asyncio
    async def test_small_pool_rejected(self):
        """Слишком малый safe pool → ошибка до вызова AI."""
        pool = make_safe_pool(2)  # меньше MIN_POOL_SIZE
        profile = make_profile()

        gateway = FakeAIGateway([])
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
        )

        with pytest.raises(ProgramGenerationError, match="слишком мал"):
            await generator.generate(profile, pool)

        # AI не вызывался
        assert len(gateway.calls) == 0

    @pytest.mark.asyncio
    async def test_duplicate_exercises_rejected(self):
        """Дубликаты упражнений в дне → отклонение."""
        pool = make_safe_pool()
        profile = make_profile()
        ex = pool.allowed[0]

        invalid_program = {
            "title": "Duplicate Program",
            "duration_weeks": 8,
            "training_days_per_week": 1,
            "training_days": [
                {
                    "day_number": 1,
                    "title": "Day 1",
                    "focus": "full_body",
                    "exercises": [
                        {
                            "exercise_external_id": ex.external_id,
                            "order": 1,
                            "sets": 3,
                            "repetitions_min": 10,
                            "repetitions_max": 12,
                            "rest_seconds": 60,
                        },
                        {
                            "exercise_external_id": ex.external_id,  # дубликат
                            "order": 2,
                            "sets": 3,
                            "repetitions_min": 10,
                            "repetitions_max": 12,
                            "rest_seconds": 60,
                        },
                    ],
                }
            ],
        }

        gateway = FakeAIGateway([json.dumps(invalid_program)])
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=0,
        )

        with pytest.raises(ProgramGenerationError, match="validation failed"):
            await generator.generate(profile, pool)

    @pytest.mark.asyncio
    async def test_schema_validation_error(self):
        """Нарушение схемы (невалидные поля) → ошибка."""
        pool = make_safe_pool()
        profile = make_profile()

        invalid_program = {
            "title": "Bad Schema",
            "duration_weeks": 100,  # > 52
            "training_days_per_week": 1,
            "training_days": [
                {
                    "day_number": 1,
                    "title": "Day 1",
                    "focus": "full_body",
                    "exercises": [
                        {
                            "exercise_external_id": pool.allowed[0].external_id,
                            "order": 1,
                            "sets": 3,
                            "repetitions_min": 10,
                            "repetitions_max": 12,
                            "rest_seconds": 60,
                        }
                    ],
                }
            ],
        }

        gateway = FakeAIGateway([json.dumps(invalid_program)])
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=0,
        )

        with pytest.raises(ProgramGenerationError, match="Schema validation failed"):
            await generator.generate(profile, pool)

class TestExerciseSourceIsTakenFromCatalog:
    """`exercise_source` не входит в зону ответственности модели.

    Ссылка на упражнение канонична как пара `external_id` + `source`. Модель
    источник не выбирает — упражнения приходят из safe pool, где source уже
    известен, — но воспроизводит поле по примеру из промпта и искажает его.
    Наблюдалось `workout` вместо `leszavr/workout`: схему такая запись
    проходит, сохраняется, а затем каталог по ней не находится и пользователь
    получает карточки без названий, техники и предупреждений.
    """

    @pytest.mark.asyncio
    async def test_foreign_source_is_replaced_by_catalog(self):
        pool = make_safe_pool()
        profile = make_profile()
        payload = json.loads(make_valid_program_json(pool))
        for exercise in payload["training_days"][0]["exercises"]:
            exercise["exercise_source"] = "workout"

        generator = AIProgramGenerator(
            gateway=FakeAIGateway([json.dumps(payload)]),
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=0,
        )
        program = await generator.generate(profile, pool)

        sources = {
            ex.exercise_source
            for day in program.training_days
            for ex in day.exercises
        }
        assert sources == {"leszavr/workout"}

    @pytest.mark.asyncio
    async def test_missing_source_is_filled_from_catalog(self):
        """Промпт больше не требует поле: его подставляет сервер."""
        pool = make_safe_pool()
        profile = make_profile()
        payload = json.loads(make_valid_program_json(pool))
        for exercise in payload["training_days"][0]["exercises"]:
            del exercise["exercise_source"]

        generator = AIProgramGenerator(
            gateway=FakeAIGateway([json.dumps(payload)]),
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=0,
        )
        program = await generator.generate(profile, pool)

        for day in program.training_days:
            for exercise in day.exercises:
                assert exercise.exercise_source == "leszavr/workout"

    @pytest.mark.asyncio
    async def test_unknown_exercise_keeps_its_source_and_fails_catalog_check(self):
        """Подстановка не маскирует выдуманное упражнение."""
        pool = make_safe_pool()
        profile = make_profile()
        payload = json.loads(make_valid_program_json(pool))
        payload["training_days"][0]["exercises"][0]["exercise_external_id"] = "GHOST"

        generator = AIProgramGenerator(
            gateway=FakeAIGateway([json.dumps(payload)]),
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=0,
        )

        with pytest.raises(ProgramGenerationError, match="exercise_not_found"):
            await generator.generate(profile, pool)


# --- Контекст repair-запроса -----------------------------------------------------


class TestRepairContext:
    """Repair должен иметь чем и по каким правилам исправлять.

    Full STAGING E2E показал обратное: repair уходил одним `user`-сообщением
    «исправь эти ошибки» — без схемы, без предыдущего ответа и без перечня
    допустимых упражнений. Вход сжимался с ~6700 до ~120 токенов, и модель
    возвращала фрагмент без обязательных полей, то есть деградировала вместо
    исправления.
    """

    @staticmethod
    def _program_with_invented_exercise(pool: SafeExercisePool) -> str:
        payload = json.loads(make_valid_program_json(pool))
        payload["training_days"][0]["exercises"][0][
            "exercise_external_id"
        ] = "Cable_Lat_Pulldown_(Generic)"
        return json.dumps(payload)

    @pytest.mark.asyncio
    async def test_repair_receives_schema_previous_answer_and_allowed_ids(self):
        pool = make_safe_pool()
        profile = make_profile()
        invalid = self._program_with_invented_exercise(pool)

        gateway = FakeAIGateway([invalid, make_valid_program_json(pool)])
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=1,
        )

        await generator.generate(profile, pool)

        repair = gateway.calls[1]
        roles = [m.role for m in repair.messages]
        assert roles == ["system", "assistant", "user"]

        # Правила и схема — те же, что в исходном запросе.
        assert repair.messages[0].content == FakePromptLoader.SYSTEM_PROMPT
        # Предыдущий ответ модели передан целиком: править нужно именно его.
        assert repair.messages[1].content == invalid

        instructions = repair.messages[2].content
        # Ошибка валидации названа.
        assert "exercise_not_found" in instructions
        # Выдуманный идентификатор назван прямо.
        assert "Cable_Lat_Pulldown_(Generic)" in instructions
        # Разрешённый набор перечислен полностью.
        for exercise in pool.allowed:
            assert exercise.external_id in instructions
        # Запрет на изобретение идентификаторов сформулирован явно.
        assert "придумывать" in instructions
        assert "ТОЛЬКО" in instructions

    @pytest.mark.asyncio
    async def test_repair_targets_the_same_model(self):
        """Исправляет свой ответ та же модель: чужой ответ править бессмысленно."""
        pool = make_safe_pool()
        profile = make_profile()

        gateway = FakeAIGateway(
            candidates=[make_candidate(1, "model-a"), make_candidate(2, "model-b")],
            per_model={
                "model-a": [
                    self._program_with_invented_exercise(pool),
                    make_valid_program_json(pool),
                ]
            },
        )
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=1,
        )

        await generator.generate(profile, pool)

        assert [model for model, _ in gateway.model_calls] == ["model-a", "model-a"]

    @pytest.mark.asyncio
    async def test_invented_exercise_is_still_rejected_by_validator(self):
        """Валидатор не ослаблен: выдуманный external_id остаётся ошибкой."""
        pool = make_safe_pool()
        profile = make_profile()
        invalid = self._program_with_invented_exercise(pool)

        generator = AIProgramGenerator(
            gateway=FakeAIGateway([invalid, invalid, invalid]),
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=2,
        )

        with pytest.raises(ProgramGenerationError, match="exercise_not_found"):
            await generator.generate(profile, pool)


# --- Переход по цепочке моделей ---------------------------------------------------


class TestModelChainFallback:
    """Невалидный ответ — тоже основание сменить модель.

    Раньше цепочку перебирал только Gateway и только по `AIError`. Провайдер
    отвечал `200 OK` с выдуманным упражнением, поэтому все три вызова уходили
    в одну и ту же flash-модель, а настроенные резервные не использовались.
    """

    @pytest.mark.asyncio
    async def test_next_model_is_tried_after_repair_attempts_fail(self):
        pool = make_safe_pool()
        profile = make_profile()

        gateway = FakeAIGateway(
            candidates=[make_candidate(1, "model-a"), make_candidate(2, "model-b")],
            per_model={
                "model-a": ["invalid", "still invalid", "nope"],
                "model-b": [make_valid_program_json(pool)],
            },
        )
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=2,
        )

        program = await generator.generate(profile, pool)

        assert program.title == "AI Test Program"
        assert program.generation.model == "model-b"
        # Первая модель получила ответ + два исправления, затем пришла очередь второй.
        assert [model for model, _ in gateway.model_calls] == [
            "model-a",
            "model-a",
            "model-a",
            "model-b",
        ]

    @pytest.mark.asyncio
    async def test_all_models_invalid_raises_for_deterministic_fallback(self):
        """Исчерпанная цепочка — отказ генератора: fallback решает оркестратор."""
        pool = make_safe_pool()
        profile = make_profile()

        gateway = FakeAIGateway(
            candidates=[make_candidate(1, "model-a"), make_candidate(2, "model-b")],
            per_model={
                "model-a": ["invalid", "invalid"],
                "model-b": ["invalid", "invalid"],
            },
        )
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=1,
        )

        with pytest.raises(ProgramGenerationError):
            await generator.generate(profile, pool)
        assert [model for model, _ in gateway.model_calls] == [
            "model-a",
            "model-a",
            "model-b",
            "model-b",
        ]

    @pytest.mark.asyncio
    async def test_provider_error_still_moves_to_next_model(self):
        """Транспортный сбой ведёт себя как раньше: сразу следующая модель."""
        pool = make_safe_pool()
        profile = make_profile()

        gateway = FakeAIGateway(
            candidates=[make_candidate(1, "model-a"), make_candidate(2, "model-b")],
            per_model={
                "model-a": [AIProviderError("boom", status_code=500)],
                "model-b": [make_valid_program_json(pool)],
            },
        )
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=2,
        )

        program = await generator.generate(profile, pool)

        assert program.generation.model == "model-b"
        # Исправлять нечего: ответа не было, repair не запрашивался.
        assert [model for model, _ in gateway.model_calls] == ["model-a", "model-b"]

    @pytest.mark.asyncio
    async def test_transport_error_of_last_model_is_raised_as_is(self):
        """Тип AI-ошибки сохраняется: по нему оркестратор различает причины."""
        pool = make_safe_pool()
        profile = make_profile()

        gateway = FakeAIGateway(
            candidates=[make_candidate(1, "model-a")],
            per_model={"model-a": [AIProviderError("boom", status_code=503)]},
        )
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
        )

        with pytest.raises(AIProviderError):
            await generator.generate(profile, pool)

    @pytest.mark.asyncio
    async def test_exhausted_budget_stops_the_chain(self):
        """Исчерпанный общий бюджет прекращает перебор.

        Предел одной модели и общий бюджет — разные ограничители: первый не даёт
        недоступной модели съесть чужое время, второй ограничивает генерацию
        целиком.
        """
        pool = make_safe_pool()
        profile = make_profile()

        gateway = FakeAIGateway(
            candidates=[make_candidate(1, "model-a"), make_candidate(2, "model-b")],
            per_model={
                "model-a": ["invalid"],
                "model-b": [make_valid_program_json(pool)],
            },
        )
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=2,
            total_budget_seconds=0,
        )

        with pytest.raises(ProgramGenerationError, match="время"):
            await generator.generate(profile, pool)
        # Первая модель получила свой вызов и провалилась по своему пределу;
        # к следующей перебор не пошёл — общий бюджет исчерпан. Это разные
        # ограничители: предел модели не даёт ей съесть чужое время, общий бюджет
        # ограничивает генерацию целиком.
        assert [model for model, _ in gateway.model_calls] == []


# --- Телеметрия попыток ----------------------------------------------------------


class TestAttemptTelemetry:
    """По журналу должно быть видно, почему цепочка fallback не спасла."""

    @pytest.mark.asyncio
    async def test_attempts_describe_each_model(self):
        pool = make_safe_pool()
        profile = make_profile()
        recorded: list[list] = []

        gateway = FakeAIGateway(
            candidates=[make_candidate(1, "model-a"), make_candidate(2, "model-b")],
            per_model={
                "model-a": ["invalid", "invalid"],
                "model-b": ["invalid", make_valid_program_json(pool)],
            },
        )
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=1,
            attempt_recorder=lambda attempts, prompt_version: _collect(
                recorded, attempts, prompt_version
            ),
        )

        await generator.generate(profile, pool)

        assert len(recorded) == 1
        recorded_attempts, recorded_version = recorded[0]
        # Версия инструкции обязана попасть в журнал вместе с попытками.
        assert recorded_version == 1
        attempts = AIProgramGenerator.attempts_metadata(recorded_attempts)
        assert [a["model_id"] for a in attempts] == ["model-a", "model-b"]

        first, second = attempts
        assert first["outcome"] == "invalid_output"
        assert first["initial_valid"] is False
        assert first["repair_attempts"] == 1
        assert first["is_primary"] is True

        assert second["outcome"] == "success"
        # Успех пришёл с исправления, а не с первого ответа.
        assert second["initial_valid"] is False
        assert second["repair_attempts"] == 1

    @pytest.mark.asyncio
    async def test_attempts_are_recorded_when_all_models_fail(self):
        pool = make_safe_pool()
        profile = make_profile()
        recorded: list[list] = []

        gateway = FakeAIGateway(
            candidates=[make_candidate(1, "model-a")],
            per_model={"model-a": [AIProviderError("boom", status_code=500)]},
        )
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            attempt_recorder=lambda attempts, prompt_version: _collect(
                recorded, attempts, prompt_version
            ),
        )

        with pytest.raises(AIProviderError):
            await generator.generate(profile, pool)

        attempts = AIProgramGenerator.attempts_metadata(recorded[0][0])
        assert attempts[0]["outcome"] == "provider_error"
        assert attempts[0]["error_type"] == "AIProviderError"

    @pytest.mark.asyncio
    async def test_telemetry_failure_does_not_break_generation(self):
        """Журнал не критичен: его сбой не отменяет готовую программу."""
        pool = make_safe_pool()
        profile = make_profile()

        async def _broken(_attempts, _prompt_version):
            raise RuntimeError("audit is down")

        generator = AIProgramGenerator(
            gateway=FakeAIGateway([make_valid_program_json(pool)]),
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            attempt_recorder=_broken,
        )

        program = await generator.generate(profile, pool)
        assert program.title == "AI Test Program"


async def _collect(sink: list, attempts, prompt_version) -> None:
    # Версия инструкции пишется рядом с попытками: без неё журнал не отвечает,
    # какая формулировка привела к отказу.
    sink.append((attempts, prompt_version))


# --- PromptLoader: единственный источник инструкций --------------------------------


class _PromptRepo:
    def __init__(self, items: list[PromptTemplate]) -> None:
        self.items = items

    async def get(self, task_type, version):
        return next(
            (t for t in self.items if t.task_type == task_type and t.version == version),
            None,
        )


class _TaskRepo:
    def __init__(self, config: AITaskConfig | None) -> None:
        self.config = config

    async def get(self, task_type):
        return self.config


def _template(**overrides) -> PromptTemplate:
    data = {
        "id": 1,
        "task_type": AITaskType.WORKOUT_GENERATION,
        "version": 1,
        "name": "Базовая инструкция",
        "system_prompt": "ПРАВИЛА",
        "user_template": "ЗАПРОС",
        "enabled": True,
    }
    data.update(overrides)
    return PromptTemplate(**data)


class TestPromptLoader:
    """Инструкция берётся только из базы.

    Раньше загрузчик читал `prompt_templates`, а при неудаче молча брал файл из
    образа. Источник истины был неопределён: файловый текст нельзя было ни
    прочитать в админке, ни изменить, ни удалить, а `prompt_version = NULL`
    означал «взять файл». Базовая инструкция перенесена в базу миграцией `0009`,
    поэтому второго источника больше нет.
    """

    @pytest.mark.asyncio
    async def test_loads_selected_version(self):
        loader = PromptLoader(_PromptRepo([_template(version=3, system_prompt="V3")]))
        system_prompt, user_template, version = await loader.load(
            AITaskType.WORKOUT_GENERATION, 3
        )
        assert system_prompt == "V3"
        assert user_template == "ЗАПРОС"
        assert version == 3

    @pytest.mark.asyncio
    async def test_version_is_taken_from_task_configuration(self):
        """Версию выбирает задача: генератор её не угадывает."""
        loader = PromptLoader(
            _PromptRepo([_template(version=1), _template(id=2, version=2, system_prompt="V2")]),
            _TaskRepo(
                AITaskConfig(
                    id=1, task_type=AITaskType.WORKOUT_GENERATION, prompt_version=2
                )
            ),
        )
        system_prompt, _, version = await loader.load(AITaskType.WORKOUT_GENERATION)
        assert system_prompt == "V2"
        assert version == 2

    @pytest.mark.asyncio
    async def test_missing_selection_is_a_configuration_error(self):
        """Пустая версия не подменяется «какой-нибудь» инструкцией."""
        loader = PromptLoader(
            _PromptRepo([_template()]),
            _TaskRepo(
                AITaskConfig(
                    id=1, task_type=AITaskType.WORKOUT_GENERATION, prompt_version=None
                )
            ),
        )
        with pytest.raises(AIConfigurationError, match="не выбрана"):
            await loader.load(AITaskType.WORKOUT_GENERATION)

    @pytest.mark.asyncio
    async def test_unknown_version_is_a_configuration_error(self):
        loader = PromptLoader(_PromptRepo([_template()]))
        with pytest.raises(AIConfigurationError, match="№7"):
            await loader.load(AITaskType.WORKOUT_GENERATION, 7)

    @pytest.mark.asyncio
    async def test_disabled_version_is_not_used(self):
        loader = PromptLoader(_PromptRepo([_template(enabled=False)]))
        with pytest.raises(AIConfigurationError, match="выключена"):
            await loader.load(AITaskType.WORKOUT_GENERATION, 1)

    def test_module_has_no_filesystem_prompt_source(self):
        """Файлового источника нет ни как пути, ни как чтения с диска."""
        import src.application.ai.program_generator as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert not hasattr(module, "PROMPTS_DIR")
        assert "read_text" not in source
        assert "prompts/program_generator" not in source


# --- Расчёт занятия в промпте -----------------------------------------------------


class TestSessionPlanInPrompt:
    """Модель получает расчёт занятия, а не оценивает время сама.

    Прогон 24 программ на staging дал разброс от 4 до 84 минут при заявленных
    60–90: модель видела `session_duration_minutes` в контексте, но что с ним
    делать, ей не сообщалось. Расчёт выполняет приложение и передаёт как ориентир.
    """

    @staticmethod
    def _plan(**overrides):
        from src.application.ai.program_context import SessionPlanBrief

        data = {
            "total_minutes": 90,
            "warmup_minutes": 8,
            "cooldown_minutes": 5,
            "main_minutes": 77,
            "tolerance_minutes": 5,
            "exercises": 7,
            "sets": 4,
            "reps_min": 4,
            "reps_max": 6,
            "rest_seconds": 150,
            "approach": "силовой, с полным восстановлением между подходами",
        }
        data.update(overrides)
        return SessionPlanBrief(**data)

    def test_plan_is_rendered_with_all_numbers(self):
        from src.application.ai.program_generator import _render_session_plan

        text = _render_session_plan(self._plan())

        # Заявленное время и допуск: без них требование «уложиться» бессодержательно.
        assert "90 минут" in text
        assert "±5 минут" in text
        # Структура занятия: разминка и заминка занимают время и должны быть учтены.
        assert "разминка 8" in text
        assert "заминка 5" in text
        # Объём и характер нагрузки.
        assert "7 упражнений" in text
        assert "4 подхода" in text
        assert "150 секунд" in text
        assert "силовой" in text

    def test_capped_plan_tells_model_not_to_stretch_program(self):
        """Недостижимое время не скрывается: модель сообщает фактическое."""
        from src.application.ai.program_generator import _render_session_plan

        text = _render_session_plan(self._plan(total_minutes=96, capped=True))

        assert "невозможно занять" in text
        assert "96 минут" in text
        assert "не" in text and "растягивай" in text

    def test_missing_plan_does_not_break_prompt(self):
        """Отсутствие расчёта не роняет генерацию: промпт остаётся валидным."""
        from src.application.ai.program_generator import _render_session_plan

        assert _render_session_plan(None) == "не рассчитан"

    def test_context_carries_plan_and_condition(self):
        """Контекст несёт расчёт и состояние человека тем же путём, что цель."""
        from src.application.ai.program_context import build_generation_context

        profile = make_profile()
        profile.training_plan_preferences.session_duration_minutes = 75
        context = build_generation_context(profile, make_safe_pool())

        assert context.session_plan is not None
        assert context.session_plan.total_minutes == 75
        # Поле состояния существует до появления вопроса в анкете: когда вопрос
        # появится, состояние пойдёт тем же путём, а не отдельной подсистемой.
        assert context.current_condition is None


# --- Проба готовности модели в цепочке --------------------------------------------


class TestProbeSkipsDeadModels:
    """Недоступная модель отсеивается до полного запроса.

    Наблюдалось на staging: две сломанные модели исчерпывали бюджет генерации за
    ~400 секунд (одна рвала соединение на 200 с, другая молчала до бюджета), и до
    рабочих моделей в конце цепочки дело не доходило — программу собирал алгоритм,
    хотя рабочая модель была.
    """

    class FakeProbe:
        """Проба со сценарием вердиктов по model_id."""

        def __init__(self, unavailable: set[str]) -> None:
            self.unavailable = unavailable
            self.checked: list[str] = []

        async def check(self, candidate):
            from src.application.ai.model_probe import ProbeVerdict

            self.checked.append(candidate.model.model_id)
            if candidate.model.model_id in self.unavailable:
                return ProbeVerdict(
                    available=False,
                    error_type="AIConnectionError",
                    detail="не удалось соединиться с сервисом ИИ",
                )
            return ProbeVerdict(available=True)

    @pytest.mark.asyncio
    async def test_dead_model_is_skipped_without_request(self):
        pool = make_safe_pool()
        profile = make_profile()
        probe = self.FakeProbe({"model-a"})

        gateway = FakeAIGateway(
            candidates=[make_candidate(1, "model-a"), make_candidate(2, "model-b")],
            per_model={"model-b": [make_valid_program_json(pool)]},
        )
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            probe_service=probe,
        )

        program = await generator.generate(profile, pool)

        assert program.generation.model == "model-b"
        # Обе модели пробовались, но запрос ушёл только в рабочую.
        assert probe.checked == ["model-a", "model-b"]
        assert [model for model, _ in gateway.model_calls] == ["model-b"]

    @pytest.mark.asyncio
    async def test_probe_failure_is_recorded_as_separate_outcome(self):
        """`probe_failed` отличается от `provider_error`: запроса не было."""
        pool = make_safe_pool()
        profile = make_profile()
        recorded: list[list] = []

        gateway = FakeAIGateway(
            candidates=[make_candidate(1, "model-a"), make_candidate(2, "model-b")],
            per_model={"model-b": [make_valid_program_json(pool)]},
        )
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            probe_service=self.FakeProbe({"model-a"}),
            attempt_recorder=lambda attempts, version: _collect(
                recorded, attempts, version
            ),
        )

        await generator.generate(profile, pool)

        # `_collect` пишет пару (попытки, версия инструкции).
        attempts = AIProgramGenerator.attempts_metadata(recorded[0][0])
        assert attempts[0]["model_id"] == "model-a"
        assert attempts[0]["outcome"] == "probe_failed"
        assert attempts[1]["outcome"] == "success"

    @pytest.mark.asyncio
    async def test_all_models_failing_probe_ends_generation(self):
        """Если ни одна модель не прошла пробу, генерация отказывает.

        Дальше решает оркестратор: программу соберёт алгоритм.
        """
        pool = make_safe_pool()
        profile = make_profile()
        probe = self.FakeProbe({"model-a", "model-b"})

        gateway = FakeAIGateway(
            candidates=[make_candidate(1, "model-a"), make_candidate(2, "model-b")]
        )
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            probe_service=probe,
        )

        with pytest.raises(ProgramGenerationError, match="проверку готовности"):
            await generator.generate(profile, pool)
        # Ни одного полного запроса: бюджет генерации не потрачен впустую.
        assert gateway.model_calls == []

    @pytest.mark.asyncio
    async def test_repair_does_not_reprobe(self):
        """Исправление не пробуется: модель только что ответила."""
        pool = make_safe_pool()
        profile = make_profile()
        probe = self.FakeProbe(set())

        gateway = FakeAIGateway(
            candidates=[make_candidate(1, "model-a")],
            per_model={"model-a": ["invalid", make_valid_program_json(pool)]},
        )
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=1,
            probe_service=probe,
        )

        await generator.generate(profile, pool)

        # Проба один раз, а запросов два (ответ + исправление).
        assert probe.checked == ["model-a"]
        assert len(gateway.model_calls) == 2

    @pytest.mark.asyncio
    async def test_generation_works_without_probe(self):
        """Без пробы поведение прежнее: отказ ловит настоящий запрос."""
        pool = make_safe_pool()
        profile = make_profile()

        generator = AIProgramGenerator(
            gateway=FakeAIGateway([make_valid_program_json(pool)]),
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
        )

        program = await generator.generate(profile, pool)
        assert program.title == "AI Test Program"


# --- JSON в служебной обёртке шлюза -----------------------------------------------


class TestGatewayWrappedJson:
    """Программа, завёрнутая шлюзом в служебное поле, распознаётся.

    У части моделей нет нативного JSON-режима, и шлюз эмулирует
    `response_format: json_object` через вызов инструмента. Результат приходит
    строкой внутри служебного поля. Наблюдалось на routerai для
    `anthropic/claude-sonnet-5` (4 отказа из 4) и `z-ai/glm-5.3-flash`: модель
    отвечала корректной программой, а валидатор сообщал «title: Field required»,
    потому что видел обёртку.
    """

    def test_wrapped_program_is_unwrapped(self):
        pool = make_safe_pool()
        inner = make_valid_program_json(pool)
        wrapped = json.dumps({"_noargs": inner}, ensure_ascii=False)

        payload = AIOutputParser.extract_json(wrapped)

        assert payload["title"] == "AI Test Program"
        assert len(payload["training_days"]) == 1

    def test_wrapper_key_name_is_not_hardcoded(self):
        """Имя служебного поля зависит от шлюза, признак обёртки — структура.

        Список известных имён пришлось бы пополнять после каждого нового
        провайдера, а сама обёртка распознаётся однозначно и без него.
        """
        pool = make_safe_pool()
        for key in ("_noargs", "arguments", "result", "tool_input"):
            wrapped = json.dumps(
                {key: make_valid_program_json(pool)}, ensure_ascii=False
            )
            assert AIOutputParser.extract_json(wrapped)["title"] == "AI Test Program"

    def test_plain_response_is_untouched(self):
        pool = make_safe_pool()
        payload = AIOutputParser.extract_json(make_valid_program_json(pool))
        assert payload["title"] == "AI Test Program"

    def test_single_field_with_plain_text_is_not_unwrapped(self):
        """Строка, которая не JSON, — это поле с текстом, а не обёртка."""
        payload = AIOutputParser.extract_json('{"description": "просто текст"}')
        assert payload == {"description": "просто текст"}

    def test_single_field_with_json_array_is_not_unwrapped(self):
        """Разворачивается только объект: массив не может быть программой."""
        payload = AIOutputParser.extract_json('{"items": "[1, 2, 3]"}')
        assert payload == {"items": "[1, 2, 3]"}

    def test_wrapped_inside_markdown_block(self):
        """Обёртка распознаётся и когда шлюз добавил markdown."""
        pool = make_safe_pool()
        wrapped = json.dumps({"_noargs": make_valid_program_json(pool)}, ensure_ascii=False)
        content = f"```json\n{wrapped}\n```"

        assert AIOutputParser.extract_json(content)["title"] == "AI Test Program"

    @pytest.mark.asyncio
    async def test_generation_succeeds_on_wrapped_answer(self):
        """Сквозная проверка: завёрнутый ответ проходит генерацию целиком."""
        pool = make_safe_pool()
        profile = make_profile()
        wrapped = json.dumps(
            {"_noargs": make_valid_program_json(pool)}, ensure_ascii=False
        )

        generator = AIProgramGenerator(
            gateway=FakeAIGateway([wrapped]),
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
        )

        program = await generator.generate(profile, pool)
        assert program.title == "AI Test Program"
        assert program.generation.source is GenerationSource.AI


# --- Бюджет генерации от числа моделей --------------------------------------------


class TestBudgetFollowsChainLength:
    """Общий бюджет считается от числа кандидатов, а не задан числом.

    Абсолютная константа была привязана к текущей конфигурации: при шести моделях
    её хватало, а после добавления администратором ещё десяти цепочка обрывалась
    на седьмой — та же проблема, из-за которой предел на модель и вводился, плюс
    правка кода ради изменения настроек в админке.
    """

    @staticmethod
    def _generator(**kwargs):
        return AIProgramGenerator(
            gateway=FakeAIGateway(),
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            **kwargs,
        )

    def test_budget_grows_with_chain_length(self):
        from src.application.ai.program_generator import (
            DEFAULT_MODEL_BUDGET_SECONDS,
        )

        generator = self._generator()

        assert generator._budget_for(1) == DEFAULT_MODEL_BUDGET_SECONDS
        assert generator._budget_for(6) == 6 * DEFAULT_MODEL_BUDGET_SECONDS
        assert generator._budget_for(16) == 16 * DEFAULT_MODEL_BUDGET_SECONDS

    def test_budget_is_capped_against_degenerate_configuration(self):
        """Потолок защищает от десятков привязанных моделей, а не ограничивает.

        Полчаса ожидания программы бессмысленны независимо от конфигурации.
        """
        from src.application.ai.program_generator import MAX_TOTAL_BUDGET_SECONDS

        assert self._generator()._budget_for(500) == MAX_TOTAL_BUDGET_SECONDS

    def test_empty_chain_still_has_budget(self):
        """Пустая цепочка не даёт нулевой бюджет: отказ должен быть осмысленным."""
        assert self._generator()._budget_for(0) > 0

    def test_explicit_budget_overrides_calculation(self):
        """Явное значение нужно тестам и вызывающим со своим ограничением."""
        assert self._generator(total_budget_seconds=42)._budget_for(6) == 42

    @pytest.mark.asyncio
    async def test_long_chain_reaches_last_model(self):
        """Шесть моделей: до последней доходит очередь, бюджет не обрывает перебор.

        Регрессия: при абсолютном бюджете 240 с и пределе 80 с на модель перебор
        обрывался на третьей, и рабочие модели в конце не опрашивались.
        """
        pool = make_safe_pool()
        profile = make_profile()
        chain = [make_candidate(i, f"model-{i}") for i in range(1, 7)]

        gateway = FakeAIGateway(
            candidates=chain,
            per_model={
                # Первые пять отвечают невалидно, последняя — корректно.
                **{f"model-{i}": ["invalid"] for i in range(1, 6)},
                "model-6": [make_valid_program_json(pool)],
            },
        )
        generator = AIProgramGenerator(
            gateway=gateway,
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            max_repair_attempts=0,
        )

        program = await generator.generate(profile, pool)

        assert program.generation.model == "model-6"
        assert [model for model, _ in gateway.model_calls] == [
            f"model-{i}" for i in range(1, 7)
        ]
