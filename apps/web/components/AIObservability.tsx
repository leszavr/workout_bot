"use client";

// Observability AI: журнал вызовов (токены, латентность, ошибки) и журнал
// изменений конфигурации. Данные уже писались backend'ом, но были доступны
// только через psql — теперь видны администратору.

import {
  AIAuditItem,
  AIModelItem,
  AIProviderItem,
  AIUsageItem,
} from "@/lib/api";
import {
  aiAuditEventLabel,
  aiTaskLabel,
  aiUsageStatusLabel,
} from "@/lib/labels";

function formatMoment(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("ru-RU");
}

export default function AIObservability(props: Readonly<{
  usage: AIUsageItem[];
  audit: AIAuditItem[];
  models: AIModelItem[];
  providers: AIProviderItem[];
  refreshing: boolean;
  onRefresh: () => void;
}>) {
  const modelName = (pk: number | null) => {
    if (pk === null) return "—";
    const model = props.models.find((m) => m.id === pk);
    return model ? `${model.display_name} (${model.model_id})` : `#${pk}`;
  };
  const providerName = (pk: number | null) => {
    if (pk === null) return "—";
    const provider = props.providers.find((p) => p.id === pk);
    return provider ? provider.name : `#${pk}`;
  };

  return (
    <>
      <div className="card">
        <div className="toolbar" style={{ alignItems: "center", marginBottom: 8 }}>
          <h2 className="section-title" style={{ margin: 0 }}>
            Вызовы AI (последние 50)
          </h2>
          <button type="button" onClick={props.onRefresh} disabled={props.refreshing}>
            {props.refreshing ? "Обновление..." : "Обновить"}
          </button>
        </div>
        {props.usage.length === 0 ? (
          <p className="muted">
            AI ещё не вызывался: записей об использовании нет.
          </p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Время</th>
                <th>Задача</th>
                <th>Провайдер</th>
                <th>Модель</th>
                <th>Статус</th>
                <th>Токены (вход/выход)</th>
                <th>Задержка</th>
                <th>Ошибка</th>
              </tr>
            </thead>
            <tbody>
              {props.usage.map((item) => (
                <tr key={item.id}>
                  <td>{formatMoment(item.created_at)}</td>
                  <td>{aiTaskLabel(item.task_type)}</td>
                  <td className="muted">{providerName(item.provider_id)}</td>
                  <td className="muted">{modelName(item.model_id)}</td>
                  <td>
                    <span
                      className={
                        item.status === "success" ? "badge confirmed" : "badge draft"
                      }
                    >
                      {aiUsageStatusLabel(item.status)}
                    </span>
                  </td>
                  <td>
                    {item.input_tokens ?? "—"} / {item.output_tokens ?? "—"}
                  </td>
                  <td>{item.latency_ms !== null ? `${item.latency_ms} мс` : "—"}</td>
                  <td className="muted">{item.error_type ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h2 className="section-title" style={{ marginTop: 0 }}>
          Изменения конфигурации (последние 50)
        </h2>
        {props.audit.length === 0 ? (
          <p className="muted">Событий пока нет.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Время</th>
                <th>Событие</th>
                <th>Кто</th>
                <th>Объект</th>
              </tr>
            </thead>
            <tbody>
              {props.audit.map((item) => (
                <tr key={item.id}>
                  <td>{formatMoment(item.created_at)}</td>
                  <td>{aiAuditEventLabel(item.event_type)}</td>
                  <td className="muted">{item.actor ?? "—"}</td>
                  <td className="muted">
                    {item.entity_type ?? "—"}
                    {item.entity_id ? ` #${item.entity_id}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
