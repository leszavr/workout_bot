"use client";

// Инструкции для ИИ (промпты).
//
// Единственный источник инструкций. Раньше текст жил в двух местах — в базе и в
// файлах образа, — и файловая версия была недоступна: её нельзя было прочитать,
// изменить или заменить, а задача с пустой версией молча брала её. Базовая
// инструкция перенесена в базу миграцией и стала обычной версией: её видно,
// можно править, копировать и удалять. Ничем не выделенной — если созданная
// позже окажется лучше, базовую незачем сохранять.
//
// Рабочий цикл: посмотреть целиком → скопировать за основу → изменить →
// выбрать для задачи → запустить генерацию → посмотреть результат.
//
// Версии показаны таблицей, как остальные списки интерфейса: раньше это был
// столбец карточек, где каждая строка занимала треть экрана, и сравнить пять
// версий по размеру и дате правки было нельзя без прокрутки. Полный текст не
// усечён нигде: администратор должен видеть ровно то, что уходит в модель.
// Список показывает только превью, а сам текст загружается при открытии
// карточки — инструкция бывает в десятки килобайт.

import { useCallback, useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataColumn, DataTable } from "@/components/ui/DataTable";
import {
  Card,
  ErrorState,
  Field,
  Notice,
  Skeleton,
  Status,
  Tag,
  moment,
} from "@/components/ui/Primitives";
import { AIPromptDetail, AIPromptItem, ApiError, aiApi } from "@/lib/api";

interface Shared {
  canWrite: boolean;
  onChanged: (message: string) => void;
  onError: (message: string) => void;
}

export default function PromptsSection(props: Readonly<Shared & {
  taskType: string;
  reloadKey: number;
}>) {
  const { taskType, reloadKey } = props;
  const [items, setItems] = useState<AIPromptItem[]>([]);
  const [activeVersion, setActiveVersion] = useState<number | null>(null);
  const [nextVersion, setNextVersion] = useState(1);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState("");
  // null — форма закрыта; иначе id инструкции-образца или 0 для пустой формы.
  const [creatingFrom, setCreatingFrom] = useState<number | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await aiApi.prompts(taskType);
      setItems(data.items);
      setActiveVersion(data.active_version);
      setNextVersion(data.next_version);
      setFailure("");
    } catch (e) {
      setFailure((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [taskType]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load, reloadKey]);

  const afterChange = (message: string) => {
    props.onChanged(message);
    setOpenId(null);
    load().catch(() => undefined);
  };

  const columns: ReadonlyArray<DataColumn<AIPromptItem>> = [
    {
      key: "version",
      header: "Версия",
      render: (item) => (
        <>
          <strong>
            №{item.version} · {item.name}
          </strong>
          <p className="field-hint" style={{ margin: "2px 0 0" }}>
            {item.system_prompt_preview}
            {item.system_prompt_length > item.system_prompt_preview.length &&
              "…"}
          </p>
        </>
      ),
    },
    {
      key: "usage",
      header: "Используется",
      hint: "Задача берёт ту версию, которая выбрана в её настройках; остальные хранятся для сравнения.",
      render: (item) => (
        <>
          {item.version === activeVersion ? (
            <Status tone="ok">используется задачей</Status>
          ) : (
            <Status tone="neutral">не используется</Status>
          )}
          {!item.enabled && <Tag tone="warn">выключена</Tag>}
        </>
      ),
    },
    {
      key: "size",
      header: "Размер",
      numeric: true,
      hint: "Символов в правилах и в шаблоне запроса.",
      render: (item) => (
        <>
          {item.system_prompt_length}
          <div className="muted" style={{ fontSize: 12 }}>
            шаблон: {item.user_template_length}
          </div>
        </>
      ),
    },
    {
      key: "updated",
      header: "Изменена",
      render: (item) => (
        <span className="muted">{moment(item.updated_at)}</span>
      ),
    },
    {
      key: "actions",
      header: "Действия",
      render: (item) => (
        <div className="button-row">
          <button
            type="button"
            className="small"
            onClick={() => setOpenId(openId === item.id ? null : item.id)}
          >
            {openId === item.id ? "Свернуть" : "Открыть текст"}
          </button>
          {props.canWrite && (
            <button
              type="button"
              className="small"
              onClick={() => setCreatingFrom(item.id)}
            >
              Копия
            </button>
          )}
        </div>
      ),
    },
  ];

  const open = openId ? items.find((item) => item.id === openId) : undefined;

  return (
    <>
      {creatingFrom !== null && (
        <NewPrompt
          canWrite={props.canWrite}
          onChanged={afterChange}
          onError={props.onError}
          taskType={taskType}
          nextVersion={nextVersion}
          sourceId={creatingFrom || null}
          onClose={() => setCreatingFrom(null)}
        />
      )}

      <Card
        title="Инструкции для ИИ"
        description="Текст, по которому модель собирает программу. Задача использует ту версию, которая выбрана в её настройках; остальные хранятся для сравнения. Другого источника инструкций нет."
        actions={
          props.canWrite && creatingFrom === null ? (
            <button
              type="button"
              className="primary small"
              onClick={() => setCreatingFrom(0)}
            >
              Создать инструкцию
            </button>
          ) : undefined
        }
      >
        <DataTable
          columns={columns}
          rows={items}
          rowKey={(item) => String(item.id)}
          loading={loading}
          error={
            failure ? `Не удалось загрузить инструкции: ${failure}` : undefined
          }
          emptyTitle="Инструкций нет"
          emptyHint="Без инструкции ИИ вызвать нельзя: задача останется не готовой, и программу соберёт алгоритмический генератор."
        />
      </Card>

      {open && (
        <Card
          title={`Инструкция №${open.version} · ${open.name}`}
          description="Полный текст без сокращений — ровно то, что получает модель."
          actions={
            <button
              type="button"
              className="ghost small"
              onClick={() => setOpenId(null)}
            >
              Закрыть
            </button>
          }
        >
          <PromptEditor
            canWrite={props.canWrite}
            onChanged={afterChange}
            onError={props.onError}
            promptId={open.id}
          />
        </Card>
      )}
    </>
  );
}

// --- Создание -----------------------------------------------------------------

function NewPrompt(props: Readonly<Shared & {
  taskType: string;
  nextVersion: number;
  // id инструкции-образца: новая создаётся её копией. Так существующая
  // инструкция служит референсом, а не единственным рабочим текстом.
  sourceId: number | null;
  onClose: () => void;
}>) {
  const { sourceId } = props;
  const [name, setName] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [userTemplate, setUserTemplate] = useState("");
  const [loadingSource, setLoadingSource] = useState(sourceId !== null);
  const [sourceLabel, setSourceLabel] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (sourceId === null) return;
    let cancelled = false;
    (async () => {
      try {
        const source = await aiApi.prompt(sourceId);
        if (cancelled) return;
        setName(`${source.name} (копия)`);
        setSystemPrompt(source.system_prompt);
        setUserTemplate(source.user_template);
        setSourceLabel(`№${source.version} «${source.name}»`);
      } catch (e) {
        if (!cancelled) props.onError((e as Error).message);
      } finally {
        if (!cancelled) setLoadingSource(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // props.onError стабилен на уровне страницы; пересоздавать эффект незачем.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceId]);

  const create = async () => {
    setSaving(true);
    try {
      await aiApi.createPrompt({
        task_type: props.taskType,
        name: name.trim(),
        system_prompt: systemPrompt,
        user_template: userTemplate,
      });
      props.onClose();
      props.onChanged(`Инструкция №${props.nextVersion} создана`);
    } catch (e) {
      props.onError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  if (loadingSource) {
    return (
      <Card title="Новая инструкция">
        <Skeleton rows={3} />
      </Card>
    );
  }

  return (
    <Card
      title={`Новая инструкция (версия №${props.nextVersion})`}
      actions={
        <button type="button" className="ghost small" onClick={props.onClose}>
          Закрыть
        </button>
      }
    >
      <Notice tone="info">
        {sourceLabel
          ? `Текст скопирован из инструкции ${sourceLabel} — правьте свободно, оригинал не изменится. `
          : ""}
        Создание не переключает задачу на новую инструкцию: выберите её в
        настройках задачи.
      </Notice>

      <Field label="Название" hint="Чтобы отличать версии в списке.">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Строже про safe pool"
          aria-label="Название инструкции"
        />
      </Field>

      <Field
        label="Правила и формат ответа (system)"
        hint="Роль модели, ограничения и схема JSON. Именно этот текст уходит в модель первым сообщением."
      >
        <textarea
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          rows={16}
          className="mono"
          aria-label="Правила и формат ответа"
        />
      </Field>

      <Field
        label="Шаблон запроса (user)"
        hint="Данные анкеты и safe pool подставляются в фигурные скобки, например {sessions_per_week} и {safe_pool_exercises}."
      >
        <textarea
          value={userTemplate}
          onChange={(e) => setUserTemplate(e.target.value)}
          rows={12}
          className="mono"
          aria-label="Шаблон запроса"
        />
      </Field>

      <div className="button-row" style={{ marginTop: "var(--s-3)" }}>
        <button
          type="button"
          className="primary"
          onClick={create}
          disabled={
            saving ||
            name.trim().length === 0 ||
            systemPrompt.trim().length === 0 ||
            userTemplate.trim().length === 0
          }
        >
          {saving ? "Создаём…" : "Создать"}
        </button>
        <button type="button" className="ghost" onClick={props.onClose}>
          Отмена
        </button>
      </div>
    </Card>
  );
}

// --- Просмотр и правка ----------------------------------------------------------

function PromptEditor(props: Readonly<Shared & { promptId: number }>) {
  const { promptId, canWrite } = props;
  const [prompt, setPrompt] = useState<AIPromptDetail | null>(null);
  const [systemPrompt, setSystemPrompt] = useState("");
  const [userTemplate, setUserTemplate] = useState("");
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await aiApi.prompt(promptId);
        if (cancelled) return;
        setPrompt(data);
        setName(data.name);
        setSystemPrompt(data.system_prompt);
        setUserTemplate(data.user_template);
        setFailure("");
      } catch (e) {
        if (!cancelled) setFailure((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [promptId]);

  const save = async () => {
    setSaving(true);
    try {
      await aiApi.patchPrompt(promptId, {
        name: name.trim(),
        system_prompt: systemPrompt,
        user_template: userTemplate,
      });
      props.onChanged("Инструкция сохранена");
    } catch (e) {
      props.onError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const reset = () => {
    if (!prompt) return;
    setName(prompt.name);
    setSystemPrompt(prompt.system_prompt);
    setUserTemplate(prompt.user_template);
  };

  const remove = async () => {
    if (!prompt) return;
    setSaving(true);
    try {
      await aiApi.deletePrompt(promptId);
      setConfirming(false);
      props.onChanged(`Инструкция №${prompt.version} удалена`);
    } catch (e) {
      const error = e as ApiError;
      const blockers = error.blockers?.map((b) => b.detail).join("; ");
      props.onError(
        blockers ? `${error.message} Мешает: ${blockers}` : error.message,
      );
      setConfirming(false);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <Skeleton rows={4} />;
  if (failure) {
    return <ErrorState message={`Не удалось загрузить текст: ${failure}`} />;
  }
  if (!prompt) return null;

  const dirty =
    name !== prompt.name ||
    systemPrompt !== prompt.system_prompt ||
    userTemplate !== prompt.user_template;

  return (
    <>
      {prompt.in_use && (
        <Notice tone="warn" title="Эта инструкция выбрана в настройках задачи">
          Изменения применятся к следующей генерации. Удалить такую инструкцию
          нельзя — сначала выберите для задачи другую версию.
        </Notice>
      )}

      <Field label="Название" hint="Видно только в этом списке.">
        <input
          type="text"
          value={name}
          disabled={!canWrite}
          onChange={(e) => setName(e.target.value)}
          aria-label="Название инструкции"
        />
      </Field>

      <Field
        label="Правила и формат ответа (system)"
        hint="Полный текст без сокращений — ровно то, что получает модель."
      >
        <textarea
          value={systemPrompt}
          disabled={!canWrite}
          onChange={(e) => setSystemPrompt(e.target.value)}
          rows={20}
          className="mono"
          aria-label="Правила и формат ответа"
        />
      </Field>

      <Field
        label="Шаблон запроса (user)"
        hint="Значения в фигурных скобках подставляет система: анкета и safe pool."
      >
        <textarea
          value={userTemplate}
          disabled={!canWrite}
          onChange={(e) => setUserTemplate(e.target.value)}
          rows={14}
          className="mono"
          aria-label="Шаблон запроса"
        />
      </Field>

      {canWrite && (
        <div className="button-row" style={{ marginTop: "var(--s-4)" }}>
          <button
            type="button"
            className="primary"
            onClick={save}
            disabled={
              saving ||
              !dirty ||
              name.trim().length === 0 ||
              systemPrompt.trim().length === 0 ||
              userTemplate.trim().length === 0
            }
          >
            {saving ? "Сохраняем…" : "Сохранить"}
          </button>
          <button type="button" onClick={reset} disabled={saving || !dirty}>
            Отменить правки
          </button>
          <button
            type="button"
            className="danger"
            onClick={() => setConfirming(true)}
            disabled={saving || prompt.in_use}
            title={
              prompt.in_use
                ? "Инструкция выбрана в настройках задачи"
                : undefined
            }
          >
            Удалить
          </button>
        </div>
      )}

      {confirming && (
        <ConfirmDialog
          title={`Удалить инструкцию №${prompt.version} «${prompt.name}»?`}
          description="Версия исчезнет вместе с текстом. Отменить это нельзя, а версия, выбранная в настройках задачи, не удаляется вовсе."
          confirmLabel="Удалить инструкцию"
          danger
          busy={saving}
          onConfirm={remove}
          onCancel={() => setConfirming(false)}
        />
      )}
    </>
  );
}
