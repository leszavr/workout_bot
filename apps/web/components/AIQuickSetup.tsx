"use client";

// Подключение ИИ за один шаг.
//
// Основной путь настройки: вместо обхода трёх уровней вложенности —
// одна форма. Порядок действий виден пользователю, и задача включается
// только если проверка связи прошла: иначе получилась бы настройка,
// которая гарантированно не работает.
//
// Своего отдельного пути на сервере у мастера нет — используются те же
// запросы, что и на вкладке «Подключения».

import { useState } from "react";

import { Card, Field, Notice, Status } from "@/components/ui/Primitives";
import { AIDiscoveredModel, aiApi } from "@/lib/api";

type StepStatus = "pending" | "running" | "ok" | "error";

interface Step {
  key: string;
  title: string;
  status: StepStatus;
  detail?: string;
}

const STEP_TITLES: Array<{ key: string; title: string }> = [
  { key: "provider", title: "Создаём сервис" },
  { key: "endpoint", title: "Сохраняем адрес и ключ доступа" },
  { key: "model", title: "Добавляем модель" },
  { key: "connection", title: "Проверяем связь" },
  { key: "task", title: "Включаем создание программ через ИИ" },
];

const STATUS_LABELS: Record<StepStatus, string> = {
  pending: "ожидает",
  running: "выполняется",
  ok: "готово",
  error: "ошибка",
};

const STATUS_TONES: Record<StepStatus, "neutral" | "info" | "ok" | "bad"> = {
  pending: "neutral",
  running: "info",
  ok: "ok",
  error: "bad",
};

/** Короткое имя для сервиса: техническое поле, пользователь его не вводит. */
function slugify(value: string): string {
  const slug = value
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "ai-service";
}

export default function AIQuickSetup(props: Readonly<{
  taskType: string;
  onFinished: (message: string) => void;
  onError: (message: string) => void;
}>) {
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [modelId, setModelId] = useState("");
  const [steps, setSteps] = useState<Step[]>([]);
  const [running, setRunning] = useState(false);
  // Список моделей запрашивается у сервиса по введённому адресу и ключу:
  // переписывать идентификатор из документации вручную не нужно.
  const [models, setModels] = useState<AIDiscoveredModel[] | null>(null);
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelsError, setModelsError] = useState("");
  const [modelFilter, setModelFilter] = useState("");

  const ready = name.trim() && baseUrl.trim() && modelId.trim();

  const loadModels = async () => {
    setLoadingModels(true);
    setModelsError("");
    try {
      const res = await aiApi.probeModels(baseUrl.trim(), apiKey.trim() || undefined);
      setModels(res.items);
      if (res.items.length === 1) setModelId(res.items[0].model_id);
    } catch (e) {
      setModels(null);
      setModelsError((e as Error).message);
    } finally {
      setLoadingModels(false);
    }
  };

  const update = (key: string, status: StepStatus, detail?: string) =>
    setSteps((prev) =>
      prev.map((step) => (step.key === key ? { ...step, status, detail } : step))
    );

  const run = async () => {
    setRunning(true);
    setSteps(
      STEP_TITLES.map((step) => ({ ...step, status: "pending" as StepStatus }))
    );
    try {
      update("provider", "running");
      const provider = await aiApi.createProvider({
        name: name.trim(),
        slug: slugify(name),
        protocol: "openai_compatible",
      });
      update("provider", "ok", provider.name);

      update("endpoint", "running");
      const endpoint = await aiApi.createEndpoint(provider.id, {
        name: `Основное подключение`,
        base_url: baseUrl.trim(),
        api_key: apiKey.trim() || undefined,
      });
      update(
        "endpoint",
        "ok",
        `${endpoint.base_url}${endpoint.has_api_key ? " · ключ сохранён" : " · без ключа"}`
      );

      update("model", "running");
      const model = await aiApi.createModel(endpoint.id, {
        model_id: modelId.trim(),
        display_name: modelId.trim(),
      });
      update("model", "ok", model.model_id);

      update("connection", "running");
      const test = await aiApi.testEndpoint(endpoint.id);
      if (!test.success) {
        update("connection", "error", test.message ?? "связь не установлена");
        update("task", "error", "Не включили: сначала должна пройти проверка связи");
        props.onFinished(
          "Сервис, подключение и модель созданы, но связь не установлена. " +
            "Проверьте адрес, ключ доступа и название модели, затем повторите " +
            "проверку на вкладке «Подключения»."
        );
        return;
      }
      update("connection", "ok", `ответ за ${test.latency_ms} мс`);

      update("task", "running");
      await aiApi.putTask(props.taskType, {
        enabled: true,
        model_ids: [model.id],
      });
      update("task", "ok", "включено");
      setName("");
      setBaseUrl("");
      setApiKey("");
      setModelId("");
      props.onFinished("ИИ подключён: связь проверена, создание программ включено");
    } catch (e) {
      const message = (e as Error).message;
      setSteps((prev) =>
        prev.map((step) =>
          step.status === "running"
            ? { ...step, status: "error" as StepStatus, detail: message }
            : step
        )
      );
      props.onError(message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <Card
      title="Подключить ИИ"
      description="Укажите адрес и ключ, выберите модель из списка сервиса — остальное система сделает сама: создаст сервис, сохранит ключ, добавит модель, проверит связь и включит создание программ через ИИ."
    >
      <div className="form-grid">
        <Field
          label="Название сервиса"
          hint="Как вы будете его узнавать в списке. Например: OpenAI, Яндекс, свой сервер."
        >
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="OpenAI"
            aria-label="Название сервиса"
          />
        </Field>

        <Field
          label="Адрес сервиса"
          hint="Ссылка из документации поставщика, обычно заканчивается на /v1. Подходят сервисы, совместимые с OpenAI."
        >
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.openai.com/v1"
            aria-label="Адрес сервиса"
          />
        </Field>

        <Field
          label="Ключ доступа"
          hint="Выдаётся в личном кабинете поставщика. Хранится в зашифрованном виде и больше никогда не показывается. Некоторым своим серверам ключ не нужен — тогда оставьте пустым."
        >
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="Оставьте пустым, если не требуется"
            autoComplete="off"
            aria-label="Ключ доступа"
          />
        </Field>
      </div>

      <h3 className="section-title">Модель</h3>
      {models === null ? (
        <>
          <p className="field-hint">
            {modelsError
              ? `Список получить не удалось: ${modelsError} Укажите название модели вручную — точно как в документации сервиса.`
              : "Нажмите «Показать доступные модели» — система спросит список у сервиса по указанному адресу и ключу."}
          </p>
          <div className="button-row" style={{ marginTop: 8 }}>
            <button
              type="button"
              onClick={loadModels}
              disabled={loadingModels || baseUrl.trim().length < 8}
            >
              {loadingModels ? "Спрашиваем…" : "Показать доступные модели"}
            </button>
          </div>
          {modelsError && (
            <Field
              label="Название модели"
              hint="Система передаёт это значение как есть."
            >
              <input
                type="text"
                value={modelId}
                onChange={(e) => setModelId(e.target.value)}
                placeholder="gpt-4o-mini"
                aria-label="Название модели"
              />
            </Field>
          )}
        </>
      ) : (
        <>
          <Field
            label="Поиск по списку"
            hint={`Сервис предоставляет ${models.length} шт. Выберите одну — остальные можно добавить позже на вкладке «Подключения».`}
          >
            <input
              type="text"
              value={modelFilter}
              onChange={(e) => setModelFilter(e.target.value)}
              placeholder="часть названия"
              aria-label="Поиск модели"
            />
          </Field>
          <div className="pick-list" style={{ marginTop: 8 }}>
            {models
              .filter((m) =>
                modelFilter.trim()
                  ? m.model_id
                      .toLowerCase()
                      .includes(modelFilter.trim().toLowerCase())
                  : true
              )
              .map((m) => (
                <label key={m.model_id} className="pick-list-item">
                  <input
                    type="radio"
                    name="quick-setup-model"
                    checked={modelId === m.model_id}
                    onChange={() => setModelId(m.model_id)}
                  />
                  <span className="pick-list-text">
                    <code>{m.model_id}</code>
                    {m.owned_by && (
                      <span className="field-hint" style={{ margin: 0 }}>
                        поставщик модели: {m.owned_by}
                      </span>
                    )}
                  </span>
                </label>
              ))}
          </div>
          <div className="button-row" style={{ marginTop: 8 }}>
            <button type="button" onClick={loadModels} disabled={loadingModels}>
              {loadingModels ? "Спрашиваем…" : "Обновить список"}
            </button>
          </div>
        </>
      )}

      <div className="button-row" style={{ marginTop: 20 }}>
        <button
          type="button"
          className="primary"
          onClick={run}
          disabled={running || !ready}
        >
          {running ? "Подключаем…" : "Подключить"}
        </button>
        {!ready && (
          <span className="field-hint">
            Заполните название сервиса, адрес и выберите модель.
          </span>
        )}
      </div>

      {steps.length > 0 && (
        <>
          <h3 className="section-title">Что происходит</h3>
          <ol className="steps">
            {steps.map((step) => (
              <li key={step.key}>
                <div>
                  <div className="inline-list">
                    <span>{step.title}</span>
                    <Status tone={STATUS_TONES[step.status]}>
                      {STATUS_LABELS[step.status]}
                    </Status>
                  </div>
                  {step.detail && <div className="field-hint">{step.detail}</div>}
                </div>
              </li>
            ))}
          </ol>

          {steps.some((s) => s.status === "error") && (
            <Notice tone="warn" title="Настройка не завершена">
              Созданные сервис и модель сохранены — их можно исправить на вкладке
              «Подключения», не начиная заново.
            </Notice>
          )}
        </>
      )}
    </Card>
  );
}
