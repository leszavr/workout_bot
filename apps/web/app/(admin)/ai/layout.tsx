"use client";

// Общая обвязка подстраниц раздела ИИ: заголовок с пояснением и вкладки.
// Layout в App Router не перемонтируется при переходе между вкладками, поэтому
// переключение происходит без мигания.

import { AI_TABS } from "@/components/ai/tabs";
import { PageHeader } from "@/components/layout/PageHeader";
import { PageTabs } from "@/components/ui/PageTabs";
import { Notice } from "@/components/ui/Primitives";
import { useCurrentUser } from "@/lib/session";

export default function AILayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const { user } = useCurrentUser();

  return (
    <>
      <PageHeader
        title="Искусственный интеллект"
        description="Система может составлять программы тренировок с помощью ИИ. Если ИИ не настроен или недоступен, программу соберёт алгоритмический генератор — пользователь получит её в любом случае."
        tabs={
          <PageTabs tabs={AI_TABS} root="/ai" label="Разделы настроек ИИ" />
        }
      />

      {/* Предупреждаем один раз на весь раздел, а не в каждом блоке. */}
      {user && !user.can_write && (
        <Notice tone="info" title="Доступ только для просмотра">
          Ваша роль — наблюдатель. Состояние и журналы видны, изменять настройки
          нельзя.
        </Notice>
      )}

      {children}
    </>
  );
}
