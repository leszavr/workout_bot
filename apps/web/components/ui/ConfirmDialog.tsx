"use client";

// Подтверждение необратимого действия.
//
// Заменяет `window.confirm`, который использовался в шести местах. Системное
// окно нельзя объяснить: в нём нет места для перечисления последствий («будут
// удалены все версии и записи об отправке»), нет различения обычного и
// разрушительного действия, и оно блокирует поток страницы целиком, из-за чего
// список за ним не успевает обновиться.
//
// Диалог здесь только для необратимых операций. Обычные действия (открыть
// форму, сменить фильтр) выполняются на месте: модальное окно для простого
// действия добавляет два нажатия и ничего не сообщает.

import { useEffect, useRef } from "react";

export function ConfirmDialog(props: Readonly<{
  title: string;
  /** Что именно произойдёт. Показывается до подтверждения, а не после. */
  description?: string;
  confirmLabel: string;
  cancelLabel?: string;
  /** Действие необратимо: кнопка подтверждения окрашивается как опасная. */
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}>) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  // Фокус переходит на подтверждение, чтобы диалог был управляем с клавиатуры,
  // а Escape закрывает его: иначе выйти можно только мышью.
  useEffect(() => {
    confirmRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") props.onCancel();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [props]);

  return (
    <div className="dialog-scrim" role="presentation">
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
      >
        <h2 className="card-title" id="confirm-title">
          {props.title}
        </h2>
        {props.description && <p className="card-desc">{props.description}</p>}
        <div className="button-row dialog-actions">
          <button
            type="button"
            ref={confirmRef}
            className={props.danger ? "danger" : "primary"}
            onClick={props.onConfirm}
            disabled={props.busy}
          >
            {props.busy ? "Выполняем…" : props.confirmLabel}
          </button>
          <button
            type="button"
            className="ghost"
            onClick={props.onCancel}
            disabled={props.busy}
          >
            {props.cancelLabel ?? "Отмена"}
          </button>
        </div>
      </div>
    </div>
  );
}
