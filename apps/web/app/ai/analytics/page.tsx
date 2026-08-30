"use client";

// Сводка качества генерации.
//
// Единица анализа — операция генерации, а не программа: программа существует
// только при успехе, поэтому по программам отказов не видно вовсе.
//
// Обращения к ИИ показаны отдельным блоком и намеренно не сведены с
// генерациями в один показатель: одна генерация делает от нуля до нескольких
// вызовов (перебор моделей, запросы на исправление), и «успешность вызовов» с
// «успешностью генераций» — разные величины.

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import {
  AnalyticsFilters,
  PeriodValue,
  periodStart,
} from "@/components/ai/AnalyticsFilters";
import { BarChart, LineChart } from "@/components/ui/Charts";
import { Metric, SampleWarning } from "@/components/ui/Metric";
import { Card, Empty, Field, Skeleton, Status } from "@/components/ui/Primitives";
import {
  AnalyticsFilter,
  AnalyticsFilterOptions,
  AnalyticsModelRow,
  AnalyticsOverview,
  AnalyticsPromptRow,
  AnalyticsTimeseriesResponse,
  ModelSort,
  PromptComparisonResponse,
  TimeBucket,
  aiApi,
  getToken,
} from "@/lib/api";
import {
  count,
  duration,
  metricLabel,
  percent,
  shortDate,
  shortDateTime,
} from "@/lib/labels";

const MODEL_SORTS: Array<{ value: ModelSort; label: string }> = [
  { value: "usage", label: "Чаще использовалась" },
  { value: "failure_rate", label: "Больше отказов" },
  { value: "fallback_rate", label: "Чаще приводила к сборке без ИИ" },
  { value: "repair_attempts", label: "Больше исправлений" },
  { value: "avg_latency_ms", label: "Дольше отвечает" },
];

export default function AnalyticsPage() {
  const [period, setPeriod] = useState<PeriodValue>("7d");
  const [filter, setFilter] = useState<AnalyticsFilter>({});
  const [bucket, setBucket] = useState<TimeBucket>("day");
  const [modelSort, setModelSort] = useState<ModelSort>("usage");

  const [options, setOptions] = useState<AnalyticsFilterOptions | null>(null);
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [series, setSeries] = useState<AnalyticsTimeseriesResponse | null>(null);
  const [models, setModels] = useState<AnalyticsModelRow[] | null>(null);
  const [prompts, setPrompts] = useState<AnalyticsPromptRow[] | null>(null);
  const [comparison, setComparison] = useState<PromptComparisonResponse | null>(
    null,
  );
  const [left, setLeft] = useState<number | "">("");
  const [right, setRight] = useState<number | "">("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const scoped: AnalyticsFilter = { ...filter, date_from: periodStart(period) };

  const load = useCallback(async (spec: AnalyticsFilter, step: TimeBucket, sort: ModelSort) => {
    setLoading(true);
    try {
      const [overviewData, seriesData, modelsData, promptsData] =
        await Promise.all([
          aiApi.analyticsOverview(spec),
          aiApi.analyticsTimeseries(step, spec),
          aiApi.analyticsModels(spec, { sort_by: sort, order: "desc" }),
          aiApi.analyticsPrompts(spec, {
            sort_by: "prompt_version",
            order: "desc",
          }),
        ]);
      setOverview(overviewData);
      setSeries(seriesData);
      setModels(modelsData.items);
      setPrompts(promptsData.items);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

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
    load(scoped, bucket, modelSort);
    // Фильтр — объект: сериализуем его, чтобы не перезапрашивать на каждый рендер.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(scoped), bucket, modelSort, load]);

  const compare = async () => {
    if (left === "" || right === "") return;
    try {
      setComparison(
        await aiApi.analyticsComparePrompts(Number(left), Number(right), scoped),
      );
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const generations = overview?.generations;
  const calls = overview?.calls;
  const formatBucket = bucket === "hour" ? shortDateTime : shortDate;

  return (
    <>
      <div className="page-head">
        <h1 className="page-title">Качество генерации</h1>
        <p className="page-subtitle">
          Что происходит с программами при их создании: сколько операций
          завершилось успехом, где ИИ не справился и программу собрал алгоритм,
          какие модели и версии инструкции дают результат. Единица счёта —
          операция генерации: программа появляется только при успехе, поэтому по
          программам отказов не видно.
        </p>
      </div>

      {error && <div className="error">{error}</div>}

      <Card title="Отбор данных">
        <AnalyticsFilters
          period={period}
          onPeriodChange={setPeriod}
          filter={filter}
          onFilterChange={setFilter}
          options={options}
          withPromptVersion
        />
      </Card>

      {loading && !overview && (
        <Card>
          <Skeleton rows={6} />
        </Card>
      )}

      {overview && generations && calls && (
        <>
          {!overview.sample.confident && (
            <SampleWarning
              generations={overview.sample.generations}
              minConfident={overview.sample.min_confident}
            />
          )}

          {generations.total === 0 ? (
            <Card>
              <Empty
                title="Генераций за этот период не было"
                hint="Измените период или снимите фильтры. Нулевые проценты здесь не показываются: считать их не на чем."
              />
            </Card>
          ) : (
            <>
              <div className="stats-grid">
                <Metric
                  label="Операций генерации"
                  value={count(generations.total)}
                  secondary={`успешно ${count(generations.succeeded)}, с ошибкой ${count(generations.failed)}`}
                  hint="Каждая попытка построить программу, включая неудачные."
                />
                <Metric
                  label="Доля успешных"
                  value={percent(generations.success_rate)}
                  hint="Операции, завершившиеся сохранённой программой."
                  tone={
                    generations.success_rate === null
                      ? "neutral"
                      : generations.success_rate >= 90
                        ? "ok"
                        : generations.success_rate >= 70
                          ? "warn"
                          : "bad"
                  }
                />
                <Metric
                  label="Собрано ИИ"
                  value={percent(generations.ai_share)}
                  secondary={`${count(generations.by_ai)} из ${count(generations.total)}`}
                  hint="Остальные программы собрал алгоритмический генератор."
                />
                <Metric
                  label="Сборок без ИИ"
                  value={percent(generations.fallback_rate)}
                  secondary={`${count(generations.deterministic_fallback)} раз программу собрал алгоритм`}
                  hint="Запрашивался ИИ, но программу собрал алгоритм: это отказ ИИ, а не выбор."
                  tone={
                    generations.fallback === 0
                      ? "ok"
                      : generations.fallback_rate !== null &&
                          generations.fallback_rate > 20
                        ? "bad"
                        : "warn"
                  }
                />
                <Metric
                  label="Не прошло проверку"
                  value={count(generations.validation_failures)}
                  secondary={`принято после исправления: ${count(generations.repaired)}`}
                  hint="Ответ модели отвергнут проверкой программы хотя бы один раз."
                  tone={generations.validation_failures > 0 ? "warn" : "ok"}
                />
                <Metric
                  label="Среднее время"
                  value={duration(generations.avg_duration_ms)}
                  secondary={`95-й процентиль: ${duration(generations.p95_duration_ms)}`}
                  hint="От запуска операции до её завершения."
                />
              </div>

              <Card
                title="Обращения к ИИ"
                description="Считаются отдельно от генераций: одна генерация делает несколько вызовов при переборе моделей и запросах на исправление, поэтому эти проценты нельзя складывать с успешностью генераций."
              >
                <div className="stats-grid" style={{ marginBottom: 0 }}>
                  <Metric
                    label="Вызовов"
                    value={count(calls.total)}
                    secondary={`с ошибкой: ${count(calls.failed)}`}
                    hint="Каждое обращение к модели, включая повторные."
                  />
                  <Metric
                    label="Успешных вызовов"
                    value={percent(calls.success_rate)}
                    hint="Транспортный успех: ответ получен. Пригодность ответа проверяется отдельно."
                  />
                  <Metric
                    label="Средний ответ"
                    value={duration(calls.avg_latency_ms)}
                    secondary={`95-й процентиль: ${duration(calls.p95_latency_ms)}`}
                    hint="Время ответа модели на один запрос."
                  />
                  <Metric
                    label="Токенов израсходовано"
                    value={count(calls.total_tokens)}
                    hint="Сумма по всем вызовам за период."
                  />
                </div>
              </Card>
            </>
          )}
        </>
      )}

      {series && series.items.length > 0 && (
        <Card
          title="Динамика"
          description="Интервалы без генераций в графике отсутствуют: нуль в них означал бы неудачные попытки, которых не было."
          actions={
            <select
              value={bucket}
              onChange={(event) => setBucket(event.target.value as TimeBucket)}
              aria-label="Шаг графика"
            >
              <option value="day">По дням</option>
              <option value="hour">По часам</option>
            </select>
          }
        >
          <LineChart
            title="Операции генерации"
            series={[
              {
                label: "Всего",
                tone: "accent",
                points: series.items.map((point) => ({
                  label: formatBucket(point.bucket),
                  value: point.total,
                })),
              },
              {
                label: "Успешно",
                tone: "ok",
                points: series.items.map((point) => ({
                  label: formatBucket(point.bucket),
                  value: point.succeeded,
                })),
              },
              {
                label: "С ошибкой",
                tone: "danger",
                points: series.items.map((point) => ({
                  label: formatBucket(point.bucket),
                  value: point.failed,
                })),
              },
              {
                label: "Собрано без ИИ",
                tone: "warn",
                points: series.items.map((point) => ({
                  label: formatBucket(point.bucket),
                  value: point.fallback,
                })),
              },
            ]}
          />
        </Card>
      )}

      <Card
        title="Модели"
        description="Единица счёта — попытка модели, а не генерация: одна генерация обращается к нескольким моделям, поэтому суммы здесь больше, чем число генераций."
        actions={
          <select
            value={modelSort}
            onChange={(event) => setModelSort(event.target.value as ModelSort)}
            aria-label="Сортировка моделей"
          >
            {MODEL_SORTS.map((sort) => (
              <option key={sort.value} value={sort.value}>
                {sort.label}
              </option>
            ))}
          </select>
        }
      >
        {loading && !models && <Skeleton rows={4} />}
        {models && models.length === 0 && (
          <Empty
            title="Модели не участвовали"
            hint="За этот период ИИ не вызывался: программы собирал алгоритмический генератор либо генераций не было вовсе."
          />
        )}
        {models && models.length > 0 && (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Модель</th>
                    <th>Сервис</th>
                    <th>Попыток</th>
                    <th>Приняты</th>
                    <th>Отказов</th>
                    <th>Исправлений</th>
                    <th>Первый ответ прошёл</th>
                    <th>Средний ответ</th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((model) => (
                    <tr key={`${model.provider}-${model.model}`}>
                      <td>
                        <code>{model.model}</code>
                        {!model.confident && (
                          <div className="muted" style={{ fontSize: 12 }}>
                            данных мало
                          </div>
                        )}
                      </td>
                      <td>{model.provider ?? "—"}</td>
                      <td>
                        {count(model.usage)}
                        <div className="muted" style={{ fontSize: 12 }}>
                          основная: {count(model.as_primary)}, резервная:{" "}
                          {count(model.as_fallback)}
                        </div>
                      </td>
                      <td>{count(model.succeeded)}</td>
                      <td>
                        {count(model.failed)}
                        <div className="muted" style={{ fontSize: 12 }}>
                          проверка: {count(model.invalid_outputs)}, сервис:{" "}
                          {count(model.provider_errors)}
                        </div>
                      </td>
                      <td>{count(model.repair_attempts)}</td>
                      <td>{percent(model.first_answer_rate)}</td>
                      <td>{duration(model.avg_latency_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ marginTop: "var(--s-5)" }}>
              <BarChart
                title="Доля отказов по моделям"
                points={models.map((model) => ({
                  label: model.model,
                  value: model.failure_rate,
                }))}
                tone="danger"
                format={(value) => percent(value)}
              />
            </div>
          </>
        )}
      </Card>

      <Card
        title="Версии инструкции"
        description="Единица счёта — генерация: инструкция участвует в ней целиком, независимо от числа перебранных моделей."
        actions={
          <Link className="btn small" href="/ai/prompts">
            Управление инструкциями
          </Link>
        }
      >
        {loading && !prompts && <Skeleton rows={3} />}
        {prompts && prompts.length === 0 && (
          <Empty
            title="Нет генераций с инструкцией"
            hint="Инструкция используется только при генерации через ИИ. Алгоритмический генератор её не применяет."
          />
        )}
        {prompts && prompts.length > 0 && (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Версия</th>
                    <th>Название</th>
                    <th>Генераций</th>
                    <th>Успешно</th>
                    <th>Не прошло проверку</th>
                    <th>Собрано без ИИ</th>
                    <th>Среднее время</th>
                    <th>Последнее использование</th>
                  </tr>
                </thead>
                <tbody>
                  {prompts.map((prompt) => (
                    <tr key={prompt.prompt_version}>
                      <td>
                        {prompt.prompt_version}
                        {prompt.enabled === false && (
                          <div className="muted" style={{ fontSize: 12 }}>
                            выключена
                          </div>
                        )}
                      </td>
                      <td>{prompt.name ?? <span className="muted">удалена</span>}</td>
                      <td>
                        {count(prompt.usage)}
                        {!prompt.confident && (
                          <div className="muted" style={{ fontSize: 12 }}>
                            данных мало
                          </div>
                        )}
                      </td>
                      <td>{percent(prompt.success_rate)}</td>
                      <td>{percent(prompt.validation_failure_rate)}</td>
                      <td>{percent(prompt.fallback_rate)}</td>
                      <td>{duration(prompt.avg_duration_ms)}</td>
                      <td>{shortDateTime(prompt.last_used_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="subcard">
              <h3 className="card-title" style={{ fontSize: 14 }}>
                Сравнить две версии
              </h3>
              <p className="card-desc">
                Вывод «какая версия лучше» делается только при достаточном числе
                генераций и заметной разнице. Рабочая версия не переключается
                автоматически: выбор остаётся за администратором.
              </p>
              <div className="filters">
                <Field label="Версия A" htmlFor="cmp-left">
                  <select
                    id="cmp-left"
                    value={left}
                    onChange={(event) =>
                      setLeft(event.target.value ? Number(event.target.value) : "")
                    }
                  >
                    <option value="">Выберите</option>
                    {prompts.map((prompt) => (
                      <option key={prompt.prompt_version} value={prompt.prompt_version}>
                        Версия {prompt.prompt_version}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Версия B" htmlFor="cmp-right">
                  <select
                    id="cmp-right"
                    value={right}
                    onChange={(event) =>
                      setRight(event.target.value ? Number(event.target.value) : "")
                    }
                  >
                    <option value="">Выберите</option>
                    {prompts.map((prompt) => (
                      <option key={prompt.prompt_version} value={prompt.prompt_version}>
                        Версия {prompt.prompt_version}
                      </option>
                    ))}
                  </select>
                </Field>
                <div className="filters-actions">
                  <button
                    type="button"
                    className="primary"
                    onClick={compare}
                    disabled={left === "" || right === "" || left === right}
                  >
                    Сравнить
                  </button>
                </div>
              </div>

              {comparison && comparison.missing_versions.length > 0 && (
                <p className="field-hint">
                  Нет данных по версиям:{" "}
                  {comparison.missing_versions.join(", ")}. Сравнивать можно
                  только версии, которые участвовали в генерациях.
                </p>
              )}

              {comparison && comparison.metrics.length > 0 && (
                <div className="table-wrap" style={{ marginTop: "var(--s-3)" }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Показатель</th>
                        <th>Версия {comparison.metrics[0].left_version}</th>
                        <th>Версия {comparison.metrics[0].right_version}</th>
                        <th>Разница</th>
                        <th>Вывод</th>
                      </tr>
                    </thead>
                    <tbody>
                      {comparison.metrics.map((metric) => (
                        <tr key={metric.metric}>
                          <td>{metricLabel(metric.metric)}</td>
                          <td>{percent(metric.left_value)}</td>
                          <td>{percent(metric.right_value)}</td>
                          <td>
                            {metric.difference_pp === null
                              ? "—"
                              : `${metric.difference_pp > 0 ? "+" : ""}${metric.difference_pp} п.п.`}
                          </td>
                          <td>
                            {metric.better_version === null ? (
                              <Status tone="neutral">{metric.note}</Status>
                            ) : (
                              <Status tone="ok">
                                лучше версия {metric.better_version}
                              </Status>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </Card>

      <Card
        title="Разбор конкретных генераций"
        description="Список операций с фильтрами и переходом к карточке: какие модели отвечали, что не прошло проверку и сколько было исправлений."
      >
        <Link className="btn" href="/ai/generations">
          Открыть список генераций
        </Link>
      </Card>
    </>
  );
}
