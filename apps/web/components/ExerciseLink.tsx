"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";

// Кэш external_id → название и внутренний id: одно упражнение встречается
// в программе несколько раз, запрашивать его повторно не нужно.
const cache = new Map<string, { id: number; name: string } | null>();

/**
 * Ссылка на карточку упражнения по его коду в справочнике.
 *
 * В программе упражнение хранится кодом вида `barbell_squat`. Показывать
 * такой код администратору бессмысленно, поэтому компонент подставляет
 * человеческое название, а код оставляет только как запасной вариант,
 * если упражнения в каталоге уже нет.
 */
export default function ExerciseLink({
  externalId,
  source,
  children,
}: {
  readonly externalId: string;
  readonly source?: string;
  readonly children: React.ReactNode;
}) {
  const [resolved, setResolved] = useState<
    { id: number; name: string } | null | undefined
  >(cache.get(externalId));

  useEffect(() => {
    if (resolved !== undefined) return;
    let cancelled = false;
    api
      .exerciseByExternalId(externalId, source)
      .then((ex) => {
        const entry = { id: ex.id, name: ex.name_ru || ex.name };
        cache.set(externalId, entry);
        if (!cancelled) setResolved(entry);
      })
      .catch(() => {
        cache.set(externalId, null);
        if (!cancelled) setResolved(null);
      });
    return () => {
      cancelled = true;
    };
  }, [externalId, source, resolved]);

  if (resolved === undefined) {
    return <span className="muted">{children}</span>;
  }
  if (resolved === null) {
    return (
      <span title="Упражнения больше нет в каталоге">{children}</span>
    );
  }
  return <Link href={`/exercises/${resolved.id}`}>{resolved.name}</Link>;
}
