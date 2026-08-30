"use client";

// Вкладки раздела ИИ.
//
// Разделено по вопросам, на которые отвечает администратор: работает ли
// сейчас — как подключено — где используется — что случилось. У каждой
// вкладки свой адрес, поэтому на неё можно дать ссылку.

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/ai", label: "Состояние" },
  { href: "/ai/providers", label: "Подключения" },
  { href: "/ai/tasks", label: "Задачи" },
  { href: "/ai/prompts", label: "Инструкции" },
  // «Качество» отвечает на вопрос «насколько хорошо это работает», а
  // «Генерации» — «что произошло в конкретной операции». Журналы остаются
  // отдельно: там события контура, а не показатели.
  { href: "/ai/analytics", label: "Качество" },
  { href: "/ai/generations", label: "Генерации" },
  { href: "/ai/logs", label: "Журналы" },
];

export default function AITabs() {
  const pathname = usePathname();

  return (
    <nav className="tabs" aria-label="Разделы настроек ИИ">
      {TABS.map((tab) => {
        // «Состояние» активно только при точном совпадении, иначе оно
        // подсвечивалось бы на всех вложенных адресах.
        const active =
          tab.href === "/ai" ? pathname === "/ai" : pathname.startsWith(tab.href);
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
