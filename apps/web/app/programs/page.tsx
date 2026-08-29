"use client";

// Программы тренировок.
//
// Программа производна от анкеты: её всегда можно собрать заново, поэтому
// удаление здесь не блокируется ничем. Обратный порядок (удалить анкету, оставив
// программы) запрещён — см. раздел анкет.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import {
  Card,
  Empty,
  Notice,
  Skeleton,
  Status,
  Tag,
  moment,
} from "@/components/ui/Primitives";
import { ListResponse, ProgramListItem, api, getToken } from "@/lib/api";
import { generationSourceLabel, statusLabel, statusTone } from "@/lib/labels";
import { useCurrentUser } from "@/lib/session";

export default function ProgramsPage() {
  const { canWrite } = useCurrentUser();
  const [data, setData] = useState<ListResponse<ProgramListItem> | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    api
      .programs({ limit: 100 })
      .then((res) => {
        setData(res);
        setError("");
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    load();
  }, [load]);

  const onDeleted = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 6000);
    load();
  };

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <div className="page-head">
          <h1 className="page-title">Программы тренировок</h1>
          <p className="page-subtitle">
            Готовые планы, собранные по анкетам. Столбец «Собрана» показывает, кто
            составил план: ИИ или алгоритм подбора упражнений.
          </p>
        </div>

        {error && <div className="error">{error}</div>}
        {notice && <Notice tone="ok">{notice}</Notice>}

        <Card
          title="Список программ"
          description={data ? `Всего: ${data.total}` : undefined}
        >
          {loading && <Skeleton rows={4} />}

          {!loading && data && data.items.length === 0 && (
            <Empty
              title="Программ пока нет"
              hint="Откройте анкету и нажмите «Собрать программу» — план появится здесь."
              action={
                <Link className="btn" href="/profiles">
                  Перейти к анкетам
                </Link>
              }
            />
          )}

          {!loading && data && data.items.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Название</th>
                    <th>Анкета</th>
                    <th>Версия</th>
                    <th>Состояние</th>
                    <th>Собрана</th>
                    <th>Отправлена</th>
                    <th>Тренировок в неделю</th>
                    <th>Длительность</th>
                    <th>Создана</th>
                    {canWrite && <th>Действия</th>}
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((p) => (
                    <ProgramRow
                      key={`${p.program_id}-v${p.version}`}
                      item={p}
                      canWrite={canWrite}
                      onDeleted={onDeleted}
                      onError={setError}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </main>
    </div>
  );
}

function ProgramRow(props: Readonly<{
  item: ProgramListItem;
  canWrite: boolean;
  onDeleted: (message: string) => void;
  onError: (message: string) => void;
}>) {
  const { item, canWrite } = props;
  const [deleting, setDeleting] = useState(false);

  const remove = async () => {
    if (
      !window.confirm(
        `Удалить программу «${item.title}»? Будут удалены все её версии и записи ` +
          "об отправке. Анкета останется, программу можно собрать заново."
      )
    ) {
      return;
    }
    setDeleting(true);
    try {
      await api.deleteProgram(item.program_id);
      props.onDeleted(`Программа «${item.title}» удалена`);
    } catch (e) {
      props.onError((e as Error).message);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <tr>
      <td>
        <Link href={`/programs/${item.program_id}`}>{item.title}</Link>
      </td>
      <td>
        <Link href={`/profiles/${item.profile_id}`}>открыть анкету</Link>
      </td>
      <td>№{item.version}</td>
      <td>
        <Status tone={statusTone(item.status)}>{statusLabel(item.status)}</Status>
      </td>
      <td>
        <Tag tone={item.generation_source === "ai" ? "info" : "neutral"}>
          {generationSourceLabel(item.generation_source)}
        </Tag>
      </td>
      <td>
        {item.delivered ? (
          <Tag tone="ok">отправлена</Tag>
        ) : (
          <span className="muted">нет</span>
        )}
      </td>
      <td>{item.training_days_per_week}</td>
      <td>{item.duration_weeks} нед.</td>
      <td className="muted">{moment(item.created_at)}</td>
      {canWrite && (
        <td className="actions">
          <button
            type="button"
            className="small danger"
            onClick={remove}
            disabled={deleting}
          >
            {deleting ? "Удаляем…" : "Удалить"}
          </button>
        </td>
      )}
    </tr>
  );
}
