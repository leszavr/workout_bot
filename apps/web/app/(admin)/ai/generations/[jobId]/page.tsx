"use client";

// Карточка одной операции генерации.
//
// Отвечает на вопрос «что именно происходило»: какие модели отвечали в каком
// порядке, прошёл ли первый ответ проверку, сколько раз запрашивалось
// исправление и почему модель была оставлена. Промптов и ответов моделей здесь
// нет: журнал их не хранит — они содержат данные анкеты.
//
// Страница лежит внутри раздела ИИ, поэтому получает его вкладки и меню от
// layout. До перехода на общий каркас она рендерилась без навигации вообще:
// открыв её из списка, вернуться можно было только кнопкой в конце страницы.

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { DataColumn, DataTable } from "@/components/ui/DataTable";
import {
  Card,
  ErrorState,
  KeyValue,
  Skeleton,
  Status,
  Tag,
} from "@/components/ui/Primitives";
import {
  AnalyticsAttemptDetail,
  AnalyticsCallRow,
  AnalyticsGenerationDetail,
  aiApi,
} from "@/lib/api";
import {
  aiAttemptOutcomeLabel,
  aiAttemptOutcomeTone,
  aiFallbackReasonLabel,
  aiUsageStatusLabel,
  count,
  dateTime,
  duration,
  generationErrorLabel,
  generationStatusLabel,
  generationStatusTone,
  generationTriggerLabel,
  generatorLabel,
  statusLabel,
} from "@/lib/labels";

// Причины отсутствующих попыток различаются по смыслу, и общая формулировка
// «см. код отказа» была бы неверной там, где отказ произошёл до вызова моделей:
// в этом случае кода модели просто не существует.
const CONFIGURATION_FALLBACK_REASONS = new Set([
  "ai_not_configured",
  "provider_unavailable",
  "endpoint_unavailable",
  "connection_not_tested",
  "model_unavailable",
  "unsupported_protocol",
  "task_disabled",
  "task_not_ready",
  "generator_not_configured",
]);

function attemptsAbsenceHint(detail: AnalyticsGenerationDetail): string {
  if (detail.actual_generator === "deterministic" && !detail.fallback_used) {
    return "Программу собрал алгоритмический генератор: ИИ не запрашивался.";
  }
  const reason = detail.fallback_reason_code ?? detail.last_error_code ?? "";
  if (CONFIGURATION_FALLBACK_REASONS.has(reason)) {
    return (
      "Запрос к ИИ не выполнялся: конфигурация не позволила его сделать — " +
      "см. причину выше. Это лечится настройкой подключения, а не повтором."
    );
  }
  return "До вызова моделей дело не дошло: операция остановилась раньше — см. код отказа.";
}

const ATTEMPT_COLUMNS: ReadonlyArray<DataColumn<AnalyticsAttemptDetail>> = [
  {
    key: "priority",
    header: "Порядок",
    numeric: true,
    render: (attempt) => (
      <>
        {attempt.priority}
        <div className="muted" style={{ fontSize: 12 }}>
          {attempt.is_primary ? "основная" : "резервная"}
        </div>
      </>
    ),
  },
  {
    key: "model",
    header: "Модель",
    render: (attempt) => <code>{attempt.model_id}</code>,
  },
  { key: "provider", header: "Сервис", render: (attempt) => attempt.provider },
  {
    key: "initial",
    header: "Первый ответ",
    hint: "Прошёл ли первый ответ модели проверку структуры без исправлений.",
    render: (attempt) =>
      attempt.initial_valid ? (
        "прошёл проверку"
      ) : (
        <span className="muted">не прошёл</span>
      ),
  },
  {
    key: "repairs",
    header: "Исправлений",
    numeric: true,
    render: (attempt) => count(attempt.repair_attempts),
  },
  {
    key: "outcome",
    header: "Исход",
    render: (attempt) => (
      <Status tone={aiAttemptOutcomeTone(attempt.outcome)}>
        {aiAttemptOutcomeLabel(attempt.outcome)}
      </Status>
    ),
  },
  {
    key: "detail",
    header: "Подробности",
    render: (attempt) => (
      <>
        {attempt.error_type && <div className="muted">{attempt.error_type}</div>}
        {attempt.detail ?? (!attempt.error_type ? "—" : null)}
      </>
    ),
  },
];

const CALL_COLUMNS: ReadonlyArray<DataColumn<AnalyticsCallRow>> = [
  { key: "time", header: "Время", render: (call) => dateTime(call.created_at) },
  {
    key: "model",
    header: "Модель",
    render: (call) => (call.model ? <code>{call.model}</code> : "—"),
  },
  {
    key: "endpoint",
    header: "Подключение",
    render: (call) => (
      <>
        {call.endpoint ?? "—"}
        {call.provider && (
          <div className="muted" style={{ fontSize: 12 }}>
            {call.provider}
          </div>
        )}
      </>
    ),
  },
  {
    key: "status",
    header: "Итог",
    render: (call) => (
      <>
        <Status tone={call.status === "success" ? "ok" : "bad"}>
          {aiUsageStatusLabel(call.status)}
        </Status>
        {call.error_type && (
          <div className="muted" style={{ fontSize: 12 }}>
            {call.error_type}
          </div>
        )}
      </>
    ),
  },
  {
    key: "latency",
    header: "Ответ",
    numeric: true,
    render: (call) => duration(call.latency_ms),
  },
  {
    key: "tokens",
    header: "Токены",
    numeric: true,
    render: (call) => (
      <>
        {count(call.total_tokens)}
        <div className="muted" style={{ fontSize: 12 }}>
          запрос: {count(call.input_tokens)}, ответ: {count(call.output_tokens)}
        </div>
      </>
    ),
  },
];

export default function GenerationDetailPage() {
  const params = useParams<{ jobId: string }>();
  const [detail, setDetail] = useState<AnalyticsGenerationDetail | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    aiApi
      .analyticsGeneration(params.jobId)
      .then((response) => {
        setDetail(response);
        setError("");
      })
      .catch((e) => setError((e as Error).message));
  }, [params.jobId]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) {
    return (
      <>
        <ErrorState message={error} onRetry={load} />
        <Link className="btn" href="/ai/generations">
          К списку генераций
        </Link>
      </>
    );
  }

  if (!detail) {
    return (
      <Card>
        <Skeleton rows={6} />
      </Card>
    );
  }

  return (
    <>
      {/* h2, а не h1: заголовок раздела уже стоит в layout, и второй h1 на
          странице сломал бы структуру документа для скринридера. */}
      <div className="page-head">
        <div className="page-head-row">
          <div className="page-head-text">
            <h2 className="page-title">
              Генерация от {dateTime(detail.created_at)}
            </h2>
            <p className="page-subtitle">
              {generationTriggerLabel(detail.trigger)}. Запрошенный генератор:{" "}
              {generatorLabel(detail.requested_generator)}.
            </p>
          </div>
          <div className="page-head-actions">
            <Link className="btn small" href="/ai/generations">
              К списку генераций
            </Link>
          </div>
        </div>
        <div className="inline-list" style={{ marginTop: "var(--s-3)" }}>
          <Status tone={generationStatusTone(detail.status)}>
            {generationStatusLabel(detail.status)}
          </Status>
          {detail.fallback_used && (
            <Tag tone="warn">программу собрал алгоритм вместо ИИ</Tag>
          )}
          {detail.repaired && <Tag tone="warn">принято после исправления</Tag>}
        </div>
      </div>

      <Card title="Итог">
        <KeyValue
          rows={[
            {
              key: "Что получилось",
              value: detail.program_id ? (
                <Link
                  href={`/programs/${detail.program_id}${
                    detail.program_version
                      ? `?version=${detail.program_version}`
                      : ""
                  }`}
                >
                  {detail.program_title || "программа"}
                  {detail.program_version
                    ? ` (версия ${detail.program_version})`
                    : ""}
                </Link>
              ) : (
                <span className="muted">
                  программы нет: операция завершилась отказом
                </span>
              ),
            },
            {
              key: "Состояние программы",
              value: detail.program_status
                ? statusLabel(detail.program_status)
                : null,
              hidden: !detail.program_status,
            },
            {
              key: "Кто собрал",
              value: detail.actual_generator
                ? generatorLabel(detail.actual_generator)
                : "—",
            },
            {
              key: "Почему без ИИ",
              value: detail.fallback_reason_code
                ? aiFallbackReasonLabel(detail.fallback_reason_code)
                : null,
              hint: detail.fallback_reason ?? undefined,
              hidden: !detail.fallback_reason_code,
            },
            {
              key: "Код отказа",
              value: detail.last_error_code
                ? generationErrorLabel(detail.last_error_code)
                : null,
              hint: detail.last_error_message ?? undefined,
              hidden: !detail.last_error_code,
            },
            {
              key: "Модель и инструкция",
              value: (
                <>
                  {detail.model ? <code>{detail.model}</code> : "—"}
                  {detail.provider && (
                    <span className="muted"> · {detail.provider}</span>
                  )}
                  {detail.prompt_version !== null && (
                    <div className="muted">
                      инструкция v{detail.prompt_version}
                    </div>
                  )}
                </>
              ),
            },
            {
              key: "Длительность",
              value: duration(detail.duration_ms),
              hint: `запуск: ${dateTime(detail.started_at)}, завершение: ${dateTime(detail.completed_at)}`,
            },
            { key: "Попыток операции", value: count(detail.attempts) },
            {
              key: "Анкета",
              value: (
                <Link href={`/profiles/${detail.profile_id}`}>
                  {detail.profile_id}
                </Link>
              ),
            },
          ]}
        />
      </Card>

      <Card
        title="Попытки моделей"
        description="Порядок, в котором система обращалась к моделям. Резервная модель вызывается только после того, как предыдущая не дала пригодный результат."
      >
        <DataTable
          columns={ATTEMPT_COLUMNS}
          rows={detail.attempt_details}
          rowKey={(attempt) => `${attempt.model_id}-${attempt.priority}`}
          emptyTitle="Обращений к моделям не было"
          emptyHint={attemptsAbsenceHint(detail)}
        />
      </Card>

      <Card
        title="Обращения к ИИ"
        description="Отдельные вызовы модели. Их больше, чем попыток: каждый запрос на исправление — тоже вызов."
      >
        <DataTable
          columns={CALL_COLUMNS}
          rows={detail.calls}
          rowKey={(call) => String(call.id)}
          emptyTitle="Вызовов не записано"
          emptyHint="Либо ИИ не вызывался, либо вызовы сделаны до появления связи журнала с операцией генерации: у прежних записей её нет, и восстановить её нельзя."
        />
      </Card>
    </>
  );
}
