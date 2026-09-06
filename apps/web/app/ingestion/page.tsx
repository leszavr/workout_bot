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

import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import IngestionTabs from "@/components/ingestion/IngestionTabs";
import { Card, Empty, Skeleton, Status, moment } from "@/components/ui/Primitives";
import { IngestionSource, getToken, ingestionApi } from "@/lib/api";
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

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    ingestionApi
      .sources()
      .then((response) => setItems(response.items))
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <div className="page-head">
          <h1 className="page-title">Внешние источники</h1>
          <p className="page-subtitle">
            Источники, из которых пополнялся справочник упражнений. Приложение к
            ним не обращается: импорт выполняется отдельной операцией
            обслуживания, а справочник остаётся единственным источником для
            генерации программ.
          </p>
        </div>

        <IngestionTabs />

        {error && <div className="error">{error}</div>}

        {loading && (
          <Card>
            <Skeleton rows={4} />
          </Card>
        )}

        {!loading && items.length === 0 && (
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
            >
              <div className="kv">
                <div className="k">Код источника</div>
                <div>
                  <code>{source.source_key}</code>
                </div>

                <div className="k">Версия</div>
                <div>
                  {source.version ? (
                    <code>{source.version}</code>
                  ) : (
                    <span className="muted">не прочитан</span>
                  )}
                </div>

                <div className="k">Прочитан</div>
                <div>
                  {source.retrieved_at ? (
                    moment(source.retrieved_at)
                  ) : (
                    <span className="muted">—</span>
                  )}
                </div>

                <div className="k">Записей в источнике</div>
                <div>{count(source.record_count)}</div>

                {source.homepage && (
                  <>
                    <div className="k">Страница источника</div>
                    <div>
                      <a
                        href={source.homepage}
                        target="_blank"
                        rel="noreferrer noopener"
                      >
                        {source.homepage}
                      </a>
                    </div>
                  </>
                )}

                {source.data_license && (
                  <>
                    <div className="k">Условия на данные</div>
                    <div>{source.data_license}</div>
                  </>
                )}

                {source.media_license && (
                  <>
                    <div className="k">Условия на медиа</div>
                    <div>{source.media_license}</div>
                  </>
                )}

                {source.attribution && (
                  <>
                    <div className="k">Указание авторства</div>
                    <div>{source.attribution}</div>
                  </>
                )}

                {source.notes && (
                  <>
                    <div className="k">Примечание</div>
                    <div>{source.notes}</div>
                  </>
                )}
              </div>

              <h3 className="section-title">Решения по записям</h3>
              <DecisionCounts counts={source.counts} />
            </Card>
          ))}
      </main>
    </div>
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

  if (decisions.length === 0 && matched === undefined && unmatched === undefined) {
    return (
      <p className="muted">
        Решений нет: источник прочитан, но записи ещё не обработаны.
      </p>
    );
  }

  return (
    <div className="inline-list">
      {decisions.map((entry) => (
        <Status key={entry.decision} tone={ingestionDecisionTone(entry.decision)}>
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
