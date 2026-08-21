"use client";

// Guided setup: одна форма вместо ручного обхода трёх уровней вложенности.
// Порядок шагов зафиксирован и виден пользователю: провайдер → эндпоинт с
// ключом → модель → проверка подключения → включение задачи. Задача НЕ
// включается, если проверка подключения не прошла.
//
// Собственного backend-пути у мастера нет: используются те же admin-endpoint'ы,
// что и в экспертном режиме ниже на странице.

import { useState } from "react";

import { aiApi } from "@/lib/api";

type StepStatus = "pending" | "running" | "ok" | "error";

interface Step {
  key: string;
  title: string;
  status: StepStatus;
  detail?: string;
}

const STEP_TITLES: Array<{ key: string; title: string }> = [
  { key: "provider", title: "Провайдер" },
  { key: "endpoint", title: "Эндпоинт и API-ключ" },
  { key: "model", title: "Модель" },
  { key: "connection", title: "Проверка подключения" },
  { key: "task", title: "Включение генерации" },
];

const STATUS_ICONS: Record<StepStatus, string> = {
  pending: "○",
  running: "…",
  ok: "✓",
  error: "✗",
};

const STATUS_COLORS: Record<StepStatus, string> = {
  pending: "#4b5563",
  running: "#1d4ed8",
  ok: "#166534",
  error: "#b91c1c",
};

function slugify(value: string): string {
  const slug = value
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "ai-provider";
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

  const update = (key: string, status: StepStatus, detail?: string) =>
    setSteps((prev) =>
      prev.map((step) => (step.key === key ? { ...step, status, detail } : step))
    );

  const run = async () => {
    if (!name.trim() || !baseUrl.trim() || !modelId.trim()) {
      props.onError(
        "Заполните название сервиса, базовый URL и идентификатор модели."
      );
      return;
    }
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
      update("provider", "ok", `${provider.name} (slug: ${provider.slug})`);

      update("endpoint", "running");
      const endpoint = await aiApi.createEndpoint(provider.id, {
        name: `Эндпоинт ${provider.name}`,
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
        update(
          "connection",
          "error",
          `${test.error_type ?? "ошибка"}: ${test.message ?? ""}`
        );
        update(
          "task",
          "error",
          "Задача не включена: сначала должно пройти подключение"
        );
        props.onFinished(
          "Провайдер, эндпоинт и модель созданы, но подключение не прошло. " +
            "Исправьте URL, ключ или идентификатор модели и повторите проверку."
        );
        return;
      }
      update("connection", "ok", `${test.latency_ms} мс, модель: ${test.model}`);

      update("task", "running");
      await aiApi.putTask(props.taskType, {
        enabled: true,
        model_ids: [model.id],
      });
      update("task", "ok", "Задача включена с параметрами по умолчанию");
      setName("");
      setBaseUrl("");
      setApiKey("");
      setModelId("");
      props.onFinished("AI подключён: проверка прошла, задача включена");
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
    <div className="card">
      <h2 className="section-title" style={{ marginTop: 0 }}>
        Быстрое подключение AI
      </h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Создаст провайдера (OpenAI-совместимый протокол), эндпоинт с ключом и
        модель, затем выполнит проверку подключения и включит задачу с
        параметрами по умолчанию. Если проверка не пройдёт, задача включена не
        будет. Тонкая настройка — в разделах ниже.
      </p>

      <div className="toolbar">
        <input
          type="text"
          placeholder="Название сервиса (например, RouterAI)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label="Название сервиса"
        />
        <input
          type="text"
          placeholder="Базовый URL (https://api.example.com/v1)"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          aria-label="Базовый URL"
          style={{ minWidth: 280 }}
        />
        <input
          type="password"
          placeholder="API-ключ (если требуется)"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          aria-label="API-ключ"
        />
        <input
          type="text"
          placeholder="Идентификатор модели (qwen/qwen3-max)"
          value={modelId}
          onChange={(e) => setModelId(e.target.value)}
          aria-label="Идентификатор модели"
          style={{ minWidth: 240 }}
        />
        <button type="button" className="primary" onClick={run} disabled={running}>
          {running ? "Подключение..." : "Подключить AI"}
        </button>
      </div>

      {steps.length > 0 && (
        <ol style={{ margin: "0 0 0 20px" }}>
          {steps.map((step) => (
            <li key={step.key} style={{ padding: "3px 0" }}>
              <span
                style={{
                  fontWeight: 700,
                  color: STATUS_COLORS[step.status],
                  marginRight: 8,
                }}
              >
                {STATUS_ICONS[step.status]}
              </span>
              {step.title}
              {step.detail && <span className="muted"> — {step.detail}</span>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
