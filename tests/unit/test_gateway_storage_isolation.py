"""Изоляция EU Gateway от хранилищ RU: архитектурный regression-тест.

Gateway работает в EU-сегменте, где нельзя хранить пользовательские данные, и
единственный его канал к данным — internal API Backend. Значит у него не должно
быть ни клиента, ни адреса, ни credentials ни одного хранилища: Redis,
PostgreSQL, MinIO.

Почему статическая проверка, а не только behavioural-тесты. Вернуть прямой
доступ можно одной строкой в compose или одним импортом — кодом, который просто
не покрыт сценарием. Поведенческий тест этого не заметит: он проверяет, что
диалог работает, а работать он будет и с лишним подключением. Поэтому здесь
фиксируется само отсутствие зависимости — в коде, в графе импортов, в compose и
в env-примерах.

Проверяется и обратная сторона: разрешённые направления (Backend internal API и
Telegram Bot API) должны остаться, иначе «изоляция» достигалась бы отключением
самого шлюза.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = PROJECT_ROOT / "apps" / "telegram_gateway"
GATEWAY_ENTRYPOINT = "apps.telegram_gateway.main"

# Признаки прямого доступа к хранилищу. Проверяются как имена в исходниках
# Gateway: и импорт, и обращение к переменной окружения дают вход в хранилище.
STORAGE_MARKERS = {
    "REDIS_URL": "адрес Redis",
    "RedisStorage": "FSM-хранилище в Redis",
    "RedisEventIsolation": "блокировка обновлений в Redis",
    "DATABASE_URL": "строка подключения PostgreSQL",
    "create_async_engine": "движок SQLAlchemy",
    "async_sessionmaker": "фабрика сессий SQLAlchemy",
    "MINIO_ENDPOINT": "адрес MinIO",
    "MINIO_ACCESS_KEY": "ключ MinIO",
    "MINIO_SECRET_KEY": "секрет MinIO",
    "MEDIA_BUCKET": "бакет медиа",
}

# Пакеты-клиенты хранилищ. Их не должно быть в графе импортов процесса Gateway:
# наличие клиента в процессе — это уже возможность подключения.
STORAGE_PACKAGES = {"redis", "sqlalchemy", "asyncpg", "psycopg", "psycopg2", "minio"}

# Переменные окружения, которых не должно быть у Gateway ни в одном compose и ни
# в одном env-примере. Секретов здесь нет — только имена ключей.
FORBIDDEN_GATEWAY_ENV = (
    "REDIS_URL",
    "DATABASE_URL",
    "MINIO_ENDPOINT",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "MEDIA_BUCKET",
    "POSTGRES_PASSWORD",
    "JWT_SECRET",
    "AI_SECRETS_KEY",
)

GATEWAY_SERVICE = "telegram-bot"
COMPOSE_FILES = (
    Path("docker/docker-compose.yml"),
    Path("docker/staging-app-compose.yml"),
)
GATEWAY_ENV_EXAMPLE = Path("docker/staging-gateway.env.example")
NFT_RULES = Path("deploy/nftables-workout-gateway-isolation.nft")
NFT_UNIT = Path("deploy/workout-gateway-isolation.service")
# Порты хранилищ, закрытые для адреса шлюза на уровне bridge.
BLOCKED_STORAGE_PORTS = ("5432", "6379", "9000", "9001")


def _gateway_sources() -> list[Path]:
    return sorted(GATEWAY_DIR.rglob("*.py"))


def _module_path(module: str) -> Path | None:
    """Путь модуля проекта. Внешние пакеты сюда не попадают — они не в дереве."""
    as_module = PROJECT_ROOT / (module.replace(".", "/") + ".py")
    if as_module.exists():
        return as_module
    as_package = PROJECT_ROOT / module.replace(".", "/") / "__init__.py"
    if as_package.exists():
        return as_package
    return None


def _import_graph(entrypoint: str) -> tuple[set[str], set[str]]:
    """Модули проекта и внешние пакеты, достижимые импортами от точки входа.

    Обход именно графа, а не одного файла: доступ к хранилищу появляется
    транзитивно — Gateway импортирует общий модуль, а тот создаёт клиент.
    """
    internal: set[str] = set()
    external: set[str] = set()
    queue = [entrypoint]
    while queue:
        module = queue.pop()
        if module in internal:
            continue
        path = _module_path(module)
        if path is None:
            continue
        internal.add(module)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in ("apps", "src", "scripts"):
                        queue.append(alias.name)
                    else:
                        external.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level or not node.module:
                    continue
                if node.module.split(".")[0] in ("apps", "src", "scripts"):
                    queue.append(node.module)
                    # `from package import module` — имя тоже может быть модулем.
                    queue.extend(f"{node.module}.{alias.name}" for alias in node.names)
                else:
                    external.add(node.module.split(".")[0])
    return internal, external


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


def _compose_service(path: Path, service: str) -> str:
    """Блок одного сервиса compose как текст, без комментариев.

    YAML-парсер не используется намеренно: он потребовал бы зависимости, которой
    в проекте нет, а проверка нужна в CI. Формат собственных compose-файлов
    известен: сервисы объявлены с отступом два пробела внутри `services:`.

    Комментарии отбрасываются: в них перечислено, чего у шлюза быть не должно, и
    без этого проверка ловила бы собственное объяснение вместо конфигурации.
    """
    lines = (PROJECT_ROOT / path).read_text(encoding="utf-8").splitlines()
    block: list[str] = []
    inside = False
    for line in lines:
        if re.fullmatch(rf"  {re.escape(service)}:\s*", line):
            inside = True
            continue
        if inside:
            if line.strip() and not line.startswith("    "):
                break
            if line.lstrip().startswith("#"):
                continue
            block.append(line)
    assert inside, f"{path}: сервис {service} не найден"
    return "\n".join(block)


def _compose_service_names(path: Path) -> set[str]:
    lines = (PROJECT_ROOT / path).read_text(encoding="utf-8").splitlines()
    return {
        match.group(1)
        for match in (re.fullmatch(r"  ([a-z0-9][a-z0-9-]*):\s*", line) for line in lines)
        if match
    }


class TestGatewayCodeHasNoStorageAccess:
    @pytest.mark.parametrize("path", _gateway_sources(), ids=lambda p: p.name)
    def test_no_storage_markers_in_sources(self, path: Path):
        """Ни одного признака хранилища в исходниках Gateway."""
        found = STORAGE_MARKERS.keys() & _referenced_names(path)
        assert not found, (
            f"{path.relative_to(PROJECT_ROOT)} обращается к хранилищу напрямую: "
            + ", ".join(f"{name} ({STORAGE_MARKERS[name]})" for name in sorted(found))
            + ". Данные доступны только через internal API Backend."
        )

    def test_no_storage_client_in_import_graph(self):
        """Клиента хранилища нет и транзитивно — через общие модули."""
        _, external = _import_graph(GATEWAY_ENTRYPOINT)
        found = STORAGE_PACKAGES & external
        assert not found, (
            f"процесс Gateway импортирует клиенты хранилищ: {sorted(found)}. "
            "В EU не должно быть ни клиента, ни соединения к хранилищам RU."
        )

    def test_redis_client_is_not_a_project_dependency(self):
        """`aiogram[redis]` возвращал бы клиент Redis в образ шлюза.

        Образ один на Backend, Gateway и worker. Пока клиент установлен, вернуть
        подключение можно одной строкой, поэтому extra снят: Redis не использует
        ни один компонент.
        """
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "aiogram[redis]" not in pyproject
        assert re.search(r'^\s*"redis[<>=~\[]', pyproject, re.MULTILINE) is None

    def test_gateway_keeps_allowed_directions(self):
        """Разрешённые направления остались: Backend internal API и Telegram."""
        internal, external = _import_graph(GATEWAY_ENTRYPOINT)
        assert "aiogram" in external, "Telegram Bot API — единственный канал к Telegram"
        assert "httpx" in external, "Backend internal API вызывается по HTTP"
        assert "apps.telegram_gateway.backend_client" in internal


class TestGatewayWritesNoUserFilesInEU:
    """Фото и файлы программ живут в памяти процесса: EU не система хранения."""

    @pytest.mark.parametrize("path", _gateway_sources(), ids=lambda p: p.name)
    def test_no_filesystem_writes(self, path: Path):
        forbidden = {
            "open",
            "write_bytes",
            "write_text",
            "mkdir",
            "NamedTemporaryFile",
            "TemporaryFile",
            "mkstemp",
            "FSInputFile",
            "PHOTOS_DIR",
            "DATA_DIR",
        }
        found = forbidden & _referenced_names(path)
        assert not found, (
            f"{path.relative_to(PROJECT_ROOT)} пишет пользовательские данные на "
            f"диск EU: {sorted(found)}. Байты передаются в RU и остаются в памяти."
        )

    def test_documents_are_sent_from_memory(self):
        """Отправка файла — из буфера в памяти, а не из файла на диске."""
        for name in ("delivery_poller.py", "view_renderer.py"):
            source = (GATEWAY_DIR / name).read_text(encoding="utf-8")
            assert "BufferedInputFile" in source
            assert "FSInputFile" not in source


class TestGatewayDeploymentHasNoStorageCredentials:
    @pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
    def test_gateway_service_gets_no_storage_env(self, path: Path):
        """В compose сервису шлюза не передаются доступы к хранилищам."""
        block = _compose_service(path, GATEWAY_SERVICE)
        found = [key for key in FORBIDDEN_GATEWAY_ENV if key in block]
        assert found == [], (
            f"{path}: сервису {GATEWAY_SERVICE} переданы доступы к хранилищам: "
            f"{found}"
        )

    @pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
    def test_gateway_service_does_not_read_backend_env_file(self, path: Path):
        """Общий env-файл дал бы шлюзу доступы RU, даже если код ими не пользуется."""
        block = _compose_service(path, GATEWAY_SERVICE)
        assert "STAGING_APP_ENV_FILE" not in block
        assert "../.env" not in block

    @pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
    def test_no_gateway_redis_service(self, path: Path):
        """Собственного Redis у шлюза тоже нет: хранить в EU нечего."""
        assert "gateway-redis" not in _compose_service_names(path)
        assert "gateway-redis" not in _compose_service(path, GATEWAY_SERVICE)

    @pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
    def test_gateway_service_has_no_persistent_storage(self, path: Path):
        """Ни тома, ни каталога данных: писать пользовательские файлы нечем."""
        block = _compose_service(path, GATEWAY_SERVICE)
        assert "volumes:" not in block, f"{path}: у шлюза появился том"
        assert "WORKOUT_DATA_DIR" not in block, (
            f"{path}: шлюзу задан каталог данных, хотя писать в EU он не должен"
        )

    def test_gateway_env_example_has_no_storage_keys(self):
        """Пример env шлюза — источник файла на хосте: лишний ключ попал бы в EU."""
        text = (PROJECT_ROOT / GATEWAY_ENV_EXAMPLE).read_text(encoding="utf-8")
        declared = {
            line.split("=", 1)[0].strip()
            for line in text.splitlines()
            if "=" in line and not line.lstrip().startswith("#")
        }
        found = declared & set(FORBIDDEN_GATEWAY_ENV)
        assert not found, f"{GATEWAY_ENV_EXAMPLE}: объявлены ключи хранилищ: {found}"

    def test_gateway_env_example_keeps_backend_channel(self):
        """Обратная сторона: без адреса и токена Backend шлюз не работает вовсе."""
        text = (PROJECT_ROOT / GATEWAY_ENV_EXAMPLE).read_text(encoding="utf-8")
        for key in ("BOT_TOKEN", "BACKEND_INTERNAL_URL", "INTERNAL_SERVICE_TOKEN"):
            assert re.search(rf"^{key}=", text, re.MULTILINE), f"нет {key}"


class TestNetworkLayerIsolation:
    """Сетевой слой изоляции: bridge-family правило и его персистентность.

    Отсутствие клиента и credentials — два верхних слоя защиты, но сетевой путь
    до хранилищ через общий docker-bridge остаётся: связность Gateway → Backend
    нужна, а трафик внутри одного bridge коммутируется на канальном уровне и в
    цепочку FORWARD семейства `ip` не попадает. Поэтому deny живёт в семействе
    `bridge`, а его описание хранится в репозитории: правило, существующее
    только на хосте, теряется при пересоздании сервера и не проходит review.
    """

    def test_rules_file_is_versioned(self):
        assert (PROJECT_ROOT / NFT_RULES).exists(), (
            f"{NFT_RULES} отсутствует: правило изоляции должно быть в репозитории"
        )

    def test_rule_blocks_gateway_address_on_storage_ports(self):
        """Правило адресует именно шлюз и именно порты хранилищ."""
        text = (PROJECT_ROOT / NFT_RULES).read_text(encoding="utf-8")
        assert "table bridge workout_gateway_isolation" in text
        assert "hook forward" in text
        gateway_ip = _compose_gateway_ip(Path("docker/staging-app-compose.yml"))
        assert f"ip saddr {gateway_ip}" in text, (
            f"правило не совпадает с адресом шлюза из compose ({gateway_ip})"
        )
        for port in BLOCKED_STORAGE_PORTS:
            assert port in text, f"порт {port} не закрыт правилом"
        assert "drop" in text

    def test_rule_does_not_touch_allowed_directions(self):
        """Backend (8000) и Telegram не должны попадать под drop."""
        text = (PROJECT_ROOT / NFT_RULES).read_text(encoding="utf-8")
        rule_lines = [
            line
            for line in text.splitlines()
            if "dport" in line and not line.lstrip().startswith("#")
        ]
        assert rule_lines, "в файле нет правила с портами"
        for line in rule_lines:
            assert "8000" not in line, "правило закрывает Backend internal API"
            assert "443" not in line, "правило закрывает Telegram Bot API"

    def test_rule_is_idempotent(self):
        """Повторный запуск заменяет таблицу, а не добавляет второе правило."""
        text = (PROJECT_ROOT / NFT_RULES).read_text(encoding="utf-8")
        assert "delete table bridge workout_gateway_isolation" in text

    def test_rule_does_not_flush_global_ruleset(self):
        """`flush ruleset` снёс бы правила ufw и docker."""
        text = (PROJECT_ROOT / NFT_RULES).read_text(encoding="utf-8")
        assert "flush ruleset" not in text

    def test_unit_is_versioned_and_reversible(self):
        """Персистентность и откат описаны в unit, а не только в отчёте."""
        text = (PROJECT_ROOT / NFT_UNIT).read_text(encoding="utf-8")
        assert "ExecStart=/usr/sbin/nft -f /etc/nftables-workout-gateway-isolation.nft" in text
        assert "delete table bridge workout_gateway_isolation" in text
        assert "Type=oneshot" in text
        assert "RemainAfterExit=yes" in text


def _compose_gateway_ip(path: Path) -> str:
    """Фиксированный адрес шлюза из compose — источник истины для правила."""
    block = _compose_service(path, GATEWAY_SERVICE)
    match = re.search(r"ipv4_address:\s*\$\{TELEGRAM_BOT_IP:-([0-9.]+)\}", block)
    assert match, f"{path}: у шлюза нет фиксированного ipv4_address"
    return match.group(1)


class TestDispatcherWiring:
    def test_state_is_local_to_the_process(self, dispatcher):
        """Служебное состояние aiogram — в памяти, без внешнего хранилища."""
        assert isinstance(dispatcher.fsm.storage, MemoryStorage)
        assert isinstance(dispatcher.fsm.events_isolation, SimpleEventIsolation)

    def test_entrypoint_builds_in_memory_state(self):
        """Точка входа собирает то же самое: тест не проверял бы production-путь."""
        from apps.telegram_gateway.main import build_isolation

        storage, isolation = build_isolation()
        assert isinstance(storage, MemoryStorage)
        assert isinstance(isolation, SimpleEventIsolation)

    def test_dialog_router_is_registered(self, dispatcher):
        names = {router.name for router in dispatcher.sub_routers}
        assert names == {"telegram_gateway.dialog"}
