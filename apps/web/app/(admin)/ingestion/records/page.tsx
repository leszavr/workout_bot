"use client";

// Внешние источники: записи и решения по ним.
//
// Раздел существует, чтобы результат импорта был проверяем поштучно. Числа из
// сводки без списка не подтверждены: «953 новых упражнения» и «953 случайные
// строки» выглядят одинаково, пока нельзя посмотреть, что именно решено по
// каждой записи и почему.
//
// Записи отклонённые и требующие проверки не скрыты: причина, по которой
// упражнение не попало в справочник, — такой же результат импорта, как
// добавленное упражнение.
//
// Фильтры серверные: записей больше страницы, и фильтрация на клиенте отвечала
// бы на другой вопрос — «что нашлось среди первых пятидесяти». Условия отбора
// хранятся отдельно от черновика поиска: раньше состояние поля поиска входило в
// зависимости загрузки, и запрос уходил на каждое нажатие клавиши, хотя рядом
// стояла кнопка «Применить».

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { INGESTION_TABS } from "@/components/ingestion/tabs";
import { PageHeader } from "@/components/layout/PageHeader";
import { DataColumn, DataTable } from "@/components/ui/DataTable";
import { FilterBar, FilterGroup, SearchInput } from "@/components/ui/FilterBar";
import { PageTabs } from "@/components/ui/PageTabs";
import { Card, Field, Status, Tag } from "@/components/ui/Primitives";
import { IngestionRecord, IngestionSource, ingestionApi } from "@/lib/api";
import {
  INGESTION_DECISION_LABELS,
  INGESTION_STATUS_LABELS,
  QUALITY_STATUS_LABELS,
  count,
  ingestionDecisionTone,
  ingestionReasonLabel,
  qualityStatusTone,
} from "@/lib/labels";

const PAGE_SIZE = 50;

const DECISIONS = [
  "new_relevant",
  "enrichable",
  "existing",
  "duplicate_variant",
  "questionable",
  "unknown",
  "low_quality",
];

const QUALITIES = ["ready", "review", "reject"];
const STATUSES = ["imported", "enriched", "skipped", "rejected", "pending"];

interface Filters {
  search: string;
  source: string[];
  decision: string[];
  quality: string[];
  status: string[];
}

const EMPTY: Filters = {
  search: "",
  source: [],
  decision: [],
  quality: [],
  status: [],
};

const COLUMNS: ReadonlyArray<DataColumn<IngestionRecord>> = [
  {
    key: "name",
    header: "Название в источнике",
    render: (item) => (
      <>
        <div>{item.normalized_name}</div>
        <div className="muted" style={{ fontSize: 12 }}>
          <code>{item.source_key}</code> <code>{item.source_record_id}</code>
        </div>
      </>
    ),
  },
  {
    key: "decision",
    header: "Решение",
    render: (item) => (
      <Status tone={ingestionDecisionTone(item.decision)}>
        {INGESTION_DECISION_LABELS[item.decision] ?? item.decision}
      </Status>
    ),
  },
  {
    key: "quality",
    header: "Качество",
    render: (item) => (
      <>
        <Status tone={qualityStatusTone(item.quality_status)}>
          {QUALITY_STATUS_LABELS[item.quality_status] ?? item.quality_status}
        </Status>
        <div className="muted" style={{ fontSize: 12 }}>
          {item.quality_score.toFixed(2)}
        </div>
      </>
    ),
  },
  {
    key: "confidence",
    header: "Уверенность",
    numeric: true,
    hint: "Насколько уверенно запись сопоставлена с упражнением справочника.",
    render: (item) =>
      item.match_confidence > 0 ? (
        item.match_confidence.toFixed(2)
      ) : (
        <span className="muted">—</span>
      ),
  },
  {
    key: "matched",
    header: "Упражнение справочника",
    render: (item) =>
      item.matched_external_id ? (
        <Link
          href={`/exercises?search=${encodeURIComponent(item.matched_external_id)}`}
        >
          {item.matched_external_id}
        </Link>
      ) : (
        <span className="muted">не найдено</span>
      ),
  },
  {
    key: "status",
    header: "Что сделано",
    render: (item) =>
      INGESTION_STATUS_LABELS[item.import_status] ?? item.import_status,
  },
  {
    key: "reasons",
    header: "Почему",
    render: (item) => (
      <>
        <div className="inline-list" style={{ gap: 4 }}>
          {item.match_reasons.slice(0, 3).map((reason) => (
            <Tag key={reason}>{ingestionReasonLabel(reason)}</Tag>
          ))}
        </div>
        {item.import_note && <p className="field-hint">{item.import_note}</p>}
      </>
    ),
  },
];

export default function IngestionRecordsPage() {
  const [items, setItems] = useState<IngestionRecord[]>([]);
  const [sources, setSources] = useState<IngestionSource[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [draftSearch, setDraftSearch] = useState("");
  const [filters, setFilters] = useState<Filters>(EMPTY);

  const load = useCallback(async (next: Filters, page: number) => {
    setLoading(true);
    try {
      const response = await ingestionApi.records({
        search: next.search || undefined,
        source: next.source.length ? next.source : undefined,
        decision: next.decision.length ? next.decision : undefined,
        quality: next.quality.length ? next.quality : undefined,
        status: next.status.length ? next.status : undefined,
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
    ingestionApi
      .sources()
      .then((response) => setSources(response.items))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    load(filters, offset).catch(() => undefined);
  }, [filters, offset, load]);

  const apply = (next: Filters) => {
    setOffset(0);
    setFilters(next);
  };

  const activeCount =
    (filters.search ? 1 : 0) +
    filters.source.length +
    filters.decision.length +
    filters.quality.length +
    filters.status.length;

  return (
    <>
      <PageHeader
        title="Записи внешних источников"
        description="Каждая запись источника с решением, уверенностью сопоставления и причинами. Отклонённые записи сохраняются: причина, по которой упражнение не попало в справочник, — такой же результат импорта."
        tabs={
          <PageTabs
            tabs={INGESTION_TABS}
            root="/ingestion"
            label="Разделы внешних источников"
          />
        }
      />

      <Card title="Отбор записей">
        <FilterBar
          onApply={() => apply({ ...filters, search: draftSearch })}
          onReset={() => {
            setDraftSearch("");
            apply(EMPTY);
          }}
          activeCount={activeCount}
          extra={
            <FilterGroup
              title="Решение, качество и результат"
              activeCount={
                filters.source.length +
                filters.decision.length +
                filters.quality.length +
                filters.status.length
              }
            >
              <CheckboxGroup
                label="Источник"
                options={sources.map((item) => ({
                  value: item.source_key,
                  label: item.source_key,
                }))}
                selected={filters.source}
                onChange={(values) => apply({ ...filters, source: values })}
              />
              <CheckboxGroup
                label="Решение"
                hint="Что импорт решил по записи: добавить, обогатить существующее, отклонить."
                options={DECISIONS.map((value) => ({
                  value,
                  label: INGESTION_DECISION_LABELS[value] ?? value,
                }))}
                selected={filters.decision}
                onChange={(values) => apply({ ...filters, decision: values })}
              />
              <CheckboxGroup
                label="Качество"
                options={QUALITIES.map((value) => ({
                  value,
                  label: QUALITY_STATUS_LABELS[value] ?? value,
                }))}
                selected={filters.quality}
                onChange={(values) => apply({ ...filters, quality: values })}
              />
              <CheckboxGroup
                label="Что сделано"
                options={STATUSES.map((value) => ({
                  value,
                  label: INGESTION_STATUS_LABELS[value] ?? value,
                }))}
                selected={filters.status}
                onChange={(values) => apply({ ...filters, status: values })}
              />
            </FilterGroup>
          }
        >
          <Field
            label="Поиск по названию"
            hint="По нормализованному названию записи источника."
            htmlFor="rec-search"
          >
            <SearchInput
              id="rec-search"
              placeholder="например, bench press"
              value={draftSearch}
              onChange={setDraftSearch}
              onSubmit={() => apply({ ...filters, search: draftSearch })}
            />
          </Field>
        </FilterBar>
      </Card>

      <Card title="Список" description={`Записей: ${count(total)}`}>
        <DataTable
          columns={COLUMNS}
          rows={items}
          rowKey={(item) => `${item.source_key}-${item.source_record_id}`}
          loading={loading}
          error={error}
          emptyTitle="Записей нет"
          emptyHint="Либо импорт не выполнялся, либо под выбранные условия ничего не попало."
          pagination={{
            total,
            limit: PAGE_SIZE,
            offset,
            onChange: setOffset,
          }}
        />
      </Card>
    </>
  );
}

function CheckboxGroup(
  props: Readonly<{
    label: string;
    hint?: string;
    options: Array<{ value: string; label: string }>;
    selected: string[];
    onChange: (values: string[]) => void;
  }>,
) {
  const toggle = (value: string) => {
    props.onChange(
      props.selected.includes(value)
        ? props.selected.filter((v) => v !== value)
        : [...props.selected, value],
    );
  };

  return (
    <Field label={props.label} hint={props.hint}>
      <div className="pick-list" style={{ maxHeight: 140 }}>
        {props.options.map((option) => (
          <label key={option.value} className="pick-list-item">
            <input
              type="checkbox"
              checked={props.selected.includes(option.value)}
              onChange={() => toggle(option.value)}
            />
            <span className="pick-list-text">
              <span>{option.label}</span>
            </span>
          </label>
        ))}
      </div>
    </Field>
  );
}
