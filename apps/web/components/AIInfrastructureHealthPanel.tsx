"use client";

// AI Infrastructure Health Dashboard.
//
// Компонент НЕ хранит собственный список провайдеров и моделей и не выводит
// состояние сам: дерево и все статусы приходят готовыми из
// GET /admin/ai/infrastructure-health. Поэтому новый провайдер или модель
// появляются здесь без изменений в этом файле.

import { useCallback, useEffect, useState } from "react";

import {
  AIHealthEndpoint,
  AIHealthModel,
  AIHealthProvider,
  AIInfrastructureHealth,
  aiApi,
} from "@/lib/api";
import {
  aiAvailabilityLabel,
  aiHealthBadgeClass,
  aiHealthLabel,
  aiProtocolLabel,
  aiTaskLabel,
} from "@/lib/labels";

// Дешёвый GET без обращений к провайдерам: подхватывает изменения, сделанные
// из другой сессии, и результат последних реальных AI-вызовов.
const AUTO_REFRESH_MS = 60_000;

function formatMoment(value: string | null): string {
  return value ? new Date(value).toLocaleString("ru-RU") : "—";
}

export default function AIInfrastructureHealthPanel(props: Readonly<{
  // Меняется после каждой CRUD-операции: дашборд синхронизируется с backend.
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

  // Активная проверка: минимальный ping включённых эндпоинтов, не генерация.
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

  return (
    <div className="card">
      <div className="toolbar" style={{ alignItems: "center" }}>
        <h2 className="section-title" style={{ marginTop: 0, marginBottom: 0 }}>
          Состояние AI-инфраструктуры
        </h2>
        <button type="button" onClick={() => load()} disabled={loading}>
          Обновить данные
        </button>
        <button type="button" className="primary" onClick={runCheck} disabled={checking}>
          {checking ? "Проверка подключений..." : "Проверить подключения"}
        </button>
        {report && (
          <span className="muted">
            данные на {formatMoment(report.generated_at)}
          </span>
        )}
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        Список строится из фактической конфигурации. «Проверить подключения»
        выполняет короткий тест включённых эндпоинтов — не генерацию программы.
      </p>

      {loading && <p className="muted">Загрузка состояния...</p>}
      {failure && <div className="error">Не удалось получить состояние: {failure}</div>}

      {report && !loading && (
        <>
          <HealthSummary report={report} />
          {report.providers.length === 0 ? (
            <p className="muted">
              Провайдеров нет. Создайте провайдера — он появится здесь автоматически.
            </p>
          ) : (
            report.providers.map((provider) => (
              <ProviderCard key={provider.id ?? provider.slug} provider={provider} />
            ))
          )}
        </>
      )}
    </div>
  );
}

function HealthSummary(props: Readonly<{ report: AIInfrastructureHealth }>) {
  const { summary } = props.report;
  return (
    <div className="toolbar" style={{ alignItems: "center" }}>
      <span className="muted">
        провайдеров: {summary.providers_healthy} из {summary.providers_total} работают
      </span>
      <span className="muted">эндпоинтов: {summary.endpoints_total}</span>
      <span className="muted">
        моделей доступно: {summary.models_available} из {summary.models_total}
      </span>
      <span className="muted">
        задействовано задачами: {summary.models_in_active_use}
      </span>
    </div>
  );
}

function ProviderCard(props: Readonly<{ provider: AIHealthProvider }>) {
  const { provider } = props;
  return (
    <div className="card" style={{ padding: 14 }}>
      <div className="toolbar" style={{ alignItems: "center" }}>
        <strong>{provider.name}</strong>
        <span className={aiHealthBadgeClass(provider.health)}>
          {aiHealthLabel(provider.health)}
        </span>
        <span className="badge">{aiProtocolLabel(provider.protocol)}</span>
        {/* Конфигурационное состояние отдельно от инфраструктурного. */}
        <span className={provider.enabled ? "badge confirmed" : "badge draft"}>
          {provider.enabled ? "включён" : "отключён"}
        </span>
        <span className="muted">slug: {provider.slug}</span>
      </div>
      {provider.reason && <p className="muted">{provider.reason}</p>}

      {provider.endpoints.length === 0 ? (
        <p className="muted">Эндпоинтов нет.</p>
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
    <div style={{ marginLeft: 16, marginTop: 12 }}>
      <div className="toolbar" style={{ alignItems: "center" }}>
        <strong>{endpoint.name}</strong>
        <span className={aiHealthBadgeClass(endpoint.health)}>
          {aiHealthLabel(endpoint.health)}
        </span>
        <span className="muted">{endpoint.base_url}</span>
        <span className={endpoint.enabled ? "badge confirmed" : "badge draft"}>
          {endpoint.enabled ? "включён" : "отключён"}
        </span>
        <span className={endpoint.has_api_key ? "badge confirmed" : "badge draft"}>
          {endpoint.has_api_key ? "ключ задан" : "ключ не задан"}
        </span>
      </div>
      <p className="muted" style={{ marginTop: 4, marginBottom: 4 }}>
        проверено: {formatMoment(endpoint.last_checked_at)}
        {endpoint.last_check_error_type
          ? ` · ошибка проверки: ${endpoint.last_check_error_type}`
          : ""}
        {endpoint.last_call_at
          ? ` · последний вызов: ${formatMoment(endpoint.last_call_at)}`
          : ""}
        {endpoint.last_call_error_type
          ? ` · ошибка вызова: ${endpoint.last_call_error_type}`
          : ""}
      </p>
      {endpoint.reason && <p className="muted">{endpoint.reason}</p>}

      {endpoint.models.length === 0 ? (
        <p className="muted">Моделей нет.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Модель</th>
              <th>Идентификатор</th>
              <th>Конфигурация</th>
              <th>Доступность</th>
              <th>Используется задачами</th>
              <th>Причина</th>
            </tr>
          </thead>
          <tbody>
            {endpoint.models.map((model) => (
              <ModelRow key={model.id ?? model.model_id} model={model} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ModelRow(props: Readonly<{ model: AIHealthModel }>) {
  const { model } = props;
  return (
    <tr>
      <td>{model.display_name}</td>
      <td className="muted">{model.model_id}</td>
      <td>
        <span className={model.enabled ? "badge confirmed" : "badge draft"}>
          {model.enabled ? "включена" : "отключена"}
        </span>
      </td>
      <td>
        <span className={aiHealthBadgeClass(model.availability)}>
          {aiAvailabilityLabel(model.availability)}
        </span>
      </td>
      <td className="muted">
        {model.tasks.length === 0
          ? "—"
          : model.tasks
              .map(
                (t) =>
                  `${aiTaskLabel(t.task_type)}` +
                  `${t.is_primary ? " (основная)" : " (резервная)"}` +
                  `${t.task_enabled ? "" : " — задача выключена"}`
              )
              .join("; ")}
      </td>
      <td className="muted">{model.reason ?? "—"}</td>
    </tr>
  );
}
