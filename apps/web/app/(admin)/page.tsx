"use client";

// Сводка.
//
// Раньше здесь было пять чисел о продукте: сколько людей, анкет, упражнений и
// программ. На вопрос «всё ли работает» такая страница не отвечала, и чтобы это
// выяснить, приходилось открывать четыре раздела подряд.
//
// Теперь сводка отвечает на три вопроса в порядке важности:
//   1. Что сломано прямо сейчас (несовместимый компонент, ИИ не настроен,
//      обновление сервера заблокировано).
//   2. Что происходит с генерацией за последнюю неделю.
//   3. Сколько всего накоплено данных.
//
// Числа берутся только из существующих endpoint'ов, новых на backend не
// добавлено. Опроса по таймеру нет: сводка делает пять запросов, и держать их в
// цикле означало бы платить за фоновую вкладку. Обновление — по кнопке.
//
// Отсутствующее число показывается как «—» с пояснением, а не нулём: ноль
// означал бы измеренный результат, которого не было.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
import { Metric } from "@/components/ui/Metric";
import {
  Card,
  ErrorState,
  Notice,
  Skeleton,
  Status,
} from "@/components/ui/Primitives";
import {
  AIReadinessReport,
  AnalyticsOverview,
  ComponentsResponse,
  Dashboard,
  DeploymentSafetyReport,
  aiApi,
  api,
  componentsApi,
} from "@/lib/api";
import {
  componentStateLabel,
  count,
  dateTime,
  duration,
  percent,
} from "@/lib/labels";

/** Состояния компонента, требующие внимания администратора. */
const PROBLEM_STATES = ["update_required", "incompatible", "offline"];

/** Окно, за которое считается состояние генерации. */
const GENERATION_WINDOW_DAYS = 7;

function windowStart(): string {
  const from = new Date();
  from.setDate(from.getDate() - GENERATION_WINDOW_DAYS);
  return from.toISOString();
}

interface Snapshot {
  dashboard: Dashboard;
  readiness: AIReadinessReport | null;
  components: ComponentsResponse | null;
  safety: DeploymentSafetyReport | null;
  generations: AnalyticsOverview | null;
}

export default function DashboardPage() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // Запросы параллельны и независимы: недоступность одного не должна
      // прятать остальные. Продуктовые числа обязательны — без них страницы
      // нет; остальные блоки при отказе показываются как «нет данных».
      const [dashboard, readiness, components, safety, generations] =
        await Promise.all([
          api.dashboard(),
          aiApi.readiness().catch(() => null),
          componentsApi.list().catch(() => null),
          componentsApi.deploymentSafety().catch(() => null),
          aiApi
            .analyticsOverview({ date_from: windowStart() })
            .catch(() => null),
        ]);
      setData({ dashboard, readiness, components, safety, generations });
      setRefreshedAt(new Date().toISOString());
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const problems =
    data?.components?.items.filter((item) =>
      PROBLEM_STATES.includes(item.compatibility_state),
    ) ?? [];
  const blockingChecks =
    data?.readiness?.checks.filter(
      (check) => check.blocking && check.status !== "ok",
    ) ?? [];

  return (
    <>
      <PageHeader
        title="Сводка"
        description="Состояние системы и накопленные данные. Сначала то, что требует внимания, затем итоги генерации за неделю и объём базы."
        actions={
          <>
            {refreshedAt && (
              <span className="muted">обновлено {dateTime(refreshedAt)}</span>
            )}
            <button
              type="button"
              className="small"
              onClick={() => load().catch(() => undefined)}
              disabled={loading}
            >
              {loading ? "Обновляем…" : "Обновить"}
            </button>
          </>
        }
      />

      {error && (
        <ErrorState
          message={error}
          onRetry={() => load().catch(() => undefined)}
        />
      )}

      {!data && !error && (
        <Card>
          <Skeleton rows={5} />
        </Card>
      )}

      {data && (
        <>
          <SystemState
            problems={problems}
            blockingChecks={blockingChecks}
            safety={data.safety}
            readiness={data.readiness}
            componentsKnown={data.components !== null}
          />

          <Card
            title={`Генерация программ за ${GENERATION_WINDOW_DAYS} дней`}
            description="Единица счёта — операция генерации, а не программа: у неудачной операции программы нет, и по программам отказы не видны."
            actions={
              <Link className="btn small" href="/ai/generations">
                Все операции
              </Link>
            }
          >
            {data.generations ? (
              <GenerationTotals overview={data.generations} />
            ) : (
              <Notice tone="warn" title="Числа генерации недоступны">
                Сводка аналитики не ответила. Это не означает, что генерации не
                было: данных для показа нет.
              </Notice>
            )}
          </Card>

          <Card
            title="Данные продукта"
            description="Накопленный объём. Числа считаются из базы при каждом открытии страницы."
          >
            <ProductTotals dashboard={data.dashboard} />
          </Card>
        </>
      )}
    </>
  );
}

/**
 * Блок «требует внимания».
 *
 * Стоит первым и показывает только отклонения: когда всё в порядке, здесь одна
 * строка. Перечислять исправное состояние подробно значило бы утопить в нём
 * единственную проблему.
 */
function SystemState(props: Readonly<{
  problems: ComponentsResponse["items"];
  blockingChecks: NonNullable<AIReadinessReport["checks"]>;
  safety: DeploymentSafetyReport | null;
  readiness: AIReadinessReport | null;
  componentsKnown: boolean;
}>) {
  const { problems, blockingChecks, safety, readiness } = props;
  const blocked = safety?.result === "BLOCKED";
  const calm =
    problems.length === 0 && blockingChecks.length === 0 && !blocked;

  return (
    <Card
      title="Состояние системы"
      description="Части системы разворачиваются независимо, поэтому совместимость определяется версией контракта, а не совпадением номеров версий."
      actions={
        <>
          <Link className="btn small" href="/infrastructure">
            Инфраструктура
          </Link>
          <Link className="btn small" href="/ai">
            ИИ
          </Link>
        </>
      }
    >
      {calm && (
        <Notice tone="ok" title="Отклонений нет">
          {props.componentsKnown
            ? "Все компоненты, сообщившие о себе, совместимы с сервером; обязательные шаги настройки ИИ выполнены."
            : "Реестр компонентов не ответил, поэтому их состояние неизвестно. Обязательные шаги настройки ИИ выполнены."}
        </Notice>
      )}

      {!props.componentsKnown && (
        <Notice tone="warn" title="Состояние компонентов неизвестно">
          Реестр не ответил. Это не то же самое, что «все компоненты работают».
        </Notice>
      )}

      {blocked && safety && (
        <Notice tone="bad" title="Обновление сервера заблокировано">
          {safety.blocking
            .map((verdict) => `${verdict.component_id}: ${verdict.detail}`)
            .join("; ")}
        </Notice>
      )}

      {problems.map((item) => (
        <Notice
          key={item.component_id}
          tone={item.compatibility_state === "offline" ? "warn" : "bad"}
          title={`${item.name}: ${componentStateLabel(item.compatibility_state)}`}
        >
          {item.compatibility_detail}
        </Notice>
      ))}

      {blockingChecks.length > 0 && (
        <Notice
          tone="warn"
          title="ИИ не заработает: не выполнены обязательные шаги"
        >
          {blockingChecks.map((check) => (
            <div key={check.key}>
              <strong>{check.title}</strong> — {check.detail}
              {check.action && ` Что сделать: ${check.action}`}
            </div>
          ))}
        </Notice>
      )}

      {readiness && (
        <div className="inline-list" style={{ marginTop: "var(--s-3)" }}>
          <Status tone={readiness.ready ? "ok" : "warn"}>
            {readiness.ready ? "ИИ готов к работе" : "ИИ пока не заработает"}
          </Status>
          {props.componentsKnown && (
            <Status tone={problems.length === 0 ? "ok" : "bad"}>
              {problems.length === 0
                ? "компоненты совместимы"
                : `компонентов с отклонением: ${problems.length}`}
            </Status>
          )}
          {safety && (
            <Status tone={blocked ? "bad" : "ok"}>
              {blocked
                ? "обновление сервера заблокировано"
                : "обновление сервера безопасно"}
            </Status>
          )}
        </div>
      )}
    </Card>
  );
}

function GenerationTotals(props: Readonly<{ overview: AnalyticsOverview }>) {
  const { generations, sample } = props.overview;
  const nothing = generations.total === 0;

  return (
    <>
      {nothing ? (
        <Notice tone="info" title="Генераций за период не было">
          Проценты не показаны: считать не на чем. Ноль означал бы «пробовали и
          не вышло».
        </Notice>
      ) : (
        !sample.confident && (
          <Notice tone="warn" title="Данных мало: проценты ненадёжны">
            В выборку попало операций: {count(sample.generations)}. Доли начинают
            что-то означать примерно с {sample.min_confident}.
          </Notice>
        )
      )}

      <div className="stats-grid">
        <Metric
          label="Операций генерации"
          value={count(generations.total)}
          secondary={`успешно ${count(generations.succeeded)} · с отказом ${count(generations.failed)}`}
          hint="Каждая попытка собрать программу, включая неудачные."
        />
        <Metric
          label="Успешность"
          value={nothing ? "—" : percent(generations.success_rate)}
          hint={
            nothing
              ? "Считать не на чем: операций за период не было."
              : "Доля операций, завершившихся программой."
          }
          tone={
            nothing || generations.success_rate === null
              ? "neutral"
              : generations.success_rate >= 0.9
                ? "ok"
                : generations.success_rate >= 0.6
                  ? "warn"
                  : "bad"
          }
        />
        <Metric
          label="Собрано алгоритмом вместо ИИ"
          value={count(generations.deterministic_fallback)}
          secondary={nothing ? undefined : `доля ${percent(generations.fallback_rate)}`}
          hint="Программа получена, но ИИ не справился. Пользователь получил план в любом случае."
          tone={generations.deterministic_fallback > 0 ? "warn" : "neutral"}
        />
        <Metric
          label="В работе"
          value={count(generations.active)}
          hint="Операции, которые ещё выполняются или ждут очереди."
          tone={generations.active > 0 ? "info" : "neutral"}
        />
        <Metric
          label="Ответ ИИ отвергнут проверкой"
          value={count(generations.validation_failures)}
          secondary={`исправлено и принято: ${count(generations.repaired)}`}
          hint="Ответ не прошёл проверку структуры. Отдельно от отказов провайдера."
          tone={generations.validation_failures > 0 ? "warn" : "neutral"}
        />
        <Metric
          label="Длительность генерации"
          value={duration(generations.avg_duration_ms)}
          secondary={`95-й процентиль ${duration(generations.p95_duration_ms)}`}
          hint="Среднее время операции. Процентиль показывает, сколько ждут самые медленные."
        />
      </div>
    </>
  );
}

function ProductTotals(props: Readonly<{ dashboard: Dashboard }>) {
  const { dashboard } = props;
  return (
    <div className="stats-grid">
      <Metric
        label="Пользователи бота"
        value={count(dashboard.users_total)}
        hint="Люди, которые хотя бы раз открыли бот в Telegram."
      />
      <Metric
        label="Анкеты"
        value={count(dashboard.profiles_total)}
        secondary={`за сегодня: ${count(dashboard.profiles_today)}`}
        hint="Заполненные анкеты с целями, опытом и ограничениями."
        action={
          <Link className="btn small" href="/profiles">
            Открыть
          </Link>
        }
      />
      <Metric
        label="Программы тренировок"
        value={dashboard.programs_total === null ? "—" : count(dashboard.programs_total)}
        hint={
          dashboard.programs_total === null
            ? "Сервер не сообщил число: показывать ноль было бы неверно."
            : "Созданные программы всех версий."
        }
        action={
          <Link className="btn small" href="/programs">
            Открыть
          </Link>
        }
      />
      <Metric
        label="Упражнения в каталоге"
        value={count(dashboard.exercises_total)}
        hint="Используемые упражнения, из которых собираются программы."
        action={
          <Link className="btn small" href="/exercises">
            Открыть
          </Link>
        }
      />
    </div>
  );
}
