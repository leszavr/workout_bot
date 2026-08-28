"use client";

// Инструкции для ИИ (промпты).
//
// До этого раздела текст инструкции существовал только в файлах образа и в
// базе: чтобы поправить формулировку, администратору приходилось лезть в SQL
// или пересобирать контейнер. Промпт-инжиниринг — это итерации, поэтому здесь
// он делается штатно: посмотреть целиком → изменить → выбрать для задачи →
// запустить генерацию → посмотреть результат.
//
// Полный текст не усечён нигде: администратор должен видеть ровно то, что
// уходит в модель. Список показывает только превью, а сам текст загружается
// при открытии карточки — инструкция бывает в десятки килобайт.

import { useCallback, useEffect, useState } from "react";

import { Card, Empty, Field, Notice, Skeleton, Status, Tag, moment } from "@/components/ui/Primitives";
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
  const [creating, setCreating] = useState(false);
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
    load().catch(() => undefined);
  };

  return (
    <Card
      title="Инструкции для ИИ"
      description="Текст, по которому модель собирает программу. Задача использует ту версию, которая указана в её настройках; остальные хранятся для сравнения."
      actions={
        props.canWrite && !creating ? (
          <button
            type="button"
            className="primary small"
            onClick={() => setCreating(true)}
          >
            Создать инструкцию
          </button>
        ) : undefined
      }
    >
      {loading && <Skeleton rows={3} />}
      {failure && <div className="error">Не удалось загрузить инструкции: {failure}</div>}

      {creating && (
        <NewPrompt
          canWrite={props.canWrite}
          onChanged={afterChange}
          onError={props.onError}
          taskType={taskType}
          nextVersion={nextVersion}
          onClose={() => setCreating(false)}
        />
      )}

      {!loading && !failure && items.length === 0 && !creating && (
        <Empty
          title="Своих инструкций нет"
          hint="Система использует инструкцию из файлов проекта. Создайте свою, чтобы менять формулировки без пересборки."
        />
      )}

      {items.length > 0 && (
        <div className="stack">
          {items.map((item) => (
            <PromptRow
              key={item.id}
              canWrite={props.canWrite}
              onChanged={afterChange}
              onError={props.onError}
              item={item}
              isActive={item.version === activeVersion}
              open={openId === item.id}
              onToggle={() => setOpenId(openId === item.id ? null : item.id)}
            />
          ))}
        </div>
      )}
    </Card>
  );
}

// --- Создание -----------------------------------------------------------------

function NewPrompt(props: Readonly<Shared & {
  taskType: string;
  nextVersion: number;
  onClose: () => void;
}>) {
  const [name, setName] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [userTemplate, setUserTemplate] = useState("");
  const [saving, setSaving] = useState(false);

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

  return (
    <div className="subcard" style={{ marginBottom: 20 }}>
      <Notice tone="info" title={`Будет создана версия №${props.nextVersion}`}>
        Создание не переключает задачу на новую инструкцию: номер версии нужно
        указать в настройках задачи.
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

      <div className="button-row" style={{ marginTop: 12 }}>
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
    </div>
  );
}

// --- Строка списка и карточка ----------------------------------------------------

function PromptRow(props: Readonly<Shared & {
  item: AIPromptItem;
  isActive: boolean;
  open: boolean;
  onToggle: () => void;
}>) {
  const { item, canWrite } = props;

  return (
    <div className="subcard">
      <div className="card-head" style={{ marginBottom: props.open ? 12 : 0 }}>
        <div>
          <div className="inline-list">
            <strong>
              №{item.version} · {item.name}
            </strong>
            {props.isActive ? (
              <Status tone="ok">используется задачей</Status>
            ) : (
              <Status tone="neutral">не используется</Status>
            )}
            {!item.enabled && <Tag tone="warn">выключена</Tag>}
          </div>
          <p className="field-hint" style={{ marginTop: 4 }}>
            {item.system_prompt_preview}
            {item.system_prompt_length > item.system_prompt_preview.length && "…"}
          </p>
          <p className="field-hint" style={{ marginTop: 2 }}>
            правила: {item.system_prompt_length} симв. · шаблон:{" "}
            {item.user_template_length} симв. · изменена {moment(item.updated_at)}
          </p>
        </div>

        <div className="card-actions">
          <button type="button" className="small" onClick={props.onToggle}>
            {props.open ? "Свернуть" : "Открыть полностью"}
          </button>
        </div>
      </div>

      {props.open && (
        <PromptEditor
          canWrite={canWrite}
          onChanged={props.onChanged}
          onError={props.onError}
          promptId={item.id}
        />
      )}
    </div>
  );
}

function PromptEditor(props: Readonly<Shared & { promptId: number }>) {
  const { promptId, canWrite } = props;
  const [prompt, setPrompt] = useState<AIPromptDetail | null>(null);
  const [systemPrompt, setSystemPrompt] = useState("");
  const [userTemplate, setUserTemplate] = useState("");
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState("");
  const [saving, setSaving] = useState(false);

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
    if (
      !window.confirm(
        `Удалить инструкцию №${prompt.version} «${prompt.name}»? Отменить это нельзя.`
      )
    ) {
      return;
    }
    try {
      await aiApi.deletePrompt(promptId);
      props.onChanged(`Инструкция №${prompt.version} удалена`);
    } catch (e) {
      const error = e as ApiError;
      const blockers = error.blockers?.map((b) => b.detail).join("; ");
      props.onError(
        blockers ? `${error.message} Мешает: ${blockers}` : error.message
      );
    }
  };

  if (loading) return <Skeleton rows={4} />;
  if (failure) return <div className="error">Не удалось загрузить текст: {failure}</div>;
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
        <div className="button-row" style={{ marginTop: 16 }}>
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
            className="small danger"
            onClick={remove}
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
    </>
  );
}
