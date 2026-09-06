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
// бы на другой вопрос — «что нашлось среди первых пятидесяти».

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import IngestionTabs from "@/components/ingestion/IngestionTabs";
import { Pagination } from "@/components/ui/Pagination";
import {
  Card,
  Empty,
  Field,
  Skeleton,
  Status,
  Tag,
} from "@/components/ui/Primitives";
import {
  IngestionRecord,
  IngestionSource,
  getToken,
  ingestionApi,
} from "@/lib/api";
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

export default function IngestionRecordsPage() {
  const [items, setItems] = useState<IngestionRecord[]>([]);
  const [sources, setSources] = useState<IngestionSource[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [search, setSearch] = useState("");
  const [source, setSource] = useState<string[]>([]);
  const [decision, setDecision] = useState<string[]>([]);
  const [quality, setQuality] = useState<string[]>([]);
  const [status, setStatus] = useState<string[]>([]);

  const load = useCallback(
    async (page: number) => {
      setLoading(true);
      try {
        const response = await ingestionApi.records({
          search: search || undefined,
          source: source.length ? source : undefined,
          decision: decision.length ? decision : undefined,
          quality: quality.length ? quality : undefined,
          status: status.length ? status : undefined,
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
    },
    [search, source, decision, quality, status],
  );

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    ingestionApi
      .sources()
      .then((response) => setSources(response.items))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    load(offset).catch(() => undefined);
  }, [offset, load]);

  const resetAndLoad = () => {
    setOffset(0);
    load(0).catch(() => undefined);
  };

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <div className="page-head">
          <h1 className="page-title">Записи внешних источников</h1>
          <p className="page-subtitle">
            Каждая запись источника с решением, уверенностью сопоставления и
            причинами. Отклонённые записи сохраняются: причина, по которой
            упражнение не попало в справочник, — такой же результат импорта.
          </p>
        </div>

        <IngestionTabs />

        {error && <div className="error">{error}</div>}

        <Card title="Отбор" description="Фильтры применяются на сервере.">
          <div className="filters">
            <Field label="Поиск по названию">
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") resetAndLoad();
                }}
                placeholder="например, bench press"
              />
            </Field>

            <CheckboxGroup
              label="Источник"
              options={sources.map((item) => ({
                value: item.source_key,
                label: item.source_key,
              }))}
              selected={source}
              onChange={setSource}
            />

            <CheckboxGroup
              label="Решение"
              options={DECISIONS.map((value) => ({
                value,
                label: INGESTION_DECISION_LABELS[value] ?? value,
              }))}
              selected={decision}
              onChange={setDecision}
            />

            <CheckboxGroup
              label="Качество"
              options={QUALITIES.map((value) => ({
                value,
                label: QUALITY_STATUS_LABELS[value] ?? value,
              }))}
              selected={quality}
              onChange={setQuality}
            />

            <CheckboxGroup
              label="Что сделано"
              options={STATUSES.map((value) => ({
                value,
                label: INGESTION_STATUS_LABELS[value] ?? value,
              }))}
              selected={status}
              onChange={setStatus}
            />

            <div className="filters-actions">
              <button type="button" onClick={resetAndLoad} disabled={loading}>
                Применить
              </button>
              <button
                type="button"
                className="secondary"
                disabled={loading}
                onClick={() => {
                  setSearch("");
                  setSource([]);
                  setDecision([]);
                  setQuality([]);
                  setStatus([]);
                  setOffset(0);
                }}
              >
                Сбросить
              </button>
            </div>
          </div>
        </Card>

        <Card title="Список" description={`Записей: ${count(total)}`}>
          {loading && <Skeleton rows={8} />}

          {!loading && items.length === 0 && (
            <Empty
              title="Записей нет"
              hint="Либо импорт не выполнялся, либо под выбранные условия ничего не попало."
            />
          )}

          {!loading && items.length > 0 && (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Название в источнике</th>
                      <th>Решение</th>
                      <th>Качество</th>
                      <th>Уверенность</th>
                      <th>Упражнение справочника</th>
                      <th>Что сделано</th>
                      <th>Почему</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr key={`${item.source_key}-${item.source_record_id}`}>
                        <td>
                          <div>{item.normalized_name}</div>
                          <div className="muted">
                            <code>{item.source_key}</code>{" "}
                            <code>{item.source_record_id}</code>
                          </div>
                        </td>
                        <td>
                          <Status tone={ingestionDecisionTone(item.decision)}>
                            {INGESTION_DECISION_LABELS[item.decision] ??
                              item.decision}
                          </Status>
                        </td>
                        <td>
                          <Status tone={qualityStatusTone(item.quality_status)}>
                            {QUALITY_STATUS_LABELS[item.quality_status] ??
                              item.quality_status}
                          </Status>
                          <div className="muted">
                            {item.quality_score.toFixed(2)}
                          </div>
                        </td>
                        <td>
                          {item.match_confidence > 0 ? (
                            item.match_confidence.toFixed(2)
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                        <td>
                          {item.matched_external_id ? (
                            <Link
                              href={`/exercises?search=${encodeURIComponent(item.matched_external_id)}`}
                            >
                              {item.matched_external_id}
                            </Link>
                          ) : (
                            <span className="muted">не найдено</span>
                          )}
                        </td>
                        <td>
                          {INGESTION_STATUS_LABELS[item.import_status] ??
                            item.import_status}
                        </td>
                        <td>
                          <div className="inline-list">
                            {item.match_reasons.slice(0, 3).map((reason) => (
                              <Tag key={reason}>
                                {ingestionReasonLabel(reason)}
                              </Tag>
                            ))}
                          </div>
                          {item.import_note && (
                            <p className="field-hint">{item.import_note}</p>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination
                total={total}
                limit={PAGE_SIZE}
                offset={offset}
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

function CheckboxGroup(
  props: Readonly<{
    label: string;
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
    <Field label={props.label}>
      <div className="pick-list" style={{ maxHeight: 140 }}>
        {props.options.map((option) => (
          <label key={option.value} className="pick-list-item">
            <input
              type="checkbox"
              checked={props.selected.includes(option.value)}
              onChange={() => toggle(option.value)}
            />
            <span>{option.label}</span>
          </label>
        ))}
      </div>
    </Field>
  );
}
