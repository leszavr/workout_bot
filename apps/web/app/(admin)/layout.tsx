"use client";

// Каркас для всех страниц внутреннего интерфейса.
//
// Страница входа лежит вне этой группы: там ещё нет ни пользователя, ни меню,
// и оборачивать её каркасом означало бы показать пустое меню до авторизации.

import { AdminShell } from "@/components/layout/AdminShell";

export default function AdminLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <AdminShell>{children}</AdminShell>;
}
