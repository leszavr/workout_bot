"use client";

// Панель отбора над списком.
//
// Единое место для поиска, фильтров и кнопок «Показать»/«Сбросить». Раньше
// каждая страница собирала эту панель сама, и она вела себя по-разному: где-то
// поиск применялся по Enter, где-то только кнопкой, а счётчик активных фильтров
// был лишь в каталоге упражнений.
//
// Счётчик в кнопке сброса важен: часть фильтров живёт в раскрытом блоке, и без
// числа непонятно, почему список короче ожидаемого.

import { ReactNode } from "react";

export function FilterBar(props: Readonly<{
  children: ReactNode;
  /** Применить фильтры. Без него панель работает как набор мгновенных фильтров. */
  onApply?: () => void;
  onReset?: () => void;
  /** Сколько условий отбора задано. Кнопка сброса показывается только при > 0. */
  activeCount?: number;
  /** Блок с дополнительными фильтрами: показывается ниже основной строки. */
  extra?: ReactNode;
}>) {
  const active = props.activeCount ?? 0;

  return (
    <>
      <div className="filters">
        {props.children}
        {(props.onApply || props.onReset) && (
          <div className="filters-actions">
            {props.onApply && (
              <button type="button" className="primary" onClick={props.onApply}>
                Показать
              </button>
            )}
            {props.onReset && active > 0 && (
              <button type="button" className="ghost" onClick={props.onReset}>
                Сбросить ({active})
              </button>
            )}
          </div>
        )}
      </div>
      {props.extra}
    </>
  );
}

/**
 * Дополнительные условия отбора, скрытые до раскрытия.
 *
 * Каталог упражнений имеет девять групп фильтров: развёрнутыми они занимали
 * почти весь экран, и сам список — то, за чем пришли — начинался за сгибом.
 * Скрытая группа сообщает в заголовке, сколько условий внутри задано, поэтому
 * непонятного сужения выборки не возникает.
 *
 * Нативные details/summary, а не своё состояние: раскрытие работает без
 * JavaScript и правильно озвучивается скринридером.
 */
export function FilterGroup(props: Readonly<{
  title: string;
  hint?: string;
  activeCount?: number;
  defaultOpen?: boolean;
  children: ReactNode;
}>) {
  const active = props.activeCount ?? 0;
  return (
    <details className="filter-group" open={props.defaultOpen || active > 0}>
      <summary>
        <span className="filter-group-title">{props.title}</span>
        {active > 0 && <span className="tab-count">{active}</span>}
      </summary>
      {props.hint && <p className="field-hint">{props.hint}</p>}
      <div className="form-grid">{props.children}</div>
    </details>
  );
}

/**
 * Поле поиска, применяющееся по Enter и по кнопке.
 *
 * Отдельный компонент, потому что правило одно на весь интерфейс: запрос на
 * каждое нажатие клавиши бил бы по базе на каждый символ, а поиск, срабатывающий
 * только кнопкой, заставляет искать эту кнопку глазами.
 */
export function SearchInput(props: Readonly<{
  id: string;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  placeholder?: string;
  ariaLabel?: string;
}>) {
  return (
    <input
      id={props.id}
      type="search"
      placeholder={props.placeholder}
      aria-label={props.ariaLabel}
      value={props.value}
      onChange={(event) => props.onChange(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") props.onSubmit();
      }}
    />
  );
}
