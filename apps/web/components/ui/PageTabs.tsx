"use client";

// Вкладки внутри раздела.
//
// Раздел с несколькими экранами (база знаний, внешние источники, ИИ) не
// разворачивается в боковом меню: там он остаётся одной строкой, а внутренние
// экраны переключаются вкладками. Так меню не разрастается, а адрес вкладки
// остаётся ссылкой, которую можно передать.
//
// До этого каждый раздел имел собственный компонент вкладок с одинаковым
// кодом и одинаковым правилом «корень активен только при точном совпадении».

import Link from "next/link";
import { usePathname } from "next/navigation";

export interface PageTab {
  href: string;
  label: string;
}

export function PageTabs(props: Readonly<{
  tabs: readonly PageTab[];
  /** Адрес корня раздела: активен только при точном совпадении. */
  root: string;
  label: string;
}>) {
  const pathname = usePathname();

  return (
    <nav className="tabs" aria-label={props.label}>
      {props.tabs.map((tab) => {
        const active =
          tab.href === props.root
            ? pathname === props.root
            : pathname === tab.href || pathname.startsWith(`${tab.href}/`);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            className={active ? "active" : ""}
            aria-current={active ? "page" : undefined}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
