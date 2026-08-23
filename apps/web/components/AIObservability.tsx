"use client";

// Журналы: обращения к ИИ и изменения настроек.
//
// Данные писались и раньше, но были видны только через базу. Здесь они
// показаны администратору: расход, скорость ответа, ошибки и кто что менял.

import {
  Card,
  Empty,
  Status,
  moment,
} from "@/components/ui/Primitives";
import {
  AIAuditItem,
  AIModelItem,
  AIProviderItem,
  AIUsageItem,
} from "@/lib/api";
import { aiAuditEventLabel, aiTaskLabel, aiUsageStatusLabel } from "@/lib/labels";

export default function AIObservability(props: Readonly<{
  usage: AIUsageItem[];
  audit: AIAuditItem[];
  models: AIModelItem[];
  providers: AIProviderItem[];
  refreshing: boolean;
  onRefresh: () => void;
}>) {
  // Идентификаторы наружу не показываем: только понятные имена.
  const modelName = (pk: number | null) => {
    if (pk === null) return "—";
    const model = props.models.find((m) => m.id === pk);
    return model ? model.display_name : "удалённая модель";
  };
  const providerName = (pk: number | null) => {
    if (pk === null) return "—";
    const provider = props.providers.find((p) => p.id === pk);
    return provider ? provider.name : "удалённый сервис";
  };

  const refreshButton = (
    <button
      type="button"
      className="small"
      onClick={props.onRefresh}
      disabled={props.refreshing}
    >
      {props.refreshing ? "Обновляем…" : "Обновить"}
    </button>
  );

  return (
    <>
      <Card
        title="Обращения к ИИ"
        description="Последние запросы: какая модель отвечала, сколько времени занял ответ и каков расход. Тексты запросов и ответов не сохраняются."
        actions={refreshButton}
      >
        {props.usage.length === 0 ? (
          <Empty
            title="Обращений не было"
            hint="Здесь появятся записи после первого запроса к ИИ. Проверка связи в этот журнал не попадает — это не выполнение задачи."
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Когда</th>
                  <th>Задача</th>
                  <th>Сервис</th>
                  <th>Модель</th>
                  <th>Расход</th>
                  <th>Ответ за</th>
                  <th>Итог</th>
                </tr>
              </thead>
              <tbody>
                {props.usage.map((item) => (
                  <tr key={item.id}>
                    <td className="text-secondary">{moment(item.created_at)}</td>
                    <td>{aiTaskLabel(item.task_type)}</td>
                    <td>{providerName(item.provider_id)}</td>
                    <td>{modelName(item.model_id)}</td>
                    <td className="text-secondary">
                      {item.total_tokens !== null
                        ? `${item.total_tokens} токенов`
                        : "—"}
                    </td>
                    <td className="text-secondary">
                      {item.latency_ms !== null ? `${item.latency_ms} мс` : "—"}
                    </td>
                    <td>
                      <Status tone={item.status === "success" ? "ok" : "bad"}>
                        {aiUsageStatusLabel(item.status)}
                        {item.error_type ? `: ${item.error_type}` : ""}
                      </Status>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card
        title="Изменения настроек"
        description="Кто и что менял в настройках ИИ и в списке пользователей. Пароли и ключи доступа здесь не сохраняются."
        actions={refreshButton}
      >
        {props.audit.length === 0 ? (
          <Empty
            title="Изменений не было"
            hint="Каждое действие с настройками попадает сюда автоматически."
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Когда</th>
                  <th>Что произошло</th>
                  <th>Кто</th>
                </tr>
              </thead>
              <tbody>
                {props.audit.map((item) => (
                  <tr key={item.id}>
                    <td className="text-secondary">{moment(item.created_at)}</td>
                    <td>{aiAuditEventLabel(item.event_type)}</td>
                    <td className="text-secondary">{item.actor ?? "система"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
