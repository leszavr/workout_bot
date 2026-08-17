"use client";

import { useCallback, useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import {
  aiApi,
  AIEndpointItem,
  AIEndpointTestResult,
  AIModelItem,
  AIProviderItem,
  AITaskItem,
  getToken,
} from "@/lib/api";
import { aiProtocolLabel, aiTaskLabel } from "@/lib/labels";

const PROTOCOLS = ["openai_compatible", "anthropic", "custom"];

export default function AIConfigPage() {
  const [providers, setProviders] = useState<AIProviderItem[]>([]);
  const [endpoints, setEndpoints] = useState<Record<number, AIEndpointItem[]>>({});
  const [models, setModels] = useState<Record<number, AIModelItem[]>>({});
  const [tasks, setTasks] = useState<AITaskItem[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [testResults, setTestResults] = useState<Record<number, AIEndpointTestResult>>({});

  const flash = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 4000);
  };

  const loadProviders = useCallback(async () => {
    const data = await aiApi.providers();
    setProviders(data.items);
    const endpointMap: Record<number, AIEndpointItem[]> = {};
    const modelMap: Record<number, AIModelItem[]> = {};
    for (const provider of data.items) {
      const eps = await aiApi.endpoints(provider.id);
      endpointMap[provider.id] = eps.items;
      for (const ep of eps.items) {
        const ms = await aiApi.models(ep.id);
        modelMap[ep.id] = ms.items;
      }
    }
    setEndpoints(endpointMap);
    setModels(modelMap);
  }, []);

  const loadTasks = useCallback(async () => {
    const data = await aiApi.tasks();
    setTasks(data.items);
  }, []);

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    Promise.all([loadProviders(), loadTasks()]).catch((e) => setError(e.message));
  }, [loadProviders, loadTasks]);

  const allModels: AIModelItem[] = Object.values(models).flat();

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <h1 className="page-title">AI-конфигурация</h1>
        {error && <div className="error">{error}</div>}
        {notice && <div className="badge confirmed">{notice}</div>}

        <ProvidersSection
          providers={providers}
          endpoints={endpoints}
          models={models}
          testResults={testResults}
          onChanged={(message) => {
            flash(message);
            loadProviders().catch((e) => setError(e.message));
          }}
          onError={setError}
          onTestResult={(endpointId, result) =>
            setTestResults((prev) => ({ ...prev, [endpointId]: result }))
          }
        />

        <TasksSection
          tasks={tasks}
          allModels={allModels}
          onChanged={(message) => {
            flash(message);
            loadTasks().catch((e) => setError(e.message));
          }}
          onError={setError}
        />
      </main>
    </div>
  );
}

// --- Providers / Endpoints / Models ---------------------------------------------

function ProvidersSection(props: Readonly<{
  providers: AIProviderItem[];
  endpoints: Record<number, AIEndpointItem[]>;
  models: Record<number, AIModelItem[]>;
  testResults: Record<number, AIEndpointTestResult>;
  onChanged: (message: string) => void;
  onError: (message: string) => void;
  onTestResult: (endpointId: number, result: AIEndpointTestResult) => void;
}>) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [protocol, setProtocol] = useState("openai_compatible");

  const createProvider = async () => {
    try {
      await aiApi.createProvider({ name, slug, protocol });
      setName("");
      setSlug("");
      props.onChanged("Провайдер создан");
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  const toggleProvider = async (provider: AIProviderItem) => {
    try {
      await aiApi.patchProvider(provider.id, { enabled: !provider.enabled });
      props.onChanged(provider.enabled ? "Провайдер отключён" : "Провайдер включён");
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  return (
    <div className="card">
      <h2 className="section-title">Провайдеры</h2>
      <div className="toolbar">
        <input
          type="text"
          placeholder="Название (например, RouterAI)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label="Название провайдера"
        />
        <input
          type="text"
          placeholder="Идентификатор slug (например, router-ai)"
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
          aria-label="Slug провайдера"
        />
        <select
          value={protocol}
          onChange={(e) => setProtocol(e.target.value)}
          aria-label="Протокол"
        >
          {PROTOCOLS.map((p) => (
            <option key={p} value={p}>
              {aiProtocolLabel(p)}
            </option>
          ))}
        </select>
        <button type="button" className="primary" onClick={createProvider}>
          Создать провайдера
        </button>
      </div>

      {props.providers.length === 0 ? (
        <p className="muted">Провайдеров пока нет.</p>
      ) : (
        props.providers.map((provider) => (
          <div key={provider.id} style={{ marginBottom: 24 }}>
            <div className="toolbar" style={{ alignItems: "center" }}>
              <strong>{provider.name}</strong>
              <span className="badge">{aiProtocolLabel(provider.protocol)}</span>
              <span className="muted">slug: {provider.slug}</span>
              <button type="button" onClick={() => toggleProvider(provider)}>
                {provider.enabled ? "Отключить" : "Включить"}
              </button>
            </div>
            <EndpointsBlock
              provider={provider}
              endpoints={props.endpoints[provider.id] ?? []}
              models={props.models}
              testResults={props.testResults}
              onChanged={props.onChanged}
              onError={props.onError}
              onTestResult={props.onTestResult}
            />
          </div>
        ))
      )}
    </div>
  );
}

function EndpointsBlock(props: Readonly<{
  provider: AIProviderItem;
  endpoints: AIEndpointItem[];
  models: Record<number, AIModelItem[]>;
  testResults: Record<number, AIEndpointTestResult>;
  onChanged: (message: string) => void;
  onError: (message: string) => void;
  onTestResult: (endpointId: number, result: AIEndpointTestResult) => void;
}>) {
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState(60);
  const [maxRetries, setMaxRetries] = useState(2);

  const createEndpoint = async () => {
    try {
      await aiApi.createEndpoint(props.provider.id, {
        name,
        base_url: baseUrl,
        api_key: apiKey || undefined,
        timeout_seconds: timeoutSeconds,
        max_retries: maxRetries,
      });
      setName("");
      setBaseUrl("");
      setApiKey("");
      props.onChanged("Эндпоинт создан");
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  return (
    <div style={{ marginLeft: 16 }}>
      <h3 className="section-title">Эндпоинты</h3>
      <div className="toolbar">
        <input
          type="text"
          placeholder="Название эндпоинта"
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label="Название эндпоинта"
        />
        <input
          type="text"
          placeholder="Базовый URL (https://example.com/v1)"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          aria-label="Базовый URL"
          style={{ minWidth: 280 }}
        />
        <input
          type="password"
          placeholder="API-ключ (необязательно)"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          aria-label="API-ключ"
        />
        <input
          type="text"
          placeholder="Таймаут, с"
          value={timeoutSeconds}
          onChange={(e) => setTimeoutSeconds(Number(e.target.value) || 60)}
          aria-label="Таймаут"
          style={{ minWidth: 90 }}
        />
        <input
          type="text"
          placeholder="Повторные попытки"
          value={maxRetries}
          onChange={(e) => setMaxRetries(Number(e.target.value) || 0)}
          aria-label="Повторные попытки"
          style={{ minWidth: 80 }}
        />
        <button type="button" className="primary" onClick={createEndpoint}>
          Создать эндпоинт
        </button>
      </div>

      {props.endpoints.length === 0 ? (
        <p className="muted">Эндпоинтов нет.</p>
      ) : (
        props.endpoints.map((endpoint) => (
          <EndpointRow
            key={endpoint.id}
            endpoint={endpoint}
            models={props.models[endpoint.id] ?? []}
            testResult={props.testResults[endpoint.id]}
            onChanged={props.onChanged}
            onError={props.onError}
            onTestResult={props.onTestResult}
          />
        ))
      )}
    </div>
  );
}

function EndpointRow(props: Readonly<{
  endpoint: AIEndpointItem;
  models: AIModelItem[];
  testResult?: AIEndpointTestResult;
  onChanged: (message: string) => void;
  onError: (message: string) => void;
  onTestResult: (endpointId: number, result: AIEndpointTestResult) => void;
}>) {
  const { endpoint } = props;
  const [newKey, setNewKey] = useState("");
  const [testing, setTesting] = useState(false);

  const rotateKey = async () => {
    if (!newKey) return;
    try {
      await aiApi.setEndpointSecret(endpoint.id, newKey);
      setNewKey("");
      props.onChanged("API-ключ сохранён (показывается только в маскированном виде)");
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  const runTest = async () => {
    setTesting(true);
    try {
      const result = await aiApi.testEndpoint(endpoint.id);
      props.onTestResult(endpoint.id, result);
    } catch (e) {
      props.onError((e as Error).message);
    } finally {
      setTesting(false);
    }
  };

  const toggleEndpoint = async () => {
    try {
      await aiApi.patchEndpoint(endpoint.id, { enabled: !endpoint.enabled });
      props.onChanged(endpoint.enabled ? "Эндпоинт отключён" : "Эндпоинт включён");
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  return (
    <div className="card" style={{ padding: 14 }}>
      <div className="toolbar" style={{ alignItems: "center" }}>
        <strong>{endpoint.name}</strong>
        <span className="muted">{endpoint.base_url}</span>
        <span className="muted">
          таймаут {endpoint.timeout_seconds} с · повторы {endpoint.max_retries}
        </span>
        {endpoint.has_api_key ? (
          <span className="badge confirmed">ключ: {endpoint.masked_api_key}</span>
        ) : (
          <span className="badge draft">ключ не задан</span>
        )}
        <button type="button" onClick={toggleEndpoint}>
          {endpoint.enabled ? "Отключить" : "Включить"}
        </button>
        <button type="button" onClick={runTest} disabled={testing}>
          {testing ? "Проверка..." : "Проверить подключение"}
        </button>
      </div>

      {props.testResult && (
        <p className={props.testResult.success ? "muted" : "error"}>
          {props.testResult.success
            ? `✓ Подключение успешно (${props.testResult.latency_ms} мс, модель: ${props.testResult.model})`
            : `✗ ${props.testResult.error_type}: ${props.testResult.message}`}
        </p>
      )}

      <div className="toolbar">
        <input
          type="password"
          placeholder="Новый API-ключ (ротация)"
          value={newKey}
          onChange={(e) => setNewKey(e.target.value)}
          aria-label="Новый API-ключ"
        />
        <button type="button" onClick={rotateKey}>
          Сохранить ключ
        </button>
      </div>

      <ModelsBlock
        endpoint={endpoint}
        models={props.models}
        onChanged={props.onChanged}
        onError={props.onError}
      />
    </div>
  );
}

function ModelsBlock(props: Readonly<{
  endpoint: AIEndpointItem;
  models: AIModelItem[];
  onChanged: (message: string) => void;
  onError: (message: string) => void;
}>) {
  const [modelId, setModelId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [contextWindow, setContextWindow] = useState("");
  const [maxOutputTokens, setMaxOutputTokens] = useState("");
  const [jsonSchema, setJsonSchema] = useState(false);
  const [structuredOutput, setStructuredOutput] = useState(false);
  const [streaming, setStreaming] = useState(false);

  const createModel = async () => {
    try {
      await aiApi.createModel(props.endpoint.id, {
        model_id: modelId,
        display_name: displayName || modelId,
        context_window: contextWindow ? Number(contextWindow) : null,
        max_output_tokens: maxOutputTokens ? Number(maxOutputTokens) : null,
        supports_json_schema: jsonSchema,
        supports_structured_output: structuredOutput,
        supports_streaming: streaming,
      });
      setModelId("");
      setDisplayName("");
      props.onChanged("Модель создана");
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  const toggleModel = async (model: AIModelItem) => {
    try {
      await aiApi.patchModel(model.id, { enabled: !model.enabled });
      props.onChanged(model.enabled ? "Модель отключена" : "Модель включена");
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  return (
    <div style={{ marginLeft: 16 }}>
      <h4 className="section-title">Модели</h4>
      <div className="toolbar">
        <input
          type="text"
          placeholder="Идентификатор модели (строка для эндпоинта)"
          value={modelId}
          onChange={(e) => setModelId(e.target.value)}
          aria-label="Идентификатор модели"
        />
        <input
          type="text"
          placeholder="Отображаемое имя"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          aria-label="Отображаемое имя модели"
        />
        <input
          type="text"
          placeholder="Контекстное окно"
          value={contextWindow}
          onChange={(e) => setContextWindow(e.target.value)}
          aria-label="Контекстное окно"
          style={{ minWidth: 120 }}
        />
        <input
          type="text"
          placeholder="Макс. токенов ответа"
          value={maxOutputTokens}
          onChange={(e) => setMaxOutputTokens(e.target.value)}
          aria-label="Максимум токенов ответа"
          style={{ minWidth: 130 }}
        />
        <label>
          <input
            type="checkbox"
            checked={jsonSchema}
            onChange={(e) => setJsonSchema(e.target.checked)}
          />{" "}
          JSON-схема
        </label>
        <label>
          <input
            type="checkbox"
            checked={structuredOutput}
            onChange={(e) => setStructuredOutput(e.target.checked)}
          />{" "}
          структурированный вывод
        </label>
        <label>
          <input
            type="checkbox"
            checked={streaming}
            onChange={(e) => setStreaming(e.target.checked)}
          />{" "}
          потоковый вывод
        </label>
        <button type="button" className="primary" onClick={createModel}>
          Создать модель
        </button>
      </div>

      {props.models.length === 0 ? (
        <p className="muted">Моделей нет.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Идентификатор</th>
              <th>Имя</th>
              <th>Контекст</th>
              <th>Макс. ответ</th>
              <th>Возможности</th>
              <th>Статус</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {props.models.map((model) => (
              <tr key={model.id}>
                <td>{model.model_id}</td>
                <td>{model.display_name}</td>
                <td>{model.context_window ?? "—"}</td>
                <td>{model.max_output_tokens ?? "—"}</td>
                <td className="muted">
                  {[
                    model.supports_json_schema && "JSON-схема",
                    model.supports_structured_output && "структурный вывод",
                    model.supports_streaming && "поток",
                  ]
                    .filter(Boolean)
                    .join(", ") || "—"}
                </td>
                <td>
                  <span className={model.enabled ? "badge confirmed" : "badge draft"}>
                    {model.enabled ? "включена" : "отключена"}
                  </span>
                </td>
                <td>
                  <button type="button" onClick={() => toggleModel(model)}>
                    {model.enabled ? "Отключить" : "Включить"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// --- AI Tasks --------------------------------------------------------------------

function TasksSection(props: Readonly<{
  tasks: AITaskItem[];
  allModels: AIModelItem[];
  onChanged: (message: string) => void;
  onError: (message: string) => void;
}>) {
  return (
    <div className="card">
      <h2 className="section-title">AI-задачи</h2>
      {props.tasks.length === 0 ? (
        <p className="muted">Загрузка...</p>
      ) : (
        props.tasks.map((task) => (
          <TaskRow
            key={task.task_type}
            task={task}
            allModels={props.allModels}
            onChanged={props.onChanged}
            onError={props.onError}
          />
        ))
      )}
    </div>
  );
}

function TaskRow(props: Readonly<{
  task: AITaskItem;
  allModels: AIModelItem[];
  onChanged: (message: string) => void;
  onError: (message: string) => void;
}>) {
  const { task } = props;
  const [enabled, setEnabled] = useState(task.enabled);
  const [temperature, setTemperature] = useState(task.temperature);
  const [maxTokens, setMaxTokens] = useState(task.max_tokens ?? 0);
  const [promptVersion, setPromptVersion] = useState(task.prompt_version ?? 0);
  const [selectedModels, setSelectedModels] = useState<number[]>(
    task.bindings.map((b) => b.model_id)
  );

  useEffect(() => {
    setEnabled(task.enabled);
    setTemperature(task.temperature);
    setMaxTokens(task.max_tokens ?? 0);
    setPromptVersion(task.prompt_version ?? 0);
    setSelectedModels(task.bindings.map((b) => b.model_id));
  }, [task]);

  const save = async () => {
    try {
      await aiApi.putTask(task.task_type, {
        enabled,
        temperature,
        max_tokens: maxTokens > 0 ? maxTokens : null,
        prompt_version: promptVersion > 0 ? promptVersion : null,
        model_ids: selectedModels,
      });
      props.onChanged(`Задача «${aiTaskLabel(task.task_type)}» сохранена`);
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  const moveModel = (index: number, direction: -1 | 1) => {
    const next = [...selectedModels];
    const target = index + direction;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setSelectedModels(next);
  };

  const modelName = (pk: number) => {
    const model = props.allModels.find((m) => m.id === pk);
    return model ? `${model.display_name} (${model.model_id})` : `#${pk}`;
  };

  return (
    <div className="card" style={{ padding: 14 }}>
      <div className="toolbar" style={{ alignItems: "center" }}>
        <strong>{aiTaskLabel(task.task_type)}</strong>
        <span className="muted">({task.task_type})</span>
        <label>
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />{" "}
          включена
        </label>
      </div>

      <div className="toolbar">
        <label>
          Температура{" "}
          <input
            type="text"
            value={temperature}
            onChange={(e) => setTemperature(Number(e.target.value) || 0)}
            aria-label="Температура"
            style={{ minWidth: 70 }}
          />
        </label>
        <label>
          Макс. токенов{" "}
          <input
            type="text"
            value={maxTokens}
            onChange={(e) => setMaxTokens(Number(e.target.value) || 0)}
            aria-label="Максимум токенов"
            style={{ minWidth: 90 }}
          />
        </label>
        <label>
          Версия промпта{" "}
          <input
            type="text"
            value={promptVersion}
            onChange={(e) => setPromptVersion(Number(e.target.value) || 0)}
            aria-label="Версия промпта"
            style={{ minWidth: 70 }}
          />
        </label>
      </div>

      <div className="section-title">Модели (порядок: основная → резервные)</div>
      {selectedModels.length === 0 ? (
        <p className="muted">Модели не выбраны.</p>
      ) : (
        <ol>
          {selectedModels.map((pk, index) => (
            <li key={pk}>
              {index === 0 ? "Основная: " : `Резервная ${index}: `}
              {modelName(pk)}{" "}
              <button type="button" onClick={() => moveModel(index, -1)}>
                ↑
              </button>{" "}
              <button type="button" onClick={() => moveModel(index, 1)}>
                ↓
              </button>{" "}
              <button
                type="button"
                onClick={() =>
                  setSelectedModels(selectedModels.filter((x) => x !== pk))
                }
              >
                убрать
              </button>
            </li>
          ))}
        </ol>
      )}

      <div className="toolbar">
        <select
          defaultValue=""
          onChange={(e) => {
            const pk = Number(e.target.value);
            if (pk && !selectedModels.includes(pk)) {
              setSelectedModels([...selectedModels, pk]);
            }
            e.target.value = "";
          }}
          aria-label="Добавить модель"
        >
          <option value="" disabled>
            Добавить модель...
          </option>
          {props.allModels.map((model) => (
            <option key={model.id} value={model.id}>
              {model.display_name} ({model.model_id})
            </option>
          ))}
        </select>
        <button type="button" className="primary" onClick={save}>
          Сохранить задачу
        </button>
      </div>
    </div>
  );
}
