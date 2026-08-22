"use client";

import { useCallback, useEffect, useState } from "react";

import AIFallbackEvents from "@/components/AIFallbackEvents";
import AIInfrastructureHealthPanel from "@/components/AIInfrastructureHealthPanel";
import AIObservability from "@/components/AIObservability";
import AIQuickSetup from "@/components/AIQuickSetup";
import AIReadinessPanel from "@/components/AIReadinessPanel";
import AppNav from "@/components/AppNav";
import {
  aiApi,
  AIAuditItem,
  AIEndpointItem,
  AIEndpointTestResult,
  AIModelItem,
  AIProviderItem,
  AIReadinessReport,
  AITaskItem,
  AIUsageItem,
  ApiError,
  getToken,
} from "@/lib/api";
import { aiProtocolLabel, aiTaskLabel } from "@/lib/labels";
import { useCurrentUser } from "@/lib/session";

const MAIN_TASK_TYPE = "workout_generation";

// Удаление конфигурации: подтверждение + расшифровка блокеров из 409.
// Одна функция на три сущности, чтобы объяснение причины не расходилось.
async function runDelete(
  label: string,
  action: () => Promise<void>,
  onChanged: (message: string) => void,
  onError: (message: string) => void
): Promise<void> {
  if (!window.confirm(`Удалить ${label}? Действие необратимо.`)) return;
  try {
    await action();
    onChanged(`Удалено: ${label}`);
  } catch (e) {
    const error = e as ApiError;
    const blockers = error.blockers?.map((b) => b.detail).join("; ");
    onError(
      blockers
        ? `${error.message} Зависимости: ${blockers}`
        : error.message
    );
  }
}

export default function AIConfigPage() {
  const { canWrite } = useCurrentUser();
  const [providers, setProviders] = useState<AIProviderItem[]>([]);
  const [endpoints, setEndpoints] = useState<Record<number, AIEndpointItem[]>>({});
  const [models, setModels] = useState<Record<number, AIModelItem[]>>({});
  const [tasks, setTasks] = useState<AITaskItem[]>([]);
  const [readiness, setReadiness] = useState<AIReadinessReport | null>(null);
  const [usage, setUsage] = useState<AIUsageItem[]>([]);
  const [audit, setAudit] = useState<AIAuditItem[]>([]);
  const [promptVersions, setPromptVersions] = useState<number[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [testResults, setTestResults] = useState<Record<number, AIEndpointTestResult>>({});
  // Растёт после каждой CRUD-операции: health-дашборд и журнал fallback
  // синхронизируются с backend, а не показывают устаревшее состояние.
  const [reloadKey, setReloadKey] = useState(0);

  const flash = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 6000);
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

  const loadStatus = useCallback(async () => {
    const [report, usageData, auditData, prompts] = await Promise.all([
      aiApi.readiness(MAIN_TASK_TYPE),
      aiApi.usage(),
      aiApi.audit(),
      aiApi.prompts(MAIN_TASK_TYPE),
    ]);
    setReadiness(report);
    setUsage(usageData.items);
    setAudit(auditData.items);
    setPromptVersions(prompts.items.filter((p) => p.enabled).map((p) => p.version));
  }, []);

  const reloadAll = useCallback(async () => {
    setRefreshing(true);
    try {
      await Promise.all([loadProviders(), loadTasks(), loadStatus()]);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRefreshing(false);
    }
  }, [loadProviders, loadTasks, loadStatus]);

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    reloadAll().catch((e) => setError(e.message));
  }, [reloadAll]);

  const allModels: AIModelItem[] = Object.values(models).flat();
  const supportedProtocols = readiness?.protocols ?? [
    { value: "openai_compatible", supported: true },
  ];

  const onChanged = (message: string) => {
    flash(message);
    setReloadKey((value) => value + 1);
    reloadAll().catch((e) => setError(e.message));
  };

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <h1 className="page-title">AI-конфигурация</h1>
        {error && <div className="error">{error}</div>}
        {notice && <div className="badge confirmed">{notice}</div>}

        {/* Роль viewer не может менять конфигурацию: сервер отвечает 403.
            Предупреждаем заранее, чтобы отказ не выглядел как поломка. */}
        {!canWrite && (
          <div className="card">
            <p style={{ margin: 0 }}>
              У вашей роли только просмотр: состояние и журналы доступны,
              изменение настроек AI — нет.
            </p>
          </div>
        )}

        <AIReadinessPanel
          report={readiness}
          refreshing={refreshing}
          onRefresh={() => reloadAll().catch((e) => setError(e.message))}
        />

        <AIInfrastructureHealthPanel reloadKey={reloadKey} onError={setError} />

        <AIQuickSetup
          taskType={MAIN_TASK_TYPE}
          onFinished={onChanged}
          onError={setError}
        />

        <ProvidersSection
          providers={providers}
          endpoints={endpoints}
          models={models}
          protocols={supportedProtocols}
          testResults={testResults}
          onChanged={onChanged}
          onError={setError}
          onTestResult={(endpointId, result) =>
            setTestResults((prev) => ({ ...prev, [endpointId]: result }))
          }
        />

        <TasksSection
          tasks={tasks}
          allModels={allModels}
          endpoints={endpoints}
          providers={providers}
          promptVersions={promptVersions}
          onChanged={onChanged}
          onError={setError}
        />

        <AIFallbackEvents reloadKey={reloadKey} onError={setError} />

        <AIObservability
          usage={usage}
          audit={audit}
          models={allModels}
          providers={providers}
          refreshing={refreshing}
          onRefresh={() => reloadAll().catch((e) => setError(e.message))}
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
  protocols: Array<{ value: string; supported: boolean }>;
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

  const unsupported = new Set(
    props.protocols.filter((p) => !p.supported).map((p) => p.value)
  );

  return (
    <div className="card">
      <h2 className="section-title" style={{ marginTop: 0 }}>
        Провайдеры (экспертный режим)
      </h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Провайдер — группировка эндпоинтов с общим протоколом. Сам по себе он
        ничего не делает: нужны эндпоинт с ключом и модель.
      </p>
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
          {props.protocols.map((p) => (
            <option key={p.value} value={p.value} disabled={!p.supported}>
              {aiProtocolLabel(p.value)}
              {p.supported ? "" : " — адаптер не реализован"}
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
            <ProviderRow
              provider={provider}
              unsupported={unsupported.has(provider.protocol)}
              protocols={props.protocols}
              onChanged={props.onChanged}
              onError={props.onError}
            />
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

function ProviderRow(props: Readonly<{
  provider: AIProviderItem;
  unsupported: boolean;
  protocols: Array<{ value: string; supported: boolean }>;
  onChanged: (message: string) => void;
  onError: (message: string) => void;
}>) {
  const { provider } = props;
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(provider.name);
  const [slug, setSlug] = useState(provider.slug);
  const [protocol, setProtocol] = useState(provider.protocol);
  const [priority, setPriority] = useState(provider.priority);

  const toggle = async () => {
    try {
      await aiApi.patchProvider(provider.id, { enabled: !provider.enabled });
      props.onChanged(provider.enabled ? "Провайдер отключён" : "Провайдер включён");
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  const save = async () => {
    try {
      await aiApi.patchProvider(provider.id, {
        name,
        slug,
        protocol,
        priority,
      });
      setEditing(false);
      props.onChanged("Провайдер изменён");
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  return (
    <>
      <div className="toolbar" style={{ alignItems: "center" }}>
        <strong>{provider.name}</strong>
        <span className="badge">{aiProtocolLabel(provider.protocol)}</span>
        {props.unsupported && (
          <span className="badge draft">адаптера нет: вызовы упадут</span>
        )}
        <span className="muted">slug: {provider.slug}</span>
        <span className={provider.enabled ? "badge confirmed" : "badge draft"}>
          {provider.enabled ? "включён" : "отключён"}
        </span>
        <button type="button" onClick={toggle}>
          {provider.enabled ? "Отключить" : "Включить"}
        </button>
        <button type="button" onClick={() => setEditing(!editing)}>
          {editing ? "Отменить" : "Изменить"}
        </button>
        <button
          type="button"
          onClick={() =>
            runDelete(
              `провайдера «${provider.name}»`,
              () => aiApi.deleteProvider(provider.id),
              props.onChanged,
              props.onError
            )
          }
        >
          Удалить
        </button>
      </div>

      {editing && (
        <div className="toolbar">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-label="Название провайдера"
          />
          <input
            type="text"
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            aria-label="Slug провайдера"
          />
          <select
            value={protocol}
            onChange={(e) => setProtocol(e.target.value)}
            aria-label="Протокол провайдера"
          >
            {props.protocols.map((p) => (
              <option key={p.value} value={p.value} disabled={!p.supported}>
                {aiProtocolLabel(p.value)}
                {p.supported ? "" : " — адаптер не реализован"}
              </option>
            ))}
          </select>
          <label>
            Приоритет{" "}
            <input
              type="number"
              min={0}
              max={1000}
              value={priority}
              onChange={(e) => setPriority(Number(e.target.value) || 0)}
              aria-label="Приоритет провайдера"
              style={{ minWidth: 90 }}
            />
          </label>
          <button type="button" className="primary" onClick={save}>
            Сохранить провайдера
          </button>
        </div>
      )}
    </>
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
          placeholder="Понятное имя (например, основной)"
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
        <label>
          Таймаут, с{" "}
          <input
            type="number"
            min={1}
            max={600}
            value={timeoutSeconds}
            onChange={(e) => setTimeoutSeconds(Number(e.target.value) || 60)}
            aria-label="Таймаут"
            style={{ minWidth: 90 }}
          />
        </label>
        <label>
          Повторы{" "}
          <input
            type="number"
            min={0}
            max={5}
            value={maxRetries}
            onChange={(e) => setMaxRetries(Number(e.target.value) || 0)}
            aria-label="Повторные попытки"
            style={{ minWidth: 80 }}
          />
        </label>
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

function ConnectionBadge(props: Readonly<{ endpoint: AIEndpointItem }>) {
  const { last_test_status: status, last_test_at: at } = props.endpoint;
  if (!status) {
    return <span className="badge draft">подключение не проверялось</span>;
  }
  const when = at ? new Date(at).toLocaleString("ru-RU") : "";
  if (status === "success") {
    return <span className="badge confirmed">подключение: ✓ {when}</span>;
  }
  return (
    <span className="badge draft">
      подключение: ✗ {props.endpoint.last_test_error_type ?? "ошибка"}
    </span>
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
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(endpoint.name);
  const [baseUrl, setBaseUrl] = useState(endpoint.base_url);
  const [timeoutSeconds, setTimeoutSeconds] = useState(endpoint.timeout_seconds);
  const [maxRetries, setMaxRetries] = useState(endpoint.max_retries);

  const saveEndpoint = async () => {
    try {
      await aiApi.patchEndpoint(endpoint.id, {
        name,
        base_url: baseUrl,
        timeout_seconds: timeoutSeconds,
        max_retries: maxRetries,
      });
      setEditing(false);
      props.onChanged("Эндпоинт изменён");
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

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
      props.onChanged(
        result.success
          ? `Подключение «${endpoint.name}» успешно (${result.latency_ms} мс)`
          : `Подключение «${endpoint.name}» не удалось: ${result.error_type ?? "ошибка"}`
      );
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
        <ConnectionBadge endpoint={endpoint} />
        <button type="button" onClick={toggleEndpoint}>
          {endpoint.enabled ? "Отключить" : "Включить"}
        </button>
        <button type="button" onClick={runTest} disabled={testing}>
          {testing ? "Проверка..." : "Проверить подключение"}
        </button>
        <button type="button" onClick={() => setEditing(!editing)}>
          {editing ? "Отменить" : "Изменить"}
        </button>
        <button
          type="button"
          onClick={() =>
            runDelete(
              `эндпоинт «${endpoint.name}»`,
              () => aiApi.deleteEndpoint(endpoint.id),
              props.onChanged,
              props.onError
            )
          }
        >
          Удалить
        </button>
      </div>

      {editing && (
        <div className="toolbar">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-label="Название эндпоинта"
          />
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            aria-label="Базовый URL эндпоинта"
            style={{ minWidth: 280 }}
          />
          <label>
            Таймаут, с{" "}
            <input
              type="number"
              min={1}
              max={600}
              value={timeoutSeconds}
              onChange={(e) => setTimeoutSeconds(Number(e.target.value) || 60)}
              aria-label="Таймаут эндпоинта"
              style={{ minWidth: 90 }}
            />
          </label>
          <label>
            Повторы{" "}
            <input
              type="number"
              min={0}
              max={5}
              value={maxRetries}
              onChange={(e) => setMaxRetries(Number(e.target.value) || 0)}
              aria-label="Повторные попытки эндпоинта"
              style={{ minWidth: 80 }}
            />
          </label>
          <button type="button" className="primary" onClick={saveEndpoint}>
            Сохранить эндпоинт
          </button>
        </div>
      )}

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
              <ModelRow
                key={model.id}
                model={model}
                onToggle={toggleModel}
                onChanged={props.onChanged}
                onError={props.onError}
              />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ModelRow(props: Readonly<{
  model: AIModelItem;
  onToggle: (model: AIModelItem) => void;
  onChanged: (message: string) => void;
  onError: (message: string) => void;
}>) {
  const { model } = props;
  const [editing, setEditing] = useState(false);
  const [modelId, setModelId] = useState(model.model_id);
  const [displayName, setDisplayName] = useState(model.display_name);
  const [contextWindow, setContextWindow] = useState(
    model.context_window ? String(model.context_window) : ""
  );
  const [maxOutputTokens, setMaxOutputTokens] = useState(
    model.max_output_tokens ? String(model.max_output_tokens) : ""
  );

  const save = async () => {
    try {
      await aiApi.patchModel(model.id, {
        model_id: modelId,
        display_name: displayName,
        context_window: contextWindow ? Number(contextWindow) : null,
        max_output_tokens: maxOutputTokens ? Number(maxOutputTokens) : null,
      });
      setEditing(false);
      props.onChanged("Модель изменена");
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  if (editing) {
    return (
      <tr>
        <td colSpan={7}>
          <div className="toolbar">
            <input
              type="text"
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              aria-label="Идентификатор модели"
            />
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              aria-label="Отображаемое имя модели"
            />
            <input
              type="text"
              value={contextWindow}
              onChange={(e) => setContextWindow(e.target.value)}
              placeholder="Контекстное окно"
              aria-label="Контекстное окно модели"
              style={{ minWidth: 120 }}
            />
            <input
              type="text"
              value={maxOutputTokens}
              onChange={(e) => setMaxOutputTokens(e.target.value)}
              placeholder="Макс. токенов ответа"
              aria-label="Максимум токенов ответа модели"
              style={{ minWidth: 130 }}
            />
            <button type="button" className="primary" onClick={save}>
              Сохранить модель
            </button>
            <button type="button" onClick={() => setEditing(false)}>
              Отменить
            </button>
          </div>
        </td>
      </tr>
    );
  }

  return (
    <tr>
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
        <button type="button" onClick={() => props.onToggle(model)}>
          {model.enabled ? "Отключить" : "Включить"}
        </button>{" "}
        <button type="button" onClick={() => setEditing(true)}>
          Изменить
        </button>{" "}
        <button
          type="button"
          onClick={() =>
            runDelete(
              `модель «${model.display_name}»`,
              () => aiApi.deleteModel(model.id),
              props.onChanged,
              props.onError
            )
          }
        >
          Удалить
        </button>
      </td>
    </tr>
  );
}

// --- AI Tasks --------------------------------------------------------------------

function TasksSection(props: Readonly<{
  tasks: AITaskItem[];
  allModels: AIModelItem[];
  endpoints: Record<number, AIEndpointItem[]>;
  providers: AIProviderItem[];
  promptVersions: number[];
  onChanged: (message: string) => void;
  onError: (message: string) => void;
}>) {
  // Модель понятна только вместе с её эндпоинтом и провайдером.
  const modelLabel = (pk: number) => {
    const model = props.allModels.find((m) => m.id === pk);
    if (!model) return `#${pk}`;
    const endpoint = Object.values(props.endpoints)
      .flat()
      .find((e) => e.id === model.endpoint_id);
    const provider = props.providers.find((p) => p.id === endpoint?.provider_id);
    const context = [endpoint?.name, provider?.name].filter(Boolean).join(" · ");
    const suffix = model.enabled ? "" : " · отключена";
    return `${model.display_name} (${model.model_id})${context ? ` — ${context}` : ""}${suffix}`;
  };

  return (
    <div className="card">
      <h2 className="section-title" style={{ marginTop: 0 }}>
        AI-задачи
      </h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Реально вызывается только «Генерация программы тренировок». Остальные
        задачи — задел на будущее: их настройка ни на что не влияет.
      </p>
      {props.tasks.length === 0 ? (
        <p className="muted">Загрузка...</p>
      ) : (
        props.tasks.map((task) => (
          <TaskRow
            key={task.task_type}
            task={task}
            allModels={props.allModels}
            modelLabel={modelLabel}
            promptVersions={props.promptVersions}
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
  modelLabel: (pk: number) => string;
  promptVersions: number[];
  onChanged: (message: string) => void;
  onError: (message: string) => void;
}>) {
  const { task } = props;
  const [enabled, setEnabled] = useState(task.enabled);
  const [temperature, setTemperature] = useState(task.temperature);
  const [maxTokens, setMaxTokens] = useState(task.max_tokens ?? 0);
  const [timeoutSeconds, setTimeoutSeconds] = useState(task.timeout_seconds);
  const [promptVersion, setPromptVersion] = useState(task.prompt_version ?? 0);
  const [selectedModels, setSelectedModels] = useState<number[]>(
    task.bindings.map((b) => b.model_id)
  );
  const isActiveTask = task.task_type === MAIN_TASK_TYPE;

  useEffect(() => {
    setEnabled(task.enabled);
    setTemperature(task.temperature);
    setMaxTokens(task.max_tokens ?? 0);
    setTimeoutSeconds(task.timeout_seconds);
    setPromptVersion(task.prompt_version ?? 0);
    setSelectedModels(task.bindings.map((b) => b.model_id));
  }, [task]);

  const save = async () => {
    try {
      await aiApi.putTask(task.task_type, {
        enabled,
        temperature,
        max_tokens: maxTokens > 0 ? maxTokens : null,
        // Без этого поля backend молча сбрасывал таймаут задачи на значение
        // по умолчанию при каждом сохранении.
        timeout_seconds: timeoutSeconds,
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

  return (
    <div className="card" style={{ padding: 14 }}>
      <div className="toolbar" style={{ alignItems: "center" }}>
        <strong>{aiTaskLabel(task.task_type)}</strong>
        <span className="muted">({task.task_type})</span>
        {isActiveTask ? (
          <span className="badge">используется системой</span>
        ) : (
          <span className="badge draft">не вызывается кодом</span>
        )}
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
            type="number"
            min={0}
            max={2}
            step={0.1}
            value={temperature}
            onChange={(e) => setTemperature(Number(e.target.value) || 0)}
            aria-label="Температура"
            style={{ minWidth: 90 }}
          />
        </label>
        <label>
          Макс. токенов{" "}
          <input
            type="number"
            min={0}
            value={maxTokens}
            onChange={(e) => setMaxTokens(Number(e.target.value) || 0)}
            aria-label="Максимум токенов"
            style={{ minWidth: 110 }}
          />
        </label>
        <label>
          Таймаут, с{" "}
          <input
            type="number"
            min={1}
            max={600}
            value={timeoutSeconds}
            onChange={(e) => setTimeoutSeconds(Number(e.target.value) || 120)}
            aria-label="Таймаут задачи"
            style={{ minWidth: 90 }}
          />
        </label>
        <label>
          Версия промпта{" "}
          <input
            type="number"
            min={0}
            value={promptVersion}
            onChange={(e) => setPromptVersion(Number(e.target.value) || 0)}
            aria-label="Версия промпта"
            style={{ minWidth: 90 }}
          />
        </label>
      </div>
      {isActiveTask && (
        <p className="muted" style={{ marginTop: 0 }}>
          {props.promptVersions.length > 0
            ? `Версии промпта в базе: ${props.promptVersions
                .map((v) => `v${v}`)
                .join(", ")}. `
            : "Версий промпта в базе нет: используется файловый промпт проекта. "}
          0 — версия по умолчанию. Несуществующую версию сервер не примет.
        </p>
      )}

      <div className="section-title">Модели (порядок: основная → резервные)</div>
      {selectedModels.length === 0 ? (
        <p className="muted">Модели не выбраны.</p>
      ) : (
        <ol>
          {selectedModels.map((pk, index) => (
            <li key={pk}>
              {index === 0 ? "Основная: " : `Резервная ${index}: `}
              {props.modelLabel(pk)}{" "}
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
              {props.modelLabel(model.id)}
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
