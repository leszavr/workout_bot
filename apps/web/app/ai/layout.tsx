"use client";

// Общая обвязка подстраниц раздела ИИ: навигация, заголовок с пояснением
// и вкладки. Layout в App Router не перемонтируется при переходе между
// вкладками, поэтому переключение происходит без мигания.

import { useEffect } from "react";

import AppNav from "@/components/AppNav";
import AITabs from "@/components/ai/AITabs";
import { Notice } from "@/components/ui/Primitives";
import { getToken } from "@/lib/api";
import { useCurrentUser } from "@/lib/session";

export default function AILayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const { user } = useCurrentUser();

  useEffect(() => {
    if (!getToken()) window.location.href = "/login";
  }, []);

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <div className="page-head">
          <h1 className="page-title">Искусственный интеллект</h1>
          <p className="page-subtitle">
            Система может составлять программы тренировок с помощью ИИ. Если ИИ
            не настроен или недоступен, программу соберёт алгоритмический
            генератор — пользователь получит её в любом случае.
          </p>
        </div>

        <AITabs />

        {/* Предупреждаем один раз на весь раздел, а не в каждом блоке. */}
        {user && !user.can_write && (
          <Notice tone="info" title="Доступ только для просмотра">
            Ваша роль — наблюдатель. Состояние и журналы видны, изменять
            настройки нельзя.
          </Notice>
        )}

        {children}
      </main>
    </div>
  );
}
