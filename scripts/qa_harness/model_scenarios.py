"""Сценарии доступности AI-моделей через Admin API.

Требование прогона — проверить три состояния: все модели доступны, основная
недоступна и работает резервная, ни одна недоступна и программу собирает
алгоритм.

Недоступность имитируется отключением модели через Admin API — так же, как это
сделал бы администратор. Вариант «подменить URL на неверный» дал бы другое
явление: эндпоинт, который не отвечает, а не модель, которую не выбрали. Разница
существенна, потому что путь в коде разный: отключённая модель не попадает в
цепочку кандидатов вовсе, а неотвечающий эндпоинт даёт транспортный отказ и
запускает перебор.

Конфигурация задачи восстанавливается всегда, включая аварийный выход: staging
используется не только этим прогоном, и оставить задачу выключенной значило бы
сломать окружение до следующего вмешательства.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Три состояния доступности, которые требуется проверить.
SCENARIO_ALL_MODELS = "all_models"
SCENARIO_FALLBACK_ONLY = "fallback_only"
SCENARIO_NO_MODELS = "no_models"

SCENARIO_TITLES = {
    SCENARIO_ALL_MODELS: "все модели доступны",
    SCENARIO_FALLBACK_ONLY: "основная модель отключена, доступны резервные",
    SCENARIO_NO_MODELS: "все модели отключены",
}


@dataclass
class ModelState:
    """Состояние одной модели задачи до вмешательства."""

    model_pk: int
    model_id: str
    enabled: bool
    priority: int
    is_primary: bool


class AdminClient:
    """Тонкий клиент Admin API: только то, что нужно прогону."""

    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}

    @classmethod
    async def login(cls, base_url: str, login: str, password: str) -> AdminClient:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/api/v1/auth/login",
                json={"login": login, "password": password},
            )
            response.raise_for_status()
            return cls(base_url, response.json()["access_token"])

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.request(
                method, f"{self._base}{path}", headers=self._headers, **kwargs
            )
            response.raise_for_status()
            return response

    async def task(self, task_type: str = "workout_generation") -> dict:
        return (await self._request("GET", f"/api/v1/admin/ai/tasks/{task_type}")).json()

    async def endpoints(self, provider_id: int) -> list[dict]:
        response = await self._request(
            "GET", f"/api/v1/admin/ai/providers/{provider_id}/endpoints"
        )
        return response.json()["items"]

    async def providers(self) -> list[dict]:
        return (await self._request("GET", "/api/v1/admin/ai/providers")).json()["items"]

    async def models(self, endpoint_id: int) -> list[dict]:
        response = await self._request(
            "GET", f"/api/v1/admin/ai/endpoints/{endpoint_id}/models"
        )
        return response.json()["items"]

    async def set_model_enabled(self, model_pk: int, enabled: bool) -> None:
        await self._request(
            "PATCH", f"/api/v1/admin/ai/models/{model_pk}", json={"enabled": enabled}
        )

    async def put_task(self, body: dict, task_type: str = "workout_generation") -> dict:
        response = await self._request(
            "PUT", f"/api/v1/admin/ai/tasks/{task_type}", json=body
        )
        return response.json()

    async def prompts(self, task_type: str = "workout_generation") -> dict:
        return (
            await self._request("GET", f"/api/v1/admin/ai/prompts/{task_type}")
        ).json()

    async def prompt(self, prompt_id: int) -> dict:
        return (
            await self._request("GET", f"/api/v1/admin/ai/prompts/detail/{prompt_id}")
        ).json()

    async def prompt_by_version(
        self, version: int, task_type: str = "workout_generation"
    ) -> dict:
        """Полный текст инструкции по номеру версии.

        Список отдаёт превью, а не текст: детальный ответ приходится запрашивать
        отдельно по id.
        """
        listing = await self.prompts(task_type)
        item = next((i for i in listing["items"] if i["version"] == version), None)
        if item is None:
            raise RuntimeError(f"Инструкции №{version} нет у задачи {task_type}")
        return await self.prompt(item["id"])

    async def create_model(self, endpoint_id: int, body: dict) -> dict:
        return (
            await self._request(
                "POST", f"/api/v1/admin/ai/endpoints/{endpoint_id}/models", json=body
            )
        ).json()

    async def create_prompt(self, body: dict) -> dict:
        return (
            await self._request("POST", "/api/v1/admin/ai/prompts", json=body)
        ).json()

    async def readiness(self, task_type: str = "workout_generation") -> dict:
        return (
            await self._request("GET", f"/api/v1/admin/ai/readiness?task_type={task_type}")
        ).json()

    async def model_attempts(self) -> list[dict]:
        return (await self._request("GET", "/api/v1/admin/ai/model-attempts")).json()[
            "items"
        ]

    async def fallback_events(self) -> list[dict]:
        return (await self._request("GET", "/api/v1/admin/ai/fallback-events")).json()[
            "items"
        ]


async def collect_task_models(admin: AdminClient) -> list[ModelState]:
    """Модели задачи в порядке приоритета, с их текущим состоянием.

    Состояние снимается до вмешательства: восстанавливать нужно ровно то, что
    было, а не «всё включить».
    """
    task = await admin.task()
    bindings = {b["model_id"]: b for b in task["bindings"]}

    by_pk: dict[int, dict] = {}
    for provider in await admin.providers():
        for endpoint in await admin.endpoints(provider["id"]):
            for model in await admin.models(endpoint["id"]):
                by_pk[model["id"]] = model

    states: list[ModelState] = []
    for model_pk, binding in bindings.items():
        model = by_pk.get(model_pk)
        if model is None:
            # Привязка на модель, которой нет: это дефект конфигурации, но не
            # повод останавливать прогон — он будет виден в отчёте readiness.
            logger.warning("Модель pk=%s привязана к задаче, но не найдена", model_pk)
            continue
        states.append(
            ModelState(
                model_pk=model_pk,
                model_id=model["model_id"],
                enabled=model["enabled"],
                priority=binding["priority"],
                is_primary=binding["is_primary"],
            )
        )
    return sorted(states, key=lambda s: s.priority)


@asynccontextmanager
async def model_availability(admin: AdminClient, scenario: str):
    """Приводит модели задачи в нужное состояние и возвращает конфигурацию назад.

    Возвращает список моделей с фактическим состоянием внутри сценария, чтобы
    отчёт мог сказать, какая модель была доступна, а не предполагать это.
    """
    original = await collect_task_models(admin)
    if not original:
        raise RuntimeError(
            "У задачи workout_generation нет привязанных моделей — "
            "сценарии доступности проверять нечем"
        )

    if scenario == SCENARIO_ALL_MODELS:
        target = {state.model_pk: True for state in original}
    elif scenario == SCENARIO_FALLBACK_ONLY:
        # Отключается только основная: резервные должны подхватить работу.
        target = {state.model_pk: not state.is_primary for state in original}
        if all(not value for value in target.values()):
            raise RuntimeError(
                "У задачи только основная модель — сценарий с резервными невозможен"
            )
    elif scenario == SCENARIO_NO_MODELS:
        target = {state.model_pk: False for state in original}
    else:
        raise ValueError(f"Неизвестный сценарий доступности: {scenario}")

    changed: list[int] = []
    try:
        for state in original:
            desired = target[state.model_pk]
            if state.enabled != desired:
                await admin.set_model_enabled(state.model_pk, desired)
                changed.append(state.model_pk)
        yield [
            ModelState(
                model_pk=s.model_pk,
                model_id=s.model_id,
                enabled=target[s.model_pk],
                priority=s.priority,
                is_primary=s.is_primary,
            )
            for s in original
        ]
    finally:
        # Восстановление обязательно и в случае ошибки: staging используется не
        # только этим прогоном.
        for state in original:
            if state.model_pk in changed:
                try:
                    await admin.set_model_enabled(state.model_pk, state.enabled)
                except Exception:  # noqa: BLE001 — сообщить, но не скрыть исходную ошибку
                    logger.exception(
                        "Не удалось вернуть модель pk=%s в состояние enabled=%s",
                        state.model_pk,
                        state.enabled,
                    )
