"use client";

// Журнал fallback: отвечает на эксплуатационный вопрос «почему программа
// сгенерирована детерминированным генератором, хотя AI включён?».
//
// Данные приходят из существующего журнала событий AI-контура
// (GET /admin/ai/fallback-events) — отдельной подсистемы под это нет.

import { useCallback, useEffect, useState } from "react";

import { AIFallbackEventItem, aiApi } from "@/lib/api";
import { aiFallbackReasonLabel, generatorLabel } from "@/lib/labels";

export default function AIFallbackEvents(props: Readonly<{
  reloadKey: number;
  onError: (message: string) => void;
}>) {
  const { reloadKey } = props;
  const [items, setItems] = useState<AIFallbackEventItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState("");

  const load = useCallback(async () => {
    try {
      const data = await aiApi.fallbackEvents();
      setItems(data.items);
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

  return (
    <div className="card">
      <div className="toolbar" style={{ alignItems: "center" }}>
        <h2 className="section-title" style={{ marginTop: 0, marginBottom: 0 }}>
          Почему AI не сработал (fallback)
        </h2>
        <button type="button" onClick={() => load()} disabled={loading}>
          Обновить
        </button>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        «AI не вызывался» — конфигурация была заведомо нерабочей, запрос не
        отправлялся. «AI вызывался» — попытка была и завершилась ошибкой.
      </p>

      {loading && <p className="muted">Загрузка журнала...</p>}
      {failure && <div className="error">Не удалось загрузить журнал: {failure}</div>}

      {!loading && !failure && items.length === 0 && (
        <p className="muted">
          Fallback не происходил: программы генерировались запрошенным генератором.
        </p>
      )}

      {items.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Время</th>
              <th>Запрошен</th>
              <th>Фактически</th>
              <th>Причина</th>
              <th>AI вызывался</th>
              <th>Детали</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td className="muted">
                  {item.created_at
                    ? new Date(item.created_at).toLocaleString("ru-RU")
                    : "—"}
                </td>
                <td>
                  {item.metadata.requested_generator
                    ? generatorLabel(item.metadata.requested_generator)
                    : "—"}
                </td>
                <td>
                  {item.metadata.actual_generator
                    ? generatorLabel(item.metadata.actual_generator)
                    : "—"}
                </td>
                <td>
                  <span className="badge draft">
                    {item.metadata.reason_code
                      ? aiFallbackReasonLabel(item.metadata.reason_code)
                      : "неизвестно"}
                  </span>
                </td>
                <td className="muted">
                  {item.metadata.ai_attempted ? "да" : "нет"}
                </td>
                <td className="muted">{item.metadata.detail ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
