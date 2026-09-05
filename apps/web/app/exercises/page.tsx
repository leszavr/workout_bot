"use client";

// Каталог упражнений.
//
// Раньше страница запрашивала первые 100 упражнений и фильтровала их сервером
// по одному значению каждого признака. Из 873 упражнений было видно 100, и
// «найдено: 100» означало не результат поиска, а предел запроса: остальные 773
// не существовали для интерфейса. Фильтры, сортировка и пагинация теперь
// серверные, а счётчики значений считаются по текущей выборке.
//
// Фильтры базы знаний (оборудование словаря, возможности, состояние знания)
// тоже серверные: они сводятся к набору идентификаторов и попадают в тот же
// SQL-запрос. Отсеивать строки после выборки страницы нельзя — «первые 50»
// перестали бы быть первыми пятьюдесятью подходящими.
//
// Столбец «Оборудование» показывает два разных факта, и они не дублируют друг
// друга: значение справочника — то, что написано в источнике каталога, а
// требования — нормализованное знание системы. Расхождение между ними видно
// сразу, и это нужно: пока требования не заполнены, подбор по оборудованию для
// упражнения не работает.

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import AppNav from "@/components/AppNav";
import { FacetFilter } from "@/components/ui/FacetFilter";
import { Pagination } from "@/components/ui/Pagination";
import {
  Card,
  Empty,
  Field,
  Notice,
  Skeleton,
  Status,
  Tag,
} from "@/components/ui/Primitives";
import {
  ActiveFilter,
  CompatibilityStatus,
  EquipmentCapability,
  EquipmentItem,
  EquipmentKnowledgeFilter,
  ExerciseListResponse,
  ExerciseSort,
  FacetCount,
  MediaFilter,
  RequirementKindFilter,
  SortOrder,
  api,
  getToken,
  knowledgeApi,
} from "@/lib/api";
import {
  COMPATIBILITY_LABELS,
  COMPATIBILITY_REASON_LABELS,
  DIFFICULTY_LABELS,
  EQUIPMENT_CATEGORY_LABELS,
  EQUIPMENT_LABELS,
  EXERCISE_TYPE_LABELS,
  FORCE_LABELS,
  MECHANIC_LABELS,
  MUSCLE_LABELS,
  compatibilityTone,
  equipmentList,
  muscleList,
} from "@/lib/labels";

const PAGE_SIZE = 50;

// Словарь читается целиком: он на порядок меньше каталога, а фильтры по
// оборудованию и возможностям нужны сразу, без второго запроса на каждый выбор.
const VOCABULARY_LIMIT = 200;

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

const COMPATIBILITY_OPTIONS: CompatibilityStatus[] = [
  "compatible",
  "incompatible",
  "unknown",
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
  equipment_id: string[];
  capability: string[];
  requirement_kind: RequirementKindFilter;
  equipment_knowledge: EquipmentKnowledgeFilter;
  available_equipment: string[];
  assume_unlisted_unavailable: boolean;
  compatibility: CompatibilityStatus[];
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
  equipment_id: [],
  capability: [],
  requirement_kind: "any",
  equipment_knowledge: "all",
  available_equipment: [],
  assume_unlisted_unavailable: false,
  compatibility: [],
  is_active: "active",
  media: "all",
};

type ListFilterKey =
  | "exercise_type"
  | "difficulty"
  | "equipment"
  | "primary_muscle"
  | "force"
  | "mechanic"
  | "equipment_id"
  | "capability"
  | "available_equipment";

export default function ExercisesPage() {
  const [data, setData] = useState<ExerciseListResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [equipment, setEquipment] = useState<EquipmentItem[]>([]);
  const [capabilities, setCapabilities] = useState<EquipmentCapability[]>([]);

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
          equipment_id: next.equipment_id,
          capability: next.capability,
          requirement_kind: next.requirement_kind,
          equipment_knowledge: next.equipment_knowledge,
          available_equipment: next.available_equipment,
          assume_unlisted_unavailable: next.assume_unlisted_unavailable,
          compatibility: next.compatibility,
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

  useEffect(() => {
    // Словарь и возможности читаются один раз: они не зависят от фильтров
    // каталога и меняются только вместе с базой знаний.
    knowledgeApi
      .equipment({ limit: VOCABULARY_LIMIT, usage: "all" })
      .then((response) => setEquipment(response.items))
      .catch(() => undefined);
    knowledgeApi
      .capabilities()
      .then((response) => setCapabilities(response.items))
      .catch(() => undefined);
  }, []);

  const equipmentLabels = useMemo(() => {
    const map: Record<string, string> = {};
    for (const item of equipment) {
      const category =
        EQUIPMENT_CATEGORY_LABELS[item.category] ?? item.category;
      map[item.equipment_id] = `${item.name_ru} · ${category}`;
    }
    return map;
  }, [equipment]);

  const capabilityLabels = useMemo(() => {
    const map: Record<string, string> = {};
    for (const capability of capabilities) {
      map[capability.capability_id] = capability.name_ru;
    }
    return map;
  }, [capabilities]);

  // Счётчик для словарных фильтров — число упражнений, связанных с записью.
  // Он приходит из базы знаний и не зависит от текущего фильтра каталога,
  // поэтому подписан иначе, чем facet-счётчики каталога.
  const equipmentOptions: FacetCount[] = useMemo(
    () =>
      equipment.map((item) => ({
        value: item.equipment_id,
        count: item.exercise_count ?? 0,
      })),
    [equipment],
  );

  const capabilityOptions: FacetCount[] = useMemo(
    () => capabilities.map((c) => ({ value: c.capability_id, count: 1 })),
    [capabilities],
  );

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

  const toggleCompatibility = (value: CompatibilityStatus) => {
    apply({
      ...filters,
      compatibility: filters.compatibility.includes(value)
        ? filters.compatibility.filter((item) => item !== value)
        : [...filters.compatibility, value],
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
      "equipment_id",
      "capability",
      "available_equipment",
    ];
    return (
      lists.reduce((sum, key) => sum + filters[key].length, 0) +
      filters.compatibility.length +
      (filters.search ? 1 : 0) +
      (filters.is_active !== "active" ? 1 : 0) +
      (filters.media !== "all" ? 1 : 0) +
      (filters.requirement_kind !== "any" ? 1 : 0) +
      (filters.equipment_knowledge !== "all" ? 1 : 0) +
      (filters.assume_unlisted_unavailable ? 1 : 0)
    );
  }, [filters]);

  const checkingCompatibility =
    filters.available_equipment.length > 0 ||
    filters.assume_unlisted_unavailable;

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
            <Field
              label="Требования к оборудованию"
              hint="«Не заполнены» — упражнения, по которым подбор с учётом оборудования пока не работает."
              htmlFor="ex-knowledge"
            >
              <select
                id="ex-knowledge"
                value={filters.equipment_knowledge}
                onChange={(event) =>
                  apply({
                    ...filters,
                    equipment_knowledge: event.target
                      .value as EquipmentKnowledgeFilter,
                  })
                }
              >
                <option value="all">Не важно</option>
                <option value="known">Заполнены</option>
                <option value="unknown">Не заполнены</option>
              </select>
            </Field>
            <Field
              label="Характер требования"
              hint="Применяется к выбору оборудования справа."
              htmlFor="ex-requirement"
            >
              <select
                id="ex-requirement"
                value={filters.requirement_kind}
                onChange={(event) =>
                  apply({
                    ...filters,
                    requirement_kind: event.target
                      .value as RequirementKindFilter,
                  })
                }
              >
                <option value="any">Любое</option>
                <option value="required">Обязательное</option>
                <option value="optional">Желательное</option>
                <option value="alternative">Одно из вариантов</option>
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
                  label="Оборудование (справочник)"
                  hint="Значение из источника каталога, как оно записано в справочнике упражнений."
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

          <div className="subcard">
            <p className="field-hint" style={{ marginTop: 0 }}>
              Фильтры базы знаний. Числа здесь — сколько упражнений связано с
              записью словаря во всём каталоге, а не в текущей выборке.
              Несколько условий соединяются «и».
            </p>
            <div className="form-grid">
              <FacetFilter
                label="Оборудование (база знаний)"
                hint="Нормализованная запись словаря. Отличается от значения справочника: словарь различает жим ногами и блочную тягу, справочник обе называет «тренажёр»."
                options={equipmentOptions}
                selected={filters.equipment_id}
                onToggle={(value) => toggle("equipment_id", value)}
                labelFor={(value) => equipmentLabels[value] ?? value}
                maxVisible={8}
              />
              <FacetFilter
                label="Возможности"
                hint="Что должно уметь оборудование. Находит требования, заданные и напрямую возможностью, и через конкретный тренажёр."
                options={capabilityOptions}
                selected={filters.capability}
                onToggle={(value) => toggle("capability", value)}
                labelFor={(value) => capabilityLabels[value] ?? value}
                maxVisible={8}
              />
              <FacetFilter
                label="Доступное оборудование"
                hint="Что есть «на руках». По этому набору считается совместимость показанных упражнений."
                options={equipmentOptions}
                selected={filters.available_equipment}
                onToggle={(value) => toggle("available_equipment", value)}
                labelFor={(value) => equipmentLabels[value] ?? value}
                maxVisible={8}
              />
              <Field
                label="Совместимость"
                hint="Работает только вместе с доступным оборудованием: без него статус не вычисляется."
              >
                <div className="pick-list" style={{ maxHeight: 140 }}>
                  {COMPATIBILITY_OPTIONS.map((status) => (
                    <label className="pick-list-item" key={status}>
                      <input
                        type="checkbox"
                        checked={filters.compatibility.includes(status)}
                        onChange={() => toggleCompatibility(status)}
                        disabled={!checkingCompatibility}
                      />
                      <span className="pick-list-text">
                        <span>{COMPATIBILITY_LABELS[status]}</span>
                      </span>
                    </label>
                  ))}
                </div>
                <label
                  className="pick-list-item"
                  style={{ marginTop: "var(--s-2)" }}
                >
                  <input
                    type="checkbox"
                    checked={filters.assume_unlisted_unavailable}
                    onChange={() =>
                      apply({
                        ...filters,
                        assume_unlisted_unavailable:
                          !filters.assume_unlisted_unavailable,
                      })
                    }
                  />
                  <span className="pick-list-text">
                    <span>Считать неотмеченное отсутствующим</span>
                    <span className="muted" style={{ fontSize: 12 }}>
                      Иначе неотмеченное оборудование считается неизвестным, а не
                      отсутствующим.
                    </span>
                  </span>
                </label>
              </Field>
            </div>
          </div>
        </Card>

        <Card
          title="Список упражнений"
          description={
            data
              ? `Под фильтр попало ${data.total.toLocaleString("ru-RU")} упражнений`
              : undefined
          }
        >
          {data?.filtered_page_count !== undefined && (
            <Notice tone="info">
              Статус совместимости считается для показанной страницы, поэтому
              фильтр по нему применён к этим {data.limit} строкам: осталось{" "}
              {data.filtered_page_count}. Общее число выше относится к выборке до
              фильтра по совместимости.
            </Notice>
          )}

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
                      {checkingCompatibility && <th>Совместимость</th>}
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
                        {checkingCompatibility && (
                          <td>
                            {exercise.compatibility ? (
                              <>
                                <Status
                                  tone={compatibilityTone(
                                    exercise.compatibility.status,
                                  )}
                                >
                                  {COMPATIBILITY_LABELS[
                                    exercise.compatibility.status
                                  ] ?? exercise.compatibility.status}
                                </Status>
                                <div
                                  className="muted"
                                  style={{ fontSize: 12 }}
                                >
                                  {COMPATIBILITY_REASON_LABELS[
                                    exercise.compatibility.reason
                                  ] ?? exercise.compatibility.reason}
                                </div>
                                {exercise.compatibility.missing.length > 0 && (
                                  <div
                                    className="field-row"
                                    style={{ flexWrap: "wrap", gap: 4 }}
                                  >
                                    {exercise.compatibility.missing.map(
                                      (item) => (
                                        <Tag key={item} tone="bad">
                                          {equipmentLabels[item] ?? item}
                                        </Tag>
                                      ),
                                    )}
                                  </div>
                                )}
                              </>
                            ) : (
                              <span className="muted">не проверялось</span>
                            )}
                          </td>
                        )}
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
