"use client";

// Почему программа собрана без ИИ.
//
// Отвечает на эксплуатационный вопрос: «ИИ включён, а программа
// алгоритмическая — почему?». Различает два случая: до ИИ дело не дошло
// (настройка) и обращение было, но не удалось.

import { useCallback, useEffect, useState } from "react";

import { Card, Empty, Skeleton, Status, Tag, moment } from "@/components/ui/Primitives";
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
      setItems((await aiApi.fallbackEvents()).items);
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
      title="Программы, собранные без ИИ"
      description="«Обращались к ИИ: нет» — настройка была нерабочей, запрос не отправляли. «Да» — запрос ушёл, но ответа не получили."
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
          title="Таких случаев не было"
          hint="Все программы собирались тем генератором, который запрашивала система."
        />
      )}

      {items.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Когда</th>
                <th>Запрашивали</th>
                <th>Собрал</th>
                <th>Причина</th>
                <th>Обращались к ИИ</th>
                <th>Подробности</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td className="text-secondary">{moment(item.created_at)}</td>
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
                    <Status tone="warn">
                      {item.metadata.reason_code
                        ? aiFallbackReasonLabel(item.metadata.reason_code)
                        : "причина неизвестна"}
                    </Status>
                  </td>
                  <td>
                    <Tag tone={item.metadata.ai_attempted ? "info" : "neutral"}>
                      {item.metadata.ai_attempted ? "да" : "нет"}
                    </Tag>
                  </td>
                  <td className="text-secondary">{item.metadata.detail ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
