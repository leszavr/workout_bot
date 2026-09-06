"use client";

// Вкладки раздела «Внешние источники».
//
// Разделено по вопросам администратора: откуда данные (источники) — что решено
// по каждой записи (записи) — сошлись ли числа (полнота). У каждой вкладки свой
// адрес, поэтому на неё можно дать ссылку.

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/ingestion", label: "Источники" },
  { href: "/ingestion/records", label: "Записи" },
  { href: "/ingestion/health", label: "Полнота" },
];

export default function IngestionTabs() {
  const pathname = usePathname();

  return (
    <nav className="tabs" aria-label="Разделы внешних источников">
      {TABS.map((tab) => {
        // «Источники» активны только при точном совпадении, иначе вкладка
        // подсвечивалась бы на всех вложенных адресах.
        const active =
          tab.href === "/ingestion"
            ? pathname === "/ingestion"
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
