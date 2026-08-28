"use client";

// Что происходило с моделями внутри одной генерации.
//
// Журнал вызовов на этот вопрос не отвечает: в нём каждый вызов отдельной
// строкой со статусом транспорта, и `200 OK` с выдуманным упражнением выглядит
// в нём как успех. Здесь видно другое: прошёл ли первый ответ проверку,
// запрашивалось ли исправление, почему модель была оставлена и дошла ли очередь
// до резервной.

import { useCallback, useEffect, useState } from "react";

import { Card, Empty, Skeleton, Status, Tag, moment } from "@/components/ui/Primitives";
import { AIModelAttemptsItem, aiApi } from "@/lib/api";
import { aiAttemptOutcomeLabel, aiAttemptOutcomeTone } from "@/lib/labels";

export default function AIModelAttempts(props: Readonly<{
  reloadKey: number;
  onError: (message: string) => void;
}>) {
  const { reloadKey } = props;
  const [items, setItems] = useState<AIModelAttemptsItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState("");

  const load = useCallback(async () => {
    try {
      setItems((await aiApi.modelAttempts()).items);
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
    <Card
      title="Попытки моделей при сборке программы"
      description="Одна запись — одна генерация. Показывает, почему система переходила от основной модели к резервным и помогло ли исправление ответа."
      actions={
        <button
          type="button"
          className="small"
          onClick={() => load()}
          disabled={loading}
        >
          Обновить
        </button>
      }
    >
      {loading && <Skeleton rows={2} />}
      {failure && <div className="error">Не удалось загрузить журнал: {failure}</div>}

      {!loading && !failure && items.length === 0 && (
        <Empty
          title="Записей нет"
          hint="Здесь появятся подробности после первой сборки программы через ИИ."
        />
      )}

      {items.length > 0 && (
        <div className="stack">
          {items.map((item) => (
            <div className="subcard" key={item.id}>
              <div className="inline-list" style={{ marginBottom: 8 }}>
                <strong>{moment(item.created_at)}</strong>
                <Tag>моделей опробовано: {item.metadata.models_tried ?? 0}</Tag>
              </div>

              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Очередь</th>
                      <th>Модель</th>
                      <th>Первый ответ</th>
                      <th>Исправлений</th>
                      <th>Итог</th>
                      <th>Подробности</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(item.metadata.attempts ?? []).map((attempt) => (
                      <tr key={`${item.id}-${attempt.priority}-${attempt.model_id}`}>
                        <td className="text-secondary">
                          {attempt.is_primary ? "основная" : `резерв №${attempt.priority}`}
                        </td>
                        <td>
                          <code>{attempt.model_id}</code>
                        </td>
                        <td>
                          <Tag tone={attempt.initial_valid ? "ok" : "warn"}>
                            {attempt.initial_valid ? "принят" : "не принят"}
                          </Tag>
                        </td>
                        <td className="text-secondary">{attempt.repair_attempts}</td>
                        <td>
                          <Status tone={aiAttemptOutcomeTone(attempt.outcome)}>
                            {aiAttemptOutcomeLabel(attempt.outcome)}
                          </Status>
                        </td>
                        <td className="text-secondary">{attempt.detail ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
