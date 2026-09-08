"use client";

// Внешние источники: полнота импорта.
//
// Все числа приходят из базы, а не зашиты в код: показатель, посчитанный один
// раз при написании интерфейса, показывал бы состояние на тот момент.
//
// «На проверку» и «отклонено» — не ошибки импорта, а его результат. Записи,
// которых система не смогла оценить уверенно, остались видны и ждут решения
// человека; превращать их в тишину значило бы делать вид, что решение принято.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { INGESTION_TABS } from "@/components/ingestion/tabs";
import { PageHeader } from "@/components/layout/PageHeader";
import { Metric } from "@/components/ui/Metric";
import { PageTabs } from "@/components/ui/PageTabs";
import {
  Card,
  ErrorState,
  KeyValue,
  Skeleton,
  Status,
} from "@/components/ui/Primitives";
import { IngestionHealth, ingestionApi } from "@/lib/api";
import {
  INGESTION_DECISION_LABELS,
  INGESTION_STATUS_LABELS,
  QUALITY_STATUS_LABELS,
  count,
  ingestionDecisionTone,
} from "@/lib/labels";

export default function IngestionHealthPage() {
  const [health, setHealth] = useState<IngestionHealth | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    ingestionApi
      .health()
      .then((response) => {
        setHealth(response);
        setError("");
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const header = (
    <PageHeader
      title="Полнота импорта"
      description="Что дали внешние источники и что осталось нерешённым. Числа считаются из базы при каждом открытии страницы."
      tabs={
        <PageTabs
          tabs={INGESTION_TABS}
          root="/ingestion"
          label="Разделы внешних источников"
        />
      }
    />
  );

  if (error) {
    return (
      <>
        {header}
        <ErrorState message={error} onRetry={load} />
      </>
    );
  }

  if (!health) {
    return (
      <>
        {header}
        <Card>
          <Skeleton rows={5} />
        </Card>
      </>
    );
  }

  return (
    <>
      {header}

      <div className="stats-grid">
        <Metric
          label="Записей источников"
          value={count(health.external_records_total)}
          hint="Всего прочитано внешних записей. Они хранятся вместе с решением по каждой, а не удаляются после импорта."
        />
        <Metric
          label="Добавлено упражнений"
          value={count(health.records_imported)}
          hint="Записей, ставших новыми упражнениями справочника."
          tone="ok"
        />
        <Metric
          label="Дополнено существующих"
          value={count(health.records_enriched)}
          hint="Записей, которые пополнили уже существующее упражнение техникой, переводом или медиа."
        />
        <Metric
          label="Ждут проверки"
          value={count(health.records_review)}
          hint="Записи, оценка которых не позволяет ни добавить их, ни отклонить автоматически."
          tone={health.records_review > 0 ? "warn" : "ok"}
          action={
            health.records_review > 0 ? (
              <Link className="btn small" href="/ingestion/records">
                Посмотреть
              </Link>
            ) : undefined
          }
        />
        <Metric
          label="Отклонено"
          value={count(health.records_rejected)}
          hint="Записи, которые нельзя показать пользователю: без техники выполнения упражнение неполно."
        />
        <Metric
          label="Повторов внутри источников"
          value={count(health.records_duplicate_variant)}
          hint="Записи, отличающиеся от уже добавленных только пометкой съёмки или номером версии."
        />
      </div>

      <Card
        title="Связи со справочником"
        description="Чем импорт связан с упражнениями: происхождением записей, полей и программным контекстом."
      >
        <KeyValue
          rows={[
            {
              key: "Упражнений со связью источника",
              value: count(health.exercises_with_source_links),
            },
            {
              key: "Связей источников",
              value: count(health.source_links_total),
            },
            {
              key: "Полей с известным происхождением",
              value: count(health.field_provenance_total),
            },
            {
              key: "Программных наблюдений",
              value: `${count(health.program_observations_total)} (упражнений: ${count(health.exercises_with_observations)})`,
              hint: "Программное наблюдение — статистика чужих программ: сколько программ включают упражнение и с какими подходами. Это не назначение нагрузки: её определяет методология проекта.",
            },
          ]}
        />
      </Card>

      {Object.entries(health.by_source).map(([sourceKey, counts]) => (
        <Card
          key={sourceKey}
          title={sourceKey}
          description="Разбивка по источнику"
        >
          <SourceBreakdown counts={counts} />
        </Card>
      ))}
    </>
  );
}

function SourceBreakdown(props: Readonly<{ counts: Record<string, number> }>) {
  const groups: Array<{
    prefix: string;
    title: string;
    labels: Record<string, string>;
  }> = [
    { prefix: "decision:", title: "Решения", labels: INGESTION_DECISION_LABELS },
    { prefix: "quality:", title: "Качество", labels: QUALITY_STATUS_LABELS },
    { prefix: "import:", title: "Что сделано", labels: INGESTION_STATUS_LABELS },
  ];

  return (
    <div className="stack">
      {groups.map((group) => {
        const entries = Object.entries(props.counts)
          .filter(([key]) => key.startsWith(group.prefix))
          .map(([key, value]) => ({
            key: key.slice(group.prefix.length),
            value,
          }))
          .sort((a, b) => b.value - a.value);
        if (entries.length === 0) return null;
        return (
          <div key={group.prefix}>
            <h3 className="section-title">{group.title}</h3>
            <div className="inline-list">
              {entries.map((entry) => (
                <Status
                  key={entry.key}
                  tone={
                    group.prefix === "decision:"
                      ? ingestionDecisionTone(entry.key)
                      : "neutral"
                  }
                >
                  {group.labels[entry.key] ?? entry.key}: {count(entry.value)}
                </Status>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
