"use client";

// Крупное число с пояснением: показатель, а не просто цифра.
//
// У каждого KPI обязательна подпись, объясняющая, что именно посчитано.
// «Успешность 96%» без указания единицы (генерации или вызовы ИИ) допускает
// два разных прочтения, и одно из них будет неверным.
//
// Отсутствующее значение показывается как «—» с пояснением «считать не на
// чем». Ноль означал бы измеренный результат, которого не было.

import { ReactNode } from "react";

import { Tone } from "@/components/ui/Primitives";

export function Metric(props: Readonly<{
  label: string;
  value: string;
  hint: string;
  /** Дополнительная строка: связанное число или доля. */
  secondary?: string;
  tone?: Tone;
  action?: ReactNode;
}>) {
  const color =
    props.tone === "bad"
      ? "var(--danger)"
      : props.tone === "warn"
        ? "var(--warn)"
        : props.tone === "ok"
          ? "var(--ok)"
          : "var(--text)";
  return (
    <div className="stat">
      <div className="value" style={{ color }}>
        {props.value}
      </div>
      <div className="label">{props.label}</div>
      {props.secondary && <div className="muted">{props.secondary}</div>}
      <p className="field-hint">{props.hint}</p>
      {props.action}
    </div>
  );
}

/**
 * Предупреждение о недостаточной выборке.
 *
 * Показывается там, где проценты уже посчитаны, но опираться на них нельзя:
 * при трёх генерациях одна ошибка меняет «успешность» на 33 пункта. Без такой
 * пометки интерфейс выглядел бы уверенным ровно там, где уверенности нет.
 */
export function SampleWarning(props: Readonly<{
  generations: number;
  minConfident: number;
}>) {
  return (
    <div className="notice warn">
      <div>
        <div className="notice-title">Данных мало: проценты ненадёжны</div>
        <p style={{ margin: 0 }}>
          В выборку попало генераций: {props.generations}. Доли начинают что-то
          означать примерно с {props.minConfident}: при меньшем числе одна
          неудача меняет процент на десятки пунктов. Числа ниже показаны как
          есть, но выводов по ним делать нельзя.
        </p>
      </div>
    </div>
  );
}
