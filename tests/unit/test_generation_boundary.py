"""Архитектурный acceptance-тест Phase 1.2-C.

Проверяется не поведение отдельного сервиса, а свойство архитектуры: у
генерации программы существует ровно одна application-level точка. Тест
статически анализирует исходники Telegram gateway и Admin API и падает, если
какой-то слой снова начинает собирать собственный generation pipeline.

Почему статический анализ, а не мок: обойти оркестратор можно новым кодом,
который просто не покрыт behavioural-тестами. Здесь фиксируется граница, и
регрессия видна сразу, без специально написанного сценария.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Компоненты generation pipeline, которыми имеет право распоряжаться только
# оркестратор. Telegram и Admin API обязаны идти через него.
FORBIDDEN_NAMES = {
    "AIProgramGenerator",
    "DeterministicProgramGenerator",
    "ProgramValidator",
    "SafetyEngine",
    "ExerciseFilter",
}

# Фабрика зависимостей — легитимное место сборки оркестратора: именно там
# generation pipeline собирается один раз для обоих вызывающих слоёв.
ALLOWED_FILES = {
    Path("apps/backend/api/v1/dependencies.py"),
}


def _python_files(*relative_dirs: str) -> list[Path]:
    files: list[Path] = []
    for relative in relative_dirs:
        files.extend(sorted((PROJECT_ROOT / relative).rglob("*.py")))
    return files


def _layer_files() -> list[Path]:
    return [
        path
        for path in _python_files("apps/backend", "apps/telegram_gateway")
        if path.relative_to(PROJECT_ROOT) not in ALLOWED_FILES
    ]


def _referenced_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[-1] for alias in node.names)
    return names


class TestSingleGenerationBoundary:
    @pytest.mark.parametrize("path", _layer_files(), ids=lambda p: str(p.name))
    def test_no_direct_generator_usage(self, path: Path):
        """Генераторы, validator и safety вызываются только из оркестратора."""
        found = FORBIDDEN_NAMES & _referenced_names(path)
        assert not found, (
            f"{path.relative_to(PROJECT_ROOT)} обращается к generation pipeline "
            f"напрямую: {sorted(found)}. Использовать ProgramGenerationOrchestrator."
        )

    def test_program_repository_is_not_written_outside_orchestrator(self):
        """Программу сохраняет только оркестратор: `save`/`next_version` наружу нет."""
        offenders: list[str] = []
        for path in _layer_files():
            source = path.read_text(encoding="utf-8")
            if "next_version(" in source or "PostgresProgramRepository(" in source:
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
        assert offenders == [], (
            "Создание версии программы допустимо только внутри оркестратора: "
            f"{offenders}"
        )

    def test_generation_job_state_is_not_changed_outside_orchestrator(self):
        """Состояние GenerationJob меняет только оркестратор через job-сервис."""
        forbidden = ("mark_running", "mark_succeeded", "mark_failed", "create_or_get")
        offenders: list[str] = []
        for path in _layer_files():
            source = path.read_text(encoding="utf-8")
            if any(marker in source for marker in forbidden):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
        assert offenders == [], (
            f"Переходы состояния GenerationJob вне оркестратора: {offenders}"
        )

    def test_program_service_has_no_generation(self):
        """`ProgramService` — только чтение: второго pipeline в нём нет."""
        source = (
            PROJECT_ROOT / "src/application/programs/service.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        service = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "ProgramService"
        )
        methods = {
            node.name
            for node in service.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert "generate" not in methods
        assert "build_pools" not in methods

    def test_admin_endpoint_uses_orchestrator(self):
        """Admin endpoint генерации вызывает оркестратор, а не ProgramService."""
        source = (PROJECT_ROOT / "apps/backend/api/v1/routes.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        endpoint = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "generate_program"
        )
        body = ast.dump(endpoint)
        assert "build_generation_orchestrator" in body
        assert "build_program_service" not in body

    def test_telegram_pipeline_uses_orchestrator(self):
        """Telegram finalization идёт через оркестратор и ничего не выбирает сам."""
        pipeline = (PROJECT_ROOT / "apps/telegram_gateway/pipeline.py").read_text(
            encoding="utf-8"
        )
        assert "build_generation_orchestrator" in pipeline

        handler = (
            PROJECT_ROOT / "apps/telegram_gateway/handlers/review.py"
        ).read_text(encoding="utf-8")
        # Handler отвечает только за Telegram-взаимодействие: pipeline и есть
        # его единственная точка входа в генерацию.
        assert "build_program_pipeline" in handler
        assert "ProgramService" not in handler
        assert "GenerationJob" not in handler
