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

import { PageHeader } from "@/components/layout/PageHeader";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataColumn, DataTable } from "@/components/ui/DataTable";
import { FilterBar, SearchInput } from "@/components/ui/FilterBar";
import {
  Card,
  Field,
  Notice,
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
} from "@/lib/api";
import { questionnaireLabel, statusLabel, statusTone } from "@/lib/labels";
import { useCurrentUser } from "@/lib/session";

const PAGE_SIZE = 50;

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

interface Filters {
  search: string;
  status: string;
  generated: TriState;
  delivered: TriState;
}

const EMPTY: Filters = { search: "", status: "", generated: "", delivered: "" };

export default function ProfilesPage() {
  const { canWrite } = useCurrentUser();
  const [data, setData] = useState<ProfileListResponse | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);

  const [draftSearch, setDraftSearch] = useState("");
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [sort, setSort] = useState<ProfileSort>("created_desc");
  const [offset, setOffset] = useState(0);
  const [pending, setPending] = useState<ProfileListItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(
    (next: Filters, order: ProfileSort, page: number) => {
      setLoading(true);
      api
        .profiles({
          search: next.search || undefined,
          status: next.status || undefined,
          generated: triToBool(next.generated),
          delivered: triToBool(next.delivered),
          sort: order,
          limit: PAGE_SIZE,
          offset: page,
        })
        .then((response) => {
          setData(response);
          setError("");
        })
        .catch((e) => setError((e as Error).message))
        .finally(() => setLoading(false));
    },
    [],
  );

  useEffect(() => {
    load(filters, sort, offset);
  }, [filters, sort, offset, load]);

  const apply = (next: Filters) => {
    // Смена условия возвращает на первую страницу: иначе открылась бы страница,
    // которой в новой выборке нет.
    setOffset(0);
    setFilters(next);
  };

  const activeCount =
    (filters.search ? 1 : 0) +
    (filters.status ? 1 : 0) +
    (filters.generated ? 1 : 0) +
    (filters.delivered ? 1 : 0);

  const remove = async () => {
    if (!pending) return;
    const label = pending.display_number || pending.profile_id;
    setDeleting(true);
    try {
      await api.deleteProfile(pending.profile_id);
      setPending(null);
      setNotice(`Анкета ${label} удалена`);
      window.setTimeout(() => setNotice(""), 6000);
      load(filters, sort, offset);
    } catch (e) {
      const apiError = e as ApiError;
      const blockers = apiError.blockers?.map((b) => b.detail).join("; ");
      setError(blockers ? `${apiError.message}. ${blockers}` : apiError.message);
      setPending(null);
    } finally {
      setDeleting(false);
    }
  };

  const columns: ReadonlyArray<DataColumn<ProfileListItem>> = [
    {
      key: "who",
      header: "Кто",
      render: (item) => (
        <>
          <Link href={`/profiles/${item.profile_id}`}>
            {item.name || "без имени"}
          </Link>
          <div className="muted" style={{ fontSize: 12 }}>
            {item.display_number || item.profile_id}
          </div>
        </>
      ),
    },
    {
      key: "age",
      header: "Возраст",
      numeric: true,
      render: (item) => item.age ?? "—",
    },
    {
      key: "goal",
      header: "Цель",
      render: (item) =>
        item.primary_goal ? questionnaireLabel(item.primary_goal) : "—",
    },
    {
      key: "status",
      header: "Состояние",
      render: (item) => (
        <Status tone={statusTone(item.status)}>{statusLabel(item.status)}</Status>
      ),
    },
    {
      key: "program",
      header: "Программа",
      render: (item) =>
        item.has_program ? (
          <Tag tone="ok">собрана</Tag>
        ) : (
          <span className="muted">нет</span>
        ),
    },
    {
      key: "delivered",
      header: "Отправлена",
      hint: "Программа ушла в Telegram. Открыл ли её человек, Telegram не сообщает.",
      render: (item) =>
        item.delivered ? (
          <>
            <Tag tone="ok">отправлена</Tag>
            <div className="field-hint">{moment(item.delivered_at)}</div>
          </>
        ) : (
          <span className="muted">нет</span>
        ),
    },
    {
      key: "created",
      header: "Создана",
      render: (item) => <span className="muted">{moment(item.created_at)}</span>,
    },
    {
      key: "actions",
      header: "Действия",
      hidden: !canWrite,
      render: (item) => (
        <button
          type="button"
          className="small danger"
          onClick={() => setPending(item)}
          // Кнопка не блокируется по has_program: администратор должен увидеть,
          // что именно мешает, а не гадать, почему нельзя.
          title={
            item.has_program ? "Сначала удалите программы этой анкеты" : undefined
          }
        >
          Удалить
        </button>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Анкеты"
        description="Что человек рассказал о себе боту: цель, опыт, ограничения. На основе анкеты собирается программа тренировок."
      />

      {notice && <Notice tone="ok">{notice}</Notice>}

      <Card title="Отбор анкет">
        <FilterBar
          onApply={() => apply({ ...filters, search: draftSearch })}
          onReset={() => {
            setDraftSearch("");
            apply(EMPTY);
          }}
          activeCount={activeCount}
        >
          <Field
            label="Поиск"
            hint="Имя, номер анкеты или её идентификатор."
            htmlFor="profiles-search"
          >
            <SearchInput
              id="profiles-search"
              placeholder="Например: Иван или 1042"
              value={draftSearch}
              onChange={setDraftSearch}
              onSubmit={() => apply({ ...filters, search: draftSearch })}
            />
          </Field>
          <Field
            label="Состояние анкеты"
            hint="Черновик — человек ещё отвечает на вопросы."
            htmlFor="profiles-status"
          >
            <select
              id="profiles-status"
              value={filters.status}
              onChange={(event) =>
                apply({ ...filters, status: event.target.value })
              }
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
              value={filters.generated}
              onChange={(event) =>
                apply({ ...filters, generated: event.target.value as TriState })
              }
            >
              <option value="">Неважно</option>
              <option value="yes">Собрана</option>
              <option value="no">Не собрана</option>
            </select>
          </Field>
          <Field
            label="Отправлена человеку"
            hint="Программа ушла в Telegram."
            htmlFor="profiles-delivered"
          >
            <select
              id="profiles-delivered"
              value={filters.delivered}
              onChange={(event) =>
                apply({ ...filters, delivered: event.target.value as TriState })
              }
            >
              <option value="">Неважно</option>
              <option value="yes">Отправлена</option>
              <option value="no">Не отправлена</option>
            </select>
          </Field>
        </FilterBar>
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
              onChange={(event) => {
                setOffset(0);
                setSort(event.target.value as ProfileSort);
              }}
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
        <DataTable
          columns={columns}
          rows={data?.items ?? []}
          rowKey={(item) => item.profile_id}
          loading={loading}
          error={error}
          emptyTitle={activeCount > 0 ? "Ничего не нашлось" : "Анкет пока нет"}
          emptyHint={
            activeCount > 0
              ? "Попробуйте изменить условия поиска или сбросить фильтры."
              : "Анкета появляется, когда человек проходит опрос в боте."
          }
          pagination={
            data
              ? {
                  total: data.total,
                  // Сервер в этом ответе не повторяет границы страницы, поэтому
                  // они берутся из запроса, который отправил сам клиент.
                  limit: PAGE_SIZE,
                  offset,
                  onChange: setOffset,
                }
              : undefined
          }
        />
      </Card>

      {pending && (
        <ConfirmDialog
          title={`Удалить анкету ${pending.display_number || pending.profile_id}?`}
          description="Ответы человека и записи об отправке будут удалены безвозвратно. Если по анкете есть программы, сервер откажет: сначала удалите их."
          confirmLabel="Удалить анкету"
          danger
          busy={deleting}
          onConfirm={remove}
          onCancel={() => setPending(null)}
        />
      )}
    </>
  );
}
