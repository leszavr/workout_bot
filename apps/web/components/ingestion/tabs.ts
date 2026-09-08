// Вкладки раздела «Внешние источники».
//
// Разделено по вопросам администратора: откуда данные (источники) — что решено
// по каждой записи (записи) — сошлись ли числа (полнота). У каждой вкладки свой
// адрес, поэтому на неё можно дать ссылку.

import { PageTab } from "@/components/ui/PageTabs";

export const INGESTION_TABS: readonly PageTab[] = [
  { href: "/ingestion", label: "Источники" },
  { href: "/ingestion/records", label: "Записи" },
  { href: "/ingestion/health", label: "Полнота" },
];
