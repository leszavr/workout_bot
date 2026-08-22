"use client";

// Управление пользователями админ-панели.
//
// Страница доступна только роли admin: backend отвечает 403 на все запросы
// от viewer, поэтому интерфейс сразу показывает это состояние, а не пустой
// список без объяснения.

import { useCallback, useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import {
  AdminIdentityItem,
  AdminUserItem,
  ApiError,
  PasswordResetResult,
  getToken,
  usersApi,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/session";

const MIN_PASSWORD_LENGTH = 10;

const ROLE_LABELS: Record<string, string> = {
  admin: "администратор",
  viewer: "только просмотр",
};

// Внешние провайдеры: OAuth-флоу пока не реализованы, привязка выполняется
// администратором вручную по идентификатору аккаунта.
const EXTERNAL_PROVIDERS = [
  { value: "yandex", label: "Яндекс" },
  { value: "vk", label: "VK" },
  { value: "max", label: "MAX" },
];

function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

function formatMoment(value: string | null): string {
  return value ? new Date(value).toLocaleString("ru-RU") : "—";
}

export default function UsersPage() {
  const { user: currentUser, loading: sessionLoading } = useCurrentUser();
  const [users, setUsers] = useState<AdminUserItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [forbidden, setForbidden] = useState(false);
  // Временный пароль показывается один раз — храним до закрытия карточки.
  const [reset, setReset] = useState<PasswordResetResult | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await usersApi.list();
      setUsers(data.items);
      setForbidden(false);
      setError("");
    } catch (e) {
      const apiError = e as ApiError;
      if (apiError.status === 403) {
        setForbidden(true);
      } else {
        setError(apiError.message);
      }
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

  const flash = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 6000);
  };

  const onChanged = (message: string) => {
    flash(message);
    load().catch(() => undefined);
  };

  if (sessionLoading || loading) {
    return (
      <div className="app-shell">
        <AppNav />
        <main className="main">
          <h1 className="page-title">Пользователи</h1>
          <p className="muted">Загрузка...</p>
        </main>
      </div>
    );
  }

  if (forbidden) {
    return (
      <div className="app-shell">
        <AppNav />
        <main className="main">
          <h1 className="page-title">Пользователи</h1>
          <div className="card">
            <p style={{ marginTop: 0 }}>
              Раздел доступен только администраторам. У вашей учётной записи
              роль «только просмотр».
            </p>
            <p className="muted" style={{ marginBottom: 0 }}>
              Попросите администратора изменить роль, если доступ действительно
              нужен.
            </p>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <h1 className="page-title">Пользователи</h1>
        {error && <div className="error">{error}</div>}
        {notice && <div className="badge confirmed">{notice}</div>}

        {reset && (
          <TemporaryPasswordCard reset={reset} onClose={() => setReset(null)} />
        )}

        <CreateUserForm onChanged={onChanged} onError={setError} />

        <div className="card">
          <h2 className="section-title" style={{ marginTop: 0 }}>
            Учётные записи ({users.length})
          </h2>
          <p className="muted" style={{ marginTop: 0 }}>
            «Администратор» может менять настройки и управлять пользователями.
            «Только просмотр» — читать данные без права изменений; это
            ограничение проверяется сервером.
          </p>

          {users.length === 0 ? (
            <p className="muted">
              Пользователей нет. Пока вход возможен только аварийным
              администратором из переменных окружения.
            </p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Логин</th>
                  <th>Имя</th>
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
          )}
        </div>
      </main>
    </div>
  );
}

function TemporaryPasswordCard(props: Readonly<{
  reset: PasswordResetResult;
  onClose: () => void;
}>) {
  return (
    <div className="card">
      <h2 className="section-title" style={{ marginTop: 0 }}>
        Временный пароль для «{props.reset.login}»
      </h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Пароль показывается только сейчас и больше не восстанавливается.
        Передайте его пользователю — при входе система потребует сменить пароль.
      </p>
      <div className="toolbar" style={{ alignItems: "center" }}>
        <code style={{ fontSize: 18, padding: "6px 10px" }}>
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
        <button type="button" onClick={props.onClose}>
          Скрыть
        </button>
      </div>
    </div>
  );
}

function CreateUserForm(props: Readonly<{
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
        login,
        display_name: displayName || null,
        password,
        role,
        must_change_password: mustChange,
      });
      reset();
      setOpen(false);
      props.onChanged(`Пользователь «${login}» создан`);
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  if (!open) {
    return (
      <div className="card">
        <div className="toolbar" style={{ alignItems: "center" }}>
          <button type="button" className="primary" onClick={() => setOpen(true)}>
            Добавить пользователя
          </button>
          <span className="muted">
            Логин, пароль и роль. По умолчанию пароль временный.
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <h2 className="section-title" style={{ marginTop: 0 }}>
        Новый пользователь
      </h2>
      <div className="toolbar">
        <input
          type="text"
          placeholder="Логин (латиница, цифры, . _ -)"
          value={login}
          onChange={(e) => setLogin(e.target.value)}
          aria-label="Логин"
        />
        <input
          type="text"
          placeholder="Имя для отображения"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          aria-label="Имя для отображения"
        />
        <input
          type="password"
          placeholder={`Пароль (минимум ${MIN_PASSWORD_LENGTH})`}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
          aria-label="Пароль"
        />
        <select
          value={role}
          onChange={(e) => setRole(e.target.value)}
          aria-label="Роль"
        >
          <option value="viewer">Только просмотр</option>
          <option value="admin">Администратор</option>
        </select>
        <label>
          <input
            type="checkbox"
            checked={mustChange}
            onChange={(e) => setMustChange(e.target.checked)}
          />{" "}
          требовать смену пароля при входе
        </label>
        <button
          type="button"
          className="primary"
          onClick={submit}
          disabled={login.length < 3 || password.length < MIN_PASSWORD_LENGTH}
        >
          Создать
        </button>
        <button
          type="button"
          onClick={() => {
            reset();
            setOpen(false);
          }}
        >
          Отменить
        </button>
      </div>
    </div>
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
  const [identitiesOpen, setIdentitiesOpen] = useState(false);

  const save = async () => {
    try {
      await usersApi.patch(item.id, {
        display_name: displayName || null,
        role,
      });
      setEditing(false);
      props.onChanged(`Пользователь «${item.login}» изменён`);
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  const toggleActive = async () => {
    try {
      await usersApi.patch(item.id, { is_active: !item.is_active });
      props.onChanged(
        item.is_active
          ? `Пользователь «${item.login}» отключён`
          : `Пользователь «${item.login}» включён`
      );
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  const resetPassword = async () => {
    if (
      !window.confirm(
        `Сбросить пароль для «${item.login}»? Текущий пароль перестанет работать.`
      )
    ) {
      return;
    }
    try {
      props.onReset(await usersApi.resetPassword(item.id));
      props.onChanged(`Пароль для «${item.login}» сброшен`);
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  const remove = async () => {
    if (!window.confirm(`Удалить пользователя «${item.login}»? Необратимо.`)) return;
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
        <td colSpan={6}>
          <div className="toolbar">
            <strong>{item.login}</strong>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Имя для отображения"
              aria-label="Имя для отображения"
            />
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              aria-label="Роль пользователя"
            >
              <option value="viewer">Только просмотр</option>
              <option value="admin">Администратор</option>
            </select>
            <button type="button" className="primary" onClick={save}>
              Сохранить
            </button>
            <button type="button" onClick={() => setEditing(false)}>
              Отменить
            </button>
          </div>
        </td>
      </tr>
    );
  }

  return (
    <>
      <tr>
        <td>
          {item.login}
          {props.isSelf && <span className="muted"> (вы)</span>}
        </td>
        <td>{item.display_name ?? "—"}</td>
        <td>
          <span className={item.role === "admin" ? "badge confirmed" : "badge"}>
            {roleLabel(item.role)}
          </span>
        </td>
        <td>
          <span className={item.is_active ? "badge confirmed" : "badge draft"}>
            {item.is_active ? "активен" : "отключён"}
          </span>
          {item.must_change_password && (
            <span className="badge draft"> нужна смена пароля</span>
          )}
          {!item.has_password && (
            <span className="badge draft"> без пароля</span>
          )}
        </td>
        <td className="muted">{formatMoment(item.last_login_at)}</td>
        <td>
          <button type="button" onClick={() => setEditing(true)}>
            Изменить
          </button>{" "}
          <button type="button" onClick={toggleActive}>
            {item.is_active ? "Отключить" : "Включить"}
          </button>{" "}
          <button type="button" onClick={resetPassword}>
            Сбросить пароль
          </button>{" "}
          <button type="button" onClick={() => setIdentitiesOpen(!identitiesOpen)}>
            Внешние входы
          </button>{" "}
          {/* Себя удалить нельзя — сервер вернёт 409, кнопку не показываем. */}
          {!props.isSelf && (
            <button type="button" onClick={remove}>
              Удалить
            </button>
          )}
        </td>
      </tr>
      {identitiesOpen && (
        <tr>
          <td colSpan={6}>
            <IdentitiesBlock
              userId={item.id}
              login={item.login}
              onError={props.onError}
              onChanged={props.onChanged}
            />
          </td>
        </tr>
      )}
    </>
  );
}

function IdentitiesBlock(props: Readonly<{
  userId: number;
  login: string;
  onChanged: (message: string) => void;
  onError: (message: string) => void;
}>) {
  const { userId, login, onChanged, onError } = props;
  const [items, setItems] = useState<AdminIdentityItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [provider, setProvider] = useState("yandex");
  const [accountId, setAccountId] = useState("");

  const load = useCallback(async () => {
    try {
      setItems((await usersApi.identities(userId)).items);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [userId, onError]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const link = async () => {
    try {
      await usersApi.linkIdentity(userId, provider, accountId);
      setAccountId("");
      await load();
      onChanged(`Внешний вход привязан к «${login}»`);
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const unlink = async (identityId: number) => {
    try {
      await usersApi.unlinkIdentity(userId, identityId);
      await load();
      onChanged("Внешний вход отвязан");
    } catch (e) {
      onError((e as Error).message);
    }
  };

  return (
    <div style={{ marginLeft: 8 }}>
      <h4 className="section-title">Внешние входы</h4>
      <p className="muted" style={{ marginTop: 0 }}>
        Вход через Яндекс, VK и MAX подготовлен на уровне данных, но сами
        OAuth-переходы ещё не реализованы: привязка выполняется вручную по
        идентификатору аккаунта у провайдера.
      </p>

      {loading ? (
        <p className="muted">Загрузка...</p>
      ) : items.length === 0 ? (
        <p className="muted">Привязанных аккаунтов нет.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Провайдер</th>
              <th>Идентификатор аккаунта</th>
              <th>Привязан</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map((identity) => (
              <tr key={identity.id}>
                <td>
                  {EXTERNAL_PROVIDERS.find((p) => p.value === identity.provider)
                    ?.label ?? identity.provider}
                </td>
                <td className="muted">{identity.provider_user_id}</td>
                <td className="muted">{formatMoment(identity.created_at)}</td>
                <td>
                  <button type="button" onClick={() => unlink(identity.id)}>
                    Отвязать
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="toolbar">
        <select
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          aria-label="Провайдер внешнего входа"
        >
          {EXTERNAL_PROVIDERS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
        <input
          type="text"
          placeholder="Идентификатор аккаунта у провайдера"
          value={accountId}
          onChange={(e) => setAccountId(e.target.value)}
          aria-label="Идентификатор аккаунта"
          style={{ minWidth: 280 }}
        />
        <button
          type="button"
          onClick={link}
          disabled={accountId.trim().length === 0}
        >
          Привязать
        </button>
      </div>
    </div>
  );
}
