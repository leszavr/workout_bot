"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import { Card, Empty, Skeleton } from "@/components/ui/Primitives";
import { api, Dashboard, getToken } from "@/lib/api";

// Что означает каждое число: без пояснения «Профилей 12» непонятно,
// это анкеты, пользователи или программы.
const CARDS: Array<{
  key: keyof Dashboard;
  label: string;
  hint: string;
  href?: string;
}> = [
  {
    key: "users_total",
    label: "Пользователи бота",
    hint: "Люди, которые хотя бы раз открыли бот в Telegram.",
  },
  {
    key: "profiles_total",
    label: "Анкеты",
    hint: "Заполненные анкеты с целями, опытом и ограничениями.",
    href: "/profiles",
  },
  {
    key: "profiles_today",
    label: "Анкет за сегодня",
    hint: "Сколько анкет создано с начала суток.",
  },
  {
    key: "exercises_total",
    label: "Упражнения в каталоге",
    hint: "Из них собираются программы тренировок.",
    href: "/exercises",
  },
  {
    key: "programs_total",
    label: "Программы тренировок",
    hint: "Созданные программы всех версий.",
    href: "/programs",
  },
];

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

  const empty =
    data !== null &&
    !data.users_total &&
    !data.profiles_total &&
    !data.exercises_total;

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <div className="page-head">
          <h1 className="page-title">Сводка</h1>
          <p className="page-subtitle">
            Ключевые числа по боту: сколько людей пришло, сколько анкет собрано и
            что есть в каталоге упражнений.
          </p>
        </div>

        {error && <div className="error">{error}</div>}

        {!data && !error && (
          <Card>
            <Skeleton rows={4} />
          </Card>
        )}

        {empty && (
          <Card>
            <Empty
              title="Данных пока нет"
              hint="Числа появятся, как только кто-нибудь начнёт диалог с ботом в Telegram."
            />
          </Card>
        )}

        {data && !empty && (
          <div className="stats-grid">
            {CARDS.map((card) => {
              const value = data[card.key];
              return (
                <div className="stat" key={card.key}>
                  <div className="value">
                    {typeof value === "number" ? value : "—"}
                  </div>
                  <div className="label">
                    {card.href ? (
                      <Link href={card.href}>{card.label}</Link>
                    ) : (
                      card.label
                    )}
                  </div>
                  <p className="field-hint">{card.hint}</p>
                </div>
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
