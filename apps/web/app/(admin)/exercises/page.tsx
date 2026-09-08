"use client";

// Каталог упражнений — главный рабочий инструмент базы знаний.
//
// Фильтры, сортировка и пагинация серверные: из 873 упражнений «первые 50 по
// алфавиту» и «первые 50 сложных» — разные выборки, и отсеивать строки после
// выборки страницы нельзя. Числа рядом со значениями считаются по текущей
// выборке, поэтому не обещают результатов, которых после уточнения нет.
//
// Панель отбора разделена на три уровня по частоте использования: строка поиска
// и состояние всегда видны, признаки каталога и знание об оборудовании
// раскрываются. Раньше все девять групп фильтров стояли развёрнутыми, и список —
// то, за чем открывают страницу, — начинался за сгибом экрана.
//
// Столбец «Оборудование» показывает два разных факта, и они не дублируют друг
// друга: значение справочника — то, что написано в источнике каталога, а
// требования — нормализованное знание системы. Расхождение видно сразу, и это
// нужно: пока требования не заполнены, подбор по оборудованию не работает.

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
import { DataColumn, DataTable } from "@/components/ui/DataTable";
import { FacetFilter } from "@/components/ui/FacetFilter";
import { FilterBar, FilterGroup, SearchInput } from "@/components/ui/FilterBar";
import {
  Card,
  Field,
  Notice,
  Status,
  Tag,
} from "@/components/ui/Primitives";
import {
  ActiveFilter,
  CompatibilityStatus,
  EquipmentCapability,
  EquipmentItem,
  EquipmentKnowledgeFilter,
  ExerciseListItem,
  ExerciseListResponse,
  ExerciseSort,
  FacetCount,
  MediaFilter,
  RequirementKindFilter,
  SortOrder,
  api,
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
  count,
  equipmentList,
  muscleList,
} from "@/lib/labels";

const PAGE_SIZE = 50;

// Словарь читается целиком: он на порядок меньше каталога, а фильтры по
// оборудованию и возможностям нужны сразу, без второго запроса на каждый выбор.
const VOCABULARY_LIMIT = 200;

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

const CATALOG_FILTER_KEYS: ListFilterKey[] = [
  "equipment",
  "primary_muscle",
  "exercise_type",
  "difficulty",
  "mechanic",
  "force",
];

export default function ExercisesPage() {
  const [data, setData] = useState<ExerciseListResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [equipment, setEquipment] = useState<EquipmentItem[]>([]);
  const [capabilities, setCapabilities] = useState<EquipmentCapability[]>([]);

  // Черновик поиска и применённые фильтры разделены: запрос на каждое нажатие
  // клавиши бил бы по базе на каждый символ. Признаки со счётчиками
  // применяются сразу — там выбор однократный и число сразу видно.
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

  const facets = data?.facets;

  const catalogCount = CATALOG_FILTER_KEYS.reduce(
    (sum, key) => sum + filters[key].length,
    0,
  );
  const knowledgeCount =
    filters.equipment_id.length +
    filters.capability.length +
    filters.available_equipment.length +
    filters.compatibility.length +
    (filters.requirement_kind !== "any" ? 1 : 0) +
    (filters.equipment_knowledge !== "all" ? 1 : 0) +
    (filters.assume_unlisted_unavailable ? 1 : 0);
  const activeCount =
    catalogCount +
    knowledgeCount +
    (filters.search ? 1 : 0) +
    (filters.is_active !== "active" ? 1 : 0) +
    (filters.media !== "all" ? 1 : 0);

  const checkingCompatibility =
    filters.available_equipment.length > 0 ||
    filters.assume_unlisted_unavailable;

  const columns: ReadonlyArray<DataColumn<ExerciseListItem>> = [
    {
      key: "name",
      header: "Название",
      sortKey: "name_ru",
      render: (item) => (
        <>
          <Link href={`/exercises/${item.id}`}>
            {item.name_ru || item.name}
          </Link>
          {item.name_ru && (
            <div className="muted" style={{ fontSize: 12 }}>
              {item.name}
            </div>
          )}
        </>
      ),
    },
    {
      key: "equipment",
      header: "Оборудование",
      hint: "Значение источника каталога. Требования базы знаний — в карточке упражнения.",
      render: (item) => equipmentList(item.equipment),
    },
    {
      key: "muscles",
      header: "Основные мышцы",
      render: (item) => muscleList(item.primary_muscles),
    },
    {
      key: "difficulty",
      header: "Сложность",
      sortKey: "difficulty",
      render: (item) =>
        item.difficulty
          ? DIFFICULTY_LABELS[item.difficulty] ?? item.difficulty
          : "—",
    },
    {
      key: "mechanic",
      header: "Работа мышц",
      sortKey: "mechanic",
      render: (item) =>
        item.mechanic ? MECHANIC_LABELS[item.mechanic] ?? item.mechanic : "—",
    },
    {
      key: "compatibility",
      header: "Совместимость",
      hidden: !checkingCompatibility,
      hint: "Считается по выбранному доступному оборудованию для показанной страницы.",
      render: (item) =>
        item.compatibility ? (
          <>
            <Status tone={compatibilityTone(item.compatibility.status)}>
              {COMPATIBILITY_LABELS[item.compatibility.status] ??
                item.compatibility.status}
            </Status>
            <div className="muted" style={{ fontSize: 12 }}>
              {COMPATIBILITY_REASON_LABELS[item.compatibility.reason] ??
                item.compatibility.reason}
            </div>
            {item.compatibility.missing.length > 0 && (
              <div className="inline-list" style={{ gap: 4, marginTop: 4 }}>
                {item.compatibility.missing.map((missing) => (
                  <Tag key={missing} tone="bad">
                    {equipmentLabels[missing] ?? missing}
                  </Tag>
                ))}
              </div>
            )}
          </>
        ) : (
          <span className="muted">не проверялось</span>
        ),
    },
    {
      key: "media",
      header: "Фото",
      render: (item) =>
        item.has_media ? "есть" : <span className="muted">нет</span>,
    },
    {
      key: "active",
      header: "В программах",
      render: (item) => (
        <Status tone={item.is_active ? "ok" : "neutral"}>
          {item.is_active ? "используется" : "выключено"}
        </Status>
      ),
    },
  ];

  const resetAll = () => {
    setDraftSearch("");
    apply(EMPTY);
  };

  return (
    <>
      <PageHeader
        title="Упражнения"
        description="Каталог, из которого собираются программы. Числа рядом со значениями показывают, сколько упражнений останется при таком фильтре. Выключенное упражнение остаётся в справочнике, но в новые программы не попадает."
      />

      <Card title="Отбор упражнений">
        <FilterBar
          onApply={() => apply({ ...filters, search: draftSearch })}
          onReset={resetAll}
          activeCount={activeCount}
          extra={
            <>
              {facets && (
                <FilterGroup
                  title="Признаки каталога"
                  hint="Числа — сколько упражнений останется в текущей выборке при выборе значения."
                  activeCount={catalogCount}
                >
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
                </FilterGroup>
              )}

              <FilterGroup
                title="Оборудование и совместимость"
                hint="Фильтры базы знаний. Числа здесь — сколько упражнений связано с записью словаря во всём каталоге, а не в текущей выборке. Несколько условий соединяются «и»."
                activeCount={knowledgeCount}
              >
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
                  hint="Применяется к выбору оборудования базы знаний."
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
                          onChange={() =>
                            apply({
                              ...filters,
                              compatibility: filters.compatibility.includes(
                                status,
                              )
                                ? filters.compatibility.filter(
                                    (item) => item !== status,
                                  )
                                : [...filters.compatibility, status],
                            })
                          }
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
                        Иначе неотмеченное оборудование считается неизвестным, а
                        не отсутствующим: «неизвестно» ≠ «нет».
                      </span>
                    </span>
                  </label>
                </Field>
              </FilterGroup>
            </>
          }
        >
          <Field
            label="Поиск"
            hint="По русскому и английскому названию, а также по коду упражнения."
            htmlFor="ex-search"
          >
            <SearchInput
              id="ex-search"
              placeholder="Например: приседания"
              value={draftSearch}
              onChange={setDraftSearch}
              onSubmit={() => apply({ ...filters, search: draftSearch })}
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
                apply({ ...filters, media: event.target.value as MediaFilter })
              }
            >
              <option value="all">Не важно</option>
              <option value="with">Есть фотографии</option>
              <option value="without">Без фотографий</option>
            </select>
          </Field>
        </FilterBar>
      </Card>

      <Card
        title="Список упражнений"
        description={
          data
            ? `Под фильтр попало ${count(data.total)} упражнений`
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

        <DataTable
          columns={columns}
          rows={data?.items ?? []}
          rowKey={(item) => String(item.id)}
          loading={loading}
          error={error}
          sort={{
            by: sortBy,
            order,
            onChange: (by, direction) => {
              setOffset(0);
              setSortBy(by as ExerciseSort);
              setOrder(direction);
            },
          }}
          emptyTitle={activeCount > 0 ? "Ничего не нашлось" : "Каталог пуст"}
          emptyHint={
            activeCount > 0
              ? "Условия слишком узкие. Снимите часть фильтров: числа рядом со значениями показывают, сколько упражнений останется."
              : "Пока не загружен ни один справочник упражнений."
          }
          emptyAction={
            activeCount > 0 ? (
              <button type="button" onClick={resetAll}>
                Сбросить фильтры
              </button>
            ) : undefined
          }
          pagination={
            data
              ? {
                  total: data.total,
                  limit: data.limit,
                  offset: data.offset,
                  onChange: setOffset,
                }
              : undefined
          }
        />
      </Card>
    </>
  );
}
