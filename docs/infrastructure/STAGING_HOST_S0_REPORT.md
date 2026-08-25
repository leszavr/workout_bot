# S0 — отчёт о подготовке staging-хоста

**Дата:** 25 августа 2026  
**Проект:** Workout Bot  
**Статус:** PASS WITH WARNINGS

## 1. Host

- Целевой host: `192.168.1.3`
- Назначение: закрытый staging all-in-one host
- Хост подготовлен без запуска приложения Workout Bot.

## 2. Исходное состояние

Первоначальный baseline discovery в `docs/INFRASTRUCTURE_STAGING.md` был
повторно проверен перед изменениями:

- Ubuntu Server 26.04 LTS;
- Intel Pentium G640, 2 физических ядра;
- 8 GiB RAM;
- около 465.8 GiB HDD, root LVM около 100 GiB;
- около 86 GiB свободно в root, swap 4 GiB;
- Docker, PostgreSQL, Redis, MinIO, web server и Workout Bot отсутствовали.

SMART также проверен после установки диагностического пакета: health result
`PASSED`, переназначенных, ожидающих и неисправимых секторов не обнаружено.

## 3. Discovery и safety checkpoint

Повторно проверены OS, сеть, firewall, SSH, пользователи, сервисы, диск,
SMART и egress. Неожиданных прикладных сервисов, контейнеров, БД или
production workload не обнаружено.

**Safety checkpoint: PASS.**

## 4. Выполненные изменения

На удалённом host:

- создан локальный ED25519 key-based доступ пользователя `odmen`;
- подтверждён отдельной SSH-сессией fingerprint
  `SHA256:7v8aHWttLs26xD0FnEN7ZWbthzhl+lUbgmQZVqo56Pw`;
- отключены `PasswordAuthentication` и `KbdInteractiveAuthentication`;
- сохранён backup SSH-конфигурации в `/root/workout-s0-ssh-backup/`;
- включён UFW с deny incoming/forward и allow outgoing для IPv4 и IPv6;
- разрешён только TCP/22;
- установлены Docker Engine и Compose plugin из Ubuntu packages;
- создана Docker network `workout_net`;
- создан `/opt/workout_bot` с каталогами `compose`, `data`, `backups`, `logs`;
- установлен `smartmontools`, выполнена read-only SMART-проверка;
- reboot не выполнялся.

В репозитории:

- уточнён статус повторного S0 discovery в `docs/INFRASTRUCTURE_STAGING.md`;
- создан этот отчёт.

## 5. SSH policy

Финальная политика: key-only SSH на TCP/22. `PermitRootLogin` оставлен в
системном значении `prohibit-password`; root login для deployment не используется.
Password authentication проверена отдельной попыткой и отклоняется. Ключевой
вход проверен в новой независимой сессии.

## 6. Firewall policy

UFW активен и включён при старте. Политика:

- `INPUT DROP`, `FORWARD DROP`, `OUTPUT ACCEPT`;
- loopback и established/related;
- SSH TCP/22 разрешён на IPv4 и IPv6 из LAN/VPN; фактическое ограничение
  внешним административным source будет отдельным изменением после определения
  VPN-подсети;
- необходимые ICMP/ICMPv6;
- без публикации PostgreSQL `5432`, Redis `6379`, MinIO `9000/9001` и
  application ports.

## 7. Docker и layout

Docker Engine `29.1.3` и Compose `2.40.3+ds1-0ubuntu1` активны и включены.
Пользователь `odmen` добавлен в группу `docker`, потому что deployment будет
управляться этим пользователем. Это даёт эквивалент root-доступа через Docker
socket и является осознанным security trade-off.

Создана только пустая инфраструктурная структура
`/opt/workout_bot/{compose,data,backups,logs}`. Владелец каталогов — `root:odmen`,
permissions — `0750`. Application data и конфигурационные файлы не создавались.

## 8. Resource, backup и monitoring policy

До S1 зафиксированы ограничения: 2 CPU cores, 8 GiB RAM, медленный HDD;
PostgreSQL и MinIO чувствительны к I/O, staging не предназначен для большой
нагрузки. Окончательные container limits определяются после фактического
workload.

Backup должен быть направлен во внешнее/off-host хранилище; backup на том же
HDD полноценным backup не считается. В будущем мониторятся CPU, RAM, swap,
disk/inodes, Docker, PostgreSQL, Redis, MinIO, `/health`, `/ready`, generation,
delivery и backup success.

## 9. Verification

| Проверка | Результат |
|---|---|
| Актуальный `main` | PASS — `a58da47` |
| Повторный SSH discovery | PASS |
| SSH key access | PASS — отдельная сессия |
| SSH hardening | PASS — password login отклонён |
| IPv4 firewall | PASS — UFW active, INPUT/FORWARD DROP |
| IPv6 firewall | PASS — UFW v6 active, INPUT/FORWARD DROP |
| Docker Engine | PASS — `29.1.3` |
| Docker Compose | PASS — `2.40.3+ds1-0ubuntu1` |
| Test container | PASS — `hello-world`, удалён после теста |
| Docker daemon | PASS — active/enabled |
| Docker network | PASS — `workout_net`, bridge |
| Disk/RAM/swap/time sync | PASS |
| SMART | PASS — overall-health PASSED |
| Reboot survivability | NOT RUN — reboot не требовался |
| Secrets в изменённых файлах | PASS — не записывались |

## 10. Не сделано намеренно

- не устанавливались PostgreSQL, Redis, MinIO и приложение;
- не менялись EU/RU/mail серверы, DNS, FastPanel, CapRover и роутер;
- не настраивался WireGuard;
- не открывались публичные порты;
- не создавались production credentials и `.env`;
- не выполнялся S1.

## 11. Remaining blockers и prerequisites для S1

1. Перед application deployment определить административную VPN/LAN-подсеть и
   сузить правило SSH вместо текущего разрешения из `Anywhere`.
2. Отдельно решить, нужен ли fail2ban после появления контролируемого VPN-входа.
3. Не выполнять массовый upgrade 4 оставшихся OS-пакетов без отдельного review:
   среди ранее доступных обновлений были system packages, но kernel/SSH/network
   stack в S0 не обновлялись.
4. Перед S1 сохранить внешний backup destination и deployment secrets вне Git.

**Ready for S1: YES, с указанными предупреждениями.**

## 12. Финальная сводка

```text
S0 STATUS: PASS WITH WARNINGS
Host: 192.168.1.3
OS: Ubuntu Server 26.04 LTS, kernel 7.0.0-30-generic
Docker: 29.1.3, Compose 2.40.3, active/enabled
SSH: key-only, password authentication disabled
Firewall: UFW active, IPv4/IPv6 deny incoming
IPv4: verified, egress works
IPv6: verified, egress works, firewall active
Disk: 98G root, 86G free; 363G LVM free
RAM: 7.2GiB, 4GiB swap, swap unused
Reboot: не выполнялся
Security: SSH key-only, UFW active, no public application ports
Unexpected findings: 4 pending OS updates remain; fail2ban not installed
```

**Commit SHA:** будет указан после создания documentation commit.  
**Изменённые файлы:**

- `docs/INFRASTRUCTURE_STAGING.md`
- `docs/infrastructure/STAGING_HOST_S0_REPORT.md`

**Git status:** проверяется перед commit.  
**Verification:** host discovery, SSH, UFW, Docker, test container, resources,
network, time sync и SMART проверены. OS updates не выполнялись.  
**Review перед S1:** желателен review firewall source policy и Docker group
решения перед application deployment.
