"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import { api, getToken, ProfileDetail } from "@/lib/api";

function Section({
  title,
  children,
}: {
  readonly title: string;
  readonly children: React.ReactNode;
}) {
  return (
    <div className="card">
      <div className="section-title">{title}</div>
      {children}
    </div>
  );
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  if (Array.isArray(value)) {
    return value.join(", ") || "—";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean" ||
    typeof value === "bigint"
  ) {
    return String(value);
  }
  return "—";
}

function Kv({
  data,
  fields,
}: {
  readonly data: Record<string, unknown>;
  readonly fields: [string, string][];
}) {
  return (
    <div className="kv">
      {fields.map(([key, label]) => {
        const display = formatValue(data?.[key]);
        return (
          <div key={key} style={{ display: "contents" }}>
            <div className="k">{label}</div>
            <div>{display}</div>
          </div>
        );
      })}
    </div>
  );
}

export default function ProfileDetailPage() {
  const params = useParams<{ id: string }>();
  const [profile, setProfile] = useState<ProfileDetail | null>(null);
  const [error, setError] = useState("");
  const [view, setView] = useState<"structured" | "raw">("structured");

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    api
      .profile(params.id)
      .then(setProfile)
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
  if (!profile) {
    return (
      <div className="app-shell">
        <AppNav />
        <main className="main">
          <div className="loading">Загрузка...</div>
        </main>
      </div>
    );
  }

  const d = profile.data as Record<string, Record<string, unknown>>;

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <h1 className="page-title">
          Профиль {profile.display_number || profile.profile_id}
        </h1>
        <div className="tabs">
          <button
            type="button"
            className={view === "structured" ? "active" : ""}
            onClick={() => setView("structured")}
          >
            Structured View
          </button>
          <button
            type="button"
            className={view === "raw" ? "active" : ""}
            onClick={() => setView("raw")}
          >
            Raw JSON
          </button>
        </div>

        {view === "raw" ? (
          <pre className="raw-json">{JSON.stringify(profile.data, null, 2)}</pre>
        ) : (
          <>
            <Section title="Основные данные">
              <Kv
                data={d.client || {}}
                fields={[
                  ["name", "Имя"],
                  ["age_years", "Возраст"],
                  ["sex", "Пол"],
                  ["height_cm", "Рост (см)"],
                  ["weight_kg", "Вес (кг)"],
                  ["waist_cm", "Талия (см)"],
                ]}
              />
            </Section>
            <Section title="Цели">
              <Kv
                data={d.goals || {}}
                fields={[
                  ["primary", "Основная цель"],
                  ["secondary", "Дополнительные"],
                  ["desired_result", "Желаемый результат"],
                  ["target_timeframe", "Срок"],
                ]}
              />
            </Section>
            <Section title="Опыт тренировок">
              <Kv
                data={d.training_background || {}}
                fields={[
                  ["experience_level", "Опыт"],
                  ["current_frequency_per_week", "Частота сейчас"],
                  ["current_activity_description", "Текущая активность"],
                  ["current_exercises", "Упражнения"],
                ]}
              />
            </Section>
            <Section title="Место и оборудование">
              <Kv
                data={d.training_location || {}}
                fields={[
                  ["primary_location", "Место"],
                  ["gym_name", "Зал"],
                  ["available_equipment", "Оборудование"],
                  ["custom_equipment_description", "Домашнее оборудование"],
                ]}
              />
            </Section>
            <Section title="Ограничения и здоровье">
              <Kv
                data={d.health_and_limitations || {}}
                fields={[
                  ["has_limitations", "Есть ограничения"],
                  ["categories", "Категории"],
                  ["movements_to_avoid", "Нежелательные движения"],
                  ["doctor_recommendations", "Рекомендации врача"],
                ]}
              />
            </Section>
            <Section title="Предпочтения">
              <Kv
                data={d.exercise_preferences || {}}
                fields={[
                  ["preferred_exercises", "Нравятся"],
                  ["disliked_exercises", "Не нравятся"],
                  ["exercise_goals", "Хочет освоить"],
                ]}
              />
            </Section>
            <Section title="Дополнительная информация">
              <Kv
                data={d.additional_information || {}}
                fields={[
                  ["schedule_constraints", "Ограничения расписания"],
                  ["free_text", "Другое"],
                ]}
              />
            </Section>
            <Section title="Согласия">
              {profile.consents.length === 0 ? (
                <p className="muted">Согласия не зафиксированы</p>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>Тип</th>
                      <th>Версия</th>
                      <th>Дата</th>
                      <th>Источник</th>
                    </tr>
                  </thead>
                  <tbody>
                    {profile.consents.map((c) => (
                      <tr key={c.consent_type}>
                        <td>{c.consent_type}</td>
                        <td>{c.consent_version}</td>
                        <td>{c.granted_at ? new Date(c.granted_at).toLocaleString("ru-RU") : "—"}</td>
                        <td>{c.source}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Section>
          </>
        )}
      </main>
    </div>
  );
}
