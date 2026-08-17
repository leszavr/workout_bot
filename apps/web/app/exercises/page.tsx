"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import { api, ExerciseListItem, getToken, ListResponse } from "@/lib/api";

const DIFFICULTY_LABELS: Record<string, string> = {
  beginner: "Начальный",
  intermediate: "Средний",
  expert: "Продвинутый",
};

export default function ExercisesPage() {
  const [data, setData] = useState<ListResponse<ExerciseListItem> | null>(null);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [difficulty, setDifficulty] = useState("");
  const [exerciseType, setExerciseType] = useState("");

  function load(s: string, d: string, t: string) {
    api
      .exercises({
        search: s || undefined,
        difficulty: d || undefined,
        exercise_type: t || undefined,
        limit: 100,
      })
      .then(setData)
      .catch((e) => setError(e.message));
  }

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    load("", "", "");
  }, []);

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <h1 className="page-title">Каталог упражнений</h1>
        {error && <div className="error">{error}</div>}
        <div className="toolbar">
          <input
            type="text"
            placeholder="Поиск по названию..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load(search, difficulty, exerciseType)}
          />
          <select value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
            <option value="">Любая сложность</option>
            <option value="beginner">Начальный</option>
            <option value="intermediate">Средний</option>
            <option value="expert">Продвинутый</option>
          </select>
          <select value={exerciseType} onChange={(e) => setExerciseType(e.target.value)}>
            <option value="">Все категории</option>
            <option value="strength">strength</option>
            <option value="stretching">stretching</option>
            <option value="plyometrics">plyometrics</option>
            <option value="powerlifting">powerlifting</option>
            <option value="olympic weightlifting">olympic weightlifting</option>
            <option value="strongman">strongman</option>
            <option value="cardio">cardio</option>
          </select>
          <button
            type="button"
            className="primary"
            onClick={() => load(search, difficulty, exerciseType)}
          >
            Найти
          </button>
        </div>
        {!data && !error && <div className="loading">Загрузка...</div>}
        {data && (
          <>
            <p className="muted">Найдено: {data.total}</p>
            <table>
              <thead>
                <tr>
                  <th>Название</th>
                  <th>Оборудование</th>
                  <th>Основные мышцы</th>
                  <th>Сложность</th>
                  <th>Источник</th>
                  <th>Статус</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((ex) => (
                  <tr key={ex.id}>
                    <td>
                      <Link href={`/exercises/${ex.id}`}>
                        {ex.name_ru || ex.name}
                      </Link>
                      <div className="muted" style={{ fontSize: 12 }}>
                        {ex.name}
                      </div>
                    </td>
                    <td>{ex.equipment.join(", ") || "—"}</td>
                    <td>{ex.primary_muscles.join(", ") || "—"}</td>
                    <td>{ex.difficulty ? DIFFICULTY_LABELS[ex.difficulty] || ex.difficulty : "—"}</td>
                    <td className="muted">{ex.source}</td>
                    <td>
                      <span className={`badge ${ex.is_active ? "confirmed" : "draft"}`}>
                        {ex.is_active ? "active" : "inactive"}
                      </span>
                    </td>
                  </tr>
                ))}
                {data.items.length === 0 && (
                  <tr>
                    <td colSpan={6} className="muted">
                      Упражнения не найдены
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </>
        )}
      </main>
    </div>
  );
}
