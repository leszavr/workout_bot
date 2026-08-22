"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { clearToken } from "@/lib/api";
import { useCurrentUser } from "@/lib/session";

const NAV = [
  { href: "/", label: "Панель" },
  { href: "/profiles", label: "Профили" },
  { href: "/programs", label: "Программы" },
  { href: "/exercises", label: "Упражнения" },
  { href: "/ai", label: "AI-конфигурация" },
];

// Управление пользователями доступно только роли admin.
const ADMIN_NAV = [{ href: "/users", label: "Пользователи" }];

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
      <div className="brand">🏋️ Workout Bot</div>
      <nav>
        {items.map((item) => {
          const active =
            item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link key={item.href} href={item.href} className={active ? "active" : ""}>
              {item.label}
            </Link>
          );
        })}
      </nav>

      {user && (
        <div style={{ marginTop: "auto", padding: "16px 12px", fontSize: 13 }}>
          <div style={{ fontWeight: 600 }}>{user.display_name || user.login}</div>
          <div className="muted">
            {user.role === "admin" ? "администратор" : "только просмотр"}
          </div>
          {/* Аварийный вход стоит показывать явно: пароль лежит в конфигурации
              сервера, а не в базе, и сменить его через интерфейс нельзя. */}
          {user.is_env_admin && (
            <div className="badge draft" style={{ marginTop: 6 }}>
              аварийный вход
            </div>
          )}
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            {!user.is_env_admin && (
              <Link href="/change-password" className="muted">
                Сменить пароль
              </Link>
            )}
            <button type="button" onClick={logout}>
              Выйти
            </button>
          </div>
        </div>
      )}
    </aside>
  );
}
