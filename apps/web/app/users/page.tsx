"use client";

// Пользователи внутреннего интерфейса.
//
// Раздел доступен только администратору: сервер отвечает отказом на все
// запросы наблюдателя, поэтому показываем это состояние явно, а не пустой
// список без объяснения.
//
// Привязки внешних аккаунтов здесь нет: вход через сторонние сервисы не
// работает, и показывать неработающую настройку нельзя.

import { useCallback, useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import {
  Card,
  Empty,
  Field,
  Notice,
  Skeleton,
  Status,
  Tag,
  moment,
} from "@/components/ui/Primitives";
import {
  AdminUserItem,
  ApiError,
  PasswordResetResult,
  getToken,
  usersApi,
} from "@/lib/api";
import { roleHint, roleLabel } from "@/lib/labels";
import { useCurrentUser } from "@/lib/session";

const MIN_PASSWORD_LENGTH = 10;

export default function UsersPage() {
  const { user: currentUser, loading: sessionLoading } = useCurrentUser();
  const [users, setUsers] = useState<AdminUserItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [forbidden, setForbidden] = useState(false);
  // Временный пароль показывается один раз — держим до закрытия карточки.
  const [reset, setReset] = useState<PasswordResetResult | null>(null);

  const load = useCallback(async () => {
    try {
      setUsers((await usersApi.list()).items);
      setForbidden(false);
      setError("");
    } catch (e) {
      const apiError = e as ApiError;
      if (apiError.status === 403) setForbidden(true);
      else setError(apiError.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    load().catch(() => undefined);
  }, [load]);

  const onChanged = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 6000);
    load().catch(() => undefined);
  };

  const shell = (children: React.ReactNode) => (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <div className="page-head">
          <h1 className="page-title">Пользователи</h1>
          <p className="page-subtitle">
            Доступ к внутреннему интерфейсу. Самостоятельной регистрации нет —
            учётные записи создаёт администратор.
          </p>
        </div>
        {children}
      </main>
    </div>
  );

  if (sessionLoading || loading) {
    return shell(
      <Card>
        <Skeleton rows={4} />
      </Card>
    );
  }

  if (forbidden) {
    return shell(
      <Card title="Раздел недоступен">
        <p style={{ marginTop: 0 }}>
          Управлять пользователями может только администратор. Ваша роль —
          наблюдатель.
        </p>
        <p className="field-hint" style={{ marginBottom: 0 }}>
          Если доступ действительно нужен, попросите администратора изменить
          вашу роль.
        </p>
      </Card>
    );
  }

  const admins = users.filter((u) => u.role === "admin" && u.is_active).length;

  return shell(
    <>
      {error && <div className="error">{error}</div>}
      {notice && <Notice tone="ok">{notice}</Notice>}

      {reset && <TemporaryPassword reset={reset} onClose={() => setReset(null)} />}

      <CreateUser onChanged={onChanged} onError={setError} />

      <Card
        title="Учётные записи"
        description="«Администратор» может менять настройки и управлять пользователями. «Наблюдатель» — только смотреть; это ограничение проверяет сервер, а не интерфейс."
      >
        {users.length === 0 ? (
          <Empty
            title="Учётных записей ещё нет"
            hint="Сейчас войти можно только администратором, заданным в настройках сервера. Создайте обычную учётную запись для повседневной работы."
          />
        ) : (
          <>
            {admins === 1 && (
              <Notice tone="info">
                Активный администратор всего один. Пока это так, его нельзя
                выключить, удалить или понизить — иначе настройки станет
                некому менять.
              </Notice>
            )}

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Кто</th>
                    <th>Роль</th>
                    <th>Состояние</th>
                    <th>Последний вход</th>
                    <th>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((item) => (
                    <UserRow
                      key={item.id}
                      item={item}
                      isSelf={currentUser?.login === item.login}
                      onChanged={onChanged}
                      onError={setError}
                      onReset={setReset}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Card>
    </>
  );
}

function TemporaryPassword(props: Readonly<{
  reset: PasswordResetResult;
  onClose: () => void;
}>) {
  return (
    <Card
      title={`Временный пароль для «${props.reset.login}»`}
      description="Показывается только сейчас и восстановить его будет нельзя. Передайте пароль пользователю — при входе система попросит его сменить."
      actions={
        <button type="button" className="small ghost" onClick={props.onClose}>
          Скрыть
        </button>
      }
    >
      <div className="field-row">
        <code style={{ fontSize: 17, padding: "8px 12px" }}>
          {props.reset.temporary_password}
        </code>
        <button
          type="button"
          onClick={() =>
            navigator.clipboard?.writeText(props.reset.temporary_password)
          }
        >
          Скопировать
        </button>
      </div>
    </Card>
  );
}

function CreateUser(props: Readonly<{
  onChanged: (message: string) => void;
  onError: (message: string) => void;
}>) {
  const [open, setOpen] = useState(false);
  const [login, setLogin] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("viewer");
  const [mustChange, setMustChange] = useState(true);

  const reset = () => {
    setLogin("");
    setDisplayName("");
    setPassword("");
    setRole("viewer");
    setMustChange(true);
  };

  const submit = async () => {
    try {
      await usersApi.create({
        login: login.trim(),
        display_name: displayName.trim() || null,
        password,
        role,
        must_change_password: mustChange,
      });
      reset();
      setOpen(false);
      props.onChanged(`Пользователь «${login.trim()}» создан`);
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  if (!open) {
    return (
      <Card
        title="Новая учётная запись"
        description="Понадобятся логин, начальный пароль и роль."
        actions={
          <button type="button" className="primary" onClick={() => setOpen(true)}>
            Добавить пользователя
          </button>
        }
      />
    );
  }

  const loginValid = /^[a-zA-Z0-9._-]{3,}$/.test(login.trim());
  const passwordValid = password.length >= MIN_PASSWORD_LENGTH;

  return (
    <Card title="Новый пользователь">
      <div className="form-grid">
        <Field
          label="Логин"
          hint="Латинские буквы, цифры, точка, дефис или подчёркивание. Не меньше трёх символов."
          error={
            login.length > 0 && !loginValid
              ? "Допустимы латинские буквы, цифры, . _ - (минимум 3 символа)"
              : undefined
          }
        >
          <input
            type="text"
            value={login}
            onChange={(e) => setLogin(e.target.value)}
            placeholder="ivanov"
            autoComplete="off"
            aria-label="Логин"
          />
        </Field>

        <Field label="Имя" hint="Как показывать в интерфейсе. Необязательно.">
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Иван Иванов"
            aria-label="Имя"
          />
        </Field>

        <Field
          label="Начальный пароль"
          hint={`Не меньше ${MIN_PASSWORD_LENGTH} символов. Передайте его пользователю лично.`}
          error={
            password.length > 0 && !passwordValid
              ? `Не меньше ${MIN_PASSWORD_LENGTH} символов`
              : undefined
          }
        >
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
            aria-label="Начальный пароль"
          />
        </Field>

        <Field label="Роль" hint={roleHint(role)}>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            aria-label="Роль"
          >
            <option value="viewer">Наблюдатель</option>
            <option value="admin">Администратор</option>
          </select>
        </Field>
      </div>

      <label className="check" style={{ marginTop: 16 }}>
        <input
          type="checkbox"
          checked={mustChange}
          onChange={(e) => setMustChange(e.target.checked)}
        />
        Попросить сменить пароль при первом входе
      </label>

      <div className="button-row" style={{ marginTop: 20 }}>
        <button
          type="button"
          className="primary"
          onClick={submit}
          disabled={!loginValid || !passwordValid}
        >
          Создать
        </button>
        <button
          type="button"
          className="ghost"
          onClick={() => {
            reset();
            setOpen(false);
          }}
        >
          Отмена
        </button>
      </div>
    </Card>
  );
}

function UserRow(props: Readonly<{
  item: AdminUserItem;
  isSelf: boolean;
  onChanged: (message: string) => void;
  onError: (message: string) => void;
  onReset: (reset: PasswordResetResult) => void;
}>) {
  const { item } = props;
  const [editing, setEditing] = useState(false);
  const [displayName, setDisplayName] = useState(item.display_name ?? "");
  const [role, setRole] = useState(item.role);

  const save = async () => {
    try {
      await usersApi.patch(item.id, {
        display_name: displayName.trim() || null,
        role,
      });
      setEditing(false);
      props.onChanged(`Данные «${item.login}» изменены`);
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  const toggleActive = async () => {
    try {
      await usersApi.patch(item.id, { is_active: !item.is_active });
      props.onChanged(
        item.is_active
          ? `Доступ «${item.login}» закрыт`
          : `Доступ «${item.login}» открыт`
      );
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  const resetPassword = async () => {
    if (
      !window.confirm(
        `Сбросить пароль «${item.login}»? Текущий пароль перестанет работать.`
      )
    ) {
      return;
    }
    try {
      props.onReset(await usersApi.resetPassword(item.id));
      props.onChanged(`Пароль «${item.login}» сброшен`);
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  const remove = async () => {
    if (!window.confirm(`Удалить «${item.login}»? Отменить это нельзя.`)) return;
    try {
      await usersApi.remove(item.id);
      props.onChanged(`Пользователь «${item.login}» удалён`);
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  if (editing) {
    return (
      <tr>
        <td colSpan={5}>
          <div className="form-grid">
            <Field label="Имя">
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Иван Иванов"
                aria-label="Имя"
              />
            </Field>
            <Field label="Роль" hint={roleHint(role)}>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value)}
                aria-label="Роль"
              >
                <option value="viewer">Наблюдатель</option>
                <option value="admin">Администратор</option>
              </select>
            </Field>
          </div>
          <div className="button-row" style={{ marginTop: 12 }}>
            <button type="button" className="primary small" onClick={save}>
              Сохранить
            </button>
            <button
              type="button"
              className="ghost small"
              onClick={() => setEditing(false)}
            >
              Отмена
            </button>
          </div>
        </td>
      </tr>
    );
  }

  return (
    <tr>
      <td>
        <strong>{item.display_name || item.login}</strong>
        {props.isSelf && <span className="muted"> — это вы</span>}
        <div className="field-hint">{item.login}</div>
      </td>
      <td>
        <Tag tone={item.role === "admin" ? "info" : "neutral"}>
          {roleLabel(item.role)}
        </Tag>
      </td>
      <td>
        <Status tone={item.is_active ? "ok" : "neutral"}>
          {item.is_active ? "доступ открыт" : "доступ закрыт"}
        </Status>
        {item.must_change_password && (
          <div className="field-hint">нужно сменить пароль</div>
        )}
      </td>
      <td className="text-secondary">{moment(item.last_login_at)}</td>
      <td className="actions">
        <div className="button-row">
          <button type="button" className="small" onClick={() => setEditing(true)}>
            Изменить
          </button>
          <button type="button" className="small" onClick={toggleActive}>
            {item.is_active ? "Закрыть доступ" : "Открыть доступ"}
          </button>
          <button type="button" className="small" onClick={resetPassword}>
            Сбросить пароль
          </button>
          {/* Себя удалить нельзя — сервер откажет, кнопку не показываем. */}
          {!props.isSelf && (
            <button type="button" className="small danger" onClick={remove}>
              Удалить
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}
