# EU VPN Hub — отчёт (Amsterdam, 31.58.181.202)

Дата: 27.08.2026. Исполнитель: агент. Хосты: EU VPS `31.58.181.202`,
Workout staging `192.168.1.3`.

## 1. Executive Summary

```text
EU VPS:            PASS
Workout VPN:       PASS
Telegram access:   PASS
Personal VPN:      PASS
VPN management UI:  NOT INSTALLED (вместо него CLI vpn-peer, см. §7)
Security:          PASS WITH WARNINGS
```

Главный функциональный критерий достигнут: Telegram Gateway на `192.168.1.3`
ходит к `api.telegram.org` через EU-узел, `bot.me()` проходит, контейнер не в
restart loop (`RestartCount=0`).

Ключевой факт, обнаруженный в процессе и определивший архитектуру: локальный
провайдер staging-хоста не только блокирует Telegram, но и **распознаёт и
глушит WireGuard**, а также **полностью блокирует IPv4 TCP до EU-узла**. Чистый
WireGuard-туннель на UDP давал handshake и 3–8 секунд трафика, после чего
замолкал. Поэтому WireGuard завёрнут в TLS/WebSocket (`wstunnel`) поверх
**IPv6 TCP/443**. Подробный разбор в §9.

## 2. Server baseline (EU VPS)

| Параметр | Значение |
|---|---|
| OS | Ubuntu 24.04.4 LTS |
| Kernel | 6.8.0-138-generic (после контролируемого reboot; было 6.8.0-35) |
| CPU | 1 vCPU, Intel Xeon Skylake (KVM, OpenStack Nova) |
| RAM | 1967 MB, swap отсутствует |
| Disk | 19 GB ext4, занято 1.9 GB (11%) |
| IPv4 | `31.58.181.202/24`, шлюз `31.58.181.1` |
| IPv6 | `2a13:7c00:11:12:f816:3eff:fe83:4843/64`, SLAAC + RA |
| DNS | `1.1.1.1` через systemd-resolved |
| Time | UTC, NTP активен, синхронизирован |
| Hostname | `amsterdam-vpn` |

Состояние до работ (Phase 0): сервер чистый. Не найдено Docker, Podman, nginx,
Apache, Caddy, PostgreSQL, Redis, MinIO, OpenVPN, WireGuard, web-панелей и
контейнеров. Публичных listeners было два: `sshd` на 22 и `systemd-resolved` на
localhost. Firewall отсутствовал (`iptables -P ACCEPT` во всех цепочках),
`ip_forward=0`. STOP CONDITION не сработало.

Исходящая доступность с EU VPS проверена сразу: `api.telegram.org` (302),
`routerai.ru` (200), `github.com` (200), Docker registry (404 — ожидаемо для
корня API), `tabitoken.com` (403 — тот же защитный контур, что и на staging).
IPv6 работает, но у него нет DNS-резолвинга для внешних имён без явного AAAA:
`getent` отдаёт AAAA для `api.telegram.org`, а для `github.com` — нет.

## 3. Installed components

На EU VPS установлено:

| Компонент | Версия | Назначение |
|---|---|---|
| wireguard-tools | 1.0.20210914-1ubuntu4 | оба VPN-зоны |
| nftables | 1.0.9-1ubuntu0.1 | firewall, NAT |
| fail2ban | 1.0.2-3ubuntu0.1 | защита sshd |
| unattended-upgrades | 2.9.1+nmu4ubuntu1 | автообновления безопасности |
| qrencode | 4.1.1-1build2 | QR-экспорт personal-конфигов |
| wstunnel | 10.6.2 | TLS-обёртка для Workout-туннеля |
| tcpdump, dnsutils, netcat-openbsd | из репозитория | диагностика |

Systemd-units, включённые в автозапуск: `nftables`, `wg-quick@wg-workout`,
`wg-quick@wg-personal`, `wstunnel-server`, `fail2ban`,
`unattended-upgrades`, `ssh.socket`.

Обновления: применены все доступные (`procps`, `libproc2-0`), выполнен
контролируемый reboot, ядро поднялось до 6.8.0-138. Major OS upgrade не
делался. `unattended-upgrades` включён для security-origins, но
`Automatic-Reboot=false`: неожидаемый ночной reboot уронил бы Workout-туннель
без наблюдения.

На staging-хосте `192.168.1.3` добавлено: `wireguard-tools 1.0.20250521`,
`wstunnel 10.6.2`. Ничего не удалялось, S1-volumes не тронуты.

## 4. Firewall

Политика `deny-by-default` реализована одной таблицей `nftables` семейства
`inet`, поэтому IPv6 не может обойти намерение правил IPv4 — это была отдельная
проверка из задания. Файл: `/etc/nftables.conf`, unit `nftables.service`
включён.

Публично разрешено (только на `eth0`):

```text
TCP/22   sshd                — управление
TCP/443  wstunnel-server     — транспорт Workout-туннеля (WSS)
UDP/51821 wg-personal        — personal VPN
UDP/67→68                    — DHCPv4 renewal
ICMP echo/unreach/ttl, ICMPv6 — PMTU и ND/RA
```

Явно НЕ публично: PostgreSQL 5432, Redis 6379, MinIO 9000/9001, Docker daemon,
любые внутренние сервисы Workout. Их на EU VPS вообще нет, и firewall их не
пропустил бы.

`UDP/51820` (wg-workout) **закрыт для WAN**. Он слушает только соединения,
которые `wstunnel` подаёт с localhost. Это осознанное решение: публичный
WireGuard-порт для Workout-зоны бесполезен, потому что провайдер staging его
всё равно глушит, а лишний открытый порт — лишняя поверхность.

Цепочка `forward` тоже `policy drop`. Разрешён ровно исходящий транзит из каждой
VPN-зоны в `eth0` с проверкой source-адреса, плюс `tcp option maxseg size set
rt mtu` (MSS clamping) — без него TLS через туннель с MTU 1420 ломался бы на
больших ответах.

NAT: `table inet nat`, `masquerade` в `eth0` для `10.10.10.0/24`,
`10.10.20.0/24` и соответствующих ULA-префиксов.

Отдельная деталь, которую стоит знать при правке правил: в файле специально
**нет** `flush ruleset`. Вместо него удаляются только собственные таблицы
(`inet filter`, `inet nat`). Глобальный flush удалял бы и runtime-таблицу
fail2ban `inet f2b-table`, и активные баны исчезали бы, пока fail2ban считает
их действующими. Проверено: после `nft -f /etc/nftables.conf` бан остаётся.

### Проверка публичной доступности

Сканирование с независимого внешнего хоста (`217.60.186.52`) по IPv4: **все**
проверенные порты закрыты, включая 22 и 443. Причина — не firewall EU VPS, а
блокировка IPv4-маршрута до `31.58.181.0/24` со стороны российского провайдера
(тот же эффект, что и на staging). Сканирование по IPv6 со staging-хоста дало
корректную картину: открыты только `22` и `443`, всё остальное закрыто, включая
`51820`, `51821` (UDP-порты TCP-сканом не видны) и порты БД.

Локальная машина-оркестратор для сканирования непригодна: её сеть отвечает
SYN-ACK на любой порт любого адреса (проверено на `192.0.2.1:22` → «open»), то
есть присутствует прозрачный перехват TCP. Все выводы о публичной поверхности
сделаны по внешним хостам и по `ss -tulpn` на самом узле.

## 5. SSH security

| Параметр | Значение |
|---|---|
| Port | 22 |
| PermitRootLogin | no |
| PasswordAuthentication | no |
| KbdInteractiveAuthentication | no |
| PubkeyAuthentication | yes |
| AllowUsers | odmen |
| MaxAuthTries | 4 |
| LoginGraceTime | 30 |
| X11Forwarding | no |

Порядок действий был именно таким, как требует задание: сначала создан
непривилегированный пользователь `odmen` с `sudo NOPASSWD`, установлен новый
ключ, **отдельным независимым SSH-сеансом** подтверждён вход по ключу, и только
после этого отключена парольная аутентификация. Отказ пароля затем проверен
явно: `root@31.58.181.202` с паролем → `Permission denied (publickey)`.

Ключ: `~/.ssh/eu-vpn-hub` (ed25519, comment `eu-vpn-hub-admin`), алиас в
`~/.ssh/config` — `eu-vpn-hub`. Приватный ключ не покидал управляющую машину,
в Git не попал, в отчёте не приводится.

Две детали, которые ломают hardening на Ubuntu 24.04 и потому обработаны
отдельно:

1. `/etc/ssh/sshd_config.d/50-cloud-init.conf` содержал
   `PasswordAuthentication yes`. Файл с меньшим номером имеет приоритет, поэтому
   наш файл назван `00-hardening.conf`, а в cloud-init-файле значение исправлено
   на `no`.
2. Добавлен `/etc/cloud/cloud.cfg.d/99-disable-pwauth.cfg` с
   `ssh_pwauth: false` и `disable_root: true`, иначе cloud-init мог вернуть
   пароль при пересборке образа.

Порт SSH не менялся: obscurity вместо аутентификации задание запрещает.
fail2ban с jail `sshd` (backend systemd, bantime 1h, maxretry 5) активен и уже
забанил один сканирующий адрес во время работ.

Rate-limit на TCP/22 сознательно **не** применён: парольная аутентификация
отключена, брутфорс успеха не даст, а лимит рискует выбить легитимную
автоматизацию, пока публичные сканеры выедают бюджет пакетов. Абузивные адреса
отсекает fail2ban.

Root-доступ по паролю на EU VPS больше невозможен; пароль root в отчёте и в Git
не сохранён.

## 6. Workout VPN architecture (ZONE A)

```text
docker-telegram-bot-1  (172.18.0.20, сеть workout_net)
        │  policy routing по source-адресу (ip rule → table 51820)
        ▼
wg-workout на staging  10.10.10.2
        │  Endpoint = 127.0.0.1:51820
        ▼
wstunnel-client (staging)  ──TLS/WSS поверх IPv6 TCP/443──▶  wstunnel-server (EU VPS)
                                                                    │ --restrict-to 127.0.0.1:51820
                                                                    ▼
                                                        wg-workout на EU VPS  10.10.10.1
                                                                    │ NAT/masquerade
                                                                    ▼
                                                            api.telegram.org
```

Зона: `10.10.10.0/24` + `fd00:10:10::/64`. Единственный peer — staging-хост.
Инициатор туннеля всегда staging (он за NAT, публичного IPv4 не имеет),
`PersistentKeepalive = 25`. EU VPS никогда не инициирует соединение к staging.

### Selective routing вместо списка IP Telegram

Задание прямо запрещает хрупкий статический список адресов Telegram и требует
предложить устойчивую схему. Выбран вариант «отдельный контейнер целиком в
туннель», а не «список адресов»:

- в `docker/staging-app-compose.yml` сервису `telegram-bot` закреплён адрес
  `172.18.0.20` (переменная `TELEGRAM_BOT_IP`, значение по умолчанию то же);
- на хосте три правила `ip rule` по source-адресу: локальные направления
  (`172.18.0.0/16`, `192.168.1.0/24`) остаются в `main`, всё остальное уходит в
  таблицу `51820`, где default — `dev wg-workout`;
- `wg-quick` поднят с `Table = off`, поэтому **default route хоста не
  меняется**: backend, frontend, PostgreSQL, Redis, MinIO продолжают ходить
  через локального провайдера.

Схема устойчива к смене IP-адресов Telegram, не зависит от DNS и не требует
списков. Ограничение: она привязана к фиксированному адресу контейнера. Если
адрес меняют, надо править и compose, и `ip rule` — это отмечено комментарием в
обоих местах.

Проверено фактическое разделение путей:

```text
docker-telegram-bot-1  → внешний IP 31.58.181.202  (через EU)
docker-backend-1       → внешний IP 91.78.244.143  (локальный провайдер)
```

### Что именно установлено на staging

| Путь | Назначение |
|---|---|
| `/etc/wireguard/wg-workout.conf` | клиентский конфиг зоны A, 0600 |
| `/etc/wireguard/keys/` (на EU VPS) | все приватные ключи и PSK, 0600/0700 |
| `/etc/wstunnel/client.env` | shared-secret префикс пути, 0600 |
| `/etc/systemd/system/wstunnel-client.service` | TLS-транспорт |
| `/etc/systemd/system/wg-quick@wg-workout.service.d/10-wstunnel.conf` | ordering |

Drop-in `10-wstunnel.conf` решает реальную проблему холодного старта:
`wg-quick` поднимается раньше, чем `wstunnel` успевает забиндить
`127.0.0.1:51820`. Он добавляет `Requires`/`After` и `ExecStartPre`, который до
30 секунд ждёт появления сокета.

MSS clamping добавлен и на staging (`iptables -t mangle -A FORWARD -o wg-workout
... TCPMSS --clamp-mss-to-pmtu`), правило восстанавливается через `PostUp` и
проверено после reboot.

Docker-специфика, из-за которой схема работает без дополнительных правил:
`DOCKER-FORWARD` уже содержит `-i br-599d5a1e8212 -j ACCEPT`, а
`nat POSTROUTING` — `-s 172.18.0.0/16 ! -o br-... -j MASQUERADE`. Поэтому
пакеты контейнера уходят в `wg-workout` с source `10.10.10.2`, и EU-узел видит
их как трафик своего peer. UFW на staging не менялся (политика
`deny incoming / deny routed` сохранена).

## 7. Personal VPN architecture (ZONE B)

```text
Personal device (phone / laptop)
        │  WireGuard, UDP/51821, публичный порт
        ▼
wg-personal на EU VPS  10.10.20.1
        │  NAT/masquerade
        ▼
Internet
```

Зона: `10.10.20.0/24` + `fd00:10:20::/64`. Полностью независима от зоны A:
отдельная пара ключей сервера, отдельные ключи и preshared-ключи peer'ов,
отдельный порт, отдельная подсеть, отдельный конфиг
`/etc/wireguard/wg-personal.conf`. Ни один ключ не переиспользован.

Созданы два peer'а: `phone` (`10.10.20.2`) и `laptop` (`10.10.20.3`).
Клиентские конфиги лежат только на управляющей машине в `~/vpn-configs/`
(режим 0600, каталог 0700) и в Git не попадают.

### Управление вместо web UI

Задание требовало сначала оценить, нужен ли web UI, и не жертвовать
инфраструктурой Workout ради интерфейса. Оценка: типовые панели
(wg-easy, wg-portal и аналоги) на этом узле дают отрицательный баланс. Они
хотят Docker и собственный публичный порт с ещё одной парольной границей на
хосте, единственная задача которого — быть сетевым шлюзом с двумя открытыми
портами. Кроме того, они управляют интерфейсом целиком и могли бы переписать
конфиг зоны A, то есть уронить Telegram-канал.

Поэтому web UI **не установлен**, а управление personal-зоной сделано скриптом
`/usr/local/sbin/vpn-peer` (0750, root-only), который закрывает ровно те
требования, что были в задании:

```bash
vpn-peer list              # peer'ы, адреса, последний handshake, трафик
vpn-peer add <name>        # новый peer, свободный адрес выбирается сам
vpn-peer show <name>       # клиентский конфиг в stdout
vpn-peer qr <name>         # тот же конфиг QR-кодом для телефона
vpn-peer revoke <name>     # снять peer с интерфейса и стереть ключи (shred)
```

Скрипт работает только с `wg-personal`; зону A он не трогает вообще. Изменения
применяются через `wg set` (живые peer'ы не рвутся) и одновременно пишутся в
конфиг, поэтому переживают reboot. Полный цикл add → list → show → qr → revoke →
restart проверен: после revoke peer исчезает и из runtime, и из файла, число
`[Peer]`-блоков остаётся консистентным.

Web UI остаётся возможным follow-up: если он понадобится, разумный вариант —
слушать только на `10.10.20.1` и пускать исключительно через personal VPN, без
публичного порта.

## 8. Routing and isolation

Критическое требование задания — personal-peer'ы не должны получать доступ к
Workout-инфраструктуре, а Workout-туннель не должен открывать staging LAN
personal-пользователям. Реализовано на четырёх уровнях, а не одним правилом:

1. **AllowedIPs.** Каждому peer'у выдан только его `/32` и `/128`. WireGuard
   сам отбрасывает пакеты с чужим source-адресом внутри туннеля
   (cryptokey routing).
2. **Изоляция зон в `forward`.** Явные `drop` на `wg-personal → wg-workout` и
   обратно, плюс drop по адресам подсетей и запрет hairpin внутрь того же
   интерфейса.
3. **Запрет приватных сетей через узел.** Трафик из любой зоны в
   `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`,
   `127.0.0.0/8`, `100.64.0.0/10`, `fc00::/7`, `fe80::/10` отбрасывается. Именно
   это правило закрывает `192.168.1.3` и `172.18.0.0/16` для personal-устройств,
   даже если маршрут туда когда-нибудь появится.
4. **Ограничение адресации самого узла.** Peer зоны A может обращаться только к
   `10.10.10.1` / `fd00:10:10::1`, peer зоны B — только к `10.10.20.1` /
   `fd00:10:20::1`. Всё остальное — `drop`.

Пункт 4 добавлен по результату первого прогона тестов: до него personal-peer
пинговал `10.10.10.1` — адрес узла в Workout-зоне. Доступа к staging это не
давало, но размывало границу зон, поэтому правило было ужесточено и тест
перезапущен.

Единственный сервис узла, доступный из туннеля, — `sshd` на `10.10.20.1:22`
(резервный путь управления, если публичный 22 когда-нибудь закроют). DNS,
proxy и прочие сервисы на VPN-адресах не слушают.

Направление «staging → personal» закрыто симметрично, но дополнительно
ограничено самой схемой: на staging туннель не является default route, в него
попадает только трафик одного контейнера.

## 9. Почему не «просто WireGuard»: диагностика транспорта

Этот раздел важен для будущих сессий: без него конфигурация выглядит
неоправданно сложной.

Первая рабочая схема была канонической: WireGuard UDP/51820 напрямую на
`31.58.181.202`. Она давала handshake и затем умирала. Замеры:

```text
burst 1 (сразу после handshake)  0% loss
burst 2 (+5 c)                   33% loss
burst 3 (+10 c)                  100% loss
burst 4..n                       100% loss
```

При этом `wg show` на staging продолжал наращивать `tx`, а на EU-узле `rx` не
менялся: пакеты уходили с хоста (подтверждено `tcpdump` на `enp3s0`) и не
доходили до узла (подтверждено `tcpdump` на `eth0`).

Дальше проверялись гипотезы по одной:

| Тест | Результат | Вывод |
|---|---|---|
| Случайные 148-байтные UDP-датаграммы на 51820 | доходят | это не размер и не порт |
| Датаграммы с байтом типа WireGuard (`0x01`) | доходят | сигнатура в одном пакете не триггер |
| WireGuard после смены source-порта | снова 3–8 с и тишина | блокируется **поток**, а не порт |
| Перенос WireGuard на UDP/8443 | тот же обрыв | порт не имеет значения |
| Переход на IPv6-endpoint | тот же обрыв | не зависит от версии IP |
| Sustained UDP/8443 (128 байт, 80 с) | 67/67 без потерь | UDP сам по себе не режется |
| Sustained UDP/51822 с WG-образными payload | 61/61 без потерь | форма пакетов не триггер |
| Sustained TCP/8443 (70 с) | без обрывов | TCP стабилен |
| IPv4 TCP до узла (22/80/443/8080/8443) | все FAIL | IPv4 TCP до узла заблокирован целиком |
| IPv4 TCP до `31.58.181.1` (шлюз провайдера узла) | FAIL | блокируется подсеть, не хост |
| IPv4 TCP до Hetzner/Cloudflare | OK | у staging нет общей блокировки TCP |
| IPv6 TCP/22 до узла | OK, но соединение сбрасывается через ~4 с | SSH-поток к этому адресу тоже глушится |
| IPv6 TCP/8443 до узла | стабильно 70 с | не-SSH TCP-порт не глушится |

Итоговая картина: провайдер staging (a) выборочно блокирует IPv4-префикс
EU-узла, (b) распознаёт WireGuard по поведению потока и обрывает его через
несколько секунд, (c) обрывает и SSH-поток к этому адресу. Не блокируется:
IPv6 TCP на нестандартный порт и обычный UDP без WireGuard-семантики.

Отсюда транспорт: WireGuard внутри TLS/WebSocket на IPv6 TCP/443 через
`wstunnel`. После переключения — шесть подряд серий ping без единой потери,
затем два soak-теста по 20 реальных HTTPS-запросов к Telegram API из контейнера:
`ok=20 fail=0` в обоих.

Что это значит для эксплуатации: **канал зависит от IPv6 у staging-хоста**. Если
IPv6 у локального провайдера пропадёт, туннель встанет. Это зафиксировано в §12
как основной риск.

## 10. Test results

| Test | Result |
|---|---|
| SSH key login (независимый сеанс) | PASS |
| Password SSH disabled | PASS |
| Root SSH login disabled | PASS |
| IPv4 firewall (deny-by-default) | PASS |
| IPv6 firewall (та же таблица inet) | PASS |
| EU → Telegram / RouterAI / GitHub | PASS |
| Workout tunnel handshake | PASS |
| Workout tunnel sustained (6 серий ping, 0% loss) | PASS |
| Staging → Telegram via EU (curl из контейнера, 302) | PASS |
| Разделение путей (bot → EU, backend → локальный ISP) | PASS |
| `bot.me()` в контейнере gateway | PASS (`id=7903710552`, `wrkoutassist_bot`) |
| Gateway restart counter | PASS (`RestartCount=0`, 36 опросов за ~18 мин) |
| Telegram soak до reboot (20 запросов) | PASS (`ok=20 fail=0`) |
| Telegram soak после reboot (20 запросов) | PASS (`ok=20 fail=0`) |
| Personal VPN Internet | PASS (egress `31.58.181.202`, Telegram 302, GitHub 200) |
| Personal → staging PostgreSQL/Redis/MinIO | PASS (blocked) |
| Personal → staging backend/frontend/SSH | PASS (blocked) |
| Personal → Docker-сеть `172.18.0.0/16` | PASS (blocked) |
| Personal → Workout peer `10.10.10.2` | PASS (unreachable) |
| Personal → адрес узла в зоне A `10.10.10.1` | PASS (unreachable после ужесточения) |
| wstunnel: неверный path prefix | PASS (HTTP 400) |
| wstunnel: `--restrict-to` на чужой destination | PASS (400, туннель не поднялся) |
| Reboot recovery EU VPS | PASS (SSH через ~20 с, все unit'ы active) |
| Reboot recovery staging | PASS (SSH ~35 с, туннель и стек сами поднялись) |
| Туннель после reboot узла, без вмешательства | PASS (handshake 21 с, 0% loss) |
| fail2ban после reboot | PASS (jail активен) |
| nftables reload не стирает баны fail2ban | PASS |

## 11. Secrets handling

```text
No private keys committed
No passwords committed
No tokens committed
```

Где что лежит:

| Секрет | Место | Режим |
|---|---|---|
| Приватные ключи и PSK обеих зон | EU VPS `/etc/wireguard/keys/` | 0600, каталог 0700 |
| Приватный ключ peer'а staging | staging `/etc/wireguard/wg-workout.conf` | 0600 |
| shared-secret пути wstunnel | оба хоста `/etc/wstunnel/{secret,client.env}` | 0600 |
| TLS-ключ wstunnel | EU VPS `/etc/wstunnel/tls/key.pem` | 0640 `root:wstunnel` |
| SSH-ключ администратора | управляющая машина `~/.ssh/eu-vpn-hub` | 0600 |
| Клиентские конфиги personal VPN | управляющая машина `~/vpn-configs/` | 0600, каталог 0700 |

В Git попали только: изменение `docker/staging-app-compose.yml`, новая
переменная-плейсхолдер в `docker/staging-app.env.example` и этот отчёт.
Публичные ключи серверов приведены в отчёте намеренно — они не секрет:

```text
wg-workout  server pubkey: koPsiFJVvIFJllGuKEci5/gzzf2Ti72q0LJ0jDEaUVE=
wg-personal server pubkey: PAHHS6TFzh1RTsxRHDE0IOwHTrbV0Tw3LpdZ774Sx0s=
```

Значения `BOT_TOKEN`, `ADMIN_PASSWORD`, `JWT_SECRET`, `DATABASE_URL`, MinIO- и
AI-ключей не выводились и не менялись. Файл `staging-app.env` не
перезаписывался. Root-only `/opt/workout_bot/compose/staging.env` не читался.

## 12. Public endpoints

EU VPS `31.58.181.202` / `2a13:7c00:11:12:f816:3eff:fe83:4843`:

| Endpoint | Обоснование |
|---|---|
| TCP/22 sshd | управление; только по ключу, только `odmen`, под fail2ban |
| TCP/443 wstunnel | транспорт Workout-туннеля; неверный path prefix → 400, destination ограничен `127.0.0.1:51820` |
| UDP/51821 wg-personal | personal VPN; аутентификация по ключам + PSK, на неверный пакет молчит |

Больше публичных listeners нет: `systemd-resolved` слушает только localhost,
`wg-workout` (UDP/51820) закрыт firewall'ом для WAN.

Staging `192.168.1.3` в части публикации не изменялся: как и раньше, в LAN
доступны только `3000` (Admin Web) и `8000` (Backend API); PostgreSQL, Redis и
MinIO остаются приватными.

## 13. Remaining warnings

1. **Канал зависит от IPv6 у staging.** Транспорт `wstunnel` идёт на
   IPv6-адрес узла, потому что IPv4 TCP до его префикса заблокирован локальным
   провайдером. Если IPv6 пропадёт, Telegram-канал встанет. Обходной путь при
   необходимости: получить у провайдера EU VPS адрес из другого IPv4-префикса
   либо поставить второй фронтенд в другой сети.
2. **Self-signed TLS у wstunnel.** Клиент подключается без проверки
   сертификата, защита от MITM опирается на WireGuard внутри (шифрование и
   аутентификация peer'ов остаются полноценными) и на shared-secret путь. Для
   строгого варианта нужен домен и Let's Encrypt.
3. **Привязка к `172.18.0.20`.** Правила `ip rule` на staging знают конкретный
   адрес контейнера. Смена адреса требует правки в двух местах; смена подсети
   `workout_net` — тоже.
4. **`docker0` на staging остался `DOWN` с подсетью `172.17.0.0/16`.** На
   маршрутизацию не влияет, но это лишняя запись в таблице.
5. **VPN management web UI не установлен** (обоснование в §7). Управление
   personal-зоной — через `vpn-peer`.
6. **1 vCPU / 2 GB и отсутствие swap** на EU VPS. Для текущей нагрузки
   (два WireGuard-интерфейса + wstunnel) достаточно, но запаса нет; при росте
   personal-трафика узел станет узким местом раньше, чем канал.
7. **Passwordless sudo для `odmen`** на EU VPS (`/etc/sudoers.d/90-odmen`).
   Сделано осознанно для автоматизации, но это означает: компрометация
   SSH-ключа равна root на узле.
8. **Root SSH на staging.** Для выполнения задачи потребовался root-доступ к
   `192.168.1.3`; он настроен по существующему ключу
   `~/.ssh/workout_staging_ed25519` (`/root/.ssh` 700, `authorized_keys` 600).
   У `odmen` на staging passwordless sudo **не** настраивался, пароль sudo
   нигде не сохранён. Если постоянный root-доступ по ключу не нужен, его стоит
   отозвать отдельной задачей.

## 14. Next required action

1. Провести живой Telegram E2E: анкета → профиль → генерация → доставка. Сетевой
   блокер, который держал это раньше, снят; остаётся согласовать retention
   тестовых данных (см. `ANSWERS.md`, §5).
2. Решить по warning 1: нужен ли резервный транспорт, если IPv6 пропадёт.
3. Раздать personal-конфиги на устройства (`vpn-peer show` / `vpn-peer qr`) и при
   необходимости отозвать неиспользуемый peer `laptop`.
4. Отдельной задачей — Tabitoken: теперь есть возможность проверить его через
   EU-адрес и понять, был ли `403` географической блокировкой.
5. Решить судьбу root-доступа по ключу на staging (warning 8).

## Приложение: эксплуатация

Состояние туннеля на EU VPS:

```bash
sudo wg show
sudo systemctl status wstunnel-server nftables wg-quick@wg-workout wg-quick@wg-personal
sudo nft list ruleset
sudo vpn-peer list
```

Состояние на staging:

```bash
sudo systemctl status wstunnel-client wg-quick@wg-workout
sudo wg show wg-workout
ip rule show | grep 172.18
ip route show table 51820
```

Быстрая проверка, что Telegram идёт через EU:

```bash
docker exec docker-telegram-bot-1 python -c \
  "import urllib.request as u; r=u.Request('https://api.ipify.org', headers={'User-Agent':'curl/8'}); print(u.urlopen(r, timeout=15).read().decode())"
# ожидается 31.58.181.202
```

Восстановление после сбоя туннеля на staging:

```bash
sudo systemctl restart wstunnel-client
sudo systemctl restart wg-quick@wg-workout
```

Порядок важен: `wg-quick@wg-workout` требует уже поднятого локального
listener'а `wstunnel`; drop-in `10-wstunnel.conf` ждёт его до 30 секунд.
