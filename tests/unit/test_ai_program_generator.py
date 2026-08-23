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
from src.application.programs.validator import ProgramValidator
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


class FakeAIGateway:
    """Фейковый AI Gateway для тестов."""

    def __init__(self, responses: list[str] | None = None):
        self.responses = responses or []
        self.calls: list = []
        self._call_index = 0

    async def generate(self, request):
        self.calls.append(request)
        if self._call_index < len(self.responses):
            content = self.responses[self._call_index]
            self._call_index += 1
        else:
            content = "{}"
        return AIResponse(
            content=content,
            model="test-model",
            provider="test-provider",
            endpoint="test-endpoint",
            input_tokens=100,
            output_tokens=200,
            total_tokens=300,
            latency_ms=500,
        )


class FakePromptLoader(PromptLoader):
    """Фейковый загрузчик промптов."""

    async def load(self, task_type, version=None):
        return (
            "You are a fitness expert.",
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
        assert program.generation.provider == "test-provider"
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