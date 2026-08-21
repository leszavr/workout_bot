"use client";

// Панель готовности AI-генерации: чек-лист шагов настройки, эффективная
// цепочка моделей и фактическая стратегия генерации. Отвечает на вопрос
// «заработает ли AI прямо сейчас», который раньше нельзя было узнать из UI.

import { AIReadinessReport } from "@/lib/api";
import {
  aiReadinessIcon,
  aiReadinessStatusLabel,
  aiTaskLabel,
  generatorLabel,
} from "@/lib/labels";

const STATUS_COLORS: Record<string, string> = {
  ok: "#166534",
  warning: "#92400e",
  missing: "#4b5563",
  failed: "#b91c1c",
};

export default function AIReadinessPanel(props: Readonly<{
  report: AIReadinessReport | null;
  refreshing: boolean;
  onRefresh: () => void;
}>) {
  const { report } = props;

  return (
    <div className="card">
      <div className="toolbar" style={{ alignItems: "center", marginBottom: 8 }}>
        <h2 className="section-title" style={{ margin: 0 }}>
          Готовность AI-генерации
        </h2>
        {report && (
          <span className={report.ready ? "badge confirmed" : "badge draft"}>
            {report.ready ? "готово к работе" : "не готово"}
          </span>
        )}
        <button type="button" onClick={props.onRefresh} disabled={props.refreshing}>
          {props.refreshing ? "Обновление..." : "Обновить"}
        </button>
      </div>

      {!report ? (
        <p className="muted">Загрузка...</p>
      ) : (
        <>
          <p className="muted" style={{ marginTop: 0 }}>
            Задача: {aiTaskLabel(report.task_type)}. Пока хотя бы один
            обязательный шаг не выполнен, программы генерируются
            детерминированным генератором.
          </p>

          <div>
            {report.checks.map((check) => (
              <div
                key={check.key}
                style={{
                  display: "flex",
                  gap: 10,
                  alignItems: "baseline",
                  padding: "7px 0",
                  borderBottom: "1px solid var(--border)",
                }}
              >
                <span
                  aria-label={aiReadinessStatusLabel(check.status)}
                  title={aiReadinessStatusLabel(check.status)}
                  style={{
                    width: 16,
                    fontWeight: 700,
                    color: STATUS_COLORS[check.status] ?? "#4b5563",
                  }}
                >
                  {aiReadinessIcon(check.status)}
                </span>
                <strong style={{ minWidth: 180 }}>
                  {check.title}
                  {!check.blocking && (
                    <span className="muted" style={{ fontWeight: 400 }}>
                      {" "}
                      (не блокирует)
                    </span>
                  )}
                </strong>
                <span style={{ flex: 1 }}>
                  {check.detail}
                  {check.action && check.status !== "ok" && (
                    <span className="muted"> → {check.action}</span>
                  )}
                </span>
              </div>
            ))}
          </div>

          <div className="section-title">Эффективная цепочка моделей</div>
          {report.chain.length === 0 ? (
            <p className="muted">
              Нет доступных моделей: AI-запрос выполнить нечем.
            </p>
          ) : (
            <ol style={{ margin: "0 0 8px 20px" }}>
              {report.chain.map((entry) => (
                <li key={`${entry.model_pk}-${entry.priority}`}>
                  {entry.is_primary ? "Основная: " : `Резервная ${entry.priority - 1}: `}
                  <strong>{entry.model_display_name}</strong>{" "}
                  <span className="muted">
                    ({entry.model_id} · {entry.endpoint} · {entry.provider})
                  </span>
                </li>
              ))}
            </ol>
          )}

          <div className="section-title">Стратегия генерации программ</div>
          <p className="muted" style={{ marginTop: 0 }}>
            Основной генератор:{" "}
            <strong>{generatorLabel(report.generation.primary_generator)}</strong>,
            резервный:{" "}
            <strong>{generatorLabel(report.generation.fallback_generator)}</strong>.
            Автогенерация после анкеты:{" "}
            {report.generation.auto_generate_after_finalize ? "включена" : "выключена"}.
            Эти значения задаются переменными окружения сервера
            (PROGRAM_PRIMARY_GENERATOR, PROGRAM_FALLBACK_GENERATOR,
            AUTO_GENERATE_PROGRAM_AFTER_FINALIZE) и не меняются из интерфейса.
          </p>
        </>
      )}
    </div>
  );
}
