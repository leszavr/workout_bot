"use client";

// Вкладки внутри карточки сущности.
//
// В отличие от `PageTabs`, эти вкладки не меняют адрес: они делят один экран
// одной сущности. Карточка упражнения содержит характеристики, требования,
// замены, технику, изображения и происхождение — вертикальным списком это
// несколько экранов прокрутки, в которых характеристики и требования нельзя
// увидеть рядом.
//
// Состояние держит вызывающая страница: она знает, какую вкладку показать после
// действия (например, вернуться к списку программ после сборки).

export interface LocalTab {
  id: string;
  label: string;
  /** Число рядом с подписью: сколько записей внутри. */
  count?: number;
}

export function LocalTabs(props: Readonly<{
  tabs: readonly LocalTab[];
  active: string;
  onChange: (id: string) => void;
  label: string;
}>) {
  return (
    <div className="tabs" role="tablist" aria-label={props.label}>
      {props.tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={tab.id === props.active}
          className={tab.id === props.active ? "active" : ""}
          onClick={() => props.onChange(tab.id)}
        >
          {tab.label}
          {tab.count !== undefined && (
            <span className="tab-count">{tab.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}
