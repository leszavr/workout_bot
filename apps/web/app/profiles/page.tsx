"use client";

// Анкеты.
//
// Раздел накапливается: анкета остаётся в списке и после того, как программа по
// ней собрана и отправлена. Поэтому в списке видно, исполнена ли анкета, по этим
// признакам можно фильтровать и сортировать, а неактуальную анкету — удалить.
//
// «Скачано пользователем» здесь нет намеренно: Telegram Bot API не сообщает,
// открыл ли человек присланный документ. Достоверно известен только факт
// отправки, и показывается именно он, а не догадка.

import Link from "next/link";
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
  ApiError,
  ProfileListItem,
  ProfileListResponse,
  ProfileSort,
  api,
  getToken,
} from "@/lib/api";
import { questionnaireLabel, statusLabel, statusTone } from "@/lib/labels";
import { useCurrentUser } from "@/lib/session";

const SORTS: ReadonlyArray<{ value: ProfileSort; label: string }> = [
  { value: "created_desc", label: "Сначала новые" },
  { value: "created_asc", label: "Сначала старые" },
  { value: "generated_first", label: "Сначала с готовой программой" },
  { value: "not_generated_first", label: "Сначала без программы" },
  { value: "delivered_first", label: "Сначала отправленные человеку" },
  { value: "not_delivered_first", label: "Сначала неотправленные" },
];

// Значение фильтра в select: пустая строка — «не фильтровать».
type TriState = "" | "yes" | "no";

function triToBool(value: TriState): boolean | undefined {
  if (value === "yes") return true;
  if (value === "no") return false;
  return undefined;
}

export default function ProfilesPage() {
  const { canWrite } = useCurrentUser();
  const [data, setData] = useState<ProfileListResponse | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [generated, setGenerated] = useState<TriState>("");
  const [delivered, setDelivered] = useState<TriState>("");
  const [sort, setSort] = useState<ProfileSort>("created_desc");
  const [loading, setLoading] = useState(true);

  const load = useCallback(
    (params: {
      search: string;
      status: string;
      generated: TriState;
      delivered: TriState;
      sort: ProfileSort;
    }) => {
      setLoading(true);
      api
        .profiles({
          search: params.search || undefined,
          status: params.status || undefined,
          generated: triToBool(params.generated),
          delivered: triToBool(params.delivered),
          sort: params.sort,
        })
        .then((res) => {
          setData(res);
          setError("");
        })
        .catch((e) => setError((e as Error).message))
        .finally(() => setLoading(false));
    },
    []
  );

  const reload = useCallback(() => {
    load({ search, status, generated, delivered, sort });
  }, [load, search, status, generated, delivered, sort]);

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    load({
      search: "",
      status: "",
      generated: "",
      delivered: "",
      sort: "created_desc",
    });
  }, [load]);

  // Смена порядка применяется сразу: это не условие поиска, а способ смотреть
  // на тот же список.
  const changeSort = (value: ProfileSort) => {
    setSort(value);
    load({ search, status, generated, delivered, sort: value });
  };

  const onDeleted = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 6000);
    reload();
  };

  const filtered =
    search !== "" || status !== "" || generated !== "" || delivered !== "";

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <div className="page-head">
          <h1 className="page-title">Анкеты</h1>
          <p className="page-subtitle">
            Что человек рассказал о себе боту: цель, опыт, ограничения. На основе
            анкеты собирается программа тренировок.
          </p>
        </div>

        {error && <div className="error">{error}</div>}
        {notice && <Notice tone="ok">{notice}</Notice>}

        <Card>
          <div className="filters">
            <Field
              label="Поиск"
              hint="Имя, номер анкеты или её идентификатор."
              htmlFor="profiles-search"
            >
              <input
                id="profiles-search"
                type="search"
                placeholder="Например: Иван или 1042"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && reload()}
              />
            </Field>
            <Field
              label="Состояние анкеты"
              hint="Черновик — человек ещё отвечает на вопросы."
              htmlFor="profiles-status"
            >
              <select
                id="profiles-status"
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                <option value="">Любое</option>
                <option value="confirmed">Подтверждена</option>
                <option value="in_progress">Заполняется</option>
                <option value="draft">Черновик</option>
              </select>
            </Field>
            <Field
              label="Программа собрана"
              hint="Есть ли по анкете хотя бы одна программа."
              htmlFor="profiles-generated"
            >
              <select
                id="profiles-generated"
                value={generated}
                onChange={(e) => setGenerated(e.target.value as TriState)}
              >
                <option value="">Неважно</option>
                <option value="yes">Собрана</option>
                <option value="no">Не собрана</option>
              </select>
            </Field>
            <Field
              label="Отправлена человеку"
              hint="Программа ушла в Telegram. Открыл ли её человек, Telegram не сообщает."
              htmlFor="profiles-delivered"
            >
              <select
                id="profiles-delivered"
                value={delivered}
                onChange={(e) => setDelivered(e.target.value as TriState)}
              >
                <option value="">Неважно</option>
                <option value="yes">Отправлена</option>
                <option value="no">Не отправлена</option>
              </select>
            </Field>
            <div className="filters-actions">
              <button type="button" className="primary" onClick={reload}>
                Показать
              </button>
              {filtered && (
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    setSearch("");
                    setStatus("");
                    setGenerated("");
                    setDelivered("");
                    load({
                      search: "",
                      status: "",
                      generated: "",
                      delivered: "",
                      sort,
                    });
                  }}
                >
                  Сбросить
                </button>
              )}
            </div>
          </div>
        </Card>

        <Card
          title="Список анкет"
          description={data ? `Найдено: ${data.total}` : undefined}
          actions={
            /* Порядок стоит в шапке списка, а не среди фильтров: это не условие
               поиска, а способ смотреть на тот же результат, и он применяется
               сразу, без кнопки «Показать». */
            <label className="inline-list">
              <span className="field-hint">Порядок</span>
              <select
                aria-label="Порядок сортировки анкет"
                value={sort}
                onChange={(e) => changeSort(e.target.value as ProfileSort)}
              >
                {SORTS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          }
        >
          {loading && <Skeleton rows={4} />}

          {!loading && data && data.items.length === 0 && (
            <Empty
              title={filtered ? "Ничего не нашлось" : "Анкет пока нет"}
              hint={
                filtered
                  ? "Попробуйте изменить условия поиска или сбросить фильтры."
                  : "Анкета появляется, когда человек проходит опрос в боте."
              }
            />
          )}

          {!loading && data && data.items.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Кто</th>
                    <th>Номер</th>
                    <th>Возраст</th>
                    <th>Цель</th>
                    <th>Состояние</th>
                    <th>Программа</th>
                    <th>Отправлена</th>
                    <th>Создана</th>
                    {canWrite && <th>Действия</th>}
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((p) => (
                    <ProfileRow
                      key={p.profile_id}
                      item={p}
                      canWrite={canWrite}
                      onDeleted={onDeleted}
                      onError={setError}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </main>
    </div>
  );
}

function ProfileRow(props: Readonly<{
  item: ProfileListItem;
  canWrite: boolean;
  onDeleted: (message: string) => void;
  onError: (message: string) => void;
}>) {
  const { item, canWrite } = props;
  const [deleting, setDeleting] = useState(false);
  const label = item.display_number || item.profile_id;

  const remove = async () => {
    if (
      !window.confirm(
        `Удалить анкету ${label}? Ответы человека и записи об отправке будут ` +
          "удалены безвозвратно."
      )
    ) {
      return;
    }
    setDeleting(true);
    try {
      await api.deleteProfile(item.profile_id);
      props.onDeleted(`Анкета ${label} удалена`);
    } catch (e) {
      const error = e as ApiError;
      const blockers = error.blockers?.map((b) => b.detail).join("; ");
      props.onError(blockers ? `${error.message}. ${blockers}` : error.message);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <tr>
      <td>
        <Link href={`/profiles/${item.profile_id}`}>
          {item.name || "без имени"}
        </Link>
      </td>
      <td>{item.display_number || "—"}</td>
      <td>{item.age ?? "—"}</td>
      <td>{item.primary_goal ? questionnaireLabel(item.primary_goal) : "—"}</td>
      <td>
        <Status tone={statusTone(item.status)}>{statusLabel(item.status)}</Status>
      </td>
      <td>
        {item.has_program ? (
          <Tag tone="ok">собрана</Tag>
        ) : (
          <span className="muted">нет</span>
        )}
      </td>
      <td>
        {item.delivered ? (
          <>
            <Tag tone="ok">отправлена</Tag>
            <div className="field-hint">{moment(item.delivered_at)}</div>
          </>
        ) : (
          <span className="muted">нет</span>
        )}
      </td>
      <td className="muted">{moment(item.created_at)}</td>
      {canWrite && (
        <td className="actions">
          <button
            type="button"
            className="small danger"
            onClick={remove}
            disabled={deleting}
            // Кнопка не блокируется по has_program: администратор должен
            // увидеть, что именно мешает, а не гадать, почему нельзя.
            title={
              item.has_program
                ? "Сначала удалите программы этой анкеты"
                : undefined
            }
          >
            {deleting ? "Удаляем…" : "Удалить"}
          </button>
        </td>
      )}
    </tr>
  );
}
