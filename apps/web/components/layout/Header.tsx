"use client";

// Верхняя полоса рабочей области.
//
// Здесь три вещи, которых не должно быть на каждой странице по отдельности:
// кнопка меню для узкого экрана, путь до текущего раздела и текущий
// пользователь с выходом. Раньше пользователь и выход жили в подвале бокового
// меню — на узком экране, где меню превращается в выдвижную панель, до них
// нельзя было добраться, не открыв меню.

import Link from "next/link";
import { usePathname } from "next/navigation";

import { CurrentUser, clearToken } from "@/lib/api";
import { roleLabel } from "@/lib/labels";
import { activeNavItem } from "@/lib/nav";

export function Header(props: Readonly<{
  user: CurrentUser | null;
  onOpenMobileNav: () => void;
}>) {
  const pathname = usePathname();
  const section = activeNavItem(pathname);
  const { user } = props;

  const logout = () => {
    clearToken();
    window.location.href = "/login";
  };

  return (
    <header className="admin-header">
      <button
        type="button"
        className="ghost small admin-header-menu"
        onClick={props.onOpenMobileNav}
        aria-label="Открыть меню"
      >
        ☰
      </button>

      {/* Путь, а не заголовок страницы: заголовок с пояснением остаётся в
          рабочей области, где на него есть место. */}
      <nav className="admin-breadcrumb" aria-label="Текущее расположение">
        <Link href="/">Форма</Link>
        {section && section.href !== "/" && (
          <>
            <span className="admin-breadcrumb-sep" aria-hidden="true">
              /
            </span>
            <span className="admin-breadcrumb-current">{section.label}</span>
          </>
        )}
      </nav>

      {user && (
        <div className="admin-header-user">
          <div className="admin-header-who">
            <span className="admin-header-name">
              {user.display_name || user.login}
            </span>
            <span className="admin-header-role">
              {roleLabel(user.role)}
              {/* Аварийный вход показываем явно: его пароль задан в настройках
                  сервера, сменить через интерфейс нельзя. */}
              {user.is_env_admin && " · вход из настроек сервера"}
            </span>
          </div>
          {!user.is_env_admin && (
            <Link className="btn small" href="/change-password">
              Пароль
            </Link>
          )}
          <button type="button" className="small" onClick={logout}>
            Выйти
          </button>
        </div>
      )}
    </header>
  );
}
