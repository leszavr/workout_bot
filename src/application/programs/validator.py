"""Program Validator: независимая проверка WorkoutProgram.

Валидатор отделён от генератора: в будущем AI-вывод пройдёт через тот же слой
(AI Output Parser → ProgramValidator → SafetyValidator) без переписывания.

Проверки:
1. строгая Pydantic-схема (при валидации из dict);
2. все exercise_id существуют в каталоге;
3. все упражнения принадлежат SafeExercisePool;
4. нет дубликатов упражнений внутри дня;
5. число дней соответствует заявленному training_days_per_week;
6. число упражнений в дне в разумном диапазоне;
7. подходы/повторения валидны (гарантируется схемой, проверяется явно).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from src.domain.pools import SafeExercisePool
from src.domain.profile import FitnessProfile
from src.domain.program import TrainingDay, WorkoutProgram

MIN_EXERCISES_PER_DAY = 1
MAX_EXERCISES_PER_DAY = 15


@dataclass
class ValidationIssue:
    code: str
    message: str


@dataclass
class ValidationResult:
    valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)


class ProgramValidator:
    """Проверяет программу против схемы, каталога и safe-пула."""

    def validate_schema(self, payload: dict) -> tuple[WorkoutProgram | None, ValidationResult]:
        """Валидация из произвольного dict (будущий AI-вывод проходит здесь же)."""
        try:
            program = WorkoutProgram.model_validate(payload)
        except ValidationError as exc:
            issues = [
                ValidationIssue(
                    code="schema",
                    message=f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}",
                )
                for e in exc.errors()
            ]
            return None, ValidationResult(valid=False, issues=issues)
        return program, ValidationResult(valid=True)

    def validate(
        self,
        program: WorkoutProgram,
        pool: SafeExercisePool,
        profile: FitnessProfile,
        catalog_ids: set[str],
    ) -> ValidationResult:
        issues: list[ValidationIssue] = []
        allowed_ids = pool.allowed_ids()

        if program.profile_id != (profile.profile_id or ""):
            issues.append(
                ValidationIssue("profile_mismatch", "Программа ссылается на другой профиль")
            )

        if len(program.training_days) != program.training_days_per_week:
            issues.append(
                ValidationIssue(
                    "days_count",
                    f"Дней {len(program.training_days)}, заявлено {program.training_days_per_week}",
                )
            )

        for day in program.training_days:
            issues.extend(self._validate_day(day, allowed_ids, catalog_ids))

        return ValidationResult(valid=not issues, issues=issues)

    @staticmethod
    def _validate_day(
        day: TrainingDay, allowed_ids: set[str], catalog_ids: set[str]
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not (MIN_EXERCISES_PER_DAY <= len(day.exercises) <= MAX_EXERCISES_PER_DAY):
            issues.append(
                ValidationIssue(
                    "day_size",
                    f"День {day.day_number}: {len(day.exercises)} упражнений "
                    f"(допустимо {MIN_EXERCISES_PER_DAY}–{MAX_EXERCISES_PER_DAY})",
                )
            )

        seen: set[str] = set()
        for item in day.exercises:
            if item.exercise_external_id in seen:
                issues.append(
                    ValidationIssue(
                        "duplicate_exercise",
                        f"День {day.day_number}: дубликат {item.exercise_external_id}",
                    )
                )
            seen.add(item.exercise_external_id)

            if item.exercise_external_id not in catalog_ids:
                issues.append(
                    ValidationIssue(
                        "exercise_not_found",
                        f"Упражнение {item.exercise_external_id} отсутствует в каталоге",
                    )
                )
            elif item.exercise_external_id not in allowed_ids:
                issues.append(
                    ValidationIssue(
                        "exercise_not_allowed",
                        f"Упражнение {item.exercise_external_id} не входит в SafeExercisePool",
                    )
                )

            if item.repetitions_max < item.repetitions_min:
                issues.append(
                    ValidationIssue(
                        "reps_range",
                        f"День {day.day_number}: некорректный диапазон повторений",
                    )
                )
        return issues
