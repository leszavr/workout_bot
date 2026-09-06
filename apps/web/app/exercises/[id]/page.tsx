"use client";

// Карточка упражнения.
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
import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import { Card, Empty, Notice, Skeleton, Status, Tag } from "@/components/ui/Primitives";
import {
  API_BASE,
  EquipmentCapability,
  EquipmentItem,
  ExerciseAlternative,
  ExerciseDetail,
  ExerciseProvenance,
  ExerciseRequirement,
  api,
  getToken,
  ingestionApi,
  knowledgeApi,
} from "@/lib/api";
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

function Text({ value }: { readonly value: string }) {
  if (value === "—") return <span className="muted">—</span>;
  return <>{value}</>;
}

function List({ items }: { readonly items: string[] }) {
  if (!items.length) return <span className="muted">—</span>;
  return <>{items.join(", ")}</>;
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

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    api
      .exercise(Number(params.id))
      .then(setExercise)
      .catch((e) => setError(e.message));
  }, [params.id]);

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
      <div className="app-shell">
        <AppNav />
        <main className="main">
          <div className="error">{error}</div>
          <Link className="btn" href="/exercises">
            К каталогу упражнений
          </Link>
        </main>
      </div>
    );
  }

  if (!exercise) {
    return (
      <div className="app-shell">
        <AppNav />
        <main className="main">
          <Card>
            <Skeleton rows={5} />
          </Card>
        </main>
      </div>
    );
  }

  const technique = exercise.technique_ru || exercise.technique;
  const photos = exercise.media ?? [];
  // Лицензий может быть несколько: медиа одного упражнения приходит из разных
  // источников, и указание авторства обязано перечислить все.
  const licenses = Array.from(
    new Set(photos.map((item) => item.license).filter((v): v is string => !!v)),
  );
  const restrictions =
    exercise.contraindications.length > 0 || exercise.limitations.length > 0;

  const equipmentName = (id: string) => equipment[id]?.name_ru ?? id;
  const capabilityName = (id: string) => capabilities[id]?.name_ru ?? id;

  // Группы «одно из» показываются вместе: три отдельные строки не сообщают, что
  // достаточно любого из вариантов. Обычный объект, а не Map: цель компиляции
  // проекта — ES5, и перебор Map требовал бы downlevelIteration.
  const groups: Record<number, ExerciseRequirement[]> = {};
  const plain: ExerciseRequirement[] = [];
  for (const requirement of requirements ?? []) {
    if (
      requirement.requirement === "alternative" &&
      requirement.alternative_group !== null
    ) {
      const group = requirement.alternative_group;
      groups[group] = [...(groups[group] ?? []), requirement];
    } else {
      plain.push(requirement);
    }
  }
  const groupNumbers = Object.keys(groups)
    .map(Number)
    .sort((a, b) => a - b);

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <div className="page-head">
          <h1 className="page-title">{exercise.name_ru || exercise.name}</h1>
          {exercise.name_ru && <p className="page-subtitle">{exercise.name}</p>}
          <div className="field-row" style={{ marginTop: "var(--s-3)" }}>
            <Status tone={exercise.is_active ? "ok" : "neutral"}>
              {exercise.is_active
                ? "используется в программах"
                : "в новые программы не попадает"}
            </Status>
          </div>
        </div>

        <Card
          title="Характеристики"
          description="По этим признакам упражнение подбирается в программу."
        >
          <div className="kv">
            <div className="k">Оборудование в справочнике</div>
            <div>
              <Text value={equipmentList(exercise.equipment)} />
              <div className="field-hint">
                Значение источника каталога. Подбор опирается на требования ниже.
              </div>
            </div>
            <div className="k">Основные мышцы</div>
            <div>
              <Text value={muscleList(exercise.primary_muscles)} />
            </div>
            <div className="k">Дополнительные мышцы</div>
            <div>
              <Text value={muscleList(exercise.secondary_muscles)} />
            </div>
            <div className="k">Вид нагрузки</div>
            <div>
              {exercise.exercise_type
                ? EXERCISE_TYPE_LABELS[exercise.exercise_type] ??
                  exercise.exercise_type
                : "—"}
            </div>
            <div className="k">Сложность</div>
            <div>
              {exercise.difficulty
                ? DIFFICULTY_LABELS[exercise.difficulty] ?? exercise.difficulty
                : "—"}
            </div>
            <div className="k">Характер усилия</div>
            <div>
              {exercise.force ? FORCE_LABELS[exercise.force] ?? exercise.force : "—"}
            </div>
            <div className="k">Работа мышц</div>
            <div>
              {exercise.mechanic
                ? MECHANIC_LABELS[exercise.mechanic] ?? exercise.mechanic
                : "—"}
            </div>
          </div>
        </Card>

        <Card
          title="Требования к оборудованию"
          description="Нормализованное знание системы. По нему определяется, выполнимо ли упражнение доступным оборудованием."
        >
          {requirements === null && <Skeleton rows={2} />}

          {requirements !== null && requirements.length === 0 && (
            <Notice tone="warn" title="Требования не заполнены">
              Система отвечает по этому упражнению «неизвестно», а не
              «оборудование не нужно»: отсутствие данных не является
              доказательством ни того, ни другого. Пока требования не заполнены,
              подбор с учётом оборудования для него не работает.
            </Notice>
          )}

          {requirements !== null && requirements.length > 0 && (
            <div className="kv">
              {plain.map((requirement) => (
                <RequirementRow
                  key={`${requirement.equipment_id ?? requirement.capability_id}-${requirement.requirement}`}
                  requirement={requirement}
                  equipmentName={equipmentName}
                  capabilityName={capabilityName}
                />
              ))}
              {groupNumbers.map((group) => {
                const variants = groups[group];
                return (
                  <div key={`group-${group}`} style={{ display: "contents" }}>
                    <div className="k">Одно из вариантов</div>
                    <div>
                      <div
                        className="field-row"
                        style={{ flexWrap: "wrap", gap: 4 }}
                      >
                        {variants.map((variant) => (
                          <Tag
                            key={
                              variant.equipment_id ??
                              variant.capability_id ??
                              ""
                            }
                          >
                            {variant.equipment_id
                              ? equipmentName(variant.equipment_id)
                              : capabilityName(variant.capability_id ?? "")}
                          </Tag>
                        ))}
                      </div>
                      <div className="field-hint">
                        Достаточно любого из перечисленного.{" "}
                        {CONFIDENCE_LABELS[variants[0]?.confidence ?? ""] ?? ""}{" "}
                        ·{" "}
                        {KNOWLEDGE_SOURCE_LABELS[variants[0]?.source ?? ""] ??
                          variants[0]?.source}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        <Card
          title="Чем можно заменить"
          description="Тип замены различается: «полная замена» и «похожее движение» — разные утверждения, и одно не выдаётся за другое."
        >
          {alternatives.length === 0 ? (
            <Empty
              title="Замен не найдено"
              hint="Совпадений по основным мышцам и характеру движения нет либо знание ещё не пересчитано."
            />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Упражнение</th>
                    <th>Тип замены</th>
                    <th>Оборудование</th>
                    <th>Совпадение</th>
                  </tr>
                </thead>
                <tbody>
                  {alternatives.map((alternative) => (
                    <tr key={alternative.alternative_external_id}>
                      <td>
                        <Link
                          href={`/exercises?search=${encodeURIComponent(alternative.alternative_external_id)}`}
                        >
                          {alternative.alternative_external_id}
                        </Link>
                      </td>
                      <td>
                        <Status
                          tone={substitutionTone(alternative.substitution)}
                        >
                          {SUBSTITUTION_LABELS[alternative.substitution] ??
                            alternative.substitution}
                        </Status>
                      </td>
                      <td>
                        {Array.isArray(alternative.rationale.equipment) &&
                        (alternative.rationale.equipment as string[]).length >
                          0 ? (
                          (alternative.rationale.equipment as string[])
                            .map(equipmentName)
                            .join(", ")
                        ) : (
                          <span className="muted">не заполнено</span>
                        )}
                      </td>
                      <td>{Math.round(alternative.score * 100)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {exercise.description && (
          <Card title="Описание">
            <p style={{ margin: 0 }}>{exercise.description}</p>
          </Card>
        )}

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
              <div style={{ display: "flex", gap: "var(--s-3)", flexWrap: "wrap" }}>
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
                      style={{
                        height: 150,
                        width: "auto",
                        borderRadius: "var(--radius)",
                        border: "1px solid var(--border)",
                      }}
                    />
                    <div className="muted" style={{ textAlign: "center" }}>
                      {item.media_type === "animation" ? "анимация" : "фото"}
                    </div>
                  </a>
                ))}
              </div>
              {licenses.length > 0 && (
                <p className="field-hint" style={{ marginTop: "var(--s-3)" }}>
                  Автор материалов: {photos[0].source || exercise.source} ·{" "}
                  {licenses.join(" · ")}
                </p>
              )}
            </>
          ) : (
            <Empty
              title="Изображений нет"
              hint="Упражнение загружено без фотографий и анимаций."
            />
          )}
        </Card>

        <Card
          title="Откуда данные"
          description="Происхождение полей и связи с внешними источниками. Пустой раздел означает, что упражнение пришло из исходного справочника проекта."
        >
          {provenance === null && <Skeleton rows={2} />}

          {provenance !== null &&
            provenance.fields.length === 0 &&
            provenance.sources.length === 0 &&
            provenance.program_observations.length === 0 && (
              <Empty
                title="Внешних источников нет"
                hint="Все данные упражнения получены из исходного справочника проекта."
              />
            )}

          {provenance !== null && provenance.sources.length > 0 && (
            <>
              <h3 className="section-title">Источники</h3>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Источник</th>
                      <th>Роль</th>
                      <th>Версия</th>
                      <th>Уверенность</th>
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
                        <td>
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
            </>
          )}

          {provenance !== null && provenance.fields.length > 0 && (
            <>
              <h3 className="section-title">Поля из внешних источников</h3>
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
                        <td>
                          {PROVENANCE_FIELD_LABELS[entry.field] ?? entry.field}
                        </td>
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
            </>
          )}

          {provenance !== null && provenance.program_observations.length > 0 && (
            <>
              <h3 className="section-title">Как упражнение используют в чужих программах</h3>
              <p className="field-hint">
                Это наблюдение источника, а не назначение нагрузки: подходы и
                повторения программы определяет методология проекта.
              </p>
              {provenance.program_observations.map((observation) => (
                <div className="kv" key={observation.source_key}>
                  <div className="k">Источник</div>
                  <div>
                    <code>{observation.source_key}</code>
                  </div>

                  <div className="k">Программ с этим упражнением</div>
                  <div>{observation.program_count}</div>

                  <div className="k">Всего вхождений</div>
                  <div>{observation.occurrence_count}</div>

                  <div className="k">Подходов (медиана, диапазон)</div>
                  <div>
                    {observation.typical_sets_median ?? "—"}
                    {observation.typical_sets_min !== null &&
                      observation.typical_sets_max !== null &&
                      ` (${observation.typical_sets_min}–${observation.typical_sets_max})`}
                  </div>

                  <div className="k">Повторений (медиана, диапазон)</div>
                  <div>
                    {observation.typical_reps_median ?? "—"}
                    {observation.typical_reps_min !== null &&
                      observation.typical_reps_max !== null &&
                      ` (${observation.typical_reps_min}–${observation.typical_reps_max})`}
                  </div>

                  {observation.typical_hold_seconds_median !== null && (
                    <>
                      <div className="k">Удержание, с (медиана)</div>
                      <div>{observation.typical_hold_seconds_median}</div>
                    </>
                  )}

                  <div className="k">Цели программ</div>
                  <div>
                    {Object.keys(observation.source_goals).length > 0 ? (
                      Object.entries(observation.source_goals)
                        .map(([goal, value]) => `${goal}: ${value}`)
                        .join(", ")
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </div>

                  <div className="k">Уровни подготовки</div>
                  <div>
                    {Object.keys(observation.source_levels).length > 0 ? (
                      Object.entries(observation.source_levels)
                        .map(([level, value]) => `${level}: ${value}`)
                        .join(", ")
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </div>
                </div>
              ))}
            </>
          )}
        </Card>

        {restrictions && (
          <Card
            title="Кому не подходит"
            description="Учитывается при подборе, если человек указал ограничения в анкете."
          >
            <div className="kv">
              <div className="k">Противопоказания</div>
              <div>
                <List items={exercise.contraindications} />
              </div>
              <div className="k">Ограничения</div>
              <div>
                <List items={exercise.limitations} />
              </div>
            </div>
          </Card>
        )}
      </main>
    </div>
  );
}

function RequirementRow(props: Readonly<{
  requirement: ExerciseRequirement;
  equipmentName: (id: string) => string;
  capabilityName: (id: string) => string;
}>) {
  const { requirement } = props;
  const target = requirement.equipment_id
    ? props.equipmentName(requirement.equipment_id)
    : props.capabilityName(requirement.capability_id ?? "");
  return (
    <>
      <div className="k">
        {REQUIREMENT_LABELS[requirement.requirement] ?? requirement.requirement}
      </div>
      <div>
        <div>{target}</div>
        <div className="field-hint">
          {requirement.capability_id
            ? "Требуется возможность: подойдёт любое оборудование, которое её даёт."
            : "Требуется конкретное оборудование."}{" "}
          {CONFIDENCE_LABELS[requirement.confidence] ?? requirement.confidence} ·{" "}
          {KNOWLEDGE_SOURCE_LABELS[requirement.source] ?? requirement.source}
        </div>
      </div>
    </>
  );
}
