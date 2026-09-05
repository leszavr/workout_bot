"use client";

// База знаний: полнота и целостность.
//
// Все числа приходят из базы, а не зашиты в код: показатель, посчитанный один
// раз при написании интерфейса, показывал бы состояние на тот момент, а не
// текущее.
//
// «Оборудование неизвестно» — не ошибка и не ноль. Это упражнения, для которых
// требования не заполнены; система отвечает по ним «неизвестно», а не
// «не подходит», и превращать пробел в отказ нельзя. Показатель нужен, чтобы
// пробел был виден и закрывался осознанно.

import Link from "next/link";
import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import KnowledgeTabs from "@/components/knowledge/KnowledgeTabs";
import { Metric } from "@/components/ui/Metric";
import { Card, Notice, Skeleton, Status } from "@/components/ui/Primitives";
import { KnowledgeHealth, getToken, knowledgeApi } from "@/lib/api";
import { UNMAPPED_REASON_LABELS, count, percent } from "@/lib/labels";

export default function KnowledgeHealthPage() {
  const [health, setHealth] = useState<KnowledgeHealth | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    knowledgeApi
      .health()
      .then(setHealth)
      .catch((e) => setError((e as Error).message));
  }, []);

  const shell = (children: React.ReactNode) => (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <div className="page-head">
          <h1 className="page-title">Полнота базы знаний</h1>
          <p className="page-subtitle">
            Насколько подробно описано оборудование упражнений. Числа считаются
            из базы при каждом открытии страницы.
          </p>
        </div>
        <KnowledgeTabs />
        {children}
      </main>
    </div>
  );

  if (error) {
    return shell(<div className="error">{error}</div>);
  }

  if (!health) {
    return shell(
      <Card>
        <Skeleton rows={5} />
      </Card>,
    );
  }

  const integrityProblems =
    health.orphan_equipment_references +
    health.invalid_capability_references +
    health.impossible_requirement_combinations;

  return shell(
    <>
      {integrityProblems > 0 ? (
        <Notice tone="bad" title="Найдены нарушения целостности">
          Ссылки в никуда и невозможные комбинации требований делают результат
          проверки совместимости непредсказуемым. Разберите их до опоры на
          автоматический подбор.
        </Notice>
      ) : (
        <Notice tone="ok" title="Нарушений целостности нет">
          Все требования ссылаются на существующие упражнения и записи словаря,
          невозможных комбинаций не найдено.
        </Notice>
      )}

      <div className="stats-grid">
        <Metric
          label="Оборудование известно"
          value={count(health.equipment_known)}
          secondary={percent(health.equipment_known_ratio * 100)}
          hint={`Упражнений, для которых заполнены требования. Всего в каталоге ${count(health.exercises_total)}.`}
          tone={health.equipment_known_ratio >= 0.9 ? "ok" : "warn"}
        />
        <Metric
          label="Оборудование неизвестно"
          value={count(health.equipment_unknown)}
          hint="По этим упражнениям проверка отвечает «неизвестно», а не «не подходит»: отсутствие данных не является доказательством несовместимости."
          tone={health.equipment_unknown > 0 ? "warn" : "ok"}
        />
        <Metric
          label="Подтверждено"
          value={count(health.equipment_confirmed)}
          hint="Требования взяты из данных источника каталога, а не выведены правилом."
        />
        <Metric
          label="Выведено правилом"
          value={count(health.equipment_inferred)}
          hint="Требования получены сопоставлением названия или типа нагрузки. Требуют проверки человеком."
          tone={health.equipment_inferred > 0 ? "warn" : "neutral"}
        />
        <Metric
          label="С альтернативами"
          value={count(health.exercises_with_alternatives)}
          secondary={
            health.exercises_total > 0
              ? percent(
                  (health.exercises_with_alternatives /
                    health.exercises_total) *
                    100,
                )
              : undefined
          }
          hint="Упражнений, для которых известна хотя бы одна замена."
        />
        <Metric
          label="Незакрытых значений"
          value={count(health.unmapped_values)}
          secondary={`упражнений: ${count(health.unmapped_exercises)}`}
          hint="Значения оборудования источника, которым не нашлось записи словаря. Сохранены, а не отброшены."
          tone={health.unmapped_values > 0 ? "warn" : "ok"}
          action={
            health.unmapped_values > 0 ? (
              <Link className="linklike" href="/knowledge/unmapped">
                Разобрать
              </Link>
            ) : undefined
          }
        />
      </div>

      <Card
        title="Словарь"
        description="Записи, возможности и синонимы, из которых складывается знание об оборудовании."
      >
        <div className="kv">
          <div className="k">Записей оборудования</div>
          <div>
            {count(health.equipment_items_total)} (используется{" "}
            {count(health.equipment_items_active)})
          </div>
          <div className="k">Не связано с упражнениями</div>
          <div>
            {health.equipment_items_unused > 0 ? (
              <Status tone="warn">
                {count(health.equipment_items_unused)}
              </Status>
            ) : (
              <Status tone="ok">0</Status>
            )}
          </div>
          <div className="k">Возможностей</div>
          <div>{count(health.capabilities_total)}</div>
          <div className="k">Синонимов</div>
          <div>{count(health.aliases_total)}</div>
          <div className="k">Требований</div>
          <div>{count(health.requirements_total)}</div>
          <div className="k">Связей «альтернатива»</div>
          <div>{count(health.alternatives_total)}</div>
        </div>
      </Card>

      <Card
        title="Целостность"
        description="Проверки, нарушение которых делает результат подбора неверным, а не просто неполным."
      >
        <div className="kv">
          <div className="k">Ссылки в никуда</div>
          <div>
            <Status
              tone={health.orphan_equipment_references > 0 ? "bad" : "ok"}
            >
              {count(health.orphan_equipment_references)}
            </Status>
            <div className="field-hint">
              Требования и альтернативы, указывающие на упражнение, которого нет
              в каталоге.
            </div>
          </div>
          <div className="k">Неизвестные возможности</div>
          <div>
            <Status
              tone={health.invalid_capability_references > 0 ? "bad" : "ok"}
            >
              {count(health.invalid_capability_references)}
            </Status>
            <div className="field-hint">
              Ссылки на возможность, отсутствующую в словаре.
            </div>
          </div>
          <div className="k">Невозможные комбинации</div>
          <div>
            <Status
              tone={
                health.impossible_requirement_combinations > 0 ? "bad" : "ok"
              }
            >
              {count(health.impossible_requirement_combinations)}
            </Status>
            <div className="field-hint">
              Упражнение требует одновременно снаряд и его отсутствие
              (собственный вес).
            </div>
          </div>
        </div>
      </Card>

      {health.unmapped_summary.length > 0 && (
        <Card
          title="Незакрытые значения источника"
          description="Сгруппированы по строке, как она записана в каталоге."
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Значение</th>
                  <th>Почему</th>
                  <th>Упражнений</th>
                </tr>
              </thead>
              <tbody>
                {health.unmapped_summary.map((row) => (
                  <tr key={`${row.raw_value}-${row.reason}`}>
                    <td>
                      <code>{row.raw_value}</code>
                    </td>
                    <td>
                      {UNMAPPED_REASON_LABELS[row.reason] ?? row.reason}
                    </td>
                    <td>{count(row.count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>,
  );
}
