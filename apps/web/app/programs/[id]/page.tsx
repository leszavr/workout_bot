"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import ExerciseLink from "@/components/ExerciseLink";
import { Card, Empty, Skeleton, Status, Tag, moment } from "@/components/ui/Primitives";
import { api, getToken, ProgramResponse } from "@/lib/api";
import { generationSourceLabel, statusLabel, statusTone } from "@/lib/labels";

export default function ProgramDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const versionParam = searchParams.get("version");
  const version = versionParam ? Number(versionParam) : undefined;
  const [data, setData] = useState<ProgramResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    api
      .program(params.id, version)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [params.id, version]);

  if (error) {
    return (
      <div className="app-shell">
        <AppNav />
        <main className="main">
          <div className="error">{error}</div>
          <Link className="btn" href="/programs">
            К списку программ
          </Link>
        </main>
      </div>
    );
  }

  if (!data) {
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

  const program = data.program;
  const byAI = program.generation.source === "ai";

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <div className="page-head">
          <h1 className="page-title">{program.title}</h1>
          {program.description && (
            <p className="page-subtitle">{program.description}</p>
          )}
          <div className="field-row" style={{ marginTop: "var(--s-3)" }}>
            <Status tone={statusTone(program.status)}>
              {statusLabel(program.status)}
            </Status>
            <Tag tone={byAI ? "info" : "neutral"}>
              собрана: {generationSourceLabel(program.generation.source)}
            </Tag>
            <Tag>версия №{program.version}</Tag>
          </div>
        </div>

        <Card title="О программе">
          <div className="kv">
            <div className="k">Анкета</div>
            <div>
              <Link href={`/profiles/${program.profile_id}`}>
                открыть анкету
              </Link>
            </div>
            <div className="k">Тренировок в неделю</div>
            <div>{program.training_days_per_week}</div>
            <div className="k">Длительность</div>
            <div>{program.duration_weeks} недель</div>
            <div className="k">Создана</div>
            <div>{moment(program.created_at)}</div>
          </div>
        </Card>

        {byAI && (
          <Card
            title="Чем собрана"
            description="Сохраняется вместе с программой, чтобы результат можно было повторить."
          >
            <div className="kv">
              <div className="k">Сервис ИИ</div>
              <div>{program.generation.provider || "не сохранён"}</div>
              <div className="k">Модель</div>
              <div>{program.generation.model || "не сохранена"}</div>
              <div className="k">Версия инструкции</div>
              <div>
                {program.generation.prompt_version
                  ? `№${program.generation.prompt_version}`
                  : "не сохранена"}
              </div>
            </div>
          </Card>
        )}

        {data.versions.length > 1 && (
          <Card
            title="Другие версии"
            description="Каждая новая сборка не заменяет предыдущую, а добавляется рядом."
          >
            <div className="tabs">
              {data.versions.map((v) => (
                <button
                  key={v.version}
                  type="button"
                  className={v.version === program.version ? "active" : ""}
                  onClick={() =>
                    router.push(`/programs/${params.id}?version=${v.version}`)
                  }
                >
                  №{v.version} · {statusLabel(v.status)}
                </button>
              ))}
            </div>
          </Card>
        )}

        {program.training_days.map((day) => (
          <Card
            key={day.day_number}
            title={`День ${day.day_number}. ${day.title}`}
            description={day.focus ? `Основная работа: ${day.focus}` : undefined}
          >
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>№</th>
                    <th>Упражнение</th>
                    <th>Подходов</th>
                    <th>Повторений</th>
                    <th>Отдых</th>
                    <th>Пояснение</th>
                  </tr>
                </thead>
                <tbody>
                  {day.exercises.map((ex) => (
                    <tr key={ex.order}>
                      <td>{ex.order}</td>
                      <td>
                        <ExerciseLink
                          externalId={ex.exercise_external_id}
                          source={ex.exercise_source}
                        >
                          {ex.exercise_external_id}
                        </ExerciseLink>
                      </td>
                      <td>{ex.sets}</td>
                      <td>
                        {ex.repetitions_min}–{ex.repetitions_max}
                      </td>
                      <td>{ex.rest_seconds} с</td>
                      <td className="muted">{ex.notes ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        ))}

        {program.progression.description && (
          <Card
            title="Как усложнять"
            description="Ориентир для роста нагрузки от недели к неделе."
          >
            <p style={{ marginTop: 0 }}>{program.progression.description}</p>
            {program.progression.weekly_increase_percent !== null && (
              <p className="field-hint" style={{ marginBottom: 0 }}>
                Прибавка — до {program.progression.weekly_increase_percent}% в неделю.
              </p>
            )}
          </Card>
        )}

        <Card
          title="Что учтено для безопасности"
          description="Технические правила отбора движений, а не медицинское заключение."
        >
          {program.safety_notes.length === 0 ? (
            <Empty
              title="Ограничений не потребовалось"
              hint="В анкете нет ответов, из-за которых пришлось бы исключать упражнения."
            />
          ) : (
            <ul className="stack" style={{ margin: 0, paddingLeft: "1.2em" }}>
              {program.safety_notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
        </Card>
      </main>
    </div>
  );
}
