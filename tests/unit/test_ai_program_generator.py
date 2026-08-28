"""Unit-тесты AIProgramGenerator (этап 4).

Тесты используют FakeAIGateway — реальные AI API не вызываются.
"""
from __future__ import annotations

import json

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
from src.domain.ai.config import AIEndpoint, AIModel, AIProvider, AITaskConfig
from src.domain.ai.enums import AITaskType
from src.domain.ai.errors import AIProviderError
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


class FakePromptLoader(PromptLoader):
    """Фейковый загрузчик промптов."""

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

        Без общего бюджета таймауты складывались (попытки × таймаут × repair) и
        запрос «висел» минутами вместо понятного отказа.
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

        with pytest.raises(ProgramGenerationError, match="время"):
            await generator.generate(profile, pool)
        # Исправление не запрашивалось: был только первоначальный вызов.
        assert len(gateway.calls) == 1

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
        """Исчерпанный бюджет не переносится на следующую модель."""
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
        assert [model for model, _ in gateway.model_calls] == ["model-a"]


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
            attempt_recorder=lambda attempts: _collect(recorded, attempts),
        )

        await generator.generate(profile, pool)

        assert len(recorded) == 1
        attempts = AIProgramGenerator.attempts_metadata(recorded[0])
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
            attempt_recorder=lambda attempts: _collect(recorded, attempts),
        )

        with pytest.raises(AIProviderError):
            await generator.generate(profile, pool)

        attempts = AIProgramGenerator.attempts_metadata(recorded[0])
        assert attempts[0]["outcome"] == "provider_error"
        assert attempts[0]["error_type"] == "AIProviderError"

    @pytest.mark.asyncio
    async def test_telemetry_failure_does_not_break_generation(self):
        """Журнал не критичен: его сбой не отменяет готовую программу."""
        pool = make_safe_pool()
        profile = make_profile()

        async def _broken(_attempts):
            raise RuntimeError("audit is down")

        generator = AIProgramGenerator(
            gateway=FakeAIGateway([make_valid_program_json(pool)]),
            prompt_loader=FakePromptLoader(),
            validator=ProgramValidator(),
            attempt_recorder=_broken,
        )

        program = await generator.generate(profile, pool)
        assert program.title == "AI Test Program"


async def _collect(sink: list, attempts) -> None:
    sink.append(attempts)
