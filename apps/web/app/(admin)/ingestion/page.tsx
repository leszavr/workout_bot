"use client";

// Внешние источники знаний об упражнениях: что за источники и что они дали.
//
// Раздел отвечает на первый вопрос администратора об импорте: откуда данные, в
// каком состоянии источник прочитан и что из этого попало в справочник. Версия
// показывается рядом с числами не для порядка: без неё «добавлено 953» не
// отвечает на вопрос, из какого состояния источника они взяты, и повторить
// импорт нельзя.
//
// Условия использования данных и media показываются раздельно, потому что у них
// разные правообладатели: данные внешнего каталога распространяются свободно, а
// media принадлежит третьей стороне и требует указания авторства.

import { useCallback, useEffect, useState } from "react";

import { INGESTION_TABS } from "@/components/ingestion/tabs";
import { PageHeader } from "@/components/layout/PageHeader";
import { PageTabs } from "@/components/ui/PageTabs";
import {
  Card,
  Empty,
  ErrorState,
  KeyValue,
  Skeleton,
  Status,
  moment,
} from "@/components/ui/Primitives";
import { IngestionSource, ingestionApi } from "@/lib/api";
import {
  INGESTION_DECISION_LABELS,
  INGESTION_SOURCE_KIND_LABELS,
  count,
  ingestionDecisionTone,
} from "@/lib/labels";

/** Порядок решений: от добавленного к отклонённому. */
const DECISION_ORDER = [
  "new_relevant",
  "enrichable",
  "existing",
  "duplicate_variant",
  "questionable",
  "unknown",
  "low_quality",
];

export default function IngestionSourcesPage() {
  const [items, setItems] = useState<IngestionSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    ingestionApi
      .sources()
      .then((response) => {
        setItems(response.items);
        setError("");
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <PageHeader
        title="Внешние источники"
        description="Источники, из которых пополнялся справочник упражнений. Приложение к ним не обращается: импорт выполняется отдельной операцией обслуживания, а справочник остаётся единственным источником для генерации программ."
        tabs={
          <PageTabs
            tabs={INGESTION_TABS}
            root="/ingestion"
            label="Разделы внешних источников"
          />
        }
      />

      {error && <ErrorState message={error} onRetry={load} />}

      {loading && (
        <Card>
          <Skeleton rows={4} />
        </Card>
      )}

      {!loading && !error && items.length === 0 && (
        <Card>
          <Empty
            title="Импорт не выполнялся"
            hint="Ни один внешний источник ещё не прочитан."
          />
        </Card>
      )}

      {!loading &&
        items.map((source) => (
          <Card
            key={source.source_key}
            title={source.name}
            description={
              INGESTION_SOURCE_KIND_LABELS[source.kind] ?? source.kind
            }
            actions={<DecisionCounts counts={source.counts} />}
          >
            <KeyValue
              rows={[
                {
                  key: "Код источника",
                  value: <code>{source.source_key}</code>,
                },
                {
                  key: "Версия",
                  value: source.version ? (
                    <code>{source.version}</code>
                  ) : (
                    <span className="muted">не прочитан</span>
                  ),
                  hint: "Без версии «добавлено 953» не отвечает на вопрос, из какого состояния источника взяты записи.",
                },
                {
                  key: "Прочитан",
                  value: source.retrieved_at ? (
                    moment(source.retrieved_at)
                  ) : (
                    <span className="muted">—</span>
                  ),
                },
                {
                  key: "Записей в источнике",
                  value: count(source.record_count),
                },
                {
                  key: "Страница источника",
                  value: source.homepage ? (
                    <a
                      href={source.homepage}
                      target="_blank"
                      rel="noreferrer noopener"
                    >
                      {source.homepage}
                    </a>
                  ) : null,
                  hidden: !source.homepage,
                },
                {
                  key: "Условия на данные",
                  value: source.data_license,
                  hidden: !source.data_license,
                },
                {
                  key: "Условия на медиа",
                  value: source.media_license,
                  hint: "У данных и медиа разные правообладатели, поэтому условия указаны раздельно.",
                  hidden: !source.media_license,
                },
                {
                  key: "Указание авторства",
                  value: source.attribution,
                  hidden: !source.attribution,
                },
                {
                  key: "Примечание",
                  value: source.notes,
                  hidden: !source.notes,
                },
              ]}
            />
          </Card>
        ))}
    </>
  );
}

function DecisionCounts(props: Readonly<{ counts: Record<string, number> }>) {
  const decisions = DECISION_ORDER.map((decision) => ({
    decision,
    value: props.counts[`decision:${decision}`] ?? 0,
  })).filter((entry) => entry.value > 0);

  // Датасет программ решений не принимает: его записи не являются кандидатами в
  // справочник, и у него в счётчиках только «сопоставлено / не сопоставлено».
  const matched = props.counts["decision:matched"];
  const unmatched = props.counts["decision:unmatched"];

  if (
    decisions.length === 0 &&
    matched === undefined &&
    unmatched === undefined
  ) {
    return (
      <span className="muted">
        Решений нет: источник прочитан, но записи ещё не обработаны.
      </span>
    );
  }

  return (
    <div className="inline-list">
      {decisions.map((entry) => (
        <Status
          key={entry.decision}
          tone={ingestionDecisionTone(entry.decision)}
        >
          {INGESTION_DECISION_LABELS[entry.decision] ?? entry.decision}:{" "}
          {count(entry.value)}
        </Status>
      ))}
      {matched !== undefined && (
        <Status tone="ok">сопоставлено с упражнением: {count(matched)}</Status>
      )}
      {unmatched !== undefined && (
        <Status tone="neutral">
          без упражнения в справочнике: {count(unmatched)}
        </Status>
      )}
    </div>
  );
}
