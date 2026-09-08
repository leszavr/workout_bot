"use client";

// Заголовок рабочей области.
//
// Один компонент на все разделы: до этого каждая страница верстала
// `page-head` руками, и заголовок, пояснение, вкладки и действия раздела
// стояли в разном порядке. Действия раздела (создать, обновить) живут справа от
// заголовка, а не внизу страницы: их ищут глазами там, где написано, чем эта
// страница является.

import { ReactNode } from "react";

export function PageHeader(props: Readonly<{
  title: string;
  description?: string;
  actions?: ReactNode;
  /** Вкладки раздела: показываются под заголовком, до содержимого. */
  tabs?: ReactNode;
}>) {
  return (
    <div className="page-head">
      <div className="page-head-row">
        <div className="page-head-text">
          <h1 className="page-title">{props.title}</h1>
          {props.description && (
            <p className="page-subtitle">{props.description}</p>
          )}
        </div>
        {props.actions && <div className="page-head-actions">{props.actions}</div>}
      </div>
      {props.tabs}
    </div>
  );
}
