"use client";

// Графики аналитики на голом SVG.
//
// Почему без библиотеки графиков. Требуется два типа диаграмм на фиксированных
// данных без интерактивности; библиотека добавила бы зависимость и бандл ради
// того, что здесь занимает несколько десятков строк. Как только понадобятся
// зум, кисти или сотни серий — это станет неверным решением, и библиотеку надо
// будет взять.
//
// Отсутствующее значение (null) не рисуется как ноль: ноль означал бы
// «показатель равен нулю», а не «данных нет», и линия провалилась бы вниз,
// показав отказ там, где просто не было генераций.

import { ReactNode } from "react";

const PALETTE = {
  accent: "var(--accent)",
  ok: "var(--ok)",
  danger: "var(--danger)",
  warn: "var(--warn)",
  neutral: "var(--neutral)",
} as const;

export type SeriesTone = keyof typeof PALETTE;

export interface ChartPoint {
  label: string;
  value: number | null;
}

function ChartFrame(props: Readonly<{
  title?: string;
  legend?: ReactNode;
  children: ReactNode;
}>) {
  return (
    <figure style={{ margin: 0 }}>
      {(props.title || props.legend) && (
        <figcaption
          className="field-row"
          style={{ justifyContent: "space-between", marginBottom: "var(--s-3)" }}
        >
          {props.title && <span className="field-label">{props.title}</span>}
          {props.legend}
        </figcaption>
      )}
      {props.children}
    </figure>
  );
}

export function ChartLegend(props: Readonly<{
  items: ReadonlyArray<{ label: string; tone: SeriesTone }>;
}>) {
  return (
    <span className="inline-list">
      {props.items.map((item) => (
        <span key={item.label} className="inline-list" style={{ gap: 6 }}>
          <span
            aria-hidden="true"
            style={{
              width: 10,
              height: 10,
              borderRadius: 2,
              background: PALETTE[item.tone],
              display: "inline-block",
            }}
          />
          <span className="muted">{item.label}</span>
        </span>
      ))}
    </span>
  );
}

/**
 * Столбчатая диаграмма: сравнение значений по категориям.
 *
 * Ось значений всегда начинается с нуля. Усечённая ось преувеличивает разницу
 * между столбцами — на сравнении моделей это прямо вводит в заблуждение.
 */
export function BarChart(props: Readonly<{
  title?: string;
  points: ReadonlyArray<ChartPoint>;
  tone?: SeriesTone;
  /** Подпись значения: проценты и миллисекунды выглядят по-разному. */
  format?: (value: number) => string;
}>) {
  const { points, tone = "accent", format } = props;
  const known = points.filter((point) => point.value !== null);
  if (known.length === 0) {
    return (
      <p className="muted" style={{ margin: 0 }}>
        Нет данных для диаграммы.
      </p>
    );
  }

  const max = Math.max(...known.map((point) => point.value as number), 0);
  const scale = max > 0 ? max : 1;

  return (
    <ChartFrame title={props.title}>
      <div
        role="img"
        aria-label={props.title}
        style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}
      >
        {points.map((point) => {
          const value = point.value;
          const width = value === null ? 0 : Math.max((value / scale) * 100, 1);
          return (
            <div key={point.label} style={{ display: "grid", gap: 4 }}>
              <div className="field-row" style={{ justifyContent: "space-between" }}>
                <span>{point.label}</span>
                <span className={value === null ? "muted" : undefined}>
                  {value === null
                    ? "нет данных"
                    : format
                      ? format(value)
                      : value.toLocaleString("ru-RU")}
                </span>
              </div>
              <div
                style={{
                  height: 8,
                  borderRadius: "var(--radius-pill)",
                  background: "var(--neutral-soft)",
                  overflow: "hidden",
                }}
              >
                {value !== null && (
                  <div
                    style={{
                      width: `${width}%`,
                      height: "100%",
                      background: PALETTE[tone],
                    }}
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>
      <p className="field-hint" style={{ marginTop: "var(--s-2)" }}>
        Масштаб от нуля до {format ? format(max) : max.toLocaleString("ru-RU")}.
      </p>
    </ChartFrame>
  );
}

export interface LineSeries {
  label: string;
  tone: SeriesTone;
  points: ReadonlyArray<ChartPoint>;
}

/**
 * Линейный график по времени: несколько серий на одной шкале.
 *
 * Разрыв в данных остаётся разрывом: точки с null не соединяются линией, иначе
 * график утверждал бы, что между ними шло плавное изменение.
 */
export function LineChart(props: Readonly<{
  title?: string;
  series: ReadonlyArray<LineSeries>;
  height?: number;
  format?: (value: number) => string;
}>) {
  const { series, height = 200, format } = props;
  const labels = series[0]?.points.map((point) => point.label) ?? [];
  const values = series
    .flatMap((line) => line.points.map((point) => point.value))
    .filter((value): value is number => value !== null);

  if (values.length === 0) {
    return (
      <p className="muted" style={{ margin: 0 }}>
        Нет данных для графика.
      </p>
    );
  }

  const max = Math.max(...values, 0);
  const scale = max > 0 ? max : 1;
  const width = 100;
  const step = labels.length > 1 ? width / (labels.length - 1) : 0;

  // Верхний и нижний край: точка ровно на границе viewBox обрезалась бы
  // наполовину, и максимум выглядел бы иначе, чем он есть.
  const toY = (value: number) => 4 + (1 - value / scale) * 92;

  return (
    <ChartFrame
      title={props.title}
      legend={
        <ChartLegend
          items={series.map((line) => ({ label: line.label, tone: line.tone }))}
        />
      }
    >
      <svg
        role="img"
        aria-label={props.title}
        viewBox={`0 0 ${width} 100`}
        preserveAspectRatio="none"
        style={{ width: "100%", height, display: "block" }}
      >
        {[0, 25, 50, 75, 100].map((line) => (
          <line
            key={line}
            x1={0}
            x2={width}
            y1={line}
            y2={line}
            stroke="var(--border)"
            strokeWidth={0.4}
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {series.map((line) => {
          // Разрывы: каждая непрерывная часть рисуется отдельной polyline.
          const segments: string[][] = [];
          let current: string[] = [];
          line.points.forEach((point, index) => {
            if (point.value === null) {
              if (current.length) segments.push(current);
              current = [];
              return;
            }
            current.push(`${index * step},${toY(point.value)}`);
          });
          if (current.length) segments.push(current);

          return (
            <g key={line.label}>
              {segments.map((segment, index) => (
                <polyline
                  key={`${line.label}-line-${index}`}
                  points={segment.join(" ")}
                  fill="none"
                  stroke={PALETTE[line.tone]}
                  strokeWidth={1.5}
                  vectorEffect="non-scaling-stroke"
                />
              ))}
              {/* Точки обязательны, а не декоративны: при одном интервале
                  ломаная не имеет длины и линия не отображается вовсе —
                  график выглядел бы пустым при наличии данных.

                  Рисуются нулевой длины линией с круглым концом, а не
                  <circle>: viewBox растянут по ширине (preserveAspectRatio
                  none), и круг в такой системе координат превращается в
                  эллипс. Толщина обводки не масштабируется, поэтому точка
                  остаётся круглой при любой ширине блока. */}
              {line.points.map((point, index) =>
                point.value === null ? null : (
                  <line
                    key={`${line.label}-dot-${index}`}
                    x1={labels.length > 1 ? index * step : width / 2}
                    x2={labels.length > 1 ? index * step : width / 2}
                    y1={toY(point.value)}
                    y2={toY(point.value)}
                    stroke={PALETTE[line.tone]}
                    strokeWidth={5}
                    strokeLinecap="round"
                    vectorEffect="non-scaling-stroke"
                  />
                ),
              )}
            </g>
          );
        })}
      </svg>
      <div
        className="field-row"
        style={{ justifyContent: "space-between", marginTop: "var(--s-2)" }}
      >
        <span className="field-hint">{labels[0]}</span>
        <span className="field-hint">
          максимум {format ? format(max) : max.toLocaleString("ru-RU")}
        </span>
        <span className="field-hint">{labels[labels.length - 1]}</span>
      </div>
    </ChartFrame>
  );
}
