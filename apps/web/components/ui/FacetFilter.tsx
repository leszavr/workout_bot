"use client";

// Фильтр по признаку с счётчиками (facet).
//
// Счётчик рядом со значением отвечает на вопрос «сколько будет, если выбрать»
// до нажатия: фильтр без счётчиков заставляет угадывать и приводит к пустым
// выборкам. Числа приходят по той же выборке, что и список, поэтому не обещают
// результатов, которых нет.
//
// Значение с нулевым счётчиком не скрывается, если оно выбрано: исчезновение
// выбранного фильтра выглядело бы как сброс, который пользователь не делал.

import { FacetCount } from "@/lib/api";

export function FacetFilter(props: Readonly<{
  label: string;
  hint?: string;
  options: ReadonlyArray<FacetCount>;
  selected: ReadonlyArray<string>;
  onToggle: (value: string) => void;
  /** Русская подпись значения: в каталоге теги хранятся по-английски. */
  labelFor: (value: string) => string;
  /** Сколько значений показывать без раскрытия списка. */
  maxVisible?: number;
}>) {
  const { options, selected, onToggle, labelFor } = props;
  const visible = options.filter(
    (option) => option.count > 0 || selected.includes(option.value),
  );

  if (visible.length === 0) {
    return (
      <div className="field">
        <span className="field-label">{props.label}</span>
        <p className="field-hint">Нет значений в текущей выборке.</p>
      </div>
    );
  }

  return (
    <fieldset
      className="field"
      style={{ border: "none", margin: 0, padding: 0, minWidth: 0 }}
    >
      <legend className="field-label" style={{ padding: 0 }}>
        {props.label}
      </legend>
      {props.hint && <p className="field-hint">{props.hint}</p>}
      <div
        className="pick-list"
        style={{ maxHeight: (props.maxVisible ?? 6) * 34 }}
      >
        {visible.map((option) => (
          <label className="pick-list-item" key={option.value}>
            <input
              type="checkbox"
              checked={selected.includes(option.value)}
              onChange={() => onToggle(option.value)}
            />
            <span className="pick-list-text">
              <span>{labelFor(option.value)}</span>
            </span>
            <span className="muted" style={{ marginLeft: "auto" }}>
              {option.count}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
