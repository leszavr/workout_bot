"use client";

// Текущий пользователь для интерфейса: роль и права.
//
// Права берутся с сервера (`/auth/me`), а не выводятся из localStorage:
// клиент не должен решать, что ему разрешено. Скрытие кнопок по `canWrite` —
// только удобство; сам запрет обеспечивают guard'ы на backend.

import { useCallback, useEffect, useState } from "react";

import { CurrentUser, authApi, getToken } from "@/lib/api";

export interface SessionState {
  user: CurrentUser | null;
  loading: boolean;
  // Роль admin: можно менять конфигурацию и управлять пользователями.
  canWrite: boolean;
  reload: () => Promise<void>;
}

export function useCurrentUser(): SessionState {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      setUser(await authApi.me());
    } catch {
      // 401 обрабатывается в api-клиенте (редирект на /login), здесь
      // достаточно не показывать пользователя.
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload().catch(() => undefined);
  }, [reload]);

  return {
    user,
    loading,
    canWrite: user?.can_write ?? false,
    reload,
  };
}
