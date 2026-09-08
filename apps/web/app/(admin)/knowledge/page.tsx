"use client";

// База знаний: словарь оборудования.
//
// Словарь — данные, а не код: добавление тренажёра здесь не требует изменения
// программы. Поэтому раздел позволяет создать запись, задать её возможности и
// синонимы, и увидеть, сколько упражнений на неё ссылается.
//
// Модель показана в разделе явно (упражнение → требование → оборудование →
// возможность): без неё различие «обязательно / желательно / одно из вариантов»
// и «несовместимо / неизвестно» приходилось выводить из поведения подбора.
//
// Удаление и деактивация — разные операции, и это не дублирование. Удалить можно
// только запись, на которую никто не ссылается; всё остальное выводится из
// обращения деактивацией, чтобы существующие требования упражнений остались
// историческим фактом. Сервер проверяет это независимо от интерфейса.

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataColumn, DataTable } from "@/components/ui/DataTable";
import { FacetFilter } from "@/components/ui/FacetFilter";
import { FilterBar, FilterGroup, SearchInput } from "@/components/ui/FilterBar";
import { KNOWLEDGE_TABS } from "@/components/knowledge/tabs";
import { PageTabs } from "@/components/ui/PageTabs";
import {
  Card,
  Field,
  Notice,
  Status,
  Tag,
} from "@/components/ui/Primitives";
import {
  ActiveFilter,
  ApiError,
  EquipmentCapability,
  EquipmentItem,
  FacetCount,
  knowledgeApi,
} from "@/lib/api";
import { EQUIPMENT_CATEGORY_LABELS, count } from "@/lib/labels";
import { useCurrentUser } from "@/lib/session";

const PAGE_SIZE = 50;

type UsageFilter = "used" | "unused" | "all";

interface Filters {
  search: string;
  category: string[];
  capability: string[];
  is_active: ActiveFilter;
  usage: UsageFilter;
}

const EMPTY: Filters = {
  search: "",
  category: [],
  capability: [],
  is_active: "active",
  usage: "all",
};

function categoryLabel(value: string): string {
  return EQUIPMENT_CATEGORY_LABELS[value] ?? value;
}

export default function KnowledgeEquipmentPage() {
  const { canWrite } = useCurrentUser();
  const [items, setItems] = useState<EquipmentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [categories, setCategories] = useState<FacetCount[]>([]);
  const [capabilities, setCapabilities] = useState<EquipmentCapability[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [draftSearch, setDraftSearch] = useState("");
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<EquipmentItem | null>(null);
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<EquipmentItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async (next: Filters, page: number) => {
    setLoading(true);
    try {
      const response = await knowledgeApi.equipment({
        search: next.search || undefined,
        category: next.category,
        capability: next.capability,
        is_active: next.is_active,
        usage: next.usage,
        limit: PAGE_SIZE,
        offset: page,
      });
      setItems(response.items);
      setTotal(response.total);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(filters, offset).catch(() => undefined);
  }, [filters, offset, load]);

  useEffect(() => {
    // Справочники читаются один раз: они меняются только вместе со словарём, и
    // перезапрашивать их на каждый фильтр незачем.
    knowledgeApi
      .categories()
      .then((response) => setCategories(response.items))
      .catch(() => undefined);
    knowledgeApi
      .capabilities()
      .then((response) => setCapabilities(response.items))
      .catch(() => undefined);
  }, []);

  const capabilityLabels = useMemo(() => {
    const map: Record<string, string> = {};
    for (const capability of capabilities) {
      map[capability.capability_id] = capability.name_ru;
    }
    return map;
  }, [capabilities]);

  const capabilityOptions: FacetCount[] = useMemo(
    () => capabilities.map((c) => ({ value: c.capability_id, count: 1 })),
    [capabilities],
  );

  const apply = (next: Filters) => {
    setOffset(0);
    setFilters(next);
  };

  const changed = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 6000);
    setEditing(null);
    setCreating(false);
    load(filters, offset).catch(() => undefined);
  };

  const deactivate = async (item: EquipmentItem) => {
    try {
      await knowledgeApi.deactivateEquipment(item.equipment_id);
      changed(`«${item.name_ru}» выключено`);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const remove = async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await knowledgeApi.deleteEquipment(pendingDelete.equipment_id);
      setPendingDelete(null);
      changed(`«${pendingDelete.name_ru}» удалено`);
    } catch (e) {
      const apiError = e as ApiError;
      setError(
        apiError.status === 409
          ? `Удалить нельзя: ${apiError.message}`
          : apiError.message,
      );
      setPendingDelete(null);
    } finally {
      setDeleting(false);
    }
  };

  const filterCount =
    filters.category.length +
    filters.capability.length +
    (filters.search ? 1 : 0) +
    (filters.is_active !== "active" ? 1 : 0) +
    (filters.usage !== "all" ? 1 : 0);

  const columns: ReadonlyArray<DataColumn<EquipmentItem>> = [
    {
      key: "name",
      header: "Название",
      render: (item) => (
        <>
          <div>{item.name_ru}</div>
          <div className="muted" style={{ fontSize: 12 }}>
            {item.name} · <code>{item.equipment_id}</code>
          </div>
          {item.manufacturer && (
            <div className="muted" style={{ fontSize: 12 }}>
              {item.manufacturer}
              {item.model_name ? ` ${item.model_name}` : ""}
            </div>
          )}
        </>
      ),
    },
    {
      key: "category",
      header: "Категория",
      render: (item) => categoryLabel(item.category),
    },
    {
      key: "capabilities",
      header: "Возможности",
      hint: "Что оборудование умеет. По возможностям упражнение подходит и другому тренажёру с тем же умением.",
      render: (item) => (
        <>
          {item.capabilities.length === 0 ? (
            <span className="muted">не заданы</span>
          ) : (
            <div className="inline-list" style={{ gap: 4 }}>
              {item.capabilities.map((capability) => (
                <Tag key={capability}>
                  {capabilityLabels[capability] ?? capability}
                </Tag>
              ))}
            </div>
          )}
          {item.specializes && (
            <div className="muted" style={{ fontSize: 12 }}>
              частный случай: <code>{item.specializes}</code>
            </div>
          )}
        </>
      ),
    },
    {
      key: "aliases",
      header: "Синонимы",
      numeric: true,
      hint: "Слова, по которым запись находится в свободном тексте анкеты.",
      render: (item) =>
        item.aliases.length === 0 ? (
          <span className="muted">—</span>
        ) : (
          <span title={item.aliases.map((alias) => alias.alias).join(", ")}>
            {item.aliases.length}
          </span>
        ),
    },
    {
      key: "exercises",
      header: "Упражнений",
      numeric: true,
      hint: "Сколько упражнений ссылается на запись требованием.",
      render: (item) => {
        const value = item.exercise_count ?? 0;
        return value > 0 ? (
          <Link href={`/exercises?equipment_id=${item.equipment_id}`}>
            {value}
          </Link>
        ) : (
          <span className="muted">0</span>
        );
      },
    },
    {
      key: "state",
      header: "Состояние",
      render: (item) => (
        <Status tone={item.is_active ? "ok" : "neutral"}>
          {item.is_active ? "используется" : "выключено"}
        </Status>
      ),
    },
    {
      key: "actions",
      header: "Действия",
      hidden: !canWrite,
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
          {item.is_active && (
            <button
              type="button"
              className="small ghost"
              onClick={() => deactivate(item)}
            >
              Выключить
            </button>
          )}
          {(item.exercise_count ?? 0) === 0 && (
            <button
              type="button"
              className="small danger"
              onClick={() => setPendingDelete(item)}
              title="Удалить можно только оборудование без связанных упражнений"
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
      <PageHeader
        title="Оборудование"
        description="Словарь, по которому система определяет, какие упражнения выполнимы. Записи и синонимы — это данные: добавление нового тренажёра не требует изменения программы."
        actions={
          canWrite && !creating && !editing ? (
            <button
              type="button"
              className="primary"
              onClick={() => {
                setEditing(null);
                setCreating(true);
              }}
            >
              Добавить оборудование
            </button>
          ) : undefined
        }
        tabs={
          <PageTabs
            tabs={KNOWLEDGE_TABS}
            root="/knowledge"
            label="Разделы базы знаний"
          />
        }
      />

      {notice && <Notice tone="ok">{notice}</Notice>}

      <KnowledgeModel />

      {creating && (
        <EquipmentForm
          capabilities={capabilities}
          categories={categories}
          generics={items}
          onCancel={() => setCreating(false)}
          onSaved={changed}
          onError={setError}
        />
      )}
      {editing && (
        <EquipmentForm
          item={editing}
          capabilities={capabilities}
          categories={categories}
          generics={items}
          onCancel={() => setEditing(null)}
          onSaved={changed}
          onError={setError}
        />
      )}

      <Card title="Отбор оборудования">
        <FilterBar
          onApply={() => apply({ ...filters, search: draftSearch })}
          onReset={() => {
            setDraftSearch("");
            apply(EMPTY);
          }}
          activeCount={filterCount}
          extra={
            <FilterGroup
              title="Категория и возможности"
              activeCount={filters.category.length + filters.capability.length}
            >
              <FacetFilter
                label="Категория"
                options={categories}
                selected={filters.category}
                onToggle={(value) =>
                  apply({
                    ...filters,
                    category: filters.category.includes(value)
                      ? filters.category.filter((item) => item !== value)
                      : [...filters.category, value],
                  })
                }
                labelFor={categoryLabel}
              />
              <FacetFilter
                label="Возможности"
                hint="Что оборудование умеет. Несколько условий соединяются «и»."
                options={capabilityOptions}
                selected={filters.capability}
                onToggle={(value) =>
                  apply({
                    ...filters,
                    capability: filters.capability.includes(value)
                      ? filters.capability.filter((item) => item !== value)
                      : [...filters.capability, value],
                  })
                }
                labelFor={(value) => capabilityLabels[value] ?? value}
                maxVisible={8}
              />
            </FilterGroup>
          }
        >
          <Field
            label="Поиск"
            hint="По названию, коду и синонимам."
            htmlFor="eq-search"
          >
            <SearchInput
              id="eq-search"
              placeholder="Например: скамья"
              value={draftSearch}
              onChange={setDraftSearch}
              onSubmit={() => apply({ ...filters, search: draftSearch })}
            />
          </Field>
          <Field
            label="Состояние"
            hint="Выключенное оборудование не предлагается, но остаётся в существующих требованиях."
            htmlFor="eq-active"
          >
            <select
              id="eq-active"
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
            label="Связь с упражнениями"
            hint="Запись без связей заведена, но нигде не применяется."
            htmlFor="eq-usage"
          >
            <select
              id="eq-usage"
              value={filters.usage}
              onChange={(event) =>
                apply({ ...filters, usage: event.target.value as UsageFilter })
              }
            >
              <option value="all">Не важно</option>
              <option value="used">Есть упражнения</option>
              <option value="unused">Нет упражнений</option>
            </select>
          </Field>
        </FilterBar>
      </Card>

      <Card
        title="Словарь оборудования"
        description={`Под фильтр попало ${count(total)} записей`}
      >
        <DataTable
          columns={columns}
          rows={items}
          rowKey={(item) => item.equipment_id}
          loading={loading}
          error={error}
          emptyTitle={filterCount > 0 ? "Ничего не нашлось" : "Словарь пуст"}
          emptyHint={
            filterCount > 0
              ? "Условия слишком узкие. Снимите часть фильтров."
              : "Начальный словарь поставляется миграцией. Если он пуст, миграции не применены."
          }
          pagination={{
            total,
            limit: PAGE_SIZE,
            offset,
            onChange: setOffset,
          }}
        />
      </Card>

      {pendingDelete && (
        <ConfirmDialog
          title={`Удалить «${pendingDelete.name_ru}»?`}
          description="Удаление возможно только для записи без связанных упражнений. Если связи появились, сервер откажет — выведите запись из обращения кнопкой «Выключить», чтобы существующие требования остались историческим фактом."
          confirmLabel="Удалить запись"
          danger
          busy={deleting}
          onConfirm={remove}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </>
  );
}

/**
 * Модель базы знаний.
 *
 * Показана в самом разделе, а не только в документации: администратор принимает
 * здесь решения («выключить или удалить», «задать возможность или конкретный
 * тренажёр»), и без модели эти решения делаются наугад. Особенно важна разница
 * «неизвестно» и «несовместимо»: первое означает пробел в данных, второе —
 * установленный факт, и подбор ведёт себя по-разному.
 */
function KnowledgeModel() {
  return (
    <details className="filter-group" style={{ marginBottom: "var(--s-4)" }}>
      <summary>
        <span className="filter-group-title">
          Как устроено знание об оборудовании
        </span>
      </summary>
      <div className="model-chain">
        <div className="model-node">
          <div className="model-node-title">Упражнение</div>
          <p className="field-hint">Запись каталога, из которого собирается программа.</p>
        </div>
        <div className="model-arrow" aria-hidden="true">
          требование →
        </div>
        <div className="model-node">
          <div className="model-node-title">Оборудование</div>
          <p className="field-hint">
            Запись этого словаря. Может быть частным случаем родовой записи.
          </p>
        </div>
        <div className="model-arrow" aria-hidden="true">
          возможность →
        </div>
        <div className="model-node">
          <div className="model-node-title">Возможность</div>
          <p className="field-hint">
            Что оборудование умеет. Требование к возможности выполняется любым
            оборудованием, которое её даёт.
          </p>
        </div>
      </div>

      <div className="form-grid" style={{ marginTop: "var(--s-3)" }}>
        <div>
          <div className="field-label">Характер требования</div>
          <div className="stack" style={{ marginTop: "var(--s-2)" }}>
            <div>
              <Tag tone="bad">обязательное</Tag>{" "}
              <span className="field-hint">
                без него упражнение выполнить нельзя
              </span>
            </div>
            <div>
              <Tag tone="warn">желательное</Tag>{" "}
              <span className="field-hint">
                упражнение выполнимо и без него
              </span>
            </div>
            <div>
              <Tag tone="info">одно из вариантов</Tag>{" "}
              <span className="field-hint">
                достаточно любого элемента группы
              </span>
            </div>
          </div>
        </div>
        <div>
          <div className="field-label">Результат проверки совместимости</div>
          <div className="stack" style={{ marginTop: "var(--s-2)" }}>
            <div>
              <Status tone="ok">совместимо</Status>{" "}
              <span className="field-hint">
                все обязательные требования закрыты
              </span>
            </div>
            <div>
              <Status tone="bad">несовместимо</Status>{" "}
              <span className="field-hint">
                установлено, что нужного оборудования нет
              </span>
            </div>
            <div>
              <Status tone="neutral">неизвестно</Status>{" "}
              <span className="field-hint">
                данных не хватает: это не «нет оборудования»
              </span>
            </div>
          </div>
          <p className="field-hint" style={{ marginTop: "var(--s-2)" }}>
            «Неизвестно» ≠ «несовместимо». Первое означает пробел в данных,
            второе — установленный факт; сводить их к одному нельзя, иначе
            система придумывала бы отсутствие тренажёра.
          </p>
        </div>
      </div>
    </details>
  );
}

function EquipmentForm(props: Readonly<{
  item?: EquipmentItem;
  capabilities: EquipmentCapability[];
  categories: FacetCount[];
  /** Записи, которые можно указать родовыми: показываются подсказкой. */
  generics: EquipmentItem[];
  onCancel: () => void;
  onSaved: (message: string) => void;
  onError: (message: string) => void;
}>) {
  const existing = props.item;
  const [equipmentId, setEquipmentId] = useState(existing?.equipment_id ?? "");
  const [name, setName] = useState(existing?.name ?? "");
  const [nameRu, setNameRu] = useState(existing?.name_ru ?? "");
  const [category, setCategory] = useState(existing?.category ?? "machine");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [manufacturer, setManufacturer] = useState(existing?.manufacturer ?? "");
  const [modelName, setModelName] = useState(existing?.model_name ?? "");
  const [selected, setSelected] = useState<string[]>(
    existing?.capabilities ?? [],
  );
  const [specializes, setSpecializes] = useState(existing?.specializes ?? "");
  // Синонимы редактируются текстом: по одному на строку, с необязательным
  // префиксом `stem:` для совпадения по основе слова. Отдельная таблица ввода
  // здесь была бы сложнее без выигрыша — синонимов у записи единицы.
  const [aliasText, setAliasText] = useState(
    (existing?.aliases ?? [])
      .map((a) => (a.match_mode === "stem" ? `stem:${a.alias}` : a.alias))
      .join("\n"),
  );
  const [busy, setBusy] = useState(false);

  const idValid = /^[a-z][a-z0-9_]*$/.test(equipmentId);
  const valid = idValid && name.trim() && nameRu.trim() && category.trim();

  const parseAliases = () =>
    aliasText
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) =>
        line.startsWith("stem:")
          ? { alias: line.slice(5).trim(), match_mode: "stem" as const }
          : { alias: line, match_mode: "exact" as const },
      )
      .filter((alias) => alias.alias.length > 0);

  const submit = async () => {
    setBusy(true);
    try {
      const payload = {
        name: name.trim(),
        name_ru: nameRu.trim(),
        category: category.trim(),
        description: description.trim() || null,
        capabilities: selected,
        aliases: parseAliases(),
        specializes: specializes.trim() || null,
        manufacturer: manufacturer.trim() || null,
        model_name: modelName.trim() || null,
        is_active: existing?.is_active ?? true,
      };
      if (existing) {
        await knowledgeApi.updateEquipment(existing.equipment_id, payload);
        props.onSaved(`«${payload.name_ru}» сохранено`);
      } else {
        await knowledgeApi.createEquipment({
          equipment_id: equipmentId,
          ...payload,
        });
        props.onSaved(`«${payload.name_ru}» добавлено`);
      }
    } catch (e) {
      props.onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title={
        existing ? `Оборудование «${existing.name_ru}»` : "Новое оборудование"
      }
      description={
        existing
          ? "Код записи не меняется: на него ссылаются требования упражнений и профили."
          : "Код записи задаётся вручную и остаётся навсегда: на него ссылаются требования упражнений."
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
            label="Код"
            hint="Латиница в нижнем регистре, цифры и подчёркивание. Например: chest_press_machine."
            error={
              equipmentId.length > 0 && !idValid
                ? "Допустимы строчные латинские буквы, цифры и подчёркивание; первый символ — буква"
                : undefined
            }
          >
            <input
              type="text"
              value={equipmentId}
              onChange={(event) => setEquipmentId(event.target.value)}
              placeholder="chest_press_machine"
              autoComplete="off"
            />
          </Field>
        )}
        <Field label="Название (RU)">
          <input
            type="text"
            value={nameRu}
            onChange={(event) => setNameRu(event.target.value)}
            placeholder="Тренажёр жима от груди"
          />
        </Field>
        <Field label="Название (EN)">
          <input
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Chest press machine"
          />
        </Field>
        <Field label="Категория" hint="Группа в словаре. Можно ввести новую.">
          <input
            type="text"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            list="equipment-categories"
            placeholder="machine"
          />
          <datalist id="equipment-categories">
            {props.categories.map((item) => (
              <option key={item.value} value={item.value}>
                {categoryLabel(item.value)}
              </option>
            ))}
          </datalist>
        </Field>
        <Field label="Производитель" hint="Необязательно.">
          <input
            type="text"
            value={manufacturer}
            onChange={(event) => setManufacturer(event.target.value)}
            placeholder="Hammer Strength"
          />
        </Field>
        <Field label="Модель" hint="Необязательно.">
          <input
            type="text"
            value={modelName}
            onChange={(event) => setModelName(event.target.value)}
          />
        </Field>
        <Field
          label="Частный случай оборудования"
          hint="Код родовой записи. «Жим ногами» — частный случай «силового тренажёра»: справочник упражнений говорит родовыми словами, и без этой связи упражнение «жим ногами» считалось бы невыполнимым при наличии жима ногами. Обратное не работает."
        >
          <input
            type="text"
            value={specializes}
            onChange={(event) => setSpecializes(event.target.value)}
            list="equipment-generics"
            placeholder="resistance_machine"
          />
          <datalist id="equipment-generics">
            {props.generics.map((item) => (
              <option key={item.equipment_id} value={item.equipment_id}>
                {item.name_ru}
              </option>
            ))}
          </datalist>
        </Field>
      </div>

      <Field label="Описание" hint="Короткое пояснение. Необязательно.">
        <input
          type="text"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </Field>

      <div className="subcard">
        <Field
          label="Возможности"
          hint="Что оборудование умеет. По возможностям упражнение может подойти тренажёру другого производителя с другим названием."
        >
          <div className="pick-list" style={{ maxHeight: 260 }}>
            {props.capabilities.map((capability) => (
              <label className="pick-list-item" key={capability.capability_id}>
                <input
                  type="checkbox"
                  checked={selected.includes(capability.capability_id)}
                  onChange={() =>
                    setSelected(
                      selected.includes(capability.capability_id)
                        ? selected.filter(
                            (item) => item !== capability.capability_id,
                          )
                        : [...selected, capability.capability_id],
                    )
                  }
                />
                <span className="pick-list-text">
                  <span>{capability.name_ru}</span>
                  {capability.description && (
                    <span className="muted" style={{ fontSize: 12 }}>
                      {capability.description}
                    </span>
                  )}
                </span>
              </label>
            ))}
          </div>
        </Field>
      </div>

      <Field
        label="Синонимы"
        hint="По одному на строку. Префикс stem: — совпадение по основе слова для свободного текста анкеты (например, stem:гантел)."
      >
        <textarea
          rows={5}
          value={aliasText}
          onChange={(event) => setAliasText(event.target.value)}
          placeholder={"chest press machine\nstem:жим от груди"}
        />
      </Field>

      <div className="button-row">
        <button
          type="button"
          className="primary"
          onClick={submit}
          disabled={!valid || busy}
        >
          {existing ? "Сохранить" : "Добавить"}
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
