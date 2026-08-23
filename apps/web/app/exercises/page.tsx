"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import { Card, Empty, Field, Skeleton, Status } from "@/components/ui/Primitives";
import { api, ExerciseListItem, getToken, ListResponse } from "@/lib/api";
import { DIFFICULTY_LABELS, EXERCISE_TYPE_LABELS, equipmentList, muscleList } from "@/lib/labels";

export default function ExercisesPage() {
  const [data, setData] = useState<ListResponse<ExerciseListItem> | null>(null);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [difficulty, setDifficulty] = useState("");
  const [exerciseType, setExerciseType] = useState("");
  const [loading, setLoading] = useState(true);

  function load(s: string, d: string, t: string) {
    setLoading(true);
    api
      .exercises({
        search: s || undefined,
        difficulty: d || undefined,
        exercise_type: t || undefined,
        limit: 100,
      })
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    load("", "", "");
  }, []);

  const filtered = search !== "" || difficulty !== "" || exerciseType !== "";

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <div className="page-head">
          <h1 className="page-title">Упражнения</h1>
          <p className="page-subtitle">
            Каталог, из которого собираются программы. Выключенное упражнение
            остаётся в справочнике, но в новые программы не попадает.
          </p>
        </div>

        {error && <div className="error">{error}</div>}

        <Card>
          <div className="filters">
            <Field
              label="Поиск по названию"
              hint="Ищет и по русскому, и по английскому названию."
              htmlFor="ex-search"
            >
              <input
                id="ex-search"
                type="search"
                placeholder="Например: приседания"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) =>
                  e.key === "Enter" && load(search, difficulty, exerciseType)
                }
              />
            </Field>
            <Field
              label="Сложность"
              hint="Для кого упражнение подходит по технике."
              htmlFor="ex-difficulty"
            >
              <select
                id="ex-difficulty"
                value={difficulty}
                onChange={(e) => setDifficulty(e.target.value)}
              >
                <option value="">Любая</option>
                {Object.entries(DIFFICULTY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="Вид нагрузки"
              hint="Силовые, кардио, растяжка и другие."
              htmlFor="ex-type"
            >
              <select
                id="ex-type"
                value={exerciseType}
                onChange={(e) => setExerciseType(e.target.value)}
              >
                <option value="">Любой</option>
                {Object.entries(EXERCISE_TYPE_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <div className="filters-actions">
              <button
                type="button"
                className="primary"
                onClick={() => load(search, difficulty, exerciseType)}
              >
                Показать
              </button>
              {filtered && (
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    setSearch("");
                    setDifficulty("");
                    setExerciseType("");
                    load("", "", "");
                  }}
                >
                  Сбросить
                </button>
              )}
            </div>
          </div>
        </Card>

        <Card
          title="Список упражнений"
          description={data ? `Найдено: ${data.total}` : undefined}
        >
          {loading && <Skeleton rows={5} />}

          {!loading && data && data.items.length === 0 && (
            <Empty
              title={filtered ? "Ничего не нашлось" : "Каталог пуст"}
              hint={
                filtered
                  ? "Попробуйте изменить условия поиска или сбросить фильтры."
                  : "Пока не загружен ни один справочник упражнений."
              }
            />
          )}

          {!loading && data && data.items.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Название</th>
                    <th>Оборудование</th>
                    <th>Основные мышцы</th>
                    <th>Сложность</th>
                    <th>В программах</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((ex) => (
                    <tr key={ex.id}>
                      <td>
                        <Link href={`/exercises/${ex.id}`}>
                          {ex.name_ru || ex.name}
                        </Link>
                        {ex.name_ru && (
                          <div className="muted" style={{ fontSize: 12 }}>
                            {ex.name}
                          </div>
                        )}
                      </td>
                      <td>{equipmentList(ex.equipment)}</td>
                      <td>{muscleList(ex.primary_muscles)}</td>
                      <td>
                        {ex.difficulty
                          ? DIFFICULTY_LABELS[ex.difficulty] || ex.difficulty
                          : "—"}
                      </td>
                      <td>
                        <Status tone={ex.is_active ? "ok" : "neutral"}>
                          {ex.is_active ? "используется" : "выключено"}
                        </Status>
                      </td>
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
