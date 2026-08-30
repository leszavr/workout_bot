"use client";

// Каталог упражнений.
//
// Раньше страница запрашивала первые 100 упражнений и фильтровала их сервером
// по одному значению каждого признака. Из 873 упражнений было видно 100, и
// «найдено: 100» означало не результат поиска, а предел запроса: остальные 773
// не существовали для интерфейса. Фильтры, сортировка и пагинация теперь
// серверные, а счётчики значений считаются по текущей выборке.

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import AppNav from "@/components/AppNav";
import { FacetFilter } from "@/components/ui/FacetFilter";
import { Pagination } from "@/components/ui/Pagination";
import { Card, Empty, Field, Skeleton, Status } from "@/components/ui/Primitives";
import {
  ActiveFilter,
  ExerciseListResponse,
  ExerciseSort,
  MediaFilter,
  SortOrder,
  api,
  getToken,
} from "@/lib/api";
import {
  DIFFICULTY_LABELS,
  EQUIPMENT_LABELS,
  EXERCISE_TYPE_LABELS,
  FORCE_LABELS,
  MECHANIC_LABELS,
  MUSCLE_LABELS,
  equipmentList,
  muscleList,
} from "@/lib/labels";

const PAGE_SIZE = 50;

// Сортировки, осмысленные для каталога. Сложность сортируется по смыслу
// (начальный → продвинутый), а не по алфавиту: это делает сервер.
const SORTS: Array<{ value: ExerciseSort; label: string }> = [
  { value: "name", label: "Английское название" },
  { value: "name_ru", label: "Русское название" },
  { value: "difficulty", label: "Сложность" },
  { value: "exercise_type", label: "Вид нагрузки" },
  { value: "mechanic", label: "Работа мышц" },
  { value: "force", label: "Характер усилия" },
];

function labelFrom(dictionary: Record<string, string>) {
  return (value: string) => dictionary[value] ?? value;
}

interface Filters {
  search: string;
  exercise_type: string[];
  difficulty: string[];
  equipment: string[];
  primary_muscle: string[];
  force: string[];
  mechanic: string[];
  is_active: ActiveFilter;
  media: MediaFilter;
}

const EMPTY: Filters = {
  search: "",
  exercise_type: [],
  difficulty: [],
  equipment: [],
  primary_muscle: [],
  force: [],
  mechanic: [],
  is_active: "active",
  media: "all",
};

type ListFilterKey =
  | "exercise_type"
  | "difficulty"
  | "equipment"
  | "primary_muscle"
  | "force"
  | "mechanic";

export default function ExercisesPage() {
  const [data, setData] = useState<ExerciseListResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  // Черновик фильтров и применённые фильтры разделены: запрос на каждое
  // нажатие клавиши в поиске бил бы по базе на каждый символ. Признаки со
  // счётчиками применяются сразу — там выбор однократный и число сразу видно.
  const [draftSearch, setDraftSearch] = useState("");
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [sortBy, setSortBy] = useState<ExerciseSort>("name");
  const [order, setOrder] = useState<SortOrder>("asc");
  const [offset, setOffset] = useState(0);

  const load = useCallback(
    (next: Filters, page: number, sort: ExerciseSort, direction: SortOrder) => {
      setLoading(true);
      api
        .exercises({
          search: next.search || undefined,
          exercise_type: next.exercise_type,
          difficulty: next.difficulty,
          equipment: next.equipment,
          primary_muscle: next.primary_muscle,
          force: next.force,
          mechanic: next.mechanic,
          is_active: next.is_active,
          media: next.media,
          sort_by: sort,
          order: direction,
          limit: PAGE_SIZE,
          offset: page,
          with_facets: true,
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
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    load(filters, offset, sortBy, order);
  }, [filters, offset, sortBy, order, load]);

  const apply = (next: Filters) => {
    // Смена фильтра возвращает на первую страницу: иначе после уточнения
    // фильтра открывалась бы страница, которой в новой выборке нет.
    setOffset(0);
    setFilters(next);
  };

  const toggle = (key: ListFilterKey, value: string) => {
    const current = filters[key];
    apply({
      ...filters,
      [key]: current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value],
    });
  };

  const facets = data?.facets;
  const activeCount = useMemo(() => {
    const lists: ListFilterKey[] = [
      "exercise_type",
      "difficulty",
      "equipment",
      "primary_muscle",
      "force",
      "mechanic",
    ];
    return (
      lists.reduce((sum, key) => sum + filters[key].length, 0) +
      (filters.search ? 1 : 0) +
      (filters.is_active !== "active" ? 1 : 0) +
      (filters.media !== "all" ? 1 : 0)
    );
  }, [filters]);

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <div className="page-head">
          <h1 className="page-title">Упражнения</h1>
          <p className="page-subtitle">
            Каталог, из которого собираются программы. Числа рядом со значениями
            показывают, сколько упражнений останется при таком фильтре.
            Выключенное упражнение остаётся в справочнике, но в новые программы
            не попадает.
          </p>
        </div>

        {error && <div className="error">{error}</div>}

        <Card title="Отбор упражнений">
          <div className="filters">
            <Field
              label="Поиск"
              hint="По русскому и английскому названию, а также по коду упражнения."
              htmlFor="ex-search"
            >
              <input
                id="ex-search"
                type="search"
                placeholder="Например: приседания"
                value={draftSearch}
                onChange={(event) => setDraftSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    apply({ ...filters, search: draftSearch });
                  }
                }}
              />
            </Field>
            <Field
              label="Состояние"
              hint="Отключённые упражнения не попадают в новые программы."
              htmlFor="ex-active"
            >
              <select
                id="ex-active"
                value={filters.is_active}
                onChange={(event) =>
                  apply({
                    ...filters,
                    is_active: event.target.value as ActiveFilter,
                  })
                }
              >
                <option value="active">Только используемые</option>
                <option value="inactive">Только выключенные</option>
                <option value="all">Все</option>
              </select>
            </Field>
            <Field
              label="Фотографии"
              hint="Упражнения без фотографий труднее выполнять по программе."
              htmlFor="ex-media"
            >
              <select
                id="ex-media"
                value={filters.media}
                onChange={(event) =>
                  apply({
                    ...filters,
                    media: event.target.value as MediaFilter,
                  })
                }
              >
                <option value="all">Не важно</option>
                <option value="with">Есть фотографии</option>
                <option value="without">Без фотографий</option>
              </select>
            </Field>
            <Field label="Сортировка" htmlFor="ex-sort">
              <select
                id="ex-sort"
                value={sortBy}
                onChange={(event) => {
                  setOffset(0);
                  setSortBy(event.target.value as ExerciseSort);
                }}
              >
                {SORTS.map((sort) => (
                  <option key={sort.value} value={sort.value}>
                    {sort.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Порядок" htmlFor="ex-order">
              <select
                id="ex-order"
                value={order}
                onChange={(event) => {
                  setOffset(0);
                  setOrder(event.target.value as SortOrder);
                }}
              >
                <option value="asc">По возрастанию</option>
                <option value="desc">По убыванию</option>
              </select>
            </Field>
            <div className="filters-actions">
              <button
                type="button"
                className="primary"
                onClick={() => apply({ ...filters, search: draftSearch })}
              >
                Показать
              </button>
              {activeCount > 0 && (
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    setDraftSearch("");
                    apply(EMPTY);
                  }}
                >
                  Сбросить ({activeCount})
                </button>
              )}
            </div>
          </div>

          {facets && (
            <div className="subcard">
              <div className="form-grid">
                <FacetFilter
                  label="Оборудование"
                  hint="Что нужно для выполнения."
                  options={facets.equipment}
                  selected={filters.equipment}
                  onToggle={(value) => toggle("equipment", value)}
                  labelFor={labelFrom(EQUIPMENT_LABELS)}
                />
                <FacetFilter
                  label="Основные мышцы"
                  hint="Какая группа работает основной."
                  options={facets.primary_muscles}
                  selected={filters.primary_muscle}
                  onToggle={(value) => toggle("primary_muscle", value)}
                  labelFor={labelFrom(MUSCLE_LABELS)}
                />
                <FacetFilter
                  label="Вид нагрузки"
                  options={facets.exercise_types}
                  selected={filters.exercise_type}
                  onToggle={(value) => toggle("exercise_type", value)}
                  labelFor={labelFrom(EXERCISE_TYPE_LABELS)}
                />
                <FacetFilter
                  label="Сложность"
                  options={facets.difficulties}
                  selected={filters.difficulty}
                  onToggle={(value) => toggle("difficulty", value)}
                  labelFor={labelFrom(DIFFICULTY_LABELS)}
                />
                <FacetFilter
                  label="Работа мышц"
                  hint="Базовое задействует несколько групп, изолирующее — одну."
                  options={facets.mechanics}
                  selected={filters.mechanic}
                  onToggle={(value) => toggle("mechanic", value)}
                  labelFor={labelFrom(MECHANIC_LABELS)}
                />
                <FacetFilter
                  label="Характер усилия"
                  options={facets.forces}
                  selected={filters.force}
                  onToggle={(value) => toggle("force", value)}
                  labelFor={labelFrom(FORCE_LABELS)}
                />
              </div>
            </div>
          )}
        </Card>

        <Card
          title="Список упражнений"
          description={
            data
              ? `Под фильтр попало ${data.total.toLocaleString("ru-RU")} упражнений`
              : undefined
          }
        >
          {loading && <Skeleton rows={6} />}

          {!loading && data && data.items.length === 0 && (
            <Empty
              title={activeCount > 0 ? "Ничего не нашлось" : "Каталог пуст"}
              hint={
                activeCount > 0
                  ? "Условия слишком узкие. Снимите часть фильтров: числа рядом со значениями показывают, сколько упражнений останется."
                  : "Пока не загружен ни один справочник упражнений."
              }
              action={
                activeCount > 0 ? (
                  <button
                    type="button"
                    onClick={() => {
                      setDraftSearch("");
                      apply(EMPTY);
                    }}
                  >
                    Сбросить фильтры
                  </button>
                ) : undefined
              }
            />
          )}

          {!loading && data && data.items.length > 0 && (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Название</th>
                      <th>Оборудование</th>
                      <th>Основные мышцы</th>
                      <th>Сложность</th>
                      <th>Фото</th>
                      <th>В программах</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((exercise) => (
                      <tr key={exercise.id}>
                        <td>
                          <Link href={`/exercises/${exercise.id}`}>
                            {exercise.name_ru || exercise.name}
                          </Link>
                          {exercise.name_ru && (
                            <div className="muted" style={{ fontSize: 12 }}>
                              {exercise.name}
                            </div>
                          )}
                        </td>
                        <td>{equipmentList(exercise.equipment)}</td>
                        <td>{muscleList(exercise.primary_muscles)}</td>
                        <td>
                          {exercise.difficulty
                            ? DIFFICULTY_LABELS[exercise.difficulty] ??
                              exercise.difficulty
                            : "—"}
                        </td>
                        <td>
                          {exercise.has_media ? (
                            "есть"
                          ) : (
                            <span className="muted">нет</span>
                          )}
                        </td>
                        <td>
                          <Status tone={exercise.is_active ? "ok" : "neutral"}>
                            {exercise.is_active ? "используется" : "выключено"}
                          </Status>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination
                total={data.total}
                limit={data.limit}
                offset={data.offset}
                onChange={setOffset}
                disabled={loading}
              />
            </>
          )}
        </Card>
      </main>
    </div>
  );
}
