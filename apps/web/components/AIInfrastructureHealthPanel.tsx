"use client";

// Состояние подключений к ИИ.
//
// Дерево «сервис → подключение → модели» строится из фактической настройки:
// компонент не хранит собственных списков и ничего не придумывает. Если
// сервисов не создано — показывается пустое состояние, а не выдуманные
// примеры.

import { useCallback, useEffect, useState } from "react";

import {
  Card,
  Empty,
  Skeleton,
  Status,
  Tag,
  moment,
} from "@/components/ui/Primitives";
import {
  AIHealthEndpoint,
  AIHealthModel,
  AIHealthProvider,
  AIInfrastructureHealth,
  aiApi,
} from "@/lib/api";
import {
  aiAvailabilityLabel,
  aiHealthLabel,
  aiTaskLabel,
  healthTone,
} from "@/lib/labels";

// Чтение состояния дешёвое (без обращений к сервисам ИИ), поэтому обновляем
// его периодически: так видны изменения, сделанные из другой сессии.
const AUTO_REFRESH_MS = 60_000;

export default function AIInfrastructureHealthPanel(props: Readonly<{
  // Растёт после каждого изменения настроек — панель не показывает устаревшее.
  reloadKey: number;
  onError: (message: string) => void;
}>) {
  const { reloadKey, onError } = props;
  const [report, setReport] = useState<AIInfrastructureHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [failure, setFailure] = useState("");

  const load = useCallback(async () => {
    try {
      setReport(await aiApi.infrastructureHealth());
      setFailure("");
    } catch (e) {
      setFailure((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load, reloadKey]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      load().catch(() => undefined);
    }, AUTO_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  // Активная проверка: короткий тестовый запрос, не создание программы.
  const runCheck = async () => {
    setChecking(true);
    try {
      setReport(await aiApi.refreshInfrastructureHealth());
      setFailure("");
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setChecking(false);
    }
  };

  const actions = (
    <>
      {report && (
        <span className="field-hint">данные на {moment(report.generated_at)}</span>
      )}
      <button
        type="button"
        className="small"
        onClick={() => load()}
        disabled={loading}
      >
        Обновить
      </button>
      <button
        type="button"
        className="small primary"
        onClick={runCheck}
        disabled={checking}
      >
        {checking ? "Проверяем…" : "Проверить связь"}
      </button>
    </>
  );

  return (
    <Card
      title="Состояние подключений"
      description="Список строится из ваших настроек. «Проверить связь» отправляет короткий тестовый запрос — программу это не создаёт."
      actions={actions}
    >
      {loading && <Skeleton rows={3} />}

      {failure && (
        <div className="error">Не удалось получить состояние: {failure}</div>
      )}

      {report && !loading && (
        <>
          {report.providers.length === 0 ? (
            <Empty
              title="Сервисы ИИ не подключены"
              hint="Пока подключений нет, программы собирает алгоритмический генератор. Добавьте подключение на вкладке «Подключения» — оно сразу появится здесь."
            />
          ) : (
            <>
              <Summary report={report} />
              {report.providers.map((provider) => (
                <ProviderBlock
                  key={provider.id ?? provider.slug}
                  provider={provider}
                />
              ))}
            </>
          )}
        </>
      )}
    </Card>
  );
}

function Summary(props: Readonly<{ report: AIInfrastructureHealth }>) {
  const { summary } = props.report;
  return (
    <div className="kv" style={{ marginBottom: 20 }}>
      <div className="k">Сервисы</div>
      <div>
        работают {summary.providers_healthy} из {summary.providers_total}
      </div>
      <div className="k">Модели</div>
      <div>
        доступны {summary.models_available} из {summary.models_total}
      </div>
      <div className="k">Задействованы задачами</div>
      <div>{summary.models_in_active_use}</div>
    </div>
  );
}

function ProviderBlock(props: Readonly<{ provider: AIHealthProvider }>) {
  const { provider } = props;
  return (
    <div className="subcard">
      <div className="inline-list">
        <strong>{provider.name}</strong>
        <Status tone={healthTone(provider.health)}>
          {aiHealthLabel(provider.health)}
        </Status>
        {!provider.enabled && <Tag>выключен вручную</Tag>}
      </div>
      {provider.reason && (
        <p className="field-hint" style={{ marginTop: 6 }}>
          {provider.reason}
        </p>
      )}

      {provider.endpoints.length === 0 ? (
        <p className="field-hint" style={{ marginTop: 10 }}>
          У сервиса нет подключений — обращаться некуда.
        </p>
      ) : (
        provider.endpoints.map((endpoint) => (
          <EndpointBlock key={endpoint.id ?? endpoint.name} endpoint={endpoint} />
        ))
      )}
    </div>
  );
}

function EndpointBlock(props: Readonly<{ endpoint: AIHealthEndpoint }>) {
  const { endpoint } = props;
  return (
    <div style={{ marginTop: 16 }}>
      <div className="inline-list">
        <strong>{endpoint.name}</strong>
        <Status tone={healthTone(endpoint.health)}>
          {aiHealthLabel(endpoint.health)}
        </Status>
        {!endpoint.enabled && <Tag>выключено вручную</Tag>}
        <Tag tone={endpoint.has_api_key ? "neutral" : "warn"}>
          {endpoint.has_api_key ? "ключ сохранён" : "ключ не задан"}
        </Tag>
      </div>

      <p className="field-hint" style={{ margin: "6px 0" }}>
        Проверяли: {moment(endpoint.last_checked_at)}
        {endpoint.last_check_error_type &&
          ` · ошибка проверки: ${endpoint.last_check_error_type}`}
        {endpoint.last_call_at &&
          ` · последнее обращение: ${moment(endpoint.last_call_at)}`}
        {endpoint.last_call_error_type &&
          ` · ошибка обращения: ${endpoint.last_call_error_type}`}
      </p>
      {endpoint.reason && <p className="field-hint">{endpoint.reason}</p>}

      {endpoint.models.length === 0 ? (
        <p className="field-hint">Моделей не добавлено.</p>
      ) : (
        <div className="table-wrap" style={{ marginTop: 10 }}>
          <table>
            <thead>
              <tr>
                <th>Модель</th>
                <th>Доступность</th>
                <th>Используется</th>
                <th>Примечание</th>
              </tr>
            </thead>
            <tbody>
              {endpoint.models.map((model) => (
                <ModelRow key={model.id ?? model.model_id} model={model} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ModelRow(props: Readonly<{ model: AIHealthModel }>) {
  const { model } = props;
  return (
    <tr>
      <td>
        <strong>{model.display_name}</strong>
        <div className="field-hint">
          <code>{model.model_id}</code>
          {!model.enabled && " · выключена вручную"}
        </div>
      </td>
      <td>
        <Status tone={healthTone(model.availability)}>
          {aiAvailabilityLabel(model.availability)}
        </Status>
      </td>
      <td className="text-secondary">
        {model.tasks.length === 0
          ? "не используется"
          : model.tasks
              .map(
                (t) =>
                  `${aiTaskLabel(t.task_type)}` +
                  `${t.is_primary ? " (основная)" : " (резервная)"}` +
                  `${t.task_enabled ? "" : " — задача выключена"}`
              )
              .join("; ")}
      </td>
      <td className="text-secondary">{model.reason ?? "—"}</td>
    </tr>
  );
}
