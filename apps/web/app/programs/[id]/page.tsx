"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import ExerciseLink from "@/components/ExerciseLink";
import { api, getToken, ProgramResponse } from "@/lib/api";
import { generationSourceLabel, statusLabel } from "@/lib/labels";

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
        </main>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="app-shell">
        <AppNav />
        <main className="main">
          <div className="loading">Загрузка...</div>
        </main>
      </div>
    );
  }

  const program = data.program;

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <h1 className="page-title">{program.title}</h1>

        <div className="card">
          <div className="section-title">Общие сведения</div>
          <div className="kv">
            <div className="k">Профиль</div>
            <div>
              <Link href={`/profiles/${program.profile_id}`}>{program.profile_id}</Link>
            </div>
            <div className="k">Версия</div>
            <div>v{program.version}</div>
            <div className="k">Статус</div>
            <div>{statusLabel(program.status)}</div>
            <div className="k">Источник генерации</div>
            <div>
              {generationSourceLabel(program.generation.source)} ({program.generation.generator_version})
            </div>
            <div className="k">Тренировок в неделю</div>
            <div>{program.training_days_per_week}</div>
            <div className="k">Длительность</div>
            <div>{program.duration_weeks} недель</div>
            <div className="k">Создана</div>
            <div>
              {program.created_at ? new Date(program.created_at).toLocaleString() : "—"}
            </div>
          </div>
          {program.description && <p className="muted">{program.description}</p>}
        </div>

        {data.versions.length > 1 && (
          <div className="card">
            <div className="section-title">Версии</div>
            <div className="tabs">
              {data.versions.map((v) => (
                <button
                  key={v.version}
                  type="button"
                  className={v.version === program.version ? "active" : ""}
                  onClick={() => router.push(`/programs/${params.id}?version=${v.version}`)}
                >
                  v{v.version} ({statusLabel(v.status)})
                </button>
              ))}
            </div>
          </div>
        )}

        {program.training_days.map((day) => (
          <div className="card" key={day.day_number}>
            <div className="section-title">
              День {day.day_number}: {day.title}
            </div>
            <p className="muted">Фокус: {day.focus}</p>
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Упражнение</th>
                  <th>Подходы</th>
                  <th>Повторения</th>
                  <th>Отдых</th>
                  <th>Примечания</th>
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
        ))}

        {program.progression.description && (
          <div className="card">
            <div className="section-title">Прогрессия</div>
            <p>{program.progression.description}</p>
            {program.progression.weekly_increase_percent !== null && (
              <p className="muted">
                Ориентир роста нагрузки: до {program.progression.weekly_increase_percent}% в неделю.
              </p>
            )}
          </div>
        )}

        <div className="card">
          <div className="section-title">Примечания по безопасности</div>
          {program.safety_notes.length === 0 ? (
            <p className="muted">Нет</p>
          ) : (
            <ul>
              {program.safety_notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          <p className="muted" style={{ marginTop: 10 }}>
            Правила безопасности — это технические правила отбора движений, а не медицинская
            диагностика или рекомендация.
          </p>
        </div>
      </main>
    </div>
  );
}
