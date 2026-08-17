"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import { api, ExerciseDetail, getToken } from "@/lib/api";
import {
  DIFFICULTY_LABELS,
  EXERCISE_TYPE_LABELS,
  FORCE_LABELS,
  MECHANIC_LABELS,
} from "@/lib/labels";

function List({ items }: { readonly items: string[] }) {
  if (!items.length) return <span className="muted">—</span>;
  return <>{items.join(", ")}</>;
}

export default function ExerciseDetailPage() {
  const params = useParams<{ id: string }>();
  const [exercise, setExercise] = useState<ExerciseDetail | null>(null);
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
  if (!exercise) {
    return (
      <div className="app-shell">
        <AppNav />
        <main className="main">
          <div className="loading">Загрузка...</div>
        </main>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <h1 className="page-title">{exercise.name_ru || exercise.name}</h1>
        <p className="muted">{exercise.name}</p>

        <div className="card">
          <div className="section-title">Характеристики</div>
          <div className="kv">
            <div className="k">Оборудование</div>
            <div><List items={exercise.equipment} /></div>
            <div className="k">Основные мышцы</div>
            <div><List items={exercise.primary_muscles} /></div>
            <div className="k">Дополнительные мышцы</div>
            <div><List items={exercise.secondary_muscles} /></div>
            <div className="k">Категория</div>
            <div>{exercise.exercise_type ? EXERCISE_TYPE_LABELS[exercise.exercise_type] ?? exercise.exercise_type : "—"}</div>
            <div className="k">Сложность</div>
            <div>{exercise.difficulty ? DIFFICULTY_LABELS[exercise.difficulty] ?? exercise.difficulty : "—"}</div>
            <div className="k">Тип усилия / механика</div>
            <div>
              {exercise.force ? FORCE_LABELS[exercise.force] ?? exercise.force : "—"} /{" "}
              {exercise.mechanic ? MECHANIC_LABELS[exercise.mechanic] ?? exercise.mechanic : "—"}
            </div>
          </div>
        </div>

        {exercise.description && (
          <div className="card">
            <div className="section-title">Описание</div>
            <p>{exercise.description}</p>
          </div>
        )}

        <div className="card">
          <div className="section-title">Техника выполнения</div>
          {(() => {
            const technique = exercise.technique_ru || exercise.technique;
            if (!technique) {
              return <p className="muted">Описание техники отсутствует в источнике</p>;
            }
            return <p style={{ whiteSpace: "pre-line" }}>{technique}</p>;
          })()}
        </div>

        <div className="card">
          <div className="section-title">Изображения</div>
          {exercise.images.length === 0 ? (
            <p className="muted">Изображения отсутствуют</p>
          ) : (
            <ul>
              {exercise.images.map((img) => (
                <li key={img} className="muted">
                  {img}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="card">
          <div className="section-title">Ограничения и противопоказания</div>
          <div className="kv">
            <div className="k">Противопоказания</div>
            <div><List items={exercise.contraindications} /></div>
            <div className="k">Ограничения</div>
            <div><List items={exercise.limitations} /></div>
          </div>
          <p className="muted" style={{ marginTop: 10 }}>
            Противопоказания заполняются на этапе правил безопасности.
          </p>
        </div>

        <div className="card">
          <div className="section-title">Источник данных</div>
          <div className="kv">
            <div className="k">Источник</div>
            <div>{exercise.source}</div>
            <div className="k">Версия источника</div>
            <div>{exercise.source_version || "—"}</div>
            <div className="k">Внешний ID</div>
            <div>{exercise.external_id}</div>
          </div>
        </div>
      </main>
    </div>
  );
}
