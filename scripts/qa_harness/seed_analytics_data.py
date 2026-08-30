"""Наполнение аналитического дашборда тестовыми данными.

Отличие от `run_mass_test`: тот прогоняет все анкеты в каждом сценарии, чтобы
сравнить поведение системы при разной доступности моделей. Здесь задача другая —
получить выборку, на которой у дашборда заполнены все разрезы, поэтому каждая
анкета проходится один раз, а сценарий ей назначается.

Почему не 20 однотипных успешных генераций. Дашборд показывает распределение по
моделям, версиям инструкций, состояниям валидации и причинам fallback. На
однотипных генерациях эти разрезы остаются пустыми: раздел сравнения инструкций
нечем заполнить при одной версии, а «доля fallback» без единого fallback не
отличима от неработающего показателя.

Группы подобраны так, чтобы каждый разрез получил данные:

    8 анкет  все модели доступны, инструкция №1   базовая выборка
    4 анкеты основная модель отключена            fallback между моделями
    4 анкеты вторая версия инструкции             данные для сравнения промптов
    4 анкеты все модели отключены                 fallback на алгоритм

Порог достоверности аналитики — 10 генераций (`MIN_CONFIDENT_SAMPLE`), поэтому
общая выборка в 20 генераций делает проценты осмысленными, а группы по 4 честно
помечаются как недостаточные — это тоже проверка дашборда.

Запуск на staging:
    python -m scripts.qa_harness.seed_analytics_data --base-url http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from scripts.qa_harness.model_scenarios import (
    SCENARIO_ALL_MODELS,
    SCENARIO_FALLBACK_ONLY,
    SCENARIO_NO_MODELS,
    AdminClient,
    model_availability,
)
from scripts.qa_harness.run_mass_test import (
    GENERATION_TIMEOUT_SECONDS,
    POLL_INTERVAL_SECONDS,
    _build_simulator,
    _drain_background_tasks,
)
from scripts.qa_harness.scenarios import EXTRA_SCENARIOS, SCENARIOS
from scripts.qa_harness.user_simulator import ScriptedUser, TelegramUserSimulator

logger = logging.getLogger(__name__)

# Вторая версия инструкции: нужна, чтобы раздел сравнения промптов получил данные.
# Текст отличается содержательно, а не косметически, — сравнение косметических
# правок ничего не показало бы. Здесь усилены требования к структуре тренировки:
# порядок упражнений и запрет растяжек в основной части.
PROMPT_V2_NAME = "Строже про структуру тренировки"
PROMPT_V2_ADDITION = """

ДОПОЛНИТЕЛЬНЫЕ ТРЕБОВАНИЯ К СТРУКТУРЕ ТРЕНИРОВКИ:
1. Порядок упражнений внутри дня: сначала многосуставные со свободным весом
   (приседания, тяги, жимы), затем упражнения на тренажёрах, затем изолирующие.
   Тяжёлое движение, выполняемое уставшим, даёт меньший результат и выше риск
   травмы.
2. Растяжку и упражнения на мобильность не включай в основной план занятия: они
   относятся к разминке и заминке.
3. Число тренировочных дней должно точно совпадать с указанным в анкете
   количеством занятий в неделю.
"""


@dataclass
class GroupSpec:
    """Одна группа анкет: сценарий доступности моделей и версия инструкции."""

    title: str
    scenario: str
    users: list[ScriptedUser]
    # None — оставить версию, выбранную в настройках задачи.
    prompt_version: int | None = None


@dataclass
class SeedOutcome:
    """Что получилось по одной анкете."""

    group: str
    user_name: str
    profile_id: str | None = None
    display_number: str | None = None
    program_id: str | None = None
    generator: str | None = None
    model: str | None = None
    prompt_version: int | None = None
    days: int | None = None
    exercises: int | None = None
    error: str | None = None
    checks_failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.program_id is not None and self.error is None


# --- Управление конфигурацией задачи --------------------------------------------


async def ensure_second_prompt(admin: AdminClient) -> int:
    """Создаёт вторую версию инструкции, если её ещё нет.

    Идемпотентность важна: скрипт запускается повторно, и каждый запуск не должен
    добавлять новую версию — иначе сравнение промптов получит выборку, размазанную
    по десятку почти одинаковых инструкций.
    """
    listing = await admin.prompts()
    existing = next(
        (item for item in listing["items"] if item["name"] == PROMPT_V2_NAME), None
    )
    if existing is not None:
        logger.info(
            "Инструкция «%s» уже есть: версия №%s", PROMPT_V2_NAME, existing["version"]
        )
        return existing["version"]

    base_version = listing.get("active_version") or listing["items"][-1]["version"]
    base = await admin.prompt_by_version(base_version)
    created = await admin.create_prompt(
        {
            "task_type": "workout_generation",
            "name": PROMPT_V2_NAME,
            "system_prompt": base["system_prompt"] + PROMPT_V2_ADDITION,
            "user_template": base["user_template"],
        }
    )
    logger.info(
        "Создана инструкция «%s»: версия №%s (на основе №%s)",
        PROMPT_V2_NAME,
        created["version"],
        base_version,
    )
    return created["version"]


async def set_task_prompt(admin: AdminClient, version: int) -> None:
    """Переключает задачу на указанную версию инструкции.

    Остальные параметры задачи сохраняются как есть: `PUT` перезаписывает
    конфигурацию целиком, и передать только `prompt_version` нельзя — задача
    вернулась бы к значениям по умолчанию.
    """
    task = await admin.task()
    await admin.put_task(
        {
            "enabled": task["enabled"],
            "temperature": task["temperature"],
            "max_tokens": task["max_tokens"],
            "timeout_seconds": task["timeout_seconds"],
            "prompt_version": version,
            "model_ids": [b["model_id"] for b in task["bindings"]],
        }
    )
    logger.info("Задача переключена на инструкцию №%s", version)


def build_groups(prompt_v1: int, prompt_v2: int) -> list[GroupSpec]:
    """Распределяет 20 анкет по группам.

    Анкеты берутся из обоих наборов: первые восемь проверяют соблюдение
    требований пользователя, дополнительные двенадцать различаются по цели, полу,
    возрасту и месту занятий — аналитика группирует генерации по этим параметрам.
    """
    everyone = SCENARIOS + EXTRA_SCENARIOS
    if len(everyone) < 20:
        raise RuntimeError(
            f"Нужно 20 анкет, доступно {len(everyone)}: проверьте scenarios.py"
        )
    return [
        GroupSpec(
            "все модели, инструкция №%s" % prompt_v1,
            SCENARIO_ALL_MODELS,
            everyone[:8],
            prompt_v1,
        ),
        GroupSpec(
            "основная модель отключена",
            SCENARIO_FALLBACK_ONLY,
            everyone[8:12],
            prompt_v1,
        ),
        GroupSpec(
            "все модели, инструкция №%s" % prompt_v2,
            SCENARIO_ALL_MODELS,
            everyone[12:16],
            prompt_v2,
        ),
        GroupSpec(
            "модели отключены, алгоритм",
            SCENARIO_NO_MODELS,
            everyone[16:20],
            prompt_v1,
        ),
    ]


# --- Прогон -----------------------------------------------------------------------


async def run_group(
    group: GroupSpec, admin: AdminClient, simulator: TelegramUserSimulator
) -> list[SeedOutcome]:
    """Проходит анкеты группы при заданной конфигурации."""
    outcomes: list[SeedOutcome] = []
    if group.prompt_version is not None:
        await set_task_prompt(admin, group.prompt_version)

    async with model_availability(admin, group.scenario) as models:
        for state in models:
            logger.info(
                "  %s %-28s %s",
                "✓" if state.enabled else "✗",
                state.model_id,
                "основная" if state.is_primary else f"резерв №{state.priority}",
            )
        readiness = await admin.readiness()
        logger.info("  готовность задачи: ready=%s", readiness["ready"])

        for user in group.users:
            outcomes.append(await run_single(group, user, simulator))
    return outcomes


async def run_single(
    group: GroupSpec, user: ScriptedUser, simulator: TelegramUserSimulator
) -> SeedOutcome:
    outcome = SeedOutcome(group=group.title, user_name=user.name)
    logger.info("→ %s / %s", group.title, user.name)
    try:
        run = await simulator.run(user)
    except Exception as exc:  # noqa: BLE001 — сбой одной анкеты не отменяет прогон
        logger.exception("Анкета не прошла: %s", user.name)
        outcome.error = f"{type(exc).__name__}: {exc}"
        return outcome

    outcome.profile_id = run.profile_id
    outcome.display_number = run.display_number
    if not run.finalized or not run.profile_id:
        outcome.error = "анкета не финализирована"
        return outcome

    program = await await_program(run.profile_id)
    if program is None:
        outcome.error = "программа не появилась за отведённое время"
        return outcome

    outcome.program_id = program.program_id
    outcome.generator = program.generation.source.value
    outcome.model = program.generation.model
    outcome.prompt_version = program.generation.prompt_version
    outcome.days = len(program.training_days)
    outcome.exercises = sum(len(d.exercises) for d in program.training_days)
    logger.info(
        "  %s · генератор=%s модель=%s инструкция=%s дней=%s упражнений=%s",
        outcome.display_number or "—",
        outcome.generator,
        outcome.model or "—",
        outcome.prompt_version or "—",
        outcome.days,
        outcome.exercises,
    )
    return outcome


async def await_program(profile_id: str):
    """Ждёт результат автогенерации: она выполняется фоновой задачей.

    Ожидание идёт по operational-записи, а не по появлению программы: у отказа
    тоже есть исход, и без чтения job прогон не отличил бы «ещё считается» от
    «не смогли».
    """
    from src.infrastructure.persistence.postgres.db import get_session_factory
    from src.infrastructure.persistence.postgres.generation_job_repository import (
        GenerationJobRepository,
    )
    from src.infrastructure.persistence.postgres.program_repository import (
        PostgresProgramRepository,
    )

    session_factory = get_session_factory()
    jobs = GenerationJobRepository(session_factory)
    programs = PostgresProgramRepository(session_factory)

    deadline = asyncio.get_running_loop().time() + GENERATION_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        history = await jobs.list_for_profile(profile_id, limit=1)
        if history and history[0].status.value in ("succeeded", "failed"):
            found = await programs.list_for_profile(profile_id)
            return found[0] if found else None
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return None


# --- Точка входа ------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Наполнение аналитического дашборда тестовыми данными"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--admin-login", default=None, help="по умолчанию из ADMIN_LOGIN")
    parser.add_argument(
        "--admin-password", default=None, help="по умолчанию из ADMIN_PASSWORD"
    )
    parser.add_argument("--report", default="", help="куда сохранить JSON-отчёт")
    parser.add_argument(
        "--groups",
        nargs="*",
        default=None,
        help="номера групп (1-4); по умолчанию все",
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
        print("Нужны учётные данные администратора: --admin-login/--admin-password")
        return 2

    admin = await AdminClient.login(args.base_url, login, password)

    task = await admin.task()
    prompt_v1 = task["prompt_version"]
    if prompt_v1 is None:
        print("У задачи не выбрана инструкция — сначала настройте её в админке")
        return 2

    prompt_v2 = await ensure_second_prompt(admin)
    groups = build_groups(prompt_v1, prompt_v2)
    if args.groups:
        selected = {int(n) for n in args.groups}
        groups = [g for i, g in enumerate(groups, start=1) if i in selected]

    simulator = _build_simulator()
    outcomes: list[SeedOutcome] = []
    try:
        for group in groups:
            logger.info("=" * 78)
            logger.info("Группа: %s (%s анкет)", group.title, len(group.users))
            outcomes.extend(await run_group(group, admin, simulator))
        await _drain_background_tasks()
    finally:
        # Конфигурация задачи возвращается к исходной инструкции всегда: staging
        # используется не только этим скриптом, и оставить задачу на тестовой
        # версии значило бы менять поведение генерации для всех.
        try:
            await set_task_prompt(admin, prompt_v1)
        except Exception:  # noqa: BLE001 — сообщить, но не скрыть исходную ошибку
            logger.exception("Не удалось вернуть инструкцию №%s", prompt_v1)

    report = build_report(outcomes)
    print_summary(report, outcomes)
    if args.report:
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("Отчёт сохранён: %s", args.report)
    return 0 if report["failed"] == 0 else 1


def build_report(outcomes: list[SeedOutcome]) -> dict:
    by_group: dict[str, dict] = {}
    for outcome in outcomes:
        entry = by_group.setdefault(
            outcome.group,
            {"total": 0, "ok": 0, "ai": 0, "deterministic": 0, "models": set(),
             "prompts": set()},
        )
        entry["total"] += 1
        if outcome.ok:
            entry["ok"] += 1
        if outcome.generator == "ai":
            entry["ai"] += 1
        elif outcome.generator == "deterministic":
            entry["deterministic"] += 1
        if outcome.model:
            entry["models"].add(outcome.model)
        if outcome.prompt_version:
            entry["prompts"].add(outcome.prompt_version)
    for entry in by_group.values():
        entry["models"] = sorted(entry["models"])
        entry["prompts"] = sorted(entry["prompts"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(outcomes),
        "ok": sum(1 for o in outcomes if o.ok),
        "failed": sum(1 for o in outcomes if not o.ok),
        "groups": by_group,
        "outcomes": [asdict(o) for o in outcomes],
    }


def print_summary(report: dict, outcomes: list[SeedOutcome]) -> None:
    print()
    print("=" * 78)
    print(f"ИТОГ: {report['ok']} из {report['total']} анкет с программой")
    print("=" * 78)
    for title, data in report["groups"].items():
        print(f"\n{title}")
        print(
            f"  успешно {data['ok']}/{data['total']} · ИИ {data['ai']} · "
            f"алгоритм {data['deterministic']}"
        )
        if data["models"]:
            print(f"  модели: {', '.join(data['models'])}")
        if data["prompts"]:
            print(f"  инструкции: {', '.join('№' + str(p) for p in data['prompts'])}")

    problems = [o for o in outcomes if not o.ok]
    if problems:
        print("\nНе получили программу:")
        for outcome in problems:
            print(f"  [{outcome.group}] {outcome.user_name}: {outcome.error}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
