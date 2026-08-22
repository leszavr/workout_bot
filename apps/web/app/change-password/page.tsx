"use client";

// Смена собственного пароля.
//
// Эта же страница обслуживает обязательную смену: если администратор выдал
// временный пароль, API закрыт до его замены, и api-клиент приводит сюда
// автоматически. Поэтому страница не должна зависеть от других запросов.

import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import { authApi, getToken } from "@/lib/api";
import { useCurrentUser } from "@/lib/session";

const MIN_PASSWORD_LENGTH = 10;

export default function ChangePasswordPage() {
  const { user, loading, reload } = useCurrentUser();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!getToken()) window.location.href = "/login";
  }, []);

  const tooShort = newPassword.length > 0 && newPassword.length < MIN_PASSWORD_LENGTH;
  const mismatch = repeat.length > 0 && newPassword !== repeat;
  const canSubmit =
    currentPassword.length > 0 &&
    newPassword.length >= MIN_PASSWORD_LENGTH &&
    newPassword === repeat &&
    !saving;

  const submit = async () => {
    setSaving(true);
    setError("");
    try {
      await authApi.changePassword(currentPassword, newPassword);
      setDone(true);
      setCurrentPassword("");
      setNewPassword("");
      setRepeat("");
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <h1 className="page-title">Смена пароля</h1>

        {loading && <p className="muted">Загрузка...</p>}

        {user?.is_env_admin && (
          <div className="card">
            <p style={{ marginTop: 0 }}>
              Вы вошли аварийным администратором. Его логин и пароль задаются
              переменными окружения <code>ADMIN_LOGIN</code> и{" "}
              <code>ADMIN_PASSWORD</code>, поэтому сменить пароль через интерфейс
              нельзя — измените значение в конфигурации сервера.
            </p>
            <p className="muted" style={{ marginBottom: 0 }}>
              Для повседневной работы создайте обычного пользователя в разделе
              «Пользователи».
            </p>
          </div>
        )}

        {user && !user.is_env_admin && (
          <div className="card" style={{ maxWidth: 520 }}>
            {user.must_change_password && !done && (
              <div className="error" style={{ marginBottom: 12 }}>
                Пароль был выдан администратором как временный. Смените его,
                чтобы получить доступ к остальным разделам.
              </div>
            )}
            {done && (
              <div className="badge confirmed" style={{ marginBottom: 12 }}>
                Пароль изменён
              </div>
            )}
            {error && <div className="error" style={{ marginBottom: 12 }}>{error}</div>}

            <div style={{ display: "grid", gap: 12 }}>
              <label>
                Текущий пароль
                <input
                  type="password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  autoComplete="current-password"
                  aria-label="Текущий пароль"
                />
              </label>
              <label>
                Новый пароль
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  autoComplete="new-password"
                  aria-label="Новый пароль"
                />
              </label>
              <label>
                Повторите новый пароль
                <input
                  type="password"
                  value={repeat}
                  onChange={(e) => setRepeat(e.target.value)}
                  autoComplete="new-password"
                  aria-label="Повторите новый пароль"
                />
              </label>

              {/* Подсказки показываются до отправки, а не после отказа сервера. */}
              {tooShort && (
                <p className="error" style={{ margin: 0 }}>
                  Минимум {MIN_PASSWORD_LENGTH} символов.
                </p>
              )}
              {mismatch && (
                <p className="error" style={{ margin: 0 }}>
                  Пароли не совпадают.
                </p>
              )}
              {!tooShort && !mismatch && (
                <p className="muted" style={{ margin: 0 }}>
                  Минимум {MIN_PASSWORD_LENGTH} символов. Новый пароль должен
                  отличаться от текущего.
                </p>
              )}

              <div>
                <button
                  type="button"
                  className="primary"
                  onClick={submit}
                  disabled={!canSubmit}
                >
                  {saving ? "Сохранение..." : "Сменить пароль"}
                </button>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
