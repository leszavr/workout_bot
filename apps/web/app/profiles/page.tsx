"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import { api, getToken, ListResponse, ProfileListItem } from "@/lib/api";

const GOAL_LABELS: Record<string, string> = {
  weight_loss: "Снижение веса",
  muscle_gain: "Набор массы",
  strength: "Сила",
  health_fitness: "Здоровье и форма",
  endurance: "Выносливость",
  return_to_training: "Возврат к тренировкам",
  other: "Другое",
};

export default function ProfilesPage() {
  const [data, setData] = useState<ListResponse<ProfileListItem> | null>(null);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");

  function load(searchValue: string, statusValue: string) {
    api
      .profiles({ search: searchValue || undefined, status: statusValue || undefined })
      .then(setData)
      .catch((e) => setError(e.message));
  }

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    load("", "");
  }, []);

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <h1 className="page-title">Профили</h1>
        {error && <div className="error">{error}</div>}
        <div className="toolbar">
          <input
            type="text"
            placeholder="Поиск: имя, ID, номер..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load(search, status)}
          />
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Все статусы</option>
            <option value="confirmed">confirmed</option>
            <option value="draft">draft</option>
            <option value="in_progress">in_progress</option>
          </select>
          <button type="button" className="primary" onClick={() => load(search, status)}>
            Найти
          </button>
        </div>
        {!data && !error && <div className="loading">Загрузка...</div>}
        {data && (
          <>
            <p className="muted">Всего: {data.total}</p>
            <table>
              <thead>
                <tr>
                  <th>ID / Номер</th>
                  <th>Имя</th>
                  <th>Возраст</th>
                  <th>Цель</th>
                  <th>Статус</th>
                  <th>Дата</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((p) => (
                  <tr key={p.profile_id}>
                    <td>
                      <Link href={`/profiles/${p.profile_id}`}>
                        {p.display_number || p.profile_id.slice(0, 8)}
                      </Link>
                    </td>
                    <td>{p.name || "—"}</td>
                    <td>{p.age ?? "—"}</td>
                    <td>{p.primary_goal ? GOAL_LABELS[p.primary_goal] || p.primary_goal : "—"}</td>
                    <td>
                      <span className={`badge ${p.status}`}>{p.status}</span>
                    </td>
                    <td className="muted">
                      {p.created_at ? new Date(p.created_at).toLocaleString("ru-RU") : "—"}
                    </td>
                  </tr>
                ))}
                {data.items.length === 0 && (
                  <tr>
                    <td colSpan={6} className="muted">
                      Профили не найдены
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </>
        )}
      </main>
    </div>
  );
}
