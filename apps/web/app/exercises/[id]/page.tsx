"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import { Card, Empty, Skeleton, Status } from "@/components/ui/Primitives";
import { API_BASE, api, ExerciseDetail, getToken } from "@/lib/api";
import {
  DIFFICULTY_LABELS,
  EXERCISE_TYPE_LABELS,
  FORCE_LABELS,
  MECHANIC_LABELS,
  equipmentList,
  muscleList,
} from "@/lib/labels";

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
  const license = photos.length > 0 ? photos[0].license : null;
  const restrictions =
    exercise.contraindications.length > 0 || exercise.limitations.length > 0;

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
            <div className="k">Оборудование</div>
            <div>
              <Text value={equipmentList(exercise.equipment)} />
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

        <Card title="Фотографии">
          {photos.length > 0 ? (
            <>
              <div style={{ display: "flex", gap: "var(--s-3)", flexWrap: "wrap" }}>
                {photos.map((item) => (
                  <a
                    key={item.sequence}
                    href={`${API_BASE}${item.url}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={`${API_BASE}${item.url}`}
                      alt={`${exercise.name_ru || exercise.name} — фото ${item.sequence}`}
                      style={{
                        height: 150,
                        width: "auto",
                        borderRadius: "var(--radius)",
                        border: "1px solid var(--border)",
                      }}
                    />
                  </a>
                ))}
              </div>
              {license && (
                <p className="field-hint" style={{ marginTop: "var(--s-3)" }}>
                  Автор материалов: {photos[0].source || exercise.source} · лицензия{" "}
                  {license}
                </p>
              )}
            </>
          ) : (
            <Empty
              title="Фотографий нет"
              hint="Упражнение загружено без изображений."
            />
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
