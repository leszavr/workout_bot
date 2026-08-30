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
import { Pagination } from "@/components/ui/Pagination";
import { Card, Empty, Field, Skeleton, Status, Tag } from "@/components/ui/Primitives";
import {
  AnalyticsFilter,
  AnalyticsFilterOptions,
  AnalyticsGenerationRow,
  GenerationSort,
  PagedResponse,
  SortOrder,
  aiApi,
  getToken,
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

const SORTS: Array<{ value: GenerationSort; label: string }> = [
  { value: "created_at", label: "По времени" },
  { value: "duration_ms", label: "По длительности" },
  { value: "attempts", label: "По числу попыток" },
  { value: "status", label: "По итогу" },
];

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
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    aiApi
      .analyticsFilters()
      .then(setOptions)
      .catch((e) => setError((e as Error).message));
  }, []);

  useEffect(() => {
    if (!getToken()) return;
    load(scoped, offset, sortBy, order);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(scoped), offset, sortBy, order, load]);

  const hasFilters = Object.values(filter).some(
    (value) => value !== undefined && value !== "",
  );

  return (
    <>
      <div className="page-head">
        <h1 className="page-title">Генерации</h1>
        <p className="page-subtitle">
          Каждая строка — одна операция генерации: попытка построить программу.
          Неудачные операции остаются здесь, хотя программы у них нет: без них
          не видно, почему ИИ не справился.
        </p>
      </div>

      {error && <div className="error">{error}</div>}

      <Card
        title="Отбор генераций"
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
        <div className="filters" style={{ marginTop: "var(--s-4)" }}>
          <Field label="Сортировка" htmlFor="gen-sort">
            <select
              id="gen-sort"
              value={sortBy}
              onChange={(event) => {
                setOffset(0);
                setSortBy(event.target.value as GenerationSort);
              }}
            >
              {SORTS.map((sort) => (
                <option key={sort.value} value={sort.value}>
                  {sort.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Порядок" htmlFor="gen-order">
            <select
              id="gen-order"
              value={order}
              onChange={(event) => {
                setOffset(0);
                setOrder(event.target.value as SortOrder);
              }}
            >
              <option value="desc">Сначала наибольшие</option>
              <option value="asc">Сначала наименьшие</option>
            </select>
          </Field>
        </div>
      </Card>

      <Card
        title="Операции генерации"
        description={
          data
            ? `Под фильтр попало ${count(data.total)} операций`
            : undefined
        }
      >
        {loading && !data && <Skeleton rows={6} />}

        {!loading && data && data.items.length === 0 && (
          <Empty
            title={hasFilters ? "Ничего не нашлось" : "Генераций пока не было"}
            hint={
              hasFilters
                ? "Условия слишком узкие: попробуйте расширить период или снять часть фильтров."
                : "Операции появятся здесь после первой попытки создать программу."
            }
          />
        )}

        {data && data.items.length > 0 && (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Время</th>
                    <th>Итог</th>
                    <th>Кто собрал</th>
                    <th>Модель</th>
                    <th>Проверка</th>
                    <th>Модели / исправления</th>
                    <th>Длительность</th>
                    <th>Анкета</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((row) => {
                    const validation = validationSummary(row);
                    return (
                      <tr key={row.job_id}>
                        <td>
                          <Link href={`/ai/generations/${row.job_id}`}>
                            {dateTime(row.created_at)}
                          </Link>
                          <div className="muted" style={{ fontSize: 12 }}>
                            {generationTriggerLabel(row.trigger)}
                          </div>
                        </td>
                        <td>
                          <Status tone={generationStatusTone(row.status)}>
                            {generationStatusLabel(row.status)}
                          </Status>
                          {row.last_error_code && (
                            <div className="muted" style={{ fontSize: 12 }}>
                              {generationErrorLabel(row.last_error_code)}
                            </div>
                          )}
                        </td>
                        <td>
                          {row.actual_generator ? (
                            <>
                              {generatorLabel(row.actual_generator)}
                              {row.fallback_used && (
                                <div className="muted" style={{ fontSize: 12 }}>
                                  вместо ИИ:{" "}
                                  {row.fallback_reason_code
                                    ? aiFallbackReasonLabel(
                                        row.fallback_reason_code,
                                      )
                                    : "причина не указана"}
                                </div>
                              )}
                            </>
                          ) : (
                            <span className="muted">программы нет</span>
                          )}
                        </td>
                        <td>
                          {row.model ? <code>{row.model}</code> : "—"}
                          {row.prompt_version !== null && (
                            <div className="muted" style={{ fontSize: 12 }}>
                              инструкция v{row.prompt_version}
                            </div>
                          )}
                        </td>
                        <td>
                          <Tag tone={validation.tone}>{validation.label}</Tag>
                        </td>
                        <td>
                          {count(row.models_tried)} / {count(row.repair_attempts)}
                        </td>
                        <td>{duration(row.duration_ms)}</td>
                        <td>
                          <Link href={`/profiles/${row.profile_id}`}>
                            анкета
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
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
    </>
  );
}
