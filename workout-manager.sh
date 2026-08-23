#!/usr/bin/env bash
# Workout Bot Manager — управление локальным окружением проекта.
#
# Под управлением четыре части: backend (FastAPI), веб-интерфейс (Next.js),
# Telegram-бот и инфраструктура в docker compose (PostgreSQL, MinIO).
#
# Процессы запускаются откреплённо (setsid), поэтому живут после закрытия
# терминала. Логи и PID-файлы лежат в data/ (каталог не под git).
#
# Команды: ./workout-manager.sh help

set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
DIM='\033[2m'
NC='\033[0m'

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$PROJECT_DIR/apps/web"
VENV_PY="$PROJECT_DIR/.venv/bin/python"
COMPOSE_FILE="$PROJECT_DIR/docker/docker-compose.yml"
ENV_FILE="$PROJECT_DIR/.env"

RUN_DIR="$PROJECT_DIR/data/run"
LOG_DIR="$PROJECT_DIR/data/logs"
MANAGER_LOG="$LOG_DIR/manager.log"

# Порты берутся из .env, где это предусмотрено; остальные — фиксированные
# значения локальной разработки.
BACKEND_HOST="${WORKOUT_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${WORKOUT_BACKEND_PORT:-8000}"
WEB_PORT="${WORKOUT_WEB_PORT:-3000}"

mkdir -p "$RUN_DIR" "$LOG_DIR"

# --- Вывод и журнал -----------------------------------------------------------

log() {
    printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "${*:2}" >>"$MANAGER_LOG"
}

say() {
    local color="$1" level="$2"
    shift 2
    printf '%b%s%b\n' "$color" "$*" "$NC"
    log "$level" "$*"
}

info() { say "$BLUE" INFO "$*"; }
ok() { say "$GREEN" INFO "$*"; }
warn() { say "$YELLOW" WARN "$*"; }
fail() { say "$RED" ERROR "$*"; }

# --- Общие проверки -----------------------------------------------------------

compose() {
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

require_python() {
    if [[ ! -x "$VENV_PY" ]]; then
        fail "Нет виртуального окружения: $VENV_PY"
        echo "Создайте его: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
        return 1
    fi
}

require_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        fail "Нет файла .env — backend и бот без него не поднимутся"
        return 1
    fi
}

require_web_deps() {
    if [[ ! -d "$WEB_DIR/node_modules" ]]; then
        fail "Не установлены зависимости веб-интерфейса"
        echo "Установите их: (cd apps/web && npm install)"
        return 1
    fi
}

port_busy() {
    timeout 2 bash -c "</dev/tcp/127.0.0.1/$1" 2>/dev/null
}

port_owner_pid() {
    # Первый слушающий процесс на порту. `ss` идёт первым: `lsof` на части
    # систем не показывает сокеты, открытые на все интерфейсы (`*:3000`).
    if command -v ss >/dev/null 2>&1; then
        local pid
        pid="$(ss -ltnpH "sport = :$1" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1)"
        if [[ -n "$pid" ]]; then
            echo "$pid"
            return
        fi
    fi
    if command -v lsof >/dev/null 2>&1; then
        lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null | head -1
    fi
}

# --- Учёт процессов -----------------------------------------------------------

pidfile() { echo "$RUN_DIR/$1.pid"; }
logfile() { echo "$LOG_DIR/$1.log"; }

alive() { [[ -n "${1:-}" ]] && kill -0 "$1" 2>/dev/null; }

# Ищет процесс службы: сначала по своему PID-файлу, затем по порту или шаблону
# команды. Второй путь важен: процесс мог быть запущен вручную, и «не вижу в
# PID-файле» не значит «не запущен».
service_pid() {
    local name="$1" pid=""
    local file
    file="$(pidfile "$name")"
    if [[ -f "$file" ]]; then
        pid="$(cat "$file" 2>/dev/null || true)"
        if alive "$pid"; then
            echo "$pid"
            return 0
        fi
        rm -f "$file"
    fi
    case "$name" in
        backend) pid="$(port_owner_pid "$BACKEND_PORT")" ;;
        web) pid="$(port_owner_pid "$WEB_PORT")" ;;
        bot) pid="$(pgrep -f 'apps\.telegram_gateway\.main' | head -1)" ;;
    esac
    [[ -n "$pid" ]] && echo "$pid"
}

# Признак того, что процесс запущен не этим скриптом: полезно, чтобы понимать,
# чей процесс останавливается.
managed() {
    local file
    file="$(pidfile "$1")"
    [[ -f "$file" && "$(cat "$file" 2>/dev/null)" == "$2" ]]
}

# Значение переменной окружения работающего процесса. Отвечает на вопрос
# «а с какой базой этот backend вообще работает» без догадок.
process_env_var() {
    local pid="$1" key="$2"
    [[ -r "/proc/$pid/environ" ]] || return 0
    tr '\0' '\n' <"/proc/$pid/environ" 2>/dev/null | grep -m1 "^$key=" | cut -d= -f2-
}

# Запуск службы в собственной сессии процессов.
#
# `exec setsid` внутри фоновой подоболочки: подоболочка заменяется самой
# службой, поэтому лишнего ожидающего процесса не остаётся (иначе скрипт не
# завершался бы после запуска). Своя сессия даёт службе отдельную группу
# процессов — её потомков (например, next-server у `npm run dev`) можно
# остановить одним сигналом по группе, а Ctrl+C приходит только скрипту,
# который останавливает службы сам, в нужном порядке.
#
# PID пишет сама служба (`echo $$` перед `exec`): так в файле оказывается
# именно тот процесс, который слушает порт.
spawn_service() {
    local name="$1" workdir="$2"
    shift 2
    local out pf
    out="$(logfile "$name")"
    pf="$(pidfile "$name")"
    printf '\n===== запуск %s: %s =====\n' "$name" "$(date '+%Y-%m-%d %H:%M:%S')" >>"$out"
    rm -f "$pf"
    # setsid вызывается напрямую, без обёртки в подоболочку: обёртка осталась бы
    # ждать службу, и скрипт не завершился бы после запуска в фоне.
    setsid bash -c 'cd "$1" || exit 1; echo $$ >"$2"; shift 2; exec "$@"' \
        _ "$workdir" "$pf" "$@" >>"$out" 2>&1 </dev/null &
    disown 2>/dev/null || true
    local _
    for _ in $(seq 1 25); do
        [[ -s "$pf" ]] && return 0
        sleep 0.2
    done
    return 0
}

# --- Работа в переднем плане ---------------------------------------------------
#
# Режим по умолчанию: логи служб идут в терминал, Ctrl+C останавливает всё.
# Файлы логов пишутся в обоих режимах — они нужны, чтобы посмотреть
# предысторию после падения.

# PID процессов, показывающих логи в терминале.
TAIL_PIDS=()
# Службы, запущенные в текущем сеансе переднего плана.
FOREGROUND_SERVICES=()

# Служба + трансляция её лога в терминал с префиксом.
start_child() {
    local name="$1" color="$2" workdir="$3"
    shift 3
    spawn_service "$name" "$workdir" "$@"
    FOREGROUND_SERVICES+=("$name")

    local prefix out
    out="$(logfile "$name")"
    prefix="$(printf '%b[%s]%b ' "$color" "$name" "$NC")"
    ( tail -n 0 -F "$out" 2>/dev/null | sed -u "s|^|$prefix|" ) &
    TAIL_PIDS+=("$!")
}

# Единая точка запуска службы: отличие режимов только в трансляции логов.
launch_service() {
    local name="$1" color="$2" workdir="$3" mode="$4"
    shift 4
    if [[ "$mode" == "fg" ]]; then
        start_child "$name" "$color" "$workdir" "$@"
    else
        spawn_service "$name" "$workdir" "$@"
    fi
}

stop_tails() {
    local pid
    for pid in "${TAIL_PIDS[@]:-}"; do
        [[ -n "$pid" ]] || continue
        # Завершаем всю группу: tail и sed в конвейере.
        pkill -P "$pid" 2>/dev/null || true
        kill "$pid" 2>/dev/null || true
    done
    TAIL_PIDS=()
}

# Обработчик Ctrl+C: останавливает всё, что подняли, как это делает `stop`.
foreground_shutdown() {
    trap '' INT TERM
    echo
    info "Останавливаю службы"
    stop_tails
    local name
    for name in "${FOREGROUND_SERVICES[@]:-}"; do
        case "$name" in
            backend) stop_service backend "Backend" ;;
            web) stop_service web "Веб-интерфейс" ;;
            bot) stop_service bot "Telegram-бот" ;;
        esac
    done
    ok "Готово"
    exit 0
}

# Ждём, пока службы работают. Падение любой из них прекращает ожидание: молча
# продолжать с половиной окружения хуже, чем выйти с понятным сообщением.
supervise() {
    local name pid
    while :; do
        for name in "${FOREGROUND_SERVICES[@]}"; do
            pid="$(cat "$(pidfile "$name")" 2>/dev/null || true)"
            if ! alive "$pid"; then
                sleep 1  # даём логу дойти до терминала
                echo
                fail "Служба «$name» завершилась — останавливаю остальные"
                stop_tails
                local other
                for other in "${FOREGROUND_SERVICES[@]}"; do
                    [[ "$other" == "$name" ]] && continue
                    case "$other" in
                        backend) stop_service backend "Backend" ;;
                        web) stop_service web "Веб-интерфейс" ;;
                        bot) stop_service bot "Telegram-бот" ;;
                    esac
                done
                return 1
            fi
        done
        sleep 2
    done
}

# Останавливает службу вместе со всем её процессным деревом.
#
# Одного kill по PID мало: `npm run dev` порождает next-server, который иначе
# продолжит держать порт. Службы запускаются через setsid, поэтому у них своя
# группа процессов — сигнал по группе (`kill -- -PGID`) достаёт всех потомков.
terminate_tree() {
    local pid="$1"
    local pgid
    pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')"

    if [[ -n "$pgid" && "$pgid" != "$$" ]]; then
        kill -TERM -- "-$pgid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    else
        # Своей группы нет: гасим процесс и его прямых потомков по отдельности.
        local -a children=()
        command -v pgrep >/dev/null 2>&1 && mapfile -t children < <(pgrep -P "$pid" 2>/dev/null || true)
        kill "$pid" 2>/dev/null || true
        local child
        for child in "${children[@]:-}"; do
            [[ -n "$child" ]] && kill "$child" 2>/dev/null || true
        done
    fi

    for _ in $(seq 1 15); do
        alive "$pid" || break
        sleep 0.5
    done
    if alive "$pid"; then
        if [[ -n "$pgid" && "$pgid" != "$$" ]]; then
            kill -9 -- "-$pgid" 2>/dev/null || true
        fi
        kill -9 "$pid" 2>/dev/null || true
        sleep 0.5
    fi
    ! alive "$pid"
}

stop_service() {
    local name="$1" label="$2" pid
    pid="$(service_pid "$name")"
    if [[ -z "$pid" ]]; then
        ok "$label уже остановлен"
        return 0
    fi
    if ! managed "$name" "$pid"; then
        warn "$label (PID $pid) запущен не через этот скрипт — останавливаю его"
    fi
    if terminate_tree "$pid"; then
        rm -f "$(pidfile "$name")"
        ok "$label остановлен"
    else
        rm -f "$(pidfile "$name")"
        fail "$label не удалось остановить (PID $pid)"
        return 1
    fi
}

# Процесс принадлежит проекту, если его рабочий каталог внутри проекта. Так
# отличается собственный забытый dev-сервер от чужого приложения на том же порту.
pid_in_project() {
    local cwd
    cwd="$(readlink -f "/proc/$1/cwd" 2>/dev/null || true)"
    [[ -n "$cwd" && "$cwd" == "$PROJECT_DIR"* ]]
}

# Порт под запуск службы. Свой забытый процесс убираем молча — это ровно то, что
# мешает запуску; посторонний процесс не трогаем и говорим об этом.
ensure_port_free() {
    local port="$1" pid
    pid="$(port_owner_pid "$port")"
    [[ -z "$pid" ]] && return 0
    if ! pid_in_project "$pid"; then
        fail "Порт $port занят процессом вне проекта (PID $pid): $(ps -o cmd= -p "$pid" 2>/dev/null | cut -c1-60)"
        echo "Освободите порт вручную или задайте другой через переменную окружения."
        return 1
    fi
    warn "Порт $port занят прежним процессом проекта (PID $pid) — освобождаю"
    kill_pid "$pid" "Процесс на порту $port"
}

wait_for_port() {
    local port="$1" label="$2" attempts="${3:-40}" pid="${4:-}"
    printf '%b⏳ Ожидание %s на порту %s%b' "$CYAN" "$label" "$port" "$NC"
    for _ in $(seq 1 "$attempts"); do
        if port_busy "$port"; then
            printf '\n'
            ok "$label готов: http://127.0.0.1:$port"
            return 0
        fi
        if [[ -n "$pid" ]] && ! alive "$pid"; then
            printf '\n'
            fail "$label завершился при запуске — смотрите логи: $0 logs ${label,,}"
            return 1
        fi
        printf '.'
        sleep 1
    done
    printf '\n'
    fail "$label не ответил за ${attempts} с"
    return 1
}

# --- Инфраструктура -----------------------------------------------------------

pg_port() {
    grep -m1 '^POSTGRES_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || true
}

pg_port_effective() {
    local port
    port="$(pg_port)"
    printf '%s\n' "${port:-5432}"
}

# Адрес базы так, как его увидит приложение: переменная окружения приоритетнее
# .env (так же читает конфигурацию сам backend).
env_database_url() {
    if [[ -n "${DATABASE_URL:-}" ]]; then
        printf '%s\n' "$DATABASE_URL"
        return
    fi
    grep -m1 '^DATABASE_URL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true
}

# Схема БД отстаёт от кода — частая причина «страница не работает» после
# обновления. Это предупреждение, а не отказ: миграции применяет `migrate`.
warn_pending_migrations() {
    local current head
    current="$("$VENV_PY" -m alembic current 2>/dev/null | grep -oE '^[0-9a-f]{4,}' | head -1)"
    head="$("$VENV_PY" -m alembic heads 2>/dev/null | grep -oE '^[0-9a-f]{4,}' | head -1)"
    [[ -z "$current" || -z "$head" ]] && return 0
    if [[ "$current" != "$head" ]]; then
        warn "Схема БД на миграции $current, а код ожидает $head"
        warn "Примените миграции: $0 migrate"
    fi
}

start_services() {
    require_env && require_python || return 1
    info "Запуск инфраструктуры (PostgreSQL, MinIO)"
    local port="${1:-$(pg_port)}"
    port="${port:-5432}"
    if port_busy "$port" && ! compose ps --services --filter status=running | grep -qx postgres; then
        local owner
        owner="$(port_owner_pid "$port")"
        warn "Порт $port занят посторонним процессом (PID ${owner:-неизвестен})."
        warn "Контейнер PostgreSQL не поднимется, а приложение подключится к чужой базе."
        return 1
    fi
    compose up -d postgres minio || return 1
    local ready=0
    for _ in $(seq 1 30); do
        if compose ps --services --filter status=running | grep -qx postgres; then
            ready=1
            break
        fi
        printf '.'
        sleep 2
    done
    ((ready == 0)) && { fail "Контейнер PostgreSQL не запустился"; return 1; }
    ensure_database || return 1
    warn_pending_migrations
}

# Доступность базы по тому адресу, с которым будет работать приложение.
#
# «Контейнер работает» ещё не значит «база доступна»: у контейнера может
# пропасть публикация порта (так и получилось: backend поднялся, а все страницы
# отдавали ошибку). Проверяется именно подключение по DATABASE_URL.
DATABASE_VERIFIED=0

ensure_database() {
    ((DATABASE_VERIFIED == 1)) && return 0

    local url probe
    url="$(env_database_url)"
    if [[ -z "$url" ]]; then
        fail "DATABASE_URL не задан ни в окружении, ни в .env"
        return 1
    fi
    local shown
    shown="$(sed -E 's#^[^@]*@##' <<<"$url")"

    # Свежезапущенный PostgreSQL несколько секунд отвечает «starting up» —
    # это не поломка, поэтому сначала просто ждём.
    local _
    for _ in $(seq 1 10); do
        probe="$(db_probe "$url")"
        [[ "$probe" == ok* ]] && break
        [[ "$probe" == *CannotConnectNow* ]] || break
        sleep 1
    done
    if [[ "$probe" == ok* ]]; then
        ok "База доступна ($shown)"
        DATABASE_VERIFIED=1
        return 0
    fi

    # Пересоздание контейнера восстанавливает публикацию порта. Данные лежат в
    # именованном volume и не затрагиваются. Делается только если адрес ведёт
    # к своему контейнеру: чужую базу этим не починить.
    local own_port
    own_port="$(pg_port_effective)"
    if [[ "$shown" == *"localhost:$own_port/"* || "$shown" == *"127.0.0.1:$own_port/"* ]] &&
        compose ps --services 2>/dev/null | grep -qx postgres; then
        warn "База недоступна ($shown): ${probe#fail }"
        info "Пересоздаю контейнер PostgreSQL (данные сохраняются в volume)"
        compose up -d --force-recreate postgres >/dev/null 2>&1 || true
        for _ in $(seq 1 15); do
            probe="$(db_probe "$url")"
            [[ "$probe" == ok* ]] && break
            printf '.'
            sleep 2
        done
        printf '\n'
    fi

    if [[ "$probe" == ok* ]]; then
        ok "База доступна ($shown)"
        DATABASE_VERIFIED=1
        return 0
    fi
    fail "База недоступна ($shown): ${probe#fail }"
    echo "Backend без базы отвечает ошибкой на все запросы, поэтому он не запущен."
    echo "Проверьте: $0 doctor  и  $0 logs postgres"
    return 1
}

stop_services() {
    info "Остановка контейнеров инфраструктуры"
    compose stop postgres minio && ok "Контейнеры остановлены"
}

# --- Приложения ---------------------------------------------------------------

backend_cmd() {
    printf '%s\n' "$VENV_PY" -m uvicorn apps.backend.main:app \
        --host "$BACKEND_HOST" --port "$BACKEND_PORT"
}

# Каждая служба стартует одинаково: проверить, не запущена ли уже, освободить
# свой порт, запустить и дождаться готовности. Отличие режимов только в том,
# показываются ли логи в терминале.
start_backend() {
    local foreground="${1:-}"
    require_python && require_env || return 1
    local pid
    pid="$(service_pid backend)"
    if [[ -n "$pid" ]]; then
        ok "Backend уже запущен (PID $pid)"
        return 0
    fi
    info "Запуск backend на $BACKEND_HOST:$BACKEND_PORT"
    # Порт backend откроет даже без базы: uvicorn стартует, а все запросы
    # отвечают ошибкой, и в интерфейсе это выглядит как «Failed to fetch».
    # Поэтому доступность базы проверяется до запуска, а не после.
    ensure_database || return 1
    ensure_port_free "$BACKEND_PORT" || return 1
    local -a cmd
    mapfile -t cmd < <(backend_cmd)
    launch_service backend "$CYAN" "$PROJECT_DIR" "$foreground" "${cmd[@]}"
    wait_for_port "$BACKEND_PORT" "Backend" 40 "$(cat "$(pidfile backend)")"
}

start_web() {
    local foreground="${1:-}"
    require_web_deps || return 1
    local pid
    pid="$(service_pid web)"
    if [[ -n "$pid" ]]; then
        ok "Веб-интерфейс уже запущен (PID $pid)"
        return 0
    fi
    info "Запуск веб-интерфейса на порту $WEB_PORT"
    ensure_port_free "$WEB_PORT" || return 1
    launch_service web "$BLUE" "$WEB_DIR" "$foreground" npm run dev
    wait_for_port "$WEB_PORT" "Веб-интерфейс" 60 "$(cat "$(pidfile web)")"
}

start_bot() {
    local foreground="${1:-}"
    require_python && require_env || return 1
    local pid
    pid="$(service_pid bot)"
    if [[ -n "$pid" ]]; then
        ok "Telegram-бот уже запущен (PID $pid)"
        return 0
    fi
    if ! grep -q '^BOT_TOKEN=.\+' "$ENV_FILE"; then
        fail "В .env нет BOT_TOKEN — бот не запустится"
        return 1
    fi
    info "Запуск Telegram-бота"
    launch_service bot "$YELLOW" "$PROJECT_DIR" "$foreground" \
        "$VENV_PY" -m apps.telegram_gateway.main
    sleep 3
    local started
    started="$(cat "$(pidfile bot)" 2>/dev/null || true)"
    if alive "$started"; then
        ok "Telegram-бот запущен (PID $started)"
    else
        fail "Бот завершился при запуске — смотрите логи: $0 logs bot"
        return 1
    fi
}

start_all() {
    local foreground="${1:-fg}"
    start_services || return 1
    start_backend "$foreground" || return 1
    start_web "$foreground" || return 1
    echo
    ok "Готово. Интерфейс: http://localhost:$WEB_PORT, API: http://127.0.0.1:$BACKEND_PORT"
    echo -e "${DIM}Бот запускается отдельно: $0 start bot${NC}"
    if [[ "$foreground" == "fg" ]]; then
        echo -e "${DIM}Логи служб ниже. Ctrl+C останавливает backend и веб-интерфейс.${NC}"
        echo
        trap foreground_shutdown INT TERM
        supervise
    fi
}

stop_all() {
    stop_service web "Веб-интерфейс"
    stop_service backend "Backend"
    stop_service bot "Telegram-бот"
}

# --- Статус -------------------------------------------------------------------

status_line() {
    local label="$1" pid="$2" extra="${3:-}" name="$4"
    # Выравнивание считаем по символам, а не байтам: printf с %-18s ломает
    # колонки на кириллице.
    local pad=$((18 - ${#label}))
    ((pad < 1)) && pad=1
    local spaces
    spaces="$(printf '%*s' "$pad" '')"
    if [[ -n "$pid" ]]; then
        local mark=""
        managed "$name" "$pid" || mark=" ${DIM}(запущен вне скрипта)${NC}"
        printf '%b\n' "  ${GREEN}●${NC} ${label}${spaces}PID ${pid}  ${extra}${mark}"
    else
        printf '%b\n' "  ${RED}○${NC} ${label}${spaces}${DIM}остановлен${NC}"
    fi
}

show_status() {
    echo -e "${CYAN}=== Workout Bot: состояние окружения ===${NC}"
    echo
    echo -e "${CYAN}Приложения:${NC}"

    local backend_pid web_pid bot_pid
    backend_pid="$(service_pid backend)"
    web_pid="$(service_pid web)"
    bot_pid="$(service_pid bot)"

    local backend_extra=""
    if [[ -n "$backend_pid" ]]; then
        backend_extra="http://127.0.0.1:$BACKEND_PORT"
        local db
        db="$(process_env_var "$backend_pid" DATABASE_URL)"
        [[ -z "$db" ]] && db="$(grep -m1 '^DATABASE_URL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"
        if [[ -n "$db" ]]; then
            # Показываем host:port/db без пароля: этого достаточно, чтобы
            # заметить подключение не к той базе.
            backend_extra+="  ${DIM}БД: $(sed -E 's#^[^@]*@##' <<<"$db")${NC}"
        fi
    fi
    status_line "Backend API" "$backend_pid" "$backend_extra" backend
    status_line "Веб-интерфейс" "$web_pid" "${web_pid:+http://localhost:$WEB_PORT}" web
    status_line "Telegram-бот" "$bot_pid" "" bot

    echo
    echo -e "${CYAN}Инфраструктура:${NC}"
    if compose ps --format '{{.Service}} {{.Status}}' 2>/dev/null | grep -q .; then
        compose ps --format '  {{.Service}}: {{.Status}}' 2>/dev/null
    else
        echo -e "  ${DIM}контейнеры не запущены${NC}"
    fi

    if [[ -n "$backend_pid" ]]; then
        echo
        echo -e "${CYAN}Проверка API:${NC}"
        local health
        health="$(curl -s -m 5 "http://127.0.0.1:$BACKEND_PORT/ready" 2>/dev/null || true)"
        echo -e "  /ready → ${health:-${RED}нет ответа${NC}}"
    fi
}

ai_status() {
    require_python && require_env || return 1
    local pid
    pid="$(service_pid backend)"
    if [[ -z "$pid" ]]; then
        fail "Backend не запущен: $0 start backend"
        return 1
    fi
    WORKOUT_API_BASE="http://127.0.0.1:$BACKEND_PORT/api/v1" "$VENV_PY" - <<'PY'
"""Готовность AI-контура: чек-лист и цепочка моделей."""
import json
import os
import urllib.error
import urllib.request

BASE = os.environ["WORKOUT_API_BASE"]
ENV = os.path.join(os.getcwd(), ".env")


def env(name):
    for line in open(ENV, encoding="utf-8"):
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    return ""


def call(method, path, payload=None, token=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except OSError as exc:
        print(f"Не удалось обратиться к API: {exc}")
        raise SystemExit(1)


status, tokens = call(
    "POST", "/auth/login", {"login": env("ADMIN_LOGIN"), "password": env("ADMIN_PASSWORD")}
)
if status != 200:
    print(f"Вход не выполнен (HTTP {status}): проверьте ADMIN_LOGIN/ADMIN_PASSWORD в .env")
    raise SystemExit(1)
token = tokens["access_token"]

status, report = call("GET", "/admin/ai/readiness", None, token)
if status != 200:
    print(f"Готовность получить не удалось (HTTP {status})")
    raise SystemExit(1)

icons = {"ok": "✅", "warning": "⚠️ ", "missing": "◻️ ", "failed": "❌"}
print(f"Готовность AI-генерации: {'да' if report['ready'] else 'нет'}\n")
for check in report["checks"]:
    print(f"  {icons.get(check['status'], '  ')} {check['title']}: {check['detail']}")
    if check["status"] != "ok" and check.get("action"):
        print(f"      → {check['action']}")

if report["chain"]:
    print("\nЦепочка моделей:")
    for link in report["chain"]:
        role = "основная " if link["is_primary"] else "резервная"
        print(f"  {role}: {link['model_id']} через «{link['endpoint']}» ({link['provider']})")

status, usage = call("GET", "/admin/ai/usage?limit=5", None, token)
if status == 200 and usage["items"]:
    print("\nПоследние вызовы ИИ:")
    for item in usage["items"]:
        print(
            f"  {item['created_at'][:19]}  {item['status']:8s}"
            f" токенов={item['total_tokens'] or '-':>6}"
            f" {item['error_type'] or ''}"
        )
PY
}

# --- Освобождение портов ------------------------------------------------------

# Процессы, слушающие TCP-порты и запущенные из каталога проекта. Именно так
# находятся забытые dev-серверы на случайных портах: docker-контейнеры и чужие
# приложения под это условие не попадают, поэтому их не задеваем.
project_listeners() {
    command -v ss >/dev/null 2>&1 || return 0
    ss -ltnpH 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u |
        while read -r pid; do
            pid_in_project "$pid" || continue
            local ports
            ports="$(ss -ltnpH 2>/dev/null | grep "pid=$pid," | grep -oP ':\K[0-9]+(?= )' | sort -un | paste -sd, -)"
            printf '%s %s\n' "$pid" "$ports"
        done
}

kill_pid() {
    local pid="$1" label="$2"
    if terminate_tree "$pid"; then
        ok "$label остановлен"
    else
        fail "$label не удалось остановить (PID $pid)"
        return 1
    fi
}

free_port() {
    local port="$1" pid
    pid="$(port_owner_pid "$port")"
    if [[ -z "$pid" ]]; then
        ok "Порт $port свободен"
        return 0
    fi
    local cmd
    cmd="$(ps -o cmd= -p "$pid" 2>/dev/null | cut -c1-70)"
    if ! pid_in_project "$pid"; then
        # Порт занят посторонним процессом. Убивать его молча нельзя: это может
        # быть чужое приложение или база другого проекта.
        warn "Порт $port занят процессом вне проекта (PID $pid): $cmd"
        warn "Он не остановлен. Освободите порт вручную или укажите другой порт."
        return 1
    fi
    info "Освобождаю порт $port (PID $pid): $cmd"
    kill_pid "$pid" "Процесс на порту $port"
    rm -f "$RUN_DIR"/*.pid 2>/dev/null || true
}

free_ports() {
    if (($# > 0)); then
        local port status=0
        for port in "$@"; do
            free_port "$port" || status=1
        done
        return $status
    fi

    info "Освобождение портов проекта"
    local status=0
    free_port "$BACKEND_PORT" || status=1
    free_port "$WEB_PORT" || status=1

    # Забытые процессы проекта на прочих портах (например, backend, поднятый
    # вручную на 8019 для проверки).
    local found=0
    while read -r pid ports; do
        [[ -n "${pid:-}" ]] || continue
        found=1
        local cmd
        cmd="$(ps -o cmd= -p "$pid" 2>/dev/null | cut -c1-70)"
        info "Забытый процесс проекта на порту(ах) $ports (PID $pid): $cmd"
        kill_pid "$pid" "Процесс на порту(ах) $ports" || status=1
    done < <(project_listeners)
    ((found == 0)) && ok "Забытых процессов проекта на других портах нет"

    rm -f "$RUN_DIR"/*.pid 2>/dev/null || true
    return $status
}

# --- Проверка окружения -------------------------------------------------------

# Достижимость базы из DATABASE_URL. Отвечает на вопрос, из-за которого проще
# всего потерять время: приложение поднялось, но смотрит не туда или вообще не
# может подключиться.
db_probe() {
    local url="$1"
    "$VENV_PY" - "$url" <<'PY' 2>/dev/null
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> int:
    engine = create_async_engine(sys.argv[1])
    try:
        async with engine.connect() as conn:
            tables = (
                await conn.execute(
                    text("select count(*) from information_schema.tables where table_schema='public'")
                )
            ).scalar_one()
            print(f"ok tables={tables}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"fail {type(exc).__name__}: {str(exc)[:120]}")
        return 1
    finally:
        await engine.dispose()


raise SystemExit(asyncio.run(main()))
PY
}

# Метка фиксированной ширины. printf с %-28s выравнивает по байтам, поэтому на
# кириллице колонки разъезжаются — считаем длину в символах.
label() {
    local text="$1" width="${2:-28}"
    local pad=$((width - ${#text}))
    ((pad < 1)) && pad=1
    printf '  %s%*s' "$text" "$pad" ''
}

doctor() {
    echo -e "${CYAN}=== Проверка окружения ===${NC}"
    local problems=0

    label "Python-окружение:"
    if [[ -x "$VENV_PY" ]]; then
        echo -e "${GREEN}$("$VENV_PY" --version 2>&1)${NC}"
    else
        echo -e "${RED}нет .venv${NC}"
        ((problems++))
    fi

    label "Файл .env:"
    if [[ -f "$ENV_FILE" ]]; then
        echo -e "${GREEN}есть${NC}"
    else
        echo -e "${RED}отсутствует${NC}"
        ((problems++))
    fi

    if [[ -f "$ENV_FILE" ]]; then
        # Ключи, без которых части системы молча не работают.
        local -a required=(DATABASE_URL ADMIN_LOGIN ADMIN_PASSWORD JWT_SECRET)
        local -a missing=()
        local key
        for key in "${required[@]}"; do
            grep -q "^$key=.\+" "$ENV_FILE" || missing+=("$key")
        done
        label "Обязательные настройки:"
        if ((${#missing[@]} == 0)); then
            echo -e "${GREEN}на месте${NC}"
        else
            echo -e "${RED}нет: ${missing[*]}${NC}"
            ((problems++))
        fi

        label "BOT_TOKEN (для бота):"
        if grep -q '^BOT_TOKEN=.\+' "$ENV_FILE"; then
            echo -e "${GREEN}задан${NC}"
        else
            echo -e "${YELLOW}не задан — бот не запустится${NC}"
        fi
    fi

    label "Зависимости веба:"
    if [[ -d "$WEB_DIR/node_modules" ]]; then
        echo -e "${GREEN}установлены${NC}"
    else
        echo -e "${RED}нет node_modules${NC}"
        ((problems++))
    fi

    label "Docker:"
    if docker info >/dev/null 2>&1; then
        echo -e "${GREEN}доступен${NC}"
    else
        echo -e "${RED}недоступен${NC}"
        ((problems++))
    fi

    if [[ -x "$VENV_PY" && -f "$ENV_FILE" ]]; then
        local url
        url="$(grep -m1 '^DATABASE_URL=' "$ENV_FILE" | cut -d= -f2- || true)"
        label "База из .env:"
        if [[ -z "$url" ]]; then
            echo -e "${RED}DATABASE_URL не задан${NC}"
            ((problems++))
        else
            local result
            result="$(db_probe "$url")"
            if [[ "$result" == ok* ]]; then
                echo -e "${GREEN}$(sed -E 's#^[^@]*@##' <<<"$url") — доступна (${result#ok })${NC}"
            else
                echo -e "${RED}$(sed -E 's#^[^@]*@##' <<<"$url") — ${result#fail }${NC}"
                ((problems++))
            fi
        fi

        # Backend может работать с другой базой, чем записано в .env: так
        # бывает после запуска с переопределённым DATABASE_URL. Расхождение
        # приводит к «настройки исчезли», поэтому показываем его прямо.
        local backend_pid backend_url
        backend_pid="$(service_pid backend)"
        if [[ -n "$backend_pid" ]]; then
            backend_url="$(process_env_var "$backend_pid" DATABASE_URL)"
            if [[ -n "$backend_url" && "$backend_url" != "$url" ]]; then
                label "Работающий backend:"
                echo -e "${YELLOW}использует другую базу: $(sed -E 's#^[^@]*@##' <<<"$backend_url")${NC}"
            fi
        fi
    fi

    label "Порты:"
    local -a busy=()
    port_busy "$BACKEND_PORT" && busy+=("$BACKEND_PORT (backend)")
    port_busy "$WEB_PORT" && busy+=("$WEB_PORT (веб)")
    if ((${#busy[@]} == 0)); then
        echo -e "${DIM}свободны${NC}"
    else
        echo -e "${DIM}заняты: ${busy[*]}${NC}"
    fi

    echo
    if ((problems == 0)); then
        ok "Проблем не найдено"
    else
        warn "Требуют внимания: $problems"
        return 1
    fi
}

# --- Логи ---------------------------------------------------------------------

show_logs() {
    local target="${1:-backend}" follow="${2:-}"
    local file
    case "$target" in
        backend | web | bot | manager) file="$(logfile "$target")" ;;
        postgres | minio)
            if [[ "$follow" == "-f" ]]; then
                compose logs -f --tail 100 "$target"
            else
                compose logs --tail 100 "$target"
            fi
            return
            ;;
        *)
            fail "Неизвестный источник логов: $target"
            echo "Доступно: backend, web, bot, manager, postgres, minio"
            return 1
            ;;
    esac
    if [[ ! -f "$file" ]]; then
        warn "Лог пока пуст: $file"
        return 0
    fi
    if [[ "$follow" == "-f" ]]; then
        tail -f "$file"
    else
        tail -n 100 "$file"
    fi
}

clean_logs() {
    info "Очистка логов в $LOG_DIR"
    find "$LOG_DIR" -maxdepth 1 -name '*.log' -delete
    ok "Логи удалены"
}

# --- Разработка ---------------------------------------------------------------

# Интеграционные тесты работают с реальной БД и удаляют свои данные. Гонять их
# по рабочей базе нельзя: раньше это уже приводило к потере настроек ИИ.
# Адрес тестовой базы берётся из TEST_DATABASE_URL (окружение или .env).
test_database_url() {
    if [[ -n "${TEST_DATABASE_URL:-}" ]]; then
        printf '%s\n' "$TEST_DATABASE_URL"
        return
    fi
    grep -m1 '^TEST_DATABASE_URL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true
}

run_tests() {
    require_python || return 1
    local scope="${1:-all}"
    local -a target
    case "$scope" in
        unit) target=(tests/unit) ;;
        integration) target=(tests/integration) ;;
        all) target=(tests) ;;
        *) fail "Неизвестный набор: $scope (unit|integration|all)"; return 1 ;;
    esac

    # Unit-тесты базу не трогают, для них ничего настраивать не нужно.
    if [[ "$scope" == "unit" ]]; then
        info "Прогон unit-тестов"
        "$VENV_PY" -m pytest "${target[@]}" -q
        return
    fi

    local test_url
    test_url="$(test_database_url)"
    if [[ -z "$test_url" ]]; then
        fail "Не задан TEST_DATABASE_URL — интеграционные тесты не запущены"
        echo "Они удаляют данные в той базе, на которую указывает DATABASE_URL,"
        echo "поэтому рабочая база использоваться не должна. Добавьте в .env строку:"
        echo "  TEST_DATABASE_URL=postgresql+asyncpg://<user>:<pass>@localhost:<порт>/<база>_test"
        return 1
    fi
    if [[ "$test_url" == "$(env_database_url)" ]]; then
        fail "TEST_DATABASE_URL совпадает с рабочей базой — тесты не запущены"
        echo "Укажите отдельную базу для тестов, иначе они удалят рабочие данные."
        return 1
    fi

    local probe
    probe="$(db_probe "$test_url")"
    if [[ "$probe" != ok* ]]; then
        fail "Тестовая база недоступна ($(sed -E 's#^[^@]*@##' <<<"$test_url")): ${probe#fail }"
        return 1
    fi

    info "Прогон тестов на $(sed -E 's#^[^@]*@##' <<<"$test_url")"
    DATABASE_URL="$test_url" "$VENV_PY" -m pytest "${target[@]}" -q
}

check_web() {
    require_web_deps || return 1
    info "Проверка типов и линтера веб-интерфейса"
    ( cd "$WEB_DIR" && npx tsc --noEmit && npx next lint )
}

build_web() {
    require_web_deps || return 1
    # Сборка идёт в отдельный каталог: production-сборка в общий .next ломает
    # работающий dev-сервер («Cannot find module './948.js'»).
    info "Проверочная сборка веб-интерфейса (каталог .next-check)"
    ( cd "$WEB_DIR" && npm run build:check )
}

migrate() {
    require_python && require_env || return 1
    info "Применение миграций (alembic upgrade head)"
    ( cd "$PROJECT_DIR" && "$VENV_PY" -m alembic upgrade head )
}

# --- Справка ------------------------------------------------------------------

show_help() {
    cat <<EOF
$(printf '%b' "${CYAN}Workout Bot Manager${NC}")

$(printf '%b' "${YELLOW}Использование:${NC}")
  $0 <команда> [аргумент]

$(printf '%b' "${YELLOW}Запуск и остановка:${NC}")
  start [all|backend|web|bot|services]   запуск (по умолчанию all: инфраструктура + backend + веб)
  stop  [all|backend|web|bot|services]   остановка
  restart [all|backend|web|bot]          перезапуск
  free-ports [порт...]                   освободить занятые порты проекта

  Логи запущенных служб идут в этот терминал, Ctrl+C останавливает их.
$(printf '%b' "  Флаг ${GREEN}-d${NC} оставляет службы работать в фоне: $0 start -d")

$(printf '%b' "${YELLOW}Наблюдение:${NC}")
  status                    что запущено, к какой базе подключён backend, состояние контейнеров
  doctor                    проверка окружения: .venv, .env, доступность базы, порты
  logs <источник> [-f]      backend | web | bot | manager | postgres | minio
  ai-status                 чек-лист готовности ИИ и последние вызовы
  clean-logs                удалить файлы логов

$(printf '%b' "${YELLOW}Разработка:${NC}")
  test [unit|integration|all]   прогон тестов (интеграционные — только на TEST_DATABASE_URL)
  check                          типы и линтер веб-интерфейса
  build                          проверочная сборка веба (не мешает dev-серверу)
  migrate                        alembic upgrade head

$(printf '%b' "${YELLOW}Примеры:${NC}")
  $0 start                  поднять окружение, логи в терминале, Ctrl+C — стоп
  $0 start -d               то же, но службы остаются в фоне
  $0 restart backend        перезапустить backend после правок кода
  $0 free-ports             убрать забытые процессы проекта, занявшие порты
  $0 free-ports 8019 8020   освободить конкретные порты
  $0 logs backend -f        следить за логами backend, работающего в фоне
  $0 ai-status              понять, почему ИИ не генерирует программу

$(printf '%b' "${DIM}Свои забытые процессы освобождаются автоматически при запуске.${NC}")
$(printf '%b' "${DIM}Посторонние процессы на портах не останавливаются — о них скрипт сообщает.${NC}")
$(printf '%b' "${DIM}Логи: data/logs/, PID-файлы: data/run/${NC}")
EOF
}

# --- Точка входа --------------------------------------------------------------

# После запуска в переднем плане показываем логи и держим службы до Ctrl+C.
hold_foreground() {
    ((${#FOREGROUND_SERVICES[@]} == 0)) && return 0
    echo -e "${DIM}Логи ниже. Ctrl+C останавливает запущенные службы.${NC}"
    echo
    trap foreground_shutdown INT TERM
    supervise
}

main() {
    local command="${1:-help}"
    shift || true

    # Логи по умолчанию идут в этот терминал. Флаг -d/--detach оставляет службы
    # работать в фоне, как раньше.
    local mode="fg"
    local -a args=()
    local arg
    for arg in "$@"; do
        case "$arg" in
            -d | --detach) mode="bg" ;;
            *) args+=("$arg") ;;
        esac
    done
    local target="${args[0]:-all}"

    case "$command" in
    start)
        case "$target" in
            all) start_all "$mode" ;;
            backend) start_backend "$mode" && hold_foreground ;;
            web) start_web "$mode" && hold_foreground ;;
            bot) start_bot "$mode" && hold_foreground ;;
            services) start_services ;;
            *) fail "Неизвестная цель: $target"; return 1 ;;
        esac
        ;;
    stop)
        case "$target" in
            all) stop_all ;;
            backend) stop_service backend "Backend" ;;
            web) stop_service web "Веб-интерфейс" ;;
            bot) stop_service bot "Telegram-бот" ;;
            services) stop_services ;;
            *) fail "Неизвестная цель: $target"; return 1 ;;
        esac
        ;;
    restart)
        case "$target" in
            all) stop_all; sleep 1; start_all "$mode" ;;
            backend) stop_service backend "Backend"; sleep 1; start_backend "$mode" && hold_foreground ;;
            web) stop_service web "Веб-интерфейс"; sleep 1; start_web "$mode" && hold_foreground ;;
            bot) stop_service bot "Telegram-бот"; sleep 1; start_bot "$mode" && hold_foreground ;;
            *) fail "Неизвестная цель: $target"; return 1 ;;
        esac
        ;;
    status) show_status ;;
    doctor) doctor ;;
    free-ports) free_ports "${args[@]}" ;;
    logs) show_logs "${args[0]:-backend}" "${args[1]:-}" ;;
    ai-status) ai_status ;;
    clean-logs) clean_logs ;;
    test) run_tests "${args[0]:-all}" ;;
    check) check_web ;;
    build) build_web ;;
    migrate) migrate ;;
    help | -h | --help) show_help ;;
    *)
        fail "Неизвестная команда: $command"
        echo
        show_help
        return 1
        ;;
    esac
}

main "$@"
