"use client";

// Боковое меню.
//
// Меню постоянное: администратор переключается между разделами десятки раз за
// сессию, и прятать навигацию за кнопкой означало бы добавить по клику к
// каждому переходу. На узком экране места для постоянного меню нет, поэтому
// там оно превращается в выдвижную панель — но остаётся тем же списком, а не
// вторым, сокращённым.
//
// Свёрнутое состояние сохраняется в браузере: это выбор рабочего места (узкий
// ноутбук против большого монитора), а не свойство страницы, и сбрасывать его
// на каждом переходе неправильно.

import Link from "next/link";
import { usePathname } from "next/navigation";

import { isNavItemActive, navGroupsFor } from "@/lib/nav";

export function Sidebar(props: Readonly<{
  canWrite: boolean;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  /** Открыта ли выдвижная панель на узком экране. */
  mobileOpen: boolean;
  onCloseMobile: () => void;
}>) {
  const pathname = usePathname();
  const groups = navGroupsFor(props.canWrite);

  const classes = [
    "admin-sidebar",
    props.collapsed ? "is-collapsed" : "",
    props.mobileOpen ? "is-open" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <aside className={classes} aria-label="Разделы">
      <div className="admin-sidebar-brand">
        <span className="admin-brand-mark" aria-hidden="true">
          Ф
        </span>
        {!props.collapsed && <span className="admin-brand-text">Форма</span>}
        <button
          type="button"
          className="ghost small admin-sidebar-collapse"
          onClick={props.onToggleCollapsed}
          aria-label={props.collapsed ? "Развернуть меню" : "Свернуть меню"}
          title={props.collapsed ? "Развернуть меню" : "Свернуть меню"}
        >
          {props.collapsed ? "»" : "«"}
        </button>
      </div>

      <nav className="admin-nav">
        {groups.map((group) => (
          <div className="admin-nav-group" key={group.title}>
            {/* В свёрнутом виде подпись группы не поместилась бы, но сама
                группировка сохраняется разделителем. */}
            {props.collapsed ? (
              <div className="admin-nav-divider" role="presentation" />
            ) : (
              <div className="admin-nav-title">{group.title}</div>
            )}
            {group.items.map((item) => {
              const active = isNavItemActive(item, pathname);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={active ? "admin-nav-link active" : "admin-nav-link"}
                  aria-current={active ? "page" : undefined}
                  title={props.collapsed ? item.label : undefined}
                  onClick={props.onCloseMobile}
                >
                  <span className="admin-nav-icon" aria-hidden="true">
                    {item.icon}
                  </span>
                  <span className="admin-nav-label">{item.label}</span>
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
    </aside>
  );
}
