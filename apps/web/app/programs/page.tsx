"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import { api, getToken, ListResponse, ProgramListItem } from "@/lib/api";
import { generationSourceLabel, statusLabel } from "@/lib/labels";

export default function ProgramsPage() {
  const [data, setData] = useState<ListResponse<ProgramListItem> | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    api
      .programs({ limit: 100 })
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <h1 className="page-title">Программы тренировок</h1>
        {error && <div className="error">{error}</div>}
        {!data && !error && <div className="loading">Загрузка...</div>}
        {data && (
          <>
            <p className="muted">Всего: {data.total}</p>
            {data.items.length === 0 ? (
              <p className="muted">
                Программ пока нет. Откройте профиль и нажмите «Сгенерировать программу».
              </p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Название</th>
                    <th>Профиль</th>
                    <th>Версия</th>
                    <th>Статус</th>
                    <th>Источник</th>
                    <th>Тренировок/нед</th>
                    <th>Недель</th>
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
                        <Link href={`/profiles/${p.profile_id}`}>{p.profile_id}</Link>
                      </td>
                      <td>v{p.version}</td>
                      <td>{statusLabel(p.status)}</td>
                      <td>{generationSourceLabel(p.generation_source)}</td>
                      <td>{p.training_days_per_week}</td>
                      <td>{p.duration_weeks}</td>
                      <td className="muted">
                        {p.created_at ? new Date(p.created_at).toLocaleString() : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </main>
    </div>
  );
}
