"use client";

// Вкладки раздела «База знаний».
//
// Разделено по вопросам администратора: чем описан мир (словарь) — насколько
// полно он описан (полнота) — что осталось незакрытым (незакрытые значения).
// У каждой вкладки свой адрес, поэтому на неё можно дать ссылку.

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/knowledge", label: "Оборудование" },
  { href: "/knowledge/health", label: "Полнота" },
  { href: "/knowledge/unmapped", label: "Незакрытые значения" },
];

export default function KnowledgeTabs() {
  const pathname = usePathname();

  return (
    <nav className="tabs" aria-label="Разделы базы знаний">
      {TABS.map((tab) => {
        // «Оборудование» активно только при точном совпадении, иначе он
        // подсвечивался бы на всех вложенных адресах.
        const active =
          tab.href === "/knowledge"
            ? pathname === "/knowledge"
            : pathname.startsWith(tab.href);
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
