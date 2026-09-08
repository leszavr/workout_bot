// Вкладки раздела «Оборудование».
//
// Разделено по вопросам администратора: чем описан мир (словарь) — насколько
// полно он описан (полнота) — что осталось незакрытым (незакрытые значения).
// У каждой вкладки свой адрес, поэтому на неё можно дать ссылку.

import { PageTab } from "@/components/ui/PageTabs";

export const KNOWLEDGE_TABS: readonly PageTab[] = [
  { href: "/knowledge", label: "Словарь" },
  { href: "/knowledge/health", label: "Полнота" },
  { href: "/knowledge/unmapped", label: "Незакрытые значения" },
];
