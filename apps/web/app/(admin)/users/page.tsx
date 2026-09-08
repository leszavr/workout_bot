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

import { PageHeader } from "@/components/layout/PageHeader";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataColumn, DataTable } from "@/components/ui/DataTable";
import {
  Card,
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
  usersApi,
} from "@/lib/api";
import { roleHint, roleLabel } from "@/lib/labels";
import { useCurrentUser } from "@/lib/session";

const MIN_PASSWORD_LENGTH = 10;

/** Необратимое действие над учётной записью: подтверждается диалогом. */
type PendingAction =
  | { kind: "reset"; item: AdminUserItem }
  | { kind: "delete"; item: AdminUserItem };

export default function UsersPage() {
  const { user: currentUser, loading: sessionLoading } = useCurrentUser();
  const [users, setUsers] = useState<AdminUserItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [forbidden, setForbidden] = useState(false);
  // Временный пароль показывается один раз — держим до закрытия карточки.
  const [reset, setReset] = useState<PasswordResetResult | null>(null);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<AdminUserItem | null>(null);
  const [creating, setCreating] = useState(false);

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
    load().catch(() => undefined);
  }, [load]);

  const onChanged = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 6000);
    setEditing(null);
    setCreating(false);
    load().catch(() => undefined);
  };

  const toggleActive = async (item: AdminUserItem) => {
    try {
      await usersApi.patch(item.id, { is_active: !item.is_active });
      onChanged(
        item.is_active
          ? `Доступ «${item.login}» закрыт`
          : `Доступ «${item.login}» открыт`,
      );
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const runPending = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      if (pending.kind === "reset") {
        setReset(await usersApi.resetPassword(pending.item.id));
        onChanged(`Пароль «${pending.item.login}» сброшен`);
      } else {
        await usersApi.remove(pending.item.id);
        onChanged(`Пользователь «${pending.item.login}» удалён`);
      }
      setPending(null);
    } catch (e) {
      setError((e as Error).message);
      setPending(null);
    } finally {
      setBusy(false);
    }
  };

  const header = (
    <PageHeader
      title="Пользователи"
      description="Доступ к внутреннему интерфейсу. Самостоятельной регистрации нет — учётные записи создаёт администратор."
      actions={
        !forbidden && !creating && !editing ? (
          <button
            type="button"
            className="primary"
            onClick={() => {
              setEditing(null);
              setCreating(true);
            }}
          >
            Добавить пользователя
          </button>
        ) : undefined
      }
    />
  );

  if (sessionLoading || loading) {
    return (
      <>
        {header}
        <Card>
          <Skeleton rows={4} />
        </Card>
      </>
    );
  }

  if (forbidden) {
    return (
      <>
        {header}
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
      </>
    );
  }

  const admins = users.filter((u) => u.role === "admin" && u.is_active).length;

  const columns: ReadonlyArray<DataColumn<AdminUserItem>> = [
    {
      key: "who",
      header: "Кто",
      render: (item) => (
        <>
          <strong>{item.display_name || item.login}</strong>
          {currentUser?.login === item.login && (
            <span className="muted"> — это вы</span>
          )}
          <div className="field-hint">{item.login}</div>
        </>
      ),
    },
    {
      key: "role",
      header: "Роль",
      hint: "«Администратор» может менять настройки; «Наблюдатель» — только смотреть. Ограничение проверяет сервер, а не интерфейс.",
      render: (item) => (
        <Tag tone={item.role === "admin" ? "info" : "neutral"}>
          {roleLabel(item.role)}
        </Tag>
      ),
    },
    {
      key: "state",
      header: "Состояние",
      render: (item) => (
        <>
          <Status tone={item.is_active ? "ok" : "neutral"}>
            {item.is_active ? "доступ открыт" : "доступ закрыт"}
          </Status>
          {item.must_change_password && (
            <div className="field-hint">нужно сменить пароль</div>
          )}
        </>
      ),
    },
    {
      key: "last_login",
      header: "Последний вход",
      render: (item) => (
        <span className="text-secondary">{moment(item.last_login_at)}</span>
      ),
    },
    {
      key: "actions",
      header: "Действия",
      render: (item) => (
        <div className="button-row">
          <button
            type="button"
            className="small"
            onClick={() => {
              setCreating(false);
              setEditing(item);
            }}
          >
            Изменить
          </button>
          <button
            type="button"
            className="small"
            onClick={() => toggleActive(item)}
          >
            {item.is_active ? "Закрыть доступ" : "Открыть доступ"}
          </button>
          <button
            type="button"
            className="small"
            onClick={() => setPending({ kind: "reset", item })}
          >
            Сбросить пароль
          </button>
          {/* Себя удалить нельзя — сервер откажет, кнопку не показываем. */}
          {currentUser?.login !== item.login && (
            <button
              type="button"
              className="small danger"
              onClick={() => setPending({ kind: "delete", item })}
            >
              Удалить
            </button>
          )}
        </div>
      ),
    },
  ];

  return (
    <>
      {header}

      {notice && <Notice tone="ok">{notice}</Notice>}

      {reset && (
        <TemporaryPassword reset={reset} onClose={() => setReset(null)} />
      )}

      {(creating || editing) && (
        <UserForm
          item={editing ?? undefined}
          onCancel={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSaved={onChanged}
          onError={setError}
        />
      )}

      {admins === 1 && (
        <Notice tone="info">
          Активный администратор всего один. Пока это так, его нельзя выключить,
          удалить или понизить — иначе настройки станет некому менять.
        </Notice>
      )}

      <Card title="Учётные записи" description={`Всего: ${users.length}`}>
        <DataTable
          columns={columns}
          rows={users}
          rowKey={(item) => String(item.id)}
          error={error}
          emptyTitle="Учётных записей ещё нет"
          emptyHint="Сейчас войти можно только администратором, заданным в настройках сервера. Создайте обычную учётную запись для повседневной работы."
        />
      </Card>

      {pending && (
        <ConfirmDialog
          title={
            pending.kind === "reset"
              ? `Сбросить пароль «${pending.item.login}»?`
              : `Удалить «${pending.item.login}»?`
          }
          description={
            pending.kind === "reset"
              ? "Текущий пароль перестанет работать. Новый временный пароль будет показан один раз — передайте его пользователю."
              : "Учётная запись будет удалена безвозвратно. Отменить это нельзя."
          }
          confirmLabel={
            pending.kind === "reset" ? "Сбросить пароль" : "Удалить запись"
          }
          danger={pending.kind === "delete"}
          busy={busy}
          onConfirm={runPending}
          onCancel={() => setPending(null)}
        />
      )}
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

/**
 * Форма учётной записи: создание и правка в одном месте.
 *
 * Раньше правка жила внутри строки таблицы: строка превращалась в форму на всю
 * ширину, остальные столбцы исчезали, и было непонятно, что именно правится.
 * Форма над списком показывает, чью запись меняют, и не ломает таблицу.
 */
function UserForm(props: Readonly<{
  item?: AdminUserItem;
  onCancel: () => void;
  onSaved: (message: string) => void;
  onError: (message: string) => void;
}>) {
  const existing = props.item;
  const [login, setLogin] = useState(existing?.login ?? "");
  const [displayName, setDisplayName] = useState(existing?.display_name ?? "");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState(existing?.role ?? "viewer");
  const [mustChange, setMustChange] = useState(true);
  const [busy, setBusy] = useState(false);

  const loginValid = /^[a-zA-Z0-9._-]{3,}$/.test(login.trim());
  const passwordValid = password.length >= MIN_PASSWORD_LENGTH;
  const valid = existing ? true : loginValid && passwordValid;

  const submit = async () => {
    setBusy(true);
    try {
      if (existing) {
        await usersApi.patch(existing.id, {
          display_name: displayName.trim() || null,
          role,
        });
        props.onSaved(`Данные «${existing.login}» изменены`);
      } else {
        await usersApi.create({
          login: login.trim(),
          display_name: displayName.trim() || null,
          password,
          role,
          must_change_password: mustChange,
        });
        props.onSaved(`Пользователь «${login.trim()}» создан`);
      }
    } catch (e) {
      props.onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title={existing ? `Учётная запись «${existing.login}»` : "Новый пользователь"}
      description={
        existing
          ? "Логин не меняется: по нему пользователь входит и на него ссылается журнал изменений."
          : "Понадобятся логин, начальный пароль и роль."
      }
      actions={
        <button
          type="button"
          className="ghost small"
          onClick={props.onCancel}
          disabled={busy}
        >
          Закрыть
        </button>
      }
    >
      <div className="form-grid">
        {!existing && (
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
        )}

        <Field label="Имя" hint="Как показывать в интерфейсе. Необязательно.">
          <input
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Иван Иванов"
            aria-label="Имя"
          />
        </Field>

        {!existing && (
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
        )}

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

      {!existing && (
        <label className="check" style={{ marginTop: "var(--s-3)" }}>
          <input
            type="checkbox"
            checked={mustChange}
            onChange={(e) => setMustChange(e.target.checked)}
          />
          Попросить сменить пароль при первом входе
        </label>
      )}

      <div className="button-row" style={{ marginTop: "var(--s-4)" }}>
        <button
          type="button"
          className="primary"
          onClick={submit}
          disabled={!valid || busy}
        >
          {existing ? "Сохранить" : "Создать"}
        </button>
        <button
          type="button"
          className="ghost"
          onClick={props.onCancel}
          disabled={busy}
        >
          Отмена
        </button>
      </div>
    </Card>
  );
}
