"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import { Card, Empty, Skeleton, Status, Tag, moment } from "@/components/ui/Primitives";
import { api, getToken, ListResponse, ProgramListItem } from "@/lib/api";
import { generationSourceLabel, statusLabel, statusTone } from "@/lib/labels";

export default function ProgramsPage() {
  const [data, setData] = useState<ListResponse<ProgramListItem> | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    api
      .programs({ limit: 100 })
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

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
                    <th>Тренировок в неделю</th>
                    <th>Длительность</th>
                    <th>Создана</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((p) => (
                    <tr key={`${p.program_id}-v${p.version}`}>
                      <td>
                        <Link href={`/programs/${p.program_id}`}>{p.title}</Link>
                      </td>
                      <td>
                        <Link href={`/profiles/${p.profile_id}`}>
                          открыть анкету
                        </Link>
                      </td>
                      <td>№{p.version}</td>
                      <td>
                        <Status tone={statusTone(p.status)}>
                          {statusLabel(p.status)}
                        </Status>
                      </td>
                      <td>
                        <Tag tone={p.generation_source === "ai" ? "info" : "neutral"}>
                          {generationSourceLabel(p.generation_source)}
                        </Tag>
                      </td>
                      <td>{p.training_days_per_week}</td>
                      <td>{p.duration_weeks} нед.</td>
                      <td className="muted">{moment(p.created_at)}</td>
                    </tr>
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
