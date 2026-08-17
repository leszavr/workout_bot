"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";

// Кэш external_id → внутренний id, чтобы не запрашивать одно упражнение дважды.
const idCache = new Map<string, number | null>();

/**
 * Ссылка на карточку упражнения по каноническому external_id.
 * Программы ссылаются на external_id; карточка упражнения живёт по /exercises/{id}.
 * Компонент резолвит id один раз и рендерит обычную ссылку.
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
  const [internalId, setInternalId] = useState<number | null | undefined>(
    idCache.get(externalId)
  );

  useEffect(() => {
    if (internalId !== undefined) return;
    let cancelled = false;
    api
      .exerciseByExternalId(externalId, source)
      .then((ex) => {
        idCache.set(externalId, ex.id);
        if (!cancelled) setInternalId(ex.id);
      })
      .catch(() => {
        idCache.set(externalId, null);
        if (!cancelled) setInternalId(null);
      });
    return () => {
      cancelled = true;
    };
  }, [externalId, source, internalId]);

  if (internalId === undefined || internalId === null) {
    return <span>{children}</span>;
  }
  return <Link href={`/exercises/${internalId}`}>{children}</Link>;
}
