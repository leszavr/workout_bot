"use client";

// Примитивы интерфейса.
//
// Нужны, чтобы подпись, подсказка и состояние выглядели одинаково во всех
// разделах. До этого поля стояли рядами с одними placeholder'ами: что именно
// вводить и зачем — приходилось угадывать.

import { ReactNode } from "react";

/** Поле формы: подпись сверху, подсказка снизу, ошибка вместо подсказки. */
export function Field(props: Readonly<{
  label: string;
  hint?: string;
  error?: string;
  htmlFor?: string;
  children: ReactNode;
}>) {
  return (
    <div className="field">
      <label htmlFor={props.htmlFor}>{props.label}</label>
      {props.children}
      {props.error ? (
        <span className="field-error">{props.error}</span>
      ) : (
        props.hint && <span className="field-hint">{props.hint}</span>
      )}
    </div>
  );
}

/** Карточка с заголовком, пояснением и действиями в шапке. */
export function Card(props: Readonly<{
  title?: string;
  description?: string;
  actions?: ReactNode;
  children?: ReactNode;
}>) {
  return (
    <section className="card">
      {(props.title || props.actions) && (
        <div className="card-head">
          <div>
            {props.title && <h2 className="card-title">{props.title}</h2>}
            {props.description && <p className="card-desc">{props.description}</p>}
          </div>
          {props.actions && <div className="card-actions">{props.actions}</div>}
        </div>
      )}
      {props.children}
    </section>
  );
}

export type Tone = "ok" | "warn" | "bad" | "info" | "neutral";

/** Статус: цвет + точка + текст. Читается и без различения цветов. */
export function Status(props: Readonly<{ tone: Tone; children: ReactNode }>) {
  const cls = props.tone === "neutral" ? "badge" : `badge ${props.tone}`;
  return (
    <span className={cls}>
      <span className="dot" aria-hidden="true" />
      {props.children}
    </span>
  );
}

/** Метка без точки: для признаков, а не состояний. */
export function Tag(props: Readonly<{ tone?: Tone; children: ReactNode }>) {
  const tone = props.tone ?? "neutral";
  return <span className={tone === "neutral" ? "badge" : `badge ${tone}`}>{props.children}</span>;
}

/** Сообщение над содержимым: пояснение, предупреждение, ошибка. */
export function Notice(props: Readonly<{
  tone?: Tone;
  title?: string;
  children?: ReactNode;
}>) {
  const tone = props.tone ?? "info";
  return (
    <div className={`notice ${tone}`}>
      <div>
        {props.title && <div className="notice-title">{props.title}</div>}
        {props.children}
      </div>
    </div>
  );
}

/**
 * Пустое состояние: объясняет, почему пусто и что сделать.
 * Пустой список без объяснения выглядит как поломка.
 */
export function Empty(props: Readonly<{
  title: string;
  hint?: string;
  action?: ReactNode;
}>) {
  return (
    <div className="empty">
      <div className="empty-title">{props.title}</div>
      {props.hint && <p className="empty-hint">{props.hint}</p>}
      {props.action}
    </div>
  );
}

/** Скелет вместо текста «Загрузка...»: меньше дёрганья при появлении данных. */
export function Skeleton(props: Readonly<{ rows?: number }>) {
  const rows = props.rows ?? 3;
  return (
    <div aria-busy="true" aria-live="polite">
      {Array.from({ length: rows }).map((_, index) => (
        <div
          key={index}
          className="skeleton"
          style={{ width: index === rows - 1 ? "60%" : "100%" }}
        />
      ))}
    </div>
  );
}

/** Дата и время в локальном формате; «—» вместо пустоты. */
export function moment(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString("ru-RU") : "—";
}
