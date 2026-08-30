"use client";

// Карточка одной операции генерации.
//
// Отвечает на вопрос «что именно происходило»: какие модели отвечали в каком
// порядке, прошёл ли первый ответ проверку, сколько раз запрашивалось
// исправление и почему модель была оставлена. Промптов и ответов моделей здесь
// нет: журнал их не хранит — они содержат данные анкеты.

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Card, Empty, Skeleton, Status, Tag } from "@/components/ui/Primitives";
import {
  AnalyticsGenerationDetail,
  aiApi,
  getToken,
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

export default function GenerationDetailPage() {
  const params = useParams<{ jobId: string }>();
  const [detail, setDetail] = useState<AnalyticsGenerationDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    aiApi
      .analyticsGeneration(params.jobId)
      .then(setDetail)
      .catch((e) => setError((e as Error).message));
  }, [params.jobId]);

  if (error) {
    return (
      <>
        <div className="error">{error}</div>
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
      <div className="page-head">
        <h1 className="page-title">Генерация от {dateTime(detail.created_at)}</h1>
        <p className="page-subtitle">
          {generationTriggerLabel(detail.trigger)}. Запрошенный генератор:{" "}
          {generatorLabel(detail.requested_generator)}.
        </p>
        <div className="field-row" style={{ marginTop: "var(--s-3)" }}>
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
        <div className="kv">
          <div className="k">Что получилось</div>
          <div>
            {detail.program_id ? (
              <Link
                href={`/programs/${detail.program_id}${
                  detail.program_version ? `?version=${detail.program_version}` : ""
                }`}
              >
                {detail.program_title || "программа"}
                {detail.program_version ? ` (версия ${detail.program_version})` : ""}
              </Link>
            ) : (
              <span className="muted">
                программы нет: операция завершилась отказом
              </span>
            )}
          </div>
          {detail.program_status && (
            <>
              <div className="k">Состояние программы</div>
              <div>{statusLabel(detail.program_status)}</div>
            </>
          )}
          <div className="k">Кто собрал</div>
          <div>
            {detail.actual_generator
              ? generatorLabel(detail.actual_generator)
              : "—"}
          </div>
          {detail.fallback_reason_code && (
            <>
              <div className="k">Почему без ИИ</div>
              <div>
                {aiFallbackReasonLabel(detail.fallback_reason_code)}
                {detail.fallback_reason && (
                  <div className="muted">{detail.fallback_reason}</div>
                )}
              </div>
            </>
          )}
          {detail.last_error_code && (
            <>
              <div className="k">Код отказа</div>
              <div>
                {generationErrorLabel(detail.last_error_code)}
                {detail.last_error_message && (
                  <div className="muted">{detail.last_error_message}</div>
                )}
              </div>
            </>
          )}
          <div className="k">Модель и инструкция</div>
          <div>
            {detail.model ? <code>{detail.model}</code> : "—"}
            {detail.provider && <span className="muted"> · {detail.provider}</span>}
            {detail.prompt_version !== null && (
              <div className="muted">инструкция v{detail.prompt_version}</div>
            )}
          </div>
          <div className="k">Длительность</div>
          <div>
            {duration(detail.duration_ms)}
            <div className="muted">
              запуск: {dateTime(detail.started_at)}, завершение:{" "}
              {dateTime(detail.completed_at)}
            </div>
          </div>
          <div className="k">Попыток операции</div>
          <div>{count(detail.attempts)}</div>
          <div className="k">Анкета</div>
          <div>
            <Link href={`/profiles/${detail.profile_id}`}>
              {detail.profile_id}
            </Link>
          </div>
        </div>
      </Card>

      <Card
        title="Попытки моделей"
        description="Порядок, в котором система обращалась к моделям. Резервная модель вызывается только после того, как предыдущая не дала пригодный результат."
      >
        {detail.attempt_details.length === 0 ? (
          <Empty
            title="Обращений к моделям не было"
            hint={attemptsAbsenceHint(detail)}
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Порядок</th>
                  <th>Модель</th>
                  <th>Сервис</th>
                  <th>Первый ответ</th>
                  <th>Исправлений</th>
                  <th>Исход</th>
                  <th>Подробности</th>
                </tr>
              </thead>
              <tbody>
                {detail.attempt_details.map((attempt, index) => (
                  <tr key={`${attempt.model_id}-${index}`}>
                    <td>
                      {attempt.priority}
                      <div className="muted" style={{ fontSize: 12 }}>
                        {attempt.is_primary ? "основная" : "резервная"}
                      </div>
                    </td>
                    <td>
                      <code>{attempt.model_id}</code>
                    </td>
                    <td>{attempt.provider}</td>
                    <td>
                      {attempt.initial_valid ? (
                        "прошёл проверку"
                      ) : (
                        <span className="muted">не прошёл</span>
                      )}
                    </td>
                    <td>{count(attempt.repair_attempts)}</td>
                    <td>
                      <Status tone={aiAttemptOutcomeTone(attempt.outcome)}>
                        {aiAttemptOutcomeLabel(attempt.outcome)}
                      </Status>
                    </td>
                    <td>
                      {attempt.error_type && (
                        <div className="muted">{attempt.error_type}</div>
                      )}
                      {attempt.detail ?? (!attempt.error_type ? "—" : null)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card
        title="Обращения к ИИ"
        description="Отдельные вызовы модели. Их больше, чем попыток: каждый запрос на исправление — тоже вызов."
      >
        {detail.calls.length === 0 ? (
          <Empty
            title="Вызовов не записано"
            hint="Либо ИИ не вызывался, либо вызовы сделаны до появления связи журнала с операцией генерации: у прежних записей её нет, и восстановить её нельзя."
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Время</th>
                  <th>Модель</th>
                  <th>Подключение</th>
                  <th>Итог</th>
                  <th>Ответ</th>
                  <th>Токены</th>
                </tr>
              </thead>
              <tbody>
                {detail.calls.map((call) => (
                  <tr key={call.id}>
                    <td>{dateTime(call.created_at)}</td>
                    <td>{call.model ? <code>{call.model}</code> : "—"}</td>
                    <td>
                      {call.endpoint ?? "—"}
                      {call.provider && (
                        <div className="muted" style={{ fontSize: 12 }}>
                          {call.provider}
                        </div>
                      )}
                    </td>
                    <td>
                      <Status tone={call.status === "success" ? "ok" : "bad"}>
                        {aiUsageStatusLabel(call.status)}
                      </Status>
                      {call.error_type && (
                        <div className="muted" style={{ fontSize: 12 }}>
                          {call.error_type}
                        </div>
                      )}
                    </td>
                    <td>{duration(call.latency_ms)}</td>
                    <td>
                      {count(call.total_tokens)}
                      <div className="muted" style={{ fontSize: 12 }}>
                        запрос: {count(call.input_tokens)}, ответ:{" "}
                        {count(call.output_tokens)}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="button-row">
        <Link className="btn" href="/ai/generations">
          К списку генераций
        </Link>
        <Link className="btn" href="/ai/analytics">
          К сводке
        </Link>
      </div>
    </>
  );
}
