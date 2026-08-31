"""Сравнение моделей на одном наборе анкет: какая точнее следует требованиям.

Зачем отдельный скрипт. `seed_analytics_data` наполняет дашборд разными
разрезами, и модель там — следствие сценария доступности. Здесь наоборот: набор
анкет один и тот же, а меняется только модель — иначе разница в результате
объяснялась бы разными профилями, а не качеством модели.

Как обеспечивается «одна модель за раз». Задача переключается на одну модель
(она же primary, других привязок нет), поэтому fallback внутри цепочки
невозможен, и каждая программа заведомо собрана той моделью, которая указана.
Без этого сравнение было бы бессмысленным: неудачу дешёвой модели подхватывала бы
следующая, и в статистику попала бы чужая работа.

Данные пишутся в те же таблицы, что рабочая генерация, поэтому сравнение видно в
админке: журнал попыток моделей, аналитика по моделям и сравнение инструкций.

Запуск на staging:
    python -m scripts.qa_harness.compare_models --base-url http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from scripts.qa_harness.model_scenarios import AdminClient
from scripts.qa_harness.run_mass_test import (
    _build_simulator,
    _drain_background_tasks,
)
from scripts.qa_harness.scenarios import EXTRA_SCENARIOS, SCENARIOS
from scripts.qa_harness.seed_analytics_data import await_program
from scripts.qa_harness.user_simulator import ScriptedUser, TelegramUserSimulator

logger = logging.getLogger(__name__)

# Кандидаты в порядке возрастания цены за генерацию. Стоимость посчитана по
# фактическому расходу прогонов (~7k токенов вход, ~4k выход) и каталогу
# routerai; она указана, чтобы в отчёте было видно цену точности, а не только
# саму точность.
CANDIDATES: list[dict] = [
    {
        "model_id": "z-ai/glm-5.3-flash",
        "display_name": "GLM 5.3 Flash",
        "cost_per_generation": 0.12,
        "note": "текущая основная: 8/8 валидных первых ответов в прогоне 29.08",
    },
    {
        "model_id": "deepseek/deepseek-v4-flash-0731",
        "display_name": "DeepSeek V4 Flash 0731",
        "cost_per_generation": 0.10,
        "note": "flash с рассуждением, дешевле текущей основной",
    },
    {
        "model_id": "qwen/qwen3.8-flash",
        "display_name": "Qwen3.8 Flash",
        "cost_per_generation": 0.33,
        "note": "текущий резерв: 4/8 первых ответов невалидны в прогоне 29.08",
    },
    {
        "model_id": "deepseek/deepseek-v4-pro-0813",
        "display_name": "DeepSeek V4 Pro 0813",
        "cost_per_generation": 1.95,
        "note": "средний класс: проверка, окупается ли переход с flash",
    },
    {
        "model_id": "anthropic/claude-sonnet-5",
        "display_name": "Claude Sonnet 5",
        "cost_per_generation": 6.01,
        "note": "флагман: верхняя граница качества для сравнения",
    },
]

# Сколько анкет прогоняется на каждой модели. Четыре — компромисс: меньше не даёт
# различить модели, больше умножает стоимость на пять кандидатов. Порог
# достоверности аналитики (10 генераций) здесь не достигается сознательно, и это
# видно в отчёте: прогон отвечает на вопрос «есть ли грубые отличия», а не
# «какая модель лучше на 3%».
USERS_PER_MODEL = 4


@dataclass
class ModelResult:
    """Результат одной анкеты на одной модели."""

    model_id: str
    user_name: str
    display_number: str | None = None
    generator: str | None = None
    actual_model: str | None = None
    days_requested: int | None = None
    days_actual: int | None = None
    exercises_total: int | None = None
    initial_valid: bool | None = None
    repair_attempts: int | None = None
    outcome: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.generator == "ai" and self.error is None

    @property
    def days_match(self) -> bool | None:
        if self.days_requested is None or self.days_actual is None:
            return None
        return self.days_requested == self.days_actual


@dataclass
class ModelSummary:
    """Сводка по одной модели."""

    model_id: str
    display_name: str
    cost_per_generation: float
    note: str
    results: list[ModelResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def ai_success(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def first_answer_valid(self) -> int:
        return sum(1 for r in self.results if r.initial_valid)

    @property
    def repairs(self) -> int:
        return sum(r.repair_attempts or 0 for r in self.results)

    @property
    def days_respected(self) -> int:
        return sum(1 for r in self.results if r.days_match)

    @property
    def fell_back(self) -> int:
        return sum(1 for r in self.results if r.generator == "deterministic")


# --- Настройка моделей ------------------------------------------------------------


async def ensure_models(admin: AdminClient, endpoint_id: int) -> dict[str, int]:
    """Регистрирует кандидатов в админке, если их там ещё нет.

    Идемпотентно: повторный запуск не создаёт дубликаты. `supports_structured_output`
    выставляется всем кандидатам — отбор шёл именно по поддержке строгого формата,
    без неё модель отвечает прозой, которую отвергает валидатор.
    """
    existing = {m["model_id"]: m["id"] for m in await admin.models(endpoint_id)}
    result: dict[str, int] = {}
    for candidate in CANDIDATES:
        model_id = candidate["model_id"]
        if model_id in existing:
            result[model_id] = existing[model_id]
            continue
        created = await admin.create_model(
            endpoint_id,
            {
                "model_id": model_id,
                "display_name": candidate["display_name"],
                "enabled": True,
                "supports_structured_output": True,
                "supports_json_schema": True,
            },
        )
        result[model_id] = created["id"]
        logger.info("Добавлена модель %s (pk=%s)", model_id, created["id"])
    return result


async def switch_task_to(admin: AdminClient, model_pk: int) -> None:
    """Оставляет у задачи одну модель.

    Единственная привязка исключает fallback: программа заведомо собрана той
    моделью, которую сравниваем. С несколькими привязками неудачу дешёвой модели
    подхватывала бы следующая, и в статистику попала бы чужая работа.
    """
    task = await admin.task()
    await admin.put_task(
        {
            "enabled": True,
            "temperature": task["temperature"],
            "max_tokens": task["max_tokens"],
            "timeout_seconds": task["timeout_seconds"],
            "prompt_version": task["prompt_version"],
            "model_ids": [model_pk],
        }
    )


async def restore_task(admin: AdminClient, model_pks: list[int], prompt_version: int) -> None:
    """Возвращает исходную конфигурацию задачи.

    Обязательно и при аварийном выходе: staging используется не только этим
    скриптом, и оставить задачу на одной тестовой модели значило бы изменить
    поведение генерации для всех.
    """
    task = await admin.task()
    await admin.put_task(
        {
            "enabled": True,
            "temperature": task["temperature"],
            "max_tokens": task["max_tokens"],
            "timeout_seconds": task["timeout_seconds"],
            "prompt_version": prompt_version,
            "model_ids": model_pks,
        }
    )
    logger.info("Конфигурация задачи восстановлена: моделей %s", len(model_pks))


# --- Прогон -----------------------------------------------------------------------


async def run_model(
    summary: ModelSummary,
    model_pk: int,
    users: list[ScriptedUser],
    admin: AdminClient,
    simulator: TelegramUserSimulator,
    offset: int,
) -> None:
    """Прогоняет набор анкет на одной модели."""
    await switch_task_to(admin, model_pk)
    readiness = await admin.readiness()
    logger.info(
        "=== %s (%.2f ₽/генерация) · готовность=%s",
        summary.model_id,
        summary.cost_per_generation,
        readiness["ready"],
    )

    for user in users:
        # Смещение id: у каждой модели свои «пользователи», иначе повторное
        # прохождение попало бы в существующий профиль и программа не создалась бы.
        scoped = ScriptedUser(
            name=user.name,
            telegram_user_id=user.telegram_user_id + offset,
            answers=user.answers,
            expectations=user.expectations,
        )
        summary.results.append(await run_single(summary.model_id, scoped, simulator))


async def run_single(
    model_id: str, user: ScriptedUser, simulator: TelegramUserSimulator
) -> ModelResult:
    result = ModelResult(model_id=model_id, user_name=user.name)
    logger.info("→ %s / %s", model_id, user.name)
    try:
        run = await simulator.run(user)
    except Exception as exc:  # noqa: BLE001 — сбой одной анкеты не отменяет прогон
        logger.exception("Анкета не прошла: %s", user.name)
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    result.display_number = run.display_number
    if not run.finalized or not run.profile_id:
        result.error = "анкета не финализирована"
        return result

    program = await await_program(run.profile_id)
    if program is None:
        result.error = "программа не появилась за отведённое время"
        return result

    result.generator = program.generation.source.value
    result.actual_model = program.generation.model
    result.days_actual = len(program.training_days)
    result.exercises_total = sum(len(d.exercises) for d in program.training_days)
    result.days_requested = _requested_days(user)
    await _fill_attempt_details(result, program.profile_id)

    logger.info(
        "  %s · генератор=%s дней=%s/%s первый ответ=%s repair=%s",
        result.display_number or "—",
        result.generator,
        result.days_actual,
        result.days_requested,
        "принят" if result.initial_valid else "отклонён",
        result.repair_attempts,
    )
    return result


def _requested_days(user: ScriptedUser) -> int | None:
    """Сколько занятий человек попросил в анкете."""
    answer = user.answers.get("q20_sessions_per_week")
    if not isinstance(answer, str) or not answer.startswith("sessions_"):
        return None
    try:
        return int(answer.rsplit("_", 1)[1])
    except ValueError:
        return None


async def _fill_attempt_details(result: ModelResult, profile_id: str) -> None:
    """Достаёт из журнала, прошёл ли первый ответ модели и сколько было исправлений.

    Читается тот же журнал, что показывает админка (`ai_model_attempts`), а не
    отдельная запись прогона: сравнение должно опираться на данные, которые видит
    администратор, иначе отчёт и дашборд разошлись бы.
    """
    from sqlalchemy import text

    from src.infrastructure.persistence.postgres.db import get_session_factory

    async with get_session_factory()() as session:
        row = (
            await session.execute(
                text(
                    """
                    select a.metadata_json
                    from ai_audit_events a
                    join generation_jobs j on j.job_id = a.entity_id
                    where a.event_type = 'ai_model_attempts'
                      and j.profile_id = :profile_id
                    order by a.id desc limit 1
                    """
                ),
                {"profile_id": profile_id},
            )
        ).fetchone()
    if not row or not row[0]:
        return
    attempts = row[0].get("attempts") or []
    if not attempts:
        return
    last = attempts[-1]
    result.initial_valid = last.get("initial_valid")
    result.repair_attempts = last.get("repair_attempts")
    result.outcome = last.get("outcome")


# --- Точка входа ------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(description="Сравнение моделей на одном наборе анкет")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--admin-login", default=None)
    parser.add_argument("--admin-password", default=None)
    parser.add_argument("--report", default="", help="куда сохранить JSON-отчёт")
    parser.add_argument(
        "--models", nargs="*", default=None, help="какие model_id прогонять"
    )
    parser.add_argument(
        "--users", type=int, default=USERS_PER_MODEL, help="анкет на модель"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s"
    )
    for noisy in ("aiogram", "httpx", "src", "apps"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    from src.infrastructure.config import ADMIN_LOGIN, ADMIN_PASSWORD

    login = args.admin_login or ADMIN_LOGIN
    password = args.admin_password or ADMIN_PASSWORD
    if not login or not password:
        print("Нужны учётные данные администратора")
        return 2

    admin = await AdminClient.login(args.base_url, login, password)
    task = await admin.task()
    original_pks = [b["model_id"] for b in task["bindings"]]
    prompt_version = task["prompt_version"]
    if not original_pks:
        print("У задачи нет привязанных моделей — нечего восстанавливать после прогона")
        return 2

    endpoint_id = await _routerai_endpoint_id(admin)
    registry = await ensure_models(admin, endpoint_id)

    candidates = CANDIDATES
    if args.models:
        wanted = set(args.models)
        candidates = [c for c in CANDIDATES if c["model_id"] in wanted]

    users = (SCENARIOS + EXTRA_SCENARIOS)[: args.users]
    simulator = _build_simulator()
    summaries: list[ModelSummary] = []

    try:
        for index, candidate in enumerate(candidates):
            summary = ModelSummary(
                model_id=candidate["model_id"],
                display_name=candidate["display_name"],
                cost_per_generation=candidate["cost_per_generation"],
                note=candidate["note"],
            )
            # Смещение на 100 за модель: диапазон QA (990100-990199) для сравнения
            # тесен, а пересечение id между моделями превратило бы вторую анкету в
            # повторное прохождение существующего профиля.
            await run_model(
                summary,
                registry[candidate["model_id"]],
                users,
                admin,
                simulator,
                offset=1000 + index * 100,
            )
            summaries.append(summary)
        await _drain_background_tasks()
    finally:
        try:
            await restore_task(admin, original_pks, prompt_version)
        except Exception:  # noqa: BLE001 — сообщить, но не скрыть исходную ошибку
            logger.exception("Не удалось восстановить конфигурацию задачи")

    report = build_report(summaries)
    print_summary(summaries)
    if args.report:
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("Отчёт сохранён: %s", args.report)
    return 0


async def _routerai_endpoint_id(admin: AdminClient) -> int:
    for provider in await admin.providers():
        for endpoint in await admin.endpoints(provider["id"]):
            if "routerai" in endpoint["name"].lower():
                return endpoint["id"]
    raise RuntimeError("Эндпоинт routerai не найден в конфигурации")


def build_report(summaries: list[ModelSummary]) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "users_per_model": USERS_PER_MODEL,
        "models": [
            {
                "model_id": s.model_id,
                "display_name": s.display_name,
                "cost_per_generation": s.cost_per_generation,
                "note": s.note,
                "total": s.total,
                "ai_success": s.ai_success,
                "first_answer_valid": s.first_answer_valid,
                "repairs": s.repairs,
                "days_respected": s.days_respected,
                "fell_back": s.fell_back,
                "results": [asdict(r) for r in s.results],
            }
            for s in summaries
        ],
    }


def print_summary(summaries: list[ModelSummary]) -> None:
    print()
    print("=" * 96)
    print("СРАВНЕНИЕ МОДЕЛЕЙ")
    print("=" * 96)
    print(
        f"{'модель':34} {'₽/ген':>6} {'собрал ИИ':>10} {'1-й ответ':>10} "
        f"{'исправл.':>9} {'дней верно':>11} {'алгоритм':>9}"
    )
    for s in summaries:
        print(
            f"{s.model_id:34} {s.cost_per_generation:>6.2f} "
            f"{s.ai_success:>6}/{s.total:<3} {s.first_answer_valid:>6}/{s.total:<3} "
            f"{s.repairs:>9} {s.days_respected:>7}/{s.total:<3} {s.fell_back:>9}"
        )

    print()
    print("Замечания по анкетам, где что-то не совпало:")
    problems = False
    for s in summaries:
        for r in s.results:
            if r.error or r.days_match is False or r.generator != "ai":
                problems = True
                detail = r.error or (
                    f"дней {r.days_actual} вместо {r.days_requested}"
                    if r.days_match is False
                    else f"генератор={r.generator}"
                )
                print(f"  {s.model_id:34} {r.user_name:28} {detail}")
    if not problems:
        print("  нет")
    print()
    print(
        "Выборка мала (по "
        f"{USERS_PER_MODEL} анкеты на модель): прогон показывает грубые отличия, "
        "а не разницу в несколько процентов."
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
