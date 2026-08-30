"use client";

// /infrastructure — состояние развёрнутых компонентов системы.
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

import { useCallback, useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import {
  Card,
  Empty,
  Notice,
  Skeleton,
  Status,
  Tag,
} from "@/components/ui/Primitives";
import {
  ApiError,
  ComponentItem,
  ComponentsResponse,
  DeploymentSafetyReport,
  componentsApi,
  getToken,
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

function contractList(values: number[]): string {
  return values.length ? values.map((v) => `v${v}`).join(", ") : "—";
}

function ComponentRow(props: Readonly<{ item: ComponentItem }>) {
  const { item } = props;
  return (
    <tr>
      <td>
        <div>{item.name}</div>
        <div className="muted">{item.component_id}</div>
      </td>
      <td>{componentTypeLabel(item.component_type)}</td>
      <td>{item.region}</td>
      <td>
        <div>{item.version}</div>
        {item.build_sha && <div className="muted">{item.build_sha}</div>}
      </td>
      <td>v{item.contract_version}</td>
      <td>
        <Status tone={componentStateTone(item.compatibility_state)}>
          {componentStateLabel(item.compatibility_state)}
        </Status>
        <div className="muted">{item.compatibility_detail}</div>
      </td>
      <td>{item.self_reported ? "—" : dateTime(item.last_heartbeat_at)}</td>
    </tr>
  );
}

function ComponentDetails(props: Readonly<{ item: ComponentItem }>) {
  const { item } = props;
  return (
    <Card
      title={item.name}
      description={`${componentTypeLabel(item.component_type)} · ${item.region}`}
    >
      <div className="kv">
        <div className="k">Версия</div>
        <div>{item.version}</div>
        <div className="k">Сборка</div>
        <div>{item.build_sha ?? "—"}</div>
        <div className="k">Контракт</div>
        <div>v{item.contract_version}</div>
        <div className="k">Требуется</div>
        <div>
          {item.required_contract ? `v${item.required_contract}` : "—"}
          {item.supported_contracts.length > 1 &&
            ` (поддерживаются ${contractList(item.supported_contracts)})`}
        </div>
        <div className="k">Последний отклик</div>
        <div>{item.self_reported ? "—" : dateTime(item.last_heartbeat_at)}</div>
        <div className="k">Состояние</div>
        <div>
          <Status tone={componentStateTone(item.compatibility_state)}>
            {componentStateLabel(item.compatibility_state)}
          </Status>
        </div>
      </div>
      {item.capabilities.length > 0 && (
        <div className="inline-list" style={{ marginTop: 12 }}>
          {item.capabilities.map((capability) => (
            <Tag key={capability}>{capabilityLabel(capability)}</Tag>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function InfrastructurePage() {
  const { user, canWrite } = useCurrentUser();
  const [data, setData] = useState<ComponentsResponse | null>(null);
  const [safety, setSafety] = useState<DeploymentSafetyReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

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
      setError((e as ApiError).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    load().catch(() => undefined);
    const timer = window.setInterval(() => {
      load().catch(() => undefined);
    }, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const forget = async (componentId: string) => {
    try {
      await componentsApi.forget(componentId);
      await load();
    } catch (e) {
      setError((e as ApiError).message);
    }
  };

  const items = data?.items ?? [];
  // Проблемные экземпляры выносим отдельно: администратору важно увидеть
  // причину, а не искать её в таблице.
  const problems = items.filter((item) =>
    ["update_required", "incompatible", "offline"].includes(
      item.compatibility_state
    )
  );
  const registered = items.filter((item) => !item.self_reported);

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <div className="page-head">
          <h1 className="page-title">Инфраструктура</h1>
          <p className="page-subtitle">
            Части системы обновляются независимо друг от друга, поэтому их
            версии могут различаться. Совместимость определяется версией
            контракта взаимодействия, а не совпадением номеров версий.
          </p>
        </div>

        {error && <div className="error">{error}</div>}

        {user && !user.can_write && (
          <Notice tone="info" title="Доступ только для просмотра">
            Ваша роль — наблюдатель. Состояние компонентов видно, удалять
            записи нельзя.
          </Notice>
        )}

        {problems.map((item) => (
          <Notice
            key={item.component_id}
            tone={item.compatibility_state === "offline" ? "warn" : "bad"}
            title={`${item.name}: ${componentStateLabel(
              item.compatibility_state
            )}`}
          >
            {item.compatibility_detail}
          </Notice>
        ))}

        {safety && (
          <Notice
            tone={safety.result === "SAFE" ? "ok" : "bad"}
            title={
              safety.result === "SAFE"
                ? "Обновление сервера безопасно"
                : "Обновление сервера заблокировано"
            }
          >
            {safety.result === "SAFE"
              ? `Все зарегистрированные компоненты совместимы с сервером ${safety.backend_version} (контракты ${contractList(
                  safety.backend_contracts
                )}).`
              : safety.blocking
                  .map((v) => `${v.component_id}: ${v.detail}`)
                  .join("; ")}
          </Notice>
        )}

        <Card
          title="Компоненты"
          description="Сервер описывает себя сам; остальные компоненты сообщают о себе периодически."
        >
          {loading && !data ? (
            <Skeleton rows={4} />
          ) : items.length === 0 ? (
            <Empty
              title="Компонентов нет"
              hint="Ни один компонент пока не сообщил о себе."
            />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Компонент</th>
                    <th>Тип</th>
                    <th>Размещение</th>
                    <th>Версия</th>
                    <th>Контракт</th>
                    <th>Состояние</th>
                    <th>Последний отклик</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <ComponentRow key={item.component_id} item={item} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {registered.map((item) => (
          <ComponentDetails key={item.component_id} item={item} />
        ))}

        {canWrite && registered.length > 0 && (
          <Card
            title="Очистка реестра"
            description="Удаляйте запись только для выведенного из эксплуатации экземпляра: работающий компонент появится снова при следующем отклике."
          >
            <div className="inline-list">
              {registered.map((item) => (
                <button
                  key={item.component_id}
                  type="button"
                  className="btn small"
                  onClick={() => forget(item.component_id)}
                >
                  Удалить {item.component_id}
                </button>
              ))}
            </div>
          </Card>
        )}
      </main>
    </div>
  );
}
