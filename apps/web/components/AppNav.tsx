"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { clearToken } from "@/lib/api";
import { roleLabel } from "@/lib/labels";
import { useCurrentUser } from "@/lib/session";

// Иконки помогают находить раздел взглядом, не перечитывая подписи.
const NAV = [
  { href: "/", label: "Сводка", icon: "▦" },
  { href: "/profiles", label: "Анкеты", icon: "☰" },
  { href: "/programs", label: "Программы", icon: "▤" },
  { href: "/exercises", label: "Упражнения", icon: "⛋" },
  { href: "/ai", label: "Искусственный интеллект", icon: "◆" },
];

// Управление пользователями доступно только администратору.
const ADMIN_NAV = [{ href: "/users", label: "Пользователи", icon: "◍" }];

export default function AppNav() {
  const pathname = usePathname();
  const { user, canWrite } = useCurrentUser();
  const items = canWrite ? [...NAV, ...ADMIN_NAV] : NAV;

  const logout = () => {
    clearToken();
    window.location.href = "/login";
  };

  return (
    <aside className="sidebar">
      <div className="brand">Workout Bot</div>

      <nav aria-label="Разделы">
        {items.map((item) => {
          const active =
            item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={active ? "active" : ""}
              aria-current={active ? "page" : undefined}
            >
              <span className="nav-icon" aria-hidden="true">
                {item.icon}
              </span>
              {item.label}
            </Link>
          );
        })}
      </nav>

      {user && (
        <div className="sidebar-footer">
          <div className="who">{user.display_name || user.login}</div>
          <div className="role">{roleLabel(user.role)}</div>

          {/* Аварийный вход показываем явно: его пароль задан в настройках
              сервера, сменить через интерфейс нельзя. */}
          {user.is_env_admin && (
            <div className="role" style={{ marginTop: 4 }}>
              вход из настроек сервера
            </div>
          )}

          <div className="links">
            {!user.is_env_admin && (
              <Link href="/change-password">Сменить пароль</Link>
            )}
            <button type="button" className="linklike" onClick={logout}>
              Выйти
            </button>
          </div>
        </div>
      )}
    </aside>
  );
}
