"use client";

// Каркас внутреннего интерфейса: меню, шапка, рабочая область.
//
// Раньше каждая страница собирала каркас сама: 23 файла повторяли
// `<div className="app-shell"><AppNav /><main className="main">`, а проверку
// токена дублировали 22 раза. Любая правка навигации требовала обхода всех
// страниц, и расхождения появлялись сами: карточка операции генерации
// оставалась вообще без меню. Здесь каркас один, страницы отдают только
// содержимое.

import { useEffect, useState } from "react";

import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { getToken } from "@/lib/api";
import { useCurrentUser } from "@/lib/session";

const COLLAPSE_KEY = "workout_admin_nav_collapsed";

export function AdminShell(props: Readonly<{ children: React.ReactNode }>) {
  const { user, canWrite } = useCurrentUser();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  // Токен проверяется в одном месте на весь интерфейс. Страницы больше не
  // повторяют этот переход, поэтому забыть его на новой странице нельзя.
  useEffect(() => {
    if (!getToken()) window.location.href = "/login";
  }, []);

  // Состояние меню читается после монтирования: на сервере localStorage нет, и
  // чтение в начальном значении useState разошлось бы с первым рендером на
  // клиенте.
  useEffect(() => {
    setCollapsed(window.localStorage.getItem(COLLAPSE_KEY) === "1");
  }, []);

  const toggleCollapsed = () => {
    setCollapsed((previous) => {
      const next = !previous;
      window.localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
      return next;
    });
  };

  return (
    <div className={collapsed ? "admin-shell is-collapsed" : "admin-shell"}>
      <Sidebar
        canWrite={canWrite}
        collapsed={collapsed}
        onToggleCollapsed={toggleCollapsed}
        mobileOpen={mobileOpen}
        onCloseMobile={() => setMobileOpen(false)}
      />

      {/* Затемнение под выдвижной панелью: закрывает её по нажатию мимо меню. */}
      {mobileOpen && (
        <button
          type="button"
          className="admin-scrim"
          aria-label="Закрыть меню"
          onClick={() => setMobileOpen(false)}
        />
      )}

      <div className="admin-main">
        <Header user={user} onOpenMobileNav={() => setMobileOpen(true)} />
        <div className="admin-content">{props.children}</div>
      </div>
    </div>
  );
}
