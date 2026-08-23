"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import {
  Card,
  Empty,
  Notice,
  Skeleton,
  Status,
  Tag,
  moment,
} from "@/components/ui/Primitives";
import { api, getToken, ProfileDetail, ProgramListItem } from "@/lib/api";
import {
  consentLabel,
  generationSourceLabel,
  questionnaireLabel,
  statusLabel,
  statusTone,
} from "@/lib/labels";
import { useCurrentUser } from "@/lib/session";

/**
 * Значение ответа в читаемом виде.
 *
 * Коды из бота переводим, свободный текст оставляем как есть, «да/нет»
 * пишем словами: `true` в таблице выглядит как техническая утечка.
 */
function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  if (Array.isArray(value)) {
    const parts = value
      .filter((item) => item !== null && item !== undefined && item !== "")
      .map((item) => (typeof item === "string" ? questionnaireLabel(item) : String(item)));
    return parts.join(", ") || "—";
  }
  if (typeof value === "string") return questionnaireLabel(value);
  if (typeof value === "number" || typeof value === "bigint") return String(value);
  return "—";
}

function Answers(props: {
  readonly data: Record<string, unknown>;
  readonly fields: ReadonlyArray<readonly [string, string]>;
}) {
  return (
    <div className="kv">
      {props.fields.map(([key, label]) => (
        <div key={key} style={{ display: "contents" }}>
          <div className="k">{label}</div>
          <div>{formatValue(props.data?.[key])}</div>
        </div>
      ))}
    </div>
  );
}

const SECTIONS: ReadonlyArray<{
  key: string;
  title: string;
  description?: string;
  fields: ReadonlyArray<readonly [string, string]>;
}> = [
  {
    key: "client",
    title: "О человеке",
    fields: [
      ["name", "Имя"],
      ["age_years", "Возраст, лет"],
      ["sex", "Пол"],
      ["height_cm", "Рост, см"],
      ["weight_kg", "Вес, кг"],
      ["waist_cm", "Обхват талии, см"],
    ],
  },
  {
    key: "goals",
    title: "Чего хочет добиться",
    description: "Основная цель определяет, как строится программа.",
    fields: [
      ["primary", "Основная цель"],
      ["secondary", "Дополнительные цели"],
      ["desired_result", "Желаемый результат"],
      ["target_timeframe", "За какой срок"],
    ],
  },
  {
    key: "training_background",
    title: "Опыт тренировок",
    fields: [
      ["experience_level", "Занимался раньше"],
      ["current_frequency_per_week", "Тренировок в неделю сейчас"],
      ["current_activity_description", "Чем занимается сейчас"],
      ["current_exercises", "Знакомые упражнения"],
    ],
  },
  {
    key: "training_plan_preferences",
    title: "Удобный график",
    description: "Сколько занятий в неделю человек готов выдержать.",
    fields: [
      ["sessions_per_week", "Тренировок в неделю"],
      ["session_duration_minutes", "Длительность занятия, мин"],
      ["preferred_days", "Удобные дни"],
      ["preferred_training_time", "Удобное время"],
    ],
  },
  {
    key: "training_location",
    title: "Где и чем тренируется",
    description: "Программа собирается только из доступного оборудования.",
    fields: [
      ["primary_location", "Место тренировок"],
      ["gym_name", "Зал"],
      ["available_equipment", "Доступное оборудование"],
      ["custom_equipment_description", "Что есть дома"],
    ],
  },
  {
    key: "lifestyle",
    title: "Образ жизни",
    description: "Влияет на объём кардио и общую нагрузку.",
    fields: [
      ["daily_activity_level", "Повседневная активность"],
      ["cardio_preference", "Отношение к кардио"],
      ["cardio_notes", "Уточнение про кардио"],
    ],
  },
  {
    key: "health_and_limitations",
    title: "Здоровье и ограничения",
    description: "Эти ответы исключают из программы небезопасные упражнения.",
    fields: [
      ["has_limitations", "Есть ограничения"],
      ["categories", "С чем связаны"],
      ["movements_to_avoid", "Каких движений избегать"],
      ["doctor_recommendations", "Рекомендации врача"],
      ["medical_clearance_required", "Нужно разрешение врача"],
    ],
  },
  {
    key: "exercise_preferences",
    title: "Предпочтения",
    fields: [
      ["preferred_exercises", "Нравятся"],
      ["disliked_exercises", "Не нравятся"],
      ["exercise_goals", "Хочет освоить"],
    ],
  },
  {
    key: "additional_information",
    title: "Что добавил сам",
    fields: [
      ["schedule_constraints", "Ограничения по расписанию"],
      ["free_text", "Комментарий"],
    ],
  },
];

export default function ProfileDetailPage() {
  const params = useParams<{ id: string }>();
  const { canWrite } = useCurrentUser();
  const [profile, setProfile] = useState<ProfileDetail | null>(null);
  const [error, setError] = useState("");
  const [programs, setPrograms] = useState<ProgramListItem[]>([]);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState("");
  const [generatorType, setGeneratorType] = useState<"deterministic" | "ai">(
    "deterministic",
  );

  const loadPrograms = useCallback(() => {
    api
      .profilePrograms(params.id)
      .then((res) => setPrograms(res.items))
      .catch(() => setPrograms([]));
  }, [params.id]);

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    api
      .profile(params.id)
      .then(setProfile)
      .catch((e) => setError(e.message));
    loadPrograms();
  }, [params.id, loadPrograms]);

  async function onGenerate() {
    setGenerating(true);
    setGenerateError("");
    try {
      await api.generateProgram(params.id, generatorType);
      loadPrograms();
    } catch (e) {
      setGenerateError(
        e instanceof Error ? e.message : "Не удалось собрать программу",
      );
    } finally {
      setGenerating(false);
    }
  }

  if (error) {
    return (
      <div className="app-shell">
        <AppNav />
        <main className="main">
          <div className="error">{error}</div>
          <Link className="btn" href="/profiles">
            К списку анкет
          </Link>
        </main>
      </div>
    );
  }

  if (!profile) {
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

  const data = profile.data as Record<string, Record<string, unknown>>;

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <div className="page-head">
          <h1 className="page-title">
            {profile.display_number
              ? `Анкета № ${profile.display_number}`
              : "Анкета без номера"}
          </h1>
          <p className="page-subtitle">
            Ответы, которые человек дал боту. По ним подбираются упражнения.
          </p>
          <div className="field-row" style={{ marginTop: "var(--s-3)" }}>
            <Status tone={statusTone(profile.status)}>
              {statusLabel(profile.status)}
            </Status>
          </div>
        </div>

        {SECTIONS.map((section) => (
          <Card
            key={section.key}
            title={section.title}
            description={section.description}
          >
            <Answers data={data[section.key] || {}} fields={section.fields} />
          </Card>
        ))}

        <Card
          title="Согласия"
          description="Без согласия на обработку данных программа не собирается."
        >
          {profile.consents.length === 0 ? (
            <Empty
              title="Согласий нет"
              hint="Человек ещё не дошёл до этого шага в боте."
            />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>На что согласился</th>
                    <th>Редакция текста</th>
                    <th>Когда</th>
                  </tr>
                </thead>
                <tbody>
                  {profile.consents.map((c) => (
                    <tr key={c.consent_type}>
                      <td>{consentLabel(c.consent_type)}</td>
                      <td>{c.consent_version}</td>
                      <td className="muted">{moment(c.granted_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card
          title="Программы по этой анкете"
          description="Каждая сборка сохраняется отдельной версией — предыдущие остаются."
        >
          {generateError && <div className="error">{generateError}</div>}

          {canWrite ? (
            <div className="subcard" style={{ marginBottom: "var(--s-4)" }}>
              <div className="field-label">Кто соберёт программу</div>
              <div className="stack" style={{ marginTop: "var(--s-2)" }}>
                <label className="check">
                  <input
                    type="radio"
                    name="generator"
                    value="deterministic"
                    checked={generatorType === "deterministic"}
                    onChange={() => setGeneratorType("deterministic")}
                  />
                  <span>
                    Алгоритм подбора
                    <span className="field-hint" style={{ display: "block" }}>
                      Правила отбора упражнений из каталога. Работает всегда.
                    </span>
                  </span>
                </label>
                <label className="check">
                  <input
                    type="radio"
                    name="generator"
                    value="ai"
                    checked={generatorType === "ai"}
                    onChange={() => setGeneratorType("ai")}
                  />
                  <span>
                    Искусственный интеллект
                    <span className="field-hint" style={{ display: "block" }}>
                      Нужны настроенные сервис и модель. Если ИИ не ответит,
                      сборка не состоится и система покажет причину — программу
                      тогда можно собрать алгоритмом.
                    </span>
                  </span>
                </label>
              </div>
              <div className="button-row">
                <button
                  type="button"
                  className="primary"
                  disabled={generating}
                  onClick={onGenerate}
                >
                  {generating ? "Собираем…" : "Собрать программу"}
                </button>
              </div>
            </div>
          ) : (
            <Notice tone="info">
              Собрать программу может только администратор — у вашей роли доступ
              на просмотр.
            </Notice>
          )}

          {programs.length === 0 ? (
            <Empty
              title="Программ по этой анкете ещё нет"
              hint={
                canWrite
                  ? "Выберите, кто соберёт программу, и нажмите «Собрать программу»."
                  : "Программа появится здесь после сборки."
              }
            />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Версия</th>
                    <th>Название</th>
                    <th>Состояние</th>
                    <th>Собрана</th>
                    <th>Когда</th>
                  </tr>
                </thead>
                <tbody>
                  {programs.map((p) => (
                    <tr key={`${p.program_id}-v${p.version}`}>
                      <td>№{p.version}</td>
                      <td>
                        <Link href={`/programs/${p.program_id}`}>{p.title}</Link>
                      </td>
                      <td>
                        <Status tone={statusTone(p.status)}>
                          {statusLabel(p.status)}
                        </Status>
                      </td>
                      <td>
                        <Tag tone={p.generation_source === "ai" ? "info" : "neutral"}>
                          {generationSourceLabel(p.generation_source)}
                        </Tag>
                      </td>
                      <td className="muted">{moment(p.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </main>
    </div>
  );
}
