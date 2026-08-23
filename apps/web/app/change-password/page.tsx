"use client";

// Смена своего пароля.
//
// Эта же страница обслуживает обязательную смену: если администратор выдал
// временный пароль, остальные разделы закрыты, и клиент приводит сюда сам.
// Поэтому страница не зависит от других запросов.

import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import { Card, Field, Notice } from "@/components/ui/Primitives";
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
  const sameAsOld =
    newPassword.length > 0 && newPassword === currentPassword;
  const canSubmit =
    currentPassword.length > 0 &&
    newPassword.length >= MIN_PASSWORD_LENGTH &&
    newPassword === repeat &&
    !sameAsOld &&
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
        <div className="page-head">
          <h1 className="page-title">Смена пароля</h1>
        </div>

        {loading && (
          <Card>
            <p className="muted" style={{ margin: 0 }}>
              Загрузка…
            </p>
          </Card>
        )}

        {user?.is_env_admin && (
          <Card title="Пароль задан в настройках сервера">
            <p style={{ marginTop: 0 }}>
              Вы вошли администратором, логин и пароль которого прописаны в
              настройках сервера. Сменить его через интерфейс нельзя — значение
              меняется в конфигурации.
            </p>
            <p className="field-hint" style={{ marginBottom: 0 }}>
              Для повседневной работы создайте обычную учётную запись в разделе
              «Пользователи».
            </p>
          </Card>
        )}

        {user && !user.is_env_admin && (
          <div style={{ maxWidth: 520 }}>
            {user.must_change_password && !done && (
              <Notice tone="warn" title="Нужно сменить пароль">
                Текущий пароль выдал администратор как временный. Пока вы его не
                смените, остальные разделы недоступны.
              </Notice>
            )}
            {done && <Notice tone="ok">Пароль изменён.</Notice>}
            {error && <div className="error">{error}</div>}

            <Card>
              <div className="stack">
                <Field label="Текущий пароль">
                  <input
                    type="password"
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    autoComplete="current-password"
                    aria-label="Текущий пароль"
                  />
                </Field>

                <Field
                  label="Новый пароль"
                  hint={`Не меньше ${MIN_PASSWORD_LENGTH} символов.`}
                  error={
                    tooShort
                      ? `Не меньше ${MIN_PASSWORD_LENGTH} символов`
                      : sameAsOld
                        ? "Новый пароль должен отличаться от текущего"
                        : undefined
                  }
                >
                  <input
                    type="password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    autoComplete="new-password"
                    aria-label="Новый пароль"
                  />
                </Field>

                <Field
                  label="Новый пароль ещё раз"
                  error={mismatch ? "Пароли не совпадают" : undefined}
                >
                  <input
                    type="password"
                    value={repeat}
                    onChange={(e) => setRepeat(e.target.value)}
                    autoComplete="new-password"
                    aria-label="Повторите новый пароль"
                  />
                </Field>

                <div className="button-row">
                  <button
                    type="button"
                    className="primary"
                    onClick={submit}
                    disabled={!canSubmit}
                  >
                    {saving ? "Сохраняем…" : "Сменить пароль"}
                  </button>
                </div>
              </div>
            </Card>
          </div>
        )}
      </main>
    </div>
  );
}
