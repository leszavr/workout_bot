"use client";

// Список операций генерации.
//
// Раздел отвечает на вопрос «что произошло в конкретной генерации», на который
// сводка ответить не может: там агрегаты. Фильтрация, сортировка и пагинация
// серверные — отсортированная страница отвечала бы на вопрос «самая долгая
// генерация» неверно, только внутри показанных строк.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  AnalyticsFilters,
  PeriodValue,
  periodStart,
} from "@/components/ai/AnalyticsFilters";
import { DataColumn, DataTable } from "@/components/ui/DataTable";
import { Card, ErrorState, Status, Tag } from "@/components/ui/Primitives";
import {
  AnalyticsFilter,
  AnalyticsFilterOptions,
  AnalyticsGenerationRow,
  GenerationSort,
  PagedResponse,
  SortOrder,
  aiApi,
} from "@/lib/api";
import {
  aiFallbackReasonLabel,
  count,
  dateTime,
  duration,
  generationErrorLabel,
  generationStatusLabel,
  generationStatusTone,
  generationTriggerLabel,
  generatorLabel,
} from "@/lib/labels";

const PAGE_SIZE = 25;

/** Что стало с результатом проверки: одна строка вместо трёх чисел. */
function validationSummary(row: AnalyticsGenerationRow): {
  label: string;
  tone: "ok" | "warn" | "bad" | "neutral";
} {
  if (row.status === "failed" && row.last_error_code === "validation_failed") {
    return { label: "не прошло проверку", tone: "bad" };
  }
  if (row.repaired) return { label: "принято после исправления", tone: "warn" };
  if (row.invalid_outputs > 0) {
    return { label: `ответов отвергнуто: ${row.invalid_outputs}`, tone: "warn" };
  }
  if (row.status === "succeeded") return { label: "принято сразу", tone: "ok" };
  return { label: "—", tone: "neutral" };
}

const COLUMNS: ReadonlyArray<DataColumn<AnalyticsGenerationRow>> = [
  {
    key: "created",
    header: "Время",
    sortKey: "created_at",
    render: (row) => (
      <>
        <Link href={`/ai/generations/${row.job_id}`}>
          {dateTime(row.created_at)}
        </Link>
        <div className="muted" style={{ fontSize: 12 }}>
          {generationTriggerLabel(row.trigger)}
        </div>
      </>
    ),
  },
  {
    key: "status",
    header: "Итог",
    sortKey: "status",
    render: (row) => (
      <>
        <Status tone={generationStatusTone(row.status)}>
          {generationStatusLabel(row.status)}
        </Status>
        {row.last_error_code && (
          <div className="muted" style={{ fontSize: 12 }}>
            {generationErrorLabel(row.last_error_code)}
          </div>
        )}
      </>
    ),
  },
  {
    key: "generator",
    header: "Кто собрал",
    render: (row) =>
      row.actual_generator ? (
        <>
          {generatorLabel(row.actual_generator)}
          {row.fallback_used && (
            <div className="muted" style={{ fontSize: 12 }}>
              вместо ИИ:{" "}
              {row.fallback_reason_code
                ? aiFallbackReasonLabel(row.fallback_reason_code)
                : "причина не указана"}
            </div>
          )}
        </>
      ) : (
        <span className="muted">программы нет</span>
      ),
  },
  {
    key: "model",
    header: "Модель",
    render: (row) => (
      <>
        {row.model ? <code>{row.model}</code> : "—"}
        {row.prompt_version !== null && (
          <div className="muted" style={{ fontSize: 12 }}>
            инструкция v{row.prompt_version}
          </div>
        )}
      </>
    ),
  },
  {
    key: "validation",
    header: "Проверка",
    hint: "Прошёл ли ответ модели проверку структуры и потребовалось ли исправление.",
    render: (row) => {
      const validation = validationSummary(row);
      return <Tag tone={validation.tone}>{validation.label}</Tag>;
    },
  },
  {
    key: "attempts",
    header: "Модели / исправления",
    numeric: true,
    sortKey: "attempts",
    render: (row) => `${count(row.models_tried)} / ${count(row.repair_attempts)}`,
  },
  {
    key: "duration",
    header: "Длительность",
    numeric: true,
    sortKey: "duration_ms",
    render: (row) => duration(row.duration_ms),
  },
  {
    key: "profile",
    header: "Анкета",
    render: (row) => <Link href={`/profiles/${row.profile_id}`}>анкета</Link>,
  },
];

export default function GenerationsPage() {
  const [period, setPeriod] = useState<PeriodValue>("7d");
  const [filter, setFilter] = useState<AnalyticsFilter>({});
  const [sortBy, setSortBy] = useState<GenerationSort>("created_at");
  const [order, setOrder] = useState<SortOrder>("desc");
  const [offset, setOffset] = useState(0);

  const [options, setOptions] = useState<AnalyticsFilterOptions | null>(null);
  const [data, setData] = useState<PagedResponse<AnalyticsGenerationRow> | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const scoped: AnalyticsFilter = { ...filter, date_from: periodStart(period) };

  const load = useCallback(
    (
      spec: AnalyticsFilter,
      page: number,
      sort: GenerationSort,
      direction: SortOrder,
    ) => {
      setLoading(true);
      aiApi
        .analyticsGenerations(spec, {
          limit: PAGE_SIZE,
          offset: page,
          sort_by: sort,
          order: direction,
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
    aiApi
      .analyticsFilters()
      .then(setOptions)
      .catch((e) => setError((e as Error).message));
  }, []);

  useEffect(() => {
    load(scoped, offset, sortBy, order);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(scoped), offset, sortBy, order, load]);

  const hasFilters = Object.values(filter).some(
    (value) => value !== undefined && value !== "",
  );

  return (
    <>
      {error && <ErrorState message={error} />}

      <Card
        title="Отбор генераций"
        description="Сортировка задаётся нажатием на заголовок столбца и выполняется на сервере, а не внутри показанной страницы."
        actions={
          <Link className="btn small" href="/ai/analytics">
            К сводке
          </Link>
        }
      >
        <AnalyticsFilters
          period={period}
          onPeriodChange={(value) => {
            setOffset(0);
            setPeriod(value);
          }}
          filter={filter}
          onFilterChange={(value) => {
            setOffset(0);
            setFilter(value);
          }}
          options={options}
          withPromptVersion
        />
      </Card>

      <Card
        title="Операции генерации"
        description={
          data ? `Под фильтр попало ${count(data.total)} операций` : undefined
        }
      >
        <DataTable
          columns={COLUMNS}
          rows={data?.items ?? []}
          rowKey={(row) => row.job_id}
          loading={loading}
          sort={{
            by: sortBy,
            order,
            onChange: (by, direction) => {
              setOffset(0);
              setSortBy(by as GenerationSort);
              setOrder(direction);
            },
          }}
          emptyTitle={
            hasFilters ? "Ничего не нашлось" : "Генераций пока не было"
          }
          emptyHint={
            hasFilters
              ? "Условия слишком узкие: попробуйте расширить период или снять часть фильтров."
              : "Операции появятся здесь после первой попытки создать программу."
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
