"use client";

// Задачи, где система обращается к ИИ.
//
// Показываются только задачи, которые действительно выполняются: сервер
// не отдаёт остальные типы. Настройки сгруппированы по смыслу, у каждой —
// объяснение, на что она влияет.

import { useEffect, useState } from "react";

import { Card, Empty, Field, Notice, Status } from "@/components/ui/Primitives";
import { AIEndpointItem, AIModelItem, AIProviderItem, AITaskItem, aiApi } from "@/lib/api";
import { aiTaskHint, aiTaskLabel } from "@/lib/labels";

export default function TasksSection(props: Readonly<{
  tasks: AITaskItem[];
  allModels: AIModelItem[];
  endpoints: Record<number, AIEndpointItem[]>;
  providers: AIProviderItem[];
  promptVersions: number[];
  canWrite: boolean;
  onChanged: (message: string) => void;
  onError: (message: string) => void;
}>) {
  // Модель понятна только вместе с её подключением и сервисом.
  const modelLabel = (pk: number) => {
    const model = props.allModels.find((m) => m.id === pk);
    if (!model) return "удалённая модель";
    const endpoint = Object.values(props.endpoints)
      .flat()
      .find((e) => e.id === model.endpoint_id);
    const provider = props.providers.find((p) => p.id === endpoint?.provider_id);
    const context = [endpoint?.name, provider?.name].filter(Boolean).join(" · ");
    const suffix = model.enabled ? "" : " · выключена";
    return `${model.display_name}${context ? ` — ${context}` : ""}${suffix}`;
  };

  if (props.tasks.length === 0) {
    return (
      <Card title="Задачи">
        <Empty
          title="Задач нет"
          hint="Система не сообщила ни одной задачи, использующей ИИ."
        />
      </Card>
    );
  }

  return (
    <>
      {props.tasks.map((task) => (
        <TaskCard
          key={task.task_type}
          task={task}
          allModels={props.allModels}
          modelLabel={modelLabel}
          promptVersions={props.promptVersions}
          canWrite={props.canWrite}
          onChanged={props.onChanged}
          onError={props.onError}
        />
      ))}
    </>
  );
}

function TaskCard(props: Readonly<{
  task: AITaskItem;
  allModels: AIModelItem[];
  modelLabel: (pk: number) => string;
  promptVersions: number[];
  canWrite: boolean;
  onChanged: (message: string) => void;
  onError: (message: string) => void;
}>) {
  const { task, canWrite } = props;
  const [enabled, setEnabled] = useState(task.enabled);
  const [temperature, setTemperature] = useState(task.temperature);
  const [maxTokens, setMaxTokens] = useState(task.max_tokens ?? 0);
  const [timeoutSeconds, setTimeoutSeconds] = useState(task.timeout_seconds);
  const [promptVersion, setPromptVersion] = useState(task.prompt_version ?? 0);
  const [selected, setSelected] = useState<number[]>(
    task.bindings.map((b) => b.model_id)
  );
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setEnabled(task.enabled);
    setTemperature(task.temperature);
    setMaxTokens(task.max_tokens ?? 0);
    setTimeoutSeconds(task.timeout_seconds);
    setPromptVersion(task.prompt_version ?? 0);
    setSelected(task.bindings.map((b) => b.model_id));
  }, [task]);

  const save = async () => {
    setSaving(true);
    try {
      await aiApi.putTask(task.task_type, {
        enabled,
        temperature,
        max_tokens: maxTokens > 0 ? maxTokens : null,
        timeout_seconds: timeoutSeconds,
        prompt_version: promptVersion > 0 ? promptVersion : null,
        model_ids: selected,
      });
      props.onChanged("Настройки задачи сохранены");
    } catch (e) {
      props.onError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const move = (index: number, direction: -1 | 1) => {
    const next = [...selected];
    const target = index + direction;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setSelected(next);
  };

  const unusedModels = props.allModels.filter((m) => !selected.includes(m.id));

  return (
    <Card
      title={aiTaskLabel(task.task_type)}
      description={aiTaskHint(task.task_type)}
      actions={
        <Status tone={task.enabled ? "ok" : "neutral"}>
          {task.enabled ? "ИИ используется" : "ИИ выключен"}
        </Status>
      }
    >
      {enabled && selected.length === 0 && (
        <Notice tone="warn" title="Не выбрана ни одна модель">
          Задачу нельзя включить без модели — сервер откажет при сохранении.
        </Notice>
      )}

      <label className="check" style={{ marginBottom: 20 }}>
        <input
          type="checkbox"
          checked={enabled}
          disabled={!canWrite}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        Использовать ИИ для этой задачи
      </label>

      <h3 className="section-title">Какие модели использовать</h3>
      <p className="field-hint" style={{ marginTop: 0, marginBottom: 12 }}>
        Первая в списке — основная. Если она не ответит, система попробует
        следующую. Если не ответит ни одна, программу соберёт алгоритмический
        генератор.
      </p>

      {selected.length === 0 ? (
        <p className="field-hint">Модели не выбраны.</p>
      ) : (
        <ol className="steps" style={{ marginBottom: 12 }}>
          {selected.map((pk, index) => (
            <li key={pk}>
              <div className="inline-list">
                <span>
                  {props.modelLabel(pk)}{" "}
                  {index === 0 && <em className="muted">— основная</em>}
                </span>
                {canWrite && (
                  <>
                    <button
                      type="button"
                      className="ghost small"
                      onClick={() => move(index, -1)}
                      disabled={index === 0}
                      aria-label="Поднять выше"
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      className="ghost small"
                      onClick={() => move(index, 1)}
                      disabled={index === selected.length - 1}
                      aria-label="Опустить ниже"
                    >
                      ↓
                    </button>
                    <button
                      type="button"
                      className="ghost small"
                      onClick={() => setSelected(selected.filter((x) => x !== pk))}
                    >
                      убрать
                    </button>
                  </>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}

      {canWrite && unusedModels.length > 0 && (
        <Field
          label="Добавить модель"
          hint="Доступны модели включённых сервисов и подключений."
        >
          <select
            value=""
            onChange={(e) => {
              const pk = Number(e.target.value);
              if (pk) setSelected([...selected, pk]);
            }}
            aria-label="Добавить модель к задаче"
          >
            <option value="">Выберите модель…</option>
            {unusedModels.map((model) => (
              <option key={model.id} value={model.id}>
                {props.modelLabel(model.id)}
              </option>
            ))}
          </select>
        </Field>
      )}

      <h3 className="section-title">Как обращаться к модели</h3>
      <div className="form-grid">
        <Field
          label="Насколько свободно отвечать"
          hint="0 — строго по шаблону, ближе к 1 — больше вариаций. Для программ тренировок обычно 0,5–0,8."
        >
          <input
            type="number"
            min={0}
            max={2}
            step={0.1}
            value={temperature}
            disabled={!canWrite}
            onChange={(e) => setTemperature(Number(e.target.value) || 0)}
            aria-label="Насколько свободно отвечать"
          />
        </Field>

        <Field
          label="Ограничение длины ответа"
          hint="0 — без ограничения. Слишком маленькое значение обрежет программу, и она не пройдёт проверку."
        >
          <input
            type="number"
            min={0}
            value={maxTokens}
            disabled={!canWrite}
            onChange={(e) => setMaxTokens(Number(e.target.value) || 0)}
            aria-label="Ограничение длины ответа"
          />
        </Field>

        <Field
          label="Сколько ждать ответа, секунд"
          hint="Если модель не ответит за это время, система перейдёт к следующей или соберёт программу без ИИ."
        >
          <input
            type="number"
            min={1}
            max={600}
            value={timeoutSeconds}
            disabled={!canWrite}
            onChange={(e) => setTimeoutSeconds(Number(e.target.value) || 120)}
            aria-label="Сколько ждать ответа"
          />
        </Field>

        <Field
          label="Версия инструкции"
          hint={
            props.promptVersions.length > 0
              ? `0 — версия по умолчанию. Сохранённые версии: ${props.promptVersions
                  .map((v) => `№${v}`)
                  .join(", ")}.`
              : "0 — версия по умолчанию из файлов проекта. Своих версий пока не сохранено."
          }
        >
          <input
            type="number"
            min={0}
            value={promptVersion}
            disabled={!canWrite}
            onChange={(e) => setPromptVersion(Number(e.target.value) || 0)}
            aria-label="Версия инструкции"
          />
        </Field>
      </div>

      {canWrite && (
        <div className="button-row" style={{ marginTop: 20 }}>
          <button type="button" className="primary" onClick={save} disabled={saving}>
            {saving ? "Сохраняем…" : "Сохранить"}
          </button>
        </div>
      )}
    </Card>
  );
}
