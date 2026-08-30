"use client";

// Постраничная навигация для серверной пагинации.
//
// Компонент показывает границы страницы и общее число записей, а не только
// стрелки: «показано 26–50 из 312» отвечает на вопрос «сколько ещё осталось»,
// на который номер страницы сам по себе не отвечает.
//
// Кнопки блокируются на границах выборки, а не скрываются: исчезающий элемент
// управления смещает соседние и выглядит как сбой интерфейса.

export function Pagination(props: Readonly<{
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
  disabled?: boolean;
}>) {
  const { total, limit, offset, onChange, disabled } = props;
  if (total === 0) return null;

  const from = offset + 1;
  const to = Math.min(offset + limit, total);
  const hasPrevious = offset > 0;
  const hasNext = to < total;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));

  return (
    <div
      className="field-row"
      style={{ justifyContent: "space-between", marginTop: "var(--s-4)" }}
    >
      <span className="muted">
        Показано {from}–{to} из {total.toLocaleString("ru-RU")}
      </span>
      <div className="button-row">
        <button
          type="button"
          className="small"
          onClick={() => onChange(0)}
          disabled={disabled || !hasPrevious}
        >
          В начало
        </button>
        <button
          type="button"
          className="small"
          onClick={() => onChange(Math.max(0, offset - limit))}
          disabled={disabled || !hasPrevious}
        >
          Назад
        </button>
        <span className="muted">
          {page} / {pages}
        </span>
        <button
          type="button"
          className="small"
          onClick={() => onChange(offset + limit)}
          disabled={disabled || !hasNext}
        >
          Вперёд
        </button>
      </div>
    </div>
  );
}
