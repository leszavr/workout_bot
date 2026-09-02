"""Статическая проверка контрактов перед развёртыванием.

Запускается в CI без доступа к staging: проверяется согласованность кода и
`deploy/release-manifest.json`, который читает deployment tooling. Живую
совместимость развёрнутых компонентов проверяет `/internal/v1/deployment-safety`
в pipeline развёртывания — CI о существовании развёрнутых экземпляров не знает и
обращаться к ним не должен.

Зачем отдельный скрипт, если есть тесты. Тесты проверяют манифест как данные;
здесь проверяются правила деплоя, которые тестом выразить неудобно: у каждого
компонента с контрактом должно быть объявленное требование, версии не обязаны
совпадать, а версия релиза обязана совпадать с версией Backend. Скрипт печатает
человекочитаемый отчёт и возвращает ненулевой код — это и есть gate.

    python -m scripts.check_contracts
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from src.domain.components import (
    BACKEND_CONTRACT_VERSION,
    BACKEND_SUPPORTED_CONTRACTS,
    COMPONENT_REQUIREMENTS,
    ComponentType,
)
from src.domain.telegram_contract import TELEGRAM_CONTRACT_VERSION
from src.version import APP_VERSION, GATEWAY_VERSION, WORKER_VERSION

MANIFEST = Path(__file__).resolve().parents[1] / "deploy" / "release-manifest.json"

# Компоненты, у которых есть версионированный контракт с Backend. Admin Web сюда
# не входит: он ходит в публичный Admin API и версии контракта не имеет.
CONTRACT_COMPONENTS = {
    "telegram_gateway": ComponentType.TELEGRAM_GATEWAY,
    "worker": ComponentType.WORKER,
}

CODE_VERSIONS = {
    "backend": APP_VERSION,
    "admin_web": APP_VERSION,
    "telegram_gateway": GATEWAY_VERSION,
    "worker": WORKER_VERSION,
}


def check() -> list[str]:
    """Возвращает список расхождений. Пустой список — всё согласовано."""
    problems: list[str] = []
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    components = manifest["components"]
    requirements = manifest["requirements"]

    backend = components["backend"]
    if backend["contract"] != BACKEND_CONTRACT_VERSION:
        problems.append(
            f"backend.contract={backend['contract']} != код {BACKEND_CONTRACT_VERSION}"
        )
    if tuple(backend["supported_contracts"]) != BACKEND_SUPPORTED_CONTRACTS:
        problems.append(
            "backend.supported_contracts не совпадает с BACKEND_SUPPORTED_CONTRACTS"
        )
    if manifest["release"] != APP_VERSION:
        problems.append(
            f"release={manifest['release']} != версия Backend {APP_VERSION}"
        )

    for name, declared_version in CODE_VERSIONS.items():
        if name not in components:
            problems.append(f"{name}: компонент отсутствует в манифесте")
            continue
        if components[name]["version"] != declared_version:
            problems.append(
                f"{name}.version={components[name]['version']} != код {declared_version}"
            )

    for name, component_type in CONTRACT_COMPONENTS.items():
        if name not in requirements:
            problems.append(
                f"{name}: требование не объявлено — deployment gate ответит UNKNOWN"
            )
            continue
        requirement = COMPONENT_REQUIREMENTS[component_type]
        declared = requirements[name]
        if tuple(declared["supported_contracts"]) != requirement.supported_contracts:
            problems.append(f"{name}.supported_contracts расходится с кодом")
        if declared["min_version"] != requirement.min_version:
            problems.append(
                f"{name}.min_version={declared['min_version']} != "
                f"код {requirement.min_version}"
            )
        # Контракт компонента должен поддерживаться Backend: иначе развёртывание
        # мгновенно делает его несовместимым.
        contract = components[name].get("requires_backend_contract")
        if contract is not None and contract not in BACKEND_SUPPORTED_CONTRACTS:
            problems.append(
                f"{name} требует contract={contract}, "
                f"Backend поддерживает {list(BACKEND_SUPPORTED_CONTRACTS)}"
            )

    # Требования, объявленные только в коде, оставили бы deployment tooling с
    # устаревшим манифестом.
    in_code = {t.value for t in COMPONENT_REQUIREMENTS}
    if in_code != set(requirements):
        problems.append(
            f"требования расходятся: код {sorted(in_code)}, "
            f"манифест {sorted(requirements)}"
        )

    if TELEGRAM_CONTRACT_VERSION not in BACKEND_SUPPORTED_CONTRACTS:
        problems.append(
            f"контракт Telegram {TELEGRAM_CONTRACT_VERSION} не поддерживается Backend"
        )

    return problems


def main() -> int:
    problems = check()
    if problems:
        print("Контракты не согласованы:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("Контракты согласованы:")
    print(f"  backend           {APP_VERSION} (contract {BACKEND_CONTRACT_VERSION})")
    print(f"  telegram_gateway  {GATEWAY_VERSION} (contract {TELEGRAM_CONTRACT_VERSION})")
    print(f"  worker            {WORKER_VERSION}")
    print(f"  поддерживаемые контракты Backend: {list(BACKEND_SUPPORTED_CONTRACTS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
