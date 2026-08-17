"use client";

import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import { api, Dashboard, getToken } from "@/lib/api";

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    api
      .dashboard()
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <h1 className="page-title">Dashboard</h1>
        {error && <div className="error">{error}</div>}
        {!data && !error && <div className="loading">Загрузка...</div>}
        {data && (
          <div className="stats-grid">
            <div className="stat">
              <div className="value">{data.users_total}</div>
              <div className="label">Всего пользователей</div>
            </div>
            <div className="stat">
              <div className="value">{data.profiles_total}</div>
              <div className="label">Всего профилей</div>
            </div>
            <div className="stat">
              <div className="value">{data.profiles_today}</div>
              <div className="label">Новых сегодня</div>
            </div>
            <div className="stat">
              <div className="value">{data.exercises_total}</div>
              <div className="label">Упражнений в каталоге</div>
            </div>
            <div className="stat">
              <div className="value">{data.programs_total ?? "—"}</div>
              <div className="label">Программ тренировок</div>
            </div>
          </div>
        )}
        {data?.programs_total === null && (
          <p className="muted">
            Программы тренировок пока не генерируются — статистика по ним появится
            на следующем этапе.
          </p>
        )}
      </main>
    </div>
  );
}
