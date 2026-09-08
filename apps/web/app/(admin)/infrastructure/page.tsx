"use client";

// Инфраструктура: состояние развёрнутых компонентов и безопасность обновления.
//
// Компоненты разворачиваются независимо и в разных сегментах сети (сервер в
// RU, шлюз Telegram в EU), поэтому их версии в общем случае не совпадают.
// Совместимость определяет сервер по версии контракта; интерфейс только
// показывает готовый вердикт и не сравнивает версии сам — иначе правила
// совместимости жили бы в двух местах и со временем разошлись.
//
// Ручного добавления коннекторов здесь нет: компонент попадает в список,
// когда сам сообщает о себе. Иначе раздел показывал бы желаемое состояние
// вместо фактического.
//
// Раньше карточки подробностей рисовались подряд под таблицей: при трёх
// компонентах страница была лентой из повторяющихся блоков, а список кнопок
// «Удалить <id>» стоял отдельной карточкой в конце и не был связан со строкой,
// к которой относится. Теперь подробности раскрываются по строке, а удаление
// живёт в самой строке.

import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataColumn, DataTable } from "@/components/ui/DataTable";
import {
  Card,
  ErrorState,
  KeyValue,
  Notice,
  Status,
  Tag,
} from "@/components/ui/Primitives";
import {
  ComponentItem,
  ComponentsResponse,
  DeploymentSafetyReport,
  componentsApi,
} from "@/lib/api";
import {
  capabilityLabel,
  componentStateLabel,
  componentStateTone,
  componentTypeLabel,
  dateTime,
} from "@/lib/labels";
import { useCurrentUser } from "@/lib/session";

// Список обновляется сам: heartbeat приходит раз в минуту, и вручную
// перезагружать страницу, чтобы увидеть «шлюз не отвечает», неудобно.
const REFRESH_MS = 30_000;

/** Состояния, требующие внимания администратора. */
const PROBLEM_STATES = ["update_required", "incompatible", "offline"];

function contractList(values: number[]): string {
  return values.length ? values.map((v) => `v${v}`).join(", ") : "—";
}

export default function InfrastructurePage() {
  const { user, canWrite } = useCurrentUser();
  const [data, setData] = useState<ComponentsResponse | null>(null);
  const [safety, setSafety] = useState<DeploymentSafetyReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [pending, setPending] = useState<ComponentItem | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [components, report] = await Promise.all([
        componentsApi.list(),
        componentsApi.deploymentSafety(),
      ]);
      setData(components);
      setSafety(report);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
    const timer = window.setInterval(() => {
      load().catch(() => undefined);
    }, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const forget = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      await componentsApi.forget(pending.component_id);
      setPending(null);
      await load();
    } catch (e) {
      setError((e as Error).message);
      setPending(null);
    } finally {
      setBusy(false);
    }
  };

  const items = data?.items ?? [];
  // Проблемные экземпляры выносим отдельно: администратору важно увидеть
  // причину, а не искать её в таблице.
  const problems = items.filter((item) =>
    PROBLEM_STATES.includes(item.compatibility_state),
  );

  const columns: ReadonlyArray<DataColumn<ComponentItem>> = [
    {
      key: "component",
      header: "Компонент",
      render: (item) => (
        <>
          <div>{item.name}</div>
          <div className="muted" style={{ fontSize: 12 }}>
            <code>{item.component_id}</code>
          </div>
        </>
      ),
    },
    {
      key: "type",
      header: "Тип",
      render: (item) => componentTypeLabel(item.component_type),
    },
    { key: "region", header: "Размещение", render: (item) => item.region },
    {
      key: "version",
      header: "Версия",
      render: (item) => (
        <>
          <div>{item.version}</div>
          {item.build_sha && (
            <div className="muted" style={{ fontSize: 12 }}>
              {item.build_sha}
            </div>
          )}
        </>
      ),
    },
    {
      key: "contract",
      header: "Контракт",
      hint: "Совместимость определяется версией контракта взаимодействия, а не совпадением номеров версий.",
      render: (item) => `v${item.contract_version}`,
    },
    {
      key: "state",
      header: "Состояние",
      render: (item) => (
        <>
          <Status tone={componentStateTone(item.compatibility_state)}>
            {componentStateLabel(item.compatibility_state)}
          </Status>
          <div className="muted" style={{ fontSize: 12 }}>
            {item.compatibility_detail}
          </div>
        </>
      ),
    },
    {
      key: "heartbeat",
      header: "Последний отклик",
      hint: "Сервер описывает себя сам, поэтому отклика у него нет.",
      render: (item) =>
        item.self_reported ? "—" : dateTime(item.last_heartbeat_at),
    },
    {
      key: "actions",
      header: "Действия",
      render: (item) => (
        <div className="button-row">
          <button
            type="button"
            className="small"
            onClick={() =>
              setExpanded(
                expanded === item.component_id ? null : item.component_id,
              )
            }
          >
            {expanded === item.component_id ? "Скрыть" : "Подробности"}
          </button>
          {/* Сервер описывает себя сам и в реестре не хранится: удалять
              нечего. */}
          {canWrite && !item.self_reported && (
            <button
              type="button"
              className="small danger"
              onClick={() => setPending(item)}
            >
              Удалить из реестра
            </button>
          )}
        </div>
      ),
    },
  ];

  const shown = expanded
    ? items.find((item) => item.component_id === expanded)
    : undefined;

  return (
    <>
      <PageHeader
        title="Инфраструктура"
        description="Части системы обновляются независимо друг от друга, поэтому их версии могут различаться. Совместимость определяется версией контракта взаимодействия, а не совпадением номеров версий."
        actions={
          <button
            type="button"
            className="small"
            onClick={() => load().catch(() => undefined)}
            disabled={loading}
          >
            Обновить
          </button>
        }
      />

      {error && (
        <ErrorState message={error} onRetry={() => load().catch(() => undefined)} />
      )}

      {user && !user.can_write && (
        <Notice tone="info" title="Доступ только для просмотра">
          Ваша роль — наблюдатель. Состояние компонентов видно, удалять записи
          нельзя.
        </Notice>
      )}

      {problems.map((item) => (
        <Notice
          key={item.component_id}
          tone={item.compatibility_state === "offline" ? "warn" : "bad"}
          title={`${item.name}: ${componentStateLabel(item.compatibility_state)}`}
        >
          {item.compatibility_detail}
        </Notice>
      ))}

      {safety && (
        <Card
          title="Безопасность обновления сервера"
          description="Вердикт сервера: не сломает ли его обновление уже развёрнутые компоненты."
          actions={
            <Status tone={safety.result === "SAFE" ? "ok" : "bad"}>
              {safety.result === "SAFE" ? "обновление безопасно" : "заблокировано"}
            </Status>
          }
        >
          <KeyValue
            rows={[
              {
                key: "Версия сервера",
                value: safety.backend_version,
                hint: `Контракты: ${contractList(safety.backend_contracts)}`,
              },
              {
                key: "Проверено компонентов",
                value: safety.verdicts.length,
              },
              {
                key: "Что блокирует",
                value:
                  safety.blocking.length === 0 ? (
                    <span className="muted">ничего</span>
                  ) : (
                    <div className="stack">
                      {safety.blocking.map((verdict) => (
                        <div key={verdict.component_id}>
                          <code>{verdict.component_id}</code>: {verdict.detail}
                        </div>
                      ))}
                    </div>
                  ),
              },
              { key: "Проверено", value: dateTime(safety.generated_at) },
            ]}
          />
        </Card>
      )}

      <Card
        title="Компоненты"
        description="Сервер описывает себя сам; остальные компоненты сообщают о себе периодически. Список обновляется каждые 30 секунд."
      >
        <DataTable
          columns={columns}
          rows={items}
          rowKey={(item) => item.component_id}
          loading={loading}
          emptyTitle="Компонентов нет"
          emptyHint="Ни один компонент пока не сообщил о себе."
        />
      </Card>

      {shown && (
        <Card
          title={shown.name}
          description={`${componentTypeLabel(shown.component_type)} · ${shown.region}`}
          actions={
            <button
              type="button"
              className="ghost small"
              onClick={() => setExpanded(null)}
            >
              Закрыть
            </button>
          }
        >
          <KeyValue
            rows={[
              { key: "Версия", value: shown.version },
              { key: "Сборка", value: shown.build_sha ?? "—" },
              { key: "Контракт", value: `v${shown.contract_version}` },
              {
                key: "Требуется",
                value: `${shown.required_contract ? `v${shown.required_contract}` : "—"}${
                  shown.supported_contracts.length > 1
                    ? ` (поддерживаются ${contractList(shown.supported_contracts)})`
                    : ""
                }`,
              },
              {
                key: "Минимальная версия",
                value: shown.min_version ?? "—",
                hidden: !shown.min_version,
              },
              {
                key: "Последний отклик",
                value: shown.self_reported
                  ? "—"
                  : dateTime(shown.last_heartbeat_at),
              },
              {
                key: "Зарегистрирован",
                value: shown.registered_at
                  ? dateTime(shown.registered_at)
                  : "—",
              },
              {
                key: "Состояние",
                value: (
                  <Status tone={componentStateTone(shown.compatibility_state)}>
                    {componentStateLabel(shown.compatibility_state)}
                  </Status>
                ),
                hint: shown.compatibility_detail,
              },
              {
                key: "Умеет",
                value: (
                  <div className="inline-list" style={{ gap: 4 }}>
                    {shown.capabilities.map((capability) => (
                      <Tag key={capability}>{capabilityLabel(capability)}</Tag>
                    ))}
                  </div>
                ),
                hidden: shown.capabilities.length === 0,
              },
            ]}
          />
        </Card>
      )}

      {pending && (
        <ConfirmDialog
          title={`Удалить «${pending.component_id}» из реестра?`}
          description="Удаляйте запись только для выведенного из эксплуатации экземпляра: работающий компонент появится снова при следующем отклике, а до этого его состояние будет неизвестно."
          confirmLabel="Удалить запись"
          danger
          busy={busy}
          onConfirm={forget}
          onCancel={() => setPending(null)}
        />
      )}
    </>
  );
}
