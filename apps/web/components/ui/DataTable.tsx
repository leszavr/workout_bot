"use client";

// Единая таблица списков.
//
// До этого каждый раздел верстал `table-wrap > table > thead/tbody` сам: девять
// почти одинаковых реализаций, у каждой своя разметка «загрузка», «ничего не
// нашлось» и своя пагинация. Одинаковые списки выглядели по-разному, а
// исправление поведения приходилось повторять девять раз.
//
// Сортировка по клику на заголовок объявляется в описании столбца, а не
// разбросана по обработчикам страницы: столбец, по которому сортировать нельзя,
// не выглядит нажимаемым. Порядок хранит страница — он часть запроса к серверу,
// и таблица не должна знать, как этот запрос собирается.
//
// Состояния списка (загрузка, ошибка, пусто) обрабатываются здесь целиком:
// «пустая таблица» и «ошибка запроса» — разные факты, и показывать вместо
// ошибки пустой список нельзя.

import { ReactNode } from "react";

import { Pagination } from "@/components/ui/Pagination";
import { Empty, Skeleton } from "@/components/ui/Primitives";
import { SortOrder } from "@/lib/api";

export interface DataColumn<T> {
  /** Ключ столбца: должен быть уникален в таблице. */
  key: string;
  header: string;
  render: (item: T) => ReactNode;
  /** Значение сортировки для сервера. Без него заголовок не нажимается. */
  sortKey?: string;
  /** Числовой столбец: выравнивается по правому краю. */
  numeric?: boolean;
  /** Подсказка к заголовку: раскрывает, что именно посчитано. */
  hint?: string;
  /** Скрыть столбец: удобнее, чем собирать массив условиями. */
  hidden?: boolean;
}

export interface DataTableSort {
  by: string;
  order: SortOrder;
  onChange: (by: string, order: SortOrder) => void;
}

export interface DataTablePagination {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}

export function DataTable<T>(props: Readonly<{
  columns: ReadonlyArray<DataColumn<T>>;
  rows: readonly T[];
  rowKey: (item: T) => string;
  loading?: boolean;
  /** Сообщение об ошибке запроса: показывается вместо списка. */
  error?: string;
  emptyTitle: string;
  emptyHint?: string;
  emptyAction?: ReactNode;
  sort?: DataTableSort;
  pagination?: DataTablePagination;
  /** Итог под таблицей, когда пагинации нет. */
  footer?: ReactNode;
}>) {
  const columns = props.columns.filter((column) => !column.hidden);

  if (props.error) {
    return (
      <div className="error" role="alert">
        {props.error}
      </div>
    );
  }

  // Скелет вместо таблицы показывается только при первой загрузке: при смене
  // страницы старые строки остаются на месте, иначе список мигал бы на каждом
  // переходе. Признак — отсутствие строк.
  if (props.loading && props.rows.length === 0) {
    return <Skeleton rows={6} />;
  }

  if (props.rows.length === 0) {
    return (
      <Empty
        title={props.emptyTitle}
        hint={props.emptyHint}
        action={props.emptyAction}
      />
    );
  }

  return (
    <>
      <div className="table-wrap">
        <table className={props.loading ? "is-stale" : undefined}>
          <thead>
            <tr>
              {columns.map((column) => (
                <HeaderCell key={column.key} column={column} sort={props.sort} />
              ))}
            </tr>
          </thead>
          <tbody>
            {props.rows.map((item) => (
              <tr key={props.rowKey(item)}>
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={column.numeric ? "num" : undefined}
                  >
                    {column.render(item)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {props.pagination && (
        <Pagination
          total={props.pagination.total}
          limit={props.pagination.limit}
          offset={props.pagination.offset}
          onChange={props.pagination.onChange}
          disabled={props.loading}
        />
      )}
      {!props.pagination && props.footer}
    </>
  );
}

function HeaderCell<T>(props: Readonly<{
  column: DataColumn<T>;
  sort?: DataTableSort;
}>) {
  const { column, sort } = props;
  const className = column.numeric ? "num" : undefined;

  if (!column.sortKey || !sort) {
    return (
      <th className={className} title={column.hint}>
        {column.header}
      </th>
    );
  }

  const active = sort.by === column.sortKey;
  // Направление по умолчанию — по возрастанию: для первого нажатия это
  // единственный предсказуемый вариант. Повторное нажатие переворачивает.
  const nextOrder: SortOrder = active && sort.order === "asc" ? "desc" : "asc";

  return (
    <th className={className} title={column.hint} aria-sort={
      active ? (sort.order === "asc" ? "ascending" : "descending") : "none"
    }>
      <button
        type="button"
        className="th-sort"
        onClick={() => sort.onChange(column.sortKey as string, nextOrder)}
      >
        {column.header}
        <span className="th-sort-mark" aria-hidden="true">
          {active ? (sort.order === "asc" ? "↑" : "↓") : "↕"}
        </span>
      </button>
    </th>
  );
}
