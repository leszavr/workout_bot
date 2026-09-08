"use client";

// Карточка упражнения.
//
// Раньше это была вертикальная лента из восьми карточек: характеристики,
// требования, замены, описание, техника, изображения, происхождение,
// ограничения. Сопоставить требования с характеристиками, не прокручивая экран
// туда и обратно, было нельзя, а происхождение — самый редко нужный блок —
// занимало середину страницы.
//
// Информация разложена по вкладкам с числами: сколько требований, сколько
// замен, сколько изображений видно до нажатия, поэтому пустая вкладка не
// открывается напрасно.
//
// Оборудование показано дважды и это не дублирование: «в справочнике» —
// значение источника каталога, «требования» — нормализованное знание системы.
// Подбор с учётом оборудования опирается на второе, и расхождение между ними
// должно быть видно, а не спрятано.
//
// Пустые требования показываются явным сообщением, а не пустым списком: по
// такому упражнению система отвечает «неизвестно», а не «оборудование не нужно»,
// и это разные утверждения.

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
import { DataColumn, DataTable } from "@/components/ui/DataTable";
import {
  Card,
  Empty,
  ErrorState,
  KeyValue,
  Notice,
  Skeleton,
  Status,
  Tag,
} from "@/components/ui/Primitives";
import { LocalTabs } from "@/components/ui/Tabs";
import {
  API_BASE,
  EquipmentCapability,
  EquipmentItem,
  ExerciseAlternative,
  ExerciseDetail,
  ExerciseProvenance,
  ExerciseRequirement,
  api,
  ingestionApi,
  knowledgeApi,
} from "@/lib/api";
import {
  groupRequirements,
  requirementName,
} from "@/lib/requirements";
import {
  CONFIDENCE_LABELS,
  DIFFICULTY_LABELS,
  EXERCISE_TYPE_LABELS,
  FORCE_LABELS,
  KNOWLEDGE_SOURCE_LABELS,
  MECHANIC_LABELS,
  PROVENANCE_FIELD_LABELS,
  REQUIREMENT_LABELS,
  SOURCE_RELATION_LABELS,
  SUBSTITUTION_LABELS,
  equipmentList,
  ingestionReasonLabel,
  muscleList,
  substitutionTone,
} from "@/lib/labels";

const VOCABULARY_LIMIT = 200;

function text(value: string) {
  return value === "—" ? <span className="muted">—</span> : value;
}

function list(items: readonly string[]) {
  return items.length ? items.join(", ") : <span className="muted">—</span>;
}

export default function ExerciseDetailPage() {
  const params = useParams<{ id: string }>();
  const [exercise, setExercise] = useState<ExerciseDetail | null>(null);
  const [requirements, setRequirements] = useState<ExerciseRequirement[] | null>(
    null,
  );
  const [alternatives, setAlternatives] = useState<ExerciseAlternative[]>([]);
  const [equipment, setEquipment] = useState<Record<string, EquipmentItem>>({});
  const [capabilities, setCapabilities] = useState<
    Record<string, EquipmentCapability>
  >({});
  // Происхождение читается отдельным запросом: карточка остаётся читаемой, даже
  // если импорт внешних источников ещё не выполнялся.
  const [provenance, setProvenance] = useState<ExerciseProvenance | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("main");

  const load = useCallback(() => {
    api
      .exercise(Number(params.id))
      .then((response) => {
        setExercise(response);
        setError("");
      })
      .catch((e) => setError((e as Error).message));
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!exercise) return;
    // Знание об оборудовании читается отдельным запросом: карточка упражнения
    // остаётся читаемой, даже если база знаний для него ещё не заполнена.
    knowledgeApi
      .requirements(exercise.external_id, exercise.source)
      .then((response) => setRequirements(response.items))
      .catch(() => setRequirements([]));
    knowledgeApi
      .alternatives(exercise.external_id, exercise.source)
      .then((response) => setAlternatives(response.items))
      .catch(() => undefined);
    ingestionApi
      .provenance(exercise.external_id, exercise.source)
      .then(setProvenance)
      .catch(() =>
        setProvenance({
          exercise_external_id: exercise.external_id,
          exercise_source: exercise.source,
          fields: [],
          sources: [],
          program_observations: [],
        }),
      );
  }, [exercise]);

  useEffect(() => {
    knowledgeApi
      .equipment({ limit: VOCABULARY_LIMIT, is_active: "all", usage: "all" })
      .then((response) => {
        const map: Record<string, EquipmentItem> = {};
        for (const item of response.items) map[item.equipment_id] = item;
        setEquipment(map);
      })
      .catch(() => undefined);
    knowledgeApi
      .capabilities()
      .then((response) => {
        const map: Record<string, EquipmentCapability> = {};
        for (const item of response.items) map[item.capability_id] = item;
        setCapabilities(map);
      })
      .catch(() => undefined);
  }, []);

  if (error) {
    return (
      <>
        <ErrorState message={error} onRetry={load} />
        <Link className="btn" href="/exercises">
          К каталогу упражнений
        </Link>
      </>
    );
  }

  if (!exercise) {
    return (
      <Card>
        <Skeleton rows={5} />
      </Card>
    );
  }

  const equipmentName = (id: string) => equipment[id]?.name_ru ?? id;
  const capabilityName = (id: string) => capabilities[id]?.name_ru ?? id;

  const technique = exercise.technique_ru || exercise.technique;
  const photos = exercise.media ?? [];
  const restrictions =
    exercise.contraindications.length > 0 || exercise.limitations.length > 0;
  const provenanceCount = provenance
    ? provenance.sources.length +
      provenance.fields.length +
      provenance.program_observations.length
    : undefined;

  return (
    <>
      <PageHeader
        title={exercise.name_ru || exercise.name}
        description={exercise.name_ru ? exercise.name : undefined}
        actions={
          <>
            <Status tone={exercise.is_active ? "ok" : "neutral"}>
              {exercise.is_active
                ? "используется в программах"
                : "в новые программы не попадает"}
            </Status>
            <Link className="btn small" href="/exercises">
              К каталогу
            </Link>
          </>
        }
        tabs={
          <LocalTabs
            label="Разделы упражнения"
            tabs={[
              { id: "main", label: "Характеристики" },
              {
                id: "equipment",
                label: "Оборудование",
                count: requirements?.length,
              },
              {
                id: "alternatives",
                label: "Замены",
                count: alternatives.length,
              },
              { id: "technique", label: "Техника и фото", count: photos.length },
              { id: "origin", label: "Происхождение", count: provenanceCount },
            ]}
            active={tab}
            onChange={setTab}
          />
        }
      />

      {tab === "main" && (
        <>
          <Card
            title="Характеристики"
            description="По этим признакам упражнение подбирается в программу."
          >
            <KeyValue
              rows={[
                {
                  key: "Код упражнения",
                  value: <code>{exercise.external_id}</code>,
                  hint: `Источник: ${exercise.source}`,
                },
                {
                  key: "Оборудование в справочнике",
                  value: text(equipmentList(exercise.equipment)),
                  hint: "Значение источника каталога. Подбор опирается на требования во вкладке «Оборудование».",
                },
                {
                  key: "Основные мышцы",
                  value: text(muscleList(exercise.primary_muscles)),
                },
                {
                  key: "Дополнительные мышцы",
                  value: text(muscleList(exercise.secondary_muscles)),
                },
                {
                  key: "Вид нагрузки",
                  value: exercise.exercise_type
                    ? EXERCISE_TYPE_LABELS[exercise.exercise_type] ??
                      exercise.exercise_type
                    : "—",
                },
                {
                  key: "Сложность",
                  value: exercise.difficulty
                    ? DIFFICULTY_LABELS[exercise.difficulty] ??
                      exercise.difficulty
                    : "—",
                },
                {
                  key: "Характер усилия",
                  value: exercise.force
                    ? FORCE_LABELS[exercise.force] ?? exercise.force
                    : "—",
                },
                {
                  key: "Работа мышц",
                  value: exercise.mechanic
                    ? MECHANIC_LABELS[exercise.mechanic] ?? exercise.mechanic
                    : "—",
                },
              ]}
            />
          </Card>

          {exercise.description && (
            <Card title="Описание">
              <p style={{ margin: 0 }}>{exercise.description}</p>
            </Card>
          )}

          {restrictions && (
            <Card
              title="Кому не подходит"
              description="Учитывается при подборе, если человек указал ограничения в анкете."
            >
              <KeyValue
                rows={[
                  {
                    key: "Противопоказания",
                    value: list(exercise.contraindications),
                  },
                  { key: "Ограничения", value: list(exercise.limitations) },
                ]}
              />
            </Card>
          )}
        </>
      )}

      {tab === "equipment" && (
        <RequirementsSection
          requirements={requirements}
          equipmentName={equipmentName}
          capabilityName={capabilityName}
        />
      )}

      {tab === "alternatives" && (
        <Card
          title="Чем можно заменить"
          description="Тип замены различается: «полная замена» и «похожее движение» — разные утверждения, и одно не выдаётся за другое."
        >
          <DataTable
            columns={alternativeColumns(equipmentName)}
            rows={alternatives}
            rowKey={(item) => item.alternative_external_id}
            emptyTitle="Замен не найдено"
            emptyHint="Совпадений по основным мышцам и характеру движения нет либо знание ещё не пересчитано."
          />
        </Card>
      )}

      {tab === "technique" && (
        <>
          <Card title="Как выполнять">
            {technique ? (
              <p style={{ whiteSpace: "pre-line", margin: 0 }}>{technique}</p>
            ) : (
              <Empty
                title="Описание техники не заполнено"
                hint="В справочнике, из которого загружено упражнение, текста техники нет."
              />
            )}
          </Card>

          <Card title="Изображения">
            {photos.length > 0 ? (
              <>
                <div className="media-grid">
                  {photos.map((item) => (
                    <a
                      key={`${item.media_type}-${item.sequence}`}
                      href={`${API_BASE}${item.url}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={`${API_BASE}${item.url}`}
                        alt={`${exercise.name_ru || exercise.name} — ${
                          item.media_type === "animation" ? "анимация" : "фото"
                        } ${item.sequence}`}
                      />
                      <div className="muted" style={{ textAlign: "center" }}>
                        {item.media_type === "animation" ? "анимация" : "фото"}
                      </div>
                    </a>
                  ))}
                </div>
                <MediaLicense
                  photos={photos}
                  fallbackSource={exercise.source}
                />
              </>
            ) : (
              <Empty
                title="Изображений нет"
                hint="Упражнение загружено без фотографий и анимаций."
              />
            )}
          </Card>
        </>
      )}

      {tab === "origin" && (
        <ProvenanceSection provenance={provenance} />
      )}
    </>
  );
}

function alternativeColumns(
  equipmentName: (id: string) => string,
): ReadonlyArray<DataColumn<ExerciseAlternative>> {
  return [
    {
      key: "exercise",
      header: "Упражнение",
      render: (item) => (
        <Link
          href={`/exercises?search=${encodeURIComponent(item.alternative_external_id)}`}
        >
          {item.alternative_external_id}
        </Link>
      ),
    },
    {
      key: "substitution",
      header: "Тип замены",
      render: (item) => (
        <Status tone={substitutionTone(item.substitution)}>
          {SUBSTITUTION_LABELS[item.substitution] ?? item.substitution}
        </Status>
      ),
    },
    {
      key: "equipment",
      header: "Оборудование",
      render: (item) => {
        const values = Array.isArray(item.rationale.equipment)
          ? (item.rationale.equipment as string[])
          : [];
        return values.length > 0 ? (
          values.map(equipmentName).join(", ")
        ) : (
          <span className="muted">не заполнено</span>
        );
      },
    },
    {
      key: "score",
      header: "Совпадение",
      numeric: true,
      render: (item) => `${Math.round(item.score * 100)}%`,
    },
  ];
}

/**
 * Требования к оборудованию.
 *
 * Обязательные, желательные и «одно из вариантов» показаны раздельно, а не
 * общим списком со подписью в тексте: администратор решает по этой странице,
 * выполнимо ли упражнение, и «нужна штанга» и «можно со штангой» — разные
 * ответы. Группа «одно из» показывается вместе: три отдельные строки не
 * сообщают, что достаточно любого варианта.
 */
function RequirementsSection(props: Readonly<{
  requirements: ExerciseRequirement[] | null;
  equipmentName: (id: string) => string;
  capabilityName: (id: string) => string;
}>) {
  const { requirements } = props;

  if (requirements === null) {
    return (
      <Card title="Требования к оборудованию">
        <Skeleton rows={3} />
      </Card>
    );
  }

  if (requirements.length === 0) {
    return (
      <Card title="Требования к оборудованию">
        <Notice tone="warn" title="Требования не заполнены">
          Система отвечает по этому упражнению «неизвестно», а не «оборудование
          не нужно»: отсутствие данных не является доказательством ни того, ни
          другого. Пока требования не заполнены, подбор с учётом оборудования для
          него не работает.
        </Notice>
      </Card>
    );
  }

  const { required, optional, alternatives } = groupRequirements(requirements);

  const name = (requirement: ExerciseRequirement) =>
    requirementName(requirement, props.equipmentName, props.capabilityName);

  const describe = (requirement: ExerciseRequirement) =>
    `${
      requirement.capability_id
        ? "Требуется возможность: подойдёт любое оборудование, которое её даёт."
        : "Требуется конкретное оборудование."
    } ${CONFIDENCE_LABELS[requirement.confidence] ?? requirement.confidence} · ${
      KNOWLEDGE_SOURCE_LABELS[requirement.source] ?? requirement.source
    }`;

  return (
    <>
      <Card
        title="Обязательное оборудование"
        description="Без этого упражнение выполнить нельзя. По этим строкам система отвечает «несовместимо»."
      >
        {required.length === 0 ? (
          <Notice tone="info">
            Обязательного оборудования нет: упражнение выполняется без снарядов
            либо требования заданы только как «одно из вариантов».
          </Notice>
        ) : (
          <KeyValue
            rows={required.map((requirement) => ({
              key:
                REQUIREMENT_LABELS[requirement.requirement] ??
                requirement.requirement,
              value: name(requirement),
              hint: describe(requirement),
            }))}
          />
        )}
      </Card>

      {alternatives.length > 0 && (
        <Card
          title="Одно из вариантов"
          description="Достаточно любого элемента группы. Группы независимы: нужно по одному варианту из каждой."
        >
          {alternatives.map(({ group, variants }) => (
            <div className="subcard" key={group}>
              <div className="field-label">Группа {group}</div>
              <div className="inline-list" style={{ marginTop: "var(--s-2)" }}>
                {variants.map((variant) => (
                  <Tag
                    key={variant.equipment_id ?? variant.capability_id ?? ""}
                  >
                    {name(variant)}
                  </Tag>
                ))}
              </div>
              <div className="field-hint">
                {CONFIDENCE_LABELS[variants[0].confidence] ??
                  variants[0].confidence}{" "}
                ·{" "}
                {KNOWLEDGE_SOURCE_LABELS[variants[0].source] ??
                  variants[0].source}
              </div>
            </div>
          ))}
        </Card>
      )}

      {optional.length > 0 && (
        <Card
          title="Желательное оборудование"
          description="Упражнение выполнимо и без него: его отсутствие не делает упражнение несовместимым."
        >
          <KeyValue
            rows={optional.map((requirement) => ({
              key:
                REQUIREMENT_LABELS[requirement.requirement] ??
                requirement.requirement,
              value: name(requirement),
              hint: describe(requirement),
            }))}
          />
        </Card>
      )}
    </>
  );
}

function MediaLicense(props: Readonly<{
  photos: NonNullable<ExerciseDetail["media"]>;
  fallbackSource: string;
}>) {
  // Лицензий может быть несколько: медиа одного упражнения приходит из разных
  // источников, и указание авторства обязано перечислить все.
  const licenses = Array.from(
    new Set(
      props.photos.map((item) => item.license).filter((v): v is string => !!v),
    ),
  );
  if (licenses.length === 0) return null;
  return (
    <p className="field-hint" style={{ marginTop: "var(--s-3)" }}>
      Автор материалов: {props.photos[0].source || props.fallbackSource} ·{" "}
      {licenses.join(" · ")}
    </p>
  );
}

function ProvenanceSection(props: Readonly<{
  provenance: ExerciseProvenance | null;
}>) {
  const { provenance } = props;

  if (provenance === null) {
    return (
      <Card title="Откуда данные">
        <Skeleton rows={3} />
      </Card>
    );
  }

  const nothing =
    provenance.fields.length === 0 &&
    provenance.sources.length === 0 &&
    provenance.program_observations.length === 0;

  if (nothing) {
    return (
      <Card title="Откуда данные">
        <Empty
          title="Внешних источников нет"
          hint="Все данные упражнения получены из исходного справочника проекта."
        />
      </Card>
    );
  }

  return (
    <>
      {provenance.sources.length > 0 && (
        <Card
          title="Источники"
          description="С какими внешними записями связано упражнение и в какой роли."
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Источник</th>
                  <th>Роль</th>
                  <th>Версия</th>
                  <th className="num">Уверенность</th>
                </tr>
              </thead>
              <tbody>
                {provenance.sources.map((link) => (
                  <tr key={`${link.source_key}-${link.source_record_id}`}>
                    <td>
                      <code>{link.source_key}</code>
                      <div className="muted">
                        <code>{link.source_record_id}</code>
                      </div>
                    </td>
                    <td>
                      {SOURCE_RELATION_LABELS[link.relation] ?? link.relation}
                    </td>
                    <td>
                      <code>{link.source_version}</code>
                    </td>
                    <td className="num">
                      {link.confidence > 0 ? (
                        link.confidence.toFixed(2)
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {provenance.fields.length > 0 && (
        <Card
          title="Поля из внешних источников"
          description="Какие значения пришли извне и почему были приняты."
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Поле</th>
                  <th>Источник</th>
                  <th>Почему взято</th>
                </tr>
              </thead>
              <tbody>
                {provenance.fields.map((entry) => (
                  <tr key={entry.field}>
                    <td>{PROVENANCE_FIELD_LABELS[entry.field] ?? entry.field}</td>
                    <td>
                      <code>{entry.source_key}</code>
                    </td>
                    <td>
                      {entry.reason ? (
                        ingestionReasonLabel(entry.reason)
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {provenance.program_observations.length > 0 && (
        <Card
          title="Как упражнение используют в чужих программах"
          description="Это наблюдение источника, а не назначение нагрузки: подходы и повторения программы определяет методология проекта."
        >
          {provenance.program_observations.map((observation) => (
            <div className="subcard" key={observation.source_key}>
              <KeyValue
                rows={[
                  {
                    key: "Источник",
                    value: <code>{observation.source_key}</code>,
                  },
                  {
                    key: "Программ с этим упражнением",
                    value: observation.program_count,
                  },
                  {
                    key: "Всего вхождений",
                    value: observation.occurrence_count,
                  },
                  {
                    key: "Подходов (медиана, диапазон)",
                    value: `${observation.typical_sets_median ?? "—"}${
                      observation.typical_sets_min !== null &&
                      observation.typical_sets_max !== null
                        ? ` (${observation.typical_sets_min}–${observation.typical_sets_max})`
                        : ""
                    }`,
                  },
                  {
                    key: "Повторений (медиана, диапазон)",
                    value: `${observation.typical_reps_median ?? "—"}${
                      observation.typical_reps_min !== null &&
                      observation.typical_reps_max !== null
                        ? ` (${observation.typical_reps_min}–${observation.typical_reps_max})`
                        : ""
                    }`,
                  },
                  {
                    key: "Удержание, с (медиана)",
                    value: observation.typical_hold_seconds_median ?? "—",
                    hidden: observation.typical_hold_seconds_median === null,
                  },
                  {
                    key: "Цели программ",
                    value:
                      Object.keys(observation.source_goals).length > 0 ? (
                        Object.entries(observation.source_goals)
                          .map(([goal, value]) => `${goal}: ${value}`)
                          .join(", ")
                      ) : (
                        <span className="muted">—</span>
                      ),
                  },
                  {
                    key: "Уровни подготовки",
                    value:
                      Object.keys(observation.source_levels).length > 0 ? (
                        Object.entries(observation.source_levels)
                          .map(([level, value]) => `${level}: ${value}`)
                          .join(", ")
                      ) : (
                        <span className="muted">—</span>
                      ),
                  },
                ]}
              />
            </div>
          ))}
        </Card>
      )}
    </>
  );
}
