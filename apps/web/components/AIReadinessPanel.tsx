"use client";

// Готовность ИИ: пошаговый чек-лист настройки и что реально будет вызвано.
//
// Отвечает на главный вопрос администратора — «заработает ли ИИ прямо
// сейчас». Каждый шаг называет причину и следующее действие, чтобы не
// приходилось догадываться, чего не хватает.

import { Card, Skeleton, Status, Tag } from "@/components/ui/Primitives";
import { AIReadinessReport } from "@/lib/api";
import {
  aiReadinessStatusLabel,
  generatorLabel,
  readinessTone,
} from "@/lib/labels";

export default function AIReadinessPanel(props: Readonly<{
  report: AIReadinessReport | null;
  refreshing: boolean;
  onRefresh: () => void;
}>) {
  const { report } = props;

  const actions = (
    <>
      {report && (
        <Status tone={report.ready ? "ok" : "warn"}>
          {report.ready ? "ИИ готов к работе" : "ИИ пока не заработает"}
        </Status>
      )}
      <button
        type="button"
        className="small"
        onClick={props.onRefresh}
        disabled={props.refreshing}
      >
        {props.refreshing ? "Обновляем…" : "Обновить"}
      </button>
    </>
  );

  return (
    <Card
      title="Готовность"
      description="Пока есть хотя бы один невыполненный обязательный шаг, программы собирает алгоритмический генератор."
      actions={actions}
    >
      {!report ? (
        <Skeleton rows={4} />
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th style={{ width: 190 }}>Шаг</th>
                  <th style={{ width: 170 }}>Состояние</th>
                  <th>Подробности</th>
                </tr>
              </thead>
              <tbody>
                {report.checks.map((check) => (
                  <tr key={check.key}>
                    <td>
                      <strong>{check.title}</strong>
                      {!check.blocking && (
                        <div className="field-hint">не обязателен</div>
                      )}
                    </td>
                    <td>
                      <Status tone={readinessTone(check.status)}>
                        {aiReadinessStatusLabel(check.status)}
                      </Status>
                    </td>
                    <td>
                      {check.detail}
                      {check.action && check.status !== "ok" && (
                        <div className="field-hint">Что сделать: {check.action}</div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h3 className="section-title">Что будет вызвано</h3>
          {report.chain.length === 0 ? (
            <p className="text-secondary" style={{ margin: 0 }}>
              Ни одной доступной модели — обращаться к ИИ нечем.
            </p>
          ) : (
            <ol className="steps">
              {report.chain.map((entry) => (
                <li key={`${entry.model_pk}-${entry.priority}`}>
                  <div>
                    <strong>{entry.model_display_name}</strong>{" "}
                    <Tag tone={entry.is_primary ? "info" : "neutral"}>
                      {entry.is_primary ? "основная" : "резервная"}
                    </Tag>
                    <div className="field-hint">
                      <code>{entry.model_id}</code> · подключение «{entry.endpoint}»
                      {" "}· сервис «{entry.provider}»
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          )}

          <h3 className="section-title">Порядок сборки программ</h3>
          <div className="kv">
            <div className="k">Сначала пробуем</div>
            <div>
              <strong>{generatorLabel(report.generation.primary_generator)}</strong>
            </div>
            <div className="k">Если не получилось</div>
            <div>
              <strong>{generatorLabel(report.generation.fallback_generator)}</strong>
            </div>
            <div className="k">Собирать сразу после анкеты</div>
            <div>
              {report.generation.auto_generate_after_finalize ? "да" : "нет"}
            </div>
          </div>
          <p className="field-hint" style={{ marginTop: 12 }}>
            Этот порядок задаётся в настройках сервера и через интерфейс не
            меняется.
          </p>
        </>
      )}
    </Card>
  );
}
