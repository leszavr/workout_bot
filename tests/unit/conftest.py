"""Общие фикстуры unit-тестов.

Dispatcher собирается один раз на всю сессию: роутеры aiogram — модульные
singletons, и повторный `include_router` того же роутера в другой dispatcher
падает с `Router is already attached`. Поэтому все тесты, которым нужен
собранный gateway, работают с одним экземпляром.
"""
from __future__ import annotations

import pytest
from aiogram import Dispatcher, Router
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

from apps.telegram_gateway.main import build_dispatcher


@pytest.fixture(scope="session")
def dispatcher() -> Dispatcher:
    return build_dispatcher(
        storage=MemoryStorage(), events_isolation=SimpleEventIsolation()
    )


@pytest.fixture(scope="session")
def routers(dispatcher: Dispatcher) -> list[Router]:
    """Роутеры в порядке распространения события: dispatcher, затем вложенные."""
    ordered: list[Router] = []

    def walk(router: Router) -> None:
        ordered.append(router)
        for child in router.sub_routers:
            walk(child)

    walk(dispatcher)
    return ordered
