"use client";

// Панель фильтров аналитики: общая для сводки и списка генераций.
//
// Значения фильтров берутся из истории (`/analytics/filters`), а не из текущей
// конфигурации ИИ: удалённая модель остаётся в прошлых генерациях, и без неё их
// нельзя было бы найти. Предлагать модель, которой в данных нет, тоже нельзя —
// пустой результат выглядел бы как поломка.

import {
  AnalyticsFilter,
  AnalyticsFilterOptions,
  GeneratorKind,
  GenerationResult,
  ValidationState,
} from "@/lib/api";
import { Field } from "@/components/ui/Primitives";
import {
  GENERATION_STATUS_LABELS,
  GENERATION_TRIGGER_LABELS,
  GENERATOR_LABELS,
  VALIDATION_STATE_LABELS,
} from "@/lib/labels";

/** Готовые периоды: вручную вводить даты для «за неделю» не требуется. */
export const PERIODS = [
  { value: "24h", label: "За сутки", hours: 24 },
  { value: "7d", label: "За 7 дней", hours: 24 * 7 },
  { value: "30d", label: "За 30 дней", hours: 24 * 30 },
  { value: "all", label: "За всё время", hours: null },
] as const;

export type PeriodValue = (typeof PERIODS)[number]["value"];

/** Начало периода в ISO. `null` — без ограничения по времени. */
export function periodStart(period: PeriodValue): string | undefined {
  const found = PERIODS.find((item) => item.value === period);
  if (!found || found.hours === null) return undefined;
  return new Date(Date.now() - found.hours * 3600_000).toISOString();
}

export function AnalyticsFilters(props: Readonly<{
  period: PeriodValue;
  onPeriodChange: (period: PeriodValue) => void;
  filter: AnalyticsFilter;
  onFilterChange: (filter: AnalyticsFilter) => void;
  options: AnalyticsFilterOptions | null;
  /** Скрывает фильтр по версии инструкции там, где он лишён смысла. */
  withPromptVersion?: boolean;
}>) {
  const { filter, onFilterChange, options } = props;
  const set = (patch: Partial<AnalyticsFilter>) =>
    onFilterChange({ ...filter, ...patch });

  const activeCount = Object.values(filter).filter(
    (value) => value !== undefined && value !== "",
  ).length;

  return (
    <div className="filters">
      <Field
        label="Период"
        hint="По времени создания операции генерации."
        htmlFor="an-period"
      >
        <select
          id="an-period"
          value={props.period}
          onChange={(event) =>
            props.onPeriodChange(event.target.value as PeriodValue)
          }
        >
          {PERIODS.map((period) => (
            <option key={period.value} value={period.value}>
              {period.label}
            </option>
          ))}
        </select>
      </Field>

      <Field
        label="Сервис ИИ"
        hint="Только те, что встречались в генерациях."
        htmlFor="an-provider"
      >
        <select
          id="an-provider"
          value={filter.provider ?? ""}
          onChange={(event) =>
            set({ provider: event.target.value || undefined })
          }
        >
          <option value="">Любой</option>
          {(options?.providers ?? []).map((provider) => (
            <option key={provider} value={provider}>
              {provider}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Модель" htmlFor="an-model">
        <select
          id="an-model"
          value={filter.model ?? ""}
          onChange={(event) => set({ model: event.target.value || undefined })}
        >
          <option value="">Любая</option>
          {(options?.models ?? []).map((item) => (
            <option key={item.model} value={item.model}>
              {item.model}
            </option>
          ))}
        </select>
      </Field>

      {props.withPromptVersion && (
        <Field
          label="Версия инструкции"
          hint="Формулировка, с которой шла генерация."
          htmlFor="an-prompt"
        >
          <select
            id="an-prompt"
            value={filter.prompt_version ?? ""}
            onChange={(event) =>
              set({
                prompt_version: event.target.value
                  ? Number(event.target.value)
                  : undefined,
              })
            }
          >
            <option value="">Любая</option>
            {(options?.prompt_versions ?? []).map((version) => (
              <option key={version} value={version}>
                Версия {version}
              </option>
            ))}
          </select>
        </Field>
      )}

      <Field
        label="Кто собрал программу"
        hint="Фактический генератор, а не запрошенный."
        htmlFor="an-generator"
      >
        <select
          id="an-generator"
          value={filter.generator ?? ""}
          onChange={(event) =>
            set({
              generator: (event.target.value || undefined) as
                | GeneratorKind
                | undefined,
            })
          }
        >
          <option value="">Любой</option>
          {Object.entries(GENERATOR_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Итог" htmlFor="an-result">
        <select
          id="an-result"
          value={filter.result ?? ""}
          onChange={(event) =>
            set({
              result: (event.target.value || undefined) as
                | GenerationResult
                | undefined,
            })
          }
        >
          <option value="">Любой</option>
          {Object.entries(GENERATION_STATUS_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </Field>

      <Field
        label="Проверка результата"
        hint="Прошёл ли ответ проверку и запрашивалось ли исправление."
        htmlFor="an-validation"
      >
        <select
          id="an-validation"
          value={filter.validation ?? ""}
          onChange={(event) =>
            set({
              validation: (event.target.value || undefined) as
                | ValidationState
                | undefined,
            })
          }
        >
          <option value="">Любая</option>
          {Object.entries(VALIDATION_STATE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </Field>

      <Field
        label="Сборка без ИИ"
        hint="Программу собрал алгоритм вместо запрошенного ИИ."
        htmlFor="an-fallback"
      >
        <select
          id="an-fallback"
          value={filter.fallback === undefined ? "" : String(filter.fallback)}
          onChange={(event) =>
            set({
              fallback:
                event.target.value === ""
                  ? undefined
                  : event.target.value === "true",
            })
          }
        >
          <option value="">Не важно</option>
          <option value="true">Только с подменой генератора</option>
          <option value="false">Без подмены</option>
        </select>
      </Field>

      <Field label="Причина запуска" htmlFor="an-trigger">
        <select
          id="an-trigger"
          value={filter.trigger ?? ""}
          onChange={(event) => set({ trigger: event.target.value || undefined })}
        >
          <option value="">Любая</option>
          {(options?.triggers ?? []).map((trigger) => (
            <option key={trigger} value={trigger}>
              {GENERATION_TRIGGER_LABELS[trigger] ?? trigger}
            </option>
          ))}
        </select>
      </Field>

      <div className="filters-actions">
        {activeCount > 0 && (
          <button type="button" className="ghost" onClick={() => onFilterChange({})}>
            Сбросить фильтры
          </button>
        )}
      </div>
    </div>
  );
}
