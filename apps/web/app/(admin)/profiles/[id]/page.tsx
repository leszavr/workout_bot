"use client";

// Карточка анкеты.
//
// Ответов много (девять разделов), и вертикальным списком они занимают четыре
// экрана прокрутки: чтобы сопоставить цель с ограничениями, приходилось
// скроллить туда и обратно. Ответы разложены по вкладкам, а работа с
// программами вынесена в свою вкладку — это действие, а не чтение анкеты.

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
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
  moment,
} from "@/components/ui/Primitives";
import { LocalTabs } from "@/components/ui/Tabs";
import { ApiError, ProfileDetail, ProgramListItem, api } from "@/lib/api";
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
      .map((item) =>
        typeof item === "string" ? questionnaireLabel(item) : String(item),
      );
    return parts.join(", ") || "—";
  }
  if (typeof value === "string") return questionnaireLabel(value);
  if (typeof value === "number" || typeof value === "bigint") {
    return String(value);
  }
  return "—";
}

interface Section {
  key: string;
  title: string;
  description?: string;
  fields: ReadonlyArray<readonly [string, string]>;
}

const SECTIONS: readonly Section[] = [
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

// Ответы разложены по вкладкам так, чтобы связанные вопросы были рядом:
// «кто и чего хочет», «как и где может», «что нельзя и что нравится».
const ANSWER_TABS: ReadonlyArray<{ id: string; label: string; sections: readonly string[] }> = [
  { id: "who", label: "Человек и цель", sections: ["client", "goals"] },
  {
    id: "how",
    label: "Опыт и условия",
    sections: [
      "training_background",
      "training_plan_preferences",
      "training_location",
      "lifestyle",
    ],
  },
  {
    id: "limits",
    label: "Ограничения и предпочтения",
    sections: [
      "health_and_limitations",
      "exercise_preferences",
      "additional_information",
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
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [generatorType, setGeneratorType] = useState<"deterministic" | "ai">(
    "deterministic",
  );
  // Ключ версии, для которой сейчас готовится HTML: программа с фотографиями
  // весит несколько мегабайт, и без индикатора нажатие выглядит безответным.
  const [htmlPending, setHtmlPending] = useState("");
  const [pendingDelete, setPendingDelete] = useState<ProgramListItem | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);
  const [tab, setTab] = useState("who");

  const loadPrograms = useCallback(() => {
    api
      .profilePrograms(params.id)
      .then((response) => setPrograms(response.items))
      .catch(() => setPrograms([]));
  }, [params.id]);

  const load = useCallback(() => {
    api
      .profile(params.id)
      .then((response) => {
        setProfile(response);
        setError("");
      })
      .catch((e) => setError((e as Error).message));
    loadPrograms();
  }, [params.id, loadPrograms]);

  useEffect(() => {
    load();
  }, [load]);

  const generate = async () => {
    setGenerating(true);
    setActionError("");
    try {
      await api.generateProgram(params.id, generatorType);
      loadPrograms();
    } catch (e) {
      setActionError(
        e instanceof Error ? e.message : "Не удалось собрать программу",
      );
    } finally {
      setGenerating(false);
    }
  };

  /**
   * Открыть или скачать тот же HTML, который уходит пользователю в Telegram.
   *
   * Через blob, а не ссылкой: токен хранится в localStorage, при обычном
   * переходе браузер его не отправит и вместо программы откроется логин.
   */
  const openHtml = async (program: ProgramListItem, download: boolean) => {
    const key = `${program.program_id}-v${program.version}`;
    setHtmlPending(key);
    setActionError("");
    let url = "";
    try {
      const blob = await api.programHtml(program.program_id, program.version);
      url = URL.createObjectURL(blob);
      if (download) {
        const link = document.createElement("a");
        link.href = url;
        link.download = `program_${program.program_id}_v${program.version}.html`;
        link.click();
      } else {
        window.open(url, "_blank", "noopener");
      }
    } catch (e) {
      setActionError(
        e instanceof Error ? e.message : "Не удалось получить HTML",
      );
    } finally {
      // Ссылка живёт до закрытия вкладки: слишком ранний revoke обрывает
      // открытие документа, слишком поздний держит файл в памяти.
      if (url) setTimeout(() => URL.revokeObjectURL(url), 60_000);
      setHtmlPending("");
    }
  };

  /**
   * Удалить программу вместе со всеми её версиями.
   *
   * Анкета остаётся: программа производна от неё и может быть собрана заново.
   * Обратный порядок запрещён сервером — анкету с программами удалить нельзя.
   */
  const deleteProgram = async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    setActionError("");
    try {
      await api.deleteProgram(pendingDelete.program_id);
      setNotice(`Программа «${pendingDelete.title}» удалена`);
      window.setTimeout(() => setNotice(""), 6000);
      setPendingDelete(null);
      loadPrograms();
    } catch (e) {
      setActionError((e as ApiError).message);
      setPendingDelete(null);
    } finally {
      setDeleting(false);
    }
  };

  if (error) {
    return (
      <>
        <ErrorState message={error} onRetry={load} />
        <Link className="btn" href="/profiles">
          К списку анкет
        </Link>
      </>
    );
  }

  if (!profile) {
    return (
      <Card>
        <Skeleton rows={5} />
      </Card>
    );
  }

  const data = profile.data as Record<string, Record<string, unknown>>;
  const activeSections = SECTIONS.filter((section) =>
    ANSWER_TABS.find((item) => item.id === tab)?.sections.includes(section.key),
  );

  const programColumns: ReadonlyArray<DataColumn<ProgramListItem>> = [
    {
      key: "version",
      header: "Версия",
      numeric: true,
      render: (item) => `№${item.version}`,
    },
    {
      key: "title",
      header: "Название",
      render: (item) => (
        <Link href={`/programs/${item.program_id}`}>{item.title}</Link>
      ),
    },
    {
      key: "status",
      header: "Состояние",
      render: (item) => (
        <Status tone={statusTone(item.status)}>{statusLabel(item.status)}</Status>
      ),
    },
    {
      key: "source",
      header: "Собрана",
      render: (item) => (
        <Tag tone={item.generation_source === "ai" ? "info" : "neutral"}>
          {generationSourceLabel(item.generation_source)}
        </Tag>
      ),
    },
    {
      key: "delivered",
      header: "Отправлена",
      hint: "Отправка в Telegram — единственный достоверный факт получения: открыл ли человек документ, Bot API не сообщает.",
      render: (item) =>
        item.delivered ? (
          <Tag tone="ok">отправлена</Tag>
        ) : (
          <span className="muted">нет</span>
        ),
    },
    {
      key: "created",
      header: "Когда",
      render: (item) => <span className="muted">{moment(item.created_at)}</span>,
    },
    {
      key: "html",
      header: "Файл программы",
      render: (item) => {
        const busy = htmlPending === `${item.program_id}-v${item.version}`;
        return (
          <div className="button-row">
            <button
              type="button"
              className="small"
              disabled={busy}
              onClick={() => openHtml(item, false)}
            >
              {busy ? "Готовим…" : "Открыть"}
            </button>
            <button
              type="button"
              className="small"
              disabled={busy}
              onClick={() => openHtml(item, true)}
            >
              Скачать
            </button>
          </div>
        );
      },
    },
    {
      key: "actions",
      header: "Действия",
      hidden: !canWrite,
      render: (item) => (
        <button
          type="button"
          className="small danger"
          onClick={() => setPendingDelete(item)}
        >
          Удалить
        </button>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title={
          profile.display_number
            ? `Анкета № ${profile.display_number}`
            : "Анкета без номера"
        }
        description="Ответы, которые человек дал боту. По ним подбираются упражнения."
        actions={
          <Status tone={statusTone(profile.status)}>
            {statusLabel(profile.status)}
          </Status>
        }
        tabs={
          <LocalTabs
            label="Разделы анкеты"
            tabs={[
              ...ANSWER_TABS.map((item) => ({ id: item.id, label: item.label })),
              {
                id: "consents",
                label: "Согласия",
                count: profile.consents.length,
              },
              {
                id: "programs",
                label: "Программы",
                count: programs.length,
              },
            ]}
            active={tab}
            onChange={setTab}
          />
        }
      />

      {notice && <Notice tone="ok">{notice}</Notice>}
      {actionError && <ErrorState message={actionError} />}

      {activeSections.map((section) => (
        <Card
          key={section.key}
          title={section.title}
          description={section.description}
        >
          <KeyValue
            rows={section.fields.map(([key, label]) => ({
              key: label,
              value: formatValue((data[section.key] || {})[key]),
            }))}
          />
        </Card>
      ))}

      {tab === "consents" && (
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
                  {profile.consents.map((consent) => (
                    <tr key={consent.consent_type}>
                      <td>{consentLabel(consent.consent_type)}</td>
                      <td>{consent.consent_version}</td>
                      <td className="muted">{moment(consent.granted_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {tab === "programs" && (
        <>
          {canWrite ? (
            <Card
              title="Собрать программу"
              description="Каждая сборка сохраняется отдельной версией — предыдущие остаются."
            >
              <div className="stack">
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
              <div className="button-row" style={{ marginTop: "var(--s-3)" }}>
                <button
                  type="button"
                  className="primary"
                  disabled={generating}
                  onClick={generate}
                >
                  {generating ? "Собираем…" : "Собрать программу"}
                </button>
              </div>
            </Card>
          ) : (
            <Notice tone="info" title="Доступ только для просмотра">
              Собрать программу может только администратор — у вашей роли доступ
              на просмотр.
            </Notice>
          )}

          <Card title="Программы по этой анкете">
            <DataTable
              columns={programColumns}
              rows={programs}
              rowKey={(item) => `${item.program_id}-v${item.version}`}
              emptyTitle="Программ по этой анкете ещё нет"
              emptyHint={
                canWrite
                  ? "Выберите, кто соберёт программу, и нажмите «Собрать программу»."
                  : "Программа появится здесь после сборки."
              }
            />
          </Card>
        </>
      )}

      {pendingDelete && (
        <ConfirmDialog
          title={`Удалить программу «${pendingDelete.title}»?`}
          description="Будут удалены все её версии и записи об отправке. Анкета останется, программу можно собрать заново."
          confirmLabel="Удалить программу"
          danger
          busy={deleting}
          onConfirm={deleteProgram}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </>
  );
}
