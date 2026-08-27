# Personal VPN на Windows — настройка

Хост: `HOME-PC\svv`. Файлы уже подготовлены в `C:\Users\svv\vpn\`:

| Файл | Что это |
|---|---|
| `wg-personal.conf` | конфиг туннеля для WireGuard |
| `wstunnel.exe` | TLS-транспорт (10.6.2, windows_amd64) |
| `start-vpn.cmd` | запуск транспорта |

Peer на узле: `windows-pc`, адрес `10.10.20.4`, отдельные ключи и preshared-ключ.
Ключи `phone` и `laptop` не переиспользованы.

## Почему два шага, а не только WireGuard

Ваш провайдер распознаёт WireGuard **по поведению потока** и обрывает его через
несколько секунд. Замерено на этой же сети: handshake проходит, 3–8 секунд
трафик идёт, дальше тишина при живом `wg show`. Порт, размер пакетов и версия IP
не помогают — проверялось отдельными тестами.

Поэтому WireGuard подключается не к узлу напрямую, а к `127.0.0.1:51821`, где
слушает `wstunnel.exe`. Он заворачивает трафик в TLS поверх IPv6 TCP/443 — такой
поток провайдер не трогает. Проверено на реальном пути этой сети:
полнотуннельный прогон, 20 запросов подряд, `ok=20 fail=0`, egress
`31.58.181.202`.

Важно: `wstunnel.exe` — обычный процесс без прав администратора, он **не**
конфликтует с `nekoray-tun`, потому что не меняет маршруты. Маршруты меняет
только WireGuard, когда вы включаете туннель.

## Шаг 1. Установить WireGuard for Windows

Сейчас он не установлен. Любой способ:

```powershell
winget install --id WireGuard.WireGuard
```

или установщик с https://www.wireguard.com/install/ (нужны права
администратора — один раз, при установке).

## Шаг 2. Запустить транспорт

Двойной клик на `C:\Users\svv\vpn\start-vpn.cmd`.

Окно не закрывать, пока нужен VPN. В нём будут строки вида
`Starting wstunnel to the EU hub`. Права администратора не нужны.

## Шаг 3. Импортировать туннель

WireGuard → **Импорт туннелей из файла** → `C:\Users\svv\vpn\wg-personal.conf`.

Импорт делается один раз.

## Шаг 4. Подключиться

Кнопка **Подключиться** в WireGuard.

Проверка:

```powershell
(Invoke-WebRequest https://api.ipify.org -UseBasicParsing).Content
# должно быть 31.58.181.202
```

Порядок обязателен: сначала `start-vpn.cmd`, потом WireGuard. Если запустить
наоборот, туннель не поднимется — ему некуда подключаться.

## Отключение

Кнопка **Отключиться** в WireGuard, затем закрыть окно `start-vpn.cmd`.

## Взаимодействие с nekoray-tun

Пока `nekoray-tun` активен, он остаётся в маршрутах, и часть трафика может идти
через него: у него свой TUN-интерфейс и свои метрики. Два full-tunnel VPN
одновременно всегда конкурируют за default route.

Практический порядок, если нужен именно EU-выход:

1. отключить nekoray,
2. `start-vpn.cmd`,
3. подключить WireGuard.

Если nekoray нужен постоянно для доступа к моделям — сделайте personal VPN
частичным (см. следующий раздел), тогда конфликта не будет.

## Вариант: split tunnel вместо full tunnel

Если через EU нужен не весь трафик, а только конкретные сервисы, замените в
`wg-personal.conf` строку

```ini
AllowedIPs = 0.0.0.0/0
```

на список нужных сетей, например только Telegram:

```ini
AllowedIPs = 149.154.160.0/20, 91.108.4.0/22, 91.108.8.0/22, 91.108.12.0/22, 91.108.16.0/22, 91.108.56.0/22, 95.161.64.0/20, 10.10.20.0/24
```

Так локальная сеть, nekoray и обычный интернет остаются как были. Минус тот же,
о котором сказано в основном отчёте: список адресов ломается, если сервис их
меняет. Для Telegram-подсетей это редко, но возможно.

Если нужен весь интернет, кроме локальной сети, вместо `0.0.0.0/0` подойдёт
готовый список из 40 подсетей, исключающий RFC1918 (можно сгенерировать
калькулятором AllowedIPs или взять из `docs/infrastructure/EU_VPN_HUB_REPORT.md`,
приложение).

## Автозапуск транспорта (необязательно)

Чтобы не запускать `start-vpn.cmd` вручную, добавьте задачу:

```powershell
$a = New-ScheduledTaskAction -Execute "C:\Users\svv\vpn\wstunnel.exe" `
  -Argument '<аргументы из start-vpn.cmd, одной строкой>' `
  -WorkingDirectory "C:\Users\svv\vpn"
$t = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "wstunnel-personal-vpn" -Action $a -Trigger $t `
  -Settings (New-ScheduledTaskSettingsSet -Hidden -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1))
```

Аргументы возьмите из `start-vpn.cmd` (там они разбиты на строки символом `^`).

## Управление peer'ами на узле

```bash
ssh eu-vpn-hub
sudo vpn-peer list                # состояние всех personal-устройств
sudo vpn-peer add <имя>           # новое устройство
sudo vpn-peer qr <имя>            # QR для телефона
sudo vpn-peer revoke <имя>        # отозвать доступ
```

## Что этот VPN не даёт

Доступа к Workout staging: PostgreSQL, Redis, MinIO, backend, frontend, SSH
`192.168.1.3`, Docker-сеть `172.18.0.0/16` и зона `10.10.10.0/24` для
personal-устройств закрыты на firewall узла. Это проверено по каждому пункту и
описано в §8 основного отчёта.
