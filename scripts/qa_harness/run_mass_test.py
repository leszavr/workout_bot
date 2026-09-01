"""Массовый прогон: анкеты в Telegram → генерация программ → проверка качества.

Запуск (на staging, из каталога проекта):
    python -m scripts.qa_harness.run_mass_test --base-url http://127.0.0.1:8000

Что делает. Для каждого из трёх сценариев доступности моделей проходит все восемь
анкет через настоящий Dispatcher бота, ждёт автогенерацию и проверяет полученную
программу восемью формальными проверками.

Почему анкеты проходятся заново в каждом сценарии, а не один раз с повторной
генерацией через Admin API: админский запрос идёт с `allow_fallback=False` —
явно выбранный ИИ не подменяется алгоритмом, иначе администратор не узнает об
отказе. Поэтому через Admin API сценарий «моделей нет» дал бы 502, а не программу
от алгоритма, и главное в нём осталось бы непроверенным. Автогенерация после
финализации работает с `allow_fallback=True` — это и есть путь реального
пользователя.

Расход обращений к моделям: 8 (все модели) + 8 (только резервные) + 0 (моделей
нет: readiness gate останавливает до вызова провайдера) = 16 на 24 генерации.

Данные остаются в базе: анкеты и программы должны быть видны в админке как
созданные обычными пользователями. Очистка — отдельной командой `--cleanup`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Dispatcher

from scripts.qa_harness import quality
from scripts.qa_harness.fake_telegram import FakeTelegramSession
from scripts.qa_harness.model_scenarios import (
    SCENARIO_ALL_MODELS,
    SCENARIO_FALLBACK_ONLY,
    SCENARIO_NO_MODELS,
    SCENARIO_TITLES,
    AdminClient,
    ModelState,
    model_availability,
)
from scripts.qa_harness.scenarios import (
    QA_USER_ID_MAX,
    QA_USER_ID_MIN,
    SCENARIOS,
)
from scripts.qa_harness.user_simulator import (
    QuestionnaireRun,
    ScriptedUser,
    TelegramUserSimulator,
)

logger = logging.getLogger(__name__)

SCENARIO_ORDER = [SCENARIO_ALL_MODELS, SCENARIO_FALLBACK_ONLY, SCENARIO_NO_MODELS]

# Смещение telegram_user_id по сценариям: у каждого прогона свои «пользователи»,
# иначе повторное прохождение попало бы в существующий профиль.
SCENARIO_USER_OFFSET = {
    SCENARIO_ALL_MODELS: 0,
    SCENARIO_FALLBACK_ONLY: 20,
    SCENARIO_NO_MODELS: 40,
}

# Автогенерация запускается фоновой задачей после финализации, поэтому программу
# нужно дождаться.
#
# Ожидание с запасом к бюджету генерации: бюджет считается от числа кандидатов
# (`len(candidates) × 80 с`), и при шести моделях это 480 секунд. Плюс время на
# подготовку промпта, запись программы и доставку. При коротком ожидании прогон
# записывал «программа не появилась» там, где генерация продолжалась и позже
# завершалась успешно — то есть измерял таймаут прогона, а не поведение системы.
GENERATION_TIMEOUT_SECONDS = 900
POLL_INTERVAL_SECONDS = 3

# Сколько ждать завершения фоновых задач pipeline перед выходом. Без этого
# ожидания процесс закрывает event loop, задачи получают CancelledError, и job
# закрывается кодом `unexpected_error` с пустым сообщением — отказ, которого в
# действительности не было.
BACKGROUND_DRAIN_SECONDS = 120


@dataclass
class ProgramOutcome:
    """Что получилось по одной анкете в одном сценарии."""

    scenario: str
    user_name: str
    profile_id: str | None
    display_number: str | None
    finalized: bool
    program_id: str | None = None
    program_version: int | None = None
    generator: str | None = None
    model: str | None = None
    fallback_used: bool | None = None
    fallback_reason: str | None = None
    pool_size: int | None = None
    checks: list[dict] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.program_id is not None and not self.failures and self.error is None


async def main() -> int:
    parser = argparse.ArgumentParser(description="Массовый прогон генерации программ")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--admin-login", default=None, help="по умолчанию из ADMIN_LOGIN")
    parser.add_argument("--admin-password", default=None, help="по умолчанию из ADMIN_PASSWORD")
    parser.add_argument(
        "--scenarios",
        nargs="*",
        default=SCENARIO_ORDER,
        help=f"какие сценарии прогонять (по умолчанию все): {', '.join(SCENARIO_ORDER)}",
    )
    parser.add_argument(
        "--users", type=int, default=0, help="ограничить число анкет (0 — все восемь)"
    )
    parser.add_argument("--report", default="", help="куда сохранить JSON-отчёт")
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="удалить данные прошлых прогонов и выйти, ничего не генерируя",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s"
    )
    # Логи приложения на INFO забивают вывод прогона: интересен результат, а не
    # каждый шаг анкеты.
    for noisy in ("aiogram", "httpx", "src", "apps"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.cleanup:
        await cleanup_qa_data()
        return 0

    from src.infrastructure.config import ADMIN_LOGIN, ADMIN_PASSWORD

    login = args.admin_login or ADMIN_LOGIN
    password = args.admin_password or ADMIN_PASSWORD
    if not login or not password:
        print("Нужны учётные данные администратора: --admin-login/--admin-password")
        return 2

    admin = await AdminClient.login(args.base_url, login, password)
    users = SCENARIOS[: args.users] if args.users else SCENARIOS

    # Диспетчер и симулятор создаются один раз на весь прогон. Роутеры aiogram —
    # модульные singletons: повторный `build_dispatcher` для следующего сценария
    # падает с «Router is already attached». Независимость сценариев обеспечивает
    # смещение telegram_user_id, а не отдельный диспетчер.
    simulator = _build_simulator()

    outcomes: list[ProgramOutcome] = []
    for scenario in args.scenarios:
        logger.info("=" * 78)
        logger.info("Сценарий: %s — %s", scenario, SCENARIO_TITLES[scenario])
        async with model_availability(admin, scenario) as models:
            _log_models(models)
            readiness = await admin.readiness()
            logger.info("Готовность задачи: ready=%s", readiness["ready"])
            outcomes.extend(await _run_scenario(scenario, users, simulator))

    await _drain_background_tasks()

    report = _build_report(outcomes)
    _print_summary(report, outcomes)

    if args.report:
        path = Path(args.report)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("Отчёт сохранён: %s", path)

    return 0 if report["failed"] == 0 else 1


def _build_simulator() -> TelegramUserSimulator:
    """Собирает диспетчер и имитатор один раз на прогон.

    Хранилище — in-memory: состояние анкеты нужно только на время прохождения, и
    Redis staging не должен получать мусор от прогона.
    """
    from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

    from apps.telegram_gateway.main import build_dispatcher

    dispatcher: Dispatcher = build_dispatcher(
        storage=MemoryStorage(), events_isolation=SimpleEventIsolation()
    )
    return TelegramUserSimulator(dispatcher, FakeTelegramSession())


async def _drain_background_tasks() -> None:
    """Даёт фоновым задачам pipeline завершиться до выхода из процесса.

    Автогенерация выполняется фоновой задачей. Если процесс завершится раньше,
    задача получит CancelledError, и job закроется кодом `unexpected_error` с
    пустым сообщением — в журнале появится отказ, которого не было.
    """
    from apps.telegram_gateway.handlers.review import _BACKGROUND_TASKS

    pending = {task for task in _BACKGROUND_TASKS if not task.done()}
    if not pending:
        return
    logger.info("Ожидаю завершения фоновых задач: %s", len(pending))
    done, still_pending = await asyncio.wait(
        pending, timeout=BACKGROUND_DRAIN_SECONDS
    )
    if still_pending:
        logger.warning(
            "Не дождался %s фоновых задач: их job закроется как отменённый",
            len(still_pending),
        )


async def _run_scenario(
    scenario: str, users: list[ScriptedUser], simulator: TelegramUserSimulator
) -> list[ProgramOutcome]:
    """Проходит анкеты и собирает результат по каждой."""
    offset = SCENARIO_USER_OFFSET[scenario]
    outcomes: list[ProgramOutcome] = []
    for user in users:
        scoped = ScriptedUser(
            name=user.name,
            telegram_user_id=user.telegram_user_id + offset,
            answers=user.answers,
            expectations=user.expectations,
        )
        outcomes.append(await _run_single(scenario, scoped, simulator))
    return outcomes


async def _run_single(
    scenario: str, user: ScriptedUser, simulator: TelegramUserSimulator
) -> ProgramOutcome:
    logger.info("→ %s / %s", scenario, user.name)
    try:
        run: QuestionnaireRun = await simulator.run(user)
    except Exception as exc:  # noqa: BLE001 — сбой одной анкеты не отменяет прогон
        logger.exception("Анкета не прошла: %s", user.name)
        return ProgramOutcome(
            scenario=scenario,
            user_name=user.name,
            profile_id=None,
            display_number=None,
            finalized=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    outcome = ProgramOutcome(
        scenario=scenario,
        user_name=user.name,
        profile_id=run.profile_id,
        display_number=run.display_number,
        finalized=run.finalized,
    )
    if not run.finalized or not run.profile_id:
        outcome.error = "анкета не финализирована"
        return outcome

    program = await _await_program(run.profile_id)
    if program is None:
        outcome.error = "программа не появилась за отведённое время"
        return outcome

    await _evaluate(outcome, run.profile_id, program)
    _log_outcome(outcome)
    return outcome


async def _await_program(profile_id: str):
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


async def _evaluate(outcome: ProgramOutcome, profile_id: str, program) -> None:
    """Пересобирает пул под профиль и прогоняет проверки качества.

    Пул строится теми же фильтром и safety-контуром, что при генерации: сравнение
    с собственной копией правил проверяло бы представление автора теста, а не
    поведение системы.
    """
    from src.application.programs.filtering import ExerciseFilter
    from src.application.programs.safety import SafetyEngine
    from src.infrastructure.persistence.postgres.db import get_session_factory
    from src.infrastructure.persistence.postgres.exercise_repository import (
        ExerciseRepository,
    )
    from src.infrastructure.persistence.postgres.profile_repository import (
        PostgresProfileRepository,
    )

    session_factory = get_session_factory()
    profile = await PostgresProfileRepository(session_factory).get(profile_id)
    if profile is None:
        outcome.error = "профиль не найден в базе"
        return

    # Тот же лимит, что у оркестратора: пул должен строиться из того же
    # каталога, иначе проверка сравнивала бы программу с другим набором.
    from src.application.programs.orchestrator import CATALOG_FETCH_LIMIT

    catalog = await ExerciseRepository(session_factory).list(limit=CATALOG_FETCH_LIMIT)
    pool_candidates = await ExerciseFilter().select_candidates(profile, catalog)
    pool = SafetyEngine().apply(profile, pool_candidates.included)

    outcome.program_id = program.program_id
    outcome.program_version = program.version
    outcome.generator = program.generation.source.value
    outcome.model = program.generation.model
    outcome.pool_size = len(pool.allowed)

    report = quality.evaluate(
        program=program,
        profile=profile,
        pool=pool,
        catalog={e.external_id: e for e in catalog},
    )
    outcome.checks = [asdict(check) for check in report.checks]
    outcome.failures = [f"{c.name}: {c.detail}" for c in report.failures]

    # Признак и причина fallback берутся из метаданных самой программы: они
    # сохраняются при генерации, поэтому не зависят от того, успел ли прогон
    # прочитать журнал администратора.
    outcome.fallback_used = program.generation.fallback_used
    outcome.fallback_reason = (
        program.generation.fallback_reason_code or program.generation.fallback_reason
    )


def _build_report(outcomes: list[ProgramOutcome]) -> dict:
    by_scenario: dict[str, dict] = {}
    for scenario in SCENARIO_ORDER:
        subset = [o for o in outcomes if o.scenario == scenario]
        if not subset:
            continue
        by_scenario[scenario] = {
            "title": SCENARIO_TITLES[scenario],
            "total": len(subset),
            "passed": sum(1 for o in subset if o.passed),
            "ai_generated": sum(1 for o in subset if o.generator == "ai"),
            "deterministic": sum(1 for o in subset if o.generator == "deterministic"),
            "no_program": sum(1 for o in subset if o.program_id is None),
            "models_used": sorted({o.model for o in subset if o.model}),
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(outcomes),
        "passed": sum(1 for o in outcomes if o.passed),
        "failed": sum(1 for o in outcomes if not o.passed),
        "scenarios": by_scenario,
        "outcomes": [asdict(o) for o in outcomes],
    }


def _print_summary(report: dict, outcomes: list[ProgramOutcome]) -> None:
    print()
    print("=" * 78)
    print(f"ИТОГ: {report['passed']} из {report['total']} прогонов без замечаний")
    print("=" * 78)
    for scenario, data in report["scenarios"].items():
        print(f"\n{scenario} — {data['title']}")
        print(
            f"  успешно {data['passed']}/{data['total']} · "
            f"ИИ {data['ai_generated']} · алгоритм {data['deterministic']} · "
            f"без программы {data['no_program']}"
        )
        if data["models_used"]:
            print(f"  модели: {', '.join(data['models_used'])}")

    problems = [o for o in outcomes if not o.passed]
    if problems:
        print("\nЗамечания:")
        for outcome in problems:
            head = f"  [{outcome.scenario}] {outcome.user_name}"
            if outcome.error:
                print(f"{head}: {outcome.error}")
            for failure in outcome.failures:
                print(f"{head}: {failure}")


def _log_models(models: list[ModelState]) -> None:
    for state in models:
        role = "основная" if state.is_primary else f"резерв №{state.priority}"
        logger.info(
            "  %s %-28s %s", "✓" if state.enabled else "✗", state.model_id, role
        )


def _log_outcome(outcome: ProgramOutcome) -> None:
    verdict = "OK" if outcome.passed else "ЗАМЕЧАНИЯ"
    logger.info(
        "  %s · %s · генератор=%s модель=%s пул=%s",
        verdict,
        outcome.display_number or "—",
        outcome.generator,
        outcome.model or "—",
        outcome.pool_size,
    )
    for failure in outcome.failures:
        logger.warning("    ! %s", failure)


async def cleanup_qa_data() -> None:
    """Удаляет анкеты и программы тестовых пользователей.

    Только диапазон telegram_user_id прогона: реальные пользователи не
    затрагиваются. Порядок удаления задан внешними ключами — сначала зависимые
    записи, затем анкета и пользователь.
    """
    from sqlalchemy import text

    from src.infrastructure.persistence.postgres.db import get_session_factory

    params = {"lo": str(QA_USER_ID_MIN), "hi": str(QA_USER_ID_MAX)}
    profiles_of_qa = (
        "select profile_id from profiles where user_id in "
        "(select id from users where telegram_user_id between :lo and :hi)"
    )
    statements = [
        f"delete from generation_jobs where profile_id in ({profiles_of_qa})",
        f"delete from program_deliveries where profile_id in ({profiles_of_qa})",
        f"delete from workout_programs where profile_id in ({profiles_of_qa})",
        "delete from consents where user_id in "
        "(select id from users where telegram_user_id between :lo and :hi)",
        "delete from profiles where user_id in "
        "(select id from users where telegram_user_id between :lo and :hi)",
        "delete from users where telegram_user_id between :lo and :hi",
    ]
    async with get_session_factory()() as session:
        async with session.begin():
            for statement in statements:
                result = await session.execute(text(statement), params)
                logger.info("%s → %s строк", statement.split(" where ")[0], result.rowcount)
    logger.info("Данные прогона удалены")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
