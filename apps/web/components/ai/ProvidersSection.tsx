"use client";

// Подключения к сервисам ИИ: сервис → адрес подключения → модели.
//
// Структура повторяет реальность: у сервиса может быть несколько адресов,
// у адреса — несколько моделей. Формы добавления свёрнуты: здесь чаще
// правят существующее, чем создают новое.
//
// canWrite=false (наблюдатель) убирает все изменяющие действия. Это удобство:
// сам запрет обеспечивает сервер.

import { useCallback, useEffect, useState } from "react";

import {
  Card,
  Empty,
  Field,
  Notice,
  Status,
  moment,
} from "@/components/ui/Primitives";
import {
  AIDiscoveredModel,
  AIEndpointItem,
  AIEndpointTestResult,
  AIModelItem,
  AIProviderItem,
  ApiError,
  aiApi,
} from "@/lib/api";

/** Удаление с подтверждением и расшифровкой того, что мешает. */
async function runDelete(
  label: string,
  action: () => Promise<void>,
  onChanged: (message: string) => void,
  onError: (message: string) => void
): Promise<void> {
  if (!window.confirm(`Удалить ${label}? Отменить это нельзя.`)) return;
  try {
    await action();
    onChanged(`Удалено: ${label}`);
  } catch (e) {
    const error = e as ApiError;
    const blockers = error.blockers?.map((b) => b.detail).join("; ");
    onError(blockers ? `${error.message} Мешает: ${blockers}` : error.message);
  }
}

interface Shared {
  canWrite: boolean;
  onChanged: (message: string) => void;
  onError: (message: string) => void;
}

export default function ProvidersSection(props: Readonly<Shared & {
  providers: AIProviderItem[];
  endpoints: Record<number, AIEndpointItem[]>;
  models: Record<number, AIModelItem[]>;
  testResults: Record<number, AIEndpointTestResult>;
  onTestResult: (endpointId: number, result: AIEndpointTestResult) => void;
}>) {
  const [adding, setAdding] = useState(false);

  const shared: Shared = {
    canWrite: props.canWrite,
    onChanged: props.onChanged,
    onError: props.onError,
  };

  return (
    <Card
      title="Сервисы ИИ"
      description="Сервис — поставщик, к которому обращается система. У него есть адрес подключения с ключом доступа и хотя бы одна модель."
      actions={
        props.canWrite && !adding ? (
          <button
            type="button"
            className="primary small"
            onClick={() => setAdding(true)}
          >
            Добавить сервис
          </button>
        ) : undefined
      }
    >
      {adding && <NewProvider {...shared} onClose={() => setAdding(false)} />}

      {props.providers.length === 0 ? (
        <Empty
          title="Сервисов пока нет"
          hint="Проще начать с формы «Подключить ИИ» на вкладке «Состояние»: она создаст сервис, подключение и модель за один шаг."
        />
      ) : (
        props.providers.map((provider) => (
          <ProviderBlock
            key={provider.id}
            {...shared}
            provider={provider}
            endpoints={props.endpoints[provider.id] ?? []}
            models={props.models}
            testResults={props.testResults}
            onTestResult={props.onTestResult}
          />
        ))
      )}
    </Card>
  );
}

// --- Сервис ------------------------------------------------------------------

function NewProvider(props: Readonly<Shared & { onClose: () => void }>) {
  const [name, setName] = useState("");

  const create = async () => {
    try {
      // Короткое имя выводим сами: пользователю незачем его придумывать.
      const slug =
        name
          .trim()
          .toLowerCase()
          .replace(/[^a-z0-9_-]+/g, "-")
          .replace(/^-+|-+$/g, "") || `service-${Date.now()}`;
      await aiApi.createProvider({
        name: name.trim(),
        slug,
        protocol: "openai_compatible",
      });
      props.onClose();
      props.onChanged(`Сервис «${name.trim()}» добавлен`);
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  return (
    <div className="subcard" style={{ marginBottom: 20 }}>
      <Field
        label="Название сервиса"
        hint="Как вы будете узнавать его в списке. Дальше добавите адрес подключения и модель."
      >
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="OpenAI"
          aria-label="Название сервиса"
        />
      </Field>
      <div className="button-row" style={{ marginTop: 12 }}>
        <button
          type="button"
          className="primary"
          onClick={create}
          disabled={name.trim().length === 0}
        >
          Добавить
        </button>
        <button type="button" className="ghost" onClick={props.onClose}>
          Отмена
        </button>
      </div>
    </div>
  );
}

function ProviderBlock(props: Readonly<Shared & {
  provider: AIProviderItem;
  endpoints: AIEndpointItem[];
  models: Record<number, AIModelItem[]>;
  testResults: Record<number, AIEndpointTestResult>;
  onTestResult: (endpointId: number, result: AIEndpointTestResult) => void;
}>) {
  const { provider, canWrite, onChanged, onError } = props;
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(provider.name);
  const [addingEndpoint, setAddingEndpoint] = useState(false);

  const save = async () => {
    try {
      await aiApi.patchProvider(provider.id, { name: name.trim() });
      setEditing(false);
      onChanged("Сервис переименован");
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const toggle = async () => {
    try {
      await aiApi.patchProvider(provider.id, { enabled: !provider.enabled });
      onChanged(provider.enabled ? "Сервис выключен" : "Сервис включён");
    } catch (e) {
      onError((e as Error).message);
    }
  };

  return (
    <div className="subcard" style={{ marginBottom: 16 }}>
      <div className="card-head" style={{ marginBottom: 12 }}>
        <div>
          <div className="inline-list">
            <strong style={{ fontSize: 15 }}>{provider.name}</strong>
            <Status tone={provider.enabled ? "ok" : "neutral"}>
              {provider.enabled ? "включён" : "выключен"}
            </Status>
          </div>
          {!provider.enabled && (
            <p className="field-hint" style={{ marginTop: 4 }}>
              Пока сервис выключен, система к нему не обращается.
            </p>
          )}
        </div>

        {canWrite && (
          <div className="card-actions">
            <button type="button" className="small" onClick={toggle}>
              {provider.enabled ? "Выключить" : "Включить"}
            </button>
            <button
              type="button"
              className="small"
              onClick={() => setEditing(!editing)}
            >
              {editing ? "Отмена" : "Переименовать"}
            </button>
            <button
              type="button"
              className="small danger"
              onClick={() =>
                runDelete(
                  `сервис «${provider.name}»`,
                  () => aiApi.deleteProvider(provider.id),
                  onChanged,
                  onError
                )
              }
            >
              Удалить
            </button>
          </div>
        )}
      </div>

      {editing && (
        <div className="field-row" style={{ marginBottom: 16 }}>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-label="Название сервиса"
            style={{ maxWidth: 320 }}
          />
          <button type="button" className="primary small" onClick={save}>
            Сохранить
          </button>
        </div>
      )}

      <div className="inline-list" style={{ marginBottom: 8 }}>
        <h3 className="section-title" style={{ margin: 0 }}>
          Адреса подключения
        </h3>
        {canWrite && !addingEndpoint && (
          <button
            type="button"
            className="small ghost"
            onClick={() => setAddingEndpoint(true)}
          >
            + добавить адрес
          </button>
        )}
      </div>

      {addingEndpoint && (
        <NewEndpoint
          canWrite={canWrite}
          onChanged={onChanged}
          onError={onError}
          providerId={provider.id}
          onClose={() => setAddingEndpoint(false)}
        />
      )}

      {props.endpoints.length === 0 ? (
        <p className="field-hint">
          Адреса нет — обращаться некуда. Добавьте адрес из документации
          поставщика.
        </p>
      ) : (
        props.endpoints.map((endpoint) => (
          <EndpointBlock
            key={endpoint.id}
            canWrite={canWrite}
            onChanged={onChanged}
            onError={onError}
            endpoint={endpoint}
            models={props.models[endpoint.id] ?? []}
            testResult={props.testResults[endpoint.id]}
            onTestResult={props.onTestResult}
          />
        ))
      )}
    </div>
  );
}

// --- Адрес подключения --------------------------------------------------------

function NewEndpoint(props: Readonly<Shared & {
  providerId: number;
  onClose: () => void;
}>) {
  const [name, setName] = useState("Основное подключение");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");

  const create = async () => {
    try {
      await aiApi.createEndpoint(props.providerId, {
        name: name.trim(),
        base_url: baseUrl.trim(),
        api_key: apiKey.trim() || undefined,
      });
      props.onClose();
      props.onChanged("Адрес подключения добавлен");
    } catch (e) {
      props.onError((e as Error).message);
    }
  };

  return (
    <div className="subcard">
      <div className="form-grid">
        <Field label="Название" hint="Понятная пометка, если адресов несколько.">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-label="Название подключения"
          />
        </Field>
        <Field
          label="Адрес"
          hint="Из документации поставщика, обычно заканчивается на /v1."
        >
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.openai.com/v1"
            aria-label="Адрес подключения"
          />
        </Field>
        <Field
          label="Ключ доступа"
          hint="Хранится в зашифрованном виде и больше не показывается. Своим серверам ключ иногда не нужен."
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
      <div className="button-row" style={{ marginTop: 12 }}>
        <button
          type="button"
          className="primary"
          onClick={create}
          disabled={name.trim().length === 0 || baseUrl.trim().length < 8}
        >
          Добавить
        </button>
        <button type="button" className="ghost" onClick={props.onClose}>
          Отмена
        </button>
      </div>
    </div>
  );
}

function ConnectionStatus(props: Readonly<{ endpoint: AIEndpointItem }>) {
  const { last_test_status: status, last_test_at: at } = props.endpoint;
  if (!status) return <Status tone="neutral">связь не проверялась</Status>;
  if (status === "success") {
    return <Status tone="ok">связь есть · {moment(at)}</Status>;
  }
  return (
    <Status tone="bad">
      связи нет
      {props.endpoint.last_test_error_type
        ? ` · ${props.endpoint.last_test_error_type}`
        : ""}
    </Status>
  );
}

function EndpointBlock(props: Readonly<Shared & {
  endpoint: AIEndpointItem;
  models: AIModelItem[];
  testResult?: AIEndpointTestResult;
  onTestResult: (endpointId: number, result: AIEndpointTestResult) => void;
}>) {
  const { endpoint, canWrite, onChanged, onError } = props;
  const [editing, setEditing] = useState(false);
  const [baseUrl, setBaseUrl] = useState(endpoint.base_url);
  const [newKey, setNewKey] = useState("");
  const [testing, setTesting] = useState(false);
  const [addingModel, setAddingModel] = useState(false);

  const save = async () => {
    try {
      await aiApi.patchEndpoint(endpoint.id, { base_url: baseUrl.trim() });
      setEditing(false);
      onChanged("Адрес изменён");
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const rotateKey = async () => {
    if (!newKey.trim()) return;
    try {
      await aiApi.setEndpointSecret(endpoint.id, newKey.trim());
      setNewKey("");
      onChanged("Ключ доступа сохранён");
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const runTest = async () => {
    setTesting(true);
    try {
      const result = await aiApi.testEndpoint(endpoint.id);
      props.onTestResult(endpoint.id, result);
      onChanged(
        result.success
          ? `Связь с «${endpoint.name}» есть (${result.latency_ms} мс)`
          : `Связи с «${endpoint.name}» нет`
      );
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setTesting(false);
    }
  };

  const toggle = async () => {
    try {
      await aiApi.patchEndpoint(endpoint.id, { enabled: !endpoint.enabled });
      onChanged(
        endpoint.enabled ? "Подключение выключено" : "Подключение включено"
      );
    } catch (e) {
      onError((e as Error).message);
    }
  };

  return (
    <div className="subcard">
      <div className="card-head" style={{ marginBottom: 10 }}>
        <div>
          <div className="inline-list">
            <strong>{endpoint.name}</strong>
            <Status tone={endpoint.enabled ? "ok" : "neutral"}>
              {endpoint.enabled ? "включено" : "выключено"}
            </Status>
            <ConnectionStatus endpoint={endpoint} />
          </div>
          <p className="field-hint" style={{ marginTop: 4 }}>
            <code>{endpoint.base_url}</code>
            {endpoint.has_api_key
              ? ` · ключ сохранён (${endpoint.masked_api_key})`
              : " · ключ не задан"}
          </p>
        </div>

        {canWrite && (
          <div className="card-actions">
            <button
              type="button"
              className="small"
              onClick={runTest}
              disabled={testing}
            >
              {testing ? "Проверяем…" : "Проверить связь"}
            </button>
            <button type="button" className="small" onClick={toggle}>
              {endpoint.enabled ? "Выключить" : "Включить"}
            </button>
            <button
              type="button"
              className="small"
              onClick={() => setEditing(!editing)}
            >
              {editing ? "Отмена" : "Изменить"}
            </button>
            <button
              type="button"
              className="small danger"
              onClick={() =>
                runDelete(
                  `подключение «${endpoint.name}»`,
                  () => aiApi.deleteEndpoint(endpoint.id),
                  onChanged,
                  onError
                )
              }
            >
              Удалить
            </button>
          </div>
        )}
      </div>

      {props.testResult && !props.testResult.success && (
        <Notice tone="bad" title="Связь не установлена">
          {props.testResult.message}
        </Notice>
      )}

      {editing && (
        <div className="form-grid" style={{ marginBottom: 12 }}>
          <Field label="Адрес" hint="Полный адрес вместе с версией, например /v1.">
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              aria-label="Адрес подключения"
            />
          </Field>
          <Field
            label="Новый ключ доступа"
            hint="Заменит текущий. Прежний ключ восстановить нельзя."
          >
            <input
              type="password"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              placeholder="Оставьте пустым, чтобы не менять"
              autoComplete="off"
              aria-label="Новый ключ доступа"
            />
          </Field>
          <div className="field">
            <span className="field-label">&nbsp;</span>
            <div className="button-row">
              <button type="button" className="primary" onClick={save}>
                Сохранить адрес
              </button>
              <button
                type="button"
                onClick={rotateKey}
                disabled={newKey.trim().length === 0}
              >
                Сохранить ключ
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="inline-list" style={{ marginBottom: 8 }}>
        <h4 className="section-title" style={{ margin: 0 }}>
          Модели
        </h4>
        {canWrite && !addingModel && (
          <button
            type="button"
            className="small ghost"
            onClick={() => setAddingModel(true)}
          >
            + добавить модель
          </button>
        )}
      </div>

      {addingModel && (
        <NewModel
          canWrite={canWrite}
          onChanged={onChanged}
          onError={onError}
          endpointId={endpoint.id}
          onClose={() => setAddingModel(false)}
        />
      )}

      {props.models.length === 0 ? (
        <p className="field-hint">
          Моделей нет. Добавьте хотя бы одну — иначе задаче нечего использовать.
        </p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Модель</th>
                <th>Состояние</th>
                {canWrite && <th>Действия</th>}
              </tr>
            </thead>
            <tbody>
              {props.models.map((model) => (
                <ModelRow
                  key={model.id}
                  canWrite={canWrite}
                  onChanged={onChanged}
                  onError={onError}
                  model={model}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// --- Модели -------------------------------------------------------------------

/**
 * Выбор моделей из списка, который отдаёт сам сервис.
 *
 * Раньше идентификатор модели вводили руками из документации: опечатка
 * обнаруживалась только при генерации. Список приходит от сервиса, поэтому
 * выбрать можно лишь то, что действительно доступно по этому ключу.
 * Список может содержать сотни моделей, поэтому он скроллируется и
 * фильтруется по подстроке.
 */
function NewModel(props: Readonly<Shared & {
  endpointId: number;
  onClose: () => void;
}>) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [available, setAvailable] = useState<AIDiscoveredModel[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  const [saving, setSaving] = useState(false);
  // Ручной ввод нужен, когда сервис не умеет отдавать список моделей.
  const [manualId, setManualId] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const res = await aiApi.availableModels(props.endpointId);
      setAvailable(res.items);
    } catch (e) {
      setLoadError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [props.endpointId]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = (modelId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(modelId)) next.delete(modelId);
      else next.add(modelId);
      return next;
    });
  };

  const addSelected = async () => {
    setSaving(true);
    try {
      const result = await aiApi.addModelsBulk(
        props.endpointId,
        Array.from(selected)
      );
      props.onClose();
      const skipped = result.skipped.length
        ? `, уже были добавлены: ${result.skipped.length}`
        : "";
      props.onChanged(`Добавлено моделей: ${result.added.length}${skipped}`);
    } catch (e) {
      props.onError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const addManual = async () => {
    setSaving(true);
    try {
      await aiApi.addModelsBulk(props.endpointId, [manualId.trim()]);
      props.onClose();
      props.onChanged("Модель добавлена");
    } catch (e) {
      props.onError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const needle = filter.trim().toLowerCase();
  const visible = needle
    ? available.filter(
        (m) =>
          m.model_id.toLowerCase().includes(needle) ||
          m.display_name.toLowerCase().includes(needle)
      )
    : available;

  return (
    <div className="subcard">
      {loading && <p className="field-hint">Спрашиваем у сервиса список моделей…</p>}

      {!loading && loadError && (
        <>
          <Notice tone="warn" title="Список моделей получить не удалось">
            {loadError} Введите название модели вручную — точно так, как оно
            указано в документации сервиса.
          </Notice>
          <Field
            label="Название у поставщика"
            hint="Система передаёт это значение как есть."
          >
            <input
              type="text"
              value={manualId}
              onChange={(e) => setManualId(e.target.value)}
              placeholder="gpt-4o-mini"
              aria-label="Название модели у поставщика"
            />
          </Field>
          <div className="button-row" style={{ marginTop: 12 }}>
            <button
              type="button"
              className="primary"
              onClick={addManual}
              disabled={manualId.trim().length === 0 || saving}
            >
              {saving ? "Добавляем…" : "Добавить"}
            </button>
            <button type="button" onClick={load} disabled={saving}>
              Повторить запрос
            </button>
            <button type="button" className="ghost" onClick={props.onClose}>
              Отмена
            </button>
          </div>
        </>
      )}

      {!loading && !loadError && available.length === 0 && (
        <>
          <p className="field-hint">
            Сервис не сообщил ни одной модели. Проверьте ключ доступа и адрес
            подключения.
          </p>
          <div className="button-row" style={{ marginTop: 12 }}>
            <button type="button" onClick={load}>
              Повторить запрос
            </button>
            <button type="button" className="ghost" onClick={props.onClose}>
              Закрыть
            </button>
          </div>
        </>
      )}

      {!loading && !loadError && available.length > 0 && (
        <>
          <Field
            label="Поиск по списку"
            hint={`Сервис предоставляет ${available.length} шт. Отметьте те, которые нужны системе.`}
          >
            <input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="часть названия"
              aria-label="Поиск модели"
            />
          </Field>

          <div className="pick-list" style={{ marginTop: 8 }}>
            {visible.length === 0 ? (
              <p className="field-hint" style={{ padding: 12, margin: 0 }}>
                По запросу ничего не найдено.
              </p>
            ) : (
              visible.map((model) => (
                <label
                  key={model.model_id}
                  className={
                    model.already_added
                      ? "pick-list-item is-disabled"
                      : "pick-list-item"
                  }
                >
                  <input
                    type="checkbox"
                    checked={selected.has(model.model_id)}
                    disabled={model.already_added}
                    onChange={() => toggle(model.model_id)}
                  />
                  <span className="pick-list-text">
                    <code>{model.model_id}</code>
                    <span className="field-hint" style={{ margin: 0 }}>
                      {model.already_added
                        ? "уже добавлена"
                        : model.owned_by
                          ? `поставщик модели: ${model.owned_by}`
                          : "\u00a0"}
                    </span>
                  </span>
                </label>
              ))
            )}
          </div>

          <div className="button-row" style={{ marginTop: 12 }}>
            <button
              type="button"
              className="primary"
              onClick={addSelected}
              disabled={selected.size === 0 || saving}
            >
              {saving ? "Добавляем…" : `Добавить выбранные (${selected.size})`}
            </button>
            <button type="button" onClick={load} disabled={saving}>
              Обновить список
            </button>
            <button type="button" className="ghost" onClick={props.onClose}>
              Отмена
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function ModelRow(props: Readonly<Shared & { model: AIModelItem }>) {
  const { model, canWrite, onChanged, onError } = props;
  const [editing, setEditing] = useState(false);
  const [displayName, setDisplayName] = useState(model.display_name);

  const save = async () => {
    try {
      await aiApi.patchModel(model.id, { display_name: displayName.trim() });
      setEditing(false);
      onChanged("Модель изменена");
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const toggle = async () => {
    try {
      await aiApi.patchModel(model.id, { enabled: !model.enabled });
      onChanged(model.enabled ? "Модель выключена" : "Модель включена");
    } catch (e) {
      onError((e as Error).message);
    }
  };

  if (editing) {
    return (
      <tr>
        <td colSpan={canWrite ? 3 : 2}>
          <div className="field-row">
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              aria-label="Отображаемое название модели"
              style={{ maxWidth: 320 }}
            />
            <button type="button" className="primary small" onClick={save}>
              Сохранить
            </button>
            <button
              type="button"
              className="ghost small"
              onClick={() => setEditing(false)}
            >
              Отмена
            </button>
          </div>
        </td>
      </tr>
    );
  }

  return (
    <tr>
      <td>
        <strong>{model.display_name}</strong>
        <div className="field-hint">
          <code>{model.model_id}</code>
        </div>
      </td>
      <td>
        <Status tone={model.enabled ? "ok" : "neutral"}>
          {model.enabled ? "включена" : "выключена"}
        </Status>
      </td>
      {canWrite && (
        <td className="actions">
          <div className="button-row">
            <button type="button" className="small" onClick={toggle}>
              {model.enabled ? "Выключить" : "Включить"}
            </button>
            <button
              type="button"
              className="small"
              onClick={() => setEditing(true)}
            >
              Изменить
            </button>
            <button
              type="button"
              className="small danger"
              onClick={() =>
                runDelete(
                  `модель «${model.display_name}»`,
                  () => aiApi.deleteModel(model.id),
                  onChanged,
                  onError
                )
              }
            >
              Удалить
            </button>
          </div>
        </td>
      )}
    </tr>
  );
}
